/* 书宠 —— 页面与档案页**共用**的渲染零件。
 *
 * 为什么单独一份: 「故事档案」是另一个页面 (/pet/story/), 要显示同一套
 * 交手战报 / 判定 / 来处 / 类型徽标。复制一份 JS 就会各自漂移 ——
 * 所以零件放这里, 两个页面都引。
 *
 * 加载顺序: 必须**先**于 pet.js / story.js (它们在 init 里就调这些函数)。
 */
'use strict';

window.RCPetUI = (function () {

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
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
    const hd = rounds.slice(0, 2);
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

  const KIND_CN = {
    fight: '动手', talk: '会人', observe: '观事',
    learn: '学艺', rescue: '救人', travel: '行路'
  };

  /** 这一趟是什么事 (斗 / 会 / 观 / 学 / 救 / 行) */
  function kindBadge(g) {
    if (!g || !g.kind) return '';
    return '<span class="adv-kind" title="' + esc(g.kind_why || '') + '">' +
      esc(g.kind_cn || '') + ' · ' + esc(KIND_CN[g.kind] || '') + '</span>';
  }

  /** 这一趟的来处: 读的是哪一本、哪一章、撞见哪些语料。全部来自账本。 */
  const CAT_CN = { person: '人', place: '地', object: '物', org: '势力',
                  event: '事', work: '书文', term: '名目', time: '时节' };

  function echoBlock(e) {
    if (!e || (!(e.books || []).length && !(e.terms || []).length
      && !(e.spark || []).length)) return '';
    const books = (e.books || []).map(function (b) {
      const bits = [];
      if (b.chapter_label) bits.push('读到 ' + b.chapter_label);
      if (b.chapters > 0) bits.push('推了 ' + b.chapters + ' 章');
      if (b.percent_to != null) {
        bits.push((b.percent_from != null && b.percent_from !== b.percent_to
          ? b.percent_from + '% → ' : '') + b.percent_to + '%');
      }
      if (b.minutes) bits.push(b.minutes + ' 分钟');
      if (b.notes) bits.push('划线 ' + b.notes);
      if (b.bookmarks) bits.push('书签 ' + b.bookmarks);
      return '<li><b>' + esc(b.title || '') + '</b>' +
        (b.author ? '<span class="echo-au">' + esc(b.author) + '</span>' : '') +
        (bits.length ? '<span class="echo-meta">' + esc(bits.join(' · ')) + '</span>' : '') +
        ((b.motif || []).length
          ? '<span class="echo-motif">' + b.motif.map(function (m) {
              return '<span class="adv-chip">' + esc(m) + '</span>';
            }).join('') + '</span>'
          : '') + '</li>';
    }).join('');
    const terms = (e.terms || []).slice(0, 10).map(function (t) {
      return '<span class="adv-chip echo-term">' + esc(t.term) +
        (t.count ? ' <b>' + t.count + '</b>' : '') + '</span>';
    }).join('');
    const spark = (e.spark || []).map(function (x) {
      return '<li><span class="echo-cat">' + esc(CAT_CN[x.cat] || x.cat || '') + '</span>' +
        '<span class="echo-trace">' + esc(x.trace || '') + '</span>' +
        (x.image ? '<span class="echo-img">' + esc(x.image) + '</span>' : '') + '</li>';
    }).join('');
    return '<div class="adv-echo"><div class="adv-echo-h">这一趟的来处' +
      (e.minutes ? '<span class="adv-echo-m">这段时间读了 ' + e.minutes + ' 分钟</span>' : '') +
      '</div>' + (books ? '<ul class="adv-echo-books">' + books + '</ul>' : '') +
      (terms ? '<div class="adv-echo-terms"><span class="adv-echo-l">' +
        (e.terms_days ? '近 ' + e.terms_days + ' 天撞见' : '撞见') + '</span>' +
        terms + '</div>' : '') +
      (spark ? '<div class="adv-echo-spark"><span class="adv-echo-l">化成了</span>' +
        '<ul class="echo-spark-list">' + spark + '</ul></div>' : '') +
        '</div>';
  }

  /** 「本回所显」—— 这一趟长出来的东西 (修为 / 技能 / 属性 / 经脉 / 禀赋) */
  function growthChips(g) {
    const show = [];
    if (!g) return show;
    if (g.days) show.push('故事推进 ' + g.days + ' 日');
    if (g.exp) show.push('修为 +' + g.exp);
    (g.skills || []).forEach(function (k) {
      show.push((k.name || k.id) + ' ' + k.from + '→' + k.to + ' 级');
    });
    (g.practice || []).forEach(function (k) {
      if (k.leveled) return;
      if (g.skills && g.skills.some(function (x) { return x.id === k.id; })) return;
      show.push((k.name || k.id) + ' 熟练 +' + k.gain);
    });
    (g.attrs || []).forEach(function (a) {
      show.push((a.name || a.key) + ' ' + a.from + '→' + a.to);
    });
    if (g.vein) show.push('通经 ' + g.vein + ' 层');
    if (g.trait) show.push('觉醒「' + g.trait + '」');
    if (g.tag === 'retire') show.push('退隐 · 待传承');
    return show;
  }

  /** 掷出来的事 (交手 / 路上的坎) 没写进正文 —— 如实标出来, 不装作打过 */
  function growthWarns(g) {
    const warns = [];
    if (!g) return warns;
    if (g.duel_omitted) warns.push('这一回真打了一场, 正文里却没写出来 (战报仍在下面)');
    if (g.checks_omitted) warns.push('这一回路上的坎没写进正文 (判定仍在下面)');
    return warns;
  }

  /* ── 内嵌块 (前台: 最新一回) ───────────────────────────────────
     判定 / 化身 / 交手 都要**回到正文里对应的那一段** —— 那一段就是
     这件事发生的地方。内嵌块因此必须**轻**: 一行能读完, 掷骰明细塞进
     title (悬停可见), 完整战报留在档案页。 */

  /** 路上的坎 —— 内嵌。掷骰明细塞 title, 不占版面。 */
  function checkInline(c) {
    if (!c) return '';
    const pill = c.fumble ? 'err' : (c.success ? 'ok' : 'warn');
    const res = c.success ? '过了' : '没过';
    const out = c.outcome || c.hint || '';
    const dice = c.total != null && c.dc != null
      ? ('d20 ' + c.d20 + (c.mod ? ' +' + c.mod : '') + ' = ' + c.total +
         ' / ' + (c.dc_label || c.dc))
      : '';
    return '<div class="adv-in adv-in-check" title="' + esc(dice || res) + '">' +
      '<span class="adv-in-tag ' + pill + '">路上的坎</span>' +
      '<b>' + esc(c.label || '') + '</b>' +
      '<span class="adv-in-res">' + esc(res) + '</span>' +
      (out ? '<span class="adv-in-out">' + esc(out) + '</span>' : '') +
      '</div>';
  }

  /** 化身 (这一趟读到的化成的东西) —— 内嵌。 */
  function sparkInline(s) {
    if (!s) return '';
    return '<div class="adv-in adv-in-spark">' +
      '<span class="adv-in-tag">化成了</span>' +
      '<span class="adv-in-cat">' + esc(CAT_CN[s.cat] || s.cat || '') + '</span>' +
      '<span class="adv-in-trace">' + esc(s.trace || '') + '</span>' +
      (s.image ? '<span class="adv-in-img">' + esc(s.image) + '</span>' : '') +
      '</div>';
  }

  /** 交手 —— 内嵌 (一行; 完整战报去档案页)。 */
  function duelInline(d) {
    if (!d || !d.foe) return '';
    const res = d.result === 'win' ? 'ok' : (d.result === 'lose' ? 'err' : 'warn');
    const hp = d.hp || {};
    return '<div class="adv-in adv-in-duel">' +
      '<span class="adv-in-tag ' + res + '">动了手</span>' +
      '<b>' + esc(d.foe.name || '对手') + '</b>' +
      '<span class="adv-in-res">' + esc(d.result_cn || '') + '</span>' +
      (hp.from != null && hp.to != null
        ? '<span class="adv-in-out">气血 ' + hp.from + ' → ' + hp.to + '</span>' : '') +
      ((d.moves || []).length
        ? '<span class="adv-in-out">使过 ' + esc((d.moves || []).slice(0, 2).join(' / ')) +
          '</span>' : '') +
      '</div>';
  }

  const api = {
    esc: esc,
    signed: signed,
    d20Verdict: d20Verdict,
    rollRow: rollRow,
    rollsBlock: rollsBlock,
    diceLine: diceLine,
    fightersBlock: fightersBlock,
    duelRound: duelRound,
    duelBlock: duelBlock,
    checksBlock: checksBlock,
    checkInline: checkInline,
    sparkInline: sparkInline,
    duelInline: duelInline,
    kindBadge: kindBadge,
    echoBlock: echoBlock,
    growthChips: growthChips,
    growthWarns: growthWarns,
    KIND_CN: KIND_CN,
    CAT_CN: CAT_CN,
  };

  /* 挂到 window —— 页面脚本是经典脚本, 探针也按全局函数调
     (window.duelBlock / window.checksBlock / ...)。 */
  Object.keys(api).forEach(function (k) {
    if (typeof api[k] === 'function') window[k] = api[k];
  });

  return api;
})();