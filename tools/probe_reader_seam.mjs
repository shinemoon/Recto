/* 左右翻页: 列间距 / 位移落点 与「前后页边沿露出」的诊断。
 *
 * 背景 (2026-11-07):
 *   用户报「翻卷模式, 如果翻页很快, 有时候出现阅读区域错位 —— 会露出前后页边沿」。
 *   怀疑两件事:
 *     A. stride (列宽+列间距) 与浏览器**实际**用的列距不一致 → 每翻一页累计错位;
 *     B. 快速连翻时, 位移落点 / 页表在过渡途中被量 → 落点不是整数页。
 *
 * 这个探针只量几何, 不改实现:
 *   1. 实际列距 = 相邻两列 .rv-blk 最小 left 之差, 与 __RV__.page().stride 比;
 *   2. 窗口内是否混进了**相邻列**的块 (= 露出前后页边沿);
 *   3. 快速连翻 12 页后: transform 落点是否等于 -page*stride, 是否还混入邻列。
 *
 * 跑: node tools/probe_reader_seam.mjs
 * env: RV_BASE / RV_KEY / RV_W (视口宽) / RV_H
 */
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || 'a6f63af571f05bb4';
const VW = +(process.env.RV_W || 390);
const VH = +(process.env.RV_H || 780);
const token = cookie();

const out = [];
const chk = (name, cond, extra = '') =>
  out.push(`${cond ? 'ok  ' : 'FAIL'} ${name}${extra ? ' — ' + extra : ''}`);

// 探针会翻页 → 会写服务端进度 → 先快照, 收尾写回
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

async function open() {
  const ctx = await browser.newContext({ viewport: { width: VW, height: VH }, hasTouch: VW < 700 });
  await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(String(e)));
  await page.addInitScript(() => {
    localStorage.setItem('recto.mode', 'page');
    localStorage.setItem('recto.fx', 'none');   // 只看几何, 不播入场动画
  });
  await page.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.rv-page p', { timeout: 20000 });
  await page.waitForTimeout(1200);
  return { page, ctx, errs };
}

// --- 几何量测: 实际列距 / 邻列入侵 -------------------------------------------
const MEASURE = () => {
  const view = document.getElementById('rv-view');
  const content = document.getElementById('rv-content');
  const cand = window.__RV__.page();
  const cs = getComputedStyle(view);
  const padL = parseFloat(cs.paddingLeft) || 0, padR = parseFloat(cs.paddingRight) || 0;
  const vr = view.getBoundingClientRect();
  // 窗口内容区 (真正看得见的矩形)
  const winL = vr.left + padL, winR = vr.right - padR;

  // 每一列的起点: 把块按 left 归并 (同一列的 left 几乎相同)
  const blocks = Array.from(content.querySelectorAll('.rv-blk')).map(el => {
    const r = el.getBoundingClientRect();
    return { ch: +el.dataset.ch, off: +el.dataset.off, l: r.left, r: r.right, w: r.width, h: r.height };
  }).filter(b => b.w > 0.5 && b.h > 0.5)
    // 比一列还宽的块 (宽表格/整幅图) 本来就跨列, 不算"邻列漏进来"
    .filter(b => b.w <= cand.w + 1);

  // 列起点: 用"块左缘"聚类 (容差 1px)
  const lefts = [];
  for (const b of blocks) {
    if (!lefts.length || Math.abs(b.l - lefts[lefts.length - 1]) > 1) {
      if (!lefts.some(x => Math.abs(x - b.l) <= 1)) lefts.push(b.l);
    }
  }
  lefts.sort((a, b) => a - b);
  const pitch = [];
  for (let i = 1; i < lefts.length; i++) pitch.push(+(lefts[i] - lefts[i - 1]).toFixed(3));

  // 邻列入侵: 块落在窗口内 (与窗口有交集) 但它的列起点不在窗口内
  // 用"块中心落在窗口外但块本身跨进窗口"更准 —— 只统计溢入量 > 0.5px 的
  const intrude = [];
  for (const b of blocks) {
    const inside = b.l >= winL - 0.5 && b.l < winR;
    if (inside) continue;
    const overlap = Math.min(b.r, winR) - Math.max(b.l, winL);
    if (overlap > 0.5) {
      intrude.push({ ch: b.ch, off: b.off, left: +b.l.toFixed(2), overlap: +overlap.toFixed(2),
                     side: b.l < winL ? 'prev' : 'next' });
    }
  }
  intrude.sort((a, b) => b.overlap - a.overlap);

  const m = new DOMMatrixReadOnly(getComputedStyle(content).transform);
  return {
    page: cand.page, count: cand.count, w: cand.w, stride: cand.stride, gap: cand.gap,
    tfX: +m.m41.toFixed(3), expectX: +(-cand.page * cand.stride).toFixed(3),
    colPitchMin: pitch.length ? Math.min(...pitch) : null,
    colPitchMax: pitch.length ? Math.max(...pitch) : null,
    colPitchSample: pitch.slice(0, 6),
    cols: lefts.length,
    cw: content.scrollWidth, ch: content.scrollHeight,
    winL: +winL.toFixed(2), winR: +winR.toFixed(2), winW: +(winR - winL).toFixed(2),
    intrude: intrude.slice(0, 6), intrudeCount: intrude.length,
  };
};

