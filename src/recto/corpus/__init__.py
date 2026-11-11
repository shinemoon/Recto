"""语料服务 (EPUB 关键字精炼) —— 云阅 Recto 的原生能力。

数据布局 (根 = config.toml 的 `[corpus] dir`, 默认 <data_dir>/corpus):

    config.json      LLM 端点 / 模型库 / 分类定义 (含 api_key, 权限 600)
    state.json       书库状态 (每本 epub 的 book_id → 路径 / 标题 / 进度)
    outputs/<bid>/   中间产物 (meta.json / blocks.json / keywords.json / status.json)
    <slug>.json      固定导出 (下游语料消费方直接扫这一层)
    index.json       导出清单 (latest_slug / latest_pinned)
    latest.json      最近一次成功产物 (下游只需读这一个)

模块划分:
    config      — LLM / 解析 / 分类 配置的读写
    llmconfig   — 共用的 LLM 零件 (脱敏 / 模型库 / key 覆盖判定) —— 在 core
    llm         — 极简 OpenAI 兼容 / Anthropic 兼容客户端 (在 core, 含配额熔断 / 硬超时)
    epub_parse  — EPUB 解析 (OPF / spine / TOC / 正文抽取)
    analyze     — 分块 + LLM 抽取 + 合并去重 → 关键字 JSON (含断点续跑)
    store       — 每本书的磁盘产物
    presets     — 端点预设与提示
    service     — 服务端逻辑 (任务队列 / 各 API 用例), 供 app/corpus 路由调用
"""
from __future__ import annotations

__version__ = "1.0.0"
