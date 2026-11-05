"""设置 (/settings) —— 顶栏那个圆点进来的地方。

三件事, 都是"用的人"自己会改的:

  1. 昵称 —— 顶栏显示什么 (登录名仍是账号本身)
  2. 口令 —— 改完立即生效, 下次登录用新的
  3. 书库目录 —— 改了会连带书架 / 云端下载落点 / 摘录与进度匹配

外观 (换肤) 只在外观页, 这一页不再重复入口。
"""
from __future__ import annotations

import html
import os
from pathlib import Path

from recto.core import paths, settings as st

from .ui import shell

_CSS = """
.st-grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(300px,1fr))}
.st-fields{display:grid;gap:12px;grid-template-columns:1fr;max-width:360px;margin:2px 0 0}
.st-field{display:flex;flex-direction:column;gap:6px;min-width:0;max-width:100%}
.st-field>span{font-size:var(--rc-fs-xs);letter-spacing:.06em;color:var(--rc-mute)}
.st-field input[type=text],.st-field input[type=password]{
  border:0;background:color-mix(in srgb,var(--rc-fg) 6%,var(--rc-surface));
  min-height:40px;padding:0 12px;border-radius:var(--rc-radius-field);
  color:var(--rc-fg);font:inherit;width:100%;max-width:100%;box-sizing:border-box}
.st-field input:hover{background:color-mix(in srgb,var(--rc-fg) 9%,var(--rc-surface))}
.st-field input:focus{outline:none;
  background:color-mix(in srgb,var(--rc-fg) 11%,var(--rc-surface));
  box-shadow:inset 0 -2px 0 var(--rc-primary)}
.st-note{font-size:var(--rc-fs-xs);color:var(--rc-mute);line-height:1.7}
.st-note code{font-size:.95em}
.st-line{display:flex;align-items:center;gap:10px;flex-wrap:wrap;
  font-size:var(--rc-fs-sm);color:var(--rc-dim);margin:10px 0 0}
.st-line b{color:var(--rc-fg);font-weight:600}
.st-msg{font-size:var(--rc-fs-sm);margin-top:10px;min-height:1.4em}
.st-msg.ok{color:var(--rc-ok,#2e7d32)}
.st-msg.err{color:var(--rc-danger)}
.st-path{font-family:var(--rc-mono);font-size:var(--rc-fs-sm);word-break:break-all}
.st-check{display:flex;align-items:center;gap:8px;font-size:var(--rc-fs-sm);color:var(--rc-dim)}
.st-check input{width:16px;height:16px;accent-color:var(--rc-primary)}
.st-field select,.mk-row select{
  border:0;background:color-mix(in srgb,var(--rc-fg) 6%,var(--rc-surface));
  min-height:40px;padding:0 10px;border-radius:var(--rc-radius-field);
  color:var(--rc-fg);font:inherit;max-width:100%}
.mk-tb{width:100%;border-collapse:collapse;margin-top:12px;font-size:var(--rc-fs-sm)}
.mk-tb th,.mk-tb td{padding:6px 8px;text-align:left;border-bottom:1px solid var(--rc-line)}
.mk-tb th{font-size:var(--rc-fs-xs);letter-spacing:.06em;color:var(--rc-mute);font-weight:500}
.mk-tb input[type=text]{width:11ch;border:0;min-height:32px;padding:0 8px;
  background:color-mix(in srgb,var(--rc-fg) 6%,var(--rc-surface));
  border-radius:var(--rc-radius-field);color:var(--rc-fg);font:inherit;font-size:var(--rc-fs-sm)}
.mk-tb input[type=checkbox]{width:15px;height:15px;accent-color:var(--rc-primary)}
.mk-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:7px 0;
  border-bottom:1px solid var(--rc-line);font-size:var(--rc-fs-sm)}
.mk-row .nm{min-width:180px;flex:1 1 180px;color:var(--rc-fg)}
.mk-row .nm small{display:block;color:var(--rc-mute);font-size:var(--rc-fs-xs);margin-top:2px}
.mk-row .st{color:var(--rc-mute);font-size:var(--rc-fs-xs);min-width:90px}
.mk-row .st.ok{color:var(--rc-ok,#2e7d32)}
.mk-num{width:9ch;border:0;min-height:36px;padding:0 8px;font:inherit;color:var(--rc-fg);
  background:color-mix(in srgb,var(--rc-fg) 6%,var(--rc-surface));border-radius:var(--rc-radius-field)}
"""