const { page, ctx, errs } = await open();
const prog0 = await readProgress(page);

// --- 1. 原始几何 -------------------------------------------------------------
const m0 = await page.evaluate(MEASURE);
chk('列宽 = 可视区真实宽度 (±0.05px)', Math.abs(m0.w - m0.winW) <= 0.05,
    `w=${m0.w} winW=${m0.winW}`);
chk('stride = 列宽 + 列间距', Math.abs(m0.stride - (m0.w + m0.gap)) <= 0.01,
    `stride=${m0.stride} w+g=${(m0.w + m0.gap).toFixed(3)}`);
if (m0.colPitchMin != null) {
  chk('实际列距 == stride (±0.05px)', Math.abs(m0.colPitchMin - m0.stride) <= 0.05,
      `pitch=${m0.colPitchMin}..${m0.colPitchMax} stride=${m0.stride}`);
} else {
  out.push('skip 实际列距 — 只量到一列');
}
chk('第 0 页无邻列入侵', m0.intrudeCount === 0,
    `${m0.intrudeCount} 块, worst=${JSON.stringify(m0.intrude[0] || null)}`);

// --- 2. 逐页翻 (慢) 到第 12 页, 每页查落点与入侵 -----------------------------
const slow = [];
for (let i = 0; i < 12; i++) {
  await page.keyboard.press('PageDown');
  await page.waitForTimeout(420);              // 等 slide 过渡 / 效果播完
  const m = await page.evaluate(MEASURE);
  slow.push(m);
}
const slowBad = slow.filter(m => Math.abs(m.tfX - m.expectX) > 0.05 || m.intrudeCount > 0);
chk('慢翻 12 页: 落点每页都精确', slow.filter(m => Math.abs(m.tfX - m.expectX) > 0.05).length === 0,
    slow.filter(m => Math.abs(m.tfX - m.expectX) > 0.05)
      .map(m => `p${m.page} tf=${m.tfX} exp=${m.expectX}`).slice(0, 3).join('; '));
chk('慢翻 12 页: 无邻列入侵', slow.every(m => m.intrudeCount === 0),
    slow.filter(m => m.intrudeCount > 0)
      .map(m => `p${m.page} ×${m.intrudeCount} ${JSON.stringify(m.intrude[0])}`).slice(0, 3).join('; '));

// --- 3. 快速连翻 12 页 (不等人) ----------------------------------------------
await page.evaluate(() => window.scrollTo(0, 0));
const before = await page.evaluate(() => window.__RV__.page().page);
for (let i = 0; i < 12; i++) {
  await page.keyboard.press('PageDown');
  await page.waitForTimeout(35);               // 比 .26s 过渡快得多
}
const midFast = await page.evaluate(MEASURE);   // 过渡还在跑
await page.waitForTimeout(1400);                // 全部落定
const fast1 = await page.evaluate(MEASURE);
const after = fast1.page;

