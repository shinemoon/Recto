/* 点划线 → 就地看笔记: **必须按章匹配**。
 *
 * 事故(2026-11-17): 在「建安二十二年的瘟疫」(第 10 篇) 第一页点一下普通正文,
 * 弹出了第 8 篇「人背负着世界的恶意生活」的笔记弹窗 —— 因为字节偏移是**章内**的,
 * 旧代码只拿偏移去比全书的笔记, 第 8 篇那条刚好落在 1555~1945, 而第一页正在这个区间。
 *
 * 断言:
 *   ① 在第 10 篇第一页点普通正文 → 不弹笔记框 (也不弹别的浮层)
 *   ② 在第 8 篇点中那条划线 → 弹出笔记框, 且章名是「人背负着世界的恶意生活」
 *   ③ 第 8 篇点空白正文 (不在任何划线里) → 不弹
 *
 * 跑法: RV_BASE=http://127.0.0.1:5015 node tools/probe_reader_note_click.mjs
 */
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
const { execFileSync } = require('child_process');

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || 'da7e470fa0c2c4b8';   // 九诗心
const CH_PLAIN = +(process.env.RV_CH_PLAIN || 10);        // 建安二十二年的瘟疫
const CH_NOTE = +(process.env.RV_CH_NOTE || 8);           // 人背负着世界的恶意生活
const LABEL = process.env.RV_LABEL || '人背负着世界的恶意生活';

const token = execFileSync('python3', [new URL('./make_cookie.py', import.meta.url).pathname],
  { encoding: 'utf8' }).trim();

const out = [];
const chk = (name, cond, extra = '') =>
  out.push(`${cond ? 'ok  ' : 'FAIL'} ${name}${extra ? ' — ' + extra : ''}`);

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
page.setDefaultTimeout(10000);
const errs = [];
page.on('pageerror', e => errs.push(String(e)));
page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
await page.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('.rv-page p', { timeout: 30000 });
await page.waitForTimeout(800);

/* 进度快照 (翻章会写进度, 收尾必须还原 —— tests/test_probe_hygiene.py) */
async function readProgress() {
  return page.evaluate(async () => {
    const key = location.pathname.split('/').pop();
    const j = await (await fetch('/read/api/book/' + key)).json();
    return (j && j.progress) || { chapter: 0, offset: 0 };
  });
}
async function writeProgress(p) {
  return page.evaluate(async prog => {
    const key = location.pathname.split('/').pop();
    await fetch('/read/api/book/' + key + '/progress', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chapter: prog.chapter || 0, offset: prog.offset || 0 })
    });
  }, p);
}
const prog0 = await readProgress();

async function openChapter(ch) {
  await page.evaluate(c => {
    const a = document.querySelector('#rv-toc a[data-ch="' + c + '"]')
           || document.querySelector('[data-ch="' + c + '"]');
    if (a) a.click();
  }, String(ch));
  await page.waitForTimeout(1500);
  const cur = await page.evaluate(() => (window.__RV__ && __RV__.cursor()) || null);
  return cur;
}

/* 页内一段可见正文的中心点 (避开左右翻页热区: 取版心中间 60% 那一列) */
async function textPoint(ch) {
  return page.evaluate(c => {
    const vw = window.innerWidth, vh = window.innerHeight;
    const blks = [...document.querySelectorAll('.rv-ch[data-ch="' + c + '"] .rv-blk')];
    for (const b of blks) {
      const r = b.getBoundingClientRect();
      if (r.width < 40 || r.height < 10) continue;
      const x = Math.round((r.left + r.right) / 2);
      const y = Math.round(r.top + Math.min(r.height - 4, 12));
      if (x < vw * 0.3 || x > vw * 0.7) continue;
      if (y < 80 || y > vh - 80) continue;
      const el = document.elementFromPoint(x, y);
      if (el && (el.closest('.rv-bar') || el.closest('.rv-turn') || el.closest('.rv-fn'))) continue;
      return { x, y, off: +b.dataset.off };
    }
    return null;
  }, ch);
}

/* 页内某条划线的第一块落在视口里的位置 */
async function markPoint(ch) {
  return page.evaluate(c => {
    const names = ['yellow', 'green', 'blue', 'pink'];
    const vh = window.innerHeight;
    for (const n of names) {
      const h = window.CSS && CSS.highlights && CSS.highlights.get('rv-hl-' + n);
      if (!h) continue;
      for (const r of h) {
        const host = r.startContainer.parentElement &&
          r.startContainer.parentElement.closest('.rv-ch');
        if (!host || +host.dataset.ch !== c) continue;
        const rects = [...r.getClientRects()];
        for (const rc of rects) {
          if (rc.width < 4 || rc.height < 4) continue;
          const y = Math.round(rc.top + rc.height / 2);
          if (y < 90 || y > vh - 60) continue;
          return { x: Math.round(rc.left + Math.min(rc.width / 2, 40)), y };
        }
      }
    }
    return null;
  }, ch);
}

