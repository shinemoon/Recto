/* 书宠 (陪读精灵) —— 云阅 Recto 原生分区
 *
 * 接口前缀 (/pet) 由 PET_BASE 负责。新增接口一律写 api('/api/xxx')。
 */
'use strict';

const $ = (s) => document.querySelector(s);

const state = {
  models: [],
  activeModelId: '',
  persona: {},
  companion: {},
  ready: false,
  char: null,
  schema: null,
  story: null,
};

/* 顶层 const 不是 window 的属性 —— 探针要读现状, 这里挂一份。
   (经典脚本里 window.state = state 只是多一个引用, 不影响页面行为) */
window.state = state;

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

function setStatus(el, msg, kind) {
  if (!el) return;
  el.textContent = msg || '';
  el.className = 'status' + (kind ? ' ' + kind : '');
}

const PET_BASE = window.RC_PET_BASE || '/pet';

/* 共用零件 (交手战报 / 判定 / 来处 / 类型徽标) —— 与「故事档案」页同一份,
   见 pet-shared.js。两个页面各自复制一份就会各自漂移。 */
const RCP = window.RCPetUI;
if (!RCP) {
  console.error('pet-shared.js 没加载 —— 战报/判定/来处会渲染不出来');
}

async function api(url, opts) {
  const res = await fetch(PET_BASE + url, Object.assign({
    headers: { 'Content-Type': 'application/json' }
  }, opts || {}));
  if (res.status === 401) {
    location.href = '/login?next=' + encodeURIComponent(location.pathname);
    throw new Error('未登录');
  }
  if (res.status === 403) {
    location.href = '/first-run';
    throw new Error('请先修改初始口令');
  }
  const txt = await res.text();
  let data;
  try { data = txt ? JSON.parse(txt) : {}; } catch (e) { data = { error: txt.slice(0, 300) }; }
  if (!res.ok) {
    const msg = (data && (data.error || data.detail)) || ('HTTP ' + res.status);
    const err = new Error(msg);
    err.status = res.status;
    err.data = data || {};
    throw err;
  }
  return data;
}

/* ───────────────── 行囊与银钱 (药 / 吃食 / 兵器 / 护具 / 佩物) ───────────────── */

function bagRow(r, mode) {
  const eff = r.effect ? '<span class="bag-eff">' + esc(r.effect) + '</span>' : '';
  const desc = r.desc ? '<span class="bag-desc">' + esc(r.desc) + '</span>' : '';
  const ops = [];
  if (mode === 'bag') {
    if (r.usable) ops.push('<button class="mini" data-bag-act="use" data-item="' + esc(r.id) + '">用</button>');
    if (r.wearable) ops.push('<button class="mini" data-bag-act="wear" data-item="' + esc(r.id) + '">带上</button>');
    ops.push('<button class="mini" data-bag-act="sell" data-item="' + esc(r.id) + '">卖 ' + r.sell + ' 文</button>');
  } else {
    ops.push('<button class="mini" data-bag-act="buy" data-item="' + esc(r.id) + '"' +
      (r.afford ? '' : ' disabled') + '>买 ' + r.price + ' 文</button>');
  }
  return '<div class="bag-row" data-kind="' + esc(r.kind) + '">' +
    '<span class="bag-nm">' + esc(r.name) + (mode === 'bag' ? ' ×' + r.count : '') +
    '<span class="bag-kind">' + esc(r.kind_cn) + '</span></span>' +
    '<span class="bag-main">' + eff + desc + '</span>' +
    '<span class="bag-ops">' + ops.join('') + '</span></div>';
}

function renderGear(g) {
  if (!g) return;
  const pill = $('#bag-money');
  if (pill) { pill.className = 'pill'; pill.textContent = g.money_text || (g.money + ' 文'); }

  const mods = $('#bag-mods');
  if (mods) {
    const rows = [];
    rows.push(['银钱', (g.money_text || '') + ' · 伤了先吃药, 没药就打坐, 钱是路上挣的']);
    rows.push(['身上', g.mods_text || '—']);
    rows.push(['医馆', (g.clinic ? g.clinic.name + ' ' + g.clinic.price + ' 文 (' + g.clinic.heal + ')' : '—') +
      ' · 客栈 ' + (g.inn ? g.inn.price + ' 文' : '—')]);
    mods.innerHTML = rows.map((r) => '<div class="cs-meta-row"><span class="cs-meta-l">' +
      esc(r[0]) + '</span><span class="cs-meta-v">' + esc(r[1]) + '</span></div>').join('');
  }

  const slots = $('#bag-slots');
  if (slots) {
    slots.innerHTML = (g.slots || []).map(function (s) {
      if (!s.name) {
        return '<div class="bag-slot empty"><span class="bs-l">' + esc(s.slot_cn) +
          '</span><span class="bs-n">空着</span></div>';
      }
      return '<div class="bag-slot"><span class="bs-l">' + esc(s.slot_cn) +
        '</span><span class="bs-n">' + esc(s.name) + '</span>' +
        (s.effect ? '<span class="bs-e">' + esc(s.effect) + '</span>' : '') +
        '<button class="mini" data-bag-act="unwear" data-slot="' + esc(s.slot) + '">卸下</button></div>';
    }).join('');
  }

  const list = $('#bag-list');
  if (list) {
    const rows = g.bag || [];
    list.innerHTML = rows.length
      ? rows.map((r) => bagRow(r, 'bag')).join('')
      : '<div class="bag-empty">行囊是空的 —— 去铺子里添点东西。</div>';
  }
  const sum = $('#bag-sum');
  if (sum) sum.textContent = (g.bag_count || 0) + ' 件';

  const shop = $('#shop-list');
  if (shop) {
    shop.innerHTML = (g.shop || []).map((r) => bagRow(r, 'shop')).join('');
  }
  const ssum = $('#shop-sum');
  if (ssum) ssum.textContent = (g.shop || []).length + ' 样 · 钱袋 ' + (g.money || 0) + ' 文';
}

document.addEventListener('click', function (e) {
  const btn = e.target.closest('[data-bag-act]');
  if (!btn) return;
  const act = btn.dataset.bagAct;
  const item = btn.dataset.item || '';
  const payload = act === 'unwear'
    ? { slot: btn.dataset.slot || '' }
    : (act === 'buy' ? { item: item, count: 1 } : { item: item });

  btn.disabled = true;
  api('/api/gear/' + act, { method: 'POST', body: JSON.stringify(payload) })
    .then(function (r) {
      if (r.char) renderChar(r.char);
      else if (r.gear) renderGear(r.gear);
      const msg = ({
        buy: '买了 ' + (r.name || '') + ' (' + (r.cost || 0) + ' 文)',
        use: '用了 ' + (r.name || '') + ' · ' +
          (Object.keys(r.healed || {}).length ? '状态见状态条' : '没什么用'),
        sell: '卖了 ' + (r.name || '') + ' (' + (r.gain || 0) + ' 文)',
        wear: '带上 ' + (r.name || ''),
        unwear: '卸下 ' + (r.name || ''),
      })[act] || '完成';
      setStatus($('#bag-tip'), msg, 'ok');
      window.setTimeout(function () { if (state.char) setStatus($('#bag-tip'), '', ''); }, 4000);
    })
    .catch(function (err) {
      btn.disabled = false;
      setStatus($('#bag-tip'), err.message, 'err');
    });
});

/* ───────────────────────── 人物面板 (武侠) ───────────────────────── */

const TYPE_CN = { basic: '基本功', weapon: '兵器', fist: '拳脚', qinggong: '轻功',
                   neigong: '内功', perform: '绝招', art: '杂学' };
const EFFECT_CN = { damage: '伤害', control: '控住', defend: '守势',
                    evade: '身法', heal: '疗伤', buff: '涨己' };

function attrName(key) {
  const a = (state.schema && state.schema.attrs || []).find((x) => x.key === key);
  return a ? a.name : key;
}

/**
 * 熟练度格: 到上限就写「满」, 否则一条细进度 + "还有多少点升一级"。
 * 为什么要有这一格: 等级是跳变的 (3 → 4 之间什么都没有), 读者只能看见"没动";
 * 熟练度是连续量, 一眼能看出"这一趟虽然没升级, 但练了四成"。
 */
function profCell(s) {
  const need = s.prof_need || 120;
  const prof = Math.max(0, s.prof || 0);
  const capped = s.cap != null && s.level >= s.cap;
  if (capped) return '<span class="cs-prof-x" title="已到等级允许的上限">满</span>';
  const pct = Math.max(0, Math.min(100, s.prof_pct != null ? s.prof_pct
    : Math.round(prof * 100 / need)));
  return '<span class="cs-prof-w" title="熟练 ' + prof + ' / ' + need +
    ' —— 满了升一级 (上限由等级决定)">' +
    '<i class="cs-bar-in"><b style="width:' + pct + '%"></b></i>' +
    '<em>' + prof + '/' + need + '</em></span>';
}

/** 状态条: 当前/上限 */
function statBar(label, cur, max, cls) {
  const pct = max > 0 ? Math.max(0, Math.min(100, Math.round(cur * 100 / max))) : 0;
  return '<div class="cs-stat' + (cls ? ' ' + cls : '') + '">' +
    '<div class="cs-stat-top"><span class="cs-stat-l">' + esc(label) + '</span>' +
    '<span class="cs-stat-v">' + cur + '<i>/' + max + '</i></span></div>' +
    '<div class="cs-bar"><i style="width:' + pct + '%"></i></div>' +
    '</div>';
}

