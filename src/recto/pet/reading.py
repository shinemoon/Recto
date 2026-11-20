"""书宠故事 · **读到哪了** (阅读位置)。

故事是一条**连续**的路 —— 前台不划分章节, 像 MUD 那样往下滚, 空格推一段。
所以位置记成**全局第几段**, 而不是「第几回第几段」。

**章节是后台的事**: 要按回查 (第几回 / 交手 / 判定 / 来处), 去档案页
``/pet/story/``。前台只管"读到哪儿了"。

**为什么存后端而不是 localStorage**: 书宠的"读到哪里"是它这条命的痕迹,
和游历记录 (``adventure`` 表) 是一家的。放浏览器本地会跟着换设备/清缓存消失,
而"它走过的路"本来就不该丢。

**为什么单独一个文件** (``readpos.json``, 不塞进 ``chronicle.json``):
``chronicle.json`` 是推演状态, 推演一回要跑 10~60 秒 —— 期间它**持有整份
state**, 最后一次性写回。前端在这段时间里记的阅读位置会被那份旧快照覆盖掉
(真踩过: 读着故事, 推演一回, 位置归零)。两个写者共用一个 JSON 就一定会互相
覆盖, 所以阅读位置自己一个文件。

**全局序号怎么算**: 故事按回存 (``id`` 升序), 每回正文按空行分段。
全局序号 = 前面所有回的总段数 + 本回段序。**从前往后累加** ——
新回只追加在末尾, 不会挪动已有段的序号 (老故事被重写才会变, 少见)。

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

#: 存档格式版本 (2 = 全局段序号; 1 = 按回的段数, 老格式)
VERSION = 2


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


def is_legacy(d: dict[str, Any] | None = None) -> bool:
    """是不是老格式 (按回记段数)? 老档要迁成全局序号。"""
    d = _load() if d is None else d
    if d.get("v") == VERSION:
        return False
    return bool(d)


def legacy_positions(d: dict[str, Any] | None = None) -> dict[str, int]:
    """老格式里"按回记的段数" → ``{"32": 6}``。"""
    d = _load() if d is None else d
    out: dict[str, int] = {}
    for k, v in d.items():
        if k in ("v", "idx", "at"):
            continue
        try:
            out[str(k)] = max(0, int((v or {}).get("para") or 0))
        except (AttributeError, TypeError, ValueError):
            continue
    return out


def get() -> dict[str, Any]:
    """读到哪儿了 → ``{"idx": n, "at": iso}`` (n = 全局段序号)。"""
    d = _load()
    try:
        idx = int(d.get("idx") or 0)
    except (TypeError, ValueError):
        idx = 0
    return {"idx": max(0, idx), "at": d.get("at") or "",
            "legacy": is_legacy(d)}


def set_idx(idx: int, *, force: bool = False, clamp: bool = True) -> dict[str, Any]:
    """记下"读到全局第几段"。返回新的位置。

    **只前进** (高水位): "读到哪儿了"是已经读过的最远处 —— 回头重读
    不该把它抹掉。``force=True`` 才允许往回写 (迁移老档时用)。

    ``clamp=True`` (默认) 会把"一次跳太远"夹住 (防手滑/脚本把位置推到天边)。
    **滚动式阅读要传 ``clamp=False``** —— 读者一口气拖过几百段是正常的,
    夹住会让"读到哪儿"永远落后于眼睛。
    """
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        return get()
    idx = max(0, idx)
    fh = _lock()
    try:
        d = _load()
        try:
            old = int(d.get("idx") or 0)
        except (TypeError, ValueError):
            old = 0
        if not force:
            if idx <= old:
                # 已经在更远处 (回头重读) —— 不动
                return {"idx": old, "at": d.get("at") or "", "legacy": False}
            # 一次跳太远 = 大概是手滑或脚本, 不认 (只往前一点点, 读者一段一段读)
            if clamp and idx - old > MAX_JUMP:
                idx = old + MAX_JUMP
        d = {"v": VERSION, "idx": idx, "at": clock.fmt_sec(time.time())}
        _save(d)
        return {"idx": d["idx"], "at": d["at"], "legacy": False}
    finally:
        _unlock(fh)


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
    """兼容层: 老代码问"第几回到第几段", 现在只有全局序号。"""
    cur = get()
    return {"idx": cur["idx"], "at": cur["at"], "no": int(no or 0), "para": 0}


def set_pos(no: int, para: int) -> dict[str, Any]:
    """兼容层: 老接口 (按回记段数) 现在写不进全局序号 —— 返回现状。

    真正的写入走 :func:`set_idx` (全局段序号)。留这个函数是为了让
    还在调它的老代码**显式地**什么也没做, 而不是悄悄写错地方。
    """
    log.warning("阅读位置: 旧接口 set_pos(no=%s, para=%s) 已废弃, 位置改记全局段序号",
                no, para)
    return get()


def all_positions() -> dict[str, int]:
    """兼容层: 老接口返回"按回的位置" —— 现在只有全局序号, 返回空。"""
    return {}