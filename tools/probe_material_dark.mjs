// 深色 Material 皮肤端到端: 切 → 量 → 切回 (不留配置)
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
const { execFileSync } = require('child_process');

const BASE = 'http://127.0.0.1:5015';
const token = execFileSync('python3', [new URL('./make_cookie.py', import.meta.url).pathname],
  { encoding: 'utf8' }).trim();
// 用户原有外观: 结束时原样还原 (否则深色会顶掉用户的选择)
const _snapR = await fetch(BASE + '/api/appearance', { headers: { Cookie: 'zy_webauth=' + token } });
const snap = (await _snapR.json()).conf || {};

const res = [];
const ok = (c, n, x) => { res.push(!!c); console.log((c ? 'PASS ' : 'FAIL ') + n + (x === undefined ? '' : ' :: ' + JSON.stringify(x))); };

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));

const setSkin = async (skin) => page.evaluate(async (s) => {
  const r = await fetch('/api/appearance', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ skin: s }),
  });
  return r.status;
}, skin);

await page.goto(BASE + '/', { waitUntil: 'networkidle' });

const light = await page.evaluate(() => ({
  bg: getComputedStyle(document.body).backgroundColor,
  shadow: getComputedStyle(document.querySelector('details.sec')).boxShadow,
  card: getComputedStyle(document.querySelector('.bk')).boxShadow,
}));

const st = await setSkin('material-dark');
ok(st === 200, '切到 material-dark 成功', st);
await page.reload({ waitUntil: 'networkidle' });
const dark = await page.evaluate(() => ({
  bg: getComputedStyle(document.body).backgroundColor,
  color: getComputedStyle(document.body).color,
  scheme: getComputedStyle(document.documentElement).colorScheme,
  shadow: getComputedStyle(document.querySelector('details.sec')).boxShadow,
  cardBg: getComputedStyle(document.querySelector('.bk')).backgroundColor,
}));
ok(dark.scheme.includes('dark'), 'color-scheme 变 dark', dark.scheme);
ok(dark.bg === 'rgb(20, 18, 24)', '深色底 #141218', dark.bg);
ok(dark.cardBg === 'rgb(29, 27, 32)', '卡片用 surface 色', dark.cardBg);
ok(/rgba\(0, 0, 0, 0\.42\)/.test(dark.shadow), '深色阴影更重', dark.shadow.slice(0, 46));
ok(dark.bg !== light.bg, '深色与浅色确实不同');

// 阅读器跟随皮肤 (默认 auto)
await page.goto(BASE + '/read/b/a6f63af571f05bb4', { waitUntil: 'networkidle' });
const rv = await page.evaluate(() => ({
  bg: getComputedStyle(document.querySelector('.rv')).backgroundColor,
  theme: document.querySelector('.rv').dataset.theme,
}));
ok(rv.theme === 'auto' && rv.bg === 'rgb(20, 18, 24)', '阅读器默认跟随皮肤 (dark)',
  rv);

// 切回用户原值
const back = await fetch(BASE + '/api/appearance', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', Cookie: 'zy_webauth=' + token },
  body: JSON.stringify(snap),
}).then((r) => r.status);
ok(back === 200, '还原用户原外观', back);
ok(errors.length === 0, '无 JS 报错', errors.slice(0, 3));

await browser.close();
const bad = res.filter((x) => !x).length;
console.log('\n' + (res.length - bad) + ' passed, ' + bad + ' failed');
process.exit(bad ? 1 : 0);
