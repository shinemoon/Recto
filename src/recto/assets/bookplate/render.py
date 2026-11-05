#!/usr/bin/env python3
"""
bookplate-generator · render.py

One-shot renderer. Takes an excerpt dict (recto aggregate shape),
auto-detects style, writes a 1080×1440 PNG.

Usage:
  python3 render.py '<json-of-excerpt-dict>'
  echo '<json>' | python3 render.py -

Excerpt dict schema (from recto aggregate):
  {
    "id": 12345,
    "bookId": 901,
    "content": "摘录文本",
    "idea": "用户批注 (optional, may be empty/null)",
    "chapter": "第3章 角色名 (optional)"
  }

Book info is looked up from aggregate/books.jsonl by bookId.
If bookId is missing or not found, the render still proceeds with
book_name="未知书名", author="" (visible in the corner).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from string import Template
from typing import Any

# ---------- paths ----------

from recto.core import legacy, paths  # noqa: E402

SKILL_ROOT = Path(paths.BOOKPLATE_ASSET_ROOT)
DATA_DIR = Path(os.environ.get(
    "BOOKPLATE_DATA_DIR",
    str(paths.BOOKPLATE_DATA_DIR),
))
CONFIG_PATH = DATA_DIR / "config.json"
EXCERPT_AGGREGATE_DIR = Path(legacy.env_aggregate_dir(
    str(paths.DATA_ROOT / "aggregate"),
))
# playwright (node) lives in bin/node_modules/ now — self-contained, no
# external skill dependency. Install with:
#   cd bin/ && npm install playwright@1.55.0 --registry=https://registry.npmmirror.com
PLAYWRIGHT_NODE_MODULES = SKILL_ROOT / "bin" / "node_modules"
PLAYWRIGHT_BROWSER = Path.home() / ".cache/ms-playwright/chromium_headless_shell-1187"
PLAYWRIGHT_NODE_CACHE = SKILL_ROOT / "bin" / ".cache"  # for ephemeral capture scripts

TEMPLATE_DIR = SKILL_ROOT / "templates"

DEFAULT_OUTPUT_DIR = paths.BOOKPLATE_OUTPUT_DIR

# Default bookplate style. Used by:
#  - this script when --style is not given (CLI default)
#  - webui "书票" link in /admin/excerpts (always passes ?style=)
#  - SKILL.md / README docs that say "the default look"
# 纸间风 = 米色纸纹 / 黑细线双层框 / 朱红 EX LIBRIS + 印章 / 顶栏 BOOKPLATE / 底栏 日期+dim
DEFAULT_BOOKPLATE_STYLE = "zhijian"
BOOKPLATE_STYLES = ("zhijian", "seal", "heraldic", "minimal")

# ---------- style detection ----------

HERALDIC_BOOK_HINTS = (
    "war and peace", "anna karenina", "crime and punishment",
    "the brothers karamazov", "don quixote", "iliad", "odyssey",
    "divine comedy", "paradise lost", "the iliad", "the odyssey",
    "ulysses", "the aeneid", "le morte d'arthur", "the canterbury tales",
    "moby-dick", "the scarlet letter", "great expectations",
    "war and peace", "the trial", "the metamorphosis", "the castle",
    "the brothers karamazov", "the idiot", "the gambler",
    "madame bovary", "les misérables", "le rouge et le noir",
    "faust", "the sorrows of young werther",
    "红楼梦", "三国演义", "西游记", "水浒传", "金瓶梅",
    "战争与和平", "安娜·卡列尼娜", "罪与罚", "卡拉马佐夫兄弟",
)
SEAL_BOOK_HINTS = (
    "源氏物语", "枕草子", "徒然草", "方丈记", "平家物语",
    "古今和歌集", "万叶集", "竹取物语", "伊势物语",
    "论语", "孟子", "道德经", "庄子", "诗经", "楚辞",
    "文心雕龙", "世说新语", "史记", "资治通鉴",
    "幽明录", "搜神记", "聊斋志异", "阅微草堂笔记",
    "人间词话", "沧浪诗话", "艺概", "苕溪渔隐丛话",
)
SEAL_AUTHOR_HINTS = (
    "紫式部", "清少纳言", "鸭长明", "吉田兼好",
    "孔子", "老子", "庄子", "孟子", "荀子",
    "司马迁", "司马光", "班固", "刘知几", "欧阳修",
    "苏轼", "欧阳修", "王安石", "曾巩", "韩愈", "柳宗元",
    "曹雪芹", "蒲松龄", "纪昀", "袁枚", "张岱",
)


# === 调色板池 ===
# vibe 来自摘录/书名/作者的意象字, 同 vibe 内多个 palette, 渲染时按 seed 抽一个.
PALETTE_VIBES = {
    "海雾": [
        ("#dde4ea", "#1a2733", "#4a6878"),
        ("#c8d3da", "#1c2a35", "#5a7888"),
        ("#e3e8e3", "#243038", "#3a5868"),
    ],
    "寒夜": [
        ("#1f2438", "#e8e8ec", "#a8b4d0"),
        ("#1a1e2e", "#d8dce4", "#8896b4"),
        ("#252038", "#e0dceb", "#b09cc8"),
    ],
    "暖春": [
        ("#f5e8d0", "#3a2814", "#c4663a"),
        ("#f0e2c4", "#3a2010", "#a8542a"),
        ("#e8d8b8", "#2a1c0c", "#b8542e"),
    ],
    "秋意": [
        ("#e8d4b0", "#2a1408", "#a83228"),
        ("#d8c098", "#1c0c04", "#8c2a1c"),
        ("#ecd8a8", "#2a1404", "#5c3a1c"),
    ],
    "晴空": [
        ("#e8eef0", "#1c2c38", "#5a8aa8"),
        ("#f0f0e8", "#2a3028", "#7a9080"),
        ("#dde4e0", "#1c2c28", "#4a7a68"),
    ],
    "雨城": [
        ("#d0d4cc", "#1c2418", "#3a5848"),
        ("#c4c8c0", "#1a1c14", "#4a5a3a"),
        ("#b8bcb0", "#1c1810", "#5a4828"),
    ],
    "书斋": [
        ("#f3ede0", "#1a1410", "#8c3c28"),
        ("#ebe2cc", "#1c1208", "#6a2818"),
        ("#f5efe0", "#1a140c", "#a8542a"),
    ],
}

VIBE_KEYWORDS = {
    "海雾": "海 水 潮 雾 河 湖 浪 涛".split(),
    "寒夜": "雪 夜 月 影 星 冬 冰".split(),
    "暖春": "春 花 树 鸟 火 光 灯 暖 茶".split(),
    "秋意": "秋 山 石 楼 酒 菊 叶 枫".split(),
    "晴空": "风 云 天 日 空 阳 朝".split(),
    "雨城": "雨 路 桥 船 城 街 巷 寺".split(),
}

VIBE_FALLBACK = {"heraldic": "书斋", "seal": "秋意", "minimal": "晴空"}


def pick_palette(content: str, book: str, author: str, style: str, seed: int = 0) -> dict:
    """摘录/书名/作者 抽意象字 → vibe → palette.
    无命中按 style 兜底. 同输入同 seed 必同 palette (稳定).
    """
    text = (content or "") + (book or "") + (author or "")
    scores = {vibe: sum(1 for k in kws if k in text) for vibe, kws in VIBE_KEYWORDS.items()}
    if max(scores.values()) > 0:
        vibe = max(scores, key=scores.get)
    else:
        vibe = VIBE_FALLBACK.get(style, "书斋")
    palettes = PALETTE_VIBES[vibe]
    idx = abs(hash((seed, content, book, author, vibe))) % len(palettes)
    bg, ink, accent = palettes[idx]
    return {"bg": bg, "ink": ink, "accent": accent, "vibe": vibe}


def _has_cjk(s: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in s)


# 意象字优先级(从摘录/书名/作者里抽有"画面感"的字)
IMAGERY_CHARS = set("海风月窗灯雪雨星花山云桥路船城树鸟火水日天光影")


def _pick_imagery_char(text: str) -> str | None:
    """从 text 里找第一个 IMAGERY_CHARS 的字,做印章字。"""
    if not text:
        return None
    for c in text:
        if c in IMAGERY_CHARS:
            return c
    return None


def _pick_seal_char(book_name: str, author: str, content: str) -> str:
    """印章字优先级:
       1) 摘录里的意象字 (海/风/月/...) — 最有"画面感"
       2) 书名首字
       3) 作者首字
       4) 兜底 藏
    """
    for src in (content or "", book_name or "", author or ""):
        c = _pick_imagery_char(src)
        if c:
            return c
    for src in (book_name or "", author or ""):
        m = re.search(r"[\u4e00-\u9fff]+", src)
        if m:
            return m.group(0)[0]
    return "藏"


def _ascii_latin_name(author: str) -> bool:
    """Two capitalized Latin words, e.g. 'Herman Melville'."""
    return bool(re.match(r"^[A-Z][a-zA-Z'\-]+ [A-Z][a-zA-Z'\-]+", author.strip()))


def detect_style(book_name: str, author: str, content: str) -> str:
    """Auto-pick style by content/book metadata. NOT called by main().

    Kept for documentation / future "auto" mode. The current default
    (DEFAULT_BOOKPLATE_STYLE = "zhijian") does not use this — it always
    renders the zhijian look unless the caller passes --style explicitly.
    """
    name_l = (book_name or "").lower()
    author_l = (author or "").lower()
    blob = name_l + " " + author_l

    # 1. heraldic: Latin name or Western classic
    if not _has_cjk(content or "") and not _has_cjk(book_name or "") and not _has_cjk(author or ""):
        if _ascii_latin_name(author or ""):
            return "heraldic"
        for hint in HERALDIC_BOOK_HINTS:
            if hint.lower() in blob:
                return "heraldic"

    # 2. seal: East-Asian classic
    for hint in SEAL_BOOK_HINTS:
        if hint in (book_name or "") or hint in (author or ""):
            return "seal"
    for hint in SEAL_AUTHOR_HINTS:
        if hint in (author or ""):
            return "seal"
    # CJK in book_name + classical markers
    if _has_cjk(book_name or ""):
        if any(k in (book_name or "") for k in ("经", "史", "集", "传", "注", "疏", "论", "笺", "释", "译注", "选", "校", "笈")):
            return "seal"
        # CJK author with CJK name + book → default East Asian visual
        if _has_cjk(author or ""):
            return "seal"

    # 3. minimal fallback
    return "minimal"


# ---------- node (runtime) lookup ----------

# 服务进程 (systemd 等) 的 HOME/PATH 往往跟登录 shell 不同, nvm 装的 node
# 因此可能找不到。这里按顺序试: 显式 env 覆盖 → PATH → nvm 根目录 → 发行版路径。
#   - nvm 根目录 = $NVM_DIR / ~/.nvm / config.toml 的 [bookplate] node_dirs
#     (机器专有的路径写进 config.toml.local, 代码里不留本机路径)
_NODE_ENV_OVERRIDE = ("BOOKPLATE_NODE_BIN", "NODE_BIN")
_node_cache: tuple[Path | None, list[str]] | None = None


def _config_node_dirs() -> list[Path]:
    """config 里配的额外 nvm 根目录; 读不到配置就当没有 (不挡渲染)。"""
    try:
        from recto.core.config import get_config

        return list(get_config().bookplate_node_dirs)
    except Exception:  # noqa: BLE001
        return []


def _nvm_roots() -> list[Path]:
    roots: list[Path] = []
    env_dir = os.environ.get("NVM_DIR")
    if env_dir:
        roots.append(Path(env_dir))
    roots.append(Path.home() / ".nvm")
    roots.extend(_config_node_dirs())
    out: list[Path] = []
    seen: set[str] = set()
    for r in roots:
        key = str(r)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _version_key(p: Path) -> tuple:
    m = re.match(r"v(\d+)\.(\d+)\.(\d+)", p.name)
    if not m:
        return (0, 0, 0)
    return tuple(int(x) for x in m.groups())


def resolve_node() -> tuple[Path | None, list[str]]:
    """Locate a node binary. Returns (path-or-None, candidates-tried)."""
    global _node_cache
    if _node_cache is None:
        tried: list[str] = []
        found: Path | None = None

        for var in _NODE_ENV_OVERRIDE:
            val = os.environ.get(var)
            if not val:
                continue
            cand = Path(val)
            tried.append(f"${var}={val}")
            if cand.is_file() and os.access(str(cand), os.X_OK):
                found = cand
                break

        if found is None:
            which = shutil.which("node")
            tried.append(f"PATH({which or '-'})")
            if which:
                found = Path(which)

        if found is None:
            for root in _nvm_roots():
                versions = root / "versions" / "node"
                if not versions.is_dir():
                    tried.append(f"{versions}(missing)")
                    continue
                bins = sorted(
                    (v / "bin" / "node" for v in versions.iterdir()
                     if (v / "bin" / "node").is_file()),
                    key=lambda p: _version_key(p.parent.parent),
                    reverse=True,
                )
                tried.append(f"{versions}({len(bins)} hit)")
                if bins:
                    found = bins[0]
                    break

        if found is None:
            for extra in ("/usr/local/bin/node", "/usr/bin/node"):
                tried.append(extra)
                if Path(extra).is_file():
                    found = Path(extra)
                    break

        _node_cache = (found, tried)
    return _node_cache


def node_env() -> dict[str, str]:
    """Env with the resolved node's bin dir prepended to PATH."""
    env = dict(os.environ)
    node, _ = resolve_node()
    if node is not None:
        bin_dir = str(node.parent)
        env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
        env.setdefault("NVM_BIN", bin_dir)
    return env


