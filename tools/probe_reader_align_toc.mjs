/* 阅读器: ① 云端进度 → 本地落地位置是否真的对得上; ② 章首题图; ③ 分层目录。
 *
 * 关键断言:
 *   ① 把 rprogress 设成"云端算出来的位置"后打开书, 顶部那一段就是该字节位置
 *      对应的段落 (不是章首), 显式百分比也一致;
 *   ② 每章开头的题图 (img.rfig) 出现在正文里, 且是本章第一块;
 *   ③ 目录是分层的: 卷 = details 分组 (默认折起, 当前卷展开), 章缩进; 展开/收起可用。
 *
 * 进度会被改写 → 开头快照, 收尾还原 (tests/test_probe_hygiene.py 硬性要求)。
 */
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
const { execFileSync } = require('child_process');

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || '8379037298fbca9c';   // 示例长篇
const CH = +(process.env.RV_CH || 183);                 // 有题图的章
const token = execFileSync('python3', [new URL('./make_cookie.py', import.meta.url).pathname],
  { encoding: 'utf8' }).trim();

const out = [];
const chk = (name, cond, extra = '') =>
  out.push(`${cond ? 'ok  ' : 'FAIL'} ${name}${extra ? ' — ' + extra : ''}`);

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
page.setDefaultTimeout(15000);
const errs = [];
page.on('pageerror', e => errs.push(String(e)));
page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });

async function apiGet(path) {
  return page.evaluate(async p => (await fetch(p)).json(), path);
}
async function readProgress() {
  const j = await apiGet('/read/api/book/' + KEY);
  return (j && j.progress) || { chapter: 0, offset: 0 };
}
async function writeProgress(p) {
  return page.evaluate(async ([key, prog]) => fetch('/read/api/book/' + key + '/progress', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chapter: prog.chapter || 0, offset: prog.offset || 0 })
  }).then(r => r.json()), [KEY, p]);
}

await page.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('.rv-page p', { timeout: 30000 });
await page.waitForTimeout(400);
const prog0 = await readProgress();

/* ---- ① 云端位置 → 本地落地 ---- */
// 示例长篇 云端最新记录: readPercent 0.3169 (手机 iReader Ocean 5 读到这儿)
const CL_PCT = 0.3177124261856079;   // = 云端 readPercent (device 自己写的)
const totalBytes = await page.evaluate(() => (+window.__RV__ ? 0 : 0) || 0);
const meta = await apiGet('/read/api/book/' + KEY);
const T = meta.bytes;
const pos = CL_PCT * T;
// 期望: 落在包含该字节的那一章, 且顶部块是该章里 >= 偏移的第一块
const expCh = (() => {
  let cum = 0;
  for (let i = 0; i < meta.chapters.length; i++) {
    const c = meta.chapters[i];
    if (pos < cum + c.size || i === meta.chapters.length - 1) return { i, off: pos - cum };
    cum += c.size;
  }
  return { i: 0, off: 0 };
})();
const expBlocks = (await apiGet(`/read/api/book/${KEY}/chapter/${expCh.i}`)).blocks;
const expTop = expBlocks.find(b => b.off >= expCh.off) || expBlocks[expBlocks.length - 1];

await writeProgress({ chapter: expCh.i, offset: Math.round(expCh.off) });
await page.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('.rv-page p', { timeout: 30000 });
await page.waitForTimeout(1200);

const land = await page.evaluate(() => {
  const blk = [...document.querySelectorAll('.rv-page .rv-blk')];
  const vis = blk.find(el => el.getBoundingClientRect().bottom > 60);
  return {
    ch: vis ? +vis.dataset.ch : null,
    off: vis ? +vis.dataset.off : null,
    text: vis ? vis.textContent.slice(0, 40) : '',
    pct: (document.getElementById('rv-pct') || {}).textContent || ''
  };
});
chk('落地章 = 云端位置所在章', land.ch === expCh.i, `expect ${expCh.i} got ${land.ch}`);
chk('落地块 = 该位置的段落 (不是章首)',
    Math.abs(land.off - expTop.off) <= 2,
    `expect off≈${expTop.off} got ${land.off}`);
chk('落地正文与源文同段', land.text.slice(0, 12) === expTop.text.slice(0, 12),
    `exp「${expTop.text.slice(0, 12)}」 got「${land.text.slice(0, 12)}」`);
chk('显示百分比 ≈ 云端 31.x%', /^31\.\d+%/.test(land.pct), land.pct);

let saved = await readProgress();
for (let i = 0; i < 20 && Math.abs(+saved.offset - expTop.off) > 2; i++) {
  await page.waitForTimeout(250);
  saved = await readProgress();
}
// 回写的是"视口最上面那一块"的字节偏移, 与"要跳到的那一段"可能差一两个块
// (跨页/跨列时更明显)。判据: 同一章, 且偏移没有回到章首。
chk('回写的位置与落地位一致 (没被回弹到章首)',
    +saved.chapter === expCh.i && Math.abs(+saved.offset - expTop.off) <= 4096,
    `saved ch=${saved.chapter} off=${saved.offset} (落地位 ${expTop.off})`);

