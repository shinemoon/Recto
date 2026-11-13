/* 书宠 d20 战报与判定的展示探针 (生产 5015)。
 *
 * 验的是 2026-11-13 那轮: 交手与"路上的坎"都按 d20 掷骰, 战报可以展开看明细。
 *
 *   · 人物卡里有「交手数据 · d20」折叠区: 护体 / 命中 / 伤害骰 / 先攻 / 豁免,
 *     每一项都拆账 (根骨 / 技能 / 禀赋 的 chip)
 *   · 战报: 摘要几拍 + 「完整战报 · N 拍 · M 次掷骰」可展开;
 *     展开后每一拍带掷骰明细 (d20 面 + 加值拆账 + 总数 vs 难度 + 结论) 与伤害骰
 *   · 判定: 「路上的坎」里带 白/红 标记、下场一句、以及自己的掷骰行
 *   · 390px 下掷骰行换成两行排布, 页面不横向溢出
 *   · 0 JS 报错
 *
 * 战报/判定的数据用**后端同一份形状的合成 payload** 注入渲染函数 —— 生产库里
 * 老的游历记录没有新字段, 直接等真数据会把"没数据"误判成"渲染坏了"。
 * 形状与 `duel.resolve()` / `checks.resolve()` 的返回一致。
 *
 * 跑法: cd tools && node probe_pet_d20.mjs
 * 环境: RECTO_BASE (默认 5015)
 */
import { chromium, cookie } from './_env.mjs';

const BASE = process.env.RECTO_BASE || '5015';
const URL = `http://127.0.0.1:${BASE}`;

let PASS = 0, FAIL = 0;
function ok(cond, name) {
  if (cond) { PASS++; console.log('PASS', name); }
  else { FAIL++; console.log('FAIL', name); }
}

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1360, height: 950 } });
await ctx.addCookies([{
  name: 'zy_webauth', value: cookie(), domain: '127.0.0.1', path: '/',
}]);
const page = await ctx.newPage();
const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(String(e)));
page.on('console', (m) => { if (m.type() === 'error') pageErrors.push(m.text()); });

