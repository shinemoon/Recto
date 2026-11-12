"""书籍知识储备 —— 书宠对每本读过的书的"底细"。

**要解决的偏心**: 书宠的成长与冒险都要从书里长出来, 但模型对一本书的认识
经常是糊的 (生僻书 / 新书 / 译名不同)。所以每本它碰过的书先立一张档案卡:

  1. **网络检索一次** (标题 + 作者), 拿到几条结果当凭据 (``websearch``)
  2. 检索 + 模型常识 → 一张卡 (定位 / 主题 / 设定 / 概要 / 母题), 存 ``lore_book``
  3. 人**打开过的章**再补一条章级储备 (``lore_chapter``): 这章讲了什么、有什么意象
     —— 依据是 ``chapter_seen.excerpt`` 里那一段原文, 不是凭空想

**检索是可选的**: 网络不通时 ``searched="off"``, 模型照旧凭常识写卡,
但 ``confidence`` 会低, 冒险那道工序也就不会把"设定"当成硬事实。

**节流**: 一张卡 30 天内不重建 (``REFRESH_DAYS``); 章节储备一次不建已有卡。
所有 LLM 失败都只记日志, 不抛 —— 后台心跳里抛异常等于这条线自己死了。
"""
from __future__ import annotations

import logging
import time
from typing import Any

from recto.core import clock
from recto.pet import config as petcfg
from recto.pet import guidance, jsonout, store, websearch

log = logging.getLogger("recto.pet.lore")

REFRESH_DAYS = 30.0
CHAT_TIMEOUTS = 60.0


# ------------------------------------------------------------------ LLM

def _llm() -> dict[str, Any] | None:
    """取书宠实际生效的模型配置 (本分区没填就借用语料那套)。

    没配就返回 None, 调用方走"不建卡 / 走保底规则"的路 —— 建卡失败不该让
    后台心跳炸。
    """
    try:
        llm = petcfg.effective_llm()
    except Exception as e:  # noqa: BLE001
        log.warning("知识储备 · 读配置失败: %s", e)
        return None
    if not llm.get("base_url") or not llm.get("model"):
        return None
    return dict(llm)


# 上一次失败的真因 (给调用方拼人话用)。空串 = 上一次成功。
# 以前这里只返回 None, 上层一律说"模型不可用" —— 用户配的是 MiniMax-M3 (推理型),
# 真因其实是 "输出被截断 (思考把 max_tokens 吃完了)", 看不出该动哪个参数。
LAST_ERROR = ""


def chat(system: str, user: str, *, max_tokens: int = 900,
         temperature: float = 0.3) -> str | None:
    global LAST_ERROR
    from recto.core import llm as llmmod

    cfg = _llm()
    if not cfg:
        LAST_ERROR = "没有可用的模型配置 (端点或型号没填)"
        return None
    cfg = dict(cfg)
    cfg["timeout"] = min(float(cfg.get("timeout") or CHAT_TIMEOUTS), CHAT_TIMEOUTS)

    mt = int(max_tokens)
    try:
        # 推理型模型 (MiniMax-M3 / o1 / r1 …) 的思考也吃 max_tokens。同一份契约
        # 提示词, 普通模型 700 够用, 推理模型光思考就能烧掉一千多 → 正文空 →
        # 表现成"模型不可用"。给 3 倍预算 (封顶 4096), 关不掉思考也能拿到正文。
        if llmmod.looks_reasoning_model(str(cfg.get("model") or ""),
                                       str(cfg.get("base_url") or "")):
            mt = min(4096, max(mt, mt * 3))
    except Exception:  # noqa: BLE001 — 识别不出来就按原预算
        pass

    try:
        out = llmmod.chat(cfg, system, user, temperature=temperature,
                          max_tokens=mt, retries=1)
        LAST_ERROR = ""
        return out
    except Exception as e:  # noqa: BLE001 — 建卡失败不该让后台心跳炸
        LAST_ERROR = f"{type(e).__name__}: {e}"[:220]
        log.warning("书宠 · LLM 失败 (max_tokens=%d): %s", mt, e)
        return None


