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

# ──────────────────────────────────────────────────────────────
#  前台: 最新一回 (判定 / 启发 / 交手 嵌在正文对应的位置)
# ──────────────────────────────────────────────────────────────
#
#  为什么要有「锚点」这一层:
#  掷出来的事 (路上的坎 / 交手) 与化身 (启发) 都是**已经发生**的 ——
#  它们在正文里本来就有一段对应的地方 (「抄了近道」就是那一次辨路的结果)。
#  但模型写出来的句子和账本里的措辞不一定字面相同, 所以要在代码里
#  算一次"这段话最像落在哪一段", 让它们回到那个位置上去。
#
#  锚法 (从便宜到准):
#  · 化身带 **keys** (几个普通词) → 精确命中优先
#  · 其它 → **最长公共子串** (按 n-gram 从长到短试, 至少 3 字才算命中)
#    3 字是底线: 2 字的常用词会到处都能命中, 锚出来的位置是噪声。
#  · 实在锚不上 → 落在"未锚定"那组, 由页面收在末尾 (不许丢)。

#: 锚点的判定门槛
#:
#: **一条都不能松**: 3 个字就能命中"黑水漠"这种地名 —— 命中了不代表
#: **那件事真的写在这一段里** (实测: 渡水那次判定压根没写进正文, 却因为
#: 段 0 提了一句"黑水漠"被锚了过去 —— 那是**假位置**, 比不锚更坏)。
#:
#: 所以用**打分 + 区分度**两个条件:
#:   score = 关键词命中数 × 4 + 最长公共子串长度
#:   接受 = score ≥ MIN_SCORE **且** 比第二名高 ≥ MIN_GAP
#:
#: MIN_SCORE = 4: 一个 4 字公共子串, 或一个关键词命中 (4 分)。
#: 3 字地名不够 (那只是顺口提了一句)。
ANCHOR_MIN_SCORE = 4
ANCHOR_MIN_GAP = 2
#: 公共子串至少这么长才算命中 (2 字太泛: "练武"到处都有)
ANCHOR_MIN_N = 3


def _ngrams(text: str, sizes=(8, 7, 6, 5, 4, 3)) -> list[tuple[int, str]]:
    """定长 n-gram 列表, **按长度从大到小**排 (先命中长的)。"""
    text = text or ""
    out: list[tuple[int, str]] = []
    for n in sizes:
        if n < ANCHOR_MIN_N:
            continue
        for i in range(0, max(0, len(text) - n + 1)):
            out.append((n, text[i:i + n]))
    return out


def _lcs_len(text: str, para: str) -> int:
    """最长公共子串长度 (n-gram 从长到短试, 第一个命中就是它)。"""
    if not text or not para:
        return 0
    for n, g in _ngrams(text):
        if g in para:
            return n
    return 0


def _score(text: str, keys: list[str], para: str) -> int:
    """这一段跟"这件事"的吻合分。

    关键词命中权重高 (4 分/个): 化身自带的 keys 是**从这一段长出来**的
    几个词, 命中它比命中一段泛泛的公共子串可信得多。
    """
    hits = sum(1 for k in (keys or []) if k and k in para)
    return hits * 4 + _lcs_len(text, para)


def _anchor(text: str, paras: list[str], keys: list[str] | None = None) -> int:
    """这段事最像落在第几段 (0 基)。锚不上 → -1。

    判据 = 打分最高的那一段, 且要**比第二名高出一截** (区分度) ——
    两段差不多高 = 说不清是哪一段, 那就不锚 (宁可收在末尾补记)。
    """
    if not text or not paras:
        return -1
    keys = keys or []
    scored: list[tuple[int, int]] = []
    for i, p in enumerate(paras):
        if not p:
            continue
        scored.append((_score(text, keys, p), i))
    if not scored:
        return -1
    scored.sort(reverse=True)
    best, best_i = scored[0]
    runner = scored[1][0] if len(scored) > 1 else 0
    if best < ANCHOR_MIN_SCORE:
        return -1
    if best - runner < ANCHOR_MIN_GAP:
        return -1
    return best_i


