/**
 * 探针: 语料页「模型库」表格里 `.badge-warn`(推理) 是否推挤了输入框。
 *
 * 背景 (2026-11-04 用户): 「该页面的 badge-warn 会推挤输入框导致排布不整齐」
 * 上游 `input{width:100%}` + inline badge → badge 换行到输入框下面, 该行变高,
 * 与相邻行不齐。修法 = 该列统一预留右侧空间, badge 绝对定位在预留区里。
 *
 * 断言 (桌面 1400 / 手机 390 各跑一遍):
 *   1. 有 badge 的行, badge 不与输入框重叠 (bbox 不相交)
 *   2. badge 垂直中线 ≈ 输入框中线 (不换行、不悬空)
 *   3. 该列所有行的输入框右边缘一致 (= 列宽统一, 没有个别行被挤窄)
 *   4. 该列所有行的输入框宽度一致
 *   5. 有 badge 的行高度 == 无 badge 的行高度 (不撑高)
 *   6. 页面无横向溢出, 0 JS 报错
 */
import { chromium } from './_env.mjs';
import { BASE, cookieValue, skinSnapshot, restoreSkin } from './_skins.mjs';

const out = [];
let fail = 0;
const ok = (name, cond, extra = '') => {
  if (!cond) fail += 1;
  out.push(`${cond ? 'PASS' : 'FAIL'}  ${name}${extra ? '  — ' + extra : ''}`);
};

const run = async (page, viewport, label) => {
  const errs = [];
  page.on('pageerror', (e) => errs.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
  await page.goto(`${BASE}/corpus/`, { waitUntil: 'networkidle' });
  await page.waitForSelector('#model-rows tr', { timeout: 20000 });

  const data = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('#model-rows tr')];
    const r = (el) => { const b = el.getBoundingClientRect(); return { x: b.x, y: b.y, w: b.width, h: b.height, r: b.right, b: b.bottom }; };
    const wrap = document.querySelector('.models-wrap');
    return {
      overflowX: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      wrapScrollable: wrap ? wrap.scrollWidth > wrap.clientWidth + 1 : false,
      wrapSize: wrap ? { scroll: wrap.scrollWidth, client: wrap.clientWidth } : null,
      rows: rows.map((tr) => {
        const td = tr.children[5];               // 模型列
        const inp = td ? td.querySelector('input') : null;
        const badge = td ? td.querySelector('.badge-warn') : null;
        return {
          h: tr.getBoundingClientRect().height,
          input: inp ? r(inp) : null,
          badge: badge ? r(badge) : null,
        };
      }),
    };
  });

  const withBadge = data.rows.filter((x) => x.badge);
  const noBadge = data.rows.filter((x) => !x.badge);
  out.push(`[${label}] rows=${data.rows.length} badge=${withBadge.length}`);

  ok(`${label} 有 badge 行 (>=1)`, withBadge.length >= 1, `count=${withBadge.length}`);
  for (const [i, x] of withBadge.entries()) {
    const bi = x.badge; const ii = x.input;
    const overlap = !(bi.r <= ii.x + 0.5 || bi.x >= ii.r - 0.5 || bi.b <= ii.y + 0.5 || bi.y >= ii.b - 0.5);
    ok(`${label} badge#${i} 不与输入框重叠`, !overlap, `badge=${JSON.stringify(bi)} input=${JSON.stringify(ii)}`);
    const cyB = bi.y + bi.h / 2; const cyI = ii.y + ii.h / 2;
    ok(`${label} badge#${i} 与输入框中线对齐`, Math.abs(cyB - cyI) <= 3, `Δ=${(cyB - cyI).toFixed(1)}px`);
    ok(`${label} badge#${i} 不换行 (在输入框同一行)`, bi.y < ii.b - 2, `badge.y=${bi.y.toFixed(1)} input.bottom=${ii.b.toFixed(1)}`);
  }
  const rights = data.rows.map((x) => x.input && x.input.r).filter(Boolean);
  const widths = data.rows.map((x) => x.input && x.input.w).filter(Boolean);
  if (rights.length > 1) {
    ok(`${label} 该列输入框右边缘一致`, Math.max(...rights) - Math.min(...rights) <= 1,
      `spread=${(Math.max(...rights) - Math.min(...rights)).toFixed(1)}px`);
    ok(`${label} 该列输入框宽度一致`, Math.max(...widths) - Math.min(...widths) <= 1,
      `spread=${(Math.max(...widths) - Math.min(...widths)).toFixed(1)}px`);
  }
  if (label === '390') {
    ok('390 模型列输入框仍可用 (>=60px)', Math.min(...widths) >= 60, `min=${Math.min(...widths).toFixed(1)}px`);
    ok('390 表格在 .models-wrap 内横向滚动', data.wrapScrollable, JSON.stringify(data.wrapSize));
  }
  if (withBadge.length && noBadge.length) {
    const hs = data.rows.map((x) => x.h);
    ok(`${label} 所有行高度一致`, Math.max(...hs) - Math.min(...hs) <= 1, `spread=${(Math.max(...hs) - Math.min(...hs)).toFixed(1)}px`);
  }
  ok(`${label} 无横向溢出`, data.overflowX <= 0, `overflowX=${data.overflowX}`);
  ok(`${label} 无 JS 报错`, errs.length === 0, errs.slice(0, 3).join(' | '));
};

const snap = await skinSnapshot();
const browser = await chromium.launch();
try {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 1000 } });
  await ctx.addCookies([{ name: 'zy_webauth', value: cookieValue(), domain: '127.0.0.1', path: '/' }]);
  const page = await ctx.newPage();
  await run(page, 1400, '1400');
  await page.setViewportSize({ width: 390, height: 900 });
  await page.waitForTimeout(200);
  await run(page, 390, '390');
} finally {
  await browser.close();
  await restoreSkin(snap);
}

console.log(out.join('\n'));
console.log(fail ? `\n${fail} FAIL` : '\nALL PASS');
process.exit(fail ? 1 : 0);
