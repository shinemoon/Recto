"""d20 那一层 —— 掷骰 / 检定 / 交手 / 路上的坎。

跑法: cd <仓库根> && python3 tests/test_pet_d20.py

验的事:
  * 加值口径: 根骨 (属性-10)//2, 技能 (级+1)//2 封顶 8
  * 难度阶梯 / 大成功 / 大失败 / 优势劣势 / 被动值
  * 每次检定都拆账 (mods 带名字+分值+来源), 描述行能读出"为什么中"
  * 交手的 d20 数值 (护体/命中/伤害骰/先攻/豁免) 都由根骨+技能+禀赋算出来
  * 战斗: 先攻掷一次 / 攻击检定对护体 / 大失败自伤 / 大成功伤害骰翻倍
  * 绝招: 运功检定的 DC 由成功率折算; 骰出 1 才反噬; 控住要对方掷豁免
  * 战报: 每一拍带 rolls, 整场带 rulebook / fighters / initiative
  * 同种子可复现; 打 40 场有胜负
  * 路上的坎: 白检定再试 +1 (封顶 +3) / 红检定一次定生死 / 成功抹账
  * 判定落在语料词上; 给故事的是精简版 (不含点数)
"""
from __future__ import annotations

import json
import os
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = Path(tempfile.mkdtemp(prefix="recto-petd20-"))
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
from recto.pet import checks as ck  # noqa: E402
from recto.pet import chronicle as chron  # noqa: E402
from recto.pet import d20  # noqa: E402
from recto.pet import duel  # noqa: E402
from recto.pet import store  # noqa: E402

PASS = FAIL = 0


def ok(cond, name):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS", name)
    else:
        FAIL += 1
        print("FAIL", name)


# ── 1. 加值与难度 ────────────────────────────────────
ok([d20.attr_mod(v) for v in (3, 8, 10, 11, 14, 18, 30)] == [-4, -1, 0, 0, 2, 4, 10],
   "根骨加值 = (值-10)//2")
ok(d20.skill_mod(0) == 0 and d20.skill_mod(1) == 1 and d20.skill_mod(10) == 5,
   "技能加值 = (级+1)//2")
ok(d20.skill_mod(30) == 8, f"技能加值封顶 +8 got {d20.skill_mod(30)}")
ok([t["dc"] for t in d20.DC_TIERS] == [5, 8, 11, 14, 17, 20, 23], "难度阶梯 7 档")
ok(d20.dc_of("normal") == 11 and d20.dc_of("tricky") == 14 and d20.dc_of(19) == 19,
   "难度既能按名字也能按数值给")
ok(d20.tier_of(15)["name"] == "不易", f"最接近的档 got {d20.tier_of(15)['name']}")
ok(d20.passive([{"name": "观气", "value": 3, "src": "skill"}]) == 13, "被动值 = 10 + 加值")


# ── 2. 检定本身 ──────────────────────────────────────
mods = d20.mods_of(("悟性 14", 2, "attr"), ("观气 5 级", 3, "skill"),
                   ("禀赋", 0, "trait"))
ok(len(mods) == 2 and all(m["src"] for m in mods), "0 分的加值不进账")

c = d20.check(random.Random(1), label="识人", dc=11, mods=mods)
ok(c["total"] == c["d20"] + 5, f"总数 = 骰点 + 加值 got {c['total']}")
ok(c["dc_label"] == "寻常" and c["kind_cn"] == "技能", "带难度名与种类名")
ok(c["text"].startswith("技能 · d20"), f"描述行开头 got {c['text'][:20]}")
ok("悟性 14 +2" in c["text"] and "观气 5 级 +3" in c["text"], "描述行把加值拆开写")

crit = None
for s in range(400):
    x = d20.check(random.Random(s), label="t", dc=30, mods=mods)
    if x["crit"]:
        crit = x
        break
ok(crit is not None and crit["success"] and crit["degree"] == "crit",
   "骰出 20 必成 (难度再高也成)")
