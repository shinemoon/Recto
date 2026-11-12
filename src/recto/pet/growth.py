"""书宠的成长接口 —— 把"陪读"变成修为。

**这一层现在只做一件事**: 把陪读这件事换算成成长, 并把换算规则**摆在一个地方**。

为什么单独一个模块: 换算比例是最容易吵起来的地方 (读 10 分钟涨几点修为?
划一条线算不算? 同一段书读第二遍还涨不涨?)。现在只做骨架, 规则集中在这里,
等真的接上阅读器、有真实数据了再调 —— **改一个文件就行, 不用翻七八个地方**。

**当前口径 (先立一个可改的初版)**

  · 陪读分钟 → 修为: ``每分钟 EXP_PER_MINUTE 点修为``
  · 升级 → 潜能点: 每级 ``POT_PER_LEVEL`` 点 (自由分配到六项根骨)
  · 升级 → 声望: 每级 ``FAME_PER_LEVEL`` 点
  · 经脉: 按累计陪读分钟推进 (层数门槛见 ``charsheet.VEINS``)
  · 技能: 陪读时长自动长「读书悟道」, 别的技能靠**研讨** (还没做)

**留白的部分** (等接阅读器再定)

  · 划线 / 批注 / 笔记 是否额外给修为
  · 重读同一本书要不要打折
  · 每天的陪读上限 (现在按 ``耐力`` 的思路想留, 但没实现)
  · 觉醒点 (wish) 从哪来 —— MUD 里那是"愿望"系统, 跟性情不是一回事

**一条硬规矩**: 成长只往一个方向走 —— 修为/等级/潜能/分钟只增不减,
不回退。回退会让"读得多就强"变成"读得多就飘", 陪读的积累感就没了。
"""
from __future__ import annotations

from typing import Any

from recto.pet import charsheet as cs

# ── 换算比例 (集中在这里, 要调只改这一处) ─────────────────────

#: 每陪读 1 分钟换多少修为
EXP_PER_MINUTE = 2
#: 每升 1 级给几个潜能点 (可分配到六项根骨)
POT_PER_LEVEL = 3
#: 每升 1 级给几点声望
FAME_PER_LEVEL = 5
#: 陪读时长自动长哪门功夫 (对应 MUD 北侠的「读书」—— 唯一不消耗潜能的涨法)
AUTO_SKILL = "dushuwudao"
#: AUTO_SKILL 每多少分钟长 1 级。
#: 一小时阅读 = 故事内一天 ⇒ 300 分钟 = 五天一级 —— 慢到"翻半个月书才长一层"。
AUTO_SKILL_MINUTES_PER_LEVEL = 300
#: 判定口径的技能限速: 每 N 个**故事内**天, 最多由判定长 1 级
#: (自动「读书悟道」不占这条, 它自己按 300 分钟一级走)
SKILL_DAYS_PER_LEVEL = 8

#: 先天属性: 每升 N 级 +1。
#: 为什么要有这一条: 光靠潜能点, 属性永远不动 —— 故事里也就写不出"人变了"。
#: 悟性每 3 级 +1、根骨每 6 级 +1 (慢, 一封顶 30)。
BORN_ON_LEVEL = {"wu": 3, "gen": 6}

#: 陪读一次最多记多少分钟 (防手滑把一整天的挂机都算进去)
MAX_MINUTES_PER_CALL = 480

