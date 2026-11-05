/* 阅读器阅读体验: 顶栏自动隐藏 / 左右页边翻页 / 点正文关浮层 / 书签笔记分列 */
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
const { execFileSync } = require('child_process');

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || 'a6f63af571f05bb4';
const token = execFileSync('python3', [new URL('./make_cookie.py', import.meta.url).pathname],
  { encoding: 'utf8' }).trim();

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', e => errors.push(String(e)));
page.on('response', r => { if (r.status() >= 400) errors.push('HTTP ' + r.status() + ' ' + r.url()); });
page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

await page.addInitScript(() => {
  localStorage.removeItem('recto.theme');
  localStorage.removeItem('recto.fs');
});
await page.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(1200);

const out = [];
const chk = (name, cond, extra = '') => out.push(`${cond ? 'ok  ' : 'FAIL'} ${name}${extra ? ' — ' + extra : ''}`);

/* 0. 站点顶栏不再叠两条 */
const rcTop = await page.locator('.rc-top').isVisible().catch(() => false);
chk('阅读页不显示站点顶栏', !rcTop);

/* 1. 顶栏自动隐藏 */
const barHidden = () => page.locator('#rv-bar').evaluate(el => el.classList.contains('is-hidden'));
const startY = await page.evaluate(() => window.scrollY);
chk('初始顶栏可见', !(await barHidden()));
await page.mouse.move(640, 500);
await page.mouse.wheel(0, 900);
await page.waitForTimeout(700);
const y1 = await page.evaluate(() => window.scrollY);
chk('下滑后顶栏收起', await barHidden(), `y=${y1}`);
await page.mouse.wheel(0, -300);
await page.waitForTimeout(600);
chk('上滑后顶栏唤出', !(await barHidden()));
await page.evaluate(() => window.scrollTo(0, 0));
await page.waitForTimeout(500);
chk('回到顶部顶栏可见', !(await barHidden()));

// 点正文中间切换
await page.mouse.click(640, 600);
await page.waitForTimeout(350);
const hiddenByTap = await barHidden();
await page.mouse.click(640, 600);
await page.waitForTimeout(350);
chk('点正文切换顶栏', hiddenByTap && !(await barHidden()), `tapHidden=${hiddenByTap}`);

/* 2. 左右页边翻页 */
const geo = await page.evaluate(() => {
  const l = document.querySelector('.rv-turn-l').getBoundingClientRect();
  const r = document.querySelector('.rv-turn-r').getBoundingClientRect();
  const p = document.querySelector('.rv-page').getBoundingClientRect();
  return { l: l.toJSON(), r: r.toJSON(), p: p.toJSON(), vh: innerHeight,
           cw: document.documentElement.clientWidth };
});
// 右侧热区不许伸到滚动条下面 (scrollbar-gutter 会独占 ~10px), 允许这点差
chk('左右热区存在且贴边', Math.round(geo.l.left) === 0 &&
    geo.r.right >= geo.cw - 12 && geo.r.right <= geo.cw,
    JSON.stringify([geo.l.width, geo.r.width, geo.r.right, geo.cw]));
chk('桌面热区不压正文', Math.round(geo.l.right) <= Math.round(geo.p.left), `zoneRight=${Math.round(geo.l.right)} textLeft=${Math.round(geo.p.left)}`);

await page.evaluate(() => window.scrollTo(0, 0));
await page.waitForTimeout(400);
await page.mouse.click(Math.round(geo.r.left + geo.r.width / 2), 500);
await page.waitForTimeout(1200);
const yNext = await page.evaluate(() => window.scrollY);
chk('点右侧下一页', yNext > 400, `y=${yNext} step≈${Math.round(geo.vh * 0.9)}`);
await page.mouse.click(Math.round(geo.l.left + geo.l.width / 2), 500);
await page.waitForTimeout(1200);
const yPrev = await page.evaluate(() => window.scrollY);
chk('点左侧上一页', yPrev < yNext, `y=${yPrev}`);

/* 3. 点正文关浮层 */
await page.evaluate(() => window.scrollTo(0, 0));
await page.waitForTimeout(300);
await page.click('[data-act="toc"]');
await page.waitForTimeout(300);
chk('目录抽屉可开', await page.locator('#rv-toc').evaluate(el => el.classList.contains('rv-on')));
await page.mouse.click(640, 620);
await page.waitForTimeout(400);
chk('点正文关目录', !(await page.locator('#rv-toc').evaluate(el => el.classList.contains('rv-on'))));

