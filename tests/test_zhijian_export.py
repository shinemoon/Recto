"""「导出到纸间」工具卡：接口 + 推送顺序 + 表单可用性。

跑法:  cd <仓库根> && PYTHONPATH=src python3 tests/test_zhijian_export.py

守的是四条真踩过的坑：
  1. `/admin/api/export-zhijian` 路由在迁移里丢了 → 前端 fetch 到 404 HTML，
     `r.json()` 抛错，用户看到的就是「JSON parse failed」。接口必须永远返 JSON。
  2. 浏览器跨源 POST JSON 会先发 OPTIONS 预检；纸间 App 不答预检时 fetch
     既不报错也不返回（挂住），所以直连不能当主路径 —— 必须 server 代发优先，
     且两段都要有超时，否则一本失败就整个流程卡死。
  3. `<input type="number">` 的 value 不满足 step 会让整个表单 checkValidity=false，
     点「推送」什么都不发生（浏览器的校验气泡在折叠区里根本看不见）。
  4. server 代发失败要返 JSON 报错（502），不能抛 traceback 变成 HTML。
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

TMP = Path(tempfile.mkdtemp())
os.environ["RECTO_WEBAUTH_DIR"] = str(TMP)
os.environ["RECTO_AUTH_DISABLE"] = "1"
# 导出现在会读 reader.sqlite 的划线/笔记 —— 必须指到临时 data_dir,
# 否则会读到本机真实数据, 断言随用户读了多少书而飘 (2026-11-07 撞过)。
os.environ["RECTO_DATA_DIR"] = str(TMP / "data")

from recto.app import excerpts  # noqa: E402

# ---------------- 造数据 ----------------

DATA = TMP / "pe"
(DATA / "aggregate").mkdir(parents=True)
(DATA / "aggregate" / "books.jsonl").write_text(
    json.dumps({"id": 1379, "name": "示例长篇（多卷）", "author": "某作者"}, ensure_ascii=False) + "\n"
    + json.dumps({"id": 42, "name": "示例短篇", "author": "另一作者"}, ensure_ascii=False) + "\n",
    encoding="utf-8",
)
rows = [
    {"id": 1, "bookId": 1379, "chapter": "第一章 惊蛰",
     "content": "天上月，人间月，负笈求学肩上月。", "idea": "记住了这句",
     "createdTime": 1793500000000},
    {"id": 2, "bookId": 1379, "chapter": "第二章 山雨",
     "content": "碎冰碰壁当啷响。", "idea": "",
     "page": 30, "pageCount": 100, "createdTime": 1793400000000},
    {"id": 3, "bookId": 42, "chapter": "第三章",
     "content": "城市中枢的某个黑暗神秘的地方。", "idea": "夜里读的",
     "createdTime": 1793300000000},
]
(DATA / "aggregate" / "aggregate.jsonl").write_text(
    "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
)


class _Handler:
    """只提供 _user() —— 渲染函数用不到别的。"""

    def _user(self):
        return "demo"


H = _Handler()


class FakeHandler:
    """喂给 handle_admin 的最小 HTTP handler。"""

    def __init__(self, command: str, form: dict | None = None):
        self.command = command
        body = urlencode(form or {}).encode()
        self.headers = {
            "Content-Length": str(len(body)),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.client_address = ("127.0.0.1", 12345)
        self._admin_ok = True   # 会话门在 server 层, 这里只测路由本身
        self.status = None
        self.resp_headers: list[tuple[str, str]] = []

    def send_response(self, code, message=None):
        self.status = code

    def send_header(self, k, v):
        self.resp_headers.append((k, v))

    def end_headers(self):
        pass

    def body(self) -> bytes:
        return self.wfile.getvalue()

    def json(self):
        return json.loads(self.body().decode("utf-8"))

    def content_type(self) -> str:
        for k, v in self.resp_headers:
            if k.lower() == "content-type":
                return v
        return ""


def call(path: str, form: dict | None = None):
    h = FakeHandler("POST", form)
    handled = excerpts.handle_admin(h, DATA, path, "")
    okv = handled is True, "handle_admin 必须消费这个路由"
    assert okv[0], okv[1]
    return h


def ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


# ---------------- 1. 接口永远是 JSON ----------------

def test_export_route_returns_json_not_html():
    h = call("/admin/api/export-zhijian", {"host": "1.2.3.4", "port": "8080"})
    ok("json" in h.content_type(), f"Content-Type 必须是 JSON, 实际 {h.content_type()!r}")
    ok(h.status == 200, f"正常请求 200, 实际 {h.status}")
    j = h.json()
    ok(j.get("ok") is True, f"ok=true, 实际 {j}")
    ok(isinstance(j.get("payloads"), list) and j["payloads"], "要有 payloads")


def test_export_route_400_is_also_json():
    h = call("/admin/api/export-zhijian", {"host": "", "port": "8080"})
    ok(h.status == 400, f"缺 host 要 400, 实际 {h.status}")
    ok("json" in h.content_type(), "400 也必须是 JSON (否则前端还是 JSON parse failed)")
    ok(h.json().get("ok") is False, "ok=false")


def test_export_payload_shape_for_zhijian():
    h = call("/admin/api/export-zhijian", {"host": "1.2.3.4", "port": "8080"})
    p = h.json()["payloads"][0]
    ok(p["type"] == 1, "纸间 /send 的 type 固定 1")
    ok(p["locationUnit"] == 0, "page 当 0-100 百分比解释")
    e = p["entries"][0]
    for key in ("page", "text", "note", "chapter", "time"):
        ok(key in e, f"entry 缺字段 {key}")
    ok(isinstance(e["time"], int), "time 必须是整数秒")
    ok(0 <= e["page"] <= 100, "page 是百分比")
    ok(p["title"] == "示例长篇（多卷）", "按书名分组")


def test_export_page_pct_uses_page_and_pagecount():
    h = call("/admin/api/export-zhijian", {"host": "1.2.3.4", "port": "8080", "book_name": "示例长篇"})
    entries = {e["text"][:4]: e for p in h.json()["payloads"] for e in p["entries"]}
    ok(entries["碎冰碰壁"]["page"] == 30, f"30/100 页 → 30%, 实际 {entries['碎冰碰壁']['page']}")
    ok(entries["天上月，"]["page"] == 0, "没有页号的老摘录 → 0 (不能算成别的)")


def test_export_respects_safety_cap():
    h = call("/admin/api/export-zhijian", {"host": "1.2.3.4", "safety_cap": "1"})
    j = h.json()
    ok(j.get("ok") is False, "超上限要拒绝")
    ok("safety_cap" in j.get("error", ""), "报错里要点名 safety_cap 和条数")
    ok(j.get("total") == 3, f"要告诉命中多少条, 实际 {j.get('total')}")


def test_export_filters_by_book_and_text():
    h = call("/admin/api/export-zhijian", {"host": "1.2.3.4", "book_name": "示例短篇"})
    j = h.json()
    ok(j["total"] == 1 and j["book_count"] == 1, f"按书名筛完只剩 1 条, 实际 {j['total']}")
    h2 = call("/admin/api/export-zhijian", {"host": "1.2.3.4", "q": "碎冰"})
    ok(h2.json()["total"] == 1, "文本搜索只命中一条")


# ---------------- 2. server 代发 ----------------

class _SendStub(BaseHTTPRequestHandler):
    """假纸间: 记录收到的 body, 应答 {"code":200}。"""

    got: list[dict] = []

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n)
        type(self).got.append(json.loads(raw.decode("utf-8")))
        out = json.dumps({"code": 200, "message": "success"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def _stub_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _SendStub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def test_send_route_forwards_and_reports_ok():
    srv, port = _stub_server()
    try:
        payload = {"title": "示例长篇", "author": "某作者", "type": 1, "locationUnit": 0,
                   "entries": [{"page": 0, "text": "天上月", "note": "", "chapter": "第一章", "time": 1}]}
        h = call("/admin/api/zhijian-send", {
            "host": "127.0.0.1", "port": str(port), "payload": json.dumps(payload, ensure_ascii=False),
        })
        ok(h.status == 200, f"代发成功要 200, 实际 {h.status}")
        j = h.json()
        ok(j.get("ok") is True, f"ok=true, 实际 {j}")
        ok(j.get("code") == 200, "带上设备返回的 code")
        ok(_SendStub.got and _SendStub.got[-1]["title"] == "示例长篇", "设备真的收到了 payload")
        ok(_SendStub.got[-1]["entries"][0]["text"] == "天上月", "条目内容原样转发")
    finally:
        srv.shutdown()


def test_send_route_unreachable_is_json_502():
    h = call("/admin/api/zhijian-send", {"host": "127.0.0.1", "port": "9", "payload": '{"entries":[]}'})
    ok(h.status == 502, f"连不上设备要 502, 实际 {h.status}")
    ok("json" in h.content_type(), "502 也必须是 JSON")
    ok(h.json().get("ok") is False, "ok=false")
    ok(h.json().get("error"), "要给出原因 (URLError/refused 等)")


def test_send_route_rejects_bad_payload():
    h = call("/admin/api/zhijian-send", {"host": "127.0.0.1", "port": "9", "payload": "not-json"})
    ok(h.status == 400, f"坏 payload 要 400, 实际 {h.status}")
    ok(h.json().get("ok") is False, "ok=false")


def test_send_route_requires_host_and_payload():
    for form in ({"payload": "{}"}, {"host": "127.0.0.1"}):
        h = call("/admin/api/zhijian-send", form)
        ok(h.status == 400, f"{form} 要 400, 实际 {h.status}")


# ---------------- 3. 表单与前端顺序 ----------------

def test_form_safety_cap_is_valid_html():
    html = excerpts._pane_html("tools", H, DATA, excerpts._pane_params({}))
    m = re.search(r'<input type="number" name="safety_cap"[^>]*>', html)
    ok(m, "表单要有 safety_cap 输入")
    tag = m.group(0)
    ok('step="1"' in tag, "step 必须是 1 —— 否则 value 不满足步长, 整个表单 checkValidity=false, 点推送毫无反应")
    val = int(re.search(r'value="(\d+)"', tag).group(1))
    step = int(re.search(r'step="(\d+)"', tag).group(1))
    lo = int(re.search(r'min="(\d+)"', tag).group(1))
    ok((val - lo) % step == 0, f"value={val} 必须落在 min={lo} / step={step} 的格点上")


def test_push_js_server_first_with_timeouts():
    html = excerpts._pane_html("tools", H, DATA, excerpts._pane_params({}))
    js = html[html.index('id="zhijian-form"'):]
    ok("withTimeout" in js, "必须给两段 fetch 都加超时 (预检挂住时 fetch 永不返回)")
    ok(js.index("/admin/api/zhijian-send") < js.index('/send"'), "server 代发要排在浏览器直连之前")
    src = Path(excerpts.__file__).read_text(encoding="utf-8")
    ok("预检" in src, "源码注释里写清为什么直连不能当主路径 (预检不答会挂住)")
    ok("30000" in js and "8000" in js, "代发 30s / 直连 8s")


def test_tools_card_no_dead_endpoint():
    """卡片里出现的每个 /admin/api/* 都要真有路由。"""
    html = excerpts._pane_html("tools", H, DATA, excerpts._pane_params({}))
    used = set(re.findall(r'"/admin/api/[a-z0-9\-]+"', html))
    src = Path(excerpts.__file__).read_text(encoding="utf-8")
    for u in used:
        path = u.strip('"')
        ok(f'"{path}"' in src, f"{path} 在前端被调用, 但源码里没有这个字面量 (路由丢了?)")


def test_form_validity_all_elements():
    """所有带约束的输入都得满足约束 —— 否则点提交浏览器静默拦下。"""
    html = excerpts._pane_html("tools", H, DATA, excerpts._pane_params({}))
    for m in re.finditer(r'<input ([^>]*)>', html):
        attrs = m.group(1)
        if 'type="number"' not in attrs:
            continue
        val = re.search(r'value="([-\d.]+)"', attrs)
        lo = re.search(r'min="([-\d.]+)"', attrs)
        step = re.search(r'step="([-\d.]+)"', attrs)
        if val and lo and step and step.group(1) not in ("0", "any"):
            gap = float(val.group(1)) - float(lo.group(1))
            sv = float(step.group(1))
            ok(abs(gap / sv - round(gap / sv)) < 1e-9,
               f"input value={val.group(1)} min={lo.group(1)} step={step.group(1)} 不满足步长: {attrs[:80]}")


# ---------------- runner ----------------

def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    passed = 0
    failed = []
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as exc:  # noqa: BLE001
            failed.append((name, f"{type(exc).__name__}: {exc}"))
    for name, err in failed:
        print(f"FAIL {name} — {err}")
    print(f"\n{passed} PASS, {len(failed)} FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
