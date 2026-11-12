/* 书宠小图标 (阅读器右下角): 两行摘要 / 有更新才转 / 悬停看 / 点击新开页。
 *
 * 需求 (2026-11-12): 「将最近一次更新的书宠梗概总结下, 最多两行字, 在阅读界面的右下角
 * (阅读时间上方) 做一个提示小图标 (不要太显眼影响阅读), 有更新则做一点小动态, 例如慢慢旋转,
 * 然后鼠标过去悬浮就显示更改内容, 点击则新开页面跳转到书宠页面看细节故事。」
 *
 * 验的事:
 *   1. 图标出现且很小、半透明 (不抢注意力), 位置在「掌阅同步」那行小字上方
 *   2. 摘要正好两行, 与接口一致 (标题 + 开头两句)
 *   3. 没看过这一回 → 带 is-new, 图标 9 秒一圈慢慢转; 悬停暂停
 *   4. 悬停 → 两行摘要浮出; 点开 → 新标签页到 /pet/; 点过就不转了 (记在 localStorage)
 *   5. 手机 (390px): 不出视口, 不压在同步小字上, 页面不横向溢出
 *   6. 全程 0 页面报错
 *
 * 跑法: node tools/probe_reader_pet_badge.mjs      (RV_BASE / RV_KEY 可覆盖)
 * 探针会真写阅读进度 (打开书就会存一次) → 开头快照, 收尾写回。
 */
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || 'da7e470fa0c2c4b8';   // 小一点的书, 快
const token = cookie();

const out = [];
const chk = (name, cond, extra = '') =>
  out.push(`${cond ? 'ok  ' : 'FAIL'} ${name}${extra ? ' — ' + extra : ''}`);

const browser = await chromium.launch({ args: ['--no-sandbox'] });

async function readProgress(pg) {
  return await pg.evaluate(async (k) => {
    const j = await fetch('/read/api/book/' + k, { headers: { accept: 'application/json' } })
      .then((x) => x.json()).catch(() => null);
    return (j && j.progress) || null;
  }, KEY);
}

async function writeProgress(pg, pr) {
  if (!pr) return null;
  return await pg.evaluate(async ([k, p]) => {
    const r = await fetch('/read/api/book/' + k + '/progress', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ chapter: p.chapter, offset: p.offset, jump: 1 })
    }).catch(() => null);
    return r ? r.status : null;
  }, [KEY, pr]);
}

