/* ============================================================
   主题编辑器 · 设计稿交互（2026-10-05 第二版）
   ------------------------------------------------------------
   ⭐⭐⭐ 本版与上一版的关键差别（用户实锤反馈后重做）：

   上一版的两个错误：
   ① 「角色卡和软件内角色页面长得不一样」
      → 我在对话页底部**凭空造了张角色卡**（96px "头图位" + 横排头像），
        而真实的 .ccard 结构是
          [.cbanner(104px 出血头图) | .chead(.cav 60×60 + 名字/标签) |
           .cdesc | .cmeta(药丸) | .caction(按钮)]
        而且 pages.css 里 **.cbanner 本来就存在**（104px、出血到卡片边缘、
        底部 38px 渐隐），我却手画了一个 96px 的"头图位" ⇒ 一眼就假。
   ② 「这个设计虽然是双页，但是不合理」
      → 我做成了「对话页 / 角色页」两个**切换按钮**，让用户在编辑器里
        切页面看效果。但正确语义是：**点 ④ 就应该真的进入角色页**
        （像用户在软件里点侧栏「角色」一样），而不是在编辑器里另开一个页面。

   本版做法：
   · iframe 的 src **可切换**：index.html ⇄ ui_redesign_characters.html
     （都是真页面，UI 100% 真实，不含任何手造元素）
   · 选 ①②③ → 载入对话页；选 ④ → 载入角色页（真页面自带 `.nav.on` 高亮）
   · ④ 的 anchor 用**真实的 `.ccard .cbanner`**（104px 头图位）
   · ③ 的 anchor：优先真实的 `.ccard .cav`（60×60 圆角方），退到 `.side .brand`
   · 顶栏"图位所在页面"显示当前 iframe 的**真实页面名**
   · 预览按被加载页面的 .app 真实尺寸缩放（两页都是 1080×740 基准）
   ============================================================ */
