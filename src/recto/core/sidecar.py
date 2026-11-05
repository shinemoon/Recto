"""每本书的 JSON 边车 (sidecar) —— 默认就跟原书放在同一个目录。

为什么要它
----------
解耦前, 「这本书对应云端哪一本书 / 上次推到云端的进度」记在一个全局
SQLite (`book_link` 表) 里, 而那张表的主键是外部书目库的 series/chapter ——
等于把 Recto 的账挂在别人的 ID 上。解耦后改成**一书本一文件**:

    <书库目录>/<书名>.recto.json

好处:
  - 换机器 / 换服务只搬书库就够, 映射跟着书走
  - 手改、备份、diff 都是纯文本
  - 不会因为外部库重扫、换 ID 而全部失效

约定
----
  - 文件名: `<stem>.recto.json`; 用户自备的语料可以叫 `<stem>.keywords.json`
  - 写入: 唯一临时名 + fsync + os.replace (书库可能在 FUSE 上, 避免半截文件)
  - 字段全是增量合并, 不做整文件覆盖 (别的进程可能同时写了别的段)
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

SIDECAR_SUFFIX = ".recto.json"
KEYWORDS_SUFFIX = ".keywords.json"

_lock = threading.RLock()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sidecar_path(book_path: str | Path, suffix: str = SIDECAR_SUFFIX) -> Path:
    """`/a/b/书名.epub` → `/a/b/书名.recto.json`。"""
    p = Path(book_path)
    return p.with_name(p.stem + suffix)


def keywords_path(book_path: str | Path) -> Path:
    """书库里的术语表约定位置: `<书名>.keywords.json`。"""
    return sidecar_path(book_path, KEYWORDS_SUFFIX)


def find_keywords(book_path: str | Path) -> Path | None:
    for p in (keywords_path(book_path), sidecar_path(book_path, ".terms.json")):
        if p.exists():
            return p
    return None


def _read_raw(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def read(book_path: str | Path) -> dict:
    """读边车; 不存在/损坏返空 dict。"""
    return _read_raw(sidecar_path(book_path))


def _deep_patch(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_patch(out[k], v)
        else:
            out[k] = v
    return out


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def write(book_path: str | Path, patch: dict) -> dict:
    """增量写 (深合并), 返回写后的完整 dict。"""
    path = sidecar_path(book_path)
    with _lock:
        cur = _read_raw(path)
        cur.setdefault("version", 1)
        cur = _deep_patch(cur, patch)
        cur["updated_at"] = _now()
        _atomic_write(path, cur)
    return cur


def delete(book_path: str | Path) -> None:
    path = sidecar_path(book_path)
    try:
        path.unlink()
    except OSError:
        pass


# --- 云端映射 --------------------------------------------------------------

def cloud_link(book_path: str | Path) -> dict | None:
    """返回 {"id64","id32","name",...}; 没映射过返 None。"""
    link = read(book_path).get("cloud") or {}
    if not link.get("id64") and not link.get("id32"):
        return None
    return link


def set_cloud(book_path: str | Path, **fields: Any) -> dict:
    """写云端映射字段 (合并)。"""
    return write(book_path, {"cloud": dict(fields)})


def touch_cloud(book_path: str | Path, **fields: Any) -> dict:
    """只更新映射里的若干字段 (不改 book 段)。"""
    patch = {k: v for k, v in fields.items() if v is not None}
    return write(book_path, {"cloud": patch}) if patch else read(book_path)


def iter_sidecars(roots: list[Path] | tuple[Path, ...] | None = None) -> list[tuple[Path, dict]]:
    """枚举所有带云端映射的边车: [(原书路径, sidecar dict)]。"""
    from . import paths

    bases = list(roots) if roots else list(paths.PATHS.library_dirs)
    out: list[tuple[Path, dict]] = []
    seen: set[str] = set()
    for base in bases:
        if not base.exists():
            continue
        for p in sorted(base.rglob("*" + SIDECAR_SUFFIX)):
            if not p.is_file():
                continue
            data = _read_raw(p)
            if not data:
                continue
            stem = p.name[: -len(SIDECAR_SUFFIX)]
            book = p.with_name(stem + ".epub")
            if not book.exists():
                book = p.with_name(stem + ".txt")
            if not book.exists():
                continue
            key = str(book)
            if key in seen:
                continue
            seen.add(key)
            out.append((book, data))
    return out


def publish_note(book_path: str | Path, key: str, value: Any) -> None:
    """给人工看的轻量备注 (例如上次同步结果)。"""
    write(book_path, {"notes": {key: value}})
