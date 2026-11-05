"""页面外壳 —— 所有分区共用同一副骨架、同一份导航、同一个登出入口。

一个页面 = `document(title=..., body=..., active=...)`。
`NAV` 是唯一的一份导航表: 加一节 = 加一行; 改名 = 改一处。
`active` 用它来控制高亮 —— 单页内的面板用 `data-pane`, 跨页的分区用 key 匹配。
"""
from __future__ import annotations

import html
import json as _json
from pathlib import Path

from . import skins

ASSET_DIR = Path(__file__).resolve().parent / "assets"

# 品牌名。界面上一律中英文并写 `云阅 Recto` (用户口径: 中英文都要)。
# BRAND 只留作"纯中文"判断, 展示一律用 BRAND_FULL。
BRAND = "云阅"
BRAND_EN = "Recto"
BRAND_FULL = BRAND + " " + BRAND_EN   # 云阅 Recto
# 顶栏那一段: 英文用小一号弱色, 视觉上仍是同一个词标
BRAND_HTML = BRAND + '<span class="rc-brand-en">' + BRAND_EN + '</span>'


# key, 文案, href, 说明 (说明只出现在 /appearance 的导航清单里)
# 「书架」是入口 —— 落地页就是书架, 概览那一屏已拆进各分区。
# 阅读增强已隐藏 (语料另有独立分区, 日志并入云端同步)。
NAV: list[tuple[str, str, str]] = [
    ("library", "书架", "/#library"),
    ("sync", "云端同步", "/#sync"),
    ("excerpts", "摘录", "/admin/"),
    ("corpus", "语料", "/corpus/"),
    ("appearance", "外观", "/appearance"),
]

# 这些是控制台单页里的面板 —— 点它们不跳页, 只切面板
PANE_KEYS = {"library", "sync"}

# 摘录后台的二级导航 —— 单页内的面板, 点它不跳页 (无 JS 时退化成 ?pane= 请求)
SUB_NAV: list[tuple[str, str, str]] = [
    ("home", "首页", "/admin/?pane=home"),
    ("excerpts", "摘录", "/admin/?pane=excerpts"),
    ("books", "书", "/admin/?pane=books"),
    ("events", "事件", "/admin/?pane=events"),
    ("tools", "工具", "/admin/?pane=tools"),
]


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


# --- 标志 (logo) ---------------------------------------------------------
# 原图 `logo.png` 由工具 `tools/make_logo.py` 加工成透明派生图放进 assets/:
#   logo-mark.png = 只有图徽 (顶栏, 跟文字品牌并排)
#   logo-full.png = 图徽 + 文字 (登录卡片, 单独就能认)
# favicon / apple-touch-icon 同源, 浏览器标签页也用同一个标志。
LOGO_MARK = ASSET_DIR / "logo-mark.png"          # 346x128 (顶栏显示高 22px)
LOGO_FULL = ASSET_DIR / "logo-full.png"          # 500x320 (登录卡片显示高 64px)
FAVICON = ASSET_DIR / "favicon.png"
TOUCH_ICON = ASSET_DIR / "apple-touch-icon.png"


def logo_mark(cls: str = "rc-logo") -> str:
    """顶栏图徽。`alt=""` —— 紧跟着就是文字品牌, 读屏不必念两遍。"""
    return (f'<img class="{cls}" src="/assets/logo-mark.png" alt="" '
            'width="346" height="128" decoding="async">')


def logo_full(cls: str = "rc-logo rc-logo-full") -> str:
    """完整标志 (图徽 + 文字)。只用在登录卡片顶部。"""
    return (f'<img class="{cls}" src="/assets/logo-full.png" alt="{_e(BRAND_FULL)}" '
            'width="500" height="320" decoding="async">')


def favicon_links() -> str:
    return ('<link rel="icon" type="image/png" href="/assets/favicon.png">'
            '<link rel="apple-touch-icon" href="/assets/apple-touch-icon.png">')


def brand(small: str = "") -> str:
    """顶栏品牌。`small` 只在显式传入时渲染 (默认顶栏只有「云阅 Recto」)。"""
    tail = f'<small>{_e(small)}</small>' if small else ""
    return (
        '<a class="rc-brand" href="/">'
        f'{logo_mark()}{BRAND_HTML}{tail}</a>'
    )


def _display_name(user: str | None) -> str:
    """顶栏显示的名字: 设置页里的昵称优先, 没设就用登录名。"""
    try:
        from recto.core import settings

        return settings.display_name(user)
    except Exception:  # noqa: BLE001 — 设置读不出来不该让页面挂
        return (user or "").strip()


def _avatar_text(name: str) -> str:
    return name[:1].upper() if name else "⚙"


