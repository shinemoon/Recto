// 书库 / 云端同步 面板归属检查 (2026-11-04)
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
const { execFileSync } = require('child_process');

const BASE = process.env.RECTO_BASE || 'http://127.0.0.1:5015';
const token = execFileSync('python3', [new URL('./make_cookie.py', import.meta.url).pathname],
  { encoding: 'utf8' }).trim();

let fail = 0;
const ok = (c, n, x) => { if (!c) fail++; console.log((c ? 'PASS ' : 'FAIL ') + n + (x === undefined ? '' : ' :: ' + JSON.stringify(x))); };

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));

const vis = (id) => page.evaluate((i) => {
  const e = document.getElementById(i);
  if (!e) return 'missing';
  const r = e.getBoundingClientRect();
  return r.width > 0 && r.height > 0 ? 'visible' : 'hidden';
}, id);

await page.goto(BASE + '/#library', { waitUntil: 'networkidle' });
for (const id of ['sec-cloud', 'sec-local', 'sec-intake', 'sec-auth']) {
  const v = await vis(id);
  ok(v !== 'visible', '书库里不显示 ' + id, v);
}
ok((await vis('shelf')) === 'visible', '书库显示书架网格');
ok((await vis('shelf-rescan')) === 'visible', '书库仍有两个悬浮圆钮');

await page.click('a[data-pane-link="sync"], .rc-nav a[href="/#sync"]');
await page.waitForTimeout(600);
for (const id of ['sec-cloud', 'sec-auth', 'sec-links', 'sec-out']) {
  ok((await vis(id)) !== 'missing', '云端同步里有 ' + id, await vis(id));
}
ok(await page.evaluate(() => !!document.getElementById('cloud-pill')
  && !!document.getElementById('auth-pill')), '云端同步里两个 pill 都在');
ok(await page.evaluate(() => document.querySelectorAll('[data-pane="sync"] details.sec').length) >= 4,
  '云端同步有多个折叠区');

// 展开云书 → 表格能拉出数据
await page.click('details#sec-cloud > summary');
await page.waitForTimeout(1200);
const rows = await page.evaluate(() => document.querySelectorAll('#cloud-body tr').length);
ok(rows >= 1, '云书表有行', rows);

const over = await page.evaluate(() => ({
  s: document.documentElement.scrollWidth, c: document.documentElement.clientWidth }));
ok(over.s <= over.c + 1, '无横向溢出', over);
ok(errors.length === 0, '无 JS 报错', errors);

// 手机口径
const m = await browser.newContext({ viewport: { width: 390, height: 780 } });
await m.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const mp = await m.newPage();
await mp.goto(BASE + '/#sync', { waitUntil: 'networkidle' });
const mo = await mp.evaluate(() => ({
  s: document.documentElement.scrollWidth, c: document.documentElement.clientWidth }));
ok(mo.s <= mo.c + 1, '手机无横向溢出', mo);

await browser.close();
console.log(fail ? `\n${fail} FAILED` : '\nall ok');
process.exit(fail ? 1 : 0);
