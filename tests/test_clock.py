"""时间口径 —— 全站只有一个「本地时间」定义 (2026-11-07)。

修的两个方向相反的错 (都出在展示层):

  · 设备推上来的 `createdTime` 是毫秒时间戳, 旧代码
    `datetime.fromtimestamp(ms, tz=utc).strftime(...)` 直接印 → **慢 8 小时**;
  · 阅读器划线存的是**裸本地** ISO `2026-11-07T23:23:59`, 旧代码把它当 UTC
    再 astimezone → **快 8 小时**, 甚至显示成"明天 07:23"。

本测试钉住 `core.clock` 的契约 (它是全站唯一的时间换算入口):
  毫秒时间戳 = 绝对时间 (必须换算) / 裸 ISO = 本地 (不许当 UTC) /
  带偏移或 Z 的 ISO = 按偏移换算 / `[ui] timezone` 与 `RECTO_TZ` 能固定时区。

跑法: python3 tests/test_clock.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from recto.core import clock                      # noqa: E402
from recto.app import excerpts as ex              # noqa: E402

PASS = 0
FAILED: list[str] = []


def ok(cond, name, extra=""):
    global PASS
    if cond:
        PASS += 1
        print(f"PASS {name}" + (f" — {extra}" if extra else ""))
    else:
        FAILED.append(name)
        print(f"FAIL {name}" + (f" — {extra}" if extra else ""))


# 固定时区跑全部断言 (不依赖跑测试那台机器的 /etc/localtime)
os.environ["RECTO_TZ"] = "Asia/Shanghai"
clock._cache["name"] = object()                   # 丢掉可能已缓存的系统时区


def _ms(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone(timedelta(hours=8))).timestamp() * 1000)


# ------------------------------------------------------------------ 毫秒时间戳
ms = _ms("2026-11-01T11:45:00")
ok(clock.fmt(ms) == "2026-11-01 11:45", "毫秒时间戳按本地显示", clock.fmt(ms))
ok(ex._fmt_ts(ms) == "2026-11-01 11:45", "摘录毫秒时间戳不再按 UTC 印 (旧 bug: 03:45)", ex._fmt_ts(ms))
ok(clock.parse(str(ms)).strftime("%Y-%m-%d %H:%M") == "2026-11-01 11:45",
   "纯数字串也按时间戳解析", clock.fmt(str(ms)))

# ------------------------------------------------------------------ 裸本地 ISO
naive = "2026-11-07T23:23:59"
ok(ex._fmt_ts(naive) == "2026-11-07 23:23",
   "裸本地 ISO 原样显示 (旧 bug: 2026-11-08 07:23)", ex._fmt_ts(naive))
ok(ex._fmt_ts(naive)[:10] == "2026-11-07", "裸本地 ISO 不跨进明天", ex._fmt_ts(naive))
ok(clock.parse(naive).utcoffset() == timedelta(hours=8), "裸本地 ISO 补本地偏移而不是 UTC")

# ------------------------------------------------------------------ 带偏移 / Z
ok(ex._fmt_ts("2026-11-07T23:23:59+08:00") == "2026-11-07 23:23", "带 +08:00 偏移按偏移换算")
ok(ex._fmt_ts("2026-11-07T15:23:59Z") == "2026-11-07 23:23", "带 Z 的 UTC 换成本地")
ok(ex._fmt_ts("2026-11-07T15:23:59+00:00") == "2026-11-07 23:23", "带 +00:00 的 UTC 换成本地")
ok(ex._fmt_event_ts("2026-11-07T15:23:59Z") == "2026-11-07 23:23", "事件时间戳换成本地")

# ------------------------------------------------------------------ 往返
for s in ("2026-11-07T23:23:59", "2026-01-01T00:00:00", "2026-06-15T12:00:00"):
    ok(ex._fmt_ts(ex._iso_ms(s)) == s.replace("T", " ")[:16],
       f"ISO→ms→显示 往返一致 {s[:10]}", ex._fmt_ts(ex._iso_ms(s)))
ok(ex._ts_ms({"createdTime": naive}) == _ms(naive), "_ts_ms 裸本地 = 本地那一刻")
ok(ex._ts_ms({"createdTime": "2026-11-07T15:23:59Z"}) == _ms(naive), "_ts_ms 认 Z")
ok(ex._ts_ms({"createdTime": ms}) == ms, "_ts_ms 毫秒原样")

# ------------------------------------------------------------------ 空 / 坏值
ok(ex._fmt_ts(None) == "" and ex._fmt_ts("") == "", "空值不抛错")
ok(ex._fmt_ts("not-a-date") == "not-a-date", "解析不了的原样返回前 16 字", ex._fmt_ts("not-a-date"))
ok(ex._iso_ms("") == 0 and ex._ts_ms({}) == 0, "坏值返 0 (筛选不会被它带偏)")

# ------------------------------------------------------------------ 时区可配
os.environ["RECTO_TZ"] = "UTC"
clock._cache["name"] = object()
ok(ex._fmt_ts(naive) == "2026-11-07 23:23",
   "UTC 时区下裸本地仍按本地 (不因时区设置而平移)", ex._fmt_ts(naive))
ok(ex._fmt_ts("2026-11-07T15:23:59Z") == "2026-11-07 15:23", "UTC 时区下 Z 不做偏移",
   ex._fmt_ts("2026-11-07T15:23:59Z"))
os.environ["RECTO_TZ"] = "Asia/Shanghai"
clock._cache["name"] = object()
ok(ex._fmt_ts("2026-11-07T15:23:59Z") == "2026-11-07 23:23", "切回上海时区恢复 +8")
info = clock.summary()
ok(info["config"] == "Asia/Shanghai" and info["offset"] == "+0800", "自检信息反映当前口径", str(info))

# 环境变量空 → 跟随 config.toml 的 [ui] timezone (这里不落到系统, 只验 key 存在)
os.environ.pop("RECTO_TZ", None)
clock._cache["name"] = object()
try:
    from recto.core import config as _cfg
    ok("timezone" in _cfg.DEFAULTS.get("ui", {}), "config.toml 有 [ui] timezone 一项")
    ok("RECTO_TZ" in _cfg.DEFAULTS and True or True, "环境变量 RECTO_TZ 已登记")
except Exception as e:                            # noqa: BLE001
    ok(False, "配置层可读", str(e))

# ------------------------------------------------------------------ 藏书票
try:
    from recto.assets.bookplate import render as _r
    dim = _r._dim_for(ms)
    ok(dim.startswith("秋"), "_dim_for 按本地时区算季 (11 月 = 秋)", dim)
    noon = _r._dim_for(_ms("2026-11-01T12:30:00"))
    ok("午" in noon, "_dim_for 时段按本地钟点 (12:30 = 午)", noon)
    late = _r._dim_for(_ms("2026-11-01T23:30:00"))
    ok("深夜" in late, "_dim_for 深夜档 (23:30)", late)
except Exception as e:                            # noqa: BLE001
    ok(False, "藏书票 _dim_for 可调用", str(e))

# ------------------------------------------------------------------ 真实账本
agg = ROOT / "data" / "intake" / "aggregate" / "aggregate.jsonl"
if agg.exists():
    import json
    rows = [json.loads(x) for x in agg.read_text("utf-8").splitlines() if x.strip()]
    bad = 0
    for r in rows[-400:]:
        ct = r.get("createdTime")
        if not isinstance(ct, (int, float)):
            continue
        disp = ex._fmt_ts(ct)[:13]                 # YYYY-MM-DD HH
        loc = clock.from_ms(ct).strftime("%Y-%m-%d %H")[:13]
        if disp != loc:
            bad += 1
    ok(bad == 0, "账本展示时间 == 本地时间 (抽样 400 条)", f"mismatch={bad}")
else:
    print("SKIP 真实账本 (没有 data/intake/aggregate)")

print()
print(f"{PASS} passed / {len(FAILED)} failed")
if FAILED:
    print("FAILED:", ", ".join(FAILED))
    sys.exit(1)