def nav_html(active: str = "", user: str | None = None) -> str:
    out = ['<nav class="rc-nav" aria-label="主导航">']
    for i, (key, label, href) in enumerate(NAV):
        if key == "appearance" and i:
            out.append('<span class="rc-nav-sep" aria-hidden="true"></span>')
        cur = ' aria-current="page"' if key == active else ""
        pane = f' data-pane-link="{key}"' if key in PANE_KEYS else ""
        out.append(f'<a href="{_e(href)}"{pane}{cur}>{_e(label)}</a>')
    out.append("</nav>")
    label = _display_name(user)
    right = ['<div class="rc-user">']
    if label:
        right.append(f'<span class="rc-user-name">{_e(label)}</span>')
    # 头像圆点 = 设置入口 (外观设置从设置页里进)
    right.append('<a class="rc-avatar" href="/settings" title="设置" aria-label="设置">'
                 f'{_e(_avatar_text(label))}</a>')
    right.append('<a href="/logout" title="退出登录">退出</a>')
    right.append("</div>")
    return "".join(out) + "".join(right)


def subnav_html(active: str = "") -> str:
    """摘录后台的二级导航 —— 单页内的面板, 点它只切面板, 不跳页。"""
    out = ['<nav class="rc-subnav" aria-label="摘录导航">']
    for key, label, href in SUB_NAV:
        cur = ' aria-current="page"' if key == active else ""
        out.append(f'<a href="{_e(href)}" data-pane-link="{_e(key)}"{cur}>{_e(label)}</a>')
    out.append("</nav>")
    return "".join(out)


def head(kicker: str = "", title: str = "", sub: str = "",
         actions: str = "", row: bool = False) -> str:
    """分区页头 —— 所有页面的第一屏用同一套层次 (眉题 / 标题 / 说明 / 操作)。"""
    parts = ['<header class="rc-head">']
    if row:
        parts.append('<div class="rc-head-row"><div>')
    if kicker:
        parts.append(f'<div class="rc-kicker">{_e(kicker)}</div>')
    if title:
        parts.append(f"<h1>{_e(title)}</h1>")
    if sub:
        parts.append(f'<div class="rc-sub">{sub}</div>')
    if row:
        parts.append("</div>" + (f'<div class="rc-head-actions">{actions}</div>'
                                if actions else "") + "</div>")
    elif actions:
        parts.append(f'<div class="rc-head-actions">{actions}</div>')
    parts.append("</header>")
    return "".join(parts)