await page.click('[data-act="notes"]');
await page.waitForTimeout(300);
chk('笔记抽屉可开', await page.locator('#rv-notes').evaluate(el => el.classList.contains('rv-on')));
await page.mouse.click(640, 620);
await page.waitForTimeout(400);
chk('点正文关笔记抽屉', !(await page.locator('#rv-notes').evaluate(el => el.classList.contains('rv-on'))));

// 选区工具条: 选中一段 → 出现；点正文 → 收起
await page.evaluate(() => {
  const blk = document.querySelectorAll('#rv-content .rv-blk')[1];
  const w = document.createTreeWalker(blk, NodeFilter.SHOW_TEXT);
  const node = w.nextNode();
  const r = document.createRange();
  r.setStart(node, 0); r.setEnd(node, Math.min(10, node.nodeValue.length));
  const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
});
await page.waitForTimeout(600);
chk('选区工具条出现', !(await page.locator('#rv-sel').evaluate(el => el.hidden)));
await page.mouse.click(640, 300);
await page.waitForTimeout(400);
const selGone = await page.locator('#rv-sel').evaluate(el => el.hidden);
chk('点正文关选区工具条', selGone);

/* 4. 书签 / 笔记分开 */
const hasMarks = await page.locator('#rv-marks').count();
chk('书签抽屉独立存在', hasMarks === 1);
const marksItems = await page.locator('#rv-marks-list .rv-note-item').count();
const notesItems = await page.locator('#rv-notes-list .rv-note-item').count();
const marksHasNote = await page.locator('#rv-marks-list [data-delnote]').count();
const notesHasMark = await page.locator('#rv-notes-list [data-delmark]').count();
chk('两列表互不混装', marksHasNote === 0 && notesHasMark === 0, `marks=${marksItems} notes=${notesItems}`);

await page.click('[data-act="marks"]');
await page.waitForTimeout(300);
const addBtnVisible = await page.locator('#rv-marks .rv-add').isVisible();
chk('书签抽屉里有"加书签"', addBtnVisible);
const before = await page.locator('#rv-marks-list .rv-note-item').count();
await page.click('#rv-marks .rv-add');
await page.waitForTimeout(700);
const after = await page.locator('#rv-marks-list .rv-note-item').count();
chk('加书签生效', after === before + 1, `${before} -> ${after}`);
const drawerStillOpen = await page.locator('#rv-marks').evaluate(el => el.classList.contains('rv-on'));
chk('加完书签抽屉不关', drawerStillOpen);
// 清场: 删掉刚加的那条
if (after === before + 1) {
  await page.locator('#rv-marks-list .rv-note-item [data-delmark]').first().click();
  await page.waitForTimeout(700);
}
const cleaned = await page.locator('#rv-marks-list .rv-note-item').count();
chk('书签清理回原状', cleaned === before, `${cleaned}`);

/* 手机视口: 热区收窄, 仍可翻页 */
await ctx.close();
const ctx2 = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
await ctx2.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const p2 = await ctx2.newPage();
p2.on('pageerror', e => errors.push('m: ' + String(e)));
await p2.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
await p2.waitForTimeout(1200);
const mL = await p2.evaluate(() => document.querySelector('.rv-turn-l').getBoundingClientRect().width);
chk('手机热区窄 (<=60px)', mL <= 60, `${Math.round(mL)}px`);
const over = await p2.evaluate(() => document.documentElement.scrollWidth - innerWidth);
chk('手机无横向溢出', over <= 0, `${over}`);
await p2.tap('.rv-turn-r');
await p2.waitForTimeout(1100);
const mY = await p2.evaluate(() => window.scrollY);
chk('手机点右侧翻页', mY > 300, `y=${mY}`);

chk('无 JS/HTTP 报错', errors.length === 0, errors.slice(0, 3).join(' | '));

console.log(out.join('\n'));
const fails = out.filter(l => l.startsWith('FAIL')).length;
console.log(`\n${out.length - fails}/${out.length} PASS`);
await browser.close();
process.exit(fails ? 1 : 0);
