"""注释层: 识别 + 归类 + 绑定 (`core/notes.py`)。

跑法: python3 tests/test_notes.py

覆盖四类写法（换书不用改代码的验收就在这里）:
1. 多看式 `<aside epub:type="footnote"><ol><li>` 紧跟段落 + 标记是带 `alt` 的图片
   —— 本样本有 52 个 `href` 被重复使用，id 指向的 `<aside>` 与引用处不是同一条，
     所以必须**内容优先**；
2. epub3 尾注 `<section epub:type="endnotes"><ol><li id="fn1">` + 章末堆叠；
3. 只靠 class 命名的 `<div class="footnotes"><p class="footnote" id=…>`；
4. 没有注释的书（零开销路径）。

外加: 段落内部嵌注释容器、孤立注释（正文没引用）、正文文本里不许混进标记数字。
"""
import os
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = tempfile.mkdtemp(prefix="recto-notes-test-")
os.environ["RECTO_DATA_DIR"] = TMP

from recto.core import bookfile, notes                # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">
 <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
  <dc:title>{title}</dc:title><dc:creator>某作者</dc:creator>
 </metadata>
 <manifest>
  <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  <item id="c1" href="Text/c1.xhtml" media-type="application/xhtml+xml"/>
 </manifest>
 <spine toc="ncx"><itemref idref="c1"/></spine>
</package>"""

NCX = """<?xml version="1.0"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
 <navMap><navPoint id="n1"><navLabel><text>第一章</text></navLabel>
 <content src="Text/c1.xhtml"/></navPoint></navMap>
</ncx>"""

XHTML = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>一</title></head>
<body>{body}</body></html>"""


def make_book(name, body, title="注释样本"):
    path = Path(TMP) / (name + ".epub")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("OEBPS/content.opf", OPF.format(title=title))
        z.writestr("OEBPS/toc.ncx", NCX)
        z.writestr("OEBPS/Text/c1.xhtml", XHTML.format(body=body))
    return path


def markers(blocks):
    import re
    out = []
    for b in blocks:
        out.extend(int(x) for x in re.findall(r'data-note="(\d+)"', b.html))
    return out


# --------------------------------------------------------------------------
# 1. 多看式: 内容优先, href 复用不可信
# --------------------------------------------------------------------------
print("== 多看式 (aside 内嵌 / href 复用 / 内容优先) ==")
DUOKAN = """
<div>
<p>甲段正文<sup class="x"><a epub:type="noteref" href="#fn-a"><img alt="注释甲：某书第 12 页。" src="../Images/i.png"/></a></sup>续甲段。</p>
<aside epub:type="footnote" id="fn-a"><ol class="duokan-footnote-content"><li class="duokan-footnote-item">注释甲：某书第 12 页。</li></ol></aside>
<p>丙段正文<sup><a epub:type="noteref" href="#fn-a"><img alt="注释丙：另一本书第 7 页。" src="../Images/i.png"/></a></sup>续丙段。</p>
<aside epub:type="footnote" id="fn-c"><ol><li>注释丙：另一本书第 7 页。</li></ol></aside>
<aside epub:type="footnote" id="fn-b"><ol><li>注释乙：没有标记引用我。</li></ol></aside>
</div>"""
doc = bookfile.BookDoc(make_book("duokan", DUOKAN))
bl = doc.blocks(0)
nt = doc.notes(0)
check("标记数 = 2", len(markers(bl)) == 2, markers(bl))
check("注释 3 条 (含孤立)", len(nt) == 3, nt)
check("第二个标记内容优先 (不会被复用的 id 拽到第一条)",
      nt[1]["text"].startswith("注释丙"), nt[1])
check("两条标记各得一个新号", markers(bl) == [1, 2], markers(bl))
check("孤立注释被编号且标记 orphan", nt[2]["orphan"] is True, nt[2])
check("正文里不再出现注释全文", all("注释甲" not in b.html for b in bl),
      [b.html[:40] for b in bl])
