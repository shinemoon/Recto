// 设置页巡检: 顶栏圆点 = 设置入口 / 昵称即时生效 / 书库目录切换 / 口令校验 / 手机不溢出。
// 跑法: RECTO_BASE=http://127.0.0.1:5017 node tools/probe_settings.mjs
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
const { execFileSync } = require('child_process');

const BASE = process.env.RECTO_BASE || 'http://127.0.0.1:5015';
const NEWLIB = process.env.RECTO_TEST_LIB || '/tmp/recto-probe-lib';
const token = execFileSync('python3', [new URL('./make_cookie.py', import.meta.url).pathname],
  { encoding: 'utf8' }).trim();

const res = [];
const ok = (c, n, x) => { res.push(!!c); console.log((c ? 'PASS ' : 'FAIL ') + n + (x === undefined ? '' : ' :: ' + JSON.stringify(x))); };

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
const badResp = [];
page.on('response', (r) => { if (r.status() >= 400) badResp.push(r.status() + ' ' + r.url()); });

// --- 1. 顶栏入口 ---
await page.goto(BASE + '/#library', { waitUntil: 'networkidle' });
// 探针会真改 昵称 / 书库 两项设置 —— 先记原值, 收尾一律还原 (别把测试值留在生产上)
const orig = await page.evaluate(() => fetch('/api/settings').then((r) => r.json())
  .then((j) => ({ nickname: j.nickname || '', eff: j.library.dir || '',
                  overridden: !!j.library.overridden })));

const nav = await page.evaluate(() => {
  const a = document.querySelector('.rc-user .rc-avatar');
  const dot = document.querySelector('.rc-theme-dot');
  if (!a) return { has: false };
  const r = a.getBoundingClientRect();
  const cs = getComputedStyle(a);
  return {
    has: true, href: a.getAttribute('href'), text: a.textContent.trim(),
    title: a.getAttribute('title'),
    w: Math.round(r.width), h: Math.round(r.height), right: Math.round(r.right),
    fs: cs.fontSize, radius: cs.borderRadius,
    oldDot: !!dot,
  };
});
ok(nav.has, '顶栏圆点还在, 已变成头像', nav);
ok(nav.href === '/settings', '点它进设置页', nav.href);
ok(!nav.oldDot, '旧的 .rc-theme-dot 已移除');
ok(nav.w >= 22 && nav.h >= 22, '头像有实体尺寸', [nav.w, nav.h]);

// --- 2. 点进去 ---
await page.click('.rc-user .rc-avatar');
await page.waitForLoadState('networkidle');
const head = await page.evaluate(() => ({
  url: location.pathname,
  title: document.title,
  logo: (document.querySelector('.rc-brand') || {}).textContent || '',
  secs: [...document.querySelectorAll('details.sec > summary')].map((s) => s.textContent.trim()),
  navHome: !!document.querySelector('.rc-nav a[href="/#library"]'),
  pwInputs: document.querySelectorAll('input[type=password]').length,
  hasLook: !!document.querySelector('a[href="/appearance"]'),
}));
ok(head.url === '/settings', 'URL 是 /settings', head.url);
ok(head.title.includes('设置') && head.title.includes('云阅 Recto'), '标题带分区与品牌', head.title);
ok(head.secs.length >= 3, '三个分区: ' + head.secs.join(' / '), head.secs);
ok(head.pwInputs === 3, '口令三个输入框', head.pwInputs);
ok(head.hasLook, '外观设置从本页进');
ok(head.navHome, '导航与外壳一致');

// --- 3. 昵称 ---
await page.fill('#st-nick', '青豆');
await page.click('[data-st="nick"]');
await page.waitForTimeout(700);
const nick = await page.evaluate(() => ({
  top: (document.querySelector('.rc-user-name') || {}).textContent || '',
  avatar: (document.querySelector('.rc-user .rc-avatar') || {}).textContent || '',
  msg: (document.getElementById('st-nick-msg') || {}).textContent || '',
  api: null,
}));
ok(nick.top === '青豆', '顶栏名字即时更新', nick.top);
ok(nick.avatar === '青', '头像首字跟着变', nick.avatar);
ok(nick.msg.includes('已保存'), '有回执', nick.msg);
const apiNick = await page.evaluate(() => fetch('/api/settings').then((r) => r.json()).then((j) => j.nickname));
ok(apiNick === '青豆', 'API 里也存下了', apiNick);
await page.reload({ waitUntil: 'networkidle' });
const afterReload = await page.evaluate(() => ({
  top: (document.querySelector('.rc-user-name') || {}).textContent || '',
  input: (document.getElementById('st-nick') || {}).value || '',
}));
ok(afterReload.top === '青豆' && afterReload.input === '青豆', '刷新后仍是昵称', afterReload);

