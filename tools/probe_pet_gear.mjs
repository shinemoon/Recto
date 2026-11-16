/* 书宠「行囊与银钱」卡真浏览器冒烟 (playwright)
 *
 * 验: 卡片在 / 银钱徽标 / 三个装具槽 / 行囊折叠 / 铺子折叠 / 按钮在 /
 *     390px 无横向溢出 / 0 JS 报错
 * 只读, 不点买与卖 —— 不往书宠的账上写东西。
 */
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RECTO_BASE || 'http://127.0.0.1:5015';
const errors = [];
let pass = 0, fail = 0;
function ok(cond, msg) {
  if (cond) { pass++; console.log('PASS', msg); } else { fail++; console.log('FAIL', msg); }
}

const browser = await chromium.launch();
try {
  for (const vp of [{ width: 1400, height: 900 }, { width: 390, height: 780 }]) {
    const ctx = await browser.newContext({ viewport: vp });
    await ctx.addCookies([{ name: 'zy_webauth', value: cookie(), domain: '127.0.0.1', path: '/' }]);
    const p = await ctx.newPage();
    p.on('pageerror', (e) => errors.push(String(e)));
    await p.goto(BASE + '/pet/', { waitUntil: 'domcontentloaded' });
    await p.waitForSelector('#card-bag', { state: 'attached' });
    // 人物面板是第二个分区, 先切过去
    await p.click('.pg-tab[data-pg="hero"]');
    await p.waitForTimeout(700);

    const info = await p.evaluate(() => {
      const card = document.querySelector('#card-bag');
      const money = document.querySelector('#bag-money');
      return {
        hasCard: !!card,
        visible: !!(card && card.offsetParent !== null),
        money: money ? money.textContent.trim() : '',
        slots: document.querySelectorAll('#bag-slots .bag-slot').length,
        mods: (document.querySelector('#bag-mods') || {}).textContent || '',
        bagOpen: document.querySelector('#fold-card-bag').open,
        shopOpen: document.querySelector('#fold-card-shop').open,
        bagSum: (document.querySelector('#bag-sum') || {}).textContent || '',
        docW: document.documentElement.scrollWidth,
        winW: window.innerWidth,
      };
    });
    ok(info.hasCard && info.visible, `${vp.width} · 行囊卡在且可见`);
    ok(/文/.test(info.money), `${vp.width} · 银钱徽标有数 (${info.money})`);
    ok(info.slots === 3, `${vp.width} · 三个装具槽 (${info.slots})`);
    ok(/命中|兵刃|赤手/.test(info.mods), `${vp.width} · 身上有一套摘要`);
    ok(info.bagOpen === false && info.shopOpen === false, `${vp.width} · 行囊/铺子默认收起`);
    ok(info.docW <= info.winW + 1, `${vp.width} · 无横向溢出 (${info.docW}/${info.winW})`);

    // 展开行囊: 有行有按钮
    await p.click('#fold-card-bag > summary');
    await p.waitForTimeout(250);
    const bag = await p.evaluate(() => {
      const rows = [...document.querySelectorAll('#bag-list .bag-row')];
      return {
        n: rows.length,
        acts: [...document.querySelectorAll('#bag-list [data-bag-act]')].length,
        names: rows.map((r) => r.querySelector('.bag-nm').textContent.trim()).slice(0, 4),
        overflow: document.documentElement.scrollWidth <= window.innerWidth + 1,
      };
    });
    ok(bag.n > 0, `${vp.width} · 行囊里有 ${bag.n} 行 (${bag.names.join(' / ')})`);
    ok(bag.acts > 0, `${vp.width} · 行上有动作按钮 (${bag.acts})`);
    ok(bag.overflow, `${vp.width} · 展开行囊后仍不溢出`);

    await p.click('#fold-card-shop > summary');
    await p.waitForTimeout(250);
    const shop = await p.evaluate(() => ({
      n: document.querySelectorAll('#shop-list .bag-row').length,
      buy: document.querySelectorAll('#shop-list [data-bag-act="buy"]').length,
      overflow: document.documentElement.scrollWidth <= window.innerWidth + 1,
    }));
    ok(shop.n > 10 && shop.buy > 10, `${vp.width} · 铺子里 ${shop.n} 样, ${shop.buy} 个买按钮`);
    ok(shop.overflow, `${vp.width} · 展开铺子后仍不溢出`);
    await ctx.close();
  }
  ok(errors.length === 0, `0 JS 报错 (${errors.slice(0, 2).join(' | ')})`);
} finally {
  await browser.close();
}

console.log(`\nPASS ${pass} FAIL ${fail}`);
process.exit(fail ? 1 : 0);