# ------------------------------------------------------------------ 书信息

def book_meta(book_key: str) -> dict[str, Any]:
    """书名 / 作者: 先问 reader_state (书还在), 再问 lore_book (书已删但卡还在)。"""
    out = {"key": book_key, "title": "", "author": ""}
    try:
        from recto.services import reader_state as rs

        b = rs.get_book(book_key) or {}
        out["title"] = str(b.get("title") or "")
        out["author"] = str(b.get("author") or "")
    except Exception:  # noqa: BLE001
        pass
    if out["title"]:
        return out
    try:
        c = store.conn()
        row = store.one(c, "SELECT title, author FROM lore_book WHERE book=?", (book_key,))
        c.close()
        if row:
            out["title"], out["author"] = row["title"], row["author"]
    except Exception:  # noqa: BLE001
        pass
    return out


def _age_days(ts: str) -> float:
    t = clock.parse(ts)
    if t is None:
        return 1e9
    return max(0.0, (time.time() - t.timestamp()) / 86400.0)


# ------------------------------------------------------------------ 建卡

def build_book(book_key: str, *, force: bool = False,
               search_mode: str = "auto") -> dict[str, Any] | None:
    """为一本书立卡 (已有且不旧 → 原样返回)。

    ``search_mode``: ``auto`` 用 ``[pet] search`` 的配置; ``off`` 不联网; ``on`` 强制。
    """
    meta = book_meta(book_key)
    if not meta["title"]:
        return None
    try:
        c = store.conn()
        row = store.one(c, "SELECT * FROM lore_book WHERE book=?", (book_key,))
    except Exception as e:  # noqa: BLE001
        log.warning("知识储备 · 读库失败: %s", e)
        return None
    if row and not force and _age_days(row.get("updated_at") or "") < REFRESH_DAYS:
        c.close()
        return _row_view(row)
    card: dict[str, Any] | None = None
    searched: dict[str, Any] = {"status": "off", "results": []}
    want_search = search_mode
    if want_search == "auto":
        try:
            want_search = "on" if bool((petcfg.load_config().get("chronicle") or {}).get("search", True)) else "off"
        except Exception:  # noqa: BLE001
            want_search = "on"
    if want_search == "on":
        q = f'{meta["title"]} {meta["author"]} 书籍 简介 主题'.strip()
        searched = websearch.search(q, limit=5)

    payload = {
        "title": meta["title"],
        "author": meta["author"],
        "search": {"enabled": want_search == "on", "status": searched.get("status"),
                   "results": [{"title": r.get("title"), "snippet": r.get("snippet")}
                               for r in (searched.get("results") or [])]},
    }
    import json as _json

    user = ("为下面这本书立一张档案卡。严格按 lore.md 的 JSON 契约回答。\n\n"
            + _json.dumps(payload, ensure_ascii=False, indent=1))
    out = chat(guidance.render("lore"), user, max_tokens=900, temperature=0.3)
    data = jsonout.parse_obj(out) if out else None
    if data:
        card = {
            "identity": jsonout.as_str(data.get("identity"), 40),
            "themes": jsonout.as_list(data.get("themes"), 6, 8),
            "setting": jsonout.as_str(data.get("setting"), 160),
            "summary": jsonout.as_str(data.get("summary"), 300),
            "moods": jsonout.as_list(data.get("moods"), 4, 8),
            "hooks": jsonout.as_list(data.get("hooks"), 3, 30),
            "confidence": max(0.0, min(1.0, jsonout.as_float(data.get("confidence"), 0.3))),
        }
    elif row:
        # LLM 不可用 → 保留旧卡, 只把 updated_at 推一下, 免得每轮都重试
        card = _row_view(row)
        card["_reused"] = True

    if not card:
        c.close()
        return None

    now = clock.iso_local()
    src = "search+model" if searched.get("status") == "ok" else "model"
    c.execute(
        "INSERT INTO lore_book (book, title, author, identity, themes, setting, summary,"
        " source, searched, confidence, fetched_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(book) DO UPDATE SET title=excluded.title, author=excluded.author,"
        " identity=excluded.identity, themes=excluded.themes, setting=excluded.setting,"
        " summary=excluded.summary, source=excluded.source, searched=excluded.searched,"
        " confidence=excluded.confidence, fetched_at=excluded.fetched_at,"
        " updated_at=excluded.updated_at",
        (book_key, meta["title"], meta["author"], card["identity"],
         _json.dumps(card["themes"], ensure_ascii=False),
         card["setting"], card["summary"], src, str(searched.get("status") or ""),
         float(card["confidence"]), now, now))
    c.commit()
    row = store.one(c, "SELECT * FROM lore_book WHERE book=?", (book_key,))
    c.close()
    if row:
        log.info("知识储备 · 立卡 %s · 检索 %s · 主题 %s",
                 meta["title"], searched.get("status"), "、".join(card["themes"][:3]))
    # moods / hooks 没进库 (表里没有专列) → 顺手挂在返回里给冒险用
    view = _row_view(row or {})
    view["moods"] = card.get("moods") or []
    view["hooks"] = card.get("hooks") or []
    return view