check("正文文本不含标记数字", all(not b.text.endswith("1") for b in bl),
      [b.text[-8:] for b in bl])
check("aside 不再作为正文块", all(b.tag != "aside" for b in bl), [b.tag for b in bl])
check("注释正文清洗掉编号前缀", not nt[0]["text"][:2].strip().isdigit(), nt[0]["text"])

print("== 标记自带正文但没有对应容器 ==")
NOPOOL = """
<div><p>正文<a epub:type="noteref" href="#gone"><img alt="只在属性里的注释正文，第 5 页。"/></a>。</p>
<p>正文二<a epub:type="noteref" href="#gone2">2</a>。</p></div>"""
doc = bookfile.BookDoc(make_book("nopool", NOPOOL))
nt = doc.notes(0)
check("用标记自带内容建条", len(nt) == 1 and nt[0]["text"].startswith("只在属性里"), nt)
check("没有正文的标记 → rv-fn-void", any("rv-fn-void" in b.html for b in doc.blocks(0)),
      [b.html for b in doc.blocks(0)])

# --------------------------------------------------------------------------
# 2. epub3 尾注 (章末 <section><ol><li id>)
# --------------------------------------------------------------------------
print("== epub3 尾注 (章末 ol/li) ==")
ENDNOTES = """
<div>
<p>正文一<a epub:type="noteref" href="#fn1">1</a>，继续。</p>
<p>正文二<a epub:type="noteref" href="#fn2">2</a>，继续。</p>
<section epub:type="endnotes" role="doc-endnotes">
  <h2>注释</h2>
  <ol><li id="fn1"><p>尾注一：某书第 3 页。</p></li>
      <li id="fn2"><p>尾注二：某书第 9 页。</p></li></ol>
</section>
</div>"""
doc = bookfile.BookDoc(make_book("endnotes", ENDNOTES))
bl = doc.blocks(0)
nt = doc.notes(0)
check("两条尾注", len(nt) == 2, nt)
check("尾注按 id 绑定", [n["text"][:3] for n in nt] == ["尾注一", "尾注二"], nt)
check("尾注容器不进正文", all("尾注一" not in b.html for b in bl), [b.html[:30] for b in bl])
check("标记号按出现顺序", markers(bl) == [1, 2], markers(bl))
check("尾注不 orphan", all(not n["orphan"] for n in nt), nt)

# --------------------------------------------------------------------------
# 3. 只靠 class 命名
# --------------------------------------------------------------------------
print("== class 驱动 (footnotes / footnote-ref) ==")
CLS = """
<div>
<p>第一段<a class="calibre footnote-ref" href="#n1">[1]</a>。</p>
<div class="footnotes">
  <p class="footnote" id="n1">类名注释一：某书第 5 页。</p>
  <p class="footnote" id="n2">类名注释二：某书第 6 页。</p>
</div>
</div>"""
doc = bookfile.BookDoc(make_book("cls", CLS))
bl = doc.blocks(0)
nt = doc.notes(0)
check("类名注释 2 条", len(nt) == 2, nt)
check("类名注释按 id 绑定", nt[0]["text"].startswith("类名注释一"), nt[0])
check("未引用的那条 orphan", nt[1]["orphan"] is True, nt[1])
check("容器块被剔掉", all("类名注释一" not in b.html for b in bl), [b.html[:30] for b in bl])

# --------------------------------------------------------------------------
# 4. 段落内部嵌 aside (少数书这么写)
# --------------------------------------------------------------------------
print("== 注释容器嵌在段落内部 ==")
INLINE = """
<div><p>段首文字<aside epub:type="footnote" id="x1"><ol><li>段内注释正文。</li></ol></aside>段尾文字<sup><a epub:type="noteref" href="#x1">1</a></sup>。</p></div>"""
doc = bookfile.BookDoc(make_book("inline", INLINE))
bl = doc.blocks(0)
nt = doc.notes(0)
check("段内注释不污染正文", all("段内注释正文" not in b.text for b in bl),
      [b.text for b in bl])
