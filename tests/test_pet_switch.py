"""书宠总开关 + 正文版心居中 (2026-11-23)。

用户两条口径:
  1. 书宠自己走的路 (前台读本) 正文要**和「按回阅读」一样居中**
  2. 设置页给一个「书宠开关」, 一键 disable 掉**所有**书宠相关的内容

第 1 条的根因: 一屏版把 `.adv-read-body p` 的 `margin: 0 0 .68em` (简写) 写在
基础规则之后, 把 `margin-left/right: auto` 一起清成了 0 → 正文贴着窗口左缘,
而旁注 (`.adv-inline`) 却是 `auto` 居中的, 两者左缘错开一格。

第 2 条的落点 (关掉之后):
  · 顶栏「书宠」入口不出现
  · `/pet` `/pet/story` 回书架; `/pet/api/*` 返 404
  · 阅读器右下角小图标不出现 (`pet-brief` 回 ok=false)
  · 陪读心跳 / 后台推演都空转 (不记时长、不判成长、不烧 token)
  · 语料遭遇也不再记 (读者读书照旧)
存档与已写的游历**不删** —— 再打开接着走。

跑法: python3 tests/test_pet_switch.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = Path(tempfile.mkdtemp(prefix="recto-petswitch-"))
os.environ["RECTO_DATA_DIR"] = str(TMP / "data")
os.environ["RECTO_LIBRARY_DIR"] = str(TMP / "library")
os.environ["RECTO_CLOUD_AUTH_FILE"] = str(TMP / "auth.json")
os.environ["RECTO_PET_DIR"] = str(TMP / "data" / "pet")
os.environ["RECTO_AUTH_LOG"] = "off"
os.environ["RECTO_AUTH_USER"] = "root"
os.environ.pop("RECTO_AUTH_PASSWORD", None)
os.environ.pop("RECTO_AUTH_DISABLE", None)
os.environ.pop("RECTO_PET_ENABLED", None)
(TMP / "library").mkdir(parents=True, exist_ok=True)

from recto.app import pet as petapp, settings_page          # noqa: E402
from recto.app.ui import shell                              # noqa: E402
from recto.core import settings as st                       # noqa: E402
from recto.pet import chronicle, companion                  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def raises(fn) -> str:
    try:
        fn()
        return ""
    except Exception as e:      # noqa: BLE001
        return f"{type(e).__name__}: {e}"


class FakeH:
    """最小的 Handler 替身 —— 只记"被回成了什么"。"""

    def __init__(self, path: str, command: str = "GET"):
        self.path = path
        self.command = command
        self.code = None
        self.payload = None
        self.sent = None
        self.redirect_to = None

    def _json(self, code, payload):
        self.code, self.payload = code, payload

    def _send(self, code, body, ctype=""):
        self.code, self.sent = code, (body, ctype)

    def _redirect(self, url):
        self.redirect_to = url

    def _read_json(self):
        return {}


print("== 1. 开关本身 (默认 / 设置 / 环境变量) ==")
check("默认是开", st.pet_enabled() is True)
check("默认来源是 config", st.pet_source() == "config")
st.update(pet_enabled="off")
check("存 off → 关", st.pet_enabled() is False)
check("来源变 settings", st.pet_source() == "settings")
st.update(pet_enabled="on")
check("存 on → 开", st.pet_enabled() is True)
st.update(pet_enabled="")
check("清空 → 回到默认开", st.pet_enabled() is True)
check("清空后来源回 config", st.pet_source() == "config")
st.update(pet_enabled="off")
os.environ["RECTO_PET_ENABLED"] = "1"
check("环境变量压过设置页", st.pet_enabled() is True)
check("来源标成 env", st.pet_source() == "env")
os.environ["RECTO_PET_ENABLED"] = "0"
st.update(pet_enabled="on")
check("环境变量 0 也能压过设置页的 on", st.pet_enabled() is False)
os.environ.pop("RECTO_PET_ENABLED")
st.update(pet_enabled="")
check("坏值当没设 (按开)", True)
st.update(pet_enabled="maybe")
check("坏值不报错, 按开处理", st.pet_enabled() is True)
st.update(pet_enabled="")

print("== 2. 顶栏入口 ==")
nav_on = shell.nav_html("pet")
check("开着时顶栏有书宠", "书宠" in nav_on and "/pet/" in nav_on)
st.update(pet_enabled="off")
nav_off = shell.nav_html("")
check("关着时顶栏没有书宠", "书宠" not in nav_off)
check("关着时也没 /pet/ 链接", "/pet/" not in nav_off)
check("别的入口照旧在", "书架" in nav_off and "云端同步" in nav_off and "语料" in nav_off)
check("pet.pet_on() 跟着关", petapp.enabled() is False)

print("== 3. /pet 分区整个不对外 ==")
h = FakeH("/pet/api/char")
check("接口被 dispatch 接住 (不是 404 交给下游)", petapp.dispatch(h) is True)
check("接口 404", h.code == 404)
check("错误码说清楚是关掉了", (h.payload or {}).get("error") == "pet_disabled")
h = FakeH("/pet/")
check("页面也接住", petapp.dispatch(h) is True)
check("页面回书架", h.redirect_to == "/#library")
check("页面没被当成 HTML 发出去", h.sent is None)
h = FakeH("/pet/story/")
petapp.dispatch(h)
check("档案页同样回书架", h.redirect_to == "/#library")
h = FakeH("/pet/static/pet.css")
petapp.dispatch(h)
check("静态资产也不给", h.redirect_to == "/#library" and h.sent is None)

st.update(pet_enabled="on")
h = FakeH("/pet/api/presets")
petapp.dispatch(h)
check("打开后接口恢复", h.code == 200)
h = FakeH("/pet/")
petapp.dispatch(h)
check("打开后页面恢复", h.code == 200 and h.sent is not None)
st.update(pet_enabled="off")

print("== 4. 阅读器右下角小图标 ==")
src = (ROOT / "src" / "recto" / "app" / "reader.py").read_text("utf-8")
check("pet-brief 先问开关", "if not _pet.enabled():" in src)
check("关掉时直接回 ok=false", '"ok": False, "enabled": False' in src)
check("关掉时不再去查库", src.index('"ok": False, "enabled": False') < src.index("payload = brief.latest()"))
check("语料遭遇也不记", "if not _pet.enabled():\n            return" in src)

print("== 5. 两个心跳都空转 ==")
check("陪读心跳看得见开关", companion._enabled() is False)
check("后台推演看得见开关", chronicle._enabled() is False)
st.update(pet_enabled="on")
check("打开后陪读心跳恢复", companion._enabled() is True)
check("打开后后台推演恢复", chronicle._enabled() is True)
st.update(pet_enabled="off")
csrc = (ROOT / "src" / "recto" / "pet" / "companion.py").read_text("utf-8")
ksrc = (ROOT / "src" / "recto" / "pet" / "chronicle.py").read_text("utf-8")
check("陪读心跳里真的挡了 (不是只定义函数)", 'if not _enabled():' in csrc)
check("后台推演里真的挡了", 'if not _enabled():' in ksrc)
check("关掉时不跑 run()", ksrc.index('if not _enabled():') < ksrc.index('out = run(trigger="tick")'))

print("== 6. 设置页: 开关 + 现状 ==")
page = settings_page.page_html("root")
check("有书宠卡", 'id="pet"' in page)
check("有开关控件", 'id="pet-on"' in page)
check("开关状态跟设置一致 (关 → 没 checked)", 'id="pet-on" checked' not in page)
check("卡头标已关闭", 'id="pet-pill">已关闭' in page)
check("有出处说明", "id" in page and "pet-src" in page)
check("有提示区", 'id="pet-msg"' in page)
st.update(pet_enabled="on")
page = settings_page.page_html("root")
check("打开后开关是选中的", 'id="pet-on" checked' in page)
check("卡头标已启用", 'id="pet-pill">已启用' in page)
summary = settings_page.summary()
check("summary 带 pet 段", isinstance(summary.get("pet"), dict))
check("summary 报得对", summary["pet"]["enabled"] is True)
check("summary 带来源文案", summary["pet"]["source_label"] in
      (st.PET_SOURCE_LABEL[_k] for _k in ("env", "settings", "config")))
check("summary 有分钟/回数两栏", "minutes" in summary["pet"] and "cycles" in summary["pet"])

print("== 7. 设置页接口 ==")
r = settings_page.api_post({"pet_enabled": "off"})
check("接口能关", r["pet"]["enabled"] is False)
check("接口回报改了哪一项", r["changed"]["pet"] is True)
check("关掉后现状不读存档 (分钟显示 0)", r["pet"]["minutes"] == 0)
r = settings_page.api_post({"pet_enabled": "on"})
check("接口能开", r["pet"]["enabled"] is True)
r = settings_page.api_post({"pet_enabled": ""})
check("空 = 回到默认开", r["pet"]["enabled"] is True)
check("空值后来源回 config", r["pet"]["source"] == "config")
check("坏值被拒", "on / off" in raises(lambda: settings_page.api_post({"pet_enabled": "也许"})))
settings_page.api_post({"pet_enabled": "on"})

print("== 8. 正文版心居中 (一屏读本 vs 按回阅读) ==")
css = (ROOT / "src" / "recto" / "app" / "ui" / "assets" / "pet.css").read_text("utf-8")
scss = (ROOT / "src" / "recto" / "app" / "ui" / "assets" / "story.css").read_text("utf-8")
plain = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
# 一屏版那一段: `... .adv-read-body > p { ... }`
m = re.search(r"body\.rc-pet-page:not\(\.rc-pet-story-page\) \.adv-read-body > p \{([^}]*)\}", plain)
check("找到一屏版的正文规则", m is not None)
body = m.group(1) if m else ""
check("版心 34em", "max-width: 34em" in body)
check("左边 auto", "margin-left: auto" in body)
check("右边 auto", "margin-right: auto" in body)
m2 = re.search(r"body\.rc-pet-page:not\(\.rc-pet-story-page\) \.adv-read-body \.adv-inline \{([^}]*)\}", plain)
check("旁注版心跟着收 (左缘对齐)", m2 is not None and "34em" in m2.group(1))
check("档案页正文本来就是居中的", "margin: 0 auto .8em" in scss)

print()
print(f"PASS {PASS} · FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