#: 规则说明 —— 随 ``/pet/api/char/schema`` 一起发给前端, 免得界面与代码各说一套
GROWTH_RULES: dict[str, Any] = {
    "exp_per_minute": EXP_PER_MINUTE,
    "pot_per_level": POT_PER_LEVEL,
    "fame_per_level": FAME_PER_LEVEL,
    "auto_skill": AUTO_SKILL,
    "auto_skill_minutes_per_level": AUTO_SKILL_MINUTES_PER_LEVEL,
    "skill_days_per_level": SKILL_DAYS_PER_LEVEL,
    "born_on_level": dict(BORN_ON_LEVEL),
    "max_minutes_per_call": MAX_MINUTES_PER_CALL,
    "one_way": True,
    "time": {
        "minutes_per_story_day": None,   # 由 pet.life 填 (60 分钟 = 故事内一天)
        "start_age": None,               # 12 岁出江湖
        "retire_age": None,              # 80 岁退隐 → 等读者确认传承
    },
    # 判定口径 (后台 skill 给数, 代码只夹逼): 每回合上限
    "judged_caps": {"exp": 3000, "pot": 3, "fame": 20, "skills": 2,
                    "morality": 5, "trait": 1},
    "pending": [
        "划线 / 批注 是否额外给修为 (现按「批注加成」间接算进判定)",
        "重读同一本书要不要打折 (现由模型按 chapters 推进量判断)",
        "每日陪读上限",
        "觉醒点的来源",
    ],
}


def rules() -> dict[str, Any]:
    """``GROWTH_RULES`` 的运行时版本 (把时序里的真实数字填进去)。"""
    import copy

    out = copy.deepcopy(GROWTH_RULES)
    try:
        from recto.pet import life

        c = life.cfg()
        out["time"].update({"minutes_per_story_day": int(c["minutes_per_day"]),
                            "start_age": int(c["start_age"]),
                            "retire_age": int(c["retire_age"])})
    except Exception:  # noqa: BLE001
        pass
    return out


# ── 换算 ─────────────────────────────────────────────────────

def _apply_born(ch: dict[str, Any], old_level: int) -> dict[str, int]:
    """升级偶得先天属性 (就地改)。返回这次长了哪几项、各几点。"""
    new_level = int(ch.get("level") or 1)
    born = dict(ch.get("born") or {})
    gained: dict[str, int] = {}
    school = cs.SCHOOL_BY_ID.get(str(ch.get("school") or "")) or {}
    bonus = school.get("bonus") or {}
    raw = ch.get("attrs") or {}
    for key, every in BORN_ON_LEVEL.items():
        n = sum(1 for lv in range(int(old_level) + 1, new_level + 1) if lv % every == 0)
        if n <= 0:
            continue
        cap = int(next(a["cap"] for a in cs.ATTRS if a["key"] == key))
        base = int(raw.get(key, next(a["init"] for a in cs.ATTRS if a["key"] == key)))
        room = max(0, cap - base - int(bonus.get(key, 0)) - int(born.get(key, 0)))
        add = min(n, room)
        if add:
            born[key] = int(born.get(key, 0)) + add
            gained[key] = add
    ch["born"] = born
    return gained


def exp_for(minutes: int) -> int:
    """陪读多少分钟换多少修为。"""
    return max(0, int(minutes)) * EXP_PER_MINUTE