check("段首段尾文字还在", any("段首文字" in b.text and "段尾文字" in b.text for b in bl),
      [b.text for b in bl])
check("段内注释也能绑上", len(nt) == 1 and nt[0]["text"].startswith("段内注释"), nt)

# --------------------------------------------------------------------------
# 5. 没有注释的书: 零开销
# --------------------------------------------------------------------------
print("== 无注释 ==")
PLAIN = "<div><p>只有正文，没有任何注释。</p><p>第二段。</p></div>"
doc = bookfile.BookDoc(make_book("plain", PLAIN))
bl = doc.blocks(0)
check("无注释 → notes 为空", doc.notes(0) == [], doc.notes(0))
check("正文块照旧", len(bl) == 2, len(bl))
check("没有 rv-fn 标记", markers(bl) == [], markers(bl))
check("harvest 返回 None", notes.harvest(b"<html><body><p>x</p></body></html>") is None)

# --------------------------------------------------------------------------
# 6. 单元: 编号去前缀 / 短 hint 不当正文 / 无正文标记
# --------------------------------------------------------------------------
print("== 细节 ==")
layer = notes.NoteLayer()
layer.add("［3］某书，第 12 页。")
layer.add("另一条，第 13 页。")
layer.finish()
check("去编号前缀", layer.notes[0].text == "某书，第 12 页。", layer.notes[0].text)

layer = notes.NoteLayer()
layer.add("正文甲，第 1 页。", "a1")
note = layer.resolve("1", "#a1")          # hint 只是标签
check("短 hint 走 id", note is not None and note.text.startswith("正文甲"), note)
layer.finish()
check("短 hint 不留垃圾条目", len(layer.notes) == 1, len(layer.notes))

layer = notes.NoteLayer()
layer.finish()
check("空池 resolve 返回 None", layer.resolve("", "#nope") is None)
check("无正文标记 HTML", "rv-fn-void" in notes.NoteLayer.marker_html(None))
check("正常标记 HTML", 'data-note="2"' in notes.NoteLayer.marker_html(notes.Note(2, "x")))

# --------------------------------------------------------------------------
# 7. 真书抽样 (库里有这本才跑)
# --------------------------------------------------------------------------
REAL = ROOT / "library" / "Others" / "九诗心：暗夜里的文学启明.epub"
if REAL.exists():
    print("== 真书 (九诗心, 541 条注释) ==")
    doc = bookfile.BookDoc(REAL)
    total = orphan = unresolved = 0
    mismatched = []
    for i in range(len(doc.chapters)):
        ns = doc.notes(i)
        total += len(ns)
        orphan += sum(1 for n in ns if n["orphan"])
        unresolved += sum(1 for n in ns if n["unresolved"])
        got = sorted(markers(doc.blocks(i)))
        if got != sorted(n["n"] for n in ns):
            mismatched.append(i)
    check("541 条注释", total == 541, total)
    check("全部绑上正文 (0 孤立 / 0 无正文)", orphan == 0 and unresolved == 0,
          (orphan, unresolved))
    check("每章标记号与注释号一一对应", not mismatched, mismatched[:5])
    # 判"漏进正文"看的是**整块就是那条注释**（注释正文曾经被当成独立段落渲染）。
    # 不能用"包含"—— 注释常是引文，书名/句子本来也会出现在正文里（实测假阳性）。
    leak = 0
    for i in range(len(doc.chapters)):
        for b in doc.blocks(i):
            t = " ".join((b.text or "").split())
            if not t:
                continue
            for n in doc.notes(i):
                nt = " ".join(n["text"].split())
                if nt and (t == nt or (t.startswith(nt) and len(t) <= len(nt) + 8)):
                    leak += 1
                    break
    check("注释全文没有混进正文", leak == 0, leak)
else:
    print("  (跳过真书抽样: 库里没有这本书)")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
