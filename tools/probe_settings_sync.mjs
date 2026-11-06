/* 设置页「云端同步」卡: 结构 + 开关落盘 + 立刻生效 + 回到 config.toml
 *
 * 收尾会把 settings.json 的 sync_* 覆盖项清干净, 不留测试痕迹。
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
page.on('pageerror', e => errs.push(String(e)));
page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });

await page.goto(`${BASE}/settings`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#sync', { state: 'attached', timeout: 20000 });
await page.evaluate(() => { const d = document.getElementById('sync'); if (d) d.open = true; });
await page.waitForTimeout(600);

const api = async (body) => await page.evaluate(async (b) => {
  const r = await fetch('/api/settings', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify(b)
  });
  return { status: r.status, json: await r.json() };
}, body);

chk('卡片存在', await page.locator('#sync').count() === 1);
chk('状态 pill 已填', /已启用|已关闭/.test(await page.locator('#sy-pill').innerText()),
  await page.locator('#sy-pill').innerText());
chk('开关都在 (4 行为 + 3 定时)', (await page.locator('#sync input[type=checkbox]').count()) === 7,
  String(await page.locator('#sync input[type=checkbox]').count()));
chk('推送间隔输入框 3–300', await page.locator('#sy-gap').getAttribute('min') === '3'
  && await page.locator('#sy-gap').getAttribute('max') === '300');
chk('来源行已填', (await page.locator('#sy-src').innerText()).length > 0,
  await page.locator('#sy-src').innerText());

// 关掉「右下角显示」→ 落盘 → 阅读页小字消失 (probe_reader_sync 已覆盖那半, 这里只看落盘)
let r = await api({ sync: { auto: true, pull_on_open: true, flush_on_hide: true,
                             stamp: false, push_debounce_sec: 12 } });
chk('保存 200', r.status === 200 && r.json.ok === true, String(r.status));
chk('回显 stamp=false', r.json.sync.stamp === false, JSON.stringify(r.json.sync));
chk('来源标成设置页', r.json.sync.source.stamp === 'settings', JSON.stringify(r.json.sync.source));

// 越界 / 非数字要被挡住 (不是静默夹到边界)
r = await api({ sync: { push_debounce_sec: 999 } });
chk('间隔越界报错', r.status === 400 && /3–300/.test(JSON.stringify(r.json)),
  String(r.status));
r = await api({ sync: { push_debounce_sec: 'abc' } });
chk('非数字报错', r.status === 400 && /数字/.test(JSON.stringify(r.json)), String(r.status));

// 回到 config.toml: 覆盖项清干净
r = await api({ sync: null });
chk('回到 config.toml', r.status === 200 && r.json.sync.source.stamp === 'config',
  JSON.stringify(r.json.sync.source));
chk('覆盖项清干净', r.json.sync.has_override === false, JSON.stringify(r.json.sync));

// 手机宽度: 卡片内容不出界
await page.setViewportSize({ width: 390, height: 780 });
await page.waitForTimeout(400);
const geo = await page.evaluate(() => {
  const box = document.getElementById('sync').getBoundingClientRect();
  const wide = [...document.querySelectorAll('#sync input, #sync button')]
    .map((e) => e.getBoundingClientRect())
    .filter((r) => r.right > box.right + 1 || r.left < box.left - 1);
  return { overflow: document.documentElement.scrollWidth > innerWidth, wide: wide.length };
});
chk('手机无横向溢出', geo.overflow === false, JSON.stringify(geo));
chk('手机控件不越界', geo.wide === 0, JSON.stringify(geo));

chk('无 JS 报错', errs.filter((e) => !/status of 400/.test(e)).length === 0,
  '（400 是本探针故意打的越界请求）' + errs.join(' | '));

await ctx.close();
await browser.close();
console.log(out.join('\n'));
const fails = out.filter((l) => l.startsWith('FAIL')).length;
console.log(`\n${out.length - fails} ok / ${fails} FAIL`);
process.exit(fails ? 1 : 0);
