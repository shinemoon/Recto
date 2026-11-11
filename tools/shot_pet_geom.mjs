/* 书宠 vs 语料: 版式口径对比 (卡片 / 字段 / 按钮是否同族)。 */
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RECTO_BASE || '5015';
const URL = `http://127.0.0.1:${BASE}`;

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
await ctx.addCookies([{ name: 'zy_webauth', value: cookie(), domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();

async function geom(path, sel) {
  await page.goto(`${URL}${path}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(500);
  return page.$eval(sel, (el) => {
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return {
      h: Math.round(r.height), w: Math.round(r.width),
      pad: cs.padding, border: cs.border, radius: cs.borderRadius,
      bg: cs.backgroundColor, shadow: cs.boxShadow.slice(0, 40),
    };
  });
}

const petCard = await geom('/pet/', '.rc-pet .card');
const corpusCard = await geom('/corpus/', '.rc-corpus .card');
console.log('pet   card:', petCard);
console.log('corpus card:', corpusCard);

const petInput = await geom('/pet/', '#llm-base');
const corpusInput = await geom('/corpus/', '#llm-base');
console.log('pet   input:', petInput);
console.log('corpus input:', corpusInput);

const petBtn = await geom('/pet/', '#btn-llm-save');
const corpusBtn = await geom('/corpus/', '#btn-llm-save');
console.log('pet   button:', petBtn);
console.log('corpus button:', corpusBtn);

const petHead = await geom('/pet/', '.rc-pet .card-head');
const corpusHead = await geom('/corpus/', '.rc-corpus .card-head');
console.log('pet   card-head:', petHead);
console.log('corpus card-head:', corpusHead);

await browser.close();
