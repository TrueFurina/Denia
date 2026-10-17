// theme_backend_bridge.js — 主题编辑器专用的轻量后端桥。
//
// 为什么不用 app_backend.js：它 2000+ 行、启动就拉起聊天/桌宠/看屏整条链路，
// 一个纯编辑器页不该为此付代价。这里只照抄 app_backend.js 里 Backend 的
// 「取 api → 等就绪 → 调用 → 降级」四件事，多一个方法都不带。
//
// 与 app_backend.js 保持一致的两条关键行为：
//   ① pywebview 只把 api 注入**顶层**窗口，iframe 里的子窗口恒为空 ⇒ 要回落到 window.parent；
//   ② 后端不可用时返回 { success:false, offline:true }，**绝不抛错**（否则整页白屏）。
(function () {
  function api() {
    try {
      if (window.parent && window.parent !== window && window.parent.pywebview && window.parent.pywebview.api) {
        return window.parent.pywebview.api;
      }
      return (window.pywebview && window.pywebview.api) || null;
    } catch (e) { return null; }
  }

  var _readyPromise = null;
  function whenReady(timeoutMs) {
    if (api()) return Promise.resolve(true);
    if (_readyPromise) return _readyPromise;
    _readyPromise = new Promise(function (resolve) {
      var t0 = Date.now();
      var tick = function () {
        if (api()) { _readyPromise = null; resolve(true); }
        else if (Date.now() - t0 > (timeoutMs || 8000)) { _readyPromise = null; resolve(false); }
        else setTimeout(tick, 120);
      };
      tick();
    });
    return _readyPromise;
  }

  /* ⚠️ payload 为 undefined 时**必须不传参**，不能传 {}。
     后端方法签名有两种：`t_list(self, data=None)` 吃 dict，
     而 `get_characters(self)` / `get_runtime_stats(self)` **不收任何参数** ——
     给它塞一个 dict，pywebview 直接把参数带过去 ⇒ TypeError
     ⇒ 被下面的 catch 吞成 {success:false} ⇒ 角色列表永远空。
     症状极具迷惑性：主题读得到（t_list 吃参数）、只有"角色"这一项空，
     界面上只剩一个「暂不绑定」，看着像"只有一个角色"。 */
  function call(method, payload) {
    return whenReady(8000).then(function (ok) {
      var a = api();
      if (!ok || !a || typeof a[method] !== 'function') {
        return { success: false, offline: true, error: 'backend_unavailable:' + method };
      }
      var invoke = payload === undefined
        ? function () { return a[method](); }
        : function () { return a[method](payload); };
      return Promise.resolve(invoke()).then(function (r) {
        if (r && typeof r === 'object') return r;
        return { success: true, result: r };
      }).catch(function (e) {
        var msg = (e && (e.message || e.error)) || String(e);
        console.warn('[themeBackend] ' + method + ' 失败:', msg);
        return { success: false, error: msg };
      });
    });
  }

  function ready() { return !!api(); }

  function toast(msg, kind) {
    try {
      var box = document.getElementById('tv5Tip');
      if (!box) return;
      box.textContent = msg;
      box.className = 'tv5-tip show' + (kind === 'err' ? ' err' : '');
      clearTimeout(toast._t);
      toast._t = setTimeout(function () { box.className = 'tv5-tip'; }, 2600);
    } catch (e) {}
  }

  window.ThemeB = {
    ready: ready,
    call: call,
    toast: toast,
    tList: function () { return call('t_list'); },
    tSave: function (d) { return call('t_save', d); },
    tDelete: function (d) { return call('t_delete', d); },
    tRename: function (d) { return call('t_rename', d); },
    tBind: function (d) { return call('t_bind', d); },
    tSlotFiles: function (d) { return call('t_slot_files', d); },
    tPick: function (d) { return call('t_pick', d); },
    /* AI 主题助手：几何体检 + AI 配色（失败自动降级本地，绝不抛错） */
    tThemeAi: function (d) { return call('t_theme_ai', d); },
    /* 贴纸 / 表情包（2026-10-12）：拖入文件走 base64 落盘；移除时清文件 */
    tStickerDrop: function (d) { return call('t_sticker_drop', d); },
    tStickerDel: function (d) { return call('t_sticker_del', d); },
    /* 无参方法：不能塞 {}，见上面 call() 的注释 */
    getCharacters: function () { return call('get_characters'); }
  };
})();
