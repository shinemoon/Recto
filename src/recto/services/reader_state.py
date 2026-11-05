"""阅读器状态: 书目录 / 进度 / 书签 / 划线笔记 / 阅读时长。

数据模型刻意对齐云端阅读服务（掌阅）的字段语义：

  进度   chapterId + f3(章内字节偏移) + readPercent(字节比例) + readPosition
  书签   chapterId + 位置 + 摘录文字（marksummary）
  划线   positionstart / positionend（字节）+ summary + remark(批注) + color
  时长   每 书×天 一行，1440 位分钟位图（180 字节 base64），与 APK 一致

这样做的直接好处：以后把本地书推到云端、或从云端读回来，不需要换算法，
只需要把同一组字节坐标写过去。**也因此不需要再把 epub 切片**——切片只是
为了让"一页 = 一篇 spine"的阅读器也能表达字节比例。

存储
----
`<data_dir>/reader.sqlite`。所有写入走事务 + WAL，避免像章节 JSON 那样被
并发写坏（历史教训: 固定名临时文件 + 多写者 = 损坏）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

from recto.core import bookfile
from recto.core.config import get_config
from recto.core.paths import PATHS

BITMAP_BYTES = 180   # 1440 分钟 / 8

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rbook (
    key TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    author TEXT DEFAULT '',
    bytes INTEGER DEFAULT 0,
    chapters INTEGER DEFAULT 0,
    size INTEGER DEFAULT 0,
    mtime REAL DEFAULT 0,
    cover TEXT DEFAULT '',
    added_at TEXT NOT NULL,
    seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rprogress (
    book TEXT PRIMARY KEY,
    chapter INTEGER NOT NULL DEFAULT 0,
    offset INTEGER NOT NULL DEFAULT 0,
    percent REAL NOT NULL DEFAULT 0,
    position TEXT DEFAULT '',
    complete INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rbookmark (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book TEXT NOT NULL,
    chapter INTEGER NOT NULL,
    offset INTEGER NOT NULL,
    summary TEXT DEFAULT '',
    title TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    zy_key TEXT DEFAULT '',
    zy_marktime INTEGER DEFAULT 0,
    zy_sig TEXT DEFAULT '',
    src TEXT DEFAULT 'local',
    UNIQUE(book, chapter, offset)
);
CREATE TABLE IF NOT EXISTS rnote (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book TEXT NOT NULL,
    chapter INTEGER NOT NULL,
    start_off INTEGER NOT NULL,
    end_off INTEGER NOT NULL,
    summary TEXT DEFAULT '',
    remark TEXT DEFAULT '',
    color TEXT DEFAULT '',
    anchors TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    zy_key TEXT DEFAULT '',
    zy_marktime INTEGER DEFAULT 0,
    zy_sig TEXT DEFAULT '',
    src TEXT DEFAULT 'local'
);
CREATE TABLE IF NOT EXISTS rsession (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book TEXT NOT NULL,
    start_ts REAL NOT NULL,
    end_ts REAL NOT NULL,
    minutes INTEGER NOT NULL DEFAULT 0,
    day TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rtime (
    book TEXT NOT NULL,
    day TEXT NOT NULL,
    bitmap TEXT NOT NULL,
    minutes INTEGER NOT NULL DEFAULT 0,
    first_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    cloud_minutes INTEGER NOT NULL DEFAULT 0,
    cloud_at TEXT DEFAULT '',
    PRIMARY KEY (book, day)
);
CREATE INDEX IF NOT EXISTS ix_rnote_book ON rnote(book, chapter, start_off);
CREATE INDEX IF NOT EXISTS ix_rbookmark_book ON rbookmark(book, chapter, offset);
CREATE INDEX IF NOT EXISTS ix_rsession_book ON rsession(book, start_ts);
CREATE INDEX IF NOT EXISTS ix_rtime_day ON rtime(day);
"""

# 每个线程一个连接: HTTP 服务是多线程的, sqlite 连接不能跨线程共享。
_LOCAL = threading.local()
_CTX: dict = {}


