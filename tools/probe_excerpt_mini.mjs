// 摘录面板:「只记录了位置 / 无正文」的条目显示简化 —— 几何级验证。
//
//   node tools/probe_excerpt_mini.mjs
//
// 断言:
//   1. mini 条目 (只有一行灰字说明) 明显比有正文的条目矮
//   2. mini 条目里没有「书票 / 复制」按钮, 也没有 "0 字"
//   3. 有正文的条目按钮齐全
//   4. 390px 窄屏不横向溢出, 说明文字不撑破卡片
//   5. 0 JS 报错
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RECTO_BASE || 'http://127.0.0.1:5015';
const C = await cookie();
const out = [];
const bad = [];
const ok = (n, c, x = '') => (c ? out.push(`ok   ${n}`) : bad.push(`FAIL ${n} ${x}`));

const browser = await chromium.launch();
try {
  for (const [w, h] of [[1400, 900], [390, 780]]) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h } });
    await ctx.addCookies([{ name: 'zy_webauth', value: C, domain: '127.0.0.1', path: '/' }]);
    const page = await ctx.newPage();
    const errs = [];
    page.on('pageerror', (e) => errs.push(String(e)));
    const Q = process.env.EX_Q ? '&book_q=' + encodeURIComponent(process.env.EX_Q) : '';
    await page.goto(`${BASE}/admin/?pane=excerpts` + Q, { waitUntil: 'load' });
    await page.waitForSelector('.tl-item', { timeout: 20000 });

    const data = await page.evaluate(() => {
      const items = [...document.querySelectorAll('.tl-item')].slice(0, 30);
      return items.map((it) => {
        const content = it.querySelector('.tl-content');
        const mini = it.querySelector('.tl-mini');
        const txt = (content?.textContent || '').trim();
        return {
          h: Math.round(it.getBoundingClientRect().height),
          mini: !!mini,
          text: txt.slice(0, 24),
          chars: txt.length,
          bookplate: !!it.querySelector('a[href*="bookplate"]'),
          copy: !!it.querySelector('button.btn-link'),
          del: !!it.querySelector('a[href*="/admin/api/delete"]'),
          meta: (it.querySelector('.tl-meta')?.textContent || '').replace(/\s+/g, ' ').trim(),
          overflow: it.scrollWidth - it.clientWidth,
        };
      });
    });

    const minis = data.filter((d) => d.mini);
    const full = data.filter((d) => !d.mini);
    ok(`${w}: 有 mini 条目`, minis.length > 0, `${minis.length}`);
    ok(`${w}: mini 条目无书票`, minis.every((d) => !d.bookplate), JSON.stringify(minis.find((d) => d.bookplate)));
    ok(`${w}: mini 条目无复制`, minis.every((d) => !d.copy));
    ok(`${w}: mini 条目仍可删除`, minis.every((d) => d.del));
    ok(`${w}: mini 条目没有 "0 字"`, minis.every((d) => !/·\s*0\s*字/.test(d.meta)),
       minis.map((d) => d.meta).find((m) => /·\s*0\s*字/.test(m)) || '');
    if (full.length) {
      const avgMini = minis.reduce((a, d) => a + d.h, 0) / (minis.length || 1);
      const avgFull = full.reduce((a, d) => a + d.h, 0) / full.length;
      ok(`${w}: mini 比有正文的矮`, avgMini < avgFull, `${avgMini.toFixed(0)} vs ${avgFull.toFixed(0)}`);
      ok(`${w}: 有正文条目按钮齐全`,
         full.some((d) => d.bookplate && d.copy && d.del));
    } else {
      out.push(`ok   ${w}: 本页没有"有正文"的条目 (跳过对比)`);
    }
    ok(`${w}: 条目内无横向溢出`, data.every((d) => d.overflow <= 1),
       JSON.stringify(data.filter((d) => d.overflow > 1).slice(0, 2)));
    const docOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    ok(`${w}: 页面不横向溢出`, docOverflow <= 1, `${docOverflow}`);
    ok(`${w}: 0 JS 报错`, errs.length === 0, errs.join(' | '));
    await ctx.close();
  }
} finally {
  await browser.close();
}

console.log(out.join('\n'));
if (bad.length) {
  console.log(bad.join('\n'));
  console.log(`\nFAIL ${bad.length} / PASS ${out.length}`);
  process.exit(1);
}
console.log(`\nALL PASS (${out.length})`);
