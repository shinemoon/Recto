"""阅读时长同步 —— 掌阅 ⇄ Recto 阅读器。

两端都用「每书×每天 1440 位分钟位图」(与掌阅 APK 的 ReadTimeInfo 同构)。

  - 本地 (Recto 阅读器): `reader.sqlite.rtime(book, day, bitmap, minutes)`
  - 云端: `read_report/book?book_id=<md5>_local` 给逐日分钟数 (只有数量,
    没有"哪一分钟"), 写入本文件的 `rtime_cloud` 表; 上传用 reading_time/report。

推送只发本地位图 (服务端按分钟并集, 天然幂等)。

拉取 = 镜像 + 融合:
  * 镜像: `rtime_cloud(book32, day, minutes)` 保存云端原值 (只读视图);
  * 融合: `reader_state.rtime.cloud_minutes` 记同一份数字, 展示与统计取
    `max(本地位图分钟, cloud_minutes)`。两边可能是"同一段阅读" (我们推上去
    又拉回来) 也可能是"不同设备的阅读", 取较大值既不重复计也不会丢时长。
    云端只给"当天读了多少分钟", 没有"哪一分钟", 所以本地位图不被回灌。
"""
from __future__ import annotations

import base64
import gzip
import json
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from recto.core import paths
from recto.core.paths import load_zy_auth, zy_query_params
from recto.services import reader_state
from recto.services import sync as _sync

REPORT_URL = "https://webboot.zhangyue.com/usercenter/user/read_report/statistics"
BOOK_URL = "https://webboot.zhangyue.com/usercenter/user/read_report/book"
UPLOAD_URL = "https://uc.ireader.com/postal/reading_time/report"

RES_READ = "R"          # APK: `res: "R"` = 阅读 (T=TTS); 服务端要实体串, 不是数字
BITMAP_BYTES = 180
KEEP_DAYS = 30


# --------------------------------------------------------------------------
# 位图
# --------------------------------------------------------------------------

def bit_index(hour: int, minute: int, second: int = 0) -> int:
    return hour * 60 + minute - 1


