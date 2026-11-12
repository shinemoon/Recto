"""书宠陪读 (阅读器时长 → 修为) —— 幂等 + 陪读源 + HTTP 测试。

跑法: cd <仓库根> && python3 tests/test_pet_companion.py

验的事:
  * credit_reading_minutes: 累计值口径, 只喂增量, **幂等** (重复喂不再涨)
  * 累计值倒退 (换库/删书重扫) → 一分钱不补 (只增不减)
  * 陪读源只取**本地**时长, 不含云端那份 (那是手机读的, 不算陪读)
  * feed_now: 读存档 → 折算 → 写回
  * HTTP: GET /pet/api/companion 与 POST /pet/api/companion

全部离线: 落盘都在临时目录, 不碰云端账号。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = Path(tempfile.mkdtemp(prefix="recto-petcompanion-"))
os.environ["RECTO_DATA_DIR"] = str(TMP / "data")
os.environ["RECTO_LIBRARY_DIR"] = str(TMP / "library")
os.environ["RECTO_CLOUD_AUTH_FILE"] = str(TMP / "auth.json")
os.environ["RECTO_PET_DIR"] = str(TMP / "data" / "pet")
os.environ["RECTO_AUTH_LOG"] = "off"
os.environ["RECTO_AUTH_PASSWORD"] = "pet-pass"
os.environ.pop("RECTO_AUTH_DISABLE", None)
os.environ.pop("RECTO_AUTH_USER", None)
(TMP / "library").mkdir(parents=True, exist_ok=True)
(TMP / "auth.json").write_text(json.dumps({
    "usr": "i0000000000", "zysid": "test", "p29": "zye5b814",
    "p2": "104583", "p3": "101010010", "p4": "501603"}), "utf-8")

from recto.pet import charsheet as cs  # noqa: E402
from recto.pet import companion as cp  # noqa: E402
from recto.pet import growth as g  # noqa: E402

PASS = FAIL = 0


def ok(cond, name):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS", name)
    else:
        FAIL += 1
        print("FAIL", name)


# ── credit_reading_minutes: 累计值口径 ────────────────────
ch = cs.new_char()
r1 = g.credit_reading_minutes(ch, 100)
ok(r1["grew"] is True, "第一次喂 100 分钟 → 有增长")
ok(r1["minutes"] == 100, f"增量 = 100 got {r1['minutes']}")
ok(r1["exp"] == 100 * g.EXP_PER_MINUTE, f"修为 +{r1['exp']}")
ok(ch["credited_minutes"] == 100, f"锚点 = 100 got {ch['credited_minutes']}")
ok(ch["minutes"] == 100, f"累计陪读分钟 100 got {ch['minutes']}")

# 幂等: 同一个累计值再喂一次 → 空转
before_exp = ch["exp"]
before_level = ch["level"]
r2 = g.credit_reading_minutes(ch, 100)
ok(r2["grew"] is False, "重复喂同一个累计值 → 不涨")
ok(r2["minutes"] == 0, "增量为 0")
ok(ch["exp"] == before_exp, f"修为没变 {before_exp} → {ch['exp']}")
ok(ch["level"] == before_level, "等级没变")

# 累计值增长 → 只喂新增的那段
r3 = g.credit_reading_minutes(ch, 160)
ok(r3["grew"] is True, "累计涨到 160 → 有增长")
ok(r3["minutes"] == 60, f"增量 = 60 got {r3['minutes']}")
ok(r3["exp"] == 60 * g.EXP_PER_MINUTE, f"修为只 +{r3['exp']} (不是重算 160)")
ok(ch["credited_minutes"] == 160, "锚点跟到 160")

# 累计值倒退 → 一分钱不补 (只增不减)
before_exp = ch["exp"]
before_cred = ch["credited_minutes"]
r4 = g.credit_reading_minutes(ch, 20)
ok(r4["grew"] is False, "累计值倒退 → 不涨")
ok(ch["exp"] == before_exp, "修为没变")
ok(ch["credited_minutes"] == before_cred,
   f"锚点不倒退 (仍是 {before_cred}) got {ch['credited_minutes']}")

# 0 / 负数
r5 = g.credit_reading_minutes(ch, 0)
ok(r5["grew"] is False and r5["minutes"] == 0, "0 分钟 → 空转")

# 负数当 0
ch_neg = cs.new_char()
r6 = g.credit_reading_minutes(ch_neg, -50)
ok(r6["grew"] is False, "负数 → 空转")
ok(ch_neg["credited_minutes"] == 0, "锚点没动")

# 存档字段默认存在 (老存档兼容)
ok("credited_minutes" in cs.new_char(), "新人物带 credited_minutes 字段")

# 老存档 (接阅读器之前) 只有 minutes、没有锚点 —— **不能被当成"锚点 = 0"**,
# 否则累计陪读被清零, 下一次折算把历史重喂一遍, 修为直接翻倍。
legacy = cs.new_char()
legacy.pop("credited_minutes", None)
legacy["minutes"] = 300
Path(cs.CHAR_PATH).parent.mkdir(parents=True, exist_ok=True)
Path(cs.CHAR_PATH).write_text(json.dumps(legacy, ensure_ascii=False), "utf-8")
lg = cs.load_char()
ok(lg["credited_minutes"] == 300,
   f"老存档缺锚点 → 以 minutes 为锚点 got {lg['credited_minutes']}")
ok(lg["minutes"] == 300, f"老存档累计分钟保留 got {lg['minutes']}")
rg0 = g.credit_reading_minutes(lg, 300)
ok(rg0["grew"] is False, "老存档的历史不会被重算 (修为不翻倍)")

# ── 单次封顶不该吃掉中间的量 ──────────────────────────
# award_minutes 有单次封顶 (MAX_MINUTES_PER_CALL), 一次喂 1000 分钟会只给 480 ——
# 但锚点不该因此跳到 1000, 否则中间那 520 分钟永远补不回来。
big = cs.new_char()
rb = g.credit_reading_minutes(big, 1000)
ok(rb["minutes"] == 1000, f"1000 分钟全部喂进去了 got {rb['minutes']}")
ok(big["minutes"] == 1000, f"累计陪读分钟 = 1000 got {big['minutes']}")
ok(big["credited_minutes"] == 1000, f"锚点 = 1000 got {big['credited_minutes']}")
ok(rb["exp"] == 1000 * g.EXP_PER_MINUTE,
   f"修为按全部 1000 分钟算 got {rb['exp']} (期望 {1000 * g.EXP_PER_MINUTE})")

# 锚点超前于实际量 → 载入时自愈回退, 下一轮补缺口
broken = cs.new_char()
broken["minutes"] = 480
broken["credited_minutes"] = 724        # 第一版的坏状态
cs.save_char(broken)
fixed = cs.load_char()
ok(fixed["credited_minutes"] == 480,
   f"锚点超前 → 载入时回退到 480 got {fixed['credited_minutes']}")# 回退后再喂 724 → 缺口补上
rg = g.credit_reading_minutes(fixed, 724)
ok(rg["minutes"] == 244, f"缺口 244 分钟被补上 got {rg['minutes']}")
ok(fixed["minutes"] == 724, f"累计 = 724 got {fixed['minutes']}")
ok(fixed["credited_minutes"] == 724, f"锚点 = 724 got {fixed['credited_minutes']}")

# 自愈要**落盘** (心跳是写盘的场合): 文件里那一对不一致的数不能一直躺着
dirty = cs.new_char()
dirty["minutes"] = 724
dirty["credited_minutes"] = 634        # 截断态: 锚点落后于累计
cs.save_char(dirty)
hz = cp.feed_now()                      # 数据源此时还是 0 → 锚点校准到 0, 但两个名字必须一致
after_disk = json.loads(Path(cs.CHAR_PATH).read_text("utf-8"))
ok(after_disk["minutes"] == after_disk["credited_minutes"],
   f"落盘后两个名字一致 got {after_disk['minutes']}/{after_disk['credited_minutes']}")
ok(hz["healed"] is True, f"这次报了自愈 got {hz.get('healed')}")
ok(cp.feed_now()["healed"] is False, "已经一致 → 不再报自愈")

# ── 陪读源: 只取本地时长 ────────────────────────────────
from recto.services import reader_state as rs  # noqa: E402

c = rs.conn()
c.execute("""INSERT INTO rtime(book,day,bitmap,minutes,first_at,updated_at,cloud_minutes)
             VALUES(?,?,?,?,?,?,?)""",
          ("bk1", "2026-11-12", "", 120, "x", "x", 999))
c.execute("""INSERT INTO rtime(book,day,bitmap,minutes,first_at,updated_at,cloud_minutes)
             VALUES(?,?,?,?,?,?,?)""",
          ("bk2", "2026-11-12", "", 30, "x", "x", 0))
c.commit()

total = cp.total_reading_minutes()
ok(total == 150, f"本地累计 = 150 (不含云端那份) got {total}")
per = cp.per_book_minutes()
ok(per.get("bk1") == 120, f"bk1 = 120 got {per.get('bk1')}")
ok(per.get("bk2") == 30, f"bk2 = 30 got {per.get('bk2')}")

st = cp.companion_state()
ok(st["total_minutes"] == 150, f"companion_state total = 150 got {st['total_minutes']}")
ok(st["books"] == 2, f"两本书 got {st['books']}")

# ── feed_now: 读存档 → 折算 → 写回 ──────────────────────
cs.save_char(cs.new_char())
gain = cp.feed_now()
ok(gain["grew"] is True, f"feed_now 有增长 got {gain}")
ok(gain["minutes"] == 150, f"折算 150 分钟 got {gain['minutes']}")
saved = cs.load_char()
ok(saved["credited_minutes"] == 150, f"存档锚点 = 150 got {saved['credited_minutes']}")
ok(saved["minutes"] == 150, f"存档累计分钟 = 150 got {saved['minutes']}")

# 再喂一次 → 幂等 (存档不该再变)
before_exp = saved["exp"]
before_level = saved["level"]
gain2 = cp.feed_now()
ok(gain2["grew"] is False, "第二次 feed_now → 空转")
saved2 = cs.load_char()
ok(saved2["exp"] == before_exp, f"修为没变 {before_exp} → {saved2['exp']}")
ok(saved2["level"] == before_level, "等级没变")

# 心跳状态 (没起线程 → 未运行)
ts = cp.tick_state()
ok(ts["enabled"] is False, "没起线程 → enabled=False")
ok(ts["interval_sec"] > 0, "周期是正数")

# ── 锚点校准 (数据源被清理过) ──────────────────────────
# 人物存档里的锚点比数据源还高 → 接下来真实的阅读会被白吃掉
# ("已经折算过了")。校准把锚点拉回来, 但**修为不回收**。
over = cs.new_char()
over["credited_minutes"] = 1000
over["minutes"] = 1000
over["exp"] = 500
fix = cp.reconcile_anchor(over, 400)
ok(fix["adjusted"] == 600, f"校准量 = 600 got {fix}")
ok(over["credited_minutes"] == 400, f"锚点拉回 400 got {over['credited_minutes']}")
ok(over["exp"] == 500, "修为不回收")
# minutes 与锚点是**同一个数** (两个名字) —— 校准必须一起动,
# 否则页面「累计陪读」与锚点对不上, 折算也会分叉。
ok(over["minutes"] == 400, f"累计分钟跟着锚点 (同一个数) got {over['minutes']}")

# 不超前 → 不动
ok0 = cs.new_char()
f2 = cp.reconcile_anchor(ok0, 0)
ok(f2["adjusted"] == 0, "锚点没超前 → 不动")
ok(ok0["credited_minutes"] == 0, "锚点还是 0")

# 校准之后, 新增的阅读能正常折算
g2 = g.credit_reading_minutes(over, 450)
ok(g2["grew"] is True, "校准后新增 50 分钟能折算")
ok(g2["minutes"] == 50, f"增量 50 got {g2['minutes']}")

# ── HTTP ─────────────────────────────────────────────────
import http.client  # noqa: E402
import threading  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

from recto.app import server  # noqa: E402

SRV = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
SRV.daemon_threads = True
threading.Thread(target=SRV.serve_forever, daemon=True).start()
PORT = SRV.server_address[1]


def req(path, method="GET", body=None, cookie=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    h = {}
    if cookie:
        h["Cookie"] = cookie
    payload = None
    if body is not None:
        payload = json.dumps(body)
        h["Content-Type"] = "application/json"
    c.request(method, path, payload, h)
    r = c.getresponse()
    txt = r.read().decode("utf-8", "replace")
    c.close()
    try:
        return r.status, json.loads(txt)
    except json.JSONDecodeError:
        return r.status, txt


sys.path.insert(0, str(ROOT / "tools"))
from recto.app import webauth  # noqa: E402

COOKIE = webauth.set_cookie("root")

code, data = req("/pet/api/companion", cookie=COOKIE)
ok(code == 200, f"GET /pet/api/companion → 200 got {code}")
ok(isinstance(data, dict) and "total_minutes" in data, "返回里有 total_minutes")
ok("tick" in data, "返回里有 tick 状态")

code, data = req("/pet/api/companion", method="POST", body={}, cookie=COOKIE)
ok(code == 200, f"POST /pet/api/companion → 200 got {code}")
ok("gain" in data and "char" in data, "返回里有 gain 与 char")
ok(data["gain"]["grew"] is False, "已经折算过 → POST 也是空转 (幂等)")

# 未登录 → 401
code, _ = req("/pet/api/companion")
ok(code == 401, f"未登录 → 401 got {code}")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
