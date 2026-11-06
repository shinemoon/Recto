// 书架删除键 —— 真浏览器探针。
//
// 跑法 (dev 实例):
//   RECTO_BASE=http://127.0.0.1:5025 RECTO_PASS=dev-pass \
//   RECTO_USER=root RV_DELETE_TITLE=删除测试书 node tools/probe_shelf_delete.mjs
//
// 会**真删一本书**（只删标题 == RV_DELETE_TITLE 的那本），所以默认标题叫
// 「删除测试书」—— 打生产也不会碰到真书 (找不到就只验界面, 不点确认)。
import { chromium, REPO, cookie } from './_env.mjs';

const BASE = (process.env.RECTO_BASE || 'http://127.0.0.1:5015').replace(/\/$/, '');
const USER = process.env.RECTO_USER || 'root';
const PASS = process.env.RECTO_PASS || '';
const TITLE = process.env.RV_DELETE_TITLE || '删除测试书';

const FAILS = [];
const ok = (name, cond, extra = '') => {
  if (cond) console.log('PASS', name);
  else { FAILS.push(name); console.log('FAIL', name, extra); }
};

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });

// 有共享会话就直接用, 没有就走登录表单
const jar = process.env.RECTO_NO_COOKIE ? '' : (() => { try { return cookie(USER); } catch { return ''; } })();
if (jar) {
  await ctx.addCookies([{ name: 'zy_webauth', value: jar, domain: new URL(BASE).hostname, path: '/' }]);
}

const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
page.on('console', (m) => {
  // 云书列表要真凭据 (dev 实例没有) —— 它的 502 跟书架无关, 不计入
  const t = m.text();
  if (m.type() === 'error' && !/502|Failed to load resource/.test(t)) errors.push(t);
});
page.on('response', (r) => {
  if (r.status() >= 400 && !/\/api\/cloud\/books/.test(r.url())) {
    errors.push(r.status() + ' ' + r.url());
  }
});

await page.goto(BASE + '/#library', { waitUntil: 'domcontentloaded' });
if (page.url().includes('/login')) {
  await page.fill('input[name=username]', USER);
  await page.fill('input[name=password]', PASS);
  await Promise.all([page.waitForNavigation({ waitUntil: 'domcontentloaded' }), page.click('button[type=submit], form button')]);
  await page.goto(BASE + '/#library', { waitUntil: 'domcontentloaded' });
}
await page.waitForSelector('.bk-cell .bk');

const cards = await page.$$eval('.bk-cell', (els) => els.map((el) => {
  const del = el.querySelector('.bk-del');
  const a = el.querySelector('a.bk');
  const r = el.getBoundingClientRect();
  const d = del ? del.getBoundingClientRect() : null;
  return {
    title: (el.querySelector('.bk-title') || {}).textContent || '',
    hasDel: !!del,
    delInAnchor: !!(del && a && a.contains(del)),
    anchorCovers: !!a,
    aria: del ? del.getAttribute('aria-label') : '',
    key: del ? del.getAttribute('data-del') : '',
    cardRight: r.right, cardTop: r.top,
    delRight: d ? d.right : 0, delTop: d ? d.top : 0,
    w: d ? d.width : 0, h: d ? d.height : 0,
    opacity: del ? Number(getComputedStyle(del).opacity) : -1,
    pointer: del ? getComputedStyle(del).pointerEvents : '',
  };
}));

ok('每张卡都有删除键', cards.length > 0 && cards.every((c) => c.hasDel), JSON.stringify(cards.slice(0, 2)));
ok('删除键不在 <a> 里 (交互元素不嵌套)', cards.every((c) => !c.delInAnchor));
ok('删除键带 aria-label', cards.every((c) => (c.aria || '').includes('删除')));
ok('删除键带书 key', cards.every((c) => /^[0-9a-f]{16}$/.test(c.key || '')));
ok('删除键贴卡片右上角', cards.every((c) => c.cardRight - c.delRight <= 14 && c.cardTop >= 0 && c.delTop - c.cardTop <= 14),
  JSON.stringify(cards.map((c) => [c.cardRight - c.delRight, c.delTop - c.cardTop]).slice(0, 3)));
ok('删除键 30px 圆钮', cards.every((c) => Math.abs(c.w - 30) < 1.5 && Math.abs(c.h - 30) < 1.5),
  JSON.stringify(cards.map((c) => [c.w, c.h]).slice(0, 2)));
