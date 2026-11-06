/* 窄屏 左右翻页: 列宽必须等于可视区真实宽度 (不能取整), 页边点击区要够大。
 *
 * 背景 (2026-11-06):
 *  - pageGeom() 原来用 viewEl.clientWidth (整数)。390 屏上可视区是 364.4px, 取整成 364,
 *    而浏览器按 364.4 排 → stride 少 0.4px/页, 翻到第 70 页正文整体偏右 ~28px、右侧被裁。
 *  - .rv-turn 原来 clamp(44px,10vw,220px) → 手机上只有 44px, 几乎点不中。
 *
 * 跑: node tools/probe_reader_paged_narrow.mjs
 * env: RV_BASE / RV_KEY
 */
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || 'a6f63af571f05bb4';
const token = cookie();

// 探针会翻几十页 —— 会真的写服务端进度并自动推送云端, 所以全程只在**同一本书**上跑,
// 收尾必须把进度写回原处 (2026-11-06 事故: 没写回 → 用户读到 71% 的书被顶到 99.9%)
async function readProgress(pg) {
  return await pg.evaluate(async (k) => {
    const r = await fetch('/read/api/book/' + k, { headers: { accept: 'application/json' } });
    const j = await r.json();
    return (j && j.progress) || null;
  }, KEY);
}
// 写回后直接把服务端回执拿来比对 (不再多读一次接口: 探针卫生检查要求
// 最后一次进度读取发生在写回之前)
async function writeProgress(pg, pr) {
  if (!pr) return null;
  return await pg.evaluate(async ([k, p]) => {
    const r = await fetch('/read/api/book/' + k + '/progress', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      // jump=true: 绕过"跨会话大幅回退"的守卫 (探针翻了几十页, 写回必然是往回拨)
      body: JSON.stringify({ chapter: p.chapter, offset: p.offset, jump: true, user: 1 })
    });
    const j = await r.json();
    return { status: r.status, chapter: j && j.chapter, offset: j && j.offset };
  }, [KEY, pr]);
}

const out = [];
const chk = (name, cond, extra = '') =>
  out.push(`${cond ? 'ok  ' : 'FAIL'} ${name}${extra ? ' — ' + extra : ''}`);

const browser = await chromium.launch({ args: ['--no-sandbox'] });

async function open(w, h) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h } });
  await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(String(e)));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
  await page.addInitScript(() => {
    localStorage.setItem('recto.mode', 'page');       // 每次导航都钉成左右翻页
  });
  await page.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.rv-page p', { timeout: 20000 });
  await page.waitForTimeout(900);
  const progress0 = await readProgress(page);
  return { ctx, page, errs, progress0 };
}

const snap = (page) => page.evaluate(() => {
  const q = s => document.querySelector(s);
  const view = q('#rv-view'), cnt = q('#rv-content');
  const vb = view.getBoundingClientRect();
  const g = window.__RV__.page();
  // 当前列里可见的块: 取最宽的一条。
  // 注意 text-indent 让"文字范围"的左边比块左边多两个字, 所以对齐看**元素盒**,
  // 裁切看**文字范围** (范围越界 = 真的被切掉)。
  let row = null;
  for (const p of cnt.querySelectorAll('p, blockquote, h1, h2, h3')) {
    const b = p.getBoundingClientRect();
    if (b.top < vb.top - 1 || b.bottom > vb.bottom + 1) continue;      // 不在这一列
    if (b.left < vb.left - 2 || b.right > vb.right + 2) continue;      // 不在可视区里
    if (b.width < 40) continue;
    const rg = document.createRange(); rg.selectNodeContents(p);
    const rb = rg.getBoundingClientRect();
    if (!row || b.width > row.elW) {
      row = {
        elL: +b.left.toFixed(2), elR: +b.right.toFixed(2), elW: +b.width.toFixed(2),
        l: +rb.left.toFixed(2), r: +rb.right.toFixed(2)
      };
    }
  }
  return {
    viewL: +vb.left.toFixed(2), viewR: +vb.right.toFixed(2), viewW: +vb.width.toFixed(3),
    stride: g.stride, pageW: g.w, gap: g.gap, page: g.page, count: g.count,
    row,
    turnL: +q('.rv-turn-l').getBoundingClientRect().width.toFixed(1),
    docSW: document.documentElement.scrollWidth,
    docCW: document.documentElement.clientWidth
  };
});