// --- 4. 书库目录 ---
const before = await page.evaluate(() => ({
  now: (document.getElementById('st-lib-now') || {}).textContent || '',
  src: (document.getElementById('st-lib-src') || {}).textContent || '',
  books: (document.getElementById('st-lib-books') || {}).textContent || '',
}));
await page.fill('#st-lib', NEWLIB);
await page.click('[data-st="lib"]');
await page.waitForTimeout(1200);
const after = await page.evaluate(() => ({
  now: (document.getElementById('st-lib-now') || {}).textContent || '',
  src: (document.getElementById('st-lib-src') || {}).textContent || '',
  books: (document.getElementById('st-lib-books') || {}).textContent || '',
  msg: (document.getElementById('st-lib-msg') || {}).textContent || '',
}));
ok(after.now === NEWLIB, '生效目录切过去了', [before.now, after.now]);
ok(after.src === '设置页', '来源标成设置页', after.src);
ok(after.msg.includes('已切换'), '有回执', after.msg);
ok(after.books === '0', '空目录 0 本', after.books);
const shelf = await page.evaluate(() => fetch('/read/api/books').then((r) => r.json()));
ok((shelf.books || []).length === 0, '书架跟着空了', (shelf.books || []).length);

// 书架页面也看不到旧书 (联动)
await page.goto(BASE + '/#library', { waitUntil: 'networkidle' });
const shelfCards = await page.$$eval('#shelf .bk', (els) => els.length).catch(() => -1);
ok(shelfCards === 0, '书架卡片 0 张', shelfCards);

// --- 5. 回到 config.toml ---
await page.goto(BASE + '/settings', { waitUntil: 'networkidle' });
await page.click('[data-st="lib-reset"]');
await page.waitForTimeout(1200);
const back = await page.evaluate(() => ({
  now: (document.getElementById('st-lib-now') || {}).textContent || '',
  src: (document.getElementById('st-lib-src') || {}).textContent || '',
  msg: (document.getElementById('st-lib-msg') || {}).textContent || '',
}));
ok(back.now === before.now, '回到 config.toml 的目录', [before.now, back.now]);
ok(back.src !== '设置页', '来源不再是设置页', back.src);
ok(back.msg.includes('config.toml'), '有回执', back.msg);

// --- 6. 口令: 错口令必须被拒 (不真改) ---
// 口令卡默认折叠 (它是低频操作), 先点开
await page.evaluate(() => {
  document.querySelectorAll('details.sec').forEach((d) => {
    if (d.querySelector('#st-old')) d.open = true;
  });
});
await page.fill('#st-old', 'definitely-wrong');
await page.fill('#st-new', 'abcdefg1');
await page.fill('#st-new2', 'abcdefg1');
await page.click('[data-st="pass"]');
await page.waitForTimeout(700);
const pwMsg = await page.evaluate(() => (document.getElementById('st-pass-msg') || {}).textContent || '');
ok(/口令/.test(pwMsg), '错口令有明确提示', pwMsg);

// --- 7. 手机 ---
await page.setViewportSize({ width: 390, height: 844 });
await page.goto(BASE + '/settings', { waitUntil: 'networkidle' });
const mob = await page.evaluate(() => {
  const av = document.querySelector('.rc-user .rc-avatar');
  const r = av && av.getBoundingClientRect();
  const cards = [...document.querySelectorAll('details.sec')].map((d) => d.getBoundingClientRect());
  return {
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    avatarIn: !!r && r.right <= window.innerWidth && r.width > 0,
    cardRight: cards.length ? Math.round(Math.max(...cards.map((c) => c.right))) : null,
    vw: window.innerWidth,
  };
});
ok(mob.overflow === 0, '390px 无横向溢出', mob.overflow);
ok(mob.avatarIn, '窄屏头像仍在视口内', mob);
ok(mob.cardRight <= mob.vw, '卡片不出界', mob);

// --- 8. 还原设置 (原样写回, 别把测试值留在生产上) ---
const restored = await page.evaluate((o) => fetch('/api/settings', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ nickname: o.nickname,
                         library_dir: o.overridden ? o.eff : '' }),
}).then((r) => r.json()).then((j) => ({ nickname: j.nickname || '', dir: j.library.dir })), orig);
ok(restored.nickname === orig.nickname, '昵称已还原', [orig.nickname, restored.nickname]);
ok(restored.dir === orig.eff, '书库目录已还原 (没覆盖就不留覆盖项)',
  [orig.eff, restored.dir]);

// 只容忍两类已知的: favicon 404, 以及我们故意用错口令打出的 400
const known = (x) => /favicon\.ico/.test(x) || /400 .*\/api\/settings\/password/.test(x);
const jsErr = errors.filter((e) => !/Failed to load resource/.test(e));
ok(jsErr.length === 0, '无 JS 报错', jsErr.slice(0, 5));
const other = badResp.filter((x) => !known(x));
ok(other.length === 0, '没有意外的 4xx/5xx', other.slice(0, 5));
await browser.close();
const bad = res.filter((x) => !x).length;
console.log(`\n${res.length - bad} passed / ${bad} failed`);
process.exit(bad ? 1 : 0);
