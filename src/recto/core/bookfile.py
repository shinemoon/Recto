"""书籍文件模型 —— txt / epub 统一的"字节坐标系"。

为什么是字节坐标
----------------
云端阅读服务的 readPercent 定义就是

    readPercent = (Σ bytes(spine[0..chapterId]) + f3) / Σ bytes(spine)

(f3 = 章内**字节**偏移，见 core/geometry.py 的反推证据)。本阅读器直接以
原书字节为唯一坐标，于是：

* 不需要把 epub 切片重排（切片是为了让"一页 = 一篇 spine"的阅读器
  也能表达字节比例；我们自己按字节记进度，没有这个约束）；
* 进度 / 书签 / 划线 / 笔记 全部能写成与云端同构的
  `readPosition`（`<ch+1>_0_0$##3-0-<ch+1>-<offset>-0##$`）；
* 同一本书在别的设备上打开时，只要拿到同一个字节偏移就能落到同一段文字。

数据结构
--------
`BookDoc.chapters` 是**阅读顺序的文档列表**：

    epub: 一篇 spine 文档 = 一章 (doc index = spine 下标)
    txt : 一个章节 = 一章（按标题行切，切不出来就整本一章）

对 epub 而言 `catalog_id = doc - 1` 就是云端的 chapterId（spine[0] 是封面，
不占目录）；封面的进度一律折算成 (chapterId 0, offset 0)。

`BookDoc.blocks(doc)` 返回该文档的块级元素列表，每块带
`off`（块起始标签在本篇原始字节里的偏移）。**块的起始字节就是可写回的
f3 值** —— 粒度是段落，比"页"细得多，且不依赖任何重排。
"""
from __future__ import annotations

import hashlib
import html as _html
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from recto.core import bookstyle
from recto.core import geometry

EPUB_SUFFIXES = {".epub"}
TXT_SUFFIXES = {".txt"}

BLOCK_TAGS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "li", "pre",
    "td", "th", "figcaption", "dd", "dt", "div", "section", "article",
    "header", "footer", "aside",
}
INLINE_KEEP = {
    "em", "i", "b", "strong", "u", "br", "sup", "sub", "code", "small",
    "ruby", "rt", "rp", "mark", "del", "ins", "q", "cite", "abbr",
}
SKIP_TAGS = {"script", "style", "head", "title", "nav", "svg", "math", "iframe", "object"}

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_TAG = re.compile(r"<(/?)([A-Za-z][\w:.-]*)((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>", re.S)
# epub 的封面/插图常写成内联 SVG (`<svg><image xlink:href=".."/></svg>`),
# 只认 <img> 会把整张图漏掉
_IMG = re.compile(r"<(?:img|image)\b[^>]*>", re.I | re.S)
_ATTR = re.compile(r"""([\w:.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""")

_DC_TITLE = re.compile(r"<dc:title[^>]*>(.*?)</dc:title>", re.S | re.I)
_DC_CREATOR = re.compile(r"<dc:creator[^>]*>(.*?)</dc:creator>", re.S | re.I)
_META_COVER = re.compile(r'<meta\b[^>]*name="cover"[^>]*content="([^"]+)"', re.I)
_ITEM = re.compile(r"<item\b[^>]*>", re.I)

_CHAP_PATTERNS = [
    re.compile(r"^\s*(?:第\s*[0-9０-９零一二三四五六七八九十百千两]+\s*[章节回卷篇话])"),
    re.compile(r"^\s*(?:序章|楔子|引子|尾声|后记|终章|番外)"),
    re.compile(r"^\s*Chapter\s+[0-9IVXLC]+", re.I),
    re.compile(r"^\s*CHAPTER\s+[0-9IVXLC]+"),
]

# 章节内的装饰性分隔符（`◎` `*` `——` 这类独占一段的符号）。
# 中文版式里它是"视觉停顿"，居中、收紧字距才像分隔而不是一个孤零零的字。
_SEP_ONLY = re.compile(r"^\s*[◎●○◆◇■□▲△☆★※＊*·•—–—―~〜❖✧✦⁂¶§#]{1,3}\s*$")


