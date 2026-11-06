#!/usr/bin/env python3
"""生成语料分区的原生前端资产 (页面片段 / JS; CSS 只做首次 bootstrap)。

语料服务的业务代码已经在 `src/recto/corpus/`, 但页面本体是从上游
(EPUB 关键字精炼器) 平移来的: 250 行 HTML + 1000 行 JS 经过多轮打磨,
重写只会丢功能。所以这里做**可重复的机械平移**:

    web/templates/index.html  →  src/recto/app/ui/assets/corpus.html   (只取 <main> 内的片段)
    web/static/app.js         →  src/recto/app/ui/assets/corpus.js     (接口统一加 /corpus 前缀)
    web/static/style.css      →  src/recto/app/ui/assets/corpus.css    (仅当文件不存在时 bootstrap)

CSS 一旦生成就是 Recto 自己的文件 (token 化 + 填充式字段 + 表格/徽标那套规则都在里面手工维护),
再跑本脚本不会覆盖它 —— 否则每次都会把本地打磨冲掉。

用法:
    python3 tools/build_corpus_assets.py [上游仓库根]
默认上游 = ../epub-refiner (与 Recto 同级)。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DST = ROOT / "src" / "recto" / "app" / "ui" / "assets"
UPSTREAM = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "epub-refiner"

HEADER_HTML = """<!-- 语料分区正文 —— 平移自上游 index.html 的 <main> 内容。
     外壳 (顶栏 / 导航 / 页脚 / 皮肤) 由 recto.app.ui.shell 提供;
     接口前缀 (/corpus) 由 corpus.js 里的 CORPUS_BASE 负责。
     重新生成: python3 tools/build_corpus_assets.py -->
"""

HEADER_JS = """/* 语料分区前端 —— 平移自上游 app.js, 只改了接口前缀与两处下载跳转。
   重新生成: python3 tools/build_corpus_assets.py
   维护约定: 新增接口一律写 api('/api/xxx'), 由 api() 统一加 /corpus 前缀。 */
"""

CSS_HEADER = """/* 语料 (EPUB 关键字精炼) —— 云阅 Recto 原生分区
 *
 * 版式基线来自上游 style.css (卡片 / 网格 / 表格 / 历史产物那几块经过多轮打磨),
 * 此后在本仓手工维护:
 *   · 颜色 / 字体 / 疏密全部走 --rc-* token (换肤跟着变)
 *   · 顶栏与页脚归外壳, 这里只管正文区块
 *   · 字段是无边框填充式 (40px 高, 与按钮同高)
 *
 * 这个文件不会被 tools/build_corpus_assets.py 覆盖 (只有缺失时才 bootstrap),
 * 所以可以放心在这里继续调。
 */
"""

# 上游 :root 的调色板 → Recto 皮肤 token
CSS_ROOT = """:root{
  --bg: var(--rc-bg);
  --panel: var(--rc-surface);
  --ink: var(--rc-fg);
  --ink-dim: var(--rc-mute);
  --line: var(--rc-line);
  --accent: var(--rc-accent);
  --ok: var(--rc-ok);
  --err: var(--rc-err);
  --warn: var(--rc-warn);
  --radius: var(--rc-radius, 0px);
}
"""

# Recto 侧的版式规则 (上游没有的东西: 填充式字段 / 单层卡片 / 徽标定位 / 表格口径)
CSS_RECTO = """
/* ── 外壳与版心 ─────────────────────────────────────────── */
.rc-corpus{max-width:var(--rc-measure)}
@media (max-width:860px){.rc-corpus{max-width:none}}

/* ── 卡片: 上游是「描边 + 卡头浅灰底 + 卡身 16px」三层结构, 跟 Recto 其它分区
   是两套做法。这里合并成一层: 卡片只留高度阴影, 卡头一条极淡分隔线, 内外边距统一。 */
.rc-corpus .card{padding:0;border:0;overflow:hidden}
.rc-corpus .card-head{
  padding:14px var(--rc-pad);
  background:transparent;
  border-bottom:1px solid var(--rc-outline-variant, var(--rc-line));
}
.rc-corpus .card-body{padding:var(--rc-pad)}
.rc-corpus .card-head h2{font-size:var(--rc-fs-sm, 13.5px);letter-spacing:.01em}
.rc-corpus .card-head .hint{font-size:var(--rc-fs-xs, 11.5px);color:var(--rc-mute)}

/* ── 字段: 上游是「白底 + 1px 灰描边」方框, 一页几十个框贴在同样有描边的卡片上,
   线条互相打架。改成填充式: 无描边, 一层极淡底色跟页面区分, 聚焦时底色加深 +
   底部一道主色指示线。 */
