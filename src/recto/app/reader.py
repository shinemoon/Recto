"""阅读器 —— 直接在原书文件上读，字节坐标与云端同构。

路由
----
    GET  /read                      重定向到控制台的「书架」分区 (/#library)
    GET  /read/b/<key>              阅读页
    GET  /read/api/books            书目 JSON（?q= 搜索 / ?refresh=1 重扫）
    GET  /read/api/book/<key>       书的元信息 + 章列表 + 进度 + 书签 + 划线笔记
    GET  /read/api/book/<key>/chapter/<i>   章节正文块（每块带字节偏移）
    GET  /read/api/book/<key>/asset?p=...   书内图片/字体
    POST /read/api/book/<key>/progress      写进度
    POST /read/api/book/<key>/bookmark      加书签 / delete 删
    POST /read/api/book/<key>/note          加划线笔记 / update / delete
    POST /read/api/book/<key>/session       阅读时长 start|beat|end

为什么读原书
------------
切片版（`kp/pNNNN.xhtml`）存在的唯一理由是让"一页 = 一篇 spine"的阅读器
也能表达字节比例。本阅读器直接按字节记进度，所以**原书即可**，切片不再必要。
扫描书库时会把 sidecar 里登记过的切片版解析回原书（见 reader_state.scan）。
"""
from __future__ import annotations

import html as _html
import json
import logging
import re
import time
import traceback
import urllib.parse
from pathlib import Path

from recto.core import bookfile
from recto.services import sync as _sync
from recto.services import reader_state as rs
from recto.app.ui import shell

_log = logging.getLogger("recto.reader")

_DOCS: dict[str, tuple[float, bookfile.BookDoc]] = {}
_DOC_MAX = 4


def _e(s) -> str:
    return _html.escape(str(s if s is not None else ""), quote=True)


def _doc(key: str) -> bookfile.BookDoc | None:
    row = rs.get_book(key)
    if not row:
        return None
    path = row["path"]
    try:
        mtime = Path(path).stat().st_mtime
    except OSError:
        return None
    hit = _DOCS.get(key)
    if hit and abs(hit[0] - mtime) < 1:
        return hit[1]
    if hit:
        hit[1].close()
        _DOCS.pop(key, None)
    doc = bookfile.BookDoc(path)
    if len(_DOCS) >= _DOC_MAX:
        old = next(iter(_DOCS))
        _DOCS.pop(old)[1].close()
    _DOCS[key] = (mtime, doc)
    rs.touch_geometry(key, doc)
    return doc


def _pct_text(p: float) -> str:
    return f"{int(p * 1000) / 10:.1f}%"


# --------------------------------------------------------------------------
# 页面
# --------------------------------------------------------------------------