# ---------- playwright health check ----------

def check_playwright_dep() -> tuple[bool, str]:
    """Verify playwright (node) + chromium binary are reachable from this skill.

    Self-contained since the original guizang-social-card-interactive dep
    was retired on 2026-06-14. bin/node_modules/playwright is installed via:
        cd bin/ && npm install playwright@1.55.0 \
            --registry=https://registry.npmmirror.com
    and chromium 1187 lives at PLAYWRIGHT_BROWSER (~/.cache/ms-playwright/).
    """
    if not PLAYWRIGHT_NODE_MODULES.is_dir():
        return False, (
            f"missing node_modules: {PLAYWRIGHT_NODE_MODULES} "
            f"(run: cd {SKILL_ROOT}/bin && npm install playwright@1.55.0 "
            f"--registry=https://registry.npmmirror.com)"
        )
    pw_pkg = PLAYWRIGHT_NODE_MODULES / "playwright" / "package.json"
    if not pw_pkg.is_file():
        return False, f"missing playwright: {pw_pkg}"
    if not PLAYWRIGHT_BROWSER.is_dir():
        return False, (
            f"missing chromium binary: {PLAYWRIGHT_BROWSER} "
            f"(run: cd {SKILL_ROOT}/bin && "
            f"PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright "
            f"npx playwright install chromium-headless-shell)"
        )
    # node check
    node_path, tried = resolve_node()
    if node_path is None:
        return False, (
            "node not available (tried: " + "; ".join(tried) + "). "
            "Set BOOKPLATE_NODE_BIN=/path/to/node to override."
        )
    node = subprocess.run(
        [str(node_path), "-v"], capture_output=True, text=True, timeout=15,
        env=node_env(),
    )
    if node.returncode != 0 or not node.stdout.strip().startswith("v"):
        return False, (
            f"node found at {node_path} but runs badly: "
            f"{node.stderr.strip() or node.stdout.strip()}"
        )
    return True, "ok"


