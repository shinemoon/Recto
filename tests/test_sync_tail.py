"""推送"尾巴"的测试 —— 读者看到的位置必须等于云端的位置。

背景 (真事): 2026-11-08 用户报「推送频次偏低, 在 web 看过的进度, 掌阅经常
没有推到位」。查到两个真原因:

  1. `push_decision` 的容差 tol=0.0005 (0.05%)。40MB 的书就是 20KB ——
     读者最后那一小截 (几百字) 永远落在容差里, 判 noop, 一次都不写。
     云端于是停在半路, 下次打开书又被拉回半路。
     实测: 本地 32.804088% vs 上次推送 32.796910%, 差 0.0072% → noop。
  2. 定时器只照顾「最近 30 分钟读过的书」。合上书走了之后, 尾巴若因节流 /
     网络抖动 / 被闸门挡过一次而没写上去, 就再没有第二次机会。

这一组钉住:
  1. `_push_tol` 默认几乎精确, 且可用 `[sync] push_tol` / 环境变量调
  2. `push_decision` 在这个容差下: 极小前进也算 forward, 持平仍 noop, 回退仍挡
  3. `_tail_keys`: 不看活跃窗口 / 只认前进 / 被挡过的同一位置不再问
  4. `tick_once` 有独立的「收尾补推」步骤, 且不计入活跃书
  5. `_changed_keys` 只认前进 (心跳永不回拨)
  6. 端到端: 一个 0.007% 的尾巴真的会被写进云端

跑法: python3 tests/test_sync_tail.py
"""
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = tempfile.mkdtemp(prefix="recto-tail-test-")
LIB = Path(TMP) / "library"
LIB.mkdir(parents=True, exist_ok=True)
os.environ["RECTO_DATA_DIR"] = TMP
os.environ["RECTO_LIBRARY_DIR"] = str(LIB)
for _k in ("RECTO_SYNC_AUTO", "RECTO_SYNC_INTERVAL", "RECTO_SYNC_PUSH_TOL",
           "RECTO_SYNC_TICK_SETTLE", "RECTO_SYNC_TICK_SETTLE_SEC",
           "RECTO_SYNC_TICK_ACTIVE_MIN"):
    os.environ.pop(_k, None)

from recto.core import config, sidecar                          # noqa: E402
from recto.services import reader_state, sync                   # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


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

PROG = {"percent": 0.41, "chapter": 3, "offset": 1200, "complete": 0}

sync.links = lambda refresh=False: [dict(ROW)]                    # type: ignore
sync._link_light = lambda key: (dict(ROW) if key == KEY else None)  # type: ignore
sync.reader_state.get_progress = lambda key: (dict(PROG) if key == KEY else {})  # type: ignore


def set_pushed(pct):
    """把边车上"上次推上去的"设成 pct (None = 从没推过)。"""
    if pct is None:
        sidecar.touch_cloud(BOOK, last_push_pct="")
    else:
        sidecar.touch_cloud(BOOK, last_push_pct=round(float(pct), 8))


def set_prog(pct):
    PROG["percent"] = pct


# ---------------------------------------------------------------- 1. 容差
print("== 1. 容差默认几乎精确 ==")
check("默认 1e-6", sync._push_tol() == 1e-6, sync._push_tol())
os.environ["RECTO_SYNC_PUSH_TOL"] = "0.002"
config.get_config(reload=True)
check("环境变量能调大", abs(sync._push_tol() - 0.002) < 1e-12, sync._push_tol())
os.environ["RECTO_SYNC_PUSH_TOL"] = "0"
config.get_config(reload=True)
check("0 退回默认 (不让它变成 0 就每轮都推)", sync._push_tol() == 1e-6, sync._push_tol())
os.environ["RECTO_SYNC_PUSH_TOL"] = "abc"
config.get_config(reload=True)
check("坏值退回默认", sync._push_tol() == 1e-6, sync._push_tol())
os.environ.pop("RECTO_SYNC_PUSH_TOL")
config.get_config(reload=True)

print("== 2. 这么小的前进必须推 ==")
tol = sync._push_tol()
pd = sync.push_decision
check("0.007% 的尾巴 (真事故那条) 算 forward",
      pd(0.32796910, False, 0.32804088, False, tol=tol) == (True, "forward"),
      pd(0.32796910, False, 0.32804088, False, tol=tol))
check("一个字的差也算 forward",
      pd(0.41, False, 0.41 + 2e-6, False, tol=tol)[0] is True)
check("完全持平仍是 noop (不空写)", pd(0.41, False, 0.41, False, tol=tol) == (False, "noop"))
check("float32 噪声 (1e-8) 不写", pd(0.41000004, False, 0.41, False, tol=tol)[0] is False,
      pd(0.41000004, False, 0.41, False, tol=tol))
