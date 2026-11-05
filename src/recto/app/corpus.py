"""语料服务 (`/corpus/*`) —— 把独立的语料应用嵌进 Recto 的同一套外壳里。

为什么是反代而不是搬代码:
  语料服务是**外部只读消费**的独立应用 (Flask, 自己的数据目录与模型调用)。
  搬到 Recto 里会同时搬来一整套任务队列 / 模型配置, 收益低风险高。
  所以这里的做法是: **留应用, 换外壳** ——
    * 同源: `/corpus/*` 反代到上游, 不再另开端口跳转
    * 同壳: 注入 Recto 的顶栏 + 导航 (与其它分区一致)
    * 同皮: 注入 recto.css / skin.css, 并把上游自己的 `--bg/--panel/...`
      变量映射到我们的 token —— 上游一行 CSS 不用改, 换肤它能跟着变

只改写两类路径: `/static/` 与 `/api/`, 因为它们在上游是绝对路径。
其它 (下载、导出) 原样透传。
"""
from __future__ import annotations

import html as _html
import re
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """别自动跟 302 —— 上游的登录跳转要翻译成 Recto 的登录页, 而不是被吃掉。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None

from recto.core.config import get_config

PREFIX = "/corpus"
_DEFAULT_UPSTREAM = "http://127.0.0.1:5013"

# 上游自己的变量名 → Recto token
_BRIDGE_CSS = """
html:root{
  --bg:var(--rc-bg); --panel:var(--rc-surface); --ink:var(--rc-fg);
  --ink-dim:var(--rc-dim); --line:var(--rc-line); --accent:var(--rc-accent);
  --ok:var(--rc-ok); --err:var(--rc-err); --warn:var(--rc-warn);
  --radius:var(--rc-radius);
}
body{font-family:var(--rc-sans);font-size:var(--rc-fs);}
/* 直角: 上游 CSS 里的字面圆角统一交给 Recto 皮肤 token 控制 */
body :where(*, *::before, *::after){border-radius:var(--rc-radius,0px) !important;}
.masthead{display:none}
.wrap{max-width:var(--rc-measure);padding:24px var(--rc-pad) 56px}
@media (max-width:860px){.wrap{padding:18px 14px 48px}}

/* ── 卡片: 上游是「卡片描边 + 卡头浅灰底 + 卡身 16px」的三层结构,
   跟 Recto 其它分区的卡片是两套做法。这里合并成一层: 卡片只留高度阴影,
   卡头用一条极淡分隔线 (token 色, 深色皮肤下不会变成一块亮底), 内外边距统一到 --rc-pad。 */
.card{padding:0;border:0;overflow:hidden}
.card-head{
  padding:14px var(--rc-pad);
  background:transparent;
  border-bottom:1px solid var(--rc-outline-variant, var(--rc-line));
}
.card-body{padding:var(--rc-pad)}
.card-head h2{font-size:var(--rc-fs-sm, 13.5px);letter-spacing:.01em}
.card-head .hint{font-size:var(--rc-fs-xs, 11.5px);color:var(--rc-mute)}

/* ── 输入框: 上游是「白底 + 1px 灰描边」的方框, 一页几十个框贴在同样有描边的卡片上,
   线条互相打架。改成 Material 填充式 —— 无描边, 一层极淡底色跟页面区分,
   聚焦时底色加深 + 底部压一道主色指示线。无边框, 但落点仍在。 */
body.rc-corpus :where(input, select, textarea){
  border:0; box-shadow:none; outline:none;
  background:color-mix(in srgb, var(--rc-fg) 6%, var(--rc-surface));
  border-radius:var(--rc-radius-field, 0);
  min-height:40px; padding:0 12px;
  transition:background .15s ease, box-shadow .15s ease;
}
body.rc-corpus :where(input, select, textarea):hover{
  background:color-mix(in srgb, var(--rc-fg) 9%, var(--rc-surface));
}
body.rc-corpus :where(input, select, textarea):focus{
  background:color-mix(in srgb, var(--rc-fg) 11%, var(--rc-surface));
  box-shadow:inset 0 -2px 0 var(--rc-primary);
}
body.rc-corpus textarea{padding:10px 12px;min-height:92px;line-height:1.6}
/* 复选 / 单选 / 滑块不是文本框, 别让上面的底色糊上去 */
body.rc-corpus :where(input[type="checkbox"], input[type="radio"], input[type="range"]){
  background:transparent;box-shadow:none;min-height:0;padding:0;
}
/* 表格里的行内输入: 同一种填充, 只是更紧凑 (上游那几条带 table 前缀的
   padding/font-size 特异度更高, 这里显式压回来) */
