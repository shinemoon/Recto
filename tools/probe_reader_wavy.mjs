/* 验阅读器里划线画成了**波浪下划线** (不是底色块)。
 *
 * 判据:
 *   ① 屏上能找到一条划线 (CSS.highlights 注册表里有区间, 且 rect 在视口内);
 *   ② 截该区域的像素 (python 侧判): 下划线颜色的像素按行分布 —— 波浪每行宽度不等,
 *      实线恒定; 底色块则整块矩形着色。
 *
 * 探针卫生:
 *   · 会跳章 → readProgress 快照 + writeProgress 还原;
 *   · 会在阅读器里停留 → 前端每 30 秒 beat 一次, 服务端给这本书真加阅读时长
 *     → 用 tools/rtime_snapshot.py save/restore 还原 (见 test_probe_hygiene.py)。
 *
 * 跑法: RV_BASE=http://127.0.0.1:5015 node tools/probe_reader_wavy.mjs
 */
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
import { fileURLToPath } from 'url';
import path from 'path';
const { execFileSync } = require('child_process');

const HERE = path.dirname(fileURLToPath(import.meta.url));

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || '8379037298fbca9c';   // 剑来 (35 条划线)
const CH = +(process.env.RV_CH || 107);                  // 有一条划线的章

let snapBook = '';
function timeSnap(cmd, key) {
  try {
    const out = execFileSync('python3', [path.join(HERE, 'rtime_snapshot.py'), cmd,
                                         '--book', key], { encoding: 'utf8', cwd: HERE });
    console.log('  · 时长快照 ' + cmd + ': ' + out.trim());
  } catch (e) {
    console.log('  ! 时长快照 ' + cmd + ' 失败: ' + String(e).slice(0, 160));
  }
}
process.on('exit', () => { if (snapBook) timeSnap('restore', snapBook); });

const token = execFileSync('python3', [new URL('./make_cookie.py', import.meta.url).pathname],
  { encoding: 'utf8' }).trim();

const out = [];
const chk = (name, cond, extra = '') =>
  out.push(`${cond ? 'ok  ' : 'FAIL'} ${name}${extra ? ' — ' + extra : ''}`);

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
page.setDefaultTimeout(20000);
const errs = [];
page.on('pageerror', e => errs.push(String(e)));
page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });

snapBook = KEY;
timeSnap('save', KEY);

await page.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('.rv-page p', { timeout: 30000 });
await page.waitForTimeout(1200);

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

/* 跳到有划线的那一章 */
await page.evaluate(c => {
  const a = document.querySelector('#rv-toc a[data-ch="' + c + '"]')
         || document.querySelector('[data-ch="' + c + '"]');
  if (a) a.click();
}, String(CH));
await page.waitForTimeout(2500);

/* 把划线滚进视口 (章很长, 划线在章中后段) */
const scrolled = await page.evaluate(() => {
  const names = ['yellow', 'green', 'blue', 'pink', 'underline'];
  for (const n of names) {
    const h = window.CSS && CSS.highlights && CSS.highlights.get('rv-hl-' + n);
    if (!h) continue;
    for (const r of h) {
      const el = (r.startContainer.nodeType === 3 ? r.startContainer.parentElement : r.startContainer);
      if (!el || !el.closest) continue;
      el.scrollIntoView({ block: 'center' });
      return n;
    }
  }
  return null;
});
await page.waitForTimeout(600);

const found = await page.evaluate(() => {
  const names = ['yellow', 'green', 'blue', 'pink', 'underline'];
  const vh = window.innerHeight;
  for (const n of names) {
    const h = window.CSS && CSS.highlights && CSS.highlights.get('rv-hl-' + n);
    if (!h) continue;
    for (const r of h) {
      for (const rc of r.getClientRects()) {
        if (rc.width < 30 || rc.height < 6) continue;
        const y = rc.top + rc.height / 2;
        if (y < 140 || y > vh - 140) continue;
        return { color: n, left: rc.left, right: rc.right, top: rc.top, bottom: rc.bottom };
      }
    }
  }
  return null;
});
chk('屏上找到一条划线', !!found, JSON.stringify({ scrolled, found }));

let clip = null;
if (found) {
  clip = {
    x: Math.max(0, Math.floor(found.left) - 2),
    y: Math.max(0, Math.floor(found.top) - 2),
    width: Math.min(460, Math.ceil(found.right - found.left) + 4),
    height: Math.ceil(found.bottom - found.top) + 12   // 下划线在文字下方, 多留 12px
  };
  await page.screenshot({ path: '/tmp/wavy_reader.png', clip });
  out.push(`info 划线色 ${found.color} 区域 ${JSON.stringify(clip)}`);
}

chk('全程 0 JS 报错', errs.length === 0, errs.slice(0, 3).join(' | '));

await writeProgress(prog0);
await browser.close();
console.log(out.join('\n'));
console.log('shot -> /tmp/wavy_reader.png');
if (clip) console.log('CLIP ' + JSON.stringify(clip));
