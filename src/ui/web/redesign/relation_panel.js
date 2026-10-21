/* 关系右栏面板（P4）—— 拉取关系状态、渲染心情 chip + 情感卡 + 控制项，定时刷新。
 * 与聊天页解耦：挂到 window.initChatPage（SPA 每次切回聊天页都会调），自身管理刷新定时器避免泄漏。
 * 防御：脚本可能晚于 app.js 内联块加载，故用轮询包裹 initChatPage，并对 DOM/桥缺失静默降级。
 */
(function () {
  'use strict';
  var POLL_MS = 15000;
  var _timer = null;

  function $(s) { return document.querySelector(s); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  var MOOD_HINT = {
    '雀跃': '她今天心情很好，话会比平时多一点。',
    '平静': '她安安静静地待着，不急不躁。',
    '想你': '她有点想你，只是没说出口。',
    '闹别扭': '她嘴上不说，其实有点不高兴，哄一句比讲道理管用。',
    '炸毛': '她在气头上，语气会冲一点，先别顶。',
    '蔫蔫的': '她提不起劲、话也少，陪陪她。'
  };

  function moodHint(mood) {
    return MOOD_HINT[mood] || '她安安静静地待着。';
  }

  function render(state) {
    if (!state || !state.success) return;
    var el;
    el = $('#relMoodChip'); if (el) el.textContent = state.mood || '平静';
    el = $('#relStage'); if (el) el.textContent = state.stage || '萍水';
    el = $('#relArch'); if (el) el.textContent = state.archetype || '温柔';
    el = $('#relArchTag'); if (el) el.textContent = state.archetype_inferred ? '·自动' : '';
    el = $('#relProgress'); if (el) el.textContent = state.progress || '刚认识';
    el = $('#relCardText'); if (el) el.textContent = moodHint(state.mood);

    var pb = $('#relProactiveBtn');
    if (pb) {
      var on = String((state.settings && state.settings.proactive) || 'on') !== 'off';
      pb.classList.toggle('on', on);
      pb.classList.remove('off');
      pb.textContent = '';
      pb.setAttribute('aria-checked', on ? 'true' : 'false');
    }
    var pace = $('#relPace'); if (pace && state.settings) pace.value = state.settings.pace || '标准';
    var freq = $('#relFreq'); if (freq && state.settings) freq.value = state.settings.frequency || 'normal';

    var last = $('#relLast');
    if (last) {
      last.textContent = '上次互动 ' + (state.last_interaction || '从未') +
        (state.pending_response ? ' · 等你回' : '');
    }
    var ev = $('#relEvent');
    if (ev) ev.textContent = state.last_event ? ('最近：' + state.last_event) : '';
  }

  /* ================= 原型下拉（2026-10-21） =================
     面板一直显示「原型 温柔 ·自动」，但从来没有能改的地方 —— 后端 set_archetype_manual
     早就写好了、界面没有入口（反向接线闸门记了很久）。那个「·自动」标签本身就在说
     "手动覆盖是设计里的东西"，只是没建。

     两条规矩：
       1. 选项**来自后端** `get_relationship_state().archetypes`，不在这里写死枚举 ——
          写死的话后端加一个原型，界面永远看不到它（本仓吃过 MOODS 白名单的亏）。
       2. 老后端没有这个字段时**禁用下拉并说明**，不猜一份清单兜底。
  */
  function fillArchetypes(state) {
    var sel = $('#relArchSel');
    if (!sel) return;
    var list = (state && state.archetypes) || [];
    if (!list.length) {
      sel.disabled = true;
      sel.title = '这个版本的后端没有提供原型列表';
      return;
    }
    var key = list.join('\u0001');
    if (sel._optsKey !== key) {          /* 枚举变了才重建，避免每次轮询都闪一下 */
      sel._optsKey = key;
      while (sel.firstChild) sel.removeChild(sel.firstChild);
      for (var i = 0; i < list.length; i++) {
        var op = document.createElement('option');
        op.value = list[i];
        op.textContent = list[i];
        sel.appendChild(op);
      }
    }
    sel.disabled = false;
    sel.title = '手动指定她的性格原型；改过之后不再自动推断';
    if (state.archetype) sel.value = state.archetype;
  }

  /* ================= 最近事件列表（2026-10-21） =================
     get_relation_events 返回同一份日志的最近 N 条（旧→新）。这里倒过来渲染，
     最近的在上；空列表整块隐藏，不留一条空线。 */
  var EVENT_LIMIT = 6;

  function _hhmm(ts) {
    try {
      var d = new Date(Number(ts) * 1000);
      var h = d.getHours(), m = d.getMinutes();
      if (isNaN(h) || isNaN(m)) return '';
      return (h < 10 ? '0' : '') + h + ':' + (m < 10 ? '0' : '') + m;
    } catch (e) { return ''; }
  }

  function renderEvents(res) {
    var box = $('#relEventList');
    if (!box) return;
    var items = (res && res.success && res.events) || [];
    while (box.firstChild) box.removeChild(box.firstChild);
    if (!items.length) { box.style.display = 'none'; return; }
    box.style.display = '';
    for (var i = items.length - 1; i >= 0; i--) {
      var it = items[i] || {};
      var row = document.createElement('div');
      row.className = 'rel-ev-row';
      var when = _hhmm(it.ts);
      var line = (when ? when + ' ' : '') + (it.kind || '');
      if (it.detail) line += ' · ' + it.detail;
      row.textContent = line;            /* textContent：detail 里可能有尖括号，不走 innerHTML */
      row.title = line;
      box.appendChild(row);
    }
  }

  function api() {
    return (window.pywebview && window.pywebview.api) ? window.pywebview.api : null;
  }

  async function refresh() {
    try {
      var a = api();
      if (!a) return;
      if (a.get_relationship_state) {
        var st = await a.get_relationship_state();
        render(st);
        fillArchetypes(st);
      }
      if (a.get_relation_events) {
        renderEvents(await a.get_relation_events('', EVENT_LIMIT));
      }
    } catch (e) { /* 静默 */ }
  }

  async function setSetting(key, value) {
    try {
      var a = api();
      if (a && a.set_relationship_setting) await a.set_relationship_setting('', key, value);
    } catch (e) { /* 静默 */ }
    refresh();
  }

  function bind() {
    var pb = $('#relProactiveBtn');
    if (pb && !pb._bound) {
      pb._bound = true;
      pb.addEventListener('click', function () {
        var on = pb.classList.contains('on');
        setSetting('proactive', on ? 'off' : 'on');
      });
    }
    var pace = $('#relPace');
    if (pace && !pace._bound) {
      pace._bound = true;
      pace.addEventListener('change', function () { setSetting('pace', pace.value); });
    }
    var freq = $('#relFreq');
    if (freq && !freq._bound) {
      freq._bound = true;
      freq.addEventListener('change', function () { setSetting('frequency', freq.value); });
    }
    var as = $('#relArchSel');
    if (as && !as._bound) {
      as._bound = true;
      as.addEventListener('change', async function () {
        var v = as.value;
        if (!v) return;
        try {
          var a = api();
          if (a && a.set_archetype_manual) await a.set_archetype_manual('', v);
        } catch (e) { /* 静默 */ }
        refresh();
      });
    }
    var rb = $('#relResetBtn');
    if (rb && !rb._bound) {
      rb._bound = true;
      rb.addEventListener('click', async function () {
        if (!window.confirm('确定重置关系？当前关系会归档后恢复默认。')) return;
        try {
          var a = api();
          if (a && a.reset_relationship) await a.reset_relationship('');
        } catch (e) { /* 静默 */ }
        refresh();
      });
    }
    var fb = $('#relFoldBtn');
    if (fb && !fb._bound) {
      fb._bound = true;
      fb.addEventListener('click', function () {
        setFold(!document.body.classList.contains('rel-collapsed'));
      });
    }
  }

  /* ================= 心情详细折叠（2026-10-15） =================
     折叠：详细区向下收起（只留标题 + 心情 chip），上方 Live2D/立绘区动画变正方形；
     展开：向上展开回当前比例（立绘区回默认 250px 高）。
     动画用 GSAP（index.html 已加载）；无 gsap / 系统减少动态效果时直接切换。
     状态存 localStorage，重开软件保持。 */
  var FOLD_KEY = 'danyaRelCollapsed';
  var _foldTween = null;

  function _panel() { return document.querySelector('.stage .panel'); }
  function _body() { return $('#relBody'); }

  function _applyCollapsedInstant() {
    document.body.classList.add('rel-collapsed');
    var b = _body();
    if (b) { b.style.height = '0px'; b.style.opacity = '0'; }
    var fb = $('#relFoldBtn');
    if (fb) fb.setAttribute('aria-expanded', 'false');
  }

  function setFold(collapse) {
    var b = _body();
    var p = _panel();
    var fb = $('#relFoldBtn');
    var reduce = false;
    try { reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) {}
    var hasGsap = !!(window.gsap && !reduce);

    if (collapse) {
      if (hasGsap && b && p) {
        if (_foldTween) { try { _foldTween.kill(); } catch (e) {} }
        /* 详细区向下收起 + 立绘区长到正方形（并行动画） */
        var target = p.clientWidth;
        _foldTween = gsap.timeline({ onComplete: function () {
          /* 动画结束交回 CSS（body.rel-collapsed 的 aspect-ratio 接管，resize 仍保正方形） */
          document.body.classList.add('rel-collapsed');
          p.style.height = '';
          _foldTween = null;
        }});
        _foldTween.to(b, { height: 0, opacity: 0, duration: 0.35, ease: 'power2.inOut' }, 0);
        _foldTween.to(p, { height: target, duration: 0.45, ease: 'power2.inOut' }, 0);
      } else {
        _applyCollapsedInstant();
        if (p) p.style.height = '';
      }
      if (fb) fb.setAttribute('aria-expanded', 'false');
      try { localStorage.setItem(FOLD_KEY, '1'); } catch (e) {}
    } else {
      if (hasGsap && b && p) {
        if (_foldTween) { try { _foldTween.kill(); } catch (e) {} }
        /* 先把当前（正方形）高度钉成内联值，再摘 class，避免瞬间跳变 */
        p.style.height = p.clientHeight + 'px';
        document.body.classList.remove('rel-collapsed');
        var t = gsap.timeline({ onComplete: function () {
          p.style.height = '';   /* 交回 CSS 默认 250px（= 展开后的当前比例） */
          _foldTween = null;
        }});
        t.to(b, { height: 'auto', opacity: 1, duration: 0.4, ease: 'power2.inOut' }, 0);
        t.to(p, { height: 250, duration: 0.45, ease: 'power2.inOut' }, 0);
      } else {
        document.body.classList.remove('rel-collapsed');
        if (b) { b.style.height = ''; b.style.opacity = ''; }
        if (p) p.style.height = '';
      }
      if (fb) fb.setAttribute('aria-expanded', 'true');
      try { localStorage.setItem(FOLD_KEY, '0'); } catch (e) {}
    }
  }

  function restoreFold() {
    var saved = null;
    try { saved = localStorage.getItem(FOLD_KEY); } catch (e) {}
    if (saved === '1') _applyCollapsedInstant();
  }

  window.initRelationPanel = function () {
    bind();
    restoreFold();   /* 折叠状态跨会话保持（localStorage） */
    refresh();
    if (_timer) clearInterval(_timer);
    _timer = setInterval(refresh, POLL_MS);
  };

  // 把面板初始化挂进聊天页初始化（兼容 SPA 重入）。脚本可能早于 app.js 内联块定义 initChatPage，
  // 故轮询包裹：待 initChatPage 出现即包裹一次；首次整页渲染若已错过，兜底延迟再跑一次。
  function wrapInit() {
    if (typeof window.initChatPage === 'function' && !window.__relWrapped) {
      window.__relWrapped = true;
      var orig = window.initChatPage;
      window.initChatPage = function () {
        try { if (typeof orig === 'function') orig(); } catch (e) {}
        try { window.initRelationPanel(); } catch (e) {}
      };
    }
  }
  var _w = setInterval(function () {
    wrapInit();
    if (window.__relWrapped) clearInterval(_w);
  }, 100);
  // 兜底：若 initChatPage 已先跑过（本脚本加载偏晚），确保面板至少初始化一次
  setTimeout(function () { try { window.initRelationPanel(); } catch (e) {} }, 1000);
})();
