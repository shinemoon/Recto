/* 书宠故事 —— **最新一回** + 内嵌块 + 档案页排序 (真浏览器探针)。
 *
 * 跑法: cd tools && node probe_pet_story_flow.mjs
 * 环境: RECTO_BASE (默认 5015)
 *
 * 验的事:
 *   · 前台显示的是**最新的一回** (不是一条连续的路)
 *   · 判定 / 启发 / 交手 **嵌在正文里对应的那一段** (不是收在末尾)
 *   · 位置记在**后端** (回号, 段号), 刷新接着上次那段
 *   · 档案页 (/pet/story/) 按回摆 + **正序 / 倒序**可切
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
    const p = d.pos || {};
    return { round: p.round || 0, idx: p.idx || 0 };
  });
}

/** 写位置 (收尾还原用)。位置默认只前进 —— 还原要带 force。 */
async function writePos(p) {
  return page.evaluate(async (v) => {
    await fetch('/pet/api/reading', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ round: v.round, idx: v.idx, force: true, clamp: false }),
    });
  }, p);
}

await page.goto(`${URL}/pet/`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#pg-bar');
await page.waitForTimeout(1200);

const snap = await readPos();
console.log(`  (快照位置: round=${snap.round} idx=${snap.idx})`);

// ── 1. 前台: 最新的一回 ─────────────────────────────────
const flow = await page.evaluate(() => {
  const st = window.state && window.state.story;
  const L = (st && st.latest) || {};
  return {
    total: (st && st.total) || 0,
    latestParas: (L.paras || []).length,
    round: L.no || 0,
    title: L.title || '',
    rounds: L.rounds || 0,
    blocks: (L.blocks || []).length,
  };
});
ok(flow.latestParas > 0, `前台有最新一回的正文 (${flow.latestParas} 段)`);
ok(flow.round > 0, `最新一回是第 ${flow.round} 回`);
ok(flow.title.length > 0, `最新一回有标题: ${flow.title}`);
ok(flow.rounds > 1, `一共走了 ${flow.rounds} 回`);

const body = await page.evaluate(() => {
  const b = document.querySelector('#adv-read-body');
  const box = document.querySelector('#adv-read');
  return {
    hidden: box ? box.hidden : null,
    paras: b ? b.querySelectorAll('p').length : 0,
    title: document.querySelector('#adv-read-title')?.textContent?.trim() || '',
    kind: document.querySelector('#adv-read-kind')?.textContent?.trim() || '',
    prog: document.querySelector('#adv-read-prog')?.textContent?.trim() || '',
    hint: document.querySelector('#adv-read-hint')?.textContent?.trim() || '',
    archive: !!document.querySelector('#btn-adv-archive'),
    archiveHref: document.querySelector('#btn-adv-archive')?.getAttribute('href') || '',
    shown: (document.querySelector('#adv-shown')?.textContent || '').trim(),
    account: !!document.querySelector('#adv-account'),
  };
});
ok(body.hidden === false, '读本摊开着');
ok(body.paras > 0, `读本里有正文 (${body.paras} 段)`);
ok(body.title === flow.title, `标题是最新一回的 (${body.title})`);
ok(/^\d+ \/ \d+ 段$/.test(body.prog), `进度按段记 (${body.prog})`);
ok(body.archive && body.archiveHref === '/pet/story/', '有「按回查阅」的入口', body.archiveHref);
ok(/翻一屏/.test(body.hint), `提示写明空格翻一屏 (${body.hint})`);
ok(body.shown.length > 0, `「这一趟长出来的」有内容 (${body.shown.slice(0, 30)})`);
ok(body.account, '有「这一趟的账」折叠块');

// ── 2. 判定 / 启发 **嵌在正文里** ──────────────────────────
//
// 关键: 内嵌块必须是正文窗口里的**兄弟节点** (跟在某一段后面),
// 不是收在窗口外的末尾。
const inline = await page.evaluate(() => {
  const b = document.querySelector('#adv-read-body');
  const ps = Array.from(b.querySelectorAll('p'));
  const boxes = Array.from(b.querySelectorAll('.adv-inline'));
  // 每段后面跟着的块
  const after = ps.map((p) => {
    let n = p.nextElementSibling;
    let c = 0;
    while (n && n.classList && n.classList.contains('adv-inline')) { c++; n = n.nextElementSibling; }
    return c;
  });
  return {
    boxes: boxes.length,
    items: boxes.reduce((n, x) => n + x.querySelectorAll('.adv-in').length, 0),
    kinds: Array.from(new Set(Array.from(b.querySelectorAll('.adv-in')).map((e) => e.className))),
    withAnchor: after.filter((x) => x > 0).length,
    firstIdx: after.findIndex((x) => x > 0),
  };
});
ok(inline.boxes > 0, `正文里有内嵌块 (${inline.boxes} 组)`);
ok(inline.items > 0, `内嵌块里有条目 (${inline.items} 条)`);
ok(inline.withAnchor > 0, `内嵌块跟在某一段后面 (${inline.withAnchor} 段带块)`);
ok(inline.firstIdx >= 0, `第一处内嵌在第 ${inline.firstIdx} 段后面`);
ok(inline.kinds.some((k) => k.includes('adv-in-check')), '有「路上的坎」内嵌');
ok(inline.kinds.some((k) => k.includes('adv-in-spark')), '有「化成了」内嵌');

// 字体紧凑一点
const font = await page.evaluate(() => {
  const p = document.querySelector('#adv-read-body p');
  if (!p) return { fs: 0, lh: 0 };
  const cs = getComputedStyle(p);
  return { fs: parseFloat(cs.fontSize), lh: parseFloat(cs.lineHeight) };
});
ok(font.fs > 0 && font.fs <= 16, `正文不大 (${font.fs}px)`);
ok(font.lh / font.fs <= 1.85, `行距紧 (${(font.lh / font.fs).toFixed(2)})`);

// ── 3. 空格翻一屏 (MUD 式) ─────────────────────────────────
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
    round: window.READER ? window.READER.round : 0,
  };
});
const delta = after.st - before.st;
ok(delta > 0, `空格让窗口往下滚 (Δ${Math.round(delta)}px)`);
// 一屏 = 视口高, 但**内容不够长时滚不满一屏** (最新那一回可能就十几段) ——
// 那就滚到尽头。这是内容长度决定的, 不是滚动坏了。
const maxScroll = await page.evaluate(() => {
  const b = document.querySelector('#adv-read-body');
  return Math.max(0, b.scrollHeight - b.clientHeight);
});
const expect = Math.min(after.ch, maxScroll);
ok(Math.abs(delta - expect) < Math.max(12, expect * 0.15),
   `翻的是一屏 (Δ${Math.round(delta)} vs 期望 ${Math.round(expect)} · 视口 ${after.ch} · 可滚 ${Math.round(maxScroll)})`);
