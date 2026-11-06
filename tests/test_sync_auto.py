"""自动同步 (本地阅读 ⇄ 云端) 的测试。

覆盖三块:
  1. 配置层: 默认值 / 设置页覆盖 / 环境变量优先级 / 立刻生效
  2. 服务层: 状态机 / 推送节流 / flush 绕过 / 打开时拉取 / 旧标签页回退保护
  3. 界面层: 阅读页右下角那行小字 / 设置页「云端同步」卡 / 前端钩子都在

跑法: python3 tests/test_sync_auto.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = tempfile.mkdtemp(prefix="recto-syncauto-test-")
LIB = Path(TMP) / "library"
LIB.mkdir(parents=True, exist_ok=True)
os.environ["RECTO_DATA_DIR"] = TMP
os.environ["RECTO_LIBRARY_DIR"] = str(LIB)
for _k in ("RECTO_SYNC_AUTO", "RECTO_SYNC_STAMP", "RECTO_SYNC_PULL_ON_OPEN",
           "RECTO_SYNC_PUSH_DEBOUNCE", "RECTO_SYNC_FLUSH_ON_HIDE"):
    os.environ.pop(_k, None)

from recto.core import config, sidecar                       # noqa: E402
from recto.core import settings as st                        # noqa: E402
from recto.services import reader_state, sync                # noqa: E402
from recto.app import reader as reader_mod                   # noqa: E402
from recto.app import settings_page                          # noqa: E402

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


BOOK = LIB / "示例书.epub"
BOOK.write_bytes(b"x" * 4096)
sidecar.set_cloud(BOOK, id64="A" * 64, id32="A" * 32, name="示例书", total_count=8)
KEY = "testkey"

ROW = {"key": KEY, "path": str(BOOK), "title": "示例书", "author": "",
       "id64": "A" * 64, "id32": "A" * 32, "name": "示例书", "total_count": 8,
       "bytes": 4096}

# 不碰网络: 映射 / 推送 / 拉取全部换成受控的替身
sync.links = lambda refresh=False: [dict(ROW)]                       # type: ignore
sync._link_by_key = lambda key: (dict(ROW) if key == KEY else None)  # type: ignore
_REAL_LINK_LIGHT = sync._link_light      # 真实现留着单独验
sync._link_light = lambda key: (dict(ROW) if key == KEY else None)    # type: ignore
_real_get_book = reader_state.get_book
reader_state.get_book = lambda key: ({"path": str(BOOK), "title": "示例书",
                                     "author": "", "bytes": 4096}
                                     if key == KEY else {})           # type: ignore

PUSHED: list = []


def fake_push(**kw):
    PUSHED.append(kw)
    return {"pushed": 1, "skipped": 0, "failed": 0,
            "detail": [{"pct": 0.4, "why": "forward", "chapter": 2, "ok": True}]}


def fake_pull(updated=0, **kw):
    return {"updated": updated, "skipped": 0 if updated else 1,
            "detail": [{"pct": 0.5, "chapter": 3, "offset": 900, "bytes": 4096,
                        "updated": bool(updated)} if updated else
                       {"skipped": "local-ahead", "cloud": 0.2, "local": 0.5}]}


sync.push_once = fake_push     # type: ignore
sync.pull_once = fake_pull     # type: ignore

print("== 配置层 ==")
cfg = config.get_config(reload=True)
sec = cfg.section("sync")
check("默认项齐全", set(sec) >= {"auto", "pull_on_open", "push_debounce_sec",
                                "pull_gap_sec", "flush_on_hide", "stamp"}, sec)
check("默认打开自动同步与右下角显示", sec["auto"] is True and sec["stamp"] is True, sec)
check("默认推送节流 12 秒", int(sec["push_debounce_sec"]) == 12, sec)
s0 = sync.sync_settings()
check("sync_settings 暴露全部项", set(s0) == {"auto", "pull_on_open",
      "push_debounce_sec", "pull_gap_sec", "flush_on_hide", "stamp"}, s0)

st.update(sync_auto="off", sync_stamp="off", sync_push_debounce="20")
config.get_config(reload=True)
s1 = sync.sync_settings()
check("设置页关掉总开关 / 右下角", s1["auto"] is False and s1["stamp"] is False, s1)
check("设置页改推送节流", s1["push_debounce_sec"] == 20.0, s1)

st.update(sync_push_debounce="1")
config.get_config(reload=True)
check("推送节流有下限 (1 → 3)", int(sync.sync_settings()["push_debounce_sec"]) == 3,
      sync.sync_settings())

st.update(sync_auto="on", sync_stamp="on", sync_push_debounce="12")
config.get_config(reload=True)
check("改回 on 立刻生效", sync.sync_settings()["auto"] is True, sync.sync_settings())

st.update(sync_auto="off")
os.environ["RECTO_SYNC_AUTO"] = "on"
config.get_config(reload=True)
check("环境变量压过设置页", sync.sync_settings()["auto"] is True, sync.sync_settings())
os.environ.pop("RECTO_SYNC_AUTO")
st.clear("sync_auto", "sync_stamp", "sync_pull_on_open", "sync_flush_on_hide",
         "sync_push_debounce")
config.get_config(reload=True)

print("== 轻量映射 (进度保存路径不扫书库) ==")
_lt = _REAL_LINK_LIGHT(KEY)
check("轻量映射读到 id32", _lt and _lt["id32"] == "A" * 32, _lt)
check("轻量映射读到书名 / 字节", _lt and _lt["title"] == "示例书"
      and _lt["bytes"] == 4096, _lt)
check("轻量映射: 没这本书 = None", _REAL_LINK_LIGHT("nope-key") is None, "")
_had = sync.links
sync.links = lambda refresh=False: []          # 故意让全库映射为空
check("轻量映射不看全库映射表", _REAL_LINK_LIGHT(KEY) is not None, "")
sync.links = _had

print("== 状态机 ==")
st0 = sync._state_of("nope-key")
check("没映射的书 = no-link", st0["state"] == "no-link", st0)

sync._PUSH_STATE.pop(KEY, None)
sidecar.touch_cloud(BOOK, last_push_at="2026-11-05T15:37:21+08:00", last_push_pct=0.32355,
                    last_pull_at="2026-11-05T15:00:00+08:00", last_pull_pct=0.31)
sx = sync._state_of(KEY)
check("有映射未动过 = synced", sx["state"] == "synced", sx)
check("带上上次推送时间", sx["last_push_at"].startswith("2026-11-05T15:37"), sx)
check("带上上次拉取时间", sx["last_pull_at"].startswith("2026-11-05T15:00"), sx)

sync._PUSH_STATE[KEY] = {"pending_pct": 0.33}
check("有待推位置 = dirty", sync._state_of(KEY)["state"] == "dirty", sync._state_of(KEY))
sync._PUSH_STATE[KEY] = {"inflight": True}
check("正在推 = pushing", sync._state_of(KEY)["state"] == "pushing", sync._state_of(KEY))
sync._PUSH_STATE[KEY] = {"ok": False, "error": "boom"}
check("上次失败 = failed", sync._state_of(KEY)["state"] == "failed", sync._state_of(KEY))
sync._PUSH_STATE.pop(KEY, None)

st.update(sync_auto="off")
config.get_config(reload=True)
check("总开关关掉 = off", sync._state_of(KEY)["state"] == "off", sync._state_of(KEY))
st.clear("sync_auto")
config.get_config(reload=True)

print("== 推送 (后台线程 + 节流) ==")
PUSHED.clear()
sync._PUSH_STATE.pop(KEY, None)
r = sync.mirror_reader_progress(KEY, 0.4)
check("第一次保存就排一次推送", r.get("queued") is True, r)
for _ in range(40):
    if not (sync._PUSH_STATE.get(KEY) or {}).get("inflight"):
        break
    time.sleep(0.05)
check("推送真的发出去了", len(PUSHED) == 1, PUSHED)
check("推完状态 = synced", sync._state_of(KEY)["state"] == "synced",
      sync._state_of(KEY))
check("推完不再挂 pending", sync._state_of(KEY)["pending_pct"] is None, sync._state_of(KEY))

PUSHED.clear()
r = sync.mirror_reader_progress(KEY, 0.41)
check("节流窗口内不重复打云端", r.get("throttled") is True and not PUSHED, r)
check("节流期间标脏", sync._state_of(KEY)["state"] == "dirty", sync._state_of(KEY))

r = sync.mirror_reader_progress(KEY, 0.42, flush=True)
for _ in range(40):
    if not (sync._PUSH_STATE.get(KEY) or {}).get("inflight"):
        break
    time.sleep(0.05)
check("flush 绕过节流, 立刻推送", len(PUSHED) == 1, PUSHED)

PUSHED.clear()
cfg_now = st.load()
st.update(sync_auto="off")
config.get_config(reload=True)
r = sync.mirror_reader_progress(KEY, 0.43)
check("总开关关掉不推 (auto-off)", r.get("skipped") == "auto-off" and not PUSHED, r)
st.clear("sync_auto")
config.get_config(reload=True)
check("设置文件恢复原样", st.load() == cfg_now, st.load())

r = sync.mirror_reader_progress("nope-key", 0.5)
check("没映射的书不推 (no-cloud-link)", r.get("skipped") == "no-cloud-link", r)

# 云端更靠前 (推送被回退闸挡住) → 小字要显示**云端**那个百分比, 不是本地待推的
sync.push_once = lambda **kw: {"pushed": 0, "skipped": 1, "failed": 0,
                               "detail": [{"why": "regress(0.4400<0.9976)"}]}
sync._PUSH_STATE.pop(KEY, None)
sync.mirror_reader_progress(KEY, 0.44, flush=True)
for _ in range(40):
    if not (sync._PUSH_STATE.get(KEY) or {}).get("inflight"):
        break
    time.sleep(0.05)
st_ahead = sync._state_of(KEY)
check("云端更靠前 = cloud-ahead", st_ahead["state"] == "cloud-ahead", st_ahead)
check("带出云端百分比", abs(float(st_ahead["cloud_pct"] or 0) - 0.9976) < 1e-9, st_ahead)
check("云端更靠前不算失败", st_ahead["ok"] is True and not st_ahead["error"], st_ahead)
sync.push_once = fake_push     # type: ignore
sync._PUSH_STATE.pop(KEY, None)

# 读者停手之后, 节流窗口到点要自动补推 (否则右下角一直"同步中…")
st.update(sync_push_debounce="3")
config.get_config(reload=True)
PUSHED.clear()
sync._PUSH_STATE[KEY] = {"at": time.time(), "ok": True}   # 刚推过 → 必然进节流
r = sync.mirror_reader_progress(KEY, 0.44)
check("节流时给出补推时间", r.get("throttled") is True and r.get("retry_in", 0) > 0, r)
check("节流后状态是 dirty (等补推)", sync._state_of(KEY)["state"] == "dirty", sync._state_of(KEY))
deadline = time.time() + 8
while time.time() < deadline and sync._state_of(KEY)["state"] == "dirty":
    time.sleep(0.2)
check("到点自动补推, 不再挂 dirty", sync._state_of(KEY)["state"] == "synced",
      sync._state_of(KEY))
check("补推真的打了云端", len(PUSHED) == 1, PUSHED)
st.clear("sync_push_debounce")
config.get_config(reload=True)

print("== 打开时拉取 ==")
sync._PULL_BOOK_AT.pop(KEY, None)
sync.pull_once = lambda **kw: fake_pull(updated=1)      # type: ignore
reader_state.save_progress(KEY, 3, 900, 0.5, "4_0_0$##3-0-4-900-0##$", allow_rewind=True)
r = sync.pull_book(KEY)
check("云端更靠前 → changed", r.get("changed") is True, r)
check("给出跳转用的 anchor", r["anchor"]["chapter"] == 3 and r["anchor"]["offset"] == 900, r)
check("anchor 带百分比", abs(r["anchor"]["percent"] - 0.5) < 1e-9, r)

r = sync.pull_book(KEY)
check("拉取有节流 (pull_gap_sec)", r.get("skipped") == "throttled", r)

sync._PULL_BOOK_AT.pop(KEY, None)
st.update(sync_pull_on_open="off")
config.get_config(reload=True)
r = sync.pull_book(KEY)
check("关掉「打开时拉取」就不拉", r.get("skipped") == "pull-off", r)
st.clear("sync_pull_on_open")
config.get_config(reload=True)

sync._PULL_BOOK_AT.pop(KEY, None)
st.update(sync_auto="off")
config.get_config(reload=True)
r = sync.pull_book(KEY)
check("总开关关掉也不拉", r.get("skipped") == "auto-off", r)
st.clear("sync_auto")
config.get_config(reload=True)

sync._PULL_BOOK_AT.pop(KEY, None)
sync.pull_once = lambda **kw: fake_pull(updated=0)      # type: ignore
r = sync.pull_book(KEY)
check("本地更靠前 → changed=False + local-ahead",
      r.get("changed") is False and r.get("skipped") == "local-ahead", r)
sync.pull_once = fake_pull                              # type: ignore

print("== 旧标签页不许把进度写回去 ==")
reader_state.reset_sid_for_test = None  # noqa: F841  (占位, 便于阅读)
reader_state.save_progress(KEY, 6, 100, 0.90, "7_0_0$##3-0-7-100-0##$",
                           sid="tab-morning", allow_rewind=True)
p = reader_state.save_progress(KEY, 2, 50, 0.30, "3_0_0$##3-0-3-50-0##$", sid="tab-stale")
check("别的会话大幅回退被挡", p.get("skipped") == "stale-rewind", p)
check("挡下时库里还是 90%", abs(reader_state.get_progress(KEY)["percent"] - 0.90) < 1e-9,
      reader_state.get_progress(KEY))

p = reader_state.save_progress(KEY, 6, 90, 0.89, "7_0_0$##3-0-7-90-0##$", sid="tab-morning")
check("同一会话内回翻放行", p.get("skipped") is None and abs(p["percent"] - 0.89) < 1e-9, p)

p = reader_state.save_progress(KEY, 0, 0, 0.01, "", sid="tab-stale", allow_rewind=True)
check("显式 jump 放行 (目录跳回去)", p.get("skipped") is None and p["percent"] == 0.01, p)

reader_state.save_progress(KEY, 6, 100, 0.90, "", sid="tab-a", allow_rewind=True)
p = reader_state.save_progress(KEY, 6, 60, 0.88, "", sid="tab-b")
check("小幅回退 (≤5%) 不算异常", p.get("skipped") is None and abs(p["percent"] - 0.88) < 1e-9, p)
check("会话 id 落库", (reader_state.get_progress(KEY).get("sid") or "") == "tab-b",
      reader_state.get_progress(KEY))

# 不带 sid 的写者 (CLI / 探针 / 同步) 不能把会话信息抹掉 ——
# 抹掉就等于给下一个会话开了"随便回退"的口子。
reader_state.save_progress(KEY, 6, 60, 0.88, "", allow_rewind=True)
check("不带 sid 的写不抹掉已有 sid",
      (reader_state.get_progress(KEY).get("sid") or "") == "tab-b",
      reader_state.get_progress(KEY))

print("== 阅读页 / 设置页 ==")
page = reader_mod._READER_HTML
check("阅读页有右下角同步位", 'id="rv-sync"' in page, "")
check("同步位默认隐藏", 'class="rv-sync" id="rv-sync" hidden' in page, "")
meta_src = (ROOT / "src/recto/app/reader.py").read_text("utf-8")
check("meta 里带同步状态", '"sync": _sync.sync_state(key)' in meta_src, "")
check("有 /sync 端点 (打开时拉取)", 'rest[:1] == ["sync"]' in meta_src, "")
check("有 /sync-state 端点 (轮询小字)", '["sync-state"]' in meta_src, "")
check("进度接口回传同步状态", 'out["sync"] = (out["library"] or {}).get("state")' in meta_src, "")
check("进度接口认 flush / jump", "flush=bool(body.get(\"flush\"))" in meta_src
      and 'allow_rewind=bool(body.get("jump"))' in meta_src, "")

js = (ROOT / "src/recto/app/ui/assets/reader.js").read_text("utf-8")
check("JS: 有 userMoved 闸门", "if (!S.userMoved && !opts.force) return;" in js, "")
check("JS: 手势才算动过 (scroll 不算)", "['wheel', 'touchstart', 'touchmove', 'pointerdown', 'keydown']" in js, "")
check("JS: 离开页面前没动过就不推", "if (!S.userMoved) return;                  // 没动过" in js, "")
check("JS: 读完判定要真的到末尾", "function atEnd()" in js and "end: atEnd()" in js, "")
check("JS: 到末尾那条也要求动过", "if (S.userMoved && S.cur === S.chapters.length - 1" in js, "")
check("后端: 读完只认 end 标记", 'at_end = body.get("end")' in meta_src, "")
check("JS: 会话 id", "sid: 's' + Math.random()" in js, "")
check("JS: 保存带 sid", "sid: S.sid" in js, "")
check("JS: 打开时先拉一次", "function autoPull()" in js and "syncBoot(meta.sync)" in js, "")
# 2026-11-05: 打开时改成"只比不写 + 问读者" —— 自动拽走那条路径整个去掉了,
# 所以 userMoved 闸门与"写回真实位置"都不再需要 (没有拽走就没有要回补的位置)。
_auto = js.split("function autoPull()", 1)[1].split("function syncBoot", 1)[0]
check("JS: 打开时不再自动拽走位置", "jumpTo" not in _auto, _auto[:220])
check("JS: 云端更靠前交给读者选", "askCloudChoice" in _auto and "need_choice" in _auto, "")
check("JS: 云端更靠前显示的是云端百分比", "stampPct(s.cloud_pct != null ? s.cloud_pct : s.last_push_pct)" in js, "")
check("JS: 右下角渲染", "function renderStamp(st)" in js, "")
check("JS: 定时轮询状态", "function syncPoll()" in js and "'/sync-state'" in js, "")
check("JS: 离开页面立刻推", "function flushProgress()" in js
      and "flushProgress();" in js, "")
check("JS: 显示格式 掌阅 HH:MM 已同步", "'掌阅 ' + t + ' 已同步 '" in js, "")
# 2026-11-06: 读者要求「不要显示『掌阅 同步中…』, 保持上一次同步的信息」——
# 推送进行中 (pushing / dirty) 只在这行还空着时铺一次 last_push_*, 否则原样不动。
check("JS: 推送中不再显示「同步中」", "同步中" not in js, "")
check("JS: 推送中保留上一次同步信息",
      "if (!stampEl.hidden && stampEl.textContent) return;" in js, "")
check("JS: 上一次同步信息有统一出口", "function lastStampText(s)" in js, "")
_pushing = js.split("s.state === 'pushing' || s.state === 'dirty'", 1)
check("JS: pushing/dirty 分支不写死文案", len(_pushing) == 2
      and "txt = lastStampText(s);" in _pushing[1].split("}", 1)[0], "")

css = (ROOT / "src/recto/app/ui/assets/reader.css").read_text("utf-8")
check("CSS: 右下角定位 + 不吃指针", ".rv-sync {" in css
      and "position: fixed" in css and "pointer-events: none" in css, "")
check("CSS: 窄屏抬高避免压页码", "@media (max-width: 460px)" in css
      and ".rv-sync { bottom:" in css, "")

sp = settings_page.page_html("root")
check("设置页有云端同步卡", "<summary>云端同步" in sp, "")
check("四个开关 + 间隔都在", all(f'id="{i}"' in sp for i in
      ("sy-auto", "sy-pull", "sy-flush", "sy-stamp", "sy-gap")), "")
check("有保存与回到配置", 'data-st="sync"' in sp and 'data-st="sync-reset"' in sp, "")

out = settings_page.api_post({"sync": {"auto": False, "stamp": False,
                                       "pull_on_open": True, "flush_on_hide": True,
                                       "push_debounce_sec": 25}})
check("API: 保存后回显", out["sync"]["auto"] is False
      and out["sync"]["push_debounce_sec"] == 25, out["sync"])
check("API: 落盘", st.load().get("sync_auto") == "off"
      and st.load().get("sync_push_debounce") == "25", st.load())
check("API: 生效 (不用重启)", sync.sync_settings()["auto"] is False,
      sync.sync_settings())
check("API: 来源标成设置页", out["sync"]["source"]["auto"] == "settings", out["sync"])

check("API: 间隔越界报错", "3–300" in raises(
    lambda: settings_page.api_post({"sync": {"push_debounce_sec": 1}})), "")
check("API: 非数字报错", "数字" in raises(
    lambda: settings_page.api_post({"sync": {"push_debounce_sec": "abc"}})), "")

out = settings_page.api_post({"sync": None})
check("API: 传 null 回到 config.toml", out["sync"]["auto"] is True, out["sync"])
check("API: 覆盖项清干净", not st.load().get("sync_auto"), st.load())

print()
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
