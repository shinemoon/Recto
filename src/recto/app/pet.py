"""书宠 (陪读精灵) —— 云阅 Recto 的原生分区。

业务在 `recto.pet.config` (模型接入配置 / 人格 / 陪伴参数), 本层只做路径分派。

    GET  /pet/                 页面
    GET  /pet/static/*         页面资产 (pet.css / pet.js)
    *    /pet/api/*            接口

与语料分区同一套外壳 / 皮肤 / 登录 —— 登录由 Recto 统一处理。
"""
from __future__ import annotations

import re
import traceback
import urllib.parse
from pathlib import Path

from recto.app.ui import shell
from recto.pet import charsheet as cs
from recto.pet import chronicle as chronicle_mod
from recto.pet import companion as companion_mod
from recto.pet import config as petcfg
from recto.pet import growth as growth_mod
from recto.pet import life as life_mod
from recto.pet import lore as lore_mod

PREFIX = "/pet"

_UI_DIR = Path(__file__).resolve().parent / "ui" / "assets"

# 只放行这两份资产
_ASSETS = {
    "pet.css": "text/css; charset=utf-8",
    "pet.js": "text/javascript; charset=utf-8",
}


def page_html() -> str:
    """整页 HTML: 外壳 + 书宠正文区块。"""
    frag = (_UI_DIR / "pet.html").read_text("utf-8")
    return shell.document(
        title="书宠",
        body=frag,
        active="pet",
        body_class="rc-pet-page",
        head_extra=(
            '<link rel="stylesheet" href="/pet/static/pet.css">\n'
            '<script src="/pet/static/pet.js" defer></script>'
        ),
    )


def _body(h) -> dict:
    """读 JSON 请求体 (没有体 / 空体 / 坏体 → {})。"""
    try:
        return h._read_json() or {}  # noqa: SLF001 — 与 console.Handler 同一套工具
    except Exception:  # noqa: BLE001
        return {}


