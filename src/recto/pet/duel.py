"""遭遇结算 —— 书宠在江湖上真打一场 (**d20 桌游口径**)。

**为什么要有这一层**: 属性、技能、禀赋如果只写在面板上, 故事里就只能靠模型
随口编"它一刀劈开了"。有了这一层, 每一回游历的"转"是**掷骰掷出来的**:
先攻看身法, 出手是 d20 + 命中加值 对护体, 伤害是兵刃骰 + 臂力, 绝招要先运功,
走偏会反噬, 被控要掷豁免, 打输了就带伤。故事只负责把这一场**照实**写出来。

**规矩 (d20, 见 ``d20`` 模块)**
  · 先攻      d20 + 身法加值 + 轻功 (每场掷一次, 高者先出手)
  · 攻击检定  d20 + 命中加值 (根骨 / 主修 / 禀赋拆开记) vs 对方护体
  · 大成功    d20 = 20 必中, 伤害骰掷两遍; 骰点进暴击区间也算 (禀赋会放宽)
  · 大失败    d20 = 1 必不中, 自己脱力受伤, 门户大开 (对方下一拍 +2)
  · 运功检定  d20 + 绝招加值 vs 招式难度 (由招式成功率折算); 不过则走偏反噬
  · 豁免检定  带"控住"的绝招命中后, 对方掷根骨豁免 vs 8 + 我方绝招加值
  · 伤害      兵刃骰 + 臂力加值×2 + 主修加值×2 + 8, 再减对方卸力
  · 结果      一方气血归零 (倒下), 或打满十二拍比气血比例 (半成以内算平手)

**只做一件事**: 把结果与消耗写回存档, 并返回一份**可展开的战报** ——
每一拍带 ``rolls`` (逐条掷骰明细), 页面点开就能看见"为什么这一下中了"。
"""
from __future__ import annotations

import random
import zlib
from typing import Any

from recto.pet import charsheet as cs
from recto.pet import d20 as D
from recto.pet import flavor as F

#: 最多打几拍 (再久就不是"遇上", 是决战了)
MAX_ROUNDS = 12

EFFECT_CN = {
    "damage": "伤害", "control": "控住", "defend": "守势",
    "evade": "身法", "heal": "疗伤", "buff": "涨己",
}

RESULT_CN = {"win": "胜", "lose": "负", "draw": "平"}

