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
};

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

/* ───────────────────────── 人物面板 (武侠) ───────────────────────── */

const TYPE_CN = { basic: '基本功', weapon: '兵器', fist: '拳脚', qinggong: '轻功',
                   neigong: '内功', perform: '绝招', art: '杂学' };
const EFFECT_CN = { damage: '伤害', control: '控住', defend: '守势',
                    evade: '身法', heal: '疗伤', buff: '涨己' };

function attrName(key) {
  const a = (state.schema && state.schema.attrs || []).find((x) => x.key === key);
  return a ? a.name : key;
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
  renderLife(ch.life);

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
    rows.push(['修为', ch.exp + ' / ' + ch.exp_needed]);
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
    pot.textContent = '潜能 ' + (ch.pot || 0);
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
        '<td class="num">' + s.level + '</td>' +
        '<td class="num cs-dim">' + (s.cap != null ? s.cap : '—') + '</td>' +
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
        ('每 ' + g.auto_skill_minutes_per_level + ' 分钟') : '—') + '</span></div>' +
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

/* ── 掷骰 (d20) 的展示零件 ──────────────────────────────────────
   后端把每一次掷骰拆成 mods (根骨 / 技能 / 禀赋 / 情境), 这里只负责画。
   不在这里算任何数 —— 页面上的每一个数都是后端掷出来的。 */

const SRC_CN = { attr: '根骨', skill: '技能', trait: '禀赋', situ: '情境', gear: '器物' };

function signed(n) {
  n = Number(n) || 0;
  return (n > 0 ? '+' : '') + n;
}

function d20Verdict(c) {
  if (c.crit) return '大成功';
  if (c.fumble) return '大失败';
  const kind = String(c.kind || '');
  if (kind === 'attack') return c.success ? '中' : '不中';
  if (kind === 'perform') return c.success ? '成了' : '没成';
  return c.success ? '过' : '没过';
}

function rollRow(c) {
  if (!c) return '';
  const mods = (c.mods || []).map(function (m) {
    return '<span class="d20-mod" data-src="' + esc(m.src || '') + '" title="' +
      esc(SRC_CN[m.src] || m.src || '') + '">' + esc(m.name) + ' <b>' +
      signed(m.value) + '</b></span>';
  }).join('');
  const bonus = Number(c.bonus || 0)
    ? '<span class="d20-mod" data-src="situ">情境 <b>' + signed(c.bonus) + '</b></span>'
    : '';
  const cls = c.crit ? 'crit' : (c.fumble ? 'fumble' : (c.success ? 'ok' : 'err'));
  const dc = Number(c.dc || 0)
    ? '= ' + c.total + ' vs ' + c.dc +
      (c.dc_label ? ' <i>' + esc(c.dc_label) + '</i>' : '')
    : '= ' + c.total;
  return '<div class="d20-row ' + cls + '">' +
    '<span class="d20-k">' + esc(c.label || c.kind_cn || '检定') + '</span>' +
    '<span class="d20-face" title="d20">' + esc(String(c.d20)) + '</span>' +
    '<span class="d20-mods">' + mods + bonus + '</span>' +
    '<span class="d20-tot">' + dc + '</span>' +
    '<span class="d20-v">' + esc(d20Verdict(c)) + '</span>' +
    '</div>';
}

function rollsBlock(rolls) {
  if (!rolls || !rolls.length) return '';
  return '<div class="d20-rolls">' + rolls.map(rollRow).join('') + '</div>';
}

function diceLine(dice) {
  if (!dice || !dice.faces) return '';
  return '<div class="d20-dice">伤害骰 ' + esc(dice.text || '') + '</div>';
}

