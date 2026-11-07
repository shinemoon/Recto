"""Web admin UI for PaperEcho.

Mounted under /admin/* on the same BaseHTTPRequestHandler that runs
the M5Paper /api/sync endpoints. Stdlib-only. Sessions via HMAC-signed
cookie. Reads aggregate + events for browsing, supports search,
delete, export, basic stats.

Routes:
    GET  /admin/login
    POST /admin/login
    GET  /admin/logout
    GET  /admin/
    GET  /admin/books
    GET  /admin/books/<id>
    GET  /admin/excerpts
    GET  /admin/events
    GET  /admin/api/excerpts.json
    POST /admin/api/delete
    GET  /admin/static/<file>
"""

from __future__ import annotations

import hashlib
import hmac
import html
import importlib
import json
import os
import re
import secrets
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable

# Canonical bookplate style registry — single source of truth (bin/render.py).
# Imported here so the webui "书票" link and route default stay in sync with
# the renderer's CLI default. If you change styles in render.py, no edit
# needed here.
from recto.core import legacy  # noqa: E402
from recto.assets.bookplate import render as _render  # noqa: E402
from recto.core import sidecar as _sidecar  # noqa: E402
from recto.services import reader_state as _reader_state  # noqa: E402
from recto.services import sessions as _sessions  # noqa: E402

# Cookie name + lifetime
_COOKIE_NAME = legacy.COOKIE_ADMIN
_COOKIE_NAME_LEGACY = legacy.COOKIE_ADMIN_LEGACY
_SESSION_TTL = 24 * 3600
_COOKIE_PATH = "/admin/"
# 书票渲染 (headless chromium 冷启动 + 截屏) 的硬超时。
BOOKPLATE_TIMEOUT = 120


# ---------- Auth ----------

def _sign(value: str, key: str) -> str:
    return hmac.new(key.encode(), value.encode(), hashlib.sha256).hexdigest()


def _make_session_cookie(password: str) -> str:
    """Build a signed session token: <exp>.<nonce>.<sig>."""
    exp = int(time.time()) + _SESSION_TTL
    nonce = secrets.token_hex(8)
    payload = f"{exp}.{nonce}"
    sig = _sign(payload, password)
    return f"{payload}.{sig}"


def _verify_session_cookie(raw: str, password: str) -> bool:
    try:
        exp_str, nonce, sig = raw.split(".", 2)
        exp = int(exp_str)
    except (ValueError, AttributeError):
        return False
    if exp < int(time.time()):
        return False
    expected = _sign(f"{exp_str}.{nonce}", password)
    return hmac.compare_digest(expected, sig)


def _get_password(handler: BaseHTTPRequestHandler) -> str | None:
    """Return the configured admin password, or None if disabled."""
    # resolve_config lives in device; we read the env directly
    import os

    pw = os.environ.get("WEB_ADMIN_PASSWORD", "").strip()
    return pw or None


def _is_authed(handler: BaseHTTPRequestHandler) -> bool:
    # Phase 2 统一登录: Recto 会话 (书库账号密码) 与独立密码等价。
    # 老的 WEB_ADMIN_PASSWORD 保留兜底 —— 已发出去的 /admin 链接不会因为合并登录失效。
    try:
        from recto.app import webauth as _wa

        if _wa.enabled() and _wa.current_user(handler.headers.get("Cookie")):
            return True
    except Exception:
        pass
    pw = _get_password(handler)
    if not pw:
        return False  # disabled if no password set
    raw = handler.headers.get("Cookie", "")
    cookies = SimpleCookie(raw)
    jar = cookies.get(_COOKIE_NAME) or cookies.get(_COOKIE_NAME_LEGACY)
    if jar is None:
        return False
    return _verify_session_cookie(jar.value, pw)


# ---------- Data loaders ----------

def _aggregate_paths(data_dir: Path) -> tuple[Path, Path]:
    # NOTE: as of 2026-06-15, the aggregate dir ships only
    # aggregate.jsonl (excerpts) + books.jsonl. excerpts.jsonl is
    # referenced in storage.md but never created by the server — it
    # is the same file as aggregate.jsonl.
    agg = data_dir / "aggregate" / "aggregate.jsonl"
    books = data_dir / "aggregate" / "books.jsonl"
    return agg, books


def _load_jsonl_cached(path: Path) -> list[dict]:
    """按 (mtime, size) 记忆一次解析结果 —— 单页要渲染多个面板, 别把
    同一个 jsonl 解析五遍。文件一变 (mtime/size 变) 立刻重读。"""
    try:
        st = path.stat()
    except OSError:
        return []
    key = (str(path), st.st_mtime_ns, st.st_size)
    hit = _JSONL_CACHE.get(key)
    if hit is not None:
        return hit
    out: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    _JSONL_CACHE.clear()  # 只留最新一份, 防内存里堆历史
    _JSONL_CACHE[key] = out
    return out


_JSONL_CACHE: dict[tuple, list[dict]] = {}


def _load_books(data_dir: Path) -> dict[int | str, dict]:
    _, books_path = _aggregate_paths(data_dir)
    out: dict[int | str, dict] = {}
    if not books_path.exists():
        return out
    for r in _load_jsonl_cached(books_path):
        bid = r.get("id")
        if bid is None:
            continue
        if isinstance(bid, str) and bid.isdigit():
            bid = int(bid)
        out[bid] = r
    return out


def _load_excerpts(data_dir: Path) -> list[dict]:
    excerpts_path, _ = _aggregate_paths(data_dir)
    if not excerpts_path.exists():
        return []
    return _load_jsonl_cached(excerpts_path)


def _load_events(data_dir: Path, limit: int = 500) -> list[dict]:
    p = data_dir / "events.jsonl"
    if not p.exists():
        return []
    return _load_jsonl_cached(p)[-limit:][::-1]  # newest first


# ---------- Time formatting ----------

def _fmt_ts(raw) -> str:
    if raw is None or raw == "":
        return ""
    if isinstance(raw, (int, float)):
        # assume ms
        try:
            return datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        except (ValueError, OSError, OverflowError):
            return ""
    if isinstance(raw, str):
        # try iso
        try:
            s = raw.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone().strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return raw[:16]
    return ""


def _month_header(month_key: str, count: int) -> str:
    """月份分隔条 —— 年份 + 月份 + 条数, 三件事一行说完。"""
    if "-" in month_key:
        year, month = month_key.split("-", 1)
        month_txt = f"{int(month)} 月" if month.isdigit() else month
    else:
        year, month_txt = month_key, ""
    return (
        '<div class="tl-date">'
        f'<span class="year">{_e(year)}</span>'
        f'<span class="ymd">{_e(month_txt)}</span>'
        f'<span class="count">{count} 条</span>'
        '</div>'
    )


def _fmt_event_ts(raw: str) -> str:
    """events.jsonl uses UTC Z; show local + UTC."""
    if not raw:
        return ""
    try:
        s = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone().strftime("%Y-%m-%d %H:%M")
        return local
    except ValueError:
        return raw[:16]


# ---------- Filter helpers ----------

def _filter_excerpts(
    excerpts: list[dict],
    q: str = "",
    book_id: str = "",
    book_q: str = "",
    author: str = "",
    day_from: str = "",
    day_to: str = "",
    books: dict | None = None,
) -> list[dict]:
    out = list(excerpts)
    books = books or {}

    if book_id:
        try:
            bid = int(book_id)
            out = [e for e in out if e.get("bookId") == bid]
        except ValueError:
            pass

    if book_q:
        ql = book_q.lower()
        matched_bids: set = set()
        for bid, b in books.items():
            name = (b.get("name", "") or "").lower()
            if ql in name:
                matched_bids.add(bid)
                # also match str-keyed ids (历史合并来源用字符串 id)
                try:
                    matched_bids.add(str(bid))
                except Exception:
                    pass
        out = [e for e in out if e.get("bookId") in matched_bids]

    if author:
        al = author.lower()
        matched_bids_a: set = set()
        for bid, b in books.items():
            a = (b.get("author", "") or "").lower()
            if al in a:
                matched_bids_a.add(bid)
                try:
                    matched_bids_a.add(str(bid))
                except Exception:
                    pass
        out = [e for e in out if e.get("bookId") in matched_bids_a]

    if day_from:
        try:
            df = int(datetime.fromisoformat(day_from).timestamp() * 1000)
            out = [e for e in out if _ts_ms(e) and _ts_ms(e) >= df]
        except ValueError:
            pass

    if day_to:
        try:
            # inclusive end-of-day
            dt_to = datetime.fromisoformat(day_to)
            dt_to = dt_to.replace(hour=23, minute=59, second=59)
            dt_to_ms = int(dt_to.timestamp() * 1000)
            out = [e for e in out if _ts_ms(e) and _ts_ms(e) <= dt_to_ms]
        except ValueError:
            pass

    if q:
        ql = q.lower()
        out = [e for e in out if ql in (e.get("content", "") or "").lower()
               or ql in (e.get("idea", "") or "").lower()]

    return out


# _filter_excerpts 接受显式 books dict; 调用方需自己 _load_books
# (避免 module-level cache 在 hot-reload 时变成 stale)



