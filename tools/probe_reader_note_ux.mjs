/* 阅读器三件事的验收 (真浏览器):
 *   1. 笔记抽屉「从新到旧」
 *   2. 点划线 → 就地看/删 (卡片: 划线文 / 批注 / 编辑 / 删除)
 *   3. 选区工具条多了「查询」(开放词典) 与「搜索」(网页)
 *
 * 自己造的划线**在探针里删掉** (顺带验证「删除」) —— 否则 tick_notes 会把它推到
 * 真实云端账号。收尾还原进度。
 */
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || 'da7e470fa0c2c4b8';
const HOST = new URL(BASE).hostname;
const token = cookie();
const WIDE = Number(process.env.RV_W || 1400);

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: WIDE, height: 900 } });
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: HOST, path: '/' }]);
const page = await ctx.newPage();
page.setDefaultTimeout(20000);
const errs = [];
page.on('pageerror', e => errs.push(String(e)));

await page.goto(BASE + '/read/b/' + KEY, { waitUntil: 'domcontentloaded' });

const checks = [];
const ok = (name, cond, extra) => checks.push({ name, ok: !!cond, extra: extra == null ? '' : String(extra) });

async function readProgress() {
  return page.evaluate(async () => {
    const k = location.pathname.split('/').pop();
    const j = await (await fetch('/read/api/book/' + k)).json();
    return (j && j.progress) || {};
  });
}
async function writeProgress(p) {
  return page.evaluate(async (prog) => {
    const k = location.pathname.split('/').pop();
    await fetch('/read/api/book/' + k + '/progress', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chapter: prog.chapter || 0, offset: prog.offset || 0 })
    });
  }, p);
}
async function notesOf() {
  return page.evaluate(async () => {
    const k = location.pathname.split('/').pop();
    const j = await (await fetch('/read/api/book/' + k)).json();
    return j.notes || [];
  });
}
/** 选中一段正文; 返回选区文字与屏幕上一点 (点它就该命中这条划线)。 */
async function pickRange(target, chars) {
  return target.evaluate((n) => {
    const blks = [...document.querySelectorAll('.rv-page .rv-blk')]
      .filter(b => (b.textContent || '').trim().length > 80 && !b.querySelector('.rv-fn'));
    // 优先挑已经在视口里的块, 否则滚到屏幕中间再量
    let b = blks.find(x => {
      const r = x.getBoundingClientRect();
      return r.top > 140 && r.bottom < window.innerHeight - 120;
    });
    if (!b) { b = blks[0]; b.scrollIntoView({ block: 'center' }); }
    const w = document.createTreeWalker(b, NodeFilter.SHOW_TEXT, null);
    let node = null, t = null;
    while ((t = w.nextNode())) { if ((t.nodeValue || '').trim().length > 30) { node = t; break; } }
    if (!node) return { text: '', x: 0, y: 0 };
    const r = document.createRange();
    r.setStart(node, 5); r.setEnd(node, 5 + n);
    const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
    document.dispatchEvent(new Event('selectionchange'));
    const box = r.getBoundingClientRect();
    return {
      text: String(s.toString()),
      x: Math.round(box.left + 6),
      y: Math.round(box.top + Math.max(6, box.height / 2))
    };
  }, chars);
}
async function toastNow(target) {
  return target.evaluate(() => {
    const t = document.getElementById('rv-toast');
    return t && !t.hidden ? (t.textContent || '').trim() : '';
  });
}
async function waitToast(target, re, tries) {
  for (let i = 0; i < (tries || 40); i++) {
    await target.waitForTimeout(120);
    const s = await toastNow(target);
    if (s && (!re || re.test(s))) return s;
  }
  return '';
}

const progBefore = await readProgress();
await page.waitForSelector('.rv-page .rv-blk', { timeout: 20000 });
await page.waitForTimeout(900);

