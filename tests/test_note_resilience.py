"""划线/笔记保存的健壮性 —— 「fetch failed」这一类网络级失败。

跑法: cd <仓库根> && PYTHONPATH=src python3 tests/test_note_resilience.py

背景: 读者在公网 (隧道) 上写「划线 + 笔记」时偶发 "Failed to fetch" —— 这是
**请求没走完** (连接被中间层掐断), 服务端可能收到了也可能没收到。带 body 的
POST 浏览器不会自动重试, 于是只能由前端自己重发。重发要安全, 所以:

  * 服务端 /note 按 (书 + 章 + 起止字节) **幂等** —— 同一段重复提交只留一行,
    第二次起返回 `dupe: true`
  * 先「只划线」、后「划线 + 笔记」落在**同一行**, 批注补上去
  * 前端 apiRetry: 网络级失败 (TypeError) 才重试一次, 其余错误照旧往外抛;
    最后一次仍失败就上报 /read/api/client-error, 服务端落日志 (下次有据可查)

验的事:
  1. 服务层幂等 (同段两次 / 先线后注 / 不同段各自成行)
  2. HTTP: 第二次带 dupe 标记, 条数不变
  3. 上报端点: 200, 不校验登录名之外的东西, 不炸
  4. 前端契约: apiRetry / isNetErr / reportClientError / 只对网络错误重试 /
     saveNote 用 apiRetry / 按 id 去重 / 网络失败的提示语与 HTTP 失败不同
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

TMP = Path(tempfile.mkdtemp(prefix="recto-noteretry-"))
LIB = TMP / "library"
LIB.mkdir(parents=True, exist_ok=True)
os.environ["RECTO_DATA_DIR"] = str(TMP / "data")
os.environ["RECTO_LIBRARY_DIR"] = str(LIB)
os.environ["RECTO_CLOUD_AUTH_FILE"] = str(TMP / "auth.json")
os.environ["RECTO_CORPUS_DIR"] = str(TMP / "data" / "corpus")
os.environ["RECTO_AUTH_LOG"] = "off"
os.environ["RECTO_AUTH_PASSWORD"] = "note-pass"
os.environ.pop("RECTO_AUTH_DISABLE", None)
(TMP / "data").mkdir(parents=True, exist_ok=True)
(TMP / "auth.json").write_text(json.dumps({
    "usr": "i0000000000", "zysid": "test", "p29": "zye5b814",
    "p2": "104583", "p3": "101010010", "p4": "501603"}), "utf-8")

from recto.app import server                    # noqa: E402
from recto.services import reader_state as rs   # noqa: E402

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
                   '<manifest>'
                   '<item id="c0" href="c0.xhtml" media-type="application/xhtml+xml"/>'
                   '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
                   '</manifest>'
                   '<spine><itemref idref="c0"/><itemref idref="c1"/></spine></package>')
        # c0 = 卷首 (无正文); c1 = 正文 —— 云端 chapterId 0 落在 spine 第 2 篇
        z.writestr("OEBPS/c0.xhtml",
                   '<?xml version="1.0" encoding="utf-8"?>'
                   '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>cover</title>'
                   '</head><body><p>卷首语</p></body></html>')
        z.writestr("OEBPS/c1.xhtml",
                   '<?xml version="1.0" encoding="utf-8"?>'
                   '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>c</title>'
                   '</head><body><h2>第一章</h2>'
                   '<p class="calibre7">山雨欲来风满楼，正文正文正文字数够长才选得中。</p>'
                   '<p>灯火阑珊处，第二段正文也要够长才能划线。</p>'
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
    body = urllib.parse.urlencode({"username": "root", "password": "note-pass", "next": "/"})
    c.request("POST", "/login", body=body,
              headers={"Content-Type": "application/x-www-form-urlencoded"})
    r = c.getresponse()
    r.read()
    hd = dict(r.getheaders())
    c.close()
    return (hd.get("Set-Cookie") or "").split(";")[0]


print("== 1. 服务层: add_note 幂等 ==")
book = make_epub(LIB / "幂等测试书.epub", "幂等测试书")
rs.scan(force=True)
key = {b["title"]: b["key"] for b in rs.list_books()}["幂等测试书"]

a = rs.add_note(key, 0, 10, 40, "山雨欲来风满楼", "", "yellow", "{}",
                chapter_label="第一章", pct=1.0)
b = rs.add_note(key, 0, 10, 40, "山雨欲来风满楼", "", "yellow", "{}",
                chapter_label="第一章", pct=1.0)
ok("同一段划线两次 → 同一行", a["id"] == b["id"], f'{a["id"]} vs {b["id"]}')
ok("库里只有一条", len(rs.all_notes()) == 1, len(rs.all_notes()))

c2 = rs.add_note(key, 0, 10, 40, "山雨欲来风满楼", "后补的批注", "yellow", "{}")
ok("先划线后补笔记 → 同一行", c2["id"] == a["id"], f'{c2["id"]} vs {a["id"]}')
ok("批注补上了", c2.get("remark") == "后补的批注", c2.get("remark"))
ok("仍然只有一条", len(rs.all_notes()) == 1, len(rs.all_notes()))

c3 = rs.add_note(key, 0, 10, 40, "山雨欲来风满楼", "改了批注", "yellow", "{}")
ok("同段再带新批注 → 覆盖而非新增", c3["id"] == a["id"] and c3["remark"] == "改了批注",
   f'{c3["id"]} / {c3.get("remark")}')

d = rs.add_note(key, 0, 12, 44, "另一段", "", "yellow", "{}")
ok("不同起止 → 新的一行", d["id"] != a["id"], f'{d["id"]} vs {a["id"]}')
ok("现在两条", len(rs.all_notes()) == 2, len(rs.all_notes()))

e = rs.add_note(key, 0, 40, 10, "起点终点写反", "", "yellow", "{}")
ok("起止写反也归一到同一行", e["id"] == a["id"], f'{e["id"]} vs {a["id"]}')
ok("总数还是两条", len(rs.all_notes()) == 2, len(rs.all_notes()))

ok("find_note 找得到", (rs.find_note(key, 0, 10, 40) or {}).get("id") == a["id"])
ok("find_note 找不到就是 None", rs.find_note(key, 0, 999, 1200) is None)

print("== 2. HTTP: 重复提交返回 dupe 标记 ==")
cookie = login()
BODY = {"chapter": 0, "start": 100, "end": 160, "summary": "灯火阑珊处",
        "remark": "", "color": "yellow",
        "anchors": {"sb": 100, "sc": 0, "eb": 100, "ec": 30}}
st, j1, _ = req("/read/api/book/" + key + "/note", "POST", BODY, cookie)
ok("第一次 200", st == 200, st)
ok("第一次 dupe=false", j1.get("dupe") is False, j1.get("dupe"))
nid = (j1.get("note") or {}).get("id")

st, j2, _ = req("/read/api/book/" + key + "/note", "POST", BODY, cookie)
ok("重发 200", st == 200, st)
ok("重发 dupe=true", j2.get("dupe") is True, j2.get("dupe"))
ok("重发返回同一 id", (j2.get("note") or {}).get("id") == nid,
   f'{(j2.get("note") or {}).get("id")} vs {nid}')
ok("库里没有多一条", len([n for n in rs.all_notes() if n["book"] == key]) == 3,
   len([n for n in rs.all_notes() if n["book"] == key]))

BODY2 = dict(BODY, remark="补上的批注")
st, j3, _ = req("/read/api/book/" + key + "/note", "POST", BODY2, cookie)
ok("划线后补笔记 200", st == 200, st)
ok("补笔记仍是同一 id 且带批注",
   (j3.get("note") or {}).get("id") == nid and (j3.get("note") or {}).get("remark") == "补上的批注",
   json.dumps(j3.get("note") or {}, ensure_ascii=False)[:120])

print("== 3. 前端报错上报 ==")
st, j4, _ = req("/read/api/client-error", "POST",
                {"path": "/note", "msg": "TypeError: Failed to fetch", "tries": 2,
                 "ua": "probe", "online": 1}, cookie)
ok("上报返回 200", st == 200, st)
ok("上报 ok", (j4 or {}).get("ok") is True, j4)
st, _j, _ = req("/read/api/client-error", "POST", {}, cookie)
ok("空载荷也不炸", st == 200, st)

print("== 4. 前端契约 ==")
JS = (ROOT / "src/recto/app/ui/assets/reader.js").read_text("utf-8")
ok("有 isNetErr", "function isNetErr(" in JS)
ok("有 reportClientError", "function reportClientError(" in JS)
ok("有 apiRetry", "function apiRetry(" in JS)
ok("重试只对网络级失败", "if (!isNetErr(e) || tries <= 1)" in JS)
ok("重试间隔不是 0", "setTimeout(r, 500)" in JS)
ok("saveNote 走 apiRetry", "apiRetry('/note', {" in JS)
ok("保存后按 id 去重", "S.notes.filter(function (x) { return x.id !== r.note.id; })" in JS)
ok("网络失败提示与 HTTP 失败不同",
   "网络中断，划线没存上（已重试）" in JS and "保存失败: ' + e.message" in JS)
ok("重试次数是 2 (首发 + 一次重发)", "}, 2).then(function (r) {" in JS)
ok("上报用裸 fetch 防递归", "fetch(API + '/client-error'" in JS)
ok("书签新增也走 apiRetry", "apiRetry('/bookmark', { method: 'POST', body: { chapter:" in JS)
ok("删除划线也走 apiRetry", "apiRetry('/note', { method: 'POST', body: { action: 'delete'" in JS)

print("== 5. 云端正文字段: 切点落在标签里/半个字符上也不带残渣 ==")
from recto.services import notes as N   # noqa: E402

raw = N._chapter_raw(str(book), 0)
ok("拿到章节原始字节", bool(raw), len(raw or b""))
tag_at = (raw or b"").find(b'<p')
cls_at = (raw or b"").find(b'class="calibre7"')
ok("样本里有 class 标签", tag_at >= 0 and cls_at > tag_at, f"{tag_at}/{cls_at}")


def pos(off):
    return "1_0_0$##3-0-1-%d-0##$" % off


# ① 起点落在标签内部 (class="calibre7" 中间) → 跳过标签, 正文不带 xxx">
txt_tag = N.text_of_span(str(book), pos(cls_at + 6), pos(cls_at + 6))
ok("标签中间的偏移也能切出正文", "山雨" in txt_tag, txt_tag[:40])
ok("不带标签残渣", '">' not in txt_tag and "class=" not in txt_tag, repr(txt_tag[:60]))

# ② 起点落在某个汉字的第 2 个字节上 → 开头不该出现 U+FFFD
body_at = (raw or b"").find("山雨".encode("utf-8"))
ok("找到正文起点", body_at > 0, body_at)
txt_mid = N.text_of_span(str(book), pos(body_at + 1), pos(body_at + 1))
ok("半个字符的起点也不出替换字符", "\ufffd" not in txt_mid, repr(txt_mid[:40]))
ok("正文仍然完整开头", txt_mid.startswith("山雨"), repr(txt_mid[:20]))

print()
print("PASS %d  FAIL %d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
