import { chromium } from './_env.mjs';
import { REPO } from './_env.mjs';
import { createRequire } from 'module';
import { fileURLToPath } from 'url';
import path from 'path';
import { execFileSync } from 'child_process';

const require = createRequire(import.meta.url);

// 取一个已签会话 cookie
const cookieVal = execFileSync('python3', ['-c', `
import sys; sys.path.insert(0,REPO + "/src")
from recto.app import webauth
print(webauth.set_cookie("recto").split(";")[0].split("=",1)[1])
`], { encoding: 'utf8' }).trim();

const BASE = process.env.RECTO_BASE || 'http://127.0.0.1:5015';
const browser = await chromium.launch();
const out = [];

for (const [w, h, tag] of [[1400, 900, 'desktop'], [390, 780, 'mobile']]) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h } });
  await ctx.addCookies([{ name: 'zy_webauth', value: cookieVal, domain: '127.0.0.1', path: '/' }]);
  const p = await ctx.newPage();
  const errs = [];
  p.on('pageerror', e => errs.push(String(e)));
  await p.goto(BASE + '/', { waitUntil: 'networkidle' });
  const info = await p.evaluate(() => {
    const b = document.querySelector('.rc-brand');
    const en = document.querySelector('.rc-brand-en');
    const cs = en ? getComputedStyle(en) : null;
    const cnCS = getComputedStyle(b.querySelector('.rc-mark').nextSibling ? b : b);
    return {
      text: b ? b.textContent.trim() : null,
      html: b ? b.innerHTML.replace(/\s+/g, ' ').slice(0, 150) : null,
      enText: en ? en.textContent : null,
      enSize: cs ? cs.fontSize : null,
      enColor: cs ? cs.color : null,
      brandW: b ? Math.round(b.getBoundingClientRect().width) : null,
      title: document.title,
      overflowX: document.documentElement.scrollWidth > window.innerWidth + 1,
    };
  });
  // 截一块顶栏
  await p.screenshot({ path: `/tmp/brand-${tag}.png`, clip: { x: 0, y: 0, width: w, height: 90 } });
  out.push({ tag, ...info, errs });
  await ctx.close();
}
await browser.close();
console.log(JSON.stringify(out, null, 1));
