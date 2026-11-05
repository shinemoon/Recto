#!/usr/bin/env python3
"""
smart_pick.py — 从 recto aggregate 挑一条"合景"的书摘

设计 (2026-06-12 db665f8 commit 的 spec 落地):

  - 数据源:  $DATA_DIR/aggregate/aggregate.jsonl + books.jsonl
  - 过滤:    跳过"测试书"/"测试摘录" / 空 content / 长度 < 8
  - 7 dim:
        season (春夏秋冬, 由月数粗判, 可被 solar_term 显式覆写)
        solar_term (24 节气, 显式 override)
        weather (rain/snow/sun/wind/cloudy, 从 /tmp/weather.json 读)
        temp (cold/warm, 由 weather + temp_C 联判)
        time_of_day (凌晨/早/午/下午/傍晚/夜/深夜, 由小时判)
        festival (13 公历节日, 显式匹配)
        weekday (weekday/weekend, 由 weekday() 判)
  - 评分:
        explicit dim 命中  +5
        auto dim 命中       +1 (但当 auto 只命中 1 dim 时不计入, 避免噪声)
        ≥ 7     严格 top-1
        4-6     ±2 抖
        1-3     ±1 抖
        0       兜底随机
  - 渲染:   给一张含书名/作者/摘录/idea/触发的 dim 卡头的"人话"回报
  - 触发词:  "书摘"/"摘录"/"挑一条"/"来一条"/"推一条摘录" / "今天读什么"

跟摘录接入服务平级, 只读 aggregate, 不写回任何状态。
"""
from __future__ import annotations

import argparse
import datetime
import json
import random
import re
import sys
from pathlib import Path
from typing import Any, Iterable

# ----- 路径 -----
SKILL_DIR = Path(__file__).resolve().parent
from recto.core import paths  # noqa: E402

DATA_ROOT = paths.DATA_ROOT
AGG_PATH = DATA_ROOT / "aggregate/aggregate.jsonl"
BOOKS_PATH = DATA_ROOT / "aggregate/books.jsonl"
WEATHER_CACHE = Path("/tmp/weather.json")


# ----- 过滤 -----
_TEST_NAME_RE = re.compile(r"测试|test", re.IGNORECASE)
_TEST_CONTENT = {
    "今天要 GC 掉旧 deck 测试",
    "第二段测试摘录",
}


def _is_real_excerpt(e: dict, books: dict) -> bool:
    bid = e.get("bookId")
    b = books.get(bid, {})
    name = (b.get("name") or "").strip()
    if _TEST_NAME_RE.search(name):
        return False
    c = (e.get("content") or "").strip()
    if len(c) < 8:
        return False
    if c in _TEST_CONTENT:
        return False
    return True


