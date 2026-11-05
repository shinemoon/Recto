"""外观 (/appearance) —— 换肤 + 细调 + 自定义 CSS + 自制皮肤。

皮肤是数据 (token), 不是代码。这里做三件事:
  1. 列出所有可用皮肤 (内置 + `data/skins/*.json`), 点一下即时预览
  2. 细调: 字号缩放 / 疏密 / 圆角 / 标题字体 —— 不改皮肤本身
  3. 自制皮肤: 基于任一内置皮肤, 只改几个颜色就存成新皮肤

保存后写 `<data_dir>/appearance.json`; `/assets/skin.css` 立即按新配置生成。
"""
from __future__ import annotations

import json

from .ui import shell, skins

_CSS = """
.skin-grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(215px,1fr))}
/* 注: skin-card 是 <button>, 必须显式覆盖按钮基类 (recto.css §4) 的
   align-items:center / white-space:nowrap —— 否则 column flex 的交叉轴按内容居中,
   空 <i> 宽 0 → 色块塌成一条竖线, 说明文字也不换行。 */
.skin-card{position:relative;border:1px solid var(--rc-line);border-radius:var(--rc-radius-lg);
  background:var(--rc-surface);padding:12px;cursor:pointer;text-align:left;
  display:flex;flex-direction:column;align-items:stretch;justify-content:flex-start;
  white-space:normal;gap:8px;font:inherit;color:inherit;min-height:0;
  transition:border-color .12s ease,box-shadow .12s ease,transform .06s ease}
.skin-card:hover{border-color:var(--rc-line-2);background:var(--rc-surface)}
.skin-card[aria-pressed="true"]{border-color:var(--rc-accent);
  box-shadow:0 0 0 1px var(--rc-accent) inset}
.skin-card .sw{display:flex;width:100%;height:34px;border-radius:calc(var(--rc-radius)*.7);
  overflow:hidden;border:1px solid var(--rc-line)}
.skin-card .sw i{flex:1 1 0;min-width:0;display:block;height:100%}
.skin-card .nm{font-family:var(--rc-heading);font-weight:650;font-size:var(--rc-fs);
  display:flex;align-items:center;gap:8px}
.skin-card .ds{font-size:var(--rc-fs-xs);color:var(--rc-mute);line-height:1.5}
.skin-card .own{position:absolute;top:8px;right:8px}
.tune{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(230px,1fr))}
.tune label{display:flex;flex-direction:column;gap:6px;font-size:var(--rc-fs-xs);
  letter-spacing:.06em;text-transform:uppercase;color:var(--rc-mute)}
.tune label b{font-family:var(--rc-mono);color:var(--rc-fg);font-size:var(--rc-fs-xs);
  text-transform:none;letter-spacing:0}
.preview{border:1px solid var(--rc-line);border-radius:var(--rc-radius-lg);
  background:var(--rc-bg);padding:16px;margin-top:4px}
.preview .row{margin-bottom:8px}
"""

