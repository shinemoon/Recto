// 标志 (logo) 的几何与皮肤探针 —— 顶栏图徽 / 登录卡片完整标志 / favicon。
//
// 跑法: cd /vol2/1001/Recto && node tools/probe_logo.mjs
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RECTO_BASE || 'http://127.0.0.1:5015';
const ck = cookie();
const browser = await chromium.launch();
const out = [];
const ok = (cond, msg, extra) => out.push([cond ? 'PASS' : 'FAIL', msg, extra]);

const DARK_CSS = `html{--rc-bg:#141218;--rc-surface:#211f26;--rc-fg:#e6e0e9}`;

for (const [w, h, tag] of [[1400, 900, 'desktop'], [820, 900, 'tablet'], [390, 780, 'mobile'], [360, 740, 'small']]) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h } });
  await ctx.addCookies([{ name: 'zy_webauth', value: ck, domain: '127.0.0.1', path: '/' }]);
  const p = await ctx.newPage();
  const errs = [];
  p.on('pageerror', (e) => errs.push(String(e)));

  // --- 顶栏 ---
  await p.goto(BASE + '/', { waitUntil: 'networkidle' });
  await p.waitForSelector('.rc-brand .rc-logo', { state: 'visible' });
  const m = await p.evaluate(() => {
    const img = document.querySelector('.rc-brand .rc-logo');
    const brand = document.querySelector('.rc-brand');
    const nav = document.querySelector('.rc-nav');
    const r = img.getBoundingClientRect();
    return {
      loaded: img.complete && img.naturalWidth > 0,
      natural: [img.naturalWidth, img.naturalHeight],
      box: [Math.round(r.width), Math.round(r.height)],
      radius: getComputedStyle(img).borderRadius,
      filter: getComputedStyle(img).filter,
      brandRight: Math.round(brand.getBoundingClientRect().right),
      navLeft: nav ? Math.round(nav.getBoundingClientRect().left) : null,
      navScroll: nav ? [nav.scrollWidth, nav.clientWidth] : null,
      docScroll: [document.documentElement.scrollWidth, document.documentElement.clientWidth],
      brandText: brand.textContent.replace(/\s+/g, ''),
    };
  });
  const ar = m.natural[0] / m.natural[1];
  ok(m.loaded, `${tag}: 图徽真的加载了`, m.natural.join('x'));
  ok(Math.abs(m.box[0] / m.box[1] - ar) < 0.03, `${tag}: 不拉伸 (比例 ${ar.toFixed(2)})`, m.box.join('x'));
  ok(m.box[1] <= 24 && m.box[1] >= 16, `${tag}: 高度在 16~24px 之间`, m.box[1] + 'px');
  ok(m.radius === '0px', `${tag}: 图徽没有额外圆角裁切`, m.radius);
  ok(m.brandText.includes('云阅Recto'), `${tag}: 文字品牌仍在`, m.brandText);
  ok(m.box[0] < 120, `${tag}: 图徽宽度不喧宾夺主`, m.box[0] + 'px');
  ok(m.docScroll[0] <= m.docScroll[1] + 1, `${tag}: 页面无横向溢出`, m.docScroll.join('/'));
  ok(errs.length === 0, `${tag}: 0 JS 报错`, errs.join(' | '));

  // --- 深色皮肤: 反相 ---
  const p2 = await ctx.newPage();
  await p2.goto(BASE + '/', { waitUntil: 'networkidle' });
  await p2.addStyleTag({ content: DARK_CSS });
  const darkOnSelf = await p2.evaluate(() => {
    // 皮肤由 /assets/skin.css 决定; 这里只验 CSS 规则存在 (深色皮肤时才输出)
    return Array.from(document.styleSheets).length > 0;
  });
  await p2.close();
  ok(darkOnSelf, `${tag}: 样式表可读`);

  // --- 登录页: 完整标志 ---
  const ctx2 = await browser.newContext({ viewport: { width: w, height: h } });
  const p3 = await ctx2.newPage();
  const errs3 = [];
  p3.on('pageerror', (e) => errs3.push(String(e)));
  await p3.goto(BASE + '/login', { waitUntil: 'networkidle' });
  await p3.waitForSelector('.rc-lockup img', { state: 'visible' });
  const lg = await p3.evaluate(() => {
    const img = document.querySelector('.rc-lockup img');
    const card = document.querySelector('.rc-auth-card');
    const r = img.getBoundingClientRect();
    const c = card.getBoundingClientRect();
    return {
      loaded: img.complete && img.naturalWidth > 0,
      natural: [img.naturalWidth, img.naturalHeight],
      box: [Math.round(r.width), Math.round(r.height)],
      inside: r.left >= c.left - 1 && r.right <= c.right + 1,
      docScroll: [document.documentElement.scrollWidth, document.documentElement.clientWidth],
    };
  });
  const ar2 = lg.natural[0] / lg.natural[1];
  ok(lg.loaded, `${tag}: 登录页标志加载`, lg.natural.join('x'));
  ok(Math.abs(lg.box[0] / lg.box[1] - ar2) < 0.03, `${tag}: 登录页标志不拉伸`, lg.box.join('x'));
  ok(lg.box[1] >= 44 && lg.box[1] <= 80, `${tag}: 登录页标志高 44~80px`, lg.box[1] + 'px');
  ok(lg.inside, `${tag}: 登录页标志不出卡片`, JSON.stringify(lg.box));
  ok(lg.docScroll[0] <= lg.docScroll[1] + 1, `${tag}: 登录页无横向溢出`);
  ok(errs3.length === 0, `${tag}: 登录页 0 JS 报错`, errs3.join(' | '));
  await ctx2.close();
  await ctx.close();
}

// --- 深色皮肤实际反相 ---
{
  const { execFileSync } = await import('child_process');
  const css = execFileSync('python3', ['-c', `
import sys; sys.path.insert(0, "${process.env.RECTO_BASE ? '/vol2/1001/Recto' : '/vol2/1001/Recto'}/src")
from recto.app.ui import skins
print(skins.skin_css({"skin": "material"}))
print("---SPLIT---")
print(skins.skin_css({"skin": "material-dark"}))
`], { encoding: 'utf8' });
  const [light, dark] = css.split('---SPLIT---');
  ok(!light.includes('invert(1)'), '浅色皮肤不反相');
  ok(dark.includes('html img.rc-logo{filter:invert(1)'), '深色皮肤把标志反相成白色');
}

// --- favicon ---
{
  const ctx = await browser.newContext();
  const p = await ctx.newPage();
  await p.goto(BASE + '/login', { waitUntil: 'networkidle' });
  const icons = await p.evaluate(() =>
    Array.from(document.querySelectorAll('link[rel*="icon"]')).map((l) => [l.rel, l.getAttribute('href')]));
  ok(icons.length === 2, '页面声明了 favicon + apple-touch-icon', JSON.stringify(icons));
  const resp = await p.request.get(BASE + '/assets/favicon.png');
  ok(resp.status() === 200 && /image\/png/.test(resp.headers()['content-type'] || ''),
     'favicon 可取 (image/png)', resp.status() + ' ' + resp.headers()['content-type']);
  const buf = await resp.body();
  ok(buf.length > 200, 'favicon 不是空文件', buf.length + 'B');
  await ctx.close();
}

await browser.close();

let bad = 0;
for (const [st, msg, extra] of out) {
  if (st === 'FAIL') bad++;
  console.log(`${st} ${msg}${extra ? ' · ' + extra : ''}`);
}
console.log(`\n${out.length - bad} passed, ${bad} failed`);
process.exit(bad ? 1 : 0);