// ---------------------------------------------------------------- 1. 列表顺序
const allNotes = await notesOf();
const drawn = await page.evaluate(async () => {
  const k = location.pathname.split('/').pop();
  const j = await (await fetch('/read/api/book/' + k)).json();
  const rows = (j.notes || []).map(x => ({ id: x.id, at: String(x.created_at || '') }));
  const want = rows.slice().sort((a, b) => (a.at === b.at ? b.id - a.id : (a.at < b.at ? 1 : -1)));
  document.querySelector('[data-act="notes"]').click();
  await new Promise(r => setTimeout(r, 300));
  const dom = [...document.querySelectorAll('#rv-notes-list .rv-note-item')]
    .map(el => String((el.querySelector('.q') || {}).textContent || '').trim().slice(0, 10));
  return {
    want: want.length, dom,
    hasTime: /·\s*\d{4}-\d{2}-\d{2}/.test(document.querySelector('#rv-notes-list')?.textContent || '')
  };
});
ok('笔记抽屉有条目', allNotes.length > 0, allNotes.length + ' 条');
ok('抽屉条数 = 库里条数', drawn.dom.length === drawn.want, drawn.dom.length + '/' + drawn.want);
const newest = allNotes.slice().sort((a, b) => {
  const ta = String(a.created_at || ''), tb = String(b.created_at || '');
  if (ta !== tb) return ta < tb ? 1 : -1;
  return (+b.id || 0) - (+a.id || 0);
})[0];
ok('抽屉第一条 = 时间最新的那条',
  String(newest ? newest.summary : '').trim().slice(0, 10) === (drawn.dom[0] || '').slice(0, 10),
  'want=' + String(newest ? newest.summary : '').trim().slice(0, 10) + ' got=' + (drawn.dom[0] || ''));
ok('抽屉里带划/记的时间', drawn.hasTime);
await page.evaluate(() => document.querySelector('[data-act="notes"]').click());
await page.waitForTimeout(200);

// ---------------------------------------------------------------- 2. 点划线 → 就地管理
const made = await pickRange(page, 16);
await page.waitForTimeout(340);
ok('选中文字后工具条出现', !(await page.locator('#rv-sel').evaluate(el => el.hidden)));
await page.click('#rv-sel [data-sel="hl"]');
const savedToast = await waitToast(page, /已划线|已保存/);
ok('划线已保存', /已划线|已保存/.test(savedToast), savedToast);
await page.waitForTimeout(200);

await page.mouse.click(made.x, made.y);
await page.waitForTimeout(380);
const card = await page.evaluate(() => {
  const el = document.querySelector('.rv-nmanage');
  if (!el) return { present: false };
  return {
    present: !el.hidden,
    head: (el.querySelector('.rv-nm-h b') || {}).textContent || '',
    text: (el.querySelector('.rv-nm-q') || {}).textContent || '',
    hasDel: !!el.querySelector('[data-nm="del"]'),
    hasEdit: !!el.querySelector('[data-nm="edit"]'),
    editLabel: (el.querySelector('[data-nm="edit"]') || {}).textContent || '',
    meta: (el.querySelector('.rv-nm-m') || {}).textContent || ''
  };
});
ok('点划线弹出管理卡', card.present);
ok('卡片标题是「划线」', String(card.head).trim() === '划线', card.head);
ok('卡片显示被划的文字', String(card.text).trim().length > 3, String(card.text).slice(0, 26));
ok('卡片有删除/编辑', !!card.hasDel && !!card.hasEdit, card.editLabel);
ok('卡片带时间/来源', /\d{4}-\d{2}-\d{2}/.test(card.meta || ''), card.meta);
ok('没写批注时编辑按钮叫「补写笔记」', card.editLabel === '补写笔记', card.editLabel);

// 再点同一处 = 收起
await page.mouse.click(made.x, made.y);
await page.waitForTimeout(300);
ok('再点同一条收起卡片', await page.evaluate(() => {
  const el = document.querySelector('.rv-nmanage');
  return !el || el.hidden;
}));