# 面板切换 + 皮肤预热。够小, 直接内联, 不多一次请求。
# 面板可以懒加载: `<section data-pane="x" data-pane-src="/admin/pane/x">` ——
# 第一次切到它时才去取, 取回的内容就长驻在 DOM 里, 之后切换是零请求。
_SHELL_JS = r"""
(function () {
  var panes = Array.prototype.slice.call(document.querySelectorAll('[data-pane]'));
  var main = document.querySelector('.rc-main');

  function panesOf(name) {
    return panes.filter(function (p) { return p.dataset.pane === name; });
  }
  function findPane(name) {
    for (var i = 0; i < panes.length; i++) if (panes[i].dataset.pane === name) return panes[i];
    return null;
  }

  // innerHTML 里插进去的 <script> 不会执行 —— 得手动搬一次
  function injectScripts(root) {
    if (!root) return;
    var list = Array.prototype.slice.call(root.querySelectorAll('script'));
    list.forEach(function (old) {
      var s = document.createElement('script');
      for (var i = 0; i < old.attributes.length; i++) {
        s.setAttribute(old.attributes[i].name, old.attributes[i].value);
      }
      s.text = old.textContent;
      old.parentNode.replaceChild(s, old);
    });
  }

  function loadPane(p, done) {
    var src = p.dataset.paneSrc;
    if (!src || p.dataset.paneLoaded === '1' || p.dataset.paneBusy === '1') { done && done(); return; }
    p.dataset.paneBusy = '1';
    fetch(src, { credentials: 'same-origin' })
      .then(function (r) { return r.text(); })
      .then(function (h) { p.innerHTML = h || ''; p.dataset.paneLoaded = '1'; injectScripts(p); })
      .catch(function () { p.innerHTML = '<p class="muted">加载失败, 刷新页面重试</p>'; })
      .then(function () { p.dataset.paneBusy = ''; done && done(); });
  }

  // 一个面板名可能对应多个元素 (控制台: 头/按钮行/折叠区都标 data-pane="sync")。
  // 所以显隐要按名字成组切换, 不是只切第一个命中的元素。
  function show(name, opts) {
    if (!panes.length) return false;
    var hit = findPane(name);
    if (!hit) return false;                       // 本页没有这个面板 → 交给调用方
    var group = panesOf(name);
    panes.forEach(function (p) {
      p.classList.toggle('is-on', p.dataset.pane === name);
    });
    document.querySelectorAll('[data-pane-link]').forEach(function (a) {
      if (a.dataset.paneLink === name) a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
    });
    if (!opts || !opts.silent) {
      var url = (opts && opts.url) || ('#' + name);
      try { history.replaceState(null, '', url); } catch (e) {}
    }
    if (!opts || !opts.noScroll) window.scrollTo({ top: 0, behavior: 'auto' });
    var left = group.length;
    var fire = function () {
      document.dispatchEvent(new CustomEvent('recto:pane', { detail: { pane: name, el: hit } }));
    };
    if (!left) { fire(); return true; }
    group.forEach(function (el) {
      loadPane(el, function () { left -= 1; if (left <= 0) fire(); });
    });
    return true;
  }

  // 链接指向另一个页面 (比如在 /admin/ 上点 /#sync) → 不该被本页 JS 截住
  function sameDoc(href) {
    try {
      var u = new URL(href, location.href);
      var norm = function (p) { return p.replace(/\/+$/, '') || '/'; };
      return u.origin === location.origin && norm(u.pathname) === norm(location.pathname);
    } catch (e) { return false; }
  }

  window.__recto = {
    showPane: show,
    loadPane: loadPane,
    injectScripts: injectScripts,
    paneEl: findPane,
    setPaneSrc: function (name, src) {
      var p = findPane(name);
      if (!p) return;
      p.dataset.paneSrc = src;
      p.dataset.paneLoaded = '0';
    }
  };

  document.addEventListener('click', function (ev) {
    if (ev.defaultPrevented) return;
    if (ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
    var a = ev.target.closest ? ev.target.closest('[data-pane-link]') : null;
    if (!a) return;
    var name = a.dataset.paneLink;
    var href = a.getAttribute('href') || '';
    // 跨页链接交给浏览器 (顶部导航在 /admin/ 上指向 /#sync 这类)
    if (href && !sameDoc(href)) return;
    // 本页没有这个面板 → 也交给浏览器, 别静默跳去第一个面板
    if (!findPane(name)) return;
    ev.preventDefault();
    show(name, { url: href || ('#' + name) });
  });
  window.addEventListener('hashchange', function () {
    show((location.hash || '').replace('#', ''), { silent: true });
  });

  // Material 水波纹 —— 纯装饰, 不改变点击行为, 500ms 后自删
  document.addEventListener('pointerdown', function (ev) {
    if (ev.button !== undefined && ev.button !== 0) return;
    var t = ev.target.closest
      ? ev.target.closest('button, .btn, .chip, .rc-nav a, .rc-subnav a, .bk') : null;
    if (!t || t.disabled) return;
    var cs = window.getComputedStyle(t);
    if (cs.position === 'static') t.style.position = 'relative';
    if (cs.overflow !== 'hidden') t.style.overflow = 'hidden';
    var r = t.getBoundingClientRect();
    var d = Math.max(r.width, r.height) || 40;
    var s = document.createElement('span');
    s.className = 'rc-ripple';
    s.style.width = s.style.height = d + 'px';
    s.style.left = (ev.clientX - r.left - d / 2) + 'px';
    s.style.top = (ev.clientY - r.top - d / 2) + 'px';
    t.appendChild(s);
    window.setTimeout(function () { s.remove(); }, 520);
  });

  if (panes.length) {
    var start = (main && main.dataset.paneStart) || '';
    // hash 优先: 从别的分区点「云端同步」会落到 /#sync, 那就该显示 sync;
    // 没有 hash 才用服务端给的默认面板 (pane_start), 再不行才回退第一个。
    var initial = (location.hash || '').replace('#', '') || start;
    if (!initial) {
      var cur = document.querySelector('.rc-nav a[aria-current="page"][data-pane-link]');
      initial = cur ? cur.dataset.paneLink : panes[0].dataset.pane;
    }
    if (!initial || !show(initial, { silent: true, noScroll: true })) {
      show(panes[0].dataset.pane, { silent: true, noScroll: true });
    }
  }
})();
"""