/** 双方交手数据 (护体 / 命中 / 伤害骰) —— 点开才看 */
function fightersBlock(f, init) {
  if (!f || !f.me) return '';
  function side(k, label) {
    const x = f[k] || {};
    const dmg = x.damage || {};
    return '<div class="duel-side"><b>' + esc(x.name || label) + '</b>' +
      '<span>护体 <b>' + (x.ac == null ? '—' : x.ac) + '</b></span>' +
      '<span>命中 <b>' + signed(x.attack) + '</b></span>' +
      '<span>伤害 <b>' + esc(dmg.text || '—') + '</b></span>' +
      '<span>卸力 <b>' + (x.pierce == null ? '—' : x.pierce) + '</b></span>' +
      (init && init[k] ? '<span>先攻 <b>' + init[k].total + '</b></span>' : '') +
      '</div>';
  }
  return '<details class="duel-stats"><summary>交手数据与规则</summary>' +
    '<div class="duel-sides">' + side('me', '我方') + side('foe', '对方') + '</div>' +
    (init && init.text ? '<div class="cs-dim">' + esc(init.text) + '</div>' : '') +
    '</details>';
}

/** 一拍的正文 + 掷骰明细 */
function duelRound(r, i) {
  const tag = r.side === 'me' ? '我' : '敌';
  const move = r.move ? '<span class="duel-move">「' + esc(r.move) + '」</span>' : '';
  return '<li class="duel-round" data-kind="' + esc(r.kind || '') + '">' +
    '<span class="duel-r">' + (r.r || i + 1) + '</span>' +
    '<span class="duel-side-tag">' + tag + '</span>' +
    '<div class="duel-body"><div class="duel-text">' + move + esc(r.text || '') +
    '</div>' + rollsBlock(r.rolls) + diceLine(r.dice) + '</div>' +
    '</li>';
}

/** 这一回真打的那一场 (后端按 d20 算出来的战报, 不是编的) */
function duelBlock(d) {
  if (!d || !d.foe) return '';
  const rounds = d.rounds || [];
  const res = d.result === 'win' ? 'ok' : (d.result === 'lose' ? 'err' : 'warn');
  const hd = rounds.slice(0, 3);
  return '<div class="adv-duel">' +
    '<div class="adv-duel-h"><span class="pill ' + res + '">' +
      esc(d.result_cn || '') + '</span>' +
      '<b>' + esc(d.foe.name || '对手') + '</b>' +
      '<span class="cs-dim">' + esc(d.foe.arena || '') +
      (d.foe.weapon_cn ? ' · ' + esc(d.foe.weapon_cn) : '') + '</span>' +
      '<span class="cs-dim">' + esc(d.summary || '') + '</span></div>' +
    '<div class="adv-duel-r">' + hd.map(function (r) {
      return '<span>' + esc(r.text || '') + '</span>';
    }).join('') + '</div>' +
    (d.moves && d.moves.length
      ? '<div class="adv-chips">' + d.moves.map(function (m) {
          return '<span class="adv-chip ok">使 ' + esc(m) + '</span>';
        }).join('') + '</div>'
      : '') +
    (rounds.length
      ? '<details class="duel-log"><summary>完整战报 · ' + rounds.length + ' 拍 · ' +
        (d.rolls || 0) + ' 次掷骰</summary>' +
        '<ol class="duel-rounds">' + rounds.map(duelRound).join('') + '</ol>' +
        '</details>'
      : '') +
    fightersBlock(d.fighters, d.initiative) +
    '<div class="cs-dim adv-duel-f">气血 ' + (d.hp ? d.hp.from + ' → ' + d.hp.to +
      ' / ' + d.hp.max : '—') + (d.fame ? ' · 声望 +' + d.fame : '') +
      (d.neili ? ' · 内力 ' + d.neili.from + ' → ' + d.neili.to + ' / ' + d.neili.max : '') +
      '</div>' +
    '</div>';
}

