"""书宠 · 读故事 (一条连续的路) 与阅读位置的持久化。

跑法: cd <仓库根> && python3 tests/test_pet_reading.py

验的事:
  * 阅读位置存在**独立文件** (readpos.json), 不跟推演状态 (chronicle.json) 混在一起
    —— 混在一起会被推演那一回合的长快照覆盖掉
  * 位置记的是**全局段序号** (不分章节: 前台是一条连续的路, 章节在后台)
  * 位置**只前进** (回头重读不抹掉已经读过的地方)
  * 一次跳太远会被夹住 (防手滑/脚本)
  * 老档 (按回记段数) 能迁成全局序号
  * 原子写的临时名带线程 id (同进程两个线程写同一个文件不会撞车)
  * 页面契约: 读本 / 抽屉 / 三个子页签 / 档案页入口
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = Path(tempfile.mkdtemp(prefix="recto-petread-"))
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

from recto.pet import chronicle  # noqa: E402
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


# ── 1. 独立文件 ──────────────────────────────────────────────────
ok(reading.state_path().name == "readpos.json",
   f"阅读位置是独立文件 got {reading.state_path().name}")
ok(reading.state_path() != chronicle.state_path(),
   "与推演状态 (chronicle.json) 不是同一个文件")

# ── 2. 记 / 读 (全局段序号) ───────────────────────────────────────
ok(reading.get()["idx"] == 0, "没读过 → 0 段")
r = reading.set_idx(3)
ok(r["idx"] == 3 and r["at"], f"记下位置 got {r}")
ok(reading.get()["idx"] == 3, "读回位置")

# 往回写**不**认 (位置是"已经读过的最远处" —— 回头重读不该抹掉)
reading.set_idx(1)
ok(reading.get()["idx"] == 3, f"往回写不认 (高水位) got {reading.get()['idx']}")

# force 可以往回写 (迁移老档时用)
reading.set_idx(1, force=True)
ok(reading.get()["idx"] == 1, "force 可以往回写 (迁移用)")

# ── 3. 一次跳太远 → 夹住 ─────────────────────────────────────────
reading.set_idx(0, force=True)
reading.set_idx(9999)
got = reading.get()["idx"]
ok(got <= reading.MAX_JUMP, f"一次跳太远被夹住 got {got}")

# ── 4. 坏输入不炸 ────────────────────────────────────────────────
ok(reading.set_idx("x")["idx"] >= 0, "坏输入不炸")
ok(reading.get()["idx"] >= 0, "读回还是整数")

# ── 5. 并发写不撞车 ──────────────────────────────────────────────
# 同一个进程两个线程同时写 —— 临时名必须不同 (只带 pid 会撞车:
# 一个 os.replace 走之后, 另一个的 os.replace 找不到文件 → 抛错)
errors: list[str] = []


def writer(base: int) -> None:
    for i in range(25):
        try:
            reading.set_idx(base + i)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{type(e).__name__}: {e}")


ts = [threading.Thread(target=writer, args=(k,)) for k in range(4)]
for t in ts:
    t.start()
for t in ts:
    t.join()
ok(not errors, f"四线程并发写不撞车 ({errors[:2]})")
ok(reading.get()["idx"] >= 0, "并发写后位置还在")

# 临时名带线程 id
src = (ROOT / "src/recto/pet/reading.py").read_text("utf-8")
ok("threading.get_ident()" in src, "临时名带线程 id")
ok("fcntl" in src and "flock" in src, "读-改-写用文件锁圈住")

# ── 6. 老档迁移 ───────────────────────────────────────────────────
# 老格式: {"32": {"para": 6, "at": ...}} —— 按回记段数
legacy = {"32": {"para": 6, "at": "2026-11-19 14:49"}}
reading.state_path().write_text(json.dumps(legacy, ensure_ascii=False), "utf-8")
ok(reading.is_legacy(), "认出老格式 (按回记段数)")
ok(reading.legacy_positions() == {"32": 6}, f"老位置读出来 got {reading.legacy_positions()}")

# 造两回假故事 (各 3 段 / 4 段) 验换算
chs = [{"id": 1, "no": 31, "paras": ["a", "b", "c"]},
       {"id": 2, "no": 32, "paras": ["d", "e", "f", "g"]}]
ok(story.legacy_global_index({"32": 6}, chs) == 3 + 4,
   "老位置 → 全局序号 (前面各回的段数 + 本回段序)")

# ── 7. 复位时清位置 ──────────────────────────────────────────────
reading.set_idx(5)
from recto.pet import reborn  # noqa: E402

reborn._clear_story()  # noqa: SLF001 — 复位的那一步
ok(reading.get()["idx"] == 0, f"复位清掉位置 got {reading.get()}")

# ── 8. 故事分段 ───────────────────────────────────────────────────
ok(story.split_paras("a\n\nb\n\nc") == ["a", "b", "c"], "空行分段")
ok(story.split_paras("a\n\n\n\nb") == ["a", "b"], "连续空行也算一次分段")
ok(story.split_paras("") == [], "空正文 → 没有段")
ok(story.split_paras("只有一段") == ["只有一段"], "单段")
ok(story.clean_title("第三十二回 · 桥上的两盏灯") == "桥上的两盏灯",
   "标题剥掉「第N回」")
ok(story.clean_title("桥上的两盏灯") == "桥上的两盏灯", "新标题不动")

flow = story.flatten(chs)
ok(len(flow) == 7, f"摊平成一条连续的段落流 got {len(flow)}")
ok(flow[3]["no"] == 32 and flow[3]["pi"] == 0,
   f"每段带它属于哪一回 got {flow[3]}")

from recto.app import pet as _petapp  # noqa: E402

js = _petapp._UI_DIR.joinpath("pet.js").read_text("utf-8")

# ── 9. MUD 式阅读窗口 (2026-11-20) ─────────────────────────────────
#
# 前台读本改成 MUD 式: 固定高度的窗口 + 窗口内滚动 + 空格翻一屏。
# 这里只钉**契约** (前端/后端代码里必须有的东西); 真实行为由
# tools/probe_pet_mudread.mjs 在真浏览器里量。

css = _petapp._UI_DIR.joinpath("pet.css").read_text("utf-8")
# 窗口高度必须是**固定**的 (height), 不是 max-height ——
# max-height 会让容器随内容长高, 把整个页面撑出滚动条 (要治的毛病)
ok("height: 58vh" in css, "正文窗口用固定高度 (height)")
ok("max-height: 62vh" not in css, "不再用 max-height (会把页面撑高)")
ok("overflow-anchor: none" in css,
   "关掉滚动锚定 (追加内容时浏览器会乱调滚动位置)")
ok("overscroll-behavior: contain" in css,
   "窗口内滚到底不带动页面 (overscroll)")

js = _petapp._UI_DIR.joinpath("pet.js").read_text("utf-8")
ok("function readPageDown" in js, "有翻屏函数")
ok("function readPageUp" in js, "有回看函数")
ok("function readSync" in js, "有滚动同步函数")
# 翻页 = 滚一个视口高 (不是推进一段)
ok("body.clientHeight" in js, "翻屏按视口高度算 (真量, 不猜)")
# **不许**用 scrollIntoView —— 它会连带把页面卷走
# (注释里提到它不算 —— 那是在解释为什么不用)
js_code = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
js_code = re.sub(r"//[^\n]*", "", js_code)
ok("scrollIntoView" not in js_code,
   "不用 scrollIntoView (它会连带把页面卷走)")
# 位置只前进
ok("bi > READER.idx" in js, "位置只前进 (往回看不抹掉已读的)")
# 打开页面**不**推进位置 (否则每开一次就凭空多读几段)
ok("readSync(false, false)" in js, "打开页面不推进位置")
# 后端: clamp 参数
ok("clamp" in _petapp._UI_DIR.joinpath("pet.js").read_text("utf-8"),
   "前端能关掉「一次跳太远就夹住」的闸")
petpy = (ROOT / "src" / "recto" / "app" / "pet.py").read_text("utf-8")
ok('"clamp"' in petpy, "后端认 clamp 参数")

# 后端 set_idx: clamp=False 时不夹
reading.set_idx(0, force=True)
r = reading.set_idx(500, clamp=False)
ok(r["idx"] == 500, f"clamp=False 不夹 (读者真读到了) got {r['idx']}")
# clamp=True: 从**当前**位置算, 一次最多前进 MAX_JUMP 段
reading.set_idx(0, force=True)
r = reading.set_idx(5000, clamp=True)
ok(r["idx"] == reading.MAX_JUMP, f"clamp=True 会夹 got {r['idx']}")

html = _petapp.page_html()
ok("翻一屏" in html, "提示写明「翻一屏」")
ok("PageUp" in html, "提示写明 PageUp 回看")

# 程序化滚动期间位置**不**推进 —— 「从头读」复位那一滚不该被当成
# "读者读到了第 9 段" (真撞过: 复位后位置跳到 9)。
ok("function readLock" in js, "有程序化滚动的位置锁")
ok("lockUntil" in js, "位置锁用时间戳 (scroll 事件是异步的, 布尔开关盖不住)")
ok("readLock()" in js, "复位/打开时加了锁")
ok("clearTimeout(readSaveTimer)" in js,
   "复位时撤掉挂着的存盘定时器 (它写的是复位前的位置)")

# ── 9. 页面契约 ──────────────────────────────────────────────────
html = _petapp.page_html()
ok('id="pg-drawer"' in html, "页面有人物抽屉")
ok('class="pg-ico-rail"' in html, "页面有图标入口")
ok('data-pg="hero"' not in html, "「人物」不再是子页签")
ok(html.count('<div class="pg-pane') == 3, "三个子页签 (故事 / 世界 / 设置)")
ok('id="adv-read"' in html and 'id="adv-read-body"' in html, "页面有读本")
ok('id="btn-adv-next"' in html, "读本有推进按钮")
ok('/pet/story/' in html, "读本上有「按回查阅」的入口 (章节在后台)")
ok('id="adv-history"' not in html, "前台不再有按回摆的往期列表")
ok(html.count("<div") == html.count("</div>"), "div 标签配对")
ok(html.count("<section") == html.count("</section>"), "section 标签配对")
ok(html.count("<details") == html.count("</details>"), "details 标签配对")

# 档案页
arch = _petapp.story_page_html()
ok('id="archive-list"' in arch, "档案页有归档列表")
ok('pet-shared.js' in arch and 'story.js' in arch, "档案页引了共用零件与自己的脚本")
ok(arch.count("<div") == arch.count("</div>"), "档案页 div 标签配对")

ok("function readNext" in js, "前端有推进函数")
ok("'Space'" in js, "空格键推进")
ok("/api/reading" in js, "前端调阅读位置接口")
ok("function drawerShow" in js, "前端有抽屉开关")
ok("'/api/story'" in js, "前端拉那条连续的路")
ok("splitParas" not in js, "前端不再自己分段 (后台切好才发)")
ok("readAutoOpen" not in js, "不再有「自动摊开某一回」的老逻辑")

shared = _petapp._UI_DIR.joinpath("pet-shared.js").read_text("utf-8")
ok("function duelBlock" in shared and "function checksBlock" in shared
   and "function echoBlock" in shared and "function kindBadge" in shared,
   "共用零件里有战报 / 判定 / 来处 / 徽标")
sjs = _petapp._UI_DIR.joinpath("story.js").read_text("utf-8")
ok("/api/story/archive" in sjs, "档案页拉按回的归档")
ok("RCPetUI" in sjs, "档案页用共用零件")

ok(".pg-drawer-panel" in css, "抽屉有样式")
ok(".adv-read-body" in css, "读本有样式")
ok(css.count("{") == css.count("}"), "css 花括号配对")

scss = _petapp._UI_DIR.joinpath("story.css").read_text("utf-8")
ok(".arch-item" in scss, "档案页条目有样式")
ok(scss.count("{") == scss.count("}"), "档案页 css 花括号配对")

print()
print("=" * 46)
print("PASS", PASS, " FAIL", FAIL)
sys.exit(1 if FAIL else 0)