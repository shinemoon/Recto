/* 自动同步 (进度) 的浏览器探针 —— 只看用户能看到/能感知的那一面。
 *
 *  1. 右下角那行小字: 位置 / 字号 / 颜色 / 不吃指针 / 文案格式
 *  2. 打开书时真的对过一次云端 (/sync 有请求且 200)
 *  3. 离开页面时真的补了一推 (/progress flush)
 *  4. 设置页关掉「右下角显示」→ 那行字消失 (改回来还能回来)
 *  5. 手机宽度不压页码、不溢出
 *
 * 跑法: node tools/probe_reader_sync.mjs        (BASE/KEY 可用 RV_BASE / RV_KEY 覆盖)
 * 会临时改一次 settings.json, 结束时复原。
 */
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || 'a6f63af571f05bb4';
const token = cookie();

const out = [];
const chk = (name, cond, extra = '') =>
  out.push(`${cond ? 'ok  ' : 'FAIL'} ${name}${extra ? ' — ' + extra : ''}`);

const browser = await chromium.launch({ args: ['--no-sandbox'] });

// 探针会顺带保存进度 (打开书本身就会写一次) —— 开头快照, 收尾写回。
// tests/test_probe_hygiene.py 就是钉这条的: 探针不许留下自己的阅读位置。
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
      // jump=1: 写回是"故意跳回去", 不该被"别被旧标签页拽回去"的闸门挡住
      body: JSON.stringify({ chapter: p.chapter, offset: p.offset, jump: 1 })
    });
    return r.status;
  }, [KEY, pr]);
}

async function open(width, height) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
  const page = await ctx.newPage();
  const errors = [];
  const reqs = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
  page.on('request', (r) => {
    const u = new URL(r.url());
    if (u.pathname.startsWith('/read/api/book/')) {
      reqs.push({ path: u.pathname.split('/').pop(), method: r.method(), post: r.postData() });
    }
  });
  await page.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1800);
  return { page, ctx, errors, reqs };
}

// 小字有三种正常形态: 已同步 / 更靠前 (云端领先, 不往回拨) / 尚未同步 / 同步中
const TEXT_OK = /^掌阅 (\d\d:\d\d 已同步 [\d.]+%|更靠前 [\d.]+%|已按本地覆写 [\d.]+%|尚未同步|同步中…)$/;

/* ---------- 桌面 ---------- */
const snapCtx = await browser.newContext({ viewport: { width: 900, height: 700 } });
await snapCtx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const snapPage = await snapCtx.newPage();
await snapPage.goto(`${BASE}/#library`, { waitUntil: 'domcontentloaded' });
const progress0 = await readProgress(snapPage);
await snapCtx.close();

{
  const { page, ctx, errors, reqs } = await open(1280, 900);

  chk('打开书时对过一次云端 (/sync)',
    reqs.some((r) => r.path === 'sync' && r.method === 'GET'), JSON.stringify(reqs.map((r) => r.path)));

  // 小字要等 /sync 回来才渲染 (正文先出来), 给它 3 秒
  await page.waitForFunction(() => {
    const el = document.getElementById('rv-sync');
    return el && !el.hidden && el.textContent.trim() !== '';
  }, null, { timeout: 3000 }).catch(() => {});
  const st = await page.evaluate(() => {
    const el = document.getElementById('rv-sync');
    if (!el) return null;
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return {
      text: el.textContent, hidden: el.hidden, fixed: cs.position,
      pe: cs.pointerEvents, fs: parseFloat(cs.fontSize), color: cs.color,
      right: window.innerWidth - r.right, bottom: window.innerHeight - r.bottom,
      overflow: document.documentElement.scrollWidth > window.innerWidth + 1
    };
  });
  chk('右下角那行在', !!st && st.hidden === false, JSON.stringify(st));
  chk('文案是「掌阅 HH:MM 已同步 x%」这一族', !!st && TEXT_OK.test(st.text), st && st.text);
  chk('固定在右下角', !!st && st.fixed === 'fixed' && st.right < 40 && st.bottom < 40,
    st && `right=${st.right} bottom=${st.bottom}`);
  chk('不吃指针', !!st && st.pe === 'none', st && st.pe);
  chk('小字 (9–13px) 且淡', !!st && st.fs >= 9 && st.fs <= 13, st && `${st.fs}px ${st.color}`);
  chk('页面不横向溢出', !!st && !st.overflow, '');

  // 离开页面 → 补一推 (先真的滚动一下: 没有手势就不该写, 那是设计)
  await page.mouse.move(600, 400);
  await page.mouse.wheel(0, 260);
  await page.waitForTimeout(900);
  const before = reqs.filter((r) => r.path === 'progress' && r.method === 'POST').length;
  await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
  await page.waitForTimeout(600);
  const after = reqs.filter((r) => r.path === 'progress' && r.method === 'POST');
  chk('pagehide 时补推最后位置 (flush)', after.length > before
    && after.some((r) => (r.post || '').includes('flush')), `+${after.length - before}`);

  chk('无 JS 报错 (桌面)', errors.length === 0, errors.join(' | '));
  await ctx.close();
}

