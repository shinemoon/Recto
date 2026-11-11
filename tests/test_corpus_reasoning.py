#!/usr/bin/env python3
"""语料: 推理/思考的"放宽"口径 (2026-11-06).

默认 = **放宽**: 思考仍然默认关闭 (抽取第一步就发"别思考"参数), 但端点若根本
关不掉, 就允许它带着思考跑完 (meta["think_off"]="forced"), 不把整轮拦死。
想要"一发现 thinking 就中止"的人, 才勾「严格避免推理」。

本测试钉住:
  1. 默认值: thinking=off / strict_no_reasoning=False (config + analyze 签名)
  2. chat(): 已知"关不掉"的端点 —— 放宽放行 (不抛, 记 forced), 严格抛
  3. _post_with_think_off(): 所有档位被拒 —— 放宽裸发一次并用缓存记住, 严格抛
  4. analyze_epub(): 块里真出现 thinking —— 放宽照跑, 严格 ReasoningAborted
  5. 前端: 勾选框默认不勾, 文案说明"默认关"
"""
import inspect
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from recto.core import llm  # noqa: E402
from recto.corpus import analyze, config  # noqa: E402

PASS = 0
FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {msg}")
    else:
        FAIL += 1
        print(f"  FAIL {msg}")


def _llm_cfg(**kw):
    cfg = dict(config.DEFAULT_CONFIG["llm"])
    cfg.update({"base_url": "https://example.test/v1", "api_key": "k",
                "model": "some-model"})
    cfg.update(kw)
    return cfg


def _reset():
    llm.reset_think_off_cache()
    llm.reset_stats()
    llm.reset_quota()


def _resp(text="ok", reasoning=False):
    return {
        "choices": [{
            "message": {"content": text, **({"reasoning_content": "think..."} if reasoning else {})},
            "finish_reason": "stop",
        }],
        "usage": {},
    }


def test_defaults():
    print("[1] 默认值")
    d = config.DEFAULT_CONFIG["llm"]
    ok(d["thinking"] == "off", "thinking 默认 off (默认关闭思考)")
    ok(d["strict_no_reasoning"] is False, "strict_no_reasoning 默认 False (放宽)")
    sig = inspect.signature(analyze.analyze_epub).parameters["strict_no_reasoning"]
    ok(sig.default is False, "analyze_epub(strict_no_reasoning=False) 默认放宽")


def test_known_forced_relaxed():
    print("[2] 已知关不掉思考 + 放宽 -> 放行")
    _reset()
    key = "https://example.test/v1|some-model"
    with llm._THINK_OFF_LOCK:
        llm._THINKING_REQUIRED.add(key)
    calls = []
    orig = llm._post_json
    llm._post_json = lambda url, payload, headers, timeout: (calls.append(dict(payload)) or _resp("正文"))
    try:
        meta = {}
        out = llm.chat(_llm_cfg(), "s", "u", meta=meta, retries=0)
    finally:
        llm._post_json = orig
    ok(out == "正文", "放宽时正常返回正文")
    ok(meta.get("think_off") == "forced", "meta['think_off'] == 'forced'")
    ok(llm.stats().get("thinking_forced", 0) >= 1, "统计 thinking_forced 有记数")
    ok(len(calls) == 1 and "reasoning_effort" not in calls[0]
       and "thinking" not in calls[0], "不再发关闭参数 (免得再吃 400)")


def test_known_forced_strict():
    print("[3] 已知关不掉思考 + 严格 -> 立刻抛")
    _reset()
    key = "https://example.test/v1|some-model"
    with llm._THINK_OFF_LOCK:
        llm._THINKING_REQUIRED.add(key)
    calls = []
    orig = llm._post_json
    llm._post_json = lambda *a, **k: (calls.append(1) or _resp())
    raised = None
    try:
        llm.chat(_llm_cfg(strict_no_reasoning=True), "s", "u", retries=0)
    except Exception as e:  # noqa: BLE001
        raised = e
    finally:
        llm._post_json = orig
    ok(isinstance(raised, llm.LLMReasoningUnavoidable), "严格模式抛 LLMReasoningUnavoidable")
    ok("严格避免推理" in str(raised), "错误文案指向「严格避免推理」开关")
    ok(calls == [], "严格模式一个网络请求都不发")


