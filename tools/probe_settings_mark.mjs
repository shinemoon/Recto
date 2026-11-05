/* 设置页「阅读高亮」卡: 结构 + 保存 + 绑定 + 恢复默认
 *
 * 收尾会把配置恢复成默认 (reading_mark.json 删掉), 不留测试痕迹。
 */
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
const { execFileSync } = require('child_process');

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY2 = 'a6f63af571f05bb4';    // 短书 (没有语料 → 用来测绑定)
// 书名占位: 真名用 env 传 (RV_TITLE_LONG / RV_TITLE_SHORT)
const T_LONG = process.env.RV_TITLE_LONG || '示例长篇';
const T_SHORT = process.env.RV_TITLE_SHORT || '示例短篇';
const RE_LONG = new RegExp(T_LONG), RE_SHORT = new RegExp(T_SHORT);
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
// 卡片默认折着, 先展开再量 (visible 才可交互)
await page.waitForSelector('#mark', { state: 'attached', timeout: 20000 });
await page.evaluate(() => { const d = document.getElementById('mark'); if (d) d.open = true; });
await page.waitForSelector('#mk-styles .mk-style', { state: 'attached', timeout: 20000 });
await page.waitForTimeout(600);

chk('卡片存在', await page.locator('#mark').count() === 1);
chk('状态 pill 已填', /已启用|已关闭/.test(await page.locator('#mk-pill').innerText()),
  await page.locator('#mk-pill').innerText());
chk('8 类配色行', await page.locator('#mk-styles .mk-style').count() === 8,
  String(await page.locator('#mk-styles .mk-style').count()));
chk('书库两本都列出来', await page.locator('#mk-books .mk-row').count() === 2);
chk('语料份数正确', /[1-9] 份/.test(await page.locator('#mk-corpora').innerText()),
  await page.locator('#mk-corpora').innerText());

const rows = await page.evaluate(() => [...document.querySelectorAll('#mk-books .mk-row')].map(r => ({
  title: r.querySelector('.nm').textContent.trim().slice(0, 6),
  st: r.querySelector('.st').textContent.trim(),
  sel: r.querySelector('[data-mk-bind]').value
})));
chk('长书显示书名匹配', rows.some(r => RE_LONG.test(r.title) && /书名匹配/.test(r.st)), JSON.stringify(rows));
chk('短书显示未匹配', rows.some(r => RE_SHORT.test(r.title) && /未匹配/.test(r.st)), JSON.stringify(rows));

/* ---- 保存配色 ---- */
await page.evaluate(() => {
  // 颜色现在只能用色板设置, 探针走同一份公开钩子
  window.__MK__.set('person', 'bg', 'rgba(255, 0, 0, .5)');
  document.querySelector('#mk-styles .mk-style[data-cat="person"] [data-mk="underline"]').checked = true;
});
await page.click('[data-st="mark"]');
await page.waitForTimeout(900);
const msg = await page.locator('#mk-msg').innerText();
chk('保存有回执', /已保存/.test(msg), msg);
const saved = await page.evaluate(async () => (await fetch('/api/mark')).json());
chk('配色落盘', saved.styles.person.bg === 'rgba(255, 0, 0, .5)' && saved.styles.person.underline === true,
  JSON.stringify(saved.styles.person));
chk('色板不写死输入框', await page.locator('#mk-styles input[type=text]').count() === 0);

/* ---- 绑定一份语料给短书 ---- */
const file = saved.corpora[0].file;
await page.evaluate(({ key, file }) => {
  const row = document.querySelector(`.mk-row[data-book="${key}"]`);
  const sel = row.querySelector('[data-mk-bind]');
  sel.value = file;
  sel.dispatchEvent(new Event('change', { bubbles: true }));
}, { key: KEY2, file });
await page.waitForTimeout(1200);
const bound = await page.evaluate(async () => (await fetch('/api/mark')).json());
const row2 = bound.books.find(b => b.key === 'a6f63af571f05bb4');
chk('绑定已生效', row2.how === 'bind' && row2.file === file, JSON.stringify(row2));
const uiRow = await page.evaluate(k => document.querySelector(`.mk-row[data-book="${k}"] .st`).textContent.trim(), KEY2);
chk('界面同步显示已绑定', /已绑定/.test(uiRow), uiRow);

/* ---- 恢复默认 ---- */
await page.click('[data-st="mark-reset"]');
await page.waitForTimeout(900);
const reset = await page.evaluate(async () => (await fetch('/api/mark')).json());
chk('恢复默认: 绑定清空', Object.keys(reset.conf.bind || {}).length === 0, JSON.stringify(reset.conf.bind));
chk('恢复默认: 配色回到半透明', /rgba\(255, 196, 64/.test(reset.styles.person.bg), reset.styles.person.bg);

chk('没有 JS 报错', errs.length === 0, errs.slice(0, 3).join(' | '));

console.log(out.join('\n'));
console.log(`\n${out.filter(x => x.startsWith('ok')).length}/${out.length} PASS`);
await browser.close();
process.exit(out.some(x => x.startsWith('FAIL')) ? 1 : 0);
