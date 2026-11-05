// 设置页「修改口令」真浏览器验证: 登录 → 打开 /settings → 用旧口令改新口令
// 跑法: NODE_PATH=... node tools/probe_settings_password.mjs
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { chromium } = require('playwright');

const BASE = process.env.RECTO_BASE || 'http://127.0.0.1:5015';
const OLD = process.env.PW_OLD || 'root';
const NEW = process.env.PW_NEW || 'uitest123';

const browser = await chromium.launch();
const page = await browser.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));

await page.goto(BASE + '/login', { waitUntil: 'domcontentloaded' });
await page.fill('input[name=username]', 'root');
await page.fill('input[name=password]', OLD);
await Promise.all([page.waitForLoadState('domcontentloaded'), page.click('button[type=submit]')]);
console.log('登录后 URL:', page.url());

await page.goto(BASE + '/settings', { waitUntil: 'domcontentloaded' });
await page.click('summary:has-text("修改口令")');
await page.fill('#st-old', OLD);
await page.fill('#st-new', NEW);
await page.fill('#st-new2', NEW);
await page.click('[data-st=pass]');
await page.waitForFunction(() => {
  const e = document.getElementById('st-pass-msg');
  return e && e.textContent.trim().length > 0;
}, { timeout: 8000 });
const msg = await page.textContent('#st-pass-msg');
const cls = await page.getAttribute('#st-pass-msg', 'class');
console.log('提示:', JSON.stringify(msg), '| class:', cls);

// 清空后再用错旧口令试一次, 确认仍被拒 (提示要能指向重置办法)
await page.fill('#st-old', 'definitely-wrong');
await page.fill('#st-new', 'another123');
await page.fill('#st-new2', 'another123');
await page.click('[data-st=pass]');
await page.waitForFunction(() => {
  const e = document.getElementById('st-pass-msg');
  return e && e.className.includes('err');
}, { timeout: 8000 });
console.log('错口令提示:', JSON.stringify(await page.textContent('#st-pass-msg')));

console.log('pageerror count:', errors.length, errors.slice(0, 2));
await browser.close();
process.exit(cls.includes('ok') ? 0 : 1);