body.rc-corpus :where(table.models, table.cats) :where(input, select){
  min-height:30px;padding:0 8px;font-size:var(--rc-fs-xs, 11.5px);
}

/* ── 对齐: 标签与输入框之间 6px, 网格行距略大于列距, 勾选项不占字段格 */
body.rc-corpus label{font-size:var(--rc-fs-xs, 11.5px);color:var(--rc-dim);line-height:1.5}
body.rc-corpus label > :where(input, select, textarea){margin-top:6px}
body.rc-corpus label.inline > :where(input, select){margin-top:0}
body.rc-corpus .grid{align-items:start;column-gap:16px;row-gap:16px}
body.rc-corpus .grid > label.inline-check{
  grid-column:1 / -1;
  align-self:start;
  min-height:0;
  padding:0;
}
/* 行内混排 (输入 + 按钮 + 状态): 高度对齐到 40, 状态文字跟按钮中线齐 */
body.rc-corpus .row{align-items:center;gap:10px;margin-top:16px}
body.rc-corpus .row > :where(input, select){min-height:40px}
body.rc-corpus .row > :where(.status, .path, code){margin:0;line-height:1.5}
body.rc-corpus label.inline{font-size:var(--rc-fs-xs, 11.5px);gap:8px}
body.rc-corpus label.inline input[type="checkbox"]{margin:0}

/* ── 提示条 / 徽标: 上游写死了浅色底 (#f7f9fc / #fff7e6 / #fff3d6),
   深色皮肤下会变成几块发光的白板。统一改成 token 混色。 */
body.rc-corpus .tip{
  margin:14px 0 0;padding:9px 12px;border-radius:0;
  font-size:var(--rc-fs-xs, 11.5px);line-height:1.65;color:var(--rc-dim);
  background:color-mix(in srgb, var(--rc-primary) 7%, var(--rc-surface));
  border-left:3px solid color-mix(in srgb, var(--rc-primary) 55%, transparent);
}
body.rc-corpus .tips{margin:12px 0 0;font-size:var(--rc-fs-xs, 11.5px);line-height:1.75}
body.rc-corpus .tips code,
body.rc-corpus .tip code{background:var(--rc-surface-2);padding:0 4px}
body.rc-corpus :where(.quota-bar, .analyze-warn),
body.rc-corpus .tip.warn{
  background:color-mix(in srgb, var(--rc-warn) 12%, var(--rc-surface));
  border:1px solid color-mix(in srgb, var(--rc-warn) 34%, transparent);
  color:var(--rc-warn);
}
body.rc-corpus .tip.warn{border-left:3px solid color-mix(in srgb, var(--rc-warn) 55%, transparent)}
body.rc-corpus .badge-warn{
  background:color-mix(in srgb, var(--rc-warn) 16%, var(--rc-surface));
  border:1px solid color-mix(in srgb, var(--rc-warn) 34%, transparent);
  color:var(--rc-warn);
}

/* 推理徽标不挤输入框 (2026-11-04 用户: 「badge-warn 会推挤输入框导致排布不整齐」)
   上游把 <span class="badge-warn">推理</span> 内联写在 input 后面, 而 input 是
   width:100% → 徽标必被挤到字段下面一行: 实测该行比相邻行高 10.4px (桌面) /
   27.2px (手机, 徽标自己还折成两行), 整张表行高参差。
   修法 = 「该列统一预留右侧空间 + 徽标绝对定位在预留区」:
   所有行输入框宽度仍然一致, 行高不再变化; 徽标不再受 inline 布局影响。
   (列序耦合: 模型列 = 上游 renderModels 的第 6 个 td。若列序变动, 这里要跟着改;
    另加一条 :has() 兜底, 万一对不上至少不会压住输入框。) */
