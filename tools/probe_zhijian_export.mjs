// 探针: 工具面板「导出到纸间」表单 —— 端到端 (server 造 payload + 服务器代发)
//
// 覆盖两件事:
//   1. /admin/api/export-zhijian 必须返回 JSON (而不是 404 HTML → 前端报 "JSON parse failed")
//   2. 从公网域名打开控制台时浏览器不能直连私网 IP, 必须能落到 /admin/api/zhijian-send 代发
//
// 跑法: RECTO_BASE=http://127.0.0.1:5015 ZY_DEVICE_IP=192.168.0.119 node tools/probe_zhijian_export.mjs
import { chromium, REPO, MAKE_COOKIE, cookie } from './_env.mjs';
import { execFileSync } from 'child_process';

const BASE = process.env.RECTO_BASE || 'http://127.0.0.1:5015';
const DEV_IP = process.env.ZY_DEVICE_IP || '';
const BOOK = process.env.ZY_BOOK_FILTER || '奋斗吧';

const out = [];
let fail = 0;
function ck(name, ok, extra) {
  out.push(`${ok ? 'PASS' : 'FAIL'} ${name}${extra ? ' — ' + extra : ''}`);
  if (!ok) fail++;
}

const tok = execFileSync('python3', [MAKE_COOKIE], { cwd: REPO }).toString().trim().split('\n').pop();

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
await ctx.addCookies([{ name: 'zy_webauth', value: tok.split('=').pop(), domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
const errs = [];
page.on('pageerror', (e) => errs.push(String(e)));

await page.goto(BASE + '/admin/?pane=tools', { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#zhijian-form', { timeout: 15000 });
ck('工具面板有导出表单', true);
ck('表单带安全上限输入', await page.locator('input[name="safety_cap"]').count() === 1);

// ① 直接打接口: 必须是 JSON (这是用户报的 "JSON parse failed" 的直接判据)
const r = await page.evaluate(async (book) => {
  const fd = new FormData();
  fd.append('host', '1.2.3.4'); fd.append('port', '8080'); fd.append('book_name', book);
  const res = await fetch('/admin/api/export-zhijian', { method: 'POST', body: fd });
  const text = await res.text();
  let json = null; try { json = JSON.parse(text); } catch (_) {}
  return { status: res.status, ctype: res.headers.get('content-type'), isJson: !!json, ok: json && json.ok, total: json && json.total };
}, BOOK);
ck('导出接口返回 JSON (非 HTML)', r.isJson, `status=${r.status} ctype=${r.ctype}`);
ck('导出接口 ok=true 且命中 > 0', !!r.ok && r.total > 0, `total=${r.total}`);

// ② 代发接口: 非法 payload 要明确报错, 不是 500 白页
const bad = await page.evaluate(async () => {
  const fd = new FormData(); fd.append('host', '127.0.0.1'); fd.append('port', '9'); fd.append('payload', 'not-json');
  const res = await fetch('/admin/api/zhijian-send', { method: 'POST', body: fd });
  return { status: res.status, json: await res.json().catch(() => null) };
});
ck('代发接口对坏 payload 返回 JSON 报错', bad.status === 400 && bad.json && bad.json.ok === false, `status=${bad.status}`);

// ③ 真表单: 填设备 IP 后点推送, 等日志出现结论 (直连或代发)
if (DEV_IP) {
  await page.fill('#zh-host', DEV_IP);
  await page.fill('#zh-port', process.env.ZY_DEVICE_PORT || '8080');
  await page.fill('input[name="book_name"]', BOOK);
  await page.click('#zhijian-form button[type="submit"]');
  await page.waitForFunction(() => {
    const el = document.getElementById('zh-log');
    return el && /=== 完成 ===/.test(el.textContent || '');
  }, null, { timeout: 60000 });
  const log = await page.textContent('#zh-log');
  const status = await page.textContent('#zh-status');
  ck('提交后日志有结论', /=== 完成 ===|设备无响应/.test(log));
  const asleep = /设备无响应/.test(log);
  // 设备被系统冻结 (端口能连、HTTP 不应答) 属于环境问题, 不算代码回归
  ck(asleep ? '设备在线 (SKIP: 手机 App 未在前台)' : '推送成功 (代发或直连)', asleep || /✓/.test(log), status.trim());
  if (!asleep) ck('失败清单为空', !/失败 \d+ 本/.test(log), log.split('\n').filter(Boolean).slice(-3).join(' | ').slice(0, 200));
}

ck('无 JS 报错', errs.length === 0, errs.join(' | ').slice(0, 200));

await browser.close();
console.log(out.join('\n'));
console.log(fail ? `\n${fail} FAIL` : '\nALL PASS');
process.exit(fail ? 1 : 0);
