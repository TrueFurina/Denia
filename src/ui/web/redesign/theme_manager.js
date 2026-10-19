/* ============================================================
   theme_manager.js — 角色图片 → 主题系统（2026-10-05）
   设计见 _design.txt。一份数据、四处渲染、自动取色、全屏 WYSIWYG 编辑器。

   对外只暴露 window.ThemeManager：
     ThemeManager.boot()        启动时拉取并应用当前角色主题
     ThemeManager.open()        打开主题编辑器（全屏）
     ThemeManager.list()        主题列表 + 绑定关系
     ThemeManager.create(name)  新建主题
     ThemeManager.rename/delete/bind
     ThemeManager.apply(id)     立即应用某主题
   设计要点：
     · 图位通过 CSS 变量下发（--tm-hero / --tm-stand / --tm-avatar / --tm-cover），
       页面只用 var() 消费 ⇒ 加新主题不用改任何页面代码。
     · 取色在 Canvas 做：图已在浏览器缓存里，零网络请求，且所见即所取。
     · 编辑器盖在真实 app 上，底层 pointer-events:none（达成"像图片一样不能交互"）。
   ============================================================ */
const ThemeManager = (() => {
  const root = document.documentElement;
  /* ⭐ 2026-10-13 卡顿/卡退根治（一）：编辑器预览 iframe 自我标记。
     主题编辑器预览装的是「真实产品页」（多个活 iframe，每个都有全幅 hero +
     十几个 backdrop-filter 玻璃面板）。这些面板逐帧重模糊是 GPU 大头，
     编辑器还带镜头平移/缩放动画 ⇒ 多帧叠加直接把 WebView2 的 GPU 进程打满，
     实测表现就是「主题定制里切换很卡、甚至卡退」。凡是在 iframe 里跑的
     产品页（含引导页内嵌），一律挂 data-tm-emb，design_tokens.css 据此
     关掉模糊与换色过渡 —— 预览缩略图上根本看不出差别，成本归零。 */
  try {
    if (window.self !== window.top) root.setAttribute('data-tm-emb', '1');
  } catch (e) { root.setAttribute('data-tm-emb', '1'); }   // 跨域取不到也按内嵌算
  const SLOTS = ['hero', 'stand', 'avatar', 'cover'];
  const SLOT_META = {
    hero:   { label: '主页背景',   hint: '建议 1920×1080 以上横向大图，占比最大、自动取色取它', ratio: 16 / 9 },
    stand:  { label: '角色立绘',   hint: '建议 800×1000 竖图，垫在 Live2D 角色后面', ratio: 4 / 5 },
    avatar: { label: '头像',       hint: '建议 512×512 正方形，侧栏/聊天卡/桌宠气泡共用', ratio: 1 },
    cover:  { label: '角色卡头图', hint: '建议 1200×800 横图；角色没有自带头图时，这张图自动成为她的卡头图', ratio: 3 / 2 },
  };
  /* 页面壁纸可选的子页（key 用于落盘目录 pages/<key>/，file 用于判断「现在在哪一页」）
     ⚠️ 2026-10-12 修：旧清单里列了并不存在的 voice 页，又漏了 calls / characters / profiles
        —— 与实际侧栏对不上，等于一部分页永远配不了壁纸。现按真实侧栏逐项对齐。 */
  const PAGES = [
    { key: 'chat',       label: '对话',     file: 'index' },
    { key: 'daily',      label: '日报周报', file: 'ui_redesign_daily' },
    { key: 'calls',      label: '通话',     file: 'ui_redesign_calls' },
    { key: 'characters', label: '角色',     file: 'ui_redesign_characters' },
    { key: 'memory',     label: '记忆',     file: 'ui_redesign_memory' },
    { key: 'stickers',   label: '表情包',   file: 'ui_redesign_stickers' },
    { key: 'lib',        label: '知识库',   file: 'lib' },
    { key: 'settings',   label: '设置',     file: 'ui_redesign_settings' },
    { key: 'profiles',   label: '软件档案', file: 'ui_redesign_software_profiles' },
  ];
  /* 当前文档属于哪一页。SPA 是「整页导航」：每个子页是独立文档、各自引本文件，
     所以从 pathname 主干就能判定。找不到时保守回落 chat。 */
  function pageKeyOf() {
    try {
      const path = String(location.pathname || '').split('/').pop().replace(/\.html?$/i, '');
      if (!path || path === 'index' || path === 'ui_redesign_main') return 'chat';
      for (let i = 0; i < PAGES.length; i++) {
        if (PAGES[i].file === path) return PAGES[i].key;
      }
      /* ⚠️ 2026-10-12：知识库页文件是 ui_redesign_knowledge.html，正则会得到
         'knowledge' —— 但壁纸槽位 key 约定是 'lib'（与编辑器/落盘目录一致）。
         软件档案页同理：正则得 'software_profiles'，槽位 key 是 'profiles'。
         不归一的话，这两页永远读不到自己的壁纸变量（配了也白配）。 */
      if (path === 'ui_redesign_knowledge' || path === 'ui_redesign_knowledge_help') return 'lib';
      if (path === 'ui_redesign_software_profiles') return 'profiles';
      const m = /^ui_redesign_([a-z0-9_]+)$/i.exec(path);
      if (m) return m[1].toLowerCase();
    } catch (e) { /* noop */ }
    return 'chat';
  }

  const state = {
    themes: [],      // 全部主题包
    bindings: {},    // character_id -> theme_id
    activeCharId: '', // 当前激活角色 id（paintCards 的兜底来源）
    current: null,   // 当前应用的主题对象
    editing: null,   // 编辑器里的工作副本（未保存）
    slotHints: {},
    /* 【2026-10-12】主题编辑器预览覆写：编辑器把「草稿壁纸」postMessage 进来时
       （__tm_preview）记在这里，applyForActive 以它为准渲染当前页 ——
       只影响本帧内存里的呈现，绝不落盘、绝不影响真实主题数据。
       放在本文件的原因：所有产品子页都引 theme_manager.js，一条监听全页生效；
       _te_real_* 副本页则由 _te_bridge.js 承担同样职责（同源直写优先）。 */
    previewWall: null,
  };
  window.addEventListener('message', (e) => {
    const d = e && e.data;
    if (!d || d.__tm_preview !== true) return;
    state.previewWall = d.wall || null;   // { page, url }（url 为裸地址，无 url() 包装）
    try { applyForActive(); } catch (err) { /* 预览覆写失败不打断页面 */ }
  });

  /* ============================================================
     ⭐ SPA 切页后按页重应用（2026-10-13 实锤修复）
     ------------------------------------------------------------
     症状：用户在主题编辑器里给「日报周报」配了壁纸，数据存了、图落了、
     编辑器预览里也看得到，一切换到日报页就没影 —— "分页壁纸定了不生效"。
     根因：本软件是 SPA 外壳（index.html），子页是 fetch 回来注入
     .app > .main 的，子页自带的 theme_manager.js 被 spa.js 的
     「同 URL 去重」跳过不执行；而外壳这一份只在启动时按当时的
     pathname（chat）应用过一次。之后无论切到哪个子页，都没有人再调
     applyTheme ⇒ --tm-wall-cur / data-tm-wall / data-tm-page 全是
     chat 的旧值 ⇒ 非 chat 页的壁纸与贴纸永远不显示。
     修法：监听 spa.js 在每次导航收尾（pushState 之后）广播的
     'spa-page' 事件，按新 pathname 重应用一次。popstate 也兜一份
     （浏览器前进/后退同样走 spaLoad，双保险无副作用——applyTheme
     幂等，贴纸层有 sig 守卫不会重建 <video>）。
     ============================================================ */
  function onSpaPage() {
    try {
      if (state.current) applyTheme(state.current);   /* 已加载过：轻量重应用（不重新拉列表） */
      else applyForActive();                          /* 还没加载过（首屏竞态）：走完整启动 */
    } catch (e) { /* 主题是增强功能，绝不影响导航 */ }
  }
  window.addEventListener('spa-page', onSpaPage);
  window.addEventListener('popstate', () => setTimeout(onSpaPage, 0));

  function ls(k, v) {
    try { return (v === undefined) ? localStorage.getItem(k) : localStorage.setItem(k, v); }
    catch (e) { return null; }
  }
  const B = () => (window.Backend || null);
  /* ⚠️ 必须用 app_backend.js 导出的**驼峰**方法名（tList/tSave/tPick…），
     它内部已有 whenReady() 轮询等 pywebview 注入 api。此前这里自造
     call('tSave') 查的是下划线属性 ⇒ 永远 undefined ⇒ 一律 offline ⇒
     点「新建主题」永远报"新建失败"。名字对不上是静默失效，不报错。 */
  function call(name, ...a) {
    const b = B();
    const fn = b && b[name];
    if (typeof fn !== 'function') {
      console.warn('[ThemeManager] 后端方法缺失: Backend.' + name);
      return Promise.resolve({ success: false, offline: true,
        error: '后端方法不可用（Backend.' + name + '）——请重启应用' });
    }
    try { return Promise.resolve(fn.apply(b, a)); }
    catch (e) { return Promise.resolve({ success: false, error: String(e && e.message || e) }); }
  }
  const toast = (msg, kind) => { try { if (B() && B().toast) B().toast(msg, kind); } catch (e) {} };

  /* ============================================================
     一、自动取色（Canvas，零网络）
     ============================================================ */

  /* 从 <img> 取主色：缩到 64×64 分桶统计，过滤掉过暗/过亮/低饱和的像素，
     再按「明度适中 + 饱和度够高 + 出现次数多」综合打分。返回 {c1, c2}。 */
  function extractPalette(imgEl) {
    const N = 64;
    const cv = document.createElement('canvas');
    cv.width = N; cv.height = N;
    const ctx = cv.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(imgEl, 0, 0, N, N);
    let data;
    try { data = ctx.getImageData(0, 0, N, N).data; }
    catch (e) { return null; }        // 跨域/画布被污染 → 放弃自动取色，不猜

    const buckets = new Map();
    for (let i = 0; i < data.length; i += 4) {
      const a = data[i + 3];
      if (a < 128) continue;          // 透明像素不参与
      const r = data[i], g = data[i + 1], b = data[i + 2];
      const [h, s, l] = rgb2hsl(r, g, b);
      /* 三条校正（设计文档第五条）：
         ① 明度区间 0.20–0.78，排除纯黑纯白与极暗背景色
         ② 饱和度下限 0.12，排除灰白背景（否则配色永远灰扑扑）
         ③ 分桶 24 级，够用又不至于太碎 */
      if (l < 0.20 || l > 0.78) continue;
      if (s < 0.12) continue;
      const key = (Math.round(h * 24) << 16) | (Math.round(s * 8) << 8) | Math.round(l * 8);
      const cur = buckets.get(key);
      if (cur) { cur.n++; cur.r += r; cur.g += g; cur.b += b; }
      else buckets.set(key, { n: 1, r, g, b, h, s, l });
    }
    if (!buckets.size) return null;

    const arr = [...buckets.values()].map(k => {
      const r = k.r / k.n, g = k.g / k.n, b = k.b / k.n;
      const [, s, l] = rgb2hsl(r, g, b);
      /* 打分：出现次数为主，明度越接近中间越加分，饱和度加权 */
      const score = k.n * (1 - Math.abs(l - 0.52) * 1.4) * (0.55 + s * 0.75);
      return { r, g, b, s, l, score };
    }).sort((x, y) => y.score - x.score);

    const c1 = arr[0];
    /* 辅色：在色相环上找与主色距离最远、且明度接近的一个 → 保证两色区分度。
       若整张图只有一种色相，退化为在 HSL 上位移 40° 并提亮。 */
    let c2 = arr.slice(1, 12).find(c => {
      let dh = Math.abs(c.r - c1.r);
      return dh > 0.10 || Math.abs(c.l - c1.l) > 0.12;
    });
    if (!c2) c2 = shiftHsl(c1.r, c1.g, c1.b, 40, 0.10);
    return { c1: rgb2hex(c1.r, c1.g, c1.b), c2: rgb2hex(c2.r, c2.g, c2.b) };
  }

  function rgb2hsl(r, g, b) {
    r /= 255; g /= 255; b /= 255;
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
    let h = 0;
    if (d) {
      if (mx === r) h = ((g - b) / d) % 6;
      else if (mx === g) h = (b - r) / d + 2;
      else h = (r - g) / d + 4;
      h /= 6; if (h < 0) h += 1;
    }
    const l = (mx + mn) / 2;
    const s = d ? d / (1 - Math.abs(2 * l - 1)) : 0;
    return [h, s, l];
  }
  function rgb2hex(r, g, b) {
    /* NaN 防线：Math.round(NaN)→NaN，NaN.toString(16)→"NaN"，会拼出
       "#NANNANNAN" 这种废色值写进 CSS（--accent-1 变无效，整站强调色崩）。
       任一分量非有限数就退回默认青紫，绝不把脏值往外传。 */
    const safe = (v, d) => (Number.isFinite(v) ? v : d);
    r = safe(r, 45); g = safe(g, 212); b = safe(b, 255);
    const c = v => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0');
    return ('#' + c(r) + c(g) + c(b)).toUpperCase();
  }
  function shiftHsl(r, g, b, dh, dl) {
    const [h, s, l] = rgb2hsl(r, g, b);
    const nh = (h + dh / 360 + 1) % 1;
    const nl = Math.max(0.2, Math.min(0.78, l + dl));
    const ns = Math.max(0.15, Math.min(0.9, s * 1.05));
    /* HSL→RGB */
    const q = nl < 0.5 ? nl * (1 + ns) : nl + ns - nl * ns;
    const p = 2 * nl - q;
    /* ⚠️ hue() 必须先对 t 做**取模**归一，不能只靠函数体内的 ±1：
       调用处会传 nh+1/3（可 >1）甚至 nh-1/3（可 <0），只减/加一次不够，
       越界后算出的 t 仍 >1 ⇒ RGB 变 NaN ⇒ "#NANNANNAN" 这种废色值。 */
    const hue = (t) => {
      t = ((t % 1) + 1) % 1;
      if (t < 1 / 6) return p + (q - p) * 6 * t;
      if (t < 1 / 2) return q;
      if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
      return p;
    };
    return rgb2hex(hue(nh + 1 / 3) * 255, hue(nh) * 255, hue(nh - 1 / 3) * 255);
  }

  /* 取一张图的尺寸（用于校验比例，不用后端）。同样带超时，防 onload/onerror 都不来。 */
  function imgSize(url) {
    return new Promise((res) => {
      if (!url) return res({ w: 0, h: 0 });
      let done = false;
      const finish = (v) => { if (!done) { done = true; res(v); } };
      const im = new Image();
      const timer = setTimeout(() => finish({ w: 0, h: 0 }), 3000);
      im.onload = () => { clearTimeout(timer); finish({ w: im.naturalWidth, h: im.naturalHeight }); };
      im.onerror = () => { clearTimeout(timer); finish({ w: 0, h: 0 }); };
      im.src = url;
    });
  }

  /* ============================================================
     二、图位应用（主题 → CSS 变量）
     ============================================================ */
  /* 图位记录 → CSS background-image 值。
     ⚠️ 2026-10-13 修双包装：编辑器 themePayload 对「预设渐变」存的是
     裸 CSS 串（linear-gradient(...)），旧代码无条件包一层 url("...")
     ⇒ url("linear-gradient(...)") 是无效值 ⇒ 预设背景在生产页完全不显示
     （用户报"别的 bug"之一）。现在：已是 CSS 值（url( / *-gradient(）
     原样用，其余（真实路径/URL）才包 url()。 */
  function bgCss(rec) {
    const u = rec && rec.url;
    if (!u) return 'none';
    const s = String(u).trim();
    if (!s) return 'none';
    if (s.indexOf('url(') === 0 || /^(linear|radial|conic|repeating-linear|repeating-radial|repeating-conic)-/i.test(s)) {
      return s;
    }
    return 'url("' + s + '")';
  }
  /* ============================================================
     ⭐ 背景自适应透明度（2026-10-13 Task #33）
     ------------------------------------------------------------
     用户诉求：透明度不该是写死的 —— 要「根据背景图算出对应板块透明度」：
     图越花/越亮（深色主题），面板与纱罩要越实才不糊字；图素净时面板退开，
     让角色图多透出来。算法（纯本地、零网络、canvas 降采样 32×32）：
       · lum  = 平均相对亮度（0~1）
       · sd   = 亮度标准差（细节/花哨程度）
     产出三样东西：
       1) --tm-hero-veil / --tm-wall-veil  整页纱罩按图重建（比例缩放默认梯度）；
       2) --tm-panel-boost (0~1)           ui_shared.css 的玻璃面板底色按它加实；
       3) 测量失败（跨域/加载失败）→ 什么都不写，保持 CSS 默认值，绝不弄坏现状。
     ============================================================ */
  function measureImg(url, cb) {
    if (!url || /^(url\(|[a-z-]+-gradient\()/i.test(url)) { cb(null); return; }
    let done = false;
    const finish = (v) => { if (!done) { done = true; cb(v); } };
    const timer = setTimeout(() => finish(null), 4000);
    try {
      const im = new Image();
      im.onload = () => {
        clearTimeout(timer);
        try {
          const N = 32;
          const cv = document.createElement('canvas');
          cv.width = N; cv.height = N;
          const ctx = cv.getContext('2d', { willReadFrequently: true });
          ctx.drawImage(im, 0, 0, N, N);
          const d = ctx.getImageData(0, 0, N, N).data;
          let sum = 0, sum2 = 0, n = 0;
          for (let i = 0; i < d.length; i += 4) {
            if (d[i + 3] < 125) continue;
            const lum = (0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2]) / 255;
            sum += lum; sum2 += lum * lum; n++;
          }
          if (!n) { finish(null); return; }
          const mean = sum / n;
          const sd = Math.sqrt(Math.max(0, sum2 / n - mean * mean));
          finish({ lum: mean, sd: sd });
        } catch (e) { finish(null); }   // 画布被污染等情况 → 保持默认
      };
      im.onerror = () => { clearTimeout(timer); finish(null); };
      im.src = url;
    } catch (e) { clearTimeout(timer); finish(null); }
  }
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  /* 由测量结果重写纱罩梯度。base 是 design_tokens.css 里默认梯度的各档百分比；
     k 是自适应系数：深色主题图越亮/越花 k 越大（压得更暗才可读），
     浅色主题图越暗/越花 k 越大（白纱越重才不糊深色文字）。 */
  function applyVeil(kind, base, st, dark) {
    const k = dark
      ? clamp(0.62 + (st.lum - 0.32) * 0.8 + st.sd * 0.55, 0.55, 1.22)
      : clamp(0.62 + (0.60 - st.lum) * 0.8 + st.sd * 0.55, 0.55, 1.22);
    const p = base.map(b => clamp(Math.round(b * k), 8, 92));
    let g;
    if (kind === 'hero') {
      g = 'linear-gradient(180deg, color-mix(in srgb, var(--bg) ' + p[0] + '%, transparent) 0%,'
        + ' color-mix(in srgb, var(--bg) ' + p[1] + '%, transparent) 36%,'
        + ' color-mix(in srgb, var(--bg) ' + p[2] + '%, transparent) 64%,'
        + ' color-mix(in srgb, var(--bg) ' + p[3] + '%, transparent) 100%)';
      root.style.setProperty('--tm-hero-veil', g);
    } else {
      g = 'linear-gradient(180deg, color-mix(in srgb, var(--bg) ' + p[0] + '%, transparent) 0%,'
        + ' color-mix(in srgb, var(--bg) ' + p[1] + '%, transparent) 42%,'
        + ' color-mix(in srgb, var(--bg) ' + p[2] + '%, transparent) 100%)';
      root.style.setProperty('--tm-wall-veil', g);
    }
    /* 面板加实系数：图越花（sd 大）或亮度离"舒适区"越远，玻璃底越实。
       极端图最多 +0.85（面板基本变实底），素净图趋近 0（面板更透、图更可见）。 */
    const edge = dark ? Math.max(0, st.lum - 0.38) : Math.max(0, 0.56 - st.lum);
    const boost = clamp((st.sd - 0.10) * 1.5 + edge * 1.1, 0, 0.85);
    root.style.setProperty('--tm-panel-boost', boost.toFixed(2));
  }
  function adaptVeils(heroRec, wallVal) {
    const dark = root.getAttribute('data-theme') !== 'light';
    const heroUrl = heroRec && heroRec.url ? String(heroRec.url) : '';
    let wallUrl = '';
    if (wallVal && wallVal !== 'none') {
      const m = /url\(["']?(.*?)["']?\)/.exec(wallVal);
      wallUrl = m ? m[1] : (/^-?[a-z]+-gradient\(/i.test(wallVal) ? '' : wallVal);
    }
    let pending = (heroUrl ? 1 : 0) + (wallUrl ? 1 : 0);
    if (!pending) { root.style.removeProperty('--tm-panel-boost'); return; }
    if (heroUrl) {
      measureImg(heroUrl, st => { if (st) applyVeil('hero', [60, 24, 32, 80], st, dark); });
    }
    if (wallUrl) {
      measureImg(wallUrl, st => { if (st) applyVeil('wall', [66, 38, 76], st, dark); });
    }
  }

  function applyTheme(t) {
    state.current = t || null;
    const im = (t && t.images) || {};
    SLOTS.forEach(s => {
      const rec = im[s];
      const v = bgCss(rec);
      root.style.setProperty('--tm-' + s, v);
      /* 2026-10-15：立绘改 contain 底部对齐（design_tokens.css 已去顶部渐隐遮罩、
         cover→contain），默认焦点落底边 50% 100%，角色始终完整可见、随面板缩放。
         其余槽位维持 50% 40%。 */
      const focusDef = (s === 'stand') ? '50% 100%' : '50% 40%';
      root.style.setProperty('--tm-' + s + '-focus', (rec && rec.focus) || focusDef);
      root.setAttribute('data-tm-' + s, (rec && rec.url) ? 'on' : 'off');
    });
    /* ⚠️ 必须同步挂 has-* class：design_tokens.css 的图位规则全部以
       body.has-hero / .stage.has-stand / .ccard.has-cover / .cav.has-img 为开关。
       只改变量不挂 class ⇒ 变量有值但没有任何规则消费它（静默失效）。
       类名要与真实 DOM 严格对齐：角色页是 .ccard（角色卡）与 .cav（卡内头像）。 */
    document.body.classList.toggle('has-hero', !!(im.hero && im.hero.url));
    const hasStand = !!(im.stand && im.stand.url);
    const hasAvatar = !!(im.avatar && im.avatar.url);
    const hasCover = !!(im.cover && im.cover.url);
    paintSlots(hasStand, hasAvatar, hasCover);
    paintCards();   /* 角色卡按各自绑定注入 cover/avatar（兜底静态卡） */
    /* ---------- 配色 ----------
       ⚠️🔴 曾经只在「有 accent1 且有 accent2」时调 setCustom，没有 else。
       结果：从「有配色」的主题切到「没配色」的主题（新建的空主题就是）时，
       这条分支**什么都不做** ⇒ --accent-1/2 原地保留上一套 ⇒
       用户看到「点了别的主题，配色还是第一个的」——
       实测四次点击 accent1 恒为 #BE929E（第一条的色），完全不变。
       静默失效：界面无报错，state 里的 current.id 也确实变了，只有眼睛看得出不对。
       修法：无配色时**显式回落**到默认预设（清掉 custom 的内联变量）。 */
    const th = (t && t.theme) || {};
    const c1 = (th.accent1 || '').trim();
    const c2 = (th.accent2 || '').trim();
    if (c1 && c2) {
      if (window.Theme && window.Theme.setCustom) window.Theme.setCustom(c1, c2);
      else {
        root.removeAttribute('data-accent');
        root.style.setProperty('--accent-1', c1);
        root.style.setProperty('--accent-2', c2);
      }
    } else if (window.Theme && window.Theme.setAccent) {
      /* 回落：Theme.applyAccent 内部会 removeProperty('--accent-1/2')，
         否则内联变量优先级高于 [data-accent] 规则，预设色永远盖不掉（见 app_backend.js:595）。 */
      window.Theme.setAccent('aurora');
    } else {
      root.removeAttribute('data-accent');
      root.style.removeProperty('--accent-1');
      root.style.removeProperty('--accent-2');
    }
    /* 页面壁纸
       ⚠️ 2026-10-12 修「消费端三处全断」：
       ① 属性原先只挂 <html>，而 design_tokens.css 的选择器是 body[data-tm-pages="on"]
          ⇒ 永不命中（配了也白配）；
       ② CSS 只硬编码消费了 --tm-wall-daily，其余页的变量没有任何规则读它；
       ③ 编辑器根本没有配壁纸的入口。
       现在：属性 html + body 双写；并把「当前这一页」的壁纸另写进 --tm-wall-cur，
       由 CSS 单条规则消费 —— 以后增删页只改上面的 PAGES，CSS 零改动。 */
    const pagesOn = !!(t && t.pageWallpaper);
    /* ⚠️ pages 可能出现在两处：主题包落盘后是 images.pages（后端 t_save 写入的位置）；
       而编辑器「完成 / 返回」回传的是**尚未落盘的内存包**，pages 在顶层。
       两处都认，才不会出现「编辑器里配好了、退出软件却没生效」。
       【2026-10-12 预览覆写】编辑器推来草稿壁纸（state.previewWall）时以它为准：
       覆写优先于激活主题，且无需总开关也生效（编辑器里看到的就是生产会出现的）。 */
    const pv = state.previewWall || null;
    const pw = pv ? {} : (pagesOn ? (im.pages || t.pages || {}) : {});
    const curKey = pv ? (pv.page || pageKeyOf()) : pageKeyOf();
    root.setAttribute('data-tm-pages', (pagesOn || pv) ? 'on' : 'off');
    if (document.body) document.body.setAttribute('data-tm-pages', (pagesOn || pv) ? 'on' : 'off');
    let curWall = 'none';
    PAGES.forEach(p => {
      let rec = pw[p.key];
      if (pv && p.key === pv.page) rec = pv.url ? { url: pv.url } : null;
      const v = bgCss(rec);
      root.style.setProperty('--tm-wall-' + p.key, v);
      if (p.key === curKey && v !== 'none') curWall = v;
    });
    root.style.setProperty('--tm-wall-cur', curWall);
    /* ⚠️ data-tm-wall 是必需的「本页到底有没有壁纸」开关：
       壁纸与 hero 抢同一个 body::before。若只写 --tm-wall-cur=none，
       规则仍会把 background-image 设成 none —— 结果是「只要开了页面壁纸总开关，
       所有没配壁纸的页连原来的 hero 背景都没了」。所以必须显式区分。 */
    root.setAttribute('data-tm-wall', curWall !== 'none' ? 'on' : 'off');
    if (document.body) {
      document.body.setAttribute('data-tm-page', curKey);
      document.body.setAttribute('data-tm-wall', curWall !== 'none' ? 'on' : 'off');
      document.body.style.setProperty('--tm-wall-cur', curWall);
    }
    paintStickers(t);   /* 贴纸 / 表情包层（2026-10-12） */
    /* 背景自适应透明度（Task #33）：按 hero 与当前页壁纸的实测亮度/细节度
       重建纱罩与面板透明度。异步测量、失败保持默认，绝不阻断主题应用。 */
    try {
      const _heroRec = im.hero || null;
      const _curWall = curWall;
      setTimeout(() => { try { adaptVeils(_heroRec, _curWall); } catch (e) {} }, 0);
    } catch (e) { /* noop */ }
    return t;
  }

  /* ============================================================
     贴纸 / 表情包层（2026-10-12）
     ------------------------------------------------------------
     主题包里的 stickers: [{id,url,kind,page,x,y,scale,w,h}]。
     · page 匹配当前页（pageKeyOf）才渲染 —— 每个子页都是独立文档，
       各自的 theme_manager 实例天然只画自己那页的贴纸；
     · x/y 是 0~1 的页面百分比（居中锚点），scale 是缩放倍率，
       基准宽 = min(原图宽, 320)px —— 与主题编辑器完全同一套规则，
       否则「编辑器里摆的位置」和「产品里出现的位置」对不上；
     · 整层 pointer-events:none —— 贴纸是装饰，绝不能挡住任何按钮/输入。
     ============================================================ */
  function paintStickers(t) {
    let layer = document.getElementById('tmStickerLayer');
    if (!layer) {
      layer = document.createElement('div');
      layer.id = 'tmStickerLayer';
      layer.setAttribute('aria-hidden', 'true');
      (document.body || document.documentElement).appendChild(layer);
    }
    const list = (t && t.stickers) || [];
    const cur = pageKeyOf();
    const mine = list.filter(s => s && s.url && (s.page || '') === cur);
    /* 内容只在「数量或内容变化」时重建 —— 主题 repaint 很频繁，别把 <video> 拆了重放 */
    const sig = mine.map(s => [s.id, s.url, s.x, s.y, s.scale].join('|')).join(';;');
    if (layer.dataset.sig === sig) return;
    layer.dataset.sig = sig;
    layer.innerHTML = '';
    mine.forEach(s => {
      const el = document.createElement('div');
      el.className = 'tm-sticker';
      el.style.left = (Math.max(0, Math.min(1, +s.x || 0.5)) * 100).toFixed(3) + '%';
      el.style.top = (Math.max(0, Math.min(1, +s.y || 0.5)) * 100).toFixed(3) + '%';
      const baseW = Math.min(+s.w || 220, 320);
      el.style.width = Math.max(24, Math.round(baseW * (+s.scale || 1))) + 'px';
      let media;
      if (s.kind === 'video') {
        media = document.createElement('video');
        media.src = s.url;
        media.autoplay = true; media.loop = true; media.muted = true;
        media.setAttribute('playsinline', '');
      } else {
        media = document.createElement('img');
        media.src = s.url;
        media.draggable = false;
        media.alt = '';
      }
      el.appendChild(media);
      layer.appendChild(el);
    });
  }

  /* 把 has-* class 刷到所有相关元素上。
     为什么需要「重新刷」而不是只刷一次：角色页的角色卡是 JS 动态生成的
     （renderChars 重建 innerHTML），SPA 切页也会换掉 .main 内容 ——
     新建的节点身上没有 class，必须重新挂，否则「配了图但卡片没变化」。 */
  let _mo = null;
  function paintSlots(hasStand, hasAvatar, hasCover) {
    const doc = document;
    const stg = doc.querySelector('.stage');
    if (stg) stg.classList.toggle('has-stand', !!hasStand);
    /* ⚠️ 只动「聊天页」相关元素，绝不动角色卡网格（.ccard / .char-card / .cav）：
       那些是角色卡页的元素，每张卡应显示「各自绑定角色」的图（renderChars 里按 boundTheme
       逐卡注入 --tm-cover / --tm-avatar）。若这里把全局 --tm-cover 挂到所有 .ccard，
       就会出现「应用一套主题、所有角色卡都变那套图」的 bug（实测：绑达妮娅却全卡变达妮娅图）。
       聊天页元素：.stage（立绘）、.side .brand（左上角头图）、.av（聊天侧栏头像）——
       这些确实跟随当前激活角色的全局主题，挂全局 has-* 是对的。 */
    /* 【2026-10-15 左上角品牌图】与 .av 同一套优先级：当前角色自带头像优先，
       有则 has-img + /local_img 变量；否则回落主题头像（applyTheme 的全局 --tm-avatar）。 */
    const _brandAv = state.activeAvatar || '';
    doc.querySelectorAll('.side .brand').forEach(el => {
      if (_brandAv) {
        el.classList.add('has-img');
        el.style.setProperty('--tm-avatar', "url('/local_img?path=" + encodeURIComponent(_brandAv) + "')");
        el.style.setProperty('--tm-avatar-focus', '50% 50%');
      } else {
        el.classList.toggle('has-img', !!hasAvatar);
        if (hasAvatar) el.style.removeProperty('--tm-avatar');
      }
    });
    /* 【2026-10-11 角色头像优先】聊天页 .av：若当前角色自带 avatar，用它覆盖主题头像；
       否则才按主题头像挂 has-img。变量与 applyCharacterAvatar 同一套。 */
    const _charAv = state.activeAvatar || '';
    doc.querySelectorAll('.av:not(.me)').forEach(el => {
      if (_charAv) {
        el.classList.add('has-img');
        el.style.setProperty('--tm-avatar', "url('/local_img?path=" + encodeURIComponent(_charAv) + "')");
        el.style.setProperty('--tm-avatar-focus', '50% 50%');
      } else {
        el.classList.toggle('has-img', !!hasAvatar);
        if (hasAvatar) el.style.removeProperty('--tm-avatar');   // 回落全局 --tm-avatar
      }
    });
    /* 监听新增节点：聊天页的 .av / .stage 是后生成的（SPA 切页） */
    if (!_mo && window.MutationObserver) {
      let pending = false;
      _mo = new MutationObserver(() => {
        if (pending) return;
        pending = true;
        /* 合并同一批 DOM 变动，别每个节点都扫全树 */
        setTimeout(() => {
          pending = false;
          const cur = state.current;
          const im2 = (cur && cur.images) || {};
          const s = !!(im2.stand && im2.stand.url);
          const a = !!(im2.avatar && im2.avatar.url);
          const st2 = doc.querySelector('.stage');
          if (st2) st2.classList.toggle('has-stand', s);
          const _cb = state.activeAvatar || '';
          doc.querySelectorAll('.side .brand').forEach(el => {
            if (_cb) {
              el.classList.add('has-img');
              el.style.setProperty('--tm-avatar', "url('/local_img?path=" + encodeURIComponent(_cb) + "')");
              el.style.setProperty('--tm-avatar-focus', '50% 50%');
            } else {
              el.classList.toggle('has-img', a);
              if (a) el.style.removeProperty('--tm-avatar');
            }
          });
          const _ca2 = state.activeAvatar || '';
          doc.querySelectorAll('.av:not(.me)').forEach(el => {
            if (_ca2) {
              el.classList.add('has-img');
              el.style.setProperty('--tm-avatar', "url('/local_img?path=" + encodeURIComponent(_ca2) + "')");
              el.style.setProperty('--tm-avatar-focus', '50% 50%');
            } else {
              el.classList.toggle('has-img', a);
              if (a) el.style.removeProperty('--tm-avatar');
            }
          });
          paintCards();
        }, 60);
      });
      _mo.observe(doc.body, { childList: true, subtree: true });
    }
  }

  /* 角色卡图位：按「每张卡各自绑定的主题」逐卡注入 avatar。
     【2026-10-12 用户裁决：头图与背景板合二为一】cover 不再注入 .has-cover 整卡背景
     （历史 bug：绑定主题的卡同时渲染「顶部通栏头图 + 整卡封面背景」两张图叠一起）。
     主题封面唯一的出场方式 = ui_redesign_characters.html bannerHtml() 的通栏头图
     （角色自带头图 > 主题封面 > 渐变占位，三选一）。
     这里保留的职责：avatar 注入（.cav.has-img），以及静态卡兜底。
     ⚠️ 绝不能像旧 paintSlots 那样把「当前主题」的图糊到所有 .ccard（历史 bug：
        应用一套主题 → 所有角色卡都变那套图）。所以：
        · 卡上有 data-char-id（renderChars 生成）→ 严格按该角色的绑定取图；
        · 没有 data-char-id（静态卡）且页面上只有一张真卡 → 视为当前角色的卡，用当前主题；
        · 其它情况（多张无 id 的卡）→ 不动，交给 renderChars 逐卡处理。 */
  function paintCards() {
    const byId = {};
    (state.themes || []).forEach(t => { if (t && t.id) byId[t.id] = t; });
    const binds = state.bindings || {};
    const cards = Array.prototype.slice.call(document.querySelectorAll('.ccard, .char-card'))
      .filter(el => !el.classList.contains('dev'));
    if (!cards.length) return;
    cards.forEach(card => {
      const cid = card.getAttribute('data-char-id') || '';
      let th = null;
      if (cid) th = byId[binds[cid]] || null;
      else if (cards.length === 1) th = state.current || null;
      if (!th) return;
      const im = th.images || {};
      const rec = im.avatar;
      const av = rec && rec.url;
      const cav = card.querySelector('.cav');
      if (cav && av) {
        cav.classList.add('has-img');
        cav.style.setProperty('--tm-avatar', "url('" + av + "')");
        cav.style.setProperty('--tm-avatar-focus', (rec.focus || '50% 40%'));
      }
    });
  }

  /* 当前角色 → 它的绑定主题 → 应用。角色切换时调它。
     【2026-10-11 角色头像】同时把「当前角色自带的 avatar」应用为聊天页头像兜底：
     角色专属头像优先级高于主题通用头像（用户给这个角色单独设的图应压过主题）。 */
  async function applyForActive() {
    try {
      const chars = await call('getCharacters');
      const list = (chars && chars.characters) || [];
      const act = list.find(c => c.is_active) || list[0];
      const cid = act ? act.id : '';
      state.activeCharId = cid;
      /* 【2026-10-15 左上角品牌名】侧栏 .side .brand .wm 跟随当前激活角色，
         不再写死「达妮娅」。theme_manager 全页共享，各子页侧栏一并生效。 */
      try {
        const wm = document.querySelector('.side .brand .wm');
        if (wm && act && act.name && wm.textContent !== act.name) wm.textContent = act.name;
      } catch (e) { /* 品牌名同步失败不影响主题 */ }
      /* 角色自带头像（1:1 方图）→ 应用到聊天页 .av（无则清空，回落主题头像） */
      applyCharacterAvatar((act && act.avatar) || '');
      const tid = state.bindings[cid];
      if (!tid) { applyTheme(null); return null; }
      const t = state.themes.find(x => x.id === tid);
      return applyTheme(t || null);
    } catch (e) { return null; }
  }

  /* 把「角色头像」铺到聊天页的 AI 头像（.av:not(.me)）与侧栏品牌图。
     与主题头像走**同一个 CSS 变量** --tm-avatar：角色头像存在时用它覆盖，
     不存在时置空（后续 applyTheme 会再按主题头像写一次）。
     只读本地路径，经 /local_img 取图；失败绝不影响主题应用。 */
  function applyCharacterAvatar(path) {
    try {
      state.activeAvatar = path || '';
      const doc = document;
      const url = path ? ("url('" + '/local_img?path=' + encodeURIComponent(path) + "')") : '';
      doc.querySelectorAll('.av:not(.me)').forEach(el => {
        if (url) {
          el.classList.add('has-img');
          el.style.setProperty('--tm-avatar', url);
          el.style.setProperty('--tm-avatar-focus', '50% 50%');
        } else {
          /* 无角色头像：交给主题头像决定（applyTheme 会重挂）。这里只在
             主题也没头像时才清，避免把主题头像误清掉。 */
          const im = (state.current && state.current.images) || {};
          if (!(im.avatar && im.avatar.url)) {
            el.classList.remove('has-img');
            el.style.removeProperty('--tm-avatar');
            el.style.removeProperty('--tm-avatar-focus');
          }
        }
      });
      /* 【2026-10-15 左上角品牌图】侧栏 .side .brand 与 .av 同步：角色头像优先，
         无角色头像时回落主题头像（applyTheme 会重挂），两皆无则清掉。 */
      doc.querySelectorAll('.side .brand').forEach(el => {
        if (url) {
          el.classList.add('has-img');
          el.style.setProperty('--tm-avatar', url);
          el.style.setProperty('--tm-avatar-focus', '50% 50%');
        } else {
          const im2 = (state.current && state.current.images) || {};
          if (!(im2.avatar && im2.avatar.url)) {
            el.classList.remove('has-img');
            el.style.removeProperty('--tm-avatar');
            el.style.removeProperty('--tm-avatar-focus');
          }
        }
      });
    } catch (e) { /* 头像应用失败绝不阻断主题 */ }
  }

  /* ============================================================
     三、主题 CRUD（全部走后端）
     ============================================================ */
  async function list() {
    const r = await call('tList');
    if (r && r.success) {
      state.themes = r.themes || [];
      state.bindings = r.bindings || {};
      state.slotHints = r.slotHints || {};
    }
    return r;
  }
  async function create(name) {
    const r = await call('tSave', { name: name || '新主题' });
    await list();
    return r;
  }
  async function rename(id, name) {
    const r = await call('tRename', { id, name });
    await list();
    return r;
  }
  async function remove(id) {
    const r = await call('tDelete', { id });
    await list();
    if (state.current && state.current.id === id) await applyForActive();
    return r;
  }
  async function bind(themeId, charId) {
    const r = await call('tBind', { id: themeId, character_id: charId || '' });
    await list();
    await applyForActive();     // 绑定/解绑后立刻生效，所见即所得
    return r;
  }
  async function save(pkg) {
    const r = await call('tSave', pkg || {});
    await list();
    if (r && r.success && state.current && r.id === state.current.id) {
      const t = state.themes.find(x => x.id === r.id);
      if (t) applyTheme(t);
    }
    return r;
  }
  /* 选文件 → 后端复制落盘 → 写进工作副本 → 应用（不落盘 themes.json，等"保存"） */
  /* 【2026-10-17 修复·导入图片无反应】
     旧实现直接 call('tPick') → 后端用 tkinter 弹系统文件框；
     而随包分发的便携 Python **没有 tkinter**（安装时 Include_tcltk=0）
     → 后端返回 "No module named 'tkinter'" → 用户点「选择图片」毫无反应。

     现在改成：前端用隐藏的 <input type="file"> 选图（WebView2 原生支持），
     读成 dataURL 后走 tStickerDrop（后端本来就支持 base64 + slot 填图位）。
     tkinter 路径保留为兜底（有 tkinter 的环境仍可用）。 */
  function _pickViaFileInput(slot, page) {
    return new Promise((resolve) => {
      const inp = document.createElement('input');
      inp.type = 'file';
      inp.accept = 'image/*,.gif,.webp';
      inp.style.display = 'none';
      document.body.appendChild(inp);
      let settled = false;
      const done = (v) => {
        if (settled) return;
        settled = true;
        try { inp.remove(); } catch (e) {}
        resolve(v);
      };
      inp.onchange = () => {
        const f = inp.files && inp.files[0];
        if (!f) { done(null); return; }
        const fr = new FileReader();
        fr.onload = () => done({ dataUrl: String(fr.result || ''), name: f.name });
        fr.onerror = () => done(null);
        fr.readAsDataURL(f);
      };
      // 用户取消（不会触发 change）→ 兜底超时
      setTimeout(() => done(null), 120000);
      inp.click();
    });
  }

  async function pickImage(themeId, slot, page) {
    let r = null;
    // ① 优先：浏览器文件选择器 + base64（不依赖 tkinter）
    try {
      const picked = await _pickViaFileInput(slot, page);
      if (picked && picked.dataUrl) {
        r = await call('tStickerDrop', {
          id: themeId,
          slot: slot || '',
          page: page || '',
          b64: picked.dataUrl,
          name: picked.name || 'image.png',
        });
      }
    } catch (e) {
      r = null;
    }
    // ② 兜底：后端 tkinter 弹框（有 tkinter 的环境才可用）
    if (!r || !r.success) {
      const r2 = await call('tPick', { id: themeId, slot, page: page || '' });
      if (r2 && r2.success) r = r2;
      else if (!r) r = r2;
    }
    if (!r || !r.success) {
      if (r && r.error) toast(r.error, 'err');
      return r;
    }
    const t = state.editing || state.current;
    if (t) {
      if (page) { t.images = t.images || {}; t.images.pages = t.images.pages || {}; t.images.pages[page] = r.image; }
      else { t.images = t.images || {}; t.images[slot] = r.image; }
      applyTheme(t);
    }
    return r;
  }
  /* 换背景后自动取色（仅当 auto=true，即用户没手动微调过） */
  async function autoPick(theme) {
    const t = theme || state.editing || state.current;
    if (!t) return null;
    const rec = t.images && t.images.hero;
    if (!rec || !rec.url) return null;
    /* ⚠️ 必须加超时：某些 WebView/沙箱环境里 <img> 永不触发 onload 也永不触发
       onerror，裸 new Image() 的 Promise 会永远挂起 → 换完背景编辑器就卡住不响应。
       取色是锦上添花，超时就放弃（返回 null，配色保持原样），绝不能拖死交互。 */
    const pal = await new Promise((res) => {
      let done = false;
      const finish = (v) => { if (!done) { done = true; res(v); } };
      const im = new Image();
      const timer = setTimeout(() => finish(null), 3000);
      im.onload = () => { clearTimeout(timer); finish(extractPalette(im)); };
      im.onerror = () => { clearTimeout(timer); finish(null); };
      im.src = rec.url;
    });
    if (!pal) return null;
    const auto = !t.theme || t.theme.auto !== false;
    if (!auto) return null;                  // 用户手动过 → 不覆盖
    t.theme = t.theme || {};
    t.theme.accent1 = pal.c1;
    t.theme.accent2 = pal.c2;
    t.theme.auto = true;
    applyTheme(t);
    return pal;
  }

  /* 只应用某套主题，**不改绑定、不落盘**。
     为什么要它：设置页「点一行看效果」和「把这套设为某角色的主题」是两件事。
     之前只有 bind() 能改变界面，于是"预览"必须走绑定 ⇒ 用户只是好奇点一下，
     自己的绑定就被改了。预览必须有一条不落盘的独立通道。 */
  function apply(id) {
    const t = state.themes.find(x => x.id === id) || null;
    applyTheme(t);
    return t;
  }

  async function boot() {
    await list();
    await applyForActive();
  }
  /* 启动：把当前角色绑定的主题应用到真实界面。
     ⚠️ 之前只把 boot 导出去却**从没调用** ⇒ 主题永远不自动生效（必须手动进
     编辑器才看得到），用户看到的是"排版不对/没生效"。这里补上自动启动。
     整段包 try/catch：主题是增强功能，它挂了绝不能连带整页报错。 */
  function autoBoot() {
    const go = () => { try { boot(); } catch (e) { console.warn('[ThemeManager] boot 失败', e); } };
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', go, { once: true });
    } else { go(); }
  }

  return { boot, autoBoot, list, create, rename, remove, bind, save, apply, applyTheme, applyForActive,
           pickImage, autoPick, extractPalette, imgSize, open,
           SLOTS, SLOT_META, PAGES, state, ls, toast };
})();
window.ThemeManager = ThemeManager;
ThemeManager.autoBoot();   /* 页面加载即应用当前角色主题 */

/* ============================================================
   主题编辑器（全屏 WYSIWYG）
   ------------------------------------------------------------
   ⚠️ 设计要点（2026-10-05 用户实锤后重写）：
   1. 预览用 **iframe 加载主页**，不是 clone 当前页。四个图位是按主页定义的
      （hero=整页背景 / stand=右侧舞台 / avatar=侧栏头像 / cover=角色卡），
      在设置页打开时当前页既无 .stage 也无 .ccard，写死百分比热点全悬空。
   2. 热点位置**从真实 DOM 实测**（getBoundingClientRect + iframe 缩放换算），
      不再用猜的百分比；实测不到就由顶部「图位条」兜底（任何页面都能换图）。
   3. 遮罩用近实色 + 强 blur：原先 70% 透明会让底层内容透上来与预览混淆。
   4. 底层真实 app 置 pointer-events:none —— 达成"像图片一样不能交互"。
   ============================================================ */

/* ⚠️ 旧 v4 内联编辑器（TM_ANCHOR / TM_NUM + 整段搭 UI 的实现）已整段移除。
   图位锚点现在**只有一处真相源**：_theme_editor_v5.js 里的 SLOTS[k].anchor，
   与它内嵌的 _te_bridge.js 共用同一份选择器表 ——
   两处各写一份选择器是这个项目栽过的坑（"框画在另一个元素上"）。
   编辑器的挂载入口见文件末尾的 window.__openThemeEditor。 */

/* ============================================================
   主题编辑器宿主：整页 iframe 挂 v5 编辑器
   ------------------------------------------------------------
   为什么改挂 iframe 而不是继续内联搭 UI：
     v5 已经是一个完整页面（自带顶栏/三栏/大预览/飞行面板/动效），
     在外面再套一层只会得到两层顶栏、两套状态，还让 v5 的
     postMessage 小桥变成「孙帧」多一跳。内联的唯一好处是同源直连 DOM，
     而 v5 早就用 postMessage 小桥绕开了这个限制。
   职责只剩三件：开/关、传主题 id、关时刷新产品主题。
   ============================================================ */
window.__openThemeEditor = function (themeId) {
  /* 幂等：已在编辑态直接返回。原先做成 toggle 会让连点两次 = 开→关→开→关，
     用户看着像按钮失灵。关闭请用编辑器的「完成」或 Esc。 */
  if (window.__themeEditorOpen) return;
  const M = window.ThemeManager;
  const toast = (msg, kind) => { try { M.toast(msg, kind); } catch (e) {} };

  let t = themeId ? M.state.themes.find(x => x.id === themeId) : M.state.current;
  if (!t && M.state.themes.length) t = M.state.themes[0];
  if (t) { boot(t); return; }

  /* 还没有任何主题（或后端暂时没回包）：直接回一句「先新建」是个死胡同 ——
     用户在设置页点的是「编辑」，却要他跳回去点「新建」再点回来。
     这里自己建一套再打开。⚠️ 必须串行：M.create() 内部会 list()，
     等它真回来再开，否则 iframe 会拿到一个还不存在的 id。 */
  toast('先给你建一套空白主题…');
  M.create('我的主题').then((r) => {
    if (!r || !r.success || !r.id) { toast('建主题失败 · 后端没连上', 'err'); return; }
    const nt = M.state.themes.find(x => x.id === r.id) || M.state.themes[M.state.themes.length - 1];
    if (!nt) { toast('建好了但没找到它 · 刷新一下设置页', 'err'); return; }
    boot(nt);
  }).catch(() => { toast('建主题失败 · 后端没连上', 'err'); });

  /* 打开编辑器本体。t 必须已存在。 */
  function boot(t) {
    M.state.editing = JSON.parse(JSON.stringify(t));   /* 工作副本 */
    M.applyTheme(M.state.editing);                      /* 底层立刻看到这套主题 */

    const layer = document.createElement('div');
    layer.id = 'themeEditor';
    layer.style.cssText = [
      'position:fixed', 'inset:0', 'z-index:2147483000',
      'background:var(--bg)',                      /* 近实色：与底层彻底分离 */
    ].join(';');

    const frame = document.createElement('iframe');
    frame.id = 'tmV5';
    frame.setAttribute('scrolling', 'no');
    frame.setAttribute('title', '主题编辑器');
    frame.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;border:0;background:var(--bg)';
    /* 用同源绝对路径（WebView 走 http，避免 file:// 的 opaque origin）。
       theme= 传要编辑的主题 id；inline=1 让编辑器知道「关掉时通知宿主」。 */
    /* ⭐ 2026-10-13：把宿主**真实**明暗模式传给编辑器。
       app_backend.js 的 Theme 已在宿主 <html> 上明确写出 data-theme="light|dark"，
       但编辑器是独立 iframe、自身没有这个属性 → 取色会永远按 dark 算 →
       浅色界面下配色发艳发灰（用户报「配色很奇怪，以前不会这样」）。
       这里把宿主真值透传，编辑器据此设置自身 data-theme 并按它取色。 */
    const _tmMode = (document.documentElement.getAttribute('data-theme') === 'light') ? 'light' : 'dark';
    frame.src = '/redesign/_theme_editor_v5.html?theme=' + encodeURIComponent(t.id) +
                '&inline=1&mode=' + _tmMode;
    layer.appendChild(frame);
    document.body.appendChild(layer);

    /* 底层真实 app 禁用交互（"像图片一样不能点"）。有些子页没有 #app ⇒ 退化为禁用 body。 */
    const app = document.getElementById('app');
    if (app) app.style.pointerEvents = 'none';
    else document.body.style.pointerEvents = 'none';

    let closed = false;
    /* ⭐ 退出时把编辑器刚保存的那套主题直接套回宿主，确保「移除图片 / 改配色」
       退出即在外生效，不再依赖 list()/getCharacters 在关闭瞬间是否可用。
       症状回溯：原先只靠 M.list().then(applyForActive)，而 boot(t) 打开时已把旧 hero
       套到宿主 #app；若 list()/getCharacters 在关闭那一瞬异步 reject，整段重应用被跳过，
       宿主停在 boot 时的旧值 —— 表现正是「移除没生效、得手动切主题才刷新」。 */
    function applyEditorTheme(d, editedIdOverride) {
      const edited = (d && d.theme && typeof d.theme === 'object') ? d.theme : null;
      const editedId = editedIdOverride || (d && d.themeId) || (M.state.editing && M.state.editing.id) || null;
      return Promise.resolve()
        .then(() => M.list().catch(() => null))            /* ⭐ 先从磁盘重拉，设置页列表才会拿到新数据 */
        .then(() => M.applyForActive().catch(() => null))   /* 再按绑定关系校准一次 */
        .then(() => {
          /* 最后按「编辑的那套」强制覆盖：它来自编辑器实打实保存的包
             （images 里 null = 已移除的槽位），即使上面 applyForActive 因后端抖动
             没跑，这里也能保证改动立刻可见，所见即所得。 */
          let t = edited;
          if (!t && editedId) t = (M.state.themes || []).find(x => x.id === editedId);
          if (t) { try { M.applyTheme(t); } catch (e) {} }
          /* ⭐ 通知设置页重渲染主题列表：左侧预览小框（.tm-sw）会即时刷新，
             否则编辑器里移除了背景图，回到设置页那张缩略图还显示旧图。
             设置页 themePanel 在 ui_redesign_settings.html:1320 监听了 'themeSaved'。 */
          try { window.dispatchEvent(new Event('themeSaved')); } catch (e) {}
        });
    }
    function close(keep, d) {
      if (closed) return;
      closed = true;
      clearTimeout(bootT);
      window.removeEventListener('message', onMsg);
      window.removeEventListener('keydown', onKey);
      layer.remove();
      window.__themeEditorOpen = false;
      const a = document.getElementById('app');
      if (a) a.style.removeProperty('pointer-events');
      document.body.style.removeProperty('pointer-events');
      const editedId = (d && d.themeId) || (M.state.editing && M.state.editing.id) || null;
      M.state.editing = null;
      applyEditorTheme(d, editedId).then(() => {
        if (!keep) toast('已关闭 · 改动已自动保存');
      });
    }
    function onKey(e) { if (e.key === 'Escape') close(false); }
    function onMsg(e) {
      const d = e && e.data;
      if (!d || !d.__te5) return;
      if (d.__te5 === 'saved') {
        M.state.editing = null;
        applyEditorTheme(d);          /* 编辑器自己存的，宿主立刻套用 */
      } else if (d.__te5 === 'close') {
        close(true, d);              /* 编辑器自己存的，别再提示「已保存」 */
      }
    }
    document.addEventListener('keydown', onKey);
    window.addEventListener('message', onMsg);

    window.__themeEditorOpen = true;
    window.__closeThemeEditor = () => close(false);
    /* 给「点了没反应」用的自检：编辑器 iframe 有没有真加载起来 */
    let bootT = setTimeout(function () {
      try {
        var w = frame.contentWindow;
        if (!w || !w.document || !w.document.getElementById('tv5')) {
          toast('编辑器没加载起来 · 关掉重开一次', 'err');
        }
      } catch (e) { /* file:// 下拿不到是正常的，不打扰用户 */ }
    }, 4000);

    toast('主题编辑器已打开 · 改完自动保存，点「完成」关闭');
  }
};

