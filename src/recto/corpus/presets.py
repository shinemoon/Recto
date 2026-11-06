"""预设的 LLM 端点 (页面下拉选, 省得手填 base_url / model).

每个预设带 note 说明适用场景 —— 尤其是**推理模型不适合做抽取**这件事.
"""
from __future__ import annotations

from typing import Any

PRESETS: list[dict[str, Any]] = [
    {
        "id": "agnes-2.5-flash",
        "name": "agnes-2.5-flash ★推荐",
        "provider": "openai",
        "base_url": "https://apihub.agnes-ai.com/v1",
        "model": "agnes-2.5-flash",
        "note": "非推理模型, 抽取任务 13s/块. 需要 agnes key.",
    },
    {
        "id": "agnes-3.0-flash",
        "name": "agnes-3.0-flash",
        "provider": "openai",
        "base_url": "https://apihub.agnes-ai.com/v1",
        "model": "agnes-3.0-flash",
        "note": "较快, 偶尔限流 (429).",
    },
    {
        "id": "minimax-m3",
        "name": "MiniMax-M3 ⚠不推荐",
        "provider": "openai",
        "base_url": "https://api.minimaxi.com/v1",
        "model": "MiniMax-M3",
        "note": "推理模型: token 全烧在 thinking 里, 抽词条会截断/超时. 只适合翻译.",
    },
    {
        "id": "custom-openai",
        "name": "自定义 (OpenAI 兼容)",
        "provider": "openai",
        "base_url": "",
        "model": "",
        "note": "手填 base_url / model / key.",
    },
    {
        "id": "custom-anthropic",
        "name": "自定义 (Anthropic)",
        "provider": "anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "model": "",
        "note": "手填 base_url / model / key.",
    },
]

# 抽取任务的建议
TIPS = [
    "抽取是**非推理**任务 —— 用 flash 类模型, 不要用推理模型 (MiniMax-M3 / o1 / 带 thinking 的).",
    "推理模型会把 max_tokens 全烧在 thinking 里, 结果是 finish_reason=length, 正文一个字没有.",
    "块大小 4000~6000 字符最稳; 太大模型要处理的 token 多, 慢且容易截断.",
    "max_tokens 给 8192~16384, 便宜.",
]
