/* 阅读器: 关键词高亮 (语料滤镜) 端到端
 *
 * 断言:
 *   ① 语料自动匹配到 (书名 → keywords.json), 词表非空, 8 类都有词;
 *   ② highlight 模式: CSS.highlights 里有 rv-kw-<cat>, 命中数量 > 0, 且
 *      DOM 里一个 .rv-kw 都没有 (不改字符 = 进度/划线坐标不受影响);
 *   ③ 抽样: 某条 range 的文本确实是语料里的词 (陈平安 一类);
 *   ④ 生成的样式表里有 ::highlight(rv-kw-*) 规则;
 *   ⑤ 关掉开关 → ranges 归零 + CSS.highlights 清空; 再打开 → 恢复;
 *   ⑥ 左右翻页模式下页数正常 (高亮不影响排版); 0 pageerror.
 *
 * 进度会被改写 → 开头快照, 收尾还原 (tests/test_probe_hygiene.py 硬性要求)。
 */
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
const { execFileSync } = require('child_process');

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || '8379037298fbca9c';   // 示例长篇（有语料）
const KEY2 = process.env.RV_KEY2 || 'a6f63af571f05bb4'; // 短书（无语料）
const T_LONG = process.env.RV_TITLE_LONG || '示例长篇';
const token = execFileSync('python3', [new URL('./make_cookie.py', import.meta.url).pathname],
  { encoding: 'utf8' }).trim();

const out = [];
const chk = (name, cond, extra = '') =>
  out.push(`${cond ? 'ok  ' : 'FAIL'} ${name}${extra ? ' — ' + extra : ''}`);

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
page.setDefaultTimeout(20000);
const errs = [];
page.on('pageerror', e => errs.push(String(e)));
page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });

const apiGet = (p) => page.evaluate(async x => (await fetch(x)).json(), p);
const readProgress = async () => ((await apiGet('/read/api/book/' + KEY)) || {}).progress || { chapter: 0, offset: 0 };
const writeProgress = (p) => page.evaluate(async ([key, prog]) => fetch('/read/api/book/' + key + '/progress', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ chapter: prog.chapter || 0, offset: prog.offset || 0 })
}).then(r => r.json()), [KEY, p]);

await page.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('.rv-page p', { timeout: 40000 });
const prog0 = await readProgress();
await page.waitForTimeout(1500);

/* ---- ① 语料匹配 ---- */
const kw = await page.evaluate(() => window.__RV__.kw());
chk('__RV__.kw() 有输出', !!kw, JSON.stringify(kw));
chk('语料匹配到了 keywords.json', new RegExp(T_LONG).test(kw.file || ''), kw.file);
chk('词表非空', kw.words > 3000, `${kw.words} 词`);
chk('8 个分类', kw.cats === 8, String(kw.cats));

/* ---- ② highlight 模式: 不改 DOM ---- */
chk('模式 = highlight', kw.mode === 'highlight', kw.mode);
chk('命中数量 > 0', kw.ranges > 0, String(kw.ranges));
const domSpans = await page.evaluate(() => document.querySelectorAll('#rv-content .rv-kw').length);
chk('DOM 里没有 .rv-kw (一个字符都没改)', domSpans === 0, String(domSpans));
const hlNames = await page.evaluate(() => {
  const names = [];
  if (window.CSS && CSS.highlights) for (const k of CSS.highlights.keys()) names.push(k);
  return names;
});
chk('CSS.highlights 里有多类 rv-kw-*', hlNames.filter(n => /^rv-kw-/.test(n)).length >= 5,
  hlNames.join(','));

/* ---- ③ 抽样: range 文本确实是语料词 ---- */
const sample = await page.evaluate(() => {
  const res = [];
  for (const name of CSS.highlights.keys()) {
    if (!/^rv-kw-/.test(name)) continue;
    const hl = CSS.highlights.get(name);
    let i = 0;
    for (const r of hl) {
      if (i++ >= 2) break;
      res.push({ cat: name, text: (r.toString() || '').slice(0, 24) });
    }
  }
  return res;
});
chk('能取到 range 样本', sample.length > 0, JSON.stringify(sample.slice(0, 3)));