_JS = r"""
(function(){
  function $(id){ return document.getElementById(id); }
  function $$(sel, el){ return Array.prototype.slice.call((el || document).querySelectorAll(sel)); }
  function msg(id, text, bad){
    var e = $(id); if (!e) return;
    e.textContent = text || '';
    e.className = 'st-msg' + (text ? (bad ? ' err' : ' ok') : '');
  }
  function post(url, body){
    return fetch(url, {
      method:'POST', credentials:'same-origin',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify(body || {})
    }).then(function(r){
      return r.json().catch(function(){ return {}; }).then(function(j){
        if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
        return j;
      });
    });
  }
  function paintUser(name){
    var el = document.querySelector('.rc-user-name');
    if (el) el.textContent = name || '';
    var av = document.querySelector('.rc-avatar');
    if (av && name) av.textContent = name.slice(0,1).toUpperCase();
  }
  function paintLib(lib){
    if (!lib) return;
    var p = $('st-lib-now'); if (p) p.textContent = lib.dir || '';
    var s = $('st-lib-src'); if (s) s.textContent = lib.source_label || '';
    var n = $('st-lib-books'); if (n) n.textContent = String(lib.books || 0);
    var e = $('st-lib-state'); if (e) e.textContent = lib.note || '';
    var f = $('st-lib'); if (f && !f.value) f.value = lib.dir || '';
  }

  // ---- 阅读高亮 (语料滤镜) ----
  function mkNum(id){ var e = $(id); return e ? e.value : ''; }
  function mkPaint(d){
    if (!d) return;
    var pill = $('mk-pill');
    if (pill) {
      pill.textContent = d.enabled ? '已启用' : '已关闭';
      pill.className = 'pill' + (d.enabled ? '' : ' mute');
    }
    var on = $('mk-on'); if (on) on.checked = !!d.enabled;
    var md = $('mk-mode'); if (md) md.value = d.mode || 'highlight';
    var dirs = $('mk-dirs');
    if (dirs && document.activeElement !== dirs) dirs.value = (d.dirs || []).join(', ');
    var mi = $('mk-minlen'); if (mi) mi.value = d.min_len;
    var mx = $('mk-maxterms'); if (mx) mx.value = d.max_terms;
    var sd = $('mk-digits'); if (sd) sd.checked = !!d.skip_digits;
    var path = $('mk-path'); if (path) path.textContent = d.path || '';

    var tb = $('mk-styles');
    if (tb) {
      var names = {};
      (d.categories || []).forEach(function(c){ names[c.id] = c.name; });
      tb.innerHTML = Object.keys(d.styles || {}).map(function(cid){
        var s = d.styles[cid] || {};
        return '<tr data-cat="' + cid + '">' +
          '<td>' + (names[cid] || cid) + ' <small style="color:var(--rc-mute)">' + cid + '</small></td>' +
          '<td><input type="checkbox" data-mk="enabled"' + (s.enabled ? ' checked' : '') + '></td>' +
          '<td><input type="text" data-mk="bg" value="' + (s.bg || '') + '"></td>' +
          '<td><input type="text" data-mk="color" value="' + (s.color || '') + '"></td>' +
          '<td><input type="checkbox" data-mk="underline"' + (s.underline ? ' checked' : '') + '></td>' +
          '</tr>';
      }).join('');
    }

    var corp = $('mk-corpora');
    if (corp) corp.textContent = (d.corpora || []).length + ' 份';
    var box = $('mk-books');
    if (box) {
      var opts = ['<option value="">自动匹配</option>'].concat((d.corpora || []).map(function(c){
        return '<option value="' + c.file.replace(/"/g, '&quot;') + '">' +
          c.title + ' · ' + c.words + ' 词</option>';
      }));
      var rows = (d.books || []).map(function(b){
        var how = {name:'书名匹配', sidecar:'书库边车', source_epub:'同源文件', bind:'已绑定',
                   bind_path:'已绑定', bind_missing:'绑定失效', none:'未匹配'}[b.how] || b.how || '未匹配';
        var ok = b.file ? ' ok' : '';
        var sel = b.bound ? '<option value="' + b.bound.replace(/"/g, '&quot;') + '" selected>' +
                    b.bound + '</option>' : '';
        return '<div class="mk-row" data-book="' + b.key + '">' +
          '<div class="nm">' + b.title + '<small>' + (b.author || '') + '</small></div>' +
          '<div class="st' + ok + '">' + how + (b.file ? ' · ' + b.words + ' 词' : '') + '</div>' +
          '<select data-mk-bind>' + sel + opts.join('') + '</select></div>';
      });
      box.innerHTML = rows.length ? rows.join('') :
        '<div class="st-note">书库还是空的。</div>';
      // 选中态: 绑定值或当前解析到的那份
      $$('.mk-row', box).forEach(function(row){
        var b = (d.books || []).filter(function(x){ return x.key === row.dataset.book; })[0];
        var sel = row.querySelector('[data-mk-bind]');
        if (!sel || !b) return;
        if (b.bound) sel.value = b.bound;
        else if (b.file) sel.value = b.file;
        else sel.value = '';
      });
    }
  }
  function mkLoad(){
    fetch('/api/mark', { credentials: 'same-origin' })
      .then(function(r){ return r.json(); })
      .then(function(d){ mkPaint(d); })
      .catch(function(){ var p = $('mk-pill'); if (p) p.textContent = '读取失败'; });
  }
  function mkCollect(){
    var styles = {};
    $$('#mk-styles tr').forEach(function(tr){
      var g = function(k){ return tr.querySelector('[data-mk="' + k + '"]'); };
      styles[tr.dataset.cat] = {
        enabled: g('enabled').checked,
        bg: g('bg').value.trim(),
        color: g('color').value.trim(),
        underline: g('underline').checked
      };
    });
    return {
      enabled: $('mk-on').checked,
      mode: $('mk-mode').value,
      dirs: ($('mk-dirs').value || '').split(',').map(function(s){ return s.trim(); })
              .filter(function(s){ return s; }),
      min_len: parseInt(mkNum('mk-minlen'), 10) || 2,
      max_terms: parseInt(mkNum('mk-maxterms'), 10) || 6000,
      skip_digits: $('mk-digits').checked,
      styles: styles
    };
  }

  document.addEventListener('change', function(ev){
    var sel = ev.target.closest ? ev.target.closest('[data-mk-bind]') : null;
    if (!sel) return;
    var row = sel.closest('.mk-row');
    if (!row) return;
    post('/api/mark', { action: 'bind', book: row.dataset.book, file: sel.value })
      .then(function(r){ mkPaint(r); msg('mk-msg', '绑定已更新'); })
      .catch(function(e){ msg('mk-msg', e.message, true); });
  });

  document.addEventListener('click', function(ev){
    var b = ev.target.closest ? ev.target.closest('[data-st]') : null;
    if (!b) return;
    ev.preventDefault();
    var act = b.dataset.st;
    b.disabled = true;
    var done = function(){ b.disabled = false; };

    if (act === 'nick') {
      post('/api/settings', { nickname: ($('st-nick') || {}).value || '' }).then(function(r){
        paintUser(r.nickname || '');
        msg('st-nick-msg', r.nickname ? '昵称已保存' : '昵称已清空, 顶栏改回登录名');
        done();
      }).catch(function(e){ msg('st-nick-msg', e.message, true); done(); });
      return;
    }
    if (act === 'lib') {
      var v = ($('st-lib') || {}).value || '';
      post('/api/settings', { library_dir: v }).then(function(r){
        paintLib(r.library);
        msg('st-lib-msg', '书库目录已切换, 书架已重扫 (' + ((r.library && r.library.books) || 0) + ' 本)');
        done();
      }).catch(function(e){ msg('st-lib-msg', e.message, true); done(); });
      return;
    }
    if (act === 'lib-reset') {
      post('/api/settings', { library_dir: '' }).then(function(r){
        paintLib(r.library);
        msg('st-lib-msg', '已回到 config.toml 的写法');
        done();
      }).catch(function(e){ msg('st-lib-msg', e.message, true); done(); });
      return;
    }
    if (act === 'mark' || act === 'mark-reset') {
      var body = act === 'mark' ? { action: 'save', conf: mkCollect() } : { action: 'reset' };
      post('/api/mark', body).then(function(r){
        mkPaint(r);
        msg('mk-msg', act === 'mark' ? '已保存 (版本 ' + r.version + ')' : '已恢复默认');
        done();
      }).catch(function(e){ msg('mk-msg', e.message, true); done(); });
      return;
    }
    if (act === 'pass') {
      var old = ($('st-old') || {}).value || '';
      var n1 = ($('st-new') || {}).value || '';
      var n2 = ($('st-new2') || {}).value || '';
      if (n1 !== n2) { msg('st-pass-msg', '两次输入的新口令不一致', true); done(); return; }
      post('/api/settings/password', { old: old, new: n1 }).then(function(){
        msg('st-pass-msg', '口令已更新, 下次登录用新口令');
        ['st-old','st-new','st-new2'].forEach(function(k){ var el = $(k); if (el) el.value = ''; });
        done();
      }).catch(function(e){ msg('st-pass-msg', e.message, true); done(); });
      return;
    }
    done();
  });

  // 阅读高亮卡: 打开页面就拉一次 (与其它卡片各自独立, 互不阻塞)
  mkLoad();
})();
"""


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def library_state() -> dict:
    """书库目录: 现在生效的、来源、能不能用、几本书。"""
    d = st.library_dir()
    src = st.library_source()
    p = Path(d)
    exists = p.exists()
    try:
        writable = exists and os.access(p, os.W_OK)
    except OSError:
        writable = False
    note = "目录可用" if (exists and writable) else (
        "目录不存在" if not exists else "目录不可写")
    books = 0
    try:
        from recto.services import reader_state

        books = len(reader_state.list_books())
    except Exception:  # noqa: BLE001 — 书库读不出来不该挡住设置页
        books = 0
    return {"dir": d, "source": src, "source_label": st.SOURCE_LABEL.get(src, src),
            "exists": exists, "writable": writable, "note": note, "books": books,
            "overridden": src == "settings"}


