// 表格口径巡检: 全站表格**无边框**, 只靠隔行底色 (banded rows) 区分。
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
const { execFileSync } = require('child_process');

const BASE = process.env.RECTO_BASE || 'http://127.0.0.1:5015';
const token = execFileSync('python3', [new URL('./make_cookie.py', import.meta.url).pathname],
  { encoding: 'utf8' }).trim();

const PAGES = [
  ['/#library', 'library'],
  ['/#sync', 'sync'],
  ['/appearance', 'appearance'],
  ['/admin/', 'admin-home'],
  ['/admin/?pane=books', 'admin-books'],
  ['/admin/?pane=excerpts', 'admin-excerpts'],
  ['/admin/?pane=events', 'admin-events'],
  ['/admin/?pane=tools', 'admin-tools'],
  ['/corpus/', 'corpus'],
];

const res = [];
const ok = (c, n, x) => { res.push(!!c); console.log((c ? 'PASS ' : 'FAIL ') + n + (x === undefined ? '' : ' :: ' + JSON.stringify(x))); };

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

const MEASURE = () => {
  const out = [];
  for (const t of document.querySelectorAll('table')) {
    const cells = [...t.querySelectorAll('th, td')];
    let maxBorder = 0;
    const borderDetail = [];
    for (const c of cells) {
      const s = getComputedStyle(c);
      const w = ['Top', 'Right', 'Bottom', 'Left'].map((k) => parseFloat(s['border' + k + 'Width']) || 0);
      const style = ['Top', 'Right', 'Bottom', 'Left']
        .map((k) => s['border' + k + 'Style']).filter((v) => v !== 'none').length;
      const eff = style ? Math.max(...w) : 0;
      if (eff > maxBorder) maxBorder = eff;
      if (eff > 0) borderDetail.push([c.tagName + '.' + (c.className || ''), eff]);
    }
    const rows = [...t.querySelectorAll('tbody > tr')];
    const bg = (tr) => {
      const td = tr.querySelector('td');
      if (!td) return 'none';
      const tb = getComputedStyle(tr).backgroundColor;
      if (tb && tb !== 'rgba(0, 0, 0, 0)') return tb;
      return getComputedStyle(td).backgroundColor;
    };
    const bands = rows.map(bg);
    out.push({
      cls: t.className || '(none)',
      rows: rows.length,
      cols: t.querySelectorAll('thead th').length || (rows[0] ? rows[0].children.length : 0),
      maxBorder,
      borderDetail: borderDetail.slice(0, 3),
      evenBg: bands.filter((_, i) => i % 2 === 1).slice(0, 2),
      oddBg: bands.filter((_, i) => i % 2 === 0).slice(0, 2),
      alternates: bands.length >= 2
        ? bands.some((b, i) => i % 2 === 1 && b !== bands[i - 1]) : null,
    });
  }
  return out;
};

for (const [path, name] of PAGES) {
  await page.goto(BASE + path, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1400);
  const tables = await page.evaluate(MEASURE);
  const nTable = tables.length;
  console.log(`--- ${name}: ${nTable} 张表`);
  for (const t of tables) console.log('    ' + JSON.stringify(t));
  const bad = tables.filter((t) => t.maxBorder > 0);
  ok(bad.length === 0, `${name} 表格无边框`, bad.map((b) => [b.cls, b.maxBorder, b.borderDetail]));
  const withRows = tables.filter((t) => t.rows >= 2 && t.cols >= 1 && !t.cls.includes('empty'));
  const notBanded = withRows.filter((t) => t.alternates === false);
  ok(notBanded.length === 0, `${name} 隔行底色生效 (${withRows.length} 张有多行)`,
    notBanded.map((t) => [t.cls, t.oddBg, t.evenBg]));
  const spill = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  ok(!spill, `${name} 无横向溢出`);
}

ok(errors.length === 0, '无 JS 报错', errors.slice(0, 3));
await browser.close();
const failed = res.filter((x) => !x).length;
console.log(`\n${res.length - failed}/${res.length} PASS`);
process.exit(failed ? 1 : 0);
