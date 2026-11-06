"""Recto 控制台 —— 书库 / 云端同步 / 阅读增强 / 摘录 / 语料 / 外观。

解耦后这里只调 Recto 自己的服务层:
    书库     reader_state (原书扫描 + 进度/书签/笔记/时长)
    云端     services.sync / notes / sessions + services.acquisition
    摘录     app.excerpts (只读账本 + 工具页)
    语料     app.corpus (内置: 解析 / 抽词 / 导出)

没有任何外部书目数据库的读写。
"""
from __future__ import annotations

import json
import re
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from recto.core import paths
from recto.core.config import get_config as _get_cfg
from recto.app import webauth

_cfg = _get_cfg()
STATE_DB = paths.STATE_DB
LOG_DIR = paths.LOG_DIR

_run_lock = threading.Lock()
_last_run: dict = {}

# 日志行形如 "2026-11-04 11:31:12,272 INFO 拉取成功 · …"
# 注意 asctime 里本身带空格 —— 按空格切会把等级切歪 (老 bug: 整个面板显示成"原文")。
_LOG_LINE_RE = re.compile(
    r"^(\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(?:[,.]\d{1,6})?)\s+"
    r"(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+(.*)$")


# ---- 只读状态 -------------------------------------------------------------

def _tail_log(n: int = 60) -> list[dict]:
    log = LOG_DIR / "sync.log"
    if not log.exists():
        return []
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-n:]:
        m = _LOG_LINE_RE.match(line)
        if m:
            out.append({"ts": m.group(1), "level": m.group(2),
                        "msg": m.group(3)})
        else:
            out.append({"ts": "", "level": "RAW", "msg": line})
    return out


def _intake_summary() -> dict:
    """摘录账本现状: 只读 aggregate / books / events, 不打网络。"""
    from recto.app import excerpts as _ex

    d = paths.PATHS.device_data_dir
    try:
        exs = _ex._load_excerpts(d)
        books = _ex._load_books(d)
        events = _ex._load_events(d, limit=1)
    except Exception as e:  # noqa: BLE001 — 账本残缺不该整页挂掉
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "data_dir": str(d)}
    return {
        "ok": True,
        "excerpts": len(exs),
        "books": len(books),
        "last_event": events[0] if events else None,
        "data_dir": str(d),
        "device_port": int(_cfg.get("device", "port", 8080) or 8080),
        "admin_path": "/admin/",
    }


def _corpus_state() -> dict:
    """语料服务的摘要 (给控制台 / 自检用)。

    语料现在是本服务的内置能力, 所以这里直接问 `recto.corpus.service`,
    不再探测外部端口 —— 也就不会有"服务没起就整块不可用"的情况。
    """
    from recto.corpus import service as corpus_service

    d = paths.PATHS.corpus_dir
    out = {"ok": True, "dir": str(d), "files": 0, "latest": None, "error": None}
    try:
        out.update(corpus_service.status_summary())
        out["files"] = int(out.get("exports") or 0)
        latest = d / "latest.json"
        if latest.exists():
            meta = (json.loads(latest.read_text(encoding="utf-8")) or {}).get("meta") or {}
            out["latest"] = {
                "title": meta.get("title"),
                "slug": meta.get("export_slug"),
                "terms": meta.get("total_terms"),
                "model": meta.get("model"),
                "at": meta.get("exported_at") or meta.get("generated_at"),
            }
    except Exception as e:  # noqa: BLE001 — 摘要失败不该影响控制台
        out["ok"] = False
        out["error"] = type(e).__name__ + ": " + str(e)
    return out


def _state() -> dict:
    from recto.services import reader_state, sync

    try:
        # 用 status() 而不是裸 links(): 裸 links() 只有映射身份, 没有进度与时间戳,
        # 前端表格就永远是空的 —— 「状态表不反映真实状态」的根因就在这。
        st = sync.status()
    except Exception as e:  # noqa: BLE001
        st = {"links": [], "link_count": 0, "error": f"{type(e).__name__}: {e}"}
    try:
        books = reader_state.list_books()
    except Exception:  # noqa: BLE001
        books = []
    return {"sync": st, "book_count": len(books), "books": books[:200],
            "tick": sync.tick_state(),
            "running": _run_lock.locked(), "last_run": dict(_last_run)}


# ---- 掌阅认证 (扫码) ------------------------------------------------------

_qr_lock = threading.Lock()
_qr_state: dict = {"code": None, "issued": None, "status": "idle", "svg": ""}


def _auth_state() -> dict:
    from recto.sources import cloud_auth

    out = dict(cloud_auth.auth_summary())
    out["qr"] = {k: v for k, v in _qr_state.items() if k != "svg"}
    return out


def _qr_issue() -> dict:
    from recto.app import qr as qrgen
    from recto.sources import cloud_auth

    global _qr_state
    with _qr_lock:
        code = cloud_auth.new_qr()
        payload = cloud_auth.qr_payload(code)
        _qr_state = {"code": code, "payload": payload,
                     "issued": time.strftime("%F %T"), "status": "waiting",
                     "svg": qrgen.to_svg(payload, scale=6, border=4)}
        return {"ok": True, "code": code, "payload": payload,
                "issued": _qr_state["issued"], "svg": _qr_state["svg"]}


def _qr_check() -> dict:
    from recto.sources import cloud_auth

    with _qr_lock:
        code = _qr_state.get("code")
        if not code:
            return {"status": _qr_state.get("status") or "idle"}
        r = cloud_auth.poll_once(code)
        if r["status"] == "ok":
            a = cloud_auth.save_auth(r["auth"])
            _qr_state.update({"status": "ok", "code": None})
            return {"status": "ok", "usr": a.get("usr"),
                    "summary": cloud_auth.auth_summary()}
        if r["status"] == "invalid":
            _qr_state.update({"status": "invalid", "code": None})
        return {"status": _qr_state["status"], "msg": r.get("msg", ""),
                "issued": _qr_state.get("issued")}


# ---- 同步动作 -------------------------------------------------------------

def _run_sync(action: str) -> dict:
    from recto.services import notes, sessions, sync

    t0 = time.time()
    if not _run_lock.acquire(blocking=False):
        return {"ok": False, "busy": True, "error": "已有同步在进行"}
    try:
        if action == "push":
            out = sync.push_once()
        elif action == "pull":
            # 进度 + 阅读时长 (sync.pull_once 内部带, 取 max 融合) + 划线/书签
            out = sync.pull_once(only_forward=True)
            try:
                out["notes"] = notes.pull()
            except Exception as e:  # noqa: BLE001
                out["notes_error"] = f"{type(e).__name__}: {e}"
        elif action == "notes":
            out = notes.push()
        elif action == "time":
            from datetime import date

            out = sessions.push(date.today())
        elif action == "links":
            out = sync.ensure_links()
        else:
            return {"ok": False, "error": f"未知动作: {action}"}
        out["ok"] = True
        out["ms"] = int((time.time() - t0) * 1000)
        _last_run.clear()
        _last_run.update({"action": action, "at": time.strftime("%F %T"), "ms": out["ms"]})
        return out
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        _run_lock.release()


