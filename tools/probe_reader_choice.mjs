/* 云端更靠前时的「问读者」选择条 —— 只看用户看得见的那一面。
 *
 *  1. 打开书时云端更靠前 → 弹选择条 (不是直接把位置拽走)
 *  2. 选择条写清两端: 云端读到 X%（第 N 章）/ 这里停在 Y%
 *  3. 「留在本地」→ 发 choice=local, 提示写在本地并覆盖云端
 *  4. 「跳到云端」→ 发 choice=cloud, 位置真的跳到云端锚点
 *  5. 不选 → 12 秒自己收起, 位置不动
 *  6. 点击后选择条消失; 手机关闭后不溢出
 *
 * 跑法: node tools/probe_reader_choice.mjs   (RV_BASE / RV_KEY 可覆盖)
 * 会拦截 /sync 与 /sync-choice (不真写云端), 并快照/还原本地进度。
 */
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || 'a6f63af571f05bb4';
const token = cookie();

const out = [];
const chk = (name, cond, extra = '') =>
  out.push(`${cond ? 'ok  ' : 'FAIL'} ${name}${extra ? ' — ' + extra : ''}`);

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);

const FAKE_SYNC = {
  ok: true, need_choice: true,
  cloud_pct: 0.8231, local_pct: 0.4102,
  cloud_chapter: 7, cloud_offset: 34893,
  anchor: { chapter: 8, offset: 34893, percent: 0.8231 },
  state: { state: 'synced', settings: { auto: true, pull_on_open: true, stamp: true } },
  settings: { auto: true, pull_on_open: true, stamp: true },
};
const SENT = [];

let page = null;
async function readProgress(pg) {
  return await pg.evaluate(async (k) => {
    const r = await fetch('/read/api/book/' + k, { headers: { accept: 'application/json' } });
    const j = await r.json();
    return (j && j.progress) || null;
  }, KEY);
}
async function writeProgress(pg, p) {
  if (!p) return;
  await pg.evaluate(async ([k, prog]) => {
    await fetch('/read/api/book/' + k + '/progress', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chapter: prog.chapter, offset: prog.offset,
                             force: 1, jump: 1, silent: 1 }),
    });
  }, [KEY, p]);
}

async function newPage(vp) {
  const c = await browser.newContext({ viewport: vp });
  await c.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
  const pg = await c.newPage();
  await pg.route('**/read/api/book/*/sync', (route) => {
    if (route.request().method() !== 'GET') return route.continue();
    return route.fulfill({ status: 200, contentType: 'application/json',
                           body: JSON.stringify(FAKE_SYNC) });
  });
  await pg.route('**/read/api/book/*/sync-choice', async (route) => {
    let body = {};
    try { body = JSON.parse(route.request().postData() || '{}'); } catch (e) { /* noop */ }
    SENT.push(body);
    const res = body.choice === 'cloud'
      ? { ok: true, cloud_pct: 0.8231,
          anchor: { chapter: 8, catalog: 7, offset: 34893, percent: 0.8231 },
          state: { state: 'synced' } }
      : { ok: true, pushed: 1, why: 'force', pct: 0.4102, state: { state: 'synced' } };
    return route.fulfill({ status: 200, contentType: 'application/json',
                           body: JSON.stringify(res) });
  });
  pg.on('pageerror', (e) => out.push('FAIL pageerror — ' + e.message));
  return pg;
}

// ---------- 1. 桌面: 选择条出现 ----------
page = await newPage({ width: 1280, height: 900 });
// 探针会把位置跳到云端锚点 —— 开头快照, 收尾写回 (tests/test_probe_hygiene.py 钉这条)
await page.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
const snap = await readProgress(page);
await page.waitForSelector('#rv-content .rv-blk', { timeout: 20000 });

let el = await page.waitForSelector('#rv-choice', { state: 'visible', timeout: 15000 })
  .catch(() => null);
chk('云端更靠前 → 弹出选择条', !!el);

const box = el ? await el.boundingBox() : null;
chk('选择条在视口内', !!box && box.y > 0 && box.y + box.height <= 900,
    box ? `y=${Math.round(box.y)} h=${Math.round(box.height)}` : 'no box');