fumble = None
for s in range(400):
    x = d20.check(random.Random(s), label="t", dc=0, mods=mods)
    if x["fumble"]:
        fumble = x
        break
ok(fumble is not None and not fumble["success"] and fumble["degree"] == "fumble",
   "骰出 1 必败 (加值再高也败)")

no_crit = d20.check(random.Random(0), label="先攻", dc=0, mods=mods,
                    kind="initiative", allow_crit=False)
ok(not no_crit["crit"] and not no_crit["fumble"], "先攻不判大成功/大失败")

adv = d20.check(random.Random(2), label="t", dc=11, mods=mods, mode="adv")
ok(len(adv["d20s"]) == 2 and adv["d20"] == max(adv["d20s"]), "优势掷两次取高")
dis = d20.check(random.Random(2), label="t", dc=11, mods=mods, mode="dis")
ok(dis["d20"] == min(dis["d20s"]), "劣势掷两次取低")

bonus_c = d20.check(random.Random(3), label="t", dc=11, mods=mods, bonus=2)
ok(bonus_c["bonus"] == 2 and bonus_c["total"] == bonus_c["d20"] + 7, "情境加值进总数")

dice = d20.roll_dice(random.Random(1), 2, 6)
ok(len(dice["faces"]) == 2 and dice["sum"] == sum(dice["faces"]), "伤害骰带明细")
ok(dice["text"].startswith("2d6"), f"伤害骰文字 got {dice['text']}")
ok(len(d20.rulebook()) >= 5 and "d20" in d20.rulebook()[0], "有规则说明")


# ── 3. 人物的 d20 数值 ────────────────────────────────
ch = cs.new_char()
ch["level"] = 8
ch["attrs"] = {"li": 14, "gen": 13, "wu": 13, "shen": 15, "ding": 12, "na": 12}
ch["skills"] = {"jianfa": 6, "jibenshenfa": 4, "yiqihcheng": 2, "jibenzhaojia": 3,
                "dianxuejiemai": 4}
st = cs.d20_stats(ch)
ok(st["ac"] == 10 + sum(m["value"] for m in st["ac_parts"]),
   f"护体 = 10 + 拆账之和 got {st['ac']}")
ok(st["attack"] == sum(m["value"] for m in st["attack_parts"]), "命中加值 = 拆账之和")
ok(st["damage"]["text"] == "1d8+18", f"兵刃骰按主修定 got {st['damage']['text']}")
ok(st["crit_range"] == 19, f"暴击区间由禀赋/身法放宽 got {st['crit_range']}")
ok(set(st["saves"]) == {"gen", "ding", "shen"}, "三种豁免: 根骨 / 定力 / 身法")
ok(all("parts" in st["saves"][k] for k in st["saves"]), "豁免也拆账")
ok(st["init"]["total"] == sum(m["value"] for m in st["init"]["parts"]), "先攻加值拆账")

ch2 = json.loads(json.dumps(ch))
ch2["traits"] = ["muliguoren"]          # 目力过人: 命中 +6
st2 = cs.d20_stats(ch2)
ok(st2["attack"] > st["attack"], f"禀赋「目力过人」进了命中 got {st['attack']}→{st2['attack']}")
ch3 = json.loads(json.dumps(ch))
ch3["skills"]["jibenzhaojia"] = 10
ok(cs.d20_stats(ch3)["ac"] > st["ac"], "基本招架加护体")

view = cs.view(ch)
ok(view["d20"]["ac"] == st["ac"] and view["d20"]["damage"]["sides"] == 8,
   "人物面板能拿到 d20 数值")
ok(any("d20" in r for r in cs.schema()["d20_rules"]), "体系表里带 d20 规则")
ok(len(cs.schema()["dc_tiers"]) == 7, "体系表里带难度阶梯")


# ── 4. 交手 ──────────────────────────────────────────
terms = [{"term": "陈平安", "cat": "person", "count": 30},
         {"term": "泥瓶巷", "cat": "place", "count": 12},
         {"term": "剑", "cat": "object", "count": 40}]

