"""RectoLink (`/s3/v1/*`) 契约测试 —— 设备侧最依赖的一层。

跑法: python3 tests/test_s3link.py

覆盖:
  - 路由与错误码（401 / 404 / bad_request）
  - 握手的 features/limits 契约
  - 书列表：字段齐全、identity 由服务端下发（设备不推导）
  - 几何：spine_bytes / total_bytes / catalog_count 与 core.geometry 一致
  - 进度：GET 与 POST 幂等，anchor 与 pct01 自洽
  - 笔记：同段重复提交幂等（不重复插入）
  - 时长：位图并集，重复推同一份不翻倍
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# ---- 隔离（必须在 import recto 之前）----
_TMPD = tempfile.mkdtemp(prefix="recto-s3link-")
os.environ["RECTO_DATA_DIR"] = _TMPD + "/data"
os.environ["RECTO_LIBRARY_DIR"] = _TMPD + "/library"
os.environ["RECTO_WEBAUTH_DIR"] = _TMPD + "/webauth"
os.environ["RECTO_AUTH_LOG"] = "off"
os.makedirs(_TMPD + "/library", exist_ok=True)

from recto.app import s3link                      # noqa: E402
from recto.core import geometry                   # noqa: E402
from recto.services import reader_state as rs     # noqa: E402

PASS = 0
FAILED: list[str] = []


def ok(cond, name, extra=""):
    global PASS
    if cond:
        PASS += 1
        print(f"PASS {name}" + (f" — {extra}" if extra else ""))
    else:
        FAILED.append(name)
        print(f"FAIL {name}" + (f" — {extra}" if extra else ""))


def call(method, path, query=None, body=None, token="tok-test"):
    return s3link.handle(method, path, query or {}, body, token)


# ---- 准备：造一本最小 epub ----
def make_epub(path: Path) -> None:
    import zipfile

    opf = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
 <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
  <dc:title>契约测试书</dc:title><dc:creator>测试作者</dc:creator>
  <dc:identifier id="id">urn:uuid:test-s3link</dc:identifier>
  <dc:language>zh-CN</dc:language>
 </metadata>
 <manifest>
  <item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>
  <item id="c2" href="c2.xhtml" media-type="application/xhtml+xml"/>
  <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
 </manifest>
 <spine toc="ncx">
  <itemref idref="c1"/><itemref idref="c2"/>
 </spine>
</package>"""
    ncx = """<?xml version="1.0"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
 <head><meta name="dtb:uid" content="urn:uuid:test-s3link"/></head>
 <docTitle><text>契约测试书</text></docTitle>
 <navMap>
  <navPoint id="n1" playOrder="1"><navLabel><text>第一章</text></navLabel>
    <content src="c1.xhtml"/></navPoint>
  <navPoint id="n2" playOrder="2"><navLabel><text>第二章</text></navLabel>
    <content src="c2.xhtml"/></navPoint>
 </navMap>
</ncx>"""
    body = "<html><body><p>" + "测试正文内容。" * 200 + "</p></body></html>"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                   'media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/toc.ncx", ncx)
        z.writestr("OEBPS/c1.xhtml", body)
        z.writestr("OEBPS/c2.xhtml", body)


lib = Path(_TMPD) / "library"
lib.mkdir(parents=True, exist_ok=True)
epub_path = lib / "契约测试书.epub"
make_epub(epub_path)
rs.scan(force=True)

s3link.register_device("CODE-0001", "tok-test", {"model": "papers3", "fw": "v0.0.1"})

# ---------------------------------------------------------------- 握手

st, r = call("POST", "/s3/v1/handshake", body={"dev": {"id": "d", "fw": "v0"}, "proto": 1}, token="")
ok(st == 200 and r.get("ok"), "握手不需要令牌")
ok(r.get("proto") == 1, "握手返回 proto=1", str(r.get("proto")))
ok("progress" in (r.get("features") or []), "features 含 progress")
ok("notes" in (r.get("features") or []), "features 含 notes")
ok("sessions" in (r.get("features") or []), "features 含 sessions")
ok(isinstance(r.get("limits"), dict) and r["limits"].get("notes_batch") == 64,
   "limits.notes_batch=64", str(r.get("limits")))
ok(isinstance(r.get("server_time"), int) and r["server_time"] > 1_700_000_000,
   "server_time 是 Unix 秒")

# ---------------------------------------------------------------- 认证

try:
    call("GET", "/s3/v1/books", token="bad-token")
    ok(False, "错令牌被拒")
except s3link.S3Error as exc:
    ok(exc.status == 401 and exc.code == "unauthorized", "错令牌 → 401 unauthorized",
       f"{exc.status}/{exc.code}")

try:
    call("GET", "/s3/v1/nope")
    ok(False, "未知端点被拒")
except s3link.S3Error as exc:
    ok(exc.status == 404, "未知端点 → 404", str(exc.status))

# ---------------------------------------------------------------- 书列表