#: 没有语料时给对手起的名字 (江湖上最常见的那种人)
FOE_POOL = [
    {"name": "一个背刀的汉子", "garb": "短打", "why": "拦在路上要过路钱", "weapon": "daofa"},
    {"name": "一个脸色发青的瘦子", "garb": "青布长衫", "why": "说你踩了他的脚", "weapon": "zhifa"},
    {"name": "一个腰间挂鞭的镖师", "garb": "皮甲", "why": "认错了人, 非要动手", "weapon": "gunfa"},
    {"name": "一个带斗笠的刀客", "garb": "斗笠压着半张脸", "why": "开口就要你的行囊", "weapon": "daofa"},
    {"name": "一个酒气未散的汉子", "garb": "赤着半边膀子", "why": "被你碰翻了酒碗", "weapon": "quanfa"},
    {"name": "一个少年模样的剑客", "garb": "白衣白靴", "why": "要拿你试他的新招", "weapon": "jianfa"},
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


# ------------------------------------------------------------------ 对手

def _world() -> dict[str, Any]:
    """当前架空世界 (对手 / 场地的名字从这里取)。"""
    try:
        from recto.pet import world as wmod

        return wmod.load()
    except Exception:  # noqa: BLE001 — 世界读不出来就用兜底池
        return {}


def make_foe(ch: dict[str, Any], terms: list[dict[str, Any]] | None = None,
             seed: int | None = None) -> dict[str, Any]:
    """按书宠当前的份量, 造一个挡路的人。

    **名字与场地一律取自它自己的架空世界** (``world.notables`` / ``regions`` /
    ``things``), 语料只用来决定"这段时间更像遇上人, 还是更像走过地貌"。

    为什么不再拿语料词当名号: 那些词是别人书里的人名地名, 搬进故事等于把两个
    世界缝在一起 —— 今天抄武侠, 明天抄散文, 前后就不自洽了 (2026-11-13 改)。
    """
    seed = int(seed if seed is not None else 0)
    rng = random.Random(seed)
    terms = terms or []

    persons = _terms_of("person", terms) or _terms_of("org", terms)
    places = _terms_of("place", terms)
    objects = _terms_of("object", terms)

    w = _world()
    notables = [x for x in (w.get("notables") or []) if str(x.get("name") or "")]
    regions = [x for x in (w.get("regions") or []) if str(x.get("name") or "")]
    things = [x for x in (w.get("things") or []) if str(x.get("name") or "")]

    # 语料里"人"这类多 → 这一趟更可能撞上人; 否则多半是路人甲
    foe_weapon_hint = ""
    if persons and notables and rng.random() < 0.65:
        who = notables[rng.randrange(len(notables))]
        name = str(who.get("name"))
        role = str(who.get("role") or "")
        name = (role + " " + name) if role else name
        why = str(who.get("note") or "") or "在道上堵住了这条路"
    else:
        pick = rng.choice(FOE_POOL)
        name, why = pick["name"], pick["why"]
        # 名号里写着"刀客"的不能使拳 —— 文辞与兵刃得对得上
        foe_weapon_hint = str(pick.get("weapon") or "")
    if regions and (places or rng.random() < 0.5):
        arena = str(regions[rng.randrange(len(regions))].get("name"))
    else:
        arena = rng.choice(PLACE_POOL)
    weapon_term = (str(things[rng.randrange(len(things))].get("name"))
                   if objects and things else "")

    # 份量: 跟书宠同量级 (±2 级), 不该出现"三岁娃娃打宗师"
    lv = max(1, int(ch.get("level") or 1) + rng.randint(-2, 2))
    attr = {a["key"]: int(10 + (lv - 1) * 0.4 + rng.randint(-1, 1)) for a in cs.ATTRS}
    for k in attr:
        attr[k] = int(_clamp(attr[k], 5, 30))

    foe_weapons = ["daofa", "jianfa", "quanfa", "gunfa", "zhangfa", "zhifa", "tuifa"]
    weapon = (foe_weapon_hint if foe_weapon_hint in foe_weapons
              else rng.choice(foe_weapons))
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
    stats = _normalize_foe_stats(ch, cs.d20_stats(pseudo), rng)

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
        "d20": stats,
        "hp": int(stats["hp"]), "hp_max": int(stats["hp"]),
        "neili": d["neili_max"], "neili_max": d["neili_max"],
        "stun": 0,
        "trait_mods": {},
    }


def _normalize_foe_stats(ch: dict[str, Any], stats: dict[str, Any],
                         rng: random.Random) -> dict[str, Any]:
    """把对手的 d20 数值拉到书宠自己的量级上。

    为什么不让等级差决定强弱: 书宠的成长很慢, 偶尔遇到"比我高三级"的人
    就会变成必输, 故事里就成了"每回都挨打"。江湖上的遭遇应该**互有胜负**,
    所以护体 / 命中 / 气血 都在我方附近浮动 (±2 点 / 七到九成半)。
    """
    mine = cs.derive(ch)
    my = cs.d20_stats(ch)

    stats = dict(stats)
    stats["ac"] = int(_clamp(my["ac"] + rng.randint(-2, 2), 8, 24))
    stats["attack"] = int(_clamp(my["attack"] + rng.randint(-2, 2), -2, 16))
    stats["init"] = {"total": int(_clamp(my["init"]["total"] + rng.randint(-2, 2), -3, 16)),
                     "parts": stats["init"]["parts"]}
    hp = max(20, int(round(mine["hp_max"] * rng.uniform(0.72, 0.96))))
    stats["hp"] = hp
    # 伤害平值也归一: 比我重的一拳不该一掌打掉我半条命
    flat = int(stats["damage"]["flat"])
    myflat = int(my["damage"]["flat"])
    stats["damage"] = dict(stats["damage"],
                           flat=int(_clamp(round(flat * 0.6 + myflat * 0.4), 4, 60)))
    return stats


# ------------------------------------------------------------------ 出场的两个人

