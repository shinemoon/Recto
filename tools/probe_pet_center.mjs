/* 书宠正文版心 —— 前台读本要跟「按回阅读」一样居中 (真浏览器探针)。
 *
 * 跑法: cd tools && node probe_pet_center.mjs
 * 环境: RECTO_BASE (默认 5015)
 *
 * 用户口径 (2026-11-23): 「它自己走的路显示页面, 正文需要和'按回阅读'一样居中」
 *
 * 根因 (2026-11-23): 一屏版把 `.adv-read-body p` 的 `margin: 0 0 .68em` (简写)
 * 写在基础规则之后, 把 `margin-left/right: auto` 一起清成 0 —— 正文贴着窗口左缘,
 * 而旁注 (`.adv-inline`) 是 `auto` 居中的, 两者左缘错开一格。
 *
 * 验的事:
 *   ① 每段正文在正文窗口里**左右余白对称** (滚动条占位 10px, 容差 ±12px)
 *   ② 段落的水平中线 ≈ 窗口的中线 (±12px) —— 也就是真的居中, 不是"两边都空"
 *   ③ 旁注 (判定 / 启发 / 交手) 的左缘与正文**同一条竖线** (±2px)
 *   ④ 档案页 (/pet/story/) 的正文也是居中的 (两处同一套口径)
 *   ⑤ 390px 窄屏: 版心本来就比窗口窄不了多少 —— 但不能有横向溢出
 *   ⑥ 0 JS 报错
 *
 * 卫生: 只读 (不翻页、不写进度、不改存档)。
 */
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RECTO_BASE || '5015';
const URL = `http://127.0.0.1:${BASE}`;

let PASS = 0, FAIL = 0;
function ok(cond, name, extra = '') {
  if (cond) { PASS++; console.log('PASS', name); }
  else { FAIL++; console.log('FAIL', name, extra); }
}

const browser = await chromium.launch({ args: ['--no-sandbox'] });

async function openPage(width, height, path) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  await ctx.addCookies([{
    name: 'zy_webauth', value: cookie(), domain: '127.0.0.1', path: '/',
  }]);
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(`${URL}${path}`, { waitUntil: 'domcontentloaded' });
  return { ctx, page, errors };
}

// 量一组段落: 左右余白 / 中线偏差
const MEASURE = () => {
  const body = document.querySelector('#adv-read-body');
  if (!body) return null;
  const br = body.getBoundingClientRect();
  const cs = getComputedStyle(body);
  const padL = parseFloat(cs.paddingLeft) || 0;
  const padR = parseFloat(cs.paddingRight) || 0;
  const innerL = br.left + padL;
  const innerR = br.right - padR;
  // 只量**完整落在窗口里**的那几段 (被裁一半的段, rect 不是它真正的位置)
  const ps = [...body.querySelectorAll(':scope > p')].filter((p) => {
    const r = p.getBoundingClientRect();
    return r.height > 0 && r.top >= br.top - 2 && r.bottom <= br.bottom + 2;
  }).slice(0, 4);
  const rows = ps.map((p) => {
    const r = p.getBoundingClientRect();
    const gapL = r.left - innerL;
    const gapR = innerR - r.right;
    return {
      width: Math.round(r.width),
      gapL: Math.round(gapL),
      gapR: Math.round(gapR),
      // 段落中线 vs 窗口 (含内边距) 中线
      midOff: Math.round((r.left + r.right) / 2 - (innerL + innerR) / 2),
      left: Math.round(r.left),
    };
  });
  const inline = [...body.querySelectorAll(':scope > .adv-inline')]
    .filter((e) => e.getBoundingClientRect().height > 0)
    .map((e) => Math.round(e.getBoundingClientRect().left));
  return { rows, inline, scrollbar: body.offsetWidth - body.clientWidth,
           innerL: Math.round(innerL), innerR: Math.round(innerR) };
};

// ── 桌面: 前台读本 ──────────────────────────────────────────────
const desk = await openPage(1400, 900, '/pet/');
await desk.page.waitForSelector('#adv-read-body p', { timeout: 20000 });
await desk.page.waitForTimeout(900);

