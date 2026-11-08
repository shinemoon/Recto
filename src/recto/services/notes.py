"""掌阅笔记 (划线 / 书签 / 批注) ⇄ Recto 阅读器笔记。

坐标只用一个: 原书字节。云端位置是
`<chapterId+1>_0_0$##3-0-<chapterId+1>-<章内字节>-0##$`, 与 Recto 阅读器的
`(chapter, offset)` 一一对应 (chapterId + 1 = spine 下标), 因此不需要任何
页号 / XPath 转换。

身份
----
`uniquecheck = <bookid32>_<positionstart><positionend>` (划线)
`uniquecheck = <bookid32>_<markpostion>`          (书签)

bookid32 = 文件内容 MD5 (大写, 前 32 位)。云端对大小写敏感, 统一大写。

正文
----
`downloadDataV2` 长期只回 status/msg 无 res, 所以正文由**原书字节**切出来
(原文即权威)。批注正文 (remark) 云端拿不到, 本地写的照推。

上传
----
一本书一个 payload (`uploadData?isCheck=1`, gzip), 只推内容变了的条目;
删除走 `delMarkOrNote` (划线 type=2 / 书签 type=1)。
"""
from __future__ import annotations

import gzip
import hashlib
import html
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Iterable

from recto.core import geometry, paths
from recto.core.paths import load_zy_auth, zy_query_params
from recto.services import reader_state
from recto.services import sync as _sync

NOTE_BASE = "https://icloud.zhangyue.com/cloud/storage2/safe"
EMPTY_VER_MD5 = "670b14728ad9902aec32e22fa4f6bd"   # APK hg/e.f70151f
DEFAULT_COLOR = -36352

_POS_RE = re.compile(r"(\d+_0_0\$##\d+-\d+-\d+-\d+-\d+##\$)")
_TAG_RE = re.compile(r"<[^>]+>")
_doc_cache: dict[tuple[str, int], bytes | None] = {}


# --------------------------------------------------------------------------
# 身份 / 位置
# --------------------------------------------------------------------------

def kb32(zy_book_id: str) -> str:
    """云端 book_id → 笔记服务用的 32hex **大写**。"""
    return (zy_book_id or "").strip().split("_")[0][:32].upper()


def hex32(value: str) -> str:
    """任意 book_id → 32hex 大写; 不是 32hex 就返回空串。"""
    s = (value or "").strip().split("_")[0][:32]
    return s.upper() if re.fullmatch(r"[0-9A-Fa-f]{32}", s) else ""


def cloud_ids(row: dict) -> list[str]:
    """一本书在云端可能存在的身份 (32hex 大写, 去重, 优先级从高到低)。

    书名相同的一本书在掌阅云端最多有两种 32hex 身份:

    - `id64` 前 32 位 = **云端那份文件的字节 MD5** (云书架 book_id 的构造), 权威;
    - `id32` = 边车记下来的身份, 文件被替换后会过期。

    并且同一身份在云端**大小写敏感**: App 的笔记身份用 `getFileMD5()` 原样 (小写),
    进度用大写。真实数据里两者可能各存一批条目 (设备上划的落小写, 网页推的落大写),
    所以查询要展开大小写、结果要合并。
    """
    out: list[str] = []
    for v in (hex32(str(row.get("id64") or "")), hex32(str(row.get("id32") or ""))):
        if v and v not in out:
            out.append(v)
    return out


def norm_key(key: str) -> str:
    return (key or "").strip().upper()


def note_key(book32: str, pos_s: str, pos_e: str) -> str:
    return f"{book32}_{pos_s}{pos_e}"


def mark_key(book32: str, pos: str) -> str:
    return f"{book32}_{pos}"


def parse_chapter_offset(pos: str) -> tuple[int, int] | None:
    d = geometry.parse_position(pos)
    if not d or d.get("chapter_id") is None:
        return None
    try:
        return int(d["chapter_id"]), int(d.get("f3") or 0)
    except (TypeError, ValueError):
        return None


def _chapter_raw(epub_path: str, chapter_id: int) -> bytes | None:
    key = (epub_path, chapter_id)
    if key in _doc_cache:
        return _doc_cache[key]
    raw = None
    try:
        idx = geometry.build_doc_index(epub_path, chapter_id + 1)
        if idx is not None:
            raw = idx.raw
    except Exception:
        raw = None
    _doc_cache[key] = raw
    return raw


def _utf8_char_len(raw: bytes, off: int) -> int:
    if raw is None or off < 0 or off >= len(raw):
        return 0
    b = raw[off]
    if b < 0x80:
        n = 1
    elif b < 0xC0:
        return 0
    elif b < 0xE0:
        n = 2
    elif b < 0xF0:
        n = 3
    elif b < 0xF8:
        n = 4
    else:
        return 0
    return n if off + n <= len(raw) else 0


_SENT_END = "。！？…"
_SENT_CLOSERS = "”’」』）】》〉\"'"


def _snap_to_sentence(raw: bytes, hi: int, max_chars: int = 18) -> int:
    """把截断的划线尾补到本句结束。

    实测 (30/30 条云端划线): App 记的 `positionend` 一律停在**最后一个完整
    句子之前**，相差 2-10 个字，且再往后读必是句末标点。位置作为条目身份
    不能改，但**正文**必须按 App 里看到的那段补齐。

    只在同一段文本内前进：遇到标签 / 换行 / 空白就放弃，补的长度不超过
    `max_chars` 个字；找不到句末标点也不动 —— 宁可短，不可跨句。
    """
    if not raw or hi <= 0 or hi >= len(raw):
        return hi
    i, n = hi, 0
    while i < len(raw) and n < max_chars:
        ln = _utf8_char_len(raw, i)
        if not ln:
            return hi
        ch = raw[i:i + ln].decode("utf-8", "replace")
        if ch == "<" or ch.isspace():
            return hi
        i += ln
        n += 1
        if ch in _SENT_END:
            while i < len(raw):
                m = _utf8_char_len(raw, i)
                if not m:
                    break
                c2 = raw[i:i + m].decode("utf-8", "replace")
                if c2 in _SENT_CLOSERS:
                    i += m
                    continue
                break
            return i
    return hi


