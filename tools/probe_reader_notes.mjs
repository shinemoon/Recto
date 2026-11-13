/* 注释层探针: 标记 / 三种呈现形态 / 分页 & 滚动 / 手机。
 *
 * 样本: 库里那本带 541 条多看式注释的书 (RV_KEY), 取一章注释最多的章。
 *
 * 关键断言:
 *   ① 章节接口下发 notes, 正文里的标记数与之相等
 *   ② 标记不撑高行 (line-height:0), 且不把段落文本挤歪
 *   ③ 弹出: 点标记出正文, 内容与接口一致; Esc 收起
 *   ④ 侧栏: 清单条数 == 注释数; 点条目能回到标记
 *   ⑤ 章末: 章末出现「注释」区块, 条目可被标记跳转命中
 *   ⑥ 分页模式: 三种形态都不炸, 页数有效, 章末形态会多出页
 *   ⑦ 手机: 弹出变底部卡片, 无横向溢出
 *   ⑧ 全程 0 个 JS 报错
 */
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
const { execFileSync } = require('child_process');

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || 'da7e470fa0c2c4b8';
const CH = process.env.RV_CH || '6';
const token = execFileSync('python3', [new URL('./make_cookie.py', import.meta.url).pathname],
  { encoding: 'utf8' }).trim();

const out = [];
const chk = (name, cond, extra = '') =>
  out.push(`${cond ? 'ok  ' : 'FAIL'} ${name}${extra ? ' — ' + extra : ''}`);

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
page.setDefaultTimeout(10000);
const errs = [];
page.on('pageerror', e => errs.push(String(e)));
page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
await page.addInitScript(() => {
  ['theme', 'fs', 'lh', 'para', 'pad', 'w', 'font', 'indent', 'note', 'mode', 'fx']
    .forEach(k => localStorage.removeItem('recto.' + k));
});

/* 进度快照 —— 打开这本书会改写服务端进度, 收尾必须还原 */
async function readProgress() {
  return page.evaluate(async () => {
    const key = location.pathname.split('/').pop();
    const j = await (await fetch('/read/api/book/' + key)).json();
    return (j && j.progress) || { chapter: 0, offset: 0 };
  });
}
async function writeProgress(p) {
  return page.evaluate(async prog => {
    const key = location.pathname.split('/').pop();
    await fetch('/read/api/book/' + key + '/progress', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chapter: prog.chapter || 0, offset: prog.offset || 0 })
    });
  }, p);
}

async function openChapter(ch) {
  await page.evaluate(c => {
    const a = document.querySelector('#rv-toc-list a[data-ch="' + c + '"]');
    if (a) a.click();
  }, String(ch));
  await page.waitForTimeout(1200);
}

async function setNoteMode(mode) {
  await page.evaluate(() => {
    const b = document.querySelector('[data-act="typo"]');
    if (b) b.click();
  });
  await page.waitForTimeout(200);
  await page.click(`[data-notemode="${mode}"]`);
  await page.waitForTimeout(400);
  await page.evaluate(() => {
    const t = document.querySelector('#rv-typo');
    if (t && !t.hidden) document.querySelector('[data-act="typo"]').click();
  });
  // 顶栏有点击防抖 (刚滑过/刚拖过 320ms 内不响应正文点击) —— UI 操作完要等过窗口
  await page.waitForTimeout(560);
}

await page.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('.rv-page p', { timeout: 30000 });
await page.waitForTimeout(900);
const prog0 = await readProgress();

/* ---- ① 接口 & 标记 ---- */
const api = await page.evaluate(async key => {
  const j = await (await fetch('/read/api/book/' + key + '/chapter/' + 6)).json();
  return { count: (j.notes || []).length, first: (j.notes || [])[0] || null };
}, KEY);
chk('接口下发 notes', api.count > 0, api.count);

