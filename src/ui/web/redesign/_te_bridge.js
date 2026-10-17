/* ============================================================
   预览小桥 · 供主题编辑器跨域 / file:// 场景使用（2026-10-05）
   ------------------------------------------------------------
   为什么需要它：
   编辑器把真实页面放进 iframe 当预览。只要 origin 不完全一致
   （最常见：用户直接双击 file:// 打开设计稿），
   `iframe.contentDocument` 拿不到 ⇒ 所有跨帧 JS 操作**静默失败**：
     页面照常渲染、CSS 照常生效，但量不到坐标、写不进 --tm-* 变量，
   表现就是「点了四个都不放大」「添加文件按钮飘在正中间」。
   解法：一律走 postMessage —— 父页与 iframe 互相 postMessage 双向通行，
   不管同源不同源、不管 file:// 还是 http://，这条路永远通。
   ============================================================ */
(function () {
  'use strict';

  var VW = 1080, VH = 740;

  /* 每个图位的候选选择器（按顺序取第一个"量得到"的） */
  var SEL = {
    hero:   ['.app'],
    stand:  ['.stage .panel', '.stage'],
    avatar: ['.side .brand', '.ccard .cav', '.cav', '.av'],
    cover:  ['.ccard:not(.dev)', '.ccard', '.char-card']
  };
  var ORDER = ['hero', 'stand', 'avatar', 'cover'];

  function fb(el) {
    if (!el) return null;
    var r = el.getBoundingClientRect();
    if (!r.width || !r.height) return null;
    /* 预览页被钉成 1080×740 ⇒ 1 CSS 像素 = 1 预览坐标，直接用，不做换算 */
    return { x: Math.round(r.left), y: Math.round(r.top),
             w: Math.max(1, Math.round(r.width)), h: Math.max(1, Math.round(r.height)) };
  }
  function first(sels) {
    for (var i = 0; i < sels.length; i++) {
      var e = document.querySelector(sels[i]);
      var r = e ? fb(e) : null;
      if (r && r.w > 2 && r.h > 2) return r;
    }
    return null;
  }
  function sane(b) {
    if (!b || !b.w || !b.h) return false;
    return b.w >= 4 && b.h >= 4 && b.x >= -8 && b.y >= -8 &&
           b.x <= VW - 4 && b.y <= VH - 4;
  }
  function inset(r, v) {
    return { x: Math.round(r.x + v), y: Math.round(r.y + v),
             w: Math.max(1, Math.round(r.w - 2 * v)), h: Math.max(1, Math.round(r.h - 2 * v)) };
  }

  /* 拿不到真实尺寸时（如 .stage 在无 Live2D 时 display:none）按布局推算，
     宁可估一个像样的位置，也不要让编辑器画不出框 —— 那等于"点了没反应"。 */
  function estimate(k) {
    var app = first(['.app']) || { x: 0, y: 0, w: VW, h: VH };
    if (k === 'stand') {
      var side = first(['.side', 'aside.side']) || { x: app.x, y: app.y, w: Math.round(app.w * 0.22), h: app.h };
      var foot = first(['.composer', '.inputbar', '.input-bar', '.bottom', 'footer']);
      var botH = foot ? foot.h : 0;
      var h = Math.round((app.h - botH) * 0.9);
      var w = Math.round(Math.min((app.w - side.w) * 0.36, h * 0.62));
      return { x: Math.round(app.x + app.w - w), y: Math.round(app.y + app.h - h), w: w, h: h };
    }
    if (k === 'avatar') {
      var s2 = first(['.side', 'aside.side']);
      if (!s2) return null;
      var d = Math.round(Math.max(22, Math.min(34, s2.w * 0.2)));
      return { x: s2.x + Math.round((s2.w - d) / 2), y: s2.y + 18, w: d, h: d };
    }
    if (k === 'cover') {
      return { x: Math.round(app.x + app.w * 0.26), y: Math.round(app.y + app.h * 0.14),
               w: Math.round(app.w * 0.48), h: Math.round(app.h * 0.5) };
    }
    return null;
  }

  function measure() {
    var out = {};
    ORDER.forEach(function (k) {
      var r = first(SEL[k] || []);
      if (sane(r)) { out[k] = (k === 'stand') ? inset(r, 6) : r; return; }
      var e = estimate(k);
      if (sane(e)) out[k] = e;
    });
    return out;
  }

  /* 父页把 --tm-* 变量与 has-* 类推过来，这里落地（等价生产 theme_manager.js） */
  function apply(msg) {
    var d = document.documentElement;
    var vars = (msg && msg.vars) || {}, has = (msg && msg.has) || {};
    ORDER.forEach(function (k) {
      var v = '--tm-' + k;
      if (vars[v] && vars[v] !== 'none') d.style.setProperty(v, vars[v]);
      else d.style.removeProperty(v);
      d.style.setProperty(v + '-focus', 'center');
    });
    var map = {
      hero:   [['body', 'has-hero']],
      stand:  [['.stage', 'has-stand']],
      avatar: [['.cav', 'has-img'], ['.av', 'has-img'], ['.side .brand', 'has-img']],
      cover:  [['.ccard', 'has-cover'], ['.char-card', 'has-cover']]
    };
    ORDER.forEach(function (k) {
      var on = !!has[k];
      (map[k] || []).forEach(function (p) {
        var els = document.querySelectorAll(p[0]);
        for (var i = 0; i < els.length; i++) {
          els[i].classList.toggle(p[1], on);
          if (k === 'cover') els[i].classList.toggle('off', !!(has.off && has.off.cover));
        }
      });
    });
    /* 页面壁纸（2026-10-12）：父页把 --tm-wall-<key> 与合成值 --tm-wall-cur 一并推来。
       ⚠️ 变量与属性缺一不可：design_tokens.css 的规则是
          body[data-tm-pages="on"][data-tm-wall="on"]::before{ background-image:var(--tm-wall-cur) }
          —— 只写变量不设属性这条规则根本不命中（旧版还把属性挂在 <html> 上，永不生效）。 */
    Object.keys(vars).forEach(function (v) {
      if (v.indexOf('--tm-wall') !== 0) return;
      var val = vars[v];
      [d, document.body].forEach(function (el) {
        if (!el) return;
        if (val && val !== 'none') el.style.setProperty(v, val);
        else el.style.removeProperty(v);
      });
    });
    var wall = (msg && msg.wall) || null;
    if (wall) {
      [d, document.body].forEach(function (el) {
        if (!el) return;
        el.setAttribute('data-tm-pages', wall.pages ? 'on' : 'off');
        el.setAttribute('data-tm-wall', wall.cur ? 'on' : 'off');
        if (wall.page) el.setAttribute('data-tm-page', wall.page);
      });
    }
    /* 【2026-10-06】编辑态标记：聚焦时藏掉主页那四个取景角标（.panel .tick），
       它们和编辑器自己的高亮描边叠在一起会被看成"四个角标错位"。
       由父页在 setFocus 变化时通过 msg.edit 告知。 */
    document.body.classList.toggle('te-editing', !!(msg && msg.edit && msg.edit.focus));
  }

  /* 调试日志默认关，逼 ?debug=1 才打 —— 免得污染正式控制台 */
  var DEBUG = /(\?|&)debug=1/.test(location.search || '');
  function say() { if (DEBUG) console.info('[bridge] ' + [].join.call(arguments, ' ')); }
  function push(tag, data) {
    try {
      parent.postMessage({ __te: tag, data: data }, '*');
      say('pushed ' + tag);
    } catch (e) {
      say('push failed: ' + e.message);
    }
  }
  /* 把已经落地的 --tm-* 报回去：编辑器用它自检「图到底有没有进预览」。
     ⚠️ 跨域下父页读不到 iframe 的 contentDocument，没有这条回包就只能猜。 */
  function pushed() {
    var cs = getComputedStyle(document.documentElement), o = {};
    ORDER.forEach(function (k) { o[k] = cs.getPropertyValue('--tm-' + k).trim(); });
    o._has = { hero: document.documentElement.classList.contains('has-hero'),
               cover: !!document.querySelector('.ccard.has-cover'),
               stand: !!document.querySelector('.stage.has-stand') };
    return o;
  }
  function sendBoxes() { push('boxes', measure()); }

  window.addEventListener('message', function (e) {
    var d = e && e.data;
    if (!d || d.__te !== 'te') return;
    if (d.act === 'measure') sendBoxes();
    else if (d.act === 'apply') { apply(d); sendBoxes(); push('vars', pushed()); }
    else if (d.act === 'vars') push('vars', pushed());
  });
  window.addEventListener('load', function () { setTimeout(sendBoxes, 80); });
  window.addEventListener('resize', sendBoxes);
  sendBoxes();   /* 早发一次，父页不等 load 也能拿到 */
})();
