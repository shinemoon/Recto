"""书宠 · 读故事 (逐段推进) 与阅读位置的持久化。

跑法: cd <仓库根> && python3 tests/test_pet_reading.py

验的事:
  * 阅读位置存在**独立文件** (readpos.json), 不跟推演状态 (chronicle.json) 混在一起
    —— 混在一起会被推演那一回合的长快照覆盖掉
  * 记位置 / 读位置 / 全量位置 / 清位置
  * 一次跳太远会被夹住 (防手滑/脚本)
  * 往回写是允许的 (读者回头重读) —— 但页面那侧只前进
  * 复位时位置一起清掉
  * 原子写的临时名带线程 id (同进程两个线程写同一个文件不会撞车)
  * 页面契约: 读本 / 抽屉 / 三个子页签
"""
from __future__ import annotations

import json
import os
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

# ── 2. 记 / 读 ───────────────────────────────────────────────────
ok(reading.get(29)["para"] == 0, "没读过 → 0 段")
r = reading.set_pos(29, 3)
ok(r["para"] == 3 and r["at"], f"记下位置 got {r}")
ok(reading.get(29)["para"] == 3, "读回位置")
ok(reading.all_positions() == {"29": 3}, f"全量位置 got {reading.all_positions()}")

# 不同回各记各的
reading.set_pos(30, 7)
ok(reading.get(29)["para"] == 3 and reading.get(30)["para"] == 7, "各回独立")

# 往回写是允许的 (读者回头重读)
reading.set_pos(29, 1)
ok(reading.get(29)["para"] == 1, "往回写也认 (回头重读是正常动作)")

# ── 3. 一次跳太远 → 夹住 ─────────────────────────────────────────
reading.set_pos(29, 0)
reading.set_pos(29, 9999)
got = reading.get(29)["para"]
ok(got <= reading.MAX_JUMP, f"一次跳太远被夹住 got {got}")

# ── 4. 坏输入不炸 ────────────────────────────────────────────────
ok(reading.set_pos("x", "y")["para"] >= 0, "坏输入不炸")
ok(reading.get("x")["para"] == 0, "坏回号 → 0")

# ── 5. 并发写不撞车 ──────────────────────────────────────────────
# 同一个进程两个线程同时写 —— 临时名必须不同 (只带 pid 会撞车:
# 一个 os.replace 走之后, 另一个的 os.replace 找不到文件 → 抛错)
errors: list[str] = []


def writer(base: int) -> None:
    for i in range(25):
        try:
            reading.set_pos(100 + base, i)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{type(e).__name__}: {e}")


ts = [threading.Thread(target=writer, args=(k,)) for k in range(4)]
for t in ts:
    t.start()
for t in ts:
    t.join()
ok(not errors, f"四线程并发写不撞车 ({errors[:2]})")
ok(reading.get(100)["para"] >= 0, "并发写后位置还在")

# 临时名带线程 id
src = (ROOT / "src/recto/pet/reading.py").read_text("utf-8")
ok("threading.get_ident()" in src, "临时名带线程 id")
ok("fcntl" in src and "flock" in src, "读-改-写用文件锁圈住")

# ── 6. 复位时清位置 ──────────────────────────────────────────────
reading.set_pos(29, 5)
from recto.pet import reborn  # noqa: E402

reborn._clear_story()  # noqa: SLF001 — 复位的那一步
ok(reading.all_positions() == {}, f"复位清掉位置 got {reading.all_positions()}")

# ── 7. 页面契约 ──────────────────────────────────────────────────
from recto.app import pet as _petapp  # noqa: E402

html = _petapp.page_html()
ok('id="pg-drawer"' in html, "页面有人物抽屉")
ok('class="pg-ico-rail"' in html, "页面有图标入口")
ok('data-pg="hero"' not in html, "「人物」不再是子页签")
ok(html.count('<div class="pg-pane') == 3, "三个子页签 (故事 / 世界 / 设置)")
ok('id="adv-read"' in html and 'id="adv-read-body"' in html, "页面有读本")
ok('id="btn-adv-next"' in html, "读本有推进按钮")
ok(html.count("<div") == html.count("</div>"), "div 标签配对")
ok(html.count("<section") == html.count("</section>"), "section 标签配对")
ok(html.count("<details") == html.count("</details>"), "details 标签配对")

js = _petapp._UI_DIR.joinpath("pet.js").read_text("utf-8")
ok("function readNext" in js, "前端有推进函数")
ok("'Space'" in js, "空格键推进")
ok("/api/reading" in js, "前端调阅读位置接口")
ok("function drawerShow" in js, "前端有抽屉开关")

css = _petapp._UI_DIR.joinpath("pet.css").read_text("utf-8")
ok(".pg-drawer-panel" in css, "抽屉有样式")
ok(".adv-read-body" in css, "读本有样式")
ok(css.count("{") == css.count("}"), "css 花括号配对")

print()
print("=" * 46)
print("PASS", PASS, " FAIL", FAIL)
sys.exit(1 if FAIL else 0)