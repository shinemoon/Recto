// 校验 /settings 的云端同步卡: 无 pageerror, 且 tick 状态行提到"收尾补推"
import { chromium, cookie, REPO } from '/vol2/1001/Recto/tools/_env.mjs';

const BASE = process.env.RECTO_BASE || 'http://127.0.0.1:5015';
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
await ctx.addCookies([{ name: 'zy_webauth', value: await cookie(), domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
const errs = [];
page.on('pageerror', (e) => errs.push(String(e)));
await page.goto(BASE + '/settings', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(1200);
const st = await page.evaluate(() => {
  const el = document.querySelector('#sy-tick-state');
  return { text: el ? el.textContent : null };
});
console.log('tick-state:', JSON.stringify(st));
console.log('containing 收尾补推:', st.text && st.text.includes('收尾补推'));
console.log('pageerrors:', errs.length ? errs : 'none');
await browser.close();
