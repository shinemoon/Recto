"""RectoS3 · RectoLink 服务端实现（`/s3/v1/*`）

设备（PaperS3）只跟本模块说话；本模块负责把「设备友好的坐标」
翻译成云端口径，并把云端的口径翻译回来。

**三条不可破的规则**（docs/DECISIONS.md D2 / D12 / D13）：

1. **进度只以 Anchor / pct01 表达**，页码是派生视图，绝不持久化。
   `pct01 = (Σ bytes(spine[0..ch]) + f3) / total_bytes`，
   `chapterId = spine_index - 1`，`totalCount = 目录条数`（不是 spine-1）。
   公式与浮点细节**只信** `recto.core.geometry`。
2. **推送容差 1e-6**，不用 5e-4（后者让最后几十页永远推不上去）。
3. **身份不由设备推导**：只使用服务端下发的
   `identity{id32, id64, notes_drawer, dual}`。

本模块只依赖 `recto.core.geometry` / `recto.services.sync` / `recto.services.reader_state`
/ `recto.services.notes`，**不新增写云端的路径** —— 云端写入仍走 `sync.push_once`。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.parse
from typing import Any

from recto.core import geometry
from recto.services import notes as _notes
from recto.services import reader_state as _rs
from recto.services import sync as _sync

PROTO = 1
PREFIX = "/s3/v1"

FEATURES = ["progress", "notes", "sessions", "books", "fonts", "geometry"]

LIMITS = {
    "max_upload_kb": 512,
    "notes_batch": 64,
    "sessions_batch": 7,
    "books_limit": 200,
}

# 设备令牌 → 设备元信息。真正的实现在 M6.7（配对流程）接入；
# 这里先给一个内存表，让 M7 的契约可以先跑起来（curl 可验）。
_DEVICES: dict[str, dict] = {}


class S3Error(Exception):
    def __init__(self, code: str, msg: str, status: int = 400):
        super().__init__(msg)
        self.code = code
        self.msg = msg
        self.status = status


def _err(code: str, msg: str, status: int = 400) -> dict:
    return {"ok": False, "code": code, "msg": msg}


def _epoch(value: Any) -> int:
    """把 reader_state 的时间戳（ISO 串或 epoch）统一成 Unix 秒。

    reader_state 存的是本地 ISO 串（`2026-11-19T14:06:24`），
    直接 int() 会炸 —— 这类「字段类型假设」在设备协议里代价很高。
    """
    if value is None or value == "":
        return int(time.time())
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value)
    try:
        return int(float(text))
    except ValueError:
        pass
    try:
        from datetime import datetime

        return int(datetime.fromisoformat(text).timestamp())
    except Exception:
        return int(time.time())


# ---------------------------------------------------------------- 设备登记

def register_device(code: str, token: str, meta: dict | None = None) -> dict:
    """5013 调本函数把「配对码」换成设备令牌。"""
    if not code or not token:
        raise S3Error("bad_request", "code 与 token 必填")
    _DEVICES[token] = {
        "id": f"device:{hashlib.sha1(token.encode()).hexdigest()[:12]}",
        "model": (meta or {}).get("model", "papers3"),
        "fw": (meta or {}).get("fw", ""),
        "created_at": int(time.time()),
    }
    return {"ok": True, "device_id": _DEVICES[token]["id"]}


def revoke_device(token: str) -> bool:
    return _DEVICES.pop(token, None) is not None


def device_of(token: str) -> dict | None:
    return _DEVICES.get(token)


def _require_device(token: str) -> dict:
    dev = _DEVICES.get(token or "")
    if dev is None:
        raise S3Error("unauthorized", "设备令牌无效，请在 5013 重新配对", 401)
    return dev


# ---------------------------------------------------------------- 握手

def handshake(payload: dict) -> dict:
    dev_info = payload.get("dev") or {}
    return {
        "ok": True,
        "server_time": int(time.time()),
        "proto": PROTO,
        "features": list(FEATURES),
        "limits": dict(LIMITS),
        "hub": os.environ.get("RECTO_S3_HUB", "recto"),
    }


# ---------------------------------------------------------------- 书列表

def _book_key(path: str) -> str:
    """书 key = sha1(原书真实路径)[0:16]（与 `recto.core.bookfile.book_key` 同算法）。

    注意：**按路径而不是按内容**——内容哈希要读整本书（42 MB 的书要读 42 MB），
    而路径哈希是 O(1)。设备端不自己算，直接用服务端下发的 key。
    """
    from recto.core import bookfile

    try:
        return bookfile.book_key(path)
    except Exception:
        return hashlib.sha1(str(path).encode()).hexdigest()[:16]


def _identity_for(path: str) -> dict:
    """身份**只从边车取**（服务端已与云端对过账），设备不推导。"""
    from recto.core import sidecar

    cloud = sidecar.cloud_link(path) or {}
    id32 = (cloud.get("id32") or "").upper()
    id64 = (cloud.get("id64") or "").upper()
    drawer = "upper"
    dual = False
    try:
        ident = _notes.identity_report([path]) if hasattr(_notes, "identity_report") else {}
        if ident:
            drawer = ident.get("drawer", "upper")
            dual = bool(ident.get("dual"))
    except Exception:
        pass
    return {
        "id32": id32,
        "id64": id64,
        "notes_drawer": drawer,
        "dual": dual,
    }


def list_books(limit: int = 200, cursor: str = "") -> dict:
    books = []
    for row in _rs.list_books():
        path = row.get("path") or ""
        if not path or not os.path.exists(path):
            continue
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        entry: dict[str, Any] = {
            "key": row.get("key") or _book_key(path),
            "title": row.get("title") or os.path.basename(path),
            "author": row.get("author") or "",
            "format": "pdf" if path.lower().endswith(".pdf") else (
                "txt" if path.lower().endswith((".txt", ".md")) else "epub"
            ),
            "size": size,
            "mtime": int(row.get("mtime") or os.path.getmtime(path)),
            "sha256": row.get("sha256") or "",
            "identity": _identity_for(path),
        }
        geo = _rs.geometry_of(entry["key"])
        if geo:
            _spine, total, _name = geo
            entry["geometry"] = {"total_bytes": total}
        books.append(entry)
    books.sort(key=lambda b: (b["title"] or "", b["key"]))
    if cursor:
        books = [b for b in books if b["key"] > cursor]
    page = books[:limit]
    return {
        "ok": True,
        "books": page,
        "next_cursor": page[-1]["key"] if len(books) > limit else "",
    }


def book_geometry(key: str) -> dict:
    row = _rs.get_book(key)
    if not row:
        raise S3Error("no_book", f"书不存在: {key}")
    path = row.get("path") or ""
    geo = None
    try:
        geo = geometry.load_geometry(path, name=row.get("title") or "")
    except Exception:
        geo = None
    if geo is None:
        raise S3Error("no_book", "无法解析该书的几何（可能不是 epub）")
    nodes = []
    for n in geo.toc_nodes[:2000]:
        nodes.append(
            {
                "spine": n.get("spine"),
                "text": n.get("text") or n.get("label") or "",
                "depth": n.get("depth", 0),
            }
        )
    return {
        "ok": True,
        "spine_bytes": list(geo.sizes),
        "total_bytes": geo.total_bytes,
        "catalog_count": geo.total_count,
        "toc_nodes": nodes,
    }


def book_file_path(key: str) -> tuple[str, int]:
    row = _rs.get_book(key)
    if not row:
        raise S3Error("no_book", f"书不存在: {key}")
    path = row.get("path") or ""
    if not os.path.exists(path):
        raise S3Error("no_book", "文件已被移动或删除")
    return path, os.path.getsize(path)


# ---------------------------------------------------------------- 进度

def _anchor_of(row: dict) -> dict:
    """从 reader_state 的进度行取 Anchor。"""
    return {
        "spine": int(row.get("chapter") or 0),
        "off": int(row.get("offset") or 0),
    }


def get_progress(key: str) -> dict:
    row = _rs.get_book(key)
    if not row:
        raise S3Error("no_book", f"书不存在: {key}")
    prog = _rs.get_progress(key)
    local_pct = float(prog.get("percent") or 0.0)
    anchor = _anchor_of(prog)

    cloud_pct = local_pct
    source = "local"
    try:
        st = _sync.sync_state(key)
        cpct = st.get("cloud_pct")
        if cpct is not None:
            cloud_pct = float(cpct)
            if cloud_pct > local_pct:
                source = "cloud"
    except Exception:
        pass

    geo_meta = {}
    try:
        geo = geometry.load_geometry(row.get("path") or "", name=row.get("title") or "")
        geo_meta = {"total_count": geo.total_count}
    except Exception:
        pass

    return {
        "ok": True,
        "pct01": cloud_pct,
        "anchor": anchor,
        "source": source,
        "updated_at": _epoch(prog.get("updated_at")),
        "done": bool(prog.get("done")),
        "total_count": geo_meta.get("total_count", 0),
        "writer_lease": {"holder": "", "until": 0},
    }


def push_progress(payload: dict) -> dict:
    """设备上报进度 → 写本地 → 交给 sync 层判断能不能推云端。"""
    key = payload.get("book") or ""
    if not key:
        raise S3Error("bad_request", "缺 book")
    row = _rs.get_book(key)
    if not row:
        raise S3Error("no_book", f"书不存在: {key}")

    anchor = payload.get("anchor") or {}
    spine = int(anchor.get("spine") or 0)
    off = int(anchor.get("off") or 0)
    pct = float(payload.get("pct01") or 0.0)
    done = bool(payload.get("done"))
    user_driven = bool(payload.get("user_driven", True))
    reader_ts = int(payload.get("reader_ts") or time.time())

    # 1) 本地先落（可靠，不依赖网络）
    try:
        _rs.save_progress(
            key,
            chapter=spine,
            offset=off,
            percent=pct,
            finished=done,
        )
    except TypeError:
        _rs.save_progress(key, spine, off, pct, done)

    # 2) 交给 sync 层判断（容差 / cloud-newer / 只前进闸门都在那边）
    decision = "noop"
    why = "no-link"
    pushed = False
    cloud_pct = None
    try:
        out = _sync.push_once(keys=[key], allow_rewind=user_driven)
        for item in out.get("pushed", []) + out.get("skipped", []):
            if item.get("key") == key:
                decision = item.get("why") or item.get("decision") or "noop"
                why = item.get("reason") or why
                pushed = bool(item.get("pushed"))
                cloud_pct = item.get("cloud_pct")
    except Exception as exc:  # noqa: BLE001 — 云端挂了不该让设备收到 500
        why = f"error:{type(exc).__name__}"

    return {
        "ok": True,
        "decision": decision,
        "cloud_pct01": cloud_pct,
        "why": why,
        "pushed": pushed,
        "reader_ts": reader_ts,
    }


# ---------------------------------------------------------------- 笔记

def sync_notes(payload: dict) -> dict:
    key = payload.get("book") or ""
    if not key:
        raise S3Error("bad_request", "缺 book")
    row = _rs.get_book(key)
    if not row:
        raise S3Error("no_book", f"书不存在: {key}")

    notes = payload.get("notes") or []
    if len(notes) > LIMITS["notes_batch"]:
        raise S3Error("bad_request", f"一次最多 {LIMITS['notes_batch']} 条")

    applied = {"added": 0, "updated": 0, "unchanged": 0, "deleted": 0}
    rejected: list[dict] = []
    for n in notes:
        kind = "bookmark" if n.get("kind") == "mark" else "note"
        spine = int(n.get("spine") or 0)
        start = int(n.get("off_start") or 0)
        end = int(n.get("off_end") or 0)
        try:
            existing = _rs.find_note(key, spine, start, end)
            if existing:
                _rs.update_note(
                    int(existing["id"]),
                    remark=n.get("remark"),
                    summary=n.get("text"),
                )
                applied["updated"] += 1
            else:
                _rs.add_note(
                    key,
                    spine,
                    start,
                    end,
                    n.get("text") or "",
                    remark=n.get("remark") or "",
                    color=str(n.get("color") or ""),
                )
                applied["added"] += 1
        except Exception as exc:  # noqa: BLE001
            rejected.append({"uc": n.get("uc") or "", "why": f"{type(exc).__name__}"})

    deleted = 0
    for d in payload.get("deletes") or []:
        try:
            nid = int(d.get("id") or 0)
            if nid and _rs.del_note(nid):
                deleted += 1
        except Exception:
            pass
    applied["deleted"] = deleted

    identity = _identity_for(row.get("path") or "")
    return {
        "ok": True,
        "applied": applied,
        "rejected": rejected,
        "cursor": "",
        "cloud": {"notes": 0, "marks": 0},
        "identity": {"drawer": identity.get("notes_drawer"), "dual": identity.get("dual")},
    }


def list_notes(key: str) -> dict:
    row = _rs.get_book(key)
    if not row:
        raise S3Error("no_book", f"书不存在: {key}")
    out = []
    for n in _rs.list_notes(key):
        out.append(
            {
                "uc": n.get("uc") or "",
                "kind": "bookmark" if n.get("kind") == 1 else "note",
                "spine": int(n.get("chapter") or 0),
                "off_start": int(n.get("start_off") or 0),
                "off_end": int(n.get("end_off") or 0),
                "text": n.get("summary") or "",
                "remark": n.get("remark") or "",
                "marktime": int(n.get("marktime") or 0),
            }
        )
    return {"ok": True, "notes": out, "cursor": ""}


# ---------------------------------------------------------------- 时长

def push_sessions(payload: dict) -> dict:
    key = payload.get("book") or ""
    day = payload.get("day") or ""
    if not key or not day:
        raise S3Error("bad_request", "缺 book 或 day")
    b64 = payload.get("bitmap_b64") or ""
    minutes = int(payload.get("minutes") or 0)
    try:
        from datetime import date

        d = date.fromisoformat(day)
        marks = _rs.minutes_from_bitmap(b64)
        changed = _rs._mark_minutes(key, d, marks)
        merged = _rs.time_stats(key, days=1)
        return {
            "ok": True,
            "merged_minutes": merged.get("minutes", minutes),
            "changed": bool(changed),
        }
    except Exception as exc:  # noqa: BLE001
        raise S3Error("bad_request", f"{type(exc).__name__}: {exc}") from exc


def list_sessions(key: str, frm: str = "", to: str = "") -> dict:
    stats = _rs.time_stats(key or None, days=30)
    days = stats.get("days") or []
    return {"ok": True, "days": days}


# ---------------------------------------------------------------- 遥测

_TELEMETRY: list[dict] = []


def push_telemetry(payload: dict) -> dict:
    entries = payload.get("entries") or []
    _TELEMETRY.extend(entries)
    if len(_TELEMETRY) > 5000:
        del _TELEMETRY[: len(_TELEMETRY) - 5000]
    return {"ok": True, "accepted": len(entries)}


# ---------------------------------------------------------------- 路由

def handle(method: str, path: str, query: dict, body: dict | None,
           token: str) -> tuple[int, dict]:
    """把 `/s3/v1/*` 派发到具体实现。

    返回 `(http_status, json_obj)`。设备端只看 `ok` 字段。
    """
    p = urllib.parse.urlparse(path).path
    if not p.startswith(PREFIX):
        raise S3Error("bad_request", f"未知前缀: {p}", 404)
    rest = p[len(PREFIX):].strip("/")
    parts = [x for x in rest.split("/") if x]
    q = {k: (v[0] if isinstance(v, list) and v else v) for k, v in (query or {}).items()}

    # 握手与登记不需要令牌
    if parts == ["handshake"] and method == "POST":
        return 200, handshake(body or {})
    if parts == ["device", "register"] and method == "POST":
        return 200, register_device(
            (body or {}).get("code", ""), (body or {}).get("token", ""), body or {}
        )

    _require_device(token)

    if parts == ["books"] and method == "GET":
        return 200, list_books(
            limit=int(q.get("limit") or LIMITS["books_limit"]), cursor=q.get("cursor", "")
        )
    if len(parts) == 3 and parts[0] == "book" and parts[2] == "file" and method == "GET":
        raise S3Error("bad_request", "文件下载需 Range，见 /s3/v1/book/{key}/file", 501)
    if len(parts) == 3 and parts[0] == "book" and parts[2] == "geometry" and method == "GET":
        return 200, book_geometry(parts[1])
    if parts == ["progress"] and method == "GET":
        return 200, get_progress(q.get("book", ""))
    if parts == ["progress"] and method == "POST":
        return 200, push_progress(body or {})
    if parts == ["notes", "sync"] and method == "POST":
        return 200, sync_notes(body or {})
    if parts == ["notes"] and method == "GET":
        return 200, list_notes(q.get("book", ""))
    if parts == ["sessions"] and method == "POST":
        return 200, push_sessions(body or {})
    if parts == ["sessions"] and method == "GET":
        return 200, list_sessions(q.get("book", ""), q.get("from", ""), q.get("to", ""))
    if parts == ["telemetry"] and method == "POST":
        return 200, push_telemetry(body or {})
    if parts == ["pet", "brief"] and method == "GET":
        try:
            from recto.pet import brief

            return 200, {"ok": True, "brief": brief.digest()}
        except Exception:
            return 200, {"ok": True, "brief": None}

    raise S3Error("bad_request", f"未知端点: {method} /{rest}", 404)
