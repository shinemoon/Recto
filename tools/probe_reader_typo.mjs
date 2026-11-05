/* 阅读器: 图标返回 / 排版面板 (行间距·段间距·上下边距·阅读宽度·字体) / 页边热区不变色 */
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
const { execFileSync } = require('child_process');

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || 'a6f63af571f05bb4';
const token = execFileSync('python3', [new URL('./make_cookie.py', import.meta.url).pathname],
  { encoding: 'utf8' }).trim();

const out = [];
const chk = (name, cond, extra = '') =>
  out.push(`${cond ? 'ok  ' : 'FAIL'} ${name}${extra ? ' — ' + extra : ''}`);

const browser = await chromium.launch({ args: ['--no-sandbox'] });

async function open(width, height) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(String(e)));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
  await page.addInitScript(() => {
    if (sessionStorage.getItem('probe.init')) return;   // 只在首次打开时清, reload 保留设置
    sessionStorage.setItem('probe.init', '1');
    ['theme', 'fs', 'lh', 'para', 'pad', 'w', 'font'].forEach(k => localStorage.removeItem('recto.' + k));
  });
  await page.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.rv-page p', { timeout: 15000 });
  await page.waitForTimeout(600);
  return { ctx, page, errs };
}

const { ctx, page, errs } = await open(1280, 900);

/* ---------- 1. 返回图标 ---------- */
const back = await page.locator('.rv-back').evaluate(el => ({
  svg: !!el.querySelector('svg'),
  text: (el.textContent || '').trim(),
  w: Math.round(el.getBoundingClientRect().width),
  h: Math.round(el.getBoundingClientRect().height)
}));
chk('返回按钮换成 SVG 图标', back.svg && back.text === '', JSON.stringify(back));
chk('返回图标是方形点击区', back.w >= 28 && back.h >= 28, `${back.w}x${back.h}`);

/* ---------- 2. 页边热区: hover 不变色 ---------- */
await page.mouse.move(8, 400);
await page.waitForTimeout(250);
const turnHover = await page.locator('.rv-turn-r').evaluate(el => {
  const s = getComputedStyle(el);
  return { op: s.opacity, bg: s.backgroundColor, cursor: s.cursor, color: s.color };
});
chk('页边热区 hover 后仍透明', Number(turnHover.op) === 0, `opacity=${turnHover.op}`);
chk('页边热区 hover 不改底色', turnHover.bg === 'rgba(0, 0, 0, 0)', `bg=${turnHover.bg}`);
chk('页边热区鼠标指针是手型', turnHover.cursor === 'pointer');

/* ---------- 3. 排版面板 ---------- */
const typoBtn = page.locator('#rv-typo-btn');
chk('顶栏有排版图标按钮', await typoBtn.locator('svg').count() === 1);
chk('面板初始隐藏', await page.locator('#rv-typo').isHidden());

await typoBtn.click();
await page.waitForTimeout(180);
const panel = await page.locator('#rv-typo').evaluate(el => {
  const r = el.getBoundingClientRect();
  const b = document.querySelector('#rv-bar').getBoundingClientRect();
  return { hidden: el.hidden, top: Math.round(r.top), barBottom: Math.round(b.bottom),
           w: Math.round(r.width), right: Math.round(window.innerWidth - r.right) };
});
chk('点图标打开面板', !panel.hidden);
chk('面板贴在顶栏下方', panel.top >= panel.barBottom && panel.top - panel.barBottom <= 14,
  `panel=${panel.top} bar=${panel.barBottom}`);
// 10px 是 scrollbar-gutter 给滚动条留的宽度, 面板按滚动区定位, 所以允许这点差
chk('面板靠右且不太宽', panel.right <= 26 && panel.w <= 360, `${panel.w}px right=${panel.right}`);
chk('按钮标记 aria-expanded', await typoBtn.getAttribute('aria-expanded') === 'true');

const rows = await page.locator('#rv-typo [data-look]').evaluateAll(els =>
  els.map(e => e.dataset.look));
chk('五项数值控制齐全',
  ['fs', 'lh', 'para', 'pad', 'w'].every(k => rows.includes(k)), rows.join(','));

await page.focus('#rv-font');
await page.waitForTimeout(120);
const fontOpts = await page.locator('#rv-font-menu .rv-combo-opt').evaluateAll(
  els => els.map(e => (e.textContent || '').trim()));
chk('字体是输入框 + 自绘下拉', await page.evaluate(() =>
  document.getElementById('rv-font').tagName === 'INPUT' &&
  !!document.getElementById('rv-font-menu') &&
  !document.getElementById('rv-font-opts')));
chk('下拉含内置四档 + 常用名',
  ['跟随界面', '衬线', '无衬线', '等宽'].every(k => fontOpts.some(t => t.includes(k)))
  && fontOpts.length > 20, `${fontOpts.length} 项`);
// 打字筛清单
await page.fill('#rv-font', 'geo');
await page.waitForTimeout(150);
const filtered = await page.locator('#rv-font-menu .rv-combo-opt').evaluateAll(
  els => els.map(e => (e.textContent || '').trim()));
chk('打字即筛选候选', filtered.length > 0 && filtered.length < fontOpts.length
  && filtered.some(t => /Georgia/i.test(t)), filtered.join(' / ').slice(0, 60));
await page.fill('#rv-font', '');
await page.waitForTimeout(120);

