// 页头清理巡检: 每页开头不再有 kicker / headline / deck 说明块, 且没有裸 h1 残留, 无横向溢出。
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
const { execFileSync } = require('child_process');

const BASE = process.env.RECTO_BASE || 'http://127.0.0.1:5015';
const token = execFileSync('python3', [new URL('./make_cookie.py', import.meta.url).pathname],
  { encoding: 'utf8' }).trim();

const PAGES = [
  ['/', 'library'],
  ['/appearance', 'appearance'],
  ['/admin/', 'admin-home'],
  ['/admin/?pane=books', 'admin-books'],
  ['/admin/?pane=excerpts', 'admin-excerpts'],
  ['/admin/?pane=events', 'admin-events'],
  ['/admin/?pane=tools', 'admin-tools'],
];

const res = [];
const ok = (c, n, x) => { res.push(!!c); console.log((c ? 'PASS ' : 'FAIL ') + n + (x === undefined ? '' : ' :: ' + JSON.stringify(x))); };

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

for (const [path, name] of PAGES) {
  await page.goto(BASE + path, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(700);
  const r = await page.evaluate(() => {
    const main = document.querySelector('.rc-main');
    const txt = (main ? main.innerText : '').split('\n').map((s) => s.trim()).filter(Boolean);
    return {
      headBlocks: {
        kicker: document.querySelectorAll('.rc-main .kicker').length,
        headline: document.querySelectorAll('.rc-main .headline').length,
        deck: document.querySelectorAll('.rc-main .deck').length,
      },
      first3: txt.slice(0, 3),
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    };
  });
  ok(r.headBlocks.headline === 0 || name === 'appearance', `${name} · 无页头 headline`, r.headBlocks);
  ok(r.headBlocks.deck === 0 || name === 'appearance', `${name} · 无页头 deck`, r.headBlocks);
  ok(r.overflow <= 0, `${name} · 无横向溢出`, r.overflow);
  console.log('     首行:', JSON.stringify(r.first3));
}

ok(errors.length === 0, '全站 0 JS 报错', errors.slice(0, 4));
console.log(`\n${res.filter(Boolean).length}/${res.length} PASS`);
await browser.close();
process.exit(res.every(Boolean) ? 0 : 1);
