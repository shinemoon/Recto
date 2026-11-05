"""外观层: 皮肤 (skin) 与外观配置。

设计成三层, 只有第一层需要懂:

  1. **token 层** —— 一个皮肤 = 一组 `--rc-*` 变量 (色板 / 字体 / 圆角 / 疏密)。
     `assets/recto.css` 只认变量, 不写死任何颜色值。
  2. **外壳层** —— `shell.py` 出 `<html>` 骨架, 导航项写在 `NAV` 里 (加一项 = 加一行)。
  3. **扩展层** —— 三种不改代码就能改外观的方式:
       * `data/skins/*.json`  放自制/第三方皮肤 (见 `load_custom_skins`)
       * `appearance.json` 的 `custom_css`  追加任意 CSS
       * `appearance.json` 的 `font_scale / density / radius_scale / heading`
         在皮肤之上再做一次微调

配置落在 `<recto.data_dir>/appearance.json`, 单用户全局生效。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from recto.core.config import get_config

# ---------------------------------------------------------------- 内置皮肤

# 每个值都对应 assets/recto.css 里的一个 --rc-* 变量。
# 新增皮肤只需要复制一份再改颜色 —— 不要往 CSS 里写颜色。
#
# 默认皮肤是 Material 3 的两套 (浅色 / 深色); paper / slate / mist / sepia
# 是备选: 骨架 (圆角 / 高度 / 组件) 依旧是 Material, 只换色板。
BUILTIN_SKINS: dict[str, dict] = {
    "material": {
        "label": "Material",
        "desc": "Material 3 浅色 · 圆形按钮 · 阴影卡片 · 默认",
        "mode": "light",
        "tokens": {
            "bg": "#fef7ff", "surface": "#ffffff", "surface-2": "#f3edf7",
            "sunk": "#ece6f0", "fg": "#1d1b20", "dim": "#49454f",
            "mute": "#79747e", "line": "#cac4d0", "line-2": "#79747e",
            "accent": "#6750a4", "accent-fg": "#ffffff",
            "accent-soft": "#eaddff", "ok": "#146c2e", "warn": "#8a5a00",
            "err": "#b3261e",
        },
    },
    "material-dark": {
        "label": "Material 深色",
        "desc": "Material 3 深色 · 夜间 · 低亮度",
        "mode": "dark",
        "tokens": {
            "bg": "#141218", "surface": "#1d1b20", "surface-2": "#2b2930",
            "sunk": "#0f0d13", "fg": "#e6e0e9", "dim": "#cac4d0",
            "mute": "#938f99", "line": "#49454f", "line-2": "#79747e",
            "accent": "#d0bcff", "accent-fg": "#381e72",
            "accent-soft": "#4f378b", "ok": "#6dd58c", "warn": "#f5c26b",
            "err": "#f2b8b5",
        },
    },
    "paper": {
        "label": "纸",
        "desc": "暖白纸面 · 衬线标题 · 默认",
        "mode": "light",
        "tokens": {
            "bg": "#f4efe4", "surface": "#fffcf5", "surface-2": "#f0e9da",
            "sunk": "#eae2d0", "fg": "#1d1a16", "dim": "#5e574c",
            "mute": "#8b8272", "line": "#ded2ba", "line-2": "#c8bb9f",
            "accent": "#9a3b2f", "accent-fg": "#fff8f2",
            "accent-soft": "#f3e3dc", "ok": "#3d6b3a", "warn": "#8a5b13",
            "err": "#a3312a",
        },
    },
    "slate": {
        "label": "石板",
        "desc": "深色 · 低亮度 · 夜间",
        "mode": "dark",
        "tokens": {
            "bg": "#14161a", "surface": "#1b1e24", "surface-2": "#22262d",
            "sunk": "#111316", "fg": "#e8e6e1", "dim": "#969ba5",
            "mute": "#6e747e", "line": "#2a2e35", "line-2": "#3a4048",
            "accent": "#c9a96a", "accent-fg": "#1a1509",
            "accent-soft": "#2b2620", "ok": "#7fb069", "warn": "#d9b06c",
            "err": "#d97a6c",
        },
    },
    "mist": {
        "label": "薄雾",
        "desc": "冷白 · 无衬线 · 清爽",
        "mode": "light",
        "tokens": {
            "bg": "#f6f8fa", "surface": "#ffffff", "surface-2": "#f1f3f6",
            "sunk": "#eceff3", "fg": "#171a1f", "dim": "#5b6472",
            "mute": "#8a93a1", "line": "#e2e6ec", "line-2": "#ccd3dc",
            "accent": "#2f6f9f", "accent-fg": "#ffffff",
            "accent-soft": "#e4eef6", "ok": "#2f7d4f", "warn": "#8c6208",
            "err": "#b3372f",
        },
    },
    "sepia": {
        "label": "旧书",
        "desc": "棕黄 · 偏黄底 · 长读",
        "mode": "light",
        "tokens": {
            "bg": "#ece1c9", "surface": "#f8f1df", "surface-2": "#e8dcc0",
            "sunk": "#e2d4b4", "fg": "#241d13", "dim": "#5b4d38",
            "mute": "#8a7960", "line": "#d3c3a1", "line-2": "#bdaa86",
            "accent": "#7a4b1e", "accent-fg": "#fdf6e7",
            "accent-soft": "#e9dcc4", "ok": "#4b6b33", "warn": "#8a5b13",
            "err": "#9c3222",
        },
    },
}

# 与皮肤无关的骨架参数 (可从配置覆盖)
_DEFAULTS = {
    "skin": "material",
    "font_scale": 1.0,
    "density": "comfortable",   # compact | comfortable | roomy
    "radius_scale": 0.0,   # 0 = 直角 (默认)
    "heading": "auto",          # auto | serif | sans
    "custom_css": "",
    "custom_skins": {},         # id -> {"label","mode","tokens":{...},"extra_css":""}
}

_DENSITY = {  # (基础字号, 行高, 卡片内边距, 区块间距)
    "compact": (14.5, 1.55, 12, 10),
    "comfortable": (15.5, 1.62, 16, 14),
    "roomy": (16.5, 1.72, 20, 18),
}

_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_HEX = re.compile(r"^#[0-9a-fA-F]{3,8}$")


def data_dir() -> Path:
    c = get_config()
    return c.data_dir


def _conf_path() -> Path:
    return data_dir() / "appearance.json"


def skins_dir() -> Path:
    return data_dir() / "skins"


# ---------------------------------------------------------------- 配置读写

def load_conf() -> dict:
    conf = dict(_DEFAULTS)
    conf["custom_skins"] = {}
    p = _conf_path()
    try:
        raw = json.loads(p.read_text("utf-8"))
        if isinstance(raw, dict):
            for k in _DEFAULTS:
                if k in raw:
                    conf[k] = raw[k]
    except FileNotFoundError:
        pass
    except Exception:  # noqa: BLE001 — 配置坏了不该让页面 500
        pass
    if not isinstance(conf.get("custom_skins"), dict):
        conf["custom_skins"] = {}
    conf["custom_skins"].update(load_custom_skins())
    return conf


def save_conf(conf: dict) -> dict:
    """写外观配置 (只保留认识的键, 值做一次清洗)."""
    clean = dict(_DEFAULTS)
    clean["custom_skins"] = {}
    for k, default in _DEFAULTS.items():
        if k in conf:
            clean[k] = conf[k]
    clean["skin"] = _clean_id(clean["skin"], fallback="paper")
    clean["font_scale"] = _clamp(clean.get("font_scale"), 0.8, 1.6, 1.0)
    clean["radius_scale"] = _clamp(clean.get("radius_scale"), 0.0, 2.5, 0.0)
    clean["density"] = (clean.get("density") if clean.get("density") in _DENSITY
                        else "comfortable")
    clean["heading"] = (clean.get("heading") if clean.get("heading") in
                        ("auto", "serif", "sans") else "auto")
    clean["custom_css"] = _sanitize_css(str(clean.get("custom_css") or ""))
    # 自制皮肤
    own = {}
    for sid, skin in (clean.get("custom_skins") or {}).items():
        s = _clean_skin(sid, skin)
        if s:
            own[sid] = s
    clean["custom_skins"] = own
    p = _conf_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(p)
    return clean


def _clamp(v, lo, hi, default):
    try:
        return max(lo, min(hi, float(v)))
    except Exception:  # noqa: BLE001
        return default


def _clean_id(v, fallback: str = "paper") -> str:
    v = str(v or "").strip().lower()
    return v if _SAFE_ID.match(v) else fallback


def _sanitize_css(css: str) -> str:
    """只挡明显危险的东西, 不做完整 CSS 解析。

    外观是单用户自管, 但 `<` 与 `expression(` / `@import` 会把页面变成攻击面,
    顺便也会让 `</style>` 意外闭合标签。
    """
    css = css.replace("<", "").replace(">", "")
    css = re.sub(r"(?i)expression\s*\(", "x(", css)
    css = re.sub(r"(?i)@import", "/*import*/", css)
    if len(css) > 20000:
        css = css[:20000]
    return css


def _clean_skin(sid, skin) -> dict | None:
    sid = _clean_id(sid, fallback="")
    if not sid or not isinstance(skin, dict):
        return None
    base = str(skin.get("base") or "paper")
    if base not in BUILTIN_SKINS:
        base = "paper"
    tokens = dict(BUILTIN_SKINS[base]["tokens"])
    for k, v in (skin.get("tokens") or {}).items():
        if not isinstance(v, str):
            continue
        if _HEX.match(v.strip()) or re.match(r"^[a-zA-Z0-9 ,.%()#-]{1,64}$", v):
            tokens[k] = v.strip()
    return {
        "label": str(skin.get("label") or sid)[:40],
        "mode": "dark" if skin.get("mode") == "dark" else
                ("light" if skin.get("mode") == "light" else BUILTIN_SKINS[base]["mode"]),
        "base": base,
        "tokens": tokens,
        "extra_css": _sanitize_css(str(skin.get("extra_css") or "")),
    }


# ---------------------------------------------------------------- 外部皮肤

def load_custom_skins() -> dict:
    """`<data_dir>/skins/*.json` → 皮肤表。

    文件形态 (最小):
        {"id": "warm", "label": "暖", "base": "paper",
         "tokens": {"accent": "#a3542a"}, "extra_css": "..."}
    文件名 (去掉 .json) 就是 id, 可与 id 字段不一致 (以文件名为准, 便于改名)。
    """
    out: dict[str, dict] = {}
    d = skins_dir()
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        try:
            raw = json.loads(p.read_text("utf-8"))
        except Exception:  # noqa: BLE001 — 坏文件跳过, 不影响其它皮肤
            continue
        if not isinstance(raw, dict):
            continue
        raw.setdefault("id", p.stem)
        skin = _clean_skin(p.stem, raw)
        if skin:
            out[p.stem] = skin
    return out


# ---------------------------------------------------------------- 渲染成 CSS

def all_skins(conf: dict | None = None) -> dict[str, dict]:
    conf = conf or load_conf()
    out = {k: {"label": v["label"], "desc": v["desc"], "mode": v["mode"],
               "tokens": dict(v["tokens"]), "builtin": True}
           for k, v in BUILTIN_SKINS.items()}
    for sid, s in (conf.get("custom_skins") or {}).items():
        out[sid] = {"label": s["label"], "desc": "自制 · 基于 " +
                    BUILTIN_SKINS.get(s.get("base", "paper"), {}).get("label", "?"),
                    "mode": s["mode"], "tokens": dict(s["tokens"]),
                    "extra_css": s.get("extra_css") or "", "builtin": False}
    return out


def resolve(conf: dict | None = None) -> tuple[str, dict, dict]:
    """→ (skin_id, tokens, conf)  —— 选中的皮肤不存在时退回默认。"""
    conf = conf or load_conf()
    skins = all_skins(conf)
    sid = conf.get("skin") if conf.get("skin") in skins else "paper"
    tokens = dict(skins[sid]["tokens"])
    tokens["mode"] = skins[sid].get("mode") or "light"   # 供 CSS/阴影判断
    return sid, tokens, conf


# Material 3 语义角色 —— 皮肤可以只给老的色板 key (accent/line/...),
# 这里补出角色名。皮肤自己写了同名 token 就不覆盖。
_M3_ROLES = {
    "primary": "var(--rc-accent)",
    "on-primary": "var(--rc-accent-fg)",
    "primary-container": "var(--rc-accent-soft)",
    "on-primary-container": "var(--rc-fg)",
    "secondary-container": "color-mix(in srgb, var(--rc-accent) 15%, var(--rc-surface-2))",
    "on-secondary-container": "var(--rc-fg)",
    "surface-container": "var(--rc-surface-2)",
    "surface-container-high": "color-mix(in srgb, var(--rc-surface-2) 72%, var(--rc-line))",
    "surface-variant": "var(--rc-surface-2)",
    "on-surface-variant": "var(--rc-dim)",
    "outline": "var(--rc-line-2)",
    "outline-variant": "var(--rc-line)",
    "scrim": "rgba(0,0,0,.42)",
}


def _elevations(dark: bool) -> dict:
    if dark:
        return {
            "elev-1": "0 1px 3px 1px rgba(0,0,0,.42), 0 1px 2px rgba(0,0,0,.30)",
            "elev-2": "0 2px 6px 2px rgba(0,0,0,.45), 0 1px 2px rgba(0,0,0,.30)",
            "elev-3": "0 4px 8px 3px rgba(0,0,0,.48), 0 1px 3px rgba(0,0,0,.36)",
            "elev-4": "0 8px 24px 4px rgba(0,0,0,.55), 0 2px 6px rgba(0,0,0,.40)",
        }
    return {
        "elev-1": "0 1px 2px rgba(0,0,0,.10), 0 1px 3px 1px rgba(0,0,0,.06)",
        "elev-2": "0 1px 2px rgba(0,0,0,.10), 0 2px 6px 2px rgba(0,0,0,.08)",
        "elev-3": "0 4px 8px 3px rgba(0,0,0,.08), 0 1px 3px rgba(0,0,0,.12)",
        "elev-4": "0 8px 24px 4px rgba(0,0,0,.12), 0 2px 6px rgba(0,0,0,.10)",
    }


def _var_block(tokens: dict, conf: dict) -> str:
    fs, lh, pad, gap = _DENSITY.get(conf.get("density") or "comfortable",
                                   _DENSITY["comfortable"])
    scale = float(conf.get("font_scale") or 1.0)
    rs = _clamp(conf.get("radius_scale"), 0.0, 2.5, 0.0)
    heading = conf.get("heading") or "auto"
    dark = tokens.get("mode") == "dark"  # 一般不在这里判断, 但 shadow 需要

    lines = []
    have = set(tokens)
    for k, v in tokens.items():
        if k == "mode":
            continue
        lines.append(f"  --rc-{k}: {v};")
    for name, expr in _M3_ROLES.items():      # Material 3 角色名
        if name not in have:
            lines.append(f"  --rc-{name}: {expr};")
    elev = _elevations(dark)
    for k, v in elev.items():
        if k not in have:
            lines.append(f"  --rc-{k}: {v};")
    lines += [
        f"  --rc-fs: {round(fs * scale, 2)}px;",
        f"  --rc-fs-sm: {round(13 * scale, 2)}px;",
        f"  --rc-fs-xs: {round(11.5 * scale, 2)}px;",
        f"  --rc-lh: {lh};",
        f"  --rc-pad: {pad}px;",
        f"  --rc-gap: {gap}px;",
        f"  --rc-radius: {round(9 * rs, 1)}px;",
        f"  --rc-radius-lg: {round(14 * rs, 1)}px;",
        f"  --rc-radius-pill: {round(999 * rs, 0)}px;",
        f"  --rc-radius-card: {round(16 * rs, 1)}px;",
        f"  --rc-radius-field: {round(4 * rs, 1)}px;",
        f"  --rc-radius-chip: {round(8 * rs, 1)}px;",
        f"  --rc-measure: 1180px;",
        f"  --rc-reading: 760px;",
        "  --rc-sans: " + ('"Roboto","Inter","Helvetica Neue",-apple-system,"PingFang SC",'
                          '"Noto Sans CJK SC","Microsoft YaHei",sans-serif;'),
        "  --rc-serif: " + ('"Source Serif 4","Source Serif Pro","Source Han Serif SC",'
                           '"Noto Serif SC","Songti SC","STSong",Georgia,serif;'),
        "  --rc-mono: " + ('"Roboto Mono","JetBrains Mono",ui-monospace,SFMono-Regular,'
                          '"SF Mono",Menlo,Consolas,monospace;'),
    ]
    if heading == "serif":
        lines.append("  --rc-heading: var(--rc-serif);")
    elif heading == "sans":
        lines.append("  --rc-heading: var(--rc-sans);")
    else:  # auto: Material 一律无衬线标题; 纸感皮肤才用衬线
        lines.append("  --rc-heading: " +
                     ("var(--rc-serif);" if tokens.get("accent") in
                      ("#9a3b2f", "#7a4b1e") else "var(--rc-sans);"))
    lines.append("  --rc-shadow: var(--rc-elev-1);")
    lines.append("  --rc-shadow-pop: var(--rc-elev-3);")
    lines.append("  --rc-state: " +
                 ("rgba(230,224,233,.10);" if dark else "rgba(29,27,32,.07);"))
    lines.append("  --rc-state-strong: " +
                 ("rgba(230,224,233,.16);" if dark else "rgba(29,27,32,.11);"))
    return ":root{\n" + "\n".join(lines) + "\n}\n"


def skin_css(conf: dict | None = None) -> str:
    """选中皮肤的 CSS —— `/assets/skin.css` 的响应体 (no-store)。"""
    sid, tokens, conf = resolve(conf)
    skins = all_skins(conf)
    css = ["/* Recto skin: %s — generated, do not edit */" % sid]
    css.append(_var_block(tokens, conf))
    dark = tokens.get("mode") == "dark"
    css.append("html{color-scheme:%s;}" % ("dark" if dark else "light"))
    if dark:
        # 标志是"纯黑 + alpha"的位图 (见 tools/make_logo.py), 深色皮肤下直接反相即可,
        # 透明区域不受影响。深色皮肤换标志图也不需要重出图。
        css.append("html img.rc-logo{filter:invert(1);}")
    extra = (skins.get(sid) or {}).get("extra_css") or ""
    if extra:
        css.append("/* skin extra */\n" + extra)
    if conf.get("custom_css"):
        css.append("/* user css */\n" + conf["custom_css"])
    return "\n".join(css) + "\n"


def swatches(sid: str, conf: dict | None = None) -> list[str]:
    """皮肤预览用的小色块 —— 外壳与外观页共用, 避免两处各写一份。"""
    skins = all_skins(conf)
    t = (skins.get(sid) or skins.get("paper") or {}).get("tokens") or {}
    return [t.get("bg", "#fff"), t.get("surface", "#fff"),
            t.get("fg", "#000"), t.get("accent", "#000")]