/* ---------- 4. 各项真的改版式 ---------- */
const read = () => page.evaluate(() => {
  const p = document.querySelector('.rv-page p');
  const pg = document.querySelector('.rv-page');
  const main = document.querySelector('.rv-main');
  const cs = getComputedStyle(p);
  return {
    lh: Math.round(parseFloat(cs.lineHeight) * 10) / 10,
    pm: Math.round(parseFloat(cs.marginBottom) * 10) / 10,
    w: Math.round(pg.getBoundingClientRect().width),
    pt: Math.round(parseFloat(getComputedStyle(main).paddingTop)),
    fs: Math.round(parseFloat(cs.fontSize) * 10) / 10,
    ff: cs.fontFamily
  };
});

async function setRange(k, v) {
  await page.locator(`#rv-typo [data-look="${k}"]`).evaluate((el, val) => {
    el.value = String(val);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }, v);
  await page.waitForTimeout(140);
}

const before = await read();
await setRange('lh', 2.4);
const afterLh = await read();
chk('行间距生效', afterLh.lh > before.lh + 3, `${before.lh} → ${afterLh.lh}`);

await setRange('para', 1.8);
const afterPara = await read();
chk('段间距生效', afterPara.pm > afterLh.pm + 5, `${afterLh.pm} → ${afterPara.pm}`);

await setRange('pad', 4);
const afterPad = await read();
chk('上下页边距生效', afterPad.pt > before.pt, `${before.pt} → ${afterPad.pt}`);

await setRange('w', 32);
const afterW = await read();
chk('阅读宽度生效', afterW.w < before.w - 60, `${before.w} → ${afterW.w}`);

const turnW = await page.locator('.rv-turn-r').evaluate(el => Math.round(el.getBoundingClientRect().width));
chk('页边热区跟着阅读宽度调整', turnW > 44, `turn=${turnW}px`);

/* 字体: 手动输入一个字体名 (datalist 只是提示, 输入框才是真源) */
const firstReal = 'Georgia';
await page.fill('#rv-font', firstReal);
await page.press('#rv-font', 'Enter');
await page.waitForTimeout(200);
{
  const afterFont = await read();
  chk('手输字体生效', afterFont.ff.includes(firstReal), `${firstReal} → ${afterFont.ff}`);
}

/* 持久化: 刷新后仍在 */
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForSelector('.rv-page p', { timeout: 15000 });
await page.waitForTimeout(500);
const reloaded = await read();
chk('刷新后排版保持', reloaded.lh === afterLh.lh && reloaded.pm === afterPara.pm,
  `lh=${reloaded.lh} pm=${reloaded.pm} w=${reloaded.w}`);

/* 恢复默认 */
await page.locator('#rv-typo-btn').click();
await page.waitForTimeout(140);
await page.locator('#rv-typo [data-typo="reset"]').click();
await page.waitForTimeout(200);
const reset = await read();
chk('恢复默认把版式还原', reset.w > reloaded.w && reset.pm < afterPara.pm,
  `w=${reset.w} pm=${reset.pm} lh=${reset.lh}`);

/* 先把面板关掉: 下面「点外面收起」那步若面板本来就是关的, 那一下点击会去切换顶栏,
 * 顶栏收起后按钮跑到视口外, 后续 click 会超时 —— 之前只是靠 sticky 顶栏的滚动巧合蒙对。 */
await page.keyboard.press('Escape');
await page.waitForTimeout(160);
chk('Esc 先收起面板 (为下一步做准备)', await page.locator('#rv-typo').isHidden());

/* 点外面收起 / Esc 收起 */
await page.locator('#rv-typo-btn').click();
await page.waitForTimeout(120);
chk('面板重新打开', !(await page.locator('#rv-typo').isHidden()));
await page.mouse.click(640, 700);
await page.waitForTimeout(160);
chk('点正文收起面板', await page.locator('#rv-typo').isHidden());
chk('点正文收面板时不会顺手把顶栏也收起',
  !(await page.locator('#rv-bar').evaluate(el => el.classList.contains('is-hidden'))));
await page.locator('#rv-typo-btn').click();
await page.waitForTimeout(120);
await page.keyboard.press('Escape');
await page.waitForTimeout(160);
chk('Esc 收起面板', await page.locator('#rv-typo').isHidden());

/* 真滚动 = 收起面板 (拖滑块引起的布局位移不算) */
await page.locator('#rv-typo-btn').click();
await page.waitForTimeout(120);
await page.mouse.move(640, 500);
await page.mouse.wheel(0, 800);
await page.waitForTimeout(400);
chk('滚动页面时面板收起', await page.locator('#rv-typo').isHidden());

chk('桌面无 JS 报错', errs.length === 0, errs.slice(0, 3).join(' | '));
await ctx.close();

/* ---------- 5. 手机 ---------- */
const m = await open(390, 844);
const mPanel = await m.page.evaluate(async () => {
  document.querySelector('#rv-typo-btn').click();
  await new Promise(r => setTimeout(r, 200));
  const el = document.querySelector('#rv-typo');
  const r = el.getBoundingClientRect();
  return { left: Math.round(r.left), right: Math.round(r.right),
           w: Math.round(r.width), vw: window.innerWidth,
           overflow: document.documentElement.scrollWidth > window.innerWidth + 1 };
});
chk('手机上排版面板满宽不出界',
  mPanel.left >= 0 && mPanel.right <= mPanel.vw && !mPanel.overflow,
  JSON.stringify(mPanel));

const mBack = await m.page.locator('.rv-back').evaluate(el => el.querySelector('svg') !== null);
chk('手机也显示图标返回', mBack);
chk('手机无 JS 报错', m.errs.length === 0, m.errs.slice(0, 3).join(' | '));
await m.ctx.close();

await browser.close();
console.log(out.join('\n'));
const fail = out.filter(l => l.startsWith('FAIL')).length;
console.log(`\n${out.length - fail} ok / ${fail} FAIL`);
process.exit(fail ? 1 : 0);