let progress0 = null;
try {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

  // 先落同源页面: 取进度快照 + 把"看过哪一回"清掉 (制造"有更新"的现场)
  await page.goto(`${BASE}/#library`, { waitUntil: 'domcontentloaded' });
  progress0 = await readProgress(page);
  await page.evaluate(() => localStorage.removeItem('recto.pet.seen'));

  await page.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => {
    const e = document.querySelector('#rv-pet');
    return !!(e && !e.hidden);
  }, null, { timeout: 20000 });
  await page.waitForTimeout(600);

  const j = await page.evaluate(() => window.__RV__.pet());
  chk('图标出现', j.shown === true);
  chk('摘要正好两行', (j.lines || []).length === 2, JSON.stringify(j.lines));
  chk('第一行是标题 (第十一回…)', (j.lines || [''])[0].includes('第十一回'), (j.lines || [''])[0]);
  chk('第二行是开头两句 (不是全文)', (j.lines || [''])[1].length <= 47,
      String((j.lines || [''])[1].length));
  chk('未看过 → 带 is-new (会转)', j.isNew === true);

  const geo = await page.evaluate(() => {
    const el = document.getElementById('rv-pet');
    const ico = el.querySelector('.rv-pet-ico');
    const sync = document.getElementById('rv-sync');
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    const ci = getComputedStyle(ico);
    const sr = sync ? sync.getBoundingClientRect() : null;
    return {
      w: r.width, h: r.height, right: innerWidth - r.right, bottom: innerHeight - r.bottom,
      opacity: +cs.opacity, color: cs.color,
      anim: ci.animationName, dur: ci.animationDuration, iter: ci.animationIterationCount,
      syncTop: (sr && !sync.hidden && sr.height > 0) ? sr.top : null,
      iconBottom: r.bottom,
      pe: cs.pointerEvents,
    };
  });
  chk('图标很小 (≤ 20px)', geo.w <= 20 && geo.h <= 20, `${geo.w}x${geo.h}`);
  chk('平时半透明 (≤ 0.55)', geo.opacity <= 0.55, String(geo.opacity));
  chk('贴着右下角', geo.right <= 14 && geo.bottom <= 40, `right=${geo.right} bottom=${geo.bottom}`);
  chk('在「掌阅同步」小字上方', geo.syncTop == null || geo.iconBottom <= geo.syncTop + 1,
      `iconBottom=${geo.iconBottom} syncTop=${geo.syncTop}`);
  chk('有更新 → 慢慢转 (rv-pet-turn / 9s / infinite)',
      geo.anim === 'rv-pet-turn' && geo.dur === '9s' && geo.iter === 'infinite',
      `${geo.anim} ${geo.dur} ${geo.iter}`);
  chk('可点击 (不吃 pointer-events: none)', geo.pe !== 'none', geo.pe);

  // 「掌阅同步」那行小字要先对一次云端才出来 —— 等它渲染后单独量一次"上不压字"。
  const stampShown = await page.waitForFunction(() => {
    const s = document.getElementById('rv-sync');
    return !!(s && !s.hidden && s.getBoundingClientRect().height > 0);
  }, null, { timeout: 8000 }).then(() => true).catch(() => false);
  const gap = await page.evaluate(() => {
    const el = document.getElementById('rv-pet');
    const s = document.getElementById('rv-sync');
    if (!s || s.hidden) return null;
    return Math.round(s.getBoundingClientRect().top - el.getBoundingClientRect().bottom);
  });
  chk('同步小字出来时: 与图标留出空隙 (>= 3px)', !stampShown || (gap != null && gap >= 3),
      stampShown ? `gap=${gap}px` : '这家书还没有云端同步记录 (小字不显示, 跳过)');

  // 悬停 → 两行摘要浮出
  const tipBefore = await page.$eval('#rv-pet-tip', (e) => +getComputedStyle(e).opacity);
  await page.hover('#rv-pet');
  await page.waitForTimeout(320);
  const tip = await page.evaluate(() => {
    const t = document.getElementById('rv-pet-tip');
    const r = t.getBoundingClientRect();
    const cs = getComputedStyle(t);
    const spans = Array.from(t.querySelectorAll('span')).map((s) => s.textContent);
    return { opacity: +cs.opacity, spans, w: r.width, h: r.height,
             bg: cs.backgroundColor, fg: cs.color, border: cs.borderTopWidth,
             inside: r.left >= -1 && r.top >= -1 && r.right <= innerWidth + 1,
             anim: getComputedStyle(document.querySelector('.rv-pet-ico')).animationPlayState };
  });
  chk('平时不显示悬停框', tipBefore < 0.2, String(tipBefore));
  chk('悬停后浮出', tip.opacity > 0.9, String(tip.opacity));
  chk('悬停框正好两行', tip.spans.length === 2, JSON.stringify(tip.spans));
  chk('悬停框内容 = 摘要', tip.spans.join('|') === j.lines.join('|'));
  chk('悬停框在视口内', tip.inside === true, `${Math.round(tip.w)}x${Math.round(tip.h)}`);
  chk('悬停框底色是不透明的面板色 (深度主题也读得清)',
      /^rgb\(/.test(tip.bg), tip.bg);
  chk('悬停框文字与底色不同', tip.fg !== tip.bg, `${tip.fg} on ${tip.bg}`);
  chk('悬停时暂停旋转 (不晃眼)', tip.anim === 'paused', tip.anim);

  // 点击 → 新标签页到书宠
  const [popup] = await Promise.all([
    ctx.waitForEvent('page', { timeout: 8000 }).catch(() => null),
    page.click('#rv-pet'),
  ]);
  chk('点击新开了一个标签页', !!popup, popup ? popup.url() : 'no popup');
  if (popup) {
    chk('新页地址是书宠分区', /\/pet\/?$/.test(new URL(popup.url()).pathname), popup.url());
    await popup.close();
  }
  await page.waitForTimeout(250);
  const after = await page.evaluate(() => {
    const el = document.getElementById('rv-pet');
    return {
      isNew: el.classList.contains('is-new'),
      anim: getComputedStyle(el.querySelector('.rv-pet-ico')).animationName,
      seen: localStorage.getItem('recto.pet.seen'),
      id: window.__RV__.pet().id,
    };
  });
  chk('点过就停转', after.isNew === false && after.anim === 'none', after.anim);
  chk('已读记在 localStorage', String(after.seen) === String(after.id), `${after.seen}/${after.id}`);

  // 刷新一次: 同一回不再转 (没有更新就不该有动效)
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => {
    const e = document.querySelector('#rv-pet');
    return !!(e && !e.hidden);
  }, null, { timeout: 20000 });
  await page.waitForTimeout(400);
  const again = await page.evaluate(() => window.__RV__.pet());
  chk('看过的这一回再打开不转', again.isNew === false);

  // ---- 手机 ----
  const ctx2 = await browser.newContext({
    viewport: { width: 390, height: 780 }, hasTouch: true, isMobile: true,
  });
  await ctx2.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
  const m = await ctx2.newPage();
  m.on('pageerror', (e) => errors.push('mobile: ' + String(e)));
  await m.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
  await m.waitForFunction(() => {
    const e = document.querySelector('#rv-pet');
    return !!(e && !e.hidden);
  }, null, { timeout: 20000 });
  await m.waitForTimeout(500);
  const mg = await m.evaluate(() => {
    const el = document.getElementById('rv-pet');
    const sync = document.getElementById('rv-sync');
    const r = el.getBoundingClientRect();
    const sr = sync ? sync.getBoundingClientRect() : null;
    return {
      inView: r.left >= 0 && r.top >= 0 && r.right <= innerWidth + 1 && r.bottom <= innerHeight + 1,
      iconBottom: r.bottom,
      syncTop: (sr && !sync.hidden && sr.height > 0) ? sr.top : null,
      overflow: document.documentElement.scrollWidth - innerWidth,
      w: r.width,
    };
  });
  chk('手机: 图标在视口内', mg.inView === true, `${mg.w}px`);
  chk('手机: 不压在同步小字上', mg.syncTop == null || mg.iconBottom <= mg.syncTop + 1,
      `iconBottom=${Math.round(mg.iconBottom)} syncTop=${mg.syncTop && Math.round(mg.syncTop)}`);
  chk('手机: 页面不横向溢出', mg.overflow <= 0, String(mg.overflow));

  chk('0 页面报错', errors.length === 0, errors.join(' | '));

  // 收尾: 把进度写回 (探针会真存一次)
  await writeProgress(page, progress0);
  await ctx.close();
  await ctx2.close();
} finally {
  await browser.close();
}

const fail = out.filter((l) => l.startsWith('FAIL')).length;
console.log(out.join('\n'));
console.log(`\n${out.length - fail} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