function renderChar(ch) {
  if (!ch) return;
  state.char = ch;

  const rank = $('#char-rank');
  if (rank) {
    rank.className = 'pill ok';
    rank.textContent = ch.rank || '初入江湖';
  }
  const nm = $('#pet-name');
  if (nm && ch.name) nm.textContent = ch.name;

  const sub = $('#char-subline');
  if (sub) {
    const bits = [];
    bits.push(ch.rank || '初入江湖');
    bits.push('Lv.' + ch.level);
    bits.push(ch.school ? ch.school.name : '无门无派');
    bits.push('陪读 ' + (ch.minutes || 0) + ' 分钟');
    sub.textContent = bits.join(' · ');
  }
  // 故事内的年岁 —— 这是"活到八十岁"的那条线
  const lif = $('#char-life');
  if (lif) {
    const L = ch.life || {};
    const bits = [];
    bits.push(L.age_label || '12 岁');
    bits.push('第 ' + (L.generation || ch.generation || 1) + ' 代');
    bits.push('故事第 ' + (L.day_no || 1) + ' 日');
    if (L.stage === 'retiring') bits.push('已退隐 · 待传承');
    lif.textContent = bits.join(' · ');
  }
  renderBar(ch);
  renderLife(ch.life);
  renderGear(ch.gear);

  // 状态条
  const st = $('#char-stats');
  if (st) {
    const order = ['hp', 'spirit', 'neili', 'jingli'];
    st.innerHTML = order.map((k) => {
      const s = (ch.stats || {})[k] || {};
      return statBar(s.label || k, s.cur || 0, s.max || 0);
    }).join('');
  }

  // 名片
  const meta = $('#char-meta');
  if (meta) {
    const rows = [];
    // 修为: 写"还差多少"而不只是两个数 —— 读者想知道的是"离下一级还有多远"
    rows.push(['修为', (ch.exp || 0) + ' / ' + (ch.exp_needed || 0) +
      ' (' + (ch.exp_pct != null ? ch.exp_pct : 0) + '%)' +
      (ch.exp_left ? ' · 还差 ' + ch.exp_left : ' · 已可升级')]);
    rows.push(['累计修为', (ch.exp_total || 0) + ' (一辈子的总和)']);
    if (ch.skill_cap != null) {
      rows.push(['技能上限', '等级 ' + (ch.level || 1) + ' → 每门最高 ' +
        ch.skill_cap + ' 级 (再按悟性折)']);
    }
    rows.push(['声望', ch.fame + ' / ' + ch.fame_needed]);
    rows.push(['门忠', ch.loyalty || 0]);
    rows.push(['心性', ch.morality || 0]);
    rows.push(['觉醒点', ch.wish || 0]);
    rows.push(['内功', 'Lv.' + (ch.neigong_level || 0)]);
    const cb = ch.combat || {};
    if (cb.hit != null) {
      rows.push(['交手', '命中 ' + cb.hit + ' · 闪避 ' + cb.dodge + ' · 招架 ' + cb.parry]);
      rows.push(['出手', '伤害 ' + cb.damage + ' · 减伤 ' + (cb.take_pct || 0) + '%' +
        (cb.main && cb.main.name ? ' · 主修 ' + cb.main.name + ' ' + cb.main.level + ' 级' : ' · 主修 无')]);
    }
    const wx = ch.wuxing || {};
    rows.push(['五行', Object.keys(wx).map((k) => k + wx[k]).join(' ') || '—']);
    meta.innerHTML = rows.map((r) =>
      '<div class="cs-meta-row"><span class="cs-meta-l">' + esc(r[0]) +
      '</span><span class="cs-meta-v">' + esc(r[1]) + '</span></div>').join('');
  }

  // 交手用的 d20 数值 —— 每一项都拆账 (根骨 / 技能 / 禀赋)
  const d20box = $('#char-d20');
  if (d20box) {
    const t = ch.d20 || {};
    const rows = [];
    function parts(list) {
      if (!list || !list.length) {
        return '<span class="d20-mod" data-src="none">无加值</span>';
      }
      return list.map(function (m) {
        return '<span class="d20-mod" data-src="' + esc(m.src || '') + '">' +
          esc(m.name) + ' <b>' + signed(m.value) + '</b></span>';
      }).join('');
    }
    rows.push(['护体', '10 + ' + parts(t.ac_parts) + ' = <b>' + t.ac + '</b>']);
    rows.push(['命中', parts(t.attack_parts) + ' = <b>' + signed(t.attack) + '</b>']);
    rows.push(['伤害', '<b>' + esc((t.damage || {}).text || '—') + '</b> · ' +
      esc((t.damage || {}).weapon || '空手') + ' · 卸力 ' + (t.pierce == null ? '—' : t.pierce)]);
    rows.push(['先攻', parts((t.init || {}).parts) + ' = <b>' + signed((t.init || {}).total) + '</b>']);
    const sv = t.saves || {};
    rows.push(['豁免', ['gen', 'ding', 'shen'].map(function (k) {
      return { gen: '根骨', ding: '定力', shen: '身法' }[k] + ' ' +
        signed(((sv[k] || {}).total) || 0);
    }).join(' · ')]);
    rows.push(['暴击', '骰出 ' + (t.crit_range || 20) + ' 以上即大成功 · 骰出 20 必中']);
    d20box.innerHTML = rows.map(function (r) {
      return '<div class="cs-d20-row"><span class="cs-d20-l">' + esc(r[0]) +
        '</span><span class="cs-d20-v">' + r[1] + '</span></div>';
    }).join('');
    const sum = $('#char-d20-sum');
    if (sum) sum.textContent = '护体 ' + (t.ac == null ? '—' : t.ac) +
      ' · 命中 ' + signed(t.attack) + ' · ' + ((t.damage || {}).text || '');
  }
  const rules = $('#char-d20-rules');
  if (rules && state.schema && (state.schema.d20_rules || []).length) {
    rules.innerHTML = state.schema.d20_rules.map(function (x) {
      return '<div class="cs-meta-row"><span class="cs-meta-v">' + esc(x) + '</span></div>';
    }).join('');
  } else if (rules && !rules.innerHTML) {
    rules.innerHTML = '<div class="cs-meta-row"><span class="cs-meta-v">' +
      '一切判定都是 d20: 骰点 + 根骨 / 技能 / 禀赋 + 情境, 与难度比大小。' +
      '</span></div>';
  }

  // 六项根骨
  const pot = $('#attr-pot');
  if (pot) {
    pot.className = 'pill ' + (ch.pot > 0 ? 'warn' : 'mute');
    pot.textContent = '潜能 ' + (ch.pot || 0) + (ch.pot > 0 ? ' · 待分配' : '');
    // 有潜能没花掉时, 提示怎么花 —— 不加这句, 读者只会看见一个数字
    pot.title = ch.pot > 0
      ? '点下面的「+」分配到六项根骨 (每项封顶 30, 六项合计封顶 180)'
      : '升级时获得, 可分配到六项根骨';
  }
  const tb = $('#attr-rows');
  if (tb) {
    tb.innerHTML = '';
    (ch.attrs || []).forEach((a) => {
      const tr = document.createElement('tr');
      // 值 = 基础 + 先天 + 门派加成 —— 有加成时把构成写出来, 免得"入门白送 2 点"看不懂
      const parts = [];
      if (a.bonus) parts.push('门派 +' + a.bonus);
      if (a.born) parts.push('先天 +' + a.born);
      tr.innerHTML =
        '<td class="nowrap"><b>' + esc(a.name) + '</b></td>' +
        '<td class="nowrap cs-dim">' + esc(a.alias || '') + '</td>' +
        '<td class="num">' + a.value +
        (parts.length ? ' <span class="cs-dim cs-sm">(' + esc(parts.join(' ')) + ')</span>' : '') +
        '</td>' +
        '<td class="num cs-dim">' + (a.base != null ? a.base : a.value) + '</td>' +
        '<td class="num cs-dim">' + (a.born || 0) + '</td>' +
        '<td class="num cs-dim">' + a.cap + '</td>' +
        '<td class="cs-desc">' + esc(a.desc || '') + '</td>' +
        '<td class="nowrap"><button class="mini" data-attr="' + esc(a.key) + '"' +
        (a.can_add ? '' : ' disabled') + '>+</button></td>';
      tb.appendChild(tr);
    });
    tb.onclick = async (e) => {
      const btn = e.target.closest('button[data-attr]');
      if (!btn) return;
      try {
        const r = await api('/api/char/alloc', {
          method: 'POST',
          body: JSON.stringify({ key: btn.dataset.attr, n: 1 }),
        });
        renderChar(r.char);
      } catch (err) { setStatus($('#char-status'), err.message, 'err'); }
    };
  }

  // 门派
  const sc = $('#school-cards');
  if (sc) {
    const list = (state.schema && state.schema.schools) || [];
    sc.innerHTML = list.map((s) => {
      const cur = ch.school && ch.school.id === s.id;
      return '<div class="cs-school' + (cur ? ' is-on' : '') + '">' +
        '<div class="cs-school-h"><b>' + esc(s.name) + '</b>' +
        '<span class="pill mute">' + esc(s.sect) + '</span>' +
        (cur ? '<span class="pill ok">已入门</span>' : '') + '</div>' +
        '<div class="cs-school-d">' + esc(s.desc) + '</div>' +
        '<div class="cs-school-k">长于: ' + (s.attrs || []).map(attrName).join(' · ') + '</div>' +
        (s.weapon_name
          ? '<div class="cs-school-k">本门: ' + esc(s.weapon_name) + ' · ' +
            esc(s.neigong_name || '') + ' · 绝招 ' + esc(s.perform_name || '') + '</div>'
          : '') +
        '<div class="row"><button class="mini' + (cur ? '' : ' primary') + '" data-school="' +
        esc(s.id) + '">' + (cur ? '已是本门' : '入门') + '</button></div>' +
        '</div>';
    }).join('');
    sc.onclick = async (e) => {
      const btn = e.target.closest('button[data-school]');
      if (!btn) return;
      const id = btn.dataset.school;
      const cur = ch.school && ch.school.id === id;
      if (cur) {
        if (!confirm('叛门? 声望与门忠会清零。')) return;
      } else if (ch.school) {
        if (!confirm('改投 ' + id + '? 视为叛门: 声望与门忠清零。')) return;
      }
      try {
        const r = await api('/api/char/school', {
          method: 'POST', body: JSON.stringify({ school: id }),
        });
        renderChar(r.char);
      } catch (err) { setStatus($('#char-status'), err.message, 'err'); }
    };
  }
  const stp = $('#school-tip');
  if (stp) {
    stp.textContent = ch.school
      ? ('本门: ' + ch.school.name + ' —— ' + ch.school.desc)
      : '尚未入门。入门得本门内功与绝招, 并加一项根骨。';
  }
  const scSum = document.querySelector('#fold-card-schools > summary');
  if (scSum) {
    scSum.innerHTML = '门派明细 <span class="cs-dim">' +
      (ch.school ? esc(ch.school.name) : '无门无派') + ' · 点开看内功与绝招</span>';
  }

  // 技能与绝招
  const sk = $('#skill-rows');
  if (sk) {
    sk.innerHTML = '';
    (ch.skills || []).forEach((s) => {
      const tr = document.createElement('tr');
      const perf = s.type === 'perform';
      tr.innerHTML =
        '<td class="nowrap"><b>' + esc(s.name) + '</b></td>' +
        '<td class="nowrap cs-dim">' + esc(TYPE_CN[s.type] || s.type) +
          (s.effect ? '·' + esc(EFFECT_CN[s.effect] || s.effect) : '') + '</td>' +
        '<td class="nowrap cs-dim">' + esc(attrName(s.attr)) + '</td>' +
        '<td class="num">' + (s.level ? s.level : '<span class="cs-dim">未入门</span>') + '</td>' +
        '<td class="num cs-dim">' + (s.cap != null ? s.cap : '—') + '</td>' +
        '<td class="cs-prof">' + profCell(s) + '</td>' +
        '<td class="num">' + (perf ? s.rate + '%' : '—') + '</td>' +
        '<td class="num">' + (perf ? s.busy : '—') + '</td>' +
        '<td class="num">' + (perf ? s.fail : '—') + '</td>' +
        '<td class="num">' + (perf ? s.cost : '—') + '</td>' +
        '<td class="cs-desc">' + esc(s.desc || '') + '</td>';
      sk.appendChild(tr);
    });
  }
  const skt = $('#skill-tip');
  if (skt) {
    const n = (ch.skills || []).length;
    skt.textContent = n
      ? ('共 ' + n + ' 项。招式参数在游历的遭遇里真被用到: 命中看身法与主修, ' +
         '伤害看臂力与兵刃, 绝招吃内力、失手会反噬。')
      : '还没有技能 —— 入门得内功与绝招, 其余靠陪读修习。';
  }
  // 折叠起来的技能表, 摘要里给出"几项 / 最高几级", 不点开也能看出长进
  const skSum = document.querySelector('#card-skills .cs-fold > summary');
  if (skSum) {
    const list = ch.skills || [];
    const top = list.reduce(function (m, x) { return Math.max(m, x.level || 0); }, 0);
    skSum.innerHTML = '技能明细 <span class="cs-dim">共 ' + list.length + ' 项 · 最高 ' +
      top + ' 级 · 点开看等级与绝招参数</span>';
  }

  // 性情
  const tp = $('#trait-pill');
  const ti = ch.traits_info || {};
  if (tp) {
    tp.className = 'pill ' + (ti.left_count > 0 && ti.left_points > 0 ? 'warn' : 'mute');
    tp.textContent = (ti.owned || []).length + ' / ' + ti.max_count +
      ' · ' + ti.spent + ' / ' + ti.max_points + ' 分';
  }
  const trSum = document.querySelector('#fold-card-traits > summary');
  if (trSum) {
    trSum.innerHTML = '性情明细 <span class="cs-dim">已觉醒 ' +
      ((ti.owned || []).length) + ' / ' + (ti.max_count || 6) + ' · 点开看 16 条</span>';
  }
  const tc = $('#trait-cards');
  if (tc) {
    tc.innerHTML = (ch.traits || []).map((t) =>
      '<div class="cs-trait c-' + esc(t.color) + (t.owned ? ' is-on' : '') + '">' +
      '<div class="cs-trait-h"><b>' + esc(t.name) + '</b>' +
      '<span class="pill mute">' + esc(t.color_name) + ' ' + t.points + '</span></div>' +
      '<div class="cs-trait-d">' + esc(t.desc || '') + '</div>' +
      (t.owned ? '' :
        '<div class="row"><button class="mini" data-trait="' + esc(t.id) + '"' +
        ((ti.left_count > 0 && ti.left_points >= t.points) ? '' : ' disabled') +
        '>觉醒</button></div>') +
      '</div>').join('');
    tc.onclick = async (e) => {
      const btn = e.target.closest('button[data-trait]');
      if (!btn) return;
      try {
        const r = await api('/api/char/trait', {
          method: 'POST', body: JSON.stringify({ trait: btn.dataset.trait }),
        });
        renderChar(r.char);
      } catch (err) { setStatus($('#char-status'), err.message, 'err'); }
    };
  }

  // 经脉
  const vp = $('#vein-pill');
  if (vp) {
    vp.className = 'pill ' + (ch.vein > 0 ? 'ok' : 'mute');
    vp.textContent = (ch.vein || 0) + ' / 10';
  }
  const vSum = document.querySelector('#fold-card-veins > summary');
  if (vSum) {
    vSum.innerHTML = '经脉明细 <span class="cs-dim">已通 ' + (ch.vein || 0) +
      ' / 10 层 · 点开看效果与进度</span>';
  }
  const vl = $('#vein-list');
  if (vl) {
    vl.innerHTML = (ch.veins || []).map((v) =>
      '<div class="cs-vein' + (v.done ? ' is-on' : '') + '">' +
      '<div class="cs-vein-h"><span class="cs-vein-i">' + v.idx + '</span>' +
      '<b>' + esc(v.name) + '</b>' +
      '<span class="pill mute">Lv.' + v.level + '</span>' +
      (v.done ? '<span class="pill ok">已通</span>' : '') + '</div>' +
      '<div class="cs-bar"><i style="width:' + v.progress + '%"></i></div>' +
      '<div class="cs-vein-d">' + esc(v.effect) + ' · 需 ' + v.minutes + ' 分钟陪读</div>' +
      '</div>').join('');
  }
  const vt = $('#vein-tip');
  if (vt) {
    const g = (state.schema && state.schema.growth) || {};
    const perMin = g.exp_per_minute != null ? g.exp_per_minute : '?';
    const perLv = g.pot_per_level != null ? g.pot_per_level : '?';
    vt.textContent = '已通 ' + (ch.vein || 0) + ' 层。通脉进度按累计陪读分钟算 —— ' +
      '每分钟换 ' + perMin + ' 点修为, 每升一级给 ' + perLv + ' 点潜能。';
  }

  // 成长规则 (接口路径与待定项)
  const gr = $('#growth-rules');
  if (gr) {
    const g = (state.schema && state.schema.growth) || {};
    const pend = (g.pending || []);
    gr.innerHTML =
      '<div class="cs-meta-row"><span class="cs-meta-l">修为 / 分钟</span>' +
      '<span class="cs-meta-v">' + (g.exp_per_minute != null ? g.exp_per_minute : '—') + '</span></div>' +
      '<div class="cs-meta-row"><span class="cs-meta-l">潜能 / 升级</span>' +
      '<span class="cs-meta-v">' + (g.pot_per_level != null ? g.pot_per_level : '—') + '</span></div>' +
      '<div class="cs-meta-row"><span class="cs-meta-l">声望 / 升级</span>' +
      '<span class="cs-meta-v">' + (g.fame_per_level != null ? g.fame_per_level : '—') + '</span></div>' +
      '<div class="cs-meta-row"><span class="cs-meta-l">自动长进</span>' +
      '<span class="cs-meta-v">' + (g.auto_skill_minutes_per_level != null ?
        ('每 ' + g.auto_skill_minutes_per_level + ' 分钟 (基本内功)') : '—') + '</span></div>' +
      '<div class="cs-meta-row"><span class="cs-meta-l">技能熟练</span>' +
      '<span class="cs-meta-v">' + (g.prof_per_level != null ?
        ('满 ' + g.prof_per_level + ' 点升一级 · 每趟每门封顶 ' +
         (g.practice_cap_per_skill != null ? g.practice_cap_per_skill : '—')) : '—') +
      '</span></div>' +
      '<div class="cs-meta-row"><span class="cs-meta-l">技能上限</span>' +
      '<span class="cs-meta-v">' + (ch.skill_cap != null ? ('等级决定 · 当前 ' + ch.skill_cap + ' 级') : '—') +
      '</span></div>' +
      (pend.length ? '<div class="cs-meta-row" style="grid-column:1/-1">' +
        '<span class="cs-meta-l">待定</span>' +
        '<span class="cs-meta-v cs-dim">' + pend.map(esc).join(' · ') + '</span></div>' : '');
  }
}

