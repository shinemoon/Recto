/* 书宠分区 —— 截图 (给人看)。 */
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RECTO_BASE || '5015';
const URL = `http://127.0.0.1:${BASE}`;
const OUT = process.env.PET_SHOT_DIR || '/tmp';

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 1000 }, deviceScaleFactor: 1 });
await ctx.addCookies([{ name: 'zy_webauth', value: cookie(), domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
await page.goto(`${URL}/pet/`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(700);
await page.screenshot({ path: `${OUT}/pet-desktop.png`, fullPage: true });

await page.setViewportSize({ width: 390, height: 780 });
await page.waitForTimeout(300);
await page.screenshot({ path: `${OUT}/pet-mobile.png`, fullPage: true });

console.log('saved', `${OUT}/pet-desktop.png`, `${OUT}/pet-mobile.png`);
await browser.close();
