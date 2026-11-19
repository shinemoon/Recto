"""划线画成**波浪下划线** (不是底色块), 且线色**随主题**走。

跑法: python3 tests/test_reader_wavy.py

为什么改: 划线原来用 `background-color` 打一块底色 —— 深色皮肤下是一块"白斑",
还会盖掉关键词高亮的半透明底。改成 `text-decoration: underline wavy` 后:
  · 不挡字, 不占底色, 与关键词高亮共存;
  · Chromium 的 `::highlight()` 真的支持 `text-decoration-style: wavy`
    (探针实测像素: 波浪每行 28/112/140/140/140/112/28, 实线恒定 140)。

线色口径 (2026-11-18 用户拍板): **一个**主题变量 `--rv-hl` ——
淡色主题深蓝 `#1d4e89`, 深色主题浅灰 `#c9ccd4`。
划线原本分 yellow/green/blue/pink/underline 五档各一个色, 满页五颜六色;
现在五档只用来区分"哪条"(列表顺序), 线本身统一。

两个浏览器限制 (实测, 别再踩):
  · `::highlight()` **不支持逗号多选择器** —— `::highlight(a, b)` 整条规则失效,
    所以五档必须各写一条规则 (值都取同一个变量);
  · `::highlight()` **支持 var() 从元素继承**, 但 CSS 的 `prefers-color-scheme`
    **不跟随**页面上的 `color-scheme` —— 所以"auto 主题跟随外壳皮肤"这件事
    只能由服务端把皮肤模式标到 DOM (`data-shell`), 不能靠 media 查询。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CSS = (ROOT / "src/recto/app/ui/assets/reader.css").read_text("utf-8")
JS = (ROOT / "src/recto/app/ui/assets/reader.js").read_text("utf-8")
PY = (ROOT / "src/recto/app/reader.py").read_text("utf-8")

PASS = FAIL = 0

LIGHT = "#1d4e89"    # 淡色主题: 深蓝
DARK = "#c9ccd4"     # 深色主题: 浅灰


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


def var_block(selector):
    """取 <selector> { ... } 那一段声明 (变量定义块)。`selector` 不带花括号。"""
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    return m.group(1) if m else ""


def has_var(block, name, value):
    """块里有没有 `--name: value` (冒号两侧空格不限)。"""
    return re.search(re.escape(name) + r"\s*:\s*" + re.escape(value), block) is not None


print("== Highlight API 路径 (Chromium / 现代浏览器) ==")
for c in ("yellow", "green", "blue", "pink", "underline"):
    blk = highlight_block("rv-hl-" + c)
    check(f"rv-hl-{c} 用波浪下划线", "wavy" in blk, blk)
    check(f"rv-hl-{c} 不再打底色", "background-color" not in blk, blk)
    check(f"rv-hl-{c} 不改文字色", not re.search(r"(^|;)\s*color\s*:", blk), blk)
    check(f"rv-hl-{c} 线色取统一变量", "var(--rv-hl)" in blk, blk)
    check(f"rv-hl-{c} 不写死颜色", not re.search(r"#[0-9a-fA-F]{3,8}\b", blk), blk)

print("\n== DOM 兜底路径 (浏览器不支持 Highlight API) ==")
m = re.search(r"\.rv mark\.rv-mark\s*\{([^}]*)\}", CSS)
mark = m.group(1) if m else ""
check("兜底 <mark> 也是波浪线", "wavy" in mark, mark)
check("兜底 <mark> 底色透明", "background: transparent" in mark or "background-color: transparent" in mark, mark)
check("兜底 <mark> 线色取统一变量", "var(--rv-hl)" in mark, mark)

print("\n== 主题变量 ==")
base = var_block(".rv")
check("默认 (.rv) 线色 = 淡色深蓝", has_var(base, "--rv-hl", LIGHT), base)
dark = var_block('.rv[data-theme="dark"]')
check("深色主题线色 = 浅灰", has_var(dark, "--rv-hl", DARK), dark)
auto = var_block('.rv[data-theme="auto"][data-shell="dark"]')
check("auto 主题跟随外壳皮肤 (服务端标记 data-shell)",
      has_var(auto, "--rv-hl", DARK), auto)
check("淡色四主题 (auto/light/sepia/green) 不覆盖线色",
      all(f"--rv-hl" not in var_block(s)
          for s in ('.rv[data-theme="light"]', '.rv[data-theme="sepia"]',
                    '.rv[data-theme="green"]')))

print("\n== 服务端: 外壳皮肤模式标到 DOM ==")
check("reader.py 导入 skins", "from recto.app.ui import skins as _skins" in PY)
check("reader.py 有 _shell_mode()", "def _shell_mode()" in PY)
check("_shell_mode 读 skins.resolve()", "_skins.resolve()" in PY)
check("DOM 上带 data-shell", 'data-shell="{shell_mode}"' in PY)
check("模板填入 shell_mode", "shell_mode=_e(_shell_mode())" in PY)

print("\n== 抽屉圆点 ==")
check("圆点用 CSS 变量 (不再内联色)",
      re.search(r"\.rv-note-item \.dot\s*\{[^}]*background:\s*var\(--rv-hl\)", CSS) is not None)
check("JS 不再内联圆点颜色", "colorHex" not in JS)

print("\n== JS 侧 ==")
check("划线颜色表含 underline 五档",
      "var names = ['yellow', 'green', 'blue', 'pink', 'underline'];" in JS)

print(f"\n{PASS} passed / {FAIL} failed")
sys.exit(1 if FAIL else 0)
