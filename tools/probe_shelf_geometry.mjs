// 书架几何审美巡检: 卡片比例 / 文本是否被截断 / 统计条对齐 / 手机两列
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
const { execFileSync } = require('child_process');

const BASE = 'http://127.0.0.1:5015';
const token = execFileSync('python3', [new URL('./make_cookie.py', import.meta.url).pathname],
  { encoding: 'utf8' }).trim();
const res = [];
const ok = (c, n, x) => { res.push(!!c); console.log((c ? 'PASS ' : 'FAIL ') + n + (x === undefined ? '' : ' :: ' + JSON.stringify(x))); };

const browser = await chromium.launch({ args: ['--no-sandbox'] });

async function measure(width, height, label) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
  const page = await ctx.newPage();
  await page.goto(BASE + '/', { waitUntil: 'networkidle' });
  await page.waitForTimeout(400);
  const m = await page.evaluate(() => {
    const card = document.querySelector('.bk');
    const cover = card.querySelector('.bk-cover').getBoundingClientRect();
    const meta = [...card.querySelectorAll('.bk-meta span')];
    const stat = [...document.querySelectorAll('#shelf-stat .s')];
    return {
      cardW: Math.round(card.getBoundingClientRect().width),
      cardH: Math.round(card.getBoundingClientRect().height),
      coverRatio: +(cover.width / cover.height).toFixed(3),
      titleLines: (() => {
        const t = card.querySelector('.bk-title');
        const lh = parseFloat(getComputedStyle(t).lineHeight);
        return Math.round(t.getBoundingClientRect().height / lh);
      })(),
      // 书名单行截断: 全部卡片书名只占一行, 超宽用省略号 (不换行)
      titleStyle: (() => {
        const t = card.querySelector('.bk-title');
        const cs = getComputedStyle(t);
        const titles = [...document.querySelectorAll('.bk-title')];
        const lh = parseFloat(cs.lineHeight);
        return {
          whiteSpace: cs.whiteSpace,
          textOverflow: cs.textOverflow,
          multiLine: titles.filter((x) => Math.round(x.getBoundingClientRect().height / lh) > 1).length,
          clipped: titles.filter((x) => x.scrollWidth > x.clientWidth + 1).length,
        };
      })(),
      metaOverflow: meta.filter((s) => s.scrollWidth > s.clientWidth + 1).length,
      metaText: meta.map((s) => s.textContent.trim()),
      statW: stat.map((s) => Math.round(s.getBoundingClientRect().width)),
      statTop: [...new Set(stat.map((s) => Math.round(s.getBoundingClientRect().top)))].length,
      gridCols: getComputedStyle(document.getElementById('shelf'))
        .gridTemplateColumns.split(' ').length,
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    };
  });
  console.log(label, JSON.stringify(m));
  await ctx.close();
  return m;
}

const desk = await measure(1400, 950, 'desktop:');
ok(desk.cardW >= 150 && desk.cardW <= 230, '卡片宽度在 150–230', desk.cardW);
ok(Math.abs(desk.coverRatio - 0.75) < 0.02, '封面 3:4', desk.coverRatio);
ok(desk.titleLines === 1, '书名单行不换行', desk.titleLines);
ok(desk.titleStyle.whiteSpace === 'nowrap' && desk.titleStyle.textOverflow === 'ellipsis',
  '书名按卡片宽度截断 (nowrap + ellipsis)', desk.titleStyle);
ok(desk.titleStyle.multiLine === 0, '所有卡片书名都只有一行', desk.titleStyle.multiLine);
ok(desk.metaOverflow === 0, '卡片元信息不溢出', desk.metaText);
ok(desk.statTop === 1, '统计条同一行对齐', desk.statTop);
ok(desk.overflow <= 1, '桌面不溢出');

const phone = await measure(390, 844, 'mobile:');
ok(phone.gridCols === 2, '手机 2 列', phone.gridCols);
ok(phone.metaOverflow === 0, '手机卡片元信息不溢出', phone.metaText);
ok(phone.overflow <= 1, '手机不溢出');

await browser.close();
const bad = res.filter((x) => !x).length;
console.log('\n' + (res.length - bad) + ' passed, ' + bad + ' failed');
process.exit(bad ? 1 : 0);
