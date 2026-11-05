/* 阅读器: 翻页效果 (滑动 / 无 / 淡入 / 水波纹 / 翻卷)
 * 1) 面板里有 5 个选项, 默认滑动
 * 2) 每种效果翻页后: 页码正确前进 + 效果真的播了
 * 3) **效果盖满整个页面** —— 动画层是满屏的, 不是只盖正文那一列 (桌面视口比正文列宽)
 * 4) 记忆 (刷新后保持) / 恢复默认 / 手机窄屏不出界 / 0 pageerror
 * 跑法: node tools/probe_reader_fx.mjs
 */
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
const { execFileSync } = require('child_process');

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || 'a6f63af571f05bb4';
const token = execFileSync('python3', [new URL('./make_cookie.py', import.meta.url).pathname],
  { encoding: 'utf8' }).trim();

const out = [];
const chk = (name, cond, extra = '') =>
  out.push(`${cond ? 'ok  ' : 'FAIL'} ${name}${extra ? ' — ' + extra : ''}`);

const browser = await chromium.launch({ args: ['--no-sandbox'] });

async function open(width = 1280, height = 900, fresh = true) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(String(e)));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
  if (fresh) {
    await page.addInitScript(() => {
      if (sessionStorage.getItem('probe.fx.init')) return;
      sessionStorage.setItem('probe.fx.init', '1');
      ['theme', 'fs', 'lh', 'para', 'pad', 'w', 'font', 'mode', 'fx'].forEach(k => localStorage.removeItem('recto.' + k));
    });
  }
  await page.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.rv-page p', { timeout: 15000 });
  await page.waitForTimeout(500);
  return { ctx, page, errs };
}

// 选中某个效果 (change 事件必须手动派发, 赋值不触发)
async function pickFx(page, val) {
  await page.evaluate(v => {
    const s = document.querySelector('#rv-fx');
    s.value = v;
    s.dispatchEvent(new Event('change', { bubbles: true }));
  }, val);
  await page.waitForTimeout(150);
}

async function toPaged(page) {
  const hidden = await page.evaluate(() => document.querySelector('#rv-typo').hidden);
  if (hidden) { await page.click('#rv-typo-btn'); await page.waitForTimeout(150); }
  await page.click('#rv-mode [data-mode="page"]');
  await page.waitForTimeout(900);          // 等 settle (分栏异步碎片化)
}

const { ctx, page, errs } = await open(1280, 900);

/* ---------- 1. 面板选项 ---------- */
await page.click('#rv-typo-btn');
await page.waitForTimeout(200);
const ui = await page.evaluate(() => {
  const s = document.querySelector('#rv-fx');
  if (!s) return null;
  const row = s.closest('.rv-typo-row');
  const r = s.getBoundingClientRect();
  const pr = document.querySelector('#rv-typo').getBoundingClientRect();
  return {
    opts: Array.from(s.options).map(o => [o.value, o.textContent.trim()]),
    value: s.value,
    label: row ? row.querySelector('span').textContent.trim() : '',
    inPanel: r.left >= pr.left - 1 && r.right <= pr.right + 1,
    h: Math.round(r.height),
    cls: document.querySelector('.rv').className
  };
});
chk('#rv-fx 存在且是 5 个选项', ui && ui.opts.length === 5, ui ? JSON.stringify(ui.opts) : 'missing');
chk('选项值齐全', ui && ['slide', 'none', 'fade', 'ripple', 'flip'].every(v => ui.opts.some(o => o[0] === v)));
chk('默认是滑动', ui && ui.value === 'slide', ui && ui.value);
chk('行标签是「翻页效果」', ui && ui.label === '翻页效果', ui && ui.label);
chk('下拉在面板内且高度不过分', ui && ui.inPanel && ui.h <= 40, ui ? `${ui.h}px inPanel=${ui.inPanel}` : '');
chk('根节点带 rv-fx-slide', ui && /\brv-fx-slide\b/.test(ui.cls), ui && ui.cls);

/* ---------- 2. 舞台本身: 固定满屏 ---------- */
const stage0 = await page.evaluate(() => {
  const st = document.querySelector('#rv-fxstage');
  if (!st) return null;
  const r = st.getBoundingClientRect();
  const cs = getComputedStyle(st);
  const view = document.querySelector('.rv-view').getBoundingClientRect();
  return {
    w: Math.round(r.width), h: Math.round(r.height),
    left: Math.round(r.left), top: Math.round(r.top),
    pos: cs.position, z: cs.zIndex, pe: cs.pointerEvents,
    vw: document.documentElement.clientWidth, vh: document.documentElement.clientHeight,
    viewW: Math.round(view.width)
  };
});
chk('效果舞台存在且固定满屏',
  !!stage0 && stage0.pos === 'fixed' && stage0.w >= stage0.vw - 12 && stage0.h === stage0.vh
  && stage0.left === 0 && stage0.top === 0,
  stage0 ? `${stage0.w}x${stage0.h} @${stage0.left},${stage0.top} ${stage0.pos} z=${stage0.z}` : 'missing');