def document(*, title: str, body: str, active: str = "", kicker: str = "",
             sub: str = "", user: str | None = None, body_class: str = "",
             head_extra: str = "", show_nav: bool = True,
             show_subnav: bool = False, sub_active: str = "",
             pane_start: str = "", footer: bool = True) -> str:
    """整页 HTML。`body` 是 `.rc-main` 的内容 (不含外壳)。"""
    title_txt = f"{title} · {BRAND_FULL}" if title else BRAND_FULL
    nav = nav_html(active, user) if show_nav else ""
    subnav = subnav_html(sub_active) if show_subnav else ""
    start_attr = f' data-pane-start="{_e(pane_start)}"' if pane_start else ""
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<meta name="theme-color" content="{_e(_theme_color())}">
<title>{_e(title_txt)}</title>
<link rel="stylesheet" href="/assets/recto.css">
<link rel="stylesheet" href="/assets/skin.css">
{favicon_links()}
{head_extra}
</head><body class="{_e(body_class)}">
<div class="rc-shell">
  <div class="rc-top"><div class="rc-top-in">{brand()}{nav}</div></div>
  <main class="rc-main"{start_attr}>{subnav}{body}</main>
  {_footer() if footer else ''}
</div>
<script>{_SHELL_JS}</script>
</body></html>"""


def _footer() -> str:
    return f'<footer class="rc-foot">{BRAND_FULL}</footer>'


def _theme_color() -> str:
    try:
        _, tokens, _ = skins.resolve()
        return tokens.get("bg") or "#f4efe4"
    except Exception:  # noqa: BLE001 — 取不到就用中性色, 不该影响渲染
        return "#f4efe4"


def auth_page(*, title: str = "", body: str) -> str:
    """登录页: 同一套 token, 但不带导航 (还没有会话)。"""
    page_title = (BRAND_FULL if not title or title in (BRAND, BRAND_FULL)
                  else f"{title} · {BRAND_FULL}")
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="{_e(_theme_color())}">
<title>{_e(page_title)}</title>
<link rel="stylesheet" href="/assets/recto.css">
<link rel="stylesheet" href="/assets/skin.css">
{favicon_links()}
</head><body class="rc-auth">{body}</body></html>"""


def login_body(*, next_url: str = "/", err: str = "", user: str = "",
               ttl_days: int = 7, note: str = "") -> str:
    err_html = f'<div class="err">{_e(err)}</div>' if err else ""
    return f"""<form class="rc-auth-card" method="post" action="/login" autocomplete="on">
  <a class="rc-lockup" href="/" aria-label="{_e(BRAND_FULL)}">{logo_full()}</a>
  <header class="rc-head"><h1>登录</h1>
  <div class="rc-sub">{_e(note or '用阅读器宿主 (:5000) 的账号密码登录 —— 一套凭据, 全部分区通用。')}</div></header>
  <input type="hidden" name="next" value="{_e(next_url or '/')}">
  <label class="field"><span>用户名</span>
    <input name="username" value="{_e(user or '')}" autocomplete="username"
           autocapitalize="none" autocorrect="off" autofocus></label>
  <label class="field"><span>密码</span>
    <input name="password" type="password" autocomplete="current-password"></label>
  <button type="submit" class="primary">登录</button>
  {err_html}
  <div class="tip">会话 {ttl_days} 天内有效 · 所有分区共用这一次登录。</div>
</form>"""


# 静态资源白名单 (文件名 → MIME)。只有这里列出来的能从 /assets/ 取到。
_ASSET_TYPES = {
    "recto.css": "text/css; charset=utf-8",
    "reader.css": "text/css; charset=utf-8",
    "reader.js": "text/javascript; charset=utf-8",
    # 标志 (tools/make_logo.py 从 logo.png 生成)
    "logo-mark.png": "image/png",
    "logo-full.png": "image/png",
    "favicon.png": "image/png",
    "apple-touch-icon.png": "image/png",
}


def assets_css(name: str) -> tuple[bytes, str] | None:
    """静态资源 → (bytes, mime)。只允许白名单里的文件名。"""
    if name == "skin.css":                       # 每请求现生成 (皮肤可换)
        return skins.skin_css().encode("utf-8"), "text/css; charset=utf-8"
    ctype = _ASSET_TYPES.get(name)
    if not ctype:
        return None
    try:
        return (ASSET_DIR / name).read_bytes(), ctype
    except OSError:
        return None


def skin_payload() -> str:
    """给外观页的 JS 用: 当前皮肤 + 全部皮肤 (连 token 一起带上, 便于本地即时预览)。"""
    conf = skins.load_conf()
    sid, tokens, base = skins.resolve(conf)
    alls = skins.all_skins(conf)
    data = {
        "current": sid,
        "conf": {k: base.get(k) for k in
                 ("skin", "font_scale", "density", "radius_scale", "heading",
                  "custom_css")},
        "custom_skins": base.get("custom_skins") or {},
        "skins": {k: {"label": v["label"], "desc": v["desc"], "mode": v["mode"],
                      "swatches": skins.swatches(k, conf), "builtin": v["builtin"],
                      "base": v.get("base", k), "tokens": v["tokens"],
                      "extra_css": v.get("extra_css") or ""}
                  for k, v in alls.items()},
    }
    return _json.dumps(data, ensure_ascii=False)
