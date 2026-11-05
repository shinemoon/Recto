/* 截图: 阅读器页内排版 (滚动 + 左右翻页) */
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
const { execFileSync } = require('child_process');

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || 'a6f63af571f05bb4';
const CH = process.env.RV_CH || '7';
const OUT = process.env.RV_OUT || '/tmp/reader-layout';
const token = execFileSync('python3', [new URL('./make_cookie.py', import.meta.url).pathname],
  { encoding: 'utf8' }).trim();

const browser = await chromium.launch({ args: ['--no-sandbox'] });
for (const [name, w, h, mode] of [['desk', 1280, 900, 'scroll'],
                                  ['desk-paged', 1280, 900, 'page'],
                                  ['phone', 390, 780, 'scroll']]) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h } });
  await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
  const page = await ctx.newPage();
  await page.addInitScript(m => {
    localStorage.removeItem('recto.indent');
    localStorage.setItem('recto.mode', m);
  }, mode);
  await page.goto(`${BASE}/read/b/${KEY}#c${CH}`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.rv-page p', { timeout: 20000 });
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${OUT}-${name}.png` });
  console.log('saved', `${OUT}-${name}.png`);
  await ctx.close();
}
await browser.close();