/* ───────────────────────── 陪读 (阅读器时长 → 修为) ───────────────────────── */

function renderCompanion(cs_) {
  const pill = $('#companion-pill');
  const box = $('#companion-state');
  if (!cs_ || !box) return;
  const total = cs_.total_minutes || 0;
  const tick = cs_.tick || {};
  if (pill) {
    pill.textContent = total + ' 分钟';
    pill.className = 'pill' + (total > 0 ? ' ok' : '');
  }
  const top = (cs_.top || []);
  const rows = top.length
    ? '<div class="cs-meta-row" style="grid-column:1/-1">' +
      '<span class="cs-meta-l">读得最多</span>' +
      '<span class="cs-meta-v cs-booklist">' +
      top.map(function (kv) {
        return '<span class="cs-book"><span class="cs-book-t">' + esc(kv[0]) +
          '</span><span class="cs-book-m">' + kv[1] + ' 分</span></span>';
      }).join('') +
      '</span></div>'
    : '';
  box.innerHTML =
    '<div class="cs-meta-row"><span class="cs-meta-l">累计陪读</span>' +
    '<span class="cs-meta-v">' + total + ' 分钟 · ' + (cs_.books || 0) + ' 本书</span></div>' +
    '<div class="cs-meta-row"><span class="cs-meta-l">心跳</span>' +
    '<span class="cs-meta-v">' + (tick.enabled ? '运行中' : '未运行') +
    ' · 每 ' + (tick.interval_sec || 60) + ' 秒</span></div>' +
    rows;
}

async function loadCompanion() {
  try {
    const c = await api('/api/companion');
    renderCompanion(c);
  } catch (e) {
    setStatus($('#companion-status'), e.message, 'err');
  }
}

async function loadChar() {
  try {
    const [ch, schema] = await Promise.all([
      api('/api/char'),
      api('/api/char/schema'),
    ]);
    state.schema = schema;
    renderChar(ch);
  } catch (e) {
    setStatus($('#char-status'), e.message, 'err');
  }
}

/* ───────────────────────── 状态 ───────────────────────── */

function renderStatus(st) {
  // 模型就绪徽标 —— 挂在「模型接入」卡的页头 (人物卡的 #char-rank 是品级, 两种徽标别混)
  const pill = $('#pet-state');
  if (pill) {
    if (st.ready) {
      pill.className = 'pill ok';
      pill.textContent = '已就绪';
    } else {
      pill.className = 'pill warn';
      pill.textContent = '还没配模型';
    }
  }
  const name = $('#pet-name');
  if (name && st.persona && st.persona.name) name.textContent = st.persona.name;
}

/* ───────────────────────── 预设 ───────────────────────── */

async function loadPresets() {
  try {
    const r = await api('/api/presets');
    const sel = $('#llm-preset');
    (r.presets || []).forEach((p) => {
      const o = document.createElement('option');
      o.value = p.id;
      o.textContent = p.name;
      o.dataset.note = p.note || '';
      sel.appendChild(o);
    });
    sel.onchange = () => {
      const p = (r.presets || []).find((x) => x.id === sel.value);
      if (!p) return;
      if (p.base_url) $('#llm-base').value = p.base_url;
      if (p.model) $('#llm-model').value = p.model;
      if (p.provider) $('#llm-provider').value = p.provider;
      $('#llm-tip').textContent = p.note || '';
    };
    const ul = $('#llm-tips');
    ul.innerHTML = (r.tips || []).map((t) => '<li>' + t + '</li>').join('');
  } catch (e) { /* 不影响主流程 */ }
}

/* ───────────────────────── 配置 ───────────────────────── */

const REASONING_MARKERS = ['minimax-m', 'thinking', 'reasoning', 'qwq', 'deepseek-r1',
  'glm-4.5', 'glm-4.6', 'o1', 'o3', 'o4'];
const REASONING_PREFIXES = ['gpt-5', 'gpt5'];

function isReasoningModel(name) {
  const m = String(name || '').toLowerCase();
  if (!m) return false;
  if (REASONING_MARKERS.some((k) => m.indexOf(k) >= 0)) return true;
  return REASONING_PREFIXES.some((p) => m.indexOf(p) === 0);
}

function renderReasoningTip(llm) {
  const el = $('#llm-reasoning-tip');
  if (!el) return;
  const model = (llm && llm.model) || $('#llm-model').value || '';
  if (!isReasoningModel(model)) {
    el.className = 'tip';
    el.textContent = '陪聊建议用非推理模型 (如 agnes-2.5-flash): 搭话要快, 慢几秒就不像陪伴了。';
    return;
  }
  const thinking = (llm && llm.thinking) || $('#llm-thinking').value || 'off';
  el.className = 'tip warn';
  el.innerHTML = '⚠ <b>' + esc(model) + '</b> 属于推理模型 —— 它会把 token 烧在思考上, ' +
    '搭话会慢一大截。<br>→ 已设「思考 = ' + (thinking === 'on' ? '<b>允许</b>' : '<b>关闭</b>') + '」' +
    (thinking === 'on' ? ' (这会明显变慢)' : ' (会尝试发送关闭参数)') + '。';
}

function llmPayload() {
  return {
    provider: $('#llm-provider').value,
    base_url: $('#llm-base').value,
    model: $('#llm-model').value,
    api_key: $('#llm-key').value,
    timeout: Number($('#llm-timeout').value) || 60,
    max_tokens: Number($('#llm-maxtok').value) || 1024,
    temperature: Number($('#llm-temp').value),
    thinking: $('#llm-thinking').value,
  };
}

async function loadConfig() {
  const cfg = await api('/api/config');
  const l = cfg.llm || {};
  $('#llm-provider').value = l.provider || 'openai';
  $('#llm-base').value = l.base_url || '';
  $('#llm-model').value = l.model || '';
  $('#llm-key').placeholder = l.has_key ? ('已保存: ' + l.api_key) : 'sk-...';
  $('#llm-timeout').value = l.timeout || 60;
  $('#llm-maxtok').value = l.max_tokens || 1024;
  $('#llm-temp').value = l.temperature != null ? l.temperature : 0.8;
  $('#llm-thinking').value = l.thinking === 'on' ? 'on' : 'off';
  renderReasoningTip(l);

  state.models = cfg.models || [];
  state.activeModelId = cfg.active_model_id || '';
  state.persona = cfg.persona || {};
  state.companion = cfg.companion || {};
  renderModels();

  const p = state.persona || {};
  $('#pet-name-input').value = p.name || '';
  $('#pet-style').value = p.style || '';

  const st = await api('/api/status');
  state.ready = !!st.ready;
  renderStatus(st);
}

$('#btn-llm-save').onclick = async () => {
  const st = $('#llm-status');
  try {
    const body = Object.assign({}, llmPayload());
    if (!body.api_key) delete body.api_key;   // 空 = 不改
    await api('/api/config', { method: 'POST', body: JSON.stringify({ llm: body }) });
    setStatus(st, '已保存', 'ok');
    $('#llm-key').value = '';
    await loadConfig();
  } catch (e) { setStatus(st, e.message, 'err'); }
};

$('#btn-llm-test').onclick = async () => {
  const st = $('#llm-status');
  setStatus(st, '测试中…');
  try {
    const r = await api('/api/llm/test', { method: 'POST', body: JSON.stringify(llmPayload()) });
    if (r.ok) setStatus(st, '连通 (' + r.latency_ms + 'ms) ' + (r.sample || '').slice(0, 60), 'ok');
    else setStatus(st, '失败: ' + r.message, 'err');
  } catch (e) { setStatus(st, e.message, 'err'); }
};

