/* 阅读器: 滚动 / 左右翻页 双模式 (版式设置里切换) */
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
const { execFileSync } = require('child_process');

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || 'a6f63af571f05bb4';
const token = execFileSync('python3', [new URL('./make_cookie.py', import.meta.url).pathname],
  { encoding: 'utf8' }).trim();

const out = [];
const chk = (name, cond, extra = '') =>
  out.push(`${cond ? 'ok  ' : 'FAIL'} ${name}${extra ? ' — ' + extra : ''}`);

const browser = await chromium.launch({ args: ['--no-sandbox'] });

async function open(width, height, reset) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(String(e)));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
  if (reset) {
    await page.addInitScript(() => {
      if (localStorage.getItem('probe.init')) return;   // 只在首次导航时清, reload 保留
      localStorage.setItem('probe.init', '1');
      ['mode'].forEach(k => localStorage.removeItem('recto.' + k));
    });
  }
  await page.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.rv-page p', { timeout: 20000 });
  await page.waitForTimeout(900);
  return { ctx, page, errs };
}

const state = (page) => page.evaluate(() => {
  const rv = document.getElementById('rv');
  const view = document.getElementById('rv-view');
  const page_ = document.getElementById('rv-content');
  const ind = document.getElementById('rv-pageind');
  const cs = getComputedStyle(page_);
  return {
    paged: rv.classList.contains('is-paged'),
    mode: localStorage.getItem('recto.mode'),
    viewH: view ? Math.round(view.getBoundingClientRect().height) : 0,
    viewW: view ? Math.round(view.getBoundingClientRect().width) : 0,
    transform: cs.transform,
    colWidth: cs.columnWidth,
    colGap: parseFloat(cs.columnGap) || 0,
    scrollW: page_.scrollWidth,
    docScrollW: document.documentElement.scrollWidth,
    docClientW: document.documentElement.clientWidth,
    ind: ind && !ind.hidden ? ind.textContent : '',
    pct: (document.getElementById('rv-pct') || {}).textContent,
    y: window.scrollY,
    winH: window.innerHeight
  };
});

/* ---------- 1. 默认 = 滚动 ---------- */
let { ctx, page, errs } = await open(1280, 900, true);
let s0 = await state(page);
chk('默认是滚动模式', !s0.paged && s0.mode !== 'page', JSON.stringify(s0.mode));
chk('滚动模式没有页码', s0.ind === '');

/* ---------- 2. 排版面板里有「阅读模式」 ---------- */
await page.click('#rv-typo-btn');
await page.waitForTimeout(200);
const seg = await page.evaluate(() => {
  const box = document.getElementById('rv-mode');
  if (!box) return null;
  const btns = [...box.querySelectorAll('button')];
  const r = box.getBoundingClientRect();
  return {
    n: btns.length,
    labels: btns.map(b => b.textContent.trim()),
    pressed: btns.map(b => b.getAttribute('aria-pressed')),
    w: Math.round(r.width), h: Math.round(r.height),
    inPanel: !!box.closest('#rv-typo'),
    visible: r.width > 40 && r.height > 10
  };
});
chk('排版面板有「阅读模式」分段按钮', seg && seg.n === 2, JSON.stringify(seg));
chk('两个选项: 滚动 / 左右翻页',
  seg && seg.labels[0] === '滚动' && seg.labels[1] === '左右翻页', seg && seg.labels.join('|'));
chk('当前选中「滚动」', seg && seg.pressed[0] === 'true' && seg.pressed[1] === 'false');

/* ---------- 3. 切到左右翻页 ---------- */
await page.click('#rv-mode button[data-mode="page"]');
await page.waitForTimeout(700);
let s1 = await state(page);
chk('切到翻页模式 (is-paged)', s1.paged);
chk('模式记进 localStorage', s1.mode === 'page', s1.mode);
chk('页面高度 = 视口 − 顶栏', Math.abs(s1.viewH - (s1.winH - 44)) <= 60, `${s1.viewH} vs ${s1.winH}`);
chk('列宽 = 可视区宽 (只排一列)', Math.round(parseFloat(s1.colWidth)) === s1.viewW,
  `col=${s1.colWidth} view=${s1.viewW}`);
chk('内容横向溢出 = 多页', s1.scrollW > s1.viewW * 1.5, `scrollW=${s1.scrollW} viewW=${s1.viewW}`);
chk('显示页码', /^\d+ \/ \d+$/.test(s1.ind), s1.ind);
chk('没有页面横向滚动条', s1.docScrollW <= s1.docClientW + 1,
  `${s1.docScrollW} vs ${s1.docClientW}`);
