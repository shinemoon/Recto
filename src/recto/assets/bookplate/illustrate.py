#!/usr/bin/env python3
"""
illustrate.py — 给 excerpt 配图:意象词 → 必应搜图 → 下载到本地 → data:URL

不依赖外网 key,只用必应国内搜图。 设计目标:
- 配图 = 装饰性 overlay, 不是主角(摘录 + 印章还是主)
- 失败 = 无图, 不阻塞主渲染
- 一张图缓存 24h, 避免重复请求

Usage:
  python3 illustrate.py "海峡 风 青豆"
  python3 illustrate.py --from-excerpt "无名的风瞬间吹过远方的海峡..."
  python3 illustrate.py --json '{"keywords":["海峡","风"]}'
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
CACHE_DIR = Path.home() / ".cache" / "bookplate-img"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_TTL = 24 * 3600  # 24h

# 意象词 → 视觉化关键词(中→中,中→英双轨)
VISUAL_HINTS = {
    "海": ["海洋", "海浪", "ocean", "sea"],
    "风": ["大风", "风吹过", "wind", "breeze"],
    "夜": ["夜晚", "夜色", "night", "evening"],
    "月": ["月亮", "月光", "moon", "moonlight"],
    "窗": ["窗户", "窗外", "window"],
    "灯": ["灯光", "灯下", "light", "lamp"],
    "雪": ["下雪", "雪景", "snow", "snowing"],
    "雨": ["下雨", "雨中", "rain", "rainy"],
    "星": ["星空", "星星", "stars", "starry"],
    "花": ["鲜花", "花开", "flowers", "bloom"],
    "山": ["远山", "山脉", "mountain", "mountains"],
    "云": ["云海", "云层", "cloud", "clouds"],
    "桥": ["桥梁", "桥上", "bridge"],
    "路": ["道路", "小路", "road", "path"],
    "船": ["帆船", "渡船", "ship", "boat"],
    "城": ["城市", "城镇", "city", "town"],
    "树": ["树林", "森林", "tree", "forest"],
    "鸟": ["飞鸟", "鸟", "bird"],
    "火": ["火焰", "篝火", "fire", "flame"],
    "水": ["流水", "溪水", "water", "stream"],
}

# 常见人名/虚词 — 不作为搜图关键词
STOP_WORDS = set("""
那 这 那些 这些 一 一个 一些 我们 你们 他们 她们 什么 怎么 怎样
觉得 知道 听见 看见 想到 一点 一直 一些 已经 还是 但是 之后 之前
可以 可能 应该 没有 已经 不是 然后 如此 如果 因为 所以 但是
青豆 天吾 牛河 角色 女人 男人 小孩 大人
""".split())


def extract_keywords(text: str, max_kw: int = 3) -> list[str]:
    """从 text 抽 max_kw 个意象词。"""
    if not text:
        return []
    chars = re.findall(r"[\u4e00-\u9fff]", text)
    freq: dict[str, int] = {}
    for c in chars:
        if c in STOP_WORDS:
            continue
        if c in VISUAL_HINTS:
            freq[c] = freq.get(c, 0) + 1
    ranked = sorted(freq.items(), key=lambda kv: -kv[1])
    return [c for c, _ in ranked[:max_kw]]


def bing_search(query: str, count: int = 8, timeout: int = 12) -> list[str]:
    """必应搜图,返回原图 URL 列表(国内 CDN)。"""
    q = urllib.parse.quote(query)
    url = f"https://cn.bing.com/images/search?q={q}&form=HDRSC2&first=1&count={count}"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            html = r.read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"[bing] fetch fail: {e}", file=sys.stderr)
        return []
    # 抽 murl:"..."
    urls = re.findall(r'murl&quot;:&quot;(https?://[^&]+\.(?:jpg|jpeg|png))', html)
    return list(dict.fromkeys(urls))  # dedup,保序


def _cache_key(query: str) -> Path:
    h = hashlib.sha1(query.encode("utf-8")).hexdigest()[:16]
    return CACHE_DIR / f"{h}.b64"


def _cache_meta(p: Path) -> dict:
    meta = p.with_suffix(".json")
    if not meta.exists():
        return {}
    try:
        return json.loads(meta.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _is_cached(p: Path) -> bool:
    if not p.exists():
        return False
    meta = _cache_meta(p)
    ts = meta.get("ts", 0)
    return (time.time() - ts) < CACHE_TTL


def _save_cache(p: Path, urls: list[str], chosen_url: str, query: str) -> None:
    p.write_text(p.read_text(encoding="ascii"), encoding="ascii")  # noop
    meta = p.with_suffix(".json")
    meta.write_text(json.dumps({
        "ts": time.time(),
        "query": query,
        "urls": urls,
        "chosen": chosen_url,
    }, ensure_ascii=False), encoding="utf-8")


def pick_image(keywords: list[str], timeout: int = 12, style: str = "") -> Optional[dict]:
    """挑一张图,返回 {data_uri, source_url, query} 或 None。"""
    if not keywords:
        return None
    # 选第一个意象词 + 2 个候选(原文 + 英文),任一搜到就 OK
    candidates: list[str] = []
    for i, kw in enumerate(keywords):
        candidates.append(kw)
        if kw in VISUAL_HINTS:
            candidates.extend(VISUAL_HINTS[kw][:2])
    if not candidates:
        return None
    # 版画风: query 拼 woodcut + 主体留白指令, 必应会返黑白线条图
    style_suffix = ""
    if style == "woodcut":
        # 版画 / 木刻 黑白线条, 主体居中或留白
        style_suffix = " 版画 木刻 黑白 线条"
    query = candidates[0] + style_suffix
    cache = _cache_key(query)
    if _is_cached(cache):
        b64 = cache.read_text(encoding="ascii")
        meta = _cache_meta(cache)
        return {"data_uri": b64, "source_url": meta.get("chosen", ""), "query": query, "cached": True}
    # 必应搜图
    urls = bing_search(query, count=10, timeout=timeout)
    if not urls:
        return None
    chosen = random.choice(urls[:6])  # 前 6 随机,排名靠前质量高
    # 下载
    try:
        req = urllib.request.Request(chosen, headers={"User-Agent": UA, "Referer": "https://cn.bing.com/"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            blob = r.read()
        # 限 2MB 防爆
        if len(blob) > 2 * 1024 * 1024:
            return None
        b64 = base64.b64encode(blob).decode("ascii")
        mime = "image/jpeg" if chosen.lower().endswith((".jpg", ".jpeg")) else "image/png"
        data_uri = f"data:{mime};base64,{b64}"
        # 缓存存完整 data_uri (带前缀), 读出来就能直接用
        cache.write_text(data_uri, encoding="ascii")
        _save_cache(cache, urls, chosen, query)
        return {"data_uri": data_uri, "source_url": chosen, "query": query, "cached": False}
    except Exception as e:
        print(f"[img] download fail: {e}", file=sys.stderr)
        return None


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("keywords", nargs="*", help="意象词")
    p.add_argument("--from-excerpt", help="从摘录文本自动抽意象词")
    p.add_argument("--json", help='JSON {keywords:[..]} 或 {content:".."}')
    p.add_argument("--max", type=int, default=3, help="最多抽几个词")
    p.add_argument("--style", default="", help="画面风格: woodcut=版画木刻 / 留空=自然")
    p.add_argument("--info", action="store_true", help="只输出 JSON 摘要,不下载")
    args = p.parse_args(argv)
    kws: list[str] = []
    if args.json:
        obj = json.loads(args.json)
        kws = obj.get("keywords") or extract_keywords(obj.get("content", ""), args.max)
    elif args.from_excerpt:
        kws = extract_keywords(args.from_excerpt, args.max)
    else:
        kws = list(args.keywords)
    if not kws:
        print(json.dumps({"ok": False, "reason": "no keywords"}, ensure_ascii=False))
        return 0
    if args.info:
        print(json.dumps({"keywords": kws, "style": args.style}, ensure_ascii=False))
        return 0
    result = pick_image(kws, style=args.style)
    if not result:
        print(json.dumps({"ok": False, "keywords": kws}, ensure_ascii=False))
        return 0
    out = {
        "ok": True,
        "keywords": kws,
        "query": result["query"],
        "source_url": result["source_url"],
        "cached": result.get("cached", False),
        "data_uri_len": len(result["data_uri"]),
    }
    # data_uri 单写一行, 走专用 key 方便调用方读
    print(json.dumps(out, ensure_ascii=False))
    print("---DATA-URI---")
    print(result["data_uri"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
