// 顶栏品牌 (图徽 + 中英文名) 的取样探针 —— 截图 + 关键几何, 不做断言 (断言见 probe_logo.mjs)。
//
// 跑法: node tools/probe_brand.mjs
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RECTO_BASE || 'http://127.0.0.1:5015';
const cookieVal = cookie();
const browser = await chromium.launch();
const out = [];

for (const [w, h, tag] of [[1400, 900, 'desktop'], [390, 780, 'mobile']]) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h } });
  await ctx.addCookies([{ name: 'zy_webauth', value: cookieVal, domain: '127.0.0.1', path: '/' }]);
  const p = await ctx.newPage();
  const errs = [];
  p.on('pageerror', (e) => errs.push(String(e)));
  await p.goto(BASE + '/', { waitUntil: 'networkidle' });
  const info = await p.evaluate(() => {
    const b = document.querySelector('.rc-brand');
    const en = document.querySelector('.rc-brand-en');
    const logo = b ? b.querySelector('.rc-logo') : null;
    const cs = en ? getComputedStyle(en) : null;
    const lr = logo ? logo.getBoundingClientRect() : null;
    return {
      text: b ? b.textContent.trim() : null,
      html: b ? b.innerHTML.replace(/\s+/g, ' ').slice(0, 150) : null,
      logo: lr ? [Math.round(lr.width), Math.round(lr.height)] : null,
      logoLoaded: logo ? logo.complete && logo.naturalWidth > 0 : false,
      enText: en ? en.textContent : null,
      enSize: cs ? cs.fontSize : null,
      enColor: cs ? cs.color : null,
      brandW: b ? Math.round(b.getBoundingClientRect().width) : null,
      title: document.title,
      overflowX: document.documentElement.scrollWidth > window.innerWidth + 1,
    };
  });
  // 截一块顶栏
  await p.screenshot({ path: `/tmp/brand-${tag}.png`, clip: { x: 0, y: 0, width: w, height: 90 } });
  out.push({ tag, ...info, errs });
  await ctx.close();
}
await browser.close();
console.log(JSON.stringify(out, null, 1));
