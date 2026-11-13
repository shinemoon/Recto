"""从模型输出里抠一个 JSON 对象 (容错: 围栏 / 前后废话 / 截断)。

三份 skill 都要求"严格 JSON, 无围栏", 但模型并不总是听话。这里做三件事:

  1. 剥 ``` 围栏
  2. 从第一个 ``{`` 到最后一个 ``}`` 取一段试解析
  3. 失败则逐个 ``{...}`` 抠 (取**最大的那个能解析的**) —— 写故事时正文里
     可能带花括号, 所以不取第一个

解析不出来就返回 ``None``, 由调用方决定"退回保底规则"还是"这一回合算失败"。
"""
from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


def _strip(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = _FENCE_RE.sub("", t)
    return t.strip()


def parse_obj(text: str) -> dict[str, Any] | None:
    t = _strip(text)
    if not t:
        return None
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        try:
            data = json.loads(t[i:j + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    # 退化: 逐段抠, 取最大的一份
    best: dict[str, Any] | None = None
    for m in re.finditer(r"\{.*?\}", t, re.S):
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and (best is None or len(data) > len(best)):
            best = data
    return best


def as_int(v: Any, default: int = 0, lo: int | None = None, hi: int | None = None) -> int:
    """宽松取整数 (字符串 / 浮点 / 坏值都收)。"""
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        n = default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n


def as_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def as_str(v: Any, limit: int = 0) -> str:
    s = v if isinstance(v, str) else ("" if v is None else str(v))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit] if limit else s


def as_text(v: Any, limit: int = 0) -> str:
    """取**保留段落**的文本 (故事正文用)。

    为什么不能复用 :func:`as_str`: 它把连续空白压成一个空格 —— 写故事时正文
    用两个换行分段, 压完就成了一整块 (UI 里看不出段落, 读着累)。
    这里只做归一: 统一换行符、去行尾空格、最多保留一个空行。
    """
    s = v if isinstance(v, str) else ("" if v is None else str(v))
    # 模型偶尔把分段写成**字面**的 \n (JSON 里被双重转义), 于是整篇没有真换行,
    # 页面上就成了一整块 —— 2026-11-13 第 8 回实测 86 处字面 \n、0 个真换行。
    # 中文正文里不会自然出现字面 \n, 所以这是安全的兜底 (有真换行就不动)。
    if "\n" not in s and s.count(chr(92) + "n") >= 3:
        s = (s.replace(chr(92) + "r" + chr(92) + "n", "\n")
             .replace(chr(92) + "n", "\n")
             .replace(chr(92) + "t", "\t"))
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = "\n".join(line.rstrip() for line in s.split("\n"))
    while "\n\n\n" in s:
        s = s.replace("\n\n\n", "\n\n")
    s = s.strip()
    return s[:limit] if limit else s


def as_list(v: Any, limit: int = 8, each: int = 24) -> list[str]:
    """宽松取字符串数组 (也接受逗号分隔的一串)。"""
    if isinstance(v, str):
        v = re.split(r"[,，、;；]\s*", v)
    if not isinstance(v, (list, tuple)):
        return []
    out: list[str] = []
    for it in v:
        s = as_str(it.get("term") if isinstance(it, dict) else it, each)
        if s and s not in out:
            out.append(s)
        if len(out) >= limit:
            break
    return out
