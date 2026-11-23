"""内功 · 运功 (exert) —— 门派内功不再是装饰。

跑法: cd <仓库根> && python3 tests/test_pet_neigong.py

为什么要这一份 (2026-11-23 用户报「门派内功从来没使用, 与侠客行 MUD 逻辑不吻合」):

  * **启用一台内功** (侠客行 ``enable force``): 内力上限看**那一门**的等级。
    旧实现取"所有 type==neigong 的最大值", 而基本内功的 type 是 ``basic`` ——
    读书自动长起来的 11 级基本内功对内力上限**一点贡献都没有**, 而门派内功又
    停在入门 1 级, 内力上限从此冻死。现在: 门派内功为主体, 基本内功给薄底子。
  * **本门内功各管一路**: SKILLS 的 ``use`` 写着"剑上更快一分 / 越练手越沉 /
    落雪不陷…", 旧代码一条都没落地。现在进 d20 拆账 (``src="neigong"``)。
  * **运功 (exert) 两式**: 加力 (powerup, 三拍) 与回气 (recover, 占一拍)。
    这是内功**唯一被用掉**的地方, 也是它唯一能攒熟练度的实战来路。
  * 打坐 (gear.selfcare) 也算一笔 —— 否则没有交手的回合内功完全不长。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = Path(tempfile.mkdtemp(prefix="recto-pet-neigong-"))
os.environ["RECTO_DATA_DIR"] = str(TMP / "data")
os.environ["RECTO_LIBRARY_DIR"] = str(TMP / "library")
os.environ["RECTO_PET_DIR"] = str(TMP / "data" / "pet")
os.environ["RECTO_PET_LLM_BORROW"] = "off"
os.environ["RECTO_AUTH_LOG"] = "off"
(TMP / "library").mkdir(parents=True, exist_ok=True)

from recto.pet import charsheet as cs  # noqa: E402
from recto.pet import chronicle as chron  # noqa: E402
from recto.pet import duel  # noqa: E402
from recto.pet import gear  # noqa: E402
from recto.pet import growth  # noqa: E402

PASS = FAIL = 0


def ok(cond, name):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS", name)
    else:
        FAIL += 1
        print("FAIL", name)


def mk(skills=None, school="fengxue", level=12):
    ch = cs.new_char()
    ch["name"] = "小墨"
    ch["level"] = level
    ch["attrs"] = {"li": 14, "gen": 13, "wu": 13, "shen": 15, "ding": 12, "na": 12}
    ch["school"] = school
    ch["skills"] = dict(skills if skills is not None else
                        {"jibenneigong": 5, "taxuegong": 1, "jibenzhaojia": 6,
                         "jibenshenfa": 6, "qinggong": 3, "tuifa": 6,
                         "taxuewuchen": 2})
    ch["prof"] = {}
    ch["hp"] = ch["neili"] = None
    return ch


# ── 1. 已启用那门内功 = 门派内功; 基本内功只给薄底子 ────────────
feng = mk({"jibenneigong": 5, "taxuegong": 1})
ok(cs._neigong_level(feng) == 1, f"启用的是本门内功 (踏雪功 1 级) got {cs._neigong_level(feng)}")
ok(cs._basic_neigong(feng) == 10, f"基本内功 5 级给 +10 底子 got {cs._basic_neigong(feng)}")
free = mk({"jibenneigong": 5, "guanhai": 3, "taxuegong": 1}, school="")
ok(cs._neigong_level(free) == 3, f"没有门派时启用等级最高的那门 got {cs._neigong_level(free)}")
ok(cs._basic_neigong(mk({"jibenneigong": 40})) == 40, "基本内功底子封顶 +40")

d1 = cs.derive(mk({"jibenneigong": 5, "taxuegong": 1}))
d2 = cs.derive(mk({"jibenneigong": 5, "taxuegong": 6}))
ok(d2["neili_max"] > d1["neili_max"],
   f"本门内功升级 → 内力上限涨 ({d1['neili_max']} → {d2['neili_max']})")
ok(d2["neili_max"] - d1["neili_max"] >= 40,
   f"每级本门内功至少 +10 内力 (另加 +3 精纯) got {d2['neili_max'] - d1['neili_max']}")
ok(d1["neili_max"] > cs.derive(mk({"jibenneigong": 0, "taxuegong": 1}))["neili_max"],
   "基本内功也不是白练的 (给一份薄底子)")

# ── 2. 本门内功各管一路 (拆账里看得见) ────────────────────────
def parts_of(stats, key="attack_parts"):
    return [p for p in (stats.get(key) or []) if p.get("src") == "neigong"]


mo = mk({"jibenneigong": 3, "guanhai": 8, "jianfa": 6}, school="mochi")
st = cs.d20_stats(mo)
ok(any("观海诀" in p["name"] for p in parts_of(st)),
   f"观海诀 (剑庐) 的运气加成进命中拆账 got {parts_of(st)}")
mo_axe = mk({"jibenneigong": 3, "guanhai": 8, "daofa": 6}, school="mochi")
ok(not parts_of(cs.d20_stats(mo_axe)),
   "观海诀**只在使剑时**才给 (使刀时不算) —— 与 SKILLS 的 use 一致")
ok(cs.neigong_effects(mo_axe).get("off"),
   "用不上时 neigong_effects 说明原因 (off 字段)")

tie = mk({"jibenneigong": 3, "monengong": 6, "daofa": 6}, school="tieyan")
base_flat = cs.d20_stats(mk({"jibenneigong": 3, "monengong": 0, "daofa": 6},
                            school="tieyan"))["damage"]["flat"]
ok(cs.d20_stats(tie)["damage"]["flat"] > base_flat,
   f"磨墨功 (铁砚堂) 抬刀法伤害 {base_flat} → {cs.d20_stats(tie)['damage']['flat']}")

feng2 = mk({"jibenneigong": 3, "taxuegong": 6})
ok(any("踏雪功" in p["name"] for p in parts_of(cs.d20_stats(feng2), "ac_parts")),
   "踏雪功 (风雪阁) 抬闪避 → 进护体拆账")
_init_parts = ((cs.d20_stats(feng2).get("init") or {}).get("parts") or [])
ok(any("踏雪功" in p["name"] and p.get("src") == "neigong" for p in _init_parts),
   f"踏雪功同时给先手 got {_init_parts}")

jing = mk({"jibenneigong": 3, "yangqijue": 6}, school="jingxin")
ok(any("养气诀" in p["name"] for p in parts_of(cs.d20_stats(jing), "saves" and None) or
       parts_of((cs.d20_stats(jing)["saves"] or {}).get("gen") or {}, "parts")),
   "养气诀 (静心斋) 抬根骨豁免 (定身那一掷)")

song = mk({"jibenneigong": 3, "woyungong": 6}, school="tingsong")
ok(cs.derive(song)["hp_max"] > cs.derive(mk({"jibenneigong": 3}, school="tingsong"))["hp_max"],
   "卧云功 (听松府) 抬气血上限")
ling = mk({"jibenneigong": 3, "tongmingjue": 6, "zhifa": 6}, school="lingxi")
ok(cs.d20_stats(ling)["attack"] > cs.d20_stats(
    mk({"jibenneigong": 3, "zhifa": 6}, school="lingxi"))["attack"],
   "通明诀 (灵犀谷) 抬命中")

# ── 3. 运功口径 ──────────────────────────────────────────
spec = cs.exert_spec(feng)
ok(spec.get("sid") == "taxuegong" and spec.get("sect") is True,
   f"运功用**本门内功** got {spec.get('sid')}")
ok(spec["power"]["pct"] >= 1 and spec["power"]["cost"] > 0,
   "加力有百分比与内力开销")
ok(spec["recover"]["hp"] >= 3 and spec["recover"]["limit"] >= 1, "回气有回血量与次数上限")
no_school = mk({"jibenneigong": 4}, school="")
ok(cs.exert_spec(no_school).get("sid") == "jibenneigong",
   "没有门派 → 运功用基本内功 (不会空)")

# 回气要"够得着"才准用: 入门 1 级时内功太浅, 赌不起那一拍
def chance(spec, level):
    from recto.pet import d20 as D
    bonus = D.skill_mod(level) + 1
    return bonus + 10 >= spec["recover"]["dc"]

ok(not chance(spec, 1), "1 级内功时**不准**回气 (加值+10 够不到难度)")
ok(chance(cs.exert_spec(mk({"jibenneigong": 3, "taxuegong": 12})), 12),
   "12 级内功时才可以回气 (是练出来的能力)")

# ── 4. 交手里真的运功, 且加的伤害只撑三拍 ──────────────────
fight_ch = mk({"jibenneigong": 5, "taxuegong": 6, "tuifa": 8,
               "taxuewuchen": 3, "jibenzhaojia": 6, "jibenshenfa": 6, "qinggong": 4})
seen_power = 0
decay_ok = False
for seed in range(40):
    c = json.loads(json.dumps(fight_ch))
    c["hp"] = None
    c["neili"] = None
    out = duel.resolve(c, terms=[], seed=seed * 977 + 3)
    ex = out.get("exert") or {}
    if int(ex.get("power") or 0):
        seen_power += 1
        # 加力的那几拍: 找 hit 事件的 buff 字段
        buffs = [e.get("dice") or {} for e in out["rounds"] if e.get("kind") == "hit"
                 and e.get("side") == "me"]
        used = [1 for _ in buffs]
        if len(used) >= 5:
            decay_ok = True
ok(seen_power >= 10, f"四十场里至少十场加力成功 got {seen_power}")
ok(decay_ok, "交手里出现过加力 (有足够的出手拍数去观察)")

# 三拍衰减: 直接验 _damage_roll
from recto.pet import d20 as D  # noqa: E402
import random as _random  # noqa: E402
atk = {"damage": {"count": 1, "sides": 6, "flat": 10}, "dmg_buff": 5,
       "dmg_buff_until": 3, "pierce": 0}
r1 = duel._damage_roll(_random.Random(1), atk, {"guard": 0.0, "pierce": 0}, False, 3)
r2 = duel._damage_roll(_random.Random(1), atk, {"guard": 0.0, "pierce": 0}, False, 4)
ok(r1["buff"] == 5 and r2["buff"] == 0, f"加力只撑三拍 got {r1['buff']} / {r2['buff']}")

# ── 5. 运功 → 内功熟练度 (唯一实战来路) ────────────────────
c = json.loads(json.dumps(fight_ch))
c["hp"] = c["neili"] = None
out = duel.resolve(c, terms=[], seed=31)
usage = out.get("usage") or {}
ok(str(usage.get("exert_sid")) == "taxuegong", "usage 里带运功用的是哪一门")
ok(int(usage.get("exert", {}).get("taxuegong", 0)) or int(usage.get("exert_fail") or 0),
   f"运功次数被记下来 got {usage.get('exert')} / fail {usage.get('exert_fail')}")
before = int(c.get("prof", {}).get("taxuegong", 0))
pr = growth.practice_from(c, duel=out, checks=[])
after = int(c.get("prof", {}).get("taxuegong", 0))
ok(after > before, f"踏雪功攒到熟练度 {before} → {after}")
ok(any("运功" in (r.get("why") or []) for r in pr["skills"]),
   f"熟练度的来由写着'运功' got {[r['why'] for r in pr['skills']]}")

# 攒满就升级 (120 熟练度一级)
c2 = mk({"jibenneigong": 3, "taxuegong": 1}, level=20)
c2["prof"] = {"taxuegong": cs.PROF_PER_LEVEL - 6}
res = cs.add_practice(c2, "taxuegong", 12)
ok(res.get("leveled") and int(c2["skills"]["taxuegong"]) >= 2,
   f"熟练度满了给本门内功升级 got {c2['skills'].get('taxuegong')}")

# ── 6. 打坐也算一笔 (没有交手的回合内功也在长) ───────────────
sitter = mk({"jibenneigong": 3, "taxuegong": 1})
sitter["hp"] = 5          # 血薄 → 会走到"打坐"那一步
sitter["money"] = 0
sitter["bag"] = {}        # 没有药, 也花不起钱 → 只剩打坐
before = int(sitter.get("prof", {}).get("taxuegong", 0))
care = gear.selfcare(sitter, seed=7, when="出门前")
after = int(sitter.get("prof", {}).get("taxuegong", 0))
rests = [a for a in care.get("acts") or [] if a.get("kind") == "rest"]
ok(bool(rests), f"血薄又没钱时它会打坐 got {[a['kind'] for a in care.get('acts') or []]}")
ok(after > before, f"打坐给内功熟练度 {before} → {after}")
if rests:
    ok(int((rests[0].get("practice") or {}).get("gain") or 0) > 0,
       "打坐那一笔在 acts 里看得见")

# ── 7. 故事侧: 运过功就必须写内息 ──────────────────────────
ok(chron._exert_need({"exert": {"power": 1, "recover": 0}})["need"] == 1,
   "加力成功 → 这一回要写内息")
ok(not chron._exert_need({"exert": {"power": 0, "recover": 0, "failed": 2}}),
   "只有走岔 (没成) → 不强制写")
ok(chron._exert_in_body("他坐下把内息收回丹田。", chron._exert_need(
    {"exert": {"power": 1}})), "正文有内息 → 算写了")
ok(not chron._exert_in_body("他打了一架。", chron._exert_need(
    {"exert": {"power": 1}})), "正文没提内息 → 算没写")
ok(chron._exert_in_body("随便什么", {}), "没运功的回合这项自动满足")

# ── 8. 界面契约 ─────────────────────────────────────────
js = (ROOT / "src" / "recto" / "app" / "ui" / "assets" / "pet.js").read_text("utf-8")
ok("运功" in js and "ex.power" in js, "人物面板的 d20 卡里有「运功」一行")
ok("本门内功" in js, "同一张卡里标出本门内功管什么")
adv = (ROOT / "src" / "recto" / "pet" / "skills" / "adventure.md").read_text("utf-8")
ok("duel.exert" in adv and "加力" in adv and "回气" in adv,
   "adventure.md 写明运功怎么落进故事")
chr_ = (ROOT / "src" / "recto" / "pet" / "skills" / "chronicle.md").read_text("utf-8")
ok("内功基本不由你判" in chr_, "chronicle.md 说明内功不靠判词给 (避免重复记账)")

print("=" * 60)
print(f"{'PASS' if not FAIL else 'FAIL'} {PASS} · FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
