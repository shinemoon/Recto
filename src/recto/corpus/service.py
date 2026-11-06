"""语料服务 —— 业务逻辑 (与 HTTP 无关, 便于直接测试)。

这里从 epub-refiner 的 Flask 路由平移而来, 但:
  * 不再有 Flask: 每个入口 = 一个普通函数, 返回 `(HTTP 状态码, JSON 可序列化对象)`
  * 日志落到 Recto 的 `logs/corpus.log` (不再各自一份 server.log)
  * 登录 / 会话由 Recto 统一处理, 这里只管业务

页面渲染在 `recto.app.corpus` —— 那是外壳 + 静态资产, 与业务无关。
"""
from __future__ import annotations

import json
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import analyze, epub_parse, llm, store
from .config import (
    DEFAULT_CATEGORIES,
    apply_model_to_llm,
    is_masked,
    load_config,
    mask_key,
    model_library,
    normalize_thinking,
    resolve_api_key,
    save_config,
)

# 本进程内在跑的分析任务 (bid -> thread). 用于识别"状态卡在 running 但线程早没了"
_TASKS: dict[str, threading.Thread] = {}

# 每次启动新 worker 就 +1. 老线程(可能挂在网络读上)的回调看到代次不符就闭嘴,
# 不会把新一轮的状态覆盖掉.
_RUN_TOKENS: dict[str, int] = {}

# 状态文件多久没更新就认为不是活任务 (别的进程里跑, 靠这个兜底)
STALE_STATUS_SEC = 180

# 线程还活着但这么久没有任何进展 = 挂死 (LLM 端不回包/慢慢挤字节).
# 到了这个门槛允许"强制重跑": 老线程无法杀, 但它写不进状态了.
STALL_ABORT_SEC = 600

_LOG_MAX = 512 * 1024


# ------------------------------------------------------------------ 日志
def log(msg: str, level: str = "info") -> None:
    """一行一条, 写到 <state>/logs/corpus.log (超 512KB 轮转一次)。"""
    try:
        from recto.core import paths as _paths

        d = Path(_paths.PATHS.log_dir)
    except Exception:  # noqa: BLE001
        d = Path(store.DATA_DIR) / "logs"
    try:
        d.mkdir(parents=True, exist_ok=True)
        p = d / "corpus.log"
        if p.exists() and p.stat().st_size > _LOG_MAX:
            p.replace(p.with_suffix(".log.1"))
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with p.open("a", encoding="utf-8") as f:
            f.write(f"{stamp} [{level}] {msg}\n")
    except Exception:  # noqa: BLE001 — 日志失败不许影响业务
        pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------ 任务状态
def _status_fresh(bid: str) -> bool:
    p = store.book_dir(bid) / "status.json"
    try:
        return (time.time() - p.stat().st_mtime) < STALE_STATUS_SEC
    except OSError:
        return False


def _stall_seconds(bid: str) -> float:
    """距上次进度写盘过了多少秒 (status.json 的 at 字段, 兜底看 mtime)。"""
    st = store.get_status(bid) or {}
    at = st.get("at") or ""
    if at:
        try:
            dt = datetime.fromisoformat(at.replace("Z", "+00:00"))
            return max(0.0, time.time() - dt.timestamp())
        except Exception:  # noqa: BLE001
            pass
    p = store.book_dir(bid) / "status.json"
    try:
        return max(0.0, time.time() - p.stat().st_mtime)
    except OSError:
        return 0.0


def task_snapshot(bid: str) -> dict[str, Any]:
    """单本书的任务快照 (含"活着/中断/挂死"修正)."""
    st = dict(store.get_status(bid) or {})
    status = (st.get("status") or "").lower()
    partial = store.partial_summary(bid)
    st["partial"] = partial
    st["book_id"] = bid
    if status in ("running", "analyzing"):
        t = _TASKS.get(bid)
        alive = bool(t and t.is_alive())
        stall = _stall_seconds(bid)
        st["alive"] = alive
        if not alive and not _status_fresh(bid):
            st["status"] = "interrupted"
            st["resumable"] = partial.get("saved", 0) > 0
            st["last_error"] = (
                st.get("last_error")
                or "任务已中断 (进程退出后状态未收尾), 点「继续」从断点接着跑"
            )
        elif not alive:
            st["external"] = True
        elif stall >= STALL_ABORT_SEC:
            st["stalled"] = True
            st["stalled_sec"] = int(stall)
            st["can_force"] = True
            st["last_error"] = (
                f"已经 {int(stall // 60)} 分钟没有任何进展 (请求挂住?), "
                f"可以点「强制重跑」放弃这次挂死的调用"
            )
    if status in ("paused", "failed", "interrupted"):
        st["resumable"] = bool(st.get("resumable")) or partial.get("saved", 0) > 0
    st["saved_blocks"] = partial.get("saved", 0)
    return st


