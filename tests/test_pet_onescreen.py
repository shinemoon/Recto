"""书宠 · 阅读界面「一屏装下 + 可见滚动条」(2026-11-21)。

用户口径: 「书宠的阅读界面我希望一个屏幕可以装下, 即尽量 compact,
然后页面要有 scrollbar」。

之前的样子 (1280×900 实测): 页面总高 1535px —— 视口 900, 得滚掉 635px
才够看正文; 正文窗口上方堆着 325px 的"壳"; 滚动条还看不见 (Chromium
默认 overlay 滚动条, 不占位)。

改法:
  * 页面变成 flex 列, 高 = 100vh → 页面**不滚**, 滚的是正文窗口
  * 壳压扁: HUD 一行 / 页签与图标栏并成一行 / 卡头与行动行压扁
  * 正文窗口吃剩余高度 (flex), 滚动条**占位式** (10px) 且看得见
  * 诊断信息收进折叠块 (默认收起), 展开时自己滚, 不挤扁正文窗口
  * 窄屏 (<= 860px) 退回"页面可滚", 窗口仍固定高 + 可见滚动条
  * 档案页 (/pet/story/) 不受一屏布局影响

这里钉**契约** (CSS/HTML 里必须有的东西); 真实行为由
tools/probe_pet_onescreen.mjs 在真浏览器里量。
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = Path(tempfile.mkdtemp(prefix="recto-petonescreen-"))
os.environ["RECTO_DATA_DIR"] = str(TMP / "data")
os.environ["RECTO_LIBRARY_DIR"] = str(TMP / "library")
os.environ["RECTO_CLOUD_AUTH_FILE"] = str(TMP / "auth.json")
os.environ["RECTO_PET_DIR"] = str(TMP / "data" / "pet")
os.environ["RECTO_AUTH_LOG"] = "off"
os.environ["RECTO_AUTH_PASSWORD"] = "pet-pass"
os.environ.pop("RECTO_AUTH_DISABLE", None)
(TMP / "library").mkdir(parents=True, exist_ok=True)
(TMP / "auth.json").write_text('{"usr":"i0","zysid":"t","p29":"z","p2":"1",'
                                '"p3":"1","p4":"1"}', "utf-8")

from recto.app import pet as _petapp  # noqa: E402

PASS = FAIL = 0


def ok(cond, name):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS", name)
    else:
        FAIL += 1
        print("FAIL", name)


css = _petapp._UI_DIR.joinpath("pet.css").read_text("utf-8")
html = _petapp.page_html()
arch_html = _petapp.story_page_html()

# ── 1. 一屏: 页面自己不滚 ─────────────────────────────────────────
ok("body.rc-pet-page:not(.rc-pet-story-page) { overflow: hidden; }" in css,
   "书宠页页面自身锁住不滚")
ok("body.rc-pet-page:not(.rc-pet-story-page) .rc-shell { height: 100vh" in css,
   "外壳高度 = 视口高度 (不是 min-height)")
ok("body.rc-pet-page:not(.rc-pet-story-page) .rc-main" in css
   and "display: flex; flex-direction: column;" in css,
   "主列是 flex 列 (内部自己分区)")
ok(".rc-foot { display: none; }" in css, "页脚藏了 (一屏装不下它)")

# ── 2. 壳压扁 ─────────────────────────────────────────────────────
ok(".pg-toprow" in css, "页签与图标栏并成一行 (pg-toprow)")
ok('class="pg-toprow"' in html, "页面有 pg-toprow 包裹层")
ok(html.index('class="pg-toprow"') < html.index('id="pg-ico-rail"'),
   "pg-toprow 包住页签与图标栏")
ok(".pg-bar {" in css and "grid-template-areas: \"face id gauges chips\"" in css,
   "HUD 压成一行 (脸/名/量条/chips)")
ok(".pg-chips { gap: 4px; flex-wrap: nowrap; }" in css
   or "flex-wrap: nowrap" in css,
   "HUD 的 chip 不再换行")

# ── 3. 正文窗口吃剩余高度 ─────────────────────────────────────────
ok(".adv-read {\n" in css or ".adv-read {" in css, "读本有样式")
ok("flex: 1 1 auto" in css, "读本吃剩余高度 (flex)")
ok("min-height: 320px" in css,
   "读本有高度底线 (展开诊断块时不至于被挤成一条缝)")
ok("height: auto;" in css,
   "正文窗口高度由 flex 给, 不再用 vh 猜")

# ── 4. 滚动条看得见 (占位式) ──────────────────────────────────────
ok("scrollbar-gutter: stable" in css,
   "滚动条占位式 (不遮内容, 也不忽隐忽现)")
ok("overflow-y: scroll" in css,
   "正文窗口一直占着滚动条 (不会忽然消失)")
ok("::-webkit-scrollbar-thumb" in css, "自己画了滚动条的滑块")
ok("::-webkit-scrollbar-track" in css, "滚动条有轨道")
ok("scrollbar-color:" in css, "Firefox 侧也给了颜色")
# 滚动条必须**看得见** —— 滑块的 background 不能是**纯** transparent。
# (`color-mix(in srgb, var(--rc-fg) 22%, transparent)` 是半透明前景色 —— 看得见;
#  `border: 3px solid transparent` 是标准细滑块技巧, 不算)
thumb_blocks = re.findall(
    r"::-webkit-scrollbar-thumb[^{]*\{([^}]*)\}", css)
ok(thumb_blocks, "有滚动条滑块规则")


def _bg_of(block: str) -> str:
    m = re.search(r"background(?:-color)?\s*:\s*([^;]+)", block)
    return (m.group(1).strip().lower() if m else "")


ok(all(_bg_of(b) and _bg_of(b) != "transparent" for b in thumb_blocks),
   "滑块背景不是纯 transparent (看得见)")
ok(any("color-mix" in _bg_of(b) for b in thumb_blocks),
   "滑块用前景色调 (深浅皮肤都看得见)")

# ── 5. 诊断信息收进折叠块 (不许消失) ──────────────────────────────
ok('id="pet-diag"' in html, "诊断块在页面里")
ok(html.index('id="pet-diag"') < html.index('id="chronicle-state"'),
   "诊断块包住状态行")
ok("id=\"chronicle-state\"" in html, "状态行还在 (没被删)")
ok("id=\"encounter-chips\"" in html, "语料遭遇还在")
ok('id="adventure-latest"' in html, "「已经走了 N 段」还在")
ok(".pg-diag-body" in css, "诊断块内容有自己的滚动容器")
ok("max-height: 200px" in css, "诊断块限高 (展开时不挤扁正文窗口)")
ok("overflow-y: auto" in css, "诊断块内容自己滚")

# ── 6. 窄屏退回"页面可滚" ─────────────────────────────────────────
mob = css.split("@media (max-width: 860px) {", 1)
ok(len(mob) == 2, "有窄屏断点")
mob_css = mob[1]
ok("body.rc-pet-page:not(.rc-pet-story-page) { overflow: auto; }" in mob_css,
   "窄屏页面可滚 (一屏装不下那么多)")
ok("height: 56vh" in mob_css, "窄屏正文窗口仍是固定高")

# ── 7. 档案页不受影响 ─────────────────────────────────────────────
ok("rc-pet-story-page" in css, "一屏规则排除了档案页")
ok(":not(.rc-pet-story-page)" in css, "一屏规则用 :not() 排除档案页")
ok('id="archive-list"' in arch_html, "档案页仍有归档列表")

# ── 8. 标签配对 (折叠块是新的 HTML 结构) ──────────────────────────
ok(html.count("<div") == html.count("</div>"), "div 标签配对")
ok(html.count("<section") == html.count("</section>"), "section 标签配对")
ok(html.count("<details") == html.count("</details>"), "details 标签配对")
ok(html.count("<nav") == html.count("</nav>"), "nav 标签配对")

# ── 9. 探针在 (真浏览器量一屏与滚动条) ───────────────────────────
probe = ROOT / "tools" / "probe_pet_onescreen.mjs"
ok(probe.is_file(), "一屏探针在")
if probe.is_file():
    ptxt = probe.read_text("utf-8")
    ok("readProgress" not in ptxt and "writeProgress" not in ptxt,
       "探针用 readPos/writePos 命名 (卫生检查认这两个名字)")
    ok("writePos" in ptxt, "探针会还原阅读位置")
    ok("scrollbar" in ptxt, "探针量滚动条")

print()
print("=" * 46)
print("PASS", PASS, " FAIL", FAIL)
sys.exit(1 if FAIL else 0)