foe = duel.make_foe(ch, terms, seed=11)
ok("陈平安" in foe["name"] and "泥瓶巷" in foe["arena"], "对手与场地从语料长出来")
ok(foe["d20"]["ac"] > 0 and foe["d20"]["damage"]["sides"] >= 4, "对手也有 d20 数值")
ok(abs(foe["hp_max"] - cs.derive(ch)["hp_max"]) <= cs.derive(ch)["hp_max"] * 0.35,
   f"对手份量同量级 got {foe['hp_max']}")


def fight(seed, char=None):
    c = json.loads(json.dumps(char or ch))
    return duel.resolve(c, terms=terms, seed=seed)


a, b = fight(5), fight(5)
ok(a["result"] == b["result"] and len(a["rounds"]) == len(b["rounds"])
   and a["hp"]["to"] == b["hp"]["to"] and a["rolls"] == b["rolls"],
   "同种子逐项相同 (含掷骰次数)")

r = fight(5)
ok(r["initiative"]["me"]["kind"] == "initiative"
   and r["initiative"]["order"][0] in ("me", "foe"), "先攻掷过且排了序")
ok(isinstance(r["rolls"], int) and r["rolls"] >= len(r["rounds"]),
   f"每一拍至少一次掷骰 got rolls={r['rolls']} rounds={len(r['rounds'])}")
ok(all("rolls" in e for e in r["rounds"]), "战报每一拍都带 rolls 字段")
ok(any(e["rolls"] and e["rolls"][0]["kind"] == "attack" for e in r["rounds"]),
   "攻击那一拍记的是攻击检定")
atk_rows = [x for e in r["rounds"] for x in e["rolls"] if x["kind"] == "attack"]
ok(all(x["mods"] for x in atk_rows), "攻击检定带拆账 (根骨/技能/禀赋)")
ok(all(any(m["src"] == "skill" for m in x["mods"]) or x["bonus"] for x in atk_rows),
   "攻击检定的加值里有技能那一份")
ok(r["fighters"]["me"]["ac"] == cs.d20_stats(ch)["ac"], "战报带我方护体")
ok(r["fighters"]["foe"]["ac"] > 0, "战报带对方护体")
ok(len(r["rulebook"]) >= 5, "战报带规则说明")

hits = [e for e in r["rounds"] if e["kind"] == "hit"]
ok(hits and all(e["dice"].get("faces") for e in hits), "命中那一拍带伤害骰明细")
ok(all(e["dmg"] > 0 for e in hits), "命中就有伤害")

results = {fight(s)["result"] for s in range(40)}
ok("win" in results and "lose" in results, f"胜负都会出现 got {results}")

# 大成功 → 伤害骰掷两遍
crit_found = False
for s in range(300):
    rr = fight(s)
    for e in rr["rounds"]:
        for x in e["rolls"]:
            if x["kind"] == "attack" and x["crit"]:
                crit_found = True
                ok(len(e["dice"]["faces"]) >= 2,
                   f"大成功伤害骰翻倍 got {e['dice']['faces']}")
                break
        if crit_found:
            break
    if crit_found:
        break
ok(crit_found, "打多了总会出现大成功")

fumbles = [e for s in range(120) for e in fight(s)["rounds"] if e["kind"] == "fail"]
ok(fumbles and all(e["self"] > 0 for e in fumbles), "大失败/反噬都伤自己")

stuns = [e for s in range(120) for e in fight(s)["rounds"] if e["kind"] == "stun"]
ok(stuns and all(any(x["kind"] == "save" for x in e["rolls"]) for e in stuns),
   "被定住那一拍掷的是豁免")

# 运功检定的 DC 由成功率折算, 且成功/失败都记在案
perf = [x for s in range(60) for e in fight(s)["rounds"] for x in e["rolls"]
        if x["kind"] == "perform"]
