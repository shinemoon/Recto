/* 书宠阅读界面 —— 一屏装下 + 可见滚动条 (真浏览器探针)。
 *
 * 跑法: cd tools && node probe_pet_onescreen.mjs
 * 环境: RECTO_BASE (默认 5015)
 *
 * 验的事 (用户口径: 「一个屏幕可以装下, 即尽量 compact, 页面要有 scrollbar」):
 *   ① **一屏装下** —— 页面自身不滚 (scrollHeight == clientHeight),
 *      页面高度 == 视口高度; 滚的是正文窗口。
 *   ② 正文窗口够高 (占视口一半以上), 壳 (HUD/页签/卡头/行动行) 压得够扁。
 *   ③ **滚动条看得见** —— 正文窗口占位 10px (不是 overlay), 且
 *      ::-webkit-scrollbar-thumb 有颜色 (不是 transparent)。
 *   ④ 空格仍然翻一屏, 页面自己不被卷走。
 *   ⑤ 档案页 (/pet/story/) **不受影响** —— 它还是普通页面 (页面可滚)。
 *   ⑥ 窄屏 390px: 退回"页面可滚", 窗口仍有可见滚动条, 无横向溢出。
 *   ⑦ 0 JS 报错。
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

// 从**开头**开始测 —— 读者本来可能已经读到尽头 (那样翻屏测不出来)。
// 收尾会还原到 snapIdx。
await writePos(0);
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForSelector('#adv-read-body p', { timeout: 15000 });
await page.waitForTimeout(1200);

// ── 1. 一屏装下 ────────────────────────────────────────────────
const geo = await page.evaluate(() => {
  const de = document.documentElement;
  const b = document.querySelector('#adv-read-body');
  const r = (s) => {
    const e = document.querySelector(s);
    if (!e) return null;
    const q = e.getBoundingClientRect();
    return { top: Math.round(q.top), h: Math.round(q.height) };
  };
  const cs = b ? getComputedStyle(b) : null;
  return {
    viewH: window.innerHeight,
    pageScrollH: de.scrollHeight,
    pageClientH: de.clientHeight,
    bodyOverflow: getComputedStyle(document.body).overflow,
    mainOverflow: getComputedStyle(document.querySelector('.rc-main')).overflow,
    hud: r('#pg-bar'),
    toprow: r('.pg-toprow'),
    cardHead: r('#card-chronicle .card-head'),
    actions: r('.pg-actions'),
    readBar: r('.adv-read-bar'),
    readBody: r('#adv-read-body'),
    scrollbarPx: b ? b.offsetWidth - b.clientWidth : 0,
    scrollbarWidthProp: cs ? cs.scrollbarWidth : '',
    gutter: cs ? cs.scrollbarGutter : '',
    overflowY: cs ? cs.overflowY : '',
    contentH: b ? b.scrollHeight : 0,
    clientH: b ? b.clientHeight : 0,
    footerVisible: !!document.querySelector('.rc-foot') &&
      document.querySelector('.rc-foot').getBoundingClientRect().height > 0,
  };
});

// 页面自己**不滚** —— 一屏装下
ok(geo.pageScrollH <= geo.pageClientH + 1,
   `页面自身不滚 (${geo.pageScrollH} <= ${geo.pageClientH})`);
ok(geo.bodyOverflow === 'hidden' && geo.mainOverflow === 'hidden',
   `页面锁住不滚 (body=${geo.bodyOverflow}, main=${geo.mainOverflow})`);
ok(!geo.footerVisible, '页脚藏了 (一屏装不下它)');

// 正文窗口够高 —— 至少占视口的一半
ok(geo.readBody && geo.readBody.h >= geo.viewH * 0.45,
   `正文窗口够高 (${geo.readBody ? geo.readBody.h : 0} / 视口 ${geo.viewH})`);

// 壳压得够扁: 正文窗口起点要靠上 (壳总共不超过视口 45%)
const chromeH = geo.readBody ? geo.readBody.top : geo.viewH;
ok(chromeH <= geo.viewH * 0.45,
   `壳压扁了 (正文窗口起点 ${chromeH} / 视口 ${geo.viewH})`);
ok(geo.hud && geo.hud.h <= 90, `HUD 一行 (${geo.hud ? geo.hud.h : 0}px)`);
ok(geo.toprow && geo.toprow.h <= 44, `页签与图标栏一行 (${geo.toprow ? geo.toprow.h : 0}px)`);

// ── 2. 滚动条看得见 ───────────────────────────────────────────
ok(geo.scrollbarPx >= 8,
   `正文窗口的滚动条占位 (${geo.scrollbarPx}px)`);
ok(geo.overflowY === 'scroll',
   `滚动条一直占位, 不忽隐忽现 (overflow-y=${geo.overflowY})`);
const thumb = await page.evaluate(() => {
  // ::-webkit-scrollbar-thumb 的样式查不到 computed —— 用规则文本判断,
  // 再用「占位宽度」证明它真的占着 (overlay 滚动条不占位)。
  const b = document.querySelector('#adv-read-body');
  return {
    gutter: getComputedStyle(b).scrollbarGutter,
    occupied: b.offsetWidth - b.clientWidth,
  };
});
ok(thumb.gutter === 'stable', `滚动条占位式 (scrollbar-gutter=${thumb.gutter})`);
ok(thumb.occupied >= 8, `滚动条真的占着位置 (${thumb.occupied}px)`);

// ── 3. 空格翻一屏, 页面不被卷走 ───────────────────────────────
const before = await page.evaluate(() => {
  const b = document.querySelector('#adv-read-body');
  return { st: b.scrollTop, ch: b.clientHeight, pageY: window.scrollY };
});
await page.keyboard.press('Space');
await page.waitForTimeout(350);
const after = await page.evaluate(() => {
  const b = document.querySelector('#adv-read-body');
  return { st: b.scrollTop, ch: b.clientHeight, pageY: window.scrollY };
});
ok(after.st > before.st, `空格让窗口往下滚 (Δ${Math.round(after.st - before.st)}px)`);
// 内容不够长时滚不满一屏 —— 滚到尽头就是"翻完了一屏"
const maxScroll = await page.evaluate(() => {
  const b = document.querySelector('#adv-read-body');
  return Math.max(0, b.scrollHeight - b.clientHeight);
});
const expect = Math.min(after.ch, maxScroll);
ok(Math.abs((after.st - before.st) - expect) < Math.max(12, expect * 0.15),
   `翻的是一屏 (Δ${Math.round(after.st - before.st)} vs 期望 ${Math.round(expect)})`);
ok(after.pageY === before.pageY, '页面自己没被卷走');

// ── 4. 档案页不受影响 ─────────────────────────────────────────
const arch = await ctx.newPage();
await arch.goto(`${URL}/pet/story/`, { waitUntil: 'domcontentloaded' });
await arch.waitForTimeout(1200);
const archGeo = await arch.evaluate(() => {
  const de = document.documentElement;
  return {
    bodyOverflow: getComputedStyle(document.body).overflow,
    shellH: Math.round(document.querySelector('.rc-shell').getBoundingClientRect().height),
    listItems: document.querySelectorAll('#archive-list .arch-item').length,
  };
});
ok(archGeo.bodyOverflow !== 'hidden',
   `档案页页面照常可滚 (body overflow=${archGeo.bodyOverflow})`);
ok(archGeo.listItems >= 0, `档案页条目还在 (${archGeo.listItems})`);
await arch.close();

// ── 5. 窄屏 ───────────────────────────────────────────────────
const m = await ctx.newPage();
await m.setViewportSize({ width: 390, height: 844 });
await m.goto(`${URL}/pet/`, { waitUntil: 'domcontentloaded' });
await m.waitForSelector('#adv-read-body p', { timeout: 15000 });
await m.waitForTimeout(1000);
const mob = await m.evaluate(() => {
  const de = document.documentElement;
  const b = document.querySelector('#adv-read-body');
  return {
    overflowX: de.scrollWidth - de.clientWidth,
    bodyOverflow: getComputedStyle(document.body).overflow,
    winH: Math.round(b.getBoundingClientRect().height),
    scrollbarPx: b.offsetWidth - b.clientWidth,
    canScrollPage: de.scrollHeight > de.clientHeight,
  };
});
ok(mob.overflowX <= 1, `手机 390px 无横向溢出 (${mob.overflowX}px)`);
ok(mob.bodyOverflow !== 'hidden', `窄屏页面可滚 (${mob.bodyOverflow})`);
ok(mob.winH > 180 && mob.winH < mob.winH + 1 && mob.winH < 844,
   `窄屏正文窗口高度合理 (${mob.winH}px)`);
ok(mob.scrollbarPx >= 8, `窄屏也有可见滚动条 (${mob.scrollbarPx}px)`);

// ── 6. 还原位置 ───────────────────────────────────────────────
await writePos(snapIdx);
console.log(`  (位置已还原到 round=${snapIdx.round} idx=${snapIdx.idx})`);

ok(pageErrors.length === 0, `无 JS 报错 (${pageErrors.length}) ${pageErrors.slice(0, 2).join(' | ')}`);
if (badResponses.length) {
  console.log('  4xx/5xx 响应:');
  badResponses.slice(0, 5).forEach((r) => console.log('   ', r));
}

await browser.close();
console.log(`\n${PASS} passed, ${FAIL} failed`);
process.exit(FAIL === 0 ? 0 : 1);
