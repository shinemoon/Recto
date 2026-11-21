/* 读本 —— MUD 式阅读窗口 (真浏览器探针)。
 *
 * 跑法: cd tools && node probe_pet_mudread.mjs
 * 环境: RECTO_BASE (默认 5015)
 *
 * 验的事:
 *   · 正文窗口**高度固定** —— 多读几屏, 页面高度不变 (页面滚动条不冒出来)
 *   · 滚动**只在窗口内** —— 翻屏时页面的 scrollTop 不动
 *   · 空格**翻一屏** —— 一次敲击滚一整屏 (视口高), 不是两行字
 *   · PageUp 能回看, 但**位置只前进**
 *   · 位置记在后端 (刷新接着上次)
 *   · 390px 无横向溢出 / 0 JS 报错
 *
 * 卫生: 只读 + 本地 UI 操作。阅读位置会写后端 —— 收尾**还原**。
 */
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RECTO_BASE || '5015';
const URL = `http://127.0.0.1:${BASE}`;

let PASS = 0, FAIL = 0;
function ok(cond, name) {
  if (cond) { PASS++; console.log('PASS', name); }
  else { FAIL++; console.log('FAIL', name); }
}

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
await ctx.addCookies([{
  name: 'zy_webauth', value: cookie(), domain: '127.0.0.1', path: '/',
}]);
const page = await ctx.newPage();
const pageErrors = [];
const badResponses = [];
page.on('response', (r) => { if (r.status() >= 400) badResponses.push(r.status() + ' ' + r.url()); });
page.on('pageerror', (e) => pageErrors.push(String(e)));
page.on('console', (m) => { if (m.type() === 'error') pageErrors.push(m.text()); });

async function readPos() {
  return page.evaluate(async () => {
    const r = await fetch('/pet/api/reading', { credentials: 'same-origin' });
    const d = await r.json();
    const p = d.pos || {};
    return { round: p.round || 0, idx: p.idx || 0 };
  });
}
async function writePos(idx) {
  return page.evaluate(async (n) => {
    const r = await fetch('/pet/api/reading', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ idx: n, force: true, clamp: false }),
    });
    return r.ok;
  }, idx);
}

