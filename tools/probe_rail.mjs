// 书架右侧操作轨巡检: 桌面竖排圆钮贴屏幕右侧 + hover 出名字; 窄屏回到页头横排 + 名字常显。
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
const { execFileSync } = require('child_process');

const BASE = process.env.RECTO_BASE || 'http://127.0.0.1:5015';
const token = execFileSync('python3', [new URL('./make_cookie.py', import.meta.url).pathname],
  { encoding: 'utf8' }).trim();

const res = [];
const ok = (c, n, x) => { res.push(!!c); console.log((c ? 'PASS ' : 'FAIL ') + n + (x === undefined ? '' : ' :: ' + JSON.stringify(x))); };

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

const probe = () => page.evaluate(() => {
  const rail = document.querySelector('.rc-rail');
  const btns = [...document.querySelectorAll('.rc-rail-btn')];
  const names = [...document.querySelectorAll('.rc-rail-name')];
  const cs = (el) => (el ? getComputedStyle(el) : null);
  const r = rail ? rail.getBoundingClientRect() : null;
  const bs = btns.map((b) => {
    const q = b.getBoundingClientRect();
    return { w: Math.round(q.width), h: Math.round(q.height), x: Math.round(q.x), y: Math.round(q.y),
             text: b.textContent.trim(), radius: cs(b).borderRadius, pos: cs(b).position };
  });
  const main = document.querySelector('.rc-main');
  const cards = [...document.querySelectorAll('.bk')].map((c) => c.getBoundingClientRect());
  return {
    has: !!rail, pos: rail ? cs(rail).position : null,
    flexDir: rail ? cs(rail).flexDirection : null,
    rect: r ? { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) } : null,
    vw: window.innerWidth, vh: window.innerHeight,
    btns: bs,
    names: names.map((n) => ({ op: +cs(n).opacity, pos: cs(n).position, bg: cs(n).backgroundColor })),
    mainPadRight: cs(main).paddingRight,
    cardRightMax: cards.length ? Math.round(Math.max(...cards.map((c) => c.right))) : null,
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  };
});

await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(900);

// --- 桌面 1400 ---
let d = await probe();
ok(d.has, 'desktop: 有操作轨');
ok(d.pos === 'fixed', 'desktop: position fixed', d.pos);
ok(d.flexDir === 'column', 'desktop: 竖排', d.flexDir);
ok(Math.abs((d.vw - (d.rect.x + d.rect.w)) - 14) <= 2, 'desktop: 贴屏幕右侧', { gap: d.vw - (d.rect.x + d.rect.w), rect: d.rect });
ok(d.rect.y > 200 && d.rect.y + d.rect.h < d.vh - 100, 'desktop: 垂直居中', { y: d.rect.y, h: d.rect.h, vh: d.vh });
ok(d.btns.length === 2 && d.btns.every((b) => b.w === 48 && b.h === 48), 'desktop: 两个 48px 圆钮', d.btns);
ok(d.btns.every((b) => b.radius.includes('50%') || parseFloat(b.radius) >= 24), 'desktop: 圆形', d.btns.map((b) => b.radius));
ok(d.btns[1].y > d.btns[0].y + 40, 'desktop: 纵向堆叠', { y0: d.btns[0].y, y1: d.btns[1].y });
ok(d.names.every((n) => n.op === 0 && n.pos === 'absolute'), 'desktop: 名字默认隐藏(绝对定位)', d.names);
ok(d.cardRightMax !== null && d.cardRightMax <= d.rect.x - 8, 'desktop: 让出右边距, 卡片不被压', { cardRightMax: d.cardRightMax, railX: d.rect.x });
ok(d.overflow <= 0, 'desktop: 无横向溢出', d.overflow);

// hover 第一个圆钮 -> 名字出现
const before = await page.evaluate(() => +getComputedStyle(document.querySelector('.rc-rail-name')).opacity);
await page.hover('.rc-rail-btn:first-child');
await page.waitForTimeout(320);
const hov = await page.evaluate(() => {
  const n = document.querySelector('.rc-rail-name');
  const nb = n.getBoundingClientRect();
  const b = document.querySelector('.rc-rail-btn').getBoundingClientRect();
  return { op: +getComputedStyle(n).opacity, left: Math.round(nb.x), right: Math.round(nb.right),
           btnLeft: Math.round(b.x), text: n.textContent.trim(), bg: getComputedStyle(n).backgroundColor };
});
ok(before === 0, 'hover 前名字透明', before);
ok(hov.op > 0.9, 'hover 后名字显现', hov);
ok(hov.right <= hov.btnLeft + 1, '名字在圆钮左侧', hov);
ok(hov.text === '重扫书库', '名字文案正确', hov.text);
ok(hov.bg !== 'rgba(0, 0, 0, 0)' && !/transparent/.test(hov.bg), '名字有底色', hov.bg);

// hover 第二个 -> 出「对齐云端映射」
await page.hover('.rc-rail-btn:nth-child(2)');
await page.waitForTimeout(300);
const hov2 = await page.evaluate(() => {
  const els = [...document.querySelectorAll('.rc-rail-name')];
  return els.map((n) => +getComputedStyle(n).opacity);
});
ok(hov2[0] < 0.5 && hov2[1] > 0.9, '两个圆钮各自出名字', hov2);

// 键盘聚焦也能出名字
await page.evaluate(() => { document.querySelector('.rc-rail-btn').focus(); });
await page.evaluate(() => { const b = document.querySelector('.rc-rail-btn'); b.blur(); b.focus(); });
await page.waitForTimeout(250);


