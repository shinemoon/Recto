/* 书宠故事 —— 连续的路 + 档案页 (真浏览器探针)。
 *
 * 跑法: cd tools && node probe_pet_story_flow.mjs
 * 环境: RECTO_BASE (默认 5015)
 *
 * 验的事:
 *   · 前台是**一条连续的路**: 不分章节, 空格推一段 (不是一行)
 *   · 位置记在**后端** (全局段序号), 刷新接着上次那段
 *   · 章节在**后台**: 档案页 (/pet/story/) 按回摆, 带交手 / 判定 / 来处
 *   · 390px 无横向溢出 / 0 JS 报错
 *
 * 卫生: 只读 + 本地 UI 操作。不点「出门走一趟」(那会真的推演)。
 *       阅读位置会写后端 —— 收尾**还原**到跑之前那个值。
 */
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RECTO_BASE || '5015';
const URL = `http://127.0.0.1:${BASE}`;

let PASS = 0, FAIL = 0;
function ok(cond, name) {
  if (cond) { PASS++; console.log('PASS', name); }
  else { FAIL++; console.log('FAIL', name); }
}

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
await ctx.addCookies([{
  name: 'zy_webauth', value: cookie(), domain: '127.0.0.1', path: '/',
}]);
const page = await ctx.newPage();
const pageErrors = [];
const badResponses = [];
page.on('response', (r) => { if (r.status() >= 400) badResponses.push(r.status() + ' ' + r.url()); });
page.on('pageerror', (e) => pageErrors.push(String(e)));
page.on('console', (m) => { if (m.type() === 'error') pageErrors.push(m.text()); });

/** 读当前后端位置 (快照)。 */
async function readPos() {
  return page.evaluate(async () => {
    const r = await fetch('/pet/api/reading', { credentials: 'same-origin' });
    const d = await r.json();
    return (d.pos || {}).idx || 0;
  });
}

/** 写位置 (收尾还原用)。位置默认只前进 —— 还原要带 force。 */
async function writePos(idx) {
  return page.evaluate(async (n) => {
    const r = await fetch('/pet/api/reading', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ idx: n, force: true }),
    });
    return r.ok;
  }, idx);
}

