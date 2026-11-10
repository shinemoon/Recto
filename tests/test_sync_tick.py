"""定时同步 (心跳通道) 的测试。

翻页推送解决"读者动了"; 定时器解决"停在一页上看很久 / 加了划线没翻页 /
页面已经关了但时长还没推"。这一组钉住:

  1. 配置层: 默认开 / 周期 / 稀疏周期 / 设置页覆盖 / 关掉 = 0
  2. 选书: 只同步"最近读过的", 且必须跟云端有映射
  3. 省网络: 本地跟上次推上去的一样就不去云端问
  4. tick_once: 只前进 (绝不回拨) / 划线、时长各按自己的周期 / 没有活动就安静
  5. 生命周期: start_ticker 幂等 / tick_state 给设置页的字段
  6. 界面: 设置页那几行 + 控制台那行提示都在

跑法: python3 tests/test_sync_tick.py
"""
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = tempfile.mkdtemp(prefix="recto-tick-test-")
LIB = Path(TMP) / "library"
LIB.mkdir(parents=True, exist_ok=True)
os.environ["RECTO_DATA_DIR"] = TMP
os.environ["RECTO_LIBRARY_DIR"] = str(LIB)
for _k in ("RECTO_SYNC_AUTO", "RECTO_SYNC_INTERVAL", "RECTO_SYNC_TICK_NOTES",
           "RECTO_SYNC_TICK_TIME", "RECTO_SYNC_TICK_ACTIVE_MIN"):
    os.environ.pop(_k, None)

from recto.core import config, sidecar                        # noqa: E402
from recto.core import settings as st                         # noqa: E402
from recto.services import notes, reader_state, sessions, sync  # noqa: E402
from recto.app import console, settings_page                  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def raises(fn):
    try:
        fn()
        return ""
    except Exception as e:      # noqa: BLE001
        return str(e)


