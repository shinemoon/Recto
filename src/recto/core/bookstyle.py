"""从 epub 自带的样式表里取出「页内排版必需」的一小撮属性，转成内联样式。

为什么需要它
------------
原书 CSS 是按「一整页纸」写的：`body` 宽度、`*` 的 margin 清零、整套配色、
字体族、行高……照搬到阅读器里，读者的版式设置（字号 / 行距 / 段距 /
字体 / 主题）会全部失效。

但**完全不读**也不行。很多版面信息只写在 class 上，典型：

    .w10    { width: 0.85em }        /* 脚注标记的小图标 */
    .biaoti { text-align: center }   /* 章节分隔符 ◎ */
    .po     { text-indent: -1em; font-size: .9em }   /* 注文悬挂缩进 */

丢掉这些声明，那个本该 0.85em 的小图标就会按图片原始像素（500×500）
铺满一整行 —— 「注释图片应该是缩小的混排小图，结果几乎全宽」正是这么来的。

取用范围（白名单，只取"这本书的版面"，不取"读者的版式"）
--------------------------------------------------------
    img : width / height / max-width / max-height / vertical-align
          以及 <img width=".." height=".."> 属性
    块  : text-indent / text-align / font-size / font-style / font-weight /
          padding-left / padding-right

不取：color / background / border / font-family / line-height / margin /
      display / position / float。颜色跟主题走，行高与段距跟读者设置走。

选择器支持
----------
只认「主体复合选择器」：`p`、`.po`、`p.po`、`.a.b`；复合选择器取最后一个
（`.section p` → `p`，`body > .section .biaoti` → `.biaoti`）。带伪类
（`:hover` 等）与属性选择器的规则一律跳过 —— 那些不是静态版面。
"""
from __future__ import annotations

import re
import zipfile
from typing import Iterable

__all__ = ["BookStyles", "parse_css"]

_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)

IMG_PROPS = {
    "width", "height", "max-width", "max-height",
    "vertical-align", "margin-top", "margin-bottom",
    "margin-left", "margin-right", "object-fit",
}
BLOCK_PROPS = {
    "text-indent", "text-align", "text-align-last",
    "font-size", "font-style", "font-weight", "letter-spacing",
    "padding-left", "padding-right",
}

_UNIT = r"(?:px|em|rem|%|pt|ex|ch|vw|vh|vmin|vmax)"
_LEN_RE = re.compile(r"^[-+]?(?:\d+(?:\.\d+)?|\.\d+)" + _UNIT + r"$", re.I)
_NUM_RE = re.compile(r"^[-+]?(?:\d+(?:\.\d+)?|\.\d+)$")
_BAD_VALUE = ("url(", "expression", "javascript", "var(", "@", "\\", "/*")

_ALIGN = {"left", "right", "center", "justify", "start", "end"}
_VALIGN = {"baseline", "middle", "sub", "super", "top", "bottom",
           "text-top", "text-bottom"}


def _ok_value(prop: str, val: str) -> bool:
    v = (val or "").strip()
    if not v or len(v) > 40:
        return False
    low = v.lower()
    if any(b in low for b in _BAD_VALUE):
        return False
    if prop in ("text-align", "text-align-last"):
        return low in _ALIGN or low == "auto"
    if prop == "font-style":
        return low in ("normal", "italic", "oblique")
    if prop == "font-weight":
        return low in ("normal", "bold", "bolder", "lighter") or (_NUM_RE.match(low) is not None
                                                                and 100 <= float(low) <= 900)
    if prop == "vertical-align":
        return low in _VALIGN or _LEN_RE.match(low) is not None
    if prop == "object-fit":
        return low in ("contain", "cover", "scale-down", "none", "fill")
    if prop in ("width", "height", "max-width", "max-height"):
        return low in ("auto", "none") or _LEN_RE.match(low) is not None
    if prop in ("text-indent", "padding-left", "padding-right",
                "margin-top", "margin-bottom", "margin-left", "margin-right"):
        return _LEN_RE.match(low) is not None
    if prop == "font-size":
        return _LEN_RE.match(low) is not None or low in (
            "xx-small", "x-small", "small", "medium", "large", "x-large")
    if prop == "letter-spacing":
        return low == "normal" or _LEN_RE.match(low) is not None
    return False


