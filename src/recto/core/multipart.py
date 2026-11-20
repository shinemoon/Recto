"""multipart/form-data 解析 —— **支持文件**, 且流式落盘。

为什么自己写
------------
``cgi.FieldStorage`` 自 3.11 起废弃, 手工构造时行为不可靠; 而
``email.parser`` 会把整个 body 读进内存 —— 一本 69MB 的 epub 就把
服务端的内存吃掉一半。

这里走另一条路: **先把 body 流式 spool 到临时文件**, 再从那个文件里
**流式地**按 boundary 切分。这样:

* 内存占用 = 一个 chunk (64KB), 不管传 1KB 还是 500MB
* 切分时只在**窗口**里找分隔符 —— 不把整个文件读进内存
* 文件部分按偏移**流式**读出来交给落盘那一层

协议口径
--------
``Content-Type: multipart/form-data; boundary=----X``。每个 part:

    --<boundary>\\r\\n
    Content-Disposition: form-data; name="file"; filename="a.epub"\\r\\n
    Content-Type: application/octet-stream\\r\\n
    \\r\\n
    <字节>\\r\\n

分隔符前有一个 ``\\r\\n``, 结尾是 ``--<boundary>--``。

**只处理 flat 的 form** (字段名 → 值 / 文件名 → 文件) —— 这套 multipart
就是给"传一本书"用的, 不需要嵌套结构。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, BinaryIO, Iterator

#: 读写块大小
CHUNK = 65536

_BOUNDARY_RE = re.compile(r'boundary=(?:"([^"]+)"|([^;]+))', re.IGNORECASE)
_FILENAME_RE = re.compile(r'filename\*?=(?:"([^"]*)"|([^;]+))', re.IGNORECASE)


class MultipartError(ValueError):
    """请求体不是能解析的 multipart —— 消息给人看。"""


def boundary_of(content_type: str) -> bytes:
    """从 Content-Type 头里取出 boundary。"""
    m = _BOUNDARY_RE.search(content_type or "")
    if not m:
        raise MultipartError("请求不是 multipart/form-data (缺 boundary)")
    b = (m.group(1) or m.group(2) or "").strip()
    if not b:
        raise MultipartError("multipart 的 boundary 是空的")
    return b.encode("latin-1", "replace")


def filename_of(head: bytes) -> str:
    """从 part 头里取文件名 (没有就是空)。

    **按 UTF-8 解**: 浏览器发的 ``filename="书.epub"`` 是 UTF-8 字节。
    按 latin-1 解会得到乱码 ("ä¹¦.epub") —— 落盘名就废了。
    latin-1 只作兜底 (真 latin-1 的老客户端)。
    """
    m = _FILENAME_RE.search(head.decode("latin-1", "replace"))
    if not m:
        return ""
    raw = (m.group(1) or m.group(2) or "").strip()
    try:
        return raw.encode("latin-1", "replace").decode("utf-8")
    except UnicodeDecodeError:
        return raw


def name_of(head: bytes) -> str:
    """从 part 头里取字段名。"""
    text = head.decode("latin-1", "replace")
    for h in text.split("\r\n"):
        if h.lower().startswith("content-disposition"):
            m = re.search(r'name="([^"]*)"', h)
            if m:
                return m.group(1)
            m = re.search(r'name=([^;]+)', h)
            if m:
                return m.group(1).strip()
    return ""


def spool(rfile: BinaryIO, length: int, dest: Path, *, max_bytes: int = 0) -> int:
    """把请求体流式写到 ``dest``。返回写入字节数。

    超过 ``max_bytes`` 就停下并抛 :class:`MultipartError` (防超大 body
    把磁盘写满 —— 客户端还没传完就中止)。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with dest.open("wb") as fh:
            left = length
            while left > 0:
                chunk = rfile.read(min(CHUNK, left))
                if not chunk:
                    break
                left -= len(chunk)
                total += len(chunk)
                if max_bytes and total > max_bytes:
                    raise MultipartError(
                        f"文件太大, 上限 {max_bytes // 1024 // 1024}MB")
                fh.write(chunk)
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        try:
            dest.unlink()
        except OSError:
            pass
        raise
    return total


