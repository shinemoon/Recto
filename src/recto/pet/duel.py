"""遭遇结算 —— 书宠在江湖上真打一场。

**为什么要有这一层**: 属性、技能、绝招如果只写在面板上, 故事里就只能靠模型
随口编"它一刀劈开了"。有了这一层, 每一回游历的"转"是**算出来的**:
命中看身法与主修、伤害看臂力与兵刃、内力被绝招吃掉、失手会反噬、打输了就伤。
故事只负责把这一场照实写出来, 不许自己判胜负。

**口径 (都来自 charsheet, 不另立一套)**
  · 出手顺序 = ``speed`` + 一点抖动 (身法 + 轻功 + 禀赋)
  · 命中率   = 55 + (我方 hit − 对方 dodge) × 0.5, 夹在 15~92
  · 招架     = 命中之后还有 ``parry × 0.3``% 的机会卸掉四成力
  · 伤害     = 基础伤害 × 0.85~1.15 − 对方 defense × 0.5, 暴击 ×1.7
  · 绝招     = 内力够才用; 成功率 ``charsheet.perform_rate``; 失败按 ``fail`` 级反噬
  · 结果     = 一方气血归零 (倒下), 或 12 回合后比气血比例 (平手)

**只做一件事**: 把结果与消耗写回存档, 并返回一份可读的战报。
"""
from __future__ import annotations

import random
import zlib
from typing import Any

from recto.pet import charsheet as cs

#: 最多打几回合 (再久就不是"遇上", 是决战了)
MAX_ROUNDS = 12

EFFECT_CN = {
    "damage": "伤害", "control": "控住", "defend": "守势",
    "evade": "身法", "heal": "疗伤", "buff": "涨己",
}

RESULT_CN = {"win": "胜", "lose": "负", "draw": "平"}

#: 没有语料时给对手起的名字 (江湖上最常见的那种人)
FOE_POOL = [
    {"name": "一个背刀的汉子", "garb": "短打", "why": "拦在路上要过路钱"},
    {"name": "一个脸色发青的瘦子", "garb": "青布长衫", "why": "说你踩了他的脚"},
    {"name": "一个腰间挂鞭的镖师", "garb": "皮甲", "why": "认错了人, 非要动手"},
    {"name": "一个带斗笠的刀客", "garb": "斗笠压着半张脸", "why": "开口就要你的行囊"},
    {"name": "一个酒气未散的汉子", "garb": "赤着半边膀子", "why": "被你碰翻了酒碗"},
    {"name": "一个少年模样的剑客", "garb": "白衣白靴", "why": "要拿你试他的新招"},
]

PLACE_POOL = ["荒村口", "石桥下", "渡口", "山神庙前", "林间小路", "集市尽头",
              "干涸的河滩", "关道边的茶棚"]