ok(after.pageY === before.pageY, `页面自己没被卷走 (pageY ${before.pageY} → ${after.pageY})`);
ok(after.round === flow.round, `位置记着第 ${after.round} 回`);
ok(after.idx > 0, `位置跟着前进 (idx=${after.idx})`);

// ── 4. 位置记在后端 ──────────────────────────────────────
await page.waitForTimeout(400);
const posNow = await readPos();
ok(posNow.round === flow.round && posNow.idx > 0,
   `位置记在后端 (round=${posNow.round} idx=${posNow.idx})`);

// ── 5. 刷新 → 接着上次那段 ───────────────────────────────
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForSelector('#adv-read-body p', { timeout: 15000 });
await page.waitForTimeout(900);
const afterReload = await page.evaluate(() => ({
  st: Math.round(document.querySelector('#adv-read-body').scrollTop),
  idx: window.READER ? window.READER.idx : 0,
}));
ok(afterReload.st > 0, `刷新后落在上次读到的地方 (scrollTop=${afterReload.st})`);
ok(Math.abs(afterReload.idx - after.idx) <= 2,
   `刷新后位置跟存档一致 (${afterReload.idx} vs ${after.idx})`);

// ── 6. 档案页: 按回查阅 + 正序 / 倒序 ─────────────────────
await page.goto(`${URL}/pet/story/`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#archive-list', { timeout: 15000 });
await page.waitForTimeout(900);

const arch = await page.evaluate(() => {
  const items = Array.from(document.querySelectorAll('#archive-list .arch-item'));
  const nos = items.map((d) => Number((d.querySelector('.arch-no') || {}).textContent?.replace(/\D/g, '') || 0));
  return {
    n: items.length,
    pill: document.querySelector('#archive-pill')?.textContent?.trim() || '',
    nos,
    asc: nos.every((v, i) => i === 0 || nos[i - 1] <= v),
    hasOrdBtns: !!document.querySelector('#arch-ord-asc') && !!document.querySelector('#arch-ord-desc'),
    onBtn: document.querySelector('.arch-order .is-on')?.getAttribute('data-ord') || '',
    back: document.querySelector('#btn-archive-back')?.getAttribute('href') || '',
  };
});
ok(arch.n > 0, `档案页按回摆 (${arch.n} 回 · ${arch.pill})`);
ok(arch.hasOrdBtns, '有正序 / 倒序两个按钮');
ok(arch.onBtn === 'asc', `默认正序 (现在 ${arch.onBtn})`);
ok(arch.asc, '正序: 回号从小到大');
ok(arch.back === '/pet/', '档案页有回到书宠的入口');

// 点「倒序」→ 回号从大到小
await page.click('#arch-ord-desc');
await page.waitForTimeout(400);
const desc = await page.evaluate(() => {
  const items = Array.from(document.querySelectorAll('#archive-list .arch-item'));
  const nos = items.map((d) => Number((d.querySelector('.arch-no') || {}).textContent?.replace(/\D/g, '') || 0));
  return {
    nos,
    desc: nos.every((v, i) => i === 0 || nos[i - 1] >= v),
    onBtn: document.querySelector('.arch-order .is-on')?.getAttribute('data-ord') || '',
    url: location.search,
  };
});
ok(desc.onBtn === 'desc', `点倒序 → 按钮切到倒序 (${desc.onBtn})`);
ok(desc.desc, '倒序: 回号从大到小');
ok(/ord=desc/.test(desc.url), `顺序记在 URL (${desc.url})`);

// 再点回正序
await page.click('#arch-ord-asc');
await page.waitForTimeout(300);
const back2 = await page.evaluate(() => {
  const items = Array.from(document.querySelectorAll('#archive-list .arch-item'));
  const nos = items.map((d) => Number((d.querySelector('.arch-no') || {}).textContent?.replace(/\D/g, '') || 0));
  return { asc: nos.every((v, i) => i === 0 || nos[i - 1] <= v) };
});
ok(back2.asc, '切回正序');

// 展开一回 → 有正文 + 交手 / 判定 / 来处
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

// ── 7. 窄屏 ────────────────────────────────────────────
const m = await ctx.newPage();
await m.setViewportSize({ width: 390, height: 844 });
m.on('pageerror', (e) => pageErrors.push(String(e)));
await m.goto(`${URL}/pet/`, { waitUntil: 'domcontentloaded' });
await m.waitForSelector('#adv-read-body p', { timeout: 15000 });
await m.waitForTimeout(700);
const mob = await m.evaluate(() => ({
  overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  inlineW: document.querySelector('.adv-inline')?.getBoundingClientRect().width || 0,
  winW: window.innerWidth,
}));
ok(mob.overflow <= 1, '手机没有横向溢出', mob);
ok(mob.inlineW > 0 && mob.inlineW <= mob.winW, '内嵌块宽度在视口内', mob);

await m.goto(`${URL}/pet/story/`, { waitUntil: 'domcontentloaded' });
await m.waitForSelector('#archive-list .arch-item', { timeout: 15000 });
await m.waitForTimeout(500);
const mobArch = await m.evaluate(() => ({
  overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  hasOrd: !!document.querySelector('.arch-order'),
}));
ok(mobArch.overflow <= 1, '档案页手机也没横向溢出', mobArch);
ok(mobArch.hasOrd, '档案页手机也有排序按钮');

await writePos(snap);
console.log(`  (位置已还原到 round=${snap.round} idx=${snap.idx})`);

ok(pageErrors.length === 0, '0 JS 报错', pageErrors.slice(0, 2));
ok(badResponses.length === 0, '无 4xx/5xx', badResponses.slice(0, 3));

await browser.close();
console.log(`\n${'='.repeat(46)}\nPASS ${PASS}  FAIL ${FAIL}`);
process.exit(FAIL ? 1 : 0);
