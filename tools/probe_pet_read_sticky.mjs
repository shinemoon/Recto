/* 读本 —— 连按空格不卡手 (真浏览器探针)。
 *
 * 跑法: cd tools && node probe_pet_read_sticky.mjs
 * 环境: RECTO_BASE (默认 5015)
 *
 * 验的事:
 *   · 连按空格, **在还有下文的时候**每一次都有位移 (不许"这一按没反应")
 *   · 一路翻到尽头 → 出现「走到尽头了」+ 收起「翻页」按钮
 *   · 位置只前进
 *
 * 2026-11-21: 前台改成显示**最新的一回** —— 那一回只有十几段, 按几下
 * 就到底了。所以"卡住"的判定要**只看还没到底的那几次**; 到底之后
 * 按空格当然不动 (那是正确的行为, 不是卡手)。
 *
 * 卫生: 位置会写后端 —— 收尾**还原**到跑之前那个值。
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

async function readPos() {
  return page.evaluate(async () => {
    const r = await fetch('/pet/api/reading', { credentials: 'same-origin' });
    const d = await r.json();
    const p = d.pos || {};
    return { round: p.round || 0, idx: p.idx || 0 };
  });
}
async function writePos(v) {
  return page.evaluate(async (p) => {
    await fetch('/pet/api/reading', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ round: p.round, idx: p.idx, force: true, clamp: false }),
    });
  }, v);
}

const page = await ctx.newPage();
const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(String(e)));
page.on('console', (m) => { if (m.type() === 'error') pageErrors.push(m.text()); });

await page.goto(`${URL}/pet/`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#adv-read-body p', { timeout: 15000 });
await page.waitForTimeout(1300);

const snap = await readPos();
console.log(`  (快照位置: round=${snap.round} idx=${snap.idx})`);

// 连按 25 次 —— **还有下文的时候**每按一次都该有位移
let sticky = 0, presses = 0, last = null;
let prev = await page.evaluate(() => document.querySelector('#adv-read-body').scrollTop);
for (let i = 0; i < 25; i++) {
  const st = await page.evaluate(() => {
    const b = document.querySelector('#adv-read-body');
    const R = window.READER || {};
    return {
      st: b.scrollTop,
      max: Math.max(0, b.scrollHeight - b.clientHeight),
      done: !!R.done,
      drawn: R.drawn || 0,
      total: R.total || 0,
    };
  });
  if (st.done) { last = st; break; }
  await page.keyboard.press('Space');
  await page.waitForTimeout(150);
  const now = await page.evaluate(() => document.querySelector('#adv-read-body').scrollTop);
  presses++;
  if (now - prev < 1) sticky++;
  prev = now;
  last = await page.evaluate(() => {
    const b = document.querySelector('#adv-read-body');
    const R = window.READER || {};
    return {
      st: Math.round(b.scrollTop),
      max: Math.round(b.scrollHeight - b.clientHeight),
      drawn: R.drawn || 0,
      total: R.total || 0,
      done: !!R.done,
    };
  });
}
ok(sticky === 0, `每次按都有位移 (卡住 ${sticky} 次 / 按了 ${presses} 次)`);
ok(last.st <= last.max, `没滚过头 (${last.st} / ${last.max})`);

// 一路翻到尽头 (内容是**边读边画**的 —— 滚到底会补画, 所以要一直按)
let doneState = null;
for (let i = 0; i < 120; i++) {
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
console.log(`  (位置已还原到 round=${snap.round} idx=${snap.idx})`);

ok(pageErrors.length === 0, `无 JS 报错 (${pageErrors.length})`);

await browser.close();
console.log('');
console.log('='.repeat(46));
console.log(`PASS ${PASS}  FAIL ${FAIL}`);
process.exit(FAIL ? 1 : 0);
