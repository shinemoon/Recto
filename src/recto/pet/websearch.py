"""为书籍知识卡做一次很轻的网络检索 (无 API key, 无依赖)。

**为什么要它**: 书宠对一本书的理解不能只靠模型印象 —— 生僻书 / 新书 / 译名不同
都会让模型胡说。检索只是"给模型一点凭据", 拿到几条摘要就够, 不需要精确。

**为什么这么薄**: 这不是搜索引擎客户端, 是"有就更好, 没有也能跑"的旁证通道。
所以:

  · 只用最朴素的一次 GET (cn.bing.com 的 HTML 结果页), 不做分页 / 不做 JS
  · 超时短 (默认 8s), 失败就返回 ``status="off"``, 调用方照旧让模型凭常识写
  · 结果只留 title / url / snippet, 每条裁到 240 字 (够模型定位主题)
  · 结果缓存 6 小时 (同一本书一轮里会被问好几次)

网络环境是"国内可达"的假设 —— 用 bing 而不是 google; 换成别的引擎只要改
``_ENGINES`` 里的 URL 与解析正则。
"""
from __future__ import annotations

import html
import re
import threading
import time
import urllib.parse
import urllib.request
from typing import Any

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

_ENGINES = {
    "bing": "https://cn.bing.com/search?q={q}&setlang=zh-hans&count={n}",
}

_CACHE: dict[str, tuple[float, dict]] = {}
_LOCK = threading.Lock()
CACHE_SEC = 6 * 3600

_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_RE = re.compile(r'<li class="b_algo".*?</li>', re.S)
_TITLE_RE = re.compile(r"<h2[^>]*>.*?<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", re.S)
_SNIP_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.S)


def _text(s: str, limit: int) -> str:
    s = _TAG_RE.sub("", s or "")
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def _parse(raw: str, limit: int) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for block in _BLOCK_RE.findall(raw)[: max(1, limit) * 2]:
        m = _TITLE_RE.search(block)
        if not m:
            continue
        url, title = m.group(1), _text(m.group(2), 120)
        sm = _SNIP_RE.search(block)
        snippet = _text(sm.group(1), 240) if sm else ""
        if title:
            out.append({"title": title, "url": url, "snippet": snippet})
        if len(out) >= limit:
            break
    return out


def search(query: str, limit: int = 5, timeout: float = 8.0,
           engine: str = "bing") -> dict[str, Any]:
    """检索一次。

    返回 ``{"status": "ok|off|empty", "engine": ..., "results": [...], "error": ""}``。

    ``status`` 语义 (调用方与模型都按这个读):

      · ``ok``    拿到至少一条结果
      · ``empty`` 请求成功但没解析出东西 (引擎改版 / 关键词太怪)
      · ``off``   没发出去或网络失败 (超时 / DNS / 拒绝) —— 不是"没有这本书"
    """
    q = (query or "").strip()
    if not q:
        return {"status": "off", "engine": engine, "results": [], "error": "空查询"}
    key = f"{engine}|{q}|{limit}"
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < CACHE_SEC:
            return dict(hit[1])

    tpl = _ENGINES.get(engine) or _ENGINES["bing"]
    url = tpl.format(q=urllib.parse.quote(q), n=max(1, min(10, limit)))
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(600_000).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001 — 网络不通是常态, 不抛出
        res = {"status": "off", "engine": engine, "results": [],
               "error": f"{type(e).__name__}: {e}"[:200]}
        with _LOCK:
            _CACHE[key] = (now, res)
        return dict(res)

    rows = _parse(raw, limit)
    res = {"status": "ok" if rows else "empty", "engine": engine,
           "results": rows, "error": ""}
    with _LOCK:
        _CACHE[key] = (now, res)
    return dict(res)


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()
