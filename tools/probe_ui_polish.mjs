import { chromium } from './_env.mjs';
import { REPO } from './_env.mjs';
import { execSync } from 'node:child_process';
import { skinSnapshot, restoreSkin } from './_skins.mjs';

const base = 'http://127.0.0.1:5015';
const cookieVal = execSync(
  'cd ' + REPO + ' && PYTHONPATH=src python3 -c "from recto.app import webauth;'
  + 'print(webauth.set_cookie(\'recto\').split(\';\')[0].split(\'=\',1)[1])"'
).toString().trim();

const out = {};
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1400, height: 1000 } });
await ctx.addCookies([{ name: 'zy_webauth', value: cookieVal, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
const errs = [];
page.on('pageerror', e => errs.push(String(e)));

await page.goto(base + '/', { waitUntil: 'networkidle' });
// 桌面端页头已收成悬浮操作轨, 行本身 0 高 —— 等挂载而非可见
await page.waitForSelector('.rc-head-row', { state: 'attached' });
out.headRow = await page.evaluate(() => {
  const r = document.querySelector('.rc-head-row');
  const cs = getComputedStyle(r);
  const top = document.querySelector('.rc-top');
  const rr = r.getBoundingClientRect();
  return {
    padding: cs.padding,
    margin: cs.margin,
    gapBelowTop: Math.round(rr.top - top.getBoundingClientRect().bottom),
    h1: (document.querySelector('.rc-head h1') || {}).textContent,
    sub: (document.querySelector('.rc-head .rc-sub') || {}).textContent,
  };
});

await page.goto(base + '/corpus/', { waitUntil: 'networkidle' });
await page.waitForTimeout(700);
out.corpusButtons = await page.evaluate(() => {
  const rows = [...document.querySelectorAll('.row')]
    .filter(r => r.querySelectorAll(':scope > button').length >= 2);
  return rows.slice(0, 6).map(r => [...r.querySelectorAll(':scope > button')].map(b => {
    const cs = getComputedStyle(b);
    return { id: b.id || b.textContent.trim().slice(0, 8), bg: cs.backgroundColor };
  }));
});

const snap = await skinSnapshot();
out.dark = await page.evaluate(async () => {
  // 切到深色皮肤看错落后是否还有区分度
  const r = await fetch('/api/appearance', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ skin: 'material-dark' }) });
  return r.ok;
});
await page.goto(base + '/corpus/', { waitUntil: 'networkidle' });
await page.waitForTimeout(600);
out.darkButtons = await page.evaluate(() => {
  const b = [...document.querySelectorAll('.row > button')].slice(0, 5)
    .map(x => getComputedStyle(x).backgroundColor);
  return { pageBg: getComputedStyle(document.body).backgroundColor, buttons: b };
});
// 切回用户原来的外观 (不是硬编码 material —— 会顶掉用户自己的选择)
await restoreSkin(snap);

out.errs = errs.slice(0, 5);
console.log(JSON.stringify(out, null, 2));
await browser.close();