_READER_HTML = """
<div class="rv" id="rv" data-book="{key}">
  <div class="rv-prog" id="rv-prog" aria-hidden="true"></div>

  <header class="rv-bar" id="rv-bar">
    <a class="rv-back" href="/#library" title="返回书架" aria-label="返回书架">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"
           stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M13.8 5.4 7.2 12l6.6 6.6"/><path d="M7.4 12h11.2" opacity=".6"/></svg>
    </a>
    <div class="rv-meta">
      <div class="rv-title" id="rv-title">{title}</div>
      <div class="rv-sub" id="rv-sub">{author}</div>
    </div>
    <div class="rv-pct" id="rv-pct">0.0%</div>
    <div class="rv-btns">
      <button type="button" class="rv-ico" data-act="toc" title="目录" aria-label="目录">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
             stroke-linecap="round" aria-hidden="true">
          <path d="M9 6.5h11M9 12h11M9 17.5h11"/>
          <circle cx="4.6" cy="6.5" r="1.1" fill="currentColor" stroke="none"/>
          <circle cx="4.6" cy="12" r="1.1" fill="currentColor" stroke="none"/>
          <circle cx="4.6" cy="17.5" r="1.1" fill="currentColor" stroke="none"/></svg>
      </button>
      <button type="button" class="rv-ico" data-act="marks" title="书签" aria-label="书签">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
             stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M7 4.5h10a1 1 0 0 1 1 1v15l-6-4.2-6 4.2v-15a1 1 0 0 1 1-1Z"/></svg>
      </button>
      <button type="button" class="rv-ico" data-act="notes" title="划线笔记" aria-label="划线笔记">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
             stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M4 20h4.2L19 9.2a2.4 2.4 0 0 0-3.4-3.4L4.8 16.6 4 20Z"/>
          <path d="M14.2 7.2 17.6 10.6"/></svg>
      </button>
      <button type="button" class="rv-ico" data-act="typo" id="rv-typo-btn"
              title="排版" aria-label="排版" aria-expanded="false" aria-controls="rv-typo">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
             stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M2.8 18.2 6.9 6.6l4.1 11.6"/><path d="M4.4 14.2h5"/>
          <circle cx="17.9" cy="14.6" r="3.5"/><path d="M21.4 11v7.2"/></svg>
      </button>
      <button type="button" class="rv-ico" data-act="theme" title="切换配色" aria-label="切换配色">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
             aria-hidden="true">
          <circle cx="12" cy="12" r="8"/>
          <path d="M12 4a8 8 0 0 1 0 16Z" fill="currentColor" stroke="none"/></svg>
      </button>
    </div>
  </header>

  <div class="rv-typo" id="rv-typo" hidden role="dialog" aria-label="排版设置">
    <div class="rv-typo-h">
      <strong>排版</strong>
      <button type="button" data-typo="reset">恢复默认</button>
    </div>
    <div class="rv-typo-row rv-wide"><span>阅读模式</span>
      <div class="rv-seg" id="rv-mode" role="group" aria-label="阅读模式">
        <button type="button" data-mode="scroll" aria-pressed="true">滚动</button>
        <button type="button" data-mode="page" aria-pressed="false">左右翻页</button>
      </div></div>
    <label class="rv-typo-row rv-wide"><span>翻页效果</span>
      <select id="rv-fx" aria-label="翻页效果">
        <option value="slide">滑动</option>
        <option value="none">无</option>
        <option value="fade">淡入</option>
        <option value="ripple">水波纹</option>
        <option value="flip">翻卷</option>
      </select></label>
    <label class="rv-typo-row"><span>字号</span>
      <input type="range" min="14" max="30" step="0.5" data-look="fs" aria-label="字号">
      <b data-val="fs"></b></label>
    <label class="rv-typo-row"><span>行间距</span>
      <input type="range" min="1.3" max="2.6" step="0.05" data-look="lh" aria-label="行间距">
      <b data-val="lh"></b></label>
    <label class="rv-typo-row"><span>段间距</span>
      <input type="range" min="0" max="2" step="0.05" data-look="para" aria-label="段间距">
      <b data-val="para"></b></label>
    <label class="rv-typo-row"><span>上下边距</span>
      <input type="range" min="0.2" max="5" step="0.1" data-look="pad" aria-label="上下页边距">
      <b data-val="pad"></b></label>
    <label class="rv-typo-row"><span>阅读宽度</span>
      <input type="range" min="26" max="68" step="1" data-look="w" aria-label="阅读宽度">
      <b data-val="w"></b></label>
    <div class="rv-typo-row rv-wide"><span>首行缩进</span>
      <div class="rv-seg" id="rv-indent" role="group" aria-label="首行缩进">
        <button type="button" data-indent="0" aria-pressed="false">无</button>
        <button type="button" data-indent="2" aria-pressed="true">两字</button>
      </div></div>
    <div class="rv-typo-row rv-wide"><span>注释呈现</span>
      <div class="rv-seg" id="rv-notemode" role="group" aria-label="注释呈现">
        <button type="button" data-notemode="popover" aria-pressed="true">弹出</button>
        <button type="button" data-notemode="panel" aria-pressed="false">侧栏</button>
        <button type="button" data-notemode="endnotes" aria-pressed="false">章末</button>
      </div></div>
    <div class="rv-typo-row rv-wide" id="rv-kw-row" hidden><span>关键词高亮</span>
      <div class="rv-seg" id="rv-kw" role="group" aria-label="关键词高亮">
        <button type="button" data-kw="0" aria-pressed="false">关</button>
        <button type="button" data-kw="1" aria-pressed="true">开</button>
      </div></div>
    <div class="rv-typo-foot" id="rv-kw-note" hidden></div>
    <div class="rv-typo-row rv-font"><span>字体</span>
      <div class="rv-combo" id="rv-font-box">
        <input type="text" id="rv-font" role="combobox" aria-controls="rv-font-menu"
               aria-expanded="false" aria-autocomplete="list" aria-label="正文字体"
               placeholder="跟随界面" autocomplete="off" spellcheck="false">
        <button type="button" class="rv-combo-btn" id="rv-font-open" tabindex="-1"
                aria-haspopup="listbox" aria-label="展开字体列表">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"
               stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M6 9.5 12 15.5 18 9.5"/></svg>
        </button>
        <div class="rv-combo-menu" id="rv-font-menu" role="listbox" aria-label="字体" hidden></div>
      </div>
      <button type="button" class="rv-scan" id="rv-font-scan" title="读取本机已装字体"
              aria-label="读取本机已装字体">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
             stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M20 12a8 8 0 1 1-2.4-5.7"/><path d="M20 4.2v4.6h-4.6"/></svg>
      </button></div>
    <div class="rv-typo-foot rv-font-note" id="rv-font-note" hidden></div>
    <div class="rv-typo-foot">设置只影响这台设备上的阅读界面。</div>
  </div>

  <button type="button" class="rv-turn rv-turn-l" data-turn="-1" aria-label="上一页">
    <span aria-hidden="true">‹</span></button>
  <button type="button" class="rv-turn rv-turn-r" data-turn="1" aria-label="下一页">
    <span aria-hidden="true">›</span></button>

  <aside class="rv-drawer" id="rv-toc" aria-hidden="true">
    <div class="rv-drawer-h"><strong>目录</strong>
      <span class="rv-drawer-acts" id="rv-toc-acts" hidden>
        <button type="button" class="rv-toc-all" data-toc-act="expand">展开</button>
        <button type="button" class="rv-toc-all" data-toc-act="collapse">收起</button>
      </span>
      <button type="button" data-close="toc">×</button></div>
    <ol class="rv-toc" id="rv-toc-list"></ol>
  </aside>

  <aside class="rv-drawer" id="rv-marks" aria-hidden="true">
    <div class="rv-drawer-h">
      <strong>书签 <span class="rv-cnt" id="rv-marks-cnt"></span></strong>
      <span class="rv-drawer-acts">
        <button type="button" class="rv-add" data-act="mark">＋ 加书签</button>
        <button type="button" data-close="marks">×</button>
      </span>
    </div>
    <div class="rv-list" id="rv-marks-list"></div>
  </aside>

  <aside class="rv-drawer" id="rv-notes" aria-hidden="true">
    <div class="rv-drawer-h">
      <strong>笔记 <span class="rv-cnt" id="rv-notes-cnt"></span></strong>
      <button type="button" data-close="notes">×</button>
    </div>
    <div class="rv-list" id="rv-notes-list"></div>
  </aside>

  <main class="rv-main" id="rv-main">
    <div class="rv-view" id="rv-view">
      <div class="rv-page" id="rv-content"></div>
    </div>
    <div class="rv-foot" id="rv-foot">— 继续下滑读下一章 —</div>
  </main>

  <div class="rv-pageind" id="rv-pageind" hidden></div>
  <!-- 右下角云端同步状态 (可在设置页关掉): 只读文字, 不吃指针 -->
  <div class="rv-sync" id="rv-sync" hidden aria-live="off"></div>

  <!-- 书宠: 最新一回的入口。平时只是个小图标 (半透明), 有更新时慢慢转;
       悬停/键盘聚焦显示两行摘要, 点击新开页去书宠看细节故事。 -->
  <a class="rv-pet" id="rv-pet" href="/pet/" target="_blank" rel="noopener"
     aria-label="书宠 · 最新一回" hidden>
    <svg class="rv-pet-ico" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M5.2 4.8h6.3a4.3 4.3 0 0 1 4.3 4.3v10.1H9.5a4.3 4.3 0 0 1-4.3-4.3V4.8Z"
            fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
      <path d="M8.8 9.6h3.6M8.8 12.8h3.6" stroke="currentColor" stroke-width="1.3"
            stroke-linecap="round"/>
      <circle cx="17.9" cy="6.3" r="1.8" fill="currentColor"/>
    </svg>
    <span class="rv-pet-tip" id="rv-pet-tip" role="tooltip"></span>
  </a>

  <!-- 云端比本地靠前时问一句: 跳过去, 还是留在本地 (留本地就覆盖云端) -->
  <div class="rv-choice" id="rv-choice" hidden role="dialog" aria-live="polite">
    <div class="rv-choice-txt" id="rv-choice-txt"></div>
    <div class="rv-choice-btns">
      <button type="button" data-c="local">留在本地</button>
      <button type="button" data-c="cloud" class="primary">跳到云端</button>
    </div>
    <div class="rv-choice-hint">不选就按本地位置继续读, 下次打开再问</div>
  </div>

  <!-- 翻页效果的舞台: 固定盖满整个视口, 不吃指针 -->
  <div class="rv-fxstage" id="rv-fxstage" aria-hidden="true"></div>

  <div class="rv-sel" id="rv-sel" hidden>
    <button type="button" data-sel="hl">划线</button>
    <button type="button" data-sel="note">划线 + 笔记</button>
    <button type="button" data-sel="copy">复制</button>
    <button type="button" data-sel="cancel">×</button>
  </div>

  <div class="rv-noteedit" id="rv-noteedit" hidden>
    <div class="rv-noteedit-q" id="rv-noteedit-q"></div>
    <textarea id="rv-noteedit-t" rows="4" placeholder="写点想法（可留空）"></textarea>
    <div class="rv-noteedit-b">
      <button type="button" class="primary" data-ne="save">保存</button>
      <button type="button" data-ne="cancel">取消</button>
    </div>
  </div>

  <div class="rv-toast" id="rv-toast" hidden></div>
</div>
"""


