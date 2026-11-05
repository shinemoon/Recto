import { chromium } from './_env.mjs';
import { REPO } from './_env.mjs';
import http from 'node:http';
import { execSync } from 'node:child_process';

const BASE = 'http://127.0.0.1:5015';
const cookie = execSync(
  `cd ${REPO} && python3 -c "import sys;sys.path.insert(0,'src');from recto.app import webauth;print(webauth.set_cookie('recto'))"`,
  { encoding: 'utf8' }
).trim();
const val = cookie.split(';')[0].split('=').slice(1).join('=');

const res = [];
const ok = (c, label, extra) => { res.push(!!c); console.log((c ? 'ok   ' : 'FAIL ') + label + (extra !== undefined ? '  ' + JSON.stringify(extra) : '')); };

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
await ctx.addCookies([{ name: 'zy_webauth', value: val, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));

await page.goto(BASE + '/', { waitUntil: 'networkidle' });

const nav = await page.$$eval('.rc-nav a', (as) => as.map((a) => a.textContent.trim()));
ok(!nav.includes('阅读增强'), '导航里没有「阅读增强」', nav);
ok(nav.includes('云端同步'), '「云端同步」仍在');

ok(await page.locator('section[data-pane="reader"]').count() === 0, '页面里没有 reader 面板');
ok(await page.locator('#sec-corpus').count() === 0, '语料卡已撤掉');

// 切到云端同步
await page.click('[data-pane-link="sync"]');
await page.waitForTimeout(200);
ok(await page.locator('section[data-pane="sync"]').isVisible(), '云端同步面板可见');
ok(await page.locator('section[data-pane="library"]').isVisible() === false, '书架面板已隐藏');

// 最近日志在云端同步里, 折叠状态, 点开有内容
const logInSync = await page.evaluate(() => {
  const sec = document.querySelector('section[data-pane="sync"]');
  const d = sec && sec.querySelector('#sec-log');
  return !!d;
});
ok(logInSync, '「最近日志」搬进了云端同步面板');
await page.click('#sec-log > summary');
await page.waitForTimeout(150);
ok(await page.locator('#sec-log').evaluate((d) => d.open), '日志折叠区可展开');
await page.click('#log-refresh');
await page.waitForTimeout(600);
const logTxt = await page.locator('#log').textContent();
ok(logTxt && !logTxt.includes('加载中'), '日志刷新按钮有效 (拿到内容)', String(logTxt).slice(0, 40));

// 书库按钮仍可用 (回执栏在 sync 面板里)
await page.click('[data-pane-link="library"]');
await page.waitForTimeout(150);
ok(await page.locator('#shelf').isVisible(), '切回书架仍正常');

// 移动端不溢出
const m = await browser.newContext({ viewport: { width: 390, height: 844 } });
await m.addCookies([{ name: 'zy_webauth', value: val, domain: '127.0.0.1', path: '/' }]);
const mp = await m.newPage();
mp.on('pageerror', (e) => errors.push('m:' + String(e)));
await mp.goto(BASE + '/#sync', { waitUntil: 'networkidle' });
const ov = await mp.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
ok(ov <= 0, '手机 390 无横向溢出', ov);
ok(await mp.locator('section[data-pane="sync"]').isVisible(), '手机 hash 直达云端同步')

ok(errors.length === 0, '全程 0 JS 报错', errors.slice(0, 3));
await browser.close();
const bad = res.filter((x) => !x).length;
console.log('\n' + (res.length - bad) + ' passed, ' + bad + ' failed');
process.exit(bad ? 1 : 0);
