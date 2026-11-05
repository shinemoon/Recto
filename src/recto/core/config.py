"""配置层 —— 唯一配置来源是 `config.toml`。

Recto 是独立服务: 这里不出现任何外部书目数据库 / 容器路径。
与本地书库相关的配置项在解耦时全部移除 (那部分属于原来的 5012 服务)。
"""
from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --- 默认值 ---------------------------------------------------------------
# 这些是「本机部署的默认值」, 不是硬编码路径 —— 全部可被 config.toml 覆盖。

# 这些默认值全部**相对项目根**推导 (见 `_resolve_paths`), 不含任何机器路径 ——
# 一份干净的 clone 直接跑就能落到 <项目根>/data 与 <项目根>/library。
# 想换到别处: 改 config.toml, 或写一份不入库的 config.toml.local, 或用 RECTO_* 环境变量。

DEFAULTS: dict[str, Any] = {
    "recto": {
        "host": "0.0.0.0",
        "port": 5015,
        "data_dir": "",                   # 空 = <项目根>/data
    },
    "library": {
        "dir": "",                        # 空 = <项目根>/library
        "dirs": [],                       # 可选: 多个根目录 (dir 仍作主目录)
        "download_subdir": "",            # 相对 library.dir 的子目录; 空 = 根
        "scan_exts": [".epub", ".txt"],
    },
    "cloud": {
        "auth_file": "",                  # 空 = <config_dir>/secrets/cloud.json
        "state_dir": "",                  # 空 = <data_dir>/state
        "download_subdir": "",            # 兼容别名, 见 library.download_subdir
    },
    "device": {
        "port": 8080,
        "data_dir": "",                   # 空 = <data_dir>
    },
    "corpus": {
        "dir": "",                        # 空 = <data_dir>/corpus
        "ui_url": "http://127.0.0.1:5013",
        "start_hint": "",                 # 上游没起时, 503 页上显示的一句启动提示
    },
    # 阅读高亮 (关键词滤镜) 的部署默认值; 用的人的覆盖项在
    # <data_dir>/reading_mark.json (设置页「阅读高亮」卡改的就是它)
    "reader": {
        "mark": {
            "enabled": True,
            "mode": "highlight",          # highlight (不改 DOM) | span (可加粗/圆角)
            "dirs": [],                   # 空 = 用 [corpus] dir
            "min_len": 2,
            "max_terms": 6000,
            "skip_digits": True,
        },
    },
    "bookplate": {
        "data_dir": "",                   # 空 = <data_dir>/bookplate
        "asset_root": "",                 # 空 = <data_dir>/assets/bookplate (需自带模板)
        "output_dir": "",                 # 空 = <data_dir>/bookplate/out
        "node_dirs": [],                  # 额外的 nvm 根 (找不到 node 时按顺序找)
    },
    "auth": {
        "mode": "password",               # password | none
        "user": "root",                   # 登录用户名 (首次启动的默认值, 可在配置里改)
        "password": "",
        "shared_dir": "",                 # 空 = <项目根>/config/webauth
        "ttl_days": 7,
    },
    "sync": {
        "auto": True,
        "push_debounce_sec": 8,
        "pull_gap_sec": 120,
    },
}


