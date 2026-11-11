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
.st-grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));
  align-items:start}
.st-grid>.sec.wide{grid-column:1/-1}
.st-fields{display:grid;gap:12px;grid-template-columns:1fr;max-width:360px;margin:2px 0 0}
.st-field{display:flex;flex-direction:column;gap:6px;min-width:0;max-width:100%}
.st-field>span{font-size:var(--rc-fs-xs);letter-spacing:.06em;color:var(--rc-mute)}
.st-field input[type=text],.st-field input[type=password],.st-in{
  -webkit-appearance:none;appearance:none;
  border:0;background:color-mix(in srgb,var(--rc-fg) 6%,var(--rc-surface));
  min-height:40px;padding:0 12px;border-radius:var(--rc-radius-field);
  color:var(--rc-fg);font:inherit;font-size:var(--rc-fs-sm);
  width:100%;max-width:100%;min-width:0;box-sizing:border-box}
.st-field input:hover,.st-in:hover{background:color-mix(in srgb,var(--rc-fg) 9%,var(--rc-surface))}
.st-field input:focus,.st-in:focus{outline:none;
  background:color-mix(in srgb,var(--rc-fg) 11%,var(--rc-surface));
  box-shadow:inset 0 -2px 0 var(--rc-primary)}
.st-field select,.st-in select{border:0}
select.st-in{padding-right:28px}
input.st-in[type=number]{-moz-appearance:textfield;text-align:right;
  font-variant-numeric:tabular-nums;width:6.4ch;padding:0 8px}
input.st-in[type=number]::-webkit-outer-spin-button,
input.st-in[type=number]::-webkit-inner-spin-button{-webkit-appearance:none;margin:0}

/* 一行字段: 左侧文字说明, 右侧固定宽控件 */
.st-row{display:flex;align-items:center;gap:10px;min-height:40px;min-width:0}
.st-row>span{flex:1 1 auto;min-width:0;font-size:var(--rc-fs-sm);color:var(--rc-dim);
  display:flex;flex-direction:column;gap:2px}
.st-row>span small{color:var(--rc-mute);font-size:var(--rc-fs-xs);line-height:1.5}
.st-row>em{font-style:normal;font-size:var(--rc-fs-xs);color:var(--rc-mute);flex:0 0 auto}
.st-row>b{font-weight:600;color:var(--rc-fg);font-variant-numeric:tabular-nums}

/* 开关行 (Material 3 switch): 文字在左, 开关在右 */
.st-sw{display:flex;align-items:center;gap:14px;min-height:46px;padding:8px 0;min-width:0;
  border-bottom:1px solid var(--rc-line);cursor:pointer}
.st-sw:last-child{border-bottom:0}
.st-sw>span{flex:1 1 auto;min-width:0;font-size:var(--rc-fs-sm);color:var(--rc-fg);
  display:flex;flex-direction:column;gap:2px}
.st-sw>span small{color:var(--rc-mute);font-size:var(--rc-fs-xs);line-height:1.5}
input.m3[type=checkbox]{-webkit-appearance:none;appearance:none;
  width:42px;height:24px;min-height:0;flex:0 0 auto;margin:0;padding:0;border:0;
  border-radius:999px;cursor:pointer;position:relative;
  background:color-mix(in srgb,var(--rc-fg) 26%,transparent);
  transition:background .16s ease}
input.m3[type=checkbox]::after{content:"";position:absolute;left:2px;top:2px;
  width:20px;height:20px;border-radius:50%;background:#fff;
  box-shadow:0 1px 2px rgba(0,0,0,.24);transition:transform .16s ease}
input.m3[type=checkbox]:checked{background:var(--rc-primary)}
input.m3[type=checkbox]:checked::after{transform:translateX(18px)}
input.m3[type=checkbox]:focus-visible{outline:2px solid color-mix(in srgb,var(--rc-primary) 60%,transparent);
  outline-offset:2px}
.st-sw.compact{min-height:0;padding:0;border:0;gap:0}

.st-two{display:grid;gap:6px 28px;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));
  margin-top:6px;align-items:start}
.st-block{min-width:0}
.st-h{font-size:var(--rc-fs-xs);letter-spacing:.08em;color:var(--rc-mute);
  margin:16px 0 6px;text-transform:none}
.st-block>.st-h:first-child{margin-top:0}

.st-note{font-size:var(--rc-fs-xs);color:var(--rc-mute);line-height:1.7}
.st-note code{font-size:.95em}
.st-line{display:flex;align-items:center;gap:10px;flex-wrap:wrap;
  font-size:var(--rc-fs-sm);color:var(--rc-dim);margin:10px 0 0}