await page.goto(`${URL}/pet/`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(1000);

// ── 1. 人物卡: 交手数据 (d20) ─────────────────────────
const fold = await page.$('#fold-card-d20');
ok(!!fold, '人物卡里有「交手数据 · d20」折叠区');
ok(await page.$eval('#fold-card-d20', (e) => !e.open), '默认是折起的');

await page.$eval('#fold-card-d20', (e) => { e.open = true; });
await page.waitForTimeout(120);

const d20rows = await page.$$eval('#char-d20 .cs-d20-row', (rows) =>
  rows.map((r) => r.textContent.replace(/\s+/g, ' ').trim()));
ok(d20rows.length >= 5, `有 5 行以上交手数值 (${d20rows.length})`);
for (const key of ['护体', '命中', '伤害', '先攻', '豁免']) {
  ok(d20rows.some((t) => t.startsWith(key)), `有「${key}」那一行`);
}
ok(d20rows.some((t) => /卸力/.test(t)), '伤害行里有卸力');
ok(d20rows.some((t) => /大成功/.test(t)), '有暴击区间说明');

const chips = await page.$$eval('#char-d20 .d20-mod', (ns) =>
  ns.map((n) => n.getAttribute('data-src')));
ok(chips.length >= 3, `命中/护体带着拆账的 chip (${chips.length} 个)`);
ok(chips.some((s) => s === 'attr') && chips.some((s) => s === 'skill'),
   `拆账里有根骨与技能 (${[...new Set(chips)].join(',')})`);

const rules = await page.$eval('#char-d20-rules', (e) => e.textContent);
ok(/d20/.test(rules) && /难度/.test(rules), '折叠区里有规则说明');

// ── 2. 战报渲染 (注入一份后端同形的 payload) ────────────
const added = await page.evaluate(() => {
  const duel = {
    foe: { name: '一个带斗笠的刀客', why: '开口就要你的行囊', arena: '渡口',
           weapon_cn: '一把刀', level: 9, hp: 0, hp_max: 58 },
    result: 'win', result_cn: '胜',
    summary: '赢了 —— 一个带斗笠的刀客 先撑不住',
    moves: ['一气呵成'], foe_moves: ['力劈华山'],
    hp: { from: 75, to: 41, max: 75 }, neili: { from: 40, to: 28, max: 40 },
    fame: 3, rolls: 14,
    rounds: [
      { r: 1, side: 'me', move: '', kind: 'hit', text: '小墨 一剑打中 一个带斗笠的刀客, 伤 17, 正中要害',
        dmg: 17, self: 0, effect: '', hp: { a: 75, b: 41 },
        rolls: [{ label: '攻击检定', kind: 'attack', kind_cn: '攻击', d20: 19, d20s: [19],
                  mods: [{ name: '身法 15', value: 2, src: 'attr' },
                         { name: '剑法 6 级', value: 3, src: 'skill' }],
                  mod: 5, bonus: 0, total: 24, dc: 15, dc_label: '不易',
                  success: true, crit: true, fumble: false, degree: 'crit' }],
        dice: { count: 2, sides: 8, faces: [6, 5], sum: 11, text: '2d8 [6, 5] = 11' } },
      { r: 1, side: 'foe', move: '力劈华山', kind: 'miss', text: '一个带斗笠的刀客 一招「力劈华山」出手, 被 小墨 让开了',
        dmg: 0, self: 0, effect: '', hp: { a: 75, b: 41 },
        rolls: [{ label: '运功 · 力劈华山', kind: 'perform', kind_cn: '运功', d20: 4, d20s: [4],
                  mods: [{ name: '招式 力劈华山 1 级', value: 1, src: 'skill' }],
                  mod: 1, bonus: 0, total: 5, dc: 12, dc_label: '不易',
                  success: false, crit: false, fumble: false, degree: 'fail' }] },
      { r: 2, side: 'me', move: '一气呵成', kind: 'hit', text: '小墨 用「一气呵成」命中 一个带斗笠的刀客, 伤 21, 定住对方 3 拍',
        dmg: 21, self: 0, effect: 'damage', hp: { a: 75, b: 20 },
        rolls: [{ label: '攻击检定', kind: 'attack', kind_cn: '攻击', d20: 12, d20s: [12],
                  mods: [{ name: '身法 15', value: 2, src: 'attr' },
                         { name: '剑法 6 级', value: 3, src: 'skill' },
                         { name: '绝招加值', value: 2, src: 'skill' }],
                  mod: 7, bonus: 2, total: 21, dc: 15, dc_label: '不易',
                  success: true, crit: false, fumble: false, degree: 'success' },
                 { label: '定身豁免', kind: 'save', kind_cn: '豁免', d20: 6, d20s: [6],
                  mods: [{ name: '根骨 13', value: 1, src: 'attr' }],
                  mod: 1, bonus: 0, total: 7, dc: 13, dc_label: '不易',
                  success: false, crit: false, fumble: false, degree: 'fail' }],
        dice: { count: 1, sides: 8, faces: [7], sum: 7, text: '1d8 [7] = 7' } },
      { r: 3, side: 'me', move: '', kind: 'fail', text: '小墨 力用过了, 招式散掉, 反伤 2',
        dmg: 0, self: 2, effect: '', hp: { a: 73, b: 0 },
        rolls: [{ label: '攻击检定', kind: 'attack', kind_cn: '攻击', d20: 1, d20s: [1],
                  mods: [{ name: '身法 15', value: 2, src: 'attr' }],
                  mod: 2, bonus: 0, total: 3, dc: 15, dc_label: '不易',
                  success: false, crit: false, fumble: true, degree: 'fumble' }] }],
    initiative: { order: ['me', 'foe'], text: '先攻: 我方先手 (d20 20 vs 9)',
                  me: { total: 3 }, foe: { total: 1 } },
    fighters: {
      me: { name: '小墨', hp: 41, hp_max: 75, ac: 13, attack: 5,
            damage: { text: '1d8+18', weapon: '剑法' }, pierce: 4,
            init: { total: 3 }, crit_range: 19 },
      foe: { name: '一个带斗笠的刀客', hp: 0, hp_max: 58, ac: 15, attack: 6,
             damage: { text: '1d10+16', weapon: '刀法' }, pierce: 5,
             init: { total: 1 }, crit_range: 20 } },
    rulebook: ['一切判定都是 d20'],
  };
  const checks = [
    { label: '辨路', target: '在泥瓶巷赶一段不熟的路', dc: 11, dc_label: '寻常',
      white: true, success: true, degree_cn: '成功', degree: 'success',
      outcome: '抄了近道, 省下半日脚程', bonus: 0, total: 19, d20: 15,
      mods: [{ name: '身法 15', value: 2, src: 'attr' },
             { name: '轻功 4 级', value: 2, src: 'skill' }], kind: 'skill' },
    { label: '看破', target: '辨出眼前这件事里谁在说谎（陈平安）', dc: 20, dc_label: '艰险',
      white: false, success: false, degree_cn: '失败', degree: 'fail',
      outcome: '被他绕了过去, 事后才想明白', bonus: 0, total: 12, d20: 9,
      mods: [{ name: '悟性 13', value: 1, src: 'attr' },
             { name: '观气 3 级', value: 2, src: 'skill' }], kind: 'skill' },
  ];
  const box = document.createElement('div');
  box.id = 'probe-d20';
  box.className = 'rc-pet';
  box.style.cssText = 'max-width:820px;margin:20px auto;padding:0 16px';
  box.innerHTML = window.duelBlock(duel) + window.checksBlock(checks);
  document.body.appendChild(box);
  return !!window.duelBlock && !!window.checksBlock;
});
ok(added, '页面里有 duelBlock / checksBlock (战报与判定都走同一套渲染)');

// 战报头部
const hd = await page.$eval('#probe-d20 .adv-duel-h', (e) => e.textContent.replace(/\s+/g, ' '));
ok(/胜/.test(hd) && /带斗笠的刀客/.test(hd), `战报头有结果与对手 (${hd.slice(0, 24)})`);
ok(/渡口/.test(hd), '战报头有场地');

// 可展开的完整战报
const log = await page.$('#probe-d20 .duel-log');
ok(!!log, '有「完整战报」折叠块');
const sumTxt = await page.$eval('#probe-d20 .duel-log > summary', (e) => e.textContent);
ok(/完整战报 · 4 拍 · 14 次掷骰/.test(sumTxt), `摘要写清了几拍几次掷骰 (${sumTxt})`);
ok(await page.$eval('#probe-d20 .duel-log', (e) => !e.open), '默认折起 (不铺开长文)');

await page.$eval('#probe-d20 .duel-log', (e) => { e.open = true; });
await page.waitForTimeout(120);
const rounds = await page.$$('#probe-d20 .duel-round');
ok(rounds.length === 4, `展开后 4 拍都在 (${rounds.length})`);

const rollRows = await page.$$eval('#probe-d20 .duel-log .d20-row', (rows) =>
  rows.map((r) => ({
    cls: r.className,
    face: (r.querySelector('.d20-face') || {}).textContent,
    mods: [...r.querySelectorAll('.d20-mod')].map((m) => m.textContent.trim()),
    tot: (r.querySelector('.d20-tot') || {}).textContent,
    v: (r.querySelector('.d20-v') || {}).textContent,
  })));
ok(rollRows.length === 5, `掷骰明细逐条列出 (${rollRows.length} 条: 3 攻击 + 1 运功 + 1 豁免)`);
ok(rollRows.some((r) => /crit/.test(r.cls)) && rollRows.some((r) => /fumble/.test(r.cls)),
   '大成功与大失败各有自己的样式');
ok(rollRows[0].face === '19' && /vs 15/.test(rollRows[0].tot), '第一行是 d20 面 + vs 难度');
ok(rollRows[0].mods.some((t) => /身法 15/.test(t)) && rollRows[0].mods.some((t) => /剑法 6 级/.test(t)),
   `加值拆开写 (${rollRows[0].mods.join(' / ')})`);
ok(rollRows.some((r) => /豁免/.test(r.v) || /过|没过/.test(r.v)), '豁免行有自己的结论词');
ok(rollRows[0].v === '大成功', `大成功结论 (${rollRows[0].v})`);
ok(rollRows[rollRows.length - 1].v === '大失败', '大失败结论');

const diceLines = await page.$$eval('#probe-d20 .d20-dice', (ns) => ns.map((n) => n.textContent));
ok(diceLines.length === 2 && /2d8/.test(diceLines[0]), `伤害骰带面值 (${diceLines.join(' | ')})`);

// 交手数据
const stats = await page.$('#probe-d20 .duel-stats');
ok(!!stats, '有「交手数据与规则」折叠块');
await page.$eval('#probe-d20 .duel-stats', (e) => { e.open = true; });
await page.waitForTimeout(100);
const sides = await page.$$eval('#probe-d20 .duel-side', (ns) =>
  ns.map((n) => n.textContent.replace(/\s+/g, ' ').trim()));
ok(sides.length === 2, '两边各一行交手数据');
ok(/护体 13/.test(sides[0]) && /1d8\+18/.test(sides[0]), `我方护体与伤害骰 (${sides[0]})`);
ok(/护体 15/.test(sides[1]), '对方护体也在');

// ── 3. 判定 (路上的坎) ────────────────────────────────
const checkBox = await page.$('#probe-d20 .adv-checks');
ok(!!checkBox, '有「路上的坎」块');
const checkTxt = await page.$eval('#probe-d20 .adv-checks', (e) => e.textContent.replace(/\s+/g, ' '));
ok(/白检定/.test(checkTxt), '白检定有标记');
ok(/红检定/.test(checkTxt) && /一次定生死/.test(checkTxt), '红检定写明一次定生死');
ok(/抄了近道/.test(checkTxt) && /被他绕了过去/.test(checkTxt), '两道坎都带照实该写的下场');
ok(/在泥瓶巷/.test(checkTxt), '坎落在具体的地方上');

const ckRows = await page.$$('#probe-d20 .adv-check .d20-row');
ok(ckRows.length === 2, `每道坎都带自己的掷骰行 (${ckRows.length})`);

// ── 4. 390px 窄屏 ────────────────────────────────────
await page.setViewportSize({ width: 390, height: 844 });
await page.waitForTimeout(220);
const overflow = await page.evaluate(() => ({
  doc: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  probe: (() => {
    const b = document.getElementById('probe-d20');
    return b ? b.scrollWidth - b.clientWidth : -1;
  })(),
}));
ok(overflow.doc <= 1, `页面不横向溢出 (${overflow.doc})`);
ok(overflow.probe <= 1, `战报块自己不横向溢出 (${overflow.probe})`);

const grid = await page.$eval('#probe-d20 .duel-log .d20-row', (e) => {
  const cs = getComputedStyle(e);
  return { areas: cs.gridTemplateAreas || '', cols: cs.gridTemplateColumns };
});
ok(/face/.test(grid.areas), `窄屏掷骰行换成两行排布 (${grid.areas})`);

ok(pageErrors.length === 0, `0 JS 报错 (${pageErrors.slice(0, 2).join(' | ')})`);

await browser.close();
console.log('');
console.log('='.repeat(46));
console.log(`PASS ${PASS}  FAIL ${FAIL}`);
process.exit(FAIL ? 1 : 0);
