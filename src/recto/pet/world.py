"""书宠的架空世界 —— 它自己活着的那片江湖。

**为什么要有这一层**: 书宠读的书五花八门 (武侠、散文、异国长篇), 而它的
故事必须是一个**自洽的世界**。如果地名、宗门、人物都从当天读的那本书里
借, 换一本书世界就崩了 —— 昨天在江湖边陲, 今天在伦敦的雨里, 故事就成了
拼贴。所以它的世界是**自造的、慢慢长厚的**: 一份地名 / 势力 / 人物 / 器物
的名单, 每次写故事都从这份名单里取, 新造的名字再记回来。

**与书的关系**: 书只提供"母题与意象" (见 ``lore`` 的 seeds), 一个名字都不借。
这一层里出现的所有专名都是模型自造或内置的架空候选, 与任何现实作品无关。

**存盘**: ``<pet_dir>/world.json`` (600)。读得勤 (每次写故事都要) → 缓存 2 秒。
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

log = logging.getLogger("recto.pet.world")

#: 每类名字最多存多少 (防无限膨胀, 也防提示词被塞满)
MAX_PER_KIND = 40

#: 模型不可用时的兜底世界 —— 全部自造, 与任何现实作品无关。
#: 先用它把故事开起来, 等模型通了再逐步替换 (``ensure(force=True)``)。
FALLBACK: dict[str, Any] = {
    "name": "残照江湖",
    "era": "不知何朝何代, 官道失修, 山头林立",
    "law": "江湖上认两样东西: 手里的本事, 和说过的话。",
    "regions": [
        {"name": "寒鸦渡", "note": "一条结不了冰的河, 渡口的灯常年亮着"},
        {"name": "青芜原", "note": "风大草深, 走上半日见不到人"},
        {"name": "白鹿坡", "note": "坡下有处荒了多年的驿亭"},
        {"name": "碎玉滩", "note": "滩石碎得像摔过的玉, 水声盖得住说话声"},
        {"name": "断碑坪", "note": "一块字被磨平的石碑, 谁立的没人记得"},
        {"name": "旧仓城", "note": "粮仓比人多, 城墙有两处补过的缺口"},
    ],
    "factions": [
        {"name": "墨池剑庐", "note": "以剑立门, 讲规矩多过讲道理"},
        {"name": "铁砚堂", "note": "刀门, 收徒看手, 不看家世"},
        {"name": "静心斋", "note": "内功一脉, 门人多半话少"},
        {"name": "风雪阁", "note": "走轻功与消息, 江湖上的风向标"},
        {"name": "灵犀谷", "note": "杂学门, 医卜星相都教一点"},
        {"name": "听松府", "note": "旧日官面上的底子, 如今只管收租与办席"},
    ],
    "notables": [
        {"name": "柳跛子", "role": "渡口摆渡人", "note": "知道谁在什么时候过了河"},
        {"name": "沈七娘", "role": "旧仓城的布商", "note": "账算得比刀快"},
        {"name": "顾铁衣", "role": "游方护卫", "note": "话少, 价钱开得死"},
        {"name": "阿拙", "role": "驿亭里扫地的", "note": "十来岁, 谁都能使唤他"},
        {"name": "白先生", "role": "讲学不第的读书人", "note": "一口旧箱子比命重"},
    ],
    "things": [
        {"name": "半截木牌", "note": "上面只剩一个姓"},
        {"name": "钝口的短刀", "note": "刀刃有一处补过的缺口"},
        {"name": "旧砚", "note": "砚底刻着别人看不懂的记号"},
    ],
    "source": "fallback",
    "created_at": "",
    "updated_at": "",
}

_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"at": 0.0, "val": None}


def _schools() -> list[dict[str, str]]:
    """人物体系里的六门派 —— 它们必须是世界观里的势力, 否则它入了门却查无此地。"""
    try:
        from recto.pet import charsheet as cs

        return [{"name": str(x.get("name") or ""),
                 "note": str(x.get("desc") or x.get("tag") or "")[:60]}
                for x in cs.SCHOOLS if x.get("name")]
    except Exception:  # noqa: BLE001
        return []


def _merge_schools(w: dict[str, Any]) -> dict[str, Any]:
    """把六门派并进 factions (缺则补, 有则不动)。"""
    bucket = w.get("factions") if isinstance(w.get("factions"), list) else []
    have = {str((x or {}).get("name") or "") for x in bucket}
    for row in _schools():
        if row["name"] and row["name"] not in have and len(bucket) < MAX_PER_KIND:
            bucket.append(row)
            have.add(row["name"])
    w["factions"] = bucket
    return w


# ------------------------------------------------------------------ 存盘

def path() -> Path:
    from recto.pet import charsheet as cs

    return Path(cs.CHAR_PATH).parent / "world.json"


def _read() -> dict[str, Any] | None:
    try:
        raw = json.loads(path().read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or not raw.get("name"):
        return None
    return _normalize(raw)


def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
    out = dict(FALLBACK)
    out.update({k: v for k, v in raw.items() if k in out or k in ("source",)})
    out["name"] = str(out.get("name") or FALLBACK["name"])[:24]
    out["era"] = str(out.get("era") or FALLBACK["era"])[:120]
    out["law"] = str(out.get("law") or FALLBACK["law"])[:160]
    for kind in ("regions", "factions", "notables", "things"):
        items = out.get(kind)
        if not isinstance(items, list):
            items = []
        clean: list[dict[str, str]] = []
        seen: set[str] = set()
        for it in items:
            if isinstance(it, str):
                it = {"name": it, "note": ""}
            if not isinstance(it, dict):
                continue
            nm = str(it.get("name") or "").strip()[:24]
            if not nm or nm in seen:
                continue
            seen.add(nm)
            clean.append({"name": nm,
                          "note": str(it.get("note") or it.get("role") or "")[:80],
                          **({"role": str(it.get("role"))[:24]} if it.get("role") else {})})
        out[kind] = clean[:MAX_PER_KIND]
    return out


def save(w: dict[str, Any]) -> None:
    w = _normalize(w)
    w["updated_at"] = clock.iso_local()
    if not w.get("created_at"):
        w["created_at"] = w["updated_at"]
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    with _LOCK:
        tmp.write_text(json.dumps(w, ensure_ascii=False, indent=2), "utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, p)
    _CACHE.update({"at": time.time(), "val": dict(w)})


def load() -> dict[str, Any]:
    """当前世界 (没有就返回兜底副本, **不落盘** —— 等真要用时再 ensure)。"""
    cached = _CACHE.get("val")
    if cached is not None and time.time() - float(_CACHE.get("at") or 0) < 2.0:
        return dict(cached)
    raw = _read()
    w = _normalize(dict(raw) if raw else dict(FALLBACK))
    before = len(w.get("factions") or [])
    _merge_schools(w)
    if raw is not None and len(w.get("factions") or []) != before:
        # 门派是后来并进去的 —— 落一次盘, 免得每次读都并一遍
        try:
            save(w)
        except OSError:
            pass
    _CACHE.update({"at": time.time(), "val": dict(w)})
    return w


def exists() -> bool:
    return _read() is not None


# ------------------------------------------------------------------ 生成

def _gen_prompt() -> str:
    return (
        "你在为一个**完全架空**的武侠世界起名字与铺底。\n"
        "硬规则:\n"
        "1. 不得使用任何现实存在的书名、人名、地名、门派名、朝代名。全部自造。\n"
        "2. 名字两到四个字, 有江湖气, 不要生僻到读不出。\n"
        "3. 只要骨架, 不要情节。每条 note 一句话 (<= 24 字), 写它能给故事什么。\n"
        "4. 只输出 JSON, 不要解释。\n\n"
        '契约: {"name": "世界名", "era": "一句年代气质", "law": "这个世界的规矩",\n'
        ' "regions": [{"name": "", "note": ""}], "factions": [{"name": "", "note": ""}],\n'
        ' "notables": [{"name": "", "role": "身份", "note": ""}],\n'
        ' "things": [{"name": "", "note": ""}]}\n'
        "数量: regions 6, factions 6, notables 5, things 3。"
    )


def ensure(*, force: bool = False, use_model: bool = True) -> dict[str, Any]:
    """确保有一份世界。没有 (或 ``force``) 时让模型铺一次, 失败用内置兜底。

    失败**不抛** —— 后台心跳里抛异常等于这条线自己死了。
    """
    if not force and exists():
        return load()
    w = _normalize(dict(FALLBACK))
    if use_model:
        try:
            from recto.pet import jsonout, lore

            out = lore.chat(_gen_prompt(), "只输出 JSON。", max_tokens=900, temperature=0.8)
            data = jsonout.parse_obj(out) if out else None
        except Exception as e:  # noqa: BLE001
            log.warning("世界 · 生成失败, 用内置兜底: %s", e)
            data = None
        if isinstance(data, dict) and data.get("name"):
            w = _normalize({**data, "source": "model"})
            log.info("世界 · 模型铺底: %s · %d 地 %d 势力",
                     w["name"], len(w["regions"]), len(w["factions"]))
    if not w.get("created_at"):
        w["source"] = w.get("source") or "fallback"
    _merge_schools(w)
    save(w)
    return w


# ------------------------------------------------------------------ 取用

def all_names(w: dict[str, Any] | None = None) -> set[str]:
    """这个世界的全部专名 (校验"故事有没有借书里的名字"时当白名单用)。"""
    w = w or load()
    out = {str(w.get("name") or "")}
    for kind in ("regions", "factions", "notables", "things"):
        for it in w.get(kind) or []:
            nm = str((it or {}).get("name") or "")
            if nm:
                out.add(nm)
    out.discard("")
    return out


def brief(w: dict[str, Any] | None = None) -> dict[str, Any]:
    """给写故事那道工序的世界骨架 (只给名字与一句话, 不给整份文件)。"""
    w = w or load()
    return {
        "name": w.get("name"),
        "era": w.get("era"),
        "law": w.get("law"),
        "regions": [{"name": x["name"], "note": x.get("note") or ""}
                    for x in (w.get("regions") or [])[:8]],
        "factions": [{"name": x["name"], "note": x.get("note") or ""}
                     for x in (w.get("factions") or [])[:14]],
        # 它能入的门派 (与人物体系同一份名单)
        "sects": [x for x in _schools()],
        "notables": [{"name": x["name"], "role": x.get("role") or "",
                      "note": x.get("note") or ""}
                     for x in (w.get("notables") or [])[:8]],
        "things": [{"name": x["name"], "note": x.get("note") or ""}
                   for x in (w.get("things") or [])[:6]],
    }


#: 故事可以自己新造名字, 但要按类报回来 —— 世界才会越走越厚
KIND_OF = {"region": "regions", "faction": "factions", "person": "notables",
           "thing": "things"}


def remember(new_items: Any) -> list[str]:
    """把故事里新造的名字收进世界 (去重, 每类封顶)。返回真正新增的名字。"""
    if not isinstance(new_items, list) or not new_items:
        return []
    w = load()
    added: list[str] = []
    for it in new_items[:12]:
        if not isinstance(it, dict):
            continue
        kind = KIND_OF.get(str(it.get("kind") or "").strip())
        if not kind:
            continue
        nm = str(it.get("name") or "").strip()[:24]
        if not nm:
            continue
        bucket = w.get(kind) if isinstance(w.get(kind), list) else []
        if any(str((x or {}).get("name") or "") == nm for x in bucket):
            continue
        if len(bucket) >= MAX_PER_KIND:
            continue
        row = {"name": nm, "note": str(it.get("note") or "")[:80]}
        role = str(it.get("role") or "")[:24]
        if role:
            row["role"] = role
        bucket.append(row)
        w[kind] = bucket
        added.append(nm)
    if added:
        save(w)
        log.info("世界 · 新增 %s", "、".join(added))
    return added


def view() -> dict[str, Any]:
    w = load()
    return {**brief(w), "source": w.get("source") or "", "exists": exists(),
            "created_at": w.get("created_at") or "",
            "updated_at": w.get("updated_at") or "",
            "counts": {k: len(w.get(k) or [])
                       for k in ("regions", "factions", "notables", "things")}}