async function popup() {
  return page.evaluate(() => {
    const nm = document.querySelector('.rv-nmanage');
    const np = document.querySelector('.rv-npop');
    return {
      manage: !!(nm && !nm.hidden),
      manageMeta: nm ? (nm.querySelector('.rv-nm-m') || {}).textContent || '' : '',
      manageQuote: nm ? (nm.querySelector('.rv-nm-q') || {}).textContent || '' : '',
      pop: !!(np && !np.hidden)
    };
  });
}
async function clearPopups() {
  await page.evaluate(() => {
    const nm = document.querySelector('.rv-nmanage');
    if (nm) nm.hidden = true;
    const np = document.querySelector('.rv-npop');
    if (np) np.hidden = true;
  });
}

/* ---------- ① 第 10 篇第一页 → 点普通正文, 不该弹笔记 ---------- */
const cur10 = await openChapter(CH_PLAIN);
chk('打开「建安二十二年的瘟疫」', cur10 && +cur10.ch === CH_PLAIN, JSON.stringify(cur10));
const p10 = await textPoint(CH_PLAIN);
chk('找到第一页的正文位置', !!p10, JSON.stringify(p10));
if (p10) {
  await clearPopups();
  await page.mouse.click(p10.x, p10.y);
  await page.waitForTimeout(400);
  const s = await popup();
  chk('第 10 篇点正文不弹笔记框', !s.manage,
      'meta=' + JSON.stringify(s.manageMeta) + ' quote=' + JSON.stringify(s.manageQuote.slice(0, 24)));
  chk('第 10 篇点正文不弹注释浮层', !s.pop);
}

/* ---------- ② 第 8 篇点中那条划线 → 弹笔记, 章名对 ---------- */
const cur8 = await openChapter(CH_NOTE);
chk('打开「人背负着世界的恶意生活」', cur8 && +cur8.ch === CH_NOTE, JSON.stringify(cur8));
await page.waitForTimeout(900);      // 等 paintHighlights
const mp = await markPoint(CH_NOTE);
chk('找到第 8 篇那条划线在屏上的位置', !!mp, JSON.stringify(mp));
if (mp) {
  await clearPopups();
  await page.mouse.click(mp.x, mp.y);
  await page.waitForTimeout(400);
  const s = await popup();
  chk('点中划线弹出笔记框', s.manage);
  chk('笔记框的章名是被点的那一章', s.manageMeta.indexOf(LABEL) >= 0, s.manageMeta);
  chk('笔记框里有正文', (s.manageQuote || '').trim().length > 4, s.manageQuote.slice(0, 24));
}

/* ---------- ③ 第 8 篇点普通正文 (不在划线里) → 不弹 ---------- */
const p8 = await textPoint(CH_NOTE);
if (p8) {
  await clearPopups();
  const inMark = await page.evaluate(({ x, y }) => {
    const names = ['yellow', 'green', 'blue', 'pink'];
    for (const n of names) {
      const h = window.CSS && CSS.highlights && CSS.highlights.get('rv-hl-' + n);
      if (!h) continue;
      for (const r of h) {
        for (const rc of r.getClientRects()) {
          if (x >= rc.left && x <= rc.right && y >= rc.top && y <= rc.bottom) return true;
        }
      }
    }
    return false;
  }, p8);
  if (!inMark) {
    await page.mouse.click(p8.x, p8.y);
    await page.waitForTimeout(350);
    const s = await popup();
    chk('第 8 篇点空白正文不弹笔记框', !s.manage, 'meta=' + JSON.stringify(s.manageMeta));
  } else {
    out.push('skip 第 8 篇取样点落在划线上, 跳过"空白正文"一条');
  }
}

chk('全程 0 JS 报错', errs.length === 0, errs.slice(0, 3).join(' | '));

await writeProgress(prog0);
await browser.close();
console.log(out.join('\n'));
const bad = out.filter(l => l.startsWith('FAIL')).length;
console.log(`\n${out.length - bad} passed, ${bad} failed`);
process.exit(bad ? 1 : 0);
