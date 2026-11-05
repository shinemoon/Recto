import { chromium } from './_env.mjs';
import { REPO } from './_env.mjs';
import { execSync } from 'node:child_process';
const cookie = execSync(`cd ${REPO} && PYTHONPATH=src python3 -c "from recto.app import webauth;print(webauth.set_cookie('recto'))"`, { encoding: 'utf8' }).trim();
const val = cookie.split(';')[0].split('=').slice(1).join('=');
const b = await chromium.launch();
const ctx = await b.newContext({ viewport: { width: 1200, height: 1000 }, deviceScaleFactor: 2 });
await ctx.addCookies([{ name: 'zy_webauth', value: val, domain: '127.0.0.1', path: '/' }]);
const p = await ctx.newPage();
await p.route('**/api/sync/run', (r) => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
  ok: true, ms: 1840, updated: 1, skipped: 1, at: '2026-11-04T03:40:00+00:00',
  detail: [
    { book: '示例长篇（多卷）', pct: 0.317195, chapter: 182, offset: 12954, updated: true },
    { book: '示例短篇', skipped: 'local-ahead', cloud: 0.542994, local: 0.542994 }],
  notes: { added: 2, updated: 0, deleted: 0, unchanged: 24, skipped: 0, books: [{ book: '示例长篇（多卷）', notes: 26, marks: 6 }] },
  time: { days: 6, merged: 6, skipped: 1, detail: [{ book: '示例长篇（多卷）', days: 6, merged: 6 }] }
}) }));
await p.goto('http://127.0.0.1:5015/', { waitUntil: 'networkidle' });
await p.click('[data-pane-link="sync"]');
await p.waitForTimeout(200);
await p.click('button[data-act="pull"]');
await p.waitForTimeout(600);
await p.click('#sec-log > summary');
await p.click('#log-refresh');
await p.waitForTimeout(700);
const box = await p.locator('#sec-out').boundingBox();
const box2 = await p.locator('#sec-log').boundingBox();
await p.screenshot({ path: '/tmp/result_text.png', clip: { x: 0, y: Math.max(0, box.y - 60), width: 1200, height: Math.min(1000, (box2.y + box2.height) - box.y + 120) } });
console.log(await p.locator('#out-pill').textContent());
await b.close();
