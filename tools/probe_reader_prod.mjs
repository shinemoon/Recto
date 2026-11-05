import { createRequire } from 'module';
const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
import fs from 'fs';

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || '9f81d4e6264e8de3';
let COOKIE = process.env.RV_COOKIE || '';
try { COOKIE = COOKIE || fs.readFileSync('/tmp/rv_cookie.txt', 'utf8').trim(); } catch (e) {}

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
if (COOKIE) await ctx.addCookies([{ name: 'zy_webauth', value: COOKIE, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', e => errors.push(String(e)));
page.on('response', r => { if (r.status() >= 400) errors.push('HTTP ' + r.status() + ' ' + r.url()); });

await page.goto(BASE + '/read', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(1200);
console.log('shelf cards', await page.locator('.rs-card').count(),
            '| nav', await page.locator('.rc-nav a[aria-current="page"]').first().textContent());

await page.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(1500);
console.log('blocks', await page.locator('#rv-content .rv-blk').count(),
            '| pct', await page.locator('#rv-pct').textContent());

let pcts = [];
for (let i = 0; i < 4; i++) {
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await page.waitForTimeout(2500);
  pcts.push(await page.locator('#rv-pct').textContent());
}
console.log('scroll pcts', pcts.join(' -> '),
            '| blocks now', await page.locator('#rv-content .rv-blk').count());

await page.goto(BASE + '/read', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(900);
const first = await page.locator('.rs-card').first().innerText();
console.log('shelf first', first.replace(/\n/g, ' / ').slice(0, 80));

await page.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(1400);
console.log('reopen pct', await page.locator('#rv-pct').textContent());

// 书库分区卡片
await page.goto(BASE + '/#library', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(1500);
console.log('library card', JSON.stringify((await page.locator('#rd-note').innerText()).slice(0, 80)),
            '| continue visible', await page.locator('#rd-continue').isVisible().catch(() => false));

console.log('errors', errors.slice(0, 5));
await browser.close();
