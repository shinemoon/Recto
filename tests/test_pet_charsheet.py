"""书宠人物属性体系 (武侠) —— 端到端 + 公式测试。

跑法: cd <仓库根> && PYTHONPATH=src python3 tests/test_pet_charsheet.py

验的事:
  * 体系定义: 六项根骨 / 门派 / 技能 / 性情 / 经脉 的内在规矩
  * 公式: 技能上限 (侠客行原式)、绝招成功率、内力上限、潜能分配上限
  * 存档: 落盘 600 权限、坏文件兜底、名字只有一处
  * HTTP: /pet/api/char* 全线 (get / schema / alloc / school / trait / name / reset / 404 / 405)

全部离线: 不调用真实 LLM, 不碰云端账号, 落盘都在临时目录。
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = Path(tempfile.mkdtemp(prefix="recto-petchar-"))
os.environ["RECTO_DATA_DIR"] = str(TMP / "data")
os.environ["RECTO_LIBRARY_DIR"] = str(TMP / "library")
os.environ["RECTO_CLOUD_AUTH_FILE"] = str(TMP / "auth.json")
os.environ["RECTO_PET_DIR"] = str(TMP / "data" / "pet")
os.environ["RECTO_AUTH_LOG"] = "off"
os.environ["RECTO_AUTH_PASSWORD"] = "pet-pass"
os.environ.pop("RECTO_AUTH_DISABLE", None)
os.environ.pop("RECTO_AUTH_USER", None)
(TMP / "library").mkdir(parents=True, exist_ok=True)
(TMP / "auth.json").write_text(json.dumps({
    "usr": "i0000000000", "zysid": "test", "p29": "zye5b814",
    "p2": "104583", "p3": "101010010", "p4": "501603"}), "utf-8")

from recto.app import server  # noqa: E402
from recto.core import paths  # noqa: E402
from recto.pet import charsheet as cs  # noqa: E402

SRV = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
SRV.daemon_threads = True
threading.Thread(target=SRV.serve_forever, daemon=True).start()
PORT = SRV.server_address[1]

PASS = FAIL = 0


def ok(cond, name):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS", name)
    else:
        FAIL += 1
        print("FAIL", name)


def req(path, method="GET", body=None, cookie=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
    h = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    if cookie:
        h["Cookie"] = cookie
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    raw = r.read()
    hd = dict(r.getheaders())
    c.close()
    return r.status, raw, hd


from recto.app import webauth  # noqa: E402

COOKIE = webauth.set_cookie("root")

# ── 定义表的内在规矩 ───────────────────────────────────
ok(len(cs.ATTRS) == 6, "六项根骨")
ok(all(a["init"] == 10 and a["cap"] == 30 for a in cs.ATTRS),
   "每项初始 10 / 封顶 30 (侠客行老规矩)")
ok(sum(a["cap"] for a in cs.ATTRS) == 180, "六项总和封顶 180")
ok(len({a["key"] for a in cs.ATTRS}) == 6, "属性 key 不重复")
ok(all(len(a["name"]) == 2 for a in cs.ATTRS), "属性名都是两字 (臂力/根骨/…)")

ok(len(cs.SCHOOLS) >= 4, "有若干门派")
ok(all(s["neigong"] in cs.SKILL_BY_ID for s in cs.SCHOOLS), "每个门派的内功都在技能表里")
ok(all(s["perform"] in cs.SKILL_BY_ID for s in cs.SCHOOLS), "每个门派的绝招都在技能表里")
ok(len({s["id"] for s in cs.SCHOOLS}) == len(cs.SCHOOLS), "门派 id 不重复")

ok(len({s["id"] for s in cs.SKILLS}) == len(cs.SKILLS), "技能 id 不重复")
ok(all(s.get("attr") in cs.ATTR_BY_KEY for s in cs.SKILLS), "每个技能都挂一项根骨")
ok(all(s.get("attr") in cs.ATTR_BY_KEY for s in cs.SKILLS if s["type"] == "perform"),
   "绝招也挂根骨")

ok(all(t["color"] in cs.TRAIT_COLORS for t in cs.TRAITS), "性情颜色合法")
ok(len({t["id"] for t in cs.TRAITS}) == len(cs.TRAITS), "性情 id 不重复")
ok([cs.TRAIT_COLORS[c]["points"] for c in ("gold", "red", "blue", "green")] == [40, 30, 20, 10],
   "性情四色点数 40/30/20/10 (侠客行老规矩)")

ok(len(cs.VEINS) == 10, "经脉十层")
ok([v["idx"] for v in cs.VEINS] == list(range(1, 11)), "经脉层号连续")
ok(all(cs.VEINS[i]["minutes"] < cs.VEINS[i + 1]["minutes"] for i in range(9)),
   "经脉耗时递增")

# ── 公式 ───────────────────────────────────────────────
# 侠客行原式: skill_level = (EXP*10)^(1/3)+1 (30 悟性)
ok(cs.skill_cap(2_700_000, 30) == 300, f"技能上限原式 (exp=270万, 悟性30 → 300) got {cs.skill_cap(2_700_000, 30)}")
ok(cs.skill_cap(2_700_000, 15) == 150, "悟性 15 → 折半")
ok(cs.skill_cap(0, 30) == 1, "修为 0 → 上限 1")

# 内力上限 = 最高内功等级 × 10 + 根骨 × 2
ch = cs.new_char()
ch["skills"] = {"guanhai": 20}
d = cs.derive(ch)
ok(d["neigong_level"] == 20, "最高内功等级 = 20")
ok(d["neili_max"] == 20 * 10 + 10 * 2, f"内力上限 = 内功×10 + 根骨×2 got {d['neili_max']}")
ch["skills"] = {"guanhai": 20, "woyungong": 30}
ok(cs.derive(ch)["neigong_level"] == 30, "取最高的内功等级")

# 绝招成功率 = 基础 + 根骨 + 等级×2, 封顶 95
sk = cs.SKILL_BY_ID["yizhenjianxue"]
ch = cs.new_char()
rate = cs.perform_rate(ch, sk, 1)
ok(rate == int(sk["rate"]) + 10 + 2,
   f"绝招成功率 = 基础+根骨+等级×2 got {rate}")
sk_lo = cs.SKILL_BY_ID["lipihuashan"]        # 基础成功率最低的一招, 不会被 95 顶掉
ch["attrs"][sk_lo["attr"]] = 30              # 它看的是臂力
ch["vein"] = 7
ok(cs.perform_rate(ch, sk_lo, 1) == int(sk_lo["rate"]) + 30 + 2 + 3,
   "第 7 层经脉 → 成功率 +3")
ok(cs.perform_rate(ch, sk, 99) == 95, "成功率封顶 95%")

# ── 门派加成的进出 (派生值, 不叠) ─────────────────────
# 老坑: 门派加成直接写进存档 → 叛门后残留, 再入别的门派就叠成两份。
ch2 = cs.new_char()
cs.choose_school(ch2, "mochi")          # 墨池剑庐: 臂力 +2
ok(cs.effective_attrs(ch2)["li"] == 12, f"入门墨池剑庐 → 臂力 12 got {cs.effective_attrs(ch2)['li']}")
ok(cs._base_attrs(ch2)["li"] == 10, "门派加成不进基础值 (玩家自己的额度不被占)")
cs.choose_school(ch2, "tieyan")         # 铁砚堂: 耐力 +2
ok(cs.effective_attrs(ch2)["li"] == 10, f"叛门后臂力加成收回 got {cs.effective_attrs(ch2)['li']}")
ok(cs.effective_attrs(ch2)["na"] == 12, f"叛门后换成耐力 +2 got {cs.effective_attrs(ch2)['na']}")
ok(cs._base_attrs(ch2)["li"] == 10 and cs._base_attrs(ch2)["na"] == 10, "叛门不动基础值")
# 反复来回叛门不会叠加成
for _ in range(3):
    cs.choose_school(ch2, "mochi")
    cs.choose_school(ch2, "tieyan")
ok(cs.effective_attrs(ch2)["li"] == 10 and cs.effective_attrs(ch2)["na"] == 12,
   "反复叛门不叠加成")

# 潜能点只加在基础值上, 不因门派加成而卡住
ch3 = cs.new_char()
ch3["pot"] = 5
cs.choose_school(ch3, "mochi")          # 臂力 +2
st3, r3 = cs.alloc_point(ch3, "li", 3)
ok(st3 == 200, f"入门状态下仍能加基础值 got {st3} {r3.get('error','')}")
ok(cs._base_attrs(ch3)["li"] == 13, f"基础臂力 10+3 got {cs._base_attrs(ch3)['li']}")
ok(cs.effective_attrs(ch3)["li"] == 15, f"实际臂力 = 基础13 + 门派2 got {cs.effective_attrs(ch3)['li']}")
# 封顶按基础值算: 基础臂力到 30 就不能再加 (门派加成不占额度)
ch3["pot"] = 99
st3, r3 = cs.alloc_point(ch3, "li", 30)
ok(st3 == 400 and "上限" in r3.get("error", ""), f"基础臂力封顶 30 → 拒 got {st3} {r3.get('error','')}")

# view 里 value = 实际值, base = 基础值, bonus = 门派加成
v3 = cs.view(ch3)
li3 = [a for a in v3["attrs"] if a["key"] == "li"][0]
ok(li3["value"] == 15 and li3["base"] == 13 and li3["bonus"] == 2,
   f"view 拆开 value/base/bonus got {li3['value']}/{li3['base']}/{li3['bonus']}")

# ── 潜能分配 ───────────────────────────────────────────
ch = cs.new_char()
ch["pot"] = 5
code, r = cs.alloc_point(ch, "wu", 3)
ok(code == 200 and r["char"]["attrs"][2]["value"] == 13, "悟性 +3")
ok(r["char"]["pot"] == 2, "潜能点扣掉")
code, r = cs.alloc_point(ch, "wu", 99)
ok(code == 400, "潜能不够 → 400")
ch["pot"] = 100
code, r = cs.alloc_point(ch, "wu", 30)
ok(code == 400 and "上限" in r.get("error", ""), "单项封顶 30 → 400")
code, r = cs.alloc_point(ch, "zzz", 1)
ok(code == 400, "未知属性 → 400")

# ── 门派 ───────────────────────────────────────────────
ch = cs.new_char()
code, r = cs.choose_school(ch, "mochi")
ok(code == 200, f"入门 200 (got {code})")
ok(r["char"]["school"]["name"] == "墨池剑庐", "门派名对上")
ok(r["char"]["attrs"][0]["value"] == 12, "墨池剑庐加臂力 +2")
names = [s["name"] for s in r["char"]["skills"]]
ok("观海诀" in names and "一气呵成" in names, "入门得本门内功与绝招")
ch["fame"] = 50
ch["loyalty"] = 20
code, r = cs.choose_school(ch, "tieyan")
ok(code == 200, "改投别的门派 200")
ok(r["char"]["fame"] == 0 and r["char"]["loyalty"] == 0, "叛门: 声望与门忠清零")
code, r = cs.choose_school(ch, "不存在")
ok(code == 400, "未知门派 → 400")

# ── 性情 ───────────────────────────────────────────────
ch = cs.new_char()
code, r = cs.grant_trait(ch, "gugqingqi")
ok(code == 200 and r["char"]["traits_info"]["spent"] == 40, "上品 40 分")
code, r = cs.grant_trait(ch, "dandaxixin")
ok(code == 200 and r["char"]["traits_info"]["spent"] == 50, "下品 10 分")
ch["traits"] = ["gugqingqi", "tianshengshenli", "tieguqingpi", "xinruzhishui"]
ch["traits"] = ["gugqingqi"] * 6
info = cs.traits_info(ch)
ok(info["left_count"] == 0, "最多 6 条")
ch2 = cs.new_char()
ch2["traits"] = ["gugqingqi", "tianshengshenli", "tieguqingpi"]   # 40+30+30 = 100 分
code, r = cs.grant_trait(ch2, "gugqingqi")
ok(code == 200, "重复觉醒同一条 → 幂等 200")
code, r = cs.grant_trait(ch2, "dandaxixin")
ok(code == 400 and "总分" in r.get("error", ""), "总分已满 100 → 拒绝觉醒")
ch3 = cs.new_char()
code, r = cs.grant_trait(ch3, "dandaxixin")
ok(code == 200 and r["char"]["traits_info"]["spent"] == 10, "还有额度 → 觉醒成功")

# ── 存档 ───────────────────────────────────────────────
ch = cs.new_char("阿墨")
cs.save_char(ch)
p = Path(cs.CHAR_PATH)
ok(p.exists(), f"存档写到 {p}")
ok((p.stat().st_mode & 0o777) == 0o600, f"存档权限 600 (got {oct(p.stat().st_mode & 0o777)})")
back = cs.load_char()
ok(back["name"] == "阿墨", "读回来名字对上")
ok(back["attrs"] == {a["key"]: 10 for a in cs.ATTRS}, "六项根骨补齐")

# 坏文件 → 兜底
p.write_text("{ 这不是 json", "utf-8")
ok(cs.load_char()["name"] == "小墨", "坏存档 → 兜底新人物, 不炸")

# 老存档缺字段 → 不炸
p.write_text(json.dumps({"name": "老友"}), "utf-8")
ch = cs.load_char()
ok(ch["name"] == "老友" and ch["attrs"]["wu"] == 10, "老存档缺字段 → 自动补默认值")

# 名字只有一处
code, r = cs.set_name(cs.load_char(), "新名字")
ok(code == 200 and r["char"]["name"] == "新名字", "改名字")
code, r = cs.set_name(cs.load_char(), "  ")
ok(code == 400, "空名字 → 400")

# ── HTTP: /pet/api/char* ───────────────────────────────
st, raw, _ = req("/pet/api/char", cookie=COOKIE)
d = json.loads(raw)
ok(st == 200, f"GET /pet/api/char 200 (got {st})")
ok("attrs" in d and len(d["attrs"]) == 6, "人物面板带六项根骨")
ok("stats" in d and "hp" in d["stats"], "带状态值 (气血/心神/内力/精力)")
ok("skills" in d and "traits" in d and "veins" in d, "带技能/性情/经脉")
ok(d.get("rank"), "带品级名号")

st, raw, _ = req("/pet/api/char/schema", cookie=COOKIE)
d = json.loads(raw)
ok(st == 200 and len(d.get("attrs") or []) == 6, "GET /pet/api/char/schema 200 且带六项")
ok(len(d.get("schools") or []) >= 4, "schema 带门派")
ok(len(d.get("skills") or []) >= 20, "schema 带技能表")
ok(len(d.get("traits") or []) >= 12, "schema 带性情表")
ok(len(d.get("veins") or []) == 10, "schema 带经脉表")
ok(d.get("rules"), "schema 带规则说明")

# 潜能点先塞进去才能加
ch = cs.load_char()
ch["pot"] = 3
cs.save_char(ch)
st, raw, _ = req("/pet/api/char/alloc", "POST", {"key": "wu", "n": 2}, COOKIE)
d = json.loads(raw)
ok(st == 200, f"POST /pet/api/char/alloc 200 (got {st})")
wu = [a for a in d["char"]["attrs"] if a["key"] == "wu"][0]
ok(wu["value"] == 12, f"悟性 +2 got {wu['value']}")
st, raw, _ = req("/pet/api/char/alloc", "POST", {"key": "zz", "n": 1}, COOKIE)
ok(st == 400, "未知属性 → 400")
st, raw, _ = req("/pet/api/char/alloc", "GET", cookie=COOKIE)
ok(st == 405, "GET alloc → 405")

st, raw, _ = req("/pet/api/char/school", "POST", {"school": "jingxin"}, COOKIE)
d = json.loads(raw)
ok(st == 200 and d["char"]["school"]["id"] == "jingxin", "POST /pet/api/char/school 200")

st, raw, _ = req("/pet/api/char/trait", "POST", {"trait": "yanjishoukuai"}, COOKIE)
d = json.loads(raw)
ok(st == 200 and "yanjishoukuai" in d["char"]["traits_info"]["owned"], "POST /pet/api/char/trait 200")
st, raw, _ = req("/pet/api/char/trait", "POST", {"trait": "不存在"}, COOKIE)
ok(st == 400, "未知性情 → 400")

st, raw, _ = req("/pet/api/char/name", "POST", {"name": "青豆"}, COOKIE)
d = json.loads(raw)
ok(st == 200 and d["char"]["name"] == "青豆", "POST /pet/api/char/name 200")

# 人格改名字 → 人物卡跟着变
st, raw, _ = req("/pet/api/config", "POST", {"persona": {"name": "小雪"}}, COOKIE)
d = json.loads(raw)
ok(st == 200, "POST /pet/api/config 200")
ok(d.get("char") and d["char"]["name"] == "小雪", "人格改名字 → 人物卡同步")

# 复位现在走 reborn (要读者点头): 不带 confirm → 400 并回现状
st, raw, _ = req("/pet/api/char/reset", "POST", {}, COOKIE)
d = json.loads(raw)
ok(st == 400 and "confirm" in d.get("error", ""), "不带 confirm 的复位被挡下")
st, raw, _ = req("/pet/api/char/reset", "POST", {"confirm": True}, COOKIE)
d = json.loads(raw)
ok(st == 200 and d["char"]["level"] == 1, "POST /pet/api/char/reset 200 且重置")
ok(d["char"]["school"] is None, "重置后无门派")
ok(d.get("cleared", {}).get("adventures") is not None, "复位报告清了多少故事")
st, raw, _ = req("/pet/api/char/reset", "GET", cookie=COOKIE)
ok(st == 405, "GET reset → 405")

st, raw, _ = req("/pet/api/char/nope", cookie=COOKIE)
ok(st == 404, "未知接口 → 404")

# ── 页面里有这些段 ─────────────────────────────────────
st, raw, _ = req("/pet/", cookie=COOKIE)
html = raw.decode("utf-8", "replace")
ok(st == 200, "GET /pet/ 200")
for want in ("六项根骨", "门派", "技能与绝招", "性情", "经脉", "模型接入"):
    ok(want in html, f"页面含「{want}」段")
ok('id="attr-rows"' in html and 'id="skill-rows"' in html, "有根骨/技能表格挂点")
ok('id="trait-cards"' in html and 'id="vein-list"' in html, "有性情/经脉挂点")
ok('id="char-stats"' in html, "有状态条挂点")

# ── 收尾 ───────────────────────────────────────────────
print(f"\n{'=' * 46}")
print(f"PASS {PASS}  FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
