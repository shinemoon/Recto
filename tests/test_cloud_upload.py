"""上传书本体到掌阅云 (cloudlink.upload) —— 全离线。

口径依据: pc.ireader.com 的 `/upload` 页 (chunk 36) 自己就在浏览器里做全套:
    authorize(GET) → OSS 预签名 PUT → bookshelf/upsert(type=1)
不需要 App 的原生签名, 所以这些分支都能用假 HTTP 验死。

跑法: cd <仓库根> && python3 tests/test_cloud_upload.py
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = Path(tempfile.mkdtemp())
os.environ["RECTO_DATA_DIR"] = str(TMP / "data")
os.environ["RECTO_LIBRARY_DIR"] = str(TMP / "library")
os.environ["RECTO_CLOUD_AUTH_FILE"] = str(TMP / "auth.json")
os.environ["RECTO_AUTH_DISABLE"] = "1"
(TMP / "library").mkdir(parents=True, exist_ok=True)
(TMP / "auth.json").write_text(json.dumps({
    "usr": "i0000000000", "zysid": "test", "p29": "zye5b814",
    "p2": "104583", "p3": "101010010", "p4": "501603"}), "utf-8")

from recto.core import sidecar  # noqa: E402
from recto.core.config import get_config  # noqa: E402
from recto.core import paths  # noqa: E402
from recto.services import acquisition as cb  # noqa: E402
from recto.services import cloudlink, reader_state, sync  # noqa: E402

get_config(reload=True)
paths.PATHS = paths.resolve()

real_put_object = cb.put_object     # 打桩前抓住原件, 用来验 PUT 的头
real_shelf_add = cb.shelf_add       # 同上: 验字段拼装

FAILED = []


def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILED.append(msg)


def make_epub(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                   'media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf",
                   '<?xml version="1.0"?><package version="2.0" '
                   'xmlns="http://www.idpf.org/2007/opf"><metadata/>'
                   '<manifest><item id="c1" href="a.html" media-type="application/xhtml+xml"/>'
                   '</manifest><spine><itemref idref="c1"/></spine></package>')
        z.writestr("OEBPS/a.html", "<html><body><p>甲</p></body></html>")


lib = TMP / "library"
book_e = lib / "要上传的书.epub"
book_t = lib / "笔记体.txt"
make_epub(book_e)
book_t.write_text("第一段\n第二段\n", encoding="utf-8")
reader_state.scan(force=True)
key_e = reader_state.book_key_by_path(str(book_e))
key_t = reader_state.book_key_by_path(str(book_t))
md5_e = sync.file_md5(book_e).upper()
md5_t = sync.file_md5(book_t).upper()

SEEN: dict[str, list] = {"api": [], "put": [], "shelf": [], "pull": [], "multi": []}


# ---------------------------------------------------------------- 假装云端
def fake_api(path, extra=None, **kw):
    SEEN["api"].append((path, dict(extra or {})))
    if path.endswith("/upload/authorize"):
        return {"code": 0, "msg": "ok", "body": {
            "type": 2, "sign_url": "https://oss.example/put",
            "callback": "cb64", "callback_var": "cv64", "bucket_name": "b"}}
    if path.endswith("/storage/file/info"):
        ids = [i for i in str((extra or {}).get("file_ids") or "").split(",") if i]
        return {"code": 0, "body": {i: {"object_name": f"book-prod/{i}.epub"}
                                    for i in ids if i == md5_e}}
    return {"code": 0, "body": {}}


def fake_put(url, data, *, callback="", callback_var="", md5_hex="", timeout=900):
    SEEN["put"].append({"url": url, "size": len(data), "md5": md5_hex,
                        "callback": callback, "callback_var": callback_var})
    return {"Status": "OK"}


def fake_shelf_add(entry):
    SEEN["shelf"].append(dict(entry))
    return {"code": 0, "book_id": entry["book_id"]}


def fake_pull(key, **kw):
    SEEN["pull"].append(key)
    return {"ok": True, "changed": True, "cloud_pct": 0.0}


def fake_multipart(path, fields, **kw):
    SEEN["multi"].append((path, dict(fields)))
    return {"code": 0, "msg": "ok"}


def fake_shelf_raw():
    return {"version": 42, "data": [{"order": 3}, {"order": -7}]}


cb._api = fake_api  # type: ignore[assignment]
cb.put_object = fake_put  # type: ignore[assignment]
cb.shelf_add = fake_shelf_add  # type: ignore[assignment]
cb.shelf_books = lambda *a, **kw: []  # type: ignore[assignment]
sync.pull_book = fake_pull  # type: ignore[assignment]
cloudlink._SHELF["at"] = 0.0
cloudlink._MD5.clear()

# ---------------------------------------------------------------- 常量表
ok(cb.BOOK_TYPE["epub"] == 5 and cb.BOOK_TYPE["pdf"] == 12 and cb.BOOK_TYPE["txt"] == 1,
   "book_type 与网页端一致 (epub=5 / pdf=12 / txt=1)")
ok("epub" in cb.UPLOAD_EXTS and "azw3" in cb.UPLOAD_EXTS, "支持的扩展名表")

# ---------------------------------------------------------------- authorize
cb.authorize_upload(md5_e, f"{md5_e}.epub", 123)
path, extra = SEEN["api"][-1]
ok(path == "/bookstore/open/cloud/storage/upload/authorize", "授权走 PC 网关那条路")
ok(extra == {"file_type": 1, "file_id": md5_e, "file_name": f"{md5_e}.epub",
             "file_size": 123}, "授权参数与网页端逐字相同 (GET query)")

# ---------------------------------------------------------------- PUT 头
_captured = {}


class FakeResp:
    def __init__(self, body: bytes):
        self._b = body

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(req, timeout=None):
    _captured["url"] = req.full_url
    _captured["method"] = req.get_method()
    _captured["headers"] = {k.lower(): v for k, v in req.header_items()}
    return FakeResp(b'{"Status":"OK"}')


real_urlopen = urllib.request.urlopen
urllib.request.urlopen = fake_urlopen  # type: ignore[assignment]
try:
    real_put_object("https://oss.example/put", b"xyz", callback="cb64",
                  callback_var="cv64", md5_hex=md5_t)
finally:
    urllib.request.urlopen = real_urlopen  # type: ignore[assignment]
want_md5 = base64.b64encode(bytes.fromhex(md5_t)).decode()
ok(_captured["method"] == "PUT", "PUT 而不是 POST")
ok(_captured["headers"].get("content-md5") == want_md5,
   "Content-MD5 = base64(MD5 原始 16 字节) 而不是 hex 串")
ok(_captured["headers"].get("content-type") == "application/octet-stream", "Content-Type 对")
ok(_captured["headers"].get("x-oss-callback") == "cb64", "带 X-Oss-Callback (服务端据此转 book-prod)")
ok(_captured["headers"].get("x-oss-callback-var") == "cv64", "带 X-Oss-Callback-var")
ok(_captured["url"] == "https://oss.example/put", "URL 用云端给的 sign_url")

# ---------------------------------------------------------------- file/info 批量
got = cb.file_present([md5_e, md5_t, md5_e, ""])
ok(list(got) == [md5_e], "批量问 file/info 只回存在的那些 (去重, 空串忽略)")
ok(SEEN["api"][-1][1]["file_ids"] == f"{md5_e},{md5_t}",
   "一次请求问一批 (逗号分隔)")

# ---------------------------------------------------------------- shelf_add 字段
cb._multipart = fake_multipart  # type: ignore[assignment]
cb.shelf_raw = fake_shelf_raw  # type: ignore[assignment]
real_shelf_add({"book_id": "BID", "file_id": md5_t, "book_name": "笔记体",
                "book_type": 1, "size": 20})
p, fields = SEEN["multi"][-1]
ok(p == "/bookstore/open/cloud/bookshelf/upsert", "加书架走 upsert")
ok(fields["type"] == 1 and str(fields["current_version"]) == "42", "type=1 新增 + 带乐观锁版本")
ok(fields["usr"] == "i0000000000", "带 usr")
rec = json.loads(fields["data"])[0]
ok(rec["dtype"] == 0 and rec["is_local_book"] is True, "dtype=书 + is_local_book=True")
ok(rec["book_file_id"] == md5_t and rec["book_file_size"] == 20, "book_file_id = 文件 MD5")
ok(rec["book_type"] == 1, "book_type=txt→1")
ok(rec["order"] == -8, "order 取现有最小值再往下排 (越小越靠前)")
ok(rec["folder_hierarchy"] == "[]", "folder_hierarchy 空数组串")

# ---------------------------------------------------------------- 正常上传
SEEN["put"].clear(); SEEN["shelf"].clear(); SEEN["api"].clear(); SEEN["pull"].clear()
res = cloudlink.upload(key_t)
want_book_id = md5_t + hashlib.md5("笔记体".encode()).hexdigest().upper()
ok(res["ok"] and res["state"] == "uploaded", "upload 成功 (state=uploaded)")
ok(SEEN["put"] and SEEN["put"][-1]["md5"] == md5_t, "PUT 带文件内容 MD5")
ok(SEEN["put"][-1]["size"] == book_t.stat().st_size, "PUT 的是文件全部字节")
ok(SEEN["put"][-1]["callback"] == "cb64", "回调透传")
ok(SEEN["shelf"] and SEEN["shelf"][-1]["book_id"] == want_book_id,
   "book_id = MD5(文件) + MD5(book_name) (与网页端一致)")
ok(SEEN["shelf"][-1]["book_name"] == "笔记体",
   "book_name 用清理过的书名 (不是带 z-library 后缀的文件名)")
ok(SEEN["shelf"][-1]["book_type"] == 1, "txt 用 book_type=1")
link = sidecar.cloud_link(str(book_t)) or {}
ok(link.get("id32") == md5_t and link.get("id64") == want_book_id, "边车写入 id32 + id64")
ok(link.get("identity") == "file-md5", "身份来源可追溯")
ok(SEEN["pull"] and SEEN["pull"][-1] == key_t, "上传后立刻拉一次进度")

# ---------------------------------------------------------------- 已上传过 (200004)
def api_exists(path, extra=None, **kw):
    if path.endswith("/upload/authorize"):
        return {"code": 200004, "msg": "file exist error", "body": None}
    return fake_api(path, extra, **kw)


cb._api = api_exists  # type: ignore[assignment]
SEEN["put"].clear(); SEEN["shelf"].clear()
cloudlink._MD5.clear()
res2 = cloudlink.upload(key_t)
ok(res2["state"] == "exists", "200004 ⇒ 认作已上传 (state=exists)")
ok(not SEEN["put"], "已上传就不再 PUT (省掉整本上传)")
ok(SEEN["shelf"] and SEEN["shelf"][-1]["book_id"] == want_book_id, "但仍然补一次云书架条目")

# ---------------------------------------------------------------- 已在云书架
cb.shelf_books = lambda *a, **kw: [{"id": want_book_id, "name": "笔记体"}]  # type: ignore[assignment]
cloudlink._SHELF["at"] = 0.0
SEEN["api"].clear(); SEEN["shelf"].clear()
res3 = cloudlink.upload(key_t)
ok(res3.get("skipped") == "已经在云书架里", "已在云书架 ⇒ 不重复上传")
ok(not SEEN["api"], "连授权都不问")

cb.shelf_books = lambda *a, **kw: []  # type: ignore[assignment]
cloudlink._SHELF["at"] = 0.0
cb._api = fake_api  # type: ignore[assignment]

# ---------------------------------------------------------------- 分片 / 坏格式
def api_split(path, extra=None, **kw):
    return {"code": 0, "body": {"type": 1, "access_key_id": "k"}}


cb._api = api_split  # type: ignore[assignment]
cloudlink._MD5.clear()
try:
    cloudlink.upload(key_t)
    ok(False, "STS 分片应当明确报错")
except ValueError as e:
    ok("分片" in str(e), "STS 分片路径明确报错 (不假装成功)")

bad = lib / "书.cbz"
bad.write_bytes(b"PK\x03\x04")
_real_row = cloudlink._row
cloudlink._row = lambda k: {"path": str(bad), "title": "书"}  # type: ignore[assignment]
try:
    cloudlink.upload("any-key")
    ok(False, "不支持的格式应当报错")
except ValueError as e:
    ok("不收" in str(e), "不支持的扩展名明确报错")
finally:
    cloudlink._row = _real_row  # type: ignore[assignment]

try:
    cloudlink.upload("no-such-book")
    ok(False, "未知 book key 应当报错")
except ValueError:
    ok(True, "未知 book key 报错")

# ---------------------------------------------------------------- 任务槽
cb._api = fake_api  # type: ignore[assignment]
cloudlink._MD5.clear()
jid = cb.job_begin("upload", "测试上传", total=2)
cloudlink._upload_worker(jid, [key_t, "no-such-book"])
job = cb.job_status(jid) or {}
res = job.get("result") or {}
ok(job.get("state") == "done", "一本成功一本失败 ⇒ 任务仍算 done")
ok(res.get("ok") == 1 and res.get("failed") == 1, "任务统计: 成功 1 失败 1")
rows = {r["key"]: r for r in res.get("rows") or []}
ok(rows[key_t]["ok"] is True, "成功那本带 book_id")
ok(rows["no-such-book"]["ok"] is False and "error" in rows["no-such-book"],
   "失败那本带原因, 不吞错")
ok(job.get("note", "").startswith("上传完成"), "任务留一句人话")
ok(job.get("finished"), "记完成时间")

jid2 = cb.job_begin("upload", "全失败", total=1)
cloudlink._upload_worker(jid2, ["no-such-book"])
job2 = cb.job_status(jid2) or {}
ok(job2.get("state") == "error" and job2.get("error"), "全部失败 ⇒ 任务 error 带原因")

# ---------------------------------------------------------------- items / summary
cb.file_present = lambda ids: {md5_e: {"object_name": "x"}}  # type: ignore[assignment]
rows = {r["key"]: r for r in cloudlink.items(refresh=True)}
ok(rows[key_e]["uploaded"] is True, "items 标出文件已在云 (批量 file/info)")
ok(rows[key_e]["attached"] is False, "没接入的仍是未接入 (上传与接入是两件事)")
ok(rows[key_t]["in_shelf"] is True, "上传过的那本留在列表里, 标「在云书架」")
s = cloudlink.summary(refresh=True)
ok(s["uploaded"] == 1, "summary 计数 uploaded")
ok(s["uploadable"] == sum(1 for r in rows.values() if not r["in_shelf"]),
   "summary 计数 uploadable = 面板里还没进云书架的那些")
ok(s["total"] == len(rows) and s["total"] >= 1, "summary 的 total 与 items 一致")

# 空书架不能被当成"沿用旧缓存" (曾经: 清空云端后本地还一直以为有)
cloudlink._SHELF["rows"] = [{"id": "STALE" + "0" * 27}]
cloudlink._SHELF["at"] = 0.0
cb.shelf_books = lambda *a, **kw: []  # type: ignore[assignment]
ok(cloudlink.shelf_by_md5(True) == {}, "云端返回空书架 ⇒ 缓存清空 (不沿用陈旧条目)")

# ---------------------------------------------------------------- upload_all 的挑选口径
# 2026-11-13: 面板不再列清单, 只剩一个按钮 —— 所以"该传哪些"必须由这里判干净:
#   在云书架 + 已有身份 => 跳过; 在云书架但没身份 => 还要走一遍 (只补身份, 不重传文件)
SEEN["put"].clear(); SEEN["shelf"].clear(); SEEN["api"].clear(); SEEN["pull"].clear()
cb.shelf_books = lambda *a, **kw: [{"id": want_book_id, "name": "笔记体"}]  # type: ignore[assignment]
cloudlink._SHELF["at"] = 0.0
cloudlink._MD5.clear()
res_all = cloudlink.upload_all()
ok(res_all["skipped"] == 1, f"已在云书架且已有身份的书被跳过 ({res_all['skipped']})")
ok(res_all["uploaded"] == 1, f"不在云书架的那本仍会传 ({res_all['uploaded']})")
ok(all(p["md5"] != md5_t for p in SEEN["put"]), "被跳过的那本一个字节都不重传")
ok([p["md5"] for p in SEEN["put"]] == [md5_e], f"只传了缺的那本 ({len(SEEN['put'])})")

# 在云书架但边车没身份 => 只补身份, 不重传文件
sidecar.touch_cloud(str(book_e), id32="", id64="", identity="", attached_at="")
SEEN["put"].clear(); SEEN["api"].clear()
cb.shelf_books = lambda *a, **kw: [{"id": md5_e + "0" * 32, "name": "要上传的书"}]  # type: ignore[assignment]
cloudlink._SHELF["at"] = 0.0
cloudlink._MD5.clear()
res_all1 = cloudlink.upload_all()
link_e = sidecar.cloud_link(str(book_e)) or {}
ok(res_all1["skipped"] == 1, f"只有已就绪的那本被跳过 ({res_all1['skipped']})")
ok(not SEEN["put"], "在云书架但缺身份的书不重传文件")
ok(link_e.get("id32") == md5_e, "补上了云端身份 (id32 = 文件 MD5)")

# 都不在云书架 => 一个都不跳
sidecar.touch_cloud(str(book_e), id32="", id64="", identity="", attached_at="")
cb.shelf_books = lambda *a, **kw: []  # type: ignore[assignment]
cloudlink._SHELF["at"] = 0.0
cloudlink._MD5.clear()
res_all2 = cloudlink.upload_all()
ok(any(r["key"] == key_e for r in res_all2["detail"]),
   "没有云端身份的书一定进上传清单 (不会被当成已就绪)")
ok(res_all2["skipped"] == 1,
   "另一本边车还留着 id64 ⇒ 仍算在云书架 (跳过是对的)")

print()
print("FAILED:", len(FAILED))
for f in FAILED:
    print("  -", f)
sys.exit(1 if FAILED else 0)
