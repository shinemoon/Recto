"""上一代部署契约 —— 只在**这一处**保留旧名字。

为什么需要这一层:
  - 设备接入口的端口/绑定/数据目录以环境变量形式写在 systemd unit 与 `.env` 里,
    改名会让**正在跑的服务**起不来;
  - 浏览器里已签发的会话 cookie 也要继续认。

所以代码其它地方一律用 Recto 的新名字, 旧名字集中在这张表里翻译。
Phase 3 (数据与部署一起切换) 完成后整块删除。
"""
from __future__ import annotations

import os

# --- 设备接入 ---
# 新名: RECTO_DEVICE_<KEY>   旧名: 上一代前缀 + <KEY>
_NEW_PREFIX = "RECTO_DEVICE_"
_LEGACY_PREFIX = "PAPERECHO_"

# --- 摘录 / 藏书票 ---
_NEW_AGG = "RECTO_EXCERPTS_AGGREGATE_DIR"
_LEGACY_AGG = "PAPERECHO_AGGREGATE_DIR"

# --- 前台状态键 (localStorage) ---
NEW_JS_HOST_KEY = "recto_zhijian_host"
NEW_JS_PORT_KEY = "recto_zhijian_port"
LEGACY_JS_HOST_KEY = "paperecho_zhijian_host"
LEGACY_JS_PORT_KEY = "paperecho_zhijian_port"

# --- 管理后台会话 cookie ---
COOKIE_ADMIN = "recto_admin"
COOKIE_ADMIN_LEGACY = "paperecho_admin"


def env(key: str, default: str) -> str:
    """先读新名, 再读旧名, 最后用默认值。"""
    v = os.environ.get(_NEW_PREFIX + key)
    if v is not None:
        return v
    v = os.environ.get(_LEGACY_PREFIX + key)
    return default if v is None else v


def env_aggregate_dir(default: str) -> str:
    v = os.environ.get(_NEW_AGG)
    if v is not None:
        return v
    return os.environ.get(_LEGACY_AGG, default)