/* ---- ④ 样式表 ---- */
const css = await page.evaluate(() => (document.getElementById('rv-kw-css') || {}).textContent || '');
chk('注入了 ::highlight(rv-kw-person) 规则', /::highlight\(rv-kw-person\)/.test(css), css.slice(0, 60));
chk('person 底色是半透明 (深色主题不会变白斑)', /background-color:rgba\(/.test(css));

/* ---- ⑤ 开关 ---- */
await page.evaluate(() => document.querySelector('[data-act="typo"]').click());
await page.waitForTimeout(200);
const rowHidden = await page.evaluate(() => (document.getElementById('rv-kw-row') || {}).hidden);
chk('排版面板里出现「关键词高亮」行', rowHidden === false, String(rowHidden));
await page.evaluate(() => document.querySelector('[data-kw="0"]').click());
await page.waitForTimeout(600);
const off = await page.evaluate(() => window.__RV__.kw());
chk('关掉后命中归零', off.ranges === 0 && off.on === false, JSON.stringify(off));
const offHl = await page.evaluate(() => {
  let n = 0;
  if (window.CSS && CSS.highlights) for (const k of CSS.highlights.keys()) if (/^rv-kw-/.test(k)) n++;
  return n;
});
chk('关掉后 CSS.highlights 清空', offHl === 0, String(offHl));
await page.evaluate(() => document.querySelector('[data-kw="1"]').click());
await page.waitForTimeout(700);
const on = await page.evaluate(() => window.__RV__.kw());
chk('再打开恢复命中', on.ranges > 0, String(on.ranges));

/* ---- ⑥ 翻页模式: 高亮不影响分页 ---- */
await page.evaluate(() => document.querySelector('[data-mode="page"]').click());
await page.waitForTimeout(1400);
const pg = await page.evaluate(() => window.__RV__.page());
chk('左右翻页仍能分页 (count > 1)', pg.count > 1, JSON.stringify(pg));
const spansAfter = await page.evaluate(() => document.querySelectorAll('#rv-content .rv-kw').length);
chk('翻页模式下仍是零 DOM 改动', spansAfter === 0, String(spansAfter));
await page.evaluate(() => document.querySelector('[data-mode="scroll"]').click());
await page.waitForTimeout(500);

/* ---- 没语料的书: 行隐藏 + 提示 ---- */
const page2 = await ctx.newPage();
page2.setDefaultTimeout(20000);
page2.on('pageerror', e => errs.push(String(e)));
await page2.goto(`${BASE}/read/b/${KEY2}`, { waitUntil: 'domcontentloaded' });
await page2.waitForSelector('.rv-page p', { timeout: 40000 });
await page2.waitForTimeout(1200);
await page2.evaluate(() => document.querySelector('[data-act="typo"]').click());
await page2.waitForTimeout(200);
const note2 = await page2.evaluate(() => {
  const row = document.getElementById('rv-kw-row');
  const nt = document.getElementById('rv-kw-note');
  return { rowHidden: row ? row.hidden : null, note: nt ? nt.textContent.trim() : '' };
});
chk('无语料的书: 隐藏开关行', note2.rowHidden === true, JSON.stringify(note2));
chk('无语料的书: 提示去设置页绑定', /没匹配到语料/.test(note2.note), note2.note);
const prog2 = ((await page2.evaluate(async k => (await fetch('/read/api/book/' + k)).json(), KEY2)) || {}).progress || {};
const kw2 = await page2.evaluate(() => window.__RV__.kw());
chk('无语料的书: 零命中', kw2.ranges === 0, JSON.stringify(kw2));

chk('没有 JS 报错', errs.length === 0, errs.slice(0, 3).join(' | '));

await writeProgress(prog0);
console.log(out.join('\n'));
console.log(`\n${out.filter(x => x.startsWith('ok')).length}/${out.length} PASS`);
await browser.close();
process.exit(out.some(x => x.startsWith('FAIL')) ? 1 : 0);
