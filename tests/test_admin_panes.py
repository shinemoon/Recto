"""摘录后台 = 一页多面板 (页面内标签) + 表格/图表排版回归。

跑法:  cd <仓库根> && PYTHONPATH=src python3 tests/test_admin_panes.py

守的是三件事:
  * 所有视图都是同一页上的面板 —— 不再有 /admin/books、/admin/excerpts 这种子页面
    (老 URL 只做 302 到 ?pane=...)
  * 面板片段 /admin/pane/<key> 只返回面板内部 HTML, 不带外壳
  * 排版零件都在: 柱状图 / 统计卡 / 筛选芯片 / 时间轴 / 表格滚动容器 / 列宽类
    (它们曾经因为换骨架被整段删掉, 页面就"看着坏了")
"""
from __future__ import annotations

import http.client
import json
import os
import re
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

TMP = Path(tempfile.mkdtemp())
os.environ["RECTO_WEBAUTH_DIR"] = str(TMP)
os.environ["RECTO_AUTH_DISABLE"] = "1"

from recto.app import excerpts  # noqa: E402
from recto.app.ui import shell  # noqa: E402
from recto.app import server  # noqa: E402

# ---------------- 造一份最小数据 ----------------

DATA = TMP / "pe"
(DATA / "aggregate").mkdir(parents=True)

BOOKS = [
    {"id": 1379, "name": "示例长篇（多卷）", "author": "某作者",
     "_firstSeenDeck": "2026-10-20/120000_abcd"},
    {"id": "kavita_a52340bf", "name": "示例短篇", "author": "",
     "_firstSeenDeck": "kavita-merge-2026-10-21/090000_ef"},
]

EXCERPTS = [
    {"id": 1, "bookId": 1379, "chapter": "第一章 惊蛰",
     "content": "天上月，人间月，负笈求学肩上月。", "idea": "记住了这句",
     "createdTime": 1793500000000, "_firstSeenDeck": "kavita-merge-2026-10-21/090000_ef"},
    {"id": 2, "bookId": "kavita_a52340bf", "chapter": "第三章",
     "content": "城市中枢的某个黑暗神秘的地方。", "idea": "",
     "createdTime": 1793400000000, "_firstSeenDeck": "kavita-merge-2026-10-21/090000_ef"},
    {"id": 3, "bookId": 1379, "chapter": "第二章",
     "content": "碎冰碰壁当啷响。", "idea": "",
     "createdTime": 1793300000000, "_firstSeenDeck": "2026-10-20/120000_abcd"},
]

with (DATA / "aggregate" / "books.jsonl").open("w", encoding="utf-8") as f:
    for b in BOOKS:
        f.write(json.dumps(b, ensure_ascii=False) + "\n")
with (DATA / "aggregate" / "aggregate.jsonl").open("w", encoding="utf-8") as f:
    for e in EXCERPTS:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
with (DATA / "events.jsonl").open("w", encoding="utf-8") as f:
    f.write(json.dumps({"ts": "2026-10-21T09:00:00Z", "kind": "oneshot",
                        "bookCount": 2, "excerptCount": 3, "batchCount": 1,
                        "deckDir": "kavita-merge-2026-10-21/090000_ef"}, ensure_ascii=False) + "\n")


class _Handler:
    """只提供 _user() —— 渲染函数用不到别的。"""

    def _user(self):
        return "demo"


H = _Handler()


def ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


# ---------------- 面板模型 ----------------

def test_panes_cover_every_view():
    ok([k for k, _ in excerpts.PANES] == ["home", "excerpts", "books", "events", "tools"],
       "面板清单固定: 首页/摘录/书/事件/工具")
    ok("book" in excerpts.ALL_PANES, "书详情也是面板 (从书架点进去, 不跳页)")


