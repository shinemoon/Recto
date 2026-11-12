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

const TYPE_CN = { basic: '基本功', kungfu: '武学', neigong: '内功', perform: '绝招' };

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
    const wx = ch.wuxing || {};
    rows.push(['五行', Object.keys(wx).map((k) => k + wx[k]).join(' ') || '—']);
    meta.innerHTML = rows.map((r) =>
      '<div class="cs-meta-row"><span class="cs-meta-l">' + esc(r[0]) +
      '</span><span class="cs-meta-v">' + esc(r[1]) + '</span></div>').join('');
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

  // 技能与绝招
  const sk = $('#skill-rows');
  if (sk) {
    sk.innerHTML = '';
    (ch.skills || []).forEach((s) => {
      const tr = document.createElement('tr');
      const perf = s.type === 'perform';
      tr.innerHTML =
        '<td class="nowrap"><b>' + esc(s.name) + '</b></td>' +
        '<td class="nowrap cs-dim">' + esc(TYPE_CN[s.type] || s.type) + '</td>' +
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
      ? ('共 ' + n + ' 项。绝招成功率 = 基础值 + 对应根骨 + 绝招等级×2 (最高 95%), 失败会反噬。')
      : '还没有技能 —— 入门得内功与绝招, 其余靠陪读修习。';
  }

  // 性情
  const tp = $('#trait-pill');
  const ti = ch.traits_info || {};
  if (tp) {
    tp.className = 'pill ' + (ti.left_count > 0 && ti.left_points > 0 ? 'warn' : 'mute');
    tp.textContent = (ti.owned || []).length + ' / ' + ti.max_count +
      ' · ' + ti.spent + ' / ' + ti.max_points + ' 分';
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
  $('#pet-chattiness').value = p.chattiness || 3;

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
          chattiness: Number($('#pet-chattiness').value) || 3,
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

/* ───────────────────────── 启动 ───────────────────────── */

(async function init() {
  await loadPresets();
  await loadConfig();
  await loadChar();
})();
