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
  · 技能: ① 陪读时长自动长「基本内功」(读书如打坐 —— 内力是慢功夫);
         ② **真用过的功夫**按拍数攒熟练度 (出手 / 绝招 / 拆招 / 过坎);
         ③ 后台判词点名的那一门再加一笔。熟练度满 ``PROF_PER_LEVEL`` 升一级,
         上限只看等级 (``charsheet.skill_cap``)。

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
#: 陪读时长自动长哪门功夫。
#: 选「基本内功」而不是某路兵器: 坐着读书长的是内力底子, 不是剑法 ——
#: 兵刃与绝招要靠真出门、真交手, 由判定那一步给 (见 chronicle.md)。
AUTO_SKILL = "jibenneigong"
#: AUTO_SKILL 每多少分钟长 1 级 (现实分钟, 与故事日无关)。
#: 300 分钟 = 五小时一级 —— 慢到"翻好几天书才长一层"。
AUTO_SKILL_MINUTES_PER_LEVEL = 300
#: ── 熟练度 (2026-11-16 起技能成长的主路径) ────────────────────────
#: 技能等级只由熟练度决定: 满 ``PROF_PER_LEVEL`` 升一级 (与 charsheet 同一口径)。
#: 熟练度从两处来 —— ① 这一趟**真用过的功夫** (出手/绝招/拆招/过坎)
#: ② 后台判词点名的那一门 ("这一趟悟到了什么")。判定不再直接给一整级。
PROF_PER_LEVEL = cs.PROF_PER_LEVEL