def summary() -> dict:
    return {"ok": True, "nickname": st.nickname(),
            "login": _login_name(), "library": library_state(),
            "password_source": _password_source_label()}


def _login_name() -> str:
    """真正的登录用户名 (来自配置, 缺省初始默认 root)。"""
    try:
        from recto.app import webauth

        return webauth.login_user()
    except Exception:  # noqa: BLE001
        return ""


def _password_source_label() -> str:
    try:
        from recto.app import webauth

        src = webauth.password_source()
    except Exception:  # noqa: BLE001
        return ""
    return {"env": "环境变量 RECTO_AUTH_PASSWORD",
            "config": "config.toml 的 [auth] password",
            "file": "data/admin-password (本页可改)"}.get(src, src)


# ---------------------------------------------------------------- 页面

def page_html(user: str | None = None) -> str:
    lib = library_state()
    nick = st.nickname()
    login = _login_name() or (user or "").strip()
    src_pill = "pill" if lib["source"] == "settings" else "pill mute"
    can_edit = lib["source"] != "env"

    body = [
        "<style>" + _CSS + "</style>",
        '<div class="st-grid">',

        # --- 账号 ---
        '<details class="sec" open><summary>账号</summary><div class="sec-body">'
        '<div class="st-field"><span>昵称 (顶栏显示)</span>'
        f'<input id="st-nick" type="text" value="{_e(nick)}" maxlength="24" '
        f'placeholder="{_e(login or "你的名字")}" autocomplete="off"></div>'
        '<div class="row" style="margin-top:10px">'
        '<button class="primary" data-st="nick">保存昵称</button>'
        f'<span class="st-note">登录名 <b>{_e(login or "-")}</b>，昵称只改顶栏显示。</span>'
        "</div>"
        '<div class="st-note" style="margin-top:8px">登录名不在本页改: '
        "config.toml 的 <code>[auth] user</code> (环境变量 "
        "<code>RECTO_AUTH_USER</code> 优先); 口令在下面「修改口令」改。</div>"
        '<div id="st-nick-msg" class="st-msg"></div>'
        "</div></details>",

        # --- 口令 ---
        '<details class="sec"><summary>修改口令</summary><div class="sec-body">'
        '<div class="st-fields">'
        '<label class="st-field"><span>当前口令</span>'
        '<input id="st-old" type="password" autocomplete="current-password"></label>'
        '<label class="st-field"><span>新口令 (至少 6 位)</span>'
        '<input id="st-new" type="password" autocomplete="new-password"></label>'
        '<label class="st-field"><span>再输一次</span>'
        '<input id="st-new2" type="password" autocomplete="new-password"></label>'
        "</div>"
        '<div class="row" style="margin-top:12px">'
        '<button class="primary" data-st="pass">更新口令</button>'
        f'<span class="st-note">当前来源: {_e(_password_source_label())}</span></div>'
        '<div class="st-note" style="margin-top:8px">说明: 本页只校验「当前口令」, 与登录名无关。忘了旧口令, 可在服务器执行 <code>python3 -m recto.cli passwd</code> 重置。</div>'
        '<div id="st-pass-msg" class="st-msg"></div>'
        "</div></details>",

        # --- 书库 ---
        '<details class="sec" open><summary>书库目录</summary><div class="sec-body">'
        '<label class="st-field"><span>原书所在目录 (epub / txt, 递归扫描)</span>'
        f'<input id="st-lib" type="text" value="{_e(lib["dir"])}" '
        'placeholder="例如 /mnt/books" spellcheck="false"></label>'
        '<div class="row" style="margin-top:12px">'
        '<button class="primary" data-st="lib">换到这个目录</button>'
        '<button data-st="lib-reset">回到 config.toml</button></div>'
        f'<div class="st-line">当前生效 <b class="st-path" id="st-lib-now">{_e(lib["dir"])}</b>'
        f'<span class="{src_pill}" id="st-lib-src">{_e(lib["source_label"])}</span></div>'
        '<div class="st-line">书库 <b id="st-lib-books">' + str(lib["books"]) + "</b> 本"
        f'<span class="st-note" id="st-lib-state">{_e(lib["note"])}</span></div>'
        '<div id="st-lib-msg" class="st-msg"></div>'
        '<div class="st-note" style="margin-top:12px">换了目录之后会连带影响: '
        "书架扫描 (只列当前目录里的书), 云端同步的下载落点, "
        "以及摘录 / 阅读进度的匹配。旧目录里的记录不会丢 —— "
        "切回去书和进度都还在。</div>"
        + ("" if can_edit else
           '<div class="st-note" style="margin-top:8px">'
           "当前书库目录由环境变量 RECTO_LIBRARY_DIR 决定, 本页改了也不生效。</div>")
        + "</div></details>",

        # --- 阅读高亮 (语料 → 阅读器) ---
        '<details class="sec" id="mark"><summary>阅读高亮 '
        '<span class="pill mute" id="mk-pill">读取中…</span></summary><div class="sec-body">'
        '<div class="st-note">把「语料分解 json」里的关键词铺到阅读器做底色。'
        '默认 highlight 模式<b>不改动一个字符</b> —— 阅读进度与划线坐标都不受影响；'
        'span 模式才能加粗/圆角，代价是改 DOM（翻页模式会重排一次）。</div>'
        '<div class="row" style="margin-top:12px">'
        '<label class="st-check"><input type="checkbox" id="mk-on"> 启用</label>'
        '<label class="st-check">渲染方式 '
        '<select id="mk-mode"><option value="highlight">highlight（不改 DOM）</option>'
        '<option value="span">span（可加粗/圆角）</option></select></label></div>'
        '<label class="st-field" style="margin-top:10px"><span>语料目录（多个用逗号分隔；'
        '留空 = config.toml 的 [corpus] dir）</span>'
        '<input id="mk-dirs" type="text" spellcheck="false"></label>'
        '<div class="row" style="margin-top:10px">'
        '<label class="st-check">最短词长 <input class="mk-num" id="mk-minlen" type="number" '
        'min="1" max="12" step="1"></label>'
        '<label class="st-check">最多词数 <input class="mk-num" id="mk-maxterms" type="number" '
        'min="50" max="6000" step="50"></label>'
        '<label class="st-check"><input type="checkbox" id="mk-digits"> 跳过纯数字词</label></div>'
        '<div class="st-note" style="margin-top:14px">分类配色（highlight 模式只认底色/文字色/下划线）</div>'
        '<table class="mk-tb"><thead><tr><th>分类</th><th>启用</th><th>底色</th>'
        '<th>文字色</th><th>下划线</th></tr></thead><tbody id="mk-styles"></tbody></table>'
        '<div class="row" style="margin-top:12px">'
        '<button class="primary" data-st="mark">保存</button>'
        '<button data-st="mark-reset">恢复默认</button>'
        '<span id="mk-msg" class="st-msg"></span></div>'
        '<div class="st-note" style="margin-top:16px">每本书用哪份语料 '
        '<span class="pill mute" id="mk-corpora">0 份</span></div>'
        '<div id="mk-books"><div class="st-note">读取中…</div></div>'
        '<div class="st-note" style="margin-top:10px">书名匹配宁可漏也不错配（同一个作者的另一本书'
        '很常见）。匹配不上时在下面给这本书指一份；书库里放 <code>&lt;书名&gt;.keywords.json</code> '
        '也会被自动认出来。</div>'
        '<div class="st-note" style="margin-top:6px">文件: <span class="st-path" id="mk-path"></span></div>'
        "</div></details>",

        "</div>",
        "<script>" + _JS + "</script>",
    ]
    return shell.document(title="设置", body="".join(body), active="", user=user)