def tasks() -> tuple[int, dict]:
    """所有未收尾任务 (刷新页面后靠这个恢复进度条)."""
    items = []
    for bid in list((store.load_state().get("books") or {}).keys()):
        snap = task_snapshot(bid)
        status = (snap.get("status") or "").lower()
        if status not in ("running", "analyzing", "paused", "interrupted", "failed"):
            continue
        if status == "failed" and not snap.get("saved_blocks"):
            continue
        b = store.get_book(bid) or {}
        items.append({
            "book_id": bid,
            "title": b.get("title") or b.get("name") or bid,
            "status": snap.get("status"),
            "done": snap.get("done") or 0,
            "total": snap.get("total") or snap.get("chunks") or 0,
            "saved_blocks": snap.get("saved_blocks") or 0,
            "started_at": snap.get("started_at") or "",
            "last_error": snap.get("last_error") or snap.get("error") or "",
            "alive": bool(snap.get("alive")),
            "stalled": bool(snap.get("stalled")),
            "resumable": bool(snap.get("resumable")),
        })
    items.sort(key=lambda x: (0 if x["status"] in ("running", "analyzing") else 1))
    return 200, {"items": items}


# ------------------------------------------------------------------ 配置
def presets() -> tuple[int, dict]:
    from .presets import PRESETS, TIPS

    return 200, {"presets": PRESETS, "tips": TIPS}


def get_config() -> tuple[int, dict]:
    cfg = load_config()
    out = json.loads(json.dumps(cfg))
    out["llm"]["api_key"] = mask_key(out["llm"].get("api_key", ""))
    out["llm"]["has_key"] = bool(cfg["llm"].get("api_key"))
    out["models"] = model_library(cfg)
    return 200, out


def set_config(body: dict) -> tuple[int, dict]:
    cfg = load_config()
    incoming = body.get("llm") or {}
    for k in ("provider", "base_url", "model"):
        if k in incoming and incoming[k] is not None:
            cfg["llm"][k] = incoming[k]
    # key: 传空串 / "***" / 脱敏串 都表示不动
    key = resolve_api_key(incoming.get("api_key"), cfg["llm"].get("api_key", ""))
    if key != cfg["llm"].get("api_key", ""):
        cfg["llm"]["api_key"] = key
    # 判定是否脱离模型库绑定: 只有改了与当前绑定模型**不同**的值才解除
    # (原样再存一次, 保留绑定)
    active = cfg.get("active_model_id") or ""
    if active:
        entry = next((m for m in (cfg.get("models") or []) if m.get("id") == active), None)
        if entry:
            same = True
            for k in ("provider", "base_url", "model"):
                if k in incoming and incoming[k] is not None:
                    if incoming[k] != entry.get(k):
                        same = False
            if key != (entry.get("api_key") or ""):
                same = False
            if not same:
                cfg["active_model_id"] = ""
        else:
            cfg["active_model_id"] = ""
    for k in ("timeout", "max_tokens", "temperature", "max_concurrency"):
        if k in incoming and incoming[k] is not None:
            try:
                cfg["llm"][k] = type(cfg["llm"].get(k, 0))(incoming[k])
            except (TypeError, ValueError):
                pass
    if incoming.get("thinking") is not None:
        cfg["llm"]["thinking"] = normalize_thinking(incoming["thinking"])
    # 布尔字段不能用 type(旧值)(新值): bool("false") == True
    if incoming.get("strict_no_reasoning") is not None:
        cfg["llm"]["strict_no_reasoning"] = bool(incoming["strict_no_reasoning"])
    if "parse" in body and isinstance(body["parse"], dict):
        cfg["parse"].update(body["parse"])
    if "categories" in body and isinstance(body["categories"], list):
        cfg["categories"] = body["categories"]
    save_config(cfg)
    out = json.loads(json.dumps(cfg))
    out["llm"]["api_key"] = mask_key(cfg["llm"]["api_key"])
    out["llm"]["has_key"] = bool(cfg["llm"]["api_key"])
    log("配置已保存 (模型 %s)" % cfg["llm"].get("model", ""))
    return 200, {"ok": True, "config": out}