chk('舞台在顶栏之上、抽屉之下, 不吃指针',
  !!stage0 && +stage0.z > 31 && +stage0.z < 40 && stage0.pe === 'none',
  stage0 ? `z=${stage0.z} pe=${stage0.pe}` : '');


/* ---------- 3. 每种效果: 翻页 + 动画 + 覆盖范围 ---------- */
await toPaged(page);
const base = await page.evaluate(() => {
  const el = document.querySelector('.rv-page');
  return { ind: document.querySelector('#rv-pageind').textContent.trim(),
           stride: Math.round(el.getBoundingClientRect().width),
           paged: document.querySelector('.rv').classList.contains('is-paged'),
           viewW: Math.round(document.querySelector('.rv-view').getBoundingClientRect().width) };
});
chk('已进入左右翻页', base.paged, JSON.stringify(base));
chk('舞台比正文列宽 (证明不是只盖阅读区)',
  !!stage0 && stage0.w > base.viewW + 200,
  stage0 ? `舞台 ${stage0.w} vs 正文列 ${base.viewW}` : '');

// 翻一页, 采样这段时间里出现过的效果层 (尺寸/位置/动画) 与整页旋转
async function turnAndMeasure(dir = 'next') {
  return await page.evaluate(async (d) => {
    const root = document.querySelector('.rv');
    const content = document.querySelector('.rv-page');
    const stage = document.querySelector('#rv-fxstage');
    const vw = document.documentElement.clientWidth, vh = document.documentElement.clientHeight;
    let pageAnim = 0, rootRot = null, layers = {}, circle = null;
    const sample = () => {
      pageAnim = Math.max(pageAnim, content.getAnimations().length);
      Array.from(stage.children).forEach(el => {
        const cls = el.className.replace('rv-fxl', '').trim() || '(bare)';
        if (layers[cls]) return;
        const r = el.getBoundingClientRect();
        layers[cls] = {
          w: Math.round(r.width), h: Math.round(r.height),
          left: Math.round(r.left), top: Math.round(r.top),
          pe: getComputedStyle(el).pointerEvents,
          anims: el.getAnimations().length
        };
      });
      const i = stage.querySelector('.rv-fxl-wave i');
      if (i && !circle) {
        const r = i.getBoundingClientRect();
        circle = { css: getComputedStyle(i).width, w: Math.round(r.width),
                   cx: Math.round(r.left + r.width / 2),
                   /* 圆心看**布局位置**: transform 在动画中会把它往里推,
                      按 getBoundingClientRect 量到的是动画中间态 (会随机器快慢漂) */
                   cxLayout: Math.round(i.offsetLeft),
                   stageW: Math.round(stage.getBoundingClientRect().width) };
      }
      if (root.getAnimations().length && !rootRot) {
        rootRot = { anim: root.getAnimations().length, pageAnim: content.getAnimations().length,
                    w: Math.round(root.getBoundingClientRect().width) };
      }
    };
    const t = setInterval(sample, 12);
    document.dispatchEvent(new KeyboardEvent('keydown',
      { key: d === 'next' ? 'ArrowRight' : 'ArrowLeft', bubbles: true }));
    for (let k = 0; k < 26; k++) { await new Promise(r => setTimeout(r, 20)); sample(); }
    clearInterval(t);
    return {
      pageAnim, rootRot, layers, circle,
      ind: document.querySelector('#rv-pageind').textContent.trim(),
      vw, vh,
      left: Math.round(content.getBoundingClientRect().left),
      residue: stage.children.length
    };
  }, dir);
}

// ��针会翻十几页, 结束时把进度写回原处 (否则下次打开这本书直接落到半路)
async function readProgress(pg) {
  return await pg.evaluate(async (k) => {
    const r = await fetch('/read/api/book/' + k, { headers: { accept: 'application/json' } });
    const j = await r.json();
    return (j && j.progress) || null;
  }, KEY);
}
async function writeProgress(pg, pr) {
  if (!pr) return null;
  return await pg.evaluate(async ([k, p]) => {
    const r = await fetch('/read/api/book/' + k + '/progress', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ chapter: p.chapter, offset: p.offset })
    });
    return r.status;
  }, [KEY, pr]);
}
const progress0 = await readProgress(page);