def award_minutes(ch: dict[str, Any], minutes: int, *, skill_gain: bool = True,
                  grant_exp: bool = True) -> dict[str, Any]:
    """记一次陪读, 返回这次的变化明细。

    ``ch`` 是**就地改**的存档字典 (调用方负责 save)。返回:
        ``{"minutes": n, "exp": n, "levels": n, "pot": n, "fame": n,
           "skills": {id: 增益等级}, "vein": n}``

    ``grant_exp=False``: 只累计分钟 / 长技能 / 通经脉, **不给修为**。
    阅读器那条路现在走这个 —— 修为改由后台推演按读书记录判定
    (2026-11 起的口径: 分钟是证据之一, 不是唯一换算), 两处同时给会双计。

    规矩: 只增不减。传 0 或负数直接原样返回, 不报错 (调用方是定时器, 不该因
    一次空转就炸)。
    """
    n = max(0, min(int(minutes or 0), MAX_MINUTES_PER_CALL))
    if n <= 0:
        return {"minutes": 0, "exp": 0, "levels": 0, "pot": 0, "fame": 0,
                "skills": {}, "vein": int(ch.get("vein") or 0)}

    before_level = int(ch.get("level") or 1)
    before_exp = int(ch.get("exp") or 0)
    before_vein = int(ch.get("vein") or 0)
    before_fame = int(ch.get("fame") or 0)

    # 累计陪读分钟 (经脉按它推进)
    total_min = int(ch.get("minutes") or 0) + n
    ch["minutes"] = total_min
    # credited_minutes 与 minutes 是同一个数的两个名字 —— 手动喂分钟也要同步推
    # (否则手动喂的分钟不被"数据源折算"认账, 幂等锚点会落后)
    ch["credited_minutes"] = total_min

    # 修为
    gain = exp_for(n) if grant_exp else 0
    ch["exp"] = before_exp + gain

    # 升级 (可能连升)
    levels = 0
    while gain:
        need = cs.exp_needed(int(ch.get("level") or 1))
        if int(ch["exp"]) < need:
            break
        ch["exp"] = int(ch["exp"]) - need
        ch["level"] = int(ch.get("level") or 1) + 1
        levels += 1
    born_gain: dict[str, int] = {}
    if levels:
        ch["pot"] = int(ch.get("pot") or 0) + POT_PER_LEVEL * levels
        ch["fame"] = int(ch.get("fame") or 0) + FAME_PER_LEVEL * levels
        born_gain = _apply_born(ch, before_level)

    # 陪读自动长「读书悟道」—— 按累计分钟算, 不是按这次 (每 N 分钟整一格)
    skill_gain_map: dict[str, int] = {}
    if skill_gain and AUTO_SKILL in cs.SKILL_BY_ID:
        skills = ch.get("skills") or {}
        want = total_min // AUTO_SKILL_MINUTES_PER_LEVEL
        cur = int(skills.get(AUTO_SKILL, 0))
        if want > cur:
            skills[AUTO_SKILL] = want
            ch["skills"] = skills
            skill_gain_map[AUTO_SKILL] = want - cur

    # 经脉: 累计分钟够就把层数推上去
    vein = before_vein
    for v in cs.VEINS:
        if total_min >= int(v["minutes"]):
            vein = max(vein, int(v["idx"]))
        else:
            break
    if vein != before_vein:
        ch["vein"] = vein

    return {
        "minutes": n,
        "exp": gain,
        "levels": levels,
        "pot": POT_PER_LEVEL * levels,
        "fame": int(ch.get("fame") or 0) - before_fame,
        "skills": skill_gain_map,
        "born": born_gain,
        "vein": vein,
        "level": int(ch.get("level") or 1),
        "total_minutes": total_min,
    }


def credit_reading_minutes(ch: dict[str, Any], total_minutes: int, *,
                           grant_exp: bool = True) -> dict[str, Any]:
    """把"阅读器那边累计的陪读分钟"折算成修为, **只喂增量**。

    为什么要有这个函数而不是直接调 :func:`award_minutes`:
    ``award_minutes`` 语义是"这次又陪读了 N 分钟"(增量口径), 而阅读器那边
    手里的是一个**累计值**。直接拿累计值去 award, 每次都会把历史重算一遍
    —— 修为会变成平方级地涨。

    所以这里用 ``credited_minutes`` 当**幂等锚点**: 存档里记着"已经折算过
    多少分钟", 只把 (累计 − 已折算) 这段新增量喂进去。

    这样:
      · 重复调用不会重复给修为 (幂等)
      · 只增不减 —— 累计值倒退时 (换库 / 删书重扫) 一分钱都不补
      · 锚点是存档里的一个数, 不依赖任何外部状态

    **``credited_minutes`` 与 ``minutes`` 是同一个数** (两个名字, 一个含义):
    "已经折算过的陪读分钟"。``award_minutes`` 单独用 (手动喂分钟) 时也会
    把两个一起推 —— 之前它们各写各的, 数据源被清理时就分叉了, 页面显示
    的"陪读 N 分钟"跟陪读卡里的"累计 N 分钟"对不上 (真踩过)。

    """
    total = max(0, int(total_minutes or 0))
    credited = max(0, int(ch.get("credited_minutes") or 0))
    delta = total - credited
    if delta <= 0:
        return {"minutes": 0, "delta": 0, "credited": credited, "grew": False,
                "level": int(ch.get("level") or 1),
                "total_minutes": int(ch.get("minutes") or 0)}

    # 分批喂: award_minutes 有单次封顶 (MAX_MINUTES_PER_CALL), 一次喂完
    # 大 delta 会被截断 —— 锚点却推到了累计值, 中间那段就凭空丢了
    # (真踩过: 首次折算 724 分钟, 只给了 480, 少 244)。
    # 所以这里循环喂, 每批不超过封顶, 批完为止。
    fed = 0
    levels = 0
    exp_gain = 0
    last: dict[str, Any] = {}
    while fed < delta:
        step = min(delta - fed, MAX_MINUTES_PER_CALL)
        last = award_minutes(ch, step, grant_exp=grant_exp)
        fed += step
        exp_gain += int(last.get("exp") or 0)
        levels += int(last.get("levels") or 0)
    # 锚点推到**实际喂进去的量** (credited + fed), 不是 total。
    # 差别在于: 万一某批 award 失败/被截断, 锚点不会跳到没喂到的位置 ——
    # 下一轮还能补上。(真踩过: 首次折算 724 分钟被单次封顶截成 480,
    # 但锚点推到了 724 → 中间 244 分钟永远补不回来。)
    ch["credited_minutes"] = credited + fed
    # credited 与 minutes 是同一个数的两个名字 —— 保持一致
    ch["minutes"] = ch["credited_minutes"]
    return {"minutes": fed, "delta": delta, "credited": ch["credited_minutes"],
            "grew": True,
            "exp": exp_gain, "levels": levels,
            "level": int(ch.get("level") or 1),
            "total_minutes": int(ch.get("minutes") or 0)}