check("回退照样挡 (没人动过)", pd(0.5, False, 0.3, False, tol=tol)[0] is False)
check("读者自己动的回退放行", pd(0.5, False, 0.3, False, tol=tol, allow_rewind=True)[0] is True)
check("旧容差 5e-4 会把那条尾巴判 noop (回归证据)",
      pd(0.32796910, False, 0.32804088, False, tol=0.0005) == (False, "noop"))

# ---------------------------------------------------------------- 3. 尾巴清单
print("== 3. _tail_keys 不看活跃窗口 ==")
set_pushed(0.41)
set_prog(0.41)
check("一致 → 没尾巴", sync._tail_keys() == [], sync._tail_keys())

set_prog(0.41005)
check("一点点前进 → 有尾巴", sync._tail_keys() == [KEY], sync._tail_keys())

set_prog(0.41)
check("又退回去了 → 不算尾巴 (心跳不回拨)", sync._tail_keys() == [], sync._tail_keys())

set_prog(0.41005)
check("exclude 里的书被跳过", sync._tail_keys(exclude=[KEY]) == [], sync._tail_keys(exclude=[KEY]))

set_pushed(None)
check("从没推过 → 算尾巴", sync._tail_keys() == [KEY], sync._tail_keys())

set_prog(0.0)
check("从没推过且 0 进度 → 不推 (推个 0 上去没意义)", sync._tail_keys() == [], sync._tail_keys())

set_pushed(0.41)
set_prog(0.41005)
sync._PUSH_STATE.clear()
sync._note_skip(KEY, 0.41005, "regress(0.4100<0.5000)")
check("被闸门挡过的同一位置 → 不再问云端", sync._tail_keys() == [], sync._tail_keys())
set_prog(0.42000)
check("位置变了 → 重新考虑", sync._tail_keys() == [KEY], sync._tail_keys())
sync._note_pushed(KEY)
check("推成功后清掉标记", sync._tail_keys() == [KEY], sync._tail_keys())
sync._PUSH_STATE.clear()

# ---------------------------------------------------------------- 4. _changed_keys
print("== 4. _changed_keys 只认前进 ==")
set_pushed(0.41)
set_prog(0.41)
check("一致 → 不用推", sync._changed_keys([KEY]) == [], sync._changed_keys([KEY]))
set_prog(0.41005)
check("前进一点 → 要推", sync._changed_keys([KEY]) == [KEY], sync._changed_keys([KEY]))
set_prog(0.35)
check("落后于上次推送 → 心跳不管 (交给翻页通道)", sync._changed_keys([KEY]) == [],
      sync._changed_keys([KEY]))
set_pushed(None)
set_prog(0.41005)
check("从没推过 → 要推", sync._changed_keys([KEY]) == [KEY], sync._changed_keys([KEY]))

# ---------------------------------------------------------------- 5. tick 里的收尾补推
print("== 5. tick_once 的收尾补推 ==")
CALLS = []


def fake_push_once(**kw):
    CALLS.append(kw)
    return {"pushed": 1, "skipped": 0, "failed": 0, "detail": []}


REAL_PUSH = sync.push_once          # 第 6 段要用真的, 先留一份
sync.push_once = fake_push_once                     # type: ignore
from recto.services import notes, sessions          # noqa: E402

notes.push = lambda keys=None, **kw: {"pushed": 0, "pushed_marks": 0, "deleted": 0,
                                      "skipped": 0, "errors": [], "books": []}      # type: ignore
notes.pull = lambda keys=None, **kw: {"added": 0, "updated": 0, "deleted": 0,
                                      "skipped": 0, "unchanged": 0, "books": []}    # type: ignore
sessions.push = lambda day=None, **kw: {"day": "today", "queued": 0, "written": 0,
                                        "errors": [], "books": []}                 # type: ignore

# 活跃窗口里没有它: 一小时前读过
sync.reader_state.list_books = lambda q="": [{"key": KEY, "title": "示例书", "path": str(BOOK),
                                              "percent": 0.41005, "chapter": 3,
                                              "offset": 1200, "complete": 0,
                                              "updated_at": iso_ago(3600)}]         # type: ignore
set_pushed(0.41)
set_prog(0.41005)
sync._PUSH_STATE.clear()
sync._TICK_STATE.clear()
CALLS.clear()
out = sync.tick_once()
check("活跃窗口里没有 → 心跳不推它", out.get("keys") == [], out.get("keys"))
check("但收尾补推把它补上了", out.get("settle", {}).get("pushed") == 1
      and out.get("settle", {}).get("keys") == [KEY], out.get("settle"))
