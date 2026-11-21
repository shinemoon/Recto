/* 故事档案页 (/pet/story/) —— 按回查阅。
 *
 * 前台 (/pet/) 读的是一条连续的路 (不分章节); 这里按回摆, 带完整的
 * 交手战报 / 路上的坎 / 这一趟的来处。
 *
 * 渲染零件与书宠主页共用 pet-shared.js (同一份, 不复制)。
 */
'use strict';

const $ = (s) => document.querySelector(s);
const RCP = window.RCPetUI;
const PET_BASE = window.RC_PET_BASE || '/pet';

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

async function api(url, opts) {
  const res = await fetch(PET_BASE + url, Object.assign({
    headers: { 'Content-Type': 'application/json' }
  }, opts || {}));
  if (res.status === 401) {
    location.href = '/login?next=' + encodeURIComponent(location.pathname);
    throw new Error('未登录');
  }
  if (res.status === 403) {
    location.href = '/first-run';
    throw new Error('请先修改初始口令');
  }
  const txt = await res.text();
  let data;
  try { data = txt ? JSON.parse(txt) : {}; } catch (e) { data = { error: txt.slice(0, 300) }; }
  if (!res.ok) {
    const msg = (data && (data.error || data.detail)) || ('HTTP ' + res.status);
    const err = new Error(msg);
    err.status = res.status;
    err.data = data || {};
    throw err;
  }
  return data;
}

/** 档案页的排序: 正序 (最早在前) / 倒序 (最新在前)。 */
let ARCH_ORDER = 'asc';

/** 一回 = 一个折叠块 (默认收起, 点开看正文与战报)。 */
function archItem(a) {
  const g = a.growth || {};
  const show = RCP ? RCP.growthChips(g) : [];
  const warns = RCP ? RCP.growthWarns(g) : [];
  const text = (a.paras || []).map(function (p) {
    return '<p>' + esc(p) + '</p>';
  }).join('');
  return '<details class="arch-item" data-no="' + esc(a.no) + '">' +
    '<summary>' +
    '<span class="arch-no">第 ' + esc(a.no) + ' 回</span>' +
    (RCP ? RCP.kindBadge(g) : '') +
    '<span class="arch-title">' + esc(a.title || '') + '</span>' +
    (a.place ? '<span class="arch-place">' + esc(a.place) + '</span>' : '') +
    '<span class="arch-at">' +
      esc(String(a.at || '').replace('T', ' ').slice(0, 16)) + '</span>' +
    '</summary>' +
    '<div class="arch-body">' +
    (show.length
      ? '<div class="adv-growth"><span class="adv-growth-l">本回所显</span>' +
        show.map(function (t) {
          return '<span class="adv-chip ok">' + esc(t) + '</span>';
        }).join('') + '</div>'
      : '') +
    warns.map(function (t) {
      return '<div class="arch-warn">' + esc(t) + '</div>';
    }).join('') +
    (RCP ? RCP.echoBlock(g.echo) : '') +
    (RCP ? RCP.duelBlock(g.duel) : '') +
    (RCP ? RCP.checksBlock(g.checks) : '') +
    '<div class="arch-text">' + text + '</div>' +
    '</div>' +
    '</details>';
}

/** 按当前顺序渲染列表 (顺序切换只重排, 不重新拉数据)。 */
let ARCH_ROWS = [];

function archRender() {
  const list = $('#archive-list');
  const pill = $('#archive-pill');
  const meta = $('#archive-meta');
  if (!list) return;
  const rows = ARCH_ORDER === 'desc' ? ARCH_ROWS.slice().reverse() : ARCH_ROWS;
  if (pill) {
    pill.className = 'pill' + (rows.length ? ' ok' : '');
    pill.textContent = rows.length + ' 回';
  }
  if (meta) {
    const paras = rows.reduce(function (n, a) { return n + (a.paras || []).length; }, 0);
    meta.innerHTML = '<div class="cs-meta-row"><span class="cs-meta-l">归档</span>' +
      '<span class="cs-meta-v">' + rows.length + ' 回 · ' + paras + ' 段 · ' +
      (ARCH_ORDER === 'desc' ? '倒序' : '正序') + '</span></div>';
  }
  list.innerHTML = rows.length
    ? rows.map(archItem).join('')
    : '<div class="arch-empty">还没有归档。它会在下一次推演时自己上路。</div>';
  // 深链: /pet/story/#12 → 直接展开那一回
  const want = (location.hash || '').replace('#', '');
  if (want) {
    const el = list.querySelector('[data-no="' + want + '"]');
    if (el) {
      el.open = true;
      el.scrollIntoView({ block: 'start' });
    }
  }
}

/** 切换顺序 (正序 / 倒序)。 */
function archSetOrder(o) {
  ARCH_ORDER = (o === 'desc') ? 'desc' : 'asc';
  ['asc', 'desc'].forEach(function (k) {
    const b = document.getElementById('arch-ord-' + k);
    if (b) b.classList.toggle('is-on', k === ARCH_ORDER);
  });
  archRender();
  try {
    const u = new URL(location.href);
    u.searchParams.set('ord', ARCH_ORDER);
    history.replaceState(null, '', u.toString());
  } catch (e) { /* 老浏览器没有 URL API —— 顺序照样能用 */ }
}

async function loadArchive() {
  const pill = $('#archive-pill');
  const list = $('#archive-list');
  try {
    const v = await api('/api/story/archive');
    ARCH_ROWS = v.archive || [];
    archRender();
  } catch (e) {
    if (pill) { pill.className = 'pill err'; pill.textContent = '读取失败'; }
    if (list) {
      list.innerHTML = '<div class="arch-empty">' + esc(e.message) + '</div>';
    }
  }
}

document.addEventListener('click', function (e) {
  const b = e.target.closest('[data-ord]');
  if (!b) return;
  archSetOrder(b.getAttribute('data-ord'));
});

/* 顺序记在 URL (?ord=desc) —— 分享/刷新都还是那个顺序 */
try {
  const u = new URL(location.href);
  if (u.searchParams.get('ord') === 'desc') ARCH_ORDER = 'desc';
} catch (e) { /* 忽略 */ }

archSetOrder(ARCH_ORDER);
loadArchive();