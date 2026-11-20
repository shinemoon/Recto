/* 书宠页面 —— 读故事 + 人物抽屉 (真浏览器探针)。
 *
 * 跑法: cd tools && node probe_pet_read.mjs
 * 环境: RECTO_BASE (默认 5015)
 *
 * 验的事:
 *   · 顶签从 4 个变 3 个 (故事 / 世界 / 设置), 「人物」收进图标抽屉
 *   · 图标点开 → 抽屉浮出来, 里面四段 (人物 / 行囊 / 本事 / 陪读) 能切
 *   · 故事: 一条连续的路 (空格推一段) —— 细节由 probe_pet_story_flow.mjs 验
 *   · 位置记在后端 (刷新页面接着上次那段)
 *   · 390px 无横向溢出 / 0 JS 报错
 *
 * 卫生: 只读 + 本地 UI 操作。不点「出门走一趟」(那会真的推演)。
 *      阅读位置会写后端 —— 收尾**还原**到跑之前那个值。
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

/** 写位置 (收尾还原用; 位置默认只前进, 还原要带 force)。 */
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

// ── 1. 顶签: 4 → 3 ───────────────────────────────────
const tabs = await page.$$eval('.pg-tab', (els) => els.map((e) => e.textContent.trim()));
ok(tabs.length === 3, `3 个子页签 got ${tabs.join('/')}`);
ok(tabs[0] === '故事', '第一个是「故事」');
ok(!tabs.includes('人物'), '「人物」不再是子页签');
const onPane = await page.evaluate(() => document.querySelector('.pg-pane.is-on')?.dataset.pg);
ok(onPane === 'story', `默认落在故事页 got ${onPane}`);

// ── 2. 图标快捷入口 ───────────────────────────────────
const ico = await page.$$eval('.pg-ico-btn', (els) => els.map((e) => ({
  k: e.dataset.drawer, label: e.textContent.trim(),
})));
ok(ico.length === 4, `4 个图标 got ${ico.length}`);
ok(ico.map((x) => x.k).join(',') === 'char,bag,skills,companion',
   `图标对应四段 got ${ico.map((x) => x.k).join(',')}`);
ok(ico.every((x) => x.label.length > 0), '每个图标都有名字');

// ── 3. 抽屉开合 ───────────────────────────────────────
const drawerHidden = await page.$eval('#pg-drawer', (e) => e.hidden);
ok(drawerHidden === true, '抽屉默认收起');
await page.click('.pg-ico-btn[data-drawer="char"]');
await page.waitForTimeout(300);
const dr = await page.evaluate(() => {
  const d = document.querySelector('#pg-drawer');
  return {
    hidden: d.hidden,
    title: document.querySelector('#pg-drawer-title')?.textContent?.trim() || '',
    panes: Array.from(document.querySelectorAll('.pg-dt-pane.is-on')).map((e) => e.dataset.dt),
    chars: document.querySelectorAll('#pg-drawer .card').length,
    panelW: document.querySelector('.pg-drawer-panel')?.getBoundingClientRect().width || 0,
  };
});
ok(!dr.hidden, '点图标 → 抽屉打开');
ok(dr.title === '人物', `抽屉标题跟着图标走 got ${dr.title}`);
ok(dr.panes.length === 1 && dr.panes[0] === 'char', `只显示一段 got ${dr.panes}`);
ok(dr.chars >= 8, `八张卡都在抽屉里 got ${dr.chars}`);
ok(dr.panelW > 300 && dr.panelW <= 560, `抽屉宽度合理 got ${Math.round(dr.panelW)}`);

