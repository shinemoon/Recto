"""版面呼吸: 相邻块之间不许贴死 —— 把一次真实报障的修法钉住。

     python3 tests/test_spacing.py

只查"声明在不在", 不查具体像素 (疏密档 compact/roomy 改了不该红);
真几何由 tools/probe_spacing.mjs 在浏览器里量。

背景: 书架上那一格格子 (#shelf) 与下面的「上传到掌阅云」折叠区之间是 0px,
两块贴成一块。扫全站之后同类还有 分页条→摘录列表 (0px)、来源行→摘录正文 (2px)、
「导出到纸间」表单的标签与输入框并排挤在一行、设置页单列行只有 min-height 没内边距。
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

_tmp = tempfile.mkdtemp(prefix="recto-spacing-")
os.environ["RECTO_DATA_DIR"] = _tmp
os.environ.setdefault("RECTO_AUTH_DISABLE", "1")

PASS = 0
FAIL = 0


def ok(cond, name, info=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}" + (f" :: {info}" if info else ""))
    else:
        FAIL += 1
        print(f"  FAIL {name}" + (f" :: {info}" if info else ""))


def group(text: str, sel: str) -> str:
    """取某条规则的大括号内容 (按选择器前缀找第一条)。"""
    m = re.search(r"(?m)^\s*" + re.escape(sel) + r"\s*[^{]*\{([^}]*)\}", text)
    return m.group(1) if m else ""


def main() -> int:
    css = (_ROOT / "src/recto/app/ui/assets/recto.css").read_text(encoding="utf-8")
    settings = (_ROOT / "src/recto/app/settings_page.py").read_text(encoding="utf-8")
    excerpts = (_ROOT / "src/recto/app/excerpts.py").read_text(encoding="utf-8")

    # ---- 书架格子 → 上传折叠区 (本次报障点)
    g = group(css, ".shelf-grid")
    ok("margin" in g and "rc-gap" in g,
       "书架网格自带下边距 (与下方折叠区留呼吸)", g.replace("\n", " ").strip()[:80])
    ok("margin: 0 0" in g or "margin-bottom" in g,
       "书架网格的下边距是显式声明的", g.replace("\n", " ").strip()[:80])

    # ---- 分页条: 上下都要留白
    ok(".pager + .timeline" in css, "分页条下面紧跟列表时有下边距规则")
    ok(re.search(r"\.pager \+ \.timeline[^{]*\{[^}]*margin-top", css) is not None,
       "分页条→列表的 margin-top 真的写了")

    # ---- 小标题不许贴着图表/列表
    k = group(css, ".kicker,")
    ok("margin" in k and "8px" in k, "小标题 (.kicker) 有下边距", k.replace("\n", " ").strip()[:80])
    ks = group(css, ".kicker-sm")
    ok("margin-bottom" in ks, "统计卡小字 (.kicker-sm) 不贴数字", ks.replace("\n", " ").strip()[:80])

    # ---- 摘录: 来源行 → 正文
    c = group(css, ".tl-item .tl-content")
    m = re.search(r"margin:\s*(\d+)px", c)
    ok(bool(m) and int(m.group(1)) >= 6,
       "摘录正文的上外边距 ≥6px (不再只隔 2px)", c.replace("\n", " ").strip()[:80])

    # ---- 手机: 操作轨落回页头后要留呼吸 / 摘录折成一列后行距
    ok(".rc-head-row:has(> .rc-rail:only-child) { padding: 0 0 2px; margin: 0 0 12px; }" in css,
       "手机页头行 (操作轨) 与下方搜索栏留出间距")
    ok(".tl-item { grid-template-columns: 1fr; gap: 8px 0; }" in css,
       "手机摘录卡折成一列后行距放大")

    # ---- 设置页: 单列块里的行与开关行同一节奏
    ok(".st-block>.st-row{" in settings, "设置页单列行有独立规则")
    ok(re.search(r"\.st-block>\.st-row\{[^}]*padding:8px 0", settings) is not None,
       "设置页单列行有 8px 上下内边距")
    ok(re.search(r"\.st-block>\.st-row\{[^}]*border-bottom", settings) is not None,
       "设置页单列行有分隔线")
    st2 = group(settings, ".st-two")
    m2 = re.search(r"gap:\s*(\d+)px", st2)
    ok(bool(m2) and int(m2.group(1)) >= 12,
       "设置页两栏块之间 ≥12px (手机上它们会叠成一列)", st2.replace("\n", " ").strip()[:70])

    # ---- 导出到纸间: 标签在上, 控件在下 (原来并排挤在一行)
    ok("#zhijian-form{display:grid" in excerpts, "导出表单改成网格 (标签不再与输入框并排)")
    ok(re.search(r"#zhijian-form>label\{[^}]*margin:12px 0 6px", excerpts) is not None,
       "导出表单每个字段之间 12px")
    ok(re.search(r"#zhijian-form>input,#zhijian-form>select\{width:100%\}", excerpts) is not None,
       "导出表单的控件占满一列")

    # ---- 探针存在 (几何由它在浏览器里量)
    ok((_ROOT / "tools/probe_spacing.mjs").is_file(), "版面呼吸探针在仓库里")

    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.exit(1)
