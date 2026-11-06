"""EPUB 解析: OPF / spine / TOC / 正文文本抽取.

只用标准库 (zipfile + xml.etree), 不引 lxml.
输出统一的 chapter 列表, 每章含 title + 段落文本.
"""
from __future__ import annotations

import posixpath
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

# XHTML 命名空间
_NS = {
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "ncx": "http://www.daisy.org/z3986/2005/ncx/",
    "container": "urn:oasis:names:tc:opendocument:xmlns:container",
    "xhtml": "http://www.w3.org/1999/xhtml",
}

_WS_RE = re.compile(r"[ \t\r\f\v]+")
_NL_RE = re.compile(r"\n{3,}")


class EpubError(RuntimeError):
    """EPUB 解析失败."""


class _TextCollector(HTMLParser):
    """把 HTML 正文抽成纯文本段落列表.

    关键点: void 元素 (meta/link/img/br/hr) 没有闭合标签, 不能进 skip 计数.
    """

    # 块级标签 — 结束/开始时切段落
    BLOCK_TAGS = {
        "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
        "blockquote", "pre", "section", "article", "header", "footer",
        "figcaption", "td", "th", "dd", "dt", "address", "aside", "nav",
        "figure", "main", "table", "ul", "ol", "dl", "body",
    }
    # 整段丢弃的标签 (都有正常闭合标签)
    SKIP_TAGS = {"script", "style", "head", "title", "svg", "noscript"}
    # void 元素 — 无闭合标签, 不进 skip 计数
    VOID_TAGS = {
        "meta", "link", "img", "br", "hr", "input", "col", "area",
        "base", "wbr", "embed", "source", "track", "param",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t in self.VOID_TAGS:
            if not self._skip_depth and t == "br":
                self.parts.append("\n")
            return
        if t in self.SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if t in self.BLOCK_TAGS:
            self._flush()

    def handle_startendtag(self, tag, attrs):
        # 自闭合形式 (<br/> <meta/> <link/>): 等价 void, 不动 skip 计数
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in self.VOID_TAGS:
            return
        if t in self.SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if t in self.BLOCK_TAGS:
            self._flush()

    def handle_data(self, data):
        if self._skip_depth:
            return
        self._buf.append(data)

    def _flush(self):
        if not self._buf:
            return
        text = "".join(self._buf)
        self._buf = []
        text = _WS_RE.sub(" ", text).strip()
        if text:
            self.parts.append(text)

    def close(self):  # noqa: D102
        super().close()
        self._flush()

    def result(self) -> str:
        out = "\n".join(self.parts)
        return _NL_RE.sub("\n\n", out).strip()


def html_to_text(raw: str) -> str:
    """HTML → 纯文本."""
    p = _TextCollector()
    try:
        p.feed(raw)
        p.close()
    except Exception:
        return ""
    return p.result()


def _local(tag: str) -> str:
    """去命名空间."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _find_rootfile(zf: zipfile.ZipFile) -> str:
    """从 META-INF/container.xml 找 OPF 路径."""
    try:
        data = zf.read("META-INF/container.xml")
    except KeyError as e:
        raise EpubError("不是标准 EPUB: 缺 META-INF/container.xml") from e
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise EpubError(f"container.xml 解析失败: {e}") from e
    for rf in root.iter():
        if _local(rf.tag) == "rootfile":
            full = rf.get("full-path")
            if full:
                return full
    raise EpubError("container.xml 里没有 rootfile")


def _parse_opf(zf: zipfile.ZipFile, opf_path: str) -> dict[str, Any]:
    """解析 OPF: manifest / spine / metadata."""
    try:
        raw = zf.read(opf_path)
    except KeyError as e:
        raise EpubError(f"OPF 不存在: {opf_path}") from e
    root = ET.fromstring(raw)
    opf_dir = posixpath.dirname(opf_path)

    manifest: dict[str, dict[str, Any]] = {}
    for item in root.iter():
        if _local(item.tag) != "item":
            continue
        iid = item.get("id")
        href = item.get("href")
        if not iid or not href:
            continue
        manifest[iid] = {
            "href": href,
            "media_type": item.get("media-type") or "",
            "properties": item.get("properties") or "",
            "full_path": posixpath.normpath(posixpath.join(opf_dir, href)) if opf_dir else href,
        }

    spine: list[str] = []  # 顺序的 full_path
    spine_ids: list[str] = []
    for item in root.iter():
        if _local(item.tag) != "itemref":
            continue
        idref = item.get("idref")
        if not idref:
            continue
        spine_ids.append(idref)
        m = manifest.get(idref)
        if m:
            spine.append(m["full_path"])

    meta: dict[str, str] = {}
    for el in root.iter():
        lt = _local(el.tag)
        if lt in ("title", "creator", "language", "publisher", "identifier", "description", "date"):
            if el.text and lt not in meta:
                meta[lt] = el.text.strip()

    toc_path = ""
    for el in root.iter():
        if _local(el.tag) == "spine":
            toc_path = el.get("toc") or ""
    if toc_path and toc_path in manifest:
        ncx = manifest[toc_path]["full_path"]
    else:
        ncx = ""
        for iid, m in manifest.items():
            if m["media_type"] == "application/x-dtbncx+xml":
                ncx = m["full_path"]
                break

    return {
        "opf_path": opf_path,
        "opf_dir": opf_dir,
        "manifest": manifest,
        "spine": spine,
        "spine_ids": spine_ids,
        "metadata": meta,
        "ncx_path": ncx,
    }


def _parse_ncx(zf: zipfile.ZipFile, ncx_path: str) -> dict[str, str]:
    """解析 NCX → {spine_full_path: title}."""
    out: dict[str, str] = {}
    if not ncx_path:
        return out
    try:
        raw = zf.read(ncx_path)
        root = ET.fromstring(raw)
    except (KeyError, ET.ParseError):
        return out
    base = posixpath.dirname(ncx_path)
    for nav in root.iter():
        if _local(nav.tag) != "navPoint":
            continue
        src = ""
        label = ""
        for child in nav:
            lt = _local(child.tag)
            if lt == "navLabel":
                for sub in child.iter():
                    if _local(sub.tag) == "text" and sub.text:
                        label = sub.text.strip()
                        break
            elif lt == "content":
                src = child.get("src") or ""
        if not src:
            continue
        # src 相对 ncx 目录
        full = posixpath.normpath(posixpath.join(base, src.split("#")[0]))
        if label:
            out.setdefault(full, label)
    return out


def _parse_nav_doc(zf: zipfile.ZipFile, opf: dict) -> dict[str, str]:
    """解析 EPUB3 nav.xhtml → {full_path: title}."""
    out: dict[str, str] = {}
    opf_dir = opf["opf_dir"]
    for m in opf["manifest"].values():
        if "nav" not in (m.get("properties") or "").split():
            continue
        try:
            raw = zf.read(m["full_path"]).decode("utf-8", errors="replace")
        except KeyError:
            continue
        base = posixpath.dirname(m["full_path"])
        # 简单解析 <a href="...">text</a>
        for m2 in re.finditer(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", raw, re.S | re.I):
            href = m2.group(1).split("#")[0]
            if not href:
                continue
            title = re.sub(r"<[^>]+>", "", m2.group(2))
            title = _WS_RE.sub(" ", title).strip()
            full = posixpath.normpath(posixpath.join(base, href))
            if title:
                out.setdefault(full, title)
        break
    return out


def _read(zf: zipfile.ZipFile, path: str) -> str:
    try:
        return zf.read(path).decode("utf-8", errors="replace")
    except KeyError:
        return ""


def parse_epub(path: str | Path) -> dict[str, Any]:
    """解析 EPUB.

    Returns:
        {
          "path": str, "size": int, "title": str, "author": str,
          "toc": {full_path: title},
          "chapters": [
             {"index": 0, "path": "OEBPS/x.html", "title": "Chapter 1",
              "text": "...", "chars": 1234}
          ],
          "total_chars": int,
          "warnings": [str]
        }
    """
    path = Path(path)
    if not path.exists():
        raise EpubError(f"文件不存在: {path}")
    warnings: list[str] = []
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise EpubError(f"不是合法 zip/epub: {e}") from e

    with zf:
        opf_path = _find_rootfile(zf)
        opf = _parse_opf(zf, opf_path)
        toc = _parse_ncx(zf, opf.get("ncx_path") or "")
        if not toc:
            nav_toc = _parse_nav_doc(zf, opf)
            if nav_toc:
                toc = nav_toc
                warnings.append("无 NCX, 已用 EPUB3 nav 目录")

        meta = opf["metadata"]
        chapters: list[dict[str, Any]] = []
        for i, sp in enumerate(opf["spine"]):
            raw = _read(zf, sp)
            text = html_to_text(raw)
            if not text.strip():
                continue
            title = toc.get(sp, "")
            if not title:
                # 从文件里找第一个 h1/h2
                m = re.search(r"<h[12][^>]*>(.*?)</h[12]>", raw, re.S | re.I)
                if m:
                    title = _WS_RE.sub(" ", re.sub(r"<[^>]+>", "", m.group(1))).strip()
            chapters.append({
                "index": i,
                "path": sp,
                "title": title or f"[{i + 1}] {posixpath.basename(sp)}",
                "text": text,
                "chars": len(text),
            })

    if not chapters:
        raise EpubError("没解析到任何正文 (spine 为空或全是图片)")

    return {
        "path": str(path),
        "size": path.stat().st_size,
        "title": meta.get("title", "") or path.stem,
        "author": meta.get("creator", ""),
        "language": meta.get("language", ""),
        "toc": toc,
        "chapters": chapters,
        "total_chars": sum(c["chars"] for c in chapters),
        "warnings": warnings,
    }


def _split_long(text: str, max_chars: int) -> list[str]:
    """把超长段落再切细.

    优先级: 换行 → 句末标点 → 硬切.
    """
    if len(text) <= max_chars:
        return [text]
    # 1) 按行切
    lines = text.split("\n")
    out: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for ln in lines:
        if cur_len + len(ln) + 1 > max_chars and cur:
            out.append("\n".join(cur))
            cur = []
            cur_len = 0
        cur.append(ln)
        cur_len += len(ln) + 1
    if cur:
        out.append("\n".join(cur))

    # 2) 还是太长 → 按句末标点切
    final: list[str] = []
    for seg in out:
        if len(seg) <= max_chars:
            final.append(seg)
            continue
        sents = re.split(r"(?<=[.!?。！？…])\s*", seg)
        cur2: list[str] = []
        cur2_len = 0
        for s in sents:
            if not s:
                continue
            if cur2_len + len(s) + 1 > max_chars and cur2:
                final.append(" ".join(cur2))
                cur2 = []
                cur2_len = 0
            cur2.append(s)
            cur2_len += len(s) + 1
        if cur2:
            final.append(" ".join(cur2))

    # 3) 仍超长 → 硬切
    hard: list[str] = []
    for seg in final:
        while len(seg) > max_chars:
            hard.append(seg[:max_chars])
            seg = seg[max_chars:]
        if seg:
            hard.append(seg)
    return hard or [text[:max_chars]]


def chunk_chapters(
    chapters: list[dict[str, Any]],
    max_chars: int = 6000,
) -> list[dict[str, Any]]:
    """把章节切成 LLM 友好的块.

    块内保留章节标题, 便于 LLM 归类. 一章可切多块.
    段落不跨块 (先按空行分段, 超长的段落再按行/句子细分).
    """
    max_chars = max(1500, int(max_chars))
    blocks: list[dict[str, Any]] = []

    def emit(ch: dict, text: str, part: int) -> None:
        text = text.strip()
        if not text:
            return
        blocks.append({
            "block_index": len(blocks),
            "chapter_index": ch["index"],
            "chapter_title": ch["title"],
            "text": text,
            "chars": len(text),
            "part": part,
        })

    for ch in chapters:
        text = ch["text"]
        if len(text) <= max_chars:
            emit(ch, text, 1)
            continue
        # 段落优先切 (空行分段)
        paras = [p for p in re.split(r"\n{2,}", text) if p.strip()]
        cur: list[str] = []
        cur_len = 0
        part = 1
        for p in paras:
            if len(p) > max_chars:
                # 超长段落: 先把已有的存起来, 再切这个
                if cur:
                    emit(ch, "\n\n".join(cur), part)
                    part += 1
                    cur = []
                    cur_len = 0
                for piece in _split_long(p, max_chars):
                    emit(ch, piece, part)
                    part += 1
                continue
            if cur_len + len(p) + 2 > max_chars and cur:
                emit(ch, "\n\n".join(cur), part)
                part += 1
                cur = []
                cur_len = 0
            cur.append(p)
            cur_len += len(p) + 2
        if cur:
            emit(ch, "\n\n".join(cur), part)
    return blocks
