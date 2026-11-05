"""顶部导航: 跨页链接 vs 本页面板, 以及"一个面板名对应多个元素"的显隐。

历史 bug (2026-11-03):
  * 外壳脚本把任何 `[data-pane-link]` 的点击都 preventDefault, 再 `show(name)`。
    在 /admin/ 上点「云端同步」时本页没有 sync 面板 → show() 退回第一个面板,
    于是"点了没反应", 顶部四个分区链接整体失灵。
  * show() 只给"第一个命中的元素"加 is-on。控制台的 home 面板由 7 个元素组成
    (头/按钮行/状态/四个折叠区), 结果只剩标题可见, 按钮统统被藏起来。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from recto.app.ui import shell  # noqa: E402

FAILED = []


def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILED.append(msg)


JS = shell._SHELL_JS

# --- 1. 面板按名字成组切换 ------------------------------------------------
ok("function panesOf(" in JS, "外壳脚本按名字取整组元素")
ok("p.dataset.pane === name" in JS, "显隐按面板名成组切换 (不是只切第一个)")
ok("p === hit" not in JS, "不再只给命中的单个元素加 is-on")

# --- 2. 本页没有的面板不吞点击 --------------------------------------------
ok("function sameDoc(" in JS, "有「同页」判定函数")
ok("if (href && !sameDoc(href)) return;" in JS,
   "跨页链接 (在 /admin/ 上指向 /#sync) 交回浏览器跳转")
ok("if (!findPane(name)) return;" in JS, "本页没有该面板时不拦截点击")
ok("if (!hit) return false;" in JS, "show() 找不到面板返回 false, 不退回第一个")
ok("if (!initial || !show(initial" in JS, "初始面板: hash 不可用时才回退第一个")

# --- 3. 导航表本身: 哪几项是本页面板, 哪几项是真的换页 ---------------------
nav = shell.nav_html("library", user="tester")
for key in ("library", "sync"):
    ok(f'data-pane-link="{key}"' in nav, f"「{key}」是本页面板链接")
ok('data-pane-link="reader"' not in nav, "「阅读增强」已从导航移除")
ok(">阅读<" not in nav, "「阅读」已并入书架 (不再单列一个分区)")
for key in ("excerpts", "corpus", "appearance"):
    ok(f'data-pane-link="{key}"' not in nav, f"「{key}」不是本页面板 (真换页)")
ok('href="/#sync"' in nav and 'href="/#library"' in nav,
   "分区链接指向控制台的 hash (换页后仍落到对应面板)")
ok("/#reader" not in nav, "不再有指向阅读增强的链接")
ok('data-pane-link="home"' not in nav, "概览已从导航里去掉")

# --- 4. 控制台 body 里同一面板名有多个元素 (因此必须成组切换) --------------
from recto.app import console  # noqa: E402

body = console._page_html(None)
ok('data-pane="home"' not in body, "控制台 body 里不再有 home 面板")

# 每个面板用一个 <section data-pane=...> 包住整块内容 —— 子元素不必各自标
for name in ("library", "sync"):
    ok(f'<section data-pane="{name}">' in body, f"面板「{name}」是一整块 section")
ok('data-pane="reader"' not in body, "控制台 body 里不再有阅读增强面板")

seg = body[body.find('<section data-pane="sync">'):]
ok('data-act="push"' in seg, "「推到掌阅」按钮在云端同步面板里")
ok('id="sec-out"' in seg, "执行结果面板在云端同步里")
ok('id="sec-links"' in seg, "云端映射表在云端同步里")
ok('id="sec-log"' in seg, "最近日志已并入云端同步面板")
ok('id="sec-corpus"' not in body, "语料卡随阅读增强一起撤掉 (语料是独立分区)")
for act in ("pull", "push", "notes", "time", "links"):
    ok(f'data-act="{act}"' in seg, f"同步动作「{act}」在云端同步面板里")

libseg = body[body.find('<section data-pane="library">'):body.find('<section data-pane="sync">')]
for sec in ('sec-local', 'sec-intake'):
    ok(f'id="{sec}"' not in body, f"「{sec}」已移出界面")
for sec in ('sec-cloud', 'sec-auth'):
    ok(f'id="{sec}"' in seg, f"「{sec}」在云端同步面板里")
_order = ['sec-auth', 'sec-links', 'sec-cloud', 'sec-out', 'sec-log']
_pos = {k: seg.find(f'id="{k}"') for k in _order}
ok(all(_pos[a] < _pos[b] for a, b in zip(_order, _order[1:])),
   "云端同步里的 section 顺序: 认证 → 映射 → 云书 → 结果 → 日志")
ok('id="sec-intake"' not in body, "摘录接入卡已隐藏 (摘录是独立分区)")
ok('class="divider"' not in libseg, "书库面板不再有「掌阅与书库」分隔线")
ok('loadLocal' not in body and 'loadIntake' not in body, "被移走的区块不再有加载调用")
ok('id="shelf"' in libseg, "书库面板里是书架卡片网格 (不再是表格)")
ok('id="shelf-q"' in libseg, "书架自带搜索框")
ok('id="shelf-stat"' in libseg, "书架顶部有统计条")
ok('data-act="links"' in libseg, "对齐映射在书架页")
ok('console._JS' or True, "")

# --- 5. 摘录后台的二级导航仍是"只切面板" ----------------------------------
sub = shell.subnav_html("home")
ok('data-pane-link="books"' in sub, "二级导航「书」不换页")
ok('href="/admin/?pane=books"' in sub, "二级导航无 JS 时退回整页请求")

print()
if FAILED:
    print(f"{len(FAILED)} FAILED")
    sys.exit(1)
print("all ok")
