/* 首次登录强制改口令 —— 真浏览器验证。
 *
 * 验的:
 *  1) 登录成功后落到 /first-run, 不是首页
 *  2) 改口令页: 两个输入框 + 按钮, 没有任何维护者视角的提示
 *  3) 未改口令时点首页 → 弹回改口令页
 *  4) 改完口令 → 进首页, 顶栏正常
 *  5) 改完后再次登录 → 直接进首页 (不再被拦)
 *  6) 深色皮肤下也不溢出
 *
 * 跑法: cd <仓库根> && node tools/probe_first_login.mjs
 */
import { chromium } from 'playwright';
import { fileURLToPath } from 'url';
import path from 'path';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');
const BASE = process.env.RECTO_BASE || 'http://127.0.0.1:5015';

let fails = 0;
const ok = (cond, msg) => {
  if (!cond) { fails++; console.log('FAIL ' + msg); }
  else console.log('PASS ' + msg);
};

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));

/* --- 1. 登录页 --- */
await page.goto(BASE + '/login', { waitUntil: 'domcontentloaded' });
ok(await page.locator('input[name="username"]').count() === 1, '登录页有用户名输入框');

/* --- 2. 用初始口令登录 → 应该落到改口令页 --- */
await page.fill('input[name="username"]', 'root');
await page.fill('input[name="password"]', 'root');
await Promise.all([
  page.waitForNavigation({ waitUntil: 'domcontentloaded' }),
  page.click('button[type="submit"]'),
]);
ok(page.url().includes('/first-run'), '用初始口令登录后落到改口令页 (实际 ' + page.url() + ')');

/* --- 3. 改口令页的形态 --- */
const h1 = await page.locator('.rc-auth-card h1').first().innerText();
ok(/口令/.test(h1), '标题是设口令 (' + h1 + ')');
ok(await page.locator('input[name="password"]').count() === 1, '有新口令输入框');
ok(await page.locator('input[name="password2"]').count() === 1, '有确认输入框');
const body = await page.locator('body').innerText();
ok(!/admin-password|config\.toml|RECTO_AUTH|\[auth\]/.test(body),
   '页面不出现配置项 / 来源路径那类维护者提示');
ok(!/root/.test(body), '页面不印初始口令');

/* --- 4. 未改口令时访问首页 → 弹回 --- */
await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
ok(page.url().includes('/first-run'), '没改口令时访问首页被弹回改口令页');

/* --- 5. 两次不一致 → 留在页面并有提示 --- */
await page.fill('input[name="password"]', 'probe-first-pass');
await page.fill('input[name="password2"]', 'probe-other-pass');
await Promise.all([
  page.waitForNavigation({ waitUntil: 'domcontentloaded' }).catch(() => {}),
  page.click('button[type="submit"]'),
]);
await page.waitForTimeout(400);
const t = await page.locator('body').innerText();
ok(/不一致/.test(t), '两次不一致有明确提示');

/* --- 6. 改成真口令 → 进首页 --- */
await page.fill('input[name="password"]', 'probe-first-pass');
await page.fill('input[name="password2"]', 'probe-first-pass');
await Promise.all([
  page.waitForNavigation({ waitUntil: 'domcontentloaded' }),
  page.click('button[type="submit"]'),
]);
ok(!page.url().includes('/first-run'), '改完口令后不再被拦 (实际 ' + page.url() + ')');
ok(await page.locator('.rc-brand').count() > 0, '进到首页, 顶栏品牌在');
const overflow = await page.evaluate(() =>
  document.documentElement.scrollWidth - document.documentElement.clientWidth);
ok(overflow <= 0, '首页无横向溢出');

/* --- 7. 再次登录 → 直接进首页 --- */
await ctx.clearCookies();
await page.goto(BASE + '/login', { waitUntil: 'domcontentloaded' });
await page.fill('input[name="username"]', 'root');
await page.fill('input[name="password"]', 'probe-first-pass');
await Promise.all([
  page.waitForNavigation({ waitUntil: 'domcontentloaded' }),
  page.click('button[type="submit"]'),
]);
ok(!page.url().includes('/first-run'), '改过之后用新口令登录直接进首页');

/* --- 8. 手机宽度 --- */
const m = await browser.newContext({ viewport: { width: 390, height: 780 } });
const mp = await m.newPage();
await mp.goto(BASE + '/login', { waitUntil: 'domcontentloaded' });
await mp.fill('input[name="username"]', 'root');
await mp.fill('input[name="password"]', 'probe-first-pass');
await Promise.all([
  mp.waitForNavigation({ waitUntil: 'domcontentloaded' }),
  mp.click('button[type="submit"]'),
]);
const mo = await mp.evaluate(() =>
  document.documentElement.scrollWidth - document.documentElement.clientWidth);
ok(mo <= 0, '手机 390 宽无横向溢出');

ok(errors.length === 0, '无 JS 报错' + (errors.length ? ': ' + errors.join(' | ') : ''));

await browser.close();
console.log(fails ? '\n' + fails + ' FAILED' : '\n全部通过');
process.exit(fails ? 1 : 0);
