"""外观层 (皮肤 / 外壳) 的单元测试。

    python3 tests/test_ui_theme.py
"""
from __future__ import annotations

import json
import re
import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

_tmp = tempfile.mkdtemp(prefix="recto-ui-")
os.environ["RECTO_DATA_DIR"] = _tmp
os.environ.setdefault("RECTO_AUTH_DISABLE", "1")

from recto.app import appearance                    # noqa: E402
from recto.app.ui import shell, skins               # noqa: E402

PASS = 0
FAIL = 0


def ok(cond, name):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS", name)
    else:
        FAIL += 1
        print("FAIL", name)


def case(fn):
    try:
        fn()
    except Exception:
        global FAIL
        FAIL += 1
        print("ERROR", fn.__name__)
        traceback.print_exc()
    return fn


# ---------------------------------------------------------------- token 层
@case
def test_builtin_skins_present():
    conf = skins.load_conf()
    alls = skins.all_skins(conf)
    for sid in ("paper", "slate", "mist", "sepia"):
        assert sid in alls, sid
    ok(True, "内置皮肤齐全")


@case
def test_skin_css_has_tokens():
    css = skins.skin_css()
    for var in ("--rc-bg", "--rc-surface", "--rc-fg", "--rc-accent",
                "--rc-line", "--rc-radius", "--rc-fs"):
        assert var + ":" in css, var
    ok("color-scheme" in css, "皮肤 CSS 生成全部 token")


@case
def test_dark_skin_sets_color_scheme():
    skins.save_conf({"skin": "slate"})
    css = skins.skin_css()
    assert "color-scheme:dark" in css
    ok(True, "深色皮肤声明 color-scheme")
    skins.save_conf({"skin": "paper"})


@case
def test_unknown_skin_falls_back():
    skins.save_conf({"skin": "does-not-exist"})
    sid, _tokens, _conf = skins.resolve()
    ok(sid == "paper", "未知皮肤退回默认")


@case
def test_font_scale_and_density_affect_css():
    skins.save_conf({"skin": "paper", "font_scale": 1.25, "density": "roomy"})
    css = skins.skin_css()
    ok("--rc-fs: 20.62px" in css or "--rc-fs: 20.63px" in css,
       "字号缩放写进 --rc-fs")
    ok("--rc-pad: 20px" in css, "疏密写进 --rc-pad")


@case
def test_values_are_clamped():
    out = skins.save_conf({"skin": "paper", "font_scale": 99, "radius_scale": -5})
    ok(out["font_scale"] <= 1.6 and out["radius_scale"] >= 0.0, "数值被夹到合法区间")


@case
def test_custom_css_sanitized():
    out = skins.save_conf({"skin": "paper",
                           "custom_css": "</style><script>x</script> @import url(x)"})
    css = out["custom_css"]
    ok("<" not in css and ">" not in css, "自定义 CSS 去掉尖括号")
    ok("@import" not in css, "自定义 CSS 挡掉 @import")


# ---------------------------------------------------------------- 外部皮肤
@case
def test_file_skin_loaded():
    d = Path(_tmp) / "skins"
    d.mkdir(parents=True, exist_ok=True)
    (d / "moss.json").write_text(json.dumps({
        "label": "苔",
        "base": "mist",
        "tokens": {"accent": "#3d6b3a", "bg": "#eef2ee"},
        "extra_css": ".rc-brand{letter-spacing:.08em}",
    }, ensure_ascii=False), "utf-8")
    skins.save_conf({"skin": "moss"})
    sid, tokens, _ = skins.resolve()
    assert sid == "moss", sid
    assert tokens["accent"] == "#3d6b3a"
    css = skins.skin_css()
    ok("letter-spacing:.08em" in css, "文件皮肤 + extra_css 进 skin.css")
    ok(tokens["surface"] == "#ffffff", "未覆盖的 token 沿用 base")


@case
def test_bad_skin_file_ignored():
    d = Path(_tmp) / "skins"
    (d / "broken.json").write_text("{not json", "utf-8")
    alls = skins.all_skins(skins.load_conf())
    ok("broken" not in alls, "坏皮肤文件被跳过")