#: 计一次"练手"折多少熟练度。数字的来处:
#: 一场十来拍的遭遇 ≈ 36 (出手) + 12 (绝招) + 10 (拆招) ≈ 58,
#: 两道坎 ≈ 15 —— 一趟合计 ≈ 70~90, 摊到四五门功夫上, 主修那门约 2~3 趟升一级。
#: 一小时阅读 = 十趟 ≈ 主修涨 3~4 级 (受等级上限约束)。
PRACTICE = {
    "attack": 3,     # 自己出手一拍 (主修的兵刃 / 拳脚)
    "perform": 4,    # 使一次绝招
    "defend": 1,     # 被攻一拍 (基本招架 / 轻功拆招)
    "check_ok": 9,   # 过了一道坎
    "check_no": 6,   # 没过 —— 败也是练, 只是少些
    "judged": 25,    # 判词点名的那一门 (说书人说"这一趟悟到了")
}
#: 一趟里**同一门功夫**最多攒多少实战熟练度 (防"一场车轮战直接把上限打满")
PER_SKILL_PER_CYCLE_CAP = 50

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
    "prof_per_level": PROF_PER_LEVEL,
    "practice": dict(PRACTICE),
    "practice_cap_per_skill": PER_SKILL_PER_CYCLE_CAP,
    "skill_cap_by_level": "技能上限 = f(等级), 见 charsheet.skill_cap —— 与修为零头无关",
    "born_on_level": dict(BORN_ON_LEVEL),
    "max_minutes_per_call": MAX_MINUTES_PER_CALL,
    "one_way": True,
    "time": {
        "minutes_per_story_day": None,   # 由 pet.life 填 (默认 0.6 分钟 = 故事内一天)
        "start_age": None,               # 12 岁出江湖
        "retire_age": None,              # 80 岁退隐 → 等读者确认传承
    },
    # 判定口径 (后台 skill 给数, 代码只夹逼): 每回合上限
    "judged_caps": {"exp": 3000, "pot": 3, "fame": 20, "skills": 1,
                    "morality": 5, "trait": 1},
    "combat": {
        "note": "属性 / 技能 / 绝招在遭遇里真的被用到: 命中看身法与主修, "
                "伤害看臂力与兵刃, 绝招吃内力、会反噬。",
        "pfm_effects": ["damage", "control", "defend", "evade", "heal", "buff"],
    },
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
        out["time"].update({"minutes_per_story_day": round(float(c["minutes_per_day"]), 2),
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

    # 陪读自动长「基本内功」—— 按累计分钟算, 不是按这次 (每 N 分钟整一格)
    # 但**仍然受等级上限约束** (2026-11-16 上限改成与等级挂钩之后, 一条老账
    # 可能让内功"读着读着超过上限": 3900 分钟 = 13 级, 而 5 级的人只许练到 6 级)
    skill_gain_map: dict[str, int] = {}
    if skill_gain and AUTO_SKILL in cs.SKILL_BY_ID:
        skills = ch.get("skills") or {}
        want = total_min // AUTO_SKILL_MINUTES_PER_LEVEL
        cur = int(skills.get(AUTO_SKILL, 0))
        cap = cs.cap_for(ch, cs.SKILL_BY_ID[AUTO_SKILL])
        want = max(cur, min(int(want), int(cap)))
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
    # 基线: 复位 / 转生那一刻的累计时长。""历史""不该被新一代继承,
    # 只有基线之后的阅读才喂给它 (否则复位第二秒就长回原样)。
    base = max(0, int(ch.get("reading_baseline") or 0))
    total = max(base, int(total_minutes or 0)) - base
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


def settle_levels(ch: dict[str, Any]) -> dict[str, Any]:
    """把口袋里攒着的修为兑现成等级 (就地改 ``ch``)。

    为什么要有这一步: 升级循环一直只跑在"这一回合新给了多少修为"里。换算曲线一改
    (2026-11-16 从 ``100*level^2`` 改成 ``24+10*(level-1)``), 存量修为就悬在半空 ——
    界面上写着"修为 701 / 升级需 54", 等级却还是 4。这里是幂等的兑现入口:
    够了就升, 顺带补潜能/声望/先天属性, 不够就一个数都不动。
    """
    lvl_before = int(ch.get("level") or 1)
    levels = 0
    while True:
        need = cs.exp_needed(int(ch.get("level") or 1))
        if int(ch.get("exp") or 0) < need:
            break
        ch["exp"] = int(ch.get("exp") or 0) - need
        ch["level"] = int(ch.get("level") or 1) + 1
        levels += 1
    if not levels:
        return {"levels": 0, "level": lvl_before, "pot": 0, "fame": 0, "born": {}}
    ch["pot"] = int(ch.get("pot") or 0) + POT_PER_LEVEL * levels
    ch["fame"] = int(ch.get("fame") or 0) + FAME_PER_LEVEL * levels
    born = _apply_born(ch, lvl_before)
    return {"levels": levels, "level": int(ch["level"]),
            "pot": POT_PER_LEVEL * levels, "fame": FAME_PER_LEVEL * levels,
            "born": born}


def skill_boost(ch: dict[str, Any], skill_id: str, levels: int = 1) -> dict[str, Any]:
    """手动长一门功夫 (研讨用)。等级不许超过当前上限。"""
    sk = cs.SKILL_BY_ID.get(skill_id or "")
    if not sk:
        return {"ok": False, "error": f"未知技能: {skill_id}"}
    if levels <= 0:
        return {"ok": False, "error": "等级必须为正"}
    skills = ch.get("skills") or {}
    cur = int(skills.get(skill_id, 0))
    cap = cs.cap_for(ch, sk)
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
#: 一回合最多长几门功夫 (由判词点名)
#: 一回合只许判词点一门 —— 多的交给"实战练到的"去累积, 那是另一条路。
MAX_SKILLS_PER_CYCLE = 1


# ── 熟练度: 把"这一趟练了什么"落到实处 ───────────────────────────

def _skill_row(sid: str, res: dict[str, Any], why: list[str],
               before: dict[str, int]) -> dict[str, Any]:
    """一条熟练度记录 (给页面看: 涨了多少 / 现在到哪 / 升没升级)。"""
    sk = cs.SKILL_BY_ID.get(str(sid)) or {}
    lv0 = int(before.get(str(sid), 0))
    return {
        "id": str(sid),
        "name": sk.get("name") or str(sid),
        "gain": int(res.get("added") or 0),
        "level": int(res.get("level") or lv0),
        "leveled": int(res.get("leveled") or 0),
        "from": lv0,
        "prof": int(res.get("prof") or 0),
        "prof_need": PROF_PER_LEVEL,
        "cap": int(res.get("cap") or 0),
        "blocked": str(res.get("blocked") or ""),
        "why": list(why),
    }


def practice_from(ch: dict[str, Any], duel: dict[str, Any] | None = None,
                  checks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """把这一趟**真用过的功夫**折成熟练度 (就地改 ``ch``)。

    为什么要这一步: 旧口径里技能只由后台"点名 +1 级"推动, 而战报里明明写着
    这一场使了十二剑、那一道坎用的是追踪 —— 读者却看不到「剑法」动过一格。
    判定掷了什么, 就该长什么。

    量: 出手 3 / 绝招 4 / 拆招 1 / 过坎 9 / 没过 6, 每门一趟封顶
    ``PER_SKILL_PER_CYCLE_CAP``; 满 ``PROF_PER_LEVEL`` 升一级, 受等级上限约束。
    """
    before = dict(ch.get("skills") or {})
    detail: dict[str, dict[str, Any]] = {}

    def add(sid: Any, n: int, why: str) -> None:
        sid = str(sid or "")
        if not sid or sid not in cs.SKILL_BY_ID or int(n) <= 0:
            return
        row = detail.setdefault(sid, {"gain": 0, "why": []})
        room = max(0, PER_SKILL_PER_CYCLE_CAP - int(row["gain"]))
        g = min(int(n), room)
        if g <= 0:
            return
        row["gain"] = int(row["gain"]) + g
        if why not in row["why"]:
            row["why"].append(why)

    if duel:
        usage = dict(duel.get("usage") or {})
        turns = int(usage.get("turns") or 0)
        add(usage.get("weapon"), turns * PRACTICE["attack"], "交手")
        for sid, n in (usage.get("perform") or {}).items():
            add(sid, int(n) * PRACTICE["perform"], "绝招")
        add("jibenzhaojia", int(usage.get("defended") or 0) * PRACTICE["defend"], "拆招")

    for c in (checks or []):
        n = PRACTICE["check_ok"] if c.get("success") else PRACTICE["check_no"]
        add(c.get("skill"), n, "路上")

    rows, levels, total = [], [], 0
    for sid, row in detail.items():
        res = cs.add_practice(ch, sid, int(row["gain"]))
        if not int(res.get("added") or 0):
            continue
        total += int(res["added"])
        item = _skill_row(sid, res, row["why"], before)
        rows.append(item)
        if item["leveled"]:
            levels.append({"id": sid, "name": item["name"],
                           "from": item["from"], "to": item["level"]})
    rows.sort(key=lambda r: (-int(r["leveled"]), -int(r["gain"]), r["name"]))
    return {"skills": rows, "levels": levels, "total": total}


def practice_judged(ch: dict[str, Any], skills: Any) -> dict[str, Any]:
    """判词点名的那一门攒一笔熟练度 (最多 ``MAX_SKILLS_PER_CYCLE`` 门)。

    为什么不再直接 +1 级: 等级是"练出来的", 判词只是**加一笔**。这样等级表里
    每一格背后都有可查的熟练度, 而不是"说书人一句话就涨一级"。
    """
    before = dict(ch.get("skills") or {})
    picked: dict[str, int] = {}
    if isinstance(skills, dict):
        for sid, lv in list(skills.items())[:MAX_SKILLS_PER_CYCLE]:
            if str(sid) in cs.SKILL_BY_ID and int(lv or 0) > 0:
                picked[str(sid)] = PRACTICE["judged"]
    rows, levels = [], []
    for sid, n in picked.items():
        res = cs.add_practice(ch, sid, n)
        item = _skill_row(sid, res, ["判定"], before)
        rows.append(item)
        if item["leveled"]:
            levels.append({"id": sid, "name": item["name"],
                           "from": item["from"], "to": item["level"]})
    return {"skills": rows, "levels": levels,
            "total": sum(int(r["gain"]) for r in rows)}


#: 没对手的「学」类事: 专门磨招。兵器与绝招各记一笔 —— 绝招只有在交手时才
#: 记得上熟练度, 而交手本来就少 (读书的多半时间是坐着看字), 于是绝招永远停在
#: 一级、永远只使得出同一个数字。给它一条"自己练"的路。
DRILL = {"perform": 26, "weapon": 20}


def drill(ch: dict[str, Any], *, school: str | None = None) -> dict[str, Any]:
    """磨招: 把本门绝招与主修兵器各练一笔 (就地改 ``ch``)。

    挑哪几门: 已经在册的绝招 (``skills`` 里 type=perform 且等级 > 0) 各一份,
    加主修兵器 (最高等级的那门 weapon) 一份。全都没有就什么都不练 ——
    不替它凭空点出一门没学过的功夫。
    """
    before = dict(ch.get("skills") or {})
    picks: dict[str, int] = {}
    skills = dict(ch.get("skills") or {})
    for sid, lv in skills.items():
        sk = cs.SKILL_BY_ID.get(str(sid)) or {}
        if sk.get("type") == "perform" and int(lv or 0) > 0:
            picks[str(sid)] = DRILL["perform"]
    weapons = [(int(lv or 0), str(sid)) for sid, lv in skills.items()
               if (cs.SKILL_BY_ID.get(str(sid)) or {}).get("type") == "weapon"]
    if weapons:
        weapons.sort(reverse=True)
        picks[weapons[0][1]] = max(picks.get(weapons[0][1], 0), DRILL["weapon"])

    rows, levels, total = [], [], 0
    for sid, n in picks.items():
        res = cs.add_practice(ch, sid, int(n))
        if not int(res.get("added") or 0):
            continue
        total += int(res["added"])
        item = _skill_row(sid, res, ["磨招"], before)
        rows.append(item)
        if item["leveled"]:
            levels.append({"id": sid, "name": item["name"],
                           "from": item["from"], "to": item["level"]})
    rows.sort(key=lambda r: (-int(r["leveled"]), -int(r["gain"]), r["name"]))
    return {"skills": rows, "levels": levels, "total": total}

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

    # 技能: 判词点名的那一门只**加熟练度**, 不再直接给一整级 (2026-11-16 改口径)。
    # 旧口径是"每 300 个故事日许升一级、每回一门 +1" —— 与"真练了什么"无关,
    # 而且闸门一挡, 读者连读十几回也看不到本事动。现在两条路并行:
    #   · 判词点名: 一笔 25 点熟练度 (说书人说"这一趟悟到了什么")
    #   · 实战练手: 出手/绝招/拆招/过坎, 见 practice_from()
    # 满 charsheet.PROF_PER_LEVEL 升一级, 上限只看等级。
    prac = practice_judged(ch, j.get("skills") or {})
    gained = {str(r["id"]): int(r["to"]) for r in prac.get("levels") or []}

    return {
        "exp": add_exp, "levels": levels,
        "pot": add_pot, "fame": add_fame, "morality": add_mor,
        "skills": gained, "trait": str(j.get("trait") or ""),
        "born": born_gain,
        "practice": prac,
        "skill_gate": {"by": "practice", "prof_per_level": PROF_PER_LEVEL,
                       "practice_cap": PER_SKILL_PER_CYCLE_CAP},
        "clamped": {"exp": raw_exp != add_exp,
                    "exp_raw": raw_exp, "exp_cap": cap_exp},
        "level": int(ch.get("level") or 1), "exp_now": int(ch["exp"]),
    }
