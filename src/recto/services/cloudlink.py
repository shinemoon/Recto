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

import hashlib
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


def _md5s(text: str) -> str:
    """字符串的 MD5 (大写) —— 云端 `book_id = MD5(文件) + MD5(book_name)`。"""
    return hashlib.md5(text.encode("utf-8")).hexdigest().upper()


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
        ok = True
    except Exception:  # noqa: BLE001 — 云端不可达时沿用上一次拿到的
        rows, ok = [], False
    with _LOCK:
        if ok:  # 拿到空书架也是"事实", 不能继续沿用旧缓存
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
    """书库里所有本地书 + 它们的云端状态 (是否已在云书架 / 文件本体是否已上传)。

    这张卡既是「接入同步」也是「上传文件本体」的入口, 所以**在云书架的书也列出来**
    (标 `in_shelf`, 不给上传按钮) —— 否则上传完那本就"消失"了, 看着像丢了。
    """
    shelf = shelf_by_md5(refresh)
    out: list[dict] = []
    for b in reader_state.list_books():
        path = str(b.get("path") or "")
        if not path:
            continue
        link = sidecar.cloud_link(path) or {}
        id32 = str(link.get("id32") or "")
        id64 = str(link.get("id64") or "")
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
            "in_shelf": bool(hit) or bool(id64),
            "shelf_name": (hit or {}).get("name") or "",
        })
    out.sort(key=lambda r: (not r["attached"], r["title"]))
    _mark_uploaded(out)
    return out


def _mark_uploaded(rows: list[dict]) -> None:
    """给每行标上「文件本体是否已经在云端存储」(一次批量请求)。

    注意: 文件在云存储 ≠ 在云书架。网页端上传过一次又从书架删掉时,
    文件仍留在 book-prod —— 那种情况只需补书架条目 (`upload` 走 200004 分支)。
    """
    from recto.services import acquisition as cb

    ids = [r["md5"] for r in rows if r.get("md5")]
    present: dict[str, dict] = {}
    if ids:
        try:
            present = cb.file_present(ids)
        except Exception:  # noqa: BLE001 — 云端不可达就当作"没上传过"
            present = {}
    for r in rows:
        r["uploaded"] = bool(present.get(r.get("md5") or ""))