def skill_boost(ch: dict[str, Any], skill_id: str, levels: int = 1) -> dict[str, Any]:
    """手动长一门功夫 (研讨用)。等级不许超过当前上限。"""
    sk = cs.SKILL_BY_ID.get(skill_id or "")
    if not sk:
        return {"ok": False, "error": f"未知技能: {skill_id}"}
    if levels <= 0:
        return {"ok": False, "error": "等级必须为正"}
    skills = ch.get("skills") or {}
    cur = int(skills.get(skill_id, 0))
    cap = min(int(sk.get("cap", 30)),
              cs.skill_cap(int(ch.get("exp") or 0),
                           cs.effective_attrs(ch).get(sk.get("attr", "wu"), 10)))
    if cur + levels > cap:
        return {"ok": False, "error": f"{sk['name']} 已达上限 {cap}"}
    skills[skill_id] = cur + levels
    ch["skills"] = skills
    return {"ok": True, "skill": skill_id, "level": cur + levels, "cap": cap}


# ── 模型判定 (后台 skill 给数, 这里只负责落地与夹逼) ─────────────

#: 一回合最多给多少修为 (模型再兴奋也不越过)
MAX_EXP_PER_CYCLE = 3000
#: 一回合最多给几点潜能 / 声望
MAX_POT_PER_CYCLE = 3
MAX_FAME_PER_CYCLE = 20
#: 一回合最多长几门功夫 (每次每门 +1 级)
#: 一回合 (≈ 一小时阅读 ≈ 故事内一天) 只许长一门 —— 慢成长的关键闸门之一
MAX_SKILLS_PER_CYCLE = 1


