"""内置阅读器测试 —— 字节坐标 / 章节块 / 状态库 / 时长位图。

跑法: python3 tests/test_reader.py
"""
import os
import re
import sys
import tempfile
import time
import zipfile
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = tempfile.mkdtemp(prefix="recto-reader-test-")
os.environ["RECTO_DATA_DIR"] = TMP

from recto.core import bookfile, geometry            # noqa: E402
from recto.services import reader_state as rs        # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def make_epub(path: Path):
    opf = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">
 <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
  <dc:title>测试之书</dc:title><dc:creator>某作者</dc:creator>
 </metadata>
 <manifest>
  <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  <item id="c1" href="Text/c1.xhtml" media-type="application/xhtml+xml"/>
  <item id="c2" href="Text/c2.xhtml" media-type="application/xhtml+xml"/>
  <item id="img" href="Images/pic.png" media-type="image/png"/>
  <item id="cov" href="Images/cover.jpg" media-type="image/jpeg" properties="cover-image"/>
 </manifest>
 <spine toc="ncx"><itemref idref="c1"/><itemref idref="c2"/></spine>
 <guide><reference type="cover" href="Text/c1.xhtml"/></guide>
</package>"""
    ncx = """<?xml version="1.0"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
 <navMap>
  <navPoint id="n1"><navLabel><text>第一章 起</text></navLabel><content src="Text/c1.xhtml"/></navPoint>
  <navPoint id="n2"><navLabel><text>第二章 承</text></navLabel><content src="Text/c2.xhtml"/></navPoint>
 </navMap>
</ncx>"""
    c1 = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>一</title></head>
<body><h1>第一章 起</h1>
<p>第一段文字，含有<b>粗体</b>与<em>斜体</em>。</p>
<blockquote><p>引用的一段话。</p></blockquote>
<p><img src="../Images/pic.png" alt=""/></p>
</body></html>"""
    c2 = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>二</title></head>
<body><h1>第二章 承</h1><p>第二章的正文。</p></body></html>"""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/toc.ncx", ncx)
        z.writestr("OEBPS/Text/c1.xhtml", c1)
        z.writestr("OEBPS/Text/c2.xhtml", c2)
        z.writestr("OEBPS/Images/pic.png", b"\x89PNG\r\n\x1a\n")
        z.writestr("OEBPS/Images/cover.jpg", b"\xff\xd8\xff\xe0COVER")


print("== bookfile: epub ==")
epub = Path(TMP) / "book.epub"
make_epub(epub)
doc = bookfile.BookDoc(epub)
check("title/author", doc.title == "测试之书" and doc.author == "某作者", (doc.title, doc.author))
check("chapters", len(doc.chapters) == 2, len(doc.chapters))
check("catalog_total", doc.catalog_total == 2, doc.catalog_total)
check("封面 (BookDoc)", doc.cover == "OEBPS/Images/cover.jpg", doc.cover)
_meta = bookfile.read_meta(epub)
check("封面 (read_meta)", _meta["cover"] == "OEBPS/Images/cover.jpg", _meta)

b0 = doc.blocks(0)
check("blocks 不重复不溢出", len(b0) == 4, [x.tag for x in b0])
check("块标签顺序", [x.tag for x in b0] == ["h1", "p", "p", "p"], [x.tag for x in b0])
check("内联标签保留", "<b>" in b0[1].html and "<em>" in b0[1].html, b0[1].html)
check("blockquote 内部 p 不重复", "引用的一段话" in b0[2].text, b0[2].text)
check("图片重写成 asset 链接", "/asset?p=OEBPS/Images/pic.png" in b0[3].html, b0[3].html)
check("块偏移递增", all(b0[i].off < b0[i + 1].off for i in range(len(b0) - 1)), [x.off for x in b0])

geo = geometry.load_geometry(epub)
ok = True
for ch, off in [(0, 0), (0, 10)]:
    a = doc.percent(ch + 1, off)
    b = geometry.to_float32(geo.percent(ch, off))
    if abs(a - b) > 1e-7:
        ok = False
        print("    mismatch", ch, off, a, b)
check("percent 与云端算法一致", ok)

pos = doc.position(1, 1234)
check("position 格式", pos == "1_0_0$##3-0-1-1234-0##$", pos)
parsed = geometry.parse_position(pos)
check("position 可解析", parsed["chapter_id"] == 0 and parsed["f3"] == "1234", parsed)
check("locate 往返", doc.locate(doc.percent(1, 100)) == (1, 100), doc.locate(doc.percent(1, 100)))

print("== bookfile: txt ==")
txt = Path(TMP) / "novel.txt"
txt.write_text("第一章 开始\n\n第一段。\n\n第二段。\n\n第二章 继续\n\n另一段。\n", encoding="utf-8")
tdoc = bookfile.BookDoc(txt)
check("txt 章数", len(tdoc.chapters) == 2, [c.title for c in tdoc.chapters])
prefix = "第一章 开始\n\n第一段。\n\n第二段。\n\n"
check("txt 章起始字节", tdoc.chapters[1].start == len(prefix.encode()), tdoc.chapters[1].start)
check("txt 块", [b.text for b in tdoc.blocks(0)] == ["第一章 开始", "第一段。", "第二段。"], [b.text for b in tdoc.blocks(0)])
check("txt percent 单调", 0 < tdoc.percent(0, 3) < tdoc.percent(1, 0) < 1.0)
plain = Path(TMP) / "plain.txt"
plain.write_text("没有章节标题的一段文字。\n\n还有一段。\n", encoding="utf-8")
check("txt 无章节标题也能读", len(bookfile.BookDoc(plain).chapters) == 1)

print("== reader_state: 进度 / 书签 / 划线 ==")
rs.reset()
key = "k1"
c = rs.conn()
c.execute("""INSERT OR REPLACE INTO rbook(key,path,kind,title,author,bytes,chapters,size,mtime,cover,added_at,seen_at)
             VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
          (key, str(epub), "epub", "测试之书", "某作者", doc.total_bytes, 2, 1, 1, "", "now", "now"))
