"""遭遇类型池 (斗/会/观/学/救/行) + 这一趟的来处 (echo)。

盯四件事:

  1. 类型由阅读纹理决定, 且**不再必然一战** —— 没有语料 / 带着伤 / 上一回斗过,
     权重就该压下去;
  2. 挑了哪一类, 就只出那一类该有的判定 (checks 的池子), 非「斗」不结算交手;
  3. `echo` 是**代码从账本取的**来处 (书 / 章 / 分钟 / 语料词), 一个都不让模型编;
  4. 落库后页面能拿到 kind 与 echo; 老记录 (没有这两栏) 不炸。

跑法: `python3 tests/test_pet_kind.py`
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = Path(tempfile.mkdtemp(prefix="recto-petkind-"))
os.environ["RECTO_DATA_DIR"] = str(TMP / "data")
os.environ["RECTO_LIBRARY_DIR"] = str(TMP / "library")
os.environ["RECTO_CLOUD_AUTH_FILE"] = str(TMP / "auth.json")
os.environ["RECTO_PET_DIR"] = str(TMP / "data" / "pet")
os.environ["RECTO_AUTH_LOG"] = "off"
os.environ["RECTO_AUTH_PASSWORD"] = "pet-pass"
os.environ.pop("RECTO_AUTH_DISABLE", None)
os.environ.pop("RECTO_PET_FORCE_KIND", None)     # 本文件要验"自己选"
(TMP / "library").mkdir(parents=True, exist_ok=True)
(TMP / "auth.json").write_text(json.dumps({
    "usr": "i0000000000", "zysid": "test", "p29": "zye5b814",
    "p2": "104583", "p3": "101010010", "p4": "501603"}), "utf-8")

from recto.core import clock  # noqa: E402
from recto.pet import charsheet as cs  # noqa: E402
from recto.pet import checks  # noqa: E402
from recto.pet import chronicle as chron  # noqa: E402
from recto.pet import kind as K  # noqa: E402
from recto.pet import lore, store  # noqa: E402
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


# ── 1. 类型池本身 ──────────────────────────────────────────
ok(set(K.KINDS) == {"fight", "talk", "observe", "learn", "rescue", "travel"},
   f"六类事齐 got {sorted(K.KINDS)}")
ok(all({"cn", "label", "shape", "checks"} <= set(v) for v in K.KINDS.values()),
   "每一类都带 徽标字/说明/写法要求/判定池")
ok(K.KINDS["fight"]["checks"] == "any" and K.KINDS["talk"]["checks"] == "person",
   "「斗」两类坎都出; 「会」只出人的坎")

# ── 2. 权重: 什么时候不该打 ────────────────────────────────
w = K.weights(cats={"person": 100}, level=12, has_terms=True, days=10)
ok(w["talk"] > w["fight"], f"人多 → 会多于斗 got talk={w['talk']:.2f} fight={w['fight']:.2f}")

w2 = K.weights(cats={}, level=12, has_terms=False, days=10)
ok(w2["fight"] == 0, f"没有语料就不打架 got fight={w2['fight']}")
ok(w2["travel"] > w2["observe"] > 0, "没素材时以行路/见闻为主")

base = K.weights(cats={"person": 100}, level=12, has_terms=True, days=10)
hurt = K.weights(cats={"person": 100}, level=12, hp_ratio=0.2, has_terms=True, days=10)
ok(hurt["fight"] < base["fight"] * 0.2, f"带伤不往刀口上撞 got {hurt['fight']:.2f}")
ok(hurt["travel"] > base["travel"], "带伤就多赶路少缠斗")

last = K.weights(cats={"person": 100}, level=12, has_terms=True, days=10, last_kind="fight")
ok(last["fight"] < base["fight"] * 0.2, f"上一回斗过, 这一回先换过法 got {last['fight']:.2f}")

kid = K.weights(cats={"person": 100}, level=2, has_terms=True, days=10)
grown = K.weights(cats={"person": 100}, level=20, has_terms=True, days=10)
ok(kid["learn"] > grown["learn"], "刚出门的多学少斗")

# ── 3. 抽样: 不可复现就是错 ────────────────────────────────
def ev(cats=None, terms=None, days=10):
    return {"days": days,
            "encounters": {"cats": cats or {}, "terms": terms or []}}


CHAR = {"name": "小墨", "level": 12}
a = K.choose(ev({"person": 50, "place": 20}, [{"term": "x"}]), CHAR, {}, seed=7)
b = K.choose(ev({"person": 50, "place": 20}, [{"term": "x"}]), CHAR, {}, seed=7)
ok(a["kind"] == b["kind"], f"同种子同一类 got {a['kind']} / {b['kind']}")
ok(a["kind"] in K.KINDS and a["cn"] and a["why"], "返回带徽标字与理由")
ok("weights" in a and len(a["weights"]) == 6, "带权重 (可查为什么是这一类)")

# 连续 40 次抽样: 不该清一色 (类型池真的在轮换), 也不该「每回都打」
kinds = [K.choose(ev({"person": 60, "place": 20, "object": 10},
                     [{"term": "x"}]), CHAR, {}, seed=s)["kind"] for s in range(40)]
ok(len(set(kinds)) >= 3, f"类型会轮换 got {sorted(set(kinds))}")
ok(kinds.count("fight") < 30, f"不再必然一战 got fight={kinds.count('fight')}/40")

# force / env 能钉死 (测试与排查用)
ok(K.choose(ev({"person": 9}, [{"term": "x"}]), CHAR, {}, seed=1,
            force="learn")["kind"] == "learn", "force 钉死类型")
os.environ["RECTO_PET_FORCE_KIND"] = "rescue"
ok(K.choose(ev({"person": 9}, [{"term": "x"}]), CHAR, {}, seed=1)["kind"] == "rescue",
   "环境变量钉死类型")
os.environ.pop("RECTO_PET_FORCE_KIND", None)

# ── 4. 判定池: 挑了哪一类就只出那一类 ──────────────────────
p_person = checks.plan({"name": "小墨"}, [], seed=3, prefer="person", count=2)
ok(all(p["id"] in {t["key"] for t in checks.PERSON_TEMPLATES} for p in p_person),
   f"「会」只出人的坎 got {[p['label'] for p in p_person]}")
p_road = checks.plan({"name": "小墨"}, [], seed=3, prefer="road", count=2)
ok(all(p["id"] in {t["key"] for t in checks.ROAD_TEMPLATES} for p in p_road),
   f"「学/救/行」只出路的坎 got {[p['label'] for p in p_road]}")
ok(len({p["id"] for p in p_person}) == len(p_person), "两道坎不重复")

# ── 5. echo: 来处是账本给的 ────────────────────────────────
ev_full = {
    "total_minutes": 96, "days": 10,
    "reading": [{
        "book": "b1", "title": "示例长篇", "author": "某作者", "minutes": 96,
        "chapters": 3, "percent_from": 12.5, "percent_to": 18.25,
        "notes": 4, "bookmarks": 1, "path": str(TMP / "nope.epub"),
        "chapter_label": "第七章 某章名",
        "lore": {"seeds": {"motif": ["迟到的赎罪"], "image": ["熄了又亮的灯"]}},
    }],
    "encounters": {"cats": {"person": 60, "place": 20},
                   "terms": [{"term": "某个人名", "cat": "person", "count": 12},
                             {"term": "某处地名", "cat": "place", "count": 5}]},
}
echo = chron._echo(ev_full, {"kind": "talk", "cn": "会", "why": "这一趟要去见一个人"})
ok(echo["kind"] == "talk" and echo["kind_cn"] == "会", "echo 带这一趟是什么事")
ok(echo["books"][0]["title"] == "示例长篇", "echo 记了书名 (页面能认出是哪一本)")
ok(echo["books"][0]["chapter_label"] == "第七章 某章名", "echo 记了读到哪一章")
ok(echo["books"][0]["percent_to"] == 18.25 and echo["books"][0]["minutes"] == 96,
   "echo 记了进度与分钟")
ok(echo["books"][0]["motif"] == ["迟到的赎罪"], "echo 记了母题")
ok([t["term"] for t in echo["terms"]] == ["某个人名", "某处地名"], "echo 记了语料词")
ok(echo["cats"]["person"] == 60 and echo["minutes"] == 96, "echo 记了类别密度与总分钟")

# 期: 没有 lore / 没有 path 也不炸
echo2 = chron._echo({"total_minutes": 0, "days": 1, "reading": [
    {"book": "b2", "title": "另一本", "minutes": 5, "chapter_label": ""}],
    "encounters": {}})
ok(echo2["books"][0]["title"] == "另一本" and echo2["books"][0]["motif"] == [],
   "缺卡/缺路径也能出 echo")
ok(chron._echo({})["books"] == [] and chron._echo({})["terms"] == [],
   "空证据出空 echo (页面据此不渲染)")

# ── 6. 端到端: 非「斗」那一趟不出交手, 但要走得出故事 ──────
cs.init() if hasattr(cs, "init") else None
ch = cs.load_char()
ch["name"] = "小墨"
ch["level"] = 12
cs.save_char(ch)

BOOK = "k" * 16
rc = rs.conn()
rc.execute("INSERT OR REPLACE INTO rbook (key, path, kind, title, author, bytes, chapters,"
           " size, mtime, cover, added_at, seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
           (BOOK, str(TMP / "book.epub"), "epub", "示例长篇", "某作者", 1000, 3, 1000,
            time.time(), "", clock.iso_local(), clock.iso_local()))
today = clock.fmt_sec(time.time(), "%Y-%m-%d")
rc.execute("INSERT OR REPLACE INTO rtime (book, day, bitmap, minutes, first_at, updated_at)"
           " VALUES (?,?,?,?,?,?)", (BOOK, today, "0", 70, clock.iso_local(),
                                     clock.iso_local()))
rc.commit()
cin = store.conn()
cin.execute("INSERT OR REPLACE INTO encounter (book, day, term, cat, count, first_at, last_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (BOOK, today, "某个人名", "person", 40, clock.iso_local(), clock.iso_local()))
cin.commit()
cin.close()

CALLS = {"story": [], "judge": 0}
_real_chat = lore.chat


def fake_chat(system, user, **kw):
    head = system.splitlines()[0] if system else ""
    if "账房" in head:
        CALLS["judge"] += 1
        return json.dumps({"assessment": {"depth": "稳读", "breadth": "专一",
                                          "one_line": "读了 70 分钟"},
                           "growth": {"exp": 40, "pot": 0, "fame": 1, "morality": 0,
                                      "skills": {}, "trait": "",
                                      "reason": "读了 70 分钟"}}, ensure_ascii=False)
    if "游历演绎" in head:
        payload = json.loads(user[user.index("{"):])
        CALLS["story"].append(payload)
        return json.dumps({"title": "第一回 · 石桥", "place": "石桥",
                           "body": "他在石桥上站了一会儿。" * 20,
                           "seeds_used": ["迟到的赎罪"], "traces_used": ["person"],
                           "growth_shown": ["修为 +40"],
                           "tail": "桥下水响。", "verdict": "见了一个人"},
                          ensure_ascii=False)
    return "{}"


lore.chat = fake_chat
os.environ["RECTO_PET_FORCE_KIND"] = "talk"
out = chron.run(trigger="manual", force=True)
ok(out["status"] == "ok", f"回合成功 got {out.get('status')} {out.get('error', '')}")
ok(out["encounter"]["kind"] == "talk", "这一趟按钉死的类型走 (会)")
ok(out.get("duel") is None, f"「会」不结算交手 got {out.get('duel')}")
ok(all(c["id"] in {t["key"] for t in checks.PERSON_TEMPLATES}
       for c in (out.get("checks") or [])), "「会」的坎全落在人上")
payload = CALLS["story"][-1]
ok(payload["encounter"]["kind"] == "talk" and payload["encounter"]["shape"],
   "故事拿到了这一趟的类型与写法要求")
ok(payload.get("duel") in (None, {}), "故事拿不到 duel (就不会自己加打斗)")
ok("示例长篇" not in json.dumps(payload, ensure_ascii=False),
   "提示词里没有书名 (世界自洽)")
ok("某个人名" not in json.dumps(payload, ensure_ascii=False),
   "提示词里没有语料原词 (只给类别密度)")

# ── 7. 落库: 页面能拿到 kind / echo ────────────────────────
row = chron.adventures(limit=3)[0]
g = row["growth"]
ok(g.get("kind") == "talk" and g.get("kind_cn") == "会", "游历记录记了类型")
ok((g.get("echo") or {}).get("books"), "游历记录记了来处")
ok((g.get("echo") or {})["books"][0]["title"] == "示例长篇", "来处里有书名")
ok(chron.load_state().get("last_kind") == "talk", "状态里记了上一回是哪一类")

# 老记录没有这两栏 → 页面不渲染, 也不该炸
VIEW = chron.state_view()
ok(isinstance(VIEW["adventures"], list) and VIEW["adventures"], "state_view 正常")
os.environ.pop("RECTO_PET_FORCE_KIND", None)
lore.chat = _real_chat

print()
print("=" * 46)
print(f"PASS {PASS}  FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
