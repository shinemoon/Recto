"""导出到纸间：把「阅读器 / 掌阅 的划线·笔记」也算进去。

跑法:  cd <仓库根> && PYTHONPATH=src python3 tests/test_zhijian_recto_notes.py

真踩过的坑 (2026-11-07):
  用户报「明明我在书里增加了笔记和划线，导出的时候却说没有可导出摘录」。

根因: 导出只读墨水屏设备的 aggregate（历史快照），而掌阅里新加的划线由
`services.notes.pull` 写进 `reader.sqlite` 的 rnote / rbookmark —— 从来不进
aggregate，于是"新加的永远导不出去"。

守这几条：
  1. 两处来源都进导出（aggregate + reader.sqlite）；
  2. 同一书名下同一条划线只发一份（旧的 aggregate 行正文可能少最后一字）；
  3. 设备独有的书（书库里没有）照旧保留；
  4. `source` 能分别只看 阅读器 / 设备；
  5. 阅读器的行不依赖页号，直接带字节比例算出的百分比。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

TMP = Path(tempfile.mkdtemp())
os.environ["RECTO_WEBAUTH_DIR"] = str(TMP)
os.environ["RECTO_AUTH_DISABLE"] = "1"

from recto.app import excerpts  # noqa: E402
from recto.services import reader_state  # noqa: E402

# ---------------- 造数据 ----------------

DATA = TMP / "pe"
(DATA / "aggregate").mkdir(parents=True)
(DATA / "aggregate" / "books.jsonl").write_text(
    json.dumps({"id": 7, "name": "示例长篇（多卷）", "author": "某作者"}, ensure_ascii=False) + "\n"
    + json.dumps({"id": 8, "name": "设备独有的书", "author": ""}, ensure_ascii=False) + "\n",
    encoding="utf-8",
)
AGG_ROWS = [
    # 与阅读器那条同一段划线, 但旧版位置口径是闭区间 → 少最后一个字
    {"id": 1, "bookId": 7, "chapter": "第一章 旧章名",
     "content": "天上月，人间月，负笈求学肩上", "idea": "", "createdTime": 1793500000000,
     "_firstSeenDeck": "2026-06-09/1200"},
    # 设备独有 (书库里没有这本书) → 必须保留
    {"id": 2, "bookId": 8, "chapter": "第二章",
     "content": "碎冰碰壁当啷响。", "idea": "", "createdTime": 1793400000000,
     "_firstSeenDeck": "2026-06-09/1201"},
]
(DATA / "aggregate" / "aggregate.jsonl").write_text(
    "\n".join(json.dumps(r, ensure_ascii=False) for r in AGG_ROWS) + "\n",
    encoding="utf-8",
)

DB = TMP / "reader.sqlite"
_c = sqlite3.connect(DB)
_c.executescript(
    """
    CREATE TABLE rbook(key TEXT PRIMARY KEY, path TEXT, kind TEXT, title TEXT,
                       author TEXT, bytes INTEGER, chapters INTEGER, size INTEGER,
                       mtime REAL, cover TEXT, added_at TEXT, seen_at TEXT);
    CREATE TABLE rnote(id INTEGER PRIMARY KEY, book TEXT, chapter INTEGER,
                       start_off INTEGER, end_off INTEGER, summary TEXT, remark TEXT,
                       color TEXT, anchors TEXT, created_at TEXT, updated_at TEXT,
                       zy_key TEXT, zy_marktime INTEGER, zy_sig TEXT, src TEXT,
                       chapter_label TEXT DEFAULT '');
    CREATE TABLE rbookmark(id INTEGER PRIMARY KEY, book TEXT, chapter INTEGER,
                           offset INTEGER, summary TEXT, title TEXT, created_at TEXT,
                           zy_key TEXT, zy_marktime INTEGER, zy_sig TEXT, src TEXT);
    """
)
_c.execute("INSERT INTO rbook(key,path,kind,title,author) VALUES(?,?,?,?,?)",
           ("k1", str(TMP / "nonexistent.epub"), "epub", "示例长篇（多卷）", "某作者"))
_c.executemany(
    "INSERT INTO rnote(book,chapter,start_off,end_off,summary,remark,created_at,src)"
    " VALUES(?,?,?,?,?,?,?,?)",
    [
        # 与 aggregate id=1 同一段 (差最后一个字)
        ("k1", 5, 1000, 1015, "天上月，人间月，负笈求学肩上月", "记住了这句",
         "2026-11-07T21:00:00", "cloud"),
        # aggregate 里没有的新划线
        ("k1", 5, 2000, 2012, "碎冰碰壁当啷响", "", "2026-11-07T22:00:00", "local"),
    ],
)
_c.execute(
    "INSERT INTO rbookmark(book,chapter,offset,summary,title,created_at,src)"
    " VALUES(?,?,?,?,?,?,?)",
    ("k1", 5, 3000, "第七页", "", "2026-11-07T22:30:00", "local"),
)
_c.commit()
_c.close()


def _fake_conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


reader_state.conn = _fake_conn          # type: ignore[assignment]


def _build(**kw):
    # 视图缓存按文件 mtime 失效; 这里用 monkeypatch 换 conn (文件没变),
    # 所以每个用例前手工清一次, 否则会读到上一个用例的缓存。
    excerpts._VIEW_CACHE["key"] = None
    excerpts._VIEW_CACHE["value"] = None
    args = dict(source="all", book_name="", author="", day_from="", day_to="",
                text_q="", safety_cap=0, include_time=False)
    args.update(kw)
    return excerpts._build_zhijian_payloads(
        DATA, "192.168.1.20", 8080,
        args["source"], args["book_name"], args["author"],
        args["day_from"], args["day_to"], args["text_q"],
        args["safety_cap"], args["include_time"])


def _entries(res):
    out = []
    for p in res.get("payloads") or []:
        for e in p["entries"]:
            out.append((p["title"], e))
    return out


# ---------------- 断言 ----------------

def test_recto_notes_reach_export():
    r = _build(book_name="示例长篇")
    assert r["ok"], r
    texts = [e["text"] for _, e in _entries(r)]
    assert "碎冰碰壁当啷响" in texts, texts          # 新加的划线必须在
    assert "第七页" in texts, texts                  # 书签也在
    assert r["recto_count"] == 3, r["recto_count"]


def test_same_highlight_is_exported_once():
    r = _build(book_name="示例长篇")
    texts = [e["text"] for _, e in _entries(r)]
    same = [t for t in texts if t.startswith("天上月")]
    assert len(same) == 1, same                      # 不许"旧+新"两条
    # 留的是阅读器那份 (完整到最后一个字)
    assert same[0] == "天上月，人间月，负笈求学肩上月", same[0]


def test_device_only_book_kept():
    r = _build()
    titles = {p["title"] for p in r["payloads"]}
    assert "设备独有的书" in titles, titles
    assert "示例长篇（多卷）" in titles, titles


def test_source_filter_rd_only():
    r = _build(source="rd")
    titles = {p["title"] for p in r["payloads"]}
    assert titles == {"示例长篇（多卷）"}, titles
    assert all(e["text"] != "碎冰碰壁当啷响。" for _, e in _entries(r))


def test_source_filter_m5_drops_recto():
    r = _build(source="m5")
    titles = {p["title"] for p in r["payloads"]}
    assert titles == {"设备独有的书"}, titles


def test_empty_filter_says_how_many_recto_notes():
    r = _build(source="m5", book_name="示例长篇")
    assert not r["ok"]
    assert "没有可导出" in r["error"]
    assert "阅读器笔记共 3" in r["error"], r["error"]


def test_text_filter_works_on_recto_rows():
    r = _build(text_q="碎冰")
    texts = [e["text"] for _, e in _entries(r)]
    assert texts and all("碎冰" in t for t in texts), texts


def test_form_offers_reader_source():
    html = excerpts._render_tools(None, DATA)          # type: ignore[arg-type]
    assert '<option value="rd">' in html
    assert "阅读器" in html


def test_page_pct_from_bytes_not_page_number():
    r = _build(book_name="示例长篇")
    for _, e in _entries(r):
        assert isinstance(e["page"], int) and 0 <= e["page"] <= 100, e
    # epub 不存在 → 算不出几何 → 0 (不是崩溃, 也不是假页号)
    assert all(e["page"] == 0 for _, e in _entries(r))


def test_empty_reader_db_is_harmless(monkeypatch=None):
    real = reader_state.conn

    def _boom():
        raise sqlite3.OperationalError("no such table")

    reader_state.conn = _boom              # type: ignore[assignment]
    try:
        r = _build()
        assert r["ok"], r                          # 设备摘录照常导出
        assert {p["title"] for p in r["payloads"]} == {"设备独有的书", "示例长篇（多卷）"}
        assert r["recto_count"] == 0
    finally:
        reader_state.conn = real               # type: ignore[assignment]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as exc:
            bad += 1
            print(f"  FAIL {fn.__name__}: {exc}")
    print(f"\n{len(fns) - bad}/{len(fns)} passed")
    sys.exit(1 if bad else 0)