def _abs(value: str | Path, fallback: Path) -> Path:
    """空值用 fallback; 相对路径按项目根展开; `~` 展开。"""
    raw = str(value or "").strip()
    p = Path(raw).expanduser() if raw else Path(fallback)
    if not p.is_absolute():
        p = (Path(__file__).resolve().parents[3] / p)
    return p


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass
class Config:
    """Recto 的运行时配置。"""

    values: dict[str, Any] = field(default_factory=dict)
    root: Path = field(default_factory=Path.cwd)
    path: Path | None = None
    overrides: dict[str, Any] = field(default_factory=dict)   # 来自 settings.json

    # --- 构造 ---
    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        """读 config.toml; 不存在则用默认值 + 环境变量覆盖。"""
        root = Path(__file__).resolve().parents[3]   # src/recto/core/config.py → 项目根
        p = Path(path) if path else (root / "config.toml")
        data: dict = {}
        if p.exists():
            with p.open("rb") as f:
                data = tomllib.load(f)
        # config.toml 是「共享的、可以入库的那份」；机器专有的值写进
        # config.toml.local（不入库），逐键覆盖在上面。部署文档里就是这么用的。
        local = p.with_name(p.name + ".local")
        if local.exists():
            try:
                with local.open("rb") as f:
                    data = _deep_merge(data, tomllib.load(f))
            except (OSError, tomllib.TOMLDecodeError) as e:  # noqa: BLE001
                print(f"[config] config.toml.local 读不了, 忽略: {e}", file=sys.stderr)
        merged = _deep_merge(DEFAULTS, data)
        cfg = cls(values=merged, root=root, path=p if p.exists() else None)
        # 顺序很重要: config.toml → 用户设置 (settings.json) → 环境变量
        # (先跑一次 env: settings.json 的位置由 data_dir 决定, 那是 env 说了算的)
        cfg._apply_env()
        over: dict = {}
        try:
            from .settings import overrides as _overrides

            over = _overrides(cfg) or {}
        except Exception:  # noqa: BLE001 — 设置读不出来不该挡启动
            over = {}
        if over:
            cfg.values = _deep_merge(cfg.values, over)
            cfg._apply_env()          # 再跑一次: 环境变量优先级最高
        cfg.overrides = over
        cfg._resolve_paths()
        return cfg

    def _apply_env(self) -> None:
        """环境变量覆盖 (容器化时主要靠这个)。"""
        env_map = {
            "RECTO_HOST": ("recto", "host"),
            "RECTO_PORT": ("recto", "port"),
            "RECTO_DATA_DIR": ("recto", "data_dir"),
            "RECTO_LIBRARY_DIR": ("library", "dir"),
            "RECTO_LIBRARY_DOWNLOAD_SUBDIR": ("library", "download_subdir"),
            "RECTO_CLOUD_AUTH_FILE": ("cloud", "auth_file"),
            "RECTO_CORPUS_DIR": ("corpus", "dir"),
            "RECTO_CORPUS_UI_URL": ("corpus", "ui_url"),
            "RECTO_DEVICE_PORT": ("device", "port"),
            "RECTO_AUTH_MODE": ("auth", "mode"),
            "RECTO_AUTH_USER": ("auth", "user"),
            "RECTO_AUTH_PASSWORD": ("auth", "password"),
            "RECTO_SYNC_AUTO": ("sync", "auto"),
        }
        for env, (sec, key) in env_map.items():
            v = os.environ.get(env)
            if v is None:
                continue
            cur = self.values[sec].get(key)
            if isinstance(cur, bool):
                self.values[sec][key] = v.strip().lower() in ("1", "true", "yes", "on")
            elif isinstance(cur, int):
                self.values[sec][key] = int(v)
            else:
                self.values[sec][key] = v

    def _resolve_paths(self) -> None:
        """把空串 / 相对路径解析成绝对路径 (空 = 相对项目根推导)。"""
        cfgdir = self.root / "config"
        recto = self.values["recto"]
        recto["data_dir"] = _abs(recto.get("data_dir") or "", self.root / "data")
        data = Path(recto["data_dir"])
        lib = self.values["library"]
        cloud = self.values["cloud"]
        dev = self.values["device"]
        auth = self.values["auth"]
        corpus = self.values["corpus"]

        cloud["auth_file"] = str(_abs(cloud.get("auth_file") or "", cfgdir / "secrets" / "cloud.json"))
        cloud["state_dir"] = str(_abs(cloud.get("state_dir") or "", data / "state"))
        lib["dir"] = str(_abs(lib.get("dir") or "", self.root / "library"))
        lib["dirs"] = [str(_abs(p, Path(p))) for p in (lib.get("dirs") or []) if p]
        if not lib.get("download_subdir") and cloud.get("download_subdir"):
            lib["download_subdir"] = cloud["download_subdir"]
        dev["data_dir"] = str(_abs(dev.get("data_dir") or "", data))
        auth["shared_dir"] = str(_abs(auth.get("shared_dir") or "", cfgdir / "webauth"))
        corpus["dir"] = str(_abs(corpus.get("dir") or "", data / "corpus"))
        bp = self.values["bookplate"]
        bp["data_dir"] = str(_abs(bp.get("data_dir") or "", data / "bookplate"))
        bp["asset_root"] = str(_abs(bp.get("asset_root") or "", data / "assets" / "bookplate"))
        bp["output_dir"] = str(_abs(bp.get("output_dir") or "", Path(bp["data_dir"]) / "out"))
        bp["node_dirs"] = [str(_abs(p, Path(p))) for p in (bp.get("node_dirs") or []) if p]

    # --- 取值 ---
    def get(self, section: str, key: str, default: Any = None) -> Any:
        return self.values.get(section, {}).get(key, default)

    def section(self, name: str) -> dict:
        return self.values.get(name, {})

    # --- 常用路径 ---
    @property
    def data_dir(self) -> Path:
        return Path(self.values["recto"]["data_dir"])

    @property
    def books_dir(self) -> Path:
        """书库主目录 (兼容旧名字)。"""
        return Path(self.values["library"]["dir"])

    @property
    def library_dirs(self) -> list[Path]:
        lib = self.values["library"]
        out = [Path(lib["dir"])]
        for p in lib.get("dirs") or []:
            q = Path(p)
            if q not in out:
                out.append(q)
        return out

    @property
    def library_download_dir(self) -> Path:
        lib = self.values["library"]
        sub = str(lib.get("download_subdir") or "").strip().strip("/")
        return self.books_dir / sub if sub else self.books_dir

    @property
    def config_dir(self) -> Path:
        return self.root / "config"

    def path_in_data(self, *parts: str) -> Path:
        return self.data_dir.joinpath(*parts)

    # --- 各域路径 ---
    @property
    def cloud_state_dir(self) -> Path:
        return Path(self.values["cloud"]["state_dir"])

    @property
    def cloud_auth_file(self) -> Path:
        return Path(self.values["cloud"]["auth_file"])

    @property
    def cloud_dl_dir(self) -> Path:
        """云端下载落点 —— 直接就是书库目录 (原书, 不再切片/归档)。"""
        return self.library_download_dir

    @property
    def device_data_dir(self) -> Path:
        return Path(self.values["device"]["data_dir"])

    @property
    def bookplate_data_dir(self) -> Path:
        return Path(self.values["bookplate"]["data_dir"])

    @property
    def bookplate_asset_root(self) -> Path:
        return Path(self.values["bookplate"]["asset_root"])

    @property
    def bookplate_output_dir(self) -> Path:
        return Path(self.values["bookplate"]["output_dir"])

    @property
    def bookplate_node_dirs(self) -> list[Path]:
        """找 node 时额外的 nvm 根目录 (config 里的 [bookplate] node_dirs)。"""
        return [Path(p) for p in (self.values["bookplate"].get("node_dirs") or [])]

    @property
    def corpus_dir(self) -> Path:
        return Path(self.values["corpus"]["dir"])

    @property
    def auth_shared_dir(self) -> Path:
        return Path(self.values["auth"]["shared_dir"])