# ---------- book lookup ----------

def lookup_book(book_id: Any) -> tuple[str, str]:
    """Return (name, author) from aggregate/books.jsonl. Falls back to defaults."""
    if book_id is None:
        return ("未知书名", "")
    books_path = EXCERPT_AGGREGATE_DIR / "books.jsonl"
    if not books_path.is_file():
        return (f"未知书名 #{book_id}", "")
    bid_str = str(book_id)
    try:
        with books_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(obj.get("id", "")) == bid_str:
                    return (obj.get("name") or f"未知书名 #{book_id}", obj.get("author") or "")
    except OSError:
        pass
    return (f"未知书名 #{book_id}", "")


# ---------- slug ----------

def slug_for(name: str) -> str:
    """ASCII-only slug, max 30 chars. CJK / non-ASCII → short hash suffix."""
    if not name:
        return "unknown"
    # Try ASCII transliteration: keep alnum, dash, underscore
    ascii_only = re.sub(r"[^A-Za-z0-9_\-]+", "-", name).strip("-").lower()
    ascii_only = re.sub(r"-+", "-", ascii_only)
    if ascii_only and len(ascii_only) >= 4:
        return ascii_only[:30].rstrip("-")
    # Fallback: hash of name
    import hashlib
    h = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    # Try to preserve a short ASCII prefix if any
    prefix = "".join(c for c in ascii_only if c.isalnum())[:8]
    return (prefix + "-" + h)[:30].strip("-")


# ---------- template render ----------

