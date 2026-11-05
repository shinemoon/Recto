"""导出到纸间时带上「精确阅读时长」(preciseReadingDurations)。

跑法:  cd <仓库根> && PYTHONPATH=src python3 tests/test_zhijian_time.py

守的是这几条：
  1. 时长来自两条源：本地 `rsession`（真实区间）+ 云端逐日分钟（只有总数，
     补一段 19:00–23:59）。只发本地会让纸间少掉大部分（人在手机上读的）。
  2. 心跳会把「一次阅读」写成很多行 rsession —— 必须先按间隔合并，否则
     推送出去的是几百个几秒钟的碎片。
  3. 书名匹配是唯一的桥梁：摘录库的 bookId（M5Paper 数字 id / kavita_xxx）
     跟书库 book key 没有任何对应关系。
  4. 时长是附加信息：匹配不到 / 出错都不能挡住摘录本身。
"""
from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

TMP = Path(tempfile.mkdtemp())
os.environ["RECTO_WEBAUTH_DIR"] = str(TMP)
os.environ["RECTO_AUTH_DISABLE"] = "1"

from recto.app import excerpts  # noqa: E402
from recto.services import reader_state  # noqa: E402
from recto.services import sessions  # noqa: E402

# ---------------- 造数据 ----------------

DATA = TMP / "pe"
(DATA / "aggregate").mkdir(parents=True)
(DATA / "aggregate" / "books.jsonl").write_text(
    json.dumps({"id": 7, "name": "示例长篇（多卷）", "author": "某作者"}, ensure_ascii=False) + "\n"
    + json.dumps({"id": 8, "name": "没有进书库的书", "author": ""}, ensure_ascii=False) + "\n",
    encoding="utf-8",
)
(DATA / "aggregate" / "aggregate.jsonl").write_text(
    "\n".join(json.dumps(r, ensure_ascii=False) for r in [
        {"id": 1, "bookId": 7, "chapter": "第一章", "content": "天上月",
         "idea": "", "createdTime": 1793500000000},
        {"id": 2, "bookId": 8, "chapter": "第二章", "content": "碎冰碰壁",
         "idea": "", "createdTime": 1793400000000},
    ]) + "\n",
    encoding="utf-8",
)

DAY1 = "2026-11-04"
DAY2 = "2026-11-05"


def ts(day: str, hh: int, mm: int, ss: int = 0) -> float:
    return datetime.strptime(day, "%Y-%m-%d").replace(hour=hh, minute=mm, second=ss).timestamp()


DB = TMP / "reader.sqlite"


def _seed_db() -> None:
    c = sqlite3.connect(DB)
    c.executescript("""
    CREATE TABLE rsession (id INTEGER PRIMARY KEY AUTOINCREMENT, book TEXT, start_ts REAL,
                           end_ts REAL, minutes INTEGER DEFAULT 0, day TEXT);
    CREATE TABLE rtime (book TEXT, day TEXT, bitmap TEXT DEFAULT '', minutes INTEGER DEFAULT 0,
                        first_at TEXT DEFAULT '', updated_at TEXT DEFAULT '',
                        cloud_minutes INTEGER DEFAULT 0, cloud_at TEXT DEFAULT '',
                        PRIMARY KEY (book, day));
    """)
    rows = [
        # 同一次阅读被心跳切成两行 (间隔 100s) —— 必须并成一段 10 分钟
        ("k1", ts(DAY1, 10, 0), ts(DAY1, 10, 5), DAY1),
        ("k1", ts(DAY1, 10, 6, 40), ts(DAY1, 10, 10), DAY1),
        # 5 秒的点一下就走 —— 必须丢掉
        ("k1", ts(DAY1, 11, 0), ts(DAY1, 11, 0, 5), DAY1),
        # 隔了很久的第二段 8 分钟
        ("k1", ts(DAY1, 15, 0), ts(DAY1, 15, 8), DAY1),
    ]
    c.executemany("INSERT INTO rsession(book,start_ts,end_ts,day) VALUES(?,?,?,?)", rows)
    # 本地那天算 18 分钟 (位图口径, 与区间不完全相等是正常的)
    c.execute("INSERT INTO rtime(book,day,minutes,cloud_minutes) VALUES(?,?,?,?)",
              ("k1", DAY1, 18, 0))
    # 云端第二天 60 分钟、本地没有 —— 要补一段
    c.execute("INSERT INTO rtime(book,day,minutes,cloud_minutes) VALUES(?,?,?,?)",
              ("k1", DAY2, 0, 60))
    c.commit()
    c.close()


_seed_db()

_REAL_CONN = reader_state.conn


def _fake_conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


reader_state.conn = _fake_conn           # type: ignore[assignment]