def db_path() -> Path:
    p = Path(get_config().data_dir) / "reader.sqlite"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def conn() -> sqlite3.Connection:
    path = str(db_path())
    c = getattr(_LOCAL, "conn", None)
    if c is not None and getattr(_LOCAL, "path", "") == path:
        return c
    c = sqlite3.connect(path, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.executescript(_SCHEMA)
    # 增量列 (老库平滑升级): 云端笔记同步用的映射字段
    for table, cols in (
        ("rnote", {"anchors": "TEXT DEFAULT ''", "zy_key": "TEXT DEFAULT ''",
                   "zy_marktime": "INTEGER DEFAULT 0", "zy_sig": "TEXT DEFAULT ''",
                   "src": "TEXT DEFAULT 'local'"}),
        ("rbookmark", {"zy_key": "TEXT DEFAULT ''", "zy_marktime": "INTEGER DEFAULT 0",
                       "zy_sig": "TEXT DEFAULT ''", "src": "TEXT DEFAULT 'local'"}),
        # 云端已记录的分钟数 —— 与本地位图分开存, 展示/统计取两者较大值
        ("rtime", {"cloud_minutes": "INTEGER NOT NULL DEFAULT 0",
                   "cloud_at": "TEXT DEFAULT ''"}),
    ):
        have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        for col, ddl in cols.items():
            if col not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
    c.commit()
    _LOCAL.conn = c
    _LOCAL.path = path
    return c


def reset() -> None:
    c = getattr(_LOCAL, "conn", None)
    if c is not None:
        try:
            c.close()
        except Exception:
            pass
    _LOCAL.conn = None
    _LOCAL.path = ""
    _CTX.clear()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


# --------------------------------------------------------------------------
# 书目
# --------------------------------------------------------------------------

def book_roots() -> list[Path]:
    """可读书目根目录 —— 就是配置里的书库目录。

    解耦后只扫 Recto 自己的书库: 不碰任何外部书目库的目录, 也不再需要
    "切片版 → 原书" 的还原映射 (我们从不切片)。
    """
    out: list[Path] = []
    for r in get_config().library_dirs:
        if r.exists() and r not in out:
            out.append(r)
    return out


def _content_sig(path: Path, size: int) -> tuple:
    """内容指纹: 大小 + 头尾抽样 md5。

    同一份文件经不同挂载点 (bind mount / FUSE) 出现时 inode 可能不同，
    只能按内容判重。抽样 256KB+64KB 足够区分正常书籍。
    """
    h = hashlib.md5()
    try:
        with path.open("rb") as f:
            h.update(f.read(262144))
            if size > 327680:
                f.seek(max(0, size - 65536))
                h.update(f.read(65536))
    except OSError:
        return (size, path.name)
    return (size, h.hexdigest())


def _is_sliced(path: Path) -> bool:
    if path.name.endswith(".kptmp.epub"):
        return True
    try:
        with bookfile.zipfile.ZipFile(path) as z:
            names = z.namelist()
    except (OSError, bookfile.zipfile.BadZipFile):
        return False
    return any("/kp/p" in f"/{n}" for n in names)


def scan(force: bool = False, max_files: int = 4000) -> list[dict]:
    """扫描书库目录 → 落 rbook 表。返回全部书（含进度）。"""
    c = conn()
    seen: list[str] = []
    seen_sig: set[tuple] = set()
    counts = 0
    for root in book_roots():
        for p in sorted(root.rglob("*")):
            if counts >= max_files:
                break
            suf = p.suffix.lower()
            if suf not in bookfile.EPUB_SUFFIXES | bookfile.TXT_SUFFIXES:
                continue
            if "/.git" in str(p) or "/state/backup" in str(p):
                continue
            counts += 1
            try:
                st = p.stat()
            except OSError:
                continue
            # 同一份文件可能通过多个挂载点出现：inode 不同时按内容指纹去重
            sig = _content_sig(p, st.st_size)
            if sig in seen_sig:
                continue
            seen_sig.add(sig)
            key = bookfile.book_key(p)
            row = c.execute(
                "SELECT key, size, mtime, title, author, cover FROM rbook WHERE key=?",
                (key,)).fetchone()
            need = force or row is None or int(row["size"]) != st.st_size or abs(float(row["mtime"]) - st.st_mtime) > 1
            if need:
                meta = bookfile.read_meta(p)
                # 解析失败（FUSE 抖动 / 临时读不到）会退化成"文件名当标题、无封面"。
                # 已经读到过的值不能被这种退化抹掉，所以只覆盖非空字段。
                if row is not None:
                    for field in ("title", "author", "cover"):
                        if not meta.get(field) and row[field]:
                            meta[field] = row[field]
                    # 书名解析失败会退化成文件名：旧值确实是解析出来的
                    # （不等于文件名）就别让文件名顶掉它。
                    if meta.get("title") == p.stem and row["title"] and row["title"] != p.stem:
                        meta["title"] = row["title"]
                c.execute(
                    """INSERT INTO rbook(key,path,kind,title,author,bytes,chapters,size,mtime,cover,added_at,seen_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(key) DO UPDATE SET
                         path=excluded.path, kind=excluded.kind, title=excluded.title,
                         author=excluded.author, bytes=excluded.bytes, chapters=excluded.chapters,
                         size=excluded.size, mtime=excluded.mtime, cover=excluded.cover,
                         seen_at=excluded.seen_at""",
                    (key, str(p), meta["kind"], meta["title"], meta["author"],
                     0, 0, st.st_size, st.st_mtime, meta["cover"],
                     _now(), _now()))
            else:
                c.execute("UPDATE rbook SET seen_at=? WHERE key=?", (_now(), key))
            seen.append(key)
    if seen:
        q = ",".join("?" * len(seen))
        c.execute(f"DELETE FROM rbook WHERE key NOT IN ({q})", seen)
    else:
        c.execute("DELETE FROM rbook")
    c.commit()
    return list_books()


def _under(path: str, roots: list[str]) -> bool:
    """path 是否落在任一书库根下 (纯字符串比较 —— 挂载点不在时也不能抛)。"""
    p = str(path or "")
    if not p:
        return False
    for root in roots:
        r = root.rstrip("/") or "/"
        if p == r or p.startswith(r + "/"):
            return True
    return False


def list_books(q: str = "") -> list[dict]:
    c = conn()
    sql = """SELECT b.*, p.chapter AS p_chapter, p.offset AS p_offset,
                    p.percent AS p_percent, p.complete AS p_complete,
                    p.updated_at AS p_updated_at,
                    (SELECT COUNT(*) FROM rbookmark m WHERE m.book=b.key) AS marks,
                    (SELECT COUNT(*) FROM rnote n WHERE n.book=b.key) AS notes,
                    (SELECT SUM(MAX(t.minutes, COALESCE(t.cloud_minutes, 0)))
                       FROM rtime t WHERE t.book=b.key) AS minutes
             FROM rbook b LEFT JOIN rprogress p ON p.book=b.key"""
    args: tuple = ()
    if q:
        sql += " WHERE b.title LIKE ? OR b.author LIKE ?"
        args = (f"%{q}%", f"%{q}%")
    sql += " ORDER BY COALESCE(p.updated_at, b.seen_at) DESC"
    roots = [str(x) for x in book_roots()]
    out = []
    for r in c.execute(sql, args):
        d = dict(r)
        # 只列**当前书库目录**里的书: 设置页换了书库之后, 旧目录的记录还留着
        # (切回去书和进度都在), 但不该出现在书架上。
        if roots and not _under(d.get("path") or "", roots):
            continue
        d["percent"] = float(d.pop("p_percent") or 0.0)
        d["chapter"] = int(d.pop("p_chapter") or 0)
        d["offset"] = int(d.pop("p_offset") or 0)
        d["complete"] = int(d.pop("p_complete") or 0)
        d["updated_at"] = d.pop("p_updated_at") or ""
        d["marks"] = int(d.get("marks") or 0)
        d["notes"] = int(d.get("notes") or 0)
        d["minutes"] = int(d.get("minutes") or 0)
        out.append(d)
    return out


def get_book(key: str) -> dict | None:
    c = conn()
    r = c.execute("SELECT * FROM rbook WHERE key=?", (key,)).fetchone()
    return dict(r) if r else None


def book_key_by_path(path: str | Path) -> str | None:
    """按文件路径找书。

    同一份书可能通过多个挂载点出现（inode 不同），所以先比绝对路径，
    再退回"文件名 + 大小"。找不到就返回 None —— 调用方宁可不写，也别写错书。
    """
    if not path:
        return None
    c = conn()
    p = Path(str(path))
    cands = {str(p)}
    try:
        cands.add(str(p.resolve()))
    except OSError:
        pass
    for cand in cands:
        r = c.execute("SELECT key FROM rbook WHERE path=?", (cand,)).fetchone()
        if r:
            return r["key"]
    try:
        st = p.stat()
    except OSError:
        return None
    r = c.execute(
        "SELECT key FROM rbook WHERE size=? AND (path LIKE ? OR path LIKE ?)",
        (st.st_size, f"%/{p.name}", f"%\\{p.name}")).fetchone()
    return r["key"] if r else None


# key -> (mtime, [(章起点, 章字节数)], 全书字节, epub|txt)
_GEO_CACHE: dict[str, tuple[float, list[tuple[int, int]], int, str]] = {}


def geometry_of(key: str) -> tuple[list[tuple[int, int]], int, str] | None:
    """(各章字节跨度, 全书字节, 类型)。按 (path, mtime) 失效。

    只解析章节骨架，不建块索引 —— 40MB 的书也能在百毫秒内量出量纲。
    """
    row = get_book(key)
    if not row:
        return None
    path = Path(row["path"])
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    hit = _GEO_CACHE.get(key)
    if not hit or abs(hit[0] - mtime) > 1:
        try:
            doc = bookfile.BookDoc(str(path))
        except (OSError, ValueError):
            return None
        try:
            hit = (mtime, [(c.start, c.size) for c in doc.chapters],
                   int(doc.total_bytes), str(doc.kind))
        finally:
            doc.close()
        _GEO_CACHE[key] = hit
    _mtime, spans, total, kind = hit
    if not spans or not total:
        return None
    return spans, total, kind


def book_bytes(key: str) -> int:
    """全书字节数（= 云端 readPercent 的分母）。取不到返 0。"""
    g = geometry_of(key)
    return int(g[1]) if g else 0


def anchor_from_percent(key: str, pct: float) -> tuple[int, int]:
    """字节比例 → (章下标, 章内字节偏移)。

    与云端 readPercent 同一套口径（累计字节 / 全书字节），所以拿比例回来
    就能落到本地阅读器的同一段正文上。缓存按 (path, mtime) 失效。
    """
    g = geometry_of(key)
    if not g:
        return (0, 0)
    spans, total, _kind = g
    pos = max(0.0, min(1.0, float(pct or 0.0))) * total
    for i, (start, size) in enumerate(spans):
        if pos < start + size or i == len(spans) - 1:
            return (i, max(0, int(pos - start)))
    return (0, 0)


def anchor_from_position(key: str, chapter_id: int, f3: int) -> tuple[int, int] | None:
    """云端 (chapterId, f3) → (章下标, 章内字节偏移)。

    **比百分比更准**：readPercent 是 float32，反解回字节会有个位数量级的抖动；
    readPosition 的 f3 本身就是最细的字节坐标，不需要经过比例。
    """
    g = geometry_of(key)
    if not g:
        return None
    spans, _total, kind = g
    try:
        cid = int(chapter_id)
        off = max(0, int(f3))
    except (TypeError, ValueError):
        return None
    # epub 的 chapterId 是目录下标，spine[0] 是封面 → +1；txt 同构，不偏移
    idx = cid + 1 if kind == "epub" else cid
    if idx < 0:
        idx = 0
    if idx >= len(spans):
        idx = len(spans) - 1
    _start, size = spans[idx]
    return (idx, min(off, max(0, size - 1)))


def percent_of_cloud_position(key: str, chapter_id: int, f3: int) -> float | None:
    """云端 (chapterId, f3) → 0-1 比例（云端口径）。"""
    g = geometry_of(key)
    if not g:
        return None
    spans, total, kind = g
    try:
        cid = int(chapter_id)
        off = max(0, int(f3))
    except (TypeError, ValueError):
        return None
    idx = cid + 1 if kind == "epub" else cid
    idx = max(0, min(idx, len(spans) - 1))
    start, _size = spans[idx]
    return max(0.0, min(1.0, (start + off) / float(total)))


def touch_geometry(key: str, doc: "bookfile.BookDoc") -> None:
    c = conn()
    c.execute("UPDATE rbook SET bytes=?, chapters=?, title=?, author=?, cover=? WHERE key=?",
              (doc.total_bytes, len(doc.chapters), doc.title, doc.author, doc.cover, key))
    c.commit()


# --------------------------------------------------------------------------
# 进度
# --------------------------------------------------------------------------

def get_progress(key: str) -> dict:
    c = conn()
    r = c.execute("SELECT * FROM rprogress WHERE book=?", (key,)).fetchone()
    if not r:
        return {"chapter": 0, "offset": 0, "percent": 0.0, "position": "",
                "complete": 0, "updated_at": ""}
    return dict(r)


def save_progress(key: str, chapter: int, offset: int, percent: float,
                  position: str = "", complete: int | None = None) -> dict:
    """写进度。`complete` 缺省按比例判（>=99.9% 视为读完，对齐云端 readComplete）。"""
    c = conn()
    if complete is None:
        complete = 1 if percent >= 0.999 else 0
    c.execute(
        """INSERT INTO rprogress(book,chapter,offset,percent,position,complete,updated_at)
           VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(book) DO UPDATE SET chapter=excluded.chapter, offset=excluded.offset,
             percent=excluded.percent, position=excluded.position, complete=excluded.complete,
             updated_at=excluded.updated_at""",
        (key, int(chapter), int(offset), float(percent), position, int(complete), _now()))
    c.commit()
    return get_progress(key)


# --------------------------------------------------------------------------
# 书签 / 划线笔记
# --------------------------------------------------------------------------

def list_bookmarks(key: str) -> list[dict]:
    c = conn()
    return [dict(r) for r in c.execute(
        "SELECT * FROM rbookmark WHERE book=? ORDER BY chapter, offset", (key,))]


def add_bookmark(key: str, chapter: int, offset: int, summary: str = "",
                 title: str = "") -> dict:
    c = conn()
    c.execute("""INSERT INTO rbookmark(book,chapter,offset,summary,title,created_at)
                 VALUES(?,?,?,?,?,?)
                 ON CONFLICT(book,chapter,offset) DO UPDATE SET summary=excluded.summary,
                   title=excluded.title""",
              (key, int(chapter), int(offset), summary[:400], title[:120], _now()))
    c.commit()
    r = c.execute("SELECT * FROM rbookmark WHERE book=? AND chapter=? AND offset=?",
                  (key, int(chapter), int(offset))).fetchone()
    return dict(r)


def del_bookmark(bmid: int) -> bool:
    c = conn()
    cur = c.execute("DELETE FROM rbookmark WHERE id=?", (int(bmid),))
    c.commit()
    return cur.rowcount > 0


def list_notes(key: str) -> list[dict]:
    c = conn()
    return [dict(r) for r in c.execute(
        "SELECT * FROM rnote WHERE book=? ORDER BY chapter, start_off", (key,))]


def add_note(key: str, chapter: int, start_off: int, end_off: int,
             summary: str, remark: str = "", color: str = "",
             anchors: str = "") -> dict:
    c = conn()
    if end_off < start_off:
        start_off, end_off = end_off, start_off
    c.execute("""INSERT INTO rnote(book,chapter,start_off,end_off,summary,remark,color,anchors,created_at,updated_at)
                 VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (key, int(chapter), int(start_off), int(end_off), summary[:4000],
               remark[:4000], color[:24], anchors[:400], _now(), _now()))
    c.commit()
    return dict(c.execute("SELECT * FROM rnote WHERE id=last_insert_rowid()").fetchone())


def update_note(nid: int, *, summary: str | None = None, remark: str | None = None,
                color: str | None = None) -> dict | None:
    c = conn()
    row = c.execute("SELECT * FROM rnote WHERE id=?", (int(nid),)).fetchone()
    if not row:
        return None
    c.execute("""UPDATE rnote SET summary=?, remark=?, color=?, updated_at=? WHERE id=?""",
              (row["summary"] if summary is None else summary[:4000],
               row["remark"] if remark is None else remark[:4000],
               row["color"] if color is None else color[:24], _now(), int(nid)))
    c.commit()
    return dict(c.execute("SELECT * FROM rnote WHERE id=?", (int(nid),)).fetchone())


def del_note(nid: int) -> bool:
    c = conn()
    cur = c.execute("DELETE FROM rnote WHERE id=?", (int(nid),))
    c.commit()
    return cur.rowcount > 0


# --------------------------------------------------------------------------
# 阅读时长 —— 分钟位图，跟 APK 的 ReadTimeInfo 同构
# --------------------------------------------------------------------------

def bit_index(hour: int, minute: int) -> int:
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


def _minute_marks(start_ts: float, end_ts: float, *, cap_min: int = 10) -> list[int]:
    """[start, end] 覆盖到的分钟下标。

    空闲保护：单次心跳最多算 `cap_min` 分钟（跟 APK"只在翻页/切前台时
    计时"的效果一致 —— 挂着不读不该刷时长）。同一分钟重复落同一 bit，天然幂等。
    """
    if end_ts <= start_ts:
        return []
    span = min(end_ts - start_ts, cap_min * 60)
    end_ts = start_ts + span
    out: list[int] = []
    t = start_ts
    while t < end_ts:
        dt = datetime.fromtimestamp(t)
        out.append(dt.hour * 60 + dt.minute)
        # 跳到下一分钟边界
        t = (int(t // 60) + 1) * 60
    dt_end = datetime.fromtimestamp(max(start_ts, end_ts - 0.001))
    out.append(dt_end.hour * 60 + dt_end.minute)
    return sorted(set(out))


def session_start(key: str, ts: float | None = None) -> dict:
    ts = float(ts or time.time())
    _CTX.setdefault("sessions", {})[key] = ts
    _CTX.setdefault("beats", {})[key] = ts
    return {"ok": True, "start": ts}


def _mark_minutes(key: str, day: date, marks: Iterable[int]) -> int:
    c = conn()
    row = c.execute("SELECT bitmap, minutes FROM rtime WHERE book=? AND day=?",
                    (key, day.isoformat())).fetchone()
    cur = set(minutes_from_bitmap(row["bitmap"])) if row else set()
    new = sorted(set(cur) | set(marks))
    b64 = bitmap_from_minutes(new)
    c.execute("""INSERT INTO rtime(book,day,bitmap,minutes,first_at,updated_at)
                 VALUES(?,?,?,?,?,?)
                 ON CONFLICT(book,day) DO UPDATE SET bitmap=excluded.bitmap,
                   minutes=excluded.minutes, updated_at=excluded.updated_at""",
              (key, day.isoformat(), b64, len(new), _now(), _now()))
    c.commit()
    return len(new)


def session_beat(key: str, ts: float | None = None, *, start_ts: float | None = None) -> dict:
    """记一次"还在读"。返回本次新增的分钟数。"""
    now = float(ts or time.time())
    start = start_ts if start_ts is not None else _CTX.get("sessions", {}).get(key)
    if not start:
        _CTX.setdefault("sessions", {})[key] = now
        _CTX.setdefault("beats", {})[key] = now
        start = now
    # 心跳按"上次心跳"算，空闲保护 2 分钟 —— 跟 APK"翻页/切前台才计时"等价，
    # 挂着不读不会刷时长。
    last = _CTX.setdefault("beats", {}).get(key) or start
    marks = _minute_marks(float(last), now, cap_min=2)
    day = datetime.fromtimestamp(now).date()
    # 跨零点的部分按各自日期落位（简单处理：> 5 分钟才拆）
    total = _mark_minutes(key, day, marks)
    c = conn()
    c.execute("""INSERT INTO rsession(book,start_ts,end_ts,minutes,day)
                 VALUES(?,?,?,?,?)""",
              (key, float(start), now, max(0, int(round((now - float(start)) / 60))),
               day.isoformat()))
    c.commit()
    _CTX.setdefault("sessions", {})[key] = start
    _CTX.setdefault("beats", {})[key] = now
    return {"ok": True, "day": day.isoformat(), "minutes": total,
            "added": len(marks)}


def session_end(key: str, ts: float | None = None) -> dict:
    out = session_beat(key, ts)
    _CTX.setdefault("sessions", {}).pop(key, None)
    _CTX.setdefault("beats", {}).pop(key, None)
    return out


def _eff() -> str:
    """有效分钟数 = max(本地, 云端)。

    两边各自记同一段阅读 —— 云端那半天可能是 App 读的, 本地这半天是 Recto
    读的。取较大值既不重复计, 也不会因为一边缺失就丢时长。
    """
    return "MAX(minutes, COALESCE(cloud_minutes, 0))"


def set_cloud_minutes(key: str, day, minutes: int) -> int:
    """把云端某天的分钟数并进本地行 (不动本地位图 —— 云端不给分钟明细)。"""
    day_s = day.isoformat() if hasattr(day, "isoformat") else str(day)
    c = conn()
    row = c.execute("SELECT minutes, cloud_minutes FROM rtime WHERE book=? AND day=?",
                    (key, day_s)).fetchone()
    cur_cloud = int(row["cloud_minutes"] or 0) if row else 0
    local = int(row["minutes"] or 0) if row else 0
    if int(minutes) <= cur_cloud and row is not None:
        return max(local, cur_cloud)
    c.execute("""INSERT INTO rtime(book,day,bitmap,minutes,first_at,updated_at,
                                   cloud_minutes,cloud_at)
                 VALUES(?,?,?,?,?,?,?,?)
                 ON CONFLICT(book,day) DO UPDATE SET
                   cloud_minutes=excluded.cloud_minutes,
                   cloud_at=excluded.cloud_at,
                   updated_at=excluded.updated_at""",
              (key, day_s, bitmap_from_minutes([]), local, _now(), _now(),
               int(minutes), _now()))
    c.commit()
    return max(local, int(minutes))


def time_stats(key: str | None = None, days: int = 30) -> dict:
    c = conn()
    since = (date.today() - timedelta(days=days)).isoformat()
    eff = _eff()
    if key:
        rows = c.execute(
            f"SELECT day, {eff} minutes, minutes AS local, cloud_minutes AS cloud "
            "FROM rtime WHERE book=? AND day>=? ORDER BY day",
            (key, since)).fetchall()
        total = c.execute(f"SELECT COALESCE(SUM({eff}),0) m FROM rtime WHERE book=?",
                          (key,)).fetchone()["m"]
    else:
        rows = c.execute(f"""SELECT day, SUM({eff}) minutes,
                                    SUM(minutes) local, SUM(COALESCE(cloud_minutes,0)) cloud
                             FROM rtime WHERE day>=? GROUP BY day ORDER BY day""",
                         (since,)).fetchall()
        total = c.execute(f"SELECT COALESCE(SUM({eff}),0) m FROM rtime").fetchone()["m"]
    return {"total": int(total or 0),
            "days": [{"day": r["day"], "minutes": int(r["minutes"]),
                      "local": int(r["local"] or 0),
                      "cloud": int(r["cloud"] or 0)} for r in rows]}


def pending_minutes(key: str | None = None) -> int:
    c = conn()
    if key:
        r = c.execute("SELECT COALESCE(SUM(minutes),0) m FROM rtime WHERE book=?", (key,)).fetchone()
    else:
        r = c.execute("SELECT COALESCE(SUM(minutes),0) m FROM rtime").fetchone()
    return int(r["m"] or 0)