HTML_TEMPLATE = Template("""<!doctype html>
<html lang="zh-CN" data-style="$style">
<head>
<meta charset="utf-8">
<title>$book_name · 藏书票</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@300;400;500;700;900&family=Noto+Sans+SC:wght@300;400;500;700&family=Playfair+Display:ital,wght@0,400;0,700;1,400&family=Inter:wght@300;400;500;700&family=IBM+Plex+Mono:wght@400;500&family=Cinzel:wght@400;700;900&family=Ma+Shan+Zheng&display=swap">
<style>
/* ===== bookplate — 1080×1440 (3:4) ===== */
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { width: 1080px; height: 1440px; overflow: hidden; background: #000; }
.poster {
  width: 1080px; height: 1440px; position: relative;
  display: flex; flex-direction: column;
  padding: 96px 96px 80px;
  font-family: "Noto Serif SC", serif;
}
/* shared chrome */
.bp-mark { font-size: 22px; letter-spacing: 0.4em; font-weight: 500; }
.bp-foot, .bp-foot-default {
  display: flex; justify-content: space-between; align-items: baseline;
  font-size: 20px; letter-spacing: 0.15em; font-weight: 400;
}
.bp-foot .bp-excerpt-id, .bp-foot-default .bp-excerpt-id {
  font-family: "IBM Plex Mono", monospace; font-size: 18px;
  letter-spacing: 0.05em;
}

/* === heraldic === */
.poster.heraldic {
  background: #f3ede0;
  color: #1a1410;
  font-family: "Playfair Display", "Noto Serif SC", serif;
  border: 14px solid #1a1410;
  box-shadow: inset 0 0 0 4px #f3ede0, inset 0 0 0 14px #1a1410;
}
.poster.heraldic .bp-shield {
  flex: 1; display: flex; align-items: center; justify-content: center;
  position: relative;
}
.poster.heraldic .bp-shield svg { width: 720px; height: 800px; }
.poster.heraldic .bp-shield .bp-excerpt {
  position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%);
  width: 620px; text-align: center; font-style: italic;
  font-size: 48px; line-height: 1.55; color: #1a1410;
  font-family: "Playfair Display", "Noto Serif SC", serif;
}

/* === 背景图 (印章晕染, 极弱只做底色肌理) === */
.bp-bg {
  position: absolute; inset: 0; z-index: 0;
  background-size: cover; background-position: center;
  opacity: 0.06; filter: grayscale(0.5) contrast(1.1) blur(14px);
  pointer-events: none;
}
.bp-bg-tint {
  /* 同色调的暖色蒙版, 让晕染色与印章红/纸质米融成一片 */
  position: absolute; inset: 0; z-index: 1;
  background: rgba(140, 60, 40, 0.18);
  mix-blend-mode: multiply;
  pointer-events: none;
}
.poster .bp-inner, .poster.heraldic .bp-shield, .poster .bp-head, .poster .bp-foot {
  position: relative; z-index: 3;
}
.poster.heraldic .bp-head { text-align: center; margin-bottom: 24px; }
.poster.heraldic .bp-head .bp-mark {
  font-family: "Cinzel", serif; letter-spacing: 0.6em; font-size: 26px;
  font-weight: 700;
}
.poster.heraldic .bp-book { text-align: center; font-size: 28px; margin-top: 24px; }
.poster.heraldic .bp-book .bp-author {
  font-style: italic; font-size: 22px; margin-top: 6px;
  letter-spacing: 0.1em; font-weight: 400;
}
.poster.heraldic .bp-foot { color: #1a1410; margin-top: 24px; padding-top: 18px; border-top: 2px solid #1a1410; }
/* === seal (朱砂篆刻) === */
.poster.seal {
  background: #f5efe0;
  color: #2b1c14;
  font-family: "Noto Serif SC", serif;
  border: 8px solid #b8362e;
}
/* === seal (A 极简文稿 · 居中左对齐) === */
/* 配图装饰: 主体留白, 透明度 18% + 中心 vignette 雾化, 只装饰周边不打扰文字.
   配图作 DOM 子元素, z=0 物理上就在最底; vignette z=1 雾化层; 内容 z=10 永远在顶. */
.poster.seal .bp-inner {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: stretch;       /* 子元素全宽 */
  justify-content: center;    /* 整块画布中部 */
  padding: 0 56px;            /* 摘录左右边距缩小 (原 120px) */
  position: relative;
}
/* 配图叠层: 18% 不透明度 + 模糊, 物理最底 */
.poster.seal .bp-bg-deco {
  position: absolute;
  inset: 0;
  background-size: cover;
  background-position: center;
  opacity: 0.18;
  filter: blur(3px) contrast(0.9);
  z-index: 0;
  pointer-events: none;
}
/* 中心雾化: 让文字区最干净, 配图只露在最外围 */
.poster.seal .bp-bg-vignette {
  position: absolute;
  inset: 0;
  background:
    radial-gradient(ellipse 70% 60% at center,
      rgba(245, 239, 224, 0.92) 0%,
      rgba(245, 239, 224, 0.75) 40%,
      rgba(245, 239, 224, 0.20) 75%,
      rgba(245, 239, 224, 0) 100%);
  z-index: 1;
  pointer-events: none;
}
.poster.seal .bp-inner {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: stretch;       /* 子元素全宽 */
  justify-content: center;    /* 整块画布中部 */
  padding: 0 56px;            /* 摘录左右边距缩小 (原 120px) */
  position: relative;
}
.poster.seal .bp-excerpt {
  font-size: 56px;
  line-height: 1.85;
  text-align: left;            /* 关键: 左对齐 */
  max-width: 880px;            /* 配合窄边距放宽 (原 720px) */
  margin: 0 auto;              /* 居中受控 */
  font-weight: 400;
  letter-spacing: 0.06em;
  font-family: "Noto Serif SC", serif;
  color: #1a1410;
  position: relative;          /* 浮在配图 + ::after vignette 之上 */
  z-index: 10;                 /* 必须 > ::after(z=1) */
}
.poster.seal .bp-seal {
  position: absolute;
  top: 80px;
  right: 96px;
  width: 64px;
  height: 64px;
  background: #b8362e;
  color: #f5efe0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: "Ma Shan Zheng", "Noto Serif SC", serif;
  font-size: 36px;
  font-weight: 900;
  line-height: 1;
  box-shadow: 0 0 0 3px #b8362e, 0 0 0 5px #f5efe0, 0 0 0 8px #b8362e;
  z-index: 10;                 /* 显式置顶, 配图绝不可挡 */
}
.poster.seal .bp-foot {
  position: absolute;
  bottom: 80px;
  left: 56px;                  /* 配合窄边距 (原 120px) */
  right: 56px;
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  font-size: 24px;             /* meta 字号增大 (原 18px) */
  letter-spacing: 0.12em;
  color: #2a1a0a;              /* 深色保对比 */
  font-family: "Noto Serif SC", serif;
  z-index: 10;
  gap: 32px;
}
.poster.seal .bp-foot span {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 50%;
}
/* 章节 (作者下方) — italic + 灰, 不抢书名/作者风头 */
.poster.seal .bp-foot-chapter {
  position: absolute;
  bottom: 56px; left: 56px; right: 56px;
  font-size: 18px; letter-spacing: 0.08em;
  font-style: italic; color: #6b5340; opacity: 0.85;
  font-family: "Noto Serif SC", serif;
  z-index: 10;
}

/* 配图装饰: 主体留白, 透明度 60%, 只装饰周边不打扰文字 */
.poster.seal .bp-bg-deco {
  position: absolute;
  inset: 0;
  background-size: cover;
  background-position: center;
  opacity: 0.18;               /* 主图先降到 18% 防止压字 */
  filter: blur(3px) contrast(0.9);
  z-index: 0;                  /* 最底层, 文字/印章/meta 全踩它 */
  pointer-events: none;
}
/* 中心雾化: 让文字区最干净, 配图只露在最外围 */
.poster.seal .bp-bg-vignette {
  position: absolute;
  inset: 0;
  background:
    radial-gradient(ellipse 70% 60% at center,
      rgba(245, 239, 224, 0.92) 0%,
      rgba(245, 239, 224, 0.75) 40%,
      rgba(245, 239, 224, 0.20) 75%,
      rgba(245, 239, 224, 0) 100%);
  z-index: 1;                  /* 雾化层, 把配图压下去让中心干净 */
  pointer-events: none;
}

/* === minimal (现代极简) === */
.poster.minimal {
  background: #f7f5f0;
  color: #0a0a0a;
  font-family: "Inter", "Noto Sans SC", sans-serif;
  border-top: 16px solid #0a0a0a;
}
.poster.minimal .bp-head {
  font-family: "IBM Plex Mono", monospace;
  font-size: 18px; letter-spacing: 0.3em; text-transform: uppercase;
  color: #6a6a6a; margin-bottom: 64px;
}
.poster.minimal .bp-body { flex: 1; display: flex; flex-direction: column; justify-content: center; }
.poster.minimal .bp-excerpt {
  font-family: "Noto Serif SC", serif;
  font-size: 68px; line-height: 1.45; font-weight: 400;
  color: #0a0a0a; letter-spacing: 0.02em;
  max-width: 920px;
}
.poster.minimal .bp-attribution {
  margin-top: 64px;
  display: flex; flex-direction: column; gap: 4px;
  font-size: 22px; letter-spacing: 0.05em; color: #0a0a0a;
}
.poster.minimal .bp-attribution .bp-book { font-weight: 500; }
.poster.minimal .bp-attribution .bp-author { color: #6a6a6a; font-size: 20px; }
.poster.minimal .bp-attribution .bp-chapter {
  font-size: 16px; color: #9a9a9a; letter-spacing: 0.08em;
  font-style: italic; margin-top: 2px;
}
.poster.minimal .bp-foot {
  margin-top: 48px;
  font-family: "IBM Plex Mono", monospace; font-size: 16px;
  color: #6a6a6a; letter-spacing: 0.1em;
}

/* === palette override (pick_palette 注入) === */
.poster { background: var(--bp-bg) !important; color: var(--bp-ink) !important; }
.poster.heraldic .bp-mark { color: var(--bp-ink); border-color: var(--bp-ink); }
.poster.heraldic .bp-excerpt { color: var(--bp-ink); }
.poster.heraldic .bp-foot { color: var(--bp-ink); border-color: var(--bp-ink); }
.poster.seal .bp-excerpt { color: var(--bp-ink); }
.poster.seal .bp-seal { background: var(--bp-accent); color: var(--bp-bg); }
.poster.seal .bp-foot { color: var(--bp-ink); }
.poster.minimal .bp-head { color: var(--bp-accent); }
.poster.minimal .bp-excerpt { color: var(--bp-ink); }
.poster.minimal .bp-book { color: var(--bp-ink); }
.poster.minimal .bp-author { color: var(--bp-accent); }
.poster.minimal .bp-foot { color: var(--bp-accent); }

/* === zhijian (纸间书摘 · 默认) ===
   纸纹米色 / 黑细线双层框 / 朱红 EX LIBRIS 与印章 / 顶栏 BOOKPLATE + issue / 底栏 日期+dim / 无配图 */
.poster.zhijian {
  background: #f4ecdc !important;
  color: #1a1410 !important;
  font-family: "Noto Serif SC", "Cinzel", serif;
  padding: 64px 64px 60px;
  /* 纸纹 */
  background-image:
    radial-gradient(ellipse at 30% 20%, rgba(180,150,100,.10), transparent 60%),
    radial-gradient(ellipse at 80% 90%, rgba(120,90,50,.08), transparent 50%),
    repeating-linear-gradient(0deg, rgba(120,90,50,.025) 0 1px, transparent 1px 4px),
    repeating-linear-gradient(90deg, rgba(120,90,50,.025) 0 1px, transparent 1px 4px) !important;
}
/* 双层框 */
.poster.zhijian::before {
  content: "";
  position: absolute; inset: 28px;
  border: 2px solid #1a1410;
  outline: 1px solid #1a1410;
  outline-offset: 5px;
  pointer-events: none;
}
/* 顶栏 */
.poster.zhijian .bp-zh-top {
  display: flex; justify-content: space-between; align-items: flex-start;
  margin-bottom: 64px;
  font-family: "Cinzel", "Noto Serif SC", serif;
}
.poster.zhijian .bp-zh-brand-en {
  font-size: 18px; letter-spacing: 0.35em; color: #1a1410; font-weight: 500;
}
.poster.zhijian .bp-zh-brand-cn {
  font-size: 13px; letter-spacing: 0.4em; color: #3a2c20; margin-top: 6px; opacity: .75;
}
.poster.zhijian .bp-zh-issue {
  text-align: right; font-size: 13px; letter-spacing: 0.25em; color: #3a2c20; line-height: 1.8;
}
.poster.zhijian .bp-zh-issue-vol {
  font-size: 28px; letter-spacing: 0.15em; color: #1a1410; font-weight: 700; margin-top: 4px;
}
/* EX LIBRIS 红细线 */
.poster.zhijian .bp-zh-exlibris {
  display: inline-block;
  font-family: "Cinzel", serif;
  font-size: 14px; letter-spacing: 0.4em; color: #b5331e;
  border-bottom: 1px solid #b5331e; padding-bottom: 4px;
  margin-bottom: 22px;
}
/* 书名块 */
.poster.zhijian .bp-zh-title-block { margin-bottom: 56px; }
.poster.zhijian .bp-zh-title {
  font-family: "Noto Serif SC", serif; font-weight: 900;
  font-size: 64px; line-height: 1.1; letter-spacing: 0.08em; color: #1a1410;
  margin-bottom: 10px;
}
.poster.zhijian .bp-zh-title-en {
  font-family: "Cinzel", serif; font-style: italic; font-weight: 500;
  font-size: 20px; letter-spacing: 0.15em; color: #3a2c20; margin-bottom: 14px;
}
.poster.zhijian .bp-zh-author {
  font-family: "Noto Serif SC", serif; font-size: 18px; letter-spacing: 0.25em; color: #1a1410;
  margin-bottom: 4px;
}
.poster.zhijian .bp-zh-author-en {
  font-family: "Cinzel", serif; font-size: 12px; letter-spacing: 0.2em; color: #3a2c20; opacity: .75;
}
.poster.zhijian .bp-zh-chapter {
  font-family: "Noto Serif SC", serif; font-size: 14px; letter-spacing: 0.18em;
  color: #6b5340; font-style: italic; opacity: 0.85;
  margin-top: 4px;
}
/* 摘录 */
.poster.zhijian .bp-zh-excerpt {
  position: relative;
  border-top: 1px solid #1a1410;
  border-bottom: 1px solid #1a1410;
  padding: 38px 0 32px;
  margin-bottom: 28px;
}
.poster.zhijian .bp-zh-quote {
  position: absolute; top: -34px; left: -4px;
  font-family: "Cinzel", serif; font-size: 64px; line-height: 1; color: #b5331e;
}
.poster.zhijian .bp-zh-excerpt p {
  font-family: "Noto Serif SC", serif; font-weight: 500;
  font-size: 34px; line-height: 2; color: #1a1410;
  text-align: justify; text-indent: 2em; letter-spacing: 0.04em;
}
/* 批注块 */
.poster.zhijian .bp-note {
  margin-top: 8px; padding: 18px 22px;
  border-left: 3px solid #b5331e;
  background: rgba(181, 51, 30, 0.04);
}
.poster.zhijian .bp-note-label {
  font-family: "Cinzel", serif; font-size: 11px; letter-spacing: 0.35em; color: #b5331e; margin-bottom: 6px;
}
.poster.zhijian .bp-note p {
  font-family: "Noto Serif SC", serif; font-size: 30px; line-height: 1.75; color: #3a2c20; font-style: normal;
}
/* 底栏 */
.poster.zhijian .bp-zh-foot {
  position: absolute;
  left: 64px; right: 64px; bottom: 84px;
  display: flex; justify-content: space-between; align-items: flex-end;
}
.poster.zhijian .bp-zh-meta { font-family: "Cinzel", "Noto Serif SC", serif; line-height: 1.8; }
.poster.zhijian .bp-zh-date { font-size: 13px; letter-spacing: 0.25em; color: #3a2c20; }
.poster.zhijian .bp-zh-dim { font-size: 16px; letter-spacing: 0.15em; color: #1a1410; font-weight: 500; }
.poster.zhijian .bp-zh-seal {
  width: 96px; height: 96px;
  background: #b5331e;
  display: flex; align-items: center; justify-content: center;
  font-family: "Noto Serif SC", serif; font-weight: 900;
  font-size: 56px; color: #f4ecdc;
  box-shadow: inset 0 0 0 2px #f4ecdc, inset 0 0 0 4px #b5331e;
  transform: rotate(-3deg);
}
</style>
</head>
<body>
<div class="poster $style" id="poster" style="--bp-bg:$bg;--bp-ink:$ink;--bp-accent:$accent;">
  $body
  <div class="bp-foot-default">
    <span class="bp-mark">$mark_text</span>
    <span class="bp-excerpt-id">№ $excerpt_id</span>
  </div>
</div>
</body>
</html>
""")


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_heraldic_body(book_name: str, author: str, content: str, bg_data_uri: str = "", palette: dict = None, chapter: str = "") -> str:
    excerpt = _esc(content)
    book = _esc(book_name)
    auth = _esc(author)
    chap = _esc(chapter) if chapter else ""
    p = palette or {"bg": PALETTE_VIBES["书斋"][0][0], "ink": PALETTE_VIBES["书斋"][0][1], "accent": PALETTE_VIBES["书斋"][0][2]}
    bg_hex, ink_hex, accent_hex = p["bg"], p["ink"], p["accent"]
    bg_layer = f'<div class="bp-bg" style="background-image:url(\'{bg_data_uri}\')"></div><div class="bp-bg-tint"></div>' if bg_data_uri else ""
    # 章节插在 author 下方 (y=640) — y=700 留 60px 行距
    chap_text = f'<text x="360" y="700" fill="{ink_hex}" font-family="Cinzel, serif" font-size="20" font-style="italic" text-anchor="middle" letter-spacing="2">{chap}</text>' if chap else ""
    return f"""
  <div class="bp-head">
    <div class="bp-mark">EX · LIBRIS</div>
  </div>
  {bg_layer}
  <div class="bp-shield">
    <svg viewBox="0 0 720 800" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <pattern id="hatch" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="6" stroke="{ink_hex}" stroke-width="1"/>
        </pattern>
      </defs>
      <!-- shield outline -->
      <path d="M 60 80 L 660 80 L 660 480 Q 660 700 360 780 Q 60 700 60 480 Z"
            fill="none" stroke="{ink_hex}" stroke-width="6"/>
      <path d="M 90 110 L 630 110 L 630 478 Q 630 680 360 750 Q 90 680 90 478 Z"
            fill="url(#hatch)" stroke="{ink_hex}" stroke-width="2"/>
      <!-- chief (top band) -->
      <path d="M 60 80 L 660 80 L 660 220 L 60 220 Z" fill="{ink_hex}"/>
      <text x="360" y="170" fill="{bg_hex}" font-family="Cinzel, serif" font-size="60" font-weight="700"
            text-anchor="middle" letter-spacing="14">{book[:14]}</text>
      <!-- bottom flourish -->
      <text x="360" y="640" fill="{ink_hex}" font-family="Playfair Display, serif" font-size="38" font-style="italic"
            text-anchor="middle">{auth[:20]}</text>
      {chap_text}
    </svg>
    <div class="bp-excerpt">{excerpt}</div>
  </div>
"""


