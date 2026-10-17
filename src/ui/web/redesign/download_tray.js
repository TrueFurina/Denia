/* ============================================================
   download_tray.js  —  统一下载托盘 v2（2026-10-15）
   作用：模型下载（引导页 / 模型与API页）与表情包下载共用同一套
         下载指示器，挂在各页面顶部 header.top 的中间空区。
   两种形态：
     · 收缩态：小胶囊「下载 N ▓▓▓ 42% 展开」——显示【总百分比】，
       下载中带流光动画；点击展开。
     · 展开态：面板从 header 下方弹出，每行一个任务，各显示
       【自己的百分比】+ 进度条 + 状态 + 终态按钮。
   用法（两个入口都只跟这一套 API 打交道）：
     DownloadTray.upsert({ id, kind, name, pct, status, current, error, action, onDismiss })
     DownloadTray.remove(id)
     DownloadTray.list()            -> [task,...]
     DownloadTray.clear()
   任务对象字段：
     id       唯一字符串（模型用 'mdl:...' / 贴纸用 'sk:...'，互不冲突）
     kind     'model' | 'sticker'      仅用于左侧小图标
     name     显示名
     pct      0-100
     status   'running' | 'paused' | 'done' | 'error' | 'interrupted'
     current  进度副文本（如下载速度/当前文件名），可选
     error    失败原因，可选
     action   { label, fn } 终态时可点的按钮（如「查看」「重试」），可选
     actions  [{label, fn}, ...] 多按钮（如运行中给「暂停」、暂停时给「继续」），
              与 action 同时存在时都渲染，可选
     onDelete 用户点「删除」时回调（运行中应先停掉后端任务）；之后托盘自动
              remove 该行。未提供则删除=仅从托盘移除。
     onDismiss 用户点 × 时回调（如从 localStorage 删掉该任务），可选
   本模块只做「实时视图」，不持久化；数据由调用方驱动（模型走后端轮询、
   贴纸走 localStorage 恢复），因此每次页面加载由调用方重新 upsert 即可。
   ============================================================ */