(function () {
  'use strict';

  /* ---------- 两个真实页面（相对路径，file:// 与 WebView 下都可用） ---------- */
  var PAGES = {
    chat:  { name: '对话页', src: '_te_real_chat.html' },
    chars: { name: '角色页', src: '_te_real_characters.html' }
  };

  /* ---------- 图位定义 ---------- */
  var SLOTS = {
    hero: {
      label: '主页背景', color: '#378ADD', tag: 'inside', on: 'chat',
      spec: '建议 1920×1080 以上横图', spec2: '占整屏，按 cover 裁切',
      anchor: function (doc) { return doc.querySelector('.app'); },
      radius: 20,
      hint: '整个窗口（含圆角）'
    },
    stand: {
      label: '角色立绘', color: '#7F77DD', tag: 'left', on: 'chat',
      spec: '建议 800×1000 竖图', spec2: '垫在 Live2D 角色后面',
      anchor: function (doc) { return doc.querySelector('.stage .panel'); },
      radius: 8,
      hint: '右侧 Live2D 舞台'
    },
    avatar: {
      label: '头像', color: '#D85A30', tag: 'outleft', on: 'chat',
      spec: '建议 512×512 正方形', spec2: '聊天气泡 / 角色卡内头像共用',
      /* 对话页侧栏是纯文字品牌区（没有圆形头像），角色页才有真正的 .cav 60×60 */
      anchor: function (doc) {
        return doc.querySelector('.ccard .cav') || doc.querySelector('.side .brand');
      },
      radius: 16,
      hint: '角色卡内的头像（60×60）'
    },
    cover: {
      /* ⭐ 第四版：头图绑定角色，落点是角色卡**卡内通栏**（.chead 下方、.cdesc 上方，
         232×76 左右），不再是出血到卡外的 104px。
         未绑定角色时这个图位被锁：见 bindChar()。 */
      label: '角色卡头图', color: '#1D9E75', tag: 'bottom', on: 'chars',
      spec: '建议 1200×400 横图（卡内通栏，约 3:1）', spec2: '写在角色卡内部，头像名字仍并排在上面',
      anchor: function (doc) { return doc.querySelector('.ccard .cbanner'); },
      radius: 8,
      hint: '角色卡内的头图（.cbanner，76px）',
      needBind: true
    }
  };
  var ORDER = ['hero', 'stand', 'avatar', 'cover'];
  var NUM = { hero: '①', stand: '②', avatar: '③', cover: '④' };

  /* ---------- 绑定角色（第四版）：主题给谁，④ 才有归属 ---------- */
  /* 产品侧对应 theme_store.bind()：一个角色只能绑一个主题，绑新的会把旧的顶掉。 */
  var bound = null;                 /* null = 未绑定；'danya' / 'newbie' / '__none' */
  var CHARS = {
    danya:   { name: '达妮娅',   canHead: true },
    newbie:  { name: '未命名伙伴', canHead: false },   /* 开发中，不能绑 */
    '__none': { name: '', canHead: false }
  };
  function canFill(k) {
    var s = SLOTS[k];
    if (!s.needBind) return true;
    return !!(bound && CHARS[bound] && CHARS[bound].canHead);
  }
  function bindReason(k) {
    if (!SLOTS[k].needBind) return '';
    if (!bound) return '④ 角色卡头图必须先绑定角色 —— 它会写到那个角色的「头图」上。先在上面选一个角色。';
    if (!CHARS[bound].canHead) return '「' + CHARS[bound].name + '」还没开放，暂时不能设头图。换一个角色，或选「暂不绑定」只做全局配色。';
    return '';
  }
  function bindChar(id) {
    bound = id;
    document.querySelectorAll('.te-char').forEach(function (b) {
      b.classList.toggle('on', b.dataset.char === id);
    });
    var note = document.getElementById('teBindNote');
    if (id && CHARS[id] && CHARS[id].canHead) {
      note.classList.remove('warn');
      note.textContent = '已绑定「' + CHARS[id].name + '」· 切角色时整套主题跟随，一个角色只挂一个主题';
    } else {
      note.classList.add('warn');
      note.textContent = (id && CHARS[id] && CHARS[id].name)
        ? '「' + CHARS[id].name + '」是占位角色，不能设头图'
        : '未绑定角色 · 只能做全局配色，④ 已锁';
    }
    document.getElementById('teSlotCover').classList.toggle('locked', !canFill('cover'));
    syncStrip(); measure(); select(cur);
  }
  document.querySelectorAll('.te-char').forEach(function (b) {
    b.addEventListener('click', function () { bindChar(b.dataset.char); });
  });
  document.getElementById('teBindClear').addEventListener('click', function () { bindChar(null); });

  /* ---------- 深浅主题：跟随界面，不写死 ---------- */
  function ensureTheme() {
    var root = document.documentElement;
    if (!root.getAttribute('data-theme')) {
      var dark = !window.matchMedia || !window.matchMedia('(prefers-color-scheme: light)').matches;
      root.setAttribute('data-theme', dark ? 'dark' : 'light');
    }
    if (!root.getAttribute('data-accent')) root.setAttribute('data-accent', 'aurora');
  }

  var stage = document.getElementById('teStage');
  var hint = document.getElementById('teHint');
  var iframe = document.getElementById('teFrame');
  var frameHost = document.getElementById('teFrameHost');
  var where = document.getElementById('teWhere');
  var page = 'chat';
  var doc = null;
  var boxes = {};
  var cur = 'hero';
  var imgs = {};

  function inFrame(e) { return e ? (e.contentDocument || e.contentWindow.document) : null; }

  /* ---------- 切换真实页面（真的换 iframe src） ---------- */
  function loadPage(key) {
    page = key;
    where.textContent = PAGES[key].name;
    hint.style.display = '';
    hint.textContent = '正在载入' + PAGES[key].name + '…';
    doc = null; boxes = {};
    paint(); syncStrip();
    iframe.src = PAGES[key].src;
    document.getElementById('teGoChars').classList.toggle('hot', key === 'chars');
    document.getElementById('teGoChat').classList.toggle('hot', key === 'chat');
  }

  iframe.addEventListener('load', function () {
    doc = inFrame(iframe);
    if (!doc) { hint.textContent = '页面载入失败'; return; }
    try {
      doc.documentElement.setAttribute('data-theme',
        document.documentElement.getAttribute('data-theme') || 'light');
      doc.documentElement.setAttribute('data-accent', 'aurora');
    } catch (e) {}
    /* 预览不可交互：像图片一样；并把 .app 固定成 1080×740 便于缩放 */
    try {
      var st = doc.createElement('style');
      st.textContent = '*{pointer-events:none !important}'
        + '.app{width:1080px !important;height:740px !important;'
        + 'max-width:none !important;max-height:none !important;'
        + 'min-width:0 !important;min-height:0 !important;}';
      doc.head.appendChild(st);
    } catch (e) {}
    fit(); syncStrip(); select(cur);
  });
  document.getElementById('teGoChars').addEventListener('click', function () { select('cover'); });
  document.getElementById('teGoChat').addEventListener('click', function () { select('hero'); });

  /* ---------- 等比缩放（不给用户手动缩放控件） ---------- */
  function getScale() {
    var t = getComputedStyle(frameHost).transform;
    if (!t || t === 'none') return 1;
    var m = t.match(/matrix\(([^,]+)/);
    return m ? (parseFloat(m[1]) || 1) : 1;
  }
  function fit() {
    if (!frameHost) return;
    var pad = 36, aw = 1080, ah = 740;
    var bw = stage.clientWidth - pad, bh = stage.clientHeight - pad;
    var s = Math.min(bw / aw, bh / ah);
    if (!isFinite(s) || s <= 0) s = 1;
    frameHost.style.transform = 'scale(' + s + ')';
    frameHost.style.marginLeft = (-(aw * s - aw) / 2) + 'px';
    frameHost.style.marginTop = (-(ah * s - ah) / 2) + 'px';
    measure();
  }

  /* ---------- ⭐ 实测：offset* + 沿 offsetParent 链累加（不受 transform 影响） ---------- */
  function measure() {
    boxes = {};
    if (!doc) { paint(); return; }
    ORDER.forEach(function (k) {
      var s = SLOTS[k];
      var el = null;
      try { el = s.anchor(doc); } catch (e) { el = null; }
      if (!el) { boxes[k] = null; return; }
      var w = el.offsetWidth, h = el.offsetHeight;
      if (!w || !h) { boxes[k] = null; return; }
      /* 未绑角色 ⇒ 真实页面里也没有该角色的头图（bannerHtml 只在 c.banner 非空时插 .cbanner），
         所以这里把框撤掉，而不是画一个"其实不存在"的指示框。 */
      if (!canFill(k)) { boxes[k] = null; return; }
      var x = el.offsetLeft, y = el.offsetTop, op = el.offsetParent;
      while (op && op !== doc.body && op !== doc.documentElement) {
        x += op.offsetLeft; y += op.offsetTop; op = op.offsetParent;
      }
      boxes[k] = { x: x, y: y, w: w, h: h, radius: s.radius };
    });
    paint();
    if (ORDER.some(function (k) { return boxes[k]; })) hint.style.display = 'none';
  }

  /* ---------- 画指示框 ---------- */
  function paint() {
    var hot = document.getElementById('teHot');
    if (!hot) return;
    hot.innerHTML = '';
    if (!doc) return;
    ORDER.forEach(function (k) {
      var s = SLOTS[k], b = boxes[k];
      /* anchor 在当前真实页面不存在 ⇒ 不画框（绝不造假 UI），只在此前的图位条标灰 */
      if (!b) return;
      var box = document.createElement('div');
      box.className = 'te-hs';
      box.style.setProperty('--hs-c', s.color);
      box.style.setProperty('--hs-r', (b.radius || 8) + 'px');
      box.style.left = b.x + 'px';
      box.style.top = b.y + 'px';
      box.style.width = b.w + 'px';
      box.style.height = b.h + 'px';
      if (imgs[k]) box.style.setProperty('--hs-img', 'url(' + imgs[k] + ')');
      box.title = s.label + ' · 点击填图';
      box.addEventListener('click', function () { select(k); });
      var tag = document.createElement('div');
      tag.className = 'te-hs-tag';
      tag.setAttribute('data-tag', s.tag);
      tag.textContent = NUM[k] + ' ' + s.label;
      box.appendChild(tag);
      hot.appendChild(box);
    });
  }

  /* ---------- 图位条 ---------- */
  function syncStrip() {
    ORDER.forEach(function (k) {
      var btn = document.querySelector('.te-slot[data-slot="' + k + '"]');
      if (!btn) return;
      var ok = !!boxes[k];
      btn.classList.toggle('off', !ok);
      btn.classList.toggle('has', !!imgs[k]);
      btn.classList.toggle('locked', !canFill(k));
      btn.title = !canFill(k) ? bindReason(k)
        : (ok ? SLOTS[k].hint
        : (SLOTS[k].label + '：在「' + PAGES[SLOTS[k].on].name + '」，点上方按钮过去'));
      var em = btn.querySelector('em');
      if (em) {
        em.textContent = imgs[k] ? '已配'
          : (!canFill(k) ? '先绑角色' : (ok ? '空' : '在' + PAGES[SLOTS[k].on].name));
      }
    });
  }

  /* ---------- 属性栏：选到别的页面的图位 ⇒ 自动真的切过去 ---------- */
  function select(k) {
    cur = k;
    var need = SLOTS[k].on;
    if (need !== page) { loadPage(need); return; }   /* 等 load 后会再回调 select */
    document.querySelectorAll('.te-slot').forEach(function (b) {
      b.classList.toggle('on', b.dataset.slot === k);
    });
    var s = SLOTS[k], b = boxes[k], fillable = canFill(k);
    var why = bindReason(k);
    document.getElementById('teSlotName').textContent = NUM[k] + ' ' + s.label;
    document.getElementById('teSlotState').textContent =
      !fillable ? '需先绑定角色'
        : (b ? (imgs[k] ? '已配置' : '未配置') : '该页无此图位');
    var th = document.getElementById('teThumb');
    if (imgs[k]) { th.classList.add('filled'); th.style.backgroundImage = 'url(' + imgs[k] + ')'; th.textContent = ''; }
    else { th.classList.remove('filled'); th.style.backgroundImage = ''; th.textContent = fillable ? '点「选择图片」填入' : '未绑定角色，暂不可填'; }
    document.getElementById('teSpec').textContent = s.spec;
    document.getElementById('teSpec2').textContent = s.spec2;
    document.getElementById('tePick').disabled = !(b && fillable);
    document.getElementById('teClear').disabled = !imgs[k];
    var lk = document.getElementById('teLockHint');
    lk.hidden = !why;
    if (why) lk.textContent = why;
    /* 保存时会发生什么 —— ④ 与其它三个的落点不同，必须说清 */
    document.getElementById('teWriteNote').textContent =
      k === 'cover'
        ? '④ 会写入「' + (bound && CHARS[bound] ? CHARS[bound].name : '') + '」的 banner 字段：角色卡和角色档案弹窗都会换成这张图。'
        : '① ② ③ 只写进这个主题包，互不影响。';
  }

  /* ---------- 填图（本地文件 → dataURL 预览，不落盘） ---------- */
  var file = document.createElement('input');
  file.type = 'file';
  file.accept = 'image/*,video/mp4';
  file.style.display = 'none';
  document.body.appendChild(file);
  file.addEventListener('change', function () {
    var f = file.files && file.files[0];
    if (!f) return;
    var fr = new FileReader();
    fr.onload = function () { imgs[cur] = fr.result; paint(); syncStrip(); select(cur); };
    fr.readAsDataURL(f);
  });
  document.getElementById('tePick').addEventListener('click', function () { file.click(); });
  document.getElementById('teClear').addEventListener('click', function () {
    delete imgs[cur]; paint(); syncStrip(); select(cur);
  });
  document.getElementById('teTweak').addEventListener('click', function () {
    document.getElementById('teSw1').style.background = 'var(--accent-1)';
    document.getElementById('teSw2').style.background = 'var(--accent-2)';
    document.getElementById('teSwTxt').textContent = '手动微调（设计稿示意）';
  });

  /* ---------- 页面壁纸清单（独立于四图位） ---------- */
  (function pages() {
    var box = document.getElementById('tePages');
    if (!box) return;
    ['日报周报', '记忆库', '设置', '语音', '知识库'].forEach(function (n) {
      var c = document.createElement('span');
      c.className = 'te-pagechip';
      c.innerHTML = '<i class="mk"></i>' + n;
      c.addEventListener('click', function () {
        box.querySelectorAll('.te-pagechip').forEach(function (x) { x.classList.remove('on'); });
        c.classList.add('on');
      });
      box.appendChild(c);
    });
  })();

  /* ---------- 顶栏 ---------- */
  document.getElementById('teRename').addEventListener('click', function () {
    var n = prompt('主题名称', document.getElementById('teName').textContent);
    if (n && n.trim()) document.getElementById('teName').textContent = n.trim();
  });
  document.getElementById('teSave').addEventListener('click', function () {
    var n = Object.keys(imgs).length;
    document.getElementById('teState').textContent =
      n ? ('已填 ' + n + ' 张 · 待保存') : '空白主题 · 跟随界面配色';
  });
  document.getElementById('teClose').addEventListener('click', function () {
    document.getElementById('teRoot').style.display = 'none';
  });
  document.querySelectorAll('.te-slot').forEach(function (b) {
    b.addEventListener('click', function () { select(b.dataset.slot); });
  });

  /* ---------- 启动 ---------- */
  ensureTheme();
  where.textContent = PAGES.chat.name;
  /* 默认未绑定：让用户一进来就看到「先绑角色」这条前置，而不是绑好了才发现 */
  bindChar(null);
  iframe.src = PAGES.chat.src;
  window.addEventListener('resize', fit);
  var tries = 0;
  var t = setInterval(function () { fit(); if (++tries > 10) clearInterval(t); }, 200);
})();