def iso_ago(seconds: float) -> str:
    return (datetime.now() - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------- 受控替身

BOOK = LIB / "示例书.epub"
BOOK.write_bytes(b"x" * 4096)
sidecar.set_cloud(BOOK, id64="A" * 64, id32="A" * 32, name="示例书", total_count=8)
KEY = "testkey"
ROW = {"key": KEY, "path": str(BOOK), "title": "示例书", "author": "",
       "id64": "A" * 64, "id32": "A" * 32, "name": "示例书", "total_count": 8,
       "bytes": 4096}

BOOKS = [{"key": KEY, "title": "示例书", "author": "", "path": str(BOOK),
          "percent": 0.41, "chapter": 3, "offset": 1200, "complete": 0,
          "updated_at": iso_ago(30)}]

sync.reader_state.list_books = lambda q="": [dict(b) for b in BOOKS]   # type: ignore
sync._link_light = lambda key: (dict(ROW) if key == KEY else None)     # type: ignore
reader_state.get_progress = lambda key: ({"percent": 0.41, "chapter": 3,
                                          "offset": 1200, "complete": 0}
                                         if key == KEY else {})        # type: ignore

PUSH_CALLS: list = []
NOTES_CALLS: list = []
TIME_CALLS: list = []


def fake_push_once(**kw):
    PUSH_CALLS.append(kw)
    return {"pushed": 1, "skipped": 0, "failed": 0,
            "detail": [{"pct": 0.41, "why": "forward", "ok": True}]}


def fake_notes_push(keys=None, **kw):
    NOTES_CALLS.append(("push", kw))
    return {"pushed": 1, "pushed_marks": 0, "deleted": 0, "skipped": 0,
            "errors": [], "books": []}


def fake_notes_pull(keys=None, **kw):
    NOTES_CALLS.append(("pull", kw))
    return {"added": 0, "updated": 0, "deleted": 0, "skipped": 0,
            "unchanged": 3, "books": []}


def fake_sessions_push(day=None, **kw):
    TIME_CALLS.append((day, kw))
    return {"day": (day or "today"), "queued": 1, "skipped": 0, "written": 1,
            "errors": [], "books": []}


sync.push_once = fake_push_once              # type: ignore
notes.push = fake_notes_push                 # type: ignore
notes.pull = fake_notes_pull                 # type: ignore
sessions.push = fake_sessions_push           # type: ignore


def reset_calls():
    PUSH_CALLS.clear()
    NOTES_CALLS.clear()
    TIME_CALLS.clear()
    with sync._PUSH_LOCK:
        sync._PUSH_STATE.clear()
    sync._TICK_STATE.update({"at": 0.0, "notes_at": 0.0, "time_at": 0.0,
                             "runs": 0, "last": None, "error": None})


def clear_calls():
    """只清记录, 不动"上一轮跑在什么时候" (验证稀疏周期要靠它)。"""
    PUSH_CALLS.clear()
    NOTES_CALLS.clear()
    TIME_CALLS.clear()


# ---------------------------------------------------------------- 1. 配置层

print("== 配置层 ==")
cfg = config.get_config(reload=True)
sec = cfg.section("sync")
check("配置项齐全", set(sec) >= {"auto", "interval_sec", "tick_active_min",
                                "tick_notes", "tick_notes_sec",
                                "tick_time", "tick_time_sec"}, sec)
check("默认每分钟一次心跳", int(sec["interval_sec"]) == 60, sec)
check("默认只同步最近 30 分钟读过的书", int(sec["tick_active_min"]) == 30, sec)
check("默认顺带同步划线 / 时长", sec["tick_notes"] is True and sec["tick_time"] is True, sec)
check("划线 / 时长各有稀疏周期", int(sec["tick_notes_sec"]) == 300
      and int(sec["tick_time_sec"]) == 600, sec)

tk = sync.tick_settings()
check("tick_settings 暴露节奏", set(tk) == {"interval_sec", "active_min", "notes",
      "notes_sec", "time", "time_sec", "settle", "settle_sec",
      "drift", "drift_sec"}, tk)
check("默认开收尾补推 (3 分钟一次)", tk["settle"] is True and int(tk["settle_sec"]) == 180, tk)
check("默认开漂移核对 (10 分钟一次)", tk["drift"] is True and int(tk["drift_sec"]) == 600, tk)
check("sync_settings 带 interval_sec", sync.sync_settings()["interval_sec"] == 60.0,
      sync.sync_settings())

st.update(sync_interval="on", sync_interval_sec="45")
config.get_config(reload=True)
check("设置页改周期生效", int(sync.tick_settings()["interval_sec"]) == 45,
      sync.tick_settings())

st.update(sync_interval="off")
config.get_config(reload=True)
check("关掉定时 = 周期 0", sync.tick_settings()["interval_sec"] == 0.0,
      sync.tick_settings())
check("关掉时 tick_state 说关了", sync.tick_state()["enabled"] is False, sync.tick_state())

st.update(sync_tick_notes="off", sync_tick_time="off")
config.get_config(reload=True)
check("单独关划线 / 时长", sync.tick_settings()["notes"] is False
      and sync.tick_settings()["time"] is False, sync.tick_settings())

st.clear("sync_interval", "sync_interval_sec", "sync_tick_notes", "sync_tick_time")
config.get_config(reload=True)
check("清空回到 config.toml", sync.tick_settings()["interval_sec"] == 60.0
      and sync.tick_settings()["notes"] is True, sync.tick_settings())

os.environ["RECTO_SYNC_INTERVAL"] = "0"
config.get_config(reload=True)
check("环境变量能关 (RECTO_SYNC_INTERVAL=0)",
      sync.tick_settings()["interval_sec"] == 0.0, sync.tick_settings())
os.environ.pop("RECTO_SYNC_INTERVAL")
config.get_config(reload=True)

# ---------------------------------------------------------------- 2. 选书

print("== 选哪几本书 ==")
check("刚读过的书入选", sync.active_keys(1800) == [KEY], sync.active_keys(1800))
check("窗口外的不算", sync.active_keys(10) == [], sync.active_keys(10))

BOOKS[0]["updated_at"] = iso_ago(3600)
check("一小时前读过的不算 (默认 30 分钟窗口)", sync.active_keys() == [],
      sync.active_keys())

BOOKS[0]["updated_at"] = iso_ago(5)
_had_link = sync._link_light
sync._link_light = lambda key: None            # 没有云端映射
check("没映射的书不同步", sync.active_keys() == [], sync.active_keys())
sync._link_light = _had_link

BOOKS[0]["updated_at"] = ""
check("没有进度时间的书不算", sync.active_keys() == [], sync.active_keys())
BOOKS[0]["updated_at"] = iso_ago(5)

check("时间戳解析: 本地朴素写法", abs(float(sync._iso_to_epoch("2026-11-06T22:31:05"))
      - time.mktime(datetime(2026, 11, 6, 22, 31, 5).timetuple())) < 1, "")
check("时间戳解析: 坏值不炸", sync._iso_to_epoch("???") is None, "")
check("时间戳解析: 空值", sync._iso_to_epoch("") is None, "")

# ---------------------------------------------------------------- 3. 省网络

print("== 本地没变化就不打云端 ==")
sidecar.touch_cloud(BOOK, last_push_pct=0.41)
check("本地 == 上次推上去的 → 跳过", sync._changed_keys([KEY]) == [],
      sync._changed_keys([KEY]))
sidecar.touch_cloud(BOOK, last_push_pct=0.30)
check("本地变了 → 要推", sync._changed_keys([KEY]) == [KEY], sync._changed_keys([KEY]))
sidecar.touch_cloud(BOOK, last_push_pct="")
check("从没推过 → 要推", sync._changed_keys([KEY]) == [KEY], sync._changed_keys([KEY]))

# ---------------------------------------------------------------- 4. tick_once

print("== tick_once ==")
reset_calls()
sidecar.touch_cloud(BOOK, last_push_pct=0.30)
out = sync.tick_once()
check("有活动 → 推一次进度", len(PUSH_CALLS) == 1
      and out["actions"] == ["progress", "notes", "time"], out)
check("推送只传这几本书", PUSH_CALLS and PUSH_CALLS[0].get("keys") == [KEY], PUSH_CALLS)
check("定时通道不打开回拨 (allow_rewind 缺省/False)",
      not PUSH_CALLS[0].get("allow_rewind", False), PUSH_CALLS)
check("顺带同步划线 + 时长", len(NOTES_CALLS) == 2 and len(TIME_CALLS) == 1,
      (NOTES_CALLS, TIME_CALLS))
check("划线走 quiet (不刷日志)", all(kw.get("quiet") for _, kw in NOTES_CALLS),
      NOTES_CALLS)
check("上一轮结果给了设置页", (sync._TICK_STATE.get("last") or {}).get("keys") == [KEY],
      sync._TICK_STATE.get("last"))

# 同一个周期内再跑: 进度没变 → 不打云端; 划线 / 时长各自的周期没到 → 也不再跑
clear_calls()
sidecar.touch_cloud(BOOK, last_push_pct=0.41)
out = sync.tick_once()
check("没有新进度 → 不推进度", PUSH_CALLS == [] and "push" not in out, out)
check("周期没到 → 划线 / 时长都不再跑", NOTES_CALLS == [] and TIME_CALLS == [],
      (NOTES_CALLS, TIME_CALLS))
check("空闲也照样记一次运行", sync.tick_state()["runs"] >= 1, sync.tick_state())

# 强制一次
clear_calls()
out = sync.tick_once(force=True)
check("force 绕过稀疏周期", len(NOTES_CALLS) == 2 and len(TIME_CALLS) == 1,
      (NOTES_CALLS, TIME_CALLS))

# 没有活动 = 安静
reset_calls()
BOOKS[0]["updated_at"] = iso_ago(7200)
out = sync.tick_once()
check("没在读 → 一本书都不碰", out.get("activity") == "idle" and PUSH_CALLS == [], out)
check("没在读 → 划线 / 时长也不跑", NOTES_CALLS == [] and TIME_CALLS == [], NOTES_CALLS)
BOOKS[0]["updated_at"] = iso_ago(5)

# 总开关关掉
reset_calls()
st.update(sync_auto="off")
config.get_config(reload=True)
out = sync.tick_once()
check("总开关关掉 → 什么都不做", out.get("skipped") == "auto-off" and PUSH_CALLS == [], out)
st.clear("sync_auto")
config.get_config(reload=True)

# ---------------------------------------------------------------- 5. 生命周期

print("== 生命周期与状态 ==")
reset_calls()
_t = sync._TICK_STATE.get("thread")
if _t is not None and getattr(_t, "is_alive", lambda: False)():
    sync.stop_ticker()
    _t.join(timeout=5)
check("start_ticker 起了一个线程", sync.start_ticker() is True, "")
check("再起一次不会重复", sync.start_ticker() is False, "")
check("线程活着且是 daemon", bool(sync._TICK_STATE["thread"].is_alive())
      and sync._TICK_STATE["thread"].daemon, "")

s = sync.tick_state()
check("tick_state 字段齐全", set(s) >= {"enabled", "interval_sec", "active_min",
      "notes", "notes_sec", "time", "time_sec", "running", "runs",
      "last_at", "last_keys", "last_actions", "last_activity", "error"}, s)
check("tick_state 说在跑", s["running"] is True, s)

sync.stop_ticker()
sync._TICK_STATE["thread"].join(timeout=8)
check("stop_ticker 能停 (分片查 stop, 不用等满一个周期)",
      not sync._TICK_STATE["thread"].is_alive(), "")

# ---------------------------------------------------------------- 6. 界面接线

print("== 界面 ==")
html = settings_page.page_html("root")
for name, frag in (("定时同步开关", 'id="sy-tick"'),
                   ("定时间隔输入框", 'id="sy-tick-sec"'),
                   ("划线开关", 'id="sy-tick-notes"'),
                   ("时长开关", 'id="sy-tick-time"'),
                   ("定时状态行", 'id="sy-tick-state"')):
    check(f"设置页有{name}", frag in html, "")

check("设置页说明提到两条通道", "翻页触发" in html and "定时触发" in html, "")
check("设置页 JS 收集 interval_on", "interval_on:" in settings_page._JS, "")
check("设置页 JS 画定时状态", "sy-tick-state" in settings_page._JS, "")

con = console._page_html("root")
check("控制台同步面板有定时提示行", 'id="tick-note"' in con, "")
check("控制台 JS 会渲染它", "function renderTick(" in con and 'renderTick(s.tick)' in con, "")

# API: 保存 + 回显 + 来源
out = settings_page.api_post({"sync": {"interval_on": True, "interval_sec": 30,
                                      "tick_notes": False, "tick_time": True}})
check("API: 保存定时设置", st.load().get("sync_interval") == "on"
      and st.load().get("sync_interval_sec") == "30"
      and st.load().get("sync_tick_notes") == "off", st.load())
check("API: 立刻生效", sync.tick_settings()["interval_sec"] == 30.0
      and sync.tick_settings()["notes"] is False, sync.tick_settings())
check("API: 回显 tick 块", out["sync"]["tick"]["interval_sec"] == 30
      and out["sync"]["tick"]["notes"] is False, out["sync"].get("tick"))
check("API: 来源标成设置页", out["sync"]["source"]["interval_sec"] == "settings",
      out["sync"]["source"])

check("API: 间隔越界报错", "0–3600" in raises(
    lambda: settings_page.api_post({"sync": {"interval_sec": 99999}})), "")
check("API: 非数字报错", "数字" in raises(
    lambda: settings_page.api_post({"sync": {"interval_sec": "abc"}})), "")

out = settings_page.api_post({"sync": {"interval_on": False, "interval_sec": 30}})
check("API: 关掉 → 周期 0", sync.tick_settings()["interval_sec"] == 0.0
      and st.load().get("sync_interval") == "off", st.load())

out = settings_page.api_post({"sync": None})
check("API: 传 null 回到 config.toml", sync.tick_settings()["interval_sec"] == 60.0,
      sync.tick_settings())
check("API: 覆盖项清干净", not st.load().get("sync_interval")
      and not st.load().get("sync_interval_sec"), st.load())

# 服务启动时真的会起心跳
src = (ROOT / "src/recto/app/server.py").read_text("utf-8")
check("服务启动时起心跳", "_start_ticker()" in src and "start_ticker" in src, "")

print()
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
