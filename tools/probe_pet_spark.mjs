// 书宠「这一趟的来处 → 化成了」冒烟: 看看故事跟读到的东西有没有挂钩。
// 跑法: node tools/probe_pet_spark.mjs   (需要 tools/node_modules 里装了 playwright)
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RECTO_BASE || 'http://127.0.0.1:5015';
const CK = cookie();
const VIEWS = [{ w: 1400, h: 950, tag: 'desktop' }, { w: 390, h: 844, tag: 'phone' }];

const r = await fetch(BASE + '/pet/api/adventures?limit=3', {
  headers: { cookie: 'zy_webauth=' + CK },
});
const data = await r.json();
const latest = (data.adventures || [])[0] || {};
const echo = (latest.growth || {}).echo || {};
const sparkItems = echo.spark || [];
console.log('最新一回:', latest.no, latest.title, '| 来处条数:', sparkItems.length,
  '| 落地:', JSON.stringify((latest.growth || {}).spark_used));

const browser = await chromium.launch();
let OK = 0, BAD = 0;
const ok = (n, c, extra = '') => { if (c) { OK++; console.log('  ok  ', n); } else { BAD++; console.log('  FAIL', n, extra); } };

for (const v of VIEWS) {
  const ctx = await browser.newContext({ viewport: { width: v.w, height: v.h } });
  await ctx.addCookies([{ name: 'zy_webauth', value: CK, domain: '127.0.0.1', path: '/' }]);
  const p = await ctx.newPage();
  const errs = [];
  p.on('pageerror', (e) => errs.push(String(e)));
  // 「化成了」在**档案页** (按回查阅) —— 前台是那条连续的路, 不摆卡片
  await p.goto(BASE + '/pet/story/', { waitUntil: 'networkidle' });
  await p.waitForSelector('#archive-list .arch-item', { timeout: 20000 }).catch(() => {});
  await p.evaluate(() => {
    document.querySelectorAll('#archive-list .arch-item').forEach((d) => { d.open = true; });
  });
  await p.waitForTimeout(250);
  // 档案页每一回都有「化成了」块 —— 要量的是**最新那一回**
  // (与接口给的 latest 对得上), 所以只看最后一个条目。
  const latestItem = p.locator('#archive-list .arch-item').last();
  const n = await latestItem.locator('.adv-echo-spark .echo-spark-list li').count();
  const heads = await latestItem.locator('.adv-echo-l').allInnerTexts();
  ok(v.tag + ' 有「化成了」那一块', n > 0 || sparkItems.length === 0, 'li=' + n);
  ok(v.tag + ' 条数与接口一致', n === 0 ? sparkItems.length === 0 : n === sparkItems.length,
     `dom=${n} api=${sparkItems.length}`);
  ok(v.tag + ' 有「化成了」标签', heads.some((t) => t.includes('化成了')), heads.join('|'));
  if (n > 0) {
    const first = (await latestItem.locator('.adv-echo-spark .echo-spark-list li').first().innerText()).replace(/\n/g, ' ');
    ok(v.tag + ' 第一条有类别与人话', /^(人|地|物|势力|事|书文|名目|时节)\s*\S/.test(first), JSON.stringify(first));
    const box = await latestItem.locator('.adv-echo-spark').boundingBox();
    ok(v.tag + ' 不越界', box && box.x >= 0 && box.x + box.width <= v.w + 1, JSON.stringify(box));
  }
  ok(v.tag + ' 0 JS 报错', errs.length === 0, errs.join(';'));
  await ctx.close();
}
await browser.close();
console.log('');
console.log(`${OK} passed, ${BAD} failed`);
process.exit(BAD ? 1 : 0);