def reader_page(user: str | None, key: str) -> str | None:
    row = rs.get_book(key)
    if not row:
        return None
    body = _READER_HTML.format(key=_e(key), title=_e(row.get("title")),
                               author=_e(row.get("author") or ""))
    return shell.document(
        title=str(row.get("title") or "阅读"), body=body, active="read", user=user,
        head_extra=('<link rel="stylesheet" href="/assets/reader.css">'
                    '<script defer src="/assets/reader.js"></script>'),
        body_class="rv-body", footer=False)


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

def _meta(key: str, doc: bookfile.BookDoc) -> dict:
    prog = rs.get_progress(key)
    chapters = []
    cum = 0
    for c in doc.chapters:
        chapters.append({"i": c.index, "title": c.title, "start": c.start,
                         "size": c.size, "catalog": c.catalog_id})
        cum += c.size
    return {
        "key": key,
        "title": doc.title,
        "author": doc.author,
        "kind": doc.kind,
        "bytes": doc.total_bytes,
        "catalog_total": doc.catalog_total,
        "cover": doc.cover,
        "chapters": chapters,
        "toc": doc.toc_nodes,
        "progress": prog,
        "bookmarks": rs.list_bookmarks(key),
        "notes": rs.list_notes(key),
        "stats": rs.time_stats(key),
        # 自动同步 (前端据此决定: 打开时要不要先拉一次 / 右下角要不要显示时间)
        "sync": _sync.sync_state(key),
    }


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3000-\u303f]")