# ---------------------------------------------------------------- API

def _norm_dir(raw: str) -> tuple[bool, str, str]:
    """校验 / 建目录。→ (ok, 绝对路径, 错误)"""
    s = (raw or "").strip()
    if not s:
        return False, "", "目录不能为空"
    p = Path(os.path.expanduser(s))
    if not p.is_absolute():
        return False, "", "要写绝对路径 (例如 /mnt/books)"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, "", f"建不了这个目录: {e}"
    if not p.is_dir():
        return False, "", "这不是一个目录"
    if not os.access(p, os.W_OK):
        return False, "", "目录不可写 (云端下载要往里写文件)"
    if str(p.resolve()) == str(Path(paths.PATHS.data_dir).resolve()):
        return False, "", "书库目录不能就是数据目录 (会和状态库混在一起)"
    return True, str(p.resolve()), ""


def _reload_runtime(rescan: bool) -> int:
    """把新配置推到当前进程: 配置单例 / 路径常量 / 书库重扫。返回书库本数。"""
    paths.reload()
    try:
        paths.ensure_dirs()
    except OSError:
        pass
    if not rescan:
        return 0
    from recto.services import reader_state

    rows = reader_state.scan()
    return len(rows)


def api_post(body: dict) -> dict:
    body = body or {}
    nickname_given = "nickname" in body
    lib_given = "library_dir" in body
    changed_lib = False

    if nickname_given:
        nick = str(body.get("nickname") or "").strip()
        if len(nick) > 24:
            raise ValueError("昵称最多 24 个字")
        if any(ord(c) < 32 for c in nick):
            raise ValueError("昵称里不要放控制字符")
        st.update(nickname=nick)

    if lib_given:
        raw = str(body.get("library_dir") or "").strip()
        if not raw:
            st.update(library_dir="")
            changed_lib = True
        else:
            ok, val, err = _norm_dir(raw)
            if not ok:
                raise ValueError(err)
            st.update(library_dir=val)
            changed_lib = True

    if nickname_given or lib_given:
        _reload_runtime(rescan=changed_lib)

    out = summary()
    out["changed"] = {"nickname": nickname_given, "library": lib_given}
    return out