ok(perf and all(6 <= x["dc"] <= 18 for x in perf),
   f"运功难度由成功率折算 got {sorted({x['dc'] for x in perf})}")

# 写回存档
c2 = json.loads(json.dumps(ch))
c2["hp"], c2["neili"] = cs.derive(c2)["hp_max"], cs.derive(c2)["neili_max"]
old_fame = int(c2.get("fame") or 0)
r2 = duel.resolve(c2, terms=terms, seed=5)
ok(c2["hp"] == r2["hp"]["to"] and c2["neili"] == r2["neili"]["to"], "气血内力写回")
ok(int(c2["hp"]) >= 1, "不会真死")
ok(int(c2.get("fame") or 0) == old_fame + (3 if r2["result"] == "win" else
                                           (1 if r2["result"] == "draw" else 0)),
   "声望按胜负涨")
ok(duel.brief(r2).startswith("遭遇"), "一句话摘要")


# ── 5. 路上的坎 ───────────────────────────────────────
one = ck.plan(ch, terms, seed=3, count=1)
ok(len(one) == 1 and one[0]["label"], "count=1 时只出一道路")
two = ck.plan(ch, terms, seed=3, count=2)
ok(len(two) == 2, "默认两道坎")
ok(two[0]["dc"] and two[0]["mods"], "坎带难度与加值")
ok(two[0]["white"] is True, "路那一类是白检定")
reds = [t for sd in range(30) for t in ck.plan(ch, terms, seed=sd, count=2)
        if t["white"] is False]
ok(reds, "抽得到红检定 (一次定生死)")
ok(any("泥瓶巷" in t["target"] or "陈平安" in t["target"] for t in two),
   f"坎落在语料上 got {[t['target'] for t in two]}")
ok(two[0]["target"].startswith("在泥瓶巷"), "场地类的坎写成'在<地方>…'")

got1 = ck.resolve(ch, terms, seed=7, memory={})
ok(len(got1["checks"]) == 2, "掷出两道坎")
ok(all("d20" in c and "mods" in c for c in got1["checks"]), "坎也是 d20 掷的")
ok(all(c["outcome"] for c in got1["checks"]), "每道坎都有照实该写的下场")

# 白检定重试: 记忆里 +1, 封顶 +3
mem = {}
failed_white = None
for s in range(200):
    g = ck.resolve(ch, terms, seed=s, memory={})
    for c in g["checks"]:
        if c["white"] and not c["success"]:
            failed_white = c
            mem = g["memory"]
            break
    if failed_white:
        break
ok(failed_white is not None, "总会有一道白检定没过")
ok(mem.get(failed_white["id"]) == 1, f"没过就记一笔 got {mem}")

g2 = ck.resolve(ch, terms, seed=999, memory={failed_white["id"]: 2})
same = [c for c in g2["checks"] if c["id"] == failed_white["id"]]
if same:
    ok(same[0]["bonus"] == 2, f"再试给 +2 got {same[0]['bonus']}")
    ok("再试" in same[0]["label"], "重试的标签会写出来")
else:
    ok(True, "这一趟没抽到同一道坎 (跳过)")

g3 = ck.resolve(ch, terms, seed=999, memory={failed_white["id"]: 9})
same3 = [c for c in g3["checks"] if c["id"] == failed_white["id"]]
if same3:
    ok(same3[0]["bonus"] == 3, f"累积加值封顶 +3 got {same3[0]['bonus']}")
else:
    ok(True, "这一趟没抽到同一道坎 (跳过)")

# 红检定不给 +1
red = None
for s in range(200):
    g = ck.resolve(ch, terms, seed=s, memory={})
    for c in g["checks"]:
        if not c["white"]:
            red = c
            break
    if red:
        break
ok(red is not None and red["bonus"] == 0 and red["retry"] is False,
   "红检定不给再试加值")

# 成功抹账
m = {}
won = None
for s in range(300):
    g = ck.resolve(ch, terms, seed=s, memory={"road": 2})
    for c in g["checks"]:
        if c["id"] == "road" and c["success"]:
            won, m = c, g["memory"]
            break
    if won:
        break
