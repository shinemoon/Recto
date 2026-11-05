import { createRequire } from 'module';
const require = createRequire(import.meta.url);
const { chromium } = require('playwright');

const BASE = process.env.RECTO_BASE || 'http://127.0.0.1:5015';
const USER = process.env.RECTO_USER || 'root';
const PASSWD = process.env.RECTO_PASS || 'root';
// 书架上的书是使用者自己的书: 仓库只放占位名, 真名用 env 传进来
const T_LONG = process.env.RV_TITLE_LONG || '示例长篇';
const T_SHORT = process.env.RV_TITLE_SHORT || '示例短篇';
const RE_LONG = new RegExp(T_LONG);
const RE_SHORT = new RegExp(T_SHORT);
const PASS = [], FAIL = [];
const ok = (c, n, e) => (c ? PASS : FAIL).push(n + (e ? ' — ' + e : ''));
const num = (s) => parseFloat(String(s || '').replace('%', ''));

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', (e) => errs.push(String(e)));

  await page.goto(BASE + '/login', { waitUntil: 'domcontentloaded' });
  await page.fill('input[name=username]', USER);
  await page.fill('input[name=password]', PASSWD);
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'domcontentloaded' }).catch(() => {}),
    page.press('input[name=password]', 'Enter'),
  ]);
  ok(!/\/login/.test(page.url()), '登录成功', page.url());

  // 打开阅读器会真的写进度 → 先快照, 收尾写回
  async function readProgress(key) {
    return page.evaluate(async (k) => {
      const j = await (await fetch('/read/api/book/' + k)).json();
      return (j && j.progress) || null;
    }, key);
  }
  async function writeProgress(key, p) {
    if (!p) return null;
    return page.evaluate(async ([k, pr]) => (await fetch('/read/api/book/' + k + '/progress', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chapter: pr.chapter || 0, offset: pr.offset || 0 }),
    })).json(), [key, p]);
  }

  await page.goto(BASE + '/#library', { waitUntil: 'networkidle' });
  await page.waitForSelector('.bk', { timeout: 20000 });
  await page.waitForTimeout(700);

  const cards = await page.$$eval('.bk', (els) => els.map((e) => ({
    title: (e.querySelector('.bk-title') || {}).textContent,
    pct: (e.querySelector('.bk-meta .p') || {}).textContent,
    href: e.getAttribute('href'),
  })));
  console.log('cards', JSON.stringify(cards.map((c) => [c.title, c.pct])));
  const jl = cards.find((c) => RE_LONG.test(c.title));
  const fox = cards.find((c) => RE_SHORT.test(c.title));

  ok(!!jl, '书架有长书 (' + T_LONG + ')');
  ok(!!jl && /^\d+\.\d{2,3}%$/.test((jl.pct || '').trim()), '长篇 2~3 位小数', jl && jl.pct);
  ok(!!jl && (jl.pct || '').trim() !== '31.8%', '长篇不再是 31.8% (固定 1 位)', jl && jl.pct);
  ok(!!fox, '书架有短书 (' + T_SHORT + ')');
  ok(!!fox && /^\d+\.\d%$/.test((fox.pct || '').trim()), '小书仍 1 位小数', fox && fox.pct);

  // 界面数字必须能由接口的全精度值**算出来** (同一套 2KB 刻度规则, 不是巧合)
  const api0 = await page.evaluate(async () => (await (await fetch('/read/api/books')).json()));
  const list0 = api0.books || api0 || [];
  const fmt = (pct01, bytes) => {
    const t = Number(bytes) || 0;
    const d = t > 0 ? Math.max(1, Math.min(3, Math.ceil(Math.log(t / 2048) / Math.LN10 - 2))) : 2;
    const sc = Math.pow(10, d);
    const x = Math.floor(Math.max(0, Math.min(1, Number(pct01) || 0)) * 100 * sc + 1e-9) / sc;
    let out = x.toFixed(d);
    if (out.indexOf('.') >= 0) out = out.replace(/0+$/, '').replace(/\.$/, '');
    return out + '%';
  };
  for (const [card, re] of [[jl, RE_LONG], [fox, RE_SHORT]]) {
    const b = list0.find((x) => re.test(x.title || ''));
    ok(!!b, '接口里有 ' + (re.source), b && b.title);
    if (b && card) {
      ok(fmt(b.percent, b.bytes) === (card.pct || '').trim(),
        '书架数字 = 全精度值按 2KB 刻度换算', fmt(b.percent, b.bytes) + ' vs ' + card.pct);
    }
  }

  if (jl) {
    const key = decodeURIComponent(jl.href.split('/').pop());
    const snap = await readProgress(key);
    try {
      await page.goto(BASE + jl.href, { waitUntil: 'networkidle' });
      await page.waitForTimeout(2600);
      const shown = await page.$eval('#rv-pct', (e) => e.textContent).catch(() => '');
      console.log('reader pct =', shown);
      ok(/^\d+\.\d{2,3}%$/.test(shown.trim()), '阅读器顶栏 2~3 位小数', shown);
      ok(Math.abs(num(shown) - num(jl.pct)) < 0.05, '阅读器与书架同一位置 (±0.05%)',
        shown + ' vs ' + jl.pct);
    } finally {
      await writeProgress(key, snap);
    }
  }

  const api = await page.evaluate(async () => (await (await fetch('/read/api/books')).json()));
  const list = api.books || api || [];
  const jlApi = list.find((b) => RE_LONG.test(b.title || ''));
  console.log('api percent =', jlApi && jlApi.percent, 'bytes =', jlApi && jlApi.bytes);
  ok(!!jlApi && jlApi.bytes > 40000000, '接口带全书字节 (界面据此选位数)');
  // 不再钉死某个读数: 进度会随阅读变动。只要求"浮点全精度 + 与界面显示一致"
  ok(!!jlApi && typeof jlApi.percent === 'number' && jlApi.percent > 0 &&
     jlApi.percent < 1 && String(jlApi.percent).split('.')[1]?.length >= 4,
    '接口 percent 是全精度浮点 (不是 2 位小数字符串)', String(jlApi && jlApi.percent));
  ok(!!jlApi && !!jl && Math.abs(jlApi.percent * 100 - num(jl.pct)) <= 0.05,
    '接口 percent 与书架显示同一位置 (±0.05%)',
    (jlApi && jlApi.percent * 100).toFixed(3) + '% vs ' + (jl && jl.pct));

  // 云端同步面板: 映射表本地 / 云端两列也走同一套位数
  await page.goto(BASE + '/#sync', { waitUntil: 'networkidle' });
  await page.waitForTimeout(900);
  const rows = await page.$$eval('#link-body tr', (els) => els.map((e) =>
    [...e.querySelectorAll('td')].map((td) => td.textContent.trim())));
  console.log('link rows', JSON.stringify(rows.slice(0, 2)));
  const jlRow = rows.find((r) => RE_LONG.test(r[0] || ''));
  ok(!!jlRow, '映射表有长书');
  ok(!!jlRow && /^\d+\.\d{2,3}%$/.test((jlRow[1] || '').trim()), '映射表本地列 2~3 位小数',
    jlRow && jlRow[1]);

  ok(errs.length === 0, '无 JS 报错', errs.join(' | '));
  await browser.close();
  console.log('\n' + PASS.length + ' passed / ' + FAIL.length + ' failed');
  PASS.forEach((p) => console.log('PASS ' + p));
  FAIL.forEach((p) => console.log('FAIL ' + p));
  process.exit(FAIL.length ? 1 : 0);
})();
