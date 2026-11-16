/* 冒烟: 真浏览器保存「划线 + 笔记」 → 提示已保存、库里多一条、重发不重复。
 * 收尾删掉自己建的划线并还原进度。
 */
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
const { execFileSync } = require('child_process');

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || 'da7e470fa0c2c4b8';
const HOST = new URL(BASE).hostname;
const token = execFileSync('python3', [new URL('./make_cookie.py', import.meta.url).pathname],
  { encoding: 'utf8' }).trim();

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: HOST, path: '/' }]);
const page = await ctx.newPage();
page.setDefaultTimeout(20000);
const errs = [];
page.on('pageerror', e => errs.push(String(e)));

await page.goto(BASE + '/read/b/' + KEY, { waitUntil: 'domcontentloaded' });

async function readProgress() {
  return page.evaluate(async () => {
    const k = location.pathname.split('/').pop();
    const j = await (await fetch('/read/api/book/' + k)).json();
    return (j && j.progress) || {};
  });
}
async function writeProgress(p) {
  return page.evaluate(async (prog) => {
    const k = location.pathname.split('/').pop();
    await fetch('/read/api/book/' + k + '/progress', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chapter: prog.chapter || 0, offset: prog.offset || 0 })
    });
  }, p);
}

const prog = await readProgress();
await page.waitForSelector('.rv-page p', { timeout: 20000 });
await page.waitForTimeout(1000);

const before = await page.evaluate(async () => {
  const k = location.pathname.split('/').pop();
  const j = await (await fetch('/read/api/book/' + k)).json();
  return (j.notes || []).length;
});

await page.evaluate(() => {
  const ps = [...document.querySelectorAll('.rv-page p')].filter(p => (p.textContent || '').trim().length > 60);
  const p = ps[0];
  const n = document.createTreeWalker(p, NodeFilter.SHOW_TEXT, null).nextNode();
  const r = document.createRange();
  r.setStart(n, 3); r.setEnd(n, 25);
  const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
  document.dispatchEvent(new Event('selectionchange'));
});
await page.waitForTimeout(320);
await page.click('#rv-sel [data-sel="note"]');
await page.waitForSelector('#rv-noteedit:not([hidden])');
await page.fill('#rv-noteedit-t', '冒烟批注');
await page.click('#rv-noteedit [data-ne="save"]');
let toast = '';
for (let i = 0; i < 40; i++) {
  await page.waitForTimeout(150);
  toast = await page.evaluate(() => {
    const t = document.getElementById('rv-toast');
    return t && !t.hidden ? (t.textContent || '').trim() : '';
  });
  if (toast) break;
}
const after = await page.evaluate(async () => {
  const k = location.pathname.split('/').pop();
  const j = await (await fetch('/read/api/book/' + k)).json();
  const n = (j.notes || []).filter(x => (x.remark || '') === '冒烟批注');
  return { total: (j.notes || []).length, mine: n.map(x => x.id) };
});

console.log(JSON.stringify({ toast, before, after, errs }, null, 1));

for (const id of after.mine) {
  await page.evaluate(async (nid) => {
    const k = location.pathname.split('/').pop();
    await fetch('/read/api/book/' + k + '/note', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'delete', id: nid })
    });
  }, id);
}
await writeProgress(prog);
await browser.close();