$('#btn-llm-toggle').onclick = () => {
  const b = $('#llm-body');
  b.classList.toggle('hidden');
  $('#btn-llm-toggle').textContent = b.classList.contains('hidden') ? '展开' : '收起';
};

/* ───────────────────────── 人格 ───────────────────────── */

$('#btn-persona-save').onclick = async () => {
  const st = $('#persona-status');
  try {
    const name = $('#pet-name-input').value.trim();
    const r = await api('/api/config', {
      method: 'POST',
      body: JSON.stringify({
        persona: {
          name: name,
          style: $('#pet-style').value.trim(),
        }
      })
    });
    // 名字只有一处 —— 人物卡那边也得跟着变 (服务端已同步, 这里把面板刷回来)
    if (r.char) renderChar(r.char);
    setStatus(st, '已保存', 'ok');
    await loadConfig();
  } catch (e) { setStatus(st, e.message, 'err'); }
};

/* ───────────────────────── 模型库 ───────────────────────── */

function renderModels() {
  const tb = $('#model-rows');
  if (!tb) return;
  tb.innerHTML = '';
  state.models.forEach((m, i) => {
    const tr = document.createElement('tr');
    tr.innerHTML =
      '<td><input type="radio" name="mdl-active" data-i="' + i + '"' +
        (state.activeModelId === m.id ? ' checked' : '') + '></td>' +
      '<td><input type="text" data-i="' + i + '" data-f="id" value="' + esc(m.id) + '"></td>' +
      '<td><input type="text" data-i="' + i + '" data-f="name" value="' + esc(m.name) + '"></td>' +
      '<td><select data-i="' + i + '" data-f="provider">' +
        '<option value="openai"' + (m.provider === 'openai' ? ' selected' : '') + '>openai</option>' +
        '<option value="anthropic"' + (m.provider === 'anthropic' ? ' selected' : '') + '>anthropic</option>' +
        '</select></td>' +
      '<td><input type="text" data-i="' + i + '" data-f="base_url" value="' + esc(m.base_url || '') + '"></td>' +
      '<td><input type="text" data-i="' + i + '" data-f="model" value="' + esc(m.model || '') + '">' +
        (m.reasoning_model ? ' <span class="badge-warn" title="推理模型: 搭话会慢">推理</span>' : '') + '</td>' +
      '<td><select data-i="' + i + '" data-f="thinking">' +
        '<option value="off"' + ((m.thinking || 'off') === 'off' ? ' selected' : '') + '>关闭</option>' +
        '<option value="on"' + (m.thinking === 'on' ? ' selected' : '') + '>允许</option>' +
        '</select></td>' +
      '<td><input type="password" data-i="' + i + '" data-f="api_key" autocomplete="off" ' +
        'placeholder="' + (m.has_key ? esc(m.api_key) : 'sk-...') + '"></td>' +
      '<td><input type="text" data-i="' + i + '" data-f="note" value="' + esc(m.note || '') + '"></td>' +
      '<td class="mdl-ops">' +
        '<button class="mini" data-act="use">使用</button>' +
        '<button class="mini" data-act="test">测</button>' +
        '<button class="mini danger" data-act="del">删</button>' +
        '</td>';
    tb.appendChild(tr);
  });
  tb.oninput = (e) => {
    const t = e.target;
    if (!t.dataset || t.dataset.i === undefined) return;
    if (t.type === 'radio') return;
    state.models[Number(t.dataset.i)][t.dataset.f] = t.value;
  };
  tb.onchange = (e) => {
    const t = e.target;
    if (t.type === 'radio' && t.dataset.i !== undefined) {
      state.activeModelId = state.models[Number(t.dataset.i)].id;
    }
  };
  tb.onclick = async (e) => {
    const btn = e.target.closest('button[data-act]');
    if (!btn) return;
    const tr = btn.closest('tr');
    const i = Array.prototype.indexOf.call(tb.rows, tr);
    if (i < 0 || !state.models[i]) return;
    const m = state.models[i];
    const act = btn.dataset.act;
    const st = $('#models-status');
    if (act === 'use') {
      try {
        const r = await api('/api/models/apply', { method: 'POST', body: JSON.stringify({ id: m.id }) });
        state.activeModelId = (r.config && r.config.active_model_id) || m.id;
        await loadConfig();
        setStatus(st, '已使用: ' + m.name, 'ok');
      } catch (err) { setStatus(st, err.message, 'err'); }
    } else if (act === 'test') {
      setStatus(st, '测试中…');
      try {
        const r = await api('/api/models/' + encodeURIComponent(m.id) + '/test', { method: 'POST' });
        if (r.ok) setStatus(st, m.name + ' 连通 (' + r.latency_ms + 'ms)', 'ok');
        else setStatus(st, m.name + ' 失败: ' + r.message, 'err');
      } catch (err) { setStatus(st, err.message, 'err'); }
    } else if (act === 'del') {
      if (!confirm('删除模型「' + m.name + '」?')) return;
      try {
        const r = await api('/api/models/' + encodeURIComponent(m.id), { method: 'DELETE' });
        state.models = r.models || [];
        state.activeModelId = r.active_model_id || '';
        renderModels();
        setStatus(st, '已删除', 'ok');
      } catch (err) { setStatus(st, err.message, 'err'); }
    }
  };
  const sel = $('#llm-model-sel');
  if (sel) {
    sel.innerHTML = '<option value="">— 手动配置 (不用模型库) —</option>';
    state.models.forEach((m) => {
      const o = document.createElement('option');
      o.value = m.id;
      o.textContent = m.name + (m.model ? ' · ' + m.model : '');
      sel.appendChild(o);
    });
    sel.value = state.activeModelId || '';
    sel.onchange = async () => {
      const id = sel.value;
      if (!id) return;
      try {
        await api('/api/models/apply', { method: 'POST', body: JSON.stringify({ id }) });
        await loadConfig();
        setStatus($('#llm-status'), '已切到模型库条目', 'ok');
      } catch (err) { setStatus($('#llm-status'), err.message, 'err'); }
    };
  }
}

$('#btn-model-add').onclick = () => {
  state.models.push({
    id: '', name: '新模型', provider: 'openai',
    base_url: '', model: '', api_key: '', note: '', has_key: false,
  });
  renderModels();
};

$('#btn-model-import').onclick = async () => {
  const st = $('#models-status');
  try {
    const cfg = await api('/api/config');
    const l = cfg.llm || {};
    state.models.push({
      id: '', name: l.model || '导入的模型',
      provider: l.provider || 'openai',
      base_url: l.base_url || '', model: l.model || '',
      api_key: '', note: '从当前配置导入',
      has_key: !!l.has_key,
      _from_current: true,
    });
    renderModels();
    setStatus(st, '已加一行, 点「保存模型库」落盘', 'ok');
  } catch (e) { setStatus(st, e.message, 'err'); }
};

$('#btn-models-save').onclick = async () => {
  const st = $('#models-status');
  try {
    // 空 key = 不改; 有 has_key 的行若 key 被清空, 传 "***" 保留旧值
    const payload = state.models.map((m) => {
      const e = Object.assign({}, m);
      if (m._from_current) {
        e.from_current = true;
        e.api_key = '';
      } else if (!e.api_key) {
        e.api_key = (m.has_key ? '***' : '');
      } else if (e.api_key.indexOf('*') >= 0) {
        // 脱敏串 = 服务端给的展示值, 不是真 key
        e.api_key = (m.has_key ? '***' : '');
      }
      delete e.has_key;
      delete e._from_current;
      return e;
    });
    const r = await api('/api/models', {
      method: 'POST',
      body: JSON.stringify({ models: payload, active_model_id: state.activeModelId })
    });
    state.models = r.models || [];
    state.activeModelId = r.active_model_id || '';
    renderModels();
    setStatus(st, '已保存 (' + state.models.length + ' 条)', 'ok');
    await loadConfig();
  } catch (e) { setStatus(st, e.message, 'err'); }
};

$('#btn-models-toggle').onclick = () => {
  const b = $('#models-body');
  b.classList.toggle('hidden');
  $('#btn-models-toggle').textContent = b.classList.contains('hidden') ? '展开' : '收起';
};

/* ── 共用零件的全局别名 (探针与页面都按全局函数调) ─────────────── */
const signed = RCP ? RCP.signed : function (n) { return String(n || 0); };
const d20Verdict = RCP ? RCP.d20Verdict : function () { return ''; };
const rollRow = RCP ? RCP.rollRow : function () { return ''; };
const rollsBlock = RCP ? RCP.rollsBlock : function () { return ''; };
const diceLine = RCP ? RCP.diceLine : function () { return ''; };
const fightersBlock = RCP ? RCP.fightersBlock : function () { return ''; };
const duelRound = RCP ? RCP.duelRound : function () { return ''; };
const duelBlock = RCP ? RCP.duelBlock : function () { return ''; };
const checksBlock = RCP ? RCP.checksBlock : function () { return ''; };
const kindBadge = RCP ? RCP.kindBadge : function () { return ''; };
const echoBlock = RCP ? RCP.echoBlock : function () { return ''; };

/* ───────────────────────── 游历 (后台推演) ───────────────────────── */

function fmtMin(n) {
  n = Number(n) || 0;
  if (n < 60) return n + ' 分钟';
  return (n / 60).toFixed(1).replace(/\.0$/, '') + ' 小时';
}

function advParagraphs(body) {
  return String(body || '').split(/\n{2,}/).map(function (p) {
    return '<p>' + esc(p) + '</p>';
  }).join('');
}

/**
 * 读本 —— **MUD 式阅读窗口** (2026-11-20 改)。
 *
 * **为什么改**: 原来正文区是 `max-height`, 每追加一段容器就长高一截 →
 * 整个页面被撑高 → 页面自己冒出滚动条, 版面跟着晃。而且空格只推**一段**
 * (平均 39 字 ≈ 两行), 一次敲击只出两行字, 读起来碎。
 *
 * **现在**:
 *   · 正文窗口**高度固定** —— 页面结构不随内容长高, 页面滚动条不会
 *     因为多读一段就冒出来。
 *   · 滚动**只在窗口内**发生 —— 自己设 scrollTop, 不用 scrollIntoView
 *     (后者会连带把页面卷走, 整个版面跟着晃 —— 那正是要治的毛病)。
 *   · 空格**翻一屏** —— 一次敲击滚一整屏 (一个视口高), 不切内容、
 *     不跳段; 一次敲击只出两行字太碎。
 *   · PageUp / Alt+← 回看一屏; 位置是**高水位** (往回看不抹掉已读的)。
 *
 * **位置 = 窗口底压着的那一段**: 眼睛看到屏幕底就等于读到那儿了。
 *
 * **段落切分在后台做** (`/api/story` 给的是已经切好的 flow) —— 位置
 * (读到第几段) 是跨端共用的索引, 两边各切一次迟早错位。
 *
 * **位置记在后端** (`/pet/api/reading`, 全局段序号) 而不是 localStorage:
 * "它走过的路"不该跟着换设备消失, 也不该被清缓存抹掉。
 */

const READER = {
  flow: [],        // 全书段落流 (后端给)
  total: 0,        // 总段数
  idx: 0,          // 已读到第几段 (= 窗口底压着的那一段)
  drawn: 0,        // DOM 里画到第几段 (段是连续的 1..drawn)
  done: false,     // 走到尽头了
  open: false,     // 读本是不是展开着
  tops: [],        // 每段在"内容坐标"里的上沿 (不随滚动变)
  bots: [],        // 每段在"内容坐标"里的下沿
  lockUntil: 0,    // 程序化滚动截止时刻 (ms) —— 期间位置**不**跟着动
};