body.rc-corpus table.models td:nth-child(6){position:relative;padding-right:40px}
body.rc-corpus table.models td:has(> .badge-warn):not(:nth-child(6)){position:relative;padding-right:40px}
body.rc-corpus table.models td > .badge-warn{
  position:absolute;right:6px;top:50%;transform:translateY(-50%);
  margin:0;padding:2px 4px;font-size:10px;line-height:1.25;vertical-align:baseline;
  white-space:nowrap;
}
body.rc-corpus .tag-latest{
  background:color-mix(in srgb, var(--rc-accent) 12%, var(--rc-surface));
  border:1px solid color-mix(in srgb, var(--rc-accent) 32%, transparent);
  color:var(--rc-accent);
}

/* 按钮群错落 / 主次配色的通用规则在 recto.css 的「4 按钮」一节
   (那里覆盖页头操作区、行、按钮行、筛选条)。这里只留上游特有的结构: */

/* 表格: 与 Recto 其它页面同一口径 —— 无任何边框线, 只靠隔行底色
   (banded rows) 区分行的区间。上游 CSS 里有 `border-bottom: #f0f2f4`,
   这里统一清掉; 选中行 (`tr.sel`) 用主色软底标识。 */
table, table.models, table.hist, table.cats { border-collapse: collapse; }
table th, table td { border: 0 !important; }
table.cats thead th, table.models thead th, table.hist thead th { background: transparent; }
table tbody tr:nth-child(even) > * { background: color-mix(in srgb, var(--rc-fg) 5%, transparent); }
table tbody tr:hover > * { background: color-mix(in srgb, var(--rc-fg) 9%, transparent); }
table tbody tr.sel { background: transparent !important; }
table tbody tr.sel > *,
table tbody tr.sel:hover > * { background: color-mix(in srgb, var(--rc-accent) 13%, transparent); }

/* 窄屏: 模型库列多 (10 列), 上游让表格 width:100% 自适应 → 390px 下每个输入框
   被压到十几像素 (连书名都只剩两个字宽), 既要读不了也谈不上对齐。
   给表格一个合理的最小宽度, 由 .models-wrap 横向滚动承接。 */
@media (max-width:860px){
  body.rc-corpus .models-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
  body.rc-corpus .models-wrap table.models{min-width:980px}
  body.rc-corpus .models-wrap table.models th:nth-child(6),
  body.rc-corpus .models-wrap table.models td:nth-child(6){min-width:150px}
}

/* 表格行内的小操作群 —— 模型库的「使用 / 测 / 删」和历史产物的
   「载入 / 导出 / 下载 / 设为最新 / 删除」。
   一排白底灰字会糊成一片, 分不出主次, 也看不出哪个是危险动作。
   按角色分四档 (全部从皮肤 token 混色, 换肤自动跟随):
     主操作 = 实心主色   ·  次要 = 主色软底   ·  辅助 = 虚线描边   ·  危险 = 透明红字 */
