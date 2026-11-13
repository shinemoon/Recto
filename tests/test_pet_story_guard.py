"""书宠: 故事没写出来时, 故事日不许推进 (否则读者看不到任何变化)。

跑法: cd <仓库根> && python3 tests/test_pet_story_guard.py

验的事:
  * 判成长成功、写故事失败 → 故事日**不动**, 记下 last_story_error
  * 下一次补写: 用同一回合的证据重写, 且**带上交手与判定** (补出来的不能只是走路)
  * 补写成功 → last_story_error 清掉、故事日对齐
  * state_view 带 llm 信息 (模型名 / 是否推理型) 与 last_story_error —— 页面才说得清
  * 正常回合成功后 last_story_error 为空

全部离线: 模型调用被替换成假函数。
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

TMP = Path(tempfile.mkdtemp(prefix="recto-petguard-"))
os.environ["RECTO_DATA_DIR"] = str(TMP / "data")
# 本文件盯的是"补写也要打一场 + 过坎" —— 钉死这一趟是「斗」,
# 否则类型由阅读纹理抽签 (T3 起不再必然一战)。
os.environ["RECTO_PET_FORCE_KIND"] = "fight"
os.environ["RECTO_LIBRARY_DIR"] = str(TMP / "library")
os.environ["RECTO_CLOUD_AUTH_FILE"] = str(TMP / "auth.json")
os.environ["RECTO_PET_DIR"] = str(TMP / "data" / "pet")
os.environ["RECTO_CORPUS_DIR"] = str(TMP / "data" / "corpus")
os.environ["RECTO_AUTH_LOG"] = "off"
os.environ["RECTO_PET_LOG"] = "off"
(TMP / "library").mkdir(parents=True, exist_ok=True)
(TMP / "auth.json").write_text(json.dumps({
    "usr": "i0000000000", "zysid": "test", "p29": "zye5b814",
    "p2": "104583", "p3": "101010010", "p4": "501603"}), "utf-8")

from recto.core import clock  # noqa: E402
from recto.pet import charsheet as cs  # noqa: E402
from recto.pet import chronicle as ch  # noqa: E402
from recto.pet import life, lore, store  # noqa: E402
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


BOOK = "c" * 16
rc = rs.conn()
rc.execute("INSERT OR REPLACE INTO rbook (key, path, kind, title, author, bytes, chapters,"
           " size, mtime, cover, added_at, seen_at)"
           " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
           (BOOK, str(TMP / "book.epub"), "epub", "示例长篇", "某作者", 1000, 3, 1000,
            time.time(), "", clock.iso_local(), clock.iso_local()))
rc.commit()

JUDGE_JSON = json.dumps({
    "assessment": {"depth": "深读", "breadth": "专一", "one_line": "读得沉"},
    "growth": {"exp": 200, "pot": 0, "fame": 2, "morality": 0, "skills": {},
               "trait": "", "reason": "读了 60 分钟"},
    "next_hint": ""}, ensure_ascii=False)

STORY_JSON = json.dumps({
    "title": "第一回 · 石阶上的风", "place": "石阶",
    "body": "他沿着石阶往上走, 风从袖口灌进来。" * 12,
    "terms_used": ["石阶"], "themes_used": ["成长"],
    "tail": "石阶尽头还有石阶。", "verdict": "稳着往上",
    "growth_shown": ["修为 +200"], "duel_shown": True, "checks_shown": True,
    "days": 1}, ensure_ascii=False)

MODE = {"story": "fail"}


def fake_chat(system, user, **kw):
    head = system.split("\n")[0]
    if "书籍知识储备" in head:
        if "chapter_title" in user:
            return json.dumps({"gist": "他推门进了青石巷。", "motifs": ["青石"],
                               "cast": ["他"]}, ensure_ascii=False)
        return json.dumps({"identity": "一部长篇", "themes": ["成长"],
                           "setting": "某处", "summary": "不剧透的概要",
                           "moods": ["苍凉"], "hooks": [], "confidence": 0.7},
                          ensure_ascii=False)
    if "读书记录 → 成长判定" in head:
        return JUDGE_JSON
    if "游历演绎" in head or "退隐" in head:
        return STORY_JSON if MODE["story"] == "ok" else None
    return "{}"


lore.chat = fake_chat


# ── 1. 造够一站 (60 分钟 = 十天) ──────────────────────
today = clock.fmt_sec(time.time(), "%Y-%m-%d")
rc = rs.conn()
rc.execute("INSERT OR REPLACE INTO rtime (book, day, bitmap, minutes, first_at, updated_at)"
           " VALUES (?,?,?,?,?,?)",
           (BOOK, today, "0", 60, clock.iso_local(), clock.iso_local()))
rc.execute("INSERT OR REPLACE INTO rprogress (book, chapter, offset, percent, position,"
           " complete, updated_at, sid) VALUES (?,?,?,?,?,?,?,?)",
           (BOOK, 6, 100, 0.32, "", 0, clock.iso_local(), ""))
rc.commit()

base_day = int(ch.load_state().get("story_day") or 0)

# ── 2. 判成长成功、写故事失败 ─────────────────────────
MODE["story"] = "fail"
out = ch.run(trigger="manual")
st = ch.load_state()
ok(out["status"] == "ok", f"回合本身不报错 got {out.get('status')} {out.get('error', '')}")
ok(out["adventure"] is None, "模型没写出故事 → 这一回没有游历")
ok(out["fallback"] is False, f"判成长是模型判的 (不是保底) got {out.get('fallback_why')}")
ok(int(st.get("story_day") or 0) == base_day,
   f"故事日不许推进 (还欠这一趟) got {st.get('story_day')} vs {base_day}")
ok(bool(st.get("last_story_error")), f"失败原因要留痕 got {st.get('last_story_error')}")
ok(len(ch.adventures(limit=9)) == 0, "没有落库游历")

view = ch.state_view()
ok(view["state"].get("last_story_error"), "state_view 带上失败原因 (页面才说得清)")
ok(len(ch.cycles(limit=3)) == 1, "cycle 行照写 (失败也是事实)")

# ── 3. 补写: 带交手 + 判定 ───────────────────────────
MODE["story"] = "ok"
story = ch.backfill_adventure()
ok(story and "石阶" in story["title"], f"补写出了故事 got {story and story.get('title')}")
advs = ch.adventures(limit=5)
ok(len(advs) == 1 and advs[0]["no"] == 1, f"落库一回 got {[a['no'] for a in advs]}")
g = advs[0]["growth"]
ok(bool(g.get("backfill")), "标了 backfill")
ok(isinstance(g.get("duel"), dict) and g["duel"].get("rounds"),
   f"补写也要打一场 got {type(g.get('duel'))}")
ok(isinstance(g.get("checks"), list) and len(g["checks"]) >= 1,
   f"补写也要过坎 got {g.get('checks')}")
st2 = ch.load_state()
ok(not st2.get("last_story_error"), "补写成功后清掉失败原因")
ok(int(st2.get("story_day") or 0) == int(life.story_day(cs.load_char())),
   f"补写后故事日对齐 got {st2.get('story_day')}")

# ── 4. 补写重复调用: 没有欠账就不动 ───────────────────
again = ch.backfill_adventure()
ok(again is None, f"没有欠账时补写直接返回 None got {type(again)}")
ok(len(ch.adventures(limit=9)) == 1, "不重复落库")

# ── 5. 正常回合成功后 last_story_error 为空 ───────────
rc = rs.conn()
rc.execute("UPDATE rtime SET minutes=180 WHERE book=? AND day=?", (BOOK, today))
rc.commit()
out2 = ch.run(trigger="manual")
ok(out2["status"] == "ok" and out2["adventure"] is not None,
   f"下一站正常走出故事 got {out2.get('status')} {out2.get('adventure')}")
ok(not ch.load_state().get("last_story_error"), "成功后失败原因清空")
ok(ch.load_state()["adventure_no"] == 2, f"回数 +1 got {ch.load_state()['adventure_no']}")

# ── 6. state_view 的模型信息 ─────────────────────────
v = ch.state_view()
ok("llm" in v and "model" in v["llm"], f"state_view 带模型信息 got {v.get('llm')}")
ok(v["llm"].get("reasoning") in (True, False), "标出是不是推理型")
ok("last_story_error" in v["state"], "state 里预留了失败原因位")

# ── 7. 表结构 ────────────────────────────────────────
c = store.conn()
cols = {r["name"] for r in store.rows(c, "PRAGMA table_info(adventure)")}
c.close()
ok({"growth", "title", "body"} <= cols, f"adventure 表字段齐 got {cols}")

print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)