def _cjk_ratio(text: str) -> float:
    """汉字占比（0..1）。用来决定这一段要不要按中文版式排（首行缩进等）。"""
    if not text:
        return 0.0
    cjk = len(_CJK_RE.findall(text))
    latin = len(re.findall(r"[A-Za-z]+", text))
    return cjk / max(1, cjk + latin)


def _chapter_payload(doc: bookfile.BookDoc, idx: int) -> dict:
    if idx < 0 or idx >= len(doc.chapters):
        return {}
    ch = doc.chapters[idx]
    blocks = doc.blocks(idx)
    cjk = _cjk_ratio("".join(b.text for b in blocks)) >= 0.3
    return {"i": idx, "title": ch.title, "start": ch.start, "size": ch.size,
            "catalog": ch.catalog_id, "cjk": cjk,
            "blocks": [b.as_dict() for b in blocks],
            # 注释单独下发（正文里只留 <sup class="rv-fn" data-note="N"> 标记）。
            # 前端按用户选的形态呈现：弹出 / 侧栏 / 章末。
            "notes": doc.notes(idx)}


def _track_chapter(key: str, idx: int, payload: dict) -> None:
    """记"人打开了这一章" —— 章节足迹 + 语料遭遇 (后台线程, 不挡翻页)。

    书宠不长嘴, 但它要记住"陪读过什么": 这一章里出现过哪些语料词、各几次,
    以后拿它去编写自己的游历 (见 `recto.pet.encounters` / `recto.pet.chronicle`)。
    """
    try:
        text = "".join(b.get("text") or "" for b in (payload.get("blocks") or []))
        if not text:
            return
        from recto.pet import encounters

        encounters.record_async(key, idx, text, str(payload.get("title") or ""))
    except Exception:  # noqa: BLE001 — 记录失败绝不影响阅读
        pass


