"""书宠的日志落地。

**为什么单独一个文件**: 后台推演是在 daemon 线程里跑的, 失败只写一行
``log.warning`` —— 而进程里从来没有配过 handler, 那些行全部掉进虚空。
结果就是页面上"什么都没变", 日志里"什么都没有", 只能靠猜。

这里把 ``recto.pet.*`` 这一族 logger 挂到 ``<data_dir>/logs/pet.log``
(512KB 轮转, 2 份), 谁调用都不影响别处的日志配置。
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_READY = False


def log_file() -> Path:
    """日志落在哪 (环境变量 ``RECTO_PET_LOG`` 可覆盖, ``off`` = 不落地)。"""
    env = os.environ.get("RECTO_PET_LOG")
    if env:
        return Path(env)
    try:
        from recto.core.config import get_config

        return Path(get_config().data_dir) / "logs" / "pet.log"
    except Exception:  # noqa: BLE001 — 配置读不出来就退回项目 data/
        return Path("data/logs/pet.log")


def ensure() -> logging.Logger:
    """给 ``recto.pet`` 挂上文件 handler (幂等)。返回它的根 logger。"""
    global _READY
    base = logging.getLogger("recto.pet")
    if _READY and base.handlers:
        return base
    env = os.environ.get("RECTO_PET_LOG")
    if env and env.lower() in ("off", "0", "no"):
        _READY = True
        return base
    try:
        p = log_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        h = RotatingFileHandler(p, maxBytes=512 * 1024, backupCount=2,
                                encoding="utf-8")
        h.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"))
        base.addHandler(h)
        base.setLevel(logging.INFO)
        base.propagate = False          # 别在 root 那边再抖一次
    except Exception:  # noqa: BLE001 — 日志落地失败不许挡住业务
        pass
    _READY = True
    return base


def tail(n: int = 40) -> list[str]:
    """最近几行 (调试面板用, 读不到就空)。"""
    try:
        p = log_file()
        if not p.exists():
            return []
        with p.open("r", encoding="utf-8", errors="replace") as f:
            return [ln.rstrip("\n") for ln in f.readlines()[-int(n):]]
    except Exception:  # noqa: BLE001
        return []