def build_seal_body(book_name: str, author: str, content: str, bg_data_uri: str = "", palette: dict = None, chapter: str = "") -> str:
    """A 极简文稿: 米色单底, 摘录居中左对齐, 右上小印 1 颗, 底部书名/作者小字.
    可选配图(版画风装饰, 主体留白不打扰文字).
    配图作 DOM 子元素 .bp-bg-deco(z=0), vignette 作 .bp-bg-vignette(z=1),
    内容子元素 z=10, 物理上保证: 配图在底, 雾化在中, 内容在顶."""
    excerpt = _esc(content)
    # 短标题: 括号/逗号/句号前截断, 留主名
    short_book = re.split(r"[（(,，.!。!！]", book_name or "", 1)[0].strip() or "未知"
    short_auth = (author or "—").split(" ")[0]  # 多名作者取第一
    book = _esc(short_book)
    auth = _esc(short_auth)
    chap_html = f'<div class="bp-foot-chapter">{_esc(chapter)}</div>' if chapter else ""
    seal_char = _pick_seal_char(book_name, author, content)
    p = palette or {"bg": PALETTE_VIBES["秋意"][0][0], "ink": PALETTE_VIBES["秋意"][0][1], "accent": PALETTE_VIBES["秋意"][0][2]}
    bg_hex, ink_hex, accent_hex = p["bg"], p["ink"], p["accent"]
    bg_layer = ""
    if bg_data_uri:
        # 装饰图: 主体留白(opacity 18% + 中央 vignette 中心 92% 雾化), 让文字浮在干净区
        bg_layer = (
            f'<div class="bp-bg-deco" style="background-image:url(\'{bg_data_uri}\')"></div>'
            f'<div class="bp-bg-vignette"></div>'
        )
    return f"""
  <div class="bp-inner">
    {bg_layer}
    <div class="bp-seal">{seal_char}</div>
    <div class="bp-excerpt">{excerpt}</div>
    <div class="bp-foot">
      <span class="bp-foot-author">{auth}</span>
      <span class="bp-foot-book">《{book}》</span>
    </div>
    {chap_html}
  </div>
"""


