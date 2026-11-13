"""书宠推演的两个坑: 推理模型 token 预算 / 保底原因可见 / 窗口解释。

背景 (2026-11-12 用户报障):
  1. 配了 MiniMax-M3 (推理型, 测试连通正常), 手动点「现在推演一回」→ 反馈"模型不可用"。
     真因: 判定契约只要 700 max_tokens, 思考把它吃光 → 正文空 → LLMTruncated →
     lore.chat 返回 None → 一律说"模型不可用", 用户无从知道该动哪个参数。
  2. 明明读了一整天, 手动推演却说"没有翻书"。真因: 窗口 = 上次推演之后,
     1.5 分钟前的定时回合刚把 37 分钟结算掉了。

跑法: cd <仓库根> && python3 tests/test_pet_llm_budget.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = Path(tempfile.mkdtemp(prefix="recto-petllm-"))
os.environ["RECTO_DATA_DIR"] = str(TMP / "data")
os.environ["RECTO_LIBRARY_DIR"] = str(TMP / "library")
os.environ["RECTO_CLOUD_AUTH_FILE"] = str(TMP / "auth.json")
os.environ["RECTO_PET_DIR"] = str(TMP / "data" / "pet")
os.environ["RECTO_AUTH_LOG"] = "off"
os.environ["RECTO_PET_LLM_BORROW"] = "off"
(TMP / "library").mkdir(parents=True, exist_ok=True)

from recto.core import llm as llmmod  # noqa: E402
from recto.pet import charsheet as cs  # noqa: E402
from recto.pet import chronicle as ch  # noqa: E402
from recto.pet import config as petcfg  # noqa: E402
from recto.pet import growth as g  # noqa: E402
from recto.pet import jsonout, lore  # noqa: E402

PASS = FAIL = 0


def ok(cond, name, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS", name)
    else:
        FAIL += 1
        print("FAIL", name, extra)


def eq(a, b, name):
    ok(a == b, name, f"got {a!r} != {b!r}")


# ---------------------------------------------------------------- 配置夹具
cfg = petcfg.load_config()
cfg["llm"] = {"provider": "openai", "base_url": "https://api.minimaxi.com/v1",
              "api_key": "sk-test", "model": "MiniMax-M3", "timeout": 60,
              "max_tokens": 1200, "temperature": 0.8, "thinking": "off",
              "strict_no_reasoning": False}
petcfg.save_config(cfg)

# ---------------------------------------------------------------- 1 推理预算
captured = {}


def fake_chat(c, system, user, **kw):
    captured.update({"mt": kw.get("max_tokens"), "retries": kw.get("retries")})
    return '{"ok": 1}'


_real = llmmod.chat
llmmod.chat = fake_chat
try:
    out = lore.chat("sys", "user", max_tokens=700)
    eq(out, '{"ok": 1}', "假模型正常返回")
    eq(captured.get("mt"), 2100, "推理模型 (MiniMax-M3): 700 → 2100 (3 倍)")

    cfg2 = petcfg.load_config()
    cfg2["llm"]["model"] = "some-plain-chat-model"
    petcfg.save_config(cfg2)
    lore.chat("sys", "user", max_tokens=700)
    eq(captured.get("mt"), 700, "普通对话模型: 预算原样 (不为它多花 token)")

    cfg3 = petcfg.load_config()
    cfg3["llm"]["model"] = "MiniMax-M3"
    petcfg.save_config(cfg3)
    lore.chat("sys", "user", max_tokens=2000)
    eq(captured.get("mt"), 4096, "推理模型预算封顶 4096")
finally:
    llmmod.chat = _real

# ---------------------------------------------------------------- 2 保底原因
def boom(c, system, user, **kw):
    raise llmmod.LLMTruncated("输出被截断 (max_tokens=2100)")


llmmod.chat = boom
try:
    lore.chat("sys", "user", max_tokens=700)
    ok("LLMTruncated" in lore.LAST_ERROR, "失败原因被记进 LAST_ERROR", lore.LAST_ERROR)
    ok("截断" in lore.LAST_ERROR, "原因里带人话 (截断)")

    ev = {"window": {"from": "2026-11-12T18:26:56", "to": "2026-11-12T18:28:25",
                     "hours": 0.1},
          "reading": [], "total_minutes": 0, "days": 0,
          "encounters": {"terms": [], "cats": {}, "days": 2}}
    j = ch.judge(ev, cs.load_char())
    ok(j.get("_fallback") is True, "模型失败 → 走保底规则")
    ok("截断" in str(j.get("_fallback_why")), "保底判词写明真因 (不再是笼统的'模型不可用')",
       j.get("_fallback_why"))
    ok("模型这一步没成" in (j.get("growth") or {}).get("reason", ""),
       "判词 reason 里也带上原因")
finally:
    llmmod.chat = _real

# 成功一次后 LAST_ERROR 要清空
llmmod.chat = lambda c, s, u, **kw: '{"assessment": {}, "growth": {"exp": 1}}'
try:
    j = ch.judge({"window": {"from": "", "to": "", "hours": 1}, "reading": [
        {"book": "b", "title": "t", "minutes": 10, "chapters": 0, "notes": 0,
         "bookmarks": 0, "percent_from": 1.0, "percent_to": 1.0}],
        "total_minutes": 10, "days": 0,
        "encounters": {"terms": [], "cats": {}, "days": 2}}, cs.load_char())
    eq(lore.LAST_ERROR, "", "成功一次后 LAST_ERROR 清空")
    ok(j.get("_fallback") is False, "成功路径不标保底")
finally:
    llmmod.chat = _real

# ---------------------------------------------------------------- 3 累计 ≠ 本回合
brief = ch._char_brief({"minutes": 677, "level": 12})
ok("lifetime_minutes" in brief, "_char_brief 用 lifetime_minutes")
ok("minutes" not in brief, "不再暴露裸 minutes (模型会当本回合基数)",
   list(brief.keys()))
eq(brief.get("lifetime_minutes"), 677, "lifetime_minutes 是累计值")

# ---------------------------------------------------------------- 4 窗口解释
from recto.core import clock as _clock     # 别把"今天"写死 —— 隔天就跑红了
_today = _clock.fmt_sec(time.time(), "%Y-%m-%d")
st = {"last_at": "2026-11-12T18:26:56", "story_day": 11,
      "credit": {f"x|{_today}": 51, "x|2020-01-01": 29},
      "progress": {}}
ev = ch.gather(st)
ok("already_today" in ev, "gather 返回 already_today")
eq(ev["already_today"], 51, "已计入只数今天那部分 (51, 不含昨天 29)")
eq(ev["prev_at"], "2026-11-12T18:26:56", "gather 带回上一回时间")

# 返回体里五个出口都带 already_today / prev_at
src = (ROOT / "src" / "recto" / "pet" / "chronicle.py").read_text()
ok(src.count("**ctx") >= 5, "五个返回出口都带上 ctx", src.count("**ctx"))
ok('"fallback_why"' in src, "返回体暴露 fallback_why")

# ---------------------------------------------------------------- 5 前端文案
js = (ROOT / "src" / "recto" / "app" / "ui" / "assets" / "pet.js").read_text()
ok("function windowNote" in js, "前端有 windowNote (窗口/已计入 一句话)")
ok("今天已计入 " in js, "前端显示今天已计入多少分钟")
ok("模型这一步没成" in js, "前端 fallback 文案不再是笼统的'模型不可用'")
ok("fallback_why" in js, "前端读 fallback_why")

# ---------------------------------------------------------------- 6 规则文档
md = (ROOT / "src" / "recto" / "pet" / "skills" / "chronicle.md").read_text()
ok("lifetime_minutes" in md, "契约文档提醒 lifetime_minutes 不可当基数")
ok("思考要短" in md, "契约文档提醒推理模型别想太久")

print()
print("=" * 46)
print("PASS", PASS, " FAIL", FAIL)
sys.exit(1 if FAIL else 0)