def _pfms_from(ch: dict[str, Any], *, is_foe: bool = False) -> list[dict[str, Any]]:
    """把绝招整理成"掷骰要用"的样子 (难度由成功率折算成 d20 的 DC)。"""
    out = []
    for sid, lv in (ch.get("skills") or {}).items():
        sk = cs.SKILL_BY_ID.get(str(sid))
        if not sk or sk.get("type") != "perform" or int(lv) <= 0:
            continue
        lv = int(lv)
        if is_foe:
            attr_v = int((ch.get("attrs") or {}).get(sk.get("attr") or "wu", 10))
            rate = int(_clamp(int(sk.get("rate") or 40) + lv * 2, 5, 92))
            bonus = D.attr_mod(attr_v) + D.skill_mod(lv) + 1
        else:
            rate = cs.perform_rate(ch, sk, lv)
            attr_v = int(cs.effective_attrs(ch).get(sk.get("attr") or "wu", 10))
            bonus = D.attr_mod(attr_v) + D.skill_mod(lv)
        out.append({
            "id": str(sid), "name": str(sk["name"]), "level": lv,
            "cost": int(sk.get("cost") or 0),
            "rate": int(rate),
            "dc": int(_clamp(21 - round(rate / 5), 6, 18)),
            "bonus": int(bonus),
            "busy": int(sk.get("busy") or 0),
            "fail": int(sk.get("fail") or 0),
            "effect": str(sk.get("effect") or "damage"),
            "attr": str(sk.get("attr") or "wu"),
        })
    out.sort(key=lambda p: -p["rate"])
    return out


ATK_WORD = {"剑法": "一剑", "刀法": "一刀", "棍法": "一棍", "拳法": "一拳",
            "掌法": "一掌", "指法": "一指", "腿法": "一腿"}


def _atk_word(fight: dict[str, Any]) -> str:
    """空手/兵器的称呼 —— 故事与战报里都要对得上 ("一剑" 不是 "一拳")。"""
    return ATK_WORD.get(str((fight.get("damage") or {}).get("weapon") or ""), "一下")


def _weapon(fight: dict[str, Any]) -> str:
    """这一位用的哪一路兵刃 —— 主修的 skill id (``jianfa``), 退而求其次用中文名。

    文辞按它挑词, 所以"刀客不能使拳"这件事就落在这里。
    """
    main = fight.get("main") or {}
    if main.get("id"):
        return str(main["id"])
    return str((fight.get("damage") or {}).get("weapon") or "")


def _fighter(name: str, stats: dict[str, Any], *, hp: int, hp_max: int,
             neili: int, neili_max: int, pfms: list[dict[str, Any]],
             side: str) -> dict[str, Any]:
    return {
        "name": name, "side": side,
        "hp": int(hp), "hp_max": int(hp_max),
        "neili": int(neili), "neili_max": int(neili_max),
        "ac": int(stats["ac"]), "ac_parts": list(stats.get("ac_parts") or []),
        "attack": int(stats["attack"]), "attack_parts": list(stats.get("attack_parts") or []),
        "damage": dict(stats["damage"]),
        "main": dict(stats.get("main") or {}),
        "pierce": int(stats.get("pierce") or 0),
        "init": dict(stats.get("init") or {"total": 0, "parts": []}),
        "saves": dict(stats.get("saves") or {}),
        "crit_range": int(stats.get("crit_range") or 20),
        "perform": dict(stats.get("perform") or {}),
        "pfms": pfms,
        "stun": 0,
        "guard": 0.0,
        "ac_buff": 0,
        "atk_buff": 0,
        "exposed": 0,
    }


def _my_fighter(ch: dict[str, Any]) -> dict[str, Any]:
    """把书宠包成一个"交手用的"结构 (气血/内力从存档里取当前值)。"""
    res = cs.resources(ch)
    return _fighter(ch.get("name") or "书宠", cs.d20_stats(ch),
                    hp=res["hp"]["cur"], hp_max=res["hp"]["max"],
                    neili=res["neili"]["cur"], neili_max=res["neili"]["max"],
                    pfms=_pfms_from(ch), side="me")


def _foe_fighter(foe: dict[str, Any]) -> dict[str, Any]:
    stats = dict(foe.get("d20") or {})
    if not stats:
        pseudo = {"level": foe.get("level") or 1, "exp": 100,
                  "attrs": foe.get("attrs") or {},
                  "skills": foe.get("skills") or {}, "traits": [], "school": "", "vein": 0}
        stats = cs.d20_stats(pseudo)
    hp = int(foe.get("hp") or stats.get("hp") or 30)
    return _fighter(foe.get("name") or "对手", stats, hp=hp,
                    hp_max=int(foe.get("hp_max") or hp),
                    neili=int(foe.get("neili") or 0),
                    neili_max=int(foe.get("neili_max") or 1),
                    pfms=_pfms_from(foe, is_foe=True), side="foe")


# ------------------------------------------------------------------ 掷骰