_cache: Config | None = None


def get_config(reload: bool = False) -> Config:
    """进程级单例。"""
    global _cache
    if _cache is None or reload:
        _cache = Config.load()
    return _cache


# ---------------------------------------------------------------- 写回

def _toml_str(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def set_value(section: str, key: str, value: str) -> bool:
    """把 `[section] key = "value"` 写回 config.toml —— **只改那一行**, 注释与排版全部保留。

    找不到该段就追加一段; 找得到但没这个键, 就插在段头下面一行。
    只在设置页需要改部署配置时用 (例如口令来源就是 config.toml 的情况)。
    """
    p = Config.load().path
    if p is None:
        root = Path(__file__).resolve().parents[3]
        p = root / "config.toml"
    try:
        text = p.read_text("utf-8")
    except OSError:
        text = ""
    lines = text.splitlines()
    head = f"[{section}]"
    line_new = f"{key} = {_toml_str(value)}"
    sec_at = -1
    key_at = -1
    cur = ""
    for i, raw in enumerate(lines):
        s = raw.strip()
        if s.startswith("[") and s.endswith("]") and not s.startswith("[["):
            cur = s
            if s == head and sec_at < 0:
                sec_at = i
            elif sec_at >= 0:
                break                     # 出了这个段, 不用再找
            continue
        if cur == head and sec_at >= 0 and "=" in s and not s.startswith("#"):
            if s.split("=", 1)[0].strip() == key:
                key_at = i
    if sec_at < 0:
        if lines and lines[-1].strip():
            lines.append("")
        lines += [head, line_new]
    elif key_at >= 0:
        lines[key_at] = line_new
    else:
        lines.insert(sec_at + 1, line_new)
    out = "\n".join(lines).rstrip("\n") + "\n"
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(out, "utf-8")
        try:
            os.chmod(tmp, p.stat().st_mode & 0o777)
        except OSError:
            pass
        os.replace(tmp, p)
        return True
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
