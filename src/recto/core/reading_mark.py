"""阅读高亮 —— 把「语料」(keywords.json) 变成阅读器里的一层关键词滤镜。

链路
----
    语料分解 json (epub-refiner 导出的 keywords.json / 自己放在书库里的
    `<书名>.keywords.json`)
      → 本模块: 挑语料 / 分预算 / 出词表 + 每类样式
      → GET /read/api/book/<key>/mark
      → reader.js 在章节 DOM 上铺高亮

两种渲染方式
------------
``highlight`` (默认)
    用 CSS Custom Highlight API (``CSS.highlights`` + ``Range``) 上色,
    **一个字符都不改 DOM** —— 阅读器的字节坐标进度、划线选区的文本偏移、
    分页时的列碎片化全都不受影响。
    局限: ``::highlight()`` 只认 color / background-color / text-decoration /
    text-shadow, 粗体 / 圆角 / 内边距这些做不到。

``span``
    把命中词包进 ``<span class="rv-kw rv-kw-<cat>">``, 任意 CSS 都能用,
    但会改 DOM (分页模式下会重排一次)。默认不用。

配置分两层
----------
  - ``config.toml`` 的 ``[reader.mark]`` = **部署默认值** (开关 / 方式 / 语料目录
    / 最短词长 / 词数上限 / 跳过纯数字)
  - ``<data_dir>/reading_mark.json`` = 用的人在设置页改的**覆盖项**
    (含每类配色 / 每本书绑定哪份语料), 缺的键完全跟随 config.toml

为什么还要「绑定」
------------------
书名模糊匹配宁可漏也不乱配 (同作者的另一本书很常见)。语料跟书对不上时,
在设置页给这本书显式指一份语料即可, 不依赖书名。
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

CONF_NAME = "reading_mark.json"

MAX_DIRS = 8
MAX_CORPORA = 400
MAX_TERMS = 6000
MAX_WORD = 80
MIN_LEN = 2
MODES = ("highlight", "span")

# 语料里会混进 "it / him / the" 这类泛词 (抽词时的噪声)。默认挡掉一批;
# 想全高亮就把 stop 清空。
DEFAULT_STOP = [
    "it", "its", "him", "his", "her", "hers", "he", "she", "they", "them",
    "the", "a", "an", "of", "to", "in", "on", "at", "and", "or", "but",
    "is", "was", "are", "were", "be", "been", "do", "did", "does",
    "my", "me", "mine", "you", "your", "i", "we", "us", "our", "this", "that",
    "的", "了", "是", "在", "和", "他", "她", "它", "我", "你", "们",
]

# 分类兜底样式 (语料里带 name 就用语料的 name, 颜色只在这里定)。
#
# 默认只给**半透明底色**、不设文字色: 阅读器有浅色/深色两套主题, 半透明色
# 在两边都只是"一层淡色", 字色跟随主题不受影响 —— 写死的浅底 + 深字放到深色
# 主题里会变成一块刺眼的白斑。想改成实色就在设置页里填。
CATEGORY_DEFAULTS = {
    "person": ("人名", "", "rgba(255, 196, 64, .42)"),
    "place": ("地点", "", "rgba(88, 166, 255, .30)"),
    "org": ("组织", "", "rgba(74, 200, 138, .32)"),
    "object": ("物件", "", "rgba(255, 138, 80, .34)"),
    "event": ("事件", "", "rgba(196, 128, 255, .34)"),
    "time": ("时间", "", "rgba(64, 210, 210, .30)"),
    "term": ("术语", "", "rgba(160, 168, 180, .30)"),
    "work": ("作品", "", "rgba(255, 110, 150, .30)"),
}
_FALLBACK_STYLE = ("", "rgba(255, 214, 102, .38)")

_COLOR_RE = re.compile(
    r"^(#[0-9a-fA-F]{3,8}"
    r"|(?:rgb|rgba|hsl|hsla)\([0-9.,%\s/]*\)"
    r"|[a-zA-Z]{3,24})$")

# 书名比较时剥掉的噪声
_NAME_NOISE = re.compile(
    r"[─-▿！-～（）()《》\[\]【】_\-\s·・:：,，.。!?'\"“”‘’]|"
    r"简体|繁體|繁体|中文|英文|完整版|精校版|校对版|无删减|完结|"
    r" epub|epub|pdf|mobi|azw3|kindle|txt",
    re.I,
)

SKIP_FILES = {"index.json", "latest.json", CONF_NAME}


# ---- 路径 -----------------------------------------------------------------

def _cfg():
    from .config import get_config

    return get_config()


def data_dir() -> Path:
    try:
        return Path(_cfg().data_dir)
    except Exception:  # noqa: BLE001 — 配置读不出来也不该让阅读器白屏
        from . import paths

        return Path(paths.PATHS.data_dir)


def conf_path() -> Path:
    return data_dir() / CONF_NAME


def _corpus_dirs() -> list[str]:
    """config.toml 里给的默认语料目录 (`[corpus] dir`)。"""
    try:
        d = str(_cfg().get("corpus", "dir", "") or "").strip()
    except Exception:  # noqa: BLE001
        d = ""
    return [d] if d else []


# ---- 配置 -----------------------------------------------------------------

def _default_style(cid: str) -> dict:
    name, color, bg = CATEGORY_DEFAULTS.get(cid, ("", _FALLBACK_STYLE[0], _FALLBACK_STYLE[1]))
    return {"enabled": True, "color": color, "bg": bg,
            "underline": False, "bold": False, "italic": False,
            "radius": "", "pad": "", "shadow": ""}


def _config_mark() -> dict:
    """配置层给的默认值 (部署者对 [reader.mark] 的写法)。"""
    try:
        sec = _cfg().section("reader") or {}
        mark = sec.get("mark") if isinstance(sec.get("mark"), dict) else {}
    except Exception:  # noqa: BLE001
        mark = {}
    return mark if isinstance(mark, dict) else {}


def default_conf() -> dict:
    base = _config_mark()
    return {
        "version": 1,
        "updated": "",
        "enabled": bool(base.get("enabled", True)),
        "mode": str(base.get("mode") or "highlight"),
        "dirs": [str(x) for x in (base.get("dirs") or []) if str(x)] or _corpus_dirs(),
        "bind": {},              # {"<book key>": "<语料文件名或绝对路径>"}
        "only": [],              # 只高亮这些分类; 空 = 全部
        "stop": list(DEFAULT_STOP),
        "min_len": int(base.get("min_len") or MIN_LEN),
        "max_terms": int(base.get("max_terms") or MAX_TERMS),
        "skip_digits": bool(base.get("skip_digits", True)),
        "styles": {cid: _default_style(cid) for cid in CATEGORY_DEFAULTS},
    }


def _clean_color(s) -> str:
    s = str(s or "").strip()[:32]
    return s if _COLOR_RE.match(s) else ""


def _clean_short(s, n: int = 24) -> str:
    s = str(s or "")
    for bad in (";", "{", "}", "<", ">", "@", "\\", '"', "'"):
        s = s.replace(bad, "")
    return s.strip()[:n]


def _clean_style(d) -> dict:
    d = d if isinstance(d, dict) else {}
    return {"enabled": bool(d.get("enabled", True)),
            "color": _clean_color(d.get("color")),
            "bg": _clean_color(d.get("bg")),
            "underline": bool(d.get("underline")),
            "bold": bool(d.get("bold")),
            "italic": bool(d.get("italic")),
            "radius": _clean_short(d.get("radius")),
            "pad": _clean_short(d.get("pad")),
            "shadow": _clean_short(d.get("shadow"), 60)}


def normalize(conf) -> dict:
    """任意输入 → 合法配置。坏字段丢掉不抛错 (配置脏了不该让阅读器白屏)。"""
    conf = conf if isinstance(conf, dict) else {}
    base = default_conf()

    dirs: list[str] = []
    for d in (conf.get("dirs") or []):
        d = str(d or "").strip()
        if d.startswith("/") and d not in dirs:
            dirs.append(d)
    if not dirs:
        dirs = list(base["dirs"])

    styles = {}
    styles_in = conf.get("styles") if isinstance(conf.get("styles"), dict) else {}
    for cid, st in styles_in.items():
        cid = _clean_short(cid, 32)
        if not cid:
            continue
        merged = _default_style(cid)
        merged.update(_clean_style(st))
        styles[cid] = merged
    for cid in CATEGORY_DEFAULTS:
        styles.setdefault(cid, _default_style(cid))

    bind = {}
    bind_in = conf.get("bind") if isinstance(conf.get("bind"), dict) else {}
    for k, v in bind_in.items():
        k = str(k or "").strip()
        v = str(v or "").strip()
        if k and v:
            bind[k] = v

    def _int(v, lo, hi, dflt):
        try:
            n = int(v)
        except Exception:  # noqa: BLE001
            return dflt
        return max(lo, min(hi, n))

    stop_in = conf.get("stop")
    stop = ([str(x) for x in stop_in if str(x)][:200]
            if isinstance(stop_in, list) else list(DEFAULT_STOP))

    mode = str(conf.get("mode") or base["mode"]).lower()
    if mode not in MODES:
        mode = "highlight"
    try:
        ver = int(conf.get("version") or 1)
    except Exception:  # noqa: BLE001
        ver = 1
    return {
        "version": max(1, ver),
        "updated": str(conf.get("updated") or ""),
        "enabled": bool(conf.get("enabled", base["enabled"])),
        "mode": mode,
        "dirs": dirs[:MAX_DIRS],
        "bind": bind,
        "only": [str(x) for x in (conf.get("only") or []) if str(x)][:32],
        "stop": stop,
        "min_len": _int(conf.get("min_len"), 1, 12, base["min_len"]),
        "max_terms": _int(conf.get("max_terms"), 50, MAX_TERMS, base["max_terms"]),
        "skip_digits": bool(conf.get("skip_digits", base["skip_digits"])),
        "styles": styles,
    }


def load() -> dict:
    p = conf_path()
    if not p.exists():
        return normalize(default_conf())
    try:
        return normalize(json.loads(p.read_text("utf-8")))
    except Exception:  # noqa: BLE001
        # 文件坏了别拿默认配色乱高亮 —— 关掉, 原文件留着给人看
        return normalize({"enabled": False})


def save(conf) -> dict:
    cur = load()
    new = normalize(conf)
    new["version"] = int(cur.get("version") or 1) + 1
    new["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    p = conf_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{CONF_NAME}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(new, ensure_ascii=False, indent=2), "utf-8")
    os.replace(tmp, p)
    try:  # 服务可能以 root 跑, 交回目录属主
        st = p.parent.stat()
        os.chmod(p, 0o644)
        if os.geteuid() == 0:
            os.chown(p, st.st_uid, st.st_gid)
    except Exception:  # noqa: BLE001
        pass
    return new


def reset() -> dict:
    """恢复默认 (= config.toml 的写法), 连绑定一起清掉。"""
    try:
        conf_path().unlink()
    except OSError:
        pass
    return load()


def set_bind(book_key: str, corpus: str) -> dict:
    conf = load()
    key = str(book_key or "").strip()
    want = str(corpus or "").strip()
    if key:
        if want:
            conf["bind"][key] = want
        else:
            conf["bind"].pop(key, None)
    return save(conf)


# ---- 语料 (keywords.json) -------------------------------------------------

def _words_of_item(it: dict) -> list[str]:
    out = []
    if it.get("term"):
        out.append(str(it["term"]))
    for a in (it.get("aliases") or []):
        if a:
            out.append(str(a))
    return out


def load_corpus(p: Path) -> dict | None:
    """读一份语料。认 epub-refiner 的 categories 格式与术语表 mappings 格式。"""
    try:
        d = json.loads(Path(p).read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(d, dict):
        return None
    cats: dict[str, dict] = {}
    if isinstance(d.get("categories"), dict):
        for cid, c in d["categories"].items():
            if not isinstance(c, dict):
                continue
            cid = str(cid)
            items = list(c.get("items") or [])
            if any(isinstance(it, dict) for it in items):
                def _cnt(pair):
                    idx, it = pair
                    if not isinstance(it, dict):
                        return (1, idx)
                    try:
                        n = int(it.get("count") or 0)
                    except Exception:  # noqa: BLE001
                        n = 0
                    return (-n, idx)
                # 按出现次数降序: 预算截断时先砍冷门词, 不按文件书写顺序砍
                items = [it for _k, it in sorted(enumerate(items), key=_cnt)]
            words: list[str] = []
            for it in items:
                if isinstance(it, dict):
                    words.extend(_words_of_item(it))
                elif isinstance(it, str):
                    words.append(it)
            cats[cid] = {"id": cid, "name": str(c.get("name") or cid),
                         "words": words, "terms": len(c.get("items") or [])}
    elif isinstance(d.get("mappings"), list):
        for m in d["mappings"]:
            if not isinstance(m, dict):
                continue
            cid = str((m.get("tags") or ["term"])[0])
            slot = cats.setdefault(cid, {"id": cid, "name": cid, "words": [], "terms": 0})
            for k in ("source", "target"):
                if m.get(k):
                    slot["words"].append(str(m[k]))
                    slot["terms"] += 1
    if not cats:
        return None
    meta = d.get("meta") if isinstance(d.get("meta"), dict) else {}
    pp = Path(p)
    try:
        stat = pp.stat()
        mtime, size = int(stat.st_mtime), stat.st_size
    except OSError:
        mtime, size = 0, 0
    return {
        "path": str(pp),
        "file": pp.name,
        "stem": pp.stem,
        "title": str(meta.get("title") or pp.stem),
        "author": str(meta.get("author") or ""),
        "lang": str(meta.get("target_language") or ""),
        "source_epub": str(meta.get("source_epub") or ""),
        "generated_at": str(meta.get("generated_at") or ""),
        "mtime": mtime,
        "size": size,
        "categories": cats,
        "words_total": sum(len(c["words"]) for c in cats.values()),
    }


_CORPUS_CACHE: dict[str, tuple[float, dict | None]] = {}


def _corpus_cached(p: Path) -> dict | None:
    key = str(p)
    try:
        mtime = Path(p).stat().st_mtime
    except OSError:
        return None
    hit = _CORPUS_CACHE.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    c = load_corpus(Path(p))
    _CORPUS_CACHE[key] = (mtime, c)
    return c


def scan_corpora(dirs=None) -> list[dict]:
    """扫语料目录里的 *.json → 概览列表 (按 mtime 降序)。"""
    if dirs is None:
        dirs = load()["dirs"]
    out: list[dict] = []
    seen: set[str] = set()
    for d in dirs or []:
        root = Path(str(d)).expanduser()
        if not root.is_absolute() or not root.exists():
            continue
        try:
            files = sorted(root.glob("*.json"))
        except OSError:
            continue
        for f in files[:MAX_CORPORA]:
            if f.name in SKIP_FILES or f.name.endswith(".tmp"):
                continue
            real = str(f)
            if real in seen:
                continue
            c = _corpus_cached(f)
            if not c:
                continue
            seen.add(real)
            out.append({
                "file": c["file"], "path": c["path"], "title": c["title"],
                "author": c["author"], "lang": c["lang"],
                "terms": sum(x["terms"] for x in c["categories"].values()),
                "words": c["words_total"],
                "categories": [{"id": x["id"], "name": x["name"], "words": len(x["words"])}
                               for x in c["categories"].values()],
                "mtime": c["mtime"], "size": c["size"],
            })
    out.sort(key=lambda x: -x["mtime"])
    return out


# ---- 选中哪一份语料 -------------------------------------------------------

def _norm_name(s: str) -> str:
    return _NAME_NOISE.sub("", str(s or "")).lower()


def _tokens(s: str) -> set[str]:
    parts = re.split(r"[^0-9A-Za-z\u4e00-\u9fff]+", str(s or "").lower())
    return {p for p in parts if len(p) >= 2}


def _name_score(name: str, corpus: dict) -> int:
    """书名 ↔ 语料标题/文件名的相似度。0 = 不像; >=50 才认。

    宁可漏 (返回 0, 让人在设置页手动绑定), 也别把"同一作者的另一本书"错配:
    只认 完全相等 / 子串 (且较短串 >= 一半长) / 词集合 Jaccard >= 0.55。
    """
    a = _norm_name(name)
    if not a:
        return 0
    best = 0
    for cand in (_norm_name(corpus.get("title")), _norm_name(corpus.get("stem"))):
        if not cand:
            continue
        if a == cand:
            return 100
        short, long = (a, cand) if len(a) <= len(cand) else (cand, a)
        if len(short) >= 4 and short in long:
            best = max(best, 50 + int(40 * len(short) / max(1, len(long))))
        ta, tc = _tokens(name), _tokens(corpus.get("title"))
        if ta and tc:
            j = len(ta & tc) / len(ta | tc)
            if j >= 0.55:
                best = max(best, 50 + int(40 * j))
    return best


def _same_file(a: str, b: str) -> bool:
    if not a or not b:
        return False
    try:
        return str(Path(a).resolve()) == str(Path(b).resolve())
    except OSError:
        return str(Path(a)) == str(Path(b))


def resolve(book: dict, conf: dict | None = None, corpora: list[dict] | None = None):
    """给一本书挑语料。→ (语料全文, 说明)

    优先级: 显式绑定 > 书库里的 `<书名>.keywords.json` 边车 > 语料 meta 里
    的 source_epub 指向同一份书 > 书名模糊匹配 (>40 分)。
    """
    conf = conf or load()
    corpora = corpora if corpora is not None else scan_corpora(conf["dirs"])
    key = str(book.get("key") or "")
    title = str(book.get("title") or "")
    path = str(book.get("path") or "")

    want = (conf.get("bind") or {}).get(key)
    if want:
        w = str(want)
        for c in corpora:
            if w in (c["file"], c["path"], c["title"]) or Path(c["path"]).stem == w:
                full = _corpus_cached(Path(c["path"]))
                if full:
                    return full, {"how": "bind", "want": w}
        if w.startswith("/") and Path(w).exists():
            c = _corpus_cached(Path(w))
            if c:
                return c, {"how": "bind_path", "want": w}
        return None, {"how": "bind_missing", "want": w}

    # 书库里的边车语料
    if path:
        try:
            from . import sidecar

            kp = sidecar.find_keywords(path)
        except Exception:  # noqa: BLE001
            kp = None
        if kp:
            c = _corpus_cached(Path(kp))
            if c:
                return c, {"how": "sidecar", "file": Path(kp).name}

    for c in corpora:
        if c.get("source_epub") and _same_file(c["source_epub"], path):
            full = _corpus_cached(Path(c["path"]))
            if full:
                return full, {"how": "source_epub"}

    best, best_score = None, 0
    for c in corpora:
        s = _name_score(title, c)
        if s > best_score:
            best, best_score = c, s
    if best and best_score >= 50:
        full = _corpus_cached(Path(best["path"]))
        if full:
            return full, {"how": "name", "score": best_score}
    return None, {"how": "none", "score": best_score}


# ---- 生成给阅读器的 payload -----------------------------------------------

def _clean_word(w: str, conf: dict) -> str:
    w = str(w or "").strip()
    if not w or len(w) > MAX_WORD:
        return ""
    alnum = sum(1 for ch in w if ch.isalnum())
    if alnum < conf["min_len"]:
        return ""
    if conf.get("skip_digits", True) and alnum and all(
            (not ch.isalnum()) or ch.isdigit() for ch in w):
        return ""
    return w


def _allocate(counts: dict[str, int], budget: int) -> dict[str, int]:
    """按分类公平分预算 (注水法)。

    按顺序装到上限就 break 的写法会让大书里第一个分类 (人名) 吃光全部额度,
    之后 7 类一个词都没有 —— 表现为"只有人名高亮"。这里每轮给还缺词的
    分类平均分, 分不完的额度留给别人。
    """
    alloc = {k: 0 for k in counts}
    left = max(0, int(budget))
    pending = [k for k, v in counts.items() if v > 0]
    while left > 0 and pending:
        share = max(1, left // len(pending))
        moved = False
        for k in list(pending):
            take = min(counts[k] - alloc[k], share, left)
            if take > 0:
                alloc[k] += take
                left -= take
                moved = True
            if alloc[k] >= counts[k]:
                pending.remove(k)
            if left <= 0:
                break
        if not moved:            # 理论到不了; 防死循环
            break
    return alloc


def _book_row(book_key: str) -> dict:
    try:
        from ..services import reader_state

        return reader_state.get_book(book_key) or {}
    except Exception:  # noqa: BLE001
        return {}


def build_payload(book_key: str, conf: dict | None = None) -> dict:
    """阅读器要的那一份: 开关 / 方式 / 每类样式 + 词表。"""
    conf = conf or load()
    out = {"ok": True, "enabled": bool(conf["enabled"]), "version": conf["version"],
           "mode": conf["mode"], "book": book_key,
           "minLen": conf["min_len"], "gen": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if not conf["enabled"]:
        return out

    book = _book_row(book_key)
    corpora = scan_corpora(conf["dirs"])
    corpus, why = resolve(book, conf, corpora)
    out["pick"] = why
    out["corpora"] = len(corpora)
    out["book_title"] = str(book.get("title") or "")
    if not corpus:
        out["cats"] = []
        out["total"] = 0
        return out

    only = set(conf["only"] or [])
    stop = {str(s).lower() for s in (conf["stop"] or [])}

    # 第一步: 每类清洗 + 类内去重
    groups: list[dict] = []
    for cid, c in corpus["categories"].items():
        st = conf["styles"].get(cid) or _default_style(cid)
        if only and cid not in only:
            continue
        if not st.get("enabled", True):
            continue
        seen: set[str] = set()
        words = []
        for w in c["words"]:
            w2 = _clean_word(w, conf)
            if not w2:
                continue
            low = w2.lower()
            if low in stop or low in seen:
                continue
            seen.add(low)
            words.append(w2)
        if words:
            groups.append({"id": cid, "name": c["name"], "style": st, "words": words})

    # 第二步: 公平分预算
    quota = _allocate({g["id"]: len(g["words"]) for g in groups}, conf["max_terms"])

    cats_out = []
    total = 0
    seen_global: set[str] = set()
    for g in groups:
        n = quota.get(g["id"], 0)
        if n <= 0:
            continue
        picked = []
        for w in g["words"]:
            low = w.lower()
            if low in seen_global:      # 跨分类去重: 先到先得 (分类顺序 = 语义优先级)
                continue
            picked.append(w)
            seen_global.add(low)
            if len(picked) >= n:
                break
        if not picked:
            continue
        total += len(picked)
        cats_out.append({"id": g["id"], "name": g["name"], "style": g["style"],
                         "words": picked, "words_total": len(g["words"])})

    out["cats"] = cats_out
    out["total"] = total
    out["file"] = corpus["file"]
    out["title"] = corpus["title"]
    out["author"] = corpus["author"]
    out["corpus_mtime"] = corpus["mtime"]
    out["words_corpus"] = int(corpus.get("words_total") or 0)
    return out


# ---- 设置页要的概览 -------------------------------------------------------

def books_with_match(conf: dict | None = None, limit: int = 200) -> list[dict]:
    conf = conf or load()
    corpora = scan_corpora(conf["dirs"])
    rows = []
    try:
        from ..services import reader_state

        books = reader_state.list_books()
    except Exception:  # noqa: BLE001
        books = []
    for b in books[:limit]:
        corpus, why = resolve(b, conf, corpora)
        rows.append({
            "key": b.get("key"), "title": b.get("title"), "author": b.get("author") or "",
            "how": why.get("how"), "score": why.get("score"),
            "bound": str((conf.get("bind") or {}).get(str(b.get("key"))) or ""),
            "file": (corpus or {}).get("file") or "",
            "corpus_title": (corpus or {}).get("title") or "",
            "words": int((corpus or {}).get("words_total") or 0),
        })
    return rows


def summary() -> dict:
    conf = load()
    corpora = scan_corpora(conf["dirs"])
    return {
        "ok": True,
        "enabled": conf["enabled"],
        "version": conf["version"],
        "updated": conf["updated"],
        "mode": conf["mode"],
        "modes": list(MODES),
        "dirs": conf["dirs"],
        "only": conf["only"],
        "min_len": conf["min_len"],
        "max_terms": conf["max_terms"],
        "skip_digits": conf["skip_digits"],
        "styles": conf["styles"],
        "categories": [{"id": cid, "name": CATEGORY_DEFAULTS.get(cid, (cid,))[0]}
                       for cid in CATEGORY_DEFAULTS],
        "conf": conf,
        "corpora": corpora,
        "books": books_with_match(conf),
        "path": str(conf_path()),
        "defaults_source": "config.toml [reader.mark]",
    }


def preview(book_key: str) -> dict:
    """设置页「试算」: 这本书匹配到哪份语料, 实际会高亮多少词、几类。"""
    conf = load()
    pay = build_payload(book_key, conf)
    return {
        "ok": bool(pay.get("total")),
        "enabled": pay["enabled"],
        "mode": pay["mode"],
        "pick": pay.get("pick"),
        "file": pay.get("file"),
        "title": pay.get("title"),
        "total": pay.get("total", 0),
        "words_corpus": pay.get("words_corpus", 0),
        "cats": [{"id": c["id"], "name": c["name"], "words": len(c["words"])}
                 for c in (pay.get("cats") or [])],
    }


def main() -> None:  # 手工排障: python3 -m recto.core.reading_mark [summary|payload KEY|preview KEY]
    import sys

    arg = sys.argv[1:] or ["summary"]
    if arg[0] == "summary":
        print(json.dumps(summary(), ensure_ascii=False, indent=2)[:4000])
    elif arg[0] == "payload" and len(arg) > 1:
        print(json.dumps(build_payload(arg[1]), ensure_ascii=False)[:2000])
    elif arg[0] == "preview" and len(arg) > 1:
        print(json.dumps(preview(arg[1]), ensure_ascii=False, indent=2))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