// 打开 → 补写笔记 → 卡片标题变「笔记」
await page.mouse.click(made.x, made.y);
await page.waitForTimeout(300);
await page.click('.rv-nmanage [data-nm="edit"]');
await page.waitForTimeout(280);
ok('编辑按钮打开笔记编辑框', !(await page.locator('#rv-noteedit').evaluate(el => el.hidden)));
ok('编辑框预填的是这条划线', await page.evaluate(() => {
  const q = document.getElementById('rv-noteedit-q');
  return (q.textContent || '').trim().length > 3;
}));
await page.fill('#rv-noteedit-t', '探针临时批注');
await page.click('#rv-noteedit [data-ne="save"]');
const editToast = await waitToast(page, /已保存笔记/);
ok('批注保存成功', /已保存笔记/.test(editToast), editToast);
await page.waitForTimeout(320);
await page.mouse.click(made.x, made.y);
await page.waitForTimeout(360);
ok('补了批注后卡片标题变「笔记」', await page.evaluate(() => {
  const el = document.querySelector('.rv-nmanage');
  return !!(el && !el.hidden && (el.querySelector('.rv-nm-h b') || {}).textContent === '笔记');
}));
ok('卡片里显示批注正文', await page.evaluate(() => {
  const el = document.querySelector('.rv-nmanage');
  return !!(el && !el.hidden && /探针临时批注/.test(el.textContent || ''));
}));

// 删除 (也清理自己造的那条)
const noteId = (await notesOf())
  .filter(x => String(x.summary || '').startsWith(String(made.text).slice(0, 8)))
  .map(x => x.id).pop() || 0;
const beforeDel = (await notesOf()).length;
await page.click('.rv-nmanage [data-nm="del"]');
const delToast = await waitToast(page, /已删除/);
await page.waitForTimeout(420);
const afterList = await notesOf();
ok('删除按钮真的删掉 (toast=已删除)', /已删除/.test(delToast), delToast);
ok('库里少一条', afterList.length === beforeDel - 1, beforeDel + ' → ' + afterList.length);
ok('删掉的就是刚才那条', !afterList.map(x => x.id).includes(noteId), 'id=' + noteId);
ok('删除后卡片关闭', await page.evaluate(() => {
  const el = document.querySelector('.rv-nmanage');
  return !el || el.hidden;
}));

// ---------------------------------------------------------------- 3. 查询 / 搜索
const bar = await page.evaluate(() => {
  const el = document.getElementById('rv-sel');
  const btns = [...el.querySelectorAll('button')];
  return { acts: btns.map(b => b.dataset.sel), labels: btns.map(b => (b.textContent || '').trim()) };
});
ok('工具条含 查询/搜索', bar.acts.includes('dict') && bar.acts.includes('web'), bar.acts.join(','));
ok('按钮文案是「查询」「搜索」',
  bar.labels[bar.acts.indexOf('dict')] === '查询' && bar.labels[bar.acts.indexOf('web')] === '搜索',
  bar.labels.join('/'));

const dictApi = await page.evaluate(async () => {
  const k = location.pathname.split('/').pop();
  return await (await fetch('/read/api/book/' + k + '/lookup?q=' + encodeURIComponent('蹉跎'))).json();
});
ok('词典接口返回释义',
  dictApi.ok && (dictApi.entries || []).length > 0 && !!((dictApi.entries[0] || {}).defs || []).length,
  ((dictApi.entries || [{}])[0].defs || []).join(' | ').slice(0, 40));
ok('词典接口标了释义出处', !!dictApi.source, dictApi.source);

await pickRange(page, 4);
await page.waitForTimeout(340);
const wordSel = await page.evaluate(() => String(window.getSelection() || ''));
await page.click('#rv-sel [data-sel="dict"]');
await page.waitForTimeout(1300);
const lookup = await page.evaluate(() => {
  const el = document.querySelector('.rv-lookup');
  if (!el) return { present: false };
  return {
    present: !el.hidden,
    word: el.dataset.word || '',
    title: (el.querySelector('.rv-lk-q') || {}).textContent || '',
    body: (el.querySelector('.rv-lk-body') || {}).textContent || '',
    src: (el.querySelector('.rv-lk-src') || {}).textContent || '',
    hasWeb: !!el.querySelector('[data-lk="web"]')
  };
});
ok('查询弹出查词卡片', lookup.present);
ok('卡片标题 = 选中的词', String(lookup.title).trim() === String(wordSel).trim(), lookup.title + ' vs ' + wordSel);
ok('卡片有内容 (释义或"没查到")', String(lookup.body).trim().length > 3, String(lookup.body).trim().slice(0, 36));
ok('卡片里有「网页搜索」出口', lookup.hasWeb);
ok('工具条已收起', await page.locator('#rv-sel').evaluate(el => el.hidden));

