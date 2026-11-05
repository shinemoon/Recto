/* 装饰分隔符 (◎ / ** / ——) 的视觉规格探针。
 *
 * 原问题: 示例短篇 把分隔符挂在书内 `.biaoti` 上 (1.7em + bold)，我们照抄字号，
 * 19px 正文旁边一个 32px 的实心粗圆 —— 用户报「◎ 显示明显过大」。
 *
 * 断言:
 *   ① 分隔符字号 <= 正文 (0.9em)，字重 400 (书内 bold 不再泄漏)
 *   ② 水平居中 (墨迹中心 == 版心中线，±2px)：text-align-last 也要 center，
 *      否则单字符段落作为"最后一行"仍会左对齐
 *   ③ 上下留白合理 (前后各 >= 8px)，块高接近一行 (不再撑成两行多高)
 *   ④ 翻页模式同样成立
 *   ⑤ 0 JS 报错
 */
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
const { execFileSync } = require('child_process');

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || 'a6f63af571f05bb4';   // 示例短篇
const CH = process.env.RV_CH || '5';                    // 含 ◎ 的一章
const token = execFileSync('python3', [new URL('./make_cookie.py', import.meta.url).pathname],
  { encoding: 'utf8' }).trim();

let pass = 0, fail = 0;
const chk = (name, cond, extra = '') => {
  console.log(`${cond ? 'ok  ' : 'FAIL'} ${name}${extra ? ' — ' + extra : ''}`);
  cond ? pass++ : fail++;
};

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
const errs = [];
page.on('pageerror', e => errs.push(String(e)));
page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });

const measure = () => page.evaluate(() => {
  const body = document.querySelector('.rv-ch .rv-blk p');
  const sep = document.querySelector('.rv-ch .rv-sep');
  if (!body || !sep) return null;
  const cs = getComputedStyle(sep), bs = getComputedStyle(body);
  const bRect = body.getBoundingClientRect(), sRect = sep.getBoundingClientRect();
  const rg = document.createRange();
  rg.selectNodeContents(sep);
  const ink = rg.getBoundingClientRect();
  const prev = sep.closest('.rv-blk').previousElementSibling;
  const next = sep.closest('.rv-blk').nextElementSibling;
  return {
    bodyFs: parseFloat(bs.fontSize), sepFs: parseFloat(cs.fontSize),
    weight: cs.fontWeight, ls: parseFloat(cs.letterSpacing) || 0,
    alignLast: cs.textAlignLast,
    bodyCenter: (bRect.left + bRect.right) / 2,
    inkCenter: (ink.left + ink.right) / 2,
    inkW: Math.round(ink.width), inkH: Math.round(ink.height),
    blockH: Math.round(sRect.height),
    gapPrev: prev ? Math.round(sRect.top - prev.getBoundingClientRect().bottom) : null,
    gapNext: next ? Math.round(next.getBoundingClientRect().top - sRect.bottom) : null,
  };
});

await page.goto(`${BASE}/read/b/${KEY}#c${CH}`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('.rv-sep', { timeout: 30000 });
await page.waitForTimeout(800);

const m = await measure();
chk('找得到分隔符', !!m);
if (m) {
  chk('字号不超过正文', m.sepFs <= m.bodyFs + 0.01, `${m.sepFs} vs ${m.bodyFs}`);
  chk('字号是正文的 0.85–0.95 倍', m.sepFs / m.bodyFs >= 0.84 && m.sepFs / m.bodyFs <= 0.96,
    (m.sepFs / m.bodyFs).toFixed(2));
  chk('字重 400 (书内 bold 不泄漏)', m.weight === '400', m.weight);
  chk('单字符不加字距', Math.abs(m.ls) < 0.01, m.ls + 'px');
  chk('text-align-last 也是 center', m.alignLast === 'center', m.alignLast);
  chk('水平居中 (±2px)', Math.abs(m.inkCenter - m.bodyCenter) <= 2,
    `ink=${m.inkCenter.toFixed(1)} col=${m.bodyCenter.toFixed(1)}`);
  chk('墨迹尺寸跟字号相称 (宽 ≤ 1.2 行高)', m.inkW <= m.bodyFs * 1.2,
    `${m.inkW}x${m.inkH}px @${m.bodyFs}px`);
  chk('块高接近一行 (不撑成标题高度)', m.blockH <= m.sepFs * 2.4,
    m.blockH + 'px');
  chk('上方留白 >= 8px', m.gapPrev === null || m.gapPrev >= 8, m.gapPrev + 'px');
  chk('下方留白 >= 8px', m.gapNext === null || m.gapNext >= 8, m.gapNext + 'px');
}

// 翻页模式应一致
await page.evaluate(() => {
  const btn = document.querySelector('[data-mode="page"]');
  if (btn) btn.click();
});
await page.waitForTimeout(1200);
const mp = await measure();
if (mp) {
  chk('[翻页] 字号同滚动模式', Math.abs(mp.sepFs - m.sepFs) < 0.01, `${mp.sepFs}`);
  chk('[翻页] 仍然居中 (±2px)', Math.abs(mp.inkCenter - mp.bodyCenter) <= 2,
    `ink=${mp.inkCenter.toFixed(1)} col=${mp.bodyCenter.toFixed(1)}`);
}

chk('0 JS 报错', errs.length === 0, errs.slice(0, 2).join(' | '));

await browser.close();
console.log(`\n${pass} ok / ${fail} FAIL`);
process.exit(fail ? 1 : 0);
