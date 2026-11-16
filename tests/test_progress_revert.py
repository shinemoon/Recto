"""「读到一半放置, 刷新却跳回早先位置」的回归测试。

2026-11-16 用户报: web 端往后读了几页, 放置半小时, 刷新又回到早先的在线进度。
拆开是两件事:

  A. 本地进度被**别的会话**写回去了 —— 旧闸门只挡"回退超过 5%",
     而 1% 在一本 1MB 的书里已经是几十段正文 (15.3% → 14% 就这么过去的)。
     现在: 跨会话回退一格都挡, 只有"这条进度之后才打开过书"的那个会话能往回翻。

  B. 本地明明比云端靠前, 但推送被闸门挡住/失败了, 界面上没有任何提示。
     现在: 打开书时两边差得够多 → `need_choice` 交给读者 (`choice_kind=local-unpushed`)。

跑法: python3 tests/test_progress_revert.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = tempfile.mkdtemp(prefix="recto-revert-test-")
LIB = Path(TMP) / "library"
LIB.mkdir(parents=True, exist_ok=True)
os.environ["RECTO_DATA_DIR"] = TMP
os.environ["RECTO_LIBRARY_DIR"] = str(LIB)
os.environ["RECTO_AUTH_LOG"] = "off"
for _k in ("RECTO_SYNC_AUTO", "RECTO_SYNC_PULL_ON_OPEN", "RECTO_SYNC_STAMP"):
    os.environ.pop(_k, None)

from recto.core import sidecar                                # noqa: E402
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


print("\n[1] 跨会话回退: 一格都挡")
reader_state.save_progress("k1", 8, 84163, 0.1530, "", sid="tab-now")
p = reader_state.save_progress("k1", 8, 68175, 0.1400, "", sid="tab-old")
check("旧标签页回退 1.3% → 挡下", p.get("skipped") == "stale-rewind", p)
check("库里还是 15.30%", abs(reader_state.get_progress("k1")["percent"] - 0.1530) < 1e-9,
      reader_state.get_progress("k1"))
check("库里还是那个会话", reader_state.get_progress("k1").get("sid") == "tab-now",
      reader_state.get_progress("k1"))

p = reader_state.save_progress("k1", 8, 68175, 0.1400, "", sid="tab-now")
check("同一会话往回翻 → 放行", p.get("skipped") is None and abs(p["percent"] - 0.1400) < 1e-9, p)

p = reader_state.save_progress("k1", 8, 68175, 0.1400, "", sid="tab-x", allow_rewind=True)
check("jump (目录/上一章) → 放行", p.get("skipped") is None, p)

p = reader_state.save_progress("k1", 8, 90000, 0.1700, "", sid="tab-y")
check("跨会话**前进**不受影响", p.get("skipped") is None and abs(p["percent"] - 0.1700) < 1e-9, p)

p = reader_state.save_progress("k1", 8, 80000, 0.1500, "")
check("不带 sid 的写者 (CLI/同步) 不被挡", p.get("skipped") is None, p)
check("不带 sid 也不抹掉已有 sid", reader_state.get_progress("k1").get("sid") == "tab-y",
      reader_state.get_progress("k1"))

print("\n[2] 例外: 这条进度之后才打开的会话 = 当前现场")
reader_state.save_progress("k2", 8, 90000, 0.1700, "", sid="tab-first")
check("没打开过的会话仍被挡",
      reader_state.save_progress("k2", 8, 100, 0.05, "", sid="tab-late").get("skipped")
      == "stale-rewind")
reader_state.note_open("k2", "tab-late")
p = reader_state.save_progress("k2", 8, 100, 0.05, "", sid="tab-late")
check("记过 open 之后往回翻 → 放行", p.get("skipped") is None, p)

p = reader_state.save_progress("k2", 0, 0, 0.001, "", sid="tab-ancient")
check("其它从没打开过的会话仍被挡", p.get("skipped") == "stale-rewind", p)

check("note_open 空 sid 不记", (reader_state.note_open("k2", ""),
                                reader_state._OPENED.get("k2", {}).get("sid"))[1] == "tab-late")
check("坏时间串 → 不放行", reader_state._opened_after("k2", "tab-late", "不是时间") is False)

print("\n[3] 门槛: 差多少字节才打招呼")
check("空书 → 4096", sync._choice_gap(0) == 4096)
check("小书 100KB → 2KB 下限以上",
      1024 <= sync._choice_gap(100 * 1024) <= 8192, sync._choice_gap(100 * 1024))
check("1MB 书 → 8KB 封顶", sync._choice_gap(1024 * 1024) == 8192, sync._choice_gap(1024 * 1024))
check("42MB 书 → 仍是 8KB", sync._choice_gap(42 * 1024 * 1024) == 8192)

print("\n[4] peek_book: 本地靠前但没推上去 → 交给读者")
BOOK = LIB / "示例书.epub"
BOOK.write_bytes(b"y" * 8192)
sidecar.set_cloud(BOOK, id64="A" * 64, id32="A" * 32, name="示例书", total_count=8)
KEY = "peekkey"
ROW = {"key": KEY, "path": str(BOOK), "title": "示例书", "author": "",
       "id64": "A" * 64, "id32": "A" * 32, "name": "示例书", "total_count": 8,
       "bytes": 8192}
sync._link_by_key = lambda key: (dict(ROW) if key == KEY else None)   # type: ignore
sync.links = lambda refresh=False: [dict(ROW)]                        # type: ignore
sync._geometry_of = lambda path: None                                 # type: ignore
sync.position_for = lambda geo, ch, off: ""                           # type: ignore

PROG = {"percent": 0.60, "chapter": 6, "offset": 100, "complete": 0}
reader_state.get_progress = lambda key: (dict(PROG) if key == KEY else {})  # type: ignore
reader_state.book_bytes = lambda key: 8192                            # type: ignore

DETAIL: list = []


def fake_pull(**kw):
    return {"updated": 0, "skipped": 1, "detail": [dict(DETAIL[0])]}


sync.pull_once = fake_pull                                            # type: ignore


def reset(cloud_pct=0.31, local_pct=0.60, last_push=None):
    sync._PEEK_CACHE.clear()
    sync._PULL_BOOK_AT.clear()
    PROG.update(percent=local_pct)
    DETAIL.clear()
    DETAIL.append({"book": "示例书", "skipped": "local-ahead",
                   "cloud": cloud_pct, "local": local_pct, "bytes": 8192})
    # 边车是深度合并的, 残留的 last_push_pct 会让"从没推过"的用例失真 → 先删再写
    try:
        sidecar.sidecar_path(BOOK).unlink()
    except OSError:
        pass
    sidecar.set_cloud(BOOK, id64="A" * 64, id32="A" * 32, name="示例书", total_count=8)
    if last_push is not None:
        sidecar.touch_cloud(BOOK, last_push_pct=last_push)


reset(last_push=0.30)
out = sync.peek_book(KEY, force=True)
check("本地 60% / 云端 31% / 上次推 30% → 问读者", out.get("need_choice") is True, out)
check("原因是 local-unpushed", out.get("choice_kind") == "local-unpushed", out)
check("带两端百分比", (out.get("local_pct"), out.get("cloud_pct")) == (0.6, 0.31), out)

reset(last_push=0.60)
out = sync.peek_book(KEY, force=True)
check("云端虽旧但本地已经推过 (last_push = 本地) → 不问", not out.get("need_choice"), out)

reset(last_push=0.60, local_pct=0.31, cloud_pct=0.30)
out = sync.peek_book(KEY, force=True)
check("差得不够多 (0.01 × 8192 ≈ 82 字节) → 不问", not out.get("need_choice"), out)

reset(last_push=None, local_pct=0.60, cloud_pct=0.31)
out = sync.peek_book(KEY, force=True)
check("从没推过 → 也算没推上去", out.get("need_choice") is True, out)

reset(last_push=0.30, local_pct=0.20, cloud_pct=0.60)
DETAIL[0] = {"book": "示例书", "dry": True, "pct": 0.60, "bytes": 8192,
             "chapter": 5, "offset": 10, "basis": "position"}
out = sync.peek_book(KEY, force=True)
check("云端靠前 → 原因仍是 cloud-ahead", out.get("choice_kind") == "cloud-ahead", out)
check("云端靠前带 anchor", (out.get("anchor") or {}).get("chapter") == 5, out)

print("\n[5] 前端: 不把重排那一瞬的锚点写回去")
js = (ROOT / "src/recto/app/ui/assets/reader.js").read_text(encoding="utf-8")
check("有 saveAnchor", "function saveAnchor()" in js)
check("saveProgress 用 saveAnchor", "var a = saveAnchor();" in js)
check("flushProgress 也用 saveAnchor", "var a = saveAnchor();" in
      js.split("function flushProgress()", 1)[1].split("function askCloudChoice", 1)[0])
check("记下最后存好的锚点", "S.saved = { ch: ch, off: off" in js)
check("往回翻时置 backIntent", "S.backIntent = true;" in js)
check("打开书带 sid", "api('?sid=' + encodeURIComponent(S.sid))" in js)
check("两种 choice 文案分开写", "local-unpushed" in js and "cloud-ahead" in js)
check("本地意见是「用本地覆盖云端」", "用本地覆盖云端" in js)
check("云端意见是「采用云端」", "采用云端" in js)
check("need_choice 不再要求带 anchor", "if (r.need_choice) { askCloudChoice(r); return; }" in js)

py = (ROOT / "src/recto/app/reader.py").read_text(encoding="utf-8")
check("meta 请求里记 open 的 sid", "rs.note_open(key," in py)
check("旧会话被挡时留一条日志", "忽略旧会话进度" in py)

print("\n[6] 浏览器自己记得上次读到哪 (最后一道兜底)")
check("记本地记忆 posSeenWrite", "function posSeenWrite(" in js)
check("保存时写下本地记忆", "posSeenWrite(ch, off, pct);" in js)
check("打开时比对本地记忆", "maybeAskRemembered(p);" in js)
check("门槛跟服务端同一口径", "function gapBytes()" in js and "0.02" in js)
check("第三种情形有独立文案", "local-remembered" in js and "这台设备上次读到" in js)
check("可以回到上次位置", "回到上次位置" in js)
check("可以从当前开始", "从当前开始" in js)
check("两条路都实现", "function chooseRemembered(" in js
      and "jumpTo(+seen.ch || 0" in js)
check("选择条按情形分派", "S.choiceKind === 'local-remembered'" in js)
check("boot 记下库里的位置", "S.bootP = p;" in js)
probe = ROOT / "tools/probe_reader_remembered.mjs"
check("有端到端探针", probe.exists())

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