def _attack_check(rng: random.Random, atk: dict[str, Any], dfd: dict[str, Any],
                  *, extra: list[dict[str, Any]] | None = None,
                  label: str = "攻击检定") -> dict[str, Any]:
    mods = list(atk.get("attack_parts") or []) + list(extra or [])
    bonus = int(atk.get("atk_buff") or 0) + int(atk.get("exposed") or 0) * 2
    c = D.check(rng, label=label, dc=int(dfd["ac"]) + int(dfd.get("ac_buff") or 0),
                mods=mods, kind="attack", bonus=bonus)
    # 暴击区间 (禀赋「胆大心细」把 19 也算大成功)
    if not c["crit"] and c["success"] and c["d20"] >= int(atk.get("crit_range") or 20):
        c["crit"] = True
        c["degree"] = "crit"
        c["degree_cn"] = D.DEGREE_CN["crit"]
        c["text"] = D.describe(c)
    return c


def _damage_roll(rng: random.Random, atk: dict[str, Any], dfd: dict[str, Any],
                 crit: bool) -> dict[str, Any]:
    dmg = atk["damage"]
    count = int(dmg["count"]) * (2 if crit else 1)
    dice = D.roll_dice(rng, count, int(dmg["sides"]))
    raw = dice["sum"] + int(dmg["flat"])
    guard = float(dfd.get("guard") or 0.0)
    taken = max(1, int(round(raw * (1.0 - guard))) - int(dfd.get("pierce") or 0))
    return {"dice": dice, "raw": int(raw), "taken": int(taken),
            "guard": round(guard, 2), "pierce": int(dfd.get("pierce") or 0)}


def _save_check(rng: random.Random, dfd: dict[str, Any], *, label: str,
                dc: int) -> dict[str, Any]:
    parts = ((dfd.get("saves") or {}).get("gen") or {}).get("parts") or []
    return D.check(rng, label=label, dc=int(dc), mods=list(parts), kind="save")


# ------------------------------------------------------------------ 一拍

def _pick_pfm(fight: dict[str, Any], rng: random.Random) -> dict[str, Any] | None:
    usable = [p for p in (fight.get("pfms") or []) if p["cost"] <= fight["neili"]]
    if not usable:
        return None
    # 不是每一下都放绝招: 内力见底就留着, 平时也只用四成
    if rng.random() > 0.45:
        return None
    if fight["neili"] < fight["neili_max"] * 0.25 and rng.random() > 0.5:
        return None
    return usable[0] if rng.random() > 0.4 else rng.choice(usable)


def _log(log: list[dict[str, Any]], rnd: int, side: str, kind: str, text: str, *,
         atk: dict[str, Any], dfd: dict[str, Any], move: str = "",
         dmg: int = 0, self_dmg: int = 0, effect: str = "",
         rolls: list[dict[str, Any]] | None = None,
         dice: dict[str, Any] | None = None) -> None:
    log.append({
        "r": int(rnd), "side": side, "move": move, "kind": kind, "text": text,
        "dmg": int(dmg), "self": int(self_dmg), "effect": effect,
        "hp": {"a": int(atk["hp"]), "b": int(dfd["hp"])},
        "rolls": rolls or [], "dice": dice or {},
    })


