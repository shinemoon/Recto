"""书宠的时序 —— 故事内的时间 / 年岁 / 退隐 / 传承。

**为什么单独一层**: 其它模块都在回答"长了多少本事", 只有这一层回答
"它活了多久、还能走多久、走完之后谁接着走"。

**故事内时间怎么走** (2026-11 定的口径):

  · 一小时阅读 = 故事里的一天 (``minutes_per_day`` 默认 60)
  · 故事内的日子不单独计数 —— 它就是**累计陪读分钟**除出来的,
    所以删书、清空时长表也不会让时间倒流 (只增不减)
  · 12 岁出江湖, 80 岁前后退隐 —— 一天一天走, 不跳年
  · 退隐后不直接换人: 先写一回"退隐 + 传承"的故事, **等读者确认**,
    再从那一位 12 岁起重新算 (子女 / 徒弟 / 学生)

**为什么"传承"要读者点头**: 这是一次不可回退的清零 (修为、技能、声望、
门派关系全部重来)。系统自己替他决定, 就等于把几百小时的陪读悄悄抹掉。
所以 ⻔槛只有一条: ``POST /pet/api/char/inherit`` 必须带 ``confirm=true``。

**只在内存里改, 调用方负责落盘** —— 与 ``charsheet`` 的规矩一致。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

#: 出江湖的岁数
START_AGE = 12
#: 该考虑退隐的岁数
RETIRE_AGE = 80
#: 故事内一年多少天 (不闰)
DAYS_PER_YEAR = 365
#: 现实里陪读多少分钟 → 故事内一天
MINUTES_PER_STORY_DAY = 60
#: 传承时保留的最高技能比例 (30%, 最少 1 级)
HEIR_SKILL_SHARE = 0.3
#: 传承时新人的起步潜能
HEIR_POT = 3

HEIR_TYPES = {
    "child": {"name": "子女", "desc": "血脉相传: 承门派的功夫与家学, 宗族里的人都认得这个姓。"},
    "disciple": {"name": "徒弟", "desc": "入室弟子: 承师门与师承, 别人提起他会先说'某人的徒弟'。"},
    "student": {"name": "学生", "desc": "受业弟子: 只承学问与家学, 不入门派 —— 路要自己走。"},
}


# ------------------------------------------------------------------ 配置

#: 配置读得很勤 (每次算故事日都要), 而读盘没必要 —— 缓存两秒。
#: 环境变量变了立刻失效 (测试/临时实例会改它)。
_CFG_CACHE: dict[str, Any] = {"at": 0.0, "env": None, "val": None}


def cfg() -> dict[str, Any]:
    """故事时序配置。``[life]`` 段可调, 环境变量可覆盖 (测试/临时实例用)。"""
    env_now = os.environ.get("RECTO_PET_MINUTES_PER_DAY")
    c = _CFG_CACHE
    if (c["val"] is not None and c["env"] == env_now
            and time.time() - float(c["at"]) < 2.0):
        return dict(c["val"])
    out = {"minutes_per_day": MINUTES_PER_STORY_DAY,
           "retire_age": RETIRE_AGE,
           "start_age": START_AGE}
    try:
        from recto.pet import config as petcfg

        raw = (petcfg.load_config() or {}).get("life") or {}
        if isinstance(raw, dict):
            for k in ("minutes_per_day", "retire_age", "start_age"):
                if isinstance(raw.get(k), (int, float)) and int(raw[k]) > 0:
                    out[k] = int(raw[k])
    except Exception:  # noqa: BLE001 — 读不到配置就用默认
        pass
    env = os.environ.get("RECTO_PET_MINUTES_PER_DAY")
    if env:
        try:
            out["minutes_per_day"] = max(1, int(float(env)))
        except ValueError:
            pass
    out["minutes_per_day"] = max(1, out["minutes_per_day"])
    out["retire_age"] = max(out["start_age"] + 1, out["retire_age"])
    c.update({"at": time.time(), "env": env_now, "val": dict(out)})
    return out


# ------------------------------------------------------------------ 时序

def story_day(ch: dict[str, Any]) -> int:
    """故事内走过的天数 (0 = 出江湖那天)。由累计陪读分钟除出来, 不单独记。"""
    return max(0, int(ch.get("minutes") or 0)) // int(cfg()["minutes_per_day"])


def age_years(ch: dict[str, Any]) -> int:
    c = cfg()
    return int(c["start_age"]) + story_day(ch) // DAYS_PER_YEAR


def age_label(ch: dict[str, Any]) -> str:
    """``12 岁 · 第 3 日`` —— 页面上一眼能看懂的写法。"""
    c = cfg()
    d = story_day(ch)
    return f"{c['start_age'] + d // DAYS_PER_YEAR} 岁 · 第 {d % DAYS_PER_YEAR + 1} 日"


def stage(ch: dict[str, Any]) -> str:
    """``growing`` 在江湖 / ``retiring`` 已退隐, 等传承。"""
    lif = ch.get("life") if isinstance(ch.get("life"), dict) else {}
    st = str(lif.get("stage") or "growing")
    return st if st in ("growing", "retiring") else "growing"


def ready_to_retire(ch: dict[str, Any]) -> bool:
    """到岁数了且还没退隐。"""
    return stage(ch) == "growing" and age_years(ch) >= int(cfg()["retire_age"])


def minutes_to_next_day(ch: dict[str, Any]) -> int:
    c = cfg()
    m = max(0, int(ch.get("minutes") or 0))
    return int(c["minutes_per_day"]) - (m % int(c["minutes_per_day"]))


def days_to_retire(ch: dict[str, Any]) -> int:
    c = cfg()
    total = (int(c["retire_age"]) - int(c["start_age"])) * DAYS_PER_YEAR
    return max(0, total - story_day(ch))


def view(ch: dict[str, Any]) -> dict[str, Any]:
    """页面 / 接口用的时序面板。"""
    c = cfg()
    d = story_day(ch)
    lif = ch.get("life") if isinstance(ch.get("life"), dict) else {}
    heir = lif.get("heir") if isinstance(lif.get("heir"), dict) else None
    return {
        "day": d,
        "day_no": d + 1,
        "age": age_years(ch),
        "age_label": age_label(ch),
        "stage": stage(ch),
        "start_age": int(c["start_age"]),
        "retire_age": int(c["retire_age"]),
        "minutes_per_day": int(c["minutes_per_day"]),
        "minutes_to_next_day": minutes_to_next_day(ch),
        "days_to_retire": days_to_retire(ch),
        "ready_to_retire": ready_to_retire(ch),
        "retired_at": str(lif.get("retired_at") or ""),
        "heir": heir or None,
        "heir_types": [{"id": k, **v} for k, v in HEIR_TYPES.items()],
        "generation": int(ch.get("generation") or 1),
        "lineage": list(ch.get("lineage") or []),
        "last_retire_story": lif.get("story_no") or 0,
        "retire_story_title": str(lif.get("story_title") or ""),
    }


# ------------------------------------------------------------------ 退隐

def mark_retired(ch: dict[str, Any], *, heir: dict[str, Any] | None = None,
                 story_no: int = 0, title: str = "") -> None:
    """把"已退隐, 等读者点头"记进存档 (就地改)。"""
    from recto.core import clock

    lif = ch.get("life") if isinstance(ch.get("life"), dict) else {}
    lif.update({
        "stage": "retiring",
        "retired_at": clock.iso_local(),
        "story_no": int(story_no),
        "story_title": title,
        "heir": heir or lif.get("heir") or {},
    })
    ch["life"] = lif


def propose_default(ch: dict[str, Any]) -> dict[str, Any]:
    """模型没给提议时的兜底: 一位徒弟, 名字从"小"字辈里取。"""
    used = {str(x.get("name") or "") for x in (ch.get("lineage") or [])}
    pool = ["小砚", "小潭", "小舟", "小满", "小禾"]
    name = next((n for n in pool if n not in used), "小墨")
    return {"type": "disciple", "name": name,
            "reason": "身后无子女, 门下收了一位徒弟; 走的仍是这条路。"}


# ------------------------------------------------------------------ 传承

def _lineage_dir() -> Path:
    from recto.pet import charsheet as cs

    return Path(cs.CHAR_PATH).parent / "lineage"


def archive(ch: dict[str, Any]) -> str:
    """把这一位的一生落一份存档, 返回路径。"""
    d = _lineage_dir()
    d.mkdir(parents=True, exist_ok=True)
    gen = int(ch.get("generation") or 1)
    name = str(ch.get("name") or "无名")
    p = d / f"gen{gen:02d}-{name}.json"
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    payload = dict(ch)
    payload["_archived_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    payload["_age"] = age_years(ch)
    payload["_story_day"] = story_day(ch)
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, p)
    return str(p)


def _best_skill(ch: dict[str, Any]) -> tuple[str, int]:
    items = [(str(k), int(v or 0)) for k, v in (ch.get("skills") or {}).items()]
    items = [x for x in items if x[1] > 0]
    return max(items, key=lambda x: x[1]) if items else ("", 0)


def inherit(ch: dict[str, Any], heir_type: str, name: str = "") -> dict[str, Any]:
    """读者确认传承 → 新人从 12 岁起。返回 ``{"char": 新人, "record": 上一位, "archive": 路径}``。

    **就地改传入的 ``ch`` 没有意义** —— 调用方要拿返回值替换存档。
    """
    from recto.core import clock
    from recto.pet import charsheet as cs

    kind = heir_type if heir_type in HEIR_TYPES else "disciple"
    lif = ch.get("life") if isinstance(ch.get("life"), dict) else {}
    heir = lif.get("heir") if isinstance(lif.get("heir"), dict) else {}
    nm = (name or str(heir.get("name") or "") or "小墨").strip()[:24] or "小墨"

    path = archive(ch)
    old_gen = int(ch.get("generation") or 1)
    record = {
        "generation": old_gen,
        "name": ch.get("name") or "",
        "school": (ch.get("school") or ""),
        "years": max(0, age_years(ch) - int(cfg()["start_age"])),
        "level": int(ch.get("level") or 1),
        "fame": int(ch.get("fame") or 0),
        "vein": int(ch.get("vein") or 0),
        "minutes": int(ch.get("minutes") or 0),
        "story_day": story_day(ch),
        "archive": path,
        "heir_type": kind,
        "heir_name": nm,
        "at": clock.iso_local(),
    }

    new = cs.new_char(nm)
    new["generation"] = old_gen + 1
    new["lineage"] = list(ch.get("lineage") or []) + [record]
    # 家学: 最高的那门功夫打三折传下去 (最少 1 级)
    sid, lv = _best_skill(ch)
    if sid and lv > 0:
        kept = max(1, int(lv * HEIR_SKILL_SHARE))
        new["skills"] = {sid: kept}
    # 门派: 子女 / 徒弟承师门; 学生不入门派
    if kind in ("child", "disciple") and ch.get("school"):
        new["school"] = ch.get("school")
    # 声望与心性留一点余荫, 潜能给一点起步
    new["fame"] = max(0, int(ch.get("fame") or 0) // 20)
    new["morality"] = max(-100, min(100, int(ch.get("morality") or 0) // 2))
    new["pot"] = HEIR_POT
    new["life"] = {"stage": "growing", "heir": {}, "inherited_at": clock.iso_local(),
                   "inherited_from": record["name"], "heir_type": kind}
    return {"char": new, "record": record, "archive": path, "heir_type": kind}


def legacy_brief(ch: dict[str, Any]) -> dict[str, Any]:
    """给"退隐/传承"那道工序看的现状。"""
    return {
        "name": ch.get("name"), "age": age_years(ch),
        "school": (cs_school_name(ch)),
        "level": int(ch.get("level") or 1),
        "fame": int(ch.get("fame") or 0),
        "vein": int(ch.get("vein") or 0),
        "skills": dict(ch.get("skills") or {}),
        "traits": list(ch.get("traits") or []),
        "minutes": int(ch.get("minutes") or 0),
        "story_day": story_day(ch),
        "lineage": list(ch.get("lineage") or []),
        "heir_types": [{"id": k, **v} for k, v in HEIR_TYPES.items()],
    }


def cs_school_name(ch: dict[str, Any]) -> str:
    try:
        from recto.pet import charsheet as cs

        s = cs.SCHOOL_BY_ID.get(str(ch.get("school") or ""))
        return s["name"] if s else "无门无派"
    except Exception:  # noqa: BLE001
        return "无门无派"