# ---- HTTP -----------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "Recto/1.0"

    # --- 基础 ---
    def _user(self) -> str | None:
        return webauth.current_user(self.headers.get("Cookie"))

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8",
              cookie: str | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _redirect(self, location: str, cookie: str | None = None) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _gate(self) -> bool:
        """未登录拦截。返回 True = 放行。"""
        if not webauth.enabled():
            return True
        path = urllib.parse.urlparse(self.path).path
        if path in ("/login", "/logout", "/healthz") or path.startswith("/assets/"):
            return True
        if self._user():
            return True
        # 接口一律返 401 JSON: 前端 fetch 拿到 303 会当成"成功但没内容".
        # 判据是"路径里有 /api/ 段" —— 语料 (/corpus/api/) 与阅读器 (/read/api/)
        # 都挂在各自前缀下, 只匹配开头 /api/ 会漏掉它们。
        if self.command in ("POST", "PUT", "DELETE") or path.startswith("/api/") \
                or "/api/" in path:
            self._json(401, {"error": "unauthorized", "login": "/login"})
        else:
            self._redirect("/login?next=" + urllib.parse.quote(path or "/"))
        return False

    def _serve_asset(self, name: str) -> None:
        from recto.app.ui import shell as _shell

        hit = _shell.assets_css(name)
        if not hit:
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        data, ctype = hit
        self._send(200, data, ctype)

    def _login_form(self) -> None:
        ip = (self.client_address or ("",))[0]
        if self.command == "GET":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            nxt = (q.get("next") or ["/"])[0] or "/"
            err = (q.get("err") or [""])[0]
            self._send(200, webauth.login_page("", nxt, err).encode("utf-8"),
                       "text/html; charset=utf-8")
            return
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        form = urllib.parse.parse_qs(raw.decode("utf-8", "replace"))
        user = (form.get("username") or [""])[0].strip()
        password = (form.get("password") or [""])[0]
        nxt = (form.get("next") or ["/"])[0] or "/"
        if not nxt.startswith("/") or nxt.startswith("//"):
            nxt = "/"
        wait = webauth.rate_blocked(ip)
        if wait:
            body = webauth.login_page("", nxt, f"失败次数太多, {wait} 秒后再试", user)
            self._send(429, body.encode("utf-8"), "text/html; charset=utf-8")
            return
        ok, msg, _via = webauth.verify(user, password)
        if ok:
            webauth.rate_clear(ip)
            # cookie 里存规范登录名 (config 的 [auth] user), 不存用户敲的大小写/别名,
            # 否则以后改用户名会让旧会话变成"用户对不上"
            self._redirect(nxt, webauth.set_cookie(webauth.login_user() or user))
        else:
            webauth.rate_fail(ip)
            body = webauth.login_page("", nxt, msg, user)
            self._send(401, body.encode("utf-8"), "text/html; charset=utf-8")

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        raw = self.rfile.read(n)
        try:
            body = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    # --- 路由 ---
    def do_GET(self):  # noqa: N802
        u = urllib.parse.urlparse(self.path)
        path, qs = u.path, urllib.parse.parse_qs(u.query)
        if path.startswith("/assets/"):
            self._serve_asset(path.split("/assets/", 1)[1])
            return
        if path in ("/login", "/logout"):
            if path == "/logout":
                self._redirect("/login", webauth.clear_cookie())
            else:
                self._login_form()
            return
        if path == "/healthz":
            self._json(200, {"ok": True, "service": "recto", "ts": int(time.time())})
            return
        if not self._gate():
            return
        if path in ("/", "/index.html"):
            self._send(200, _page_html(self._user()).encode("utf-8"),
                       "text/html; charset=utf-8")
            return
        if path in ("/appearance", "/appearance/"):
            from recto.app import appearance as _ap

            self._send(200, _ap.page_html(self._user()).encode("utf-8"),
                       "text/html; charset=utf-8")
            return
        if path in ("/settings", "/settings/"):
            from recto.app import settings_page as _st

            self._send(200, _st.page_html(self._user()).encode("utf-8"),
                       "text/html; charset=utf-8")
            return
        if path == "/api/settings":
            from recto.app import settings_page as _st

            self._json(200, _st.summary())
            return
        if path == "/api/mark":
            from recto.app import settings_page as _st

            try:
                self._json(200, _st.mark_summary())
            except Exception as e:  # noqa: BLE001 — 语料读不出来不该让设置页 500
                self._json(503, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            return
        if path == "/api/appearance":
            from recto.app import appearance as _ap

            self._json(200, _ap.api_get())
            return
        if path == "/api/state":
            self._json(200, _state())
            return
        if path == "/api/sync/probe":
            from recto.services import sync

            try:
                self._json(200, sync.probe())
            except Exception as e:  # noqa: BLE001 — 掌阅不可达不该让面板 500
                self._json(502, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            return
        if path == "/api/log":
            self._json(200, {"lines": _tail_log(int((qs.get("n") or ["80"])[0]))})
            return
        if path == "/api/intake":
            self._json(200, _intake_summary())
            return
        if path == "/api/corpus":
            self._json(200, _corpus_state())
            return
        if path == "/api/auth":
            self._json(200, _auth_state())
            return
        if path == "/api/library":
            from recto.services import reader_state

            self._json(200, {"books": reader_state.list_books((qs.get("q") or [""])[0])})
            return
        if path == "/api/cloud/books":
            from recto.services import acquisition as cb

            try:
                shelf = cb.shelf_books()
                local = {str(Path(f["path"]).stem).lower(): f for f in cb.local_files()}
                for b in shelf:
                    hit = local.get(str(b.get("name") or "").lower())
                    b["local"] = bool(hit)
                    b["local_path"] = (hit or {}).get("path") if hit else None
                self._json(200, {"books": shelf, "dl_dir": cb.dl_dir()})
            except Exception as e:  # noqa: BLE001 — 掌阅不可达不该让面板 500
                self._json(502, {"error": f"{type(e).__name__}: {e}"})
            return
        if path == "/api/cloud/local":
            from recto.services import acquisition as cb

            self._json(200, {"files": cb.local_files(), "dl_dir": cb.dl_dir()})
            return
        if path == "/api/cloud/jobs":
            from recto.services import acquisition as cb

            self._json(200, {"jobs": cb.jobs_list()})
            return
        if path == "/api/cloud/job":
            from recto.services import acquisition as cb

            jid = (qs.get("id") or [""])[0]
            self._json(200, {"job": cb.job_status(jid)})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path == "/login":
            self._login_form()
            return
        if not self._gate():
            return
        body = self._read_json()
        if path == "/api/sync/run":
            self._json(200, _run_sync(str(body.get("action") or "pull")))
            return
        if path == "/api/cloud/download":
            from recto.services import acquisition as cb

            try:
                jid = cb.start_download(
                    str(body.get("key") or ""),
                    name=body.get("name") or None,
                    overwrite=bool(body.get("overwrite")),
                    target=body.get("target") or None,
                )
                self._json(200, {"ok": True, "job": cb.job_status(jid)})
            except Exception as e:  # noqa: BLE001
                self._json(400, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            return
        if path == "/api/cloud/delete":
            from recto.services import acquisition as cb

            try:
                self._json(200, cb.delete_cloud(list(body.get("keys") or [])))
            except Exception as e:  # noqa: BLE001
                self._json(400, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            return
        if path == "/api/auth/qr":
            self._json(200, _qr_issue())
            return
        if path == "/api/auth/check":
            self._json(200, _qr_check())
            return
        if path == "/api/appearance":
            from recto.app import appearance as _ap

            out = _ap.api_post(body)
            # 留痕: 谁在什么时候把外观改成了什么样 (探针顶掉用户选择时能一眼看出来)
            try:
                import sys as _sys

                _ua = (self.headers.get("User-Agent") or "")[:40]
                _sys.stderr.write(
                    f"[appearance] save skin={out['conf'].get('skin')} "
                    f"from={self.client_address[0]} ua={_ua}\n")
                _sys.stderr.flush()
            except Exception:  # noqa: BLE001 — 日志失败不该影响保存
                pass
            self._json(200, out)
            return
        if path == "/api/settings":
            from recto.app import settings_page as _st

            try:
                self._json(200, _st.api_post(body))
            except Exception as e:  # noqa: BLE001 — 校验失败按 400 回, 别 500
                self._json(400, {"ok": False, "error": str(e)})
            return
        if path == "/api/mark":
            from recto.app import settings_page as _st

            try:
                self._json(200, _st.mark_post(body))
            except Exception as e:  # noqa: BLE001 — 校验失败按 400 回
                self._json(400, {"ok": False, "error": str(e)})
            return
        if path == "/api/settings/password":
            from recto.app import settings_page as _st

            try:
                self._json(200, _st.api_password(body, self._user()))
            except Exception as e:  # noqa: BLE001
                self._json(400, {"ok": False, "error": str(e)})
            return
        self._json(404, {"error": "not found"})

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt, *args):  # 安静: 自己有日志
        return


def make_server(host: str = "0.0.0.0", port: int = 5015) -> ThreadingHTTPServer:
    ThreadingHTTPServer.allow_reuse_address = True
    return ThreadingHTTPServer((host, port), Handler)


# ---- 页面 -----------------------------------------------------------------

_JS = r"""
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// 百分比精度按书的大小自适应: 一个刻度 ≈ 2KB 正文（一屏）。
// 371KB 的书 0.1% = 371 字节, 1 位小数就够; 40MB 的长篇 0.1% = 41KB
// （一万多字）, 要 3 位小数才看得见它在动。向下取整: 不把没读到的地方说成读过。
const pctDec = (bytes) => {
  const t = Number(bytes) || 0;
  if (t <= 0) return 2;
  return Math.max(1, Math.min(3, Math.ceil(Math.log(t / 2048) / Math.LN10 - 2)));
};
const pct = (v, bytes) => {
  if (v == null) return "—";
  const d = pctDec(bytes), sc = Math.pow(10, d);
  const x = Math.floor(Math.max(0, Math.min(1, Number(v) || 0)) * 100 * sc + 1e-9) / sc;
  let t = x.toFixed(d);
  if (t.indexOf(".") >= 0) t = t.replace(/0+$/, "").replace(/\.$/, "");
  return t + "%";
};
const mb = (n) => (n == null ? "—" : (n / 1048576).toFixed(1) + " MB");
const ts = (v) => {
  if (!v) return "—";
  const d = new Date(v);
  if (isNaN(d.getTime())) return String(v);
  const p = (n) => String(n).padStart(2, "0");
  return p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
};

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (r.status === 401) { location.href = "/login?next=" + encodeURIComponent(location.pathname); return {}; }
  try { return await r.json(); } catch (e) { return { error: "HTTP " + r.status }; }
}

function setPill(id, text, ok) {
  const el = $(id); if (!el) return;
  el.textContent = text; el.className = "pill" + (ok === true ? " ok" : ok === false ? " err" : "");
}

let LIB = [];

/* 书架排列顺序 —— recent = 最近阅读（有进度用进度时间，没读过退到入库时间）,
 * title = 按书名（中文走拼音序，数字按数值）。选择逐设备记在 localStorage:
 * 桌面习惯按书名找书, 手机习惯接着上次读, 不该互相覆盖。 */
const SORT_KEY = "recto.shelf.sort";
const SORTS = ["recent", "title"];
let SORT = (() => {
  try {
    const v = localStorage.getItem(SORT_KEY);
    return SORTS.indexOf(v) >= 0 ? v : "recent";
  } catch (e) { return "recent"; }
})();

const collator = (() => {
  try { return new Intl.Collator("zh-Hans-CN", { numeric: true, sensitivity: "base" }); }
  catch (e) { return null; }
})();

function cmpText(a, b) {
  const x = String(a == null ? "" : a), y = String(b == null ? "" : b);
  return collator ? collator.compare(x, y) : (x < y ? -1 : x > y ? 1 : 0);
}

/* 「最近阅读」的时间: 有阅读进度就用进度时间, 没读过退到入库时间。
 * seen_at 是"上次扫描看到它"的时间 —— 全库同一刻, 当排序依据没有意义。 */
function lastReadAt(b) { return String(b.updated_at || b.added_at || b.seen_at || ""); }

function sortShelf(list) {
  const arr = list.slice();
  if (SORT === "title") {
    arr.sort((a, b) => cmpText(a.title, b.title) || cmpText(a.author, b.author));
  } else {
    /* 同一刻按书名兜底, 保证顺序稳定（否则每次刷新卡片会跳位） */
    arr.sort((a, b) => {
      const x = lastReadAt(a), y = lastReadAt(b);
      return (y > x ? 1 : y < x ? -1 : 0) || cmpText(a.title, b.title);
    });
  }
  return arr;
}

function setShelfSort(v, save) {
  SORT = SORTS.indexOf(v) >= 0 ? v : "recent";
  if (save) { try { localStorage.setItem(SORT_KEY, SORT); } catch (e) {} }
  document.querySelectorAll("[data-sort]").forEach((btn) => {
    const on = btn.getAttribute("data-sort") === SORT;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  });
}

const fmtMin = (m) => {
  const v = Math.round(Number(m) || 0);
  if (!v) return "—";
  if (v < 60) return v + " 分钟";
  const h = v / 60;
  return (h >= 10 ? Math.round(h) : Math.round(h * 10) / 10) + " 小时";
};

function bookCard(b) {
  const pctv = Math.max(0, Math.min(1, Number(b.percent) || 0));
  const badge = pctv >= 0.999 ? '<span class="bk-badge done">读完</span>'
    : (pctv > 0 ? '<span class="bk-badge reading">在读</span>'
      : '<span class="bk-badge">未读</span>');
  const key = encodeURIComponent(b.key);
  const cover = b.cover
    ? `<img loading="lazy" alt="" src="/read/api/book/${key}/asset?p=${encodeURIComponent(b.cover)}">`
    : `<span class="bk-ph">${esc(String(b.title || "?").slice(0, 1))}</span>`;
  return `<a class="bk" href="/read/b/${key}" title="${esc(b.title)}">
    <div class="bk-cover">${badge}${cover}</div>
    <div class="bk-body">
      <div class="bk-title">${esc(b.title)}</div>
      <div class="bk-author">${esc(b.author || "佚名")}</div>
      <div class="bk-prog"><i style="width:${(pctv * 100).toFixed(1)}%"></i></div>
      <div class="bk-meta"><span class="p">${pct(b.percent, b.bytes)}</span>
        <span>${b.notes || 0} 划线 · ${fmtMin(b.minutes)}</span></div>
    </div></a>`;
}

function renderShelf() {
  const el = $("shelf"); if (!el) return;
  /* 这页的 <script> 挂在 <head> 里, 首屏执行时 body 还没解析完 —— 开关的选中态
   * 必须在"确实拿到 DOM"之后再刷一次, 否则只留 HTML 里的初始值。 */
  setShelfSort(SORT, false);
  const q = (($("shelf-q") && $("shelf-q").value) || "").trim().toLowerCase();
  const list = q ? LIB.filter((b) => ((b.title || "") + " " + (b.author || ""))
    .toLowerCase().indexOf(q) >= 0) : LIB;
  const sorted = sortShelf(list);
  el.innerHTML = sorted.length ? sorted.map(bookCard).join("")
    : `<div class="empty" style="grid-column:1/-1">${q
      ? "没有匹配的书" : "书库还是空的 —— 去「云端同步」下载掌阅云书，或把 epub / txt 放进书库目录"}</div>`;
}

function renderShelfStats() {
  const el = $("shelf-stat"); if (!el) return;
  const reading = LIB.filter((b) => b.percent > 0 && b.percent < 0.999).length;
  const done = LIB.filter((b) => b.percent >= 0.999).length;
  const mins = LIB.reduce((a, b) => a + (Number(b.minutes) || 0), 0);
  const notes = LIB.reduce((a, b) => a + (Number(b.notes) || 0), 0);
  const marks = LIB.reduce((a, b) => a + (Number(b.marks) || 0), 0);
  el.innerHTML = [["本", LIB.length], ["在读", reading], ["读完", done],
    ["累计时长", fmtMin(mins)], ["划线", notes], ["书签", marks]]
    .map(([l, v]) => `<div class="s"><b>${v}</b><span>${l}</span></div>`).join("");
}

let PROBE = {at: null, books: {}};

function drift(localP, cloudP, bytes) {
  if (localP == null || cloudP == null) return "";
  const d = localP - cloudP;
  // 容差 = 2KB 正文（一屏）。固定 0.2% 在长篇上等于 84KB, 会把真差异说成"对齐"。
  const tol = Number(bytes) > 0 ? Math.max(2048 / Number(bytes), 1e-6) : 0.002;
  if (Math.abs(d) < tol) return '<span class="pill ok">对齐</span>';
  return d > 0 ? '<span class="pill">本地领先</span>' : '<span class="pill err">云端领先</span>';
}

function renderLinks(sync) {
  const el = $("link-body"); if (!el) return;
  const lk = (sync && sync.links) || [];
  el.innerHTML = lk.length ? lk.map((l) => {
    const live = PROBE.at && l.key && Object.prototype.hasOwnProperty.call(PROBE.books, l.key);
    const cloudP = live ? PROBE.books[l.key].cloud_percent : l.cloud_percent;
    const cloudCell = cloudP == null
      ? '<span class="meta">—</span>'
      : pct(cloudP, l.bytes) + (live ? "" : ' <span class="meta">上次已知</span>')
        + " " + drift(l.local_percent, cloudP, l.bytes);
    const sub = [l.author, (l.cloud && l.cloud !== l.book) ? l.cloud : "", l.id32]
      .filter(Boolean).join(" · ");
    const flag = l.missing ? ' <span class="pill err">文件缺失</span>' : "";
    return `<tr>
      <td class="cell-book">${esc(l.book)}${flag}<div class="meta">${esc(sub)}</div></td>
      <td class="num nowrap">${pct(l.local_percent, l.bytes)}</td>
      <td class="num nowrap">${cloudCell}</td>
      <td class="nowrap">${esc(ts(l.last_push_at))}</td>
      <td class="nowrap">${esc(ts(l.last_pull_at))}</td>
    </tr>`;
  }).join("") : `<tr><td colspan="5" class="empty">还没有云端映射 —— 下载云书会自动建立，或点「对齐映射」</td></tr>`;
  $("link-cnt").textContent = lk.length;
  const miss = lk.filter((l) => l.missing).length;
  $("link-note").innerHTML = (PROBE.at
    ? "云端为刚校准的实时值 · " + esc(ts(PROBE.at))
    : "本地 = 阅读器进度；云端 = 最近一次与云端打过交道的值，点「校准云端」现查")
    + (miss ? ` · <span class="pill err">${miss} 本找不到文件</span>` : "");
}

async function loadState() {
  const s = await api("/api/state");
  LIB = s.books || [];
  setPill("lib-pill", LIB.length + " 本", true);
  setPill("link-pill", (s.sync && s.sync.link_count || 0) + " 本", true);
  renderShelf(); renderShelfStats();
  renderLinks(s.sync);
  renderTick(s.tick);
}

function renderTick(tk) {
  const el = $("tick-note");
  if (!el) return;
  tk = tk || {};
  if (!tk.enabled) {
    el.textContent = "定时同步：已关闭（设置页可打开 —— 停在一页上看很久时也会按时同步）";
    return;
  }
  const bits = ["定时同步：每 " + (tk.interval_sec || 0) + " 秒",
    "只看最近 " + (tk.active_min || 0) + " 分钟内读过的书"];
  if (!tk.running) bits.push("心跳未运行");
  if (tk.last_at) {
    bits.push("上次 " + ts(tk.last_at));
    const act = tk.last_actions || [];
    if (act.length) {
      const name = {progress: "进度", notes: "划线", time: "时长"};
      bits.push("同步了 " + act.map((a) => name[a] || a).join(" / "));
    } else if ((tk.last_keys || []).length) bits.push("无变化");
    else bits.push("最近没在读");
  } else bits.push("还没跑过");
  el.textContent = bits.join(" · ");
}

async function probeCloud() {
  const btn = $("link-refresh");
  if (btn) { btn.disabled = true; btn.textContent = "查询中…"; }
  const r = await api("/api/sync/probe");
  if (btn) { btn.disabled = false; btn.textContent = "校准云端"; }
  if (r && r.ok) {
    PROBE = {at: r.at, books: r.books || {}};
    await loadState();
    toast("云端进度已校准");
  } else {
    toast("校准失败：" + ((r && r.error) || "未知错误"), true);
  }
}

async function loadCloud(refresh) {
  const s = await api("/api/cloud/books");
  if (s.error) { setPill("cloud-pill", "云端不可达", false); $("cloud-body").innerHTML = `<tr><td colspan="4" class="empty">${esc(s.error)}</td></tr>`; return; }
  const books = s.books || [];
  setPill("cloud-pill", books.length + " 本", true);
  $("dl-dir").textContent = s.dl_dir || "";
  $("cloud-body").innerHTML = books.length ? books.map((b) => `<tr>
      <td class="cell-book">${esc(b.name)}<div class="meta">${esc(b.author || "")}</div></td>
      <td class="num nowrap">${mb(b.size)}</td>
      <td class="nowrap">${b.local ? '<span class="pill ok">已下载</span>' : '<span class="pill">未下载</span>'}</td>
      <td class="nowrap"><button class="small" data-key="${esc(b.key)}" data-name="${esc(b.name)}">${b.local ? "覆盖下载" : "下载"}</button></td>
    </tr>`).join("") : `<tr><td colspan="4" class="empty">云端书架是空的</td></tr>`;
  $("cloud-body").querySelectorAll("button[data-key]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      btn.disabled = true; btn.textContent = "下载中…";
      const r = await api("/api/cloud/download", {method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({key: btn.dataset.key, name: btn.dataset.name, overwrite: btn.textContent === "覆盖下载"})});
      if (!r.ok) { btn.textContent = "失败"; btn.disabled = false;
        outHtml(rsCard({act: "下载云书", state: "无法开始", bad: true,
          body: `<ul class="rs-list"><li><span class="pill err">原因</span> ${esc(r.error || "未知错误")}</li></ul>`}));
        return; }
      pollJob(r.job && r.job.id || "");
    });
  });
}

async function pollJob(jid) {
  if (!jid) return;
  for (let i = 0; i < 600; i++) {
    const s = await api("/api/cloud/job?id=" + encodeURIComponent(jid));
    const j = s.job || {};
    if (j.state === "done" || j.state === "error") {
      outHtml(renderJob(j));
      loadCloud(); loadState();
      return;
    }
    if (i === 0) outHtml(renderJob(j));
    await new Promise((r) => setTimeout(r, 1500));
  }
}

// ---- 人话渲染: 执行结果 ----------------------------------------------------
// 后端的返回值是给程序看的 (pct / why / skipped…) —— 这里翻成读者能读的句子:
// 一句话结论 + 几个计数 + 每本书一行状态, 原始 JSON 收进折叠区备查。

const ACT_LABEL = {pull: "从掌阅拉取", push: "推到掌阅", links: "对齐映射",
                   notes: "推划线/书签", time: "推今天时长"};

const WHY = {
  "noop": "进度一致，不用推",
  "forward": "往前推进",
  "meta": "只补齐书籍信息",
  "finish": "记为读完",
  "no-baseline": "首次推送",
  "cloud-already-done": "云端已读完，不往回拨",
  "rewind": "按本地覆写云端",
  "cloud-empty": "云端还没有这本书的进度",
  "local-ahead": "本地更新，不用拉",
  "no-progress": "还没开始读",
  "throttled": "刚同步过，稍后再试",
  "no-cloud-link": "没有云端映射",
  "auto-off": "自动同步已关闭",
  "no-key": "取不到云端记录"
};

function whyText(w) {
  if (!w) return "";
  let m = /^regress\(([-\d.eE+]+)<([-\d.eE+]+)\)$/.exec(String(w));
  if (m) return "云端更靠前（" + pct(+m[1]) + " > " + pct(+m[2]) + "），不回拨";
  m = /^rewind\(([-\d.eE+]+)<([-\d.eE+]+)\)$/.exec(String(w));
  if (m) return "按本地覆写云端（" + pct(+m[1]) + " < " + pct(+m[2]) + "）";
  m = /^cloud-newer\(([-\d.eE+]+)<([-\d.eE+]+)\)$/.exec(String(w));
  if (m) return "云端刚读过（" + pct(+m[2]) + "），这次先不覆盖";
  return WHY[w] || String(w);
}

const secs = (ms) => (ms == null ? "" : ms < 1000 ? ms + " 毫秒" : (ms / 1000).toFixed(1) + " 秒");
const chapterNo = (n) => (n == null || n === "" ? "" : "第 " + (Number(n) + 1) + " 章");

function rsChips(pairs) {
  const s = pairs.filter((p) => p && p[1] != null && p[1] !== "")
    .map(([l, v, k]) => `<span class="chip${k ? " " + k : ""}">${esc(l)} <b>${esc(v)}</b></span>`)
    .join("");
  return s ? `<div class="rs-chips">${s}</div>` : "";
}

function rsRows(items, fn) {
  if (!items || !items.length) return "";
  return `<ul class="rs-list">${items.map((it) => `<li>${fn(it)}</li>`).join("")}</ul>`;
}

function rsNote(note) {
  return note ? `<span class="rs-note">${esc(note)}</span>` : "";
}

let LASTCARD = null;

function rsCard(o) {
  const cls = o.bad ? "err" : (o.warn ? "" : "ok");
  LASTCARD = {act: o.act || "", state: o.state || "", cls: cls};
  return `<div class="rs">
    <div class="rs-head"><span class="rs-act">${esc(o.act || "")}</span>
      <span class="pill ${cls}">${esc(o.state || "")}</span>
      <span class="rs-meta">${esc(o.meta || "")}</span></div>
    ${o.chips || ""}${o.body || ""}
    ${o.raw === undefined ? "" : `<details class="rs-raw"><summary>原始数据</summary>
      <pre>${esc(typeof o.raw === "string" ? o.raw : JSON.stringify(o.raw, null, 1))}</pre></details>`}
  </div>`;
}

function outHtml(html) {
  const el = $("out"); if (!el) return;
  el.innerHTML = html;
  // 折叠时也能一眼看到"上次做了什么、成不成" —— 不用展开
  const pill = $("out-pill");
  if (pill && LASTCARD) {
    pill.hidden = !LASTCARD.state;
    pill.className = "pill " + LASTCARD.cls;
    pill.textContent = (LASTCARD.act || "") + (LASTCARD.state ? " · " + LASTCARD.state : "");
  }
  const sec = $("sec-out"); if (sec) sec.open = true;
}
function out(text) { outHtml(`<p class="meta">${esc(text)}</p>`); }

// 每本书一行的通用渲染: 书名 + 一句状态
function detailRows(items, one) {
  return rsRows(items, (d) => `<b>${esc(d.book || d.title || "—")}</b>${rsNote(one(d))}`);
}

function notesBody(n) {
  const body = detailRows(n.books, (b) => [
    b.notes != null ? "划线 " + b.notes : "",
    b.marks ? "书签 " + b.marks : "",
    b.del_notes ? "删划线 " + b.del_notes : "",
    b.deleted ? "删除 " + b.deleted : "",
    b.dry ? "试运行" : ""
  ].filter(Boolean).join(" · "));
  const errs = (n.errors || []).map((e) => `<li><span class="pill err">失败</span> ${esc(String(e))}</li>`).join("");
  return body + (errs ? `<ul class="rs-list">${errs}</ul>` : "");
}

function timeBody(t) {
  return detailRows(t.detail, (d) => {
    if (d.minutes != null) return fmtMin(d.minutes);
    if (d.days != null) {
      return d.days + " 天" + (d.changed ? " · 更新 " + d.changed + " 天" : " · 无变化");
    }
    return "";
  });
}

function pullBody(r) {
  return detailRows(r.detail, (d) => {
    if (d.updated) return "云端 " + pct(d.pct, d.bytes) + " → " + chapterNo(d.chapter)
      + (d.basis === "position" ? "（按云端精确位置）" : "");
    if (d.dry) return "试运行：将写到 " + pct(d.pct, d.bytes) + " · " + chapterNo(d.chapter);
    const extra = (d.cloud != null && d.local != null)
      ? "（云端 " + pct(d.cloud, d.bytes) + " · 本地 " + pct(d.local, d.bytes) + "）" : "";
    return whyText(d.skipped) + extra;
  });
}

function pushBody(r) {
  return detailRows(r.detail, (d) => {
    if (d.error) return "失败：" + d.error;
    if (d.skipped) {
      const extra = (d.pct != null && d.cloud != null)
        ? "（本地 " + pct(d.pct, d.bytes) + " · 云端 " + pct(d.cloud, d.bytes) + "）" : "";
      return whyText(d.skipped) + extra;
    }
    if (d.dry) return "试运行：" + pct(d.pct, d.bytes) + " · " + chapterNo(d.chapter) + " · " + whyText(d.why);
    return "已写入 " + pct(d.pct, d.bytes) + " · " + chapterNo(d.chapter) +
      (d.total ? "（共 " + d.total + " 章）" : "") + (d.why && d.why !== "forward" ? " · " + whyText(d.why) : "");
  });
}

function renderRun(action, r) {
  const act = ACT_LABEL[action] || action;
  const meta = [secs(r && r.ms), r && r.at ? "· " + ts(r.at) : ""].filter(Boolean).join(" ");
  if (!r) return rsCard({act: act, state: "没有返回", bad: true, meta: meta});
  if (r.busy) return rsCard({act: act, state: "已有同步在进行", warn: true,
                             body: `<p class="meta">等这一次跑完再点，或稍后重试。</p>`});
  if (r.error) return rsCard({act: act, state: "失败", bad: true, meta: meta,
                              body: `<ul class="rs-list"><li><span class="pill err">原因</span> ${esc(r.error)}</li></ul>`,
                              raw: r});

  if (action === "pull") {
    const chips = rsChips([["更新", r.updated ?? 0, "ok"], ["跳过", r.skipped ?? 0]]);
    let body = pullBody(r);
    if (r.notes) {
      body += `<div class="rs-sub"><div class="rs-sub-h">划线 / 书签</div>` +
        rsChips([["新增", r.notes.added ?? 0, (r.notes.added ? "ok" : "")],
                 ["更新", r.notes.updated ?? 0], ["删除", r.notes.deleted ?? 0],
                 ["未变", r.notes.unchanged ?? 0]]) + notesBody(r.notes) + `</div>`;
    }
    if (r.time) {
      body += `<div class="rs-sub"><div class="rs-sub-h">阅读时长</div>` +
        rsChips([["更新", (r.time.changed ?? 0) + " 天", (r.time.changed ? "ok" : "")],
                 ["云端有", (r.time.days ?? 0) + " 天"],
                 ["合并", r.time.merged ?? 0]]) +
        timeBody(r.time) + `</div>`;
    }
    if (r.time_error) body += `<p class="meta">阅读时长没拉到：${esc(r.time_error)}（进度已照常更新）</p>`;
    const nothing = !r.updated && !(r.notes && (r.notes.added || r.notes.updated || r.notes.deleted));
    return rsCard({act: act, state: r.updated ? "已更新 " + r.updated + " 本"
      : (nothing ? "已是最新" : "完成"), warn: !r.updated, meta: meta,
      chips: chips, body: body, raw: r});
  }

  if (action === "push") {
    const bad = (r.failed || 0) > 0;
    return rsCard({act: act, state: bad ? r.failed + " 本失败" : (r.pushed ? "已推 " + r.pushed + " 本" : "无需推送"),
      bad: bad, warn: !bad && !r.pushed, meta: meta,
      chips: rsChips([["推送", r.pushed ?? 0, r.pushed ? "ok" : ""],
                      ["跳过", r.skipped ?? 0], ["失败", r.failed ?? 0, bad ? "err" : ""]]),
      body: pushBody(r), raw: r});
  }

  if (action === "notes") {
    const bad = (r.errors || []).length > 0;
    return rsCard({act: act, state: bad ? "有失败" : ((r.pushed || r.pushed_marks || r.deleted) ? "已同步" : "无需同步"),
      bad: bad, warn: !bad && !(r.pushed || r.pushed_marks || r.deleted), meta: meta,
      chips: rsChips([["划线", r.pushed ?? 0, r.pushed ? "ok" : ""],
                      ["书签", r.pushed_marks ?? 0], ["删除", r.deleted ?? 0],
                      ["跳过", r.skipped ?? 0]]),
      body: notesBody(r), raw: r});
  }

  if (action === "time") {
    const bad = (r.errors || []).length > 0;
    const pend = (r.pending || []).length;
    return rsCard({act: act, state: bad ? "有失败" : (r.written ? "已写入" : (r.queued ? "已入队" : "没有可推的时长")),
      bad: bad, warn: !bad && !r.written && !r.queued, meta: meta,
      chips: rsChips([["入队", (r.queued ?? 0) + " 本", r.queued ? "ok" : ""],
                      ["写入", r.written ?? 0], ["待发", pend || null]]),
      body: detailRows(r.books, (d) => fmtMin(d.minutes)) +
        (pend ? `<p class="meta">队列里还剩 ${pend} 条，下次推送自动补发。</p>` : ""),
      raw: r});
  }

  if (action === "links") {
    return rsCard({act: act, state: r.ok ? "已对齐" : "失败", bad: !r.ok, meta: meta,
      chips: rsChips([["新增映射", r.added ?? 0, r.added ? "ok" : ""], ["云端书架", (r.shelf ?? 0) + " 本"]]),
      body: `<p class="meta">${r.added ? "新认出了 " + r.added + " 本云端书；" : ""}书库里
        跟云端对得上的书都可以推 / 拉了。</p>`, raw: r});
  }
  return rsCard({act: act, state: "完成", meta: meta, raw: r});
}

// 下载任务: 状态码翻成中文, 只报关键数字
const JOB_STATE = {queued: "排队中", resolving: "获取下载地址", downloading: "下载中",
                   prep: "整理入库", done: "已下载", error: "下载失败"};

function renderJob(j) {
  if (!j || !j.id) return rsCard({act: "下载", state: "任务不存在", bad: true});
  const done = j.state === "done";
  const bad = j.state === "error";
  const progress = j.total ? Math.min(100, Math.round((j.bytes || 0) / j.total * 100)) : null;
  const chips = rsChips([
    ["大小", j.total ? mb(j.total) : (j.bytes ? mb(j.bytes) : null)],
    ["已下载", j.bytes && !done ? mb(j.bytes) : null],
    ["方式", j.overwrite ? "原地覆盖" : null]
  ]);
  let body = `<ul class="rs-list"><li><b>${esc(j.name || j.key || "")}</b>
    ${rsNote(j.state === "downloading" && progress != null ? progress + "%" : "")}</li></ul>`;
  if (j.path) body += `<p class="meta">落在 ${esc(j.path)}</p>`;
  if (done) body += `<p class="meta">已建立云端映射并拉了最近一次进度与时长。</p>`;
  if (bad && j.error) body += `<ul class="rs-list"><li><span class="pill err">原因</span> ${esc(j.error)}</li></ul>`;
  return rsCard({act: "下载云书", state: JOB_STATE[j.state] || j.state, bad: bad,
    warn: !done && !bad, meta: [j.started ? ts(j.started) : "", j.finished ? "→ " + ts(j.finished) : ""]
      .filter(Boolean).join(" "), chips: chips, body: body, raw: j});
}

function toast(msg, bad) {
  const d = document.createElement("div");
  d.className = "rc-toast" + (bad ? " bad" : "");
  d.textContent = msg;
  document.body.appendChild(d);
  setTimeout(() => d.remove(), 3600);
}

async function runAction(action) {
  // 结果写进 #out —— 它在 sync 面板里。人站在别的面板点按钮时看不到,
  // 所以额外给一句回执条 (顺手也挡掉"点了没反应"的错觉)。
  const box = $("out");
  const offPane = !box || !box.offsetParent;
  const label = ACT_LABEL[action] || action;
  out(label + " …");
  const r = await api("/api/sync/run", {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({action})});
  outHtml(renderRun(action, r));
  if (offPane) {
    if (r && r.ok) toast(`${label} 完成${r.ms ? " · " + (r.ms / 1000).toFixed(1) + "s" : ""}`);
    else toast(`${label} 失败：${(r && (r.error || (r.busy && "已有同步在进行"))) || "未知错误"}`, true);
  }
  PROBE = {at: null, books: {}};   // 刚同步过, 侧车的"上次已知"已是最新
  loadState();
}

async function loadAuth() {
  const a = await api("/api/auth");
  setPill("auth-pill", a.authenticated ? "已认证" : "未认证", !!a.authenticated);
  $("auth-meta").textContent = a.authenticated
    ? ("账号 " + (a.usr_masked || a.usr || "") + " · 凭据 " + (a.file_mtime || ""))
    : "还没有掌阅凭据 —— 点「扫二维码」用 App 授权";
}

const LOG_LEVEL = {DEBUG: ["调试", ""], INFO: ["信息", "ok"], WARNING: ["注意", "warn"],
                   ERROR: ["失败", "err"], CRITICAL: ["失败", "err"], RAW: ["原文", ""]};

// 旧日志里是英文缩写 (push OK …) —— 顺手翻译成一句人话; 新日志已是中文, 原样显示。
const LOG_RULES = [
  [/^push OK (.+?) pct=([\d.]+) ch=(\S+) total=(\S+)$/,
    (m) => `推送成功 · ${m[1]} · 进度 ${pct(+m[2])} · ${chapterNo(m[3])}（共 ${m[4]} 章）`],
  [/^push FAIL (.+?): (.+)$/, (m) => `推送失败 · ${m[1]} · ${m[2]}`],
  [/^pull OK (.+?) pct=([\d.]+) -> ch=(\S+) off=(\S+)$/,
    (m) => `拉取成功 · ${m[1]} · 云端 ${pct(+m[2])}，落在 ${chapterNo(m[3])}`]
];

function logText(msg) {
  const s = String(msg == null ? "" : msg);
  for (let i = 0; i < LOG_RULES.length; i++) {
    const m = LOG_RULES[i][0].exec(s);
    if (m) return LOG_RULES[i][1](m);
  }
  return s;
}

function shortTs(v) {
  const s = String(v || "");
  const m = /^(\d{4})-(\d\d)-(\d\d)[ T](\d\d):(\d\d)/.exec(s);
  return m ? m[2] + "-" + m[3] + " " + m[4] + ":" + m[5] : s;
}

function renderLog(lines) {
  const el = $("log"); if (!el) return;
  if (!lines || !lines.length) {
    el.innerHTML = `<p class="meta">还没有同步记录。</p>`;
    return;
  }
  // 新的在上: 打开面板先看到刚刚发生了什么
  el.innerHTML = lines.slice().reverse().map((l) => {
    const lv = LOG_LEVEL[l.level] || [l.level, ""];
    return `<div class="log-row${lv[1] ? " lv-" + lv[1] : ""}">
      <span class="log-ts" title="${esc(l.ts)}">${esc(shortTs(l.ts))}</span>
      <span class="log-lv">${esc(lv[0])}</span>
      <span class="log-msg">${esc(logText(l.msg))}</span></div>`;
  }).join("");
}

async function loadLog() {
  const s = await api("/api/log?n=80");
  renderLog(s.lines || []);
}

async function qrStart() {
  const r = await api("/api/auth/qr", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
  const box = $("qr-box");
  if (r.svg) { box.innerHTML = r.svg; box.style.display = "block"; }
  $("qr-status").textContent = "等待扫码（App → 我的 → 扫码登录）";
  const t = setInterval(async () => {
    const s = await api("/api/auth/check", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
    if (s.status === "ok") { clearInterval(t); $("qr-status").textContent = "已登录"; box.style.display = "none"; loadAuth(); }
    else if (s.status === "invalid") { clearInterval(t); $("qr-status").textContent = "二维码已过期，请重试"; }
  }, 3000);
}

// 唯一的一条点击委托 —— 元素在脚本执行时未必已解析完, 所以不用直连监听
document.addEventListener("click", (e) => {
  const t = e.target && e.target.closest ? e.target.closest("button") : null;
  if (t && t.dataset && t.dataset.act) runAction(t.dataset.act);
  const id = (t && t.id) || (e.target && e.target.id) || "";
  if (id === "qr-start") qrStart();
  else if (id === "log-refresh") loadLog();
  else if (id === "link-refresh") probeCloud();
  else if (id === "cloud-refresh") loadCloud(true);
});
// 用 document 委托, 不依赖"脚本执行时元素已在 DOM 里" (defer 顺序靠不住)
document.addEventListener("input", (e) => {
  if (e.target && e.target.id === "shelf-q") renderShelf();
});
document.addEventListener("click", (e) => {
  const btn = e.target && e.target.closest ? e.target.closest("[data-sort]") : null;
  if (!btn) return;
  e.preventDefault();
  setShelfSort(btn.getAttribute("data-sort"), true);
  renderShelf();
});
document.addEventListener("click", async (e) => {
  const btn = e.target && e.target.closest ? e.target.closest("#shelf-rescan") : null;
  if (!btn || btn.disabled) return;
  // 圆钮里的文字是 .rc-rail-name —— 不能改 textContent, 那会把图标一起抹掉
  const name = btn.querySelector(".rc-rail-name");
  const setLabel = (s) => {
    if (name) name.textContent = s; else btn.textContent = s;
  };
  btn.disabled = true; btn.classList.add("is-busy"); setLabel("扫描中…");
  const r = await api("/read/api/books?refresh=1");
  if (r && r.books) { LIB = r.books; renderShelf(); renderShelfStats(); }
  await loadState();
  btn.disabled = false; btn.classList.remove("is-busy"); setLabel("重扫书库");
});
setShelfSort(SORT, false);
document.addEventListener("DOMContentLoaded", () => setShelfSort(SORT, false));
loadState(); loadCloud(); loadAuth(); loadLog();
"""


def _page_html(user: str | None) -> str:
    from recto.app.ui import shell

    body = r"""
<style>
  .rc-main h1 { margin-bottom: 6px }
  .stat-grid { display: grid; gap: var(--rc-gap); grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); margin-bottom: 18px }
  .stat { padding: 12px 14px; border: 1px solid var(--rc-line); border-radius: var(--rc-radius-field); background: var(--rc-surface) }
  .stat b { display: block; font: 600 1.5em/1.2 var(--rc-serif, serif) }
  .cell-book { min-width: 12em }
  .meta { color: var(--rc-mute); font-size: var(--rc-fs-xs) }
</style>

<section data-pane="library">
  <div class="rc-head-row">
    <div class="rc-rail" role="toolbar" aria-label="书库操作">
      <button id="shelf-rescan" class="rc-rail-btn" aria-label="重扫书库">
        <span class="rc-rail-ico" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
               stroke-linecap="round" stroke-linejoin="round">
            <path d="M20 11.2a8 8 0 1 0-2.4 6.2"/>
            <path d="M20 4.6v6.6h-6.6"/>
          </svg>
        </span>
        <span class="rc-rail-name">重扫书库</span>
      </button>
      <button class="rc-rail-btn" data-act="links" aria-label="对齐云端映射">
        <span class="rc-rail-ico" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
               stroke-linecap="round" stroke-linejoin="round">
            <path d="M10.6 13.4a2 2 0 0 0 2.8 0l3.5-3.5a2 2 0 0 0-2.8-2.8L12.7 8.5"/>
            <path d="M13.4 10.6a2 2 0 0 0-2.8 0l-3.5 3.5a2 2 0 0 0 2.8 2.8l1.4-1.4"/>
          </svg>
        </span>
        <span class="rc-rail-name">对齐云端映射</span>
      </button>
    </div>
  </div>

  <div class="shelf-head">
    <div class="shelf-search">
      <input id="shelf-q" type="search" placeholder="搜书名 / 作者" aria-label="搜书名或作者">
    </div>
    <div class="shelf-sort" role="group" aria-label="书架排列顺序">
      <button class="chip" data-sort="recent" aria-pressed="true">最近阅读</button>
      <button class="chip" data-sort="title" aria-pressed="false">书名</button>
    </div>
    <div class="shelf-stat" id="shelf-stat">
      <div class="s"><b>…</b><span>本</span></div>
    </div>
  </div>

  <div class="shelf-grid" id="shelf">
    <div class="empty" style="grid-column:1/-1">加载中…</div>
  </div>
</section>

<section data-pane="sync">

  <div class="row fill" style="margin:16px 0">
    <button class="primary" data-act="pull">从掌阅拉取</button>
    <button data-act="push">推到掌阅</button>
    <button class="ghost" data-act="notes">推划线/书签</button>
    <button class="ghost" data-act="time">推今天时长</button>
    <button class="ghost" data-act="links">对齐映射</button>
  </div>
  <p class="meta" id="tick-note"></p>

  <details class="sec" id="sec-auth">
    <summary>掌阅认证 <span class="pill" id="auth-pill">…</span></summary>
    <div class="sec-body">
      <p class="meta" id="auth-meta">加载中…</p>
      <div class="row"><button id="qr-start" class="ghost">扫二维码</button>
        <span class="meta" id="qr-status"></span></div>
      <div id="qr-box" style="display:none;margin-top:14px;max-width:240px"></div>
    </div>
  </details>

  <details class="sec" open id="sec-links">
    <summary>云端映射 <span class="cnt" id="link-cnt">…</span></summary>
    <div class="sec-body">
      <div class="row"><button id="link-refresh" class="ghost">校准云端</button>
        <span class="meta" id="link-note">本地 = 阅读器进度；云端 = 最近一次与云端打过交道的值，点「校准云端」现查</span></div>
      <div class="table-wrap"><table class="bordered">
        <thead><tr><th>书</th><th class="num">本地</th><th class="num">云端</th>
          <th>上次推送</th><th>上次拉取</th></tr></thead>
        <tbody id="link-body"><tr><td colspan="5" class="empty">加载中…</td></tr></tbody>
      </table></div>
    </div>
  </details>

  <details class="sec" id="sec-cloud">
    <summary>掌阅个人云书籍 <span class="pill" id="cloud-pill">…</span></summary>
    <div class="sec-body">
      <p class="meta">下载即原书落进书库（<span id="dl-dir">—</span>），不切片、不改内容；下载后自动建立云端映射并拉一次进度与时长。</p>
      <div class="row"><button id="cloud-refresh" class="ghost">刷新列表</button></div>
      <div class="table-wrap"><table class="bordered">
        <thead><tr><th>书名</th><th class="num">大小</th><th>状态</th><th></th></tr></thead>
        <tbody id="cloud-body"><tr><td colspan="4" class="empty">加载中…</td></tr></tbody>
      </table></div>
    </div>
  </details>

  <details class="sec" id="sec-out">
    <summary>执行结果 <span class="pill" id="out-pill" hidden></span></summary>
    <div class="sec-body"><div id="out" class="rs-wrap"><p class="meta">（还没有操作）</p></div></div>
  </details>

  <details class="sec" id="sec-log">
    <summary>最近日志</summary>
    <div class="sec-body">
      <div class="row"><button id="log-refresh" class="ghost">刷新</button>
        <span class="meta">只显示最近发生的事，点书名可看原始数据</span></div>
      <div id="log" class="log-list"><p class="meta">加载中…</p></div>
    </div>
  </details>
</section>
"""
    return shell.document(title="书架", body=body, active="library",
                          pane_start="library", user=user,
                          head_extra=f"<script defer>{_JS}</script>")


def main() -> None:
    srv = make_server()
    print("recto console on :5015", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
