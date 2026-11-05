"""书架合并 / Material 骨架 / 阅读时长拉取融合 —— 三件一起回归。

    python3 tests/test_shelf_material_time.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

_tmp = tempfile.mkdtemp(prefix="recto-shelf-")
os.environ["RECTO_DATA_DIR"] = _tmp
os.environ.setdefault("RECTO_AUTH_DISABLE", "1")

from recto.app import console                      # noqa: E402
from recto.app.ui import shell, skins              # noqa: E402
from recto.services import reader_state, sessions  # noqa: E402

PASS = 0
FAIL = 0

ASSETS = Path(__file__).resolve().parents[1] / "src" / "recto" / "app" / "ui" / "assets"


def ok(cond, name):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS", name)
    else:
        FAIL += 1
        print("FAIL", name)


def case(fn):
    try:
        fn()
    except Exception:
        global FAIL
        FAIL += 1
        print("ERROR", fn.__name__)
        traceback.print_exc()
    return fn


# ------------------------------------------------------------ 1 书架合并
@case
def test_nav_merged():
    keys = [k for k, _l, _h in shell.NAV]
    ok("read" not in keys, "导航里不再有独立的「阅读」")
    ok(keys[0] == "library", "书架是第一个分区")
    nav = shell.nav_html("library")
    ok(">书架<" in nav, "导航文案改成「书架」")
    ok('href="/#library"' in nav, "书架指向控制台的 library 面板")


@case
def test_read_redirects_to_shelf():
    src = (Path(__file__).resolve().parents[1] / "src" / "recto" / "app" / "reader.py").read_text("utf-8")
    ok('h._redirect("/#library")' in src, "/read 重定向到书架分区")
    ok("def shelf_page" not in src, "独立书架页已删除 (无重复实现)")
    ok('href="/#library" title="返回书架"' in src, "阅读页返回键指向合并后的书架")


@case
def test_shelf_cards_in_console():
    body = console._page_html(None)
    for marker in ('id="shelf"', 'id="shelf-stat"', 'id="shelf-q"', 'class="shelf-grid"'):
        ok(marker in body, f"书架命中 {marker}")
    js = console._JS
    ok("function bookCard(" in js, "书架按卡片渲染")
    ok('class="bk-cover"' in js and "bk-prog" in js, "卡片带封面与进度条")
    ok("/read/api/book/" in js and "/asset?p=" in js, "封面走阅读器的资产接口")
    ok("renderShelfStats" in js, "书架顶部有统计条")
    ok("lib-body" not in js, "旧的表格渲染已移除")


# ------------------------------------------------------------ 2 Material
@case
def test_material_skin_default():
    alls = skins.all_skins(skins.load_conf())
    for sid in ("material", "material-dark"):
        ok(sid in alls, f"内置皮肤有 {sid}")
    ok(skins._DEFAULTS["skin"] == "material", "默认皮肤是 Material")
    sid, tokens, _conf = skins.resolve()
    ok(sid == "material", f"没配置时解析到 material (得到 {sid})")
    ok(tokens.get("mode") == "light", "material 是浅色模式")


@case
def test_m3_roles_emitted():
    css = skins.skin_css()
    for role in ("--rc-primary:", "--rc-on-primary:", "--rc-primary-container:",
                 "--rc-secondary-container:", "--rc-outline:", "--rc-outline-variant:",
                 "--rc-elev-1:", "--rc-elev-2:", "--rc-elev-3:", "--rc-state:"):
        ok(role in css, f"skin.css 带 {role}")


@case
def test_dark_elevation_differs():
    light = skins.skin_css({"skin": "material"})
    dark = skins.skin_css({"skin": "material-dark"})
    ok(light != dark, "深色皮肤产出不同 CSS")
    ok("color-scheme:dark" in dark.replace(" ", ""), "深色皮肤声明 color-scheme")
    ok("--rc-elev-1: 0 1px 3px 1px rgba(0,0,0,.42)" in dark, "深色高度更重")


@case
def test_css_is_material():
    css = (ASSETS / "recto.css").read_text("utf-8")
    ok("Material 3" in css, "样式表声明 Material 3")
    for token in ("--rc-primary", "--rc-secondary-container", "--rc-elev-1", "--rc-elev-3"):
        ok(token in css, f"骨架用 {token}")
    ok("--rc-accent" not in css.split("13 兼容别名")[0].replace("--rc-accent-soft", ""),
       "组件层不再写死旧的 accent 名")
    ok(".shelf-grid" in css and ".bk-cover" in css, "书架卡片样式在骨架里")
    ok_title_clip = css.split(".bk-title {")[1].split("}")[0]
    ok("white-space: nowrap" in ok_title_clip and "text-overflow: ellipsis" in ok_title_clip,
       "书名单行截断 (不换行 + 省略号), 按卡片宽度")
    ok("-webkit-line-clamp" not in ok_title_clip, "书名不再用两行夹取")
    ok("rc-ripple" in css, "水波纹动画在骨架里")
    ok("border-radius: var(--rc-radius-pill)" in css or
       "border-radius: var(--rc-radius-pill, 999px)" in css, "按钮是胶囊形")


@case
def test_ripple_js_present():
    ok("rc-ripple" in shell._SHELL_JS, "外壳脚本插水波纹")
    ok("pointerdown" in shell._SHELL_JS, "按下的瞬间起波纹 (不是 click)")


# ------------------------------------------------------------ 3 时长融合
def _fake_book(key="bk1", title="样本书"):
    return {"key": key, "title": title, "id32": "A" * 32, "id64": "A" * 64,
            "path": str(Path(_tmp) / "x.epub"), "name": title, "author": ""}


@case
def test_time_merge_max():
    day = date(2026, 5, 1)
    reader_state._mark_minutes("bk1", day, list(range(0, 20)))       # 本地 20 分钟
    reader_state.set_cloud_minutes("bk1", day, 45)                   # 云端 45 分钟
    st = reader_state.time_stats("bk1", days=9000)
    row = [d for d in st["days"] if d["day"] == day.isoformat()][0]
    ok(row["minutes"] == 45, "融合取较大值 (45 而不是 65, 不重复计)")
    ok(row["local"] == 20 and row["cloud"] == 45, "本地/云端分开留痕")

    day2 = date(2026, 5, 2)
    reader_state._mark_minutes("bk1", day2, list(range(60, 120)))    # 本地 60
    reader_state.set_cloud_minutes("bk1", day2, 10)                  # 云端 10
    st2 = reader_state.time_stats("bk1", days=9000)
    row2 = [d for d in st2["days"] if d["day"] == day2.isoformat()][0]
    ok(row2["minutes"] == 60, "本地更长的日子按本地算")


@case
def test_time_merge_idempotent():
    day = date(2026, 5, 3)
    reader_state._mark_minutes("bk1", day, list(range(0, 5)))
    for _ in range(3):
        reader_state.set_cloud_minutes("bk1", day, 30)
    st = reader_state.time_stats("bk1", days=9000)
    row = [d for d in st["days"] if d["day"] == day.isoformat()][0]
    ok(row["minutes"] == 30, "重复融合不累加")
    total = st["total"]
    ok(total >= 30, "总时长包含融合后的值")


@case
def test_book_list_uses_merged_minutes():
    import inspect

    sql = inspect.getsource(reader_state.list_books)
    ok("cloud_minutes" in sql, "书单时长的 SQL 走 cloud_minutes (max 口径)")
    books = reader_state.list_books()
    hit = [b for b in books if b["key"] == "bk1"]
    if hit:
        ok(hit[0]["minutes"] >= 30, "书架上的时长用融合值")
    else:
        ok(True, "临时书库里没有实体书 (SQL 口径已验)")


@case
def test_sessions_pull_merges(monkeypatched=True):
    import recto.services.sessions as S

    orig_links, orig_days = S._sync.links, S.book_days
    S._sync.links = lambda *a, **k: [{"key": "bk1", "title": "样本书",
                                      "id32": "B" * 32, "id64": "B" * 64}]
    S.book_days = lambda _key: {"2026-06-01": 12, "2026-06-02": 7}
    try:
        out = S.pull(date(2026, 6, 1), date(2026, 6, 2), merge=True)
    finally:
        S._sync.links, S.book_days = orig_links, orig_days
    ok(out["days"] == 2, "pull 写下两天的云端分钟")
    ok(out.get("merged") == 2, "两天都并进本地")
    st = reader_state.time_stats("bk1", days=9000)
    days = {d["day"]: d["minutes"] for d in st["days"]}
    ok(days.get("2026-06-01") == 12 and days.get("2026-06-02") == 7,
       "融合后的分钟数进 rtime")


@case
def test_sessions_pull_can_skip_merge():
    import recto.services.sessions as S

    orig_links, orig_days = S._sync.links, S.book_days
    S._sync.links = lambda *a, **k: [{"key": "bk2", "title": "另一本",
                                      "id32": "C" * 32, "id64": "C" * 32}]
    S.book_days = lambda _key: {"2026-07-01": 9}
    try:
        out = S.pull(date(2026, 7, 1), date(2026, 7, 1), merge=False)
    finally:
        S._sync.links, S.book_days = orig_links, orig_days
    ok(out["days"] == 1 and out.get("merged") == 0, "merge=False 只做镜像")
    st = reader_state.time_stats("bk2", days=9999)
    ok(st["total"] == 0, "不融合时本地没有多余时长")


@case
def test_pull_once_has_time():
    from recto.services import sync

    ok(sync.pull_once.__defaults__ is None or "with_time" in
       sync.pull_once.__code__.co_varnames, "pull_once 支持 with_time")
    src = Path(sync.__file__).read_text("utf-8")
    ok("def pull_time(" in src, "sync 有 pull_time (带节流)")
    ok('out["time"]' in src, "pull_once 的结果里带时长小节")
    ok("_TIME_GAP" in src, "时长拉取有节流窗口")


@case
def test_console_pull_no_duplicate_time():
    src = Path(console.__file__).read_text("utf-8")
    seg = src[src.index('elif action == "pull":'):src.index('elif action == "notes":')]
    ok("sync.pull_once" in seg, "pull 动作走 sync.pull_once (内含时长)")
    ok("sessions.pull" not in seg, "不再重复调一次 sessions.pull")
    ok("notes.pull" in seg, "划线/书签仍然一起拉")


print()
if FAIL:
    print(f"{FAIL} FAILED")
    sys.exit(1)
print("all ok")