def quota() -> tuple[int, dict]:
    return 200, llm.quota_state()


def quota_reset() -> tuple[int, dict]:
    llm.reset_quota()
    return 200, {"ok": True, "quota": llm.quota_state()}


def llm_test(body: dict) -> tuple[int, dict]:
    cfg = load_config()
    test_cfg = dict(cfg["llm"])
    # 前端回传的可能是**脱敏串** —— 那是展示值, 不是真 key.
    test_cfg["api_key"] = resolve_api_key(body.get("api_key"), cfg["llm"].get("api_key", ""))
    if body.get("base_url"):
        test_cfg["base_url"] = body["base_url"].strip()
    if body.get("model"):
        test_cfg["model"] = body["model"].strip()
    if body.get("provider"):
        test_cfg["provider"] = body["provider"].strip()
    return 200, llm.test_llm(test_cfg)


# ------------------------------------------------------------------ 模型库
def models_get() -> tuple[int, dict]:
    cfg = load_config()
    return 200, {
        "models": model_library(cfg),
        "active_model_id": cfg.get("active_model_id", ""),
    }


def models_set(body: dict) -> tuple[int, dict]:
    """整库覆盖. body.models = [{id?,name,provider,base_url,api_key,model,note}]"""
    raw_list = body.get("models")
    if not isinstance(raw_list, list):
        return 400, {"error": "缺少 models 列表"}
    cfg = load_config()
    old = {m.get("id"): m for m in (cfg.get("models") or [])}
    used: set[str] = set()
    out: list[dict[str, Any]] = []
    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        entry = dict(raw)
        mid = (entry.get("id") or "").strip()
        # "from_current": 从当前 llm 段导入 (含明文 key, 不用手抄)
        if entry.pop("from_current", False):
            src = cfg["llm"]
            entry["provider"] = src.get("provider", "openai")
            entry["base_url"] = src.get("base_url", "")
            entry["model"] = src.get("model", "")
            entry["api_key"] = src.get("api_key", "")
            if is_masked(entry.get("api_key", "")):
                entry["api_key"] = (old.get(mid) or {}).get("api_key", "")
        key = resolve_api_key(entry.get("api_key"), (old.get(mid) or {}).get("api_key", ""))
        entry["api_key"] = key
        from .config import normalize_model_entry

        out.append(normalize_model_entry(entry, used))
    cfg["models"] = out
    active = (body.get("active_model_id") or cfg.get("active_model_id") or "").strip()
    if active and not any(m["id"] == active for m in out):
        active = ""
    cfg["active_model_id"] = active
    save_config(cfg)
    log("模型库已保存 (%d 条)" % len(out))
    return 200, {"ok": True, "models": model_library(cfg),
                 "active_model_id": cfg["active_model_id"]}


def models_apply(body: dict) -> tuple[int, dict]:
    mid = (body.get("id") or "").strip()
    if not mid:
        return 400, {"error": "缺少 id"}
    cfg = load_config()
    if not apply_model_to_llm(cfg, mid):
        return 404, {"error": f"模型不存在: {mid}"}
    save_config(cfg)
    out = json.loads(json.dumps(cfg))
    out["llm"]["api_key"] = mask_key(cfg["llm"]["api_key"])
    out["llm"]["has_key"] = bool(cfg["llm"]["api_key"])
    log("模型库条目生效: %s (%s)" % (mid, cfg["llm"].get("model", "")))
    return 200, {"ok": True, "config": out}