def _turn(atk: dict[str, Any], dfd: dict[str, Any], rng: random.Random,
          log: list[dict[str, Any]], rnd: int, voice: Any) -> None:
    """一次出手: 解定身 → 选招 → 运功 → 攻击检定 → 伤害/效果。就地改两人。"""
    side = str(atk["side"])

    # 被定住: 掷豁免挣脱 (不过就再错过一拍)
    if atk["stun"] > 0:
        atk["stun"] -= 1
        save = _save_check(rng, atk, label="定身豁免", dc=12)
        _log(log, rnd, side, "stun",
             (F.stunned(voice, who=atk["name"]) + " —— "
              + (f"这一挣挣开了 (豁免 {save['total']}/{save['dc']})" if save["success"]
                 else f"挣了一下没挣开 (豁免 {save['total']}/{save['dc']})")),
             atk=atk, dfd=dfd, rolls=[save])
        if save["success"]:
            atk["stun"] = 0
        return

    # 守势逐拍消退
    if atk.get("guard"):
        atk["guard"] = max(0.0, round(float(atk["guard"]) - 0.25, 2))
    atk["ac_buff"] = 0
    atk["atk_buff"] = 0
    atk["exposed"] = 0

    pfm = _pick_pfm(atk, rng)
    rolls: list[dict[str, Any]] = []
    move, effect = "", "damage"

    if pfm:
        atk["neili"] = max(0, atk["neili"] - pfm["cost"])
        move, effect = pfm["name"], pfm["effect"]
        tier = int(atk.get("perform", {}).get("neili_tier") or 0)
        pc = D.check(rng, label="运功 · " + move, dc=int(pfm["dc"]),
                     mods=D.mods_of((f"招式 {move} {pfm['level']} 级",
                                     D.skill_mod(pfm["level"]), "skill"),
                                    ("内力档次", tier, "situ")),
                     kind="perform")
        rolls.append(pc)
        if not pc["success"]:
            # 骰出 1 = 内息倒冲, 真反噬; 只是没过难度 = 招式没运成, 手还照常递出去
            if pc["fumble"]:
                back = 2 + int(pfm["fail"]) * (2 + int(atk["damage"]["flat"]) // 12)
                atk["hp"] = max(0, atk["hp"] - back)
                _log(log, rnd, side, "fail",
                     (F.backfire(voice, who=atk["name"], move=move,
                                 weapon=_weapon(atk)).rstrip("。")
                      + f", 自伤 {back}。"),
                     atk=atk, dfd=dfd, move=move, self_dmg=back, effect=effect,
                     rolls=rolls)
                return
            _log(log, rnd, side, "miss",
                 F.pfm_lost(voice, who=atk["name"], move=move),
                 atk=atk, dfd=dfd, move="", rolls=rolls)
            pfm = None
            move, effect = "", "damage"

    # 疗伤 / 守势 / 身法 / 涨己 —— 不必打到人
    if pfm and effect in ("heal", "defend", "evade", "buff"):
        flat = int(atk["damage"]["flat"])
        if effect == "heal":
            heal = max(1, int(flat * 0.8))
            atk["hp"] = min(atk["hp_max"], atk["hp"] + heal)
            _log(log, rnd, side, "heal",
                 F.effect(voice, who=atk["name"], move=move, kind="heal",
                          amount=heal),
                 atk=atk, dfd=dfd, move=move, effect=effect, rolls=rolls)
        elif effect == "defend":
            atk["guard"] = 0.30
            _log(log, rnd, side, "defend",
                 F.effect(voice, who=atk["name"], move=move, kind="defend"),
                 atk=atk, dfd=dfd, move=move, effect=effect, rolls=rolls)
        elif effect == "evade":
            atk["ac_buff"] = 3
            _log(log, rnd, side, "evade",
                 F.effect(voice, who=atk["name"], move=move, kind="evade"),
                 atk=atk, dfd=dfd, move=move, effect=effect, rolls=rolls)
        else:
            atk["atk_buff"] = 2
            _log(log, rnd, side, "buff",
                 F.effect(voice, who=atk["name"], move=move, kind="buff"),
                 atk=atk, dfd=dfd, move=move, effect=effect, rolls=rolls)
        return

    extra = []
    if pfm:
        extra.append({"name": "绝招加值", "value": 2, "src": "skill"})
    atk_check = _attack_check(rng, atk, dfd, extra=extra)
    rolls.append(atk_check)

    if not atk_check["success"]:
        if atk_check["fumble"]:
            atk["exposed"] = 1
            back = 1 + int(atk["damage"]["flat"]) // 12
            atk["hp"] = max(0, atk["hp"] - back)
            _log(log, rnd, side, "fail",
                 (F.fumble(voice, who=atk["name"], weapon=_weapon(atk),
                           move=move).rstrip("。")
                  + f", 反伤 {back}。"),
                 atk=atk, dfd=dfd, move=move, self_dmg=back, rolls=rolls)
            return
        _log(log, rnd, side, "miss",
             F.miss(voice, who=atk["name"], whom=dfd["name"],
                    weapon=_weapon(atk), move=move),
             atk=atk, dfd=dfd, move=move, rolls=rolls)
        return

    hit = _damage_roll(rng, atk, dfd, bool(atk_check["crit"]))
    dfd["hp"] = max(0, dfd["hp"] - hit["taken"])

    stunned = 0
    note = ""
    if pfm and effect == "control":
        dc = 8 + D.skill_mod(pfm["level"]) + int(pfm["bonus"] // 2)
        save = _save_check(rng, dfd, label="定身豁免", dc=dc)
        rolls.append(save)
        if save["success"]:
            note = "这一记本该定人, 劲到穴上却被他挣脱了"
        else:
            dfd["stun"] = max(dfd["stun"], int(pfm["busy"]))
            stunned = int(pfm["busy"])

    _log(log, rnd, side, "hit",
         F.hit(voice, who=atk["name"], whom=dfd["name"], weapon=_weapon(atk),
               move=move, crit=bool(atk_check["crit"]), dmg=hit["taken"],
               guard=float(hit["guard"] or 0), stun=stunned, note=note),
         atk=atk, dfd=dfd, move=move, dmg=hit["taken"],
         effect=(effect if pfm else ""), rolls=rolls, dice=hit["dice"])


# ------------------------------------------------------------------ 结算

def resolve(ch: dict[str, Any], foe: dict[str, Any] | None = None, *,
            terms: list[dict[str, Any]] | None = None,
            seed: int | None = None) -> dict[str, Any]:
    """结算一场遭遇。就地改 ``ch`` 的 hp / neili / fame, 返回可展开的战报。"""
    if seed is None:
        seed = zlib.crc32(f"{ch.get('name')}|{ch.get('level')}|{foe or ''}".encode()) & 0x7FFFFFFF
    rng = random.Random(int(seed))
    if foe is None:
        foe = make_foe(ch, terms, seed=int(seed) + 1)
    else:
        foe = dict(foe)

    me = _my_fighter(ch)
    him = _foe_fighter(foe)
    start_hp, start_neili = me["hp"], me["neili"]

    # ── 先攻: 每场掷一次
    init_me = D.check(rng, label="先攻", dc=0, mods=list(me["init"]["parts"]),
                      kind="initiative", allow_crit=False)
    init_him = D.check(rng, label="先攻", dc=0, mods=list(him["init"]["parts"]),
                       kind="initiative", allow_crit=False)
    order = [me, him] if init_me["total"] >= init_him["total"] else [him, me]

    log: list[dict[str, Any]] = []
    voice = F.new_voice(rng)      # 一场一张嘴: 同一句不连着说第二遍
    result = "draw"
    for rnd in range(1, MAX_ROUNDS + 1):
        if me["hp"] <= 0 or him["hp"] <= 0:
            break
        for who in order:
            other = him if who is me else me
            if me["hp"] <= 0 or him["hp"] <= 0:
                break
            _turn(who, other, rng, log, rnd, voice)

        if me["hp"] <= 0 or him["hp"] <= 0:
            result = "lose" if me["hp"] <= 0 else "win"
            break
    else:
        # 打满回合: 比气血比例, 差 5% 以内算平手
        a = me["hp"] / max(1, me["hp_max"])
        b = him["hp"] / max(1, him["hp_max"])
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
        "lose": f"吃了 {foe['name']} 一记重的, 带伤退走",
        "draw": "打平 —— 双方各自收手, 谁都看出再打下去要断骨头",
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
        "initiative": {"order": [f["side"] for f in order],
                       "me": init_me, "foe": init_him,
                       "text": (("先攻: 我方先手 " if order[0] is me else "先攻: 对方先手 ")
                                + f"(d20 {init_me['d20']} vs {init_him['d20']})")},
        "fighters": {
            "me": {"name": me["name"], "hp": me["hp"], "hp_max": me["hp_max"],
                   "ac": me["ac"], "ac_parts": me["ac_parts"],
                   "attack": me["attack"], "attack_parts": me["attack_parts"],
                   "damage": me["damage"], "pierce": me["pierce"],
                   "init": me["init"], "crit_range": me["crit_range"]},
            "foe": {"name": him["name"], "hp": him["hp"], "hp_max": him["hp_max"],
                    "ac": him["ac"], "attack": him["attack"],
                    "damage": him["damage"], "pierce": him["pierce"],
                    "init": him["init"], "crit_range": him["crit_range"]},
        },
        "rulebook": D.rulebook(),
        "rolls": int(sum(len(e.get("rolls") or []) for e in log)),
        "rounds_used": (log[-1]["r"] if log else 0),
        "seed": int(seed),
        "effect_cn": EFFECT_CN,
    }


def brief(duel: dict[str, Any] | None) -> str:
    """战报的一句话摘要 (日志 / 页面 pill 用)。"""
    if not duel:
        return ""
    foe = (duel.get("foe") or {}).get("name") or "对手"
    return (f"遭遇 {foe} · {duel.get('result_cn') or ''} · "
            f"气血 {duel['hp']['to']}/{duel['hp']['max']}")