.rc-corpus :where(input, select, textarea){
  border:0; box-shadow:none; outline:none;
  background:color-mix(in srgb, var(--rc-fg) 6%, var(--rc-surface));
  border-radius:var(--rc-radius-field, 0);
  min-height:40px; padding:0 12px;
  transition:background .15s ease, box-shadow .15s ease;
}
.rc-corpus :where(input, select, textarea):hover{
  background:color-mix(in srgb, var(--rc-fg) 9%, var(--rc-surface));
}
.rc-corpus :where(input, select, textarea):focus{
  background:color-mix(in srgb, var(--rc-fg) 11%, var(--rc-surface));
  box-shadow:inset 0 -2px 0 var(--rc-primary);
}
.rc-corpus textarea{padding:10px 12px;min-height:92px;line-height:1.6}
/* 复选 / 单选 / 滑块不是文本框, 别让上面的底色糊上去 */
.rc-corpus :where(input[type="checkbox"], input[type="radio"], input[type="range"]){
  background:transparent;box-shadow:none;min-height:0;padding:0;
}
/* 表格里的行内输入: 同一种填充, 只是更紧凑 (上游那几条带 table 前缀的
   padding/font-size 特异度更高, 这里显式压回来) */
.rc-corpus :where(table.models, table.cats) :where(input, select){
  min-height:30px;padding:0 8px;font-size:var(--rc-fs-xs, 11.5px);
}

/* ── 对齐: 标签与输入框之间 6px, 网格行距略大于列距, 勾选项不占字段格 ── */
.rc-corpus label{font-size:var(--rc-fs-xs, 11.5px);color:var(--rc-dim);line-height:1.5}
.rc-corpus label > :where(input, select, textarea){margin-top:6px}
.rc-corpus label.inline > :where(input, select){margin-top:0}
.rc-corpus .grid{align-items:start;column-gap:16px;row-gap:16px}
.rc-corpus .grid > label.inline-check{
  grid-column:1 / -1; align-self:start; min-height:0; padding:0;
}
.rc-corpus .row{align-items:center;gap:10px;margin-top:16px}
.rc-corpus .row > :where(input, select){min-height:40px}
.rc-corpus .row > :where(.status, .path, code){margin:0;line-height:1.5}
.rc-corpus label.inline{font-size:var(--rc-fs-xs, 11.5px);gap:8px}
.rc-corpus label.inline input[type="checkbox"]{margin:0}

/* ── 提示条 / 徽标: 上游写死浅色底 (#f7f9fc / #fff7e6), 深色皮肤下会变成几块
   发光的白板。统一改成 token 混色。 */
.rc-corpus .tip{
  margin:14px 0 0;padding:9px 12px;border-radius:0;
  font-size:var(--rc-fs-xs, 11.5px);line-height:1.65;color:var(--rc-dim);
  background:color-mix(in srgb, var(--rc-primary) 7%, var(--rc-surface));
  border-left:3px solid color-mix(in srgb, var(--rc-primary) 55%, transparent);
}
.rc-corpus .tips{margin:12px 0 0;font-size:var(--rc-fs-xs, 11.5px);line-height:1.75}
.rc-corpus .tips code, .rc-corpus .tip code{background:var(--rc-surface-2);padding:0 4px}
.rc-corpus :where(.quota-bar, .analyze-warn), .rc-corpus .tip.warn{
  background:color-mix(in srgb, var(--rc-warn) 12%, var(--rc-surface));
  border:1px solid color-mix(in srgb, var(--rc-warn) 34%, transparent);
  color:var(--rc-warn);
}
.rc-corpus .tip.warn{border-left:3px solid color-mix(in srgb, var(--rc-warn) 55%, transparent)}
.rc-corpus .badge-warn{
  background:color-mix(in srgb, var(--rc-warn) 16%, var(--rc-surface));
  border:1px solid color-mix(in srgb, var(--rc-warn) 34%, transparent);
  color:var(--rc-warn);
}

/* 推理徽标不挤输入框 (用户 2026-11-04: 「badge-warn 会推挤输入框」)
   上游把 <span class="badge-warn">推理</span> 内联写在 input 后面, 而 input 是
   width:100% → 徽标必被挤到下一行: 实测该行比相邻行高 10.4px (桌面) / 27.2px (手机)。
   修法 = 「该列统一预留右侧空间 + 徽标绝对定位在预留区」: 输入框宽度仍一致,
   行高不再变化。(列序耦合: 模型列 = 上游 renderModels 的第 6 个 td;
   另加一条 :has() 兜底, 列序变了至少不会压住输入框。) */
