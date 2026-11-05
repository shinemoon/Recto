"""登录口令 (Recto 自有账号) —— 口令来源 / cookie 签名 / 限流 / 关闭开关。

跑法: cd <仓库根> && PYTHONPATH=src python3 tests/test_webauth.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

TMP = Path(tempfile.mkdtemp())
os.environ["RECTO_WEBAUTH_DIR"] = str(TMP)
os.environ["RECTO_AUTH_PASSWORD"] = "s3cret-pass"
os.environ.pop("RECTO_AUTH_USER", None)  # 登录名用 config.toml (初始 root)
os.environ.pop("RECTO_AUTH_DISABLE", None)

from recto.app import webauth  # noqa: E402
from recto.core.config import get_config  # noqa: E402

FAILED = []
USER = webauth.login_user()  # 初始默认 root


def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILED.append(msg)


# --- 0. 初始默认账号 ------------------------------------------------------
ok(webauth.DEFAULT_USER == "root", "初始默认登录名 root")
ok(webauth.DEFAULT_PASSWORD == "root", "初始默认口令 root")
ok(webauth.login_user() == "root", "没配 env 时用 config.toml 的登录名 (初始 root)")
os.environ["RECTO_AUTH_USER"] = "alice"
get_config(reload=True)
ok(webauth.login_user() == "alice", "RECTO_AUTH_USER 可覆盖登录名")
os.environ.pop("RECTO_AUTH_USER", None)
get_config(reload=True)
ok(webauth.login_user() == "root", "去掉 env 后回到 root")


# --- 1. 口令 / 模式 -------------------------------------------------------
ok(webauth.enabled() is True, "默认需要登录")
ok(webauth.mode() == "password", "默认模式 password")
ok(webauth.verify(USER, "s3cret-pass")[0] is True, "正确账号口令通过")
ok(webauth.verify(USER, "bad")[0] is False, "错口令拒绝")
ok(webauth.verify("other", "s3cret-pass")[0] is False, "错账号拒绝")
ok(webauth.verify("", "s3cret-pass")[0] is False, "空账号拒绝")
ok(webauth.verify(USER, "")[0] is False, "空口令拒绝")

# --- 2. cookie 签名 -------------------------------------------------------
token = webauth.make_token(USER)
ok(webauth.parse_token(token) == USER, "签名 token 可验回用户名")
ok(webauth.parse_token(token + "x") is None, "签名被改即失效")
ok(webauth.parse_token("") is None and webauth.parse_token(None) is None, "空 token 不认")

hdr = webauth.set_cookie(USER)
ok("zy_webauth=" in hdr and "HttpOnly" in hdr and "SameSite=Strict" in hdr,
   "Set-Cookie 带 HttpOnly / SameSite=Strict")
clear = webauth.clear_cookie()
ok("Max-Age=0" in clear, "清 cookie 用 Max-Age=0")
ok(webauth.current_user(hdr.split(";")[0]) == USER, "从 Cookie 头解出用户")

# 过期
old = webauth.make_token(USER, now=time.time() - webauth.ttl() - 10)
ok(webauth.parse_token(old) is None, "过期 token 不认")

# --- 3. 限流 --------------------------------------------------------------
ip = "10.0.0.9"
for _ in range(webauth._RATE_N):
    webauth.rate_fail(ip)
wait = webauth.rate_blocked(ip)
ok(wait > 0, "连续失败后限流生效")
webauth.rate_clear(ip)
ok(webauth.rate_blocked(ip) == 0, "清除后不再限流")

# --- 4. 关闭开关 ----------------------------------------------------------
os.environ["RECTO_AUTH_DISABLE"] = "1"
ok(webauth.enabled() is False, "RECTO_AUTH_DISABLE=1 关闭登录")
ok(webauth.verify("anyone", "whatever")[0] is True, "关闭后放行")
os.environ.pop("RECTO_AUTH_DISABLE", None)

# --- 5. 口令落盘 (没配 env/config 时自动生成) -----------------------------
os.environ.pop("RECTO_AUTH_PASSWORD", None)
cfg = get_config()
cfg.values["auth"]["password"] = ""
f = webauth.password_file()
if f.exists():
    f.unlink()
got = webauth.password()
ok(bool(got) and f.exists(), "缺口令时自动生成 <data_dir>/admin-password")
ok(oct(f.stat().st_mode & 0o777) == "0o600", "口令文件权限 0600")
ok(got == webauth.DEFAULT_PASSWORD, "首次生成用初始默认口令 root (不再随机)")
ok(webauth.verify(USER, got)[0] is True, "生成的口令可用")

print()
if FAILED:
    print(f"{len(FAILED)} FAILED")
    raise SystemExit(1)
print("全部通过")
