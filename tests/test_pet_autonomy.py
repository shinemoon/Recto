"""书宠的"自我架空"与"重开一世" —— 去书化 / 世界 / 专名闸门 / 复位转生。

跑法: cd <仓库根> && python3 tests/test_pet_autonomy.py

验的事:
  * 判成长与写故事的 payload **没有书名 / 作者 / 章名 / 语料原词**
  * 语料只以"类别密度"出现 (traces), 书只以"种子"出现 (seeds)
  * 专名: 跨书出现的词不当专名; 两字词只报告不替换; 三字以上替换成世界里的名字
  * 世界: 六门派必在势力名单里; remember 去重 / 分类 / 封顶
  * 复位: 清故事与回合留痕, 世界与书卡不动, 上一代归档
  * 转生: 继承 N 分钟 → 分钟 / 等级 / 内功 / 声望按同一份阅历折算, 起点岁数随之
  * HTTP: /pet/api/char/reborn 不带 confirm → 400; 带 confirm → 200
  * 专名闸门: 故事里出现书名 → 重写一次; 仍命中 → 兜底替换

全部离线: 模型调用替换成假函数, 数据都在临时目录。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = Path(tempfile.mkdtemp(prefix="recto-petauto-"))
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
from recto.pet import growth  # noqa: E402
from recto.pet import jsonout, lore, proper, reborn, store, world  # noqa: E402

PASS = 0
FAIL = 0


def ok(cond, name):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS", name)
    else:
        FAIL += 1
        print("FAIL", name)


print("=" * 60)
print("A. 世界 (它自己那片地)")
print("=" * 60)

w = world.ensure(use_model=False)
ok(bool(w.get("name")), f"世界有名字 got {w.get('name')}")
ok(len(w.get("regions") or []) >= 3, "有去处")
ok(len(w.get("notables") or []) >= 3, "有人物")
sect_names = [x["name"] for x in world.brief().get("sects") or []]
fac_names = [x["name"] for x in w.get("factions") or []]
ok(all(s in fac_names for s in sect_names),
   f"人物体系的六门派都在势力名单里 got {sect_names[:3]}")
ok(all(s in world.all_names() for s in sect_names), "门派名算世界白名单 (永不禁)")

added = world.remember([
    {"kind": "region", "name": "枯井坳", "note": "井里没水"},
    {"kind": "person", "name": "老秤", "role": "秤铺掌柜", "note": "秤砣换了三回"},
    {"kind": "region", "name": "枯井坳", "note": "重复"},
    {"kind": "bogus", "name": "不该进"},
])
ok(added == ["枯井坳", "老秤"], f"remember 去重 + 只收认得的类 got {added}")
ok("枯井坳" in world.all_names(), "新记的名字进白名单")

print()
print("=" * 60)
print("B. 去书化: 判成长与写故事的输入")
print("=" * 60)

card = {
    "title": "示例长篇", "author": "某作者",
    "themes": ["成长"], "setting": "某个王朝的末年",
    "summary": "少年在某地学剑的故事",
    "seeds": {"motif": ["迟到的赎罪"], "image": ["熄了又亮的灯"],
              "weather": "常年阴雨", "texture": "粗粝",
              "conflict": "守住一样东西要拿另一样去换"},
}
r = {"book": "bk1", "title": "示例长篇", "author": "某作者", "minutes": 96,
     "chapters": 18, "notes": 3, "bookmarks": 1,
     "chapter_from": 171, "chapter_to": 189,
     "percent_from": 27.1, "percent_to": 31.9, "progress_known": True,
     "lore": card}
brief = ch._reading_brief(r)
raw = json.dumps(brief, ensure_ascii=False)
ok("示例长篇" not in raw and "某作者" not in raw, "读书记录里没有书名/作者")
ok("chapter_from" not in brief and "book" not in brief, "没有章号 / 书 key")
ok(brief["seeds"]["motif"] == ["迟到的赎罪"], "带上了种子")
ok(brief["minutes"] == 96 and brief["chapters"] == 18, "读数还在 (分钟/推进)")

ev = {"reading": [r], "encounters": {"cats": {"place": 42, "person": 30}, "days": 2},
      "window": {"from": "a", "to": "b"}, "days": 3}
ch._char_brief.__doc__  # 存在即可
ch_now = cs.new_char("试剑")
payload = ch._adventure_payload(ev, ch_now, {"duel": None, "checks": []}, None, None)
praw = json.dumps(payload, ensure_ascii=False)
ok("books" not in payload and "terms" not in payload, "故事输入里没有 books / terms 字段")
ok("示例长篇" not in praw, "故事输入里没有书名")
ok(payload["traces"] and payload["traces"][0]["cat"] == "place", "语料只给类别密度")
ok("term" not in json.dumps(payload["traces"], ensure_ascii=False), "类别密度里没有词")
ok(payload["world"] and payload["world"]["name"], "带上了世界骨架")

print()
print("=" * 60)
print("C. 专名闸门")
print("=" * 60)

cin = store.conn()
today = clock.fmt_sec(__import__("time").time(), "%Y-%m-%d")
for term, cat, book in (("陈平安", "person", "bk1"), ("泥瓶巷", "place", "bk1"),
                        ("人间", "place", "bk1"), ("剑客", "person", "bk1"),
                        ("渡口", "place", "bk1"), ("渡口", "place", "bk2")):
    cin.execute("INSERT INTO encounter (book, day, term, cat, count, first_at, last_at)"
                " VALUES (?,?,?,?,?,?,?) ON CONFLICT(book,day,term) DO UPDATE SET"
                " count=encounter.count+1",
                (book, today, term, cat, 5, clock.iso_local(), clock.iso_local()))
cin.commit()
cin.close()
words = proper.forbidden(force=True)
ok("陈平安" in words, "人名进禁用表")
ok("泥瓶巷" in words, "地名进禁用表")
ok("渡口" not in words, "跨书出现的词不算专名 (通用词)")
ok("剑客" not in words, "两字身份词不当专名")
ok("人间" not in words, "两字通用词不在此列")

hits = proper.check("陈平安从泥瓶巷出来, 手里攥着《示例长篇》。", words)
hit_words = [h["word"] for h in hits]
ok("陈平安" in hit_words and "泥瓶巷" in hit_words, f"命中书名外的专名 got {hit_words}")
text, replaced = proper.scrub("陈平安从泥瓶巷出来。", hits)
ok("陈平安" not in text and "泥瓶巷" not in text, f"兜底替换生效 got {text}")
ok(all(n in world.all_names() for n in
       (text.replace("从", " ").replace("出来。", " ").split())), "替换成的是世界里的名字")

print()
print("=" * 60)
print("D. 故事里的专名: 命中 → 重写一次")
print("=" * 60)

calls = {"n": 0}


def fake_chat(system, user, **kw):
    calls["n"] += 1
    body = "他走进泥瓶巷, 把伞收了。" * 12
    if "forbidden" in user:
        body = ("他走进" + world.load()["regions"][0]["name"] + ", 把伞收了。") * 12
    return json.dumps({
        "title": "第一回 · 雨里的伞", "place": world.load()["regions"][0]["name"],
        "body": body, "seeds_used": ["迟到的赎罪"], "traces_used": ["place"],
        "world_new": [{"kind": "region", "name": "伞铺巷", "note": "只有一家伞铺"}],
        "tail": "灯还亮着。", "verdict": "记住了那条巷子。",
        "growth_shown": [], "duel_shown": False, "checks_shown": False, "days": 1,
    }, ensure_ascii=False)


_real_chat = lore.chat
lore.chat = fake_chat
try:
    story = ch.compose(ev, ch_now, {"duel": None, "checks": []}, None, None)
finally:
    lore.chat = _real_chat
ok(story is not None, "写出了一回")
ok(calls["n"] == 2, f"命中专名后重写一次 (共 {calls['n']} 次调用)")
ok("泥瓶巷" not in story["body"], "重写后不再出现别人的地名")
ok(story["seeds_used"] == ["迟到的赎罪"], "记下了借到的种子")
ok(story["world_new"] and story["world_new"][0]["name"] == "伞铺巷", "带回新造的世界元素")

# 重写也脏 → 兜底替换 (只对三字以上)
calls["n"] = 0


def fake_dirty(system, user, **kw):
    return json.dumps({"title": "第一回 · 脏", "place": "泥瓶巷",
                       "body": "他走进泥瓶巷, 站了很久。" * 14,
                       "tail": "", "verdict": "", "growth_shown": [],
                       "duel_shown": False, "checks_shown": False, "days": 1},
                      ensure_ascii=False)


lore.chat = fake_dirty
try:
    dirty = ch.compose(ev, ch_now, {"duel": None, "checks": []}, None, None)
finally:
    lore.chat = _real_chat
ok(dirty is not None and "泥瓶巷" not in dirty["body"],
   f"两次都脏 → 兜底替换 got {(dirty or {}).get('body', '')[:20]}")
ok(dirty.get("sanitized"), "替换过的故事带 sanitized 标记")

print()
print("=" * 60)
print("E. 复位 / 转生")
print("=" * 60)

# 造一个"活了一阵"的角色 + 两回故事
ch_a = cs.new_char("旧人")
growth.credit_reading_minutes(ch_a, 900)
cs.save_char(ch_a)
db = store.conn()
db.execute("INSERT INTO cycle (at, trigger, status, minutes) VALUES (?,?,?,?)",
           (clock.iso_local(), "manual", "ok", 90))
db.execute("INSERT INTO adventure (cycle, at, no, title, place, body, created_at)"
           " VALUES (?,?,?,?,?,?,?)",
           (1, clock.iso_local(), 1, "第一回 · 旧事", "旧地", "正文", clock.iso_local()))
db.commit()
db.close()
ch._STORE_STATE_BACKUP = None

st_before = ch.load_state()
st_before["story_day"] = 150
st_before["adventure_no"] = 1
st_before["cycle"] = 1
ch.save_state(st_before)
cards_before = lore.all_cards()

out = reborn.reborn(inherit_minutes=0)
new = out["char"]
ok(new["minutes"] == 0 and new["level"] == 1, "复位: 12 岁白纸, 无修为")
ok(not new["skills"], "复位: 没有功夫")
ok(out["cleared"]["adventures"] == 1, f"复位: 清掉 1 回故事 got {out['cleared']}")
ok(out["cleared"]["cycles"] == 1, "复位: 清掉 1 个回合留痕")
ok(ch.adventures(limit=5) == [], "复位后没有故事")
ok(ch.load_state().get("story_day") == 0, "故事日归零")
ok(len(new.get("lineage") or []) >= 1, "上一代进了 lineage")
ok(bool(out["archive"]), f"上一代归档了 got {out['archive']}")
ok(world.exists(), "世界还在 (复位不动世界)")
ok(lore.all_cards() == cards_before, "书卡还在")

# 转生: 继承一半
ch_b = cs.new_char("旧人二")
growth.credit_reading_minutes(ch_b, 1200)
cs.save_char(ch_b)
out2 = reborn.reborn(inherit_minutes=600)
nb = out2["char"]
ok(nb["minutes"] == 600, f"转生: 带走 600 分钟 got {nb['minutes']}")
ok(nb["credited_minutes"] == 600, "锚点与累计分钟一致 (不会被重算一遍)")
ok(nb["level"] >= 2, f"转生: 按阅历折算等级 got Lv.{nb['level']}")
ok(int(nb["skills"].get("jibenneigong") or 0) >= 2,
   f"转生: 基本内功按阅历折算 got {nb['skills'].get('jibenneigong')}")
ok(nb["fame"] > 0, "转生: 带一点声望")
ok(out2["gained"]["minutes"] == 600, "报告里写明继承了多少")
ok(bool((nb.get("life") or {}).get("backstory")), "写下了这一世的来历")

# 继承不能超过现有
ch_c = cs.new_char("旧人三")
growth.credit_reading_minutes(ch_c, 300)
cs.save_char(ch_c)
out3 = reborn.reborn(inherit_minutes=99999)
ok(out3["inherited_minutes"] == 300, f"继承上限 = 现有阅历 got {out3['inherited_minutes']}")

# 基线: 复位之后, 阅读器那边**已经**累计的历史时长不许再折算回来
# (没有这道闸, 复位第二秒就长回 12 岁第 127 日 —— 真踩过)
ch_x = cs.new_char("基线试")
ch_x["reading_baseline"] = 756
growth.credit_reading_minutes(ch_x, 756)
ok(ch_x["minutes"] == 0, "基线内的历史阅读不再折算 (复位才真复位)")
growth.credit_reading_minutes(ch_x, 812)
ok(ch_x["minutes"] == 56, f"只有基线之后的新阅读才算 got {ch_x['minutes']}")
ok(ch_x["reading_baseline"] == 756, "基线本身不被折算动到")

print()
print("=" * 60)
print("F. HTTP: /pet/api/char/reborn")
print("=" * 60)

import threading  # noqa: E402
import urllib.request  # noqa: E402

from recto.app import server, webauth  # noqa: E402

srv = server.make_server("127.0.0.1", 0)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
raw_cookie = webauth.set_cookie(webauth.login_user())
COOKIE = raw_cookie.split(";")[0].split("=", 1)[1]


def req(path, method="GET", body=None):
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body).encode() if body is not None else None
    rq = urllib.request.Request(url, data=data, method=method,
                                headers={"Cookie": "zy_webauth=" + COOKIE,
                                         "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(rq, timeout=20) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:  # noqa: BLE001
        return e.code, e.read().decode()


st, body = req("/pet/api/char/reborn", "POST", {})
ok(st == 400 and "confirm" in json.loads(body).get("error", ""),
   f"不带 confirm 被挡 got {st}")
st, body = req("/pet/api/char/reborn", "POST", {"confirm": True, "inherit_minutes": 120})
d = json.loads(body)
ok(st == 200 and d["char"]["minutes"] == 120, f"带 confirm 转生成功 got {st}")
st, body = req("/pet/api/world")
ok(st == 200 and json.loads(body)["name"], "GET /pet/api/world 有世界")
st, body = req("/pet/api/proper")
ok(st == 200 and json.loads(body)["words"] >= 0, "GET /pet/api/proper 可用")

srv.shutdown()

print()
print("=" * 60)
print(f"PASS {PASS}  FAIL {FAIL}")
print("=" * 60)
sys.exit(0 if FAIL == 0 else 1)
