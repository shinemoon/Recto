"""LLM 配置的共用零件 —— 端点 / 模型库 / key 脱敏。

**为什么放在 core**: 语料 (抽词条) 与书宠 (陪伴对话) 是两个功能, 但"接一个
LLM 端点"这件事完全一样: 协议 / base_url / 模型 / key / 超时 / 思考开关,
外加一个"模型库" (多套端点, 随手换, 避开 429)。

把这套逻辑抽到 core, 两个分区共用一份 —— 否则书宠就得再抄一遍脱敏/覆盖
判定, 而那几行正是**最容易抄错**的地方 (见 `resolve_api_key` 的注释:
脱敏串是 truthy, 前端的"空 = 不改"与服务端的"*** = 不改"两条兜底都不会触发,
真 key 会被脱敏串静默覆盖, 之后每次请求 401)。

不含任何网络调用 —— 发请求在 `core/llm.py`。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

# 模型 id 允许字母数字下划线连字符
_ID_RE = re.compile(r"[^a-zA-Z0-9_\-]+")

# 思考开关的三档
THINK_MODES = ("off", "on", "auto")

# 支持的协议
PROVIDERS = ("openai", "anthropic")


# ------------------------------------------------------------------ 基础零件

def mask_key(key: str) -> str:
    """脱敏显示 api key (只留头 6 尾 4)。"""
    if not key:
        return ""
    if len(key) <= 10:
        return key[:2] + "*" * (len(key) - 2)
    return key[:6] + "*" * 8 + key[-4:]


def is_masked(key: str) -> bool:
    """判断一个 key 是不是**脱敏串** (mask_key 的产物)。

    为什么需要这个:
        GET 接口都会把 key 脱敏后发给前端。前端保存时会把那个脱敏串原样回传 ——
        它是 truthy, 所以前端 "空 = 不改" 和服务端 "== '***' = 不改" 两条兜底
        都不触发。结果真 key 被脱敏串**覆盖**, 之后每次请求 401。

    所以凡是要"拿 key 当真值用"的地方, 必须先过这一关。
    """
    return bool(key) and "*" in key


def resolve_api_key(incoming: str, previous: str) -> str:
    """决定保存时该写入哪个 key。

    incoming  = 前端回传的值
    previous  = 磁盘上已有的值

    规则:
        - 空串          → 不改 (前端没填)
        - "***"         → 不改 (前端明确说"沿用旧值")
        - 脱敏串 (含 *) → 不改 (前端拿的是展示用脱敏值, 不是真 key)
        - 其它          → 用新值
    """
    val = (incoming or "").strip()
    if not val or val == "***" or is_masked(val):
        return previous
    return val


def normalize_thinking(value: Any, default: str = "off") -> str:
    v = str(value or "").strip().lower()
    return v if v in THINK_MODES else default


def normalize_provider(value: Any) -> str:
    v = str(value or "").strip().lower()
    return v if v in PROVIDERS else "openai"


def _slug(name: str) -> str:
    """从名字生成 id: 保留字母数字下划线连字符, 其余转 -。"""
    s = _ID_RE.sub("-", (name or "").strip()).strip("-")
    return s.lower() or "model"


def normalize_model_entry(raw: dict[str, Any], used_ids: set[str]) -> dict[str, Any]:
    """归一化一条模型配置。保证有 id / provider / base_url / model 字段。

    磁盘上存的是**真 key** (调用要用)。data/ 整个目录已 gitignore。
    对外输出时才脱敏 (见 model_library)。
    """
    raw = raw or {}
    name = (raw.get("name") or "").strip()
    mid = (raw.get("id") or "").strip() or _slug(name) or "model"
    base = mid
    n = 2
    while mid in used_ids:
        mid = f"{base}-{n}"
        n += 1
    used_ids.add(mid)
    return {
        "id": mid,
        "name": name or mid,
        "provider": normalize_provider(raw.get("provider")),
        "base_url": (raw.get("base_url") or "").strip().rstrip("/"),
        "api_key": (raw.get("api_key") or "").strip(),
        "model": (raw.get("model") or "").strip(),
        "note": (raw.get("note") or "").strip(),
        "thinking": normalize_thinking(raw.get("thinking")),
    }


def model_library(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """返回模型库 (脱敏 key, 带 has_key / reasoning_model 标记)。"""
    from .llm import looks_reasoning_model

    out = []
    for m in cfg.get("models") or []:
        e = dict(m)
        e["api_key"] = mask_key(e.get("api_key", ""))
        e["has_key"] = bool(m.get("api_key"))
        e["thinking"] = normalize_thinking(e.get("thinking"))
        # 只读标记: UI 用它给推理模型挂角标 / 提示换模型
        e["reasoning_model"] = looks_reasoning_model(e.get("model", ""), e.get("base_url", ""))
        out.append(e)
    return out


def apply_model_to_llm(cfg: dict[str, Any], model_id: str) -> bool:
    """把模型库里的某条套到 llm 段。找不到返回 False。

    注意: llm 段的 timeout / max_tokens / temperature 不动 —— 模型库只管
    "连哪台机器 + 跑哪个模型 + 用哪把 key"。
    """
    mid = (model_id or "").strip()
    if not mid:
        return False
    for m in cfg.get("models") or []:
        if m.get("id") == mid:
            for k in ("provider", "base_url", "model"):
                if m.get(k):
                    cfg["llm"][k] = m[k]
            cfg["llm"]["thinking"] = normalize_thinking(m.get("thinking"))
            # key: 只在条目自己**没有** key 时才沿用 llm 段的;
            # 条目有 key 就用它 (哪怕它已被脱敏串污染, 也不该用
            # llm 段那个可能被污染的值去覆盖)。
            if m.get("api_key"):
                cfg["llm"]["api_key"] = m["api_key"]
            cfg["active_model_id"] = mid
            return True
    return False


def llm_section(cfg: dict[str, Any]) -> dict[str, Any]:
    """取出 llm 段 (没有就空 dict)。"""
    sec = cfg.get("llm")
    return sec if isinstance(sec, dict) else {}


def masked_view(cfg: dict[str, Any]) -> dict[str, Any]:
    """对外输出一份配置 (key 脱敏)。"""
    out = json.loads(json.dumps(cfg))
    sec = out.get("llm")
    if isinstance(sec, dict):
        sec["api_key"] = mask_key(sec.get("api_key", ""))
        sec["has_key"] = bool(llm_section(cfg).get("api_key"))
    out["models"] = model_library(cfg)
    return out


# ------------------------------------------------------------------ 配置文件

def load_config(path: Path, defaults: dict[str, Any]) -> dict[str, Any]:
    """读一份配置; 不存在就写默认。深合并 (缺的键跟默认值走)。"""
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    if not p.exists():
        save_config(p, defaults)
        return json.loads(json.dumps(defaults))
    try:
        with p.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        raw = {}
    return deep_merge(defaults, raw if isinstance(raw, dict) else {})


def save_config(path: Path, cfg: dict[str, Any]) -> None:
    """原子写配置。文件含 api key → 权限 600。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def deep_merge(base: dict, over: dict) -> dict:
    """递归合并, over 覆盖 base。"""
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out
