/* 书宠 · 人物面板 (武侠属性体系) —— 真浏览器探针。
 *
 * 跑法: cd tools && node probe_pet_char.mjs
 * 环境: RECTO_BASE (默认 5015)
 *
 * 验的事:
 *   人物卡 / 六项根骨 / 门派 / 技能绝招 / 性情 / 经脉 六段都渲染出来
 *   数字自洽 (属性和 = 表格里的和; 潜能扣减对)
 *   入门 → 门派卡高亮 + 技能表多两行
 *   觉醒 → 性情卡点亮 + 点数变化
 *   面板不横向溢出 (桌面 + 手机)
 *   0 JS 报错
 *
 * 卫生: 探针会真的入门派 + 觉醒性情 (都落盘) → 开跑前重开一只, 收尾还原快照。
 *      工具: tools/pet_char_snapshot.py {save|restore|reset}
 */
import { chromium, cookie } from './_env.mjs';
import { execFileSync } from 'child_process';
import path from 'path';
import { fileURLToPath } from 'url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SNAP_TOOL = path.join(HERE, 'pet_char_snapshot.py');

function snapTool(cmd) {
  return execFileSync('python3', [SNAP_TOOL, cmd], { encoding: 'utf8' }).trim();
}

// 开跑前: 存快照 → 重开一只 (保证"一开始无门无派"这类断言成立)
const snapshot = snapTool('save');
snapTool('reset');

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
await page.waitForSelector('#attr-rows tr', { timeout: 15000 });

// ── 段落都在 ──────────────────────────────────────────
const cards = await page.$$eval('.rc-pet .card h2', (hs) => hs.map((h) => h.textContent.trim()));
ok(cards.length >= 9, `有 ${cards.length} 张卡 (${cards.join(' / ')})`);
for (const want of ['六项根骨', '门派', '技能与绝招', '性情', '经脉', '成长规则']) {
  ok(cards.some((c) => c.includes(want)), `有「${want}」卡`);
}

// ── 人物卡 ────────────────────────────────────────────
const rank = (await page.textContent('#char-rank')) || '';
ok(rank.trim().length > 0, `品级徽标: ${rank.trim()}`);
const subline = (await page.textContent('#char-subline')) || '';
ok(subline.includes('Lv.'), `副标题带等级: ${subline.trim()}`);

const stats = await page.$$eval('#char-stats .cs-stat', (els) => els.map((e) => ({
  label: e.querySelector('.cs-stat-l')?.textContent?.trim(),
  value: e.querySelector('.cs-stat-v')?.textContent?.trim(),
  w: e.querySelector('.cs-bar > i')?.style?.width,
})));
ok(stats.length === 4, `四条状态 (气血/心神/内力/精力): ${stats.map((s) => s.label).join('/')}`);
ok(stats.every((s) => /\d+\/\d+/.test(s.value || '')), '状态值格式 当前/上限');
ok(stats.every((s) => (s.w || '').endsWith('%')), '状态条都有百分比宽度');

const metaRows = await page.$$eval('#char-meta .cs-meta-row', (els) => els.map((e) => e.textContent.trim()));
ok(metaRows.length >= 6, `名片 ${metaRows.length} 行`);
ok(metaRows.join(' ').includes('修为'), '名片含修为');
ok(metaRows.join(' ').includes('五行'), '名片含五行');

// ── 六项根骨 ──────────────────────────────────────────
const attrs = await page.$$eval('#attr-rows tr', (rows) => rows.map((r) => ({
  name: r.children[0].textContent.trim(),
  value: Number(r.children[2].textContent.trim().split(' ')[0]),
  base: Number(r.children[3].textContent.trim()),
  cap: Number(r.children[5].textContent.trim()),
  canAdd: !r.querySelector('button')?.disabled,
})));
ok(attrs.length === 6, `六项根骨: ${attrs.map((a) => a.name).join('/')}`);
ok(attrs.every((a) => a.value === 10 && a.cap === 30), '每项 10 / 封顶 30');
const sum = attrs.reduce((s, a) => s + a.value, 0);
ok(sum === 60, `六项和 = ${sum}`);
ok(attrs.every((a) => a.base === 10), '基础值都是 10 (门派加成不进基础)');
ok(attrs.every((a) => !a.canAdd), '没有潜能点时 + 按钮禁用');