# 书库 (rbook) 只喂两本: 书名要能对上摘录库里的「示例长篇（多卷）」
BOOKS = [
    {"key": "k1", "title": "示例长篇（多卷）", "path": "/tmp/nonexistent/示例长篇.epub"},
    {"key": "k2", "title": "示例短篇", "path": "/tmp/nonexistent/示例短篇.epub"},
]
_REAL_LIST = reader_state.list_books
reader_state.list_books = lambda q="": [dict(b) for b in BOOKS]   # type: ignore[assignment]


class _Handler:
    def _user(self):
        return "demo"


H = _Handler()


def ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


class FakeHandler:
    def __init__(self, command: str, form: dict | None = None):
        self.command = command
        body = urlencode(form or {}).encode()
        self.headers = {"Content-Length": str(len(body)),
                        "Content-Type": "application/x-www-form-urlencoded"}
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.client_address = ("127.0.0.1", 12345)
        self._admin_ok = True
        self.status = None
        self.resp_headers: list[tuple[str, str]] = []

    def send_response(self, code, message=None):
        self.status = code

    def send_header(self, k, v):
        self.resp_headers.append((k, v))

    def end_headers(self):
        pass

    def json(self):
        return json.loads(self.wfile.getvalue().decode("utf-8"))


def call(path: str, form: dict | None = None):
    h = FakeHandler("POST", form)
    assert excerpts.handle_admin(h, DATA, path, "") is True
    return h


# ---------------- 1. 区间合并 ----------------

def test_merge_spans_joins_and_orders():
    got = sessions._merge_spans([(900.0, 960.0), (0.0, 100.0), (150.0, 200.0)])
    ok(got == [(0.0, 200.0), (900.0, 960.0)], f"间隔 <=300 要并起来, 实际 {got}")


def test_merge_spans_keeps_far_apart():
    got = sessions._merge_spans([(0.0, 60.0), (1000.0, 1060.0)])
    ok(len(got) == 2, f"间隔 >300 不该合并, 实际 {got}")


def test_book_time_drops_tiny_sessions():
    r = sessions.book_time("k1", include_cloud=False)
    ok(len(r["durations"]) == 2, f"5 秒那段要丢掉, 剩 2 段, 实际 {r}")
    ok(r["sessions"] == 2, f"sessions 是合并后的段数, 实际 {r['sessions']}")


def test_book_time_matches_elapsed_minutes():
    r = sessions.book_time("k1", include_cloud=False)
    ok(abs(r["minutes"] - 18.0) < 0.1, f"10 分钟 + 8 分钟 = 18, 实际 {r['minutes']}")
    ok(r["days"] == 1, f"只有一天, 实际 {r['days']}")


def test_book_time_caps_durations():
    r = sessions.book_time("k1", include_cloud=False, limit=1)
    ok(len(r["durations"]) == 1, f"限 1 段, 实际 {len(r['durations'])}")
    ok(r.get("truncated") is True, "截断了要标出来")


def test_book_time_day_window():
    r = sessions.book_time("k1", day_from=DAY2, day_to=DAY2, include_cloud=False)
    ok(r["durations"] == [], f"第二天本地没有会话, 实际 {r}")


# ---------------- 2. 云端分钟补段 ----------------

def test_cloud_minutes_filled_as_span():
    r = sessions.book_time("k1", include_cloud=True)
    ok(r["cloudDays"] == 1, f"只有一天需要补, 实际 {r.get('cloudDays')}")
    ok(abs(r["cloudMinutes"] - 60.0) < 0.5, f"补 60 分钟, 实际 {r.get('cloudMinutes')}")
    ok(len(r["durations"]) == 3, f"两段本地 + 一段补的, 实际 {len(r['durations'])}")
    last = r["durations"][-1]
    day = datetime.fromtimestamp(last["startTime"]).strftime("%Y-%m-%d")
    ok(day == DAY2, f"补的那段要落在云端那天, 实际 {day}")


def test_cloud_filler_is_capped_to_day():
    r = sessions.book_time("k1", include_cloud=True)
    for d in r["durations"]:
        a = datetime.fromtimestamp(d["startTime"])
        b = datetime.fromtimestamp(d["endTime"])
        ok(a.strftime("%Y-%m-%d") == b.strftime("%Y-%m-%d")
           or b.hour == 0, f"补的段不该跨天: {a} → {b}")
        ok(d["endTime"] > d["startTime"], "纸间要求 endTime > startTime (否则整本被拒)")


def test_no_cloud_fill_when_disabled():
    r = sessions.book_time("k1", include_cloud=False)
    ok("cloudMinutes" not in r, "关掉云端补段时不该有 cloudMinutes 字段")
    ok(len(r["durations"]) == 2, f"只剩本地两段, 实际 {len(r['durations'])}")


