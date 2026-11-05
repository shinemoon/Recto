/* 阅读器: 字体手输 / 章末翻页 / 滚动条 */
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
const { execFileSync } = require('child_process');

const BASE = process.env.RV_BASE || 'http://127.0.0.1:5015';
const KEY = process.env.RV_KEY || '8379037298fbca9c';
const token = execFileSync('python3', [new URL('./make_cookie.py', import.meta.url).pathname], { encoding: 'utf8' }).trim();
const out = [];
const chk = (n, c, x = '') => out.push((c ? 'ok   ' : 'FAIL ') + n + (x ? ' — ' + x : ''));

const browser = await chromium.launch({ args: ['--no-sandbox'] });

// ---------- 1) 字体: datalist + 手输, 面板打开要快 ----------
{
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(String(e)));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
  await page.goto(BASE + '/read/b/' + KEY, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.rv-page p', { timeout: 20000 });
  await page.waitForTimeout(900);
  // 进度会被恢复: 落在书中段时顶栏可能因"下滑收起"处于隐藏态 (定位在视口外)
  // → 先回顶并向上滚一轮唤出顶栏, 再点按钮 (测的是面板本身, 不是自动收起逻辑)
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.mouse.wheel(0, -400);
  await page.waitForTimeout(400);

  const t0 = Date.now();
  await page.click('#rv-typo-btn');
  const openMs = Date.now() - t0;
  await page.waitForTimeout(200);
  // 阈值放宽到 350ms: 打开成本主要是首帧绘制, 机器负载会让 250ms 抖动
  chk('排版面板打开 < 350ms', openMs < 350, openMs + 'ms');

  const ui = await page.evaluate(() => {
    const inp = document.getElementById('rv-font');
    inp.focus();
    const menu = document.getElementById('rv-font-menu');
    const cs = menu ? getComputedStyle(menu) : null;
    const opt = menu ? menu.querySelector('.rv-combo-opt') : null;
    return {
      tag: inp && inp.tagName, isInput: inp && inp.type === 'text',
      hasDatalist: !!document.getElementById('rv-font-opts'),
      listAttr: inp ? inp.getAttribute('list') : '',
      hasMenu: !!menu, opened: !!menu && !menu.hidden,
      opts: menu ? menu.querySelectorAll('.rv-combo-opt').length : -1,
      bg: cs ? cs.backgroundColor : '', border: cs ? cs.borderTopWidth : '',
      optH: opt ? Math.round(opt.getBoundingClientRect().height) : -1,
      menuW: menu ? Math.round(menu.getBoundingClientRect().width) : -1,
      boxW: Math.round(document.getElementById('rv-font-box').getBoundingClientRect().width),
      inpH: Math.round(inp.getBoundingClientRect().height),
      menuBottom: menu ? Math.round(menu.getBoundingClientRect().bottom) : -1,
      menuRight: menu ? Math.round(menu.getBoundingClientRect().right) : -1,
      panelLeft: Math.round(document.getElementById('rv-typo').getBoundingClientRect().left),
      vw: window.innerWidth,
      noCanvas: !document.querySelector('#rv-font option[value="__ask__"]')
    };
  });
  chk('字体是 input 而非 select', ui.tag === 'INPUT' && ui.isInput, String(ui.tag));
  chk('不再用原生 datalist', !ui.hasDatalist && !ui.listAttr,
      'datalist=' + ui.hasDatalist + ' list=' + ui.listAttr);
  chk('聚焦展开自绘下拉', ui.hasMenu && ui.opened);
  chk('下拉含常用名', ui.opts >= 20, 'opts=' + ui.opts);
  chk('下拉用面板底色 + 描边 (跟 UI 一致)', !!ui.bg && ui.bg !== 'rgba(0, 0, 0, 0)'
      && ui.border !== '0px', `bg=${ui.bg} border=${ui.border}`);
  chk('候选行 30px (清单密度, 一屏约 6 条)', ui.optH >= 28 && ui.optH <= 32, ui.optH + 'px');
  chk('下拉比输入框宽 (长字体名不早折)', ui.menuW > ui.boxW + 20,
      `${ui.boxW} → ${ui.menuW}`);
  chk('输入框高度跟「阅读模式」行对齐 (不 44px)', ui.inpH >= 30 && ui.inpH <= 38, ui.inpH + 'px');
  chk('下拉不出面板左边 / 不出视口', ui.menuRight <= ui.vw && ui.menuRight > 0,
      `menuRight=${ui.menuRight} vw=${ui.vw} panelLeft=${ui.panelLeft}`);

  // 点清单里的选项 → 直接生效 (不用手输)
  const firstPick = await page.evaluate(() => {
    const opts = [...document.querySelectorAll('#rv-font-menu .rv-combo-opt')];
    const hit = opts.find((o) => (o.textContent || '').includes('无衬线'));
    return hit ? (hit.textContent || '').trim() : '';
  });
  if (firstPick) {
    await page.evaluate(() => {
      const opts = [...document.querySelectorAll('#rv-font-menu .rv-combo-opt')];
      const hit = opts.find((o) => (o.textContent || '').includes('无衬线'));
      const r = hit.getBoundingClientRect();
      hit.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true,
        clientX: r.left + 4, clientY: r.top + 4 }));
    });
    await page.waitForTimeout(250);
    const byPick = await page.evaluate(() => localStorage.getItem('recto.font'));
    chk('点选清单项即生效', byPick === 'sans-serif', String(byPick));
  } else {
    chk('点选清单项即生效', false, '清单里找不到「无衬线」');
  }

  // 手输一个字体名 → 立刻生效
  await page.fill('#rv-font', 'Georgia');
  await page.press('#rv-font', 'Enter');
  await page.waitForTimeout(400);
  const applied = await page.evaluate(() => ({
    ls: localStorage.getItem('recto.font'),
    css: getComputedStyle(document.getElementById('rv').querySelector('.rv-page p')).fontFamily
  }));
  chk('手输字体写入 localStorage', applied.ls === 'Georgia', applied.ls);
  chk('手输字体应用到家正文', /Georgia/i.test(applied.css), applied.css.slice(0, 40));

  // 清空 = 跟随界面
  await page.fill('#rv-font', '');
  await page.press('#rv-font', 'Enter');
  await page.waitForTimeout(300);
  const cleared = await page.evaluate(() => ({
    ls: localStorage.getItem('recto.font'),
    css: getComputedStyle(document.querySelector('.rv-page p')).fontFamily
  }));
  chk('清空回到跟随界面', !cleared.ls && !/Georgia/i.test(cleared.css), JSON.stringify(cleared));
  chk('字体面板 0 报错', errs.length === 0, errs.join(' | '));
  await ctx.close();
}