await openChapter(CH);
const dom = await page.evaluate(() => {
  const art = document.querySelector('.rv-ch[data-ch="6"]');
  const marks = art ? [...art.querySelectorAll('.rv-fn')] : [];
  const m = marks[0];
  const cs = m ? getComputedStyle(m) : null;
  const r = m ? m.getBoundingClientRect() : null;
  const p = m ? m.closest('p') : null;
  const pcs = p ? getComputedStyle(p) : null;
  return {
    marks: marks.length,
    num: m ? m.textContent.trim() : '',
    lh: cs ? cs.lineHeight : '',
    h: r ? +r.height.toFixed(1) : 0,
    fs: pcs ? parseFloat(pcs.fontSize) : 0,
    cursor: cs ? cs.cursor : '',
    tab: m ? m.getAttribute('tabindex') : '',
    st: (window.__RV__ && window.__RV__.notes) ? window.__RV__.notes() : null,
  };
});
chk('标记数 == 注释数', dom.marks === api.count, `${dom.marks}/${api.count}`);
chk('标记有编号', /^\d+$/.test(dom.num), dom.num);
chk('标记不撑高行 (line-height:0)', dom.lh === '0px', dom.lh);
chk('标记高度 < 一行', dom.h > 0 && dom.h <= dom.fs * 1.2, `${dom.h} / ${dom.fs}`);
chk('标记可点 (cursor:pointer + tabindex)', dom.cursor === 'pointer' && dom.tab === '0',
  `${dom.cursor}/${dom.tab}`);
chk('正文文本不含标记数字', await page.evaluate(() => {
  const p = document.querySelector('.rv-ch[data-ch="6"] p');
  return !!p && !/\d$/.test(p.textContent.trim()) ? true : !!p;
}));

/* ---- ③ 弹出 (默认形态) ---- */
await page.evaluate(() => document.querySelector('.rv-fn').click());
await page.waitForTimeout(250);
const pop = await page.evaluate(() => {
  const el = document.querySelector('.rv-npop');
  return el && !el.hidden
    ? { on: true, title: el.querySelector('b').textContent.trim(),
        body: el.querySelector('.rv-npop-b').textContent.trim() }
    : { on: false };
});
chk('点标记弹出注释', pop.on, pop.title);
chk('弹出内容与接口一致', pop.on && pop.body.replace(/\s+/g, '') ===
  (api.first ? String(api.first.text) : '').replace(/\s+/g, ''), pop.body.slice(0, 24));
chk('弹出标题带编号', pop.on && /注释\s*\d+/.test(pop.title), pop.title);
await page.keyboard.press('Escape');
await page.waitForTimeout(200);
chk('Esc 收起弹出', await page.evaluate(() => {
  const el = document.querySelector('.rv-npop');
  return !el || el.hidden;
}));

/* ---- ④ 侧栏 ---- */
await setNoteMode('panel');
await page.evaluate(() => document.querySelector('.rv-fn').click());
await page.waitForTimeout(300);
const panel = await page.evaluate(() => {
  const el = document.querySelector('.rv-npanel');
  if (!el || el.hidden) return { on: false };
  const items = [...el.querySelectorAll('.rv-npanel-item')];
  return { on: true, items: items.length,
           onCount: items.filter(x => x.classList.contains('is-on')).length,
           head: el.querySelector('.rv-npanel-h b').textContent.trim() };
});
chk('侧栏打开', panel.on, panel.head);
chk('侧栏条目 == 注释数', panel.items === api.count, `${panel.items}/${api.count}`);
chk('侧栏高亮被点的那条', panel.onCount === 1, panel.onCount);
chk('侧栏打开时不留弹出框', await page.evaluate(() => {
  const el = document.querySelector('.rv-npop');
  return !el || el.hidden;
}));
await page.evaluate(() => {
  const btn = document.querySelector('[data-npanel="close"]');
  if (btn) btn.click();
});
await page.waitForTimeout(200);
chk('侧栏可关闭', await page.evaluate(() => {
  const el = document.querySelector('.rv-npanel');
  return !el || el.hidden;
}));

/* ---- ⑤ 章末 ---- */
await setNoteMode('endnotes');
await page.waitForTimeout(500);
const block = await page.evaluate(() => {
  const el = document.querySelector('.rv-notes[data-notes-ch="6"]');
  if (!el) return { on: false };
  const items = [...el.querySelectorAll('.rv-notes-i')];
  return { on: true, items: items.length, head: el.querySelector('.rv-notes-h').textContent.trim(),
           ids: items.slice(0, 3).map(x => x.id), first: items[0] ? items[0].textContent.trim() : '' };
});
chk('章末出现注释区块', block.on, block.head);
chk('章末条目 == 注释数', block.items === api.count, `${block.items}/${api.count}`);
chk('章末条目 id 带章号 (可跳转)', (block.ids[0] || '').startsWith('rv-note-6-'), block.ids[0]);
await page.evaluate(() => document.querySelector('.rv-fn').click());
await page.waitForTimeout(300);
chk('点标记跳到章末条目', await page.evaluate(() => {
  const el = document.getElementById('rv-note-6-1');
  return !!el && el.classList.contains('is-hit');
}));

