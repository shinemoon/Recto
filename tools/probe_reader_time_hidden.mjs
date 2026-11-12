// 探针: 网页阅读端的阅读时长是否只在页面处于前台时累计
//
// 用户的验收口径: "如果网页没有被激活 (例如用户切换到了别的网页), 不要累计阅读时间"
//
// 做法 (真浏览器, 三次采样):
//   1) 页面在前台读 70 秒        → 今日本地分钟应 +1 ~ +2
//   2) 切到另一个标签页 90 秒    → 应**不再增长** (visibilityState=hidden)
//   3) 切回前台再读 70 秒        → 恢复增长
//
// 用法: RECTO_BASE=http://127.0.0.1:5015 node tools/probe_reader_time_hidden.mjs
//       RV_TIME_BOOK=<bookKey> 指定书 (默认取第一本)
//
// 卫生: 本探针只读时长, 不翻页不写进度; 但打开书仍可能触发自动 pull, 所以
//       开头/结尾都走同一份进度快照 (_env 里的 readProgress / writeProgress 之外这里
//       只用 GET, 不改 rprogress)。真正的数据改动由 tools/restore_time_row.py 收尾。
import { chromium, cookie } from './_env.mjs';
import { execFileSync } from 'child_process';
import path from 'path';
import { fileURLToPath } from 'url';

const HERE = path.dirname(fileURLToPath(import.meta.url));

// 探针会在阅读器里待 3-4 分钟 → 前端每 30 秒 beat 一次, 服务端会给这本书真加时长。
// 因此开头快照、退出时还原 (rtime 行 + 新增的 rsession), 见 tools/rtime_snapshot.py。
let snapBook = '';
function timeSnap(cmd, key) {
  try {
    const out = execFileSync('python3', [path.join(HERE, 'rtime_snapshot.py'), cmd,
                                         '--book', key], { encoding: 'utf8', cwd: HERE });
    console.log('  · 时长快照 ' + cmd + ': ' + out.trim());
  } catch (e) {
    console.log('  ! 时长快照 ' + cmd + ' 失败: ' + String(e).slice(0, 160));
  }
}
process.on('exit', () => { if (snapBook) timeSnap('restore', snapBook); });

const BASE = process.env.RECTO_BASE || 'http://127.0.0.1:5015';
const KEY_ENV = process.env.RV_TIME_BOOK || '';
const VISIBLE_SEC = +(process.env.RV_VISIBLE_SEC || 70);
const HIDDEN_SEC = +(process.env.RV_HIDDEN_SEC || 90);

const errors = [];
let pass = 0, fail = 0;
function check(ok, name, extra) {
  if (ok) { pass++; console.log('  [PASS] ' + name + (extra !== undefined ? '  ' + JSON.stringify(extra) : '')); }
  else { fail++; console.log('  [FAIL] ' + name + '  ' + JSON.stringify(extra)); errors.push(name); }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function api(page, path, opts = {}) {
  return await page.evaluate(async (arg) => {
    const r = await fetch(arg.p, {
      method: arg.o.method || 'GET',
      credentials: 'same-origin',
    });
    if (!r.ok) return { _status: r.status };
    return await r.json();
  }, { p: path, o: opts });
}

function today() {
  const d = new Date();
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' +
    String(d.getDate()).padStart(2, '0');
}

async function localMinutes(page, key) {
  const st = await api(page, '/read/api/book/' + encodeURIComponent(key));
  const days = (st && st.stats && st.stats.days) || [];
  const row = days.find((d) => d.day === today());
  return { today: row ? row.local : 0, total: (st && st.stats && st.stats.total) || 0,
           title: st && st.title, clock: new Date().toTimeString().slice(0, 8) };
}

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const token = cookie();            // make_cookie.py 打印的是 cookie 值 (不是 name=value)
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: new URL(BASE).hostname,
                        path: '/' }]);
const page = await ctx.newPage();
const jsErr = [];
page.on('pageerror', (e) => jsErr.push(String(e)));
page.on('console', (m) => { if (m.type() === 'error') jsErr.push('console: ' + m.text()); });

// 1. 找书
await page.goto(BASE + '/read/', { waitUntil: 'domcontentloaded' });
check(!/\/login/.test(page.url()), '会话 cookie 有效 (没被弹到登录页)', page.url());
const books = await api(page, '/read/api/books');
const list = (books && books.books) || [];
let key = KEY_ENV;
if (!key) {
  const pick = list.find((b) => /刺杀骑士团长/.test(b.title || '')) || list[0];
  key = pick && pick.key;
}
check(!!key, '拿到书 key', key);
if (!key) { await browser.close(); process.exit(2); }