def _drop_decls(style: str, names: set[str]) -> str:
    """从内联样式串里剔掉若干声明（`a:1;b:2` → 保留不在 names 里的）。"""
    if not style:
        return ""
    keep = []
    for decl in style.split(";"):
        if ":" not in decl:
            continue
        prop = decl.split(":", 1)[0].strip().lower()
        if prop and prop not in names:
            keep.append(decl.strip())
    return ";".join(keep)


# 破折号/分数线一族：它们连起来才是一条线，不该散开字距
_DASH_CHARS = set("—–―─━ーｰ-_")


def _is_dash_only(text: str) -> bool:
    t = text.strip()
    return bool(t) and all(c in _DASH_CHARS for c in t)



def book_key(path: str | Path) -> str:
    """稳定书 key —— 由**原书真实路径**派生（切片版会先解析回原书）。"""
    real = str(Path(path).resolve())
    return hashlib.sha1(real.encode("utf-8")).hexdigest()[:16]


def _unescape(s: str) -> str:
    return _html.unescape(s)


def strip_tags(s: str) -> str:
    return _unescape(re.sub(r"<[^>]+>", "", s or ""))


def _img_html(tag: str, *, doc_path: str, key: str,
              styles: "bookstyle.BookStyles | None" = None) -> str:
    """单个 img / image 标签 -> 阅读器可渲染的 HTML。

    尺寸来自本书样式表（img_style）：epub 里像 .w10 这类
    "脚注标记小图标"的窄宽度被丢掉后，图片会按原始像素铺满整行。
    """
    attrs = {a.group(1).lower(): (a.group(2) or a.group(3) or a.group(4) or "")
             for a in _ATTR.finditer(tag or "")}
    src = (attrs.get("src") or attrs.get("xlink:href")
           or attrs.get("href") or "").strip()
    size = (styles.img_style(attrs) if styles else "")
    style_attr = f' style="{_html.escape(size)}"' if size else ""
    if not src:
        return '<span class="rfig">［图片］</span>'
    if re.match(r"^(?:https?:|data:)", src, re.I):
        return (f'<img class="rfig" loading="eager" decoding="async" '
                f'src="{_html.escape(src)}" alt=""{style_attr}>')
    p = _resolve_href(doc_path, src)
    url = f"/read/api/book/{key}/asset?p={quote(p)}"
    return (f'<img class="rfig" loading="eager" decoding="async" '
            f'src="{url}" alt=""{style_attr}>')


def _clean_inline(raw: str, *, doc_path: str, key: str,
                  styles: "bookstyle.BookStyles | None" = None) -> str:
    """清洗一段块级元素的内部 HTML：保留行内语义、去掉一切危险/无关属性。

    `<img>` 的尺寸从本书样式表 + 行内 style + width/height 属性里取
    （见 `core/bookstyle.py`）—— 这是图文混排的关键：epub 常把脚注标记图标
    写成 `<img class="w10">`，`.w10 { width: .85em }` 一丢，图片就按原始像素
    （动辄 500px）铺满整行。
    """
    raw = _COMMENT.sub("", raw or "")

    imgs: list[str] = []

    def _img(m: re.Match) -> str:
        imgs.append(_img_html(m.group(0), doc_path=doc_path, key=key, styles=styles))
        return f"\x00IMG{len(imgs) - 1}\x00"

    # 先把 <img> 换成占位符，否则通用标签过滤器会把它当未知标签丢掉
    raw = _IMG.sub(_img, raw)

    out: list[str] = []
    pos = 0
    for m in _TAG.finditer(raw):
        out.append(raw[pos:m.start()])
        closing, name, _attrs = m.group(1), m.group(2).lower(), m.group(3)
        if name in INLINE_KEEP:
            if name == "br":
                out.append("<br>")
            else:
                out.append(f"</{name}>" if closing else f"<{name}>")
        # 其余标签一律丢弃（保留内部文字）
        pos = m.end()
    out.append(raw[pos:])
    txt = "".join(out)
    txt = re.sub(r"\x00IMG(\d+)\x00", lambda m: imgs[int(m.group(1))], txt)
    # 块里残留的换行压成空格（pre 由 CSS white-space 处理）
    txt = re.sub(r"[ \t\r\n]+", " ", txt).strip()
    # 字节切片可能把结尾标签切成半截（`</body`），别把它当正文显示出来
    txt = re.sub(r"</?[A-Za-z][\w:.-]*$", "", txt).strip()
    return txt