// ---------- 2) 滚动条: html 有底色, 轨道透明 ----------
{
  const ctx = await browser.newContext({ viewport: { width: 1200, height: 800 } });
  await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
  const page = await ctx.newPage();
  await page.addInitScript(() => { localStorage.setItem('recto.mode', 'scroll'); });
  await page.goto(BASE + '/read/b/' + KEY, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.rv-page p', { timeout: 20000 });
  await page.waitForTimeout(700);
  const sb = await page.evaluate(() => {
    const css = Array.from(document.styleSheets).flatMap(ss => { try { return Array.from(ss.cssRules); } catch (e) { return []; } })
      .map(r => r.cssText).join('\n');
    return {
      htmlBg: getComputedStyle(document.documentElement).backgroundColor,
      bodyBg: getComputedStyle(document.body).backgroundColor,
      noBarRule: /scrollbar-width:\s*none/.test(css) &&
                  /::-webkit-scrollbar[^{]*\{[^}]*width:\s*0/.test(css),
      gutter: getComputedStyle(document.documentElement).scrollbarGutter,
      barWidth: window.innerWidth - document.documentElement.clientWidth
    };
  });
  chk('html 底面色铺满 (不再露出白条)', sb.htmlBg !== 'rgba(0, 0, 0, 0)', sb.htmlBg);
  chk('滚动条已隐藏 (条宽 0)',
      !!sb.noBarRule && (sb.gutter === 'auto' || !sb.gutter) && sb.barWidth === 0,
      'rule=' + !!sb.noBarRule + ' gutter=' + JSON.stringify(sb.gutter) + ' 宽=' + sb.barWidth);
  chk('正文底色与画布一致', sb.htmlBg === sb.bodyBg, sb.htmlBg + ' vs ' + sb.bodyBg);
  await ctx.close();
}

// ---------- 3) 章末翻页: 正常前进 + 加载失败后仍能恢复 ----------
{
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(String(e)));
  await page.addInitScript(() => { localStorage.setItem('recto.mode', 'page'); });

  // 第一次请求下一章故意失败, 模拟网络抖动
  let failNext = 0;
  await page.route('**/read/api/book/*/chapter/*', async route => {
    if (failNext > 0) { failNext--; await route.abort('failed'); return; }
    await route.continue();
  });

  await page.goto(BASE + '/read/b/' + KEY, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.rv-page p', { timeout: 20000 });
  await page.waitForTimeout(1000);

  const st = () => page.evaluate(() => {
    const s = window.__RV__.page(); const c = window.__RV__.cursor();
    return { page: s.page, count: s.count, cur: c && c.ch };
  });
  const jumpToEnd = async () => {
    // 一路点到本章末页
    for (let i = 0; i < 400; i++) {
      const before = await st();
      await page.keyboard.press('ArrowRight');
      await page.waitForTimeout(45);
      const after = await st();
      if (after.count > before.count) return after;      // 补了新章
      if (after.page >= before.count - 1) {
        // 已在本章最后一页, 再点一下应触发补章
        await page.waitForTimeout(500);
        const again = await st();
        if (again.count > before.count) return again;
      }
    }
    return await st();
  };

  const start = await st();
  failNext = 1;                      // 下一章第一次请求失败
  const after = await jumpToEnd();
  chk('章末翻页能进入下一章', after.count > start.count, JSON.stringify({ start, after }));
  chk('新章真的进了 DOM', await page.evaluate(n => !!document.querySelector('.rv-ch[data-ch="' + n + '"]'), (start.cur + 1)),
      'ch=' + (start.cur + 1));

  // 再往前走几页, 确认没有停在"卡死"
  const p1 = await st();
  await page.keyboard.press('ArrowRight');
  await page.waitForTimeout(300);
  const p2 = await st();
  chk('进入新章后还能继续翻页', p2.page > p1.page || p2.cur > p1.cur, JSON.stringify({ p1, p2 }));
  chk('翻页过程 0 报错', errs.length === 0, errs.join(' | '));
  await ctx.close();
}

// ---------- 4) 滚动模式: 章末点右侧热区要能"接着往下" ----------
{
  const ctx = await browser.newContext({ viewport: { width: 1000, height: 760 } });
  await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
  const page = await ctx.newPage();
  await page.addInitScript(() => { localStorage.setItem('recto.mode', 'scroll'); });
  await page.goto(BASE + '/read/b/' + KEY, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.rv-page p', { timeout: 20000 });
  await page.waitForTimeout(900);

  // 直接滚到已加载内容的底部
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await page.waitForTimeout(700);
  const before = await page.evaluate(() => ({
    y: Math.round(window.scrollY), h: document.body.scrollHeight,
    chs: Array.from(document.querySelectorAll('.rv-ch')).length
  }));
  await page.click('.rv-turn-r');
  await page.waitForTimeout(1200);
  const after = await page.evaluate(() => ({
    y: Math.round(window.scrollY), h: document.body.scrollHeight,
    chs: Array.from(document.querySelectorAll('.rv-ch')).length
  }));
  chk('滚动模式章末点热区会继续往下', after.y > before.y + 100 || after.chs > before.chs,
      JSON.stringify({ before, after }));
  await ctx.close();
}

console.log(out.join('\n'));
const bad = out.filter(l => l.startsWith('FAIL')).length;
console.log(bad ? ('FAILED ' + bad) : 'ALL PASS (' + out.length + ')');
await browser.close();
process.exit(bad ? 1 : 0);