ok(won is not None and "road" not in m, f"过了就把账抹平 got {m}")

ok(ck.brief(got1["checks"]), "坎有日志摘要")
ok(len(ck.rulebook()) >= 4, "坎也有规则说明")


# ── 6. 推演接线: 判定进 payload / 落库 / 给下一回参考 ──────
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
        global_judge_payload.append(user)
        return json.dumps({
            "assessment": {"depth": "稳读", "breadth": "专一", "one_line": "读了一小时"},
            "growth": {"exp": 200, "pot": 1, "fame": 4, "morality": 0,
                       "skills": {"jianfa": 1}, "trait": "", "reason": "读了 70 分钟"},
            "next_hint": "往北"}, ensure_ascii=False)
    if "游历演绎" in head:
        global_story_payload.append(user)
        return json.dumps({"title": "第一回 · 石桥", "place": "石桥",
                           "body": "他在石桥上站了一会儿, 又往下走。" * 12,
                           "terms_used": [], "themes_used": ["江湖"],
                           "growth_shown": ["剑法 7 级"], "duel_shown": True,
                           "checks_shown": True,
                           "tail": "桥下水响。", "verdict": "知道疼了"},
                          ensure_ascii=False)
    return "{}"


global_judge_payload: list[str] = []
global_story_payload: list[str] = []
from recto.pet import lore  # noqa: E402
lore.chat = fake_chat

from recto.core import clock  # noqa: E402
from recto.services import reader_state as rs  # noqa: E402

BOOK = "d" * 16
rc = rs.conn()
rc.execute("INSERT OR REPLACE INTO rbook (key, path, kind, title, author, bytes, chapters,"
           " size, mtime, cover, added_at, seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
           (BOOK, str(TMP / "book.epub"), "epub", "示例长篇", "某作者", 1000, 3, 1000,
            __import__("time").time(), "", clock.iso_local(), clock.iso_local()))
today = clock.fmt_sec(__import__("time").time(), "%Y-%m-%d")
rc.execute("INSERT OR REPLACE INTO rtime (book, day, bitmap, minutes, first_at, updated_at)"
           " VALUES (?,?,?,?,?,?)", (BOOK, today, "0", 70, clock.iso_local(),
                                     clock.iso_local()))
rc.commit()

from recto.pet import encounters as enc  # noqa: E402,F401

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
ok(out["status"] == "ok", f"回合成功 got {out.get('status')} {out.get('error','')}")
ok(len(out.get("checks") or []) == 2, f"回合里带两道坎 got {len(out.get('checks') or [])}")
ok(any("泥瓶巷" in (c.get("target") or "") or "陈平安" in (c.get("target") or "")
       for c in out.get("checks") or []), "坎落在真实语料上")

advs = chron.adventures(limit=1)
g = (advs[0].get("growth") or {}) if advs else {}
ok(g.get("checks") and len(g["checks"]) == 2, "判定落进游历记录")
ok(g.get("checks_shown") is True, "故事有没有照写判定被记下来")

stt = chron.load_state()
ok(isinstance(stt.get("last_checks"), list) and stt["last_checks"], "上一回的坎留给下一回")
ok(all({"label", "dc", "total", "ok"} <= set(x) for x in stt["last_checks"]),
   "上一回的坎记了 label/dc/total/ok")

story_payload = global_story_payload[-1] if global_story_payload else ""
ok('"checks"' in story_payload, "故事看到了 checks")
ok('"target"' in story_payload and '"outcome"' in story_payload, "坎带 target 与 outcome")
ok('"d20"' not in story_payload, "故事看不到骰点 (不能复述数字)")
judge_payload = global_judge_payload[-1] if global_judge_payload else ""
ok('"skill_menu"' in judge_payload, "判成长那边照旧拿到技能菜单")

print()
print("=" * 46)
print(f"PASS {PASS}  FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
