/* ui_confirm.js —— 全站统一的「危险操作确认」弹窗（替代原生 window.confirm）
 *
 * 为什么要换：
 *   原生 confirm 是系统灰白框，与暗色界面割裂、阻塞 JS、无法定制文案层级，
 *   且在 pywebview 里观感尤其突兀。本模块沿用 index.html 里已有的
 *   agentConfirmModal 设计（同一套令牌/圆角/按钮），抽成共享组件。
 *
 * 用法：
 *   uiConfirm('确定删除「达妮娅」？', { danger: true }).then(ok => { if (!ok) return; ... });
 *
 * 契约（重要，改动前先读）：
 *   · Promise 必然 resolve，返回 true=确认 / false=取消。
 *   · fail-closed：DOM 未就绪 ⇒ 立即 resolve(false)（宁可不做，不可误删）。
 *   · 60 秒无操作自动按「取消」，杜绝弹窗悬挂导致后续点击全部失效。
 *   · Esc / 点遮罩 / 取消按钮 都算「取消」，三者走同一个 settle()。
 *   · 同一时刻只弹一个：重复调用时旧的先按「取消」结算。
 */
(function (global) {
  'use strict';

  var MODAL_ID = 'uiConfirmModal';
  var AUTO_MS = 60000;
  var _cb = null;        // 当前待结算的 resolve
  var _timer = null;
  var _built = false;
  var _escHandler = null;

  function hide() {
    var root = document.getElementById(MODAL_ID);
    if (root) root.style.display = 'none';
  }

  function settle(ok) {
    var cb = _cb;
    _cb = null;
    if (_timer) { clearTimeout(_timer); _timer = null; }
    if (_escHandler) {
      document.removeEventListener('keydown', _escHandler);
      _escHandler = null;
    }
    hide();
    if (cb) cb(!!ok);
  }

  function onKey(e) {
    if (e.key === 'Escape' || e.key === 'Esc') settle(false);
  }

  function ensureDom() {
    if (_built && document.getElementById(MODAL_ID)) return true;
    if (!document.body) return false;                 // body 未就绪 → fail-closed

    var root = document.getElementById(MODAL_ID);
    if (!root) {
      root = document.createElement('div');
      root.id = MODAL_ID;
      root.setAttribute('role', 'dialog');
      root.setAttribute('aria-modal', 'true');
      root.style.cssText =
        'display:none;position:fixed;inset:0;z-index:10050;' +
        'align-items:center;justify-content:center;' +
        'background:var(--mask-strong,rgba(8,10,16,.72));' +
        'backdrop-filter:blur(3px);-webkit-backdrop-filter:blur(3px)';
      root.innerHTML =
        '<div style="width:min(420px,86vw);background:var(--surface-1);' +
        'border:1px solid var(--line);border-radius:var(--r-lg,14px);' +
        'box-shadow:var(--shadow-pop);padding:22px 22px 18px;color:var(--ink);' +
        'font-size:14px;line-height:1.6;font-family:inherit">' +
          '<div data-role="title" style="font-weight:700;font-size:15px;' +
          'margin-bottom:10px;color:var(--ink)">请确认</div>' +
          '<div data-role="msg" style="white-space:pre-wrap;word-break:break-word;' +
          'background:var(--surface-2);border:1px solid var(--line);border-radius:10px;' +
          'padding:12px 14px;margin-bottom:18px;color:var(--ink-2)"></div>' +
          '<div data-role="btns" style="display:flex;gap:10px;justify-content:flex-end">' +
            '<button type="button" data-role="no" style="flex:1;padding:10px 0;border:none;' +
            'border-radius:10px;background:var(--surface-3);color:var(--ink);font-size:14px;' +
            'cursor:pointer">取消</button>' +
            '<button type="button" data-role="yes" style="flex:1;padding:10px 0;border:none;' +
            'border-radius:10px;background:var(--accent-1,#2DD4FF);color:var(--on-accent,#04121a);' +
            'font-weight:700;font-size:14px;cursor:pointer">确定</button>' +
          '</div>' +
          '<div data-role="timeout" style="margin-top:10px;text-align:right;font-size:11px;' +
          'color:var(--ink-3)">' + Math.round(AUTO_MS / 1000) + ' 秒未操作将自动取消</div>' +
        '</div>';
      document.body.appendChild(root);

      root.addEventListener('click', function (e) {
        if (e.target === root) settle(false);           // 点遮罩 = 取消
      });
      root.querySelector('[data-role="no"]').onclick = function () { settle(false); };
      root.querySelector('[data-role="yes"]').onclick = function () { settle(true); };
    }
    _built = true;
    return true;
  }

  function uiConfirm(message, opts) {
    opts = opts || {};
    if (!ensureDom()) return Promise.resolve(false);     // fail-closed
    if (_cb) settle(false);                              // 已有弹窗 → 旧的按取消结算

    var root = document.getElementById(MODAL_ID);
    root.querySelector('[data-role="msg"]').textContent = message || '确定要继续吗？';
    root.querySelector('[data-role="title"]').textContent =
      opts.title || (opts.danger ? '危险操作' : '请确认');
    var yes = root.querySelector('[data-role="yes"]');
    yes.textContent = opts.okText || '确定';
    yes.style.background = opts.danger
      ? 'var(--bad,#E5484D)'
      : 'var(--accent-1,#2DD4FF)';
    var no = root.querySelector('[data-role="no"]');
    if (no) no.style.display = '';
    var to = root.querySelector('[data-role="timeout"]');
    if (to) to.style.display = '';
    var btns = root.querySelector('[data-role="btns"]');
    if (btns) btns.style.justifyContent = 'flex-end';

    root.style.display = 'flex';

    if (_escHandler) document.removeEventListener('keydown', _escHandler);
    _escHandler = onKey;
    document.addEventListener('keydown', _escHandler);

    if (_timer) clearTimeout(_timer);
    _timer = setTimeout(function () { settle(false); }, AUTO_MS);

    /* ⚠️ _cb 必须在 Promise 构造器【内部】赋值：若先置 null 再 return，
       用户点按钮时 _cb 仍是 null ⇒ 无人结算 ⇒ Promise 永久悬挂（闸门抓到过）。
       同时把 cancel 时的监听解绑挂进 settle，保证每条路径都清理干净。 */
    return new Promise(function (resolve) { _cb = resolve; });
  }

  /* 单按钮「知道了」告知弹窗：用于门控拦截后的明确指引（不询问，只告知）。
     与 uiConfirm 共用同一套 DOM，但隐藏取消按钮、不显示超时提示。 */
  function uiAlert(message, opts) {
    opts = opts || {};
    if (!ensureDom()) return Promise.resolve(false);
    if (_cb) settle(false);

    var root = document.getElementById(MODAL_ID);
    root.querySelector('[data-role="msg"]').textContent = message || '请确认';
    root.querySelector('[data-role="title"]').textContent = opts.title || '提示';
    var yes = root.querySelector('[data-role="yes"]');
    yes.textContent = opts.okText || '知道了';
    yes.style.background = 'var(--accent-1,#2DD4FF)';
    var no = root.querySelector('[data-role="no"]');
    if (no) no.style.display = 'none';
    var to = root.querySelector('[data-role="timeout"]');
    if (to) to.style.display = 'none';
    var btns = root.querySelector('[data-role="btns"]');
    if (btns) btns.style.justifyContent = 'center';

    root.style.display = 'flex';

    if (_escHandler) document.removeEventListener('keydown', _escHandler);
    _escHandler = onKey;
    document.addEventListener('keydown', _escHandler);

    if (_timer) clearTimeout(_timer);
    _timer = setTimeout(function () { settle(false); }, AUTO_MS);

    return new Promise(function (resolve) { _cb = resolve; });
  }

  global.uiConfirm = uiConfirm;
  global.uiAlert = uiAlert;
})(window);
