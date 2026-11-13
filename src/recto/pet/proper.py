"""专名闸门 —— 故事**不许搬用**书里的名字。

**为什么要有这道闸**: 书宠的故事是架空世界里的游历, 它读的书只是"启发"。
可模型一旦看到书名、人名、地名, 就会顺手抄进故事 —— 今天抄一本武侠,
明天抄一本海外散文, 世界立刻拼贴起来, 前后不自洽。

所以规矩分两层:
  1. **不给**: 判成长与写故事的提示词里不再出现书名 / 作者 / 章名 / 语料原词
     (见 ``chronicle`` 的 payload)
  2. **查**: 故事写完之后再照一份"禁用词表"过一遍 (本模块)。命中就重写一次,
     重写仍命中则做替换兜底, 并在故事上标 ``sanitized``。

禁用词表 = 读过的书的书名与作者 + 阅读器里出现过的语料词中属于
``person / place / org / work / event`` 的那一类 (通用词不在此列 —— 禁掉
"客栈""大人"只会让文字变形)。

**白名单**优先: 架空世界自己造的名字 (``world.all_names``) 永不禁。
"""
from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger("recto.pet.proper")

#: 语料里算作"专名"的分类
PROPER_CATS = {"person", "place", "org", "work", "event"}

#: 通用词兜底 —— 这些虽然会落在 person/place 类里, 但它们不是名字。
#: 禁掉它们只会让故事读起来像被剪过。
GENERIC = {
    "大人", "老爷", "夫人", "先生", "姑娘", "公子", "少年", "老者", "老人",
    "孩子", "女儿", "儿子", "父亲", "母亲", "兄弟", "师姐", "师兄", "师父",
    "老师", "掌柜", "小二", "客人", "众人", "有人", "自己", "江湖", "天下",
    "县城", "城里", "城外", "山上", "河边", "路口", "街上", "客栈", "酒楼",
    "茶棚", "马厩", "庙里", "庙前", "院里", "屋内", "屋里", "门外", "门口",
    "京城", "长安", "中原", "江南", "塞外", "边境", "关外", "北边", "南边",
    "一条", "一处", "一片", "一座", "一间", "一口", "一把", "一块", "一只",
    "刀子", "剑法", "内功", "法术", "功夫", "身手", "气力", "学问", "道理",
    # 语料把这些也标成 person/place —— 但它们不是名字, 禁掉只会让文字变形
    "人间", "人世", "世人", "主人", "丫头", "丫鬟", "下人", "小厮", "汉子",
    "女子", "男人", "女人", "老妇", "汉子", "道士", "和尚", "书生", "夫子",
    "先生们", "书院", "学堂", "衙门", "官衙", "集市", "市集", "街市", "巷子",
    "胡同", "院子", "后院", "前厅", "堂屋", "书房", "楼上", "楼下", "屋里屋外",
    "上山", "下山", "入山", "出山", "关外", "关内", "江南水乡", "北地", "南疆",
    "中土", "神州", "五岳", "四海", "八荒", "天下人", "附近", "周围", "四周",
    "半路", "路上", "道上", "街上", "门口", "门外", "门内", "屋里", "窗外",
    "清晨", "黄昏", "夜里", "白日", "早年", "晚年", "山门", "山脚", "山顶",
    "河边", "湖边", "渡口边", "城门口", "客栈里", "庙门口", "院子外",
}

#: 太短 (单字) 的词不当专名 —— 单字误伤面太大
MIN_LEN = 2

_CACHE: dict[str, Any] = {"at": 0.0, "sig": "", "words": {}}


def _book_terms() -> tuple[dict[str, str], str]:
    """收集禁用词 ``{词: 分类}`` 与一个签名 (变了就重算)。"""
    words: dict[str, str] = {}
    sig_parts: list[str] = []
    try:
        from recto.pet import store

        c = store.conn()
        for r in store.rows(c, "SELECT title, author FROM lore_book"):
            t = str(r.get("title") or "").strip()
            a = str(r.get("author") or "").strip()
            if len(t) >= MIN_LEN:
                words.setdefault(t, "work")
            if len(a) >= MIN_LEN:
                words.setdefault(a, "person")
            sig_parts.append(t + "/" + a)
        seen = store.rows(c, "SELECT DISTINCT book FROM encounter")
        c.close()
    except Exception as e:  # noqa: BLE001
        log.debug("专名 · 读书卡失败: %s", e)
        seen = []
    try:
        from recto.services import reader_state as rs

        rc = rs.conn()
        for r in rs.rows(rc, "SELECT key, title, author FROM rbook"):
            t = str(r.get("title") or "").strip()
            a = str(r.get("author") or "").strip()
            if len(t) >= MIN_LEN:
                words.setdefault(t, "work")
            if len(a) >= MIN_LEN:
                words.setdefault(a, "person")
            sig_parts.append(t)
    except Exception as e:  # noqa: BLE001
        log.debug("专名 · 读书目失败: %s", e)
    try:
        from recto.pet import encounters, store

        c = store.conn()
        rows = store.rows(
            c, "SELECT term, cat, SUM(count) n, COUNT(DISTINCT book) books"
               " FROM encounter WHERE cat IN ('person','place','org','work','event')"
               " GROUP BY term, cat ORDER BY n DESC LIMIT 500")
        c.close()
        for r in rows:
            w = str(r.get("term") or "").strip()
            if len(w) < MIN_LEN or w in GENERIC:
                continue
            # 两个字的多半是通用词或身份词 (剑客 / 前辈 / 云海)。
            # 名字一般三个字以上 (陈平安 / 泥瓶巷 / 书简湖); 书名整段另算。
            if len(w) < 3 and str(r.get("cat") or "") != "work":
                continue
            # **跨书出现过** → 这是个通用词 (人名地名只在它自己那本书里出现)。
            # 没有这条, 语料里那些"人间 / 主人 / 书院"会把正常用语一起禁掉。
            if int(r.get("books") or 1) > 1:
                continue
            words.setdefault(w, str(r.get("cat") or "other"))
        for one in seen:
            sig_parts.append(str(one.get("book") or ""))
    except Exception as e:  # noqa: BLE001
        log.debug("专名 · 读遭遇失败: %s", e)
    # 语料词会随章增加, 用总数当签名的一部分
    sig = "|".join(sorted(sig_parts))[:4000] + f"#{len(words)}"
    return words, sig


