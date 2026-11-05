"""书票渲染 —— 摘录后台「书票」按钮打到 render.py 的那条链路。

盯五件事:
  1) 子进程用 `python3 -m recto.assets.bookplate.render` 起 (render.py 要
     `import recto.core.paths`, 按脚本路径起必 ModuleNotFoundError)
  2) 素材根 (模板 / node_modules / 输出目录) 来自配置 [bookplate] asset_root,
     不是包所在目录 —— 仓库里没有 bin/
  3) 输出路径以 render.py 自己报告的 `out=` 为准, 不去猜目录 / 不取"最新文件"
     (Recto 没有 bin/output, 猜出来的可能是别人的图)
  4) 失败要说清楚 (rc / stderr / 没有 out= / 文件不存在 / 不是 PNG)
  5) 真渲一张 (依赖齐全时): 2160×2880 的 PNG, 有纸色有墨有印章色
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from recto.app import excerpts  # noqa: E402
from recto.assets.bookplate import render as R  # noqa: E402

ok_n = 0
bad: list[str] = []
skip_n = 0


def ok(cond, label: str, extra=None) -> None:
    global ok_n
    if cond:
        ok_n += 1
    else:
        bad.append(label)
        print(f"FAIL {label}" + (f"  {extra!r}" if extra is not None else ""))


def skip(label: str) -> None:
    global skip_n
    skip_n += 1
    print(f"SKIP {label}")


EXCERPTS_SRC = (ROOT / "src/recto/app/excerpts.py").read_text(encoding="utf-8")
RENDER_PATH = Path(R.__file__).resolve()

# --- 1) 源码契约 -----------------------------------------------------------
ok("skill_root" not in EXCERPTS_SRC, "excerpts.py 里不再引用未定义的 skill_root")
ok('"-m", "recto.assets.bookplate.render"' in EXCERPTS_SRC,
   "子进程以模块方式 (-m recto.assets.bookplate.render) 启动渲染器")
ok("--no-bg" in EXCERPTS_SRC, "webui 渲染关掉配图背景 (同原设计)")
ok('env["PYTHONPATH"]' in EXCERPTS_SRC, "给子进程补 PYTHONPATH, 让它能 import recto")
ok("bin\" / \"output" not in EXCERPTS_SRC and "'bin' / 'output'" not in EXCERPTS_SRC,
   "不再回落到 bin/output 找'最新 PNG'")
ok('out=(\\S+\\.png)' in EXCERPTS_SRC, "输出路径取自 render.py 报告的 out=")
ok("BOOKPLATE_TIMEOUT" in EXCERPTS_SRC, "渲染有显式超时常量")

# render.py 自己仍然是"包内模块 + 素材根来自配置"
RENDER_SRC = RENDER_PATH.read_text(encoding="utf-8")
ok("from recto.core import legacy, paths" in RENDER_SRC,
   "render.py 从 recto.core 读配置 (所以必须按模块起)")
ok("SKILL_ROOT = Path(paths.BOOKPLATE_ASSET_ROOT)" in RENDER_SRC,
   "素材根 = 配置 [bookplate] asset_root")
ok("DEFAULT_BOOKPLATE_STYLE" in RENDER_SRC and R.DEFAULT_BOOKPLATE_STYLE == "zhijian",
   "默认风格仍是纸间风 zhijian")
ok(set(R.BOOKPLATE_STYLES) == {"zhijian", "seal", "heraldic", "minimal"},
   "风格清单与原设计一致 (旧三风格已 alias 到 zhijian)", list(R.BOOKPLATE_STYLES))

# --- 2) 临时数据集 ---------------------------------------------------------
tmp = Path(tempfile.mkdtemp(prefix="recto-bp-"))
agg = tmp / "aggregate"
agg.mkdir(parents=True)
(agg / "books.jsonl").write_text(
    json.dumps({"id": 77, "name": "某本书", "author": "某作者"}, ensure_ascii=False) + "\n",
    encoding="utf-8",
)
(agg / "aggregate.jsonl").write_text(
    json.dumps({"id": 4242, "bookId": 77, "content": "一段摘录文字。",
                "idea": "", "chapter": "第一章", "createdTime": 1780329799372},
               ensure_ascii=False) + "\n",
    encoding="utf-8",
)

# --- 3) 参数校验 -----------------------------------------------------------
png, err = excerpts._render_bookplate_png(tmp, 4242, "nope")
ok(png is None and err and "未知 style" in err, "非法 style 直接拒", err)

png, err = excerpts._render_bookplate_png(tmp, "does-not-exist", "zhijian")
ok(png is None and err and "找不到" in err, "找不到摘录时明确报错", err)

# --- 4) 子进程调用契约 (拦截 subprocess.run) -------------------------------
_orig_run = subprocess.run
seen: dict = {}
fake_png = tmp / "out" / "fake.png"
fake_png.parent.mkdir(parents=True, exist_ok=True)


def fake_run(argv, **kw):
    seen["argv"] = argv
    seen["kw"] = kw
    fake_png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    return subprocess.CompletedProcess(argv, 0, stdout=f"OK style=zhijian out={fake_png}\n", stderr="")


subprocess.run = fake_run
try:
    png, err = excerpts._render_bookplate_png(tmp, 4242, "zhijian")
finally:
    subprocess.run = _orig_run

argv = seen.get("argv") or []
ok(not err, "拦截路径下渲染成功", err)
ok(png is not None and png.startswith(b"\x89PNG"), "返回的是 PNG 字节")
ok(argv[:3] == [sys.executable, "-m", "recto.assets.bookplate.render"],
   "命令 = <python> -m recto.assets.bookplate.render", argv[:3])
ok("--no-bg" in argv and "--style" in argv and "zhijian" in argv,
   "命令带 --no-bg 与 --style zhijian", argv)
ok(str(RENDER_PATH.parents[3]) in (seen.get("kw") or {}).get("env", {}).get("PYTHONPATH", ""),
   "PYTHONPATH 指向包根 (src)", (seen.get("kw") or {}).get("env", {}).get("PYTHONPATH"))
ok((seen.get("kw") or {}).get("timeout") == excerpts.BOOKPLATE_TIMEOUT,
   "用统一的超时常量")
ok(not Path(argv[-1]).exists(), "临时 excerpt JSON 用完即删")

# 送进去的 excerpt 已经合并了书名/作者 (render 时需要)
# —— 这一条靠临时 JSON 内容验证, 所以单独起一次
held: dict = {}


def hold_run(argv, **kw):
    src = Path(argv[-1])
    held["json"] = json.loads(src.read_text(encoding="utf-8"))
    fake_png.write_bytes(b"\x89PNG\r\n\x1a\n")
    return subprocess.CompletedProcess(argv, 0, stdout=f"OK out={fake_png}\n", stderr="")


subprocess.run = hold_run
try:
    excerpts._render_bookplate_png(tmp, 4242, "zhijian")
finally:
    subprocess.run = _orig_run
ok(held.get("json", {}).get("bookName") == "某本书"
   and held["json"].get("bookAuthor") == "某作者",
   "喂给渲染器的 JSON 带上了 bookName / bookAuthor", held.get("json"))

# --- 5) 失败路径 -----------------------------------------------------------
def run_stub(rc=0, stdout="", stderr="", write=True):
    def _r(argv, **kw):
        if write:
            fake_png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        return subprocess.CompletedProcess(argv, rc, stdout=stdout, stderr=stderr)
    return _r


for label, stub, want in [
    ("非零退出码要说清 stderr", run_stub(rc=2, stderr="playwright dep missing: node"), "playwright dep missing"),
    ("没有 out= 不猜路径", run_stub(rc=0, stdout="something else\n"), "没有报告输出路径"),
    ("报告了但文件不存在", run_stub(rc=0, stdout="OK out=/nope/missing.png\n"), "文件不存在"),
]:
    subprocess.run = stub
    try:
        png, err = excerpts._render_bookplate_png(tmp, 4242, "zhijian")
    finally:
        subprocess.run = _orig_run
    ok(png is None and err and want in err, label, err)


def not_png(argv, **kw):
    fake_png.write_bytes(b"<html>oops</html>")
    return subprocess.CompletedProcess(argv, 0, stdout=f"OK out={fake_png}\n", stderr="")


subprocess.run = not_png
try:
    png, err = excerpts._render_bookplate_png(tmp, 4242, "zhijian")
finally:
    subprocess.run = _orig_run
ok(png is None and err and "不是 PNG" in err, "输出不是 PNG 时报错", err)


def boom(argv, **kw):
    raise subprocess.TimeoutExpired(argv, 1)


subprocess.run = boom
try:
    png, err = excerpts._render_bookplate_png(tmp, 4242, "zhijian")
finally:
    subprocess.run = _orig_run
ok(png is None and err and "超时" in err, "超时给中文提示", err)

# --- 6) 真渲一张 (依赖齐全才跑) --------------------------------------------
from recto.core import config as _config  # noqa: E402

cfg = _config.get_config()
asset_root = Path(cfg.bookplate_asset_root)
node_modules = asset_root / "bin" / "node_modules" / "playwright"
chromium = Path.home() / ".cache/ms-playwright/chromium_headless_shell-1187"
node = R.resolve_node()[0]

if not node_modules.is_dir():
    skip(f"真渲染: 素材根缺少 node_modules ({node_modules})")
elif not chromium.is_dir():
    skip(f"真渲染: 缺 chromium ({chromium})")
else:
    if node is None:
        skip("真渲染: 没有 node")
    else:
        png, err = excerpts._render_bookplate_png(tmp, 4242, "zhijian")
        ok(png is not None and err is None, "真渲染成功", err)
        if png:
            ok(png.startswith(b"\x89PNG\r\n\x1a\n"), "真渲染返回 PNG")
            ok(len(png) > 100_000, "真渲染不是空白图 (体积够大)", len(png))
            out = tmp / "real.png"
            out.write_bytes(png)
            try:
                from PIL import Image

                im = Image.open(out).convert("RGB")
                ok(im.size == (2160, 2880), "1080×1440 @2x = 2160×2880", im.size)
                small = im.resize((270, 360))
                px = list(small.get_flattened_data()) if hasattr(small, "get_flattened_data") else list(small.getdata())
                paper = sum(1 for r, g, b in px if r > 200 and g > 190 and b > 170)
                ink = sum(1 for r, g, b in px if r < 90 and g < 90 and b < 90)
                red = sum(1 for r, g, b in px if r > 120 and r - g > 50 and r - b > 50)
                total = len(px)
                ok(paper / total > 0.5, "底色是纸色", paper / total)
                ok(ink > 0, "有墨 (正文/边框)", ink)
                ok(red > 0, "有朱红印章", red)
            except ImportError:
                skip("PIL 缺失, 跳过颜色检查")

# --- 7) 长摘录不溢出画框 (自适配缩排) -------------------------------------
ok("_ZH_FIT_JS" in RENDER_SRC, "渲染器带按框缩排脚本")
ok("document.fonts.ready" in RENDER_SRC, "缩排等字体就位后再量")
for n, idea, label in ((17, "", "极短"), (161, "", "常见短"), (625, "", "库内最长"),
                       (1500, "批注" * 5, "极端长 + 批注")):
    html = R.render_html({"id": 1, "bookId": 77, "content": "字" * n,
                          "idea": idea, "chapter": "第一章"}, "zhijian", with_bg=False)
    ok("bp-zh-excerpt" in html and "fonts.ready" in html, f"{label}: HTML 带缩排脚本")

if node is None or not node_modules.is_dir() or not chromium.is_dir():
    skip("长摘录溢出的实测: 缺 node / playwright / chromium")
else:
    measure = Path(tempfile.mkdtemp(prefix="recto-bp-fit-")) / "measure.cjs"
    measure.write_text(
        """const { chromium } = require('playwright');
