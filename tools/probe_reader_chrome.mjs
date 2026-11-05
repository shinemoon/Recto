/* 阅读界面细节: ① 滚动条已隐藏且不占布局 ② 顶栏按钮全图标化 + 右边距 ③ 字体清单 */
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
const { execFileSync } = require('child_process');

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || 'a6f63af571f05bb4';
const token = execFileSync('python3', [new URL('./make_cookie.py', import.meta.url).pathname],
  { encoding: 'utf8' }).trim();

const out = [];
const chk = (name, cond, extra = '') =>
  out.push(`${cond ? 'ok  ' : 'FAIL'} ${name}${extra ? ' — ' + extra : ''}`);

// RV_CLASSIC=1 时关掉浮层滚动条, 验"藏条之后连经典占位条也不出现"
const ARGS = ['--no-sandbox'];
if (process.env.RV_CLASSIC) ARGS.push('--disable-features=OverlayScrollbar');
const browser = await chromium.launch({ args: ARGS });

async function open(width, height) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
  await page.goto(`${BASE}/read/b/${KEY}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1200);
  return { page, ctx, errors };
}

/* ---------- 桌面 ---------- */
{
  const { page, ctx, errors } = await open(1280, 900);

  /* ① 滚动条: 完全隐藏 (宽高 0), 不占布局也不压顶栏 */
  const sb = await page.evaluate(() => {
    const cs = getComputedStyle(document.documentElement);
    return {
      inner: window.innerWidth,
      client: document.documentElement.clientWidth,
      gutter: cs.scrollbarGutter || '',
      sbw: cs.scrollbarWidth || '',
      barRight: document.getElementById('rv-bar').getBoundingClientRect().right,
      btnsRight: document.querySelector('.rv-btns').getBoundingClientRect().right,
      barTop: document.getElementById('rv-bar').getBoundingClientRect().top,
      // 用一个真能滚的子元素反查: 藏条之后它自己的 scrollbar 宽也应是 0
      innerBar: (() => {
        const d = document.createElement('div');
        d.style.cssText = 'width:40px;height:40px;overflow:scroll';
        document.body.appendChild(d);
        const w = d.offsetWidth - d.clientWidth;
        d.remove();
        return w;
      })()
    };
  });
  // 藏条之后: scrollbar-width:none, 没有 gutter, 视口宽 == 滚动区宽 (滚动条 0px)
  out.push('-- 视口 ' + sb.inner + ' / 滚动区 ' + sb.client +
           ' / 滚动条 ' + (sb.inner - sb.client) + 'px');
  chk('滚动条已隐藏 (width none, 无 gutter)', sb.sbw === 'none' &&
      (sb.gutter === 'auto' || !sb.gutter) && sb.inner - sb.client === 0,
      'gutter=' + JSON.stringify(sb.gutter) + ' width=' + sb.sbw +
      ' 占位 ' + (sb.inner - sb.client) + 'px');
  chk('顶栏吃满整幅宽 (不再为滚动条让位)', sb.barRight >= sb.inner - 0.5,
      `bar右=${sb.barRight.toFixed(1)} 视口=${sb.inner}`);
  chk('滚动条不可见 (子滚动区条宽 0)',
      sb.innerBar === 0, '子滚动区条宽=' + sb.innerBar + 'px');
  chk('按钮组离右边至少 12px', sb.inner - sb.btnsRight >= 12,
      `距右 ${(sb.inner - sb.btnsRight).toFixed(1)}px`);
  chk('顶栏贴顶', Math.abs(sb.barTop) < 1.5, `top=${sb.barTop.toFixed(1)}`);

  /* ② 顶栏按钮: 全图标, 无文字 */
  const btns = await page.evaluate(() =>
    Array.from(document.querySelectorAll('.rv-btns button')).map(b => ({
      act: b.dataset.act,
      text: (b.textContent || '').trim(),
      hasSvg: !!b.querySelector('svg'),
      w: Math.round(b.getBoundingClientRect().width),
      h: Math.round(b.getBoundingClientRect().height),
      aria: b.getAttribute('aria-label') || '',
      title: b.getAttribute('title') || ''
    })));
  chk('顶栏 5 个按钮', btns.length === 5, btns.map(b => b.act).join(','));
  chk('全部图标化 (无文字 + 有 svg)', btns.every(b => b.text === '' && b.hasSvg),
      btns.filter(b => b.text || !b.hasSvg).map(b => b.act).join(',') || 'all good');
  chk('全部有 aria-label / title', btns.every(b => b.aria && b.title));
  chk('图标按钮统一 32px 方块',
      btns.every(b => b.w === 32 && b.h === 32),
      btns.map(b => `${b.act}:${b.w}x${b.h}`).join(' '));

  /* ③ 字体清单 */
  await page.click('[data-act="typo"]');
  await page.waitForTimeout(300);
  const f = await page.evaluate(() => {
    const inp = document.getElementById('rv-font');
    if (inp) inp.focus();
    const menu = document.getElementById('rv-font-menu');
    return {
      isInput: !!inp && inp.tagName === 'INPUT',
      hasDatalist: !!document.getElementById('rv-font-opts'),
      hasMenu: !!menu,
      opened: !!menu && !menu.hidden,
      options: menu ? menu.querySelectorAll('.rv-combo-opt').length : -1,
      scanBtn: !!document.getElementById('rv-font-scan'),
      noteHidden: document.getElementById('rv-font-note').hidden
    };
  });
  chk('字体是输入框 + 自绘下拉', f.isInput && f.hasMenu && !f.hasDatalist);
  chk('下拉展开后有常用候选', f.opened && f.options >= 20, f.options + ' 项');
  chk('有"读取本机字体"按钮', f.scanBtn);
  chk('提示行默认隐藏', f.noteHidden);
  out.push(`-- 字体提示 ${f.options} 个`);

  const tb = await page.evaluate(() =>
    document.getElementById('rv-typo').getBoundingClientRect());
  chk('排版面板没出横向界', tb.right <= 1281 && tb.left >= 0,
      `left=${tb.left.toFixed(0)} right=${tb.right.toFixed(0)}`);
  chk('桌面无 JS 报错', errors.length === 0, errors.slice(0, 3).join(' | '));
  await ctx.close();
}

/* ---------- 手机 ---------- */
{
  const { page, ctx, errors } = await open(390, 844);
  const m = await page.evaluate(() => {
    const bar = document.getElementById('rv-bar').getBoundingClientRect();
    const btns = document.querySelector('.rv-btns').getBoundingClientRect();
    return {
      barH: Math.round(bar.height),
      btnsRight: btns.right,
      inner: window.innerWidth,
      client: document.documentElement.clientWidth,
      overflowX: Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth)
    };
  });
  chk('手机顶栏仍是一行', m.barH <= 60, `高 ${m.barH}px`);
  chk('手机按钮组不贴边', m.inner - m.btnsRight >= 8,
      `距右 ${(m.inner - m.btnsRight).toFixed(1)}px`);
  chk('手机无横向溢出', m.overflowX === 0, `溢出 ${m.overflowX}`);
  await page.click('[data-act="typo"]');
  await page.waitForTimeout(300);
  const mb = await page.evaluate(() =>
    document.getElementById('rv-typo').getBoundingClientRect());
  chk('手机排版面板不出界', mb.left >= 0 && mb.right <= 391,
      `left=${mb.left.toFixed(0)} right=${mb.right.toFixed(0)}`);
  chk('手机无 JS 报错', errors.length === 0, errors.slice(0, 3).join(' | '));
  await ctx.close();
}

await browser.close();
console.log(out.join('\n'));
const fails = out.filter(l => l.startsWith('FAIL')).length;
console.log(`\n${fails === 0 ? 'ALL OK' : fails + ' FAILED'}`);
process.exit(fails ? 1 : 0);
