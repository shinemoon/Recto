/* 阅读器: 左右翻页模式下, 顶栏显隐时阅读区不能跳 */
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
const { execFileSync } = require('child_process');

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || 'a6f63af571f05bb4';
const token = execFileSync('python3', [new URL('./make_cookie.py', import.meta.url).pathname],
  { encoding: 'utf8' }).trim();

const out = [];
const chk = (name, cond, extra = '') =>
  out.push(`${cond ? 'ok  ' : 'FAIL'} ${name}${extra ? ' — ' + extra : ''}`);

const browser = await chromium.launch({ args: ['--no-sandbox'] });

const probe = (page) => page.evaluate(() => {
  const rv = document.getElementById('rv');
  const view = document.getElementById('rv-view');
  const p = document.querySelector('.rv-page p');
  const bar = document.querySelector('.rv-bar') || document.querySelector('header');
  const v = view.getBoundingClientRect();
  const pr = p ? p.getBoundingClientRect() : null;
  const br = bar.getBoundingClientRect();
  return {
    immersive: rv.classList.contains('is-immersive'),
    barHidden: bar.classList.contains('is-hidden'),
    barTop: Math.round(br.top), barH: Math.round(br.height),
    viewTop: Math.round(v.top), viewH: Math.round(v.height),
    pTop: pr ? Math.round(pr.top) : null,
    ind: (document.getElementById('rv-pageind') || {}).textContent || '',
    page: window.__RV__ && window.__RV__.page ? window.__RV__.page().page : null,
    count: window.__RV__ && window.__RV__.page ? window.__RV__.page().count : null,
    scrollY: Math.round(window.scrollY)
  };
});

for (const [w, h] of [[1280, 900], [390, 780]]) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h } });
  await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(String(e)));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
  await page.addInitScript(() => {
    if (localStorage.getItem('probe.still')) return;
    localStorage.setItem('probe.still', '1');
    localStorage.setItem('recto.mode', 'page');       // 直接以翻页模式打开
    ['fs', 'lh', 'para', 'pad', 'w', 'font'].forEach(k => localStorage.removeItem('recto.' + k));
  });
  await page.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.rv-page p', { timeout: 20000 });
  await page.waitForTimeout(1200);

  const tag = `${w}x${h}`;
  const a = await probe(page);
  chk(`[${tag}] 进书即翻页模式`, a.viewH > 200 && a.count > 1, `pageH=${a.viewH} count=${a.count}`);

  // 点正文 = 收起顶栏
  await page.mouse.click(Math.round(w * 0.5), Math.round(h * 0.55));
  await page.waitForTimeout(600);
  const b = await probe(page);
  chk(`[${tag}] 点正文后顶栏收起`, b.barHidden && b.immersive,
      `hidden=${b.barHidden} immersive=${b.immersive} barTop=${b.barTop}`);

  // 核心: 阅读区原地不动
  chk(`[${tag}] 阅读区不跳 (view.top 不变)`, Math.abs(b.viewTop - a.viewTop) <= 1,
      `${a.viewTop} → ${b.viewTop}`);
  chk(`[${tag}] 正文第一行不跳`, a.pTop !== null && Math.abs(b.pTop - a.pTop) <= 1,
      `${a.pTop} → ${b.pTop}`);
  chk(`[${tag}] 页高 / 页码 / 当前页都没变`,
      b.viewH === a.viewH && b.ind === a.ind && b.page === a.page,
      `h ${a.viewH}/${b.viewH} ind "${a.ind}"/"${b.ind}" pg ${a.page}/${b.page}`);
  chk(`[${tag}] 阅读区没盖住顶栏位置 (顶栏只是自己收起)`, b.viewTop >= 0,
      `viewTop=${b.viewTop}`);

  // 再点一下 = 顶栏回来, 也不能跳
  await page.mouse.click(Math.round(w * 0.5), Math.round(h * 0.55));
  await page.waitForTimeout(600);
  const c = await probe(page);
  chk(`[${tag}] 顶栏回来后阅读区仍在原位`,
      !c.barHidden && Math.abs(c.viewTop - a.viewTop) <= 1
      && Math.abs(c.pTop - a.pTop) <= 1,
      `view ${a.viewTop}→${c.viewTop} p ${a.pTop}→${c.pTop}`);
  chk(`[${tag}] 来回切换后页码不漂`, c.page === a.page && c.count === a.count,
      `${a.page}/${a.count} → ${c.page}/${c.count}`);

  // 翻页仍然正常 (别为了不跳把翻页弄坏)
  await page.keyboard.press('ArrowRight');
  await page.waitForTimeout(500);
  const d = await probe(page);
  chk(`[${tag}] 翻页仍可用 (页码 +1)`, d.page === a.page + 1, `${a.page} → ${d.page}`);
  chk(`[${tag}] 翻页后阅读区顶端不变`, Math.abs(d.viewTop - a.viewTop) <= 1,
      `${a.viewTop} → ${d.viewTop}`);

  chk(`[${tag}] 0 JS 报错`, errs.length === 0, errs.slice(0, 2).join(' | '));
  await ctx.close();
}

await browser.close();
console.log(out.join('\n'));
const fails = out.filter((l) => l.startsWith('FAIL')).length;
console.log(`\n${out.length - fails} ok / ${fails} FAIL`);
process.exit(fails ? 1 : 0);
