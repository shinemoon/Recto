"""读者自己动过的位置要能覆写云端 (push 侧允许回退)。

背景: 云端 32.8%、本地 31.9% 时旧逻辑把推送判成 `regress` 挡掉 ——
读者在本地怎么翻页/跳转, 云端都不动, 下次打开又被拽回云端那个位置。
掌阅 App 自己的语义是"最后写入者胜", 所以读者**自己动的**位置必须如实写上去。

覆盖:
  1. push_decision: 回退放行 / 无人动仍挡回 / 云端刚更新过先让一让
  2. _cloud_is_newer: 秒/毫秒、缺字段、时钟容差
  3. mirror_reader_progress(user=) 打标 → _push_worker 消费 → push_once(allow_rewind)
  4. 标记是一次性的 (推完就清, 不会让后面无脑回拨)
  5. user=False (系统补写) 仍旧只前进
  6. 状态机 rewound 标记 + 前端/控制台文案

跑法: python3 tests/test_push_rewind.py
"""
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = tempfile.mkdtemp(prefix="recto-rewind-test-")
LIB = Path(TMP) / "library"
LIB.mkdir(parents=True, exist_ok=True)
os.environ["RECTO_DATA_DIR"] = TMP
os.environ["RECTO_LIBRARY_DIR"] = str(LIB)

from recto.core import sidecar                                  # noqa: E402
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


BOOK = LIB / "示例书.epub"
BOOK.write_bytes(b"y" * 8192)
sidecar.set_cloud(BOOK, id64="A" * 64, id32="A" * 32, name="示例书", total_count=8)
KEY = "rewindkey"
ROW = {"key": KEY, "path": str(BOOK), "title": "示例书", "author": "",
       "id64": "A" * 64, "id32": "A" * 32, "name": "示例书", "total_count": 8,
       "bytes": 8192}

sync._link_by_key = lambda key: (dict(ROW) if key == KEY else None)     # type: ignore
sync.links = lambda refresh=False: [dict(ROW)]                         # type: ignore
sync._link_light = lambda key: (dict(ROW) if key == KEY else None)      # type: ignore
sync._geometry_of = lambda path: None                                   # type: ignore
sync.position_for = lambda geo, ch, off: ""                             # type: ignore
reader_state.book_bytes = lambda key: 8192                              # type: ignore
reader_state.get_book = lambda key: {"path": str(BOOK), "title": "示例书",
                                     "author": "", "bytes": 8192}       # type: ignore

LOCAL_TS = datetime.now().astimezone() - timedelta(minutes=5)
PROG = {"percent": 0.319, "chapter": 183, "offset": 91786, "complete": 0,
        "updated_at": LOCAL_TS.isoformat(timespec="seconds")}
reader_state.get_progress = lambda key: (dict(PROG) if key == KEY else {})   # type: ignore

CLOUD = {"pct": 0.3287, "max_pct": 0.3287, "pos_chapter": 188, "pos_f3": 1,
         "finished": False, "total_count": 8,
         "ts": (LOCAL_TS + timedelta(minutes=1)).timestamp() * 1000.0}
sync._cloud_state = lambda auth, rows: {r["id64"] or r["id32"]: dict(CLOUD)
                                        for r in rows}                   # type: ignore

UPLOADED: list = []
cloud_mod = sync.cloud
cloud_mod.upload_progress = lambda auth, **kw: (UPLOADED.append(kw), {"code": 0})[1]  # type: ignore

print("\n[1] push_decision 决策矩阵")
pd = sync.push_decision
check("默认挡回退", pd(0.33, False, 0.31, False)[0] is False,
      pd(0.33, False, 0.31, False))
check("默认挡回退 (理由)", pd(0.33, False, 0.31, False)[1].startswith("regress"))
check("读者动过 → 放行", pd(0.33, False, 0.31, False, allow_rewind=True) ==
      (True, "rewind(0.3100<0.3300)"), pd(0.33, False, 0.31, False, allow_rewind=True))
check("读者动过 + 云端刚更新 → 仍挡",
      pd(0.33, False, 0.31, False, allow_rewind=True, cloud_newer=True)[0] is False,
      pd(0.33, False, 0.31, False, allow_rewind=True, cloud_newer=True))
check("云端刚更新 → 理由写 cloud-newer",
      pd(0.33, False, 0.31, False, allow_rewind=True,
         cloud_newer=True)[1].startswith("cloud-newer"))
check("往前推不受影响", pd(0.31, False, 0.35, False, allow_rewind=True) ==
      (True, "forward"))
check("一致仍算 noop", pd(0.31, False, 0.31, False, allow_rewind=True)[0] is False)
check("读完/首次推送语义不变",
      pd(None, False, 0.5, False)[0] is True and
      pd(0.2, True, 0.1, False)[1] == "cloud-already-done")

print("\n[2] _cloud_is_newer (秒/毫秒 + 时钟容差)")
prog_now = {"updated_at": datetime.now().astimezone().isoformat(timespec="seconds")}
now_ms = time.time() * 1000.0
check("云端毫秒时间更新 → True", sync._cloud_is_newer({"ts": now_ms + 600000}, prog_now) is True)
check("云端秒级时间更新 → True",
      sync._cloud_is_newer({"ts": (now_ms + 600000) / 1000.0}, prog_now) is True)