def _anchor_keys(keys: list[str], paras: list[str]) -> int:
    """按几个关键词精确命中 (化身带 keys 时最准)。命中最多的一段。"""
    keys = [k for k in (keys or []) if k]
    if not keys or not paras:
        return -1
    best, best_score = -1, 0
    for i, p in enumerate(paras):
        score = sum(1 for k in keys if k in p)
        if score > best_score:
            best, best_score = i, score
    return best if best_score > 0 else -1


def _spark_keys_map() -> dict[str, list[str]]:
    """化身缓存 → ``{"trace 文本": [keys...]}``。

    **为什么**: 老记录落库时 ``growth.echo.spark`` 里没有 ``keys`` (那是
    2026-11-21 才加的字段), 但化身缓存在 (``spark`` 表) 里一直有 ——
    拿它补回来, 老记录的锚点也能用精确命中, 不必退到 n-gram 猜。
    """
    out: dict[str, list[str]] = {}
    try:
        c = store.conn()
        rows = store.rows(c, "SELECT payload FROM spark ORDER BY at DESC")
        c.close()
    except Exception:  # noqa: BLE001 — 读不到就按没有 keys 走
        return out
    for r in rows:
        try:
            payload = json.loads(str(r.get("payload") or "{}"))
        except ValueError:
            continue
        for e in (payload.get("echo") or []):
            if not isinstance(e, dict):
                continue
            trace = str(e.get("trace") or "").strip()
            keys = [str(k) for k in (e.get("keys") or []) if str(k or "").strip()]
            if trace and keys and trace not in out:
                out[trace] = keys[:4]
    return out


def _inline_blocks(growth: dict[str, Any], paras: list[str]) -> list[dict[str, Any]]:
    """这一回掷出来的事 / 化身 → 锚到段落上的"内嵌块"。

    每块 = ``{"type": "check"|"spark"|"duel", "anchor": 段号, "data": 原始数据}``。
    ``anchor`` 为 -1 = 锚不上 (页面收在末尾, 不许丢)。

    **锚不上的比锚错好**: 判定那一回到底有没有写进正文, 模型自己说了不算
    (实测三回全留空), 靠文本核实 —— 锚不上就说明**正文里真没有那件事**,
    老实收在末尾的「补记」里, 别硬塞到某一段旁边 (那是假位置)。
    """
    blocks: list[dict[str, Any]] = []
    for c in (growth.get("checks") or []):
        if not isinstance(c, dict):
            continue
        text = " ".join([str(c.get("outcome") or ""), str(c.get("target") or ""),
                         str(c.get("label") or "")])
        blocks.append({"type": "check", "anchor": _anchor(text, paras), "data": c})
    keymap = _spark_keys_map()
    for s in ((growth.get("echo") or {}).get("spark") or []):
        if not isinstance(s, dict):
            continue
        keys = [str(k) for k in (s.get("keys") or []) if str(k or "").strip()]
        if not keys:
            keys = keymap.get(str(s.get("trace") or "").strip()) or []
        text = " ".join([str(s.get("trace") or ""), str(s.get("image") or "")])
        # 化身带 keys → 走打分锚 (keys 权重高); 否则退化到纯公共子串
        blocks.append({"type": "spark", "anchor": _anchor(text, paras, keys), "data": s})
    d = growth.get("duel") or None
    if isinstance(d, dict) and d:
        foe = d.get("foe") or {}
        text = " ".join([str(foe.get("name") or ""),
                         " ".join(str(m) for m in (d.get("moves") or [])),
                         str(d.get("summary") or ""), str(d.get("result_cn") or "")])
        blocks.append({"type": "duel", "anchor": _anchor(text, paras), "data": d})
    return blocks


def _counts() -> list[tuple[int, int]]:
    """每一回 (id, 段数), 按 id 升序。"""
    try:
        c = store.conn()
        rows = store.rows(c, "SELECT id, body FROM adventure ORDER BY id ASC")
        c.close()
    except Exception as e:  # noqa: BLE001
        log.warning("故事 · 读段数失败: %s", e)
        return []
    return [(int(r.get("id") or 0), len(split_paras(r.get("body") or "")))
            for r in rows]


def _gidx_map() -> tuple[dict[int, int], int]:
    """(每一回的全局起始段序号, 全部段数)。"""
    counts = _counts()
    gidx: dict[int, int] = {}
    base = 0
    for rid, n in counts:
        gidx[rid] = base
        base += n
    return gidx, base