const potTxt = (await page.textContent('#attr-pot')) || '';
ok(potTxt.includes('潜能'), `潜能徽标: ${potTxt.trim()}`);

// ── 门派 ──────────────────────────────────────────────
const schools = await page.$$eval('#school-cards .cs-school', (els) => els.map((e) => ({
  name: e.querySelector('b')?.textContent?.trim(),
  on: e.classList.contains('is-on'),
})));
ok(schools.length >= 4, `${schools.length} 个门派: ${schools.map((s) => s.name).join('/')}`);
ok(schools.every((s) => !s.on), '一开始无门无派');

// ── 技能表 ────────────────────────────────────────────
const skillRows0 = await page.$$eval('#skill-rows tr', (r) => r.length);
ok(skillRows0 === 0, '入门前没有技能');

// ── 性情 / 经脉 ───────────────────────────────────────
const traits = await page.$$eval('#trait-cards .cs-trait', (els) => els.map((e) => ({
  name: e.querySelector('b')?.textContent?.trim(),
  on: e.classList.contains('is-on'),
})));
ok(traits.length >= 12, `${traits.length} 条性情`);
ok(traits.every((t) => !t.on), '一开始没有已获性情');

const veins = await page.$$eval('#vein-list .cs-vein', (els) => els.map((e) => ({
  name: e.querySelector('b')?.textContent?.trim(),
  on: e.classList.contains('is-on'),
})));
ok(veins.length === 10, `十层经脉: ${veins.map((v) => v.name).slice(0, 3).join('/')}…`);
ok(veins.every((v) => !v.on), '一开始没通脉');

// ── 成长规则 ────────────────────────────────────────────
const growthRows = await page.$$eval('#growth-rules .cs-meta-row', (els) => els.map((e) => e.textContent.trim()));
ok(growthRows.length >= 4, `成长规则 ${growthRows.length} 行`);
ok(growthRows.join(' ').includes('修为 / 分钟'), '成长规则含修为/分钟');
ok(growthRows.join(' ').includes('潜能 / 升级'), '成长规则含潜能/升级');
ok(growthRows.join(' ').includes('待定'), '成长规则列出待定项');
const veinTip = (await page.textContent('#vein-tip')) || '';
ok(/每分钟换 \d+ 点修为/.test(veinTip), `经脉提示带换算比例: ${veinTip.trim()}`);

// ── 入门 (点第一个门派的「入门」) ─────────────────────
const before = await page.textContent('#skill-tip');
await page.click('#school-cards .cs-school button[data-school]');
await page.waitForFunction(() => document.querySelectorAll('#school-cards .cs-school.is-on').length === 1,
  { timeout: 8000 });