def test_ladder_relaxed():
    print("[4] 档位全被拒 + 放宽 -> 裸发一次并记住")
    _reset()
    key = "https://example.test/v1|some-model"
    sent = []

    def fake(url, payload, headers, timeout):
        sent.append(dict(payload))
        if any(k in payload for k in ("reasoning_effort", "thinking", "enable_thinking",
                                      "chat_template_kwargs")):
            raise llm.LLMError(
                'invalid params, model "some-model" requires adaptive thinking; '
                'reasoning effort none is not allowed (2013)')
        return _resp("裸发成功")

    orig = llm._post_json
    llm._post_json = fake
    try:
        out = llm._post_with_think_off(
            "https://example.test/v1/chat/completions", {"model": "some-model"},
            {}, 5, {"reasoning_effort": "none"},
            "https://example.test/v1", "some-model", strict=False)
    finally:
        llm._post_json = orig
    ok(out.get("choices"), "放宽路径返回裸发结果")
    ok(len(sent) == 4, f"先试 3 档 (原参数 + minimal + low) 再裸发, 实际 {len(sent)}")
    ok(not any(k in sent[-1] for k in ("reasoning_effort", "thinking",
                                      "enable_thinking", "chat_template_kwargs")),
       "最后一次是裸发 (不带任何关闭参数)")
    with llm._THINK_OFF_LOCK:
        remembered = key in llm._THINKING_REQUIRED
    ok(remembered, "记住「这个端点关不掉」, 下次直接放行")


def test_ladder_strict():
    print("[5] 档位全被拒 + 严格 -> 抛")
    _reset()

    def fake(url, payload, headers, timeout):
        if any(k in payload for k in ("reasoning_effort", "thinking", "enable_thinking",
                                      "chat_template_kwargs")):
            raise llm.LLMError('requires adaptive thinking; not allowed')
        return _resp("不该到这")

    orig = llm._post_json
    llm._post_json = fake
    raised = None
    try:
        llm._post_with_think_off(
            "https://example.test/v1/chat/completions", {"model": "some-model"},
            {}, 5, {"reasoning_effort": "none"},
            "https://example.test/v1", "some-model", strict=True)
    except Exception as e:  # noqa: BLE001
        raised = e
    finally:
        llm._post_json = orig
    ok(isinstance(raised, llm.LLMReasoningUnavoidable), "严格模式抛 LLMReasoningUnavoidable")


def _blocks():
    return [{"chapter_title": "某章", "text": "正文内容" * 20, "chars": 80}]


def test_analyze_relaxed():
    print("[6] 块里真出现 thinking + 放宽 -> 照跑")
    _reset()

    def fake_chat(cfg, system, user, meta=None, **kw):
        if meta is not None:
            meta["reasoning"] = True
            meta["reasoning_tokens"] = 42
        return '[{"term": "某词", "category": "person"}]'

    orig = llm.chat
    llm.chat = fake_chat
    stats = {}
    raised = None
    try:
        res = analyze.analyze_epub(
            "dummy.epub", _llm_cfg(), config.DEFAULT_CATEGORIES, _blocks(),
            strict_no_reasoning=False, stats_out=stats)
    except Exception as e:  # noqa: BLE001
        raised = e
        res = None
    finally:
        llm.chat = orig
    ok(raised is None, "放宽模式不中止")
    ok(stats.get("reasoning_blocks") == 1, "统计到 1 个推理块")
    ok(bool(res and res.get("person")), "结果照样产出")


def test_analyze_strict():
    print("[7] 块里真出现 thinking + 严格 -> ReasoningAborted")
    _reset()

    def fake_chat(cfg, system, user, meta=None, **kw):
        if meta is not None:
            meta["reasoning"] = True
        return '[{"term": "某词", "category": "person"}]'

    orig = llm.chat
    llm.chat = fake_chat
    raised = None
    try:
        analyze.analyze_epub(
            "dummy.epub", _llm_cfg(), config.DEFAULT_CATEGORIES, _blocks(),
            strict_no_reasoning=True)
    except Exception as e:  # noqa: BLE001
        raised = e
    finally:
        llm.chat = orig
    ok(isinstance(raised, analyze.ReasoningAborted), "严格模式抛 ReasoningAborted")
    ok("严格避免推理" in str(raised), "文案指向「严格避免推理」")


def test_frontend_default():
    print("[8] 前端默认不勾 + 文案")
    js = (ROOT / "src/recto/app/ui/assets/corpus.js").read_text()
    html = (ROOT / "src/recto/app/ui/assets/corpus.html").read_text()
    ok("cfg.llm.strict_no_reasoning === true" in js, "勾选框只在真为 true 时勾上")
    ok("strict_no_reasoning !== false" not in js,
       "不再用 '!== false' (那会让 undefined 也算开)")
    ok("允许它带着思考跑完" in js, "提示文案说明放宽行为")
    ok("默认关" in html, "勾选框标签写明默认关")


def main():
    for fn in (test_defaults, test_known_forced_relaxed, test_known_forced_strict,
               test_ladder_relaxed, test_ladder_strict, test_analyze_relaxed,
               test_analyze_strict, test_frontend_default):
        fn()
    print(f"\n{'=' * 58}\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
