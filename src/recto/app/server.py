"""Recto 统一 Web 服务 —— 一个进程、一个端口 (:5015)。

Recto 独立服务的全部入口 (不依赖任何外部书目数据库):

    /                 书库 / 云端同步 / 阅读增强 / 语料 / 外观
    /api/*            控制台 API
    /read/*           内置阅读器
    /admin/*          摘录接入后台 (墨水屏设备账本)
    /corpus/*         语料服务 (反代外部只读服务, 同一套外壳与皮肤)

登录是 Recto 自己的账号口令 (见 [auth] 段)。

启动:
    python3 -m recto.app.server            # 前台
    python3 -m recto.cli serve             # 同上 (CLI 入口)
"""
from __future__ import annotations

import json
import threading
import traceback
import urllib.parse
from http.server import ThreadingHTTPServer

from recto.app import console, corpus, excerpts, reader, webauth
from recto.core import paths
from recto.core.config import get_config

_cfg = get_config()
HOST = str(_cfg.get("recto", "host", "0.0.0.0") or "0.0.0.0")
PORT = int(_cfg.get("recto", "port", 5015) or 5015)

# 摘录账本的数据目录 (设备推来的 deck + aggregate)
EXCERPTS_DATA_DIR = paths.PATHS.device_data_dir

def _admin_allowed(h) -> bool:
    """摘录后台的登录判定 (统一会话优先, 老密码兜底)。"""
    if excerpts._is_authed(h):
        return True
    # 两个都没配 = 本地开发态, 放行 (与老的 8081 行为一致: 没密码时锁死?
    # 老实现是锁死, 但合并后如果 webauth 也没开就没法登录, 所以这里显式放行)
    if not webauth.enabled() and not excerpts._get_password(h):
        return True
    return False


class Handler(console.Handler):
    """控制台的 Handler, 前面挂上阅读器与摘录两个分区的派发。"""

    server_version = "Recto/1.0"

    # ---- 摘录接入 ------------------------------------------------------
    def _serve_admin(self) -> bool:
        u = urllib.parse.urlparse(self.path)
        if not u.path.startswith("/admin"):
            return False
        if u.path == "/admin/login" and not excerpts._get_password(self):
            # 没配老密码 → 用统一登录表单, 不再单独一套
            nxt = "/admin/"
            self._redirect("/login?next=" + urllib.parse.quote(nxt))
            return True
        if not _admin_allowed(self):
            if u.path.startswith("/admin/api/"):
                self._json(401, {"error": "unauthorized", "login": "/login"})
            else:
                self._redirect("/login?next=" + urllib.parse.quote(self.path or "/admin/"))
            return True
        try:
            self._admin_ok = True   # 已由 _admin_allowed 判过, 别再让老密码拦一遍
            handled = excerpts.handle_admin(self, EXCERPTS_DATA_DIR, u.path, u.query)
        except Exception as e:  # noqa: BLE001 — 摘录后台故障不该带挂整个控制台
            traceback.print_exc()
            self._json(500, {"error": f"{type(e).__name__}: {e}"})
            return True
        if not handled:
            self._json(404, {"error": "not found"})
        return True

    # ---- 语料服务 (反代到独立应用, 但用同一套外壳) ----------------------
    def _serve_corpus(self) -> bool:
        u = urllib.parse.urlparse(self.path)
        if not (u.path == corpus.PREFIX or u.path.startswith(corpus.PREFIX + "/")):
            return False
        if not self._gate():
            return True
        return corpus.dispatch(self)

    # ---- 内置阅读器 (/read) --------------------------------------------
    def _serve_read(self) -> bool:
        path = urllib.parse.urlparse(self.path).path
        if not (path == "/read" or path.startswith("/read/")):
            return False
        if not self._gate():
            return True
        return reader.handle(self)

    # ---- 派发顺序: 内置阅读器 → 语料 → 摘录 → 控制台 -------------------
    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler 契约
        path = urllib.parse.urlparse(self.path).path
        if self._serve_read():
            return
        if path.startswith(corpus.PREFIX) and self._serve_corpus():
            return
        if path.startswith("/admin") and self._serve_admin():
            return
        super().do_GET()

    def do_POST(self):  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if self._serve_read():
            return
        if path.startswith(corpus.PREFIX) and self._serve_corpus():
            return
        if path.startswith("/admin") and self._serve_admin():
            return
        super().do_POST()

    def do_OPTIONS(self):  # noqa: N802
        super().do_OPTIONS()

    def log_message(self, fmt, *args):  # 保持安静: 控制台自己也写日志
        return


def make_server(host: str = HOST, port: int = PORT) -> ThreadingHTTPServer:
    ThreadingHTTPServer.allow_reuse_address = True
    return ThreadingHTTPServer((host, port), Handler)


def _warn_default_login() -> None:
    """首次部署默认口令是 root/root —— 启动时大声说一次, 别让人带着它上公网。"""
    try:
        from recto.app import webauth

        if not webauth.enabled():
            print("[recto] 登录已关闭 (auth.mode = none): 只在完全可信的本机上这么做", flush=True)
            return
        if webauth.password() == webauth.DEFAULT_PASSWORD:
            print(
                "[recto] ⚠ 正在使用默认账号 " + webauth.DEFAULT_USER + "/" + webauth.DEFAULT_PASSWORD
                + " —— 请立刻在 /settings 改口令 (或 python3 -m recto.cli passwd)", flush=True)
    except Exception:  # noqa: BLE001 — 提示失败不该挡启动
        pass


def _start_device_listener():
    """墨水屏设备接入口 (默认 :8080) —— 与主服务同进程, 协议与端口都沿用设备固件期望的样子。

    端口被占 / 无权限时不致命: 主服务照常起, 只打一行明确的话。
    """
    try:
        port = int(_cfg.get("device", "port", 8080) or 0)
    except Exception:  # noqa: BLE001
        port = 0
    if port <= 0:
        return None
    from recto.sources import device as device_mod

    data_dir = paths.PATHS.device_data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    try:
        srv = device_mod._make_server("0.0.0.0", port, data_dir)
    except OSError as e:  # 端口占用 / 权限
        print(f"[recto] 设备接入口 {port} 未能绑定 ({e}) —— 设备推送不可用, 其余功能正常",
              flush=True)
        return None
    threading.Thread(target=srv.serve_forever, name="device-intake", daemon=True).start()
    print(f"[recto] 设备接入口 http://0.0.0.0:{port}/api/sync  账本: {data_dir}", flush=True)
    return srv


def main() -> None:
    srv = make_server()
    print(f"recto on http://{HOST}:{PORT}  (library + cloud + reader + excerpts)", flush=True)
    _warn_default_login()
    dev_srv = _start_device_listener()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        if dev_srv is not None:
            try:
                dev_srv.shutdown()
            except Exception:  # noqa: BLE001
                pass
            dev_srv.server_close()


if __name__ == "__main__":
    main()
