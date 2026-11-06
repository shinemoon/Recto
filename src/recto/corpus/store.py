"""磁盘产物管理: 每个 epub 一个 "book_id" 目录.

目录结构 (outputs/<book_id>/):
    meta.json        — 解析出的书籍元信息 + 章节列表 (不含正文)
    keywords.json    — 最终关键字 JSON (预渲染用)
    blocks.json      — 分块缓存 (可复用, 避免重复切分)
    status.json      — 任务状态 (running / done / failed)

inputs/ 放用户选定的本地 epub 路径清单 (state.json).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DATA_DIR, EXPORT_DIR, OUTPUT_DIR

STATE_PATH = DATA_DIR / "state.json"

# status.json 多久没更新就当作"进程已经不在了" (CLI / web 每块都会写一次)
STALE_STATUS_SEC = 180

# RLock: set_status() 持锁后再调 upsert_book() (也持锁), 普通 Lock 会自死锁
_LOCK = threading.RLock()


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def book_id_for(path: str | Path) -> str:
    """按路径 + mtime + size 生成稳定 book_id."""
    p = Path(path)
    try:
        st = p.stat()
        raw = f"{p.resolve()}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        raw = str(p)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def book_dir(bid: str) -> Path:
    d = OUTPUT_DIR / bid
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_state() -> dict[str, Any]:
    data = read_json(STATE_PATH, None)
    if not isinstance(data, dict):
        data = {"books": {}, "last": ""}
    data.setdefault("books", {})
    data.setdefault("last", "")
    return data


def save_state(state: dict[str, Any]) -> None:
    _atomic_write_json(STATE_PATH, state)


def upsert_book(bid: str, info: dict[str, Any], remove: list[str] | None = None) -> None:
    with _LOCK:
        state = load_state()
        books = state.setdefault("books", {})
        old = books.get(bid, {})
        old.update(info)
        for k in (remove or []):
            old.pop(k, None)
        old["id"] = bid
        old["updated_at"] = datetime.now(timezone.utc).isoformat()
        books[bid] = old
        state["last"] = bid
        save_state(state)


def get_book(bid: str) -> dict[str, Any] | None:
    return load_state()["books"].get(bid)


def list_books() -> list[dict[str, Any]]:
    state = load_state()
    books = list(state["books"].values())
    books.sort(key=lambda b: b.get("updated_at", ""), reverse=True)
    # 补上"产物在哪" — UI 历史列表和下游脚本都要用固定路径
    idx = _load_export_index().get("items", {})
    for b in books:
        d = OUTPUT_DIR / b.get("id", "")
        kwf = d / "keywords.json"
        # 状态以 status.json 为准: state.json 里的 status 是 upsert 时写的快照,
        # 进程被硬杀时会永远停在 "running" (历史列表上误导)
        st = read_json(d / "status.json", None)
        if isinstance(st, dict) and st.get("status"):
            b["status"] = st["status"]
            b["done"] = st.get("done") or 0
            b["total"] = st.get("total") or st.get("chunks") or 0
            b["started_at"] = st.get("started_at") or ""
            if st.get("last_error"):
                b["last_error"] = st["last_error"]
        if b.get("status") in ("running", "analyzing") and status_is_stale(b.get("id", "")):
            # 进程被硬杀 (SIGKILL / 宿主重启): 状态永远停在 running, 列表上误导
            b["status"] = "interrupted"
        partial = partial_summary(b.get("id", ""))
        b["saved_blocks"] = partial.get("saved", 0)
        if b["saved_blocks"] and b.get("status") != "done":
            b["resumable"] = True
        b["keywords_exists"] = kwf.exists()
        b["keywords_path"] = str(kwf) if kwf.exists() else ""
        slug = book_slug(b.get("path"), b.get("title"))
        exp = EXPORT_DIR / (slug + ".json")
        b["export_slug"] = slug
        b["export_path"] = str(exp) if exp.exists() else ""
        it = idx.get(slug) or {}
        if it:
            b.setdefault("terms", it.get("terms") or 0)
            b["export_generated_at"] = it.get("generated_at", "")
    return books


def save_meta(bid: str, epub_info: dict[str, Any]) -> None:
    """存元信息 (不含正文, 免得文件巨大)."""
    slim = {
        "path": epub_info.get("path"),
        "size": epub_info.get("size"),
        "title": epub_info.get("title"),
        "author": epub_info.get("author"),
        "language": epub_info.get("language"),
        "total_chars": epub_info.get("total_chars"),
        "warnings": epub_info.get("warnings", []),
        "toc": epub_info.get("toc", {}),
        "chapters": [
            {
                "index": c["index"],
                "path": c["path"],
                "title": c["title"],
                "chars": c["chars"],
            }
            for c in epub_info.get("chapters", [])
        ],
    }
    _atomic_write_json(book_dir(bid) / "meta.json", slim)


def load_meta(bid: str) -> dict[str, Any] | None:
    return read_json(book_dir(bid) / "meta.json", None)


def save_keywords(bid: str, data: dict[str, Any], name: str = "keywords.json") -> Path:
    p = book_dir(bid) / name
    _atomic_write_json(p, data)
    return p


def load_keywords(bid: str) -> dict[str, Any] | None:
    return read_json(book_dir(bid) / "keywords.json", None)


def save_blocks(bid: str, blocks: list[dict]) -> None:
    _atomic_write_json(book_dir(bid) / "blocks.json", blocks)


def load_blocks(bid: str) -> list[dict]:
    return read_json(book_dir(bid) / "blocks.json", []) or []


# ---------------------------------------------------------------- 断点续跑
#
# outputs/<bid>/chunks.partial.json
#   {"sig": {...}, "blocks": {"12": {"fp": "<指纹>", "items": [...], "at": "..."}}}
#
# 为什么单独一个文件 (而不是混进 status.json):
#   - status.json 每块都在写 (进度), 不该顺带搬几百 KB 的词条
#   - 续跑只认指纹: 块正文/分类变了 → 那块的缓存自动作废, 不会把旧结果
#     张冠李戴到新内容上

PARTIAL_NAME = "chunks.partial.json"


def partial_path(bid: str) -> Path:
    return book_dir(bid) / PARTIAL_NAME


def load_partial(bid: str) -> dict[str, dict[str, Any]]:
    data = read_json(partial_path(bid), None)
    if not isinstance(data, dict):
        return {}
    blocks = data.get("blocks")
    return blocks if isinstance(blocks, dict) else {}


def save_partial_block(bid: str, idx: int, fp: str, items: list[dict[str, Any]]) -> None:
    """落一个块的抽取结果 (断点). 每块结束立刻调, 别攒着."""
    with _LOCK:
        path = partial_path(bid)
        data = read_json(path, None)
        if not isinstance(data, dict) or not isinstance(data.get("blocks"), dict):
            data = {"sig": {}, "blocks": {}}
        data["blocks"][str(idx)] = {
            "fp": fp,
            "items": items,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        data["sig"] = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "blocks": len(data["blocks"]),
        }
        _atomic_write_json(path, data)


def clear_partial(bid: str) -> bool:
    with _LOCK:
        p = partial_path(bid)
        try:
            p.unlink()
            return True
        except OSError:
            return False


def partial_summary(bid: str) -> dict[str, Any]:
    """断点概况 (给 UI 显示「继续 (12/48)」)."""
    blocks = load_partial(bid)
    if not blocks:
        return {"saved": 0, "updated_at": ""}
    at = max((b.get("at") or "") for b in blocks.values()) if blocks else ""
    return {"saved": len(blocks), "updated_at": at}


def set_status(bid: str, status: str, **extra: Any) -> None:
    with _LOCK:
        # 终态清掉上一轮的残留字段, 否则 state.json 里会同时出现
        # status="done" 和陈旧的 error / done 计数 (UI 上误导)
        stale = ("error", "last_error") if status != "failed" else ()
        # 终态还要清掉进度计数: 上一轮中断时留下 done=12, 这轮跑完
        # 48 块后 UI 仍显示 "12/48" 而状态是 done.
        if status in ("done", "failed"):
            stale = stale + ("done", "total")
        info: dict[str, Any] = {"status": status}
        for k in stale:
            info[k] = ""
        info.update(extra)
        # done 时把进度补齐成 "全部完成", 而不是留空
        if status == "done" and "done" not in extra:
            chunks = extra.get("chunks")
            if isinstance(chunks, int):
                info["done"] = chunks
                info["total"] = chunks
        upsert_book(bid, info, remove=list(stale))
        _atomic_write_json(book_dir(bid) / "status.json", {
            "status": status,
            "at": datetime.now(timezone.utc).isoformat(),
            **info,
        })


def get_status(bid: str) -> dict[str, Any]:
    return read_json(book_dir(bid) / "status.json", {"status": "unknown"})


def status_is_stale(bid: str, sec: float = STALE_STATUS_SEC) -> bool:
    """status.json 是否久未更新 (约等于"那个进程已经不在了")."""
    try:
        import time

        return (time.time() - (book_dir(bid) / "status.json").stat().st_mtime) > sec
    except OSError:
        return True


# ---------------------------------------------------------------- epub 库扫描

def scan_epub_roots(roots: list[str] | None = None) -> list[dict[str, Any]]:
    """扫描可用的本地 epub. 返回 [{path, name, size, mtime}]."""
    if roots is None:
        roots = default_epub_roots()
    seen: dict[str, dict[str, Any]] = {}
    for root in roots:
        rp = Path(root).expanduser()
        if not rp.exists():
            continue
        try:
            files = rp.rglob("*.epub")
        except Exception:
            continue
        for f in files:
            s = str(f)
            if s in seen:
                continue
            # 跳过临时/隐藏文件
            name = f.name
            if name.startswith(".") or name.startswith("~"):
                continue
            if ".tmp" in name or name.endswith("~"):
                continue
            try:
                st = f.stat()
            except OSError:
                continue
            seen[s] = {
                "path": s,
                "name": f.name,
                "size": st.st_size,
                "mtime": st.st_mtime,
                "root": str(rp),
            }
    out = sorted(seen.values(), key=lambda x: x["name"].lower())
    return out


def default_epub_roots() -> list[str]:
    """默认扫描的 epub 目录: 先是云阅 Recto 自己的书库, 再看环境变量给的额外根。

    语料是"给书库里的书抽词", 所以书库必须排第一 —— 否则用户得手填路径。
    """
    candidates: list[str] = []
    try:
        from recto.core import paths as _paths

        for d in _paths.PATHS.library_dirs:
            candidates.append(str(d))
    except Exception:  # noqa: BLE001 — 单独跑时没有 Recto 配置
        pass
    env = os.environ.get("RECTO_CORPUS_ROOTS", "")
    if env:
        candidates += [c.strip() for c in env.split(":") if c.strip()]
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def safe_slug(s: str, maxlen: int = 40) -> str:
    """文件名安全的 slug."""
    s = re.sub(r"[^\w\u4e00-\u9fff\-. ]+", "_", s or "").strip()
    s = re.sub(r"[\s_]+", "_", s)
    return s[:maxlen] or "book"


# ---------------------------------------------------------------- 固定导出路径
#
# outputs/<book_id>/ 里的 book_id = sha1(路径|大小|mtime) —— epub 被 touch 一下
# 就换目录, 下游没法稳定引用. 所以另开 exports/:
#
#   exports/<slug>.json   每本书一份, slug 只由 (标题, epub 绝对路径) 决定 → 稳定
#   exports/latest.json   下游只需要读这一个固定路径. 指向谁由 index.json 的
#                         latest_slug 决定 (默认 = 最近一次成功导出), 可用
#                         set_latest() / POST /api/export/latest 显式钉住,
#                         免得一次测试跑把指针拨到测试书上
#   exports/index.json    清单 (slug / 标题 / 词条数 / 时间 / 源路径 / latest_slug)
#
# 三个文件都是"产物副本", 删掉可自动重建 (sync_exports 在服务启动时跑一次).

def book_slug(epub_path: str | Path | None, title: str | None = None) -> str:
    """稳定的导出文件名 (不含 .json).

    只用 epub 路径 (不含 mtime / size) + 标题 → 同一本书永远是同一个名字.
    """
    p = str(epub_path or "")
    stem = Path(p).stem if p else ""
    base = safe_slug(title or stem or "book", 40)
    tail = hashlib.sha1(p.encode("utf-8")).hexdigest()[:6] if p else "000000"
    return f"{base}-{tail}"


def export_path_for(epub_path: str | Path | None, title: str | None = None) -> Path:
    return EXPORT_DIR / (book_slug(epub_path, title) + ".json")


def latest_export_path() -> Path:
    return EXPORT_DIR / "latest.json"


def export_index_path() -> Path:
    return EXPORT_DIR / "index.json"


def _load_export_index() -> dict[str, Any]:
    data = read_json(export_index_path(), None)
    if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
        return {"items": {}}
    return data


def export_keywords(bid: str, set_latest: bool = True,
                    force_latest: bool = False) -> dict[str, Any] | None:
    """把 outputs/<bid>/keywords.json 复制到固定路径. 没有产物则返回 None.

    set_latest=True 时同时更新 exports/latest.json, 并把 latest_slug 指向这本书.
    force_latest=True = 显式动作 (用户在界面上点了导出 / CLI 指定了某本),
    即使 latest 已被钉住也把它拨过来; 被动对账 (sync_exports) 不传.
    """
    kw = load_keywords(bid)
    if not isinstance(kw, dict) or not kw.get("categories"):
        return None
    meta = kw.get("meta") or {}
    src_path = (meta.get("path") or meta.get("source_epub")
                or (get_book(bid) or {}).get("path"))
    title = meta.get("title") or (get_book(bid) or {}).get("title") or ""
    slug = book_slug(src_path, title)
    target = EXPORT_DIR / (slug + ".json")
    payload = dict(kw)
    payload["meta"] = dict(meta, export_slug=slug, book_id=bid,
                           export_path=str(target), exported_at=_now_iso())
    _atomic_write_json(target, payload)
    written = [str(target)]

    # latest 已被人为钉住 → 被动流程不拨走 (显式导出仍可拨)
    pinned = _load_export_index().get("latest_pinned") is True and not force_latest
    if set_latest and not pinned:
        latest = latest_export_path()
        _atomic_write_json(latest, payload)
        written.append(str(latest))

    with _LOCK:
        idx = _load_export_index()
        idx["items"][slug] = {
            "slug": slug,
            "book_id": bid,
            "title": title,
            "author": meta.get("author", ""),
            "source": src_path,
            "terms": sum(len((c or {}).get("items") or [])
                         for c in (kw.get("categories") or {}).values()),
            "model": meta.get("model", ""),
            "generated_at": meta.get("generated_at", ""),
            "exported_at": payload["meta"]["exported_at"],
            "path": str(target),
        }
        if set_latest and not (idx.get("latest_pinned") and not force_latest):
            idx["latest_slug"] = slug
            if force_latest:
                idx["latest_pinned"] = False
        idx["updated_at"] = payload["meta"]["exported_at"]
        _atomic_write_json(export_index_path(), idx)
    return {"slug": slug, "path": str(target), "paths": written}


def export_items() -> list[dict[str, Any]]:
    """exports/index.json 的清单 + 每条的存活状态.

    orphan=True  产物目录 (outputs/<book_id>/keywords.json) 已经没了, 只剩 exports/ 这份副本
    missing=True exports/<slug>.json 本身也不在了
    """
    idx = _load_export_index()
    out: list[dict[str, Any]] = []
    for slug, it in (idx.get("items") or {}).items():
        item = dict(it)
        item["slug"] = slug
        card = Path(str(it.get("path") or (EXPORT_DIR / (slug + ".json"))))
        item["exists"] = card.exists()
        kwf = OUTPUT_DIR / str(it.get("book_id") or "") / "keywords.json"
        item["orphan"] = item["exists"] and not kwf.exists()
        item["missing"] = not item["exists"]
        out.append(item)
    out.sort(key=lambda x: x.get("exported_at") or x.get("generated_at") or "", reverse=True)
    return out


def latest_state() -> dict[str, Any]:
    """exports/latest.json 现在指向谁 / 是否还对应一份活着的产物."""
    idx = _load_export_index()
    slug = (idx.get("latest_slug") or "").strip()
    data = read_json(latest_export_path(), None)
    meta = (data or {}).get("meta") or {} if isinstance(data, dict) else {}
    if not slug:
        slug = meta.get("export_slug") or ""
    terms = 0
    if isinstance(data, dict):
        terms = sum(len((c or {}).get("items") or [])
                    for c in (data.get("categories") or {}).values())
    it = (idx.get("items") or {}).get(slug) or {}
    kwf = OUTPUT_DIR / str(it.get("book_id") or "") / "keywords.json" if it else None
    return {
        "slug": slug,
        "title": (meta.get("title") or it.get("title") or ""),
        "book_id": it.get("book_id", ""),
        "terms": terms,
        "pinned": idx.get("latest_pinned") is True,
        "exists": latest_export_path().exists(),
        # 产物目录已不在 (只有副本) / 或 exports/ 里没有对应条目
        "stale": bool(slug) and (not it or not (kwf and kwf.exists())),
    }


def clear_latest() -> dict[str, Any]:
    """撤掉 exports/latest.json + 取消 index 里的指向.

    用于"这份产物已经不要了"的情况: 副本 exports/<slug>.json 作为归档留下,
    但下游读 latest 会明确拿不到东西, 而不是读到一份旧数据。
    """
    prev = latest_state()
    try:
        latest_export_path().unlink()
    except FileNotFoundError:
        pass
    with _LOCK:
        idx = _load_export_index()
        idx.pop("latest_slug", None)
        idx.pop("latest_pinned", None)
        idx["updated_at"] = _now_iso()
        _atomic_write_json(export_index_path(), idx)
    return {"cleared": prev.get("slug", ""), "previous": prev}


def set_latest(slug: str, pin: bool = True) -> dict[str, Any] | None:
    """把 exports/latest.json 显式钉到某个 slug. 该 slug 没有产物文件则返回 None.

    存在的意义: latest 默认跟着"最近一次成功导出"走, 跑一次测试书就会被拨走;
    钉住之后 sync_exports 会照着 latest_slug 复原.
    """
    slug = (slug or "").strip()
    if slug.endswith(".json"):
        slug = slug[:-5]
    if not slug:
        return None
    src = EXPORT_DIR / (slug + ".json")
    kw = read_json(src, None)
    if not isinstance(kw, dict) or not kw.get("categories"):
        return None
    payload = dict(kw)
    meta = dict(payload.get("meta") or {})
    meta["exported_at"] = _now_iso()
    meta.setdefault("export_slug", slug)
    payload["meta"] = meta
    _atomic_write_json(latest_export_path(), payload)
    with _LOCK:
        idx = _load_export_index()
        idx["latest_slug"] = slug
        idx["latest_pinned"] = bool(pin)
        it = idx["items"].get(slug)
        if isinstance(it, dict):
            it["exported_at"] = meta["exported_at"]
        idx["updated_at"] = meta["exported_at"]
        _atomic_write_json(export_index_path(), idx)
    return {"slug": slug, "path": str(src), "latest": str(latest_export_path()),
            "pinned": bool(pin), "terms": sum(len((c or {}).get("items") or [])
                         for c in (payload.get("categories") or {}).values())}


def sync_exports() -> list[dict[str, Any]]:
    """启动时对账: outputs/ 里已有产物但 exports/ 缺或更旧的, 补一份.

    只补不删 (exports/ 里多出来的旧文件留着, 不主动清).
    """
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    idx = _load_export_index()["items"]
    done: list[dict[str, Any]] = []
    if not OUTPUT_DIR.exists():
        return done
    for d in sorted(OUTPUT_DIR.iterdir()):
        if not d.is_dir():
            continue
        kwf = d / "keywords.json"
        if not kwf.exists():
            continue
        bid = d.name
        kw = read_json(kwf, None)
        if not isinstance(kw, dict) or not kw.get("categories"):
            continue
        meta = kw.get("meta") or {}
        slug = book_slug(meta.get("path") or meta.get("source_epub")
                         or (get_book(bid) or {}).get("path"),
                         meta.get("title") or (get_book(bid) or {}).get("title"))
        cur = idx.get(slug) or {}
        try:
            newer = kwf.stat().st_mtime > (EXPORT_DIR / (slug + ".json")).stat().st_mtime
        except OSError:
            newer = True
        if cur.get("book_id") != bid or newer:
            r = export_keywords(bid, set_latest=False)
            if r:
                done.append(r)
    # latest.json 跟着 index 的 latest_slug 走 (显式钉住优先)
    idx_now = _load_export_index()
    want = (idx_now.get("latest_slug") or "").strip()
    latest = latest_export_path()
    cur_latest = read_json(latest, None)
    cur_slug = ((cur_latest or {}).get("meta") or {}).get("export_slug") if isinstance(cur_latest, dict) else None
    if want and want in idx_now["items"]:
        if cur_slug != want:
            kw = read_json(Path(idx_now["items"][want]["path"]), None)
            if isinstance(kw, dict) and kw.get("categories"):
                payload = dict(kw)
                payload["meta"] = dict(payload.get("meta") or {},
                                       export_slug=want,
                                       exported_at=_now_iso())
                _atomic_write_json(latest, payload)
    elif not latest.exists():
        # 没有显式指向时, 补成"最近生成的那一份"
        newest = None
        for slug, it in idx_now["items"].items():
            key = it.get("generated_at") or it.get("exported_at") or ""
            if newest is None or key > newest[0]:
                newest = (key, it)
        if newest:
            kw = read_json(Path(newest[1]["path"]), None)
            if isinstance(kw, dict):
                _atomic_write_json(latest, kw)
    return done


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

