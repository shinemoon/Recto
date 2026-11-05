"""掌阅「个人云书籍」—— 列表 / 下载到书库 / 搬移 / 删除。

解耦后只有一条链路: **下载原书到 Recto 自己的书库目录**。
不切片、不归档第二份、不碰任何外部书目库。下完立刻:

  1. 在书库里登记 (阅读器扫描)
  2. 写边车 `<书名>.recto.json` 的 cloud 段 (云端身份 + 文件名)
  3. 拉一次云端进度/笔记 (best-effort)

接口 (个人云书架, 自己上传的书, 无 DRM):
    GET  /bookstore/open/cloud/bookshelf/list
    GET  /bookstore/open/cloud/storage/file/info?file_ids=
    POST /bookstore/open/cloud/bookshelf/upsert   (type=1 新增 / 2 删除 / 3 更新)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

from recto.core import geometry, paths, sidecar
from recto.sources import cloud_auth

PC_BASE = "https://api-pc.zhangyue.com"
CLOUD_BASE = "https://icloud.zhangyue.com"

SHELF_LIST_PATH = "/bookstore/open/cloud/bookshelf/list"
SHELF_UPSERT_PATH = "/bookstore/open/cloud/bookshelf/upsert"
FILE_INFO_PATH = "/bookstore/open/cloud/storage/file/info"

LIST_PATH = "/cloud/storage2/safe/getCloudBookListV2"
INFO_PATH = "/cloud/storage2/safe/getCloudBookInfo"
DEL_PATH = "/cloud/storage2/safe/delBooks"

UA = "Mozilla/5.0 (Recto)"

STATE_DIR = paths.STATE_DIR
CACHE_PATH = STATE_DIR / "cloud_shelf.json"
INDEX_PATH = STATE_DIR / "cloud_books.json"

_DL_SUFFIXES = (".epub", ".ebk3", ".pdf", ".mobi", ".azw3", ".txt")


class CloudError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def _auth() -> dict:
    return cloud_auth.load() if hasattr(cloud_auth, "load") else paths.load_zy_auth()


def _qs(extra: dict | None = None) -> str:
    return urllib.parse.urlencode(paths.zy_query_params(_auth(), extra or {}))


def _fetch(url: str, *, timeout: int = 40, data: bytes | None = None,
           headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("User-Agent", UA)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _api(path: str, extra: dict | None = None, *, timeout: int = 30,
         base: str = PC_BASE) -> dict:
    raw = _fetch(f"{base}{path}?{_qs(extra)}", timeout=timeout)
    return json.loads(raw.decode("utf-8", "replace"))


def _multipart(path: str, fields: dict, *, timeout: int = 30, base: str = PC_BASE) -> dict:
    boundary = "----recto" + uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n"
                 f"{v}\r\n").encode("utf-8")
    body += f"--{boundary}--\r\n".encode("utf-8")
    raw = _fetch(f"{base}{path}?{_qs()}", data=body, timeout=timeout,
                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    return json.loads(raw.decode("utf-8", "replace"))


# --------------------------------------------------------------------------
# 云书架
# --------------------------------------------------------------------------

def shelf_raw() -> dict:
    j = _api(SHELF_LIST_PATH)
    body = j.get("body") or {}
    if str(j.get("code")) not in ("0", "None"):
        raise CloudError(f"云书架列表失败: {j.get('msg') or j}")
    return {"version": body.get("current_version"), "data": body.get("data") or []}


def _norm_shelf(it: dict) -> dict:
    bid = str(it.get("book_id") or "")
    fid = str(it.get("book_file_id") or "")
    return {
        "key": f"shelf:{bid}",
        "source": "shelf",
        "id": bid,
        "file_id": fid,
        "cover_file_id": it.get("cover_file_id") or "",
        "name": it.get("book_name") or f"book-{bid[:8]}",
        "author": it.get("author") or "",
        "size": int(it.get("book_file_size") or 0),
        "ctime": it.get("ctime") or "",
        "book_type": it.get("book_type"),
        "personal": bool(it.get("is_local_book")),
        "raw": it,
    }


def shelf_books(*, personal_only: bool = True) -> list[dict]:
    raw = shelf_raw()
    out = []
    for it in raw["data"]:
        if personal_only and not it.get("is_local_book"):
            continue
        out.append(_norm_shelf(it))
    if personal_only:
        try:
            _atomic_json(CACHE_PATH, {"shelf": out,
                                      "at": datetime.now().isoformat(timespec="seconds")})
        except OSError:
            pass
    return out


def _find_cached(key: str) -> dict | None:
    try:
        doc = json.loads(CACHE_PATH.read_text("utf-8"))
    except (OSError, ValueError):
        doc = {}
    for b in (doc.get("shelf") or []):
        if b.get("key") == key or b.get("id") == key:
            return b
    return None


# --------------------------------------------------------------------------
# 下载地址
# --------------------------------------------------------------------------

def resolve_file_url(key: str) -> tuple[str, dict]:
    """返回 (可下载直链, book 记录)。key = "shelf:<id>" / 裸 id。"""
    book = _find_cached(key)
    if not book:
        shelf_books()
        book = _find_cached(key)
    if not book:
        raise CloudError(f"找不到这本书: {key}")
    fid = book.get("file_id") or ""
    if not fid:
        raise CloudError("这本书没有 book_file_id（可能只是书架条目，文件不在云上）")
    j = _api(FILE_INFO_PATH, {"file_ids": fid})
    ent = (j.get("body") or {}).get(fid) or {}
    url = ent.get("sign_url")
    if not url:
        raise CloudError(f"没拿到签名地址: {str(j)[:200]}")
    obj = ent.get("object_name") or ""
    suffix = obj.rsplit(".", 1)[-1] if "." in obj else "epub"
    return url, {**book, "suffix": suffix or "epub", "object_name": obj}


# --------------------------------------------------------------------------
# 本地索引 / 目录
# --------------------------------------------------------------------------

def _read_index() -> dict:
    try:
        return json.loads(INDEX_PATH.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _atomic_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), "utf-8")
    os.replace(tmp, path)


def _save_index(doc: dict) -> None:
    _atomic_json(INDEX_PATH, doc)


def _record(key: str, rec: dict | None) -> None:
    doc = _read_index()
    if rec is None:
        doc.pop(str(key), None)
    else:
        doc[str(key)] = rec
    _save_index(doc)


def dl_dir() -> str:
    """下载落点 = 书库目录 (可选子目录)。配置改, 不在这里改。"""
    return str(paths.PATHS.download_dir)


def safe_dir(target: str | None = None) -> Path:
    """落地目录必须落在书库里。"""
    base = Path(dl_dir()).resolve()
    if not target:
        p = base
    else:
        p = Path(os.path.expanduser(str(target)))
        if not p.is_absolute():
            p = base / p
        p = p.resolve()
        if not (p == base or base in p.parents):
            raise CloudError(f"只能落在书库 {base} 里（含子目录）: {p}")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_name(name: str, ident: str, suffix: str) -> str:
    base = re.sub(r"[\\/:*?\"<>|\n\r\t]", "_", (name or f"book-{ident}")).strip()
    if not base:
        base = f"book-{ident}"
    if suffix and not base.lower().endswith("." + suffix.lower()):
        base = f"{base}.{suffix}"
    return base[:180]


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suf = path.stem, path.suffix
    for i in range(1, 1000):
        cand = path.with_name(f"{stem} ({i}){suf}")
        if not cand.exists():
            return cand
    return path.with_name(f"{stem}-{int(time.time())}{suf}")


def _share_file(path: Path) -> None:
    """服务可能以 root 跑; 文件属主跟随父目录, 让 CLI 侧也能读写。"""
    try:
        st, pst = path.stat(), path.parent.stat()
        if os.geteuid() == 0 and (st.st_uid != pst.st_uid or st.st_gid != pst.st_gid):
            os.chown(path, pst.st_uid, pst.st_gid)
        os.chmod(path, 0o644)
    except OSError:
        pass


# --------------------------------------------------------------------------
# 下载任务
# --------------------------------------------------------------------------

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _job_new(key: str, dest: Path, name: str) -> str:
    jid = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[jid] = {"id": jid, "key": str(key), "state": "queued",
                      "dest": str(dest), "name": name, "bytes": 0, "total": None,
                      "path": None, "error": None, "note": None,
                      "started": datetime.now().isoformat(timespec="seconds"),
                      "finished": None}
    return jid


def start_download(key: str, dest_dir: str | None = None, *, name: str | None = None,
                   overwrite: bool = False, target: str | None = None) -> str:
    """起一个下载任务。

    overwrite=True + target=<书库里已有文件的绝对路径> → 原地覆盖 (原书直接替换)。
    """
    dest = safe_dir(dest_dir)
    fixed: Path | None = None
    if overwrite and target:
        base = Path(dl_dir()).resolve()
        fixed = Path(os.path.expanduser(str(target))).resolve()
        if not (fixed == base or base in fixed.parents):
            raise CloudError(f"只能落在书库 {base} 里: {fixed}")
        if fixed.is_dir():
            raise CloudError(f"目标是目录不是文件: {fixed}")
    jid = _job_new(key, fixed.parent if fixed else dest,
                   name or (fixed.name if fixed else ""))
    threading.Thread(target=_download_worker,
                     args=(jid, str(key), dest, name, fixed), daemon=True).start()
    return jid


def _download_worker(jid: str, key: str, dest: Path, name: str | None,
                     fixed: Path | None = None) -> None:
    job = _jobs[jid]

    def upd(**kw):
        with _jobs_lock:
            job.update(kw)

    try:
        upd(state="resolving")
        url, info = resolve_file_url(key)
        suffix = (info.get("suffix") or "epub").lstrip(".")
        fname = _safe_name(name or info.get("name") or "", info.get("id") or key, suffix)
        target = fixed if fixed else _unique(dest / fname)
        upd(state="downloading", name=target.name, overwrite=bool(fixed))

        req = urllib.request.Request(url, headers={"User-Agent": UA})
        got = 0
        tmp = target.with_name(target.name + ".part")
        with urllib.request.urlopen(req, timeout=600) as r, open(tmp, "wb") as f:
            total = r.headers.get("Content-Length")
            upd(total=int(total) if total and total.isdigit() else None)
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                upd(bytes=got)
        if got == 0:
            tmp.unlink(missing_ok=True)
            raise CloudError("下载 0 字节")
        os.replace(tmp, target)
        _share_file(target)

        # --- 落盘即登记: 边车映射 + 阅读器书目
        upd(state="registering")
        book32 = _md5(target)[:32].upper()
        bookinfo = {"id64": info.get("id") or "", "id32": book32,
                    "name": info.get("name") or target.stem,
                    "author": info.get("author") or "",
                    "downloaded_at": datetime.now().astimezone().isoformat(timespec="seconds")}
        try:
            geo = geometry.load_geometry(target)
            bookinfo["total_count"] = int(geo.total_count)
        except Exception:
            pass
        sidecar.set_cloud(target, **bookinfo)

        from recto.services import reader_state

        reader_state.scan(force=True)

        # --- 拉一次云端进度 / 笔记 (best-effort)
        pull_report: dict = {}
        book_key = reader_state.book_key_by_path(target)
        if book_key:
            try:
                from recto.services import sync as _sync

                pull_report["progress"] = _sync.pull_once(keys=[book_key], only_forward=False)
            except Exception as e:  # noqa: BLE001
                pull_report["progress_error"] = f"{type(e).__name__}: {e}"
            try:
                from recto.services import notes as _notes

                pull_report["notes"] = _notes.pull(keys=[book_key])
            except Exception as e:  # noqa: BLE001
                pull_report["notes_error"] = f"{type(e).__name__}: {e}"

        rec = {"path": str(target), "size": got, "key": key,
               "source": info.get("source"), "bookid": info.get("id"),
               "name": target.name, "author": info.get("author"),
               "book_key": book_key,
               "downloaded_at": datetime.now().astimezone().isoformat(timespec="seconds")}
        _record(key, rec)
        upd(state="done", path=str(target), bytes=got, book_key=book_key,
            pull=pull_report, finished=datetime.now().isoformat(timespec="seconds"))
    except Exception as e:  # noqa: BLE001
        upd(state="error", error=f"{type(e).__name__}: {e}",
            finished=datetime.now().isoformat(timespec="seconds"))


def _md5(path: Path) -> str:
    from recto.services.sync import file_md5

    return file_md5(path)


def job_status(jid: str) -> dict | None:
    with _jobs_lock:
        j = _jobs.get(jid)
        return dict(j) if j else None


def jobs_list(limit: int = 20) -> list[dict]:
    with _jobs_lock:
        items = sorted(_jobs.values(), key=lambda x: x["started"], reverse=True)
        return [dict(x) for x in items[:limit]]


# --------------------------------------------------------------------------
# 搬移 / 删除
# --------------------------------------------------------------------------

def move_local(key: str, dest_dir: str, *, new_name: str | None = None) -> dict:
    idx = _read_index()
    rec = idx.get(str(key))
    if not rec:
        raise CloudError("这本还没下载到本地")
    src = Path(rec["path"])
    if not src.exists():
        _record(key, None)
        raise CloudError(f"索引里的文件已不存在: {src}")
    dest = safe_dir(dest_dir)
    target = _unique(dest / (_safe_name(new_name, rec.get("bookid") or key, src.suffix.lstrip("."))
                             if new_name else src.name))
    shutil.move(str(src), str(target))
    # 边车跟书走
    sc_src, sc_dst = sidecar.sidecar_path(src), sidecar.sidecar_path(target)
    if sc_src.exists():
        shutil.move(str(sc_src), str(sc_dst))
    rec.update({"path": str(target), "name": target.name,
                "moved_at": datetime.now().isoformat(timespec="seconds")})
    _record(key, rec)
    return rec


def delete_local(key: str, *, delete_file: bool = False) -> dict:
    """从本地索引移除; delete_file=True 连文件 (和边车) 一起删。

    也接受 `file:<绝对路径>` (书库里没登记进索引的文件)。
    """
    if str(key).startswith("file:"):
        p = Path(str(key)[len("file:"):])
        base = Path(dl_dir()).resolve()
        rp = p.resolve()
        if not (rp == base or base in rp.parents):
            raise CloudError(f"只能删书库里的文件: {p}")
        if not p.exists():
            return {"ok": True, "file_deleted": False, "path": str(p)}
        if not delete_file:
            raise CloudError("未登记进索引的文件只能整体删掉（勾选「删文件」）")
        sidecar.delete(p)
        p.unlink()
        return {"ok": True, "file_deleted": True, "path": str(p)}

    idx = _read_index()
    rec = idx.get(str(key))
    if not rec:
        raise CloudError("这本不在本地索引里")
    removed = False
    if delete_file:
        p = Path(rec.get("path") or "")
        if p.exists():
            sidecar.delete(p)
            p.unlink()
            removed = True
    _record(key, None)
    return {"ok": True, "file_deleted": removed, "path": rec.get("path")}


def delete_cloud(keys: list[str], *, source: str | None = None) -> dict:
    """删云上的书 (个人云书架 bookshelf/upsert type=2)。"""
    keys = [str(k) for k in keys if str(k).strip()]
    if not keys:
        raise CloudError("keys 为空")
    shelf_ids = [k.split(":", 1)[1] if k.startswith("shelf:") else k for k in keys]
    ver = shelf_raw().get("version")
    if ver is None:
        raise CloudError("拿不到 current_version，删不了")
    data = [{"book_id": i, "dtype": 0} for i in shelf_ids]
    j = _multipart(SHELF_UPSERT_PATH,
                   {"usr": _auth().get("usr", ""), "current_version": ver,
                    "type": 2, "data": json.dumps(data, ensure_ascii=False)})
    if str(j.get("code")) != "0":
        raise CloudError(f"删除云书架条目失败: {j.get('code')} {j.get('msg')}")
    for i in shelf_ids:
        _record(f"shelf:{i}", None)
    return {"ok": True, "deleted": [f"shelf:{i}" for i in shelf_ids]}


def local_files() -> list[dict]:
    """书库里的书: 索引 + 未登记文件, 附实际大小 / 云端映射。"""
    out = []
    seen: set[str] = set()
    for key, rec in _read_index().items():
        p = Path(rec.get("path", ""))
        rec = dict(rec)
        rec["key"] = key
        rec["exists"] = p.exists()
        rec["actual_size"] = p.stat().st_size if p.exists() else None
        rec["cloud_link"] = bool(sidecar.cloud_link(p)) if rec["exists"] else False
        out.append(rec)
        try:
            seen.add(str(p.resolve()))
        except OSError:
            seen.add(str(p))
    base = Path(dl_dir())
    if base.is_dir():
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in _DL_SUFFIXES:
                continue
            rp = str(p.resolve())
            if rp in seen:
                continue
            out.append({"key": f"file:{rp}", "path": rp, "name": p.name,
                        "source": "本地目录", "unmanaged": True, "exists": True,
                        "actual_size": p.stat().st_size,
                        "cloud_link": bool(sidecar.cloud_link(p))})
    return sorted(out, key=lambda r: r.get("downloaded_at") or "", reverse=True)
