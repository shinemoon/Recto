"""书宠的节奏 (2026-11-13 调整) —— 走一趟的门槛 / 时钟对不上的闸 / 基线自愈。

跑法: cd <仓库根> && python3 tests/test_pet_cadence.py

为什么单独一支: 这三条都是"看起来在跑, 其实不动"的病根, 而且都不是算错,
是**门槛/状态**错 —— 单测模型写法抓不到, 只能钉死默认值与自愈行为:

  * 门槛: 一回合走一趟 = 10 个故事日 (≈ 6 分钟阅读), 心跳 10 分钟一次。
    时间流速是 0.6 分钟一天 (六分钟 = 十天), 所以"走一趟"的现实代价没变。
  * 时钟闸: 存档被还原 / 时长表被清理后, 故事日会一次跳几百天 →
    一回合最多走 ``max_days_per_cycle`` (300) 天, 并校准到当前值。
  * 基线自愈: ``reading_baseline`` 高过数据源总分钟时, 读多少都不会再长一分。
    这是"复位之后再也不动"的真凶之一, 必须自己校准回来。

全部离线: 模型调用打桩, 数据都在临时目录。
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

TMP = Path(tempfile.mkdtemp(prefix="recto-petcadence-"))
os.environ["RECTO_DATA_DIR"] = str(TMP / "data")
os.environ["RECTO_LIBRARY_DIR"] = str(TMP / "library")
os.environ["RECTO_PET_DIR"] = str(TMP / "data" / "pet")
os.environ["RECTO_AUTH_LOG"] = "off"
os.environ["RECTO_AUTH_PASSWORD"] = "pet-pass"
os.environ.pop("RECTO_AUTH_DISABLE", None)
(TMP / "library").mkdir(parents=True, exist_ok=True)

from recto.core import clock  # noqa: E402
from recto.pet import charsheet as cs  # noqa: E402
from recto.pet import chronicle as ch  # noqa: E402
from recto.pet import companion as comp  # noqa: E402
from recto.pet import growth as g  # noqa: E402
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


def fake_chat(system, user, **kw):
    head = system.split("\n")[0]
    if "读书记录 → 成长判定" in head:
        return json.dumps({
            "assessment": {"depth": "深读", "breadth": "专一", "one_line": "读得沉"},
            "growth": {"exp": 99999, "pot": 9, "fame": 99, "reason": "读了很久"},
            "next_hint": "往北走"}, ensure_ascii=False)
    if "游历演绎" in head:
        return json.dumps({"title": "某一回 · 某地", "place": "某地",
                           "body": "他推门出去, 巷子里有风。" * 10,
                           "terms_used": [], "themes_used": [],
                           "tail": "门还没关。", "verdict": "学会了听风"},
                          ensure_ascii=False)
    return "{}"


lore.chat = fake_chat

# ── 1. 默认节奏 ────────────────────────────────────────
cfg = ch.cfg()
ok(int(cfg["interval_min"]) == 10, f"心跳 10 分钟一次 got {cfg['interval_min']}")
ok(int(cfg["days_per_adventure"]) == 10, f"走一趟的门槛 = 10 个故事日 got {cfg['days_per_adventure']}")
ok(int(cfg["max_days_per_cycle"]) == 300, f"时钟闸 300 天 got {cfg['max_days_per_cycle']}")
ok(int(cfg["interval_min"]) >= 5, "心跳不短于 5 分钟 (别把模型烧穿)")
lc = life.cfg()
ok(abs(float(lc["minutes_per_day"]) - 0.6) < 1e-9, f"0.6 分钟 = 故事里一天 got {lc['minutes_per_day']}")


# ── 2. 基线自愈 (复位之后"再也不动"的真凶) ──────────────
ch_ = cs.new_char("小墨")
ch_["minutes"] = 0
ch_["credited_minutes"] = 0
ch_["reading_baseline"] = 756          # 复位那一刻的旧累计
fix = comp.reconcile_anchor(ch_, 100)  # 而时长表只剩 100 分钟
ok(int(ch_["reading_baseline"]) == 100, f"基线拉回数据源 got {ch_['reading_baseline']}")
ok(fix.get("baseline_fixed") == 656, f"报告改了多少 got {fix.get('baseline_fixed')}")
gain = g.credit_reading_minutes(ch_, 106)   # 之后读 6 分钟
ok(gain["minutes"] == 6, f"基线修好后阅读能折算 got {gain['minutes']}")
ok(int(ch_["minutes"]) == 6, f"累计分钟跟着长 got {ch_['minutes']}")
ok(life.story_day(ch_) == 10, f"6 分钟 = 故事里 10 天 got {life.story_day(ch_)}")

ch2 = cs.new_char("小石")
ch2["reading_baseline"] = 50
fix2 = comp.reconcile_anchor(ch2, 100)
ok(fix2.get("baseline_fixed") == 0 and ch2["reading_baseline"] == 50,
   "基线不高过数据源时不动它")
ch3 = cs.new_char("小水")
ch3["credited_minutes"] = 300
fix3 = comp.reconcile_anchor(ch3, 120)
ok(ch3["credited_minutes"] == 120 and fix3["adjusted"] == 180,
   f"锚点比数据源高 → 校准 got {fix3['adjusted']}")


# ── 3. 时钟对不上时不许一次跳几百天 ────────────────────
BOOK = "a" * 16
today = clock.fmt_sec(time.time(), "%Y-%m-%d")
rc = rs.conn()
rc.execute("INSERT OR REPLACE INTO rtime (book, day, bitmap, minutes, first_at, updated_at,"
           " book_title) VALUES (?,?,?,?,?,?,?)",
           (BOOK, today, "0", 3000, clock.iso_local(), clock.iso_local(), "示例长篇"))
rc.execute("INSERT OR REPLACE INTO rprogress (book, chapter, offset, percent, position,"
           " complete, updated_at, sid) VALUES (?,?,?,?,?,?,?,?)",
           (BOOK, 6, 100, 0.32, "", 0, clock.iso_local(), ""))
rc.commit()

ch3 = cs.new_char("小墨")
ch3["minutes"] = 3000          # 0.6 分钟一天 ⇒ 故事日 5000
ch3["credited_minutes"] = 3000
ch3["reading_baseline"] = 0
cs.save_char(ch3)
st = ch.load_state()
st["story_day"] = 0            # 存档被还原过: 状态里的时钟停在开头
ch.save_state(st)

out = ch.run(trigger="manual")
ok(out["status"] == "ok", f"回合跑成功 got {out.get('status')} {out.get('error', '')}")
ok(out["days"] == 300, f"一次最多走 300 天 got {out.get('days')}")
ok(out.get("cycle") == 1, "回合数 +1")
ok(int(ch.load_state()["story_day"]) == 5000,
   f"时钟校准到当前值 got {ch.load_state().get('story_day')}")
ok(int(out["growth"]["exp"]) <= 300 * 3.2, f"修为按 300 天封顶 got {out['growth']['exp']}")

rep = ch.run(trigger="manual")
ok(rep["status"] == "idle", f"同一段阅读不重复结算 got {rep.get('status')}")

# ── 4. 走一趟真的只要 10 个故事日 ────────────────────────
rc.execute("UPDATE rtime SET minutes=3006 WHERE book=? AND day=?", (BOOK, today))
rc.commit()
out2 = ch.run(trigger="manual")
ok(out2["status"] == "ok", f"再过 6 分钟就能走一趟 got {out2.get('status')} {out2.get('error','')}")
ok(out2["days"] == 10, f"走十天 got {out2.get('days')}")
ok(int(ch.load_state()["adventure_no"]) == 2, f"第二回已落 got {ch.load_state().get('adventure_no')}")


print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
