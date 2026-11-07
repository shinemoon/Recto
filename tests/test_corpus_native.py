"""语料分区 (原生) —— 真起 ThreadingHTTPServer 的端到端测试。

跑法: cd <仓库根> && PYTHONPATH=src python3 tests/test_corpus_native.py

验的事:
  * 页面: 同一套外壳 (顶栏 / 导航 / 皮肤), 自带 corpus.css / corpus.js, 没有上游 masthead
  * 资产: /corpus/static/* 白名单
  * 接口: config (脱敏 key 不覆盖真 key) / models / epubs / parse / books / exports /
          task / keywords / analyze 的守卫 / DELETE 方法 / 405 / 404
  * 登录门: 未登录 303 或 401
  * 结构性: 本分区不依赖 Flask, 导出目录 == 语料根 (阅读高亮直接扫那一层)

全部离线: 不调用真实 LLM, 不碰云端账号。
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = Path(tempfile.mkdtemp(prefix="recto-corpus-"))
os.environ["RECTO_DATA_DIR"] = str(TMP / "data")
os.environ["RECTO_LIBRARY_DIR"] = str(TMP / "library")
os.environ["RECTO_CLOUD_AUTH_FILE"] = str(TMP / "auth.json")
# 语料根也要隔离: config.toml.local 里写的是绝对路径, 不隔离就会写进生产语料目录
os.environ["RECTO_CORPUS_DIR"] = str(TMP / "data" / "corpus")
os.environ["RECTO_AUTH_LOG"] = "off"   # 测试不写生产留痕 (data/logs/webauth.log)
os.environ["RECTO_AUTH_PASSWORD"] = "corpus-pass"
os.environ.pop("RECTO_AUTH_DISABLE", None)          # 要测登录门
os.environ.pop("RECTO_AUTH_USER", None)
(TMP / "library").mkdir(parents=True, exist_ok=True)
(TMP / "auth.json").write_text(json.dumps({
    "usr": "i0000000000", "zysid": "test", "p29": "zye5b814",
    "p2": "104583", "p3": "101010010", "p4": "501603"}), "utf-8")

from recto.app import server  # noqa: E402
from recto.core import paths  # noqa: E402
from recto.corpus import config as cconf  # noqa: E402
from recto.corpus import store  # noqa: E402

SRV = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
SRV.daemon_threads = True
threading.Thread(target=SRV.serve_forever, daemon=True).start()
PORT = SRV.server_address[1]

PASS = FAIL = 0


def ok(cond, name):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS", name)
    else:
        FAIL += 1
        print("FAIL", name)


def req(path, method="GET", body=None, cookie=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
    h = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    if cookie:
        h["Cookie"] = cookie
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    raw = r.read()
    hd = dict(r.getheaders())
    c.close()
    return r.status, hd, raw


def jreq(path, method="GET", body=None, cookie=None):
    st, hd, raw = req(path, method, body, cookie)
    try:
        return st, json.loads(raw.decode("utf-8") or "{}"), hd
    except Exception:  # noqa: BLE001
        return st, {"_raw": raw[:200].decode("utf-8", "replace")}, hd


def login():
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=20)
    import urllib.parse

    body = urllib.parse.urlencode({"username": "root", "password": "corpus-pass", "next": "/"})
    c.request("POST", "/login", body=body,
              headers={"Content-Type": "application/x-www-form-urlencoded"})
    r = c.getresponse()
    r.read()
    hd = dict(r.getheaders())
    c.close()
    return (hd.get("Set-Cookie") or "").split(";")[0]


def make_epub(path: Path, texts=("第一章 山雨 ABC", "第二章 灯火 DEF")) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                   'media-type="application/oebps-package+xml"/></rootfiles></container>')
        items = "".join(
            f'<item id="c{i}" href="c{i}.xhtml" media-type="application/xhtml+xml"/>'
            for i in range(len(texts)))
        refs = "".join(f'<itemref idref="c{i}"/>' for i in range(len(texts)))
        z.writestr("OEBPS/content.opf",
                   '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
                   'version="3.0" unique-identifier="id"><metadata '
                   'xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   '<dc:identifier id="id">x</dc:identifier>'
                   '<dc:title>语料测试书</dc:title><dc:creator>某作者</dc:creator>'
                   '<dc:language>zh</dc:language></metadata>'
                   f'<manifest>{items}</manifest><spine>{refs}</spine></package>')
        for i, t in enumerate(texts):
            z.writestr(f"OEBPS/c{i}.xhtml",
                       '<?xml version="1.0" encoding="utf-8"?>'
                       '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>c</title>'
                       f'</head><body><h2>第{i + 1}章</h2><p>{t}</p></body></html>')
    return path


# ---------------------------------------------------------------- 结构性
def test_no_flask():
    ok("flask" not in sys.modules, "不依赖 Flask (整个分区是 stdlib)")
    ok(cconf.EXPORT_DIR == paths.PATHS.corpus_dir,
       "固定导出目录 == 语料根 (阅读高亮直接扫这一层)")
    ok(cconf.OUTPUT_DIR == paths.PATHS.corpus_dir / "outputs", "中间产物在 语料根/outputs")
    ok(store.STATE_PATH == paths.PATHS.corpus_dir / "state.json", "状态文件在语料根")


def test_gate():
    st, hd, _ = req("/corpus/")
    ok(st == 303 and "/login" in hd.get("Location", ""), "未登录 GET /corpus/ → 303 登录页")
    st, body, _ = jreq("/corpus/api/config")
    ok(st == 401 and body.get("login") == "/login", "未登录 API → 401 + login 提示")
    st, hd, _ = req("/corpus/static/corpus.css")
    ok(st in (200, 303), "资产在登录门前后的行为不崩 (实际 %s)" % st)


def test_page(cookie):
    st, hd, raw = req("/corpus/", cookie=cookie)
    html = raw.decode("utf-8")
    ok(st == 200, "GET /corpus/ 200")
    ok(hd.get("Content-Type", "").startswith("text/html"), "页面是 text/html")
    ok("rc-top" in html and "rc-brand" in html, "用的是同一套外壳 (顶栏)")
    ok('href="/corpus/"' in html, "导航里「语料」是当前分区")
    ok("/corpus/static/corpus.css" in html, "引用语料自己的 CSS")
    ok("/corpus/static/corpus.js" in html, "引用语料自己的 JS")
    ok("/assets/recto.css" in html and "/assets/skin.css" in html, "外壳样式仍在 (同皮)")
    ok('class="masthead"' not in html, "没有上游那套 masthead")
    ok("/static/style.css" not in html, "不再引用上游 /static/ 路径")
    ok('id="card-llm"' in html and 'id="card-history"' in html, "六张卡都在页面里")
    ok("<title>语料 · 云阅 Recto</title>" in html, "标题走品牌口径")


def test_assets(cookie):
    st, hd, raw = req("/corpus/static/corpus.css", cookie=cookie)
    ok(st == 200 and hd.get("Content-Type", "").startswith("text/css"), "corpus.css 200")
    ok(b"--rc-accent" in raw or b"var(--rc-" in raw, "CSS 用皮肤 token (换肤跟着变)")
    ok(b".rc-corpus" in raw, "CSS 作用域限定在语料区块")
    st, hd, raw = req("/corpus/static/corpus.js", cookie=cookie)
    ok(st == 200 and "javascript" in hd.get("Content-Type", ""), "corpus.js 200")
    ok(b"CORPUS_BASE" in raw, "JS 里有 /corpus 前缀常量")
    st, _, _ = req("/corpus/static/secret.txt", cookie=cookie)
    ok(st == 404, "资产白名单: 其它文件名 404")


def test_config(cookie):
    st, body, _ = jreq("/corpus/api/config", cookie=cookie)
    ok(st == 200 and "llm" in body, "GET /api/config 200")
    ok("*" in body["llm"].get("api_key", "") or body["llm"].get("api_key") == "",
       "api_key 是脱敏串")
    ok(body["llm"].get("has_key") is False, "初始没有 key")

    st, body, _ = jreq("/corpus/api/config", "POST",
                       {"llm": {"api_key": "sk-real-key-123456", "model": "agnes-2.5-flash",
                                "base_url": "https://api.example.com/v1"}}, cookie)
    ok(st == 200 and body["config"]["llm"]["has_key"], "POST 保存了真 key")
    masked = body["config"]["llm"]["api_key"]
    ok(masked.startswith("sk-rea") and "*" in masked, "回显仍是脱敏串")

    # 关键回归: 前端把**脱敏串**原样传回来时, 不能覆盖盘上的真 key
    st, body, _ = jreq("/corpus/api/config", "POST",
                       {"llm": {"api_key": masked, "model": "agnes-2.5-flash"}}, cookie)
    ok(st == 200, "重存 (脱敏串) 200")
    disk = json.loads((cconf.CONFIG_PATH).read_text("utf-8"))
    ok(disk["llm"]["api_key"] == "sk-real-key-123456", "脱敏串没有覆盖磁盘上的真 key")

    st, body, _ = jreq("/corpus/api/config", cookie=cookie)
    ok(body["config"]["llm"]["thinking"] in ("off", "on", "auto") if "config" in body
       else body["llm"].get("thinking") == "off", "thinking 默认 off (抽取不用推理)")


def test_models(cookie):
    st, body, _ = jreq("/corpus/api/models", cookie=cookie)
    ok(st == 200 and isinstance(body.get("models"), list), "GET /api/models 200")

    st, body, _ = jreq("/corpus/api/models", "POST", {"models": [
        {"name": "测试模型", "provider": "openai", "base_url": "https://a.example/v1",
         "model": "m-a", "api_key": "sk-aaaaaaaaaaaaaaaa", "note": "n1"},
    ]}, cookie)
    ok(st == 200 and len(body["models"]) == 1, "POST /api/models 新建一条")
    mid = body["models"][0]["id"]
    ok("*" in body["models"][0]["api_key"], "模型库 key 也是脱敏的")

    st, body, _ = jreq("/corpus/api/models/apply", "POST", {"id": mid}, cookie)
    ok(st == 200 and body["config"]["llm"]["model"] == "m-a", "apply 把条目套到 llm 段")
    ok(body["config"].get("active_model_id") == mid, "apply 记下生效项")

    st, body, _ = jreq("/corpus/api/models/%s/test" % mid, "POST", {}, cookie)
    ok(st in (200, 500) and ("ok" in body or "error" in body),
       "models/<id>/test 有结果 (离线端点 → 失败但结构对)")

    st, body, _ = jreq("/corpus/api/models/%s" % mid, "DELETE", None, cookie)
    ok(st == 200 and body["models"] == [], "DELETE /api/models/<id> 生效 (do_DELETE 通了)")
    st, body, _ = jreq("/corpus/api/models/nope", "DELETE", None, cookie)
    ok(st == 404, "删不存在的模型 → 404")


def test_epubs_and_parse(cookie):
    st, body, _ = jreq("/corpus/api/epubs/roots", cookie=cookie)
    ok(st == 200 and str(paths.PATHS.library_dir) in body["roots"],
       "默认扫描根 = 书库 (不要求手填路径)")

    epub = make_epub(TMP / "library" / "语料测试书.epub")
    st, body, _ = jreq("/corpus/api/epubs", cookie=cookie)
    ok(st == 200 and any(i["name"] == epub.name for i in body["items"]),
       "GET /api/epubs 扫到书库里的 epub")

    st, body, _ = jreq("/corpus/api/parse", "POST", {"path": str(epub)}, cookie)
    ok(st == 200 and body.get("book_id"), "POST /api/parse 解析成功")
    bid = body["book_id"]
    ok(body["meta"]["title"] == "语料测试书", "解析出书名")

    st, body, _ = jreq("/corpus/api/books", cookie=cookie)
    ok(st == 200 and any(b["id"] == bid for b in body["items"]), "书目列表里有这本")
    ok(body["exports"] == str(paths.PATHS.corpus_dir), "书目接口报的导出目录是语料根")

    st, body, _ = jreq("/corpus/api/book/%s" % bid, cookie=cookie)
    ok(st == 200 and body["meta"]["title"] == "语料测试书", "单书详情")

    st, body, _ = jreq("/corpus/api/task/%s" % bid, cookie=cookie)
    ok(st == 200 and body.get("book_id") == bid, "任务快照 (还没跑 → 无状态也不报错)")
    st, body, _ = jreq("/corpus/api/tasks", cookie=cookie)
    ok(st == 200 and isinstance(body.get("items"), list), "未收尾任务列表")

    st, body, _ = jreq("/corpus/api/keywords/%s" % bid, cookie=cookie)
    ok(st == 404, "还没有产物 → keywords 404")

    # 抽取守卫: 没有 key 时必须在起线程之前就拒掉
    cfg_now = cconf.load_config()
    saved_key = cfg_now["llm"].get("api_key", "")
    cfg_now["llm"]["api_key"] = ""
    cconf.save_config(cfg_now)
    st, body, _ = jreq("/corpus/api/analyze", "POST", {"book_id": bid}, cookie)
    ok(st == 400 and "Key" in (body.get("error") or ""), "没配 key → 400 (不起线程)")
    cfg_now = cconf.load_config()
    cfg_now["llm"]["api_key"] = saved_key
    cconf.save_config(cfg_now)
    st, body, _ = jreq("/corpus/api/analyze", "POST", {}, cookie)
    ok(st == 400, "缺 book_id → 400")
    st, body, _ = jreq("/corpus/api/analyze", "POST", {"book_id": "0" * 16}, cookie)
    ok(st == 404, "书目不存在 → 404")

    st, body, _ = jreq("/corpus/api/analyze/clear", "POST", {"book_id": bid}, cookie)
    ok(st == 200, "analyze/clear 可用")

    st, body, _ = jreq("/corpus/api/book/%s" % bid, "DELETE", None, cookie)
    ok(st == 200, "DELETE /api/book/<bid> 清掉书目")
    st, body, _ = jreq("/corpus/api/books", cookie=cookie)
    ok(not any(b["id"] == bid for b in body["items"]), "书目列表里已消失")


def test_exports(cookie):
    # 写一份最小语料, 验证 exports 层能读到 (阅读高亮就是这么扫的)
    corpus_file = paths.PATHS.corpus_dir / "示例书-abc123.json"
    paths.PATHS.corpus_dir.mkdir(parents=True, exist_ok=True)
    corpus_file.write_text(json.dumps({
        "meta": {"title": "示例书", "source_epub": "/x/示例书.epub"},
        "categories": {"person": {"name": "人名", "items": [
            {"term": "张三", "count": 3}, {"term": "李四", "count": 1}]}},
    }, ensure_ascii=False), "utf-8")

    st, body, _ = jreq("/corpus/api/exports", cookie=cookie)
    ok(st == 200 and body["dir"] == str(paths.PATHS.corpus_dir), "GET /api/exports 目录正确")
    ok(isinstance(body["items"], list) and "latest_state" in body, "清单结构完整")
    ok(corpus_file.exists(), "手放的语料留在语料根 (阅读高亮靠扫这一层)")

    st, body, _ = jreq("/corpus/api/export/latest", "POST", {"slug": "示例书-abc123"}, cookie)
    ok(st == 200 and body.get("ok"), "可以钉住 latest")
    ok((paths.PATHS.corpus_dir / "latest.json").exists(), "latest.json 落在语料根")

    st, body, _ = jreq("/corpus/api/export/latest", "POST", {"slug": "没有这份"}, cookie)
    ok(st == 404, "钉不存在的 slug → 404")

    st, body, _ = jreq("/corpus/api/export/sync", "POST", {}, cookie)
    ok(st == 200 and body.get("ok"), "export/sync 可用")

    # 阅读高亮的语料扫描必须认这一份 (两个能力的接缝)
    from recto.core import reading_mark as rm

    hit = rm.load_corpus(corpus_file)
    ok(hit is not None and hit["words_total"] == 2, "阅读高亮能直接读语料根里的产出")
    ok(rm.default_conf()["dirs"] == [str(paths.PATHS.corpus_dir)],
       "阅读高亮的默认扫描目录就是语料根")


def test_shapes(cookie):
    st, body, _ = jreq("/corpus/api/nope", cookie=cookie)
    ok(st == 404 and body.get("error") == "not found", "未知接口 → 404 JSON")
    st, hd, raw = req("/corpus/api/nope", cookie=cookie)
    ok("application/json" in hd.get("Content-Type", ""), "404 也是 JSON (前端能 r.json())")
    st, body, _ = jreq("/corpus/api/config", "PUT", {}, cookie)
    ok(st == 405, "不支持的方法 → 405 (不是 501/500)")
    st, hd, _ = req("/corpus/whatever", cookie=cookie)
    ok(st == 303, "分区内的野路径 → 回分区首页")


def main():
    cookie = login()
    ok(bool(cookie), "登录拿到会话 cookie")
    test_no_flask()
    test_gate()
    test_page(cookie)
    test_assets(cookie)
    test_config(cookie)
    test_models(cookie)
    test_epubs_and_parse(cookie)
    test_exports(cookie)
    test_shapes(cookie)
    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
