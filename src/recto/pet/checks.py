"""路上的坎 —— 不打斗的那些判定。

一回游历最多两道: 一道**路** (辨路 / 夜行 / 渡水 / 辨药), 一道**人**
(识人 / 看破 / 交涉 / 忍痛)。每道都从语料里挂一个具体的说法 ——
「在泥瓶巷辨路」「识破那个自称陈平安的人」—— 所以故事能照着写, 不是空判。

规矩 (与交手同一套 d20, 见 ``d20``):
  · **白检定**败了下一回还能再试; 每再试一次 +1 累积 (封顶 +3) —— 这是
    《极乐迪斯科》里最像"人在琢磨一件事"的机制, 也让重复的坎有意思。
  · **红检定**一次定生死: 过了就过, 没过这件事就到此为止。
  · 加值照旧拆开记: 根骨 + 技能 + 禀赋, 战报里逐条可查。

这里只出**结果与说法**, 故事只负责照实写 —— 成功与失败的说法都给了,
模型不许自己改判。
"""
from __future__ import annotations

import random
from typing import Any

from recto.pet import charsheet as cs
from recto.pet import d20 as D

#: 路上一类的模板。``trait`` 指的是禀赋里哪一项在这里起作用 (取四分之一)。
ROAD_TEMPLATES: list[dict[str, Any]] = [
    {"key": "road", "label": "辨路", "attr": "shen", "skill": "qinggong",
     "trait": "speed", "dc": "normal", "what": "赶一段不熟的路",
     "ok": "抄了近道, 省下半日脚程", "no": "绕了远路, 天黑才看见人烟"},
    {"key": "night", "label": "夜行", "attr": "shen", "skill": "qinggong",
     "trait": "dodge", "dc": "tricky", "what": "摸黑过一段荒路",
     "ok": "落脚没声, 一路无惊", "no": "踩塌了碎石, 惊起一片犬吠"},
    {"key": "cross", "label": "渡水", "attr": "gen", "skill": "jibenshenfa",
     "trait": "take_pct", "dc": "tricky", "what": "涉过一条涨水的河",
     "ok": "水只到腰间, 干着衣裳上了岸", "no": "被水冲了半丈, 行囊湿透"},
    {"key": "herb", "label": "辨药", "attr": "wu", "skill": "yishu",
     "trait": "hit", "dc": "normal", "what": "认一味长在乱草里的药",
     "ok": "认准了, 顺手采了一小把", "no": "认错了, 白费半日还沾了手"},
    {"key": "track", "label": "追踪", "attr": "wu", "skill": "zhuizong",
     "trait": "hit", "dc": "tricky", "what": "跟着一路淡淡的痕迹",
     "ok": "痕迹没断, 跟到了地方", "no": "痕断在石板上, 线索就到这里"},
    {"key": "hear", "label": "听声", "attr": "wu", "skill": "guanqi",
     "trait": "hit", "dc": "normal", "what": "听出墙后头有几个人",
     "ok": "听出三个人的呼吸, 还有一个在陪坐",
     "no": "只听见风, 进去了才知道里头有人"},
]

#: 人一类的模板 (红检定偏多 —— 人与人的事往往只给一次机会)
PERSON_TEMPLATES: list[dict[str, Any]] = [
    {"key": "read", "label": "识人", "attr": "wu", "skill": "guanqi",
     "trait": "hit", "dc": "tricky", "what": "看穿一个人的来路",
     "ok": "看见他虎口的老茧 —— 是个常拿兵器的",
     "no": "把他当成了寻常行商, 直到他抬手"},
    {"key": "talk", "label": "交涉", "attr": "ding", "skill": "", "trait": "parry",
     "dc": "tricky", "what": "跟一个不好说话的人把话说完",
     "ok": "话留了余地, 双方都不必动手", "no": "话说岔了, 他把手按在刀上"},
    {"key": "judge", "label": "看破", "attr": "wu", "skill": "guanqi",
     "trait": "hit", "dc": "hard", "what": "辨出眼前这件事里谁在说谎",
     "ok": "听出他话里少了一个人", "no": "被他绕了过去, 事后才想明白"},
    {"key": "endure", "label": "忍痛", "attr": "gen", "skill": "jibenneigong",
     "trait": "take_pct", "dc": "normal", "what": "带着伤走完这一段",
     "ok": "一声没出, 步子没乱", "no": "走到半路就得扶墙歇一歇"},
    {"key": "seek", "label": "问路", "attr": "ding", "skill": "", "trait": "parry",
     "dc": "normal", "what": "向一个不爱搭理人的人问路",
     "ok": "问到了, 还多听了一句闲话", "no": "被指了个错方向"},
]