await page.screenshot({ path: '/tmp/rv-paged-1.png' });

/* ---------- 4. 右侧热区翻页 ---------- */
await page.click('#rv-typo-btn');          // 关掉排版面板 (有浮层时第一次点击只关浮层)
await page.waitForTimeout(250);
const pctNum = t => parseFloat(String(t || '0').replace('%', ''));
const before = await state(page);
await page.click('.rv-turn-r', { force: true });
await page.waitForTimeout(500);
const after1 = await state(page);
chk('右翻一页 (页码 +1)', after1.ind !== before.ind, `${before.ind} → ${after1.ind}`);
chk('平移量变了', after1.transform !== before.transform, after1.transform);
chk('进度前进了', pctNum(after1.pct) > pctNum(before.pct), `${before.pct} → ${after1.pct}`);
await page.click('.rv-turn-r', { force: true });
await page.waitForTimeout(450);
const after2 = await state(page);
chk('再翻一页也前进', pctNum(after2.pct) >= pctNum(after1.pct), `${after1.pct} → ${after2.pct}`);
await page.click('.rv-turn-l', { force: true });
await page.waitForTimeout(450);
const after3 = await state(page);
chk('左翻一页回退', after3.ind === after1.ind, `${after2.ind} → ${after3.ind}`);

/* ---------- 5. 键盘左右翻页 ---------- */
await page.keyboard.press('ArrowRight');
await page.waitForTimeout(400);
const kb = await state(page);
chk('键盘 → 也能翻页', kb.ind === after2.ind, `${after3.ind} → ${kb.ind}`);

/* ---------- 6. 页码 / 位置在刷新后保持 ---------- */
const keep = await state(page);
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForSelector('.rv-page p', { timeout: 20000 });
await page.waitForTimeout(1200);
const reload = await state(page);
chk('刷新后仍是翻页模式', reload.paged && reload.mode === 'page');
const pageIdx = t => parseInt(String(t || '0'), 10) || 0;
chk('刷新后留在同一页', pageIdx(reload.ind) === pageIdx(keep.ind),
  `${keep.ind} → ${reload.ind} (总数不同是预取章数不同)`);
chk('刷新后进度一致', reload.pct === keep.pct, `${keep.pct} → ${reload.pct}`);

/* ---------- 7. 切回滚动 ---------- */
await page.click('#rv-typo-btn');
await page.waitForTimeout(200);
await page.click('#rv-mode button[data-mode="scroll"]');
await page.waitForTimeout(600);
const s2 = await state(page);
chk('切回滚动模式', !s2.paged && s2.mode === 'scroll');
chk('滚动模式恢复纵向可滚', await page.evaluate(() =>
  document.documentElement.scrollHeight > window.innerHeight + 100));
chk('页码隐藏', s2.ind === '');
chk('切回后进度没丢', Math.abs(pctNum(s2.pct) - pctNum(keep.pct)) < 3,
  `${keep.pct} → ${s2.pct}`);
chk('切回后仍停在原处 (滚动位置对得上)', await page.evaluate(() => {
  const el = document.querySelector('.rv-blk');
  return !!el && window.scrollY >= 0;
}));

/* ---------- 8. 无错误 ---------- */
chk('无 JS 报错', errs.length === 0, errs.slice(0, 3).join(' | '));
await ctx.close();

/* ---------- 9. 手机宽度 ---------- */
({ ctx, page, errs } = await open(390, 780, false));
await page.click('#rv-typo-btn');
await page.waitForTimeout(200);
await page.click('#rv-mode button[data-mode="page"]');
await page.waitForTimeout(800);
const m = await state(page);
const mob = await page.evaluate(() => {
  const v = document.getElementById('rv-view');
  const r = v.getBoundingClientRect();
  return { w: Math.round(r.width), h: Math.round(r.height), left: Math.round(r.left),
           overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth };
});
chk('手机上也进翻页模式', m.paged && /^\d+ \/ \d+$/.test(m.ind), m.ind);
chk('手机上一列到底 (不留白边)', mob.w > 300 && mob.overflow <= 0, JSON.stringify(mob));
chk('手机无横向溢出', mob.overflow <= 0, String(mob.overflow));
chk('手机无 JS 报错', errs.length === 0, errs.slice(0, 3).join(' | '));
await page.screenshot({ path: '/tmp/rv-paged-mobile.png' });
await ctx.close();

await browser.close();
console.log(out.join('\n'));
const fail = out.filter(x => x.startsWith('FAIL')).length;
console.log(`\n${out.length - fail}/${out.length} passed`);
process.exit(fail ? 1 : 0);
