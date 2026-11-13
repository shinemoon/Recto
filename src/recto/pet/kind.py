"""这一趟是什么事 —— 遭遇类型池。

旧做法是"每回必然撞上一场打": 十回故事里九回在拔剑。可读书记录里大半时间是
坐着看字 —— 不是每天都在渡口遇见刀客。故事之所以像复读机, 是因为骨架只有一副。

这里按**这段时间的阅读纹理**挑一类事, 交手只是其中一种:

    斗 fight    —— 动了手 (有对手、有招式、有伤)
    会 talk     —— 见一个人, 把话说成或说崩
    观 observe  —— 撞见一处景 / 一桩事, 看见别人看不见的那一层
    学 learn    —— 学一门手艺, 磨一门功夫
    救 rescue   —— 有人遭难, 伸手
    行 travel   —— 只是赶路, 路上有一处景一个人

选了哪一类, 后端就只出那一类该有的判定 (``checks`` 的偏好), 也不再有"必须有交手"
这条 —— ``duel`` 只在 ``fight`` 时才算。同一名字 / 故事日 / 语料 → 同一类 (可复现),
且**不连着两回重样**。
"""
from __future__ import annotations

import os
import random
from typing import Any

#: 六类事。``cn`` 是页面上的徽标字, ``shape`` 是给说书人的骨架提示 (不进正文)。
KINDS: dict[str, dict[str, str]] = {
    "fight": {
        "cn": "斗",
        "label": "动了手",
        "checks": "any",
        "shape": ("这一趟撞上了人, 且谈不拢 —— 照 duel 写那一场: 起手、换招、见血、收势。"
                  "打完这一趟仍要落地一处景或一件物, 别写成纯打斗。"),
    },
    "talk": {
        "cn": "会",
        "label": "会了一个人",
        "checks": "person",
        "shape": ("这一趟是去见一个人 (赴约、问话、还一件东西)。没有交手 —— 全部的分量在"
                  "「话怎么说、对方怎么看它」。走的时候关系变了: 近了、远了, 或换了一种看法。"),
    },
    "observe": {
        "cn": "观",
        "label": "看见了一桩事",
        "checks": "person",
        "shape": ("这一趟是去看 / 去听: 一处从前没留意的景, 或者一桩别人不当回事的小事。"
                  "它看见的那一层, 是这段日子磨出来的眼力 (观气 / 看破) 给它的。"
                  "不解决什么, 只是看明白了。"),
    },
    "learn": {
        "cn": "学",
        "label": "磨了一门功夫",
        "checks": "road",
        "shape": ("这一趟是去学 / 去练: 找一个由头把一门手艺再磨一遍 (辨药、抄书、"
                  "打坐、拆招)。没有对手, 对手是它自己那只不够稳的手。"
                  "结尾要有一次「比从前稳了一点」的具体动作。"),
    },
    "rescue": {
        "cn": "救",
        "label": "伸手帮了一个人",
        "checks": "road",
        "shape": ("这一趟路上有人遭了难 (翻了的车、病倒的人、被围的牲口)。它不是侠客, "
                  "只做手边能做的那一点 —— 但它做了。写成动作与代价, 不要写成义举宣言。"),
    },
    "travel": {
        "cn": "行",
        "label": "走了一趟远路",
        "checks": "road",
        "shape": ("这一趟只是赶路: 过一处地形, 宿一个地方, 遇见一个只说三句话的人。"
                  "分量全在「路上是什么样子」—— 天气、脚程、随身那点东西。"),
    },
}

#: 语料类别 → 它会催出哪几类事 (权重加成)
_CAT_PULL: dict[str, dict[str, float]] = {
    "person": {"talk": 3.0, "fight": 1.8},
    "place": {"travel": 3.0, "observe": 1.6},
    "object": {"learn": 2.2, "observe": 1.0},
    "org": {"talk": 2.0, "fight": 1.4},
    "event": {"rescue": 2.2, "observe": 1.0},
    "term": {"learn": 2.0},
    "work": {"learn": 1.0, "observe": 0.6},
    "time": {"travel": 1.0},
}


