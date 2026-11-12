"""书宠简要 —— 阅读器右下角小图标要的两行字。

数据源 = 最新一回游历 (``store.adventure``)。**不调模型**: 摘要是确定性的
(标题 + 开头两句, 截到 ``LINE_MAX`` 字), 每打开一本书算一遍也不花钱、不等网络。

    {"ok": true, "id": 5, "no": 11, "at": "2026-11-12T14:06:56",
     "title": "第十一回 · 石阶上的半张符", "place": "云上城后山",
     "lines": ["第十一回 · 石阶上的半张符", "云阶很长，石缝里长满青苔。…"],
     "day": 10, "day_no": 11, "age": 12, "name": "小墨"}

``id`` 是"有没有更新"的唯一依据: 前端把它记进 localStorage,
变大了才让图标慢慢转一下; 读者点开过就把新的 id 记下来。
"""
from __future__ import annotations

import re
from typing import Any

from recto.pet import charsheet as cs
from recto.pet import life as life_mod
from recto.pet import store

LINE_MAX = 46

# 句末标点 (中文 + 英文); 引号跟着前一句走, 不在引号处断
_END = "。！？…；!?;"
_TAIL = "”’」』）)》"


def _flat(s: str) -> str:
    """压空白: 故事正文一段一行, 悬停框里不该出现换行。"""
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _clip(s: str, n: int = LINE_MAX) -> str:
    s = _flat(s)
    if len(s) <= n:
        return s
    return s[:n].rstrip("，、,;； ") + "…"


def _lead(body: str, n: int = LINE_MAX, max_sent: int = 2) -> str:
    """正文开头取 1~2 句, 总长不超过 n 字 (不切在半句上, 除非第一句就超长)。"""
    txt = _flat(body)
    if not txt:
        return ""
    out, i, taken = [], 0, 0
    while i < len(txt) and len(out) < max_sent:
        j = i
        while j < len(txt):
            if txt[j] in _END:
                k = j + 1
                while k < len(txt) and txt[k] in _TAIL:
                    k += 1
                j = k
                break
            j += 1
        else:
            j = len(txt)
        seg = txt[i:j].strip()
        if seg and taken + len(seg) > n and out:
            break
        out.append(seg)
        taken += len(seg)
        i = j
    return _clip("".join(out), n)


def digest(adv: dict[str, Any], *, name: str = "", life: dict[str, Any] | None = None) -> dict[str, Any]:
    """把一行 adventure 变成两行摘要 (纯函数, 好测)。"""
    title = _flat(adv.get("title")) or _flat(adv.get("place")) or "无题"
    body = _lead(adv.get("body") or "")
    lines = [title]
    if body and body != title:
        lines.append(body)
    life = life or {}
    return {
        "ok": True,
        "id": int(adv.get("id") or 0),
        "no": int(adv.get("no") or 0),
        "at": adv.get("at") or "",
        "title": title,
        "place": _flat(adv.get("place")),
        "lines": lines[:2],
        "day": int(life.get("day") or 0),
        "day_no": int(life.get("day_no") or 0),
        "age": int(life.get("age") or 0),
        "name": name,
    }


def latest() -> dict[str, Any]:
    """最新一回的两行摘要。没有游历 (或库还没建) → ``{"ok": False}``。"""
    try:
        c = store.conn()
        row = store.one(c, "SELECT id, no, at, title, place, body FROM adventure"
                           " ORDER BY id DESC LIMIT 1")
        c.close()
    except Exception:  # noqa: BLE001 — 小图标不该因为库没建好就把阅读页搞坏
        return {"ok": False, "lines": []}
    if not row:
        return {"ok": False, "lines": []}
    try:
        ch = cs.load_char()
        name = str(ch.get("name") or "")
        life = life_mod.view(ch)
    except Exception:  # noqa: BLE001
        name, life = "", {}
    adv = {"id": row.get("id"), "no": row.get("no"), "at": row.get("at"),
           "title": row.get("title"), "place": row.get("place"), "body": row.get("body")}
    return digest(adv, name=name, life=life)
