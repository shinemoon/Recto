/* 阅读器: 字体候选清单能用滚轮滑 (面板不许"滚一下就消失") */
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
const { execFileSync } = require('child_process');

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || 'a6f63af571f05bb4';
const token = execFileSync('python3', [new URL('./make_cookie.py', import.meta.url).pathname],
  { encoding: 'utf8' }).trim();

const out = [];
const chk = (name, cond, extra = '') =>
  out.push(`${cond ? 'ok  ' : 'FAIL'} ${name}${extra ? ' — ' + extra : ''}`);

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
const errs = [];
page.on('pageerror', e => errs.push(String(e)));
page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
await page.addInitScript(() => {
  if (sessionStorage.getItem('probe.init')) return;
  sessionStorage.setItem('probe.init', '1');
  ['theme', 'fs', 'lh', 'para', 'pad', 'w', 'font', 'mode'].forEach(k => localStorage.removeItem('recto.' + k));
});
await page.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('.rv-page p', { timeout: 15000 });
await page.waitForTimeout(500);

const panelVisible = () => page.evaluate(() => !document.getElementById('rv-typo').hidden);
const menuVisible = () => page.evaluate(() => !document.getElementById('rv-font-menu').hidden);

/* 1. 打开面板 + 字体清单 */
await page.locator('#rv-typo-btn').click();
await page.waitForTimeout(180);
chk('排版面板已打开', await panelVisible());
await page.locator('#rv-font-open').click();
await page.waitForTimeout(200);
chk('字体候选清单已展开', await menuVisible());
const optCount = await page.locator('#rv-font-menu .rv-combo-opt').count();
chk('候选清单足够长 (能滚)', optCount > 20, `${optCount} 项`);

const menuBox = await page.locator('#rv-font-menu').boundingBox();
chk('清单有确定位置', !!menuBox,
  menuBox ? `${Math.round(menuBox.x)},${Math.round(menuBox.y)} ${Math.round(menuBox.width)}x${Math.round(menuBox.height)}` : '');

/* 2. 滚轮滑清单: 面板不消失、清单跟着滚 */
await page.mouse.move(menuBox.x + menuBox.width / 2, menuBox.y + menuBox.height / 2);
await page.mouse.wheel(0, 240);
await page.waitForTimeout(350);
const afterWheel = await page.evaluate(() => {
  const m = document.getElementById('rv-font-menu');
  return { top: Math.round(m.scrollTop), scrollable: m.scrollHeight > m.clientHeight + 2 };
});
chk('在清单上滚轮 → 面板仍在', await panelVisible());
chk('在清单上滚轮 → 清单仍展开', await menuVisible());
chk('清单内部真的滚了', afterWheel.scrollable && afterWheel.top > 0, `scrollTop=${afterWheel.top}`);

/* 3. 滚到底也不消失 */
await page.mouse.wheel(0, 4000);
await page.waitForTimeout(350);
chk('滚到清单底部 → 面板仍在', await panelVisible());
chk('滚到清单底部 → 清单仍展开', await menuVisible());

/* 4. 指针在面板空白处滚 (非清单) 也不消失 */
const rowBox = await page.locator('#rv-typo .rv-typo-row').first().boundingBox();
await page.mouse.move(rowBox.x + 30, rowBox.y + rowBox.height / 2);
const yBefore = await page.evaluate(() => Math.round(window.scrollY));
await page.mouse.wheel(0, 200);
await page.waitForTimeout(300);
chk('在面板其它行上滚轮 → 面板仍在', await panelVisible());

/* 5. 选一个候选: 清单收起, 面板留着 (继续调别的) */
await page.mouse.move(menuBox.x + menuBox.width / 2, menuBox.y + menuBox.height / 2);
await page.locator('#rv-font-menu .rv-combo-opt').nth(6).click();
await page.waitForTimeout(300);
const picked = await page.evaluate(() => ({
  menu: document.getElementById('rv-font-menu').hidden,
  panel: !document.getElementById('rv-typo').hidden,
  font: getComputedStyle(document.querySelector('.rv-page p')).fontFamily
}));
chk('选中后清单收起', picked.menu);
chk('选中后面板留着', picked.panel);
chk('选中后字体真生效', !!picked.font, picked.font.slice(0, 40));

/* 6. 合成 touchmove: 面板内不动, 面板外收起 (手机手感) */
const touchInside = await page.evaluate(() => {
  const menu = document.getElementById('rv-font-menu');
  menu.hidden = false;
  document.getElementById('rv-typo').hidden = false;
  menu.querySelector('.rv-combo-opt')?.dispatchEvent(
    new Event('touchmove', { bubbles: true, cancelable: true }));
  return !document.getElementById('rv-typo').hidden;
});
chk('面板内 touchmove → 不收起', touchInside);
const touchOutside = await page.evaluate(() => {
  document.body.dispatchEvent(new Event('touchmove', { bubbles: true, cancelable: true }));
  return document.getElementById('rv-typo').hidden;
});
chk('面板外 touchmove → 收起', touchOutside);

/* 7. 面板外真滚轮: 仍然收起 (原有行为不回归) */
await page.evaluate(() => { document.getElementById('rv-font-menu').hidden = true; });
await page.locator('#rv-typo-btn').click();
await page.waitForTimeout(160);
chk('重新打开面板', await panelVisible());
await page.mouse.move(640, 520);
await page.mouse.wheel(0, 900);
await page.waitForTimeout(400);
chk('页面正文滚轮 → 面板收起 (不回归)', !(await panelVisible()));

chk('无 JS 报错', errs.length === 0, errs.slice(0, 3).join(' | '));

/* 8. 手机视口: 清单照样能滚, 面板不消失 */
const mctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
await mctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const mp = await mctx.newPage();
const merrs = [];
mp.on('pageerror', e => merrs.push(String(e)));
await mp.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
await mp.waitForSelector('.rv-page p', { timeout: 15000 });
await mp.evaluate(() => document.getElementById('rv-typo-btn').click());
await mp.waitForTimeout(220);
await mp.evaluate(() => document.getElementById('rv-font-open').click());
await mp.waitForTimeout(260);
const mBox = await mp.locator('#rv-font-menu').boundingBox();
const mInfo = await mp.evaluate(() => {
  const p = document.getElementById('rv-typo').getBoundingClientRect();
  return { inView: p.left >= 0 && p.right <= window.innerWidth + 1,
           overflow: document.documentElement.scrollWidth > window.innerWidth + 1 };
});
chk('手机面板不出界', mInfo.inView && !mInfo.overflow);
await mp.mouse.move(mBox.x + mBox.width / 2, mBox.y + mBox.height / 2);
await mp.mouse.wheel(0, 200);
await mp.waitForTimeout(320);
const mAfter = await mp.evaluate(() => ({
  panel: !document.getElementById('rv-typo').hidden,
  top: Math.round(document.getElementById('rv-font-menu').scrollTop)
}));
chk('手机: 清单上滚轮 → 面板仍在', mAfter.panel, `scrollTop=${mAfter.top}`);
chk('手机无 JS 报错', merrs.length === 0, merrs.slice(0, 2).join(' | '));

console.log(out.join('\n'));
const fail = out.filter(l => l.startsWith('FAIL')).length;
console.log(`\n${out.length - fail}/${out.length} PASS`);
await browser.close();
process.exit(fail ? 1 : 0);
