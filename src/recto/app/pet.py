"""书宠 (陪读精灵) —— 云阅 Recto 的原生分区。

业务在 `recto.pet.config` (模型接入配置 / 人格 / 陪伴参数), 本层只做路径分派。

    GET  /pet/                 页面
    GET  /pet/static/*         页面资产 (pet.css / pet.js)
    *    /pet/api/*            接口

与语料分区同一套外壳 / 皮肤 / 登录 —— 登录由 Recto 统一处理。
"""
from __future__ import annotations

import re
import traceback
import urllib.parse
from pathlib import Path

from recto.app.ui import shell
from recto.pet import config as petcfg

PREFIX = "/pet"

_UI_DIR = Path(__file__).resolve().parent / "ui" / "assets"

# 只放行这两份资产
_ASSETS = {
    "pet.css": "text/css; charset=utf-8",
    "pet.js": "text/javascript; charset=utf-8",
}


def page_html() -> str:
    """整页 HTML: 外壳 + 书宠正文区块。"""
    frag = (_UI_DIR / "pet.html").read_text("utf-8")
    return shell.document(
        title="书宠",
        body=frag,
        active="pet",
        body_class="rc-pet-page",
        head_extra=(
            '<link rel="stylesheet" href="/pet/static/pet.css">\n'
            '<script src="/pet/static/pet.js" defer></script>'
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
    """`/pet/api/...` (已去掉前缀) → (状态码, JSON)。"""
    method = (h.command or "GET").upper()

    if path == "/presets":
        return petcfg.presets()

    if path == "/status":
        return petcfg.status()

    if path == "/config":
        if method == "POST":
            return petcfg.set_config(_body(h))
        return petcfg.get_config()

    if path == "/llm/test":
        return petcfg.llm_test(_body(h))

    # 模型库
    if path == "/models":
        if method == "POST":
            return petcfg.models_set(_body(h))
        return petcfg.models_get()
    if path == "/models/apply":
        return petcfg.models_apply(_body(h))
    m = re.fullmatch(r"/models/([^/]+)/test", path)
    if m:
        if method != "POST":
            return 405, {"error": "method not allowed"}
        return petcfg.models_test(urllib.parse.unquote(m.group(1)))
    m = re.fullmatch(r"/models/([^/]+)", path)
    if m:
        if method != "DELETE":
            return 405, {"error": "method not allowed"}
        return petcfg.models_delete(urllib.parse.unquote(m.group(1)))

    return 404, {"error": "not found"}


# ------------------------------------------------------------------ 入口
def dispatch(h) -> bool:
    """把 `/pet` 下的请求处理掉。返回 True = 已处理。"""
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
            code, payload = 500, {"error": "internal_error", "detail": f"{type(e).__name__}: {e}"}
        h._json(code, payload)  # noqa: SLF001
        return True

    # 其它 (老书签 / 手误) → 回分区首页
    h._redirect(PREFIX + "/")  # noqa: SLF001
    return True