class _Window:
    """在文件里找分隔符的**滑动窗口**。

    做法: 保留尾部 ``len(needle) + 4`` 字节 (分隔符前有 ``\\r\\n``),
    块与块之间可能把分隔符切成两半 —— 所以窗口要留够重叠。

    **自己开一个句柄**: 调用方会在扫描过程中 ``seek`` 到别处读 part 头 ——
    窗口若共用同一个句柄, 它的读位置就跟不上了 (实测会把 part 结束位置
    算到文件尾之后)。
    """

    def __init__(self, path: Path, needle: bytes) -> None:
        self.fh = open(path, "rb")
        self.needle = needle
        self.overlap = len(needle) + 4
        self.buf = b""
        self.base = 0          # buf[0] 在文件里的绝对偏移
        self.eof = False

    def close(self) -> None:
        try:
            self.fh.close()
        except OSError:
            pass

    def _fill(self) -> bool:
        if self.eof:
            return False
        b = self.fh.read(CHUNK)
        if not b:
            self.eof = True
            return False
        self.buf += b
        return True

    def find(self, from_abs: int) -> int:
        """从绝对偏移 ``from_abs`` 起找分隔符, 返回绝对偏移 (找不到 -1)。"""
        while self.base + len(self.buf) < from_abs and self._fill():
            pass
        if self.base + len(self.buf) < from_abs:
            return -1
        skip = from_abs - self.base
        while True:
            i = self.buf.find(self.needle, skip)
            if i != -1:
                return self.base + i
            if self.eof:
                return -1
            keep = self.overlap
            if len(self.buf) > keep:
                drop = len(self.buf) - keep
                self.buf = self.buf[drop:]
                self.base += drop
                # 裁剪后相对起点也跟着挪 —— 否则 skip 指着裁掉的地方, 越界
                skip = max(0, skip - drop)
            if not self._fill():
                i = self.buf.find(self.needle, 0)
                return self.base + i if i != -1 else -1


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def part_spans(spooled: Path, boundary: bytes) -> list[dict[str, Any]]:
    """切分并给出每个 part 的 ``{name, filename, head, start, end}``。

    **偏移是 spooled 文件里的字节偏移** —— 调用方据此流式读出文件内容。
    """
    delim = b"--" + boundary
    spans: list[dict[str, Any]] = []
    win = _Window(spooled, delim)
    try:
        pos = 0
        while True:
            at = win.find(pos)
            if at == -1:
                break
            after = at + len(delim)
            with spooled.open("rb") as fh:
                fh.seek(after)
                tail = fh.read(2)
                if tail == b"--":
                    break
                if tail == b"\r\n":
                    body_start = after + 2
                else:
                    nxt = win.find(after)
                    if nxt == -1:
                        break
                    body_start = nxt
                fh.seek(body_start)
                head = fh.read(8192)
            cut = head.find(b"\r\n\r\n")
            if cut == -1:
                break
            head = head[:cut]
            content_start = body_start + cut + 4
            nxt = win.find(content_start)
            content_end = (nxt - 2) if nxt != -1 else _size(spooled)
            if content_end < content_start:
                content_end = content_start
            spans.append({
                "name": name_of(head),
                "filename": filename_of(head),
                "head": head,
                "start": content_start,
                "end": content_end,
            })
            pos = content_end
    finally:
        win.close()
    return spans


def read_span(spooled: Path, span: dict[str, Any], *, chunk: int = CHUNK) -> Iterator[bytes]:
    """按偏移把一个 part 的内容**流式**读出来。"""
    with spooled.open("rb") as fh:
        fh.seek(int(span["start"]))
        left = int(span["end"]) - int(span["start"])
        while left > 0:
            b = fh.read(min(chunk, left))
            if not b:
                break
            left -= len(b)
            yield b


def text_fields(spooled: Path, boundary: bytes) -> dict[str, str]:
    """只要**文字字段** (没有 filename 的那些)。"""
    out: dict[str, str] = {}
    for sp in part_spans(spooled, boundary):
        if sp["filename"]:
            continue
        with spooled.open("rb") as fh:
            fh.seek(int(sp["start"]))
            raw = fh.read(int(sp["end"]) - int(sp["start"]))
        out[sp["name"]] = raw.decode("utf-8", "replace")
    return out