WEAPON_CN = {"jianfa": "一柄剑", "daofa": "一把刀", "gunfa": "一根齐眉棍",
             "quanfa": "一双拳头", "zhangfa": "两只手掌", "zhifa": "一根食指",
             "tuifa": "一双腿", "qinggong": "一双快脚"}


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _terms_of(cat: str, terms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [t for t in (terms or []) if str(t.get("cat") or "") == cat and t.get("term")]


def make_foe(ch: dict[str, Any], terms: list[dict[str, Any]] | None = None,
             seed: int | None = None) -> dict[str, Any]:
    """按书宠当前的份量, 造一个挡路的人。

    对手从**语料里长出来**: 有 ``person`` 类的词就用它当名号 (读者这段时间
    真遇到的角色类型), 有 ``place`` 的当场地, 有 ``object`` 的当兵器。
    没有语料就退回江湖上最常见的那种人 —— 不空转。
    """
    seed = int(seed if seed is not None else 0)
    rng = random.Random(seed)
    terms = terms or []

    persons = _terms_of("person", terms) or _terms_of("org", terms)
    places = _terms_of("place", terms)
    objects = _terms_of("object", terms)

    if persons:
        name = f"一个自称「{str(persons[0]['term'])[:8]}」的人"
        why = "在道上堵住了这条路"
    else:
        pick = rng.choice(FOE_POOL)
        name, why = pick["name"], pick["why"]
    arena = (str(places[0]["term"])[:8] + "一带") if places else rng.choice(PLACE_POOL)
    weapon_term = str(objects[0]["term"])[:8] if objects else ""

    # 份量: 跟书宠同量级 (±2 级), 不该出现"三岁娃娃打宗师"
    lv = max(1, int(ch.get("level") or 1) + rng.randint(-2, 2))
    attr = {a["key"]: int(10 + (lv - 1) * 0.4 + rng.randint(-1, 1)) for a in cs.ATTRS}
    for k in attr:
        attr[k] = int(_clamp(attr[k], 5, 30))

    foe_weapons = ["daofa", "jianfa", "quanfa", "gunfa", "zhangfa", "tuifa"]
    weapon = rng.choice(foe_weapons)
    skills = {
        weapon: max(1, lv),
        "jibenquanjiao": max(1, lv // 2),
        "jibenshenfa": max(1, lv // 2),
        "jibenzhaojia": max(1, lv // 3),
    }
    # 对手也会一两手绝招: 会哪一路由兵器定
    if weapon in ("jianfa", "daofa"):
        skills["lipihuashan" if weapon == "daofa" else "yiqihcheng"] = 1
    elif weapon in ("zhangfa", "zhifa"):
        skills["yizhenjianxue"] = 1
    else:
        skills["bafangfengyu"] = 1
    skills = {k: v for k, v in skills.items() if k in cs.SKILL_BY_ID}

    pseudo = {"level": lv, "exp": lv * lv * 50, "attrs": attr, "skills": skills,
              "traits": [], "school": "", "vein": 0}
    d = cs.derive(pseudo)
    d = _normalize_foe(ch, d, rng)

    return {
        "name": name,
        "why": why,
        "arena": arena,
        "weapon": weapon,
        "weapon_cn": WEAPON_CN.get(weapon, "一件兵器"),
        "weapon_term": weapon_term,
        "level": lv,
        "attrs": attr,
        "skills": skills,
        "combat": d["combat"],
        "hp": d["hp_max"], "hp_max": d["hp_max"],
        "neili": d["neili_max"], "neili_max": d["neili_max"],
        "stun": 0,
        "trait_mods": {},
    }


def _normalize_foe(ch: dict[str, Any], d: dict[str, Any],
                   rng: random.Random) -> dict[str, Any]:
    """把对手的数值拉到书宠自己的量级上。

    为什么不让等级差决定强弱: 书宠的成长很慢 (五天一级), 偶尔遇到"比我高三级"
    的人就会变成必输, 故事里就成了"每回都挨打"。江湖上的遭遇应该**互有胜负**,
    所以按对方当前战力做归一: 伤害 / 命中 / 闪避 / 气血 都在我方 ±20% 内浮动。
    """
    mine = cs.derive(ch)
    mc, fc = mine["combat"], d["combat"]
    k = rng.uniform(0.82, 1.12)

    def ratio(a: int, b: int, lo: float, hi: float) -> float:
        return _clamp((a / max(1, b)) * k, lo, hi)

    fc["damage"] = max(4, int(round(fc["damage"] * ratio(mc["damage"], fc["damage"], 0.65, 1.0))))
    fc["hit"] = int(round(fc["hit"] + rng.uniform(-6, 6) + (mc["hit"] - fc["hit"]) * 0.6))
    fc["dodge"] = int(round(fc["dodge"] + rng.uniform(-6, 6) + (mc["dodge"] - fc["dodge"]) * 0.6))
    fc["parry"] = int(round(fc["parry"] + (mc["parry"] - fc["parry"]) * 0.6))
    fc["defense"] = int(round(fc["defense"] * ratio(mc["defense"], fc["defense"], 0.7, 1.25)))
    fc["speed"] = int(round(fc["speed"] + (mc["speed"] - fc["speed"]) * 0.6))
    d["hp_max"] = max(30, int(round(mine["hp_max"] * rng.uniform(0.7, 0.95))))
    d["neili_max"] = max(20, int(round(mine["neili_max"] * rng.uniform(0.8, 1.2))))
    return d


def _my_fighter(ch: dict[str, Any]) -> dict[str, Any]:
    """把书宠包成一个"打架用的"结构 (气血/内力从存档里取当前值)。"""
    d = cs.derive(ch)
    res = cs.resources(ch)
    pfms = []
    for sid, lv in (ch.get("skills") or {}).items():
        sk = cs.SKILL_BY_ID.get(str(sid))
        if not sk or sk.get("type") != "perform" or int(lv) <= 0:
            continue
        pfms.append({
            "id": str(sid), "name": sk["name"], "level": int(lv),
            "cost": int(sk.get("cost") or 0),
            "rate": cs.perform_rate(ch, sk, int(lv)),
            "busy": int(sk.get("busy") or 0) + (1 if int(ch.get("vein") or 0) >= 10 else 0),
            "fail": cs.perform_fail(ch, sk),
            "effect": str(sk.get("effect") or "damage"),
        })
    pfms.sort(key=lambda p: -p["rate"])
    return {
        "name": ch.get("name") or "书宠",
        "combat": d["combat"],
        "hp": res["hp"]["cur"], "hp_max": res["hp"]["max"],
        "neili": res["neili"]["cur"], "neili_max": res["neili"]["max"],
        "pfms": pfms,
        "stun": 0,
        "guard": 0.0,
        "dodge_buff": 0,
        "dmg_buff": 0.0,
        "low_hp_damage": int(d["combat"].get("low_hp_damage") or 0),
    }


def _foe_pfms(foe: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for sid, lv in (foe.get("skills") or {}).items():
        sk = cs.SKILL_BY_ID.get(str(sid))
        if not sk or sk.get("type") != "perform":
            continue
        out.append({
            "id": str(sid), "name": sk["name"], "level": int(lv),
            "cost": int(sk.get("cost") or 0),
            "rate": int(_clamp(int(sk.get("rate") or 40) + int(lv) * 2, 5, 90)),
            "busy": int(sk.get("busy") or 0),
            "fail": int(sk.get("fail") or 0),
            "effect": str(sk.get("effect") or "damage"),
        })
    out.sort(key=lambda p: -p["rate"])
    return out


def _hit_chance(atk: dict[str, Any], dfd: dict[str, Any]) -> float:
    extra = dfd.get("dodge_buff") or 0
    return _clamp(55 + (atk["combat"]["hit"] - (dfd["combat"]["dodge"] + extra)) * 0.5, 15, 92)


def _damage(atk: dict[str, Any], dfd: dict[str, Any], rng: random.Random,
            mult: float = 1.0) -> tuple[int, bool, str]:
    base = atk["combat"]["damage"] * mult
    # 残血拼命 (禀赋「死缠烂打」)
    if atk.get("low_hp_damage") and atk["hp"] <= atk["hp_max"] * 0.3:
        base *= 1 + atk["low_hp_damage"] / 100.0
    base *= rng.uniform(0.85, 1.15)
    base -= dfd["combat"]["defense"] * 0.5
    crit = rng.random() * 100 < atk["combat"].get("crit", 0)
    if crit:
        base *= 1.6
    # 守势 / 门派守招
    base *= (1 - (dfd.get("guard") or 0.0))
    # 招架
    parried = rng.random() * 100 < _clamp(dfd["combat"]["parry"] * 0.3, 0, 30)
    if parried:
        base *= 0.6
    # 禀赋减伤
    take = int(dfd["combat"].get("take_pct") or 0)
    if take:
        base *= (100 + take) / 100.0
    dmg = max(1, int(round(base)))
    return dmg, crit, ("招架" if parried else ("暴击" if crit else ""))


def _pick_pfm(fight: dict[str, Any], rng: random.Random) -> dict[str, Any] | None:
    usable = [p for p in (fight.get("pfms") or []) if p["cost"] <= fight["neili"]]
    if not usable:
        return None
    # 不是每一下都放绝招: 起手先用狠招, 之后看情况 (内力见底就留着)
    if rng.random() > 0.45:
        return None
    if fight["neili"] < fight["neili_max"] * 0.25 and rng.random() > 0.5:
        return None
    return usable[0] if rng.random() > 0.4 else rng.choice(usable)


def _act(atk: dict[str, Any], dfd: dict[str, Any], rng: random.Random,
         log: list[dict[str, Any]], rnd: int, side: str) -> None:
    """一次出手: 选招 → 命中 → 结算。就地改 atk / dfd。"""
    if atk["stun"] > 0:
        atk["stun"] -= 1
        log.append({"r": rnd, "side": side, "move": "", "kind": "stun",
                    "text": f"{atk['name']} 一时动弹不得, 错过一拍",
                    "dmg": 0, "hp": {"a": atk["hp"], "b": dfd["hp"]}})
        return

    atk["guard"] = max(0.0, (atk.get("guard") or 0.0) - 0.25) if atk.get("guard") else 0.0
    pfm = _pick_pfm(atk, rng)
    move = ""
    mult = 1.0
    effect = "damage"
    success = True
    if pfm:
        atk["neili"] = max(0, atk["neili"] - pfm["cost"])
        move = pfm["name"]
        effect = pfm["effect"]
        mult = {"damage": 1.40, "control": 0.60, "defend": 0.50,
                "evade": 0.75, "heal": 0.0, "buff": 0.85}.get(effect, 1.0)
        success = rng.random() * 100 < pfm["rate"]

    if pfm and not success:
        # 反噬: 自己吃一下, 内力白花
        back = 2 + pfm["fail"] * (2 + int(atk["combat"]["damage"]) // 12)
        atk["hp"] = max(0, atk["hp"] - back)
        log.append({"r": rnd, "side": side, "move": move, "kind": "fail",
                    "text": f"{atk['name']} 一招「{move}」走偏, 反噬己身, 伤 {back}",
                    "dmg": 0, "self": back, "hp": {"a": atk["hp"], "b": dfd["hp"]}})
        return

    if effect == "heal":
        heal = max(1, int(atk["combat"]["damage"] * 0.8))
        atk["hp"] = min(atk["hp_max"], atk["hp"] + heal)
        log.append({"r": rnd, "side": side, "move": move, "kind": "heal",
                    "text": f"{atk['name']} 用「{move}」调息, 回气 {heal}",
                    "dmg": 0, "hp": {"a": atk["hp"], "b": dfd["hp"]}})
        return
    if effect == "defend":
        atk["guard"] = 0.30
        atk["dmg_buff"] = 0.0
        log.append({"r": rnd, "side": side, "move": move, "kind": "defend",
                    "text": f"{atk['name']} 摆出「{move}」, 稳住门户",
                    "dmg": 0, "hp": {"a": atk["hp"], "b": dfd["hp"]}})
        return
    if effect == "evade":
        atk["dodge_buff"] = 15
        mult = 0.75
    if effect == "control":
        mult = 0.65

    chance = _hit_chance(atk, dfd)
    if rng.random() * 100 >= chance:
        log.append({"r": rnd, "side": side, "move": move, "kind": "miss",
                    "text": (f"{atk['name']} 一招「{move}」出手, 被 {dfd['name']} 让开了"
                             if move else
                             f"{atk['name']} 一拳递过去, 被 {dfd['name']} 让开了"),
                    "dmg": 0, "hp": {"a": atk["hp"], "b": dfd["hp"]}})
        return

    dmg, crit, note = _damage(atk, dfd, rng, mult)
    dfd["hp"] = max(0, dfd["hp"] - dmg)
    extra = ""
    if pfm and effect == "control":
        dfd["stun"] = max(dfd["stun"], int(pfm["busy"]))
        extra = f", 定住对方 {pfm['busy']} 拍"
    if crit:
        extra += ", 正中要害"
    hit_txt = (f"{atk['name']} 用「{move}」命中 {dfd['name']}, 伤 {dmg}"
               if move else f"{atk['name']} 一拳打中 {dfd['name']}, 伤 {dmg}")
    log.append({"r": rnd, "side": side, "move": move, "kind": "hit",
                "text": f"{hit_txt}{(' (' + note + ')') if note else ''}{extra}",
                "dmg": dmg, "effect": effect if pfm else "",
                "hp": {"a": atk["hp"], "b": dfd["hp"]}})


def resolve(ch: dict[str, Any], foe: dict[str, Any] | None = None, *,
            terms: list[dict[str, Any]] | None = None,
            seed: int | None = None) -> dict[str, Any]:
    """结算一场遭遇。就地改 ``ch`` 的 hp / neili / fame, 返回战报。"""
    if seed is None:
        seed = zlib.crc32(f"{ch.get('name')}|{ch.get('level')}|{foe or ''}".encode()) & 0x7FFFFFFF
    rng = random.Random(int(seed))
    if foe is None:
        foe = make_foe(ch, terms, seed=int(seed) + 1)
    else:
        foe = dict(foe)
        foe.setdefault("stun", 0)
        foe.setdefault("guard", 0.0)
        foe.setdefault("dodge_buff", 0)
    foe.setdefault("pfms", _foe_pfms(foe))

    me = _my_fighter(ch)
    start_hp, start_neili = me["hp"], me["neili"]
    log: list[dict[str, Any]] = []

    for rnd in range(1, MAX_ROUNDS + 1):
        # 先手: 速度 + 抖动, 每回合重算 (招式会添乱)
        me_sp = me["combat"]["speed"] + rng.uniform(-3, 3)
        foe_sp = foe["combat"]["speed"] + rng.uniform(-3, 3)
        order = [(me, foe, "me"), (foe, me, "foe")]
        if foe_sp > me_sp:
            order = [(foe, me, "foe"), (me, foe, "me")]
        for atk, dfd, side in order:
            if me["hp"] <= 0 or foe["hp"] <= 0:
                break
            _act(atk, dfd, rng, log, rnd, side)
        if me["hp"] <= 0 or foe["hp"] <= 0:
            break

    if foe["hp"] <= 0 and me["hp"] > 0:
        result = "win"
    elif me["hp"] <= 0 and foe["hp"] > 0:
        result = "lose"
    elif me["hp"] <= 0 and foe["hp"] <= 0:
        result = "draw"
    else:
        # 打满回合: 比气血比例, 差 5% 以内算平手
        a = me["hp"] / max(1, me["hp_max"])
        b = foe["hp"] / max(1, foe["hp_max"])
        if abs(a - b) < 0.05:
            result = "draw"
        else:
            result = "win" if a > b else "lose"

    # 输也不能真死 —— 书宠的命是读者的, 这里只留"重伤"
    me["hp"] = max(1, me["hp"])
    fame_gain = {"win": 3, "draw": 1, "lose": 0}[result]

    ch["hp"] = me["hp"]
    ch["neili"] = me["neili"]
    if fame_gain:
        ch["fame"] = int(ch.get("fame") or 0) + fame_gain

    moves, foe_moves = [], []
    for e in log:
        mv = e.get("move") or ""
        if not mv:
            continue
        bucket = moves if e.get("side") == "me" else foe_moves
        if mv not in bucket:
            bucket.append(mv)

    summary = {
        "win": f"赢了 —— {foe['name']} 先撑不住",
        "lose": f"输了 —— 吃了 {foe['name']} 一记重的, 带伤退走",
        "draw": f"打平 —— 双方各自收手, 谁都看出再打下去要断骨头",
    }[result]

    return {
        "foe": {k: foe.get(k) for k in ("name", "why", "arena", "weapon_cn",
                                        "weapon_term", "level", "hp", "hp_max")},
        "rounds": log,
        "result": result,
        "result_cn": RESULT_CN[result],
        "summary": summary,
        "moves": moves,
        "foe_moves": foe_moves,
        "fame": fame_gain,
        "hp": {"from": start_hp, "to": me["hp"], "max": me["hp_max"]},
        "neili": {"from": start_neili, "to": me["neili"], "max": me["neili_max"]},
        "seed": int(seed),
        "rounds_used": (log[-1]["r"] if log else 0),
        "effect_cn": EFFECT_CN,
    }


def brief(duel: dict[str, Any] | None) -> str:
    """战报的一句话摘要 (日志 / 页面 pill 用)。"""
    if not duel:
        return ""
    foe = (duel.get("foe") or {}).get("name") or "对手"
    return (f"遭遇 {foe} · {duel.get('result_cn') or ''} · "
            f"气血 {duel['hp']['to']}/{duel['hp']['max']}")