(function () {
  if (window.DownloadTray) return;   /* 已加载：避免重复注入 */

  var tasks = {};                    /* id -> task */
  var host = null, pill = null, panel = null, bodyEl = null;
  var expanded = false;              /* 默认收缩态（小胶囊） */

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  var CSS = [
    /* ---- 宿主：占住 header.top 中间空区 ---- */
    '.dtr-host{flex:1;min-width:0;display:flex;justify-content:center;position:relative;pointer-events:none;}',
    '.dtr-host > *{pointer-events:auto;}',
    /* 无 header 可挂时的兜底：固定在顶部居中 */
    '.dtr-host.dtr-fallback{position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:9000;}',
    /* ---- 收缩态胶囊（跟随主题的设计令牌，深浅通用） ---- */
    '.dtr-pill{display:none;align-items:center;gap:8px;height:34px;padding:0 12px;border-radius:999px;',
    '  background:var(--surface-1);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);',
    '  border:1px solid var(--line-2);box-shadow:var(--shadow-pop);',
    '  color:var(--ink);font:12.5px/1 -apple-system,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;',
    '  cursor:pointer;user-select:none;white-space:nowrap;transition:transform .18s ease, box-shadow .18s ease;}',
    '.dtr-pill:hover{transform:translateY(-1px);box-shadow:var(--shadow-pop);}',
    '.dtr-pill .pt{font-weight:600;letter-spacing:.5px;}',
    '.dtr-pill .pc{min-width:18px;text-align:center;font-size:11px;font-weight:600;color:var(--ink-3);',
    '  background:color-mix(in srgb,var(--ink) 10%,transparent);border-radius:999px;padding:2px 7px;}',
    '.dtr-pill .pbar{width:64px;height:5px;border-radius:999px;background:color-mix(in srgb,var(--ink) 12%,transparent);overflow:hidden;position:relative;}',
    '.dtr-pill .pbar>i{display:block;height:100%;width:0;border-radius:999px;',
    '  background:var(--accent-grad);transition:width .45s ease;position:relative;}',
    '.dtr-pill.live .pbar>i::after{content:"";position:absolute;inset:0;border-radius:999px;',
    '  background:linear-gradient(90deg,transparent,color-mix(in srgb,var(--accent-1) 50%,transparent),transparent);',
    '  animation:dtrShimmer 1.3s linear infinite;}',
    '.dtr-pill .ppct{font-size:11.5px;font-weight:600;color:var(--accent-1);min-width:34px;text-align:right;font-variant-numeric:tabular-nums;}',
    '.dtr-pill .ptg{font-size:11px;color:var(--ink-3);}',
    '@keyframes dtrShimmer{from{transform:translateX(-100%);}to{transform:translateX(100%);}}',
    /* ---- 展开态面板：从 header 下缘弹出（跟随主题） ---- */
    '.dtr-panel{position:absolute;top:calc(100% + 12px);left:50%;transform:translateX(-50%) translateY(-6px);',
    '  width:380px;max-width:min(380px,86vw);z-index:9100;opacity:0;visibility:hidden;',
    '  transition:opacity .18s ease, transform .18s ease, visibility .18s;',
    '  background:var(--surface-1);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);',
    '  border:1px solid var(--line-2);border-radius:14px;box-shadow:var(--shadow-pop);',
    '  color:var(--ink);font:13px/1.5 -apple-system,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;',
    '  overflow:hidden;}',
    '.dtr-panel.show{opacity:1;visibility:visible;transform:translateX(-50%) translateY(0);}',
    '.dtr-head{display:flex;align-items:center;gap:8px;padding:10px 12px;cursor:pointer;',
    '  border-bottom:1px solid color-mix(in srgb,var(--ink) 8%,transparent);user-select:none;}',
    '.dtr-head .t{font-weight:600;}',
    '.dtr-head .c{font-size:11px;color:var(--ink-3);background:color-mix(in srgb,var(--ink) 10%,transparent);border-radius:999px;padding:1px 8px;}',
    '.dtr-head .spacer{flex:1;}',
    '.dtr-head .tg{color:var(--ink-3);font-size:12px;}',
    '.dtr-body{padding:8px;max-height:336px;overflow-y:auto;display:flex;flex-direction:column;gap:8px;}',
    /* 336px ≈ 同屏 4 行（每行约 78px + 间距 8px），更多任务向下滚动 */
    '.dtr-row{background:color-mix(in srgb,var(--ink) 4%,transparent);border:1px solid var(--line);border-radius:10px;padding:8px 10px;position:relative;}',
    '.dtr-top{display:flex;align-items:center;gap:7px;}',
    '.dtr-ico{width:18px;height:18px;flex:0 0 18px;border-radius:5px;display:flex;align-items:center;justify-content:center;',
    '  background:var(--accent-grad);color:var(--on-accent);font-size:11px;font-weight:700;}',
    '.dtr-name{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:500;}',
    '.dtr-chip{font-size:10px;padding:1px 7px;border-radius:999px;white-space:nowrap;}',
    '.dtr-chip.running{background:color-mix(in srgb,var(--accent-1) 16%,transparent);color:var(--accent-1);}',
    '.dtr-chip.paused{background:color-mix(in srgb,var(--warn) 18%,transparent);color:var(--warn);}',
    '.dtr-chip.done{background:color-mix(in srgb,var(--good) 18%,transparent);color:var(--good);}',
    '.dtr-chip.error{background:color-mix(in srgb,var(--bad) 18%,transparent);color:var(--bad);}',
    '.dtr-chip.interrupted{background:color-mix(in srgb,var(--ink-3) 22%,transparent);color:var(--ink-3);}',
    '.dtr-x{border:none;background:transparent;color:var(--ink-3);cursor:pointer;font-size:15px;line-height:1;padding:0 2px;}',
    '.dtr-x:hover{color:var(--ink);}',
    '.dtr-bar{height:5px;background:color-mix(in srgb,var(--ink) 10%,transparent);border-radius:999px;margin:7px 0 5px;overflow:hidden;}',
    '.dtr-bar>i{display:block;height:100%;width:0;border-radius:999px;background:var(--accent-grad);transition:width .45s ease;position:relative;}',
    '.dtr-row.live .dtr-bar>i::after{content:"";position:absolute;inset:0;border-radius:999px;',
    '  background:linear-gradient(90deg,transparent,color-mix(in srgb,var(--accent-1) 50%,transparent),transparent);',
    '  animation:dtrShimmer 1.3s linear infinite;}',
    '.dtr-meta{font-size:11px;color:var(--ink-3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:flex;justify-content:space-between;gap:8px;}',
    '.dtr-meta .mp{color:var(--accent-1);font-weight:600;font-variant-numeric:tabular-nums;flex:0 0 auto;}',
    '.dtr-act{margin-top:6px;}',
    '.dtr-act button{border:1px solid var(--line-2);background:color-mix(in srgb,var(--ink) 5%,transparent);color:var(--ink);',
    '  border-radius:8px;padding:3px 12px;font-size:12px;cursor:pointer;}',
    '.dtr-act button:hover{background:color-mix(in srgb,var(--ink) 10%,transparent);}'
  ].join('\n');

  function ensureCss() {
    if (document.getElementById('dtrCss')) return;
    var s = document.createElement('style');
    s.id = 'dtrCss';
    s.textContent = CSS;
    (document.head || document.documentElement).appendChild(s);
  }

  function mount() {
    ensureCss();
    if (host) return;

    host = document.createElement('div');
    host.className = 'dtr-host';
    pill = document.createElement('div');
    pill.className = 'dtr-pill';
    pill.innerHTML =
      '<span class="pt">下载</span><span class="pc" data-c>0</span>' +
      '<span class="pbar"><i data-pbar></i></span>' +
      '<span class="ppct" data-pct>0%</span>' +
      '<span class="ptg" data-tg>展开</span>';
    panel = document.createElement('div');
    panel.className = 'dtr-panel';
    panel.innerHTML =
      '<div class="dtr-head"><span class="t">下载</span><span class="c" data-c>0</span>' +
      '<span class="spacer"></span><span class="tg" data-tg>收起</span></div>' +
      '<div class="dtr-body" data-body></div>';
    host.appendChild(pill);
    host.appendChild(panel);
    bodyEl = panel.querySelector('[data-body]');

    /* 挂载点：优先注入当前页 header.top 的中间空区（标题与右侧按钮之间）；
       找不到（极少数独立页）再退回固定顶部居中。 */
    var hd = document.querySelector('.app > .main > header.top') ||
             document.querySelector('section.main > header.top') ||
             document.querySelector('header.top');
    if (hd) {
      var right = hd.querySelector('.right');
      if (right) hd.insertBefore(host, right); else hd.appendChild(host);
    } else {
      host.classList.add('dtr-fallback');
      document.body.appendChild(host);
    }

    pill.addEventListener('click', function () { setExpanded(!expanded); });
    panel.querySelector('.dtr-head').addEventListener('click', function (e) {
      if (e.target.closest('[data-tg]')) setExpanded(false);
    });
    applyExpanded();
  }

  function setExpanded(v) {
    expanded = !!v;
    applyExpanded();
    render();   /* 立即重建行内容：不能等下一次轮询，否则点开瞬间面板是空的 */
  }

  function applyExpanded() {
    if (!pill || !panel) return;
    pill.style.display = expanded ? 'none' : 'flex';
    panel.classList.toggle('show', expanded);
    pill.querySelector('[data-tg]').textContent = expanded ? '收起' : '展开';
  }

  var STATUS_TXT = { running: '下载中', paused: '已暂停', done: '完成', error: '失败', interrupted: '中断' };
  var KIND_ICON = { model: '模', sticker: '图' };

  /* 总百分比：所有任务百分比的平均值（完成的算 100%） */
  function overallPct(ids) {
    if (!ids.length) return 0;
    var sum = 0;
    ids.forEach(function (k) { sum += Math.max(0, Math.min(100, tasks[k].pct || 0)); });
    return Math.round(sum / ids.length);
  }

  function render() {
    if (!bodyEl) return;
    var ids = Object.keys(tasks);
    var cntEls = [pill.querySelector('[data-c]'), panel.querySelector('[data-c]')];
    if (!ids.length) {
      cntEls.forEach(function (el) { if (el) el.textContent = '0'; });
      host.style.display = 'none';
      return;
    }
    host.style.display = '';

    var order = { running: 0, paused: 1, interrupted: 2, error: 3, done: 4 };
    ids.sort(function (a, b) { return (order[tasks[a].status] || 9) - (order[tasks[b].status] || 9); });

    /* ---- 收缩态胶囊：任务数 + 总百分比 + 动画条 ---- */
    var total = overallPct(ids);
    var anyLive = ids.some(function (k) {
      var s = tasks[k].status; return s === 'running' || s === 'paused';
    });
    cntEls.forEach(function (el) { if (el) el.textContent = String(ids.length); });
    pill.querySelector('[data-pbar]').style.width = total + '%';
    pill.querySelector('[data-pct]').textContent = total + '%';
    pill.classList.toggle('live', anyLive);
    pill.querySelector('[data-tg]').textContent = expanded ? '收起' : '展开';

    if (!expanded) { panel.classList.remove('show'); return; }

    /* ---- 展开态面板：每行各自百分比 ---- */
    bodyEl.innerHTML = ids.map(function (k) {
      var t = tasks[k];
      var st = t.status || 'running';
      var pct = Math.max(0, Math.min(100, Math.round(t.pct || 0)));
      var chip = '<span class="dtr-chip ' + st + '">' + (STATUS_TXT[st] || st) + '</span>';
      var meta = esc(t.current || t.error || '');
      /* 操作按钮：actions 数组（暂停/继续等多按钮）+ 旧版单 action + 内置删除 */
      var btns = [];
      (t.actions || []).forEach(function (a, i) {
        if (a && a.label && a.fn) btns.push({ label: a.label, fn: a.fn, idx: i });
      });
      if (t.action && t.action.label && t.action.fn) btns.push({ label: t.action.label, fn: t.action.fn, idx: -1 });
      var act = '<div class="dtr-act">' +
        btns.map(function (b) {
          return '<button type="button" data-act="' + esc(k) + '" data-i="' + b.idx + '">' + esc(b.label) + '</button>';
        }).join('') +
        '<button type="button" class="danger" data-del="' + esc(k) + '" title="停止并移除该任务">删除</button>' +
        '</div>';
      var ico = KIND_ICON[t.kind] || '↓';
      var live = (st === 'running' || st === 'paused') ? ' live' : '';
      return '<div class="dtr-row' + live + '" data-row="' + esc(k) + '">' +
        '<div class="dtr-top"><span class="dtr-ico">' + esc(ico) + '</span>' +
        '<span class="dtr-name" title="' + esc(t.name || '') + '">' + esc(t.name || '下载') + '</span>' +
        chip + '<button class="dtr-x" type="button" data-x="' + esc(k) + '" title="隐藏">×</button></div>' +
        '<div class="dtr-bar"><i style="width:' + pct + '%"></i></div>' +
        '<div class="dtr-meta"><span>' + meta + '</span><span class="mp">' + pct + '%</span></div>' + act +
        '</div>';
    }).join('');

    bodyEl.querySelectorAll('[data-x]').forEach(function (b) {
      b.addEventListener('click', function (e) {
        e.stopPropagation();
        var id = b.getAttribute('data-x');
        var t = tasks[id];
        if (t && t.onDismiss) t.onDismiss();
        remove(id);
      });
    });
    bodyEl.querySelectorAll('[data-act]').forEach(function (b) {
      b.addEventListener('click', function (e) {
        e.stopPropagation();
        var t = tasks[b.getAttribute('data-act')];
        if (!t) return;
        var i = parseInt(b.getAttribute('data-i'), 10);
        var fn = (i >= 0 && t.actions && t.actions[i]) ? t.actions[i].fn
                                                       : (t.action && t.action.fn);
        if (fn) fn();
      });
    });
    bodyEl.querySelectorAll('[data-del]').forEach(function (b) {
      b.addEventListener('click', function (e) {
        e.stopPropagation();
        var id = b.getAttribute('data-del');
        var t = tasks[id];
        if (t && t.onDelete) { try { t.onDelete(t); } catch (err) {} }
        remove(id);
      });
    });
  }

  function upsert(task) {
    if (!task || !task.id) return;
    var merged = tasks[task.id] || {};
    for (var key in task) if (Object.prototype.hasOwnProperty.call(task, key)) merged[key] = task[key];
    tasks[task.id] = merged;
    mount();
    render();
  }

  function remove(id) {
    if (tasks[id]) { delete tasks[id]; render(); }
  }

  function list() {
    return Object.keys(tasks).map(function (k) { return tasks[k]; });
  }

  function clear() {
    tasks = {};
    if (host) host.style.display = 'none';
    if (bodyEl) bodyEl.innerHTML = '';
  }

  window.DownloadTray = {
    upsert: upsert, remove: remove, list: list, clear: clear,
    expand: function () { setExpanded(true); },
    collapse: function () { setExpanded(false); }
  };
})();
