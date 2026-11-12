"""时间口径 —— 全站唯一的「现在」和「本地时间」来源。

为什么要有这个模块: 摘录时间戳曾出现**两个方向相反**的错

  - 设备推上来的 `createdTime` 是毫秒时间戳 (UTC 绝对时间), 旧代码
    `datetime.fromtimestamp(ms, tz=utc).strftime(...)` 直接印出来 → 比本地慢 8 小时;
  - 阅读器划线存的是**裸本地** ISO (`2026-11-07T23:23:59`), 旧代码把它当 UTC
    再 astimezone → 比本地快 8 小时, 甚至跨进"明天"。

约定 (与墨水屏设备 / 云端阅读服务一致):

  1. 存盘用**本地时间**; 两种形态都要能读:
       - 带偏移的 ISO   `2026-11-07T23:23:59+08:00`
       - 裸 ISO         `2026-11-07T23:23:59`   ← 按**本地**解释, 绝不当 UTC
       - 毫秒时间戳     `1793504718000`          ← 绝对时间, 显示时转本地
  2. 显示一律转成本地时区, 不在展示层做加减。
  3. 需要 UTC 的地方 (设备的 `receivedAt` / 协议里的 `Z`) 显式调 `iso_utc()`。

时区来源 (优先级从高到低): 环境变量 `RECTO_TZ` > `config.toml` 的 `[ui] timezone`
> 系统时区 (空值 = 跟随系统 `TZ` / `/etc/localtime`)。
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

try:                                      # Python 3.9+
    from zoneinfo import ZoneInfo
except Exception:                         # pragma: no cover — 老解释器/缺 tzdata
    ZoneInfo = None                       # type: ignore[assignment]

_MM = "%Y-%m-%d %H:%M"
_cache: dict[str, Any] = {"name": object(), "tz": None}


def zone_name() -> str:
    """配置里的时区名 (空 = 跟随系统)。"""
    name = (os.environ.get("RECTO_TZ") or "").strip()
    if name:
        return name
    try:
        from .config import get_config

        name = str(get_config().get("ui", "timezone", "") or "").strip()
    except Exception:                     # noqa: BLE001 — 配置读不出来不该影响时间显示
        name = ""
    return name


def zone():
    """`ZoneInfo` 实例; 跟随系统时返回 None (交给 `astimezone()`)。"""
    name = zone_name()
    if _cache["name"] != name:
        tz = None
        if name and ZoneInfo is not None:
            try:
                tz = ZoneInfo(name)
            except Exception:             # noqa: BLE001 — 写错时区名就退回系统本地
                tz = None
        _cache["name"] = name
        _cache["tz"] = tz
    return _cache["tz"]


# ------------------------------------------------------------------ 读 / 转

def now() -> datetime:
    """当前时间 (带时区)。"""
    return local(datetime.now(timezone.utc))


def local(dt: datetime | None = None) -> datetime:
    """把任意 datetime 转成本地 (裸时间按本地解释)。"""
    tz = zone()
    if dt is None:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:                 # 裸本地时间: 补上本地时区, 不做平移
        return dt.replace(tzinfo=tz or dt.astimezone().tzinfo)
    return dt.astimezone(tz) if tz else dt.astimezone()


def from_ms(ms: float | int) -> datetime:
    """毫秒时间戳 → 本地时间 (绝对时间, 必须换算)。"""
    return local(datetime.fromtimestamp(float(ms) / 1000.0, tz=timezone.utc))


def to_ms(dt: datetime) -> int:
    """datetime → 毫秒时间戳 (裸时间按本地解释)。"""
    return int(local(dt).timestamp() * 1000)


def parse(raw: Any) -> datetime | None:
    """把「毫秒时间戳 / ISO 串」解析成本地时间。

    裸 ISO 按本地解释 —— 这是我们自己写盘的形态; 带 `Z` / 偏移的按偏移换算。
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        try:
            return from_ms(raw)
        except (ValueError, OSError, OverflowError):
            return None
    s = str(raw).strip()
    if not s:
        return None
    if s.isdigit() and len(s) >= 11:      # 纯数字且像时间戳 (>= 2001 年)
        try:
            return from_ms(int(s))
        except (ValueError, OSError, OverflowError):
            return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return local(dt)


def fmt(raw: Any, pattern: str = _MM) -> str:
    """解析 + 格式化; 解析不了就原样返回前 16 个字符 (不抛错)。"""
    dt = parse(raw)
    if dt is None:
        s = str(raw or "")
        return s[:16] if s else ""
    return dt.strftime(pattern)


def fmt_dt(dt: datetime | None, pattern: str = _MM) -> str:
    if dt is None:
        return ""
    return local(dt).strftime(pattern)


# ------------------------------------------------------------------ 存盘

def iso_local() -> str:
    """带偏移的本地 ISO (存盘推荐形态)。"""
    return local(datetime.now(timezone.utc)).isoformat(timespec="seconds")


def iso_utc() -> str:
    """带 Z 的 UTC ISO (仅协议/设备字段用)。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def summary() -> dict:
    """给设置页/自检看的时区口径。"""
    tz = zone()
    return {
        "config": zone_name(),
        "effective": getattr(tz, "key", None) or (local().tzname() or "system"),
        "now": local().strftime("%Y-%m-%d %H:%M:%S"),
        "offset": local().strftime("%z"),
        "system_tz": os.environ.get("TZ", ""),
    }


def fmt_sec(sec: Any, pattern: str = _MM) -> str:
    """格式化**秒级**时间戳 (``time.time()`` 那种形态)。

    为什么要有它: :func:`fmt` / :func:`parse` 把裸数字一律当**毫秒**
    (全站存盘口径), 直接把 ``time.time()`` 喂进去会得到 1970 年 ——
    书宠的推演窗口第一版就这么错了 (窗口起点变成 "1970-01-22")。
    """
    try:
        return fmt(float(sec) * 1000.0, pattern)
    except (TypeError, ValueError):
        return ""
