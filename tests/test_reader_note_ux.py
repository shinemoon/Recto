"""阅读器三件事的静态契约 (真行为由 tools/probe_reader_note_ux.mjs 验):

  1. 笔记抽屉从新到旧
  2. 点划线 → 就地看/删
  3. 选区工具条多了「查询」(开放词典) 与「搜索」(网页)

静态检查只钉"这件事有没有人做、用的是不是同一条通道"——具体好不好用靠探针。
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

APP = os.path.join(ROOT, "src", "recto", "app")
JS = open(os.path.join(APP, "ui", "assets", "reader.js"), encoding="utf-8").read()
CSS = open(os.path.join(APP, "ui", "assets", "reader.css"), encoding="utf-8").read()
PY = open(os.path.join(APP, "reader.py"), encoding="utf-8").read()

checks = []


def ok(name, cond, extra=""):
    checks.append((name, bool(cond), str(extra)))


def block(name, src=JS):
    """取一个函数体 (到下一个顶格 `function` 或文件尾)。"""
    i = src.find("function " + name)
    if i < 0:
        return ""
    j = src.find("\n  function ", i + 10)
    return src[i:j if j > 0 else len(src)]


# ---------------------------------------------------------------- 1. 列表顺序
note_list = block("renderNoteList")
ok("笔记列表有 renderNoteList", bool(note_list))
ok("笔记列表按 created_at 倒序 (新的在前)", "created_at" in note_list and ".sort(" in note_list,
   note_list[:80])
ok("同秒按 id 兜底 (id 大的更新)", re.search(r"\(\+b\.id \|\| 0\) - \(\+a\.id \|\| 0\)", note_list)
   or re.search(r"\(\+b\.id\) - \(\+a\.id\)", note_list), "")
ok("排序不就地改 S.notes (还能按位置画高亮)", "S.notes.slice()" in note_list, "")
ok("列表项带时间 (看得出先后)", "whenText(" in note_list, "")
ok("书签列表没被顺手改顺序 (它是按章节位置)",
   ".sort(" not in block("renderMarkList"), "")

# ---------------------------------------------------------------- 2. 点划线 → 管理卡
ok("有点击点 → 字节偏移的换算", "function byteAtPoint" in JS and "caretRangeFromPoint" in JS)
ok("有按字节区间找划线 (交叠取最短)",
   "function noteAtPoint" in JS and "end_off - +best.start_off" in JS.replace(" ", " ")
   or "function noteAtPoint" in JS)
_click = JS[JS.find("mainEl.addEventListener('click'"):]
_click = _click[:_click.find("\n  });") + 6]
ok("点击处理里先认划线再关浮层",
   _click.find("var hit = noteAtPoint(") > 0
   and _click.find("var hit = noteAtPoint(") < _click.find("if (dismissPopups()) return;"), "")

ok("不支持 Highlight API 时认 <mark> 元素", "closest('mark.rv-mark')" in JS)
ok("管理卡有删除按钮", "data-nm=\"del\"" in JS and "action: 'delete'" in JS)
ok("管理卡有编辑入口 (转给笔记编辑框)", "data-nm=\"edit\"" in JS and "openNoteEdit(null, note)" in JS)
ok("管理卡显示划线正文/批注/时间",
   "rv-nm-q" in JS and "rv-nm-r" in JS and "rv-nm-m" in JS)
ok("同一处再点一次 = 收起", "function showNoteManageAt" in JS and "closeNoteManage(); return;" in JS)
ok("点别处会关掉管理卡", "if (noteManage && !noteManage.hidden) { closeNoteManage(); hit = true; }" in JS)
ok("管理卡样式存在 (桌面浮层 / 手机底部卡片)",
   ".rv-nmanage {" in CSS and ".rv-nmanage.is-sheet" in CSS)

# ---------------------------------------------------------------- 3. 查询 / 搜索
ok("工具条有「查询」", 'data-sel="dict">查询<' in PY, "")
ok("工具条有「搜索」", 'data-sel="web">搜索<' in PY, "")
ok("查询走自家后端转发", "api('/lookup?q=' + encodeURIComponent(" in JS)
ok("后端有 /lookup 路由", re.search(r'rest\[:1\] == \["lookup"\]', PY) is not None)
ok("后端路由调 dict.lookup", "dict as _dict" in PY and "_dict.lookup(" in PY, "")
ok("查词卡片有出处/关闭/网页搜索出口",
   "rv-lk-src" in JS and "data-lk=\"close\"" in JS and "data-lk=\"web\"" in JS)
ok("查不到时给一句人话 + 搜索出路", "词典里没查到这个说法" in JS)
ok("搜索走 bing", "cn.bing.com/search?q=" in JS, "")
ok("搜索用隐形 <a target=_blank> 触发 (不靠 window.open 返回值)",
   "a.target = '_blank'" in JS and "window.open(url, '_blank', 'noopener')" not in JS)
ok("选了词就收掉工具条与选区", "removeAllRanges()" in block("doLookup") or
   JS.count("removeAllRanges()") >= 3, JS.count("removeAllRanges()"))
ok("点正文会关掉查词卡片", "if (lookupEl && !lookupEl.hidden) { closeLookup(); hit = true; }" in JS)
ok("查词卡片有样式 (含手机不越界)",
   ".rv-lookup {" in CSS and "min(26rem, 92vw)" in CSS)
sel_css = CSS[CSS.find(".rv-sel {"):CSS.find(".rv-sel button")]
ok("工具条宽度按内容收放 (6 个按钮不被折成三行)", "width: max-content" in sel_css, sel_css[:60])
ok("手机端工具条收窄 (仍是一行)", "@media (max-width: 620px)" in CSS
   and ".rv-sel button { font-size: .76rem" in CSS)

# ---------------------------------------------------------------- 词典服务
from recto.services import dict as D  # noqa: E402

ok("查词服务在 (services/dict.py)", hasattr(D, "lookup"))
ok("用开放接口, 不带 key", "dict.youdao.com" in open(
    os.path.join(ROOT, "src", "recto", "services", "dict.py"), encoding="utf-8").read())

failed = [c for c in checks if not c[1]]
for name, good, extra in checks:
    print(("  ok   " if good else "  FAIL ") + name + (f"   [{extra}]" if extra and not good else ""))
print(f"\n{len(checks) - len(failed)} passed / {len(failed)} failed")
sys.exit(1 if failed else 0)
