/** 语料服务嵌进 Recto 后的冒烟: 同壳 / 同皮 / API 走通 / 无 JS 错误。 */
import { chromium } from './_env.mjs';
import { execSync } from 'node:child_process';

const BASE = process.env.RECTO_BASE || 'http://127.0.0.1:5017';
const cookieLine = execSync('python3 /tmp/mkcookie.py', { encoding: 'utf8' }).trim();
const value = cookieLine.split('=')[1];

const b = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await b.newContext({ viewport: { width: 1280, height: 900 } });
await ctx.addCookies([{ name: 'zy_webauth', value, domain: '127.0.0.1', path: '/' }]);
const p = await ctx.newPage();
const errs = [];
p.on('pageerror', e => errs.push(String(e)));
p.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });

const out = {};
await p.goto(BASE + '/corpus/', { waitUntil: 'networkidle' });
await p.waitForTimeout(1200);

out.url = p.url();
out.navItems = await p.$$eval('.rc-nav a', as => as.map(a => a.textContent.trim()));
out.active = await p.$eval('.rc-nav a[aria-current="page"]', a => a.textContent.trim()).catch(() => null);
out.hasBrand = await p.$$eval('.rc-brand', e => e.length);
out.oldMastheadHidden = await p.$eval('.masthead', e => getComputedStyle(e).display).catch(() => 'absent');
out.bodyBg = await p.evaluate(() => getComputedStyle(document.body).backgroundColor);
out.cssVars = await p.evaluate(() => {
  const s = getComputedStyle(document.documentElement);
  return { bg: s.getPropertyValue('--rc-bg').trim(), upBg: s.getPropertyValue('--bg').trim() };
});
// 上游列表是否真的加载了（走 /corpus/api/books）
out.booksLoaded = await p.evaluate(async () => {
  const r = await fetch('/corpus/api/books');
  return { status: r.status, hasBooks: 'books' in (await r.json()) };
});
out.mobileOverflow = await p.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
out.errors = errs;

for (const [k, v] of Object.entries(out)) console.log(k.padEnd(20), JSON.stringify(v));
await b.close();
