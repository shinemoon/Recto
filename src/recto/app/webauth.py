"""Web 登录保护 —— Recto 自己的账号口令。

口令来源 (优先级): 环境变量 RECTO_AUTH_PASSWORD > config.toml 的 [auth] password
> `<data_dir>/admin-password` (缺失时按初始默认 root/root 生成, 0600)。
`[auth] mode = "none"` 可整体关闭登录 (仅本机调试用)。

会话: HMAC-SHA256 签名的 cookie (HttpOnly / SameSite=Strict), 默认 7 天;
密钥落在 `[auth] shared_dir` (同机多端口可共用一个登录会话)。
同一 IP 连续失败 8 次 / 5 分钟会被限流。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from recto.core import paths

# 初始默认账号 (首次部署即可登录, 登录后请立刻在 /settings 改口令)
DEFAULT_USER = "root"
DEFAULT_PASSWORD = "root"

COOKIE_NAME = "zy_webauth"
STATE_NAME = "webauth.json"
_LOCK = threading.RLock()
_FAILS: dict[str, list[float]] = {}
_RATE_N = 8
_RATE_WIN = 300


# ---------------------------------------------------------------- env

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default) or ""


def enabled() -> bool:
    if _env("RECTO_AUTH_DISABLE").strip().lower() in ("1", "true", "yes", "on"):
        return False
    try:
        return mode() != "none"
    except Exception:  # noqa: BLE001 — 配置读不到时保守: 需要登录
        return True


def ttl() -> int:
    try:
        return max(300, int(_env("ZY_WEBAUTH_TTL", "604800")))
    except ValueError:
        return 604800


def state_dir() -> Path:
    p = Path(_env("RECTO_WEBAUTH_DIR") or _env("ZY_WEBAUTH_DIR") or str(paths.WEBAUTH_DIR))
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return p


def _own(path: Path) -> None:
    """root 写的文件交回目录属主 (目录常由非 root 的 5013 进程持有)."""
    try:
        if os.geteuid() != 0:
            return
        st = path.parent.stat()
        os.chown(path, st.st_uid, st.st_gid)
    except Exception:  # noqa: BLE001 — 拿不到就算了, 只是会挡住非 root 的写
        pass


# ---------------------------------------------------------------- state

def _load() -> dict:
    p = state_dir() / STATE_NAME
    try:
        d = json.loads(p.read_text("utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001 — 缺/坏都当空
        return {}


def _save(d: dict) -> None:
    p = state_dir() / STATE_NAME
    tmp = p.with_name(f".{STATE_NAME}.{os.getpid()}.tmp")
    with _LOCK:
        try:
            tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), "utf-8")
            try:
                os.chmod(tmp, 0o660)
            except OSError:
                pass
            _own(tmp)
            os.replace(tmp, p)
            _own(p)
        except Exception:  # noqa: BLE001 — 写不了不该让页面 500
            pass


def _secret() -> bytes:
    d = _load()
    s = d.get("secret")
    if isinstance(s, str) and len(s) >= 32:
        try:
            return bytes.fromhex(s)
        except ValueError:
            return s.encode()
    d["secret"] = secrets.token_hex(32)
    d.setdefault("verifier", {})
    _save(d)
    # 两个进程可能同时首启, 以落盘那份为准
    again = _load().get("secret")
    s = again if isinstance(again, str) and len(again) >= 32 else d["secret"]
    try:
        return bytes.fromhex(s)
    except ValueError:
        return s.encode()


# ---------------------------------------------------------------- session

def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode().rstrip("=")


def _unb64(text: str) -> str:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)).decode("utf-8")


def _sign(body: str) -> str:
    return hmac.new(_secret(), body.encode("utf-8"), hashlib.sha256).hexdigest()[:40]


def make_token(user: str, now: float | None = None) -> str:
    exp = int((now if now is not None else time.time()) + ttl())
    body = f"{_b64(user)}.{exp}.{secrets.token_hex(6)}"
    return f"{body}.{_sign(body)}"


def parse_token(value: str | None) -> str | None:
    parts = (value or "").split(".")
    if len(parts) != 4:
        return None
    u, exp, nonce, sig = parts
    body = f"{u}.{exp}.{nonce}"
    if not hmac.compare_digest(_sign(body), sig):
        return None
    try:
        if int(exp) < time.time():
            return None
        return _unb64(u)
    except Exception:  # noqa: BLE001
        return None


def cookie_from_header(header: str | None) -> str | None:
    for part in (header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k.strip() == COOKIE_NAME:
            return v.strip()
    return None


def current_user(cookie_header: str | None) -> str | None:
    """已登录用户名; 未登录 None. 关闭保护时给占位 '*'."""
    if not enabled():
        return "*"
    return parse_token(cookie_from_header(cookie_header))


def set_cookie(user: str) -> str:
    return (f"{COOKIE_NAME}={make_token(user)}; Path=/; Max-Age={ttl()}; "
            f"HttpOnly; SameSite=Strict")


def clear_cookie() -> str:
    return f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"


# ---------------------------------------------------------------- auth

def _cfg():
    from recto.core.config import get_config

    return get_config()


def mode() -> str:
    """auth.mode: password | none"""
    m = str(_cfg().get("auth", "mode", "password") or "password").strip().lower()
    return m if m in ("password", "none") else "password"


def login_user() -> str:
    """登录用户名 (只改这一处: config.toml 的 [auth] user, 缺省初始默认 root)。"""
    try:
        v = str(_cfg().get("auth", "user", DEFAULT_USER) or "").strip()
    except Exception:  # noqa: BLE001 — 配置读不到时用初始默认
        v = ""
    return v or DEFAULT_USER


def password_file() -> Path:
    return Path(_cfg().data_dir) / "admin-password"


def password() -> str:
    """口令来源: env > config.toml > <data_dir>/admin-password (缺失则生成)。"""
    p = _env("RECTO_AUTH_PASSWORD") or str(_cfg().get("auth", "password", "") or "").strip()
    if p:
        return p
    f = password_file()
    try:
        if f.exists():
            v = f.read_text("utf-8").strip()
            if v:
                return v
        f.parent.mkdir(parents=True, exist_ok=True)
        v = DEFAULT_PASSWORD
        f.write_text(v + "\n", "utf-8")
        try:
            os.chmod(f, 0o600)
        except OSError:
            pass
        _own(f)
        return v
    except OSError:
        return ""


def password_source() -> str:
    """口令来源: env | config | file (设置页要如实显示, 别假装能改)。"""
    if _env("RECTO_AUTH_PASSWORD"):
        return "env"
    if str(_cfg().get("auth", "password", "") or "").strip():
        return "config"
    return "file"


def set_password(new: str) -> tuple[bool, str]:
    """改口令。→ (ok, 说明)

    env 提供口令时**拒绝**: 环境变量优先级最高, 改了文件也不生效, 不如直说。
    来源是 config.toml 时同时改那一行 (只动这一行, 注释保留)。
    """
    new = (new or "").strip()
    if len(new) < 6:
        return False, "新口令至少要 6 位"
    if len(new) > 200:
        return False, "新口令太长了"
    src = password_source()
    if src == "env":
        return False, ("口令由环境变量 RECTO_AUTH_PASSWORD 提供 —— 请改环境变量, "
                       "或去掉它再用本页修改")
    try:
        f = password_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(new + "\n", "utf-8")
        try:
            os.chmod(f, 0o600)
        except OSError:
            pass
        _own(f)
    except OSError as e:
        return False, f"写口令文件失败: {e}"
    if src == "config":
        from recto.core import config as _conf

        if not _conf.set_value("auth", "password", new):
            return False, "已更新 admin-password, 但 config.toml 写不进去 (它优先级更高)"
    return True, ""


def _same(a: str, b: str) -> bool:
    """常量时间比较 —— 两端都转 bytes, 非 ASCII (中文昵称/口令) 也能比。

    hmac.compare_digest 对 str 只接受 ASCII, 中文会直接抛 TypeError。
    """
    try:
        return hmac.compare_digest(str(a).encode("utf-8"), str(b).encode("utf-8"))
    except Exception:  # noqa: BLE001
        return False


def check_password(password_in: str) -> bool:
    """只校验口令, 不看用户名 (会话已认证时改口令用这个)。"""
    want = password()
    if not want:
        return False
    return _same(password_in or "", want)


def verify(user: str, password_in: str) -> tuple[bool, str, str]:
    """登录用 → (ok, 错误文案, 途径 'local'|'none')"""
    if not enabled():
        return True, "", "none"
    user = (user or "").strip()
    if not user or not password_in:
        return False, "用户名和口令都要填", ""
    want = password()
    if not want:
        return False, "服务端没有配置口令（[auth] password 或 <data_dir>/admin-password）", ""
    ok = _same(user, login_user()) and _same(password_in, want)
    return (True, "", "local") if ok else (False, "用户名或口令不对", "")


# ---------------------------------------------------------------- ratelimit

def rate_blocked(ip: str) -> int:
    """被限流则返回还需等待的秒数 (>0), 否则 0."""
    now = time.time()
    with _LOCK:
        arr = [t for t in _FAILS.get(ip, []) if now - t < _RATE_WIN]
        _FAILS[ip] = arr
        if len(arr) >= _RATE_N:
            return max(1, int(_RATE_WIN - (now - arr[0])) + 1)
        return 0


def rate_fail(ip: str) -> None:
    with _LOCK:
        _FAILS.setdefault(ip, []).append(time.time())


def rate_clear(ip: str) -> None:
    with _LOCK:
        _FAILS.pop(ip, None)


# ---------------------------------------------------------------- 页面

def login_page(title: str = "", next_url: str = "/", err: str = "",
               user: str = "") -> str:
    """登录页 —— 与其它页面共用同一套 token 与皮肤 (外观页里换肤, 这里跟着变)。"""
    from recto.app.ui import shell

    body = shell.login_body(
        next_url=next_url, err=err, user=user,
        ttl_days=max(1, ttl() // 86400),
        note="云阅 Recto 自有账号口令（[auth] 段或 <data_dir>/admin-password）。",
    )
    return shell.auth_page(title=title or shell.BRAND_FULL, body=body)


def status() -> dict:
    d = _load()
    return {"enabled": enabled(), "mode": mode(), "dir": str(state_dir()),
            "ttl": ttl(), "cached_users": sorted((d.get("verifier") or {}).keys())}