.st-line b{color:var(--rc-fg);font-weight:600}
.st-msg{font-size:var(--rc-fs-sm);margin-top:10px;min-height:1.4em}
.st-msg.ok{color:var(--rc-ok,#2e7d32)}
.st-msg.err{color:var(--rc-danger)}
.st-path{font-family:var(--rc-mono);font-size:var(--rc-fs-sm);word-break:break-all}
.st-field select,.mk-row select{
  border:0;background:color-mix(in srgb,var(--rc-fg) 6%,var(--rc-surface));
  min-height:40px;padding:0 28px 0 12px;border-radius:var(--rc-radius-field);
  color:var(--rc-fg);font:inherit;font-size:var(--rc-fs-sm);max-width:100%}

/* --- 分类配色: 一张分类一张小卡, 颜色用色板点选 --- */
.mk-styles{display:grid;gap:10px;grid-template-columns:repeat(auto-fill,minmax(268px,1fr));
  margin-top:8px}
.mk-style{border:1px solid var(--rc-line);border-radius:var(--rc-radius-card,12px);
  padding:10px 12px 8px;min-width:0;background:color-mix(in srgb,var(--rc-fg) 2%,transparent)}
.mk-style-hd{display:flex;align-items:center;gap:10px;min-height:26px}
.mk-style-t{flex:1 1 auto;min-width:0;font-size:var(--rc-fs-sm);color:var(--rc-fg);
  font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mk-style-t small{font-weight:400;color:var(--rc-mute);font-size:var(--rc-fs-xs);
  margin-left:4px;letter-spacing:.04em}
.mk-col{display:flex;align-items:center;gap:10px;min-height:32px;margin-top:2px;min-width:0}
.mk-lbl{flex:0 0 auto;min-width:40px;white-space:nowrap;font-size:var(--rc-fs-xs);
  color:var(--rc-mute)}
.sw-wrap{display:flex;align-items:center;gap:4px;flex-wrap:wrap;min-width:0}
button.sw{-webkit-appearance:none;appearance:none;
  width:20px;height:20px;min-height:0;padding:0;flex:0 0 auto;
  border-radius:50%;border:1px solid color-mix(in srgb,var(--rc-fg) 18%,transparent);
  background:var(--c,var(--rc-surface));cursor:pointer;position:relative;
  box-shadow:none;transition:transform .12s ease}
button.sw:hover{transform:scale(1.12)}
button.sw[aria-pressed="true"]{box-shadow:0 0 0 2px var(--rc-surface),0 0 0 3.5px var(--rc-primary)}
button.sw.none{background:var(--rc-surface)}
button.sw.none::before{content:"";position:absolute;left:-1px;right:-1px;top:50%;height:1.5px;
  transform:rotate(-45deg);background:color-mix(in srgb,var(--rc-fg) 45%,transparent)}
button.sw.none[aria-pressed="true"]::after{content:"";position:absolute;left:50%;top:50%;
  width:6px;height:6px;margin:-3px 0 0 -3px;border-radius:50%;background:var(--rc-primary)}
.sw.pick{position:relative;display:inline-flex;align-items:center;justify-content:center;
  width:20px;height:20px;flex:0 0 auto;border-radius:50%;cursor:pointer;
  border:1px solid color-mix(in srgb,var(--rc-fg) 18%,transparent);
  background:var(--c,conic-gradient(#f45b69,#f7b267,#f7e26b,#6fcf97,#56ccf2,#9b8cff,#f45b69))}
.sw.pick.is-on{box-shadow:0 0 0 2px var(--rc-surface),0 0 0 3.5px var(--rc-primary)}
.sw.pick input{position:absolute;inset:0;width:100%;height:100%;opacity:0;
  padding:0;border:0;min-height:0;cursor:pointer}
.sw-a{-webkit-appearance:none;appearance:none;border:0;height:20px;min-height:20px;padding:0 2px;
  line-height:1;
  background:transparent;color:var(--rc-mute);font:inherit;font-size:var(--rc-fs-xs);
  cursor:pointer;border-radius:6px;flex:0 0 auto}
.sw-a:hover{background:color-mix(in srgb,var(--rc-fg) 8%,transparent)}
.mk-val{font-family:var(--rc-mono);font-size:11px;color:var(--rc-mute);
  margin:0 0 2px 50px;min-height:1.2em;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis;max-width:100%}
.mk-under{display:flex;align-items:center;gap:8px;min-height:26px;margin:2px 0 4px 0;
  font-size:var(--rc-fs-xs);color:var(--rc-dim);cursor:pointer}
.mk-under input[type=checkbox]{width:15px;height:15px;min-height:0;accent-color:var(--rc-primary)}

.mk-opts{display:grid;gap:6px 22px;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));
  margin-top:4px;align-items:center}
.mk-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:7px 0;
  border-bottom:1px solid var(--rc-line);font-size:var(--rc-fs-sm)}
.mk-row .nm{min-width:180px;flex:1 1 180px;color:var(--rc-fg)}
.mk-row .nm small{display:block;color:var(--rc-mute);font-size:var(--rc-fs-xs);margin-top:2px}
.mk-row .st{color:var(--rc-mute);font-size:var(--rc-fs-xs);min-width:90px}
.mk-row .st.ok{color:var(--rc-ok,#2e7d32)}
.mk-row select{flex:0 1 240px;min-width:0}
@media (max-width:560px){
  .mk-lbl{min-width:34px}
  .mk-val{margin-left:44px}
  .mk-row select{flex:1 1 100%}
}
.st-row>.st-in{flex:0 1 auto;width:auto;min-width:0}
select.st-in{flex:0 1 200px;min-width:120px}
.st-h small{font-weight:400;letter-spacing:0;color:var(--rc-mute);font-size:var(--rc-fs-xs);
  margin-left:6px}
.mk-opts .st-sw{border-bottom:0;padding:0;min-height:44px}
@media (max-width:560px){
  select.st-in{flex:1 1 100%}
  .st-two{grid-template-columns:1fr}
}
"""

_JS = r"""
(function(){
  function $(id){ return document.getElementById(id); }
  function $$(sel, el){ return Array.prototype.slice.call((el || document).querySelectorAll(sel)); }
  function esc(s){ return String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/"/g,'&quot;')
    .replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
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
      if (r.status === 403) { location.href = '/first-run'; return {}; }
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

  // ---- 云端同步 (进度) ----
  var SY_LABEL = { env: '环境变量', settings: '设置页', config: 'config.toml' };
  function syPaint(d){
    if (!d) return;
    var on = $('sy-auto'); if (on) on.checked = !!d.auto;
    var pu = $('sy-pull'); if (pu) pu.checked = !!d.pull_on_open;
    var fl = $('sy-flush'); if (fl) fl.checked = !!d.flush_on_hide;
    var st = $('sy-stamp'); if (st) st.checked = !!d.stamp;
    var gp = $('sy-gap'); if (gp && document.activeElement !== gp) gp.value = d.push_debounce_sec;
    var pg = $('sy-pullgap'); if (pg) pg.textContent = d.pull_gap_sec;
    var tk = d.tick || {};
    var ton = $('sy-tick'); if (ton) ton.checked = !!tk.enabled;
    var tse = $('sy-tick-sec');
    if (tse && document.activeElement !== tse) tse.value = tk.interval_sec || 60;
    var tn = $('sy-tick-notes'); if (tn) tn.checked = !!tk.notes;
    var tt = $('sy-tick-time'); if (tt) tt.checked = !!tk.time;
    var tst = $('sy-tick-state');
    if (tst) {
      if (!tk.enabled) tst.textContent = '已关闭';
      else {
        var bits = ['每 ' + (tk.interval_sec || 0) + ' 秒'];
        if (tk.settle) bits.push('含收尾补推 ' + (tk.settle_sec || 180) + 's');
        if (!tk.running) bits.push('心跳未运行');
        // 动作名翻成人话 (settle 是"补上最后那一小截", 不是"在读")
        var ACT = { progress: '进度', settle: '收尾', notes: '划线', time: '时长' };
        var actTxt = function (list) {
          return (list || []).map(function (a) { return ACT[a] || a; }).join(' / ');
        };
        if (tk.last_at) {
          bits.push('上次 ' + String(tk.last_at).slice(11, 19));
          if ((tk.last_actions || []).length) {
            bits.push('同步了 ' + actTxt(tk.last_actions));
          } else if ((tk.last_keys || []).length) {
            bits.push((tk.last_keys || []).length + ' 本无变化');
          } else {
            bits.push('最近没在读');
          }
        } else bits.push('还没跑过');
        tst.textContent = bits.join(' · ');
      }
    }
    var pill = $('sy-pill');
    if (pill) {
      var bits = [];
      if (!d.auto) bits.push('已关闭');
      else {
        bits.push('已启用');
        if (d.stamp) bits.push('右下角显示');
        if (tk.enabled) bits.push('定时 ' + (tk.interval_sec || 0) + 's');
      }
      pill.textContent = bits.join(' · ');
      pill.className = 'pill' + (d.auto ? '' : ' mute');
    }
    var srcBox = $('sy-src');
    if (srcBox && d.source) {
      var names = [];
      ['auto','pull_on_open','flush_on_hide','stamp','push_debounce_sec','interval_sec'].forEach(function(k){
        var v = d.source[k];
        var label = SY_LABEL[v] || v;
        if (names.indexOf(label) < 0) names.push(label);
      });
      srcBox.textContent = names.join(' / ');
    }
  }
  function syLoad(){
    fetch('/api/settings', { credentials: 'same-origin' })
      .then(function(r){ return r.json(); })
      .then(function(d){ syPaint(d.sync); })
      .catch(function(){ var p = $('sy-pill'); if (p) p.textContent = '读取失败'; });
  }
  function syCollect(){
    return {
      auto: !!($('sy-auto') || {}).checked,
      pull_on_open: !!($('sy-pull') || {}).checked,
      flush_on_hide: !!($('sy-flush') || {}).checked,
      stamp: !!($('sy-stamp') || {}).checked,
      push_debounce_sec: parseInt(($('sy-gap') || {}).value, 10) || 12,
      interval_on: !!($('sy-tick') || {}).checked,
      interval_sec: parseInt(($('sy-tick-sec') || {}).value, 10) || 60,
      tick_notes: !!($('sy-tick-notes') || {}).checked,
      tick_time: !!($('sy-tick-time') || {}).checked
    };
  }

  // ---- 颜色 ----
  // 底色默认都是半透明: 阅读器有浅色/深色两套主题, 半透明在两边都只是"一层淡色"。
  var BG_SW = [
    ['rgba(255, 196, 64, .42)', '黄'],
    ['rgba(88, 166, 255, .30)', '蓝'],
    ['rgba(74, 200, 138, .32)', '绿'],
    ['rgba(255, 138, 80, .34)', '橙'],
    ['rgba(196, 128, 255, .34)', '紫'],
    ['rgba(64, 210, 210, .30)', '青'],
    ['rgba(160, 168, 180, .30)', '灰'],
    ['rgba(255, 110, 150, .30)', '粉']
  ];
  var FG_SW = [
    ['#8a5a00', '棕'], ['#0b5cad', '蓝'], ['#12693f', '绿'], ['#a33a12', '橙'],
    ['#6b2fb3', '紫'], ['#0d6d6d', '青'], ['#5a5f68', '灰'], ['#b01747', '红']
  ];
  var ALPHAS = [0.15, 0.25, 0.35, 0.45, 0.6, 1];

  function parseColor(v){
    v = String(v || '').trim();
    if (!v) return null;
    var m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(v);
    if (m) {
      var h = m[1];
      if (h.length === 3) h = h[0]+h[0]+h[1]+h[1]+h[2]+h[2];
      return {r: parseInt(h.slice(0,2),16), g: parseInt(h.slice(2,4),16),
              b: parseInt(h.slice(4,6),16), a: 1};
    }
    m = /^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)\s*(?:[,\/]\s*([\d.]+%?)\s*)?\)$/i.exec(v);
    if (m) {
      var a = m[4] == null ? 1 : (/ %?$/.test(m[4]) && m[4].indexOf('%') > 0
        ? parseFloat(m[4]) / 100 : parseFloat(m[4]));
      if (!isFinite(a)) a = 1;
      return {r: +m[1], g: +m[2], b: +m[3], a: Math.max(0, Math.min(1, a))};
    }
    return null;
  }
  function hx(n){ var s = Math.round(n).toString(16); return s.length < 2 ? '0' + s : s; }
  function toHex(c){ return '#' + hx(c.r) + hx(c.g) + hx(c.b); }
  function toRgba(c, a){
    if (a == null) a = c.a;
    a = Math.round(a * 100) / 100;
    var at = String(a);
    if (at.indexOf('0.') === 0) at = at.slice(1);
    return 'rgba(' + Math.round(c.r) + ', ' + Math.round(c.g) + ', ' + Math.round(c.b) +
      ', ' + at + ')';
  }
  function nearAlpha(a){
    var best = ALPHAS[0], d = 9;
    ALPHAS.forEach(function(x){ var dd = Math.abs(x - a); if (dd < d) { d = dd; best = x; } });
    return best;
  }

  function swatches(kind){
    return kind === 'bg' ? BG_SW : FG_SW;
  }
  function colorCell(kind, val, label){
    var html = '<div class="mk-col"><span class="mk-lbl">' + label + '</span>' +
      '<div class="sw-wrap" data-mk="' + kind + '" data-val="' + esc(val) + '">';
    swatches(kind).forEach(function(it){
      html += '<button type="button" class="sw" data-v="' + esc(it[0]) + '" title="' + it[1] +
        '" style="--c:' + it[0] + '" aria-pressed="false"></button>';
    });
    html += '<button type="button" class="sw none" data-v="" title="不设颜色" ' +
      'aria-pressed="false"></button>';
    html += '<label class="sw pick" title="自定义颜色 (打开调色板)">' +
      '<input type="color" value="#ffc440" aria-label="自定义颜色"></label>';
    if (kind === 'bg') {
      html += '<select class="sw-a" title="不透明度" aria-label="不透明度">' +
        ALPHAS.map(function(a){
          return '<option value="' + a + '">' + Math.round(a * 100) + '%</option>';
        }).join('') + '</select>';
    }
    html += '</div></div>';
    html += '<div class="mk-val" data-mk-val="' + kind + '"></div>';
    return html;
  }
  function paintWrap(wrap){
    if (!wrap) return;
    var val = String(wrap.dataset.val || '').trim();
    var list = swatches(wrap.dataset.mk);
    var hit = null;
    list.forEach(function(it){ if (it[0] === val) hit = it[0]; });
    $$('.sw[data-v]', wrap).forEach(function(b){
      b.setAttribute('aria-pressed', (b.dataset.v || '') === val ? 'true' : 'false');
    });
    var c = parseColor(val);
    var pick = wrap.querySelector('.sw.pick');
    if (pick) {
      var inp = pick.querySelector('input');
      if (val && !hit && c) {
        pick.classList.add('is-on');
        pick.style.setProperty('--c', toRgba(c, c.a));
        if (inp) inp.value = toHex(c);
      } else {
        pick.classList.remove('is-on');
        pick.style.removeProperty('--c');
        if (inp) inp.value = hit ? toHex(parseColor(hit)) : '#ffc440';
      }
    }
    var sel = wrap.querySelector('.sw-a');
    if (sel) sel.value = String(nearAlpha(c ? c.a : 1));
    var holder = wrap.parentNode;
    var out = holder ? holder.parentNode.querySelector('.mk-val[data-mk-val="' + wrap.dataset.mk + '"]') : null;
    if (out) out.textContent = val || '无';
  }
  function setCell(card, kind, val){
    var wrap = card.querySelector('.sw-wrap[data-mk="' + kind + '"]');
    if (!wrap) return;
    wrap.dataset.val = val || '';
    paintWrap(wrap);
  }

  // ---- 阅读高亮 (语料滤镜) ----
  var CAT_NAME = {};
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

    var box = $('mk-styles');
    if (box) {
      CAT_NAME = {};
      (d.categories || []).forEach(function(c){ CAT_NAME[c.id] = c.name; });
      box.innerHTML = Object.keys(d.styles || {}).map(function(cid){
        var s = d.styles[cid] || {};
        var name = CAT_NAME[cid] || cid;
        return '<div class="mk-style" data-cat="' + cid + '">' +
          '<div class="mk-style-hd">' +
            '<div class="mk-style-t">' + esc(name) + '<small>' + cid + '</small></div>' +
            '<label class="st-sw compact" title="这个分类是否高亮">' +
              '<input type="checkbox" class="m3" data-mk="enabled"' +
              (s.enabled ? ' checked' : '') + ' aria-label="' + esc(name) + ' 启用"></label>' +
          '</div>' +
          colorCell('bg', s.bg || '', '底色') +
          colorCell('color', s.color || '', '文字色') +
          '<label class="mk-under"><input type="checkbox" data-mk="underline"' +
            (s.underline ? ' checked' : '') + '> 下划线</label>' +
          '</div>';
      }).join('');
      $$('.sw-wrap', box).forEach(paintWrap);
    }

    var corp = $('mk-corpora');
    if (corp) corp.textContent = (d.corpora || []).length + ' 份';
    var books = $('mk-books');
    if (books) {
      var opts = ['<option value="">自动匹配</option>'].concat((d.corpora || []).map(function(c){
        return '<option value="' + esc(c.file) + '">' +
          esc(c.title) + ' · ' + c.words + ' 词</option>';
      }));
      var rows = (d.books || []).map(function(b){
        var how = {name:'书名匹配', sidecar:'书库边车', source_epub:'同源文件', bind:'已绑定',
                   bind_path:'已绑定', bind_missing:'绑定失效', none:'未匹配'}[b.how] || b.how || '未匹配';
        var ok = b.file ? ' ok' : '';
        var sel = b.bound ? '<option value="' + esc(b.bound) + '" selected>' +
                    esc(b.bound) + '</option>' : '';
        return '<div class="mk-row" data-book="' + esc(b.key) + '">' +
          '<div class="nm">' + esc(b.title) + '<small>' + esc(b.author || '') + '</small></div>' +
          '<div class="st' + ok + '">' + how + (b.file ? ' · ' + b.words + ' 词' : '') + '</div>' +
          '<select data-mk-bind>' + sel + opts.join('') + '</select></div>';
      });
      books.innerHTML = rows.length ? rows.join('') :
        '<div class="st-note">书库还是空的。</div>';
      // 选中态: 绑定值或当前解析到的那份
      $$('.mk-row', books).forEach(function(row){
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
    $$('#mk-styles .mk-style').forEach(function(card){
      var g = function(k){ return card.querySelector('[data-mk="' + k + '"]'); };
      var wrapOf = function(k){ return card.querySelector('.sw-wrap[data-mk="' + k + '"]'); };
      var en = g('enabled'), un = g('underline');
      styles[card.dataset.cat] = {
        enabled: en ? !!en.checked : true,
        bg: wrapOf('bg') ? String(wrapOf('bg').dataset.val || '') : '',
        color: wrapOf('color') ? String(wrapOf('color').dataset.val || '') : '',
        underline: un ? !!un.checked : false
      };
    });
    return {
      enabled: !!($('mk-on') || {}).checked,
      mode: ($('mk-mode') || {}).value || 'highlight',
      dirs: String(($('mk-dirs') || {}).value || '').split(',')
              .map(function(s){ return s.trim(); })
              .filter(function(s){ return s; }),
      min_len: parseInt(mkNum('mk-minlen'), 10) || 2,
      max_terms: parseInt(mkNum('mk-maxterms'), 10) || 6000,
      skip_digits: !!($('mk-digits') || {}).checked,
      styles: styles
    };
  }

  // 色板: 点一下换色 (只是改界面, 点保存才落盘)
  document.addEventListener('click', function(ev){
    var btn = ev.target.closest ? ev.target.closest('button.sw[data-v]') : null;
    if (!btn) return;
    var wrap = btn.closest('.sw-wrap');
    if (!wrap) return;
    ev.preventDefault();
    wrap.dataset.val = btn.dataset.v || '';
    paintWrap(wrap);
  });

  // 自定义颜色: 系统调色板选完即时用 (底色沿用当前不透明度)
  document.addEventListener('input', function(ev){
    var inp = ev.target;
    if (!inp || inp.type !== 'color') return;
    var wrap = inp.closest('.sw-wrap');
    if (!wrap) return;
    var c = parseColor(inp.value);
    if (!c) return;
    var sel = wrap.querySelector('.sw-a');
    var a = sel ? parseFloat(sel.value) : 1;
    // 文字色不带透明度 (实色更清楚); 只有底色才拼 rgba
    wrap.dataset.val = wrap.dataset.mk === 'bg' ? toRgba(c, a) : toHex(c);
    paintWrap(wrap);
  });

  document.addEventListener('change', function(ev){
    var t = ev.target;
    // 不透明度
    if (t && t.classList && t.classList.contains('sw-a')) {
      var wrap = t.closest('.sw-wrap');
      if (wrap) {
        var c = parseColor(wrap.dataset.val);
        if (c) { wrap.dataset.val = toRgba(c, parseFloat(t.value)); paintWrap(wrap); }
      }
      return;
    }
    // 每本书的语料绑定
    var sel = t && t.closest ? t.closest('[data-mk-bind]') : null;
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
    if (act === 'sync' || act === 'sync-reset') {
      var sbody = act === 'sync' ? { sync: syCollect() } : { sync: null };
      post('/api/settings', sbody).then(function(r){
        syPaint(r.sync);
        msg('st-sync-msg', act === 'sync' ? '已保存, 下一次翻页就按新设置同步'
                                          : '已回到 config.toml 的写法');
        done();
      }).catch(function(e){ msg('st-sync-msg', e.message, true); done(); });
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

  // 排障 / 探针用 (只读 + 设置一个颜色)
  window.__MK__ = {
    collect: mkCollect,
    set: function(cat, kind, val){
      var card = document.querySelector('#mk-styles .mk-style[data-cat="' + cat + '"]');
      if (card) setCell(card, kind, val);
      return !!card;
    },
    value: function(cat, kind){
      var card = document.querySelector('#mk-styles .mk-style[data-cat="' + cat + '"]');
      var w = card ? card.querySelector('.sw-wrap[data-mk="' + kind + '"]') : null;
      return w ? String(w.dataset.val || '') : null;
    }
  };

  // 两张卡各自拉一次, 互不阻塞
  syLoad();
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


def sync_state() -> dict:
    """云端同步的生效值 + 每一项的来源 (设置页 / config.toml / 环境变量)。"""
    import os

    from recto.services import sync as _sync

    eff = _sync.sync_settings()
    tick = _sync.tick_state()
    over = st.active_overrides()
    db = over.get("sync_push_debounce")

    def src(key: str, env: str) -> str:
        if os.environ.get(env) is not None:
            return "env"
        return "settings" if key in over else "config"

    return {
        "auto": eff["auto"],
        "pull_on_open": eff["pull_on_open"],
        "flush_on_hide": eff["flush_on_hide"],
        "stamp": eff["stamp"],
        "push_debounce_sec": int(round(eff["push_debounce_sec"])),
        "pull_gap_sec": int(round(eff["pull_gap_sec"])),
        "tick": tick,
        "source": {
            "auto": src("sync_auto", "RECTO_SYNC_AUTO"),
            "pull_on_open": src("sync_pull_on_open", "RECTO_SYNC_PULL_ON_OPEN"),
            "flush_on_hide": src("sync_flush_on_hide", "RECTO_SYNC_FLUSH_ON_HIDE"),
            "stamp": src("sync_stamp", "RECTO_SYNC_STAMP"),
            "push_debounce_sec": src("sync_push_debounce", "RECTO_SYNC_PUSH_DEBOUNCE"),
            "interval_sec": src("sync_interval_sec", "RECTO_SYNC_INTERVAL"),
        },
        "override_keys": sorted(over.keys()),
        "has_override": bool(db) or any(
            k in over for k in ("sync_auto", "sync_pull_on_open",
                                "sync_flush_on_hide", "sync_stamp",
                                "sync_interval_sec")),
    }


def summary() -> dict:
    return {"ok": True, "nickname": st.nickname(),
            "login": _login_name(), "library": library_state(),
            "sync": sync_state(),
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

        # --- 云端同步 (阅读进度) ---
        '<details class="sec wide" id="sync"><summary>云端同步 '
        '<span class="pill mute" id="sy-pill">读取中…</span></summary><div class="sec-body">'
        '<div class="st-note">阅读时把进度推到云端阅读服务（掌阅），打开一本书时先跟云端对一次。'
        '推送<b>只前进</b> —— 手机端读得更靠前时不会被网页这边往回拨。'
        '同步有两条通道: <b>翻页触发</b>（读者一动就推）与<b>定时触发</b>'
        '（停在原地也按时补一次, 顺带把划线与阅读时长带上）。</div>'
        '<div class="st-two">'
        '<div class="st-block"><div class="st-h">同步行为</div>'
        '<label class="st-sw"><span>阅读时自动同步<small>翻页就把进度推给云端</small></span>'
        '<input type="checkbox" class="m3" id="sy-auto"></label>'
        '<label class="st-sw"><span>打开书时先看云端进度<small>云端更靠前会问你要不要跳过去</small></span>'
        '<input type="checkbox" class="m3" id="sy-pull"></label>'
        '<label class="st-sw"><span>切后台 / 离开页面时立刻推送'
        '<small>关标签页也不丢最后那点进度</small></span>'
        '<input type="checkbox" class="m3" id="sy-flush"></label>'
        '<label class="st-sw"><span>阅读页右下角显示同步时间'
        '<small>一眼看出是不是刚推上去了</small></span>'
        '<input type="checkbox" class="m3" id="sy-stamp"></label>'
        "</div>"
        '<div class="st-block"><div class="st-h">节奏与状态</div>'
        '<div class="st-row"><span>推送间隔<small>停手后最多等这么久再推</small></span>'
        '<input class="st-in" id="sy-gap" type="number" min="3" max="300" step="1">'
        "<em>秒</em></div>"
        '<div class="st-row"><span>拉取间隔<small>同一本书两次自动拉取之间</small></span>'
        '<b id="sy-pullgap">—</b><em>秒</em></div>'
        '<label class="st-sw"><span>定时同步<small>停在一页上看很久 / 没翻页也在同步</small></span>'
        '<input type="checkbox" class="m3" id="sy-tick"></label>'
        '<div class="st-row"><span>定时间隔<small>多久看一眼最近读过的书</small></span>'
        '<input class="st-in" id="sy-tick-sec" type="number" min="15" max="600" step="5">'
        "<em>秒</em></div>"
        '<label class="st-sw"><span>顺带同步划线 / 书签<small>定时的同时跟云端对一次</small></span>'
        '<input type="checkbox" class="m3" id="sy-tick-notes"></label>'
        '<label class="st-sw"><span>顺带推送阅读时长<small>今天的分钟数, 十分钟一次</small></span>'
        '<input type="checkbox" class="m3" id="sy-tick-time"></label>'
        '<div class="st-line">定时同步 <span id="sy-tick-state">—</span></div>'
        '<div class="st-line">来源 <span id="sy-src">—</span></div>'
        '<div class="st-note" style="margin-top:10px">改完立刻生效（下一次翻页 / 打开书 / '
        "定时心跳就用新设置），不用重启服务。</div>"
        "</div>"
        "</div>"
        '<div class="row" style="margin-top:14px">'
        '<button class="primary" data-st="sync">保存</button>'
        '<button data-st="sync-reset">回到 config.toml</button>'
        '<span id="st-sync-msg" class="st-msg"></span></div>'
        "</div></details>",

        # --- 阅读高亮 (语料 → 阅读器) ---
        '<details class="sec wide" id="mark"><summary>阅读高亮 '
        '<span class="pill mute" id="mk-pill">读取中…</span></summary><div class="sec-body">'
        '<div class="st-note">把「语料分解 json」里的关键词铺到阅读器做底色。'
        '默认 highlight 模式<b>不改动一个字符</b> —— 阅读进度与划线坐标都不受影响；'
        'span 模式才能加粗/圆角，代价是改 DOM（翻页模式会重排一次）。</div>'
        '<div class="mk-opts">'
        '<label class="st-sw"><span>启用</span>'
        '<input type="checkbox" class="m3" id="mk-on"></label>'
        '<label class="st-row"><span>渲染方式</span>'
        '<select class="st-in" id="mk-mode">'
        '<option value="highlight">highlight（不改 DOM）</option>'
        '<option value="span">span（可加粗/圆角）</option></select></label>'
        '<label class="st-row"><span>最短词长</span>'
        '<input class="st-in" id="mk-minlen" type="number" min="1" max="12" step="1">'
        "<em>字</em></label>"
        '<label class="st-row"><span>最多词数</span>'
        '<input class="st-in" id="mk-maxterms" type="number" min="50" max="6000" step="50">'
        "</label>"
        '<label class="st-sw"><span>跳过纯数字词</span>'
        '<input type="checkbox" class="m3" id="mk-digits"></label>'
        "</div>"
        '<label class="st-field" style="margin-top:10px"><span>语料目录（多个用逗号分隔；'
        '留空 = config.toml 的 [corpus] dir）</span>'
        '<input class="st-in" id="mk-dirs" type="text" spellcheck="false" '
        'placeholder="例如 /srv/corpora"></label>'
        '<div class="st-h">分类配色<small>底色默认半透明，深浅主题都不刺眼；最后一格是自定义调色板</small></div>'
        '<div class="mk-styles" id="mk-styles"><div class="st-note">读取中…</div></div>'
        '<div class="row" style="margin-top:12px">'
        '<button class="primary" data-st="mark">保存</button>'
        '<button data-st="mark-reset">恢复默认</button>'
        '<span id="mk-msg" class="st-msg"></span></div>'
        '<div class="st-h">每本书用哪份语料 <span class="pill mute" id="mk-corpora">0 份</span></div>'
        '<div id="mk-books"><div class="st-note">读取中…</div></div>'
        '<div class="st-note" style="margin-top:10px">书名匹配宁可漏也不错配（同一个作者的另一本书'
        '很常见）。匹配不上时在下面给这本书指一份；书库里放 <code>&lt;书名&gt;.keywords.json</code> '
        '也会被自动认出来。</div>'
        '<div class="st-note" style="margin-top:6px">文件: '
        '<span class="st-path" id="mk-path"></span></div>'
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

    sync_given = "sync" in body
    if sync_given:
        raw = body.get("sync")
        if raw is None or raw == "":
            st.clear("sync_auto", "sync_pull_on_open", "sync_flush_on_hide",
                     "sync_stamp", "sync_push_debounce",
                     "sync_interval", "sync_interval_sec",
                     "sync_tick_notes", "sync_tick_time")
        elif isinstance(raw, dict):
            kv = {}
            for key in ("auto", "pull_on_open", "flush_on_hide", "stamp"):
                if key in raw:
                    val = bool(raw.get(key))
                    kv["sync_" + key] = "on" if val else "off"
            if "push_debounce_sec" in raw:
                try:
                    gap = int(float(raw.get("push_debounce_sec")))
                except (TypeError, ValueError):
                    raise ValueError("推送间隔要写数字 (秒)")
                if gap < 3 or gap > 300:
                    raise ValueError("推送间隔要在 3–300 秒之间")
                kv["sync_push_debounce"] = str(gap)
            # 定时同步 (心跳): interval_on / interval_sec / tick_notes / tick_time
            if "interval_on" in raw or "interval_sec" in raw:
                on = bool(raw.get("interval_on", True))
                try:
                    iv = int(float(raw.get("interval_sec", 60)))
                except (TypeError, ValueError):
                    raise ValueError("定时间隔要写数字 (秒)")
                if iv < 0 or iv > 3600:
                    raise ValueError("定时间隔要在 0–3600 秒之间")
                kv["sync_interval"] = "on" if (on and iv > 0) else "off"
                kv["sync_interval_sec"] = str(iv if iv > 0 else 60)
            for tkey in ("tick_notes", "tick_time"):
                if tkey in raw:
                    kv["sync_" + tkey] = "on" if bool(raw.get(tkey)) else "off"
            if kv:
                st.update(**kv)
        else:
            raise ValueError("sync 要是对象或 null")

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

    if nickname_given or lib_given or sync_given:
        # sync 改了必须让配置单例重读 settings.json 的覆盖层, 否则新设置要等重启才生效
        _reload_runtime(rescan=changed_lib)

    out = summary()
    out["changed"] = {"nickname": nickname_given, "library": lib_given,
                      "sync": sync_given}
    return out


def api_password(body: dict, user: str | None, ip: str = "") -> dict:
    """改口令 —— 只校验「当前口令」, 不校验用户名。

    会话已经证明是登录用户了; 再拿 cookie 里的用户名去比 `[auth] user` 只会
    在两种情况下误伤: (a) 升级前留下的旧 cookie 里是老的登录名,
    (b) 用户名改了。两者都会让「旧口令永远不对」。
    """
    from recto.app import webauth

    body = body or {}
    old = str(body.get("old") or "")
    new = str(body.get("new") or "")
    who = (ip or "-") + ("" if not user else "/" + str(user))
    src = webauth.password_source()
    if src == "env":
        # 环境变量优先级最高: 改了文件也不生效, 不如直说 (也别白验一遍旧口令)
        webauth._audit(f"口令修改 by={who} 拒绝: 来源是环境变量")   # noqa: SLF001
        raise ValueError("口令由环境变量 RECTO_AUTH_PASSWORD 提供 —— 请改环境变量, "
                         "或去掉它再用本页修改")
    if webauth.enabled() and not webauth.check_password(old):
        # 「旧口令不对」每次都要留痕: 用户看到的只是一句提示, 我们得知道到底哪一步
        webauth._audit(                                        # noqa: SLF001
            f"口令修改 by={who} 拒绝: 当前口令不对 (服务端来源 {src}, "
            f"填了 {len(old)} 位)")
        raise ValueError("当前口令不对 (口令来源: " + _password_source_label() + ")。"
                         "忘了旧口令, 可在服务器执行 python3 -m recto.cli passwd 重置")
    ok, err = webauth.set_password(new, who=who)
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
