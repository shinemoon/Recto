/* 设置页「书宠开关」—— 一键 disable 所有书宠内容 (真浏览器探针)。
 *
 * 跑法: cd tools && node probe_settings_pet.mjs
 * 环境: RECTO_BASE (默认 5015)
 *
 * 用户口径 (2026-11-23): 「/settings 中增加一个'书宠开关', 可以一键 disable 掉
 * 所有书宠相关的内容」。
 *
 * 验的事:
 *   ① 设置页有「书宠」卡与开关, 初始状态与后端一致
 *   ② 关掉 → 顶栏「书宠」消失; /pet/ 落到书架; /pet/api/* 404;
 *      阅读器 pet-brief 回 enabled=false
 *   ③ 再打开 → 顶栏回来, /pet/ 正常
 *   ④ 0 JS 报错
 *
 * 卫生: 会改 settings.json 的 pet_enabled —— 收尾按开头读到的值**还原**。
 */
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RECTO_BASE || '5015';
const BASE_URL = `http://127.0.0.1:${BASE}`;

let PASS = 0, FAIL = 0;
function ok(cond, name, extra = '') {
  if (cond) { PASS++; console.log('PASS', name); }
  else { FAIL++; console.log('FAIL', name, extra); }
}

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
await ctx.addCookies([{
  name: 'zy_webauth', value: cookie(), domain: '127.0.0.1', path: '/',
}]);
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
// 我们**故意**请求了 /pet/api/char 拿 404 (验"关掉就 404"), 浏览器会打一条
// "Failed to load resource: ... 404" —— 那不是 JS 故障, 滤掉。
// (404 本身由下面那条 `apiStatus === 404` 断言兜着)
page.on('console', (m) => {
  if (m.type() !== 'error') return;
  if (/Failed to load resource/i.test(m.text())) return;
  errors.push(m.text());
});

// ── 初始值 (收尾要还原) ────────────────────────────────────────
await page.goto(`${BASE_URL}/settings`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#pet-on', { timeout: 15000 });
const before = await page.evaluate(async () => {
  const r = await fetch('/api/settings', { credentials: 'same-origin' });
  const d = await r.json();
  return d.pet || {};
});
console.log(`  (初始: enabled=${before.enabled} 来源=${before.source_label})`);

async function setPet(on) {
  await page.evaluate(async (v) => {
    const r = await fetch('/api/settings', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pet_enabled: v ? 'on' : 'off' }),
    });
    return r.ok;
  }, on);
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#pet-on', { timeout: 15000 });
}

// ── ① 卡片与开关 ───────────────────────────────────────────────
const card = await page.evaluate(() => {
  const d = document.querySelector('#pet');
  const sw = document.querySelector('#pet-on');
  const pill = document.querySelector('#pet-pill');
  if (!d || !sw) return null;
  const r = d.getBoundingClientRect();
  return {
    visible: r.height > 40 && r.width > 200,
    open: d.hasAttribute('open'),
    checked: !!sw.checked,
    pill: pill ? pill.textContent.trim() : '',
    hasMsg: !!document.querySelector('#pet-msg'),
    label: d.querySelector('summary') ? d.querySelector('summary').textContent.trim() : '',
  };
});
ok(!!card && card.visible, '设置页有书宠卡且看得见');
ok(!!card && card.open, '默认是展开的 (开关不该藏在折叠里)');
ok(!!card && card.label.startsWith('书宠'), `卡头写着「书宠」 (${card ? card.label : ''})`);
ok(!!card && card.hasMsg, '有回执区 (#pet-msg)');
ok(!!card && card.checked === !!before.enabled,
   `开关状态跟后端一致 (${card ? card.checked : '?'} / ${before.enabled})`);

// ── ② 关掉 ────────────────────────────────────────────────────
await setPet(false);
const off = await page.evaluate(async () => {
  const navHas = !!document.querySelector('.rc-nav a[href="/pet/"]');
  const pill = document.querySelector('#pet-pill');
  const st = await (await fetch('/api/settings', { credentials: 'same-origin' })).json();
  const api = await fetch('/pet/api/char', { credentials: 'same-origin' });
  const brief = await (await fetch('/read/api/pet-brief', { credentials: 'same-origin' })).json();
  return {
    navHas,
    pill: pill ? pill.textContent.trim() : '',
    checked: !!document.querySelector('#pet-on').checked,
    enabled: !!(st.pet || {}).enabled,
    apiStatus: api.status,
    apiBody: await api.json().catch(() => ({})),
    briefOk: brief.ok, briefEnabled: brief.enabled,
  };
});
ok(off.navHas === false, '关掉后顶栏没有「书宠」入口');
ok(off.pill === '已关闭', `卡头标已关闭 (${off.pill})`);
ok(off.checked === false, '开关回读是未选中');
ok(off.enabled === false, '后端也认为是关的');
ok(off.apiStatus === 404, `/pet/api/char 返 404 (${off.apiStatus})`);
ok((off.apiBody || {}).error === 'pet_disabled', '错误码说清楚是书宠关了');
ok(off.briefOk === false && off.briefEnabled === false,
   '阅读器 pet-brief 说"别画" (小图标藏起来)');

// /pet/ 页面 → 落到书架
await page.goto(`${BASE_URL}/pet/`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(400);
const landed = page.url();
const landedPath = new globalThis.URL(landed).pathname;
ok(landedPath === '/' || landedPath === '/index', `/pet/ 落到书架 (${landed})`);
ok(!(await page.$('#card-chronicle')), '书宠页面内容没有出现');

// 手书档案页同样落回家
await page.goto(`${BASE_URL}/pet/story/`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(400);
ok(new globalThis.URL(page.url()).pathname === '/', `档案页也落回家 (${page.url()})`);

// ── ③ 再打开 ──────────────────────────────────────────────────
await page.goto(`${BASE_URL}/settings`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#pet-on', { timeout: 15000 });
await setPet(true);
const on = await page.evaluate(async () => {
  const navHas = !!document.querySelector('.rc-nav a[href="/pet/"]');
  const st = await (await fetch('/api/settings', { credentials: 'same-origin' })).json();
  const brief = await (await fetch('/read/api/pet-brief', { credentials: 'same-origin' })).json();
  return { navHas, enabled: !!(st.pet || {}).enabled, briefOk: brief.ok };
});
ok(on.navHas === true, '打开后顶栏「书宠」回来');
ok(on.enabled === true, '后端也认为是开的');
await page.goto(`${BASE_URL}/pet/`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#card-chronicle', { timeout: 20000 });
ok(true, '/pet/ 页面恢复正常');

// ── ④ 还原成开头的样子 ─────────────────────────────────────────
await page.goto(`${BASE_URL}/settings`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#pet-on', { timeout: 15000 });
await setPet(!!before.enabled);
const restored = await page.evaluate(async () => {
  const st = await (await fetch('/api/settings', { credentials: 'same-origin' })).json();
  return !!(st.pet || {}).enabled;
});
ok(restored === !!before.enabled, `收尾已还原 (${restored} == ${before.enabled})`);

ok(errors.length === 0, '0 JS 报错', errors.slice(0, 3).join(' | '));

await ctx.close();
await browser.close();
console.log(`\nPASS ${PASS} · FAIL ${FAIL}`);
process.exit(FAIL ? 1 : 0);
