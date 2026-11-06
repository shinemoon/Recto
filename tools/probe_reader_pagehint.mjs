/* 左右翻页: 页号提示 (recto.pg) 必须"章内相对", 且单次翻页只走一页。
 *
 * 背景 (2026-11-06 定位的"老问题"):
 *  1. hint 存的是**整篇文档的绝对页号**。刷新后 boot 只载入进度所在那一章,
 *     绝对页号被当成"本章内页号"用 → 往后跳掉前几章的页数 (用户看到的"刷新后落到 96.1%")。
 *  2. rebuildFlow 把 (left − x0)/stride 直接 max(0,…), 而 x0 含当前页平移 ——
 *     翻过一页之后**前面所有块都塌到第 0 页**, 页表只在第 0 页是对的。补章/重排时读到
 *     这张瞬时表, "当前页第一段"就指到后面某一章 → 一次翻页跳过好几页 + 存错章号。
 *
 * 跑: node tools/probe_reader_pagehint.mjs
 * env: RV_BASE / RV_KEY / RV_JUMP
 */
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || '921fc65624463c47';
const token = cookie();

// 探针会真的翻页并写服务端进度 (自动推云端) → 开头快照, 收尾写回
async function readProgress(pg) {
  return await pg.evaluate(async (k) => {
    const r = await fetch('/read/api/book/' + k, { headers: { accept: 'application/json' } });
    const j = await r.json();
    return (j && j.progress) || null;
  }, KEY);
}
async function writeProgress(pg, pr) {
  if (!pr) return null;
  return await pg.evaluate(async ([k, p]) => {
    const r = await fetch('/read/api/book/' + k + '/progress', {
      method: 'POST', headers: { 'content-type': 'application/json' },
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
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
const errs = [];
page.on('pageerror', e => errs.push(String(e)));
page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
await page.addInitScript(() => localStorage.setItem('recto.mode', 'page'));

async function open() {
  await page.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.rv-page p', { timeout: 20000 });
  await page.waitForTimeout(1200);
}
const state = () => page.evaluate(() => ({
  page: window.__RV__.page(), cur: window.__RV__.cursor(), hint: window.__RV__.hint()
}));
const flowInfo = () => page.evaluate(() => {
  const f = window.__RV__.flow();
  const firsts = [];
  let ch = -1;
  for (const b of f) { if (b.ch !== ch) { firsts.push({ ch: b.ch, page: b.page }); ch = b.ch; } }
  return { firsts, last: f.length ? f[f.length - 1].page : 0 };
});

await open();
const snap = await readProgress(page);

// 0) 页表可信: 章首页号严格递增 (break-before: column 的硬不变量)
{
  const f = await flowInfo();
  let mono = true;
  for (let i = 1; i < f.firsts.length; i++) {
    if (f.firsts[i].page <= f.firsts[i - 1].page) { mono = false; break; }
  }
  chk('页表自洽 (章首页号严格递增)', mono, JSON.stringify(f.firsts));
}

// 1) 连续右翻: 每次只前进一页, 游标章号不回跳, 提示是章内相对页
const steps = [];
for (let i = 0; i < 7; i++) {
  const before = await state();
  await page.keyboard.press('ArrowRight');
  // 章末那一页会先补下一章 (大章要拉几百 KB), 这里等页号真的动, 不靠固定 sleep
  await page.waitForFunction(
    (p0) => window.__RV__.page().page !== p0, before.page.page, { timeout: 6000 }
  ).catch(() => {});
  await page.waitForTimeout(320);
  const after = await state();
  const info = await flowInfo();
  let baseP = null;
  for (const f of info.firsts) {
    if (f.ch === (after.hint && after.hint.ch)) { baseP = f.page; break; }
  }
  steps.push({ before: before.page.page, after: after.page.page,
               cur: after.cur, hint: after.hint, baseP });
}
chk('每次右翻只前进一页', steps.every(s => s.after === s.before + 1),
    steps.map(s => s.before + '→' + s.after).join(' '));
chk('游标章号只前进不跳跃', steps.every((s, i) => i === 0 || s.cur.ch >= steps[i - 1].cur.ch),
    steps.map(s => s.cur && s.cur.ch).join(','));
chk('页号提示带章内相对页 (rel)', steps.every(s => s.hint && typeof s.hint.rel === 'number'),
    JSON.stringify(steps[0].hint));
chk('rel 与页表一致 (page − 章首页)', steps.every(s =>
      s.hint && s.baseP != null && s.hint.rel === s.hint.page - s.baseP),
    steps.map(s => `${s.hint && s.hint.page}-${s.baseP}=${s.hint && s.hint.rel}`).join(' '));
chk('页顶块就是提示里那一段',
      steps.every(s => s.hint && s.cur && s.hint.ch === s.cur.ch && s.hint.off === s.cur.off),
      JSON.stringify(steps[steps.length - 1]));

// 2) 刷新: 落点必须与刷新前同一段 (跨章边界也一样)
{
  const before = await state();
  const domChs = await page.evaluate(() => [...new Set(window.__RV__.flow().map(b => b.ch))]);
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.rv-page p', { timeout: 20000 });
  await page.waitForTimeout(1600);
  const after = await state();
  const domAfter = await page.evaluate(() => [...new Set(window.__RV__.flow().map(b => b.ch))]);
  chk('刷新后落在同一段 (不是同一页号)',
      !!before.cur && !!after.cur &&
      before.cur.ch === after.cur.ch && before.cur.off === after.cur.off,
      `${JSON.stringify(before.cur)} → ${JSON.stringify(after.cur)}`);
  // boot 只载入进度所在那一章, 之后的章是 prefetch 补的 —— 关键是"前面的章不在"
  chk('刷新后文档从当前章起 (页号必须按章内算)',
      domAfter.length >= 1 && domAfter[0] === before.cur.ch && before.page.page > 0,
      `before=${JSON.stringify(domChs)} after=${JSON.stringify(domAfter)} page=${before.page.page}`);
  chk('刷新后 hint 的 rel 与重建后的页表一致',
      after.hint && typeof after.hint.rel === 'number' && after.hint.rel >= 0,
      JSON.stringify(after.hint));
}

// 3) 中段位置 (非章首) 也一致: 再翻几页后刷新
{
  for (let i = 0; i < 3; i++) {
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(400);
  }
  const before = await state();
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.rv-page p', { timeout: 20000 });
  await page.waitForTimeout(1600);
  const after = await state();
  chk('章内往后几页刷新也落在同一段',
      !!before.cur && !!after.cur &&
      before.cur.ch === after.cur.ch && before.cur.off === after.cur.off,
      `${JSON.stringify(before.cur)} → ${JSON.stringify(after.cur)}`);
}

chk('无 JS 报错', errs.length === 0, errs.slice(0, 2).join(' | '));

// 收尾: 写回原进度
const restored = await writeProgress(page, snap);
chk('探针把阅读进度写回原处',
    JSON.stringify(restored) && restored.chapter === (snap ? snap.chapter : restored.chapter),
    JSON.stringify(restored));

await browser.close();
console.log(out.join('\n'));
const bad = out.filter(l => l.startsWith('FAIL'));
console.log(`\n${out.length - bad.length} ok / ${bad.length} FAIL`);
process.exit(bad.length ? 1 : 0);