def models_delete(mid: str) -> tuple[int, dict]:
    cfg = load_config()
    before = len(cfg.get("models") or [])
    cfg["models"] = [m for m in (cfg.get("models") or []) if m.get("id") != mid]
    if len(cfg["models"]) == before:
        return 404, {"error": f"模型不存在: {mid}"}
    if cfg.get("active_model_id") == mid:
        cfg["active_model_id"] = ""
    save_config(cfg)
    return 200, {"ok": True, "models": model_library(cfg),
                 "active_model_id": cfg["active_model_id"]}


def models_test(mid: str) -> tuple[int, dict]:
    cfg = load_config()
    entry = next((m for m in (cfg.get("models") or []) if m.get("id") == mid), None)
    if not entry:
        return 404, {"error": f"模型不存在: {mid}"}
    key = entry.get("api_key", "")
    if is_masked(key):
        key = cfg["llm"].get("api_key", "")
    test_cfg = {
        "provider": entry.get("provider", "openai"),
        "base_url": entry.get("base_url", ""),
        "api_key": key,
        "model": entry.get("model", ""),
        "timeout": cfg["llm"].get("timeout", 180),
        "max_tokens": 32,
        "temperature": 0.0,
    }
    return 200, llm.test_llm(test_cfg)


# ------------------------------------------------------------------ epub 库
def epubs(roots_q: str = "") -> tuple[int, dict]:
    roots = [r.strip() for r in roots_q.split("|") if r.strip()] if roots_q else None
    return 200, {"items": store.scan_epub_roots(roots)}


def roots_get() -> tuple[int, dict]:
    return 200, {"roots": store.default_epub_roots()}


def roots_set(body: dict) -> tuple[int, dict]:
    roots = [r.strip() for r in (body.get("roots") or []) if r.strip()]
    cfg = load_config()
    cfg["epub_roots"] = roots
    save_config(cfg)
    return 200, {"ok": True, "roots": roots}


def parse(body: dict) -> tuple[int, dict]:
    path = (body.get("path") or "").strip()
    if not path:
        return 400, {"error": "缺少 path"}
    p = Path(path)
    if not p.exists():
        return 404, {"error": f"文件不存在: {path}"}
    if p.suffix.lower() != ".epub":
        return 400, {"error": "只支持 .epub"}
    try:
        info = epub_parse.parse_epub(p)
    except epub_parse.EpubError as e:
        return 400, {"error": str(e)}
    except Exception as e:  # noqa: BLE001
        return 500, {"error": f"解析异常: {e}", "trace": traceback.format_exc()[-1500:]}
    bid = store.book_id_for(p)
    store.save_meta(bid, info)
    store.upsert_book(bid, {
        "path": str(p),
        "name": p.name,
        "title": info.get("title") or p.stem,
        "author": info.get("author", ""),
        "size": info.get("size", 0),
        "total_chars": info.get("total_chars", 0),
        "chapter_count": len(info.get("chapters", [])),
        "status": "parsed",
    })
    log("已解析 %s (%d 章)" % (p.name, len(info.get("chapters", []))))
    return 200, {"ok": True, "book_id": bid, "meta": store.load_meta(bid)}


def books() -> tuple[int, dict]:
    st = store.load_state()
    items = store.list_books()
    for it in items:
        th = _TASKS.get(it.get("id") or "")
        if th and th.is_alive():
            it["status"] = "running"
            it["alive"] = True
    active = [it["id"] for it in items
              if (it.get("status") or "").lower() in ("running", "analyzing")]
    paused = [it["id"] for it in items
              if (it.get("status") or "").lower() in ("paused", "interrupted")]
    return 200, {
        "items": items,
        "last": st.get("last") or "",
        "active": active,
        "unfinished": active + paused,
        "exports": str(store.EXPORT_DIR),
        "latest_path": str(store.latest_export_path()),
    }


# ------------------------------------------------------------------ 固定导出
def export_book(bid: str) -> tuple[int, dict]:
    if not store.load_meta(bid):
        return 404, {"error": "书目不存在"}
    r = store.export_keywords(bid, force_latest=True)
    if not r:
        return 404, {"error": "还没有 keywords.json 可导出"}
    log("导出固定路径: %s" % r.get("slug"))
    return 200, {"ok": True, **r}


