"""云端更靠前时「由读者决定」的测试。

覆盖:
  1. 边车标记 `local_wins_at` 的有效期
  2. 选「留在本地」→ 推送允许回拨 (覆盖更靠前的云端), 覆盖成功后解除标记
  3. 打开书只比不写 (`peek_book`), 跳/留两个动作分开
  4. 状态机不再把「推不上去」谎报成「同步中…」
  5. 阅读页那套界面 (选择条 / 两个按钮 / 端点) 都在

跑法: python3 tests/test_sync_choice.py
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = tempfile.mkdtemp(prefix="recto-syncchoice-test-")
LIB = Path(TMP) / "library"
LIB.mkdir(parents=True, exist_ok=True)
os.environ["RECTO_DATA_DIR"] = TMP
os.environ["RECTO_LIBRARY_DIR"] = str(LIB)
for _k in ("RECTO_SYNC_AUTO", "RECTO_SYNC_PULL_ON_OPEN", "RECTO_SYNC_STAMP",
           "RECTO_SYNC_PUSH_DEBOUNCE", "RECTO_SYNC_FLUSH_ON_HIDE"):
    os.environ.pop(_k, None)

from recto.core import sidecar                                # noqa: E402
from recto.services import reader_state, sync                 # noqa: E402
from recto.app import reader as reader_mod                    # noqa: E402
from recto.sources import cloud                               # noqa: E402

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
KEY = "choicekey"
ROW = {"key": KEY, "path": str(BOOK), "title": "示例书", "author": "",
       "id64": "A" * 64, "id32": "A" * 32, "name": "示例书", "total_count": 8,
       "bytes": 8192}

# ---- 受控替身: 不碰网络、不碰真几何 -------------------------------------
sync._link_by_key = lambda key: (dict(ROW) if key == KEY else None)   # type: ignore
sync.links = lambda refresh=False: [dict(ROW)]                        # type: ignore
sync._link_light = lambda key: (dict(ROW) if key == KEY else None)    # type: ignore
sync._geometry_of = lambda path: None                                 # type: ignore
sync.position_for = lambda geo, ch, off: f"{int(ch) + 1}_0_0$##3-0-{int(ch) + 1}-{int(off)}-0##$"  # type: ignore

PROG = {"percent": 0.20, "chapter": 2, "offset": 100, "complete": 0}
reader_state.get_book = lambda key: ({"path": str(BOOK), "title": "示例书",
                                     "author": "", "bytes": 8192}
                                     if key == KEY else {})           # type: ignore
reader_state.get_progress = lambda key: (dict(PROG) if key == KEY else {})  # type: ignore
reader_state.book_bytes = lambda key: 8192                            # type: ignore
SAVED: list = []


def fake_save(key, chapter, offset, percent, position="", **kw):
    SAVED.append((key, chapter, offset, percent, kw))
    PROG.update(percent=percent, chapter=chapter, offset=offset)
    return True


reader_state.save_progress = fake_save                                # type: ignore

UPLOADED: list = []


def fake_upload(auth, **kw):
    UPLOADED.append(kw)
    return {"code": 0}


cloud.upload_progress = fake_upload                                   # type: ignore

CLOUD = {"pct": 0.52, "max_pct": 0.52, "pos_chapter": 5, "pos_f3": 10,
         "finished": False, "total_count": 8}
sync._cloud_state = lambda auth, rows: {r["id64"] or r["id32"]: dict(CLOUD)
                                        for r in rows}                # type: ignore


def reset():
    UPLOADED.clear()
    SAVED.clear()
    PROG.update(percent=0.20, chapter=2, offset=100, complete=0)
    sync._PUSH_STATE.clear()
    sync._PEEK_CACHE.clear()
    sync._PULL_BOOK_AT.clear()
    sidecar.write(BOOK, {"cloud": {"local_wins_at": ""}})


print("\n[1] 边车标记 _local_wins")
reset()
check("无标记 → False", sync._local_wins(str(BOOK)) is False)
sidecar.touch_cloud(BOOK, local_wins_at=datetime.now().astimezone().isoformat(timespec="seconds"))
check("刚写 → True", sync._local_wins(str(BOOK)) is True)
old = (datetime.now().astimezone() - timedelta(hours=25)).isoformat(timespec="seconds")
sidecar.touch_cloud(BOOK, local_wins_at=old)
check("25 小时前 → False", sync._local_wins(str(BOOK)) is False)
sidecar.touch_cloud(BOOK, local_wins_at="不是时间")
check("坏时间 → False", sync._local_wins(str(BOOK)) is False)
check("空路径 → False", sync._local_wins("") is False)

print("\n[2] 推送: 默认挡回拨, 选过「留在本地」就允许覆盖")
reset()
res = sync.push_once(keys=[KEY])
det = (res.get("detail") or [{}])[0]
check("默认被 regress 挡住", str(det.get("skipped", "")).startswith("regress"),
      det)
check("默认一个请求都没发", UPLOADED == [], UPLOADED)

reset()
sidecar.touch_cloud(BOOK, local_wins_at=datetime.now().astimezone().isoformat(timespec="seconds"))
res = sync.push_once(keys=[KEY])
det = (res.get("detail") or [{}])[0]
check("有标记 → 推成功", int(res.get("pushed") or 0) == 1, res)
check("写的是本地位置", float(det.get("pct") or 0) == 0.2, det)
check("发了两个 key (32+64)", len(UPLOADED) == 2, len(UPLOADED))
check("覆盖完解除标记", not (sidecar.cloud_link(str(BOOK)) or {}).get("local_wins_at"),
      sidecar.cloud_link(str(BOOK)))

reset()
res = sync.push_once(keys=[KEY], force=True)
check("force 仍能直推", int(res.get("pushed") or 0) == 1, res)

print("\n[3] 状态机: 推不上去时别谎报「同步中」")
reset()
sync._PUSH_STATE[KEY] = {"ok": True, "cloud_ahead": True, "pending_pct": 0.2,
                         "cloud_pct": 0.52}
check("cloud_ahead 优先 → cloud-ahead", sync._state_of(KEY)["state"] == "cloud-ahead",
      sync._state_of(KEY))
sync._PUSH_STATE[KEY] = {"ok": None, "pending_pct": 0.2, "cloud_pct": 0.52}
check("本地落后 → cloud-ahead 而不是 dirty", sync._state_of(KEY)["state"] == "cloud-ahead",
      sync._state_of(KEY))
sync._PUSH_STATE[KEY] = {"ok": None, "pending_pct": 0.62, "cloud_pct": 0.52}
check("本地靠前 → dirty (真的在等推)", sync._state_of(KEY)["state"] == "dirty",
      sync._state_of(KEY))
sync._PUSH_STATE[KEY] = {"ok": None, "inflight": True, "pending_pct": 0.2}
check("正在推 → pushing", sync._state_of(KEY)["state"] == "pushing")

print("\n[4] peek_book: 只比不写")
reset()
CALLS = []


def fake_pull_once(**kw):
    CALLS.append(kw)
    if kw.get("dry_run"):
        if not kw.get("only_forward"):
            raise AssertionError("peek 必须用 only_forward 比较")
        return {"updated": 0, "detail": [{"book": "示例书", "dry": True, "pct": 0.52,
                                          "bytes": 8192, "chapter": 5, "offset": 10,
                                          "basis": "position"}]}
    fake_save(kw.get("keys") and KEY or KEY, 5, 10, 0.52)
    return {"updated": 1, "detail": [{"book": "示例书", "pct": 0.52, "chapter": 5,
                                      "offset": 10, "updated": True}]}


_real_pull_once = sync.pull_once
sync.pull_once = fake_pull_once                                       # type: ignore

out = sync.peek_book(KEY, force=True)
check("云端更靠前 → need_choice", out.get("need_choice") is True, out)
check("带 anchor (章/偏移)", (out.get("anchor") or {}).get("chapter") == 5, out)
check("带两端百分比", out.get("cloud_pct") == 0.52 and out.get("local_pct") == 0.2, out)
check("dry_run=True → 没写本地库", SAVED == [], SAVED)
check("peek 用了 dry_run", CALLS and CALLS[-1].get("dry_run") is True, CALLS)

CALLS.clear()
out2 = sync.peek_book(KEY)
check("节流内不重复打云端", CALLS == [], CALLS)
check("节流命中仍给结论", out2.get("need_choice") is True, out2)

reset()
sync._PEEK_CACHE.clear()
CALLS.clear()


def fake_pull_local_ahead(**kw):
    CALLS.append(kw)
    return {"updated": 0, "detail": [{"book": "示例书", "skipped": "local-ahead",
                                      "cloud": 0.31, "local": 0.60}]}


sync.pull_once = fake_pull_local_ahead                                # type: ignore
out = sync.peek_book(KEY, force=True)
check("本地更靠前 → 不要选", not out.get("need_choice"), out)
check("照样报出云端比例", out.get("cloud_pct") == 0.31, out)

print("\n[5] apply_cloud: 选了才写库")
reset()
sync._PEEK_CACHE.clear()
CALLS.clear()
sync.pull_once = fake_pull_once                                       # type: ignore
out = sync.apply_cloud(KEY)
check("only_forward=False (真的拉)", CALLS and CALLS[-1].get("only_forward") is False,
      CALLS)
check("写进了本地库", len(SAVED) == 1, SAVED)
check("返回 anchor", (out.get("anchor") or {}).get("chapter") is not None, out)

print("\n[6] keep_local: 留本地并覆盖云端")
reset()
sync._PEEK_CACHE.clear()
out = sync.keep_local(KEY)
check("覆盖成功后标记解除 (不会再无限回拨)",
      not (sidecar.cloud_link(str(BOOK)) or {}).get("local_wins_at"),
      sidecar.cloud_link(str(BOOK)))
check("立刻推了一次", len(UPLOADED) == 2, len(UPLOADED))
check("返回 ok", out.get("ok") is True, out)
check("状态变回已同步", (out.get("state") or {}).get("state") == "synced", out.get("state"))

reset()
out = sync.keep_local(KEY, push_now=False)
check("push_now=False 只标记不推", UPLOADED == [] and bool(
    (sidecar.cloud_link(str(BOOK)) or {}).get("local_wins_at")), out)

print("\n[7] 界面: 选择条 / 按钮 / 端点")
reset()
html = reader_mod._READER_HTML
check("有选择条容器", 'id="rv-choice"' in html)
check("两个按钮 (跳/留)", 'data-c="cloud"' in html and 'data-c="local"' in html)
check("有不选的出路说明", 'rv-choice-hint' in html)
js = (ROOT / "src/recto/app/ui/assets/reader.js").read_text(encoding="utf-8")
check("前端问而不是直接跳", "askCloudChoice" in js)
check("调 sync-choice 端点", "/sync-choice" in js)
auto = js.split("function autoPull()", 1)[1].split("function syncBoot", 1)[0]
check("autoPull 里不再有 jumpTo", "jumpTo" not in auto, auto[:200])
check("autoPull 拿到 need_choice 就问", "need_choice" in auto and "askCloudChoice" in auto)
css = (ROOT / "src/recto/app/ui/assets/reader.css").read_text(encoding="utf-8")
check("选择条有样式", ".rv-choice" in css)
py = (ROOT / "src/recto/app/reader.py").read_text(encoding="utf-8")
check("GET /sync 走 peek", "_sync.peek_book(" in py)
check("有 sync-choice 路由", '"sync-choice"' in py)
check("cloud → apply_cloud", "_sync.apply_cloud(" in py)
check("local → keep_local", "_sync.keep_local(" in py)

sync.pull_once = _real_pull_once                                      # type: ignore

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
