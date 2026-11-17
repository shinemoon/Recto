"""阅读 → 化身 —— 把"读到的东西"化成书宠世界里能用的东西。

**为什么要有这一步 (2026-11-17)**: 在这之前, 写故事那道工序只拿到两样东西:
整本书的抽象种子 (母题 / 意象, 而且同一本书一百回都是同一份) 和语料的
**类别计数** (``person 1663``)。于是故事跟"这段时间真读到什么"几乎没有关系 ——
读者看到的是"一只书宠又在江湖上走了一趟", 而不是"这是我刚读的那本书长出来的"。

这一步补的就是那道缺口, 但要守住原来那条底线 (**不许搬用别人书里的名字**):

    别人书里的名字  →  [本模块: 一次联想]  →  它世界里的人和物
    陈平安 1663 次   →      化身一        →  "一个把旧誓背在身上走了很久的人"

**分界**:
  · 本模块是**唯一**允许把书名 / 人名 / 章名 / 语料原词摆在模型眼前的工序 ——
    因为不看真东西, 联想只能空转。
  · 产出一律过 ``proper`` 的禁用词表: 命中专名的那一条**整条丢弃**
    (宁可少一条, 不许把名字漏进下一道工序)。
  · 下一道工序 (写故事) 拿到的只有化身过的东西, 一个字的名字都没有。

**成本**: 一回合多一次模型调用, 但有缓存 —— 同一批语料 (同窗口、同章) 六小时内
不重算; 语料是"这段时间新撞见的", 半个下午通常只算一次。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from recto.core import clock
from recto.pet import encounters, guidance, jsonout, proper, store

log = logging.getLogger("recto.pet.spark")

#: 缓存多久有效 (小时)。语料按天累积, 半小时一回合也没必要每回都重算。
TTL_HOURS = 6.0
#: 给模型的语料词上限 (太多会挤掉书的信息)
MAX_TERMS = 30
#: 一本书给几章的开头 (读到哪一章最有"现场感")
MAX_CHAPTERS = 3


# ------------------------------------------------------------------ 材料

def _chapters_of(book_key: str, upto: int, limit: int = MAX_CHAPTERS) -> list[dict[str, Any]]:
    """这本书**最近读到的**几章 (标题 + 开头一段)。

    章是这一回故事最具体的依据 —— 书卡说的是"整本是什么", 章说的是"读到哪儿了"。
    """
    try:
        c = store.conn()
        rows = store.rows(
            c,
            "SELECT chapter, title, excerpt FROM chapter_seen WHERE book=?"
            " ORDER BY chapter DESC LIMIT ?", (book_key, int(limit)))
        c.close()
    except Exception as e:  # noqa: BLE001
        log.warning("化身 · 读章节足迹失败: %s", e)
        return []
    out = []
    for r in rows:
        if upto and int(r["chapter"] or 0) > upto:
            continue
        exc = str(r.get("excerpt") or "").strip()
        out.append({"chapter": int(r["chapter"] or 0),
                    "title": str(r.get("title") or ""),
                    "excerpt": exc[:400]})
    return out


def materials(ev: dict[str, Any]) -> dict[str, Any]:
    """把这一回合的阅读摊成给模型看的材料 (含真名字, 只在本模块内流转)。"""
    books: list[dict[str, Any]] = []
    for r in (ev.get("reading") or [])[:5]:
        card = r.get("lore") or {}
        seeds = card.get("seeds") if isinstance(card.get("seeds"), dict) else {}
        bkey = str(r.get("book") or "")
        per = []
        try:
            per = encounters.for_book(bkey, days=7, limit=12) if bkey else []
        except Exception:  # noqa: BLE001
            per = []
        books.append({
            "title": r.get("title") or "",
            "author": r.get("author") or "",
            "minutes": int(r.get("minutes") or 0),
            "chapters_read": int(r.get("chapters") or 0),
            "percent_to": r.get("percent_to"),
            "identity": card.get("identity") or "",
            "themes": list(card.get("themes") or [])[:6],
            "setting": str(card.get("setting") or "")[:200],
            "summary": str(card.get("summary") or "")[:300],
            "seeds": {k: seeds.get(k) for k in ("motif", "image", "weather", "texture", "conflict")
                      if seeds.get(k)} or {},
            "chapters": _chapters_of(bkey, int(r.get("chapter_to") or 0)),
            "terms": [{"term": t["term"], "cat": t["cat"], "count": int(t["count"] or 0)}
                      for t in per[:12]],
        })
    enc = ev.get("encounters") or {}
    terms = [{"term": str(t.get("term") or ""), "cat": str(t.get("cat") or ""),
              "count": int(t.get("count") or 0)}
             for t in (enc.get("terms") or [])[:MAX_TERMS] if t.get("term")]
    return {"window": ev.get("window") or {}, "books": books, "terms": terms,
            "cats": enc.get("cats") or {}}


def _sig(mat: dict[str, Any]) -> str:
    """同一批材料 → 同一个指纹 (缓存键)。"""
    books = []
    for b in mat.get("books") or []:
        books.append([b.get("title"), b.get("percent_to"),
                      [c.get("chapter") for c in (b.get("chapters") or [])]])
    terms = [[t.get("term"), t.get("count")] for t in (mat.get("terms") or [])[:20]]
    raw = json.dumps([books, terms], ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------ 清洗

def _keys_of(v: Any, bad: dict[str, str]) -> list[str]:
    """``keys`` 只留普通词 (含禁用词的丢掉) —— 它们要拿去核对故事正文。"""
    out: list[str] = []
    for k in jsonout.as_list(v, 4, 8):
        k = k.strip()
        if len(k) < 2 or len(k) > 8:
            continue
        if k in bad or any(w in k for w in bad):
            continue
        if k not in out:
            out.append(k)
    return out[:3]


def _clean(data: dict[str, Any] | None) -> dict[str, Any]:
    """夹取 + **去专名**: 命中禁用词的条目整条丢弃 (不替换, 替换会把句子弄坏)。"""
    if not isinstance(data, dict):
        return {}
    bad = proper.forbidden()
    echo: list[dict[str, Any]] = []
    dropped = 0
    for raw in (data.get("echo") or [])[:6]:
        if not isinstance(raw, dict):
            continue
        trace = jsonout.as_str(raw.get("trace"), 40)
        image = jsonout.as_str(raw.get("image"), 30)
        keys = _keys_of(raw.get("keys"), bad)
        if not trace or not keys:
            continue
        if proper.check(trace + image + "".join(keys), bad):
            dropped += 1
            continue
        echo.append({"cat": jsonout.as_str(raw.get("cat"), 10) or "other",
                     "trace": trace, "image": image, "keys": keys})
    twists: list[str] = []
    for raw in (data.get("twists") or [])[:4]:
        s = jsonout.as_str(raw, 60)
        if s and not proper.check(s, bad):
            twists.append(s)
        elif s:
            dropped += 1
    scene_in = data.get("scene") if isinstance(data.get("scene"), dict) else {}

    def _s(key: str, n: int) -> str:
        s = jsonout.as_str(scene_in.get(key), n)
        return "" if (s and proper.check(s, bad)) else s

    out = {
        "echo": echo,
        "twists": twists[:3],
        "scene": {"weather": _s("weather", 30), "place": _s("place", 40),
                  "texture": _s("texture", 40)},
        "motif": [s for s in jsonout.as_list(data.get("motif"), 5, 12)
                  if not proper.check(s, bad)],
        "image": [s for s in jsonout.as_list(data.get("image"), 5, 12)
                  if not proper.check(s, bad)],
        "_dropped": dropped,
    }
    if not out["echo"]:
        # 一条都没剩 = 这一回等于没做 (多半是模型把名字写进了每条)。
        # 宁可按"没有化身"走旧路, 也不给半截。
        return {}
    return out


# ------------------------------------------------------------------ 缓存

def _cache_get(sig: str) -> dict[str, Any] | None:
    try:
        c = store.conn()
        row = store.one(c, "SELECT at, payload FROM spark WHERE sig=?", (sig,))
        c.close()
    except Exception as e:  # noqa: BLE001
        log.warning("化身 · 读缓存失败: %s", e)
        return None
    if not row:
        return None
    try:
        t = clock.parse(str(row.get("at") or ""))
        if not t or (time.time() - t.timestamp()) > TTL_HOURS * 3600:
            return None
        data = json.loads(str(row.get("payload") or "{}"))
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) and data.get("echo") else None


def _cache_put(sig: str, data: dict[str, Any]) -> None:
    try:
        c = store.conn()
        c.execute("INSERT INTO spark (sig, at, payload) VALUES (?,?,?)"
                  " ON CONFLICT(sig) DO UPDATE SET at=excluded.at, payload=excluded.payload",
                  (sig, clock.iso_local(), json.dumps(data, ensure_ascii=False)))
        c.execute("DELETE FROM spark WHERE at < ?",
                  (clock.fmt_sec(time.time() - 7 * 86400, "%Y-%m-%dT%H:%M:%S"),))
        c.commit()
        c.close()
    except Exception as e:  # noqa: BLE001
        log.warning("化身 · 写缓存失败: %s", e)


# ------------------------------------------------------------------ 入口

def build(ev: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    """这一回合的化身。模型不可用 / 全被过滤 → 空 dict (走旧路, 不挡推演)。"""
    from recto.pet import lore

    mat = materials(ev)
    if not mat.get("books"):
        return {}
    sig = _sig(mat)
    if not force:
        hit = _cache_get(sig)
        if hit:
            hit["_cached"] = True
            return hit
    user = ("把这批阅读材料化成书宠世界里能用的东西。严格按 spark.md 的 JSON 契约回答。"
            "\n\n" + json.dumps(mat, ensure_ascii=False, indent=1))
    out = lore.chat(guidance.render("spark"), user, max_tokens=1100, temperature=0.8)
    data = jsonout.parse_obj(out) if out else None
    clean = _clean(data)
    if not clean:
        why = lore.LAST_ERROR or "模型没有给出可用的 JSON"
        log.info("化身 · 这一轮没成 (%s), 按无化身继续", why)
        return {}
    clean["_sig"] = sig
    clean["_at"] = clock.iso_local()
    _cache_put(sig, {k: v for k, v in clean.items() if not k.startswith("_")})
    if clean.get("_dropped"):
        log.info("化身 · 丢弃 %d 条 (含专名)", clean["_dropped"])
    log.info("化身 · %d 条来处 · %d 条走向 · 语料 %d 词",
             len(clean.get("echo") or []), len(clean.get("twists") or []),
             len(mat.get("terms") or []))
    return clean


def cover(body: str, spark: dict[str, Any]) -> dict[str, Any]:
    """故事正文落地了几条来处 (拿 ``keys`` 字面核对, 给闸门用)。

    判据故意粗: 一条来处里**任一个 key 出现**就算这一条写了。写故事的人不会
    逐字照抄 ``trace``, 但既然让它在故事里成为一个人 / 一处地, 那几个词总该露面。
    """
    body = body or ""
    echo = [e for e in (spark or {}).get("echo") or [] if e.get("keys")]
    hit: list[int] = []
    for i, e in enumerate(echo):
        if any(str(k) in body for k in e.get("keys") or []):
            hit.append(i)
    return {"used": len(hit), "total": len(echo), "idx": hit,
            "enough": len(hit) >= min(2, len(echo))}


def brief(spark: dict[str, Any], limit: int = 3) -> list[dict[str, Any]]:
    """给故事的**精简**版来处 (页面用完整版, 模型用这个)。"""
    out = []
    for e in (spark or {}).get("echo") or []:
        if len(out) >= limit:
            break
        out.append({"cat": e.get("cat"), "trace": e.get("trace")})
    return out