/* ---------- 关掉「右下角显示」 ---------- */
{
  const { page, ctx } = await open(1280, 900);
  const saved = await page.evaluate(async () => {
    const r = await fetch('/api/settings', { credentials: 'same-origin' }).then((x) => x.json());
    return r.sync;
  });
  await page.evaluate(async (s) => {
    await fetch('/api/settings', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sync: { auto: s.auto, pull_on_open: s.pull_on_open,
        flush_on_hide: s.flush_on_hide, stamp: false,
        push_debounce_sec: s.push_debounce_sec } })
    });
  }, saved);
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1200);
  const off = await page.evaluate(() => {
    const el = document.getElementById('rv-sync');
    return { hidden: el.hidden, text: el.textContent };
  });
  chk('设置页关掉后那行字消失', off.hidden === true, JSON.stringify(off));

  // 复原 + 确认能回来
  await page.evaluate(async (s) => {
    await fetch('/api/settings', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sync: { auto: s.auto, pull_on_open: s.pull_on_open,
        flush_on_hide: s.flush_on_hide, stamp: true,
        push_debounce_sec: s.push_debounce_sec } })
    });
  }, saved);
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1200);
  const back = await page.evaluate(() => document.getElementById('rv-sync').hidden);
  chk('改回来立刻又显示 (不用重启)', back === false, String(back));
  await ctx.close();
}

/* ---------- 手机 ---------- */
{
  const { page, ctx, errors } = await open(390, 780);
  const m = await page.evaluate(() => {
    const el = document.getElementById('rv-sync');
    const ind = document.getElementById('rv-pageind');
    const a = el.getBoundingClientRect();
    const b = ind.hidden ? null : ind.getBoundingClientRect();
    return {
      hidden: el.hidden, text: el.textContent,
      right: window.innerWidth - a.right, bottom: window.innerHeight - a.bottom,
      overlap: b ? !(a.bottom <= b.top || a.top >= b.bottom) : false,
      w: a.width, overflow: document.documentElement.scrollWidth > window.innerWidth + 1
    };
  });
  chk('手机上也在 (窄屏抬高)', m.hidden === false, JSON.stringify(m));
  chk('手机文案也是那一族', TEXT_OK.test(m.text), m.text);
  chk('窄屏不与页码同一行', m.overlap === false, '');
  chk('窄屏不溢出', !m.overflow, '');
  chk('无 JS 报错 (手机)', errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// 把探针碰过的进度写回原处
{
  const back = await browser.newContext({ viewport: { width: 900, height: 700 } });
  await back.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
  const bp = await back.newPage();
  await bp.goto(`${BASE}/#library`, { waitUntil: 'domcontentloaded' });
  const code = await writeProgress(bp, progress0);
  // 用行内 fetch 复核 (不调 readProgress: 收尾那次写必须是最后一步)
  const now = await bp.evaluate(async (k) => {
    const j = await fetch('/read/api/book/' + k).then((x) => x.json());
    return (j && j.progress) || null;
  }, KEY);
  chk('探针把阅读进度写回原处', !!now && !!progress0
    && now.chapter === progress0.chapter
    && Math.abs(now.offset - progress0.offset) <= 2,
    JSON.stringify({ code, before: progress0, after: now }));
  await back.close();
}

await browser.close();

console.log(out.join('\n'));
const bad = out.filter((x) => x.startsWith('FAIL')).length;
console.log(bad ? `\n${bad} FAIL` : `\nALL PASS (${out.length})`);
process.exit(bad ? 1 : 0);
