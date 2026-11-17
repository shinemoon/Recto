"""阅读 → 化身 (spark) —— 故事要从"读到的东西"里长出来。

盯六件事:

  1. 材料摊得对: 书 (定位/设定/概要/种子/读到的章) + 真语料词 (带类别与次数);
  2. 产出**去专名**: 命中禁用词的那一条整条丢弃, 一条不剩就当没有化身;
  3. `keys` 只留普通词 (它们的用途是核对故事正文, 不是拿来做高亮);
  4. 缓存: 同一批材料不重算, 过期重算;
  5. 写故事的 payload 里有 spark, 且 seeds 把 spark 的母题排在前面;
  6. 闸门: 正文一条来处都没落地 → 判没写 (`spark_omitted`)。

跑法: `python3 tests/test_pet_spark.py`
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = Path(tempfile.mkdtemp(prefix="recto-petspark-"))
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

from recto.pet import chronicle as chron  # noqa: E402
from recto.pet import guidance  # noqa: E402
from recto.pet import proper  # noqa: E402
from recto.pet import spark  # noqa: E402
from recto.pet import store  # noqa: E402

OK = 0
BAD = 0


def check(name, cond, extra=""):
    global OK, BAD
    if cond:
        OK += 1
        print("  ok   %s" % name)
    else:
        BAD += 1
        print("  FAIL %s %s" % (name, extra))


def _ev():
    return {
        "window": {"from": "2026-11-17T10:00:00", "to": "2026-11-17T13:00:00", "hours": 3},
        "reading": [
            {"book": "b1", "title": "一本书", "author": "某人", "minutes": 60,
             "chapters": 4, "chapter_to": 12, "percent_from": 20.0, "percent_to": 26.5,
             "notes": 2, "bookmarks": 1, "chapter_label": "",
             "lore": {"identity": "一部以剑道江湖讲少年行走世道的长篇",
                      "themes": ["剑道与心性", "因果与报应"],
                      "setting": "架空古风江湖", "summary": "少年一路行走",
                      "seeds": {"motif": ["迟到的赎罪"], "image": ["熄了又亮的灯"],
                                "weather": "常年风雨", "texture": "粗粝",
                                "conflict": "守住要拿另一样去换"}}},
        ],
        "total_minutes": 60, "days": 10,
        "encounters": {"days": 2, "total": 3,
                       "cats": {"person": 100, "place": 20},
                       "terms": [{"term": "陈平安", "cat": "person", "count": 1663},
                                 {"term": "落魄山", "cat": "place", "count": 257},
                                 {"term": "符箓", "cat": "object", "count": 41}]},
    }


print("[1] 材料")
mat = spark.materials(_ev())
check("书进了材料", len(mat["books"]) == 1)
b0 = mat["books"][0]
check("带书的大致情况", b0.get("identity") and b0.get("setting") and b0.get("summary"))
check("带种子", (b0.get("seeds") or {}).get("motif") == ["迟到的赎罪"])
check("带真语料词 (不是类别计数)",
      [t["term"] for t in mat["terms"]] == ["陈平安", "落魄山", "符箓"])
check("语料词带类别与次数", mat["terms"][0]["cat"] == "person" and mat["terms"][0]["count"] == 1663)

print("[2] 去专名清洗")
# 禁用词表来自"真读过的东西" (书卡 + 语料) —— 临时库里没有, 先造两条。
_c = store.conn()
for _term, _cat in (("陈平安", "person"), ("落魄山", "place"), ("一本书", "work")):
    _c.execute(
        "INSERT OR REPLACE INTO encounter (book, day, term, cat, count, first_at, last_at)"
        " VALUES (?,?,?,?,?,?,?)",
        ("b1", "2026-11-17", _term, _cat, 10, "2026-11-17T10:00:00", "2026-11-17T10:00:00"))
_c.commit()
_c.close()
bad = proper.forbidden(force=True)
check("禁用词表里有那个人名", "陈平安" in bad, str(list(bad)[:3]))
data = {"echo": [
    {"cat": "person", "trace": "一个把旧誓背在身上的少年", "image": "泥水里的鞋",
     "keys": ["旧誓", "长路"]},
    {"cat": "person", "trace": "陈平安走过的那条路", "image": "", "keys": ["走路"]},
    {"cat": "place", "trace": "一处守着的山门", "image": "门槛磨出坑",
     "keys": ["山门", "陈平安"]},
], "twists": ["有人拿着旧剑来找他", "陈平安回来了"],
   "scene": {"weather": "阴天", "place": "靠河的旧街", "texture": "潮"},
   "motif": ["迟到的赎罪", "落魄山"], "image": ["熄了又亮的灯"]}
clean = spark._clean(data)
traces = [e["trace"] for e in clean["echo"]]
check("干净的两条留下", "一个把旧誓背在身上的少年" in traces)
check("带专名的那条整条丢弃", not any("陈平安" in t for t in traces), str(traces))
check("keys 里的专名被摘掉", clean["echo"][-1]["keys"] == ["山门"],
      str(clean["echo"][-1]["keys"]))
check("twists 里的专名被摘掉", clean["twists"] == ["有人拿着旧剑来找他"])
check("motif 里的专名被摘掉", clean["motif"] == ["迟到的赎罪"], str(clean["motif"]))
check("记了丢弃条数", clean["_dropped"] >= 2, str(clean.get("_dropped")))
check("全被丢弃 → 空 (按没有化身走)",
      spark._clean({"echo": [{"trace": "陈平安", "keys": ["陈平安"]}]}) == {})
check("没有 echo → 空", spark._clean({"twists": ["一件小事"]}) == {})

print("[3] 落地核对 (cover)")
sp = {"echo": [{"keys": ["旧誓", "长路"]}, {"keys": ["山门"]}, {"keys": ["木枪"]}]}
got = spark.cover("他背着旧誓走了很远, 山门在那儿", sp)
check("命中两条", got["used"] == 2 and got["enough"], str(got))
check("只命中一条 → 不够", not spark.cover("他背着旧誓", sp)["enough"])
check("一条都没 → 不够", not spark.cover("天色很阴", sp)["enough"])
check("没有来处时直接算过", spark.cover("随便", {})["enough"] is True)

print("[4] 缓存")
sig = spark._sig(mat)
check("同材料同指纹", spark._sig(spark.materials(_ev())) == sig)
spark._cache_put(sig, {"echo": [{"keys": ["甲"]}], "twists": []})
hit = spark._cache_get(sig)
check("命中缓存", bool(hit and hit["echo"][0]["keys"] == ["甲"]))
c = store.conn()
c.execute("UPDATE spark SET at=? WHERE sig=?",
          (time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 13 * 3600)), sig))
c.commit()
c.close()
check("过期不命中", spark._cache_get(sig) is None)

print("[5] 写故事的 payload")
ev = _ev()
ev["_spark"] = {"echo": [{"cat": "person", "trace": "一个守着旧誓的人",
                          "image": "磨破的鞋", "keys": ["旧誓"]}],
                "twists": ["有人来讨那把旧剑"],
                "scene": {"weather": "阴天", "place": "靠河的老街", "texture": "潮"},
                "motif": ["迟到的赎罪"], "image": ["熄了又亮的灯"]}
from recto.pet import charsheet as cs  # noqa: E402

ch = cs.new_char()
payload = chron._adventure_payload(ev, ch, {"exp": 10, "fame": 1, "duel": None,
                                            "checks": [], "gear": {}}, None, {})
check("payload 有 spark", bool(payload.get("spark", {}).get("echo")))
check("payload 有走向", payload["spark"][0] if False else bool(payload["spark"].get("twists")))
check("seeds 把这一段的母题排在最前",
      payload["seeds"] and payload["seeds"][0]["motif"] == ["迟到的赎罪"],
      str(payload["seeds"][:1]))
check("traces 仍是类别计数 (背景用)",
      payload["traces"] and payload["traces"][0]["cat"] == "person")
check("模型看不到书里的名字",
      "陈平安" not in json.dumps(payload, ensure_ascii=False))
check("模型看不到书名", "一本书" not in json.dumps(payload, ensure_ascii=False))

print("[6] 闸门")
sp_req = payload["spark"]
check("正文没落地 → 判没写", chron._spark_in_body("天色很阴, 他走了", sp_req, {}) is False)
check("正文落地 → 放过",
      chron._spark_in_body("他把旧誓又背了一遍", sp_req, {}) is True)
check("历来没有来处 → 放过", chron._spark_in_body("天色很阴", {}, {}) is True)

print("[7] 契约与装载")
check("guidance 认得 spark", "spark" in guidance.NAMES)
check("spark.md 存在且非空", len(guidance.load("spark")) > 800)
adv = guidance.load("adventure")
check("写故事契约里有 spark 一节", "spark" in adv)
check("明写至少两条出场", "至少" in adv and "两条" in adv)
check("明写 traces 只有类别",
      "只有类别" in adv or "类别, 没有词" in adv)

print()
print("%d passed, %d failed" % (OK, BAD))
sys.exit(0 if BAD == 0 else 1)
