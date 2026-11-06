// 语料分区 (原生) 的实际浏览器巡检:
//   · 同一套外壳 (顶栏 / 导航 / 皮肤), 没有上游 masthead
//   · 六张卡都在, 字段是填充式 (无描边), 模型库徽标不推挤输入框
//   · 接口真的通: 配置 / 模型库 / 书目 / 导出清单
//   · 深色皮肤下没有一块发光的白板
//   · 手机 390px 无横向溢出
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RECTO_BASE || 'http://127.0.0.1:5015';
const token = cookie();

const res = [];
const ok = (c, n, x) => {
  res.push(!!c);
  console.log((c ? 'PASS ' : 'FAIL ') + n + (x === undefined ? '' : ' :: ' + JSON.stringify(x)));
};

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

await page.goto(BASE + '/corpus/', { waitUntil: 'networkidle' });

// ---- 外壳 ----
ok(await page.locator('.rc-top').count() === 1, '有 Recto 顶栏 (同一套外壳)');
ok(await page.locator('.masthead').count() === 0, '没有上游 masthead');
const navCur = await page.locator('.rc-nav a[aria-current="page"]').innerText().catch(() => '');
ok(navCur.trim() === '语料', '导航高亮在「语料」', navCur);
ok((await page.title()).includes('云阅 Recto'), '标题带品牌', await page.title());

// ---- 六张卡 ----
const cards = ['card-llm', 'card-models', 'card-cats', 'card-epub', 'card-run', 'card-out', 'card-history'];
const found = [];
for (const id of cards) found.push(await page.locator('#' + id).count());
ok(found.every((n) => n === 1), '七块卡片都在', found);

// ---- 字段样式 (填充式: 无描边 + 40 高) ----
const field = page.locator('#llm-base');
const st = await field.evaluate((el) => {
  const c = getComputedStyle(el);
  return { border: c.borderTopWidth, h: el.getBoundingClientRect().height, bg: c.backgroundColor };
});
ok(st.border === '0px', '字段无描边 (填充式)', st);
ok(Math.abs(st.h - 40) < 1.5, '字段高 40 (与按钮同高)', st.h);
ok(st.bg !== 'rgba(0, 0, 0, 0)', '字段有一层填充底色', st.bg);

// ---- 真实接口 (页面自己拉的) ----
const cfg = await page.evaluate(() => api('/api/config'));
ok(!!cfg.llm, 'GET /corpus/api/config 通 (页面内 api())');
ok(!cfg.llm.api_key || cfg.llm.api_key.includes('*'), 'api_key 是脱敏串', cfg.llm.api_key);
const models = await page.evaluate(() => api('/api/models'));
ok(Array.isArray(models.models), 'GET /corpus/api/models 通', models.models.length + ' 条');
const books = await page.evaluate(() => api('/api/books'));
ok(Array.isArray(books.items), 'GET /corpus/api/books 通', books.items.length + ' 本');
const exports = await page.evaluate(() => api('/api/exports'));
ok(Array.isArray(exports.items), 'GET /corpus/api/exports 通', exports.items.length + ' 份');
const roots = await page.evaluate(() => api('/api/epubs/roots'));
ok(roots.roots.length > 0, '扫描根默认来自书库', roots.roots);

// ---- 模型库表格: 徽标不推挤输入框 ----
const modelRows = await page.locator('table.models tbody tr').count();
if (modelRows > 0) {
  const geo = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('table.models tbody tr')];
    return rows.slice(0, 3).map((tr) => Math.round(tr.getBoundingClientRect().height));
  });
  ok(new Set(geo).size === 1, '模型库各行等高 (徽标不换行)', geo);
  const inputW = await page.evaluate(() => {
    const ins = [...document.querySelectorAll('table.models tbody tr td:nth-child(6) input')];
    return [...new Set(ins.map((i) => Math.round(i.getBoundingClientRect().width)))];
  });
  ok(inputW.length === 1, '模型名输入框等宽', inputW);
} else {
  console.log('SKIP 模型库为空 (本轮没建条目)');
}

// ---- 深色皮肤: 不许有发光白板 ----
await page.evaluate(() => {
  const s = document.createElement('style');
  s.textContent = ':root{--rc-bg:#121212;--rc-surface:#1e1e1e;--rc-fg:#e6e1e5;--rc-dim:#c9c5ca;--rc-mute:#938f94;--rc-line:#3a373c;--rc-surface-2:#2a272c;--rc-primary:#d0bcff;--rc-accent:#d0bcff;--rc-ok:#7bd88f;--rc-err:#ffb4ab;--rc-warn:#ffd68a;color-scheme:dark}';
  document.head.appendChild(s);
});
const dark = await page.evaluate(() => {
  const card = document.querySelector('#card-llm .card-body');
  const tip = document.querySelector('.tip');
  return {
    cardBg: getComputedStyle(card).backgroundColor,
    tipBg: tip ? getComputedStyle(tip).backgroundColor : '',
  };
});
ok(!/255,\s*255,\s*255/.test(dark.cardBg), '卡片底色跟随皮肤 (不是纯白)', dark.cardBg);
ok(!/255,\s*255,\s*255/.test(dark.tipBg) && !/247,\s*249,\s*252/.test(dark.tipBg),
  '提示条不再是写死的浅底', dark.tipBg);

// ---- 手机 ----
await page.setViewportSize({ width: 390, height: 844 });
await page.waitForTimeout(300);
const m = await page.evaluate(() => ({
  scrollW: document.documentElement.scrollWidth,
  clientW: document.documentElement.clientWidth,
  tableWrap: !!document.querySelector('.models-wrap'),
}));
ok(m.scrollW <= m.clientW + 1, '390px 无横向溢出', m);
ok(m.tableWrap, '窄屏表格由 .models-wrap 横向滚动承接');

ok(errors.length === 0, '0 个 JS 报错', errors.slice(0, 3));

await browser.close();
const bad = res.filter((x) => !x).length;
console.log('\n' + (res.length - bad) + ' passed, ' + bad + ' failed');
process.exit(bad ? 1 : 0);
