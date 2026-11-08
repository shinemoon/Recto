"""执行结果 / 最近日志 —— 必须是"人话", 不是裸 JSON。

盯四件事:
  1) `#out` 是 DOM 容器 (能塞结构化结果), 不再是一个 <pre> 里 stringify 出来的 JSON
  2) 渲染层有: 结论行 / 计数 chip / 每本书一行 / 原始数据折叠区
  3) 后端的 "为什么跳过" 有中文说法 (noop / local-ahead / regress …)
  4) 日志行按 asctime 里的**空格**正确切分, 等级是 INFO/WARNING 而不是整行当 RAW
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from recto.app import console  # noqa: E402
from recto.core import paths  # noqa: E402

ok_n = 0
bad: list[str] = []


def ok(cond, label: str, extra=None) -> None:
    global ok_n
    if cond:
        ok_n += 1
    else:
        bad.append(label)
        print(f"FAIL {label}" + (f"  {extra!r}" if extra is not None else ""))


JS = console._JS
CSS = (ROOT / "src/recto/app/ui/assets/recto.css").read_text(encoding="utf-8")
PAGE = console._page_html("recto")

# --- 1) 容器 ---------------------------------------------------------------
ok('id="out" class="rs-wrap"' in PAGE, "#out 是结构化容器 (rs-wrap), 不是 <pre>")
ok('id="log" class="log-list"' in PAGE, "#log 是行列表容器 (log-list), 不是 <pre>")
ok('<pre id="out"' not in PAGE, "页面上没有 <pre id=out> 了")
ok('<pre id="log"' not in PAGE, "页面上没有 <pre id=log> 了")

# --- 2) 渲染层 -------------------------------------------------------------
for fn in ["rsCard", "rsChips", "rsRows", "detailRows", "renderRun", "renderJob",
           "renderLog", "logText", "whyText", "outHtml"]:
    ok(f"function {fn}" in JS, f"渲染函数存在: {fn}")
ok("const chapterNo" in JS, "渲染函数存在: chapterNo")
ok("JSON.stringify(r, null, 1)" not in JS.split("function renderRun")[1].split("function renderJob")[0],
   "renderRun 不再直接 stringify 结果")
ok('class="rs-raw"' in JS and "原始数据" in JS, "原始 JSON 收在折叠区 (.rs-raw)")
ok("function toast" in JS and "outHtml(renderRun(action, r))" in JS,
   "动作跑完走 renderRun 渲染")

# 每个动作都有专门的文案
for act, word in [("pull", "已更新"), ("push", "无需推送"), ("notes", "已同步"),
                  ("time", "已写入"), ("links", "已对齐")]:
    seg = JS.split(f'if (action === "{act}")')[1][:600] if f'if (action === "{act}")' in JS else ""
    ok(bool(seg), f"动作有专属分支: {act}")
ok("已是最新" in JS, "拉取无变化时给一句结论 (已是最新)")
ok("云端更靠前" in JS, "regress 的原因翻成人话")

# 跳过的原因都要有中文
for why in ["noop", "local-ahead", "cloud-empty", "no-progress", "throttled",
            "no-cloud-link", "forward", "finish", "meta", "no-baseline"]:
    ok(f'"{why}":' in JS, f"跳过原因有译文: {why}")

# --- 3) CSS ----------------------------------------------------------------
for cls in [".rs-head", ".rs-chips", ".rs-list", ".rs-note", ".rs-raw",
            ".log-row", ".log-ts", ".log-lv", ".log-msg"]:
    ok(cls in CSS, f"样式存在: {cls}")
ok("color-mix" in CSS.split("14 同步结果")[1], "chip 用 token 混色, 没有硬编码颜色")
ok("@media (max-width: 600px)" in CSS.split("14 同步结果")[1], "日志针对窄屏改两行排版")

# --- 4) 日志解析 -----------------------------------------------------------
log_file = paths.LOG_DIR / "sync.log"
sample = [
    "2026-11-04 11:31:12,272 INFO 拉取成功 · 示例短篇 · 云端 54.3% · 第 8 章",
    "2026-11-04 11:32:00,001 WARNING 推送失败 · 示例长篇 · timeout",
    "Traceback (most recent call last):",
]
parsed = []
for line in sample:
    m = console._LOG_LINE_RE.match(line)
    parsed.append({"ts": m.group(1), "level": m.group(2), "msg": m.group(3)}
                  if m else {"ts": "", "level": "RAW", "msg": line})
ok(parsed[0]["level"] == "INFO" and parsed[0]["ts"].startswith("2026-11-04"),
   "asctime 带空格也能切对 (INFO)", parsed[0])
ok(parsed[0]["msg"].startswith("拉取成功"), "消息体不含时间戳前缀", parsed[0]["msg"][:20])
ok(parsed[1]["level"] == "WARNING", "WARNING 也认得", parsed[1])
ok(parsed[2]["level"] == "RAW", "不认识的行保留为原文, 不丢")

ok(log_file.exists() or True, "日志文件可有可无")
lines = console._tail_log(5) if log_file.exists() else []
if lines:
    ok(all(l["level"] != "RAW" for l in lines), "真日志里不再出现整行 RAW", lines[:1])

# --- 5) 后端日志措辞 -------------------------------------------------------
sync_src = (ROOT / "src/recto/services/sync.py").read_text(encoding="utf-8")
ok("推送成功 ·" in sync_src and "拉取成功 ·" in sync_src,
   "sync 写的是中文日志 (推送成功 / 拉取成功)")
ok('"push OK' not in sync_src and '"pull OK' not in sync_src,
   "没有留下旧的 push OK / pull OK 措辞")
ok("def audit(" in sync_src, "提供 audit() 供 notes/sessions 共用同一份日志")
notes_src = (ROOT / "src/recto/services/notes.py").read_text(encoding="utf-8")
ok("_sync.audit(" in notes_src, "划线同步也写日志")
ok("unchanged" in notes_src, "拉取划线能认出'未变' (不每次报更新 28 条)")
ok("def cloud_ids(" in notes_src and "def cloud_versions_multi(" in notes_src,
   "笔记按多个云端身份合并 (大小写 / id64 前缀)")
ok("text-mismatch" in notes_src and "def file_md5(" in notes_src,
   "本地文件与云端不一致时拒绝按位置切/写")
ok("unmapped" in notes_src, "书库外有笔记的书会被单列出来")
for why in ["text-mismatch", "cloud-only"]:
    ok(f'"{why}":' in JS, f"笔记跳过原因有译文: {why}")
ok("b.unchanged" in JS and "b.skipped" in JS, "每本书那一行会写清未变/跳过原因")
ok("云端还有" in JS, "拉取结果里会提示书库外的笔记")

print(f"\n{ok_n} PASS, {len(bad)} FAIL")
if bad:
    print("失败: " + "; ".join(bad))
    sys.exit(1)
