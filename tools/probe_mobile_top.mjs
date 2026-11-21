/* 移动端界面优化 (2026-11-21): 顶栏自动隐藏 + 窄屏顶栏压成两行 + 底部不再被裁。
 *
 * 验的事:
 *   1. 窄屏顶栏从三行 (品牌/导航/用户) 压成**两行** (品牌+用户 / 导航)
 *   2. 下滑 → 顶栏收起; 上滑 → 回来; 回顶 → 可见 (收起只是 transform, 正文不跳)
 *   3. 收起时正文**不跟着跳** (main.top 不变)
 *   4. 顶部有一条 2px 进度细线 (收起时可见)
 *   5. 底部: 书架封面不再裁掉一截; 各分区内容不被 overflow:hidden 裁掉
 *   6. 阅读器: 滚动模式第一行不被浮层顶栏压住; 翻页模式最后一行不被页码/小图标压住
 *   7. 全程 0 JS 报错 + 页面无横向溢出
 *
 * 跑法: node tools/probe_mobile_top.mjs      (RECTO_BASE 可覆盖)
 */
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RECTO_BASE || 'http://127.0.0.1:5015';
const token = cookie();
const out = [];
const chk = (name, cond, extra = '') =>
  out.push(`${cond ? 'PASS' : 'FAIL'} ${name}${extra ? ' :: ' + extra : ''}`);

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({
  viewport: { width: 390, height: 780 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2,
});
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

/* ---------- 1. 窄屏顶栏: 两行 ---------- */
await page.goto(BASE + '/', { waitUntil: 'networkidle' });
await page.waitForTimeout(900);
const top0 = await page.evaluate(() => {
  const topIn = document.querySelector('.rc-top-in');
  // 行数: 把三个元素的**几何**归成行 (按 top 分组, 差 < 一半高度算同一行)
  const els = [
    { n: 'brand', r: document.querySelector('.rc-brand').getBoundingClientRect() },
    { n: 'user', r: document.querySelector('.rc-user').getBoundingClientRect() },
    { n: 'nav', r: document.querySelector('.rc-nav').getBoundingClientRect() },
  ];
  const rowTops = [];
  for (const e of els) {
    const t = Math.round(e.r.top);
    if (!rowTops.some((x) => Math.abs(x - t) < Math.max(12, e.r.height / 2))) rowTops.push(t);
  }
  const rows = rowTops.length;
  const brand = els[0].r, user = els[1].r, nav = els[2].r;
  return {
    rows,
    topH: Math.round(document.querySelector('.rc-top').getBoundingClientRect().height),
    brandTop: Math.round(brand.top), userTop: Math.round(user.top),
    navTop: Math.round(nav.top),
    navOverflows: document.querySelector('.rc-nav').scrollWidth > document.querySelector('.rc-nav').clientWidth + 1,
    overflowX: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  };
});
chk('窄屏顶栏只有两行 (品牌+用户一行, 导航一行)', top0.rows === 2, JSON.stringify(top0));
chk('品牌与用户同一行', Math.abs(top0.brandTop - top0.userTop) < 12,
    `${top0.brandTop} vs ${top0.userTop}`);
chk('导航在第二行', top0.navTop > Math.max(top0.brandTop, top0.userTop), String(top0.navTop));
chk('顶栏高度 ≤ 110px (原来 146px)', top0.topH <= 110, top0.topH + 'px');
chk('页面无横向溢出', top0.overflowX <= 0, String(top0.overflowX));

/* ---------- 2/3/4. 自动隐藏 ---------- */
const st = async () => await page.evaluate(() => {
  const top = document.querySelector('.rc-top');
  const main = document.querySelector('.rc-main');
  const prog = document.querySelector('.rc-top-prog');
  const mr = main.getBoundingClientRect();
  return {
    tucked: top.classList.contains('is-tucked'),
    topBottom: Math.round(top.getBoundingClientRect().bottom),
    // 文档坐标 (main.top 随滚动变, 要比"文档里的位置"才说明没跳)
    mainDocTop: Math.round(mr.top + window.scrollY),
    scrollY: Math.round(window.scrollY),
    progW: prog ? prog.style.width : null,
    progOpacity: prog ? +getComputedStyle(prog).opacity : null,
  };
});
const a = await st();
chk('初始顶栏可见', !a.tucked, JSON.stringify(a));

await page.evaluate(() => window.scrollTo(0, 500));
await page.waitForTimeout(600);
const b = await st();
chk('下滑后顶栏收起', b.tucked, JSON.stringify(b));
chk('收起时顶栏完全移出视口', b.topBottom <= 1, String(b.topBottom));
chk('收起不改变正文位置 (文档坐标不变)', Math.abs(b.mainDocTop - a.mainDocTop) <= 1,
    `${a.mainDocTop} → ${b.mainDocTop}`);
chk('收起时顶部进度线可见', b.progOpacity > 0.3, String(b.progOpacity));
chk('进度线宽度随滚动增长', parseFloat(b.progW) > 0, b.progW);

await page.evaluate(() => window.scrollTo(0, 300));
await page.waitForTimeout(600);
const c = await st();
chk('上滑后顶栏回来', !c.tucked, JSON.stringify(c));

await page.evaluate(() => window.scrollTo(0, 0));
await page.waitForTimeout(600);
const d = await st();
chk('回到顶部顶栏可见', !d.tucked, JSON.stringify(d));
chk('回顶后正文位置复原', Math.abs(d.mainDocTop - a.mainDocTop) <= 1, `${a.mainDocTop} → ${d.mainDocTop}`);

/* ---------- 5. 底部: 各分区不被裁 ---------- */
const PAGES = [['书架', '/'], ['摘录', '/admin/'], ['摘录书', '/admin/?pane=books'],
  ['摘录工具', '/admin/?pane=tools'], ['语料', '/corpus/'], ['书宠', '/pet/'],
  ['书宠档案', '/pet/story/'], ['外观', '/appearance'], ['设置', '/settings']];
for (const [label, path] of PAGES) {
  await page.goto(BASE + path, { waitUntil: 'networkidle' });
  await page.waitForTimeout(800);
  await page.evaluate(() => document.querySelectorAll('details').forEach((d) => { d.open = true; }));
  await page.waitForTimeout(400);
  const m = await page.evaluate(() => {
    const seen = new Set(); const out = [];
    document.querySelectorAll('body *').forEach((e) => {
      const s = getComputedStyle(e);
      if (s.display === 'none' || s.visibility === 'hidden') return;
      let a2 = e.parentElement, clipper = null, scrollable = false;
      while (a2 && a2 !== document.body) {
        if (a2.tagName === 'DETAILS' && !a2.open) { scrollable = true; break; }
        const as = getComputedStyle(a2);
        const hid = as.overflowY === 'hidden' || as.overflow === 'hidden';
        const scr = as.overflowY === 'auto' || as.overflowY === 'scroll' ||
          as.overflow === 'auto' || as.overflow === 'scroll';
        if (hid) { clipper = a2; break; }
        if (scr && a2.scrollHeight > a2.clientHeight + 1) { scrollable = true; break; }
        a2 = a2.parentElement;
      }
      if (!clipper || scrollable) return;
      const r = e.getBoundingClientRect(); const cr = clipper.getBoundingClientRect();
      if (r.height === 0) return;
      if (r.bottom > cr.bottom + 1) {
        const k = (e.className || e.tagName) + '|' + (clipper.className || clipper.tagName);
        if (seen.has(k)) return; seen.add(k);
        out.push({ el: (e.className || e.tagName).toString().slice(0, 24), over: Math.round(r.bottom - cr.bottom) });
      }
    });
    return { n: out.length, sample: out.slice(0, 3), overflowX: document.documentElement.scrollWidth - document.documentElement.clientWidth };
  });
  chk(`${label} · 内容不被裁`, m.n === 0, JSON.stringify(m.sample));
  chk(`${label} · 无横向溢出`, m.overflowX <= 0, String(m.overflowX));
}

/* 书架封面: 图片铺满封面框, 不裁掉一截 */
await page.goto(BASE + '/', { waitUntil: 'networkidle' });
await page.waitForTimeout(900);
const covers = await page.evaluate(() => [...document.querySelectorAll('.bk-cover')].map((c) => {
  const img = c.querySelector('img');
  if (!img) return null;
  const cr = c.getBoundingClientRect(); const ir = img.getBoundingClientRect();
  return { coverH: Math.round(cr.height), imgH: Math.round(ir.height), over: Math.round(ir.bottom - cr.bottom) };
}).filter(Boolean));
chk('书架封面图片不溢出封面框',
    covers.length > 0 && covers.every((c) => c.over <= 1),
    JSON.stringify(covers.slice(0, 4)));

/* ---------- 6. 阅读器: 底部/顶部不被压住 ---------- */
await page.goto(BASE + '/', { waitUntil: 'networkidle' });
await page.waitForTimeout(800);
const href = await page.evaluate(() => {
  const el = document.querySelector('.bk[href^="/read/"]');
  return el ? el.getAttribute('href') : null;
});
if (href) {
  // 滚动模式: 第一行不被浮层顶栏压住
  await page.goto(BASE + href, { waitUntil: 'domcontentloaded' });
  await page.evaluate(() => localStorage.setItem('recto.mode', 'scroll'));
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(2200);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(400);
  const sc = await page.evaluate(() => {
    const bar = document.querySelector('#rv-bar').getBoundingClientRect();
    const pageEl = document.querySelector('.rv-page');
    let first = null;
    const w = document.createTreeWalker(pageEl, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = w.nextNode())) {
      if (!(n.nodeValue || '').trim()) continue;
      const rg = document.createRange(); rg.selectNodeContents(n);
      for (const r of rg.getClientRects()) {
        if (r.width < 2 || r.height < 2) continue;
        if (!first || r.top < first.t) first = { t: r.top, txt: n.nodeValue.trim().slice(0, 12) };
      }
    }
    return { barBottom: Math.round(bar.bottom), firstTop: first ? Math.round(first.t) : null, txt: first ? first.txt : null };
  });
  chk('阅读器(滚动) 第一行不被浮层顶栏压住',
      sc.firstTop !== null && sc.firstTop >= sc.barBottom,
      `firstTop=${sc.firstTop} barBottom=${sc.barBottom}`);

  // 翻页模式: 最后一行不被页码/小图标压住
  await page.evaluate(() => localStorage.setItem('recto.mode', 'page'));
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(2400);
  let worst = -9999;
  for (let k = 0; k < 10; k++) {
    const m = await page.evaluate(() => {
      const view = document.querySelector('.rv-view');
      const vr = view.getBoundingClientRect();
      const ind = document.querySelector('#rv-pageind').getBoundingClientRect();
      const petEl = document.querySelector('#rv-pet');
      const pet = petEl && !petEl.hidden ? petEl.getBoundingClientRect() : null;
      let low = null;
      const w = document.createTreeWalker(document.querySelector('.rv-page'), NodeFilter.SHOW_TEXT);
      let n;
      while ((n = w.nextNode())) {
        if (!(n.nodeValue || '').trim()) continue;
        const rg = document.createRange(); rg.selectNodeContents(n);
        for (const r of rg.getClientRects()) {
          if (r.width < 2 || r.height < 2) continue;
          if (r.left < vr.left - 2 || r.right > vr.right + 2) continue;
          if (r.bottom > vr.bottom + 1 || r.top < vr.top - 1) continue;
          if (!low || r.bottom > low.b) low = { b: r.bottom, txt: n.nodeValue.trim().slice(0, 10) };
        }
      }
      if (!low) return null;
      return {
        over: Math.max(low.b - ind.top, pet ? low.b - pet.top : -9999),
        txt: low.txt,
      };
    });
    if (m && m.over > worst) worst = m.over;
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(500);
  }
  chk('阅读器(翻页) 最后一行不被页码/小图标压住', worst <= 1, `worst=${worst}px`);
}

chk('全程 0 JS 报错', errors.length === 0, errors.slice(0, 3).join(' | '));

await browser.close();
let bad = 0;
for (const l of out) {
  if (l.startsWith('FAIL')) bad++;
  console.log(l);
}
console.log(`\n${out.length - bad}/${out.length} PASS`);
process.exit(bad ? 1 : 0);
