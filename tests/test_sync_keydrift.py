"""逐 key 漂移 —— App 回写把云端那条退回旧位置时, 必须补推回去。

背景 (真事): 2026-11-10 用户报「网页昨天确认写过的进度, App 拉过之后跳到了
别的地方」。查到的实况:

    本地 (网页)      第 190 篇 + 78691 字节 = 33.0710%
    云端 64 hex      recto  11-09 14:01  33.0710%   ← 我们推的, 还在
    云端 32 hex      11-10 08:27        32.9993%   ← App 读书时把它回写成自己的位置

掌阅把 32 hex (文件内容 MD5) 与 64 hex (云书架 book_id) 当**两份独立记录**,
而 App 只读它自己那个 32 hex。于是我们推上去的值在 App 侧被退回旧位置, 而
64 hex 上还留着我们那条 —— 旧实现用 "云端三份记录里最大的那个" 做闸门,
看到 64 hex == 本地就判 noop, **永远不会补推**, App 也就永远拉不到新位置。

这组测试钉住:
  1. `_cloud_state` 给出逐 key 现值 (`per_key`), 32/64 分开记
  2. 本地 == 某个 key、但另一个 key 落后 → why=key-drift, **只写落后的那条**
  3. 两条 key 都已是最新 → noop, 一条都不写
  4. 云端一条记录都没有 → 两条都写
  5. `_drift_keys` 只挑"本地没再动过"的书去核 (本地更靠前的交给收尾补推)
  6. `tick_once` 有独立的漂移核对步骤, 且按自己的周期走

跑法: python3 tests/test_sync_keydrift.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = tempfile.mkdtemp(prefix="recto-drift-test-")
LIB = Path(TMP) / "library"
LIB.mkdir(parents=True, exist_ok=True)
os.environ["RECTO_DATA_DIR"] = TMP
os.environ["RECTO_LIBRARY_DIR"] = str(LIB)
for _k in ("RECTO_SYNC_AUTO", "RECTO_SYNC_INTERVAL", "RECTO_SYNC_PUSH_TOL",
           "RECTO_SYNC_TICK_SETTLE", "RECTO_SYNC_TICK_SETTLE_SEC",
           "RECTO_SYNC_TICK_DRIFT", "RECTO_SYNC_TICK_DRIFT_SEC"):
    os.environ.pop(_k, None)

from recto.core import config, sidecar                        # noqa: E402
from recto.services import reader_state, sync                  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


ID64 = "A" * 64
ID32 = "A" * 32
BOOK = LIB / "示例书.epub"
BOOK.write_bytes(b"x" * 4096)
sidecar.set_cloud(BOOK, id64=ID64, id32=ID32, name="示例书", total_count=8)
KEY = "driftkey"
ROW = {"key": KEY, "path": str(BOOK), "title": "示例书", "author": "",
       "id64": ID64, "id32": ID32, "name": "示例书", "total_count": 8, "bytes": 4096}

PROG = {"percent": 0.33071038, "chapter": 190, "offset": 78691, "complete": 0}
STATE = {}

real_state = sync._cloud_state                     # 留一份真实现 (下面会被替身覆盖)

sync.links = lambda refresh=False: [dict(ROW)]                     # type: ignore
sync._link_light = lambda key: (dict(ROW) if key == KEY else None)  # type: ignore
sync.reader_state.get_progress = lambda key: (dict(PROG) if key == KEY else {})  # type: ignore
sync.reader_state.book_bytes = lambda key: 41907094                # type: ignore


class _Geo:
    """最小的几何替身: 只提供 push 需要的两个值 (目录章数 / 存在性)。"""
    total_count = 8
    total_bytes = 41907094
    catalog = 8


sync._geometry_of = lambda path: _Geo()                            # type: ignore
sync._cloud_state = lambda auth, rows: dict(STATE)                 # type: ignore
sync.load_zy_auth = lambda: {"usr": "u", "zysid": "z"}             # type: ignore

UPLOADS = []


class _FakeCloud:
    @staticmethod
    def upload_progress(auth, **kw):
        UPLOADS.append(kw)
        return {"ok": True}


sync.cloud = _FakeCloud                                            # type: ignore


def cloud_state(per_key, max_pct=None, total_count=8):
    """造一份 _cloud_state 的返回值 (形状与真实实现一致)。"""
    pcts = [v for v in per_key.values() if v is not None]
    return {
        ID64: {
            "pct": max(pcts) if pcts else None,
            "max_pct": max(pcts) if pcts else None,
            "finished": False,
            "total_count": total_count,
            "ts": 0.0,
            "src": "progress32",
            "per_key": dict(per_key),
            "records": len(pcts),
        }
    }


def set_pushed(pct):
    sidecar.touch_cloud(BOOK, last_push_pct=("" if pct is None else round(float(pct), 8)))


# ---------------------------------------------------------------- 1. per_key
print("== 1. _cloud_state 逐 key 记现值 ==")
RAW = {
    ID32: {"updateTime": "1794270447030",
           "readProcess": {"readPercent": "0.32999307",
                           "readPosition": "190_0_0$##3-0-190-48624-0##$",
                           "chapterId": "190", "totalCount": "588"}},
    ID64: {"updateTime": "1794204111254",
           "readProcess": {"readPercent": "0.33071038",
                           "readPosition": "190_0_0$##3-0-190-78691-0##$",
                           "chapterId": "189", "totalCount": "588"}},
}


class _CloudRead:
    @staticmethod
    def get_progress(auth, keys):
        return {k: RAW[k] for k in keys if k in RAW}


sync.cloud = _CloudRead                                            # type: ignore
sync._zy_notebook_pct = lambda force=False: {}                     # type: ignore
STATE.clear()
st = real_state({"usr": "u"}, [dict(ROW)])[ID64]
check("has per_key", isinstance(st.get("per_key"), dict), st.get("per_key"))
check("32 hex 记成 32", abs(st["per_key"].get("32", 0) - 0.32999307) < 1e-9, st["per_key"])
check("64 hex 记成 64", abs(st["per_key"].get("64", 0) - 0.33071038) < 1e-9, st["per_key"])
check("max_pct 取两者较大", abs(st["max_pct"] - 0.33071038) < 1e-9, st["max_pct"])
sync.cloud = _FakeCloud                                            # type: ignore

# ---------------------------------------------------------------- 2. 只补落后的 key
print("== 2. key-drift: 只写被 App 回写退回去的那条 ==")
STATE.clear()
STATE.update(cloud_state({"32": 0.32999307, "64": 0.33071038}))
sync._PUSH_STATE.clear()
UPLOADS.clear()
res = sync.push_once()
check("推了 1 本", res["pushed"] == 1, res)
check("为什么推: key-drift", res["detail"][0].get("why") == "key-drift", res["detail"][0])
check("只写 32 hex 这一条", [u["book_id"] for u in UPLOADS] == [ID32], UPLOADS)
check("写的是本地位置", abs(float(UPLOADS[0]["percent"]) - 0.33071038) < 1e-8, UPLOADS)
check("位置串用 (章-1) 口径", UPLOADS[0]["read_position"].startswith("190_0_0$##3-0-190-78691"),
      UPLOADS[0]["read_position"])
check("总章数照旧", UPLOADS[0]["total_count"] == "8", UPLOADS[0])

# ---------------------------------------------------------------- 3. 都在最新 → noop
print("== 3. 两条 key 都已是本地位置 → 一条都不写 ==")
STATE.clear()
STATE.update(cloud_state({"32": 0.33071038, "64": 0.33071038}))
sync._PUSH_STATE.clear()
UPLOADS.clear()
res = sync.push_once()
check("pushed=0", res["pushed"] == 0, res)
check("原因是 noop", res["detail"][0].get("skipped") == "noop", res["detail"][0])
check("没写云端", UPLOADS == [], UPLOADS)

# 只差一条 (32 落后但本地也落后) → 不该往回写
STATE.clear()
STATE.update(cloud_state({"32": 0.50000000, "64": 0.50000000}))
sync._PUSH_STATE.clear()
UPLOADS.clear()
res = sync.push_once()
check("云端更靠前 → 挡住 (不覆盖)", res["pushed"] == 0 and "regress" in str(res["detail"][0]),
      res["detail"][0])
check("没写云端 (2)", UPLOADS == [], UPLOADS)

# ---------------------------------------------------------------- 4. 云端空 → 两条都写
print("== 4. 云端一条记录都没有 → 两条都写 ==")
STATE.clear()
STATE.update(cloud_state({}))
sync._PUSH_STATE.clear()
UPLOADS.clear()
res = sync.push_once()
check("pushed=1", res["pushed"] == 1, res)
check("两条 key 都写", [u["book_id"] for u in UPLOADS] == [ID32, ID64], UPLOADS)

# ---------------------------------------------------------------- 5. _drift_keys
print("== 5. _drift_keys: 只挑本地没再动过的书 ==")
set_pushed(0.33071038)
PROG["percent"] = 0.33071038
sync._PUSH_STATE.clear()
check("本地 == 上次推送 → 进漂移核对", sync._drift_keys() == [KEY], sync._drift_keys())

PROG["percent"] = 0.34000000
check("本地更靠前 → 不进漂移核对 (交给收尾补推)", sync._drift_keys() == [], sync._drift_keys())

PROG["percent"] = 0.33071038
set_pushed(None)
check("从没推过 → 不进漂移核对 (交给收尾补推)", sync._drift_keys() == [], sync._drift_keys())

set_pushed(0.33071038)
sync._note_skip(KEY, 0.33071038, "noop")
check("同一位置刚被判定过 → 这轮不问", sync._drift_keys() == [], sync._drift_keys())
sync._PUSH_STATE.clear()

PROG["percent"] = 0.0
check("0 进度的书不问 (推个 0 没意义)", sync._drift_keys() == [], sync._drift_keys())
PROG["percent"] = 0.33071038
check("exclude 生效", sync._drift_keys(exclude=[KEY]) == [], sync._drift_keys(exclude=[KEY]))

# ---------------------------------------------------------------- 6. tick 里的漂移步骤
print("== 6. tick_once 有独立的漂移核对 ==")
CALLS = []


def fake_push_once(**kw):
    CALLS.append(kw)
    return {"pushed": 1, "skipped": 0, "failed": 0, "detail": []}


sync.push_once = fake_push_once                                    # type: ignore
from recto.services import notes, sessions                         # noqa: E402

notes.push = lambda keys=None, **kw: {"pushed": 0, "pushed_marks": 0, "deleted": 0,
                                      "skipped": 0, "errors": [], "books": []}      # type: ignore
notes.pull = lambda keys=None, **kw: {"added": 0, "updated": 0, "deleted": 0,
                                      "skipped": 0, "unchanged": 0, "books": []}    # type: ignore
sessions.push = lambda day=None, **kw: {"day": "today", "queued": 0, "written": 0,
                                        "errors": [], "books": []}                 # type: ignore
sync.reader_state.list_books = lambda q="": []                     # type: ignore
set_pushed(0.33071038)
PROG["percent"] = 0.33071038
sync._PUSH_STATE.clear()
sync._TICK_STATE.clear()
CALLS.clear()
out = sync.tick_once()
check("没有活跃书也做漂移核对", out.get("drift", {}).get("keys") == [KEY], out.get("drift"))
check("动作里记了 drift", "drift" in (out.get("actions") or []), out.get("actions"))
check("漂移核对开了回拨吗: 没开", not (CALLS and CALLS[-1].get("allow_rewind")), CALLS)

CALLS.clear()
out2 = sync.tick_once()
check("漂移核对按自己的周期走 (不每轮)", "drift" not in (out2 or {}), out2)

os.environ["RECTO_SYNC_TICK_DRIFT"] = "0"
config.get_config(reload=True)
sync._TICK_STATE.clear()
CALLS.clear()
out3 = sync.tick_once()
check("tick_drift=0 → 不做漂移核对", "drift" not in (out3 or {}) and CALLS == [],
      (out3, CALLS))
os.environ.pop("RECTO_SYNC_TICK_DRIFT")
config.get_config(reload=True)

print("\n" + "=" * 46)
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