// 切小段
await page.click('.pg-dt-tab[data-dt="skills"]');
await page.waitForTimeout(220);
const sk = await page.evaluate(() => ({
  pane: document.querySelector('.pg-dt-pane.is-on')?.dataset.dt,
  title: document.querySelector('#pg-drawer-title')?.textContent?.trim() || '',
  ids: Array.from(document.querySelectorAll('.pg-dt-pane.is-on .card')).map((c) => c.id),
}));
ok(sk.pane === 'skills' && sk.title === '本事', `切到「本事」got ${sk.pane}/${sk.title}`);
ok(sk.ids.includes('card-attrs') && sk.ids.includes('card-skills')
   && sk.ids.includes('card-traits') && sk.ids.includes('card-veins'),
   `本事段有四张卡 got ${sk.ids.join(',')}`);

// 陪读段
await page.click('.pg-dt-tab[data-dt="companion"]');
await page.waitForTimeout(200);
const cp = await page.evaluate(() => ({
  pane: document.querySelector('.pg-dt-pane.is-on')?.dataset.dt,
  has: !!document.querySelector('.pg-dt-pane.is-on #card-growth'),
  feed: !!document.querySelector('.pg-dt-pane.is-on #btn-companion-feed'),
}));
ok(cp.pane === 'companion' && cp.has && cp.feed, '陪读段有卡与按钮');

// Esc 关抽屉
await page.keyboard.press('Escape');
await page.waitForTimeout(250);
ok((await page.$eval('#pg-drawer', (e) => e.hidden)) === true, 'Esc 关抽屉');

// ── 4. 读故事: 一条连续的路 ───────────────────────────
const read = await page.evaluate(() => ({
  open: !document.querySelector('#adv-read')?.hidden,
  paras: document.querySelectorAll('#adv-read-body p').length,
  prog: document.querySelector('#adv-read-prog')?.textContent?.trim() || '',
}));
ok(read.open, '读本摊开着');
ok(read.paras >= 1, `读本里有正文 (${read.paras} 段)`);
ok(/^\d+ \/ \d+ 段$/.test(read.prog), `进度按段记 (${read.prog})`);

// 空格推进一段
const before = read.paras;
await page.keyboard.press('Space');
await page.waitForTimeout(280);
const after = await page.evaluate(() => document.querySelectorAll('#adv-read-body p').length);
ok(after === before + 1, `空格推进一段 (${before} → ${after})`);

// 位置记在后端
await page.waitForTimeout(300);
const posNow = await readPos();
ok(posNow > 0, `位置记在后端 (idx=${posNow})`);

// 刷新 → 接着上次那段
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForSelector('#adv-read-body p', { timeout: 15000 });
await page.waitForTimeout(900);
const afterReload = await page.evaluate(() =>
  document.querySelectorAll('#adv-read-body p').length);
ok(afterReload === after, `刷新后接着上次那段 (${afterReload} 段)`);

// ── 5. 窄屏 ───────────────────────────────────────────
const m = await ctx.newPage();
await m.setViewportSize({ width: 390, height: 844 });
await m.goto(`${URL}/pet/`, { waitUntil: 'domcontentloaded' });
await m.waitForSelector('#pg-bar');
await m.waitForTimeout(900);
const mob = await page.evaluate(() => ({
  overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  rail: document.querySelectorAll('.pg-ico-btn').length,
  railW: document.querySelector('.pg-ico-rail')?.scrollWidth || 0,
  railC: document.querySelector('.pg-ico-rail')?.clientWidth || 0,
}));
ok(mob.overflow <= 0, `手机 390px 无横向溢出 (${mob.overflow}px)`);
ok(mob.rail === 4, '手机上也看得到四个图标');

// 手机上抽屉占满宽
await m.click('.pg-ico-btn[data-drawer="char"]');
await m.waitForTimeout(300);
const mdr = await m.evaluate(() => ({
  w: document.querySelector('.pg-drawer-panel')?.getBoundingClientRect().width || 0,
  win: window.innerWidth,
  overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
}));
ok(Math.abs(mdr.w - mdr.win) < 2, `手机抽屉占满宽 (${mdr.w}/${mdr.win})`);
ok(mdr.overflow <= 0, '抽屉开着也不横向溢出');

// ── 6. 还原位置 (收尾) ─────────────────────────────────
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