check("云端时间更旧 → False", sync._cloud_is_newer({"ts": now_ms - 900000}, prog_now) is False)
check("容差内不算新 (skew)", sync._cloud_is_newer({"ts": now_ms + 60_000}, prog_now) is False)
check("缺 ts → False", sync._cloud_is_newer({}, prog_now) is False)
check("缺本地时间 → False", sync._cloud_is_newer({"ts": now_ms}, {}) is False)
check("坏时间串 → False",
      sync._cloud_is_newer({"ts": now_ms + 600000}, {"updated_at": "不是时间"}) is False)

print("\n[3] push_once: allow_rewind 才写得更靠后")
reset_state = lambda: (UPLOADED.clear(), sync._PUSH_STATE.clear())      # noqa: E731

reset_state()
CLOUD["ts"] = (LOCAL_TS + timedelta(minutes=1)).timestamp() * 1000.0
res = sync.push_once(keys=[KEY])
check("无人动 → 被挡", int(res.get("skipped") or 0) == 1 and not UPLOADED, res)
check("挡的理由是 regress", str((res["detail"][0]).get("skipped")).startswith("regress"), res)

reset_state()
res = sync.push_once(keys=[KEY], allow_rewind=True)
check("读者动过 → 推成功", int(res.get("pushed") or 0) == 1, res)
check("写的是本地那个更靠后的位置", UPLOADED and UPLOADED[0]["percent"].startswith("0.319"),
      UPLOADED[0] if UPLOADED else None)
check("detail 里 why = rewind",
      str((res["detail"][0]).get("why")).startswith("rewind"), res["detail"])

reset_state()
CLOUD["ts"] = time.time() * 1000.0 + 600000.0          # 手机刚读过 10 分钟前
res = sync.push_once(keys=[KEY], allow_rewind=True)
check("云端刚更新过 → 先不覆盖", int(res.get("pushed") or 0) == 0, res)
check("理由 cloud-newer",
      str((res["detail"][0]).get("skipped")).startswith("cloud-newer"), res["detail"])

print("\n[4] 打标 → 消费 → 一次性")
reset_state()
CLOUD["ts"] = (LOCAL_TS + timedelta(minutes=1)).timestamp() * 1000.0
CALLS: list = []
real_push_once = sync.push_once


def spy_push_once(**kw):
    CALLS.append(kw)
    return real_push_once(**kw)


sync.push_once = spy_push_once                                          # type: ignore

reader_state.get_progress = lambda key: (dict(PROG) if key == KEY else {})  # type: ignore
sync._PUSH_STATE[KEY] = {"at": 0}          # 保证第一次不触发节流
r = sync.mirror_reader_progress(KEY, 0.319, flush=True, user=True)
for _ in range(60):
    if not (sync._PUSH_STATE.get(KEY) or {}).get("inflight"):
        break
    time.sleep(0.05)
check("读者保存 → 推送带了 allow_rewind", CALLS and CALLS[-1].get("allow_rewind") is True, CALLS)
check("标记推完就清", not (sync._PUSH_STATE.get(KEY) or {}).get("user"),
      sync._PUSH_STATE.get(KEY))
check("这次真的写上了", len(UPLOADED) == 2, UPLOADED)   # 32 + 64 两个 key

CALLS.clear()
UPLOADED.clear()
sync._PUSH_STATE[KEY].pop("at", None)
sync._PUSH_STATE[KEY]["at"] = 0
r = sync.mirror_reader_progress(KEY, 0.319, flush=True, user=False)
for _ in range(60):
    if not (sync._PUSH_STATE.get(KEY) or {}).get("inflight"):
        break
    time.sleep(0.05)
check("user=False (系统补写) → 不带 allow_rewind", CALLS and CALLS[-1].get("allow_rewind") is False,
      CALLS)
check("系统补写没写云端", UPLOADED == [], UPLOADED)

sync.push_once = real_push_once                                          # type: ignore

print("\n[5] 状态机 / 文案")
sync._PUSH_STATE[KEY] = {"ok": True, "why": "rewind(0.3190<0.3287)",
                         "at": time.time(), "pct": 0.319}
st = sync._state_of(KEY)
check("推完标 rewound", st.get("rewound") is True, st)
check("状态是 synced", st.get("state") == "synced", st)
sync._PUSH_STATE[KEY] = {"ok": True, "why": "forward", "at": time.time()}
check("普通前进不标 rewound", sync._state_of(KEY).get("rewound") is False,
      sync._state_of(KEY))

js = (ROOT / "src/recto/app/ui/assets/reader.js").read_text(encoding="utf-8")
check("阅读页小字有「已按本地覆写」", "已按本地覆写" in js)
check("保存进度带 user=1 (默认)", "user: opts.user === false ? 0 : 1" in js)
check("关页面补推也带 user=1", "flush: 1, user: 1" in js)
check("主动跳转打 jumpIntent", "jumpIntent: false" in js and "S.jumpIntent = true" in js)
check("目录点击走 jumpTo (不再丢意图)", "jumpTo(c.i, 0);" in js)
check("saveProgress 把 jumpIntent 传给后端", "jump: jump," in js)

console = (ROOT / "src/recto/app/console.py").read_text(encoding="utf-8")
check("控制台能翻译 rewind", "按本地覆写云端" in console)
check("控制台能翻译 cloud-newer", "这次先不覆盖" in console)

rd = (ROOT / "src/recto/app/reader.py").read_text(encoding="utf-8")
check("reader API 把 user 传给 mirror", 'user=bool(body.get("user", 1))' in rd)

print(f"\n{'=' * 46}\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