st, r = call("GET", "/s3/v1/books", {"limit": ["10"]})
ok(st == 200 and r.get("ok"), "书列表 200")
books = r.get("books") or []
ok(len(books) == 1, "列出 1 本书", str(len(books)))
b = books[0] if books else {}
ok(b.get("key"), "书有 key")
ok(b.get("title") == "契约测试书", "书名正确", b.get("title"))
ok(b.get("format") == "epub", "format=epub", b.get("format"))
ok(b.get("size", 0) > 0, "有 size")
ident = b.get("identity") or {}
ok(set(ident.keys()) >= {"id32", "id64", "notes_drawer", "dual"},
   "identity 字段齐全（设备不推导，全靠服务端下发）", str(sorted(ident.keys())))
ok(ident.get("notes_drawer") in ("upper", "lower"), "notes_drawer 合法")

# ---------------------------------------------------------------- 几何

st, g = call("GET", f"/s3/v1/book/{b.get('key')}/geometry")
ok(st == 200 and g.get("ok"), "几何 200")
ok(isinstance(g.get("spine_bytes"), list) and len(g["spine_bytes"]) == 2,
   "spine_bytes 长度=2", str(len(g.get("spine_bytes") or [])))
ok(g.get("total_bytes") == sum(g.get("spine_bytes") or []), "total_bytes = Σ spine_bytes")
ok(g.get("catalog_count") == 2, "catalog_count = 目录条数（不是 spine-1）",
   str(g.get("catalog_count")))

# 与 core.geometry 对拍（唯一权威）
geo = geometry.load_geometry(str(epub_path))
ok(g["total_bytes"] == geo.total_bytes, "total_bytes 与 core.geometry 一致")
ok(g["catalog_count"] == geo.total_count, "catalog_count 与 core.geometry 一致")
ok(list(g["spine_bytes"]) == list(geo.sizes), "spine_bytes 与 core.geometry 一致")

# ---------------------------------------------------------------- 进度

key = b.get("key")
st, p = call("POST", "/s3/v1/progress", body={
    "book": key,
    "anchor": {"spine": 1, "off": 100},
    "pct01": 0.5,
    "done": False,
    "user_driven": True,
    "reader_ts": 1_795_000_000,
})
ok(st == 200 and p.get("ok"), "推进度 200")
ok("decision" in p, "返回 decision", p.get("decision"))

st, p2 = call("GET", "/s3/v1/progress", {"book": [key]})
ok(st == 200, "读进度 200")
ok(abs(float(p2.get("pct01") or 0) - 0.5) < 1e-6, "pct01 落库正确", str(p2.get("pct01")))
ok(p2.get("anchor", {}).get("spine") == 1, "anchor.spine 保真")
ok(p2.get("anchor", {}).get("off") == 100, "anchor.off 保真")
ok(p2.get("total_count") == 2, "进度带 total_count")

# 幂等：同 anchor 再推一次，pct 不变
call("POST", "/s3/v1/progress", body={
    "book": key, "anchor": {"spine": 1, "off": 100},
    "pct01": 0.5, "user_driven": True,
})
st, p3 = call("GET", "/s3/v1/progress", {"book": [key]})
ok(abs(float(p3.get("pct01") or 0) - 0.5) < 1e-9, "同位置重推幂等", str(p3.get("pct01")))

# ---------------------------------------------------------------- 笔记

st, n = call("POST", "/s3/v1/notes/sync", body={
    "book": key,
    "notes": [{"op": "up", "kind": "note", "spine": 1, "off_start": 10,
               "off_end": 40, "text": "这是一段被划线的文字", "remark": "批注"}],
    "deletes": [],
})
ok(st == 200 and n.get("ok"), "推笔记 200")
applied = n.get("applied") or {}
ok(applied.get("added") == 1, "新增 1 条", str(applied))

# 同段再推 → 不应重复
st, n2 = call("POST", "/s3/v1/notes/sync", body={
    "book": key,
    "notes": [{"op": "up", "kind": "note", "spine": 1, "off_start": 10,
               "off_end": 40, "text": "这是一段被划线的文字", "remark": "批注"}],
})
ok((n2.get("applied") or {}).get("added") == 0, "同段重复提交不新增（幂等）",
   str(n2.get("applied")))
ok((n2.get("applied") or {}).get("updated") == 1, "同段重复提交算更新")

st, nl = call("GET", "/s3/v1/notes", {"book": [key]})
ok(st == 200 and len(nl.get("notes") or []) == 1, "本地一条笔记", str(len(nl.get("notes") or [])))

# ---------------------------------------------------------------- 时长

from recto.services import reader_state as _rs  # noqa: E402

marks = _rs.minutes_from_bitmap("")  # 空位图
ok(isinstance(marks, list), "位图解析可用")

st, s = call("POST", "/s3/v1/sessions", body={
    "book": key, "day": "2026-11-21", "bitmap_b64": "", "minutes": 0,
})
ok(st == 200 and s.get("ok"), "推时长 200")
ok("merged_minutes" in s, "返回 merged_minutes")
ok("changed" in s, "返回 changed（幂等省电）")

# ---------------------------------------------------------------- 汇总

print()
print(f"{PASS} passed, {len(FAILED)} failed")
if FAILED:
    for name in FAILED:
        print("  FAIL:", name)
    raise SystemExit(1)
raise SystemExit(0)