def build_chapter(book_key: str, chapter: int, *, force: bool = False) -> dict[str, Any] | None:
    """为一章补"典故储备" (依据 = chapter_seen 里那一段原文)。"""
    try:
        c = store.conn()
        seen = store.one(c, "SELECT * FROM chapter_seen WHERE book=? AND chapter=?",
                         (book_key, int(chapter)))
        if not seen:
            c.close()
            return None
        row = store.one(c, "SELECT * FROM lore_chapter WHERE book=? AND chapter=?",
                        (book_key, int(chapter)))
        if row and not force and _age_days(row.get("fetched_at") or "") < REFRESH_DAYS:
            c.close()
            return _chapter_view(row)
    except Exception as e:  # noqa: BLE001
        log.warning("知识储备 · 读章节失败: %s", e)
        return None

    meta = book_meta(book_key)
    import json as _json

    payload = {
        "title": meta["title"], "author": meta["author"],
        "chapter_title": seen.get("title") or "",
        "chapter_no": int(chapter) + 1,
        "excerpt": (seen.get("excerpt") or "")[:1400],
        "task": "为这一章写章节储备 (lore.md 的章节契约)",
    }
    user = ("按 lore.md 的章节契约, 只依据下面这段章首节选回答。\n\n"
            + _json.dumps(payload, ensure_ascii=False, indent=1))
    out = chat(guidance.render("lore"), user, max_tokens=500, temperature=0.3)
    data = jsonout.parse_obj(out) if out else None
    if not data:
        c.close()
        return None
    gist = jsonout.as_str(data.get("gist"), 200)
    motifs = jsonout.as_list(data.get("motifs"), 5, 10)
    now = clock.iso_local()
    c.execute(
        "INSERT INTO lore_chapter (book, chapter, title, gist, motifs, fetched_at)"
        " VALUES (?,?,?,?,?,?)"
        " ON CONFLICT(book, chapter) DO UPDATE SET title=excluded.title,"
        " gist=excluded.gist, motifs=excluded.motifs, fetched_at=excluded.fetched_at",
        (book_key, int(chapter), str(seen.get("title") or ""), gist,
         _json.dumps(motifs, ensure_ascii=False), now))
    c.commit()
    row = store.one(c, "SELECT * FROM lore_chapter WHERE book=? AND chapter=?",
                    (book_key, int(chapter)))
    c.close()
    return _chapter_view(row or {})


def pending_chapters(book_key: str, limit: int = 3) -> list[dict[str, Any]]:
    """人打开过、但还没写储备的章 (新读的优先)。"""
    try:
        c = store.conn()
        rows = store.rows(
            c,
            "SELECT s.book, s.chapter, s.title FROM chapter_seen s"
            " LEFT JOIN lore_chapter l ON l.book=s.book AND l.chapter=s.chapter"
            " WHERE s.book=? AND l.book IS NULL ORDER BY s.last_at DESC LIMIT ?",
            (book_key, int(limit)))
        c.close()
        return rows
    except Exception:  # noqa: BLE001
        return []


