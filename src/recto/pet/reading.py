"""书宠故事 · **读到哪了** (阅读位置)。

前台显示的是**最新的一回** (2026-11-21 改), 所以位置记成
**「读到哪一回的第几段」** —— 一对 (回号, 段号)。

**为什么不是「全局第几段」** (那是 2026-11-19 ~ 11-21 的中间形态):
全局段序号要求前台显示"一条连续的路" (全部回摊平)。现在前台只显示
最新那一回 —— 全局序号就得在"这一回的第几段"与"全书第几段"之间来回换算,
而且**新回一出来, 全局序号会跳** (新回接在末尾, 全局号从更大处开始),
读者明明还没读新回, 位置却显示"读完了"。记 (回号, 段号) 没这问题:
新回 = 新的一格, 位置自动回到那一回的开头。

**位置只在同一回里前进** (高水位): 回头重读不抹掉已经读过的地方。
换到更新的一回 → 那一回从第 0 段重新开始 (它是新内容)。

**为什么单独一个文件** (``readpos.json``, 不塞进 ``chronicle.json``):
``chronicle.json`` 是推演状态, 推演一回要跑 10~60 秒 —— 期间它**持有整份
state**, 最后一次性写回。前端在这段时间里记的阅读位置会被那份旧快照覆盖掉
(真踩过: 读着故事, 推演一回, 位置归零)。两个写者共用一个 JSON 就一定会互相
覆盖, 所以阅读位置自己一个文件。

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

#: 存档格式版本
#:   3 = ``{"v":3, "round": 回号, "idx": 段号}``  (当前)
#:   2 = ``{"v":2, "idx": 全局段序号}``             (中间形态, 要迁)
#:   1 = ``{"<回号>": {"para": 段号}}``              (最初形态, 要迁)
VERSION = 3


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


def version(d: dict[str, Any] | None = None) -> int:
    """存档格式版本。"""
    d = _load() if d is None else d
    try:
        return int(d.get("v") or 0)
    except (TypeError, ValueError):
        return 0


def is_legacy(d: dict[str, Any] | None = None) -> bool:
    """是不是老格式 (要迁)?"""
    d = _load() if d is None else d
    return version(d) != VERSION and bool(d)


def legacy_positions(d: dict[str, Any] | None = None) -> dict[str, int]:
    """v1 格式 (按回记段数) → ``{"32": 6}``。"""
    d = _load() if d is None else d
    out: dict[str, int] = {}
    for k, v in d.items():
        if k in ("v", "idx", "at", "round"):
            continue
        try:
            out[str(k)] = max(0, int((v or {}).get("para") or 0))
        except (AttributeError, TypeError, ValueError):
            continue
    return out


def get() -> dict[str, Any]:
    """读到哪儿了 → ``{"round": 回号, "idx": 段号, "at": iso}``。"""
    d = _load()
    try:
        idx = int(d.get("idx") or 0)
    except (TypeError, ValueError):
        idx = 0
    try:
        rnd = int(d.get("round") or 0)
    except (TypeError, ValueError):
        rnd = 0
    return {"round": max(0, rnd), "idx": max(0, idx), "at": d.get("at") or "",
            "legacy": is_legacy(d)}


def set_pos(round_no: int, idx: int, *, force: bool = False,
             clamp: bool = True) -> dict[str, Any]:
    """记下"读到第几回的第几段"。返回新的位置。

    **同一回里只前进** (高水位): 回头重读不抹掉已读的地方。
    ``force=True`` 才允许往回写 (「从头读」/ 迁移)。

    **换到更新的一回** → 那一回从第 0 段起 (新内容, 没读过)。
    换到**更早**的一回 → 不认 (那是回头看, 不该把进度倒退)。

    ``clamp=True`` (默认) 会把"一次跳太远"夹住 (防手滑/脚本把位置推到天边)。
    **滚动式阅读要传 ``clamp=False``** —— 读者一口气拖过几百段是正常的。
    """
    try:
        idx = max(0, int(idx))
    except (TypeError, ValueError):
        return get()
    try:
        round_no = max(0, int(round_no))
    except (TypeError, ValueError):
        round_no = 0
    fh = _lock()
    try:
        d = _load()
        try:
            old_round = int(d.get("round") or 0)
        except (TypeError, ValueError):
            old_round = 0
        try:
            old_idx = int(d.get("idx") or 0)
        except (TypeError, ValueError):
            old_idx = 0
        if not force:
            if round_no < old_round:
                # 更早的一回 —— 不认 (位置是"正在读哪一回")
                return {"round": old_round, "idx": old_idx,
                        "at": d.get("at") or "", "legacy": False}
            if round_no == old_round:
                if idx <= old_idx:
                    # 已经在更远处 (回头重读) —— 不动
                    return {"round": old_round, "idx": old_idx,
                            "at": d.get("at") or "", "legacy": False}
                if clamp and idx - old_idx > MAX_JUMP:
                    idx = old_idx + MAX_JUMP
            # round_no > old_round → 新的一回, 从头开始 (idx 就是新的)
        d = {"v": VERSION, "round": round_no, "idx": idx,
             "at": clock.fmt_sec(time.time())}
        _save(d)
        return {"round": d["round"], "idx": d["idx"], "at": d["at"],
                "legacy": False}
    finally:
        _unlock(fh)


def set_idx(idx: int, *, force: bool = False, clamp: bool = True) -> dict[str, Any]:
    """兼容层: 老接口 (全局段序号) —— 现在位置按 (回号, 段号) 记。

    真要写的是 :func:`set_pos`。留这个函数是为了让还在调它的老代码
    **显式地**什么也没做, 而不是悄悄写错地方。
    """
    log.warning("阅读位置: 旧接口 set_idx() 已废弃, 位置改记 (回号, 段号)", idx)
    return get()


def reset() -> None:
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


#: 老名字 (按回记位置) —— 老调用点留着, 别静默失效
def get_by_no(no: int = 0) -> dict[str, Any]:
    """兼容层: 老代码问"第几回到第几段"。"""
    cur = get()
    return {"round": cur["round"], "idx": cur["idx"], "at": cur["at"],
            "no": int(no or 0), "para": cur["idx"]}


def all_positions() -> dict[str, int]:
    """兼容层: 老接口返回"按回的位置" —— 现在只有一对 (回号, 段号)。"""
    cur = get()
    return {str(cur["round"]): cur["idx"]} if cur["round"] else {}
