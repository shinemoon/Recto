// 书宠「这一趟的来处」+ 类型徽标 —— 真浏览器验收 (T3)
//
// 盯: 故事卡上有没有「会/斗/观/学/救/行」徽标、有没有来处块 (书/章/进度/分钟/母题/语料),
// 往期条目是否也带得出来, 手机上不溢出, 0 JS 报错。
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
await page.goto(BASE + '/pet/', { waitUntil: 'networkidle' });

// 故事卡在最前 (T3: 故事置顶), 等它渲出来
await page.waitForSelector('#adventure-latest .adv-card', { timeout: 15000 });
await page.waitForTimeout(400);

const data = await page.evaluate(() => {
  const card = document.querySelector('#adventure-latest .adv-card');
  const kind = card.querySelector('.adv-kind');
  const echo = card.querySelector('.adv-echo');
  const books = [...card.querySelectorAll('.adv-echo-books li')].map((li) => li.textContent.trim());
  const terms = [...card.querySelectorAll('.adv-echo-terms .adv-chip')].map((c) => c.textContent.trim());
  const hero = document.querySelector('.adv-hero');
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
    bodyLen: (card.querySelector('.adv-body') || {}).textContent?.trim().length || 0,
    cardFirst: hero ? hero.querySelector('.adv-card') === card : false,
    bodyHasDuelBlock: !!card.querySelector('.adv-duel'),
  };
});

ok(data.hasKind, '故事卡上有这一趟的类型徽标');
ok(/^[斗会观学救行]\s·/.test(data.kindText), '徽标是「字 · 类」形态', data.kindText);
ok(data.kindTitle.length > 0, '悬停能看为什么是这一类', data.kindTitle);
ok(data.hasEcho, '故事卡带「这一趟的来处」');
ok(data.echoLabel.includes('来处'), '来处块有标题', data.echoLabel);
ok(data.books.length > 0, '来处列出了读过的书', data.books);
ok(data.books.some((b) => /读到|推了|%|分钟/.test(b)), '来处写了章/进度/分钟', data.books);
ok(!data.terms.length || data.echoTermsLabel.length > 0, '语料词有窗口说明', data.echoTermsLabel);
ok(data.bodyLen > 200, '正文正常', data.bodyLen);
ok(data.cardFirst, '故事仍是第一张卡 (置顶没被挤下去)');

// 往期: 凡记录里带 kind / echo 的, 页面上都要显示出来 (老记录没有这两栏, 不补假)
const older = await page.evaluate(async () => {
  const list = document.querySelector('#adventure-list');
  const items = list ? [...list.querySelectorAll('details.adv-item')] : [];
  const r = await fetch('/pet/api/chronicle', { credentials: 'same-origin' });
  const v = await r.json();
  const rows = (v.adventures || []).slice(1);
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
await m.goto(BASE + '/pet/', { waitUntil: 'networkidle' });
await m.waitForSelector('#adventure-latest .adv-card', { timeout: 15000 });
await m.waitForTimeout(300);
const mob = await m.evaluate(() => {
  const echo = document.querySelector('#adventure-latest .adv-echo');
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

ok(errors.length === 0, '0 JS 报错', errors);
await browser.close();
console.log(`\n${'='.repeat(46)}\nPASS ${pass}  FAIL ${fail}`);
process.exit(fail ? 1 : 0);
