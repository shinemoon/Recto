/* 翻页模式细节: 顶栏收起时整页上移 / 点中间切顶栏 / 滑动不误触顶栏 / 页码贴底 */
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
const { execFileSync } = require('child_process');

const BASE = 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || 'a6f63af571f05bb4';
const token = execFileSync('python3', [new URL('./make_cookie.py', import.meta.url).pathname],
  { encoding: 'utf8' }).trim();

const out = [];
const chk = (n, c, x = '') => out.push(`${c ? 'ok  ' : 'FAIL'} ${n}${x ? ' — ' + x : ''}`);

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, hasTouch: true });
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
const errs = [];
page.on('pageerror', e => errs.push(String(e)));
await page.addInitScript(() => localStorage.setItem('recto.mode', 'page'));
await page.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('.rv-page p');
await page.waitForTimeout(1500);

const box = () => page.evaluate(() => {
  const v = document.getElementById('rv-view').getBoundingClientRect();
  const bar = document.getElementById('rv-bar').getBoundingClientRect();
  const ind = document.getElementById('rv-pageind').getBoundingClientRect();
  return { top: Math.round(v.top), bottom: Math.round(v.bottom), h: Math.round(v.height),
           barBottom: Math.round(bar.bottom), hidden: document.getElementById('rv-bar').classList.contains('is-hidden'),
           indBottom: Math.round(ind.bottom), winH: window.innerHeight,
           ind: document.getElementById('rv-pageind').textContent };
});

const b0 = await box();
// 顶栏是浮层 (2026-11-05): .rv-view 占满视口 (top=0), 正文靠上下等距内边距让开;
// 顶栏显示时会盖住顶部一两行, 这是设计 (点正文中间可收起)。
chk('阅读区占满视口 (顶栏不吃版面)', Math.abs(b0.top) <= 1 && Math.abs(b0.h - b0.winH) <= 2,
  JSON.stringify(b0));

await page.mouse.click(640, 500);            // 点正文中间 → 收起顶栏
await page.waitForTimeout(500);
const b1 = await box();
chk('点正文中间收起顶栏', b1.hidden);
chk('收起后阅读区不位移 (不跟着顶栏上移)', Math.abs(b1.top - b0.top) <= 1, JSON.stringify(b1));
chk('收起后整页仍在视口内', b1.top >= 0 && b1.bottom <= b1.winH + 1, JSON.stringify(b1));
chk('页高不变 (分页不重排)', b1.h === b0.h, `${b0.h} → ${b1.h}`);
chk('页码仍贴底可见', b1.indBottom <= b1.winH + 1 && b1.indBottom > b1.winH - 40,
  `${b1.indBottom} / ${b1.winH}`);

await page.mouse.click(640, 500);
await page.waitForTimeout(500);
const b2 = await box();
chk('再点一次顶栏回来', !b2.hidden && Math.abs(b2.top - b0.top) <= 1, JSON.stringify(b2));

/* 滑动翻页: 不应该顺手切顶栏 */
const p0 = await page.evaluate(() => document.getElementById('rv-pageind').textContent);
await page.mouse.move(900, 500);
await page.mouse.down();
await page.mouse.move(700, 505, { steps: 6 });
await page.mouse.up();
await page.waitForTimeout(500);
const p1 = await page.evaluate(() => ({
  ind: document.getElementById('rv-pageind').textContent,
  hidden: document.getElementById('rv-bar').classList.contains('is-hidden')
}));
chk('鼠标拖动不翻页也不切顶栏', p1.ind === p0 && !p1.hidden, `${p0} → ${p1.ind} hidden=${p1.hidden}`);

/* 触摸左右滑动 = 翻页 (合成 pointer 事件, 只走我们自己的手势判断) */
const swipe = (from, to) => page.evaluate(([x0, x1, y]) => {
  const el = document.getElementById('rv-main');
  const mk = (type, x) => new PointerEvent(type, {
    bubbles: true, cancelable: true, pointerType: 'touch', pointerId: 1,
    isPrimary: true, clientX: x, clientY: y
  });
  el.dispatchEvent(mk('pointerdown', x0));
  el.dispatchEvent(mk('pointerup', x1));
}, [from, to, 480]);

const sw0 = await page.evaluate(() => document.getElementById('rv-pageind').textContent);
await swipe(900, 640);                        // 左滑 → 下一页
await page.waitForTimeout(450);
const sw1 = await page.evaluate(() => ({
  ind: document.getElementById('rv-pageind').textContent,
  hidden: document.getElementById('rv-bar').classList.contains('is-hidden')
}));
chk('触摸左滑翻到下一页', sw1.ind !== sw0 && !sw1.hidden, `${sw0} → ${sw1.ind}`);
await swipe(300, 620);                        // 右滑 → 上一页
await page.waitForTimeout(450);
const sw2 = await page.evaluate(() => document.getElementById('rv-pageind').textContent);
chk('触摸右滑回上一页', sw2 === sw0, `${sw1.ind} → ${sw2}`);

/* 窗口只裁不滚: 页外内容靠 overflow: hidden 切掉, 不能出现横向滚动 */
const clip = await page.evaluate(() => {
  const v = document.getElementById('rv-view');
  const cs = getComputedStyle(v);
  // 本页起始的块 (左缘对齐窗口) 都在窗口内
  const vr = v.getBoundingClientRect();
  const starts = [...document.querySelectorAll('.rv-blk')].filter(b => {
    const r = b.getBoundingClientRect();
    return r.left >= vr.left - 1 && r.left < vr.right - 1;
  });
  const out = starts.filter(b => {
    const r = b.getBoundingClientRect();
    return r.left < vr.left - 1 || r.right > vr.right + 1;
  }).length;
  return {
    overflow: cs.overflow, scrollLeft: v.scrollLeft,
    scrollW: v.scrollWidth, clientW: v.clientWidth,
    starts: starts.length, out,
    overflowX: document.documentElement.scrollWidth - document.documentElement.clientWidth
  };
});
chk('裁切窗口只裁不滚 (overflow: clip)', clip.overflow === 'clip' && clip.scrollLeft === 0,
  JSON.stringify(clip));
chk('本页起始的块都在窗口内', clip.starts > 0 && clip.out === 0, JSON.stringify(clip));
chk('页面无横向溢出', clip.overflowX <= 0, String(clip.overflowX));
chk('无 JS 报错', errs.length === 0, errs.slice(0, 2).join(' | '));

await page.screenshot({ path: '/tmp/rv-paged-immersive.png' });
await browser.close();
console.log(out.join('\n'));
const fail = out.filter(x => x.startsWith('FAIL')).length;
console.log(`\n${out.length - fail}/${out.length} passed`);
process.exit(fail ? 1 : 0);
