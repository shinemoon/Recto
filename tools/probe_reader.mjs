import { createRequire } from 'module';
const require = createRequire(import.meta.url);
const { chromium } = require('playwright');

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5294';
const KEY = process.env.RV_KEY || '9f81d4e6264e8de3';

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', e => errors.push(String(e)));
page.on('response', r => { if (r.status() >= 400) errors.push('HTTP ' + r.status() + ' ' + r.url()); });
page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

await page.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(800);

const blocks = await page.locator('#rv-content .rv-blk').count();
const title = await page.locator('#rv-title').textContent();
const pct = await page.locator('#rv-pct').textContent();
const tocItems = await page.locator('#rv-toc-list a').count();
console.log('blocks', blocks, '| title', title, '| pct', pct, '| toc', tocItems);

// 目录抽屉
await page.click('[data-act="toc"]');
const tocOpen = await page.locator('#rv-toc').evaluate(el => el.classList.contains('rv-on'));
await page.click('#rv-toc a[data-ch="6"]');
await page.waitForTimeout(700);
const chText = (await page.locator('#rv-content .rv-blk').first().innerText()).slice(0, 30);
console.log('tocOpen', tocOpen, '| after jump chapter text', JSON.stringify(chText));

// 划线：用 DOM Range 选中第二段前 12 个字，触发工具条
const info = await page.evaluate(() => {
  const blk = document.querySelectorAll('#rv-content .rv-blk')[1];
  const walker = document.createTreeWalker(blk, NodeFilter.SHOW_TEXT);
  const node = walker.nextNode();
  const r = document.createRange();
  r.setStart(node, 0);
  r.setEnd(node, Math.min(12, node.nodeValue.length));
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(r);
  return { text: r.toString(), off: blk.dataset.off };
});
await page.waitForTimeout(500);
const selVisible = await page.locator('#rv-sel').isVisible();
console.log('selection', JSON.stringify(info), '| bar', selVisible);
await page.click('[data-sel="hl"]');
await page.waitForTimeout(600);
const hlCount = await page.evaluate(() => {
  if (!window.CSS || !CSS.highlights) return -1;
  let n = 0;
  ['yellow', 'green', 'blue', 'pink'].forEach(c => {
    const h = CSS.highlights.get('rv-hl-' + c);
    if (h) n += Array.from(h).length;
  });
  return n;
});
console.log('highlight ranges', hlCount);

// 笔记面板
await page.click('[data-act="notes"]');
const noteItems = await page.locator('#rv-notes-list .rv-note-item').count();
console.log('note items', noteItems);
await page.click('#rv-notes [data-close="notes"]');

// 排版面板: 字号 + 主题
const fs0 = await page.locator('#rv').evaluate(el => getComputedStyle(el).getPropertyValue('--rv-fs'));
await page.click('[data-act="typo"]');
await page.locator('#rv-typo [data-look="fs"]').evaluate(el => {
  el.value = '24';
  el.dispatchEvent(new Event('input', { bubbles: true }));
});
const fs1 = await page.locator('#rv').evaluate(el => getComputedStyle(el).getPropertyValue('--rv-fs'));
await page.keyboard.press('Escape');
await page.click('[data-act="theme"]');
const theme = await page.locator('#rv').getAttribute('data-theme');
console.log('font', fs0.trim(), '->', fs1.trim(), '| theme', theme);

// 滚动 → 进度保存
await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight * 0.35));
await page.waitForTimeout(3200);
const pct2 = await page.locator('#rv-pct').textContent();
console.log('pct after scroll', pct2);

// 手机视口
const mob = await ctx.newPage();
await mob.setViewportSize({ width: 390, height: 844 });
await mob.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
await mob.waitForTimeout(600);
const overflow = await mob.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
console.log('mobile overflow', overflow);

// 书架页
const shelf = await ctx.newPage();
await shelf.goto(`${BASE}/read`, { waitUntil: 'domcontentloaded' });
const cards = await shelf.locator('.rs-card').count();
const navCurrent = await shelf.locator('.rc-nav a[aria-current="page"]').first().textContent();
console.log('shelf cards', cards, '| nav current', navCurrent);

console.log('errors', errors);
await browser.close();
