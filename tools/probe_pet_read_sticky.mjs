/* 读本 —— 翻到"画出来的尽头"不许粘手 (真浏览器探针)。
 *
 * 背景: 段落是**边读边画**的 (只往后追加 DOM)。读者翻到"画出来的尽头"
 * 时, 如果不先补画就滚, 那一按会滚到旧底 (内容还没长出来) → 看起来
 * 像"这一按没反应", 得再按一次才动。
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

async function readPos() {
  return page.evaluate(async () => {
    const r = await fetch('/pet/api/reading', { credentials: 'same-origin' });
    const d = await r.json();
    return (d.pos || {}).idx || 0;
  });
}
async function writePos(idx) {
  return page.evaluate(async (n) => {
    await fetch('/pet/api/reading', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ idx: n, force: true, clamp: false }),
    });
  }, idx);
}

await page.goto(`${URL}/pet/`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#adv-read-body p', { timeout: 15000 });
await page.waitForTimeout(1300);

const snap = await readPos();
console.log(`  (快照位置: idx=${snap})`);

// 连按 25 次 —— 每按一次都该有位移 (不许出现"这一按没反应")
let sticky = 0;
let prev = await page.evaluate(() => document.querySelector('#adv-read-body').scrollTop);
for (let i = 0; i < 25; i++) {
  await page.keyboard.press('Space');
  await page.waitForTimeout(140);
  const now = await page.evaluate(() => document.querySelector('#adv-read-body').scrollTop);
  if (now - prev < 1) sticky++;
  prev = now;
}
ok(sticky === 0, `每按一次都有位移 (卡住 ${sticky} 次)`);

const end = await page.evaluate(() => {
  const b = document.querySelector('#adv-read-body');
  return {
    st: Math.round(b.scrollTop),
    max: Math.round(b.scrollHeight - b.clientHeight),
    drawn: window.READER ? window.READER.drawn : 0,
    total: window.READER ? window.READER.total : 0,
  };
});
ok(end.drawn < end.total, `还没读完 (画了 ${end.drawn} / ${end.total} 段)`);
ok(end.st <= end.max, `没滚过头 (${end.st} / ${end.max})`);

// 一路翻到尽头 (内容是**边读边画**的 —— 滚到底会补画, 所以要一直按)
let doneState = null;
for (let i = 0; i < 80; i++) {
  await page.keyboard.press('Space');
  await page.waitForTimeout(110);
  doneState = await page.evaluate(() => ({
    done: window.READER ? window.READER.done : false,
    drawn: window.READER ? window.READER.drawn : 0,
    total: window.READER ? window.READER.total : 0,
    doneHidden: document.querySelector('#adv-read-done')?.hidden,
    footHidden: document.querySelector('#adv-read-foot')?.hidden,
  }));
  if (doneState.done) break;
}
ok(doneState.done === true, `一路翻到尽头 (${doneState.drawn} / ${doneState.total} 段)`);
ok(doneState.doneHidden === false, '读到尽头 → 出现「走到尽头了」');
ok(doneState.footHidden === true, '读到尽头 → 收起「翻页」按钮');

await writePos(snap);
console.log(`  (位置已还原到 idx=${snap})`);

ok(pageErrors.length === 0, `无 JS 报错 (${pageErrors.length})`);

await browser.close();
console.log('');
console.log('='.repeat(46));
console.log(`PASS ${PASS}  FAIL ${FAIL}`);
process.exit(FAIL ? 1 : 0);
