/* 书宠 —— 真跑一遍: 存配置 → 模型库增删 → 状态变就绪。 */
import { chromium, cookie, REPO } from './_env.mjs';
import { execFileSync } from 'child_process';
import path from 'path';

const BASE = process.env.RECTO_BASE || '5015';
const URL = `http://127.0.0.1:${BASE}`;

let PASS = 0, FAIL = 0;
function ok(cond, name) {
  if (cond) { PASS++; console.log('PASS', name); }
  else { FAIL++; console.log('FAIL', name); }
}

// 探针会真写生产配置 —— 收尾一律还原 (跟其它进度类探针同一个规矩)
function resetPetConfig() {
  try {
    execFileSync('python3', [path.join(REPO, 'tools', 'reset_pet_config.py')],
      { encoding: 'utf8', cwd: REPO });
  } catch (e) {
    console.log('WARN 还原失败:', e.message);
  }
}

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
await ctx.addCookies([{ name: 'zy_webauth', value: cookie(), domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
const errs = [];
page.on('pageerror', (e) => errs.push(String(e)));
page.on('dialog', (d) => d.accept());

await page.goto(`${URL}/pet/`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(600);

// ── 存模型配置 ────────────────────────────────────
await page.fill('#llm-base', 'https://probe.example/v1');
await page.fill('#llm-model', 'probe-model');
await page.fill('#llm-key', 'sk-probe-key-123456');
await page.click('#btn-llm-save');
await page.waitForFunction(() => {
  const s = document.querySelector('#llm-status');
  return s && s.textContent.includes('已保存');
}, { timeout: 8000 }).catch(() => {});
const llmStatus = await page.$eval('#llm-status', (e) => e.textContent.trim());
ok(llmStatus.includes('已保存'), `保存模型配置: ${llmStatus}`);

// key 框被清空 (不留在 DOM 里)
const keyAfter = await page.$eval('#llm-key', (e) => e.value);
ok(keyAfter === '', '保存后 key 输入框被清空');

// placeholder 显示脱敏值
await page.waitForTimeout(300);
const ph = await page.$eval('#llm-key', (e) => e.placeholder);
ok(ph.includes('已保存') && !ph.includes('sk-probe-key'), `placeholder 是脱敏串: ${ph}`);

// ── 状态变就绪 ────────────────────────────────────
await page.waitForFunction(() => {
  const p = document.querySelector('#pet-state');
  return p && p.textContent.includes('已就绪');
}, { timeout: 8000 }).catch(() => {});
const state = await page.$eval('#pet-state', (e) => e.textContent.trim());
ok(state === '已就绪', `状态变「已就绪」: ${state}`);
const stateLine = await page.$eval('#pet-state-line', (e) => e.textContent.trim());
ok(stateLine.length > 0, `状态行有文案: ${stateLine}`);

// ── 人格 ──────────────────────────────────────────
await page.fill('#pet-name-input', '探针宠');
await page.fill('#pet-style', '话少, 有点冷');
await page.fill('#pet-chattiness', '4');
await page.click('#btn-persona-save');
await page.waitForFunction(() => {
  const s = document.querySelector('#persona-status');
  return s && s.textContent.includes('已保存');
}, { timeout: 8000 }).catch(() => {});
const pStatus = await page.$eval('#persona-status', (e) => e.textContent.trim());
ok(pStatus.includes('已保存'), `保存人格: ${pStatus}`);

// 预览区的名字跟着变
await page.waitForTimeout(400);
const previewName = await page.$eval('#pet-name', (e) => e.textContent.trim());
ok(previewName === '探针宠', `预览区名字跟着变: ${previewName}`);

// ── 模型库 ────────────────────────────────────────
await page.click('#btn-model-add');
await page.waitForTimeout(200);
const rows = await page.$$eval('#model-rows tr', (trs) => trs.length);
ok(rows === 1, `模型库新增一行 (${rows} 行)`);

// 填这一行
const inputs = await page.$$('#model-rows tr:first-child input[data-f]');
await inputs[0].fill('probe-m');      // id
await inputs[1].fill('探针模型');       // name
await inputs[4].fill('probe-model');   // model
const keyInput = await page.$('#model-rows tr:first-child input[data-f="api_key"]');
await keyInput.fill('sk-lib-key-999');
await page.click('#btn-models-save');
await page.waitForFunction(() => {
  const s = document.querySelector('#models-status');
  return s && s.textContent.includes('已保存');
}, { timeout: 8000 }).catch(() => {});
const mStatus = await page.$eval('#models-status', (e) => e.textContent.trim());
ok(mStatus.includes('已保存'), `保存模型库: ${mStatus}`);

// 「使用」→ llm 段被换掉
await page.click('#model-rows tr:first-child button[data-act="use"]');
await page.waitForTimeout(800);
const modelVal = await page.$eval('#llm-model', (e) => e.value);
ok(modelVal === 'probe-model', `「使用」把模型套进主表单: ${modelVal}`);

// 删掉
await page.click('#model-rows tr:first-child button[data-act="del"]');
await page.waitForTimeout(700);
const rows2 = await page.$$eval('#model-rows tr', (trs) => trs.length);
ok(rows2 === 0, `删除后清空 (${rows2} 行)`);

ok(errs.length === 0, `无 JS 报错 (${errs.length}): ${errs.slice(0, 2).join(' | ')}`);

await browser.close();
resetPetConfig();
console.log(`\n${PASS} passed, ${FAIL} failed`);
process.exit(FAIL ? 1 : 0);
