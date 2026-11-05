import { chromium } from './_env.mjs';
import { REPO } from './_env.mjs';
import { execSync } from 'node:child_process';
import { skinSnapshot, restoreSkin } from './_skins.mjs';

const base = 'http://127.0.0.1:5015';
const cookieVal = execSync(
  'cd ' + REPO + ' && PYTHONPATH=src python3 -c "from recto.app import webauth;'
  + 'print(webauth.set_cookie(\'recto\').split(\';\')[0].split(\'=\',1)[1])"'
).toString().trim();

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1400, height: 1000 } });
await ctx.addCookies([{ name: 'zy_webauth', value: cookieVal, domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
const errs = [];
page.on('pageerror', e => errs.push(String(e)));

async function shot(skin) {
  await page.goto(base + '/', { waitUntil: 'domcontentloaded' });
  await page.evaluate(async (s) => {
    await fetch('http://127.0.0.1:5015/api/appearance', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ skin: s }),
    });
  }, skin);
  await page.goto(base + '/corpus/', { waitUntil: 'networkidle' });
  await page.waitForTimeout(500);
  return page.evaluate(() => {
    const rows = [];
    for (const r of document.querySelectorAll('.row')) {
      const bs = [...r.querySelectorAll(':scope > button')];
      if (bs.length < 2) continue;
      rows.push(bs.map(b => ({
        id: b.id || b.textContent.trim().slice(0, 6),
        cls: b.className,
        bg: getComputedStyle(b).backgroundColor,
      })));
    }
    return { bg: getComputedStyle(document.body).backgroundColor, rows };
  });
}

const snap = await skinSnapshot();
const light = await shot('material');
await page.goto(base + '/', { waitUntil: 'networkidle' });
await page.waitForTimeout(300);
await page.screenshot({ path: '/tmp/ui-library.png', fullPage: false });
await page.goto(base + '/corpus/', { waitUntil: 'networkidle' });
await page.waitForTimeout(400);
await page.screenshot({ path: '/tmp/ui-corpus.png', fullPage: false });
const dark = await shot('material-dark');
await page.goto(base + '/corpus/', { waitUntil: 'networkidle' });
await page.waitForTimeout(400);
await page.screenshot({ path: '/tmp/ui-corpus-dark.png', fullPage: false });
await restoreSkin(snap);   // 还原用户原值, 不是硬编码 material

console.log(JSON.stringify({ light, dark, errs }, null, 1));
await browser.close();