const results = {};
for (const fx of ['none', 'fade', 'ripple', 'flip']) {
  await pickFx(page, fx);
  const cls = await page.evaluate(() => (document.querySelector('.rv').className.match(/rv-fx-\w+/) || [''])[0]);
  const r1 = await turnAndMeasure('next');
  const r2 = await turnAndMeasure('next');
  results[fx] = { cls, r1, r2 };
}
await pickFx(page, 'slide');
results.slide = { cls: '', r1: await turnAndMeasure('next'), r2: await turnAndMeasure('next') };

const pages = s => s.split('/').map(x => parseInt(x.trim(), 10));
chk('无: 不播动画也不铺层',
  results.none.r1.pageAnim === 0 && Object.keys(results.none.r1.layers).length === 0,
  `${results.none.r1.pageAnim} / ${JSON.stringify(results.none.r1.layers)}`);
chk('无: 页码前进', pages(results.none.r2.ind)[0] === pages(base.ind)[0] + 2,
  `${base.ind} → ${results.none.r2.ind}`);

const veil = results.fade.r1.layers['rv-fxl-veil'] || results.fade.r2.layers['rv-fxl-veil'];
chk('淡入: 铺一层满屏底色 (盖住顶栏与页边)',
  !!veil && veil.w >= results.fade.r1.vw - 12 && veil.h === results.fade.r1.vh
  && veil.left === 0 && veil.top === 0 && veil.pe === 'none',
  JSON.stringify(veil));
chk('淡入: 这层真的在动 (WAAPI)', !!veil && veil.anims > 0, veil ? `anims=${veil.anims}` : '');
chk('淡入: 页码前进', pages(results.fade.r2.ind)[0] === pages(base.ind)[0] + 4,
  `${base.ind} → ${results.fade.r2.ind}`);

const wave = results.ripple.r1.layers['rv-fxl-wave'] || results.ripple.r2.layers['rv-fxl-wave'];
const circle = results.ripple.r1.circle || results.ripple.r2.circle;
chk('水波纹: 铺满整屏的一层', !!wave && wave.w >= results.ripple.r1.vw - 12
  && wave.h === results.ripple.r1.vh
  && wave.left === 0 && wave.top === 0 && wave.pe === 'none', JSON.stringify(wave));
chk('水波纹: 圆心贴在整页的转页一侧',
  !!circle && Math.abs(circle.cxLayout - circle.stageW) < 3
  && circle.cx <= circle.stageW + 3, JSON.stringify(circle));
chk('水波纹: 波纹直径远大于正文列 (漫过整页)',
  !!circle && parseFloat(circle.css) > results.ripple.r1.vw * 1.5,
  circle ? `直径 ${circle.css} vs 视口 ${results.ripple.r1.vw}` : '');
chk('水波纹: 页码前进', pages(results.ripple.r2.ind)[0] === pages(base.ind)[0] + 6,
  `${base.ind} → ${results.ripple.r2.ind}`);

chk('翻卷: 整页 (根节点) 在转, 正文列自己不转',
  !!results.flip.r1.rootRot && results.flip.r1.rootRot.anim > 0
  && results.flip.r1.rootRot.pageAnim === 0
  && results.flip.r1.rootRot.w >= results.flip.r1.vw,
  JSON.stringify(results.flip.r1.rootRot));
chk('翻卷: 同时有掠过整屏的高光层',
  !!(results.flip.r1.layers['rv-fxl-sweep'] || results.flip.r2.layers['rv-fxl-sweep']),
  JSON.stringify(results.flip.r1.layers));
chk('翻卷: 页码前进', pages(results.flip.r2.ind)[0] === pages(base.ind)[0] + 8,
  `${base.ind} → ${results.flip.r2.ind}`);

chk('滑动: 页码前进', pages(results.slide.r2.ind)[0] === pages(base.ind)[0] + 10,
  `${base.ind} → ${results.slide.r2.ind}`);
chk('滑动: 不铺满屏效果层', Object.keys(results.slide.r1.layers).length === 0,
  JSON.stringify(results.slide.r1.layers));

await page.waitForTimeout(600);
const residue = await page.evaluate(() => ({
  n: document.querySelector('#rv-fxstage').children.length,
  rootAnim: document.querySelector('.rv').getAnimations().length,
  origin: document.querySelector('.rv').style.transformOrigin,
  scrollX: document.documentElement.scrollWidth - document.documentElement.clientWidth
}));
chk('动画播完不残留视觉层 / 不留下 transform',
  residue.n === 0 && residue.rootAnim === 0 && residue.origin === '', JSON.stringify(residue));
chk('效果期间页面不出现横向滚动条', residue.scrollX <= 0, `${residue.scrollX}px`);