def build_minimal_body(book_name: str, author: str, content: str, bg_data_uri: str = "", palette: dict = None, chapter: str = "") -> str:
    excerpt = _esc(content)
    book = _esc(book_name)
    auth = _esc(author)
    chap_html = f'<div class="bp-chapter">{_esc(chapter)}</div>' if chapter else ""
    p = palette or {"bg": PALETTE_VIBES["晴空"][0][0], "ink": PALETTE_VIBES["晴空"][0][1], "accent": PALETTE_VIBES["晴空"][0][2]}
    bg_hex, ink_hex, accent_hex = p["bg"], p["ink"], p["accent"]
    bg_layer = f'<div class="bp-bg" style="background-image:url(\'{bg_data_uri}\')"></div><div class="bp-bg-tint"></div>' if bg_data_uri else ""
    return f"""
  <div class="bp-head">BOOKPLATE · 藏書票</div>
  {bg_layer}
  <div class="bp-body">
    <div class="bp-excerpt">{excerpt}</div>
    <div class="bp-attribution">
      <div class="bp-book">{book}</div>
      <div class="bp-author">{auth if auth else "—"}</div>
      {chap_html}
    </div>
  </div>
"""


STYLE_BODY_BUILDERS = {
    "heraldic": build_heraldic_body,
    "seal": build_seal_body,
    "minimal": build_minimal_body,
}

STYLE_MARK = {
    "heraldic": "EX · LIBRIS",
    "seal": "藏書印譜",
    "minimal": "BOOKPLATE",
}


def _dim_for(created_time_ms: int) -> str:
    """从 createdTime (ms) 派生 dim 短串: 季·天气·时段·星期.
    季: createdTime 落北京时; 天气: 无外部数据, 永远 '阴' (米色调对阴最稳);
    时段: 0-5 深夜 / 6-11 晨 / 12-13 午 / 14-17 下午 / 18-22 夜 / 23 深夜;
    星期: 周一/二/…/日."""
    if not created_time_ms or created_time_ms < 10_000_000_000:
        return "夏 · 阴 · 午后 · 周末"
    from datetime import datetime
    from zoneinfo import ZoneInfo
    dt = datetime.fromtimestamp(created_time_ms / 1000, tz=ZoneInfo("Asia/Shanghai"))
    m = dt.month
    if m in (3, 4, 5): season = "春"
    elif m in (6, 7, 8): season = "夏"
    elif m in (9, 10, 11): season = "秋"
    else: season = "冬"
    h = dt.hour
    if h <= 5: tod = "深夜"
    elif h <= 11: tod = "晨"
    elif h <= 13: tod = "午"
    elif h <= 17: tod = "下午"
    elif h <= 22: tod = "夜"
    else: tod = "深夜"
    wkd = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][dt.weekday()]
    weekend = "周末" if dt.weekday() >= 5 else "工作日"
    return f"{season} · 阴 · {tod} · {weekend}"


def _author_nationality(author: str) -> str:
    """粗暴猜国别 (放进 [国] 槽). 失败返 '' (渲染时整段跳过)."""
    if not author: return ""
    a = author
    # 中文名 (2-4 字) → 默认中国 (留空, 用户原图里是 [德])
    jp = ["村上春树", "东野圭吾", "川端康成", "夏目漱石", "三岛由纪夫", "太宰治", "芥川龙之介", "谷崎润一郎", "松本清张", "渡边淳一"]
    en = ["Remarque", "Fitzgerald", "Hemingway", "Faulkner", "Steinbeck", "Marquez", "García", "García Márquez", "Tolstoy", "Dostoyevsky", "Chekhov", "Pushkin", "Gogol"]
    de = ["Remarque", "Mann", "Kafka", "Hesse", "Schiller", "Goethe", "Grass"]
    fr = ["Camus", "Sartre", "Hugo", "Flaubert", "Proust", "Saint-Exupéry", "Saint-Exupery"]
    for k in de:
        if k in a: return "德"
    for k in fr:
        if k in a: return "法"
    for k in jp:
        if k in a: return "日"
    for k in en:
        if k in a: return "美"
    return ""


def _format_book_title(book: str, max_chars: int = 8) -> str:
    """凯 旋 门 — 汉字之间插空格, 让排版看起来更松弛.
    max_chars: 截断到 N 字 (豆瓣抓的全名常 30+ 字, 框架装不下)."""
    if not book: return ""
    book = book.strip()
    # 短书名按原样, 长书名截到 max_chars + "..."
    if len(book) > max_chars:
        book = book[:max_chars].rstrip() + "…"
    return " ".join(list(book))


# 摘录过长时按画框自动缩排: 底栏是绝对定位的, 正文字号固定 (34px / 行高 2) 时
# 长摘录会直接穿过底栏、被画布裁掉 —— 生成出来的书票既缺尾句也压住日期和印章。
# 这里在页面内量一次真实高度, 只缩不放: 优先保持 34px 的排版, 装不下才逐级降到
# 18px; 连 18px 都装不下的极端长文再加硬裁 (overflow:hidden), 不让它溢出画框。
# 量高度必须等字体就位 —— document.fonts.ready 之后再量, 否则会按回退字体算。
_ZH_FIT_JS = r"""
<script>
(function () {
  function fit() {
    var box = document.querySelector('.bp-zh-excerpt');
    var foot = document.querySelector('.bp-zh-foot');
    if (!box || !foot) return;
    var p = box.querySelector('p');
    if (!p) return;
    var note = document.querySelector('.bp-note');
    var limit = foot.getBoundingClientRect().top - 18;
    function bottom() {
      var el = note || box;
      return el.getBoundingClientRect().bottom;
    }
    var size = 34;
    while (size > 18 && bottom() > limit) {
      size -= 1;
      p.style.fontSize = size + 'px';
      p.style.lineHeight = size >= 28 ? '2' : '1.9';
    }
    if (bottom() > limit) {
      // 极端长文: 18px 仍装不下时按超出量硬裁, 至少不要盖住底栏。
      // 批注块跟在正文后面, 所以按"超出多少"缩正文高度, 批注才会一起上来。
      var excess = Math.ceil(bottom() - limit);
      var h = p.getBoundingClientRect().height;
      var keep = Math.max(60, Math.floor(h - excess - 4));
      p.style.maxHeight = keep + 'px';
      p.style.overflow = 'hidden';
    }
  }
  function schedule() {
    if (window.requestAnimationFrame) requestAnimationFrame(fit);
    else fit();
  }
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(schedule).catch(schedule);
  }
  document.addEventListener('DOMContentLoaded', schedule);
  window.addEventListener('load', schedule);
  setTimeout(schedule, 1200);
})();
</script>
"""


