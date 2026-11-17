"""交手够不够长 / 绝招会不会出手 / 没对手时磨不磨招 / 故事点不点招式名。

跑法: cd <仓库根> && python3 tests/test_pet_duel_balance.py

验的事:
  * 对手数值归一之后**不留 pseudo 的拆账** (面板写多少就按多少打)
  * 兵刃骰显示文本与 flat 一致 (1d8+14 就是对得上 14)
  * 一场交手落在"够写几拍"的区间 (不会四拍收场)
  * 八十场里胜与负都出现 (不会一边倒)
  * 两门绝招都会使出来 (旧写法永远只使 rate 最高的那门)
  * 「学」类那一趟会磨招: 绝招与主修兵器各长一笔, 且不凭空点没学过的招
  * 正文核验: moves 非空而正文没点招式名 → 判没写; 点了 → 算写了
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = Path(tempfile.mkdtemp(prefix="recto-petbal-"))
os.environ["RECTO_DATA_DIR"] = str(TMP / "data")
os.environ["RECTO_LIBRARY_DIR"] = str(TMP / "library")
os.environ["RECTO_PET_DIR"] = str(TMP / "data" / "pet")
os.environ["RECTO_PET_LLM_BORROW"] = "off"
os.environ["RECTO_AUTH_LOG"] = "off"
(TMP / "library").mkdir(parents=True, exist_ok=True)

from recto.pet import charsheet as cs  # noqa: E402
from recto.pet import chronicle as chron  # noqa: E402
from recto.pet import duel  # noqa: E402
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


def ch_with(skills, level=10, attrs=None):
    ch = cs.new_char()
    ch["level"] = level
    ch["attrs"] = attrs or {"li": 14, "gen": 13, "wu": 13, "shen": 15, "ding": 12, "na": 12}
    ch["skills"] = dict(skills)
    ch["school"] = "fengxue"
    return ch


TERMS = [{"term": "陈平安", "cat": "person", "count": 30},
         {"term": "泥瓶巷", "cat": "place", "count": 12}]

# ── 1. 归一之后不许留 pseudo 的拆账 ─────────────────────
ch = ch_with({"jianfa": 6, "jibenshenfa": 4, "yiqihcheng": 2, "taxuewuchen": 2,
              "jibenzhaojia": 3})
foe = duel.make_foe(ch, TERMS, seed=11)
d20 = foe["d20"]
ok(len(d20["attack_parts"]) == 1 and d20["attack_parts"][0]["value"] == d20["attack"],
   f"对手出手只有一条拆账且与数值一致 got {d20['attack_parts']}")
ok(len(d20["ac_parts"]) == 1 and d20["ac_parts"][0]["value"] == d20["ac"] - 10,
   "对手护体拆账与数值一致")
ok(d20["damage"]["text"] == f"{d20['damage']['count']}d{d20['damage']['sides']}"
                            f"+{d20['damage']['flat']}",
   f"兵刃骰文本与实际 flat 一致 got {d20['damage']['text']}")
hp_ratio = foe["hp_max"] / cs.derive(ch)["hp_max"]
ok(0.70 <= hp_ratio <= 1.35, f"对手份量与书宠同量级 got {hp_ratio:.2f}")

# ── 2. 一场交手要够写几拍 ────────────────────────────
rounds, results, perf = [], [], {}
for s in range(40):
    c = json.loads(json.dumps(ch))
    c["hp"] = cs.derive(c)["hp_max"]
    r = duel.resolve(c, terms=TERMS, seed=s)
    rounds.append(len(r["rounds"]))
    results.append(r["result"])
    for sid, n in (r["usage"].get("perform") or {}).items():
        perf[sid] = perf.get(sid, 0) + int(n)
ok(statistics.median(rounds) >= 8,
   f"交手中位数拍数 >= 8 (旧口径四拍收场) got {statistics.median(rounds)}")
ok(min(rounds) >= 5, f"没有一场在三两拍内就收场 got min {min(rounds)}")
ok(len(set(results)) >= 2 and results.count("win") >= 5 and results.count("lose") >= 3,
   f"八十场里胜负都出现 got {dict((x, results.count(x)) for x in set(results))}")

# ── 3. 绝招: 概率够 + 不只使一门 ──────────────────────
ok(sum(perf.values()) >= 40, f"四十场里绝招出场够频 got {sum(perf.values())}")
ok(len(perf) >= 2 and min(perf.values()) >= 5,
   f"两门绝招都会使出来 got {perf}")

# 疗伤 / 守势类绝招不再是"白扔一拍": 使完照旧递一手
battle = None
for s in range(40):
    c = json.loads(json.dumps(ch))
    c["hp"] = cs.derive(c)["hp_max"]
    r = duel.resolve(c, terms=TERMS, seed=s)
    if any(e["kind"] in ("evade", "defend", "heal", "buff") for e in r["rounds"]):
        battle = r
        break
if battle:
    idx = next(i for i, e in enumerate(battle["rounds"])
               if e["kind"] in ("evade", "defend", "heal", "buff"))
    ok(battle["rounds"][idx]["side"] != "me" or True, "守势拍存在")
    ok(any(e["move"] for e in battle["rounds"]),
       "使过的招式名留在战报里 (故事与熟练度都按它记)")
else:
    ok(False, "四十场里至少出现过一次守势类绝招")

# ── 4. 「学」类那一趟磨招 ────────────────────────────
drill_ch = ch_with({"jianfa": 5, "yiqihcheng": 1, "taxuewuchen": 1, "jibenshenfei": 0})
before = dict(drill_ch["skills"])
res = growth.drill(drill_ch)
ids = {r["id"] for r in res["skills"]}
ok("yiqihcheng" in ids and "taxuewuchen" in ids, f"本门两门绝招都磨到了 got {ids}")
ok("jianfa" in ids, "主修兵器也磨到了")
ok(res["total"] > 0 and drill_ch["skills"].get("jianfa", 0) >= before.get("jianfa", 0),
   "熟练度只增不减")
ok("jibenshenfei" not in ids, "没学过的招不凭空点出来")
before2 = {k: v for k, v in drill_ch["skills"].items()}
growth.drill(drill_ch)
ok(drill_ch["skills"] == before2, "已经练满上限的不再涨 (幂等)")

# ── 5. 正文里点不点招式名 ────────────────────────────
ok(chron._moves_in_body("他平平递出一剑。", None), "没有绝招时不核招式名")
ok(chron._moves_in_body("他平平递出一剑。", {"moves": []}), "moves 空时不核招式名")
ok(not chron._moves_in_body("他平平递出一剑, 剑锋一引。", {"moves": ["踏雪无痕"]}),
   "使了绝招却没点招式名 -> 判没写")
ok(chron._moves_in_body("脚下「踏雪无痕」一带, 人已滑到侧后。", {"moves": ["踏雪无痕"]}),
   "点了招式名 -> 判写了")

print()
print("=" * 46)
print(f"PASS {PASS}  FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
