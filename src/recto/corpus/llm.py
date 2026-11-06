"""极简 LLM 客户端 (无第三方依赖, urllib).

支持:
    openai     — OpenAI 兼容 /chat/completions (MiniMax / agnes / 本地 vllm / ollama 都走这个)
    anthropic  — Anthropic /v1/messages

两家都返回统一的 str。
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class LLMError(RuntimeError):
    """LLM 调用失败."""


class LLMTruncated(LLMError):
    """输出被 max_tokens 截断 (推理模型尤其常见: token 全烧在 thinking 里)."""


class LLMReasoningUnavoidable(LLMError):
    """端点明确表示"这个模型必须思考" (关不掉).

    实测: MiniMax-M3.1-Flash 的 anthropic 端点对 thinking.type=disabled 直接 400
    ("requires adaptive thinking"). 这种模型在严格模式下不该继续 —— 抽取会
    把 token 烧在思考里, 而且 max_tokens 一不够就只回 thinking.
    """


class LLMQuotaExceeded(LLMError):
    """额度/配额用尽 —— 重试没有任何意义, 必须立刻整体停下.

    与限流 (429 但只是"太快了") 区分: 限流退避后能过, 额度没了要等窗口重置
    (MiniMax Token Plan 是 5 小时窗口 + 周窗口), 硬重试只会把 48 个块
    挨个跑成 429 —— 实测一次白烧 25 分钟, 还什么都没产出.
    """


# 429 响应体里出现这些字样 = 额度没了, 而不是"请求太快"
_QUOTA_MARKERS = (
    "用量上限",
    "token plan",
    "insufficient_quota",
    "exceeded your current quota",
    "额度已用尽",
    "余额不足",
    "欠费",
    "arrearage",
)

# 全局熔断: 一旦确认额度用尽, 后续所有请求立刻失败, 不再打网络
_QUOTA_TRIPPED = threading.Event()
_QUOTA_TRIPPED_AT: float = 0.0
_QUOTA_TRIPPED_MSG: str = ""

# 熔断有效期 (秒). 超过就自动放行一次, 让额度窗口重置后能自愈.
# 没有 TTL 时, 一次 17:30 的熔断会卡住 22:30 的连通性测试 —— 实测踩过.
QUOTA_TTL_SEC = float(os.environ.get("REFINER_QUOTA_TTL", "1800"))


def quota_state() -> dict[str, Any]:
    """熔断状态 (给 UI / 排障用)."""
    tripped = _QUOTA_TRIPPED.is_set()
    age = (time.time() - _QUOTA_TRIPPED_AT) if tripped else 0.0
    return {
        "tripped": tripped,
        "age_sec": int(age),
        "ttl_sec": int(QUOTA_TTL_SEC),
        "expired": tripped and QUOTA_TTL_SEC > 0 and age > QUOTA_TTL_SEC,
        "at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_QUOTA_TRIPPED_AT))
        if tripped
        else "",
        "message": _QUOTA_TRIPPED_MSG,
    }


def quota_tripped() -> bool:
    """熔断是否生效. 超过 TTL 视为已过期 (自动放行, 顺带清标记)."""
    if not _QUOTA_TRIPPED.is_set():
        return False
    if QUOTA_TTL_SEC > 0 and (time.time() - _QUOTA_TRIPPED_AT) > QUOTA_TTL_SEC:
        reset_quota()
        return False
    return True


def _trip_quota(msg: str) -> None:
    global _QUOTA_TRIPPED_AT, _QUOTA_TRIPPED_MSG
    _QUOTA_TRIPPED_AT = time.time()
    _QUOTA_TRIPPED_MSG = (msg or "")[:400]
    _QUOTA_TRIPPED.set()


def reset_quota() -> None:
    global _QUOTA_TRIPPED_AT, _QUOTA_TRIPPED_MSG
    _QUOTA_TRIPPED_AT = 0.0
    _QUOTA_TRIPPED_MSG = ""
    _QUOTA_TRIPPED.clear()


def _is_quota_error(msg: str) -> bool:
    """429 + 额度字样才算额度用尽; 单纯 429 仍按限流退避重试."""
    low = (msg or "").lower()
    if "429" not in low and "http 4" not in low:
        return False
    return any(m in low for m in _QUOTA_MARKERS)


# ───────────────────────── 推理模型 (thinking) 处理 ─────────────────────────
#
# 抽取任务不该用推理模型: 一次块抽取的产出只有几百 token 的 JSON, 而推理模型
# 会把 token 烧在 thinking 里 (实测 MiniMax-M3 单块 8000 字能烧掉 2-3 倍),
# 慢 3-5 倍, 且 max_tokens 不够时正文直接被截断 (只回 thinking).
#
# 三层处理:
#   1. 识别: looks_reasoning_model() — 名字命中已知推理家族
#   2. 关闭: 按协议/家族发 "不要思考" 参数 (端点不支持就退一步, 记住别再发)
#   3. 核实: 每次响应检查 reasoning_content / thinking 块 / reasoning_tokens,
#      真的出现过就标记出来 — 严格模式下整轮中止 (不烧 48 个块的冤枉钱)

# 名字里出现这些 = 推理家族 (保守: 只列已知会思考的)
_REASONING_NAME_MARKERS = (
    "thinking",
    "reasoner",
    "reasoning",
    "qwq",
    "deepseek-r1",
    "minimax-m1",
    "minimax-m2",
    "minimax-m3",
    "glm-z1",
    "glm-4.5",
    "glm-4.6",
    "kimi-k2-thinking",
    "magistral",
)
_REASONING_PREFIXES = ("o1", "o3", "o4", "gpt-5", "gpt5")


def looks_reasoning_model(model: str, base_url: str = "") -> bool:
    """模型名是否属于"会思考"的家族.

    只看名字, 不发请求 —— 判断错了也只是多/少发一个关闭参数, 不影响正确性.
    """
    m = (model or "").strip().lower()
    if not m:
        return False
    if any(mk in m for mk in _REASONING_NAME_MARKERS):
        return True
    # o1/o3/o4/gpt-5 系列: 用前缀判, 避免 "pro-1" 之类误命中
    return any(m.startswith(p) for p in _REASONING_PREFIXES)


def _thinking_off_payload(provider: str, model: str, base_url: str) -> dict:
    """各家的"别思考"参数形状. 猜错的代价 = 一次 400 + 自动重试, 可接受."""
    m = (model or "").lower()
    b = (base_url or "").lower()
    if provider == "anthropic":
        # Anthropic 官方 + MiniMax 的 anthropic 兼容层都认这个
        return {"thinking": {"type": "disabled"}}
    if m.startswith(("o1", "o3", "o4")):
        # 老 o 系列没有 minimal, 只能压到 low
        return {"reasoning_effort": "low"}
    if "openai.com" in b:
        # 官方 OpenAI: gpt-5 能降到 minimal; 非推理型号发 reasoning_effort 会 400
        if m.startswith("gpt-5"):
            return {"reasoning_effort": "minimal"}
        if m.startswith(("o1", "o3", "o4")):
            return {"reasoning_effort": "low"}
        return {}
    if "dashscope" in b or "aliyun" in b or "qwen" in m:
        return {"enable_thinking": False}
    if "bigmodel" in b or "zhipu" in b or m.startswith("glm"):
        return {"thinking": {"type": "disabled"}}
    if "minimax" in b or m.startswith("minimax"):
        # openai 兼容端点: 实测 reasoning_effort=none 被拒 ("requires adaptive
        # thinking; reasoning effort none is not allowed"), minimal 可用且
        # reasoning_tokens ≈ 0 → 交给 _think_off_ladder 自动降级, 别裸发
        return {"reasoning_effort": "none"}
    if "vllm" in b or "127.0.0.1" in b or "localhost" in b:
        return {"chat_template_kwargs": {"enable_thinking": False}}
    # 未知的 OpenAI 兼容端点: reasoning_effort=none 实测对 agnes 有效
    # (会把 reasoning_tokens 压到 0; enable_thinking=false 反而被忽略).
    # 端点不认就吃一个 400, 由 _post_with_think_off 去掉参数重试并记住.
    return {"reasoning_effort": "none"}


# 这些字样 = "这个参数我不认识", 才值得去掉参数重试
_PARAM_UNSUPPORTED_MARKERS = (
    "unrecognized",
    "unknown parameter",
    "unknown field",
    "extra fields",
    "extra_forbidden",
    "unexpected keyword",
    "not supported",
    "unsupported parameter",
    "invalid parameter",
    "invalid params",
    "unsupported_value",
)

# 错误里出现这些 = "问题就出在思考参数上" (而不是别的地方)
_THINKING_REJECT_MARKERS = ("thinking", "reasoning", "adaptive", "budget_tokens")

# base_url|model → 该端点不吃关闭参数 (查到一次就记住, 后续不再发)
_THINK_OFF_UNSUPPORTED: set[str] = set()
# base_url|model → 已试出"能压住思考"的参数 (例如 reasoning_effort=minimal)
_THINK_OFF_LEVEL: dict[str, dict] = {}
# base_url|model → 所有关闭参数都被拒 = 这个模型强制思考 (别再裸发请求)
_THINKING_REQUIRED: set[str] = set()
_THINK_OFF_LOCK = threading.Lock()


def _thinking_required_err(msg: str) -> bool:
    """这条 400 是"模型必须思考"吗.

    必须先于 _param_unsupported 判断: 官方文案长这样
        invalid params, model "MiniMax-M3.1-Flash" requires adaptive thinking;
        thinking.type="disabled" ... is not allowed (2013)
    它同时命中 "invalid params" —— 旧代码就把它当成"参数名不认识", 去掉参数
    裸发出去, 结果模型开始自适应思考, 一个块挂十几分钟 (2026-10-30 事故).
    """
    low = (msg or "").lower()
    if "requires adaptive thinking" in low:
        return True
    if "thinking" in low and "not allowed" in low:
        return True
    if "requires" in low and ("thinking" in low or "reasoning" in low):
        return True
    return False


def _think_off_ladder(provider: str, think_off: dict) -> list[dict]:
    """被拒之后的降级档位 (从"关得最彻底"到"只能压低").

    只对 openai 兼容端点有意义: anthropic 协议没有"少想一点"这个旋钮,
    关不掉就是关不掉 —— 直接判死, 绝不裸发.
    """
    if provider == "anthropic":
        return []
    out: list[dict] = []
    cur = json.dumps(think_off, sort_keys=True)
    for cand in ({"reasoning_effort": "minimal"}, {"reasoning_effort": "low"}):
        if json.dumps(cand, sort_keys=True) != cur:
            out.append(cand)
    return out


def thinking_required(base_url: str, model: str) -> bool:
    with _THINK_OFF_LOCK:
        return f"{base_url}|{model}" in _THINKING_REQUIRED


def reset_think_off_cache() -> None:
    """测试/排查用: 清掉"这个端点关不掉思考"的记忆."""
    with _THINK_OFF_LOCK:
        _THINK_OFF_UNSUPPORTED.clear()
        _THINK_OFF_LEVEL.clear()
        _THINKING_REQUIRED.clear()


# 运行期统计 (每轮抽取前 reset_stats())
_STATS_LOCK = threading.Lock()
_STATS: dict[str, int] = {}


def reset_stats() -> None:
    with _STATS_LOCK:
        _STATS.clear()


def _bump(key: str, n: int = 1) -> None:
    with _STATS_LOCK:
        _STATS[key] = _STATS.get(key, 0) + n


def stats() -> dict[str, int]:
    with _STATS_LOCK:
        return dict(_STATS)


def _param_unsupported(msg: str) -> bool:
    low = (msg or "").lower()
    return any(mk in low for mk in _PARAM_UNSUPPORTED_MARKERS)


def _strip_think(text: str) -> str:
    """剥掉推理模型的 <think>...</think> 块."""
    import re
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _split_think(text: str) -> tuple[str, str]:
    """拆成 (thinking, 正文). 正文可能为空 (全是 think)."""
    import re
    m = re.search(r"<think>(.*?)</think>", text, flags=re.DOTALL)
    if not m:
        return "", text.strip()
    return m.group(1).strip(), text[m.end():].strip()


# ---- 全局节流: 免费额度常按 QPS/RPM 限, 并发一多就 429 ----
_THROTTLE_LOCK = threading.Lock()
_LAST_CALL = [0.0]
_MIN_INTERVAL = float(os.environ.get("EPUB_REFINER_MIN_INTERVAL", "1.2"))


def _throttle() -> None:
    """保证两次请求发起间隔 >= _MIN_INTERVAL 秒.

    注意: 等待必须在锁**外**, 否则持锁 sleep 会把并发完全串行化.
    """
    if _MIN_INTERVAL <= 0:
        return
    with _THROTTLE_LOCK:
        now = time.monotonic()
        earliest = _LAST_CALL[0] + _MIN_INTERVAL
        _LAST_CALL[0] = max(now, earliest)
        wait = earliest - now
    if wait > 0:
        time.sleep(wait)


def _post_json(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    """POST 一个 JSON 请求.

    用 http.client 而不是 urllib: 需要拿到 socket, 好在"硬墙钟"到点时主动掐断.
    原因 —— urllib 的 timeout 是**单次 read 的超时**, 服务端只要偶尔吐一点字节
    (或者 TLS 层有心跳), read 就永远不超时, 一个块能挂十几分钟且不留任何错误.
    实测 2026-10-30: 23:18 起的抽取挂到 23:30 仍无进展、无任何报错.
    """
    _throttle()
    data = json.dumps(payload).encode("utf-8")
    limit = float(timeout or 180)
    parts = urllib.parse.urlsplit(url)
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    hdrs = {k: str(v) for k, v in (headers or {}).items()}
    hdrs.setdefault("Content-Type", "application/json")
    hdrs["Content-Length"] = str(len(data))
    hdrs["Connection"] = "close"
    sock_timeout = min(limit, 60.0)
    if parts.scheme == "https":
        conn: http.client.HTTPConnection = http.client.HTTPSConnection(
            parts.hostname, parts.port or 443, timeout=sock_timeout)
    else:
        conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=sock_timeout)

    aborted = {"v": False}

    def _abort() -> None:
        aborted["v"] = True
        try:
            sock = getattr(conn, "sock", None)
            if sock is not None:
                sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass

    timer = threading.Timer(limit, _abort)
    timer.daemon = True
    timer.start()
    code = 0
    body = ""
    try:
        conn.request("POST", path, body=data, headers=hdrs)
        resp = conn.getresponse()
        code = resp.status
        buf: list[bytes] = []
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            buf.append(chunk)
        body = b"".join(buf).decode("utf-8", errors="replace")
    except Exception as e:
        if aborted["v"]:
            raise LLMError(
                f"请求超时 (硬墙钟 {limit:.0f}s, 不限单次 read) {url}"
            ) from e
        raise LLMError(f"请求失败 {url}: {type(e).__name__}: {e}") from e
    finally:
        timer.cancel()
        try:
            conn.close()
        except Exception:
            pass
    # 计时器掐断 socket 时, read 有时返回的是"提前 EOF"而不是异常 — 这里补一刀,
    # 否则半截响应会被当成正常 body 去解析
    if aborted["v"]:
        raise LLMError(f"请求超时 (硬墙钟 {limit:.0f}s, 不限单次 read) {url}")
    if code >= 400:
        raise LLMError(f"HTTP {code} {url}\n{body[:2000]}")
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise LLMError(f"响应不是 JSON: {body[:800]}") from e


def chat(
    llm_cfg: dict[str, Any],
    system: str,
    user: str,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    retries: int = 4,
    probe: bool = False,
    meta: dict[str, Any] | None = None,
) -> str:
    """发一次对话, 返回文本内容 (已剥 think 块).

    Args:
        probe: 连通性测试用. **不受熔断影响**: 已经熔断也照发 (人不该被上次运行的
            标记挡住), 但失败时仍会记熔断, 好让同进程里跑着的抽取任务一起停.
        meta: 出参. 填 {"reasoning": bool, "reasoning_tokens": int,
            "think_off": str} — 判断"这次到底有没有在思考"用.

    Raises:
        LLMTruncated: 输出被 max_tokens 截断.
    """
    provider = (llm_cfg.get("provider") or "openai").lower()
    base_url = (llm_cfg.get("base_url") or "").rstrip("/")
    api_key = llm_cfg.get("api_key") or ""
    model = llm_cfg.get("model") or ""
    timeout = float(llm_cfg.get("timeout") or 180)
    temp = float(llm_cfg.get("temperature", 0.2) if temperature is None else temperature)
    mt = int(llm_cfg.get("max_tokens") or 8192 if max_tokens is None else max_tokens)

    if not base_url or not model:
        raise LLMError("LLM 配置不完整: 需要 base_url 与 model")

    # 推理关闭参数: 只在"这个模型会思考"且用户没要求开启时才发.
    # 普通对话模型一个额外字段都不加 — 少一个 400 的机会.
    think_mode = str(llm_cfg.get("thinking") or "off").lower()
    # 底层默认 False: 策略由调用方 (web/cli 从 config 读) 决定, 不在这里假设
    strict_reasoning = bool(llm_cfg.get("strict_no_reasoning", False))
    think_off: dict = {}
    think_off_state = "not_needed"
    if think_mode != "on":
        # 不只对"名字像推理"的模型发: agnes-2.5-flash 名字不像, 实测每轮也
        # 烧 19 个 reasoning_tokens; 只有 reasoning_effort=none 能压到 0.
        key = f"{base_url}|{model}"
        with _THINK_OFF_LOCK:
            blocked = key in _THINK_OFF_UNSUPPORTED
            remembered = dict(_THINK_OFF_LEVEL.get(key) or {})
            forced = key in _THINKING_REQUIRED
        if forced:
            raise LLMReasoningUnavoidable(
                f"{model} 要求自适应思考, 该端点不允许关闭 (已实测 {base_url}). "
                f"抽取请换非推理模型; 确实要用它, 就在「模型库」把该模型的「思考」设为开启"
                f" (但会慢很多、token 成倍)."
            )
        if remembered:
            think_off = remembered
            think_off_state = "downgraded"
        elif blocked:
            think_off_state = "unsupported"
        else:
            think_off = _thinking_off_payload(provider, model, base_url)
            think_off_state = "sent" if think_off else "not_needed"
    if meta is not None:
        meta.setdefault("reasoning", False)
        meta.setdefault("reasoning_tokens", 0)
        meta["think_off"] = think_off_state

    if not probe and quota_tripped():
        st = quota_state()
        raise LLMQuotaExceeded(
            "LLM 额度已用尽, 本次运行已中止 (不再发请求; 等额度窗口重置后重跑)\n"
            f"熔断记录: {st['at']} 起, 已 {st['age_sec']}s (TTL {st['ttl_sec']}s)\n"
            f"原始报错: {st['message'][:300]}"
        )

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            if provider == "anthropic":
                out = _anthropic(base_url, api_key, model, system, user, temp, mt, timeout,
                                 think_off=think_off, meta=meta, strict=strict_reasoning)
            else:
                out = _openai(base_url, api_key, model, system, user, temp, mt, timeout,
                              think_off=think_off, meta=meta, strict=strict_reasoning)
            _bump("calls")
            if meta and meta.get("reasoning"):
                _bump("reasoning_calls")
                _bump("reasoning_tokens", int(meta.get("reasoning_tokens") or 0))
            if meta:
                meta["think_off"] = think_off_state
            return out
        except LLMTruncated:
            # 截断 = token 不够. 推理模型会把 token 烧在 thinking 里, 加倍重试.
            last_err = LLMTruncated(
                f"输出被截断 (max_tokens={mt}); 推理模型的 token 会烧在 thinking 里, "
                f"需要更大的 max_tokens"
            )
            if attempt < retries:
                mt *= 2
                time.sleep(1.5 * (attempt + 1))
                continue
            raise last_err
        except LLMError as e:
            last_err = e
            msg = str(e)
            # 已经是"额度用尽"的显式异常 (可能是外层熔断抛的) → 直接透传, 不重试
            if isinstance(e, LLMQuotaExceeded):
                _trip_quota(msg)
                raise
            # "这个模型关不掉思考" 是结构性事实, 重试没有意义
            if isinstance(e, LLMReasoningUnavoidable):
                raise
            # 额度用尽 (429 + "用量上限"/quota): 重试毫无意义, 直接熔断
            if _is_quota_error(msg):
                _trip_quota(msg)
                raise LLMQuotaExceeded(msg) from e
            # 4xx (除 429) 不重试
            if "HTTP 4" in msg and "429" not in msg:
                raise
            # 硬墙钟超时: 端点要么很慢要么挂死, 只再给一次机会 (180s x 5 次
            # = 15 分钟白等, 之前就是这么把整轮拖死的)
            if "请求超时" in msg and attempt >= 1:
                raise
            if attempt < retries:
                # 429 限流退避更久 (免费额度常见)
                wait = 3.0 * (2 ** attempt)
                if "429" in msg:
                    wait = max(wait, 10.0 * (attempt + 1))
                time.sleep(wait)
    raise LLMError(str(last_err))


def _post_with_think_off(url: str, payload: dict, headers: dict, timeout: float,
                         think_off: dict, base_url: str, model: str,
                         strict: bool = False, provider: str = "openai") -> dict:
    """发请求; 若"关闭思考"的参数不被接受, 按档位降级或直接判死.

    三层走向, 顺序不能换:
      1. 端点说"这个模型必须思考" -> 换更弱但合法的档位 (openai 兼容才有);
         没有档位可换 -> 记住 _THINKING_REQUIRED 并抛 LLMReasoningUnavoidable.
         **绝不裸发请求**: 裸发 = 模型开始自适应思考, 一个块挂十几分钟.
      2. 端点只是"不认识这个参数名" -> 去掉参数重发, 记住别再加.
      3. 其它错误 -> 原样抛出 (交给 chat() 的重试逻辑).
    """
    if not think_off:
        return _post_json(url, payload, headers, timeout)
    key = f"{base_url}|{model}"
    ladder = _think_off_ladder(provider, think_off)
    cands = [think_off] + ladder
    last: Exception | None = None
    for i, cand in enumerate(cands):
        body = dict(payload)
        body.update(cand)
        try:
            out = _post_json(url, body, headers, timeout)
        except LLMError as e:
            msg = str(e)
            last = e
            if _thinking_required_err(msg):
                _bump("think_off_rejected")
                if i + 1 < len(cands):
                    continue
                with _THINK_OFF_LOCK:
                    _THINKING_REQUIRED.add(key)
                    _THINK_OFF_UNSUPPORTED.discard(key)
                    _THINK_OFF_LEVEL.pop(key, None)
                raise LLMReasoningUnavoidable(
                    f"端点拒绝关闭思考, 该模型无法在抽取中禁用推理 "
                    f"(已试 {len(cands)} 档): {msg[:300]}"
                ) from e
            if not _param_unsupported(msg):
                raise
            # 纯"参数名不认识": 去掉参数重发 (此时模型本身不需要思考, 是安全的)
            with _THINK_OFF_LOCK:
                _THINK_OFF_UNSUPPORTED.add(key)
            _bump("think_off_rejected")
            return _post_json(url, payload, headers, timeout)
        else:
            if cand != think_off:
                with _THINK_OFF_LOCK:
                    _THINK_OFF_LEVEL[key] = cand
                    _THINK_OFF_UNSUPPORTED.discard(key)
                _bump("think_off_degraded")
            return out
    raise last if last is not None else LLMError("请求失败")


def _openai(base_url, api_key, model, system, user, temp, mt, timeout,
            think_off: dict | None = None, meta: dict | None = None,
            strict: bool = False) -> str:
    url = base_url + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temp,
        "max_tokens": mt,
    }
    data = _post_with_think_off(url, payload, headers, timeout,
                                think_off or {}, base_url, model, strict=strict, provider='openai')
    choices = data.get("choices")
    if not choices:
        raise LLMError(f"响应无 choices: {json.dumps(data, ensure_ascii=False)[:600]}")
    ch = choices[0]
    msg = ch.get("message") or {}
    content = msg.get("content")
    if content is None:
        content = ch.get("text") or ""
    raw = str(content or "")
    reasoning = str(msg.get("reasoning_content") or msg.get("reasoning") or "")
    finish = ch.get("finish_reason")
    usage = data.get("usage") or {}
    rt = int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)

    # 剥 think 块 (正文里嵌的 <think> 也算"它思考过了")
    body = raw
    if "<think>" in raw or "<thinking>" in raw:
        import re as _re
        body = _re.sub(r"<think(?:ing)?>.*?</think(?:ing)?>", "", raw, flags=_re.DOTALL)
        reasoning = reasoning or "<think>"
    body = body.strip()

    if meta is not None:
        meta["reasoning"] = bool(reasoning.strip()) or rt > 0
        meta["reasoning_tokens"] = rt

    if finish == "length" or (not body and (reasoning or rt)):
        raise LLMTruncated(
            f"输出被截断 (finish_reason={finish}, max_tokens={mt}, "
            f"reasoning_tokens={rt}); 推理模型的 token 会烧在 thinking 里"
        )
    if not body:
        raise LLMError(
            "模型只返回了推理内容 (reasoning_content), 没有正文. "
            "请把该模型的「思考」设为关闭, 或改用非推理模型"
        )
    return body


def _anthropic(base_url, api_key, model, system, user, temp, mt, timeout,
               think_off: dict | None = None, meta: dict | None = None,
               strict: bool = False) -> str:
    url = base_url + "/messages"
    headers = {
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    if api_key:
        headers["x-api-key"] = api_key
    payload = {
        "model": model,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "temperature": temp,
        "max_tokens": mt,
    }
    data = _post_with_think_off(url, payload, headers, timeout,
                                think_off or {}, base_url, model, strict=strict, provider='anthropic')
    blocks = data.get("content") or []
    parts: list[str] = []
    saw_thinking = False
    for b in blocks:
        if not isinstance(b, dict):
            continue
        bt = b.get("type")
        if bt in ("thinking", "redacted_thinking"):
            saw_thinking = True
            continue
        if bt in ("text", "output_text"):
            parts.append(b.get("text") or "")
    usage = data.get("usage") or {}
    rt = int((usage.get("output_tokens_details") or {}).get("thinking_tokens") or 0)
    text = "\n".join(parts)
    # 兼容端点可能把思考塞进正文
    if "<think" in text:
        import re as _re
        stripped = _re.sub(r"<think(?:ing)?>.*?</think(?:ing)?>", "", text, flags=_re.DOTALL)
        if stripped != text:
            saw_thinking = True
            text = stripped
    if meta is not None:
        meta["reasoning"] = saw_thinking or rt > 0
        meta["reasoning_tokens"] = rt
    if not text.strip():
        if saw_thinking or rt:
            raise LLMTruncated(
                f"只有思考内容没有正文 (max_tokens={mt}, thinking_tokens={rt}); "
                f"请关闭该模型的思考, 或调大 max_tokens"
            )
        raise LLMError(f"响应无文本: {json.dumps(data, ensure_ascii=False)[:600]}")
    return text.strip()


def test_llm(llm_cfg: dict[str, Any]) -> dict[str, Any]:
    """连通性测试. 返回 {ok, message, latency_ms, sample}.

    **永远 probe=True**: 连通性测试是人的显式动作, 不能被上次运行留下的熔断标记挡住.
    """
    t0 = time.time()
    try:
        out = chat(
            llm_cfg,
            "You are a helpful assistant. Reply with exactly: OK",
            "ping",
            temperature=0.0,
            max_tokens=32,
            retries=1,
            probe=True,
        )
        return {
            "ok": True,
            "message": "连通正常",
            "latency_ms": int((time.time() - t0) * 1000),
            "sample": (out or "")[:200],
            "quota": quota_state(),
        }
    except Exception as e:
        return {
            "ok": False,
            "message": str(e)[:600],
            "latency_ms": int((time.time() - t0) * 1000),
            "sample": "",
            "quota": quota_state(),
        }