def build_zhijian_body(book_name: str, author: str, content: str, bg_data_uri: str = "", palette: dict = None, created_time_ms: int = 0, idea: str = "", chapter: str = "") -> str:
    """纸间书摘 · 纸纹米色 / 黑边 / 朱红印章 / 无配图.
    信息架构: 顶栏品牌 + issue 号 → EX LIBRIS → 书名 (汉字间加空格) → 罗马字 → 国别 + 作者 → 引号+摘录 → 批注块 → 底栏日期+dim + 朱红印章."""
    book_raw = (book_name or "").strip()
    auth_raw = (author or "").strip()
    excerpt = _esc(content)
    # 书名: 先算罗马字 (用全名查表), 再截断+加空格
    book_en = _to_pinyin_book_title(book_raw)
    book_spaced = _esc(_format_book_title(book_raw, max_chars=8))
    nat = _author_nationality(auth_raw)
    author_cn = _esc(auth_raw)
    author_line = f"[ {nat} ] {author_cn}" if nat else author_cn
    dim = _dim_for(created_time_ms)
    ts = ""
    if created_time_ms and created_time_ms > 10_000_000_000:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        dt = datetime.fromtimestamp(created_time_ms / 1000, tz=ZoneInfo("Asia/Shanghai"))
        ts = dt.strftime("%Y . %m . %d")

    seal_char = _pick_seal_char(book_raw, auth_raw, content)
    note_html = ""
    if idea and idea.strip():
        note_html = (
            f'<div class="bp-note">'
            f'  <div class="bp-note-label">— 批 注 —</div>'
            f'  <p>{_esc(idea.strip())}</p>'
            f'</div>'
        )

    title_en_html = f'<div class="bp-zh-title-en">{_esc(book_en)}</div>' if book_en else ""
    author_en_html = f'<div class="bp-zh-author-en">{_esc(_to_pinyin_author(auth_raw))}</div>' if _to_pinyin_author(auth_raw) else ""
    chapter_html = f'<div class="bp-zh-chapter">{_esc(chapter)}</div>' if chapter else ""

    return f"""
  <style>.bp-foot-default{{display:none !important;}}</style>
  <div class="bp-zh-top">
    <div>
      <div class="bp-zh-brand-en">BOOKPLATE</div>
      <div class="bp-zh-brand-cn">纸 · 间 · 书 · 摘</div>
    </div>
    <div class="bp-zh-issue">
      <div>No. 14 / 06</div>
      <div class="bp-zh-issue-vol">VOL.I</div>
    </div>
  </div>

  <div class="bp-zh-exlibris">— EX LIBRIS —</div>

  <div class="bp-zh-title-block">
    <div class="bp-zh-title">{book_spaced}</div>
    {title_en_html}
    <div class="bp-zh-author">{author_line}</div>
    {author_en_html}
    {chapter_html}
  </div>

  <div class="bp-zh-excerpt">
    <div class="bp-zh-quote">“</div>
    <p>{excerpt}</p>
  </div>

  {note_html}

  <div class="bp-zh-foot">
    <div class="bp-zh-meta">
      <div class="bp-zh-date">{ts}</div>
      <div class="bp-zh-dim">{_esc(dim)}</div>
    </div>
    <div class="bp-zh-seal">{_esc(seal_char)}</div>
  </div>
""" + _ZH_FIT_JS


def _to_pinyin_book_title(book: str) -> str:
    """书名 → 罗马字. 中文 → 手敲常用译名表; 英文 → 保持原样.
    没命中就空 (用户原图也是空白时干净点)."""
    if not book: return ""
    T = {
        "凯旋门": "Arc de Triomphe",
        "了不起的盖茨比": "The Great Gatsby",
        "老人与海": "The Old Man and the Sea",
        "月亮与六便士": "The Moon and Sixpence",
        "局外人": "L'Étranger",
        "百年孤独": "Cien años de soledad",
        "1Q84": "1Q84",
        "挪威的森林": "Norwegian Wood",
        "海边的卡夫卡": "Kafka on the Shore",
        "舞！舞！舞！": "Dance, Dance, Dance",
        "基督山伯爵": "Le Comte de Monte-Cristo",
    }
    return T.get(book.strip(), book if re.match(r"^[A-Za-z0-9 ,.'!:&\-]+$", book) else "")


def _to_pinyin_author(author: str) -> str:
    """作者 → 罗马字. 优先按名查表; 英文名原样."""
    if not author: return ""
    A = {
        "埃里希·玛丽亚·雷马克": "Erich Maria Remarque",
        "欧内斯特·海明威": "Ernest Hemingway",
        "弗朗西斯·斯科特·菲茨杰拉德": "F. Scott Fitzgerald",
        "村上春树": "Haruki Murakami",
        "加西亚·马尔克斯": "García Márquez",
        "阿尔贝·加缪": "Albert Camus",
        "马塞尔·普鲁斯特": "Marcel Proust",
        "列夫·托尔斯泰": "Leo Tolstoy",
        "陀思妥耶夫斯基": "Fyodor Dostoyevsky",
    }
    a = author.strip()
    if a in A: return A[a]
    if re.match(r"^[A-Za-z .'\-]+$", a): return a
    return ""


STYLE_BODY_BUILDERS["zhijian"] = build_zhijian_body
STYLE_MARK["zhijian"] = "BOOKPLATE"


# legacy alias: old webui links (seal/heraldic/minimal) now all serve zhijian.
_STYLE_ALIAS = {
    "heraldic": "zhijian",
    "seal": "zhijian",
    "minimal": "zhijian",
}


def _extract_keywords(content: str, book: str, author: str) -> str:
    """基于摘录/书名/作者抽 1-3 个画面感强的关键字, 给 LLM 配图 prompt 用.
    优先意象字(海/风/月/窗/灯/雪/雨/星/花/山/云/桥/路/船/城/树/鸟/火/水/日/天/光/影/夜/春/秋),
    否则回退到书名/作者首字."""
    IMAGE_WORDS = [
        "海", "风", "月", "窗", "灯", "雪", "雨", "星", "花", "山", "云",
        "桥", "路", "船", "城", "树", "鸟", "火", "水", "日", "天", "光",
        "影", "夜", "春", "秋", "雾", "石", "楼", "酒", "路", "沙", "潮",
    ]
    text = (content or "") + (book or "") + (author or "")
    seen = []
    for ch in text:
        if ch in IMAGE_WORDS and ch not in seen:
            seen.append(ch)
        if len(seen) >= 3:
            break
    if not seen:
        # 回退: 拿书名或作者前 2 字
        for src in (book, author, content):
            if src and len(src) >= 2:
                return src[:2]
        return "书"
    return "、".join(seen)


