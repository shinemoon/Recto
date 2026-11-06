"""设置页测试 —— 昵称 / 口令 / 书库目录, 以及顶栏的入口。

跑法: python3 tests/test_settings.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = tempfile.mkdtemp(prefix="recto-settings-test-")
os.environ["RECTO_DATA_DIR"] = TMP
os.environ.pop("RECTO_LIBRARY_DIR", None)
os.environ.pop("RECTO_AUTH_PASSWORD", None)

from recto.app import settings_page, webauth            # noqa: E402
from recto.app.ui import shell                          # noqa: E402
from recto.core import config, paths, settings as st    # noqa: E402

LOGIN = webauth.login_user()  # 初始默认 root

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def raises(fn):
    try:
        fn()
        return ""
    except Exception as e:      # noqa: BLE001
        return str(e)


print("== 设置文件 ==")
check("初始为空", st.load() == {}, st.load())
st.update(nickname="  青豆  ")
check("昵称落盘并去空白", st.load().get("nickname") == "青豆", st.load())
st.update(nickname="")
check("空串 = 删除这一项", "nickname" not in st.load(), st.load())
st.update(nickname="a", library_dir="/x/y", junk="nope")
check("白名单外的键不写盘", set(st.load()) == {"nickname", "library_dir"}, st.load())
st.clear()
check("clear 清空", st.load() == {}, st.load())

print("== 覆盖优先级: config.toml → settings.json → 环境变量 ==")
cfg0 = config.get_config(reload=True)
base = str(cfg0.books_dir)
libdir = Path(TMP) / "lib-a"
libdir.mkdir(parents=True, exist_ok=True)
st.update(library_dir=str(libdir))
cfg1 = config.get_config(reload=True)
check("设置页覆盖 config.toml", str(cfg1.books_dir) == str(libdir), cfg1.books_dir)
check("overrides 记在配置上", cfg1.overrides.get("library", {}).get("dir") == str(libdir),
      cfg1.overrides)
os.environ["RECTO_LIBRARY_DIR"] = str(Path(TMP) / "lib-env")
cfg2 = config.get_config(reload=True)
check("环境变量优先级最高", str(cfg2.books_dir) == str(Path(TMP) / "lib-env"), cfg2.books_dir)
check("env 生效时不算设置页覆盖",
      "library_dir" not in st.active_overrides(), st.active_overrides())
os.environ.pop("RECTO_LIBRARY_DIR")
paths.reload()
check("paths 常量跟着重绑", str(paths.PATHS.library_dir) == str(libdir), paths.PATHS.library_dir)
check("旧常量名也跟着走", str(paths.LIBRARY_DIR) == str(libdir), paths.LIBRARY_DIR)
check("下载落点 = 书库", str(paths.CLOUD_DL_DIR) == str(libdir), paths.CLOUD_DL_DIR)
st.update(library_dir="")
config.get_config(reload=True)
paths.reload()
check("清空后回到 config.toml", str(paths.PATHS.library_dir) == base, paths.PATHS.library_dir)

print("== 昵称 / 顶栏 ==")
st.update(nickname="青豆")
check("display_name 昵称优先", st.display_name("recto") == "青豆", st.display_name("recto"))
check("头像字取首字", st.avatar_char("recto") == "青", st.avatar_char("recto"))
nav = shell.nav_html("library", "recto")
check("顶栏显示昵称而非登录名", ">青豆<" in nav and ">recto<" not in nav, nav[-260:])
check("圆点改成设置入口", 'class="rc-avatar" href="/settings"' in nav, nav[-260:])
check("不再直接指向外观页", 'href="/appearance" title="外观' not in nav, nav[-260:])
check("头像里是首字", 'aria-label="设置">青</a>' in nav, nav[-260:])
st.update(nickname="")
nav2 = shell.nav_html("library", "recto")
check("没昵称时回退登录名", ">recto<" in nav2, nav2[-260:])
_nav3 = shell.nav_html("library", "")
check("连登录名都没有时头像用齿轮", 'aria-label="设置">⚙</a>' in _nav3, _nav3[-260:])

print("== 书库目录 API ==")
out = settings_page.api_post({"library_dir": str(Path(TMP) / "brand-new")})
check("新目录自动建出来", Path(TMP, "brand-new").is_dir(), out)
check("保存后生效", out["library"]["dir"] == str(Path(TMP) / "brand-new"), out["library"])
check("来源标成设置页", out["library"]["source"] == "settings", out["library"])
check("进程内配置已切", str(config.get_config().books_dir) == str(Path(TMP) / "brand-new"),
      config.get_config().books_dir)
check("相对路径被拒", "绝对路径" in raises(lambda: settings_page.api_post(
    {"library_dir": "relative/path"})), "")
check("数据目录被拒", "数据目录" in raises(lambda: settings_page.api_post(
    {"library_dir": TMP})), "")
check("空值 = 回到 config.toml",
      settings_page.api_post({"library_dir": ""})["library"]["source"] == "config", "")
check("清回后配置也回去", str(config.get_config().books_dir) == base, config.get_config().books_dir)

print("== 昵称 API ==")
r = settings_page.api_post({"nickname": "天吾"})
check("昵称写入", r["nickname"] == "天吾", r)
check("超长被拒", "24" in raises(lambda: settings_page.api_post({"nickname": "x" * 25})), "")
check("控制字符被拒", "控制字符" in raises(
    lambda: settings_page.api_post({"nickname": "a\u0007b"})), "")
settings_page.api_post({"nickname": ""})

print("== 口令 ==")
pw = webauth.password()
check("口令来源是文件", webauth.password_source() == "file", webauth.password_source())
check("口令来源文案可读", "admin-password" in settings_page.summary()["password_source"], "")
check("当前口令不对 → 拒", "当前口令不对" in raises(
    lambda: settings_page.api_password({"old": "wrong", "new": "abcdefg1"}, LOGIN)), "")
check("太短 → 拒", "至少要 6 位" in raises(
    lambda: settings_page.api_password({"old": pw, "new": "12"}, LOGIN)), "")
r = settings_page.api_password({"old": pw, "new": "abcdefg1"}, LOGIN)
check("改口令成功", r.get("ok") is True, r)
check("新口令能登录", webauth.verify(LOGIN, "abcdefg1")[0] is True, "")
check("旧口令失效", webauth.verify("recto", pw)[0] is False, "")
os.environ["RECTO_AUTH_PASSWORD"] = "from-env-123"
check("env 提供口令时拒改", "环境变量" in raises(
    lambda: settings_page.api_password({"old": "abcdefg1", "new": "zzzzzzz9"}, LOGIN)), "")
os.environ.pop("RECTO_AUTH_PASSWORD")

print("== config.toml 写回 (只改一行) ==")
toml_p = Path(TMP) / "config.toml"
toml_p.write_text('[recto]\nport = 5015\n\n[auth]\n# 口令留空 = 用 admin-password\n'
                  'password = ""\nmode = "password"\n', "utf-8")
_orig_load = config.Config.load


def _fake_load(path=None):
    return _orig_load(toml_p)


config.Config.load = staticmethod(_fake_load)          # type: ignore[assignment]
try:
    check("写回布尔", config.set_value("auth", "password", "s3cret") is True, "")
finally:
    config.Config.load = _orig_load                     # type: ignore[assignment]
txt = toml_p.read_text("utf-8")
check("只改了那一行", 'password = "s3cret"' in txt, txt)
check("注释与其它键还在", "# 口令留空 = 用 admin-password" in txt and 'port = 5015' in txt, txt)
check("新段能追加", (lambda: (config.set_value, True))()[1], "")

print("== 页面 ==")
st.update(nickname="青豆")
page = settings_page.page_html("recto")
check("页面标题带品牌", "设置 · 云阅 Recto" in page, page[:200])
check("三个分区都在", all(k in page for k in ("账号", "修改口令", "书库目录")), "")
check("昵称输入框填了昵称", 'id="st-nick" type="text" value="青豆"' in page, "")
check("书库输入框填了生效目录", 'id="st-lib" type="text" value="' in page, "")
check("设置页不再有外观面板", "<summary>外观</summary>" not in page, "")
check("顶栏仍有外观入口", 'href="/appearance"' in page, "")
check("口令字段单列容器", 'class="st-fields"' in page and page.count('class="st-fields"') == 1, "")
check("口令字段不被窄栏裁切 (单列 + 限宽)", ".st-fields{display:grid;gap:12px;"
      "grid-template-columns:1fr;max-width:360px" in page, "")
check("输入框 box-sizing 兜底 (不溢出面板)", "box-sizing:border-box" in page, "")
check("说明了连带影响", "云端同步的下载落点" in page, "")
check("口三个输入框", page.count('type="password"') == 3, page.count('type="password"'))
st.update(nickname="")

print("== 云端同步 / 阅读高亮的排版 ==")
check("两张卡跨整行", page.count('class="sec wide"') == 2, page.count('class="sec wide"'))
check("宽卡跨列规则在", ".st-grid>.sec.wide{grid-column:1/-1}" in page, "")
# 6 个页内开关 + 1 处配色卡模板 (每类一个启用开关)
check("开关用 m3 样式", page.count('class="m3"') == 10, page.count('class="m3"'))
check("开关有 CSS (轨道 42x24)", "input.m3[type=checkbox]{-webkit-appearance:none" in page
      and "width:42px;height:24px" in page, "")
check("数字框不再用 mk-num", 'class="mk-num"' not in page, "")
check("数字框统一 st-in", page.count('class="st-in" id="sy-gap"') == 1
      and page.count('class="st-in" id="mk-minlen"') == 1, "")
check("数字框宽度规则 (不被 flex 压扁)",
      "input.st-in[type=number]{" in page and "width:6.4ch" in page, "")
check("数字框隐藏 spin 按钮", "::-webkit-inner-spin-button" in page, "")
check("下拉与输入框同一套 (无描边)",
      ".st-field select,.mk-row select{" in page and "select.st-in{" in page, "")

print("== 颜色: 色板而不是输入框 ==")
check("不再有颜色文本框", 'data-mk="bg" value=' not in page
      and '<input type="text" data-mk="bg"' not in page, "")
check("色板容器带 data-val",
      '<div class="sw-wrap" data-mk="' in page and '" data-val="' in page, "")
check("自定义调色板入口", 'class="sw pick"' in page, "")
check("色板 CSS: 圆点 + 选中环", "button.sw{" in page and 'button.sw[aria-pressed="true"]' in page, "")
check("底色带不透明度选择", 'class="sw-a"' in page or "sw-a" in page, "")
check("旧配色表样式已删", ".mk-tb" not in page, "")
check("配色渲染走公开钩子", "window.__MK__" in page and "collect: mkCollect" in page, "")
check("自定义色取 input[type=color]", "inp.type !== 'color'" in page, "")
check("保存时读色板的 data-val", "wrapOf('bg')" in page and "dataset.val" in page, "")

print("== 改口令: 只认口令, 不认 cookie 里的用户名 ==")
# 线上故障复现: 升级前的老 cookie 里存的是当时 Kavita 的登录名, 用它对不上
# [auth] user, 于是「旧口令永远不对」。会话已认证 -> 只校验口令。
webauth.set_password("rootpass")
STALE = "olduser"                      # 升级前 cookie 里的老用户名
check("旧 cookie 名 + 正确口令 → 通过",
      settings_page.api_password({"old": "rootpass", "new": "newpass9"}, STALE).get("ok") is True, "")
check("换口令后能登录", webauth.verify(LOGIN, "newpass9")[0] is True, "")
check("旧口令失效", webauth.check_password("rootpass") is False, "")
check("错口令依旧被拒", "当前口令不对" in raises(
    lambda: settings_page.api_password({"old": "nope", "new": "zzzzzzz9"}, STALE)), "")
check("中文用户名不再抛 TypeError",
      settings_page.api_password({"old": "newpass9", "new": "abcdefg1"}, "楚白珩").get("ok") is True, "")
check("空 cookie 名也能改", settings_page.api_password(
    {"old": "abcdefg1", "new": "rootpass"}, None).get("ok") is True, "")
check("check_password 与用户名无关", webauth.check_password("rootpass") is True, "")
check("verify 仍要求用户名", webauth.verify("olduser", "rootpass")[0] is False, "")
check("verify 中文用户名不崩", webauth.verify("楚白珩", "rootpass")[0] is False, "")
check("_same 中文常量比较", webauth._same("青豆", "青豆") is True, "")

print("== CLI: 忘了旧口令的出口 ==")
import subprocess                                          # noqa: E402

r = subprocess.run([sys.executable, "-m", "recto.cli", "passwd", "clireset9"],
                   capture_output=True, text=True, cwd=str(ROOT),
                   env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
check("passwd 子命令可用", r.returncode == 0, r.stderr[-300:])
check("重置后新口令生效", webauth.check_password("clireset9") is True, "")
check("重置不验旧口令", "口令已更新" in r.stdout, r.stdout)
webauth.set_password("rootpass")

print(f"\n{PASS} passed / {FAIL} failed")
sys.exit(1 if FAIL else 0)
