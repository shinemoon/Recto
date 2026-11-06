"""语料分区 (`/corpus/*`) —— 云阅 Recto 的原生能力 (EPUB 关键字精炼)。

它以前是独立进程 (:5013 Flask), 由本服务反代 + 注入外壳。现在整个服务在这里:

    业务逻辑   recto.corpus.service   (任务队列 / LLM / 解析 / 导出, 与本层无关)
    页面外壳   recto.app.ui.shell     (同一套顶栏 / 导航 / 皮肤 / 登录)
    HTTP 路由  本文件                 (薄薄一层, 只做路径与方法分派)

    GET  /corpus/                 页面
    GET  /corpus/static/*         页面资产 (corpus.css / corpus.js)
    *    /corpus/api/*            与上游一一对应的接口

登录由 Recto 统一处理 (`server.Handler._gate` 先过), 这里没有第二套账号。
"""
from __future__ import annotations

import re
import traceback
import urllib.parse
from pathlib import Path

from recto.app.ui import shell
from recto.corpus import service

PREFIX = "/corpus"

_UI_DIR = Path(__file__).resolve().parent / "ui" / "assets"

# 只放行这两份资产: 语料页的前端是构建产物, 不该暴露目录内容
_ASSETS = {
    "corpus.css": "text/css; charset=utf-8",
    "corpus.js": "text/javascript; charset=utf-8",
}

# book_id = sha1 前 16 位 hex; 模型 id 允许字母数字下划线连字符
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def page_html() -> str:
    """整页 HTML: 外壳 + 语料正文区块。"""
    frag = (_UI_DIR / "corpus.html").read_text("utf-8")
    return shell.document(
        title="语料",
        body=frag,
        active="corpus",
        body_class="rc-corpus-page",
        head_extra=(
            '<link rel="stylesheet" href="/corpus/static/corpus.css">\n'
            '<script src="/corpus/static/corpus.js" defer></script>'
        ),
    )


def _body(h) -> dict:
    """读 JSON 请求体 (没有体 / 空体 / 坏体 → {})。"""
    try:
        return h._read_json() or {}  # noqa: SLF001 — 与 console.Handler 同一套工具
    except Exception:  # noqa: BLE001
        return {}


# ------------------------------------------------------------------ 接口路由
def _api(h, path: str, query: str) -> tuple[int, dict]:
    """`/corpus/api/...` (已去掉前缀) → (状态码, JSON)。"""
    method = (h.command or "GET").upper()
    q = urllib.parse.parse_qs(query or "")

    if path == "/presets":
        return service.presets()

    if path == "/config":
        return service.set_config(_body(h)) if method == "POST" else service.get_config()

    if path == "/quota":
        return service.quota()
    if path == "/quota/reset":
        return service.quota_reset()
    if path == "/llm/test":
        return service.llm_test(_body(h))

    # 模型库
    if path == "/models":
        return service.models_set(_body(h)) if method == "POST" else service.models_get()
    if path == "/models/apply":
        return service.models_apply(_body(h))
    m = re.fullmatch(r"/models/([^/]+)/test", path)
    if m:
        if method != "POST":
            return 405, {"error": "method not allowed"}
        return service.models_test(urllib.parse.unquote(m.group(1)))
    m = re.fullmatch(r"/models/([^/]+)", path)
    if m:
        if method != "DELETE":
            return 405, {"error": "method not allowed"}
        return service.models_delete(urllib.parse.unquote(m.group(1)))

    # epub 库
    if path == "/epubs":
        return service.epubs((q.get("roots") or [""])[0])
    if path == "/epubs/roots":
        return service.roots_set(_body(h)) if method == "POST" else service.roots_get()

    if path == "/parse":
        return service.parse(_body(h))
    if path == "/books":
        return service.books()

    # 固定导出
    if path == "/exports":
        return service.exports()
    if path == "/export/sync":
        return service.export_sync()
    if path == "/export/latest":
        return service.export_latest(_body(h))
    m = re.fullmatch(r"/export/([^/]+)", path)
    if m:
        return service.export_book(urllib.parse.unquote(m.group(1)))

    # 单书
    m = re.fullmatch(r"/book/([^/]+)", path)
    if m:
        bid = urllib.parse.unquote(m.group(1))
        if not _ID_RE.match(bid):
            return 400, {"error": "book_id 非法"}
        return service.book_delete(bid) if method == "DELETE" else service.book_detail(bid)

    # 抽取
    if path == "/analyze":
        return service.analyze_book(_body(h))
    if path == "/analyze/clear":
        return service.analyze_clear(_body(h))

    if path == "/tasks":
        return service.tasks()
    m = re.fullmatch(r"/task/([^/]+)", path)
    if m:
        bid = urllib.parse.unquote(m.group(1))
        if not _ID_RE.match(bid):
            return 400, {"error": "book_id 非法"}
        return 200, service.task_snapshot(bid)

    m = re.fullmatch(r"/keywords/([^/]+)", path)
    if m:
        bid = urllib.parse.unquote(m.group(1))
        if not _ID_RE.match(bid):
            return 400, {"error": "book_id 非法"}
        return service.keywords(bid)

    return 404, {"error": "not found"}


# ------------------------------------------------------------------ 入口
def dispatch(h) -> bool:
    """把 `/corpus` 下的请求处理掉。返回 True = 已处理。"""
    u = urllib.parse.urlparse(h.path)
    path = u.path
    if not (path == PREFIX or path.startswith(PREFIX + "/")):
        return False
    rest = path[len(PREFIX):] or "/"

    # 页面
    if rest in ("/", ""):
        h._send(200, page_html().encode("utf-8"), "text/html; charset=utf-8")  # noqa: SLF001
        return True

    # 资产
    if rest.startswith("/static/"):
        name = rest[len("/static/"):]
        ctype = _ASSETS.get(name)
        if not ctype:
            h._send(404, b"not found", "text/plain; charset=utf-8")  # noqa: SLF001
            return True
        try:
            data = (_UI_DIR / name).read_bytes()
        except OSError:
            h._send(404, b"not found", "text/plain; charset=utf-8")  # noqa: SLF001
            return True
        h._send(200, data, ctype)  # noqa: SLF001
        return True

    # 接口
    if rest.startswith("/api/"):
        api_path = rest[len("/api"):]
        try:
            code, payload = _api(h, api_path, u.query)
        except Exception as e:  # noqa: BLE001 — 单个接口故障不许带挂整个服务
            service.log("接口异常 %s: %s\n%s" % (api_path, e, traceback.format_exc()[-1200:]),
                        "error")
            code, payload = 500, {"error": "internal_error", "detail": f"{type(e).__name__}: {e}"}
        h._json(code, payload)  # noqa: SLF001
        return True

    # 其它 (老书签 / 手误) → 回分区首页
    h._redirect(PREFIX + "/")  # noqa: SLF001
    return True
