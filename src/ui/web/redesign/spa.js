/* ============================================================
   SPA-lite 路由（独立普通脚本，不依赖任何 ES module 加载成败）。

   设计要点：
   - 外壳 index.html 只在「软件启动」时整页加载一次，含 .side（左栏）/
     .main（中栏聊天）/ .stage（右栏 Live2D + 数据面板）。
   - 侧栏 / 内部链接点击只替换中间 .main，左栏与右栏永不被整页重载 →
     右栏 Live2D 与数据面板始终常驻、「左/中/右」布局稳定。
   - 首启引导浮层(#onboardingOverlay)是外壳里的顶层浮层，只在启动判定一次；
     导航不整页重载外壳 → 回到聊天页永远不会再弹引导页。
   - 子页 HTML 通过 /_spa/<启动时间戳>/<相对文件> 拉取：时间戳放在「路径」
     里使 WebView2 缓存键必变，彻底绕开其对 fetch 磁盘缓存的顽固保留，
     保证每次切栏都拿到最新子页（不再有「同文件新旧两套 UI」）。
   - 本脚本是普通 <script>（非 module）：即便 morphicons 模块加载失败，
     路由依然生效，导航与常驻布局不受影响。
   ============================================================ */
(function () {
  'use strict';

  /* 外壳标记：本文件只被 index.html 引入。子页被 SPA 注入执行其内联脚本时，
     靠这个标记让「外壳守门」脚本直接 return，避免误判成整页加载而跳走。 */
  window.__SPA_SHELL = true;

  function normPath(p) {
    p = (p || '').split('?')[0].split('#')[0];
    if (p.endsWith('/')) p = p.slice(0, -1);
    if (p.endsWith('/index.html')) p = p.slice(0, -10);
    if (p === '') p = '/';
    return p;
  }

  var spaBusy = false;
  var _chatMain = null;   // 持久保留的聊天 .main：切走时不销毁，保留事件监听与轮询 timer

  /* ============ 切页预取（2026-10-08 治"点一下要等 0.2s 才进"）============
     实测：切页本身的 DOM 替换只要 24~94ms，但那段时间**包含一次 fetch 往返**
     （/_spa/<ts>/<file>，服务端要读文件 + 重写 ?v=）。鼠标移到菜单上到真正点击
     通常有几百毫秒，足够把目标页 HTML 先取回来 —— 点击时直接用缓存，只剩替换 DOM。
     缓存 30s 过期（避免拿到跨轮次的旧片段）；点首页(/)不预取（那是常驻节点）。 */
  var _prefetch = {};      // href -> {html, ts}
  var PREFETCH_TTL_MS = 30000;

  function _fragUrl(clean) {
    return '/_spa/' + Date.now() + '/' + clean.replace(/^\//, '')
      + '?_n=' + Math.random().toString(36).slice(2, 10);
  }

  function prefetchHref(href) {
    try {
      if (!href || href.charAt(0) !== '/' || href.charAt(0) === '#') return;
      if (href.indexOf('#') === 0 || href.indexOf('http') === 0) return;
      var clean = href.split('#')[0];
      if (normPath(clean) === '/') return;
      var hit = _prefetch[clean];
      if (hit && (Date.now() - hit.ts) < PREFETCH_TTL_MS) return;
      var url = _fragUrl(clean);
      fetch(url, { cache: 'no-store' }).then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.text();
      }).then(function (t) {
        _prefetch[clean] = { html: t, ts: Date.now() };
      }).catch(function () { /* 预取失败无所谓，点击时照常拉 */ });
    } catch (_) { /* ignore */ }
  }

  /* 启动即缓存聊天 .main（此时文档是 index.html 外壳，.app > .main 即聊天区）。
     之后切到子页再切回，直接复用这个未被销毁的节点 —— 监听与 window.__chatTimer
     全在，消息持续从后端轮询，回来即最新，绝不丢记录、绝不重复绑定导致发两遍。 */
  function cacheChatMain() {
    if (!_chatMain && document.querySelector('#scroll')) {
      _chatMain = document.querySelector('.app > .main');
    }
  }

  async function spaLoad(href, push) {
    if (spaBusy) return;
    /* P4 通话中门禁：用户拍板「设置以外能进，设置页通话中禁入」。
       通话进行中（含已最小化）点设置 → 不切栏、琥珀 toast 给「为什么+怎么解」，
       设置项照常显示可点（不 display:none/disabled），符合项目铁律。 */
    if (window.__isCallActive && window.__isCallActive()) {
      var _np = normPath(href);
      if (_np === '/redesign/ui_redesign_settings.html') {
        if (window.Backend && window.Backend.toast) {
          try { window.Backend.toast('通话中无法打开设置，挂断后再试', 'warn'); } catch (_) {}
        }
        return;
      }
    }
    spaBusy = true;
    /* 【2026-10-08 切页卡顿修复】切栏期间置 __SPA_NAV：
       各子页结尾都有一段 gsap.from(..., {opacity:0, delay:.28, stagger:.08, duration:.5})
       的"逐卡淡入"入场动画（首屏很好看），但 SPA 切页时**每切一次都重播** —— 内容先透明、
       再一张张冒出来，最后一张要 ~1s 以上才到不透明，用户观感就是"每次跳转卡顿 1 秒多"。
       语义：入场动画只属于"首次进入该页"（整页加载）的仪式感，切栏应当即时可见。
       子页统一判 `!window.__SPA_NAV` 跳过入场；本标志在切页时置位、~1.2s 后自动复位
       （gasp 动画块都在内联脚本末尾同步执行，1.2s 足够覆盖异步脚本链）。 */
    window.__SPA_NAV = true;
    setTimeout(function () { window.__SPA_NAV = false; }, 1200);
    try {
      /* 【SPA 浮层清理】子页脚本常把弹卡/浮层 append 到 document.body，而 SPA 只替换
         .app > .main，这些节点无人移除 → 切走该页后仍以「幽灵浮层」留在界面上
         （曾表现为左上角多出一个迷你主页、悬停预览卡切页不消）。约定：子页给此类
         节点加 data-spa-transient，这里每次导航前统一清掉。 */
      Array.prototype.forEach.call(document.querySelectorAll('[data-spa-transient]'), function (el) {
        try { el.remove(); } catch (_) {}
      });
      var isHome = normPath(href) === '/';
      var oldMain = document.querySelector('.app > .main') || document.querySelector('.main');
      if (!oldMain) throw new Error('当前页面无 .main 容器');

      /* 【2026-10-13 页面切换骨架屏 v3 · 三段式】
         v1 教训：MIN_MS=700 硬等 → 所有页面冤死。
         v2 教训：零延时 → 页面 5~20ms 换完，扫光一闪而过看不见。
         v3（YouTube/Facebook 同款思路）：
           phase-1（fetch 期间）：把 .main 换成骨架 main —— 卡顿期不白屏
           phase-2（真身挂载）：真身直接顶掉骨架 main，**同时**在真身上铺一层
                  同形骨架盖住内容（层 pointer-events:none，期间页面可正常点击！）
           phase-3（浮现）：停留 clamp(minHold−已耗时, 0, minHold)=450ms 保证扫光
                  看得见，然后骨架淡出、真实块按 60ms 错峰浮现（淡入+上移8px）
         双保险（结构上不可能再「冤死」）：
           · 骨架层永远 pointer-events:none，等待期间页面照样可点
           · 2.5s 强制放行定时器；紧急出口 Skeleton.releaseAll()
         旋钮：Skeleton.minHold = 0 即回到「零延时」；调大更明显。 */
      var _navStart = (window.performance && performance.now()) || Date.now();
      var _skPage = (function (h) {
        if (h.indexOf('ui_redesign_daily') >= 0) return 'daily';
        if (h.indexOf('ui_redesign_calls') >= 0) return 'calls';
        if (h.indexOf('ui_redesign_characters') >= 0) return 'chars';
        if (h.indexOf('ui_redesign_memory') >= 0) return 'memory';
        if (h.indexOf('ui_redesign_stickers') >= 0) return 'stickers';
        if (h.indexOf('ui_redesign_settings') >= 0) return 'settings';
        if (h.indexOf('ui_redesign_software_profiles') >= 0) return 'profiles';
        if (h.indexOf('ui_redesign_developer') >= 0) return 'dev';
        if (h.indexOf('lib.html') >= 0) return 'lib';
        return 'generic';
      })(href);
      try {
        if (!isHome && window.Skeleton) {
          var _skMain = document.createElement('main');
          _skMain.className = 'main';
          Skeleton.show(_skMain, _skPage, [], _navStart);
          oldMain.replaceWith(_skMain);     // phase-1：立即换骨架（同步，无等待）
        }
      } catch (_) { /* 骨架失败不影响导航本身 */ }


      var newMain;
      if (isHome) {
        /* 首页（/index.html）不在 redesign/ 下，绝不能走 /_spa/（会 404），
           否则点「对话」回聊天页时 fetch 404 → 抛异常 → 卡死回不去。
           直接复用启动即缓存的聊天 .main（从未销毁）→ 监听/timer 全在，
           消息也持续从后端轮询，切回来即是最新，绝不会丢记录也不会重绑导致双发。 */
        if (!_chatMain) { cacheChatMain(); }
        if (!_chatMain) {
          /* 绝不再整页跳转：那样加载的是「无 spa.js、无右栏 Live2D 舞台、侧栏还是
             该文件里旧副本」的子页独立文档 —— 也就是用户看到的「卡回老页面」，
             而且此后所有点击都变成整页跳转、再也回不到外壳。这里原地提示即可。 */
          if (window.Backend && window.Backend.toast) {
            try { window.Backend.toast('页面加载失败，请重试', 'err'); } catch (_) {}
          }
          return;
        }
        newMain = _chatMain;
      } else {
        /* 子页：路径级时间戳接口 /_spa/<ts>/<file>：WebView2 的 fetch 磁盘缓存无视
           no-store，且对 ?v= 令牌式缓存键仍可能命中旧版；把时间戳放进「路径」使缓存键
           必变，每次切栏都拉最新页面，彻底消灭「同一份文件新旧两套 UI」的分裂。
           兜底：/\_spa/ 不可用时（如旧服务进程未重启）直连静态文件 /<file>，服务端对
           .html 同样做 ?v 重写 + no-store，仍能拿到最新版，绝不让切栏彻底失败。 */
        var clean = href.split('#')[0];
        var html = null;
        /* 先吃预取缓存（鼠标悬停时已拉过）——命中就省掉整次 fetch 往返 */
        var _hit = _prefetch[clean];
        if (_hit && (Date.now() - _hit.ts) < PREFETCH_TTL_MS) {
          html = _hit.html;
          delete _prefetch[clean];      // 用掉即作废，避免同页连点拿到陈旧片段
        }
        if (html == null) {
          var ts = Date.now();
          var nonce = Math.random().toString(36).slice(2, 10);
          var fragUrl = '/_spa/' + ts + '/' + clean.replace(/^\//, '') + '?_n=' + nonce;
          try {
            var res = await fetch(fragUrl, {
              cache: 'no-store',
              headers: {
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Pragma': 'no-cache'
              }
            });
            if (res.ok) html = await res.text();
          } catch (_) { /* 忽略，走兜底 */ }
        }
        if (html == null) {
          var res2 = await fetch(clean + '?_cb=' + Date.now() + '&_n=' + Math.random().toString(36).slice(2, 10), {
            cache: 'no-store',
            headers: { 'Cache-Control': 'no-cache, no-store', 'Pragma': 'no-cache' }
          });
          if (!res2.ok) throw new Error('HTTP ' + res2.status);
          html = await res2.text();
        }
        var doc = new DOMParser().parseFromString(html, 'text/html');

        /* 新鲜度校验：若目标页是已知页面但返回内容不含新版特征串，
           说明 WebView2 磁盘缓存命中了旧版 → 带新时间戳重试一次。
           特征串取各页独有的 UI 文字（不取版本号/时间戳，那些会被服务端重写）。 */
        var _fresh = true;
        if ((href.indexOf('knowledge') >= 0 || href.indexOf('kb.html') >= 0) && html.indexOf('了解教学') < 0) _fresh = false;
        if (!_fresh) {
          console.warn('[spa] 检测到可能缓存的旧版内容，重新拉取...');
          try {
            var retryUrl = '/_spa/' + Date.now() + '/' + clean.replace(/^\//, '') + '?_r=1&_n=' + Math.random().toString(36).slice(2, 10);
            var rr = await fetch(retryUrl, { cache: 'no-store', headers: { 'Cache-Control':'no-store','Pragma':'no-cache' } });
            if (rr.ok) {
              var rhtml = await rr.text();
              if (((href.indexOf('knowledge') >= 0 || href.indexOf('kb.html') >= 0) && rhtml.indexOf('了解教学') >= 0) || (href.indexOf('knowledge') < 0 && href.indexOf('kb.html') < 0)) {
                html = rhtml; doc = new DOMParser().parseFromString(html, 'text/html');
                console.log('[spa] 重试成功，拿到新版内容');
              }
            }
          } catch (_) { /* 重试失败就用上一次的结果 */ }
        }

        /* 把目标页 <head> 的内联 <style> 与 <link> 注入外壳：子页样式写在各自 <head> 内联
           （如知识库 .kb-*）。按文本/URL 去重，避免反复切栏重复注入。 */
        /* 样式差量替换：SPA 只把子页 <head> 的 <style>/<link> 注入外壳且从不移除，
           多切几栏后十几份页面 CSS 会同时生效、同名选择器（.card/.row/.btn …）互相覆盖，
           表现成「切个页面外观就变成另一页的旧样子」。这里先摘掉上一页遗留、本页不再
           需要的样式；两者都用的（如 ui_shared.css）保留原节点，避免无谓闪一下。 */
        var _newStyles = [];
        /* 2026-09-29 兜底：不限于 head——曾有人把 <style> 写在 body 尾部
           （Part B 能力概览/横幅/额度卡），SPA 只搬 head 样式导致应用内全裸奔、
           独立打开却正常。head+body 一起收集，按文本去重逻辑不变。 */
        doc.querySelectorAll('style').forEach(function (st) {
          var _t = st.textContent;
          if (_t && _t.trim()) _newStyles.push(_t);
        });
        var _newLinks = [];
        doc.querySelectorAll('head link[rel="stylesheet"]').forEach(function (ln) {
          var _h = ln.getAttribute('href');
          if (_h) _newLinks.push(_h);
        });
        Array.prototype.forEach.call(document.querySelectorAll('head [data-spa-style]'), function (el) {
          try {
            if (el.tagName === 'STYLE') { if (_newStyles.indexOf(el.textContent) < 0) el.remove(); }
            else { if (_newLinks.indexOf(el.getAttribute('href') || '') < 0) el.remove(); }
          } catch (_) {}
        });
        doc.querySelectorAll('style').forEach(function (st) {
          var txt = st.textContent;
          if (!txt || !txt.trim()) return;
          var dup = Array.prototype.some.call(document.querySelectorAll('head style'), function (s) {
            return s.textContent === txt;
          });
          if (dup) return;
          var el = document.createElement('style');
          el.textContent = txt; el.dataset.spaStyle = '1';
          document.head.appendChild(el);
        });
        doc.querySelectorAll('head link[rel="stylesheet"]').forEach(function (ln) {
          var h = ln.getAttribute('href');
          if (!h) return;
          var dup = Array.prototype.some.call(document.querySelectorAll('head link[rel="stylesheet"]'), function (l) {
            return (l.getAttribute('href') || '') === h;
          });
          if (dup) return;
          var el = document.createElement('link');
          el.rel = 'stylesheet'; el.href = h; el.dataset.spaStyle = '1';
          document.head.appendChild(el);
        });

        newMain = doc.querySelector('.app > .main') || doc.querySelector('.main');
        if (!newMain) throw new Error('目标页无 .main 内容');
      }

      /* ⚠️【2026-10-13 修·所有页面冤死的真因】
         换真身时必须替换「**当前真正挂在 DOM 里**的那个 .main」。
         v1 的 bug：oldMain 是在导航开头抓的引用；随后骨架 main 把它顶掉，
         oldMain 就成了**游离节点** —— 对游离节点调 replaceWith() 什么都不发生，
         于是骨架 main 永远留在页面上：它带着 pointer-events:none 的骨架层和空灰块
         ⇒ 每个分栏看起来都是「死的」、点哪都没反应（开发者截图实证）。
         修法：每次都重新 querySelector 取现挂的节点。 */
      var _cur = document.querySelector('.app > .main') || document.querySelector('.main');
      if (_cur && _cur !== newMain) _cur.replaceWith(newMain);
      if (newMain && newMain.scrollTop !== undefined) newMain.scrollTop = 0;
      window.scrollTo(0, 0);

      /* 【骨架屏 v3 · phase-2/3】真身已挂载。
         phase-2：在**真身**上铺一层同形骨架盖住内容 —— 层 pointer-events:none，
                   内容已经在下面了，等待期间页面**可以正常点击**（与 v1 的"冤死"
                   有本质区别：那是内容根本没挂上）。
         phase-3：reveal() 按 clamp(minHold−已耗时,0,minHold)=450ms 停留后淡出骨架、
                   真实块按 60ms 错峰浮现 —— 扫光效果肉眼可见，又不会拖慢慢页面。
         兜底：2.5s 强制放行；紧急出口 Skeleton.releaseAll()。 */
      try {
        if (window.Skeleton && !isHome) {
          Skeleton.show(newMain, _skPage, [], _navStart);
          Skeleton.reveal(newMain, 60);
        }
      } catch (_) {}

      /* 子页注入后重新挂载图标库：外壳只在「启动」挂载过一次，SPA 注入的新内容里的
         <i data-icon> 占位不会被自动转换 → 一直是空。这里对 newMain 幂等重挂
         （DIcon.mount 内置 data-icon-done 守卫，重复跑无副作用）。首页(newMain=缓存的
         聊天 .main) 启动已挂载过，再挂一次也无碍。 */
      try { if (window.DIcon && window.DIcon.mount) window.DIcon.mount(newMain); } catch (_) {}

      /* 右栏显隐：由路由统一控制、每次切栏都重置，避免子页 <style> 泄漏到常驻外壳。
         对话页(首页)永远显示右栏；子页若声明 <meta name="stage" content="off"> 则隐藏。
         这样「点知识库隐藏右栏」不会串味到对话页（之前是因为子页 .app{} 全局样式被注入外壳 head 残留）。 */
      var appEl = document.getElementById('app') || document.querySelector('.app');
      if (appEl){
        if (isHome){
          appEl.classList.remove('no-stage');
        } else {
          var stageMeta = doc.querySelector('meta[name="stage"]');
          var hideStage = !!(stageMeta && (stageMeta.getAttribute('content') || '').toLowerCase() === 'off');
          appEl.classList.toggle('no-stage', hideStage);
        }
      }

      /* 只更新当前栏目高亮，其余不变 */
      document.querySelectorAll('.side .nav').forEach(function (n) {
        n.classList.toggle('on', normPath(n.getAttribute('href')) === normPath(href));
      });

      /* 仅子页需要重跑其初始化脚本（聊天逻辑已在 shell 初始化一次，且 .main 未销毁不用重跑）。
         脚本以 new Function 隔离作用域执行，避免各页 const/$ 等声明污染全局导致重复执行报错。
         注意：必须查询解析出的 doc（子页来源），不要查活动文档。 */
      if (!isHome) spaRunScripts(doc);

      /* 通知新内容的 morph 图标做初始绘制 */
      document.dispatchEvent(new Event('morph-ready'));
      if (push) history.pushState({ spa: 1 }, '', href);
      /* ⭐ 2026-10-13：「页面真的换完了」事件（必须在 pushState 之后发，
         监听方读 location.pathname 才是新页）。
         为什么需要它：SPA 只替换 .app > .main，各子页自带的脚本被
         「同 URL 去重」跳过（外壳已加载过同一份 theme_manager.js），
         于是按页生效的模块（主题管理器：分页壁纸 --tm-wall-cur / 分页贴纸）
         永远停留在启动时那一页（chat）的状态 —— 用户实测「分页壁纸定了
         不生效」：数据存了、图落了、编辑器预览也看到，一切换过去就没影。
         现在由导航方统一广播，theme_manager 监听后按新页重应用一次。 */
      try {
        window.dispatchEvent(typeof CustomEvent === 'function'
          ? new CustomEvent('spa-page', { detail: { path: href } })
          : (function () { var e = new Event('spa-page'); e.path = href; return e; })());
      } catch (_) {}
    } catch (e) {
      console.warn('[spa] 路由失败：', e);
      /* 安全回退：绝不整页跳转 window.location —— 那会丢掉右栏 Live2D 舞台、
         并重跑首启引导浮层（表现成「回聊天页又进引导问卷」）。改为原地提示，用户可重试。 */
      if (window.Backend && window.Backend.toast) {
        try { window.Backend.toast('页面加载失败，请重试', 'err'); } catch (_) {}
      }
    } finally {
      spaBusy = false;
    }
  }

  function spaRunScripts(doc) {
    var scripts = Array.prototype.slice.call(doc.querySelectorAll('script'));
    /* 先收集所有脚本（含外部 + 内联），按文档顺序依次执行。
       外部脚本动态创建 <script> 追加到 document.head 加载；
       内联脚本用 new Function 隔离作用域执行。
       关键修复：旧版只取 textContent 导致 <script src="..."> 被静默跳过，
       造成 kb.html 等子页的 app_backend.js / gsap 等依赖未加载，
       业务脚本里 Backend/listKnowledge 等全部 undefined → 空白页。 */
    var idx = 0;
    function runNext() {
      if (idx >= scripts.length) return;
      var s = scripts[idx++];
      /* 跳过 module（由浏览器原生管理，不应在此重复执行） */
      if (s.type === 'module') { runNext(); return; }
      var src = (s.getAttribute('src') || '').trim();
      if (src) {
        /* 外部脚本：去重（同 URL 不重复加载） */
        var dup = false;
        Array.prototype.some.call(document.querySelectorAll('script[src]'), function (e) {
          if ((e.getAttribute('src') || '') === src) { dup = true; return true; }
          return false;
        });
        if (dup) { runNext(); return; }
        var el = document.createElement('script');
        el.src = src;
        el.dataset.spaScript = '1';
        el.addEventListener('load', runNext);
        el.addEventListener('error', function () {
          console.warn('[spa] 外部脚本加载失败：' + src);
          runNext();   // 不阻塞后续脚本
        });
        document.head.appendChild(el);
      } else {
        /* 内联脚本 */
        var code = s.textContent;
        if (code && code.trim()) {
          try { new Function(code)(); }
          catch (err) { console.warn('[spa] 页面脚本执行失败：', err); }
        }
        runNext();
      }
    }
    runNext();
  }

  function initRouter() {
    /* 拦截所有内部链接（侧栏 .nav「对话/知识库/…」、知识库内「了解教学」、教学页
       「返回知识库」等 .btn 链接统统算），只替换中间 .main，不整页重载外壳：
       - 外壳（含引导浮层 / 左栏 / 右栏 Live2D 舞台）只在启动加载一次，
         因此首启引导浮层也只判定一次，回到聊天页永远不会再弹引导页；
       - 左右两栏常驻，导航只换中间内容 → 即「左 / 中 / 右」拆开的人性化布局，
         最右的 Live2D 舞台与数据面板永不丢失；
       - 走路径级时间戳接口 /_spa/<ts>/<file>，WebView2 缓存键必变，永远拉最新页面。
       外链 / #锚点 / 带 data-no-spa / target!=_self 的链接保持原生行为。 */
    document.addEventListener('click', function (e) {
      var a = e.target.closest ? e.target.closest('a[href]') : null;
      if (!a) return;
      var href = (a.getAttribute('href') || '').trim();
      if (!href || href.startsWith('#') || href.startsWith('http') || href.startsWith('//') || href.startsWith('mailto:')) return;
      if (a.target && a.target !== '_self') return;
      if (a.hasAttribute('data-no-spa')) return;
      if (!href.startsWith('/')) return;   // 仅内部绝对路径走 SPA
      e.preventDefault();
      /* 乐观高亮：立刻点亮被点的那一项，别等 fetch 回来才变色
         （实测切页 DOM 只要几十 ms，但"点了没反应"的观感主要来自等待期没有任何反馈）。 */
      try {
        document.querySelectorAll('.side .nav').forEach(function (n) {
          n.classList.toggle('on', normPath(n.getAttribute('href')) === normPath(href));
        });
      } catch (_) {}
      spaLoad(href, true);
    });
  }

  /* 悬停即预取：把"点击后才开始的 fetch"提前到"鼠标移上去"。
     典型用户从移到菜单到点下去有几百毫秒，够把目标页拉回来 → 点击几乎瞬时。 */
  document.addEventListener('mouseover', function (e) {
    var a = e.target.closest ? e.target.closest('a[href]') : null;
    if (!a) return;
    if (a.hasAttribute('data-no-spa') || (a.target && a.target !== '_self')) return;
    var href = (a.getAttribute('href') || '').trim();
    if (!href || href.charAt(0) !== '/' || href.indexOf('//') === 0) return;
    prefetchHref(href);
  }, true);

  var _firstReady = true;
  document.addEventListener('morph-ready', function () {
    /* 首次整页载入由 setupNav 负责；之后每次切栏都给当前栏目一个脉冲动效 */
    if (_firstReady) { _firstReady = false; return; }
    var active = document.querySelector('.side .nav.on') || document.querySelector('.side .nav');
    if (active && window.Morph && window.Morph.pulseNav) {
      try { window.Morph.pulseNav(active); } catch (_) {}
    }
  });

  /* 启动恢复：子页被整页加载时，服务端注入的「外壳守门」脚本把目标页写进
     sessionStorage 后 replace 回 /index.html；这里接着用 SPA 把它载入到中栏，
     用户看到的就是「还是刚才那一页」，而不是被甩回聊天页。 */
  function bootRestore() {
    var target = '';
    try {
      var m = /(?:^|[?&])goto=([^&]+)/.exec(location.search || '');
      if (m) { try { target = decodeURIComponent(m[1]); } catch (_) { target = m[1]; } }
      if (!target) target = sessionStorage.getItem('__spa_pending') || '';
      sessionStorage.removeItem('__spa_pending');
    } catch (_) {}
    if (!target || target.indexOf('/redesign/') !== 0) return;
    if (normPath(target) === '/') return;
    setTimeout(function () { spaLoad(target, false); }, 0);
  }

  /* 浏览器前进/后退：没有 popstate 监听时，浏览器会「整页加载」上一个 URL，
     而那个 URL 往往正是子页独立文档（无 spa.js / 无右栏舞台 / 侧栏旧副本），
     于是「点一下后退就卡回老页面」。这里统一接管成 SPA 内切换。 */
  window.addEventListener('popstate', function () {
    var p = location.pathname + (location.search || '');
    if (normPath(p) === '/') { spaLoad('/index.html', false); return; }
    if (p.indexOf('/redesign/') === 0 && p.indexOf('.html') > 0) { spaLoad(p, false); return; }
  });

  /* 提前缓存聊天 .main：原实现只在 window load 时缓存一次，若用户在 load 之前
     就点了侧栏（或 load 事件因资源阻塞迟到），_chatMain 为空 → 回聊天页会失败。 */
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', cacheChatMain);
  else cacheChatMain();

  window.addEventListener('load', function () {
    cacheChatMain();   // 缓存聊天 .main，供 SPA 切回对话栏时复用（保留监听与轮询）
    document.dispatchEvent(new Event('morph-ready'));
    bootRestore();
  });

  /* 供页面脚本做 SPA 内跳转（替代 window.location.href = '/redesign/xxx.html'） */
  window.spaGo = function (href) { spaLoad(href, true); };

  initRouter();
})();
