/* 验 ::highlight() 里 text-decoration-style: wavy 真的生效 (对比 solid / none 的像素数) */
import { chromium } from './_env.mjs';

const html = `<!doctype html><html><head><style>
body { margin: 0; background: #ffffff; font: 24px/2 serif; }
p { margin: 0 0 40px; padding: 0 20px; }
::highlight(h-wavy)  { text-decoration: underline wavy #d9a404; text-decoration-thickness: 1.5px; text-underline-offset: 4px; }
::highlight(h-dash)  { text-decoration: underline dashed #d9a404; text-decoration-thickness: 1.5px; text-underline-offset: 4px; }
::highlight(h-solid) { text-decoration: underline solid #d9a404; text-decoration-thickness: 1.5px; text-underline-offset: 4px; }
::highlight(h-none)  { color: #d9a404; }
</style></head><body>
<p id="a">波浪下划线的文字波浪下划线的文字波浪下划线的文字</p>
</body></html>`;

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 700, height: 120 } });
await page.setContent(html);

async function shot(name) {
  await page.evaluate((n) => {
    ['h-wavy', 'h-dash', 'h-solid', 'h-none'].forEach(k => CSS.highlights.delete(k));
    const p = document.getElementById('a');
    const t = p.firstChild;
    const r = document.createRange();
    r.setStart(t, 0); r.setEnd(t, 12);
    const h = new Highlight(); h.add(r);
    CSS.highlights.set(n, h);
  }, name);
  await page.waitForTimeout(150);
  return await page.screenshot({ path: `/tmp/wavy_${name}.png` });
}

for (const n of ['h-wavy', 'h-dash', 'h-solid', 'h-none']) await shot(n);
await browser.close();
console.log('done');