def load_pool() -> tuple[list[dict], dict]:
    if not AGG_PATH.exists() or not BOOKS_PATH.exists():
        return [], {}
    books = {b["id"]: b for b in (json.loads(l) for l in BOOKS_PATH.read_text(encoding="utf-8").splitlines() if l.strip())}
    raw = [json.loads(l) for l in AGG_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    pool = [e for e in raw if _is_real_excerpt(e, books)]
    return pool, books


# ----- dim 推导 -----

# 24 节气 → 季节 (显式表, 避免月数边界错)
SOLAR_TERM_SEASON = {
    # 春
    "立春": "春", "雨水": "春", "惊蛰": "春", "春分": "春", "清明": "春", "谷雨": "春",
    # 夏
    "立夏": "夏", "小满": "夏", "芒种": "夏", "夏至": "夏", "小暑": "夏", "大暑": "夏",
    # 秋
    "立秋": "秋", "处暑": "秋", "白露": "秋", "秋分": "秋", "寒露": "秋", "霜降": "秋",
    # 冬
    "立冬": "冬", "小雪": "冬", "大雪": "冬", "冬至": "冬", "小寒": "冬", "大寒": "冬",
}

# 月数粗判季节 (被 solar_term 显式覆写)
def _season_by_month(m: int) -> str:
    if m in (3, 4, 5):
        return "春"
    if m in (6, 7, 8):
        return "夏"
    if m in (9, 10, 11):
        return "秋"
    return "冬"

# 公历节日 (13)
FESTIVALS = {
    (1, 1): "元旦", (2, 14): "情人节", (3, 8): "妇女节", (3, 12): "植树节",
    (4, 1): "愚人节", (5, 1): "劳动节", (5, 4): "青年节",
    (6, 1): "儿童节", (9, 10): "教师节", (10, 1): "国庆节",
    (12, 24): "平安夜", (12, 25): "圣诞节", (12, 31): "跨年夜",
}

# 关键词词表 (用字面词, 不用 LLM 预打标)
KW = {
    "season": {
        "春": "春", "春": "春", "春天": "春", "春光": "春", "春风": "春", "春雨": "春",
        "夏": "夏", "夏天": "夏", "夏日": "夏", "夏夜": "夏",
        "秋": "秋", "秋天": "秋", "秋风": "秋", "秋雨": "秋", "落叶": "秋",
        "冬": "冬", "冬天": "冬", "冬夜": "冬", "雪": "冬", "寒冷": "冬",
    },
    "solar_term": {
        # 24 节气, 部分还兼表 season (上面 SOLAR_TERM_SEASON)
        "立春": "立春", "雨水": "雨水", "惊蛰": "惊蛰", "春分": "春分", "清明": "清明", "谷雨": "谷雨",
        "立夏": "立夏", "小满": "小满", "芒种": "芒种", "夏至": "夏至", "小暑": "小暑", "大暑": "大暑",
        "立秋": "立秋", "处暑": "处暑", "白露": "白露", "秋分": "秋分", "寒露": "寒露", "霜降": "霜降",
        "立冬": "立冬", "小雪": "小雪", "大雪": "大雪", "冬至": "冬至", "小寒": "小寒", "大寒": "大寒",
    },
    "weather": {
        "雨": "rain", "细雨": "rain", "大雨": "rain", "暴雨": "rain", "梅雨": "rain", "听雨": "rain",
        "雪": "snow", "大雪": "snow", "飘雪": "snow", "下雪": "snow",
        "晴": "sun", "阳光": "sun", "晴天": "sun", "日光": "sun",
        "风": "wind", "大风": "wind", "清风": "wind", "晚风": "wind", "微风": "wind",
        "云": "cloudy", "阴": "cloudy", "多云": "cloudy", "阴天": "cloudy", "天色": "cloudy",
    },
    "temp": {
        "冷": "cold", "寒": "cold", "凉": "cold", "冰冷": "cold", "凛": "cold",
        "暖": "warm", "温": "warm", "热": "warm", "暖阳": "warm", "温热": "warm",
    },
    "time_of_day": {
        "凌晨": "凌晨", "深夜": "深夜", "午夜": "深夜",
        "早晨": "早", "清晨": "早", "朝阳": "早", "晨": "早", "黎明": "早",
        "正午": "午", "午": "午", "中午": "午", "晌午": "午",
        "下午": "下午", "午后": "下午",
        "黄昏": "傍晚", "夕阳": "傍晚", "傍晚": "傍晚", "落日": "傍晚", "暮色": "傍晚", "日暮": "傍晚",
        "夜": "夜", "夜里": "夜", "夜晚": "夜", "夜色": "夜", "星空": "夜", "月光": "夜", "夜里": "夜",
    },
    "festival": {
        "元旦": "元旦", "新年": "元旦", "跨年": "跨年夜",
        "情人节": "情人节", "圣诞": "圣诞节", "平安夜": "平安夜",
        "妇女节": "妇女节", "植树节": "植树节", "愚人节": "愚人节",
        "劳动节": "劳动节", "青年节": "青年节", "儿童节": "儿童节",
        "教师节": "教师节", "国庆": "国庆节", "中秋": "中秋", "端午": "端午", "春节": "春节",
    },
    "weekday": {
        "周末": "weekend", "假日": "weekend", "休息日": "weekend", "周一": "weekday",
        "周二": "weekday", "周三": "weekday", "周四": "weekday", "周五": "weekday",
        "工作日": "weekday", "上班": "weekday",
    },
}


def _read_weather() -> dict | None:
    if not WEATHER_CACHE.exists():
        return None
    try:
        return json.loads(WEATHER_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return None


def derive_context(now: datetime.datetime, weather: dict | None) -> dict:
    """返回 7 个 dim 的当前值, 显式 + auto 各自一份。"""
    m, h, wd = now.month, now.hour, now.weekday()
    explicit: dict[str, str] = {}
    auto: dict[str, str] = {}

    # 1. season (solar_term 显式优先, 否则月数粗判)
    st = explicit.get("solar_term")
    if st and st in SOLAR_TERM_SEASON:
        explicit["season"] = SOLAR_TERM_SEASON[st]
    else:
        auto["season"] = _season_by_month(m)

    # 2. weather (从 /tmp/weather.json 读)
    if weather:
        desc = ""
        try:
            desc = (weather["current_condition"][0]["weatherDesc"][0]["value"] or "").lower()
        except Exception:
            pass
        try:
            temp_c = int(weather["current_condition"][0].get("temp_C", "20"))
        except Exception:
            temp_c = 20
        if "rain" in desc or "shower" in desc or "drizzle" in desc:
            auto["weather"] = "rain"
        elif "snow" in desc:
            auto["weather"] = "snow"
        elif "sun" in desc or "clear" in desc:
            auto["weather"] = "sun"
        elif "cloud" in desc or "overcast" in desc:
            auto["weather"] = "cloudy"
        elif "wind" in desc:
            auto["weather"] = "wind"
        # temp 由 weather + temp_C 联判
        if temp_c <= 12 or auto.get("weather") in ("snow",):
            auto["temp"] = "cold"
        elif temp_c >= 24:
            auto["temp"] = "warm"

    # 3. time_of_day (由小时判)
    if h < 5:
        auto["time_of_day"] = "凌晨"
    elif h < 8:
        auto["time_of_day"] = "早"
    elif h < 11:
        auto["time_of_day"] = "早"
    elif h < 14:
        auto["time_of_day"] = "午"
    elif h < 17:
        auto["time_of_day"] = "下午"
    elif h < 19:
        auto["time_of_day"] = "傍晚"
    elif h < 23:
        auto["time_of_day"] = "夜"
    else:
        auto["time_of_day"] = "深夜"

    # 4. festival
    f = FESTIVALS.get((m, now.day))
    if f:
        auto["festival"] = f

    # 5. weekday
    auto["weekday"] = "weekend" if wd >= 5 else "weekday"

    return {"explicit": explicit, "auto": auto}


def _dim_hits(text: str, dim: str) -> set[str]:
    """摘录/书名/作者合并后, 匹配 KW[dim] 词表, 返命中的 dim_value 集合。"""
    blob = text or ""
    matches: set[str] = set()
    for kw, val in KW.get(dim, {}).items():
        if kw and kw in blob:
            matches.add(val)
    return matches


# ----- 评分 & 选条 -----

def score_excerpt(e: dict, book: dict, ctx: dict) -> tuple[int, set[str]]:
    """返 (score, all_auto_hit_dims)。
    规则:
      - explicit 命中 (value 真的在 content 词表里): +5
      - auto 命中:    +1 per dim (一个 dim 算一次)
      - 阈值:         score ≥ 2 视为"合适"
    """
    text_parts = [e.get("content", ""), book.get("name", ""), book.get("author", "")]
    blob = "\n".join(text_parts)
    score = 0
    auto_hit_dims: set[str] = set()

    for dim in ("season", "solar_term", "weather", "temp", "time_of_day", "festival", "weekday"):
        hits = _dim_hits(blob, dim)
        if not hits:
            continue
        if dim in ctx["explicit"]:
            exp_v = ctx["explicit"][dim]
            # 验证 explicit value 真的命中
            if exp_v in hits:
                score += 5
            elif dim == "season" and ctx["explicit"].get("solar_term"):
                # season 由 solar_term 派生: SOLAR_TERM_SEASON[st] 必须在 hits
                st = ctx["explicit"]["solar_term"]
                if SOLAR_TERM_SEASON.get(st) in hits:
                    score += 5
        else:
            auto_hit_dims.add(dim)  # 一个 dim 算一次
    score += len(auto_hit_dims)
    return score, auto_hit_dims


def pick(pool: list[dict], books: dict, ctx: dict, rng: random.Random, force_random: bool = False) -> tuple[dict, dict, int]:
    """返 (excerpt, book, score); fallback 时 score=0。
    force_random=True: 跳过 dim 匹配, 走纯随机 (用户没给条件时)。
    """
    if force_random or not ctx["explicit"]:
        # 无 explicit dim → 走随机, 但 auto 有 ≥3 dim 命中仍给分 (场景贴)
        e = rng.choice(pool)
        return e, books.get(e.get("bookId"), {}), 0
    scored = []
    for e in pool:
        b = books.get(e.get("bookId"), {})
        s, _ = score_excerpt(e, b, ctx)
        if s >= 2:  # 至少 1 explicit 或 ≥ 2 auto
            scored.append((s, e, b))
    if not scored:
        return rng.choice(pool), books.get(rng.choice(pool).get("bookId"), {}), 0
    scored.sort(key=lambda x: -x[0])
    top = scored[0][0]
    cands = [t for t in scored if t[0] == top]
    chosen = rng.choice(cands)
    return chosen[1], chosen[2], chosen[0]


# ----- 渲染 -----

_DIM_CN = {
    "season": {v: v for v in "春夏秋冬"},
    "solar_term": {v: v for v in SOLAR_TERM_SEASON},
    "weather": {"rain": "雨", "snow": "雪", "sun": "晴", "wind": "风", "cloudy": "阴"},
    "temp": {"cold": "寒", "warm": "暖"},
    "time_of_day": {"凌晨": "凌晨", "早": "晨", "午": "午", "下午": "午后", "傍晚": "傍晚", "夜": "夜", "深夜": "深夜"},
    "festival": {v: v for v in FESTIVALS.values()},
    "weekday": {"weekend": "周末", "weekday": "工作日"},
}


def render_card(e: dict, book: dict, ctx: dict, score: int) -> str:
    name = book.get("name") or "未知书名"
    author = book.get("author") or ""
    if author:
        cite = f"**{author}《{name}》**"
    else:
        cite = f"— 《{name}》"
    content = (e.get("content") or "").strip()
    idea = (e.get("idea") or "").strip()

    # 卡头 = explicit 全显 + auto 最多 1 个最强 (time_of_day > weather > festival > season)
    head_parts: list[str] = []
    for dim in ("solar_term", "festival", "season", "weather", "temp", "time_of_day", "weekday"):
        if dim in ctx["explicit"]:
            v = ctx["explicit"][dim]
            head_parts.append(_DIM_CN[dim].get(v, v))
    if not head_parts:
        for dim in ("time_of_day", "weather", "festival", "season", "weekday"):
            if dim in ctx["auto"]:
                head_parts.append(_DIM_CN[dim].get(ctx["auto"][dim], ctx["auto"][dim]))
                break
    head = " · ".join(head_parts) if head_parts else "随缘"

    out = [
        f"## {head}",
        "",
        f"> {cite}",
        "",
        f"> {content}",
    ]
    if idea:
        out += ["", f"_批注:_ {idea}"]
    out += ["", f"_(score={score})_"]
    return "\n".join(out)


# ----- CLI -----

def _parse_kv(args: list[str]) -> dict:
    out = {}
    for a in args:
        if "=" not in a:
            continue
        k, v = a.split("=", 1)
        out[k] = v
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="从 recto aggregate 挑一条合适的书摘")
    ap.add_argument("--seed", type=int, default=None, help="固定随机种子")
    ap.add_argument("--print-context", action="store_true", help="只打印当前 context 不选条")
    ap.add_argument("--text", nargs="*", default=None, metavar="K=V", help="手动加 'season=夏 weather=雨' 这种 override (空格分隔 k=v)")
    ap.add_argument("--random", action="store_true", help="跳过 dim 匹配, 纯随机")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    now = datetime.datetime.now()
    weather = _read_weather()
    ctx = derive_context(now, weather)

    # 手动 override (explicit) 注入
    if args.text:
        for tok in args.text:
            if "=" not in tok:
                continue
            k, v = tok.split("=", 1)
            if k not in KW:
                continue
            # 接受两种: 已经是 dim_value (如 "夏"), 或词表里的 kw (如 "夏天" → "夏")
            if v in set(KW[k].values()):
                ctx["explicit"][k] = v
            elif v in KW[k]:
                ctx["explicit"][k] = KW[k][v]
        # 联动: solar_term → season
        if "solar_term" in ctx["explicit"]:
            st = ctx["explicit"]["solar_term"]
            if st in SOLAR_TERM_SEASON:
                ctx["explicit"]["season"] = SOLAR_TERM_SEASON[st]
        # 联动: 冬 → 冷
        if ctx["explicit"].get("season") == "冬":
            ctx["explicit"]["temp"] = "cold"
        # 联动: festival → season (部分)
        _FEST_SEASON = {"元旦": "冬", "情人节": "冬", "圣诞节": "冬", "平安夜": "冬", "跨年夜": "冬"}
        f = ctx["explicit"].get("festival")
        if f in _FEST_SEASON:
            ctx["explicit"]["season"] = _FEST_SEASON[f]

    if args.print_context:
        print(json.dumps({"now": now.isoformat(), "ctx": ctx}, ensure_ascii=False, indent=2))
        return 0

    pool, books = load_pool()
    if not pool:
        print("(aggregate 空, 没法挑)", file=sys.stderr)
        return 1

    e, b, score = pick(pool, books, ctx, rng, force_random=args.random)
    print(render_card(e, b, ctx, score))
    return 0


if __name__ == "__main__":
    sys.exit(main())
