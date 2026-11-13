"""转生 / 复位 —— 从头开始一只书宠, 可以带走上辈子的**一部分**阅历。

**两种起步** (都由读者手动触发, 带 ``confirm``):

  · **复位 (inherit=0)** —— 白纸一张。12 岁, 没有门派, 没有故事。
  · **转生 (inherit=N 分钟)** —— 把过去累计陪读里的 N 分钟带过来当"底子":
    时间轴按分钟走, 所以起点岁数 = 12 + N/6/365; 修为 / 内功 / 一点声望按同一份
    阅历折算; 再让模型写一段"这一世的来历"(它就是这段阅历留下的东西)。
    N 可以填到当前累计分钟 (全带走), 也可以只带一小截。

**清掉什么**: 游历故事 (``adventure``) 与回合留痕 (``cycle``) 全部清空 ——
那是**上一个人**走过的路, 新一代不该继承别人的情节。世界 (``world``) 不动:
地名、门派、人物都在原地, 只是换了个人走。书卡 (``lore``) 与语料遭遇也不动 ——
那是江湖的底细, 谁走都还在。

**为什么不让系统自己换人**: 这是一次不可回退的清零。门槛只有一条 ——
必须带 ``confirm=true``。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from recto.core import clock
from recto.pet import charsheet as cs
from recto.pet import growth, jsonout, life, lore, store

log = logging.getLogger("recto.pet.reborn")

#: 转生时"每 N 分钟陪读"折算 1 点起手修为 (与 growth.EXP_PER_MINUTE 同口径)
EXP_PER_MINUTE = growth.EXP_PER_MINUTE
#: 转生时本门内功每 N 分钟涨 1 级 (与 growth.AUTO_SKILL_MINUTES_PER_LEVEL 同口径)
NEIGONG_MINUTES_PER_LEVEL = growth.AUTO_SKILL_MINUTES_PER_LEVEL
#: 转生附带的一点声望 / 心性 (按继承比例给, 有上限)
MAX_FAME = 30
MAX_MORALITY = 30


def _current_minutes(ch: dict[str, Any]) -> int:
    return max(0, int(ch.get("minutes") or 0))


def _levels_for(exp: int) -> tuple[int, int]:
    """按修为算等级 (与 growth 的升级口径一致), 返回 (等级, 剩余修为)。"""
    lv, need, left = 1, cs.exp_needed(1), int(exp)
    while left >= need and lv < 200:
        left -= need
        lv += 1
        need = cs.exp_needed(lv)
    return lv, left


def _backstory(minutes: int, prev: dict[str, Any], world: dict[str, Any]) -> str:
    """这一世的来历 (一段话)。模型不可用时给一句规则拼的话。"""
    days = minutes // max(1, life.cfg()["minutes_per_day"])
    if minutes <= 0:
        return "初入江湖, 什么都没带来。"
    fallback = (f"它带着大约 {days} 天的旧日阅历重来。那些事它说不清, "
                f"只记得手上还有点旧力气, 夜里偶尔会醒。")
    try:
        payload = {
            "inherited_minutes": minutes,
            "inherited_days": days,
            "start_age": life.cfg()["start_age"] + days // life.DAYS_PER_YEAR,
            "world": {"name": world.get("name"), "era": world.get("era"),
                      "law": world.get("law"),
                      "regions": [r.get("name") for r in (world.get("regions") or [])][:6]},
            "previous_life": {"name": prev.get("name") or "",
                              "school": prev.get("school") or "",
                              "level": prev.get("level") or 1,
                              "years": prev.get("years") or 0},
            "task": "写这一世的来历",
        }
        user = ("为一只刚转生的书宠写它的来历。只依据下面这份底子, 只输出 JSON。\n\n"
                + json.dumps(payload, ensure_ascii=False, indent=1))
        out = lore.chat(
            "你是一部架空武侠世界的说书人。写一段来历, 三到五句中文 (<= 120 字)。"
            "要求: 不出现任何现实书名/人名/地名; 不写它前世的具体情节, 只写它"
            "身上留下的东西 (手感 / 习惯 / 说不清的梦); 不要煽情, 不要总结。"
            "输出 JSON: {\"backstory\": \"...\"}",
            user, max_tokens=400, temperature=0.8)
        data = jsonout.parse_obj(out) if out else None
        text = jsonout.as_str((data or {}).get("backstory"), 160)
        return text or fallback
    except Exception as e:  # noqa: BLE001 — 写不出来不该挡住复位
        log.warning("转生 · 来历没写出来: %s", e)
        return fallback


def _apply_inherit(ch: dict[str, Any], minutes: int) -> dict[str, Any]:
    """把继承的阅历折算成起点 (就地改 ``ch``, 返回一份摘要)。"""
    minutes = max(0, int(minutes))
    if minutes <= 0:
        return {"minutes": 0, "levels": 0, "exp": 0, "skill": "", "skill_level": 0}
    exp = minutes * EXP_PER_MINUTE
    lv, left = _levels_for(exp)
    ch["minutes"] = minutes
    ch["credited_minutes"] = minutes          # 两个名字必须相等 (见 load_char 的自愈)
    ch["level"] = lv
    ch["exp"] = left
    ch["fame"] = min(MAX_FAME, minutes // 600)
    ch["morality"] = max(-MAX_MORALITY, min(MAX_MORALITY, minutes // 1200))
    lv_neigong = max(0, minutes // max(1, NEIGONG_MINUTES_PER_LEVEL))
    if lv_neigong:
        ch.setdefault("skills", {})["jibenneigong"] = lv_neigong
    # 经脉: 与 growth 的推进口径一致 (每层按累计分钟数)
    vein = 0
    for v in cs.VEINS:
        if minutes >= int(v["minutes"]):
            vein = max(vein, int(v["idx"]))
        else:
            break
    ch["vein"] = vein
    # 气血内力按新上限拉满 (刚出生不该是残血)
    r = cs.resources(ch)
    ch["hp"] = r["hp"]["max"]
    ch["neili"] = r["neili"]["max"]
    ch["spirit"] = r["spirit"]["max"]
    ch["jingli"] = r["jingli"]["max"]
    return {"minutes": minutes, "levels": lv, "exp": left,
            "skill": "jibenneigong" if lv_neigong else "", "skill_level": lv_neigong}


def _clear_story() -> dict[str, Any]:
    """清空故事与回合留痕 (世界 / 书卡 / 遭遇计数都不动)。"""
    out = {"adventures": 0, "cycles": 0, "state": False}
    try:
        c = store.conn()
        a = c.execute("SELECT COUNT(*) n FROM adventure").fetchone()
        cy = c.execute("SELECT COUNT(*) n FROM cycle").fetchone()
        out["adventures"] = int(a[0] if a else 0)
        out["cycles"] = int(cy[0] if cy else 0)
        c.execute("DELETE FROM adventure")
        c.execute("DELETE FROM cycle")
        c.commit()
        c.close()
    except Exception as e:  # noqa: BLE001
        log.warning("复位 · 清故事失败: %s", e)
    try:
        from recto.pet import chronicle

        st = chronicle.load_state()
        keep = {"credit": st.get("credit") or {}, "progress": st.get("progress") or {}}
        # 只留"已折算过"的账 (否则同一段阅读会被再算一次), 其余归零。
        chronicle.save_state({
            "cycle": 0, "adventure_no": 0, "story_day": 0,
            "credit": keep["credit"], "progress": keep["progress"],
            "last_status": "reset",
            "last_check_at": clock.fmt_sec(time.time(), "%Y-%m-%dT%H:%M:%S"),
        })
        out["state"] = True
    except Exception as e:  # noqa: BLE001
        log.warning("复位 · 清推演状态失败: %s", e)
    return out


def reborn(*, inherit_minutes: int = 0, name: str = "",
           keep_lineage: bool = True) -> dict[str, Any]:
    """复位 / 转生 (调用方负责确认 ``confirm``)。返回新人物与一份交代。"""
    old = cs.load_char()
    world = None
    try:
        from recto.pet import world as wmod

        world = wmod.ensure()                 # 世界要一直在 (没有就铺一次)
    except Exception as e:  # noqa: BLE001
        log.warning("复位 · 世界不可用: %s", e)
        world = {}

    # 上一位的一生先归档 (有阅历才留档: 刚出生的空档没意义)
    archive = ""
    record: dict[str, Any] = {}
    if int(old.get("minutes") or 0) > 0 or int(old.get("level") or 1) > 1:
        try:
            archive = life.archive(old)
            record = {
                "generation": int(old.get("generation") or 1),
                "name": old.get("name") or "",
                "school": old.get("school") or "",
                "level": int(old.get("level") or 1),
                "fame": int(old.get("fame") or 0),
                "minutes": int(old.get("minutes") or 0),
                "story_day": life.story_day(old),
                "years": max(0, life.age_years(old) - int(life.cfg()["start_age"])),
                "archive": archive,
                "kind": "reborn" if int(inherit_minutes) > 0 else "reset",
                "at": clock.iso_local(),
            }
        except Exception as e:  # noqa: BLE001
            log.warning("复位 · 归档上一代失败: %s", e)

    cap = _current_minutes(old)
    take = max(0, min(int(inherit_minutes or 0), cap))
    new = cs.new_char((name or "").strip()[:24] or "小墨")
    if keep_lineage and record:
        new["generation"] = int(record["generation"]) + 1
        new["lineage"] = list(old.get("lineage") or []) + [record]
    else:
        new["generation"] = 1
        new["lineage"] = []
    gained = _apply_inherit(new, take)
    # 新的零点: 阅读器那边此刻累计了多少分钟。之后读的才算这一世的。
    try:
        from recto.pet import companion as _companion

        new["reading_baseline"] = int(_companion.total_reading_minutes() or 0)
    except Exception as e:  # noqa: BLE001
        log.warning("复位 · 读累计时长失败 (基线记 0): %s", e)
        new["reading_baseline"] = 0
    story = _backstory(take, old, world)
    new["life"] = {
        "stage": "growing",
        "reborn_at": clock.iso_local(),
        "inherited_minutes": take,
        "inherited_from": record.get("name") or "",
        "backstory": story,
    }
    new["created_at"] = clock.iso_local()
    cs.save_char(new)
    cleared = _clear_story()

    log.info("复位 · %s · 继承 %d 分钟 (可继承 %d) · 第 %d 代 · %d 级",
             "转生" if take else "重开", take, cap,
             int(new.get("generation") or 1), int(new.get("level") or 1))
    return {
        "char": new,
        "gained": gained,
        "record": record,
        "archive": archive,
        "cleared": cleared,
        "backstory": story,
        "inherited_minutes": take,
        "available_minutes": cap,
        "life": life.view(new),
    }