@case
def test_custom_skin_from_conf():
    skins.save_conf({"skin": "paper",
                     "custom_skins": {"warm": {"label": "暖", "base": "paper",
                                               "tokens": {"accent": "#a3542a"}}}})
    alls = skins.all_skins(skins.load_conf())
    ok("warm" in alls and alls["warm"]["builtin"] is False, "自制皮肤进列表")
    ok(alls["warm"]["tokens"]["accent"] == "#a3542a", "自制皮肤改色生效")


@case
def test_custom_skin_bad_id_rejected():
    out = skins.save_conf({"skin": "paper",
                           "custom_skins": {"../evil": {"label": "x"}}})
    ok("../evil" not in out["custom_skins"], "非法皮肤 id 被丢弃")


# ---------------------------------------------------------------- 外壳
@case
def test_document_shape():
    html = shell.document(title="测试", body="<p>x</p>", active="sync", user="me")
    assert '<link rel="stylesheet" href="/assets/recto.css">' in html
    assert '<link rel="stylesheet" href="/assets/skin.css">' in html
    ok('aria-current="page"' in html, "外壳标出当前导航")
    ok("me" in html, "外壳带用户名")
    ok(html.count("rc-nav") >= 1, "外壳有导航")


@case
def test_nav_single_source():
    keys = [k for k, _l, _h in shell.NAV]
    ok(keys == ["library", "sync",
                "excerpts", "corpus", "appearance"], "导航顺序固定且唯一")
    ok(len(set(keys)) == len(keys), "导航 key 不重复")
    pane_keys = {k for k in keys if k in shell.PANE_KEYS}
    ok(pane_keys == {"library", "sync"},
       "面板 key 与导航 key 对得上")
    ok("reader" not in keys, "「阅读增强」已隐藏 (语料另有分区, 日志并入云端同步)")
    ok("home" not in keys, "概览已从导航里去掉")


@case
def test_active_matching():
    html = shell.document(title="x", body="", active="excerpts")
    ok('<a href="/admin/" aria-current="page">摘录</a>' in html,
       "摘录分区高亮")


@case
def test_auth_page_uses_same_assets():
    html = shell.auth_page(title="登录", body=shell.login_body())
    ok("/assets/skin.css" in html and "/assets/recto.css" in html,
       "登录页加载同一套样式")
    ok("rc-nav" not in html, "登录页不出现导航")
    ok('name="password"' in html, "登录页有密码框")


@case
def test_assets_whitelist():
    hit = shell.assets_css("recto.css")
    ok(hit is not None and hit[1].startswith("text/css"), "recto.css 可服务")
    ok(shell.assets_css("../secret") is None, "白名单外一律 404")


@case
def test_theme_color_tracks_skin():
    skins.save_conf({"skin": "slate"})
    html = shell.document(title="x", body="")
    ok('name="theme-color" content="#14161a"' in html, "浏览器栏色跟随皮肤")
    skins.save_conf({"skin": "paper"})


# ---------------------------------------------------------------- 外观 API
@case
def test_appearance_api_roundtrip():
    r = appearance.api_post({"skin": "mist", "font_scale": 1.1,
                             "density": "compact", "heading": "sans"})
    assert r["ok"]
    got = appearance.api_get()
    ok(got["current"] == "mist", "外观 API 写后能读回")
    ok(abs(got["conf"]["font_scale"] - 1.1) < 1e-6, "细调值被保存")
    ok("skins" in got and len(got["skins"]) >= 4, "外观 API 返回皮肤清单")


@case
def test_appearance_page_contains_controls():
    html = appearance.page_html("me")
    for marker in ('id="ap-save"', 'data-skin="paper"', 'id="ap-fs"',
                   'id="ap-css"', "ap-data"):
        assert marker in html, marker
    ok(True, "外观页含保存 / 皮肤卡 / 细调 / 自定义 CSS")


@case
def test_appearance_page_no_nav_mismatch():
    html = appearance.page_html("me")
    ok('aria-current="page">外观' in html, "外观页导航高亮")


