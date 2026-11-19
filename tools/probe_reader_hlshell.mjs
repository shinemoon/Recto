/* 验 auto 主题 + 深色外壳 → 线色应为浅灰 (服务端 data-shell 标记那一条路) */
import { chromium } from './_env.mjs';
import { execFileSync } from 'child_process';

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || '8379037298fbca9c';

const token = execFileSync('python3', [new URL('./make_cookie.py', import.meta.url).pathname],
  { encoding: 'utf8' }).trim();

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
await page.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('.rv-page p', { timeout: 30000 });
await page.waitForTimeout(800);

// 模拟服务端把外壳皮肤标成 dark (正常是 server 渲染时写好的)
const r1 = await page.evaluate(() => {
  const rv = document.getElementById('rv');
  rv.setAttribute('data-theme', 'auto');
  rv.setAttribute('data-shell', 'dark');
  return getComputedStyle(rv).getPropertyValue('--rv-hl').trim();
});
console.log('auto + shell=dark →', r1);

await page.evaluate(() => document.getElementById('rv').setAttribute('data-shell', 'light'));
const r2 = await page.evaluate(() => {
  const rv = document.getElementById('rv');
  rv.setAttribute('data-theme', 'auto');
  rv.setAttribute('data-shell', 'light');
  return getComputedStyle(rv).getPropertyValue('--rv-hl').trim();
});
console.log('auto + shell=light →', r2);

// 服务端实际渲染出来的标记
const served = await page.evaluate(() => document.getElementById('rv').getAttribute('data-shell'));
console.log('served data-shell =', served);

await browser.close();
console.log(r1 === '#c9ccd4' && r2 === '#1d4e89' ? 'PASS' : 'FAIL');