def award_judged(ch: dict[str, Any], j: dict[str, Any], *,
                 max_exp: int | None = None,
                 story_day: int | None = None) -> dict[str, Any]:
    """把模型判定的成长落进存档 (就地改 ``ch``)。

    与 :func:`award_minutes` 的区别: 那个是"按分钟换算"的机械口径, 这个是
    "读书记录 → 判断"的**判定口径** (分钟只是其中一项证据)。所以:

      · 修为/潜能/声望/心性都由外部给数, 本函数**只做夹逼**, 不自己算比例
      · 只增不减: 负数一律当 0
      · 升级循环与 ``award_minutes`` 同一套 (修为够就升, 连升也算)
      · 技能: 每门最多 +1 级, 且不超过 ``skill_cap`` 上限
      · 性情: 不在这里授予 (要走 ``charsheet.grant_trait`` 校验条数/点数),
        这里只把建议值原样带回去让调用方决定

    返回这次的变化明细 (含被夹掉的量, 便于排查"为什么只涨了这么点")。
    """
    cap_exp = MAX_EXP_PER_CYCLE if max_exp is None else max(0, int(max_exp))
    raw_exp = int(j.get("exp") or 0)
    add_exp = max(0, min(raw_exp, cap_exp))
    add_pot = max(0, min(int(j.get("pot") or 0), MAX_POT_PER_CYCLE))
    add_fame = max(0, min(int(j.get("fame") or 0), MAX_FAME_PER_CYCLE))
    add_mor = max(-5, min(5, int(j.get("morality") or 0)))

    lvl_before = int(ch.get("level") or 1)
    before = {"level": int(ch.get("level") or 1), "exp": int(ch.get("exp") or 0),
              "pot": int(ch.get("pot") or 0), "fame": int(ch.get("fame") or 0),
              "morality": int(ch.get("morality") or 0)}

    ch["exp"] = before["exp"] + add_exp
    levels = 0
    while True:
        need = cs.exp_needed(int(ch.get("level") or 1))
        if int(ch["exp"]) < need:
            break
        ch["exp"] = int(ch["exp"]) - need
        ch["level"] = int(ch.get("level") or 1) + 1
        levels += 1

    born_gain: dict[str, int] = {}
    if levels:
        add_pot += POT_PER_LEVEL * levels
        add_fame += FAME_PER_LEVEL * levels
        born_gain = _apply_born(ch, lvl_before)
    ch["pot"] = before["pot"] + add_pot
    ch["fame"] = max(0, before["fame"] + add_fame)
    ch["morality"] = max(-100, min(100, before["morality"] + add_mor))

    # 技能 (最多 MAX_SKILLS_PER_CYCLE 门, 每门 +1) —— 且受"每 N 个故事日一级"限速。
    # 限速为什么必要: 一回合 ≈ 故事内一天, 没有这道闸门, 模型每回合都会挑一门给 +1,
    # 一个月就把技能堆到上限 —— 招式等级一高, 故事里"长本事"这件事就不值钱了。
    gained: dict[str, int] = {}
    gate: dict[str, Any] = {}
    try:
        from recto.pet import life as _life

        day = int(story_day if story_day is not None else _life.story_day(ch))
        last = int(ch.get("last_skill_day") or 0)
        gate = {"day": day, "last": last, "days_per_level": SKILL_DAYS_PER_LEVEL,
                "blocked": (day - last) < SKILL_DAYS_PER_LEVEL}
    except Exception:  # noqa: BLE001
        gate = {"blocked": False}
    skills_in = j.get("skills") or {}
    if isinstance(skills_in, dict) and not gate.get("blocked"):
        for sid, lv in list(skills_in.items())[:MAX_SKILLS_PER_CYCLE]:
            sk = cs.SKILL_BY_ID.get(str(sid))
            if not sk or int(lv or 0) <= 0:
                continue
            cur = int((ch.get("skills") or {}).get(str(sid), 0))
            cap = min(int(sk.get("cap", 30)),
                      cs.skill_cap(int(ch.get("exp") or 0),
                                   cs.effective_attrs(ch).get(sk.get("attr", "wu"), 10)))
            if cur + 1 > cap:
                continue
            ch.setdefault("skills", {})[str(sid)] = cur + 1
            ch["last_skill_day"] = int(gate.get("day") or 0)
            gained[str(sid)] = cur + 1
            break

    return {
        "exp": add_exp, "levels": levels,
        "pot": add_pot, "fame": add_fame, "morality": add_mor,
        "skills": gained, "trait": str(j.get("trait") or ""),
        "born": born_gain,
        "skill_gate": gate,
        "clamped": {"exp": raw_exp != add_exp,
                    "exp_raw": raw_exp, "exp_cap": cap_exp},
        "level": int(ch.get("level") or 1), "exp_now": int(ch["exp"]),
    }