/**
 * 程序化滚动期间**锁住**位置。
 *
 * 为什么不能是一个布尔开关: `body.scrollTop = x` 触发的 scroll 事件是
 * **异步**的 (下一个事件循环才派发) —— 同步地置位/复位布尔开关根本
 * 盖不住它 (真撞过: 「从头读」复位后位置跳到 9 段 —— 复位那一滚被
 * 当成了"读者读到了第 9 段")。
 *
 * 用**时间戳**: 程序化滚动前设一个截止时刻, scroll 事件里看还有没有
 * 到期。没到期就是程序化滚的 (不推进位置), 到期后自然放行。
 */
function readLock(ms) {
  READER.lockUntil = Date.now() + (ms || 400);
}

/* 往后面多画多少段 —— 滚动要有料, 但也别一次画几千段。 */
const READER_LOOKAHEAD = 120;

/* 位置存盘的防抖 (滚动是连续的, 一次翻屏会连着滚很多事件) */
let readSaveTimer = 0;

/* 探针要读读本现状 (顶层 const 不是 window 的属性) */
window.READER = READER;

/**
 * 摊开读本 —— 从上次读到的地方接着往下。
 *
 * 打开时**对齐到存档位置**: 窗口底落在上次读到的那一段的下沿, 读者
 * 看到的还是上次那一屏, 再按空格就往下走。
 */
function readOpen(flow, total, idx) {
  const box = $('#adv-read');
  const body = $('#adv-read-body');
  if (!box || !body) return;
  READER.flow = flow || [];
  READER.total = Number(total) || READER.flow.length;
  const read = Math.max(0, Number(idx) || 0);
  READER.idx = read;
  READER.done = false;
  READER.open = true;
  READER.drawn = 0;
  READER.tops = [];
  READER.bots = [];
  body.innerHTML = '';
  box.hidden = false;
  // 先画够一屏 + 后面一截 (滚动要有料)
  readEnsure(Math.min(READER.total, read + READER_LOOKAHEAD));
  // 对齐到存档位置 (从没用过 → 从头看)
  // **加锁**: 这一滚是程序化对齐, 不是读者在读 —— 不该算进度。
  readLock();
  readScrollToBottom(read);
  // **打开时不推进位置** —— 见 readSync 的 advance 说明
  readSync(false, false);
  // 之前挂着的存盘定时器 (读别的页面时排的) 得撤掉 ——
  // 否则它会把"复位前"的位置又写回去。
  if (readSaveTimer) { clearTimeout(readSaveTimer); readSaveTimer = 0; }
}

/** 量一遍某一段在"内容坐标"里的上下沿 (scrollTop 坐标系, 不随滚动变)。 */
function readRectOf(el) {
  const body = $('#adv-read-body');
  if (!body || !el) return null;
  const br = body.getBoundingClientRect();
  const base = body.scrollTop - br.top - body.clientTop;
  const r = el.getBoundingClientRect();
  return { top: base + r.top, bot: base + r.bottom };
}

/** 往后面补画段 (滚到画出来的尽头时要续)。 */
function readEnsure(upto) {
  const body = $('#adv-read-body');
  if (!body) return;
  const want = Math.min(READER.total, Math.max(0, upto));
  if (want <= READER.drawn) return;
  for (let i = READER.drawn; i < want; i++) {
    const item = READER.flow[i];
    if (!item) break;
    const el = document.createElement('p');
    el.textContent = item.text;
    body.appendChild(el);
    const rc = readRectOf(el);
    if (rc) { READER.tops.push(rc.top); READER.bots.push(rc.bot); }
  }
  READER.drawn = want;
}

/** 全量重量 (窗口尺寸变了 → 之前的几何都作废)。 */
function readMeasureAll() {
  const body = $('#adv-read-body');
  if (!body) return;
  const ps = body.querySelectorAll('p');
  READER.tops = [];
  READER.bots = [];
  for (let i = 0; i < ps.length; i++) {
    const rc = readRectOf(ps[i]);
    if (!rc) continue;
    READER.tops.push(rc.top);
    READER.bots.push(rc.bot);
  }
}

/**
 * 窗口底压着的是第几段 (1 基) —— "读到哪儿了"的唯一口径。
 *
 * = **完整落在窗口里**的最后一段 (眼睛看到屏幕底就等于读到那儿)。
 * +2px 容差: 正好齐底的那段算已读完。
 *
 * 曾经写成"第一个还没读完的段" —— 那样每开一次页面就凭空多读一段。
 */
function readBottomIdx() {
  const body = $('#adv-read-body');
  if (!body || !READER.bots.length) return 0;
  const limit = body.scrollTop + body.clientHeight;
  let idx = 0;
  for (let i = 0; i < READER.bots.length; i++) {
    if (READER.bots[i] <= limit + 2) idx = i + 1;
    else break;
  }
  // 一段都装不进窗口 (段比窗口还高) —— 至少算读过了第一段
  return idx || 1;
}

/** 把窗口滚到底落在第 idx 段的下沿 (只滚窗口自己, 不碰页面)。 */
function readScrollToBottom(idx) {
  const body = $('#adv-read-body');
  if (!body) return;
  const maxScroll = Math.max(0, body.scrollHeight - body.clientHeight);
  if (!idx || idx > READER.drawn) { body.scrollTop = 0; return; }
  const i = Math.min(idx, READER.bots.length) - 1;
  const target = READER.bots[i] - body.clientHeight;
  body.scrollTop = Math.max(0, Math.min(target, maxScroll));
}

/** 翻一屏 (往下): 恰好一个视口高 —— 不切内容, 不跳段。 */
function readPageDown() {
  const body = $('#adv-read-body');
  if (!body) return;
  // **先补画再翻** —— 否则读者翻到"画出来的尽头"时, 这一按会滚到旧
  // 底 (内容还没长出来), 看起来像"这一按没反应", 得再按一次才动。
  // (补画在前面 → 内容够长 → 这一按直接滚到真正的下一屏)
  const maxBefore = Math.max(0, body.scrollHeight - body.clientHeight);
  if (body.scrollTop + body.clientHeight >= maxBefore - 8) {
    readEnsure(READER.drawn + READER_LOOKAHEAD);
  }
  const maxScroll = Math.max(0, body.scrollHeight - body.clientHeight);
  const target = Math.min(body.scrollTop + body.clientHeight, maxScroll);
  readScrollTo(target);
}

/** 回看一屏 (往上): 恰好一个视口高。 */
function readPageUp() {
  const body = $('#adv-read-body');
  if (!body) return;
  readScrollTo(Math.max(0, body.scrollTop - body.clientHeight));
}

/**
 * 滚到某个位置 (只动窗口自己的 scrollTop)。
 *
 * **瞬时, 不做平滑** —— 平滑滚动是**动画**, 动画期间 scrollTop 在动,
 * 连按空格时"下一页"要从动画中途的位置算起 → 每次只走半屏, 手感乱。
 * MUD 翻页本来就是瞬时的, 而且瞬时才能算准。
 */
function readScrollTo(top) {
  const body = $('#adv-read-body');
  if (!body) return;
  body.scrollTop = Math.max(0, top);
}

/**
 * 滚动之后: 同步"读到哪儿了" (位置只前进)。
 *
 * ``advance=false`` —— **打开页面时不推进位置**。理由: 刚打开时窗口里
 * 摊着的那几段是"刚好装得下"的量, 不是读者读过的量 —— 不设这个闸,
 * 每打开一次页面位置就凭空往前挪几段 (真撞过: idx 4 → 9)。
 * 只有读者**自己往前滚**了才算数。
 */
function readSync(save, advance) {
  const body = $('#adv-read-body');
  if (!body) return;
  // 滚到画出来的尽头 → 后面还有段, 补画 (滚动要有料)
  const maxScroll = Math.max(0, body.scrollHeight - body.clientHeight);
  if (body.scrollTop + body.clientHeight >= maxScroll - 8) {
    readEnsure(READER.drawn + READER_LOOKAHEAD);
  }
  const bi = readBottomIdx();
  // **位置只前进** (高水位): 往回看不抹掉已经读到的地方
  //
  // 锁定期内**不**推进 —— 程序化滚动 (打开时对齐存档 / 「从头读」复位)
  // 会触发 scroll 事件, 那不是读者在读, 不该算进度。
  // (真撞过: 「从头读」后位置跳到 9 —— 复位那一滚被当成了读者读到了第 9 段)
  const locked = Date.now() < READER.lockUntil;
  if (advance !== false && !locked && bi > READER.idx) {
    READER.idx = bi;
    if (save !== false) readSaveSoon();
  }
  const prog = $('#adv-read-prog');
  if (prog) prog.textContent = READER.total ? (READER.idx + ' / ' + READER.total + ' 段') : '—';
  const finished = READER.total > 0 && READER.idx >= READER.total;
  const foot = $('#adv-read-foot');
  const doneBox = $('#adv-read-done');
  if (foot) foot.hidden = finished;
  if (doneBox) doneBox.hidden = !finished;
  READER.done = finished;
}

/** 推进一屏 (空格 / 点按钮)。走到尽头 → 记位置 + 收尾。 */
function readNext() {
  if (!READER.open) return;
  const body = $('#adv-read-body');
  if (!body) return;
  const maxScroll = Math.max(0, body.scrollHeight - body.clientHeight);
  if (body.scrollTop >= maxScroll - 2) {
    // 已经在尽头
    READER.idx = READER.total;
    readSync(false);
    readFinish();
    return;
  }
  readPageDown();
}

/** 回看一屏 (往回看, 不改位置)。 */
function readPrev() {
  if (!READER.open) return;
  readPageUp();
}

/** 走到尽头: 记位置。 */
function readFinish() {
  READER.done = true;
  readSave();
}

/** 位置存盘 (防抖) —— 滚动是连续的, 一次翻屏会连着滚很多事件。 */
function readSaveSoon() {
  if (readSaveTimer) clearTimeout(readSaveTimer);
  readSaveTimer = setTimeout(function () {
    readSaveTimer = 0;
    readSave();
  }, 600);
}

function readSave(force) {
  const total = READER.total;
  if (!total) return;
  // 位置 = **窗口底压着的那一段** (眼睛看到屏幕底就等于读到了那儿)。
  // 可以合法地是 0 (「从头读」复位后还没读任何一段)。
  const finished = READER.idx >= total;
  const idx = finished ? total : Math.max(0, READER.idx);
  api('/api/reading', {
    method: 'POST',
    // clamp=false: 读者真读到了那儿 (拖滚动条一次滑到底是合法的) ——
    // 后端那个"一次跳太远就夹住"的闸是给手滑/脚本用的, 这里不适用。
    body: JSON.stringify({ idx: idx, force: force === true, clamp: false }),
  }).then(function (r) {
    const p = (r || {}).pos || {};
    if (state.story) state.story.read = p;
  }).catch(function () { /* 位置记不上不影响读 */ });
}

/** 从头读 (回到第一段)。 */
function readRestart() {
  const body = $('#adv-read-body');
  READER.idx = 0;
  READER.done = false;
  READER.drawn = 0;
  READER.tops = [];
  READER.bots = [];
  if (body) body.innerHTML = '';
  readEnsure(Math.min(READER.total, READER_LOOKAHEAD));
  // 撤掉挂着的存盘定时器 —— 它写的是"复位前"的位置
  if (readSaveTimer) { clearTimeout(readSaveTimer); readSaveTimer = 0; }
  // **加锁**: 复位那一滚是程序化滚动, 不是读者在读
  readLock();
  if (body) body.scrollTop = 0;
  readMeasureAll();
  readSync(false, false);
  // 「从头读」是**明确**的动作 —— 位置回到 0 (平时位置只前进)
  readSave(true);
}

/* 键盘: 空格翻一屏 / PageUp 回看 (不在输入框里时) */
document.addEventListener('keydown', function (e) {
  if (!READER.open) return;
  const t = e.target;
  if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA'
    || t.tagName === 'SELECT' || t.isContentEditable)) return;
  if (e.code === 'Space' || e.key === ' ') {
    e.preventDefault();
    readNext();
  } else if (e.key === 'PageDown') {
    e.preventDefault();
    readNext();
  } else if (e.key === 'PageUp' || (e.key === 'ArrowLeft' && e.altKey)) {
    e.preventDefault();
    readPrev();
  }
});