.rc-corpus table.models td:nth-child(6){position:relative;padding-right:40px}
.rc-corpus table.models td:has(> .badge-warn):not(:nth-child(6)){position:relative;padding-right:40px}
.rc-corpus table.models td > .badge-warn{
  position:absolute;right:6px;top:50%;transform:translateY(-50%);
  margin:0;padding:2px 4px;font-size:10px;line-height:1.25;
  white-space:nowrap;
}
.rc-corpus .tag-latest{
  background:color-mix(in srgb, var(--rc-accent) 12%, var(--rc-surface));
  border:1px solid color-mix(in srgb, var(--rc-accent) 32%, transparent);
  color:var(--rc-accent);
}

/* ── 表格: 与 Recto 其它页面同一口径 —— 无边框线, 只靠隔行底色区分行区间 ── */
.rc-corpus table, .rc-corpus table.models, .rc-corpus table.hist, .rc-corpus table.cats{
  border-collapse:collapse;
}
.rc-corpus table th, .rc-corpus table td{border: 0 !important}
.rc-corpus table.cats thead th,
.rc-corpus table.models thead th,
.rc-corpus table.hist thead th{background:transparent}
.rc-corpus table tbody tr:nth-child(even) > *{
  background:color-mix(in srgb, var(--rc-fg) 5%, transparent);
}
.rc-corpus table tbody tr:hover > *{
  background:color-mix(in srgb, var(--rc-fg) 9%, transparent);
}
.rc-corpus table tbody tr.sel{background:transparent !important}
.rc-corpus table tbody tr.sel > *,
.rc-corpus table tbody tr.sel:hover > *{
  background:color-mix(in srgb, var(--rc-accent) 13%, transparent);
}

/* 窄屏: 模型库 10 列, 上游 width:100% 自适应 → 390px 下输入框只剩十几像素。
   给表格合理最小宽度, 由 .models-wrap 横向滚动承接。 */
@media (max-width:860px){
  .rc-corpus .models-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
  .rc-corpus .models-wrap table.models{min-width:980px}
  .rc-corpus .models-wrap table.models th:nth-child(6),
  .rc-corpus .models-wrap table.models td:nth-child(6){min-width:150px}
}

/* ── 表格行内的小操作群 —— 模型库「使用 / 测 / 删」与历史产物
   「载入 / 导出 / 下载 / 设为最新 / 删除」。一排白底灰字分不出主次,
   按角色分四档 (全部从 token 混色):
     主操作 = 实心主色 · 次要 = 主色软底 · 辅助 = 虚线描边 · 危险 = 淡红底 */