_JS = r"""
(function(){
  var data = JSON.parse(document.getElementById('ap-data').textContent);
  var root = document.documentElement;
  var conf = Object.assign({}, data.conf);
  var dirty = false;

  function cssVar(name, val){ root.style.setProperty(name, val); }
  function applyLocal(){
    if (conf.font_scale) {
      var base = 15.5 * Number(conf.font_scale || 1);
      cssVar('--rc-fs', base.toFixed(2) + 'px');
      cssVar('--rc-fs-sm', (13 * Number(conf.font_scale)).toFixed(2) + 'px');
      cssVar('--rc-fs-xs', (11.5 * Number(conf.font_scale)).toFixed(2) + 'px');
    }
    var ln = root.style.getPropertyValue('--rc-line-note');
    void ln;
    dirty = true;
    document.getElementById('ap-save').classList.add('primary');
  }
  function label(id, txt){ var e = document.getElementById(id); if (e) e.textContent = txt; }

  document.querySelectorAll('[data-skin]').forEach(function(btn){
    btn.addEventListener('click', function(){
      var id = btn.dataset.skin;
      var skin = (data.skins || {})[id];
      if (!skin) return;
      conf.skin = id;
      document.querySelectorAll('[data-skin]').forEach(function(b){
        b.setAttribute('aria-pressed', b.dataset.skin === id ? 'true' : 'false');
      });
      // 本地即时预览: 直接套这个皮肤的 token, 不额外请求
      Object.keys(skin.tokens || {}).forEach(function(k){
        cssVar('--rc-' + k, skin.tokens[k]);
      });
      var extra = document.getElementById('ap-extra');
      if (!extra) {
        extra = document.createElement('style');
        extra.id = 'ap-extra';
        document.head.appendChild(extra);
      }
      extra.textContent = skin.extra_css || '';
      root.style.colorScheme = skin.mode === 'dark' ? 'dark' : 'light';
      applyLocal();
    });
  });

  function bindRange(id, key, fmt){
    var el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('input', function(){
      conf[key] = Number(el.value);
      label(id + '-v', fmt ? fmt(el.value) : el.value);
      applyLocal();
    });
  }
  bindRange('ap-fs', 'font_scale', function(v){ return Number(v).toFixed(2) + '×'; });
  bindRange('ap-rad', 'radius_scale', function(v){ return Number(v).toFixed(2) + '×'; });

  ['ap-density','ap-heading'].forEach(function(id){
    var el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('change', function(){
      conf[id === 'ap-density' ? 'density' : 'heading'] = el.value;
      applyLocal();
    });
  });

  var css = document.getElementById('ap-css');
  if (css) css.addEventListener('input', function(){ conf.custom_css = css.value; applyLocal(); });

  // 自制皮肤: 复制当前皮肤 → 改名 → 只改颜色
  var mk = document.getElementById('ap-mk');
  if (mk) mk.addEventListener('click', function(){
    var name = (document.getElementById('ap-mk-name').value || '').trim().toLowerCase()
      .replace(/[^a-z0-9_-]/g, '-');
    if (!name) { alert('先起个 id (字母/数字/连字符)'); return; }
    var base = document.getElementById('ap-mk-base').value || conf.skin || 'paper';
    var tokens = { accent: document.getElementById('ap-mk-accent').value };
    var bg = document.getElementById('ap-mk-bg').value;
    if (bg) tokens.bg = bg;
    conf.custom_skins = conf.custom_skins || {};
    conf.custom_skins[name] = { label: name, base: base, tokens: tokens };
    conf.skin = name;
    save(true);
  });

  function save(reload){
    var btn = document.getElementById('ap-save');
    if (btn) { btn.disabled = true; btn.textContent = '保存中…'; }
    fetch('/api/appearance', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(conf)
    }).then(function(r){ return r.json(); }).then(function(j){
      if (j.error) { alert('保存失败: ' + j.error); if (btn) { btn.disabled=false; btn.textContent='保存'; } return; }
      if (reload !== false) location.reload();
    }).catch(function(e){
      alert('保存失败: ' + e.message);
      if (btn) { btn.disabled=false; btn.textContent='保存'; }
    });
  }
  var s = document.getElementById('ap-save');
  if (s) s.addEventListener('click', function(){ save(true); });
  var rs = document.getElementById('ap-reset');
  if (rs) rs.addEventListener('click', function(){
    if (!confirm('恢复默认外观? (皮肤回到「纸」, 细调清零, 自定义 CSS 清空; 自制皮肤保留)')) return;
    conf.skin = 'paper'; conf.font_scale = 1; conf.density = 'comfortable';
    conf.radius_scale = 0; conf.heading = 'auto'; conf.custom_css = '';
    save(true);
  });
  document.querySelectorAll('[data-del-skin]').forEach(function(b){
    b.addEventListener('click', function(ev){
      ev.stopPropagation();
      var id = b.dataset.delSkin;
      if (!confirm('删除自制皮肤 ' + id + '?')) return;
      delete (conf.custom_skins || {})[id];
      if (conf.skin === id) conf.skin = 'paper';
      save(true);
    });
  });
})();
"""


def _swatch_html(colors: list[str]) -> str:
    return ('<div class="sw" aria-hidden="true">' +
            "".join(f'<i style="background:{c}"></i>' for c in colors) + "</div>")


