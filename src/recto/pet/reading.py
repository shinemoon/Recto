"""书宠故事 · **读到哪了** (阅读位置)。

故事是一段正文, 不是一张卡。读者要能一段一段往下读, 读到哪里要记住 ——
关掉页面、换一天回来, 接着上次那句往下读, 而不是从头再读一遍。

**为什么存后端而不是 localStorage**: 书宠的"读到哪里"是它这条命的痕迹,
和游历记录 (``adventure`` 表) 是一家的。放浏览器本地会跟着换设备/清缓存消失,
而"它走过的路"本来就不该丢。

**为什么单独一个文件** (``readpos.json``, 不塞进 ``chronicle.json``):
``chronicle.json`` 是推演状态, 推演一回要跑 10~60 秒 —— 期间它**持有整份
state**, 最后一次性写回。前端在这段时间里记的阅读位置会被那份旧快照覆盖掉
(真踩过: 读着故事, 推演一回, 位置归零)。两个写者共用一个 JSON 就一定会互相
覆盖, 所以阅读位置自己一个文件。

键 = "第几回" (adventure.no), 值 = ``{"para": n, "at": iso}`` —— n = 已读完的段数。
新回合一出现, 没有记录就当 0 (从头读)。

**锁**: 同一个文件里也有多个写者 (HTTP 处理线程 + 可能的后台线程),
所以"读 → 改 → 写"用**文件锁** (``fcntl.flock``) 圈住。
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from recto.core import clock
from recto.pet import store

log = logging.getLogger("recto.pet.reading")

#: 一次最多往前跳多少段 (防手滑/脚本把位置一次推到天边)
MAX_JUMP = 400

STATE_NAME = "readpos.json"


def state_path() -> Path:
    return Path(store.pet_dir()) / STATE_NAME


def _lock():
    """``readpos.json`` 的跨线程/跨进程文件锁。"""
    try:
        import fcntl
    except ImportError:  # noqa: BLE001 — Windows 没有 fcntl
        return None
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fh = open(p.with_name(p.name + ".lock"), "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    except OSError:
        fh.close()
        return None
    return fh


def _unlock(fh) -> None:
    if fh is None:
        return
    try:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except Exception:  # noqa: BLE001
        pass
    try:
        fh.close()
    except Exception:  # noqa: BLE001
        pass


def _load() -> dict[str, Any]:
    try:
        d = json.loads(state_path().read_text("utf-8"))
        if isinstance(d, dict):
            return d
    except (OSError, ValueError):
        pass
    return {}


def _save(d: dict[str, Any]) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # 临时名带线程 id: 同进程两个线程同时写, 固定名会撞车 (os.replace 找不到文件)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.{_tid()}.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), "utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, p)


def _tid() -> int:
    import threading

    return threading.get_ident()


def get(no: int) -> dict[str, Any]:
    """第几回读到哪了 → ``{"no": n, "para": k, "at": iso}``。"""
    try:
        no = int(no)
    except (TypeError, ValueError):
        no = 0
    rec = _load().get(str(no)) or {}
    try:
        para = int(rec.get("para") or 0)
    except (TypeError, ValueError):
        para = 0
    return {"no": no, "para": max(0, para), "at": rec.get("at") or ""}


def set_pos(no: int, para: int) -> dict[str, Any]:
    """记下"第几回读到第几段"。返回新的位置。"""
    try:
        no = int(no)
        para = int(para)
    except (TypeError, ValueError):
        return get(no)
    para = max(0, para)
    fh = _lock()
    try:
        d = _load()
        key = str(no)
        old = d.get(key) or {}
        try:
            old_para = int(old.get("para") or 0)
        except (TypeError, ValueError):
            old_para = 0
        # 一次跳太远 = 大概是手滑或脚本, 不认 (只往前一点点, 读者一页一页读)
        if para - old_para > MAX_JUMP:
            para = old_para + MAX_JUMP
        d[key] = {"para": para, "at": clock.fmt_sec(time.time())}
        _save(d)
        return {"no": no, "para": para, "at": d[key]["at"]}
    finally:
        _unlock(fh)


def all_positions() -> dict[str, int]:
    """全部回读的进度 → ``{"29": 12, ...}`` (给页面一次拿全)。"""
    out: dict[str, int] = {}
    for k, v in _load().items():
        try:
            out[str(k)] = max(0, int((v or {}).get("para") or 0))
        except (TypeError, ValueError):
            continue
    return out


def clear() -> None:
    """复位 / 转生时清掉 (故事都清了, 位置也就没有意义了)。"""
    fh = _lock()
    try:
        p = state_path()
        try:
            p.unlink()
        except OSError:
            pass
    finally:
        _unlock(fh)