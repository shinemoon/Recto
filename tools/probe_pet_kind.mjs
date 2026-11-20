// 书宠「这一趟的来处」+ 类型徽标 —— 真浏览器验收 (T3)
//
// 盯: 档案页 (按回查阅) 上有没有「会/斗/观/学/救/行」徽标、有没有来处块
// (书/章/进度/分钟/母题/语料), 手机上不溢出, 0 JS 报错。
//
// **注意**: 前台 (/pet/) 现在是**一条连续的路** (不分章节) —— 按回的东西
// 都在档案页 (/pet/story/) 上。这个探针量的是档案页。
//
// 跑: RECTO_BASE=http://127.0.0.1:5015 node tools/probe_pet_kind.mjs
import { chromium } from 'playwright';
import { cookie } from './_env.mjs';

const BASE = process.env.RECTO_BASE || 'http://127.0.0.1:5015';
const errors = [];
let pass = 0, fail = 0;
function ok(cond, name, extra) {
  if (cond) { pass++; console.log('PASS', name); }
  else { fail++; console.log('FAIL', name, extra === undefined ? '' : JSON.stringify(extra)); }
}

const COOKIE = { name: 'zy_webauth', value: cookie(), domain: '127.0.0.1', path: '/' };
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1400, height: 1000 } });
await ctx.addCookies([COOKIE]);
const page = await ctx.newPage();
page.on('pageerror', (e) => errors.push(String(e)));
await page.goto(BASE + '/pet/story/', { waitUntil: 'networkidle' });

// 档案页: 按回摆的条目
await page.waitForSelector('#archive-list .arch-item', { timeout: 15000 });
await page.waitForTimeout(400);

// 展开全部 (徽标 / 来处在 summary 与 body 里)
await page.evaluate(() => {
  document.querySelectorAll('#archive-list .arch-item').forEach((d) => { d.open = true; });
});
await page.waitForTimeout(300);

const data = await page.evaluate(() => {
  const items = document.querySelectorAll('#archive-list .arch-item');
  const first = items[items.length - 1];
  const kind = first.querySelector('.adv-kind');
  const echo = first.querySelector('.adv-echo');
  const books = [...first.querySelectorAll('.adv-echo-books li')].map((li) => li.textContent.trim());
  const terms = [...first.querySelectorAll('.adv-echo-terms .adv-chip')].map((c) => c.textContent.trim());
  return {
    hasKind: !!kind,
    kindText: kind ? kind.textContent.trim() : '',
    kindTitle: kind ? (kind.getAttribute('title') || '') : '',
    hasEcho: !!echo,
    echoLabel: echo ? echo.querySelector('.adv-echo-h').textContent.trim() : '',
    books,
    terms,
    echoTermsLabel: echo && echo.querySelector('.adv-echo-l')
      ? echo.querySelector('.adv-echo-l').textContent.trim() : '',
    bodyLen: (first.querySelector('.arch-text') || {}).textContent?.trim().length || 0,
    bodyHasDuelBlock: !!first.querySelector('.adv-duel'),
  };
});

ok(data.hasKind, '故事条目上有这一趟的类型徽标');
ok(/^[斗会观学救行]\s·/.test(data.kindText), '徽标是「字 · 类」形态', data.kindText);
ok(data.kindTitle.length > 0, '悬停能看为什么是这一类', data.kindTitle);
ok(data.hasEcho, '故事条目带「这一趟的来处」');
ok(data.echoLabel.includes('来处'), '来处块有标题', data.echoLabel);
ok(data.books.length > 0, '来处列出了读过的书', data.books);
ok(data.books.some((b) => /读到|推了|%|分钟/.test(b)), '来处写了章/进度/分钟', data.books);
ok(!data.terms.length || data.echoTermsLabel.length > 0, '语料词有窗口说明', data.echoTermsLabel);
ok(data.bodyLen > 200, '正文正常', data.bodyLen);

// 往期: 凡记录里带 kind / echo 的, 页面上都要显示出来 (老记录没有这两栏, 不补假)
const older = await page.evaluate(async () => {
  const items = Array.from(document.querySelectorAll('#archive-list .arch-item'));
  const r = await fetch('/pet/api/story/archive', { credentials: 'same-origin' });
  const v = await r.json();
  const rows = v.archive || [];
  const wantKind = rows.filter((a) => (a.growth || {}).kind).length;
  const wantEcho = rows.filter((a) => ((a.growth || {}).echo || {}).books).length;
  return {
    n: items.length,
    withKind: items.filter((d) => d.querySelector('summary .adv-kind')).length,
    withEcho: items.filter((d) => d.querySelector('.adv-echo')).length,
    wantKind, wantEcho,
  };
});
ok(older !== null && older.n > 0, '有往期游历', older);
ok(older && older.withKind === older.wantKind,
   '往期标了这一回是什么事 (与记录一致)', older);
ok(older && older.withEcho === older.wantEcho,
   '往期带得出这一趟的来处 (与记录一致)', older);

// 窄屏: 来处块不许把页面撑出横向滚动
const ctx2 = await browser.newContext({ viewport: { width: 390, height: 780 }, isMobile: true, hasTouch: true });
await ctx2.addCookies([COOKIE]);
const m = await ctx2.newPage();
m.on('pageerror', (e) => errors.push(String(e)));
await m.goto(BASE + '/pet/story/', { waitUntil: 'networkidle' });
await m.waitForSelector('#archive-list .arch-item', { timeout: 15000 });
await m.waitForTimeout(300);
const mob = await m.evaluate(() => {
  const echo = document.querySelector('#archive-list .adv-echo');
  return {
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    hasEcho: !!echo,
    echoW: echo ? Math.round(echo.getBoundingClientRect().width) : 0,
    viewW: window.innerWidth,
  };
});
ok(mob.hasEcho, '手机上也有来处块');
ok(mob.overflow <= 1, '手机没有横向溢出', mob);
ok(mob.echoW > 0 && mob.echoW <= mob.viewW, '来处块宽度在视口内', mob);

// 前台: 那条连续的路不该有「第N回」的标记
const p2 = await ctx.newPage();
p2.on('pageerror', (e) => errors.push(String(e)));
await p2.goto(BASE + '/pet/', { waitUntil: 'networkidle' });
await p2.waitForTimeout(900);
const front = await p2.evaluate(() => ({
  advNo: document.querySelectorAll('#adv-read .adv-no').length,
  title: document.querySelector('#adv-read-title')?.textContent?.trim() || '',
  archive: !!document.querySelector('#btn-adv-archive'),
}));
ok(front.advNo === 0, '前台不分章节 (没有「第N回」的标记)');
ok(!/第\s*\d+\s*回/.test(front.title), `前台标题不带章节号 (${front.title})`);
ok(front.archive, '前台有「按回查阅」的入口');

ok(errors.length === 0, '0 JS 报错', errors);
await browser.close();
console.log(`\n${'='.repeat(46)}\nPASS ${pass}  FAIL ${fail}`);
process.exit(fail ? 1 : 0);