# ------------------------------------------------------------------ 接口路由
def _api(h, path: str, query: str) -> tuple[int, dict]:
    """`/pet/api/...` (已去掉前缀) → (状态码, JSON)。"""
    method = (h.command or "GET").upper()

    if path == "/presets":
        return petcfg.presets()

    if path == "/status":
        return petcfg.status()

    if path == "/config":
        if method == "POST":
            body = _body(h)
            code, payload = petcfg.set_config(body)
            # 名字只有一处 —— 人格那边改名字, 人物卡那边也得跟着改 (反之亦然)。
            persona = body.get("persona") or {}
            if isinstance(persona, dict) and persona.get("name"):
                ch = cs.load_char()
                if ch.get("name") != persona["name"]:
                    cs.set_name(ch, str(persona["name"]))
                    payload["char"] = cs.view(ch)
            return code, payload
        return petcfg.get_config()

    if path == "/llm/test":
        return petcfg.llm_test(_body(h))

    # ── 人物属性体系 (武侠) ─────────────────────────
    if path == "/char":
        return 200, cs.view(cs.load_char())

    if path == "/char/schema":
        s = cs.schema()
        # 成长规则跟体系定义一起发 —— 前端不用另抄一份
        s["growth"] = growth_mod.rules()
        return 200, s

    # ── 时序: 故事内第几天 / 几岁 / 是否该退隐 ───────
    if path == "/char/life":
        return 200, life_mod.view(cs.load_char())

    # 传承: 上一代已退隐 (或到岁数) + 读者确认 → 新人从 12 岁起
    if path == "/char/inherit":
        if method != "POST":
            return 405, {"error": "method not allowed"}
        body = _body(h)
        if body.get("confirm") is not True:
            return 400, {"error": "传承要读者自己点头: 请带 confirm=true",
                         "life": life_mod.view(cs.load_char())}
        ch = cs.load_char()
        if life_mod.stage(ch) != "retiring" and not life_mod.ready_to_retire(ch):
            return 409, {"error": "还没到退隐的时候", "life": life_mod.view(ch)}
        out = life_mod.inherit(ch, str(body.get("heir_type") or "disciple"),
                               str(body.get("name") or ""))
        cs.save_char(out["char"])
        chronicle_mod.reset_after_inherit()      # 新一代的故事时钟从零开始
        return 200, {"ok": True, "heir_type": out["heir_type"],
                     "record": out["record"], "archive": out["archive"],
                     "char": cs.view(out["char"]), "life": life_mod.view(out["char"])}

    if path == "/lineage":
        ch = cs.load_char()
        return 200, {"generation": int(ch.get("generation") or 1),
                     "current": ch.get("name") or "",
                     "lineage": list(ch.get("lineage") or []),
                     "life": life_mod.view(ch)}

    if path == "/char/alloc":
        if method != "POST":
            return 405, {"error": "method not allowed"}
        body = _body(h)
        ch = cs.load_char()
        return cs.alloc_point(ch, str(body.get("key") or ""), int(body.get("n") or 1))

    if path == "/char/school":
        if method != "POST":
            return 405, {"error": "method not allowed"}
        ch = cs.load_char()
        return cs.choose_school(ch, str(_body(h).get("school") or ""))

    if path == "/char/trait":
        if method != "POST":
            return 405, {"error": "method not allowed"}
        ch = cs.load_char()
        return cs.grant_trait(ch, str(_body(h).get("trait") or ""))

    if path == "/char/reset":
        if method != "POST":
            return 405, {"error": "method not allowed"}
        return cs.reset_char()

    if path == "/char/name":
        if method != "POST":
            return 405, {"error": "method not allowed"}
        ch = cs.load_char()
        return cs.set_name(ch, str(_body(h).get("name") or ""))

    # 成长接口 (陪读 → 修为/等级/潜能/经脉)。现在还没接阅读器, 手动喂分钟即可验。
    if path == "/char/grow":
        if method != "POST":
            return 405, {"error": "method not allowed"}
        body = _body(h)
        ch = cs.load_char()
        gain = growth_mod.award_minutes(ch, int(body.get("minutes") or 0))
        cs.save_char(ch)
        return 200, {"ok": True, "gain": gain, "char": cs.view(ch)}

    # 研讨: 手动长一门功夫
    if path == "/char/skill":
        if method != "POST":
            return 405, {"error": "method not allowed"}
        body = _body(h)
        ch = cs.load_char()
        r = growth_mod.skill_boost(ch, str(body.get("skill") or ""),
                                   int(body.get("levels") or 1))
        if not r.get("ok"):
            return 400, r
        cs.save_char(ch)
        return 200, {"ok": True, "char": cs.view(ch)}

    # 陪读源: 阅读器时长 → 书宠修为。现在由心跳自动折算, 这里是手动触发 + 现状。
    if path == "/companion":
        if method == "POST":
            gain = companion_mod.feed_now()
            return 200, {"ok": True, "gain": gain, "char": cs.view(cs.load_char())}
        return 200, {**companion_mod.companion_state(), "tick": companion_mod.tick_state()}

    # ── 后台推演 (读账本 → 判成长 → 走一趟) ──────────
    if path == "/chronicle":
        if method == "POST":
            body = _body(h)
            out = chronicle_mod.run(trigger="manual", force=bool(body.get("force")),
                                    dry=bool(body.get("dry")))
            return 200, out
        return 200, chronicle_mod.state_view()

    if path == "/chronicle/run":
        if method != "POST":
            return 405, {"error": "method not allowed"}
        body = _body(h)
        return 200, chronicle_mod.run(trigger="manual",
                                      force=bool(body.get("force")),
                                      dry=bool(body.get("dry")))

    if path == "/adventures":
        q = urllib.parse.parse_qs(urllib.parse.urlparse(getattr(h, "path", "")).query)
        try:
            limit = max(1, min(100, int((q.get("limit") or ["20"])[0])))
        except ValueError:
            limit = 20
        return 200, {"adventures": chronicle_mod.adventures(limit=limit),
                     "cycles": chronicle_mod.cycles(limit=8)}

    # 补写: 上一回判成长落了地、故事却没写出来的那一趟 (不重判成长)
    if path == "/adventure/backfill":
        if method != "POST":
            return 405, {"error": "method not allowed"}
        body = _body(h)
        cid = body.get("cycle")
        story = chronicle_mod.backfill_adventure(int(cid) if cid else None)
        if not story:
            return 400, {"error": "这一回补不出来",
                         "detail": chronicle_mod.load_state().get("last_story_error") or "",
                         "llm": lore_mod.LAST_ERROR}
        return 200, {"ok": True, "story": story,
                     "adventures": chronicle_mod.adventures(limit=8)}

    if path == "/lore":
        return 200, {"stats": lore_mod.stats(), "books": lore_mod.all_cards()}

    if path == "/lore/refresh":
        if method != "POST":
            return 405, {"error": "method not allowed"}
        body = _body(h)
        key = str(body.get("book") or "")
        if not key:
            return 400, {"error": "缺少 book"}
        card = lore_mod.build_book(key, force=True,
                                   search_mode="auto" if body.get("search", True) else "off")
        if not card:
            return 400, {"error": "无法为该书写卡 (缺书名或模型不可用)"}
        for pend in lore_mod.pending_chapters(key, limit=int(body.get("chapters") or 3)):
            lore_mod.build_chapter(key, int(pend["chapter"]))
        return 200, {"ok": True, "card": lore_mod.card(key)}

    # 模型库
    if path == "/models":
        if method == "POST":
            return petcfg.models_set(_body(h))
        return petcfg.models_get()
    if path == "/models/apply":
        return petcfg.models_apply(_body(h))
    m = re.fullmatch(r"/models/([^/]+)/test", path)
    if m:
        if method != "POST":
            return 405, {"error": "method not allowed"}
        return petcfg.models_test(urllib.parse.unquote(m.group(1)))
    m = re.fullmatch(r"/models/([^/]+)", path)
    if m:
        if method != "DELETE":
            return 405, {"error": "method not allowed"}
        return petcfg.models_delete(urllib.parse.unquote(m.group(1)))

    return 404, {"error": "not found"}