def export_latest(body: dict) -> tuple[int, dict]:
    if body.get("clear"):
        return 200, {"ok": True, **store.clear_latest()}
    slug = (body.get("slug") or "").strip()
    bid = (body.get("book_id") or "").strip()
    if not slug and bid:
        b = store.get_book(bid) or {}
        slug = store.book_slug(b.get("path"), b.get("title"))
    if not slug:
        return 400, {"error": "缺少 slug 或 book_id"}
    pin = body.get("pin", True)
    r = store.set_latest(slug, pin=bool(pin))
    if not r:
        return 404, {"error": f"语料目录里没有这份产物 (slug={slug})"}
    return 200, {"ok": True, **r}


def export_sync() -> tuple[int, dict]:
    done = store.sync_exports()
    return 200, {"ok": True, "count": len(done), "items": done}


def exports() -> tuple[int, dict]:
    idx = store._load_export_index()
    return 200, {
        "dir": str(store.EXPORT_DIR),
        "latest": str(store.latest_export_path()),
        "latest_slug": idx.get("latest_slug", ""),
        "latest_pinned": idx.get("latest_pinned") is True,
        "latest_state": store.latest_state(),
        "updated_at": idx.get("updated_at", ""),
        "items": store.export_items(),
    }


def book_detail(bid: str) -> tuple[int, dict]:
    meta = store.load_meta(bid)
    if not meta:
        return 404, {"error": "书目不存在"}
    return 200, {
        "meta": meta,
        "keywords": store.load_keywords(bid),
        "status": store.get_status(bid),
    }


def book_delete(bid: str) -> tuple[int, dict]:
    d = store.book_dir(bid)
    removed = False
    for name in ("meta.json", "keywords.json", "blocks.json", "status.json"):
        f = d / name
        if f.exists():
            f.unlink()
            removed = True
    state = store.load_state()
    state["books"].pop(bid, None)
    store.save_state(state)
    return 200, {"ok": True, "removed": removed}


def keywords(bid: str) -> tuple[int, dict]:
    data = store.load_keywords(bid)
    if not data:
        return 404, {"error": "还没有 keywords.json"}
    return 200, data