def _try_pick_bg(content: str, book: str = "", author: str = "") -> str:
    """Try to pick a background image via illustrate.py; return data:URI or '' on failure.
    Failure is non-fatal: the renderer continues with no bg.
    """
    try:
        import subprocess
        here = Path(__file__).parent
        script = here / "illustrate.py"
        if not script.exists():
            return ""
        keywords = _extract_keywords(content, book, author)
        out = subprocess.run(
            [sys.executable, str(script), keywords, "--style", "woodcut"],
            capture_output=True, text=True, timeout=45,
        )
        if out.returncode != 0 or "---DATA-URI---" not in out.stdout:
            return ""
        _, _, payload = out.stdout.partition("---DATA-URI---")
        return payload.strip()
    except Exception:
        return ""


def render_html(excerpt: dict, style: str, with_bg: bool = True, seed: int = 0) -> str:
    # legacy alias 收敛: 旧三风格 URL/flag 全部 → zhijian (新默认)
    style = _STYLE_ALIAS.get(style, style)
    if style not in STYLE_BODY_BUILDERS:
        style = "zhijian"
    book_name, author = lookup_book(excerpt.get("bookId"))
    content = excerpt.get("content") or ""
    idea = excerpt.get("idea") or ""
    chapter = excerpt.get("chapter") or ""
    created_time_ms = int(excerpt.get("createdTime") or 0)
    if style == "zhijian":
        # 纸间风固定色, 不走 palette, 也不配图 (背景就是米色纸纹)
        palette = {"bg": "#f4ecdc", "ink": "#1a1410", "accent": "#b5331e", "vibe": "纸间"}
        body = build_zhijian_body(
            book_name, author, content,
            bg_data_uri="", palette=palette,
            created_time_ms=created_time_ms, idea=idea, chapter=chapter,
        )
    else:
        palette = pick_palette(content, book_name, author, style, seed=seed)
        bg = _try_pick_bg(content, book_name, author) if with_bg else ""
        body = STYLE_BODY_BUILDERS[style](book_name, author, content, bg_data_uri=bg, palette=palette, chapter=chapter)
    return HTML_TEMPLATE.substitute(
        style=style,
        book_name=book_name,
        body=body,
        mark_text=STYLE_MARK[style],
        excerpt_id=str(excerpt.get("id", "?")),
        bg=palette["bg"],
        ink=palette["ink"],
        accent=palette["accent"],
    )


# ---------- playwright capture ----------

NODE_RENDER = r"""
const { chromium } = require('playwright');
const fs = require('fs');

(async () => {
  const htmlPath = process.argv[2];
  const outPath = process.argv[3];
  const html = fs.readFileSync(htmlPath, 'utf-8');
  const browser = await chromium.launch();
  const ctx = await browser.newContext({
    viewport: { width: 1080, height: 1440 },
    deviceScaleFactor: 2,
  });
  const page = await ctx.newPage();
  await page.setContent(html, { waitUntil: 'networkidle' });
  // Give Google Fonts time to apply
  await page.waitForTimeout(1500);
  await page.locator('#poster').screenshot({ path: outPath, type: 'png' });
  await browser.close();
  console.log('rendered:', outPath);
})().catch(e => { console.error('playwright error:', e); process.exit(1); });
"""


def capture(html: str, out_png: Path) -> None:
    """Render the inline HTML to PNG via headless chromium (node + playwright).

    Self-contained: node_modules lives in bin/, the temp .cjs script and
    any ephemeral files live in bin/.cache/. The cd into bin/ is what
    makes `require('playwright')` resolve — that's the one trick we still
    carry over from the old guizang-social-card-interactive borrow.
    """
    PLAYWRIGHT_NODE_CACHE.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(html)
        html_tmp = f.name
    out_png.parent.mkdir(parents=True, exist_ok=True)
    # Write node script inside bin/ so require('playwright') resolves.
    node_script = PLAYWRIGHT_NODE_CACHE / f".bookplate-capture-{os.getpid()}-{int(time.time()*1000)}.cjs"
    node_script.write_text(NODE_RENDER, encoding="utf-8")
    node_path, tried = resolve_node()
    if node_path is None:
        raise RuntimeError(
            "node not available (tried: " + "; ".join(tried) + ")"
        )
    try:
        bash = f"""
set -e
cd "{SKILL_ROOT}/bin"
exec "{node_path}" "{node_script}" "{html_tmp}" "{out_png}"
"""
        result = subprocess.run(
            ["bash", "-c", bash],
            capture_output=True, text=True, timeout=60,
            env=node_env(),
        )
        if result.returncode != 0:
            print("--- stdout ---", file=sys.stderr)
            print(result.stdout, file=sys.stderr)
            print("--- stderr ---", file=sys.stderr)
            print(result.stderr, file=sys.stderr)
            print("--- end ---", file=sys.stderr)
            raise RuntimeError(f"playwright render failed (exit {result.returncode})")
        print(result.stdout.strip())
    finally:
        try:
            os.unlink(html_tmp)
            node_script.unlink(missing_ok=True)
        except OSError:
            pass


# ---------- main ----------

def load_config() -> dict:
    if not CONFIG_PATH.is_file():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def main(argv: list[str]) -> int:
    # 0. flags
    with_bg = True
    seed = 0
    style_override: str | None = None
    args = list(argv[1:])
    if "--no-bg" in args:
        with_bg = False
        args.remove("--no-bg")
    if "--random-palette" in args:
        seed = int(time.time()) % 100000
        args.remove("--random-palette")
    if "--style" in args:
        i = args.index("--style")
        if i + 1 >= len(args):
            print(f"usage: --style <{'|'.join(BOOKPLATE_STYLES)}>  (default {DEFAULT_BOOKPLATE_STYLE})", file=sys.stderr)
            return 1
        style_override = args[i + 1]
        if style_override not in BOOKPLATE_STYLES:
            print(f"unknown --style value: {style_override!r} (want {'|'.join(BOOKPLATE_STYLES)})", file=sys.stderr)
            return 1
        args.pop(i)
        args.pop(i)
    if not args:
        print(f"usage: render.py [--no-bg] [--random-palette] [--style {'|'.join(BOOKPLATE_STYLES)}] <excerpt.json|->  (default style: {DEFAULT_BOOKPLATE_STYLE})", file=sys.stderr)
        return 1
    src = args[0]
    if src == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(src).read_text(encoding="utf-8")
    try:
        excerpt = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"bad json: {e}", file=sys.stderr)
        return 1

    # 2. config
    cfg = load_config()
    output_dir = cfg.get("output_dir") or DEFAULT_OUTPUT_DIR
    if not output_dir.endswith("/"):
        output_dir += "/"

    # 3. playwright health (self-contained in bin/)
    ok, msg = check_playwright_dep()
    if not ok:
        print(f"playwright dep missing: {msg}", file=sys.stderr)
        return 2

    # 4. style: explicit --style overrides default (zhijian)
    book_name, author = lookup_book(excerpt.get("bookId"))
    style = style_override or DEFAULT_BOOKPLATE_STYLE

    # 5. build + capture
    html = render_html(excerpt, style, with_bg=with_bg, seed=seed)
    ts = time.strftime("%Y%m%d-%H%M%S")
    slug = slug_for(book_name) if book_name else "unknown"
    out_path = Path(output_dir) / f"{ts}-{slug}.png"

    # /fs FUSE: write to /tmp first, then cp
    if str(out_path).startswith("/fs/"):
        tmp_png = Path("/tmp") / out_path.name
        capture(html, tmp_png)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["cp", str(tmp_png), str(out_path)], check=True)
        tmp_png.unlink(missing_ok=True)
    else:
        capture(html, out_path)

    print(f"OK style={style} out={out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
