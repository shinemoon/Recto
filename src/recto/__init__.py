"""Recto — 把「在哪读」「读得好」「留了痕」「收好痕」串成一条线。

分层规则（见 docs/PLAN.md §2.1）:

    core/      只依赖标准库。不碰网络、不碰文件、不读配置。
    sources/   外部世界适配器。每个对端一个模块。
    services/  用例编排。所有 IO 走 store/。
    app/       Web 层。只做鉴权 / 路由 / 渲染, 不含业务逻辑。
    store/     持久化 (sqlite / jsonl / sidecar)。
"""

__version__ = "0.0.0"
__all__ = ["__version__"]
