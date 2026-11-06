/* 语料分区前端 —— 平移自上游 app.js, 只改了接口前缀与两处下载跳转。
   重新生成: python3 tools/build_corpus_assets.py
   维护约定: 新增接口一律写 api('/api/xxx'), 由 api() 统一加 /corpus 前缀。 */
'use strict';

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const state = {
  categories: [],
  models: [],
  activeModelId: '',
  epubList: [],
  selected: null,     // {path, name, size}
  bookId: null,
  pollTimer: null,
  books: [],          // 历史书目 (含固定导出路径)
  exportSlug: '',
  exportsDir: '',
  exportsLatest: '',
  latestSlug: '',      // exports/latest.json 当前指向谁
  latestPinned: false, // 是否被显式钉住
  latestStale: false,  // 源产物目录已删, 只剩 exports/ 这份副本
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

// 语料是云阅 Recto 的原生分区: 所有接口都在 /corpus 前缀下
const CORPUS_BASE = window.RC_CORPUS_BASE || '/corpus';

async function api(url, opts) {
  const res = await fetch(CORPUS_BASE + url, Object.assign({
    headers: { 'Content-Type': 'application/json' }
  }, opts || {}));
  if (res.status === 401) {
    // 登录会话过期: 整个页面回登录页 (不在这里抛错让人以为是接口坏了)
    location.href = '/login?next=' + encodeURIComponent(location.pathname);
    throw new Error('未登录');
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

function fmtSize(n) {
  if (!n && n !== 0) return '';
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' K';
  return (n / 1024 / 1024).toFixed(1) + ' M';
}

/* ───────────────────────── LLM 配置 ───────────────────────── */

async function loadPresets() {
  try {
    const r = await api('/api/presets');
    const sel = $('#llm-preset');
    (r.presets || []).forEach(p => {
      const o = document.createElement('option');
      o.value = p.id;
      o.textContent = p.name;
      o.dataset.note = p.note || '';
      sel.appendChild(o);
    });
    sel.onchange = () => {
      const p = (r.presets || []).find(x => x.id === sel.value);
      if (!p) return;
      if (p.base_url) $('#llm-base').value = p.base_url;
      if (p.model) $('#llm-model').value = p.model;
      if (p.provider) $('#llm-provider').value = p.provider;
      $('#llm-tip').textContent = p.note || '';
    };
    const ul = $('#llm-tips');
    ul.innerHTML = (r.tips || []).map(t => '<li>' + t + '</li>').join('');
  } catch (e) { /* ignore */ }
}

async function loadConfig() {
  const cfg = await api('/api/config');
  $('#llm-provider').value = cfg.llm.provider || 'openai';
  $('#llm-base').value = cfg.llm.base_url || '';
  $('#llm-model').value = cfg.llm.model || '';
  $('#llm-key').placeholder = cfg.llm.has_key ? ('已保存: ' + cfg.llm.api_key) : 'sk-...';
  $('#llm-timeout').value = cfg.llm.timeout || 180;
  $('#llm-maxtok').value = cfg.llm.max_tokens || 16384;
  $('#llm-temp').value = cfg.llm.temperature != null ? cfg.llm.temperature : 0.2;
  $('#llm-conc').value = cfg.llm.max_concurrency || 3;
  $('#llm-thinking').value = cfg.llm.thinking === 'on' ? 'on' : 'off';
  // 严格避免推理默认关 (放宽): 关不掉思考的端点允许它带着思考跑完
  $('#llm-strict').checked = cfg.llm.strict_no_reasoning === true;
  $('#chunk-size').value = cfg.parse.max_chars_per_chunk || 6000;
  renderReasoningTip(cfg.llm);
  state.categories = cfg.categories || [];
  state.models = cfg.models || [];
  state.activeModelId = cfg.active_model_id || '';
  renderModels();
  renderCats();
}

function llmPayload() {
  return {
    provider: $('#llm-provider').value,
    base_url: $('#llm-base').value.trim(),
    model: $('#llm-model').value.trim(),
    api_key: $('#llm-key').value.trim(),
    timeout: Number($('#llm-timeout').value) || 180,
    max_tokens: Number($('#llm-maxtok').value) || 8192,
    temperature: Number($('#llm-temp').value) || 0,
    max_concurrency: Number($('#llm-conc').value) || 1,
    thinking: $('#llm-thinking').value || 'off',
    strict_no_reasoning: !!$('#llm-strict').checked,
  };
}

/* 推理模型提示: 抽取不该用推理 -- 名字命中已知家族就直说 */
const REASONING_MARKERS = ['thinking', 'reasoner', 'reasoning', 'qwq',
  'deepseek-r1', 'minimax-m1', 'minimax-m2', 'minimax-m3', 'glm-z1',
  'glm-4.5', 'glm-4.6', 'kimi-k2-thinking', 'magistral'];
const REASONING_PREFIXES = ['o1', 'o3', 'o4', 'gpt-5', 'gpt5'];

function isReasoningModel(name) {
  const m = String(name || '').toLowerCase();
  if (!m) return false;
  if (REASONING_MARKERS.some(k => m.indexOf(k) >= 0)) return true;
  return REASONING_PREFIXES.some(p => m.indexOf(p) === 0);
}

function renderReasoningTip(llm) {
  const el = $('#llm-reasoning-tip');
  if (!el) return;
  const model = (llm && llm.model) || $('#llm-model').value || '';
  if (!isReasoningModel(model)) {
    el.className = 'tip';
    el.textContent = '抽取建议用非推理模型 (如 agnes-2.5-flash): 同样内容快 3-5 倍, token 少一半以上。';
    return;
  }
  const thinking = (llm && llm.thinking) || $('#llm-thinking').value || 'off';
  const strict = (llm && llm.strict_no_reasoning) === true;
  el.className = 'tip warn';
  el.innerHTML = '⚠ <b>' + esc(model) + '</b> 属于推理模型。抽取时它会把 token 烧在思考上, ' +
    '慢而且容易撞 max_tokens 截断。<br>' +
    '→ 已设「思考 = ' + (thinking === 'on' ? '<b>允许</b>' : '<b>关闭</b>') + '」' +
    (thinking === 'on' ? ' (这会明显变慢)' : ' (会尝试发送关闭参数)') + '。' +
    (strict ? '严格模式开启: 一旦响应里出现 thinking 就中止本轮, 不浪费后面的块。'
            : '严格模式关闭 (默认): 端点关不掉思考就允许它带着思考跑完, 慢一些但不会拦死; 想「一发现就停」就勾上「严格避免推理」。');
}

$('#btn-llm-save').onclick = async () => {
  const st = $('#llm-status');
  try {
    const p = llmPayload();
    const body = Object.assign({}, p);
    if (!body.api_key) delete body.api_key;   // 空 = 不改
    await api('/api/config', { method: 'POST', body: JSON.stringify({ llm: body }) });
    setStatus(st, '已保存', 'ok');
    $('#llm-key').value = '';
    await loadConfig();
  } catch (e) { setStatus(st, e.message, 'err'); }
};

/* ─────────────────── 熔断状态 (额度用尽标记) ───────────────────
   额度用尽后本进程会拒掉后续所有请求 (不再打网络). TTL 到期自动放行,
   也可以手动清 —— 充值 / 换 key / 窗口重置后不用重启服务. */

function renderQuota(q) {
  const bar = $('#quota-bar');
  if (!bar) return;
  if (!q || !q.tripped) { bar.classList.add('hidden'); return; }
  bar.classList.remove('hidden');
  $('#quota-text').textContent =
    '熔断中: ' + (q.at || '?') + ' 起 (' + q.age_sec + 's 前, TTL ' + q.ttl_sec + 's) · ' +
    (q.message || '额度用尽').slice(0, 160) +
    ' —— 抽取任务会被直接拒绝; 「测试连通」不受影响';
}

async function loadQuota() {
  try { renderQuota(await api('/api/quota')); } catch (e) { /* 不影响主流程 */ }
}

$('#btn-quota-reset').onclick = async () => {
  const st = $('#llm-status');
  try {
    const r = await api('/api/quota/reset', { method: 'POST' });
    renderQuota(r.quota);
    setStatus(st, '熔断标记已清, 可以重跑', 'ok');
  } catch (e) { setStatus(st, e.message, 'err'); }
};

$('#btn-llm-test').onclick = async () => {
  const st = $('#llm-status');
  setStatus(st, '测试中…');
  try {
    const r = await api('/api/llm/test', { method: 'POST', body: JSON.stringify(llmPayload()) });
    renderQuota(r.quota);
    if (r.ok) setStatus(st, '连通 (' + r.latency_ms + 'ms) ' + (r.sample || '').slice(0, 60), 'ok');
    else setStatus(st, '失败: ' + r.message, 'err');
  } catch (e) { setStatus(st, e.message, 'err'); }
};

$('#btn-llm-toggle').onclick = () => {
  const b = $('#llm-body');
  b.classList.toggle('hidden');
  $('#btn-llm-toggle').textContent = b.classList.contains('hidden') ? '展开' : '收起';
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
        (m.reasoning_model ? ' <span class="badge-warn" title="推理模型: 抽取会慢 + 烧 token">推理</span>' : '') + '</td>' +
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
    const i = Number(t.dataset.i);
    if (t.type === 'radio') return;
    state.models[i][t.dataset.f] = t.value;
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
        state.activeModelId = r.config ? (r.config.active_model_id || '') : m.id;
        await loadConfig();
        setStatus(st, '已使用: ' + m.name, 'ok');
      } catch (err) { setStatus(st, err.message, 'err'); }
    } else if (act === 'test') {
      setStatus(st, '测试中…');
      try {
        const r = await api('/api/models/' + encodeURIComponent(m.id) + '/test', { method: 'POST' });
        renderQuota(r.quota);
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
  // 顶部下拉同步
  const sel = $('#llm-model-sel');
  if (sel) {
    sel.innerHTML = '<option value="">— 手动配置 (不用模型库) —</option>';
    state.models.forEach(m => {
      const o = document.createElement('option');
      o.value = m.id;
      o.textContent = m.name + (m.model ? ' · ' + m.model : '');
      sel.appendChild(o);
    });
    sel.value = state.activeModelId || '';
  }
}

$('#btn-model-add').onclick = () => {
  state.models.push({
    id: '', name: '新模型', provider: 'openai',
    base_url: '', model: '', api_key: '', note: '', has_key: false,
  });
  renderModels();
};

// 从当前 llm 段导入一条 (含 key, 不用手抄)
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
    const payload = state.models.map(m => {
      const e = Object.assign({}, m);
      if (m._from_current) {
        e.from_current = true;
        e.api_key = '';
      } else if (!e.api_key) {
        e.api_key = (m.has_key ? '***' : '');
      } else if (e.api_key.indexOf('*') >= 0) {
        // 脱敏串 = 服务端给的展示值, 不是真 key.
        // 回传 '***' 让服务端沿用旧值, 别把脱敏串存成真 key.
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
    setStatus(st, '已保存 ' + state.models.length + ' 个模型', 'ok');
  } catch (e) { setStatus(st, e.message, 'err'); }
};

$('#btn-models-toggle').onclick = () => {
  const b = $('#models-body');
  b.classList.toggle('hidden');
  $('#btn-models-toggle').textContent = b.classList.contains('hidden') ? '展开' : '收起';
};

// 顶部「模型库」下拉 → 直接 apply
$('#llm-model-sel').onchange = async (e) => {
  const mid = e.target.value;
  const st = $('#llm-status');
  if (!mid) { state.activeModelId = ''; return; }
  try {
    const r = await api('/api/models/apply', { method: 'POST', body: JSON.stringify({ id: mid }) });
    state.activeModelId = (r.config && r.config.active_model_id) || mid;
    await loadConfig();
    setStatus(st, '已使用: ' + mid, 'ok');
  } catch (err) { setStatus(st, err.message, 'err'); }
};

/* ───────────────────────── 分类 ───────────────────────── */

function renderCats() {
  const tb = $('#cat-rows');
  tb.innerHTML = '';
  state.categories.forEach((c, i) => {
    const tr = document.createElement('tr');
    tr.innerHTML =
      '<td><input type="checkbox" data-i="' + i + '" data-f="enabled"' + (c.enabled ? ' checked' : '') + '></td>' +
      '<td><input type="text" data-i="' + i + '" data-f="id" value="' + esc(c.id) + '"></td>' +
      '<td><input type="text" data-i="' + i + '" data-f="name" value="' + esc(c.name) + '"></td>' +
      '<td><input type="text" data-i="' + i + '" data-f="hint" value="' + esc(c.hint || '') + '"></td>';
    tb.appendChild(tr);
  });
  tb.oninput = (e) => {
    const t = e.target;
    if (!t.dataset || t.dataset.i === undefined) return;
    const i = Number(t.dataset.i);
    state.categories[i][t.dataset.f] = t.type === 'checkbox' ? t.checked : t.value;
  };
  tb.onchange = (e) => {
    const t = e.target;
    if (t.type === 'checkbox' && t.dataset.f === 'enabled') {
      state.categories[Number(t.dataset.i)].enabled = t.checked;
    }
  };
}

$('#btn-cat-save').onclick = async () => {
  const st = $('#cat-status');
  try {
    await api('/api/config', { method: 'POST', body: JSON.stringify({ categories: state.categories }) });
    setStatus(st, '已保存 ' + state.categories.filter(c => c.enabled).length + ' 类', 'ok');
  } catch (e) { setStatus(st, e.message, 'err'); }
};

$('#btn-cat-add').onclick = () => {
  state.categories.push({ id: 'custom', name: '自定义', hint: '', enabled: true });
  renderCats();
};

/* ───────────────────────── epub 列表 ───────────────────────── */

async function loadRoots() {
  try {
    const r = await api('/api/epubs/roots');
    $('#roots-input').value = (r.roots || []).join('\n');
  } catch (e) { /* ignore */ }
}

async function loadEpubs() {
  const st = $('#epub-status');
  const list = $('#epub-list');
  list.innerHTML = '<div class="empty">扫描中…</div>';
  try {
    const r = await api('/api/epubs');
    state.epubList = r.items || [];
    renderEpubs();
    setStatus(st, state.epubList.length + ' 个 epub', 'ok');
  } catch (e) {
    list.innerHTML = '<div class="empty">扫描失败: ' + esc(e.message) + '</div>';
    setStatus(st, e.message, 'err');
  }
}

function renderEpubs() {
  const q = ($('#epub-filter').value || '').toLowerCase();
  const list = $('#epub-list');
  const items = state.epubList.filter(e => !q || e.name.toLowerCase().includes(q));
  if (!items.length) {
    list.innerHTML = '<div class="empty">没匹配到 epub</div>';
    return;
  }
  list.innerHTML = '';
  items.forEach(e => {
    const d = document.createElement('div');
    d.className = 'epub-row' + (state.selected && state.selected.path === e.path ? ' sel' : '');
    d.innerHTML = '<span class="nm">' + esc(e.name) + '</span>' +
      '<span class="sz">' + fmtSize(e.size) + '</span>';
    d.title = e.path;
    d.onclick = () => {
      state.selected = e;
      state.bookId = null;
      renderEpubs();
      $('#run-target').className = 'run-target';
      $('#run-target').innerHTML = '<b>' + esc(e.name) + '</b><br><span class="status">' + esc(e.path) + '</span>';
      $('#btn-analyze').disabled = true;
    };
    list.appendChild(d);
  });
}

$('#btn-epub-refresh').onclick = loadEpubs;
$('#epub-filter').oninput = renderEpubs;

$('#btn-roots-toggle').onclick = () => {
  const p = $('#roots-panel');
  p.classList.toggle('hidden');
  if (!p.classList.contains('hidden')) loadRoots();
};

$('#btn-roots-save').onclick = async () => {
  const st = $('#roots-status');
  const roots = $('#roots-input').value.split(/[\n|]+/).map(s => s.trim()).filter(Boolean);
  try {
    await api('/api/epubs/roots', { method: 'POST', body: JSON.stringify({ roots: roots }) });
    setStatus(st, '已保存', 'ok');
    loadEpubs();
  } catch (e) { setStatus(st, e.message, 'err'); }
};

$('#btn-roots-reset').onclick = async () => {
  try {
    await api('/api/epubs/roots', { method: 'POST', body: JSON.stringify({ roots: [] }) });
    await loadRoots();
    loadEpubs();
  } catch (e) { /* ignore */ }
};

/* ───────────────────────── 解析 / 抽取 ───────────────────────── */

$('#btn-parse').onclick = async () => {
  const st = $('#epub-status');
  if (!state.selected) { setStatus(st, '先选一本 epub', 'err'); return; }
  setStatus(st, '解析中…');
  try {
    const r = await api('/api/parse', { method: 'POST', body: JSON.stringify({ path: state.selected.path }) });
    state.bookId = r.book_id;
    const m = r.meta || {};
    $('#run-target').innerHTML =
      '<b>' + esc(m.title || state.selected.name) + '</b>' +
      (m.author ? ' · ' + esc(m.author) : '') + '<br>' +
      '<span class="status">' + (m.chapters || []).length + ' 章 · ' +
      (m.total_chars || 0).toLocaleString() + ' 字符</span>';
    $('#btn-analyze').disabled = false;
    setStatus(st, '解析完成', 'ok');
  } catch (e) { setStatus(st, e.message, 'err'); }
};

async function startAnalyze(fresh, force) {
  const st = $('#epub-status');
  if (!state.bookId) { setStatus(st, '先解析', 'err'); return; }
  $('#btn-analyze').disabled = true;
  $('#btn-resume').disabled = true;
  $('#btn-cancel').disabled = false;
  $('#progress-wrap').classList.remove('hidden');
  $('#analyze-warn').classList.add('hidden');
  setStatus(st, fresh ? '抽取中 (从零)…' : '抽取中 (自动续跑)…');
  try {
    await api('/api/config', {
      method: 'POST',
      body: JSON.stringify({ parse: { max_chars_per_chunk: Number($('#chunk-size').value) || 8000 } })
    });
    const r = await api('/api/analyze', {
      method: 'POST',
      body: JSON.stringify({
        book_id: state.bookId,
        target_language: $('#target-lang').value || 'Chinese',
        fresh: !!fresh,
        force: !!force,
      })
    });
    if (r.warnings && r.warnings.length) {
      const w = $('#analyze-warn');
      w.innerHTML = r.warnings.map(x => '<div>⚠ ' + esc(x) + '</div>').join('');
      w.classList.remove('hidden');
    }
    const total = r.chunks || 1;
    const done = r.resumed || 0;
    $('#bar-fill').style.width = Math.round(done * 100 / total) + '%';
    $('#progress-text').textContent = done + ' / ' + total + ' 块' +
      (done ? ' (续跑 ' + done + ', 待跑 ' + (r.pending || (total - done)) + ')' : '');
    pollTask();
  } catch (e) {
    if (e.status === 409 && e.data && e.data.can_force && !force) {
      const sec = Number(e.data.stalled_sec || 0);
      const why = sec >= 60
        ? '已经 ' + Math.floor(sec / 60) + ' 分钟没有任何进展 (请求可能挂住了)'
        : '这本书已经在抽取中';
      if (confirm(why + '。\n\n强制重跑? 会放弃卡住的那次调用, 已完成的块仍然保留。')) {
        return startAnalyze(fresh, true);
      }
    }
    setStatus(st, e.message, 'err');
    $('#btn-analyze').disabled = false;
    $('#btn-cancel').disabled = true;
  }
}

$('#btn-analyze').onclick = () => startAnalyze(false);
$('#btn-resume').onclick = () => startAnalyze(false);

$('#btn-clear-partial').onclick = async () => {
  if (!state.bookId) return;
  if (!confirm('丢弃断点? 下次抽取会从第一块重新跑 (已完成的块会重新花钱)。')) return;
  try {
    await api('/api/analyze/clear', { method: 'POST', body: JSON.stringify({ book_id: state.bookId }) });
    setStatus($('#epub-status'), '断点已丢弃', 'ok');
    refreshResumeUi();
  } catch (e) { setStatus($('#epub-status'), e.message, 'err'); }
};

/* 断点/续跑按钮的显隐: 读 /api/task, 有断点就亮「继续」 */
async function refreshResumeUi() {
  const tip = $('#resume-tip');
  if (!state.bookId) return;
  try {
    const t = await api('/api/task/' + state.bookId);
    const status = (t.status || '').toLowerCase();
    const saved = (t.partial && t.partial.saved) || t.saved_blocks || 0;
    const busy = status === 'running' || status === 'analyzing';
    // done 的产物不该出现「继续」(partial 可能残留上一轮, 但任务已经完结)
    const show = saved > 0 && !busy && status !== 'done';
    $('#btn-resume').classList.toggle('hidden', !show);
    $('#btn-clear-partial').classList.toggle('hidden', !show);
    $('#btn-resume').textContent = '继续 (已完成 ' + saved + '/' + (t.total || t.chunks || '?') + ')';
    if (show) {
      tip.className = 'tip warn';
      tip.textContent = '断点: 已完成 ' + saved + ' 块, 再点抽取只会补剩下的 (上次停在: ' +
        (t.last_error || t.status || '未知') + ')';
    } else if (saved > 0 && status !== 'done') {
      tip.className = 'tip';
      tip.textContent = '断点: 已完成 ' + saved + ' 块';
    } else {
      tip.className = 'tip';
      tip.textContent = '';
    }
  } catch (e) { /* 没断点或没选书 */ }
}

/* 清掉上一本书残留的进度/断点提示 (切书、刷新后先归零) */
function resetProgressUi() {
  if (state.pollTimer) { clearTimeout(state.pollTimer); state.pollTimer = null; }
  $('#progress-wrap').classList.add('hidden');
  $('#bar-fill').style.width = '0%';
  $('#progress-text').textContent = '';
  $('#analyze-warn').classList.add('hidden');
  $('#btn-cancel').disabled = true;
  $('#btn-resume').classList.add('hidden');
  $('#btn-clear-partial').classList.add('hidden');
  const tip = $('#resume-tip');
  tip.className = 'tip';
  tip.textContent = '';
}

/* 刷新页面 / 切换书目后, 把"进行中(或停在中途)的任务"重新画到界面上.
   之前进度条只活在点击「抽取」那一刻的闭包里, 一刷新就全丢 —— 后台还在跑,
   页面却什么也不显示, 只能靠用户重新点一次抽取 (还会撞 409). */
async function restoreTaskUi() {
  if (!state.bookId) return;
  let t;
  try { t = await api('/api/task/' + state.bookId); } catch (e) { return; }
  const status = (t.status || '').toLowerCase();
  const done = t.done || 0;
  const total = t.total || t.chunks || 0;
  const saved = (t.partial && t.partial.saved) || t.saved_blocks || 0;
  const wrap = $('#progress-wrap');

  if (status === 'running' || status === 'analyzing') {
    wrap.classList.remove('hidden');
    $('#bar-fill').style.width = total ? Math.min(100, Math.round(done * 100 / total)) + '%' : '0%';
    const bits = [done + ' / ' + total + ' 块'];
    if (t.resumed) bits.push('续跑 ' + t.resumed + ' 块');
    if (t.pending) bits.push('待跑 ' + t.pending + ' 块');
    if (t.stalled) bits.push('已 ' + Math.floor((t.stalled_sec || 0) / 60) + ' 分钟无进展');
    if (t.external) bits.push('由命令行进程执行');
    $('#progress-text').textContent = bits.join(' · ') + ' · 进行中';
    // 任务在跑: 不许再点抽取, 但可以随时停止"本页面轮询" (后台不动)
    $('#btn-analyze').disabled = !t.stalled;
    $('#btn-resume').classList.add('hidden');
    $('#btn-clear-partial').classList.add('hidden');
    $('#btn-cancel').disabled = false;
    setStatus($('#epub-status'), '抽取中… (已恢复进度显示)', '');
    pollTask();
    return;
  }

  // 停在中途: 有断点就给进度 + 「继续」按钮 (refreshResumeUi 负责按钮)
  if (saved > 0) {
    wrap.classList.remove('hidden');
    $('#bar-fill').style.width = total ? Math.min(100, Math.round(saved * 100 / total)) + '%' : '0%';
    const label = status === 'paused' ? '已暂停'
      : status === 'interrupted' ? '已中断'
      : status === 'failed' ? '失败 (可续跑)' : '未完成';
    $('#progress-text').textContent = label + ' · 断点 ' + saved + '/' + total + ' 块' +
      (t.last_error || t.error ? ' · ' + (t.last_error || t.error) : '');
  }
  await refreshResumeUi();
}

function pollTask() {
  if (state.pollTimer) clearTimeout(state.pollTimer);
  state.pollTimer = setTimeout(async () => {
    try {
      const t = await api('/api/task/' + state.bookId);
      const done = t.done || 0, total = t.total || t.chunks || 1;
      $('#bar-fill').style.width = Math.min(100, Math.round(done * 100 / total)) + '%';
      $('#progress-text').textContent = done + ' / ' + total + ' 块';
      if (t.status === 'running') {
        if (t.stalled) {
          // 线程还在但长时间没进展: 允许用户放弃这次挂死的调用
          $('#progress-text').textContent = done + ' / ' + total + ' 块 · 已 ' +
            Math.floor((t.stalled_sec || 0) / 60) + ' 分钟无进展 (点「抽取」可强制重跑)';
          $('#btn-analyze').disabled = false;
        }
        pollTask();
        return;
      }
      if (t.status === 'done') {
        state.bookHasKeywords = true;
        $('#progress-text').textContent = '完成 · ' + (t.terms || 0) + ' 个词条' +
          (t.resumed ? ' (续跑 ' + t.resumed + ' 块)' : '');
        setStatus($('#epub-status'), '抽取完成', 'ok');
        await loadKeywords();
        await loadHistory(false);
      } else if (t.status === 'paused' || t.status === 'interrupted') {
        const saved = (t.partial && t.partial.saved) || 0;
        $('#progress-text').textContent =
          (t.status === 'interrupted' ? '已中断' : '已暂停') +
          ' · 断点 ' + saved + '/' + total + ' 块 · ' + (t.error || t.last_error || '');
        setStatus($('#epub-status'),
          t.status === 'interrupted' ? '中断 (可继续)' : '暂停 (可继续)', 'warn');
      } else {
        const saved = (t.partial && t.partial.saved) || 0;
        $('#progress-text').textContent = '失败: ' + (t.error || '未知错误') +
          (saved ? ' · 断点 ' + saved + '/' + total + ' 块' : '');
        setStatus($('#epub-status'), '失败', 'err');
      }
      $('#btn-analyze').disabled = false;
      $('#btn-cancel').disabled = true;
      refreshResumeUi();
    } catch (e) {
      setStatus($('#epub-status'), e.message, 'err');
      $('#btn-analyze').disabled = false;
      $('#btn-cancel').disabled = true;
    }
  }, 1200);
}

$('#btn-cancel').onclick = () => {
  if (state.pollTimer) clearTimeout(state.pollTimer);
  state.pollTimer = null;
  $('#btn-analyze').disabled = false;
  $('#btn-cancel').disabled = true;
  setStatus($('#epub-status'), '已停止轮询 (后台任务仍在跑)', 'warn');
};

/* ───────────────────────── 产物 ───────────────────────── */

async function loadKeywords() {
  if (!state.bookId) return;
  const sum = $('#kw-summary');
  if (state.bookHasKeywords === false) {
    sum.className = 'empty';
    sum.textContent = '还没有产物';
    $('#kw-cats').innerHTML = '';
    return;
  }
  try {
    const kw = await api('/api/keywords/' + state.bookId);
    const meta = kw.meta || {};
    sum.className = '';
    sum.innerHTML = '<b>' + esc(meta.title || '') + '</b>' +
      (meta.author ? ' · ' + esc(meta.author) : '') +
      ' <span class="status">· ' + esc(meta.model || '') + ' · ' +
      (meta.chunks || 0) + ' 块 · ' + new Date(meta.generated_at).toLocaleString() + '</span>';
    // 固定导出路径 (下游预渲染器直接引用这个)
    const fp = $('#kw-fixed-path');
    if (fp) {
      const slug = state.exportSlug || '';
      fp.innerHTML = slug
        ? '固定路径: <code>' + esc(state.exportsDir + '/' + slug + '.json') + '</code><br>' +
          '下游只读这个: <code>' + esc(state.exportsLatest) + '</code>'
        : '尚未导出到固定路径 (点「历史产物 → 刷新」或重新抽取一次)';
    }
    const box = $('#kw-cats');
    box.innerHTML = '';
    Object.keys(kw.categories || {}).forEach((cid) => {
      const c = kw.categories[cid];
      const items = (c.items || []).slice(0, 60);
      const d = document.createElement('div');
      d.className = 'kw-cat';
      d.innerHTML = '<h3>' + esc(c.name || cid) + '<span class="ct">' + (c.items || []).length + ' 项</span></h3>' +
        '<ul>' + items.map(it =>
          '<li><span class="t">' + esc(it.term) + '</span>' +
          '<span class="c">×' + (it.count || 0) + '</span>' +
          (it.aliases && it.aliases.length ? '<span class="al">' + esc(it.aliases.slice(0, 3).join(', ')) + '</span>' : '') +
          '</li>').join('') +
        ((c.items || []).length > 60 ? '<li class="al">…还有 ' + ((c.items.length) - 60) + ' 项</li>' : '') +
        '</ul>';
      box.appendChild(d);
    });
  } catch (e) {
    sum.className = 'empty';
    sum.textContent = '还没有产物';
  }
}

$('#btn-download').onclick = () => {
  if (!state.bookId) return;
  window.location.href = CORPUS_BASE + '/api/keywords/' + state.bookId;
};

$('#btn-copy-path').onclick = async () => {
  if (!state.bookId) return;
  const p = state.exportSlug
    ? state.exportsDir + '/' + state.exportSlug + '.json'
    : '/vol2/1001/epub-refiner/outputs/' + state.bookId + '/keywords.json';
  try {
    await navigator.clipboard.writeText(p);
    setStatus($('#out-status'), '已复制: ' + p, 'ok');
  } catch (e) {
    setStatus($('#out-status'), p, 'warn');
  }
};

/* ───────────────────────── 历史产物 ───────────────────────── */

function fmtTime(s) {
  if (!s) return '';
  const d = new Date(s);
  if (isNaN(d.getTime())) return s;
  return d.toLocaleString();
}

async function loadHistory(restoreLast) {
  const st = $('#history-status');
  try {
    const r = await api('/api/books');
    state.books = r.items || [];
    state.exportsDir = r.exports || '';
    state.exportsLatest = r.latest_path || '';
    $('#exports-dir').textContent = state.exportsDir || 'exports/';
    try {
      const ex = await api('/api/exports');
      state.latestSlug = ex.latest_slug || '';
      state.latestPinned = !!ex.latest_pinned;
      const ls = ex.latest_state || {};
      state.latestStale = !!ls.stale;
      state.latestTerms = ls.terms || 0;
      renderLatestNote();
    } catch (e) { /* latest 信息拿不到不影响主流程 */ }
    renderHistory();
    setStatus(st, state.books.length + ' 本', 'ok');
    if (restoreLast) {
      // 优先落回"正在跑"的那本书, 其次上次操作的书
      const running = (r.active && r.active[0]) || '';
      const target = running || r.last || (r.unfinished && r.unfinished[0]) || (state.books[0] && state.books[0].id);
      const b = state.books.find(x => x.id === target) || state.books[0];
      if (b) await selectBook(b);   // show 默认 → 同时载入产物
      if (running) setStatus($('#history-status'), '有 1 个任务在后台执行中, 已恢复进度显示', 'warn');
    }
  } catch (e) {
    setStatus(st, e.message, 'err');
  }
}

function renderHistory() {
  const tbody = $('#hist-rows');
  const empty = $('#hist-empty');
  tbody.innerHTML = '';
  if (!state.books.length) {
    empty.classList.remove('hidden');
    empty.textContent = '还没有产物 — 选一本 epub, 解析 + 抽取后就会出现在这里';
    return;
  }
  empty.classList.add('hidden');
  state.books.forEach(b => {
    const tr = document.createElement('tr');
    if (b.id === state.bookId) tr.className = 'sel';
    const st = b.status || '';
    const kind = st === 'done' ? 'ok' : (st === 'failed' ? 'err' : '');
    const running = st === 'running' || st === 'analyzing';
    const prog = (running || st === 'paused' || st === 'interrupted') && b.total
      ? ' ' + (b.done || 0) + '/' + b.total
      : '';
    tr.innerHTML =
      '<td><b>' + esc(b.title || b.name || b.id) + '</b>' +
        '<div class="sub">' + esc(b.name || '') + '</div></td>' +
      '<td>' + esc(b.author || '') + '</td>' +
      '<td>' + (b.terms || 0) + '</td>' +
      '<td><span class="status ' + kind + (running ? ' warn' : '') + '">' +
        esc((st || '—') + prog) + '</span></td>' +
      '<td>' + esc(fmtTime(b.export_generated_at || b.finished_at || b.updated_at)) + '</td>' +
      '<td class="pathcell">' + (b.export_path
        ? '<code>' + esc(b.export_path.split('/').pop()) + '</code>'
        : '<span class="warn">未导出</span>') +
        (b.export_slug && b.export_slug === state.latestSlug
          ? ' <span class="tag-latest' + (state.latestStale ? ' stale' : '') + '" title="' +
            (state.latestStale ? 'exports/latest.json 指向这份, 但产物目录已删' : 'exports/latest.json 指向这份') +
            '">latest</span>'
          : '') + '</td>' +
      '<td class="acts"></td>';
    const acts = tr.querySelector('.acts');
    const mk = (label, fn, cls) => {
      const btn = document.createElement('button');
      btn.className = 'mini' + (cls ? ' ' + cls : '');
      btn.textContent = label;
      btn.onclick = fn;
      acts.appendChild(btn);
    };
    if (b.keywords_exists) mk('载入', () => selectBook(b, true), 'act-load');
    if (!b.export_path && b.keywords_exists) mk('导出', () => exportBook(b), 'act-export');
    if (b.export_path) mk('下载', () => { window.location.href = CORPUS_BASE + '/api/keywords/' + b.id; }, 'act-export');
    if (b.export_path && b.export_slug && b.export_slug !== state.latestSlug) {
      mk('设为最新', () => pinLatest(b), 'act-pin');
    }
    mk('删除', () => deleteBook(b), 'danger act-del');
    tbody.appendChild(tr);
  });
}

async function selectBook(b, show) {
  state.bookId = b.id;
  state.exportSlug = b.export_slug || '';
  // 没有产物就别去请求 keywords (每次开页面都吃一个 404, 控制台一片红)
  state.bookHasKeywords = b.keywords_exists !== false;
  $('#run-target').className = 'run-target';
  $('#run-target').innerHTML = '<b>' + esc(b.title || b.name || b.id) + '</b>' +
    (b.author ? ' · ' + esc(b.author) : '') +
    '<br><span class="status">' + esc(b.path || '') + '</span>';
  $('#btn-analyze').disabled = false;
  resetProgressUi();
  renderHistory();
  if (show !== false) await loadKeywords();
  // 刷新页面 / 切书目后, 若这本书有在跑或停在中途的任务, 把进度重新画出来
  await restoreTaskUi();
}

async function exportBook(b) {
  const st = $('#history-status');
  try {
    const r = await api('/api/export/' + b.id, { method: 'POST' });
    setStatus(st, '已导出 → ' + r.path, 'ok');
    await loadHistory(false);
  } catch (e) {
    setStatus(st, e.message, 'err');
  }
}

function renderLatestNote() {
  const el = $('#latest-note');
  if (!el) return;
  if (!state.latestSlug) {
    el.className = 'status warn';
    el.textContent = 'latest 未指向任何产物 (还没有成功导出过)';
    return;
  }
  const bits = [];
  bits.push('latest → ' + state.latestSlug);
  if (state.latestTerms) bits.push(state.latestTerms + ' 词条');
  if (state.latestPinned) bits.push('已钉住');
  if (state.latestStale) bits.push('⚠ 源产物已删, 只余导出副本');
  el.className = 'status' + (state.latestStale ? ' warn' : ' ok');
  el.textContent = bits.join(' · ');
}

async function pinLatest(b) {
  const st = $('#history-status');
  const slug = b.export_slug || state.exportSlug;
  if (!slug) { setStatus(st, '这本书还没导出', 'warn'); return; }
  try {
    const r = await api('/api/export/latest', {
      method: 'POST',
      body: JSON.stringify({ slug, pin: true }),
    });
    setStatus(st, 'latest → ' + r.slug + (r.pinned ? ' (已钉住)' : ''), 'ok');
    await loadHistory(false);
  } catch (e) {
    setStatus(st, e.message, 'err');
  }
}

async function deleteBook(b) {
  if (!confirm('删除《' + (b.title || b.name || b.id) + '》的产物?\n(fixed 导出文件不会被删)')) return;
  try {
    await api('/api/book/' + b.id, { method: 'DELETE' });
    if (state.bookId === b.id) {
      state.bookId = null;
      state.exportSlug = '';
      $('#kw-summary').className = 'empty';
      $('#kw-summary').textContent = '还没有产物';
      $('#kw-cats').innerHTML = '';
      $('#kw-fixed-path').textContent = '';
    }
    await loadHistory(false);
  } catch (e) {
    setStatus($('#history-status'), e.message, 'err');
  }
}

$('#btn-history-refresh').onclick = async () => {
  const st = $('#history-status');
  try {
    const r = await api('/api/export/sync', { method: 'POST' });
    if (r.count) setStatus(st, '补齐 ' + r.count + ' 份固定路径', 'ok');
    await loadHistory(false);
  } catch (e) {
    setStatus(st, e.message, 'err');
    await loadHistory(false);
  }
};

$('#btn-copy-latest').onclick = async () => {
  const p = state.exportsLatest || '';
  if (!p) return;
  try {
    await navigator.clipboard.writeText(p);
    setStatus($('#history-status'), '已复制: ' + p, 'ok');
  } catch (e) {
    setStatus($('#history-status'), p, 'warn');
  }
};

/* ───────────────────────── 启动 ───────────────────────── */

(async function init() {
  await loadPresets();
  await loadConfig();
  await loadQuota();
  await loadEpubs();
  await loadHistory(true);   // 刷新页面后也回到上一次的产物
})();