c.commit()

p = rs.save_progress(key, 1, 100, doc.percent(1, 100), doc.position(1, 100))
check("进度写入", p["chapter"] == 1 and p["offset"] == 100, p)
check("未读完 complete=0", p["complete"] == 0, p)
p = rs.save_progress(key, 1, 0, 0.9999, doc.position(1, 0))
check("≥99.9% 判读完", p["complete"] == 1, p)

bm1 = rs.add_bookmark(key, 1, 50, "书签甲", "第二章")
bm2 = rs.add_bookmark(key, 1, 50, "书签乙", "第二章")
check("书签按位置去重", bm1["id"] == bm2["id"] and bm2["summary"] == "书签乙", (bm1["id"], bm2["id"]))
check("书签列表", len(rs.list_bookmarks(key)) == 1)
check("书签删除", rs.del_bookmark(bm1["id"]) and not rs.list_bookmarks(key))

n = rs.add_note(key, 0, 100, 140, "一段划线", "我的想法", "yellow",
                '{"sb": 100, "sc": 0, "eb": 100, "ec": 5}')
check("划线写入", n["summary"] == "一段划线" and n["anchors"].startswith("{"), n)
n2 = rs.update_note(n["id"], remark="改过的想法")
check("划线更新", n2 and n2["remark"] == "改过的想法", n2)
check("划线列表", len(rs.list_notes(key)) == 1)
check("划线删除", rs.del_note(n["id"]) and not rs.list_notes(key))

