/* 翻卷 (flip) 连翻: 同一次只许有一个掀页动画, origin 不许被抢, 版面几何不许被动画污染。
 *
 * 报障 (2026-11-07): 「翻卷模式, 如果翻页很快, 有时候出现阅读区域错位, 即会露出前后页边沿」。
 *
 * 两个真因 (这个探针各钉一条):
 *   1. 连翻时上一次掀页还在跑, 它的 onfinish 会把 transformOrigin 清成默认值 ——
 *      还在转的那几个就改成绕画布中心转, 整页左右各缩一截。
 *   2. 掀页期间 #rv 带 3D 变换, `.rv-view` 的 getBoundingClientRect().width 是**投影后**
 *      的宽 (桌面 704 → 781px, +11%)。这时若来一次重排 (补章 / 图片加载 / resize),
 *      列宽与 stride 就按错的值算, 版面永久错位到下一次重排为止 —— 相邻列从页边露出来。
 *
 * 跑: node tools/probe_flip_rapid.mjs     (env RV_BASE / RV_KEY / RV_W / RV_H)
 */
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || 'a6f63af571f05bb4';
const VW = +(process.env.RV_W || 390);
const VH = +(process.env.RV_H || 780);
const token = cookie();

const out = [];
const chk = (n, c, e = '') => out.push(`${c ? 'ok  ' : 'FAIL'} ${n}${e ? ' — ' + e : ''}`);

async function readProgress(pg) {
  return await pg.evaluate(async (k) => {
    const r = await fetch('/read/api/book/' + k, { headers: { accept: 'application/json' } });
    const j = await r.json();
    return (j && j.progress) || null;
  }, KEY);
}
async function writeProgress(pg, pr) {
  if (!pr) return null;
  return await pg.evaluate(async ([k, p]) => {
    const r = await fetch('/read/api/book/' + k + '/progress', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ chapter: p.chapter, offset: p.offset, jump: true, user: 1 })
    });
    return r.status;
  }, [KEY, pr]);
}

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: VW, height: VH }, hasTouch: VW < 700 });
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
const errs = [];
page.on('pageerror', e => errs.push(String(e)));
page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
await page.addInitScript(() => {
  localStorage.setItem('recto.mode', 'page');
  localStorage.setItem('recto.fx', 'flip');
});
await page.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('.rv-page p', { timeout: 20000 });
await page.waitForTimeout(1400);
const prog0 = await readProgress(page);

// 采样器: 掀页动画数量 / origin / 是否在播
await page.evaluate(() => {
  window.__S__ = [];
  const rv = document.getElementById('rv');
  window.__SAMP__ = setInterval(() => {
    const cs = getComputedStyle(rv);
    window.__S__.push({ n: rv.getAnimations().length,
                        tf: cs.transform === 'none' ? 0 : 1, org: cs.transformOrigin });
  }, 16);
});

const geo = () => page.evaluate(() => {
  const v = document.getElementById('rv-view');
  const c = document.getElementById('rv-content');
  const p = window.__RV__.page();
  const m = new DOMMatrixReadOnly(getComputedStyle(c).transform);
  const cs = getComputedStyle(v);
  const r = v.getBoundingClientRect();
  return { w: p.w, stride: p.stride, page: p.page, count: p.count,
           tfX: +m.m41.toFixed(3), expectX: +(-p.page * p.stride).toFixed(3),
           rectW: +r.width.toFixed(2), cssW: +parseFloat(cs.width).toFixed(3),
           winL: +(r.left).toFixed(2) };
});

const win = await page.evaluate(() => window.innerWidth);
const g0 = await geo();
chk('列宽取的是布局宽 (与 rect 一致: 无动画时)', Math.abs(g0.w - g0.cssW) <= 0.05,
    `w=${g0.w} css=${g0.cssW} rect=${g0.rectW}`);

// --- 1. 连翻 6 次 (每 40ms 一下), 掀页不许堆叠, origin 不许被抢 --------------
for (let i = 0; i < 6; i++) { await page.keyboard.press('PageDown'); await page.waitForTimeout(40); }
// 掀页途中量一次 "布局宽 vs rect 宽" 的差距 (证明为什么不能用 rect)
await page.keyboard.press('PageDown');
await page.waitForTimeout(60);
const during = await page.evaluate(() => {
  const v = document.getElementById('rv-view');
  const rv = document.getElementById('rv');
  return { rectW: +v.getBoundingClientRect().width.toFixed(2),
           cssW: +parseFloat(getComputedStyle(v).width).toFixed(3),
           anim: rv.getAnimations().length };
});
await page.waitForTimeout(1600);
const g1 = await geo();