await page.goto(`${URL}/pet/`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#pg-bar');
await page.waitForTimeout(1200);

const snapIdx = await readPos();
console.log(`  (快照位置: idx=${snapIdx})`);

// ── 1. 前台: 一条连续的路 ───────────────────────────────
const flow = await page.evaluate(() => ({
  total: (window.state && window.state.story && window.state.story.total) || 0,
  chapters: (window.state && window.state.story && (window.state.story.chapters || []).length) || 0,
}));
ok(flow.total > 0, `故事有内容 (${flow.total} 段 / ${flow.chapters} 回)`);
ok(flow.chapters > 0, '后端按回组织 (章节在后台)');

const body = await page.evaluate(() => {
  const b = document.querySelector('#adv-read-body');
  const box = document.querySelector('#adv-read');
  return {
    hidden: box ? box.hidden : null,
    paras: b ? b.querySelectorAll('p').length : 0,
    title: document.querySelector('#adv-read-title')?.textContent?.trim() || '',
    prog: document.querySelector('#adv-read-prog')?.textContent?.trim() || '',
    hint: document.querySelector('#adv-read-hint')?.textContent?.trim() || '',
    archive: !!document.querySelector('#btn-adv-archive'),
    archiveHref: document.querySelector('#btn-adv-archive')?.getAttribute('href') || '',
    // 前台不该出现「第N回」这种章节标记
    hasNoBadge: !document.querySelector('#adv-read .adv-no'),
  };
});
ok(body.hidden === false, '读本摊开着 (一条路, 不分章节)');
ok(body.paras >= 1, `读本里有正文 (${body.paras} 段)`);
ok(!/第\s*\d+\s*回/.test(body.title), `标题不带章节号 (${body.title})`);
ok(body.hasNoBadge, '正文区没有「第N回」的标记');
ok(body.archive && body.archiveHref === '/pet/story/', '有「按回查阅」的入口', body.archiveHref);
ok(/翻一屏/.test(body.hint), `提示写明空格翻一屏 (${body.hint})`);

// ── 2. 空格翻一屏 (MUD 式, 不是一段一行) ────────────────────────
//
// 2026-11-20 改: 空格**翻一屏** (滚一个视口高), 不是推一段。
// 「推一段」那次敲击只出两行字, 读起来碎; 而且 max-height 容器会随
// 内容长高, 把整个页面撑出滚动条 —— 版面跟着晃。
// 现在: 窗口高度固定, 滚动只在窗口内, 位置 = 窗口底压着的那一段。
const before = await page.evaluate(() => {
  const b = document.querySelector('#adv-read-body');
  return { st: b.scrollTop, ch: b.clientHeight, pageY: window.scrollY };
});
await page.keyboard.press('Space');
await page.waitForTimeout(300);
const after = await page.evaluate(() => {
  const b = document.querySelector('#adv-read-body');
  return {
    st: b.scrollTop, ch: b.clientHeight, pageY: window.scrollY,
    prog: document.querySelector('#adv-read-prog')?.textContent?.trim() || '',
    idx: window.READER ? window.READER.idx : 0,
  };
});
const delta = after.st - before.st;
ok(delta > 0, `空格让窗口往下滚 (Δ${Math.round(delta)}px)`);
ok(Math.abs(delta - after.ch) < after.ch * 0.15,
   `翻的正好是一屏 (Δ${Math.round(delta)} vs 视口 ${after.ch})`);
ok(after.pageY === before.pageY, `页面自己没被卷走 (pageY ${before.pageY} → ${after.pageY})`);
ok(/^\d+ \/ \d+ 段$/.test(after.prog), `进度按段记 (${after.prog})`);
ok(after.idx > 0, `位置跟着前进 (idx=${after.idx})`);

// 位置 = 窗口底压着的那一段 (眼睛看到屏幕底就等于读到那儿)
// 拿后端给的段落边界对账: 窗口底那一段的**下沿**应落在窗口底附近。
const bot = await page.evaluate(() => {
  const b = document.querySelector('#adv-read-body');
  const R = window.READER;
  const i = R.idx - 1;
  return {
    bot: R.bots[i] || 0,
    limit: b.scrollTop + b.clientHeight,
    text: (R.flow[i] || {}).text || '',
  };
});
ok(bot.bot <= bot.limit + 2,
   `位置 = 窗口底压着的那一段 (下沿 ${Math.round(bot.bot)} vs 窗口底 ${Math.round(bot.limit)})`);

// ── 3. 位置记在后端 ──────────────────────────────────────
await page.waitForTimeout(300);
const posNow = await readPos();
ok(posNow > 0, `位置记在后端 (idx=${posNow})`);

// ── 4. 刷新 → 接着上次那段 ───────────────────────────────
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForSelector('#adv-read-body p', { timeout: 15000 });
await page.waitForTimeout(900);
const afterReload = await page.evaluate(() => {
  const b = document.querySelector('#adv-read-body');
  return {
    st: Math.round(b.scrollTop),
    idx: window.READER ? window.READER.idx : 0,
    prog: document.querySelector('#adv-read-prog')?.textContent?.trim() || '',
  };
});
ok(afterReload.st > 0, `刷新后落在上次读到的地方 (scrollTop=${afterReload.st})`);
ok(Math.abs(afterReload.idx - after.idx) <= 2,
   `刷新后位置跟存档一致 (${afterReload.idx} vs ${after.idx})`);

// ── 5. 档案页: 按回查阅 ──────────────────────────────────
await page.goto(`${URL}/pet/story/`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#archive-list', { timeout: 15000 });
await page.waitForTimeout(900);
const arch = await page.evaluate(() => {
  const items = document.querySelectorAll('#archive-list .arch-item');
  const first = items[items.length - 1];
  return {
    n: items.length,
    pill: document.querySelector('#archive-pill')?.textContent?.trim() || '',
    firstNo: first?.querySelector('.arch-no')?.textContent?.trim() || '',
    firstTitle: first?.querySelector('.arch-title')?.textContent?.trim() || '',
    firstHasNo: /第\s*\d+\s*回/.test(first?.querySelector('.arch-no')?.textContent || ''),
    back: document.querySelector('#btn-archive-back')?.getAttribute('href') || '',
  };
});
ok(arch.n > 0, `档案页按回摆 (${arch.n} 回 · ${arch.pill})`);
ok(arch.firstHasNo, `档案页标着「第N回」(${arch.firstNo} ${arch.firstTitle})`);
ok(arch.back === '/pet/', '档案页有回到那条路的入口');

// 展开一回 → 有正文 + 交手 / 判定
await page.evaluate(() => {
  const items = document.querySelectorAll('#archive-list .arch-item');
  const last = items[items.length - 1];
  if (last) last.open = true;
});
await page.waitForTimeout(300);
const opened = await page.evaluate(() => {
  const items = document.querySelectorAll('#archive-list .arch-item');
  const last = items[items.length - 1];
  return {
    paras: last?.querySelectorAll('.arch-text p').length || 0,
    duel: !!last?.querySelector('.adv-duel'),
    checks: !!last?.querySelector('.adv-checks'),
    echo: !!last?.querySelector('.adv-echo'),
    growth: !!last?.querySelector('.adv-growth'),
  };
});
ok(opened.paras > 0, `展开的那回有正文 (${opened.paras} 段)`);
ok(opened.growth || opened.echo || opened.duel || opened.checks,
   '展开的那回带战报 / 判定 / 来处 / 所显');

// ── 6. 窄屏 ──────────────────────────────────────────────
const m = await ctx.newPage();
await m.setViewportSize({ width: 390, height: 844 });
await m.goto(`${URL}/pet/`, { waitUntil: 'domcontentloaded' });
await m.waitForSelector('#pg-bar');
await m.waitForTimeout(900);
const mob = await m.evaluate(() => ({
  overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  bodyW: document.querySelector('#adv-read-body')?.getBoundingClientRect().width || 0,
  win: window.innerWidth,
}));
ok(mob.overflow <= 1, `手机 390px 无横向溢出 (${mob.overflow}px)`);
ok(mob.bodyW > 0 && mob.bodyW <= mob.win, `正文宽度在视口内 (${Math.round(mob.bodyW)}/${mob.win})`);

await m.goto(`${URL}/pet/story/`, { waitUntil: 'domcontentloaded' });
await m.waitForTimeout(900);
const mobArch = await m.evaluate(() => ({
  overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  n: document.querySelectorAll('#archive-list .arch-item').length,
}));
ok(mobArch.overflow <= 1, `档案页手机无横向溢出 (${mobArch.overflow}px)`);
ok(mobArch.n > 0, '档案页手机上有条目');

// ── 7. 还原位置 (收尾) ───────────────────────────────────
// 后端只前进 —— 还原要用 force (接口不暴露 force, 所以直接写文件)
await page.goto(`${URL}/pet/`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(400);
await writePos(snapIdx);
console.log(`  (位置已还原到 idx=${snapIdx})`);

ok(pageErrors.length === 0, `无 JS 报错 (${pageErrors.length}) ${pageErrors.slice(0, 2).join(' | ')}`);
if (badResponses.length) {
  console.log('  4xx/5xx 响应:');
  badResponses.slice(0, 6).forEach((b) => console.log('   ', b));
}

await browser.close();
console.log('');
console.log('='.repeat(46));
console.log(`PASS ${PASS}  FAIL ${FAIL}`);
process.exit(FAIL ? 1 : 0);