def migrate_position(pos: dict[str, Any], latest: dict[str, Any]) -> dict[str, Any]:
    """老位置档 → (回号, 段号)。

    老格式有两种:
      * **v1** ``{"<回号>": {"para": 段号}}`` —— 按回记段数
      * **v2** ``{"v": 2, "idx": 全局段序号}`` —— 全书摊平成一条路时的记法

    v1 直接就是 (回号, 段号) —— 取**最靠前**的那一回 (保守: 宁可少读)。
    v2 的全局段序号要换算: 找到它落在哪一回的第几段。

    换算不出来 → 归零 (最新那一回从头读)。**不许报错拖垮页面**。
    """
    from recto.pet import reading

    d = reading._load()  # noqa: SLF001 — 读原始档 (要拿到 v1 的那批键)
    ver = reading.version(d)
    if ver == 1:
        legacy = reading.legacy_positions(d)
        if not legacy:
            return reading.set_pos(0, 0, force=True)
        no = max(int(k) for k in legacy)
        para = int(legacy.get(str(no)) or 0)
        return reading.set_pos(no, para, force=True)
    # v2 (全局段序号) 或别的 → 按全局号找落在哪一回
    try:
        gidx = int((d or {}).get("idx") or 0)
    except (TypeError, ValueError):
        gidx = 0
    if gidx <= 0:
        return reading.set_pos(int(latest.get("no") or 0), 0, force=True)
    counts = _counts()
    base = 0
    for rid, n in counts:
        if base <= gidx < base + n:
            # 找到落在哪一回 —— rid → no
            for a in chronicle.adventures(limit=500):
                if int(a.get("id") or 0) == rid:
                    return reading.set_pos(int(a.get("no") or 0),
                                           gidx - base, force=True)
            break
        base += n
    return reading.set_pos(int(latest.get("no") or 0), 0, force=True)


def _group_blocks(paras: list[str], blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """内嵌块 → 按段落归组, 并把锚不上的收在末尾。

    输出 = ``[{"text": 段, "blocks": [块...]} , ...]`` —— 前端照着画即可。

    **锚不上的不许丢**: 放在最后一段之后 (``anchor`` = 最后一段的下沿),
    页面会把它们收成"末尾补记"。掷出来的事一定要看得见。
    """
    groups: list[dict[str, Any]] = [{"text": p, "blocks": []} for p in paras]
    tail: list[dict[str, Any]] = []
    for b in blocks:
        a = int(b.get("anchor", -1))
        if 0 <= a < len(groups):
            groups[a]["blocks"].append(b)
        else:
            tail.append(b)
    if tail:
        if not groups:
            groups.append({"text": "", "blocks": []})
        groups[-1]["blocks"].extend(tail)
        groups[-1]["tail"] = True
    return groups


def latest() -> dict[str, Any]:
    """前台显示的那一回 —— **最新的一回** (带内嵌锚点)。

    没有故事 → 返回空 dict (页面显示「还没出门」)。
    """
    advs = chronicle.adventures(limit=1)
    if not advs:
        return {}
    a = advs[0]
    body = a.get("body") or ""
    paras = split_paras(body)
    if not paras:
        return {}
    g = a.get("growth") or {}
    counts = _counts()
    gidx: dict[int, int] = {}
    base = 0
    for rid, n in counts:
        gidx[rid] = base
        base += n
    rid = int(a.get("id") or 0)
    start = int(gidx.get(rid, 0))
    blocks = _inline_blocks(g, paras)
    return {
        "id": rid,
        "no": int(a.get("no") or 0),
        "at": a.get("at") or "",
        "title": clean_title(a.get("title") or ""),
        "place": a.get("place") or "",
        "kind": g.get("kind") or "",
        "kind_cn": g.get("kind_cn") or "",
        "kind_why": g.get("kind_why") or "",
        "paras": paras,
        "blocks": blocks,
        "flow": _group_blocks(paras, blocks),
        # base = 这一回在**全局段序号**里的起点 (位置存的是全局的)
        "base": start,
        # total = 这一回有几段 (进度条按这个算)
        "total": len(paras),
        "rounds": len(counts),
        # 这一趟的账 (所显 / 来处 / 交手) —— 跟在正文后面 (见前台读本)
        "growth": g,
        "growth_shown": g.get("growth_shown") or [],
        "echo": g.get("echo") or {},
    }