check("动作里记了 settle", "settle" in (out.get("actions") or []), out.get("actions"))
check("补推传的是这本书", CALLS and CALLS[-1].get("keys") == [KEY], CALLS)
check("补推不开回拨", not CALLS[-1].get("allow_rewind", False), CALLS[-1])

# 收尾补推按自己的周期走 (180s), 不是每轮心跳都问
CALLS.clear()
out2 = sync.tick_once()
check("同一周期内不重复补推", "settle" not in (out2 or {}), out2)

# 活跃书已经在进度步骤里推过, 不该在补推里再来一次
sync.reader_state.list_books = lambda q="": [{"key": KEY, "title": "示例书", "path": str(BOOK),
                                              "percent": 0.41005, "chapter": 3,
                                              "offset": 1200, "complete": 0,
                                              "updated_at": iso_ago(20)}]          # type: ignore
sync._TICK_STATE.clear()
CALLS.clear()
out3 = sync.tick_once()
check("活跃书只推一次", len(CALLS) == 1 and CALLS[0].get("keys") == [KEY], CALLS)
check("活跃书不重复出现在补推里", "settle" not in (out3 or {}), out3)

# 关掉收尾补推
os.environ["RECTO_SYNC_TICK_SETTLE"] = "0"
config.get_config(reload=True)
sync.reader_state.list_books = lambda q="": [{"key": KEY, "title": "示例书", "path": str(BOOK),
                                              "percent": 0.42, "chapter": 3,
                                              "offset": 1300, "complete": 0,
                                              "updated_at": iso_ago(3600)}]        # type: ignore
set_prog(0.42)
sync._TICK_STATE.clear()
CALLS.clear()
out4 = sync.tick_once()
check("tick_settle=0 → 不做补推", "settle" not in (out4 or {}), out4)
check("关掉后活跃窗口外的书确实没被推", CALLS == [], CALLS)
os.environ.pop("RECTO_SYNC_TICK_SETTLE")
config.get_config(reload=True)

# ---------------------------------------------------------------- 6. 端到端: 尾巴真写进云端
print("== 6. 端到端 (假云端): 尾巴落地 ==")
import recto.services.sync as sync_mod                       # noqa: E402

sync_mod.push_once = REAL_PUSH                               # 换回真实现

UPLOADS = []


class _FakeCloud:
    @staticmethod
    def upload_progress(auth, **kw):
        UPLOADS.append(kw)
        return {"ok": True}


_real_cloud = sync_mod.cloud
sync_mod.cloud = _FakeCloud                                  # type: ignore
sync_mod.load_zy_auth = lambda: {"usr": "u", "zysid": "z"}    # type: ignore
def _fake_state(auth, rows):
    """云端现值 = 上一次写上去的 (真实云端也是这样回读的)。"""
    pct = float(UPLOADS[-1]["percent"]) if UPLOADS else 0.32796910
    return {"A" * 64: {"pct": pct, "max_pct": pct, "finished": False,
                       "total_count": 8, "chapter_id": "3", "ts": time.time(),
                       "src": "progress64", "records": 2}}


sync_mod._cloud_state = _fake_state                          # type: ignore
set_pushed(0.32796910)
set_prog(0.32804088)
UPLOADS.clear()
res = sync_mod.push_once(keys=[KEY])
check("0.007% 的尾巴这次真的写了", res.get("pushed") == 1, res)
check("写上去的就是读者的位置",
      UPLOADS and abs(float(UPLOADS[-1]["percent"]) - 0.32804088) < 1e-9, UPLOADS)
check("边车记住新位置", abs(float((sidecar.cloud_link(BOOK) or {}).get("last_push_pct"))
                           - 0.32804088) < 1e-9, sidecar.cloud_link(BOOK))

# 同一个位置再推一次 → noop (不会空写云端)
_n_before = len(UPLOADS)
res2 = sync_mod.push_once(keys=[KEY])
check("同位置再推 → noop, 不打云端",
      res2.get("skipped") == 1 and len(UPLOADS) == _n_before, res2)

# 云端更靠前 → 挡, 并记住这个位置
sync_mod._cloud_state = lambda auth, rows: {                  # type: ignore
    "A" * 64: {"pct": 0.9, "max_pct": 0.9, "finished": False, "total_count": 8,
               "chapter_id": "5", "ts": time.time(), "src": "progress64", "records": 2},
}
set_prog(0.5)
sync._PUSH_STATE.clear()
UPLOADS.clear()
res3 = sync_mod.push_once(keys=[KEY])
check("云端更靠前 → 挡", res3.get("skipped") == 1 and not UPLOADS, res3)
check("挡住的位置被记住 (补推不再白问)", sync._skip_known(KEY, 0.5) is True,
      sync._PUSH_STATE.get(KEY))

sync_mod.cloud = _real_cloud                                  # type: ignore

print()
print("=" * 60)
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