def summary(refresh: bool = False) -> dict:
    rows = items(refresh)
    return {
        "items": rows,
        "total": len(rows),
        "attached": sum(1 for r in rows if r["attached"]),
        "syncable": sum(1 for r in rows if r["attached"] and r["md5"]),
        "uploaded": sum(1 for r in rows if r.get("uploaded")),
        "uploadable": sum(1 for r in rows if not r.get("in_shelf")),
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
    """能不能**把文件本身**传到掌阅云。

    2026-11-13 打通: 走 pc.ireader.com 的 `/upload` 页那套口径
    (`bookstore/open/cloud/storage/upload/authorize` + OSS 预签名 PUT +
    `bookshelf/upsert`), **不需要 App 的原生签名**。
    """
    from recto.services import acquisition as cb

    return {
        "available": True,
        "method": "oss-presigned-put",
        "exts": list(cb.UPLOAD_EXTS),
        "reason": "",
        "hint": "上传后书会出现在掌阅云书架，其它设备/App 可直接下载阅读",
    }


def _ext(path: str | Path) -> str:
    return Path(path).suffix.lstrip(".").lower()


def upload(key: str) -> dict:
    """把书库里这本书的**文件本体**传到掌阅云, 然后写身份 + 拉一次进度。

    流程 (与网页端逐字对齐, 见 `acquisition` 里的注释):
      md5 → authorize → PUT → bookshelf/upsert(type=1) → 边车身份 → pull
    """
    from recto.services import acquisition as cb

    row = _row(key)
    path = str(row.get("path") or "")
    if not path:
        raise ValueError("书库记录里没有文件路径")
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    ext = _ext(p)
    if ext not in cb.UPLOAD_EXTS:
        raise ValueError(f"掌阅不收这种格式: .{ext}")
    md5 = _md5(p)
    if not md5:
        raise ValueError("算不出文件指纹")
    size = p.stat().st_size
    fname = f"{md5}.{ext}"
    title = str(row.get("title") or p.stem)
    # 云书架里显示的名字用**清理过的书名** (网页端用文件名, 但那种
    # "书名 = 日文名 (译者) (z-library.sk...)" 的文件名在 App 里很难看)。
    book_name = (title or p.stem)[:120]
    book_id = md5 + _md5s(book_name)

    hit = shelf_by_md5(True).get(md5)
    if hit:
        return _finish_upload(key, path, md5, str(hit.get("id") or book_id), title,
                             skipped="已经在云书架里", size=size)

    au = cb.authorize_upload(md5, fname, size)
    code = str(au.get("code"))
    body = au.get("body") or {}
    put_state = "skipped"
    if code == "0" and int(body.get("type") or 0) == 1:
        raise ValueError("云端要求分片上传 (STS)，当前还不支持这本书的大小/类型")
    if code == "0":
        data = p.read_bytes()
        cb.put_object(str(body.get("sign_url") or ""), data,
                      callback=str(body.get("callback") or ""),
                      callback_var=str(body.get("callback_var") or ""),
                      md5_hex=md5)
        put_state = "uploaded"
    elif code == "200004":
        put_state = "exists"
    else:
        raise ValueError(f"拿不到上传授权: {code} {au.get('msg')}")

    cb.shelf_add({
        "book_id": book_id, "file_id": md5, "book_name": book_name,
        "book_type": cb.BOOK_TYPE.get(ext, 5), "size": size,
    })
    return _finish_upload(key, path, md5, book_id, title, skipped="", size=size,
                          put_state=put_state)


def _finish_upload(key: str, path: str, md5: str, book_id: str, title: str, *,
                   skipped: str, size: int, put_state: str = "") -> dict:
    """写完边车身份 + 拉一次进度 (上传成功的收尾, 幂等)。"""
    sidecar.touch_cloud(
        path, id32=md5, id64=book_id, name=title,
        total_count=(_catalog(path) or None),
        identity="file-md5", attached_at=_now(),
    )
    _MD5.clear()
    _SHELF["at"] = 0.0
    sync.links(refresh=True)
    res: dict = {"ok": True, "key": key, "title": title, "md5": md5,
                 "book_id": book_id, "size": size, "state": put_state,
                 "skipped": skipped, "pull": None}
    try:
        res["pull"] = sync.pull_book(key, force=True)
    except Exception as e:  # noqa: BLE001 — 拉不动不影响上传结果
        res["pull"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return res


def upload_all() -> dict:
    """书库里所有「有文件、但文件还没进云」的书, 一本本传。"""
    done, failed, skipped = [], [], []
    for r in items(refresh=True):
        if r.get("in_shelf") and r.get("attached"):
            skipped.append(r["key"])
            continue
        try:
            done.append(upload(r["key"]))
        except Exception as e:  # noqa: BLE001
            failed.append({"key": r.get("key"), "title": r.get("title"),
                           "error": f"{type(e).__name__}: {e}"})
    return {"ok": True, "uploaded": len(done), "failed": failed,
            "skipped": len(skipped), "detail": done}


def start_upload(keys: list[str]) -> str:
    """起一个后台上传任务 (大文件要几分钟, 不能卡住请求)。"""
    from recto.services import acquisition as cb

    keys = [k for k in dict.fromkeys(keys) if k]
    if not keys:
        raise ValueError("没挑书")
    jid = cb.job_begin("upload", f"上传到掌阅云 · {len(keys)} 本", total=len(keys))
    threading.Thread(target=_upload_worker, args=(jid, keys), daemon=True).start()
    return jid


def _upload_worker(jid: str, keys: list[str]) -> None:
    from recto.services import acquisition as cb

    cb.job_update(jid, state="running")
    rows: list[dict] = []
    for i, key in enumerate(keys, 1):
        title = str(_row(key).get("title") or key)
        cb.job_update(jid, note=f"正在上传 {i}/{len(keys)} · {title}", bytes=i - 1)
        try:
            r = upload(key)
            rows.append({"key": key, "title": r.get("title") or title, "ok": True,
                         "state": r.get("state") or "", "book_id": r.get("book_id") or "",
                         "size": r.get("size") or 0, "skipped": r.get("skipped") or ""})
        except Exception as e:  # noqa: BLE001 — 一本失败不影响其余
            rows.append({"key": key, "title": title, "ok": False,
                         "error": f"{type(e).__name__}: {e}"})
        cb.job_update(jid, bytes=i)
    ok = sum(1 for r in rows if r.get("ok"))
    bad = [r for r in rows if not r.get("ok")]
    cb.job_update(
        jid, state="done" if not bad else ("done" if ok else "error"),
        note=f"上传完成 {ok}/{len(rows)}" + (f" · 失败 {len(bad)}" if bad else ""),
        error=(bad[0]["error"] if bad and not ok else None),
        result={"ok": ok, "failed": len(bad), "rows": rows},
        finished=datetime.now().isoformat(timespec="seconds"),
    )