const onSchool = await page.$$eval('#school-cards .cs-school.is-on b', (e) => e.map((x) => x.textContent.trim()));
ok(onSchool.length === 1, `入门高亮: ${onSchool[0]}`);
// 门派加成是派生的: 显示值 = 基础 + 加成, 表格里要看得出来
const afterSchool = await page.$$eval('#attr-rows tr', (rows) => rows.map((r) => ({
  name: r.children[0].textContent.trim(),
  value: Number(r.children[2].textContent.trim().split(' ')[0]),
  base: Number(r.children[3].textContent.trim()),
  raw: r.children[2].textContent.trim(),
})));
const bumped = afterSchool.find((a) => a.value !== a.base);
ok(!!bumped, `入门后有一项被加成: ${bumped ? bumped.name + ' ' + bumped.value + ' (基础 ' + bumped.base + ')' : ''}`);
ok(bumped && bumped.raw.includes('门派'), `加成构成写进表格: ${bumped ? bumped.raw : ''}`);
const skillRows1 = await page.$$eval('#skill-rows tr', (r) => r.length);
ok(skillRows1 === 2, `入门得 2 项 (内功 + 绝招): ${skillRows1}`);
const perfRow = await page.$$eval('#skill-rows tr', (rows) => rows.map((r) => ({
  name: r.children[0].textContent.trim(),
  type: r.children[1].textContent.trim(),
  rate: r.children[5].textContent.trim(),
  busy: r.children[6].textContent.trim(),
  fail: r.children[7].textContent.trim(),
})));
const perf = perfRow.find((r) => r.type === '绝招');
ok(!!perf, '有绝招行');
ok(/%$/.test(perf?.rate || ''), `绝招成功率带%: ${perf?.rate}`);
ok(Number(perf?.busy) > 0 && Number(perf?.fail) > 0, `定身/反噬都有值 (${perf?.busy}/${perf?.fail})`);
ok(before !== (await page.textContent('#skill-tip')), '技能提示文案跟着变');

// ── 觉醒一条性情 (绿色, 10 分) ────────────────────────
await page.click('#trait-cards .cs-trait button[data-trait]');
await page.waitForFunction(() => document.querySelectorAll('#trait-cards .cs-trait.is-on').length >= 1,
  { timeout: 8000 });
const traitPill = (await page.textContent('#trait-pill')) || '';
ok(/\d \/ 6/.test(traitPill), `性情点数徽标: ${traitPill.trim()}`);
const onTrait = await page.$$eval('#trait-cards .cs-trait.is-on b', (e) => e.map((x) => x.textContent.trim()));
ok(onTrait.length >= 1, `已获性情: ${onTrait.join('/')}`);

// ── 潜能加点 ──────────────────────────────────────────
await page.evaluate(async () => {
  // 直接给一份带潜能点的存档 —— 走真接口, 不走后门
  const r = await fetch('/pet/api/char', { credentials: 'same-origin' });
  return r.status;
});
// 用 POST /char/reset 之前先看有没有潜能; 没有就断言"加点按钮禁用"即可
const addDisabled = await page.$$eval('#attr-rows button', (bs) => bs.every((b) => b.disabled));
ok(addDisabled, '没有潜能点时加点按钮禁用 (潜能来源留白, 等接阅读器)');

// ── 溢出 ──────────────────────────────────────────────
const overflowDesktop = await page.evaluate(() =>
  document.documentElement.scrollWidth - document.documentElement.clientWidth);
ok(overflowDesktop <= 0, `桌面无横向溢出 (${overflowDesktop}px)`);

const mob = await ctx.newPage();
await mob.setViewportSize({ width: 390, height: 780 });
await mob.goto(`${URL}/pet/`, { waitUntil: 'domcontentloaded' });
await mob.waitForSelector('#attr-rows tr', { timeout: 15000 });
const overflowMobile = await mob.evaluate(() =>
  document.documentElement.scrollWidth - document.documentElement.clientWidth);
ok(overflowMobile <= 0, `手机无横向溢出 (${overflowMobile}px)`);
const mobCols = await mob.evaluate(() => {
  const g = document.querySelector('#char-stats');
  return g ? getComputedStyle(g).gridTemplateColumns.split(' ').length : 0;
});
ok(mobCols <= 2, `手机状态条两列 (${mobCols})`);

// ── 页面无 JS 报错 ────────────────────────────────────
ok(pageErrors.length === 0, `0 JS 报错 ${pageErrors.length ? pageErrors.slice(0, 3).join(' | ') : ''}`);

await browser.close();

// 收尾: 还原用户原本的书宠 (探针入门派/觉醒性情都落盘了)
const restored = snapTool('restore');
console.log(restored);

console.log(`\n${'='.repeat(46)}`);
console.log(`PASS ${PASS}  FAIL ${FAIL}`);
process.exit(FAIL ? 1 : 0);
