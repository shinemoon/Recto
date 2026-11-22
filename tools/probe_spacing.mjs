// 版面呼吸: 相邻块之间不许贴死。
//
// 这一组断言来自一次真实的报障: 书架上那一格格子 (#shelf) 与它下面的
// 「上传到掌阅云」折叠区之间是 **0px** —— 两块贴在一起, 看着像同一块。
// 扫全站之后同类还有: 分页条 → 摘录列表 (0px)、摘录里的 来源行 → 正文 (2px)、
// 「导出到纸间」表单的标签与输入框挤在同一行。
//
// 所以这里把「哪两块之间至少留多少」钉住, 以后谁再改边距, 贴死了会当场红。
// 只写"最小间距", 不写具体像素实现 —— 皮肤疏密档 (compact/roomy) 改了也不该红。
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RECTO_BASE || 'http://127.0.0.1:5015';
const VP = (process.env.SP_VP || '1400x1000,390x844').split(',').map((s) => {
  const [w, h] = s.split('x').map(Number);
  return { w, h };
});

const res = [];
const ok = (name, cond, info) => res.push({ name, pass: !!cond, info });

const b = await chromium.launch();
const c = cookie();

for (const vp of VP) {
  const tag = `${vp.w}px`;
  const ctx = await b.newContext({ viewport: { width: vp.w, height: vp.h } });
  await ctx.addCookies([{ name: 'zy_webauth', value: c, domain: '127.0.0.1', path: '/' }]);
  const p = await ctx.newPage();

  // ---- 书架: 格子 → 上传折叠区
  await p.goto(BASE + '/#library', { waitUntil: 'domcontentloaded' });
  await p.waitForSelector('#shelf .bk', { timeout: 15000 });
  await p.waitForTimeout(400);
  const shelf = await p.evaluate(() => {
    const g = document.querySelector('#shelf');
    const z = document.querySelector('#sec-ident');
    if (!g || !z) return null;
    return {
      gap: Math.round((z.getBoundingClientRect().top - g.getBoundingClientRect().bottom) * 10) / 10,
      mb: getComputedStyle(g).marginBottom,
    };
  });
  ok(`${tag} 书架格子与上传折叠区之间 ≥14px`, shelf && shelf.gap >= 14, shelf);

  // ---- 摘录: 分页条 → 时间线 / 来源行 → 正文
  await p.goto(BASE + '/admin/?pane=excerpts', { waitUntil: 'domcontentloaded' });
  await p.waitForTimeout(1200);
  const ex = await p.evaluate(() => {
    const pager = document.querySelector('.pane-sec .pager');
    const tl = document.querySelector('.pane-sec .timeline');
    const item = document.querySelector('.tl-item');
    let metaGap = null;
    if (item) {
      const m = item.querySelector('.tl-meta');
      const t = item.querySelector('.tl-content');
      if (m && t) metaGap = Math.round((t.getBoundingClientRect().top - m.getBoundingClientRect().bottom) * 10) / 10;
    }
    return {
      pagerToTl: pager && tl ? Math.round((tl.getBoundingClientRect().top - pager.getBoundingClientRect().bottom) * 10) / 10 : null,
      metaToText: metaGap,
    };
  });
  ok(`${tag} 分页条与摘录列表之间 ≥14px`, ex.pagerToTl === null || ex.pagerToTl >= 14, ex);
  ok(`${tag} 摘录的来源行与正文之间 ≥6px`, ex.metaToText === null || ex.metaToText >= 6, ex);

  // ---- 导出表单: 标签与它自己的输入框上下排, 不并排
  await p.goto(BASE + '/admin/?pane=tools', { waitUntil: 'domcontentloaded' });
  await p.waitForTimeout(1200);
  const form = await p.evaluate(() => {
    const f = document.querySelector('#zhijian-form');
    if (!f) return null;
    const lab = Array.from(f.querySelectorAll('label')).find((l) => l.textContent.includes('书名'));
    const inp = f.querySelector('input[name="book_name"]');
    if (!lab || !inp) return null;
    const a = lab.getBoundingClientRect(), z = inp.getBoundingClientRect();
    return {
      stacked: z.top >= a.bottom - 1,
      gap: Math.round((z.top - a.bottom) * 10) / 10,
      sameRow: Math.abs(a.top - z.top) < 20,
    };
  });
  ok(`${tag} 表单标签在输入框上方 (不并排)`, form && form.stacked && !form.sameRow, form);
  ok(`${tag} 表单标签与输入框之间 ≥4px`, form && form.gap >= 4, form);

  // ---- 设置页: 单列块里的行有内边距 (不靠外边距撑)
  await p.goto(BASE + '/settings', { waitUntil: 'domcontentloaded' });
  await p.waitForTimeout(1200);
  const st = await p.evaluate(() => {
    const row = document.querySelector('.st-block > .st-row');
    if (!row) return null;
    const s = getComputedStyle(row);
    return { pt: parseFloat(s.paddingTop), h: Math.round(row.getBoundingClientRect().height) };
  });
  ok(`${tag} 设置页单列行有上下内边距`, st && st.pt >= 6, st);

  // ---- 概览: 小标题与图表/列表之间不贴
  await p.goto(BASE + '/admin/', { waitUntil: 'domcontentloaded' });
  await p.waitForTimeout(1200);
  const kv = await p.evaluate(() => {
    const k = document.querySelector('.pane-sec .kicker');
    if (!k) return null;
    let n = k.nextElementSibling, guard = 0;
    while (n && !n.getBoundingClientRect().height && guard++ < 5) n = n.nextElementSibling;
    if (!n) return null;
    return Math.round((n.getBoundingClientRect().top - k.getBoundingClientRect().bottom) * 10) / 10;
  });
  ok(`${tag} 概览小标题与下方图表/列表之间 ≥6px`, kv === null || kv >= 6, { gap: kv });

  await ctx.close();
}

await b.close();

let bad = 0;
for (const r of res) {
  if (!r.pass) bad++;
  console.log(`${r.pass ? 'PASS' : 'FAIL'} ${r.name} :: ${JSON.stringify(r.info)}`);
}
console.log(`\n${res.length - bad} passed, ${bad} failed`);
process.exit(bad ? 1 : 0);