#: 红检定 (一次定生死) 的模板 —— 人与人的事, 过了就过了
RED_KEYS = {"judge", "talk"}


def _dc_for(tpl: dict[str, Any], red: bool) -> int:
    dc = D.dc_of(str(tpl.get("dc") or "normal"))
    return dc + (3 if red else 0)


def _mods_for(ch: dict[str, Any], tpl: dict[str, Any]) -> list[dict[str, Any]]:
    """根骨 + 技能 + 禀赋 —— 三样拆开记 (战报里逐条可查)。"""
    a = cs.effective_attrs(ch)
    tm = cs.trait_mods(ch)
    attr_key = str(tpl.get("attr") or "wu")
    pairs: list[tuple[str, int, str]] = [
        ((cs.ATTR_BY_KEY.get(attr_key) or {}).get("name", attr_key) + " " + str(a.get(attr_key, 10)),
         D.attr_mod(int(a.get(attr_key, 10))), "attr"),
    ]
    sid = str(tpl.get("skill") or "")
    if sid and sid in cs.SKILL_BY_ID:
        lv = cs.skill_level(ch, sid)
        if lv:
            pairs.append((cs.SKILL_BY_ID[sid]["name"] + " " + str(lv) + " 级",
                          D.skill_mod(lv), "skill"))
    tk = str(tpl.get("trait") or "")
    if tk:
        raw = int(tm.get(tk) or 0)
        if raw:
            # take_pct 是"减伤百分比", 负得越多越抗 —— 这里只当"耐得住"
            val = abs(raw) // 4
            if val:
                pairs.append(("禀赋", val, "trait"))
    return D.mods_of(*pairs)


def _world_names(kind: str) -> list[str]:
    try:
        from recto.pet import world as wmod

        w = wmod.load()
    except Exception:  # noqa: BLE001
        return []
    return [str(x.get("name")) for x in (w.get(kind) or []) if str(x.get("name") or "")]


def _target(ch: dict[str, Any], tpl: dict[str, Any], terms: list[dict[str, Any]],
            rng: random.Random) -> str:
    """这一道坎具体落在什么上 —— 用地名/人名, 全部来自**它自己的世界**。

    语料只决定"这段时间这一路走得多", 不再把别人书里的词塞进句子
    (2026-11-13: 故事要自我架空, 只借"意"不借"名")。
    """
    cat = "place" if tpl in ROAD_TEMPLATES else "person"
    base = str(tpl.get("what") or "")
    names = _world_names("regions" if cat == "place" else "notables")
    if not names:
        names = _world_names("factions") or _world_names("things")
    if names:
        w = names[rng.randrange(len(names))][:12]
        if cat == "place":
            return f"在{w}{base}"
        return f"{base}（{w}）"
    return base


