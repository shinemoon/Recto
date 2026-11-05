/* 设置页「云端同步」「阅读高亮」两块的排版 / 输入框 / 色板
 *
 * 断言的"输入框都有点问题"的具体形态:
 *   1. 数字框曾被 flex 压成 16px 宽 (基本不可用)
 *   2. 卡片在 auto-fit 网格里只分到 368px, 配色表挤成一列
 *   3. 颜色只能手输 CSS 字符串 (rgba(255, 196, 64, .42)), 没有色板
 *
 * 收尾会把 reading_mark 配置恢复默认 + 清掉 sync 覆盖, 不留测试痕迹。
 */
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RV_BASE || process.env.RECTO_BASE || 'http://127.0.0.1:5015';
const out = [];
const chk = (name, cond, extra = '') =>
  out.push(`${cond ? 'ok  ' : 'FAIL'} ${name}${extra ? ' — ' + extra : ''}`);

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 1200 } });
await ctx.addCookies([{ name: 'zy_webauth', value: cookie(), url: BASE }]);
const page = await ctx.newPage();
page.setDefaultTimeout(20000);
const errs = [];
page.on('pageerror', e => errs.push(String(e)));
page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });

await page.goto(`${BASE}/settings`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#mk-styles .mk-style', { state: 'attached', timeout: 20000 });
await page.evaluate(() => { document.querySelectorAll('details.sec').forEach(d => { d.open = true; }); });
await page.waitForSelector('#mk-styles .mk-style', { timeout: 20000 });
await page.waitForTimeout(600);

/* ---------- 1. 两张卡跨整行 ---------- */
const cards = await page.evaluate(() => [...document.querySelectorAll('.st-grid > details')].map(d => {
  const r = d.getBoundingClientRect();
  return { id: d.id, w: Math.round(r.width), x: Math.round(r.x) };
}));
const syncCard = cards.find(c => c.id === 'sync');
const markCard = cards.find(c => c.id === 'mark');
const narrow = cards.filter(c => c.id !== 'sync' && c.id !== 'mark').map(c => c.w);
chk('云端同步跨整行', syncCard.w > Math.min(...narrow) * 1.8, `${syncCard.w} vs ${Math.min(...narrow)}`);
chk('阅读高亮跨整行', markCard.w > Math.min(...narrow) * 1.8, `${markCard.w}`);
chk('两张宽卡左边缘对齐', syncCard.x === markCard.x, `${syncCard.x} / ${markCard.x}`);

/* ---------- 2. 输入框: 尺寸统一、不再被压扁 ---------- */
const fields = await page.evaluate(() => ['sy-gap', 'mk-minlen', 'mk-maxterms'].map(id => {
  const el = document.getElementById(id);
  const r = el.getBoundingClientRect();
  const s = getComputedStyle(el);
  return { id, w: +r.width.toFixed(1), h: +r.height.toFixed(1), border: s.borderTopWidth };
}));
fields.forEach(f => {
  chk(`${f.id} 宽度可用`, f.w >= 44, `${f.w}px`);
  chk(`${f.id} 高度 40`, Math.abs(f.h - 40) < 1.5, `${f.h}px`);
  chk(`${f.id} 无描边 (填充式)`, f.border === '0px', f.border);
});
const modeSel = await page.evaluate(() => {
  const el = document.getElementById('mk-mode'); const r = el.getBoundingClientRect();
  const s = getComputedStyle(el);
  return { w: +r.width.toFixed(1), h: +r.height.toFixed(1), border: s.borderTopWidth };
});
chk('渲染方式下拉与输入框同高', Math.abs(modeSel.h - 40) < 1.5, `${modeSel.h}px`);
chk('渲染方式下拉无描边', modeSel.border === '0px', modeSel.border);
chk('渲染方式下拉宽度合理', modeSel.w >= 140, `${modeSel.w}px`);

/* 行内基线: 控件中线 vs 左侧文字中线 */
const align = await page.evaluate(() => {
  const rows = [...document.querySelectorAll('#sync .st-row, #mark .st-row')];
  return rows.map(r => {
    const label = r.querySelector('span');
    const ctrl = r.querySelector('input, select, b');
    if (!label || !ctrl) return 0;
    const a = label.getBoundingClientRect(), b = ctrl.getBoundingClientRect();
    return Math.abs((a.top + a.height / 2) - (b.top + b.height / 2));
  });
});
chk('字段行中线对齐 (≤1.5px)', align.every(d => d <= 1.5), `max ${Math.max(...align, 0).toFixed(1)}`);

/* 开关行: 每一行开关右边缘一致 */
const swRight = await page.evaluate(() => [...document.querySelectorAll('#sync .st-sw input.m3')]
  .map(el => Math.round(el.getBoundingClientRect().right)));
chk('四个开关右对齐', new Set(swRight).size === 1, JSON.stringify(swRight));

/* ---------- 3. 配色 = 色板, 而不是文本框 ---------- */
const swap = await page.evaluate(() => {
  const cells = [...document.querySelectorAll('#mk-styles .mk-style')];
  const texts = document.querySelectorAll('#mk-styles input[type=text]').length;
  const first = cells[0];
  const wraps = [...first.querySelectorAll('.sw-wrap')];
  return {
    cards: cells.length,
    textInputs: texts,
    swatches: wraps.map(w => w.querySelectorAll('button.sw').length),
    pickers: first.querySelectorAll('.sw.pick input[type=color]').length,
    alpha: first.querySelectorAll('.sw-a').length,
    // 色板是否排成一行 (wrap 高度 <= 26)
    wrapH: wraps.map(w => Math.round(w.getBoundingClientRect().height)),
    // 卡片是否都排在同一行 (桌面 3 列)
    rowTops: cells.slice(0, 3).map(c => Math.round(c.getBoundingClientRect().top))
  };
});
chk('8 张分类配色卡', swap.cards === 8, String(swap.cards));
chk('不再有颜色文本框', swap.textInputs === 0, String(swap.textInputs));
chk('底色/文字色各 9 个色块 + 无', swap.swatches.every(n => n === 9), JSON.stringify(swap.swatches));
chk('每类两个自定义调色板入口', swap.pickers === 2, String(swap.pickers));
chk('底色带不透明度选择', swap.alpha === 1, String(swap.alpha));
chk('色板一行排开', swap.wrapH.every(h => h <= 26), JSON.stringify(swap.wrapH));
chk('配色卡 3 列并排', new Set(swap.rowTops).size === 1, JSON.stringify(swap.rowTops));

/* 预设色: 默认底色应该已经被点亮 (黄) */
const pressed = await page.evaluate(() => {
  const card = document.querySelector('#mk-styles .mk-style[data-cat="person"]');
  const wrap = card.querySelector('.sw-wrap[data-mk="bg"]');
  const on = wrap.querySelector('button.sw[aria-pressed="true"]');
  return { val: wrap.dataset.val, on: on ? on.dataset.v : null };
});
chk('默认底色命中预设 (选中态)', pressed.on && pressed.on === pressed.val, JSON.stringify(pressed));

/* 点一个色板 → 值变 */
await page.click('#mk-styles .mk-style[data-cat="person"] .sw-wrap[data-mk="bg"] button.sw[data-v="rgba(88, 166, 255, .30)"]');
const afterClick = await page.evaluate(() => window.__MK__.value('person', 'bg'));
chk('点色板即换值', afterClick === 'rgba(88, 166, 255, .30)', afterClick);

/* 不透明度: 从 30% 改到 60% */
await page.evaluate(() => {
  const sel = document.querySelector('#mk-styles .mk-style[data-cat="person"] .sw-wrap[data-mk="bg"] .sw-a');
  sel.value = '0.6';
  sel.dispatchEvent(new Event('change', { bubbles: true }));
});
const afterAlpha = await page.evaluate(() => window.__MK__.value('person', 'bg'));
chk('不透明度改写 rgba', /\.6\)$/.test(afterAlpha), afterAlpha);

