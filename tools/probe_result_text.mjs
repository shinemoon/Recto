// 执行结果 / 最近日志 的人话渲染探针
//   1) 用桩数据覆盖 pull / push / notes / time / links 五种返回形态
//   2) 再真跑一次「从掌阅拉取」, 确认真实 payload 也读得懂
//   3) 断言: 顶部不再是裸 JSON, 数字进 chip, 每本书一行, 原始 JSON 收进折叠区
import { chromium } from './_env.mjs';
import { REPO } from './_env.mjs';
import { execSync } from 'node:child_process';

const BASE = 'http://127.0.0.1:5015';
const cookie = execSync(
  `cd ${REPO} && PYTHONPATH=src python3 -c "import sys;from recto.app import webauth;print(webauth.set_cookie('recto'))"`,
  { encoding: 'utf8' }
).trim();
const val = cookie.split(';')[0].split('=').slice(1).join('=');

const res = [];
const ok = (c, label, extra) => {
  res.push(!!c);
  console.log((c ? 'ok   ' : 'FAIL ') + label + (extra !== undefined ? '  ' + JSON.stringify(extra) : ''));
};

const STUB = {
  pull: {
    ok: true, ms: 1840, updated: 1, skipped: 1, at: '2026-11-04T03:40:00+00:00',
    detail: [
      { book: '示例长篇（多卷）', pct: 0.317195, chapter: 182, offset: 12954, updated: true },
      { book: '示例短篇', skipped: 'local-ahead', cloud: 0.542994, local: 0.542994 }
    ],
    notes: { added: 2, updated: 0, deleted: 0, skipped: 24,
             books: [{ book: '示例长篇（多卷）', notes: 26, marks: 6, deleted: 0 }] },
    time: { days: 6, merged: 6, skipped: 1,
            detail: [{ book: '示例长篇（多卷）', days: 6, merged: 6 }] }
  },
  push: {
    ok: true, ms: 900, pushed: 1, skipped: 1, failed: 0, at: '2026-11-04T03:41:00+00:00',
    detail: [
      { book: '示例长篇（多卷）', pct: 0.317195, why: 'forward', chapter: 158, total: 588, ok: true },
      { book: '示例短篇', skipped: 'noop', pct: 0.542994, cloud: 0.542994 }
    ]
  },
  notes: {
    pushed: 3, pushed_marks: 1, deleted: 0, skipped: 5, errors: [],
    books: [{ book: '示例长篇（多卷）', notes: 3, marks: 1, del_notes: 0, del_marks: 0 }]
  },
  time: {
    day: '2026-11-04', queued: 2, skipped: 1, written: 2, errors: [],
    books: [{ book: '示例长篇（多卷）', minutes: 25 }, { book: '示例短篇', minutes: 23 }],
    pending: []
  },
  links: { ok: true, added: 1, shelf: 4 }
};

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
await ctx.addCookies([{ name: 'zy_webauth', value: val, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));

async function stub(action) {
  await page.route('**/api/sync/run', (route) => {
    const body = JSON.parse(route.request().postData() || '{}');
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify(STUB[body.action] || { ok: true })
    });
  });
}

await page.goto(BASE + '/', { waitUntil: 'networkidle' });
await page.click('[data-pane-link="sync"]');
await page.waitForTimeout(200);
await stub();

const outText = () => page.locator('#out').innerText();

// --- 1) 拉取 --------------------------------------------------------------
await page.click('button[data-act="pull"]');
await page.waitForTimeout(400);
let t = await outText();
ok(!t.trim().startsWith('{'), '拉取: 结果面板顶部不是裸 JSON', t.slice(0, 24));
ok(await page.locator('#out .rs-head .rs-act').innerText() === '从掌阅拉取', '拉取: 标题是人话动作名');
ok((await page.locator('#out .rs-head .pill').innerText()).includes('已更新'), '拉取: 结论一句话 (已更新 N 本)');
ok((await page.locator('#out .rs-meta').innerText()).includes('秒'), '拉取: 显示耗时');
const chips = await page.$$eval('#out .rs-chips .chip', (cs) => cs.map((c) => c.textContent.replace(/\s+/g, ' ').trim()));
ok(chips.some((c) => c.startsWith('更新')), '拉取: 更新数进 chip', chips);
ok(chips.some((c) => c.startsWith('跳过')), '拉取: 跳过数进 chip');
ok(chips.some((c) => c.startsWith('新增')), '拉取: 划线新增进 chip');
ok(chips.some((c) => c.startsWith('云端有')), '拉取: 时长天数进 chip', chips);
const rows = await page.$$eval('#out .rs-list li', (ls) => ls.map((l) => l.innerText.replace(/\s+/g, ' ')));
ok(rows.some((r) => r.includes('示例长篇') && r.includes('云端 31.7%') && r.includes('第 183 章')), '拉取: 每本书一行, 进度与章节可读', rows[0]);
ok(rows.some((r) => r.includes('本地更新，不用拉')), '拉取: 跳过原因翻成人话');
ok(rows.some((r) => r.includes('6 天')), '拉取: 时长行可读');
ok(!/local-ahead|updated|"pct"/.test(t), '拉取: 面板上看不到字段名', t.slice(0, 60));
ok(await page.locator('#out details.rs-raw pre').count() >= 1, '拉取: 原始 JSON 收在折叠区');
ok(await page.locator('#sec-out').evaluate((d) => d.open), '执行结果自动展开');