const fs = require('fs');
(async () => {
  const html = fs.readFileSync(process.argv[2], 'utf-8');
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: 1080, height: 1440 } });
  const page = await ctx.newPage();
  await page.setContent(html, { waitUntil: 'networkidle' });
  await page.waitForTimeout(2200);
  const r = await page.evaluate(() => {
    const box = document.querySelector('.bp-zh-excerpt');
    const foot = document.querySelector('.bp-zh-foot');
    const p = box.querySelector('p');
    const note = document.querySelector('.bp-note');
    const last = (note || box).getBoundingClientRect();
    const f = foot.getBoundingClientRect();
    return { last: +last.bottom.toFixed(1), footTop: +f.top.toFixed(1),
             fs: parseFloat(getComputedStyle(p).fontSize) };
  });
  console.log(JSON.stringify(r));
  await b.close();
})().catch(e => { console.error(e); process.exit(1); });
""",
        encoding="utf-8",
    )
    # require('playwright') 必须从素材根的 bin/ 下解析
    run_dir = asset_root / "bin" / ".cache"
    run_dir.mkdir(parents=True, exist_ok=True)
    runner = run_dir / "recto-test-bp-fit.cjs"
    runner.write_text(measure.read_text(encoding="utf-8"), encoding="utf-8")
    env = dict(os.environ)
    env["PATH"] = str(node.parent) + os.pathsep + env.get("PATH", "")
    for n, idea, label in ((161, "", "常见短"), (527, "", "中长"), (625, "", "库内最长")):
        html = R.render_html({"id": 1, "bookId": 77, "content": "字" * n,
                              "idea": idea, "chapter": "第一章"}, "zhijian", with_bg=False)
        page_file = Path(tempfile.mkdtemp(prefix="recto-bp-page-")) / "p.html"
        page_file.write_text(html, encoding="utf-8")
        proc = subprocess.run([str(node), str(runner), str(page_file)],
                              capture_output=True, text=True, env=env, timeout=120)
        if proc.returncode != 0:
            ok(False, f"{label}: 量高失败", proc.stderr[-300:])
            continue
        data = json.loads(proc.stdout.strip().splitlines()[-1])
        ok(data["last"] <= data["footTop"],
           f"{label}: 正文/批注底边不压底栏 ({data['last']} <= {data['footTop']})", data)
        ok(data["fs"] >= 18, f"{label}: 字号不低于下限 18px", data["fs"])
    try:
        runner.unlink()
    except OSError:
        pass

print(f"\n{ok_n} PASS, {len(bad)} FAIL, {skip_n} SKIP")
if bad:
    print("失败: " + "; ".join(bad))
    sys.exit(1)