@case
def test_skin_card_overrides_button_base():
    """皮肤卡是 <button>, 必须显式覆盖按钮基类 (recto.css §4) 的
    align-items:center / white-space:nowrap。

    否则 column flex 的交叉轴按内容居中 → 空 <i> 宽 0 → 色块塌成一条竖线
    (实测 .sw 宽 2px, 只剩边框)。"""
    css = appearance._CSS
    card = css[css.index(".skin-card{"):css.index(".skin-card:hover")]
    ok("align-items:stretch" in card, "皮肤卡交叉轴撑满 (不是按钮基类的 center)")
    ok("white-space:normal" in card, "皮肤卡允许换行 (不是按钮基类的 nowrap)")
    ok("justify-content:flex-start" in card, "皮肤卡主轴从顶部开始 (不是 center)")

    sw = css[css.index(".skin-card .sw{"):css.index(".skin-card .nm{")]
    ok("width:100%" in sw, "色块显式撑满卡片宽度")
    ok("flex:1 1 0" in sw, "色条以 0 为基准均分 (空元素也能分到宽度)")
    ok("height:100%" in sw, "色条撑满色块高度")


@case
def test_pane_css_hides_only_inactive():
    """面板显隐只能靠 :not(.is-on) —— 裸 [data-pane]{display:none} 会把
    激活的那块也一起藏掉, 表现成"导航点了没反应"。"""
    css = shell.assets_css("recto.css")[0].decode("utf-8")
    import re
    ok("[data-pane]:not(.is-on)" in css, "用 :not(.is-on) 隐藏非活动面板")
    bare = re.findall(r"\[data-pane\]\s*\{([^}]*)\}", css)
    ok(not any("display" in b for b in bare),
       "没有裸 [data-pane] 的 display 规则")
    ok(".row" in css and "display: flex" in css, "行布局仍在")


def _css() -> str:
    return shell.assets_css("recto.css")[0].decode("utf-8")


@case
def test_rail_actions():
    """书架页头的两个维护动作走「右侧悬浮操作轨」: 桌面竖排圆钮 + hover 出名字,
    窄屏回到页头横排。改 markup / CSS 任何一边都会在这里红。"""
    from recto.app import console

    css = _css()
    page = console._page_html("recto")

    ok('class="rc-rail"' in page, "页头用了 .rc-rail")
    ok(page.count("rc-rail-btn") >= 2, "两个圆钮")
    ok(page.count("rc-rail-name") >= 2, "每个圆钮都带名字 (hover 用)")
    ok("<svg" in page and "rc-rail-ico" in page, "圆钮里是图标不是文字")
    ok("rc-head-actions" not in page.split("data-pane=")[1][:2000],
       "书架页头不再用老的 .rc-head-actions 横排")

    ok(".rc-rail {" in css and "position: fixed" in css, "轨是悬浮的")
    ok("right: max(14px, env(safe-area-inset-right))" in css, "贴屏幕右侧 (留安全区)")
    ok("flex-direction: column" in css, "桌面竖排")
    ok(".rc-rail-btn .rc-ripple { display: none; }" in css,
       "圆钮关掉水波纹 (溢出要留给 hover 名字)")
    ok("overflow: visible;" in css, "圆钮允许名字溢出")
    ok(".rc-rail-name" in css and "opacity: 0" in css, "名字默认隐藏")
    ok(".rc-rail-btn:hover .rc-rail-name" in css, "hover 出名字")
    ok(".rc-rail-btn:focus-visible .rc-rail-name" in css, "键盘聚焦也出名字")

    # 窄屏段: 取消悬浮 + 名字常显
    narrow = css.split("@media (max-width: 860px)")[-1]
    ok("position: static;" in narrow and "flex-direction: row" in narrow,
       "窄屏回到页头横排")
    ok("opacity: 1;" in narrow, "窄屏名字常显")

    # 面板淡入不能带 transform —— 否则 fixed 派生效应, 轨会掉进内容列
    ok("transform" not in css.split("@keyframes rc-fade")[1].split("}")[0],
       "面板淡入只动 opacity (不给 fixed 后代建 containing block)")

    # 结果面板在别处时给回执条 (#out 在 sync 面板里)
    ok("function toast(" in console._JS, "有回执条实现")
    ok(".rc-toast" in css and "rc-toast-in" in css, "回执条有样式与入场动画")
    ok("pointer-events: none" in css.split(".rc-toast {")[1].split("}")[0],
       "回执条不挡点击")

    # rescan 忙态不能抹掉图标
    js = console._JS
    ok('btn.textContent = "扫描中' not in js, "忙态不用 textContent (会抹掉图标)")
    ok('querySelector(".rc-rail-name")' in js and "is-busy" in js,
       "忙态改名字 + 图标转")


