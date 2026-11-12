"""后台推演 —— 书宠定期"自己去读账本, 自己上路"。

**它不做的事**: 不跟读者说话。书宠在阅读器里一句话都不说; 它的存在方式是
"隔一段时间自己翻一遍账本, 长一点本事, 然后出门走一趟"。所以这里没有对话、
没有提示、没有浮窗 —— 只有一个定时任务和一份留痕。

**它做的三件事** (一次 run = 一个"回合"):

  1. **取证据** —— 这段时间的读书记录 (分钟 / 推进章数 / 划线书签)、
     语料遭遇计数 (``encounters``)、书籍知识卡 (``lore``)、上一回的结尾
  2. **判成长** —— 把证据交给模型, 按 `skills/chronicle.md` 的口径给数;
     代码只做**夹逼** (上限 / 只增不减 / 技能白名单), 不做判断
  3. **走一趟** —— 用书籍母题 + 语料词 + 刚得的成长, 按 `skills/adventure.md`
     写一回游历, 落进 ``adventure`` 表

**幂等**: 分钟的"已折算"口径记在 ``chronicle.json`` 的 ``credit`` 里
(``书|天`` → 已计入的分钟), 所以重跑同一段时间不会重复给修为。
和 ``companion`` 那条心的关系: 那条只累计分钟 / 长技能 / 通经脉,
**不给修为** —— 修为归这里判 (否则会双计)。

**没有活动就不烧 token**: 证据里既没有阅读分钟、也没有语料遭遇时直接
``status="idle"`` 返回, 不调模型、不写 cycle 行。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from recto.core import clock
from recto.pet import charsheet as cs
from recto.pet import config as petcfg
from recto.pet import encounters, guidance, growth, jsonout, life, lore, store

log = logging.getLogger("recto.pet.chronicle")

DEFAULT_CFG: dict[str, Any] = {
    "enabled": True,
    "interval_min": 120,        # 多久推演一次
    "lookback_hours": 48,       # 每次看多长的窗口 (有 last_at 时以 last_at 为界)
    "min_minutes": 3,           # 少于这么多分钟且无推进 → 视为没有活动
    "max_lore_books": 3,        # 一回合最多为几本书立卡
    "max_lore_chapters": 4,     # 一回合最多补几条章节储备
    "search": True,             # 建卡时是否联网检索
    "keep_adventures": 200,     # 游历保留条数
    "max_exp_per_minute": 6,    # 修为上限 = 分钟 × 这个系数 (兜底)
    "max_exp_per_day": 320,     # 修为上限 = 故事内天数 × 这个数 (一回合的主口径)
    "days_per_adventure": 1,    # 攒够几个故事内天才走一趟 (一小时阅读 = 一天)
}

STATE_NAME = "chronicle.json"

_LOCK = threading.Lock()
_RUN: dict[str, Any] = {"thread": None, "stop": False, "at": 0.0, "runs": 0,
                        "last": None, "error": None, "running": False}
_TICK_SEC = float(os.environ.get("RECTO_CHRONICLE_TICK_SEC", "60") or 60)


# ------------------------------------------------------------------ 配置

def cfg() -> dict[str, Any]:
    out = dict(DEFAULT_CFG)
    try:
        raw = (petcfg.load_config() or {}).get("chronicle") or {}
    except Exception:  # noqa: BLE001
        raw = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if k in DEFAULT_CFG and isinstance(v, (int, float, bool, str)):
                out[k] = v
    env = os.environ.get("RECTO_CHRONICLE_INTERVAL")
    if env:
        try:
            out["interval_min"] = max(5, int(float(env)))
        except ValueError:
            pass
    out["interval_min"] = max(5, int(out["interval_min"]))
    out["lookback_hours"] = max(1, int(out["lookback_hours"]))
    out["min_minutes"] = max(0, int(out["min_minutes"]))
    return out


def enabled() -> bool:
    return bool(cfg().get("enabled"))


# ------------------------------------------------------------------ 状态

def state_path() -> Path:
    return Path(store.pet_dir()) / STATE_NAME


def load_state() -> dict[str, Any]:
    try:
        d = json.loads(state_path().read_text("utf-8"))
        if isinstance(d, dict):
            d.setdefault("credit", {})
            d.setdefault("progress", {})
            return d
    except (OSError, ValueError):
        pass
    return {"credit": {}, "progress": {}, "cycle": 0, "adventure_no": 0}


def save_state(st: dict[str, Any]) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), "utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, p)


def _prune_credit(st: dict[str, Any], days: int = 45) -> None:
    cut = clock.fmt_sec(time.time() - days * 86400, "%Y-%m-%d")
    cr = st.get("credit") or {}
    st["credit"] = {k: v for k, v in cr.items() if k.rsplit("|", 1)[-1] >= cut}


# ------------------------------------------------------------------ 证据

def _rs_conn():
    from recto.services import reader_state as rs

    return rs.conn()


def _char_brief(ch: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": ch.get("name"), "level": int(ch.get("level") or 1),
        "exp": int(ch.get("exp") or 0), "fame": int(ch.get("fame") or 0),
        "morality": int(ch.get("morality") or 0),
        "school": ch.get("school") or "", "vein": int(ch.get("vein") or 0),
        "rank": cs.rank_of(int(ch.get("level") or 1)).get("name"),
        "attrs": cs.effective_attrs(ch),
        "skills": dict(ch.get("skills") or {}),
        "traits": list(ch.get("traits") or []),
        "minutes": int(ch.get("minutes") or 0),
        # 时序: 故事内活到第几天 / 几岁 (退隐与节奏都看这个)
        "story_day": life.story_day(ch),
        "age": life.age_years(ch),
        "age_label": life.age_label(ch),
        "generation": int(ch.get("generation") or 1),
    }


def gather(st: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    """这段窗口的读书记录 (只算**没被上一回合计入过**的部分)。"""
    c = cfg()
    now = now or time.time()
    last = clock.parse(st.get("last_at") or "")
    floor_ts = now - c["lookback_hours"] * 3600
    from_ts = max(floor_ts, last.timestamp() if last else floor_ts)
    from_day = clock.fmt_sec(from_ts, "%Y-%m-%d")
    from_iso = clock.fmt_sec(from_ts, "%Y-%m-%dT%H:%M:%S")
    credit = dict(st.get("credit") or {})
    progress = dict(st.get("progress") or {})

    reading: list[dict[str, Any]] = []
    try:
        rc = _rs_conn()
        rows = store.rows(
            rc,
            "SELECT book, day, minutes, book_title t, book_author a FROM rtime"
            " WHERE day >= ?", (from_day,))
        prog = {r["book"]: r for r in store.rows(rc, "SELECT * FROM rprogress")}
        note_rows = store.rows(
            rc, "SELECT book, COUNT(*) n FROM rnote WHERE created_at >= ? GROUP BY book",
            (from_iso,))
        bm_rows = store.rows(
            rc, "SELECT book, COUNT(*) n FROM rbookmark WHERE created_at >= ? GROUP BY book",
            (from_iso,))
        titles = {}
        for r in store.rows(rc, "SELECT key, title, author FROM rbook"):
            titles[r["key"]] = r
    except Exception as e:  # noqa: BLE001
        log.warning("推演 · 读阅读记录失败: %s", e)
        rows, prog, note_rows, bm_rows, titles = [], {}, [], [], {}

    # 分钟: 按 (书|天) 的去重口径算增量 —— 同一段时间重跑不会重复计
    per_book: dict[str, dict[str, Any]] = {}
    for r in rows:
        book = r["book"]
        key = f"{book}|{r['day']}"
        new_min = max(0, int(r["minutes"] or 0) - int(credit.get(key) or 0))
        slot = per_book.setdefault(book, {"minutes": 0, "title": "", "author": ""})
        slot["minutes"] += new_min
        slot["title"] = slot["title"] or (r.get("t") or "")
        slot["author"] = slot["author"] or (r.get("a") or "")

    notes = {r["book"]: int(r["n"] or 0) for r in note_rows}
    marks = {r["book"]: int(r["n"] or 0) for r in bm_rows}
    total_new = 0
    for book, slot in per_book.items():
        new_min = int(slot["minutes"])
        cur = prog.get(book) or {}
        prev = progress.get(book) or {}
        ch_to = int(cur.get("chapter") or 0)
        ch_from = int(prev.get("chapter") or ch_to)
        pct_to = round(float(cur.get("percent") or 0) * 100, 3)
        # 上一回合没记过这本书的进度 → 起点不是 0, 而是"未知" (第一回合)。
        # 把它当 0 会虚报"推进了几十个百分点", 模型就会给不该给的加成。
        pct_from = pct_to if not prev else round(float(prev.get("percent") or 0) * 100, 3)
        chapters = max(0, ch_to - ch_from)
        title = (titles.get(book) or {}).get("title") or slot.get("title") or ""
        author = (titles.get(book) or {}).get("author") or slot.get("author") or ""
        if new_min <= 0 and chapters <= 0 and not notes.get(book) and not marks.get(book):
            continue
        # 书已经删了 (只剩时长快照里的占位名) → 不进判定: 书宠没法从
        # "已删除的书"里读出任何母题, 拿它当证据只会污染判词。
        if not title or title == "已删除的书":
            continue
        total_new += new_min
        reading.append({
            "book": book, "title": title, "author": author,
            "minutes": new_min, "chapters": chapters,
            "chapter_from": ch_from, "chapter_to": ch_to,
            "percent_from": pct_from, "percent_to": pct_to,
            "progress_known": bool(prev),
            "notes": notes.get(book, 0), "bookmarks": marks.get(book, 0),
            "lore": {},
        })
    reading.sort(key=lambda x: -x["minutes"])
    del reading[8:]      # 一回合最多看 8 本, 别把提示词塞满
    window = {"from": from_iso, "to": clock.fmt_sec(now, "%Y-%m-%dT%H:%M:%S"),
              "hours": round((now - from_ts) / 3600, 1)}
    return {"window": window, "reading": reading, "total_minutes": total_new,
            "from_ts": from_ts, "from_day": from_day,
            "encounters": encounters.recent(days=max(2, int(window["hours"] // 24) + 2),
                                            limit=40)}


# ------------------------------------------------------------------ 判定

def _fallback(ev: dict[str, Any]) -> dict[str, Any]:
    """模型不可用时的保底判定 (只按分钟, 与旧口径同比例)。"""
    minutes = int(ev.get("total_minutes") or 0)
    chapters = sum(int(r.get("chapters") or 0) for r in ev.get("reading") or [])
    cap = growth.MAX_EXP_PER_CYCLE
    exp = min(cap, minutes * growth.EXP_PER_MINUTE)
    return {
        "assessment": {"depth": "稳读" if minutes >= 20 else "浅尝",
                       "breadth": "散读" if len(ev.get("reading") or []) > 1 else "专一",
                       "one_line": f"读了 {minutes} 分钟" if minutes else "这段时间没有翻书"},
        "growth": {"exp": exp, "pot": 1 if chapters >= 5 else 0,
                   "fame": min(20, minutes // 30), "morality": 0,
                   "skills": {}, "trait": "",
                   "reason": f"模型不可用, 按分钟保底: {minutes} 分钟 × "
                             f"{growth.EXP_PER_MINUTE} 修为/分钟"},
        "next_hint": "",
        "_fallback": True,
    }


def clamp(j: dict[str, Any], ev: dict[str, Any]) -> dict[str, Any]:
    """把模型的判定夹进规矩里 (上限 / 只增不减 / 技能白名单 / 性情可授性)。

    这些上限在 `skills/chronicle.md` 里也写了一份 —— 那是给模型看的, 这里是
    真的执行的地方。两边不一致时**以代码为准** (模型给的数一律被夹)。
    """
    g = j.get("growth") if isinstance(j.get("growth"), dict) else {}
    minutes = int(ev.get("total_minutes") or 0)
    days = int(ev.get("days") or 0)
    c = cfg()
    per_day = int(c.get("max_exp_per_day") or 320)
    if days > 0:
        cap = min(growth.MAX_EXP_PER_CYCLE, days * per_day)
    else:
        cap = min(growth.MAX_EXP_PER_CYCLE, minutes * int(c.get("max_exp_per_minute") or 6))
    if minutes <= 0:
        cap = 0
    out: dict[str, Any] = {
        "exp": jsonout.as_int(g.get("exp"), 0, 0, cap),
        "pot": jsonout.as_int(g.get("pot"), 0, 0, growth.MAX_POT_PER_CYCLE),
        "fame": jsonout.as_int(g.get("fame"), 0, 0, growth.MAX_FAME_PER_CYCLE),
        "morality": jsonout.as_int(g.get("morality"), 0, -5, 5),
        "skills": {},
        "trait": "",
        "reason": jsonout.as_str(g.get("reason"), 200),
        "cap": cap,
    }
    if minutes <= 0:
        out.update({"exp": 0, "pot": 0, "fame": 0})
    skills = g.get("skills") if isinstance(g.get("skills"), dict) else {}
    for sid in list(skills.keys())[: growth.MAX_SKILLS_PER_CYCLE]:
        if str(sid) in cs.SKILL_BY_ID and jsonout.as_int(skills.get(sid), 0) > 0:
            out["skills"][str(sid)] = 1
    tid = jsonout.as_str(g.get("trait"), 24)
    if tid and tid in cs.TRAIT_BY_ID and len(out.get("skills") or {}) < 2:
        info = cs.traits_info(cs.load_char())
        pts = cs.TRAIT_COLORS[cs.TRAIT_BY_ID[tid]["color"]]["points"]
        if (tid not in info["owned"] and info["left_count"] > 0
                and info["spent"] + pts <= info["max_points"]):
            out["trait"] = tid
    j["growth"] = out
    return j


def judge(ev: dict[str, Any], ch: dict[str, Any]) -> dict[str, Any]:
    """交给模型判断成长; 失败 → 保底规则。"""
    payload = {
        "window": ev["window"],
        "char": _char_brief(ch),
        "time": {"story_day": life.story_day(ch),
                 "day_no": life.story_day(ch) + 1,
                 "days_advanced": int(ev.get("days") or 0),
                 "age": life.age_years(ch),
                 "minutes_per_story_day": life.cfg()["minutes_per_day"],
                 "retire_age": life.cfg()["retire_age"]},
        "reading": [{k: v for k, v in r.items() if k != "lore"} for r in ev["reading"]],
        "encounters": {"days": ev["encounters"].get("days"),
                       "terms": ev["encounters"].get("terms", [])[:25],
                       "cats": ev["encounters"].get("cats", {})},
        "last_growth": ev.get("last_growth") or {},
    }
    user = ("按 chronicle.md 的契约判断这一回合的成长。只输出 JSON。\n\n"
            + json.dumps(payload, ensure_ascii=False, indent=1))
    out = lore.chat(guidance.render("chronicle"), user, max_tokens=700, temperature=0.2)
    data = jsonout.parse_obj(out) if out else None
    if not data:
        log.info("推演 · 模型未给可用判定, 走保底规则")
        return clamp(_fallback(ev), ev)
    data.setdefault("assessment", {})
    data.setdefault("growth", {})
    data["_fallback"] = False
    return clamp(data, ev)


# ------------------------------------------------------------------ 冒险

def _fallback_legacy(ch: dict[str, Any]) -> dict[str, Any]:
    """模型不可用时也要留下"退隐"这一笔 (否则时间会继续往前走, 人却停在门里)。"""
    heir = life.propose_default(ch)
    return {
        "title": "终回 · 关门弟子",
        "place": "旧屋",
        "body": (
            f"{ch.get('name')} 到了该收手的年纪。\n\n"
            "他把常用的那把剑挂回墙上, 把几本翻烂的书按次序摆好, 又从匣子里取出一方旧砚。"
            "院子里站着一个刚满十二岁的孩子, 是这些年在门口扫地的那个。\n\n"
            "他把砚台递过去, 只说了一句: 从明天起, 你自己读。\n\n"
            "孩子接过砚台, 双手有点抖。他没有再多说什么 —— 该教的都教了, "
            "剩下的路, 谁替谁走都不算数。"
        ),
        "terms_used": [],
        "themes_used": ["传承"],
        "tail": "门关上, 院子里的扫地声换了一个人。",
        "verdict": "退隐, 待传承。",
        "growth_shown": [],
        "days": 1,
        "heir": heir,
    }


def _change_brief(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """这一回合**真实发生的**变化 (属性 / 技能 / 经脉 / 性情) —— 给写故事那道工序看。

    故事里"长本事"要有据可依: 不能模型自己编"悟性大进", 必须对着这份清单写。
    """
    out: dict[str, Any] = {"attrs": [], "skills": [], "vein": None, "trait": ""}
    try:
        b_attrs = cs.effective_attrs(before)
        a_attrs = cs.effective_attrs(after)
        for a in cs.ATTRS:
            k = a["key"]
            f, t = int(b_attrs.get(k, 0)), int(a_attrs.get(k, 0))
            if t != f:
                out["attrs"].append({"key": k, "name": a["name"], "from": f, "to": t})
        b_sk = dict(before.get("skills") or {})
        a_sk = dict(after.get("skills") or {})
        for sid, lv in a_sk.items():
            f, t = int(b_sk.get(sid, 0)), int(lv or 0)
            if t > f:
                sk = cs.SKILL_BY_ID.get(str(sid)) or {}
                out["skills"].append({"id": sid, "name": sk.get("name") or sid,
                                      "from": f, "to": t, "type": sk.get("type") or ""})
        if int(after.get("vein") or 0) > int(before.get("vein") or 0):
            out["vein"] = int(after.get("vein") or 0)
        new_traits = [t for t in (after.get("traits") or []) if t not in (before.get("traits") or [])]
        if new_traits:
            t0 = cs.TRAIT_BY_ID.get(str(new_traits[0])) or {}
            out["trait"] = t0.get("name") or str(new_traits[0])
    except Exception as e:  # noqa: BLE001
        log.warning("推演 · 变化清单算不出来: %s", e)
    return out


def _adventure_payload(ev: dict[str, Any], ch: dict[str, Any],
                       gain: dict[str, Any], prev: dict[str, Any] | None,
                       change: dict[str, Any] | None = None) -> dict[str, Any]:
    books = []
    for r in ev["reading"][:4]:
        card = r.get("lore") or {}
        books.append({
            "title": r.get("title"), "identity": card.get("identity") or "",
            "themes": card.get("themes") or [], "setting": card.get("setting") or "",
            "summary": card.get("summary") or "",
            "motifs": [c.get("gist", "")[:60] for c in (card.get("chapters") or [])][:3],
        })
    terms = [{"term": t.get("term"), "cat": t.get("cat"), "count": t.get("count")}
             for t in ev["encounters"].get("terms", [])[:20]]
    return {
        "char": _char_brief(ch),
        "growth": {"exp": gain.get("exp"), "fame": gain.get("fame"),
                   "levels": gain.get("levels"), "pot": gain.get("pot"),
                   "vein": (change or {}).get("vein"),
                   "trait": (change or {}).get("trait") or "",
                   "attrs": (change or {}).get("attrs") or [],
                   "skills": (change or {}).get("skills") or [],
                   "reason": (ev.get("judgement_reason") or "")},
        "time": {"days": int(ev.get("days") or 0),
                 "story_day": life.story_day(ch),
                 "day_no": life.story_day(ch) + 1,
                 "minutes_per_story_day": life.cfg()["minutes_per_day"]},
        "books": books,
        "terms": terms,
        "previous": prev or {},
        "window": ev["window"],
    }


def compose(ev: dict[str, Any], ch: dict[str, Any], gain: dict[str, Any],
            prev: dict[str, Any] | None, change: dict[str, Any] | None = None,
            guide: str = "adventure") -> dict[str, Any] | None:
    """写一回游历 (模型不可用 / 解析失败 → None, 这一回合就不出故事)。

    为什么重试两次而不是一次: 判成长那一次调用刚吃掉一轮额度, 紧接着的第二次
    很容易撞 429 / 超时 —— 实测就丢过一整回的故事。这里多给一次机会 (间隔 2s),
    仍然失败才算这一回合没出门。

    ``guide="legacy"`` 时走「退隐 + 传承」那道工序 (多返回一个 ``heir`` 提议)。
    """
    payload = _adventure_payload(ev, ch, gain, prev, change)
    if guide == "legacy":
        payload["char"] = life.legacy_brief(ch)
        payload["growth"]["age"] = life.age_years(ch)
    user = ("按 %s.md 的契约写这一回。只输出 JSON。\n\n" % guide
            + json.dumps(payload, ensure_ascii=False, indent=1))
    data = None
    for attempt in (1, 2):
        out = lore.chat(guidance.render(guide), user, max_tokens=1600,
                        temperature=0.9)
        data = jsonout.parse_obj(out) if out else None
        if data:
            break
        log.info("游历 · 第 %d 次没拿到可解析的故事, 稍后重试", attempt)
        time.sleep(2.0)
    if not data:
        return None
    body = jsonout.as_text(data.get("body"), 2600)
    if len(body) < 120:
        return None
    story = {
        "title": jsonout.as_str(data.get("title"), 40) or "一回游历",
        "place": jsonout.as_str(data.get("place"), 20),
        "body": body,
        "terms_used": jsonout.as_list(data.get("terms_used"), 6, 16),
        "themes_used": jsonout.as_list(data.get("themes_used"), 4, 16),
        "tail": jsonout.as_str(data.get("tail"), 60),
        "verdict": jsonout.as_str(data.get("verdict"), 40),
        "growth_shown": jsonout.as_list(data.get("growth_shown"), 5, 24),
        "days": jsonout.as_int(data.get("days"), 1, 1, 5),
    }
    if guide == "legacy":
        h = data.get("heir") if isinstance(data.get("heir"), dict) else {}
        kind = str(h.get("type") or "").strip()
        if kind not in life.HEIR_TYPES:
            kind = "disciple"
        story["heir"] = {
            "type": kind,
            "name": jsonout.as_str(h.get("name"), 16) or life.propose_default(ch)["name"],
            "reason": jsonout.as_str(h.get("reason"), 80) or life.propose_default(ch)["reason"],
        }
    return story


# ------------------------------------------------------------------ 主流程

def run(*, trigger: str = "manual", force: bool = False,
        dry: bool = False) -> dict[str, Any]:
    """跑一个回合。返回摘要 (接口直接用)。"""
    c = cfg()
    if not c.get("enabled") and not force:
        return {"status": "disabled", "enabled": False}
    if not _LOCK.acquire(blocking=False):
        return {"status": "busy"}
    _RUN["running"] = True
    try:
        return _run_locked(trigger=trigger, force=force, dry=dry)
    except Exception as e:  # noqa: BLE001 — 手动触发不该把 500 丢给页面
        log.warning("推演 · 回合失败: %s", e)
        try:
            db = store.conn()
            db.execute(
                "INSERT INTO cycle (at, trigger, status, error) VALUES (?,?,?,?)",
                (clock.iso_local(), trigger, "error", f"{type(e).__name__}: {e}"[:300]))
            db.commit()
            db.close()
        except Exception:  # noqa: BLE001
            pass
        return {"status": "error", "error": f"{type(e).__name__}: {e}"}
    finally:
        _RUN["running"] = False
        _LOCK.release()


def _run_locked(*, trigger: str, force: bool, dry: bool) -> dict[str, Any]:
    c = cfg()
    st = load_state()
    now = time.time()
    ev = gather(st, now=now)
    minutes = int(ev["total_minutes"] or 0)
    has_terms = bool(ev["encounters"].get("terms"))

    # 时序: 这一回合攒够了几个"故事内的一天" (一小时阅读 = 一天)
    ch = cs.load_char()
    # 故事时钟由"累计陪读分钟"除出来 —— 而分钟归陪读心跳记账。推演自己也可能
    # 先跑 (心跳还没折算过), 所以这里补一次幂等折算 (只记分钟, 不给修为),
    # 否则读了一小时却因为"心跳没到点"而永远不出门。
    anchor_before = int(ch.get("credited_minutes") or 0)
    try:
        from recto.pet import companion as _companion

        growth.credit_reading_minutes(ch, _companion.total_reading_minutes(),
                                      grant_exp=False)
    except Exception as e:  # noqa: BLE001 — 时长读不出来不该挡住推演
        log.warning("推演 · 折算陪读分钟失败: %s", e)
    anchor_moved = int(ch.get("credited_minutes") or 0) != anchor_before
    ev["days"] = max(0, life.story_day(ch) - int(st.get("story_day") or 0))
    ev["story_day"] = life.story_day(ch)
    ev["age"] = life.age_years(ch)

    # 已退隐, 等读者点头传承 —— 不再长大, 也不再出门
    if life.stage(ch) == "retiring":
        st["last_check_at"] = clock.fmt_sec(now, "%Y-%m-%dT%H:%M:%S")
        save_state(st)
        if anchor_moved:
            cs.save_char(ch)
        return {"status": "retired", "life": life.view(ch),
                "minutes": minutes, "window": ev["window"]}

    if not force and minutes < int(c["min_minutes"]) and not has_terms:
        st["last_check_at"] = clock.fmt_sec(now, "%Y-%m-%dT%H:%M:%S")
        st["next_at"] = clock.fmt_sec(now + int(c["interval_min"]) * 60,
                                      "%Y-%m-%dT%H:%M:%S")
        save_state(st)
        if anchor_moved:
            cs.save_char(ch)
        return {"status": "idle", "minutes": minutes,
                "window": ev["window"], "days": ev["days"],
                "reading": len(ev["reading"])}

    # 故事节奏闸门: 一天一天走。不足一天就连故事都不写 —— 否则"读十分钟走一回"
    # 会让故事世界比现实跑得还快, 80 岁退隐这件事永远等不到。
    need_days = max(1, int(c.get("days_per_adventure") or 1))
    if not force and ev["days"] < need_days:
        st["last_check_at"] = clock.fmt_sec(now, "%Y-%m-%dT%H:%M:%S")
        st["next_at"] = clock.fmt_sec(now + int(c["interval_min"]) * 60,
                                      "%Y-%m-%dT%H:%M:%S")
        st["waiting_note"] = (f"还差 {life.minutes_to_next_day(ch)} 分钟到下一日")
        save_state(st)
        if anchor_moved:
            cs.save_char(ch)
        return {"status": "waiting", "minutes": minutes, "days": ev["days"],
                "need_days": need_days,
                "minutes_to_next_day": life.minutes_to_next_day(ch),
                "window": ev["window"], "reading": len(ev["reading"])}
    st.pop("waiting_note", None)

    # ── 知识储备 (先给模型看书, 再判成长)
    for r in ev["reading"][: int(c["max_lore_books"])]:
        if dry:
            card = lore.card(r["book"])
            if card.get("missing"):
                continue
        else:
            card = lore.build_book(r["book"], search_mode="auto" if c.get("search") else "off")
            if not card:
                card = lore.card(r["book"])
        r["lore"] = card or {}
    if not dry:
        for r in ev["reading"][: int(c["max_lore_books"])]:
            for pending in lore.pending_chapters(r["book"],
                                                 limit=int(c["max_lore_chapters"]))[:2]:
                lore.build_chapter(r["book"], int(pending["chapter"]))
                if r.get("lore"):
                    r["lore"] = lore.card(r["book"], chapters=3)

    before = json.loads(json.dumps(ch, ensure_ascii=False))   # 落地前快照 (算变化清单)
    j = judge(ev, ch)
    ev["judgement_reason"] = (j.get("growth") or {}).get("reason") or ""
    gain = dict(j.get("growth") or {})

    if dry:
        return {"status": "dry", "window": ev["window"], "minutes": minutes,
                "days": ev["days"], "reading": ev["reading"], "judgement": j,
                "fallback": bool(j.get("_fallback")),
                "skill_version": guidance.version("chronicle"),
                "life": life.view(ch),
                "encounters": ev["encounters"]}

    # ── 落地成长
    applied = growth.award_judged(ch, {**gain, "exp": gain.get("exp"),
                                       "pot": gain.get("pot"), "fame": gain.get("fame"),
                                       "morality": gain.get("morality"),
                                       "skills": gain.get("skills")},
                                  max_exp=int(gain.get("cap") or growth.MAX_EXP_PER_CYCLE),
                                  story_day=life.story_day(ch))
    trait_gained = ""
    tid = str(gain.get("trait") or "")
    if tid:
        code, _ = cs.grant_trait(ch, tid)     # 内部会校验条数/点数并落盘
        if code == 200:
            trait_gained = tid
    change = _change_brief(before, ch)

    # ── 走一趟 (到岁数了这一趟就是"退隐 + 传承")
    retiring = life.ready_to_retire(ch)
    prev_row = _last_adventure()
    prev = None
    if prev_row:
        prev = {"no": int(prev_row.get("no") or 0), "title": prev_row.get("title") or "",
                "place": prev_row.get("place") or "",
                "tail": _tail_of(prev_row)}
    story = compose(ev, ch, applied, prev, change,
                    guide="legacy" if retiring else "adventure")
    if retiring and not story:
        story = _fallback_legacy(ch)

    at = clock.fmt_sec(now, "%Y-%m-%dT%H:%M:%S")
    cycle_no = int(st.get("cycle") or 0) + 1
    adv_no = int(st.get("adventure_no") or 0)
    db = store.conn()
    cur = db.execute(
        "INSERT INTO cycle (at, from_at, to_at, trigger, status, minutes, evidence,"
        " judgement, error) VALUES (?,?,?,?,?,?,?,?,?)",
        (at, ev["window"]["from"], ev["window"]["to"], trigger, "ok", minutes,
         json.dumps({"reading": [{k: v for k, v in r.items() if k != "lore"}
                                 for r in ev["reading"]],
                     "encounters": {"terms": ev["encounters"].get("terms", [])[:25],
                                    "cats": ev["encounters"].get("cats", {})}},
                    ensure_ascii=False),
         json.dumps(j, ensure_ascii=False), ""))
    cycle_id = int(cur.lastrowid or 0)
    if story:
        adv_no += 1
        db.execute(
            "INSERT INTO adventure (cycle, at, no, title, place, body, themes, terms,"
            " growth, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (cycle_id, at, adv_no, story["title"], story["place"], story["body"],
             json.dumps(story["themes_used"], ensure_ascii=False),
             json.dumps(story["terms_used"], ensure_ascii=False),
             json.dumps({"exp": applied.get("exp"), "levels": applied.get("levels"),
                         "fame": applied.get("fame"), "pot": applied.get("pot"),
                         "trait": trait_gained,
                         "skills": change.get("skills") or [],
                         "attrs": change.get("attrs") or [],
                         "vein": change.get("vein"),
                         "growth_shown": story.get("growth_shown") or [],
                         "days": int(ev.get("days") or 0),
                         "story_day": life.story_day(ch),
                         "age": life.age_years(ch),
                         "tag": "retire" if retiring else ""},
                        ensure_ascii=False),
             at))
        if retiring:
            heir = story.get("heir") or life.propose_default(ch)
            life.mark_retired(ch, heir=heir, story_no=adv_no, title=story["title"])
    keep = int(c.get("keep_adventures") or 200)
    db.execute("DELETE FROM adventure WHERE id NOT IN"
               " (SELECT id FROM adventure ORDER BY id DESC LIMIT ?)", (keep,))
    db.commit()
    db.close()

    # ── 记账 (只有真落地才推进 credit / progress)
    books_in = {r["book"] for r in ev["reading"]}
    try:
        rc = _rs_conn()
        all_days = store.rows(rc, "SELECT book, day, minutes FROM rtime WHERE day >= ?",
                              (ev["from_day"],))
        for d in all_days:
            if d["book"] in books_in:
                st.setdefault("credit", {})[f"{d['book']}|{d['day']}"] = int(d["minutes"] or 0)
    except Exception as e:  # noqa: BLE001
        log.warning("推演 · 记账失败: %s", e)
    for r in ev["reading"]:
        st.setdefault("progress", {})[r["book"]] = {
            "chapter": int(r.get("chapter_to") or 0),
            # 存**比例** (0~1), 不是百分数 —— gather 那边统一 ×100 展示
            "percent": round(float(r.get("percent_to") or 0) / 100.0, 6)}
    _prune_credit(st)
    st.update({"last_at": at, "cycle": cycle_no, "adventure_no": adv_no,
               "last_status": "ok", "next_at": clock.fmt_sec(now + int(c["interval_min"]) * 60,
                                                             "%Y-%m-%dT%H:%M:%S"),
               "story_day": life.story_day(ch),
               "age": life.age_years(ch),
               "last_growth": {"at": at, "exp": applied.get("exp"),
                               "fame": applied.get("fame"),
                               "levels": applied.get("levels"),
                               "days": int(ev.get("days") or 0)},
               "last_summary": (j.get("assessment") or {}).get("one_line") or ""})
    save_state(st)
    cs.save_char(ch)          # 成长 / 技能 / 退隐标记 一起落盘 (一次写完)

    log.info("推演 · 第 %d 回合 · %d 分钟 · %d 故事日 · 修为 +%d%s · 游历 %s",
             cycle_no, minutes, int(ev.get("days") or 0),
             int(applied.get("exp") or 0),
             " · 故事" if story else " · 无故事",
             (story or {}).get("title") or "-")
    if retiring:
        log.info("推演 · %s 已到 %d 岁, 退隐待传承 (第 %d 回: %s)",
                 ch.get("name"), life.age_years(ch), adv_no,
                 (story or {}).get("title") or "-")
    return {"status": "ok", "cycle": cycle_no, "window": ev["window"],
            "minutes": minutes, "days": int(ev.get("days") or 0),
            "reading": len(ev["reading"]),
            "judgement": j, "growth": applied, "trait": trait_gained,
            "change": change, "retiring": retiring,
            "fallback": bool(j.get("_fallback")),
            "adventure": story, "skill_version": guidance.version("chronicle"),
            "life": life.view(ch), "char": cs.view(ch)}


def backfill_adventure(cycle_id: int | None = None) -> dict[str, Any] | None:
    """给"没走出故事"的那一回合补写一回 (不重新判成长)。

    什么时候会缺: 判成长那次调用吃掉了额度, 紧接着写故事撞 429/超时 —— 实测
    丢过一整回。这里用 `cycle.evidence` 里存的证据把故事补上, 成长不再动
    (那是已经落地的事实, 不能因为补故事而改数)。
    """
    c = store.conn()
    row = store.one(c, "SELECT * FROM cycle WHERE id=?" if cycle_id else
                    "SELECT * FROM cycle WHERE id NOT IN (SELECT COALESCE(cycle,0) FROM adventure)"
                    " ORDER BY id DESC LIMIT 1",
                    (int(cycle_id),) if cycle_id else ())
    c.close()
    if not row:
        return None
    ev_raw = _jdict(row.get("evidence"))
    st = load_state()
    reading = []
    for r in ev_raw.get("reading") or []:
        if not isinstance(r, dict):
            continue
        item = dict(r)
        card = lore.card(str(item.get("book") or ""), chapters=3)
        item["lore"] = {} if card.get("missing") else card
        reading.append(item)
    ev = {"window": {"from": row.get("from_at"), "to": row.get("to_at"),
                     "hours": 0},
          "reading": reading,
          "encounters": ev_raw.get("encounters") or {"terms": [], "cats": {}}}
    if not reading and not (ev["encounters"].get("terms")):
        return None
    ch = cs.load_char()
    prev_row = _last_adventure()
    prev = None
    if prev_row:
        prev = {"no": int(prev_row.get("no") or 0), "title": prev_row.get("title") or "",
                "place": prev_row.get("place") or "", "tail": _tail_of(prev_row)}
    judgement = _jdict(row.get("judgement"))
    gain = judgement.get("growth") or {}
    story = compose(ev, ch, gain, prev)
    if not story:
        return None
    no = int(st.get("adventure_no") or 0) + 1
    at = clock.iso_local()
    db = store.conn()
    db.execute(
        "INSERT INTO adventure (cycle, at, no, title, place, body, themes, terms,"
        " growth, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (int(row.get("id") or 0), at, no, story["title"], story["place"], story["body"],
         json.dumps(story["themes_used"], ensure_ascii=False),
         json.dumps(story["terms_used"], ensure_ascii=False),
         json.dumps({"exp": gain.get("exp"), "backfill": True}, ensure_ascii=False), at))
    db.commit()
    db.close()
    st["adventure_no"] = no
    save_state(st)
    log.info("游历 · 补写了第 %d 回 (回合 #%s)", no, row.get("id"))
    return story


# ------------------------------------------------------------------ 查询

def _tail_of(row: dict[str, Any]) -> str:
    body = str(row.get("body") or "")
    return body[-300:] if body else ""


def _last_adventure() -> dict[str, Any] | None:
    try:
        c = store.conn()
        row = store.one(c, "SELECT * FROM adventure ORDER BY id DESC LIMIT 1")
        c.close()
        return row
    except Exception:  # noqa: BLE001
        return None


def adventures(limit: int = 20) -> list[dict[str, Any]]:
    try:
        c = store.conn()
        rows = store.rows(c, "SELECT * FROM adventure ORDER BY id DESC LIMIT ?",
                          (int(limit),))
        c.close()
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in rows:
        out.append({
            "id": r["id"], "no": int(r.get("no") or 0), "at": r.get("at"),
            "title": r.get("title") or "", "place": r.get("place") or "",
            "body": r.get("body") or "",
            "themes": _jlist(r.get("themes")), "terms": _jlist(r.get("terms")),
            "growth": _jdict(r.get("growth")),
        })
    return out


def _jlist(v: Any) -> list[str]:
    try:
        d = json.loads(v or "[]")
        return [str(x) for x in d] if isinstance(d, list) else []
    except Exception:  # noqa: BLE001
        return []


def _jdict(v: Any) -> dict[str, Any]:
    try:
        d = json.loads(v or "{}")
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def cycles(limit: int = 10) -> list[dict[str, Any]]:
    try:
        c = store.conn()
        rows = store.rows(c, "SELECT id, at, trigger, status, minutes, judgement"
                             " FROM cycle ORDER BY id DESC LIMIT ?", (int(limit),))
        c.close()
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in rows:
        j = _jdict(r.get("judgement"))
        g = j.get("growth") or {}
        out.append({"id": r["id"], "at": r.get("at"), "trigger": r.get("trigger"),
                    "status": r.get("status"), "minutes": int(r.get("minutes") or 0),
                    "exp": int(g.get("exp") or 0), "fame": int(g.get("fame") or 0),
                    "one_line": (j.get("assessment") or {}).get("one_line") or "",
                    "reason": g.get("reason") or ""})
    return out


def reset_after_inherit() -> None:
    """传承之后把故事时钟归零。

    不归零会死锁: 故事日 = 累计陪读分钟 // 60, 而新人的分钟从 0 起 ——
    ``days = 现在 - 上次`` 永远是负数, 推演永远判"不足一天", 新一代再也走不出去。
    (真踩过: 第一版忘了这一步, 换人后 60 分钟读完还是不出门。)
    """
    st = load_state()
    st["story_day"] = 0
    st["age"] = int(life.cfg()["start_age"])
    st["last_growth"] = {}
    st["last_summary"] = ""
    st.pop("waiting_note", None)
    save_state(st)
    log.info("推演 · 传承完成, 故事时钟归零 (新一代从 %d 岁起)", st["age"])


def state_view() -> dict[str, Any]:
    """页面 / 接口用的现状 (一次给全, 前端不用问三遍)。"""
    c = cfg()
    st = load_state()
    adv = adventures(limit=8)
    ch = cs.load_char()
    return {
        "enabled": bool(c.get("enabled")),
        "config": c,
        "tick": tick_state(),
        "state": {k: st.get(k) for k in
                  ("last_at", "next_at", "cycle", "adventure_no", "last_status",
                   "last_summary", "last_check_at", "story_day", "age",
                   "waiting_note", "last_growth")},
        "life": life.view(ch),
        "adventures": adv,
        "recent": cycles(limit=6),
        "lore": lore.stats(),
        "encounters": encounters.summary(days=7),
        "guides": guidance.all_specs(),
        "char": _char_brief(ch),
    }


# ------------------------------------------------------------------ 心跳

def tick_state() -> dict[str, Any]:
    th = _RUN.get("thread")
    return {"enabled": bool(th is not None and getattr(th, "is_alive", lambda: False)()),
            "interval_sec": int(round(_TICK_SEC)),
            "runs": int(_RUN.get("runs") or 0),
            "last_at": _RUN.get("at"), "last": _RUN.get("last"),
            "error": _RUN.get("error"), "running": bool(_RUN.get("running"))}


def start_ticker() -> bool:
    th = _RUN.get("thread")
    if th is not None and getattr(th, "is_alive", lambda: False)():
        return False
    _RUN["stop"] = False
    t = threading.Thread(target=_loop, daemon=True, name="recto-pet-chronicle")
    _RUN["thread"] = t
    t.start()
    log.info("后台推演已启动 · 每 %d 分钟一回合",
             int(cfg()["interval_min"]))
    return True


def stop_ticker() -> None:
    _RUN["stop"] = True


def _loop() -> None:
    time.sleep(20)   # 服务刚起来先让阅读器 / 同步跑顺
    while not _RUN.get("stop"):
        try:
            _RUN["at"] = time.time()
            _RUN["runs"] = int(_RUN.get("runs") or 0) + 1
            out = run(trigger="tick")
            _RUN["last"] = out.get("status")
            _RUN["error"] = None
            if out.get("status") == "ok":
                log.info("后台推演 · 回合 #%s 完成", out.get("cycle"))
        except Exception as e:  # noqa: BLE001 — 心跳不能死
            _RUN["error"] = f"{type(e).__name__}: {e}"
            log.warning("后台推演出错 · %s", e)
        # 醒来只看一次: 到点就跑 (interval 现读, 改设置不用重启)
        gap = max(60.0, float(cfg()["interval_min"]) * 60)
        end = time.time() + gap
        while not _RUN.get("stop") and time.time() < end:
            time.sleep(min(30.0, max(1.0, end - time.time())))