const txt = (await page.textContent('#rv-choice-txt')) || '';
chk('写明云端读到多少', /82\.3%/.test(txt), txt.trim());
chk('写明云端在第几章', /第\s*8\s*章/.test(txt), txt.trim());
chk('写明本地停在哪', /41\.0%/.test(txt), txt.trim());
chk('有不选的出路说明', /下次打开再问/.test((await page.textContent('.rv-choice-hint')) || ''));

const btnCloud = await page.textContent('#rv-choice [data-c="cloud"]');
const btnLocal = await page.textContent('#rv-choice [data-c="local"]');
chk('两个按钮文案', /跳到云端/.test(btnCloud) && /留在本地/.test(btnLocal),
    `${btnCloud} / ${btnLocal}`);

// 正文在这一刻没有被拽走 (用户还在原来那一页)
const before = await page.evaluate(() => (document.querySelector('#rv-pct') || {}).textContent);
chk('没有自动跳转 (进度数字没变)', typeof before === 'string' && !/82\.3%/.test(before),
    before);

// ---------- 2. 留在本地 ----------
SENT.length = 0;
await page.click('#rv-choice [data-c="local"]');
await page.waitForTimeout(900);
chk('发了 choice=local', SENT.length === 1 && SENT[0].choice === 'local', JSON.stringify(SENT));
chk('选择条收起', !(await page.isVisible('#rv-choice')));
const toastTxt = (await page.textContent('#rv-toast')) || '';
chk('提示写在本地并覆盖云端', /留在本地/.test(toastTxt) && /云端/.test(toastTxt), toastTxt.trim());

// ---------- 3. 跳到云端 ----------
SENT.length = 0;
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForSelector('#rv-choice', { state: 'visible', timeout: 15000 });
await page.click('#rv-choice [data-c="cloud"]');
await page.waitForTimeout(1500);
chk('发了 choice=cloud', SENT.length === 1 && SENT[0].choice === 'cloud', JSON.stringify(SENT));
chk('选择条收起 (跳完)', !(await page.isVisible('#rv-choice')));
const toast2 = (await page.textContent('#rv-toast')) || '';
chk('提示已同步掌阅进度', /已同步掌阅进度/.test(toast2), toast2.trim());
const after = await page.evaluate(() => (document.querySelector('#rv-pct') || {}).textContent);
chk('位置真的跳到云端 (≈82%)', /82\.\d%/.test(after || ''), after);

await writeProgress(page, snap);
await page.context().close();
page = null;

// ---------- 4. 手机宽度 ----------
page = await newPage({ width: 390, height: 780 });
await page.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#rv-choice', { state: 'visible', timeout: 15000 }).catch(() => {});
const m = await page.evaluate(() => {
  const el = document.getElementById('rv-choice');
  if (!el || el.hidden) return null;
  const b = el.getBoundingClientRect();
  const c = el.querySelector('[data-c="cloud"]').getBoundingClientRect();
  const l = el.querySelector('[data-c="local"]').getBoundingClientRect();
  return {
    left: b.left, right: b.right, top: b.top, bottom: b.bottom,
    h: b.height, cloudH: c.height, localH: l.height,
    overflow: document.documentElement.scrollWidth > window.innerWidth + 1,
  };
});
chk('手机上也弹出', !!m);
chk('手机不横向溢出', !!m && m.overflow === false && m.left >= 0 && m.right <= 390,
    m ? `L=${Math.round(m.left)} R=${Math.round(m.right)}` : '');
chk('手机按钮可点 (≥28px)', !!m && m.cloudH >= 28 && m.localH >= 28,
    m ? `h=${Math.round(m.cloudH)}/${Math.round(m.localH)}` : '');
chk('选择条不贴底 (让出页码)', !!m && m.bottom < 780 - 20, m ? `bottom=${Math.round(m.bottom)}` : '');

// ---------- 5. 不选 → 自己收起 ----------
await page.waitForTimeout(13000);
chk('不选 12 秒后自己收起', !(await page.isVisible('#rv-choice')));

await page.context().close();
await browser.close();

console.log(out.join('\n'));
const bad = out.filter((l) => l.startsWith('FAIL'));
console.log(`\n${out.length - bad.length} passed, ${bad.length} failed`);
process.exit(bad.length ? 1 : 0);
