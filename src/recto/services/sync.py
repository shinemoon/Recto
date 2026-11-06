"""云端进度同步 —— 掌阅 ⇄ Recto 阅读器。

唯一口径: **原书字节比例** (`readPercent`)。两边的位置都用
`(chapterId, f3)` 表达; Recto 阅读器的章号就是 spine 下标, 与云端
`chapterId + 1` 一一对应, 所以位置互换不需要任何中间映射表。

书的身份来自**书库里的边车 JSON**(`<书名>.recto.json` 的 `cloud` 段),
不再依赖任何外部书目数据库:

    cloud: {
      "id64": "<云书架 book_id>",     # 64 hex, 云盘视图
      "id32": "<文件内容 MD5 大写>",  # App 真正读写的 key
      "name": "<云端书名>",
      "total_count": 588
    }

推送只前进 (不把读者在手机上的位置往回拨), 除非显式 allow_rewind。
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from recto.core import config as _config_mod
from recto.core import geometry, paths, sidecar
from recto.core.paths import load_zy_auth
from recto.services import reader_state
from recto.sources import cloud

log = logging.getLogger("recto.sync")

_HEX64_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_LINK_CACHE: dict[str, object] = {"at": 0.0, "rows": []}
_NOTES_CACHE: dict[str, object] = {"at": 0.0, "map": {}}
_GEO_CACHE: dict[str, tuple[float, geometry.BookGeometry]] = {}
_PUSH_LOCK = threading.Lock()
_TIME_CACHE: dict[str, float] = {"at": 0.0}
_TIME_GAP = 600.0          # 时长拉的节流: 10 分钟内不重复打云端

# 自动同步的运行时状态 (只活在内存里; 权威时间戳在边车 JSON 上)
#   _PUSH_STATE[key] = {at, ok, pct, why, error, inflight, again, tries}
#   _PULL_BOOK_AT[key] = 上次自动拉这本的时间
_PUSH_STATE: dict[str, dict] = {}
_PULL_BOOK_AT: dict[str, float] = {}
# 打开书时"只比不写"的结论缓存: key -> {at, res}
_PEEK_CACHE: dict[str, dict] = {}

# 定时同步 (`[sync] interval_sec`) 的运行时状态 —— 与"翻页触发"是两条通道,
# 共用同一份节流窗口和同一份推送结论。
#   at/notes_at/time_at = 各自上一次跑的墙钟
#   runs / last / error  = 给设置页看的"最近一次定时同步"
_TICK_STATE: dict[str, object] = {"at": 0.0, "notes_at": 0.0, "time_at": 0.0,
                                 "runs": 0, "last": None, "error": None,
                                 "thread": None, "started_at": 0.0}

# 读者显式选过「留在本地」之后, 多久之内允许把进度写到云端更后面。
# 这是读者的明确选择, 不是我们擅自回拨; 也不是一劳永逸——
# 过期后回到默认的只前进, 免得哪天忘了这回事把云端写回旧位置。
LOCAL_WINS_HOURS = 24.0


def _sync_cfg(name: str, default=None):
    """读 [sync] 段的一项 —— 每次现读, 设置页改了立刻生效 (不用重启)。"""
    try:
        cfg = _config_mod.get_config()
        return cfg.get("sync", name, default)
    except Exception:  # noqa: BLE001 — 配置读不出来按默认走
        return default


def _setup_log() -> None:
    if log.handlers:
        return
    d = paths.LOG_DIR
    try:
        d.mkdir(parents=True, exist_ok=True)
        h = logging.FileHandler(d / "sync.log", encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(h)
    except OSError:
        pass
    log.setLevel(logging.INFO)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# 口径 / 身份
# --------------------------------------------------------------------------

def _normalize_percent(raw) -> tuple[float, bool]:
    """云端 readPercent -> 0..1。返 (value, ok)。

    float32 的轻微超界 (1.0000001) 归一到 1, 明显越界 (如 100) 视为坏值。
    """
    try:
        v = float(str(raw).strip())
    except (TypeError, ValueError):
        return 0.0, False
    if v > 1.0 + 1e-6:
        return 0.0, False
    return max(0.0, min(1.0, v)), True


def app_book_keys(zy_book_id: str) -> list[str]:
    """云端要写的 bookId 列表。

    云书架给 64 hex (MD5 内容 + MD5 路径); App 读写的却是
    32 hex 大写 (文件内容 MD5)。服务端把两者当**两份独立记录**,
    只写 64 的话手机永远看不到 —— 所以两条都写。
    """
    bid = (zy_book_id or "").strip()
    if not bid:
        return []
    keys = [bid]
    if _HEX64_RE.match(bid):
        k32 = bid[:32].upper()
        if k32 not in keys:
            keys.insert(0, k32)
    return keys


def file_md5(path: str | Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def _catalog_chapter(geo: geometry.BookGeometry | None, spine_chapter: int) -> int:
    """spine 章号 → 云端 App 的章号 (uploadProcess 的 chapterId 字段)。

    口径与 readPosition 一致: `chapterId = spine 下标 - 1`, 也就是
    `pre - 1`。实测 App 自己写的记录满足这条 (示例长篇 spine 158 → chapterId 157;
    示例短篇 spine 5 → pre `5_0_0`)。之前用 "spine+1-前置文档数" 拟合出来
    的式子只在示例长篇 (前置 2 篇) 上碰巧相等, 其它书会整体偏 1。
    """
    try:
        ch = int(spine_chapter)
    except (TypeError, ValueError):
        return 0
    if geo is None:
        return max(0, ch)
    return max(0, ch - 1)


def position_for(geo: geometry.BookGeometry | None, spine_chapter: int,
                 offset: int) -> str:
    """阅读器的 (spine 下标, 章内字节) → 云端 readPosition。

    readPosition 的 `pre` = **spine 下标** = `chapterId + 1`; 而 rprogress.chapter
    本来就是 spine 下标。所以要把 chapterId 减 1 再交给 build_position()。
    (2026-11-05 用真实云端记录钉死: pct 0.340034 ⇔ chapterId 194 = spine 195,
    旧代码把 spine 直接当 chapterId 用, 写出的 pre 是 196 —— 超前一整章,
    App "继续阅读" 就跳到下一章去了。)

    txt 书没有 spine/封面, chapter 就是章序号, 原样使用。
    """
    ch = int(spine_chapter)
    pos_chapter = max(0, ch - 1) if geo is not None else max(0, ch)
    return geometry.build_position(pos_chapter, int(offset))


def _geometry_of(path: str | Path) -> geometry.BookGeometry | None:
    p = Path(path)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return None
    hit = _GEO_CACHE.get(str(p))
    if hit and abs(hit[0] - mtime) < 1:
        return hit[1]
    try:
        geo = geometry.load_geometry(p)
    except Exception:
        log.debug("geometry failed: %s", p, exc_info=True)
        return None
    _GEO_CACHE[str(p)] = (mtime, geo)
    return geo


# --------------------------------------------------------------------------
# 书 ↔ 云端映射 (来自边车 JSON)
# --------------------------------------------------------------------------

def _norm_name(s: str) -> str:
    s = re.sub(r"[\s　]*[-–—_]+[\s　]*", " ", (s or "").strip())
    return re.sub(r"\s+", " ", s).strip().lower()


def links(refresh: bool = False) -> list[dict]:
    """所有已建立云端映射的书。

    返回 [{key, path, title, author, id64, id32, name, total_count}]。
    """
    if not refresh and time.time() - float(_LINK_CACHE["at"] or 0) < 30:
        return list(_LINK_CACHE["rows"])  # type: ignore[arg-type]
    reader_state.scan()
    out: list[dict] = []
    for book_path, data in sidecar.iter_sidecars():
        c = data.get("cloud") or {}
        if not (c.get("id64") or c.get("id32")):
            continue
        key = reader_state.book_key_by_path(book_path)
        if not key:
            continue
        row = reader_state.get_book(key) or {}
        out.append({
            "key": key,
            "path": str(book_path),
            "title": row.get("title") or Path(book_path).stem,
            "author": row.get("author") or "",
            "id64": str(c.get("id64") or ""),
            "id32": str(c.get("id32") or ""),
            "name": str(c.get("name") or row.get("title") or Path(book_path).stem),
            "total_count": int(c.get("total_count") or 0),
            "bytes": int(row.get("bytes") or 0),
        })
    _LINK_CACHE["at"] = time.time()
    _LINK_CACHE["rows"] = out
    return out


def _link_by_key(key: str) -> dict | None:
    for row in links():
        if row["key"] == key:
            return row
    return None


def _link_light(key: str) -> dict | None:
    """只看这一本书自己的边车, 不扫书库。

    进度每几秒就保存一次, 那条路径上不需要"全库映射表" —— 扫一遍书库
    在书多的机器上会被翻页拖成肉眼可见的卡顿。
    """
    row = reader_state.get_book(key) or {}
    path = str(row.get("path") or "")
    if not path:
        return None
    link = sidecar.cloud_link(path) or {}
    if not (link.get("id64") or link.get("id32")):
        return None
    return {
        "key": key, "path": path,
        "title": row.get("title") or Path(path).stem,
        "author": row.get("author") or "",
        "id64": str(link.get("id64") or ""),
        "id32": str(link.get("id32") or ""),
        "name": str(link.get("name") or row.get("title") or Path(path).stem),
        "total_count": int(link.get("total_count") or 0),
        "bytes": int(row.get("bytes") or 0),
    }


def ensure_links() -> dict:
    """给书库里还没映射的书按书名匹配云端个人书架, 写回边车。

    下载时已经写过映射; 这里兜底"手动放进书库"的书。
    """
    from recto.services import acquisition

    try:
        shelf = acquisition.shelf_books()
    except Exception as e:  # 云端不可达时不该影响本地阅读
        log.warning("对齐映射 · 取不到云端书架 · %s", e)
        return {"ok": False, "added": 0, "error": str(e)}
    by_name: dict[str, dict] = {}
    for it in shelf:
        by_name.setdefault(_norm_name(it["name"]), it)
    reader_state.scan()
    added = 0
    for book in reader_state.list_books():
        p = Path(book["path"])
        if sidecar.cloud_link(p):
            continue
        hit = by_name.get(_norm_name(Path(p).stem))
        if not hit:
            continue
        sidecar.set_cloud(
            p,
            id64=hit["id"],
            id32=file_md5(p),
            name=hit["name"],
            author=hit.get("author") or "",
            matched_at=_now(),
        )
        added += 1
    _LINK_CACHE["at"] = 0.0
    return {"ok": True, "added": added, "shelf": len(shelf)}


# --------------------------------------------------------------------------
# 云端当前状态
# --------------------------------------------------------------------------

def _zy_notebook_pct(force: bool = False) -> dict[str, tuple[float, str | None, int | None, float]]:
    """App 自己写的进度 (getUserNotebooks 的 readpercent/readpostion)。

    只覆盖"有笔记的书", 但那份通常比 getProgress 更新 (App 在本地写)。
    返回 {book32lower: (pct01, chapterId, f3, 更新时间ms)}。
    """
    if not force and time.time() - float(_NOTES_CACHE["at"] or 0) < 120:
        return dict(_NOTES_CACHE["map"])  # type: ignore[arg-type]
    out: dict[str, tuple[float, str | None, int | None, float]] = {}
    try:
        from recto.services import notes as zy_notes

        for nb in zy_notes.notebooks():
            bid = str(nb.get("bookid") or nb.get("bookId") or "")
            if not bid:
                continue
            pct, ok = _normalize_percent(nb.get("readpercent"))
            if not ok:
                continue
            pos = str(nb.get("readpostion") or nb.get("readPosition") or "")
            parsed = geometry.parse_position(pos)
            try:
                ts = float(nb.get("updatetime") or nb.get("updateTime") or 0)
            except (TypeError, ValueError):
                ts = 0.0
            out[bid.strip().lower()[:32]] = (
                pct,
                None if parsed.get("chapter_id") is None else str(parsed["chapter_id"]),
                int(parsed.get("f3") or 0),
                ts,
            )
    except Exception:
        log.debug("notebooks 进度读取失败", exc_info=True)
    _NOTES_CACHE["at"] = time.time()
    _NOTES_CACHE["map"] = out
    return dict(out)


def _cloud_state(auth: dict, rows: Iterable[dict]) -> dict[str, dict]:
    """读云端当前进度, 按书的 id64 归一成一份。

    云端对同一本书有 **三份记录**: getProgress 的 32 hex / 64 hex 两个 key,
    以及 getUserNotebooks 里 App 自己写的那份。三份互不同步, 更新时刻也不同。

    **取"最后读过的那个" (最新 updateTime), 不是取最大百分比。**
    取最大值曾经把"我在手机读到 31.7%, 但桥接上次推过 34%"混成 34% ——
    拉回来就跳到读者还没读到的位置 ("云端进度到了本地不是真正阅读地点")。

    返回 {id64: {pct, finished, chapter_id, total_count, ts, src, max_pct,
                 position, max_f3}}。
    `max_pct` 只给"别往回拨"的闸门用。
    """
    rows = list(rows)
    owner: dict[str, str] = {}
    keys: list[str] = []
    for r in rows:
        for k in app_book_keys(r["id64"] or r["id32"]):
            owner[k.strip().upper()] = r["id64"] or r["id32"]
            if k not in keys:
                keys.append(k)
    recs: dict = {}
    if keys:
        try:
            recs = cloud.get_progress(auth, keys) or {}
        except Exception:
            log.warning("取云端进度失败", exc_info=True)

    cands: dict[str, list[dict]] = {}
    for k, rec in recs.items():
        bid = owner.get(str(k).strip().upper())
        if not bid:
            continue
        rec = rec or {}
        rp = rec.get("readProcess") or {}
        pct, ok = _normalize_percent(rp.get("readPercent"))
        pos = str(rp.get("readPosition") or "")
        parsed = geometry.parse_position(pos)
        try:
            ts = float(rec.get("updateTime") or rp.get("lastReadDate") or 0)
        except (TypeError, ValueError):
            ts = 0.0
        cands.setdefault(bid, []).append({
            "pct": pct if ok else None,
            "finished": str(rp.get("readComplete") or "0").strip() == "1",
            "chapter_id": (str(rp["chapterId"]) if rp.get("chapterId") not in (None, "") else None),
            "total_count": _int_or_none(rp.get("totalCount")),
            "ts": ts,
            "src": "progress32" if str(k).strip().upper()[:32] == str(k).strip().upper() else "progress64",
            "position": pos,
            "pos_chapter": parsed.get("chapter_id"),
            "pos_f3": _int_or_none(parsed.get("f3")),
        })

    nb = _zy_notebook_pct()
    for r in rows:
        bid = r["id64"] or r["id32"]
        nbv = nb.get(str(r["id32"] or bid)[:32].lower())
        if not nbv:
            continue
        cands.setdefault(bid, []).append({
            "pct": nbv[0], "finished": False,
            "chapter_id": nbv[1], "total_count": None,
            "ts": float(nbv[3] or 0), "src": "notebook",
            "position": "", "pos_chapter": _int_or_none(nbv[1]), "pos_f3": nbv[2],
        })

    out: dict[str, dict] = {}
    for bid, lst in cands.items():
        good = [c for c in lst if c.get("pct") is not None]
        if not good:
            continue
        # 同一时刻 (我们同时写 32/64, 时间戳只差几毫秒) 取百分比大的那条
        good.sort(key=lambda c: (float(c.get("ts") or 0), float(c.get("pct") or 0)))
        chosen = dict(good[-1])
        chosen["max_pct"] = max(float(c.get("pct") or 0) for c in good)
        chosen["records"] = len(good)
        out[bid] = chosen
    return out


# 云端 readPosition 与 readPercent 互相对不上时（同一本书两份不同排版的
# 文件），位置就不能信。允许的偏差 = 4KB（远大于 float32 反解误差，又远小于
# 任何一次真实的"文件不是同一份"）。
_POS_GUARD_BYTES = 4096


def _anchor_for_cloud(key: str, ent: dict) -> tuple[int, int, str]:
    """云端记录 → (章下标, 章内字节偏移, 依据)。

    **优先用 readPosition 的 (chapterId, f3)**：那是云端自己记的最细坐标，
    不经过 0-1 比例，长书上不会因为 percent 的小数位而漂。只有位置缺失、
    或者位置与 readPercent 明显矛盾（不是同一份文件）时才退回按比例反解。
    """
    pct = float(ent.get("pct") or 0.0)
    cid, f3 = ent.get("pos_chapter"), ent.get("pos_f3")
    if cid is not None and f3 is not None:
        pair = reader_state.anchor_from_position(key, int(cid), int(f3))
        pos_pct = reader_state.percent_of_cloud_position(key, int(cid), int(f3))
        if pair and pos_pct is not None:
            total = reader_state.book_bytes(key)
            drift_bytes = abs(pos_pct - pct) * total if total else 0.0
            if drift_bytes <= _POS_GUARD_BYTES:
                return pair[0], pair[1], "position"
            log.debug("云端位置与比例不一致 (%s): %.0f 字节, 退回按比例反解",
                      key, drift_bytes)
    ch, off = reader_state.anchor_from_percent(key, pct)
    return ch, off, "percent"


def _local_wins(book_path: str) -> bool:
    """读者显式选过「留在本地」吗 (边车 `cloud.local_wins_at`, 24h 内有效)。

    选过一次之后, 本书的推送允许把进度写到云端更后面 —— 否则
    「云端 32%、本地 15%」会永远卡在 regress, 读者本地怎么读都推不上去。
    """
    if not book_path:
        return False
    try:
        link = sidecar.cloud_link(book_path) or {}
    except Exception:  # noqa: BLE001 — 边车读不出来就当没选过
        return False
    ts = link.get("local_wins_at")
    if not ts:
        return False
    try:
        when = datetime.fromisoformat(str(ts))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.astimezone()
    age = (datetime.now().astimezone() - when).total_seconds()
    return 0 <= age < LOCAL_WINS_HOURS * 3600


def _local_progress_ts(prog: dict) -> float:
    """本地这条进度的写入时刻 (epoch 秒)。"""
    ts = str((prog or {}).get("updated_at") or "")
    if not ts:
        return 0.0
    try:
        when = datetime.fromisoformat(ts)
    except ValueError:
        return 0.0
    if when.tzinfo is None:
        when = when.astimezone()
    return when.timestamp()


# 云端/本地写入时刻的对比容差: 手机与本机时钟不可能完全一致
CLOCK_SKEW = 180.0


def _cloud_is_newer(cur: dict, prog: dict) -> bool:
    """云端那条记录是否比本地这条**明显**更新 (手机那边刚读过)。"""
    cloud_ts = float((cur or {}).get("ts") or 0.0)
    if cloud_ts > 1e12:            # 毫秒
        cloud_ts /= 1000.0
    local_ts = _local_progress_ts(prog)
    if not cloud_ts or not local_ts:
        return False
    return cloud_ts > local_ts + CLOCK_SKEW


def _int_or_none(v) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# PUSH: Recto → 云端
# --------------------------------------------------------------------------

def audit(msg: str, *args) -> None:
    """写一条人话到同步日志。

    notes / sessions 也走这里 —— 界面上「最近日志」只有一份, 措辞统一比各写各的好。
    """
    _setup_log()
    log.info(msg, *args)


def push_decision(cur_pct: float | None, cur_done: bool, new_pct: float,
                  new_done: bool, *, tol: float = 0.0005,
                  allow_rewind: bool = False,
                  meta_stale: bool = False,
                  cloud_newer: bool = False) -> tuple[bool, str]:
    """要不要把本地进度写到云端。

    两条规则并存:

    * **读者自己动了** (`allow_rewind`, 翻页 / 目录跳转 / 划线跳转) —— 本地就是
      他眼前的真实位置, 如实覆写云端, 哪怕比云端更靠后。掌阅 App 自己也
      是这个语义 (退出书把本地位置回写云端, 最后写入者胜)。
    * **没人动** (定时 / 打开书对齐 / 别处的旧会话) —— 只前进, 绝不擅自把
      云端往回拨。`meta_stale` 允许进度不变时修一次元信息 (totalCount),
      但也不借机回退。

    `cloud_newer` = 云端记录的写入时刻比本地这条更新 (手机刚读过) —— 这时
    连"读者自己动"也先让别人一次: 人家那边是更新的现场, 我们这边只是旧页
    在回翻。等本地真的再读一页, 时间就翻过来了。
    """
    if cur_pct is None:
        return True, "no-baseline"
    if cur_done and not new_done:
        return False, "cloud-already-done"
    if new_done and not cur_done:
        return True, "finish"
    behind = new_pct < cur_pct - tol
    if not allow_rewind:
        if behind:
            if cloud_newer:
                return False, f"cloud-newer({new_pct:.4f}<{cur_pct:.4f})"
            return False, f"regress({new_pct:.4f}<{cur_pct:.4f})"
        if abs(new_pct - cur_pct) <= tol * 0.5:
            if meta_stale and new_pct >= cur_pct - 1e-6:
                return True, "meta"
            return False, "noop"
        return True, "forward"
    if behind:
        if cloud_newer:
            return False, f"cloud-newer({new_pct:.4f}<{cur_pct:.4f})"
        return True, f"rewind({new_pct:.4f}<{cur_pct:.4f})"
    if abs(new_pct - cur_pct) <= tol * 0.5:
        if meta_stale and new_pct >= cur_pct - 1e-6:
            return True, "meta"
        return False, "noop"
    return True, "forward"


def push_once(auth: dict | None = None, *, keys: Iterable[str] | None = None,
              dry_run: bool = False, allow_rewind: bool = False,
              force: bool = False) -> dict:
    """把阅读器里的进度推到云端。返回 {pushed, skipped, failed, detail}.

    force=True 绕过 push_decision (修历史脏数据 / 单本重写用)。
    """
    _setup_log()
    auth = auth or load_zy_auth()
    rows = links()
    if keys is not None:
        want = set(keys)
        rows = [r for r in rows if r["key"] in want]
    state = _cloud_state(auth, rows)
    pushed = skipped = failed = 0
    detail: list[dict] = []
    for r in rows:
        key = r["key"]
        prog = reader_state.get_progress(key) or {}
        if not prog:
            skipped += 1
            detail.append({"book": r["title"], "skipped": "no-progress"})
            continue
        pct = float(prog.get("percent") or 0.0)
        done = bool(prog.get("complete"))
        geo = _geometry_of(r["path"])
        spine_ch = int(prog.get("chapter") or 0)
        offset = int(prog.get("offset") or 0)
        catalog_ch = _catalog_chapter(geo, spine_ch)
        position = position_for(geo, spine_ch, offset)
        cur = state.get(r["id64"] or r["id32"]) or {}
        # 闸门看"云端三份记录里最靠前的那一个"：往回拨的代价比多写一次大
        cur_pct = cur.get("max_pct") if cur.get("max_pct") is not None else cur.get("pct")
        total_count = _total_count_for(geo, cur.get("total_count"), catalog_ch)
        # 只有 totalCount 明显不对才允许"进度不变也写一次"。
        # chapterId 是 App 自己的目录口径, 跟我们算的差一两个是常态 (前置文档
        # 计数方式不同), 不值得为它去改读者眼前的记录。
        meta_stale = (cur.get("total_count") is not None
                      and int(cur["total_count"]) != int(total_count))
        # 读者显式选过「留在本地」→ 这本书允许把进度写到云端更后面
        # (那是他自己的选择, 不是我们擅自回拨)。
        wins = _local_wins(r.get("path") or "")
        # 云端那条记录是不是比本地这条更新 (手机刚读过) —— 是的话这次先让一让
        cloud_newer = _cloud_is_newer(cur, prog)
        allow, why = push_decision(cur_pct, bool(cur.get("finished")), pct, done,
                                   allow_rewind=allow_rewind or wins,
                                   meta_stale=meta_stale,
                                   cloud_newer=cloud_newer and not wins)
        if force and not allow:
            allow, why = True, "force"
        if not allow:
            skipped += 1
            detail.append({"book": r["title"], "skipped": why,
                           "pct": round(pct, 8), "cloud": cur_pct,
                           "bytes": reader_state.book_bytes(key)})
            continue
        if dry_run:
            detail.append({"book": r["title"], "dry": True, "why": why,
                           "pct": round(pct, 8), "chapter": catalog_ch,
                           "total": total_count,
                           "bytes": reader_state.book_bytes(key)})
            continue
        ok_keys, errs = [], []
        for bid in app_book_keys(r["id64"] or r["id32"]):
            try:
                cloud.upload_progress(
                    auth, book_id=bid, book_name=r["name"],
                    chapter_id=str(catalog_ch),
                    percent=f"{pct:.8f}".rstrip("0").rstrip("."),
                    read_complete="1" if done else "0",
                    total_count=str(total_count),
                    read_position=position,
                    source="recto", device_name="Recto",
                )
                ok_keys.append(bid)
            except Exception as e:  # noqa: BLE001
                errs.append(f"{bid[:8]}:{e}")
        if ok_keys:
            pushed += 1
            _touch = {"last_push_at": _now(),
                      "last_push_pct": round(pct, 8), "total_count": total_count}
            if wins:
                # 覆盖完成 → 解除「本地优先」, 之后照常只前进
                _touch["local_wins_at"] = ""
            sidecar.touch_cloud(r["path"], **_touch)
            detail.append({"book": r["title"], "pct": round(pct, 8), "why": why,
                           "chapter": catalog_ch, "total": total_count, "ok": True,
                           "bytes": reader_state.book_bytes(key)})
            if str(why).startswith("rewind"):
                log.info("按本地进度覆写云端 · %s · 本地 %s 比云端靠前一条更旧的位置",
                         r["title"], geometry.format_pct(pct, reader_state.book_bytes(key)))
            log.info("推送成功 · %s · 进度 %s · 第 %s 章（共 %s 章）", r["title"],
                     geometry.format_pct(pct, reader_state.book_bytes(key)),
                     int(catalog_ch) + 1, total_count)
        else:
            failed += 1
            detail.append({"book": r["title"], "error": "; ".join(errs) or "no key"})
            log.warning("推送失败 · %s · %s", r["title"], "; ".join(errs))
        time.sleep(0.2)
    _LINK_CACHE["at"] = 0.0
    return {"pushed": pushed, "skipped": skipped, "failed": failed, "detail": detail,
            "at": _utc()}


def _total_count_for(geo: geometry.BookGeometry | None, cur: int | None,
                     chapter_id: int) -> int:
    """云端 totalCount 写什么: 云端自洽值 > epub 目录章数 > 当前章 + 1。"""
    if cur and cur >= 2 and cur >= chapter_id + 1:
        return int(cur)
    if geo is not None and geo.total_count > 0:
        return int(geo.total_count)
    return max(2, chapter_id + 1)


def sync_settings() -> dict:
    """当前生效的自动同步设置 (每次现读, 设置页改完立刻生效)。"""
    return {
        "auto": bool(_sync_cfg("auto", True)),
        "pull_on_open": bool(_sync_cfg("pull_on_open", True)),
        "push_debounce_sec": float(_sync_cfg("push_debounce_sec", 12) or 12),
        "pull_gap_sec": float(_sync_cfg("pull_gap_sec", 120) or 120),
        "flush_on_hide": bool(_sync_cfg("flush_on_hide", True)),
        "stamp": bool(_sync_cfg("stamp", True)),
        # 定时通道: 0 = 关闭。翻页推送是"读者动一下", 这个是"心跳",
        # 两者互补 —— 停在一页上不动时进度/划线/时长照样会被带上去。
        "interval_sec": float(_sync_cfg("interval_sec", 60) or 0),
    }


def tick_settings() -> dict:
    """定时同步的具体节奏 (「心跳」通道)。

    `interval_sec` 是主周期 (每这么久看一眼有没有活可干);
    划线/时长比它稀疏, 各自有周期, 免得每分钟都去打一次云端。
    """
    def _num(name: str, default: float) -> float:
        try:
            return float(_sync_cfg(name, default) or 0)
        except (TypeError, ValueError):
            return default

    return {
        "interval_sec": max(0.0, _num("interval_sec", 60)),
        "active_min": max(1.0, _num("tick_active_min", 30)),
        "notes": bool(_sync_cfg("tick_notes", True)),
        "notes_sec": max(30.0, _num("tick_notes_sec", 300)),
        "time": bool(_sync_cfg("tick_time", True)),
        "time_sec": max(60.0, _num("tick_time_sec", 600)),
    }


def _state_of(key: str) -> dict:
    """一本书的同步状态 (不读云端, 只看边车 + 内存 —— 要快)。

    state:
      no-link   这本书还没跟云端对上 (没有边车映射)
      off       自动同步被关了
      synced    本地最后位置已经推上去
      dirty     本地动过, 还在等节流窗口
      pushing   正在推
      cloud-ahead  云端更靠前, 这一推还没轮到 (节流窗口内)。读者一动就会覆写
      failed    上一次推失败了
    """
    st = sync_settings()
    rec = _PUSH_STATE.get(key) or {}
    out: dict = {
        "state": "no-link",
        "last_push_at": None, "last_push_pct": None,
        "last_pull_at": None, "last_pull_pct": None,
        "inflight": bool(rec.get("inflight")),
        "ok": rec.get("ok"), "error": rec.get("error"), "why": rec.get("why"),
        "pending_pct": rec.get("pending_pct"),
        "cloud_ahead": bool(rec.get("cloud_ahead")),
        "cloud_pct": rec.get("cloud_pct"),
        # 上一次推送是"按本地覆盖云端"(读者自己动的) —— 小字要说实话
        "rewound": str(rec.get("why") or "").startswith("rewind"),
    }
    row = _link_light(key)
    if row:
        link = sidecar.cloud_link(row["path"]) or {}
        out["last_push_at"] = link.get("last_push_at")
        out["last_push_pct"] = link.get("last_push_pct")
        out["last_pull_at"] = link.get("last_pull_at")
        out["last_pull_pct"] = link.get("last_pull_pct")
        if rec.get("inflight"):
            out["state"] = "pushing"
        elif rec.get("ok") and rec.get("cloud_ahead"):
            out["state"] = "cloud-ahead"
        elif rec.get("pending_pct") is not None:
            # 本地比云端靠后 → 推不上去。这时报「同步中…」是谎话:
            # 每存一次进度又把 pending 填回来, 小字会永远转圈。
            cp = rec.get("cloud_pct")
            if cp is not None and float(cp) > float(rec["pending_pct"] or 0) + 1e-6:
                out["state"] = "cloud-ahead"
            else:
                out["state"] = "dirty"
        elif rec.get("ok") is False:
            out["state"] = "failed"
        else:
            out["state"] = "synced"
    if not st["auto"]:
        out["state"] = "off"
    return out


def sync_state(key: str) -> dict:
    """同步状态 + 生效设置 (阅读页右下角那行小字 / 前端判断用)。"""
    out = _state_of(key)
    out["settings"] = sync_settings()
    return out


def _push_worker(key: str) -> None:
    """后台把这本书的进度推上去。节流在调用方做, 这里只管推。"""
    for _ in range(2):        # 推期间又存了进度 → 最多再补一次
        ok = False
        why = ""
        err = None
        pct = None
        rec_cloud = None
        ahead = False
        with _PUSH_LOCK:
            rec0 = _PUSH_STATE.setdefault(key, {})
            user_driven = bool(rec0.get("user"))
            rec0["user"] = False
        try:
            res = push_once(keys=[key], allow_rewind=user_driven)
            det = (res.get("detail") or [{}])[0]
            ok = int(res.get("pushed") or 0) > 0
            why = str(det.get("why") or det.get("skipped") or "")
            err = det.get("error")
            pct = det.get("pct")
            if why.startswith(("regress(", "cloud-newer(")):
                # 「云端比本地靠前」时我们没写 —— 把小字要显示的**云端**百分比留出来
                try:
                    rec_cloud = float(str(why).split("<", 1)[1].rstrip(")"))
                except (IndexError, ValueError):
                    rec_cloud = None
            if not ok and not err:
                # "没写" 不等于 "失败": 云端已经是这个位置 / 云端更靠前 / 本地没进度,
                # 三种都是正常结局。只有真的报错才算失败 (否则小字永远挂"同步失败")。
                ahead = why.startswith("regress") or why == "cloud-already-done"
                ok = ahead or why in ("noop", "no-baseline", "no-progress")
        except Exception as e:  # noqa: BLE001 — 网络抖动不该冒到阅读页
            err = f"{type(e).__name__}: {e}"
            log.warning("自动推送失败 · %s · %s", key, e)
        with _PUSH_LOCK:
            rec = _PUSH_STATE.setdefault(key, {})
            rec["at"] = time.time()
            rec["ok"] = bool(ok)
            rec["why"] = why
            rec["error"] = err
            rec["cloud_ahead"] = bool(ahead)
            if pct is not None:
                rec["pct"] = pct
            if rec_cloud is not None:
                rec["cloud_pct"] = rec_cloud
            rec.pop("pending_pct", None)
            rec["inflight"] = False
            again = bool(rec.get("again"))
            rec["again"] = False
        if not again:
            return
        time.sleep(0.5)


def _delayed_push(key: str) -> None:
    """节流窗口到了再补一次 (读者停手之后最后那个位置不能一直挂着)。"""
    with _PUSH_LOCK:
        rec = _PUSH_STATE.setdefault(key, {})
        if rec.get("inflight"):
            rec["again"] = True
            return
        if rec.get("pending_pct") is None:
            return
        rec["inflight"] = True
    threading.Thread(target=_push_worker, args=(key,), daemon=True,
                     name="recto-push").start()


def mirror_reader_progress(key: str, pct: float, *, finished: bool = False,
                           flush: bool = False, user: bool = True) -> dict:
    """阅读器存进度后顺手推云端 —— **放到后台线程, 不阻塞翻页**。

    节流 = `[sync] push_debounce_sec`; `flush=True` 绕过节流 (离开页面时的最后一推)。

    `user=True` (默认) = 这次进度是**读者自己动出来的** (翻页 / 目录跳转 /
    划线跳转 / 关页面时的最后一推)。这种推送允许把云端写到更靠后的位置 ——
    本地就是他眼前的位置, 该如实覆写; 不这样的话"云端 32.8%、本地 31.9%"
    会永远卡在 regress, 读者在本地怎么读云端都不动, 下次打开又被拽回去。

    `user=False` = 系统性的对齐/补写 (打开书后的对齐、CLI 批量), 仍旧只前进。
    """
    st = sync_settings()
    if not st["auto"]:
        return {"ok": False, "skipped": "auto-off", "state": _state_of(key)}
    row = _link_light(key)
    if not row:
        return {"ok": False, "skipped": "no-cloud-link", "state": _state_of(key)}
    now = time.time()
    gap = float(st["push_debounce_sec"] or 12)
    with _PUSH_LOCK:
        rec = _PUSH_STATE.setdefault(key, {})
        rec["pending_pct"] = round(float(pct), 8)
        if user:
            # 一次性标记: 消费完就清, 别让一次读者动作永久打开回拨
            rec["user"] = True
        if rec.get("inflight"):
            rec["again"] = True          # 这一推跑完再补一次, 不丢最后位置
            return {"ok": True, "queued": True, "inflight": True, "state": _state_of(key)}
        last = float(rec.get("at") or 0)
        if not flush and now - last < gap:
            # 节流: 不立刻打云端, 但**排一个到点的补推** ——
            # 否则读者停在原地不再翻页, 最后一个位置就永远挂在那里,
            # 右下角一直显示"同步中…"。
            wait = max(0.5, gap - (now - last))
            threading.Timer(wait, _delayed_push, args=(key,)).start()
            return {"ok": True, "throttled": True, "retry_in": round(wait, 1),
                    "state": _state_of(key)}
        rec["inflight"] = True
    threading.Thread(target=_push_worker, args=(key,), daemon=True,
                     name="recto-push").start()
    return {"ok": True, "queued": True, "state": _state_of(key)}


# --------------------------------------------------------------------------
# 定时同步 —— 与"翻页触发"并行的第二条通道 (心跳)
# --------------------------------------------------------------------------
#
# 翻页推送解决"读者动了", 但下面这些情况它一次都不会推:
#   * 停在一页上看很久 (进度不变, 但阅读时长在涨)
#   * 在阅读器里加了划线 / 书签却不翻页
#   * 页面已经关了, 队列里还躺着没推的时长
# 定时器就是干这个的: 每 `[sync] interval_sec` 秒看一眼**最近读过的书**,
# 有活就推, 没活动就什么都不做 (不打网络, 也不写日志)。

def _iso_to_epoch(s: str | None) -> float | None:
    """`rprogress.updated_at` 那种本地朴素时间 ("2026-11-06T22:31:05") → 墙钟秒。"""
    if not s:
        return None
    txt = str(s)
    try:
        return time.mktime(datetime.strptime(txt[:19], "%Y-%m-%dT%H:%M:%S").timetuple())
    except Exception:  # noqa: BLE001 —— 带时区 / 带微秒的写法
        try:
            return datetime.fromisoformat(txt).timestamp()
        except Exception:
            return None


def active_keys(window_sec: float | None = None) -> list[str]:
    """最近动过进度的书 (默认 `[sync] tick_active_min` 分钟), 且跟云端有映射。

    没活动就不打网络: 人没读书的时候定时器该是安静的, 日志也不该被 noop 刷满。
    """
    if window_sec is None:
        window_sec = tick_settings()["active_min"] * 60
    now = time.time()
    out: list[str] = []
    try:
        books = reader_state.list_books()
    except Exception as e:  # noqa: BLE001 — 书库读不出来不该让定时器死掉
        log.warning("定时同步 · 书库读不出来 · %s", e)
        return out
    for b in books:
        ts = _iso_to_epoch(b.get("updated_at"))
        if not ts or now - ts > float(window_sec):
            continue
        key = str(b.get("key") or "")
        if key and _link_light(key):
            out.append(key)
    return out


def _changed_keys(keys: list[str]) -> list[str]:
    """这些书里, 本地进度跟"上次推上去的"不一样的那几本。

    没有这一步的话, 每 60 秒都要为了一本没翻过的书去云端问一圈 —— 白打网络,
    而 `push_once` 最后也只会给一句 noop。
    """
    out: list[str] = []
    for key in keys:
        row = _link_light(key)
        if not row:
            continue
        prog = reader_state.get_progress(key) or {}
        if not prog:
            continue
        pct = float(prog.get("percent") or 0.0)
        link = sidecar.cloud_link(row["path"]) or {}
        last = link.get("last_push_pct")
        try:
            if last is None or abs(float(last) - pct) > 1e-9:
                out.append(key)
        except (TypeError, ValueError):
            out.append(key)
    return out


def tick_once(*, force: bool = False) -> dict:
    """跑一次定时同步。

    **只走"只前进"的闸门** (`allow_rewind=False`): 定时器替读者做主才是不对的 ——
    回拨只允许读者自己动出来的位置 (翻页 / 目录跳转)。
    """
    st = sync_settings()
    now = time.time()
    _TICK_STATE["at"] = now
    _TICK_STATE["runs"] = int(_TICK_STATE.get("runs") or 0) + 1
    out: dict = {"at": _now(), "keys": [], "actions": []}
    if not st["auto"]:
        out["skipped"] = "auto-off"
        _TICK_STATE["last"] = out
        return out
    tk = tick_settings()
    keys = active_keys(tk["active_min"] * 60)
    out["keys"] = keys
    if not keys:
        out["activity"] = "idle"
        _TICK_STATE["last"] = out
        _TICK_STATE["error"] = None
        return out

    # 1) 进度 (只前进) —— 只对"本地跟上次推上去的不一样"的书打云端
    push_keys = _changed_keys(keys)
    if push_keys:
        try:
            res = push_once(keys=push_keys)
            out["push"] = {k: int(res.get(k) or 0) for k in ("pushed", "skipped", "failed")}
            if out["push"]["pushed"]:
                out["actions"].append("progress")
        except Exception as e:  # noqa: BLE001
            out["push_error"] = f"{type(e).__name__}: {e}"
            log.warning("定时同步 · 进度推送失败 · %s", e)
        # 两条通道共用同一个节流窗口: 定时推完, 紧跟着的翻页不必再打一次云端
        with _PUSH_LOCK:
            for k in push_keys:
                _PUSH_STATE.setdefault(k, {})["at"] = now

    # 2) 划线 / 书签 (稀疏得多)
    if tk["notes"] and (force or now - float(_TICK_STATE.get("notes_at") or 0) >= tk["notes_sec"]):
        _TICK_STATE["notes_at"] = now
        try:
            from recto.services import notes as _notes

            pu = _notes.push(keys=keys, quiet=True)
            pl = _notes.pull(keys=keys, quiet=True)
            out["notes"] = {
                "pushed": int(pu.get("pushed") or 0) + int(pu.get("pushed_marks") or 0),
                "deleted": int(pu.get("deleted") or 0),
                "added": int(pl.get("added") or 0),
                "updated": int(pl.get("updated") or 0),
                "failed": len(pu.get("errors") or []),
            }
            if any(out["notes"].get(k) for k in ("pushed", "deleted", "added", "updated")):
                out["actions"].append("notes")
        except Exception as e:  # noqa: BLE001
            out["notes_error"] = f"{type(e).__name__}: {e}"
            log.warning("定时同步 · 划线同步失败 · %s", e)

    # 3) 今天的阅读时长 (云端只认"今天", 所以每次都是今天这一档)
    if tk["time"] and (force or now - float(_TICK_STATE.get("time_at") or 0) >= tk["time_sec"]):
        _TICK_STATE["time_at"] = now
        try:
            from recto.services import sessions as _sessions

            tm = _sessions.push(keys=keys)
            out["time"] = {"queued": int(tm.get("queued") or 0),
                           "written": int(tm.get("written") or 0),
                           "failed": len(tm.get("errors") or [])}
            if out["time"]["written"]:
                out["actions"].append("time")
        except Exception as e:  # noqa: BLE001
            out["time_error"] = f"{type(e).__name__}: {e}"
            log.warning("定时同步 · 时长推送失败 · %s", e)

    _TICK_STATE["last"] = out
    _TICK_STATE["error"] = None
    return out


def tick_state() -> dict:
    """定时同步的生效设置 + 最近一次结果 (设置页显示用, 只读内存)。"""
    tk = tick_settings()
    last = _TICK_STATE.get("last") or {}
    return {
        "enabled": tk["interval_sec"] > 0,
        "interval_sec": int(round(tk["interval_sec"])),
        "active_min": int(round(tk["active_min"])),
        "notes": tk["notes"], "notes_sec": int(round(tk["notes_sec"])),
        "time": tk["time"], "time_sec": int(round(tk["time_sec"])),
        "running": bool((_TICK_STATE.get("thread") or {}).is_alive())
        if hasattr(_TICK_STATE.get("thread"), "is_alive") else False,
        "runs": int(_TICK_STATE.get("runs") or 0),
        "last_at": last.get("at"),
        "last_keys": list(last.get("keys") or []),
        "last_actions": list(last.get("actions") or []),
        "last_activity": last.get("activity") or last.get("skipped"),
        "error": _TICK_STATE.get("error"),
    }


def start_ticker() -> bool:
    """起后台心跳线程 (幂等)。返回是否真的新起了一个。"""
    th = _TICK_STATE.get("thread")
    if th is not None and getattr(th, "is_alive", lambda: False)():
        return False
    _TICK_STATE["stop"] = False
    _TICK_STATE["started_at"] = time.time()
    thread = threading.Thread(target=_ticker_loop, daemon=True, name="recto-tick")
    _TICK_STATE["thread"] = thread
    thread.start()
    log.info("定时同步已启动 · 每 %d 秒看一眼最近读过的书",
             int(round(tick_settings()["interval_sec"])) or 0)
    return True


def stop_ticker() -> None:
    _TICK_STATE["stop"] = True


def _ticker_loop() -> None:
    """心跳主体。设置**每轮现读** —— 关掉/改周期立刻生效, 不用重启服务。"""
    # 启动时先歇一会儿: 别跟"打开书时的拉取"抢同一批网络
    time.sleep(3)
    while not _TICK_STATE.get("stop"):
        try:
            tk = tick_settings()
            gap = tk["interval_sec"]
            if gap <= 0:
                # 关着也继续转 (5 秒看一次设置), 免得改了要重启才回来
                time.sleep(5)
                continue
            tick_once()
        except Exception as e:  # noqa: BLE001 — 心跳不能死
            _TICK_STATE["error"] = f"{type(e).__name__}: {e}"
            log.warning("定时同步出错 · %s", e)
        # 睡的时候分段查 stop, 关服务时不用等满一个周期
        end = time.time() + max(5.0, float(tick_settings()["interval_sec"] or 0))
        while not _TICK_STATE.get("stop") and time.time() < end:
            time.sleep(min(5.0, max(0.5, end - time.time())))


def pull_book(key: str, *, force: bool = False) -> dict:
    """打开一本书时跟云端对一次进度 (节流 `[sync] pull_gap_sec`)。

    `changed=True` 时前端应该跳到 `anchor`; 本地更靠前时不动 (只报 local-ahead)。
    """
    st = sync_settings()
    if not st["auto"]:
        return {"ok": False, "skipped": "auto-off", "state": sync_state(key)}
    if not st["pull_on_open"] and not force:
        return {"ok": False, "skipped": "pull-off", "state": sync_state(key)}
    row = _link_by_key(key)
    if not row:
        return {"ok": False, "skipped": "no-cloud-link", "state": sync_state(key)}
    now = time.time()
    gap = float(st["pull_gap_sec"] or 120)
    if not force and now - float(_PULL_BOOK_AT.get(key) or 0) < gap:
        return {"ok": True, "skipped": "throttled", "state": sync_state(key)}
    _PULL_BOOK_AT[key] = now
    try:
        # 时长不在打开时拉: 那是"记录"不是"位置", 手动拉取 / 定时任务去做
        res = pull_once(keys=[key], only_forward=True, with_time=False)
    except Exception as e:  # noqa: BLE001 — 拉不到就按本地位置读, 不挡打开
        log.warning("自动拉取失败 · %s · %s", key, e)
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "state": sync_state(key)}
    det = (res.get("detail") or [{}])[0]
    prog = reader_state.get_progress(key) or {}
    changed = bool(res.get("updated"))
    if changed:
        log.info("自动拉取 · %s · 云端 %s · 第 %s 章", row["title"],
                 geometry.format_pct(det.get("pct"), int(det.get("bytes") or 0)),
                 int(det.get("chapter") or 0) + 1)
    return {
        "ok": True,
        "changed": changed,
        "skipped": det.get("skipped"),
        "cloud_pct": det.get("pct"),
        "local_pct": det.get("local"),
        "anchor": {"chapter": int(prog.get("chapter") or 0),
                   "offset": int(prog.get("offset") or 0),
                   "percent": float(prog.get("percent") or 0.0)},
        "state": sync_state(key),
    }


# --------------------------------------------------------------------------
# PULL: 云端 → Recto
# --------------------------------------------------------------------------

def pull_time(days: int = 30, *, force: bool = False) -> dict:
    """云端逐日阅读时长 → 本地 rtime (取 max 融合)。10 分钟节流。"""
    from recto.services import sessions

    if not force and time.time() - float(_TIME_CACHE.get("at") or 0) < _TIME_GAP:
        return {"skipped": "throttled", "days": 0, "merged": 0}
    out = sessions.pull(date.today() - timedelta(days=max(1, int(days))), date.today())
    _TIME_CACHE["at"] = time.time()
    return out


def pull_once(auth: dict | None = None, *, keys: Iterable[str] | None = None,
              dry_run: bool = False, only_forward: bool = True,
              with_time: bool = True, time_days: int = 30) -> dict:
    """把云端进度 (连同阅读时长) 写进 Recto 阅读器。

    only_forward=True: 云端比本地靠前才覆盖 (自动钩子用), 本地回翻不会被拉回。
    with_time=True: 同时拉逐日阅读时长, 与本地位图取较大值融合。
    """
    _setup_log()
    auth = auth or load_zy_auth()
    rows = links()
    if keys is not None:
        want = set(keys)
        rows = [r for r in rows if r["key"] in want]
    state = _cloud_state(auth, rows)
    updated = skipped = 0
    detail: list[dict] = []
    for r in rows:
        ent = state.get(r["id64"] or r["id32"]) or {}
        pct = ent.get("pct")
        if pct is None:
            skipped += 1
            detail.append({"book": r["title"], "skipped": "cloud-empty"})
            continue
        prog = reader_state.get_progress(r["key"]) or {}
        cur = float(prog.get("percent") or 0.0)
        # 云端 readPercent 是 float32, 反解回字节会差不到 1 字节; 给 1e-6
        # (42MB 的书 ≈ 42 字节, 远小于一个段) 的容差, 避免"同一位置反复写"。
        if only_forward and cur and pct <= cur + 1e-6:
            skipped += 1
            detail.append({"book": r["title"], "skipped": "local-ahead",
                           "bytes": reader_state.book_bytes(r["key"]) or int(r.get("bytes") or 0),
                           "cloud": round(pct, 8), "local": round(cur, 8)})
            continue
        chapter, offset, basis = _anchor_for_cloud(r["key"], ent)
        nbytes = reader_state.book_bytes(r["key"]) or int(r.get("bytes") or 0)
        if dry_run:
            detail.append({"book": r["title"], "dry": True, "pct": round(pct, 8),
                           "bytes": nbytes, "chapter": chapter, "offset": offset,
                           "basis": basis})
            continue
        row = reader_state.get_book(r["key"]) or {}
        pct_store = pct
        try:
            from recto.core import bookfile

            doc = bookfile.BookDoc(str(row["path"]))
            try:
                position = doc.position(chapter, offset)
                # 落库的百分比用**本地几何**按 (章, 偏移) 重算, 保证
                # percent 与 (chapter, offset) 永远自洽 —— 否则下次
                # only_forward 会拿两个不同口径的数字互相比较。
                pct_store = float(doc.percent(chapter, offset))
            finally:
                doc.close()
        except Exception:
            position = ""
        reader_state.save_progress(r["key"], chapter, offset, pct_store, position,
                                   complete=1 if (ent.get("finished") or pct >= 0.999) else 0,
                                   allow_rewind=True)
        updated += 1
        sidecar.touch_cloud(r["path"], last_pull_at=_now(), last_pull_pct=round(pct, 8))
        detail.append({"book": r["title"], "pct": round(pct, 8), "bytes": nbytes,
                       "chapter": chapter, "offset": offset, "basis": basis,
                       "updated": True})
        log.info("拉取成功 · %s · 云端 %s · 第 %s 章", r["title"],
                 geometry.format_pct(pct, nbytes), int(chapter) + 1)
    _LINK_CACHE["at"] = 0.0
    out = {"updated": updated, "skipped": skipped, "detail": detail, "at": _utc()}
    if with_time and not dry_run:
        try:
            t = pull_time(time_days)
            out["time"] = {"days": t.get("days"), "merged": t.get("merged", 0),
                           "skipped": t.get("skipped")}
            if t.get("detail"):
                out["time"]["detail"] = t["detail"]
        except Exception as e:  # noqa: BLE001 — 时长拉不动不该毁掉进度拉取
            out["time_error"] = f"{type(e).__name__}: {e}"
            log.warning("阅读时长没拉到 · %s", e)
    return out


def peek_book(key: str, *, force: bool = False) -> dict:
    """打开一本书时**只比不写**: 云端更靠前就交给读者决定 (`need_choice`)。

    和 `pull_book` 的区别只有一个: 云端更靠前时**不动本地库**。
    以前是直接拉进来再跳过去 —— 读者正在等正文, 突然被拽到别处,
    而且他原本的位置已经被覆盖, 想留在本地都留不住。
    """
    st = sync_settings()
    base = {"state": sync_state(key), "settings": st}
    if not st["auto"]:
        return {"ok": False, "skipped": "auto-off", **base}
    if not st["pull_on_open"] and not force:
        return {"ok": False, "skipped": "pull-off", **base}
    row = _link_by_key(key)
    if not row:
        return {"ok": False, "skipped": "no-cloud-link", **base}
    now = time.time()
    gap = float(st["pull_gap_sec"] or 120)
    cached = _PEEK_CACHE.get(key) or {}
    if not force and cached and now - float(cached.get("at") or 0) < gap:
        out = dict(cached.get("res") or {})
        out.update(base)          # 状态/设置每次都取最新
        return out
    _PULL_BOOK_AT[key] = now
    try:
        res = pull_once(keys=[key], dry_run=True, only_forward=True, with_time=False)
    except Exception as e:  # noqa: BLE001 — 比不出来就按本地位置读, 不挡打开
        log.warning("自动比对失败 · %s · %s", key, e)
        return {"ok": False, "error": f"{type(e).__name__}: {e}", **base}
    det = (res.get("detail") or [{}])[0]
    prog = reader_state.get_progress(key) or {}
    nbytes = reader_state.book_bytes(key) or int(det.get("bytes") or 0)
    out = {"ok": True, "local_pct": round(float(prog.get("percent") or 0.0), 8),
           "skipped": det.get("skipped"), "bytes": nbytes}
    if det.get("dry"):
        ch, off = int(det.get("chapter") or 0), int(det.get("offset") or 0)
        out.update({
            "need_choice": True,
            "cloud_pct": det.get("pct"),
            "cloud_chapter": ch,
            "cloud_offset": off,
            "basis": det.get("basis"),
            "anchor": {"chapter": ch, "offset": off,
                       "percent": float(det.get("pct") or 0.0)},
        })
        log.info("云端更靠前 · %s · 云端 %s · 本地 %s · 等读者选择", row["title"],
                 geometry.format_pct(det.get("pct"), nbytes),
                 geometry.format_pct(prog.get("percent"), nbytes))
    elif det.get("skipped") == "local-ahead":
        out["cloud_pct"] = det.get("cloud")
    _PEEK_CACHE[key] = {"at": now, "res": out}
    return out


def apply_cloud(key: str) -> dict:
    """读者选择「跳到云端」→ 这时才真的把云端位置写进本地库。"""
    row = _link_by_key(key)
    if not row:
        return {"ok": False, "skipped": "no-cloud-link", "state": sync_state(key)}
    try:
        res = pull_once(keys=[key], only_forward=False, with_time=False)
    except Exception as e:  # noqa: BLE001
        log.warning("跳到云端失败 · %s · %s", key, e)
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "state": sync_state(key)}
    det = (res.get("detail") or [{}])[0]
    prog = reader_state.get_progress(key) or {}
    spine_ch = int(prog.get("chapter") or 0)
    _PEEK_CACHE.pop(key, None)
    _PUSH_STATE.pop(key, None)
    log.info("按读者选择跳到云端 · %s · 云端 %s", row["title"],
             geometry.format_pct(det.get("pct"), reader_state.book_bytes(key)))
    return {
        "ok": bool(res.get("updated")),
        "cloud_pct": det.get("pct"),
        # chapter 是 spine 下标 (跳转用); catalog 是 App 的目录章号 (显示用),
        # 两者差一两个前置文档 —— 界面上说"第几章"要用 catalog。
        "anchor": {"chapter": spine_ch,
                   "catalog": _catalog_chapter(_geometry_of(row["path"]), spine_ch),
                   "offset": int(prog.get("offset") or 0),
                   "percent": float(prog.get("percent") or 0.0)},
        "state": sync_state(key),
    }


def keep_local(key: str, *, push_now: bool = True) -> dict:
    """读者选择「留在本地」→ 之后允许按本地进度覆盖更靠前的云端记录。

    标记写进边车 (`cloud.local_wins_at`), 重启服务、换进程都还在;
    并且**不是只这一次** —— 后续每次常规推送 (节流 / 离开页面) 都能覆盖,
    覆盖成功一次后自动解除。
    """
    row = _link_by_key(key)
    if not row:
        return {"ok": False, "skipped": "no-cloud-link", "state": sync_state(key)}
    sidecar.touch_cloud(row["path"], local_wins_at=_now())
    _PEEK_CACHE.pop(key, None)
    res: dict = {}
    if push_now:
        try:
            res = push_once(keys=[key], force=True)
        except Exception as e:  # noqa: BLE001 — 网络抖也不撤销读者的选择
            log.warning("按本地覆盖云端失败 · %s · %s", key, e)
            return {"ok": False, "error": f"{type(e).__name__}: {e}",
                    "state": sync_state(key)}
    det = (res.get("detail") or [{}])[0]
    prog = reader_state.get_progress(key) or {}
    pct = float(prog.get("percent") or 0.0)
    log.info("按读者选择覆盖云端 · %s · 本地 %s", row["title"],
             geometry.format_pct(pct, reader_state.book_bytes(key)))
    return {"ok": True, "pushed": res.get("pushed"), "why": det.get("why"),
            "pct": round(pct, 8), "state": sync_state(key)}


# --------------------------------------------------------------------------
# 位置 ↔ 百分比 自洽体检
# --------------------------------------------------------------------------

_POS_TOL = 1e-4      # 位置反解出的比例与 readPercent 的允许偏差 (42MB ≈ 4KB)


def _pos_pct(geo: geometry.BookGeometry | None, pos: str) -> float | None:
    if geo is None or not pos:
        return None
    parsed = geometry.parse_position(pos)
    ch = parsed.get("chapter_id")
    if ch is None:
        return None
    return float(geo.percent(int(ch), int(parsed.get("f3") or 0)))


def audit_alignment(keys: Iterable[str] | None = None) -> dict:
    """逐本核对两端的「位置 ↔ 百分比」是否自洽。

    * 本地: rprogress 的 (chapter, offset) 用几何反算, 应与 percent 一致;
    * 云端: readPosition 的 (chapterId, f3) 反算, 应与 readPercent 一致。

    两端不一致 = 界面显示一个百分比、跳转按另一个位置走
    ("云端进度到了本地不是真正阅读地点" 就是这么来的)。
    """
    _setup_log()
    rows = links()
    if keys is not None:
        want = set(keys)
        rows = [r for r in rows if r["key"] in want]
    auth = load_zy_auth()
    state = _cloud_state(auth, rows)
    out_books: list[dict] = []
    bad = 0
    for r in rows:
        geo = _geometry_of(r["path"])
        item: dict = {"book": r["title"], "key": r["key"], "ok": True}
        prog = reader_state.get_progress(r["key"]) or {}
        if geo is not None and prog:
            ch = int(prog.get("chapter") or 0)
            off = int(prog.get("offset") or 0)
            pct_local = float(prog.get("percent") or 0.0)
            pct_geo = float(geo.percent(max(0, ch - 1), off))
            item["local"] = {"chapter": ch, "offset": off,
                             "percent": round(pct_local, 8),
                             "from_anchor": round(pct_geo, 8),
                             "position": prog.get("position") or "",
                             "src": "spine %d + %d 字节" % (ch, off)}
            if abs(pct_geo - pct_local) > _POS_TOL:
                item["ok"] = False
                item["local_bad"] = "percent 与 (章, 偏移) 不是一个位置"
        ent = state.get(r["id64"] or r["id32"]) or {}
        if geo is not None and ent and ent.get("pct") is not None:
            pct_cloud = float(ent["pct"])
            pos = ent.get("position") or ""
            implied = _pos_pct(geo, pos)
            item["cloud"] = {"percent": round(pct_cloud, 8),
                             "position": pos,
                             "from_position": None if implied is None else round(implied, 8),
                             "src": ent.get("src"), "records": ent.get("records")}
            if implied is None:
                item["cloud"]["note"] = "没有 readPosition, 只能按百分比走"
            elif abs(implied - pct_cloud) > _POS_TOL:
                item["ok"] = False
                item["cloud_bad"] = "readPosition 指向 %.4f%%, readPercent 却是 %.4f%%" % (
                    implied * 100, pct_cloud * 100)
        if not item["ok"]:
            bad += 1
        out_books.append(item)
    return {"books": out_books, "bad": bad, "checked": len(out_books), "at": _utc()}


def repair_positions(keys: Iterable[str] | None = None, *, dry_run: bool = False) -> dict:
    """把云端 readPosition 按它自己的 readPercent 重写成自洽值。

    只动位置字段, 不动进度值 —— 修复"百分比对、跳转错"的记录。
    """
    _setup_log()
    rows = links()
    if keys is not None:
        want = set(keys)
        rows = [r for r in rows if r["key"] in want]
    auth = load_zy_auth()
    state = _cloud_state(auth, rows)
    fixed = skipped = 0
    detail: list[dict] = []
    for r in rows:
        geo = _geometry_of(r["path"])
        ent = state.get(r["id64"] or r["id32"]) or {}
        if geo is None or ent.get("pct") is None:
            skipped += 1
            detail.append({"book": r["title"], "skipped": "no-geometry-or-cloud"})
            continue
        pct = float(ent["pct"])
        implied = _pos_pct(geo, ent.get("position") or "")
        if implied is not None and abs(implied - pct) <= _POS_TOL:
            skipped += 1
            detail.append({"book": r["title"], "skipped": "already-consistent"})
            continue
        ch_id, f3 = geo.solve(pct)
        position = geometry.build_position(int(ch_id), int(f3))
        catalog = _catalog_chapter(geo, int(ch_id) + 1)
        row = {"book": r["title"], "pct": round(pct, 8),
               "old": ent.get("position") or "", "new": position,
               "chapter": catalog}
        if dry_run:
            row["dry"] = True
            detail.append(row)
            continue
        ok = True
        errs = []
        for bid in app_book_keys(r["id64"] or r["id32"]):
            try:
                cloud.upload_progress(
                    auth, book_id=bid, book_name=r["name"],
                    chapter_id=str(catalog),
                    percent=("%.8f" % pct).rstrip("0").rstrip("."),
                    read_complete="1" if ent.get("finished") else "0",
                    total_count=str(_total_count_for(geo, ent.get("total_count"), catalog)),
                    read_position=position, source="recto", device_name="Recto",
                )
            except Exception as e:  # noqa: BLE001
                ok = False
                errs.append(str(e))
        if ok:
            fixed += 1
            sidecar.touch_cloud(r["path"], last_push_at=_now(), last_push_pct=round(pct, 8))
            detail.append(row)
            log.info("位置修正 · %s · %.4f%% · %s", r["title"], pct * 100, position)
        else:
            skipped += 1
            detail.append({"book": r["title"], "error": "; ".join(errs)})
    _LINK_CACHE["at"] = 0.0
    return {"fixed": fixed, "skipped": skipped, "detail": detail, "at": _utc()}


# --------------------------------------------------------------------------
# 状态
# --------------------------------------------------------------------------

def _float_or_none(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if 0.0 <= f <= 1.0 else None


_BOOK_SUFFIXES = (".epub", ".txt", ".mobi", ".azw3", ".pdf")


def _orphan_rows(seen_paths: set[str]) -> list[dict]:
    """边车还在、书却不认了 —— 这类映射必须列出来 (missing)。

    `sidecar.iter_sidecars()` 会跳过"书文件不存在"的边车, 所以书一改名,
    映射就从表里凭空消失, 用户看到的是"少了一本"而不是"这本书找不到文件"。
    """
    from recto.core import paths

    out: list[dict] = []
    for base in paths.PATHS.library_dirs:
        if not base.exists():
            continue
        for p in sorted(base.rglob("*" + sidecar.SIDECAR_SUFFIX)):
            if not p.is_file():
                continue
            c = sidecar._read_raw(p).get("cloud") or {}
            if not (c.get("id64") or c.get("id32")):
                continue
            stem = p.name[: -len(sidecar.SIDECAR_SUFFIX)]
            book = next((p.with_name(stem + s) for s in _BOOK_SUFFIXES
                         if p.with_name(stem + s).exists()), None)
            if book is not None and str(book) in seen_paths:
                continue
            push_pct = _float_or_none(c.get("last_push_pct"))
            pull_pct = _float_or_none(c.get("last_pull_pct"))
            out.append({
                "book": stem, "author": "", "key": "",
                "cloud": str(c.get("name") or ""),
                "id32": str(c.get("id32") or "")[:8],
                "id64": str(c.get("id64") or "")[:8],
                "percent": None, "local_percent": None,
                "cloud_percent": pull_pct if pull_pct is not None else push_pct,
                "cloud_percent_at": c.get("last_pull_at") or c.get("last_push_at"),
                "last_push_pct": push_pct, "last_pull_pct": pull_pct,
                "chapter": 0,
                "last_push_at": c.get("last_push_at"),
                "last_pull_at": c.get("last_pull_at"),
                "total_count": int(c.get("total_count") or 0),
                "bytes": 0,
                "missing": True,
            })
    return out


def status() -> dict:
    rows = links(refresh=True)
    out = []
    for r in rows:
        prog = reader_state.get_progress(r["key"]) or {}
        sc = sidecar.read(r["path"]).get("cloud") or {}
        local_pct = round(float(prog.get("percent") or 0.0), 8)
        push_pct = _float_or_none(sc.get("last_push_pct"))
        pull_pct = _float_or_none(sc.get("last_pull_pct"))
        # 云端进度 = 最近一次与云端打交道时记下的值 (拉取优先, 因为拉的是对方写的)
        cloud_pct = pull_pct if pull_pct is not None else push_pct
        out.append({
            "book": r["title"], "author": r["author"], "key": r["key"],
            "cloud": r["name"], "id32": r["id32"][:8], "id64": r["id64"][:8],
            "percent": local_pct,
            "local_percent": local_pct,
            "cloud_percent": cloud_pct,
            "cloud_percent_at": (sc.get("last_pull_at") if pull_pct is not None
                                 else sc.get("last_push_at")),
            "last_push_pct": push_pct,
            "last_pull_pct": pull_pct,
            "chapter": int(prog.get("chapter") or 0),
            "last_push_at": sc.get("last_push_at"),
            "last_pull_at": sc.get("last_pull_at"),
            "total_count": int(sc.get("total_count") or r.get("total_count") or 0),
            "bytes": int(r.get("bytes") or 0),
            "missing": False,
        })
    # 边车还在、书文件却不认了 (改名/搬走/被删) —— 不能从表里消失,
    # 否则用户看到的就是"映射莫名其妙少了一本"。
    seen = {r["path"] for r in rows}
    try:
        out.extend(_orphan_rows(seen))
    except Exception:  # noqa: BLE001 — 清单补漏失败不该毁掉整张表
        log.warning("状态检查 · 读书库边车失败", exc_info=True)
    try:
        reader_state.scan()
        books = reader_state.list_books()
    except Exception:
        books = []
    return {
        "links": out,
        "link_count": len(out),
        "book_count": len(books),
        "checked_at": _utc(),
    }


def probe(auth: dict | None = None) -> dict:
    """只读探一次云端当前进度, 不写任何进度 (状态表「校准云端」用)。

    返回 {key: {cloud_percent, finished, chapter_id}}, 外加 at。
    """
    _setup_log()
    auth = auth or load_zy_auth()
    rows = links()
    state = _cloud_state(auth, rows)
    books: dict[str, dict] = {}
    for r in rows:
        ent = state.get(r["id64"] or r["id32"]) or {}
        books[r["key"]] = {
            "cloud_percent": ent.get("pct"),
            "finished": bool(ent.get("finished")),
            "chapter_id": ent.get("chapter_id"),
            "total_count": ent.get("total_count"),
        }
    return {"books": books, "at": _now(), "ok": True}



def sync_all(*, auth: dict | None = None) -> dict:
    """先拉后推 (自动钩子用)。"""
    pull = pull_once(auth, only_forward=True)
    push = push_once(auth)
    return {"pull": pull, "push": push}


def daemon(poll_seconds: int = 60) -> None:
    """轮询拉云端进度 (可选; 控制台/自动钩子一般就够用)。"""
    while True:
        try:
            pull_once(only_forward=True)
        except Exception:
            log.exception("daemon pull 失败")
        time.sleep(max(15, poll_seconds))
