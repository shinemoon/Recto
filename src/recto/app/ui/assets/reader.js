/* 内置阅读器前端 —— 字节坐标 = 唯一真相。
 *
 * 三个要点：
 *   1. 每个块带 data-off（块起始字节）。滚动时取"第一个仍在视口上方的块"
 *      作为位置写回服务端 —— 服务端再换算成 readPercent / readPosition，
 *      与云端阅读服务同一套算法。
 *   2. 划线的起止用「块偏移 + 块内字符序号」双向记：字节偏移给云端，
 *      字符序号给 DOM（精确还原，不靠文本搜索）。
 *   3. 阅读时长每 30 秒心跳一次，隐藏/关闭时收尾；服务端按分钟位图记账。
 *
 * 阅读体验（沉浸）：
 *   4. 顶栏下滑自动收起、上滑/点正文中间唤出；收起时顶部留一条 2px 进度线。
 *   5. 左右页边点击 = 上一页 / 下一页（左右两侧固定热区，桌面不压正文）。
 *   6. 点正文即关掉所有浮层（目录/书签/笔记抽屉、选区工具条、笔记编辑）。
 *   7. 排版面板（顶栏 Aa）: 阅读模式(滚动/左右翻页)/字号/行间距/段间距/上下边距/阅读宽度/字体,
 *      逐设备记 localStorage; 所有值只写 .rv 上的 CSS 变量, 正文样式保持单一真相。
 *
 * 两种阅读模式（排版面板里切换，只影响这台设备）：
 *   scroll 连续滚动 —— 上下滑，进度按滚动位置记。
 *   page   左右翻页 —— 多栏分页：正文列宽 = 阅读宽度、页高 = 视口高，翻页 = 横向平移一列。
 *          坐标口径不变（还是"章 + 块起始字节"），只是"当前页左上角那一段"当游标。
 */