def test_each_pane_renders_fragment_only():
    for key in excerpts.ALL_PANES:
        html = excerpts._pane_html(key, H, DATA, excerpts._pane_params({}))
        ok("<html" not in html.lower(), f"{key} 面板只能是片段, 不能整页")
        ok(not html.lstrip().startswith("<!doctype"), f"{key} 面板不该带 doctype")
        ok(html.strip(), f"{key} 面板不能是空串")


def test_pane_book_placeholder_when_no_id():
    html = excerpts._pane_html("book", H, DATA, excerpts._pane_params({}))
    ok("点一本书" in html, "没给 id 时书面板给引导, 不报错")
    ok('data-pane-link="books"' in html, "引导里可以直接去书架")


def test_pane_book_detail_for_known_id():
    html = excerpts._pane_html("book", H, DATA, excerpts._pane_params({"id": ["1379"]}))
    ok("示例长篇" in html, "书面板渲染书名")
    ok("天上月" in html, "书面板带该书摘录")
    ok("示例短篇" not in html, "别的书的摘录不该混进来")


def test_full_page_contains_all_panes_with_only_one_loaded():
    html = excerpts._render_admin_page(H, DATA, "excerpts", excerpts._pane_params({}))
    for key in excerpts.ALL_PANES:
        ok(f'data-pane="{key}"' in html, f"整页要有 {key} 面板占位")
    ok(html.count('data-pane-loaded="1"') == 1, "只有当前面板是服务端渲染好的")
    ok('data-pane-start="excerpts"' in html, "首屏就落在请求的面板, 不等 JS")
    ok(html.count('data-pane-src="/admin/pane/') == len(excerpts.ALL_PANES),
       "每个面板都带懒加载地址")


def test_subnav_is_pane_links_not_pages():
    nav = shell.subnav_html("excerpts")
    links = re.findall(r'<a href="([^"]+)"([^>]*)>', nav)
    ok(len(links) == len(shell.SUB_NAV), "二级导航条数不变")
    for href, attrs in links:
        ok("data-pane-link=" in attrs, f"{href} 必须是面板链接 (点它不跳页)")
        ok(href.startswith("/admin/?pane="), f"{href} 的无 JS 退路也要落在同一页")


# ---------------- 排版零件 ----------------

def test_home_has_cards_and_working_chart():
    html = excerpts._pane_html("home", H, DATA, excerpts._pane_params({}))
    ok('class="cards"' in html, "统计卡要包在 .cards 网格里")
    ok(html.count('class="bar') >= 7, "7 天 = 7 根柱子")
    ok("today" in html, "今天那根要高亮")
    ok("zero" in html, "0 条的日子也要有地平线, 不能在图上凭空消失")


def test_books_table_is_scroll_wrapped_and_column_hinted():
    html = excerpts._pane_html("books", H, DATA, excerpts._pane_params({}))
    ok('class="table-wrap"' in html, "表格必须进 .table-wrap (窄屏横向滚动)")
    ok("cols-books" in html, "表格带列宽类")
    ok("<colgroup>" in html, "列宽用 colgroup 定下限, 别让列被挤成竖排")
    ok('class="num"' in html, "数字列右对齐 + 不换行")


def test_events_table_columns_are_locked():
    html = excerpts._pane_html("events", H, DATA, excerpts._pane_params({}))
    ok('class="table-wrap"' in html, "事件表也要能横向滚动")
    ok("cols-events" in html, "事件表带列宽类")
    ok("cell-deck" in html, "落盘路径单独一格, 允许折行但不挤别的列")


def test_tools_tables_are_built_wrapped():
    html = excerpts._pane_html("tools", H, DATA, excerpts._pane_params({}))
    ok("zhijian" in html.lower() or "纸间" in html, "导出到纸间的卡片还在")
    ok("tool-card" in html, "工具卡还在")


