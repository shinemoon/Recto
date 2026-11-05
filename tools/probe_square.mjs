// 直角 (radius_scale=0) 验收: 列出页面上所有计算后 border-radius 非 0 的元素
// 跑法: node tools/probe_square.mjs
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
const { execFileSync } = require('child_process');

const BASE = process.env.RECTO_BASE || 'http://127.0.0.1:5015';
const PAGES = ['/', '/appearance', '/admin/', '/corpus/'];

const collect = () => {
  // 例外白名单: 书架页头的「右侧操作轨」圆钮 —— 2026-11-04 用户点名要圆形,
  // 其余元素继续按直角口径验收。(写在这里因为整个函数要序列化进页面跑)
  const ALLOW = [/rc-rail-btn/];
  const out = [];
  const seen = new Map();
  for (const el of document.querySelectorAll('*')) {
    const cs = getComputedStyle(el);
    const r = cs.borderTopLeftRadius;
    if (!r || r === '0px') continue;
    const key = el.tagName.toLowerCase() + (el.className && typeof el.className === 'string'
      ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : '');
    if (ALLOW.some((re) => re.test(key))) continue;
    const box = el.getBoundingClientRect();
    if (box.width < 2 || box.height < 2) continue;
    seen.set(key, (seen.get(key) || 0) + 1);
    out.push(key + ' = ' + r);
  }
  return { listing: out.slice(0, 60), uniq: [...seen.entries()] };
};

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const token = execFileSync('python3', [new URL('./make_cookie.py', import.meta.url).pathname],
    { encoding: 'utf8' }).trim();
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
  await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));

  let bad = 0;
  for (const p of PAGES) {
    const res = await page.goto(BASE + p, { waitUntil: 'networkidle' });
    await page.waitForTimeout(500);
    const r = await page.evaluate(collect);
    const status = res ? res.status() : 0;
    console.log('\n== ' + p + '  HTTP ' + status);
    if (r.uniq.length === 0) console.log('   ✓ 全部直角');
    else {
      bad += r.uniq.length;
      console.log('   圆角元素: ' + JSON.stringify(r.uniq));
    }
  }
  // 阅读器
  const shelf = await (async () => {
    await page.goto(BASE + '/', { waitUntil: 'networkidle' });
    const href = await page.evaluate(() => {
      const a = document.querySelector('#shelf .bk');
      return a ? a.getAttribute('href') : null;
    });
    return href;
  })();
  if (shelf) {
    await page.goto(BASE + shelf, { waitUntil: 'networkidle' });
    await page.waitForTimeout(900);
    const r = await page.evaluate(collect);
    console.log('\n== ' + shelf + ' (阅读器)');
    if (r.uniq.length === 0) console.log('   ✓ 全部直角');
    else { bad += r.uniq.length; console.log('   圆角元素: ' + JSON.stringify(r.uniq)); }
    await page.screenshot({ path: '/tmp/recto-square-reader.png' });
  }
  await page.goto(BASE + '/', { waitUntil: 'networkidle' });
  await page.waitForTimeout(400);
  await page.screenshot({ path: '/tmp/recto-square-shelf.png', fullPage: false });
  console.log('\npageerror:', errors.length, errors.slice(0, 3));
  console.log(bad === 0 ? 'RESULT: 0 处圆角' : 'RESULT: ' + bad + ' 类圆角残留');
  await browser.close();
  process.exit(bad === 0 ? 0 : 1);
})();