@case
def test_buttongroup_stagger_is_gated():
    """按钮群错落必须由 :has(≥2 个按钮) 闸住 ——
    否则全站 (含卡片里孤零零一个按钮) 全被改成空心描边框。"""
    css = _css()
    ok(":where(:has(" in css.replace("\n", " "), "错落规则由 :has 判定是否成组")
    ok("~ :is(button, a.btn)" in css, ":has 里要求至少两个按钮/链接")
    ok("nth-of-type(4n+2)" in css and "nth-of-type(4n+3)" in css
       and "nth-of-type(4n+4)" in css, "四档底色轮换存在")
    ok(":not(.btn-link)" in css, "行内链接按钮 (复制/书票) 不参与轮换")


@case
def test_buttongroup_stagger_uses_tokens():
    """四档底色全部来自皮肤 token (含深色皮肤自动跟随), 不写死色值。"""
    css = _css()
    start = css.index("按钮群错落")
    block = css[start:start + 2600]
    ok("--rc-surface" in block and "--rc-secondary-container" in block
       and "--rc-accent-soft" in block, "四档底色用 token")
    ok("color-mix(in srgb" in block, "混色走 color-mix, 换肤自动跟随")
    import re
    hard = re.findall(r"#[0-9a-fA-F]{3,8}\b", block)
    ok(not hard, f"错落块里没有硬编码颜色 (found {hard[:3]})")


@case
def test_danger_and_submit_roles():
    """破坏性动作 = 红字红边空心; 组里的 submit = 这一组的主操作。"""
    css = _css()
    ok("button.danger, .btn.danger" in css, "全局 .danger 有样式 (不再靠页面局部)")
    ok(".filter-bar > button[type=\"submit\"]" in css, "筛选条的提交按钮单列规则")
    ok("var(--rc-err)" in css, "危险色走 token")


def _corpus_css() -> str:
    """语料分区的样式表现在是仓内文件 (以前是反代时注入的字符串)。"""
    from recto.app import corpus

    return (corpus._UI_DIR / "corpus.css").read_text("utf-8")


@case
def test_corpus_does_not_duplicate_stagger():
    """按钮错落只有一处实现 (recto.css)。语料页只保留上游特有的表格规则,
    否则两边改一处忘一处, 皮肤一变就对不上。"""
    bridge = _corpus_css()
    ok(".row > button:not(.primary)" not in bridge, "语料页不再重复通用错落规则")
    ok("table.models .mdl-ops button" in bridge, "上游表格操作群规则仍在")
    ok(".filter-bar" not in bridge, "语料页不重复筛选条规则")
    ok(".rc-corpus" in bridge, "语料规则全部作用域限定 (不污染别的分区)")



