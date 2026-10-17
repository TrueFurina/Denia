/* ============================================================================
   本地模型下载器 · UI 模块（2026-10-12 · B2 落码）
   ----------------------------------------------------------------------------
   设计稿：docs/mockups/模型下载器_设计稿_20261012.html（v1，已过评审）
   设计语言：沿用引导页/设置页 token（--accent-1/2、--surface*、--ink*、--line*），
             深浅模式自动跟随；动效复用 call_motion.css 原子（页面需已引入）。
   后端接线（全部走已有桥，新增三个已在 app_backend.js 注册）：
     Backend.getModelCatalog()           → 显存 + 各档 available/recommended/tight
     Backend.pickModelDir()              → 系统文件夹选择框
     Backend.modelDirInfo(path)          → 剩余空间 + 已安装档
     Backend.downloadModelQuant({...})   → 起任务，返回 task_id
     Backend.modelQuantStatus(taskId)    → {status,log,overall_pct,speed_mbps,eta_s}
     Backend.stopModelDownload(id, keep) → 暂停(keep=true)/取消(keep=false)
     Backend.deleteModelQuant({...})     → 卸载
     Backend.listModelDownloads({...})   → 下载总览（进行中任务+断点残留；切档保留/小窗口共用）
   红线：不画假进度 —— 百分比/速度/ETA 全部来自后端真实字节统计；
         桥调用一律 6s 超时，超时报错而不挂死（项目既有教训）。
   用法：ModelDownloader.open()  独立弹层；或 ModelDownloader.mountInto(容器)
   ============================================================================ */
