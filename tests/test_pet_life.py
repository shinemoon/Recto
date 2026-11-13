"""书宠的时序 —— 故事内的一天 / 八十岁退隐 / 传承给十二岁那位。

跑法: cd <仓库根> && python3 tests/test_pet_life.py

验的事:
  * 每 60 分钟阅读 = 故事内 10 天; 年岁是除出来的, 不是另记的数
  * 到八十岁 → 这一回合写的是"退隐 + 传承", 之后停在 retiring 不再长大
  * 传承必须读者点头 (confirm), 且只在退隐/到岁数后允许
  * 新人从 12 岁起: 家学三成 / 门派按类型 / 上一代存档留档 / lineage 记账
  * 页面: 故事在最前, 技能折叠, 传承卡只在需要时出现
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = Path(tempfile.mkdtemp(prefix="recto-petlife-"))
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
from recto.pet import chronicle as ch  # noqa: E402
from recto.pet import life  # noqa: E402
from recto.pet import lore  # noqa: E402
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


def near(a, b, eps=1e-6):
    return abs(float(a) - float(b)) <= eps


# ── 1. 时序换算: 一小时 = 十天 (6 分钟一天) ───────────────
ch0 = cs.new_char()
v0 = life.view(ch0)
ok(v0["day"] == 0 and v0["day_no"] == 1, f"刚出门 → 第 1 日 got {v0['day_no']}")
ok(v0["age"] == 12 and v0["stage"] == "growing", f"12 岁 growing got {v0}")
ok(v0["minutes_to_next_day"] == 6, f"还差 6 分钟到第一日 got {v0['minutes_to_next_day']}")
ok(v0["days_to_retire"] == (80 - 12) * 365, f"距退隐 68 年 got {v0['days_to_retire']}")

ch0["minutes"] = 6
ok(life.story_day(ch0) == 1, "6 分钟 → 故事内第 1 天")
ok(life.age_label(ch0).startswith("12 岁"), f"还是一天, 不到一岁 got {life.age_label(ch0)}")
ch0["minutes"] = 6 * 365          # 一小时 = 十天 ⇒ 一年 365 天 = 36.5 小时
ok(life.age_years(ch0) == 13, f"一年 365 天 → 13 岁 got {life.age_years(ch0)}")
ok(life.story_day(ch0) == 365 and life.minutes_to_next_day(ch0) == 6,
   "刚好整日时下一日还差一天的量 (6 分钟)")

# 配置可调 (测试实例用 30 分钟一天)
os.environ["RECTO_PET_MINUTES_PER_DAY"] = "30"
ok(life.cfg()["minutes_per_day"] == 30, "环境变量可调一天的长度")
ok(life.story_day({"minutes": 60}) == 2, "30 分钟一天 → 60 分钟两天")
os.environ.pop("RECTO_PET_MINUTES_PER_DAY", None)
ok(life.cfg()["minutes_per_day"] == 6, "去掉环境变量回到 6 (一小时 = 十天)")

# ── 2. 退隐判定 ──────────────────────────────────────
ch1 = cs.new_char()
ch1["minutes"] = (life.cfg()["retire_age"] - 12) * 365 * life.cfg()["minutes_per_day"] - 1
ok(life.age_years(ch1) == 79 and not life.ready_to_retire(ch1), "少一分钟还在 79 岁")
ch1["minutes"] += 1
ok(life.age_years(ch1) == 80 and life.ready_to_retire(ch1), "满八十 → 该退隐了")
ok(life.view(ch1)["days_to_retire"] == 0, "距退隐 0 天")

ch1["life"] = {"stage": "retiring"}
ok(not life.ready_to_retire(ch1) and life.stage(ch1) == "retiring",
   "已退隐就不再重复触发")

# ── 3. 传承: 家学 / 门派 / 存档 ──────────────────────────
old = cs.new_char("老墨")
old["generation"] = 1
old["school"] = "moshi"
old["skills"] = {"jianfa": 20, "daofa": 9}
old["fame"] = 200
old["morality"] = 40
old["minutes"] = (80 - 12) * 365 * life.cfg()["minutes_per_day"]
old["life"] = {"stage": "retiring", "heir": {"type": "disciple", "name": "小砚",
                                             "reason": "门里最小那个一直跟着"}}
res = life.inherit(old, "disciple")
new = res["char"]
ok(new["name"] == "小砚", f"用传承人的名字 got {new['name']}")
ok(new["generation"] == 2, f"第 2 代 got {new['generation']}")
ok(new["minutes"] == 0 and life.age_years(new) == 12, "新人从 12 岁、零分钟起")
ok(new["school"] == "moshi", "徒弟承师门")
ok(new["skills"].get("jianfa") == 6, f"家学三成 (20 → 6) got {new['skills']}")
ok("xuantiejian" not in new["skills"], f"只传最高那一门 got {new['skills']}")
ok(new["fame"] == 10, f"声望余荫 200//20 = 10 got {new['fame']}")
ok(new["morality"] == 20, f"心性留一半 got {new['morality']}")
ok(new["pot"] == life.HEIR_POT, f"起步潜能 {life.HEIR_POT} got {new['pot']}")
ok(new["life"]["stage"] == "growing", "新人重新在路上")
ok(len(new["lineage"]) == 1, f"lineage 记了上一代 got {new['lineage']}")
rec = new["lineage"][0]
ok(rec["name"] == "老墨" and rec["level"] == 1 and rec["heir_type"] == "disciple",
   f"记录里是上一代 got {rec}")
ok(Path(res["archive"]).exists(), f"上一代存档已落盘 got {res['archive']}")
saved = json.loads(Path(res["archive"]).read_text("utf-8"))
ok(saved["_age"] == 80 and saved["name"] == "老墨", "存档里带着年岁")

# 学生不入门派
old2 = cs.new_char("老李")
old2["school"] = "tieyan"
old2["skills"] = {"jianfa": 3}
res2 = life.inherit(old2, "student")
ok(res2["char"]["school"] == "", "学生不承门派")
ok(res2["char"]["skills"].get("jianfa") == 1, "三成最少 1 级")

# 未知类型 → 按徒弟处理
res3 = life.inherit(cs.new_char("老赵"), "bogus")
ok(res3["heir_type"] == "disciple", f"未知类型兜底徒弟 got {res3['heir_type']}")

# ── 4. 后台推演: 到岁数 → 写退隐 / 之后不再长大 ──────────────
BOOK = "lifebookkey"


def legacy_chat(system, user, **kw):
    head = system.split("\n")[0]
    if "退隐与传承" in system or "退隐" in head:
        return json.dumps({
            "title": "终回 · 关门弟子", "place": "旧屋",
            "body": "他把剑挂回墙上, 又取出一方旧砚递给院子里扫地的孩子。" * 12,
            "terms_used": [], "themes_used": ["传承"], "growth_shown": [],
            "days": 1,
            "heir": {"type": "child", "name": "小潭", "reason": "儿子一直跟着"},
            "tail": "门关上。", "verdict": "收手。"}, ensure_ascii=False)
    if "读书记录 → 成长判定" in system:
        return json.dumps({"assessment": {"depth": "深读", "breadth": "专一",
                                          "one_line": "读得沉"},
                           "growth": {"exp": 200, "pot": 1, "fame": 3,
                                      "skills": {}, "reason": "读了一小时"},
                           "next_hint": ""}, ensure_ascii=False)
    return json.dumps({"title": "第N回", "place": "某处", "body": "他走了一趟。" * 40,
                       "terms_used": [], "themes_used": [], "tail": "",
                       "verdict": ""}, ensure_ascii=False)


lore.chat = legacy_chat
elder = cs.new_char("小墨")
elder["minutes"] = (80 - 12) * 365 * life.cfg()["minutes_per_day"]
elder["credited_minutes"] = elder["minutes"]   # 存档里两个锚点必须一致
elder["life"] = {"stage": "growing"}
cs.save_char(elder)

today = clock.fmt_sec(time.time(), "%Y-%m-%d")
rc = rs.conn()
rc.execute("INSERT OR REPLACE INTO rtime (book, day, bitmap, minutes, first_at, updated_at)"
           " VALUES (?,?,?,?,?,?)", (BOOK, today, "0", 60, clock.iso_local(),
                                     clock.iso_local()))
rc.commit()

run = ch.run(trigger="manual", force=True)
ok(run["status"] == "ok", f"到岁数这一回合仍然跑起来 got {run.get('status')} {run.get('error','')}")
ok(run.get("retiring") is True, "标记成退隐那一回合")
adv = run.get("adventure") or {}
ok("终回" in adv.get("title", ""), f"写的是终回 got {adv.get('title')}")
ok((adv.get("heir") or {}).get("type") == "child", f"传承人提议带类型 got {adv.get('heir')}")
st_now = cs.load_char()
ok(life.stage(st_now) == "retiring", "存档进了 retiring")
ok((st_now.get("life") or {}).get("heir", {}).get("name") == "小潭",
   f"提议落进存档 got {st_now.get('life')}")
rows = ch.adventures(limit=3)
ok(rows and (rows[0].get("growth") or {}).get("tag") == "retire",
   f"游历带 retire 标记 got {rows[0].get('growth')}")

again = ch.run(trigger="manual", force=True)
ok(again["status"] == "retired", f"退隐后不再推演 got {again.get('status')}")
ok(len(ch.adventures(limit=9)) == 1, "不再产生新故事")

# ── 5. HTTP: 确认传承 ────────────────────────────────
from recto.app import server, webauth  # noqa: E402

SRV = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
SRV.daemon_threads = True
threading.Thread(target=SRV.serve_forever, daemon=True).start()
PORT = SRV.server_address[1]
COOKIE = webauth.set_cookie("root")


def req(path, method="GET", body=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=15)
    h = {"Cookie": COOKIE}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    c.request(method, path, body=payload, headers=h)
    r = c.getresponse()
    raw = r.read()
    c.close()
    return r.status, raw


st, raw = req("/pet/api/char/life")
d = json.loads(raw)
ok(st == 200 and d["stage"] == "retiring", f"GET /char/life got {st} {d.get('stage')}")

st, raw = req("/pet/api/char/inherit", "POST", {"heir_type": "child"})
ok(st == 400 and "confirm" in raw.decode("utf-8"), f"不带 confirm 被拒 got {st}")

st, raw = req("/pet/api/char/inherit", "POST",
              {"heir_type": "child", "name": "小潭", "confirm": True})
d = json.loads(raw)
ok(st == 200 and d.get("ok"), f"确认传承 200 got {st} {raw[:120]}")
ok(d["char"]["generation"] == 2, f"换代 got {d['char'].get('generation')}")
ok(d["char"]["life"]["stage"] == "growing", "新人重新在路上")
ok(d["record"]["name"] == "小墨", f"记录上一代 got {d['record'].get('name')}")
ok(Path(d["archive"]).exists(), "存档落盘")
ok(ch.load_state().get("story_day") == 0, "传承后故事时钟归零 (否则新一代永远不出门)")

st, raw = req("/pet/api/char/inherit", "POST",
              {"heir_type": "child", "confirm": True})
ok(st == 409, f"没退隐/没到岁数 → 409 got {st}")

st, raw = req("/pet/api/lineage")
d = json.loads(raw)
ok(st == 200 and d["generation"] == 2 and len(d["lineage"]) == 1,
   f"GET /lineage got {st} {d.get('generation')}")

# 页面: 故事在最前 / 技能折叠 / 传承卡
from recto.app import pet as _petapp  # noqa: E402

html = _petapp.page_html()
ok(html.index('id="card-chronicle"') < html.index('id="card-hero"'),
   "故事卡排在人物卡前面")
ok('id="card-legacy"' in html and 'id="btn-inherit"' in html, "页面有传承卡与确认按钮")
ok('id="story-clock"' in html and 'id="story-meta"' in html, "故事时钟挂在故事卡上")
ok('<details class="cs-fold">' in html and '<details class="cs-fold" open>' not in html,
   "技能明细默认折叠")
for fid, label in (("fold-card-schools", "门派"), ("fold-card-traits", "性情"),
                   ("fold-card-veins", "经脉")):
    ok('id="%s"' % fid in html, "%s明细也折叠" % label)
ok(html.count('<details') == html.count('</details>'), "details 标签配对")
ok(html.count("<div") == html.count("</div>"), "div 标签配对")
js = _petapp._UI_DIR.joinpath("pet.js").read_text("utf-8")
ok("renderLife" in js and "/api/char/inherit" in js, "前端接了传承")
ok("本回所显" in js, "故事卡展示成长的痕迹")
ok("一小时阅读" in html and "十天" in html, "页面写明节奏 (一小时 = 十天)")

rules = __import__("recto.pet.growth", fromlist=["rules"]).rules()
ok(rules["time"]["minutes_per_story_day"] == 6 and rules["time"]["retire_age"] == 80,
   f"schema 里带时序口径 got {rules['time']}")
ok(rules["skill_days_per_level"] >= 5, f"技能限速写进规则 got {rules['skill_days_per_level']}")

print()
print("=" * 46)
print("PASS", PASS, " FAIL", FAIL)
sys.exit(1 if FAIL else 0)
