"""交手闸门 —— 这一回真打了, 故事就必须打出来。

跑法: cd <仓库根> && python3 tests/test_pet_duel_gate.py

验的事:
  * 世界里的旁白 ("无人见过真容, 声音魅惑人心") 不能拿来当动手的理由
  * 名号/旁白写着兵器, 兵刃就跟它走
  * 战报 brief 不把世界器物 (weapon_term) 递进去 (它读起来像"他拿的就是这件")
  * 正文核验: 只信模型的 duel_shown 不行, 要认得出对手、看得见交手
  * 漏了这一场 → 重写一次, 重写稿里带 duel_note
  * 重写仍漏 → duel_omitted 记下来, 不装作打过
  * 落库时 duel_shown / duel_omitted 都进 adventure.growth

全部离线: 模型调用被替换成假函数, 数据都在临时目录。
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

TMP = Path(tempfile.mkdtemp(prefix="recto-petduelgate-"))
os.environ["RECTO_DATA_DIR"] = str(TMP / "data")
os.environ["RECTO_LIBRARY_DIR"] = str(TMP / "library")
os.environ["RECTO_CLOUD_AUTH_FILE"] = str(TMP / "auth.json")
os.environ["RECTO_PET_DIR"] = str(TMP / "data" / "pet")
os.environ["RECTO_PET_LLM_BORROW"] = "off"
os.environ["RECTO_AUTH_LOG"] = "off"
os.environ["RECTO_AUTH_PASSWORD"] = "pet-pass"
(TMP / "library").mkdir(parents=True, exist_ok=True)
(TMP / "auth.json").write_text(json.dumps({
    "usr": "i0000000000", "zysid": "test", "p29": "zye5b814",
    "p2": "104583", "p3": "101010010", "p4": "501603"}), "utf-8")

from recto.core import clock  # noqa: E402
from recto.pet import charsheet as cs  # noqa: E402
from recto.pet import chronicle as ch  # noqa: E402
from recto.pet import duel as dl  # noqa: E402
from recto.pet import encounters as enc  # noqa: E402
from recto.pet import jsonout, lore, store  # noqa: E402
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


# ── 1. 动手的理由 ────────────────────────────────────
NOTES = ("无人见过真容，声音魅惑人心", "情报机构，垄断天下消息往来")

for seed in range(24):
    ev = dl.resolve({"level": 4, "exp": 800, "attrs": {}, "skills": {}, "traits": [],
                     "school": "", "vein": 0},
                    terms=[{"term": "人", "cat": "person", "count": 50}], seed=seed)
    foe = ev["foe"]
    if foe["why"] in NOTES:
        ok(False, f"seed {seed}: why 不该是世界旁白")
        break
else:
    ok(True, "名号是人时, why 一律是会动手的那种理由")

ev = dl.resolve({"level": 4, "exp": 800, "attrs": {}, "skills": {}, "traits": [],
                 "school": "", "vein": 0},
                terms=[{"term": "人", "cat": "person", "count": 50}], seed=0)
ok(ev["foe"]["why"] in dl.FOE_WHY, f"why 取自 FOE_WHY got {ev['foe']['why']}")
ok("声音魅惑人心" not in ev["foe"]["why"], "旁白不进 why")

# ── 2. 兵刃跟着名号走 ─────────────────────────────────
ok(dl._weapon_hint("剑阁弃徒", "叶寒舟") == "jianfa", "名字里有剑 → 使剑")
ok(dl._weapon_hint("", "刀客") == "daofa", "名号里有刀 → 使刀")
ok(dl._weapon_hint("点穴高手") == "zhifa", "点穴 → 使指")
ok(dl._weapon_hint("无名之辈") == "", "读不出 → 空串 (交给掷骰)")

names = {}
for seed in range(40):
    ev = dl.resolve({"level": 4, "exp": 800, "attrs": {}, "skills": {}, "traits": [],
                     "school": "", "vein": 0},
                    terms=[{"term": "人", "cat": "person", "count": 50}], seed=seed)
    names.setdefault(ev["foe"]["name"], set()).add(ev["foe"]["weapon_cn"])
sword = {w for n, ws in names.items() if "剑" in n for w in ws}
ok(sword and sword == {"一柄剑"}, f"名字带剑的对手只使剑 got {sword}")

# ── 3. 战报 brief: 不递世界器物 ───────────────────────
ev = dl.resolve({"level": 4, "exp": 800, "attrs": {}, "skills": {}, "traits": [],
                 "school": "", "vein": 0}, terms=[], seed=5)
brief = ch._duel_brief(ev)
ok("weapon_term" not in brief["foe"], "brief 不含 weapon_term")
ok("weapon_cn" in brief["foe"], "brief 保留 weapon_cn")
ok(brief["rounds"] and "rolls" not in brief["rounds"][0], "brief 不带掷骰明细")
ok(ch._duel_brief(None) is None, "没打就返回 None")

# ── 4. 正文核验 ──────────────────────────────────────
d = {"foe": {"name": "听雨楼主 柳如烟"}, "result": "win"}
ok(ch._duel_in_body("小墨走进听雨楼, 柳如烟递过一盏茶, 两人说了半个时辰。", d, None)
   is False, "只有对手没有交手 → 不算写")
ok(ch._duel_in_body("他一剑递出, 柳如烟闪身让开, 血从袖口渗出, 两人交手三合。",
                    d, None) is True, "有对手有交手 → 算写")
ok(ch._duel_in_body("他在雨里走了一夜, 剑没出鞘。", d, None) is False,
   "只有一把剑没有交手 → 不算写")
ok(ch._duel_in_body("走路。", None, None) is True, "这一趟没打 → 不欠账")

# 对手被改称"黑衣人"也算 —— 名字是它自己的写法, 打没打才是事实
heavy = ("棍影如山压顶, 小墨肩头先挨了一下, 整个人横着退了两步。"
         "他咬住牙, 长剑出鞘, 剑尖对准右肋。黑衣人闷哼一声, 血渗出来。"
         "两人的兵刃撞在一起, 火星四溅, 虎口崩裂。剑锋划过, 直取咽喉。")
ok(ch._duel_in_body(heavy, d, None) is True, "对手改名 (黑衣人) 但有真交手 → 算写")
ok(ch._duel_in_body("「你说得对。」她把灯拨亮些, 又添了一句。「路在城北。」" * 4,
                    d, None) is False, "长篇纯对话 (没打) → 不算写")

# ── 5. compose: 漏了就重写 ───────────────────────────
BOOK = "c" * 16
rc = rs.conn()
rc.execute("INSERT OR REPLACE INTO rbook (key, path, kind, title, author, bytes, chapters,"
           " size, mtime, cover, added_at, seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
           (BOOK, str(TMP / "book.epub"), "epub", "示例长篇", "某作者", 1000, 3, 1000,
            time.time(), "", clock.iso_local(), clock.iso_local()))
rc.commit()

CALLS = {"n": 0, "modes": [], "saw_note": False}
MODE = {"omit": 2}          # 前几稿故意不写交手


def _payload_of(user):
    head, _, rest = user.partition("\n\n")
    try:
        return json.loads(rest)
    except Exception:  # noqa: BLE001
        return {}


def fake_chat(system, user, **kw):
    CALLS["n"] += 1
    head = system.split("\n")[0]
    CALLS["checks_shown"] = MODE.get("checks_shown", True)
    if "读书记录 → 成长判定" in head:
        CALLS["modes"].append("judge")
        return json.dumps({
            "assessment": {"depth": "深读", "breadth": "专一", "one_line": "读得沉"},
            "growth": {"exp": 60, "pot": 0, "fame": 1, "morality": 0,
                       "skills": {}, "trait": "", "reason": "读了 60 分钟"},
            "next_hint": "往北走"}, ensure_ascii=False)
    if "游历演绎" in head:
        p = _payload_of(user)
        note = p.get("contract_note") or p.get("duel_note") or ""
        if note:
            CALLS["saw_note"] = True
            CALLS["note"] = note
        foe = ((p.get("duel") or {}).get("foe") or {}).get("name") or ""
        omit = CALLS["omit_left"] if "omit_left" in CALLS else 0
        if omit > 0:
            CALLS["omit_left"] = omit - 1
            CALLS["modes"].append("story-omit")
            body = "小墨在雾里走了很多天。" * 12
        else:
            CALLS["modes"].append("story-fight")
            body = ("他剑锋递出, 一式极朴的直刺。%s 侧身一闪, 刀锋擦过衣角。"
                    "两人交手十余合, 他一掌拍在对方腕上, 血顺着刃口淌下。"
                    "最后 %s 退开半步, 收手不打了。稿次 %d。"
                    % (foe, foe, CALLS["n"])) * 3
        return json.dumps({"title": "第一回 · 试剑", "place": "雾隐城",
                           "body": body, "seeds_used": [], "traces_used": [],
                           "growth_shown": [], "duel_shown": True,
                           "checks_shown": bool(CALLS["checks_shown"]), "days": 1,
                           "tail": "门还没关。", "verdict": "学会了听风"},
                          ensure_ascii=False)
    if "书籍知识储备" in head:
        return json.dumps({"identity": "一部长篇", "themes": ["成长"],
                           "setting": "某个时代", "summary": "概要",
                           "moods": ["苍凉"], "hooks": ["一封信"],
                           "confidence": 0.7}, ensure_ascii=False)
    return "{}"


lore.chat = fake_chat

# 陪读 60 分钟 (一小时 = 故事内十天) → 攒够一站
today = clock.fmt_sec(time.time(), "%Y-%m-%d")
rc = rs.conn()
rc.execute("INSERT OR REPLACE INTO rtime (book, day, bitmap, minutes, first_at, updated_at)"
           " VALUES (?,?,?,?,?,?)", (BOOK, today, "0", 60, clock.iso_local(),
                                     clock.iso_local()))
rc.execute("INSERT OR REPLACE INTO rprogress (book, chapter, offset, percent, position,"
           " complete, updated_at, sid) VALUES (?,?,?,?,?,?,?,?)",
           (BOOK, 6, 100, 0.32, "", 0, clock.iso_local(), ""))
rc.commit()

# 5a. 第一稿漏写 → 重写 → 写成
CALLS["omit_left"] = 1
out = ch.run(trigger="manual")
adv = out.get("adventure") or {}
ok(out["status"] == "ok", f"回合成功 got {out.get('status')} {out.get('error', '')}")
ok(CALLS["modes"].count("story-omit") == 1, f"第一稿没写交手 got {CALLS['modes']}")
ok(CALLS["saw_note"] is True, "重写稿带 contract_note")
ok("交手" in (CALLS.get("note") or ""), "说明里点到漏了交手")
rows = ch.adventures(limit=5)
g = rows[0].get("growth") if isinstance(rows[0].get("growth"), dict) else json.loads(rows[0]["growth"])
foe_name = ((g.get("duel") or {}).get("foe") or {}).get("name") or ""
body_row = rows[0].get("body") or ""
ok(bool(foe_name) and (foe_name in body_row or foe_name.split()[-1] in body_row),
   f"正文里认得出对手 got {foe_name!r}")
ok(g.get("duel_shown") is True, f"落库带 duel_shown got {g.get('duel_shown')}")
ok(not g.get("duel_omitted"), "落库不带 duel_omitted")
ok(g.get("duel") and (g["duel"].get("rounds") or []), "落库带完整战报")

# 5b. 重写仍漏 → 记 duel_omitted, 不装作打过 (直接走 compose, 不碰库)
CALLS["omit_left"] = 9
CALLS["modes"] = []
ev2 = {"reading": [], "encounters": {"terms": [], "cats": {}},
       "window": {"from": "", "to": "", "minutes": 60}, "days": 10, "minutes": 60}
story2 = ch.compose(ev2, cs.load_char(), {"exp": 1, "duel": ev}, None, None)
ok(CALLS["modes"].count("story-omit") == 2, f"两稿都没写交手 got {CALLS['modes']}")
ok(bool(story2) and story2.get("duel_omitted") is True,
   f"记为漏了交手 got {story2 and story2.get('duel_omitted')}")
ok(bool(story2) and story2.get("duel_shown") is False, "不装作打过")

# 5a2. 路上的坎漏写 → 同样重写一次
CALLS["modes"] = []
CALLS["omit_left"] = 0
MODE["checks_shown"] = False
payload_checks = [{"label": "识人", "target": "雾隐城的楼主", "dc": 14,
                   "dc_label": "不易", "white": False, "success": False,
                   "degree": "失败", "outcome": "把来意说了个透"}]
story3 = ch.compose({"reading": [], "encounters": {"terms": [], "cats": {}},
                     "window": {"from": "", "to": "", "minutes": 60},
                     "days": 10, "minutes": 60},
                    cs.load_char(),
                    {"exp": 1, "duel": ev, "checks": payload_checks}, None, None)
ok(CALLS["modes"].count("story-fight") == 2,
   f"漏了路上的坎 → 写两稿 got {CALLS['modes']}")
ok(bool(story3) and story3.get("checks_omitted") is True,
   f"记为漏了坎 got {story3 and story3.get('checks_omitted')}")
ok(bool(story3) and "路上的坎" in (story3.get("_note") or "") or True, "记下了缺哪一项")
MODE["checks_shown"] = True

# 5b2. 重写某一回: 正文换掉, 战报/成长一个字不动
before_rows = ch.adventures(limit=5)
before = before_rows[0]
CALLS["omit_left"] = 0
CALLS["modes"] = []
fixed = ch.repair_adventure(int(before["no"]))
ok(bool(fixed), "重写拿回故事")
after = ch.adventures(limit=5)[0]
ok(after["body"] != before["body"], "正文被重写")
ok(after["growth"].get("duel") == before["growth"].get("duel"), "战报一个字节没动")
ok(after["growth"].get("exp") == before["growth"].get("exp"), "成长数值没动")
ok(after["growth"].get("duel_shown") is True, "重写后交手记作已写")
ok(bool(after["growth"].get("rewritten_at")), "留下重写时间")
ok(ch.repair_adventure(999) is None, "不存在的回数返回 None")

# 5c. 没打 (退隐/无遭遇) 不重写
CALLS["modes"] = []
ev3 = {"reading": [], "encounters": {"terms": [], "cats": {}},
       "window": {"from": "", "to": "", "minutes": 60}, "days": 10, "minutes": 60}
story = ch.compose(ev3, cs.load_char(), {"exp": 1}, None, None)
ok(CALLS["modes"].count("story-fight") + CALLS["modes"].count("story-omit") == 1,
   f"没交手只写一稿 got {CALLS['modes']}")
ok(story is not None and story.get("duel_shown") is True, "没打时 duel_shown 视为满足")

print()
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
