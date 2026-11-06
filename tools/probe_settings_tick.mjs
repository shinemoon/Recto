/* 设置页「定时同步」那几行 + 控制台那行提示 (真浏览器)
 *
 * 验的是"人能不能看见 / 能不能改 / 改完是不是真的生效", 不重复服务端逻辑
 * (那部分在 tests/test_sync_tick.py)。
 * 收尾把 sync_* 覆盖项清干净, 不留测试痕迹。
 */
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
const { execFileSync } = require('child_process');

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const token = execFileSync('python3', [new URL('./make_cookie.py', import.meta.url).pathname],
  { encoding: 'utf8' }).trim();

const out = [];
const chk = (name, cond, extra = '') =>
  out.push(`${cond ? 'ok  ' : 'FAIL'} ${name}${extra ? ' — ' + extra : ''}`);

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
page.setDefaultTimeout(20000);
const errs = [];
page.on('pageerror', (e) => errs.push(String(e)));
page.on('console', (m) => { if (m.type() === 'error') errs.push('console: ' + m.text()); });

const openSync = async () => {
  await page.evaluate(() => { const d = document.getElementById('sync'); if (d) d.open = true; });
  await page.waitForTimeout(500);
};

await page.goto(`${BASE}/settings`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#sync', { state: 'attached' });
await openSync();

// ---- 页面结构 ----
chk('定时同步开关在', await page.locator('#sy-tick').count() === 1);
chk('定时间隔输入框在', await page.locator('#sy-tick-sec').count() === 1);
chk('划线开关在', await page.locator('#sy-tick-notes').count() === 1);
chk('时长开关在', await page.locator('#sy-tick-time').count() === 1);
chk('间隔范围 15–600',
  await page.locator('#sy-tick-sec').getAttribute('min') === '15'
  && await page.locator('#sy-tick-sec').getAttribute('max') === '600');
chk('说明提到两条通道',
  /翻页触发/.test(await page.locator('#sync .st-note').first().innerText())
  && /定时触发/.test(await page.locator('#sync .st-note').first().innerText()));

const stateLine = await page.locator('#sy-tick-state').innerText();
chk('状态行有内容', stateLine.trim().length > 0 && stateLine !== '—', stateLine);
chk('状态行说周期或已关闭', /每 \d+ 秒|已关闭/.test(stateLine), stateLine);

// ---- 控件在卡片里不出界 ----
await openSync();
const geo = await page.evaluate(() => {
  const box = document.getElementById('sync').getBoundingClientRect();
  const ids = ['sy-tick', 'sy-tick-sec', 'sy-tick-notes', 'sy-tick-time', 'sy-tick-state'];
  const bad = ids.filter((id) => {
    const e = document.getElementById(id);
    if (!e) return true;
    const r = e.getBoundingClientRect();
    return r.width < 4 || r.right > box.right + 1 || r.left < box.left - 1;
  });
  return { bad, overflow: document.documentElement.scrollWidth > innerWidth };
});
chk('控件都在卡片内', geo.bad.length === 0, JSON.stringify(geo.bad));
chk('桌面无横向溢出', geo.overflow === false);

// ---- 改设置 → 立刻生效 ----
const api = async (body) => await page.evaluate(async (b) => {
  const r = await fetch('/api/settings', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify(b)
  });
  return { status: r.status, json: await r.json() };
}, body);

let r = await api({ sync: { interval_on: true, interval_sec: 30,
                            tick_notes: false, tick_time: true } });
chk('保存 200', r.status === 200 && r.json.ok === true, String(r.status));
chk('回显周期 30', r.json.sync.tick.interval_sec === 30, JSON.stringify(r.json.sync.tick));
chk('回显划线关', r.json.sync.tick.notes === false, JSON.stringify(r.json.sync.tick));
chk('来源标成设置页', r.json.sync.source.interval_sec === 'settings',
  JSON.stringify(r.json.sync.source));

await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForSelector('#sync', { state: 'attached' });
await openSync();
chk('刷新后开关勾着', await page.locator('#sy-tick').isChecked());
chk('刷新后间隔是 30', (await page.locator('#sy-tick-sec').inputValue()) === '30',
  await page.locator('#sy-tick-sec').inputValue());
chk('刷新后划线开关是关的', !(await page.locator('#sy-tick-notes').isChecked()));

// 越界 / 非数字
r = await api({ sync: { interval_sec: 99999 } });
chk('间隔越界报错', r.status === 400 && /0–3600/.test(JSON.stringify(r.json)), String(r.status));
r = await api({ sync: { interval_sec: 'abc' } });
chk('非数字报错', r.status === 400 && /数字/.test(JSON.stringify(r.json)), String(r.status));

// 关掉 → 状态行说已关闭
r = await api({ sync: { interval_on: false, interval_sec: 60 } });
chk('关掉后 tick.enabled=false', r.json.sync.tick.enabled === false,
  JSON.stringify(r.json.sync.tick));
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForSelector('#sync', { state: 'attached' });
await openSync();
chk('关掉后开关不勾', !(await page.locator('#sy-tick').isChecked()));
chk('关掉后状态行写已关闭',
  /已关闭/.test(await page.locator('#sy-tick-state').innerText()),
  await page.locator('#sy-tick-state').innerText());

// ---- 控制台「云端同步」面板那行 ----
await page.goto(`${BASE}/#sync`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#tick-note', { state: 'attached' });
await page.waitForTimeout(1200);
const tickNote = await page.locator('#tick-note').innerText();
chk('控制台有定时提示行', tickNote.trim().length > 0, tickNote);
chk('提示行说周期或已关闭', /定时同步/.test(tickNote) && /每 \d+ 秒|已关闭/.test(tickNote),
  tickNote);

// ---- 手机宽度 ----
await page.setViewportSize({ width: 390, height: 780 });
await page.goto(`${BASE}/settings`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#sync', { state: 'attached' });
await openSync();
const mob = await page.evaluate(() => {
  const box = document.getElementById('sync').getBoundingClientRect();
  const bad = ['sy-tick', 'sy-tick-sec', 'sy-tick-notes', 'sy-tick-time', 'sy-tick-state']
    .filter((id) => {
      const e = document.getElementById(id);
      if (!e) return true;
      const r = e.getBoundingClientRect();
      return r.width < 4 || r.right > box.right + 1 || r.left < box.left - 1;
    });
  return { bad, overflow: document.documentElement.scrollWidth > innerWidth };
});
chk('手机无横向溢出', mob.overflow === false);
chk('手机控件不越界', mob.bad.length === 0, JSON.stringify(mob.bad));

chk('无 JS 报错', errs.filter((e) => !/status of 400/.test(e)).length === 0,
  '（400 是本探针故意打的越界请求）' + errs.join(' | '));

// 收尾: 恢复默认 (清掉覆盖项)
await api({ sync: null });

await ctx.close();
await browser.close();
console.log(out.join('\n'));
const fails = out.filter((l) => l.startsWith('FAIL')).length;
console.log(`\n${out.length - fails} ok / ${fails} FAIL`);
process.exit(fails ? 1 : 0);
