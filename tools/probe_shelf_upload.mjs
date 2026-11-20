/* 书架上传 —— 真浏览器探针。
 *
 * 跑法: cd tools && node probe_shelf_upload.mjs
 * 环境: RECTO_BASE (默认 5015)
 *
 * 验的事:
 *   · 书架页有「上传书籍」按钮 + 隐藏的 file input (accept 限 epub/txt)
 *   · 选文件 → 走 multipart → 书真的进了书库, 书架上多一张卡
 *   · 重名不覆盖 (两本都在)
 *   · 非白名单文件被拒, 且提示是人话
 *   · 手机 390px 不溢出 / 0 JS 报错
 *
 * 卫生: 会上传**真文件**进书库 —— 用 RV_UPLOAD_TITLE 前缀的书, 收尾删掉
 *       (走 /read/api/book/<key>/delete, 与「删除」按钮同一条路)。
 */
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RECTO_BASE || '5015';
const URL = `http://127.0.0.1:${BASE}`;
const PREFIX = process.env.RV_UPLOAD_TITLE || '上传测试书';

let PASS = 0, FAIL = 0;
function ok(cond, name, extra = '') {
  if (cond) { PASS++; console.log('PASS', name); }
  else { FAIL++; console.log('FAIL', name, extra); }
}

/* 造一个最小 epub (zip: mimetype + container.xml + content.opf) */
function makeEpubBytes(title, author) {
  // 手写 zip 太麻烦 —— 用 Node 的 zlib 拼 store 模式的最小 zip。
  // 这里直接用已压缩的最小 epub: 一段固定字节不够 (要含 opf)，
  // 所以借系统的 zip 命令生成。
  return null; // 占位, 下面用 fs + 外部 zip
}

import { execFileSync } from 'child_process';
import fs from 'fs';
import os from 'os';
import path from 'path';

function buildEpub(dir, title, author) {
  const opf = `<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>${title}</dc:title><dc:creator>${author}</dc:creator></metadata><manifest><item id="c1" href="ch1.html" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="c1"/></spine></package>`;
  const container = '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>';
  const work = path.join(dir, 'src');
  fs.mkdirSync(path.join(work, 'META-INF'), { recursive: true });
  fs.mkdirSync(path.join(work, 'OEBPS'), { recursive: true });
  fs.writeFileSync(path.join(work, 'mimetype'), 'application/epub+zip');
  fs.writeFileSync(path.join(work, 'META-INF', 'container.xml'), container);
  fs.writeFileSync(path.join(work, 'OEBPS', 'content.opf'), opf);
  fs.writeFileSync(path.join(work, 'OEBPS', 'ch1.html'), '<html><body><p>正文</p></body></html>');
  const out = path.join(dir, `${title}.epub`);
  execFileSync('zip', ['-X', '-q', '-r', out, 'mimetype', 'META-INF', 'OEBPS'], { cwd: work });
  return out;
}

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'recto-upload-probe-'));
const epubPath = buildEpub(tmp, PREFIX, '探针作者');
const epubBuf = fs.readFileSync(epubPath);

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
await ctx.addCookies([{
  name: 'zy_webauth', value: cookie(), domain: '127.0.0.1', path: '/',
}]);
const page = await ctx.newPage();
const pageErrors = [];
const badResponses = [];
page.on('pageerror', (e) => pageErrors.push(String(e)));
// 只算**真** JS 报错: 资源加载失败 (含我们故意制造的 400) 不是 JS 异常
page.on('console', (m) => {
  if (m.type() !== 'error') return;
  const t = m.text();
  if (/Failed to load resource/.test(t)) return;
  pageErrors.push(t);
});
page.on('response', (r) => { if (r.status() >= 400) badResponses.push(r.status() + ' ' + r.url()); });

await page.goto(`${URL}/#library`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#shelf-upload');

/* 1 · 按钮与 file input 在 */
ok(await page.locator('#shelf-upload').count() === 1, '书架页有「上传书籍」按钮');
const accept = await page.getAttribute('#shelf-upload-file', 'accept');
ok(!!accept && accept.includes('.epub') && accept.includes('.txt'),
   'file input 限了 epub / txt', String(accept));
ok(await page.locator('#shelf-upload-file').evaluate((el) => el.hidden), 'file input 是隐藏的');

/* 2 · 真传一本 */
await page.setInputFiles('#shelf-upload-file', epubPath);
await page.waitForFunction(
  () => (document.querySelector('#shelf') || {}).textContent?.includes('上传测试书'),
  null, { timeout: 30000 }).catch(() => {});
const shelfText = await page.locator('#shelf').innerText();
ok(shelfText.includes(PREFIX), '传完的书出现在书架上', shelfText.slice(0, 200));

/* 3 · 书真的在书库里 (侧车写了 source=local-upload) */
const booksRes = await page.evaluate(async () => {
  const r = await fetch('/read/api/books', { credentials: 'same-origin' });
  return r.json();
});
const hit = (booksRes.books || []).find((b) => (b.title || '').includes(PREFIX));
ok(!!hit, '书库里能查到这本书', JSON.stringify((booksRes.books || []).length));

/* 4 · 再传一次 → 不覆盖, 两本都在 */
await page.setInputFiles('#shelf-upload-file', epubPath);
await page.waitForTimeout(2500);
const booksRes2 = await page.evaluate(async () => {
  const r = await fetch('/read/api/books', { credentials: 'same-origin' });
  return r.json();
});
const dups = (booksRes2.books || []).filter((b) => (b.title || '').includes(PREFIX));
ok(dups.length >= 2, '重名不覆盖 (两本都在)', `count=${dups.length}`);

/* 5 · 非白名单文件被拒, 提示是人话 */
const badPath = path.join(tmp, '坏文件.pdf');
fs.writeFileSync(badPath, '%PDF-1.4 fake');
await page.setInputFiles('#shelf-upload-file', badPath);
await page.waitForTimeout(2000);
const toastText = await page.evaluate(() => {
  const t = document.querySelector('.rc-toast');
  return t ? t.textContent : '';
});
ok(/格式|不支持|类型/.test(toastText), '非白名单文件被拒且说的是人话', toastText);
ok(!/Traceback|Exception|<class/.test(toastText), '提示里没有 Python 栈', toastText);

/* 6 · 手机 390px 不溢出 */
await page.setViewportSize({ width: 390, height: 780 });
await page.waitForTimeout(400);
const overflow = await page.evaluate(() =>
  document.documentElement.scrollWidth - document.documentElement.clientWidth);
ok(overflow <= 0, '手机 390px 无横向溢出', String(overflow));

/* 7 · 收尾: 把探针传的书删掉 */
for (const b of dups) {
  await page.evaluate(async (k) => {
    await fetch(`/read/api/book/${encodeURIComponent(k)}/delete`, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: '{}',
    });
  }, b.key);
}
const after = await page.evaluate(async () => {
  const r = await fetch('/read/api/books', { credentials: 'same-origin' });
  return r.json();
});
const left = (after.books || []).filter((b) => (b.title || '').includes(PREFIX));
ok(left.length === 0, '探针传的书已清理', `left=${left.length}`);

ok(pageErrors.length === 0, '无 JS 报错', pageErrors.slice(0, 3).join(' | '));

await browser.close();
fs.rmSync(tmp, { recursive: true, force: true });

console.log('\n==============================================');
console.log(`PASS ${PASS}  FAIL ${FAIL}`);
process.exit(FAIL ? 1 : 0);