/* 自定义调色板: input[type=color] 的 input 事件 */
await page.evaluate(() => {
  const inp = document.querySelector('#mk-styles .mk-style[data-cat="person"] .sw-wrap[data-mk="color"] input[type=color]');
  inp.value = '#123456';
  inp.dispatchEvent(new Event('input', { bubbles: true }));
});
const afterPick = await page.evaluate(() => window.__MK__.value('person', 'color'));
chk('自定义调色板生效', /^#123456$/i.test(String(afterPick)), String(afterPick));
const pickOn = await page.evaluate(() => document.querySelector('#mk-styles .mk-style[data-cat="person"] .sw-wrap[data-mk="color"] .sw.pick').classList.contains('is-on'));
chk('自定义色显示为选中', pickOn === true);

/* 「无」也点得掉 */
await page.click('#mk-styles .mk-style[data-cat="person"] .sw-wrap[data-mk="color"] button.sw.none');
const afterNone = await page.evaluate(() => window.__MK__.value('person', 'color'));
chk('「无」清空颜色', afterNone === '', JSON.stringify(afterNone));

/* ---------- 4. 保存 → 落盘 → 刷新保持 ---------- */
await page.evaluate(() => window.__MK__.set('person', 'bg', 'rgba(88, 166, 255, .6)'));
await page.click('[data-st="mark"]');
await page.waitForTimeout(900);
const msg = await page.locator('#mk-msg').innerText();
chk('保存有回执', /已保存/.test(msg), msg);
const saved = await page.evaluate(async () => (await fetch('/api/mark')).json());
chk('配色落盘', saved.styles.person.bg === 'rgba(88, 166, 255, .6)' && saved.styles.person.color === '',
  JSON.stringify(saved.styles.person));

await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForSelector('#mk-styles .mk-style', { state: 'attached', timeout: 20000 });
await page.evaluate(() => { document.querySelectorAll('details.sec').forEach(d => { d.open = true; }); });
await page.waitForTimeout(700);
const afterReload = await page.evaluate(() => window.__MK__.value('person', 'bg'));
chk('刷新后保持', afterReload === 'rgba(88, 166, 255, .6)', afterReload);
const reloadOn = await page.evaluate(() => {
  const w = document.querySelector('#mk-styles .mk-style[data-cat="person"] .sw-wrap[data-mk="bg"]');
  const pick = w.querySelector('.sw.pick');
  return { pick: pick.classList.contains('is-on'), tint: pick.style.getPropertyValue('--c') };
});
chk('刷新后自定义色仍高亮', reloadOn.pick === true, JSON.stringify(reloadOn));

/* ---------- 5. 手机 390: 不溢出 / 不换行挤成一坨 ---------- */
const mctx = await browser.newContext({ viewport: { width: 390, height: 860 }, isMobile: true, hasTouch: true });
await mctx.addCookies([{ name: 'zy_webauth', value: cookie(), url: BASE }]);
const mp = await mctx.newPage();
const merrs = [];
mp.on('pageerror', e => merrs.push(String(e)));
await mp.goto(`${BASE}/settings`, { waitUntil: 'domcontentloaded' });
await mp.waitForSelector('#mk-styles .mk-style', { state: 'attached', timeout: 20000 });
await mp.evaluate(() => { document.querySelectorAll('details.sec').forEach(d => { d.open = true; }); });
await mp.waitForSelector('#mk-styles .mk-style', { timeout: 20000 });
await mp.waitForTimeout(600);
const mob = await mp.evaluate(() => {
  const el = document.querySelector('#mk-styles .mk-style');
  const gapW = document.getElementById('sy-gap').getBoundingClientRect();
  const wrap = el.querySelector('.sw-wrap');
  return {
    overflow: document.documentElement.scrollWidth - window.innerWidth,
    cardW: Math.round(el.getBoundingClientRect().width),
    gapW: +gapW.width.toFixed(1),
    wrapH: Math.round(wrap.getBoundingClientRect().height),
    wrapW: Math.round(wrap.getBoundingClientRect().width)
  };
});
chk('手机无横向溢出', mob.overflow <= 0, String(mob.overflow));
chk('手机数字框仍可用', mob.gapW >= 44, `${mob.gapW}px`);
chk('手机色板不炸行 (≤2 行)', mob.wrapH <= 60, `${mob.wrapH}px (w=${mob.wrapW})`);
chk('手机无 JS 报错', merrs.length === 0, merrs.slice(0, 2).join(' | '));

/* ---------- 收尾: 恢复默认 ---------- */
await page.evaluate(async () => {
  await fetch('/api/mark', { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ action: 'reset' }) });
  await fetch('/api/settings', { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ sync: null }) });
});
const final = await page.evaluate(async () => (await fetch('/api/mark')).json());
chk('收尾恢复默认', /rgba\(255, 196, 64/.test(final.styles.person.bg), final.styles.person.bg);

chk('没有 JS 报错 (桌面)', errs.length === 0, errs.slice(0, 3).join(' | '));

console.log(out.join('\n'));
console.log(`\n${out.filter(x => x.startsWith('ok')).length}/${out.length} PASS`);
await browser.close();
process.exit(out.some(x => x.startsWith('FAIL')) ? 1 : 0);