document.addEventListener('click', function (e) {
  if (e.target.closest('#btn-adv-next')) { readNext(); return; }
  if (e.target.closest('#btn-adv-restart')) { readRestart(); return; }
  if (e.target.closest('#btn-adv-again')) { readRestart(); return; }
  // 读本正文区点一下也算翻屏 (但别把点按钮算进去, 也不打断选字)
  const body = $('#adv-read-body');
  if (body && body.contains(e.target) && !e.target.closest('button')) {
    const sel = window.getSelection && window.getSelection().toString();
    if (!sel) readNext();
  }
});

/* 窗口内滚动了 (鼠标滚轮 / 触摸 / 程序滚动) → 同步"读到哪儿了"。
   位置**只前进**, 往回滚不抹掉已读的地方。 */
let readScrollTick = 0;
(function () {
  const el = document.querySelector('#adv-read-body');
  if (!el) return;
  el.addEventListener('scroll', function () {
    if (readScrollTick) return;
    readScrollTick = requestAnimationFrame(function () {
      readScrollTick = 0;
      readSync(true);
    });
  }, { passive: true });
})();

/* 窗口尺寸变了 → 之前量的段落几何作废, 重量一遍 */
window.addEventListener('resize', function () {
  if (!READER.open) return;
  readMeasureAll();
});

function renderChronicle(v) {
  const pill = $('#chronicle-pill');
  const box = $('#chronicle-state');
  if (!v) return;
  const c = v.config || {};
  const st = v.state || {};
  const tick = v.tick || {};
  if (pill) {
    if (!c.enabled) { pill.className = 'pill warn'; pill.textContent = '已关闭'; }
    else if (tick.running) { pill.className = 'pill'; pill.textContent = '推演中…'; }
    else { pill.className = 'pill ok'; pill.textContent = (st.cycle || 0) + ' 回合'; }
  }
  // 故事时钟 —— 页面第一眼要看到"活到第几天、几岁"
  const L = v.life || {};
  const clockPill = $('#story-clock');
  if (clockPill) {
    if (L.stage === 'retiring') {
      clockPill.className = 'pill warn';
      clockPill.textContent = L.age_label + ' · 已退隐';
    } else {
      clockPill.className = 'pill' + (L.day > 0 ? ' ok' : '');
      clockPill.textContent = (L.age_label || '12 岁') + ' · 第 ' + (L.generation || 1) + ' 代';
    }
  }
  const metaBox = $('#story-meta');
  if (metaBox) {
    const bits = [];
    bits.push('故事里第 ' + (L.day_no || 1) + ' 日');
    bits.push((L.age || 12) + ' 岁');
    if (L.days_to_retire > 0) {
      bits.push('距退隐还有 ' + Math.ceil(L.days_to_retire / 365) + ' 年');
    } else {
      bits.push('已到退隐之年');
    }
    if (st.waiting_note) bits.push(st.waiting_note);
    bits.push('已走 ' + (st.adventure_no || 0) + ' 回');
    bits.push('六分钟阅读 = 故事里十天');
    metaBox.innerHTML = bits.map(function (x) {
      return '<span class="story-bit">' + esc(String(x)) + '</span>';
    }).join('');
  }
  renderLife(L);
  const rows = [
    ['上一回合', st.last_at ? st.last_at.replace('T', ' ').slice(0, 16) : '还没跑过'],
    ['下一回合', st.next_at ? st.next_at.replace('T', ' ').slice(0, 16) : '未定'],
    ['节奏', c.enabled ? ('每 ' + c.interval_min + ' 分钟 · 看最近 ' + c.lookback_hours + ' 小时')
                       : '已关闭 (只能手动推演)'],
    ['游历', (st.adventure_no || 0) + ' 回'],
  ];
  if (st.last_summary) rows.push(['上次判词', st.last_summary]);
  if (st.last_story_error) {
    rows.push(['上一回没写出故事', st.last_story_error + ' (可点「补写上一回」)']);
  }
  if (st.last_fallback_why) rows.push(['判成长走了保底', st.last_fallback_why]);
  const lm = v.llm || {};
  if (lm.model) {
    rows.push(['推演模型', lm.model + (lm.reasoning ? ' · 推理型 (思考吃预算, 容易截断)' : '')]);
  }
  const lore = v.lore || {};
  rows.push(['书籍底细', (lore.books || 0) + ' 本立卡 · ' + (lore.chapters || 0) +
    ' 章储备 · 见过 ' + (lore.seen || 0) + ' 章']);
  if (tick.error) rows.push(['心跳异常', tick.error]);
  box.innerHTML = rows.map(function (kv) {
    return '<div class="cs-meta-row"><span class="cs-meta-l">' + esc(kv[0]) +
      '</span><span class="cs-meta-v">' + esc(String(kv[1])) + '</span></div>';
  }).join('');

  // 语料遭遇 —— 书宠联想的燃料
  const enc = v.encounters || {};
  const chipBox = $('#encounter-chips');
  if (chipBox) {
    const top = (enc.top || []).slice(0, 14);
    chipBox.innerHTML = top.length
      ? '<div class="cs-meta-row" style="grid-column:1/-1"><span class="cs-meta-l">语料遭遇</span>' +
        '<span class="cs-meta-v adv-chips">' +
        top.map(function (t) {
          return '<span class="adv-chip" title="' + esc(t.cat) + '">' + esc(t.term) +
            '<b>' + (t.count || 0) + '</b></span>';
        }).join('') +
        '</span></div>'
      : '<div class="cs-meta-row" style="grid-column:1/-1"><span class="cs-meta-l">语料遭遇</span>' +
        '<span class="cs-meta-v">近 7 天没有记录 (打开有语料的书才会记)</span></div>';
  }

  // 上一回合判了成长、故事却没写出来 → 给一个补写入口 (不重判成长)
  const bf = $('#btn-chronicle-backfill');
  if (bf) bf.hidden = !st.last_story_error;

  // 正文不再按回摆成卡片 —— 前台是**一条连续的路** (见 loadStory)。
  // 「第几回 / 交手 / 判定 / 来处」是**后台**的事, 去档案页看。
  const boxLatest = $('#adventure-latest');
  if (boxLatest) {
    const n = (state.story && state.story.total) || 0;
    boxLatest.innerHTML = n
      ? '<div class="adv-latest-note">已经走了 ' + n + ' 段。' +
        '<a href="/pet/story/">按回查阅</a></div>'
      : '<p class="tip">还没有出门。它会在下一次推演时自己上路 ——' +
        ' 也可以点上面的「出门走一趟」。</p>';
  }
}

/** 故事: 拉那条连续的路, 摊开读本。 */
async function loadStory() {
  try {
    const v = await api('/api/story');
    state.story = v;
    const flow = v.flow || [];
    const total = Number(v.total) || flow.length;
    const read = (v.read || {}).idx || 0;
    if (!total) {
      const body = $('#adv-read-body');
      if (body) body.innerHTML = '';
      const box = $('#adv-read');
      if (box) box.hidden = true;
      READER.open = false;
      return;
    }
    readOpen(flow, total, read);
  } catch (e) {
    setStatus($('#chronicle-status'), e.message, 'err');
  }
}

async function loadChronicle() {
  try {
    const v = await api('/api/chronicle');
    state.chronicle = v;
    renderChronicle(v);
    const c = v.config || {};
    if ($('#ch-enabled')) $('#ch-enabled').checked = !!c.enabled;
    if ($('#ch-interval')) $('#ch-interval').value = c.interval_min || 120;
    if ($('#ch-minmin')) $('#ch-minmin').value = c.min_minutes || 0;
    if ($('#ch-search')) $('#ch-search').checked = c.search !== false;
    await loadStory();
  } catch (e) {
    setStatus($('#chronicle-status'), e.message, 'err');
  }
}

function windowNote(r) {
  const w = r.window || {};
  const parts = [];
  if (w.from && w.to) {
    parts.push('这一回合的窗口 ' + String(w.from).slice(11, 16) + '–' + String(w.to).slice(11, 16));
  }
  if (r.prev_at) parts.push('上一回 ' + String(r.prev_at).slice(11, 16) + ' 刚结算过');
  const al = Number(r.already_today || 0);
  if (al > 0) parts.push('今天已计入 ' + al + ' 分钟');
  return parts.join(' · ');
}

function renderRunResult(r) {
  const box = $('#chronicle-out');
  if (!box) return;
  box.hidden = false;
  if (r.status === 'idle') {
    const n = windowNote(r);
    box.innerHTML = '<div class="adv-run">这段时间没有新的读书记录 (' +
      (r.minutes || 0) + ' 分钟) —— 等读了书再来。' +
      (n ? '<div class="adv-run-line">' + esc(n) + '</div>' : '') + '</div>';
    return;
  }
  if (r.status === 'busy') { box.textContent = '上一次推演还没结束。'; return; }
  if (r.status === 'waiting') {
    const n = windowNote(r);
    box.innerHTML = '<div class="adv-run">还不到下一站 —— 还差 ' +
      (r.short_days || 1) + ' 个故事日, 再读约 ' +
      (r.minutes_to_next_adventure || r.minutes_to_next_day || 0) +
      ' 分钟 (六分钟 = 故事里十天)。' +
      (n ? '<div class="adv-run-line">' + esc(n) + '</div>' : '') + '</div>';
    return;
  }
  if (r.status === 'retired') {
    box.innerHTML = '<div class="adv-run">已经退隐了 —— 等你在下面的「传承」里点头,' +
      ' 才会从那一位 12 岁重新开始。</div>';
    return;
  }
  if (r.status === 'error' || r.error) {
    box.innerHTML = '<div class="adv-run err">推演失败: ' + esc(r.error || '未知错误') + '</div>';
    return;
  }
  if (r.status === 'disabled') { box.textContent = '后台推演已关闭。'; return; }
  const j = r.judgement || {};
  const a = j.assessment || {};
  const g = (r.status === 'dry') ? (j.growth || {}) : (r.growth || {});
  const lines = [];
  if (r.status === 'dry') {
    lines.push('<div class="adv-run-head">这一回合会这么判 (未落地)</div>');
  } else {
    lines.push('<div class="adv-run-head">第 ' + (r.cycle || '?') + ' 回合 · ' +
      fmtMin(r.minutes) + ' · 修为 +' + (g.exp || 0) +
      (g.levels ? ' · 升 ' + g.levels + ' 级' : '') +
      (g.fame ? ' · 声望 +' + g.fame : '') + '</div>');
  }
  if (a.one_line) lines.push('<div class="adv-run-line">' + esc(a.one_line) + '</div>');
  if (a.depth || a.breadth) {
    lines.push('<div class="adv-run-line">深度: ' + esc(a.depth || '-') +
      ' · 广度: ' + esc(a.breadth || '-') + '</div>');
  }
  if (g.reason) lines.push('<div class="adv-run-reason">判词: ' + esc(g.reason) + '</div>');
  if (r.fallback) {
    const why = r.fallback_why ? ' —— ' + r.fallback_why : '';
    lines.push('<div class="adv-run-line warn">模型这一步没成, 走的是保底规则' +
      esc(why) + '</div>');
  }
  const reading = r.reading || [];
  if (Array.isArray(reading) && reading.length && typeof reading[0] === 'object') {
    lines.push('<ul class="adv-reading">' + reading.map(function (x) {
      return '<li>' + esc(x.title || x.book) + ' · ' + fmtMin(x.minutes) +
        (x.chapters ? ' · 推进 ' + x.chapters + ' 章' : '') +
        (x.notes ? ' · 划线 ' + x.notes : '') + '</li>';
    }).join('') + '</ul>');
  } else if (typeof r.reading === 'number') {
    lines.push('<div class="adv-run-line">涉及 ' + r.reading + ' 本书</div>');
  }
  if ((!reading || !reading.length) && !r.minutes) {
    const n = windowNote(r);
    if (n) lines.push('<div class="adv-run-line">' + esc(n) + '</div>');
  }
  if (r.duel) {
    const d = r.duel;
    lines.push('<div class="adv-run-line">遭遇 (' +
      (r.status === 'dry' ? '预演, 不落盘' : '已结算') + '): ' +
      esc((d.foe || {}).name || '有人挡路') + ' · ' + esc(d.result_cn || '') +
      ' · 气血 ' + ((d.hp || {}).from) + ' → ' + ((d.hp || {}).to) + '</div>');
    if ((d.moves || []).length) {
      lines.push('<div class="adv-run-line">使过: ' + d.moves.map(esc).join(' / ') + '</div>');
    }
  }
  // 这一趟练到了什么 (熟练度): 升级与"只攒了熟练"分开写
  const prac = (r.practice && r.practice.skills) || (g.practice || []);
  if (prac.length) {
    lines.push('<div class="adv-run-line">练了: ' + prac.slice(0, 5).map(function (k) {
      return esc(k.name || k.id) + ' 熟练 +' + (k.gain || 0) +
        (k.leveled ? ' (升到 ' + k.level + ' 级)' : '');
    }).join(' · ') + '</div>');
  }
  if ((r.checks || []).length) {
    lines.push('<div class="adv-run-line">路上的坎: ' + r.checks.map(function (c) {
      return esc(c.label) + ' ' + (c.success ? '过' : '没过') +
        ' (' + c.total + '/' + c.dc + ')';
    }).join(' · ') + '</div>');
  }
  if (r.adventure) {
    lines.push('<div class="adv-run-line">游历: ' + esc(r.adventure.title) + '</div>');
  }
  box.innerHTML = lines.join('');
}