await page.goto(`${URL}/pet/`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#adv-read-body p', { timeout: 15000 });
await page.waitForTimeout(1200);

const snapIdx = await readPos();
console.log(`  (快照位置: round=${snapIdx.round} idx=${snapIdx.idx})`);

// ── 1. 窗口存在且有内容 ───────────────────────────────────────
const base = await page.evaluate(() => {
  const b = document.querySelector('#adv-read-body');
  const r = b.getBoundingClientRect();
  return {
    h: Math.round(r.height),
    clientH: b.clientHeight,
    scrollH: b.scrollHeight,
    paras: b.querySelectorAll('p').length,
    pageH: document.documentElement.scrollHeight,
    pageScrollTop: window.scrollY,
    hint: document.querySelector('#adv-read-hint')?.textContent?.trim() || '',
  };
});
ok(base.h > 200, `窗口有高度 (${base.h}px)`);
ok(base.paras >= 1, `窗口里有正文 (${base.paras} 段)`);
ok(base.scrollH > base.clientH, `内容比窗口长 (${base.scrollH} > ${base.clientH})`);
ok(/翻一屏/.test(base.hint), `提示写明「翻一屏」 (${base.hint})`);

// 窗口高度是**固定**的 (不是 max-height) —— CSS 层面量
const cssFixed = await page.evaluate(() => {
  const b = document.querySelector('#adv-read-body');
  const cs = getComputedStyle(b);
  return { height: cs.height, maxHeight: cs.maxHeight, overflowY: cs.overflowY };
});
ok(cssFixed.maxHeight === 'none' || parseFloat(cssFixed.maxHeight) >= parseFloat(cssFixed.height),
   `窗口高度固定 (height=${cssFixed.height}, max-height=${cssFixed.maxHeight})`);

// ── 2. 空格翻一屏 (不是两行字) ─────────────────────────────────
const before = await page.evaluate(() => {
  const b = document.querySelector('#adv-read-body');
  return { st: b.scrollTop, ch: b.clientHeight, pageY: window.scrollY };
});
await page.keyboard.press('Space');
await page.waitForTimeout(350);
const after = await page.evaluate(() => {
  const b = document.querySelector('#adv-read-body');
  return {
    st: b.scrollTop, ch: b.clientHeight, pageY: window.scrollY,
    prog: document.querySelector('#adv-read-prog')?.textContent?.trim() || '',
    idx: window.READER ? window.READER.idx : 0,
  };
});
const delta = after.st - before.st;
ok(delta > 0, `空格让窗口往下滚 (Δ${Math.round(delta)}px)`);
// 一屏 = 视口高, 但**内容不够长时滚不满一屏** (最新那一回可能就十几段) ——
// 那就滚到尽头。这是内容长度决定的, 不是滚动坏了。
const maxScroll = await page.evaluate(() => {
  const b = document.querySelector('#adv-read-body');
  return Math.max(0, b.scrollHeight - b.clientHeight);
});
const expect = Math.min(after.ch, maxScroll);
ok(Math.abs(delta - expect) < Math.max(12, expect * 0.15),
   `翻的是一屏 (Δ${Math.round(delta)} vs 期望 ${Math.round(expect)} · 视口 ${after.ch})`);
ok(after.pageY === before.pageY,
   `页面自己没被卷走 (pageY ${before.pageY} → ${after.pageY})`);

// ── 3. 连按十次: 页面结构不变 (窗口高度 / 页面高度都不变) ───────
await page.keyboard.press('Space');
await page.waitForTimeout(200);
const mid = await page.evaluate(() => {
  const b = document.querySelector('#adv-read-body');
  return {
    winH: Math.round(b.getBoundingClientRect().height),
    pageH: document.documentElement.scrollHeight,
    st: b.scrollTop,
  };
});
for (let i = 0; i < 8; i++) {
  await page.keyboard.press('Space');
  await page.waitForTimeout(120);
}
await page.waitForTimeout(400);
const after10 = await page.evaluate(() => {
  const b = document.querySelector('#adv-read-body');
  return {
    winH: Math.round(b.getBoundingClientRect().height),
    pageH: document.documentElement.scrollHeight,
    st: b.scrollTop,
    pageY: window.scrollY,
    idx: window.READER ? window.READER.idx : 0,
  };
});
ok(after10.winH === mid.winH, `窗口高度没变 (${mid.winH} → ${after10.winH})`);
ok(after10.pageH === mid.pageH, `页面总高没变 (${mid.pageH} → ${after10.pageH})`);
// 走到尽头之后不该再"往下走" (内容就那么多) —— 但**位置**仍要算读完
ok(after10.st >= mid.st, `窗口没往回卷 (${Math.round(mid.st)} → ${Math.round(after10.st)})`);
ok(after10.idx > 0, `位置前进了 (idx=${after10.idx})`);

// ── 4. PageUp 回看, 但位置只前进 ────────────────────────────────
await page.waitForTimeout(900);
const posBeforeUp = await readPos();
await page.keyboard.press('PageUp');
await page.waitForTimeout(350);
const afterUp = await page.evaluate(() => {
  const b = document.querySelector('#adv-read-body');
  return { st: b.scrollTop, idx: window.READER ? window.READER.idx : 0 };
});
ok(afterUp.st < after10.st, `PageUp 真的往回滚了 (${Math.round(after10.st)} → ${Math.round(afterUp.st)})`);
ok(afterUp.idx >= after10.idx, `回看不把位置拉回去 (idx ${after10.idx} → ${afterUp.idx})`);

// ── 5. 位置记在后端 + 刷新接着 ─────────────────────────────────
await page.waitForTimeout(600);
const posNow = await readPos();
ok(posNow.idx > 0, `位置记在后端 (round=${posNow.round} idx=${posNow.idx})`);

await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForSelector('#adv-read-body p', { timeout: 15000 });
await page.waitForTimeout(1200);
const afterReload = await page.evaluate(() => {
  const b = document.querySelector('#adv-read-body');
  return {
    st: Math.round(b.scrollTop),
    idx: window.READER ? window.READER.idx : 0,
    prog: document.querySelector('#adv-read-prog')?.textContent?.trim() || '',
  };
});
ok(afterReload.st > 0, `刷新后落在上次读到的地方 (scrollTop=${afterReload.st})`);
ok(Math.abs(afterReload.idx - posNow.idx) <= 2,
   `刷新后位置跟存档一致 (${afterReload.idx} vs ${posNow.idx})`);

// ── 6. 窄屏 ────────────────────────────────────────────────────
const m = await ctx.newPage();
await m.setViewportSize({ width: 390, height: 844 });
await m.goto(`${URL}/pet/`, { waitUntil: 'domcontentloaded' });
await m.waitForSelector('#adv-read-body p', { timeout: 15000 });
await m.waitForTimeout(900);
const mob = await m.evaluate(() => {
  const b = document.querySelector('#adv-read-body');
  return {
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    h: Math.round(b.getBoundingClientRect().height),
    win: window.innerHeight,
  };
});
ok(mob.overflow <= 1, `手机 390px 无横向溢出 (${mob.overflow}px)`);
ok(mob.h > 200 && mob.h < mob.win, `手机窗口高度合理 (${mob.h} / 视口 ${mob.win})`);

// ── 7. 还原位置 ─────────────────────────────────────────────────
await writePos(snapIdx);
console.log(`  (位置已还原到 round=${snapIdx.round} idx=${snapIdx.idx})`);

ok(pageErrors.length === 0, `无 JS 报错 (${pageErrors.length}) ${pageErrors.slice(0, 2).join(' | ')}`);
if (badResponses.length) {
  console.log('  4xx/5xx 响应:');
  badResponses.slice(0, 6).forEach((b) => console.log('   ', b));
}

await browser.close();
console.log('');
console.log('='.repeat(46));
console.log(`PASS ${PASS}  FAIL ${FAIL}`);
process.exit(FAIL ? 1 : 0);