table.models .mdl-ops button,
table.hist td.acts button {
  font-size: var(--rc-fs-xs, 12px);
  font-weight: 600;
  letter-spacing: .01em;
  border: 1px solid transparent;
  transition: background .12s, border-color .12s, color .12s;
}
/* 主操作 */
table.hist td.acts button.act-load {
  background: var(--rc-accent);
  border-color: var(--rc-accent);
  color: var(--rc-accent-fg, #fff);
}
table.hist td.acts button.act-load:hover:not(:disabled) {
  background: color-mix(in srgb, var(--rc-accent) 86%, var(--rc-fg));
  border-color: transparent;
}
/* 次要操作 */
table.hist td.acts button.act-export,
table.models .mdl-ops button[data-act="use"] {
  background: var(--rc-accent-soft, color-mix(in srgb, var(--rc-accent) 14%, var(--rc-surface)));
  border-color: transparent;
  color: var(--rc-accent);
}
table.hist td.acts button.act-export:hover:not(:disabled),
table.models .mdl-ops button[data-act="use"]:hover:not(:disabled) {
  background: color-mix(in srgb, var(--rc-accent) 24%, var(--rc-surface));
}
/* 辅助操作 */
table.hist td.acts button.act-pin,
table.models .mdl-ops button[data-act="test"] {
  background: transparent;
  border-color: var(--rc-line-2, var(--rc-line));
  border-style: dashed;
  color: var(--rc-dim);
}
table.hist td.acts button.act-pin:hover:not(:disabled),
table.models .mdl-ops button[data-act="test"]:hover:not(:disabled) {
  border-style: solid;
  border-color: color-mix(in srgb, var(--rc-accent) 45%, transparent);
  background: var(--rc-surface-2);
  color: var(--rc-fg);
}
/* 危险操作 */
table.hist td.acts button.danger,
table.models .mdl-ops button.danger,
table.models .mdl-ops button[data-act="del"] {
  /* 极淡红底: 跟旁边「虚线描边」的辅助按钮区分开, 又不至于像实心危险键 */
  background: color-mix(in srgb, var(--rc-err) 7%, var(--rc-surface));
  border-color: color-mix(in srgb, var(--rc-err) 34%, transparent);
  color: var(--rc-err);
}
table.hist td.acts button.danger:hover:not(:disabled),
table.models .mdl-ops button.danger:hover:not(:disabled),
table.models .mdl-ops button[data-act="del"]:hover:not(:disabled) {
  background: color-mix(in srgb, var(--rc-err) 12%, var(--rc-surface));
  border-color: color-mix(in srgb, var(--rc-err) 60%, transparent);
}
"""


def upstream() -> str:
    try:
        return str(get_config().get("corpus", "ui_url") or _DEFAULT_UPSTREAM).rstrip("/")
    except Exception:  # noqa: BLE001
        return _DEFAULT_UPSTREAM


def _rewrite_html(html: str, title_hint: str = "语料", user: str | None = None) -> str:
    from recto.app.ui import shell

    body = html
    # 页面标题统一成其它分区的风格 (`<分区> · 云阅 Recto`), 不用上游自己的品牌名
    page_title = (shell.BRAND_FULL if not title_hint
                  else f"{title_hint} · {shell.BRAND_FULL}")
    body, n = re.subn(r"<title>.*?</title>",
                      f"<title>{_html.escape(page_title)}</title>",
                      body, count=1, flags=re.I | re.S)
    if not n and "</head>" in body:
        body = body.replace("</head>",
                            f"<title>{_html.escape(page_title)}</title></head>", 1)
    # 绝对路径 → 前缀路径 (含 JS 里拼出来的)
    body = body.replace('"/static/', f'"{PREFIX}/static/')
    body = body.replace("'/static/", f"'{PREFIX}/static/")
    body = body.replace('"/api/', f'"{PREFIX}/api/')
    body = body.replace("'/api/", f"'{PREFIX}/api/")
    # 上游自己的登录/登出交给 Recto 的会话
    body = body.replace('href="/logout"', 'href="/logout"')

    css = ('<link rel="stylesheet" href="/assets/recto.css">'
           '<link rel="stylesheet" href="/assets/skin.css">'
           + shell.favicon_links()
           + f"<style>{_BRIDGE_CSS}</style>")
    if "</head>" in body:
        body = body.replace("</head>", css + "</head>", 1)
    header = _shell_header(user)
    m = re.search(r"<body([^>]*)>", body, re.I)
    if m:
        attrs = m.group(1)
        cls = re.search(r'class\s*=\s*"([^"]*)"', attrs, re.I)
        if cls:
            new_attrs = attrs[:cls.start()] + f'class="{cls.group(1).strip()} rc-corpus"' + attrs[cls.end():]
        else:
            new_attrs = attrs + ' class="rc-corpus"'
        body = body[:m.start()] + f"<body{new_attrs}>" + header + body[m.end():]
    return body


def _shell_header(user: str | None = None) -> str:
    """复用 shell 的顶栏 —— 导航里的 active 由 key 决定, 这里固定高亮「语料」。"""
    from recto.app.ui import shell

    return ('<div class="rc-top"><div class="rc-top-in">'
            + shell.brand() + shell.nav_html("corpus", user) +
            "</div></div>")


def _rewrite_js(js: str) -> str:
    js = js.replace('"/api/', f'"{PREFIX}/api/')
    js = js.replace("'/api/", f"'{PREFIX}/api/")
    js = js.replace("`/api/", f"`{PREFIX}/api/")
    js = js.replace("location.href = '/login", "location.href = '/login")
    return js


def _upstream_path(path: str, query: str) -> str:
    rest = path[len(PREFIX):] or "/"
    if not rest.startswith("/"):
        rest = "/" + rest
    return upstream() + rest + (("?" + query) if query else "")


def dispatch(handler: BaseHTTPRequestHandler) -> bool:
    """处理 /corpus/* —— 返回 True 表示本请求已由本模块应答。"""
    u = urllib.parse.urlparse(handler.path)
    if not (u.path == PREFIX or u.path.startswith(PREFIX + "/")):
        return False

    target = _upstream_path(u.path, u.query)
    n = int(handler.headers.get("Content-Length") or 0)
    payload = handler.rfile.read(n) if n else None

    req = urllib.request.Request(target, data=payload, method=handler.command)
    for h in ("Content-Type", "Accept", "Accept-Language", "Range", "Cookie"):
        v = handler.headers.get(h)
        if v:
            req.add_header(h, v)
    req.add_header("X-Forwarded-Prefix", PREFIX)

    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=120) as r:
            raw = r.read()
            ctype = r.headers.get("Content-Type") or "application/octet-stream"
            extra = {k: r.headers.get(k) for k in
                     ("Content-Disposition", "Cache-Control") if r.headers.get(k)}
            status = r.status
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location") or ""
        if e.code in (301, 302, 303, 307, 308) and loc:
            return _redirect(handler, e.code, _map_location(loc))
        raw = e.read() or b""
        ctype = e.headers.get("Content-Type") or "text/plain; charset=utf-8"
        extra, status = {}, e.code
    except Exception as e:  # noqa: BLE001 — 上游没起/超时
        return _down(handler, e)

    lower = ctype.lower()
    if "text/html" in lower:
        raw = _rewrite_html(raw.decode("utf-8", "replace"),
                           user=getattr(handler, "_user", lambda: None)()
                           ).encode("utf-8")
        ctype = "text/html; charset=utf-8"
    elif "javascript" in lower:
        raw = _rewrite_js(raw.decode("utf-8", "replace")).encode("utf-8")

    handler.send_response(status)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "no-store")
    for k, v in extra.items():
        handler.send_header(k, v)
    handler.end_headers()
    if handler.command != "HEAD":
        handler.wfile.write(raw)
    return True


def _map_location(loc: str) -> str:
    """上游的跳转目标 → 本服务下的等价地址。

    上游的登录页就是我们的登录页 (同一个会话), 所以 /login 直接外抛;
    其它站内路径一律加 /corpus 前缀。
    """
    if loc.startswith("http://") or loc.startswith("https://"):
        return loc
    if loc.startswith("/login") or loc.startswith("/logout"):
        return loc
    return PREFIX + loc if loc.startswith("/") else PREFIX + "/" + loc


def _redirect(handler: BaseHTTPRequestHandler, code: int, loc: str) -> bool:
    handler.send_response(code)
    handler.send_header("Location", loc)
    handler.send_header("Content-Length", "0")
    handler.end_headers()
    return True


def _start_hint_html() -> str:
    """上游没起时给一句「怎么起」—— 由 config 的 [corpus] start_hint 提供。

    这里不写死任何本机路径: 部署的人知道自己怎么起的, 写在 config.toml.local 里。
    """
    hint = str(get_config().get("corpus", "start_hint") or "").strip()
    if not hint:
        return ""
    return ('<div class="meta" style="margin-top:10px">启动方式: '
            f"<code>{_html.escape(hint)}</code></div>")


def _down(handler: BaseHTTPRequestHandler, err: Exception) -> bool:
    """上游不可达时给一个同样外壳的说明页, 而不是裸 502。"""
    from recto.app.ui import shell

    body = (shell.head("语料服务", "上游没有应答",
                       "语料应用没在跑, 或者地址配错了。这里不是故障——"
                       "这一节按需启动即可。")
            + f'<div class="card"><div class="meta">上游: <code>{upstream()}</code>'
              f'</div><pre class="out">{type(err).__name__}: {err}</pre>'
            + _start_hint_html()
            + "</div>")
    html = shell.document(title="语料服务", body=body, active="corpus")
    data = html.encode("utf-8")
    handler.send_response(503)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)
    return True
