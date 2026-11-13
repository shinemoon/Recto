"""交手的文辞 (flavor) —— 一句话要写得像打斗, 不是像日志。

跑法: cd <仓库根> && python3 tests/test_pet_flavor.py

验的事:
  * 每一路兵刃的词库齐全 (起手 / 无招 / 有招 / 中招 / 落空 / 大成功 / 部位)
  * 产出的句子: 有招式名就引在「」里, 有对手名, 有数字 (伤 N), 不留占位符
  * 不重样: 同一场里不会连着说同一句
  * 兵刃对得上: "刀客"不会使拳 (主修的 skill id 决定词库)
  * 大成功 / 大失败 / 反噬 / 疗伤守势 各有各的写法 (不是都写"没打中")
  * 真实一场: 每一拍都有文辞, 且 12 拍里没有两句完全一样
"""
from __future__ import annotations

import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from recto.pet import charsheet as cs  # noqa: E402
from recto.pet import duel, flavor as F  # noqa: E402

PASS = FAIL = 0


def ok(cond, name):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS", name)
    else:
        FAIL += 1
        print("FAIL", name)


def has_bad_token(t: str) -> bool:
    return ("{" in t or "}" in t or "%s" in t or "None" in t)


# ── 1. 词库齐全 ──────────────────────────────────────
dump = F.dump()
ok(len(dump) == 7, f"七路兵刃都有词库 got {sorted(dump)}")
for k, slots in dump.items():
    for slot in ("hand", "plain", "force", "hit", "miss", "crit", "target"):
        ok(slots.get(slot, 0) >= 3, f"{k}.{slot} 至少 3 条 got {slots.get(slot)}")
ok(all(len(F.POOLS[k]["miss"]) >= 8 for k in F.POOLS), "落空的写法足够多 (≥8, 一场不重样)")

# ── 2. 单拍: 句式要件 ────────────────────────────────
for key in F.POOLS:
    v = F.new_voice(random.Random(7))
    t = F.hit(v, who="墨行", whom="刀客", weapon=key, move="一针见血",
              crit=False, dmg=12)
    ok("「一针见血」" in t, f"{key} 命中: 招名引在「」里 got {t}")
    ok("刀客" in t and "墨行" in t, f"{key} 命中: 两个人都写到 got {t}")
    ok("伤 12" in t, f"{key} 命中: 数字落在末尾 got {t}")
    ok(not has_bad_token(t), f"{key} 命中: 没有残留占位符 got {t}")
    ok(not t.strip().startswith(",") and ", ," not in t, f"{key} 命中: 标点没粘连 got {t}")

v = F.new_voice(random.Random(3))
t = F.miss(v, who="墨行", whom="刀客", weapon="jianfa", move="")
ok("墨行" in t and "刀客" in t, f"落空: 两个人都写到 got {t}")
ok(not has_bad_token(t), f"落空: 没有残留占位符 got {t}")

v = F.new_voice(random.Random(3))
t = F.fumble(v, who="墨行", weapon="jianfa", move="一针见血")
ok("一针见血" in t and "没递到人身上" in t, f"大失败: 写明这一记没递到人 got {t}")

v = F.new_voice(random.Random(3))
t = F.backfire(v, who="墨行", move="力劈华山", weapon="daofa")
ok("力劈华山" in t and ("自己身上" in t or "内息" in t), f"反噬: 写明伤在自己 got {t}")

v = F.new_voice(random.Random(3))
t = F.pfm_lost(v, who="墨行", move="八方风雨")
ok("八方风雨" in t and "花样散了" in t, f"招式没运成: 另一套写法 got {t}")

for kind in ("heal", "defend", "evade", "buff"):
    v = F.new_voice(random.Random(3))
    t = F.effect(v, who="墨行", move="铁桥拦江", kind=kind, amount=8)
    ok("铁桥拦江" in t and not has_bad_token(t), f"{kind}: 有招名无占位符 got {t}")

v = F.new_voice(random.Random(3))
t = F.stunned(v, who="墨行")
ok("墨行" in t and "这一拍" in t, f"定身: 写明错过这一拍 got {t}")

# ── 3. 不重样 ────────────────────────────────────────
v = F.new_voice(random.Random(11))
seen = []
for i in range(6):
    seen.append(F.miss(v, who="墨行", whom="刀客", weapon="jianfa", move=""))
ok(len(set(seen)) == 6, f"同一场六次落空不重样 got {len(set(seen))}")
v2 = F.new_voice(random.Random(11))
hits = [F.hit(v2, who="墨行", whom="刀客", weapon="jianfa", move="一针见血",
              crit=False, dmg=9) for _ in range(5)]
ok(len(set(hits)) == 5, f"同一场五次命中不重样 got {len(set(hits))}")

# ── 4. 兵刃对得上 ────────────────────────────────────
ok(F._key("daofa") == "dao", "刀法 → 刀的词库")
ok(F._key("刀法") == "dao", "中文名也能对上 (d20_stats 给的是中文)")
ok(F._key("jibenneigong") == "quan", "认不出来就用空手的词库 (不炸)")

ch = cs.new_char("墨行")
ch["school"] = "mochi"
ch["skills"] = {"jianfa": 12, "jibenshenfa": 8, "jibenzhaojia": 6}
ch["exp"] = 400
me = duel._my_fighter(ch)
ok(duel._weapon(me) == "jianfa", f"我方主修是剑 got {duel._weapon(me)}")

for seed in (11, 12, 13, 14, 15):
    foe = duel.make_foe(ch, seed=seed)
    if "刀客" in foe["name"] or "背刀" in foe["name"]:
        ok(foe["weapon"] == "daofa", f"名号写着刀的使刀 got {foe['name']} / {foe['weapon']}")
    if "镖师" in foe["name"]:
        ok(foe["weapon"] == "gunfa", f"挂鞭的镖师使棍 got {foe['weapon']}")
    if "剑客" in foe["name"]:
        ok(foe["weapon"] == "jianfa", f"剑客使剑 got {foe['weapon']}")

# ── 5. 真打一场: 每一拍都有文辞, 不重样 ─────────────────
r = duel.resolve(ch, seed=51)
ok(r["rounds"], "打出了战报")
texts = [e["text"] for e in r["rounds"]]
ok(all(t and not has_bad_token(t) for t in texts), "每一拍都有文辞、无占位符")
ok(len(set(texts)) == len(texts), f"整场没有两句完全一样 got {len(texts)}/{len(set(texts))}")
ok(all(re.search(r"[。—]", t) for t in texts), "每一拍至少有一个句子结束符")
ok(any(t.endswith("。") for t in texts), "文辞以句号收尾")

for e in r["rounds"]:
    if e["kind"] == "hit":
        ok(f"伤 {e['dmg']}" in e["text"], f"命中那拍的数字对得上 got {e['text']}")

# 同一 seed 可复现 (seed 决定一切)
r2 = duel.resolve(cs.new_char("墨行"), seed=51)
ok([e["text"] for e in r2["rounds"]] ==
   [e["text"] for e in duel.resolve(cs.new_char("墨行"), seed=51)["rounds"]],
   "同一 seed 的文辞逐字可复现")

# ── 6. 规则说明 ──────────────────────────────────────
rb = F.rulebook()
ok(isinstance(rb, list) and len(rb) >= 2, "有给读者看的文辞口径")
ok(any("MUD" in x for x in rb), "口径里点名了文辞来路")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
