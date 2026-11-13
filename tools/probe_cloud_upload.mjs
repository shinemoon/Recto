// 书架分区「上传到掌阅云」卡巡检 (2026-11-13 起: 只有一句说明 + 一个按钮, 不再列清单):
//   · 卡片在书库分区里, 卡头写「上传到掌阅云」
//   · 卡里没有表格 (清单已删)
//   · 说明写清「传文件本体」与「以文件内容认书」
//   · 按钮文案带待传本数, 与后端 /api/cloud/ident 的 pending 一致
//   · 全部都在云书架时按钮置灰
//   · 手机 390px 不横向溢出
//
// 只读: 不点上传 (真的会往掌阅云端写), 上传动作由 tests/test_cloud_upload.py 离线验。
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RECTO_BASE || 'http://127.0.0.1:5015';
const token = cookie();
const res = [];
const ok = (c, n, x) => {
  res.push(!!c);
  console.log((c ? 'PASS ' : 'FAIL ') + n + (x === undefined ? '' : ' :: ' + JSON.stringify(x)));
};

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

await page.goto(BASE + '/#library', { waitUntil: 'networkidle' });
await page.waitForSelector('#sec-ident', { state: 'attached', timeout: 15000 });
await page.evaluate(() => { const d = document.getElementById('sec-ident'); if (d) d.open = true; });
await page.waitForSelector('#ident-upload-all', { timeout: 15000 });

const data = await page.evaluate(() => fetch('/api/cloud/ident', { credentials: 'same-origin' })
  .then((r) => r.json()));
const rows = (data && data.items) || [];
const pending = rows.filter((r) => !r.in_shelf || !r.attached).length;
const inShelf = rows.filter((r) => r.in_shelf).length;

ok(await page.locator('#sec-ident').count() === 1, '卡片存在');
ok(await page.locator('#sec-ident summary', { hasText: '上传到掌阅云' }).count() === 1,
  '卡头写着「上传到掌阅云」');
ok((await page.locator('#sec-ident table').count()) === 0, '卡里没有清单表格');
ok((await page.locator('#sec-ident button').count()) === 1, '卡里只有一个按钮');

const note = await page.locator('#ident-note').innerText();
ok(note.includes('传到掌阅云书架'), '说明点了「传到掌阅云书架」', note.slice(0, 60));
ok(note.includes('文件内容'), '说明点了「以文件内容认书」');
ok(!/不可用/.test(note), '不再显示「上传文件不可用」');

const pill = (await page.locator('#ident-pill').innerText()).trim();
ok(pill === `${inShelf} / ${rows.length} 在云书架`, '徽标 = 在云书架 / 总数', pill);

const btn = page.locator('#ident-upload-all');
const txt = (await btn.innerText()).trim();
const disabled = await btn.isDisabled();
if (pending > 0) {
  ok(txt === `上传到掌阅云 (${pending} 本)`, '按钮文案带待传本数', { txt, pending });
  ok(!disabled, '有待传的书时可点');
} else {
  ok(txt === '都已上传', '没有待传的书时文案是「都已上传」', txt);
  ok(disabled, '没有待传的书时按钮置灰');
}

// 手机视口: 不出横向滚动条
await page.setViewportSize({ width: 390, height: 844 });
await page.waitForTimeout(350);
const overflow = await page.evaluate(() => ({
  doc: document.documentElement.scrollWidth, win: window.innerWidth,
}));
ok(overflow.doc <= overflow.win + 1, '390px 页面不横向溢出', overflow);

ok(errors.length === 0, '0 个 JS 报错', errors.slice(0, 3));

await browser.close();
const bad = res.filter((x) => !x).length;
console.log(`\n${res.length - bad}/${res.length} passed`);
process.exit(bad ? 1 : 0);