const samp = await page.evaluate(() => { clearInterval(window.__SAMP__); return window.__S__; });
const maxN = Math.max(...samp.map(s => s.n));
const stolen = samp.filter(s => s.n > 0 && s.tf &&
  Math.abs(parseFloat(s.org) - win / 2) < 1).length;

chk('掀页动画同刻最多 1 个 (不堆叠)', maxN <= 1, `max=${maxN}`);
chk('掀页途中 origin 从不落到画布中心', stolen === 0, `被抢 ${stolen} 次`);
chk('掀页确实把可视区放大了 (证明 rect 不可信)', during.rectW > during.cssW + 1,
    `rect=${during.rectW} css=${during.cssW}`);
chk('连翻后列宽/stride 未被动画污染', g1.stride === g0.stride && g1.w === g0.w,
    `before ${g0.w}/${g0.stride} after ${g1.w}/${g1.stride}`);
chk('连翻后落点精确', Math.abs(g1.tfX - g1.expectX) <= 0.05, `tf=${g1.tfX} exp=${g1.expectX}`);

// --- 2. 掀页途中来一次"重排" (补章/图片加载都会走这条路), 版面必须不变 --------
const before2 = await geo();
await page.keyboard.press('PageDown');
await page.waitForTimeout(40);
// 往 .rv-page 里挂一张零尺寸的 <img> 并手动派发 load —— 就是 img 加载触发的
// scheduleSettle 那条路径 (90ms 后重排), 但不会改动版面几何
await page.evaluate(() => {
  const p = document.querySelector('.rv-page .rv-blk');
  const im = document.createElement('img');
  im.setAttribute('data-probe', '1');
  p.after(im);
  im.dispatchEvent(new Event('load'));
});
// 沿时间轴采样: 重排链 (settle 最多三轮, 每 90ms) 若撞在掀页动画里,
// 中间某几拍会读到被放大的宽度 —— 采样比「等它自愈」更能看清污染
const trace = [];
for (let i = 0; i < 14; i++) {
  await page.waitForTimeout(70);
  trace.push(await page.evaluate(() => {
    const p = window.__RV__.page();
    return { w: p.w, stride: p.stride, page: p.page };
  }));
}
await page.waitForTimeout(600);
await page.evaluate(() => {
  const im = document.querySelector('img[data-probe]');
  if (im) im.remove();
});
const polluted = trace.filter(t => Math.abs(t.w - before2.w) > 0.05 ||
                                   Math.abs(t.stride - before2.stride) > 0.05);
chk('掀页途中重排: 期间任何一拍列宽/stride 都没被放大值污染', polluted.length === 0,
    polluted.slice(0, 3).map(t => 'w=' + t.w + ' s=' + t.stride).join(' / '));
const after2 = await geo();
chk('掀页途中触发重排: 列宽/stride 不变',
    after2.stride === before2.stride && after2.w === before2.w,
    `before ${before2.w}/${before2.stride} after ${after2.w}/${after2.stride}`);
chk('掀页途中触发重排: 落点仍精确', Math.abs(after2.tfX - after2.expectX) <= 0.05,
    `tf=${after2.tfX} exp=${after2.expectX}`);
chk('掀页途中触发重排: 页号自洽 (没跳页)', Math.abs(after2.page - (before2.page + 1)) <= 1,
    `before=${before2.page} after=${after2.page}`);

// --- 3. 换到别的翻页效果: 掀页立刻收干净 ------------------------------------
await page.evaluate(() => {
  const sel = document.getElementById('rv-fx');
  if (sel) { sel.value = 'none'; sel.dispatchEvent(new Event('change', { bubbles: true })); }
});
await page.waitForTimeout(60);
const stopped = await page.evaluate(() => {
  const rv = document.getElementById('rv');
  return { anim: rv.getAnimations().length, org: getComputedStyle(rv).transformOrigin,
           tf: getComputedStyle(rv).transform };
});
chk('切走翻卷: 动画收掉且 origin 归位',
    stopped.anim === 0 && stopped.tf === 'none' && Math.abs(parseFloat(stopped.org) - win / 2) < 1,
    JSON.stringify(stopped));

chk('无 JS 报错', errs.length === 0, errs.slice(0, 2).join(' | '));
const back = await writeProgress(page, prog0);
chk('进度已写回原处', !prog0 || back === 200, `status=${back}`);

console.log(`视口 ${VW}×${VH}  key=${KEY}`);
console.log(out.join('\n'));
const fails = out.filter(l => l.startsWith('FAIL')).length;
console.log(`\n${out.length - fails}/${out.length} ok, ${fails} FAIL`);
await browser.close();
process.exit(fails ? 1 : 0);
