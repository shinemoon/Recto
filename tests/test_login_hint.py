"""登录页文案 + 口令事件留痕 (webauth.log) + 写后读回校验。

跑法: cd <仓库根> && PYTHONPATH=src python3 tests/test_login_hint.py

两个真实报障的回归:
  1. 「登录页写了一堆 [auth] / admin-password 之类的提示词」—— 登录页只该有
     标志、两个输入框和按钮。
  2. 「改了口令但登录还是旧口令」—— 之前没有任何留痕, 无从定位。现在
     改口令 / 登录成败都落 <data_dir>/logs/webauth.log, 且写完立刻读回校验。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

TMP = Path(tempfile.mkdtemp())
os.environ["RECTO_WEBAUTH_DIR"] = str(TMP)
os.environ.pop("RECTO_AUTH_PASSWORD", None)  # 让口令走文件来源
os.environ.pop("RECTO_AUTH_USER", None)
os.environ.pop("RECTO_AUTH_DISABLE", None)

from recto.app import webauth  # noqa: E402
from recto.app.ui import shell  # noqa: E402
from recto.core.config import get_config  # noqa: E402

FAILED = []


def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILED.append(msg)


# 把 data_dir 指到临时目录: 口令文件与日志都落在里面, 不碰真实部署
class _Cfg:
    data_dir = str(TMP)

    def get(self, *a, **k):
        return ""


_real_cfg = webauth._cfg
webauth._cfg = lambda: _Cfg()            # noqa: SLF001 — 测试隔离
get_config(reload=True)

LOG = TMP / "logs" / "webauth.log"

# --- 1. 登录页: 只有标志 + 两个输入框 + 按钮 -------------------------------

page = webauth.login_page()
for bad, why in (
    ("[auth]", "不写 config.toml 段名"),
    ("admin-password", "不写口令文件路径"),
    ("data_dir", "不写占位变量"),
    ("RECTO_AUTH_PASSWORD", "不写环境变量名"),
    ("环境变量", "不提口令来源"),
    ("会话", "不写会话时长说明"),
    (":5000", "不提阅读器宿主端口"),
    ("口令来源", "不提来源"),
):
    ok(bad not in page, "登录页" + why + " (无 " + bad + ")")

ok('name="username"' in page and 'type="password"' in page, "登录页有两个输入框")
ok('class="primary"' in page and "登录" in page, "登录页有登录按钮")
ok("rc-lockup" in page and "logo-full.png" in page, "登录页保留完整标志")
ok("rc-sub" not in page, "没有副标题那行")
ok(webauth.login_page(err="用户名或口令不对").count('class="err"') == 1, "错误仍会显示")
ok("用户名或口令不对" in webauth.login_page(err="用户名或口令不对"), "错误文案照原样输出")
ok("rc-sub" in shell.login_body(note="自定义一句"), "调用方要文案时仍可显示")

# --- 2. 改口令: 成功留痕 ---------------------------------------------------

ok(not LOG.exists(), "一开始没有日志")
okpath = TMP / "admin-password"
real_password_file = webauth.password_file
webauth.password_file = lambda: okpath      # noqa: SLF001 — 指向临时文件
ok(webauth.set_password("newpass9", who="test/1.2.3.4")[0] is True, "改口令成功")
ok(okpath.read_text("utf-8").strip() == "newpass9", "口令落盘")
text = LOG.read_text("utf-8") if LOG.exists() else ""
ok("口令修改" in text and "成功" in text, "成功写一行日志")
ok("1.2.3.4" in text, "日志带来源 (谁改的)")
ok("newpass9" not in text, "日志里不出现口令本身")
ok(okpath.stat().st_mode & 0o777 == 0o600, "口令文件 0600")

# --- 3. 写后读回不一致 -> 明确报错 (不是"改成功了却登不进") ----------------
_shadow = "shadow-pass"


def _fake_password():
    return _shadow


real_password = webauth.password
webauth.password = _fake_password             # noqa: SLF001 — 影子来源
ok_, msg = webauth.set_password("another9", who="test/1.2.3.4")
ok(ok_ is False, "读回不一致时判定失败")
ok("实际用的" in msg and "shadow" not in msg, "错误文案点明是另一份来源, 且不泄露口令")
ok("读回不一致" in LOG.read_text("utf-8"), "失败也留痕")
webauth.password = real_password              # noqa: SLF001

# --- 4. 登录成败留痕 -------------------------------------------------------

webauth.password = _fake_password             # 口令 = shadow-pass
ok(webauth.verify("root", "shadow-pass", ip="9.9.9.9")[0] is True, "正确口令通过")
ok(webauth.verify("root", "wrong", ip="9.9.9.9")[0] is False, "错口令拒绝")
ok(webauth.verify("nobody", "shadow-pass", ip="9.9.9.9")[0] is False, "错用户名拒绝")
webauth.password = real_password

text = LOG.read_text("utf-8")
ok("登录成功 ip=9.9.9.9" in text, "登录成功留痕 (带 IP)")
ok(text.count("登录失败") >= 2, "登录失败留痕")
ok("shadow-pass" not in text and "wrong" not in text, "登录日志不含口令")
ok("用户名不对" in text, "失败原因区分用户名 / 口令")
ok("logs/webauth.log".split("/")[-1] in str(LOG), "日志路径 <data_dir>/logs/webauth.log")

# 日志轮转: 超过 512KB 换 .1
LOG.write_text("x" * (512 * 1024 + 10), "utf-8")
webauth._audit("轮转测试")                     # noqa: SLF001
ok((TMP / "logs" / "webauth.log.1").exists(), "超过 512KB 轮转成 .1")
ok("轮转测试" in LOG.read_text("utf-8"), "新日志从空文件重新写")

webauth._cfg = _real_cfg                       # noqa: SLF001

print()
if FAILED:
    print(f"{len(FAILED)} FAILED")
    for f in FAILED:
        print("  -", f)
    sys.exit(1)
print("all ok")
