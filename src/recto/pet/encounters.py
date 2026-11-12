"""语料遭遇 —— 书宠"这段时间在正文里撞见过什么词"。

**为什么单独记账**: 高亮是"当下这一刻画在页面上", 用完就没了。书宠要的是
**历史**: 这几天它陪读时, 哪些人名/地名/器物反复出现。这份计数是冒险那道
工序的燃料 —— 一个 `place` 类词出现 14 次, 它就该在故事里成为一处真实的地形。

**记的是"遭遇", 不是"划过的字"**:
  · 只统计**人真正打开过的章** (打开章节正文时才记), 不统计全书
  · 同一章 6 小时内重复打开不重复计数 (刷新页面不该让词频翻倍),
    但**隔天再读**会照常累加 —— 重读也是遭遇
  · 每个词按 ``书 × 天 × 词`` 一行, 计数按天累加

**词表来源就是阅读器高亮用的那一份** (`core.reading_mark.build_payload`):
同一套语料、同一套采样上限, 书宠"遇到过的词"跟人眼里的高亮同源。
没有语料的书 → 只记 ``chapter_seen`` (给章节储备用), 不产生遭遇行。

**性能**: 匹配走前缀树 (最长匹配, 单遍扫描), 不使用 N 次 ``str.count``。
一章 5 万字 × 6 千词, 前缀树约 1~3 ms; 而且记录放在后台线程里, 不挡翻页。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from recto.core import clock
from recto.pet import store

log = logging.getLogger("recto.pet.encounters")

#: 同一章重复打开的去重窗口 (小时)
CHAPTER_DEDUPE_HOURS = 6.0
#: 章节节选最多存多少字 (给知识储备当依据)
EXCERPT_CHARS = 1500

_CACHE: dict[str, tuple[str, dict[str, str]]] = {}
_LOCK = threading.Lock()

#: 语料里偶尔会混进这种"万能词" —— 它们在每本书里都高频, 但对书宠的联想
#: 毫无用处 (拿 "自己" 去编一个江湖故事只会写出废话)。语料由模型抽, 这一层
#: 是我们自己的兜底过滤 (不删源语料, 只是不进遭遇计数)。
NOISE = {
    "自己", "我们", "你们", "他们", "她们", "大家", "别人", "什么", "这样",
    "那样", "时候", "一个", "没有", "知道", "可以", "还是", "已经", "因为",
    "所以", "如果", "现在", "这里", "那里", "东西", "事情", "地方", "问题",
    # 指示词 / 方位 / 时间泛词: 语料分类常把它们算进 place/time, 但拿它们
    # 编故事只会写出"此地有一处地方"这种废话
    "此地", "此处", "这份", "此时", "此刻", "那边", "这边", "上面", "下面",
    "里面", "外面", "身上", "心里", "眼前", "身后", "今日", "昨夜", "方才",
    "之后", "之前", "后来", "一人", "两人", "众人", "有人", "旁人", "少年",
}


# ------------------------------------------------------------------ 词表

def words_for(book_key: str) -> dict[str, str]:
    """这本书的语料词 → 分类 id (没有语料就空字典)。"""
    try:
        from recto.core import reading_mark

        conf = reading_mark.load()
        sig = f"{conf.get('version')}|{conf.get('max_terms')}|{conf.get('min_len')}"
        with _LOCK:
            hit = _CACHE.get(book_key)
            if hit and hit[0] == sig:
                return hit[1]
        payload = reading_mark.build_payload(book_key, conf)
        words: dict[str, str] = {}
        for cat in payload.get("cats") or []:
            for w in cat.get("words") or []:
                w = str(w)
                if w in NOISE:
                    continue
                words.setdefault(w, str(cat.get("id")))
        with _LOCK:
            _CACHE[book_key] = (sig, words)
        return words
    except Exception as e:  # noqa: BLE001 — 没有语料是常态
        log.debug("语料词表不可用 · %s · %s", book_key, e)
        return {}


def _trie(words: dict[str, str]) -> dict:
    root: dict = {}
    for w, cat in words.items():
        if not w:
            continue
        node = root
        for ch in w:
            node = node.setdefault(ch, {})
        node["\x00"] = cat
    return root


def scan(text: str, words: dict[str, str]) -> dict[str, int]:
    """单遍最长匹配, 返回 {词: 次数}。"""
    if not text or not words:
        return {}
    root = _trie(words)
    counts: dict[str, int] = {}
    n = len(text)
    i = 0
    while i < n:
        node = root
        j = i
        best = ""
        best_cat = ""
        while j < n:
            nxt = node.get(text[j])
            if nxt is None:
                break
            node = nxt
            j += 1
            cat = node.get("\x00")
            if cat is not None:
                best = text[i:j]
                best_cat = cat
        if best:
            counts[best] = counts.get(best, 0) + 1
            i += len(best)
        else:
            i += 1
    return counts


# ------------------------------------------------------------------ 记录

def _recently_seen(book_key: str, chapter: int) -> bool:
    try:
        c = store.conn()
        row = store.one(c, "SELECT last_at FROM chapter_seen WHERE book=? AND chapter=?",
                        (book_key, int(chapter)))
        c.close()
    except Exception:  # noqa: BLE001
        return False
    if not row:
        return False
    t = clock.parse(row.get("last_at") or "")
    if t is None:
        return False
    return (time.time() - t.timestamp()) < CHAPTER_DEDUPE_HOURS * 3600


def record_chapter(book_key: str, chapter: int, text: str,
                   title: str = "") -> dict[str, Any]:
    """记一次"打开了这一章": 章节足迹 + 语料遭遇计数。

    调用方应放在后台线程里 (它要读语料 / 写库, 别挡翻页)。
    重复打开同一章 (6 小时内) → ``skipped=True``, 不写任何计数。
    """
    text = text or ""
    out: dict[str, Any] = {"book": book_key, "chapter": int(chapter),
                           "chars": len(text), "skipped": False,
                           "terms": 0, "hits": 0, "cats": {}}
    if not text:
        return out
    if _recently_seen(book_key, chapter):
        out["skipped"] = True
        return out

    now = clock.iso_local()
    day = now[:10]
    excerpt = text[:EXCERPT_CHARS]
    try:
        c = store.conn()
        c.execute(
            "INSERT INTO chapter_seen (book, chapter, title, chars, excerpt, first_at, last_at)"
            " VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(book, chapter) DO UPDATE SET title=excluded.title,"
            " chars=excluded.chars, excerpt=excluded.excerpt, last_at=excluded.last_at",
            (book_key, int(chapter), title or "", len(text), excerpt, now, now))
        c.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("遭遇 · 记章节失败 %s #%s: %s", book_key, chapter, e)
        return out

    words = words_for(book_key)
    counts = scan(text, words)
    if not counts:
        return out
    try:
        c = store.conn()
        for term, n in counts.items():
            cat = words.get(term, "")
            c.execute(
                "INSERT INTO encounter (book, day, term, cat, count, first_at, last_at)"
                " VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(book, day, term) DO UPDATE SET"
                " count=encounter.count+excluded.count, last_at=excluded.last_at",
                (book_key, day, term, cat, n, now, now))
        c.commit()
        c.close()
    except Exception as e:  # noqa: BLE001
        log.warning("遭遇 · 写计数失败 %s #%s: %s", book_key, chapter, e)
        return out

    cats: dict[str, int] = {}
    for term, n in counts.items():
        cats[words.get(term, "")] = cats.get(words.get(term, ""), 0) + n
    out.update({"terms": len(counts), "hits": sum(counts.values()), "cats": cats})
    return out


def record_async(book_key: str, chapter: int, text: str, title: str = "") -> None:
    """后台记录 (不阻塞请求线程)。"""
    def _run():
        try:
            record_chapter(book_key, chapter, text, title)
        except Exception as e:  # noqa: BLE001
            log.warning("遭遇 · 后台记录异常: %s", e)

    threading.Thread(target=_run, name="pet-encounter", daemon=True).start()


# ------------------------------------------------------------------ 统计

def recent(days: int = 3, limit: int = 40) -> dict[str, Any]:
    """最近 N 天的遭遇 (按次数降序)。

    返回 ``{"days": n, "terms": [{term, cat, count, books, last_at}], "cats": {cat: count}, "total": n}``。
    """
    days = max(1, int(days))
    since = clock.fmt_sec(time.time() - days * 86400, "%Y-%m-%d")
    try:
        c = store.conn()
        rows = store.rows(
            c,
            "SELECT term, cat, SUM(count) n, COUNT(DISTINCT book) books, MAX(last_at) last_at"
            " FROM encounter WHERE day >= ? GROUP BY term, cat"
            " ORDER BY n DESC LIMIT ?", (since, int(limit)))
        catrows = store.rows(
            c, "SELECT cat, SUM(count) n FROM encounter WHERE day >= ? GROUP BY cat",
            (since,))
        c.close()
    except Exception as e:  # noqa: BLE001
        log.warning("遭遇 · 读统计失败: %s", e)
        return {"days": days, "terms": [], "cats": {}, "total": 0}
    cats = {str(r["cat"] or "other"): int(r["n"] or 0) for r in catrows}
    terms = [{"term": r["term"], "cat": r["cat"] or "other", "count": int(r["n"] or 0),
              "books": int(r["books"] or 0), "last_at": r["last_at"]}
             for r in rows]
    return {"days": days, "terms": terms, "cats": cats,
            "total": sum(int(r["n"] or 0) for r in rows)}


def for_book(book_key: str, days: int = 7, limit: int = 30) -> list[dict[str, Any]]:
    since = clock.fmt_sec(time.time() - max(1, days) * 86400, "%Y-%m-%d")
    try:
        c = store.conn()
        rows = store.rows(
            c,
            "SELECT term, cat, SUM(count) n, MAX(last_at) last_at FROM encounter"
            " WHERE book=? AND day>=? GROUP BY term, cat ORDER BY n DESC LIMIT ?",
            (book_key, since, int(limit)))
        c.close()
    except Exception:  # noqa: BLE001
        return []
    return [{"term": r["term"], "cat": r["cat"] or "other", "count": int(r["n"] or 0),
             "last_at": r["last_at"]} for r in rows]


def summary(days: int = 7) -> dict[str, Any]:
    r = recent(days, limit=12)
    try:
        c = store.conn()
        books = store.one(
            c, "SELECT COUNT(DISTINCT book) b, SUM(count) n FROM encounter WHERE day>=?",
            (clock.fmt_sec(time.time() - days * 86400, "%Y-%m-%d"),)) or {}
        seen = store.one(c, "SELECT COUNT(*) n FROM chapter_seen") or {}
        last = store.one(c, "SELECT MAX(last_at) at FROM chapter_seen") or {}
        c.close()
    except Exception:  # noqa: BLE001
        books, seen, last = {}, {}, {}
    return {"days": days, "books": int(books.get("b") or 0),
            "hits": int(books.get("n") or 0),
            "top": r.get("terms") or [], "cats": r.get("cats") or {},
            "chapters_seen": int(seen.get("n") or 0), "last_at": last.get("at") or ""}


def reset() -> None:
    """清空遭遇与章节足迹 (测试用)。"""
    c = store.conn()
    c.execute("DELETE FROM encounter")
    c.execute("DELETE FROM chapter_seen")
    c.commit()
    c.close()
    with _LOCK:
        _CACHE.clear()


def dump_json(days: int = 3, limit: int = 40) -> str:
    return json.dumps(recent(days, limit), ensure_ascii=False)
