"""摘录后台：阅读器 / 掌阅 的划线也要出现在面板里 (首页 / 摘录 / 书 / 书票)。

跑法:  cd <仓库根> && PYTHONPATH=src python3 tests/test_admin_reader_notes.py

真踩过的坑 (2026-11-07):
  用户在 `/read/b/<key>` 里划了不少线, 打开 `/admin/?pane=home` 一条也看不到。
  根因 = 后台所有面板只读墨水屏设备的 aggregate (历史快照), 而阅读器的划线
  只落在 reader.sqlite 的 rnote / rbookmark —— 导出那条路上一轮已经接了,
  面板这条路没接。

守这几条:
  1. `_load_view` 同时含设备摘录与阅读器记录, 来源标注正确;
  2. 首页 / 摘录 / 书 / 书详情 都能看到阅读器那批, 且能按「阅读器」筛;
  3. 阅读器的书 `bookId` 是字符串 (`recto:<key>`), 精确筛选不能退化成"全部";
  4. `recto-note-N` / `recto-bm-N` 能删 (只动本地), 设备 int id 照旧删 aggregate;
  5. 视图缓存按文件失效 —— 新划一条线, 下一次读立刻可见 (不需要重启)。
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
os.environ["RECTO_DATA_DIR"] = str(TMP / "data")

from recto.app import excerpts                     # noqa: E402
from recto.services import reader_state            # noqa: E402

DATA = TMP / "pe"
(DATA / "aggregate").mkdir(parents=True)
(DATA / "aggregate" / "books.jsonl").write_text(
    json.dumps({"id": 7, "name": "设备里的书", "author": "某作者"}, ensure_ascii=False) + "\n",
    encoding="utf-8")
(DATA / "aggregate" / "aggregate.jsonl").write_text(
    json.dumps({"id": 1, "bookId": 7, "chapter": "第一章",
                "content": "设备推来的摘录", "idea": "",
                "createdTime": 1793500000000,
                "_firstSeenDeck": "2026-06-09/1200"}, ensure_ascii=False) + "\n",
    encoding="utf-8")


def _fresh_db() -> Path:
    db = Path(reader_state.db_path())
    db.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(db)
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS rbook(key TEXT PRIMARY KEY, path TEXT, kind TEXT,
            title TEXT, author TEXT, bytes INTEGER, chapters INTEGER, size INTEGER,
            mtime REAL, cover TEXT, added_at TEXT, seen_at TEXT);
        CREATE TABLE IF NOT EXISTS rnote(id INTEGER PRIMARY KEY, book TEXT,
            chapter INTEGER, start_off INTEGER, end_off INTEGER, summary TEXT,
            remark TEXT, color TEXT, anchors TEXT, created_at TEXT, updated_at TEXT,
            zy_key TEXT, zy_marktime INTEGER, zy_sig TEXT, src TEXT);
        CREATE TABLE IF NOT EXISTS rbookmark(id INTEGER PRIMARY KEY, book TEXT,
            chapter INTEGER, offset INTEGER, summary TEXT, title TEXT,
            created_at TEXT, zy_key TEXT, zy_marktime INTEGER, zy_sig TEXT, src TEXT);
        """
    )
    c.execute("DELETE FROM rnote")
    c.execute("DELETE FROM rbookmark")
    c.execute("INSERT OR REPLACE INTO rbook(key,path,kind,title,author) VALUES(?,?,?,?,?)",
              ("rk1", str(TMP / "missing.epub"), "epub", "阅读器里的书", "某作者"))
    c.execute("INSERT INTO rnote(book,chapter,start_off,end_off,summary,remark,"
              "created_at,src) VALUES(?,?,?,?,?,?,?,?)",
              ("rk1", 5, 1000, 1015, "阅读器里划的线", "我的批注",
               "2026-11-07T21:00:00", "cloud"))
    c.execute("INSERT INTO rbookmark(book,chapter,offset,summary,title,created_at,src)"
              " VALUES(?,?,?,?,?,?,?)",
              ("rk1", 5, 3000, "", "", "2026-11-07T22:00:00", "local"))
    c.commit()
    c.close()
    return db


def _bind_conn():
    def _conn():
        c = sqlite3.connect(Path(reader_state.db_path()))
        c.row_factory = sqlite3.Row
        return c
    reader_state.conn = _conn            # type: ignore[assignment]


def _clear_cache():
    excerpts._VIEW_CACHE["key"] = None
    excerpts._VIEW_CACHE["value"] = None


_fresh_db()
_bind_conn()
_clear_cache()


def _sources(ex: list[dict]) -> dict:
    out: dict[str, int] = {}
    for e in ex:
        k = excerpts._source_type(e)
        out[k] = out.get(k, 0) + 1
    return out


# ---------------- 断言 ----------------

def test_view_merges_both_sources():
    _fresh_db()
    _clear_cache()
    ex, books = excerpts._load_view(DATA)
    src = _sources(ex)
    assert src.get("M5Paper") == 1, src
    assert src.get("阅读器") == 2, src          # 1 划线 + 1 书签
    assert "recto:rk1" in books, list(books)