def _ts_ms(e: dict) -> int:
    t = e.get("createdTime")
    if isinstance(t, (int, float)):
        return int(t)
    if isinstance(t, str) and t:
        try:
            return int(datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            return 0
    return 0


# ---------- HTML helpers ----------

_HTML_ESCAPE_TABLE = str.maketrans({"<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})


def _e(s: Any) -> str:
    if s is None:
        return ""
    return html.escape(str(s), quote=True)


def _highlight(text: str, q: str) -> str:
    if not q:
        return _e(text)
    out = _e(text)
    # case-insensitive highlight on escaped form (Chinese unaffected)
    pattern = re.compile(re.escape(_e(q)), re.IGNORECASE)
    return pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", out)


# ---------- Source / meta helpers ----------

def _source_type(excerpt: dict) -> str:
    """Infer where the excerpt came from. The aggregate doesn't carry an
    explicit source field, but the deck-id prefix is a reliable signal:
        merged-*  -> 合并 (历史 deck 名以 kavita-merge- 开头, 继续兼容)
        anything else  -> M5Paper device push
    """
    deck = str(excerpt.get("_firstSeenDeck", ""))
    if deck.startswith(("kavita-merge-", "merged-")):
        return "合并"
    if deck:
        return "M5Paper"
    return "Unknown"


def _source_badge(excerpt: dict) -> str:
    src = _source_type(excerpt)
    cls = "kv" if src == "合并" else "m5"
    return f'<span class="src-badge {cls}" title="来源: {_e(src)}">{_e(src)}</span>'


def _char_count(excerpt: dict) -> int:
    return len(excerpt.get("content", "") or "")


def _book_meta_line(excerpt: dict, books: dict) -> str:
    """Render book name + author chip for an excerpt."""
    bid = excerpt.get("bookId")
    b = books.get(bid) or {}
    name = b.get("name", f"#{bid}")
    author = b.get("author", "")
    if author:
        return f'<a href="/admin/books/{_e(bid)}"><b>{_e(name)}</b></a> <span class="muted">· {_e(author)}</span>'
    return f'<a href="/admin/books/{_e(bid)}"><b>{_e(name)}</b></a>'


# ---------- Bulk action JS ----------

_BULK_JS = """
<script>
(function(){
  // copy single excerpt (全局, 因为面板内容会被整块替换, 不能绑定一次性监听)
  window.copyEx = function(btn, text){
    var done = function(){
      var old = btn.textContent;
      btn.textContent = '已复制';
      btn.classList.add('copied');
      setTimeout(function(){ btn.textContent = old; btn.classList.remove('copied'); }, 1200);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(function(){ legacy(text, done); });
    } else { legacy(text, done); }
    function legacy(t, cb){
      var ta = document.createElement('textarea');
      ta.value = t; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); cb(); } catch (e) {}
      document.body.removeChild(ta);
    }
  };

  function checks() {
    return Array.prototype.slice.call(document.querySelectorAll('.excerpt-check'));
  }
  function update(){
    var bar = document.getElementById('bulkBar');
    if (!bar) return;
    var n = checks().filter(function(c){ return c.checked; }).length;
    var selCount = document.getElementById('selCount');
    if (n > 0) { bar.classList.add('show'); if (selCount) selCount.textContent = n; }
    else {
      bar.classList.remove('show');
      var all = document.getElementById('selAll');
      if (all) all.checked = false;
    }
  }

  document.addEventListener('change', function(ev){
    var t = ev.target;
    if (!t) return;
    if (t.classList && t.classList.contains('excerpt-check')) { update(); return; }
    if (t.id === 'selAll') {
      checks().forEach(function(c){ c.checked = t.checked; });
      update();
    }
  });

  document.addEventListener('click', function(ev){
    var t = ev.target.closest ? ev.target.closest('#selNone') : null;
    if (!t) return;
    checks().forEach(function(c){ c.checked = false; });
    update();
  });

  document.addEventListener('submit', function(ev){
    var f = ev.target;
    if (!f || f.id !== 'bulkForm') return;
    var ids = checks().filter(function(c){ return c.checked; }).map(function(c){ return c.value; });
    if (!ids.length) { ev.preventDefault(); return; }
    if (!confirm('确认删除这 ' + ids.length + ' 条摘录？此操作不可撤销。')) { ev.preventDefault(); return; }
    var box = document.getElementById('bulkIds');
    if (box) box.value = ids.join(',');
  });

  // 面板内容被替换后, 选择态归零
  document.addEventListener('recto:pane', function(){ update(); });
})();
</script>
"""


# ---------- Template parts ----------

def _masthead(handler) -> str:
    """保留这个名字只为兼容老调用 —— 导航已由统一外壳出。"""
    from recto.app.ui import shell as _shell
    return _shell.subnav_html(getattr(handler, "_active_nav", "") or "")


def _page(title: str, body: str, active: str = "", handler=None) -> str:
    """摘录后台整页 —— 与其它分区共用同一个外壳、同一份皮肤、同一套导航。"""
    from recto.app.ui import shell as _shell

    if handler is not None:
        try:
            handler._active_nav = active
        except Exception:
            pass
    user = None
    try:
        user = handler._user() if handler is not None else None
    except Exception:  # noqa: BLE001 — 独立运行时不一定有会话
        user = None
    head = title.split(" · ")[0] if title else "摘录"
    return _shell.document(
        title=head, body=body, active="excerpts", user=user,
        show_subnav=True, sub_active=active, head_extra=f"<style>{_EXTRA_CSS}</style>",
    )


def _nav(active: str, handler) -> str:
    return _masthead(handler)


# 统一设计系统 (assets/recto.css) 已经覆盖九成九的零件; 这里只补摘录特有的。
_EXTRA_CSS = """
.excerpt-list{display:flex;flex-direction:column;gap:12px}
.excerpt-check{width:17px;height:17px;align-self:flex-start;margin-top:3px}
.tl-footer form{display:inline-flex;gap:6px;align-items:center;margin:0}
.kt-row{cursor:pointer}
.kt-row:hover td{background:var(--rc-surface-2)}
.kt-detail td{background:var(--rc-sunk);font-family:var(--rc-mono);
  font-size:var(--rc-fs-xs);line-height:1.6}
#include{display:inline-block;color:var(--rc-mute);font-size:var(--rc-fs-xs);
  word-break:break-all}
.rc-note{border-left:2px solid var(--rc-accent-soft);padding-left:12px;
  color:var(--rc-dim);font-family:var(--rc-serif);margin-top:6px}
.rc-pair{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media (max-width:700px){.rc-pair{grid-template-columns:1fr}}
"""

# ---------- Page renderers ----------

def _render_login(handler, error: str = "") -> str:
    """摘录后台的登录入口 —— 与主控制台同一张表单 (同一个会话)。"""
    from recto.app.ui import shell as _shell

    return _shell.auth_page(
        title="摘录接入",
        body=_shell.login_body(err=error, next_url="/admin/"),
    )


def _render_home(handler, data_dir: Path) -> str:
    books = _load_books(data_dir)
    excerpts = _load_excerpts(data_dir)
    events = _load_events(data_dir, limit=200)

    # Source split
    n_m5 = sum(1 for e in excerpts if _source_type(e) == "M5Paper")
    n_kv = sum(1 for e in excerpts if _source_type(e) == "合并")

    # 7-day bar chart
    buckets: dict[str, int] = {}
    today = datetime.now().date()
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        buckets[d.isoformat()] = 0
    for e in excerpts:
        ms = _ts_ms(e)
        if not ms:
            continue
        d = datetime.fromtimestamp(ms / 1000).date().isoformat()
        if d in buckets:
            buckets[d] += 1

    max_v = max(buckets.values()) if buckets else 0
    bars = []
    for d, v in buckets.items():
        # 0 条也留一根"地平线"细条, 否则那天在图上凭空消失
        h = 0 if not max_v or not v else max(6, round(96 * v / max_v))
        short = d[5:]  # MM-DD
        is_today = (d == today.isoformat())
        cls = "bar"
        if is_today:
            cls += " today"
        if not v:
            cls += " zero"
        bars.append(
            f'<div class="{cls}" style="height:{h}px" title="{d}: {v} 条">'
            f'<span class="v">{v if v else ""}</span>'
            f'<span class="lbl">{short}</span></div>'
        )
    chart = f'<div class="bar-chart">{"".join(bars)}</div>'

    # stats
    days_with_activity = sum(1 for v in buckets.values() if v > 0)
    last_event = events[0].get("ts", "")[:16] if events else "—"
    last_event = _fmt_event_ts(last_event) if last_event else "—"

    # recent 3 excerpts (newest first) as a mini feed
    recent_excerpts = sorted(
        [e for e in excerpts if _ts_ms(e)],
        key=_ts_ms,
        reverse=True,
    )[:3]
    recent_rows = []
    for e in recent_excerpts:
        ts = _fmt_ts(e.get("createdTime"))
        ts_short = ts[11:] if ts else ""
        ts_day = ts[:10] if ts else ""
        content = e.get("content", "")
        if len(content) > 140:
            content = content[:140] + "…"
        idea = e.get("idea", "")
        bid = e.get("bookId")
        b = books.get(bid) or {}
        bname = b.get("name", f"#{bid}")
        bauthor = b.get("author", "")
        chap = e.get("chapter", "") or "未分章"
        cc = _char_count(e)
        eid = e.get("id", "")
        copy_text = (e.get("content", "") or "").replace("\\", "\\\\").replace("'", "\\'")
        recent_rows.append(
            f'<div class="tl-item">'
            f'  <div class="tl-time"><span class="h">{_e(ts_short)}</span><span class="d">{_e(ts_day)}</span></div>'
            f'  <div class="tl-body">'
            f'    <div class="tl-meta">'
            f'      {_source_badge(e)} '
            f'      <a href="/admin/books/{_e(bid)}"><b>{_e(bname)}</b></a>'
            f'      {("<span class=muted>· " + _e(bauthor) + "</span>") if bauthor else ""}'
            f'      · <b>{_e(chap)}</b> · {cc} 字 · <span class="id-hint">#{_e(eid)}</span>'
            f'    </div>'
            f'    <div class="tl-content">{_e(content)}</div>'
            f'    {f"<div class=tl-idea>— {_e(idea)}</div>" if idea else ""}'
            f'    <div class="tl-footer">'
            f'      <a class="btn-link" href="/admin/api/bookplate?id={_e(eid)}&style={_render.DEFAULT_BOOKPLATE_STYLE}" target="_blank" title="生成纸间书票 PNG">书票</a>'
            f'      <span style="flex:1"></span>'
            f'      <button class="btn-link" onclick="copyEx(this, \'{copy_text}\')">复制</button>'
            f'    </div>'
            f'  </div>'
            f'</div>'
        )
    recent_excerpts_block = (
        '<div class="timeline">' + "".join(recent_rows) + '</div>'
        if recent_rows else '<p class="muted">暂无摘录</p>'
    )

    # Page body
    body = f"""
    <div class="byline">最后事件 <b>{_e(last_event)}</b> · 统计于 {datetime.now().strftime("%Y-%m-%d %H:%M")}</div>

    <div class="cards">
      <div class="card">
        <div class="kicker-sm">书架</div>
        <div class="n">{len(books)}</div>
        <div class="l">累计收录</div>
      </div>
      <div class="card">
        <div class="kicker-sm">摘录</div>
        <div class="n">{len(excerpts)}<sup>条</sup></div>
        <div class="l">M5Paper <b>{n_m5}</b> · 合并 <b>{n_kv}</b></div>
      </div>
      <div class="card">
        <div class="kicker-sm">同步</div>
        <div class="n">{len(events)}</div>
        <div class="l">M5Paper + 合并 合并</div>
      </div>
      <div class="card">
        <div class="kicker-sm">活跃</div>
        <div class="n">{days_with_activity}<sup>/7</sup></div>
        <div class="l">近 7 天有新增的天数</div>
      </div>
    </div>

    <div class="kicker" style="margin-top:32px">7 日</div>
    {chart}

    <div class="kicker" style="margin-top:36px">最近摘录</div>
    {recent_excerpts_block}

    <div class="kicker" style="margin-top:36px">直达</div>
    <p class="row">
      <a class="btn" href="/admin/?pane=excerpts" data-pane-link="excerpts">浏览全部摘录</a>
      <a class="btn" href="/admin/?pane=books" data-pane-link="books">浏览书架</a>
      <a class="btn" href="/admin/?pane=events" data-pane-link="events">全部同步事件</a>
    </p>
    """
    return body


def _render_books(handler, data_dir: Path, q: str = "") -> str:
    books = _load_books(data_dir)
    excerpts = _load_excerpts(data_dir)
    # count per book
    counts: dict[int | str, int] = {}
    for e in excerpts:
        b = e.get("bookId")
        counts[b] = counts.get(b, 0) + 1

    rows = []
    for bid, b in sorted(books.items(), key=lambda kv: -counts.get(kv[0], 0)):
        name = b.get("name", "(无名)")
        author = b.get("author", "")
        cnt = counts.get(bid, 0)
        first_seen = b.get("_firstSeenDeck", "")
        src = "合并" if str(first_seen).startswith(("kavita-merge-", "merged-")) else "M5Paper"
        src_cls = "kv" if src == "合并" else "m5"
        if q and q.lower() not in name.lower() and q.lower() not in author.lower():
            continue
        rows.append(
            f'<tr>'
            f'<td class="cell-book"><a href="/admin/?pane=book&id={urllib.parse.quote(str(bid))}"'
            f' data-pane-target="book"><b>{_e(name)}</b></a>'
            f'<span class="sub">{_e(author) or "未署名"}</span></td>'
            f'<td class="num">{cnt}</td>'
            f'<td class="cell-src"><span class="src-badge {src_cls}">{_e(src)}</span></td>'
            f'<td class="cell-deck mono">{_e(first_seen)}</td>'
            f'</tr>'
        )
    search = (
        f'<form class="filter-bar" method="GET" action="/admin/" data-frag-pane="books">'
        f'<input type="hidden" name="pane" value="books">'
        f'<span class="lbl">书名 / 作者</span>'
        f'<input type="search" name="q" value="{_e(q)}" placeholder="输入关键词">'
        f'<button type="submit" class="submit-btn">搜索</button>'
        f'<a class="btn" href="/admin/?pane=books">清空</a>'
        f'</form>'
    )
    table = (
        f'<div class="table-wrap"><table class="bordered cols-books">'
        f'<colgroup><col class="col-grow"><col class="col-num"><col class="col-tag"><col class="col-deck"></colgroup>'
        f'<thead><tr><th>书名 / 作者</th><th class="num">摘录</th><th>来源</th><th>首次见</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
        if rows else '<p class="muted">无匹配</p>'
    )
    body = search + table
    return body


def _render_book_detail(handler, data_dir: Path, book_id: str) -> str:
    books = _load_books(data_dir)
    excerpts = _load_excerpts(data_dir)
    # bid type coercion
    try:
        bid = int(book_id)
    except ValueError:
        bid = book_id
    book = books.get(bid) or books.get(book_id) or {}
    name = book.get("name", f"未知 ({book_id})")
    author = book.get("author", "")
    first_seen = book.get("_firstSeenDeck", "")
    src = "合并" if str(first_seen).startswith(("kavita-merge-", "merged-")) else "M5Paper"
    src_cls = "kv" if src == "合并" else "m5"

    # group by year-month
    by_month: dict[str, list[dict]] = {}
    for e in excerpts:
        if e.get("bookId") != bid:
            continue
        ms = _ts_ms(e)
        if not ms:
            key = "未注明日期"
        else:
            dt = datetime.fromtimestamp(ms / 1000)
            key = f"{dt.year}-{dt.month:02d}"
        by_month.setdefault(key, []).append(e)

    timeline = []
    for month_key in sorted(by_month.keys(), reverse=True):
        items = sorted(by_month[month_key], key=_ts_ms, reverse=True)
        timeline.append(_month_header(month_key, len(items)))
        for e in items:
            ts = _fmt_ts(e.get("createdTime"))
            ts_short = ts[11:] if ts else ""
            ts_day = ts[:10] if ts else ""
            content = _highlight(e.get("content", ""), "")
            idea = e.get("idea", "")
            chap = e.get("chapter", "")
            eid = e.get("id", "")
            eid_str = f'<span class="id-hint">#{_e(eid)}</span>'
            copy_text = (e.get("content", "") or "").replace("'", "\\'")
            timeline.append(
                f'<div class="tl-item">'
                f'  <div class="tl-time"><span class="h">{_e(ts_short)}</span><span class="d">{_e(ts_day)}</span></div>'
                f'  <div class="tl-body">'
                f'    <div class="tl-meta">'
                f'      <span class="src-badge {src_cls}">{_e(src)}</span> '
                f'      <b>{_e(chap) or "未分章"}</b> · {_e(_char_count(e))} 字 · {eid_str}'
                f'    </div>'
                f'    <div class="tl-content">{content}</div>'
                f'    {f"<div class=tl-idea>— {_e(idea)}</div>" if idea else ""}'
                f'    <div class="tl-footer">'
                f'      <span class="sub">首次见 {_e(first_seen)}</span>'
                f'      <button class="btn-link" onclick="copyEx(this, \'{copy_text}\')">复制</button>'
                f'    </div>'
                f'  </div>'
                f'</div>'
            )

    body = (
        '<div><a href="/admin/?pane=books" data-pane-link="books" class="crumb">'
        '← 书架</a></div>'
        f'<h1>{_e(name)}</h1>'
        f'<div class="byline"><b>{_e(author) or "未知作者"}</b> · '
        f'<span class="src-badge {src_cls}">{_e(src)}</span> · '
        f'共 <b>{sum(len(v) for v in by_month.values())}</b> 条 · 首次见 {_e(first_seen)}</div>'
        + ('<div class="timeline">' + "".join(timeline) + '</div>' if timeline else '<p class="muted">本书暂无摘录</p>')
    )
    return body


def _render_excerpts(
    handler, data_dir: Path,
    q: str = "", book_id: str = "", book_q: str = "", author: str = "",
    day_from: str = "", day_to: str = "",
    page: int = 1, source: str = "all", chars: str = "all",
) -> str:
    books = _load_books(data_dir)
    excerpts = _load_excerpts(data_dir)
    filtered = _filter_excerpts(
        excerpts, q=q, book_id=book_id, book_q=book_q, author=author,
        day_from=day_from, day_to=day_to, books=books,
    )

    # Source filter (derived from _firstSeenDeck prefix)
    if source in ("m5", "kv"):
        want = "M5Paper" if source == "m5" else "合并"
        filtered = [e for e in filtered if _source_type(e) == want]

    # Char count filter
    if chars == "short":
        filtered = [e for e in filtered if _char_count(e) < 50]
    elif chars == "mid":
        filtered = [e for e in filtered if 50 <= _char_count(e) <= 200]
    elif chars == "long":
        filtered = [e for e in filtered if _char_count(e) > 200]

    filtered.sort(key=_ts_ms, reverse=True)

    per_page = 30
    page = max(1, page)
    total_pages = max(1, (len(filtered) + per_page - 1) // per_page)
    start = (page - 1) * per_page
    chunk = filtered[start : start + per_page]

    # Group by year-month for timeline
    by_month: dict[str, list[dict]] = {}
    for e in chunk:
        ms = _ts_ms(e)
        if not ms:
            key = "未注明日期"
        else:
            dt = datetime.fromtimestamp(ms / 1000)
            key = f"{dt.year}-{dt.month:02d}"
        by_month.setdefault(key, []).append(e)

    timeline = []
    for month_key in sorted(by_month.keys(), reverse=True):
        items = by_month[month_key]
        timeline.append(_month_header(month_key, len(items)))
        for e in items:
            ts = _fmt_ts(e.get("createdTime"))
            ts_short = ts[11:] if ts else ""
            ts_day = ts[:10] if ts else ""
            bid = e.get("bookId")
            b = books.get(bid) or {}
            bname = b.get("name", f"#{bid}")
            bauthor = b.get("author", "")
            chap = e.get("chapter", "")
            idea = e.get("idea", "")
            eid = e.get("id", "")
            cc = _char_count(e)
            content = _highlight(e.get("content", ""), q)
            copy_text = (e.get("content", "") or "").replace("\\", "\\\\").replace("'", "\\'")
            back = (
                f"/admin/?pane=excerpts&q={urllib.parse.quote(q)}"
                f"&book={urllib.parse.quote(book_id)}&book_q={urllib.parse.quote(book_q)}"
                f"&author={urllib.parse.quote(author)}&from={day_from}&to={day_to}"
                f"&page={page}&source={source}&chars={chars}"
            )
            timeline.append(
                f'<div class="tl-item">'
                f'  <input type="checkbox" class="excerpt-check" value="{_e(eid)}">'
                f'  <div class="tl-time"><span class="h">{_e(ts_short)}</span><span class="d">{_e(ts_day)}</span></div>'
                f'  <div class="tl-body">'
                f'    <div class="tl-meta">'
                f'      {_source_badge(e)} '
                f'      <a href="/admin/?pane=book&id={urllib.parse.quote(str(bid))}"'
                f'         data-pane-target="book"><b>{_e(bname)}</b></a>'
                f'      {("<span class=muted>· " + _e(bauthor) + "</span>") if bauthor else ""}'
                f'      · <b>{_e(chap) or "未分章"}</b> · {cc} 字 · <span class="id-hint">#{_e(eid)}</span>'
                f'    </div>'
                f'    <div class="tl-content">{content}</div>'
                f'    {f"<div class=tl-idea>— {_e(idea)}</div>" if idea else ""}'
                f'    <div class="tl-footer">'
                f'      <a class="btn-link" href="/admin/api/bookplate?id={_e(eid)}&style={_render.DEFAULT_BOOKPLATE_STYLE}" target="_blank" title="生成纸间书票 PNG (skill 默认风格)">书票</a>'
                f'      <span style="flex:1"></span>'
                f'      <button class="btn-link" onclick="copyEx(this, \'{copy_text}\')">复制</button>'
                f'      <a class="btn-link" href="/admin/api/delete?id={_e(eid)}&back={urllib.parse.quote(back)}" '
                f'         onclick="return confirm(\'确认删除这条摘录？\')">删除</a>'
                f'    </div>'
                f'  </div>'
                f'</div>'
            )

    # Filter chips: source
    def src_chip(key, label):
        cls = "chip" + (" active" if source == key else "")
        if key == "kv":
            cls = "chip kv" + (" active" if source == key else "")
        params = _qs_replace(pane="excerpts", q=q, book=book_id, day_from=day_from,
                             day_to=day_to, source=key, chars=chars)
        return f'<a class="{cls}" href="/admin/?{params}">{label}</a>'

    def chars_chip(key, label):
        cls = "chip" + (" active" if chars == key else "")
        params = _qs_replace(pane="excerpts", q=q, book=book_id, day_from=day_from,
                             day_to=day_to, source=source, chars=key)
        return f'<a class="{cls}" href="/admin/?{params}">{label}</a>'

    # book dropdown 取消 — 改文本输入 + 模糊匹配
    # (保留精确 book_id 通道走 ?book=1379 这种链接)

    search = (
        '<form class="filter-bar" method="GET" action="/admin/" data-frag-pane="excerpts">'
        f'<input type="hidden" name="pane" value="excerpts">'
        f'<span class="lbl">来源</span>'
        f'{src_chip("all", "全部")}'
        f'{src_chip("m5", "M5Paper")}'
        f'{src_chip("kv", "合并")}'
        f'<span class="lbl" style="margin-left:8px">字数</span>'
        f'{chars_chip("all", "全部")}'
        f'{chars_chip("short", "短 <50")}'
        f'{chars_chip("mid", "中 50-200")}'
        f'{chars_chip("long", "长 >200")}'
        f'<span class="spacer"></span>'
        f'<input type="search" name="q" value="{_e(q)}" placeholder="搜文字 / 批注" title="摘录 / 批注 substring">'
        f'<input type="search" name="book_q" value="{_e(book_q)}" placeholder="书名 (模糊)" title="书名 substring 匹配, 留空 = 全部" style="width:150px">'
        f'<input type="search" name="author" value="{_e(author)}" placeholder="作者 (模糊)" title="作者 substring 匹配, 留空 = 全部" style="width:130px">'
        f'<input type="date" name="from" value="{_e(day_from)}" title="起始日期">'
        f'<input type="date" name="to" value="{_e(day_to)}" title="结束日期">'
        f'<input type="hidden" name="source" value="{_e(source)}">'
        f'<input type="hidden" name="chars" value="{_e(chars)}">'
        f'<input type="hidden" name="book" value="{_e(book_id)}">'
        f'<button type="submit" class="submit-btn">筛选</button>'
        f'<a class="btn" href="/admin/?pane=excerpts">清空</a>'
        f'</form>'
    )

    back_qs = _qs_replace(pane="excerpts", q=q, book=book_id, book_q=book_q, author=author,
                          day_from=day_from, day_to=day_to, source=source, chars=chars)
    pager = (
        f'<div class="pager">'
        f'  <span class="meta">第 {page} / {total_pages} 页 · 共 <b>{len(filtered)}</b> 条命中</span>'
        f'  <span>'
        + (f'<a href="/admin/?{_qs_replace(pane="excerpts", page=page-1, q=q, book=book_id, book_q=book_q, author=author, day_from=day_from, day_to=day_to, source=source, chars=chars)}">‹ 上一页</a> · ' if page > 1 else "")
        + (f'<a href="/admin/?{_qs_replace(pane="excerpts", page=page+1, q=q, book=book_id, book_q=book_q, author=author, day_from=day_from, day_to=day_to, source=source, chars=chars)}">下一页 ›</a> · ' if page < total_pages else "")
        + f'<a class="btn" href="/admin/api/excerpts.json?{back_qs}">导出 JSON</a>'
        f'  </span>'
        f'</div>'
    )

    bulk_bar = (
        f'<div class="bulk-bar" id="bulkBar">'
        f'  <span>已选</span>'
        f'  <span class="n" id="selCount">0</span>'
        f'  <span>条</span>'
        f'  <form id="bulkForm" method="POST" action="/admin/api/delete" style="display:inline">'
        f'    <input type="hidden" name="ids" id="bulkIds" value="">'
        f'    <input type="hidden" name="back" value="/admin/?{back_qs}">'
        f'    <button type="submit">批量删除</button>'
        f'  </form>'
        f'  <span class="spacer" style="flex:1"></span>'
        f'  <label style="font-size:11px"><input type="checkbox" id="selAll" style="margin-right:4px">全选当前页</label>'
        f'  <button type="button" id="selNone" class="ghost">清空选择</button>'
        f'</div>'
    )

    timeline_html = (
        '<div class="timeline">' + "".join(timeline) + '</div>'
        if timeline else '<p class="muted">无匹配</p>'
    )

    body = search + bulk_bar + pager + timeline_html + pager
    return body


def _qs_replace(**kwargs) -> str:
    """Build a query string with the given values, dropping empty ones."""
    items = []
    for k, v in kwargs.items():
        if v is None or v == "":
            continue
        items.append(f"{k}={urllib.parse.quote(str(v))}")
    return "&".join(items)


def _render_events(handler, data_dir: Path) -> str:
    events = _load_events(data_dir, limit=500)
    rows = []
    for ev in events:
        kind = ev.get("kind", "")
        ts = _fmt_event_ts(ev.get("ts", ""))
        bc = ev.get("bookCount", 0)
        ec = ev.get("excerptCount", 0)
        bk = ev.get("batchCount", 0)
        deck = ev.get("deckDir", "")
        sid = ev.get("sessionId", "")
        ev_src = "合并" if str(deck).startswith(("kavita-merge-", "merged-")) else "M5Paper"
        ev_cls = "kv" if ev_src == "合并" else "m5"
        rows.append(
            f'<tr>'
            f'<td class="cell-ts"><b>{_e(ts)}</b>'
            f'  {f"<span class=sub>{_e(sid)}</span>" if sid else ""}</td>'
            f'<td class="cell-src"><span class="src-badge {ev_cls}">{_e(ev_src)}</span>'
            f'  <span class="kind">{_e(kind)}</span></td>'
            f'<td class="num">{_e(bc)} 本 · {_e(ec)} 条 · {_e(bk)} 批</td>'
            f'<td class="cell-deck mono">{_e(deck)}</td>'
            f'</tr>'
        )
    table = (
        f'<div class="table-wrap"><table class="bordered cols-events">'
        f'<colgroup><col class="col-ts"><col class="col-tag"><col class="col-num"><col class="col-deck"></colgroup>'
        f'<thead><tr><th>时间 / 会话</th><th>来源 · 类型</th><th class="num">规模</th><th>落盘</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
        if rows else '<p class="muted">暂无事件</p>'
    )
    body = table
    return body


# ---------- Tools page ----------

TOOL_CSS = """
/* 视觉交给 assets/recto.css; 这里只留行为相关的 */
.tools-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr));gap:var(--rc-gap)}
.tool-card.wide{grid-column:1/-1}
.tool-card .btn-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:12px}
.kt-row{cursor:pointer}
.kt-detail td{background:var(--rc-sunk);font-family:var(--rc-mono);font-size:var(--rc-fs-xs)}
"""


def _render_tools(handler, data_dir: Path, flash: str = "") -> str:
    excerpts = _load_excerpts(data_dir)
    books = _load_books(data_dir)
    kv_count = sum(1 for e in excerpts if _source_type(e) == "合并")
    m5_count = sum(1 for e in excerpts if _source_type(e) == "M5Paper")
    books_list = [b for b in books.values() if b.get("name")]

    flash_html = (
        f'<div class="out ok">{_e(flash)}</div>' if flash else ""
    )

    book_opts = ['<option value="">— 不限,导所有 —</option>']
    for b in sorted(books_list, key=lambda x: x.get("name", ""))[:80]:
        book_opts.append(
            f'<option value="{_e(b.get("name",""))}">{_e(b.get("name",""))[:60]}</option>'
        )

    # Author select (from books.jsonl)
    author_opts = ['<option value="">— 不限 —</option>']
    seen_authors: set[str] = set()
    for b in sorted(books_list, key=lambda x: x.get("author", "")):
        a = (b.get("author") or "").strip()
        if a and a not in seen_authors:
            seen_authors.add(a)
            author_opts.append(f'<option value="{_e(a)}">{_e(a)[:60]}</option>')

    card_zhijian = (
        '<div class="tool-card">'
        '  <h3>导出到纸间 (xmnote)</h3>'
        '  <div class="cors-warn" id="zh-cors-warn" style="display:none">'
        '    <b>⚠ 浏览器 CORS 必 block</b><br>'
        '    当前 origin 是公网域名 (<code id="zh-cors-origin"></code>),'
        '    浏览器不允许从公网页面 fetch 私网 IP,推送会失败并报 "more-private address space local"。<br>'
        '    <b>修法</b>: PC 浏览器地址栏改成 <code>http://&lt;服务器私网 IP&gt;:5015</code> 直接访问 (需 server 跟 PC 同一 / 互通网段)。<br>'
        '    例: 服务器在 <code>192.168.1.10</code>,PC 浏览器填 <code>http://192.168.1.10:5015</code>。'
        '  </div>'
        '  <form id="zhijian-form">'
        '    <label>设备 IP : 端口 (浏览器 localStorage 记住, 每次自动填回)</label>'
        '    <div style="display:flex;gap:8px">'
        '      <input type="text" id="zh-host" name="host" placeholder="192.168.1.20" style="flex:3">'
        '      <input type="text" id="zh-port" name="port" placeholder="8080" value="8080" style="flex:1">'
        '    </div>'
        '    <label>书名 (留空 = 全部, 模糊匹配)</label>'
        '    <input type="text" name="book_name" list="zh-book-list" placeholder="例: 1Q84">'
        f'   <datalist id="zh-book-list">{"".join(book_opts)}</datalist>'
        '    <label>作者 (留空 = 全部, 模糊匹配)</label>'
        '    <input type="text" name="author" list="zh-author-list" placeholder="例: 村上春树">'
        f'   <datalist id="zh-author-list">{"".join(author_opts)}</datalist>'
        '    <label>只导出来源</label>'
        '    <select name="source" style="width:100%;padding:7px;border:1px solid var(--rule);font:13px var(--sans);background:var(--paper)">'
        '      <option value="all">全部</option>'
        '      <option value="m5">M5Paper</option>'
        '    </select>'
        '    <label>时间窗 (createdTime 倒序, 留空 = 全部)</label>'
        '    <div style="display:flex;gap:8px">'
        '      <input type="date" name="day_from" placeholder="从" style="flex:1">'
        '      <input type="date" name="day_to" placeholder="至" style="flex:1">'
        '    </div>'
        '    <label>摘录文本搜索 (content / idea, 留空 = 全部)</label>'
        '    <input type="text" name="q" placeholder="例: 风 / 月亮 / 桥">'
        '    <label>最多推送 (安全上限, 命中更多就先收紧筛选)</label>'
        f'    <input type="number" name="safety_cap" min="0" step="1" value="{len(excerpts) + 50}" '
        '           title="0 = 不限">'
        '    <label style="display:flex;align-items:center;gap:6px;margin:2px 0 6px">'
        '      <input type="checkbox" name="include_time" value="1" checked style="width:auto;margin:0">'
        '      <span>附带阅读时长 (<code>preciseReadingDurations</code>, 随书一起进纸间)</span>'
        '    </label>'
        '    <div class="btn-row">'
        '      <button type="submit">推送 (浏览器转发)</button>'
        '      <span style="color:var(--muted);font:11px var(--sans)" id="zh-status">就绪</span>'
        '    </div>'
        '    <div id="zh-log" class="out" style="display:none"></div>'
        '  </form>'
        '  <div class="note" style="font:11px var(--sans);color:var(--muted);margin-top:6px;line-height:1.5">'
        '    阅读时长 = 本阅读器的会话区间 + 掌阅云端每日分钟 (没有时段, 补成 19:00–23:59 的一段)。'
        '书库里没有这本书 (或从未读过) 就不带时长。'
        '  </div>'
        '</div>'
        '<script>'
        # CORS 警告: 检测 origin 是不是公网域名,是则显示横幅
        '(function(){'
        '  var h=location.hostname;'
        '  function isPrivate(x){'
        '    if(x==="localhost"||x==="127.0.0.1"||x==="::1"||x==="[::1]")return true;'
        '    if(/^10\./.test(x))return true;'
        '    if(/^192\.168\./.test(x))return true;'
        '    if(/^172\.(1[6-9]|2[0-9]|3[01])\./.test(x))return true;'
        '    if(/^\[?fd[0-9a-f]{2}:/i.test(x))return true;'
        '    if(/^\[?fe80:/i.test(x))return true;'
        '    return false;'
        '  }'
        '  if(!isPrivate(h)){'
        '    var w=document.getElementById("zh-cors-warn");'
        '    if(w){document.getElementById("zh-cors-origin").textContent=location.protocol+"//"+location.host;w.style.display="block";}'
        '  }'
        '})();'
        # 设备 IP 持久化 (localStorage)
        '(function(){'
        f'  var KEY_H="{legacy.NEW_JS_HOST_KEY}",KEY_P="{legacy.NEW_JS_PORT_KEY}";'
        f'  var OLD_H="{legacy.LEGACY_JS_HOST_KEY}",OLD_P="{legacy.LEGACY_JS_PORT_KEY}";'
        '  try{var h=localStorage.getItem(KEY_H)||localStorage.getItem(OLD_H),'
        'p=localStorage.getItem(KEY_P)||localStorage.getItem(OLD_P);'
        '    if(h)document.getElementById("zh-host").value=h;'
        '    if(p)document.getElementById("zh-port").value=p;'
        '  }catch(e){}'
        '  document.getElementById("zh-host").addEventListener("change",function(e){'
        '    try{localStorage.setItem(KEY_H,e.target.value);}catch(_){}'
        '  });'
        '  document.getElementById("zh-port").addEventListener("change",function(e){'
        '    try{localStorage.setItem(KEY_P,e.target.value);}catch(_){}'
        '  });'
        # 提交:fetch server 拿 payload, 浏览器 fetch 推手机
        '  document.getElementById("zhijian-form").addEventListener("submit",async function(ev){'
        '    ev.preventDefault();'
        '    var f=ev.target,status=document.getElementById("zh-status"),log=document.getElementById("zh-log");'
        '    var fd=new FormData(f);'
        '    if(!fd.get("include_time"))fd.set("include_time","0");'
        '    var host=(fd.get("host")||"").toString().trim();'
        '    var port=(fd.get("port")||"8080").toString().trim();'
        '    if(!host){status.textContent="请填手机 IP";return;}'
        '    status.textContent="拉取 payload...";'
        '    log.style.display="block";log.textContent="";log.className="out";'
        '    var r=await fetch("/admin/api/export-zhijian",{method:"POST",body:fd});'
        '    var j=await r.json().catch(function(_){return {ok:false,error:"JSON parse failed"};});'
        '    if(!j.ok){status.textContent="✗ "+j.error;log.textContent=JSON.stringify(j,null,2);log.className="out err";return;}'
        '    status.textContent="共 "+j.total+" 条 / "+j.book_count+" 本"+(j.time_minutes?(" / 时长 "+j.time_minutes+" 分钟"):"")+", 开始推送...";'
        # 逐本推, 串行, 实时回显
        '    function withTimeout(p,ms,label){'
        '      return Promise.race([p,new Promise(function(_,rej){setTimeout(function(){rej(new Error(label+" 超时 "+ms+"ms"));},ms);})]);'
        '    }'
        '    var sent=0,failed=[];'
        '    for(var i=0;i<j.payloads.length;i++){'
        '      var p=j.payloads[i];'
        '      var line="▸ 《"+p.title+"》"+p.entries.length+" 条"+(p.timeMinutes?(" + "+p.timeMinutes+" 分钟/"+(p.preciseReadingDurations||[]).length+" 段时长"):"")+"…";'
        '      log.textContent+=line+"\\n";'
        '      var bodyObj={title:p.title,author:p.author,type:p.type||1,locationUnit:p.locationUnit||0,entries:p.entries};'
        '      if(p.preciseReadingDurations&&p.preciseReadingDurations.length)bodyObj.preciseReadingDurations=p.preciseReadingDurations;'
        '      var txt=JSON.stringify(bodyObj);'
        '      var okone=false,how="";'
        # ① 先由 server 代发: 浏览器跨源 POST JSON 会先发 OPTIONS 预检, 纸间 App
        #    不答预检时 fetch 既不报错也不返回 (挂住) —— 所以不能把它当主路径。
        '      try{'
        '        var fd2=new FormData();fd2.append("host",host);fd2.append("port",port);fd2.append("payload",txt);'
        '        var r2=await withTimeout(fetch("/admin/api/zhijian-send",{method:"POST",body:fd2}),30000,"服务器代发");'
        '        var j2=await r2.json().catch(function(_){return null;});'
        '        if(j2 && j2.ok){okone=true;how="服务器代发";}'
        '        else{log.textContent+="  · 服务器代发不通 ("+((j2&&(j2.error||j2.body))||r2.status)+"), 改走浏览器直连\\n";}'
        '      }catch(e){log.textContent+="  · 服务器代发异常 ("+e+"), 改走浏览器直连\\n";}'
        # ② 兜底: 浏览器直连手机 (server 与手机不在同一网段时唯一可行路径)
        '      if(!okone){'
        '        try{'
        '          var rr=await withTimeout(fetch("http://"+host+":"+port+"/send",{method:"POST",headers:{"Content-Type":"application/json"},body:txt}),8000,"直连");'
        '          var t=await rr.text().catch(function(_){return "";});'
        '          var jr=null;try{jr=JSON.parse(t);}catch(_){}'
        '          if(rr.ok && jr && jr.code===200){okone=true;how="浏览器直连";}'
        '          else{failed.push({title:p.title,status:rr.status,code:jr&&jr.code,msg:jr&&jr.message,body:t.slice(0,200)});log.textContent+="  ✗ 直连 HTTP "+rr.status+(jr?(" code="+jr.code+" "+(jr.message||"")):t.slice(0,200))+"\\n";}'
        '        }catch(e2){failed.push({title:p.title,err:String(e2)});log.textContent+="  ✗ 直连失败 "+e2+"\\n";}'
        '      }'
        '      if(okone){sent+=p.entries.length;log.textContent+="  ✓ "+how+"成功\\n";}'
        # 第一本两条路都不通 = 设备不在线 (App 被系统冻结时端口能连但不应答)。
        # 继续往下试只会每本各等 20s, 不如立刻停, 并告诉用户怎么办。
        '      if(!okone && sent===0){log.textContent+="\\n设备无响应, 已停止后续推送。请让手机上的阅读 App 回到前台 (亮屏) 后再试。\\n";log.scrollTop=log.scrollHeight;break;}'
        '      log.scrollTop=log.scrollHeight;'
        '    }'
        '    var summary="\\n=== 完成 ===\\n共推 "+sent+" 条 / "+j.payloads.length+" 本";'
        '    if(failed.length)summary+="\\n失败 "+failed.length+" 本:\\n"+failed.map(function(f){return"  · "+f.title+(f.status?" ["+f.status+"]":"")+(f.err?" ("+f.err+")":"")+(f.body?"\\n    "+f.body:"");}).join("\\n");'
        '    log.textContent+=summary;log.scrollTop=log.scrollHeight;'
        '    status.textContent=failed.length?"✗ "+failed.length+" 本失败":"✓ "+sent+" 条已推";'
        '    log.className=failed.length?"out err":"out ok";'
        '  });'
        '})();'
        '</script>'
    )


    body = (
        '<style>' + TOOL_CSS + '</style>'
        + flash_html
        + f'<div class="tools-grid">{card_zhijian}</div>'
    )
    return body


# ---------- 单页多面板 (页面内标签, 不再有子页面) ----------
#
# 所有视图都是同一个页面上的面板: 切标签只切 DOM, 取数据走 /admin/pane/<key>
# (返回面板内部 HTML, 不含外壳)。没有 JS 时退化成 /admin/?pane=<key> 整页请求,
# 行为一致, 只是会刷新一次。

PANES: list[tuple[str, str]] = [
    ("home", "首页"),
    ("excerpts", "摘录"),
    ("books", "书"),
    ("events", "事件"),
    ("tools", "工具"),
]
PANE_LABELS = dict(PANES)
PANE_LABELS["book"] = "书"
ALL_PANES = [k for k, _ in PANES] + ["book"]


def _pane_params(qs: dict | None) -> dict:
    """把查询串收敛成面板参数 (字符串都在这里 strip 一次)。"""
    qs = qs or {}

    def one(key: str, default: str = "") -> str:
        v = qs.get(key, [default])
        v = v[0] if isinstance(v, list) and v else (v or default)
        return str(v).strip()

    try:
        page = int(one("page", "1") or 1)
    except ValueError:
        page = 1
    return {
        "q": one("q"),
        "book_id": one("book"),
        "book_q": one("book_q"),
        "author": one("author"),
        "day_from": one("from"),
        "day_to": one("to"),
        "page": max(1, page),
        "source": one("source", "all") or "all",
        "chars": one("chars", "all") or "all",
        "id": one("id"),
        "flash": one("flash"),
    }


def _pane_book_placeholder() -> str:
    return (
        '<p class="muted">从书架里点一本书，它的摘录会在这里展开。</p>'
        '<p class="row"><a class="btn" href="/admin/?pane=books" data-pane-link="books">去书架挑一本</a></p>'
    )


def _pane_html(key: str, handler, data_dir: Path, p: dict) -> str:
    if key == "excerpts":
        return _render_excerpts(
            handler, data_dir,
            q=p["q"], book_id=p["book_id"], book_q=p["book_q"], author=p["author"],
            day_from=p["day_from"], day_to=p["day_to"], page=p["page"],
            source=p["source"], chars=p["chars"],
        )
    if key == "books":
        return _render_books(handler, data_dir, p["q"])
    if key == "book":
        bid = p["id"] or p["book_id"]
        if not bid:
            return _pane_book_placeholder()
        return _render_book_detail(handler, data_dir, bid)
    if key == "events":
        return _render_events(handler, data_dir)
    if key == "tools":
        return _render_tools(handler, data_dir, p["flash"])
    return _render_home(handler, data_dir)


def _render_admin_page(handler, data_dir: Path, pane: str = "home", params: dict | None = None) -> str:
    """整个摘录后台 = 一页 + 若干面板。当前面板立刻渲染, 其余按需懒加载。"""
    from recto.app.ui import shell as _shell

    p = params or _pane_params({})
    if pane not in ALL_PANES:
        pane = "home"
    if p.get("book_id") and pane in ("home", "book"):
        # 兼容老链接 /admin/?book=1379 (从摘要点书名)
        pane = "book"
        p["id"] = p["id"] or p["book_id"]

    sections = []
    for key in ALL_PANES:
        label = PANE_LABELS.get(key, key)
        if key == pane:
            inner = _pane_html(key, handler, data_dir, p)
            loaded = ' data-pane-loaded="1"'
        else:
            inner, loaded = "", ""
        sections.append(
            f'<section class="pane-sec" data-pane="{_e(key)}" '
            f'data-pane-src="/admin/pane/{_e(key)}"{loaded} aria-label="{_e(label)}">{inner}</section>'
        )

    user = None
    try:
        user = handler._user()
    except Exception:  # noqa: BLE001 — 独立运行时不一定有会话
        user = None

    title = PANE_LABELS.get(pane, "摘录")
    return _shell.document(
        title=title,
        body="".join(sections),
        active="excerpts",
        user=user,
        show_subnav=True,
        sub_active=pane,
        pane_start=pane,
        head_extra=f"<style>{_EXTRA_CSS}</style>{_BULK_JS}{_PANE_JS}",
    )


# 面板内的片段导航: 点书 → 书面板; 翻页 / 筛选 / 搜索 → 只换当前面板。
# 没有它也能用 (链接是 /admin/?pane=... 整页请求), 有它则不刷新。
_PANE_JS = r"""
<script>
(function () {
  function paneOf(el) {
    var s = el && el.closest ? el.closest('[data-pane]') : null;
    return s ? s.dataset.pane : '';
  }
  function sec(name) { return document.querySelector('[data-pane="' + name + '"]'); }
  function fragUrl(pane, search) {
    var q = new URLSearchParams(search || '');
    q.delete('pane');
    var s = q.toString();
    return '/admin/pane/' + encodeURIComponent(pane) + (s ? '?' + s : '');
  }
  function apply(pane, href) {
    var s = sec(pane);
    if (!s) return;
    var u = new URL(href, location.origin);
    var url = fragUrl(pane, u.search);
    if (s.dataset.paneBusy === '1') return;
    s.dataset.paneBusy = '1';
    fetch(url, { credentials: 'same-origin' })
      .then(function (r) { return r.text(); })
      .then(function (h) {
        s.innerHTML = h || '';
        s.dataset.paneLoaded = '1';
        s.dataset.paneSrc = url;
        if (window.__recto && window.__recto.injectScripts) window.__recto.injectScripts(s);
        try { history.replaceState(null, '', u.pathname + u.search); } catch (e) {}
        if (window.__recto && window.__recto.showPane) window.__recto.showPane(pane, { silent: true });
        var top = s.getBoundingClientRect().top + window.scrollY - 70;
        window.scrollTo({ top: top < 0 ? 0 : top, behavior: 'auto' });
      })
      .catch(function () { location.href = href; })
      .then(function () { s.dataset.paneBusy = ''; });
  }

  document.addEventListener('click', function (ev) {
    if (ev.defaultPrevented) return;
    if (ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
    var a = ev.target.closest ? ev.target.closest('a[href]') : null;
    if (!a || a.target === '_blank' || a.hasAttribute('data-pane-link')) return;
    var u;
    try { u = new URL(a.href, location.origin); } catch (e) { return; }
    if (u.origin !== location.origin) return;
    if (u.pathname.indexOf('/admin/') !== 0 || u.pathname.indexOf('/admin/api/') === 0) return;
    if (u.pathname.indexOf('/admin/pane/') === 0) return;
    var target = a.dataset.paneTarget || u.searchParams.get('pane') || paneOf(a) || 'home';
    ev.preventDefault();
    apply(target, a.href);
  }, false);

  document.addEventListener('submit', function (ev) {
    var f = ev.target;
    if (!f || f.tagName !== 'FORM') return;
    var act = f.getAttribute('action') || '';
    if (act.indexOf('/admin/') !== 0 || act.indexOf('/admin/api/') === 0) return;
    if ((f.getAttribute('method') || 'get').toLowerCase() !== 'get') return;
    ev.preventDefault();
    var fd = new FormData(f);
    var q = new URLSearchParams();
    fd.forEach(function (v, k) { if (v !== '') q.append(k, v); });
    var pane = f.dataset.fragPane || q.get('pane') || paneOf(f) || 'home';
    q.delete('pane');
    var s = q.toString();
    apply(pane, '/admin/?pane=' + encodeURIComponent(pane) + (s ? '&' + s : ''));
  }, true);
})();
</script>
"""



def _send_html(handler, body: str, status: int = 200) -> None:
    data = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)


def _send_redirect(handler, location: str) -> None:
    handler.send_response(302)
    handler.send_header("Location", location)
    handler.send_header("Content-Length", "0")
    handler.end_headers()


ZHIJIAN_TIMEOUT = 12.0


def _post_json(url: str, obj: dict, timeout: float = ZHIJIAN_TIMEOUT) -> tuple[int, str, str | None]:
    """POST 一个 JSON 到设备。返回 (status, body_text, error)。

    server 直推存在的唯一理由: 浏览器从公网页面不能 fetch 私网 IP。
    """
    import urllib.error
    import urllib.request

    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), None
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            pass
        return exc.code, body, None
    except Exception as exc:
        return 0, "", f"{type(exc).__name__}: {exc}"


def _send_json(handler, obj: Any, status: int = 200) -> None:
    data = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _read_form(handler) -> dict[str, str]:
    length = int(handler.headers.get("Content-Length", 0) or 0)
    if not length:
        return {}
    ctype = (handler.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    body = handler.rfile.read(length)
    if ctype == "multipart/form-data":
        return _parse_multipart(body, handler.headers.get("Content-Type", ""))
    if ctype == "application/json":
        # 2026-10-25: JSON body (阅读时长导入用) —— 顶层每个 key 拍平成字符串值,
        # 复杂对象保留原对象 (调用方自己判断类型)。
        try:
            obj = json.loads(body.decode("utf-8", errors="replace") or "{}")
        except Exception:
            return {}
        if not isinstance(obj, dict):
            return {}
        out: dict[str, str] = {}
        for k, v in obj.items():
            out[k] = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
            if isinstance(v, bool):
                out[k] = "1" if v else "0"
        return out
    text = body.decode("utf-8", errors="replace")
    return {k: v[0] for k, v in urllib.parse.parse_qs(text).items()}


def _parse_multipart(body: bytes, content_type: str) -> dict[str, str]:
    """Tiny multipart/form-data parser — text fields only (no file uploads).

    Avoids cgi.FieldStorage which is deprecated since 3.11 and behaves
    erratically when constructed manually outside a real WSGI/HTTP request.
    """
    # boundary
    boundary = None
    for part in content_type.split(";"):
        part = part.strip()
        if part.lower().startswith("boundary="):
            boundary = part.split("=", 1)[1].strip().strip('"')
            break
    if not boundary:
        return {}
    delim = ("--" + boundary).encode("ascii")
    out: dict[str, str] = {}
    # split on boundary; skip preamble (before first) and epilogue (after last --)
    chunks = body.split(delim)
    for chunk in chunks:
        chunk = chunk.strip(b"\r\n")
        if not chunk or chunk == b"--":
            continue
        # split headers from body
        if b"\r\n\r\n" not in chunk:
            continue
        head, _, val = chunk.partition(b"\r\n\r\n")
        # parse Content-Disposition: form-data; name="host"
        name = None
        for h in head.split(b"\r\n"):
            hl = h.decode("latin-1", errors="replace").lower()
            if hl.startswith("content-disposition"):
                for attr in hl.split(";"):
                    attr = attr.strip()
                    if attr.startswith("name="):
                        name = attr.split("=", 1)[1].strip().strip('"')
                        break
                break
        if not name:
            continue
        # strip trailing \r\n before next boundary
        if val.endswith(b"\r\n"):
            val = val[:-2]
        out[name] = val.decode("utf-8", errors="replace")
    return out


def handle_admin(handler, data_dir: Path, path: str, query: str) -> bool:
    """Mount handler. Returns True if request was handled, False to fall through."""
    if not path.startswith("/admin"):
        return False

    # dev hot-reload: importlib.reload so live-pickup of code edits.
    # Path-gated to localhost only — do not expose on LAN/prod.
    if path == "/admin/api/reload" and handler.client_address[0] in ("127.0.0.1", "::1"):
        from recto.app import excerpts as _au
        importlib.reload(_au)
        # 兄弟模块也一起重载, 否则 import 缓存里的旧实现继续生效
        reloaded = ["recto.app.excerpts"]
        for _name in ("recto.services.notes", "recto.services.sync",
                      "recto.services.sessions", "recto.services.reader_state"):
            _mod = sys.modules.get(_name)
            if _mod is not None:
                try:
                    importlib.reload(_mod)
                    reloaded.append(_name)
                except Exception:
                    pass
        _send_json(handler, {"reloaded": reloaded, "ok": True})
        return True

    qs = urllib.parse.parse_qs(query or "")
    q = (qs.get("q", [""])[0] or "").strip()
    book_id = (qs.get("book", [""])[0] or "").strip()
    book_q = (qs.get("book_q", [""])[0] or "").strip()
    author = (qs.get("author", [""])[0] or "").strip()
    day_from = (qs.get("from", [""])[0] or "").strip()
    day_to = (qs.get("to", [""])[0] or "").strip()
    source = (qs.get("source", ["all"])[0] or "all").strip()
    chars = (qs.get("chars", ["all"])[0] or "all").strip()
    try:
        page = int(qs.get("page", ["1"])[0] or 1)
    except ValueError:
        page = 1

    # /admin/login - GET + POST
    if path == "/admin/login":
        if handler.command == "GET":
            _send_html(handler, _render_login(handler))
            return True
        # POST
        pw_input = _read_form(handler).get("password", "")
        pw = _get_password(handler)
        if not pw or not hmac.compare_digest(pw_input, pw):
            _send_html(handler, _render_login(handler, "密码错"), status=401)
            return True
        cookie = SimpleCookie()
        cookie[_COOKIE_NAME] = _make_session_cookie(pw)
        cookie[_COOKIE_NAME]["path"] = _COOKIE_PATH
        cookie[_COOKIE_NAME]["httponly"] = True
        cookie[_COOKIE_NAME]["max-age"] = _SESSION_TTL
        cookie[_COOKIE_NAME]["samesite"] = "Strict"
        handler.send_response(302)
        handler.send_header("Set-Cookie", cookie.output(header="").strip())
        handler.send_header("Location", "/admin/")
        handler.send_header("Content-Length", "0")
        handler.end_headers()
        return True

    # /admin/logout
    if path == "/admin/logout":
        cookie = SimpleCookie()
        cookie[_COOKIE_NAME] = ""
        cookie[_COOKIE_NAME]["path"] = _COOKIE_PATH
        cookie[_COOKIE_NAME]["max-age"] = 0
        handler.send_response(302)
        handler.send_header("Set-Cookie", cookie.output(header="").strip())
        handler.send_header("Location", "/admin/login")
        handler.send_header("Content-Length", "0")
        handler.end_headers()
        return True

    # auth gate
    # 合并进 Recto 单进程后, 外层 (recto.app.server) 已按统一会话判过一次;
    # 它放行时会打 `_admin_ok` 标记, 这里就不再拿老密码重判 —— 否则会出现
    # "控制台进得去、摘录一直被弹回登录页" 的割裂感。
    if not getattr(handler, "_admin_ok", False) and not _is_authed(handler):
        _send_redirect(handler, "/login?next=/admin/")
        return True

    # ---- 单页多面板: 所有视图都在 /admin/ 这一页上, 用面板标签切换 ----
    pane_params = _pane_params(qs)

    if path in ("/admin/", "/admin"):
        pane = (qs.get("pane", ["home"])[0] or "home").strip() or "home"
        _send_html(handler, _render_admin_page(handler, data_dir, pane, pane_params))
        return True

    # 面板片段 (前端换面板 / 翻页 / 筛选时取, 返回面板内部 HTML)
    if path.startswith("/admin/pane/"):
        pane = path[len("/admin/pane/"):].strip("/")
        if pane not in ALL_PANES:
            _send_html(handler, '<p class="muted">面板不存在</p>', status=404)
            return True
        _send_html(handler, _pane_html(pane, handler, data_dir, pane_params))
        return True

    # 老 URL 一律跳到同一页的对应面板 —— 外部书签 / 旧日志链接不许断
    _legacy = {
        "/admin/books": "books",
        "/admin/excerpts": "excerpts",
        "/admin/events": "events",
        "/admin/tools": "tools",
    }
    if path in _legacy:
        keep = {k: v[0] for k, v in qs.items() if v and v[0]}
        keep["pane"] = _legacy[path]
        _send_redirect(handler, "/admin/?" + urllib.parse.urlencode(keep))
        return True

    m = re.match(r"^/admin/books/(.+)$", path)
    if m:
        _send_redirect(handler, "/admin/?pane=book&id=" + urllib.parse.quote(m.group(1)))
        return True

    # /admin/api/bookplate (GET: id, style) — render to PNG, return as image/png
    if path == "/admin/api/bookplate" and handler.command == "GET":
        qp = urllib.parse.parse_qs(query or "")
        eid_raw = (qp.get("id", [""])[0] or "").strip()
        style = (qp.get("style", [_render.DEFAULT_BOOKPLATE_STYLE])[0] or _render.DEFAULT_BOOKPLATE_STYLE).strip()
        # id can be int or str (历史合并来源) — both accepted as-is
        if not eid_raw:
            return _send_html(handler, _page("失败", '<p style="color:var(--danger)">ID 缺失</p>', "tools", handler), status=400)
        png_bytes, err = _render_bookplate_png(data_dir, eid_raw, style)
        if err:
            return _send_html(handler, _page("书票失败", f'<p style="color:var(--danger)">{_e(err)}</p>', "tools", handler), status=500)
        handler.send_response(200)
        handler.send_header("Content-Type", "image/png")
        handler.send_header("Content-Length", str(len(png_bytes)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(png_bytes)
        return True

    # /admin/api/export-zhijian (POST) — 浏览器当桥: server 只造 payload 清单,
    # 浏览器拿到后自己 fetch 到 http://<手机>:<端口>/send。
    # 为什么不在 server 直推: server 与手机可能不同子网, 而浏览器跟手机在同一网段;
    # 且 filter 维度 (书名/作者/时间窗/文本) 在 server 端比 client 端更可靠。
    if path == "/admin/api/export-zhijian" and handler.command == "POST":
        form = _read_form(handler)
        host = (form.get("host", "") or "").strip()
        try:
            port = int(form.get("port", "8080") or "8080")
        except ValueError:
            port = 8080
        source = (form.get("source", "all") or "all").strip()
        book_name = (form.get("book_name", "") or "").strip()
        author = (form.get("author", "") or "").strip()
        day_from = (form.get("day_from", "") or "").strip()
        day_to = (form.get("day_to", "") or "").strip()
        text_q = (form.get("q", "") or "").strip()
        try:
            safety_cap = int(form.get("safety_cap", "200") or "200")
        except ValueError:
            safety_cap = 200
        # 勾选框缺省 = 带上 (与旧实现一致); 显式传 0/false/off 才不带
        include_time = (form.get("include_time", "1") or "1").strip().lower() not in (
            "0", "false", "no", "off", "")
        if not host:
            _send_json(handler, {"ok": False, "error": "手机 IP 必填 (host)"}, status=400)
            return True
        try:
            result = _build_zhijian_payloads(
                data_dir, host, port, source, book_name, author,
                day_from, day_to, text_q, safety_cap,
                include_time=include_time,
            )
        except Exception as exc:  # 不许把 traceback 当 JSON 吐给前端
            _send_json(handler, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status=500)
            return True
        _send_json(handler, result)
        return True

    # /admin/api/zhijian-send (POST) — 由服务器代发一本到 http://<手机>:<端口>/send
    # 存在理由: 从公网域名 (如 xx.zicp.fun) 打开控制台时, 浏览器不允许页面
    # fetch 私网 IP (CORS / Private Network Access), 而服务器与手机同在局域网。
    if path == "/admin/api/zhijian-send" and handler.command == "POST":
        form = _read_form(handler)
        host = (form.get("host") or "").strip()
        try:
            port = int(form.get("port") or "8080")
        except ValueError:
            port = 8080
        raw = form.get("payload") or ""
        if not host or not raw:
            _send_json(handler, {"ok": False, "error": "host / payload 必填"}, status=400)
            return True
        try:
            body_obj = json.loads(raw)
        except Exception:
            _send_json(handler, {"ok": False, "error": "payload 不是合法 JSON"}, status=400)
            return True
        code, text, err = _post_json(f"http://{host}:{port}/send", body_obj, ZHIJIAN_TIMEOUT)
        if err:
            _send_json(handler, {"ok": False, "error": err}, status=502)
            return True
        try:
            jr = json.loads(text)
        except Exception:
            jr = None
        ok = code == 200 and (jr is None or jr.get("code") == 200)
        _send_json(handler, {
            "ok": ok,
            "status": code,
            "code": (jr or {}).get("code"),
            "message": (jr or {}).get("message"),
            "body": (text or "")[:300],
        }, status=200 if ok else 502)
        return True

    # /admin/api/excerpts.json (enriched: joined book meta + source + char count)
    if path == "/admin/api/excerpts.json":
        books = _load_books(data_dir)
        excerpts = _load_excerpts(data_dir)
        filtered = _filter_excerpts(
            excerpts, q=q, book_id=book_id, book_q=book_q, author=author,
            day_from=day_from, day_to=day_to, books=books,
        )
        if source in ("m5", "kv"):
            want = "M5Paper" if source == "m5" else "合并"
            filtered = [e for e in filtered if _source_type(e) == want]
        if chars == "short":
            filtered = [e for e in filtered if _char_count(e) < 50]
        elif chars == "mid":
            filtered = [e for e in filtered if 50 <= _char_count(e) <= 200]
        elif chars == "long":
            filtered = [e for e in filtered if _char_count(e) > 200]
        enriched = []
        for e in filtered:
            bid = e.get("bookId")
            b = books.get(bid) or {}
            enriched.append({
                **e,
                "bookName": b.get("name", ""),
                "bookAuthor": b.get("author", ""),
                "source": _source_type(e),
                "charCount": _char_count(e),
            })
        _send_json(handler, {
            "count": len(enriched),
            "filter": {"q": q, "book": book_id, "book_q": book_q, "author": author, "from": day_from, "to": day_to, "source": source, "chars": chars},
            "excerpts": enriched,
        })
        return True

    # /admin/api/delete  - GET ?id=NN (single, redirect-back)
    #                    - POST id=NN  (single, form-encoded)
    #                    - POST ids=A,B,C (bulk)
    if path == "/admin/api/delete":
        if handler.command == "GET":
            query_params = urllib.parse.parse_qs(query or "")
            eid_str = (query_params.get("id", [""])[0] or "")
            back = (query_params.get("back", ["/admin/excerpts"])[0] or "/admin/excerpts")
        else:
            form = _read_form(handler)
            eid_str = form.get("id", "")
            ids_str = form.get("ids", "")
            back = form.get("back", "/admin/excerpts")
            if ids_str:
                # bulk delete
                eids: list[int] = []
                for x in ids_str.split(","):
                    x = x.strip()
                    if not x:
                        continue
                    try:
                        eids.append(int(x))
                    except ValueError:
                        pass
                deleted = sum(1 for x in eids if _delete_excerpt(data_dir, x))
                # flash via cookie-less query param
                sep = "&" if "?" in back else "?"
                _send_redirect(handler, f"{back}{sep}deleted={deleted}")
                return True
        try:
            eid = int(eid_str)
        except ValueError:
            _send_html(handler, _page("失败", '<p style="color:var(--danger)">ID 非法</p>', "excerpts", handler), status=400)
            return True
        ok = _delete_excerpt(data_dir, eid)
        if ok:
            _send_redirect(handler, back)
        else:
            _send_html(handler, _page("失败", '<p style="color:var(--danger)">删除失败 (未找到)</p>', "excerpts", handler), status=404)
        return True

    # 404 within admin
    _send_html(handler, _page("404", '<p class="muted">页面不存在</p>', "", handler), status=404)
    return True


def _delete_excerpt(data_dir: Path, excerpt_id: int) -> bool:
    """Remove an excerpt from aggregate. Rewrite excerpts.jsonl + index.jsonl + deck files."""
    excerpts_path, _ = _aggregate_paths(data_dir)
    if not excerpts_path.exists():
        return False
    found = False
    kept: list[str] = []
    with excerpts_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                kept.append(line)
                continue
            if r.get("id") == excerpt_id:
                found = True
                continue
            kept.append(json.dumps(r, ensure_ascii=False))
    if not found:
        return False
    # atomic write
    tmp = excerpts_path.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    tmp.replace(excerpts_path)
    return found


def _redirect_with_flash(handler, location: str, flash: str) -> bool:
    """Redirect with flash message in ?flash= (URL-encoded, length-limited)."""
    if len(flash) > 4000:
        flash = flash[:4000] + "\n... [truncated]"
    sep = "&" if "?" in location else "?"
    target = f"{location}{sep}flash={urllib.parse.quote(flash)}"
    _send_redirect(handler, target)
    return True


def _entry_page_pct(e: dict) -> int:
    """纸间 /send 的 locationUnit=0 把 page 当「书内百分比 0-100」。

    以前这里恒 0: 所有摘录落在同一个位置, 既没法"跳到原文", 同一章里的多条
    在 App 侧也无法区分。有 合并 页号 + 总页数就换算成百分比, 没有就 0。
    """
    try:
        pg = int(e.get("page") or 0)
        total = int(e.get("pageCount") or 0)
    except (TypeError, ValueError):
        return 0
    if total <= 0:
        return 0
    pct = round(pg * 100.0 / total)
    return max(0, min(100, pct))


def _norm_title(s: str) -> str:
    """书名归一 —— 只用于匹配, 不做展示。

    摘录库里的书名来自阅读器/设备上报 ("剑来（1-54册）完结精校版"),
    书库里的来自文件名或 epub 元数据, 标点与括号写法常有差异。
    """
    s = (s or "").strip().lower()
    s = re.sub(r"[\s　]+", "", s)
    s = re.sub(r"[（(].*?[)）]", "", s)
    s = re.sub(r"[《》「」『』\"'“”‘’·・:：,，.。!！?？\-–—_/\\]+", "", s)
    return s


_TITLE_MAP_CACHE: dict[str, Any] = {"at": 0.0, "map": {}}


def _reader_key_map(ttl: float = 60.0) -> dict[str, str]:
    """书库里的书 -> {归一书名: 阅读器 book key}。

    两个来源: 阅读器自己的书名, 以及边车里的云端书名 (下载时写下的原名)。
    只读 rbook + 每本边车, 不扫库 (扫描是几秒级, 导出时不该付这个成本)。
    """
    now = time.time()
    if now - float(_TITLE_MAP_CACHE.get("at") or 0) < ttl and _TITLE_MAP_CACHE.get("map"):
        return _TITLE_MAP_CACHE["map"]  # type: ignore[return-value]
    out: dict[str, str] = {}
    try:
        rows = _reader_state.list_books()
    except Exception:
        rows = []
    for b in rows:
        key = str(b.get("key") or "")
        if not key:
            continue
        title = str(b.get("title") or "")
        path = str(b.get("path") or "")
        for nm in (title, Path(path).stem if path else ""):
            n = _norm_title(nm)
            if n:
                out.setdefault(n, key)
        if path:
            try:
                link = _sidecar.cloud_link(path) or {}
            except Exception:
                link = {}
            n = _norm_title(str(link.get("name") or ""))
            if n:
                out.setdefault(n, key)
    _TITLE_MAP_CACHE.update({"at": now, "map": out})
    return out


def _reader_key_for(title: str, tmap: dict[str, str]) -> str:
    """书名 -> 阅读器 book key。先精确归一, 再单向包含 (书库那本更短/更长)。"""
    n = _norm_title(title)
    if not n:
        return ""
    hit = tmap.get(n)
    if hit:
        return hit
    for k, key in tmap.items():
        if not k:
            continue
        if len(k) >= 3 and (k in n or n in k):
            return key
    return ""


def _build_zhijian_payloads(
    data_dir: Path,
    host: str,
    port: int,
    source: str,
    book_name: str,
    author: str,
    day_from: str,
    day_to: str,
    text_q: str,
    safety_cap: int,
    include_time: bool = True,
) -> dict:
    """从 aggregate 摘录按 filter 选, 按书分组造 xmnote /send payload.

    不发网络 — 浏览器拿到返回后, 用 fetch POST 到 http://<手机>:<port>/send.
    旧版 _export_to_zhijian (server 直推) 2026-06-15 删, 因为:
      - server 跟手机可能不同子网, server 直推失败但浏览器能通
      - filter 维度 (书名/作者/时间窗/文本) 在 server 端比 client 端更可靠
    """
    excerpts = _load_excerpts(data_dir)
    books = _load_books(data_dir)

    # 1. source filter (m5/kv/all)
    if source in ("m5", "kv"):
        want = "M5Paper" if source == "m5" else "合并"
        excerpts = [e for e in excerpts if _source_type(e) == want]

    # 2. 提前用 books 表筛 book_name / author — 摘录自己没这俩字段
    if book_name or author:
        keep: list[dict] = []
        bn_l = book_name.lower()
        au_l = author.lower()
        for e in excerpts:
            b = books.get(e.get("bookId")) or {}
            if book_name and bn_l not in (b.get("name") or "").lower():
                continue
            if author and au_l not in (b.get("author") or "").lower():
                continue
            keep.append(e)
        excerpts = keep

    # 3. 时间窗 + 文本搜索 — 复用 _filter_excerpts
    if day_from or day_to or text_q:
        # 暂存原文, _filter_excerpts 返新 list
        excerpts = _filter_excerpts(excerpts, q=text_q, book_id="", day_from=day_from, day_to=day_to, books=books)

    # 4. createdTime 倒序
    excerpts.sort(key=_ts_ms, reverse=True)

    # 5. safety cap
    if safety_cap > 0 and len(excerpts) > safety_cap:
        return {
            "ok": False,
            "error": f"命中 {len(excerpts)} 条 > safety_cap {safety_cap}, 请收紧 filter",
            "total": len(excerpts),
        }

    if not excerpts:
        return {"ok": False, "error": "没有可导出的摘录", "total": 0, "payloads": []}

    # 6. 按书分组
    by_book: dict[str, list[dict]] = {}
    for e in excerpts:
        bid = e.get("bookId")
        b = books.get(bid) or {}
        title = b.get("name") or f"book#{bid}"
        by_book.setdefault(title, []).append(e)

    # 6.5 阅读时长: 书名 -> 阅读器 book key -> 本地会话区间
    #     匹配不到 (摘录来自 M5Paper 独有书 / 书库里没这本) 就留空, 不报错 ——
    #     时长是附加信息, 不该挡推送。
    tmap: dict[str, str] = {}
    time_error = ""
    if include_time:
        try:
            tmap = _reader_key_map()
        except Exception as exc:                    # noqa: BLE001
            time_error = f"{type(exc).__name__}: {exc}"
            tmap = {}

    payloads: list[dict] = []
    for title, es in by_book.items():
        bid0 = es[0].get("bookId")
        author_v = (books.get(bid0) or {}).get("author", "")
        entries: list[dict] = []
        for e in es:
            ms = _ts_ms(e) // 1000 if _ts_ms(e) else int(datetime.now().timestamp())
            entries.append({
                "page": _entry_page_pct(e),
                "text": e.get("content", ""),
                "note": e.get("idea", "") or "",
                "chapter": e.get("chapter", "") or "未分章",
                "time": int(ms),
            })
        pl = {
            "title": title,
            "author": author_v,
            "type": 1,           # 纸间 /send: int, 0/1 都合法, 1=book
            "locationUnit": 0,   # page 字段当 0-100 百分比解释
            "entries": entries,
        }
        if include_time and tmap:
            rkey = _reader_key_for(title, tmap)
            if rkey:
                try:
                    rec = _sessions.book_time(rkey, day_from, day_to)
                except Exception as exc:            # noqa: BLE001
                    rec = {}
                    time_error = time_error or f"{type(exc).__name__}: {exc}"
                pl["timeMinutes"] = rec.get("minutes", 0)
                pl["timeSessions"] = rec.get("sessions", 0)
                pl["readerKey"] = rkey
                if rec.get("durations"):
                    # 纸间 /send 顶层字段: 精确阅读时长明细 (开始/结束 Unix 秒)
                    pl["preciseReadingDurations"] = rec["durations"]
        payloads.append(pl)

    out = {
        "ok": True,
        "host": host,
        "port": port,
        "total": len(excerpts),
        "book_count": len(payloads),
        "time_minutes": round(sum((p.get("timeMinutes") or 0) for p in payloads), 1),
        "time_books": sum(1 for p in payloads if p.get("preciseReadingDurations")),
        "payloads": payloads,
    }
    if time_error:
        out["time_error"] = time_error
    return out


def _render_bookplate_png(data_dir: Path, excerpt_id, style: str) -> tuple[bytes | None, str | None]:
    """Render one excerpt to PNG bytes via bin/render.py (subprocess).

    Returns (png_bytes, None) on success, or (None, error_msg) on failure.
    excerpt_id can be int or str (历史合并来源用字符串 id)。

    `style` is validated against the canonical list exported by render.py
    (BOOKPLATE_STYLES), with DEFAULT_BOOKPLATE_STYLE = "zhijian" as the
    skill-wide default. The webui "书票" button always uses zhijian.
    """
    import subprocess
    import tempfile

    if style not in _render.BOOKPLATE_STYLES:
        return None, (
            f"未知 style {style!r} (可选: {', '.join(_render.BOOKPLATE_STYLES)}, "
            f"默认 {_render.DEFAULT_BOOKPLATE_STYLE})"
        )

    # 1. Find the excerpt
    excerpts = _load_excerpts(data_dir)
    books = _load_books(data_dir)
    target = next((e for e in excerpts if str(e.get("id")) == str(excerpt_id)), None)
    if target is None:
        return None, f"找不到 id={excerpt_id} 的摘录"
    bid = target.get("bookId")
    b = books.get(bid) or {}
    target = {**target, "bookName": b.get("name", ""), "bookAuthor": b.get("author", "")}

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(target, f, ensure_ascii=False)
        tmp_json = f.name

    # render.py 是包内模块 (要读 recto.core.paths 的书籍票配置), 所以只能
    # 用 `-m` 起, 不能按脚本路径起 —— 按路径起 sys.path[0] 是它自己所在的目录,
    # `import recto` 必然失败。素材根 (模板 / node_modules / 输出目录) 全部来自
    # 配置 [bookplate] asset_root, 与包所在位置无关, 所以 cwd 用包父目录即可。
    from recto.assets.bookplate import render as _render_mod
    pkg_root = Path(_render_mod.__file__).resolve().parents[3]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(pkg_root) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "recto.assets.bookplate.render",
             "--no-bg", "--style", style, tmp_json],
            capture_output=True, text=True, timeout=BOOKPLATE_TIMEOUT,
            cwd=str(pkg_root), env=env,
        )
    except subprocess.TimeoutExpired:
        return None, f"渲染超时 ({BOOKPLATE_TIMEOUT}s)"
    finally:
        try:
            Path(tmp_json).unlink()
        except OSError:
            pass

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return None, f"渲染失败 (rc={proc.returncode}):\n{detail[-800:]}"

    # render.py 自己声明输出路径: "OK style=zhijian out=/.../xxx.png"
    m = re.search(r"out=(\S+\.png)", proc.stdout or "")
    if not m:
        return None, f"渲染器没有报告输出路径。 stdout: {(proc.stdout or '')[:300]}"
    out_png_path = Path(m.group(1))
    if not out_png_path.exists():
        return None, f"渲染器报告了 {out_png_path}, 但文件不存在"
    try:
        data = out_png_path.read_bytes()
    except OSError as e:
        return None, f"读 PNG 失败 ({out_png_path}): {e}"
    if not data.startswith(b"\x89PNG"):
        return None, f"输出不是 PNG ({out_png_path})"
    return data, None
