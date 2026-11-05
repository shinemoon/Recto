/** 语料页输入框: 无描边 / 填充底色 / 焦点指示线 / 对齐 / 深色皮肤 —— 断言式体检。 */
import { chromium } from './_env.mjs';
import { REPO } from './_env.mjs';
import { execSync } from 'node:child_process';

const BASE = process.env.RECTO_BASE || 'http://127.0.0.1:5015';
const value = execSync('python3 /tmp/mkcookie.py', { encoding: 'utf8' }).trim().split('=')[1];
const darkCss = execSync(
  'cd ' + REPO + ' && PYTHONPATH=src python3 -c "from recto.app.ui import skins;c=skins.load_conf();c[\'skin\']=\'material-dark\';print(skins.skin_css(c))"',
  { encoding: 'utf8' });

let pass = 0, fail = 0;
const ok = (name, cond, extra = '') => {
  if (cond) { pass++; console.log('  PASS', name, extra); }
  else { fail++; console.log('  FAIL', name, extra); }
};

const b = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await b.newContext({ viewport: { width: 1400, height: 1000 } });
await ctx.addCookies([{ name: 'zy_webauth', value, domain: '127.0.0.1', path: '/' }]);
const p = await ctx.newPage();
const errs = [];
p.on('pageerror', e => errs.push(String(e)));
await p.goto(BASE + '/corpus/', { waitUntil: 'networkidle' });
await p.waitForTimeout(1200);

const probe = async () => p.evaluate(() => {
  const fields = [...document.querySelectorAll('.card-body input, .card-body select, .card-body textarea')]
    .filter(el => !['checkbox', 'radio', 'range', 'hidden'].includes(el.type) && el.getBoundingClientRect().width > 0);
  const one = fields[0];
  const cs = getComputedStyle(one);
  const lab = one.closest('label');
  const gap = lab ? Math.round(one.getBoundingClientRect().top - lab.getBoundingClientRect().top) : null;
  const rows = [...document.querySelectorAll('.row')].map(r => {
    const kids = [...r.children].filter(k => k.getBoundingClientRect().width > 0);
    if (!kids.length) return null;
    const mids = kids.map(k => { const bb = k.getBoundingClientRect(); return bb.top + bb.height / 2; });
    return { n: kids.length, spread: Math.round(Math.max(...mids) - Math.min(...mids)) };
  }).filter(Boolean);
  const card = document.querySelector('.card');
  const head = card?.querySelector('.card-head');
  const body = card?.querySelector('.card-body');
  const leftOf = e => Math.round(e.getBoundingClientRect().left);
  const innerLeft = Math.round(card.getBoundingClientRect().left);
  const ic = document.querySelector('.grid > label.inline-check');
  return {
    count: fields.length,
    border: cs.borderTopWidth + '/' + cs.borderLeftWidth,
    bg: cs.backgroundColor,
    h: Math.round(one.getBoundingClientRect().height),
    radius: cs.borderTopLeftRadius,
    gap,
    rows,
    cardPad: getComputedStyle(card).padding,
    headInset: head ? leftOf(head.querySelector('h2')) - innerLeft : null,
    bodyInset: body ? leftOf(body.firstElementChild) - innerLeft : null,
    inlineCheck: ic ? { h: Math.round(ic.getBoundingClientRect().height), w: Math.round(ic.getBoundingClientRect().width), col: getComputedStyle(ic).gridColumnStart } : null,
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  };
});

console.log('— 浅色 (material) —');
const L = await probe();
ok('字段数 > 10', L.count > 10, L.count);
ok('零描边', L.border === '0px/0px', L.border);
ok('直角 (皮肤 radius_scale=0)', L.radius === '0px', L.radius);
ok('输入框高 40', L.h === 40, L.h);
ok('有填充底色 (非透明)', !/rgba\(0, 0, 0, 0\)/.test(L.bg), L.bg);
ok('标签→输入 23px (文字 17 + 6 间距)', L.gap === 23, L.gap);
ok('行内混排中线偏差 <= 3px', L.rows.every(r => r.spread <= 3), JSON.stringify(L.rows));
ok('卡片内边距单层 0', L.cardPad === '0px', L.cardPad);
ok('卡头 / 卡身内容左缘一致', Math.abs(L.headInset - L.bodyInset) <= 1, `${L.headInset} vs ${L.bodyInset}`);
ok('勾选项跨整行且只有一行高', !!L.inlineCheck && L.inlineCheck.h <= 30 && L.inlineCheck.col === '1', JSON.stringify(L.inlineCheck));
ok('无横向溢出', L.overflow <= 0, L.overflow);

await p.click('#llm-base');
const focus = await p.evaluate(() => {
  const cs = getComputedStyle(document.activeElement);
  return { el: document.activeElement.id, shadow: cs.boxShadow, bg: cs.backgroundColor };
});
ok('聚焦出现底部指示线', /inset/.test(focus.shadow), focus.shadow);

console.log('— 深色 (material-dark, 运行时注入) —');
await p.addStyleTag({ content: darkCss });
await p.waitForTimeout(300);
const D = await probe();
ok('深色下字段仍无描边', D.border === '0px/0px', D.border);
ok('深色下填充底色不同于浅色', D.bg !== L.bg, `${L.bg} → ${D.bg}`);
const darkSurfaces = await p.evaluate(() => {
  const pick = s => { const e = document.querySelector(s); return e ? getComputedStyle(e).backgroundColor : null; };
  return { tip: pick('.tip'), head: pick('.card-head'), card: pick('.card') };
});
ok('深色下卡头不再是浅灰', !/250, 251, 252/.test(darkSurfaces.head || ''), JSON.stringify(darkSurfaces));

console.log('— 手机 390 —');
const m = await b.newContext({ viewport: { width: 390, height: 900 } });
await m.addCookies([{ name: 'zy_webauth', value, domain: '127.0.0.1', path: '/' }]);
const mp = await m.newPage();
await mp.goto(BASE + '/corpus/', { waitUntil: 'networkidle' });
await mp.waitForTimeout(1000);
const mob = await mp.evaluate(() => {
  const fields = [...document.querySelectorAll('.card-body input, .card-body select')]
    .filter(el => !['checkbox', 'radio', 'hidden'].includes(el.type));
  // 在「横向可滚动的容器」里 (模型库 .models-wrap) 的字段, 本来就该在视口外 —— 那不是出界。
  const inScroller = (el) => {
    for (let n = el.parentElement; n; n = n.parentElement) {
      const s = getComputedStyle(n).overflowX;
      if ((s === 'auto' || s === 'scroll') && n.scrollWidth > n.clientWidth + 1) return true;
    }
    return false;
  };
  const overAll = fields.filter(f => f.getBoundingClientRect().right > document.documentElement.clientWidth + 1);
  const over = overAll.filter(f => !inScroller(f)).length;
  const inWrap = overAll.length - over;
  return { over, inWrap, overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth };
});
ok('手机端字段不出界', mob.over === 0, JSON.stringify(mob));
ok('手机端出视口字段都在横向滚动的表格里', mob.inWrap > 0, JSON.stringify(mob));
ok('手机端无横向溢出', mob.overflow <= 0, mob.overflow);

ok('无 JS 报错', errs.length === 0, errs.length);
console.log(`\n${pass} PASS / ${fail} FAIL`);
await b.close();
process.exit(fail ? 1 : 0);