def _read_json(h) -> dict:
    n = int(h.headers.get("Content-Length") or 0)
    if not n:
        return {}
    raw = h.rfile.read(n)
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        return {}


def handle(h) -> bool:
    """把 /read* 的请求接过来。返回 True = 已处理。"""
    path = urllib.parse.urlparse(h.path).path
    if not (path == "/read" or path.startswith("/read/")):
        return False
    q = urllib.parse.parse_qs(urllib.parse.urlparse(h.path).query)
    method = h.command

    # ---- 页面 ----
    if method == "GET" and path in ("/read", "/read/"):
        # 书架与控制台合并 —— 旧链接仍然能用, 直接落到「书架」分区
        if q.get("refresh"):
            rs.scan(force=True)
        h._redirect("/#library")
        return True

    if method == "GET" and path.startswith("/read/b/"):
        key = path[len("/read/b/"):].strip("/")
        page = reader_page(h._user(), key)
        if page is None:
            h._send(404, b"book not found", "text/plain; charset=utf-8")
        else:
            h._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
        return True

    # ---- API ----
    if not path.startswith("/read/api/"):
        h._json(404, {"error": "not found"})
        return True

    try:
        if method == "GET" and path == "/read/api/pet-brief":
            # 书宠最新一回的两行摘要 (阅读器右下角小图标悬停用)。与书无关,
            # 所以放在 book/<key> 之前。
            from recto.pet import brief

            h._json(200, brief.latest())
            return True

        if method == "GET" and path == "/read/api/books":
            if (q.get("refresh") or ["0"])[0] in ("1", "true", "yes"):
                rs.scan(force=True)
            elif not rs.list_books():
                rs.scan()
            h._json(200, {"books": rs.list_books((q.get("q") or [""])[0]),
                          "stats": rs.time_stats()})
            return True

        parts = [p for p in path[len("/read/api/"):].split("/") if p]
        # book/<key>...
        if not parts or parts[0] != "book" or len(parts) < 2:
            h._json(404, {"error": "not found"})
            return True
        key = parts[1]
        rest = parts[2:]
        if method == "POST" and rest[:1] == ["delete"] and len(rest) == 1:
            # 从书架删掉一本书: 文件直接删除, 库里只删 rbook 那一行
            # (进度 / 书签 / 划线 / 时长留在库里, 文件放回原位就还在)。
            payload = _read_json(h)
            try:
                out = rs.delete_book(key, keep_file=bool(payload.get("keep_file")))
            except FileNotFoundError as e:
                h._json(404, {"error": str(e)})
                return True
            except PermissionError as e:
                h._json(403, {"error": str(e)})
                return True
            _log.info("删书 · %s · %s", out.get("title"),
                      "文件已删除" if out.get("file_removed") else "文件保留")
            h._json(200, out)
            return True
        doc = _doc(key)
        if doc is None:
            h._json(404, {"error": "book not found"})
            return True

        if method == "GET" and not rest:
            h._json(200, _meta(key, doc))
            return True
        if method == "GET" and rest[:1] == ["chapter"] and len(rest) == 2:
            idx = int(rest[1])
            payload = _chapter_payload(doc, idx)
            _track_chapter(key, idx, payload)
            h._json(200, payload)
            return True
        if method == "GET" and rest[:1] == ["sync"] and len(rest) == 1:
            # 打开一本书时**只比不写** (节流 [sync] pull_gap_sec)。
            # 云端更靠前 → need_choice, 交读者决定跳不跳; 本地更靠前 → local-ahead。
            h._json(200, _sync.peek_book(key, force=bool(q.get("force"))))
            return True
        if method == "GET" and rest[:1] == ["sync-state"] and len(rest) == 1:
            h._json(200, _sync.sync_state(key))
            return True
        if method == "GET" and rest[:1] == ["mark"] and len(rest) == 1:
            # 关键词高亮: 语料 (keywords.json) → 词表 + 每类样式。
            # 一个字符都不改 DOM (highlight 模式), 所以进度/划线坐标不受影响。
            from recto.core import reading_mark

            h._json(200, reading_mark.build_payload(key))
            return True
        if method == "GET" and rest[:1] == ["asset"]:
            inner = (q.get("p") or [""])[0]
            try:
                data, ctype = doc.asset(inner)
            except Exception as e:  # noqa: BLE001
                h._json(404, {"error": f"{type(e).__name__}"})
                return True
            h._send(200, data, ctype)
            return True

        body = _read_json(h) if method == "POST" else {}

        if method == "POST" and rest[:1] == ["sync-choice"] and len(rest) == 1:
            # 云端比本地靠前时二选一:
            #   cloud → 把云端位置写进本地库并跳过去
            #   local → 保留本地位置, 之后按常规节奏覆盖云端
            choice = str(body.get("choice") or "").strip().lower()
            if choice == "cloud":
                out = _sync.apply_cloud(key)
            elif choice == "local":
                out = _sync.keep_local(key, push_now=body.get("push", True) is not False)
            else:
                h._json(400, {"error": "choice must be cloud|local"})
                return True
            h._json(200, out)
            return True

        if method == "POST" and rest[:1] == ["progress"]:
            ch = int(body.get("chapter") or 0)
            off = int(body.get("offset") or 0)
            ch = max(0, min(ch, len(doc.chapters) - 1))
            pct = doc.percent(ch, off)
            pos = doc.position(ch, off)
            # sid = 这个阅读器页面实例的随机 id; jump = 目录跳转这类"我就是想回去"的动作;
            # user = 这次写入来自读者的真实动作 (前端在用户动过之后才会保存)。
            # 两者合起来挡住"早上没关的旧标签页把进度写回上午的位置"。
            # 「读完」只认读者真的到了末尾 (at_end), 不认百分比:
            # 最后一章的块粒度可能把 99.76% 抬到 99.9997%, 那不是读完。
            at_end = body.get("end")
            complete = (1 if at_end else 0) if at_end is not None else None
            out = rs.save_progress(key, ch, off, pct, pos, complete=complete,
                                   sid=str(body.get("sid") or "")[:24],
                                   allow_rewind=bool(body.get("jump")))
            if out.get("skipped") == "stale-rewind":
                # 旧会话的落后位置: 不写库, 也不推云端; 让前端跟上真正的位置
                out["sync"] = _sync.sync_state(key)
                out["percent"] = float(out.get("percent") or 0.0)
                h._json(200, {"ok": True, "stale": True, **out})
                return True
            # 推云端放到后台线程 (节流在 sync 里) —— 翻页不能等网络。
            # flush=True 是"离开页面/切后台"的最后一推, 绕过节流。
            try:
                out["library"] = _sync.mirror_reader_progress(
                    key, pct, finished=bool(out.get("complete")),
                    flush=bool(body.get("flush")),
                    # 读者自己动出来的位置 (翻页 / 跳转 / 关页面) 可以覆写云端;
                    # 系统补写 (user=0) 仍旧只前进。
                    user=bool(body.get("user", 1))) or None
                out["sync"] = (out["library"] or {}).get("state") or _sync.sync_state(key)
            except Exception:
                out["library"] = None
            h._json(200, {"ok": True, "percent": pct, "position": pos, **out})
            return True

        if method == "POST" and rest[:1] == ["bookmark"]:
            action = body.get("action") or "add"
            if action == "delete":
                h._json(200, {"ok": rs.del_bookmark(int(body.get("id") or 0))})
                return True
            ch = max(0, min(int(body.get("chapter") or 0), len(doc.chapters) - 1))
            off = max(0, int(body.get("offset") or 0))
            summary = str(body.get("summary") or "")[:400]
            title = doc.chapters[ch].title
            row = rs.add_bookmark(key, ch, off,
                                  summary or f"{title} · {_pct_text(doc.percent(ch, off))}",
                                  title, chapter_label=title,
                                  pct=float(doc.percent(ch, off)) * 100.0)
            h._json(200, {"ok": True, "bookmark": row})
            return True

        if method == "POST" and rest[:1] == ["note"]:
            action = body.get("action") or "add"
            if action == "delete":
                h._json(200, {"ok": rs.del_note(int(body.get("id") or 0))})
                return True
            if action == "update":
                row = rs.update_note(int(body.get("id") or 0),
                                     summary=body.get("summary"),
                                     remark=body.get("remark"),
                                     color=body.get("color"))
                h._json(200, {"ok": bool(row), "note": row})
                return True
            ch = max(0, min(int(body.get("chapter") or 0), len(doc.chapters) - 1))
            s_off = max(0, int(body.get("start") or 0))
            e_off = max(0, int(body.get("end") or s_off))
            summary = str(body.get("summary") or "")
            anchors = json.dumps(body.get("anchors") or {}, ensure_ascii=False)
            row = rs.add_note(key, ch, s_off, e_off, summary,
                              str(body.get("remark") or ""),
                              str(body.get("color") or ""), anchors,
                              chapter_label=doc.chapters[ch].title,
                              pct=float(doc.percent(ch, s_off)) * 100.0)
            h._json(200, {"ok": True, "note": row})
            return True

        if method == "POST" and rest[:1] == ["session"]:
            action = body.get("action") or "beat"
            if action == "start":
                h._json(200, rs.session_start(key))
            elif action == "end":
                h._json(200, rs.session_end(key))
            else:
                h._json(200, rs.session_beat(key, start_ts=body.get("start")))
            return True

        h._json(404, {"error": "not found"})
        return True
    except Exception as e:  # noqa: BLE001 — 阅读器报错不能让整台服务 500
        traceback.print_exc()
        h._json(500, {"error": f"{type(e).__name__}: {e}"})
        return True