@case
def test_tables_borderless_banded():
    """表格口径: 一根线都不画, 行区间只靠隔行底色区分。

    这条钉住两件事 —— ① 表格相关规则里不许再出现 border 声明;
    ② 隔行底色那条 (nth-child(even)) 必须存在, 否则表格退回"一坨没有分隔的字"。
    """
    css = _css()
    # 取「6 表格」那一节 (到「7 徽标」为止) 单看, 别被其它组件的 border 干扰
    start = css.rindex("6 表格")   # 文件头有一份目录, 取真正的那一节
    end = css.index("7 徽标", start)
    sec = re.sub(r"/\*.*?\*/", "", css[start:end], flags=re.S)  # 先剥注释, 只看真声明
    ok(not re.search(r"border-(?:top|right|bottom|left)\s*:", sec), "没有任何单边描边")
    ok(" solid" not in sec and " dashed" not in sec, "没有任何实线/虚线")
    ok("border: 0" in sec, "边框被显式清零 (继承浏览器默认线宽也归零)")
    ok("nth-child(even)" in sec, "隔行底色规则在")
    ok("--rc-state" in sec and "--rc-state-strong" in sec,
       "斑马纹与悬停都用皮肤状态层 token (换肤自动跟随)")
    ok(".bordered" not in sec, "`.bordered` 的竖线规则已移除 (类名留作空壳)")
    ok("text-align: left" in sec, "对齐方式还在 (列关系靠对齐表达)")


@case
def test_corpus_tables_borderless():
    """语料页: 上游 CSS 的 border-bottom 必须被清掉, 否则口径不一致。"""
    bridge = _corpus_css()
    ok("border: 0 !important" in bridge, "上游表格边框被清掉")
    ok("nth-child(even)" in bridge, "语料页也上隔行底色")
    ok("tr.sel" in bridge, "选中行 (tr.sel) 有单独强调底色")
    ok("var(--rc-" in bridge, "语料样式走皮肤 token (换肤跟着变)")


@case
def test_brand_name():
    """品牌名一律中英文并写「云阅 Recto」: 顶栏 / 浏览器标题 / 页脚 / 登录页。"""
    ok(shell.BRAND == "云阅", "中文名是 云阅")
    ok(shell.BRAND_EN == "Recto", "英文名是 Recto")
    ok(shell.BRAND_FULL == "云阅 Recto", "展示名 = 云阅 Recto (中英文并写)")

    page = shell.document(title="书架", body="<p>x</p>", active="library")
    ok("<title>书架 · 云阅 Recto</title>" in page, "分区标题 = `<分区> · 云阅 Recto`")
    ok('rc-brand-en">Recto</span>' in page, "顶栏品牌中英文并写")
    ok('rc-foot">云阅 Recto' in page, "页脚用 云阅 Recto")

    bare = shell.document(title="", body="")
    ok("<title>云阅 Recto</title>" in bare, "没有分区名时标题就是 云阅 Recto")

    login = shell.auth_page(body="<p>x</p>")
    ok("<title>云阅 Recto</title>" in login, "登录页标题 云阅 Recto")
    named = shell.auth_page(title="登录", body="")
    ok("<title>登录 · 云阅 Recto</title>" in named, "登录页带分区时同样拼接")
    ok("<title>云阅 Recto</title>" in shell.auth_page(title="云阅 Recto", body=""),
       "传入完整品牌名时不重复拼接")


@case
def test_brand_no_subtitle():
    """顶栏品牌只要「云阅 Recto」—— 默认不再带「本地书库 ⇄ 云端阅读」说明小字。"""
    page = shell.document(title="书架", body="<p>x</p>", active="library")
    ok("<small>" not in page.split('<div class="rc-top"')[1].split("</div>")[0],
       "顶栏品牌里没有 small 小字")
    ok("本地书库" not in page, "顶栏不再出现「本地书库 ⇄ 云端阅读」")
    ok("<small></small>" not in shell.brand(), "空 small 不渲染成空标签")
    ok("<small>自己写的</small>" in shell.brand("自己写的"), "显式传 small 仍然渲染")


@case
def test_logo_assets():
    """标志: `logo.png` 的透明派生图必须入库, 并被 /assets/ 白名单放行。"""
    assets = shell.ASSET_DIR
    for name in ("logo-mark.png", "logo-full.png", "favicon.png", "apple-touch-icon.png"):
        p = assets / name
        ok(p.exists() and p.stat().st_size > 200,
           f"{name} 已生成 ({p.stat().st_size if p.exists() else 0}B)")
        hit = shell.assets_css(name)
        ok(hit is not None and hit[1] == "image/png", f"{name} 能被 /assets/ 取到 (image/png)")
        ok(hit is not None and hit[0][:8] == b"\x89PNG\r\n\x1a\n", f"{name} 是合法 PNG")

    ok(shell.assets_css("../config.toml") is None, "白名单外的路径取不到 (防穿越)")
    ok(shell.assets_css("recto.css")[1].startswith("text/css"), "样式表仍然可服务")


