/* 读本 —— MUD 式窗口的截图 (给人眼看的)。 */
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RECTO_BASE || '5015';
const URL = `http://127.0.0.1:${BASE}`;

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
await ctx.addCookies([{
  name: 'zy_webauth', value: cookie(), domain: '127.0.0.1', path: '/',
}]);

const page = await ctx.newPage();
await page.goto(`${URL}/pet/`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#adv-read-body p', { timeout: 15000 });
await page.waitForTimeout(1400);

await page.locator('#adv-read').scrollIntoViewIfNeeded();
await page.waitForTimeout(400);
await page.screenshot({ path: '/tmp/shot_mudread_1.png' });

// 翻两屏
await page.keyboard.press('Space');
await page.waitForTimeout(250);
await page.keyboard.press('Space');
await page.waitForTimeout(500);
await page.screenshot({ path: '/tmp/shot_mudread_2.png' });

console.log('ok');
await browser.close();
