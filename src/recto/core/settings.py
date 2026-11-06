"""用户设置 —— 目前三项: 昵称 / 口令 / 书库目录。

与 `config.toml` 的分工:

  - `config.toml` 是**部署配置** (端口 / 数据目录 / 凭据 / 语料路径), 由部署的人维护;
  - `settings.json` 是**用的人**在设置页里改的那几项, 落在 `<data_dir>/settings.json`。

生效优先级: **环境变量 > settings.json > config.toml**
(容器化部署时环境变量说了算, 设置页会把「由环境变量决定」如实显示出来,
不会假装保存成功。)

`settings.json` 只放「覆盖项」, 没有的键就完全跟随 config.toml ——
所以想回到 config.toml 的写法, 把那一项清空即可 (`clear`)。
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

NAME = "settings.json"

# 口令文件 (与 webauth 共用同一个位置语义)
_LOCK = threading.RLock()

# 允许设置的键 (白名单, 其余一律不写盘)
KEYS = ("nickname", "library_dir",
        # 云端同步 (设置页「云端同步」卡): 布尔用 "on"/"off" 存,
        # 数字用字符串存 —— settings.json 里人能直接看懂
        "sync_auto", "sync_pull_on_open", "sync_flush_on_hide",
        "sync_stamp", "sync_push_debounce",
        # 定时同步 (心跳通道): 开关 + 周期 + 是否顺带推划线/时长
        "sync_interval", "sync_interval_sec",
        "sync_tick_notes", "sync_tick_time")


# ---------------------------------------------------------------- 文件

def _path(cfg=None) -> Path:
    if cfg is None:
        from .config import get_config

        cfg = get_config()
    return Path(cfg.data_dir) / NAME


def _own(p: Path) -> None:
    """root 写的文件交回目录属主 (目录常由非 root 的进程持有)。"""
    try:
        if os.geteuid() != 0:
            return
        st = p.parent.stat()
        os.chown(p, st.st_uid, st.st_gid)
    except Exception:  # noqa: BLE001 — 拿不到就算了
        pass


def load(cfg=None) -> dict:
    try:
        d = json.loads(_path(cfg).read_text("utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001 — 缺 / 坏都当空
        return {}


def save(d: dict) -> bool:
    """原子写。返回是否真的落盘。"""
    p = _path()
    keep = {k: v for k, v in (d or {}).items() if k in KEYS and v not in (None, "")}
    tmp = p.with_name(f".{NAME}.{os.getpid()}.tmp")
    with _LOCK:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(keep, ensure_ascii=False, indent=1), "utf-8")
            try:
                os.chmod(tmp, 0o660)
            except OSError:
                pass
            _own(tmp)
            os.replace(tmp, p)
            _own(p)
            return True
        except Exception:  # noqa: BLE001 — 写不了不该让页面 500
            try:
                tmp.unlink()
            except OSError:
                pass
            return False


def update(**kv) -> dict:
    """改几项 (空串 = 删掉这一项覆盖)。返回落盘后的完整设置。"""
    d = load()
    for k, v in (kv or {}).items():
        if k not in KEYS:
            continue
        v = (v or "").strip() if isinstance(v, str) else v
        if v in (None, ""):
            d.pop(k, None)
        else:
            d[k] = v
    save(d)
    return load()


def clear(*keys) -> dict:
    return update(**{k: "" for k in (keys or KEYS)})


# ---------------------------------------------------------------- 配置覆盖

def _bool(v, default: bool | None = None) -> bool | None:
    """settings.json 里的 "on"/"off"/1/true… → bool。空 = 不覆盖。"""
    if v is None or v == "":
        return default
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("1", "on", "true", "yes", "y"):
        return True
    if s in ("0", "off", "false", "no", "n"):
        return False
    return default


_SYNC_BOOLS = (
    ("sync_auto", "auto"),
    ("sync_pull_on_open", "pull_on_open"),
    ("sync_flush_on_hide", "flush_on_hide"),
    ("sync_stamp", "stamp"),
    ("sync_tick_notes", "tick_notes"),
    ("sync_tick_time", "tick_time"),
)


def _interval_override(d: dict) -> int | None:
    """设置页的「定时同步」开关 + 周期 → `[sync] interval_sec` (0 = 关)。"""
    on = _bool(d.get("sync_interval"))
    raw = str(d.get("sync_interval_sec") or "").strip()
    try:
        num = int(float(raw)) if raw else None
    except (TypeError, ValueError):
        num = None
    if num is not None:
        num = max(0, min(3600, num))
        if on is False:
            return 0
        return num
    if on is None:
        return None
    return 60 if on else 0


def overrides(cfg=None) -> dict:
    """给 `config.Config.load()` 用的覆盖层 (只含真的设了的项)。"""
    d = load(cfg)
    out: dict = {}
    lib = str(d.get("library_dir") or "").strip()
    if lib:
        out.setdefault("library", {})["dir"] = lib
    sync: dict = {}
    for key, name in _SYNC_BOOLS:
        v = _bool(d.get(key))
        if v is not None:
            sync[name] = v
    try:
        db = str(d.get("sync_push_debounce") or "").strip()
        if db:
            sync["push_debounce_sec"] = max(3, min(300, int(float(db))))
    except (TypeError, ValueError):
        pass
    iv = _interval_override(d)
    if iv is not None:
        sync["interval_sec"] = iv
    if sync:
        out["sync"] = sync
    return out


def active_overrides() -> dict:
    """当前真正生效的覆盖项 (环境变量压过设置页时, 那一项不算)。"""
    d = load()
    env_lib = os.environ.get("RECTO_LIBRARY_DIR")
    out: dict = {}
    lib = str(d.get("library_dir") or "").strip()
    if lib and not env_lib:
        out["library_dir"] = lib
    nick = str(d.get("nickname") or "").strip()
    if nick:
        out["nickname"] = nick
    env_auto = os.environ.get("RECTO_SYNC_AUTO")
    for key, name in _SYNC_BOOLS:
        if key == "sync_auto" and env_auto is not None:
            continue
        v = _bool(d.get(key))
        if v is not None:
            out[key] = v
    db = str(d.get("sync_push_debounce") or "").strip()
    if db and os.environ.get("RECTO_SYNC_PUSH_DEBOUNCE") is None:
        out["sync_push_debounce"] = db
    iv = _interval_override(d)
    if iv is not None and os.environ.get("RECTO_SYNC_INTERVAL") is None:
        out["sync_interval_sec"] = iv
    return out


# ---------------------------------------------------------------- 取值

def nickname() -> str:
    return str(load().get("nickname") or "").strip()


def display_name(login: str | None = None) -> str:
    """顶栏显示用的名字: 昵称优先, 没设就用登录名。"""
    return nickname() or (login or "").strip()


def avatar_char(login: str | None = None) -> str:
    name = display_name(login)
    return name[:1].upper() if name else "⚙"


def library_dir(cfg=None) -> str:
    """当前生效的书库目录 (绝对路径)。"""
    if cfg is None:
        from .config import get_config

        cfg = get_config()
    return str(cfg.books_dir)


def library_source() -> str:
    """书库目录的三档来源: env | settings | config"""
    if os.environ.get("RECTO_LIBRARY_DIR"):
        return "env"
    if str(load().get("library_dir") or "").strip():
        return "settings"
    return "config"


SOURCE_LABEL = {
    "env": "环境变量 RECTO_LIBRARY_DIR",
    "settings": "设置页",
    "config": "config.toml",
}