def page_html(user: str | None = None) -> str:
    conf = skins.load_conf()
    sid, _tokens, conf = skins.resolve(conf)
    alls = skins.all_skins(conf)
    cards = []
    for key, s in alls.items():
        pressed = "true" if key == sid else "false"
        del_btn = ("" if s["builtin"] else
                   f'<button class="btn ghost small own" data-del-skin="{key}" '
                   f'title="删除这个自制皮肤">删</button>')
        tag = ('<span class="pill mute">内置</span>' if s["builtin"]
               else '<span class="pill">自制</span>')
        cards.append(
            f'<button class="skin-card" data-skin="{key}" aria-pressed="{pressed}">'
            f'{del_btn}{_swatch_html(skins.swatches(key, conf))}'
            f'<div class="nm">{s["label"]} {tag}</div>'
            f'<div class="ds">{s["desc"]}</div></button>')

    body = [
        shell.head(row=True,
                   actions='<button id="ap-save" class="primary">保存</button>'
                           '<button id="ap-reset">恢复默认</button>'),
        '<style>' + _CSS + "</style>",
        '<details class="sec" open><summary>皮肤 <span class="cnt">'
        + str(len(alls)) + ' 套</span></summary><div class="sec-body">'
        '<div class="skin-grid">' + "".join(cards) + "</div>"
        '<div class="meta" style="margin-top:12px">点一下立刻在本地预览; 点「保存」才写盘。'
        "所有分区 (书库 / 同步 / 阅读增强 / 摘录) 共用同一套皮肤 —— "
        "要加一套内置皮肤: 放进 <code>data/skins/&lt;id&gt;.json</code>, "
        "刷新页面即可见。</div>"
        "</div></details>",

        '<details class="sec" open><summary>细调</summary><div class="sec-body">'
        '<div class="tune">'
        f'<label>字号缩放 <b id="ap-fs-v">{conf["font_scale"]:.2f}×</b>'
        f'<input id="ap-fs" type="range" min="0.85" max="1.5" step="0.05" '
        f'value="{conf["font_scale"]}"></label>'
        f'<label>圆角 <b id="ap-rad-v">{conf["radius_scale"]:.2f}×</b>'
        f'<input id="ap-rad" type="range" min="0" max="2" step="0.1" '
        f'value="{conf["radius_scale"]}"></label>'
        '<label>疏密<select id="ap-density">'
        + "".join(f'<option value="{k}"{" selected" if conf["density"] == k else ""}>{v}</option>'
                  for k, v in (("compact", "紧凑"), ("comfortable", "舒适"), ("roomy", "宽松")))
        + "</select></label>"
        '<label>标题字体<select id="ap-heading">'
        + "".join(f'<option value="{k}"{" selected" if conf["heading"] == k else ""}>{v}</option>'
                  for k, v in (("auto", "跟随皮肤"), ("serif", "衬线"), ("sans", "无衬线")))
        + "</select></label></div></div></details>",

        '<details class="sec"><summary>自制皮肤 <span class="cnt">'
        + str(len(conf.get("custom_skins") or {})) + " 套</span></summary>"
        '<div class="sec-body"><div class="tune">'
        '<label>id (字母/数字/-)<input id="ap-mk-name" placeholder="warm-paper"></label>'
        '<label>基于<select id="ap-mk-base">'
        + "".join(f'<option value="{k}">{s["label"]}</option>' for k, s in alls.items())
        + "</select></label>"
        '<label>主色<input id="ap-mk-accent" type="color" value="#9a3b2f"></label>'
        '<label>底色<input id="ap-mk-bg" type="color" value="#f4efe4"></label>'
        '</div><div class="row" style="margin-top:12px">'
        '<button id="ap-mk" class="primary">复制当前皮肤为新的</button></div>'
        '<div class="meta">生成后落在 <code>data/appearance.json</code>; 想手工精修就把它抄到 '
        "<code>data/skins/&lt;id&gt;.json</code> (字段: base / tokens / extra_css), "
        "然后从 appearance.json 里删掉同名条目 —— 文件皮肤优先生效。</div>"
        "</div></details>",

        '<details class="sec"><summary>自定义 CSS</summary><div class="sec-body">'
        f'<textarea id="ap-css" spellcheck="false" style="min-height:150px;width:100%;'
        f'font-family:var(--rc-mono);font-size:var(--rc-fs-xs)" '
        f'placeholder="/* 追加在 skin.css 之后, 想改什么写什么 */\n'
        f'.rc-nav a{{letter-spacing:.02em}}">{_esc(conf.get("custom_css") or "")}</textarea>'
        '<div class="meta" style="margin-top:8px">只挡 <code>&lt;</code> 与 '
        "<code>@import</code>; 其余照原样注入。</div></div></details>",

        '<details class="sec"><summary>预览</summary><div class="sec-body">'
        '<div class="preview">'
        '<div class="rc-kicker">Sample</div>'
        '<div class="headline">排版是界面的一半</div>'
        '<div class="lede">正文用衬线, 控件用无衬线 —— 阅读与操作分开。</div>'
        '<div class="row" style="margin-top:10px"><button class="primary small">主操作</button>'
        '<button class="small">次操作</button>'
        '<span class="pill ok">ok</span><span class="pill warn">warn</span>'
        '<span class="pill err">err</span></div>'
        "<table><thead><tr><th>字段</th><th>值</th></tr></thead><tbody>"
        "<tr><td>皮肤</td><td>" + sid + "</td></tr>"
        "<tr><td>字号</td><td>" + f"{conf['font_scale']:.2f}×" + "</td></tr>"
        "</tbody></table></div></div></details>",
        f'<script type="application/json" id="ap-data">{shell.skin_payload()}</script>',
        "<script>" + _JS + "</script>",
    ]
    return shell.document(title="外观", body="".join(body), active="appearance",
                          user=user)


def _esc(s: str) -> str:
    import html
    return html.escape(s or "", quote=False)


def api_get() -> dict:
    return json.loads(shell.skin_payload())


def api_post(body: dict) -> dict:
    conf = skins.load_conf()
    merged = dict(conf)
    for k in ("skin", "font_scale", "density", "radius_scale", "heading",
              "custom_css", "custom_skins"):
        if k in body:
            merged[k] = body[k]
    out = skins.save_conf(merged)
    return {"ok": True, "conf": {k: out.get(k) for k in
                                 ("skin", "font_scale", "density",
                                  "radius_scale", "heading")}}