snapBook = key;
timeSnap('save', key);

// 2. 打开阅读页
await page.goto(BASE + '/read/b/' + encodeURIComponent(key), { waitUntil: 'domcontentloaded' });
await page.waitForFunction('window.__RV__ && window.__RV__.page && document.querySelector("#rv-pageind, .rv-view")', null, { timeout: 20000 });
await page.waitForTimeout(1500);

check(await page.evaluate('document.visibilityState') === 'visible', '开局页面在前台');

const m0 = await localMinutes(page, key);
console.log('  · 书名 = ' + m0.title + ' / 今日本地分钟 = ' + m0.today);

// --- 阶段 A: 前台 70 秒 ---
console.log('  · 阶段 A: 前台读 ' + VISIBLE_SEC + ' 秒 ...');
await sleep(VISIBLE_SEC * 1000);
const mA = await localMinutes(page, key);
const dA = mA.today - m0.today;
check(dA >= 1, '前台阅读累计分钟 (期望 +1~2)', { before: m0.today, after: mA.today, delta: dA });

// --- 阶段 B: 模拟"切到别的网页" 90 秒 ---
// 说明: 无头 Chromium (headless shell) 里所有页面都报 visible, bringToFront 不会
// 触发 visibilitychange。所以这里按浏览器标准语义直接改 document.visibilityState
// 并派发 visibilitychange —— 与真实切标签页对页面脚本的可见效果一致。
const hideClock = new Date().toTimeString().slice(0, 8);
await page.evaluate(() => {
  Object.defineProperty(document, 'visibilityState', { get: () => 'hidden', configurable: true });
  Object.defineProperty(document, 'hidden', { get: () => true, configurable: true });
  document.dispatchEvent(new Event('visibilitychange'));
});
await page.waitForTimeout(500);
check(await page.evaluate('document.visibilityState') === 'hidden',
      '切走后 visibilityState = hidden (浏览器标准语义)', 'hidden');
// 切走瞬间会补一次 beacon end —— 它会把"上一次心跳到切走"这一小段算进去,
// 属于分钟粒度的边界滑点 (<=1 分钟), 单独记录, 不算隐藏期累积。
await sleep(6000);
const mB1 = await localMinutes(page, key);
console.log('  · 阶段 B: 后台挂 ' + HIDDEN_SEC + ' 秒 (切走时刻 ' + hideClock + ', 6 秒后基线 ' +
            mB1.today + ' 分钟)...');
await sleep(Math.max(0, HIDDEN_SEC - 6) * 1000);
const mB2 = await localMinutes(page, key);
check(mB2.today === mB1.today, '后台期间不累计 (切走后 6s -> ' + HIDDEN_SEC + 's 应 delta = 0)',
      { base6s: mB1.today, after: mB2.today, delta: mB2.today - mB1.today });
const hideSlop = mB1.today - mA.today;
check(hideSlop <= 1, '切走瞬间的补记不超过 1 分钟 (分钟粒度边界)', { delta: hideSlop, at: hideClock });
const mA2 = mB2;

// --- 阶段 C: 切回前台再读 70 秒 ---
await page.evaluate(() => {
  Object.defineProperty(document, 'visibilityState', { get: () => 'visible', configurable: true });
  Object.defineProperty(document, 'hidden', { get: () => false, configurable: true });
  document.dispatchEvent(new Event('visibilitychange'));
});
await page.waitForTimeout(500);
check(await page.evaluate('document.visibilityState') === 'visible', '切回后 visibilityState = visible');
console.log('  · 阶段 C: 回到前台再读 ' + VISIBLE_SEC + ' 秒 ...');
await sleep(VISIBLE_SEC * 1000);
const mC = await localMinutes(page, key);
const dC = mC.today - mA2.today;
check(dC >= 1, '回到前台恢复累计 (期望 +1~2)', { before: mA2.today, after: mC.today, delta: dC });
check(dC <= 3, '恢复后没有把后台那段补进来 (delta <= 3)', dC);

check(jsErr.length === 0, '无 JS 报错', jsErr.slice(0, 3));

console.log('\n== 结果: ' + pass + ' passed, ' + fail + ' failed ==');
if (fail) console.log('   失败项: ' + errors.join(' / '));
await browser.close();
process.exit(fail ? 1 : 0);
