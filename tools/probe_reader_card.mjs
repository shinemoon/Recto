import { createRequire } from 'module';
const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
import fs from 'fs';

const BASE = 'http://127.0.0.1:5015';
const COOKIE = fs.readFileSync('/tmp/rv_cookie.txt', 'utf8').trim();
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
await ctx.addCookies([{ name: 'zy_webauth', value: COOKIE, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', e => errors.push(String(e)));
await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(1800);
const out = await page.evaluate(() => {
  const n = document.getElementById('rd-note');
  const c = document.getElementById('rd-continue');
  const sec = document.getElementById('sec-read');
  return {
    text: n ? n.textContent : null,
    pill: (document.getElementById('rd-pill') || {}).textContent,
    contHidden: c ? c.hidden : null,
    contHref: c ? c.getAttribute('href') : null,
    secOpen: sec ? sec.open : null,
    secPane: sec ? sec.dataset.pane : null,
    libraryVisible: !!(document.querySelector('[data-pane="library"].is-on'))
  };
});
console.log(JSON.stringify(out, null, 1));
console.log('errors', errors.slice(0, 4));
await browser.close();
