/* 读本 —— 「从头读」复位 (真浏览器探针)。 */
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

async function readPos(page) {
  return page.evaluate(async () => {
    const r = await fetch('/pet/api/reading', { credentials: 'same-origin' });
    const d = await r.json();
    return (d.pos || {}).idx || 0;
  });
}
async function writePos(page, idx) {
  return page.evaluate(async (n) => {
    await fetch('/pet/api/reading', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ idx: n, force: true, clamp: false }),
    });
  }, idx);
}

const page = await ctx.newPage();
const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(String(e)));
page.on('console', (m) => { if (m.type() === 'error') pageErrors.push(m.text()); });

await page.goto(`${URL}/pet/`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#adv-read-body p', { timeout: 15000 });
await page.waitForTimeout(1300);

const snap = await readPos(page);
console.log(`  (快照位置: idx=${snap})`);

// 先往前读几屏
for (let i = 0; i < 3; i++) {
  await page.keyboard.press('Space');
  await page.waitForTimeout(220);
}
await page.waitForTimeout(900);
const posMoved = await readPos(page);
ok(posMoved > snap, `往前翻了, 位置前进 (${snap} → ${posMoved})`);

// 「从头读」→ 位置回到 0, 窗口回到顶部
await page.click('#btn-adv-restart');
await page.waitForTimeout(900);
const afterReset = await page.evaluate(() => {
  const b = document.querySelector('#adv-read-body');
  return {
    st: Math.round(b.scrollTop),
    idx: window.READER ? window.READER.idx : 0,
    prog: document.querySelector('#adv-read-prog')?.textContent?.trim() || '',
    first: b.querySelector('p') ? b.querySelector('p').textContent.slice(0, 20) : '',
  };
});
ok(afterReset.st === 0, `「从头读」把窗口拉回顶部 (scrollTop=${afterReset.st})`);
const posReset = await readPos(page);
ok(posReset === 0, `「从头读」把位置归零 (idx=${posReset})`);
ok(/^0 \/ /.test(afterReset.prog), `进度显示归零 (${afterReset.prog})`);

// 复位后按空格 → 从头往下读
await page.keyboard.press('Space');
await page.waitForTimeout(900);
const posAfter = await readPos(page);
ok(posAfter > 0, `复位后空格能往下读 (idx=${posAfter})`);

await writePos(page, snap);
console.log(`  (位置已还原到 idx=${snap})`);

ok(pageErrors.length === 0, `无 JS 报错 (${pageErrors.length}) ${pageErrors.slice(0, 2).join(' | ')}`);

await browser.close();
console.log('');
console.log('='.repeat(46));
console.log(`PASS ${PASS}  FAIL ${FAIL}`);
process.exit(FAIL ? 1 : 0);