ok('桌面默认不露出 (悬停才显)', cards.every((c) => c.opacity === 0 && c.pointer === 'none'));

const first = await page.$('.bk-cell');
await first.hover();
await page.waitForTimeout(280);
const hovered = await page.$eval('.bk-cell .bk-del', (el) => ({
  opacity: Number(getComputedStyle(el).opacity),
  pointer: getComputedStyle(el).pointerEvents,
}));
ok('悬停后变可见可点', hovered.opacity === 1 && hovered.pointer === 'auto', JSON.stringify(hovered));

const target = cards.find((c) => c.title.trim() === TITLE);
if (!target) {
  console.log('SKIP 没找到《' + TITLE + '》，只验界面不真删');
} else {
  const del = await page.$(`.bk-del[data-del="${target.key}"]`);
  const before = await page.$$eval('.bk-cell', (e) => e.length);
  // 桌面删除键悬停才露 (pointer-events: none) —— 先悬停那张卡, 跟真人一样
  await (await del.evaluateHandle((el) => el.closest('.bk-cell'))).hover();
  await page.waitForTimeout(260);

  // 1) 取消: 什么都不该发生
  let asked = '';
  page.once('dialog', async (d) => { asked = d.message(); await d.dismiss(); });
  await del.click();
  await page.waitForTimeout(400);
  ok('先弹二次确认', asked.includes('从书架删除') && asked.includes(TITLE), asked);
  ok('确认框提到直接删除', asked.includes('直接删除'), asked);
  const afterCancel = await page.$$eval('.bk-cell', (e) => e.length);
  ok('取消后卡片还在', afterCancel === before, `${before}->${afterCancel}`);

  // 2) 确认: 卡片消失 + 回执
  page.once('dialog', async (d) => await d.accept());
  await del.click();
  await page.waitForFunction((n) => document.querySelectorAll('.bk-cell').length === n - 1, before, { timeout: 8000 });
  const after = await page.$$eval('.bk-cell', (e) => e.length);
  ok('确认后卡片少一张', after === before - 1, `${before}->${after}`);
  const gone = await page.$$eval('.bk-cell .bk-title', (els) => els.map((e) => e.textContent.trim()));
  ok('删的正是那一本', !gone.includes(TITLE), gone.join(' | '));
  const toastText = await page.$eval('.rc-toast', (el) => el.textContent).catch(() => '');
  ok('有回执条', toastText.includes('已删除'), toastText || '(无)');

  const api = await page.evaluate(async () => (await fetch('/read/api/books')).json());
  ok('接口里也没了', !api.books.some((b) => (b.title || '').trim() === TITLE),
    api.books.map((b) => b.title).join(' | '));
  ok('别的书还在', api.books.length >= 1);
}

// 3) 手机: 触屏没有 hover, 删除键必须常显 (hasTouch 才让 (hover: none) 生效)
const mctx = await browser.newContext({
  viewport: { width: 390, height: 780 }, hasTouch: true, isMobile: true,
});
if (jar) {
  await mctx.addCookies([{ name: 'zy_webauth', value: jar, domain: new URL(BASE).hostname, path: '/' }]);
}
const m = await mctx.newPage();
await m.goto(BASE + '/#library', { waitUntil: 'domcontentloaded' });
if (m.url().includes('/login')) {
  await m.fill('input[name=username]', USER);
  await m.fill('input[name=password]', PASS);
  await Promise.all([m.waitForNavigation({ waitUntil: 'domcontentloaded' }), m.click('button[type=submit], form button')]);
  await m.goto(BASE + '/#library', { waitUntil: 'domcontentloaded' });
}
await m.waitForSelector('.bk-cell .bk-del');
const mobile = await m.$eval('.bk-cell .bk-del', (el) => {
  const st = getComputedStyle(el);
  const r = el.getBoundingClientRect();
  return { opacity: Number(st.opacity), pointer: st.pointerEvents, w: r.width, h: r.height,
    hoverNone: matchMedia('(hover: none)').matches };
});
ok('手机上报 hover:none', mobile.hoverNone === true);
ok('手机上常显', mobile.opacity === 1 && mobile.pointer === 'auto', JSON.stringify(mobile));
const overflow = await m.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
ok('手机无横向溢出', overflow <= 0, String(overflow));

ok('0 JS 报错', errors.length === 0, errors.slice(0, 3).join(' / '));
await browser.close();
console.log(FAILS.length ? `\n${FAILS.length} FAIL` : '\nALL PASS');
process.exit(FAILS.length ? 1 : 0);
