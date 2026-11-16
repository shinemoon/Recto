"""钱与药的闸门 —— 后端真扣过, 故事就必须写出来。

跑法: cd <仓库根> && python3 tests/test_pet_gear_narr.py

验的事:
  * 带着伤出门 → 自理真花了药 (存档里少一包, 气血真涨)
  * 第一稿一个字没提药与钱 → 重写一次, 说明里点明"钱与药"
  * 重写稿写了 → gear_shown, 落库的 growth.gear 带得出"用了什么药"
  * 两稿都漏 → gear_omitted 记下来 (不装作没花)
  * 气足的一趟 (没花药没花钱) → 不强制写, 也不重写

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

TMP = Path(tempfile.mkdtemp(prefix="recto-petgearnarr-"))
os.environ["RECTO_DATA_DIR"] = str(TMP / "data")
# 这一套盯的是"钱与药"这条链: 钉死"走一趟远路"(不交手), 免得抽到打斗把水搅浑
os.environ["RECTO_PET_FORCE_KIND"] = "travel"
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
from recto.pet import gear as gr  # noqa: E402
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


BOOK = "d" * 16
rc = rs.conn()
rc.execute("INSERT OR REPLACE INTO rbook (key, path, kind, title, author, bytes, chapters,"
           " size, mtime, cover, added_at, seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
           (BOOK, str(TMP / "book.epub"), "epub", "示例长篇", "某作者", 1000, 3, 1000,
            time.time(), "", clock.iso_local(), clock.iso_local()))
rc.commit()

CALLS = {"modes": [], "note": "", "seen": 0}
MODE = {"omit": 2}          # 前几稿故意不提药与钱


def _payload_of(user):
    _head, _sep, rest = user.partition("\n\n")
    try:
        return json.loads(rest)
    except Exception:  # noqa: BLE001
        return {}


def fake_chat(system, user, **kw):
    head = system.split("\n")[0]
    if "读书记录 → 成长判定" in head:
        return json.dumps({
            "assessment": {"depth": "深读", "breadth": "专一", "one_line": "读得沉"},
            "growth": {"exp": 40, "pot": 0, "fame": 0, "morality": 0, "skills": {},
                       "trait": "", "reason": "读了 60 分钟"},
            "next_hint": "继续往北"}, ensure_ascii=False)
    if "游历演绎" in head:
        p = _payload_of(user)
        note = p.get("contract_note") or ""
        gear = p.get("gear") or {}
        CALLS["gear_payload"] = gear
        if note:
            CALLS["seen"] += 1
            CALLS["note"] = note
        omit = CALLS.pop("omit_left", 0) if "omit_left" in CALLS else 0
        if omit > 0:
            CALLS["omit_left"] = omit - 1
            CALLS["modes"].append("story-omit")
            body = "小墨沿着山道走了很多天, 天很冷, 路很长。" * 8
        else:
            CALLS["modes"].append("story-gear")
            body = ("小墨沿着山道走了很多天。走到第三日, 袖口的伤又裂开, "
                    "他寻了处背风的石缝坐下, 敷了药, 又打坐半晌才动身。"
                    "在渡口替人捎了两程货, 得了几个钱, 钱袋沉了些。" * 4)
        return json.dumps({"title": "第 N 回 · 北行", "place": "雾隐城外的山道",
                           "body": body, "seeds_used": [], "traces_used": [],
                           "growth_shown": [], "duel_shown": False,
                           "checks_shown": True, "days": 1,
                           "tail": "山道还在往北。", "verdict": "走了很多路"},
                          ensure_ascii=False)
    if "书籍知识储备" in head:
        return json.dumps({"identity": "一部长篇", "themes": ["行走"],
                           "setting": "某个年代", "summary": "概要", "moods": ["苍凉"],
                           "hooks": ["一场雨"], "confidence": 0.7}, ensure_ascii=False)
    return "{}"


lore.chat = fake_chat

# 一只带伤出门的书宠 (气血 20%) —— 出门前必用药
ch0 = cs.load_char()
mx = cs.resources(ch0)["hp"]["max"]
ch0["hp"] = int(mx * 0.2)
ch0["bag"] = {"jinchuangyao": 2}
ch0["money"] = 60
cs.save_char(ch0)

# 陪读 60 分钟 → 攒够一站
today = clock.fmt_sec(time.time(), "%Y-%m-%d")
rc.execute("INSERT OR REPLACE INTO rtime (book, day, bitmap, minutes, first_at, updated_at)"
           " VALUES (?,?,?,?,?,?)", (BOOK, today, "0", 60, clock.iso_local(),
                                     clock.iso_local()))
rc.execute("INSERT OR REPLACE INTO rprogress (book, chapter, offset, percent, position,"
           " complete, updated_at, sid) VALUES (?,?,?,?,?,?,?,?)",
           (BOOK, 1, 10, 0.2, "", 0, clock.iso_local(), ""))
rc.commit()

# ── 1. 带伤出门 → 自理真花了药 ────────────────────────
CALLS["omit_left"] = 1          # 第一稿不提药 → 应重写
out = ch.run(trigger="manual")
ok(out.get("status") == "ok", f"回合成功 got {out.get('status')} {out.get('error', '')}")
care = (out.get("growth") or {}).get("gear") or {}
before_notes = ((care.get("before") or {}).get("notes") or [])
ok(any("药" in n for n in before_notes), f"出门前真用了药 got {before_notes}")
ch1 = cs.load_char()
ok(gr.bag(ch1).get("jinchuangyao", 0) < 2, f"行囊里的药少了一包 got {gr.bag(ch1)}")
ok(cs.resources(ch1)["hp"]["cur"] > int(mx * 0.2), "气血真的回来了")

# ── 2. 闸门: 漏了就重写, 重写稿写了就过 ────────────────
ok(CALLS["modes"].count("story-omit") == 1, f"第一稿没提药 got {CALLS['modes']}")
_note = CALLS.get("note") or ""
ok("真花过" in _note and "药" in _note,
   f"说明里点明'它真花过这些' got {_note[:50]}")
adv = out.get("adventure") or {}
ok(bool(adv), "故事写出来了")
ok(adv.get("gear_shown") is True, f"记为写了 got {adv.get('gear_shown')}")
ok(not adv.get("gear_omitted"), "不记漏项")

rows = ch.adventures(limit=3)
g = rows[0].get("growth") or {}
if isinstance(g, str):
    g = json.loads(g)
gp = g.get("gear") or {}
ok(any("药" in n for n in ((gp.get("before") or {}).get("notes") or [])),
   f"落库的 growth.gear 带得出用药 got {gp.get('before')}")
ok(g.get("gear_shown") is True, "落库带 gear_shown")
money_delta = (gp.get("money") or {}).get("delta")
ok(money_delta is not None and money_delta >= 0,
   f"落库带上这一趟的账 got {gp.get('money')}")

# payload 里给模型看的 gear 也有内容 (不是空壳)
ok(bool((CALLS.get("gear_payload") or {}).get("before")), "payload.gear.before 有东西")
ok(bool((CALLS.get("gear_payload") or {}).get("equipped")), "payload.gear.equipped 有东西")

# ── 3. 两稿都漏 → gear_omitted ────────────────────────
CALLS["modes"] = []
CALLS["omit_left"] = 9
ch2 = cs.load_char()
ch2["hp"] = int(cs.resources(ch2)["hp"]["max"] * 0.2)
ch2["bag"] = {"jinchuangyao": 3}
cs.save_char(ch2)
story2 = ch.compose({"reading": [], "encounters": {"terms": [], "cats": {}},
                     "window": {"from": "", "to": "", "minutes": 60},
                     "days": 10, "minutes": 60}, cs.load_char(),
                    {"exp": 1, "gear": {"before": {"notes": ["用了金创药, 气血 +34"],
                                                   "acts": [{"kind": "use", "name": "金创药"}]},
                                        "after": {"notes": [], "acts": []},
                                        "aftermath": {"money": {}, "items": []}}},
                    None, None)
ok(CALLS["modes"].count("story-omit") == 2, f"两稿都没提 got {CALLS['modes']}")
ok(bool(story2) and story2.get("gear_omitted") is True,
   f"记为漏了钱与药 got {story2 and story2.get('gear_omitted')}")
ok(bool(story2) and story2.get("gear_shown") is False, "不装作写了")

# ── 4. 气足的一趟: 不强制, 也不重写 ────────────────────
CALLS["modes"] = []
CALLS["omit_left"] = 0
story3 = ch.compose({"reading": [], "encounters": {"terms": [], "cats": {}},
                     "window": {"from": "", "to": "", "minutes": 60},
                     "days": 10, "minutes": 60}, cs.load_char(),
                    {"exp": 1, "gear": {"before": {"notes": [], "acts": []},
                                        "after": {"notes": [], "acts": []},
                                        "aftermath": {"money": {"delta": 0}, "items": []}}},
                    None, None)
ok(CALLS["modes"] == ["story-gear"], f"只写了一稿 got {CALLS['modes']}")
ok(bool(story3) and story3.get("gear_shown") is True, "没花东西也算满足")

print()
print("=" * 46)
print("PASS", PASS, " FAIL", FAIL)
sys.exit(1 if FAIL else 0)