$('#btn-chronicle-run').onclick = async () => {
  const st = $('#chronicle-status');
  try {
    setStatus(st, '推演中… (要调两次模型, 约 20~60 秒)');
    const r = await api('/api/chronicle/run', {
      method: 'POST', body: JSON.stringify({ force: true })
    });
    renderRunResult(r);
    if (r.char) renderChar(r.char);
    setStatus(st, r.status === 'ok' ? '完成' : (r.status || 'done'),
      r.status === 'ok' ? 'ok' : '');
    await loadChronicle();
  } catch (e) { setStatus(st, e.message, 'err'); }
};

$('#btn-chronicle-dry').onclick = async () => {
  const st = $('#chronicle-status');
  try {
    setStatus(st, '试算中…');
    const r = await api('/api/chronicle/run', {
      method: 'POST', body: JSON.stringify({ dry: true })
    });
    renderRunResult(r);
    setStatus(st, '只是试算, 没有落地', '');
  } catch (e) { setStatus(st, e.message, 'err'); }
};

$('#btn-chronicle-refresh').onclick = () => { loadChronicle(); };

$('#btn-chronicle-backfill').onclick = async () => {
  const st = $('#chronicle-status');
  const btn = $('#btn-chronicle-backfill');
  try {
    btn.disabled = true;
    setStatus(st, '补写中…');
    const r = await api('/api/adventure/backfill', {
      method: 'POST', body: JSON.stringify({})
    });
    setStatus(st, '补写了「' + ((r.story || {}).title || '一回') + '」', 'ok');
    await loadChronicle();
  } catch (e) {
    setStatus(st, e.message, 'err');
  } finally { btn.disabled = false; }
};

$('#btn-chronicle-save').onclick = async () => {
  const st = $('#chronicle-cfg-status');
  try {
    await api('/api/config', {
      method: 'POST',
      body: JSON.stringify({ chronicle: {
        enabled: $('#ch-enabled').checked,
        interval_min: Number($('#ch-interval').value) || 120,
        min_minutes: Number($('#ch-minmin').value) || 0,
        search: $('#ch-search').checked,
      } })
    });
    setStatus(st, '已保存 (间隔改了下一轮就生效)', 'ok');
    await loadChronicle();
  } catch (e) { setStatus(st, e.message, 'err'); }
};


/* ───────────────────────── 传承 (八十岁 → 十二岁) ───────────────────────── */

function renderLife(L) {
  const card = $('#card-legacy');
  if (!card) return;
  const show = !!(L && (L.stage === 'retiring' || L.ready_to_retire));
  card.hidden = !show;
  if (!show) return;

  state.life = L;
  const heir = L.heir || {};
  const types = L.heir_types || [];
  if (!state.heirType || !types.some(function (t) { return t.id === state.heirType; })) {
    state.heirType = heir.type || (types[0] && types[0].id) || 'disciple';
  }
  if (!state.heirName) state.heirName = heir.name || '';

  const pill = $('#legacy-pill');
  if (pill) {
    pill.className = 'pill warn';
    pill.textContent = L.stage === 'retiring' ? '退隐 · 等确认' : '到岁数了 · 可退隐';
  }

  const box = $('#legacy-state');
  if (box) {
    const rows = [
      ['这一代', '第 ' + (L.generation || 1) + ' 代 · ' + (L.age || 12) + ' 岁'],
      ['故事里', '第 ' + (L.day_no || 1) + ' 日 · 走了 ' + Math.round((L.day || 0) / 365 * 10) / 10 + ' 年'],
      ['退隐', L.stage === 'retiring'
        ? ('已写下第 ' + (L.last_retire_story || 0) + ' 回' +
           (L.retire_story_title ? ' · ' + L.retire_story_title : '') +
           (L.retired_at ? ' · ' + String(L.retired_at).replace('T', ' ').slice(0, 16) : ''))
        : ('还差 ' + Math.max(0, (L.days_to_retire || 0)) + ' 个故事日 (约 ' + Math.ceil((L.days_to_retire || 0) / 365) + ' 年)')],
    ];
    if ((L.lineage || []).length) {
      rows.push(['上一代', (L.lineage || []).map(function (x) {
        return (x.name || '') + ' · ' + (x.years || 0) + ' 年 · Lv.' + (x.level || 1);
      }).join(' / ')]);
    }
    box.innerHTML = rows.map(function (kv) {
      return '<div class="cs-meta-row"><span class="cs-meta-l">' + esc(kv[0]) +
        '</span><span class="cs-meta-v">' + esc(String(kv[1])) + '</span></div>';
    }).join('');
  }

  const hb = $('#legacy-heir');
  if (hb) {
    hb.innerHTML =
      '<div class="legacy-pick">' + types.map(function (t) {
        return '<button class="legacy-opt' + (t.id === state.heirType ? ' is-on' : '') +
          '" data-heir="' + esc(t.id) + '"><b>' + esc(t.name) + '</b>' +
          '<span>' + esc(t.desc || '') + '</span></button>';
      }).join('') + '</div>' +
      '<label class="legacy-name">传承人名<input id="legacy-name" maxlength="24" value="' +
      esc(state.heirName || '') + '" placeholder="两个字到三个字"></label>' +
      (heir.reason ? '<p class="legacy-why">' + esc(heir.reason) + '</p>' : '') +
      '<p class="tip">确认后这一代的一生会存档（' +
      esc((L.lineage || []).length ? '已有 ' + L.lineage.length + ' 代记录' : '这是第一代') +
      '），新人从 12 岁起，只带走一份家学（最高那门功夫的三成）、一点声望与余荫。</p>';
  }
}

document.addEventListener('click', function (e) {
  const opt = e.target.closest('[data-heir]');
  if (opt) {
    state.heirType = opt.dataset.heir;
    renderLife(state.life || {});
    return;
  }
  if (!e.target.closest('#btn-inherit')) return;
  const st = $('#legacy-status');
  const name = (document.getElementById('legacy-name') || {}).value || state.heirName || '';
  const label = ({ child: '子女', disciple: '徒弟', student: '学生' })[state.heirType] || '徒弟';
  if (!confirm('确认传承? 这一代清空 —— 新人从 12 岁起 (' + label + ' · ' + (name || '小墨') + ')。')) return;
  setStatus(st, '传承中…');
  api('/api/char/inherit', {
    method: 'POST',
    body: JSON.stringify({ heir_type: state.heirType, name: name, confirm: true }),
  }).then(function (r) {
    state.heirType = '';
    state.heirName = '';
    if (r.char) renderChar(r.char);
    if (r.life) renderLife(r.life);
    setStatus(st, '已传承 · 第 ' + ((r.char && r.char.generation) || '?') + ' 代从 12 岁起', 'ok');
    loadChronicle();
  }).catch(function (err) { setStatus(st, err.message, 'err'); });
});

document.addEventListener('input', function (e) {
  if (e.target && e.target.id === 'legacy-name') state.heirName = e.target.value;
});

/* ───────────────────────── 启动 ───────────────────────── */

$('#btn-companion-feed').onclick = async () => {
  const st = $('#companion-status');
  try {
    setStatus(st, '折算中…');
    const r = await api('/api/companion', { method: 'POST', body: '{}' });
    const g = r.gain || {};
    if (g.grew) {
      setStatus(st, '折算 ' + g.minutes + ' 分钟' +
        (g.exp ? (' · 修为 +' + g.exp + (g.levels ? ' · 升 ' + g.levels + ' 级' : ''))
               : ' (修为归「游历」里的后台推演判定)'), 'ok');
    } else {
      setStatus(st, '没有新增陪读时长 (已折算过)', '');
    }
    if (r.char) renderChar(r.char);
    await loadCompanion();
  } catch (e) { setStatus(st, e.message, 'err'); }
};

/* ───────────────────────── 游戏化 HUD / 子页 / 世界 / 复位 ─────────
 * 为什么有这一层: 面板上摆着十个卡片, 读故事时要一路滚过设置项。
 * 现在拆成 4 个子页: 故事 (默认) / 人物 / 世界 / 设置, 页内切换不重载。
 * 顶部状态栏只放"每回合都在变的数": 气血 / 内力 / 修为 / 声望 / 潜能 / 岁数。
 */

function barGauge(label, cur, max, cls) {
  const pct = max > 0 ? Math.max(0, Math.min(100, Math.round(cur * 100 / max))) : 0;
  return '<div class="pg-gauge"><span class="pg-gauge-l">' + esc(label) + '</span>' +
    '<div class="pg-gauge-bar ' + (cls || '') + '"><i style="width:' + pct + '%"></i></div>' +
    '<span class="pg-gauge-v">' + cur + ' / ' + max + '</span></div>';
}

function renderBar(ch) {
  if (!ch) return;
  const put = function (id, v) {
    const el = document.getElementById(id);
    if (el) el.textContent = v;
  };
  put('bar-name', ch.name || '小墨');
  put('bar-level', 'Lv.' + (ch.level || 1));
  put('bar-rank', ch.rank || '初入江湖');
  const L = ch.life || {};
  const bits = [L.age_label || '12 岁', '故事第 ' + (L.day_no || 1) + ' 日',
                '第 ' + (L.generation || ch.generation || 1) + ' 代'];
  if (L.stage === 'retiring') bits.push('已退隐 · 待传承');
  put('bar-life', bits.join(' · '));

  const st = ch.stats || {};
  const hp = st.hp || {};
  const nl = st.neili || {};
  const g = document.getElementById('bar-gauges');
  if (g) {
    g.innerHTML = barGauge('气血', hp.cur || 0, hp.max || 0, 'hp') +
      barGauge('内力', nl.cur || 0, nl.max || 0, 'neili') +
      barGauge('修为', ch.exp || 0, ch.exp_needed || 0, 'exp');
  }
  const c = document.getElementById('bar-chips');
  if (c) {
    const rows = [
      '<span class="pg-chip">声望 <b>' + (ch.fame || 0) + '</b></span>',
      '<span class="pg-chip">潜能 <b>' + (ch.pot || 0) + '</b></span>',
      '<span class="pg-chip">陪读 <b>' + (ch.minutes || 0) + '</b> 分钟</span>',
      '<span class="pg-chip">经脉 <b>' + (ch.vein || 0) + '</b>/10</span>',
    ];
    if (ch.school) rows.push('<span class="pg-chip">门派 <b>' + esc(ch.school.name) + '</b></span>');
    c.innerHTML = rows.join('');
  }
  // 转生时的"来历"写在这里 (它自己说不清的那点东西)
  const bs = document.getElementById('char-backstory');
  if (bs) {
    const txt = (L && L.backstory) || '';
    bs.textContent = txt;
    bs.hidden = !txt;
  }
  pgRebornHint();
}