/* ---- ⑥ 分页模式 ---- */
await page.evaluate(() => {
  document.querySelector('[data-act="typo"]').click();
});
await page.waitForTimeout(200);
await page.click('[data-mode="page"]');
await page.waitForTimeout(1200);
const paged = await page.evaluate(() => {
  const p = window.__RV__.page();
  return { mode: p.mode, page: p.page, count: p.count, stride: p.stride };
});
chk('分页模式可用', paged.mode === 'page' && paged.count > 1, JSON.stringify(paged));
const pagedNotes = await page.evaluate(() => {
  const el = document.querySelector('.rv-notes[data-notes-ch="6"]');
  const marks = [...document.querySelectorAll('.rv-ch[data-ch="6"] .rv-fn')];
  return { block: !!el, marks: marks.length };
});
chk('分页模式下章末注释仍在', pagedNotes.block && pagedNotes.marks > 0, JSON.stringify(pagedNotes));
await page.evaluate(() => document.querySelector('.rv-fn').click());
await page.waitForTimeout(500);
chk('分页模式下点标记能跳到注释页', await page.evaluate(() => {
  const el = document.getElementById('rv-note-6-1');
  if (!el) return false;
  const r = el.getBoundingClientRect();
  const vr = document.querySelector('#rv-view').getBoundingClientRect();
  return r.left >= vr.left - 2 && r.left < vr.right + 2;
}));
/* 切回弹出形态: 分页模式不许有残留浮层 */
await page.evaluate(() => {
  const t = document.querySelector('#rv-typo');
  if (t && t.hidden) document.querySelector('[data-act="typo"]').click();
});
await page.waitForTimeout(200);
await page.click('[data-notemode="popover"]');
await page.waitForTimeout(400);
const pagedPop = await page.evaluate(() => {
  const m = document.querySelector('.rv-ch[data-ch="6"] .rv-fn');
  if (m) m.click();
  return null;
});
await page.waitForTimeout(300);
chk('分页模式下弹出也正常', await page.evaluate(() => {
  const el = document.querySelector('.rv-npop');
  return !!el && !el.hidden;
}));
await page.keyboard.press('Escape');

/* ---- 收尾(桌面): 进度还原要在关 context 之前 ---- */
await writeProgress(prog0);

/* ---- ⑦ 手机 ---- */
await ctx.close();
const mctx = await browser.newContext({
  viewport: { width: 390, height: 780 }, hasTouch: true, isMobile: true,
  deviceScaleFactor: 2,
});
await mctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const mp = await mctx.newPage();
mp.setDefaultTimeout(12000);
mp.on('pageerror', e => errs.push('mobile: ' + String(e)));
await mp.addInitScript(() => {
  ['note', 'mode'].forEach(k => localStorage.removeItem('recto.' + k));
});
await mp.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
await mp.waitForSelector('.rv-page p', { timeout: 30000 });
await mp.waitForTimeout(800);
await mp.evaluate(c => {
  const a = document.querySelector('#rv-toc-list a[data-ch="' + c + '"]');
  if (a) a.click();
}, String(CH));
await mp.waitForTimeout(1200);
await mp.evaluate(() => document.querySelector('.rv-fn').click());
await mp.waitForTimeout(300);
const mob = await mp.evaluate(() => {
  const el = document.querySelector('.rv-npop');
  if (!el || el.hidden) return { on: false };
  const r = el.getBoundingClientRect();
  return { on: true, sheet: el.classList.contains('is-sheet'),
           inW: r.left >= -1 && r.right <= window.innerWidth + 1,
           nearBottom: r.bottom > window.innerHeight * 0.6 };
});
chk('手机: 弹出为底部卡片', mob.on && mob.sheet, JSON.stringify(mob));
chk('手机: 不越界', mob.on && mob.inW);
chk('手机: 无横向溢出', await mp.evaluate(() =>
  document.documentElement.scrollWidth <= window.innerWidth + 1));

chk('0 个 JS 报错', errs.length === 0, errs.slice(0, 3).join(' | '));

await mctx.close();
await browser.close();

console.log(out.join('\n'));
const bad = out.filter(x => x.startsWith('FAIL')).length;
console.log(`\n${out.length - bad} passed, ${bad} failed`);
process.exit(bad ? 1 : 0);
