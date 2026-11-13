"""书宠的"后台推演" —— 语料遭遇 / 书籍底细 / 判定成长 / 游历故事。

跑法: cd <仓库根> && python3 tests/test_pet_chronicle.py

验的事:
  * 语料遭遇: 前缀树最长匹配 / 只记打开过的章 / 同章 6 小时内不重复计
  * 书籍底细: 有检索 → 立卡; 无模型 → 不立卡但不炸; 章节储备只用节选
  * 判定成长: 上限 / 只增不减 / 技能白名单 / 性情可授性 / 分钟为 0 不给修为
  * 回合: 模型给出 JSON → 落地修为 + 存游历 + 记 credit (重跑不重复计)
  * 模型不可用 → 保底规则 (按分钟) 仍然落地
  * 没有活动 → idle, 不调模型、不写 cycle
  * HTTP: GET /pet/api/chronicle, POST /pet/api/chronicle/run, GET /pet/api/adventures,
          GET /pet/api/lore

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

TMP = Path(tempfile.mkdtemp(prefix="recto-petchron-"))
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
from recto.pet import encounters as enc  # noqa: E402
from recto.pet import guidance  # noqa: E402
from recto.pet import growth as g  # noqa: E402
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


# ── 0. 库 ────────────────────────────────────────────
c = store.conn()
tables = {r["name"] for r in store.rows(c, "SELECT name FROM sqlite_master WHERE type='table'")}
c.close()
for t in ("lore_book", "lore_chapter", "chapter_seen", "encounter", "cycle", "adventure"):
    ok(t in tables, f"表 {t} 已建")


# ── 1. 语料遭遇: 前缀树 ───────────────────────────────
words = {"剑来": "work", "剑": "object", "青石巷": "place", "陈平安": "person"}
counts = enc.scan("剑来里的陈平安走过青石巷, 又走过青石巷, 手里一柄剑。", words)
ok(counts.get("剑来") == 1, f"最长匹配优先 (剑来 != 剑) got {counts}")
ok(counts.get("陈平安") == 1, "人名命中")
ok(counts.get("青石巷") == 2, f"重复出现计 2 got {counts.get('青石巷')}")
ok(counts.get("剑") == 1, "短词只在没有长词时命中")


# ── 2. 书籍: 建卡 (模型假函数) ────────────────────────
BOOK = "b" * 16
rc = rs.conn()
rc.execute("INSERT OR REPLACE INTO rbook (key, path, kind, title, author, bytes, chapters,"
           " size, mtime, cover, added_at, seen_at)"
           " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
           (BOOK, str(TMP / "book.epub"), "epub", "示例长篇", "某作者", 1000, 3, 1000,
            time.time(), "", clock.iso_local(), clock.iso_local()))
rc.commit()

CALLS = {"n": 0, "systems": []}


def fake_chat(system, user, **kw):
    CALLS["n"] += 1
    CALLS["systems"].append(system[:40])
    head = system.split("\n")[0]
    if "书籍知识储备" in head:
        if "chapter_title" in user:
            return json.dumps({"gist": "他推门进了青石巷。", "motifs": ["青石", "门"],
                               "cast": ["他"]}, ensure_ascii=False)
        return json.dumps({"identity": "一部长篇", "themes": ["成长", "江湖"],
                           "setting": "某个时代", "summary": "不剧透的概要",
                           "moods": ["苍凉"], "hooks": ["一封信"], "confidence": 0.7},
                          ensure_ascii=False)
    if "读书记录 → 成长判定" in head:
        return json.dumps({
            "assessment": {"depth": "深读", "breadth": "专一", "one_line": "读得沉"},
            "growth": {"exp": 99999, "pot": 9, "fame": 99, "morality": 2,
                       "skills": {"jianfa": 1, "不存在的功夫": 1, "neigong_mochi": 1,
                                  "sword": 1},
                       "trait": "lengyan", "reason": "读了 40 分钟, 推进 6 章"},
            "next_hint": "往北走"}, ensure_ascii=False)
    if "游历演绎" in head:
        return json.dumps({"title": "第一回 · 青石巷", "place": "青石巷",
                           "body": "他推门出去, 巷子里有风。" * 20,
                           "terms_used": ["青石巷"], "themes_used": ["成长"],
                           "tail": "门还没关。", "verdict": "学会了听风"},
                          ensure_ascii=False)
    return "{}"


lore.chat = fake_chat      # 打桩: 所有模型调用走本地假数据

card = lore.build_book(BOOK, search_mode="off")
ok(card and card["identity"] == "一部长篇", f"书卡已立 got {card and card.get('identity')}")
ok(card["themes"] == ["成长", "江湖"], "主题是数组")
ok(card["searched"] == "off", f"检索关闭时如实记 off got {card.get('searched')}")
ok(lore.stats()["books"] == 1, "统计: 1 本立卡")
ok(json.loads(json.dumps(lore.all_cards()))[0]["title"] == "示例长篇", "卡片列表带书名")


# ── 3. 章节足迹 + 章节储备 ─────────────────────────────
r = enc.record_chapter(BOOK, 0, "他推门出去, 青石巷里没有人。", "第一章")
ok(r["skipped"] is False and r["chars"] > 0, f"章节足迹已记 got {r}")
r2 = enc.record_chapter(BOOK, 0, "他推门出去, 青石巷里没有人。", "第一章")
ok(r2["skipped"] is True, "同章 6 小时内不重复计")
pend = lore.pending_chapters(BOOK)
ok(len(pend) == 1, f"缺储备的章被排上队 got {pend}")
lch = lore.build_chapter(BOOK, 0)
ok(lch and lch["gist"].startswith("他推门"), f"章节储备已写 got {lch and lch.get('gist')}")
ok(lore.pending_chapters(BOOK) == [], "写完之后不再排队")


# ── 4. 判定: 夹逼 ────────────────────────────────────
ev = {"total_minutes": 40, "reading": [{"book": BOOK, "minutes": 40, "chapters": 6}],
      "window": {"from": "", "to": "", "hours": 1}}
raw = {"assessment": {}, "growth": {"exp": 99999, "pot": 9, "fame": 99, "morality": 9,
                                    "skills": {"jianfa": 1, "bogus": 1},
                                    "trait": ""}}
cl = ch.clamp(raw, ev)
ok(cl["growth"]["exp"] == 40 * 6, f"修为上限 = 分钟 × 6 got {cl['growth']['exp']}")
ok(cl["growth"]["pot"] == 3, f"潜能上限 3 got {cl['growth']['pot']}")
ok(cl["growth"]["fame"] == 20, f"声望上限 20 got {cl['growth']['fame']}")
ok(cl["growth"]["morality"] == 5, f"心性上限 5 got {cl['growth']['morality']}")
ok(list(cl["growth"]["skills"]) == ["jianfa"], f"技能白名单 got {cl['growth']['skills']}")
ok(cl["growth"]["trait"] == "", "未知性情被丢掉")

ev0 = {"total_minutes": 0, "reading": [], "window": {}}
cl0 = ch.clamp({"growth": {"exp": 500, "pot": 2, "fame": 10}}, ev0)
ok(cl0["growth"]["exp"] == 0 and cl0["growth"]["pot"] == 0, "没读就没有修为/潜能")

raw2 = {"growth": {"exp": -50, "pot": -1, "fame": -3, "morality": -99}}
cl2 = ch.clamp(raw2, ev)
ok(cl2["growth"]["exp"] == 0 and cl2["growth"]["pot"] == 0, "负数收敛为 0 (只增不减)")
ok(cl2["growth"]["morality"] == -5, f"心性下限 -5 got {cl2['growth']['morality']}")


# ── 5. award_judged: 落地 ────────────────────────────
cu = cs.new_char()
cu["minutes"] = 60 * 9          # 已在故事内走了 9 天 → 技能闸门开着
be = int(cu["exp"])
aw = g.award_judged(cu, {"exp": 250, "pot": 1, "fame": 6, "morality": -2,
                         "skills": {"jianfa": 1, "bogus": 1}}, max_exp=300)
ok(aw["exp"] == 250, f"修为按判定落地 got {aw['exp']}")
ok(int(cu["level"]) == 2 and int(cu["exp"]) == be + 250 - 100,
   f"升级把修为吃掉 (lvl={cu['level']} exp={cu['exp']})")
ok(aw["skills"] == {"jianfa": 1}, f"技能只落白名单 got {aw['skills']}")
ok(int(cu["morality"]) == -2, "心性可负 (但不超 5)")
aw2 = g.award_judged(cu, {"exp": 500, "pot": 0, "fame": 0}, max_exp=100)
ok(aw2["exp"] == 100 and aw2["clamped"]["exp"] is True, "超上限被夹且标出来")


# ── 6. 回合: 有活动 → 落地 + 游历 ──────────────────────
# 造一段陪读时长 (今天的 40 分钟) 与进度推进
today = clock.fmt_sec(time.time(), "%Y-%m-%d")
rc = rs.conn()
# 70 分钟 = 故事内一天多一点 (一小时阅读 = 一天) —— 攒够一天才出门
rc.execute("INSERT OR REPLACE INTO rtime (book, day, bitmap, minutes, first_at, updated_at)"
           " VALUES (?,?,?,?,?,?)", (BOOK, today, "0", 70, clock.iso_local(),
                                     clock.iso_local()))
rc.execute("INSERT OR REPLACE INTO rprogress (book, chapter, offset, percent, position,"
           " complete, updated_at, sid) VALUES (?,?,?,?,?,?,?,?)",
           (BOOK, 6, 100, 0.32, "", 0, clock.iso_local(), ""))
rc.commit()

out = ch.run(trigger="manual")
ok(out["status"] == "ok", f"回合成功 got {out.get('status')} {out.get('error', '')}")
ok(out["minutes"] == 70, f"窗口内新增 70 分钟 got {out['minutes']}")
ok(out["days"] == 1, f"故事内走过 1 天 got {out.get('days')}")
ok(out["growth"]["exp"] == 320, f"修为按故事日夹到 320 got {out['growth']['exp']}")
ok(out["life"]["day"] >= 1 and out["life"]["age"] == 12,
   f"故事时钟前进了 got {out.get('life')}")
ok(out["adventure"] and "青石巷" in out["adventure"]["title"], f"游历故事已写 got {out.get('adventure')}")
advs = ch.adventures(limit=5)
ok(len(advs) == 1 and advs[0]["no"] == 1, f"游历落库 got {[a['no'] for a in advs]}")
ok(ch.load_state()["cycle"] == 1, "回合数 +1")

rep = ch.run(trigger="manual")
ok(rep["status"] == "idle", f"同一段记录重跑 → idle (不重复给) got {rep.get('status')}")
ok(ch.load_state()["cycle"] == 1, "idle 不写 cycle 行")
ok(len(ch.adventures(limit=9)) == 1, "idle 不产生新故事")


# ── 6b. 故事节奏: 不足一天 → waiting (一小时阅读 = 一天) ─────────
st_before = int(ch.load_state().get("story_day") or 0)
rc = rs.conn()
rc.execute("UPDATE rtime SET minutes=90 WHERE book=? AND day=?", (BOOK, today))
rc.commit()
w = ch.run(trigger="manual")
ok(w["status"] == "waiting",
   f"才读 20 分钟 (不足一天) → 不出门 got {w.get('status')}")
ok(w.get("minutes_to_next_day", 0) > 0, "告诉人还差多少分钟到下一日")
ok(int(ch.load_state().get("story_day") or 0) == st_before, "故事日没有虚涨")


# ── 7. 模型不可用 → 保底 ──────────────────────────────
def broken_chat(system, user, **kw):
    return None


lore.chat = broken_chat
# 造新的阅读时长 (再加 60 分钟 = 又一天) 让回合有活动
rc = rs.conn()
rc.execute("UPDATE rtime SET minutes=130 WHERE book=? AND day=?", (BOOK, today))
rc.commit()
out2 = ch.run(trigger="manual")
ok(out2["status"] == "ok" and out2["fallback"] is True, "模型不可用时走保底")
ok(out2["growth"]["exp"] == 120, f"保底 = 60 分钟 × 2 got {out2['growth']['exp']}")
ok(out2["adventure"] is None, "写不出故事时宁可不写")

lore.chat = fake_chat


# ── 8. dry: 只看不落地 ───────────────────────────────
rc = rs.conn()
rc.execute("UPDATE rtime SET minutes=190 WHERE book=? AND day=?", (BOOK, today))
rc.commit()
before = cs.load_char()
d = ch.run(trigger="manual", dry=True)
after = cs.load_char()
ok(d["status"] == "dry", f"dry 状态 got {d.get('status')}")
ok(d["judgement"]["growth"]["exp"] == 320, f"dry 也判数 (按故事日上限) got {d['judgement']['growth']}")
ok(int(after["exp"]) == int(before["exp"]) and int(after["level"]) == int(before["level"]),
   "dry 没有改动存档")
ok(ch.load_state()["cycle"] == 2, "dry 不推进回合数")


# ── 9. 强制跑 (没活动也给一回合) ───────────────────────
f = ch.run(trigger="manual", force=True)
ok(f["status"] == "ok", f"force 无视最小分钟 got {f.get('status')}")
ok(f["growth"]["exp"] <= f["minutes"] * 6, f"force 修为仍受分钟约束 got {f['growth']['exp']} / {f['minutes']} 分钟")


# ── 9b. 文本 / 页面 ─────────────────────────────────
ok(jsonout.as_text("一段。\n\n二段。\n\n\n三段。") == "一段。\n\n二段。\n\n三段。",
   "as_text 保留分段 (故事正文不能压成一行)")
ok(jsonout.as_str("a\n\nb") == "a b", "as_str 仍然压缩空白 (判词 / 标题用)")

from recto.app import pet as _petapp  # noqa: E402

html = _petapp.page_html()
ok('id="card-chronicle"' in html, "页面有「游历」卡")
ok('id="btn-chronicle-run"' in html and 'id="adv-history"' in html, "页面有推演按钮与往期")
js = _petapp._UI_DIR.joinpath("pet.js").read_text("utf-8")
ok("loadChronicle" in js and "/api/chronicle/run" in js, "前端接了推演接口")
ok("chattiness" not in js, "书宠不再有「话痨程度」(它不说话)")


# ── 10. guidance ────────────────────────────────────
ok(guidance.version("chronicle") and len(guidance.version()) == 8, "skill 版本 8 位")
specs = guidance.all_specs()
ok([s["name"] for s in specs] == ["lore", "chronicle", "adventure", "legacy"],
   f"四份 skill got {[s['name'] for s in specs]}")
ok("账房 + 教习" in guidance.load("chronicle"), "chronicle skill 可读")
ok("说书人" in guidance.load("adventure"), "adventure skill 可读")
ok("传承" in guidance.load("legacy"), "legacy skill 可读")


# ── 11. HTTP ────────────────────────────────────────
from recto.app import pet as petapp  # noqa: E402


class FakeHandler:
    def __init__(self, path, body=None, method="GET"):
        self.path = path
        self.command = method
        self._body = body or {}

    def _read_json(self):
        return self._body


code, view = petapp._api(FakeHandler("/chronicle"), "/chronicle", "")
ok(code == 200 and "adventures" in view and "config" in view, "GET /chronicle 返现状")
ok(view["guides"] and view["lore"]["books"] == 1, "现状里带 skill 版本与书籍统计")

code, r = petapp._api(FakeHandler("/chronicle/run", {"dry": True}, "POST"), "/chronicle/run", "")
ok(code == 200 and r["status"] in ("dry", "idle"), f"POST /chronicle/run dry got {r.get('status')}")

code, r = petapp._api(FakeHandler("/chronicle/run", {}, "POST"), "/chronicle/run", "")
ok(code == 200 and r["status"] in ("ok", "idle"), f"POST /chronicle/run got {r.get('status')}")

code, r = petapp._api(FakeHandler("/adventures?limit=3"), "/adventures", "")
ok(code == 200 and isinstance(r["adventures"], list), "GET /adventures")

code, r = petapp._api(FakeHandler("/lore"), "/lore", "")
ok(code == 200 and r["books"][0]["title"] == "示例长篇", "GET /lore 列表")

code, r = petapp._api(FakeHandler("/lore/refresh", {"book": BOOK}, "POST"), "/lore/refresh", "")
ok(code == 200 and r["ok"] is True, f"POST /lore/refresh got {code} {r.get('error', '')}")


# ── 12. 无模型时立卡不炸 ──────────────────────────────
lore.chat = broken_chat
ok(lore.build_book("c" * 16, search_mode="off") is None, "没书名/没模型 → 不立卡且不抛")
lore.chat = fake_chat


# ── 13. 设置能存 ────────────────────────────────────
from recto.pet import config as petcfg  # noqa: E402

code, r = petcfg.set_config({"chronicle": {"enabled": False, "interval_min": 30,
                                           "search": False}})
cfg2 = r["config"]["chronicle"]
ok(code == 200 and cfg2["enabled"] is False and cfg2["interval_min"] == 30, f"配置已存 got {cfg2}")
ok(cfg2["search"] is False, "search 关掉")
ok(ch.run(trigger="manual")["status"] == "disabled", "关掉后心跳不跑")
ok(ch.run(trigger="manual", force=True)["status"] == "ok", "force 仍然可以手动跑")
petcfg.set_config({"chronicle": {"enabled": True, "interval_min": 120, "search": True}})


print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
