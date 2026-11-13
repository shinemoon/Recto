// 书架分区「本地书籍 · 云端同步」卡的上传入口巡检:
//   · 卡片在书库分区里, 表格有「文件」列
//   · 不在云书架的书有「上传到云端 / 加入云书架」按钮; 在云书架的那行没有上传按钮
//   · 「全部上传 (N)」的 N 与后端 uploadable 一致
//   · 卡片说明写清了「上传文件本体」与「接入同步」是两件事
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
await page.waitForSelector('#ident-body tr', { timeout: 15000 });

// 后端真实数据做对照
const data = await page.evaluate(() => fetch('/api/cloud/ident', { credentials: 'same-origin' })
  .then((r) => r.json()));
const rows = (data && data.items) || [];
const notInShelf = rows.filter((r) => !r.in_shelf).length;

ok(await page.locator('#sec-ident').count() === 1, '卡片「本地书籍 · 云端同步」在');
ok(await page.locator('#sec-ident summary', { hasText: '本地书籍' }).count() === 1,
  '卡头写着「本地书籍 · 云端同步」');
const heads = await page.locator('#sec-ident thead th').allInnerTexts();
ok(heads.some((h) => h.trim() === '文件'), '表格有「文件」列', heads);
ok(heads.some((h) => h.trim() === '同步'), '状态列拆成「文件」+「同步」两列');

const note = await page.locator('#ident-note').innerText();
ok(note.includes('上传到云端'), '说明里点了「上传到云端」', note.slice(0, 60));
ok(note.includes('接入同步'), '说明里点了「接入同步」(两件事说清)');
ok(!/不可用/.test(note), '不再显示「上传文件不可用」（能力已打通）');

const upBtns = page.locator('#ident-body button[data-up]');
const upCount = await upBtns.count();
ok(upCount === notInShelf, '可上传按钮数 = 后端 uploadable', { upCount, notInShelf });

const labels = await upBtns.allInnerTexts();
ok(labels.every((t) => /上传到云端|加入云书架/.test(t.trim())), '按钮文案是「上传到云端」或「加入云书架」', labels);

const allBtn = page.locator('#ident-upload-all');
const allHidden = await allBtn.isHidden().catch(() => true);
if (notInShelf > 0) {
  ok(!allHidden, '有可上传的书时显示「全部上传」');
  const txt = (await allBtn.innerText()).trim();
  ok(txt === `全部上传 (${notInShelf})`, '「全部上传」带正确数量', txt);
} else {
  ok(allHidden, '没有可上传的书时隐藏「全部上传」');
}

// 已在云书架的那一行不该有上传按钮
const shelfRow = page.locator('#ident-body tr', { hasText: '在云书架' });
if (await shelfRow.count()) {
  ok((await shelfRow.first().locator('button[data-up]').count()) === 0,
    '已在云书架的行没有上传按钮');
}

// 手机视口: 不出横向滚动条
await page.setViewportSize({ width: 390, height: 844 });
await page.waitForTimeout(350);
const overflow = await page.evaluate(() => ({
  doc: document.documentElement.scrollWidth, win: window.innerWidth,
  wrap: (() => { const w = document.querySelector('#sec-ident .table-wrap');
    return w ? { sw: w.scrollWidth, cw: w.clientWidth } : null; })()
}));
ok(overflow.doc <= overflow.win + 1, '390px 页面不横向溢出', overflow);
if (overflow.wrap) {
  ok(overflow.wrap.sw >= overflow.wrap.cw, '表格在 .table-wrap 里自己横滚（不撑破页面）');
}

ok(errors.length === 0, '0 个 JS 报错', errors.slice(0, 3));

await browser.close();
const bad = res.filter((x) => !x).length;
console.log(`\n${res.length - bad}/${res.length} passed`);
process.exit(bad ? 1 : 0);