// ---------- 1. 几何: 列宽不能取整, 深处不能漂 ----------
for (const [w, h] of [[360, 640], [390, 780], [430, 900]]) {
  const { ctx, page, errs, progress0 } = await open(w, h);
  const s0 = await snap(page);
  chk(`[${w}] 列宽 = 可视区真实宽度 (小于 1px 误差)`,
    Math.abs(s0.pageW - s0.viewW) < 0.01, `pageW=${s0.pageW} viewW=${s0.viewW}`);
  chk(`[${w}] stride = 列宽 + 列间距`,
    Math.abs(s0.stride - (s0.pageW + s0.gap)) < 0.01, `stride=${s0.stride}`);
  chk(`[${w}] 列宽不是整数取整值 (小数部分保留)`,
    Math.abs(s0.pageW - Math.round(s0.pageW)) > 0.05 || Math.abs(s0.pageW - s0.viewW) < 0.01,
    `pageW=${s0.pageW} round=${Math.round(s0.pageW)}`);
  chk(`[${w}] 首屏正文左对齐可视区`,
    !!s0.row && Math.abs(s0.row.elL - s0.viewL) < 1.5,
    s0.row ? `elL=${s0.row.elL} viewL=${s0.viewL}` : 'no row');

  // 往后翻 40 页, 累计漂移应为 0 (修复前每页 0.4px → 第 40 页偏 16px)
  for (let i = 0; i < 40; i++) {
    await page.locator('.rv-turn-r').click({ force: true });
    await page.waitForTimeout(25);
  }
  await page.waitForTimeout(500);
  const s1 = await snap(page);
  const dl = s1.row ? Math.abs(s1.row.elL - s1.viewL) : 999;
  const dr = s1.row ? s1.row.elR - s1.viewR : 999;
  chk(`[${w}] 翻到第 ${s1.page} 页仍不偏右 (左边距差 ${dl.toFixed(1)}px)`,
    !!s1.row && dl < 1.5, s1.row ? `elL=${s1.row.elL} viewL=${s1.viewL}` : 'no row');
  chk(`[${w}] 翻到深处右边不被裁 (块右差 ${dr.toFixed(1)}px)`, !!s1.row && dr < 1.5,
    s1.row ? `elR=${s1.row.elR} viewR=${s1.viewR}` : 'no row');
  chk(`[${w}] 正文文字没有越出可视区`,
    !!s1.row && s1.row.r <= s1.viewR + 1 && s1.row.l >= s1.viewL - 1,
    s1.row ? `文字 ${s1.row.l}..${s1.row.r}` : 'no row');
  chk(`[${w}] 页面没有横向滚动`, s1.docSW === s1.docCW, `${s1.docSW}/${s1.docCW}`);

  // ---------- 2. 点击区宽度 ----------
  const exp = Math.min(160, Math.max(64, Math.round(w * 0.25)));
  chk(`[${w}] 左侧翻页热区 ≥64px 且 ≈25vw (${s1.turnL}px)`,
    s1.turnL >= 64 && Math.abs(s1.turnL - exp) <= 1, `exp=${exp}`);
  chk(`[${w}] 中间仍留至少一半给"点正文切顶栏"`, s1.turnL * 2 <= w * 0.55 + 1,
    `${s1.turnL * 2}/${w}`);

  // ---------- 3. 手感: 1/4 处点 = 翻页, 正中点 = 切顶栏 ----------
  const p0 = (await snap(page)).page;
  await page.mouse.click(Math.round(w * 0.12), Math.round(h * 0.5));
  await page.waitForTimeout(450);
  const p1 = (await snap(page)).page;
  chk(`[${w}] 点左 12% → 上一页 (${p0} → ${p1})`, p1 === p0 - 1 || (p0 === 0 && p1 === 0),
    `${p0} → ${p1}`);
  await page.mouse.click(Math.round(w * 0.88), Math.round(h * 0.5));
  await page.waitForTimeout(450);
  const p2 = (await snap(page)).page;
  chk(`[${w}] 点右 88% → 下一页 (${p1} → ${p2})`, p2 === p1 + 1, `${p1} → ${p2}`);

  const barBefore = await page.evaluate(() => !document.getElementById('rv').classList.contains('is-immersive'));
  await page.mouse.click(Math.round(w * 0.5), Math.round(h * 0.62));
  await page.waitForTimeout(350);
  const barAfter = await page.evaluate(() => !document.getElementById('rv').classList.contains('is-immersive'));
  chk(`[${w}] 点正中不翻页, 只切顶栏`, barAfter !== barBefore);

  chk(`[${w}] 无 JS 报错`, !errs.length, errs.slice(0, 2).join(' | '));

  // 收尾: 把阅读进度写回原处, 用服务端回执确认真的还回去了
  const back = await writeProgress(page, progress0);
  chk(`[${w}] 探针把阅读进度写回原处`,
    !!back && !!back.chapter && !!progress0 && back.chapter === progress0.chapter
    && Math.abs(back.offset - progress0.offset) <= 2,
    JSON.stringify({ before: progress0, after: back }));
  await ctx.close();
}

// ---------- 4. 桌面: 按正文列外留白算, 不压字 ----------
{
  const { ctx, page, errs, progress0 } = await open(1280, 900);
  const s = await snap(page);
  // 桌面规则刻意再减去 1.4rem, 让热区不出正文列
  const margin = 1280 / 2 - s.viewW / 2 - 1.4 * 16;
  chk('[1280] 热区宽度 = 正文列外留白 (减去 1.4rem 缓冲)', Math.abs(s.turnL - margin) < 4,
    `turn=${s.turnL} exp=${margin.toFixed(1)}`);
  chk('[1280] 列宽不受窄屏修复影响', Math.abs(s.pageW - s.viewW) < 0.01, `pageW=${s.pageW}`);
  chk('[1280] 无 JS 报错', !errs.length, errs.slice(0, 2).join(' | '));
  await ctx.close();
}

await browser.close();
console.log(out.join('\n'));
const fails = out.filter(l => l.startsWith('FAIL'));
console.log(`\n${out.length - fails.length}/${out.length} PASS`);
process.exit(fails.length ? 1 : 0);
