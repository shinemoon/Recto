"""书架「删除书籍」 —— 服务层 + HTTP 路由 + 界面契约。

跑法: cd <仓库根> && PYTHONPATH=src python3 tests/test_shelf_delete.py

验的事:
  * 删书默认**搬到回收站** (`<data_dir>/trash/<时间戳>/`), 不是真删
  * `.recto.json` / `.keywords.json` 边车跟着书走, 不留在书库当孤儿
  * 只删 rbook 那一行: 进度 / 书签 / 划线 / 时长留在库里 (文件放回原位就还在)
  * 下载索引里的登记一并摘掉
  * 拒绝删书库目录之外的文件 (403), 不存在的书 404
  * 界面: 卡片有删除键 (不能嵌在 <a> 里) + 悬停/触屏可见的 CSS + 二次确认
全部离线。
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
import urllib.parse
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = Path(tempfile.mkdtemp(prefix="recto-shelfdel-"))
LIB = TMP / "library"
LIB.mkdir(parents=True, exist_ok=True)
os.environ["RECTO_DATA_DIR"] = str(TMP / "data")
os.environ["RECTO_LIBRARY_DIR"] = str(LIB)
os.environ["RECTO_CLOUD_AUTH_FILE"] = str(TMP / "auth.json")
os.environ["RECTO_CORPUS_DIR"] = str(TMP / "data" / "corpus")
os.environ["RECTO_AUTH_PASSWORD"] = "del-pass"
os.environ.pop("RECTO_AUTH_DISABLE", None)
(TMP / "data").mkdir(parents=True, exist_ok=True)
(TMP / "auth.json").write_text(json.dumps({
    "usr": "i0000000000", "zysid": "test", "p29": "zye5b814",
    "p2": "104583", "p3": "101010010", "p4": "501603"}), "utf-8")

from recto.app import console, reader, server     # noqa: E402
from recto.core import bookfile, sidecar          # noqa: E402
from recto.services import acquisition            # noqa: E402
from recto.services import reader_state as rs     # noqa: E402

SRV = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
SRV.daemon_threads = True
threading.Thread(target=SRV.serve_forever, daemon=True).start()
PORT = SRV.server_address[1]

PASS = FAIL = 0


def ok(name, cond=True, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS", name)
    else:
        FAIL += 1
        print("FAIL", name, extra)


def make_epub(path: Path, title: str) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                   'media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf",
                   '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
                   'version="3.0" unique-identifier="id"><metadata '
                   'xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   '<dc:identifier id="id">x</dc:identifier>'
                   f'<dc:title>{title}</dc:title><dc:creator>某作者</dc:creator>'
                   '<dc:language>zh</dc:language></metadata>'
                   '<manifest><item id="c0" href="c0.xhtml" '
                   'media-type="application/xhtml+xml"/></manifest>'
                   '<spine><itemref idref="c0"/></spine></package>')
        z.writestr("OEBPS/c0.xhtml",
                   '<?xml version="1.0" encoding="utf-8"?>'
                   '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>c</title>'
                   '</head><body><h2>第一章</h2><p>山雨欲来风满楼，正文正文。</p>'
                   '</body></html>')
    return path


def req(path, method="GET", body=None, cookie=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=20)
    h, data = {}, None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    if cookie:
        h["Cookie"] = cookie
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    raw = r.read()
    hd = dict(r.getheaders())
    st = r.status
    c.close()
    try:
        j = json.loads(raw.decode("utf-8") or "{}")
    except Exception:  # noqa: BLE001
        j = {"_raw": raw[:300].decode("utf-8", "replace")}
    return st, j, hd


def login():
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=20)
    body = urllib.parse.urlencode({"username": "root", "password": "del-pass", "next": "/"})
    c.request("POST", "/login", body=body,
              headers={"Content-Type": "application/x-www-form-urlencoded"})
    r = c.getresponse()
    r.read()
    hd = dict(r.getheaders())
    c.close()
    return (hd.get("Set-Cookie") or "").split(";")[0]


print("== 1. 服务层: 搬家到回收站, 记录留存 ==")
book = make_epub(LIB / "要删的书.epub", "要删的书")
keep = make_epub(LIB / "要留的书.epub", "要留的书")
sidecar.set_cloud(book, id64="a" * 64, id32="A" * 32, name="要删的书")
sidecar.write(book, {"cloud": {"last_push_pct": 0.42}})
(book.parent / (book.stem + ".keywords.json")).write_text("{}", "utf-8")
rs.scan(force=True)
books = {b["title"]: b for b in rs.list_books()}
ok("扫描到两本", len(books) == 2, str(list(books)))
key = books["要删的书"]["key"]
rs.save_progress(key, 0, 10, 0.42, "1_0_0")
rs.add_note(key, 0, 4, 10, "山雨欲来", "批注", "", "{}")
rs.add_bookmark(key, 0, 6, "记一下", "第一章")

out = rs.delete_book(key)
ok("返回 ok", out.get("ok") is True)
ok("文件已搬走", not book.exists())
ok("回收站里能找到", bool(out.get("trash")) and Path(out["trash"]).exists())
ok("回收站保留层级 (<trash>/<ts>/<name>)",
   Path(out["trash"]).parent.name and Path(out["trash"]).name == book.name, out.get("trash"))
ok("边车跟着走", not sidecar.sidecar_path(book).exists())
ok("术语表也跟着走", not (book.parent / (book.stem + ".keywords.json")).exists())
ok("边车在回收站", Path(out["trash"]).with_name(book.stem + ".recto.json").exists())
ok("书架上没了", "要删的书" not in {b["title"] for b in rs.list_books()})
ok("另一本还在", "要留的书" in {b["title"] for b in rs.list_books()})
ok("rbook 行已删", rs.get_book(key) is None)
ok("进度还在 (放回原书可续读)", bool(rs.get_progress(key).get("percent")))
ok("划线还在", len(rs.list_notes(key)) == 1)
ok("书签还在", len(rs.list_bookmarks(key)) == 1)

print("== 2. 重复删 / 保留文件 / 越界 ==")
try:
    rs.delete_book(key)
    ok("重复删报 404 语义", False)
except FileNotFoundError:
    ok("重复删报 404 语义")

key2 = bookfile.book_key(keep)
out2 = rs.delete_book(key2, keep_file=True)
ok("keep_file 不搬文件", keep.exists() and out2["file_removed"] is False)
ok("keep_file 也从书架消失", rs.get_book(key2) is None)

outside = TMP / "outside.epub"
make_epub(outside, "库外的书")
ck = rs.conn()
ck.execute("""INSERT INTO rbook(key,path,kind,title,author,bytes,chapters,size,mtime,cover,added_at,seen_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
           ("deadbeefdeadbeef", str(outside), "epub", "库外的书", "", 0, 0, 1, 1,
            "", rs._now(), rs._now()))