def test_no_page_header_blocks():
    """每页开头的 kicker / headline / deck 说明块已去掉 (2026-11-04 用户要求)。

    二级导航已经指出"这是哪一页", 再叠一行眉题 + 一句诗 + 一句解释就是噪音。
    小节标签 (.kicker 用在页中间) 与排版预览 (appearance 的 Sample) 不算页头, 保留。
    """
    for key in ("books", "excerpts", "events", "tools", "book"):
        html = excerpts._pane_html(key, H, DATA, excerpts._pane_params({}))
        ok('class="headline"' not in html, f"{key} 面板不该有 .headline 页头")
        ok('class="deck"' not in html, f"{key} 面板不该有 .deck 说明句")
        ok('<h1 class="headline"' not in html, f"{key} 面板不该有 headline 大标题")


def test_excerpt_items_have_time_and_month_header():
    html = excerpts._pane_html("excerpts", H, DATA, excerpts._pane_params({}))
    ok('class="tl-time"' in html and 'class="d"' in html, "时间 + 日期分两行, 不糊成一串")
    ok('class="tl-date"' in html, "月份分隔条要在")
    ok('class="chip' in html, "来源/字数筛选是芯片")


def test_css_defines_every_part_used():
    css = (Path(excerpts.__file__).resolve().parent / "ui" / "assets" / "recto.css").read_text()
    for sel in (".cards", ".bar-chart", ".bar ", ".tl-date", ".tl-time", ".chip",
                ".table-wrap", ".cols-books", ".cols-events", ".col-num", "td.nowrap"):
        ok(sel in css, f"recto.css 缺少 {sel} —— 页面会静默变形")


# ---------------- HTTP: 一页多面板 + 老 URL 兼容 ----------------

SRV = server.make_server("127.0.0.1", 0)
PORT = SRV.server_address[1]
threading.Thread(target=SRV.serve_forever, daemon=True).start()

_orig_data_dir = None


def _req(path):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=40)
    c.request("GET", path)
    r = c.getresponse()
    body = r.read().decode("utf-8", "replace")
    c.close()
    return r.status, dict(r.getheaders()), body


def test_http_page_and_fragments():
    st, _, body = _req("/admin/?pane=excerpts")
    ok(st == 200, f"/admin/ 应 200, 实际 {st}")
    ok('data-pane="excerpts"' in body, "整页里有摘录面板")

    st, _, frag = _req("/admin/pane/books")
    ok(st == 200, f"片段应 200, 实际 {st}")
    ok("<html" not in frag.lower() and "rc-top" not in frag, "片段不只是整页去掉头尾, 也不能带外壳")
    ok("table-wrap" in frag, "片段里是书架表")

    st, _, frag = _req("/admin/pane/nope")
    ok(st == 404, "未知面板 404")


def test_http_legacy_urls_redirect_to_panes():
    for old, pane in (("/admin/books", "books"), ("/admin/excerpts", "excerpts"),
                      ("/admin/events", "events"), ("/admin/tools", "tools")):
        c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=40)
        c.request("GET", old)
        r = c.getresponse()
        r.read()
        loc = r.getheader("Location") or ""
        c.close()
        ok(r.status == 302, f"{old} 应 302, 实际 {r.status}")
        ok(f"pane={pane}" in loc, f"{old} 应跳到 ?pane={pane}, 实际 {loc}")

    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=40)
    c.request("GET", "/admin/books/1379")
    r = c.getresponse()
    r.read()
    loc = r.getheader("Location") or ""
    c.close()
    ok(r.status == 302 and "pane=book" in loc and "1379" in loc,
       f"书详情老链接要落到书面板, 实际 {r.status} {loc}")


def test_http_pane_start_matches_requested():
    st, _, body = _req("/admin/?pane=events")
    ok(st == 200, f"/admin/?pane=events 应 200, 实际 {st}")
    ok('data-pane-start="events"' in body, "刷新时首屏就是请求的面板")
    ok(body.count('data-pane-loaded="1"') == 1, "别把五个面板全渲一遍")


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as exc:
            fails += 1
            print(f"FAIL {name}: {exc}")
    print("ALL PASS" if not fails else f"{fails} FAILED")
    sys.exit(1 if fails else 0)
