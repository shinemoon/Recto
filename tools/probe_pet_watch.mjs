/* 书宠: 「推演有没有在动 + 为什么没动」探针 (生产 5015)。
 *
 * 验的是 2026-11-13 那轮修的两个洞:
 *   · 页面能说清"上一回没写出来"的原因 (last_story_error), 并有「补写上一回」入口
 *   · 推演模型那行写出来了; 推理型模型要带「推理型」提醒
 *   · 最新一回带着交手 (duel) 与判定 (checks) —— 故事不是只有走路
 *   · 故事时钟与"已走几回"对得上, 390px 无横向溢出
 *
 * 跑法: cd tools && node probe_pet_watch.mjs
 * 环境: RECTO_BASE (默认 5015)
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
await page.goto(`${URL}/pet/`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(1200);

const api = await page.evaluate(async () => {
  const r = await fetch('/pet/api/chronicle', { credentials: 'same-origin' });
  return r.json();
});
const st = api.state || {};

// ── 1. 状态行说清模型与失败 ─────────────────────────
const rows = await page.$$eval('#chronicle-state .cs-meta-row', (rs) =>
  rs.map((r) => [r.querySelector('.cs-meta-l')?.textContent || '',
    r.querySelector('.cs-meta-v')?.textContent || '']));
const find = (k) => (rows.find((r) => r[0].includes(k)) || [])[1] || '';
ok(!!find('推演模型'), `状态行有「推演模型」 (${find('推演模型')})`);
ok(find('推演模型').includes(api.llm?.model || '@@'),
   `模型名与接口一致 (${api.llm?.model})`);
ok(!api.llm?.reasoning || find('推演模型').includes('推理型'),
   '推理型模型要标出来 (否则截断了没人知道)');
ok((st.last_story_error || '') ? find('没写出故事').length > 0 : find('没写出故事') === '',
   '有失败原因才显示那一行');

// ── 2. 补写入口: 没欠账就隐藏 ────────────────────────
const bfHidden = await page.$eval('#btn-chronicle-backfill', (b) => b.hidden);
ok(bfHidden === !st.last_story_error,
   `补写按钮显隐跟欠账走 (hidden=${bfHidden}, err=${!!st.last_story_error})`);

// ── 3. 最新一回带交手 / 所显 ────────────────────────
const adv = (api.adventures || [])[0] || {};
const g = adv.growth || {};
ok(!!adv.title, `最新一回有标题 (${adv.title})`);
ok(!!g.duel || g.tag === 'retire', `最新一回带交手 (${g.duel ? g.duel.result_cn : '退隐'})`);
const duelBlockShown = await page.$eval('#adventure-latest', (el) =>
  !!el.querySelector('.adv-duel'));
ok(duelBlockShown === !!g.duel, '交手面板按数据渲染');
const clock = await page.$eval('#story-clock', (el) => el.textContent || '');
ok(/岁/.test(clock) && /代/.test(clock), `故事时钟在 (${clock})`);
const meta = await page.$eval('#story-meta', (el) => el.textContent || '');
ok(meta.includes('已走 ' + (st.adventure_no || 0) + ' 回'),
   `已走回数与接口一致 (${st.adventure_no})`);
ok(meta.includes('一小时阅读 = 十天'), '节奏说明在');

// ── 4. 390px ─────────────────────────────────────────
await page.setViewportSize({ width: 390, height: 780 });
await page.waitForTimeout(400);
const overflow = await page.evaluate(() =>
  Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth));
ok(overflow === 0, `390px 无横向溢出 (${overflow}px)`);

ok(pageErrors.length === 0, `无 JS 报错 (${pageErrors.length}): ${pageErrors.slice(0, 2).join(' | ')}`);

await browser.close();
console.log(`\n${PASS} passed, ${FAIL} failed`);
process.exit(FAIL === 0 ? 0 : 1);
