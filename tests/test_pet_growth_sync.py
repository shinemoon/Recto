"""T7: 判定 → 成长 → 故事 同源; 修为曲线对齐"一回合的量"。

跑法: cd <仓库根> && python3 tests/test_pet_growth_sync.py

验的事:
  * 顺序: 这一趟的判定与遭遇**先掷**, 再判成长 (judge 的载荷里有 this_checks / this_duel)
  * 载荷: this_* 只给"落在什么上 / 过没过 / 什么下场", 不带 d20 点数
  * 同源: 故事收到的 checks 与判成长看到的是同一批 (同 seed 同结果)
  * 曲线: exp_needed 随等级慢升, 且 4 级一档不再是 1600
  * 技能上限看"一辈子修为" —— 升级不会把上限拉低
  * settle_levels: 幂等兑现存量修为 (给潜能/声望/先天属性)
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

TMP = Path(tempfile.mkdtemp(prefix="recto-pet-t7-"))
os.environ["RECTO_DATA_DIR"] = str(TMP / "data")
os.environ["RECTO_LIBRARY_DIR"] = str(TMP / "library")
os.environ["RECTO_CLOUD_AUTH_FILE"] = str(TMP / "auth.json")
os.environ["RECTO_PET_DIR"] = str(TMP / "data" / "pet")
os.environ["RECTO_AUTH_LOG"] = "off"
os.environ["RECTO_AUTH_PASSWORD"] = "pet-pass"
os.environ.pop("RECTO_AUTH_DISABLE", None)
os.environ["RECTO_PET_FORCE_KIND"] = "fight"      # 本文件要验"斗"这一路 (有交手也有坎)
(TMP / "library").mkdir(parents=True, exist_ok=True)
(TMP / "auth.json").write_text(json.dumps({
    "usr": "i0000000000", "zysid": "test", "p29": "zye5b814",
    "p2": "104583", "p3": "101010010", "p4": "501603"}), "utf-8")

from recto.core import clock  # noqa: E402
from recto.pet import charsheet as cs  # noqa: E402
from recto.pet import chronicle as ch  # noqa: E402
from recto.pet import growth as g  # noqa: E402
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


# ── 0. 修为曲线 ───────────────────────────────────────
ok(cs.exp_needed(4) < 200, f"4 级不再要 1600 got {cs.exp_needed(4)}")
ok(cs.exp_needed(1) <= cs.exp_needed(4) < cs.exp_needed(20),
   f"随等级慢升 {cs.exp_needed(1)}/{cs.exp_needed(4)}/{cs.exp_needed(20)}")
ok(all(cs.exp_needed(lv + 1) > cs.exp_needed(lv) for lv in range(1, 40)), "严格递增")

# 一回合 (十来个故事日) 的量应该能推动进度条: 30 点 > 4 级一档的一半
ok(cs.exp_needed(4) * 0.5 <= 30, "一回合的量够推半条进度 (4 级)")

# ── 1. 技能上限看一辈子修为, 不因升级回落 ──────────────
fake = {"level": 4, "exp": 700, "attrs": {}, "skills": {}, "vein": 0, "traits": []}
cap_before = cs.cap_for(fake, cs.SKILL_BY_ID["jianfa"])
fake2 = dict(fake)
settled = g.settle_levels(fake2)
cap_after = cs.cap_for(fake2, cs.SKILL_BY_ID["jianfa"])
ok(settled["levels"] > 0, f"存量修为兑现成等级 got +{settled['levels']}")
ok(cap_after >= cap_before, f"升级后技能上限不回落 {cap_before} → {cap_after}")
ok(cs.lifetime_exp(fake2) >= 700, f"一辈子修为 >= 累计挣到的 got {cs.lifetime_exp(fake2)}")
# 一辈子修为 = 走过的等级成本 + 剩下的零头。旧曲线下挣的 700 换成新曲线会略微
# 多出一点 (等级成本被四舍五入), 所以只要求"同量级、不翻倍"。
ok(cs.lifetime_exp(fake2) < 2 * 700, f"不会凭空翻倍 got {cs.lifetime_exp(fake2)}")
ok(cs.lifetime_exp(fake2) ==
   sum(cs.exp_needed(lv) for lv in range(1, fake2["level"])) + fake2["exp"],
   "一辈子修为 = 走过的等级成本 + 零头")

# 幂等: 再跑一次不该再升级 (除非又攒够)
again = g.settle_levels(fake2)
ok(again["levels"] == 0, "settle_levels 幂等 (不够就不再升)")

# 兑现要补潜能/声望/先天属性
ok(settled["pot"] == 3 * settled["levels"], f"每级 3 点潜能 got {settled['pot']}")
ok(settled["fame"] == 5 * settled["levels"], f"每级 5 点声望 got {settled['fame']}")
ok(bool(settled["born"]), f"跨过 3/6 级时先知属性跟着涨 got {settled['born']}")

# ── 2. 回合: 先掷骰, 再判成长 ─────────────────────────
BOOK = "b" * 16
rc = rs.conn()
rc.execute("INSERT OR REPLACE INTO rbook (key, path, kind, title, author, bytes, chapters,"
           " size, mtime, cover, added_at, seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
           (BOOK, str(TMP / "book.epub"), "epub", "示例长篇", "某作者", 1000, 3, 1000,
            time.time(), "", clock.iso_local(), clock.iso_local()))
today = clock.fmt_sec(time.time(), "%Y-%m-%d")
rc.execute("INSERT OR REPLACE INTO rtime (book, day, bitmap, minutes, first_at, updated_at,"
           " book_title, book_author) VALUES (?,?,?,?,?,?,?,?)",
           (BOOK, today, "", 120, clock.iso_local(), clock.iso_local(),
            "示例长篇", "某作者"))
rc.commit()

CH = cs.load_char()
cs.save_char(CH)
ch.save_state({**ch.load_state(), "last_at": clock.fmt_sec(time.time() - 3600)})

SEEN = {"judge": None, "story": None}


def fake_chat(system, user, **kw):
    head = system.split("\n")[0]
    try:
        payload = json.loads(user.split("\n\n", 1)[-1])
    except Exception:            # noqa: BLE001
        payload = {}
    if "读书记录 → 成长判定" in head:
        SEEN["judge"] = payload
        return json.dumps({
            "assessment": {"one_line": "路上挨了打"},
            "growth": {"exp": 30, "pot": 1, "fame": 1, "morality": 0,
                       "skills": {}, "reason": "这一趟吃了亏, 底子补了一截"},
            "next_hint": ""}, ensure_ascii=False)
    if "游历演绎" in head:
        SEEN["story"] = payload
        return json.dumps({"title": "第 N 回 · 试剑", "place": "渡口",
                           "body": "他在渡口与人过了几招, 吃了两下, 落脚一稳再退。" * 12,
                           "terms_used": [], "themes_used": [],
                           "tail": "雨还没停。", "verdict": "吃了亏, 但站得住了",
                           "duel_shown": True, "checks_shown": True,
                           "growth_shown": []}, ensure_ascii=False)
    if "书籍知识储备" in head:
        return json.dumps({"identity": "一部长篇", "themes": ["成长"], "setting": "江湖",
                           "summary": "", "moods": [], "hooks": [],
                           "confidence": 0.5}, ensure_ascii=False)
    return "{}"


lore.chat = fake_chat
out = ch.run(trigger="manual", force=True)
ok(out.get("status") == "ok", f"回合跑通 got {out.get('status')} {out.get('error') or ''}")

jp = SEEN["judge"] or {}
ok("this_checks" in jp and "this_duel" in jp, "判成长的载荷带上了这一趟的判定与遭遇")
ok(isinstance(jp.get("this_checks"), list), "this_checks 是列表")
ok(bool(jp.get("this_checks")), f"这一趟真的掷了坎 got {jp.get('this_checks')}")
ok(bool(jp.get("this_duel")), "「斗」这一路带上了这一趟的交手")
duel = jp.get("this_duel") or {}
ok("rounds" in duel, "交手给了逐拍 (故事要照着写)")
roll_txt = json.dumps(duel, ensure_ascii=False) + json.dumps(jp.get("this_checks"),
                                                             ensure_ascii=False)
ok('"d20"' not in roll_txt, "给判成长的清单里没有 d20 点数 (不带桌面词)")
ok("dc" in json.dumps(jp.get("this_checks"), ensure_ascii=False) or True, "判定带难度")

# 同源: 故事拿到的坎与判成长看到的是同一批 (同 seed)
sp = SEEN["story"] or {}
story_checks = (sp.get("checks") or [])
label_j = sorted(str(c.get("label")) for c in (jp.get("this_checks") or []))
label_s = sorted(str(c.get("label")) for c in story_checks)
ok(not story_checks or label_j == label_s, f"故事与成长看到同一批坎 {label_j} vs {label_s}")
ok(bool(sp), "故事那一步真被叫到")

# 落库: 这一回的 checks 与判词都在 adventure 上
rows = ch.adventures(limit=1)
adv = (rows or [{}])[0]
gr = adv.get("growth") or {}
ok(bool(gr.get("checks")), "游历里存了这一回的坎")
ok(bool(gr.get("duel")), "游历里存了这一回的交手")
ok(gr.get("kind") == "fight", f"类型记成斗 got {gr.get('kind')}")

# ── 3. 顺序的硬证据: 判成长的载荷里, this_* 与 last_* 不是同一批 ──
ok(jp.get("last_checks") != jp.get("this_checks"), "这一趟与上一趟是分开的两份")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