def text_of_span(epub_path: str, pos_s: str, pos_e: str = "",
                 *, end_inclusive: bool = True) -> str:
    """云端位置区间 → 划线正文 (从原书字节切)。

    App 写的 `positionend` 是「最后一个字的起始字节」(闭区间), 所以默认补一个字;
    我们自己推上去的条目按开区间写, 读回来要 end_inclusive=False。
    """
    a = parse_chapter_offset(pos_s)
    if a is None:
        return ""
    ch, start = a
    raw = _chapter_raw(epub_path, ch)
    if not raw:
        return ""
    b = parse_chapter_offset(pos_e) if pos_e else None
    end = b[1] if (b and b[0] == ch) else start
    lo = max(0, min(start, len(raw)))
    hi = max(lo, min(end, len(raw)))
    if end_inclusive and hi > lo:
        n = _utf8_char_len(raw, hi)
        if n:
            cand = raw[hi:hi + n].decode("utf-8", "ignore")
            if cand and cand != "<" and not cand.isspace():
                hi = min(len(raw), hi + n)
        # App 的末位还常停在句末标点前几个字, 补到本句结束
        hi = _snap_to_sentence(raw, hi)
    if hi <= lo:
        try:
            idx = geometry.build_doc_index(epub_path, ch + 1)
            hit = idx.element_at(lo) if idx else None
        except Exception:
            hit = None
        if not hit:
            return ""
        seg = hit[1]
    else:
        seg = raw[lo:hi].decode("utf-8", "replace")
    txt = html.unescape(_TAG_RE.sub("", seg))
    txt = re.sub(r"<[^>]*$", "", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt[:200]


def parse_pdf_span(uniquecheck: str) -> tuple[int, int, int] | None:
    """PDF/图片型书籍的 uniquecheck → (页号, 起字符, 止字符)。

    这类书没有 `$##` 那套字节坐标, 云端给的是一串数字, 实测三种写法:

        bookid_1-1-76-91.276062-718.933044-1491-1-76-225.626068-500.254028-414
        bookid_1-1-47-101.764679-252.0159911-1-47-162.213623-92.214470
        bookid_176#&#0001000000001.0000000...

    第 3 段一律是**页号**; 两个位置的边界常被粘在一起 (数字紧邻数字),
    所以字符偏移只在恰好 12 段时才取 —— 我们只需要"哪一页", 没有原文可切。
    """
    tail = (uniquecheck or "").split("_", 1)[-1]

    def _int(x: str) -> int:
        try:
            return int(float(x))
        except (TypeError, ValueError):
            return 0

    m = re.search(r"(\d+)#&#", tail)
    if m:
        page = _int(m.group(1))
        return (page, 0, 0) if page > 0 else None

    parts = tail.split("-")
    if len(parts) < 6:
        return None
    page = _int(parts[2])
    if page <= 0:
        return None
    if len(parts) == 12:
        start, end = _int(parts[5]), _int(parts[11])
        return page, start, max(start, end)
    return page, 0, 0


def _split_positions(uniquecheck: str) -> list[str]:
    return _POS_RE.findall(uniquecheck or "")


def _sig(kind: str, key: str, text: str, remark: str, meta: str = "") -> str:
    h = hashlib.sha1()
    for part in (kind, key, text or "", remark or "", meta or ""):
        h.update(part.encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


# --------------------------------------------------------------------------
# 掌阅 HTTP
# --------------------------------------------------------------------------

def _label_at_spine(geo, spine: int) -> str:
    """篇下标 → 展示用章名 (目录名优先, 退回文件名)。

    **push 与 pull 必须用同一份口径**: 这个字符串进了 `_sig` 指纹,
    两边算出来不一样就会"推完又被拉回", 每轮心跳都白推一遍。
    `spine` = 阅读器的篇下标 (= rnote.chapter), 云端 chapterId 比它小 1。
    """
    toc = (geo.toc if geo else {}) or {}
    for idx in (spine, spine - 1):
        if idx in toc:
            return str(toc[idx])
    if geo is not None and 0 <= spine < len(geo.spine):
        return Path(geo.spine[spine]).stem
    return ""


def _call(path: str, body: dict | None = None, extra: dict | None = None,
          timeout: int = 30) -> dict:
    auth = load_zy_auth()
    qs = urllib.parse.urlencode(zy_query_params(auth, extra or {}))
    data = None
    if body is not None:
        data = gzip.compress(json.dumps(body, ensure_ascii=False).encode("utf-8"))
    # path 自带查询串时用 & 续接, 否则会拼出 "...?isCheck=1?usr=..."
    sep = "&" if "?" in path else "?"
    req = urllib.request.Request(f"{NOTE_BASE}{path}{sep}{qs}", data=data,
                                 method="POST" if data else "GET")
    if data:
        req.add_header("Content-Type", "application/octet-stream")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            raise RuntimeError(f"掌阅 {path} HTTP {e.code}: {raw[:200]!r}")
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8", "replace"))


def notebooks() -> list[dict]:
    """getUserNotebooks: 每本有笔记的书 + 计数 + 进度。"""
    d = _call("/getUserNotebooks")
    return d.get("res") or []


def data_ver(book32: str) -> dict:
    """getUserDataVer: 云端三组 {uniquecheck, marktime} 版本表。"""
    d = _call("/getUserDataVer", extra={"bookids": book32})
    want = kb32(book32)
    for row in (d.get("res") or []):
        if kb32(str(row.get("bookid", ""))) == want:
            return row
    return {}


def download(book32: str) -> dict:
    """downloadDataV2 (实测长期无 res, 保留用于未来兼容)。"""
    body = {"usr": load_zy_auth()["usr"], "rgt": load_zy_auth()["zysid"],
            "versionlist": {book32: {
                "booknotesmd5": EMPTY_VER_MD5,
                "bookScaleNotesMd5": EMPTY_VER_MD5,
                "bookmarksmd5": EMPTY_VER_MD5,
                "booknotesVerList": [], "bookScaleNotesVerList": [],
                "bookmarksVerList": []}}}
    d = _call("/downloadDataV2", body)
    return d.get("res") or {}


def upload(book_payload: dict) -> dict:
    auth = load_zy_auth()
    return _call("/uploadData?isCheck=1",
                 {"usr": auth["usr"], "rgt": auth["zysid"], "books": [book_payload]})


def delete_items(book32: str, keys: Iterable[str], type_: int = 2) -> dict:
    """delMarkOrNote。划线 type=2 (实测) / 书签 type=1。"""
    auth = load_zy_auth()
    items = [{"type": type_, "bookid": book32, "uniquecheks": list(keys)}]
    return _call("/delMarkOrNote", {"usr": auth["usr"], "rgt": auth["zysid"],
                                    "delItems": items})


def cloud_versions(book32: str) -> dict[str, Any]:
    """云端版本表 {'note': {key: marktime}, 'mark': {...}, 'del': set()}。"""
    try:
        row = data_ver(book32)
    except Exception:
        return {"note": {}, "mark": {}, "del": set()}

    def _pick(name: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for it in (row.get(name) or []):
            if not isinstance(it, dict):
                continue
            k = it.get("uniquecheck") or it.get("uniquechek")
            if not k:
                continue
            try:
                out[norm_key(str(k))] = int(it.get("marktime") or 0)
            except (TypeError, ValueError):
                out[norm_key(str(k))] = 0
        return out

    return {"note": _pick("booknotesVerList"), "mark": _pick("bookmarksVerList"),
            "del": {norm_key(str(x)) for x in (row.get("delList") or [])}}


def cloud_versions_multi(ids: Iterable[str]) -> dict[str, Any]:
    """多个身份 × 大小写 合并后的版本表; key 一律大写。

    `del` 只留下**任何身份都不存在**的条目。
    原因: 2026-11-01 做过一次"大小写合并" (小写身份 → 大写), 那次删除在云端留了
    墓碑; 若照单执行, 本地刚拉下来的笔记会被自己清空。
    """
    note: dict[str, int] = {}
    mark: dict[str, int] = {}
    dels: set[str] = set()
    used: list[str] = []
    sources: dict[str, dict[str, int]] = {}
    for bid in ids:
        for variant in dict.fromkeys((str(bid).upper(), str(bid).lower())):
            try:
                row = data_ver(variant)
            except Exception:      # noqa: BLE001 — 单个身份读失败不该拖垮整本
                continue
            if not row:
                continue
            used.append(variant)
            got = {"note": len(row.get("booknotesVerList") or []),
                   "mark": len(row.get("bookmarksVerList") or [])}
            if got["note"] or got["mark"]:
                sources[str(bid).upper()] = got
            for name, slot in (("booknotesVerList", note), ("bookmarksVerList", mark)):
                for it in (row.get(name) or []):
                    if not isinstance(it, dict):
                        continue
                    k = norm_key(str(it.get("uniquecheck") or it.get("uniquechek") or ""))
                    if not k:
                        continue
                    try:
                        mt = int(it.get("marktime") or 0)
                    except (TypeError, ValueError):
                        mt = 0
                    if k not in slot or mt > slot[k]:
                        slot[k] = mt
            for x in (row.get("delList") or []):
                dels.add(norm_key(str(x)))
    dels -= set(note) | set(mark)
    return {"note": note, "mark": mark, "del": dels, "ids": used, "sources": sources}


_md5_cache: dict[tuple[str, int, int], str] = {}


def file_md5(path: str) -> str:
    """本地原书的 MD5 (大写)。

    云端每个条目的位置都是**按云端那份文件的字节**算出来的。本地这份若不是同一份
    文件 (重新下载过 / 换过版本), 按位置切出来的正文一定是错的 —— 调用方必须比对。
    """
    try:
        st = os.stat(path)
    except OSError:
        return ""
    key = (str(path), int(st.st_size), int(st.st_mtime))
    hit = _md5_cache.get(key)
    if hit is not None:
        return hit
    h = hashlib.md5()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return ""
    out = h.hexdigest().upper()
    _md5_cache[key] = out
    return out


def cloud_only_books(rows: list[dict] | None = None) -> list[dict]:
    """云端有笔记/书签、但书库里没有这本书 (取不到原文, 划不了线)。

    只看「有内容」的书; 返回 [{book_id, name, notes, marks}]。
    """
    rows = _sync.links() if rows is None else rows
    have = {i for r in rows for i in cloud_ids(r)}
    out: list[dict] = []
    try:
        nbs = notebooks()
    except Exception:           # noqa: BLE001
        return out
    for nb in nbs:
        bid = hex32(str(nb.get("bookid") or nb.get("bookId") or ""))
        if not bid or bid in have:
            continue
        try:
            n = int(nb.get("notenum") or 0)
            m = int(nb.get("marknum") or 0)
        except (TypeError, ValueError):
            continue
        if n + m <= 0:
            continue
        out.append({"book_id": bid, "name": str(nb.get("name") or nb.get("bookname") or ""),
                    "notes": n, "marks": m})
    return out


CLOUD_ONLY_PREFIX = "cloud:"
_UNMAPPED_AT = {"ts": 0.0}


def cloud_book_key(bid: str) -> str:
    """书库里没有这本书时的本地书目键 (带前缀, 与真实文件书分开)。"""
    return f"{CLOUD_ONLY_PREFIX}{hex32(bid)}"


def _ensure_cloud_book(bid: str, name: str) -> str:
    key = cloud_book_key(bid)
    c = reader_state.conn()
    row = c.execute("SELECT key, title FROM rbook WHERE key=?", (key,)).fetchone()
    title = (name or "").strip() or hex32(bid)
    if row is None:
        c.execute(
            """INSERT INTO rbook(key,path,kind,title,author,bytes,chapters,size,
                                 mtime,cover,added_at,seen_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (key, "", "cloud", title, "", 0, 0, 0, 0, "",
             reader_state._now(), reader_state._now()))
        c.commit()
    elif title and row["title"] != title:
        c.execute("UPDATE rbook SET title=? WHERE key=?", (title, key))
        c.commit()
    return key


def _locate_any(uniquecheck: str, *, span: bool) -> tuple[int, int, int, str] | None:
    """两种位置格式 → (章/页序号, 起, 止, 展示名)。

    - epub: `$##` 字节坐标 → 篇下标 = chapterId + 1
    - PDF/图片: 12 段数字 → 页号
    """
    poses = _split_positions(uniquecheck)
    if poses:
        a = parse_chapter_offset(poses[0])
        if not a:
            return None
        b = parse_chapter_offset(poses[1]) if (span and len(poses) > 1) else None
        spine = a[0] + 1
        end = b[1] if b else a[1]
        return spine, a[1], max(a[1], end), f"第 {spine} 篇"
    pdf = parse_pdf_span(uniquecheck)
    if pdf:
        page, start, end = pdf
        return page, start, end, f"第 {page} 页"
    return None


def pull_unmapped(quiet: bool = False, *, force: bool = False,
                  min_gap: float = 600.0) -> dict:
    """云端有笔记、书库里没有的书 → 也落到本地 (只有位置, 没有正文)。

    正文只能从原书字节切, 没有那份文件就切不出来 —— 这些条目照样进摘录,
    只是正文留空 (界面显示「原文不在书库」), 不去云端猜内容。
    """
    import time as _time

    now = _time.time()
    if not force and now - float(_UNMAPPED_AT["ts"]) < float(min_gap):
        return {"books": 0, "notes": 0, "marks": 0, "cached": True}
    _UNMAPPED_AT["ts"] = now

    have = {i for r in _sync.links() for i in cloud_ids(r)}
    try:
        nbs = notebooks()
    except Exception:                              # noqa: BLE001
        return {"books": 0, "notes": 0, "marks": 0, "error": "notebooks"}
    books = notes_n = marks_n = 0
    detail: list[dict] = []
    for nb in nbs:
        bid = hex32(str(nb.get("bookid") or nb.get("bookId") or ""))
        if not bid or bid in have:
            continue
        try:
            want_n = int(nb.get("notenum") or 0)
            want_m = int(nb.get("marknum") or 0)
        except (TypeError, ValueError):
            want_n = want_m = 0
        if want_n + want_m <= 0:
            continue
        try:
            ver = cloud_versions_multi([bid])
        except Exception:                          # noqa: BLE001
            continue
        if not ver["note"] and not ver["mark"]:
            continue
        key = _ensure_cloud_book(bid, str(nb.get("name") or nb.get("bookname") or ""))
        got_n = got_m = 0
        for uc, marktime in ver["note"].items():
            loc = _locate_any(uc, span=True)
            if not loc:
                continue
            chapter, start, end, label = loc
            _upsert_note(key, chapter=chapter, start=start, end=end,
                         summary="", remark="", color="", zy_key=uc,
                         marktime=marktime,
                         zy_sig=_sig("note", uc, "", "", label),
                         src="cloud-only", label=label)
            got_n += 1
        for uc, marktime in ver["mark"].items():
            loc = _locate_any(uc, span=False)
            if not loc:
                continue
            chapter, start, _end, label = loc
            _upsert_bookmark(key, chapter=chapter, offset=start, summary="",
                             title=label, zy_key=uc, marktime=marktime,
                             zy_sig=_sig("bookmark", uc, "", label),
                             src="cloud-only")
            got_m += 1
        if got_n or got_m:
            books += 1
            notes_n += got_n
            marks_n += got_m
            detail.append({"book": nb.get("name") or bid, "notes": got_n,
                           "marks": got_m, "book_id": bid})
    if not quiet and (books or notes_n or marks_n):
        _sync.audit("拉取云端笔记 · 书库没有的书 %d 本 · 划线 %d · 书签 %d "
                    "(只有位置, 原文不在书库)", books, notes_n, marks_n)
    return {"books": books, "notes": notes_n, "marks": marks_n, "detail": detail}


def positions_payload(book32: str, epub_path: str) -> dict:
    """云端版本表 → 条目 (正文从原书切)。"""
    v = data_ver(book32)
    notes, marks = [], []
    for it in (v.get("booknotesVerList") or []):
        uc = str(it.get("uniquecheck") or "")
        poses = _split_positions(uc)
        if len(poses) < 2:
            continue
        notes.append({"uniquecheck": uc,
                      "positionstart": poses[0], "positionend": poses[1],
                      "summary": text_of_span(epub_path, poses[0], poses[1]),
                      "remark": "", "marktime": int(it.get("marktime") or 0)})
    for it in (v.get("bookmarksVerList") or []):
        uc = str(it.get("uniquecheck") or "")
        poses = _split_positions(uc)
        if not poses:
            continue
        marks.append({"uniquecheck": uc, "markpostion": poses[0],
                      "marktime": int(it.get("marktime") or 0)})
    return {"booknotes": notes, "bookmarks": marks,
            "delList": [str(x) for x in (v.get("delList") or [])],
            "from_positions": True}


# --------------------------------------------------------------------------
# 本地映射记忆 (只用于"本地删了 → 云端也删")
# --------------------------------------------------------------------------

def _state_conn() -> sqlite3.Connection:
    paths.ensure_dirs()
    c = sqlite3.connect(paths.STATE_DB)
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE IF NOT EXISTS zy_seen (
            book32 TEXT NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL,
            marktime INTEGER DEFAULT 0, PRIMARY KEY (book32, kind, key)
        );
    """)
    c.commit()
    return c


def _seen_map(book32: str) -> dict[tuple[str, str], int]:
    c = _state_conn()
    try:
        return {(r["kind"], r["key"]): int(r["marktime"] or 0) for r in
                c.execute("SELECT kind, key, marktime FROM zy_seen WHERE book32=?", (book32,))}
    finally:
        c.close()


def _seen_put(book32: str, kind: str, key: str, marktime: int) -> None:
    c = _state_conn()
    try:
        c.execute("INSERT INTO zy_seen(book32,kind,key,marktime) VALUES(?,?,?,?) "
                  "ON CONFLICT(book32,kind,key) DO UPDATE SET marktime=excluded.marktime",
                  (book32, kind, key, int(marktime or 0)))
        c.commit()
    finally:
        c.close()


def _seen_del(book32: str, kind: str, key: str) -> None:
    c = _state_conn()
    try:
        c.execute("DELETE FROM zy_seen WHERE book32=? AND kind=? AND key=?", (book32, kind, key))
        c.commit()
    finally:
        c.close()


# --------------------------------------------------------------------------
# 本地条目 upsert
# --------------------------------------------------------------------------

def _upsert_note(book_key: str, *, chapter: int, start: int, end: int,
                 summary: str, remark: str, color: str, zy_key: str,
                 marktime: int, zy_sig: str = "", src: str = "cloud",
                 label: str = "") -> int:
    """按 zy_key 命中就更新, 否则按位置命中认领, 都不行就插入。返回行 id。"""
    c = reader_state.conn()
    row = c.execute("SELECT * FROM rnote WHERE book=? AND zy_key=?",
                    (book_key, zy_key)).fetchone() if zy_key else None
    if row is None and zy_key:
        row = c.execute("""SELECT * FROM rnote WHERE book=? AND chapter=?
                           AND start_off=? AND end_off=? AND (zy_key IS NULL OR zy_key='')
                           LIMIT 1""", (book_key, chapter, start, end)).fetchone()
    if row is not None:
        new_summary = summary or row["summary"]
        new_remark = row["remark"] or remark      # 本地批注优先 (云端拿不到)
        new_color = color or row["color"]
        c.execute("""UPDATE rnote SET chapter=?, start_off=?, end_off=?, summary=?, remark=?, color=?,
                     zy_key=?, zy_marktime=?, src=?, zy_sig=?, updated_at=?,
                     chapter_label=?
                     WHERE id=?""",
                  (chapter, start, end, new_summary, new_remark, new_color,
                   zy_key or (row["zy_key"] or ""), int(marktime or 0), src,
                   zy_sig or _sig("note", zy_key, new_summary, new_remark),
                   reader_state._now(), label or (row["chapter_label"] or ""),
                   int(row["id"])))
        c.commit()
        return int(row["id"])
    c.execute("""INSERT INTO rnote(book,chapter,start_off,end_off,summary,remark,color,anchors,
                 created_at,updated_at,zy_key,zy_marktime,src,zy_sig,chapter_label)
                 VALUES(?,?,?,?,?,?,?,'',?,?,?,?,?,?,?)""",
              (book_key, chapter, start, end, summary[:4000], remark[:4000],
               color[:24], reader_state._now(), reader_state._now(),
               zy_key, int(marktime or 0), src,
               zy_sig or _sig("note", zy_key, summary, remark), label[:120]))
    c.commit()
    return int(c.execute("SELECT id FROM rnote WHERE book=? AND zy_key=?",
                         (book_key, zy_key)).fetchone()["id"])


def _upsert_bookmark(book_key: str, *, chapter: int, offset: int, summary: str,
                     title: str, zy_key: str, marktime: int,
                     zy_sig: str = "", src: str = "cloud") -> int:
    c = reader_state.conn()
    row = c.execute("SELECT * FROM rbookmark WHERE book=? AND zy_key=?",
                    (book_key, zy_key)).fetchone() if zy_key else None
    if row is None and zy_key:
        row = c.execute("""SELECT * FROM rbookmark WHERE book=? AND chapter=? AND offset=?
                           AND (zy_key IS NULL OR zy_key='') LIMIT 1""",
                        (book_key, chapter, offset)).fetchone()
    if row is not None:
        c.execute("""UPDATE rbookmark SET chapter=?, offset=?, summary=?, title=?, zy_key=?, zy_marktime=?,
                     src=?, zy_sig=? WHERE id=?""",
                  (chapter, offset, summary or row["summary"], title or row["title"],
                   zy_key or (row["zy_key"] or ""), int(marktime or 0), src,
                   zy_sig or _sig("bookmark", zy_key, summary, title), int(row["id"])))
        c.commit()
        return int(row["id"])
    c.execute("""INSERT INTO rbookmark(book,chapter,offset,summary,title,created_at,
                 zy_key,zy_marktime,src,zy_sig) VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (book_key, chapter, offset, summary[:400], title[:120],
               reader_state._now(), zy_key, int(marktime or 0), src,
               zy_sig or _sig("bookmark", zy_key, summary, title)))
    c.commit()
    return int(c.execute("SELECT id FROM rbookmark WHERE book=? AND zy_key=?",
                         (book_key, zy_key)).fetchone()["id"])


# --------------------------------------------------------------------------
# PULL: 云端 → 阅读器
# --------------------------------------------------------------------------

def pull(keys: Iterable[str] | None = None, *, dry_run: bool = False,
         quiet: bool = False) -> dict:
    """云端划线/书签 → 本地。

    `quiet=True` 时「什么都没变」不写日志 —— 定时通道每几分钟跑一次,
    不该每次都留一条"未变 28 条"。
    """
    rows = _sync.links()
    if keys is not None:
        want = set(keys)
        rows = [r for r in rows if r["key"] in want]
    added = updated = deleted = skipped = unchanged = 0
    report: list[dict] = []
    for r in rows:
        ids = cloud_ids(r)
        if not ids:
            continue
        book32 = ids[0]
        ver = cloud_versions_multi(ids)
        if not ver["note"] and not ver["mark"] and not ver["del"]:
            skipped += 1
            report.append({"book": r["title"], "skipped": "cloud-empty"})
            continue
        epub = r["path"]
        local_md5 = file_md5(epub)
        have_src = {str(k).upper() for k in (ver.get("sources") or {})}
        if local_md5 and have_src and local_md5 not in have_src:
            # 云端位置按**它那份文件**的字节算; 本地不是同一份 → 切出来的正文必错
            skipped += 1
            report.append({"book": r["title"], "skipped": "text-mismatch",
                           "local_id": local_md5, "cloud_ids": sorted(have_src)})
            continue
        geo = _sync._geometry_of(epub)

        def _label(spine: int) -> str:
            """spine 下标 → 展示用章名 (目录名优先, 退回文件名)。"""
            return _label_at_spine(geo, spine)

        item = {"book": r["title"], "notes": 0, "marks": 0, "deleted": 0}
        if len(ver["ids"]) > 1:
            item["cloud_keys"] = len(ver["ids"])
        for uc, marktime in ver["note"].items():
            poses = _split_positions(uc)
            if len(poses) < 2:
                continue
            a = parse_chapter_offset(poses[0])
            b = parse_chapter_offset(poses[1])
            if not a or not b:
                continue
            text = text_of_span(epub, poses[0], poses[1])
            if dry_run:
                item["notes"] += 1
                continue
            spine = a[0] + 1          # 云端 chapterId 是"读完的上一篇", 阅读器用篇下标
            row_old = reader_state.conn().execute(
                "SELECT id, zy_sig, zy_marktime, remark, chapter_label "
                "FROM rnote WHERE book=? AND zy_key=?",
                (r["key"], uc)).fetchone()
            # 指纹里的 remark 用**本地已有的批注** — 云端拿不到 remark,
            # 若这里按空串算, 本地批注过的那条会被判成"变了", 每轮心跳都推一遍。
            local_remark = (row_old["remark"] or "") if row_old is not None else ""
            sig = _sig("note", uc, text, local_remark, _label(spine))
            item["notes"] += 1
            if (row_old is not None and (row_old["zy_sig"] or "") == sig
                    and int(row_old["zy_marktime"] or 0) == int(marktime or 0)):
                # 一字不差就别重写库 —— 否则每次拉取都显示"更新 28 条", 读起来像在干活。
                unchanged += 1
                item["unchanged"] = item.get("unchanged", 0) + 1
                if row_old is not None and not (row_old["chapter_label"] or ""):
                    c2 = reader_state.conn()
                    c2.execute("UPDATE rnote SET chapter_label=? WHERE id=?",
                               (_label(spine), int(row_old["id"])))
                    c2.commit()
            else:
                _upsert_note(r["key"], chapter=spine, start=a[1], end=b[1],
                             summary=text, remark="", color="",
                             zy_key=uc, marktime=marktime, zy_sig=sig,
                             label=_label(spine))
                if row_old is not None:
                    updated += 1
                else:
                    added += 1
            _seen_put(book32, "note", uc, marktime)
        for uc, marktime in ver["mark"].items():
            poses = _split_positions(uc)
            if not poses:
                continue
            a = parse_chapter_offset(poses[0])
            if not a:
                continue
            if dry_run:
                item["marks"] += 1
                continue
            spine = a[0] + 1
            _upsert_bookmark(r["key"], chapter=spine, offset=a[1], summary="",
                             title=_label(spine), zy_key=uc, marktime=marktime,
                             zy_sig=_sig("bookmark", uc, "", _label(spine)))
            item["marks"] += 1
            _seen_put(book32, "mark", uc, marktime)
        for uc in ver["del"]:
            if dry_run:
                item["deleted"] += 1
                continue
            c = reader_state.conn()
            gone = c.execute("DELETE FROM rnote WHERE book=? AND zy_key=?",
                             (r["key"], uc)).rowcount or 0
            gone += c.execute("DELETE FROM rbookmark WHERE book=? AND zy_key=?",
                              (r["key"], uc)).rowcount or 0
            c.commit()
            _seen_del(book32, "note", uc)
            _seen_del(book32, "mark", uc)
            # 云端墓碑只算"真的删掉了本地条目", 否则每轮都会报一次"删除 19"
            item["deleted"] += gone
            deleted += gone
        report.append(item)
    # 书库里没有的书: 云端划线也要落到本地 (只有位置), 否则「明明划了却看不到」
    try:
        pulled_unmapped = pull_unmapped(quiet=quiet)
    except Exception:                              # noqa: BLE001
        pulled_unmapped = {}
    unmapped = cloud_only_books(_sync.links())
    if not (quiet and not (added or updated or deleted)):
        _sync.audit("拉取划线 · 新增 %d · 更新 %d · 删除 %d · 未变 %d",
                    added, updated, deleted, unchanged)
    if unmapped and not quiet:
        _sync.audit("云端另有 %d 本有笔记、书库里没有 (拉不下来): %s", len(unmapped),
                    "、".join(u["name"] or u["book_id"] for u in unmapped[:3]))
    return {"added": added, "updated": updated, "deleted": deleted,
            "skipped": skipped, "unchanged": unchanged, "books": report,
            "unmapped": unmapped, "unmapped_pulled": pulled_unmapped}


# --------------------------------------------------------------------------
# PUSH: 阅读器 → 云端
# --------------------------------------------------------------------------

def _idea_fields(epub: str, geo: geometry.BookGeometry, chapter: int, offset: int) -> dict:
    """APK BookHighLight 的 idea 字段 (只影响掌阅端展示)。"""
    out: dict[str, Any] = {}
    try:
        out["chapterId"] = int(chapter)
        out["notesType"] = 2
        out["chaptername"] = str((geo.toc or {}).get(chapter + 1)
                                 or (geo.toc or {}).get(chapter) or "")
        if not out["chaptername"] and chapter + 1 < len(geo.spine):
            out["chaptername"] = Path(geo.spine[chapter + 1]).stem
    except Exception:
        pass
    return out


def push(keys: Iterable[str] | None = None, *, dry_run: bool = False,
         quiet: bool = False) -> dict:
    """本地划线/书签 → 云端。

    `quiet=True` 时「云端已是最新」不写日志 (定时通道用)。
    """
    rows = _sync.links()
    if keys is not None:
        want = set(keys)
        rows = [r for r in rows if r["key"] in want]
    pushed = pushed_marks = deleted = skipped = 0
    errors: list[str] = []
    report: list[dict] = []
    for r in rows:
        ids = cloud_ids(r)
        if not ids:
            continue
        book32 = ids[0]
        ver = cloud_versions_multi(ids)
        epub = r["path"]
        local_md5 = file_md5(epub)
        have_src = {str(k).upper() for k in (ver.get("sources") or {})}
        if local_md5 and have_src and local_md5 not in have_src:
            # 位置是按字节算的: 本地与云端不是同一份文件时, 推上去会让 App 跳到错处
            skipped += 1
            report.append({"book": r["title"], "skipped": "text-mismatch",
                           "local_id": local_md5, "cloud_ids": sorted(have_src)})
            continue
        geo = _sync._geometry_of(epub)
        notes = reader_state.list_notes(r["key"])
        marks = reader_state.list_bookmarks(r["key"])
        out_notes: list[dict] = []
        out_marks: list[dict] = []
        staged: list[tuple[str, str, str, int]] = []
        now_ms = int(time.time() * 1000)
        for n in notes:
            ch_id = max(0, int(n["chapter"]) - 1)
            uc = n.get("zy_key") or note_key(
                book32,
                geometry.build_position(ch_id, int(n["start_off"])),
                geometry.build_position(ch_id, int(n["end_off"])))
            sig = _sig("note", uc, n.get("summary") or "", n.get("remark") or "",
                       _label_at_spine(geo, int(n["chapter"])))
            if n.get("zy_key") and (n.get("zy_sig") or "") == sig:
                skipped += 1
                continue
            ck = ver["note"].get(uc)
            if ck is not None and n.get("zy_key") and int(n.get("zy_marktime") or 0) and ck > int(n["zy_marktime"]):
                skipped += 1          # 云端更新 (App 上改过)
                continue
            pos_s = geometry.build_position(ch_id, int(n["start_off"]))
            pos_e = geometry.build_position(ch_id, int(n["end_off"]))
            marktime = int(n.get("zy_marktime") or 0) or now_ms
            item = {"uniquecheck": norm_key(uc), "style": 0, "color": DEFAULT_COLOR,
                    "summary": (n.get("summary") or "").strip(),
                    "remark": (n.get("remark") or "").strip(),
                    "positionstart": pos_s, "positionend": pos_e,
                    "positionstartl": 0, "positionendl": 0, "marktime": marktime}
            if geo is not None:
                item.update(_idea_fields(epub, geo, ch_id, int(n["start_off"])))
            out_notes.append(item)
            staged.append(("note", uc, sig, marktime, int(n["id"])))
        for m in marks:
            m_ch = max(0, int(m["chapter"]) - 1)
            pos = geometry.build_position(m_ch, int(m["offset"]))
            uc = m.get("zy_key") or mark_key(book32, pos)
            label = _label_at_spine(geo, int(m["chapter"]))
            sig = _sig("bookmark", uc, "", label)
            if m.get("zy_key") and (m.get("zy_sig") or "") == sig:
                skipped += 1
                continue
            ck = ver["mark"].get(uc)
            if ck is not None and m.get("zy_key") and int(m.get("zy_marktime") or 0) and ck > int(m["zy_marktime"]):
                skipped += 1
                continue
            marktime = int(m.get("zy_marktime") or 0) or now_ms
            pct = 0.0
            if geo is not None and geo.total_bytes:
                pct = round((geo.prefix_bytes(m_ch) + int(m["offset"])) / geo.total_bytes, 8)
            out_marks.append({"uniquecheck": norm_key(uc),
                              "marksummary": label or (m.get("summary") or "")[:120],
                              "markpostion": pos, "markpercent": pct, "markstyle": 0,
                              "marktime": marktime})
            staged.append(("mark", uc, sig, marktime, int(m["id"])))

        # 本地删了 → 云端也删 (靠上次同步的 seen 快照做差集)
        seen = _seen_map(book32)
        cur_keys = {("note", norm_key(n.get("zy_key") or "")) for n in notes if n.get("zy_key")}
        cur_keys |= {("mark", norm_key(m.get("zy_key") or "")) for m in marks if m.get("zy_key")}
        del_notes = [k for (kind, k) in seen if kind == "note" and ("note", k) not in cur_keys]
        del_marks = [k for (kind, k) in seen if kind == "mark" and ("mark", k) not in cur_keys]

        if dry_run:
            report.append({"book": r["title"], "dry": True, "notes": len(out_notes),
                           "marks": len(out_marks), "del_notes": len(del_notes),
                           "del_marks": len(del_marks)})
            continue

        item = {"book": r["title"]}
        if out_notes or out_marks:
            idx = _cloud_index_row(book32)
            payload: dict[str, Any] = {"bookbase": {
                "name": idx.get("name") or r["name"],
                "type": int(idx.get("type") or 5),
                "readpostion": idx.get("readpostion") or "",
                "readpercent": idx.get("readpercent") or 0,
                "bookid": book32,
                "updatetime": now_ms,
                "deviceName": "Recto",
                "marknum": int(idx.get("marknum") or 0) + len(out_marks),
                "notenum": int(idx.get("notenum") or 0) + len(out_notes)}}
            if out_marks:
                payload["bookmarks"] = out_marks
            if out_notes:
                payload["booknotes"] = out_notes
            try:
                res = upload(payload)
                if res.get("code") not in (0, None):
                    errors.append(f"upload {book32}: {res}")
                else:
                    for kind, key, sig, mt, row_id in staged:
                        _mark_pushed_row(kind, row_id, key, sig, mt)
                        _seen_put(book32, kind, key, mt)
                    pushed += len(out_notes)
                    pushed_marks += len(out_marks)
                    item.update({"pushed": len(out_notes), "pushed_marks": len(out_marks)})
            except Exception as e:  # noqa: BLE001
                errors.append(f"upload {book32}: {e}")
        for keys_, kind, type_ in ((del_notes, "note", 2), (del_marks, "mark", 1)):
            if not keys_:
                continue
            try:
                delete_items(book32, keys_, type_=type_)
                for k in keys_:
                    _seen_del(book32, kind, k)
                deleted += len(keys_)
                item["deleted"] = item.get("deleted", 0) + len(keys_)
            except Exception as e:  # noqa: BLE001
                errors.append(f"del {book32} {kind}: {e}")
        report.append(item)
    if errors:
        _sync.log.warning("推划线 · 划线 %d · 书签 %d · 删除 %d · 失败 %d",
                          pushed, pushed_marks, deleted, len(errors))
    elif pushed or pushed_marks or deleted:
        _sync.audit("推划线 · 划线 %d · 书签 %d · 删除 %d", pushed, pushed_marks, deleted)
    elif not quiet:
        _sync.audit("推划线 · 云端已是最新（%d 条未变）", skipped)
    return {"pushed": pushed, "pushed_marks": pushed_marks, "deleted": deleted,
            "skipped": skipped, "errors": errors, "books": report}


def _cloud_index_row(book32: str) -> dict:
    try:
        for nb in notebooks():
            if kb32(str(nb.get("bookid") or "")) == book32:
                return {
                    "name": nb.get("bookname") or nb.get("name") or "",
                    "type": nb.get("booktype") or 5,
                    "readpostion": nb.get("readpostion") or "",
                    "readpercent": nb.get("readpercent") or 0,
                    "marknum": nb.get("marknum") or 0,
                    "notenum": nb.get("notenum") or 0,
                }
    except Exception:
        pass
    return {}


def _mark_pushed_row(kind: str, row_id: int, key: str, sig: str, marktime: int) -> None:
    """把刚推上去的条目回写 zy_key / 指纹 (只按行 id 改, 不猜)。"""
    c = reader_state.conn()
    table = "rnote" if kind == "note" else "rbookmark"
    c.execute(f"UPDATE {table} SET zy_key=?, zy_sig=?, zy_marktime=? WHERE id=?",
              (key, sig, int(marktime), int(row_id)))
    c.commit()


def _cloud_only_local() -> list[dict]:
    """已落到本地 (只有位置) 的「书库没有的书」。"""
    try:
        c = reader_state.conn()
        rows = c.execute(
            """SELECT b.key, b.title,
                      (SELECT COUNT(*) FROM rnote n WHERE n.book=b.key) AS notes,
                      (SELECT COUNT(*) FROM rbookmark m WHERE m.book=b.key) AS marks
                 FROM rbook b WHERE b.kind='cloud' ORDER BY b.title""").fetchall()
    except Exception:                              # noqa: BLE001
        return []
    return [{"book": r["title"], "book_id": r["key"],
             "notes": int(r["notes"] or 0), "marks": int(r["marks"] or 0)}
            for r in rows if (r["notes"] or r["marks"])]


def status() -> dict:
    rows = _sync.links()
    out = []
    for r in rows:
        notes = reader_state.list_notes(r["key"])
        marks = reader_state.list_bookmarks(r["key"])
        out.append({"book": r["title"], "notes": len(notes), "bookmarks": len(marks),
                    "synced_notes": sum(1 for n in notes if n.get("zy_key")),
                    "synced_marks": sum(1 for m in marks if m.get("zy_key"))})
    return {"books": out, "count": len(out), "cloud_only": cloud_only_books(rows),
            "cloud_only_local": _cloud_only_local()}
