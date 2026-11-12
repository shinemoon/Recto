"""陪读源 —— 把阅读器的阅读时长折算成书宠的修为。

**这一层只做一件事: 回答"这只书宠一共被陪读了多少分钟"。**

为什么单独一个模块:
`pet/` 是**业务层** (人物 / 成长 / 人格), `services/` 是**数据层**
(进度 / 书签 / 划线 / 时长)。让 `pet` 直接 import `reader_state` 会把
两层焊死 —— 以后想换时长来源 (比如接掌阅那边) 就得改人物层。

**唯一真相源是 `reader_state.rtime`** (书 × 天 → 分钟位图):
阅读器的心跳 (`session_beat`) 每 30 秒落一次, 空闲保护 2 分钟,
所以它记的就是"人真的在读"的时长 —— 不是挂机时长。
云端那份 (`cloud_minutes`) 不算陪读: 那是手机那边读的,
跟"这本书在我面前陪着"不是一回事, 混进来会让书宠替别人读书。

**为什么用"累计值 + 锚点"而不是"每次新增多少"**:
成长层是增量口径 (这次又陪读了 N 分钟), 阅读器手里是累计值。
直接拿累计值去 award, 每次都会把历史重算一遍 —— 修为会平方级地涨。
所以成长层用 `credited_minutes` 当幂等锚点, 这里只负责把累计值送过去。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

log = logging.getLogger("recto.pet.companion")

#: 心跳周期 (秒)。陪读是"分钟级"的事, 一分钟看一眼足够。
TICK_SEC = float(os.environ.get("RECTO_PET_TICK_SEC", "60") or 60)

_TICK: dict[str, Any] = {"thread": None, "stop": False, "at": 0.0,
                         "runs": 0, "last": None, "error": None}


def total_reading_minutes() -> int:
    """全部书加起来的**本地**阅读分钟 (不含云端那份)。

    用 `rtime.minutes` 而不是 `_eff()` 的 MAX(local, cloud):
    云端那部分是手机读的, 不该算"陪读"。
    """
    from recto.services import reader_state as rs

    try:
        return int(rs.pending_minutes() or 0)
    except Exception as e:  # noqa: BLE001 — 时长表读不出来不该挡住陪读
        log.warning("陪读源 · 读时长失败: %s", e)
        return 0


def per_book_minutes() -> dict[str, int]:
    """每本书的本地阅读分钟 (书 key → 分钟)。"""
    from recto.services import reader_state as rs

    out: dict[str, int] = {}
    try:
        c = rs.conn()
        rows = c.execute(
            "SELECT book, COALESCE(SUM(minutes),0) m FROM rtime GROUP BY book"
        ).fetchall()
        for r in rows:
            out[str(r["book"])] = int(r["m"] or 0)
    except Exception as e:  # noqa: BLE001
        log.warning("陪读源 · 读分书时长失败: %s", e)
    return out


def _book_titles(keys: list[str]) -> dict[str, str]:
    """书 key → 书名 (界面上要给人看的, 不是 hash)。

    两处来源, 优先级从高到低:
      1. `rtime` 行里的**快照** (删书前抄下来的) —— 书已经不在书库里了, 只有它认得
      2. `rbook` 的当前书名 —— 书还在, 但可能被改名
    """
    from recto.services import reader_state as rs

    out: dict[str, str] = {}
    if not keys:
        return out
    try:
        c = rs.conn()
        for k in keys:
            row = c.execute(
                "SELECT book_title FROM rtime WHERE book=? "
                "AND COALESCE(book_title,'')<>'' LIMIT 1", (k,)).fetchone()
            if row and row["book_title"]:
                out[k] = str(row["book_title"])
                continue
            row = c.execute("SELECT title FROM rbook WHERE key=?", (k,)).fetchone()
            if row and row["title"]:
                out[k] = str(row["title"])
    except Exception as e:  # noqa: BLE001
        log.warning("陪读源 · 读书名失败: %s", e)
    return out


def companion_state() -> dict[str, Any]:
    """陪读源的现状 (给接口 / 页面看)。"""
    total = total_reading_minutes()
    books = per_book_minutes()
    top = sorted(books.items(), key=lambda kv: -kv[1])[:12]
    titles = _book_titles([k for k, _ in top])
    # 认不出的 = 书已经从书架删了, 而且是**改动之前**删的 (没有快照)。
    # 显示裸 hash 没人看得懂, 给个能读的说法。
    named = [[titles.get(k) or "已删除的书", m] for k, m in top]
    return {
        "total_minutes": total,
        "books": len(books),
        "top": named,
    }


def feed_once(ch: dict[str, Any]) -> dict[str, Any]:
    """把当前累计陪读分钟折算进人物存档 (就地改 `ch`, 调用方负责 save)。

    返回这次的变化明细。**幂等** —— 同一份时长反复喂, 第二次起就是空转。
    """
    from recto.pet import growth

    total = total_reading_minutes()
    gain = growth.credit_reading_minutes(ch, total)
    if gain.get("grew"):
        log.info("陪读 · 折算 %d 分钟 → 修为 +%d%s", gain["minutes"],
                 gain.get("exp", 0),
                 (f" · 升 {gain['levels']} 级") if gain.get("levels") else "")
    return gain


def reconcile_anchor(ch: dict[str, Any], total: int) -> dict[str, Any]:
    """锚点比数据源还高 → 校准回来 (数据源被清理过时会发生)。

    **什么时候会**: 数据源的累计分钟被清理过 (删测试数据 / 换书库),
    但人物存档里的锚点还停在旧值 → 接下来的一段时间里, 真实的阅读
    会因为"已经折算过了"而被白吃掉。

    这是**数据修复**, 不是"回退": 修为不回收, 只是把锚点拉回数据源说的位置,
    让后面的阅读能正常折算。

    返回 {"adjusted": n, "from": a, "to": b} (没调时 adjusted=0)。
    """
    credited = max(0, int(ch.get("credited_minutes") or 0))
    cap = max(0, int(total or 0))
    if credited <= cap:
        return {"adjusted": 0, "from": credited, "to": credited}
    # credited 与 minutes 是同一个数的两个名字 —— 一起校准, 别分叉
    ch["credited_minutes"] = cap
    ch["minutes"] = cap
    log.info("陪读 · 锚点校准 %d → %d (数据源被清理过)", credited, cap)
    return {"adjusted": credited - cap, "from": credited, "to": cap}


def _disk_minutes() -> tuple[int, int] | None:
    """读存档里落盘的 (minutes, credited_minutes)。读不出来返回 None。

    为什么要它: :func:`charsheet.load_char` 在**内存里**做自愈 (两份拉齐),
    但不落盘 —— GET 页面不该写盘。心跳是写盘的场合, 顺手把自愈结果固化下来,
    否则文件里那对不一致的数会一直躺着, 每次载入都要重算一遍。
    """
    import json
    from pathlib import Path

    from recto.pet import charsheet as cs

    try:
        d = json.loads(Path(cs.CHAR_PATH).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return (int(d.get("minutes") or 0), int(d.get("credited_minutes") or 0))


def feed_now() -> dict[str, Any]:
    """读存档 → 校准锚点 → 折算 → 写回。返回这次的变化。"""
    from recto.pet import charsheet as cs

    disk = _disk_minutes()
    ch = cs.load_char()
    total = total_reading_minutes()
    fix = reconcile_anchor(ch, total)
    gain = feed_once(ch)
    # 自愈也要落盘: 载入时把 minutes / credited 拉齐过, 文件里还是旧的一对
    healed = bool(disk is not None
                  and disk != (int(ch.get("minutes") or 0),
                               int(ch.get("credited_minutes") or 0)))
    if gain.get("grew") or fix.get("adjusted") or healed:
        cs.save_char(ch)
    out = dict(gain)
    out["reconciled"] = fix
    out["healed"] = healed
    return out


def tick_state() -> dict[str, Any]:
    """陪读心跳的现状 (页面 / 接口看)。"""
    th = _TICK.get("thread")
    return {
        "enabled": bool(th is not None and getattr(th, "is_alive", lambda: False)()),
        "interval_sec": int(round(TICK_SEC)),
        "runs": int(_TICK.get("runs") or 0),
        "last_at": _TICK.get("at"),
        "last": _TICK.get("last"),
        "error": _TICK.get("error"),
    }


def start_ticker() -> bool:
    """起陪读心跳 (幂等)。返回是否真的新起了一个。"""
    th = _TICK.get("thread")
    if th is not None and getattr(th, "is_alive", lambda: False)():
        return False
    _TICK["stop"] = False
    thread = threading.Thread(target=_loop, daemon=True, name="recto-pet-tick")
    _TICK["thread"] = thread
    thread.start()
    log.info("陪读心跳已启动 · 每 %d 秒折算一次", int(round(TICK_SEC)))
    return True


def stop_ticker() -> None:
    _TICK["stop"] = True


def _loop() -> None:
    """心跳主体。每一轮现读周期 —— 改设置不用重启服务。"""
    time.sleep(5)   # 刚起来先等等, 别跟"打开书时的拉取"抢
    while not _TICK.get("stop"):
        try:
            gap = max(10.0, TICK_SEC)
            _TICK["at"] = time.time()
            _TICK["runs"] = int(_TICK.get("runs") or 0) + 1
            gain = feed_now()
            _TICK["last"] = gain
            _TICK["error"] = None
        except Exception as e:  # noqa: BLE001 — 心跳不能死
            _TICK["error"] = f"{type(e).__name__}: {e}"
            log.warning("陪读心跳出错 · %s", e)
        end = time.time() + max(5.0, TICK_SEC)
        while not _TICK.get("stop") and time.time() < end:
            time.sleep(min(5.0, max(0.5, end - time.time())))