# ------------------------------------------------------------------ 视图

def _row_view(row: dict[str, Any]) -> dict[str, Any]:
    import json as _json

    def _j(v):
        try:
            return _json.loads(v or "[]")
        except Exception:  # noqa: BLE001
            return []

    return {
        "book": row.get("book"), "title": row.get("title"), "author": row.get("author"),
        "identity": row.get("identity") or "", "themes": _j(row.get("themes")),
        "setting": row.get("setting") or "", "summary": row.get("summary") or "",
        "source": row.get("source") or "", "searched": row.get("searched") or "",
        "confidence": float(row.get("confidence") or 0),
        "fetched_at": row.get("fetched_at") or "", "updated_at": row.get("updated_at") or "",
    }


def _chapter_view(row: dict[str, Any]) -> dict[str, Any]:
    import json as _json

    try:
        motifs = _json.loads(row.get("motifs") or "[]")
    except Exception:  # noqa: BLE001
        motifs = []
    return {"book": row.get("book"), "chapter": row.get("chapter"),
            "title": row.get("title") or "", "gist": row.get("gist") or "",
            "motifs": motifs, "fetched_at": row.get("fetched_at") or ""}


def card(book_key: str, chapters: int = 5) -> dict[str, Any]:
    """给页面看的一张卡 (书 + 最近几章的储备)。"""
    try:
        c = store.conn()
        row = store.one(c, "SELECT * FROM lore_book WHERE book=?", (book_key,))
        chs = store.rows(
            c, "SELECT * FROM lore_chapter WHERE book=? ORDER BY chapter DESC LIMIT ?",
            (book_key, int(chapters)))
        seen = store.one(c, "SELECT COUNT(*) n FROM chapter_seen WHERE book=?", (book_key,))
        c.close()
    except Exception:  # noqa: BLE001
        return {"book": book_key, "missing": True}
    if not row:
        return {"book": book_key, "missing": True,
                "chapters_seen": int((seen or {}).get("n") or 0)}
    out = _row_view(row)
    out["missing"] = False
    out["chapters"] = [_chapter_view(r) for r in chs]
    out["chapters_seen"] = int((seen or {}).get("n") or 0)
    out["chapters_lore"] = len(chs)
    return out


def all_cards(limit: int = 60) -> list[dict[str, Any]]:
    """所有已立卡的书 (页面列表用)。"""
    try:
        c = store.conn()
        rows = store.rows(c, "SELECT * FROM lore_book ORDER BY updated_at DESC LIMIT ?",
                          (int(limit),))
        chs = store.rows(c, "SELECT book, COUNT(*) n FROM lore_chapter GROUP BY book")
        c.close()
    except Exception:  # noqa: BLE001
        return []
    counts = {r["book"]: int(r["n"] or 0) for r in chs}
    out = []
    for r in rows:
        v = _row_view(r)
        v["chapters"] = counts.get(r.get("book"), 0)
        out.append(v)
    return out


def stats() -> dict[str, Any]:
    try:
        c = store.conn()
        b = store.one(c, "SELECT COUNT(*) n, AVG(confidence) conf, MAX(updated_at) last"
                         " FROM lore_book") or {}
        ch = store.one(c, "SELECT COUNT(*) n FROM lore_chapter") or {}
        seen = store.one(c, "SELECT COUNT(*) n FROM chapter_seen") or {}
        c.close()
    except Exception:  # noqa: BLE001
        return {"books": 0, "chapters": 0, "seen": 0, "avg_confidence": 0, "last": ""}
    return {"books": int(b.get("n") or 0), "chapters": int(ch.get("n") or 0),
            "seen": int(seen.get("n") or 0),
            "avg_confidence": round(float(b.get("conf") or 0), 2),
            "last": b.get("last") or ""}
