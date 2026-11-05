import { chromium } from './_env.mjs';
import { REPO } from './_env.mjs';
import { execFileSync } from 'child_process';

// 外观页 skin-card 里色块 (.sw) 的几何探针
const cookieVal = execFileSync('python3', ['-c', `
import sys; sys.path.insert(0,REPO + "/src")
from recto.app import webauth
print(webauth.set_cookie("root").split(";")[0].split("=",1)[1])
`], { encoding: 'utf8' }).trim();

const BASE = process.env.RECTO_BASE || 'http://127.0.0.1:5015';
const browser = await chromium.launch();
const out = [];
let fail = 0;
const ck = (name, cond, extra) => {
  if (!cond) fail++;
  out.push(`${cond ? 'PASS' : 'FAIL'}  ${name}${extra !== undefined ? '  ' + JSON.stringify(extra) : ''}`);
};

for (const [w, h, tag] of [[1400, 900, 'desktop'], [820, 900, 'tablet'], [390, 780, 'mobile']]) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h } });
  await ctx.addCookies([{ name: 'zy_webauth', value: cookieVal, domain: '127.0.0.1', path: '/' }]);
  const p = await ctx.newPage();
  const errs = [];
  p.on('pageerror', e => errs.push(String(e)));
  await p.goto(BASE + '/appearance', { waitUntil: 'networkidle' });

  const info = await p.evaluate(() => {
    const card = document.querySelector('.skin-card');
    if (!card) return { none: true };
    const cardCS = getComputedStyle(card);
    const sws = [...card.querySelectorAll('.sw')].map(sw => {
      const r = sw.getBoundingClientRect();
      const bars = [...sw.querySelectorAll('i')].map(i => {
        const b = i.getBoundingClientRect();
        return { w: Math.round(b.width), h: Math.round(b.height), bg: getComputedStyle(i).backgroundColor };
      });
      return {
        w: Math.round(r.width), h: Math.round(r.height),
        align: getComputedStyle(sw).alignItems,
        bars,
      };
    });
    return {
      cardW: Math.round(card.getBoundingClientRect().width),
      align: cardCS.alignItems,
      justify: cardCS.justifyContent,
      whiteSpace: cardCS.whiteSpace,
      sws,
      dsH: Math.round(card.querySelector('.ds')?.getBoundingClientRect().height || 0),
      dsOverflow: (() => {
        const d = card.querySelector('.ds');
        return d ? d.scrollWidth > d.clientWidth + 1 : false;
      })(),
      overflowX: document.documentElement.scrollWidth > window.innerWidth + 1,
    };
  });

  if (info.none) { ck(`${tag} 皮肤卡存在`, false); continue; }
  ck(`${tag} skin-card 宽度合理`, info.cardW > 180, info.cardW);
  ck(`${tag} card align-items 为 stretch`, info.align === 'stretch', info.align);
  ck(`${tag} card 允许换行`, info.whiteSpace === 'normal', info.whiteSpace);

  for (const [n, sw] of info.sws.entries()) {
    ck(`${tag} .sw#${n} 高度 34`, sw.h === 34, sw.h);
    ck(`${tag} .sw#${n} 宽度撑满卡片`, sw.w >= info.cardW - 30, { sw: sw.w, card: info.cardW });
    const total = sw.bars.reduce((a, b) => a + b.w, 0);
    ck(`${tag} .sw#${n} 色条总宽 ≈ 容器宽`, Math.abs(total - (sw.w - 2)) <= 3, { total, sw: sw.w });
    ck(`${tag} .sw#${n} 色条等高满高`, sw.bars.every(b => b.h >= 32 && b.w >= 8),
      sw.bars.map(b => `${b.w}x${b.h}`));
    ck(`${tag} .sw#${n} 色条有底色`, sw.bars.every(b => b.bg && b.bg !== 'rgba(0, 0, 0, 0)'),
      sw.bars.map(b => b.bg));
  }
  ck(`${tag} 说明文字不被裁`, info.dsOverflow === false, info.dsH);
  ck(`${tag} 无横向溢出`, info.overflowX === false);
  ck(`${tag} 无 JS 报错`, errs.length === 0, errs);
  await ctx.close();
}

await browser.close();
console.log(out.join('\n'));
console.log(`\n${fail === 0 ? 'ALL PASS' : fail + ' FAIL'} (${out.length} 项)`);
process.exit(fail === 0 ? 0 : 1);
