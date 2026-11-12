/* 书宠 · 陪读卡 —— 真浏览器探针 (生产 5015)。
 *
 * 跑法: cd tools && node probe_pet_companion.mjs
 * 环境: RECTO_BASE (默认 5015)
 *
 * 验的事:
 *   · 页面里有「陪读」卡, 数字不是占位符
 *   · 「立即折算」按钮点下去有回执, 且第二次点是幂等的 (没有新增时长)
 *   · 陪读分钟数与后端接口一致
 *   · 0 pageerror
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
page.on('pageerror', (e) => pageErrors.push(String(e)));
page.on('console', (m) => { if (m.type() === 'error') pageErrors.push(m.text()); });

// ── 进书宠页 ──────────────────────────────────────
await page.goto(`${URL}/pet/`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#companion-state', { state: 'attached', timeout: 15000 });
await page.waitForTimeout(600);

// ── 卡片在 ────────────────────────────────────────
const hasCard = await page.$('#card-growth');
ok(!!hasCard, '有「陪读」卡');
const h2 = await page.$eval('#card-growth h2', (e) => e.textContent.trim()).catch(() => '');
ok(h2.includes('陪读'), `卡标题含「陪读」 got "${h2}"`);

// 规则表渲染出来了 (不是空)
const rulesLen = await page.$eval('#growth-rules', (e) => e.innerText.trim().length);
ok(rulesLen > 10, `换算比例表已渲染 (${rulesLen} 字)`);

// 陪读状态渲染出来了
const stateText = await page.$eval('#companion-state', (e) => e.innerText.trim());
ok(stateText.length > 5, `陪读状态已渲染 (${stateText.slice(0, 60)})`);
ok(/累计陪读/.test(stateText), `状态里有「累计陪读」 got "${stateText.slice(0, 40)}"`);

// 徽标显示分钟数
const pill = await page.$eval('#companion-pill', (e) => e.textContent.trim());
ok(/分钟/.test(pill), `徽标显示分钟数 got "${pill}"`);

const topText = await page.$eval('#companion-state', (e) => e.innerText);
ok(/读得最多/.test(topText), '状态里有「读得最多」');
// 书名 (不是 hash)
const bookTitles = await page.$$eval('.cs-book-t', (els) => els.map((e) => e.textContent.trim()));
ok(bookTitles.length > 0, `列出了书 (${bookTitles.length} 本)`);
// hash 兜底只该出现在"书库里没有的书"上 (云端占位) —— 不该占多数
const hashLike = bookTitles.filter((t) => /^[0-9a-f]{16,}$/.test(t));
ok(hashLike.length <= Math.ceil(bookTitles.length / 2),
   `多数显示书名 (hash 兜底 ${hashLike.length}/${bookTitles.length})`);
ok(bookTitles.some((t) => !/^[0-9a-f]{16,}$/.test(t)),
   `至少有一本显示书名 (${bookTitles.find((t) => !/^[0-9a-f]{16,}$/.test(t))})`);

// ── 后端接口对得上 ────────────────────────────────
const api = await page.evaluate(async () => {
  const r = await fetch('/pet/api/companion', { headers: { 'Content-Type': 'application/json' } });
  return await r.json();
});
ok(typeof api.total_minutes === 'number', `接口有 total_minutes got ${api.total_minutes}`);
ok(!!api.tick, '接口有 tick 状态');
const shownMin = parseInt((stateText.match(/累计陪读\s*(\d+)/) || [])[1] || '-1', 10);
ok(shownMin === api.total_minutes,
   `页面分钟数 == 接口 (页面 ${shownMin} / 接口 ${api.total_minutes})`);

// ── 立即折算 ──────────────────────────────────────
const btn = await page.$('#btn-companion-feed');
ok(!!btn, '有「立即折算」按钮');
await btn.click();
await page.waitForTimeout(900);
const st1 = await page.$eval('#companion-status', (e) => e.innerText.trim());
ok(st1.length > 0, `点完有回执 got "${st1}"`);

// 幂等: 再点一次, 应该说"没有新增"
await btn.click();
await page.waitForTimeout(900);
const st2 = await page.$eval('#companion-status', (e) => e.innerText.trim());
ok(/没有新增/.test(st2), `第二次是幂等的 got "${st2}"`);

// ── 人物面板还在 (折算后要刷新) ──────────────────
const nameText = await page.$eval('#pet-name', (e) => e.textContent.trim());
ok(nameText.length > 0, `人物名还在 got "${nameText}"`);

// ── 直角口径 (全站要求) ──────────────────────────
const radii = await page.$$eval('#card-growth, #card-growth .cs-meta, #companion-state', (els) =>
  els.map((e) => getComputedStyle(e).borderTopLeftRadius));
ok(radii.every((r) => r === '0px'), `陪读卡直角 (${radii.join(',')})`);

// ── 窄屏不出界 ────────────────────────────────────
await page.setViewportSize({ width: 390, height: 780 });
await page.waitForTimeout(300);
const overflow = await page.evaluate(() =>
  document.documentElement.scrollWidth - document.documentElement.clientWidth);
ok(overflow <= 0, `390px 无横向溢出 (${overflow})`);

ok(pageErrors.length === 0, `0 pageerror (${pageErrors.slice(0, 2).join(' | ')})`);

await browser.close();
console.log(`\n${PASS} passed, ${FAIL} failed`);
process.exit(FAIL ? 1 : 0);
