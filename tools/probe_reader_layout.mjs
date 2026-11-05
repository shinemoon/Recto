/* 页内排版探针: 图文混排尺寸 / 中文版式 (clreq) / 首行缩进开关。
 *
 * 关键断言:
 *   ① 段内脚注标记小图 ≤ 1.5 行高 (书内 CSS .w10 = .85em), 不再铺满整行
 *   ② 独占一行的插图限制在版心内且不越页
 *   ③ 中文段落有首行缩进、西文段落没有 (按章判定)
 *   ④ 两端对齐 + 末行左对齐 + 禁则/中西文间距等属性真的落到 computedStyle
 *   ⑤ 排版面板「首行缩进 无/两字」即时生效并记住
 */
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
const { execFileSync } = require('child_process');

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || 'a6f63af571f05bb4';
const CH = process.env.RV_CH || '7';
const token = execFileSync('python3', [new URL('./make_cookie.py', import.meta.url).pathname],
  { encoding: 'utf8' }).trim();

const out = [];
const chk = (name, cond, extra = '') =>
  out.push(`${cond ? 'ok  ' : 'FAIL'} ${name}${extra ? ' — ' + extra : ''}`);

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
page.setDefaultTimeout(8000);
const errs = [];
page.on('pageerror', e => errs.push(String(e)));
page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
await page.addInitScript(() => {
  ['theme', 'fs', 'lh', 'para', 'pad', 'w', 'font', 'indent'].forEach(
    k => localStorage.removeItem('recto.' + k));
});
await page.goto(`${BASE}/read/b/${KEY}#c${CH}`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('.rv-page p', { timeout: 30000 });
await page.waitForTimeout(900);

/* 进度快照: 翻章会改写服务端进度, 收尾必须还原 (tests/test_probe_hygiene.py) */
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
const prog0 = await readProgress();

/* 需要看指定章时从目录点进去 (开局位置由服务端进度决定, hash 只在翻页模式当锚点) */
if (process.env.RV_OPEN_CH) {
  await page.evaluate(ch => {
    const a = document.querySelector('#rv-toc-list a[data-ch="' + ch + '"]');
    if (a) a.click();
  }, String(process.env.RV_OPEN_CH));
  await page.waitForTimeout(1200);
}

const info = await page.evaluate(() => {
  const pageEl = document.querySelector('.rv-page');
  const colW = pageEl.getBoundingClientRect().width;
  const cs = getComputedStyle(pageEl);
  const p = document.querySelector('.rv-ch.rv-cjk p');
  const pcs = p ? getComputedStyle(p) : null;
  const figs = [...document.querySelectorAll('.rv-page img.rfig')].map(img => {
    const r = img.getBoundingClientRect();
    return { cls: img.className, w: +r.width.toFixed(1), h: +r.height.toFixed(1),
             natural: img.naturalWidth + 'x' + img.naturalHeight,
             style: img.getAttribute('style') || '' };
  });
  return {
    colW: +colW.toFixed(1),
    textAlign: cs.textAlign, lineBreak: cs.lineBreak || cs.getPropertyValue('line-break'),
    wordBreak: cs.wordBreak, trim: cs.getPropertyValue('text-spacing-trim'),
    autospace: cs.getPropertyValue('text-autospace'),
    hanging: cs.getPropertyValue('hanging-punctuation'),
    fontSize: cs.fontSize,
    lineH: cs.lineHeight,
    pAlign: pcs && pcs.textAlign, pAlignLast: pcs && pcs.textAlignLast,
    pIndent: pcs && pcs.textIndent,
    pFontSize: pcs && pcs.fontSize,
    indentVar: getComputedStyle(document.querySelector('.rv')).getPropertyValue('--rv-indent').trim(),
    cjkChapters: document.querySelectorAll('.rv-ch.rv-cjk').length,
    figs,
    noteIndent: (() => {
      const n = [...document.querySelectorAll('.rv-page p')].find(e => e.textContent.startsWith('注：'));
      return n ? getComputedStyle(n).textIndent : null;
    })(),
    sepAlign: (() => {
      const n = [...document.querySelectorAll('.rv-page p')].find(e => e.textContent.trim() === '◎');
      return n ? getComputedStyle(n).textAlign : null;
    })()
  };
});

/* ① 段内小图 */
const inl = info.figs.filter(f => /rfig-inl/.test(f.cls));
const blk = info.figs.filter(f => !/rfig-inl/.test(f.cls));
const fs = parseFloat(info.fontSize);
if (!inl.length) {
  chk('(本章无段内小图, 跳过行内图断言)', true, '本章没有段内插图');
} else {
  chk('段内小图都被识别 (rfig-inl)', true, `${inl.length} 张`);
  chk('段内小图高度 ≈ 1 个字号 (书内 .85em)',
    inl.every(f => f.h <= fs * 1.5), inl.map(f => f.h).join(','));
  chk('段内小图不是原始像素宽 (500px)', inl.every(f => f.w < 40),
    inl.map(f => f.w).join(','));
  chk('段内小图没有铺满整行', inl.every(f => f.w < info.colW * 0.2),
    `col=${info.colW}`);
  chk('段内小图带书内 width 声明 (不是猜的)',
    inl.every(f => /width:\s*[.\d]+(em|px|%)/.test(f.style)), inl[0] && inl[0].style);
}
/* ①b 小图必须真的"混排在行内" —— 前后紧贴正文, 不是自己独占一行 */
const inlinePos = await page.evaluate(() => {
  const img = [...document.querySelectorAll('.rv-page img.rfig-inl')].find(
    i => i.parentNode && i.parentNode.textContent.trim().length > 10);
  if (!img) return null;
  const prev = img.previousSibling, next = img.nextSibling;
  const p = img.closest('p') || img.parentNode;
  const ir = img.getBoundingClientRect(), pr = p.getBoundingClientRect();
  return {
    prevText: prev ? String(prev.textContent || '').slice(-6) : '',
    nextText: next ? String(next.textContent || '').slice(0, 6) : '',
    insideP: ir.top >= pr.top - 1 && ir.bottom <= pr.bottom + 1,
    ownLine: Math.abs(ir.width - pr.width) < 4
  };
});
chk('小图前后紧贴正文 (真的是行内混排)',
  !inl.length || (!!inlinePos && inlinePos.prevText.length > 0 && inlinePos.nextText.length > 0),
  JSON.stringify(inlinePos));
chk('小图在段落框内且不独占一行',
  !inl.length || (!!inlinePos && inlinePos.insideP && !inlinePos.ownLine),
  JSON.stringify(inlinePos));

/* ①c 纯图段落: 即使在中文章的上下文里也不吃首行缩进 */
const figIndent = await page.evaluate(() => {
  const art = document.querySelector('.rv-ch');
  const p = document.querySelector('.rv-ch .rv-figblk');
  if (!art || !p) return null;
  const before = getComputedStyle(p).textIndent;
  art.classList.add('rv-cjk');            // 临时当作中文段落章
  const after = getComputedStyle(p).textIndent;
  art.classList.remove('rv-cjk');
  return { before, after };
});
chk('纯图段落不吃首行缩进 (中文章上下文里也是 0)',
  !figIndent || (figIndent.after === '0px'), JSON.stringify(figIndent));

/* ② 块级图 */
const figGeo = await page.evaluate(() => {
  const pageEl = document.querySelector('.rv-page');
  const pRect = pageEl.getBoundingClientRect();
  return [...document.querySelectorAll('.rv-page img.rfig:not(.rfig-inl)')].map(img => {
    const r = img.getBoundingClientRect();
    return { w: +r.width.toFixed(1), h: +r.height.toFixed(1),
             cx: +(r.left + r.width / 2).toFixed(1),
             pageCx: +(pRect.left + pRect.width / 2).toFixed(1),
             vh: window.innerHeight };
  });
});
chk('块级插图不超版心宽', figGeo.every(f => f.w <= info.colW + 1),
  figGeo.map(f => f.w).join(',') || '本章无块级插图');
chk('块级插图水平居中 (不被首行缩进推右)',
  figGeo.every(f => Math.abs(f.cx - f.pageCx) < 6),
  figGeo.map(f => `${f.cx}/${f.pageCx}`).join(' ') || '本章无块级插图');
chk('块级插图不越出一屏',
  figGeo.every(f => f.h <= f.vh * 0.92),
  figGeo.map(f => `${f.h}/${f.vh}`).join(' ') || '本章无块级插图');

/* ③ 中文版式 */
chk('中文段落有首行缩进 2 字符 (按该段字号算)',
  Math.abs(parseFloat(info.pIndent) - parseFloat(info.pFontSize) * 2) < 1.5,
  `${info.pIndent} @ ${info.pFontSize}`);
chk('CSS 变量 --rv-indent 有值', info.indentVar !== '', info.indentVar);
chk('段落两端对齐 + 末行左对齐',
  info.pAlign === 'justify' && info.pAlignLast === 'left',
  `${info.pAlign}/${info.pAlignLast}`);
chk('禁则: line-break strict', /strict/.test(info.lineBreak || ''), info.lineBreak);
chk('西文整词不拆: word-break normal', info.wordBreak === 'normal', info.wordBreak);
chk('行首标点压缩 (text-spacing-trim)',
  /space-first|trim/.test(info.trim || '') || info.trim === '',
  info.trim || '(浏览器不支持, 忽略)');
chk('中西文自动间距 (text-autospace)',
  /normal/.test(info.autospace || '') || info.autospace === '',
  info.autospace || '(浏览器不支持, 忽略)');
chk('章节被标为中文段落 (.rv-cjk)', info.cjkChapters >= 1, String(info.cjkChapters));
chk('注文段是悬挂缩进 (负 text-indent)',
  info.noteIndent === null || /-/.test(info.noteIndent), String(info.noteIndent));
chk('装饰分隔符 ◎ 居中', info.sepAlign === null || info.sepAlign === 'center',
  String(info.sepAlign));

/* ⑤ 首行缩进开关 */
await page.click('[data-act="typo"]');
await page.waitForTimeout(200);
await page.click('[data-indent="0"]');
await page.waitForTimeout(300);
const off = await page.evaluate(() => {
  const p = document.querySelector('.rv-ch.rv-cjk p');
  return { indent: getComputedStyle(p).textIndent,
           stored: localStorage.getItem('recto.indent'),
           pressed: document.querySelector('[data-indent="0"]').getAttribute('aria-pressed') };
});
chk('切「无缩进」即时生效', off.indent === '0px', off.indent);
chk('缩进选择被记住', off.stored === '0', String(off.stored));
chk('分段按钮状态同步', off.pressed === 'true', String(off.pressed));

await page.click('[data-indent="2"]');
await page.waitForTimeout(300);
const back2 = await page.evaluate(() => {
  const p = document.querySelector('.rv-ch.rv-cjk p');
  return getComputedStyle(p).textIndent;
});
chk('切回「两字」恢复缩进',
  Math.abs(parseFloat(back2) - parseFloat(info.pFontSize) * 2) < 1.5, back2);

/* ④ 后端契约: 章节 payload 带 cjk 标记 + 每块带 style */
await page.evaluate(() => { window.scrollTo(0, 0); });
const apiShape = await page.evaluate(async () => {
  const key = location.pathname.split('/').pop();
  const j = await (await fetch('/read/api/book/' + key + '/chapter/' + (location.hash.match(/c(\d+)/) || [0, '7'])[1])).json();
  return { cjk: j.cjk, hasStyle: (j.blocks || []).every(b => 'style' in b),
           inlStyled: (j.blocks || []).filter(b => b.html.includes('rfig-inl'))
                        .every(b => /style="width:/.test(b.html)),
           blocksStyled: (j.blocks || []).filter(b => b.html.includes('<img'))
                        .every(b => /class="rfig/.test(b.html)) };
});
chk('章节 payload 带 cjk 标记', typeof apiShape.cjk === 'boolean', String(apiShape.cjk));
chk('每个块都带 style 字段', apiShape.hasStyle === true);
chk('行内小图的尺寸在 HTML 里就内联好了 (不靠前端猜)',
  apiShape.inlStyled === true, String(apiShape.inlStyled));
chk('所有插图都带 .rfig 类 (块级/行内分流)',
  apiShape.blocksStyled === true, String(apiShape.blocksStyled));

/* ⑥ 左右翻页: 图片不许溢出页面 (分页高度是按视口算的, 图必须先被限高) */
await page.evaluate(() => {
  const b = document.querySelector('[data-act="typo"]');
  if (b) b.click();
});
await page.waitForTimeout(200);
await page.evaluate(() => {
  const b = document.querySelector('[data-mode="page"]');
  if (b) b.click();
});
await page.waitForTimeout(1200);
const paged = await page.evaluate(() => {
  const view = document.querySelector('.rv-view');
  const vr = view ? view.getBoundingClientRect() : null;
  const imgs = [...document.querySelectorAll('.rv-page img')].map(i => {
    const r = i.getBoundingClientRect();
    return { w: +r.width.toFixed(1), h: +r.height.toFixed(1) };
  });
  const pg = window.__RV__ && window.__RV__.page ? window.__RV__.page() : null;
  return { viewH: vr ? +vr.height.toFixed(1) : null,
           viewW: vr ? +vr.width.toFixed(1) : null,
           pageW: pg ? pg.stride : null, count: pg ? pg.count : null,
           imgs, overflowX: document.documentElement.scrollWidth - document.documentElement.clientWidth };
});
chk('左右翻页模式能起来', paged.count >= 1, JSON.stringify({ c: paged.count, w: paged.viewW }));
chk('插图不高于一页 (paged 限高生效)',
  paged.imgs.every(i => i.h <= (paged.viewH || 1e9)),
  paged.imgs.map(i => `${i.w}x${i.h}/${paged.viewH}`).join(' ') || '本章无图');
chk('插图不宽于版心', paged.imgs.every(i => i.w <= (paged.pageW || paged.viewW || 1e9) + 1),
  paged.imgs.map(i => i.w).join(',') || '本章无图');
chk('翻页模式无横向溢出', paged.overflowX <= 0, String(paged.overflowX));
await page.evaluate(() => {
  const b = document.querySelector('[data-mode="scroll"]');
  if (b) b.click();
});

/* ⑦ 手机: 排版面板多了一行「首行缩进」, 不许把面板撑出界 */
const mctx = await browser.newContext({ viewport: { width: 390, height: 780 } });
await mctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const mp = await mctx.newPage();
const merrs = [];
mp.on('pageerror', e => merrs.push(String(e)));
mp.setDefaultTimeout(8000);
await mp.goto(`${BASE}/read/b/${KEY}#c${CH}`, { waitUntil: 'domcontentloaded' });
await mp.waitForSelector('.rv-page p', { timeout: 20000 });
await mp.evaluate(() => { const b = document.querySelector('[data-act="typo"]'); if (b) b.click(); });
await mp.waitForTimeout(400);
const mob = await mp.evaluate(() => {
  const el = document.querySelector('#rv-typo');
  const seg = document.querySelector('#rv-indent');
  const r = seg ? seg.getBoundingClientRect() : null;
  const pr = el ? el.getBoundingClientRect() : null;
  return { panelW: pr && Math.round(pr.width), panelScroll: el && el.scrollWidth,
           segRight: r && Math.round(r.right), panelRight: pr && Math.round(pr.right),
           inView: r ? (r.right <= window.innerWidth + 1 && r.left >= -1) : false,
           overflowX: document.documentElement.scrollWidth - document.documentElement.clientWidth };
});
chk('手机: 首行缩进分段按钮在面板内',
  !!mob.inView && mob.segRight <= mob.panelRight + 1, JSON.stringify(mob));
chk('手机: 面板不横向溢出', mob.overflowX <= 0, String(mob.overflowX));
chk('手机: 无 JS 报错', merrs.length === 0, merrs.join(' | '));
await mctx.close();

chk('无 JS 报错', errs.length === 0, errs.join(' | '));

console.log(out.join('\n'));
const bad = out.filter(l => l.startsWith('FAIL')).length;
console.log(`\n${out.length - bad} passed / ${bad} failed`);

/* 还原阅读进度 (翻章/切缩进都可能触发保存) */
await writeProgress(prog0);

await browser.close();
process.exit(bad ? 1 : 0);