def weights(*, cats: dict[str, Any] | None = None, hp_ratio: float = 1.0,
            level: int = 1, last_kind: str = "", has_terms: bool = True,
            days: int = 1) -> dict[str, float]:
    """各类事的权重。**没有语料就不打架** (没素材不该凭空生出对手)。"""
    w = {k: 1.6 for k in KINDS}
    w["talk"] = 2.0
    w["observe"] = 2.0
    w["learn"] = 1.8
    w["travel"] = 1.8
    w["rescue"] = 1.0

    cats = {str(k): float(v or 0) for k, v in (cats or {}).items()}
    total = sum(cats.values())
    if total > 0:
        for cat, pull in _CAT_PULL.items():
            share = cats.get(cat, 0.0) / total
            if share <= 0:
                continue
            for kind, add in pull.items():
                w[kind] += add * share

    if hp_ratio < 0.45:          # 带着伤, 不该再往刀口上撞
        w["fight"] *= 0.08
        w["travel"] += 1.6
        w["observe"] += 1.0
    if int(level) <= 3:          # 刚出门, 多学少斗
        w["learn"] += 1.4
        w["fight"] *= 0.7
    if int(days) >= 30:          # 走得远, 路上的事就多
        w["travel"] += 1.0
    if last_kind and last_kind in w:   # 不连着两回重样
        w[last_kind] *= 0.12
    if not has_terms:
        w["fight"] = 0.0
        w["travel"] += 2.6
        w["observe"] += 1.6
        w["learn"] += 0.8
    return {k: max(0.0, v) for k, v in w.items()}


def _why(kind: str, cats: dict[str, Any], *, hp_ratio: float, level: int,
         has_terms: bool) -> str:
    """一句人话 (给说书人看, 也进页面) —— 说清"为什么这一趟是这一类"。"""
    top = sorted(((int(v or 0), k) for k, v in (cats or {}).items()), reverse=True)
    top_cat = top[0][1] if top else ""
    words = {
        "person": "这一路撞见的都是人", "place": "这一路记下的多是地方",
        "object": "这一路见的多是器物", "org": "这一路绕不开势力",
        "event": "这一路听说的多是事", "term": "这一路琢磨的多是门道",
        "work": "这一路翻的多是名目", "time": "这一路过的多是时辰",
    }
    lead = words.get(top_cat, "")
    if kind == "fight":
        base = "这一趟谈不拢, 动了手"
    elif kind == "talk":
        base = "这一趟要去见一个人"
    elif kind == "observe":
        base = "这一趟是去看、去听"
    elif kind == "learn":
        base = "这一趟是把手上的功夫再磨一遍"
    elif kind == "rescue":
        base = "这一趟路上有人遭了难"
    else:
        base = "这一趟只是赶路"
    bits = [base]
    if lead:
        bits.append(lead)
    if hp_ratio < 0.45:
        bits.append("带着伤, 不宜再战")
    elif int(level) <= 3:
        bits.append("刚出门不久")
    if not has_terms:
        bits.append("这段日子没撞见什么可借的物事")
    return ", ".join(bits)


def choose(ev: dict[str, Any], ch: dict[str, Any], st: dict[str, Any] | None = None,
           *, seed: int | None = None, force: str = "") -> dict[str, Any]:
    """挑这一趟的事。返回 ``{kind, cn, label, shape, why, checks, weights}``。

    ``force`` / 环境变量 ``RECTO_PET_FORCE_KIND`` 可以钉死某一类 ——
    测试与排查用 (平时不该设: 类型本来就该由阅读纹理决定)。
    """
    st = st or {}
    enc = ev.get("encounters") or {}
    cats = enc.get("cats") or {}
    terms = enc.get("terms") or []
    try:
        from recto.pet import charsheet as _cs

        res = _cs.resources(ch)
        hp_now = float(res["hp"]["cur"])
        hp_max = float(res["hp"]["max"]) or 1.0
    except Exception:  # noqa: BLE001 — 算不出气血就当满值, 别让类型挑不出来
        hp_now, hp_max = 1.0, 1.0
    hp_ratio = max(0.0, min(1.0, hp_now / hp_max)) if hp_max else 1.0

    w = weights(cats=cats, hp_ratio=hp_ratio,
                level=int(ch.get("level") or 1),
                last_kind=str(st.get("last_kind") or ""),
                has_terms=bool(terms),
                days=int(ev.get("days") or 1))
    forced = str(force or os.environ.get("RECTO_PET_FORCE_KIND") or "").strip().lower()
    rng = random.Random((int(seed) if seed is not None else 0) ^ 0x5A17)
    names = list(w.keys())
    pick = (forced if forced in KINDS else
            str(rng.choices(names, weights=[w[n] for n in names], k=1)[0]))
    meta = dict(KINDS[pick])
    return {
        "kind": pick,
        "cn": meta["cn"],
        "label": meta["label"],
        "shape": meta["shape"],
        "checks": meta["checks"],
        "why": _why(pick, cats, hp_ratio=hp_ratio,
                    level=int(ch.get("level") or 1), has_terms=bool(terms)),
        "weights": {k: round(v, 2) for k, v in w.items()},
    }