// 翻页后位置必须精确 (页宽 stride 的整数倍, 不能被动画带歪)
const geom = await page.evaluate(() => {
  const el = document.querySelector('.rv-page');
  const m = getComputedStyle(el).transform;
  const tx = m && m !== 'none' ? parseFloat(m.replace(/^matrix\(([^)]*)\).*$/, '$1').split(',')[4]) : 0;
  const w = el.getBoundingClientRect().width;
  const gap = parseFloat(getComputedStyle(el).columnGap) || 0;
  return { tx, w, stride: w + gap };
});
chk('位移是整页 (无残留偏移)', Math.abs(Math.abs(geom.tx) % geom.stride) < 1.5,
  `tx=${geom.tx} stride=${geom.stride}`);

/* ---------- 4. 记忆 / 恢复默认 ---------- */
await pickFx(page, 'ripple');
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForSelector('.rv-page p', { timeout: 15000 });
await page.waitForTimeout(700);
const afterReload = await page.evaluate(() => ({
  val: document.querySelector('#rv-fx').value,
  cls: (document.querySelector('.rv').className.match(/rv-fx-\w+/) || [''])[0],
  stored: localStorage.getItem('recto.fx'),
  paged: document.querySelector('.rv').classList.contains('is-paged')
}));
chk('刷新后效果保持', afterReload.val === 'ripple' && /rv-fx-ripple/.test(afterReload.cls),
  JSON.stringify(afterReload));
chk('刷新后仍在翻页模式', afterReload.paged);

await page.click('#rv-typo-btn');
await page.waitForTimeout(150);
await page.click('.rv-typo-h [data-typo="reset"]');
await page.waitForTimeout(250);
const afterReset = await page.evaluate(() => ({
  val: document.querySelector('#rv-fx').value,
  cls: (document.querySelector('.rv').className.match(/rv-fx-\w+/) || [''])[0],
  stored: localStorage.getItem('recto.fx')
}));
chk('恢复默认回到滑动', afterReset.val === 'slide' && /rv-fx-slide/.test(afterReset.cls),
  JSON.stringify(afterReset));
chk('恢复默认清掉记忆', afterReset.stored === null, String(afterReset.stored));

chk('桌面 0 报错', errs.length === 0, errs.slice(0, 2).join(' | '));

const restored = await writeProgress(page, progress0);
const after = await readProgress(page);
chk('探针把阅读进度写回原处',
  !!after && !!progress0 && after.chapter === progress0.chapter
  && Math.abs(after.offset - progress0.offset) <= 2, JSON.stringify({ restored, before: progress0, after }));
await ctx.close();

/* ---------- 5. 手机窄屏 (正文列 = 满宽, 效果同样满屏) ---------- */
const m = await open(390, 780, true);
const progressM = await readProgress(m.page);
await m.page.click('#rv-typo-btn');
await m.page.waitForTimeout(250);
const mob = await m.page.evaluate(() => {
  const s = document.querySelector('#rv-fx');
  const r = s.getBoundingClientRect();
  const pr = document.querySelector('#rv-typo').getBoundingClientRect();
  return {
    inPanel: r.left >= pr.left - 1 && r.right <= pr.right + 1,
    w: Math.round(r.width),
    overflowX: document.documentElement.scrollWidth - document.documentElement.clientWidth
  };
});
chk('手机: 下拉不出面板', mob.inPanel, JSON.stringify(mob));
chk('手机: 下拉可用宽度', mob.w >= 100, `${mob.w}px`);
await toPaged(m.page);
await pickFx(m.page, 'ripple');
const mobWave = await m.page.evaluate(async () => {
  const stage = document.querySelector('#rv-fxstage');
  let info = null;
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
  for (let k = 0; k < 20; k++) {
    await new Promise(r => setTimeout(r, 20));
    const w = stage.querySelector('.rv-fxl-wave');
    if (w && !info) {
      const r = w.getBoundingClientRect();
      info = { w: Math.round(r.width), h: Math.round(r.height),
               vw: document.documentElement.clientWidth,
               vh: document.documentElement.clientHeight };
    }
  }
  return info;
});
chk('手机: 效果层同样铺满整屏',
  !!mobWave && mobWave.w >= mobWave.vw - 12 && mobWave.h === mobWave.vh, JSON.stringify(mobWave));
chk('手机: 无横向溢出', mob.overflowX <= 0, `${mob.overflowX}px`);
chk('手机: 0 报错', m.errs.length === 0, m.errs.slice(0, 2).join(' | '));
await writeProgress(m.page, progressM);
await m.ctx.close();

await browser.close();
console.log(out.join('\n'));
const bad = out.filter(l => l.startsWith('FAIL')).length;
console.log(`\n${out.length - bad}/${out.length} passed`);
process.exit(bad ? 1 : 0);