@case
def test_logo_in_shell():
    """顶栏 = 图徽 + 文字品牌; 登录页 = 完整标志; 两个外壳都声明 favicon。"""
    page = shell.document(title="书架", body="", active="library")
    ok('/assets/logo-mark.png' in page, "顶栏用 logo-mark.png")
    ok('alt=""' in page.split('class="rc-brand"')[1].split("</a>")[0],
       "图徽 alt 为空 (文字品牌已读, 不重复念)")
    wh = shell._png_size(shell.LOGO_MARK)
    ok(wh is not None and abs(wh[0] / wh[1] - 2.7) < 0.06,
       "图徽比例 ~2.7:1 (宽幅图徽, 不是方形)" + (f" · {wh}" if wh else ""))
    ok(wh is not None and f'width="{wh[0]}" height="{wh[1]}"' in page,
       "图徽带固有尺寸 (与文件实际像素一致, 防加载时跳动)")
    ok('rc-brand-en">Recto</span>' in page, "图徽旁边仍是中英文品牌名")
    ok('<link rel="icon" type="image/png" href="/assets/favicon.png">' in page, "顶层页面有 favicon")
    ok('rel="apple-touch-icon"' in page, "顶层页面有 apple-touch-icon")

    login = shell.auth_page(body="<p>x</p>")
    ok('rel="icon"' in login, "登录页也有 favicon")
    ok('/assets/logo-mark.png' not in login, "登录页不放小图徽")

    body = shell.login_body(next_url="/", ttl_days=7)
    ok('class="rc-lockup"' in body, "登录卡片顶部是完整标志")
    ok('/assets/logo-full.png' in body, "登录卡片用 logo-full.png")
    ok('aria-label="云阅 Recto"' in body, "完整标志带可读名称")
    ok('rc-brand' not in body, "登录卡片不再重复一遍文字品牌")


@case
def test_logo_dark_skin_invert():
    """深色皮肤把标志反相成白色; 浅色不动。"""
    light = skins.skin_css({"skin": "material"})
    dark = skins.skin_css({"skin": "material-dark"})
    ok("invert(1)" not in light, "浅色皮肤不反相")
    ok("html img.rc-logo{filter:invert(1);}" in dark, "深色皮肤反相标志")
    ok("color-scheme:dark" in dark, "深色皮肤仍是 color-scheme:dark")


@case
def test_logo_freshness():
    """派生图不能比源图旧 —— 换了 logo.png 忘了重跑 tools/make_logo.py 要报出来。"""
    src = Path(__file__).resolve().parents[1] / "logo.png"
    if not src.exists():
        ok(True, "仓库里没有 logo.png, 跳过新鲜度检查")
        return
    stale = [n for n in ("logo-mark.png", "logo-full.png", "favicon.png", "apple-touch-icon.png")
             if not (shell.ASSET_DIR / n).exists()
             or (shell.ASSET_DIR / n).stat().st_mtime < src.stat().st_mtime]
    ok(not stale, "派生图不比 logo.png 旧" + (f" (陈旧: {stale})" if stale else ""))


@case
def test_logo_css():
    """标志的版式: 只按高度约束 (不拉伸), 手机缩一档, 登录页 64px。"""
    css = (shell.ASSET_DIR / "recto.css").read_text(encoding="utf-8")
    block = css.split(".rc-brand .rc-logo {")[1].split("}")[0]
    ok("height: 22px" in block, "顶栏图徽 22px 高")
    ok("width: auto" in block, "宽度自动 (不拉伸)")
    ok("height: 64px" in css.split(".rc-logo-full {")[1].split("}")[0], "登录页标志 64px 高")
    ok(".rc-lockup" in css, "登录页标志有独立容器")
    ok(css.count(".rc-brand .rc-logo { height:") >= 2, "手机/极窄机各缩一档")


def main():
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