def _parse_decls(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (body or "").split(";"):
        if ":" not in part:
            continue
        prop, _, val = part.partition(":")
        prop = prop.strip().lower()
        val = re.sub(r"!\s*important", "", val, flags=re.I).strip()
        if prop and val:
            out[prop] = val
    # padding / margin 简写展开成四边 —— 注文的悬挂缩进就靠
    # `padding: 0 0.1em 0.1em 1em` 里的 padding-left
    for prop in ("padding", "margin"):
        if prop in out:
            parts = out.pop(prop).split()
            if 1 <= len(parts) <= 4:
                sides = _expand_box(parts)
                for side, v in sides.items():
                    out.setdefault(f"{prop}-{side}", v)
    return out


def _expand_box(parts: list[str]) -> dict[str, str]:
    top = parts[0]
    right = parts[1] if len(parts) > 1 else top
    bottom = parts[2] if len(parts) > 2 else top
    left = parts[3] if len(parts) > 3 else right
    return {"top": top, "right": right, "bottom": bottom, "left": left}


def _filter(decls: dict[str, str], allow: set[str]) -> dict[str, str]:
    return {p: v for p, v in decls.items() if p in allow and _ok_value(p, v)}


def _walk_block(src: str, out: list[tuple[list[str], dict[str, str]]]) -> None:
    i, n = 0, len(src)
    while i < n:
        j = src.find("{", i)
        if j < 0:
            break
        sel = src[i:j].strip()
        depth, k = 1, j + 1
        while k < n and depth:
            c = src[k]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            k += 1
        body = src[j + 1:k - 1]
        if sel.startswith("@"):
            low = sel.lower()
            # @media / @supports 里是正常规则，钻进去；@font-face 之类跳过
            if re.match(r"@(media|supports|document)\b", low):
                _walk_block(body, out)
        elif sel:
            decls = _parse_decls(body)
            if decls:
                out.append(([t.strip() for t in sel.split(",") if t.strip()], decls))
        i = k


def parse_css(text: str) -> list[tuple[list[str], dict[str, str]]]:
    """CSS 文本 → [(选择器列表, 声明表)]。只做静态规则的浅层展开。"""
    return_rules: list[tuple[list[str], dict[str, str]]] = []
    _walk_block(_CSS_COMMENT.sub(" ", text or ""), return_rules)
    return return_rules


def _subject(term: str) -> str:
    """复合选择器主体（最后一个 token）；带伪类/属性选择器的返回空串。"""
    t = (term or "").strip()
    if not t or ":" in t or "[" in t:
        return ""
    parts = [p for p in re.split(r"\s*[>+~]\s*|\s+", t) if p]
    return parts[-1] if parts else ""


_COMPOUND = re.compile(r"^([A-Za-z][\w-]*|\*)?((?:\.[\w-]+)*)$")


def _match(compound: str, tag: str, classes: set[str]) -> bool:
    m = _COMPOUND.match(compound or "")
    if not m:
        return False
    t = (m.group(1) or "").lower()
    if t and t != "*" and t != tag:
        return False
    need = [c for c in (m.group(2) or "").split(".") if c]
    if need:
        return all(c in classes for c in need)
    return bool(t) and t != "*"


def _classes(attr: str | None) -> set[str]:
    return set((attr or "").split())


def _css(decls: dict[str, str]) -> str:
    return ";".join(f"{p}:{v}" for p, v in decls.items())


class BookStyles:
    """一本书的样式表（已切到内联样式口径）。"""

    __slots__ = ("rules",)

    def __init__(self, rules: Iterable[tuple[list[str], dict[str, str]]] = ()):
        # 预先算好每条规则的"主体复合选择器"，查询时只做字符串比较
        self.rules: list[tuple[str, dict[str, str], bool]] = []
        for terms, decls in rules:
            for term in terms:
                subj = _subject(term)
                if not subj or subj == "*":
                    continue
                self.rules.append((subj, decls, "." in subj))

    # ---- 构造 ---------------------------------------------------------
    @classmethod
    def empty(cls) -> "BookStyles":
        return cls()

    @classmethod
    def from_zip(cls, z: zipfile.ZipFile, opf_names: Iterable[str] = ()) -> "BookStyles":
        names: list[str] = [n for n in opf_names if n.lower().endswith(".css")]
        if not names:
            names = [n for n in z.namelist() if n.lower().endswith(".css")]
        rules: list[tuple[list[str], dict[str, str]]] = []
        for name in names[:12]:          # 一本书的样式表不会多到几十个；防异常包
            try:
                data = z.read(name).decode("utf-8", "replace")
            except (KeyError, OSError):
                continue
            rules.extend(parse_css(data))
        return cls(rules)

    # ---- 查询 ---------------------------------------------------------
    def decls(self, tag: str, classes: set[str]) -> tuple[dict[str, str], dict[str, str]]:
        """(来自裸标签选择器的声明, 来自带 class 选择器的声明)，均按文档序合并。"""
        tag_d: dict[str, str] = {}
        cls_d: dict[str, str] = {}
        for subj, decls, has_class in self.rules:
            if not _match(subj, tag, classes):
                continue
            (cls_d if has_class else tag_d).update(decls)
        return tag_d, cls_d

    def img_style(self, attrs: dict[str, str]) -> str:
        tag_d, cls_d = self.decls("img", _classes(attrs.get("class")))
        decls = _filter(tag_d, IMG_PROPS)
        decls.update(_filter(cls_d, IMG_PROPS))
        decls.update(_filter(_parse_decls(attrs.get("style") or ""), IMG_PROPS))
        # `max-width/max-height: 100%` 是 epub 里「别超出父容器」的常规写法，
        # 搬成内联反而会盖掉阅读器自己按页高算的上限（`100%` 在内联里
        # 相对的是自动高度的父盒 = 不设限）。交给阅读器 CSS 处理。
        for prop in ("max-width", "max-height"):
            if (decls.get(prop) or "").replace(" ", "") == "100%":
                decls.pop(prop)
        for prop in ("width", "height"):
            if prop in decls:
                continue
            v = (attrs.get(prop) or "").strip()
            if v and (_NUM_RE.match(v) or _LEN_RE.match(v)):
                decls[prop] = v + "px" if _NUM_RE.match(v) else v
        return _css(decls)

    def block_style(self, tag: str, attrs: dict[str, str]) -> str:
        tag_d, cls_d = self.decls(tag, _classes(attrs.get("class")))
        decls: dict[str, str] = {}
        for prop, val in _filter(tag_d, BLOCK_PROPS).items():
            # 首行缩进是读者的排版选择（clreq 2 字符 / 现代无缩进），
            # 只有 class 规则里明确写死的才采纳（注文悬挂、分隔符居中）
            if prop == "text-indent":
                continue
            decls[prop] = val
        decls.update(_filter(cls_d, BLOCK_PROPS))
        decls.update(_filter(_parse_decls(attrs.get("style") or ""), BLOCK_PROPS))
        return _css(decls)

    # ---- 诊断 ---------------------------------------------------------
    def summary(self) -> dict:
        return {"rules": len(self.rules),
                "selectors": [r[0] for r in self.rules][:80]}
