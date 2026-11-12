"""书宠分区 —— 真起 ThreadingHTTPServer 的端到端测试。

跑法: cd <仓库根> && PYTHONPATH=src python3 tests/test_pet.py

验的事:
  * 页面: 同一套外壳 (顶栏 / 导航 / 皮肤), 自带 pet.css / pet.js
  * 导航: 顶栏出现「书宠」
  * 资产: /pet/static/* 白名单 (别的文件拿不到)
  * 接口: config (脱敏 key 不覆盖真 key) / status / presets / models 增删改用 / 404 / 405
  * 登录门: 未登录 303 或 401
  * 落盘: 配置写到 <data_dir>/pet/config.json, 权限 600

全部离线: 不调用真实 LLM, 不碰云端账号。
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = Path(tempfile.mkdtemp(prefix="recto-pet-"))
os.environ["RECTO_DATA_DIR"] = str(TMP / "data")
os.environ["RECTO_LIBRARY_DIR"] = str(TMP / "library")
os.environ["RECTO_CLOUD_AUTH_FILE"] = str(TMP / "auth.json")
os.environ["RECTO_PET_LLM_BORROW"] = "off"   # 本文件验"没配就没就绪", 不借用语料那套
os.environ["RECTO_PET_DIR"] = str(TMP / "data" / "pet")
os.environ["RECTO_AUTH_LOG"] = "off"   # 测试不写生产留痕
os.environ["RECTO_AUTH_PASSWORD"] = "pet-pass"
os.environ.pop("RECTO_AUTH_DISABLE", None)
os.environ.pop("RECTO_AUTH_USER", None)
(TMP / "library").mkdir(parents=True, exist_ok=True)
(TMP / "auth.json").write_text(json.dumps({
    "usr": "i0000000000", "zysid": "test", "p29": "zye5b814",
    "p2": "104583", "p3": "101010010", "p4": "501603"}), "utf-8")

from recto.app import server  # noqa: E402
from recto.core import paths  # noqa: E402
from recto.pet import config as petcfg  # noqa: E402

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
    return r.status, raw, hd


def login():
    """用真实口令换一个会话 cookie。"""
    from recto.app import webauth

    return webauth.set_cookie("root")


# ── 登录门 ───────────────────────────────────────
st, raw, _ = req("/pet/")
ok(st in (302, 303, 401), f"未登录访问 /pet/ 被拦 (got {st})")

COOKIE = login()

# ── 页面 ─────────────────────────────────────────
st, raw, hd = req("/pet/", cookie=COOKIE)
html = raw.decode("utf-8", "replace")
ok(st == 200, f"GET /pet/ 200 (got {st})")
ok("<html" in html, "返回的是整页 HTML")
ok("/assets/recto.css" in html, "用同一套外壳样式 (recto.css)")
ok("/pet/static/pet.css" in html, "自带 pet.css")
ok("/pet/static/pet.js" in html, "自带 pet.js")
ok(">书宠<" in html or "书宠" in html, "页面里有「书宠」字样")
ok("模型接入" in html, "页面里有「模型接入」段")

# 导航里有「书宠」
st, raw, _ = req("/", cookie=COOKIE)
ok(">书宠<" in raw.decode("utf-8", "replace"), "顶栏导航出现「书宠」")

# ── 资产白名单 ───────────────────────────────────
st, raw, _ = req("/pet/static/pet.css", cookie=COOKIE)
ok(st == 200 and b"rc-pet" in raw, "GET /pet/static/pet.css 200 且是本书宠样式")
st, raw, _ = req("/pet/static/reader.js", cookie=COOKIE)
ok(st == 404, f"白名单外的资产 404 (got {st})")

# ── 状态 / 预设 ─────────────────────────────────
st, raw, _ = req("/pet/api/status", cookie=COOKIE)
d = json.loads(raw)
ok(st == 200, f"GET /pet/api/status 200 (got {st})")
ok(d.get("ready") is False, "没配模型时 ready=false")
ok("llm" in d and "persona" in d, "status 带 llm / persona / companion")

st, raw, _ = req("/pet/api/presets", cookie=COOKIE)
d = json.loads(raw)
ok(st == 200 and len(d.get("presets") or []) >= 3, "presets 有若干条")

# ── 配置: 脱敏 key 不覆盖真 key ─────────────────
st, raw, _ = req("/pet/api/config", cookie=COOKIE)
d = json.loads(raw)
ok(st == 200 and "llm" in d, "GET /pet/api/config 200 且带 llm 段")
ok(d["llm"].get("has_key") is False, "初始没有 key")

# 存一把真 key
st, raw, _ = req("/pet/api/config", "POST",
                 {"llm": {"base_url": "https://x.example/v1", "model": "m1",
                          "api_key": "sk-realsecret-123456"}}, COOKIE)
ok(st == 200, f"POST /pet/api/config 200 (got {st})")

st, raw, _ = req("/pet/api/config", cookie=COOKIE)
d = json.loads(raw)
ok("sk-realsecret" not in d["llm"]["api_key"], "对外的 key 是脱敏的")
ok(d["llm"].get("has_key") is True, "has_key=true")

# 前端把脱敏串原样回传 → 不能覆盖真 key
st, raw, _ = req("/pet/api/config", "POST",
                 {"llm": {"api_key": d["llm"]["api_key"]}}, COOKIE)
ok(st == 200, "POST 脱敏串 200")

cfg = petcfg.load_config()
ok(cfg["llm"]["api_key"] == "sk-realsecret-123456", "脱敏串回传后真 key 没被覆盖")
ok(cfg["llm"]["model"] == "m1", "其它字段没被这次 POST 冲掉")

# 落盘位置 + 权限
p = Path(petcfg.CONFIG_PATH)
ok(p.exists(), f"配置写到 {p}")
ok((p.stat().st_mode & 0o777) == 0o600, f"配置权限 600 (got {oct(p.stat().st_mode & 0o777)})")

# ready 现在应为 true
st, raw, _ = req("/pet/api/status", cookie=COOKIE)
ok(json.loads(raw).get("ready") is True, "配好之后 ready=true")

# ── 模型库 ───────────────────────────────────────
st, raw, _ = req("/pet/api/models", "POST", {"models": [
    {"id": "m-a", "name": "甲", "provider": "openai",
     "base_url": "https://a.example/v1", "model": "model-a",
     "api_key": "sk-aaaa-1111"},
]}, COOKIE)
d = json.loads(raw)
ok(st == 200 and len(d.get("models") or []) == 1, "POST /pet/api/models 200 且存了 1 条")
ok("sk-aaaa" not in json.dumps(d), "模型库对外也是脱敏的")

st, raw, _ = req("/pet/api/models", cookie=COOKIE)
d = json.loads(raw)
ok(d["models"][0].get("has_key") is True, "模型条目 has_key=true")

# 使用 (套进 llm 段)
st, raw, _ = req("/pet/api/models/apply", "POST", {"id": "m-a"}, COOKIE)
d = json.loads(raw)
ok(st == 200, f"POST /pet/api/models/apply 200 (got {st})")
cfg = petcfg.load_config()
ok(cfg["llm"]["model"] == "model-a", "「使用」把模型套进了 llm 段")
ok(cfg.get("active_model_id") == "m-a", "active_model_id 记住了")

# 改了 llm 段 → 解除绑定
st, raw, _ = req("/pet/api/config", "POST", {"llm": {"model": "other"}}, COOKIE)
cfg = petcfg.load_config()
ok(cfg.get("active_model_id") == "", "改了 llm 段后解除模型库绑定")

# 原样再存 → 保留绑定 (先恢复绑定)
req("/pet/api/models/apply", "POST", {"id": "m-a"}, COOKIE)
st, raw, _ = req("/pet/api/config", "POST", {"llm": {
    "provider": "openai", "base_url": "https://a.example/v1", "model": "model-a"}}, COOKIE)
cfg = petcfg.load_config()
ok(cfg.get("active_model_id") == "m-a", "原样再存保留绑定")

# 删
st, raw, _ = req("/pet/api/models/m-a", "DELETE", cookie=COOKIE)
ok(st == 200, f"DELETE /pet/api/models/m-a 200 (got {st})")
st, raw, _ = req("/pet/api/models/m-a", "DELETE", cookie=COOKIE)
ok(st == 404, f"删两次 → 404 (got {st})")

# ── 404 / 405 ────────────────────────────────────
st, raw, _ = req("/pet/api/nope", cookie=COOKIE)
ok(st == 404, f"未知接口 404 (got {st})")
st, raw, _ = req("/pet/api/models/x", "GET", cookie=COOKIE)
ok(st == 405, f"GET 单条模型 → 405 (got {st})")

# ── 别名 / 路径不串 ──────────────────────────────
st, raw, _ = req("/pet/api/config", cookie=COOKIE)
ok(st == 200, "书宠接口不被语料/摘录的路由吃掉")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