# ------------------------------------------------------------------ 抽取
def analyze_book(body: dict) -> tuple[int, dict]:
    bid = (body.get("book_id") or "").strip()
    if not bid:
        return 400, {"error": "缺少 book_id"}
    cfg = load_config()
    if not cfg["llm"].get("api_key"):
        return 400, {"error": "尚未配置 LLM API Key"}
    meta_info = store.load_meta(bid)
    if not meta_info:
        return 404, {"error": "书目不存在, 请先解析"}

    # 同一本书已经在跑 -> 别开第二个线程 (两个写同一份 partial/status)
    running = _TASKS.get(bid)
    force = bool(body.get("force"))
    if running and running.is_alive():
        stall = _stall_seconds(bid)
        if not (force or stall >= STALL_ABORT_SEC):
            return 409, {
                "error": "这本书已经在抽取中",
                "stalled_sec": int(stall),
                "can_force": True,
                "status": store.get_status(bid),
            }
        log("放弃旧任务 %s (stalled %.0fs, force=%s)" % (bid, stall, force), "warn")

    if bool(body.get("fresh")):
        store.clear_partial(bid)

    # 取回正文 (从原 epub 重新解析, 不长期缓存正文)
    try:
        info = epub_parse.parse_epub(meta_info["path"])
    except Exception as e:  # noqa: BLE001
        return 500, {"error": f"重新读取 epub 失败: {e}"}

    categories = [c for c in (cfg.get("categories") or DEFAULT_CATEGORIES) if c.get("enabled", True)]
    blocks = epub_parse.chunk_chapters(info["chapters"], cfg["parse"].get("max_chars_per_chunk", 8000))
    store.save_blocks(bid, blocks)
    store.save_meta(bid, info)

    # 断点: 上次跑到哪. 指纹不符的块在 analyze 内部自动作废.
    cache = store.load_partial(bid)
    resumed = sum(
        1 for i in range(len(blocks))
        if isinstance(cache.get(str(i)), dict)
        and cache[str(i)].get("fp") == analyze.block_fingerprint(blocks[i], categories)
    )
    pending = len(blocks) - resumed

    # 推理模型体检 (只提示, 不拦 -- 拦截交给严格模式在首个块之后中止)
    model = cfg["llm"].get("model", "")
    thinking = str(cfg["llm"].get("thinking") or "off")
    strict = bool(cfg["llm"].get("strict_no_reasoning", True))
    warnings: list[str] = []
    if llm.looks_reasoning_model(model, cfg["llm"].get("base_url", "")):
        if thinking == "on":
            warnings.append(
                f"当前模型 {model} 是推理模型, 且「思考」为开启: 抽取会慢很多、"
                f"token 消耗成倍, 输出还容易撞 max_tokens 截断"
            )
        else:
            warnings.append(
                f"当前模型 {model} 是推理模型: 已尝试发送「关闭思考」参数; "
                f"若该端点忽略它, 严格模式会在第一块之后中止"
            )
        if strict and thinking != "on":
            warnings.append("严格避免推理已开启 (检测到 thinking 就整轮中止)")

    target_lang = body.get("target_language") or "Chinese"
    store.set_status(
        bid, "running", chunks=len(blocks), total=len(blocks),
        done=resumed, resumed=resumed, pending=pending,
        model=model, thinking=thinking, started_at=_now(),
    )
    llm.reset_quota()   # 每轮重新给额度一次机会 (上一轮熔断不能永久卡住)
    llm.reset_stats()

    _RUN_TOKENS[bid] = _RUN_TOKENS.get(bid, 0) + 1
    run_token = _RUN_TOKENS[bid]
    log("开始抽取 %s: %d 块 (断点 %d, 待跑 %d) 模型=%s"
        % (meta_info.get("title") or bid, len(blocks), resumed, pending, model))

    def worker() -> None:
        errors: list[dict[str, Any]] = []
        failed = [0]
        stats: dict[str, Any] = {}

        def alive() -> bool:
            """这一轮的代次还是当前代次吗 (老线程被"强制重跑"后就不写了)."""
            return _RUN_TOKENS.get(bid) == run_token

        def progress(done, total, idx):
            if not alive():
                return
            store.set_status(bid, "running", done=done, total=total, chunks=total,
                             failed_blocks=failed[0], resumed=resumed, pending=pending)

        def on_error(idx, total, e):
            failed[0] += 1
            if not alive():
                return
            store.set_status(bid, "running", total=total, chunks=total,
                             failed_blocks=failed[0], last_error=str(e)[:300])

        def on_chunk_done(idx, fp, items):
            # 断点落盘: 每块结束立刻写, 进程被砍也不丢已完成的块
            store.save_partial_block(bid, idx, fp, items)

        try:
            merged = analyze.analyze_epub(
                info["path"],
                cfg["llm"],
                categories,
                blocks,
                target_lang=target_lang,
                max_concurrency=cfg["llm"].get("max_concurrency", 3),
                progress_cb=progress,
                on_error=on_error,
                errors_out=errors,
                cache=cache,
                on_chunk_done=on_chunk_done,
                strict_no_reasoning=strict,
                stats_out=stats,
            )
            if not alive():
                # 被"强制重跑"接手了: 本轮结果照样是有效数据, 但别写状态/产物,
                # 免得把新一轮的 progress 覆盖成 done
                return
            out = analyze.build_output(
                info, cfg["llm"], categories, merged,
                n_chunks=len(blocks), target_lang=target_lang,
                errors=errors,
            )
            new_terms = sum(len(v["items"]) for v in out["categories"].values())
            # 本轮明显比已有产物差 -> 另存 partial, 不覆盖好结果
            existing = store.load_keywords(bid) or {}
            old_terms = sum(
                len(v.get("items") or [])
                for v in (existing.get("categories") or {}).values()
            )
            reason = analyze.degraded_reason(errors, len(blocks), new_terms, old_terms)
            if reason:
                store.save_keywords(bid, out, name="keywords.partial.json")
                store.set_status(
                    bid, "failed",
                    error=f"本轮结果不如上一份 ({reason}), 未覆盖 keywords.json",
                    last_error=f"degraded: {reason}",
                    failed_blocks=len(errors), finished_at=_now(),
                )
                store.upsert_book(bid, {"status": "failed", "error": reason})
                log("抽取被拦 (结果不如上一份): %s" % reason, "warn")
                return
            store.save_keywords(bid, out)
            store.clear_partial(bid)
            store.set_status(
                bid, "done", terms=new_terms, chunks=len(blocks),
                failed_blocks=failed[0], finished_at=_now(),
                resumed=stats.get("resumed", 0), ran=stats.get("ran", 0),
                llm_calls=(stats.get("llm") or {}).get("calls", 0),
                reasoning_calls=(stats.get("llm") or {}).get("reasoning_calls", 0),
            )
            store.upsert_book(bid, {"status": "done", "terms": new_terms})
            # 复制到固定导出路径 (<slug>.json + latest.json)
            try:
                store.export_keywords(bid, force_latest=True)
            except Exception as e:  # noqa: BLE001 — 导出失败不影响任务终态
                log("导出失败: %s" % e, "error")
            log("抽取完成: %s · %d 词条 · %d/%d 块"
                % (meta_info.get("title") or bid, new_terms, len(blocks) - len(errors), len(blocks)))
        except analyze.QuotaAborted as e:
            if alive():
                _pause(bid, e, "额度用尽", errors, stats)
        except analyze.ReasoningAborted as e:
            if alive():
                _pause(bid, e, "推理模型", errors, stats)
        except Exception as e:  # noqa: BLE001
            log("抽取异常: %s: %s" % (type(e).__name__, e), "error")
            if alive():
                _pause(bid, e, "异常", errors, stats, hard=True)
        finally:
            if alive():
                _TASKS.pop(bid, None)

    t = threading.Thread(target=worker, daemon=True, name=f"corpus-{bid}")
    _TASKS[bid] = t
    t.start()
    return 200, {
        "ok": True,
        "book_id": bid,
        "chunks": len(blocks),
        "resumed": resumed,
        "pending": pending,
        "warnings": warnings,
    }


