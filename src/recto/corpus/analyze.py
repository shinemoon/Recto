"""关键字分析: 分块送 LLM → 抽取 → 合并去重 → 输出 JSON.

输出 JSON 结构 (可直接给预渲染用):
    {
      "meta": {
        "source_epub": "...", "title": "...", "author": "...",
        "generated_at": "ISO8601", "model": "...", "chunks": 12,
        "total_chars": 123456, "category_schema": [{id,name,hint}]
      },
      "categories": {
        "person": {"name": "人名", "items": [
            {"term": "Adrian Mole", "aliases": ["Mr. Mole", "阿德里安"],
             "count": 42, "first_seen": 0, "notes": ""}
        ]},
        ...
      },
      "index": {   # 扁平列表, 方便预渲染器直接遍历
        "person": ["Adrian Mole", ...],
        ...
      }
    }
"""
from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import llm

SYSTEM_PROMPT = """You are a literary analyst extracting a controlled keyword vocabulary from a book.

You will be given a block of text from a novel. Identify the KEY TERMS that a downstream
pre-renderer must know about — proper nouns and recurring concrete referents.

Rules:
1. Only extract terms that are REPEATED or STRUCTURALLY IMPORTANT (main characters, recurring
   places, named objects, named events, dates). Skip one-off generic words.
2. Use the term's canonical form as it appears in the text (English original spelling).
3. Add aliases: other surface forms used for the same referent (Mr. Mole, Mole, 阿德里安).
4. count = how many times the term appears in THIS block (integer, your best estimate).
5. category must be exactly one id from the provided category id list.
6. If the text is in Chinese, still output the term as written in the text.
7. Output ONLY a JSON array. No prose, no markdown fence.

Output format:
[{"term":"...","aliases":["..."],"category":"<category_id>","count":3,"notes":""}]

Available category ids:
<<CATEGORY_LIST>>"""


def _build_user_prompt(block: dict, categories: list[dict], target_lang: str) -> str:
    cat_lines = "\n".join(
        f"- {c['id']}: {c['name']} — {c.get('hint','')}" for c in categories
    )
    return (
        f"Category id list:\n{cat_lines}\n\n"
        f"Target language for downstream rendering: {target_lang}\n\n"
        f"--- BEGIN TEXT (chapter: {block['chapter_title']}) ---\n"
        f"{block['text']}\n"
        f"--- END TEXT ---\n\n"
        f"Extract keywords. Output only the JSON array."
    )


def _parse_json_array(text: str) -> list[dict[str, Any]]:
    """从 LLM 输出里抠出 JSON 数组 (容错截断/围栏)."""
    if not text:
        return []
    t = text.strip()
    # 去 markdown 围栏
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    # 找第一个 [ 和最后一个 ]
    i = t.find("[")
    if i < 0:
        return []
    j = t.rfind("]")
    if j < 0:
        # 截断了 — 尝试补齐
        frag = t[i:]
        # 逐条对象救
        objs = []
        for m in re.finditer(r"\{[^{}]*\}", frag, re.S):
            try:
                objs.append(json.loads(m.group(0)))
            except json.JSONDecodeError:
                continue
        return objs
    frag = t[i:j + 1]
    try:
        data = json.loads(frag)
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
        return []
    except json.JSONDecodeError:
        pass
    # 退化: 逐个对象抠
    objs = []
    for m in re.finditer(r"\{[^{}]*\}", frag, re.S):
        try:
            objs.append(json.loads(m.group(0)))
        except json.JSONDecodeError:
            continue
    return objs


def _norm_key(s: str) -> str:
    """归一化用于去重的 key."""
    return re.sub(r"\s+", " ", (s or "").strip().lower())


class ReasoningAborted(RuntimeError):
    """模型在输出推理内容 (thinking) -> 严格模式下整轮中止.

    跟 QuotaAborted 一样"本轮结果不可用", 但原因不同: 是模型选错了.
    调用方要保住旧产物, 并提示用户把模型的「思考」关掉或换非推理模型.
    """


class QuotaAborted(RuntimeError):
    """LLM 额度用尽 → 整体中止.

    刻意跟"部分块失败"区分开: 这种情况下本轮结果一定残缺, 调用方**不要**
    用它覆盖上一份完整的 keywords.json.
    """


SEP = chr(1)


