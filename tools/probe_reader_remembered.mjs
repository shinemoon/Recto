/* 刷新后库里比这台设备记得的还靠前 → 问一句 (local-remembered) */
import { chromium } from '/vol2/1001/Recto/tools/_env.mjs';
const { execFileSync } = await import('child_process');

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || 'da7e470fa0c2c4b8';
const token = execFileSync('python3', ['/vol2/1001/Recto/tools/make_cookie.py'],
  { encoding: 'utf8' }).trim();

const out = [];
const chk = (n, c, e = '') => out.push(`${c ? 'ok  ' : 'FAIL'} ${n}${e ? ' — ' + e : ''}`);

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
const errs = [];
page.on('pageerror', e => errs.push('pageerror: ' + String(e)));
let wrote = 0;
page.on('request', r => {
  if (r.method() === 'POST' && /\/read\/api\/book\/[^/]+\/progress$/.test(r.url())) wrote++;
});

await page.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('.rv-page p', { timeout: 20000 });
await page.waitForTimeout(900);

// 造出"服务端记得更少"的局面: 本地 localStorage 记一个更远的读数
const fake = await page.evaluate(async (k) => {
  const j = await (await fetch('/read/api/book/' + k, { headers: { accept: 'application/json' } })).json();
  const p = j.progress || {};
  const seen = { ch: +p.chapter || 0, off: (+p.offset || 0) + 40000,
                 pct: Math.min(0.99, (+p.percent || 0) + 0.05), at: Date.now() };
  localStorage.setItem('recto.pos.' + k, JSON.stringify(seen));
  return { cur: p, seen: seen };
}, KEY);
chk('造好局面 (库里 vs 本地记忆)', fake.seen.pct > fake.cur.percent,
    `${fake.cur.percent} -> ${fake.seen.pct}`);

wrote = 0;
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForSelector('.rv-page p', { timeout: 20000 });
await page.waitForTimeout(1500);

const bar = await page.evaluate(() => {
  const el = document.getElementById('rv-choice');
  const b = el && el.querySelector('[data-c="local"]');
  const c = el && el.querySelector('[data-c="cloud"]');
  return {
    shown: !!(el && !el.hidden),
    txt: (document.getElementById('rv-choice-txt') || {}).textContent || '',
    localBtn: b ? b.textContent : '', cloudBtn: c ? c.textContent : '',
    localPrimary: b ? b.className : ''
  };
});
chk('打开时问了一句', bar.shown, JSON.stringify(bar).slice(0, 200));
chk('文案: 这台设备上次读到 / 库里只记得',
    /这台设备上次读到/.test(bar.txt) && /库里只记得/.test(bar.txt), bar.txt);
chk('按钮: 回到上次位置', bar.localBtn === '回到上次位置', bar.localBtn);
chk('按钮: 从当前开始', bar.cloudBtn === '从当前开始', bar.cloudBtn);
chk('默认建议回到上次', bar.localPrimary === 'primary', bar.localPrimary);
chk('问的时候没写进度', wrote === 0, 'POST /progress × ' + wrote);

// 选「从当前开始」: 关掉提示 + 把本地记忆拉回库里位置, 不再反复问
await page.click('#rv-choice [data-c="cloud"]');
await page.waitForTimeout(400);
const after = await page.evaluate((k) => {
  const el = document.getElementById('rv-choice');
  let seen = null;
  try { seen = JSON.parse(localStorage.getItem('recto.pos.' + k) || 'null'); } catch (e) { }
  return { hidden: !!(el && el.hidden), seen: seen };
}, KEY);
chk('提示收起', after.hidden, JSON.stringify(after));
chk('本地记忆被拉回库里位置', after.seen && Math.abs(after.seen.pct - fake.cur.percent) < 0.01,
    JSON.stringify(after.seen));
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForSelector('.rv-page p', { timeout: 20000 });
await page.waitForTimeout(1200);
const again = await page.evaluate(() => {
  const el = document.getElementById('rv-choice');
  return !!(el && !el.hidden);
});
chk('再打开就不再问了', again === false, String(again));
chk('0 前端报错', errs.length === 0, errs.join(' | '));

console.log(out.join('\n'));
await browser.close();
process.exit(out.some(l => l.startsWith('FAIL')) ? 1 : 0);
