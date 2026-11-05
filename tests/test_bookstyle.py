"""页内排版: 书内样式表抽取 + 图文混排 + 中文版式 (clreq)。

跑法: python3 tests/test_bookstyle.py

覆盖三件事:
1. `core/bookstyle.py` 只取「版面」属性, 丢掉颜色/字体族/行高/margin;
2. 图文混排 —— 段内小图 (脚注标记) 按书内 CSS 的 width 缩到 0.85em,
   不被原始像素 (500×500) 撑满整行; 独占一行的插图才走块级;
3. 中文版式 —— 首行缩进只给中文段落、面板可关; 分隔符居中。
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = tempfile.mkdtemp(prefix="recto-bookstyle-test-")
os.environ["RECTO_DATA_DIR"] = TMP

from recto.core import bookfile, bookstyle          # noqa: E402
from recto.app import reader as reader_app          # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


LIB = ROOT / "library"
FOX = LIB / "示例短篇.epub"

# --------------------------------------------------------------------------
# 1. CSS 解析
# --------------------------------------------------------------------------
CSS = """
/* 注释里的大括号 { } 不该被当成规则 */
body { font-family: serif; line-height: 1.2; }
* { margin: 0; padding: 0; }
@font-face { font-family: "tit"; src: url("../Fonts/tit.ttf"); }
.w10 { width: 0.85em; color: #960014; background: #fff; }
.section p { text-indent: 2em; margin: 0.5em 5%; }
body > .section .biaoti { font-size: 1.7em; font-weight: bold; text-align: center; }
.po { padding: 0em 0.1em 0.1em 1em; text-indent: -1em; font-size: 0.9em; color: #960014; }
p.po { letter-spacing: 0.02em; }
a:hover { width: 100%; }
img[width] { width: 33%; }
@media screen and (min-width: 600px) { .wide { width: 70%; } }
.evil { width: url(x.png); height: expression(alert(1)); }
"""
rules = bookstyle.parse_css(CSS)
S = bookstyle.BookStyles(rules)

check("解析出规则 (注释/at-rule 不污染)", len(rules) >= 8, len(rules))
check("@media 里的规则被展开", any("wide" in r[0] for r in S.rules))
check("@font-face 被跳过", not any("font-face" in r[0] for r in S.rules))
check("注释里的花括号不产生规则 (无 '*/' 选择器)",
      not any("*/" in r[0] or "{" in r[0] for r in S.rules))

img = S.img_style({"class": "w10", "src": "a.png"})
check("图片 class 尺寸被取到", "width:0.85em" in img, img)
check("图片不取颜色", "color" not in img and "background" not in img, img)

img2 = S.img_style({"style": "width: 120px; color: red", "class": "w10"})
check("行内 style 覆盖 class 尺寸", "width:120px" in img2 and "0.85em" not in img2, img2)
check("行内 style 里的颜色仍被过滤", "color" not in img2, img2)

img3 = S.img_style({"width": "300", "height": "0.8em"})
check("width 属性 (无单位) → px", "width:300px" in img3, img3)
check("height 属性带单位保留", "height:0.8em" in img3, img3)

img4 = S.img_style({"class": "evil"})
check("url() / expression() 一律拒绝", img4 == "", img4)

img5 = S.img_style({"class": "wide"})
check("@media 里的 class 也生效", "width:70%" in img5, img5)

b = S.block_style("p", {"class": "po"})
check("块级取到 class 的 font-size", "font-size:0.9em" in b, b)
check("padding 简写展开成 padding-left", "padding-left:1em" in b, b)
check("class 的悬挂缩进被采纳", "text-indent:-1em" in b, b)
check("块级不取颜色/字体族/行高", all(x not in b for x in ("color", "font-family", "line-height")), b)
check("块级不取左右 margin (版心由阅读器控制)", "margin-left" not in b and "margin-right" not in b, b)
check("p.po 复合选择器也命中", "letter-spacing:0.02em" in b, b)

b2 = S.block_style("p", {})
check("裸标签 p 的 text-indent 不采纳 (首行缩进归读者)",
      "text-indent" not in b2, b2)
check("裸标签 p 的 margin 简写不采纳", "margin" not in b2, b2)

b3 = S.block_style("p", {"class": "biaoti"})
check("后代选择器的主体被识别 (.biaoti 居中)", "text-align:center" in b3, b3)
check("标题字级被采纳", "font-size:1.7em" in b3, b3)

check("伪类规则不参与静态版面", S.img_style({"class": "x", "tag": "a"}) == "")
check("* 通配规则被忽略", S.block_style("div", {}) == "" or "margin" not in S.block_style("div", {}))

# --------------------------------------------------------------------------
# 2. 真实书: 图文混排
# --------------------------------------------------------------------------
if not FOX.exists():
    print("\n(skip 真实书用例: 没有 library/示例短篇.epub)")
else:
    doc = bookfile.BookDoc(FOX)
    # chapter2.xhtml (Chapter 03 魔) 里 7 个脚注标记图标
    idx = next(i for i, c in enumerate(doc.chapters) if c.href.endswith("chapter2.xhtml"))
    blocks = doc.blocks(idx)
    imgs = [b for b in blocks if "<img" in b.html]
    check("脚注标记图一共 7 处", len(imgs) == 7, len(imgs))
    check("段内小图被标成 .rfig-inl (不是块级插图)",
          all('class="rfig rfig-inl"' in b.html for b in imgs))
    check("段内小图带书内 CSS 的 0.85em 宽度",
          all('style="width:0.85em"' in b.html for b in imgs))
    check("小图不再按原始 500px 铺满",
          all("width:500" not in b.html for b in imgs))

    note = next(b for b in blocks if b.text.startswith("注："))
    check("注文段取到小字 + 悬挂缩进",
          "font-size:0.9em" in note.style and "text-indent:-1em" in note.style, note.style)

    sep = next(b for b in blocks if b.text.strip() == "◎")
    # 书内 `.biaoti` 是 1.7em + bold（原书拿来当标题）, 分隔符不能继承它,
    # 否则一个实心粗圆比正文重得多 —— 视觉规格改由 reader.css 的 .rv-sep 定
    check("装饰分隔符标成 role=sep", sep.role == "sep", sep.role)
    check("装饰分隔符不继承书内字号/字重/字距",
          all(x not in sep.style for x in ("font-size", "font-weight",
                                           "letter-spacing", "text-align")), sep.style)

    head = next(b for b in blocks if b.tag == "h1")
    check("章标题取到书内字级", "font-size:1.5em" in head.style, head.style)

    pay = reader_app._chapter_payload(doc, idx)
    check("章节 payload 带中文版式标记", pay.get("cjk") is True, pay.get("cjk"))
    check("payload 每块带 style 字段（缺省为空串）",
          all("style" in b for b in pay["blocks"]))
    latin = reader_app._chapter_payload(doc, 0)
    check("英文/封面章不当作中文段落", latin.get("cjk") in (False, None), latin.get("cjk"))

    doc.close()

    # 封面章: 内联 SVG 的 <image> 也要认成图片 (整页只有它)
    cover = next(i for i, c in enumerate(doc.chapters) if c.href.endswith("cover.xhtml"))
    cb = doc.blocks(cover)
    if cb:
        check("SVG <image> 被当成插图", "<img" in cb[0].html, cb[0].html[:60])
        check("切片残留的半个标签不当正文显示",
              all("</body" not in b.text for b in cb), [b.text for b in cb])

# --------------------------------------------------------------------------
# 3. 前端 / 样式表
# --------------------------------------------------------------------------
JS = (ROOT / "src/recto/app/ui/assets/reader.js").read_text()
CSSF = (ROOT / "src/recto/app/ui/assets/reader.css").read_text()
HTML = (ROOT / "src/recto/app/reader.py").read_text()

check("渲染时把块样式写到真正的那个元素上",
      'if (b.style) inner.setAttribute(\'style\', b.style);' in JS)
check("中文段落按章打标记", "box.className = 'rv-ch' + (data.cjk ? ' rv-cjk' : '');" in JS)
check("首行缩进只作用于 .rv-cjk 段落 (插图块与装饰分隔符除外)",
      ".rv-page .rv-ch.rv-cjk p:not(.rv-figblk):not(.rv-sep) "
      "{ text-indent: var(--rv-indent, 2em); }" in CSSF)
check("纯图段落不吃首行缩进 (选择器特异度够)",
      ".rv-page .rv-ch p.rv-figblk { text-indent: 0; }" in CSSF
      and "p:not(.rv-figblk)" in CSSF)
check("行内小图有兜底高度上限 (没写尺寸也不会铺满)",
      "max-height: 1.35em" in CSSF and ".rv-page .rfig-inl {" in CSSF)
check("块级插图有最大高度 (不越页)", "max-height: calc(var(--rv-page-h, 80vh) - 7em)" in CSSF)
check("禁则 + 中西文间距 + 标点压缩都在",
      all(x in CSSF for x in ("line-break: strict", "text-autospace: normal",
                              "text-spacing-trim: space-first",
                              "hanging-punctuation: allow-end")))
check("西文整词不拆 (word-break: normal)", "word-break: normal" in CSSF)
check("排版面板有首行缩进开关", 'data-indent="2"' in HTML and 'data-indent="0"' in HTML)
check("缩进设置逐设备记住", "localStorage.setItem('recto.indent'" in JS
      and "localStorage.getItem('recto.indent')" in JS)
check("恢复默认也回到 2 字缩进", "'indent'" in JS and "LOOK_NUM.concat(['font', 'indent'])" in JS)
check("切缩进会重排 + 存进度", "function setIndent(v)" in JS and "queueRelayout(keep)" in JS)

print(f"\n{PASS} passed / {FAIL} failed")
sys.exit(1 if FAIL else 0)
