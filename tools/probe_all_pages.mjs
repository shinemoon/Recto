// 全站 Material 骨架巡检: 每个分区都能开、无横向溢出、无 JS 报错, 组件量到 M3 形状。
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
const { execFileSync } = require('child_process');

const BASE = process.env.RECTO_BASE || 'http://127.0.0.1:5015';
const token = execFileSync('python3', [new URL('./make_cookie.py', import.meta.url).pathname],
  { encoding: 'utf8' }).trim();

const res = [];
const ok = (c, n, x) => { res.push(!!c); console.log((c ? 'PASS ' : 'FAIL ') + n + (x === undefined ? '' : ' :: ' + JSON.stringify(x))); };

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

for (const [label, url] of [['书架', '/'], ['摘录', '/admin/'], ['语料', '/corpus/'],
  ['外观', '/appearance'], ['阅读页', '/read/b/a6f63af571f05bb4']]) {
  const r = await page.goto(BASE + url, { waitUntil: 'networkidle' });
  const m = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
    bg: getComputedStyle(document.body).backgroundColor,
    font: getComputedStyle(document.body).fontFamily.split(',')[0],
  }));
  ok(r.status() === 200, label + ' 200', r.status());
  ok(m.scroll <= m.client + 1, label + ' 无横向溢出', { s: m.scroll, c: m.client });
  ok(m.font === 'Roboto', label + ' 字体 Roboto', m.font);
  console.log('   bg', m.bg);
}

// 外观页: Material 两套皮肤在列 + 色块
await page.goto(BASE + '/appearance', { waitUntil: 'networkidle' });
const skins = await page.evaluate(() => ({
  ids: [...document.querySelectorAll('[data-skin]')].map((e) => e.dataset.skin),
  swatches: document.querySelectorAll('.skin-card .sw i').length,
}));
ok(skins.ids.includes('material') || skins.ids.includes('material-dark'),
  '外观页列出 Material 皮肤', skins.ids);
ok(skins.swatches > 0, '皮肤有色块', skins.swatches);

// 摘录后台: 表格在 wrap 里 (M3 卡片样式)
await page.goto(BASE + '/admin/?pane=books', { waitUntil: 'networkidle' });
await page.waitForTimeout(300);
const tt = await page.evaluate(() => {
  const w = document.querySelector('.table-wrap');
  if (!w) return null;
  const cs = getComputedStyle(w);
  return { radius: cs.borderTopLeftRadius, shadow: cs.boxShadow !== 'none' };
});
ok(tt && parseFloat(tt.radius) <= 1 && tt.shadow, '摘录表格是卡片 (直角 + 阴影)', tt);

// 面板切换: 云端同步 → 书架
await page.goto(BASE + '/', { waitUntil: 'networkidle' });
for (const key of ['sync', 'library']) {
  await page.click(`[data-pane-link="${key}"]`);
  await page.waitForTimeout(120);
  const vis = await page.locator(`section[data-pane="${key}"]`).isVisible();
  ok(vis, `面板 ${key} 可见`);
}

ok(errors.length === 0, '全程无 JS 报错', errors.slice(0, 4));
await browser.close();
const bad = res.filter((x) => !x).length;
console.log('\n' + (res.length - bad) + ' passed, ' + bad + ' failed');
process.exit(bad ? 1 : 0);
