"""配置管理: LLM 端点 / 解析参数 / 分类定义.

配置文件: <项目根>/data/config.json
结构:
    {
      "llm": {
        "provider": "openai" | "anthropic" | "minimax",
        "base_url": "https://api.minimaxi.com/v1",
        "api_key": "sk-...",
        "model": "MiniMax-M3",
        "timeout": 120,
        "max_tokens": 4096,
        "temperature": 0.2,
        "max_concurrency": 2
      },
      "parse": {
        "max_chars_per_chunk": 8000,
        "include_toc": true,
        "include_frontmatter": false
      },
      "categories": [ {"id": "person", "name": "人名", "hint": "...", "enabled": true}, ... ],
      "models": [ {"id":"m1","name":"agnes-2.5","provider":"openai",
                   "base_url":"...","api_key":"sk-...","model":"agnes-2.5-flash",
                   "note":"..."} ],
      "active_model_id": "m1"
    }
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any

# 路径全部由云阅 Recto 的配置派生: `[corpus] dir` (默认 <data_dir>/corpus)。
# 语料根之下:
#   config.json   本服务的配置 (LLM / 模型库 / 分类)
#   state.json    书库状态
#   outputs/      中间产物
#   *.json        固定导出 (slug / latest / index) —— 下游语料消费方直接扫这一层
PROJECT_ROOT = Path(__file__).resolve().parents[2]      # <仓库根> (src/recto/corpus/config.py)


def _corpus_root() -> Path:
    """语料根。配置优先, 环境变量 RECTO_CORPUS_DIR 可由 Recto 的 config 覆盖。"""
    try:
        from recto.core import paths as _paths

        return Path(_paths.PATHS.corpus_dir)
    except Exception:  # noqa: BLE001 — 单独跑测试时没有 Recto 配置也要能用
        return Path(os.environ.get("RECTO_CORPUS_DIR") or (PROJECT_ROOT / "data" / "corpus"))


DATA_DIR = _corpus_root()
OUTPUT_DIR = DATA_DIR / "outputs"
# 固定导出目录: 路径不随 epub 的 mtime / 大小变化, 方便下游长期引用
#   <slug>.json    — 每本书一份, slug = 标题 + 路径哈希 (稳定)
#   latest.json    — 最近一次成功产物 (下游只需读这一个)
#   index.json     — 清单
EXPORT_DIR = DATA_DIR
CONFIG_PATH = DATA_DIR / "config.json"

_LOCK = threading.Lock()

# 默认分类: 预渲染时按分类建索引
DEFAULT_CATEGORIES: list[dict[str, Any]] = [
    {
        "id": "person",
        "name": "人名",
        "hint": "人物姓名、称谓、别名、昵称。例: Adrian Mole, Mr. Mole, 帕迪",
        "enabled": True,
    },
    {
        "id": "place",
        "name": "地点",
        "hint": "地理地点、建筑物、街道、房间、区域。例: Nuneaton, Ashby Canal",
        "enabled": True,
    },
    {
        "id": "org",
        "name": "组织",
        "hint": "学校、公司、社团、政府机构、商店等有名字的组织。例: Ashby Comprehensive",
        "enabled": True,
    },
    {
        "id": "object",
        "name": "道具",
        "hint": "书中有名的物品: 玩具、礼物、书刊、乐器、宠物等。例: Dandelion Clock",
        "enabled": True,
    },
    {
        "id": "event",
        "name": "事件",
        "hint": "有专名或重要性的事件、仪式、比赛、灾难。例: The Great Flood of 1978",
        "enabled": True,
    },
    {
        "id": "time",
        "name": "时间",
        "hint": "书中明确的日期、时间点、年代、节日、学期。例: 1978, Christmas, New Year's Eve",
        "enabled": True,
    },
    {
        "id": "term",
        "name": "术语",
        "hint": "书中的专有名词、概念、口头禅、方言词、度量衡。例: 'Mad as a hatter'",
        "enabled": True,
    },
    {
        "id": "work",
        "name": "作品",
        "hint": "书中提到的书名、报章、电影、歌曲、电视剧等作品名。",
        "enabled": True,
    },
]


DEFAULT_CONFIG: dict[str, Any] = {
    "llm": {
        "provider": "openai",
        "base_url": "https://api.minimaxi.com/v1",
        "api_key": "",
        "model": "MiniMax-M3",
        "timeout": 180,
        "max_tokens": 8192,
        "temperature": 0.2,
        "max_concurrency": 2,
        # 思考开关: off(默认, 抽取不该用推理) / on(允许) / auto(视作 off)
        "thinking": "off",
        # 严格避免推理: **默认关** —— 关不掉思考的端点允许它思考照跑 (慢/费 token,
        # 但不会被拦死); 只有打开它才"响应里出现 thinking 就整轮中止".
        "strict_no_reasoning": False,
    },
    "parse": {
        "max_chars_per_chunk": 8000,
        "include_toc": True,
        "include_frontmatter": False,
    },
    "categories": DEFAULT_CATEGORIES,
    # 模型库: 可保存多套端点 (换 key / 换端点避开 429)
    "models": [],
    # 当前生效的模型条目 id (空 = 直接用 llm 段的值)
    "active_model_id": "",
}


def _deep_merge(base: dict, over: dict) -> dict:
    """递归合并, over 覆盖 base."""
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict[str, Any]:
    """读配置; 不存在则写默认."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
        return json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        raw = {}
    return _deep_merge(DEFAULT_CONFIG, raw)


