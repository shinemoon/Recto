"""书宠分区 —— 陪读精灵 (陪伴宠物) 的业务层。

**这一层是空壳, 但边界先划好。**

书宠要做的事 (还没做):
  · 记住你在读哪本书、读到哪一章
  · 在阅读器里探头说话 (搭话 / 提问 / 吐槽)
  · 长大 (亲密度 / 心情 / 记忆)

现在只做第一件事的前置: **模型接入配置**。
接一个 LLM 端点这件事, 语料分区早就做过了 (协议 / base_url / 模型 / key /
超时 / 思考开关 / 模型库)。那套零件在 `recto.core.llmconfig` 与
`recto.core.llm` 里 —— 本分区直接共用, 不再抄一遍。

**为什么单独一个分区而不塞进语料**: 语料是"离线批处理"(抽词条, 跑完就完);
书宠是"在线陪读"(每次搭话都要快, 要有上下文, 要能记住人)。两者的配置、
节奏、失败处理完全不同。共用零件, 不共用配置。
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from recto.core import llmconfig

# 配置落点: <data_dir>/pet/config.json (与语料的 <data_dir>/corpus/config.json 同级)
DEFAULT_CONFIG: dict[str, Any] = {
    # 接哪个模型 —— 字段名与语料的 llm 段完全一致 (共用 core.llmconfig 的零件)
    "llm": {
        "provider": "openai",
        "base_url": "",
        "api_key": "",
        "model": "",
        "timeout": 60,
        "max_tokens": 1024,
        "temperature": 0.8,
        # 思考开关: 陪聊一般不需要推理, 默认关
        "thinking": "off",
        "strict_no_reasoning": False,
    },
    # 模型库: 多套端点, 随手换 (书宠要快, 换一把 key 避开 429 很常见)
    "models": [],
    "active_model_id": "",
    # 陪伴人格 (还没接上, 先把位置留出来)
    "persona": {
        "name": "小墨",
        "style": "安静, 偶尔吐槽, 不剧透",
        "chattiness": 3,      # 1-5: 主动搭话的频率
    },
    # 陪伴参数 (还没接上)
    "companion": {
        "enabled": False,
        "auto_chat": True,      # 阅读时主动搭话
        "max_context_chars": 1200,
    },
}

_LOCK = threading.Lock()


def _pet_root() -> Path:
    """书宠配置根。优先读 Recto 配置里的 [pet] dir, 环境变量可覆盖。"""
    try:
        from recto.core import paths as _paths

        return Path(_paths.PATHS.pet_dir)
    except Exception:  # noqa: BLE001 — 单独跑测试时没有 Recto 配置也要能用
        env = os.environ.get("RECTO_PET_DIR")
        if env:
            return Path(env)
        return Path(__file__).resolve().parents[2] / "data" / "pet"


DATA_DIR = _pet_root()
CONFIG_PATH = DATA_DIR / "config.json"


def load_config() -> dict[str, Any]:
    """读配置; 不存在则写默认。深合并 (缺的键跟默认值走)。"""
    return llmconfig.load_config(CONFIG_PATH, DEFAULT_CONFIG)


def save_config(cfg: dict[str, Any]) -> None:
    """原子写配置 (含 api key → 权限 600)。"""
    llmconfig.save_config(CONFIG_PATH, cfg)


def masked_config() -> dict[str, Any]:
    """对外输出一份配置 (key 脱敏)。"""
    cfg = load_config()
    return llmconfig.masked_view(cfg)


# ------------------------------------------------------------------ 接口层

def get_config() -> tuple[int, dict]:
    return 200, masked_config()


def set_config(body: dict) -> tuple[int, dict]:
    """保存配置。键的判定规则与语料一致 (脱敏串 / 空串 / "***" 都表示"不改")。"""
    cfg = load_config()
    incoming = body.get("llm") or {}
    for k in ("provider", "base_url", "model"):
        if k in incoming and incoming[k] is not None:
            cfg["llm"][k] = incoming[k]
    key = llmconfig.resolve_api_key(incoming.get("api_key"), cfg["llm"].get("api_key", ""))
    cfg["llm"]["api_key"] = key
    # 改了跟当前绑定模型不同的值 → 解除绑定 (原样再存则保留)
    active = cfg.get("active_model_id") or ""
    if active:
        entry = next((m for m in (cfg.get("models") or []) if m.get("id") == active), None)
        same = True
        if entry:
            for k in ("provider", "base_url", "model"):
                if k in incoming and incoming[k] is not None and incoming[k] != entry.get(k):
                    same = False
            if key != (entry.get("api_key") or ""):
                same = False
        else:
            same = False
        if not same:
            cfg["active_model_id"] = ""
    for k in ("timeout", "max_tokens", "temperature"):
        if k in incoming and incoming[k] is not None:
            try:
                cfg["llm"][k] = type(cfg["llm"].get(k, 0))(incoming[k])
            except (TypeError, ValueError):
                pass
    if incoming.get("thinking") is not None:
        cfg["llm"]["thinking"] = llmconfig.normalize_thinking(incoming["thinking"])
    if incoming.get("strict_no_reasoning") is not None:
        cfg["llm"]["strict_no_reasoning"] = bool(incoming["strict_no_reasoning"])
    if isinstance(body.get("persona"), dict):
        cfg["persona"].update(body["persona"])
    if isinstance(body.get("companion"), dict):
        cfg["companion"].update(body["companion"])
    save_config(cfg)
    return 200, {"ok": True, "config": masked_config()}


def llm_test(body: dict) -> tuple[int, dict]:
    """连通性测试。前端回传的可能只是脱敏串 —— 那是展示值, 不是真 key。"""
    from recto.core import llm as llm_mod

    cfg = load_config()
    test_cfg = dict(cfg["llm"])
    test_cfg["api_key"] = llmconfig.resolve_api_key(
        body.get("api_key"), cfg["llm"].get("api_key", ""))
    for k in ("base_url", "model", "provider"):
        if body.get(k):
            test_cfg[k] = str(body[k]).strip()
    return 200, llm_mod.test_llm(test_cfg)


# ------------------------------------------------------------------ 模型库

def models_get() -> tuple[int, dict]:
    cfg = load_config()
    return 200, {
        "models": llmconfig.model_library(cfg),
        "active_model_id": cfg.get("active_model_id", ""),
    }


def models_set(body: dict) -> tuple[int, dict]:
    """整库覆盖。body.models = [{id?,name,provider,base_url,api_key,model,note}]"""
    raw_list = body.get("models")
    if not isinstance(raw_list, list):
        return 400, {"error": "缺少 models 列表"}
    cfg = load_config()
    old = {m.get("id"): m for m in (cfg.get("models") or [])}
    used: set[str] = set()
    out: list[dict[str, Any]] = []
    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        entry = dict(raw)
        mid = (entry.get("id") or "").strip()
        if entry.pop("from_current", False):
            src = cfg["llm"]
            entry["provider"] = src.get("provider", "openai")
            entry["base_url"] = src.get("base_url", "")
            entry["model"] = src.get("model", "")
            entry["api_key"] = src.get("api_key", "")
            if llmconfig.is_masked(entry.get("api_key", "")):
                entry["api_key"] = (old.get(mid) or {}).get("api_key", "")
        entry["api_key"] = llmconfig.resolve_api_key(
            entry.get("api_key"), (old.get(mid) or {}).get("api_key", ""))
        out.append(llmconfig.normalize_model_entry(entry, used))
    cfg["models"] = out
    active = (body.get("active_model_id") or cfg.get("active_model_id") or "").strip()
    if active and not any(m["id"] == active for m in out):
        active = ""
    cfg["active_model_id"] = active
    save_config(cfg)
    return 200, {"ok": True, "models": llmconfig.model_library(cfg),
                 "active_model_id": cfg["active_model_id"]}


def models_apply(body: dict) -> tuple[int, dict]:
    mid = (body.get("id") or "").strip()
    if not mid:
        return 400, {"error": "缺少 id"}
    cfg = load_config()
    if not llmconfig.apply_model_to_llm(cfg, mid):
        return 404, {"error": f"模型不存在: {mid}"}
    save_config(cfg)
    return 200, {"ok": True, "config": masked_config()}


def models_delete(mid: str) -> tuple[int, dict]:
    cfg = load_config()
    before = len(cfg.get("models") or [])
    cfg["models"] = [m for m in (cfg.get("models") or []) if m.get("id") != mid]
    if len(cfg["models"]) == before:
        return 404, {"error": f"模型不存在: {mid}"}
    if cfg.get("active_model_id") == mid:
        cfg["active_model_id"] = ""
    save_config(cfg)
    return 200, {"ok": True, "models": llmconfig.model_library(cfg),
                 "active_model_id": cfg["active_model_id"]}


def models_test(mid: str) -> tuple[int, dict]:
    from recto.core import llm as llm_mod

    cfg = load_config()
    entry = next((m for m in (cfg.get("models") or []) if m.get("id") == mid), None)
    if not entry:
        return 404, {"error": f"模型不存在: {mid}"}
    key = entry.get("api_key", "")
    if llmconfig.is_masked(key):
        key = cfg["llm"].get("api_key", "")
    test_cfg = {
        "provider": entry.get("provider", "openai"),
        "base_url": entry.get("base_url", ""),
        "api_key": key,
        "model": entry.get("model", ""),
        "timeout": cfg["llm"].get("timeout", 60),
        "max_tokens": 32,
        "temperature": 0.0,
    }
    return 200, llm_mod.test_llm(test_cfg)


def presets() -> tuple[int, dict]:
    """端点预设 —— 陪聊要**快**, 首选 flash 类; 推理模型不适合。"""
    from recto.core.llm import looks_reasoning_model

    items = []
    for p in _PRESETS:
        e = dict(p)
        e["reasoning_model"] = looks_reasoning_model(p.get("model", ""), p.get("base_url", ""))
        items.append(e)
    return 200, {"presets": items, "tips": _TIPS}


_PRESETS: list[dict[str, Any]] = [
    {
        "id": "agnes-2.5-flash",
        "name": "agnes-2.5-flash ★推荐",
        "provider": "openai",
        "base_url": "https://apihub.agnes-ai.com/v1",
        "model": "agnes-2.5-flash",
        "note": "非推理模型, 搭话快 (~1s). 需要 agnes key.",
    },
    {
        "id": "agnes-3.0-flash",
        "name": "agnes-3.0-flash",
        "provider": "openai",
        "base_url": "https://apihub.agnes-ai.com/v1",
        "model": "agnes-3.0-flash",
        "note": "较快, 偶尔限流 (429).",
    },
    {
        "id": "minimax-m3",
        "name": "MiniMax-M3 ⚠不推荐",
        "provider": "openai",
        "base_url": "https://api.minimaxi.com/v1",
        "model": "MiniMax-M3",
        "note": "推理模型: token 烧在 thinking 里, 搭话会慢一大截.",
    },
    {
        "id": "custom-openai",
        "name": "自定义 (OpenAI 兼容)",
        "provider": "openai",
        "base_url": "",
        "model": "",
        "note": "手填 base_url / model / key.",
    },
    {
        "id": "custom-anthropic",
        "name": "自定义 (Anthropic)",
        "provider": "anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "model": "",
        "note": "手填 base_url / model / key.",
    },
]

_TIPS = [
    "陪聊是**在线**任务 —— 选 flash 类模型, 慢 5 秒就没法聊了.",
    "推理模型 (MiniMax-M3 / o1 / 带 thinking 的) 不适合: token 全烧在 thinking 里.",
    "max_tokens 给 512~1024 就够 —— 一句话不该写一篇.",
    "温度给 0.8~1.0: 陪伴要有点起伏, 太冷显得像客服.",
]


def status() -> tuple[int, dict]:
    """书宠分区状态 —— 现在只报"配没配好"。"""
    cfg = load_config()
    llm = cfg["llm"]
    ready = bool(llm.get("base_url") and llm.get("model") and llm.get("api_key"))
    return 200, {
        "ready": ready,
        "llm": llmconfig.masked_view(cfg)["llm"],
        "persona": cfg.get("persona", {}),
        "companion": cfg.get("companion", {}),
        "config_path": str(CONFIG_PATH),
    }


def write_state(state: dict[str, Any]) -> None:
    """存书宠的运行时状态 (以后放心情 / 记忆 / 亲密度)。现在只是占位。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "state.json"
    tmp = path.with_name(f".state.json.{os.getpid()}.tmp")
    with _LOCK:
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)


def read_state() -> dict[str, Any]:
    try:
        return json.loads((DATA_DIR / "state.json").read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
