"""行囊 · 银钱 · 伤药 —— 它靠自己活下来 (而不是永远带伤)。

跑法: cd <仓库根> && python3 tests/test_pet_gear.py

验的事:
  * 存档: 钱 / 行囊 / 装具三栏写得住, 老存档坏数据被清掉 (未知物件 / 负数)
  * 装具: 三个槽 (兵器 / 护具 / 佩物) 的加成**真进** d20 拆账 (护体 / 命中 / 兵刃骰 /
          先攻 / 暴击 / 上限), 换上换下都是派生值, 不写进存档
  * 买卖: 钱不够买不了; 卖价是买价的三分之一; 带在身上卖不掉
  * 消耗: 药吃掉一份就少一份, 恢复按上限百分比
  * 自理 (selfcare): 伤了先吃药 → 没药花钱 (医馆 / 客栈) → 什么都没有就打坐;
          **这是"气血不再永远停在 20/97"的机制**
  * 路上的账 (aftermath): 打赢有赏钱 (偶尔有战利品), 打输被摸走一点,
         学艺要花纸墨钱; 同一个种子重跑一致
  * 行囊里的杂用物件给路上的坎加值 (封顶 +2)
  * 界面契约: 人物面板带 gear 块, 页面有行囊卡, 四个动作都有按钮
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = Path(tempfile.mkdtemp(prefix="recto-pet-gear-"))
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
from recto.pet import gear as gr  # noqa: E402
from recto.pet import checks as ck  # noqa: E402
from recto.pet import chronicle as ch_mod  # noqa: E402

PASS = FAIL = 0


def ok(cond, msg=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS", msg)
    else:
        FAIL += 1
        print("FAIL", msg)


# ── 1. 存档 ──────────────────────────────────────────────
ch = cs.new_char()
ok(ch["money"] == gr.START_MONEY, f"新人物有钱 (got {ch['money']})")
ok(ch["bag"] == gr.START_BAG, f"新人物有起步行囊 (got {ch['bag']})")
ok(ch["equip"]["weapon"] == "tiejian", f"起步带兵器 (got {ch['equip']['weapon']})")

gr.add_money(ch, 40)
gr.give(ch, "jinchuangyao", 1)
gr.give(ch, "不存在的东西", 3)
ok(gr.money(ch) == 100, f"加钱 (got {gr.money(ch)})")
ok(gr.bag(ch)["jinchuangyao"] == 3, "放东西进行囊")
ok(gr.bag(ch).get("不存在的东西") is None, "未知物件进不了行囊")

gr.take(ch, "jinchuangyao", 1)
ok(gr.bag(ch)["jinchuangyao"] == 2, "拿掉一件")
ok(gr.take(ch, "jinchuangyao", 99) is False, "不够就拿不动")

bad = cs.new_char()
bad["money"] = -50
bad["bag"] = {"jinchuangyao": -2, "妖物": 1, "ganliang": 1}
bad["equip"] = {"weapon": "jinchuangyao", "armor": "不存在的", "charm": "tiepai"}
ok(gr.money(bad) == 0, "负数钱夹到 0")
ok(gr.bag(bad) == {"ganliang": 1}, f"行囊洗掉负数与未知物件 (got {gr.bag(bad)})")
ok(gr.equip(bad)["weapon"] == "" and gr.equip(bad)["armor"] == "",
   "装具洗掉错槽 / 不认识的东西")
ok(gr.equip(bad)["charm"] == "tiepai", "对的东西留着")


# ── 2. 装具加成真进 d20 ───────────────────────────────────
ch = cs.new_char()
ch["skills"] = {"jianfa": 4}
ch["attrs"] = {a["key"]: 12 for a in cs.ATTRS}
ch["equip"] = {"weapon": "mujian", "armor": "buyi", "charm": ""}
s0 = cs.d20_stats(ch)
ch["equip"] = {"weapon": "qingfengjian", "armor": "huxinjing", "charm": "tiepai"}
s1 = cs.d20_stats(ch)
ok(s1["attack"] > s0["attack"], f"好兵器抬命中 ({s0['attack']} → {s1['attack']})")
ok(s1["damage"]["sides"] > s0["damage"]["sides"],
   f"兵刃骰跟着换 ({s0['damage']['text']} → {s1['damage']['text']})")
ok(s1["ac"] > s0["ac"], f"护具抬护体 ({s0['ac']} → {s1['ac']})")
ok(s1["pierce"] > s0["pierce"], f"护具抬卸力 ({s0['pierce']} → {s1['pierce']})")
ok(s1["crit_range"] < s0["crit_range"], f"佩物放宽暴击 ({s0['crit_range']} → {s1['crit_range']})")
ok(any(m["src"] == "gear" for m in s1["ac_parts"]), "护体的拆账里有 gear 一项")
ok(any(m["src"] == "gear" for m in s1["attack_parts"]), "命中的拆账里有 gear 一项")
ok(s1["damage"]["weapon"] == "青锋剑", f"伤害那一行写的是兵器名 (got {s1['damage']['weapon']})")
ok("equip" not in s1, "装具加成是派生值, 不进 d20 结构以外的地方")

ch["equip"] = {"weapon": "tiejian", "armor": "buyi", "charm": "yupai"}
ok(cs.derive(ch)["neili_max"] > cs.derive(cs.new_char())["neili_max"],
   "玉佩抬内力上限")
ch["equip"] = {"weapon": "tiejian", "armor": "buyi", "charm": "pinganfu"}
ok(cs.derive(ch)["hp_max"] > cs.derive(cs.new_char())["hp_max"], "平安符抬气血上限")


# ── 3. 买 / 卖 / 带 / 卸 ─────────────────────────────────
ch = cs.new_char()
ch["money"] = 100
r = gr.buy(ch, "pijia")
ok(not r["ok"], "钱不够买不了 (皮甲 180, 兜里 100)")
ok(gr.money(ch) == 100 and gr.bag(ch).get("pijia") is None, "买不成也不扣钱")
r = gr.buy(ch, "jinchuangyao", 2)
ok(r["ok"] and gr.money(ch) == 100 - 48 and gr.bag(ch)["jinchuangyao"] == 4,
   f"买两包药 (钱 {gr.money(ch)} / 包 {gr.bag(ch)['jinchuangyao']})")
ok(gr.sell_price("pijia") == 60, f"卖价 = 买价的三分之一 (got {gr.sell_price('pijia')})")

gr.give(ch, "pijia", 1)
r = gr.wear(ch, "pijia")
ok(r["ok"] and ch["equip"]["armor"] == "pijia", "换上护具")
ok(gr.bag(ch).get("buyi") == 1, "换下来的布衣回行囊")
ok(gr.bag(ch).get("pijia") is None, "身上那件不在行囊里")
r = gr.sell(ch, "pijia")
ok(not r["ok"], "带在身上的卖不掉")
r = gr.unwear(ch, "armor")
ok(r["ok"] and ch["equip"]["armor"] == "" and gr.bag(ch).get("pijia") == 1,
   "卸下回行囊")
before = gr.money(ch)
r = gr.sell(ch, "pijia")
ok(r["ok"] and gr.money(ch) == before + 60, "卖得掉 (按三分之一价)")


# ── 4. 吃药 ─────────────────────────────────────────────
ch = cs.new_char()
mx = cs.resources(ch)["hp"]["max"]
ch["hp"] = 10
r = gr.use(ch, "jinchuangyao")
ok(r["ok"], "吃药成功")
ok(cs.resources(ch)["hp"]["cur"] == 10 + int(round(mx * 0.35)),
   f"按上限比例回血 (got {cs.resources(ch)['hp']['cur']} / max {mx})")
ok(gr.bag(ch)["jinchuangyao"] == 1, "吃掉一份")
r = gr.use(ch, "tiejian")
ok(not r["ok"], "兵器不能吃")
r = gr.use(ch, "jinchuangyao")
gr.use(ch, "jinchuangyao")
ok(not gr.use(ch, "jinchuangyao")["ok"], "吃光了就没了")


# ── 5. 自理 (selfcare) —— 回血的三条路 ────────────────────
ch = cs.new_char()
mx = cs.resources(ch)["hp"]["max"]
ch["hp"] = int(mx * 0.2)
r = gr.selfcare(ch, seed=1)
ok(any(a["kind"] == "use" for a in r["acts"]), f"伤了先吃药 (acts={[a['name'] for a in r['acts']]})")
ok(cs.resources(ch)["hp"]["cur"] > int(mx * 0.4),
   f"吃完药血明显回来 (got {cs.resources(ch)['hp']['cur']}/{mx})")
ok(gr.money(ch) == gr.START_MONEY, "有药就不花钱")

ch = cs.new_char()
ch["bag"] = {}
ch["money"] = 100
ch["hp"] = int(cs.resources(ch)["hp"]["max"] * 0.2)
r = gr.selfcare(ch, seed=2)
ok(any(a["kind"] == "pay" and a["name"] == "医馆" for a in r["acts"]),
   f"没药就找医馆 (acts={[a['name'] for a in r['acts']]})")
ok(gr.money(ch) == 100 - gr.CLINIC["price"], f"医馆扣钱 (got {gr.money(ch)})")
ok("医馆" in r["note"], f"记账文案带上它 (got {r['note']})")

ch = cs.new_char()
ch["bag"] = {}
ch["money"] = 0
ch["hp"] = int(cs.resources(ch)["hp"]["max"] * 0.2)
r = gr.selfcare(ch, seed=3)
ok(any(a["kind"] == "rest" for a in r["acts"]), "没钱没药就打坐")
ok(gr.money(ch) == 0, "打坐不要钱")
ok(cs.resources(ch)["hp"]["cur"] > int(cs.resources(ch)["hp"]["max"] * 0.2),
   "打坐也回一点 (不至于永远停在重伤)")

ch = cs.new_char()
full = cs.resources(ch)
ch["hp"] = full["hp"]["max"]
ch["neili"] = full["neili"]["max"]
ch["spirit"] = full["spirit"]["max"]
r = gr.selfcare(ch, seed=4)
ok(r["acts"] == [] and r["note"] == "", "气足时什么都不做, 也不写账")


# ── 6. 路上的账 (aftermath) ──────────────────────────────
ch = cs.new_char()
ch["money"] = 50
win = gr.aftermath(ch, kind="fight", duel={"result": "win", "foe": {"weapon": "daofa"}}, seed=11)
ok(win["money"]["delta"] > 0, f"打赢有钱 (got {win['money']['delta']})")
ok(win["gained"], "有收入明细")

ch2 = cs.new_char()
ch2["money"] = 50
lose = gr.aftermath(ch2, kind="fight", duel={"result": "lose", "foe": {}}, seed=11)
ok(lose["money"]["delta"] < 0, f"打输掉钱 (got {lose['money']['delta']})")

ch3 = cs.new_char()
ch3["money"] = 100
learn = gr.aftermath(ch3, kind="learn", seed=5)
ok(learn["money"]["delta"] < 0, "学艺要花纸墨钱")
ok(gr.money(ch3) < 100, "真扣了")

ch4 = cs.new_char()
ch4["money"] = 50
a = gr.aftermath(ch4, kind="rescue", seed=7)
ok(a["money"]["delta"] > 0, "救人得谢礼")

# 同一个种子重跑一致 (故事与账要对得上)
x1 = cs.new_char()
x2 = cs.new_char()
r1 = gr.aftermath(x1, kind="fight", duel={"result": "win", "foe": {"weapon": "jianfa"}}, seed=99)
r2 = gr.aftermath(x2, kind="fight", duel={"result": "win", "foe": {"weapon": "jianfa"}}, seed=99)
ok(r1["money"]["delta"] == r2["money"]["delta"] and
   [i["name"] for i in r1["items"]] == [i["name"] for i in r2["items"]],
   "同种子结果一致")

ch5 = cs.new_char()
ch5["money"] = 0
z = gr.aftermath(ch5, kind="fight", duel={"result": "lose", "foe": {}}, seed=3)
ok(gr.money(ch5) == 0, "没钱时不会被扣成负数")


# ── 7. 行囊里的物件给路上的坎加值 ────────────────────────
ch = cs.new_char()
base_mods = ck._mods_for(ch, ck.ROAD_TEMPLATES[0])
gr.give(ch, "huozhezi", 1)
with_kit = ck._mods_for(ch, ck.ROAD_TEMPLATES[0])
ok(any(m["src"] == "gear" for m in with_kit) and
   not any(m["src"] == "gear" for m in base_mods),
   "带上火折子才给加值")
gr.give(ch, "shengzi", 3)
ok(gr.check_bonus(ch, "road") == 2, f"同类封顶 +2 (got {gr.check_bonus(ch, 'road')})")
ok(gr.check_bonus(ch, "person") == 0, "不同的坎要不同的物件")

# 过坎会长功夫 (2026-11-16 之前 plan() 漏了 skill 一栏, 这记账永远为空)
got = ck.resolve(cs.new_char(), [], seed=4, prefer="road")
ok(all(c.get("skill") for c in got["checks"]),
   f"每道坎都记着用哪门功夫 (got {[c.get('skill') for c in got['checks']]})")


# ── 8. 界面契约 ─────────────────────────────────────────
ch = cs.new_char()
gr.give(ch, "jinchuangyao", 1)
v = cs.view(ch)
g = v.get("gear") or {}
ok(isinstance(g.get("money"), int), "人物面板带银钱")
ok(len(g.get("slots") or []) == 3, "三个装具槽都列出来")
ok(any(r["id"] == "jinchuangyao" for r in (g.get("bag") or [])), "行囊里列出药")
ok(any(r["id"] == "pijia" for r in (g.get("shop") or [])), "铺子里能买护具")
ok(all(("afford" in r) for r in (g.get("shop") or [])), "铺子每样都标了买不买得起")
ok(g.get("mods_text"), "身上带的东西有一句摘要")
ok("打坐" in (g.get("rule") or ""), "面板写明'没钱没药就打坐'")

html = (ROOT / "src/recto/app/ui/assets/pet.html").read_text("utf-8")
js = (ROOT / "src/recto/app/ui/assets/pet.js").read_text("utf-8")
css = (ROOT / "src/recto/app/ui/assets/pet.css").read_text("utf-8")
ok('id="card-bag"' in html, "页面有行囊卡")
ok('id="bag-slots"' in html and 'id="bag-list"' in html and 'id="shop-list"' in html,
   "行囊卡三块都在")
for act in ("use", "wear", "sell", "buy", "unwear"):
    ok(("data-bag-act" in js) and ("'/api/gear/' + act" in js or
                                   "'/api/gear/'" in js and act in
                                   "use wear sell buy unwear"),
       f"前端有 {act} 动作")
ok("function renderGear" in js, "前端有 renderGear")
ok("renderGear(ch.gear)" in js, "renderChar 会渲染行囊")
ok(".rc-pet .bag-row" in css, "行囊行有样式")

# 接口路由存在 (app 层)
app = (ROOT / "src/recto/app/pet.py").read_text("utf-8")
for path in ('"/gear"', '"/gear/buy"', '"/gear/use"', '"/gear/sell"',
             '"/gear/wear"', '"/gear/unwear"'):
    ok(path in app, f"接口 {path} 存在")

# 故事契约: gear 真的传给了写故事的那道工序
ok('"gear": _gear_brief' in (ROOT / "src/recto/pet/chronicle.py").read_text("utf-8"),
   "payload 里有 gear 一栏")
adv = (ROOT / "src/recto/pet/skills/adventure.md").read_text("utf-8")
ok("gear.aftermath" in adv and "gear.equipped" in adv, "故事契约写明钱与药")
ok("不许改" in adv or "数额照输入" in adv, "故事契约禁止改数字")

brief = ch_mod._gear_brief({"before": {"notes": ["用了金创药"], "acts": [{"kind": "use", "name": "金创药"}]},
                            "after": {"notes": [], "acts": []},
                            "aftermath": {"money": {"from": 10, "to": 46, "delta": 36},
                                          "gained": [{"money": 36, "why": "赏钱"}],
                                          "items": [{"name": "柳叶刀", "why": "从对手身上取下"}]}},
                           cs.new_char())
ok(brief["before"]["notes"] == ["用了金创药"], "gear brief 带出门前的账")
ok(brief["aftermath"]["money"]["delta"] == 36, "gear brief 带收入")
ok(brief["aftermath"]["items"][0]["name"] == "柳叶刀", "gear brief 带战利品")
ok(brief["equipped"].get("weapon", {}).get("name") == "铁剑", "gear brief 带身上那件兵器")

summ = ch_mod._gear_summary({"before": {"notes": ["a"], "acts": [{"name": "金创药"}]},
                             "aftermath": {"money": {"delta": 5}, "items": [{"name": "麻绳"}]}})
ok(summ["before"]["notes"] == ["a"] and summ["items"] == ["麻绳"], "落库摘要是紧凑版")

# ── 9. "真花过的钱与药"要写进正文 (核正文, 不认自报) ──────
need = ch_mod._gear_need({"before": {"notes": ["用了金创药, 气血 +34"]}})
ok(len(need) == 1 and need[0]["key"] == "med", "用了药 → 要求提到药")
ok(ch_mod._gear_in_body("他敷了药, 又在墙根坐了一夜。", need) is True, "写了药就算过")
ok(ch_mod._gear_in_body("他一路北行, 心很定。", need) is False, "一个字没提就要重写")

need2 = ch_mod._gear_need({"after": {"notes": ["去医馆花了 45 文"]}})
ok(all("医馆" in g["words"] for g in need2), "医馆那条的允许词里有它自己")

need3 = ch_mod._gear_need({"before": {"notes": ["找了个背风处打坐"]}})
ok(any(g["key"] == "rest" for g in need3), "打坐也算账")
ok(ch_mod._gear_in_body("他寻了个破庙, 打坐到天亮。", need3) is True, "写了打坐就过")

# 战利品**不强制** (提了更好, 但不为"没写一把刀"整篇重写)
need4 = ch_mod._gear_need({"aftermath": {"items": [{"name": "柳叶刀"}]}})
ok(need4 == [], "捡到东西不强制写 (不至于为它重写)")

ok(ch_mod._gear_need({}) == [] and ch_mod._gear_in_body("随便", []) is True,
   "气足的一趟不强制写药")
ok(ch_mod._gear_need({"aftermath": {"gained": [{"money": 36}]}}) == [],
   "只有赏钱不强制 (写成捎带东西也说得通)")

js_gate = (ROOT / "src/recto/pet/chronicle.py").read_text("utf-8")
ok("gear_need = _gear_need" in js_gate, "compose 里接了这道闸")
ok("gear_omitted" in js_gate, "漏了会记账 (故事卡要标出来)")
ok("会被核" in (ROOT / "src/recto/pet/skills/adventure.md").read_text("utf-8"),
   "故事契约写明这一条会被核")


print()
print("=" * 46)
print("PASS", PASS, " FAIL", FAIL)
sys.exit(1 if FAIL else 0)