def api_password(body: dict, user: str | None) -> dict:
    """改口令 —— 只校验「当前口令」, 不校验用户名。

    会话已经证明是登录用户了; 再拿 cookie 里的用户名去比 `[auth] user` 只会
    在两种情况下误伤: (a) 升级前留下的旧 cookie 里是老的登录名,
    (b) 用户名改了。两者都会让「旧口令永远不对」。
    """
    from recto.app import webauth

    body = body or {}
    old = str(body.get("old") or "")
    new = str(body.get("new") or "")
    if webauth.password_source() == "env":
        # 环境变量优先级最高: 改了文件也不生效, 不如直说 (也别白验一遍旧口令)
        raise ValueError("口令由环境变量 RECTO_AUTH_PASSWORD 提供 —— 请改环境变量, "
                         "或去掉它再用本页修改")
    if webauth.enabled() and not webauth.check_password(old):
        raise ValueError("当前口令不对 (口令来源: " + _password_source_label() + ")。"
                         "忘了旧口令, 可在服务器执行 python3 -m recto.cli passwd 重置")
    ok, err = webauth.set_password(new)
    if not ok:
        raise ValueError(err or "改不了口令")
    return {"ok": True, "login": webauth.login_user(),
            "password_source": _password_source_label()}


# ---------------------------------------------------------------- 阅读高亮

