"""语料服务反代层 (/corpus/*) 的单元测试 —— 纯函数, 不打网络。

    python3 tests/test_corpus_proxy.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("RECTO_DATA_DIR", tempfile.mkdtemp(prefix="recto-corpus-"))
os.environ.setdefault("RECTO_AUTH_DISABLE", "1")

from recto.app import corpus  # noqa: E402

PASS = FAIL = 0


def ok(cond, name):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS", name)
    else:
        FAIL += 1
        print("FAIL", name)


HTML = """<!doctype html><html><head><title>语料</title>
<link rel="stylesheet" href="/static/style.css"></head>
<body><div class="masthead"><h1>语料服务</h1><a href="/logout">退出</a></div>
<div class="wrap"><button id="x">分析</button></div>
<script src="/static/app.js"></script></body></html>"""


def test_html_rewrite():
    out = corpus._rewrite_html(HTML)
    ok('"/corpus/static/style.css"' in out or "/corpus/static/style.css" in out,
       "静态资源改走 /corpus 前缀")
    ok('src="/corpus/static/app.js"' in out, "脚本路径同样改写")
    ok("/assets/recto.css" in out and "/assets/skin.css" in out,
       "注入统一设计系统")
    ok("rc-top" in out and "rc-nav" in out, "注入统一顶栏与导航")
    ok('href="/logout"' in out, "登出仍交给外层会话")
    ok(out.count("</head>") == 1, "head 结构没被破坏")


def test_html_bridge_vars():
    out = corpus._rewrite_html(HTML)
    for var in ("--bg:var(--rc-bg)", "--panel:var(--rc-surface)",
                "--ink:var(--rc-fg)", "--accent:var(--rc-accent)"):
        ok(var in out, f"上游变量映射 {var}")
    ok(".masthead{display:none}" in out, "上游旧页头被收起来")


def test_html_title():
    """标题统一成其它分区的风格 —— 不用上游自己的品牌名。"""
    out = corpus._rewrite_html(HTML)
    ok("<title>语料 · 云阅 Recto</title>" in out, "标题 = 语料 · 云阅 Recto")
    ok("epub-refiner" not in out and "关键字精炼器" not in out, "上游品牌名不出现")
    ok(out.count("<title>") == 1, "只有一个 <title>")
    out2 = corpus._rewrite_html(HTML, title_hint="")
    ok("<title>云阅 Recto</title>" in out2, "空提示时退化成 云阅 Recto")
    no_title = corpus._rewrite_html("<html><head></head><body>x</body></html>")
    ok("<title>语料 · 云阅 Recto</title>" in no_title, "上游没有 <title> 时补一个")


def test_html_no_head():
    ok(corpus._rewrite_html("<html><body>hi</body></html>"), "缺 head 也不炸")


def test_html_body_class():
    """`body.rc-corpus` 是表单样式的作用域 —— 缺了它, 无边框输入框就不生效。"""
    out = corpus._rewrite_html(HTML)
    ok('<body class="rc-corpus">' in out, "body 打上 rc-corpus 作用域")
    with_cls = HTML.replace("<body>", '<body class="foo">')
    out2 = corpus._rewrite_html(with_cls)
    ok('class="foo rc-corpus"' in out2, "已有 class 时追加而不是覆盖")
    ok(out2.count('class="foo rc-corpus"') == 1, "不重复追加")


def test_form_styles():
    """语料页表单: 无描边填充式输入框 + 对齐 + 卡片单层内边距。"""
    css = corpus._BRIDGE_CSS
    ok("body.rc-corpus :where(input, select, textarea)" in css, "输入框规则按作用域生效")
    field_rule = css.split("body.rc-corpus :where(input, select, textarea){", 1)
    ok(len(field_rule) == 2, "输入框主规则存在")
    body_rule = field_rule[1].split("}", 1)[0]
    ok("border:0" in body_rule.replace(" ", ""), "输入框无描边")
    ok("background:color-mix" in body_rule.replace(" ", ""), "输入框有填充底色 (靠底色而不是线)")
    ok("min-height:40px" in body_rule.replace(" ", ""), "输入框高度对齐按钮 40px")
    ok("inset 0 -2px 0 var(--rc-primary)" in css, "聚焦用底部指示线, 不用整圈描边")
    ok("input[type=\"checkbox\"]" in css and "background:transparent" in css, "勾选框不吃填充底色")
    ok("body.rc-corpus label > :where(input, select, textarea){margin-top:6px}" in css,
       "标签与输入框间距统一 6px")
    ok("body.rc-corpus .grid{align-items:start" in css, "网格按顶对齐")
    ok("grid-column:1 / -1" in css, "勾选项跨整行")
    ok("body.rc-corpus .row{align-items:center" in css, "行内混排按中线对齐")
    ok(".card{padding:0;" in css, "卡片内边距只留一层 (卡头/卡身各自 padding)")
    ok("color-mix(in srgb, var(--rc-warn) 12%" in css, "提示条改 token 混色 (深色皮肤不发光)")


def test_badge_warn_does_not_push_input():
    """推理徽标 (`.badge-warn`) 不许挤动模型库的输入框 (2026-11-04 用户报)。

    上游是 `<input width:100%> <span class=badge-warn>` 内联, 徽标必被挤到下一行:
    实测该行比相邻行高 10.4px(桌面) / 27.2px(手机), 整表行高参差。
    修法 = 该列统一预留右侧空间 + 徽标绝对定位。
    """
    css = corpus._BRIDGE_CSS
    ok("table.models td:nth-child(6){position:relative;padding-right:40px}" in css,
       "模型列统一预留徽标位置 (所有行输入框等宽)")
    ok("td:has(> .badge-warn):not(:nth-child(6))" in css, "列序变了也有 :has() 兜底")
    badge_rule = css.split("body.rc-corpus table.models td > .badge-warn{", 1)
    ok(len(badge_rule) == 2, "徽标表格态规则存在")
    body = badge_rule[1].split("}", 1)[0].replace(" ", "")
    ok("position:absolute" in body, "徽标脱离 inline 流 (不再撑高行)")
    ok("transform:translateY(-50%)" in body, "徽标与输入框中线对齐")
    ok("margin:0" in body, "清掉上游 margin-left 4px")
    ok("white-space:nowrap" in body, "徽标自身不折行")
    ok("table.models{min-width:980px}" in css.replace(" ", ""), "窄屏表格横向滚动, 列不再被压扁")


def test_js_rewrite():
    js = 'fetch("/api/books");fetch(\'/api/task\');fetch(`/api/x`);'
    out = corpus._rewrite_js(js)
    ok(out.count("/corpus/api/") == 3, "三种引号形式的 /api 都改写")
    ok('"/api/' not in out, "没有漏掉的裸 /api")


def test_location_mapping():
    ok(corpus._map_location("/login?next=/") == "/login?next=/",
       "登录跳转留在本服务")
    ok(corpus._map_location("/logout") == "/logout", "登出原地")
    ok(corpus._map_location("/index") == "/corpus/index", "站内跳转加前缀")
    ok(corpus._map_location("https://x/y") == "https://x/y", "外链不动")


def test_prefix_match():
    ok(corpus.PREFIX == "/corpus", "前缀固定")
    ok(corpus.upstream().startswith("http"), "上游地址可取")


def main():
    for fn in (test_html_rewrite, test_html_bridge_vars, test_html_title,
               test_html_no_head,
               test_html_body_class, test_form_styles,
               test_js_rewrite, test_location_mapping, test_prefix_match):
        fn()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
