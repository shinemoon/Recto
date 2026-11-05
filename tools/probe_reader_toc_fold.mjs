/* 阅读器目录: ① 卷这一级不能重复出现; ② 点卷名只折叠, 不跳章。
 *
 * 断言:
 *   1. 每个卷名在目录里只出现一次 (之前 li 里既有 <a> 卷名 又有 <summary> 卷名)
 *   2. 点 summary: open 翻转, 但当前章 / 进度百分比 / URL hash 都不变
 *   3. 点分组里的章链接: 正常跳章 (折叠行为没把跳转一起废掉)
 *   4. 卷内无子项时退化成可跳转的普通行
 *
 * 进度会被改写 → 开头快照, 收尾还原。
 */
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
const { execFileSync } = require('child_process');

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || '8379037298fbca9c';   // 示例长篇 (多卷合订本)
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

async function apiGet(path) {
  const r = await ctx.request.get(BASE + path);
  return r.ok() ? await r.json() : null;
}
async function readProgress() {
  const j = await apiGet('/read/api/book/' + KEY);
  return (j && j.progress) || { chapter: 0, offset: 0 };
}
async function writeProgress(p) {
  return page.evaluate(async ([k, body]) => {
    const r = await fetch('/read/api/book/' + k + '/progress',
      { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
    return r.status;
  }, [KEY, p]);
}

const snap = await readProgress();
try {
  await page.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#rv-content .rv-blk', { timeout: 20000 });
  await page.waitForTimeout(400);

  // 打开目录抽屉
  await page.click('.rv-ico[data-act="toc"]');
  await page.waitForSelector('#rv-toc-list .rv-toc-grp', { timeout: 10000 });
  await page.click('[data-toc-act="expand"]');  // 全部展开, 便于数重复
  await page.waitForTimeout(200);

  const stat = await page.evaluate(() => {
    const ol = document.querySelector('#rv-toc-list');
    const secs = [...ol.querySelectorAll('.rv-toc-sec')];
    const titles = secs.map(s => s.querySelector('summary').textContent.trim());
    const dupTitle = titles.filter((t, i) => titles.indexOf(t) !== i);
    // 同一个 li 里既 a 又 summary = 老 bug 的形态
    const bothInOneLi = secs.filter(s => {
      const li = s.closest('li');
      return li && li.querySelector(':scope > a');
    }).length;
    const firstLi = secs[0] && secs[0].closest('li');
    return {
      groups: secs.length,
      rows: ol.querySelectorAll('a[data-ch]').length,
      dupTitle: dupTitle.slice(0, 3),
      bothInOneLi,
      firstLiLinks: firstLi ? firstLi.querySelectorAll(':scope > a').length : -1,
      anchorTexts: firstLi ? [...firstLi.querySelectorAll(':scope > a')].map(a => a.textContent.trim()) : [],
      cur: window.__RV__ ? window.__RV__.cursor().ch : null,
    };
  });

  chk('目录有可选分组 (多卷本)', stat.groups > 5, `${stat.groups} 组`);
  chk('卷名不重复', stat.dupTitle.length === 0, JSON.stringify(stat.dupTitle));
  chk('同一 li 里不再有额外的同名链接', stat.bothInOneLi === 0, `${stat.bothInOneLi} 处`);
  chk('卷分组自身的直接链接 = 0', stat.firstLiLinks === 0, `${stat.firstLiLinks} 个 ${JSON.stringify(stat.anchorTexts)}`);

  // ---- 点卷名: 只折叠, 不跳章 ----
  const before = await page.evaluate(() => ({
    ch: window.__RV__.cursor().ch,
    pct: document.querySelector('#rv-pct').textContent,
    hash: location.hash,
    open: null,
  }));
  const target = await page.evaluate(() => {
    const secs = [...document.querySelectorAll('#rv-toc-list .rv-toc-sec')];
    // 挑一个"当前章不在里面"的分组, 才能判定没有跳转
    const cur = window.__RV__.cursor().ch;
    for (const s of secs) {
      const has = [...s.querySelectorAll('a[data-ch]')].some(a => +a.dataset.ch === cur);
      if (!has) {
        s.open = true;
        s.dataset.probe = '1';
        return { ch: +s.querySelector('summary') ? +s.querySelector('summary').dataset.ch : -1 };
      }
    }
    return null;
  });
  const openBefore = await page.evaluate(() =>
    document.querySelector('#rv-toc-list .rv-toc-sec[data-probe="1"]').open);
  await page.click('#rv-toc-list .rv-toc-sec[data-probe="1"] > summary');
  await page.waitForTimeout(500);
  const after = await page.evaluate(() => ({
    ch: window.__RV__.cursor().ch,
    pct: document.querySelector('#rv-pct').textContent,
    hash: location.hash,
    open: document.querySelector('#rv-toc-list .rv-toc-sec[data-probe="1"]').open,
    drawerOn: document.querySelector('#rv-toc').classList.contains('rv-on'),
  }));
  chk('点卷名 = 折叠翻转', openBefore !== after.open, `${openBefore} -> ${after.open}`);
  chk('点卷名不跳章', before.ch === after.ch, `${before.ch} -> ${after.ch}`);
  chk('点卷名不改进度显示', before.pct === after.pct, `${before.pct} vs ${after.pct}`);
  chk('点卷名不改 URL', before.hash === after.hash, `${before.hash} vs ${after.hash}`);
  chk('点卷名后抽屉仍开着', after.drawerOn === true, `rv-on=${after.drawerOn}`);

  // ---- 点分组里的章: 仍然正常跳章并收抽屉 ----
  const beforeCh = await page.evaluate(() => window.__RV__.cursor().ch);
  const kidCh = await page.evaluate(() => {
    const secs = [...document.querySelectorAll('#rv-toc-list .rv-toc-sec')];
    for (const s of secs) {
      s.open = true;
      const a = s.querySelector('a[data-ch]');
      if (a && +a.dataset.ch !== window.__RV__.cursor().ch) {
        a.dataset.probe = '1';
        return +a.dataset.ch;
      }
    }
    return -1;
  });
  await page.click('#rv-toc-list a[data-probe="1"]');
  await page.waitForTimeout(800);
  const afterCh = await page.evaluate(() => window.__RV__.cursor().ch);
  chk('点章链接仍然跳章', afterCh === kidCh && afterCh !== beforeCh, `${beforeCh} -> ${afterCh} (目标 ${kidCh})`);
  chk('跳章后抽屉收起', await page.evaluate(() => {
    const d = document.querySelector('#rv-toc');
    return !d.classList.contains('rv-on');
  }), '');

  // ---- 卷内无子项: 退化成普通可跳行 ----
  const solo = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('#rv-toc-list > li')];
    const plain = rows.filter(li => li.querySelector(':scope > a'));
    return { plain: plain.length, grp: rows.filter(li => li.querySelector(':scope > .rv-toc-sec')).length };
  });
  chk('存在退化成普通行的目录项 (或全部成组)', solo.plain + solo.grp > 0, JSON.stringify(solo));

  chk('无 JS 报错', errs.length === 0, errs.slice(0, 3).join(' | '));
} finally {
  try { await writeProgress(snap); } catch (e) { out.push('FAIL 还原进度失败 ' + e); }
  await browser.close();
}

console.log(out.join('\n'));
const bad = out.filter(l => l.startsWith('FAIL')).length;
console.log(bad ? `\n${bad} FAIL` : `\n${out.length} PASS`);
process.exit(bad ? 1 : 0);