# ------------------------------------------------------------------ 入口
def dispatch(h) -> bool:
    """把 `/pet` 下的请求处理掉。返回 True = 已处理。"""
    u = urllib.parse.urlparse(h.path)
    path = u.path
    if not (path == PREFIX or path.startswith(PREFIX + "/")):
        return False
    rest = path[len(PREFIX):] or "/"

    # 页面
    if rest in ("/", ""):
        h._send(200, page_html().encode("utf-8"), "text/html; charset=utf-8")  # noqa: SLF001
        return True

    # 资产
    if rest.startswith("/static/"):
        name = rest[len("/static/"):]
        ctype = _ASSETS.get(name)
        if not ctype:
            h._send(404, b"not found", "text/plain; charset=utf-8")  # noqa: SLF001
            return True
        try:
            data = (_UI_DIR / name).read_bytes()
        except OSError:
            h._send(404, b"not found", "text/plain; charset=utf-8")  # noqa: SLF001
            return True
        h._send(200, data, ctype)  # noqa: SLF001
        return True

    # 接口
    if rest.startswith("/api/"):
        api_path = rest[len("/api"):]
        try:
            code, payload = _api(h, api_path, u.query)
        except Exception as e:  # noqa: BLE001 — 单个接口故障不许带挂整个服务
            code, payload = 500, {"error": "internal_error", "detail": f"{type(e).__name__}: {e}"}
        h._json(code, payload)  # noqa: SLF001
        return True

    # 其它 (老书签 / 手误) → 回分区首页
    h._redirect(PREFIX + "/")  # noqa: SLF001
    return True