/** 路上的坎 (不打斗的判定) */
function checksBlock(list) {
  if (!list || !list.length) return '';
  return '<div class="adv-checks">' +
    '<div class="adv-checks-h"><b>路上的坎</b>' +
    '<span class="cs-dim">' + list.map(function (c) {
      return esc(c.label || '') + ' ' + (c.success ? '✓' : '✗');
    }).join(' · ') + '</span></div>' +
    '<ol class="adv-check-list">' + list.map(function (c) {
      const pill = c.fumble ? 'err' : (c.success ? 'ok' : 'warn');
      return '<li class="adv-check"><span class="pill ' + pill + '">' +
        esc(c.degree_cn || (c.success ? '过' : '没过')) + '</span>' +
        '<b>' + esc(c.label || '') + '</b>' +
        '<span class="cs-dim">' + esc(c.target || '') + '</span>' +
        (c.white ? '<span class="adv-chip">白检定</span>'
                 : '<span class="adv-chip warn">红检定 · 一次定生死</span>') +
        '<span class="adv-check-out">' + esc(c.outcome || '') + '</span>' +
        rollRow(c) + '</li>';
    }).join('') + '</ol></div>';
}

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
    bits.push('一小时阅读 = 十天');
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

  // 最新一回
  const latest = (v.adventures || [])[0];
  // 上一回合判了成长、故事却没写出来 → 给一个补写入口 (不重判成长)
  const bf = $('#btn-chronicle-backfill');
  if (bf) bf.hidden = !st.last_story_error;
  const boxLatest = $('#adventure-latest');
  if (boxLatest) {
    if (!latest) {
      boxLatest.innerHTML = '<p class="tip">还没有游历。它会在下一次推演时自己出门 ——' +
        ' 也可以点上面的「现在推演一回」。</p>';
    } else {
      const g = latest.growth || {};
      const show = [];
      if (g.days) show.push('故事推进 ' + g.days + ' 日');
      if (g.exp) show.push('修为 +' + g.exp);
      (g.skills || []).forEach(function (k) {
        show.push((k.name || k.id) + ' ' + k.from + '→' + k.to + ' 级');
      });
      (g.attrs || []).forEach(function (a) {
        show.push((a.name || a.key) + ' ' + a.from + '→' + a.to);
      });
      if (g.vein) show.push('通经 ' + g.vein + ' 层');
      if (g.trait) show.push('觉醒「' + g.trait + '」');
      if (g.tag === 'retire') show.push('退隐 · 待传承');
      boxLatest.innerHTML =
        '<article class="adv-card' + (g.tag === 'retire' ? ' is-retire' : '') + '"><header>' +
        '<span class="adv-no">第 ' + latest.no + ' 回</span>' +
        '<h3>' + esc(latest.title) + '</h3>' +
        (latest.place ? '<span class="adv-place">' + esc(latest.place) + '</span>' : '') +
        '<span class="adv-at">' + esc(String(latest.at || '').replace('T', ' ').slice(0, 16)) +
        '</span></header>' +
        (show.length
          ? '<div class="adv-growth"><span class="adv-growth-l">本回所显</span>' +
            show.map(function (t) {
              return '<span class="adv-chip ok">' + esc(t) + '</span>';
            }).join('') + '</div>'
          : '') +
        duelBlock(g.duel) +
        '<div class="adv-body">' + advParagraphs(latest.body) + '</div>' +
        checksBlock(g.checks) +
        (latest.terms && latest.terms.length
          ? '<div class="adv-chips">' + latest.terms.map(function (t) {
              return '<span class="adv-chip">' + esc(t) + '</span>';
            }).join('') + '</div>'
          : '') +
        '</article>';
    }
  }

  // 往期
  const list = $('#adventure-list');
  if (list) {
    const older = (v.adventures || []).slice(1);
    list.innerHTML = older.length
      ? older.map(function (a) {
          return '<details class="adv-item"><summary><b>第 ' + a.no + ' 回</b> ' +
            esc(a.title) + '<span class="adv-at">' +
            esc(String(a.at || '').replace('T', ' ').slice(0, 16)) + '</span></summary>' +
            '<div class="adv-body">' + advParagraphs(a.body) + '</div>' +
            checksBlock((a.growth || {}).checks) + '</details>';
        }).join('')
      : '<p class="tip">还没有更早的游历。</p>';
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
    box.innerHTML = '<div class="adv-run">还不到下一日 —— 再读 ' +
      (r.minutes_to_next_day || 0) + ' 分钟 (一小时 = 故事里十天)。' +
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

(async function init() {
  await loadPresets();
  await loadConfig();
  await loadChar();
  await loadCompanion();
  await loadChronicle();
})();
