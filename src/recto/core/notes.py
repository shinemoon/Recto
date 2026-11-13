"""注释层 —— 把「注释正文」与「正文里的引用标记」分开。

电子书的注释有几十种写法：多看式 `<aside epub:type="footnote"><ol><li>` 紧跟在
段落后、epub3 的 `<section epub:type="endnotes"><ol><li id="fn1">` 堆在章末、
calibre 导出的 `<div class="footnote">`、以及只靠 class 命名的 sidenote / marginalia。
本模块只认**语义**（`epub:type` / `role` / class 词干），不认具体书，也不写死
任何 class 名 —— 换一本书不用改代码。

两件事：

1. `harvest(raw)` → `NoteLayer`
   扫一遍本章字节流，收集注释正文池 + 需要从正文流里剔掉的字节区间
   （注释正文不该当正文段落读出来）。

2. 正文清洗时把 `noteref` 锚点换成 `<sup class="rv-fn" data-note="N">N</sup>`，
   注释正文由章节接口单独下发，前端按用户选的形态（弹出 / 侧栏 / 章末）呈现。

**为什么不能只靠 id 绑**：本样本（541 条注释）里有 52 个 `href` 被重复使用，
被指向的那个 `<aside>` 与引用处根本不是同一条注释；真正的注释正文写在标记
`<img alt="…">` 的 `alt` / `zy-footnote` 属性里。所以绑定顺序是
「内容优先，id 兜底」，并且内容匹配要能**消费**（同一正文出现两次就算两条）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape

from recto.core import geometry

# 语义词干 —— 命中任何一个就认为这个元素跟注释有关
_NOTE_WORDS = (
    "footnote", "endnote", "rearnote", "sidenote", "marginalia",
    "duokan-footnote", "doc-footnote", "doc-endnote", "note-ref", "noteref",
)
# 引用标记（正文里那个小数字）的语义词干
_MARK_WORDS = ("noteref", "note-ref", "footnote-ref", "fnref", "calibre-noteref")

# 容器里"本身就是一条注释"的标签：`<ol><li>` 是主流写法
_ITEM_TAGS = {"li"}

# 注释正文开头的编号（`1.` `［1］` `①` `(1)`）—— 前端自己编号，正文里不要重复
_LABEL_RE = re.compile(r"^\s*(?:[\[［(（【]\s*\d{1,3}\s*[\]］)）】]|\d{1,3}\s*[.、,．]|[①-⑳])\s*")

_ANCHOR_RE = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.S | re.I)
_ATTR_RE = re.compile(r"""([\w:.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""")
_TAG_RE = re.compile(r"<[^>]+>")



def _attrs(s: str) -> dict[str, str]:
    return {(m.group(1) or "").lower(): (m.group(2) or m.group(3) or m.group(4) or "")
            for m in _ATTR_RE.finditer(s or "")}


def _words(s: str) -> str:
    """把 class / epub:type 串压成小写词串，方便按词干找。"""
    return " ".join(re.findall(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", (s or "").lower()))


def _sig(attrs: dict[str, str]) -> tuple[str, str]:
    """元素的语义签名：`epub:type` / `role` / `class` 三者归一。"""
    kind = " ".join([attrs.get("epub:type", ""), attrs.get("role", ""),
                     attrs.get("class", "")]).lower()
    return _words(kind), kind


def _is_note_container(attrs: dict[str, str], tag: str) -> bool:
    words, kind = _sig(attrs)
    if not any(w in words for w in _NOTE_WORDS):
        return False
    # `note-ref` / `noteref` 是**引用**，不是容器
    if any(w in words for w in _MARK_WORDS) and "footnote" not in words.replace("noteref", ""):
        return False
    return tag not in ("a", "sup", "sub", "img")


def _is_mark(attrs: dict[str, str]) -> bool:
    words, _ = _sig(attrs)
    if any(w in words for w in _MARK_WORDS):
        return True
    href = (attrs.get("href") or "").strip()
    if not href.startswith("#"):
        return False
    # 没有语义标注时退一步：`#fn1` / `#ftn_2` / `#note3` 这类锚点 + 锚文本很短
    if re.match(r"^#(?:fn|ftn|note|endnote|footnote|n)\d", href, re.I):
        return True
    return False


def _norm(s: str) -> str:
    return " ".join((s or "").split())


def _plain(s: str) -> str:
    return _norm(unescape(_TAG_RE.sub("", s or "")))


def _node_end(raw: bytes, node) -> int:
    e = node.end
    if e < len(raw) and raw[e:e + 1] == b">":
        e += 1
    return e


@dataclass
class Note:
    n: int                      # 本章内序号（1 基，按引用出现的顺序）
    text: str                   # 注释正文（纯文本）
    nid: str = ""               # 源 id（诊断用）
    orphan: bool = False        # 正文里没有引用标记（只在侧栏/章末里出现）
    unresolved: bool = False    # 有标记但找不到正文

    def as_dict(self) -> dict:
        return {"n": self.n, "text": self.text,
                "orphan": self.orphan, "unresolved": self.unresolved}


@dataclass
class NoteLayer:
    """一章的注释池。`resolve()` 逐步消费，`payload()` 给章节接口。"""

    notes: list[Note] = field(default_factory=list)
    _skip: list[tuple[int, int]] = field(default_factory=list)
    _by_id: dict[str, list[Note]] = field(default_factory=dict)
    _by_text: dict[str, list[Note]] = field(default_factory=dict)
    _claimed: set[int] = field(default_factory=set)
    _next: int = 1

    # ---- 采集期 -------------------------------------------------------
    def add(self, text: str, nid: str = "") -> Note | None:
        text = _plain(_LABEL_RE.sub("", text or ""))
        if not text:
            return None
        note = Note(n=0, text=text, nid=nid or "")
        self.notes.append(note)
        if nid:
            self._by_id.setdefault(nid, []).append(note)
        self._by_text.setdefault(_norm(text), []).append(note)
        return note

    def skip_range(self, start: int, end: int) -> None:
        self._skip.append((start, end))

    def skips(self, pos: int) -> bool:
        for s, e in self._skip:
            if s <= pos < e:
                return True
        return False

    def skip_list(self) -> list[tuple[int, int]]:
        return list(self._skip)

    # ---- 引用期 -------------------------------------------------------
    def _claim(self, note: Note) -> Note:
        if id(note) not in self._claimed:
            self._claimed.add(id(note))
            note.n = self._next
            self._next += 1
        return note

    def resolve(self, hint: str = "", href: str = "") -> Note | None:
        """引用处 → 注释正文。

        顺序：内容精确匹配（未占用优先，已占用就复用同一条）→ id → 用标记
        自带的内容（多看的 `alt` / `zy-footnote`）→ 无正文。
        """
        hint = _norm(hint)
        # 太短的 hint（`1` / `注`）只是标签，不拿它当正文
        use_hint = len(hint) >= 6
        if use_hint:
            pool = self._by_text.get(hint) or []
            for cand in pool:
                if id(cand) not in self._claimed:
                    return self._claim(cand)
            if pool:                        # 同一正文被引用两次 → 共用同一条
                return self._claim(pool[0])
        nid = (href or "").strip().lstrip("#")
        if nid:
            pool = self._by_id.get(nid) or []
            for cand in pool:
                if id(cand) not in self._claimed:
                    return self._claim(cand)
            if pool:
                return self._claim(pool[0])
        if use_hint:
            note = Note(n=0, text=hint)
            self.notes.append(note)
            return self._claim(note)
        return None

    def finish(self) -> None:
        """正文里没被引用的注释也编号（侧栏/章末能看到）。"""
        for note in self.notes:
            if id(note) not in self._claimed:
                self._claimed.add(id(note))
                note.n = self._next
                note.orphan = True
                self._next += 1
        self.notes.sort(key=lambda x: x.n)

    def payload(self) -> list[dict]:
        return [n.as_dict() for n in self.notes]

    # ---- 标记 HTML ----------------------------------------------------
    @staticmethod
    def marker_html(note: Note | None) -> str:
        if note is None:
            return ('<sup class="rv-fn rv-fn-void" role="button" tabindex="0" '
                    'aria-label="注释（无正文）">※</sup>')
        return (f'<sup class="rv-fn" data-note="{note.n}" role="button" '
                f'tabindex="0" aria-label="注释 {note.n}">{note.n}</sup>')


def harvest(raw: bytes) -> NoteLayer | None:
    """扫一章字节流，收集注释正文池与要从正文里剔掉的区间。

    没有注释的章节返回 `None`（调用方据此走原来的零开销路径）。
    """
    roots = geometry._scan_elements(raw)
    body = geometry._find_body(roots)
    if body is None:
        return None
    layer = NoteLayer()
    found = 0

    def visit(node, inside_container: bool) -> None:
        nonlocal found
        for ch in node.children:
            name = ch.name.lower()
            container = _is_note_container(ch.attrs, name)
            if container and not inside_container:
                found += visit_container(ch)
                continue
            visit(ch, inside_container)

    def visit_container(node) -> int:
        targets = _items(node)
        added = 0
        for t in targets:
            text = raw[t.start:_node_end(raw, t)].decode("utf-8", "replace")
            nid = (t.attrs.get("id") or node.attrs.get("id") or "").strip()
            if layer.add(text, nid) is not None:
                added += 1
        layer.skip_range(node.start, _node_end(raw, node))
        return max(1, added) if added else 1

    def _items(node) -> list:
        """容器里"每一条注释"分别是谁。

        三种常见写法都要认：
          `<aside><ol><li>…</li></ol></aside>`        多看式（li 才是单条）
          `<section epub:type="endnotes"><ol><li>…`    epub3 尾注（同样藏在 ol 里）
          `<div class="footnotes"><p class="footnote">…`  每条子元素自己带 note 语义
        认不出来就当整个容器是一条。
        """
        lis: list = []
        for c in node.children:
            name = c.name.lower()
            if name in _ITEM_TAGS:
                lis.append(c)
            elif name in ("ol", "ul"):
                for g in c.children:
                    if g.name.lower() in _ITEM_TAGS:
                        lis.append(g)
        if lis:
            flat: list = []
            for it in lis:
                flat.extend(_item_descendants(it))
            return flat or lis
        subs = [c for c in node.children
                if _is_note_container(c.attrs, c.name.lower())]
        if subs:
            return subs
        return [node]

    def _item_descendants(it) -> list:
        # 有的书写 `<ol><li><ol><li>`（嵌套列表当编号），取最内层有文字的 li
        subs = [c for c in it.children if c.name.lower() in _ITEM_TAGS]
        if not subs:
            return [it]
        out: list = []
        for s in subs:
            out.extend(_item_descendants(s))
        return out or [it]

    visit(body, False)
    if not found:
        # 没有注释容器, 但仍可能有引用标记（标记自带正文, 或根本没有正文）。
        # 这时也要给一个空池, 否则 `<a epub:type="noteref">` 会原样留在正文里
        # —— 表现为段落里冒出一个无意义的数字或小图标。
        if not _RAW_MARK_RE.search(raw):
            return None
    return layer


_INLINE_CONTAINER_TAGS = {"aside", "section", "div", "ol", "ul"}


_ANY_TAG = re.compile(r"<(/?)([A-Za-z][\w:.-]*)\b([^>]*)>", re.S)
# 引用标记的字节级特征 —— 用来判断"这一章到底有没有注释引用"（不再解析）
_RAW_MARK_RE = re.compile(
    rb"<(?:a|span|sup)\b[^>]*(?:noteref|note-ref|footnote-ref|fnref)", re.I)


def _match_end(raw: str, pos: int, tag: str) -> int:
    """从 `pos` 往后找 `tag` 的配对闭合位置（按同名标签计深度）。"""
    depth = 1
    for m in _ANY_TAG.finditer(raw, pos):
        if m.group(2).lower() != tag:
            continue
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return m.end()
    return len(raw)


def strip_inline_bodies(raw: str) -> str:
    """剔掉嵌在段落内部的注释容器（`<p>…<aside>注释</aside>…</p>`）。

    按标签逐个扫，不能用一个"外层容器包住全部"的正则 —— `<body><div><p>…<aside>`
    里 `(aside|section|div|span)` 会先咬住外层 `<div>…</div>`，而它自己不算注释
    容器，`re.sub` 就直接跳过整段，里面的 aside 一辈子扫不到（实测踩过）。
    """
    if not raw:
        return raw
    out: list[str] = []
    last = 0
    i = 0
    n = len(raw)
    while i < n:
        m = _ANY_TAG.search(raw, i)
        if not m:
            break
        if m.group(1):                        # 闭合标签: 原样留
            i = m.end()
            continue
        tag = m.group(2).lower()
        if tag in _INLINE_CONTAINER_TAGS and _is_note_container(_attrs(m.group(3)), tag):
            out.append(raw[last:m.start()])
            last = _match_end(raw, m.end(), tag)
            i = last
            continue
        i = m.end()
    out.append(raw[last:])
    return "".join(out)


def extract_marker_anchor(anchor_attrs: dict[str, str], inner: str) -> tuple[bool, str, str]:
    """判断一个 `<a>` 是不是注释引用。

    返回 `(是否引用, 内容提示, href)`。内容提示来自标记图片的
    `alt` / `zy-footnote` / `title`（多看式把注释正文直接写在这里）。
    """
    if not _is_mark(anchor_attrs):
        return False, "", ""
    hint = ""
    for key in ("zy-footnote", "alt", "title", "data-note", "data-content"):
        val = anchor_attrs.get(key)
        if val and len(_norm(val)) >= 6:
            hint = val
            break
    if not hint:
        for key in ("zy-footnote", "alt", "title"):
            for m in _ATTR_RE.finditer(inner or ""):
                if (m.group(1) or "").lower() == key:
                    val = m.group(2) or m.group(3) or m.group(4) or ""
                    if len(_norm(val)) >= 6:
                        hint = val
                        break
            if hint:
                break
    return True, hint, (anchor_attrs.get("href") or "")
