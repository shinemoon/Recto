"""书宠 · 前台改成「最新一回」+ 判定/启发**嵌在正文里** (2026-11-21)。

用户口径:

> 「书虫的首页显示还是改成显示最新的一回吧, 和后面逐回显示唯一区别在于
> 那些所有的判定和启发, 都把它嵌入到对应的正文位置去。注意一下排版和美观。
> 字体也要稍微紧凑一点。然后在按回显示的页面当中, 列表可以选择正序和倒序。」

三件事:
  1. **前台显示最新的一回** —— 不再是"全书摊平成一条连续的路"
  2. **判定 / 启发 / 交手 嵌在正文里对应的那一段** —— 不是收在末尾
  3. **档案页可选正序 / 倒序**

**位置模型跟着改**: 原来是"全局段序号" (全书摊平), 现在前台只显示
最新那一回 —— 全局序号在"新回一出来"时会跳 (读者会看到"读完了"),
所以位置改成 **(回号, 段号)** 一对值。

这里钉**契约** (代码里必须有的东西); 真实行为由
``tools/probe_pet_story_flow.mjs`` 在真浏览器里量。
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = Path(tempfile.mkdtemp(prefix="recto-petlatest-"))
os.environ["RECTO_DATA_DIR"] = str(TMP / "data")
os.environ["RECTO_LIBRARY_DIR"] = str(TMP / "library")
os.environ["RECTO_CLOUD_AUTH_FILE"] = str(TMP / "auth.json")
os.environ["RECTO_PET_DIR"] = str(TMP / "data" / "pet")
os.environ["RECTO_AUTH_LOG"] = "off"
os.environ["RECTO_AUTH_PASSWORD"] = "pet-pass"
os.environ.pop("RECTO_AUTH_DISABLE", None)
(TMP / "library").mkdir(parents=True, exist_ok=True)
(TMP / "auth.json").write_text(json.dumps({
    "usr": "i0000000000", "zysid": "test", "p29": "zye5b814",
    "p2": "104583", "p3": "101010010", "p4": "501603"}), "utf-8")

from recto.app import pet as _petapp  # noqa: E402
from recto.pet import reading  # noqa: E402
from recto.pet import story  # noqa: E402

PASS = FAIL = 0


def ok(cond, name):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS", name)
    else:
        FAIL += 1
        print("FAIL", name)


html = _petapp.page_html()
arch_html = _petapp.story_page_html()
js = _petapp._UI_DIR.joinpath("pet.js").read_text("utf-8")
shared = _petapp._UI_DIR.joinpath("pet-shared.js").read_text("utf-8")
sjs = _petapp._UI_DIR.joinpath("story.js").read_text("utf-8")
css = _petapp._UI_DIR.joinpath("pet.css").read_text("utf-8")
scss = _petapp._UI_DIR.joinpath("story.css").read_text("utf-8")
petpy = (ROOT / "src/recto/app/pet.py").read_text("utf-8")

# ── 1. 前台显示最新的一回 ────────────────────────────────────────
ok('id="/story/latest"' in petpy or '"/story/latest"' in petpy,
   "后端有最新一回的接口")
ok('if path == "/story/latest"' in petpy, "路由认 /story/latest")
ok("story_mod.latest()" in petpy, "接口调 story.latest()")
ok("'/api/story/latest'" in js, "前端拉最新一回")
ok("function loadStory" in js, "有 loadStory")
ok("state.story = v" in js, "前端把结果挂到 state.story")
ok("latest.flow" in js, "前端用后端给好的 flow (含内嵌块)")
# 前台不再是"一条连续的路"
ok("state.story.flow" not in js, "前端不再用全书摊平的 flow")
ok("'/api/story'" not in js, "前端不再拉 /api/story (旧的全书流)")

# ── 2. 判定 / 启发 嵌在正文里 ───────────────────────────────────
ok("def _inline_blocks" in (ROOT / "src/recto/pet/story.py").read_text("utf-8"),
   "后端有内嵌块计算")
ok('"flow": _group_blocks' in (ROOT / "src/recto/pet/story.py").read_text("utf-8"),
   "内嵌块按段落归组")
ok("function readInlineHtml" in js, "前端有内嵌块渲染")
ok("readInlineHtml(item.blocks)" in js, "内嵌块跟着它那一段画")
# 内嵌块必须是正文窗口里的**兄弟节点**, 不是窗口外的收尾
ok("body.appendChild(box)" in js, "内嵌块插进正文窗口")
ok('box.className = \'adv-inline\'' in js, "内嵌块有自己的类名")
# 三种内嵌块的零件
ok("function checkInline" in shared, "零件: 路上的坎")
ok("function sparkInline" in shared, "零件: 化成了 (启发)")
ok("function duelInline" in shared, "零件: 动了手 (交手)")
# 内嵌块必须**轻** —— 一行能读完, 掷骰明细塞 title
ok("title=" in shared.split("function checkInline")[1].split("function sparkInline")[0],
   "判定内嵌把掷骰明细塞进 title (悬停可见)")

# ── 3. 位置 = (回号, 段号) ───────────────────────────────────────
ok("def set_pos" in (ROOT / "src/recto/pet/reading.py").read_text("utf-8"),
   "后端位置记 (回号, 段号)")
ok("VERSION = 3" in (ROOT / "src/recto/pet/reading.py").read_text("utf-8"),
   "存档版本升到 3")
ok('"round": round_no' in (ROOT / "src/recto/pet/reading.py").read_text("utf-8"),
   "存档里有 round")
ok("def migrate_position" in (ROOT / "src/recto/pet/story.py").read_text("utf-8"),
   "老档能迁成 (回号, 段号)")
ok("reading_mod.set_pos" in petpy, "接口用 set_pos")
ok("round: READER.round" in js, "前端存位置带 round")
ok("READER.round" in js, "前端记着当前是第几回")

# ── 4. 这一趟的账 (没嵌进去的那些) ───────────────────────────────
ok('id="adv-account"' in html, "有「这一趟的账」折叠块")
ok("function renderAccount" in js, "有 renderAccount")
ok("RCP.duelBlock(g.duel)" in js, "完整交手战报在「这一趟的账」里")
ok("RCP.echoBlock(g.echo)" in js, "来处在「这一趟的账」里")
ok('id="adv-shown"' in html, "有「这一趟长出来的」")
ok("growth_shown" in js, "「这一趟长出来的」用后端算好的")

# ── 5. 字体紧凑 ──────────────────────────────────────────────────
ok(".adv-read-body p {" in css, "正文有样式")
body_css = css.split(".adv-read-body p {", 1)[1].split("}", 1)[0]
fs = re.search(r"font-size:\s*([0-9.]+)rem", body_css)
ok(fs and float(fs.group(1)) <= 1.0, f"正文字号紧凑 ({fs.group(1) if fs else '?'}rem)")
lh = re.search(r"line-height:\s*([0-9.]+)", body_css)
ok(lh and float(lh.group(1)) <= 1.85, f"行距紧凑 ({lh.group(1) if lh else '?'})")
# 一屏版 (body.rc-pet-page...) 里也要紧
tight = re.search(
    r"body\.rc-pet-page:not\(\.rc-pet-story-page\) \.adv-read-body p \{(.*?)\}", css, re.S)
ok(tight is not None, "一屏版也有正文排版规则")
if tight:
    tfs = re.search(r"font-size:\s*([0-9.]+)rem", tight.group(1))
    ok(tfs and float(tfs.group(1)) <= 0.95,
       f"一屏版正文字号更紧 ({tfs.group(1) if tfs else '?'}rem)")

# ── 6. 档案页: 正序 / 倒序 ──────────────────────────────────────
ok('id="arch-ord-asc"' in arch_html, "档案页有「正序」按钮")
ok('id="arch-ord-desc"' in arch_html, "档案页有「倒序」按钮")
ok('data-ord="asc"' in arch_html and 'data-ord="desc"' in arch_html,
   "两个按钮带 data-ord")
ok("function archSetOrder" in sjs, "有切顺序的函数")
ok("ARCH_ORDER" in sjs, "顺序有状态")
ok("slice().reverse()" in sjs, "倒序 = 反转列表")
ok("archRender()" in sjs, "切顺序后重画")
ok("searchParams.set('ord'" in sjs, "顺序记在 URL (刷新/分享都还在)")
ok(".arch-order" in scss, "排序条有样式")
ok(".arch-order button.is-on" in scss, "选中的那个有样式")

# ── 7. 标签配对 ─────────────────────────────────────────────────
ok(html.count("<div") == html.count("</div>"), "div 标签配对")
ok(html.count("<section") == html.count("</section>"), "section 标签配对")
ok(html.count("<details") == html.count("</details>"), "details 标签配对")
ok(html.count("<span") == html.count("</span>"), "span 标签配对")
ok(arch_html.count("<div") == arch_html.count("</div>"), "档案页 div 标签配对")
ok(arch_html.count("<details") == arch_html.count("</details>"), "档案页 details 配对")

# ── 8. 探针在 ───────────────────────────────────────────────────
probe = ROOT / "tools" / "probe_pet_story_flow.mjs"
ok(probe.is_file(), "最新一回的探针在")
if probe.is_file():
    ptxt = probe.read_text("utf-8")
    ok("readPos(" in ptxt and "writePos(" in ptxt,
       "探针用 readPos/writePos 命名 (卫生检查认这两个名字)")
    ok("round:" in ptxt, "探针读写 (回号, 段号)")
    ok("adv-inline" in ptxt, "探针验内嵌块在正文里")
    ok("arch-ord-desc" in ptxt, "探针验正序 / 倒序")

print()
print("=" * 46)
print("PASS", PASS, " FAIL", FAIL)
sys.exit(1 if FAIL else 0)