// --- 2) 推送 --------------------------------------------------------------
await page.click('button[data-act="push"]');
await page.waitForTimeout(400);
t = await outText();
ok((await page.locator('#out .rs-head .pill').innerText()).includes('已推 1 本'), '推送: 结论一句话');
ok((await page.locator('#out .rs-chips').innerText()).includes('推送'), '推送: 计数 chip 在');
const prows = await page.$$eval('#out .rs-list li', (ls) => ls.map((l) => l.innerText.replace(/\s+/g, ' ')));
ok(prows.some((r) => r.includes('已写入 31.7%') && r.includes('第 159 章') && r.includes('共 588 章')), '推送: 写入行带章/总数', prows[0]);
ok(prows.some((r) => r.includes('进度一致，不用推')), '推送: noop 翻成人话');

// --- 3) 划线 / 时长 / 映射 -------------------------------------------------
await page.click('button[data-act="notes"]');
await page.waitForTimeout(350);
ok((await page.locator('#out .rs-head .pill').innerText()).includes('已同步'), '划线: 结论一句话');
ok((await outText()).includes('划线 3'), '划线: 计数可读');

await page.click('button[data-act="time"]');
await page.waitForTimeout(350);
ok((await page.locator('#out .rs-head .pill').innerText()).includes('已写入'), '时长: 结论一句话');
ok((await outText()).includes('25 分钟'), '时长: 分钟数可读');

await page.click('section[data-pane="sync"] button[data-act="links"]');
await page.waitForTimeout(350);
ok((await outText()).includes('新增映射'), '映射: 显示新增数');

// --- 4) 最近日志 ----------------------------------------------------------
await page.unroute('**/api/sync/run');
await page.click('#sec-log > summary');
await page.click('#log-refresh');
await page.waitForTimeout(700);
ok(await page.locator('#log .log-row').count() > 0, '日志: 渲染成行', await page.locator('#log .log-row').count());
const lv = await page.$$eval('#log .log-lv', (ns) => [...new Set(ns.map((n) => n.textContent.trim()))]);
ok(lv.every((x) => /信息|注意|失败|调试|原文/.test(x)), '日志: 等级是中文', lv);
const msgs = await page.$$eval('#log .log-msg', (ns) => ns.map((n) => n.textContent.trim()));
ok(msgs.every((m) => !/^(push|pull) (OK|FAIL)/.test(m)), '日志: 英文缩写已翻译', msgs.slice(0, 3));
ok(msgs.some((m) => /推送成功|拉取成功|推划线|拉取划线/.test(m)), '日志: 是完整句子', msgs.slice(0, 2));
const first = await page.locator('#log .log-row').first().innerText();
ok(/\d\d-\d\d \d\d:\d\d/.test(first), '日志: 时刻可读', first.slice(0, 30));

// --- 5) 真跑一次拉取 (真实 payload) ---------------------------------------
await page.click('button[data-act="pull"]');
await page.waitForSelector('#out .rs', { timeout: 120000 });
await page.waitForTimeout(400);
t = await outText();
ok(!t.trim().startsWith('{'), '真拉取: 结果不是裸 JSON', t.slice(0, 40));
ok((await page.locator('#out .rs-head').count()) === 1, '真拉取: 走同一套渲染');
ok(/秒|毫秒/.test(await page.locator('#out .rs-meta').innerText()), '真拉取: 有耗时');

// --- 6) 移动端 ------------------------------------------------------------
const m = await browser.newContext({ viewport: { width: 390, height: 844 } });
await m.addCookies([{ name: 'zy_webauth', value: val, domain: '127.0.0.1', path: '/' }]);
const mp = await m.newPage();
const merr = [];
mp.on('pageerror', (e) => merr.push(String(e)));
await mp.route('**/api/sync/run', (route) => route.fulfill({
  status: 200, contentType: 'application/json', body: JSON.stringify(STUB.pull)
}));
await mp.goto(BASE + '/', { waitUntil: 'networkidle' });
await mp.click('[data-pane-link="sync"]');
await mp.waitForTimeout(200);
await mp.click('button[data-act="pull"]');
await mp.waitForTimeout(500);
const over = await mp.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
ok(over <= 0, '移动端: 结果面板无横向溢出', over);
const rowsFit = await mp.evaluate(() => {
  const li = document.querySelector('#out .rs-list li');
  if (!li) return true;
  return li.getBoundingClientRect().right <= document.documentElement.clientWidth + 1;
});
ok(rowsFit, '移动端: 每本书一行不越界');
await mp.click('#sec-log > summary');
await mp.click('#log-refresh');
await mp.waitForTimeout(600);
ok((await mp.locator('#log .log-row').count()) > 0, '移动端: 日志正常渲染');
const logOver = await mp.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
ok(logOver <= 0, '移动端: 日志无横向溢出', logOver);

ok(errors.length === 0, '桌面 0 个 JS 报错', errors.slice(0, 2));
ok(merr.length === 0, '移动端 0 个 JS 报错', merr.slice(0, 2));

await browser.close();
const bad = res.filter((x) => !x).length;
console.log(bad ? `\n${bad} FAILED` : `\nall ${res.length} PASS`);
process.exit(bad ? 1 : 0);
