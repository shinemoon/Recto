/* 右下角同步小字: 推送进行中不许刷「同步中」——只保留上一次同步的信息。
 *
 * 需求 (2026-11-06): 「阅读时『掌阅同步中...』这种显示就不要了, 直接保持在上次同步的信息即可」
 *
 *  1. 基线: 小字已是某个「有结论」的形态 (已同步 / 更靠前 / 已按本地覆写 / 尚未同步)
 *  2. 滚动触发一次保存 → 服务端进 dirty/pushing 的那几秒里逐帧采样小字
 *  3. 全程不出现「同步」字样前带「…」的过渡文案, 且文本**保持不变**
 *  4. 推送结束后仍是合法形态
 *
 * 跑法: node tools/probe_reader_sync_quiet.mjs   (RV_BASE / RV_KEY 可覆盖)
 * 探针会真写进度 → 开头快照, 收尾写回 (tests/test_probe_hygiene.py 钉这条)。
 */
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || 'a6f63af571f05bb4';
const token = cookie();

const out = [];
const chk = (name, cond, extra = '') =>
  out.push(`${cond ? 'ok  ' : 'FAIL'} ${name}${extra ? ' — ' + extra : ''}`);

const TEXT_OK = /^掌阅 (\d\d:\d\d 已同步 [\d.]+%|更靠前 [\d.]+%|已按本地覆写 [\d.]+%|尚未同步|同步失败)$/;

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

// 探针会真写进度 (滚动 -> 保存 -> 推云端) —— 开头快照, 收尾写回。
// tests/test_probe_hygiene.py 就是钉这条的: 探针不许留下自己的阅读位置。
async function readProgress(pg) {
  return await pg.evaluate(async (k) => {
    const j = await fetch('/read/api/book/' + k, { headers: { accept: 'application/json' } })
      .then((x) => x.json());
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

// 先落一个同源页面再取快照 (about:blank 上相对 URL 无法解析)
await page.goto(`${BASE}/#library`, { waitUntil: 'domcontentloaded' });
const progress0 = await readProgress(page);

await page.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
// 等小字出现 (打开书会先对一次云端)
// 打开书会先向云端 /sync 对一次 (网络慢时要几秒), 40s 兜住
await page.waitForFunction(
  () => { const el = document.getElementById('rv-sync'); return el && !el.hidden && el.textContent; },
  null, { timeout: 40000, polling: 250 });
await page.waitForTimeout(1200);

const stamp = () => page.evaluate(() => {
  const el = document.getElementById('rv-sync');
  return el ? { hidden: el.hidden, text: el.textContent } : null;
});
const svcState = () => page.evaluate(async (k) => {
  const j = await fetch('/read/api/book/' + k + '/sync-state', { headers: { accept: 'application/json' } })
    .then((x) => x.json()).catch(() => null);
  const s = (j && ((j.state && j.state.state) || j.state)) || null;
  return typeof s === 'string' ? s : null;
}, KEY);

const base = await stamp();
chk('小字可见', base && base.hidden === false, JSON.stringify(base));
chk('基线是不带过渡词的形态', !!base && TEXT_OK.test(base.text), base && base.text);
chk('基线不含「同步中」', !!base && !/同步中/.test(base.text), base && base.text);

// 用滚动把进度推上去 → 前端保存 → 服务端进 dirty / pushing
const seenStates = new Set();
const samples = [];
for (let i = 0; i < 26; i++) {
  if (i < 8) await page.mouse.wheel(0, 260);
  const st = await svcState();
  const sp = await stamp();
  if (st) seenStates.add(st);
  if (sp) samples.push({ st, text: sp.text, hidden: sp.hidden });
  await page.waitForTimeout(220);
}

const inFlight = samples.filter((s) => s.st === 'dirty' || s.st === 'pushing');
chk('采样覆盖到推送进行中', inFlight.length > 0,
  'states=' + [...seenStates].join(','));
chk('进行中那几帧完全没出现「同步中」',
  inFlight.every((s) => !/同步中/.test(s.text) && !/…$/.test(s.text)),
  JSON.stringify(inFlight.map((s) => s.st + ':' + s.text).slice(0, 8)));
chk('进行中文本保持不变 (不闪字)',
  inFlight.length > 0 && inFlight.every((s) => s.text === base.text),
  JSON.stringify([base.text, ...inFlight.map((s) => s.text)]));

// 等节流窗口过去, 推送落地 → 文本回到「已同步 HH:MM x%」
await page.waitForTimeout(16000);
const after = await stamp();
chk('推送结束后仍是「已同步」形态', !!after && TEXT_OK.test(after.text), after && after.text);
chk('结束后时间戳/百分比更新 (或有结论)', !!after && !/同步中/.test(after.text), after && after.text);
chk('无 JS 报错', errors.length === 0, errors.join(' | '));

// 写回原来的阅读位置
const code = await writeProgress(page, progress0);
// 用行内 fetch 复核 (不调 readProgress: 收尾那次写必须是最后一步)
const now = await page.evaluate(async (k) => {
  const j = await fetch('/read/api/book/' + k).then((x) => x.json());
  return (j && j.progress) || null;
}, KEY);
chk('探针把阅读进度写回原处', !!now && !!progress0
  && now.chapter === progress0.chapter && Math.abs(now.offset - progress0.offset) <= 2,
  JSON.stringify({ code, before: progress0, after: now }));

await ctx.close();
await browser.close();

console.log(out.join('\n'));
const bad = out.filter((x) => x.startsWith('FAIL')).length;
console.log(bad ? `\n${bad} FAIL` : `\nALL PASS (${out.length})`);
process.exit(bad ? 1 : 0);
