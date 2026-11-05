import { chromium } from './_env.mjs';
import { REPO } from './_env.mjs';
import { execSync } from 'node:child_process';
import { skinSnapshot, restoreSkin } from './_skins.mjs';

/**
 * 按钮群错落回归探针。
 *
 * 校验的是「一排按钮不应该同色」这条视觉规则有没有在全站生效:
 *   1 每组 (≥2 个按钮/按钮式链接) 里相邻按钮底色必须有差 — 差 < 12 (sRGB 距离) 记失败
 *   2 每个按钮的文字对底色对比度 ≥ 3.0 — 否则错落换色把字换糊了
 *   3 皮肤卡组 (div.skin-grid) 豁免: 那是有意做的"同尺寸色块", 不该错落
 *
 * 跑法: node tools/probe_btn_groups.mjs [--mobile]
 * 六套皮肤全扫 (含两套深色), 就是怕只调好浅色、深色一塌。
 */
const base = process.env.RECTO_BASE || 'http://127.0.0.1:5015';
const cookieVal = execSync(
  'cd ' + REPO + ' && PYTHONPATH=src python3 -c "from recto.app import webauth;'
  + 'print(webauth.set_cookie(\'recto\').split(\';\')[0].split(\'=\',1)[1])"'
).toString().trim();

const SKINS = ['material', 'material-dark', 'paper', 'sepia', 'slate', 'mist'];
const ALLOW_SAME = ['.skin-grid', '.rv-btns', '.rc-rail', '.rv-note-item .m', '.m '];  // 有意不用错落的组:
//   .skin-grid   皮肤卡 = 一组同尺寸色块, 故意同色
//   .rv-btns     阅读器工具栏 = 图标按钮, 统一透明才对
//   .rc-rail     书架右侧悬浮圆钮 = 两个同形图标, 统一外观才对 (名字在 hover 才出)
//   .rv-note-item .m  抽屉里「编辑 / 删除」小字操作, 靠字色区分

const browser = await chromium.launch();

const MEASURE = () => {
  const toRgb = (s) => {
    const m = s.match(/rgba?\(([^)]+)\)/);
    if (m) {
      const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
      return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
    }
    const c = s.match(/color\(srgb ([^)]+)\)/);
    if (c) {
      const p = c[1].split(/[ /]+/).filter(Boolean).map(Number);
      return { r: p[0] * 255, g: p[1] * 255, b: p[2] * 255, a: 1 };
    }
    return null;
  };
  const lum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  };
  const dist = (a, b) => Math.sqrt((a.r - b.r) ** 2 + (a.g - b.g) ** 2 + (a.b - b.b) ** 2);
  const contrast = (a, b) => {
    const l1 = lum(a), l2 = lum(b);
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
  };
  const pageBg = toRgb(getComputedStyle(document.body).backgroundColor);
  const groups = new Map();
  for (const b of document.querySelectorAll('button, a.btn')) {
    if (!b.offsetParent) continue;
    const par = b.parentElement;
    if (!groups.has(par)) groups.set(par, []);
    groups.get(par).push(b);
  }
  const out = [];
  for (const [par, bs] of groups) {
    if (bs.length < 2) continue;
    const info = bs.map((b) => {
      const cs = getComputedStyle(b);
      let bg = toRgb(cs.backgroundColor);
      if (!bg || bg.a < 1) bg = pageBg;
      return {
        label: b.id || b.textContent.trim().slice(0, 8),
        cls: String(b.className || ''),
        bg,
        fg: toRgb(cs.color),
      };
    });
    // 可区分性 = 底色差 或 文字色差 (表格行里的小操作靠字色分主次)
    let minAdj = 999;
    for (let i = 1; i < info.length; i++) {
      const d = Math.max(dist(info[i - 1].bg, info[i].bg), dist(info[i - 1].fg, info[i].fg));
      minAdj = Math.min(minAdj, d);
    }
    out.push({
      where: par.tagName.toLowerCase() + '.' + String(par.className || '').split(/\s+/).join('.'),
      n: bs.length,
      minAdj: Math.round(minAdj),
      minContrast: Math.round(Math.min(...info.map(x => contrast(x.fg, x.bg))) * 10) / 10,
      cols: [...new Set(info.map(x => `rgb(${Math.round(x.bg.r)},${Math.round(x.bg.g)},${Math.round(x.bg.b)})`))],
    });
  }
  return { overflow: document.documentElement.scrollWidth - window.innerWidth, groups: out };
};

const PAGES = ['/', '/#sync', '/admin/', '/admin/?pane=excerpts', '/admin/?pane=books',
  '/admin/?pane=tools', '/appearance', '/corpus/'];

async function sweep(viewport, skins) {
  const ctx = await browser.newContext({ viewport });
  await ctx.addCookies([{ name: 'zy_webauth', value: cookieVal, domain: '127.0.0.1', path: '/' }]);
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(String(e)));
  const setSkin = async (skin) => {
    await page.evaluate(async (s) => {
      await fetch('/api/appearance', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ skin: s }),
      });
    }, skin);
  };
  await page.goto(base + '/', { waitUntil: 'domcontentloaded' });
  const pages = [...PAGES];
  const readHref = await page.evaluate(() => {
    const a = document.querySelector('a[href^="/read/b/"]');
    return a ? a.getAttribute('href') : null;
  });
  if (readHref) pages.push(readHref);

  const fails = [];
  const summary = [];
  for (const skin of skins) {
    await page.goto(base + '/', { waitUntil: 'domcontentloaded' });
    await setSkin(skin);
    for (const p of pages) {
      await page.goto(base + p, { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(300);
      const r = await page.evaluate(MEASURE);
      if (r.overflow > 1) fails.push(`${skin} ${p} 横向溢出 ${r.overflow}px`);
      for (const g of r.groups) {
        const allowed = ALLOW_SAME.some(a => ('.' + g.where.split('.').slice(1).join('.')).includes(a));
        if (!allowed && g.minAdj < 12) fails.push(`${skin} ${p} ${g.where} n=${g.n} 相邻同色 adj=${g.minAdj}`);
        if (g.minContrast < 3.0) fails.push(`${skin} ${p} ${g.where} 对比度 ${g.minContrast} < 3.0`);
        summary.push({ skin, page: p, ...g });
      }
    }
  }
  await ctx.close();
  return { fails, summary, errs };
}

// 探针会切皮肤 —— 先记住用户原值, 扫完无条件还原 (不还原就是"重启后外观被重置"的元凶)
const snap = await skinSnapshot();
let desktop, mobile;
try {
  desktop = await sweep({ width: 1400, height: 900 }, SKINS);
  mobile = await sweep({ width: 390, height: 844 }, ['material', 'material-dark']);
} finally {
  await restoreSkin(snap);
}

const fails = [...desktop.fails, ...mobile.fails];
const errs = [...desktop.errs, ...mobile.errs];
const groups = desktop.summary.length + mobile.summary.length;
console.log(JSON.stringify({
  groups,
  fails: fails.slice(0, 40),
  errs: errs.slice(0, 10),
  sample: desktop.summary.filter(g => g.skin === 'material').map(g => ({
    page: g.page, where: g.where, n: g.n, adj: g.minAdj, ctr: g.minContrast, cols: g.cols,
  })),
}, null, 1));
if (fails.length || errs.length) process.exitCode = 1;
await browser.close();