def block_fingerprint(block: dict, categories: list[dict]) -> str:
    """块的指纹: 正文 + 章节名 + 启用分类.

    用于断点续跑 —— 变了就说明这一块的输入变了, 缓存结果不能复用.
    **刻意不含模型名**: 换模型继续跑是允许的 (额度用完切备用端点是常态).
    """
    import hashlib

    cat_ids = ",".join(c["id"] for c in categories)
    raw = SEP.join([
        str(block.get("chapter_title") or ""),
        str(block.get("text") or ""),
        cat_ids,
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


# 失败块超过这个比例就认为"本轮结果不可用"
DEGRADED_ERROR_RATIO = 1.0 / 3.0

# 干净跑 (零失败块) 也要保住已有产物的 40%, 否则疑似"解析中途丢了内容"
DEGRADED_TERM_RATIO = 0.4


def degraded_reason(
    errors: list[dict[str, Any]] | None,
    total: int,
    new_terms: int,
    old_terms: int = 0,
) -> str:
    """判断本轮结果是否**不如**已有产物. 返回空串 = 可以覆盖.

    起因: 额度用尽时 48 个块全挂, 旧版仍会写出一个近乎空的 keywords.json,
    把上一份 877 词条的好结果覆盖掉 (2026-10-30 实测差点发生).

    注意 40% 这个门槛不能定太高: 换个模型重跑, 抽取宽容度不同, 词条数
    上下浮动 30-50% 是正常的 (实测 tiny-test 27 → 13). 定 60% 会把
    一次完全成功的干净跑挡在门外.
    """
    n = len(errors or [])
    if new_terms <= 0:
        return "本轮零词条"
    if total and n / total > DEGRADED_ERROR_RATIO:
        return f"失败块 {n}/{total} 超过 1/3"
    if old_terms and new_terms < old_terms * DEGRADED_TERM_RATIO:
        return (
            f"词条 {new_terms} 不足已有 {old_terms} 的 "
            f"{int(DEGRADED_TERM_RATIO * 100)}%"
        )
    return ""


def analyze_chunk(
    llm_cfg: dict[str, Any],
    block: dict,
    categories: list[dict],
    target_lang: str = "Chinese",
    meta: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """对一个块跑 LLM, 返回原始条目列表."""
    system = SYSTEM_PROMPT.replace(
        "<<CATEGORY_LIST>>", ", ".join(c["id"] for c in categories)
    )
    user = _build_user_prompt(block, categories, target_lang)
    # retries 收在 2: 端点挂死时 4 次重试 x 180s 能把一个块拖成 15 分钟,
    # 整套 25 块就再也不会结束 (2026-10-30 事故). 单块最多让 3 次尝试.
    out = llm.chat(llm_cfg, system, user, meta=meta, retries=2)
    items = _parse_json_array(out)
    valid_ids = {c["id"] for c in categories}
    res = []
    for it in items:
        term = (it.get("term") or it.get("source") or "").strip()
        if not term:
            continue
        cat = (it.get("category") or "").strip()
        if cat not in valid_ids:
            # 兜底别用 sorted(valid)[0] —— 那按字母序落到 "event",
            # 语义上应该是"通用术语". 静默把词条塞进"事件"更糟.
            cat = "term" if "term" in valid_ids else (sorted(valid_ids)[0] if valid_ids else "term")
        aliases = it.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        aliases = [a.strip() for a in aliases if isinstance(a, str) and a.strip()]
        try:
            count = int(it.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        res.append({
            "term": term,
            "aliases": aliases,
            "category": cat,
            "count": max(0, count),
            "notes": (it.get("notes") or "").strip(),
        })
    return res


def merge_items(
    per_chunk: list[list[dict[str, Any]]],
    categories: list[dict],
) -> dict[str, list[dict[str, Any]]]:
    """合并多块结果: 同 term 合并 count, 并集 aliases, 类别投票 (多数优先)."""
    merged: dict[str, dict[str, Any]] = {}
    cat_votes: dict[str, dict[str, int]] = {}
    for items in per_chunk:
        for it in items:
            key = _norm_key(it["term"])
            if not key:
                continue
            rec = merged.get(key)
            if rec is None:
                rec = {
                    "term": it["term"],
                    "aliases": [],
                    "count": 0,
                    "notes": "",
                    "blocks": [],
                }
                merged[key] = rec
                cat_votes[key] = {}
            rec["count"] += it["count"]
            rec["blocks"].append(it.get("_block"))
            for a in it["aliases"]:
                if a and a not in rec["aliases"] and _norm_key(a) != key:
                    rec["aliases"].append(a)
            if it.get("notes") and not rec["notes"]:
                rec["notes"] = it["notes"]
            cat_votes[key][it["category"]] = cat_votes[key].get(it["category"], 0) + 1

    out: dict[str, list[dict[str, Any]]] = {c["id"]: [] for c in categories}
    for key, rec in merged.items():
        votes = cat_votes.get(key) or {}
        cat = max(votes.items(), key=lambda kv: (kv[1], -list(out.keys()).index(kv[0]) if kv[0] in out else 0))[0] if votes else categories[0]["id"]
        if cat not in out:
            cat = next(iter(out))
        rec.pop("blocks", None)
        rec["category"] = cat
        out[cat].append(rec)

    for cat in out:
        out[cat].sort(key=lambda r: (-r["count"], r["term"].lower()))
    return out


def analyze_epub(
    epub_path: str | Path,
    llm_cfg: dict[str, Any],
    categories: list[dict],
    blocks: list[dict],
    *,
    target_lang: str = "Chinese",
    max_concurrency: int = 2,
    progress_cb=None,
    on_error=None,
    errors_out: list[dict[str, Any]] | None = None,
    cache: dict[str, dict[str, Any]] | None = None,
    on_chunk_done=None,
    strict_no_reasoning: bool = True,
    stats_out: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """跑完整分析. 返回合并后的 categories 结构.

    单块失败不中断整体 —— 记录到 errors_out 里. 全挂则抛异常.

    Args:
        cache: 断点续跑缓存 {str(idx): {"fp": 指纹, "items": [...]}}. 指纹一致就跳过.
        on_chunk_done: `(idx, fp, items)` — 每块成功立刻回调, 用于落盘断点.
            必须在块结束**当时**写盘, 否则进程一挂整轮白跑 (这是原来最大的坑).
        strict_no_reasoning: 响应里真出现 thinking 就整轮中止.
        stats_out: 出参, 填 {"resumed": n, "ran": n, "reasoning_blocks": n}.
    """
    total = len(blocks)
    done = [0]
    resumed = [0]
    reasoning_blocks: list[int] = []
    lock = threading.Lock()
    results: list[list[dict[str, Any]]] = [[] for _ in range(total)]
    errors: list[dict[str, Any]] = errors_out if errors_out is not None else []
    quota_reported = [False]
    abort_evt = threading.Event()
    cache = cache or {}

    # 预计算指纹 (一次遍历, 别在并发里各算一遍)
    fps = [block_fingerprint(b, categories) for b in blocks]

    def _err(idx: int, e: Exception) -> None:
        with lock:
            errors.append({
                "block": idx,
                "chapter": blocks[idx].get("chapter_title", ""),
                "chars": blocks[idx].get("chars", 0),
                "error": str(e)[:500],
            })

    def _finish(idx: int, items: list[dict[str, Any]]) -> None:
        for it in items:
            it["_block"] = idx
        results[idx] = items
        with lock:
            done[0] += 1
            if progress_cb:
                progress_cb(done[0], total, idx)

    def _run_remote(idx: int) -> None:
        meta: dict[str, Any] = {}
        try:
            if llm.quota_tripped():
                # 熔断后别再打网络: 剩余块立刻失败, 不再逐块等退避
                raise llm.LLMQuotaExceeded("LLM 额度已用尽, 跳过剩余块")
            items = analyze_chunk(llm_cfg, blocks[idx], categories, target_lang, meta=meta)
        except llm.LLMQuotaExceeded as e:
            _err(idx, e)
            with lock:
                first = not quota_reported[0]
                quota_reported[0] = True
            # 熔断只报一次, 否则 40 多个块各刷一行同样的错误
            if on_error and first:
                on_error(idx, total, e)
            items = []
        except llm.LLMReasoningUnavoidable as e:
            # 端点明说"这个模型关不掉思考" -> 立刻停, 不必等它跑完再发现
            _err(idx, e)
            with lock:
                reasoning_blocks.append(idx)
            abort_evt.set()
            if on_error:
                on_error(idx, total, e)
            items = []
            _finish(idx, items)
            return
        except Exception as e:
            _err(idx, e)
            if on_error:
                on_error(idx, total, e)
            items = []
        else:
            # 只在**成功**时判断推理: 失败块的 meta 不代表模型行为
            if meta.get("reasoning"):
                with lock:
                    reasoning_blocks.append(idx)
                if strict_no_reasoning:
                    abort_evt.set()
                    _err(idx, RuntimeError("模型输出了推理内容 (thinking) - 严格模式中止"))
        _finish(idx, items)
        if items and on_chunk_done:
            try:
                on_chunk_done(idx, fps[idx], items)
            except Exception as e:  # 落盘失败不该毁掉整轮
                if on_error:
                    on_error(idx, total, e)

    def run_one(idx: int) -> None:
        hit = cache.get(str(idx))
        if hit and hit.get("fp") == fps[idx] and isinstance(hit.get("items"), list):
            with lock:
                resumed[0] += 1
            _finish(idx, hit["items"])
            return
        if abort_evt.is_set():
            _err(idx, RuntimeError("上一块触发了中止, 本块跳过"))
            return
        _run_remote(idx)

    workers = max(1, min(int(max_concurrency or 1), 8, total))
    if total:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(run_one, range(total)))

    if stats_out is not None:
        stats_out.update({
            "resumed": resumed[0],
            "ran": total - resumed[0],
            "reasoning_blocks": len(reasoning_blocks),
            "llm": llm.stats(),
        })

    if llm.quota_tripped():
        # 额度用尽: 结果必然残缺, 抛专门异常让调用方保住旧产物
        raise QuotaAborted(
            f"LLM 额度用尽, 本轮 {len(errors)}/{total} 块失败, 已中止 "
            f"(已完成的块已存断点, 额度重置后点「继续」只补剩下的)"
        )

    if reasoning_blocks and strict_no_reasoning:
        detail = ""
        for e in errors:
            t = str(e.get("error") or "")
            if "思考" in t or "thinking" in t.lower():
                detail = " 端点原话: " + t[:200]
                break
        raise ReasoningAborted(
            f"模型输出了推理内容 (thinking), 涉及块 {reasoning_blocks[:5]}"
            f"{' 等' if len(reasoning_blocks) > 5 else ''}.{detail} "
            f"抽取不该用推理模型: 请在「模型库」把该模型的「思考」设为关闭; "
            f"若该端点忽略该参数, 请换非推理模型 (如 agnes-2.5-flash). "
            f"确认要容忍推理, 可在「LLM 配置」里关掉「严格避免推理」."
        )

    if total and len(errors) >= total:
        raise RuntimeError(
            f"全部 {total} 块都失败. 第一个错误: {errors[0]['error'] if errors else '?'}"
        )
    return merge_items(results, categories)


def build_output(
    epub_info: dict[str, Any],
    llm_cfg: dict[str, Any],
    categories: list[dict],
    merged: dict[str, list[dict[str, Any]]],
    *,
    n_chunks: int,
    target_lang: str = "Chinese",
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """组装最终 JSON."""
    cat_meta = []
    for c in categories:
        cat_meta.append({
            "id": c["id"],
            "name": c["name"],
            "hint": c.get("hint", ""),
            "count": len(merged.get(c["id"], [])),
        })
    index = {c["id"]: [r["term"] for r in merged.get(c["id"], [])] for c in categories}
    total_terms = sum(len(v) for v in merged.values())
    out = {
        "meta": {
            "source_epub": str(epub_info.get("path", "")),
            "title": epub_info.get("title", ""),
            "author": epub_info.get("author", ""),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": llm_cfg.get("model", ""),
            "provider": llm_cfg.get("provider", ""),
            "target_language": target_lang,
            "chunks": n_chunks,
            "total_chars": epub_info.get("total_chars", 0),
            "chapter_count": len(epub_info.get("chapters", [])),
            "category_schema": cat_meta,
            "format_version": 1,
            "total_terms": total_terms,
            "failed_chunks": len(errors or []),
        },
        "categories": {
            c["id"]: {
                "name": c["name"],
                "hint": c.get("hint", ""),
                "items": merged.get(c["id"], []),
            }
            for c in categories
        },
        "index": index,
    }
    if errors:
        out["errors"] = errors[:50]
    return out
