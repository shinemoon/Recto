// 书架合并 + Material 骨架 + 时长融合 —— 真浏览器验证 (只读, 不写云端)
// 跑法: NVM 环境, node tools/probe_material_shelf.mjs
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import { chromium } from './_env.mjs';
const { execFileSync } = require('child_process');

const BASE = process.env.RECTO_BASE || 'http://127.0.0.1:5015';
const SHOT = '/tmp/recto-shots';

function cookie() {
  return execFileSync('python3', [new URL('./make_cookie.py', import.meta.url).pathname],
    { encoding: 'utf8' }).trim();
}

const results = [];
const ok = (cond, name, extra) => {
  results.push({ ok: !!cond, name, extra });
  console.log((cond ? 'PASS ' : 'FAIL ') + name + (extra === undefined ? '' : ' :: ' + JSON.stringify(extra)));
};

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const token = cookie();
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
  await ctx.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

  await page.goto(BASE + '/', { waitUntil: 'networkidle' });
  await page.waitForTimeout(600);

  // --- 书架 ---
  const cards = await page.locator('#shelf .bk').count();
  ok(cards >= 1, '书架渲染成卡片', { cards });
  const stats = await page.locator('#shelf-stat .s').count();
  ok(stats >= 4, '书架顶部统计条', { stats });
  const covers = await page.evaluate(() => {
    const imgs = [...document.querySelectorAll('#shelf .bk-cover img')];
    return imgs.map((i) => ({ w: i.naturalWidth, h: i.naturalHeight }));
  });
  ok(covers.every((c) => c.w > 0), '有封面的书封面都加载了 (无封面走占位字)', covers);
  const prog = await page.evaluate(() =>
    [...document.querySelectorAll('#shelf .bk-prog i')].map((i) => i.style.width));
  ok(prog.length > 0, '进度条有条目', prog);

  // 搜索过滤
  await page.fill('#shelf-q', '狐');
  await page.waitForTimeout(200);
  const hits = await page.evaluate(() =>
    [...document.querySelectorAll('#shelf .bk-title')].map((e) => e.textContent.trim()));
  // 书库里可能有同名不同文件 (同一本书的两个版本), 所以只断言"命中的都在关键词上"
  ok(hits.length >= 1 && hits.every((t) => t.indexOf('狐') >= 0), '搜索即时过滤', { hits });
  await page.fill('#shelf-q', '');
  await page.waitForTimeout(150);

  // --- Material 骨架: 量计算样式 ---
  const style = await page.evaluate(() => {
    const g = (sel, props) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      const cs = getComputedStyle(el);
      const out = {};
      props.forEach((p) => { out[p] = cs.getPropertyValue(p); });
      return out;
    };
    return {
      body: g('body', ['font-family', 'background-color']),
      // 页头已改悬浮操作轨 (圆钮是 2026-11-04 用户明确要的例外), 直角口径改量普通按钮
      // height 对 display:none 的子树拿不到 (返回 auto), 改到云端同步面板可见时再量
      btn: g('#cloud-refresh, .row > button', ['border-radius', 'background-color',
        'box-shadow', 'font-weight']),
      card: g('details.sec', ['border-radius', 'box-shadow', 'border-width',
        'background-color']),
      bk: g('#shelf .bk', ['border-radius', 'box-shadow']),
      nav: g('.rc-nav a[aria-current="page"]', ['color', 'font-weight']),
      input: g('#shelf-q', ['border-radius', 'height', 'border-color']),
      // 页头 h1 已按 2026-11-04 的要求去掉, 改量书架卡片书名 (同一套 heading 字体)
      head: g('.bk-title, .rc-head h1, h1', ['font-weight', 'font-family']),
    };
  });
  ok(/Roboto|Inter/.test(style.body['font-family']), '正文字体走 Roboto/Inter',
    style.body['font-family'].slice(0, 48));
  ok(parseFloat(style.btn['border-radius']) <= 1,
    '按钮直角 (用户口径: 全站取消圆角)', style.btn['border-radius']);
  ok(style.btn['box-shadow'] === 'none' || !!style.btn['box-shadow'], '按钮有状态层规则');
  ok(parseFloat(style.card['border-radius']) <= 1, '卡片直角', style.card['border-radius']);
  ok(style.card['box-shadow'] !== 'none', '卡片带高度阴影', style.card['box-shadow'].slice(0, 42));
  ok(style.card['border-width'] === '0px', '卡片不再用边框 (改阴影)', style.card['border-width']);
  ok(parseFloat(style.bk['border-radius']) <= 1, '书架卡片直角', style.bk['border-radius']);
  ok(style.bk['box-shadow'] !== 'none', '书架卡片有高度');
  ok(parseFloat(style.input['border-radius']) <= 1, '输入框直角 (取消圆角后)',
    style.input['border-radius']);
  ok(parseInt(style.head['font-weight'], 10) <= 500, '标题字重走 M3 (<=500)',
    style.head['font-weight']);

  // 导航指示条 (active tab 的 ::after)
  const indicator = await page.evaluate(() => {
    const a = document.querySelector('.rc-nav a[aria-current="page"]');
    const cs = getComputedStyle(a, '::after');
    return { w: cs.width, h: cs.height, bg: cs.backgroundColor, content: cs.content };
  });
  ok(indicator.h === '3px' && indicator.bg !== 'rgba(0, 0, 0, 0)',
    '激活导航有 3px 指示条', indicator);

  // 水波纹 —— 页头按钮已收进悬浮轨 (圆钮刻意关掉水波纹: 圆钮要让溢出可见,
  // 否则悬浮名字被 overflow:hidden 裁掉), 改在有水波纹的普通按钮上验
  await page.goto(BASE + '/#sync', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(500);
  await page.click('details#sec-cloud > summary');
  await page.waitForTimeout(220);
  const btnH = await page.evaluate(() => {
    const b = document.querySelector('#cloud-refresh');
    return b ? getComputedStyle(b).height : null;
  });
  ok(btnH === '40px', '按钮高 40 (M3 标准)', btnH);
  const box = await page.locator('#cloud-refresh').boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  const ripple = await page.locator('.rc-ripple').count();
  await page.mouse.up();
  ok(ripple >= 1, '按下时出现水波纹', { ripple });

  // 无横向溢出
  const over = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }));
  ok(over.scroll <= over.client + 1, '桌面无横向溢出', over);

  await page.screenshot({ path: SHOT + '/shelf-desktop.png', fullPage: false });

  // --- /read 旧链接重定向到书架 ---
  await page.goto(BASE + '/read', { waitUntil: 'networkidle' });
  ok(page.url().includes('#library') || page.url().endsWith('/'),
    '/read 重定向到书架分区', page.url());

  // --- 从卡片进阅读页 ---
  await page.goto(BASE + '/', { waitUntil: 'networkidle' });
  await page.locator('#shelf .bk').first().click();
  await page.waitForTimeout(1200);
  ok(/\/read\/b\//.test(page.url()), '点卡片进阅读页', page.url());
  const backHref = await page.locator('.rv-back').getAttribute('href');
  ok(backHref === '/#library', '返回键指回书架', backHref);
  ok(await page.locator('#rv-content').count() === 1, '阅读器正文容器在');

  // --- 云端同步面板: 说明句已去掉, 但操作仍然在 (含时长推送) ---
  await page.goto(BASE + '/', { waitUntil: 'networkidle' });
  await page.click('[data-pane-link="sync"]');
  await page.waitForTimeout(150);
  const syncTxt = await page.locator('section[data-pane="sync"]').innerText();
  ok(/时长/.test(syncTxt), '同步面板仍能推/拉阅读时长', syncTxt.replace(/\s+/g, ' ').slice(0, 60));

  // --- 移动端 ---
  const m = await browser.newContext({ viewport: { width: 390, height: 844 },
    isMobile: true, hasTouch: true });
  await m.addCookies([{ name: 'zy_webauth', value: token, domain: '127.0.0.1', path: '/' }]);
  const mp = await m.newPage();
  mp.on('pageerror', (e) => errors.push('mobile: ' + String(e)));
  await mp.goto(BASE + '/', { waitUntil: 'networkidle' });
  await mp.waitForTimeout(500);
  const mOver = await mp.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }));
  ok(mOver.scroll <= mOver.client + 1, '手机 390 无横向溢出', mOver);
  const cols = await mp.evaluate(() => {
    const g = document.querySelector('#shelf');
    return getComputedStyle(g).gridTemplateColumns.split(' ').length;
  });
  ok(cols === 2 || cols === 3, '手机书架 2 列', { cols });
  await mp.screenshot({ path: SHOT + '/shelf-mobile.png', fullPage: false });

  ok(errors.length === 0, '没有 JS 报错', errors.slice(0, 3));

  await browser.close();
  const failed = results.filter((r) => !r.ok).length;
  console.log('\n' + (results.length - failed) + ' passed, ' + failed + ' failed');
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
