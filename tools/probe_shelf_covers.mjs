// 书架封面巡检: 每张封面都真的加载出来 (naturalWidth>0), 且多次刷新稳定
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
const { execFileSync } = require('child_process');

const BASE = 'http://127.0.0.1:5015';
const token = execFileSync('python3', [new URL('./make_cookie.py', import.meta.url).pathname],
  { encoding: 'utf8' }).trim();
const res = [];
const ok = (c, n, x) => { res.push(!!c); console.log((c ? 'PASS ' : 'FAIL ') + n + (x === undefined ? '' : ' :: ' + JSON.stringify(x))); };

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 1000 } });
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();

const snap = async (label) => {
  await page.goto(BASE + '/#library', { waitUntil: 'networkidle' });
  await page.waitForTimeout(600);
  const m = await page.evaluate(async () => {
    const imgs = [...document.querySelectorAll('.bk-cover img')];
    // 等图片请求全部结束 (成功或失败都算结束)
    await Promise.all(imgs.map((i) => (i.complete ? Promise.resolve()
      : new Promise((r) => { i.onload = r; i.onerror = r; }))));
    return {
      cards: document.querySelectorAll('.bk').length,
      imgs: imgs.length,
      broken: imgs.filter((i) => !i.naturalWidth).length,
      placeholders: document.querySelectorAll('.bk-ph').length,
      srcs: imgs.map((i) => decodeURIComponent(i.getAttribute('src')).slice(-28)),
    };
  });
  console.log(label, JSON.stringify(m));
  return m;
};

let prev = null;
for (const round of [1, 2, 3, 4]) {
  const m = await snap('round ' + round);
  ok(m.broken === 0, `第 ${round} 次刷新: 0 张裂图`, m);
  ok(m.cards === m.imgs + m.placeholders, `第 ${round} 次: 每张卡都有封面或占位`, m);
  if (prev) ok(m.imgs === prev.imgs, `第 ${round} 次: 封面数量不变`, { now: m.imgs, prev: prev.imgs });
  prev = m;
}

// 走一次强制重扫 (旧的会把封面全清空), 再刷一次看还在不在
await page.evaluate(() => fetch('/read/api/books?refresh=1').then((r) => r.json()));
const after = await snap('after refresh=1');
ok(after.broken === 0 && after.imgs === prev.imgs, '强制重扫后封面不丢', after);

await browser.close();
const bad = res.filter((x) => !x).length;
console.log(`\n${res.length - bad} passed / ${bad} failed`);
process.exit(bad ? 1 : 0);
