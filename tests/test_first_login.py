"""首次登录强制改口令 —— 初始口令 (公开的默认值) 不能直接带进服务。

跑法: cd <仓库根> && python3 tests/test_first_login.py

验的:
  * 口令还是初始默认值时, 已登录会话只能去「改口令」那一页 (页面 303,
    接口 403 JSON)
  * 改完口令后正常放行
  * 两次输入不一致要拒绝
  * 环境变量提供的口令不强制改 (set_password 会拒绝, 逼人走死路)
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = Path(tempfile.mkdtemp())
os.environ["RECTO_DATA_DIR"] = str(TMP / "data")
os.environ["RECTO_WEBAUTH_DIR"] = str(TMP / "webauth")
os.environ["RECTO_AUTH_LOG"] = "off"
os.environ.pop("RECTO_AUTH_PASSWORD", None)   # 走文件来源, 才能改
os.environ.pop("RECTO_AUTH_USER", None)
os.environ.pop("RECTO_AUTH_DISABLE", None)

from recto.app import console as web  # noqa: E402
from recto.app import webauth  # noqa: E402

SRV = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
SRV.daemon_threads = True
threading.Thread(target=SRV.serve_forever, daemon=True).start()
PORT = SRV.server_address[1]

LOGIN = webauth.login_user()


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


def _reset_to_default():
    """把口令文件直接写回初始默认值。

    走 set_password 做不到: 它要求至少 6 位, 而初始默认只有 4 位 ——
    所以「改回初始状态」只能直接写文件 (测试专用)。
    """
    f = webauth.password_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(webauth.DEFAULT_PASSWORD + "\n", "utf-8")
    try:
        os.chmod(f, 0o600)
    except OSError:
        pass


def _login(password=None, nxt="/"):
    st, hd, _ = _req("/login", "POST", {
        "username": LOGIN, "password": password or webauth.DEFAULT_PASSWORD,
        "next": nxt})
    return st, (hd.get("Set-Cookie") or "").split(";")[0]


# --- 1. 判定 -------------------------------------------------------------

def test_default_password_detected():
    _reset_to_default()
    assert webauth.password_is_default() is True
    assert webauth.needs_password_change() is True


def test_changed_password_not_default():
    _reset_to_default()
    ok, err = webauth.set_password("brand-new-pass", who="test")
    assert ok, err
    assert webauth.password_is_default() is False
    assert webauth.needs_password_change() is False


# --- 2. 登录后被拦住 -----------------------------------------------------

def test_login_redirects_to_first_run():
    _reset_to_default()
    st, ck = _login()
    assert st == 303, st
    assert ck.startswith("zy_webauth="), ck


def test_first_run_page_shows_form():
    _reset_to_default()
    _, ck = _login()
    st, _, d = _req("/first-run", cookie=ck)
    assert st == 200, st
    assert b'name="password2"' in d, "改口令页要有两个输入框"
    assert b"root" not in d, "页面不该把初始口令印出来"


def test_blocked_pages_redirect_to_first_run():
    _reset_to_default()
    _, ck = _login()
    st, hd, _ = _req("/", cookie=ck)
    assert st == 303 and "/first-run" in (hd.get("Location") or ""), (st, hd.get("Location"))
    st, hd, _ = _req("/settings", cookie=ck)
    assert st == 303 and "/first-run" in (hd.get("Location") or ""), (st, hd.get("Location"))


def test_blocked_api_returns_403_json():
    _reset_to_default()
    _, ck = _login()
    st, _, d = _req("/api/state", cookie=ck)
    assert st == 403, st
    body = json.loads(d)
    assert body.get("redirect") == "/first-run", body


def test_first_run_needs_login():
    _reset_to_default()
    st, hd, _ = _req("/first-run")
    assert st == 303 and "/login" in (hd.get("Location") or ""), (st, hd.get("Location"))


# --- 3. 改口令 -----------------------------------------------------------

def test_change_password_unlocks():
    _reset_to_default()
    _, ck = _login()
    st, hd, _ = _req("/first-run", "POST",
                     {"password": "brand-new-pass", "password2": "brand-new-pass",
                      "next": "/"}, cookie=ck)
    assert st == 303, st
    assert "/first-run" not in (hd.get("Location") or ""), hd.get("Location")
    assert webauth.needs_password_change() is False
    new_ck = (hd.get("Set-Cookie") or "").split(";")[0]
    st, _, d = _req("/", cookie=new_ck)
    assert st == 200, st
    st, ck2 = _login(password="brand-new-pass")
    assert st == 303 and ck2.startswith("zy_webauth="), (st, ck2)


def test_mismatch_rejected():
    _reset_to_default()
    _, ck = _login()
    st, _, d = _req("/first-run", "POST",
                    {"password": "brand-new-pass", "password2": "other-pass",
                     "next": "/"}, cookie=ck)
    assert st == 400, st
    assert "不一致" in d.decode("utf-8"), "要说清楚是两次不一致"
    assert webauth.password_is_default() is True


def test_short_password_rejected():
    _reset_to_default()
    _, ck = _login()
    st, _, d = _req("/first-run", "POST",
                    {"password": "123", "password2": "123", "next": "/"}, cookie=ck)
    assert st == 400, st
    assert webauth.password_is_default() is True


def test_env_password_not_forced():
    """环境变量提供的口令改不了 (set_password 明确拒绝) → 不该逼用户去改。"""
    os.environ["RECTO_AUTH_PASSWORD"] = "env-only-pass"
    try:
        assert webauth.password_is_default() is False
        assert webauth.needs_password_change() is False
    finally:
        os.environ.pop("RECTO_AUTH_PASSWORD", None)


if __name__ == "__main__":
    fails = 0
    # 按**定义顺序**跑 (不排序): 后面的用例依赖前面的状态复位
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except AssertionError as e:
                fails += 1
                print("FAIL", name, e)
    SRV.shutdown()
    raise SystemExit(1 if fails else 0)
