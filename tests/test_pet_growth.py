"""书宠成长接口 (陪读 → 修为) —— 公式 + HTTP 测试。

跑法: cd <仓库根> && python3 tests/test_pet_growth.py

验的事:
  * 换算: 分钟 → 修为 / 升级 / 潜能 / 声望
  * 技能自动长进 (基本内功) 按累计分钟, 不是按这一次
  * 经脉按累计分钟推进
  * 只增不减 (传 0 / 负数不改任何东西)
  * 研讨长技能不许超上限
  * HTTP: /pet/api/char/grow 与 /pet/api/char/skill

全部离线: 不调用真实 LLM, 不碰云端账号, 落盘都在临时目录。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = Path(tempfile.mkdtemp(prefix="recto-petgrowth-"))
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

from recto.pet import charsheet as cs  # noqa: E402
from recto.pet import growth as g  # noqa: E402

PASS = FAIL = 0


def ok(cond, name):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS", name)
    else:
        FAIL += 1
        print("FAIL", name)


# ── 换算比例 ─────────────────────────────────────────────
ok(g.EXP_PER_MINUTE == 2, "每分钟 2 点修为")
ok(g.POT_PER_LEVEL == 3, "每级 3 点潜能")
ok(g.exp_for(30) == 60, f"exp_for(30) = {g.exp_for(30)}")
ok(g.exp_for(-5) == 0, "负分钟 → 0")
ok(g.exp_for(0) == 0, "零分钟 → 0")

# ── 一次陪读 ─────────────────────────────────────────────
ch = cs.new_char()
r = g.award_minutes(ch, 100)
ok(r["minutes"] == 100, "记了 100 分钟")
ok(r["exp"] == 200, f"修为 +200 got {r['exp']}")
ok(r["levels"] >= 1, f"至少升 1 级 got {r['levels']}")
ok(ch["level"] >= 2, f"等级 {ch['level']}")
ok(ch["pot"] == g.POT_PER_LEVEL * r["levels"], f"潜能 {ch['pot']}")
ok(ch["fame"] == g.FAME_PER_LEVEL * r["levels"], f"声望 {ch['fame']}")
ok(ch["minutes"] == 100, "累计分钟 100")
# 技能成长要慢 (2026-11-12): 300 分钟 = 故事内五天 = 一级
ok(not ch["skills"].get(g.AUTO_SKILL), f"100 分钟还不该长技能 got {ch['skills']}")

# 第二次: 技能按累计分钟, 不是按这次
r2 = g.award_minutes(ch, 100)
ok(not ch["skills"].get(g.AUTO_SKILL),
   f"累计 200 分钟仍不够 (300 分钟一级) got {ch['skills'].get(g.AUTO_SKILL)}")
ok(not r2["skills"], f"本次不该给技能 got {r2['skills']}")

r2b = g.award_minutes(ch, 200)
ok(ch["skills"].get(g.AUTO_SKILL) == 1,
   f"累计 400 分钟 → 基本内功 1 级 got {ch['skills'].get(g.AUTO_SKILL)}")
ok(r2b["skills"].get(g.AUTO_SKILL) == 1, f"本次增益 1 级 got {r2b['skills']}")

# ── 只增不减 ─────────────────────────────────────────────
before = (ch["level"], ch["exp"], ch["pot"], ch["minutes"], ch["skills"])
r0 = g.award_minutes(ch, 0)
rneg = g.award_minutes(ch, -30)
after = (ch["level"], ch["exp"], ch["pot"], ch["minutes"], ch["skills"])
ok(r0["exp"] == 0 and rneg["exp"] == 0, "0 / 负数不涨修为")
ok(before == after, f"0 / 负数不改存档 {before} vs {after}")

# 超长单次封顶
ch2 = cs.new_char()
r3 = g.award_minutes(ch2, 100000)
ok(r3["minutes"] == g.MAX_MINUTES_PER_CALL,
   f"单次封顶 {g.MAX_MINUTES_PER_CALL} got {r3['minutes']}")

# ── 经脉 ─────────────────────────────────────────────────
ch3 = cs.new_char()
g.award_minutes(ch3, 130)   # 第一层要 120 分钟
ok(ch3["vein"] == 1, f"130 分钟 → 通 1 层 got {ch3['vein']}")
g.award_minutes(ch3, 200)   # 累计 330 → 第二层 (300)
ok(ch3["vein"] == 2, f"330 分钟 → 通 2 层 got {ch3['vein']}")
g.award_minutes(ch3, 300)   # 累计 630 → 第三层 (540)
ok(ch3["vein"] == 3, f"630 分钟 → 通 3 层 got {ch3['vein']}")

# ── 技能限速: 每 SKILL_DAYS_PER_LEVEL 个故事日 += 1 ─────────
ok(g.SKILL_DAYS_PER_LEVEL == 80, "技能 80 个故事日一级 (现实约 8 小时)")
cg = cs.new_char()
cg["minutes"] = 6 * 3             # 故事内第 3 天 (6 分钟一天)
aw_g1 = g.award_judged(cg, {"exp": 100, "skills": {"jibenneigong": 1}}, max_exp=200)
ok(not aw_g1["skills"], f"第 3 天不该长技能 got {aw_g1['skills']}")
ok(aw_g1["skill_gate"]["blocked"] is True, "闸门关着")
cg["minutes"] = 6 * 90            # 第 90 天 (闸门 80 天)
aw_g2 = g.award_judged(cg, {"exp": 100, "skills": {"jibenneigong": 1}}, max_exp=200)
ok(aw_g2["skills"] == {"jibenneigong": 1}, f"第 90 天长一级 got {aw_g2['skills']}")
ok(int(cg["last_skill_day"]) == 90, "记住这次长技能的故事日")
aw_g3 = g.award_judged(cg, {"exp": 100, "skills": {"jibenneigong": 1}}, max_exp=200)
ok(not aw_g3["skills"], "同一天内不会连涨")

# ── 先天属性: 升级偶得 (成长才看得见) ──────────────────────
ok(g.BORN_ON_LEVEL.get("wu") == 3, "悟性每 3 级 +1 (慢)")
cb = cs.new_char()
cb["minutes"] = 60000
g.award_minutes(cb, g.MAX_MINUTES_PER_CALL)      # 480 分钟 → 960 修为 → 3 级
ok(int(cb["level"]) >= 3, f"升到 3 级以上 got Lv.{cb['level']}")
ok(int((cb["born"] or {}).get("wu") or 0) == 1, f"3 级得悟性先天 +1 got {cb.get('born')}")
eff = cs.effective_attrs(cb)
ok(eff["wu"] == 11, f"先天进实际值 (10+1) got {eff['wu']}")
before_eff = cs.effective_attrs(cb)
cb["exp"] = 900000
cb2 = cs.new_char()
# 判定口径同样给先天
cj = g.award_judged(cb, {"exp": 900000}, max_exp=900000)
ok(int(cb["level"]) > 3, f"判定也能升级 got Lv.{cb['level']}")
ok(bool(cj.get("born")) or int((cb["born"] or {}).get("wu") or 0) >= 1,
   f"判定升级也给先天 got {cj.get('born')} / {cb.get('born')}")

# ── 研讨: 长技能不许超上限 ────────────────────────────────
ch4 = cs.new_char()
ch4["exp"] = 100
r4 = g.skill_boost(ch4, "jibenneigong", 1)
ok(r4.get("ok"), f"研讨成功 got {r4}")
ok(ch4["skills"]["jibenneigong"] == 1, "基本内功 1 级")
r5 = g.skill_boost(ch4, "jibenneigong", 999)
ok(not r5.get("ok") and "上限" in r5.get("error", ""),
   f"超上限被拒 got {r5.get('error','')}")
r6 = g.skill_boost(ch4, "不存在", 1)
ok(not r6.get("ok"), "未知技能被拒")
r7 = g.skill_boost(ch4, "jibenneigong", 0)
ok(not r7.get("ok"), "等级 0 被拒")

# ── 规则随 schema 一起发 ────────────────────────────────
sch = cs.schema()
ok("growth" not in sch, "cs.schema() 本身不带 growth (由 API 层合并)")
ok(g.GROWTH_RULES["exp_per_minute"] == g.EXP_PER_MINUTE, "GROWTH_RULES 与常量一致")
ok(isinstance(g.GROWTH_RULES.get("pending"), list) and g.GROWTH_RULES["pending"],
   "GROWTH_RULES 列出待定项")

# ── HTTP ─────────────────────────────────────────────────
import http.client  # noqa: E402
import threading  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

from recto.app import server  # noqa: E402

SRV = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
SRV.daemon_threads = True
threading.Thread(target=SRV.serve_forever, daemon=True).start()
PORT = SRV.server_address[1]


def req(path, method="GET", body=None, cookie=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    h = {}
    if cookie:
        h["Cookie"] = cookie
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    c.request(method, path, body=payload, headers=h)
    r = c.getresponse()
    raw = r.read()
    c.close()
    return r.status, raw, r.getheader("Set-Cookie")


st, raw, ck = req("/pet/api/char")
from recto.app import webauth  # noqa: E402

COOKIE = webauth.set_cookie("root")
ok(st == 401, f"没登录 → 401 got {st}")

st, raw, _ = req("/pet/api/char/grow", "POST", {"minutes": 120}, COOKIE)
d = json.loads(raw)
ok(st == 200, f"POST /pet/api/char/grow 200 got {st}")
ok(d.get("gain", {}).get("minutes") == 120, f"成长明细带分钟 got {d.get('gain')}")
ok(d["char"]["level"] >= 2, f"升级了 got Lv.{d['char']['level']}")
ok(d["char"]["minutes"] == 120, f"累计分钟 {d['char']['minutes']}")

st, raw, _ = req("/pet/api/char/grow", "GET", cookie=COOKIE)
ok(st == 405, f"GET grow → 405 got {st}")

st, raw, _ = req("/pet/api/char/skill", "POST", {"skill": "jibenneigong", "levels": 1}, COOKIE)
d = json.loads(raw)
ok(st == 200, f"POST /pet/api/char/skill 200 got {st}")
ok(d["char"]["skills"], "技能表非空")

st, raw, _ = req("/pet/api/char/skill", "POST", {"skill": "不存在"}, COOKIE)
ok(st == 400, f"未知技能 → 400 got {st}")

st, raw, _ = req("/pet/api/char/schema", cookie=COOKIE)
d = json.loads(raw)
ok(st == 200 and "growth" in d, "schema 带 growth 规则")
ok(d["growth"]["exp_per_minute"] == 2, "schema 的 growth 数值对")

print(f"\n{'=' * 46}")
print(f"PASS {PASS}  FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
