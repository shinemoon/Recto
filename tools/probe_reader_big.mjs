import { createRequire } from 'module';
const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
import fs from 'fs';

const BASE = 'http://127.0.0.1:5015';
const COOKIE = fs.readFileSync('/tmp/rv_cookie.txt', 'utf8').trim();
const keys = process.env.RV_KEYS || '8d33cb707e91556a';   // 示例长篇

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
await ctx.addCookies([{ name: 'zy_webauth', value: COOKIE, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', e => errors.push(String(e)));
page.on('response', r => { if (r.status() >= 400) errors.push('HTTP ' + r.status() + ' ' + r.url()); });

for (const key of keys.split(',')) {
  const t0 = Date.now();
  await page.goto(`${BASE}/read/b/${key}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2500);
  const info = await page.evaluate(() => ({
    title: (document.getElementById('rv-title') || {}).textContent,
    sub: (document.getElementById('rv-sub') || {}).textContent,
    blocks: document.querySelectorAll('#rv-content .rv-blk').length,
    pct: (document.getElementById('rv-pct') || {}).textContent,
    toc: document.querySelectorAll('#rv-toc-list a').length
  }));
  console.log(key, (Date.now() - t0) + 'ms', JSON.stringify(info));

  // 划线 + 笔记全链路
  const info2 = await page.evaluate(() => {
    const b = document.querySelectorAll('#rv-content .rv-blk')[3];
    if (!b) return null;
    const w = document.createTreeWalker(b, NodeFilter.SHOW_TEXT);
    const n = w.nextNode();
    if (!n) return null;
    const r = document.createRange();
    r.setStart(n, 0); r.setEnd(n, Math.min(10, n.nodeValue.length));
    const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
    return { text: r.toString(), off: b.dataset.off };
  });
  await page.waitForTimeout(500);
  if (info2) {
    const bar = await page.locator('#rv-sel').isVisible();
    if (bar) await page.click('[data-sel="note"]');
    await page.waitForTimeout(300);
    await page.fill('#rv-noteedit-t', '生产环境测试批注');
    await page.click('[data-ne="save"]');
    await page.waitForTimeout(800);
    const hl = await page.evaluate(() => {
      let n = 0;
      if (window.CSS && CSS.highlights) {
        ['yellow', 'green', 'blue', 'pink'].forEach(c => {
          const h = CSS.highlights.get('rv-hl-' + c);
          if (h) n += Array.from(h).length;
        });
      }
      return n;
    });
    console.log('  selection', JSON.stringify(info2), '| bar', bar, '| highlight ranges', hl);
  }

  // 目录跳到中间
  const total = await page.locator('#rv-toc-list a').count();
  if (total > 4) {
    await page.click('[data-act="toc"]');
    await page.locator('#rv-toc-list a').nth(Math.floor(total / 2)).click();
    await page.waitForTimeout(2200);
    const mid = await page.evaluate(() => ({
      pct: (document.getElementById('rv-pct') || {}).textContent,
      title: (document.querySelector('#rv-content .rv-ch-title') || {}).textContent
    }));
    console.log('  mid', JSON.stringify(mid));
  }
}

// 手机端
const mob = await ctx.newPage();
await mob.setViewportSize({ width: 390, height: 844 });
await mob.goto(`${BASE}/read/b/${keys.split(',')[0]}`, { waitUntil: 'domcontentloaded' });
await mob.waitForTimeout(1800);
console.log('mobile overflow', await mob.evaluate(() => document.documentElement.scrollWidth - window.innerWidth));

console.log('errors', errors.slice(0, 6));
await browser.close();
