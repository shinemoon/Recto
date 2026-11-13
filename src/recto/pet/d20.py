"""掷骰与检定 —— 书宠世界的桌游底子 (d20)。

口味是 **D&D 的 d20 + 加值 vs 难度**, 叠上《极乐迪斯科》的几条规矩:

  · **加值必须能拆开。** 这一分来自根骨, 那一分来自技能, 那一分来自禀赋。
    战报里逐条列 (``mods``), 不写一个笼统的总数 —— 读者点开要能看见
    "为什么这一下中了 / 为什么那一下没中"。
  · **白检定 / 红检定。** 白检定败了还能再来 (每再试一次 +1 累积, 封顶 +3);
    红检定一次定生死 (``retry=False``)。
  · **大成功 / 大失败压过难度。** 骰出 20 必成 (难度高过总加值也成),
    骰出 1 必败 (加值再高也败)。桌游里最要紧的就是这两种意外。
  · **被动值 = 10 + 加值。** 不掷骰的察觉与记认 (对面在不在、这话像不像谎话)。

只在两处用: 交手 (``duel``) 与路上的坎 (``checks``)。两处共用同一套加值算法 ——
不各算一套, 否则同一个人在两处会忽然变强或变弱。
"""
from __future__ import annotations

import random
from typing import Any

#: 难度阶梯 —— 名字借《极乐迪斯科》的口气, 数值是 d20 的规矩。
#: 寻常 11 = 加值 +3 的人掷出 8 以上? 差不多六成半 —— 江湖上"该成的事"。
DC_TIERS: list[dict[str, Any]] = [
    {"key": "trivial", "name": "轻而易举", "dc": 5},
    {"key": "easy", "name": "简单", "dc": 8},
    {"key": "normal", "name": "寻常", "dc": 11},
    {"key": "tricky", "name": "不易", "dc": 14},
    {"key": "hard", "name": "困难", "dc": 17},
    {"key": "formidable", "name": "艰险", "dc": 20},
    {"key": "legendary", "name": "传奇", "dc": 23},
]
DC_BY_KEY = {t["key"]: t for t in DC_TIERS}

#: 检定种类 (战报与页面用它上色/命名)
KIND_CN = {
    "initiative": "先攻",
    "attack": "攻击",
    "damage": "伤害",
    "save": "豁免",
    "skill": "技能",
    "attr": "根骨",
    "perform": "运功",
    "passive": "被动",
}

