"""划线画成**波浪下划线** (不是底色块)。

跑法: python3 tests/test_reader_wavy.py

为什么改: 划线原来用 `background-color` 打一块底色 —— 深色皮肤下是一块"白斑",
还会盖掉关键词高亮的半透明底。改成 `text-decoration: underline wavy` 后:
  · 不挡字, 不占底色, 与关键词高亮共存;
  · Chromium 的 `::highlight()` 真的支持 `text-decoration-style: wavy`
    (探针实测像素: 波浪每行 28/112/140/140/140/112/28, 实线恒定 140)。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CSS = (ROOT / "src/recto/app/ui/assets/reader.css").read_text("utf-8")
JS = (ROOT / "src/recto/app/ui/assets/reader.js").read_text("utf-8")

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def highlight_block(name):
    """取 ::highlight(<name>) { ... } 那一段声明。"""
    m = re.search(r"::highlight\(" + re.escape(name) + r"\)\s*\{([^}]*)\}", CSS)
    return m.group(1) if m else ""


print("== Highlight API 路径 (Chromium / 现代浏览器) ==")
for c in ("yellow", "green", "blue", "pink", "underline"):
    blk = highlight_block("rv-hl-" + c)
    check(f"rv-hl-{c} 用波浪下划线", "wavy" in blk, blk)
    check(f"rv-hl-{c} 不再打底色", "background-color" not in blk, blk)
    check(f"rv-hl-{c} 不改文字色", not re.search(r"(^|;)\s*color\s*:", blk), blk)

print("\n== DOM 兜底路径 (浏览器不支持 Highlight API) ==")
m = re.search(r"\.rv mark\.rv-mark\s*\{([^}]*)\}", CSS)
mark = m.group(1) if m else ""
check("兜底 <mark> 也是波浪线", "wavy" in mark, mark)
check("兜底 <mark> 底色透明", "background: transparent" in mark or "background-color: transparent" in mark, mark)
for c in ("green", "blue", "pink", "underline"):
    check(f"兜底 <mark> 有 {c} 线色",
          re.search(r'mark\.rv-mark\[data-color="' + c + r'"\]\s*\{[^}]*text-decoration-color', CSS) is not None)

print("\n== 深色皮肤 ==")
dark = re.findall(r'\.rv\[data-theme="dark"\][^{]*::highlight\(rv-hl-\w+\)[^{]*\{([^}]*)\}', CSS)
check("深色皮肤有 5 条更亮的线色", len(dark) >= 5, f"found {len(dark)}")
check("深色皮肤用 text-decoration-color", all("text-decoration-color" in b for b in dark))

print("\n== JS 侧 ==")
check("划线颜色表含 underline 五档",
      "var names = ['yellow', 'green', 'blue', 'pink', 'underline'];" in JS)
check("抽屉圆点色与正文线色同源",
      "{ yellow: '#f5c518', green: '#4caf50', blue: '#3d8bd9', pink: '#e0699a', underline: '#c0392b' }" in JS)

print(f"\n{PASS} passed / {FAIL} failed")
sys.exit(1 if FAIL else 0)
