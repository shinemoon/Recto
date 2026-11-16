"""查词 —— 给阅读器选区工具条的「查询」用。

**为什么需要它**: 读书遇到生词, 原来是"复制 → 切到字典 App → 粘贴", 一断就
读不下去了。选区工具条里点一下就该出释义。

**为什么在服务端转发**: 手机上的页面是跨域访问外站, 且各站对 Referer/UA 挑剔;
转发一次, 前端只吃自家 JSON (也不会把阅读内容拼进第三方 URL 的 query 里被
浏览器历史/日志记两份)。服务器在国内, 用国内可达的接口。

**用的是哪个接口**: 有道词典的开放 JSON 接口 (无需 key):
  · ``dict.youdao.com/jsonapi?q=词``   —— 最全: 汉语给拼音/词性/释义/例句 (《现代
    汉语规范词典》), 英汉给音标/释义/双语例句
  · ``dict.youdao.com/suggest?...``    —— 退路: 只要"词 + 一行解释", 接口最稳

**口径 (踩过才知道的)**:
  · 查汉语词时 ``jsonapi`` 的 ``newhh`` 才是中文释义; 只有 ``ec`` 时是"英文释义
    中文词条", 不是读者要的
  · 释义里会带 ``<b>`` / ``<self>`` 这类标签, 必须剥掉 (否则前端显示成源码)
  · 例句里用 ``<self>`` 标出被查词, 换成「」更像阅读界面
  · 同一个词反复点很常见 → 结果缓存 12 小时 (缓存的是**规范化后的**数据,
    不缓存带异常的响应)
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
import urllib.request
from typing import Any

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

_JSON_API = "https://dict.youdao.com/jsonapi?q={q}"
_SUGGEST = ("https://dict.youdao.com/suggest?num={n}&ver=3.0&doctype=json"
            "&cache=false&q={q}")

_MAX_Q = 40            # 再长就不是"查词"了, 交给网页搜索
_NORM_MAX = 80         # 清洗时先留点余量, 超过 _MAX_Q 再由 lookup 拒
_TTL = 12 * 3600
_CACHE_MAX = 400

_CACHE: dict[str, tuple[float, dict]] = {}
_LOCK = threading.Lock()

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\u00a0]+")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_EN_RE = re.compile(r"^[A-Za-z][A-Za-z'’\-\. ]{0,38}$")


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def _clean(s: Any) -> str:
    """剥标签 / 压空白 —— 词典的释义串里常带 <b> 与多余空格。"""
    if s is None:
        return ""
    t = _TAG_RE.sub("", str(s))
    t = t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
    return _WS_RE.sub(" ", t).strip()


def _example(s: Any) -> str:
    """例句: <self>被查词</self> → 「被查词」, 更好读。"""
    if s is None:
        return ""
    t = str(s)
    t = t.replace("<self>", "「").replace("</self>", "」")
    return _clean(t)


def _lang_of(q: str) -> str:
    if _CJK_RE.search(q):
        return "zh"
    if _EN_RE.match(q):
        return "en"
    return "other"


def _norm_query(q: str) -> str:
    q = (q or "").strip()
    q = re.sub(r"\s+", " ", q)
    # 选区常带首尾标点/引号, 去掉再查 (「蹉跎，」查不到)
    q = q.strip("，。、；：？！…—·\"'“”‘’「」『』()（）[]【】《》<> \t\r\n")
    return q[:_NORM_MAX]


def _get_json(url: str, timeout: float) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": _UA,
                                               "Accept": "application/json,text/plain,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(600_000)
    except Exception:                       # noqa: BLE001 — 外部接口, 失败即降级
        return None
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------

def _parse_zh(d: dict) -> list[dict]:
    """汉语词条: newhh (《现代汉语规范词典》) → 拼音 / 词性 / 释义 / 例句。"""
    out: list[dict] = []
    hh = (d.get("newhh") or {}).get("dataList") or []
    for item in hh[:3]:
        if not isinstance(item, dict):
            continue
        defs, examples = [], []
        for sense in (item.get("sense") or [])[:4]:
            if not isinstance(sense, dict):
                continue
            cat = _clean(sense.get("cat") or item.get("cat") or "")
            for one in (sense.get("def") or [])[:4]:
                t = _clean(one)
                if t:
                    defs.append((cat + " " if cat else "") + t)
            for ex in (sense.get("examples") or [])[:2]:
                t = _example(ex)
                if t:
                    examples.append(t)
        if defs or examples:
            out.append({
                "word": _clean(item.get("word")) or "",
                "phone": _clean(item.get("pinyin")),
                "cat": _clean(item.get("cat")),
                "defs": defs[:6],
                "examples": examples[:3],
                "source": _clean(((d.get("newhh") or {}).get("source") or {}).get("name")),
            })
    return out


def _parse_en(d: dict) -> list[dict]:
    """英汉: ec.word[] → 音标 + trs[].tr[].l.i[] 释义行。"""
    out: list[dict] = []
    ec = d.get("ec") or {}
    words = ec.get("word") or []
    if isinstance(words, dict):
        words = [words]
    for w in words[:3]:
        if not isinstance(w, dict):
            continue
        defs: list[str] = []
        for tr in (w.get("trs") or [])[:6]:
            for one in ((tr or {}).get("tr") or [])[:6]:
                node = ((one or {}).get("l") or {}).get("i")
                if isinstance(node, list):
                    node = " ".join(_clean(x) for x in node)
                t = _clean(node)
                if t:
                    defs.append(t)
        phone = _clean(w.get("ukphone") or w.get("usphone") or "")
        rp = w.get("return-phrase")
        if isinstance(rp, dict):
            rp = ((rp.get("l") or {}).get("i"))
            if isinstance(rp, list):
                rp = " ".join(_clean(x) for x in rp)
        if defs:
            out.append({
                "word": _clean(rp) or "",
                "phone": phone,
                "cat": "",
                "defs": defs[:8],
                "examples": [],
                "source": _clean((ec.get("source") or {}).get("name")),
            })
    # 双语例句 (blng_sents_part) 挑前两条, 有就更好, 没有也不影响
    pairs = ((d.get("blng_sents_part") or {}).get("sentence-pair") or [])
    if out and isinstance(pairs, list):
        ex = []
        for p in pairs[:2]:
            if isinstance(p, dict):
                t = _example(p.get("sentence")) + " / " + _example(p.get("sentence-translation"))
                if t.strip(" /"):
                    ex.append(t)
        out[0]["examples"] = ex[:2]
    return out


def _parse_suggest(d: dict) -> list[dict]:
    """退路: suggest 只给「词 + 一行解释」, 但接口最稳。"""
    out: list[dict] = []
    data = d.get("data") or {}
    for e in (data.get("entries") or [])[:6]:
        if not isinstance(e, dict):
            continue
        entry = _clean(e.get("entry"))
        explain = _clean(e.get("explain"))
        if entry or explain:
            out.append({"word": entry, "phone": "", "cat": "",
                        "defs": [explain] if explain else [],
                        "examples": [], "source": "有道词典"})
    return out


def _href(q: str, lang: str) -> str:
    return ("https://dict.youdao.com/result?word=" + urllib.parse.quote(q)
            + "&lang=" + ("en" if lang == "en" else "zh"))


# --------------------------------------------------------------------------
# 对外
# --------------------------------------------------------------------------

def lookup(q: str, *, timeout: float = 6.0, use_cache: bool = True) -> dict:
    """查一个词。永远返回 dict (``ok`` 表示有没有查到), 不抛异常。"""
    word = _norm_query(q)
    lang = _lang_of(word)
    out: dict = {
        "ok": False, "q": word or (q or "")[:60], "raw_q": (q or "")[:120],
        "lang": lang, "source": "", "href": _href(word, lang),
        "entries": [],
    }
    if not word:
        out["error"] = "没有可查的词"
        return out
    if len(word) > _MAX_Q:
        out["error"] = "太长了，用网页搜索更合适"
        return out

    ck = lang + "|" + word
    now = time.time()
    if use_cache:
        with _LOCK:
            hit = _CACHE.get(ck)
        if hit and hit[0] > now:
            return dict(hit[1])

    d = _get_json(_JSON_API.format(q=urllib.parse.quote(word)), timeout)
    entries: list[dict] = []
    if d:
        entries = _parse_zh(d) if lang == "zh" else _parse_en(d)
        if not entries:
            # 汉语词也可能只有英汉节 (新词/外来词), 反过来也认
            entries = _parse_zh(d) or _parse_en(d)
    if not entries:
        sg = _get_json(_SUGGEST.format(n=6, q=urllib.parse.quote(word)), timeout)
        if sg:
            entries = _parse_suggest(sg)
    if not entries:
        out["error"] = "词典里没查到这个说法"
        return out

    out["ok"] = True
    out["entries"] = entries[:6]
    out["source"] = next((e.get("source") for e in entries if e.get("source")), "有道词典")
    if use_cache:
        with _LOCK:
            if len(_CACHE) > _CACHE_MAX:
                for k in sorted(_CACHE, key=lambda x: _CACHE[x][0])[: _CACHE_MAX // 4]:
                    _CACHE.pop(k, None)
            _CACHE[ck] = (now + _TTL, dict(out))
    return out


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()