const front = await desk.page.evaluate(MEASURE);
ok(!!front && front.rows.length >= 2,
   `有段可量 (${front ? front.rows.length : 0} 段)`);
if (front && front.rows.length) {
  const r0 = front.rows[0];
  // ① 左右余白对称 (右边多 10px 滚动条占位是正常的)
  const skew = Math.abs(r0.gapL - r0.gapR);
  ok(skew <= 12, `左右余白对称 (左 ${r0.gapL} / 右 ${r0.gapR})`);
  // 真的是"居中"而不是"两边都空": 左边余白必须够大
  ok(r0.gapL >= 40, `左边留出了版心余白 (${r0.gapL}px, 段宽 ${r0.width})`);
  // ② 中线对齐
  const worst = Math.max(...front.rows.map((r) => Math.abs(r.midOff)));
  ok(worst <= 12, `段落中线 ≈ 窗口中线 (最大偏 ${worst}px)`);
  // 版心比窗口窄 (不是撑满整行)
  ok(r0.width < front.innerR - front.innerL - 40,
     `版心收窄了 (段宽 ${r0.width} < 窗口 ${front.innerR - front.innerL})`);
  // ③ 旁注左缘与正文同一条竖线
  if (front.inline.length) {
    const d = Math.abs(front.inline[0] - r0.left);
    ok(d <= 2, `旁注左缘与正文对齐 (差 ${d}px)`);
  } else {
    ok(true, '这一回没有旁注可量 (跳过左缘对齐)');
  }
}
ok(desk.errors.length === 0, `桌面 0 JS 报错`, desk.errors.slice(0, 2).join(' | '));
await desk.ctx.close();

// ── 桌面: 档案页 (按回阅读) ─────────────────────────────────────
const arch = await openPage(1400, 900, '/pet/story/');
await arch.page.waitForTimeout(1500);
const archGeo = await arch.page.evaluate(() => {
  const ps = [...document.querySelectorAll('.arch-text p')]
    .filter((p) => p.getBoundingClientRect().height > 0).slice(0, 4);
  if (!ps.length) return null;
  return ps.map((p) => {
    const r = p.getBoundingClientRect();
    const parent = p.parentElement.getBoundingClientRect();
    return {
      gapL: Math.round(r.left - parent.left),
      gapR: Math.round(parent.right - r.right),
    };
  });
});
ok(!!archGeo, '档案页有正文可量');
if (archGeo) {
  const skew = Math.max(...archGeo.map((r) => Math.abs(r.gapL - r.gapR)));
  ok(skew <= 4, `档案页正文居中 (最大左右差 ${skew}px)`);
  ok(archGeo[0].gapL >= 40, `档案页左边有余白 (${archGeo[0].gapL}px)`);
}
ok(arch.errors.length === 0, '档案页 0 JS 报错', arch.errors.slice(0, 2).join(' | '));
await arch.ctx.close();

// ── 窄屏 390 ────────────────────────────────────────────────────
const mob = await openPage(390, 800, '/pet/');
await mob.page.waitForSelector('#adv-read-body p', { timeout: 20000 });
await mob.page.waitForTimeout(900);
const m = await mob.page.evaluate(MEASURE);
ok(!!m && m.rows.length >= 1, '窄屏有段可量');
if (m && m.rows.length) {
  const r0 = m.rows[0];
  ok(Math.abs(r0.gapL - r0.gapR) <= 14,
     `窄屏左右余白接近 (左 ${r0.gapL} / 右 ${r0.gapR})`);
  ok(Math.abs(r0.midOff) <= 14, `窄屏段落中线 ≈ 窗口中线 (偏 ${r0.midOff}px)`);
}
const overflowX = await mob.page.evaluate(() =>
  document.documentElement.scrollWidth - document.documentElement.clientWidth);
ok(overflowX <= 1, `窄屏无横向溢出 (${overflowX}px)`);
ok(mob.errors.length === 0, '窄屏 0 JS 报错', mob.errors.slice(0, 2).join(' | '));
await mob.ctx.close();

await browser.close();
console.log(`\nPASS ${PASS} · FAIL ${FAIL}`);
process.exit(FAIL ? 1 : 0);