def forbidden(force: bool = False) -> dict[str, str]:
    """当前禁用词表 ``{词: 分类}`` (缓存 60 秒; 白名单里的架空名字剔除)。"""
    import time

    hit = _CACHE
    if not force and hit["words"] and time.time() - float(hit["at"] or 0) < 60:
        return dict(hit["words"])
    words, sig = _book_terms()
    try:
        from recto.pet import world

        allowed = world.all_names()
    except Exception:  # noqa: BLE001
        allowed = set()
    for nm in allowed:
        words.pop(nm, None)
    _CACHE.update({"at": time.time(), "sig": sig, "words": dict(words)})
    return words


def _first_chars(words: dict[str, str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for w in words:
        out.setdefault(w[0], []).append(w)
    # 长的先查 (避免"张三丰"被"张三"抢先)
    for k in out:
        out[k].sort(key=len, reverse=True)
    return out


def check(text: str, words: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """故事正文里出现了哪些禁用词。返回 ``[{word, cat, count}]`` (按次数降序)。"""
    text = text or ""
    if not text:
        return []
    words = forbidden() if words is None else words
    if not words:
        return []
    index = _first_chars(words)
    found: dict[str, int] = {}
    n = len(text)
    for i in range(n):
        bucket = index.get(text[i])
        if not bucket:
            continue
        for w in bucket:
            if text.startswith(w, i):
                found[w] = found.get(w, 0) + 1
                break
    out = [{"word": w, "cat": words.get(w, "other"), "count": c}
           for w, c in found.items()]
    out.sort(key=lambda x: (-x["count"], -len(x["word"])))
    return out


def describe(hits: list[dict[str, Any]], limit: int = 12) -> str:
    """给模型看的一句话 (只列命中的那几个词 —— 词表本身太长, 塞不进提示词)。"""
    if not hits:
        return ""
    names = [h["word"] for h in hits[:limit]]
    return ("下面这些名字属于你读过的**别人的**书, 不许出现在你的世界里: "
            + "、".join(names) + "。")


def _pick(cat: str, used: set[str]) -> str:
    """从架空世界里挑一个同类名字来做兜底替换。"""
    try:
        from recto.pet import world

        w = world.load()
    except Exception:  # noqa: BLE001
        return ""
    kind = {"person": "notables", "place": "regions", "org": "factions",
            "work": "things", "event": ""}.get(cat, "")
    if not kind:
        return ""
    for it in (w.get(kind) or []):
        nm = str((it or {}).get("name") or "")
        if nm and nm not in used:
            return nm
    return ""


def scrub(text: str, hits: list[dict[str, Any]] | None = None) -> tuple[str, list[str]]:
    """兜底: 把命中的名字换成架空世界里的同类名字 (重放仍失败时才用)。

    语义上未必最贴切, 但**世界是自造的**这条底线比逐句漂亮重要。
    找不到可替换的名字时退成中性词 (``某处`` / ``某人`` / ``一件旧物``)。
    """
    text = text or ""
    hits = check(text) if hits is None else hits
    if not text or not hits:
        return text, []
    used: set[str] = set()
    replaced: list[str] = []
    neutral = {"person": "某人", "place": "某处", "org": "某个去处",
               "work": "一本书", "event": "一桩旧事"}
    for h in hits:
        word, cat = str(h["word"]), str(h["cat"])
        # 两个字的多半是通用词 (人间 / 五岳 / 丫头)。禁它只是让模型换个说法,
        # 真去替换会把句子改成地名, 反而更糟 —— 只报告, 不动手。
        if len(word) < 3 and cat != "work":
            continue
        tgt = _pick(cat, used) or neutral.get(cat, "")
        if not tgt:
            continue
        used.add(tgt)
        cnt = text.count(word)
        if cnt:
            text = text.replace(word, tgt)
            replaced.append(f"{word}→{tgt}")
            log.info("专名 · 兜底替换 %s → %s (%d 处)", word, tgt, cnt)
    return text, replaced


#: 正则粗筛: 带引号的短语常是书名 —— 重写时额外提醒
QUOTED = re.compile(r"[《「『]([^》」』]{2,20})[》」』]")
