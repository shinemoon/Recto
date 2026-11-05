"""控制台登录门 (真起 ThreadingHTTPServer)。

跑法: cd <仓库根> && PYTHONPATH=src python3 tests/test_web_gate.py

验的是 Handler 的分流, 不是口令本身 (那是 test_webauth.py):
  * 未登录 GET /            -> 303 /login?next=...
  * 未登录 GET /api/*       -> 401 JSON
  * /healthz                -> 200 (探活用)
  * /assets/*               -> 200 (登录页也要样式)
  * POST /login 口令对      -> 303 + Set-Cookie, 之后能进首页
  * POST /login 口令错      -> 401
  * /logout                 -> 清 cookie
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

TMP = Path(tempfile.mkdtemp())
os.environ["RECTO_WEBAUTH_DIR"] = str(TMP)
os.environ["RECTO_AUTH_PASSWORD"] = "gate-pass"
os.environ.pop("RECTO_AUTH_USER", None)  # 登录名用 config.toml (初始 root)
os.environ.pop("RECTO_AUTH_DISABLE", None)

from recto.app import console as web  # noqa: E402

SRV = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
SRV.daemon_threads = True
threading.Thread(target=SRV.serve_forever, daemon=True).start()
PORT = SRV.server_address[1]


def _req(path, method="GET", form=None, cookie=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=20)
    h, body = {}, None
    if form is not None:
        body = urllib.parse.urlencode(form)
        h["Content-Type"] = "application/x-www-form-urlencoded"
    if cookie:
        h["Cookie"] = cookie
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    data = r.read()
    hd = dict(r.getheaders())
    c.close()
    return r.status, hd, data


LOGIN = "root"  # 初始默认登录名


def _login(user=LOGIN, password="gate-pass", nxt="/"):
    st, hd, _ = _req("/login", "POST", {"username": user, "password": password, "next": nxt})
    return st, (hd.get("Set-Cookie") or "").split(";")[0]


def test_index_redirects_to_login():
    st, hd, _ = _req("/")
    assert st == 303, st
    assert hd.get("Location") == "/login?next=/", hd.get("Location")


def test_api_returns_401_json():
    st, _, d = _req("/api/state")
    assert st == 401 and json.loads(d)["login"] == "/login", (st, d[:60])


def test_healthz_open():
    st, _, d = _req("/healthz")
    assert st == 200 and json.loads(d)["ok"] is True


def test_assets_open():
    st, _, d = _req("/assets/recto.css")
    assert st == 200 and b"--rc-" in d


def test_login_page_open():
    st, _, d = _req("/login")
    assert st == 200 and b'name="password"' in d


def test_login_wrong_password():
    st, ck = _login(password="bad")
    assert st == 401 and not ck, (st, ck)


def test_login_ok_then_browse():
    st, ck = _login()
    assert st == 303 and ck.startswith("zy_webauth="), (st, ck)
    st, _, d = _req("/", cookie=ck)
    assert st == 200 and b"<!doctype html>" in d[:40].lower(), st
    st, _, d = _req("/api/state", cookie=ck)
    assert st == 200 and b"book_count" in d, (st, d[:80])


def test_post_needs_cookie():
    st, _, _ = _req("/api/sync/run", "POST", {})
    assert st == 401, st


def test_logout_clears_cookie():
    _, ck = _login()
    st, hd, _ = _req("/logout", cookie=ck)
    assert st == 303 and "Max-Age=0" in (hd.get("Set-Cookie") or ""), (st, hd)


def test_next_kept_in_login_form():
    st, _, d = _req("/login?next=" + urllib.parse.quote("/api/state"))
    assert st == 200 and b"/api/state" in d


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except AssertionError as e:
                fails += 1
                print("FAIL", name, e)
    SRV.shutdown()
    raise SystemExit(1 if fails else 0)