#: 结果档 —— 桌游里"大成功"和"成功"是两回事, 战报要分开写
DEGREE_CN = {"crit": "大成功", "success": "成功", "fail": "失败", "fumble": "大失败"}


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def attr_mod(value: int) -> int:
    """根骨加值 —— D&D 的老式子 ``(属性 − 10) // 2``。

    根骨 10 → +0, 14 → +2, 18 → +4, 30 → +10。所以"臂力 14"读作 +2。
    """
    return int((int(value) - 10) // 2)


def skill_mod(level: int) -> int:
    """技能加值 —— 一级算半分, 封顶 +8。

    侠客行的技能等级是 1~30+, 直接当加值会压死 d20 的尺度; 折半后
    "十级"读作 +5, 与 D&D 的熟练加值同量级。
    """
    return int(clamp((int(level) + 1) // 2, 0, 8))


def dc_of(key_or_dc: str | int) -> int:
    if isinstance(key_or_dc, int):
        return key_or_dc
    t = DC_BY_KEY.get(str(key_or_dc))
    return int(t["dc"]) if t else int(DC_BY_KEY["normal"]["dc"])


def tier_of(dc: int) -> dict[str, Any]:
    """最接近的那一档 (页面上的难度名)。"""
    dc = int(dc)
    best = DC_TIERS[0]
    for t in DC_TIERS:
        if abs(int(t["dc"]) - dc) < abs(int(best["dc"]) - dc):
            best = t
    return best


def roll(rng: random.Random, sides: int) -> int:
    return int(rng.randint(1, max(1, int(sides))))


def roll_d20(rng: random.Random, mode: str = "normal") -> tuple[int, list[int]]:
    """掷 d20。``adv`` = 有优势 (掷两次取高), ``dis`` = 有劣势 (取低)。"""
    if mode == "adv":
        a, b = roll(rng, 20), roll(rng, 20)
        return max(a, b), [a, b]
    if mode == "dis":
        a, b = roll(rng, 20), roll(rng, 20)
        return min(a, b), [a, b]
    v = roll(rng, 20)
    return v, [v]


def roll_dice(rng: random.Random, count: int, sides: int) -> dict[str, Any]:
    """掷一把骰子 (伤害用)。返回明细, 页面照它画。"""
    count = max(1, int(count))
    sides = max(2, int(sides))
    faces = [roll(rng, sides) for _ in range(count)]
    return {"count": count, "sides": sides, "faces": faces, "sum": int(sum(faces)),
            "text": f"{count}d{sides} {faces} = {sum(faces)}"}


def mods_of(*pairs: tuple[str, int, str]) -> list[dict[str, Any]]:
    """把 (名字, 分值, 来源) 收成 mods 列表, 丢掉 0 分。

    来源只有几种: ``attr`` 根骨 / ``skill`` 技能 / ``trait`` 禀赋 /
    ``school`` 门派 / ``situ`` 情境 (守势、暗处、伤、疲劳) / ``gear`` 器物。
    """
    out = []
    for name, value, src in pairs:
        if int(value):
            out.append({"name": str(name), "value": int(value), "src": str(src)})
    return out


def check(rng: random.Random, *, label: str, dc: int, mods: list[dict[str, Any]] | None = None,
          kind: str = "skill", kind_cn: str = "", bonus: int = 0,
          mode: str = "normal", retry: bool = False, note: str = "",
          allow_crit: bool = True) -> dict[str, Any]:
    """掷一次检定。

    ``bonus`` 是"不写在加值清单里"的临时加减 (守势、暗处、被围)。它会进总数,
    但战报里单列一行 ``情境`` —— 免得和根骨/技能混在一起看。
    """
    dc = int(dc)
    mods = [dict(m) for m in (mods or []) if int(m.get("value") or 0)]
    total_mod = sum(int(m["value"]) for m in mods) + int(bonus)
    face, faces = roll_d20(rng, mode)
    total = face + total_mod
    crit = bool(allow_crit and face == 20)
    fumble = bool(allow_crit and face == 1)
    if crit:
        success = True
    elif fumble:
        success = False
    else:
        success = total >= dc
    degree = "crit" if crit else ("fumble" if fumble else
                                  ("success" if success else "fail"))
    tier = tier_of(dc)
    out = {
        "label": str(label),
        "kind": kind,
        "kind_cn": kind_cn or KIND_CN.get(kind, "检定"),
        "d20": int(face),
        "d20s": faces,
        "mode": mode,
        "mods": mods,
        "mod": int(sum(int(m["value"]) for m in mods)),
        "bonus": int(bonus),
        "total": int(total),
        "dc": dc,
        "dc_label": str(tier["name"]),
        "success": bool(success),
        "crit": crit,
        "fumble": fumble,
        "degree": degree,
        "degree_cn": DEGREE_CN[degree],
        "margin": int(total - dc),
        "retry": bool(retry),
        "note": str(note),
    }
    out["text"] = describe(out)
    return out


def describe(c: dict[str, Any]) -> str:
    """一行掷骰明细 —— 战报与提示词共用这一份措辞。

    样子: ``攻击检定 · d20 13 + 命中 5 (臂力 +2 / 剑法 +3) = 18 vs 护体 15 → 命中``
    """
    if not c:
        return ""
    parts = []
    for m in c.get("mods") or []:
        parts.append(f"{m['name']} {int(m['value']):+d}")
    if int(c.get("bonus") or 0):
        parts.append(f"情境 {int(c['bonus']):+d}")
    detail = (" (" + " / ".join(parts) + ")") if parts else ""
    head = f"{c.get('kind_cn') or '检定'} · d20 {c.get('d20')}"
    tail = f" = {c.get('total')} vs {c.get('dc')}"
    verb = {"attack": ("命中", "未中"), "save": ("过", "没过"),
            "perform": ("成了", "没成"), "initiative": ("", "")}.get(
        str(c.get("kind") or ""), ("过", "没过"))
    res = verb[0] if c.get("success") else verb[1]
    if c.get("crit"):
        res = "大成功"
    elif c.get("fumble"):
        res = "大失败"
    return f"{head} {int(c.get('mod') or 0) + int(c.get('bonus') or 0):+d}{detail}{tail} → {res}"


def short(c: dict[str, Any]) -> str:
    """更短的一行 (页面 chip / 日志)。"""
    if not c:
        return ""
    mark = {True: "✓", False: "✗"}[bool(c.get("success"))]
    if c.get("crit"):
        mark = "★"
    elif c.get("fumble"):
        mark = "☠"
    return f"{mark} {c.get('label') or ''} {c.get('total')}/{c.get('dc')}"


def passive(mods: list[dict[str, Any]] | None = None, bonus: int = 0) -> int:
    """被动值 —— 不掷骰时的"够不够察觉"。"""
    return 10 + sum(int(m.get("value") or 0) for m in (mods or [])) + int(bonus)


def rulebook() -> list[str]:
    """规则说明 (页面「规则」折叠区与 skill 文档共用一份措辞)。"""
    return [
        "一切判定都是 d20: 骰点 + 加值 (根骨 / 技能 / 禀赋) + 情境, 与难度比大小。",
        "难度: 轻而易举 5 · 简单 8 · 寻常 11 · 不易 14 · 困难 17 · 艰险 20 · 传奇 23。",
        "骰出 20 = 大成功 (必成); 骰出 1 = 大失败 (必败)。两者都压过难度。",
        "白检定败了能再试, 每再试一次 +1 (封顶 +3); 红检定一次定生死。",
        "交手: 先攻 → 出手 → 攻击检定对护体 → 伤害骰; 绝招先掷运功检定, 走偏则反噬。",
        "中带控的绝招, 对方掷根骨/定力豁免 (DC = 8 + 我方绝招加值); 不过就定住一拍。",
        "气血归零即倒; 打满十二拍比气血比例, 相差半成以内算平手。",
    ]


def dc_table() -> list[dict[str, Any]]:
    return [dict(t) for t in DC_TIERS]
