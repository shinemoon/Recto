"""故事 —— 一条连续的「它走过的路」。

两套视图, 同一份数据:

  * ``chapters()``  按回组织 (后台口径: 第几回) —— 轻量, 只要正文
  * ``archive()``   按回组织 + 完整数据 (交手 / 判定 / 来处) —— 档案页用
  * ``flatten()``   摊平成一条连续的段落流 —— 前台读的那条路

**为什么摊平**: 读者看的是「它走过的路」, 不是「第几回第几章」。
章节编号是**后台**的事 —— 要按回查, 去档案页 (``/pet/story/``);
正文里不该有。前台只要一条往下滚的正文 (MUD 那样的), 空格推一段。

**段落切分在后台做**: 前端不再自己 split —— 位置 (读到第几段) 是
跨端共用的索引, 两边各切一次迟早错位。段落 = 空行分隔 (``\\n\\n``)；
``jsonout.as_text`` 已经把「字面 \\n」和连续空行归一过, 这里直接用。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from recto.pet import chronicle
from recto.pet import jsonout, store

log = logging.getLogger("recto.pet.story")

#: 标题里那一段「第N回」的后台编号 —— 前台不显示, 档案页自己按 no 拼。
_NO_RE = re.compile(r"^\s*第\s*[0-9０-９一二三四五六七八九十百千零〇两]+\s*回\s*[·・.:：、\-—~]?\s*")

#: 摊平后的默认上限 (后台保留条数是 200, 这里留一点余量)
DEFAULT_LIMIT = 200


def _jdict(v: Any) -> dict[str, Any]:
    try:
        d = json.loads(v or "{}")
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def clean_title(title: str) -> str:
    """把标题里的「第N回 · 」剥掉 (新写的故事本来就不带)。

    旧记录 (2026-11-19 之前) 的标题都带这个前缀, 档案页要按回显示,
    正文流里则不该出现 —— 这里剥一次, 两边各取所需。
    """
    s = str(title or "").strip()
    return _NO_RE.sub("", s).strip() or s


def split_paras(body: str) -> list[str]:
    """正文 → 段落数组 (空行分段)。"""
    text = jsonout.as_text(body or "")
    out: list[str] = []
    for chunk in re.split(r"\n{2,}", text):
        c = chunk.strip()
        if c:
            out.append(c)
    return out


def chapters(limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """按回组织的轻量列表 (前台滚动用)。

    轻 = 不带交手 / 判定 / 来处那些大件 (那套在档案页)。
    但**类型徽标**要留 (斗 / 会 / 观 / 学 / 救 / 行) —— 它说明这一趟
    是什么事, 是读者最想知道的一句。
    """
    try:
        c = store.conn()
        rows = store.rows(c, "SELECT * FROM adventure ORDER BY id ASC LIMIT ?",
                          (int(limit),))
        c.close()
    except Exception as e:  # noqa: BLE001 — 读不到就当没有故事, 别拖垮页面
        log.warning("故事 · 读取失败: %s", e)
        return []
    out: list[dict[str, Any]] = []
    for r in rows:
        g = _jdict(r.get("growth"))
        body = r.get("body") or ""
        paras = split_paras(body)
        if not paras:
            continue
        out.append({
            "id": int(r.get("id") or 0),
            "no": int(r.get("no") or 0),
            "at": r.get("at") or "",
            "title": clean_title(r.get("title") or ""),
            "place": r.get("place") or "",
            "paras": paras,
            "kind": g.get("kind") or "",
            "kind_cn": g.get("kind_cn") or "",
            "kind_why": g.get("kind_why") or "",
        })
    return out


def archive(limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """按回组织的**完整**列表 (档案页): 正文 + 交手 / 判定 / 来处。

    走 ``chronicle.adventures()`` —— 那里面已经把老记录缺失的
    「交手有没有写进正文」两面旗补算过, 档案页要的就是那份。
    """
    rows = chronicle.adventures(limit=int(limit))
    out: list[dict[str, Any]] = []
    for a in reversed(rows):            # adventures() 是倒序 (新的在前), 档案页按时间正序
        paras = split_paras(a.get("body") or "")
        out.append({
            "id": int(a.get("id") or 0),
            "no": int(a.get("no") or 0),
            "at": a.get("at") or "",
            "title": clean_title(a.get("title") or ""),
            "place": a.get("place") or "",
            "paras": paras,
            "themes": a.get("themes") or [],
            "terms": a.get("terms") or [],
            "growth": a.get("growth") or {},
        })
    return out


def flatten(chapters_: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按回组织的列表 → 一条连续的段落流。

    每段带它属于哪一回 (``no``) 与段序 (``pi``) —— 前台要用它把
    「读到第几段」换算回「这一回读到第几段」(档案页要显示)。
    """
    out: list[dict[str, Any]] = []
    for ci, ch in enumerate(chapters_ or []):
        for pi, text in enumerate(ch.get("paras") or []):
            out.append({"ci": ci, "pi": pi, "no": int(ch.get("no") or 0),
                        "text": text})
    return out


def legacy_global_index(legacy: dict[str, int],
                        chapters_: list[dict[str, Any]]) -> int:
    """老位置 (按回记的段数) → 全局段序号。

    老格式记的是"第 no 回读到第 para 段"。换成全局序号 = 前面所有回
    的总段数 + 本回段序。

    **只取最大的那个**: 读者可能同时开着好几回 (老格式允许多回各记各的),
    取最靠前的那个作为"读到哪儿了" —— 保守 (宁可少读, 不跳过)。
    """
    if not legacy or not chapters_:
        return 0
    best = 0
    for ch in chapters_:
        no = int(ch.get("no") or 0)
        para = int(legacy.get(str(no)) or 0)
        if para <= 0:
            continue
        before = 0
        for other in chapters_:
            if int(other.get("id") or 0) < int(ch.get("id") or 0):
                before += len(other.get("paras") or [])
        idx = before + min(para, len(ch.get("paras") or []))
        best = max(best, idx)
    return best