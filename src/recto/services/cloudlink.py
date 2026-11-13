"""本地书接入云端同步 —— 用「文件内容指纹」当云端身份。

为什么需要它
------------
掌阅 App 对**本地导入**的书 (`mBookID == 0`), 进度 / 划线 / 批注 / 时长
全部按 `getFileMD5(file).toUpperCase()` (32 hex, 文件内容 MD5) 记在云端
(`BookBrowserFragment.q1()` → `BookHolder.mLocalBookFileMd5`)。

也就是说: 只要手机和书库里有**同一份文件**, 云端早就有这本书的账。
Recto 的同步链路只认边车里的 `cloud.id32` / `cloud.id64`, 书库里那些
"没进云书架"的书一个都没有 → 于是一直不同步。

这个模块就是补上那份身份:

    attach(book_key)  → 边车写 cloud.id32 (md5) [+ 云书架命中时的 id64]
                      → 立刻 pull 一次进度

边界
----
* 只按**内容 MD5** 认书。同名的另一份文件不算(字节不同 ⇒ 云端是另一本)。
* 不传文件。掌阅云端的上传接口 (`cloud_book/storage/upload/authorize`) 要求
  App 原生签名 (`Security.hash(sortedParams)`, JNI), 从网页侧拿不到 —— 见
  `upload_capability()`。所以"接入同步"与"上传文件"是两件事:
  前者只解决进度/划线互通, 后者才会让这本书出现在云书架里。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path

from recto.core import geometry, sidecar
from recto.services import reader_state, sync
from recto.sources import cloud as zy_cloud

_LOCK = threading.RLock()
_MD5: dict[tuple, str] = {}
_SHELF = {"at": 0.0, "rows": []}
_SHELF_TTL = 300.0


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _md5(path: str | Path) -> str:
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return ""
    key = (str(p), int(st.st_size), int(st.st_mtime))
    hit = _MD5.get(key)
    if hit:
        return hit
    val = sync.file_md5(p).upper()
    _MD5[key] = val
    return val


def _shelf(refresh: bool = False) -> list[dict]:
    from recto.services import acquisition

    with _LOCK:
        if not refresh and time.time() - _SHELF["at"] < _SHELF_TTL:
            return list(_SHELF["rows"])
    try:
        rows = acquisition.shelf_books()
    except Exception:  # noqa: BLE001 — 云端不可达时按"没有云书架"处理
        rows = []
    with _LOCK:
        if rows:
            _SHELF["rows"] = rows
            _SHELF["at"] = time.time()
        return list(_SHELF["rows"])


def shelf_by_md5(refresh: bool = False) -> dict[str, dict]:
    """{文件内容 MD5(32 大写): 云书架条目}"""
    out: dict[str, dict] = {}
    for b in _shelf(refresh):
        bid = str(b.get("id") or "")
        if len(bid) >= 32:
            out[bid[:32].upper()] = b
    return out


def _catalog(path: str | Path) -> int:
    """云端口径的章数 (= epub 目录条数); 不是 epub 就 0 (让上层用云端现值)。"""
    try:
        if str(path).lower().endswith(".epub"):
            return int(geometry.catalog_count(path) or 0)
    except Exception:  # noqa: BLE001 — 目录坏了不影响接入
        pass
    return 0


def _row(key: str) -> dict:
    return reader_state.get_book(key) or {}


def items(refresh: bool = False) -> list[dict]:
    """书库里所有**没有云端身份**的书 (外加已接入的, 用 attached 标出)。"""
    shelf = shelf_by_md5(refresh)
    out: list[dict] = []
    for b in reader_state.list_books():
        path = str(b.get("path") or "")
        if not path:
            continue
        link = sidecar.cloud_link(path) or {}
        id32 = str(link.get("id32") or "")
        id64 = str(link.get("id64") or "")
        if id64:  # 已在云书架里 —— 不属于"本地书", 交给常规同步面板
            continue
        md5 = id32 or _md5(path)
        hit = shelf.get(md5) if md5 else None
        out.append({
            "key": b.get("key"),
            "title": b.get("title") or Path(path).stem,
            "author": b.get("author") or "",
            "path": path,
            "size": int(b.get("bytes") or 0),
            "pct": round(float(b.get("percent") or 0.0), 6),
            "md5": md5,
            "attached": bool(id32),
            "in_shelf": bool(hit),
            "shelf_name": (hit or {}).get("name") or "",
        })
    out.sort(key=lambda r: (not r["attached"], r["title"]))
    return out


def summary(refresh: bool = False) -> dict:
    rows = items(refresh)
    return {
        "items": rows,
        "total": len(rows),
        "attached": sum(1 for r in rows if r["attached"]),
        "syncable": sum(1 for r in rows if r["attached"] and r["md5"]),
        "upload": upload_capability(),
    }


def attach(key: str) -> dict:
    """把「文件内容 MD5」写成这本书的云端身份, 然后 pull 一次进度。"""
    row = _row(key)
    path = str(row.get("path") or "")
    if not path:
        raise ValueError("书库记录里没有文件路径")
    if not Path(path).exists():
        raise FileNotFoundError(path)
    md5 = _md5(path)
    if not md5:
        raise ValueError("算不出文件指纹")
    hit = shelf_by_md5(True).get(md5)
    title = str(row.get("title") or Path(path).stem)
    total = _catalog(path)
    sidecar.touch_cloud(
        path,
        id32=md5,
        id64=(hit or {}).get("id") or "",
        name=title,
        total_count=(total or None),  # 目录解析不出来时别覆盖云端已有的章数
        identity="file-md5",
        attached_at=_now(),
    )
    _MD5.clear()
    _SHELF["at"] = 0.0
    sync.links(refresh=True)  # 让 pull 立刻看到这份新身份 (links 有 30s 缓存)
    res: dict = {
        "ok": True, "key": key, "title": title, "md5": md5,
        "in_shelf": bool(hit), "total_count": total,
        "pull": None,
    }
    try:
        res["pull"] = sync.pull_book(key, force=True)
    except Exception as e:  # noqa: BLE001 — 拉不动不算接入失败
        res["pull"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return res


def attach_all() -> dict:
    done, failed = [], []
    for r in items(refresh=True):
        if r["attached"]:
            continue
        try:
            done.append(attach(r["key"]))
        except Exception as e:  # noqa: BLE001
            failed.append({"key": r["key"], "error": f"{type(e).__name__}: {e}"})
    return {"ok": True, "attached": len(done), "failed": failed, "detail": done}


def detach(key: str) -> dict:
    """撤掉云端身份 (只动边车, 云端记录与本地进度都不删)。"""
    row = _row(key)
    path = str(row.get("path") or "")
    if not path:
        raise ValueError("书库记录里没有文件路径")
    sidecar.touch_cloud(path, id32="", id64="", identity="", attached_at="")
    return {"ok": True, "key": key}


def upload_capability() -> dict:
    """能不能**把文件本身**传到掌阅云 —— 目前不能, 原因写在 reason 里。

    实测 (2026-11-13):
      * `icloud.../cloud_book/storage/upload/authorize`
        → 参数齐 (file_type/file_id/file_name/file_size/user_name/timestamp) 后返
          `100004 verify sign error`。sign = App 里 `Security.hash(sortedParams)`
          (JNI 原生), 网页侧拿不到。
      * `api-pc.../bookstore/open/cloud/storage/upload/authorize` 同样过不了
        (`600001 token Verification failed` → 补齐后 `200003` / `100004`)。
      * `icloud.../cloud/bookcase/safe/uploadData` 返 `20712 文件签名不能为空`。
    结论: 文件上传必须有 App 的签名; 只靠 usr/zysid 走不通。
    """
    return {
        "available": False,
        "reason": "掌阅云端上传接口要求 App 原生签名 (Security.hash)，网页端无法生成",
        "endpoints": [
            "icloud.zhangyue.com/cloud_book/storage/upload/authorize",
            "api-pc.zhangyue.com/bookstore/open/cloud/storage/upload/authorize",
            "icloud.zhangyue.com/cloud/bookcase/safe/uploadData",
        ],
        "hint": "同一份文件在手机上导入一次后，进度/划线/时长即可双向同步",
    }