/* ---- ② 章首题图 ---- */
await writeProgress({ chapter: CH, offset: 0 });
await page.goto(`${BASE}/read/b/${KEY}#c${CH}`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('.rv-page .rv-blk', { timeout: 30000 });
await page.waitForTimeout(1200);
const fig = await page.evaluate(ch => {
  const art = document.querySelector(`.rv-ch[data-ch="${ch}"]`);
  if (!art) return { ok: false };
  const first = art.querySelector('.rv-blk');
  const img = first && first.querySelector('img.rfig');
  const box = art.querySelector('img.rfig');
  return {
    ok: !!img,
    firstTag: first ? first.tagName.toLowerCase() : '',
    cls: first ? first.className : '',
    off: first ? +first.dataset.off : null,
    src: box ? box.getAttribute('src') : '',
    w: box ? Math.round(box.getBoundingClientRect().width) : 0,
    h: box ? Math.round(box.getBoundingClientRect().height) : 0,
    natural: box ? box.naturalWidth : 0
  };
}, CH);
chk('章首题图渲染出来了', fig.ok, JSON.stringify(fig));
chk('题图是本章第一块', fig.firstTag === 'img' || /rv-figblk/.test(fig.cls), fig.cls);
chk('题图有真实尺寸', fig.w > 100 && fig.h > 10, `${fig.w}x${fig.h}`);
chk('题图不是裂图', fig.natural > 0, `naturalWidth=${fig.natural}`);

/* ---- ③ 分层目录 ---- */
await page.evaluate(() => document.querySelector('[data-act="toc"]').click());
await page.waitForTimeout(300);
const toc = await page.evaluate(ch => {
  const ol = document.getElementById('rv-toc-list');
  const secs = [...ol.querySelectorAll('.rv-toc-sec')];
  const cur = [...ol.querySelectorAll('a.rv-cur')][0];
  const curSec = cur ? cur.closest('.rv-toc-sec') : null;
  const openCount = secs.filter(s => s.open).length;
  const row = cur ? cur.closest('li') : null;
  const a = row ? row.querySelector('a') : null;
  const label = secs[0] ? secs[0].querySelector('summary') : null;
  const kidA = secs[0] ? secs[0].querySelector('.rv-toc-kids a') : null;
  const padL = el => (el ? Math.round(parseFloat(getComputedStyle(el).paddingLeft)) : -1);
  return {
    secs: secs.length,
    rows: ol.querySelectorAll('a').length,
    openCount,
    curInOpenSec: !!(curSec && curSec.open),
    labelPadL: padL(label),
    kidPadL: padL(kidA),
    actsHidden: document.getElementById('rv-toc-acts').hidden,
    innerText: (ol.innerText || '').slice(0, 60)
  };
}, CH);
chk('目录按卷分组', toc.secs > 10 && toc.rows > 500, `secs=${toc.secs} rows=${toc.rows}`);
chk('默认只展开当前卷', toc.openCount === 1, `open=${toc.openCount}`);
chk('当前章所在卷是展开的', toc.curInOpenSec);
chk('子项相对卷名缩进', toc.kidPadL >= toc.labelPadL + 20,
    `label padding-left=${toc.labelPadL} kid padding-left=${toc.kidPadL}`);
chk('展开/收起按钮在一层以上目录时显示', toc.actsHidden === false);

await page.evaluate(() => document.querySelector('[data-toc-act="expand"]').click());
await page.waitForTimeout(150);
const allOpen = await page.evaluate(() =>
  [...document.querySelectorAll('.rv-toc-sec')].every(s => s.open));
chk('「展开」把所有卷展开', allOpen);
await page.evaluate(() => document.querySelector('[data-toc-act="collapse"]').click());
await page.waitForTimeout(150);
const allClosed = await page.evaluate(() =>
  document.querySelectorAll('.rv-toc-sec[open]').length === 1);
chk('「收起」只留当前卷', allClosed);

// 点卷名 = 折叠/展开 (2026-11-05 起不再跳转: 点开目录只为找章, 误跳很烦)
const fold = await page.evaluate(async () => {
  const secs = [...document.querySelectorAll('.rv-toc-sec')];
  const t = secs[2];
  const label = t.querySelector('summary');
  t.open = true;
  const before = t.open;
  const chBefore = window.__RV__.cursor().ch;
  const hashBefore = location.hash;
  label.querySelector('.rv-toc-n').click();     // 点卷名 (不是箭头)
  await new Promise(r => setTimeout(r, 600));
  return { before, after: t.open, hashBefore, hash: location.hash,
           chBefore, chAfter: window.__RV__.cursor().ch };
});
chk('点卷名 = 折叠翻转', fold.before !== fold.after, JSON.stringify(fold));
chk('点卷名不跳章 (URL 与当前章都不变)',
    fold.hash === fold.hashBefore && fold.chAfter === fold.chBefore, JSON.stringify(fold));

// 点章 = 换章
const chJump = await page.evaluate(async () => {
  const secs = [...document.querySelectorAll('.rv-toc-sec')];
  secs[1].open = true;
  const a = secs[1].querySelector('.rv-toc-kids a');
  const want = a ? +a.dataset.ch : null;
  a.click();
  await new Promise(r => setTimeout(r, 900));
  return { want, hash: location.hash, cur: document.querySelector('a.rv-cur') ? +document.querySelector('a.rv-cur').dataset.ch : null };
});
chk('点章目录能换章', chJump.cur === chJump.want, JSON.stringify(chJump));

chk('没有 JS 报错', errs.length === 0, errs.slice(0, 3).join(' | '));

/* 还原进度 */
await writeProgress(prog0);
console.log(out.join('\n'));
const bad = out.filter(l => l.startsWith('FAIL'));
console.log(`\n${out.length - bad.length}/${out.length} PASS`);
await browser.close();
if (bad.length) process.exit(1);