print("== reader_state: 阅读时长位图 ==")
day = date(2026, 11, 3)
ts0 = datetime.combine(day, datetime.min.time()).timestamp() + 9 * 3600  # 09:00
rs.reset()
c = rs.conn()
c.execute("""INSERT OR REPLACE INTO rbook(key,path,kind,title,author,bytes,chapters,size,mtime,cover,added_at,seen_at)
             VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
          (key, str(epub), "epub", "测试之书", "某作者", 1, 1, 1, 1, "", "now", "now"))
c.commit()
rs.session_start(key, ts0)
r1 = rs.session_beat(key, ts0 + 61)
r2 = rs.session_beat(key, ts0 + 121)
check("心跳记分钟", r2["minutes"] >= 2, (r1, r2))
marks = rs.minutes_from_bitmap(
    c.execute("SELECT bitmap FROM rtime WHERE book=? AND day=?", (key, day.isoformat())).fetchone()["bitmap"])
check("位图 = 09:00/09:01/09:02", marks == [540, 541, 542], marks)
# 空闲保护: 隔 40 分钟再心跳, 只补 2 分钟
r3 = rs.session_beat(key, ts0 + 40 * 60)
check("空闲保护", r3["minutes"] <= 5, r3)
check("时长统计", rs.time_stats(key)["total"] >= 2, rs.time_stats(key))

print("== reader_state: 书目列表 ==")
# 书架只列**当前书库目录**里的书 (设置页换目录后旧目录的书要隐藏),
# 所以这里把书库根指到临时目录, 让上面手工插的那本算作"在库里"。
_roots_lib = rs.book_roots
rs.book_roots = lambda: [Path(str(epub)).parent]       # type: ignore[assignment]
books = rs.list_books()
check("书目含进度/计数", len(books) == 1 and books[0]["key"] == key, books)
check("书目字段", {"percent", "notes", "marks", "minutes"} <= set(books[0].keys()), books[0].keys())
rs.book_roots = lambda: [Path(TMP) / "empty-lib"]      # type: ignore[assignment]
check("换书库后旧书不列出", rs.list_books() == [], rs.list_books())
rs.book_roots = _roots_lib                             # type: ignore[assignment]

print("== reader_state: 扫描不抹元数据 ==")
# 书架封面"时有时无"的根因: scan() 的 INSERT..ON CONFLICT 会用 read_meta 的结果
# 覆盖 cover，而 read_meta 曾经根本不解析封面 → 任何一次重扫都把封面清空。
libdir = Path(TMP) / "lib"
libdir.mkdir(exist_ok=True)
epub2 = libdir / "扫.epub"
make_epub(epub2)
_orig_roots = rs.book_roots
rs.book_roots = lambda: [libdir]            # type: ignore[assignment]
try:
    rs.scan()
    b1 = [b for b in rs.list_books() if b["path"] == str(epub2)]
    check("首次扫描就有封面", b1 and b1[0]["cover"] == "OEBPS/Images/cover.jpg", b1)
    rs.scan(force=True)
    b2 = [b for b in rs.list_books() if b["path"] == str(epub2)]
    check("重扫保留封面", b2 and b2[0]["cover"] == "OEBPS/Images/cover.jpg", b2)

    # 解析退化成空 (FUSE 抖动 / 临时读不到) 时也不能覆盖已有值
    _orig_meta = bookfile.read_meta
    bookfile.read_meta = lambda p: {"title": Path(p).stem, "author": "",
                                    "cover": "", "kind": "epub", "bytes": 0}
    try:
        rs.scan(force=True)
    finally:
        bookfile.read_meta = _orig_meta
    b3 = [b for b in rs.list_books() if b["path"] == str(epub2)]
    check("解析失败不清空封面", b3 and b3[0]["cover"] == "OEBPS/Images/cover.jpg", b3)
    check("解析失败不清空书名", b3 and b3[0]["title"] == "测试之书", b3)
finally:
    rs.book_roots = _orig_roots                  # type: ignore[assignment]

# --------------------------------------------------------------------------
# 阅读体验: 顶栏自动隐藏 / 左右翻页 / 点正文关浮层 / 书签笔记分列
# --------------------------------------------------------------------------
from recto.app import reader as rdr          # noqa: E402

_html = rdr._READER_HTML
_css = (ROOT / "src/recto/app/ui/assets/reader.css").read_text("utf-8")
_js = (ROOT / "src/recto/app/ui/assets/reader.js").read_text("utf-8")

check("顶栏有 id (自动隐藏要抓手)", 'id="rv-bar"' in _html)
check("顶栏有收起态", ".rv-bar.is-hidden" in _css and "translateY(-105%)" in _css)
check("顶栏藏起来仍有进度线", 'id="rv-prog"' in _html and ".rv-prog" in _css)
check("滚轮按方向切换顶栏", "setBar(true)" in _js and "setBar(false)" in _js)
check("阅读页不再叠站点顶栏", ".rv-body .rc-top { display: none; }" in _css)

check("左右翻页热区成对存在", _html.count('class="rv-turn') == 2
      and 'data-turn="-1"' in _html and 'data-turn="1"' in _html)
check("热区固定贴边且不压正文", "position: fixed" in _css
      and ".rv-turn-l { left: 0; }" in _css and "calc(50vw - var(--rv-w) / 2" in _css)
check("页边热区不变色, 只改指针", ".rv-turn:hover" in _css
      and "cursor: pointer" in _css
      and ".rv-turn:hover { opacity: .6" not in _css
      and ".rv-turn:active { opacity: .85" not in _css)
check("热区允许纵向滚动 (手机)", "touch-action: pan-y" in _css)
check("拖动手势不触发翻页", "moved > 10" in _js)
check("翻页按屏高 90%", "window.innerHeight * 0.9" in _js)

check("点正文关浮层", "function dismissPopups" in _js
      and "if (dismissPopups()) return;" in _js)
check("浮层含抽屉/工具条/笔记框",
      "if (!noteEdit.hidden) { closeNoteEdit(); hit = true; }" in _js
      and "if (!selBar.hidden) { selBar.hidden = true; hit = true; }" in _js)
check("关笔记框会留草稿", "DRAFT[k] = v" in _js and "DRAFT[k] != null" in _js)
check("Esc 也能关浮层", "ev.key === 'Escape'" in _js)

check("书签 / 笔记是两个抽屉",
      'id="rv-marks"' in _html and 'id="rv-notes"' in _html
      and 'id="rv-marks-list"' in _html)
check("书签抽屉自带加书签", 'class="rv-add" data-act="mark"' in _html)
check("两个列表分别渲染", "function renderMarkList" in _js and "function renderNoteList" in _js)
check("书签列表不混划线", "data-delnote" not in _js.split("function renderMarkList")[1]
      .split("function renderNoteList")[0])
check("笔记列表不混书签", "data-delmark" not in _js.split("function renderNoteList")[1]
      .split("function renderLists")[0])
check("顶栏按钮: 书签开书签抽屉", 'data-act="marks"' in _html)

# ---- 排版面板 ----
for _k, _label in (("fs", "字号"), ("lh", "行间距"), ("para", "段间距"),
                   ("pad", "上下边距"), ("w", "阅读宽度")):
    check(f"排版面板有{_label}", f'data-look="{_k}"' in _html)
check("排版面板有字体输入框 + 自绘下拉", 'id="rv-font"' in _html
      and 'id="rv-font-menu"' in _html and 'role="listbox"' in _html
      and 'id="rv-font-open"' in _html and "function fillFontList" in _js
      and "function renderFontMenu" in _js)
check("字体清单不用原生 datalist (弹层样式不可控)",
      "datalist" not in _html and 'list="rv-font-opts"' not in _html
      and "rv-font-opts" not in _js)
check("字体下拉跟面板同一套变量 (面板底 + 描边 + 强调色)",
      ".rv-combo-menu" in _css and "background: var(--rv-panel)" in _css
      and "border: 1px solid var(--rv-line)" in _css
      and ".rv-combo-opt:hover" in _css and "var(--rv-accent)" in _css)
check("字体输入框无边框填充式 (跟按钮同密度)",
      '.rv-combo > input[type="text"]' in _css
      and "color-mix(in srgb, var(--rv-fg) 7%, transparent)" in _css)
check("字体下拉能筛 / 键盘可选 / 点箭头展开",
      "function fontFiltered" in _js and "ArrowDown" in _js
      and "if (!fontMenuOpen()) openFontMenu(''" in _js
      and "fontMenu.addEventListener('mousedown'" in _js)
check("排版面板可恢复默认", 'data-typo="reset"' in _html and "function resetLook" in _js)
check("排版按钮是图标 (Aa)", 'data-act="typo"' in _html and "<svg" in _html)
check("返回按钮是图标而非字符", "←</a>" not in _html and 'class="rv-back"' in _html)
check("排版值写进 CSS 变量", "--rv-para" in _js and "--rv-pad" in _js
      and "--rv-w', L.w" in _js and "--rv-font" in _js)
check("行间距真的用变量", "line-height: var(--rv-lh)" in _css)
check("段间距真的用变量", "margin: 0 0 var(--rv-para, 1.05em)" in _css)
check("上下边距真的用变量", "padding: var(--rv-pad) 1rem calc(var(--rv-pad)" in _css)
check("字号默认跟随视口 (手机更小)", "matchMedia('(max-width: 620px)')" in _js)
check("点面板外收起", "if (typoEl.contains(ev.target)) return;" in _js)
check("顶栏收起时关掉面板", "if (hide) hideTypo();" in _js)
check("面板里的操作也关浮层", "if (typoEl && !typoEl.hidden) { hideTypo(); hit = true; }" in _js)


# ---- 滚动条 / 顶栏按钮 / 字体清单 (2026-11-04) ----
check("滚动条隐藏但页面仍可滚动",
      "scrollbar-width: none" in _css and "-ms-overflow-style: none" in _css
      and "::-webkit-scrollbar" in _css and "display: none" in _css)
_css_nc = re.sub(r"/\*.*?\*/", "", _css, flags=re.S)   # 去注释, 只看生效的声明
check("藏条后不再留 gutter (右侧不留白沟)", "scrollbar-gutter" not in _css_nc)
check("顶栏不再为滚动条让位", "calc(.8rem + 6px)" not in _css)
check("按钮组不贴右边框", "margin-right: .55rem" in _css)

_bar_btns = _html.split('class="rv-btns"')[1].split("</div>")[0]


def _btn_seg(act):
    """取顶栏某个按钮的完整 <button …>…</button> 片段。"""
    i = _bar_btns.find(f'data-act="{act}"')
    if i < 0:
        return ""
    a = _bar_btns.rfind("<button", 0, i)
    b = _bar_btns.find("</button>", i)
    return _bar_btns[a:b]


for _act in ("toc", "marks", "notes", "typo", "theme"):
    _seg = _btn_seg(_act)
    check(f"顶栏「{_act}」是图标按钮",
          "rv-ico" in _seg and "aria-label" in _seg and "<svg" in _seg)
check("顶栏按钮没有文字标签",
      ">目录<" not in _bar_btns and ">书签<" not in _bar_btns
      and ">笔记<" not in _bar_btns and "◐" not in _bar_btns)
check("图标按钮统一方块 (min-height 归零)",
      "min-height: 0" in _css and "min-height: 2rem" in _css)

check("字体候选表覆盖中文常见族",
      "LXGW WenKai" in _js and "Source Han Serif SC" in _js
      and "PingFang SC" in _js and "SimSun" in _js)
check("字体不靠 canvas 逐个度量 (打开面板零延迟)",
      "measureText(" not in _js and "function detectFonts" not in _js
      and "FONT_CANDIDATES" not in _js and "createElement('canvas')" not in _js)
check("支持读取本机真实字体清单 (Local Font Access)",
      "window.queryLocalFonts" in _js and 'id="rv-font-scan"' in _html)
check("已授权时静默补一次", "function autoScanLocalFonts" in _js)
check("手动输入即生效 (Enter / 失焦, 不用弹出窗)",
      "function applyFontValue" in _js and "ev.key !== 'Enter'" in _js
      and "fontInp.addEventListener('change'" in _js)
check("清空输入 = 跟随界面",
      "function normalizeFontValue" in _js and "localStorage.removeItem('recto.font')" in _js)
check("字体列表可重建 (读到新字体后刷新)",
      "fillFontList" in _js and "fontExtra" in _js)
check("字体清单中文在前 (常用名单顺序 + 读到的按拼音排序)",
      _js.find("思源宋体") < _js.find("Georgia") and "localeCompare" in _js)

# --------------------------------------------------------------------------
# 阅读模式: 滚动 / 左右翻页 (版式设置里可配)
# --------------------------------------------------------------------------
check("排版面板有「阅读模式」分段按钮",
      'id="rv-mode"' in _html and 'data-mode="scroll"' in _html and 'data-mode="page"' in _html)
check("默认是滚动模式", 'data-mode="scroll" aria-pressed="true"' in _html
      and "localStorage.getItem('recto.mode') === 'page' ? 'page' : 'scroll'" in _js)
check("模式记在 localStorage 并即时切换",
      "localStorage.setItem('recto.mode'" in _js and "function setMode" in _js
      and "classList.toggle('is-paged'" in _js)
check("正文外面套一个定高裁切窗口",
      'id="rv-view"' in _html and ".rv.is-paged .rv-view" in _css
      and "height: var(--rv-page-h" in _css)
check("翻页 = 分栏 + 横向平移",
      "column-fill: auto" in _css and "column-gap: 2rem" in _css
      and "translate3d(" in _js and "contentEl.style.columnWidth" in _js)
check("列宽必须等于可视区宽 (否则会并排两列)",
      "function pageGeom" in _js and "var w = Math.round(viewEl.clientWidth)" in _js)
check("每章另起一页 / 图片不跨页",
      "break-before: column" in _css and "break-inside: avoid" in _css)
check("页号表静态化 (翻页动画途中量 rect 会飘)",
      "function rebuildFlow" in _js and "rv-no-anim" in _js
      and "flow.pages[i] === S.page" in _js)
check("分栏异步碎片化要二次稳版",
      "function scheduleSettle" in _js and "settleDepth" in _js)
check("翻页模式有页码指示", 'id="rv-pageind"' in _html and "function paintPageInd" in _js)
check("顶栏收起时阅读区不位移 (翻页不跳)",
      ".rv.is-paged.is-immersive .rv-view" not in _css
      and "margin-top: calc(-1 * var(--rv-bar-h" not in _css)
check("顶栏是浮层, 不占版面", ".rv-bar {\n  position: fixed; top: 0; left: 0; right: 0; z-index: 30;" in _css
      and "position: sticky" not in _css.split(".rv-bar {")[1].split("}")[0])
check("页高 = 整个视口高 (不扣顶栏)",
      "window.innerHeight - barH" not in _js
      and "var h = Math.max(220, Math.round(window.innerHeight));" in _js
      and "height: var(--rv-page-h, 100dvh);" in _css)
check("正文上下边距取同一个值 (垂直居中)",
      "padding: max(var(--rv-pad), env(safe-area-inset-top, 0px), "
      "env(safe-area-inset-bottom, 0px)) 0;" in _css)
check("逐设备记住页号 (块级游标会差一页)",
      "recto.pg." in _js and "function pageSig" in _js and "function pageHintRead" in _js)
check("翻页模式键盘左右可用",
      "ev.key === 'ArrowRight'" in _js and "ev.key === 'ArrowLeft'" in _js
      and "PageDown" in _js)
check("翻页模式支持手机左右滑动",
      "pressFrom" in _js and "from.touch" in _js and "Math.abs(dx) < 44" in _js)
check("翻页到边界自动补章 / 回上一章",
      "function pageStep" in _js and "tail: true" in _js)
check("翻页模式进度按页顶那一段记",
      "function currentAnchor" in _js and "isPaged() ? pageTopBlock() : topBlock()" in _js)

# 排版面板里的滚动不该把排版面板关掉 (字体清单几十条, 用户要用滚轮滑)
check("面板内的滚轮/滑动不收起面板",
      "function scrollInsidePanel" in _js
      and "if (!scrollInsidePanel(ev)) hideTypo();" in _js)
check("从面板里按下的点击不算'点正文'",
      "downInTypo" in _js and "if (downInTypo) return;" in _js)

# --------------------------------------------------------------------------
# 翻页效果 (滑动 / 无 / 淡入 / 水波纹 / 翻卷)
# --------------------------------------------------------------------------
check("排版面板有翻页效果下拉",
      'id="rv-fx"' in _html and ">翻页效果<" in _html)
check("五个效果选项齐全",
      all(f'value="{v}"' in _html for v in ("slide", "none", "fade", "ripple", "flip"))
      and _html.count('<option value="') >= 5)
check("默认滑动 (第一条就是滑动)",
      _html.index('value="slide"') < _html.index('value="none"'))
check("记忆键 recto.fx", "localStorage.getItem('recto.fx')" in _js
      and "localStorage.setItem('recto.fx'" in _js)
check("根节点挂 rv-fx-* 类", "root.classList.add('rv-fx-'" in _js
      and ".rv-fx-none .rv-page { transition: none; }" in _css)
check("非滑动效果把位移瞬时切到位",
      "var fx = (opts.instant || !moved) ? 'none' : fxMode();" in _js
      and "if (fx !== 'slide') root.classList.add('rv-no-anim');" in _js)
check("入场动画走 Web Animations (不碰布局)",
      "root.animate([" in _js and "rotateY(" in _js
      and "perspective(2200px)" in _js)
check("效果盖满整页 (固定满屏舞台, 不是只盖正文列)",
      'class="rv-fxstage"' in _html and "function fxStage()" in _js
      and ".rv-fxstage {" in _css and "position: fixed; inset: 0; z-index: 32;" in _css
      and all(cls in _css for cls in (".rv-fxl-veil", ".rv-fxl-wave", ".rv-fxl-sweep"))
      and "fxStage().appendChild" not in _js and "st.appendChild(el)" in _js)
check("水波纹是满屏一层不吃指针的覆盖层",
      "function fxRipple" in _js and "220vmax" in _css
      and "overflow: hidden; pointer-events: none; contain: paint;" in _css)
check("flip 不往 body 挂 perspective (fixed 层会改容器)",
      "perspective(2200px) rotateY(" in _js and "body.style.perspective" not in _js)
check("减少动态效果时默认不播动画",
      "prefers-reduced-motion: reduce" in _js and "if (prefersReduced() && !fxStored()) return 'none';" in _js)
check("恢复默认也回到滑动", "S.fx = 'slide';" in _js
      and "localStorage.removeItem('recto.fx')" in _js)
check("面板下拉改高度对齐其它行 (不吃 44px 触控高)",
      "min-height: 0; height: 2.2rem;" in _css)

print(f"\n{PASS} passed / {FAIL} failed")
sys.exit(1 if FAIL else 0)
