/* 书宠分区 —— 深色皮肤 + 状态药丸 + 焦点指示线。 */
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RECTO_BASE || '5015';
const URL = `http://127.0.0.1:${BASE}`;

let PASS = 0, FAIL = 0;
function ok(cond, name) {
  if (cond) { PASS++; console.log('PASS', name); }
  else { FAIL++; console.log('FAIL', name); }
}

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
await ctx.addCookies([{ name: 'zy_webauth', value: cookie(), domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
const errs = [];
page.on('pageerror', (e) => errs.push(String(e)));

await page.goto(`${URL}/pet/`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(600);

// 状态药丸: 用的是全局 .pill (Material chip)
const pill = await page.$eval('#pet-state', (el) => {
  const cs = getComputedStyle(el);
  const r = el.getBoundingClientRect();
  return { cls: el.className, h: Math.round(r.height), w: Math.round(r.width),
           radius: cs.borderRadius, fs: cs.fontSize, text: el.textContent.trim() };
});
console.log('pill:', pill);
ok(pill.h >= 18 && pill.h <= 30, `药丸高度合理 (${pill.h}px)`);
ok(pill.w > 20, `药丸有宽度 (${pill.w}px)`);
// 全站走直角口径 (radius_scale=0), 药丸也该是方的
ok(pill.radius === '0px', `药丸跟着全站走直角 (${pill.radius})`);

// 聚焦指示线
await page.click('#llm-base');
await page.waitForTimeout(150);
const focused = await page.$eval('#llm-base', (el) => getComputedStyle(el).boxShadow);
console.log('focus shadow:', focused);
ok(focused.includes('inset'), '聚焦有底部指示线 (inset box-shadow)');

// 深色皮肤: 注入 material-dark 的 token
const darkCss = await page.evaluate(async () => {
  const r = await fetch('/assets/skin.css');
  return (await r.text()).slice(0, 200);
});
ok(darkCss.length > 0, 'skin.css 可取');

// 读 appearance 接口拿深色皮肤 CSS
const res = await page.evaluate(async () => {
  const r = await fetch('/appearance');
  return r.status;
});
ok(res === 200, `外观页可访问 (${res})`);

// 深色皮肤通过 ?skin= 或设置保存 —— 这里直接验证 token 存在
const tokens = await page.evaluate(() => {
  const cs = getComputedStyle(document.documentElement);
  return {
    primary: cs.getPropertyValue('--rc-primary').trim(),
    surface: cs.getPropertyValue('--rc-surface').trim(),
    dim: cs.getPropertyValue('--rc-dim').trim(),
  };
});
console.log('tokens:', tokens);
ok(!!tokens.primary && !!tokens.surface, '皮肤 token 齐全');

ok(errs.length === 0, `无 JS 报错 (${errs.length})`);

await browser.close();
console.log(`\n${PASS} passed, ${FAIL} failed`);
process.exit(FAIL ? 1 : 0);
