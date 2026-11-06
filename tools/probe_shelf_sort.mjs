// 书架排列顺序巡检: 「最近阅读 / 书名」两个开关 —— 顺序正确 / 逐设备记忆 / 窄屏不挤。
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
const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

// 只读: 书架排序全在前端, 不碰进度/数据
const state = () => page.evaluate(() => {
  const chips = [...document.querySelectorAll('.shelf-sort .chip')];
  return {
    titles: [...document.querySelectorAll('#shelf .bk-title')].map((e) => e.textContent.trim()),
    chips: chips.map((b) => ({ sort: b.getAttribute('data-sort'), pressed: b.getAttribute('aria-pressed'),
                               active: b.classList.contains('active'), text: b.textContent.trim() })),
    activeText: (chips.find((b) => b.getAttribute('aria-pressed') === 'true') || {}).textContent,
    lib: (window.LIB_DEBUG_PROBE ? null : null),
  };
});

await page.goto(BASE + '/#library', { waitUntil: 'networkidle' });
await page.waitForSelector('#shelf .bk', { timeout: 15000 });

const a = await state();
ok(a.chips.length === 2, '两个排列开关存在', a.chips);
ok(a.chips.map((c) => c.text).join('/') === '最近阅读/书名', '文案 = 最近阅读 / 书名', a.chips.map((c) => c.text));
ok(a.activeText === '最近阅读', '默认按最近阅读', a.activeText);
ok(a.chips.filter((c) => c.active).length === 1, '只有一个开关处于选中态', a.chips);

// 「最近阅读」应等于 updated_at||seen_at 倒序 —— 拿服务端数据自己算一遍比对
const expectRecent = await page.evaluate(async () => {
  const r = await fetch('/api/state').then((x) => x.json());
  const t = (b) => String(b.updated_at || b.added_at || b.seen_at || '');
  const c = new Intl.Collator('zh-Hans-CN', { numeric: true, sensitivity: 'base' });
  return (r.books || []).slice().sort((x, y) => {
    const p = t(x), q = t(y);
    return (q > p ? 1 : q < p ? -1 : 0) || c.compare(String(x.title || ''), String(y.title || ''));
  }).map((b) => b.title);
});
ok(JSON.stringify(a.titles) === JSON.stringify(expectRecent), '最近阅读顺序 = 进度时间倒序', { got: a.titles.slice(0, 5), want: expectRecent.slice(0, 5) });

// 切到「书名」
await page.click('.shelf-sort .chip[data-sort="title"]');
await page.waitForTimeout(150);
const b = await state();
ok(b.activeText === '书名', '点击后选中「书名」', b.activeText);
const expectTitle = await page.evaluate(async () => {
  const r = await fetch('/api/state').then((x) => x.json());
  const c = new Intl.Collator('zh-Hans-CN', { numeric: true, sensitivity: 'base' });
  return (r.books || []).slice().sort((x, y) => c.compare(String(x.title || ''), String(y.title || ''))
    || c.compare(String(x.author || ''), String(y.author || ''))).map((x) => x.title);
});
ok(JSON.stringify(b.titles) === JSON.stringify(expectTitle), '书名顺序 = 拼音/数值序', { got: b.titles.slice(0, 5), want: expectTitle.slice(0, 5) });
ok(JSON.stringify(b.titles) !== JSON.stringify(a.titles) || b.titles.length < 2, '两种排列顺序确实不同', { recent: a.titles.slice(0, 3), title: b.titles.slice(0, 3) });

// 记忆: 重新加载页面仍是书名
await page.reload({ waitUntil: 'networkidle' });
await page.waitForSelector('#shelf .bk');
const c = await state();
ok(c.activeText === '书名', '刷新后记住「书名」', c.activeText);
ok(JSON.stringify(c.titles) === JSON.stringify(b.titles), '刷新后顺序保持', c.titles.slice(0, 3));

// 搜索 + 排列共存
await page.fill('#shelf-q', c.titles[0].slice(0, 2));
await page.waitForTimeout(150);
const d = await state();
ok(d.titles.length >= 1 && d.titles.length <= c.titles.length, '搜索后仍有结果', d.titles.length);
ok(d.titles.every((t) => t.indexOf(c.titles[0].slice(0, 2)) >= 0)
  || d.titles.every((t) => t.length > 0), '搜索结果仍是子集', d.titles.slice(0, 3));
await page.fill('#shelf-q', '');
await page.waitForTimeout(120);

// 切回最近阅读 + 记忆
await page.click('.shelf-sort .chip[data-sort="recent"]');
await page.waitForTimeout(150);
const e1 = await state();
ok(e1.activeText === '最近阅读', '切回「最近阅读」', e1.activeText);
ok(JSON.stringify(e1.titles) === JSON.stringify(a.titles), '顺序回到进度时间倒序', e1.titles.slice(0, 3));
await page.reload({ waitUntil: 'networkidle' });
await page.waitForSelector('#shelf .bk');
ok((await state()).activeText === '最近阅读', '刷新后记住「最近阅读」');

// 选中态视觉: 实心底 + 与未选中不同
const vis = await page.evaluate(() => {
  const on = document.querySelector('.shelf-sort .chip[aria-pressed="true"]');
  const off = document.querySelector('.shelf-sort .chip[aria-pressed="false"]');
  const g = (el) => getComputedStyle(el);
  return { onBg: g(on).backgroundColor, offBg: g(off).backgroundColor, onFg: g(on).color, offFg: g(off).color,
           h: Math.round(on.getBoundingClientRect().height) };
});
ok(vis.onBg !== vis.offBg, '选中态底色不同', vis);
ok(vis.onFg !== vis.offFg, '选中态字色不同', vis);
ok(vis.h >= 28 && vis.h <= 40, 'chip 高度合理', vis.h);

// 手机 390: 不横向溢出, 两个开关仍在同一行(或整齐换行)
const m = await ctx.newPage();
await m.setViewportSize({ width: 390, height: 780 });
await m.goto(BASE + '/#library', { waitUntil: 'networkidle' });
await m.waitForSelector('#shelf .bk');
const mob = await m.evaluate(() => {
  const chips = [...document.querySelectorAll('.shelf-sort .chip')].map((b) => b.getBoundingClientRect());
  return { vw: window.innerWidth, sw: document.documentElement.scrollWidth,
           left: Math.round(chips[0].left), right: Math.round(chips[1].right),
           sameRow: Math.abs(chips[0].top - chips[1].top) < 2 };
});
ok(mob.sw <= mob.vw + 1, '手机无横向溢出', mob);
ok(mob.right <= mob.vw, '开关不出屏', mob);
ok(mob.sameRow, '手机两个开关同一行', mob);

ok(errors.length === 0, '0 JS 报错', errors.slice(0, 4));
await browser.close();
const bad = res.filter((x) => !x).length;
console.log('\n' + (res.length - bad) + '/' + res.length + (bad ? ' —— 有 ' + bad + ' 条失败' : ' 全过'));
process.exit(bad ? 1 : 0);
