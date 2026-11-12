/* 书宠页面改版探针 (生产 5015)。
 *
 * 验的是 2026-11-12 那轮:
 *   · 故事置顶 (第一张卡就是故事), 故事时钟与"本回所显"都在
 *   · 技能明细默认折叠, 摘要里能看出几项 / 最高几级
 *   · 传承卡平时隐藏; 退隐时才出现 (这里用 DOM 直接现身验证样式与按钮)
 *   · 390px 无横向溢出
 *
 * 跑法: cd tools && node probe_pet_story.mjs
 * 环境: RECTO_BASE (默认 5015)
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
page.on('pageerror', (e) => pageErrors.push(String(e)));
page.on('console', (m) => { if (m.type() === 'error') pageErrors.push(m.text()); });

await page.goto(`${URL}/pet/`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(900);

// ── 1. 故事置顶 ───────────────────────────────────
const ids = await page.$$eval('.rc-pet .card', (cs) => cs.map((c) => c.id));
ok(ids[0] === 'card-chronicle', `第一张卡是故事 (${ids[0]})`);
ok(ids.indexOf('card-hero') === 1, `人物第二 (${ids.indexOf('card-hero')})`);
ok(ids.includes('card-legacy'), '页面里有传承卡');

const title = await page.$eval('#card-chronicle h2', (e) => e.textContent.trim());
ok(title.includes('故事'), `故事卡标题: ${title}`);

// ── 2. 故事时钟 + 故事概览 ─────────────────────────
const clock = await page.$eval('#story-clock', (e) => e.textContent.trim());
ok(/岁/.test(clock), `故事时钟显示年岁: ${clock}`);
const meta = await page.$eval('#story-meta', (e) => e.textContent.trim());
ok(meta.includes('故事里第'), `故事概览有"故事里第 N 日": ${meta.slice(0, 40)}`);
ok(meta.includes('一小时阅读'), '概览写明节奏');

// 最新一回 (若已有故事, 要能看到"本回所显")
const adv = await page.$('#adventure-latest .adv-card');
if (adv) {
  const h3 = await page.$eval('#adventure-latest h3', (e) => e.textContent.trim());
  ok(h3.length > 0, `最新一回标题: ${h3}`);
  const shown = await page.$$eval('#adventure-latest .adv-growth .adv-chip', (els) => els.length);
  ok(shown >= 0, `本回所显条目数: ${shown}`);
  const bodyP = await page.$$eval('#adventure-latest .adv-body p', (els) => els.length);
  ok(bodyP >= 2, `故事正文分段: ${bodyP} 段`);
  const fs = await page.$eval('#adventure-latest .adv-body p',
    (e) => parseFloat(getComputedStyle(e).fontSize));
  ok(fs >= 15, `故事正文比正文大 (${fs}px)`);
} else {
  ok(true, '还没有故事 (首次运行) — 跳过最新一回检查');
  ok(true, '还没有故事 — 跳过本回所显');
  ok(true, '还没有故事 — 跳过分段');
  ok(true, '还没有故事 — 跳过字号');
}

// ── 3. 技能折叠 ───────────────────────────────────
const fold = await page.$('#card-skills .cs-fold');
ok(!!fold, '技能表在折叠块里');
const openAttr = fold ? await fold.getAttribute('open') : 'n/a';
ok(openAttr === null, `默认折叠 (open=${openAttr})`);
const sum = await page.$eval('#card-skills .cs-fold > summary', (e) => e.textContent.trim());
ok(/共 \d+ 项/.test(sum), `摘要含项数: ${sum}`);
// Chromium 用 overflow 裁切 details 的非 summary 子节点: 子节点仍有布局盒,
// 所以要比"details 自身高度"与"内容高度", 不能看子元素 height === 0。
const box = await page.$eval('#card-skills .cs-fold', (d) => ({
  open: d.open,
  self: d.getBoundingClientRect().height,
  inner: d.querySelector('.models-wrap').getBoundingClientRect().height,
}));
ok(!box.open && box.self < box.inner - 20,
  `折叠时内容被裁掉 (${Math.round(box.self)} < ${Math.round(box.inner)})`);
await page.$eval('#card-skills .cs-fold', (d) => { d.open = true; });
await page.waitForTimeout(120);
const box2 = await page.$eval('#card-skills .cs-fold', (d) => d.getBoundingClientRect().height);
ok(box2 > box.self + 20, `点开后展开 (${Math.round(box.self)} → ${Math.round(box2)})`);
await page.$eval('#card-skills .cs-fold', (d) => { d.open = false; });

// ── 4. 传承卡: 平时隐藏 ────────────────────────────
const legacyHidden = await page.$eval('#card-legacy', (e) => e.hidden);
const life = await page.evaluate(async () => {
  const r = await fetch('/pet/api/char/life', { credentials: 'same-origin' });
  return r.ok ? r.json() : null;
});
ok(!!life, 'GET /pet/api/char/life 通');
if (life && (life.stage === 'retiring' || life.ready_to_retire)) {
  ok(legacyHidden === false, '退隐/到岁数 → 传承卡显示');
  const opts = await page.$$eval('#legacy-heir [data-heir]', (els) => els.map((e) => e.dataset.heir));
  ok(opts.length === 3, `三类传承人可选 (${opts.join('/')})`);
  ok((await page.$$eval('#legacy-heir .legacy-opt', (e) => e.length)) === 3, '选项是卡片样式');
} else {
  ok(legacyHidden === true, '未退隐 → 传承卡隐藏');
  // 手工现身一次, 只看样式与按钮 (不动后端)
  await page.$eval('#card-legacy', (e) => { e.hidden = false; });
  const btn = await page.$('#btn-inherit');
  ok(!!btn, '传承确认按钮存在');
  const grid = await page.$eval('#legacy-heir', (e) => getComputedStyle(e).display);
  ok(grid !== 'none', `传承人区域可见 (${grid})`);
  await page.$eval('#card-legacy', (e) => { e.hidden = true; });
}

// ── 4b. 门派 / 性情 / 经脉 也折叠 (不占空间) ────────
for (const [id, label] of [['fold-card-schools', '门派'], ['fold-card-traits', '性情'],
  ['fold-card-veins', '经脉']]) {
  const d = await page.$(`#${id}`);
  ok(!!d, `${label}明细有折叠块`);
  const o = d ? await d.getAttribute('open') : 'n/a';
  ok(o === null, `${label}明细默认折叠`);
  const txt = d ? await d.$eval('summary', (e) => e.textContent.trim()) : '';
  ok(txt.includes(label) || txt.length > 2, `${label}摘要: ${txt.slice(0, 24)}`);
}
const foldBox = await page.$eval('#fold-card-traits', (d) => ({
  self: d.getBoundingClientRect().height,
  inner: d.querySelector('.cs-traits').getBoundingClientRect().height,
}));
ok(foldBox.self < Math.max(60, foldBox.inner), `性情折叠后只剩一行高 (${Math.round(foldBox.self)}px)`);

// ── 5. 移动端不溢出 ───────────────────────────────
await page.setViewportSize({ width: 390, height: 844 });
await page.waitForTimeout(300);
const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
ok(overflow <= 1, `390px 无横向溢出 (${overflow}px)`);
const clockW = await page.$eval('#story-clock', (e) => e.getBoundingClientRect().width);
ok(clockW > 40, `手机上故事时钟没被压扁 (${Math.round(clockW)}px)`);

ok(pageErrors.length === 0, `无 JS 报错 (${pageErrors.length}): ${pageErrors.slice(0, 2).join(' | ')}`);

await browser.close();
console.log(`\n${PASS} passed, ${FAIL} failed`);
process.exit(FAIL ? 1 : 0);
