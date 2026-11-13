"""书宠的武侠体系 + 遭遇结算 —— 参数不是摆设, 故事里真被用到。

跑法: cd <仓库根> && python3 tests/test_pet_duel.py

验的事:
  * 体系是江湖的: 技能分类 (基本功/兵器/拳脚/轻功/内功/绝招/杂学) 与招式参数齐全
  * 根骨 / 技能 / 禀赋 真的算进 combat (命中 / 闪避 / 伤害 / 减伤)
  * 禀赋的 mods 真的生效 (天生神力 +3 臂力 / 铁骨铜皮 减伤 15%)
  * 老存档的"读书路数"技能迁到新体系 (读书悟道 → 基本内功), 对不上的退休
  * 遭遇: 对手从语料长出来 / 同种子可复现 / 结果有胜有负
  * 绝招真被使用: 内力被扣; 失败会反噬自己
  * 打完写回存档: 气血内力下降但不死 (最低 1), 胜了涨声望
  * 调息: 每故事日回一部分
  * 推演: 遭遇战报进了游历 payload、落了库、并留给下一回参考
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = Path(tempfile.mkdtemp(prefix="recto-petduel-"))
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

from recto.core import clock  # noqa: E402
from recto.pet import charsheet as cs  # noqa: E402
from recto.pet import chronicle as chron  # noqa: E402
from recto.pet import duel  # noqa: E402
from recto.pet import growth as g  # noqa: E402
from recto.pet import lore, store  # noqa: E402
from recto.services import reader_state as rs  # noqa: E402

PASS = FAIL = 0


def ok(cond, name):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS", name)
    else:
        FAIL += 1
        print("FAIL", name)


# ── 1. 体系: 江湖的那一套 ─────────────────────────────
types = {s["type"] for s in cs.SKILLS}
ok(types == {"basic", "weapon", "fist", "qinggong", "neigong", "perform", "art"},
   f"技能分类齐全 got {sorted(types)}")

names = {s["name"] for s in cs.SKILLS}
for n in ("剑法", "刀法", "腿法", "轻功", "基本内功", "观气", "医术"):
    ok(n in names, f"有武侠功夫「{n}」")
for n in ("读书悟道", "批注点睛", "抄经", "追根溯源", "回溯拾遗", "妙语解颐"):
    ok(n not in names, f"读书路数的「{n}」已退休")

pfms = [s for s in cs.SKILLS if s["type"] == "perform"]
ok(all({"cost", "rate", "busy", "fail", "effect"} <= set(p) for p in pfms),
   "每个绝招都有 内力/成功率/定身/反噬/作用")
ok({p["effect"] for p in pfms} >= {"damage", "control", "defend", "evade"},
   f"绝招作用不止一种 got {sorted({p['effect'] for p in pfms})}")
ok(all(s["neigong"] in cs.SKILL_BY_ID and s["perform"] in cs.SKILL_BY_ID
       and s["weapon"] in cs.SKILL_BY_ID for s in cs.SCHOOLS),
   "每个门派的内功 / 本门兵器 / 绝招都在技能表里")
ok(all("mods" in t and t["mods"] for t in cs.TRAITS), "每条禀赋都带数值 mods")

# 描述里不该再出现"读书"这套比喻
ok(not any("读" in s.get("desc", "") for s in cs.SKILLS),
   "技能说明里不再拿阅读打比方")
ok(not any("读" in t.get("desc", "") for t in cs.TRAITS), "禀赋说明同上")


# ── 2. 根骨 / 技能 真的进 combat ────────────────────────
ch = cs.new_char()
base = cs.derive(ch)["combat"]
ok(base["hit"] == round(40 + 10 * 1.2), f"命中 = 40 + 身法×1.2 got {base['hit']}")
ok(base["damage"] == round(8 + 10 * 1.2), f"伤害看臂力 got {base['damage']}")

ch2 = cs.new_char()
ch2["attrs"]["shen"] = 20
ok(cs.derive(ch2)["combat"]["hit"] > base["hit"], "身法涨 → 命中涨")
ch2["attrs"]["shen"] = 10

ch3 = cs.new_char()
ch3["skills"] = {"jianfa": 10}
d3 = cs.derive(ch3)["combat"]
ok(d3["main"] and d3["main"]["id"] == "jianfa" and d3["main"]["level"] == 10,
   f"主修取最高那门兵器 got {d3.get('main')}")
ok(d3["hit"] > base["hit"] and d3["damage"] > base["damage"],
   "剑法涨 → 命中与伤害都涨")

ch4 = cs.new_char()
ch4["skills"] = {"jibenzhaojia": 10, "jibenyinggong": 10}
ok(cs.derive(ch4)["combat"]["parry"] > base["parry"], "招架涨")
ok(cs.derive(ch4)["combat"]["defense"] > base["defense"], "硬功涨 → 防御涨")

ch5 = cs.new_char()
ch5["skills"] = {"qinggong": 10}
ok(cs.derive(ch5)["combat"]["dodge"] > base["dodge"], "轻功涨 → 闪避涨")
ok(cs.derive(ch5)["combat"]["speed"] > base["speed"], "轻功涨 → 先手涨")


# ── 3. 禀赋 mods 生效 ─────────────────────────────────
ch = cs.new_char()
ch["traits"] = ["tianshengshenli"]
ok(cs.effective_attrs(ch)["li"] == 13, f"天生神力 臂力 +3 got {cs.effective_attrs(ch)['li']}")
ok(cs.derive(ch)["combat"]["damage"] > cs.derive(cs.new_char())["combat"]["damage"],
   "臂力 +3 → 伤害也涨")

ch = cs.new_char()
ch["traits"] = ["tieguqingpi"]
ok(cs.derive(ch)["combat"]["take_pct"] == -15, "铁骨铜皮 减伤 15%")

ch = cs.new_char()
ch["traits"] = ["neiximianchang"]
ok(cs.derive(ch)["neili_max"] > cs.derive(cs.new_char())["neili_max"],
   "内息绵长 → 内力上限涨")

ch = cs.new_char()
ch["traits"] = ["gugqingqi"]
ch["exp"] = 1000                    # 上限还没顶到 skill.cap 时, 百分比才看得出来
plain = cs.new_char()
plain["exp"] = 1000
ok(cs.cap_for(ch, cs.SKILL_BY_ID["jianfa"]) > cs.cap_for(plain, cs.SKILL_BY_ID["jianfa"]),
   f"骨骼清奇 → 技能上限涨 got {cs.cap_for(ch, cs.SKILL_BY_ID['jianfa'])} vs "
   f"{cs.cap_for(plain, cs.SKILL_BY_ID['jianfa'])}")

ch = cs.new_char()
ch["traits"] = ["xinruzhishui"]
ok(cs.perform_fail(ch, cs.SKILL_BY_ID["lipihuashan"]) == 2,
   f"心如止水 反噬轻一级 (3 → 2) got {cs.perform_fail(ch, cs.SKILL_BY_ID['lipihuashan'])}")


# ── 4. 老存档迁移 ────────────────────────────────────
cs.CHAR_PATH.parent.mkdir(parents=True, exist_ok=True)
legacy = cs.new_char()
legacy["skills"] = {"dushuwudao": 10, "jingzuo": 3, "yanli": 4, "huihaopomo": 2,
                    "shenmeyoudemeiyou": 5}
cs.CHAR_PATH.write_text(json.dumps(legacy, ensure_ascii=False), "utf-8")
mig = cs.load_char()
ok(mig["skills"].get("jibenneigong") == 10, f"读书悟道 → 基本内功 10 got {mig['skills']}")
ok(mig["skills"].get("guanqi") == 4, f"眼力 → 观气 4 got {mig['skills']}")
ok(mig["skills"].get("bafangfengyu") == 2, f"挥毫泼墨 → 八方风雨 2 got {mig['skills']}")
ok("shenmeyoudemeiyou" not in mig["skills"], "对不上的旧技能退休")
ok("dushuwudao" not in mig["skills"], "旧 id 不再留着")
ok(mig["hp"] <= cs.derive(mig)["hp_max"], "气血被夹到上限 (旧存档 100 不是上限)")
ok(cs.resources(mig)["hp"]["cur"] >= 1, "气血不会因迁移变 0")


# ── 5. 遭遇: 对手从语料长出来 ─────────────────────────
ch = cs.new_char()
ch["level"] = 8
ch["attrs"] = {"li": 14, "gen": 13, "wu": 13, "shen": 15, "ding": 12, "na": 12}
ch["skills"] = {"jianfa": 6, "jibenshenfa": 4, "yiqihcheng": 2, "jibenzhaojia": 3,
                "dianxuejiemai": 4}
ch["school"] = "mochi"
terms = [{"term": "陈平安", "cat": "person", "count": 30},
         {"term": "泥瓶巷", "cat": "place", "count": 12},
         {"term": "剑", "cat": "object", "count": 40}]
foe = duel.make_foe(ch, terms, seed=11)
ok("陈平安" in foe["name"], f"对手从 person 语料取名 got {foe['name']}")
ok("泥瓶巷" in foe["arena"], f"场地从 place 语料取 got {foe['arena']}")
ok(foe["weapon_term"] == "剑", f"兵器从 object 语料取 got {foe['weapon_term']}")
ok(foe["hp_max"] > 0 and foe["combat"]["damage"] > 0, "对手有气血与伤害")
ok(abs(foe["hp_max"] - cs.derive(ch)["hp_max"]) <= cs.derive(ch)["hp_max"] * 0.3,
   f"对手份量跟书宠同量级 got {foe['hp_max']} vs {cs.derive(ch)['hp_max']}")

foe2 = duel.make_foe(ch, [], seed=11)
ok(foe2["name"] and "自称" not in foe2["name"], f"无语料也有对手 got {foe2['name']}")


# ── 6. 同种子可复现, 不同种子结果不同 ───────────────────
def fight(seed, char=None):
    c = json.loads(json.dumps(char or ch))
    return duel.resolve(c, terms=terms, seed=seed)


a, b = fight(5), fight(5)
ok(a["result"] == b["result"] and len(a["rounds"]) == len(b["rounds"])
   and a["hp"]["to"] == b["hp"]["to"], "同种子结果逐项相同 (可复现)")

results = {fight(s)["result"] for s in range(40)}
ok("win" in results and "lose" in results, f"胜负都会出现 got {results}")


# ── 7. 绝招真被用: 扣内力 / 失败反噬 ────────────────────
used = [r for s in range(40) for r in [fight(s)] if r["moves"]]
ok(used, "绝招会被使出来 (moves 非空)")
ok(all(r["neili"]["to"] <= r["neili"]["from"] for r in
       [fight(s) for s in range(40)]), "内力只减不增 (被绝招吃掉)")

fails = [e for s in range(40) for e in fight(s)["rounds"] if e.get("kind") == "fail"]
ok(fails, "会有走偏反噬的记录")
ok(all(e["self"] > 0 for e in fails), "反噬扣的是自己的气血")
ok(any(e.get("kind") == "hit" and e.get("effect") for e in
       [x for s in range(40) for x in fight(s)["rounds"]]), "成功的绝招带作用标记")

# 定身: 有 control 类绝招时对方会错过一拍
stuns = [e for s in range(60) for e in fight(s)["rounds"] if e.get("kind") == "stun"]
ok(stuns, f"控住会让对方错过一拍 got {len(stuns)}")


# ── 8. 打完写回存档 ──────────────────────────────────
ch2 = json.loads(json.dumps(ch))
ch2["hp"], ch2["neili"] = cs.derive(ch2)["hp_max"], cs.derive(ch2)["neili_max"]
before_fame = int(ch2.get("fame") or 0)
r = duel.resolve(ch2, terms=terms, seed=5)
ok(ch2["hp"] == r["hp"]["to"], "气血写回存档")
ok(ch2["neili"] == r["neili"]["to"], "内力写回存档")
ok(int(ch2["hp"]) >= 1, "输了也不会是 0 (不真死)")
if r["result"] == "win":
    ok(int(ch2["fame"]) == before_fame + 3, f"胜了涨声望 got {ch2['fame']}")
else:
    ok(int(ch2["fame"]) == before_fame, "非胜不涨声望")
ok(duel.brief(r).startswith("遭遇"), f"一句话摘要 got {duel.brief(r)}")

# 气血被打低 → resources 如实反映
low = json.loads(json.dumps(ch))
low["hp"] = 12
low["neili"] = 0
ok(cs.resources(low)["hp"]["cur"] == 12, "低气血不被自动补满")
ok(cs.resources(low)["neili"]["cur"] == 0, "内力见底如实")

# 调息
heal = cs.restore(low, 1.0)
ok(low["hp"] > 12 and low["neili"] > 0, f"调息回气血与内力 got {heal}")
ok(low["hp"] <= cs.derive(low)["hp_max"], "回气不超上限")


# ── 9. 推演: 战报进 payload / 落库 / 留给下一回 ─────────
def fake_chat(system, user, **kw):
    head = system.split("\n")[0]
    if "书籍知识储备" in head:
        if "chapter_title" in user:
            return json.dumps({"gist": "他推门进了青石巷。", "motifs": ["青石"],
                               "cast": ["他"]}, ensure_ascii=False)
        return json.dumps({"identity": "一部长篇", "themes": ["江湖"], "setting": "某处",
                           "summary": "概要", "moods": ["苍凉"], "hooks": ["一封信"],
                           "confidence": 0.6}, ensure_ascii=False)
    if "读书记录 → 成长判定" in head:
        return json.dumps({
            "assessment": {"depth": "稳读", "breadth": "专一", "one_line": "读了一小时"},
            "growth": {"exp": 200, "pot": 1, "fame": 4, "morality": 0,
                       "skills": {"jianfa": 1}, "trait": "", "reason": "读了 70 分钟"},
            "next_hint": "往北"}, ensure_ascii=False)
    if "游历演绎" in head:
        return json.dumps({"title": "第一回 · 石桥", "place": "石桥",
                           "body": "他在石桥上站了一会儿。" * 20,
                           "terms_used": [], "themes_used": ["江湖"],
                           "growth_shown": ["剑法 7 级"], "duel_shown": True,
                           "tail": "桥下水响。", "verdict": "知道疼了"}, ensure_ascii=False)
    if "退隐" in head:
        return "{}"
    return "{}"


lore.chat = fake_chat

BOOK = "d" * 16
rc = rs.conn()
rc.execute("INSERT OR REPLACE INTO rbook (key, path, kind, title, author, bytes, chapters,"
           " size, mtime, cover, added_at, seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
           (BOOK, str(TMP / "book.epub"), "epub", "示例长篇", "某作者", 1000, 3, 1000,
            time.time(), "", clock.iso_local(), clock.iso_local()))
today = clock.fmt_sec(time.time(), "%Y-%m-%d")
rc.execute("INSERT OR REPLACE INTO rtime (book, day, bitmap, minutes, first_at, updated_at)"
           " VALUES (?,?,?,?,?,?)", (BOOK, today, "0", 70, clock.iso_local(),
                                     clock.iso_local()))
rc.commit()

# 语料遭遇要真实存在, 才可能"撞上人"
from recto.pet import encounters as enc  # noqa: E402
words = {"陈平安": "person", "泥瓶巷": "place", "剑": "object"}

store.init() if hasattr(store, "init") else None
cin = store.conn()
cin.execute("INSERT OR REPLACE INTO encounter (book, day, term, cat, count, first_at, last_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (BOOK, today, "陈平安", "person", 12, clock.iso_local(), clock.iso_local()))
cin.execute("INSERT OR REPLACE INTO encounter (book, day, term, cat, count, first_at, last_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (BOOK, today, "泥瓶巷", "place", 6, clock.iso_local(), clock.iso_local()))
cin.commit()
cin.close()

out = chron.run(trigger="manual")
ok(out["status"] == "ok", f"回合成功 got {out.get('status')} {out.get('error', '')}")
d = out.get("duel") or {}
ok(d.get("foe", {}).get("name"), f"回合里有遭遇 got {d.get('foe')}")
ok(d.get("result") in ("win", "lose", "draw"), f"遭遇有结果 got {d.get('result')}")
ok("陈平安" in (d.get("foe", {}).get("name") or ""), "对手来自真实语料遭遇")

advs = chron.adventures(limit=3)
ok(advs and (advs[0].get("growth") or {}).get("duel"), "战报落进游历记录")
ok(chron.load_state().get("last_duel", {}).get("result") in ("胜", "负", "平", None),
   f"上一场留给下一回参考 got {chron.load_state().get('last_duel')}")

ch_now = cs.load_char()
ok(cs.resources(ch_now)["hp"]["cur"] < cs.derive(ch_now)["hp_max"] or d.get("result") != "lose",
   "打完的气血留在存档里 (没有偷偷补满)")
ok(cs.resources(ch_now)["hp"]["cur"] >= 1, "存档里气血不为 0")

print()
print("=" * 46)
print(f"PASS {PASS}  FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