(function () {
  'use strict';

  var root = document.getElementById('rv');
  if (!root) return;

  var API = '/read/api/book/' + root.dataset.book;
  var $ = function (sel, el) { return (el || document).querySelector(sel); };
  var $$ = function (sel, el) { return Array.prototype.slice.call((el || document).querySelectorAll(sel)); };
  var enc = new TextEncoder();

  var S = {
    key: root.dataset.book,
    meta: null,
    chapters: [],
    bytes: 0,
    cur: 0,
    loaded: {},          // chapter index -> element
    blocks: {},          // chapter index -> [block data]
    cjk: {},             // chapter index -> 是否中文段落 (决定首行缩进等版式)
    notes: [],
    marks: [],
    fs: 19,              // 占位, 实际值见 S.look (排版面板)
    look: null,
    theme: localStorage.getItem('recto.theme') || 'auto',
    sel: null,
    scrollT: 0,
    saveT: 0,
    lastOff: 0,
    hlReady: false,
    // 阅读模式: scroll = 连续滚动, page = 左右翻页 (多栏分页)
    mode: localStorage.getItem('recto.mode') === 'page' ? 'page' : 'scroll',
    // 翻页效果: slide(默认) / none / fade / ripple / flip —— 只影响左右翻页
    fx: localStorage.getItem('recto.fx') || 'slide',
    page: 0, pageCount: 1, pageStride: 0, pageW: 0, pageGap: 0,
    swipedAt: 0,
    draggedAt: 0,
    // 云端同步: sid = 这个页面实例的随机 id (服务端用它挡住"旧标签页把进度写回去")
    sid: 's' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-3),
    sync: null,          // {settings:{...}, state:{...}}
    syncT: 0,
    pulled: false,
    // 这次打开书已经问过"要不要跳到云端"了 (同一次打开不重复打扰)
    choiceAsked: false,
    choiceT: 0,
    // 读者在这一页里真的动过没有 (滚轮/触摸/按键/点击)。
    // 没动过就不写进度: 刚打开时正文还在排版, 量到的锚点可能是恢复前的位置,
    // 写回去就会把"读到 90%"变成"停在章首" —— 这是老用户报过的现象。
    userMoved: false,
    // 读者刚刚**主动跳**过 (目录 / 上一章 / 笔记跳转)。这一次保存要带 jump=1:
    // 「我就是想回去」的意图得让服务端的"旧会话不许回退"闸门放行,
    // 否则在新标签页里往回跳一章会被当成别处读得更远的旧页面挡下来。
    jumpIntent: false
  };

  // ---------- 基础 ----------
  function toast(msg, ms) {
    var el = $('#rv-toast');
    el.textContent = msg;
    el.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { el.hidden = true; }, ms || 1800);
  }

  var API_TIMEOUT = 25000;   // 一条请求最多等多久 (ms)

  function api(path, opts) {
    opts = opts || {};
    opts.headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
    if (opts.body && typeof opts.body !== 'string') opts.body = JSON.stringify(opts.body);
    // fetch 默认永不超时: 网络半死时"补下一章"会一直挂在 loading 上, 翻页就再也走不动
    var ac = null, timer = 0;
    if (!opts.signal && typeof AbortController === 'function') {
      ac = new AbortController();
      opts.signal = ac.signal;
      timer = setTimeout(function () { try { ac.abort(); } catch (e) { /* noop */ } }, API_TIMEOUT);
    }
    var clear = function () { if (timer) { clearTimeout(timer); timer = 0; } };
    return fetch(API + path, opts).then(function (r) {
      if (r.status === 401) { location.href = '/login?next=' + encodeURIComponent(location.pathname); return null; }
      if (r.status === 403) { location.href = '/first-run'; return null; }
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).then(function (v) { clear(); return v; },
            function (e) { clear(); throw (e && e.name === 'AbortError' ? new Error('请求超时') : e); });
  }

  function beacon(path, body) {
    try {
      var data = new Blob([JSON.stringify(body)], { type: 'application/json' });
      navigator.sendBeacon(API + path, data);
    } catch (e) { /* ignore */ }
  }

  function bytesOf(str) { return enc.encode(str || '').length; }

  function pctOf(ch, off) {
    var c = S.chapters[ch];
    if (!c || !S.bytes) return 0;
    return Math.max(0, Math.min(1, (c.start + off) / S.bytes));
  }

  // 百分比精度按书的大小自适应：一个刻度 ≈ 2KB 正文（一屏）。
  // 371KB 的书 1 位小数就够；40MB 的长篇要 3 位（0.1% = 41KB = 一万多字，
  // 1 位会让进度条长时间"不动"）。向下取整，不把没读到的地方说成读过。
  function pctDec(bytes) {
    var t = Number(bytes) || 0;
    if (t <= 0) return 2;
    return Math.max(1, Math.min(3, Math.ceil(Math.log(t / 2048) / Math.LN10 - 2)));
  }
  function pctText(p, bytes) {
    var t = Number(bytes) || S.bytes || 0;
    var d = pctDec(t), sc = Math.pow(10, d);
    var x = Math.floor(Math.max(0, Math.min(1, Number(p) || 0)) * 100 * sc + 1e-9) / sc;
    var out = x.toFixed(d);
    if (out.indexOf('.') >= 0) out = out.replace(/0+$/, '').replace(/\.$/, '');
    return out + '%';
  }

  // ---------- 渲染 ----------
  // 排版: 字号/行间距/段间距/上下边距/阅读宽度/字体/首行缩进
  // (逐设备记在 localStorage)
  function lookDefaults() {
    var narrow = window.matchMedia('(max-width: 620px)').matches;
    return { fs: narrow ? 17.5 : 19, lh: narrow ? 1.9 : 1.95, para: 1.05,
             pad: narrow ? 1 : 1.4, w: 44, font: '', indent: '2' };
  }

  var LOOK_NUM = ['fs', 'lh', 'para', 'pad', 'w'];

  function numOr(v, def) { var n = parseFloat(v); return isFinite(n) ? n : def; }

  function loadLook() {
    var d = lookDefaults();
    var L = {};
    LOOK_NUM.forEach(function (k) {
      L[k] = numOr(localStorage.getItem('recto.' + k), d[k]);
    });
    L.font = localStorage.getItem('recto.font') || '';
    // 首行缩进 (字符数): '0' = 无缩进, '2' = 2 字符 (clreq 中文版式惯例)
    var ind = localStorage.getItem('recto.indent');
    L.indent = (ind === '0' || ind === '2') ? ind : d.indent;
    return L;
  }

  // 字体值可能是通用族名 (serif…) 或具体字体名 —— 具体名要带兜底族
  function fontCss(v) {
    if (!v) return '';
    if (v === 'serif' || v === 'sans-serif' || v === 'monospace' || v === 'system-ui') return v;
    return '"' + v + '", serif';
  }

  function applyLook() {
    var L = S.look || (S.look = loadLook());
    root.setAttribute('data-theme', S.theme);
    root.style.setProperty('--rv-fs', L.fs + 'px');
    root.style.setProperty('--rv-lh', String(L.lh));
    root.style.setProperty('--rv-para', L.para + 'em');
    root.style.setProperty('--rv-pad', L.pad + 'rem');
    root.style.setProperty('--rv-w', L.w + 'rem');
    root.style.setProperty('--rv-indent', (L.indent === '0' ? '0' : L.indent + 'em'));
    var f = fontCss(L.font);
    if (f) root.style.setProperty('--rv-font', f);
    else root.style.removeProperty('--rv-font');
    document.body.style.background = '';
  }

  // 目录: 单层目录平铺; 多层目录 (多卷合订本) 按层级折叠 + 缩进
  function tocNode(idx) {
    var nodes = (S.meta && S.meta.toc) || [];
    for (var i = 0; i < nodes.length; i++) if (+nodes[i].spine === +idx) return nodes[i];
    return null;
  }

  function tocIsMulti() {
    var nodes = (S.meta && S.meta.toc) || [];
    for (var i = 0; i < nodes.length; i++) if (+nodes[i].depth > 0) return true;
    return false;
  }

  function tocRow(c, depth) {
    var nm = tocNode(c.i);
    var li = document.createElement('li');
    li.className = 'rv-toc-row' + (depth > 0 ? ' d' + Math.min(depth, 4) : '');
    var a = document.createElement('a');
    a.href = '#c' + c.i;
    a.dataset.ch = c.i;
    var name = (nm && nm.text) ? nm.text : (c.title || '');
    a.innerHTML = '<span class="rv-toc-n">' + (c.i + 1) + '</span>' + escapeHtml(name);
    a.addEventListener('click', function (ev) {
      ev.preventDefault();
      jumpTo(c.i, 0);
      closeDrawer('toc');
    });
    li.appendChild(a);
    return li;
  }

  function tocGroup(c, nm, kids) {
    var li = document.createElement('li');
    li.className = 'rv-toc-row rv-toc-grp';
    var box = document.createElement('details');
    box.className = 'rv-toc-sec';
    var sum = document.createElement('summary');
    sum.dataset.ch = c.i;
    sum.innerHTML = '<i class="rv-toc-caret" aria-hidden="true"></i>' +
                    '<span class="rv-toc-n">' + (c.i + 1) + '</span>' +
                    escapeHtml((nm && nm.text) || c.title || '');
    box.appendChild(sum);
    var ul = document.createElement('ul');
    ul.className = 'rv-toc-kids';
    kids.forEach(function (k) { ul.appendChild(tocRow(k.c, k.d < 0 ? 1 : k.d)); });
    box.appendChild(ul);
    li.appendChild(box);
    return li;
  }

  function renderToc() {
    var ol = $('#rv-toc-list');
    if (!ol) return;
    ol.innerHTML = '';
    var acts = $('#rv-toc-acts');
    var multi = tocIsMulti();
    if (acts) acts.hidden = !multi;

    if (!multi) {                       // 单层目录 = 原来的平铺列表
      S.chapters.forEach(function (c) { ol.appendChild(tocRow(c, 0)); });
      markToc();
      return;
    }

    // 多卷合订本: 卷 = 可折叠分组, 章挂在分组内。
    // 卷这一行只由 summary 承担 (不再额外生成一条同名链接, 否则父级会重复出现),
    // summary 的点击就是折叠/展开本身, 不做跳转。
    var rows = S.chapters.map(function (c) {
      var nm = tocNode(c.i);
      return { c: c, nm: nm, d: nm ? +nm.depth : -1 };  // 目录外的篇 (封面/目录页) 归到上级
    });
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      if (r.d !== 0) {                  // 没有父卷的散篇: 平铺
        ol.appendChild(tocRow(r.c, r.d < 0 ? 1 : r.d));
        continue;
      }
      var kids = [];
      var j = i + 1;
      while (j < rows.length && rows[j].d !== 0) { kids.push(rows[j]); j++; }
      // 卷里没有子项时不需要折叠, 退化成可点击跳转的普通行
      ol.appendChild(kids.length ? tocGroup(r.c, r.nm, kids) : tocRow(r.c, 0));
      i = j - 1;
    }
    markToc();
  }

  function markToc() {
    var ol = $('#rv-toc-list');
    if (!ol) return;
    $$('a', ol).forEach(function (a) {
      a.classList.toggle('rv-cur', +a.dataset.ch === S.cur);
    });
    $$('.rv-toc-sec > summary', ol).forEach(function (s) {
      s.classList.toggle('rv-cur', +s.dataset.ch === S.cur);
    });
    // 折到当前章所在的卷, 并把那一行滚进可视区 (590 章的目录不开分组找不到人)
    $$('.rv-toc-grp', ol).forEach(function (li) {
      var inner = $$('a', li);
      var hit = null;
      for (var i = 0; i < inner.length; i++) {
        if (+inner[i].dataset.ch === S.cur) { hit = inner[i]; break; }
      }
      if (!hit) {                       // 当前章就是卷首页 -> 命中卷标题本身
        var sm = $('summary', li);
        if (sm && +sm.dataset.ch === S.cur) hit = sm;
      }
      if (!hit) return;
      var box = $('details', li);
      if (box) box.open = true;
      try { hit.scrollIntoView({ block: 'nearest' }); } catch (e) { /* 老浏览器 */ }
    });
  }

  function tocExpand(all) {
    var ol = $('#rv-toc-list');
    if (!ol) return;
    $$('.rv-toc-sec', ol).forEach(function (d) { d.open = !!all; });
    if (!all) markToc();
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function chapterEl(idx) {
    var arts = $$('.rv-ch', $('#rv-content'));
    for (var i = 0; i < arts.length; i++) if (+arts[i].dataset.ch === idx) return arts[i];
    return null;
  }

  function renderChapter(data, append) {
    var box = document.createElement('article');
    box.className = 'rv-ch' + (data.cjk ? ' rv-cjk' : '');
    box.dataset.ch = data.i;
    box.dataset.start = data.start;
    var h = document.createElement('h2');
    h.className = 'rv-ch-title';
    h.textContent = data.title || ('第 ' + (data.i + 1) + ' 章');
    box.appendChild(h);
    var frag = document.createDocumentFragment();
    data.blocks.forEach(function (b) {
      var el = document.createElement('div');
      el.className = 'rv-blk';
      el.dataset.off = b.off;
      el.dataset.ch = data.i;
      el.dataset.tag = b.tag;
      // 书内样式表里这一块的版面声明 (缩进/对齐/字号/字重)。放在**真正的
      // 那个元素**上才能盖过阅读器自己的 .rv-page p 规则 (行内样式优先)。
      var inner = null;
      if (b.tag && /^h[1-6]$/.test(b.tag)) {
        inner = document.createElement(b.tag === 'h1' ? 'h3' : b.tag);
      } else if (b.tag === 'blockquote') {
        inner = document.createElement('blockquote');
      } else if (b.tag === 'li') {
        var ul = document.createElement('ul');
        inner = document.createElement('li');
        ul.appendChild(inner);
        el.appendChild(ul);
      } else if (!b.tag || b.tag === 'p') {
        // 正文段包一层 <p>: 段间距/两端对齐都挂在 .rv-page p 上
        inner = document.createElement('p');
      }
      if (inner) {
        if (b.style) inner.setAttribute('style', b.style);
        // 只有图、没有字的块: 不参与首行缩进 (否则整幅图被推右两个字符)
        if (!b.text && /<img/.test(b.html)) inner.classList.add('rv-figblk');
        // 装饰分隔符 (◎ / * / ——): 视觉规格由 CSS 定, 不继承书内标题字号
        if (b.role === 'sep') inner.classList.add('rv-sep');
        else if (b.role === 'sep-multi') inner.classList.add('rv-sep', 'rv-sep-multi');
        else if (b.role === 'sep-dash') inner.classList.add('rv-sep', 'rv-sep-dash');
        inner.innerHTML = b.html;
        if (!inner.parentNode) el.appendChild(inner);
      } else {
        if (b.style) el.setAttribute('style', b.style);
        // 只有图没有字的块 (div/figure 包裹): 同样不进首行缩进的版式
        if (!b.text && /<img/.test(b.html)) el.classList.add('rv-figblk');
        el.innerHTML = b.html;
      }
      frag.appendChild(el);
    });
    box.appendChild(frag);
    var content = $('#rv-content');
    if (append) {
      content.appendChild(box);
    } else {
      content.innerHTML = '';
      content.appendChild(box);
      S.loaded = {};        // 换章 = 之前那些 DOM 已经不在页面上了
    }
    S.loaded[data.i] = box;
    S.blocks[data.i] = data.blocks;
    paintHighlights(data.i);
    kmApply();
    return box;
  }

  // 章节请求: 同一章并发只发一条; 失败也必须把回调"落地"(带 null),
  // 否则等它的人 (补章链路) 会永远停在 loading 里 —— 那就是"章末再也翻不动"。
  var fetchBusy = Object.create(null), fetchWait = Object.create(null);

  function loadChapter(idx, cb) {
    if (S.blocks[idx]) { cb && cb(S.blocks[idx], false); return; }
    if (fetchBusy[idx]) { (fetchWait[idx] = fetchWait[idx] || []).push(cb); return; }
    fetchBusy[idx] = true;
    fetchWait[idx] = cb ? [cb] : [];
    var settle = function (blocks) {
      fetchBusy[idx] = false;
      var ws = fetchWait[idx] || [];
      fetchWait[idx] = [];
      ws.forEach(function (f) {
        try { f && f(blocks, true); } catch (e) { /* 一个回调出错不连累其余 */ }
      });
    };
    api('/chapter/' + idx).then(function (data) {
      if (!data || !data.blocks) { settle(null); return; }
      S.blocks[idx] = data.blocks;
      S.cjk[idx] = !!data.cjk;
      settle(data.blocks);
    }).catch(function (e) {
      toast('章节加载失败: ' + e.message + '（可再点一下重试）');
      settle(null);
    });
  }

  function openChapter(idx, opts) {
    opts = opts || {};
    idx = Math.max(0, Math.min(idx, S.chapters.length - 1));
    S.cur = idx;
    markToc();
    loadChapter(idx, function (blocks) {
      if (!blocks) { footText('— 这一章没载入, 点这里重试 —', true); return; }
      renderChapter(Object.assign({ i: idx }, chapterMeta(idx),
                                  { blocks: blocks, cjk: S.cjk[idx] }), false);
      if (isPaged()) {
        layoutPaged({ ch: idx, off: opts.offset || 0 }, { tail: opts.tail });
      } else if (opts.offset) {
        scrollToOffset(idx, opts.offset);
      } else {
        window.scrollTo({ top: 0 });
      }
      history.replaceState(null, '', '#c' + idx);
      queueSave(true);
      if (opts.appendNext !== false) prefetch();
    });
  }

  // 页脚那句话: 既能当提示, 也能当"重试 / 继续"的按钮
  function footText(msg, link) {
    var el = $('#rv-foot');
    if (!el) return;
    el.textContent = msg;
    el.classList.toggle('is-link', !!link);
    el.dataset.retry = link ? '1' : '';
  }

  function chapterMeta(idx) {
    var c = S.chapters[idx] || {};
    return { start: c.start, size: c.size, title: c.title, catalog: c.catalog };
  }

  function scrollToOffset(ch, off) {
    var box = chapterEl(ch);
    if (!box) return;
    var els = $$('.rv-blk', box);
    var target = null;
    for (var i = 0; i < els.length; i++) {
      if (+els[i].dataset.off >= off) { target = els[i]; break; }
    }
    if (!target) target = els[els.length - 1];
    if (!target) return;
    var bar = $('.rv-bar').getBoundingClientRect().height || 44;
    var top = target.getBoundingClientRect().top + window.scrollY - bar - 8;
    window.scrollTo({ top: Math.max(0, top) });
  }

  // ---------- 左右翻页 (多栏分页) ----------
  // 原理: 列宽 = 阅读宽度, 页高固定 → 放不下的内容流进"下一列"(横向溢出);
  // 翻页 = 把 #rv-content 横向平移一列。DOM 不动, 所以划线/选区/字节坐标全照旧。
  var viewEl = $('#rv-view');
  var contentEl = $('#rv-content');

  function isPaged() { return S.mode === 'page'; }

  function domChapters() {
    return $$('.rv-ch', contentEl).map(function (a) { return +a.dataset.ch; });
  }

  function lastDomChapter() {
    var list = domChapters();
    return list.length ? Math.max.apply(null, list) : S.cur;
  }

  // 量出来一页的几何: 页高写进 CSS 变量, 列宽必须等于可视区宽 (否则会并排放两列)
  // 页高 = 整个视口高。顶栏是浮层 (position: fixed), 不从版面里扣 —— 这样正文的
  // 上下边距都只由 --rv-pad 决定, 阅读区在视口里垂直居中。顶栏显示时盖住顶部几行,
  // 点正文中间可以收起。--rv-bar-h 仍然记下来, 给"第一条可见段落"的判定用。
  function pageGeom() {
    if (!viewEl) return false;
    var barH = Math.round(bar.getBoundingClientRect().height) || 44;
    var h = Math.max(220, Math.round(window.innerHeight));
    root.style.setProperty('--rv-bar-h', barH + 'px');
    root.style.setProperty('--rv-page-h', h + 'px');
    // 列宽必须等于可视区的**真实**宽度, 不能用 clientWidth —— 它取整。
    // 窄屏上可视区常是小数宽 (390 屏 → 364.4px), 取整后列宽 364 而浏览器实际按 364.4 排,
    // 每翻一页就多算 0.4px 的 stride → 翻到第 70 页整块正文偏右 ~28px、右边被裁掉一截。
    //
    // 但也不能用 getBoundingClientRect() —— 翻卷/滑动正在播动画时 (祖先 #rv 带 3D 变换)
    // 它给的是**投影后**的宽: 桌面实测 704 → 781px (+11%)。拿它当列宽会让整本书按错误的
    // 宽度重新分栏, stride 也跟着错 → 正文按错的列距平移, 相邻列就从页边露出来,
    // 而且这个错会留在版面里直到下一次重排。用 getComputedStyle 的 width:
    // 那是布局值, 与 transform 无关 (即使小数也保真)。
    var vcs = getComputedStyle(viewEl);
    var padX = (parseFloat(vcs.paddingLeft) || 0) + (parseFloat(vcs.paddingRight) || 0);
    var boxW = parseFloat(vcs.width);
    // 兜底 (元素隐藏/老浏览器取不到 width): 退回 rect, 但那是投影值, 只配用在不播动画时
    if (!boxW) boxW = viewEl.getBoundingClientRect().width;
    var w = Math.round((boxW - padX) * 1000) / 1000;   // box-sizing: border-box
    if (!w) return false;
    contentEl.style.columnWidth = w + 'px';
    var gap = parseFloat(getComputedStyle(contentEl).columnGap) || 0;
    S.pageW = w; S.pageGap = gap; S.pageStride = w + gap;
    return true;
  }

  function measurePages() {
    if (!S.pageStride) { S.pageCount = 1; return S.pageCount; }
    // scrollWidth = N 列 × 列宽 + (N−1) 列间距
    var sw = contentEl.scrollWidth;
    S.pageCount = Math.max(1, Math.round((sw + S.pageGap) / S.pageStride));
    return S.pageCount;
  }

  // 块 → 页号 的静态表。
  // 为什么不在翻页后现量: 平移有 .26s 过渡, 动画途中 getBoundingClientRect() 拿到的是
  // 中间位置 —— 那样算出来的"当前页第一段"会飘。这表只在排版稳定时重建一次。
  var flow = null;

  // 瞬态页表 (补章/重排进行中, 分栏还没切完) 会把整篇的块挤到第 0/1 列,
  // 于是"当前页第一段"会指到后面某一章的段上 —— 光标、存进度、settle 全跟着跑偏。
  // 真排好时有一條硬不变量: 章首带 break-before: column, 后一章的首块一定在更后的页。
  function flowLooksSane(f) {
    if (!f || !f.els.length) return false;
    var prev = -1, ch = -1;
    for (var i = 0; i < f.els.length; i++) {
      var c = +f.els[i].dataset.ch;
      if (c === ch) continue;
      if (f.pages[i] <= prev) return false;
      prev = f.pages[i]; ch = c;
    }
    return true;
  }

  function flowTrusted() { return !!flow && flow.trusted === true; }

  // 最后一次"页表可信"时落到的位置 —— 瞬态期间存进度/加书签都用它, 不用瞎猜的块。
  var lastGood = null;

  function rebuildFlow() {
    flow = null;
    if (!isPaged() || !viewEl || !S.pageStride) return;
    flipStop();                                   // 同上: 量 rect 前不能有 3D 投影
    var els = $$('.rv-blk', contentEl);
    if (!els.length) return;
    // 先把平移"钉"到当前页 (翻页动画途中量到的是中间位置 → 页号会飘)
    root.classList.add('rv-no-anim');
    contentEl.style.transform = 'translate3d(' + (-S.page * S.pageStride) + 'px,0,0)';
    void contentEl.offsetWidth;
    var vr = viewEl.getBoundingClientRect();
    var stride = S.pageStride;
    // 视口左缘 = 流内零点平移了「当前页」个列宽。原来的写法把 (left − x0)/stride
    // 直接 max(0, …), 于是**前面已经翻过的页全部塌成 0** —— 页表只在第 0 页是对的。
    // 先除以列宽回到"相对当前页"的页差, 再加回 S.page 即可。
    var x0 = vr.left;
    var pages = new Array(els.length);
    for (var i = 0; i < els.length; i++) {
      pages[i] = Math.max(0, Math.round((els[i].getBoundingClientRect().left - x0) / stride) + S.page);
    }
    flow = { els: els, pages: pages };
    flow.trusted = flowLooksSane(flow);
    root.classList.remove('rv-no-anim');
  }

  function pageOfEl(el) {
    if (!el) return 0;
    if (!flow) rebuildFlow();
    if (!flow) return 0;
    var i = flow.els.indexOf(el);
    return i >= 0 ? flow.pages[i] : 0;
  }

  // 落在 (章, 字节偏移) 的那个块 —— 允许偏移落在块中间 (跨页段也能落对)
  function blockForAnchor(ch, off) {
    var els = $$('.rv-blk', contentEl);
    var best = null, any = false;
    for (var i = 0; i < els.length; i++) {
      if (+els[i].dataset.ch !== ch) continue;
      any = true;
      if (+els[i].dataset.off <= off) best = els[i];
      else break;
    }
    return any ? best : null;
  }

  // 当前页左上角那一段 = 进度游标 (一段跨页时优先取"从本页开始"的那段,
  // 取不到才退回跨过来的那段 —— 否则重开时会漂回上一页)
  function pageTopBlockAt(pg) {
    if (!flow) rebuildFlow();
    if (!flow || !flow.els.length) return null;
    var spanning = flow.els[0];
    for (var i = 0; i < flow.els.length; i++) {
      if (flow.pages[i] > pg) break;
      if (flow.pages[i] === pg) return flow.els[i];
      spanning = flow.els[i];
    }
    return spanning;
  }

  function pageTopBlock() { return pageTopBlockAt(S.page); }

  // 逐设备记"上次停在第几页" —— 块级游标在跨页段上会差一页,
  // 同一台设备 + 同一套排版时用页号精确复原 (换设备就退回粗口径)。
  function pageSig() {
    var L = S.look || loadLook();
    return [S.pageW, S.pageGap, root.style.getPropertyValue('--rv-page-h') || '',
            L.fs, L.lh, L.para, L.w, L.font || ''].join('|');
  }

  function pageHintRead() {
    try {
      var v = JSON.parse(localStorage.getItem('recto.pg.' + S.key) || 'null');
      return (v && typeof v.page === 'number') ? v : null;
    } catch (e) { return null; }
  }

  // 某章第一块落在第几页 —— 页号提示按"本章内偏移"存: 刷新后 DOM 里只有起始
  // 那一章, 绝对页号会把前几章的页数也算进去 → 往后跳 (2026-11-06 定位)
  function chapterFirstPage(ch) {
    if (!flow) rebuildFlow();
    if (!flow) return null;
    for (var i = 0; i < flow.els.length; i++) {
      var c = +flow.els[i].dataset.ch;
      if (c === ch) return flow.pages[i];
      if (c > ch) return null;
    }
    return null;
  }

  function pageHintWrite() {
    if (!isPaged() || !S.pageStride) return;
    if (!flowTrusted()) return;                 // 瞬态页表写出来的提示是错的, 宁可不写
    var el = pageTopBlock();
    if (!el) return;
    var ch = +el.dataset.ch;
    var base = chapterFirstPage(ch);
    if (base == null || base > S.page) return;
    try {
      localStorage.setItem('recto.pg.' + S.key, JSON.stringify({
        ch: ch, off: +el.dataset.off, page: S.page, rel: S.page - base, sig: pageSig()
      }));
    } catch (e) { /* 配额/隐私模式: 忽略 */ }
  }

  function paintPageInd() {
    var el = $('#rv-pageind');
    if (!el) return;
    if (!isPaged()) { el.hidden = true; return; }
    el.hidden = false;
    el.textContent = (S.page + 1) + ' / ' + S.pageCount;
  }

  function goPage(p, opts) {
    opts = opts || {};
    if (!isPaged()) return;
    var prev = S.page;
    S.page = Math.max(0, Math.min(p, S.pageCount - 1));
    var moved = S.page !== prev;
    // 只有"滑动"让 CSS transition 播; 其它效果把位移瞬时切到位, 再由 playFx 做入场
    var fx = (opts.instant || !moved) ? 'none' : fxMode();
    if (fx !== 'slide') root.classList.add('rv-no-anim');
    contentEl.style.transform = 'translate3d(' + (-S.page * S.pageStride) + 'px,0,0)';
    if (fx !== 'slide') {
      void contentEl.offsetWidth;                 // 立即生效, 不播动画
      root.classList.remove('rv-no-anim');
    }
    if (fx !== 'slide' && fx !== 'none') playFx(fx, S.page > prev ? 1 : -1);
    // 瞬态页表会把"本页第一段"指到后面某一章 —— 这时既不更新游标也不写页号提示,
    // 等 settle 把页表量稳 (90ms 内) 再来。否则会存下错误的章号。
    if (flowTrusted()) {
      var el = pageTopBlock();
      if (el) {
        var ch = +el.dataset.ch;
        if (ch !== S.cur) { S.cur = ch; markToc(); }
        $('#rv-pct').textContent = pctText(pctOf(ch, +el.dataset.off), S.bytes);
        lastGood = { ch: ch, off: +el.dataset.off, page: S.page };
      }
    }
    pageHintWrite();
    paintPageInd();
    syncProg();
    if (moved && opts.noSave !== true) queueSave(true);
    if (opts.noGrow !== true) ensurePages(2);
  }

  // 按 (章, 块起始字节) 定位到对应那一页; tail = 落到这一章的最后一页
  function layoutPaged(anchor, opts) {
    opts = opts || {};
    if (!isPaged() || !pageGeom()) return;
    // 重排要量一堆 rect (列位/页首块)。祖先 #rv 若正在播翻卷, 量到的是投影后的位置,
    // 页表会整体错位 —— 动画只是观感, 该让路就让路。
    flipStop();
    root.classList.add('rv-no-anim');
    S.page = 0;                                   // 量列位时必须在"零平移"状态
    contentEl.style.transform = 'translate3d(0,0,0)';
    void contentEl.offsetWidth;
    measurePages();
    rebuildFlow();
    var p = 0;
    if (anchor) {
      var el = blockForAnchor(anchor.ch, anchor.off);
      if (el) p = pageOfEl(el);
      // 同一台设备 + 同一套排版 = 页号可以精确复原 (块级游标会差一页)。
      // 提示存的是"本章内第几页", 这里加上本章在本文档里的起始页; 算出来的那一页
      // 必须真的以那段开头, 否则 (瞬态页表写出的错提示) 一律退回块级定位。
      if (flowTrusted()) {
        var hint = pageHintRead();
        if (hint && hint.ch === anchor.ch && hint.off === anchor.off &&
            hint.sig === pageSig() && typeof hint.rel === 'number') {
          var base = chapterFirstPage(anchor.ch);
          var hp = (base == null) ? -1 : base + hint.rel;
          if (hp >= 0 && hp < S.pageCount) {
            var top = pageTopBlockAt(hp);
            if (top && +top.dataset.ch === hint.ch && +top.dataset.off === hint.off) p = hp;
          }
        }
      }
    }
    if (opts.tail) p = S.pageCount - 1;
    S.page = p;
    goPage(p, { instant: true, noSave: opts.noSave });
    root.classList.remove('rv-no-anim');
    if (!opts.noSettle) scheduleSettle();
  }

  // 分栏是异步碎片化的: 首屏量到的 scrollWidth 可能还是上一次页高算出来的。
  // 稳定后（~90ms）再量一遍 —— 页数或页界变了就按锚点重排，最多稳三轮。
  var settleTimer = 0, settleDepth = 0;
  function scheduleSettle() {
    if (!isPaged()) return;
    clearTimeout(settleTimer);
    settleTimer = setTimeout(function () {
      if (!isPaged() || !S.pageStride) return;
      var before = S.pageCount;
      // 瞬态页表里"当前页第一段"会指到后面某一章 —— 拿它当锚点重排会直接跳过几页。
      // 这种时候只重量、保留页号: 尾部补章不会改动前面已有的页界。
      var a = flowTrusted() ? currentAnchor() : null;
      if (a) {
        layoutPaged({ ch: a.ch, off: a.off }, { noSave: true, noSettle: true });
      } else {
        measurePages();
        rebuildFlow();
        var keep = Math.max(0, Math.min(S.page, S.pageCount - 1));
        S.page = keep;
        goPage(keep, { instant: true, noSave: true, noGrow: true });
      }
      if ((S.pageCount !== before || !flowTrusted()) && settleDepth < 3) {
        settleDepth++;
        scheduleSettle();
      } else {
        settleDepth = 0;
      }
    }, 90);
  }

  // 页数不够就再补一章: 短章/前置页会让一屏只剩一两页, 不给缓冲就是"翻两下就断"
  function ensurePages(ahead, depth) {
    if (!isPaged()) return;
    depth = depth || 0;
    if (depth > 3) return;
    if (S.pageCount - 1 - S.page >= ahead) return;
    var nxt = lastDomChapter() + 1;
    if (nxt >= S.chapters.length) return;
    if (S.loaded[nxt] || appendBusy) return;
    appendNextChapter(function (grown) {
      measurePages();
      rebuildFlow();
      paintPageInd();
      syncProg();
      goPage(S.page, { noSave: true, noGrow: true });
      if (grown) { scheduleSettle(); ensurePages(ahead, depth + 1); }
    });
  }

  // 图片是异步解码的: 量列位那一刻图片可能还没有固有尺寸 (高 = 0),
  // 页数会算少。img 的 load 不冒泡, 所以用捕获阶段统一兜住 —— 图到位就重排一次。
  document.addEventListener('load', function (ev) {
    var t = ev.target;
    if (t && t.tagName === 'IMG' && t.closest && t.closest('.rv-page')) scheduleSettle();
  }, true);

  var appendBusy = false;
  var appendWait = [];        // 加载途中又来的补章请求: 排到这次之后, 不吞掉

  // 往后补一章 (append 到流末尾) —— 滚动模式和翻页模式共用
  function appendNextChapter(cb) {
    var nxt = lastDomChapter() + 1;
    if (nxt >= S.chapters.length) { cb && cb(false); return; }
    if (S.loaded[nxt]) { cb && cb(false); return; }
    if (appendBusy) { appendWait.push(cb || function () {}); return; }   // 排队, 不丢
    appendBusy = true;
    var queued = appendWait;
    appendWait = [];
    loadChapter(nxt, function (blocks) {
      var grown = false;
      if (blocks && !S.loaded[nxt]) {
        renderChapter(Object.assign({ i: nxt }, chapterMeta(nxt),
                                    { blocks: blocks, cjk: S.cjk[nxt] }), true);
        grown = true;
      }
      appendBusy = false;                    // 成功/失败都要解锁, 否则之后永远补不上
      if (!blocks) footText('— 下一章没载入, 点这里重试 —', true);
      else if (grown) footText('— 继续下滑读下一章 —');
      try { cb && cb(grown); } catch (e) { /* noop */ }
      queued.forEach(function (f) { try { f(grown); } catch (e) { /* noop */ } });
    });
  }

  function ensureNext(cb) {
    appendNextChapter(function (grown) {
      var nxt = lastDomChapter() + 1;
      if (nxt >= S.chapters.length) footText('— 已是最后一章 —');
      else if (grown) footText('— 继续下滑读下一章 —');
      if (grown && !isPaged()) growIfShort();
      cb && cb(grown);
    });
  }

  // 翻页模式下补章: 只要落在页尾附近就继续补, 免得翻两下就断
  function prefetch() {
    if (isPaged()) { measurePages(); paintPageInd(); syncProg(); ensurePages(2); }
    else ensureNext();
  }

  // 前置页/短章会让首屏只剩半屏 —— 补到至少 2.4 屏高再停 (仅滚动模式)
  function growIfShort() {
    if (isPaged()) return;
    var min = window.innerHeight * 2.4;
    if ($('#rv-content').scrollHeight < min && S.loaded[S.cur + 1]) {
      S.cur = S.cur + 1;
      markToc();
      ensureNext();
    }
  }

  // ---------- 位置 / 进度 ----------
  function topBlock() {
    var els = $$('#rv-content .rv-blk');
    if (!els.length) return null;
    var bar = ($('.rv-bar') || {}).clientHeight || 44;
    var line = bar + 12;
    var best = els[0];
    for (var i = 0; i < els.length; i++) {
      var r = els[i].getBoundingClientRect();
      if (r.bottom > line) { best = els[i]; break; }
      best = els[i];
    }
    return best;
  }

  function queueSave(now) {
    clearTimeout(S.saveT);
    S.saveT = setTimeout(saveProgress, now ? 200 : 2500);
  }

  // 读者是否真的到了全书末尾 (scroll: 滚到底; page: 最后一章最后一页)
  function atEnd() {
    if (isPaged()) {
      return S.cur >= S.chapters.length - 1 && S.page >= S.pageCount - 1;
    }
    return window.scrollY + window.innerHeight >= document.body.scrollHeight - 40;
  }

  // 当前游标: 滚动模式 = 视口顶部那一段; 翻页模式 = 当前页左上角那一段
  function currentAnchor() {
    if (isPaged() && !flowTrusted()) {
      // 量测瞬态: 页表可能把某一章错当成本页开头 —— 用最后一次可信位置兜住
      if (!lastGood) return null;
      return { el: blockForAnchor(lastGood.ch, lastGood.off),
               ch: lastGood.ch, off: lastGood.off };
    }
    var el = isPaged() ? pageTopBlock() : topBlock();
    if (!el) return null;
    return { el: el, ch: +el.dataset.ch, off: +el.dataset.off };
  }

  function saveProgress(now, opts) {
    opts = opts || {};
    if (!S.userMoved && !opts.force) return;   // 见 S.userMoved 的注释
    var a = currentAnchor();
    if (!a) return;
    var ch = a.ch, off = a.off;
    var pct = pctOf(ch, off);
    $('#rv-pct').textContent = pctText(pct, S.bytes);
    if (ch === S.lastCh && off === S.lastOff && !opts.force) return;
    var moved = (ch !== S.lastCh || off !== S.lastOff);
    S.lastCh = ch; S.lastOff = off;
    var jump = !!(opts.jump || S.jumpIntent);
    S.jumpIntent = false;
    api('/progress', {
      method: 'POST',
      body: { chapter: ch, offset: off, sid: S.sid,
              flush: !!opts.flush, jump: jump, end: atEnd(),
              // user=1: 这是读者自己动出来的位置 (翻页 / 跳转 / 关页面),
              // 云端按"最后写入者胜"被覆写 —— 包括比云端更靠后的位置。
              user: opts.user === false ? 0 : 1 }
    }).then(function (r) {
      if (!r) return;
      if (r.stale) {
        // 服务端判定"这是落后于别处的旧位置": 保留更靠前的进度, 页面跟着对齐
        toast('别处读到更后面, 已保留那边的进度', 2600);
        if (r.chapter != null && (+r.chapter !== ch || +r.offset !== off)) {
          S.lastCh = null; S.lastOff = null;
          jumpTo(+r.chapter, +r.offset);
        }
      } else if (r.percent != null) {
        $('#rv-pct').textContent = pctText(r.percent, S.bytes);
      }
      if (r.sync) renderStamp(r.sync);
    }).catch(function () { /* 离线时静默, 下次滚动再试 */ });
    if (moved && opts.flush) flushProgress();
  }

  // ---------- 云端同步 (进度) ----------
  // 口径: 服务端负责"只前进"的闸门; 前端只做三件事 ——
  //   1. 打开时先对一次 (云端更靠前就跳过去)
  //   2. 右下角那行小字 (可在设置页关掉)
  //   3. 离开 / 切后台时把最后位置立刻推上去 (不等节流)
  var stampEl = $('#rv-sync');

  function clockOf(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    return ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2);
  }

  function stampPct(p) {
    if (p == null) return '—';
    return (Math.floor((+p || 0) * 1000) / 10).toFixed(1) + '%';
  }

  // 上一次成功同步的样子 (没有记录就是「尚未同步」)。既用于常规渲染,
  // 也用于「正在推送」时保留旧信息 —— 那时不刷新文本, 只在这行还空着时铺一次。
  function lastStampText(s) {
    var t = clockOf(s && s.last_push_at);
    return t ? ('掌阅 ' + t + ' 已同步 ' + stampPct(s.last_push_pct))
             : '掌阅 尚未同步';
  }

  function renderStamp(st) {
    if (!stampEl) return;
    var cfg = (st && st.settings) || (S.sync && S.sync.settings) || {};
    var s = (st && st.state !== undefined) ? st : ((S.sync || {}).state || {});
    if (!cfg.stamp || !cfg.auto || !s || !s.state) { stampEl.hidden = true; return; }
    if (s.state === 'no-link' || s.state === 'off') { stampEl.hidden = true; return; }
    var txt;
    if (s.state === 'pushing' || s.state === 'dirty') {
      // 正在推 / 排队中: 不打扰读者 —— 保留上一次的同步信息
      // (节流窗口里 dirty 会持续十几秒, 反复刷那行只是噪声)。
      // 还没有任何历史文本时, 才按 last_push_* 铺一次同样的「已同步 x%」。
      if (!stampEl.hidden && stampEl.textContent) return;
      txt = lastStampText(s);
    } else if (s.state === 'failed') {
      txt = '掌阅 同步失败';
    } else if (s.state === 'cloud-ahead') {
      // 云端读得更靠前, 这一推还在节流窗口里; 读者一动就按本地覆写
      txt = '掌阅 更靠前 ' + stampPct(s.cloud_pct != null ? s.cloud_pct : s.last_push_pct);
    } else if (s.rewound) {
      // 读者自己往回读了 —— 云端跟着覆写, 小字直说
      txt = '掌阅 已按本地覆写 ' + stampPct(s.last_push_pct);
    } else {
      txt = lastStampText(s);
    }
    stampEl.textContent = txt;
    stampEl.hidden = false;
  }

  function syncPoll() {
    clearInterval(S.syncT);
    S.syncT = setInterval(function () {
      if (document.visibilityState === 'hidden') return;
      api('/sync-state').then(renderStamp).catch(function () {});
    }, 20000);
  }

  function flushProgress() {
    var cfg = (S.sync && S.sync.settings) || {};
    if (!cfg.auto || cfg.flush_on_hide === false) return;
    if (!S.userMoved) return;                  // 没动过 = 库里的位置已经是对的
    var a = currentAnchor();
    if (!a) return;
    // 节流窗口里可能还有一次没推上去 —— 离开页面时补上 (服务端 flush 绕过节流)。
    // 用 keepalive fetch 而不是 sendBeacon: 同样的"页面走了还发得出去", 但
    // 请求体是普通 JSON, 排查时看得见 (beacon 的 body 在日志里往往是空的)。
    var payload = JSON.stringify({ chapter: a.ch, offset: a.off, sid: S.sid, flush: 1, user: 1 });
    try {
      fetch(API + '/progress', {
        method: 'POST', keepalive: true,
        headers: { 'Content-Type': 'application/json' }, body: payload
      }).catch(function () {});
    } catch (e) {
      beacon('/progress', { chapter: a.ch, offset: a.off, sid: S.sid, flush: 1, user: 1 });
    }
  }

  // 云端比本地靠前时, 让读者自己决定: 跳过去, 还是留在本地继续读。
  // 以前是"拉进来 + 自动跳" —— 读者正等着看正文, 位置突然被拽走, 而且
  // 原来的位置已经被覆盖, 想留下都留不住。现在打开这一刻只比不写。
  function askCloudChoice(r) {
    var el = $('#rv-choice');
    if (!el || S.choiceAsked) return;
    S.choiceAsked = true;
    var cp = r.cloud_pct, lp = r.local_pct;
    var txt = $('#rv-choice-txt');
    if (txt) {
      txt.innerHTML =
        '掌阅云端读到 <b>' + stampPct(cp) + '</b>（第 ' + ((+r.cloud_chapter || 0) + 1) + ' 章）' +
        '<br>这里停在 <b>' + stampPct(lp) + '</b>';
    }
    el.hidden = false;
    clearTimeout(S.choiceT);
    S.choiceT = setTimeout(hideChoice, 12000);
  }

  function hideChoice() {
    clearTimeout(S.choiceT);
    var el = $('#rv-choice');
    if (el) el.hidden = true;
  }

  function chooseCloud(which) {
    hideChoice();
    api('/sync-choice', { method: 'POST', body: { choice: which } }).then(function (r) {
      if (!r) return;
      if (r.state) { S.sync.state = r.state; renderStamp(r.state); }
      if (which === 'cloud') {
        var a = r.anchor || {};
        if (a.chapter == null) return;
        S.lastCh = null; S.lastOff = null;
        jumpTo(+a.chapter || 0, +a.offset || 0);
        // 服务端已经把云端位置写进库了; 这里只把"已保存锚点"钉到目标上,
        // 不再补一次保存 —— 跳转刚发起时 DOM 还没排好, 量出来的锚点会偏。
        S.lastCh = +a.chapter || 0;
        S.lastOff = +a.offset || 0;
        var cn = (a.catalog != null) ? (+a.catalog + 1) : ((+a.chapter || 0) + 1);
        toast('已同步掌阅进度 · ' + stampPct(a.percent) + ' · 第 ' + cn + ' 章', 2400);
      } else if (r.ok) {
        toast('留在本地 · 已把当前进度写到云端', 2600);
      } else {
        toast('留在本地, 但这次没写上去 (' + (r.error || r.skipped || '稍后自动重试') + ')', 3200);
      }
    }).catch(function () { toast('同步失败, 稍后自动重试', 2200); });
  }

  // 打开一本书时先跟云端对一次: 云端更靠前 → 问读者; 本地更靠前 → 说明该推。
  function autoPull() {
    var cfg = (S.sync && S.sync.settings) || {};
    if (!cfg.auto || !cfg.pull_on_open) return;
    api('/sync').then(function (r) {
      if (!r) return;
      if (r.state) { S.sync.state = r.state; renderStamp(r.state); }
      if (r.need_choice && r.anchor) { askCloudChoice(r); return; }
      if (r.skipped === 'local-ahead') queueSave(true);
    }).catch(function () { /* 离线: 按本地位置读 */ });
  }

  // 选择条上的两个按钮 (document 级委托: 面板/正文随时会被替换)
  document.addEventListener('click', function (e) {
    var t = e.target;
    var b = t && t.closest ? t.closest('#rv-choice [data-c]') : null;
    if (!b) return;
    e.preventDefault();
    chooseCloud(b.getAttribute('data-c'));
  });

  function syncBoot(cfg) {
    S.sync = cfg || S.sync || {};
    renderStamp(S.sync.state);
    if (S.sync.settings && S.sync.settings.stamp) syncPoll();
    if (!S.pulled) { S.pulled = true; autoPull(); }
  }

  // ---------- 选区 / 划线 ----------
  function blockOf(node) {
    var el = node && (node.nodeType === 1 ? node : node.parentElement);
    while (el && el !== document.body) {
      if (el.classList && el.classList.contains('rv-blk')) return el;
      el = el.parentElement;
    }
    return null;
  }

  function charIndexIn(blockEl, node, offset) {
    try {
      var r = document.createRange();
      r.selectNodeContents(blockEl);
      r.setEnd(node, offset);
      return r.toString().length;
    } catch (e) { return 0; }
  }

  function collectSelection() {
    var sel = window.getSelection();
    if (!sel || sel.isCollapsed) return null;
    var a = sel.anchorNode, b = sel.focusNode;
    if (!a || !b) return null;
    var ba = blockOf(a), bb = blockOf(b);
    if (!ba || !bb) return null;
    var text = sel.toString();
    if (!text.trim()) return null;
    var order = (sel.anchorNode === sel.focusNode)
      ? (sel.anchorOffset <= sel.focusOffset ? 1 : -1)
      : (sel.containsNode(ba, true) || ba.compareDocumentPosition(bb) & Node.DOCUMENT_POSITION_FOLLOWING ? 1 : -1);
    var sNode = order > 0 ? a : b, sOff = order > 0 ? sel.anchorOffset : sel.focusOffset;
    var eNode = order > 0 ? b : a, eOff = order > 0 ? sel.focusOffset : sel.anchorOffset;
    var sBlock = order > 0 ? ba : bb, eBlock = order > 0 ? bb : ba;
    if (+sBlock.dataset.ch !== +eBlock.dataset.ch) {
      // 跨章的选择截断到起始章（云端的位置也只在单章内）
      eBlock = sBlock; eNode = sNode; eOff = sNode.length || 0;
      try {
        var rr = sel.getRangeAt(0);
        var last = $$('.rv-blk', sBlock.parentElement).filter(function (x) { return x === sBlock; });
        void last;
      } catch (e2) { /* ignore */ }
    }
    var sc = charIndexIn(sBlock, sNode, sOff);
    var ec = charIndexIn(eBlock, eNode, eOff);
    if (ec < sc) { var t = sc; sc = ec; ec = t; }
    var sByte = +sBlock.dataset.off + bytesOf(textPrefix(sBlock, sNode, sOff));
    var eByte = +eBlock.dataset.off + bytesOf(textPrefix(eBlock, eNode, eOff));
    if (eByte <= sByte) return null;
    return {
      chapter: +sBlock.dataset.ch,
      start: sByte, end: eByte,
      text: text.slice(0, 2000),
      anchors: {
        sb: +sBlock.dataset.off, sc: sc,
        eb: +eBlock.dataset.off, ec: ec
      }
    };
  }

  function textPrefix(blockEl, node, offset) {
    try {
      var r = document.createRange();
      r.selectNodeContents(blockEl);
      r.setEnd(node, offset);
      return r.toString();
    } catch (e) { return ''; }
  }

  var selBar = $('#rv-sel');
  function showSelBar(ev) {
    var info = collectSelection();
    S.sel = info;
    if (!info) { selBar.hidden = true; return; }
    selBar.hidden = false;
  }
  document.addEventListener('selectionchange', function () {
    clearTimeout(showSelBar._t);
    showSelBar._t = setTimeout(showSelBar, 180);
  });
  document.addEventListener('touchend', function () { setTimeout(showSelBar, 220); });
  document.addEventListener('mouseup', function () { setTimeout(showSelBar, 10); });

  selBar.addEventListener('click', function (ev) {
    var act = ev.target.dataset.sel;
    if (!act) return;
    if (act === 'cancel') { selBar.hidden = true; window.getSelection().removeAllRanges(); return; }
    var info = S.sel;
    if (!info) return;
    if (act === 'copy') {
      navigator.clipboard && navigator.clipboard.writeText(info.text);
      toast('已复制');
    } else if (act === 'hl') {
      saveNote(info, '', '');
    } else if (act === 'note') {
      openNoteEdit(info);
    }
    selBar.hidden = true;
  });

  // ---------- 笔记编辑 ----------
  var noteEdit = $('#rv-noteedit');
  var pending = null;
  var DRAFT = {};   // 点正文关掉编辑框时暂存草稿, 同一处再打开还在

  function draftKey(info, existing) {
    return existing ? 'n' + existing.id : 's' + (info ? info.start : '0');
  }

  function openNoteEdit(info, existing) {
    pending = { info: info, existing: existing || null };
    var k = draftKey(info, existing || null);
    $('#rv-noteedit-q').textContent = info ? info.text.slice(0, 160) : (existing ? existing.summary : '');
    $('#rv-noteedit-t').value = DRAFT[k] != null ? DRAFT[k] : (existing ? (existing.remark || '') : '');
    noteEdit.hidden = false;
    $('#rv-noteedit-t').focus();
  }

  function closeNoteEdit() {
    if (pending) {
      var k = draftKey(pending.info, pending.existing);
      var v = $('#rv-noteedit-t').value;
      if (v.trim()) DRAFT[k] = v; else delete DRAFT[k];
    }
    noteEdit.hidden = true;
    pending = null;
  }

  noteEdit.addEventListener('click', function (ev) {
    var act = ev.target.dataset.ne;
    if (!act) return;
    if (act === 'cancel') { closeNoteEdit(); return; }
    if (act === 'save') {
      var remark = $('#rv-noteedit-t').value.trim();
      if (pending && pending.existing) {
        api('/note', { method: 'POST', body: { action: 'update', id: pending.existing.id, remark: remark } })
          .then(function () { closeNoteEdit(); reloadNotes(); });
      } else if (pending) {
        saveNote(pending.info, remark, 'yellow');
      }
    }
  });

  function saveNote(info, remark, color) {
    api('/note', {
      method: 'POST',
      body: {
        chapter: info.chapter, start: info.start, end: info.end,
        summary: info.text.slice(0, 1000), remark: remark || '',
        color: color || 'yellow', anchors: info.anchors
      }
    }).then(function (r) {
      if (r && r.note) {
        S.notes.push(r.note);
        paintHighlights(info.chapter);
        toast(remark ? '已保存笔记' : '已划线');
      }
      if (pending) delete DRAFT[draftKey(pending.info, pending.existing)];
      $('#rv-noteedit-t').value = '';
      closeNoteEdit();
      window.getSelection && window.getSelection().removeAllRanges();
    }).catch(function (e) { toast('保存失败: ' + e.message); });
  }

  function reloadNotes() {
    api('').then(function (m) {
      if (!m) return;
      S.notes = m.notes || [];
      S.marks = m.bookmarks || [];
      Object.keys(S.loaded).forEach(function (i) { paintHighlights(+i); });
      renderLists();
    });
  }

  // ---------- 高亮 ----------
  function charToNode(blockEl, chars) {
    var walker = document.createTreeWalker(blockEl, NodeFilter.SHOW_TEXT, null);
    var remaining = chars, n;
    while ((n = walker.nextNode())) {
      var len = n.nodeValue.length;
      if (remaining <= len) return { node: n, offset: remaining };
      remaining -= len;
    }
    return null;
  }

  function findBlock(ch, off) {
    var box = chapterEl(ch);
    if (!box) return null;
    return $('.rv-blk[data-off="' + off + '"]', box);
  }

  function rangeFor(note) {
    var a = {};
    try { a = JSON.parse(note.anchors || '{}'); } catch (e) { a = {}; }
    var ch = +note.chapter;
    var sb = a.sb, sc = a.sc, eb = a.eb, ec = a.ec;
    var sBlock = sb != null ? findBlock(ch, sb) : null;
    var eBlock = eb != null ? findBlock(ch, eb) : null;
    if (!sBlock) sBlock = directBlockByOffset(ch, +note.start_off);
    if (!eBlock) eBlock = directBlockByOffset(ch, +note.end_off);
    if (!sBlock || !eBlock) return searchRange(ch, note);
    var s = charToNode(sBlock, sc || 0);
    var e = charToNode(eBlock, ec || 0);
    if (!s || !e) return searchRange(ch, note);
    try {
      var r = document.createRange();
      r.setStart(s.node, s.offset);
      r.setEnd(e.node, e.offset);
      return r;
    } catch (err) { return searchRange(ch, note); }
  }

  function directBlockByOffset(ch, off) {
    var box = chapterEl(ch);
    if (!box) return null;
    var els = $$('.rv-blk', box);
    for (var i = 0; i < els.length; i++) {
      if (+els[i].dataset.off <= off) return els[i];
    }
    return els[0] || null;
  }

  function searchRange(ch, note) {
    // 兜底：在章内按文本找（锚点丢失时仍能标出来）
    var box = chapterEl(ch);
    if (!box || !note.summary) return null;
    var target = note.summary.slice(0, 40);
    var els = $$('.rv-blk', box);
    for (var i = 0; i < els.length; i++) {
      var t = els[i].textContent || '';
      var pos = t.indexOf(target);
      if (pos >= 0) {
        var s = charToNode(els[i], pos);
        var e = charToNode(els[i], pos + (note.summary.length || target.length));
        if (s && e) {
          try {
            var r = document.createRange();
            r.setStart(s.node, s.offset);
            r.setEnd(e.node, e.offset);
            return r;
          } catch (err) { return null; }
        }
      }
    }
    return null;
  }

  function paintHighlights(ch) {
    if (!S.loaded[ch]) return;
    if (isPaged()) flow = null;          // 兜底高亮会插 <mark>, 页号表要重建
    var useApi = !!(window.CSS && window.CSS.highlights && window.Highlight);
    if (!S.hlReady && useApi) {
      var st = document.createElement('style');
      st.textContent = '@media all{}';
      document.head.appendChild(st);
      S.hlReady = true;
    }
    if (useApi) {
      $$('.rv-blk mark.rv-mark', S.loaded[ch]).forEach(function (m) {
        var p = m.parentNode;
        while (m.firstChild) p.insertBefore(m.firstChild, m);
        p.removeChild(m);
        p.normalize();
      });
    }
    var names = ['yellow', 'green', 'blue', 'pink'];
    if (useApi) names.forEach(function (n) { CSS.highlights.delete('rv-hl-' + n); });
    S.notes.filter(function (n) { return +n.chapter === ch; }).forEach(function (n) {
      var r = rangeFor(n);
      if (!r) return;
      var color = (n.color || 'yellow').replace(/[^a-z]/g, '') || 'yellow';
      if (useApi) {
        var name = 'rv-hl-' + (names.indexOf(color) >= 0 ? color : 'yellow');
        var h = CSS.highlights.get(name) || new Highlight();
        h.add(r);
        h.priority = 1;   // 自己的划线/笔记永远盖在关键词底色之上
        CSS.highlights.set(name, h);
      } else {
        try {
          var mark = document.createElement('mark');
          mark.className = 'rv-mark';
          mark.dataset.color = color;
          mark.dataset.note = n.id;
          r.surroundContents(mark);
        } catch (e) { /* 跨节点选择在不支持 Range API 时跳过 */ }
      }
    });
  }

  // ---------- 关键词高亮 (语料滤镜) ----------
  // 语料 = 语料分解 json (epub-refiner 导出的 keywords.json, 或书库里的
  // `<书名>.keywords.json`)。默认走 CSS Custom Highlight API: 一个字符都不改
  // DOM —— 字节坐标进度 / 划线选区 / 分页的列碎片化全都不受影响。
  // span 模式才包 <span> (能加粗/圆角), 代价是改 DOM。
  var KM = { data: null, trie: null, words: 0, ranges: 0, spans: 0, err: '',
             mode: '', names: [], on: localStorage.getItem('recto.kw') };

  function kmOn() {
    var d = KM.data;
    if (!d || !d.enabled || !d.total) return false;
    return KM.on !== '0';
  }

  var KM_ALNUM = /[0-9A-Za-z]/;

  // 词典树: 同一位置上"最长命中优先"("陈平安"不会被"陈平"抢先),
  // 比一条几千分支的正则快得多, 也不用担心正则回溯。
  function kmBuildTrie(data) {
    var root2 = { c: Object.create(null), w: null }, n = 0;
    (data.cats || []).forEach(function (cat) {
      (cat.words || []).forEach(function (w) {
        var s = String(w == null ? '' : w).trim().toLowerCase();
        if (!s || s.length > 80) return;
        var node = root2;
        for (var i = 0; i < s.length; i++) {
          var ch = s.charAt(i);
          var nx = node.c[ch];
          if (!nx) { nx = node.c[ch] = { c: Object.create(null), w: null }; }
          node = nx;
        }
        if (!node.w) { node.w = { cat: cat.id, len: s.length, low: s }; n++; }
      });
    });
    KM.trie = root2;
    KM.words = n;
  }

  function kmFind(text, out) {
    if (!KM.trie || !text) return;
    var low = text;
    try {
      var lc = text.toLowerCase();
      // 极少数字符 toLowerCase 会变长度 —— 那就退回原串比 (只损失大小写不敏感)
      if (lc.length === text.length) low = lc;
    } catch (e) { /* ignore */ }
    var trie = KM.trie, n = low.length;
    for (var i = 0; i < n; i++) {
      var node = trie.c[low.charAt(i)];
      if (!node) continue;
      var best = node.w ? node.w : null, j = i + 1;
      while (j < n) {
        var nx = node.c[low.charAt(j)];
        if (!nx) break;
        node = nx; j++;
        if (node.w) best = node.w;
      }
      if (!best) continue;
      var end = i + best.len;
      var w0 = best.low.charAt(0), w1 = best.low.charAt(best.len - 1);
      // 拉丁词要卡在词边界上: "the" 不该命中 "there"
      if (KM_ALNUM.test(w0) && i > 0 && KM_ALNUM.test(low.charAt(i - 1))) continue;
      if (KM_ALNUM.test(w1) && end < n && KM_ALNUM.test(low.charAt(end))) continue;
      out.push({ s: i, e: end, cat: best.cat });
      i = end - 1;
    }
  }

  function kmContainer() { return $('#rv-content'); }

  function kmClear() {
    if (window.CSS && window.CSS.highlights) {
      (KM.names || []).forEach(function (n) {
        try { window.CSS.highlights.delete(n); } catch (e) { /* noop */ }
      });
    }
    KM.names = [];
    var box = kmContainer();
    var sp = box ? $$('.rv-kw', box) : [];
    if (sp.length) {
      sp.forEach(function (e) {
        var p = e.parentNode;
        if (p) p.replaceChild(document.createTextNode(e.textContent), e);
      });
      try { box.normalize(); } catch (e) { /* noop */ }
    }
    KM.ranges = 0; KM.spans = 0;
  }

  function kmWalk(root2, fn) {
    var w = document.createTreeWalker(root2, NodeFilter.SHOW_TEXT, {
      acceptNode: function (n) {
        var v = n.nodeValue;
        if (!v || !v.trim()) return NodeFilter.FILTER_REJECT;
        var pa = n.parentNode;
        if (!pa) return NodeFilter.FILTER_REJECT;
        var tag = (pa.nodeName || '').toLowerCase();
        if (tag === 'script' || tag === 'style' || tag === 'svg') return NodeFilter.FILTER_REJECT;
        if (pa.classList && pa.classList.contains('rv-kw')) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    var n;
    while ((n = w.nextNode())) fn(n);
  }

  function kmCssText(data, mode) {
    var out = ['/* recto keyword mark v' + (data.version || 0) + ' */'];
    (data.cats || []).forEach(function (c) {
      var st = c.style || {};
      var decl = [];
      if (st.bg) decl.push('background-color:' + st.bg);
      if (st.color) decl.push('color:' + st.color);
      if (st.underline) decl.push('text-decoration:underline');
      if (st.shadow) decl.push('text-shadow:' + st.shadow);
      if (mode === 'highlight') {
        if (decl.length) out.push('::highlight(rv-kw-' + c.id + '){' + decl.join(';') + ';}');
        return;
      }
      if (st.bold) decl.push('font-weight:600');
      if (st.italic) decl.push('font-style:italic');
      if (st.radius) decl.push('border-radius:' + st.radius);
      if (st.pad) decl.push('padding:' + st.pad);
      if (decl.length) out.push('.rv-kw-' + c.id + '{' + decl.join(';') + ';}');
    });
    if (mode !== 'highlight') out.push('.rv-kw{box-decoration-break:clone;}');
    return out.length > 1 ? out.join('\n') : '';
  }

  function kmInjectCss(css) {
    var node = $('#rv-kw-css');
    if (!css) {
      if (node && node.parentNode) node.parentNode.removeChild(node);
      return;
    }
    if (!node) {
      node = document.createElement('style');
      node.id = 'rv-kw-css';
      document.head.appendChild(node);
    }
    node.textContent = css;
  }

  function kmApply() {
    if (!KM.data) return;
    var data = KM.data;
    var canApi = !!(window.CSS && window.CSS.highlights && typeof Highlight === 'function');
    var mode = data.mode === 'span' ? 'span' : 'highlight';
    if (mode === 'highlight' && !canApi) mode = 'span';   // 老浏览器降级
    KM.mode = mode;
    KM.err = '';
    kmClear();
    if (!kmOn()) { kmInjectCss(''); return; }
    if (!KM.trie) kmBuildTrie(data);
    kmInjectCss(kmCssText(data, mode));
    var box = kmContainer();
    if (!box) return;
    var per = Object.create(null), jobs = [];
    kmWalk(box, function (node) {
      var hits = [];
      kmFind(node.nodeValue, hits);
      if (!hits.length) return;
      if (mode === 'highlight') {
        hits.forEach(function (h) {
          var r = document.createRange();
          r.setStart(node, h.s);
          r.setEnd(node, h.e);
          (per[h.cat] = per[h.cat] || []).push(r);
        });
        return;
      }
      var txt = node.nodeValue, last = 0, frag = null;
      hits.forEach(function (h) {
        if (!frag) frag = document.createDocumentFragment();
        if (h.s > last) frag.appendChild(document.createTextNode(txt.slice(last, h.s)));
        var sp = document.createElement('span');
        sp.className = 'rv-kw rv-kw-' + h.cat;
        sp.textContent = txt.slice(h.s, h.e);
        frag.appendChild(sp);
        last = h.e;
        KM.spans++;
      });
      if (frag) {
        if (last < txt.length) frag.appendChild(document.createTextNode(txt.slice(last)));
        jobs.push([node, frag]);
      }
    });
    if (mode === 'span') {
      jobs.forEach(function (j) { if (j[0].parentNode) j[0].parentNode.replaceChild(j[1], j[0]); });
      KM.ranges = KM.spans;
      return;
    }
    if (!canApi) return;
    Object.keys(per).forEach(function (cat) {
      try {
        var hl = new Highlight();
        per[cat].forEach(function (r) { hl.add(r); });
        hl.priority = 0;
        window.CSS.highlights.set('rv-kw-' + cat, hl);
        KM.names.push('rv-kw-' + cat);
        KM.ranges += per[cat].length;
      } catch (e) { KM.err = String((e && e.message) || e); }
    });
  }

  function kmPaintNote() {
    var row = $('#rv-kw-row'), note = $('#rv-kw-note');
    if (!row || !note) return;
    var d = KM.data;
    var has = !!(d && d.total);
    row.hidden = !has;
    $$('[data-kw]', row).forEach(function (b) {
      b.setAttribute('aria-pressed', (kmOn() ? '1' : '0') === b.dataset.kw ? 'true' : 'false');
    });
    note.hidden = false;
    if (has) {
      note.innerHTML = '语料 ' + escapeHtml(d.title || d.file || '') + ' · ' +
        (d.cats || []).length + ' 类 ' + d.total + ' 词' +
        (KM.mode === 'span' ? ' · span 模式会改动排版' : '');
      return;
    }
    note.innerHTML = (d && d.enabled === false)
      ? '关键词高亮已在设置页关闭。'
      : '这本书没匹配到语料 —— 在 <a href="/settings#mark">设置 · 阅读高亮</a> 里指一份即可。';
  }

  function kmLoad() {
    return api('/mark').then(function (d) {
      KM.data = d || null;
      KM.trie = null;
      if (d && d.cats) kmBuildTrie(d);
      kmApply();
      kmPaintNote();
    }).catch(function () { KM.data = null; });
  }

  function setKw(on) {
    KM.on = on ? '1' : '0';
    try { localStorage.setItem('recto.kw', KM.on); } catch (e) { /* noop */ }
    var keep = currentAnchor();
    kmApply();
    kmPaintNote();
    if (isPaged()) layoutPaged(keep || null, { noSave: true });
    else if (keep) scrollToOffset(keep.ch, keep.off);
  }

  // ---------- 书签 / 笔记面板（两个独立列表） ----------
  function renderMarkList() {
    var box = $('#rv-marks-list');
    var n = S.marks.length;
    $('#rv-marks-cnt').textContent = n ? n + ' 条' : '';
    if (!n) { box.innerHTML = '<div class="rv-empty">还没有书签</div>'; return; }
    box.innerHTML = S.marks.map(function (m) {
      return '<div class="rv-note-item" data-jump="' + m.chapter + '" data-off="' + m.offset + '">' +
        '<div class="q">🔖 ' + escapeHtml(m.summary || '书签') + '</div>' +
        '<div class="m"><span>' + escapeHtml(m.title || '') + '</span>' +
        '<button data-delmark="' + m.id + '">删除</button></div></div>';
    }).join('');
  }

  function renderNoteList() {
    var box = $('#rv-notes-list');
    var n = S.notes.length;
    $('#rv-notes-cnt').textContent = n ? n + ' 条' : '';
    if (!n) { box.innerHTML = '<div class="rv-empty">还没有划线或笔记</div>'; return; }
    box.innerHTML = S.notes.map(function (x) {
      return '<div class="rv-note-item" data-jump="' + x.chapter + '" data-off="' + x.start_off + '">' +
        '<div class="q"><span class="dot" style="background:' + colorHex(x.color) + '"></span> ' +
        escapeHtml(x.summary || '') + '</div>' +
        (x.remark ? '<div class="r">' + escapeHtml(x.remark) + '</div>' : '') +
        '<div class="m"><span>' + (x.remark ? '笔记' : '划线') + '</span>' +
        '<button data-editnote="' + x.id + '">编辑</button>' +
        '<button data-delnote="' + x.id + '">删除</button></div></div>';
    }).join('');
  }

  function renderLists() { renderMarkList(); renderNoteList(); }

  function colorHex(c) {
    return { yellow: '#f5c518', green: '#4caf50', blue: '#3d8bd9', pink: '#e0699a', underline: '#c0392b' }[c] || '#f5c518';
  }

  $('#rv-marks').addEventListener('click', function (ev) {
    var t = ev.target;
    if (t.dataset.delmark) {
      api('/bookmark', { method: 'POST', body: { action: 'delete', id: +t.dataset.delmark } })
        .then(function () { return api(''); })
        .then(function (m) { if (m) { S.marks = m.bookmarks; S.notes = m.notes; renderLists(); } });
      return;
    }
    var item = t.closest ? t.closest('.rv-note-item') : null;
    if (item && item.dataset.jump != null) {
      jumpTo(+item.dataset.jump, +item.dataset.off);
      closeDrawer('marks');
    }
  });

  $('#rv-notes').addEventListener('click', function (ev) {
    var t = ev.target;
    if (t.dataset.delnote) {
      api('/note', { method: 'POST', body: { action: 'delete', id: +t.dataset.delnote } })
        .then(function () { reloadNotes(); });
      return;
    }
    if (t.dataset.editnote) {
      var n = S.notes.filter(function (x) { return x.id === +t.dataset.editnote; })[0];
      if (n) openNoteEdit(null, n);
      return;
    }
    var item = t.closest ? t.closest('.rv-note-item') : null;
    if (item && item.dataset.jump != null) {
      jumpTo(+item.dataset.jump, +item.dataset.off);
      closeDrawer('notes');
    }
  });

  function jumpTo(ch, off) {
    S.jumpIntent = true;          // 读者主动跳转: 允许把进度写回/覆写云端
    if (isPaged()) {
      if (S.loaded[ch] && blockForAnchor(ch, off)) {
        S.cur = ch; markToc();
        layoutPaged({ ch: ch, off: off });
      } else {
        openChapter(ch, { offset: off });
      }
      return;
    }
    if (S.loaded[ch]) { S.cur = ch; markToc(); scrollToOffset(ch, off); }
    else openChapter(ch, { offset: off });
  }

  // ---------- 抽屉 / 弹层 ----------
  var DRAWERS = ['toc', 'marks', 'notes'];
  function drawerEl(name) { return $('#rv-' + name); }
  function openDrawer(name) {
    var el = drawerEl(name);
    if (el.classList.contains('rv-on')) { closeDrawer(name); return; }   // 再点一次 = 收起
    DRAWERS.forEach(function (n) {
      if (n === name) return;
      drawerEl(n).classList.remove('rv-on');
      drawerEl(n).setAttribute('aria-hidden', 'true');
    });
    el.classList.add('rv-on');
    el.setAttribute('aria-hidden', 'false');
    if (name === 'marks') renderMarkList();
    if (name === 'notes') renderNoteList();
    setBar(false);
  }
  function closeDrawer(name) {
    var el = drawerEl(name);
    if (!el) return;
    el.classList.remove('rv-on');
    el.setAttribute('aria-hidden', 'true');
  }

  /* 点正文即关掉所有浮层（抽屉 / 选区工具条 / 笔记编辑 / 排版面板） */
  function dismissPopups() {
    var hit = false;
    DRAWERS.forEach(function (n) {
      if (drawerEl(n).classList.contains('rv-on')) { closeDrawer(n); hit = true; }
    });
    if (!noteEdit.hidden) { closeNoteEdit(); hit = true; }
    if (!selBar.hidden) { selBar.hidden = true; hit = true; }
    if (typoEl && !typoEl.hidden) { hideTypo(); hit = true; }
    return hit;
  }

  // ---------- 顶栏自动隐藏 ----------
  var bar = $('#rv-bar');
  var prog = $('#rv-prog');
  var barHidden = false;
  var lastY = window.scrollY;

  function setBar(hide) {
    if (hide === barHidden) return;
    barHidden = hide;
    bar.classList.toggle('is-hidden', hide);
    root.classList.toggle('is-immersive', hide);
    bar.setAttribute('aria-hidden', hide ? 'true' : 'false');
    if (hide) hideTypo();
  }

  function syncProg() {
    var f;
    if (isPaged()) {
      f = S.pageCount > 1 ? S.page / (S.pageCount - 1) : 0;
    } else {
      var total = document.documentElement.scrollHeight - window.innerHeight;
      f = total > 8 ? window.scrollY / total : 0;
    }
    prog.style.width = (Math.min(1, Math.max(0, f)) * 100) + '%';
  }

  // ---------- 排版面板 ----------
  var typoEl = $('#rv-typo');
  var typoBtn = $('#rv-typo-btn');
  var fontInp = $('#rv-font');          // 也可以直接敲字体名
  var fontBox = $('#rv-font-box');      // 组合控件 (输入框 + 自绘下拉)
  var fontMenu = $('#rv-font-menu');    // 自绘候选清单
  var fontOpenBtn = $('#rv-font-open'); // 右侧小箭头
  var fontScanBtn = $('#rv-font-scan');
  var fontNote = $('#rv-font-note');
  var fontExtra = [];       // 来自 Local Font Access (queryLocalFonts) 的真实清单
  var fontExtraLoaded = false;
  var fontOptions = [];     // [{v, label}] —— 清单唯一真源
  var fontCursor = -1;      // 键盘高亮的下标 (指向当前筛过的列表)
  var fontQuery = '';       // 展开时的筛选词; 点箭头打开 = 空 (全量)

  // 字体 = 轻量清单 + 手动输入 + 自绘下拉。
  // 旧做法用 canvas 把上百个候选逐个"量宽度验证", 打开面板要跑几千次 measureText。
  // 之后短暂用过 <datalist>, 但它的弹层是浏览器自己画的 (灰底/系统字体/宽度不受控),
  // 跟排版面板割裂 —— 现在候选清单由我们自己渲染 (.rv-combo-menu), 借同一套 --rv-* 变量;
  // 清单外的字体直接敲名字; 本机完整清单按需读 (Local Font Access)。
  var FONT_BUILTIN = [
    ['跟随界面', ''], ['衬线 serif', 'serif'], ['无衬线 sans-serif', 'sans-serif'],
    ['等宽 monospace', 'monospace']
  ];
  var FONT_COMMON = [
    // 中文: 衬线/宋
    '思源宋体', 'Source Han Serif SC', 'Noto Serif SC', 'Noto Serif CJK SC', '宋体', 'SimSun', 'Songti SC',
    // 中文: 楷/仿宋/文楷
    '楷体', 'KaiTi', 'Kaiti SC', '仿宋', 'FangSong', '霞鹜文楷', 'LXGW WenKai',
    // 中文: 黑/圆/系统
    '思源黑体', 'Source Han Sans SC', 'Noto Sans SC', 'Noto Sans CJK SC', '黑体', 'SimHei',
    'PingFang SC', '苹方-简', 'Microsoft YaHei', '微软雅黑', 'WenQuanYi Micro Hei', 'MiSans',
    'HarmonyOS Sans SC', 'Alibaba PuHuiTi',
    // 西文
    'Georgia', 'Times New Roman', 'Palatino', 'Baskerville', 'Cambria', 'Charter',
    'Crimson Text', 'Merriweather', 'Lora', 'PT Serif', 'IBM Plex Serif', 'Source Serif 4',
    'Helvetica', 'Helvetica Neue', 'Arial', 'Inter', 'Roboto', 'Segoe UI', 'Open Sans',
    'Consolas', 'Menlo', 'JetBrains Mono', 'Fira Code', 'Courier New'
  ];

  function setFontNote(msg) {
    if (!fontNote) return;
    fontNote.textContent = msg || '';
    fontNote.hidden = !msg;
  }

  // 清单 = 内置四档 + 常用名 + 读到的本机字体; 纯字符串拼接, 不做任何度量
  function fillFontList() {
    var seen = Object.create(null);
    var out = [];
    FONT_BUILTIN.forEach(function (it) {
      seen[it[1]] = 1;
      out.push({ v: it[1], label: it[0] });
    });
    FONT_COMMON.concat(fontExtra).forEach(function (n) {
      if (!n || seen[n]) return;
      seen[n] = 1;
      out.push({ v: n, label: n });
    });
    fontOptions = out;
    renderFontMenu();
  }
  var buildFontOptions = fillFontList;
  fillFontList();        // 先填一次: 输入框里显示「衬线 serif」这种可读名字

  function fontMatch(o, q) {
    if (!q) return true;
    q = q.toLowerCase();
    return (o.label + ' ' + o.v).toLowerCase().indexOf(q) >= 0;
  }

  function fontFiltered() {
    return fontOptions.filter(function (o) { return fontMatch(o, fontQuery); });
  }

  // 值 -> 给人看的名字 (serif 显示成「衬线 serif」, 输入框里更好认)
  function fontLabelFor(v) {
    v = normalizeFontValue(v);
    for (var i = 0; i < fontOptions.length; i++) {
      if (fontOptions[i].v === v) return fontOptions[i].label;
    }
    return v;
  }

  function renderFontMenu() {
    if (!fontMenu) return;
    var list = fontFiltered();
    var cur = normalizeFontValue(fontInp ? fontInp.value : '');
    if (!list.length) {
      fontMenu.innerHTML = '<div class="rv-combo-empty">没有匹配的字体名，' +
        '回车就直接用你输入的名字</div>';
      fontCursor = -1;
      return;
    }
    var h = [];
    list.forEach(function (o, i) {
      var on = normalizeFontValue(o.v) === cur;
      h.push('<button type="button" class="rv-combo-opt' + (on ? ' is-cur' : '') +
        (i === fontCursor ? ' is-on' : '') + '" role="option" data-idx="' + i + '"' +
        ' aria-selected="' + (on ? 'true' : 'false') + '">' +
        escapeHtml(o.label) +
        (o.v && o.v !== o.label ? '<small>' + escapeHtml(o.v) + '</small>' : '') +
        '</button>');
    });
    fontMenu.innerHTML = h.join('');
  }

  function fontMenuOpen() { return !!fontMenu && !fontMenu.hidden; }

  function openFontMenu(q) {
    if (!fontMenu) return;
    fontQuery = (q == null) ? '' : String(q);
    fontCursor = -1;
    renderFontMenu();
    fontMenu.hidden = false;
    if (fontBox) fontBox.classList.add('is-open');
    if (fontInp) fontInp.setAttribute('aria-expanded', 'true');
    var el = fontMenu.querySelector('.rv-combo-opt.is-cur');
    if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest' });
  }

  function closeFontMenu() {
    if (!fontMenuOpen()) return;
    fontMenu.hidden = true;
    fontCursor = -1;
    fontQuery = '';
    if (fontBox) fontBox.classList.remove('is-open');
    if (fontInp) fontInp.setAttribute('aria-expanded', 'false');
  }

  function pickFont(i) {
    var o = fontFiltered()[i];
    if (!o) return false;
    applyFontValue(o.v);
    return true;
  }

  // 输进来的可能是 "衬线 serif" 这种带说明的写法, 归一成 effective 值
  function normalizeFontValue(raw) {
    var v = String(raw == null ? '' : raw).trim();
    if (!v) return '';
    if (v === 'system-ui' || v === '跟随界面') return '';
    var m = /(^|[\s(（])(serif|sans-serif|monospace)$/i.exec(v);
    if (m) {
      // "衬线 serif" / "sans-serif" 都落到通用族; 但 "Noto Serif SC" 这类不能误伤
      if (v.toLowerCase() === m[2].toLowerCase()) return m[2].toLowerCase();
      if (/^(衬线|serif)\s*serif$/i.test(v)) return 'serif';
      if (/^(无衬线|sans)\s*sans-serif$/i.test(v)) return 'sans-serif';
      if (/^(等宽|mono)\s*monospace$/i.test(v)) return 'monospace';
    }
    return v;
  }

  function applyFontValue(raw) {
    var v = normalizeFontValue(raw);
    var keep = currentAnchor();
    S.look.font = v;
    try {
      if (v) localStorage.setItem('recto.font', v); else localStorage.removeItem('recto.font');
    } catch (e) { /* 隐私模式忽略 */ }
    applyLook();
    queueRelayout(keep);
    queueSave(true);
    if (fontInp) fontInp.value = fontLabelFor(v);
    setFontNote(v && v !== 'serif' && v !== 'sans-serif' && v !== 'monospace'
      ? '正文字体: ' + v : '');
  }
  // ---- 真实字体清单 (Local Font Access) ----
  // 只在"安全上下文"(https / localhost) 可用; 局域网 http 打开时浏览器不提供, 按钮自动隐藏。
  function localFontsSupported() { return typeof window.queryLocalFonts === 'function'; }

  function scanLocalFonts() {
    if (!localFontsSupported()) {
      setFontNote('此浏览器不提供完整字体清单 (需 https / localhost 打开)；' +
                  '清单外的字体直接在输入框里敲名字，回车即生效');
      return;
    }
    if (fontScanBtn) fontScanBtn.classList.add('is-busy');
    setFontNote('正在读取本机字体…');
    window.queryLocalFonts().then(function (list) {
      var seen = Object.create(null);
      (list || []).forEach(function (f) {
        var fam = (f && f.family) ? String(f.family).trim() : '';
        if (fam) seen[fam] = 1;
      });
      fontExtra = Object.keys(seen).sort(function (a, b) {
        return a.localeCompare(b, 'zh-Hans-CN', { numeric: true, sensitivity: 'base' });
      });
      fontExtraLoaded = true;
      fillFontList();
      setFontNote('已读到 ' + fontExtra.length + ' 个本机字体, 敲几个字就能筛');
    }).catch(function (err) {
      setFontNote('读取本机字体失败：' + ((err && err.name) || err) +
                  '（浏览器需要你允许访问本机字体）');
    }).then(function () {
      if (fontScanBtn) fontScanBtn.classList.remove('is-busy');
    });
  }

  // 已经授权过就静默补一次, 不用再点
  function autoScanLocalFonts() {
    if (!localFontsSupported() || fontExtraLoaded || !navigator.permissions) return;
    navigator.permissions.query({ name: 'local-fonts' }).then(function (st) {
      if (st && st.state === 'granted') scanLocalFonts();
    }).catch(function () { /* 浏览器不认识这个权限名, 忽略 */ });
  }

  var LOOK_UNIT = { fs: 'px', lh: '', para: 'em', pad: 'rem', w: 'rem' };

  function paintLook() {
    if (!typoEl) return;
    Object.keys(LOOK_UNIT).forEach(function (k) {
      var out = $('[data-val="' + k + '"]', typoEl);
      var inp = $('[data-look="' + k + '"]', typoEl);
      var v = S.look[k];
      var txt = k === 'lh' ? v.toFixed(2)
        : (Math.round(v * 10) / 10).toString();
      if (out) out.textContent = txt + LOOK_UNIT[k];
      if (inp && document.activeElement !== inp) inp.value = v;
    });
    if (fontInp && document.activeElement !== fontInp) fontInp.value = fontLabelFor(S.look.font);
    $$('[data-indent]', typoEl).forEach(function (b) {
      b.setAttribute('aria-pressed', b.dataset.indent === S.look.indent ? 'true' : 'false');
    });
  }

  function placeTypo() {
    if (!typoEl || typoEl.hidden) return;
    var r = bar.getBoundingClientRect();
    typoEl.style.top = Math.max(8, Math.round(r.bottom + 6)) + 'px';
  }

  function showTypo() {
    if (!typoEl) return;
    if (barHidden) setBar(false);
    buildFontOptions();
    autoScanLocalFonts();
    typoEl.hidden = false;
    if (typoBtn) typoBtn.setAttribute('aria-expanded', 'true');
    paintLook();
    placeTypo();
  }

  function hideTypo() {
    if (!typoEl || typoEl.hidden) return;
    typoEl.hidden = true;
    if (typoBtn) typoBtn.setAttribute('aria-expanded', 'false');
  }

  function toggleTypo() {
    if (!typoEl) return;
    if (typoEl.hidden) showTypo(); else hideTypo();
  }

  // 首行缩进: 0 = 无 (西文/现代版式), 2 = 两个汉字宽 (中文版式惯例)
  function setIndent(v) {
    v = (v === '0' || v === '2') ? v : '2';
    if (S.look.indent === v) return;
    var keep = currentAnchor();
    S.look.indent = v;
    try { localStorage.setItem('recto.indent', v); } catch (e) { /* 隐私模式忽略 */ }
    applyLook();
    paintLook();
    queueRelayout(keep);
    queueSave(true);
  }

  function resetLook() {
    S.look = lookDefaults();
    LOOK_NUM.concat(['font', 'indent']).forEach(function (k) {
      localStorage.removeItem('recto.' + k);
    });
    S.fx = 'slide';
    flipStop();
    try { localStorage.removeItem('recto.fx'); } catch (e) { /* 隐私模式忽略 */ }
    applyLook();
    paintLook();
    paintFx();
    queueRelayout();
    queueSave(true);
    toast('已恢复默认排版');
  }

  // ---------- 翻页效果 (左右翻页时新页面怎么进来) ----------
  // 位移永远瞬时到位 (rv-no-anim), 这里只做"入场"动画 —— 不参与分页测量,
  // 所以任何效果都不会让块→页号映射或续读位置飘。
  var FX_ALL = ['slide', 'none', 'fade', 'ripple', 'flip'];
  var FX_NAME = { slide: '滑动', none: '无', fade: '淡入', ripple: '水波纹', flip: '翻卷' };

  function prefersReduced() {
    try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; }
    catch (e) { return false; }
  }

  // 系统开了"减少动态效果"且用户自己没选过 → 按"无"处理
  function fxMode() {
    var v = S.fx || 'slide';
    if (FX_ALL.indexOf(v) < 0) v = 'slide';
    if (prefersReduced() && !fxStored()) return 'none';
    return v;
  }

  function fxStored() {
    try { return localStorage.getItem('recto.fx'); } catch (e) { return null; }
  }

  function paintFx() {
    FX_ALL.forEach(function (k) { root.classList.remove('rv-fx-' + k); });
    root.classList.add('rv-fx-' + (FX_ALL.indexOf(S.fx) >= 0 ? S.fx : 'slide'));
    var sel = $('#rv-fx');
    if (sel && sel.value !== S.fx) sel.value = S.fx;
  }

  function setFx(fx) {
    if (FX_ALL.indexOf(fx) < 0) fx = 'slide';
    if (fx === S.fx) { paintFx(); return; }
    S.fx = fx;
    try { localStorage.setItem('recto.fx', fx); } catch (e) { /* 隐私模式忽略 */ }
    if (fx !== 'flip') flipStop();          // 离开翻卷: 收掉还在转的那一页
    paintFx();
    if (isPaged()) playFx(fx, 1);          // 选中就当场演示一次
    toast('翻页效果: ' + FX_NAME[fx]);
  }

  // 翻页效果的视觉层都放进这张固定满屏的舞台 (不是只盖正文那一列)
  var fxStageEl = null;
  function fxStage() {
    if (!fxStageEl || !fxStageEl.parentNode) fxStageEl = $('#rv-fxstage');
    return fxStageEl;
  }

  // 往舞台上铺一层满屏的视觉层; ms 后无论如何都收掉 (WAAPI 不支持的兜底)
  function fxLayer(cls, ms) {
    var st = fxStage();
    if (!st) return null;
    var el = document.createElement('div');
    el.className = 'rv-fxl ' + cls;
    st.appendChild(el);
    if (ms) setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, ms);
    return el;
  }

  function fxDrop(el) { if (el && el.parentNode) el.parentNode.removeChild(el); }

  // 淡入: 整屏底色 (含顶栏/页边) 盖住再揭开 —— 新页面从整屏里显影
  function fxFade(ms) {
    var veil = fxLayer('rv-fxl-veil', ms + 140);
    if (!veil || typeof veil.animate !== 'function') { fxDrop(veil); return; }
    var a = veil.animate([{ opacity: 1 }, { opacity: 0 }],
                         { duration: ms, easing: 'cubic-bezier(.4,0,.2,1)' });
    a.onfinish = function () { fxDrop(veil); };
    a.oncancel = function () { fxDrop(veil); };
  }

  // 水波纹: 圆心贴在转页那一侧的屏幕边缘, 一路漫过整屏 (pointer-events: none)
  function fxRipple(dir, ms) {
    var wrap = fxLayer('rv-fxl-wave', ms + 140);
    if (!wrap) return;
    var i = document.createElement('i');
    if (dir > 0) i.style.left = '100%'; else i.style.left = '0';
    wrap.appendChild(i);
    var inward = dir > 0 ? '-32%' : '32%';
    var a = null;
    try {
      a = i.animate([
        { transform: 'translate(-50%,-50%) translateX(0) scale(.16)', opacity: .5, offset: 0 },
        { transform: 'translate(-50%,-50%) translateX(' + inward + ') scale(.7)', opacity: .34, offset: .5 },
        { transform: 'translate(-50%,-50%) translateX(' + inward + ') scale(1.08)', opacity: 0, offset: 1 }
      ], { duration: ms, easing: 'ease-out' });
    } catch (e) { /* 老浏览器没有 WAAPI: 靠 setTimeout 收层 */ }
    if (a) { a.onfinish = function () { fxDrop(wrap); }; a.oncancel = function () { fxDrop(wrap); }; }
  }

  // 翻卷: 整页绕转页那一侧的边缘掀起 (±12deg, perspective 写在 transform 里,
  // 不给 body 挂 perspective —— 那会让 fixed 层改以 body 为容器), 再掠过一道纸页高光
  //
  // 单飞 (2026-11-07): 连翻时上一次翻卷往往还在跑。若让它和新的一次并排存在,
  // **先结束的那个会在自己的 onfinish 里把 transformOrigin 清成默认值** ——
  // 还在转的那几个就改成绕画布中心转, 整页左右各缩一截, 读起来就是
  // 「阅读区域错位, 露出前后页边沿」。所以这里只允许一个翻卷动画 + 一层高光。
  var flipAnim = null, flipSweep = null, flipMeta = null;

  function flipStop() {
    var a = flipAnim;
    flipAnim = null; flipMeta = null;
    if (a) { try { a.cancel(); } catch (e) { /* 已结束 */ } }
    if (flipSweep) { fxDrop(flipSweep); flipSweep = null; }
    if (root) root.style.transformOrigin = '';
  }

  // 上一次翻卷已经转过去多少 (0..1) —— 连翻时从这个角度接着转, 不弹回 ±12°
  function flipDone() {
    if (!flipAnim || !flipMeta) return 1;
    var t = flipAnim.currentTime;
    if (!(typeof t === 'number')) return 1;
    var p = t / flipMeta.ms;
    return p < 0 ? 0 : (p > 1 ? 1 : p);
  }

  function fxFlip(dir, ms) {
    if (!root || typeof root.animate !== 'function') return;
    var deg = dir > 0 ? -12 : 12;
    var from = deg, dur = ms;
    if (flipAnim && flipMeta) {
      if (flipMeta.dir === dir) {
        var p = flipDone();
        // 已经转过九成: 当作新的一页, 从头掀; 否则接着当前角度往回收
        from = (p < 0.9) ? +(deg * (1 - p)).toFixed(2) : deg;
        if (from !== deg) dur = Math.max(150, Math.round(ms * (1 - p) + 120));
      }
    }
    flipStop();
    root.style.transformOrigin = dir > 0 ? 'left center' : 'right center';
    var a = root.animate([
      { transform: 'perspective(2200px) rotateY(' + from + 'deg)' },
      { transform: 'perspective(2200px) rotateY(0deg)' }
    ], { duration: dur, easing: 'cubic-bezier(.22,.61,.36,1)' });
    flipAnim = a; flipMeta = { dir: dir, ms: dur };
    var done = function () {
      if (flipAnim !== a) return;             // 已被新的一次接管: 不许动 origin
      flipAnim = null; flipMeta = null;
      root.style.transformOrigin = '';
      try { a.cancel(); } catch (e) { /* 已结束 */ }
    };
    a.onfinish = done;
    a.oncancel = function () {
      if (flipAnim !== a) return;
      flipAnim = null; flipMeta = null;
      root.style.transformOrigin = '';
    };
    var sw = fxLayer('rv-fxl-sweep', ms + 140);
    if (!sw || typeof sw.animate !== 'function') { fxDrop(sw); return; }
    flipSweep = sw;
    var b = sw.animate([
      { opacity: 0, transform: 'translateX(' + (dir > 0 ? '-40%' : '40%') + ')' },
      { opacity: .9, transform: 'translateX(0)', offset: .38 },
      { opacity: 0, transform: 'translateX(' + (dir > 0 ? '40%' : '-40%') + ')' }
    ], { duration: ms, easing: 'ease-out' });
    var sweepDone = function () {
      if (flipSweep === sw) flipSweep = null;
      fxDrop(sw);
    };
    b.onfinish = sweepDone;
    b.oncancel = sweepDone;
  }

  function playFx(fx, dir) {
    if (!isPaged() || typeof root.animate !== 'function') return;
    if (fx === 'slide' || fx === 'none') return;
    if (fx === 'fade') fxFade(230);
    else if (fx === 'ripple') fxRipple(dir, 520);
    else if (fx === 'flip') fxFlip(dir, 320);
  }

  // ---------- 阅读模式 (滚动 / 左右翻页) ----------
  function paintMode() {
    $$('#rv-mode button').forEach(function (b) {
      b.setAttribute('aria-pressed', b.dataset.mode === S.mode ? 'true' : 'false');
    });
  }

  function setMode(m) {
    m = (m === 'page') ? 'page' : 'scroll';
    if (m === S.mode) { paintMode(); return; }
    var anchor = currentAnchor();
    if (anchor) anchor = { ch: anchor.ch, off: anchor.off };
    S.mode = m;
    try { localStorage.setItem('recto.mode', m); } catch (e) { /* 隐私模式下忽略 */ }
    root.classList.toggle('is-paged', isPaged());
    paintMode();
    if (isPaged()) {
      layoutPaged(anchor || { ch: S.cur, off: 0 });
      prefetch();
      toast('左右翻页');
    } else {
      flipStop();                            // 退出左右翻页: 别把半转的页面留在屏上
      root.classList.add('rv-no-anim');
      contentEl.style.transform = '';
      contentEl.style.columnWidth = '';
      root.style.removeProperty('--rv-page-h');
      root.style.removeProperty('--rv-bar-h');
      S.page = 0;
      root.classList.remove('rv-no-anim');
      paintPageInd();
      if (anchor) scrollToOffset(anchor.ch, anchor.off);
      else window.scrollTo({ top: 0 });
      syncProg();
      toast('连续滚动');
    }
    placeTypo();
  }

  // 字号/宽度/页边距改了 = 分页要重排; 用改之前的游标当锚点, 不让人"跳页"
  var relayoutT = 0;
  function queueRelayout(anchor) {
    if (!isPaged()) return;
    clearTimeout(relayoutT);
    relayoutT = setTimeout(function () {
      if (!isPaged()) return;
      var a = anchor || currentAnchor();
      layoutPaged(a ? { ch: a.ch, off: a.off } : null);
    }, 160);
  }

  if (typoEl) {
    typoEl.addEventListener('input', function (ev) {
      var el = ev.target;
      var k = el.dataset && el.dataset.look;
      if (!k) return;
      var v = parseFloat(el.value);
      if (!isFinite(v)) return;
      var keep = currentAnchor();        // 先记游标, 再重排 (改了行高/宽度位置就变了)
      S.look[k] = v;
      localStorage.setItem('recto.' + k, String(v));
      applyLook();
      paintLook();
      queueRelayout(keep);
    });
    // 松手才算一次"布局变了", 记一次进度 (行高/宽度改了, 视口里的位置也变了)
    typoEl.addEventListener('change', function (ev) {
      var t = ev.target;
      if (t && t.id === 'rv-fx') { setFx(t.value); return; }
      if (t.dataset && t.dataset.look) queueSave(true);
    });
    typoEl.addEventListener('click', function (ev) {
      var b = ev.target.closest && ev.target.closest('[data-typo]');
      if (b && b.dataset.typo === 'reset') { resetLook(); return; }
      var m = ev.target.closest && ev.target.closest('[data-mode]');
      if (m) setMode(m.dataset.mode);
      var ind = ev.target.closest && ev.target.closest('[data-indent]');
      if (ind) setIndent(ind.dataset.indent);
      var kw = ev.target.closest && ev.target.closest('[data-kw]');
      if (kw) setKw(kw.dataset.kw === '1');
    });
  }
  if (fontInp) {
    fontInp.addEventListener('change', function () { applyFontValue(fontInp.value); });
    fontInp.addEventListener('focus', function () {
      if (!fontMenuOpen()) openFontMenu('');
    });
    fontInp.addEventListener('input', function () { openFontMenu(fontInp.value); });
    fontInp.addEventListener('blur', function () { closeFontMenu(); });
    fontInp.addEventListener('keydown', function (ev) {
      if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
        ev.preventDefault();
        if (!fontMenuOpen()) { openFontMenu(''); return; }
        var list = fontFiltered();
        if (!list.length) return;
        var n = list.length;
        fontCursor = ev.key === 'ArrowDown'
          ? (fontCursor + 1) % n
          : (fontCursor <= 0 ? n - 1 : fontCursor - 1);
        renderFontMenu();
        var on = fontMenu && fontMenu.querySelector('.rv-combo-opt.is-on');
        if (on && on.scrollIntoView) on.scrollIntoView({ block: 'nearest' });
        return;
      }
      if (ev.key === 'Escape') {
        if (fontMenuOpen()) { ev.preventDefault(); closeFontMenu(); }
        return;
      }
      if (ev.key !== 'Enter') return;
      ev.preventDefault();
      var picked = fontMenuOpen() && fontCursor >= 0 && pickFont(fontCursor);
      if (!picked) applyFontValue(fontInp.value);
      closeFontMenu();
      fontInp.blur();
    });
  }
  if (fontOpenBtn) {
    fontOpenBtn.addEventListener('mousedown', function (ev) { ev.preventDefault(); });
    fontOpenBtn.addEventListener('click', function () {
      if (fontMenuOpen()) { closeFontMenu(); return; }
      if (fontInp) fontInp.focus();
      openFontMenu('');
    });
  }
  if (fontMenu) {
    // 用 mousedown + preventDefault: 否则点选项会先让输入框失焦,
    // 触发 change 时拿半截文字把刚选中的字体覆盖掉
    fontMenu.addEventListener('mousedown', function (ev) {
      var b = ev.target.closest && ev.target.closest('[data-idx]');
      if (!b) return;
      ev.preventDefault();
      pickFont(+b.dataset.idx);
      closeFontMenu();
      if (fontInp) fontInp.blur();
    });
  }
  if (fontScanBtn) fontScanBtn.addEventListener('click', scanLocalFonts);

  // ---------- 左右页边翻页 ----------
  function turnPage(dir) {
    if (isPaged()) { pageStep(dir); return; }
    var step = Math.max(240, Math.round(window.innerHeight * 0.9));
    var maxY = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
    var y = Math.max(0, Math.min(maxY, window.scrollY + dir * step));
    window.scrollTo({ top: y, behavior: 'smooth' });
    if (y <= 0) setBar(false);
    if (dir > 0) {
      // 到底了才去补章; 补上了就接着往下滚一屏, 否则"点一下不动"像卡死
      var atEnd = y >= maxY - 24;
      ensureNext(function (grown) {
        if (!grown || !atEnd) return;
        requestAnimationFrame(function () {
          var max2 = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
          window.scrollTo({ top: Math.min(max2, window.scrollY + step), behavior: 'smooth' });
        });
      });
    }
  }

  // 翻页模式: 一页一页走; 走到头自动补下一章, 退到头回上一章末页
  function pageStep(dir) {
    var p = S.page + dir;
    if (p < 0) {
      if (S.cur > 0) { S.jumpIntent = true; openChapter(S.cur - 1, { tail: true }); return; }
      p = 0;
    }
    if (p > S.pageCount - 1) {
      measurePages();                       // 别的路径可能刚补了章却没重新量
      if (p <= S.pageCount - 1) { goPage(p); return; }
      var nxt = lastDomChapter() + 1;
      if (nxt >= S.chapters.length) { goPage(S.pageCount - 1); toast('已是最后一章'); return; }
      // 章末这一下必须"记得住": 加载中再点也只做一次, 加载完自己翻过去
      if (turnWait === dir) return;
      turnWait = dir;
      appendNextChapter(function (grown) {
        turnWait = 0;
        measurePages();
        rebuildFlow();
        paintPageInd();
        syncProg();
        if (grown) goPage(Math.min(S.page + 1, S.pageCount - 1), { noGrow: false });
        else {
          goPage(S.pageCount - 1);
          footText('— 下一章没载入, 点这里重试 —', true);
          toast('下一章没载入, 再翻一下重试');
        }
      });
      return;
    }
    goPage(p);
  }

  var turnWait = 0;          // 章末正在补下一章时的翻页方向 (防连点重复触发)

  $$('.rv-turn').forEach(function (btn) {
    var down = null;
    btn.addEventListener('pointerdown', function (e) { down = { x: e.clientX, y: e.clientY }; });
    btn.addEventListener('pointercancel', function () { down = null; });
    btn.addEventListener('pointerup', function (e) {
      if (!down) return;
      var moved = Math.abs(e.clientX - down.x) + Math.abs(e.clientY - down.y);
      down = null;
      if (moved > 10) return;                       // 拖动 = 想滚动, 不翻页
      var sel = window.getSelection();
      if (sel && String(sel).length) return;         // 正在选字, 不动
      if (dismissPopups()) return;                   // 有浮层时先关浮层
      turnPage(+btn.dataset.turn || 1);
    });
  });

  // 页脚那句话: 载入失败时点一下重试, 正常时点一下手动补下一章
  var footEl = $('#rv-foot');
  if (footEl) {
    footEl.addEventListener('click', function () {
      if (isPaged()) return;
      var retry = footEl.dataset.retry === '1';
      footEl.dataset.retry = '';
      footEl.classList.remove('is-link');
      if (retry && S.blocks[S.cur] === undefined) { openChapter(S.cur); return; }
      ensureNext();
    });
  }

  // 点正文中间: 先关浮层；没有浮层就收起/唤出顶栏
  var mainEl = $('#rv-main');
  var mainDownSelBar = false;
  var pressFrom = null;
  mainEl.addEventListener('pointerdown', function (e) {
    mainDownSelBar = !selBar.hidden;     // 按下时工具条还在 → 这次只当"关掉", 不切顶栏
    pressFrom = { x: e.clientX, y: e.clientY, t: Date.now(), touch: e.pointerType !== 'mouse' };
  }, true);
  mainEl.addEventListener('pointercancel', function () { pressFrom = null; });
  mainEl.addEventListener('pointerup', function (e) {
    var from = pressFrom;
    pressFrom = null;
    if (!from) return;
    var dx = e.clientX - from.x, dy = e.clientY - from.y;
    if (Math.abs(dx) + Math.abs(dy) > 12) S.draggedAt = Date.now();   // 拖过 = 这次别切顶栏
    if (!isPaged() || !from.touch) return;
    if (Date.now() - from.t > 800) return;
    if (Math.abs(dx) < 44 || Math.abs(dx) < Math.abs(dy) * 1.6) return;
    var sel = window.getSelection();
    if (sel && String(sel).length) return;
    if (dismissPopups()) return;
    S.swipedAt = Date.now();
    turnPage(dx < 0 ? 1 : -1);
  });
  mainEl.addEventListener('click', function (ev) {
    var wasSelBar = mainDownSelBar;
    mainDownSelBar = false;
    // 刚滑过一页 / 刚拖过 → 别顺手把顶栏也切了
    if (Date.now() - Math.max(S.swipedAt || 0, S.draggedAt || 0) < 320) return;
    if (downInTypo) return;                        // 这次点击是从排版面板里按下去的
    if (ev.target.closest && ev.target.closest('a')) return;
    var sel = window.getSelection();
    if (sel && String(sel).length) return;
    if (dismissPopups()) return;
    if (wasSelBar) return;
    setBar(!barHidden);
  });

  // ---------- 顶部按钮 ----------
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest && ev.target.closest('[data-act]');
    if (!b || !root.contains(b)) return;
    var act = b.dataset.act;
    if (act === 'toc') openDrawer('toc');
    else if (act === 'marks') openDrawer('marks');
    else if (act === 'notes') openDrawer('notes');
    else if (act === 'mark') addBookmark();
    else if (act === 'typo') toggleTypo();
    else if (act === 'theme') {
      var order = ['auto', 'sepia', 'light', 'dark', 'green'];
      S.theme = order[(order.indexOf(S.theme) + 1) % order.length];
      localStorage.setItem('recto.theme', S.theme);
      applyLook();
    }
  });

  // 一次点击算不算"点正文" / "点面板外", 看**按下点**而不是 click 的 target。
  // 因为字体清单在 mousedown 里就收起了: 等 mouseup 时指针底下已经换人,
  // 浏览器把 click 派发给两者的共同祖先 (= 正文), 于是"选个字体"顺带把面板关了。
  var downInTypo = false;
  document.addEventListener('pointerdown', function (ev) {
    downInTypo = !!(typoEl && !typoEl.hidden && typoEl.contains(ev.target));
  }, true);

  // 点面板外面收起 (面板自己 / 排版按钮 / 从面板里按下去的这次点击除外)
  document.addEventListener('click', function (ev) {
    if (!typoEl || typoEl.hidden) return;
    if (downInTypo) return;
    if (typoEl.contains(ev.target)) return;
    if (ev.target.closest && ev.target.closest('[data-act="typo"]')) return;
    hideTypo();
  });

  document.addEventListener('click', function (ev) {
    var c = ev.target.closest && ev.target.closest('[data-close]');
    if (c) closeDrawer(c.dataset.close);
    var ta = ev.target.closest && ev.target.closest('[data-toc-act]');
    if (ta) tocExpand(ta.dataset.tocAct === 'expand');
  });

  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape' && dismissPopups()) { ev.preventDefault(); return; }
    if (!isPaged() || ev.defaultPrevented) return;
    var t = ev.target;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' ||
              t.tagName === 'SELECT' || t.isContentEditable)) return;
    var sel = window.getSelection();
    if (sel && String(sel).length) return;
    var next = ev.key === 'ArrowRight' || ev.key === 'PageDown' || (ev.key === ' ' && !ev.shiftKey);
    var prev = ev.key === 'ArrowLeft' || ev.key === 'PageUp' || (ev.key === ' ' && ev.shiftKey);
    if (!next && !prev) return;
    ev.preventDefault();
    turnPage(next ? 1 : -1);
  });

  function addBookmark() {
    var a = currentAnchor();
    if (!a) return;
    var el = a.el;
    var text = (el.textContent || '').trim().slice(0, 80);
    api('/bookmark', { method: 'POST', body: { chapter: a.ch, offset: a.off, summary: text } })
      .then(function (r) {
        if (r && r.bookmark) { S.marks.push(r.bookmark); renderMarkList(); toast('已加书签'); }
      }).catch(function (e) { toast('书签保存失败: ' + e.message); });
  }

  // 真正在滚 (滚轮 / 手指滑) 就把面板收起来; 拖滑块引起的布局位移不算滚动。
  // 例外: 指针落在面板内部 —— 那是在面板自己的列表里滚 (字体候选几十条), 收起面板
  // 等于「想选字体, 弹窗先没了」。面板内的滚动由面板自己消化, 不当作"开始阅读"。
  function scrollInsidePanel(ev) {
    if (!typoEl || typoEl.hidden) return false;
    var t = ev.target;
    if (!t) return false;
    if (typoEl === t) return true;
    return !!(typoEl.contains && typoEl.contains(t));
  }
  window.addEventListener('wheel', function (ev) {
    if (!scrollInsidePanel(ev)) hideTypo();
  }, { passive: true });
  window.addEventListener('touchmove', function (ev) {
    if (!scrollInsidePanel(ev)) hideTypo();
  }, { passive: true });

  // ---------- 用户输入标记 ----------
  // 只认真正的手势 —— 程序化的 scrollToOffset / layoutPaged 也会发 scroll 事件,
  // 不能拿 scroll 当"读者动过"的证据。
  ['wheel', 'touchstart', 'touchmove', 'pointerdown', 'keydown'].forEach(function (ev) {
    window.addEventListener(ev, function () { S.userMoved = true; }, { passive: true });
  });

  // ---------- 滚动 ----------
  // 滚动方向决定顶栏显隐: 下滑收起, 上滑 / 回到顶部唤出
  window.addEventListener('scroll', function () {
    var y = window.scrollY;
    syncProg();
    // 排版面板开着时布局在变 (滚动位置会跳), 不拿它当"下滑阅读"
    if (typoEl && !typoEl.hidden) { lastY = y; return; }
    if (y <= 24) setBar(false);
    else if (y > lastY + 6) setBar(true);
    else if (y < lastY - 6) setBar(false);
    lastY = y;
  }, { passive: true });
  window.addEventListener('resize', function () {
    if (isPaged()) {
      var a = currentAnchor();
      layoutPaged(a ? { ch: a.ch, off: a.off } : null, { noSave: true });
    }
    syncProg();
    placeTypo();
  }, { passive: true });

  window.addEventListener('scroll', function () {
    if (isPaged()) return;
    clearTimeout(S.scrollT);
    S.scrollT = setTimeout(function () {
      var el = topBlock();
      if (el) {
        var ch = +el.dataset.ch;
        if (ch !== S.cur) { S.cur = ch; markToc(); if (!S.loaded[ch + 1]) ensureNext(); }
        $('#rv-pct').textContent = pctText(pctOf(ch, +el.dataset.off), S.bytes);
      }
      // 读到底: 最后一章末尾进入视口 → 位置记到章末, 并把「读完」明确告诉服务端。
      // 注意: 只在读者**真的动过**之后才算 —— 打开一本书时如果本来就停在末尾,
      // 这一屏也会"看起来到底", 那不等于刚读完 (会误标 readComplete=1)。
      if (S.userMoved && S.cur === S.chapters.length - 1 &&
          window.scrollY + window.innerHeight >= document.body.scrollHeight - 40) {
        var last = S.chapters[S.cur];
        api('/progress', { method: 'POST',
                           body: { chapter: S.cur, offset: Math.max(0, last.size - 1),
                                   sid: S.sid, end: S.cur >= S.chapters.length - 1 } })
          .then(function (r) {
            if (!r) return;
            if (r.percent != null && !r.stale) $('#rv-pct').textContent = pctText(r.percent, S.bytes);
            if (r.sync) renderStamp(r.sync);
          })
          .catch(function () {});
        return;
      }
      queueSave();
    }, 120);
  }, { passive: true });

  // 章节末尾自动续读
  var footObs = null;
  function watchFoot() {
    if (!('IntersectionObserver' in window)) return;
    footObs = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) { if (en.isIntersecting) ensureNext(); });
    }, { rootMargin: '600px 0px' });
    footObs.observe($('#rv-foot'));
  }

  // ---------- 阅读时长 ----------
  function timeStart() {
    api('/session', { method: 'POST', body: { action: 'start' } }).catch(function () {});
    clearInterval(S.timeT);
    S.timeT = setInterval(function () {
      if (document.visibilityState === 'hidden') return;
      api('/session', { method: 'POST', body: { action: 'beat' } }).catch(function () {});
    }, 30000);
  }
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') {
      flushProgress();
      beacon('/session', { action: 'end' });
    } else {
      api('/session', { method: 'POST', body: { action: 'start' } }).catch(function () {});
      api('/sync-state').then(renderStamp).catch(function () {});
    }
  });
  window.addEventListener('pagehide', function () {
    flushProgress();
    beacon('/session', { action: 'end' });
  });

  // 排查口 (只读): 页号表 / 当前游标 —— 探针与手查都用它
  window.__RV__ = {
    page: function () {
      return { mode: S.mode, page: S.page, count: S.pageCount, stride: S.pageStride,
               w: S.pageW, gap: S.pageGap };
    },
    flow: function () {
      if (!flow) rebuildFlow();
      if (!flow) return [];
      return flow.els.map(function (el, i) {
        return { ch: +el.dataset.ch, off: +el.dataset.off, page: flow.pages[i] };
      });
    },
    cursor: function () {
      var a = currentAnchor();
      return a ? { ch: a.ch, off: a.off } : null;
    },
    hint: function () { return pageHintRead(); },
    kw: function () {
      return { on: kmOn(), mode: KM.mode, words: KM.words, ranges: KM.ranges,
               spans: KM.spans, err: KM.err,
               file: (KM.data || {}).file || '',
               cats: ((KM.data || {}).cats || []).length };
    }
  };

  // ---------- 启动 ----------
  function boot(meta) {
    S.meta = meta;
    S.chapters = meta.chapters || [];
    S.bytes = meta.bytes || 0;
    S.notes = meta.notes || [];
    S.marks = meta.bookmarks || [];
    S.lastCh = null; S.lastOff = null;
    lastGood = null;
    applyLook();
    root.classList.toggle('is-paged', isPaged());
    paintMode();
    paintFx();
    renderToc();
    renderLists();
    kmLoad();
    var p = meta.progress || {};
    var startCh = Math.max(0, Math.min(+p.chapter || 0, S.chapters.length - 1));
    S.cur = startCh;
    $('#rv-title').textContent = meta.title;
    $('#rv-sub').textContent = (meta.author || '') + ' · ' + S.chapters.length + ' 章 · ' + fmtBytes(meta.bytes);
    loadChapter(startCh, function (blocks) {
      if (!blocks) { footText('— 这一章没载入, 点这里重试 —', true); return; }
      renderChapter(Object.assign({ i: startCh }, chapterMeta(startCh),
                                  { blocks: blocks, cjk: S.cjk[startCh] }), false);
      if (isPaged()) layoutPaged({ ch: startCh, off: +(p.offset || 0) });
      else if (p.offset) scrollToOffset(startCh, +p.offset);
      $('#rv-pct').textContent = pctText(pctOf(startCh, +p.offset || 0), S.bytes);
      markToc();
      if (isPaged()) prefetch(); else { ensureNext(); growIfShort(); }
      watchFoot();
      timeStart();
      queueSave(true);
      syncProg();
      // 从上次位置恢复后的那一次跳转不算"下滑阅读" —— 落地时顶栏应当可见
      lastY = window.scrollY;
      setBar(false);
      syncBoot(meta.sync);
    });
  }

  function fmtBytes(n) {
    n = +n || 0;
    if (n > 1024 * 1024) return (n / 1048576).toFixed(1) + ' MB';
    if (n > 1024) return (n / 1024).toFixed(0) + ' KB';
    return n + ' B';
  }

  api('').then(function (m) {
    if (m && m.chapters && m.chapters.length) {
      boot(m);
    } else if (m) {
      $('#rv-content').innerHTML = '<div class="rv-empty">这本书没有可读内容</div>';
    }
  }).catch(function (e) {
    $('#rv-content').innerHTML = '<div class="rv-empty">加载失败: ' + escapeHtml(e.message) + '</div>';
  });
})();