def plan(ch: dict[str, Any], terms: list[dict[str, Any]] | None = None,
         seed: int | None = None, memory: dict[str, Any] | None = None,
         count: int = 2, prefer: str = "any") -> list[dict[str, Any]]:
    """挑这一趟路上要过的坎 (不掷骰), 返回待判定的清单。

    ``prefer`` 决定坎从哪个池子里挑 (由这一趟是什么事决定, 见 ``kind``):
      · ``any``    一道"路" + 一道"人" (交手那一趟)
      · ``person`` 两道"人" —— 见人 / 看破 / 交涉
      · ``road``   两道"路" —— 赶路 / 学艺 / 伸手帮人
    ``count=1`` 时只出一道 (从偏好的池子里挑)。
    """
    rng = random.Random(int(seed if seed is not None else 0))
    terms = terms or []
    memory = memory or {}
    prefer = prefer if prefer in ("any", "person", "road") else "any"
    pools: list[list[dict[str, Any]]] = []
    if prefer == "person":
        pools = [PERSON_TEMPLATES, PERSON_TEMPLATES]
    elif prefer == "road":
        pools = [ROAD_TEMPLATES, ROAD_TEMPLATES]
    else:
        pools = [ROAD_TEMPLATES, PERSON_TEMPLATES]
    picks: list[dict[str, Any]] = []
    used: set[str] = set()
    for pool in pools[:max(1, int(count))]:
        want = 1
        cand = [t for t in pool if str(t["key"]) not in used] or list(pool)
        got = rng.sample(cand, min(want, len(cand)))
        for tpl in got:
            used.add(str(tpl["key"]))
            picks.append(tpl)

    out = []
    for tpl in picks:
        red = str(tpl["key"]) in RED_KEYS
        cid = str(tpl["key"])
        tries = int((memory.get(cid) or 0))
        out.append({
            "id": cid,
            "label": str(tpl["label"]),
            "target": _target(ch, tpl, terms, rng),
            "white": not red,
            "retry": not red,
            "tries": tries,
            "what": str(tpl["what"]),
            "ok": str(tpl["ok"]),
            "no": str(tpl["no"]),
            "dc": _dc_for(tpl, red),
            "mods": _mods_for(ch, tpl),
        })
    return out


def resolve(ch: dict[str, Any], terms: list[dict[str, Any]] | None = None, *,
            seed: int | None = None, memory: dict[str, Any] | None = None,
            count: int = 2, prefer: str = "any") -> dict[str, Any]:
    """掷这一趟路上的坎。返回 ``{"checks": [...], "memory": {...}, ...}``。

    ``memory`` 传上一回合的账; 白检定重试一次 +1 (封顶 +3), 成了就把账抹平。
    """
    rng = random.Random((int(seed) if seed is not None else 0) + 977)
    memory = dict(memory or {})
    planned = plan(ch, terms, seed, memory, count=count, prefer=prefer)
    out: list[dict[str, Any]] = []
    for p in planned:
        tries = int(p.get("tries") or 0)
        bonus = min(3, tries) if p["white"] else 0
        label = p["label"] + ("·再试" if bonus else "")
        c = D.check(rng, label=label, dc=int(p["dc"]), mods=list(p["mods"]),
                    kind="skill", bonus=bonus, retry=bool(p["retry"]))
        c.update({
            "id": p["id"], "target": p["target"], "white": bool(p["white"]),
            "tries": tries, "what": p["what"],
            "ok": p["ok"], "no": p["no"],
            "outcome": (p["ok"] if c["success"] else p["no"]),
            "hint": (f"过了 · {p['ok']}" if c["success"] else
                     (f"没过 · {p['no']}" + ("" if p["white"] else " (这一道不再给机会)"))),
        })
        if c["success"]:
            memory.pop(p["id"], None)
        elif p["white"]:
            memory[p["id"]] = tries + 1
        out.append(c)

    return {
        "checks": out,
        "memory": memory,
        "rolls": len(out),
        "summary": " · ".join(
            f"{c['label']} {'✓' if c['success'] else '✗'}" for c in out),
    }


def brief(checks: list[dict[str, Any]] | None) -> str:
    """一行摘要 (日志用)。"""
    if not checks:
        return ""
    return " · ".join(
        f"{c.get('label')} {c.get('total')}/{c.get('dc')} "
        f"{'过' if c.get('success') else '未过'}" for c in checks)


def rulebook() -> list[str]:
    return [
        "路上的坎也是 d20: 根骨 + 技能 + 禀赋, 与难度比大小。",
        "白检定 (走、看、耐) 败了下一回能再试, 每再试一次 +1, 封顶 +3。",
        "红检定 (看破、交涉) 一次定生死 —— 过了就过, 没过这件事就到此为止。",
        "大成功必过, 大失败必不过; 两者都比难度优先。",
    ]