# ---------------- 3. 书名匹配 ----------------

def test_norm_title_strips_brackets_and_punct():
    a = excerpts._norm_title("示例长篇（多卷）")
    b = excerpts._norm_title("示例长篇")
    ok(a == b, f"括号内容要抹掉: {a!r} != {b!r}")
    ok(excerpts._norm_title("A: B/C") == excerpts._norm_title("A B C"),
       "标点不能影响匹配")


def test_reader_key_for_exact_and_fuzzy():
    tmap = excerpts._reader_key_map()
    ok(tmap, "书库映射不该是空的")
    ok(excerpts._reader_key_for("示例长篇（多卷）", tmap) == "k1", "精确命中")
    ok(excerpts._reader_key_for("示例长篇（多卷）完结精校版", tmap) == "k1",
       "书库名是摘录名的子串时要能命中")
    ok(excerpts._reader_key_for("完全无关的书", tmap) == "", "匹配不到要返空, 不能乱配")


# ---------------- 4. payload ----------------

def test_payload_carries_reading_time():
    h = call("/admin/api/export-zhijian", {"host": "1.2.3.4", "port": "8080"})
    j = h.json()
    by = {p["title"]: p for p in j["payloads"]}
    p = by["示例长篇（多卷）"]
    ok(p.get("timeMinutes", 0) > 0, f"配上书库的书要带时长, 实际 {p.get('timeMinutes')}")
    ok(isinstance(p.get("preciseReadingDurations"), list) and p["preciseReadingDurations"],
       "要带 preciseReadingDurations 明细")
    for d in p["preciseReadingDurations"]:
        ok(set(d) == {"startTime", "endTime"}, f"字段只能是 startTime/endTime, 实际 {d}")
        ok(d["endTime"] > d["startTime"], "endTime 必须大于 startTime")
    ok(by["没有进书库的书"].get("preciseReadingDurations") is None,
       "书库里没有的书不该凭空有时长")
    ok(j["time_minutes"] > 0 and j["time_books"] == 1,
       f"汇总要算出来, 实际 {j.get('time_minutes')} / {j.get('time_books')}")


def test_payload_without_time_when_unchecked():
    h = call("/admin/api/export-zhijian",
             {"host": "1.2.3.4", "port": "8080", "include_time": "0"})
    j = h.json()
    for p in j["payloads"]:
        ok("preciseReadingDurations" not in p, f"没勾选还带了时长: {p['title']}")
        ok("timeMinutes" not in p, "没勾选不该有 timeMinutes")
    ok(j["time_books"] == 0 and j["time_minutes"] == 0, f"汇总归零, 实际 {j}")


def test_payload_time_failure_does_not_block_export():
    """时长出错只记 time_error, 摘录照发。"""
    good = excerpts._sessions.book_time

    def boom(*a, **kw):
        raise RuntimeError("boom")

    excerpts._sessions.book_time = boom            # type: ignore[assignment]
    try:
        h = call("/admin/api/export-zhijian", {"host": "1.2.3.4", "port": "8080"})
        j = h.json()
        ok(j.get("ok") is True, f"导出仍要成功, 实际 {j}")
        ok(j.get("total") == 2, "摘录一条不少")
        ok("time_error" in j, f"出错要留痕, 实际 {j.get('time_error')}")
        ok(j["time_books"] == 0, "出错时不该有声称为有时长的书")
    finally:
        excerpts._sessions.book_time = good        # type: ignore[assignment]


# ---------------- 5. 前端 ----------------

def test_form_has_include_time_checkbox():
    html = excerpts._pane_html("tools", H, DATA, excerpts._pane_params({}))
    ok('name="include_time"' in html, "表单要有「附带阅读时长」勾选")
    m = re.search(r'<input[^>]*name="include_time"[^>]*>', html)
    ok(m and "checked" in m.group(0), "默认要勾上 (用户要的就是带上时长)")


def test_js_sends_durations():
    src = Path(excerpts.__file__).read_text(encoding="utf-8")
    ok('bodyObj.preciseReadingDurations' in src,
       "请求体要带上 preciseReadingDurations (否则设备只收到 entries)")
    ok('fd.set("include_time","0")' in src,
       "未勾选时要显式发 0 —— 勾选框不传字段, 服务端会按默认值带上时长")


# ---------------- runner ----------------

def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = 0
    failed = []
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as exc:  # noqa: BLE001
            failed.append((name, f"{type(exc).__name__}: {exc}"))
    for name, err in failed:
        print(f"FAIL {name} — {err}")
    print(f"\n{passed} PASS, {len(failed)} FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
