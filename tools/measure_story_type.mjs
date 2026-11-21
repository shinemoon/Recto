/* 量一下书宠正文窗口的排版现状 —— 每行多少字、字多大、行距多少。 */
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RECTO_BASE || 'http://127.0.0.1:5015';
const token = cookie();

const browser = await chromium.launch({ args: ['--no-sandbox'] });

for (const vp of [{ width: 1280, height: 900 }, { width: 390, height: 780 }]) {
  const ctx = await browser.newContext({
    viewport: { width: vp.width, height: vp.height },
    isMobile: vp.width < 500, hasTouch: vp.width < 500,
  });
  await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
  const page = await ctx.newPage();
  await page.goto(BASE + '/pet/', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1800);

  const m = await page.evaluate(() => {
    const body = document.querySelector('#adv-read-body');
    const p = body && body.querySelector('p');
    if (!body || !p) return { err: 'no body/p' };
    const cs = getComputedStyle(p);
    const bs = getComputedStyle(body);
    // 拿第一段的前两行, 量每行实际多少个汉字
    const text = p.textContent || '';
    const rng = document.createRange();
    rng.selectNodeContents(p);
    const rects = Array.from(rng.getClientRects()).filter((r) => r.height > 2);
    const perLine = [];
    if (rects.length) {
      const h0 = rects[0].top;
      let n = 0;
      for (const r of rects) {
        if (Math.abs(r.top - h0) < 2) n += (r.width / parseFloat(cs.fontSize));
        else break;
      }
      perLine.push(Math.round(n));
    }
    return {
      bodyW: Math.round(body.getBoundingClientRect().width),
      bodyClientW: body.clientWidth,
      padL: bs.paddingLeft, padR: bs.paddingRight,
      fontFamily: cs.fontFamily.slice(0, 70),
      fontSize: cs.fontSize,
      lineHeight: cs.lineHeight,
      margin: cs.margin,
      textIndent: cs.textIndent,
      textAlign: cs.textAlign,
      charsPerLine: perLine[0] || 0,
      lineCount: rects.length,
      textSample: text.slice(0, 30),
      windowH: Math.round(body.getBoundingClientRect().height),
    };
  });
  console.log(vp.width + 'px →', JSON.stringify(m, null, 2));
  await ctx.close();
}
await browser.close();