def test_home_counts_reader_rows():
    _fresh_db()
    _clear_cache()
    html = excerpts._render_home(None, DATA)
    assert "阅读器 <b>2</b>" in html, html[:400]
    assert 'src-badge rd' in html


def test_excerpts_pane_lists_reader_rows():
    _fresh_db()
    _clear_cache()
    html = excerpts._render_excerpts(None, DATA)
    assert "阅读器里划的线" in html
    assert "设备推来的摘录" in html
    assert 'chip rd' in html                       # 来源筛选里有「阅读器」
    # 书签没正文 → 不留空块
    assert "无正文的定位书签" in html


def test_source_filter_rd_and_m5():
    _fresh_db()
    _clear_cache()
    rd = excerpts._render_excerpts(None, DATA, source="rd")
    assert "阅读器里划的线" in rd and "设备推来的摘录" not in rd
    m5 = excerpts._render_excerpts(None, DATA, source="m5")
    assert "设备推来的摘录" in m5 and "阅读器里划的线" not in m5


def test_string_book_id_filter_is_exact():
    _fresh_db()
    _clear_cache()
    """`?book=recto:rk1` 不能退化成"显示全部" (旧代码 int() 抛错就跳过筛选)."""
    only = excerpts._render_excerpts(None, DATA, book_id="recto:rk1")
    assert "阅读器里划的线" in only
    assert "设备推来的摘录" not in only
    other = excerpts._render_excerpts(None, DATA, book_id="recto:nope")
    assert "阅读器里划的线" not in other
    assert "设备推来的摘录" not in other


def test_books_pane_marks_reader_source():
    _fresh_db()
    _clear_cache()
    html = excerpts._render_books(None, DATA)
    assert "阅读器里的书" in html
    idx = html.index("阅读器里的书")
    row = html[idx:idx + 600]
    assert "阅读器" in row, row


def test_book_detail_lists_reader_rows():
    _fresh_db()
    _clear_cache()
    html = excerpts._render_book_detail(None, DATA, "recto:rk1")
    assert html.count('class="tl-item"') == 2, html.count('class="tl-item"')
    assert 'src-badge rd' in html
    assert "阅读器里的书" in html


def test_delete_reader_row_only_touches_local():
    _fresh_db()
    _clear_cache()
    ex, _ = excerpts._load_view(DATA)
    row = next(e for e in ex if e.get("_kind") == "note")
    assert excerpts._delete_row(DATA, row["id"]) is True
    ex2, _ = excerpts._load_view(DATA)
    assert "阅读器里划的线" not in [e.get("content") for e in ex2]
    # 设备摘录还在
    assert "设备推来的摘录" in [e.get("content") for e in ex2]


def test_delete_device_row_still_works():
    pid = "1"
    assert excerpts._delete_row(DATA, pid) is True
    ex, _ = excerpts._load_view(DATA)
    assert "设备推来的摘录" not in [e.get("content") for e in ex]
    # 复原给后面的用例 (文件已改, 直接重写)
    (DATA / "aggregate" / "aggregate.jsonl").write_text(
        json.dumps({"id": 1, "bookId": 7, "chapter": "第一章",
                    "content": "设备推来的摘录", "idea": "",
                    "createdTime": 1793500000000,
                    "_firstSeenDeck": "2026-06-09/1200"}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    _clear_cache()
    ex2, _ = excerpts._load_view(DATA)
    assert "设备推来的摘录" in [e.get("content") for e in ex2]


def test_delete_rejects_garbage():
    assert excerpts._delete_row(DATA, "") is False
    assert excerpts._delete_row(DATA, "recto-nope-1") is False
    assert excerpts._delete_row(DATA, "abc") is False


def test_view_cache_sees_new_note():
    """缓存按文件失效: 新写一条线, 下一次读立刻可见 (刷页面不用重启)。"""
    _fresh_db()
    _clear_cache()
    before = len(excerpts._load_view(DATA)[0])
    c = sqlite3.connect(Path(reader_state.db_path()))
    c.execute("INSERT INTO rnote(book,chapter,start_off,end_off,summary,remark,"
              "created_at,src) VALUES(?,?,?,?,?,?,?,?)",
              ("rk1", 6, 50, 60, "刚划的一条", "", "2026-11-07T23:59:00", "local"))
    c.commit()
    c.close()
    after = len(excerpts._load_view(DATA)[0])
    assert after == before + 1, (before, after)


def test_export_view_matches_panel_view():
    _fresh_db()
    _clear_cache()
    """导出与面板同一份视图 —— 数字不许各说各话。"""
    ex, books = excerpts._load_view(DATA)
    r = excerpts._build_zhijian_payloads(
        DATA, "192.168.1.20", 8080, "all", "", "", "", "", "", 0, False)
    assert r["ok"], r
    assert r["recto_count"] == sum(1 for e in ex if e.get("_recto")), (
        r["recto_count"], ex)


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