def mark_summary() -> dict:
    """设置页「阅读高亮」卡: 配置 + 语料清单 + 每本书的匹配情况。"""
    from recto.core import reading_mark

    return reading_mark.summary()


def mark_post(body: dict) -> dict:
    """保存 / 恢复默认 / 绑定语料。

    只接三个动作, 别的字段 (stop / only / bind 的其余键) 一律从当前配置继承 ——
    页面不该因为"没提交某个字段"就把用户手写的配置抹掉。
    """
    from recto.core import reading_mark

    body = body or {}
    act = str(body.get("action") or "save").strip()

    if act == "reset":
        reading_mark.reset()
        return mark_summary()

    if act == "bind":
        book = str(body.get("book") or "").strip()
        if not book:
            raise ValueError("绑定要指定一本书")
        reading_mark.set_bind(book, str(body.get("file") or "").strip())
        return mark_summary()

    if act != "save":
        raise ValueError(f"不认识的动作: {act}")

    conf_in = body.get("conf") if isinstance(body.get("conf"), dict) else {}
    cur = reading_mark.load()
    merged = dict(cur)
    for k in ("enabled", "mode", "dirs", "min_len", "max_terms", "skip_digits"):
        if k in conf_in:
            merged[k] = conf_in[k]
    styles_in = conf_in.get("styles") if isinstance(conf_in.get("styles"), dict) else {}
    styles = dict(cur.get("styles") or {})
    for cid, st in styles_in.items():
        if not isinstance(st, dict):
            continue
        slot = dict(styles.get(cid) or {})
        for k in ("enabled", "bg", "color", "underline"):
            if k in st:
                slot[k] = st[k]
        styles[str(cid)] = slot
    merged["styles"] = styles
    reading_mark.save(merged)
    return mark_summary()
