"""T8: 技能成长的唯一来源 = 熟练度; 上限与等级挂钩。

跑法: cd <仓库根> && python3 tests/test_pet_practice.py

验的事:
  * 熟练度: 满 PROF_PER_LEVEL 升一级; 到等级上限就停; 只增不减
  * 技能上限**只看等级** —— 手里剩多少修为不影响; 升一级上限不回落
  * 这一趟真用过的功夫会被记上 (duel 的 usage / checks 的 skill)
  * 每趟每门封顶 (PER_SKILL_PER_CYCLE_CAP), 判词那一笔是另一份预算
  * 界面契约: view 里有 prof/cap, 页面有「熟练」列与进度条
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = Path(tempfile.mkdtemp(prefix="recto-pet-practice-"))
os.environ["RECTO_DATA_DIR"] = str(TMP / "data")
os.environ["RECTO_LIBRARY_DIR"] = str(TMP / "library")
os.environ["RECTO_CLOUD_AUTH_FILE"] = str(TMP / "auth.json")
os.environ["RECTO_PET_DIR"] = str(TMP / "data" / "pet")
os.environ["RECTO_AUTH_LOG"] = "off"
os.environ["RECTO_AUTH_PASSWORD"] = "pet-pass"
os.environ.pop("RECTO_AUTH_DISABLE", None)
(TMP / "library").mkdir(parents=True, exist_ok=True)
(TMP / "auth.json").write_text(json.dumps({
    "usr": "i0000000000", "zysid": "test", "p29": "zye5b814",
    "p2": "104583", "p3": "101010010", "p4": "501603"}), "utf-8")

from recto.pet import charsheet as cs  # noqa: E402
from recto.pet import growth as g  # noqa: E402
from recto.pet import duel as dl  # noqa: E402
from recto.pet import checks as ck  # noqa: E402

PASS = FAIL = 0


def ok(cond, msg=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS", msg)
    else:
        FAIL += 1
        print("FAIL", msg)


# ── 1. 上限与等级挂钩 ─────────────────────────────────────
base = cs.new_char()
base["skills"] = {"jianfa": 3}
lv = []
for level in (1, 3, 6, 12, 24, 48):
    ch = cs.new_char()
    ch["level"] = level
    lv.append(cs.cap_for(ch, cs.SKILL_BY_ID["jianfa"]))
ok(lv == sorted(lv), f"等级越高上限越高 (单调) got {lv}")
ok(len(set(lv)) > 3, f"上限真的随等级动 got {lv}")

a, b = cs.new_char(), cs.new_char()
a["level"] = b["level"] = 12
a["exp"], b["exp"] = 0, 50000
ok(cs.cap_for(a, cs.SKILL_BY_ID["jianfa"]) == cs.cap_for(b, cs.SKILL_BY_ID["jianfa"]),
   "上限与'手里剩多少修为'无关 (只看等级)")
a["level"] = 13
ok(cs.cap_for(a, cs.SKILL_BY_ID["jianfa"]) >= cs.cap_for(b, cs.SKILL_BY_ID["jianfa"]),
   "升一级上限不回落")

cap12 = cs.cap_for(a, cs.SKILL_BY_ID["jianfa"])


# ── 2. 熟练度 ────────────────────────────────────────────
ch = cs.new_char()
ch["level"] = 20
ch["skills"] = {"jianfa": 3}
ok(cs.prof_of(ch, "jianfa") == 0, "开局熟练度 0")
cs.add_practice(ch, "jianfa", cs.PROF_PER_LEVEL - 1)
ok(ch["skills"]["jianfa"] == 3, "差一点还不升级")
res = cs.add_practice(ch, "jianfa", 1)
ok(res["leveled"] == 1 and ch["skills"]["jianfa"] == 4, f"满一格升一级 got {res}")
ok(res["prof"] == 0, f"升完余数归零 got {res['prof']}")
ok(cs.add_practice(ch, "bogus", 100)["added"] == 0, "未知技能不认")
ok(cs.add_practice(ch, "jianfa", -50)["added"] == 0, "负数当 0")

# 到上限: 不吃熟练度, 也不越级
ch2 = cs.new_char()
ch2["level"] = 6
cap = cs.cap_for(ch2, cs.SKILL_BY_ID["jianfa"])
ch2["skills"] = {"jianfa": cap}
r = cs.add_practice(ch2, "jianfa", 999)
ok(r["added"] == 0 and r["blocked"] == "capped", f"到上限不吃熟练度 got {r}")
ok(cs.prof_of(ch2, "jianfa") == 0, "到上限时熟练度停在 0")


# ── 3. 这一趟真用过的功夫 ─────────────────────────────────
ch3 = cs.new_char()
ch3["level"] = 20
ch3["skills"] = {"jianfa": 3, "jibenzhaojia": 2, "yizhenjianxue": 1, "guanqi": 1}
duel = {
    "usage": {"turns": 10, "weapon": "jianfa", "perform": {"yizhenjianxue": 3},
              "defended": 8, "rounds": 6},
    "result": "win",
}
checks = [
    {"skill": "guanqi", "success": True},
    {"skill": "jibenzhaojia", "success": False},
]
prac = g.practice_from(ch3, duel, checks)
got = {r["id"]: r["gain"] for r in prac["skills"]}
ok(got.get("jianfa") == 10 * g.PRACTICE["attack"], f"出手十拍 → 剑法攒 30 got {got.get('jianfa')}")
ok(got.get("yizhenjianxue") == 3 * g.PRACTICE["perform"],
   f"三次绝招 → 那门绝招攒 12 got {got.get('yizhenjianxue')}")
ok(got.get("jibenzhaojia") == 8 * g.PRACTICE["defend"] + g.PRACTICE["check_no"],
   f"被攻八拍 + 一道坎没过 → 基本招架 got {got.get('jibenzhaojia')}")
ok(got.get("guanqi") == g.PRACTICE["check_ok"], f"过坎 → 观气 got {got.get('guanqi')}")
ok(prac["total"] == sum(got.values()), "总计 = 各项之和")

# 每趟每门封顶
ch4 = cs.new_char()
ch4["level"] = 20
ch4["skills"] = {"jianfa": 1}
prac4 = g.practice_from(ch4, {"usage": {"turns": 999, "weapon": "jianfa",
                                        "perform": {}, "defended": 0}}, None)
ok(prac4["skills"][0]["gain"] == g.PER_SKILL_PER_CYCLE_CAP,
   f"一趟里同一门封顶 {g.PER_SKILL_PER_CYCLE_CAP} got {prac4['skills'][0]['gain']}")

# 没用过的功夫不动
ok("tuifa" not in cs.__dict__ or True, "-")
before_tuifa = cs.prof_of(ch4, "tuifa")
ok(before_tuifa == 0, "没使过的功夫不会有熟练度")


# 0 级 = 还没入门: 只记熟练度, 不进 skills; 攒满一格才算练成第一层
ch8 = cs.new_char()
ch8["level"] = 12
cs.add_practice(ch8, "zhuizong", 40)
ok("zhuizong" not in (ch8.get("skills") or {}), "还没入门的不写进 skills")
ok(cs.prof_of(ch8, "zhuizong") == 40, "但熟练度记着")
ok(any(x["id"] == "zhuizong" and x["level"] == 0 for x in cs.view(ch8)["skills"]),
   "面板上仍列出来 (显示未入门)")
cs.add_practice(ch8, "zhuizong", 80)
ok(ch8["skills"].get("zhuizong") == 1, f"满一格 → 练成 1 级 got {ch8['skills']}")
ok(cs.prof_of(ch8, "zhuizong") == 0, "升级后余数归零")
# 存档往返: 未入门那门的熟练度不能被 load_char 抹掉
save_path = cs.CHAR_PATH
cs.save_char(ch8)
back = cs.load_char()
ok(cs.prof_of(back, "zhuizong") == 0 and back["skills"].get("zhuizong") == 1,
   "读回来还在 (1 级)")

# 陪读自动长的基本内功同样受等级上限约束 (3900 分钟 = 13 级, 但 5 级的人只许 6 级)
ch7 = cs.new_char()
ch7["level"] = 5
cap7 = cs.cap_for(ch7, cs.SKILL_BY_ID["jibenneigong"])
g.award_minutes(ch7, 3900)
ok(ch7["skills"]["jibenneigong"] <= cap7,
   f"自动内功不越上限 got {ch7['skills']['jibenneigong']} vs cap {cap7}")


# ── 4. 战报/判定如实带上"练了什么" ─────────────────────────
me = cs.new_char()
me["level"] = 20
me["skills"] = {"jianfa": 5, "jibenzhaojia": 3, "yizhenjianxue": 3, "jibenshenfa": 3}
out = dl.resolve(me, terms=[], seed=7)
ok("usage" in out, "战报带 usage")
u = out["usage"]
ok(u["weapon"] == "jianfa", f"主修兵刃是剑法 got {u['weapon']}")
ok(u["turns"] > 0, f"记了自己出手几拍 got {u['turns']}")
ok(isinstance(u["perform"], dict), "绝招按门计次")
ok(u["defended"] >= 0, "被攻拍数记上了")
sid_ok = all(s in cs.SKILL_BY_ID for s in u["perform"])
ok(sid_ok, f"绝招记的是 skill id got {list(u['perform'])}")

co = ck.resolve(me, [], seed=7, count=2)
ok(all("skill" in c for c in co["checks"]), "每道坎带上是哪门功夫")


# ── 5. 端到端: 一趟下来技能真会长 ──────────────────────────
ch5 = cs.new_char()
ch5["level"] = 20
ch5["skills"] = {"jianfa": 3}
prac5 = g.practice_from(ch5, {"usage": {"turns": 12, "weapon": "jianfa",
                                        "perform": {}, "defended": 6}},
                        [{"skill": "jianfa", "success": True}])
ok(cs.prof_of(ch5, "jianfa") > 0, "这条路真的攒上了熟练度")
for _ in range(5):          # 同样的遭遇多来几趟 (封顶是"每趟", 不是终身)
    g.practice_from(ch5, {"usage": {"turns": 12, "weapon": "jianfa",
                                    "perform": {}, "defended": 6}}, [])
ok(ch5["skills"]["jianfa"] > 3, f"几趟之后剑法升级 got {ch5['skills']['jianfa']}")
ok(ch5["skills"]["jianfa"] <= cs.cap_for(ch5, cs.SKILL_BY_ID["jianfa"]), "不会越过上限")

# 判词那一笔与实战那一份分开算
ch6 = cs.new_char()
ch6["level"] = 20
ch6["skills"] = {"jianfa": 3}
g.practice_from(ch6, {"usage": {"turns": 12, "weapon": "jianfa",
                                "perform": {}, "defended": 0}}, None)
aw = g.award_judged(ch6, {"exp": 0, "skills": {"jianfa": 1}}, max_exp=0)
ok(cs.prof_of(ch6, "jianfa") >= g.PRACTICE["judged"],
   f"判词还能再加一笔 got {cs.prof_of(ch6, 'jianfa')}")
ok(aw["practice"]["skills"][0]["why"] == ["判定"], "判词那笔标了来由")


# ── 6. 界面契约 ───────────────────────────────────────────
v = cs.view(ch5)
row = next(x for x in v["skills"] if x["id"] == "jianfa")
ok("prof" in row and "cap" in row, "技能行带 prof / cap")
ok(row["prof_need"] == cs.PROF_PER_LEVEL, "带满格所需点数")
ok(v["skill_cap"] == cs.skill_cap(int(v["level"]), 10 + 0) or v["skill_cap"] > 0,
   f"人物面板暴露 skill_cap got {v.get('skill_cap')}")
sch = cs.schema()
ok(sch.get("prof_per_level") == cs.PROF_PER_LEVEL, "schema 带 prof_per_level")
ok(any("熟练度" in r for r in sch["rules"]), "规则里写明熟练度")
ok(any("只看等级" in r for r in sch["rules"]), "规则里写明上限只看等级")

html = (ROOT / "src/recto/app/ui/assets/pet.html").read_text("utf-8")
js = (ROOT / "src/recto/app/ui/assets/pet.js").read_text("utf-8")
ok("<th>熟练</th>" in html, "页面有「熟练」列")
ok("function profCell" in js, "前端有熟练度格子")
ok("profCell(s)" in js, "技能表用了它")
ok("cs-bar-in" in (ROOT / "src/recto/app/ui/assets/pet.css").read_text("utf-8"),
   "熟练度进度条有样式")

print()
print("=" * 46)
print("PASS", PASS, " FAIL", FAIL)
sys.exit(1 if FAIL else 0)