def bitmap_from_minutes(minutes: Iterable[int]) -> str:
    buf = bytearray(BITMAP_BYTES)
    for m in minutes:
        if 0 <= m < 1440:
            buf[m // 8] |= 1 << (m % 8)
    return base64.b64encode(bytes(buf)).decode("ascii")


def minutes_from_bitmap(b64: str) -> list[int]:
    try:
        raw = base64.b64decode(b64 or "")
    except Exception:
        return []
    if len(raw) < BITMAP_BYTES:
        raw = raw + bytes(BITMAP_BYTES - len(raw))
    return [i for i in range(1440) if raw[i // 8] & (1 << (i % 8))]


def bitmap_from_span(start: datetime, end: datetime) -> str:
    minutes: list[int] = []
    cur = start
    while cur < end:
        minutes.append(cur.hour * 60 + cur.minute)
        cur += timedelta(minutes=1)
    return bitmap_from_minutes(sorted(set(minutes)))


def _union(a: str | None, b: str | None) -> tuple[str, int]:
    bits = set(minutes_from_bitmap(a or "")) | set(minutes_from_bitmap(b or ""))
    return bitmap_from_minutes(sorted(bits)), len(bits)


# --------------------------------------------------------------------------
# 掌阅读取
# --------------------------------------------------------------------------

def _get(url: str, extra: dict) -> dict:
    auth = load_zy_auth()
    qs = urllib.parse.urlencode(zy_query_params(auth, extra))
    with urllib.request.urlopen(f"{url}?{qs}", timeout=30) as r:
        raw = r.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8", "replace"))


def book_key(book32: str) -> str:
    """read_report/book 的书 key = 文件内容 MD5(大写) + '_local'。"""
    return f"{(book32 or '').strip().split('_')[0][:32].upper()}_local"


def book_report(key: str) -> dict:
    return _get(BOOK_URL, {"book_id": key}).get("body") or {}


def book_days(key: str) -> dict[str, int]:
    """{'YYYY-MM-DD': minutes} —— 该书逐日阅读分钟。"""
    body = book_report(key)
    out: dict[str, int] = {}
    for month in body.get("daybyday_times") or []:
        mon = month.get("read_month") or ""
        if len(mon) != 7:
            continue
        for d in month.get("detail") or []:
            try:
                day = int(d.get("read_day") or 0)
            except (TypeError, ValueError):
                continue
            if not day:
                continue
            out[f"{mon}-{day:02d}"] = int(d.get("read_minutes") or 0)
    return out


def week_report(start_date: str, end_date: str) -> dict:
    d = _get(REPORT_URL, {"date_type": "week",
                          "start_date": start_date, "end_date": end_date})
    return d.get("body") or {}


def account_days(start: date, end: date) -> dict[str, int]:
    """账号级逐日分钟 (所有设备合计)。"""
    out: dict[str, int] = {}
    cur = start - timedelta(days=start.weekday())
    while cur <= end:
        mon, sun = cur, cur + timedelta(days=6)
        try:
            body = week_report(mon.isoformat(), sun.isoformat())
        except Exception:
            cur += timedelta(days=7)
            continue
        for item in body.get("reading_distribution") or []:
            k, v = item.get("key"), item.get("value")
            if k and isinstance(v, int):
                out[k] = out.get(k, 0) + v
        cur += timedelta(days=7)
    return {k: v for k, v in out.items() if start.isoformat() <= k <= end.isoformat()}


# --------------------------------------------------------------------------
# 云端时长镜像 + 上传队列 (Recto 自己的状态库)
# --------------------------------------------------------------------------

def _state_conn() -> sqlite3.Connection:
    paths.ensure_dirs()
    c = sqlite3.connect(paths.STATE_DB)
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE IF NOT EXISTS rtime_cloud (
            book32 TEXT NOT NULL, day TEXT NOT NULL, minutes INTEGER NOT NULL,
            synced_at TEXT NOT NULL, PRIMARY KEY (book32, day)
        );
        CREATE TABLE IF NOT EXISTS rtime_queue (
            book32 TEXT NOT NULL, day TEXT NOT NULL, bitmap TEXT NOT NULL,
            minutes INTEGER DEFAULT 0, read_percent REAL DEFAULT 0,
            tries INTEGER DEFAULT 0, first_at TEXT NOT NULL, last_try TEXT,
            PRIMARY KEY (book32, day)
        );
        CREATE TABLE IF NOT EXISTS rtime_push (
            book32 TEXT NOT NULL, day TEXT NOT NULL, bitmap TEXT NOT NULL,
            minutes INTEGER DEFAULT 0, pushed_at TEXT NOT NULL,
            PRIMARY KEY (book32, day)
        );
    """)
    c.commit()
    return c


def _pushed_bitmap(book32: str, day: str) -> str:
    """这天这本书**已经推上去过**的分钟位图 (没有 = 空串)。"""
    c = _state_conn()
    try:
        row = c.execute("SELECT bitmap FROM rtime_push WHERE book32=? AND day=?",
                        (book32, day)).fetchone()
        return str(row["bitmap"] or "") if row else ""
    finally:
        c.close()


def _mark_pushed(book32: str, day: str, bitmap: str, minutes: int) -> None:
    c = _state_conn()
    try:
        c.execute("""INSERT INTO rtime_push(book32,day,bitmap,minutes,pushed_at)
                     VALUES(?,?,?,?,?)
                     ON CONFLICT(book32,day) DO UPDATE SET bitmap=excluded.bitmap,
                       minutes=excluded.minutes, pushed_at=excluded.pushed_at""",
                  (book32, day, bitmap, int(minutes), _now()))
        c.commit()
    finally:
        c.close()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _queue_put(book32: str, day: date, bitmap: str, minutes: int, pct: float) -> None:
    c = _state_conn()
    try:
        row = c.execute("SELECT bitmap, minutes FROM rtime_queue WHERE book32=? AND day=?",
                        (book32, day.isoformat())).fetchone()
        if row:
            bitmap, minutes = _union(row["bitmap"], bitmap)
        c.execute("""INSERT INTO rtime_queue(book32,day,bitmap,minutes,read_percent,first_at)
                     VALUES(?,?,?,?,?,?)
                     ON CONFLICT(book32,day) DO UPDATE SET bitmap=excluded.bitmap,
                       minutes=excluded.minutes, read_percent=excluded.read_percent,
                       last_try=excluded.first_at""",
                  (book32, day.isoformat(), bitmap, int(minutes), float(pct), _now()))
        c.commit()
    finally:
        c.close()


def _queue_del(book32: str, day: str) -> None:
    c = _state_conn()
    try:
        c.execute("DELETE FROM rtime_queue WHERE book32=? AND day=?", (book32, day))
        c.commit()
    finally:
        c.close()


def _queue_mark_try(book32: str, day: str) -> None:
    c = _state_conn()
    try:
        c.execute("UPDATE rtime_queue SET tries=tries+1, last_try=? WHERE book32=? AND day=?",
                  (_now(), book32, day))
        c.commit()
    finally:
        c.close()


def queue_info(keys: Iterable[str] | None = None) -> list[dict]:
    c = _state_conn()
    try:
        rows = c.execute("SELECT * FROM rtime_queue ORDER BY day").fetchall()
    finally:
        c.close()
    want = None
    if keys is not None:
        want = {str(k).split("_")[0][:32].upper() for k in keys}
    return [dict(r) for r in rows if not want or r["book32"] in want]


# --------------------------------------------------------------------------
# PULL: 云端 → rtime_cloud (镜像)
# --------------------------------------------------------------------------

def pull(start: date, end: date, *, keys: Iterable[str] | None = None,
         merge: bool = True) -> dict:
    """云端逐日分钟 → rtime_cloud (镜像) + 并进本地 rtime.cloud_minutes。

    merge=True 时, 本地书架上看到的时长 = max(本地位图, 云端分钟)。
    这是"融合", 不是"覆盖": 两边可能各记了半天的阅读, 取较大值既不重复计,
    也不会因为一边缺记录就丢掉时长。
    """
    rows = _sync.links()
    if keys is not None:
        want = set(keys)
        rows = [r for r in rows if r["key"] in want]
    c = _state_conn()
    written = skipped = merged = changed = 0
    detail: list[dict] = []
    try:
        for r in rows:
            book32 = (r["id32"] or r["id64"])[:32].upper()
            if not book32:
                continue
            try:
                days = book_days(book_key(book32))
            except Exception as e:  # noqa: BLE001
                detail.append({"book": r["title"], "error": str(e)})
                continue
            n = n_merged = n_changed = 0
            for day_s, minutes in days.items():
                if not (start.isoformat() <= day_s <= end.isoformat()):
                    continue
                old = c.execute("SELECT minutes FROM rtime_cloud WHERE book32=? AND day=?",
                                (book32, day_s)).fetchone()
                old_min = int(old["minutes"]) if old else None
                c.execute("""INSERT INTO rtime_cloud(book32,day,minutes,synced_at)
                             VALUES(?,?,?,?)
                             ON CONFLICT(book32,day) DO UPDATE SET minutes=excluded.minutes,
                               synced_at=excluded.synced_at""",
                          (book32, day_s, int(minutes), _now()))
                n += 1
                if old_min != int(minutes):
                    n_changed += 1
                if merge:
                    try:
                        reader_state.set_cloud_minutes(r["key"], day_s, int(minutes))
                        n_merged += 1
                    except Exception as e:  # noqa: BLE001 — 融合失败不该毁掉镜像
                        detail.append({"book": r["title"], "merge_error":
                                       f"{type(e).__name__}: {e}"})
            c.commit()
            merged += n_merged
            if n:
                written += n
                changed += n_changed
                detail.append({"book": r["title"], "days": n, "merged": n_merged,
                               "changed": n_changed})
            else:
                skipped += 1
    finally:
        c.close()
    if changed:
        _sync.audit("拉阅读时长 · 更新 %d 天 · %d 本",
                    changed, len([d for d in detail if d.get("changed")]))
    return {"days": written, "merged": merged, "changed": changed,
            "skipped_books": skipped, "detail": detail,
            "account": account_days(start, end)}


def clouds_stats(start: date, end: date) -> dict[str, int]:
    c = _state_conn()
    try:
        rows = c.execute("SELECT day, SUM(minutes) m FROM rtime_cloud WHERE day BETWEEN ? AND ? "
                         "GROUP BY day ORDER BY day",
                         (start.isoformat(), end.isoformat())).fetchall()
    finally:
        c.close()
    return {r["day"]: int(r["m"] or 0) for r in rows}


# --------------------------------------------------------------------------
# PUSH: 本地位图 → 云端
# --------------------------------------------------------------------------

def _post_report(data: dict) -> dict:
    """reading_time/report 单次 POST (APK q0() 形态: gzip body)。"""
    auth = load_zy_auth()
    payload = {"usr": auth["usr"],
               "data": json.dumps(data, ensure_ascii=False),
               "timestamp": str(int(time.time() * 1000)),
               "sign": ""}
    qs = urllib.parse.urlencode(zy_query_params(auth, {}))
    body = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    req = urllib.request.Request(f"{UPLOAD_URL}?{qs}", data=body, method="POST")
    req.add_header("Content-Type", "application/octet-stream")
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8", "replace"))


def _local_day_bitmap(book_key_: str, day: date) -> tuple[str, int]:
    c = reader_state.conn()
    row = c.execute("SELECT bitmap, minutes FROM rtime WHERE book=? AND day=?",
                    (book_key_, day.isoformat())).fetchone()
    if not row:
        return "", 0
    return row["bitmap"], int(row["minutes"] or 0)


def _pct_for_day(book_key_: str, day: date) -> float:
    c = reader_state.conn()
    row = c.execute("SELECT percent FROM rprogress WHERE book=?", (book_key_,)).fetchone()
    return float(row["percent"] or 0.0) if row else 0.0


def enqueue(day: date, *, keys: Iterable[str] | None = None,
            dry_run: bool = False) -> dict:
    rows = _sync.links()
    if keys is not None:
        want = set(keys)
        rows = [r for r in rows if r["key"] in want]
    queued = skipped = unchanged = 0
    detail: list[dict] = []
    for r in rows:
        bitmap, minutes = _local_day_bitmap(r["key"], day)
        if not minutes:
            skipped += 1
            continue
        book32 = (r["id32"] or r["id64"])[:32].upper()
        # 云端已经有这些分钟了 → 别再推一遍 (定时通道每 10 分钟跑一次,
        # 不然同一份"今天读了 N 分钟"会反复写上去, 日志也变成噪声)。
        prev = _pushed_bitmap(book32, day.isoformat())
        if prev and _union(prev, bitmap)[0] == prev:
            unchanged += 1
            continue
        if not dry_run:
            _queue_put(book32, day, bitmap, minutes, _pct_for_day(r["key"], day))
        queued += 1
        detail.append({"book": r["title"], "minutes": minutes})
    return {"day": day.isoformat(), "queued": queued, "skipped": skipped,
            "unchanged": unchanged, "books": detail}


def flush(*, dry_run: bool = False) -> dict:
    """把队列按日分组一次 POST, 成功才删行。"""
    report: dict[str, Any] = {"days": [], "entries": 0, "cleared": 0, "kept": 0, "errors": []}
    c = _state_conn()
    try:
        rows = c.execute("SELECT * FROM rtime_queue ORDER BY day").fetchall()
    finally:
        c.close()
    if not rows:
        return report
    groups: dict[str, list] = {}
    for r in rows:
        try:
            day = date.fromisoformat(r["day"])
        except ValueError:
            _queue_del(r["book32"], r["day"])
            continue
        if day > date.today():
            _queue_del(r["book32"], r["day"])
            continue
        groups.setdefault(day.strftime("%Y%m%d"), []).append(r)
    if not groups:
        return report
    report["days"] = sorted(groups)
    report["entries"] = sum(len(v) for v in groups.values())
    data = {k: [{"bid": 0, "local_bid": book_key(r["book32"]),
                 "read_percent": float(r["read_percent"] or 0.0),
                 "res": RES_READ, "d": r["bitmap"]} for r in v]
            for k, v in groups.items()}
    if dry_run:
        report["payload"] = data
        return report
    try:
        res = _post_report(data)
    except Exception as e:  # noqa: BLE001
        report["errors"].append(f"reading_time/report: {e}")
        for v in groups.values():
            for r in v:
                _queue_mark_try(r["book32"], r["day"])
        report["kept"] = report["entries"]
        return report
    if res.get("code") not in (0, None):
        report["errors"].append(f"reading_time/report: {res}")
        for v in groups.values():
            for r in v:
                _queue_mark_try(r["book32"], r["day"])
        report["kept"] = report["entries"]
        return report
    for v in groups.values():
        for r in v:
            _queue_del(r["book32"], r["day"])
            _mark_pushed(r["book32"], r["day"], str(r["bitmap"] or ""),
                         int(r["minutes"] or 0))
            report["cleared"] += 1
    report["resp"] = res.get("body")
    return report


def push(day: date | None = None, *, dry_run: bool = False,
         keys: Iterable[str] | None = None) -> dict:
    day = day or date.today()
    enq = enqueue(day, keys=keys, dry_run=dry_run)
    out = dict(enq)
    out.update({"written": 0, "errors": []})
    if dry_run:
        out["pending"] = queue_info()
        return out
    res = flush()
    out["flush"] = res
    out["written"] = int(res.get("cleared") or 0)
    out["errors"] = list(res.get("errors") or [])
    if out["errors"]:
        _sync.log.warning("推阅读时长 · 写入 %d 条 · 失败 %d 条",
                          out["written"], len(out["errors"]))
    elif out["written"]:
        _sync.audit("推阅读时长 · 写入 %d 条 · %s", out["written"], day.isoformat())
    return out


def backfill(start: date, end: date, *, dry_run: bool = False,
             keys: Iterable[str] | None = None) -> dict:
    """离线多天补录: 逐日入队 → 一次 flush。"""
    cur = start
    queued = 0
    while cur <= end:
        queued += enqueue(cur, keys=keys, dry_run=dry_run)["queued"]
        cur += timedelta(days=1)
    out = {"start": start.isoformat(), "end": end.isoformat(), "queued": queued,
           "written": 0, "errors": []}
    if dry_run:
        out["pending"] = queue_info()
        return out
    res = flush()
    out["flush"] = res
    out["written"] = int(res.get("cleared") or 0)
    out["errors"] = list(res.get("errors") or [])
    return out


# --------------------------------------------------------------------------
# 统计 (本地 / 云端)
# --------------------------------------------------------------------------

def stats(days: int = 30) -> dict:
    end = date.today()
    start = end - timedelta(days=days)
    local = reader_state.time_stats(days=days)
    cloud = clouds_stats(start, end)
    merged: dict[str, dict] = {}
    for d in local["days"]:
        merged.setdefault(d["day"], {"day": d["day"], "local": 0, "cloud": 0})["local"] = d["minutes"]
    for day_s, minutes in cloud.items():
        merged.setdefault(day_s, {"day": day_s, "local": 0, "cloud": 0})["cloud"] = minutes
    rows = [merged[k] for k in sorted(merged)]
    for r in rows:
        # 同一天两边都有: 取较大者 (我们推上去的就是本地那份, 相加会重复)
        r["minutes"] = max(r["local"], r["cloud"])
    return {"days": rows,
            "local_total": int(local["total"]),
            "cloud_total": sum(cloud.values()),
            "total": sum(r["minutes"] for r in rows)}


# --------------------------------------------------------------------------
# 纸间精确阅读时长 (preciseReadingDurations)
# --------------------------------------------------------------------------
#
# 纸间 /send 的 `preciseReadingDurations` = [{"startTime": 秒, "endTime": 秒}]。
# 它要的是"什么时候在读"的连续区间，不是我们内部的分钟位图 —— 位图只能算出
# 每天读了多少分钟，还原不出跨度。所以这里直接用 rsession 的 start_ts/end_ts。
#
# 三条口径 (与旧 echopaper 的 kv_time.durations 一致):
#   1. 相邻间隔 <= gap(300s) 的会话并成一段 —— 翻页/切后台把一次阅读切得很碎;
#   2. 丢掉 < 20s 的片段 (点一下就走, 不是阅读);
#   3. 单本最多 400 段 (纸间是手机端解析, 太多会卡), 超出取最近的部分。

MERGE_GAP_SEC = 300
MAX_DURATIONS = 400
MIN_SESSION_SEC = 20


def _merge_spans(pairs: Iterable[tuple[float, float]], gap: float = MERGE_GAP_SEC) -> list[tuple[float, float]]:
    """把相邻 (间隔 <= gap) 的区间并起来, 输入顺序无关。"""
    out: list[list[float]] = []
    for a, b in sorted(pairs, key=lambda x: x[0]):
        if b <= a:
            continue
        if out and a - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def local_spans(key: str, day_from: str | None = None, day_to: str | None = None,
                *, gap: float = MERGE_GAP_SEC) -> list[tuple[float, float]]:
    """本地阅读器的会话区间 (已合并)。日窗按 `rsession.day` (本地日期) 过滤。"""
    if not key:
        return []
    c = reader_state.conn()
    sql = "SELECT start_ts, end_ts FROM rsession WHERE book=?"
    args: list[Any] = [key]
    if day_from:
        sql += " AND day>=?"
        args.append(str(day_from)[:10])
    if day_to:
        sql += " AND day<=?"
        args.append(str(day_to)[:10])
    try:
        rows = c.execute(sql + " ORDER BY start_ts", args).fetchall()
    except sqlite3.Error:
        return []
    return _merge_spans([(float(r["start_ts"]), float(r["end_ts"])) for r in rows], gap)


def _day_bounds(day_s: str) -> tuple[float, float]:
    d = datetime.strptime(day_s[:10], "%Y-%m-%d")
    start = d.timestamp()
    return start, start + 86400.0


def _span_seconds_in_day(a: float, b: float, day_s: str) -> float:
    ds, de = _day_bounds(day_s)
    lo, hi = max(a, ds), min(b, de)
    return max(0.0, hi - lo)


def _filler_span(day_s: str, minutes: float) -> tuple[float, float] | None:
    """给"云端有分钟、本地没有"的那天造一段区间 (只用于时长总量对齐)。

    云端只给"当天读了几分钟", 没有时段。放在 19:00 起, 超过 23:59 就往前挪,
    仍然放不下就把剩余部分丢掉 (会被 cloud_filled_minutes 记下来)。
    """
    if minutes <= 0:
        return None
    ds, de = _day_bounds(day_s)
    end = ds + 23 * 3600 + 59 * 60          # 23:59
    start = max(ds, end - minutes * 60)
    if end - start < 60:
        return None
    return (start, end)


def book_time(key: str, day_from: str | None = None, day_to: str | None = None,
              *, gap: float = MERGE_GAP_SEC, limit: int = MAX_DURATIONS,
              min_sec: float = MIN_SESSION_SEC, include_cloud: bool = True) -> dict:
    """单本书的时长明细 —— 纸间 payload 用。

    两条来源, 都落到"什么时候在读"的区间:
      * 本地 `rsession` (真实区间, 精确到秒) —— 阅读器自己读的;
      * 云端逐日分钟 (`rtime.cloud_minutes`) —— 别的设备读的, 只有每天的
        总分钟数, 没有时段, 于是在那天补一段 (19:00→23:59 往前推)。

    补云端是为了"总量对得上" —— Recto 界面显示的时长本来就是
    `max(本地, 云端)`; 只发本地区间会让纸间少掉大半 (人在手机 / 别的设备上读的)。

    返回 {minutes, sessions, days, durations[, cloud_minutes, truncated]}。
    """
    spans = local_spans(key, day_from, day_to, gap=gap)
    spans = [(a, b) for a, b in spans if (b - a) >= min_sec]

    cloud_added = 0.0
    cloud_days = 0
    if include_cloud and key:
        try:
            c = reader_state.conn()
            sql = "SELECT day, minutes, COALESCE(cloud_minutes,0) cl FROM rtime WHERE book=?"
            args: list[Any] = [key]
            if day_from:
                sql += " AND day>=?"
                args.append(str(day_from)[:10])
            if day_to:
                sql += " AND day<=?"
                args.append(str(day_to)[:10])
            rows = c.execute(sql + " ORDER BY day", args).fetchall()
        except sqlite3.Error:
            rows = []
        for r in rows:
            day_s = str(r["day"])[:10]
            target = max(int(r["minutes"] or 0), int(r["cl"] or 0))    # = 界面上的有效分钟
            if target <= 0:
                continue
            covered = sum(_span_seconds_in_day(a, b, day_s) for a, b in spans)
            extra_min = target - covered / 60.0
            if extra_min < 1:
                continue
            filler = _filler_span(day_s, extra_min)
            if not filler:
                continue
            spans.append(filler)
            cloud_added += (filler[1] - filler[0]) / 60.0
            cloud_days += 1
        if cloud_days:
            spans = _merge_spans(spans, gap=gap)

    truncated = False
    if limit and len(spans) > limit:
        spans = spans[-limit:]
        truncated = True
    durations = [{"startTime": int(a), "endTime": int(b)} for a, b in spans if b > a]
    days = sorted({
        datetime.fromtimestamp(a).strftime("%Y-%m-%d") for a, _ in spans
    })
    total_sec = sum(b - a for a, b in spans)
    out = {
        "minutes": round(total_sec / 60.0, 1),
        "sessions": len(spans),
        "days": len(days),
        "durations": durations,
    }
    if include_cloud:
        out["cloudMinutes"] = round(cloud_added, 1)
        out["cloudDays"] = cloud_days
    if truncated:
        out["truncated"] = True
    return out
