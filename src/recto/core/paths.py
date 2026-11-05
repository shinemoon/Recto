"""路径 / 凭据常量 —— 全部由 `config.toml` 派生。

解耦后这里只剩 Recto 自己的东西:
  - 书库目录 (原书)
  - 状态目录 (同步状态库 / 日志)
  - 云端凭据
  - 摘录账本 / 藏书票 / 语料

不再有任何外部书目数据库 / 容器路径 / 分页 sidecar。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .config import get_config


@dataclass(frozen=True)
class Paths:
    """一处解析, 全局复用。"""

    root: Path                 # 状态根 (= data_dir)
    data_dir: Path
    state_dir: Path
    state_db: Path
    log_dir: Path
    library_dir: Path          # 书库主目录 (原书)
    library_dirs: tuple[Path, ...]
    download_dir: Path         # 云端下载落点 (= 书库 + download_subdir)
    cloud_auth_file: Path
    corpus_dir: Path
    webauth_dir: Path
    device_data_dir: Path
    bookplate_data_dir: Path
    bookplate_asset_root: Path
    bookplate_output_dir: Path


def resolve() -> Paths:
    c = get_config()
    state_dir = c.cloud_state_dir
    data_dir = c.data_dir
    return Paths(
        root=data_dir,
        data_dir=data_dir,
        state_dir=state_dir,
        state_db=state_dir / "sync.sqlite",
        log_dir=state_dir / "logs",
        library_dir=c.books_dir,
        library_dirs=tuple(c.library_dirs),
        download_dir=c.cloud_dl_dir,
        cloud_auth_file=c.cloud_auth_file,
        corpus_dir=c.corpus_dir,
        webauth_dir=c.auth_shared_dir,
        device_data_dir=c.device_data_dir,
        bookplate_data_dir=c.bookplate_data_dir,
        bookplate_asset_root=c.bookplate_asset_root,
        bookplate_output_dir=c.bookplate_output_dir,
    )


PATHS = resolve()

# --- 兼容层: 平移过来的代码直接引用这些名字 ------------------------------
STATE_DIR = PATHS.state_dir
STATE_DB = PATHS.state_db
LOG_DIR = PATHS.log_dir
LIBRARY_DIR = PATHS.library_dir
LIBRARY_DIRS = PATHS.library_dirs
CLOUD_DL_DIR = str(PATHS.download_dir)
ZY_AUTH_FILE = PATHS.cloud_auth_file
CORPUS_DIR = PATHS.corpus_dir
WEBAUTH_DIR = PATHS.webauth_dir
DATA_ROOT = PATHS.device_data_dir
BOOKPLATE_DATA_DIR = PATHS.bookplate_data_dir
BOOKPLATE_ASSET_ROOT = PATHS.bookplate_asset_root
BOOKPLATE_OUTPUT_DIR = str(PATHS.bookplate_output_dir)

# 云端 API 端点
ZY_GET_PROCESS_URL = "https://icloud.zhangyue.com/cloud/storage2/safe/getProcess"
ZY_UPLOAD_PROCESS_URL = "https://icloud.zhangyue.com/cloud/storage2/safe/uploadProcess"


def reload() -> Paths:
    """重新解析配置 (设置页改了书库目录之后调用)。

    模块级常量 (`PATHS` / `LIBRARY_DIR` / …) 是历史包袱: 平移过来的代码按名字引用它们,
    所以这里整体重绑一次, 而不是要求每个调用方都改成函数调用。
    """
    global PATHS
    from .config import get_config

    get_config(reload=True)
    PATHS = resolve()
    g = globals()
    g["STATE_DIR"] = PATHS.state_dir
    g["STATE_DB"] = PATHS.state_db
    g["LOG_DIR"] = PATHS.log_dir
    g["LIBRARY_DIR"] = PATHS.library_dir
    g["LIBRARY_DIRS"] = PATHS.library_dirs
    g["CLOUD_DL_DIR"] = str(PATHS.download_dir)
    g["ZY_AUTH_FILE"] = PATHS.cloud_auth_file
    g["CORPUS_DIR"] = PATHS.corpus_dir
    g["WEBAUTH_DIR"] = PATHS.webauth_dir
    g["DATA_ROOT"] = PATHS.device_data_dir
    g["BOOKPLATE_DATA_DIR"] = PATHS.bookplate_data_dir
    g["BOOKPLATE_ASSET_ROOT"] = PATHS.bookplate_asset_root
    g["BOOKPLATE_OUTPUT_DIR"] = str(PATHS.bookplate_output_dir)
    return PATHS


def load_zy_auth(path: Path | None = None) -> dict:
    """读云端凭据。回退看 ZY_AUTH_JSON 环境变量。"""
    raw = os.environ.get("ZY_AUTH_JSON")
    if raw:
        return json.loads(raw)
    with open(path or ZY_AUTH_FILE) as f:
        return json.load(f)


def zy_query_params(auth: dict, extra: dict | None = None) -> dict:
    """拼云端接口必带参数。"""
    p = {
        "usr": auth["usr"],
        "zysid": auth["zysid"],
        "p29": auth["p29"],
        "p2": auth["p2"],
        "p3": auth["p3"],
        "p4": auth["p4"],
        "p33": auth.get("p33", "com.chaozh.iReaderFree"),
    }
    if extra:
        p.update(extra)
    return p


def ensure_dirs() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)


def _norm_name(s: str) -> str:
    """书名归一: 去分隔符 / 空白 / 全角空格。"""
    s = re.sub(r"[\s　]*[-–—_]+[\s　]*", " ", (s or "").strip())
    return re.sub(r"\s+", " ", s).strip()


def iter_library_books(exts: tuple[str, ...] = (".epub", ".txt")):
    """递归遍历书库, 产出 (path, root)。"""
    seen: set[str] = set()
    for base in PATHS.library_dirs:
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in exts:
                continue
            try:
                key = str(p.resolve())
            except OSError:
                key = str(p)
            if key in seen:
                continue
            seen.add(key)
            yield p, base


def find_library_book(name: str) -> str:
    """按书名在书库里找原书, 找不到返空串。

    先精确, 再剥常见修饰 (完结/精校/全本), 最后分隔符归一后比较。
    """
    if not name:
        return ""
    flat = _norm_name(name)
    bases = list(PATHS.library_dirs)
    for stem in (name, _norm_name(name)):
        for base in bases:
            for ext in (".epub", ".txt"):
                p = base / f"{stem}{ext}"
                if p.exists():
                    return str(p)
    for junk in ("（1-54册）完结精校版", "完结精校版", "完结版", "全本"):
        stem = name.replace(junk, "")
        for base in bases:
            for ext in (".epub", ".txt"):
                p = base / f"{stem}{ext}"
                if p.exists():
                    return str(p)
    for p, _base in iter_library_books():
        if _norm_name(p.stem) == flat:
            return str(p)
    return ""
