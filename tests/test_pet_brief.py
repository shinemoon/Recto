"""书宠简要 (阅读器右下角小图标) —— 两行摘要 + 页面契约。

跑法: cd <仓库根> && PYTHONPATH=src python3 tests/test_pet_brief.py

验的事:
  * digest: 第一行是标题, 第二行是开头两句, 最多两行, 不换行, 长句打省略号
  * digest: 没正文 → 只一行; 没标题 → 退地点; 都没有 → 「无题」
  * latest: 空库 → ok=False (小图标不显示); 有游历 → 取最新那条
  * HTTP: GET /read/api/pet-brief 登录后 200 JSON; 未登录被拦
  * 阅读器页面: 有那个小图标的锚点 (target=_blank / href=/pet/)
  * CSS: 小图 (15px) + 半透明 + 9s 慢转 + 悬停暂停 + 减少动效时不转 + 窄屏让位
  * JS: 有"更新才转"的闸 (id 比本机记过的大), 点击记已读, 拿不到就隐藏
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

TMP = Path(tempfile.mkdtemp(prefix="recto-petbrief-"))
os.environ["RECTO_DATA_DIR"] = str(TMP / "data")
os.environ["RECTO_LIBRARY_DIR"] = str(TMP / "library")
os.environ["RECTO_CLOUD_AUTH_FILE"] = str(TMP / "auth.json")
os.environ["RECTO_PET_DIR"] = str(TMP / "data" / "pet")
os.environ["RECTO_PET_LLM_BORROW"] = "off"
os.environ["RECTO_AUTH_LOG"] = "off"
os.environ["RECTO_AUTH_PASSWORD"] = "brief-pass"
os.environ.pop("RECTO_AUTH_DISABLE", None)
os.environ.pop("RECTO_AUTH_USER", None)
(TMP / "library").mkdir(parents=True, exist_ok=True)
(TMP / "auth.json").write_text(json.dumps({
    "usr": "i0000000000", "zysid": "test", "p29": "zye5b814",
    "p2": "104583", "p3": "101010010", "p4": "501603"}), "utf-8")

from recto.app import server  # noqa: E402
from recto.pet import brief  # noqa: E402
from recto.pet import store  # noqa: E402

PASS = FAIL = 0


def ok(cond, name):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS", name)
    else:
        FAIL += 1
        print("FAIL", name)


def adv(**kw):
    d = {"id": 1, "no": 1, "at": "2026-11-12T14:06:56", "title": "", "place": "", "body": ""}
    d.update(kw)
    return d


# ── digest (纯函数) ──────────────────────────────
d = brief.digest(adv(title="第十一回 · 石阶上的半张符", place="云上城后山",
                     body="云阶很长，石缝里长满青苔。\n\n小墨提着扫帚，一下一下地扫。第三句不要。"),
                 name="小墨", life={"day": 10, "day_no": 11, "age": 12})
ok(len(d["lines"]) == 2, f"有正文 → 两行 (got {len(d['lines'])})")
ok(d["lines"][0] == "第十一回 · 石阶上的半张符", "第一行 = 标题")
ok(d["lines"][1].startswith("云阶很长，石缝里长满青苔。"), "第二行 = 开头第一句")
ok("\n" not in "".join(d["lines"]), "两行里没有换行 (悬停框里不能折成多行)")
ok(all(len(x) <= brief.LINE_MAX + 1 for x in d["lines"]), "每行不超过 LINE_MAX 字")
ok(d["day_no"] == 11 and d["age"] == 12 and d["name"] == "小墨", "带上故事时序与名字")
ok(d["id"] == 1 and d["at"].startswith("2026-11-12"), "id / at 原样带出")

# 只取前两句 (第三句不进摘要)
ok("第三句" not in d["lines"][1], "只取前两句, 不铺全文")

# 超长单句 → 打省略号
long_body = "此事说来话长，" + "山风一阵阵吹过石阶上的青苔" * 4 + "。第二句。"
d2 = brief.digest(adv(title="长句", body=long_body))
ok(d2["lines"][1].endswith("…"), "超长句打省略号")
ok(len(d2["lines"][1]) <= brief.LINE_MAX + 1, "超长句截断在 LINE_MAX 内")

# 没正文 → 只一行
d3 = brief.digest(adv(title="只有标题"))
ok(d3["lines"] == ["只有标题"], f"没正文只有标题 (got {d3['lines']})")

# 没标题 → 退地点; 都没 → 「无题」
d4 = brief.digest(adv(place="云上城后山", body="云阶很长。"))
ok(d4["lines"][0] == "云上城后山", "没标题时用地点当第一行")
d5 = brief.digest(adv())
ok(d5["lines"][0] == "无题", "都没给 → 「无题」(不显示空行)")

# 空白与多段压缩成一整行
d6 = brief.digest(adv(title=" 标题 ", body="  第一段。\n\n\n第二段。  "))
ok(d6["lines"][0] == "标题", "标题去首尾空白")
ok("第一段。第二段。" == d6["lines"][1], f"多段压成一行 (got {d6['lines'][1]!r})")

# 标题与正文相同 → 不重复占两行
d7 = brief.digest(adv(title="云阶很长。", body="云阶很长。"))
ok(len(d7["lines"]) == 1, "标题与开头相同 → 只显示一行")

# ── latest: 空库 ─────────────────────────────────
r = brief.latest()
ok(r.get("ok") is False and r.get("lines") == [], f"空库 → ok=False 且无行 (got {r})")

# ── latest: 有游历 → 取最新 ──────────────────────
c = store.conn()
c.execute("INSERT INTO adventure (at, no, title, place, body, growth) VALUES (?,?,?,?,?,?)",
          ("2026-11-12T11:43:35", 2, "第二回 · 彩雀府外的符箓", "旧街市", "旧的一回。", "{}"))
c.execute("INSERT INTO adventure (at, no, title, place, body, growth) VALUES (?,?,?,?,?,?)",
          ("2026-11-12T14:06:56", 4, "第十一回 · 石阶上的半张符", "云上城后山",
           "云阶很长，石缝里长满青苔。小墨提着扫帚，一下一下地扫。", "{}"))
c.commit()
c.close()

r = brief.latest()
ok(r.get("ok") is True, "有游历 → ok=True")
ok(r["title"] == "第十一回 · 石阶上的半张符", "取最新那一回 (按 id 倒序)")
ok(len(r["lines"]) == 2, "两行")
ok(r["place"] == "云上城后山", "带地点")

# ── HTTP ─────────────────────────────────────────
SRV = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
SRV.daemon_threads = True
threading.Thread(target=SRV.serve_forever, daemon=True).start()
PORT = SRV.server_address[1]


def req(path, method="GET", body=None, cookie=None):
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
    hdrs = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    if cookie:
        hdrs["Cookie"] = cookie
    conn.request(method, path, body=data, headers=hdrs)
    resp = conn.getresponse()
    raw = resp.read()
    hdr = dict(resp.getheaders())
    conn.close()
    return resp.status, raw, hdr


st, raw, _ = req("/read/api/pet-brief")
ok(st in (303, 401), f"未登录拿摘要被拦 (got {st})")

from recto.app import webauth  # noqa: E402

COOKIE = webauth.set_cookie("root")

st, raw, _ = req("/read/api/pet-brief", cookie=COOKIE)
ok(st == 200, f"GET /read/api/pet-brief 200 (got {st})")
try:
    j = json.loads(raw)
except Exception:  # noqa: BLE001
    j = {}
ok(j.get("ok") is True, "返回 JSON 且 ok=True")
ok(len(j.get("lines") or []) == 2, "接口给两行")
ok("石阶上的半张符" in (j.get("lines") or [""])[0], "摘要取自最新一回")

# ── 阅读器页面 / 资产契约 ────────────────────────
page_src = (ROOT / "src/recto/app/reader.py").read_text("utf-8")
ok('id="rv-pet"' in page_src, "阅读器页面有小图标锚点")
ok('href="/pet/"' in page_src, "点击指向书宠分区")
ok('target="_blank"' in page_src and "noopener" in page_src, "新开页且 noopener")
ok('id="rv-pet-tip"' in page_src, "有悬停摘要容器")
ok("rv-pet-turn" in (ROOT / "src/recto/app/ui/assets/reader.css").read_text("utf-8")
   or "rv-pet-turn" in page_src, "动画名在样式里")

css = (ROOT / "src/recto/app/ui/assets/reader.css").read_text("utf-8")
ok(".rv-pet {" in css or ".rv-pet {" in css, "有 .rv-pet 样式块")
ok("width: 15px" in css and "height: 15px" in css, "图标小 (15px), 不抢注意力")
ok("opacity: .34" in css, "平时半透明")
ok("@keyframes rv-pet-turn" in css, "有慢转关键帧")
ok("animation: rv-pet-turn 9s linear infinite" in css, "转得慢 (9 秒一圈)")
ok("animation-play-state: paused" in css, "悬停时暂停, 不晃眼")
ok("prefers-reduced-motion: reduce" in css, "系统要求减少动效时不转")
ok(".rv-pet-tip > span" in css, "悬停框里每行一条 (最多两行)")
ok("text-overflow: ellipsis" in css, "超长行打省略号")
ok("safe-area-inset-bottom" in css, "让开手机底部安全区")

js = (ROOT / "src/recto/app/ui/assets/reader.js").read_text("utf-8")
ok("/read/api/pet-brief" in js, "前端拿的就是这个摘要接口")
ok("recto.pet.seen" in js, "本机记住看过哪一回")
ok("petState.id > petState.seen" in js, "只有更新才加动效")
ok("classList.remove('is-new')" in js, "点开就停转")
ok("petEl.hidden = true" in js, "拿不到就整块隐藏 (不干扰阅读)")
ok("pet: function ()" in js, "保留只读排查口 __RV__.pet()")
ok("petLoad();" in js, "打开书时加载一次")
ok("textContent = t" in js, "摘要用 textContent 写 (故事里的尖括号不当标签)")

print(f"\n{'=' * 46}")
print(f"PASS {PASS}  FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