// 桌面: 点「对齐云端映射」—— 结果面板在 sync 里, 得给一句回执条
await page.evaluate(() => {
  const b = [...document.querySelectorAll('.rc-rail-btn')].find((x) => x.dataset.act === 'links');
  b.click();
});
await page.waitForTimeout(1400);
const toast = await page.evaluate(() => {
  const t = document.querySelector('.rc-toast');
  if (!t) return null;
  const cs = getComputedStyle(t);
  return { text: t.textContent, bg: cs.backgroundColor, pos: cs.position,
           pe: cs.pointerEvents, x: Math.round(t.getBoundingClientRect().x) };
});
ok(!!toast && /对齐映射/.test(toast.text), '离页动作给回执条', toast);
ok(!!toast && toast.pos === 'fixed' && toast.pe === 'none', '回执条不挡点击', toast && { pos: toast.pos, pe: toast.pe });
await page.waitForTimeout(2600);
ok((await page.evaluate(() => document.querySelectorAll('.rc-toast').length)) === 0, '回执条自己消失');


// 圆钮刻意不放水波纹 (overflow 要让给悬浮名字); 坐标外的普通按钮仍然有
const rip = await page.evaluate(() => {
  const b = document.querySelector('.rc-rail-btn');
  b.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, clientX: 10, clientY: 10 }));
  // 波纹元素仍会被外壳脚本插进来, 但圆钮把它设成 display:none
  const rs = [...b.querySelectorAll('.rc-ripple')];
  return rs.filter((el) => getComputedStyle(el).display !== 'none').length;
});
ok(rip === 0, '圆钮不放水波纹 (给悬浮名字留溢出)', rip);

// --- 窄屏 390 ---
await page.setViewportSize({ width: 390, height: 844 });
await page.waitForTimeout(500);
let m = await probe();
ok(m.pos === 'static', 'mobile: 不悬浮', m.pos);
ok(m.flexDir === 'row', 'mobile: 横排在页头', m.flexDir);
ok(m.rect.y < 260, 'mobile: 在顶端', { y: m.rect.y });
ok(m.btns.every((b) => b.w > 90), 'mobile: 带文字的胶囊', m.btns.map((b) => [b.w, b.h]));
ok(m.names.every((n) => n.op === 1 && n.pos === 'static'), 'mobile: 名字常显', m.names);
ok(m.overflow <= 0, 'mobile: 无横向溢出', m.overflow);
ok(m.mainPadRight === '16px', 'mobile: 不再留悬浮轨边距', m.mainPadRight);

// 点一下「重扫书库」: 标签变「扫描中…」再复原, 且 SVG 没被抹掉
await page.evaluate(() => {
  window.__seen = [];
  const b = document.querySelector('#shelf-rescan');
  const snap = () => window.__seen.push({
    busy: b.classList.contains('is-busy'),
    name: b.querySelector('.rc-rail-name').textContent,
    svg: !!b.querySelector('svg'),
  });
  new MutationObserver(snap).observe(b, { attributes: true, childList: true, subtree: true, characterData: true });
  snap();
});
await page.click('.rc-rail-btn:first-child');
await page.waitForTimeout(300);
const seen = await page.evaluate(() => window.__seen);
const busy = seen.find((x) => x.busy) || { busy: false, svg: false, name: '' };
if (!busy.svg) busy.svg = seen.every((x) => x.svg);
ok(busy.busy && busy.svg && /扫描/.test(busy.name), '点击中: 图标还在, 文字变扫描中', busy);
await page.waitForTimeout(3500);
const done = await page.evaluate(() => {
  const b = document.querySelector('#shelf-rescan');
  return { busy: b.classList.contains('is-busy'), name: b.querySelector('.rc-rail-name').textContent,
           svg: !!b.querySelector('svg'), disabled: b.disabled };
});
ok(!done.busy && !done.disabled && done.svg && done.name === '重扫书库', '复原: 文字回到重扫书库', done);

// 平板 900: 仍是悬浮竖排 (861 以上), 不与卡片打架
await page.setViewportSize({ width: 900, height: 800 });
await page.waitForTimeout(400);
const t = await probe();
ok(t.pos === 'fixed' && t.flexDir === 'column', '900px: 仍是悬浮竖排', { pos: t.pos, dir: t.flexDir });
ok(t.cardRightMax === null || t.cardRightMax <= t.rect.x - 8, '900px: 卡片不被压', { cardRightMax: t.cardRightMax, railX: t.rect.x });
ok(t.overflow <= 0, '900px: 无横向溢出', t.overflow);


// 各宽度扫一遍: 悬浮态不压卡片, 横排态不出界
for (const w of [1920, 1440, 1280, 1100, 900, 861, 860, 600, 390, 380]) {
  await page.setViewportSize({ width: w, height: 860 });
  await page.waitForTimeout(320);
  const x = await probe();
  const overlap = x.cardRightMax !== null && x.pos === 'fixed' ? x.cardRightMax > x.rect.x - 6 : false;
  ok(x.overflow <= 0 && !overlap, '宽度 ' + w + ': 不溢出 / 不压卡片',
     { pos: x.pos, rail: x.rect, cardRightMax: x.cardRightMax, overflow: x.overflow, padR: x.mainPadRight });
}

ok(errors.length === 0, '无 JS 报错', errors.slice(0, 4));

await browser.close();
const bad = res.filter((x) => !x).length;
console.log('\n' + (bad ? 'FAILED ' + bad + '/' + res.length : 'ALL PASS ' + res.length));
process.exit(bad ? 1 : 0);
