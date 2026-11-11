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
    """root 写的文件交回目录属主 (目录常由非 root 的应用进程持有)."""
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


def _write_secret(path: Path, text: str) -> tuple[bool, str]:
    """原子写口令文件 (临时文件 → fsync → os.replace)。

    原来是 `f.write_text(...)` 直写: 先 truncate 再写, 写窗口里另一个进程
    (比如正在登录的 verify) 会读到空串或半截 —— 症状是「刚改完口令, 登录
    立刻说口令不对」。
    """
    from recto.core.secret_io import write_secret

    return write_secret(path, text, mode=0o600)


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
        ok, err = _write_secret(f, v + "\n")
        if not ok:
            return ""
        # 重新生成 = 口令被重置回默认值, 一定留痕。
        # 以前这里是**静默**的: 谁删了口令文件都查不到, 表现就是「改了口令,
        # 过一阵又变回 root:root」。这个日志行是这类故障唯一的线索。
        _audit(f"口令文件缺失, 已重新生成初始默认口令 ({len(v)} 位) "
               f"-> {f}")
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


def set_password(new: str, *, who: str = "") -> tuple[bool, str]:
    """改口令。→ (ok, 说明)

    env 提供口令时**拒绝**: 环境变量优先级最高, 改了文件也不生效, 不如直说。
    来源是 config.toml 时同时改那一行 (只动这一行, 注释保留)。

    写完**立刻读回校验**: `password()` 反映的才是登录时真正用的那一份。
    两者不一致就如实报错 (多半是 env / config 把文件盖掉了), 别让用户"改成功了"
    却登不进去。
    """
    new = (new or "").strip()
    tag = f"口令修改 by={who or '-'}"
    if len(new) < 6:
        _audit(f"{tag} 拒绝: 新口令太短 ({len(new)} 位)")
        return False, "新口令至少要 6 位"
    if len(new) > 200:
        _audit(f"{tag} 拒绝: 新口令太长")
        return False, "新口令太长了"
    src = password_source()
    if src == "env":
        _audit(f"{tag} 拒绝: 口令由环境变量提供")
        return False, ("口令由环境变量 RECTO_AUTH_PASSWORD 提供 —— 请改环境变量, "
                       "或去掉它再用本页修改")
    f = password_file()
    ok, err = _write_secret(f, new + "\n")
    if not ok:
        _audit(f"{tag} 失败: 写 {f} 出错 {err}")
        return False, f"写口令文件失败: {err}"
    if src == "config":
        from recto.core import config as _conf

        if not _conf.set_value("auth", "password", new):
            _audit(f"{tag} 失败: config.toml 写不进去 (来源 config)")
            return False, "已更新 admin-password, 但 config.toml 写不进去 (它优先级更高)"
    # 读回校验: 登录判定用的是 password(), 不是"我刚写进文件的字符串"
    if not _same(password(), new):
        shadow = password_source()
        _audit(f"{tag} 失败: 写入后读回不一致 (实际来源 {shadow})")
        return False, ("口令已写入 " + str(f) + f", 但服务端实际用的是「{shadow}」那一份 —— "
                       "请改那个来源 (环境变量 RECTO_AUTH_PASSWORD 或 config.toml 的 "
                       "[auth] password)")
    _audit(f"{tag} 成功: 已写入 {f} (来源 {src}, {len(new)} 位)")
    return True, ""


def _audit(msg: str) -> None:
    """登录 / 口令事件写一行 —— 这类问题没有留痕就只能靠猜。

    data/recto.log 里一条口令记录都没有, 所以「改了口令但登录还是旧口令」无从定位。
    日志落 `<data_dir>/logs/webauth.log` (512KB 轮转); 不记口令本身。
    """
    try:
        # RECTO_AUTH_LOG: 空 = <data_dir>/logs/webauth.log; off = 不写; 其它 = 该路径
        # (测试与临时实例靠它把日志指到临时目录, 不污染生产留痕)
        override = _env("RECTO_AUTH_LOG").strip()
        if override.lower() in ("off", "0", "none", "no"):
            return
        p = Path(override) if override else Path(_cfg().data_dir) / "logs" / "webauth.log"
        d = p.parent
        d.mkdir(parents=True, exist_ok=True)
        try:
            if p.stat().st_size > 512 * 1024:
                os.replace(p, p.with_name(p.name + ".1"))
        except OSError:
            pass
        line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n"
        with p.open("a", encoding="utf-8") as f:
            f.write(line)
        try:
            os.chmod(p, 0o640)
        except OSError:
            pass
        _own(p)
    except Exception:  # noqa: BLE001 — 写日志失败不该影响登录/改口令
        pass


def _who(user: str) -> str:
    u = (user or "").strip()
    return u if len(u) <= 24 else u[:24] + "…"


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


def verify(user: str, password_in: str, *, ip: str = "") -> tuple[bool, str, str]:
    """登录用 → (ok, 错误文案, 途径 'local'|'none')

    失败/成功都写一行 webauth.log (用户名与来源 IP, 不记口令本身):
    「口令改了但登录还是不认」这类问题只能靠这条线定位。
    """
    if not enabled():
        return True, "", "none"
    user = (user or "").strip()
    src = password_source()
    if not user or not password_in:
        _audit(f"登录失败 ip={ip or '-'} user={_who(user) or '-'} 原因=没填全 (来源 {src})")
        return False, "用户名和口令都要填", ""
    want = password()
    if not want:
        _audit(f"登录失败 ip={ip or '-'} user={_who(user)} 原因=服务端没有口令 (来源 {src})")
        return False, "服务端没有配置口令（[auth] password 或 <data_dir>/admin-password）", ""
    ok = _same(user, login_user()) and _same(password_in, want)
    if ok:
        _audit(f"登录成功 ip={ip or '-'} user={_who(user)} (来源 {src})")
        return True, "", "local"
    why = ("用户名不对" if not _same(user, login_user())
           else f"口令不对 (服务端来源 {src})")
    _audit(f"登录失败 ip={ip or '-'} user={_who(user)} 原因={why}")
    return False, "用户名或口令不对", ""


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

    # 登录页不写任何配置项 / 口令来源的提示 (用户口径: 只留输入框和按钮)
    body = shell.login_body(
        next_url=next_url, err=err, user=user,
        ttl_days=max(1, ttl() // 86400),
    )
    return shell.auth_page(title=title or shell.BRAND_FULL, body=body)


def status() -> dict:
    d = _load()
    return {"enabled": enabled(), "mode": mode(), "dir": str(state_dir()),
            "ttl": ttl(), "cached_users": sorted((d.get("verifier") or {}).keys())}
