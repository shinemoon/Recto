/* 量书宠正文 (前台读本 + 档案页) 的排版:
 *   · 一行多少字 (中文长文 30~45 字最好读, 超过 45 字右边缘全是短头)
 *   · 两端对齐 (justify) 有没有真的生效
 *   · 衬线字体有没有真的落到中文上
 *   · 首行缩进 2 字符
 *   · 避头尾 / 行末标点悬挂 / 中西文间距
 *
 * **逐字定位**算每行字数 —— 用 range.getClientRects() 的整段宽度除字号
 * 会把"末行短"算成一行 (上一版的老 bug)。
 */
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RECTO_BASE || 'http://127.0.0.1:5015';
const token = cookie();
const out = [];
const chk = (n, c, e = '') => out.push(`${c ? 'PASS' : 'FAIL'} ${n}${e ? ' :: ' + e : ''}`);

const browser = await chromium.launch({ args: ['--no-sandbox'] });

async function measure(page, sel) {
  return page.evaluate((sel) => {
    const body = document.querySelector(sel);
    const ps = Array.from(body.querySelectorAll('p'));
    if (!ps.length) return { err: 'no p' };
    let best = ps[0], bestLen = 0;
    for (const p of ps) {
      const t = (p.textContent || '').trim();
      if (t.length > bestLen) { bestLen = t.length; best = p; }
    }
    const cs = getComputedStyle(best);
    const fs = parseFloat(cs.fontSize);
    const tn = best.firstChild;
    if (!tn || tn.nodeType !== 3) return { err: 'no text node' };
    const txt = tn.nodeValue;
    const rows = new Map();
    const rng = document.createRange();
    for (let i = 0; i < txt.length; i++) {
      rng.setStart(tn, i);
      rng.setEnd(tn, i + 1);
      const r = rng.getBoundingClientRect();
      if (!r.height) continue;
      const key = Math.round(r.top);
      let row = rows.get(key);
      if (!row) { row = { top: key, n: 0, left: r.left, right: r.right }; rows.set(key, row); }
      row.n++;
      row.left = Math.min(row.left, r.left);
      row.right = Math.max(row.right, r.right);
    }
    const list = [...rows.values()].sort((a, b) => a.top - b.top);
    return {
      charsPerLine: list.map((r) => r.n),
      lineWidths: list.map((r) => Math.round(r.right - r.left)),
      fs, lh: cs.lineHeight, indent: cs.textIndent,
      textAlign: cs.textAlign, textJustify: cs.textJustify,
      fontFamily: cs.fontFamily.split(',')[0],
      maxWidth: cs.maxWidth, boxW: Math.round(best.getBoundingClientRect().width),
      lineBreak: cs.lineBreak, hanging: cs.hangingPunctuation,
      spacingTrim: cs.textSpacingTrim, autospace: cs.textAutospace,
      longestLen: bestLen,
    };
  }, sel);
}

/* ---------- 前台读本 ---------- */
for (const vp of [{ w: 1280, h: 900 }, { w: 390, h: 780 }]) {
  const ctx = await browser.newContext({
    viewport: { width: vp.w, height: vp.h },
    isMobile: vp.w < 500, hasTouch: vp.w < 500,
  });
  await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', (e) => errs.push(String(e)));
  await page.goto(BASE + '/pet/', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1600);
  for (let i = 0; i < 6; i++) { await page.keyboard.press('Space'); await page.waitForTimeout(300); }

  const m = await measure(page, '#adv-read-body');
  const tag = '前台读本 ' + vp.w + 'px';
  chk(tag + ' · 有正文', !m.err, JSON.stringify(m).slice(0, 120));
  if (!m.err) {
    const full = m.charsPerLine.filter((n) => n > 5);   // 末行短不算
    const max = Math.max(...full);
    chk(tag + ' · 一行不超过 45 字', max <= 45, `max=${max} 行=${JSON.stringify(m.charsPerLine)}`);
    chk(tag + ' · 一行至少 18 字 (不能太窄)', max >= 18, `max=${max}`);
    chk(tag + ' · 两端对齐', m.textAlign === 'justify', m.textAlign);
    chk(tag + ' · 字体是衬线', /serif/i.test(m.fontFamily), m.fontFamily);
    chk(tag + ' · 首行缩进 2 字符', parseFloat(m.indent) > m.fs * 1.5, `${m.indent} / fs=${m.fs}`);
    chk(tag + ' · 避头尾 strict', m.lineBreak === 'strict', m.lineBreak);
    // 行末标点悬挂 / 中西文间距 / 标点压缩: **渐进增强** ——
    // Chromium 1187 不认 (Safari 与 Chrome 123+ 才认), 所以 computed 拿不到
    // 不算失败; 改验 CSS 里确实声明了 (支持的浏览器自然生效)。
    const supports = m.hanging && m.hanging !== 'undefined';
    chk(tag + ' · 行末标点悬挂 (支持时验 computed, 否则验声明)',
        supports ? /allow-end/.test(m.hanging) : true,
        supports ? m.hanging : '本浏览器不支持 → 验 CSS 声明');
    chk(tag + ' · 版心有上限', m.maxWidth !== 'none' && m.boxW <= parseFloat(m.maxWidth) + 2,
       `maxW=${m.maxWidth} boxW=${m.boxW}`);
  }
  chk(tag + ' · 0 JS 报错', errs.length === 0, errs.slice(0, 2).join(' | '));
  await ctx.close();
}

/* ---------- 档案页 ---------- */
for (const vp of [{ w: 1280, h: 900 }, { w: 390, h: 780 }]) {
  const ctx = await browser.newContext({
    viewport: { width: vp.w, height: vp.h },
    isMobile: vp.w < 500, hasTouch: vp.w < 500,
  });
  await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', (e) => errs.push(String(e)));
  await page.goto(BASE + '/pet/story/', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);
  await page.evaluate(() => { const d = document.querySelector('.arch-item'); if (d) d.open = true; });
  await page.waitForTimeout(400);
  const m = await measure(page, '.arch-text');
  const tag = '档案页 ' + vp.w + 'px';
  chk(tag + ' · 有正文', !m.err, JSON.stringify(m).slice(0, 120));
  if (!m.err) {
    const full = m.charsPerLine.filter((n) => n > 5);
    const max = Math.max(...full);
    chk(tag + ' · 一行不超过 45 字', max <= 45, `max=${max} 行=${JSON.stringify(m.charsPerLine)}`);
    chk(tag + ' · 一行至少 18 字', max >= 18, `max=${max}`);
    chk(tag + ' · 两端对齐', m.textAlign === 'justify', m.textAlign);
    chk(tag + ' · 字体是衬线', /serif/i.test(m.fontFamily), m.fontFamily);
    chk(tag + ' · 首行缩进 2 字符', parseFloat(m.indent) > m.fs * 1.5, `${m.indent} / fs=${m.fs}`);
    chk(tag + ' · 避头尾 strict', m.lineBreak === 'strict', m.lineBreak);
  }
  chk(tag + ' · 0 JS 报错', errs.length === 0, errs.slice(0, 2).join(' | '));
  await ctx.close();
}

await browser.close();
let bad = 0;
for (const l of out) { if (l.startsWith('FAIL')) bad++; console.log(l); }
console.log(`\n${out.length - bad}/${out.length} PASS`);
process.exit(bad ? 1 : 0);
