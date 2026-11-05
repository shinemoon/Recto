"""装饰分隔符（◎ / ** / ——）的规格：不继承书内标题字号、居中、字距分档。

跑法: python3 tests/test_reader_sep.py

原问题: 示例短篇 把分隔符挂在 `.biaoti` 上（书内 CSS = 1.7em + bold），
我们照抄字号 → 19px 正文旁边一个 32px 的实心粗圆，视觉上"过大"。
现在分隔符只认自己的 `.rv-sep` 规则，书内 font-size / font-weight /
letter-spacing / text-align 全部丢弃。
"""
import os
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = tempfile.mkdtemp(prefix="recto-sep-test-")
os.environ["RECTO_DATA_DIR"] = TMP

from recto.core import bookfile            # noqa: E402
from recto.app import reader as reader_app  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


HTML = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>一</title>
<link rel="stylesheet" href="../Styles/style.css"/></head>
<body><div class="section">
<h1 class="biaoti1">第一章</h1>
<p>正文第一段。</p>
<p class="biaoti">◎</p>
<p>正文第二段。</p>
<p class="biaoti">——</p>
<p>正文第三段。</p>
<p class="biaoti">※※</p>
<p>正文第四段。</p>
</div></body></html>"""

CSS = """
body > .section .biaoti {
  font-size: 1.7em; font-weight: bold; text-align: center;
  margin: -0.3em 0 -0.5em 0.3em; color: #7e0f06;
}
.biaoti1 { font-size: 1.5em; font-weight: bold; text-align: center; }
"""

OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">
 <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
  <dc:title>分隔符之书</dc:title><dc:creator>某作者</dc:creator>
 </metadata>
 <manifest>
  <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  <item id="c1" href="Text/c1.xhtml" media-type="application/xhtml+xml"/>
  <item id="css" href="Styles/style.css" media-type="text/css"/>
 </manifest>
 <spine toc="ncx"><itemref idref="c1"/></spine>
</package>"""

NCX = """<?xml version="1.0"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
 <navMap><navPoint id="n1"><navLabel><text>第一章</text></navLabel>
  <content src="Text/c1.xhtml"/></navPoint></navMap>
</ncx>"""

epub = Path(TMP) / "sep.epub"
with zipfile.ZipFile(epub, "w") as z:
    z.writestr("mimetype", "application/epub+zip")
    z.writestr("OEBPS/content.opf", OPF)
    z.writestr("OEBPS/toc.ncx", NCX)
    z.writestr("OEBPS/Text/c1.xhtml", HTML)
    z.writestr("OEBPS/Styles/style.css", CSS)

doc = bookfile.BookDoc(epub)
blocks = doc.blocks(0)
by_text = {b.text.strip(): b for b in blocks if b.text.strip()}

print("== 角色分档 ==")
check("单字符 ◎ → role=sep", by_text["◎"].role == "sep", by_text["◎"].role)
check("破折号串 —— → role=sep-dash", by_text["——"].role == "sep-dash", by_text["——"].role)
check("多字符 ※※ → role=sep-multi", by_text["※※"].role == "sep-multi", by_text["※※"].role)
check("普通正文不带角色", by_text["正文第一段。"].role == "", by_text["正文第一段。"].role)
check("标题不当分隔符", not any(b.role and b.tag.startswith("h") for b in blocks))

print("== 书内字号不再泄漏 ==")
for t in ("◎", "——", "※※"):
    st = by_text[t].style
    check(f"{t} 丢掉书内 font-size / font-weight",
          "font-size" not in st and "font-weight" not in st, st)
    check(f"{t} 丢掉书内 letter-spacing / text-align",
          "letter-spacing" not in st and "text-align" not in st, st)

print("== 章节 payload 带上角色 ==")
pay = reader_app._chapter_payload(doc, 0)
roles = {b["text"].strip(): b.get("role") for b in pay["blocks"] if b.get("text")}
check("payload 有 role 字段", "role" in pay["blocks"][0])
check("payload 的 ◎ 是 sep", roles.get("◎") == "sep", roles.get("◎"))

print("== 前端规则 ==")
CSSF = (ROOT / "src/recto/app/ui/assets/reader.css").read_text(encoding="utf-8")
JS = (ROOT / "src/recto/app/ui/assets/reader.js").read_text(encoding="utf-8")

check(".rv-sep 存在", ".rv-page .rv-sep {" in CSSF)
check(".rv-sep 字号小于正文 (<=1em)", "font-size: .9em" in CSSF)
check(".rv-sep 字重归正 (不被书内 bold 拖走)", "font-weight: 400" in CSSF)
check(".rv-sep 同时写 text-align-last: center (否则单字符仍左对齐)",
      "text-align-last: center" in CSSF)
check(".rv-sep 单字符不放字距 (字距会导致居中偏移)", "letter-spacing: 0" in CSSF)
check("多字符分档有字距", ".rv-page .rv-sep-multi { letter-spacing: .4em" in CSSF)
check("破折号档不收字距", ".rv-page .rv-sep-dash { letter-spacing: 0; }" in CSSF)
check("首行缩进规则排除分隔符", "p:not(.rv-figblk):not(.rv-sep)" in CSSF)
check("JS 三种角色都挂 class",
      "'rv-sep'" in JS and "'rv-sep-multi'" in JS and "'rv-sep-dash'" in JS
      and "b.role === 'sep'" in JS)

print("== 兜底 helper ==")
check("破折号判定 (—— / —)", bookfile._is_dash_only("——") and bookfile._is_dash_only("—"))
check("非破折号不误判", not bookfile._is_dash_only("◎") and not bookfile._is_dash_only("*"))
check("_drop_decls 只剔指定属性",
      bookfile._drop_decls("a:1;font-size:2em;b:2", {"font-size"}) == "a:1;b:2")

print(f"\n{PASS} passed / {FAIL} failed")
sys.exit(1 if FAIL else 0)