await page.click('.rv-lookup [data-lk="close"]');
await page.waitForTimeout(260);
ok('查词卡片可关闭', await page.evaluate(() => {
  const el = document.querySelector('.rv-lookup');
  return !el || el.hidden;
}));

await pickRange(page, 8);
await page.waitForTimeout(340);
const popupP = ctx.waitForEvent('page', { timeout: 6000 }).catch(() => null);
await page.click('#rv-sel [data-sel="web"]');
const popup = await popupP;
ok('搜索开了新标签页', !!popup, popup ? popup.url().slice(0, 50) : 'no popup');
if (popup) {
  const url = popup.url();
  ok('搜索 URL 走 bing + 带查询词', /bing\.com\/search\?q=/.test(url) && url.length > 40, url.slice(0, 70));
  await popup.close();
}
ok('点了搜索后工具条收起', await page.locator('#rv-sel').evaluate(el => el.hidden));

// ---------------------------------------------------------------- 手机视口
const p2 = await ctx.newPage();
await p2.setViewportSize({ width: 360, height: 780 });
await p2.goto(BASE + '/read/b/' + KEY, { waitUntil: 'domcontentloaded' });
await p2.waitForSelector('.rv-page .rv-blk', { timeout: 20000 });
await p2.waitForTimeout(900);
await pickRange(p2, 8);
await p2.waitForTimeout(420);
const mob = await p2.evaluate(() => {
  const el = document.getElementById('rv-sel');
  const r = el.getBoundingClientRect();
  return {
    shown: !el.hidden, left: Math.round(r.left), right: Math.round(r.right),
    vw: window.innerWidth, h: Math.round(r.height), btns: el.querySelectorAll('button').length
  };
});
ok('手机: 工具条不越界', mob.shown && mob.left >= -1 && mob.right <= mob.vw + 1, JSON.stringify(mob));
ok('手机: 六个按钮都在', mob.btns === 6, mob.btns);

await p2.click('#rv-sel [data-sel="dict"]');
await p2.waitForTimeout(1000);
const mobLookup = await p2.evaluate(() => {
  const el = document.querySelector('.rv-lookup');
  if (!el || el.hidden) return { present: false };
  const r = el.getBoundingClientRect();
  return { present: true, w: Math.round(r.width), left: Math.round(r.left),
           fits: r.left >= 0 && r.right <= window.innerWidth + 1 };
});
ok('手机: 查词卡片是底部卡片且不越界', mobLookup.present && mobLookup.fits, JSON.stringify(mobLookup));

ok('无 JS 报错', errs.length === 0, errs.slice(0, 2).join(' | '));

