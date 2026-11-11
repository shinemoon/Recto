/* 书宠分区 —— 真浏览器探针 (生产 5015)。
 *
 * 跑法: cd tools && node probe_pet.mjs
 * 环境: RECTO_BASE (默认 5015)
 */
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RECTO_BASE || '5015';
const URL = `http://127.0.0.1:${BASE}`;

let PASS = 0, FAIL = 0;
const errors = [];
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

// ── 导航里有「书宠」 ───────────────────────────────
await page.goto(`${URL}/`, { waitUntil: 'domcontentloaded' });
const navLabels = await page.$$eval('.rc-nav a', (as) => as.map((a) => a.textContent.trim()));
ok(navLabels.includes('书宠'), `顶栏导航含「书宠」 (${navLabels.join('/')})`);

// ── 点进去 ────────────────────────────────────────
const petLink = await page.$('.rc-nav a[href="/pet/"]');
ok(!!petLink, '导航里书宠的链接指向 /pet/');
if (petLink) {
  await petLink.click();
  await page.waitForLoadState('domcontentloaded');
  await page.waitForTimeout(400);
}
ok(page.url().includes('/pet/'), `点到书宠分区 (${page.url()})`);
ok((await page.title()).includes('书宠'), `标题带「书宠」 (${await page.title()})`);

// ── 页面结构 ──────────────────────────────────────
const cards = await page.$$eval('.rc-pet .card h2', (hs) => hs.map((h) => h.textContent.trim()));
ok(cards.length >= 3, `有 ${cards.length} 张卡 (${cards.join(' / ')})`);
ok(cards.some((c) => c.includes('模型接入')), '有「模型接入」卡');
ok(cards.some((c) => c.includes('模型库')), '有「模型库」卡');
ok(cards.some((c) => c.includes('人格')), '有「人格」卡');

// ── 表单字段 ──────────────────────────────────────
const ids = await page.$$eval('.rc-pet input, .rc-pet select', (els) => els.map((e) => e.id));
for (const want of ['llm-base', 'llm-model', 'llm-key', 'llm-provider', 'llm-timeout',
                    'llm-maxtok', 'llm-temp', 'llm-thinking', 'llm-preset',
                    'llm-model-sel', 'pet-name-input', 'pet-style', 'pet-chattiness']) {
  ok(ids.includes(want), `字段存在: ${want}`);
}

// ── 预设下拉被填上了 ──────────────────────────────
await page.waitForFunction(() => document.querySelectorAll('#llm-preset option').length > 1, { timeout: 5000 }).catch(() => {});
const presetCount = await page.$$eval('#llm-preset option', (o) => o.length);
ok(presetCount > 1, `预设下拉有 ${presetCount - 1} 条`);

// ── 状态药丸 ──────────────────────────────────────
const stateText = await page.$eval('#pet-state', (e) => e.textContent.trim());
ok(stateText.length > 0, `状态药丸有文案: ${stateText}`);

// ── 模型库表头 ────────────────────────────────────
const headers = await page.$$eval('.rc-pet table.cats.models th', (th) => th.map((t) => t.textContent.trim()));
ok(headers.length >= 8, `模型库表头 ${headers.length} 列`);

// ── 横向不溢出 ────────────────────────────────────
const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
ok(overflow <= 0, `桌面无横向溢出 (${overflow}px)`);

// ── 手机 ──────────────────────────────────────────
await page.setViewportSize({ width: 390, height: 780 });
await page.waitForTimeout(300);
const ov2 = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
ok(ov2 <= 0, `手机 390px 无横向溢出 (${ov2}px)`);
const gridCols = await page.$eval('.rc-pet .grid', (g) => getComputedStyle(g).gridTemplateColumns.split(' ').length);
ok(gridCols === 1, `手机表单单列 (${gridCols} 列)`);

// ── 无 JS 报错 ────────────────────────────────────
ok(pageErrors.length === 0, `无 JS 报错 (${pageErrors.length}): ${pageErrors.slice(0, 3).join(' | ')}`);

await browser.close();
console.log(`\n${PASS} passed, ${FAIL} failed`);
if (FAIL) { errors.push(...pageErrors); console.log(errors.join('\n')); process.exit(1); }
