/* 书宠 —— 游戏化界面探针 (子页签 / HUD / 世界页 / 复位滑块)。
 *
 * 跑法: cd tools && node probe_pet_ui.mjs
 * 环境: RECTO_BASE (默认 5015)
 *
 * **注意**: 这个探针**不点**「复位 / 转生」—— 那会真的清空角色与故事。
 * 它只验滑块的上限、提示文字与按钮存在。要验提交路径看 tests/test_pet_autonomy.py。
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
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });

await page.goto(`${URL}/pet/`, { waitUntil: 'networkidle' });
await page.waitForSelector('#pg-bar');

// ── HUD ──────────────────────────────────────────────
const hud = await page.evaluate(() => ({
  name: document.querySelector('#bar-name')?.textContent?.trim() || '',
  lv: document.querySelector('#bar-level')?.textContent?.trim() || '',
  rank: document.querySelector('#bar-rank')?.textContent?.trim() || '',
  life: document.querySelector('#bar-life')?.textContent?.trim() || '',
  gauges: document.querySelectorAll('#bar-gauges .pg-gauge').length,
  chips: document.querySelectorAll('#bar-chips .pg-chip').length,
  barH: document.querySelector('#pg-bar')?.getBoundingClientRect().height || 0,
}));
ok(!!hud.name, `状态栏有名字: ${hud.name}`);
ok(/^Lv\.\d+/.test(hud.lv), `状态栏有等级: ${hud.lv}`);
ok(!!hud.rank, `状态栏有头衔: ${hud.rank}`);
ok(/岁/.test(hud.life) && /第 \d+ 日/.test(hud.life), `状态栏有时序: ${hud.life}`);
ok(hud.gauges === 3, `三条量条 (气血/内力/修为) got ${hud.gauges}`);
ok(hud.chips >= 3, `至少 3 个数值 chip got ${hud.chips}`);
ok(hud.barH > 60 && hud.barH < 200, `状态栏高度合理 got ${Math.round(hud.barH)}`);

// ── 子页签 ───────────────────────────────────────────
const tabs = await page.$$eval('.pg-tab', (els) => els.map((e) => e.textContent.trim()));
ok(tabs.length === 4, `4 个子页签 got ${tabs.join('/')}`);
ok(tabs[0] === '故事', '默认第一个是「故事」');
const firstOn = await page.evaluate(() =>
  document.querySelector('.pg-pane.is-on')?.dataset.pg);
ok(firstOn === 'story', `默认打开故事页 got ${firstOn}`);

async function openPane(name) {
  await page.click(`.pg-tab[data-pg="${name}"]`);
  await page.waitForTimeout(180);
  return page.evaluate(() => ({
    on: document.querySelector('.pg-pane.is-on')?.dataset.pg,
    hash: location.hash,
    visible: Array.from(document.querySelectorAll('.pg-pane.is-on'))
      .reduce((n, el) => n + (el.getBoundingClientRect().height > 0 ? 1 : 0), 0),
  }));
}

for (const name of ['hero', 'world', 'setup', 'story']) {
  const st = await openPane(name);
  ok(st.on === name && st.hash === `#${name}`, `切到「${name}」且地址记下 (${st.hash})`);
  ok(st.visible >= 1, `「${name}」有可见内容`);
}

// 只有一个 pane 可见 (页内切换, 不是叠在一起)
const panesOn = await page.evaluate(() =>
  document.querySelectorAll('.pg-pane.is-on').length);
ok(panesOn === 1, `同一时刻只有一个子页 got ${panesOn}`);

// ── 世界页 ───────────────────────────────────────────
await openPane('world');
await page.waitForSelector('#world-regions .pg-world-item', { timeout: 8000 })
  .catch(() => {});
const wd = await page.evaluate(() => ({
  name: document.querySelector('#world-name')?.textContent?.trim() || '',
  regions: document.querySelectorAll('#world-regions .pg-world-item').length,
  factions: document.querySelectorAll('#world-factions .pg-world-item').length,
  notables: document.querySelectorAll('#world-notables .pg-world-item').length,
  things: document.querySelectorAll('#world-things .pg-world-item').length,
  pill: document.querySelector('#world-pill')?.textContent?.trim() || '',
}));
ok(!!wd.name && wd.name !== '—', `世界有名字: ${wd.name}`);
ok(wd.regions >= 3, `有去处 got ${wd.regions}`);
ok(wd.factions >= 6, `有势力 (含六门派) got ${wd.factions}`);
ok(wd.notables >= 3, `有人物 got ${wd.notables}`);
ok(/处/.test(wd.pill), `世界 pill 有文案: ${wd.pill}`);

// 专名闸门
await page.waitForTimeout(400);
const pd = await page.evaluate(() => ({
  words: (document.querySelector('#proper-pill')?.textContent || '').trim(),
  chips: document.querySelectorAll('#proper-chips .pg-chip').length,
  rows: document.querySelectorAll('#proper-state .cs-meta-row').length,
}));
ok(/个专属名/.test(pd.words), `专名 pill: ${pd.words}`);
ok(pd.rows >= 2, `专名说明行 got ${pd.rows}`);
ok(pd.chips >= 1, `列出了一些被挡的词 got ${pd.chips}`);

// ── 设置页: 复位滑块 (只验界面, 不提交) ─────────────────
await openPane('setup');
const rb = await page.evaluate(() => {
  const inp = document.querySelector('#reborn-inherit');
  return {
    exists: !!inp,
    max: inp ? Number(inp.max) : -1,
    val: inp ? Number(inp.value) : -1,
    btn: !!document.querySelector('#btn-reborn'),
    pill: (document.querySelector('#reborn-pill')?.textContent || '').trim(),
    hint: (document.querySelector('#reborn-hint')?.textContent || '').trim(),
  };
});
ok(rb.exists && rb.btn, '有复位滑块与按钮');
ok(rb.max >= 0, `滑块上限 = 现有阅历 got ${rb.max}`);
ok(/分钟|还没有阅历/.test(rb.pill), `复位 pill: ${rb.pill}`);
ok(/白纸复位|转生/.test(rb.hint), `复位提示: ${rb.hint.slice(0, 30)}`);

if (rb.max > 100) {
  await page.evaluate(() => {
    const inp = document.querySelector('#reborn-inherit');
    inp.value = String(Math.round(Number(inp.max) / 2));
    inp.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await page.waitForTimeout(120);
  const mid = await page.evaluate(() => ({
    val: document.querySelector('#reborn-val')?.textContent || '',
    hint: document.querySelector('#reborn-hint')?.textContent || '',
  }));
  ok(/转生/.test(mid.hint) && /岁/.test(mid.hint),
     `拖到一半显示起点岁数: ${mid.hint.slice(0, 42)}`);
  ok(/\d+ 分钟/.test(mid.val), `数值跟着走: ${mid.val}`);
}

// 设置项都在设置页, 不在故事页
const leak = await page.evaluate(() => {
  const story = document.querySelector('.pg-pane[data-pg="story"]');
  return {
    llm: !!story.querySelector('#llm-base'),
    models: !!story.querySelector('#model-rows'),
    reborn: !!story.querySelector('#reborn-inherit'),
    persona: !!story.querySelector('#pet-name-input'),
  };
});
ok(!leak.llm && !leak.models && !leak.reborn && !leak.persona,
   '设置项没漏进故事页');

// ── 窄屏 ────────────────────────────────────────────
const m = await ctx.newPage();
await m.setViewportSize({ width: 390, height: 844 });
await m.goto(`${URL}/pet/`, { waitUntil: 'networkidle' });
await m.waitForSelector('#pg-bar');
const mob = await m.evaluate(() => ({
  overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  tabsW: document.querySelector('.pg-tabs')?.scrollWidth || 0,
  tabsC: document.querySelector('.pg-tabs')?.clientWidth || 0,
  gauge: document.querySelectorAll('#bar-gauges .pg-gauge').length,
}));
ok(mob.overflow <= 0, `手机 390px 无横向溢出 (${mob.overflow}px)`);
ok(mob.tabsW >= mob.tabsC, '页签可横滑 (不会被挤爆)');
ok(mob.gauge === 3, '手机上也显示三条量条');

ok(errors.length === 0, `无 JS 报错 (${errors.length}) ${errors.slice(0, 2).join(' | ')}`);

console.log('');
console.log('='.repeat(46));
console.log(`${PASS} passed, ${FAIL} failed`);
await browser.close();
process.exit(FAIL === 0 ? 0 : 1);