// ---------------------------------------------------------------- 左右翻页模式
// 分页模式下点划线同样要出卡片, **不能**顺手翻页 (页边热区只占两侧)。
const p3 = await ctx.newPage();
await p3.setDefaultTimeout(20000);
await p3.addInitScript(() => { try { localStorage.setItem('recto.mode', 'page'); } catch (e) {} });
await p3.goto(BASE + '/read/b/' + KEY, { waitUntil: 'domcontentloaded' });
await p3.waitForSelector('.rv-page .rv-blk', { timeout: 20000 });
await p3.waitForTimeout(1200);
const pg = await p3.evaluate(() => {
  const vw = window.innerWidth, vh = window.innerHeight;
  const blks = [...document.querySelectorAll('.rv-page .rv-blk')]
    .filter(b => (b.textContent || '').trim().length > 80 && !b.querySelector('.rv-fn'));
  // 分页模式下段落会跨列 (整块 boundingRect 宽到 1400+), 所以按**选区那一行**的
  // rect 判断: 既要落在视口里, 又要避开左右翻页热区 (两侧各约 23%)。
  for (const b of blks) {
    const r0 = b.getBoundingClientRect();
    if (r0.top < 140 || r0.top > vh - 140) continue;
    const w = document.createTreeWalker(b, NodeFilter.SHOW_TEXT, null);
    let node = null, t = null;
    while ((t = w.nextNode())) { if ((t.nodeValue || '').trim().length > 30) { node = t; break; } }
    if (!node) continue;
    const r = document.createRange();
    r.setStart(node, 5); r.setEnd(node, 21);
    const box = r.getBoundingClientRect();
    if (box.left < vw * 0.27 || box.right > vw - 30 || box.top < 120 || box.bottom > vh - 100) continue;
    const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
    document.dispatchEvent(new Event('selectionchange'));
    const page0 = window.__RV__ && window.__RV__.page ? window.__RV__.page().page : -1;
    return { text: String(s), x: Math.round(box.left + 8),
             y: Math.round(box.top + Math.max(6, box.height / 2)), page0 };
  }
  return { text: '', x: 0, y: 0, page0: -1, miss: true };
});
ok('翻页模式: 找到了本页正文 (否则后面的检查无意义)', !pg.miss, JSON.stringify({ x: pg.x, y: pg.y }));
await p3.waitForTimeout(380);
await p3.click('#rv-sel [data-sel="hl"]');
const pgToast = await waitToast(p3, /已划线|已保存/);
ok('翻页模式: 划线保存', /已划线|已保存/.test(pgToast), pgToast);
await p3.waitForTimeout(240);
await p3.mouse.click(pg.x, pg.y);
await p3.waitForTimeout(400);
const pgCard = await p3.evaluate(() => {
  const el = document.querySelector('.rv-nmanage');
  const page1 = window.__RV__ && window.__RV__.page ? window.__RV__.page().page : -1;
  return { present: !!(el && !el.hidden), page1 };
});
ok('翻页模式: 点划线弹出管理卡', pgCard.present);
ok('翻页模式: 点划线没有顺手翻页', pgCard.page1 === pg.page0, pg.page0 + ' → ' + pgCard.page1);
// 清掉这条 (别留在库里被推到云端)
const pgId = await p3.evaluate(async (sum) => {
  const k = location.pathname.split('/').pop();
  const j = await (await fetch('/read/api/book/' + k)).json();
  const hit = (j.notes || []).filter(x => String(x.summary || '').startsWith(sum.slice(0, 8)));
  return hit.length ? hit[hit.length - 1].id : 0;
}, pg.text);
const pgLeft = await p3.evaluate(async (id) => {
  const k = location.pathname.split('/').pop();
  const j = await (await fetch('/read/api/book/' + k)).json();
  return (j.notes || []).filter(x => x.id === id).length;
}, pgId);
if (pgCard.present) {
  await p3.click('.rv-nmanage [data-nm="del"]');
  await waitToast(p3, /已删除/);
  await p3.waitForTimeout(300);
}
ok('翻页模式: 删掉自己造的划线 (库里 1 → 0)', pgLeft === 1 && (await p3.evaluate(async (id) => {
  const k = location.pathname.split('/').pop();
  const j = await (await fetch('/read/api/book/' + k)).json();
  return (j.notes || []).filter(x => x.id === id).length;
}, pgId)) === 0, 'id=' + pgId);

await writeProgress(progBefore);
await browser.close();

const failed = checks.filter(c => !c.ok);
for (const c of checks) {
  console.log((c.ok ? '  ok   ' : '  FAIL ') + c.name + (c.extra ? '   [' + c.extra + ']' : ''));
}
console.log(`\n${checks.length - failed.length}/${checks.length} passed${failed.length ? ', ' + failed.length + ' FAILED' : ''}`);
process.exit(failed.length ? 1 : 0);