def _pause(bid, exc, kind, errors, stats, hard: bool = False) -> None:
    """本轮停下但**断点还在** -> status=paused (UI 显示「继续」).

    hard=True 是"真正的失败"(代码异常), 但已完成块依然可以续跑,
    所以照样给 resumable, 只是文案不同.
    """
    partial = store.partial_summary(bid)
    can_resume = partial.get("saved", 0) > 0
    store.set_status(
        bid, "failed" if hard else "paused",
        error=str(exc)[:800],
        last_error=f"{kind}: {str(exc)[:280]}",
        failed_blocks=len(errors),
        finished_at=_now(),
        resumable=can_resume,
        saved_blocks=partial.get("saved", 0),
        resumed=stats.get("resumed", 0),
        ran=stats.get("ran", 0),
    )
    store.upsert_book(bid, {"status": "failed" if hard else "paused",
                            "error": str(exc)[:280]})
    log("抽取停下 (%s): %s" % (kind, str(exc)[:200]), "warn")


def analyze_clear(body: dict) -> tuple[int, dict]:
    """丢弃断点 (改完分块大小/分类后想从零跑时用)."""
    bid = (body.get("book_id") or "").strip()
    if not bid:
        return 400, {"error": "缺少 book_id"}
    removed = store.clear_partial(bid)
    return 200, {"ok": True, "removed": removed}


# ------------------------------------------------------------------ 只读输出
def status_summary() -> dict:
    """给控制台 / 自检用的一行摘要 (不暴露 key)。"""
    cfg = load_config()
    st = store.load_state()
    books_ = st.get("books") or {}
    done = sum(1 for b in books_.values() if (b.get("status") or "") == "done")
    running = sum(1 for bid, t in _TASKS.items() if t.is_alive())
    idx = store._load_export_index()
    return {
        "dir": str(store.DATA_DIR),
        "books": len(books_),
        "done": done,
        "running": running,
        "exports": len(store.export_items()),
        "latest_slug": idx.get("latest_slug", ""),
        "model": cfg["llm"].get("model", ""),
        "has_key": bool(cfg["llm"].get("api_key")),
    }