def save_config(cfg: dict[str, Any]) -> None:
    """原子写配置."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


def mask_key(key: str) -> str:
    """脱敏显示 api key."""
    if not key:
        return ""
    if len(key) <= 10:
        return key[:2] + "*" * (len(key) - 2)
    return key[:6] + "*" * 8 + key[-4:]


def is_masked(key: str) -> bool:
    """判断一个 key 是不是**脱敏串** (mask_key 的产物).

    为什么需要这个:
        GET /api/models 和 /api/config 都会把 key 脱敏后发给前端.
        前端保存时会把那个脱敏串原样回传 —— 它是 truthy, 所以前端
        的 "空 = 不改" 分支不触发; 服务端的 "== '***' = 不改" 分支
        也不触发. 结果真 key 被脱敏串**覆盖**, 之后每次请求 401.

    所以凡是要"拿 key 当真值用"的地方, 必须先过这一关.
    """
    return bool(key) and "*" in key


# ───────────────────────── 模型库 ─────────────────────────

_ID_RE = re.compile(r"[^a-zA-Z0-9_\-]+")


def _slug(name: str) -> str:
    """从名字生成 id: 保留字母数字下划线连字符, 其余转 -."""
    s = _ID_RE.sub("-", (name or "").strip()).strip("-")
    return s.lower() or "model"


_THINK_MODES = ("off", "on", "auto")


def normalize_thinking(value: Any, default: str = "off") -> str:
    v = str(value or "").strip().lower()
    return v if v in _THINK_MODES else default


def normalize_model_entry(raw: dict[str, Any], used_ids: set[str]) -> dict[str, Any]:
    """归一化一条模型配置. 保证有 id / provider / base_url / model 字段.

    磁盘上存的是**真 key** (调用要用). data/ 整个目录已 gitignore.
    对外输出时才脱敏 (见 model_library).
    """
    raw = raw or {}
    name = (raw.get("name") or "").strip()
    mid = (raw.get("id") or "").strip() or _slug(name) or "model"
    # id 冲突时加序号
    base = mid
    n = 2
    while mid in used_ids:
        mid = f"{base}-{n}"
        n += 1
    used_ids.add(mid)
    provider = (raw.get("provider") or "openai").strip().lower()
    if provider not in ("openai", "anthropic"):
        provider = "openai"
    return {
        "id": mid,
        "name": name or mid,
        "provider": provider,
        "base_url": (raw.get("base_url") or "").strip().rstrip("/"),
        "api_key": (raw.get("api_key") or "").strip(),
        "model": (raw.get("model") or "").strip(),
        "note": (raw.get("note") or "").strip(),
        # 思考开关: off = 抽取时不要推理 (默认); on = 允许; auto = 同 off
        "thinking": normalize_thinking(raw.get("thinking")),
    }


def model_library(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """返回模型库 (脱敏 key, 带 has_key)."""
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


def resolve_api_key(incoming: str, previous: str) -> str:
    """决定保存时该写入哪个 key.

    incoming  = 前端回传的值
    previous  = 磁盘上已有的值

    规则:
        - 空串          → 不改 (前端没填)
        - "***"         → 不改 (前端明确说"沿用旧值")
        - 脱敏串 (含 *) → 不改 (前端拿的是展示用脱敏值, 不是真 key)
        - 其它          → 用新值

    is_masked 这一条是必须的: 前端拿到的永远是 mask_key() 的产物,
    它是 truthy, 所以前端的 "空 = 不改" 和服务端的 "== '***' = 不改"
    两条兜底都不会触发 —— 真 key 会被脱敏串静默覆盖, 之后所有请求 401.
    """
    val = (incoming or "").strip()
    if not val or val == "***" or is_masked(val):
        return previous
    return val


def apply_model_to_llm(cfg: dict[str, Any], model_id: str) -> bool:
    """把模型库里的某条套到 llm 段. 找不到返回 False.

    注意: llm 段的 timeout / max_tokens / temperature / max_concurrency
    保留用户设置 —— 模型库只管"连哪台机器 + 跑哪个模型 + 用哪把 key".
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
            # llm 段那个可能被污染的值去覆盖).
            if m.get("api_key"):
                cfg["llm"]["api_key"] = m["api_key"]
            cfg["active_model_id"] = mid
            return True
    return False


def enabled_categories(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """返回启用的分类列表."""
    cats = cfg.get("categories") or DEFAULT_CATEGORIES
    out = [c for c in cats if c.get("enabled", True)]
    return out or DEFAULT_CATEGORIES
