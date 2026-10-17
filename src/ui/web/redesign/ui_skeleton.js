/* ============================================================
   ui_skeleton.js · 通用骨架屏 + 扫光  v2（2026-10-13）
   ------------------------------------------------------------
   v1 的问题（开发者实测"看不到骨架屏"）：
   ① 数据毫秒级返回 → 骨架一闪而过，肉眼不可见
      ⇒ v2 加 **最短展示时长 MIN_MS(700ms)**：即使数据秒回，
         骨架也会停留够时间再交错浮现（真实产品同款做法）
   ② 宿主若是 position:static，absolute 骨架层会相对更外层定位
      ⇒ 可能画到可视区之外。v2 的 show() 会强制宿主 relative
   ③ 骨架形状与真实内容不一致
      ⇒ v2 的 'wizard' 预设改为横向卡片排（与真实轮播一致）

   交互不变：show(host, specs|preset, targets) → reveal(host, stagger) → 自动清理
   ============================================================ */
(function () {
  'use strict';
  if (window.Skeleton) return;

  var reduce = false;
  try { reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) {}

  /* 骨架最短展示时长已废弃（v2）：数据到了就立刻放行。
     骨架的价值在于"等待期间有反馈"，不在于"必须展示够久"。 */

  var STYLE_ID = 'ui-skeleton-style';
  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) return;
    var st = document.createElement('style');
    st.id = STYLE_ID;
    st.textContent =
      '.sk2{position:relative;background:var(--sk2-bg, rgba(255,255,255,.06));' +
      'border-radius:var(--sk2-r,10px);overflow:hidden;' +
      'box-shadow:inset 0 0 0 1px rgba(255,255,255,.03)}' +
      /* 一道柔和斜向光带，1.5s 从左往右扫过（所有骨架相位一致 → 视觉上是同一道） */
      '.sk2::after{content:"";position:absolute;inset:0;' +
      'background:linear-gradient(100deg,transparent 24%,' +
      '  color-mix(in srgb,var(--sk2-shine,#fff) 11%,transparent) 44%,' +
      '  color-mix(in srgb,var(--sk2-shine,#fff) 17%,transparent) 50%,' +
      '  color-mix(in srgb,var(--sk2-shine,#fff) 11%,transparent) 56%,' +
      '  transparent 76%);' +
      'transform:translateX(-120%);' +
      'animation:sk2Sweep 1.5s cubic-bezier(.4,0,.2,1) infinite;will-change:transform}' +
      '@keyframes sk2Sweep{0%{transform:translateX(-120%)}100%{transform:translateX(120%)}}' +
      '.sk2-host{position:relative}' +
      /* 骨架层：absolute 盖在内容上（内容此时不可见但占位，避免布局跳动） */
      '.sk2-layer{position:absolute;inset:0;z-index:60;pointer-events:none;' +
      '  display:flex;flex-direction:column;justify-content:flex-start;gap:12px;padding:18px 20px;' +
      '  transition:opacity .38s cubic-bezier(.16,1,.3,1);' +
      '  background:var(--sk2-layer-bg, transparent)}' +
      '.sk2-layer.out{opacity:0}' +
      /* 内容浮现：骨架淡出的同时，块按 60ms 错峰 淡入+上移8px */
      '.sk2-host.sk2-on>[data-sk2-target]{opacity:0;transform:translateY(8px)}' +
      '[data-sk2-reveal]{opacity:1 !important;transform:none !important;' +
      '  transition:opacity .42s cubic-bezier(.16,1,.3,1),transform .42s cubic-bezier(.16,1,.3,1)}' +
      '.sk2.av{--sk2-r:50%}.sk2.bar{--sk2-r:8px}.sk2.blk{--sk2-r:14px}.sk2.chip{--sk2-r:99px}' +
      '.sk2-row{display:flex;gap:12px;align-items:center}' +
      '@media (prefers-reduced-motion:reduce){.sk2::after{animation:none;display:none}}';
    document.head.appendChild(st);
  }

  function el(s) {
    var d = document.createElement('div');
    d.className = 'sk2 ' + (s.type || 'bar');
    if (s.flex) d.style.flex = s.flex;           // 先处理 flex（占满剩余宽度）
    else if (s.w) d.style.width = typeof s.w === 'number' ? s.w + 'px' : s.w;
    if (s.h) d.style.height = typeof s.h === 'number' ? s.h + 'px' : s.h;
    if (s.selfEnd) d.style.marginLeft = 'auto';
    return d;
  }

  /** 预设：形状与真实内容一致 */
  function preset(name) {
    if (name === 'wizard') {
      /* 引导页首屏：说明条两根 + **横向 3 张方向卡**（与真实轮播一致，v1 是竖条堆叠）*/
      var card = function () {
        return [
          { type: 'row', raw: [
            { type: 'av', w: 34, h: 34 },
            { type: 'bar', w: '52%', h: 15 }
          ] },
          { type: 'bar', w: '92%', h: 10 },
          { type: 'bar', w: '84%', h: 10 },
          { type: 'bar', w: '70%', h: 10 },
          { type: 'chip', w: '38%', h: 28 }
        ];
      };
      return [
        { type: 'bar', w: '72%', h: 16 },
        { type: 'bar', w: '45%', h: 10 },
        { type: 'blk', w: '100%', h: 150, raw: card() },
        { type: 'blk', w: '100%', h: 150, raw: card() },
        { type: 'blk', w: '100%', h: 150, raw: card() }
      ];
    }
    /* ---- 页面切换骨架（SPA 切栏用，形状按各子页真实布局）---- */
    if (name === 'stickers') {
      /* 表情包管理：统计条 5 格 + 左包列表 + 右网格 */
      return [
        { type: 'row', raw: [
          { type: 'blk', w: 0, h: 62, flex: '1' }, { type: 'blk', w: 0, h: 62, flex: '1' },
          { type: 'blk', w: 0, h: 62, flex: '1' }, { type: 'blk', w: 0, h: 62, flex: '1' },
          { type: 'blk', w: 0, h: 62, flex: '1' }
        ] },
        { type: 'row', raw: [
          { type: 'blk', w: 218, h: 300 },
          { type: 'blk', w: 0, h: 300, flex: '1' }
        ] }
      ];
    }
    if (name === 'chars') {
      /* 角色：搜索条 + 大网格卡片（含圆头像/名字条/按钮） */
      var cc = function () {
        return [
          { type: 'row', raw: [{ type: 'av', w: 52, h: 52 }, { type: 'bar', w: '60%', h: 14 }] },
          { type: 'bar', w: '88%', h: 10 },
          { type: 'bar', w: '64%', h: 10 },
          { type: 'chip', w: '44%', h: 26 }
        ];
      };
      return [
        { type: 'row', raw: [{ type: 'bar', w: '52%', h: 34 }, { type: 'chip', w: 96, h: 34, selfEnd: true }] },
        { type: 'blk', w: '100%', h: 190, raw: cc() },
        { type: 'blk', w: '100%', h: 190, raw: cc() },
        { type: 'blk', w: '100%', h: 190, raw: cc() }
      ];
    }
    if (name === 'settings' || name === 'dev' || name === 'profiles') {
      /* 设置/开发者：左侧分类列表 + 右侧大面板 */
      var rows = [];
      for (var i = 0; i < 6; i++) {
        rows.push({ type: 'row', raw: [
          { type: 'bar', w: (i % 2 ? '38%' : '58%'), h: 13 },
          { type: 'chip', w: 74, h: 26, selfEnd: true }
        ] });
      }
      return [
        { type: 'row', raw: [{ type: 'blk', w: 200, h: 380 }, { type: 'blk', w: 0, h: 380, flex: '1', raw: rows }] }
      ];
    }
    if (name === 'memory' || name === 'lib') {
      /* 记忆库/知识库：统计条 + 列表条目 */
      return [
        { type: 'row', raw: [
          { type: 'blk', w: 0, h: 56, flex: '1' }, { type: 'blk', w: 0, h: 56, flex: '1' },
          { type: 'blk', w: 0, h: 56, flex: '1' }, { type: 'blk', w: 0, h: 56, flex: '1' }
        ] },
        { type: 'blk', w: '100%', h: 64 },
        { type: 'blk', w: '100%', h: 64 },
        { type: 'blk', w: '100%', h: 64 },
        { type: 'blk', w: '100%', h: 64 }
      ];
    }
    if (name === 'daily' || name === 'calls') {
      /* 日报周报/通话记录：筛选条 + 时间线卡片 */
      return [
        { type: 'row', raw: [{ type: 'chip', w: 110, h: 30 }, { type: 'chip', w: 110, h: 30 }, { type: 'chip', w: 110, h: 30 }] },
        { type: 'blk', w: '100%', h: 96 },
        { type: 'blk', w: '100%', h: 96 },
        { type: 'blk', w: '100%', h: 96 }
      ];
    }
    if (name === 'generic') {
      /* 未知页兜底：标题 + 大面板 */
      return [
        { type: 'bar', w: '40%', h: 18 },
        { type: 'blk', w: '100%', h: 320 }
      ];
    }
    /* 主页对话：头像 + 气泡条，交替左右（与真实消息行同形） */
    return [
      { type: 'av', w: 36, h: 36 },
      { type: 'bar', w: '62%', h: 14 },
      { type: 'bar', w: '38%', h: 14 },
      { type: 'av', w: 36, h: 36, selfEnd: true },
      { type: 'bar', w: '54%', h: 14, selfEnd: true },
      { type: 'av', w: 36, h: 36 },
      { type: 'blk', w: '48%', h: 92 },
      { type: 'chip', w: '30%', h: 30 }
    ];
  }

  function build(list, parent) {
    list.forEach(function (s) {
      if (s.raw) {                       // 容器块：内部再铺子骨架（模拟卡片内部结构）
        var box = el(s);
        var inner = document.createElement('div');
        inner.style.cssText = 'display:flex;flex-direction:column;gap:9px;padding:14px 16px';
        build(s.raw, inner);
        box.appendChild(inner);
        parent.appendChild(box);
        return;
      }
      if (s.type === 'row') {            // 横向行（头像+文字 并排）
        var r = document.createElement('div');
        r.className = 'sk2-row';
        (s.raw || []).forEach(function (x) { r.appendChild(el(x)); });
        parent.appendChild(r);
        return;
      }
      parent.appendChild(el(s));
    });
  }

  /* ============================================================
     v3 · 三段式等待机制（解决「看得见扫光」与「绝不锁死」的矛盾）
     ------------------------------------------------------------
     之前两版的教训：
       · MIN_MS=700 硬等 → 数据早到了也拖着 → 所有页面冤死
       · 完全不延时 → 页面 5~20ms 就换完 → 扫光一闪而过看不见
     v3 的解法（YouTube/Facebook 同款思路）：
       ① 内容**先挂载**，骨架只是盖在上面的一层「视觉」——
          层永远 pointer-events:none，**期间页面可正常点击**，不可能锁死
       ② 停留时长 = clamp(minHold − 已加载耗时, 0, minHold)：
            · 页面加载快(<450ms) → 补足到 450ms，保证扫光看得见
            · 页面本来就慢(>450ms) → 不再追加等待，立即浮现
       ③ 双保险：
            · 2.5s 强制放行定时器（无论 reveal 有没有被调）
            · reveal/hide 随时可强制释放全部内容
       ④ minHold 是旋钮：Skeleton.minHold = 0 即恢复「零延时」模式
     ============================================================ */

  /** 骨架最短可见时长（ms）。想调观感改这里；设 0 = 内容就绪立即浮现。 */
  var minHold = 450;
  /** 强制放行兜底（ms）：无论如何，骨架层最多存在这么久。 */
  var FAILSAFE_MS = 2500;

  function forceRelease(host) {
    if (!host) return;
    if (host.__sk2Failsafe) { clearTimeout(host.__sk2Failsafe); host.__sk2Failsafe = null; }
    var layer = host.__sk2Layer;
    if (layer && layer.parentNode) layer.parentNode.removeChild(layer);
    host.__sk2Layer = null;
    host.classList.remove('sk2-on');
    (host.__sk2Targets || []).forEach(function (e) {
      e.removeAttribute('data-sk2-target');
      e.setAttribute('data-sk2-reveal', '1');
    });
    host.__sk2Targets = [];
    host.__sk2Revealing = false;
  }

  function show(host, specs, targets, navStart) {
    if (!host) return null;
    ensureStyle();
    /* 修定位：宿主若是 static，absolute 骨架层会相对更外层摆放 → 画到屏幕外 */
    if (getComputedStyle(host).position === 'static') host.style.position = 'relative';
    host.classList.add('sk2-host', 'sk2-on');

    var tgEls = [];
    (targets || []).forEach(function (sel) {
      Array.prototype.forEach.call(host.querySelectorAll(sel), function (e) {
        e.setAttribute('data-sk2-target', '1');
        tgEls.push(e);
      });
    });
    // targets 也可能在宿主之外（如 .carousel 与 #detectTxt 不在同一个父级）—— 全局兜底
    if (!tgEls.length) {
      (targets || []).forEach(function (sel) {
        try {
          Array.prototype.forEach.call(document.querySelectorAll(sel), function (e) {
            e.setAttribute('data-sk2-target', '1');
            tgEls.push(e);
          });
        } catch (e) {}
      });
    }
    /* 未指定 targets（SPA 切栏场景）：自动取宿主**顶层内容块**做错峰浮现对象。
       ⚠️ 必须排除骨架层自己（.sk2-layer）—— 否则会把骨架当成"内容"去浮现。 */
    if (!tgEls.length) {
      Array.prototype.forEach.call(host.children, function (e, i) {
        if (i >= 8) return;                       // 最多 8 块，错峰不至于拖太长
        if (e.classList && (e.classList.contains('sk2-layer'))) return;
        e.setAttribute('data-sk2-target', '1');
        tgEls.push(e);
      });
    }
    host.__sk2Targets = tgEls;
    /* 计时起点用「导航开始」而不是「show 此刻」：
       这样 phase-1（fetch 期骨架）+ phase-2（真身上覆盖层）共用一个时钟，
       页面若已加载 900ms，phase-2 就不会再补 450ms —— 总可见时长 = max(minHold, 加载耗时) */
    host.__sk2Start = (typeof navStart === 'number' && navStart) ||
                      (window.performance && performance.now()) || Date.now();

    var layer = document.createElement('div');
    layer.className = 'sk2-layer';
    build(Array.isArray(specs) ? specs : preset(specs || 'chat'), layer);
    host.appendChild(layer);
    host.__sk2Layer = layer;

    /* ③ 强制放行兜底：无论后续发生什么（reveal 没调 / 脚本报错 / 时序异常），
          骨架层最多存在 FAILSAFE_MS 就被拆掉 —— 「冤死」在结构上不可能再发生 */
    if (host.__sk2Failsafe) clearTimeout(host.__sk2Failsafe);
    host.__sk2Failsafe = setTimeout(function () { forceRelease(host); }, FAILSAFE_MS);

    host.__sk2Revealing = false;
    return layer;
  }

  function doReveal(host, step) {
    if (host.__sk2Failsafe) { clearTimeout(host.__sk2Failsafe); host.__sk2Failsafe = null; }
    var layer = host.__sk2Layer;
    if (layer) {
      layer.classList.add('out');
      var kill = function () { if (layer.parentNode) layer.parentNode.removeChild(layer); };
      layer.addEventListener('transitionend', kill, { once: true });
      setTimeout(kill, 800);
      host.__sk2Layer = null;
    }
    host.classList.remove('sk2-on');
    (host.__sk2Targets || []).forEach(function (e, i) {
      if (reduce) { e.style.opacity = 1; e.style.transform = 'none'; return; }
      setTimeout(function () { e.setAttribute('data-sk2-reveal', '1'); }, i * step);
      setTimeout(function () { e.removeAttribute('data-sk2-target'); }, i * step + 650);
    });
    host.__sk2Targets = [];
  }

  function reveal(host, staggerMs) {
    if (!host) return;
    if (host.__sk2Revealing) return;                // 防重复（轮询会多次调）
    host.__sk2Revealing = true;
    /* ② 停留时长 = clamp(minHold − 已加载耗时, 0, minHold)
         · 页面加载快 → 补足到 minHold，扫光看得见
         · 页面本来就慢 → 不追加等待，立即浮现
       ① 内容早已挂载、层是 pointer-events:none → 这段等待期间页面照样可点，
          与「冤死」有本质区别：那是内容没挂上，这是内容已在、只差动画。 */
    var start = host.__sk2Start || 0;
    var elapsed = ((window.performance && performance.now()) || Date.now()) - start;
    var wait = Math.max(0, Math.min(minHold, minHold - elapsed));
    if (wait <= 0) { doReveal(host, typeof staggerMs === 'number' ? staggerMs : 60); return; }
    host.__sk2HoldTimer = setTimeout(function () {
      doReveal(host, typeof staggerMs === 'number' ? staggerMs : 60);
    }, wait);
  }

  function hide(host) {
    if (!host) return;
    if (host.__sk2HoldTimer) { clearTimeout(host.__sk2HoldTimer); host.__sk2HoldTimer = null; }
    forceRelease(host);
  }

  window.Skeleton = {
    show: show, reveal: reveal, hide: hide, preset: preset,
    /* ④ 旋钮：Skeleton.minHold = 0 可切回"零延时"；= 800 更明显 */
    get minHold() { return minHold; },
    set minHold(v) { minHold = Math.max(0, v | 0); },
    /* 紧急出口：任何页面卡在骨架上，控制台跑这句立即全部放行 */
    releaseAll: function () {
      Array.prototype.forEach.call(document.querySelectorAll('.sk2-layer'), function (l) {
        l.remove();
      });
      Array.prototype.forEach.call(document.querySelectorAll('[data-sk2-target]'), function (e) {
        e.removeAttribute('data-sk2-target'); e.setAttribute('data-sk2-reveal', '1');
      });
      Array.prototype.forEach.call(document.querySelectorAll('.sk2-host'), function (e) {
        e.classList.remove('sk2-on');
      });
    }
  };
})();