ck.commit()
try:
    rs.delete_book("deadbeefdeadbeef")
    ok("拒绝删除书库外的文件", False)
except PermissionError:
    ok("拒绝删除书库外的文件")
ok("库外文件没被动", outside.exists())
ck.execute("DELETE FROM rbook WHERE key=?", ("deadbeefdeadbeef",))
ck.commit()

print("== 3. 下载索引一并摘掉 ==")
b3 = make_epub(LIB / "下载来的.epub", "下载来的")
rs.scan(force=True)
key3 = bookfile.book_key(b3)
acquisition._record("shelf:" + "f" * 64, {"path": str(b3), "name": b3.name})
ok("索引里有它", bool(acquisition._read_index().get("shelf:" + "f" * 64)))
out3 = rs.delete_book(key3)
ok("删书时摘掉索引", out3.get("index_removed") is True)
ok("索引已空", not acquisition._read_index().get("shelf:" + "f" * 64))
ok("forget_path 对没登记的文件返 False", acquisition.forget_path(str(TMP / "nope.epub")) is False)

print("== 4. HTTP: 登录后 POST /read/api/book/<key>/delete ==")
cookie = login()
ok("登录拿到 cookie", bool(cookie), cookie)
b4 = make_epub(LIB / "接口删的书.epub", "接口删的书")
rs.scan(force=True)
key4 = bookfile.book_key(b4)
st, j, _ = req(f"/read/api/book/{key4}/delete", "POST", {}, cookie)
ok("200", st == 200, f"{st} {j}")
ok("返回书名", j.get("title") == "接口删的书", str(j))
ok("文件搬走了", not b4.exists())
ok("书架查询里没有它", key4 not in {b["key"] for b in rs.list_books()})
st, j, _ = req(f"/read/api/book/{key4}/delete", "POST", {}, cookie)
ok("再删一次 404", st == 404 and "不在书库" in str(j.get("error")), f"{st} {j}")
st, j, _ = req("/read/api/book/ffffffffffffffff/delete", "POST", {}, cookie)
ok("未知 key 404", st == 404, f"{st} {j}")
st, j, _ = req("/read/api/book/" + key4 + "/delete", "POST")   # 没 cookie
ok("未登录被门挡住", st in (401, 403), f"{st} {j}")

print("== 5. 界面契约 ==")
page = console._page_html("root")
ok("卡片套了定位容器", 'class="bk-cell"' in page)
ok("有删除键", 'class="bk-del"' in page or "bk-del" in page)
ok("删除键带书 key", 'data-del="' in page)
ok("删除键不在 <a class=\"bk\" ...> 里面", '<a class="bk"' in page and
   page.index('class="bk-cell"') < page.index('data-del='))
ok("提示写了回收站", "data/trash" in page)
ok("二次确认 (confirm)", "window.confirm" in page and "从书架删除" in page)
ok("路线存在", "\"delete\"" in Path(reader.__file__).read_text("utf-8"))
css = (ROOT / "src" / "recto" / "app" / "ui" / "assets" / "recto.css").read_text("utf-8")
ok("CSS: .bk-cell 定位", ".bk-cell { position: relative" in css)
ok("CSS: 桌面 hover 才露出", ".bk-cell:hover .bk-del" in css)
ok("CSS: 触屏常显", "@media (hover: none)" in css and
   ".bk-del { opacity: 1; pointer-events: auto; }" in css)
ok("CSS: 悬停变红", "var(--rc-err)" in css.split(".bk-del {", 1)[1][:900])

print("== 6. 删完重扫不会复活 ==")
rs.scan(force=True)
ok("重扫后仍不在书架", key4 not in {b["key"] for b in rs.list_books()})
ok("回收站里那份还在", any(True for _ in (TMP / "data" / "trash").rglob("*.epub")))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
