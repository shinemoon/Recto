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
                  meta_stale: bool = False) -> tuple[bool, str]:
    """要不要把本地进度写到云端。

    云端 (手机) 才是读者眼前那一端: 本地落后时该 pull 拉上来, 而不是
    把云端往下拨。所以默认只前进。`meta_stale` 允许在进度不变时修一次
    元信息 (totalCount / chapterId), 但绝不借机回退。
    """
    if cur_pct is None:
        return True, "no-baseline"
    if cur_done and not new_done:
        return False, "cloud-already-done"
    if new_done and not cur_done:
        return True, "finish"
    if not allow_rewind:
        if new_pct < cur_pct - tol:
            return False, f"regress({new_pct:.4f}<{cur_pct:.4f})"
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
        allow, why = push_decision(cur_pct, bool(cur.get("finished")), pct, done,
                                   allow_rewind=allow_rewind, meta_stale=meta_stale)
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
            sidecar.touch_cloud(r["path"], last_push_at=_now(),
                                last_push_pct=round(pct, 8), total_count=total_count)
            detail.append({"book": r["title"], "pct": round(pct, 8), "why": why,
                           "chapter": catalog_ch, "total": total_count, "ok": True,
                           "bytes": reader_state.book_bytes(key)})
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


def mirror_reader_progress(key: str, pct: float, *, finished: bool = False) -> dict:
    """阅读器存进度时顺手推云端 (节流, 只前进)。

    与 pull 用同一口径 (字节比例), 所以两侧不会互相拉扯。
    """
    cfg = _config_mod.get_config()
    if not bool(cfg.get("sync", "auto", True)):
        return {"ok": False, "skipped": "auto-off"}
    gap = float(cfg.get("sync", "push_debounce_sec", 8) or 8)
    now = time.time()
    stamp = f"push_at_{key}"
    with _PUSH_LOCK:
        last = float(_LINK_CACHE.get(stamp) or 0)  # type: ignore[arg-type]
        if now - last < gap:
            return {"ok": True, "throttled": True}
        _LINK_CACHE[stamp] = now  # type: ignore[index]
    row = _link_by_key(key)
    if not row:
        return {"ok": False, "skipped": "no-cloud-link"}
    try:
        res = push_once(keys=[key])
    except Exception as e:  # noqa: BLE001
        log.warning("推给阅读器失败 · %s", e)
        return {"ok": False, "error": str(e)}
    return {"ok": res["pushed"] > 0, "result": res}


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
                                   complete=1 if (ent.get("finished") or pct >= 0.999) else 0)
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