def _resolve_href(doc_path: str, href: str) -> str:
    href = (href or "").strip().split("#", 1)[0]
    if not href:
        return ""
    base = posixpath.dirname(doc_path)
    p = posixpath.normpath(posixpath.join(base, href))
    return p.lstrip("/")


def _block_text(inner_html: str) -> str:
    return strip_tags(inner_html).strip()


@dataclass
class Chapter:
    index: int              # 阅读顺序下标（epub = spine 下标，txt = 章序号）
    title: str
    start: int              # 本章在全书字节流里的起始字节
    size: int               # 本章字节数
    catalog_id: int | None  # 云端 chapterId（epub: index-1；txt: index）
    href: str = ""          # epub 内部路径 / txt 空


@dataclass
class Block:
    off: int                # 块起始字节（相对本章）
    tag: str
    html: str
    text: str
    style: str = ""         # 本书样式表里该块的版面声明（内联样式串）
    role: str = ""          # 语义角色："" 普通 / "sep" 装饰分隔符（样式见 reader.css）

    def as_dict(self) -> dict:
        return {"off": self.off, "tag": self.tag, "html": self.html,
                "text": self.text, "style": self.style, "role": self.role}


class BookDoc:
    """统一的书籍文档。所有位置都是"全书字节偏移"。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.kind = "epub" if self.path.suffix.lower() in EPUB_SUFFIXES else "txt"
        self.key = book_key(self.path)
        self.title = self.path.stem
        self.author = ""
        self.chapters: list[Chapter] = []
        self.toc_nodes: list[dict] = []
        self.total_bytes = 0
        self.catalog_total = 0
        self.cover = ""
        self.styles = bookstyle.BookStyles.empty()
        self._geo: geometry.BookGeometry | None = None
        self._blocks: dict[int, list[Block]] = {}
        self._zip: zipfile.ZipFile | None = None
        self._txt: str = ""
        self._txt_raw: bytes = b""
        if self.kind == "epub":
            self._load_epub()
        else:
            self._load_txt()

    # ---- 构建 -----------------------------------------------------------
    def _load_epub(self) -> None:
        self._zip = zipfile.ZipFile(self.path)
        z = self._zip
        opf = next((n for n in z.namelist() if n.lower().endswith(".opf")), None)
        if not opf:
            raise ValueError(f"{self.path}: no opf")
        raw = z.read(opf).decode("utf-8", "replace")
        m = _DC_TITLE.search(raw)
        if m:
            self.title = strip_tags(m.group(1)).strip() or self.title
        m = _DC_CREATOR.search(raw)
        if m:
            self.author = strip_tags(m.group(1)).strip()
        # 封面
        self.cover = _pick_cover_in_zip(z, _cover_from_opf(raw, opf))

        # 书内样式表（只取尺寸/缩进/对齐这类版面属性，见 core/bookstyle.py）
        self.styles = bookstyle.BookStyles.from_zip(z, _css_names_from_opf(raw, opf))

        geo = geometry.load_geometry(self.path, name=self.title)
        self._geo = geo
        self.total_bytes = geo.total_bytes
        self.catalog_total = geo.total_count
        self.toc_nodes = list(geo.toc_nodes or [])
        self.chapters = []
        for i, href in enumerate(geo.spine):
            label = geo.toc.get(i) or geo.toc.get(i - 1) or ""
            if not label:
                label = f"第 {i + 1} 篇" if i else "封面"
            self.chapters.append(Chapter(
                index=i,
                title=label,
                start=sum(geo.sizes[:i]),
                size=geo.sizes[i],
                catalog_id=(i - 1) if i > 0 else 0,
                href=href,
            ))

    def _load_txt(self) -> None:
        self._txt_raw = self.path.read_bytes()
        self.total_bytes = len(self._txt_raw)
        try:
            text = self._txt_raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = self._txt_raw.decode("gb18030")
            except UnicodeDecodeError:
                text = self._txt_raw.decode("utf-8", "replace")
        self._txt = text
        self.title = self.path.stem

        # 逐行定位，按标题行切章。用 encode 长度累计 → 精确定位字节偏移。
        chapters: list[Chapter] = []
        cur_title = "正文"
        cur_start = 0
        cur_lines: list[str] = []

        def flush(end_byte: int) -> None:
            nonlocal cur_lines, cur_start
            size = end_byte - cur_start
            if size > 0:
                chapters.append(Chapter(index=len(chapters), title=cur_title,
                                        start=cur_start, size=size,
                                        catalog_id=len(chapters)))
            cur_lines = []

        offset = 0
        for line in self._txt.splitlines(keepends=True):
            nxt = offset + len(line.encode("utf-8"))
            head = line.strip()
            if head and len(head) <= 60 and any(p.match(head) for p in _CHAP_PATTERNS):
                if chapters or cur_lines:
                    flush(offset)
                cur_title = head
                cur_start = offset
            cur_lines.append(line)
            offset = nxt
        flush(len(self._txt_raw))
        if not chapters:
            chapters = [Chapter(index=0, title="正文", start=0,
                                size=self.total_bytes, catalog_id=0)]
        self.chapters = chapters
        self.catalog_total = len(chapters)
        self.toc_nodes = [{"spine": c.index, "text": c.title, "label": c.title,
                           "depth": 0} for c in chapters]

    # ---- 章节内容 -------------------------------------------------------
    def _chapter_raw(self, idx: int) -> bytes:
        ch = self.chapters[idx]
        if self.kind == "txt":
            return self._txt_raw[ch.start:ch.start + ch.size]
        assert self._zip is not None
        try:
            return self._zip.read(ch.href)
        except KeyError:
            return b""

    def blocks(self, idx: int) -> list[Block]:
        if idx in self._blocks:
            return self._blocks[idx]
        raw = self._chapter_raw(idx)
        if self.kind == "txt":
            out = self._txt_blocks(raw)
        else:
            out = self._epub_blocks(raw, self.chapters[idx].href)
        self._blocks[idx] = out
        return out

    def _txt_blocks(self, raw: bytes) -> list[Block]:
        text = raw.decode("utf-8", "replace")
        out: list[Block] = []
        off = 0
        for para in re.split(r"(\n\s*\n)", text):
            seg = para
            if not seg or seg.isspace():
                off += len(seg.encode("utf-8"))
                continue
            body = seg.strip()
            lead = seg[:len(seg) - len(seg.lstrip())]
            block_off = off + len(lead.encode("utf-8"))
            html = _html.escape(body).replace("\n", "<br>")
            if body:
                out.append(Block(off=block_off, tag="p", html=html, text=body))
            off += len(seg.encode("utf-8"))
        if not out:
            for line in text.splitlines():
                body = line.strip()
                if body:
                    boff = raw.find(line.encode("utf-8")) if False else None
                    out.append(Block(off=0, tag="p",
                                     html=_html.escape(body), text=body))
        return out

    def _epub_blocks(self, raw: bytes, href: str) -> list[Block]:
        roots = geometry._scan_elements(raw)
        body = geometry._find_body(roots)
        if body is None:
            return []
        out: list[Block] = []
        key = self.key
        styles = self.styles

        def node_end(node) -> int:
            e = node.end
            if e < len(raw) and raw[e:e + 1] == b">":
                e += 1
            return e

        def emit(node) -> None:
            slice_ = raw[node.start:node_end(node)].decode("utf-8", "replace")
            # 去掉首尾标签（按标签名精确匹配，别把行内 `</b>` 当结尾）
            inner = slice_
            m1 = _TAG.match(inner)
            if m1 and not m1.group(1) and m1.group(2).lower() == node.name.lower():
                inner = inner[m1.end():]
            lt = inner.rfind("<")
            tail = inner[lt:] if lt >= 0 else ""
            if tail.lower().startswith("</" + node.name.lower()):
                inner = inner[:lt]
            cleaned = _clean_inline(inner, doc_path=href, key=key, styles=styles)
            text = _block_text(cleaned)
            if not text and "<img" not in cleaned:
                return
            # 段内有文字 → 里面的图片是**行内**插图（脚注标记之类），
            # 不是独占一行的插图块，尺寸与基线按行内图片走
            if text:
                cleaned = cleaned.replace('class="rfig"', 'class="rfig rfig-inl"')
            bstyle = styles.block_style(node.name.lower(), node.attrs)
            # 独占一段的装饰符号（◎ / * / ——）：当分隔符用，**不继承书内字号**。
            # 书内 CSS 常把它们挂在标题类上（示例短篇 `.biaoti` = 1.7em + bold），
            # 一个满 em 的实心圆在 1.7em 下比正文重得多 —— 分隔符是"版面停顿"，
            # 不是标题。尺寸/字重/字距一律交给 reader.css 的 .rv-sep 规则。
            role = ""
            if text and _SEP_ONLY.match(text):
                bstyle = _drop_decls(bstyle, {"font-size", "font-weight", "font-style",
                                             "letter-spacing", "text-align",
                                             "text-align-last"})
                # 破折号串（—— ）本来要连成一条长线，加字距会把它拆成两截；
                # 单字符不放字距（否则居中会偏半个字距），多字符才散开
                if _is_dash_only(text):
                    role = "sep-dash"
                else:
                    role = "sep" if len(text.strip()) == 1 else "sep-multi"
            out.append(Block(off=node.start, tag=node.name.lower(),
                             html=cleaned, text=text, style=bstyle, role=role))

        def walk(node) -> None:
            for ch in node.children:
                name = ch.name.lower()
                # 图片元素自己就是一个块: <img class="top"> 这类章首题图/整幅插图
                # 常常是 div 的直接子节点 (与 h2/p 平级), 原来既不进 BLOCK_TAGS
                # 也没有文字, 会被整块丢掉 —— 于是"每章开头的题图"永远不显示。
                # 内联 SVG 包裹的 `<svg><image xlink:href>` 同理。
                if name in ("img", "image", "svg"):
                    seg = raw[ch.start:node_end(ch)].decode("utf-8", "replace")
                    m = _IMG.search(seg)
                    if m:
                        html = _img_html(m.group(0), doc_path=href, key=key,
                                         styles=styles)
                        out.append(Block(off=ch.start, tag="img", html=html, text=""))
                    continue
                if name in SKIP_TAGS:
                    continue
                nested_block = any(c.name.lower() in BLOCK_TAGS for c in ch.children)
                direct_text = bool(strip_tags(raw[ch.start:ch.end].decode("utf-8", "replace")).strip())
                if name in BLOCK_TAGS:
                    if nested_block:
                        walk(ch)
                    else:
                        emit(ch)
                else:
                    if any(c.name.lower() in BLOCK_TAGS for c in ch.children):
                        walk(ch)
                    elif direct_text:
                        emit(ch)

        walk(body)
        if not out:
            slice_ = raw[body.start:body.end].decode("utf-8", "replace")
            cleaned = _clean_inline(slice_, doc_path=href, key=key, styles=styles)
            text = _block_text(cleaned)
            if text:
                out.append(Block(off=body.start, tag="p", html=cleaned, text=text))
        return out

    # ---- 坐标 -----------------------------------------------------------
    def _prefix(self, idx: int) -> int:
        return sum(c.size for c in self.chapters[:idx])

    def percent(self, idx: int, off: int = 0) -> float:
        if self.total_bytes <= 0:
            return 0.0
        idx = max(0, min(idx, len(self.chapters) - 1))
        p = (self._prefix(idx) + max(0, off)) / self.total_bytes
        return max(0.0, min(1.0, p))

    def locate(self, pct01: float) -> tuple[int, int]:
        """字节比例 → (章下标, 章内字节偏移)。"""
        if not self.chapters or self.total_bytes <= 0:
            return 0, 0
        g = max(0.0, min(1.0, float(pct01))) * self.total_bytes
        cum = 0
        for i, c in enumerate(self.chapters):
            if g < cum + c.size or i == len(self.chapters) - 1:
                return i, int(max(0, min(g - cum, max(0, c.size - 1))))
            cum += c.size
        return len(self.chapters) - 1, 0

    def catalog_id(self, idx: int) -> int:
        ch = self.chapters[max(0, min(idx, len(self.chapters) - 1))]
        return int(ch.catalog_id or 0)

    def position(self, idx: int, off: int) -> str:
        """云端同构的 readPosition。epub 用官方格式；txt 用同一格式表达自己的章序。"""
        cid = self.catalog_id(idx)
        return geometry.build_position(cid, max(0, int(off)))

    def percent_of_position(self, chapter_id: int, off: int) -> float:
        """云端 chapterId + f3 → 比例。epub 严格按云端口径；txt 同构。"""
        if self.kind == "epub":
            return float(self._geo.percent(chapter_id, off)) if self._geo else 0.0
        return self.percent(chapter_id, off)

    def locate_position(self, chapter_id: int, off: int) -> tuple[int, int]:
        if self.kind == "epub":
            doc = max(1, min(int(chapter_id) + 1, len(self.chapters) - 1))
            return doc, max(0, int(off))
        return max(0, min(int(chapter_id), len(self.chapters) - 1)), max(0, int(off))

    def text_of_chapter(self, idx: int) -> str:
        return "".join(b.text for b in self.blocks(idx))

    def asset(self, inner_path: str) -> tuple[bytes, str]:
        if self.kind == "txt":
            return b"", "application/octet-stream"
        assert self._zip is not None
        data = self._zip.read(inner_path)
        return data, _mime(inner_path)

    def close(self) -> None:
        if self._zip is not None:
            try:
                self._zip.close()
            except Exception:
                pass
            self._zip = None


_MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
    ".css": "text/css; charset=utf-8", ".xhtml": "application/xhtml+xml; charset=utf-8",
    ".html": "text/html; charset=utf-8", ".ttf": "font/ttf", ".otf": "font/otf",
    ".woff": "font/woff", ".woff2": "font/woff2",
}


def _mime(path: str) -> str:
    return _MIME.get(Path(path).suffix.lower(), "application/octet-stream")


_IMG_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".svg")


def _css_names_from_opf(raw: str, opf: str) -> list[str]:
    """OPF manifest 里登记的样式表路径（压缩包内绝对路径）。"""
    base = posixpath.dirname(opf)
    out: list[str] = []
    for item in _ITEM.finditer(raw):
        tag = item.group(0)
        if "text/css" not in tag.lower():
            continue
        h = re.search(r'href="([^"]+)"', tag)
        if not h:
            continue
        p = _resolve_href(posixpath.join(base, "x"), h.group(1)) if base else h.group(1)
        if p:
            out.append(p)
    return out


def _cover_from_opf(raw: str, opf: str) -> str:
    """从 OPF 里解析封面图路径（压缩包内相对路径）。

    两代写法都认：EPUB3 的 `properties="cover-image"`，
    EPUB2 的 `<meta name="cover" content="<id>"/>`。解析不出来返回空串。
    """
    ids: dict[str, str] = {}
    base = posixpath.dirname(opf)
    cover = ""
    for item in _ITEM.finditer(raw):
        tag = item.group(0)
        i = re.search(r'id="([^"]+)"', tag)
        h = re.search(r'href="([^"]+)"', tag)
        if not (i and h):
            continue
        p = _resolve_href(posixpath.join(base, "x"), h.group(1)) if base else h.group(1)
        ids[i.group(1)] = p
        if 'properties="' in tag and "cover-image" in tag:
            cover = p
    if not cover:
        mc = _META_COVER.search(raw)
        if mc:
            cover = ids.get(mc.group(1), "")
    return cover


def _pick_cover_in_zip(z: "zipfile.ZipFile", cover: str) -> str:
    """校验封面确实在包里；不在就按文件名里带 cover 的图兜底。"""
    names = z.namelist()
    if cover and cover in names:
        return cover
    for n in names:
        base = posixpath.basename(n).lower()
        if "cover" in base and base.endswith(_IMG_SUFFIXES):
            return n
    return ""


def read_meta(path: str | Path) -> dict:
    """只读 OPF 的轻量元数据（书架列表用，不加载全书几何）。"""
    p = Path(path)
    out = {"title": p.stem, "author": "", "cover": "", "kind": "txt",
           "bytes": p.stat().st_size if p.exists() else 0}
    if p.suffix.lower() in EPUB_SUFFIXES:
        out["kind"] = "epub"
        try:
            with zipfile.ZipFile(p) as z:
                opf = next((n for n in z.namelist() if n.lower().endswith(".opf")), None)
                if opf:
                    raw = z.read(opf).decode("utf-8", "replace")
                    m = _DC_TITLE.search(raw)
                    if m:
                        out["title"] = strip_tags(m.group(1)).strip() or out["title"]
                    m = _DC_CREATOR.search(raw)
                    if m:
                        out["author"] = strip_tags(m.group(1)).strip()
                    out["cover"] = _pick_cover_in_zip(z, _cover_from_opf(raw, opf))
        except (OSError, zipfile.BadZipFile):
            pass
    return out