.rc-corpus table.models .mdl-ops button,
.rc-corpus table.hist td.acts button{
  font-size:var(--rc-fs-xs, 12px);
  font-weight:600;
  letter-spacing:.01em;
  border:1px solid transparent;
  transition:background .12s, border-color .12s, color .12s;
}
.rc-corpus table.hist td.acts button.act-load{
  background:var(--rc-accent);border-color:var(--rc-accent);
  color:var(--rc-accent-fg, #fff);
}
.rc-corpus table.hist td.acts button.act-load:hover:not(:disabled){
  background:color-mix(in srgb, var(--rc-accent) 86%, var(--rc-fg));
  border-color:transparent;
}
.rc-corpus table.hist td.acts button.act-export,
.rc-corpus table.models .mdl-ops button[data-act="use"]{
  background:var(--rc-accent-soft, color-mix(in srgb, var(--rc-accent) 14%, var(--rc-surface)));
  border-color:transparent;color:var(--rc-accent);
}
.rc-corpus table.hist td.acts button.act-export:hover:not(:disabled),
.rc-corpus table.models .mdl-ops button[data-act="use"]:hover:not(:disabled){
  background:color-mix(in srgb, var(--rc-accent) 24%, var(--rc-surface));
}
.rc-corpus table.hist td.acts button.act-pin,
.rc-corpus table.models .mdl-ops button[data-act="test"]{
  background:transparent;
  border-color:var(--rc-line-2, var(--rc-line));
  border-style:dashed;color:var(--rc-dim);
}
.rc-corpus table.hist td.acts button.act-pin:hover:not(:disabled),
.rc-corpus table.models .mdl-ops button[data-act="test"]:hover:not(:disabled){
  border-style:solid;
  border-color:color-mix(in srgb, var(--rc-accent) 45%, transparent);
  background:var(--rc-surface-2);color:var(--rc-fg);
}
.rc-corpus table.hist td.acts button.danger,
.rc-corpus table.models .mdl-ops button.danger,
.rc-corpus table.models .mdl-ops button[data-act="del"]{
  background:color-mix(in srgb, var(--rc-err) 7%, var(--rc-surface));
  border-color:color-mix(in srgb, var(--rc-err) 34%, transparent);
  color:var(--rc-err);
}
.rc-corpus table.hist td.acts button.danger:hover:not(:disabled),
.rc-corpus table.models .mdl-ops button.danger:hover:not(:disabled),
.rc-corpus table.models .mdl-ops button[data-act="del"]:hover:not(:disabled){
  background:color-mix(in srgb, var(--rc-err) 12%, var(--rc-surface));
  border-color:color-mix(in srgb, var(--rc-err) 60%, transparent);
}
"""


def build_fragment() -> str:
    html = (UPSTREAM / "web" / "templates" / "index.html").read_text("utf-8")
    m = re.search(r'<main class="wrap">(.*?)</main>', html, re.S)
    if not m:
        raise SystemExit("上游 index.html 结构变了 (找不到 <main class=wrap>)")
    frag = m.group(1).strip("\n")
    # 提示里的 exports/ 前缀在 Recto 里没有这个子目录 (固定导出就在语料根)
    frag = frag.replace("exports/&lt;书名&gt;-&lt;hash&gt;.json", "&lt;书名&gt;-&lt;hash&gt;.json")
    frag = re.sub(r'<code id="exports-dir" class="path">.*?</code>',
                  '<code id="exports-dir" class="path">语料目录</code>', frag)
    return HEADER_HTML + '<div class="rc-corpus">\n' + frag + '\n</div>\n'


def build_js() -> str:
    js = (UPSTREAM / "web" / "static" / "app.js").read_text("utf-8")
    anchor = "async function api(url, opts) {"
    if anchor not in js:
        raise SystemExit("上游 app.js 结构变了 (找不到 api() 定义)")
    js = js.replace(anchor, """// 语料是云阅 Recto 的原生分区: 所有接口都在 /corpus 前缀下
const CORPUS_BASE = window.RC_CORPUS_BASE || '/corpus';

async function api(url, opts) {""", 1)
    js = js.replace("  const res = await fetch(url, Object.assign({",
                    "  const res = await fetch(CORPUS_BASE + url, Object.assign({", 1)
    # 两处直接跳转的下载链接也要带前缀
    js = js.replace("window.location.href = '/api/keywords/' + state.bookId;",
                    "window.location.href = CORPUS_BASE + '/api/keywords/' + state.bookId;")
    js = js.replace("window.location.href = '/api/keywords/' + b.id;",
                    "window.location.href = CORPUS_BASE + '/api/keywords/' + b.id;")
    if js.count("CORPUS_BASE + '/api/keywords/'") != 2:
        raise SystemExit("app.js 里 keywords 下载链接的数量变了, 检查平移规则")
    return HEADER_JS + js


def build_css_bootstrap() -> str:
    css = (UPSTREAM / "web" / "static" / "style.css").read_text("utf-8")
    css = re.sub(r":root\s*\{.*?\n\}", CSS_ROOT, css, count=1, flags=re.S)
    css = re.sub(r"body\s*\{[^}]*\}\n", "", css, count=1)
    css = re.sub(r"\* \{ box-sizing: border-box; \}\n", "", css, count=1)
    css = css.replace(".wrap { max-width: 980px; margin: 0 auto; padding: 0 16px; }",
                      ".rc-corpus { max-width: 1080px; }")
    css = re.sub(r"\.masthead\s*\{.*?\n\}\n", "", css, count=1, flags=re.S)
    css = re.sub(r"\.masthead[^\n]*\{[^}]*\}\n", "", css)
    return CSS_HEADER + CSS_ROOT + css + CSS_RECTO


def main() -> None:
    DST.mkdir(parents=True, exist_ok=True)
    (DST / "corpus.html").write_text(build_fragment(), encoding="utf-8")
    (DST / "corpus.js").write_text(build_js(), encoding="utf-8")
    css_path = DST / "corpus.css"
    if css_path.exists():
        print("keep   ", css_path.name, "(手工维护, 不覆盖)")
    else:
        css_path.write_text(build_css_bootstrap(), encoding="utf-8")
        print("bootstrap", css_path.name)
    print("wrote   ", (DST / "corpus.html").name, (DST / "corpus.js").name, "→", DST)


if __name__ == "__main__":
    main()
