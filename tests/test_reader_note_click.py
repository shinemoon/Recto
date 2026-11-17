"""点划线 → 就地看笔记: **章内匹配** + 划过章不被抹掉。

跑法: python3 tests/test_reader_note_click.py

两个真事故 (2026-11-17):
1. 在「建安二十二年的瘟疫」(第 10 篇) 第一页点一下普通正文, 弹出的是第 8 篇那条
   1555~1945 的笔记 —— 字节偏移是**章内**的, 旧代码只拿偏移去比全书的笔记。
2. 划线的 CSS Custom Highlight 名字是全局的, 旧代码每次重画都先删掉全部
   `rv-hl-*` 再只装当前章 → 预取的下一章一画, 前一章的划线全消失
   (跳到第 8 篇后立刻预取第 9 篇, 第 8 篇一条划线都不见了)。
"""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

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


def block(fn_name):
    """粗切一个 JS 函数体 (够用来做源码契约断言)。"""
    i = JS.find("function " + fn_name + "(")
    if i < 0:
        return ""
    j = JS.find("\n  function ", i + 10)
    return JS[i:j if j > 0 else i + 4000]


# ---------------- ① 点击 → 必须带出"这是哪一章" ----------------
point = block("pointAt")
check("点位置解析成 {ch, byte}", bool(point) and "ch: blk.dataset.ch" in point)
check("字节偏移仍按块起点累加", "blk.dataset.off + bytesOf(" in point)

nat = block("noteAtPoint")
check("noteAtPoint 不再只靠偏移", "blk.dataset.off" not in nat or "p.ch" in nat)
check("没指定章时用点到的那块所在章", "if (ch == null) ch = p.ch;" in nat)
check("认不出章就放弃, 不猜", "if (ch == null) return null;" in nat)
check("逐条笔记按章过滤", "if (+n.chapter !== +ch) return;" in nat)
check("没有旧写法 (只判非空就算匹配)", "if (ch != null && +n.chapter !== +ch) return;" not in JS)

# 点击入口: 把坐标交给 noteAtPoint 即可 (章由 pointAt 带出来)
check("点击入口调 noteAtPoint", "var hit = noteAtPoint(ev.clientX, ev.clientY);" in JS)
check("点中注释标记优先 (别被笔记盖住)",
      JS.find("var fnHit = ev.target.closest") < JS.find("var hit = noteAtPoint("))

# ---------------- ② 划线高亮按章登记, 别章不许被抹 ----------------
check("有按章登记表", "var HLREG = Object.create(null);" in JS)
check("有统一提交函数", "function hlCommit(" in JS and "CSS.highlights.set(name, h)" in JS)
check("只摘掉当前章的区间", "function hlForget(ch)" in JS
      and "HLREG[n] = HLREG[n].filter(function (it) { return it.ch !== ch; })" in JS)
check("paintHighlights 用 hlForget, 不再全删",
      "hlForget(ch);" in block("paintHighlights")
      and "names.forEach(function (n) { CSS.highlights.delete('rv-hl-' + n); });" not in JS)
check("重新画时按全量重建", "names.forEach(function (n) { hlCommit('rv-hl-' + n); });" in JS)
check("换章清空登记表 (Range 会变游离节点)",
      re.search(r"S\.loaded = \{\};.*?delete HLREG\[n\]", JS, re.S) is not None)
check("每章各自压着关键词底色", "h.priority = 1" in block("hlCommit"))

# ---------------- ③ 探针存在且守着这两条 ----------------
probe = ROOT / "tools/probe_reader_note_click.mjs"
check("有真浏览器探针", probe.exists())
pt = probe.read_text("utf-8") if probe.exists() else ""
check("探针覆盖跨章误弹", "第 10 篇点正文不弹笔记框" in pt)
check("探针覆盖点中划线弹框", "点中划线弹出笔记框" in pt)
check("探针带进度快照还原 (探针卫生)",
      "readProgress(" in pt and "writeProgress(" in pt)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