chk('快速连翻: 落点最终精确', Math.abs(fast1.tfX - fast1.expectX) <= 0.05,
    `p${fast1.page} tf=${fast1.tfX} exp=${fast1.expectX}`);
chk('快速连翻: 落定后无邻列入侵', fast1.intrudeCount === 0,
    `${fast1.intrudeCount} 块 ${JSON.stringify(fast1.intrude[0] || null)}`);
chk('快速连翻: 页号与位移自洽 (无跨页跳错)', fast1.page === before + 12 || fast1.page === Math.min(before + 12, fast1.count - 1),
    `before=${before} after=${after} count=${fast1.count}`);

// --- 4. 过渡**途中**是否露边 (用户看到的其实是动画中间帧) --------------------
// 只做记录: 中间帧本来就会滑过邻列, 关键是有没有"停在半页"的稳定态
out.push(`info 过渡途中帧: p${midFast.page} tf=${midFast.tfX} exp=${midFast.expectX} ` +
         `intrude=${midFast.intrudeCount}`);

// --- 5. 反复快速来回 (最容易撞上"停在半截图") ---------------------------------
let stoppedBad = 0, stoppedWorst = null;
for (let round = 0; round < 3; round++) {
  const dir = round % 2 === 0 ? 'PageDown' : 'PageUp';
  for (let i = 0; i < 6; i++) {
    await page.keyboard.press(dir);
    await page.waitForTimeout(28);
  }
  await page.waitForTimeout(1300);
  const m = await page.evaluate(MEASURE);
  const bad = Math.abs(m.tfX - m.expectX) > 0.05 || m.intrudeCount > 0;
  if (bad) { stoppedBad++; if (!stoppedWorst) stoppedWorst = m; }
}
chk('快速来回 3 轮: 每次落定都对齐且无入侵', stoppedBad === 0,
    stoppedWorst ? `p${stoppedWorst.page} tf=${stoppedWorst.tfX} exp=${stoppedWorst.expectX} ` +
                   `intrude=${stoppedWorst.intrudeCount} ${JSON.stringify(stoppedWorst.intrude[0] || null)}` : '');

// --- 6. 章末补章 (ensurePages) 之后仍然对齐 -----------------------------------
for (let i = 0; i < 200; i++) {                       // 一路翻到本章末
  const m = await page.evaluate(() => window.__RV__.page());
  if (m.page >= m.count - 1) break;
  await page.keyboard.press('PageDown');
  await page.waitForTimeout(60);
}
await page.waitForTimeout(1600);
await page.keyboard.press('PageDown');                 // 触发补下一章
await page.waitForTimeout(2200);
const mEnd = await page.evaluate(MEASURE);
chk('章末补章后落点仍精确', Math.abs(mEnd.tfX - mEnd.expectX) <= 0.05,
    `p${mEnd.page}/${mEnd.count} tf=${mEnd.tfX} exp=${mEnd.expectX}`);
chk('章末补章后无邻列入侵', mEnd.intrudeCount === 0,
    `${mEnd.intrudeCount} 块 ${JSON.stringify(mEnd.intrude[0] || null)}`);

chk('无 JS 报错', errs.length === 0, errs.slice(0, 2).join(' | '));

// 收尾: 进度写回
const back = await writeProgress(page, prog0);
chk('进度已写回原处', !prog0 || back === 200, `status=${back}`);

console.log(`\n视口 ${VW}×${VH}  key=${KEY}`);
console.log('初始几何:', JSON.stringify({ w: m0.w, stride: m0.stride, gap: m0.gap,
  winW: m0.winW, cols: m0.cols, colPitchSample: m0.colPitchSample }, null, 1));
console.log(out.join('\n'));
const fails = out.filter(l => l.startsWith('FAIL')).length;
console.log(`\n${out.length - fails}/${out.length} ok, ${fails} FAIL`);

await browser.close();
process.exit(fails ? 1 : 0);