/* ══════════════ 人物抽屉 (图标唤起) ══════════════
 *
 * 原来「人物」是一个独立子页签, 占了整页。现在它收成右侧抽屉:
 * 顶部一排图标 (人物 / 行囊 / 本事 / 陪读), 点一下浮出来, 里面再分小段。
 *
 * 页面顶签从 4 个变 3 个 (故事 / 世界 / 设置) —— 故事是主角, 不该被别的事挤掉。
 */

const DRAWER_TABS = {
  char: { label: '人物' },
  bag: { label: '行囊' },
  skills: { label: '本事' },
  companion: { label: '陪读' },
};

let drawerLast = 'char';

function drawerShow(key) {
  const dr = $('#pg-drawer');
  if (!dr) return;
  const k = DRAWER_TABS[key] ? key : 'char';
  drawerLast = k;
  dr.hidden = false;
  // 抽屉里的小段
  document.querySelectorAll('.pg-dt-pane').forEach(function (el) {
    el.classList.toggle('is-on', el.dataset.dt === k);
  });
  document.querySelectorAll('.pg-dt-tab').forEach(function (el) {
    const on = el.dataset.dt === k;
    el.classList.toggle('is-on', on);
    el.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  const title = $('#pg-drawer-title');
  if (title) title.textContent = (DRAWER_TABS[k] || {}).label || '人物';
  // 图标的选中态
  document.querySelectorAll('.pg-ico-btn').forEach(function (el) {
    el.classList.toggle('is-on', el.dataset.drawer === k);
  });
  document.body.classList.add('pg-drawer-open');
  const closeBtn = $('.pg-drawer-x');
  if (closeBtn) closeBtn.focus();
}

function drawerHide() {
  const dr = $('#pg-drawer');
  if (!dr) return;
  dr.hidden = true;
  document.body.classList.remove('pg-drawer-open');
  const btn = document.querySelector('.pg-ico-btn[data-drawer="' + drawerLast + '"]');
  if (btn) btn.focus();
}

document.addEventListener('click', function (e) {
  const ico = e.target.closest('.pg-ico-btn');
  if (ico) {
    drawerShow(ico.dataset.drawer);
    return;
  }
  const dt = e.target.closest('.pg-dt-tab');
  if (dt) {
    drawerShow(dt.dataset.dt);
    return;
  }
  if (e.target.closest('[data-drawer-close]')) {
    drawerHide();
  }
});

document.addEventListener('keydown', function (e) {
  const dr = $('#pg-drawer');
  if (!dr || dr.hidden) return;
  if (e.key === 'Escape') {
    e.preventDefault();
    drawerHide();
  }
});

/* ── 页签 ─────────────────────────────────────────────────────── */
const PG_PANES = ['story', 'world', 'setup'];

function pgShow(name) {
  if (PG_PANES.indexOf(name) < 0) name = 'story';
  document.querySelectorAll('.pg-pane').forEach(function (el) {
    el.classList.toggle('is-on', el.dataset.pg === name);
  });
  document.querySelectorAll('.pg-tab').forEach(function (el) {
    const on = el.dataset.pg === name;
    el.classList.toggle('is-on', on);
    el.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  try { history.replaceState(null, '', '#' + name); } catch (e) { /* file:// 之类 */ }
}

function pgInit() {
  const want = (location.hash || '').replace('#', '');
  // 老书签 (#hero) → 打开人物抽屉 (那个页签已经收进抽屉了)
  if (want === 'hero') {
    pgShow('story');
    drawerShow('char');
    try { history.replaceState(null, '', '#story'); } catch (e) { /* file:// 之类 */ }
  } else {
    pgShow(PG_PANES.indexOf(want) >= 0 ? want : 'story');
  }
  // 委托: 页签是页内切换, 不许触发导航 (老坑: 拦了却没换页 → 哑链接)
  document.addEventListener('click', function (e) {
    const tab = e.target.closest('.pg-tab');
    if (!tab) return;
    e.preventDefault();
    pgShow(tab.dataset.pg);
  });
}

/* ── 世界页 ───────────────────────────────────────────────────── */
function worldCol(rows, kind) {
  if (!rows || !rows.length) return '<div class="pg-empty">还没有。</div>';
  return rows.map(function (x) {
    return '<div class="pg-world-item"><span class="n">' + esc(x.name || '') + '</span>' +
      (x.role ? '<span class="r">' + esc(x.role) + '</span>' : '') +
      (x.note ? '<span class="d">' + esc(x.note) + '</span>' : '') + '</div>';
  }).join('');
}

async function loadWorld() {
  try {
    const r = await api('/api/world');
    const pill = document.getElementById('world-pill');
    if (pill) {
      pill.className = 'pill ok';
      pill.textContent = (r.source === 'model' ? '模型铺的' : '内置底本') +
        ' · ' + ((r.counts && r.counts.regions) || 0) + ' 处';
    }
    const nm = document.getElementById('world-name');
    if (nm) nm.textContent = r.name || '—';
    const meta = document.getElementById('world-meta');
    if (meta) {
      const rows = [];
      if (r.era) rows.push(['年代', r.era]);
      if (r.law) rows.push(['规矩', r.law]);
      meta.innerHTML = rows.map(function (kv) {
        return '<div class="cs-meta-row"><span class="cs-meta-l">' + esc(kv[0]) +
          '</span><span class="cs-meta-v">' + esc(String(kv[1])) + '</span></div>';
      }).join('');
    }
    const put = function (id, html) {
      const el = document.getElementById(id);
      if (el) el.innerHTML = html;
    };
    put('world-regions', worldCol(r.regions));
    put('world-factions', worldCol(r.factions));
    put('world-notables', worldCol(r.notables));
    put('world-things', worldCol(r.things));
    state.world = r;
  } catch (e) {
    setStatus(document.getElementById('world-status'), e.message, 'err');
  }
}

async function loadProper() {
  try {
    const r = await api('/api/proper');
    const pill = document.getElementById('proper-pill');
    if (pill) {
      pill.className = 'pill';
      pill.textContent = (r.words || 0) + ' 个专属名';
    }
    const st = document.getElementById('proper-state');
    if (st) {
      const rows = [
        ['挡在门外的词', String(r.words || 0) + ' 个 (书名 / 作者 / 人地门派 / 器物)'],
        ['它自己的名字', String((r.allowed || []).length) + ' 个 (永不拦)'],
        ['判定', '故事写完照表过一遍; 命中 → 重写一次 → 仍命中则替换成它自己的名字'],
      ];
      st.innerHTML = rows.map(function (kv) {
        return '<div class="cs-meta-row"><span class="cs-meta-l">' + esc(kv[0]) +
          '</span><span class="cs-meta-v">' + esc(kv[1]) + '</span></div>';
      }).join('');
    }
    const ch = document.getElementById('proper-chips');
    if (ch) {
      ch.innerHTML = (r.sample || []).slice(0, 28).map(function (w) {
        return '<span class="pg-chip">' + esc(w) + '</span>';
      }).join('') || '<div class="pg-empty">还没有读到带专名的书。</div>';
    }
  } catch (e) { /* 专名闸门读不到不挡页面 */ }
}

/* ── 复位 / 转生 ─────────────────────────────────────────────── */
function pgRebornHint() {
  const inp = document.getElementById('reborn-inherit');
  const lab = document.getElementById('reborn-val');
  const hint = document.getElementById('reborn-hint');
  const st = document.getElementById('reborn-state');
  const pill = document.getElementById('reborn-pill');
  const ch = state.char || {};
  const cap = Math.max(0, ch.minutes || 0);
  if (!inp) return;
  if (inp.dataset.cap !== String(cap)) {
    inp.dataset.cap = String(cap);
    inp.max = String(cap);
    inp.step = String(cap > 200 ? Math.round(cap / 100) : (cap > 0 ? 10 : 10));
    if (Number(inp.value) > cap) inp.value = String(cap);
    if (pill) {
      pill.className = 'pill';
      pill.textContent = cap > 0 ? ('可继承 ' + cap + ' 分钟') : '还没有阅历';
    }
  }
  const mpd = (state.char && state.char.life && state.char.life.minutes_per_day) || 0.6;
  const take = Math.max(0, Math.min(cap, Number(inp.value) || 0));
  if (lab) lab.textContent = take + ' 分钟';
  if (hint) {
    if (take <= 0) {
      hint.textContent = '白纸复位: 12 岁, 无门派, 故事清空。';
    } else {
      const days = Math.floor(take / mpd);
      const age = 12 + Math.floor(days / 365);
      hint.textContent = '转生: 故事内第 ' + (days + 1) + ' 日 · 起点 ' + age + ' 岁 · 约 ' +
        (take * 2) + ' 修为 · 基本内功 ' + Math.floor(take / 300) + ' 级 · 声望约 ' +
        Math.floor(take / 600) + '。';
    }
  }
  if (st) {
    const rows = [
      ['当前', (ch.name || '小墨') + ' · Lv.' + (ch.level || 1) + ' · ' +
        ((ch.life && ch.life.age_label) || '12 岁')],
      ['清空', '往期故事与回合留痕 (世界 / 书卡 / 足迹都留着)'],
      ['保留', '上一代的一生会存档到 lineage/'],
    ];
    st.innerHTML = rows.map(function (kv) {
      return '<div class="cs-meta-row"><span class="cs-meta-l">' + esc(kv[0]) +
        '</span><span class="cs-meta-v">' + esc(String(kv[1])) + '</span></div>';
    }).join('');
  }
}

async function doReborn() {
  const inp = document.getElementById('reborn-inherit');
  const nm = document.getElementById('reborn-name');
  const st = document.getElementById('reborn-status');
  const take = Math.max(0, Number((inp || {}).value) || 0);
  const name = ((nm || {}).value || '').trim();
  const label = take > 0 ? ('转生 (带走 ' + take + ' 分钟阅历)') : '复位 (白纸)';
  if (!confirm('确认' + label + '?\n\n这一下不可回退: 往期故事会清空, ' +
               '世界与书卡留着。' + (take > 0 ? '新一代会带着那段阅历重来。' : ''))) return;
  setStatus(st, '正在重开…');
  try {
    const r = await api('/api/char/reborn', {
      method: 'POST',
      body: JSON.stringify({ confirm: true, inherit_minutes: take, name: name }),
    });
    if (r.char) renderChar(r.char);
    if (inp) inp.value = '0';
    await loadChronicle();
    await loadWorld();
    pgShow('story');
    const back = (r.char && r.char.life && r.char.life.backstory) || r.backstory || '';
    setStatus(st, '已重开 · 第 ' + ((r.char && r.char.generation) || 1) + ' 代' +
      (back ? ' · ' + back.slice(0, 40) : ''), 'ok');
  } catch (e) {
    setStatus(st, e.message, 'err');
  }
}

document.addEventListener('input', function (e) {
  if (e.target && e.target.id === 'reborn-inherit') pgRebornHint();
});
document.addEventListener('click', function (e) {
  if (e.target.closest('#btn-reborn')) { doReborn(); return; }
  if (!e.target.closest('#btn-world-regen')) return;
  const st = document.getElementById('world-status');
  if (!confirm('重新铺一次世界?\n\n地名 / 势力 / 人物会换掉一批, 往期故事里用过的名字' +
               '不会跟着改 —— 建议只在刚开局时重铺。')) return;
  setStatus(st, '铺底中… (要调一次模型)');
  api('/api/world/regen', { method: 'POST', body: '{}' })
    .then(async function (r) {
      await loadWorld();
      setStatus(st, '已重铺 · ' + (r.name || ''), 'ok');
    })
    .catch(function (err) { setStatus(st, err.message, 'err'); });
});

(async function init() {
  pgInit();
  await loadPresets();
  await loadConfig();
  await loadChar();
  await loadCompanion();
  await loadChronicle();
  await loadWorld();
  await loadProper();
})();