(function () {
  "use strict";

  /* B4（2026-10-12）修「构造期快照」：原 var B = window.Backend || {} 在脚本解析时求值，
     而 pywebview 的 Backend 注入晚于脚本、且引导页场景下 Backend 在 window.parent 上——
     快照会把 B 冻结成 {} → 所有桥调用静默降级（项目历史坑，与引导页 BK 同款修法）。
     改为实时代理：每次属性访问都重新解析（含 parent 兜底）。 */
  function getB() {
    try {
      if (window.parent && window.parent !== window && window.parent.Backend) return window.parent.Backend;
    } catch (e) { /* 跨域：忽略 */ }
    return window.Backend || null;
  }
  var B = new Proxy(function () {}, {
    get: function (_, prop) { var b = getB(); return b ? b[prop] : undefined; },
    set: function () { return true; },
    apply: function () { return undefined; }
  });

  /* ---------- 工具 ---------- */
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (m) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[m];
    });
  }
  function icon(name, size) {
    var P = {
      download: '<path d="M12 15V3"/><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/>',
      bars: '<path fill="currentColor" stroke="none" d="M11 6H13V18H11V6Z"/><path fill="currentColor" stroke="none" d="M7 13H9V18H7V13Z"/><path fill="currentColor" stroke="none" d="M15 9H17V18H15V9Z"/>',
      pause: '<rect x="14" y="3" width="5" height="18" rx="1"/><rect x="5" y="3" width="5" height="18" rx="1"/>',
      play: '<path d="M5 5a2 2 0 0 1 3.008-1.728l11.997 6.998a2 2 0 0 1 .003 3.458l-12 7A2 2 0 0 1 5 19z"/>',
      x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
      folder: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
      cpu: '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="M17 20v2"/><path d="M17 2v2"/><path d="M2 12h2"/><path d="M20 12h2"/>',
      info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
      db: '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5V19A9 3 0 0 0 21 19V5"/><path d="M3 12A9 3 0 0 0 21 12"/>',
      pkg: '<path d="M11 21.73a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73z"/><path d="M12 22V12"/><polyline points="3.29 7 12 12 20.71 7"/>',
      zap: '<path d="M15.914 4a1.5 1.5 0 00-2.474-1.561l-9 9A1.5 1.5 0 005.5 14h4.002a.5.5 0 01.471.666L8.086 20a1.5 1.5 0 002.475 1.56l9-9A1.5 1.5 0 0018.5 10h-3.997a.5.5 0 01-.472-.667z"/>'
    };
    var fill = (name === 'bars') ? ' fill="currentColor" stroke="none"' : ' fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"';
    return '<svg width="' + (size || 16) + '" height="' + (size || 16) + '" viewBox="0 0 24 24"' + fill + '>' + (P[name] || '') + '</svg>';
  }
  /* 桥调用超时保护（项目教训：桥 Promise 可能挂死） */
  function callSafe(fn, arg, ms) {
    var p;
    try { p = fn(arg); } catch (e) { return Promise.resolve({ success: false, error: String(e) }); }
    if (!p || typeof p.then !== 'function') return Promise.resolve(p || { success: false, error: '桥未返回 Promise' });
    return Promise.race([
      p.catch(function (e) { return { success: false, error: String(e) }; }),
      new Promise(function (res) { setTimeout(function () { res({ success: false, error: '后端响应超时（' + (ms || 6000) + 'ms）' }); }, ms || 6000); })
    ]);
  }
  function fmtGB(n) {
    if (n == null || isNaN(n)) return '—';
    return (Math.round(n * 10) / 10) + ' GB';
  }
  function fmtEta(s) {
    if (s == null || isNaN(s) || s <= 0) return '—';
    if (s < 90) return Math.ceil(s) + ' 秒';
    return Math.ceil(s / 60) + ' 分钟';
  }

  /* ---------- 样式（注入一次；作用域限定在 .mdl- 前缀下） ---------- */
  var CSS_ID = 'mdl_downloader_css';
  function ensureCss() {
    if (document.getElementById(CSS_ID)) return;
    var st = document.createElement('style');
    st.id = CSS_ID;
    st.textContent = [
      '.mdl-mask{position:fixed;inset:0;z-index:1200;background:rgba(6,9,15,.62);backdrop-filter:blur(4px);',
      ' display:flex;align-items:flex-start;justify-content:center;overflow:auto;padding:36px 18px;animation:m-fade .32s ease both}',
      '.mdl-sheet{width:100%;max-width:880px;background:var(--bg-2,#0D1018);border:1px solid var(--line);',
      ' border-radius:22px;box-shadow:0 24px 70px rgba(0,0,0,.55);padding:26px 26px 22px;animation:m-pop .32s cubic-bezier(.34,1.56,.64,1) both}',
      '.mdl-head{display:flex;align-items:flex-start;gap:12px;margin-bottom:6px}',
      '.mdl-head .t{font-size:19px;font-weight:700;display:flex;align-items:center;gap:9px}',
      '.mdl-head .s{font-size:12.5px;color:var(--ink-3);margin-top:3px}',
      '.mdl-x{margin-left:auto;background:transparent;border:0;color:var(--ink-3);cursor:pointer;padding:6px;border-radius:9px}',
      '.mdl-x:hover{color:var(--ink);background:var(--surface-2)}',
      '.mdl-card{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:16px 18px;margin-top:14px}',
      '.mdl-card h4{font-size:14.5px;font-weight:650;display:flex;align-items:center;gap:8px;margin:0}',
      '.mdl-card h4 .n{width:20px;height:20px;border-radius:6px;background:var(--accent-soft-1,rgba(45,212,255,.14));',
      ' color:var(--accent-1,#2DD4FF);display:grid;place-items:center;font-size:11.5px;font-weight:700}',
      '.mdl-desc{color:var(--ink-3);font-size:12.5px;margin:6px 0 10px}',
      '.mdl-row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}',
      '.mdl-path{flex:1;min-width:220px;display:flex;align-items:center;gap:8px;background:var(--bg);border:1px solid var(--line);',
      ' border-radius:11px;padding:9px 13px;color:var(--ink-2);font-size:13px}',
      '.mdl-path .pt{color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
      '.mdl-btn{border:1px solid var(--line-2);background:var(--surface-2);color:var(--ink-2);border-radius:11px;',
      ' padding:9px 16px;font-size:13px;font-weight:600;display:inline-flex;align-items:center;gap:7px;transition:.15s}',
      '.mdl-btn:hover{border-color:var(--accent-1);color:var(--accent-1)}',
      '.mdl-chip{display:inline-flex;align-items:center;gap:6px;border-radius:99px;padding:3px 11px;font-size:12px;font-weight:600}',
      '.mdl-chip.ok{background:rgba(61,220,151,.12);color:#3ddc97}',
      '.mdl-chip.warn{background:rgba(255,180,90,.12);color:#ffb45a}',
      '.mdl-chip.bad{background:rgba(255,90,90,.12);color:#ff8a8a}',
      '.mdl-chip.idle{background:var(--surface-2);color:var(--ink-3);border:1px solid var(--line)}',
      '.mdl-list{display:flex;flex-direction:column;gap:9px}',
      '.mdl-q{display:flex;align-items:center;gap:12px;padding:12px 15px;border:1px solid var(--line);border-radius:12px;',
      ' background:var(--surface);cursor:pointer;transition:.18s;text-align:left;width:100%;color:inherit;font:inherit}',
      '.mdl-q:hover:not(.dis){border-color:var(--line-2)}',
      '.mdl-q.on{border-color:var(--accent-1);background:var(--accent-soft-1,rgba(45,212,255,.14))}',
      '.mdl-q.dis{opacity:.55;cursor:not-allowed}',
      '.mdl-q .mi{width:36px;height:36px;border-radius:50%;display:grid;place-items:center;flex:none;color:var(--accent-1);',
      ' background:var(--bg);border:1px solid var(--line)}',
      '.mdl-q .mm{flex:1;min-width:0}',
      '.mdl-q .mn{font-weight:650;font-size:14px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}',
      '.mdl-q .md2{font-size:12px;color:var(--ink-2);margin-top:2px}',
      '.mdl-q .ma{margin-left:auto;flex:none;font-size:12px;font-weight:600;color:var(--ink-3);border:1px solid var(--line);',
      ' border-radius:99px;padding:5px 13px}',
      '.mdl-q.on .ma{border-color:var(--accent-1);color:var(--accent-1)}',
      '.mdl-q.dis .ma{border-color:rgba(255,90,90,.3);color:#ff8a8a}',
      '.mdl-tag{font-size:10.5px;font-weight:700;padding:1px 8px;border-radius:99px;background:var(--accent-soft-1,rgba(45,212,255,.14));color:var(--accent-1,#2DD4FF)}',
      '.mdl-tag.warn{background:rgba(255,180,90,.14);color:#ffb45a}',
      '.mdl-tag.bad{background:rgba(255,90,90,.14);color:#ff8a8a}',
      '.mdl-tag.gray{background:var(--surface-2);color:var(--ink-3)}',
      '.mdl-note{margin-top:10px;font-size:12px;color:var(--ink-3);display:flex;gap:7px;align-items:flex-start}',
      '.mdl-dl{display:flex;align-items:center;gap:11px;margin-top:4px}',
      '.mdl-dlico{width:40px;height:40px;border-radius:12px;display:grid;place-items:center;flex:none;position:relative;',
      ' background:var(--accent-soft-1,rgba(45,212,255,.14));color:var(--accent-1,#2DD4FF)}',
      '.mdl-dlico .m-ping i{position:absolute;inset:0;border-radius:12px;border:1px solid color-mix(in srgb,var(--accent-1) 45%,transparent);',
      ' opacity:0;animation:m-ping 2.4s var(--ease-out,cubic-bezier(.16,1,.3,1)) infinite}',
      '.mdl-dlico .m-ping i:nth-child(2){animation-delay:.8s}',
      '.mdl-dlico .m-ping i:nth-child(3){animation-delay:1.6s}',
      '.mdl-pct{margin-left:auto;text-align:right}',
      '.mdl-pct .n{font-size:26px;font-weight:700;font-variant-numeric:tabular-nums;background:linear-gradient(90deg,var(--accent-1,#2DD4FF),var(--accent-2,#A06BFF));',
      ' -webkit-background-clip:text;background-clip:text;color:transparent}',
      '.mdl-pct .u{font-size:11px;color:var(--ink-3)}',
      '.mdl-bar{position:relative;height:14px;border-radius:99px;background:var(--surface-2);border:1px solid var(--line);overflow:hidden;margin:14px 0 10px}',
      '.mdl-bar .f{position:absolute;inset:0;width:0%;border-radius:99px;background:linear-gradient(90deg,var(--accent-1,#2DD4FF),var(--accent-2,#A06BFF));transition:width .35s ease}',
      '.mdl-bar .f::after{content:"";position:absolute;inset:0;background:repeating-linear-gradient(-45deg,transparent 0 8px,rgba(255,255,255,.07) 8px 16px)}',
      '.mdl-bar.run .f::before{content:"";position:absolute;top:0;bottom:0;width:38%;background:linear-gradient(90deg,transparent,rgba(255,255,255,.5),transparent);animation:m-shimmer 1.8s ease infinite}',
      '.mdl-stats{display:flex;gap:18px;flex-wrap:wrap;font-size:12.5px;color:var(--ink-3);font-variant-numeric:tabular-nums}',
      '.mdl-stats b{color:var(--ink-2);font-weight:600}',
      '.mdl-log{margin-top:10px;font-size:12px;color:var(--ink-3);background:var(--bg);border:1px solid var(--line);border-radius:9px;',
      ' padding:8px 12px;min-height:34px;display:flex;gap:8px;align-items:center}',
      '.mdl-log .t{color:var(--ink-4);flex:none}',
      '.mdl-act{display:flex;gap:10px;margin-top:14px;flex-wrap:wrap;align-items:center}',
      '.mdl-primary{border:0;border-radius:11px;padding:11px 22px;font-size:14px;font-weight:650;display:inline-flex;align-items:center;gap:8px;',
      ' background:linear-gradient(90deg,var(--accent-1,#2DD4FF),var(--accent-2,#A06BFF));color:#06121a;',
      ' box-shadow:0 8px 24px color-mix(in srgb,var(--accent-1) 26%,transparent);position:relative;overflow:hidden}',
      '.mdl-primary:disabled{opacity:.45;cursor:not-allowed;filter:grayscale(.6);box-shadow:none}',
      '.mdl-danger{background:rgba(255,90,90,.12);color:#ff8a8a;border:1px solid rgba(255,90,90,.3)}',
      '.mdl-hint{font-size:12px;color:var(--ink-3);margin-top:8px;display:flex;gap:6px;align-items:center}',
      /* 档位行 + 行内卸载（每个模型可单独删除，2026-10-13） */
      '.mdl-qrow{display:flex;gap:8px;align-items:stretch}',
      '.mdl-qrow .mdl-q{flex:1;min-width:0}',
      '.mdl-qdel{flex:none;width:38px;border:1px solid var(--line);border-radius:12px;background:var(--surface);',
      ' color:var(--ink-3);cursor:pointer;display:grid;place-items:center;transition:.18s;padding:0}',
      '.mdl-qdel:hover{border-color:rgba(255,90,90,.45);color:#ff8a8a;background:rgba(255,90,90,.08)}',
      /* 后台其它档位下载中横幅（切档后仍可暂停/取消那个任务） */
      '.mdl-other{margin-top:12px;display:none;align-items:center;gap:10px;border:1px solid rgba(255,180,90,.35);',
      ' background:rgba(255,180,90,.08);border-radius:12px;padding:10px 13px;font-size:12.5px;color:var(--ink-2)}',
      '.mdl-other .oi{flex:none;color:#ffb45a;display:grid;place-items:center}',
      '.mdl-other .ot{flex:1;min-width:0;line-height:1.45}',
      '.mdl-other .ot b{color:var(--ink)}',
      '.mdl-other .obtn{flex:none;border:1px solid var(--line-2);background:var(--surface-2);color:var(--ink-2);',
      ' border-radius:9px;padding:5px 12px;font-size:12px;font-weight:600;cursor:pointer;display:inline-flex;align-items:center;gap:5px}',
      '.mdl-other .obtn:hover{border-color:var(--accent-1);color:var(--accent-1)}',
      '.mdl-other .obtn.danger:hover{border-color:rgba(255,90,90,.5);color:#ff8a8a}',
      /* 全局小窗口（进软件也看得到下载进度）+ 完成气泡（2026-10-13） */
      '@keyframes mdlBubbleIn{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:none}}',
      '#mdlMiniWrap{position:fixed;top:72px;right:16px;z-index:8500;display:none;flex-direction:column;gap:8px;',
      ' align-items:flex-end;max-width:min(304px,calc(100vw - 32px))}',
      '#mdlMiniWrap.show{display:flex}',
      '.mdl-mini{display:flex;align-items:center;gap:9px;background:var(--bg-2,#0D1018);border:1px solid var(--line);',
      ' border-radius:14px;padding:9px 13px;box-shadow:0 12px 34px rgba(0,0,0,.32);cursor:pointer;',
      ' color:var(--ink);font-size:12.5px;transition:.18s;max-width:100%}',
      '.mdl-mini:hover{border-color:var(--accent-1)}',
      '.mdl-mini .mi{flex:none;width:30px;height:30px;border-radius:9px;display:grid;place-items:center;',
      ' background:var(--accent-soft-1,rgba(45,212,255,.14));color:var(--accent-1,#2DD4FF)}',
      '.mdl-mini .mt{flex:1;min-width:0}',
      '.mdl-mini .mt .l1{font-weight:650;font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
      '.mdl-mini .mt .l2{font-size:11px;color:var(--ink-3);font-variant-numeric:tabular-nums;margin-top:1px;',
      ' white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
      '.mdl-mini .mbar{width:74px;height:5px;border-radius:99px;background:var(--surface-2);overflow:hidden;flex:none}',
      '.mdl-mini .mbar i{display:block;height:100%;width:0%;border-radius:99px;',
      ' background:linear-gradient(90deg,var(--accent-1,#2DD4FF),var(--accent-2,#A06BFF));transition:width .35s ease}',
      '.mdl-mini.warn .mi{background:rgba(255,180,90,.12);color:#ffb45a}',
      '.mdl-mini .mx{flex:none;border:0;background:transparent;color:var(--ink-3);cursor:pointer;padding:3px;',
      ' border-radius:7px;display:grid;place-items:center}',
      '.mdl-mini .mx:hover{color:var(--ink);background:var(--surface-2)}',
      '.mdl-bubble{display:flex;align-items:center;gap:10px;background:var(--bg-2,#0D1018);border:1px solid var(--line);',
      ' border-radius:14px;padding:11px 14px;box-shadow:0 14px 38px rgba(0,0,0,.36);font-size:12.5px;color:var(--ink);',
      ' max-width:100%;animation:mdlBubbleIn .28s ease both}',
      '.mdl-bubble .bi{flex:none;width:28px;height:28px;border-radius:8px;display:grid;place-items:center;',
      ' background:rgba(61,220,151,.14);color:#3ddc97}',
      '.mdl-bubble.err .bi{background:rgba(255,90,90,.12);color:#ff8a8a}',
      '.mdl-bubble .bt{flex:1;min-width:0}',
      '.mdl-bubble .bt .l1{font-weight:650}',
      '.mdl-bubble .bt .l2{font-size:11px;color:var(--ink-3);margin-top:1px}',
      '.mdl-bubble .bgo{flex:none;border:1px solid var(--line-2);background:var(--surface-2);color:var(--ink-2);',
      ' border-radius:9px;padding:5px 11px;font-size:12px;font-weight:600;cursor:pointer}',
      '.mdl-bubble .bgo:hover{border-color:var(--accent-1);color:var(--accent-1)}',
      '.mdl-bubble .bx{flex:none;border:0;background:transparent;color:var(--ink-3);cursor:pointer;padding:3px;',
      ' border-radius:7px;display:grid;place-items:center}',
      '.mdl-bubble .bx:hover{color:var(--ink);background:var(--surface-2)}'
    ].join('');
    document.head.appendChild(st);
  }

  /* ---------- 主体 ---------- */
  var S = {
    host: null, mask: null, timer: null,
    catalog: null, dir: '', dirInfo: null,
    quant: '', taskId: '', phase: 'idle',
    pct: 0, speed: null, eta: null, log: '下载引擎就绪。',
    resume: null,   /* 断点残留（无进行中任务但磁盘有 .incomplete）：{pct,partial_gb,...} */
    other: null,    /* 别的档位正在后台下载的任务（切档后仍可暂停/取消它） */
    busy: false
  };

  function q() { return (S.catalog && S.catalog.quants || []).filter(function (x) { return x.id === S.quant; })[0] || null; }

  function renderShell(host) {
    host.innerHTML =
      '<div class="mdl-head">' +
        '<div><div class="t">' + icon('pkg', 22) + '本地模型 · 下载管理' +
          '<span class="mdl-tag gray" id="mdlVram">检测中…</span></div>' +
          '<div class="s">Comni（MiniCPM-o 4.5）本地大脑。下载一次，之后断网也能聊。与设置页共用同一份任务状态。</div>' +
        '</div>' +
        '<button class="mdl-x" id="mdlClose" type="button" title="关闭">' + icon('x', 18) + '</button>' +
      '</div>' +
      /* ① 目录 */
      '<div class="mdl-card">' +
        '<h4><span class="n">1</span>安装位置</h4>' +
        '<div class="mdl-desc">模型较大，建议放空间充足的非系统盘。下载支持断点续传，中断后重开继续。</div>' +
        '<div class="mdl-row">' +
          '<div class="mdl-path">' + icon('folder', 15) + '<span class="pt" id="mdlPath">（未选择）</span></div>' +
          '<button class="mdl-btn" id="mdlBrowse" type="button">' + icon('folder', 14) + '浏览…</button>' +
        '</div>' +
        '<div class="mdl-row" style="margin-top:10px"><span class="mdl-chip idle" id="mdlSpace">等待选择目录…</span>' +
          '<span style="font-size:12px;color:var(--ink-3)" id="mdlInst"></span></div>' +
      '</div>' +
      /* ② 档位 */
      '<div class="mdl-card">' +
        '<h4><span class="n">2</span>选择量化档</h4>' +
        '<div class="mdl-desc" id="mdlGateTip">正在读取显卡与档位…</div>' +
        '<div class="mdl-list" id="mdlList"><div style="color:var(--ink-3);font-size:12.5px">读取中…</div></div>' +
        '<div class="mdl-note" id="mdlWarn" style="display:none">' + icon('info', 15) + '<span></span></div>' +
      '</div>' +
      /* ③ 下载 */
      '<div class="mdl-card">' +
        '<h4><span class="n">3</span>下载与安装</h4>' +
        '<div class="mdl-dl">' +
          '<div class="mdl-dlico"><div class="m-ping" id="mdlPing" style="display:none"><i></i><i></i><i></i></div>' + icon('bars', 20) + '</div>' +
          '<div><div style="font-size:14.5px;font-weight:650;display:flex;gap:8px;align-items:center" id="mdlState">等待开始<span class="mdl-tag gray">未开始</span></div>' +
            '<div class="mdl-desc" style="margin:1px 0 0" id="mdlSub">选好目录和档位后，点「开始下载」</div></div>' +
          '<div class="mdl-pct"><div class="n" id="mdlPct">0%</div><div class="u" id="mdlSize">— / —</div></div>' +
        '</div>' +
        '<div class="mdl-bar" id="mdlBar"><div class="f" id="mdlFill"></div></div>' +
        '<div class="mdl-stats">' +
          '<span>速度 <b id="mdlSpeed">—</b></span><span>剩余 <b id="mdlEta">—</b></span>' +
          '<span>状态 <b id="mdlPhase">待机</b></span>' +
        '</div>' +
        '<div class="mdl-log"><span class="t">[日志]</span><span id="mdlLog">下载引擎就绪。</span></div>' +
        '<div class="mdl-act">' +
          '<button class="mdl-primary" id="mdlMain" type="button" disabled>' + icon('download', 16) + '开始下载</button>' +
          '<button class="mdl-btn" id="mdlPause" type="button" disabled>' + icon('pause', 15) + '暂停</button>' +
          '<button class="mdl-btn" id="mdlCancel" type="button" disabled>' + icon('x', 15) + '取消</button>' +
          '<button class="mdl-btn mdl-danger" id="mdlDel" type="button" style="display:none">' + icon('pkg', 15) + '卸载</button>' +
          '<button class="mdl-btn" id="mdlCopy" type="button">' + icon('download', 15) + '复制手动命令</button>' +
        '</div>' +
        /* 后台其它档位下载中（切档浏览时也能暂停/取消，不会"下一半摆那了"） */
        '<div class="mdl-other" id="mdlOther">' +
          '<span class="oi">' + icon('bars', 15) + '</span>' +
          '<span class="ot" id="mdlOtherT"></span>' +
          '<button class="obtn" id="mdlOtherPause" type="button">' + icon('pause', 13) + '暂停</button>' +
          '<button class="obtn danger" id="mdlOtherCancel" type="button">' + icon('x', 13) + '取消</button>' +
        '</div>' +
        '<div class="mdl-hint" id="mdlWhy" style="display:none">' + icon('info', 14) + '<span></span></div>' +
      '</div>';
  }

  function $(id) { return document.getElementById(id); }

  function renderQuants() {
    var box = $('mdlList'); if (!box) return;
    if (!S.catalog || !S.catalog.quants || !S.catalog.quants.length) {
      box.innerHTML = '<div style="color:var(--ink-3);font-size:12.5px">未能读取档位清单（后端未返回）。</div>';
      return;
    }
    var anyFit = false;
    var rows = S.catalog.quants.map(function (x) {
      var dis = !x.available;
      if (x.available) anyFit = true;
      var on = (!dis && S.quant === x.id);
      var badge = x.recommended ? '<span class="mdl-tag">推荐</span>'
        : (x.tight ? '<span class="mdl-tag warn">偏紧</span>'
          : (x.manual_only ? '<span class="mdl-tag warn">仅手动</span>' : ''));
      var installed = (S.dirInfo && S.dirInfo.installed || []).some(function (i) { return i.id === x.id; });
      if (installed) badge += '<span class="mdl-tag">已安装</span>';
      var act = dis ? ('需要约 ' + (x.min_card || Math.ceil(x.vram_gb)) + 'GB')
        : (installed ? '已下载' : (on ? '已选择' : '选择'));
      /* 行内卸载：每个模型都能单独删除（不再要求先选中再下滑到③找卸载按钮） */
      var delBtn = installed
        ? '<button type="button" class="mdl-qdel" data-del="' + esc(x.id) + '" title="卸载 ' + esc(x.id) + '（删除已下载文件）">' + icon('x', 15) + '</button>'
        : '';
      return '<div class="mdl-qrow">' +
        '<button type="button" class="mdl-q' + (dis ? ' dis' : '') + (on ? ' on' : '') + '" data-q="' + esc(x.id) + '">' +
          '<span class="mi">' + icon(dis ? 'cpu' : 'bars', 17) + '</span>' +
          '<span class="mm"><span class="mn">' + esc(x.id) + badge + '</span>' +
          '<span class="md2">' + esc(x.quality || '') + ' · 下载 ' + fmtGB(x.size_gb) + ' · 运行需 <b style="color:var(--ink)">' + x.vram_gb + 'GB</b> 显存 · ' + esc(x.note || '') + '</span></span>' +
          '<span class="ma">' + act + '</span></button>' +
        delBtn +
      '</div>';
    }).join('');
    box.innerHTML = rows;
    /* 全员不可用 ⇒ 给出口，不留死路 */
    var w = $('mdlWarn');
    if (!anyFit) {
      w.style.display = 'flex';
      w.querySelector('span').textContent = (S.catalog.warning || '本机显存不足以运行本地双工模型') +
        ' —— 建议先走云端双工；若坚持本地，只能关掉看屏并手动选最小档（有掉帧风险）。';
    } else if (S.catalog.warning) {
      w.style.display = 'flex';
      w.querySelector('span').textContent = S.catalog.warning;
    } else { w.style.display = 'none'; }
    var g = $('mdlGateTip');
    if (g) g.textContent = S.catalog.cloud_only
      ? '本机显存不足以跑本地双工（云端更合适），下面的档位已按显存置灰。'
      : '档位越高质量越好、越吃显存；带「推荐」的是按你的显卡算好的。';
  }

  function renderSpace() {
    var chip = $('mdlSpace'); if (!chip) return;
    var sel = q();
    if (!S.dir) { chip.className = 'mdl-chip idle'; chip.innerHTML = icon('db', 13) + '等待选择目录…'; return; }
    var free = (S.dirInfo && S.dirInfo.free_gb);
    if (free == null) { chip.className = 'mdl-chip idle'; chip.innerHTML = icon('db', 13) + '目录已选，空间未知'; return; }
    var need = sel ? sel.size_gb : 0;
    if (need && free < need) { chip.className = 'mdl-chip bad'; chip.innerHTML = icon('db', 13) + '空间不足 · 剩 ' + fmtGB(free) + ' / 需 ' + fmtGB(need); }
    else if (need && free < need * 1.5) { chip.className = 'mdl-chip warn'; chip.innerHTML = icon('db', 13) + '有点紧 · 剩 ' + fmtGB(free) + ' / 需 ' + fmtGB(need); }
    else { chip.className = 'mdl-chip ok'; chip.innerHTML = icon('db', 13) + '空间充足 · 剩 ' + fmtGB(free) + (need ? ' / 需 ' + fmtGB(need) : ''); }
    var inst = $('mdlInst');
    if (inst) {
      var list = (S.dirInfo && S.dirInfo.installed) || [];
      inst.textContent = list.length ? ('该目录已存：' + list.map(function (i) { return i.id; }).join('、')) : '';
    }
  }

  function renderRun() {
    var sel = q();
    var pct = Math.max(0, Math.min(100, S.pct || 0));
    $('mdlPct').textContent = (pct < 10 ? pct.toFixed(1) : Math.round(pct)) + '%';
    $('mdlFill').style.width = pct + '%';
    $('mdlBar').className = 'mdl-bar' + (S.phase === 'running' ? ' run' : '');
    $('mdlPing').style.display = (S.phase === 'running') ? 'block' : 'none';
    $('mdlSpeed').textContent = (S.speed == null ? '—' : S.speed.toFixed(1) + ' MB/s');
    $('mdlEta').textContent = fmtEta(S.eta);
    $('mdlLog').textContent = S.log || '';
    $('mdlSize').textContent = sel ? (fmtGB(sel.size_gb * pct / 100) + ' / ' + fmtGB(sel.size_gb)) : '— / —';

    var tag = '', sub = '';
    /* 标签只写"状态别称"，避免与前面的主状态文字重复（例："下载中"+"下载中"） */
    if (S.phase === 'idle') {
      tag = S.resume ? '<span class="mdl-tag warn">断点保留</span>' : '<span class="mdl-tag gray">未开始</span>';
      sub = S.resume
        ? ('上次下载中断，已保留 ' + fmtGB(S.resume.partial_gb) + '（约 ' + Math.round(S.resume.pct || 0) + '%），点「继续下载」从断点续传')
        : '选好目录和档位后，点「开始下载」';
    }
    if (S.phase === 'running') { tag = '<span class="mdl-tag">进行中</span>'; sub = '可随时暂停；关掉软件下次从断点继续'; }
    if (S.phase === 'paused') { tag = '<span class="mdl-tag warn">断点保留</span>'; sub = '已下部分保留，点「开始下载」从断点续传'; }
    if (S.phase === 'done') { tag = '<span class="mdl-tag">就位</span>'; sub = '已写入 Comni 配置，重启引擎后生效'; }
    if (S.phase === 'error' || S.phase === 'manual') { tag = '<span class="mdl-tag bad">需手动</span>'; sub = '自动下载不可用，可复制命令手动下载'; }
    if (S.phase === 'canceled') { tag = '<span class="mdl-tag gray">已停止</span>'; sub = '临时文件已清理'; }
    $('mdlState').innerHTML = ({
      idle: '等待开始', running: '下载中', paused: '已暂停',
      done: '安装完成', error: '下载失败', manual: '需手动下载', canceled: '已取消'
    }[S.phase] || '等待开始') + tag;
    $('mdlSub').textContent = sub;
    $('mdlPhase').textContent = ({ idle: '待机', running: '下载中', paused: '已暂停', done: '完成', error: '失败', manual: '手动', canceled: '取消' }[S.phase] || '待机');

    var main = $('mdlMain');
    main.disabled = S.busy || !q() || !S.dir || S.phase === 'running';
    var mainLabel = (S.phase === 'paused') ? '继续下载'
      : (S.phase === 'done' ? '重新校验'
        : (S.resume ? ('继续下载（' + Math.round(S.resume.pct || 0) + '%）') : '开始下载'));
    main.innerHTML = ((S.phase === 'paused' || S.resume) ? icon('play', 16) : icon('download', 16)) + mainLabel;
    $('mdlPause').disabled = !(S.phase === 'running');
    $('mdlCancel').disabled = !(S.phase === 'running' || S.phase === 'paused');
    $('mdlDel').style.display = (q() && (S.dirInfo && S.dirInfo.installed || []).some(function (i) { return i.id === S.quant; })) ? 'inline-flex' : 'none';

    /* 后台其它档位下载中横幅：切档浏览时也能暂停/取消那个任务 */
    var ob = $('mdlOther');
    if (ob) {
      if (S.other && S.other.task_id && S.other.task_id !== S.taskId) {
        var op = (S.other.overall_pct != null ? Math.round(S.other.overall_pct) : 0);
        var ospd = (S.other.speed_mbps ? (' · ' + Number(S.other.speed_mbps).toFixed(1) + ' MB/s') : '');
        ob.style.display = 'flex';
        $('mdlOtherT').innerHTML = '后台还有 <b>' + esc(S.other.quant_id) + '</b> 在下载（' + op + '%' + ospd +
          '）。不用切回那个档位，在这里就能暂停或取消。';
        $('mdlOtherPause').disabled = (S.other.status !== 'running');
        $('mdlOtherCancel').disabled = false;
      } else { ob.style.display = 'none'; }
    }

    var why = $('mdlWhy');
    var msg = '';
    if (!S.dir) msg = '还差一步：先选一个安装目录';
    else if (!q()) msg = '还差一步：选一个可用的量化档';
    else if ((S.dirInfo && S.dirInfo.free_gb != null) && q() && S.dirInfo.free_gb < q().size_gb) msg = '当前目录空间不足，换一个盘或清理后再下';
    why.style.display = msg ? 'flex' : 'none';
    if (msg) why.querySelector('span').textContent = msg;
  }

  function render() { renderQuants(); renderSpace(); renderRun(); }

  function setLog(t) { S.log = t; renderRun(); }

  /* ---------- 后端交互 ---------- */
  function loadCatalog() {
    if (!B.getModelCatalog) { setLog('前端桥未就绪（getModelCatalog 缺失）'); return Promise.resolve(); }
    return callSafe(B.getModelCatalog).then(function (r) {
      if (!r || !r.success) { setLog('读取档位失败：' + ((r && r.error) || '未知错误')); return; }
      S.catalog = r;
      var v = $('mdlVram');
      if (v) v.textContent = (r.vram_gb != null ? ('显存 ' + r.vram_gb + 'GB') : '未检测到独显');
      /* 默认选中推荐档（或第一个可用档） */
      var pick = r.recommended;
      if (!pick) { var f = (r.quants || []).filter(function (x) { return x.available; })[0]; pick = f ? f.id : ''; }
      if (pick) S.quant = pick;
      render();
    });
  }

  function refreshDir(path) {
    if (!path) return Promise.resolve();
    S.dir = path;
    $('mdlPath').textContent = path;
    render();
    if (!B.modelDirInfo) { renderSpace(); return Promise.resolve(); }
    return callSafe(B.modelDirInfo, path).then(function (r) {
      S.dirInfo = (r && r.success) ? r : null;
      render();
    });
  }

  /* ---------- 任务接管（切档/重进不丢进度，2026-10-13）----------
     根因：旧代码切档即 S.taskId='' + stopPoll，后台还在跑的任务瞬间"失联"——
     进度没了、暂停/取消也没了（用户报"切回去得重新点下载""下一半摆那了"）。
     现在每次选档/挂载都向后端要一次总览：有进行中的任务就接管（继续轮询），
     没有任务但有 .incomplete 断点残留就显示"断点保留"，别的档位在跑就出横幅。 */
  function sameDir(a, b) {
    var na = String(a || '').replace(/[\\/]+$/, '').toLowerCase();
    var nb = String(b || '').replace(/[\\/]+$/, '').toLowerCase();
    return !!na && na === nb;
  }
  function pickTask(tasks, quantId, dir) {
    for (var i = 0; i < (tasks || []).length; i++) {
      var t = tasks[i];
      if (t.quant_id === quantId && (!dir || sameDir(t.model_dir, dir))) return t;
    }
    return null;
  }
  function pickPartial(partials, quantId, dir) {
    for (var i = 0; i < (partials || []).length; i++) {
      var p = partials[i];
      if (p.quant_id === quantId && (!dir || sameDir(p.model_dir, dir))) return p;
    }
    return null;
  }
  function phaseFromStatus(st) {
    if (st === 'done' || st === 'paused' || st === 'canceled' || st === 'manual' || st === 'error') return st;
    return 'running';
  }
  function probeTasks() {
    if (!B.listModelDownloads) return Promise.resolve({ tasks: [], partials: [] });
    return callSafe(B.listModelDownloads, { model_dir: S.dir || '' }, 8000).then(function (r) {
      if (!r || !r.success) return { tasks: [], partials: [] };
      return { tasks: r.tasks || [], partials: r.partials || [] };
    }, function () { return { tasks: [], partials: [] }; });
  }
  function adoptTask(t) {
    if (!t || !t.task_id) return;
    S.taskId = t.task_id;
    S.phase = phaseFromStatus(t.status);
    S.pct = (t.overall_pct != null ? t.overall_pct : S.pct);
    S.speed = (t.speed_mbps != null ? t.speed_mbps : null);
    S.eta = (t.eta_s != null ? t.eta_s : null);
    if (t.log) S.log = t.log;
    S.resume = null;
    renderRun();
    if (S.phase === 'running') startPoll();
  }
  /* 同步后端任务视图：接管当前档的任务 / 标记断点 / 标记别档在跑的任务 */
  function syncTasks() {
    return probeTasks().then(function (ov) {
      var mine = pickTask(ov.tasks, S.quant, S.dir);
      if (mine) { adoptTask(mine); }
      else { S.resume = pickPartial(ov.partials, S.quant, S.dir) || null; }
      var other = null;
      for (var i = 0; i < ov.tasks.length; i++) {
        var t = ov.tasks[i];
        if (mine && t.task_id === mine.task_id) continue;
        if (t.quant_id === S.quant && sameDir(t.model_dir, S.dir)) continue;
        other = t; break;
      }
      S.other = other;
      render();
      return ov;
    }, function () { return { tasks: [], partials: [] }; });
  }

  function startDownload() {
    var sel = q();
    if (!sel || !S.dir || S.busy) return;
    S.busy = true; setLog('正在启动下载…'); renderRun();
    callSafe(B.downloadModelQuant, { quant_id: sel.id, model_dir: S.dir }).then(function (r) {
      S.busy = false;
      if (!r || !r.success) { S.phase = 'error'; setLog('启动失败：' + ((r && r.error) || '未知错误')); renderRun(); return; }
      S.taskId = r.task_id || '';
      S.phase = 'running';
      S.resume = null;   /* 断点已被这次下载接管 */
      setLog(r.already_running ? '已有下载任务在跑，已接管进度' : '已开始下载（后台进程）');
      renderRun();
      startPoll();
    });
  }

  function startPoll() {
    stopPoll();
    if (!S.taskId || !B.modelQuantStatus) return;
    S.timer = setInterval(function () {
      callSafe(B.modelQuantStatus, S.taskId, 8000).then(function (r) {
        if (!r || !r.success) { setLog('状态读取失败：' + ((r && r.error) || '')); return; }
        var st = r.status || 'unknown';
        S.pct = (r.overall_pct != null ? r.overall_pct : (st === 'done' ? 100 : S.pct));
        S.speed = (r.speed_mbps != null ? r.speed_mbps : S.speed);
        S.eta = (r.eta_s != null ? r.eta_s : null);
        if (st === 'running') S.phase = 'running';
        else if (st === 'done') S.phase = 'done';
        else if (st === 'manual') S.phase = 'manual';
        else if (st === 'error') S.phase = 'error';
        else if (st === 'paused') S.phase = 'paused';
        else if (st === 'canceled') S.phase = 'canceled';
        if (r.log) S.log = r.log;
        renderRun();
        if (S.phase === 'done' || S.phase === 'error' || S.phase === 'manual' || S.phase === 'canceled') {
          stopPoll();
          if (S.phase === 'done') {
            refreshDir(S.dir);
            /* 下完给个通知（用户报"下完了没有通知气泡"） */
            try { if (B.toast) B.toast('本地模型下载完成：' + (q() ? q().id : '') + '，重启引擎后生效'); } catch (e) {}
          }
          if (S.phase === 'error' || S.phase === 'manual') {
            try { if (B.toast) B.toast('本地模型下载失败，可复制命令手动下载', 'err'); } catch (e) {}
          }
          syncTasks();   /* 刷新断点/别档任务视图 */
        }
      });
    }, 1200);
  }
  function stopPoll() { if (S.timer) { clearInterval(S.timer); S.timer = null; } }

  function stopDownload(keep) {
    if (!S.taskId) { S.phase = 'idle'; S.pct = 0; renderRun(); return; }
    if (!B.stopModelDownload) { setLog('停止下载桥未就绪'); return; }
    S.busy = true;
    var p = Promise.resolve(B.stopModelDownload(S.taskId, keep === true))
      .catch(function (e) { return { success: false, error: String(e) }; });
    Promise.race([p, new Promise(function (res) {
      setTimeout(function () { res({ success: false, error: '停止请求超时' }); }, 6000);
    })]).then(function (r) {
      S.busy = false; stopPoll();
      S.phase = keep ? 'paused' : 'canceled';
      if (!keep) S.pct = 0;
      setLog((r && r.success)
        ? (keep ? '已暂停：已下部分保留，重新点击从断点继续' : '已取消，未完成文件已清理')
        : ('停止失败：' + ((r && r.error) || '未知错误')));
      renderRun();
      syncTasks();   /* 取消后重算断点；暂停后确认别档任务视图 */
    });
  }

  /* 停止"别的档位"的后台任务（横幅按钮用）：不动当前档的 S.phase */
  function stopOtherTask(keep) {
    var t = S.other;
    if (!t || !t.task_id) return;
    if (!B.stopModelDownload) { setLog('停止下载桥未就绪'); return; }
    var p = Promise.resolve(B.stopModelDownload(t.task_id, keep === true))
      .catch(function (e) { return { success: false, error: String(e) }; });
    Promise.race([p, new Promise(function (res) {
      setTimeout(function () { res({ success: false, error: '停止请求超时' }); }, 6000);
    })]).then(function (r) {
      setLog((r && r.success)
        ? ((keep ? '已暂停 ' : '已取消 ') + t.quant_id + ' 的后台下载')
        : ('停止失败：' + ((r && r.error) || '未知错误')));
      syncTasks();
    });
  }

  /* ---------- 挂载 ---------- */
  function bind(host) {
    host.addEventListener('click', function (e) {
      var t = e.target;
      /* 行内卸载优先于选档（删除按钮在档位按钮外侧，但双保险） */
      var delBtn = (t.closest && t.closest('.mdl-qdel')) || null;
      if (delBtn) {
        var did = delBtn.getAttribute('data-del');
        var dq = (S.catalog.quants || []).filter(function (i) { return i.id === did; })[0];
        if (!dq) return;
        if (!window.confirm('确定删除已下载的 ' + did + '（' + fmtGB(dq.size_gb) + '）吗？')) return;
        callSafe(B.deleteModelQuant, { quant_id: did, model_dir: S.dir }).then(function (r) {
          var ok = !!(r && r.success);
          setLog(ok ? ('已卸载 ' + did + '，释放 ' + fmtGB(r.freed_gb)) : ('卸载失败：' + ((r && r.error) || '')));
          try { if (ok && B.toast) B.toast('已卸载 ' + did + '，释放 ' + fmtGB(r.freed_gb)); } catch (e2) {}
          if (S.quant === did) { S.phase = 'idle'; S.pct = 0; }
          refreshDir(S.dir);
        });
        return;
      }
      var qbtn = t.closest ? t.closest('.mdl-q') : null;
      if (qbtn) {
        var id = qbtn.getAttribute('data-q');
        var x = (S.catalog.quants || []).filter(function (i) { return i.id === id; })[0];
        if (!x) return;
        if (!x.available) { setLog(x.id + ' 需要约 ' + (x.min_card || Math.ceil(x.vram_gb)) + 'GB 显存，本机不满足，已置灰'); return; }
        /* 不再盲目清 taskId：向后端要总览，接管该档正在跑的任务 / 显示断点残留，
           别档在跑的任务进横幅（暂停/取消不失联） */
        S.quant = id; stopPoll(); S.taskId = ''; S.phase = 'idle'; S.pct = 0;
        S.speed = null; S.eta = null; S.resume = null; S.other = null;
        render(); setLog('已选档位 ' + id + '（下载 ' + fmtGB(x.size_gb) + '）');
        syncTasks();
        return;
      }
      if (t.closest && t.closest('#mdlBrowse')) {
        if (!B.pickModelDir) { setLog('选目录桥未就绪'); return; }
        callSafe(B.pickModelDir).then(function (r) {
          if (!r || !r.success) { if (r && r.error) setLog('选择目录失败：' + r.error); return; }
          refreshDir(r.path || '').then(function () { syncTasks(); });
        });
        return;
      }
      if (t.closest && t.closest('#mdlMain')) { startDownload(); return; }
      if (t.closest && t.closest('#mdlPause')) { stopDownload(true); return; }
      if (t.closest && t.closest('#mdlCancel')) { stopDownload(false); return; }
      if (t.closest && t.closest('#mdlOtherPause')) { stopOtherTask(true); return; }
      if (t.closest && t.closest('#mdlOtherCancel')) { stopOtherTask(false); return; }
      if (t.closest && t.closest('#mdlDel')) {
        var sel = q(); if (!sel) return;
        if (!window.confirm('确定删除已下载的 ' + sel.id + '（' + fmtGB(sel.size_gb) + '）吗？')) return;
        callSafe(B.deleteModelQuant, { quant_id: sel.id, model_dir: S.dir }).then(function (r) {
          var ok2 = !!(r && r.success);
          setLog(ok2 ? ('已卸载 ' + sel.id + '，释放 ' + fmtGB(r.freed_gb)) : ('卸载失败：' + ((r && r.error) || '')));
          try { if (ok2 && B.toast) B.toast('已卸载 ' + sel.id + '，释放 ' + fmtGB(r.freed_gb)); } catch (e3) {}
          S.phase = 'idle'; S.pct = 0; refreshDir(S.dir);
        });
        return;
      }
      if (t.closest && t.closest('#mdlCopy')) {
        var s2 = q();
        var cmd = (s2 && (S.catalog && S.catalog.quants.filter(function (i) { return i.id === s2.id; })[0] || {}).download_cmd) || '';
        if (!cmd && s2) cmd = 'modelscope download --model OpenBMB/MiniCPM-o-4_5-gguf ' + s2.file + ' --local_dir "' + (S.dir || '') + '"';
        if (!cmd) { setLog('暂无可复制命令'); return; }
        try {
          if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(cmd);
          else {
            var ta = document.createElement('textarea'); ta.value = cmd; ta.style.position = 'fixed'; ta.style.top = '-9999px';
            document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta);
          }
          setLog('命令已复制：' + cmd.slice(0, 60) + '…');
        } catch (err) { setLog('复制失败，请手动复制：' + cmd); }
      }
    });
  }

  function open() {
    ensureCss();
    var mask = document.createElement('div');
    mask.className = 'mdl-mask';
    var sheet = document.createElement('div');
    sheet.className = 'mdl-sheet';
    mask.appendChild(sheet);
    document.body.appendChild(mask);
    renderShell(sheet);
    bind(sheet);
    var close = function () { stopPoll(); if (mask.parentNode) mask.parentNode.removeChild(mask); };
    mask.addEventListener('click', function (e) { if (e.target === mask) close(); });
    sheet.querySelector('#mdlClose').addEventListener('click', close);
    var onEsc = function (e) { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', onEsc); } };
    document.addEventListener('keydown', onEsc);
    /* 初始目录：后端给的默认模型目录（catalog.model_dir 可能为空 → 留空让用户选） */
    loadCatalog().then(function () {
      var p = (S.catalog && S.catalog.model_dir) ? refreshDir(S.catalog.model_dir) : Promise.resolve();
      p.then(function () { syncTasks(); });   /* 接管后台在跑的任务/显示断点，不丢进度 */
    });
    return mask;
  }

  function mountInto(el) {
    ensureCss();
    renderShell(el);
    bind(el);
    var c = el.querySelector('#mdlClose'); if (c) c.style.display = 'none';
    loadCatalog().then(function () {
      var p = (S.catalog && S.catalog.model_dir) ? refreshDir(S.catalog.model_dir) : Promise.resolve();
      p.then(function () { syncTasks(); });
    });
  }

  /* ---------- 全局小窗口 + 完成气泡（2026-10-13）----------
     只由外壳 index.html 显式调用（ModelDownloader.autoMini()），弹层页不自动起。
     治两个用户报的点：①没下完进软件没有任何进度提示；②下完了没有通知。
     轮询 list_model_downloads（全局视图，3s 一次）：
       - 有进行中任务 → 右上角小胶囊（档位/百分比/速度），点击打开下载器弹层；
       - 无任务但有 .incomplete 断点 → 黄色"上次下到 xx%，点击继续"；
       - 任务 running → done/error → 完成/失败气泡（带「查看」按钮，12s 自消）。 */
  var MINI = { timer: null, seen: {}, dismissed: {}, started: false };

  function miniEnsure() {
    ensureCss();   /* 小胶囊/气泡的样式与弹层同源；autoMini 可能没开过弹层，必须先注入 */
    var w = document.getElementById('mdlMiniWrap');
    if (!w) {
      w = document.createElement('div');
      w.id = 'mdlMiniWrap';
      document.body.appendChild(w);
    }
    return w;
  }

  function miniOpenDownloader() {
    try {
      if (window.ModelDownloader && window.ModelDownloader.open) { window.ModelDownloader.open(); return; }
    } catch (e) { /* fallthrough */ }
    try { if (window.spaGo) window.spaGo('/redesign/ui_redesign_modelapi.html'); } catch (e2) {}
  }

  function miniBubble(title, sub, isErr) {
    var w = miniEnsure();
    var old = document.getElementById('mdlMiniBubble');
    if (old && old.parentNode) old.parentNode.removeChild(old);
    var b = document.createElement('div');
    b.className = 'mdl-bubble' + (isErr ? ' err' : '');
    b.id = 'mdlMiniBubble';
    b.innerHTML = '<span class="bi">' + icon(isErr ? 'info' : 'bars', 15) + '</span>' +
      '<span class="bt"><span class="l1">' + esc(title) + '</span><span class="l2">' + esc(sub) + '</span></span>' +
      '<button class="bgo" type="button" id="mdlMiniGo">查看</button>' +
      '<button class="bx" type="button" id="mdlMiniBx" title="关闭">' + icon('x', 13) + '</button>';
    w.insertBefore(b, w.firstChild);
    var kill = function () { clearTimeout(b._tm); if (b.parentNode) b.parentNode.removeChild(b); };
    b.querySelector('#mdlMiniGo').addEventListener('click', function () { miniOpenDownloader(); kill(); });
    b.querySelector('#mdlMiniBx').addEventListener('click', kill);
    clearTimeout(b._tm);
    b._tm = setTimeout(kill, 12000);
    /* 顺手用系统 toast 兜一条（某些场景气泡被浮层盖住时也能看到） */
    try { if (window.Backend && window.Backend.toast) window.Backend.toast(title); } catch (e) {}
  }

  function miniKeyOf(t) { return t.task_id || (t.quant_id + '@' + t.model_dir); }

  /* 统一下载浮层：模型下载小窗与表情下载浮层现在是【同一个】面板（同样式、同位置）。
     autoMini 仍按 3s 轮询 listModelDownloads，但只把任务推给 DownloadTray.upsert，
     不再自己画右上角胶囊/气泡。done/error 转场仍补一条系统 toast 通知。 */
  function miniRender(ov) {
    if (!window.DownloadTray) return;   /* 统一浮层脚本未加载：不崩、也不空转提示 */
    var tasks = (ov && ov.tasks) || [];
    var partials = (ov && ov.partials) || [];

    tasks.forEach(function (t) {
      var prev = MINI.seen[t.task_id];
      if (prev && prev !== t.status && (t.status === 'done' || t.status === 'error')) {
        try {
          if (window.Backend && window.Backend.toast)
            window.Backend.toast(t.status === 'done' ? ('本地模型下载完成 · ' + t.quant_id)
                                                     : ('本地模型下载失败 · ' + t.quant_id));
        } catch (e) {}
      }
      MINI.seen[t.task_id] = t.status;
    });
    Object.keys(MINI.seen).forEach(function (k) {
      if (!tasks.some(function (t) { return t.task_id === k; })) delete MINI.seen[k];
    });
    /* 用户删过的任务/断点，后端不再返回后才清掉隐藏记录 */
    Object.keys(MINI.dismissed).forEach(function (k) {
      var stillTask = tasks.some(function (t) { return t.task_id === k; });
      var stillPartial = partials.some(function (p) {
        return k === 'partial:' + p.quant_id + '@' + p.model_dir;
      });
      if (!stillTask && !stillPartial) delete MINI.dismissed[k];
    });

    tasks.forEach(function (t) {
      if (MINI.dismissed[t.task_id]) return;   /* 已被用户删除：不再重新出现 */
      var pct = (t.overall_pct != null ? Math.max(0, Math.min(100, t.overall_pct)) : 0);
      var spd = (t.speed_mbps ? (Number(t.speed_mbps).toFixed(1) + ' MB/s') : '');
      var eta = (t.eta_s ? ('约 ' + fmtEta(t.eta_s)) : '');
      var acts = [];
      if (t.status === 'running' && B.stopModelDownload) {
        acts.push({ label: '暂停', fn: function () {
          callSafe(B.stopModelDownload, t.task_id, true, 8000);   /* 停进程保留断点 */
        }});
      } else if (t.status === 'paused' && B.downloadModelQuant) {
        acts.push({ label: '继续', fn: function () {
          callSafe(B.downloadModelQuant, { quant_id: t.quant_id, model_dir: t.model_dir || '' }, 8000);
        }});
      }
      DownloadTray.upsert({
        id: 'mdl:' + t.task_id, kind: 'model', name: t.quant_id,
        pct: pct, status: t.status,
        current: (t.status === 'running' || t.status === 'paused') ? [spd, eta].filter(Boolean).join(' · ') : '',
        error: t.error || '',
        actions: acts,
        action: (t.status === 'done' || t.status === 'error')
          ? { label: '查看', fn: miniOpenDownloader } : null,
        onDelete: function () {
          if ((t.status === 'running' || t.status === 'paused') && B.stopModelDownload)
            try { var pr = B.stopModelDownload(t.task_id, false); if (pr && pr.catch) pr.catch(function () {}); } catch (e) {}
          MINI.dismissed[t.task_id] = true;
        }
      });
    });
    partials.forEach(function (p) {
      var pid = 'partial:' + p.quant_id + '@' + p.model_dir;
      if (MINI.dismissed[pid]) return;
      var pct = (p.pct != null ? p.pct : 0);
      DownloadTray.upsert({
        id: 'mdl:' + pid, kind: 'model',
        name: p.quant_id + ' 未下完', pct: pct, status: 'interrupted',
        current: '上次下到 ' + Math.round(pct) + '%（' + (p.partial_gb != null ? fmtGB(p.partial_gb) : '') + '）· 点击继续',
        action: { label: '继续', fn: miniOpenDownloader },
        onDelete: function () { MINI.dismissed[pid] = true; }
      });
    });
    /* 清理 tray 里已不存在的模型任务（如下完/取消后后端不再返回） */
    DownloadTray.list().forEach(function (t) {
      if (t.kind !== 'model') return;
      var still = tasks.some(function (x) { return 'mdl:' + x.task_id === t.id; }) ||
        partials.some(function (x) { return 'mdl:partial:' + x.quant_id + '@' + x.model_dir === t.id; });
      if (!still) DownloadTray.remove(t.id);
    });
  }

  function autoMini() {
    if (MINI.started) return;
    MINI.started = true;
    if (!B.listModelDownloads) return;
    if (!window.DownloadTray) return;   /* 缺统一浮层脚本则不起小窗，避免空转报错 */
    var tick = function () {
      try {
        if (B.ready && !B.ready()) { setTimeout(tick, 1500); return; }  /* 后端未就绪（预览模式）：不空转 */
      } catch (e) { /* 继续尝试 */ }
      callSafe(B.listModelDownloads, {}, 8000).then(function (r) {
        if (!r || !r.success) return;
        miniRender({ tasks: r.tasks || [], partials: r.partials || [] });
      }, function () {});
    };
    tick();
    MINI.timer = setInterval(tick, 3000);
  }

  window.ModelDownloader = {
    open: open, mountInto: mountInto, autoMini: autoMini,
    close: function () { stopPoll(); }
  };
})();
