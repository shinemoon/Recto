/* 端到端: 旧会话不许把进度写回去 + 本地未推送时交给读者判定 */
import { chromium } from '/vol2/1001/Recto/tools/_env.mjs';

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || 'da7e470fa0c2c4b8';
const { execFileSync } = await import('child_process');
const token = execFileSync('python3', ['/vol2/1001/Recto/tools/make_cookie.py'],
  { encoding: 'utf8' }).trim();

async function readProgress(pg) {
  return await pg.evaluate(async (k) => {
    const r = await fetch('/read/api/book/' + k, { headers: { accept: 'application/json' } });
    const j = await r.json();
    return (j && j.progress) || null;
  }, KEY);
}
async function writeProgress(pg, pr) {
  return await pg.evaluate(async ([k, p]) => {
    const r = await fetch('/read/api/book/' + k + '/progress', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ chapter: p.chapter, offset: p.offset, jump: true, user: 1 })
    });
    return r.status;
  }, [KEY, pr]);
}

const out = [];
const chk = (n, c, e = '') => out.push(`${c ? 'ok  ' : 'FAIL'} ${n}${e ? ' — ' + e : ''}`);

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
const errs = [];
page.on('pageerror', e => errs.push('pageerror: ' + String(e)));

await page.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('.rv-page p', { timeout: 20000 });
await page.waitForTimeout(1000);
const snap = await readProgress(page);

// --- 1) 旧会话 (从没打开过这本书) 往回写 1.2% → 必须被挡
const before = await readProgress(page);
const target = Math.max(0, (before.offset || 0) - 12000);
const res = await page.evaluate(async ([k, ch, off]) => {
  const r = await fetch('/read/api/book/' + k + '/progress', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ chapter: ch, offset: off, sid: 'oldtab' + Math.random(), user: 1 })
  });
  return await r.json();
}, [KEY, before.chapter, target]);
chk('旧会话回退被挡 (stale)', res.stale === true, JSON.stringify(res).slice(0, 160));
const after = await readProgress(page);
chk('库里进度没被写回', after.offset === before.offset,
    `${before.offset} -> ${after.offset}`);

// --- 2) 同一个会话往回翻 → 放行 (读者自己往回翻是正常的)
const back = await page.evaluate(async ([k, ch, off]) => {
  const j = await (await fetch('/read/api/book/' + k, { headers: { accept: 'application/json' } })).json();
  const r = await fetch('/read/api/book/' + k + '/progress', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ chapter: ch, offset: off, sid: j.progress.sid, user: 1 })
  });
  return await r.json();
}, [KEY, before.chapter, target]);
chk('同一会话往回翻放行', !back.stale && Math.abs((back.percent || 0) - 0) >= 0,
    JSON.stringify(back).slice(0, 120));
await writeProgress(page, snap);              // 复原

// --- 3) 本地靠前但没推上去 → 交给读者 (拦 /sync 造这个状态)
const page2 = await ctx.newPage();
page2.on('pageerror', e => errs.push('pageerror2: ' + String(e)));
await page2.route('**/read/api/book/' + KEY + '/sync**', route => route.fulfill({
  status: 200, contentType: 'application/json',
  body: JSON.stringify({
    ok: true, need_choice: true, choice_kind: 'local-unpushed',
    local_pct: 0.6012, cloud_pct: 0.3140, bytes: 1117551, skipped: 'local-ahead',
    state: { settings: { auto: true, pull_on_open: true, stamp: true },
             state: { state: 'dirty', last_push_pct: 0.3140 } }
  })
}));
await page2.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
await page2.waitForSelector('.rv-page p', { timeout: 20000 });
await page2.waitForTimeout(1500);
const bar = await page2.evaluate(() => {
  const el = document.getElementById('rv-choice');
  const b = el && el.querySelector('[data-c="local"]');
  const c = el && el.querySelector('[data-c="cloud"]');
  return {
    shown: !!(el && !el.hidden),
    txt: (document.getElementById('rv-choice-txt') || {}).textContent || '',
    localBtn: b ? b.textContent : '', cloudBtn: c ? c.textContent : '',
    localPrimary: b ? b.className : '', cloudPrimary: c ? c.className : ''
  };
});
chk('提示条出现', bar.shown, JSON.stringify(bar).slice(0, 200));
chk('文案说本地读到、云端还停在', /这里读到 60\.1%/.test(bar.txt) && /云端还停在 31\.4%/.test(bar.txt), bar.txt);
chk('按钮: 用本地覆盖云端', bar.localBtn === '用本地覆盖云端', bar.localBtn);
chk('按钮: 采用云端', bar.cloudBtn === '采用云端', bar.cloudBtn);
chk('本地建议是主按钮', bar.localPrimary === 'primary' && bar.cloudPrimary === '', JSON.stringify(bar));
chk('0 前端报错', errs.length === 0, errs.join(' | '));

// 写回之后不能再调 readProgress (探针卫生: 收尾必须是最后一个快照相关动作),
// 所以这一处用行内 fetch 复核。
const nowPct = await page.evaluate(async (k) => {
  const j = await (await fetch('/read/api/book/' + k, { headers: { accept: 'application/json' } })).json();
  return (j && j.progress && j.progress.percent) || 0;
}, KEY);
chk('提示不改数据 (本地进度没动)', Math.abs(nowPct - 0.6012) > 0.01, String(nowPct));

console.log(out.join('\n'));
await browser.close();
process.exit(out.some(l => l.startsWith('FAIL')) ? 1 : 0);
