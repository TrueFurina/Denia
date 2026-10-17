/* ============================================================
   主题编辑器 v5 · 设计稿交互（2026-10-05 第二版）
   ------------------------------------------------------------
   v5.1 相对 v5.0 的五处改动（来自 10-05 的三张截图反馈）：
   ① 跟随聚焦：默认全局视图；点底部任一项 → 预览里【线性运动】推近放大到那一块，
      再点一次 / 点空白处 / 点「回到全局」→ 线性退回全局。（截图2）
   ② 放大后，在该局部上方浮出「添加文件」按钮就地换图。（截图3）
   ③ 角色卡头图改成【整张卡片的背景】，不是卡内一小条。（截图1）
   ④ 图片落点从「直接改元素 backgroundImage」改成「写 --tm-* 变量 + 挂 has-* 类」，
      与生产 theme_manager.js 走同一条路 ⇒ 编辑器里看到的就是产品里出现的，
      顺带修掉「主题色自适配在预览版不生效」。
   ⑤ 流程：先决定给哪个角色用 → 选成套预设（可选）→ 点部位放大 → 就地换图 → 配色。
   ============================================================ */
(function () {
  'use strict';

  /* ---------- 两个真实页面（设计稿副本） ---------- */
  var PAGES = {
    chat:  { name: '对话页', src: '_te_real_chat.html' },
    chars: { name: '角色页', src: '_te_real_characters.html' }
  };

  /* ---------- 四个部位 ---------- */
  var SLOTS = {
    hero: {
      label: '背景', color: '#378ADD', on: 'chat', radius: 18,
      spec: '建议 1920×1080 横图', hint: '整个窗口背景',
      anchor: function (d) { return d.querySelector('.app'); },
      varName: '--tm-hero',
      hosts: [{ sel: 'body', cls: 'has-hero' }],
      seed: true                      /* 参与自动取色 */
    },
    stand: {
      label: '立绘', color: '#7F77DD', on: 'chat', radius: 10,
      spec: '建议 800×1000 竖图', hint: '右侧 Live2D 舞台',
      anchor: function (d) { return d.querySelector('.stage .panel'); },
      varName: '--tm-stand',
      hosts: [{ sel: '.stage', cls: 'has-stand' }],
      seed: true
    },
    avatar: {
      label: '头像', color: '#D85A30', on: 'chat', radius: 16,
      /* 头像是方形/圆形图，用方角框看着不像"这一块" ⇒ 描边跟着走圆。
         ⭐ CSS 里 .tv5-hi.round .tv5-hi-in 早就写好了，但 JS 从没挂过这个类
            —— 典型的"样式写了、行为没接"，零报错。 */
      round: true,
      spec: '建议 512×512 方图', hint: '侧栏 / 气泡 / 角色卡头像',
      anchor: function (d) { return d.querySelector('.side .brand') || d.querySelector('.ccard .cav'); },
      varName: '--tm-avatar',
      hosts: [{ sel: '.cav', cls: 'has-img' }, { sel: '.av', cls: 'has-img' },
              { sel: '.side .brand', cls: 'has-img' }],
      tiny: true
    },
    cover: {
      /* ⭐ 用户裁决（2026-10-12）：头图与背景板【合二为一】——
         这张图不再是「整卡背景板」，而是「角色卡头图」：
         · 绑定的角色已有自带头图 → 角色头图优先，这张图自动让位（不出场）；
         · 角色没有头图 → 这张图自动成为她的卡头图（通栏）。
         锚点仍取 .ccard（取景框指到卡上），hosts 保留仅作定位，CSS 已无铺图。 */
      label: '角色卡头图', color: '#1D9E75', on: 'chars', radius: 14,
      spec: '建议 1200×800 横图', hint: '角色没自带头图时，自动成为她的头图',
      anchor: function (d) { return d.querySelector('.ccard'); },
      varName: '--tm-cover',
      hosts: [{ sel: '.ccard', cls: 'has-cover' }],
      offCls: 'off',                  /* 未绑角色时连示例图也不显示 */
      needBind: true
    }
  };
  var ORDER = ['hero', 'stand', 'avatar', 'cover'];

  /* ============================================================
     版面（2026-10-12 扩容）与「页面壁纸」
     ------------------------------------------------------------
     「版面」= 预览顶栏上可点的那一条。分两类：
       · 内容版面（content）：对话页 / 角色页 —— 有专属图位（背景 / 立绘 / 头像 / 角色卡头图）；
       · 壁纸版面（wall）：侧栏每个子页 —— 各配一张「整页背景」。
     槽位 key 约定：内容版面用 'chat' / 'chars'；壁纸版面用 'wall@<page>'，
     落盘目录 pages/<page>/（对齐 theme_store.slot_dir(page) 与后端 t_pick(page)）。
     ⚠️ WALL_PAGES 的 key 必须与 theme_manager.js 的 PAGES 一致，
        否则「编辑器里配了、软件里不显示」——这类不一致正是旧版壁纸失效的根因之一。
     ============================================================ */
  /* ⭐ 2026-10-12 二次修正（用户反馈"预览不真实"）：壁纸版面不再用灰底画布假装预览，
     每个版面直接加载【真实页面】当预览 —— 对话/角色用设计稿副本（与内容版面同一份），
     其余子页直接加载产品页本体。壁纸铺在页面背后，所见即生产。
     ⚠️ src 必须真实存在；lib 对应产品文件是 ui_redesign_knowledge.html（键名仍为 lib，
        与 theme_manager.js 的 PAGES key 保持一致 —— 落盘目录 pages/lib 已按此约定）。 */
  var WALL_PAGES = [
    { key: 'chat',       label: '对话',     src: '_te_real_chat.html' },
    { key: 'daily',      label: '日报周报', src: 'ui_redesign_daily.html' },
    { key: 'calls',      label: '通话',     src: 'ui_redesign_calls.html' },
    { key: 'characters', label: '角色',     src: '_te_real_characters.html' },
    { key: 'memory',     label: '记忆',     src: 'ui_redesign_memory.html' },
    { key: 'lib',        label: '知识库',   src: 'ui_redesign_knowledge.html' },
    { key: 'settings',   label: '设置',     src: 'ui_redesign_settings.html' },
    { key: 'profiles',   label: '软件档案', src: 'ui_redesign_software_profiles.html' }
  ];
  function wallPageDef(key) {
    for (var i = 0; i < WALL_PAGES.length; i++) if (WALL_PAGES[i].key === key) return WALL_PAGES[i];
    return null;
  }
  var WALL_PREFIX = 'wall@';
  var SCENES = [
    { key: 'chat',  name: '对话页', kind: 'content' },
    { key: 'chars', name: '角色页', kind: 'content' }
  ].concat(WALL_PAGES.map(function (p) {
    return { key: WALL_PREFIX + p.key, name: p.label, kind: 'wall', page: p.key };
  }));

    function isWallSlot(k) { return String(k).indexOf(WALL_PREFIX) === 0; }
    function wallPageOf(k) { return String(k).slice(WALL_PREFIX.length); }
    /* ⭐ 壁纸槽位的「框」：整页就是它的落点，所以框 = 整页内缩一点（留出描边可见的余量）。
       有了它，壁纸槽位才能走进和 hero 完全相同的那条链路：
       框选（整页描边）→ 运镜（整页 ⇒ 倍率停在 base）→ 气泡小卡飞入 → 点「添加文件」才选图。
       没这个框的话 paint/paintDim/paintAct 都拿不到 b ⇒ 一律 early-return，
       表现正是用户反馈的「框选和运镜都没出现、点了直接弹文件框」。 */
    function wallBox() { return { x: 10, y: 10, w: fbSize.w - 20, h: fbSize.h - 20 }; }
    /* 统一取框：实测/推算的优先，壁纸槽位回落到整页框。
       三处消费（paint / paintDim / paintAct）全走它，避免各自漏判。 */
    function boxOf(k) {
      if (!k) return null;
      if (boxes[k]) return boxes[k];
      return isWallSlot(k) ? wallBox() : null;
    }
    function wallPageName(key) {
      for (var i = 0; i < WALL_PAGES.length; i++) if (WALL_PAGES[i].key === key) return WALL_PAGES[i].label;
      return key;
    }
    /* 版面显示名（「这个部位在『X』」的提示要用）：内容版面读 PAGES，壁纸版面读 WALL_PAGES。 */
    function sceneName(key) {
      for (var i = 0; i < SCENES.length; i++) if (SCENES[i].key === key) return SCENES[i].name;
      if (isWallSlot(key)) return wallPageName(wallPageOf(key));
      return key || '预览';
    }
  /* 统一取槽位元信息：内容图位读 SLOTS，壁纸图位按页动态生成（对外形同 SLOTS[k]）。
     这样下面所有「原有逻辑」都能无差别地处理壁纸槽位，不必到处分支。 */
  function metaOf(k) {
    if (!isWallSlot(k)) return SLOTS[k];
    var pg = wallPageOf(k);
    return {
      label: wallPageName(pg) + '背景', color: '#0E9AA7', on: WALL_PREFIX + pg, radius: 14,
      spec: '建议 1920×1080 横图', hint: '这一页的整页背景（铺满）',
      varName: '--tm-wall-' + pg, isWall: true, page: pg
    };
  }
  /* 某个版面拥有哪些槽位 —— 这就是「点版面 → 下面槽位随之变换」的唯一规则来源。 */
  function slotsOfScene(sceneKey) {
    if (isWallSlot(sceneKey)) return [sceneKey];
    var out = [];
    ORDER.forEach(function (k) { if (SLOTS[k].on === sceneKey) out.push(k); });
    return out;
  }

  /* 方案A（2026-10-06 用户裁决）：取色种子按优先级自动选 —— 背景 > 立绘 > 封面 > 头像。
     谁"最高优先级地存在"，就用谁定整站配色；一张图都没有时才回到默认。
     头像/封面平时不参与（点缀），但若只传了它们也能顶上来，避免"传了图却没色"。 */
  var SEED_PRIORITY = ['hero', 'stand', 'cover', 'avatar'];
  function pickSeedSlot() {
    for (var i = 0; i < SEED_PRIORITY.length; i++) {
      var k = SEED_PRIORITY[i];
      if (st && st.recs && st.recs[k] && st.recs[k].url) return k;
    }
    return '';
  }

  /* ---------- 成套预设 ---------- */
  var PRESETS = [
    { id: 'honey',  name: '蜜糖', c1: '#D9738F', c2: '#8B83B0',
      hero: 'linear-gradient(135deg,#F6DDE6 0%,#E9C9D8 45%,#BFA7D6 100%)' },
    { id: 'night',  name: '夜航', c1: '#3AA7E0', c2: '#5B4BD6',
      hero: 'linear-gradient(160deg,#0E1A2B 0%,#16334F 50%,#2B2A6B 100%)' },
    { id: 'frost',  name: '霜白', c1: '#5B8AA8', c2: '#9FB3C8',
      hero: 'linear-gradient(135deg,#F2F5F8 0%,#DFE7EE 50%,#C6D2DC 100%)' },
    { id: 'forest', name: '森语', c1: '#3E9E6E', c2: '#7FA86A',
      hero: 'linear-gradient(135deg,#E7F0E4 0%,#CBE0C4 45%,#9FC3A6 100%)' }
  ];

  var CHARS = {};   /* 由 loadChars() 从后端 getCharacters 填；离线时留空并给提示 */

  /* ---------- 角色列表：必须从后端读，不能写死 ----------
     写死「达妮娅 / 未命名伙伴」的后果很直接：用户加了第三个角色，
     编辑器里根本看不到，绑定也绑不上去。 */
  function loadChars() {
    if (!B) return Promise.resolve(false);
    /* ⚠️ 必须用 B.getCharacters()（无参）。写 B.call('get_characters') 时如果
       顺手塞了 {}，pywebview 会把 dict 带进不接受参数的方法 ⇒ TypeError
       ⇒ 桥把它吞成 {success:false} ⇒ 角色列表永远空，界面上只剩「暂不绑定」，
       看起来像"只有一个角色"。 */
    var p = B.getCharacters ? B.getCharacters() : B.call('get_characters');
    return Promise.resolve(p).then(function (r) {
      if (!r || !r.success || !Array.isArray(r.characters) || !r.characters.length) return false;
      var next = {};
      r.characters.forEach(function (c) {
        if (!c || !c.id) return;
        next[c.id] = {
          name: c.name || c.id,
          /* 2026-10-12 用户裁决「头图与背景板合二为一」：卡头图不再依赖 3D 形象，
             任何角色都能有卡头图 ⇒ 这两个标记只用于**文案**（继承 or 上传），
             不再当权限闸门。旧 canHead 用它来锁死 cover 图位，导致非 3D 角色
             一绑上就显示「先绑角色」的空占位（用户实测的"重新搞背景板占位"）。 */
          hasBanner: !!c.banner,
          active: !!c.is_active
        };
      });
      if (!Object.keys(next).length) return false;
      CHARS = next;
      renderChars();
      return true;
    });
  }

  /* ---------- URL 参数：产品链路用 ?theme=<id> 打开 ----------
     /index.html?theme=xxx 与直接双击打开（无参数 → 新建/离线草稿）都走同一条路。 */
  function qParam(k) {
    try {
      var s = String(location.search || '');
      var m = new RegExp('[?&]' + k + '=([^&]*)').exec(s);
      return m ? decodeURIComponent(m[1].replace(/\+/g, ' ')) : '';
    } catch (e) { return ''; }
  }
  var PARAM_THEME = qParam('theme');
  var INLINE = qParam('inline') === '1';   /* 由产品层 iframe 挂进来：关闭时通知宿主 */
  /* ⭐ 2026-10-13 修「AI 配色算出鬼样子」：本页是被宿主 iframe 挂进来的，
     自身没有 data-theme（design_tokens 的 :root 默认是**深色**，浅色只靠
     @media(prefers-color-scheme:light) 兜底）—— 而取色必须按界面**真实**明暗给提示词
     （深色底要亮而鲜、浅色底要偏深才不糊）。原来直接读 documentElement 的 data-theme，
     恒为 null ⇒ 永远按 dark 算 ⇒ 浅色界面下就会得到发艳、发灰的配色。
     优先级：①宿主显式传的 ?mode= ②跟随父页 ③系统偏好；并写回自身，
     让编辑器与它的预览 iframe 渲染也跟界面一致。 */
  var MODE = (function () {
    var q = (qParam('mode') || '').toLowerCase();
    if (q === 'light' || q === 'dark') return q;
    try {
      var pm = window.parent && window.parent.document &&
               window.parent.document.documentElement.getAttribute('data-theme');
      if (pm === 'light' || pm === 'dark') return pm;
    } catch (e) {}
    try { return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'; } catch (e) {}
    return 'dark';
  })();
  try { document.documentElement.setAttribute('data-theme', MODE); } catch (e) {}

  /* ---------- 状态 ----------
     ⭐ v5.9 接入产品链路：st 多了 tid / name / recs 三项。
        - tid   主题包 id，t_pick / t_save / t_bind 全靠它定位落盘目录
        - name  主题名（可改）
        - recs  后端图位记录 {url,name,w,h,focus}。**必须原样保留**：
                落盘时 t_save 要的是这个对象，而 --tm-* 只吃其中的 url。
                早先只在 st.imgs 里存 CSS 串（url(...)），一旦落盘就会把
                用户的 focus / 尺寸信息丢掉，下次打开编辑器缩略图也没了。 */
  var st = { tid: '', name: '我的主题', imgs: {}, recs: {}, colors: null, preset: null,
             bound: null, slot: 'hero', page: 'chat', focus: null,
             stickers: [], selStk: null };   /* 贴纸/表情包（2026-10-12） */
  var history = [];
  var schemes = [];
  /* 后端桥（theme_backend_bridge.js）。ONLINE 只影响文案与提示，
     离线时编辑器仍可用（纯本地预览）—— 不能因为拿不到后端就白屏。 */
  var B = window.ThemeB || null;
  var ONLINE = false;
  var doc = null;
  var reals = {};             /* offset 累加实测：受 display:none / 0 尺寸 影响，可能不准 */
  var boxes = {};             /* 最终画框用的框：优先取 reals，不可信时回退到 rect2fb 的几何推算 */
  var baseScale = 1;          /* fit() 算出的「全局视图」缩放 */
  var cam = { s: 1, tx: 0, ty: 0 };
  var camAnimating = false;   /* v5.6：镜头补间是否正在跑（fit/measure 期间不许碰 host） */
  var fbSize = { w: 1080, h: 740 };

  var $ = function (id) { return document.getElementById(id); };
  /* ⭐ 两个常驻预览框（对话页 / 角色页）：切页只切显隐，不再 iframe.src 重载。
     iframe 始终指向「当前活动页」；frameLoaded 记录各页是否已载入过。 */
  var FRAMES = { chat: $('tv5Frame'), chars: $('tv5FrameB') };
  /* 壁纸版面的专属预览帧（2026-10-12）：每个子页一帧、首访才建才给 src，
     与内容帧同池管理（都挂在 #tv5FrameHost 下、同尺寸同圆角、靠 fg/bg 淡入淡出切换）。 */
  var WALL_FRAMES = {};
  var wallLoaded = {};
  var frameLoaded = { chat: false, chars: false };
  var iframe = FRAMES.chat;
  var host = $('tv5FrameHost');
  var stage = $('tv5Stage');
  var act = $('tv5Act');
  var dim = $('tv5Dim');
  /* ⭐ v5.6 动效库：本目录本来就有 gsap.min.js（3.12.5），首页 / 导航图标一直在用。
     这里必须用**能力判定**而不是假设它加载成功 —— 加载失败时若把面板留在 CSS 的
     opacity:0，用户看到的就是「面板永远不出现」这类静默失效。 */
  var HAS_GSAP = (typeof window.gsap !== 'undefined') && !!window.gsap.fromTo;
  var REDUCE = false;
  try { REDUCE = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches); }
  catch (e) {}

  /* 预览可视区尺寸缓存：镜头补间期间 stage 根本不动，
     没必要每帧 getBoundingClientRect（每帧造一个 DOMRect 也是开销）。
     超过 350ms / 显式 force（fit、resize）才重读。 */
  /* ⚠️ 字段名必须和调用处的 `sr.width / sr.height` 一致：
     这里曾经写成 {w,h}，于是 targetCam 里 `undefined - 80` = NaN ⇒ 整条 transform
     被 CSSOM 拒绝 ⇒ 「点了没放大、浮出按钮失踪」，而界面一个报错都没有。
     NaN 闸门把它兜成了"没放大"，所以现在能靠 console.warn 一眼抓到。别再改回去。 */
  var stageBox = { width: 0, height: 0 }, stageAt = 0;
  function stageSize(force) {
    var now = (window.performance && performance.now) ? performance.now() : new Date().getTime();
    if (force || !stageBox.width || now - stageAt > 350) {
      if (stage) {
        var r = stage.getBoundingClientRect();
        if (r.width && r.height) { stageBox.width = r.width; stageBox.height = r.height; stageAt = now; }
      }
    }
    return stageBox;
  }

  /* ⭐ 预览是 iframe：所有跨帧操作（量坐标 / 写 --tm-*）一律走 postMessage 小桥。
     ⚠️ 真踩到的坑：用户双击 file:// 打开时，iframe 渲染照常、CSS 照常生效，
        但 contentDocument 拿不到 ⇒ 量不到坐标、写不进变量，全部静默失效，
        表现正是「点了四个都不放大 + 添加文件按钮飘在正中间」。postMessage 永远通。 */
  var bridgeBoxes = {};
  var bridgeReady = false;
  var bridgeMergeT = 0;
  var bridgeVars = {};      /* 预览里 --tm-* 的落地情况（跨域下这是唯一能读到的） */
  /* 两个预览框都会往父页回包 —— 只有「当前活动页」的坐标 / 变量才算数，
     否则会把角色页的框套到对话页上（或反之）。 */
  function fromActive(e) {
    try { return !e.source || !iframe || e.source === iframe.contentWindow; } catch (err) { return true; }
  }
  window.addEventListener('message', function (e) {
    var d = e && e.data;
    if (d && d.__te === 'vars') { if (!fromActive(e)) return; bridgeVars = d.data || {}; return; }
    if (!d || d.__te !== 'boxes') return;
    if (!fromActive(e)) return;
    var b = d.data || d.boxes || {};
    if (/(\?|&)debug=1/.test(location.search || '')) console.info('[v5] bridge boxes: ' + Object.keys(b).join(','));
    bridgeBoxes = b;
    bridgeReady = true;
    /* ⚠️ v5.4 续：几何一换（换页 / 换图 / 窗口变化）就得复核一次镜头。
       原来只有 setFocus 里的 320ms 兜底会做这件事，跨页时那个兜底会被
       "pendingFocus 已消费" 跳过 ⇒ 框画的是新页、镜头却还停在旧值上，
       看着就是「框对了但没放大 / 放大倍数不对」。 */
    if (!camAnimating) reassertCam();
    /* 回包到货后要重新合并一次，否则换页后的 boxes 还是旧页的（框与镜头对不上） */
    if (!bridgeMergeT) {
      bridgeMergeT = setTimeout(function () { bridgeMergeT = 0; if (st.focus) measure(); }, 30);
    }
  });
  function post2frame(msg) {
    try { iframe.contentWindow.postMessage(msg, '*'); return true; } catch (e) { return false; }
  }
  /* 主题变更时推给**两个**预览页：切页那一瞬间目标页已经是最新主题，
     不会先闪一下旧样式再被 apply 覆盖。 */
  function postBoth(msg) {
    var ok = false;
    for (var k in FRAMES) {
      try { FRAMES[k].contentWindow.postMessage(msg, '*'); ok = true; } catch (e) {}
    }
    return ok || post2frame(msg);
  }
  function askBoxes() { bridgeReady = false; post2frame({ __te: 'te', act: 'measure' }); }
  /* 几何刷新后的统一复核：镜头跟当前聚焦项对不上，就补一次对焦（有动画在跑就不抢镜头） */
  function reassertCam() {
    var k = st.focus;
    if (!k || !boxes[k] && !bridgeBoxes[k]) return;
    var t = targetCam();
    if (Math.abs(cam.s - t.s) < 0.004 && Math.abs(cam.tx - t.tx) < 1 && Math.abs(cam.ty - t.ty) < 1) return;
    moveCam(t.s, t.tx, t.ty);
  }
  function pushTheme() {
    var vars = {}, has = {}, off = {};
    ORDER.forEach(function (k) {
      var on = !!st.imgs[k] && canFill(k);
      vars[SLOTS[k].varName] = on ? st.imgs[k] : null;
      vars[SLOTS[k].varName + '-focus'] = 'center';
      has[k] = on;
      if (SLOTS[k].offCls) off[k] = !canFill(k);
    });
    /* 页面壁纸（2026-10-12）：逐页变量 + 当前页合成值，口径与生产 theme_manager 完全一致，
       由 _te_bridge.js 落地 ⇒ 预览里看到的背景就是软件里会出现的。 */
    var anyWall = false;
    WALL_PAGES.forEach(function (p) {
      var kk = WALL_PREFIX + p.key;
      var on = !!st.imgs[kk];
      if (on) anyWall = true;
      vars['--tm-wall-' + p.key] = on ? st.imgs[kk] : null;
    });
    var curWk = isWallSlot(st.page) ? st.page : '';
    var curV = (curWk && st.imgs[curWk]) ? st.imgs[curWk] : null;
    vars['--tm-wall-cur'] = curV;
    postBoth({
      __te: 'te', act: 'apply', vars: vars, has: has, off: off,
      edit: { focus: st.focus || '' },
      wall: { pages: anyWall, cur: !!curV, page: curWk ? wallPageOf(curWk) : '' }
    });
  }

  /* ---------- 撤销栈 ---------- */
  function snapshot() {
    history.push(JSON.stringify({ imgs: st.imgs, recs: st.recs, colors: st.colors, preset: st.preset }));
    if (history.length > 30) history.shift();
    $('tv5Undo').disabled = false;
  }
  function undo() {
    if (!history.length) return;
    var s = JSON.parse(history.pop());
    st.imgs = s.imgs; st.recs = s.recs || {}; st.colors = s.colors; st.preset = s.preset;
    $('tv5Undo').disabled = !history.length;
    applyAll(); renderLib(); renderSlots(); renderSwatches();
    autoSave('已撤销');
  }

  /* ---------- 主题色（同时写进编辑器自己 + 预览 iframe）
     ⭐ 修「自适配在预览版不生效」：原来只写 documentElement，预览页自己的
        [data-accent="xxx"] 规则与 body 级覆盖会把它盖掉。
        现在 root 与 body 都写，并带 important —— 保证预览里一定看得到。 ---------- */
  function setAccentOn(root, body, c1, c2) {
    if (!root) return;
    var set = function (el, k, v) {
      if (!el) return;
      if (v) el.style.setProperty(k, v, 'important');
      else el.style.removeProperty(k);
    };
    set(root, '--accent-1', c1); set(root, '--accent-2', c2);
    set(body, '--accent-1', c1); set(body, '--accent-2', c2);
  }
  function setAccent(c1, c2) {
    setAccentOn(document.documentElement, document.body, c1, c2);
    try {
      if (doc) setAccentOn(doc.documentElement, doc.body, c1, c2);
    } catch (e) {}
  }

  /* ---------- 图片落点：走 --tm-* 变量 + has-* 类（与生产同路） ---------- */
  /* 图片落点：改走 postMessage 让预览页自己写 --tm-* + 挂 has-*（等价生产 theme_manager.js） */
  function applyImages() { pushTheme(); }
  function applyAll() {
    if (st.colors) setAccent(st.colors[0], st.colors[1]);
    else setAccent(null, null);
    applyImages();
    applyWallPreview();   /* 壁纸版面：预览层随主题一起刷新 */
    markDirty();
  }
  function markDirty() {
    /* ⚠️ 顶栏那格是「保存状态」的地盘（读取中/保存中/已存/没存上）。
       这里只在**离线草稿**模式下描述改动数量 —— 在线时改了会自动存，
       再写「已改 3 处」会跟 autoSave 的「已存」互相覆盖，看起来像保存失败。 */
    if (ONLINE || !saveEl) return;
    var n = 0;
    ORDER.forEach(function (k) { if (st.imgs[k]) n++; });
    WALL_PAGES.forEach(function (p) { if (st.imgs[WALL_PREFIX + p.key]) n++; });
    var el = $('tv5State');
    el.textContent = n ? ('草稿 · 改了 ' + n + ' 处') : (st.preset ? ('预设「' + presetName(st.preset) + '」') : '离线草稿');
    el.classList.toggle('dirty', !!n);
  }
  function presetName(id) {
    for (var i = 0; i < PRESETS.length; i++) if (PRESETS[i].id === id) return PRESETS[i].name;
    return id;
  }

  /* ---------- 左栏 ---------- */
  function renderLib() {
    var box = $('tv5Presets');
    box.innerHTML = '';
    PRESETS.forEach(function (p) {
      var b = document.createElement('button');
      b.className = 'tv5-card' + (st.preset === p.id ? ' on' : '');
      b.dataset.preset = p.id;
      b.title = '以「' + p.name + '」的配色为起点 · 只换颜色，你传过的图都留着';
      b.innerHTML =
        '<div class="tv5-card-prev" style="background-image:' + p.hero + '">' +
        '<span class="tv5-card-dot" style="background:linear-gradient(135deg,' + p.c1 + ',' + p.c2 + ')"></span></div>' +
        '<div class="tv5-card-t"><b>' + p.name + '</b>' +
        (st.preset === p.id ? '<em class="tv5-card-tag">在用</em>' : '') + '</div>';
      b.addEventListener('click', function () { applyPreset(p.id); });
      box.appendChild(b);
    });

    /* ---------- ③ 我的主题：这里是「当前这套长什么样」的活状态，不是入口 ----------
       ⚠️ 原来它是一张可点的卡，点一下走 resetAll()（清空全部）。
          于是 ③ 看起来像个"第 5 个主题"，用户点它是期待"切过去"，
          结果被清空 —— 而清空是破坏性操作，绝不能藏在"选择"里。
          现在它只做展示：当前配色来源 + 已传图数 + 当前配色是谁算的。 */
    var mine = $('tv5Mine');
    mine.innerHTML = '';
    var nImg = ORDER.filter(function (k) { return !!st.imgs[k]; }).length;
    var src = st.preset ? ('「' + presetName(st.preset) + '」预设') :
      (st.colors ? '你的图自动算的' : '还没配色');
    var dotBg = (st.colors && st.colors.length)
      ? ('linear-gradient(135deg,' + st.colors[0] + ',' + st.colors[1] + ')')
      : 'linear-gradient(135deg,var(--accent-1),var(--accent-2))';
    var c = document.createElement('div');
    c.className = 'tv5-mine' + (st.preset ? '' : ' on');
    /* ⭐ 同 renderSlots 的引号陷阱：st.imgs.hero 是 url("/local_img?…")，
       直接拼进双引号 style 属性会被内层双引号截断 ⇒ 这张预览图也不显示。
       转成单引号再嵌。 */
    var heroImg = st.imgs.hero ? st.imgs.hero.replace(/"/g, "'") : '';
    var heroBg = heroImg || (st.colors
        ? 'linear-gradient(135deg,' + st.colors[0] + ' 0%,' + st.colors[1] + ' 100%)'
        : 'linear-gradient(135deg,var(--accent-soft-1),var(--surface-3))');
    c.innerHTML =
      '<div class="tv5-card-prev" style="background-image:' + heroBg + '">' +
      '<span class="tv5-card-dot" style="background:' + dotBg + '"></span></div>' +
      '<div class="tv5-card-t"><b>我的主题</b><em>' + (nImg ? (nImg + '/4 张图') : '还没传图') + '</em></div>' +
      '<div class="tv5-mine-src">配色来源：' + src +
      (schemes.length ? ' · ' + schemes.length + ' 套可选（右下角）' : '') + '</div>';
    mine.appendChild(c);

    /* ② ③ 之间的关系要说清：预设是起点，传图后就以图为准了 */
    var pn = $('tv5PresetNote');
    if (pn) {
      pn.textContent = st.preset
        ? ('当前用「' + presetName(st.preset) + '」的配色 · 传图后会自动改成按你的图算')
        : '挑一个配色起点 · 你传过的图不会被清掉';
    }
    var mn = $('tv5MineNote');
    if (mn) {
      mn.textContent = nImg
        ? ('已传 ' + nImg + ' 张 · 点下方部位继续加，预览会自动放大到那一块')
        : '从零攒一套：点下方任一部位 → 预览自动放大 → 就地添加图片';
    }
  }

  function renderChars() {
    var box = $('tv5Bind');
    box.innerHTML = '';
    var ids = Object.keys(CHARS);
    if (!ids.length) {
      /* 空列表不能让用户干等一句"去设置里加角色"——先给一次重试。
         角色列表常常只是**比主题包慢**（t_list 回来时角色还没读完成），
         一次失败就判死刑会让"稍等一下"变成"必须重启软件"。 */
      var e0 = document.createElement('div');
      e0.className = 'tv5-char-retry';
      var t0 = document.createElement('p');
      t0.className = 'tv5-note warn';
      t0.textContent = B ? '还没读到角色 · 可能正在启动，再试一次' : '连不上后台 · 只改配色，存不了';
      var r0 = document.createElement('button');
      r0.className = 'tv5-btn';
      r0.textContent = '重新读取角色';
      r0.addEventListener('click', function () {
        r0.disabled = true; r0.textContent = '读取中…';
        loadChars().then(function (ok) {
          if (!ok) { r0.disabled = false; r0.textContent = '再试一次'; tip('还是没读到 · 确认软件已在运行'); }
        });
      });
      e0.appendChild(t0); e0.appendChild(r0);
      box.appendChild(e0);
    }
    ids.forEach(function (id) {
      var c = CHARS[id];
      var b = document.createElement('button');
      b.className = 'tv5-char' + (st.bound === id ? ' on' : '');
      b.dataset.char = id;
      b.title = '把这套主题绑给「' + c.name + '」· 切到它时整套跟随（再点一次取消）';
      b.innerHTML = '<span class="tv5-char-av">' + c.name.slice(0, 1) + '</span><b>' + c.name + '</b>' +
        '<em>' + (st.bound === id ? '已绑定 ✓' : (c.active ? '当前在用 · 可绑' : (c.hasBanner ? '自带卡头图' : '可绑'))) + '</em>';
      b.addEventListener('click', function () { bindChar(st.bound === id ? null : id); });
      box.appendChild(b);
    });
    var b2 = document.createElement('button');
    b2.className = 'tv5-char' + (!st.bound ? ' on' : '');
    b2.title = '不绑给任何角色 · 这套只影响全局配色，角色卡头图不可用';
    b2.innerHTML = '<span class="tv5-char-av none">＋</span><b>暂不绑定</b><em>仅全局配色</em>';
    b2.addEventListener('click', function () { bindChar(null); });
    box.appendChild(b2);

    var note = $('tv5BindNote');
    if (st.bound && CHARS[st.bound]) {
      var _bc = CHARS[st.bound];
      note.className = 'tv5-note';
      note.textContent = _bc.hasBanner
        ? '已绑给「' + _bc.name + '」· 她自带的卡头图会自动继承（不用另传背景板）· 一个角色只挂一套'
        : '已绑给「' + _bc.name + '」· 她还没有卡头图：这里传一张，就会自动成为她的卡头图';
    } else if (st.bound) {
      note.className = 'tv5-note';
      note.textContent = '已绑给「' + ((CHARS[st.bound] || {}).name || st.bound) + '」· 切到它时整套跟随';
    } else if (ids.length) {
      note.className = 'tv5-note';
      note.textContent = '上面选一个角色，这套主题就会跟着它走 · 不选也能用，只是角色卡头图用不了';
    } else {
      note.className = 'tv5-note';
      note.textContent = '绑定后可随角色自动切换 · 角色卡头图需要绑定才可用';
    }
  }

  function bindChar(id) {
    snapshot();
    st.bound = id;
    renderChars(); renderSlots(); measure();
    if (!B || !ONLINE || !st.tid) { autoSave('绑定已改'); return; }
    /* 绑定是**双向**的（后端 t_bind 同时写 theme.boundCharacter 与 bindings[cid]），
       落盘前先确保主题包存在，否则会把绑定写到一个不存在的 id 上。 */
    ensureTid().then(function (ok) {
      if (!ok) { toast2('绑定没生效：后端没连上', true); return; }
      B.tBind({ id: st.tid, character_id: id || '' }).then(function (r) {
        if (!r || !r.success) { toast2('绑定没生效：' + ((r && r.error) || '未知原因'), true); return; }
        var cn = id && CHARS[id] ? CHARS[id].name : '';
        tip(cn ? ('已绑给「' + cn + '」· 切到它时整套跟随') : '已解绑 · 只影响全局配色');
        autoSave('绑定已存');
        toHost('bound', { themeId: st.tid, characterId: id || '' });
      });
    });
  }

  /* ---------- 底部部位轨 ---------- */
  function renderSlots() {
    var box = $('tv5Slots');
    box.innerHTML = '';
    var wall = isWallSlot(st.page);   /* 当前版面是不是「页面壁纸」版面 */

    /* 复位项：内容版面 = 全局视图；壁纸版面 = 整页预览。位置固定，操作手感一致。 */
    var all = document.createElement('button');
    /* ⚠️ 复位项与部位项不能同时高亮 —— 之前两个 .on 并排，看着像 bug。
       规则：没聚焦时只亮复位项；聚焦了就只亮那一项。 */
    all.className = 'tv5-slot tv5-slot-all' + (st.focus ? '' : ' on');
    all.dataset.slot = 'all';   /* 与部位项统一：外部脚本 / 自动化测试可统一选择器命中 */
    all.title = wall ? '看这一页背景的铺满效果' : '全局视图 · 看整页效果（点任意部位放大到那一块）';
    all.innerHTML = '<span class="tv5-thumb tv5-thumb-all">全</span>' +
      '<span class="tv5-slot-meta"><b>' + (wall ? '整页预览' : '全局视图') + '</b><em>' +
      (wall ? '背景铺满的样子' : '看整页效果') + '</em></span>';
    all.addEventListener('click', function () {
      if (wall) { applyWallPreview(); tip('这一页的背景就是所见的样子'); return; }
      setFocus(null);
    });
    box.appendChild(all);

    /* ⭐ 只画「当前版面」的槽位 —— 点版面，这里就跟着换（用户诉求）。
       壁纸版面只有一项（这页的背景）；内容版面是该页的图位。 */
    slotsOfScene(st.page).forEach(function (k) {
      var s = metaOf(k);
      var v = st.imgs[k];
      var recK = st.recs[k] || {};
      /* 头图继承态（2026-10-12 用户裁决：头图与背景板合二为一）：
         cover 的图来自「绑定角色的自带 banner」——角色头图优先，
         这里如实标出来，不再像过去那样要求用户重传一张背景板占位。 */
      var inh = !!recK.inheritedFromCharacter;
      var locked = !!s.needBind && !canFill(k);
      var b = document.createElement('button');
      b.className = 'tv5-slot' + (st.slot === k && st.focus === k ? ' on' : '') +
                    (locked ? ' locked' : '') + (st.focus === k ? ' focusing' : '');
      b.dataset.slot = k;
      /* ⚠️ v 必须在 title 之前取：原代码把 var v = st.imgs[k] 写在 title 的**下一行**，
         var 声明提升 ⇒ title 里的 v 恒为 undefined ⇒「已配图」提示从来没出现过（静默失效）。 */
      b.title = s.label + ' · ' + (locked ? '先绑角色'
                : (inh ? '继承自角色头图（角色自带优先；删掉角色头图才轮到这里）'
                       : (v ? '已配图，点它放大' : s.spec)));
      /* ⭐ 引号陷阱（问题②根因）：st.imgs[k] 形如 url("/local_img?path=…")，
         直接拼进双引号 HTML 属性 style="background-image:url("/local_img?…")" 时，
         内嵌的双引号会提前闭合属性 ⇒ 整个 style 报废 ⇒ 缩略图不显示。
         把内嵌双引号转成单引号即可安全嵌进双引号属性。 */
      var vq = v ? v.replace(/"/g, "'") : '';
      b.innerHTML =
        '<span class="tv5-thumb"' + (v ? ' style="background-image:' + vq + '"' : '') + '>' +
        (v ? '' : '空') + '</span>' +
        '<span class="tv5-slot-meta"><b>' + s.label + '</b><em>' +
        (locked ? '先绑角色'
                : (inh ? '继承自角色头图'
                       : (v ? (s.isWall ? '已配 · 点它换' : '已配 · 点它放大') : s.spec))) + '</em></span>';
      b.addEventListener('click', function () { select(k); });
      box.appendChild(b);
    });
  }

  /* ---------- 配色方案轨 ---------- */
  function renderSwatches() {
    var box = $('tv5Sw');
    box.innerHTML = '';
    if (!schemes.length) {
      var e = document.createElement('span');
      e.className = 'tv5-sw-empty';
      e.textContent = '还没传图 · 传了背景（或立绘 / 封面 / 头像），这里会出现几套 AI 配色让你挑';
      box.appendChild(e);
      return;
    }
    schemes.forEach(function (sc) {
      var b = document.createElement('button');
      b.className = 'tv5-sw' + (st.colors && st.colors[0] === sc.c1 ? ' on' : '');
      b.dataset.c1 = sc.c1;
      b.title = sc.c2 + '　' + (sc.note || '');
      b.innerHTML = '<i style="background:' + sc.c1 + '"></i><i style="background:' + sc.c2 + '"></i>' +
        '<span>' + sc.name + '</span>';
      b.addEventListener('click', function () {
        snapshot();
        palettePicked = true;   /* 用户自己选过 → AI 回来时不再覆盖他的选择 */
        st.colors = [sc.c1, sc.c2];
        setAccent(sc.c1, sc.c2);
        renderSwatches(); markDirty();
        tip('已用「' + sc.name + '」' + (sc.note ? ' · ' + sc.note : ''));
        autoSave('配色已存');
      });
      box.appendChild(b);
    });
  }

  /* ---------- 选部位 → 切页 → 聚焦放大 ---------- */
  function canFill(k) {
    var s = metaOf(k);
    if (!s || !s.needBind) return true;
    /* 头图与背景板合并后：只要绑定了角色就能设「角色卡头图」——
       不再要求角色有 3D 形象（旧判据把非 3D 角色的 cover 锁死成
       「先绑角色」空占位，用户实测的"重新搞背景板占位"就是它）。
       角色自带头图时该图位显示「继承自角色头图」，上传仅在角色没有头图时才需要。 */
    return !!st.bound;
  }
  function select(k) {
    var s = metaOf(k);
    /* 锁住的槽位：给一句人话，别点了没反应（"点了没生效"多半就是这么来的） */
    if (!canFill(k)) {
      st.slot = k; renderSlots();
      tip(s.needBind ? '先把上面「应用到角色」绑到一个角色，才能设它' : '这个部位现在用不了');
      measure(); return;
    }
      if (st.slot === k && st.focus === k) { setFocus(null); return; }   /* 再点一次退回全局 */
      st.slot = k;
      /* ⚠️ 跨页时也必须先把轨道重画一遍：loadPage 要等 iframe 才回来，
         中间这段时间底部还亮着上一项 —— 用户看到的就是「点了头像，亮的还是头像」。 */
      renderSlots();
      if (s.on !== st.page) { gotoScene(s.on); setTimeout(function () { select(k); }, 80); return; }
      /* ⭐ 壁纸槽位同样走「框选 + 运镜 + 气泡」，不再一点就直接弹文件框。
         整页就是它的落点 ⇒ 框选画的是整页描边、镜头停在全局倍率，
         气泡（小卡）照旧从底部那一项飞出来 —— 与「主页背景(hero)」完全一致的手感。
         （2026-10-12 用户反馈：点了直接弹文件选择器，框选和运镜都没出现。） */
      measure(); setFocus(k);
  }

  /* ⭐ 跟随聚焦：把 host 用 translate + scale 推近到目标块
     ⚠️ v5.6 换了驱动方式 —— 镜头补间改由 CSS transition 跑（moveCam 写一次终值），
        JS 不再逐帧写 transform。原因见 moveCam 的注释：这是「移动缩放视角看起来巨卡」的正解。
     ⚠️ 两个沿用下来的坑：
       ① 任何时刻只有一个写入者：fit() 在聚焦态下绝不写 host（只更新 baseScale）。
       ② 全局视图的 scale 用当前实测的 baseScale（不是 fit() 那一刻的旧值）。
  */
  function setFocus(k) {
    st.focus = k;
    renderSlots();
    /* 聚焦态要告诉预览页：让它藏掉主页那四个取景角标（.panel .tick）。
       不换图时 applyAll 不会跑、pushTheme 也不会发，所以必须在这里单独推一次。
       少了这一句，编辑器描边和主页 tick 会叠在一起 —— 看着就是"四个角标错位"。 */
    pushTheme();
    var t = targetCam();
    /* 坐标可能还没从小桥报回来 ⇒ 先要一次，下一帧再推（不然又变成"点了没放大"） */
    if (!boxes[k]) {
      askBoxes();
      setTimeout(function () { if (st.focus === k) { var t2 = targetCam(); moveCam(t2.s, t2.tx, t2.ty); } }, 90);
    } else {
      moveCam(t.s, t.tx, t.ty);
    }
    // ⭐ 兜底：本机跑得通不代表用户那边也跑得通。某些 WebView（含负载高时）rAF 会被饿死，
    //   一帧都不跑 ⇒ 镜头停在起点、浮出按钮也不跟着更新 ⇒ 用户看到的就是"点了没放大、也没按钮"。
    //   所以 320ms 后检查一次：还在起点就直接落到终态（snap），保证"点了必有反应"。
    /* ⚠️ 320ms 兜底里必须**重新**取一次 targetCam()：
       跨页时点下去那一刻 boxes 还是上一页的，拿旧坐标当终态，
       320ms 后（新页坐标已回来）就会把镜头钉在错误倍率上 —— 看着就是「点了没放大」。
       重新取一次，顺便天然覆盖「坐标晚到」这一路。 */
    setTimeout(function () {
      if (st.focus !== k) return;
      var t2 = targetCam();
      if (Math.abs(cam.s - t2.s) < 0.004 && Math.abs(cam.tx - t2.tx) < 1 && Math.abs(cam.ty - t2.ty) < 1) return;
      moveCam(t2.s, t2.tx, t2.ty, true);
      reassertCam();
    }, 320);
    reassertCam();
    moveCam(t.s, t.tx, t.ty);
    $('tv5Global').classList.toggle('show', !!st.focus);
  }

  /* ⭐ v5.6 镜头驱动方式换掉 —— 这是「移动缩放视角看起来巨卡」的正解。
     为什么不再用 rAF 逐帧写 transform：
       transform 属于 compositor-animatable 属性。交给 CSS transition 后，整段 580ms
       的插值在**合成线程**跑完，主线程全程零参与；而 rAF 每帧写 style 意味着每帧都要
       ① 走一次主线程样式计算 ② 把 1080×740（内含完整页面 + iframe）的层重新光栅。
       用户看到的「推近巨卡」+「添加文件一出来就顿」，主因就是 ②（DOM 重建只是小头）。
     ⚠️ 跟 v5.1 那次「transition 不跑」不是一回事：那次是 fit()/measure() 在同一帧
        按**起点**把 transform 覆盖重算，transition 每次都从当前值重启，看着像瞬移。
        现在 fit() 在聚焦态下只更新 baseScale 这个变量，moveCam 是唯一写入者。 */
  var CAM_DUR = 580, camRelT = 0;
  function moveCam(s2, tx2, ty2, snap) {
    if (!host) return;
    /* 第二道 NaN 闸门：cam 一旦被污染就再也回不来了，入口先把三个值修成有限数。 */
    if (!isFinite(s2)) s2 = cam.s;
    if (!isFinite(tx2)) tx2 = cam.tx;
    if (!isFinite(ty2)) ty2 = cam.ty;
    if (!isFinite(s2)) s2 = baseScale || 1;
    if (!isFinite(tx2)) tx2 = 0;
    if (!isFinite(ty2)) ty2 = 0;
    var anim = !snap && !REDUCE;
    camAnimating = anim;
    var prevT = host.style.transition;
    if (!anim) host.style.transition = 'none';   /* 静态落位（fit/兜底）不能带补间，否则会滑一下 */
    cam.s = s2; cam.tx = tx2; cam.ty = ty2;
    host.style.transform = 'translate(' + tx2.toFixed(1) + 'px,' + ty2.toFixed(1) + 'px) scale(' + s2.toFixed(4) + ')';
    host.style.setProperty('--hi-bw', (2 / (s2 || 1)).toFixed(2) + 'px');
    host.style.setProperty('--inv', (1 / (s2 || 1)).toFixed(4));
    if (!anim) { void host.offsetWidth; host.style.transition = prevT || ''; }
    /* 派生视觉全部按**终态**摆一次：动画期间不再有任何逐帧写入（主线程零工作）。 */
    paintDim(); paint(); paintAct();
    clearTimeout(camRelT);
    if (anim) camRelT = setTimeout(function () { camAnimating = false; camRelT = 0; }, CAM_DUR + 180);
    else { camAnimating = false; camRelT = 0; }
  }

  /* 目标镜头：把 cam 写成 translate + scale（origin 是 host 中心 540,370） */
  var nanWarned = 0;
  function targetCam() {
    var base = baseScale;
    if (!stage) return { s: base, tx: 0, ty: 0 };
    var k = st.focus, b = k && boxes[k];
    if (!b || !b.w || !b.h) return { s: base, tx: 0, ty: 0 };
    var sr = stageSize();
    /* 目标块放大后约占预览区 52% 宽 / 62% 高；倍数钳在 base ~ 3.2 */
    var desired = Math.min((sr.width - 80) * 0.52 / b.w, (sr.height - 60) * 0.62 / b.h);
    var S = Math.max(base, Math.min(desired, 3.2));
    var bcx = b.x + b.w / 2, bcy = b.y + b.h / 2;
    /* ⚠️ NaN 闸门（血泪）：一旦这里吐出 NaN 且没人拦，animateCam 会把 cam.s 写成 NaN，
       于是 `host.style.transform = '... scale(NaN)'` 整条被 CSSOM 拒绝 ⇒ host 永远停在
       旧 transform（看着"没放大"），cam 却是 NaN ⇒ 浮出按钮的 cx 也 NaN  ⇒
       transform 同样被拒 ⇒ 按钮永远停在 CSS 初始的 -9999px。界面上一点报错都没有，
       但「点了没反应」—— 最难查的那一类。这里就地兜住，并只报一次以便定位。 */
    if (!isFinite(S) || !isFinite(desired)) {
      if (!nanWarned) {
        nanWarned = 1;
        console.warn('[v5] targetCam 算出 NaN，已退回全局视图 · 坐标=', JSON.stringify(b), ' 预览区=', sr.width, sr.height);
      }
      S = base;
    }
    if (!isFinite(bcx) || !isFinite(bcy)) { S = base; bcx = 540; bcy = 370; }
    return { s: isFinite(S) ? S : base, tx: S * (540 - bcx), ty: S * (370 - bcy) };
  }

  /* ---------- 版面轨（2026-10-12）：内容版面 + 各页背景，都可点 ---------- */
  function renderScenes() {
    var box = $('tv5Scenes');
    if (!box) return;
    box.innerHTML = '';
    SCENES.forEach(function (sc) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'tv5-scene' + (st.page === sc.key ? ' on' : '');
      b.dataset.page = sc.key;
      b.dataset.kind = sc.kind;
      b.textContent = sc.name;
      b.title = sc.kind === 'wall'
        ? ('给「' + sc.name + '」配整页背景')
        : ('预览 ' + sc.name + '（点下方图位可放大到那一块）');
      b.addEventListener('click', function () { gotoScene(sc.key); });
      box.appendChild(b);
    });
  }
  /* 切版面：内容版面走 iframe 预览；壁纸版面同样走【真实页面 iframe】——
     只是页面上铺的是「草稿壁纸」而不是槽位图。两条分支做同一件事：
     把槽位轨切成这个版面的图位。（2026-10-12 用户反馈"预览不真实"的修正：
     壁纸版面不再用灰底画布假装，见 WALL_PAGES.src。） */
  function gotoScene(key) {
    var scene = null;
    for (var i = 0; i < SCENES.length; i++) if (SCENES[i].key === key) scene = SCENES[i];
    if (!scene) return;
    st.page = key;
    st.focus = null;                    /* 切版面必须退回全局：否则镜头钉在上一个部位上 */
    if ($('tv5Global')) $('tv5Global').classList.remove('show');
    renderScenes();
    renderSlots();
    renderStickers();                   /* 换版面 = 贴纸层切到这一页的贴纸 */
    if (scene.kind === 'wall') {
      showWallScene(scene.page);
      return;
    }
    hideWallPlaceholder();
    loadPage(key);                      /* 内容版面：沿用原有 iframe 流程（含跨页收尾） */
    applyAll();
  }

  /* ---------- 壁纸版面：真实页面预览（2026-10-12 重做） ---------- */

  /* 取/建某个子页的预览帧。chat / characters 直接复用内容版面的两帧
     （同一份真实页面，壁纸由小桥/直写落到帧内）；其余子页首访才建才给 src。 */
  function wallFrameFor(pg) {
    /* chat / characters 直接复用内容版面那两帧（同一份真实页面）——
       但在切过去之前，先把任何已存在的产品页壁纸帧回收掉。 */
    if (pg === 'chat') { releaseWallFrames(); return FRAMES.chat; }
    if (pg === 'characters') { releaseWallFrames(); return FRAMES.chars; }
    /* ⚠️ 关键顺序：先回收【除 pg 外】的壁纸帧，再做「已存在就复用」的早退。
       早先把回收写在早退之后 ⇒ 复用分支永远不回收，切过几页就同时养着好几个
       活页面（各自带轮询 / 定时器）⇒ 宿主被拖到无响应（用户实测的「卡死 / 闪退」）。 */
    releaseWallFrames(pg);
    var fr = WALL_FRAMES[pg];
    if (fr) return fr;
    var def = wallPageDef(pg);
    if (!def) return null;
    fr = document.createElement('iframe');
    fr.className = 'tv5-frame tv5-wallf bg';
    fr.dataset.page = WALL_PREFIX + pg;
    fr.title = '预览 · ' + def.label;
    fr.setAttribute('scrolling', 'no');
    host.insertBefore(fr, $('tv5Hot'));
    WALL_FRAMES[pg] = fr;
    fr.addEventListener('load', function () { onWallFrameLoad(fr, pg); });
    return fr;
  }
  /* ⭐ 回收壁纸预览帧：只保留 keepPg 这一张，其余全部释放。
     ⚠️ 这是 2026-10-12「软件卡死 / 闪退」的直接修正 ——
        产品子页（记忆 / 日报 / 设置 / 通话…）不是静态稿：它们各自带着后端轮询、
        定时器与数据加载（记忆页还会触发 LLM 整理）。早先每页一帧、切过就常驻，
        等于在编辑器里同时养着 6 个活页面 ⇒ 宿主被拖死。
        现在切版面就把上一帧从 DOM 摘除 ⇒ 该 iframe 的浏览上下文被销毁，
        它的脚本 / 定时器 / 轮询立刻停止。全程最多「内容帧 ×2 + 壁纸帧 ×1」三段存活。
     ⚠️ 摘除后不要再对旧帧写 src —— 那会在已脱离文档的帧上再触发一次 load。 */
  function releaseWallFrames(keepPg) {
    Object.keys(WALL_FRAMES).forEach(function (k) {
      if (keepPg && k === keepPg) return;
      var old = WALL_FRAMES[k];
      if (!old) return;
      try { if (old.parentNode) old.parentNode.removeChild(old); } catch (e) {}
      delete WALL_FRAMES[k];
      wallLoaded[k] = false;
    });
  }
  function wallFrameReady(pg) {
    if (pg === 'chat') return frameLoaded.chat === true;
    if (pg === 'characters') return frameLoaded.chars === true;
    return wallLoaded[pg] === true;
  }
  /* 帧载入完成：注入「预览不可交互」样式、把 doc 对准活动帧、铺草稿壁纸、
     淡入真页盖过占位层。 */
  function onWallFrameLoad(fr, pg) {
    wallLoaded[pg] = true;
    var d = null;
    try { d = fr.contentDocument || (fr.contentWindow && fr.contentWindow.document) || null; } catch (e) {}
    if (d && d.head) {
      try {
        var s = d.createElement('style');
        s.textContent = '*{pointer-events:none !important}';
        d.head.appendChild(s);
      } catch (e) {}
    }
    if (isWallSlot(st.page) && wallPageOf(st.page) === pg) {
      if (d) doc = d;                   /* fit() 的 --te-fit / 镜头落位都要对着活动帧的文档 */
      applyWallToFrame(fr, pg);         /* 页面自己的 theme_manager 可能已刷过一次，再钉一遍草稿 */
      fit();
      hideWallPlaceholder();
    }
  }
  /* 把「草稿壁纸」写进帧内：变量 + 属性双写，与生产 theme_manager 完全同口径。
     同源直写优先（编辑器与页面同源，必然可达）；拿不到 contentDocument 时
     postMessage 兜底 —— 产品页 theme_manager.js 里有 __tm_preview 监听接住。 */
  function applyWallToFrame(fr, pg) {
    if (!fr) return;
    var v = st.imgs[WALL_PREFIX + pg] || '';
    var win = null;
    try { win = fr.contentWindow; } catch (e) {}
    if (win && win.document && win.document.body) {
      try {
        var d = win.document, root = d.documentElement;
        if (v) {
          root.style.setProperty('--tm-wall-' + pg, v);
          root.style.setProperty('--tm-wall-cur', v);
        } else {
          root.style.removeProperty('--tm-wall-' + pg);
          root.style.removeProperty('--tm-wall-cur');
        }
        [root, d.body].forEach(function (el) {
          if (!el) return;
          el.setAttribute('data-tm-pages', v ? 'on' : 'off');
          el.setAttribute('data-tm-wall', v ? 'on' : 'off');
          el.setAttribute('data-tm-page', pg);
        });
      } catch (e) {}
    }
    /* 同源直写之外【再发一条】__tm_preview：产品页 theme_manager 记下覆写后，
       它自己随后的任何 repaint 都会继续用草稿值 —— 否则载入后一次重刷就把
       直写的内联变量盖回去，用户看到「壁纸闪一下就没了」。 */
    try {
      if (win) {
        var raw = v ? v.replace(/^url\(["']?/, '').replace(/["']?\)\s*$/, '') : '';
        win.postMessage({ __tm_preview: true, wall: { page: pg, url: raw } }, '*');
      }
    } catch (e) {}
  }
  /* 统一的活动帧切换：内容帧 + 壁纸帧一起让位/上台（只切 fg/bg，绝不动尺寸）。 */
  function activateFrame(fr) {
    Object.keys(FRAMES).forEach(function (k) {
      var f = FRAMES[k];
      if (!f || f === fr) return;
      f.classList.remove('fg'); f.classList.add('bg');
    });
    Object.keys(WALL_FRAMES).forEach(function (k) {
      var f = WALL_FRAMES[k];
      if (!f || f === fr) return;
      f.classList.remove('fg'); f.classList.add('bg');
    });
    fr.classList.remove('bg'); fr.classList.add('fg');
    iframe = fr;
  }
  function showWallScene(pg) {
    var fr = wallFrameFor(pg);
    if (!fr) return;
    /* 占位层先顶上：把草稿壁纸先铺出来（视觉零空窗），真页载入完成后淡入盖过它 */
    showWallPlaceholder(pg);
    activateFrame(fr);
    if (!fr.getAttribute('src')) fr.src = (wallPageDef(pg) || {}).src || '';
    applyWallToFrame(fr, pg);           /* 已载入过的帧立刻铺壁纸（未载入的由 load 收尾） */
    applyAll();                         /* 对齐内容版面：配色/主题变量同步推送 */
    fit();
  }
  /* 占位层：真页未就绪时的过渡画布 —— 铺草稿壁纸 + 载入提示，
     真页淡入后它退到透明（保留在 DOM，下一页继续复用）。 */
  function showWallPlaceholder(pg) {
    var el = $('tv5WallPrev');
    if (!el) return;
    el.hidden = false;
    el.classList.remove('off');
    var v = st.imgs[WALL_PREFIX + pg] || '';
    el.style.backgroundImage = v || 'none';
    el.setAttribute('data-empty', v ? 'off' : 'on');
    el.setAttribute('data-loading', 'on');
    var tag = $('tv5WallTag');
    if (tag) tag.textContent = wallPageName(pg) + ' · 整页背景';
  }
  function hideWallPlaceholder() {
    var el = $('tv5WallPrev');
    if (!el) return;
    el.classList.add('off');            /* 淡出而不是 display:none —— 过渡动画才跑得起来 */
    el.setAttribute('data-loading', 'off');
    var t = setTimeout(function () { if (el.classList.contains('off')) el.hidden = true; }, 360);
    void t;
  }
  /* 旧调用点的统一入口（applyAll / renderSlots 的「整页预览」项 / 换图收尾）：
     壁纸版面的「刷新预览」——真页已就绪就重铺壁纸并撤占位；没就绪就先让占位层顶住。 */
  function applyWallPreview() {
    if (!isWallSlot(st.page)) return;
    var pg = wallPageOf(st.page);
    var fr = (pg === 'chat') ? FRAMES.chat : (pg === 'characters') ? FRAMES.chars : WALL_FRAMES[pg];
    if (!fr) return;
    if (wallFrameReady(pg)) {
      applyWallToFrame(fr, pg);
      hideWallPlaceholder();
    } else {
      showWallPlaceholder(pg);
      if (!fr.getAttribute('src')) fr.src = (wallPageDef(pg) || {}).src || '';
    }
    fit();
  }

  /* ---------- 页面切换（两个常驻 frame：只切显隐，不重载） ---------- */
  function loadPage(key, focusAfter) {
    st.page = key;
    [].forEach.call(document.querySelectorAll('.tv5-scene'), function (b) {
      b.classList.toggle('on', b.dataset.page === key);
    });
    doc = null; boxes = {}; bridgeReady = false;
    pendingFocus = focusAfter || null;
    showFrame(key);
  }
  /* 切到某一页：
     · 首次用到才给 src（不在打开编辑器时就白跑一个重页面）；
     · 之后只切显隐 + 立刻按当前主题/目标部位收尾 —— 没有 iframe.src 重载，
       所以不会「切过去卡一下、切回来再卡一下」。 */
  function showFrame(key) {
    var next = FRAMES[key] || FRAMES.chat;
    if (next !== iframe) {
      if (iframe) { iframe.classList.remove('fg'); iframe.classList.add('bg'); }
      next.classList.remove('bg'); next.classList.add('fg');
      iframe = next;
    }
    if (frameLoaded[key] !== true) {
      if (!next.getAttribute('src')) {
        frameLoaded[key] = 'loading';
        next.src = PAGES[key].src;
      }
      return;                       /* 首次载入中：交给 load 事件收尾 */
    }
    /* 已载入过：立刻收尾，无需等 load */
    askBoxes();
    applyAll();
    fit();
    var re = pendingFocus || st.focus;
    if (pendingFocus) { var k0 = pendingFocus; pendingFocus = null; setFocus(k0); }
    else if (re) setFocus(re);
    setTimeout(reassertCam, 80);
  }
  var pendingFocus = null;

  var frameBrokenT = 0;
  /* ⚠️ 两个 frame 都要挂 load；但只有「当前活动页」的 load 才驱动 UI
     （后台页的 load 只记一个「已载入」状态，供之后切过去时免重载）。 */
  function onFrameLoad(fr, key) {
    if (!fr.getAttribute('src')) return;   /* 还没给 src 时的 about:blank load 不算数 */
    frameLoaded[key] = true;
    if (fr !== iframe) return;
    try { doc = iframe.contentDocument || iframe.contentWindow.document; } catch (e) { doc = null; }
    /* ⚠️ v5.4：原来这里 `if (!doc) { 提示载入失败; return; }` 是一条致命早退 ——
       跨域 / file:// 下 doc 恒为 null，于是：applyAll() 不跑、fit() 不跑、
       更要命的是 pendingFocus 永远没人消费 ⇒ 点「角色卡头图」跨到角色页后，
       相机停在上一项的镜头上，高亮框画的是新的、镜头却是旧的，
       用户看到的正是「角色卡点了也没放大」。
       修法：拿不到 doc 只说明「进不去内部 DOM」，不代表这一页没载入 ——
       坐标走小桥、样式走 postMessage，这两条路跟 doc 无关，照样走完再收尾。 */
    if (!doc) {
      $('tv5PrevHint').textContent = '预览已载入 · 点下方任一项会自动放大到那一块';
      clearTimeout(frameBrokenT);
      /* 兜底：万一桥还没回包，1.2s 后强制补一次对焦，绝不让镜头停在错误位置 */
      frameBrokenT = setTimeout(function () {
        var k = pendingFocus || st.focus;
        if (!k) return;
        var t = targetCam();
        if (Math.abs(cam.s - t.s) < 0.004 && Math.abs(cam.tx - t.tx) < 1 && Math.abs(cam.ty - t.ty) < 1) return;
        moveCam(t.s, t.tx, t.ty, true);
        reassertCam();
      }, 1200);
    }
    try {
      doc.documentElement.setAttribute('data-theme',
        document.documentElement.getAttribute('data-theme') || 'light');
      /* 本编辑器专供 1080×740 预览：给页面打个标记，预览专有规则都挂在这个类下，
         绝不外泄到产品页面（产品是自适应宽度的）。 */
      if (doc.documentElement) {
        doc.documentElement.classList.add('is-te');
        doc.documentElement.classList.add(key === 'chars' ? 'is-chars' : 'is-chat');
      }
    } catch (e) {}
    try {
      /* ① 预览不可交互；② 页面内任何 sticky/fixed 在预览里都别赖着（否则推近放大时会浮在镜头外） */
      var s = doc.createElement('style');
      s.textContent = '*{pointer-events:none !important}'
        + '.app,.main,.content,.wrap,.chat,.stage,.side{position:relative !important}';
      doc.head.appendChild(s);
    } catch (e) {}
    /* ⚠️ v5.4 续：fit() 会按「当前 focus」重算并直接写死 cam，
       而这里它跑在 setFocus 之前 —— 一旦那时 boxes 还没从小桥回来，
       targetCam() 只能退回 base ⇒ 镜头被按回全局，等 setFocus 动画跑完又被
       下一帧/下一次 load 事件复位，用户看到的就是「框画出来了、但没放大」。
       所以 fit() 一律不许在聚焦态下碰 cam， anim 结束后再复核一次焦点。 */
    applyAll();
    var reassert = st.focus || pendingFocus;
    fit();
    /* 壁纸版面复用本帧（wall@chat / wall@characters）：真页就绪 → 铺草稿壁纸 + 撤占位层 */
    if (isWallSlot(st.page) && wallPageOf(st.page) === key) {
      applyWallToFrame(FRAMES[key], wallPageOf(st.page));
      hideWallPlaceholder();
    }
    if (pendingFocus) { var k0 = pendingFocus; pendingFocus = null; setFocus(k0); }
    else if (reassert && reassert !== st.focus) { var k1 = reassert; setFocus(k1); }
    setTimeout(reassertCam, 140);   /* 预览刚载入，坐标（小桥回包）可能还没到 */
  }
  FRAMES.chat.addEventListener('load', function () { onFrameLoad(FRAMES.chat, 'chat'); });
  FRAMES.chars.addEventListener('load', function () { onFrameLoad(FRAMES.chars, 'chars'); });

  /* ---------- 缩放（全局视图） ---------- */
  function fit() {
    if (!host || !stage) return;
    var aw = 1080, ah = 740, pad = 36;
    stageSize(true);
    var bw = stage.clientWidth - pad, bh = stage.clientHeight - pad;
    var s = Math.min(bw / aw, bh / ah);
    if (!isFinite(s) || s <= 0) s = 1;
    baseScale = s;
    /* ⭐ 等比缩放到 1080×740 后，.app 自己也要跟着缩 —— 否则内容溢出、右 / 下被裁掉。
       这里只加 scale，不改 .app 的尺寸（改了反而会让 .side 之类相对宽度错位）。 */
    try {
      if (doc && doc.documentElement) {
        doc.documentElement.style.setProperty('--te-fit', String(s));
      }
    } catch (e) {}
    measure();
    /* 聚焦态下 fit() 只负责重算 baseScale，绝不写 cam —— 镜头归 setFocus 独家管理，
       否则「镜头动画刚跑完又被 fit 拍回全局」= 用户眼里的「点了没放大」。 */
    if (st.focus) {
      /* 聚焦态下 fit() 只重算 baseScale，镜头归 moveCam 独家管理 —— 见 moveCam 注释 */
      paint(); paintAct(); paintDim();
      return;
    }
    /* ⚠️ 这里必须走 moveCam(..., snap)：fit() 是初始化 / 尺寸变化的**静态落位**，
       若带着 CSS transition 写 transform，页面刚打开时预览会「滑」进来一大段。 */
    var t = targetCam();
    moveCam(t.s, t.tx, t.ty, true);
    paint(); paintAct(); paintStkToolbar();
  }

  /* ---------- 定位：以 getBoundingClientRect 为主，offset 累加只当兜底 ----------
     ⚠️ 血泪教训（v5.1 两版都死在这）：
       上一版用 offsetLeft/offsetTop 沿 offsetParent 链累加，理由是"不受 transform 影响"。
       但那个链在 WebView2 里并不可靠 —— 实测「立绘」量到 287×250 且 x=754、「头像」量到
       203×69（都是错的），于是点「立绘」放大的是一块跑偏的小框，用户看到的就是"点了没反应"。
       现在改成：**getBoundingClientRect 作为主路径**（所见即所得），offset 累加仅作兜底。
     预览页被 <style> 钉成 1080×740，所以 rect 单位就是 fb 单位，1:1 直接可用。 */
  function rect2fb(el) {
    var r = el.getBoundingClientRect();
    if (!r.width || !r.height) return null;
    return { x: Math.round(r.left), y: Math.round(r.top),
             w: Math.max(1, Math.round(r.width)), h: Math.max(1, Math.round(r.height)) };
  }
  function offset2fb(el) {
    var w = el.offsetWidth, h = el.offsetHeight;
    if (!w || !h) return null;
    var x = el.offsetLeft, y = el.offsetTop, op = el.offsetParent;
    var d = doc.documentElement;
    while (op && op !== doc.body && op !== d) { x += op.offsetLeft; y += op.offsetTop; op = op.offsetParent; }
    return { x: x, y: y, w: w, h: h };
  }
  /* 合理性闸门：离谱的框宁可不画，也不要画到用户看不懂的地方去 */
  function sane(b) {
    if (!b) return false;
    if (!b.w || !b.h) return false;
    if (b.w < 4 || b.h < 4) return false;
    if (b.x < -6 || b.y < -6) return false;
    if (b.x > fbSize.w - 6 || b.y > fbSize.h - 6) return false;
    return true;
  }
  function pick(sel) {
    if (!doc) return null;
    for (var i = 0; i < sel.length; i++) {
      var e = doc.querySelector(sel[i]);
      if (e && e.getBoundingClientRect().width > 1) return e;
    }
    return null;
  }
  function inset(r, v) {
    return { x: Math.round(r.x + v), y: Math.round(r.y + v),
             w: Math.max(1, Math.round(r.w - 2 * v)), h: Math.max(1, Math.round(r.h - 2 * v)) };
  }
  /* 立绘：舞台不可见时，按「贴在右侧、底部对齐、不压在输入区」估一块 */
  function standFallback(viewport, content) {
    var h = Math.max(1, Math.round(content.h * 0.94));
    var w = Math.max(1, Math.round(Math.min(content.w * 0.34, h * 0.62)));
    return { x: content.x + content.w - w, y: content.y + content.h - h, w: w, h: h };
  }

  function measure() {
    reals = {}; boxes = {};
    /* 小桥优先：跨域 / file:// 下这是唯一靠得住的通道。
       ⚠️ 用缓存而不是只认"本次刚回包"，否则一次 measure() 把 boxes 清空、
       下一次拿不到回包就永远是空 —— 高亮框不画（看着像"点了没放大"）。 */
    if (bridgeBoxes && Object.keys(bridgeBoxes).length) {
      ORDER.forEach(function (k) {
        var b = bridgeBoxes[k];
        if (b && sane(b)) { reals[k] = b; boxes[k] = b; }
      });
      ORDER.forEach(function (k) { if (!canFill(k) && boxes[k]) delete boxes[k]; });
      paint();
      return;
    }
    if (!doc) { paint(); askBoxes(); return; }
    ORDER.forEach(function (k) {
      var el = null;
      try { el = SLOTS[k].anchor(doc); } catch (e) { el = null; }
      if (!el) return;
      var r = rect2fb(el);
      if (sane(r)) { reals[k] = r; boxes[k] = r; return; }
      /* rect 拿不到（display:none / 尺寸为 0）才退回 offset 累加 */
      var o = offset2fb(el);
      if (sane(o)) { reals[k] = o; boxes[k] = o; }
    });
    boxes = bboxes();       /* 能推算的都补上 */
    /* 锁住的槽位（未绑角色）不该有框 —— 否则会画出一个点不动的框，看着像坏了 */
    ORDER.forEach(function (k) { if (!canFill(k) && boxes[k]) delete boxes[k]; });
    paint();
    askBoxes();             /* 顺手让预览页再报一次坐标（换页 / 改图后尺寸会变） */
  }

  function bboxes() {
    var out = {};
    if (!doc) return out;
    ORDER.forEach(function (k) { if (reals[k]) out[k] = reals[k]; });

    /* .app 是整页外框，兜底几乎必然成立 ⇒ hero 一定有框可画 */
    var appEl = pick(['.app']);
    var app = appEl ? rect2fb(appEl) : { x: 0, y: 0, w: fbSize.w, h: fbSize.h };
    if (!sane(app)) app = { x: 0, y: 0, w: fbSize.w, h: fbSize.h };
    if (!out.hero) out.hero = app;

    if (!out.stand) {
      var sideEl = pick(['.side', 'aside.side']);
      var side = sideEl ? rect2fb(sideEl) : null;
      if (!sane(side)) side = { x: 0, y: 0, w: Math.round(app.w * 0.22), h: app.h };
      var viewport = { x: side.x + side.w, y: app.y,
                       w: Math.max(1, app.x + app.w - (side.x + side.w)), h: app.h };
      var botEl = pick(['.composer', '.inputbar', '.input-bar', '.bottom', 'footer']);
      var bot = botEl ? rect2fb(botEl) : null;
      var content = { x: viewport.x, y: viewport.y, w: viewport.w,
                      h: Math.max(1, viewport.h - (sane(bot) ? bot.h : 0)) };
      var stageEl = pick(['.stage .panel', '.stage']);
      if (stageEl) {
        var rr = inset(rect2fb(stageEl), 6);
        if (sane(rr) && rr.w > content.w * 0.45) out.stand = rr;
      }
      if (!out.stand) out.stand = standFallback(viewport, content);
    }

    if (!out.avatar) {
      var brandEl = pick(['.side .brand', '.brand']);
      var br = brandEl ? rect2fb(brandEl) : null;
      var avEl = pick(['.ccard .cav', '.cav', '.av']);
      var ar = avEl ? rect2fb(avEl) : null;
      var sr2 = null;
      var sideEl2 = pick(['.side', 'aside.side']);
      if (sideEl2) sr2 = rect2fb(sideEl2);
      if (sane(ar) && ar.w <= 140) {
        out.avatar = ar;                                   /* 就是个方形头像，直接用 */
      } else if (sane(br)) {
        /* .brand 是整条（含文字）：取它左边 26px 见方那块才是头像本体 */
        var d = Math.round(Math.max(20, Math.min(30, br.h * 0.5)));
        out.avatar = { x: br.x + 12, y: br.y + Math.round((br.h - d) / 2), w: d, h: d };
      } else if (sane(sr2)) {
        out.avatar = { x: sr2.x + 16, y: sr2.y + 16, w: 30, h: 30 };
      }
    }

    if (!out.cover) {
      var ccEl = pick(['.ccard:not(.dev)', '.ccard', '.char-card']);
      var cr = ccEl ? rect2fb(ccEl) : null;
      if (sane(cr)) out.cover = cr;
    }
    return out;
  }

  /* 开发/调试用：一眼看出每个框是实测的还是推算的 */
  function measureDiag() {
    var o = {};
    Object.keys(boxes).forEach(function (k) { o[k] = (reals[k] ? 'real' : 'est') + ' ' + JSON.stringify(boxes[k]); });
    o._base = baseScale.toFixed(4);
    o._cam = cam.s.toFixed(4);
    return o;
  }

  /* ---------- 高亮框：DOM 只建一次，之后只改几何 / 显隐 ----------
     ⚠️ 旧版每帧（镜头补间 580ms ≈ 35 帧）都 hot.innerHTML='' 再 createElement
     一个 div + 一个 span + 一个 click 监听 —— 580ms 内重建 70 次。
     更要命的是 `.tv5-hi` 那条 box-shadow:0 0 0 2000px 的扩散遮罩，
     意味着每帧都要把「整块预览」重绘一遍 —— 这才是「添加文件浮出来时卡」的主因。
     现在节点常驻：几何只在聚焦项/坐标变化时写（hiKey 守卫），每帧只写标签的 transform。 */
  var hiEl = null, hiIn = null, hiTag = null, hiKey = '';
  function paint() {
    if (!hiEl) {
      var hot = $('tv5Hot');
      if (!hot) return;
      /* v5.7：两层结构 —— 外层负责「到哪儿、多大」，内层反向缩放把外层那次缩放抵掉，
         于是边框粗细/圆角始终等于设计值（见 CSS 里 .tv5-hi 的注释）。 */
      hiEl = document.createElement('div');
      hiEl.className = 'tv5-hi';
      hiIn = document.createElement('div');
      hiIn.className = 'tv5-hi-in';
      hiTag = document.createElement('span');
      hiTag.className = 'tv5-hi-tag';
      hiIn.appendChild(hiTag);
      hiEl.appendChild(hiIn);
      hiEl.addEventListener('click', function (e) { e.stopPropagation(); pickFile(); });
      hot.appendChild(hiEl);
    }
    /* 全局视图：不画高亮框、不压暗 —— 整页干净地看效果。
       （画框的 box-shadow 会把其余部分压暗，全局模式下那是噪音不是引导） */
      var k = st.focus;
      var b = boxOf(k);
      /* ⚠️ 不能因为 doc 是 null（跨域预览）就不画 —— 坐标是小桥给的，跟 doc 无关。 */
      if (!k) { hiEl.style.display = 'none'; hint('点下方任一项，预览会自动放大到那一块'); return; }
      if (!b) {
        hiEl.style.display = 'none';
        hint('这个部位在「' + sceneName(metaOf(k).on) + '」，点上方标签过去');
        return;
      }
      hiEl.style.display = '';
      var s = metaOf(k);
    /* 「背景」的锚点就是整页，放大后等于全局视图 ⇒ 别骗用户说「已放大」。
       带上具体倍率，万一视觉不够明显，用户也能确认「确实放大了」而不是以为没生效。 */
    var zoomed = st.focus === k && cam.s > baseScale * 1.02;
    var mult = (cam.s / (baseScale || 1));
    hint(s.hint + (zoomed ? (' · 已放大 ' + mult.toFixed(1) + '×，可直接换图') : ' · 框内可直接换图'));
    /* 几何只在「聚焦的槽 / 它的坐标 / 有没有图」变化时重写一次 */
    var key = k + '|' + b.x + ',' + b.y + ',' + b.w + ',' + b.h + '|' + (st.imgs[k] ? 1 : 0);
    if (key !== hiKey) {
      var first = !hiKey;
      hiKey = key;
      /* ⚠️ 首帧必须掐掉过渡：框刚出现时没有「上一个位置」可言，
         让它从 (0,0,1px,1px) 补间过来会是「从左上角缩开」的怪异入场。 */
      if (first) hiEl.style.transition = 'none';
      hiEl.style.setProperty('--hi-r', (s.radius || 10) + 'px');
      hiEl.style.left = b.x + 'px'; hiEl.style.top = b.y + 'px';
      hiEl.style.width = b.w + 'px'; hiEl.style.height = b.h + 'px';
      hiTag.textContent = s.label + (st.imgs[k] ? '' : ' · 空');
      hiEl.classList.toggle('tiny', !!s.tiny);
      hiEl.classList.toggle('round', !!s.round);   /* CSS 早有 .round 规则，行为一直没接上 */
      if (first) {
        void hiEl.offsetWidth;                     /* 强制把起点落定 */
        requestAnimationFrame(function () { hiEl.style.transition = ''; });
      }
    }
    paintCam();
  }
  /* 每帧只干这一件事：标签的反向补偿（scale 1/cam.s），一条 inline transform */
  function paintCam() {
    if (!hiTag) return;
    var v = 'scale(' + (1 / (cam.s || 1)).toFixed(4) + ')';
    if (hiTag.style.transform !== v) hiTag.style.transform = v;
  }
  var lastHint = '';
  function hint(t) { if (t !== lastHint) { lastHint = t; $('tv5PrevHint').textContent = t; } }

  /* ---------- 压暗层：几何按**终态**投影写一次，动画期间一动不动 ----------
     旧版压暗是 .tv5-hi 上一条 box-shadow:0 0 0 2000px 的巨量扩散，它待在缩放层里，
     host 每变一次 scale 就要把整块预览连阴影重画一遍 —— 卡顿的另一半来源。
     现在压暗移到了不缩放的 #tv5Dim，用 clip-path 挖出目标洞。 */
  var DIM_PAD = 60;                 /* 必须与 CSS 里 #tv5Dim 的 inset 一致 */
  function paintDim() {
    if (!dim) return;
      var k = st.focus, b = boxOf(k);
      if (!k || !b) { dim.classList.remove('on'); return; }
    var t = targetCam(), sr = stageSize(), s = t.s;
    var x = sr.width / 2 + t.tx + s * (b.x - 540);
    var y = sr.height / 2 + t.ty + s * (b.y - 370);
    var w = s * b.w, h = s * b.h;
    if (!isFinite(x) || !isFinite(y) || !isFinite(w) || !isFinite(h)) { dim.classList.remove('on'); return; }
    /* #tv5Dim 的 inset 是 -60，洞在它自己的坐标系里要平移 +60。
       ⭐ 洞要比高亮框**大 3px**：描边正好骑在框线上，若洞和框严丝合缝，
       压暗层会把描边的外侧一半盖掉（dim 的 z-index 高于 host），看着像框缺了一半。 */
    var P = 3;
    var L = Math.max(0, x + DIM_PAD - P), T = Math.max(0, y + DIM_PAD - P);
    var R = Math.max(0, (sr.width + DIM_PAD) - (x + w) - P);
    var B = Math.max(0, (sr.height + DIM_PAD) - (y + h) - P);
    var rad = Math.max(0, Math.round(s * (metaOf(k).radius || 10)));
    dim.style.clipPath = 'inset(' + T.toFixed(0) + 'px ' + R.toFixed(0) + 'px '
      + B.toFixed(0) + 'px ' + L.toFixed(0) + 'px round ' + rad + 'px)';
    dim.classList.add('on');
  }

  /* ---------- 面板：从底部部位轨那一项飞出来，停在目标部位旁边 ----------
     v5.6 重做。旧版是「贴在高亮框内侧的一条小工具栏」，用户反馈「添加文件位置理应不是这样」——
     现在改成手机主题设置的做法：目标部位旁边弹一张小卡，箭头指向它，
     出场方向就是底部轨道那一项，用户一眼看出「这组按钮管的是这一块」。 */
  var pBox = null, pName, pSize, pThumb, pDesc, pAdd, pAddLbl, pDel;
  var panelBuilt = false, panelKey = '', panelTween = null, flyGuard = 0, panelHidden = false;
  var panelFlyAt = 0;                /* 上一次飞入的时刻（防抖用） */
  var PW = 236, PH = 156, GAP = 18;
  var MIN_TRAVEL = 104;              /* ⭐ 入场行程保底：小于这个值 ⇒ 看着就是「原地淡入 2 帧」 */

  function clampN(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  function buildPanel() {
    if (panelBuilt || !act) return;
    pBox = document.createElement('div');
    pBox.className = 'tv5-panel';
    pBox.innerHTML =
      '<div class="tv5-panel-head"><span class="tv5-panel-dot"></span>'
      + '<b class="tv5-panel-name"></b><em class="tv5-panel-size"></em></div>'
      + '<div class="tv5-panel-body"><div class="tv5-panel-thumb"></div>'
      + '<p class="tv5-panel-desc"></p></div>'
      + '<div class="tv5-panel-acts">'
      + '<button class="tv5-act-add" type="button"><span class="tv5-act-plus">＋</span>'
      + '<span class="tv5-act-label">添加文件</span></button>'
      + '<button class="tv5-act-mini" type="button" data-act="del" hidden>移除</button>'
      + '<button class="tv5-act-mini" type="button" data-act="out">回全局</button>'
      + '</div><i class="tv5-panel-arrow"></i>';
    pName = pBox.querySelector('.tv5-panel-name');
    pSize = pBox.querySelector('.tv5-panel-size');
    pThumb = pBox.querySelector('.tv5-panel-thumb');
    pDesc = pBox.querySelector('.tv5-panel-desc');
    pAdd = pBox.querySelector('.tv5-act-add');
    pAddLbl = pBox.querySelector('.tv5-act-label');
    pDel = pBox.querySelector('[data-act=del]');
    pAdd.addEventListener('click', function (e) { e.stopPropagation(); pickFile(); });
    pDel.addEventListener('click', function (e) {
      e.stopPropagation();
      if (!st.focus) return;
      snapshot();
      /* ⭐ 必须连 recs 一起删：themePayload() 看到 st.recs[k] 还在就会把它原样塞回
         images[k]，删除等于没删（问题①根因之一）。 */
      delete st.imgs[st.focus]; delete st.recs[st.focus];
      /* ⭐ 移除图 ⇒ 自动配色一并回到默认：配色是「由图决定」的，图没了就没依据，
         不能把旧色粘在主题上（否则外面移除了背景、强调色还是旧图算出来的色，
         跟「已回到默认」的观感打架）。st.colors=null 后 themePayload 发的
         accent1/accent2 为空，宿主 applyTheme 会回落到默认预设（aurora）。 */
      st.colors = null;
      applyAll(); renderLib(); renderSlots(); renderSwatches(); measure();
      /* 方案A：移掉一张图后，配色交给"下一个最高优先级的图"；一张不剩才回默认。 */
      syncSeedColor(st.focus);
      var t = targetCam(); moveCam(t.s, t.tx, t.ty);
      /* ⭐ 移除也要落盘：之前这里没调 autoSave，预览里删了、退出后后端还是旧图
         （问题①根因之二）。autoSave 内部会因 themePayload 的 null 真正清掉槽位。 */
      autoSave('已移除「' + metaOf(st.focus).label + '」');
    });
    pBox.querySelector('[data-act=out]').addEventListener('click', function (e) {
      e.stopPropagation(); setFocus(null);
    });
    act.appendChild(pBox);
    panelBuilt = true;
  }

  var DBG = /(\?|&)debug=1/.test(location.search || '');
  function flyLog(s) { if (DBG) { (window.__flyLog = window.__flyLog || []).push(s); } }
  /* ⭐ 面板收尾：直接落终态的**安全网**（正常情况下 CSS 过渡自己就跑完了，这里纯兜底）。
     ⚠️ 曾经用 GSAP 收尾 —— 坑在于：对一个身上还挂着若干**未完成补间**的元素调
     gsap.set(scale:1)，立刻读回仍可能是旧值（僵尸补间把它按回去了）。所以直接写内联样式，
     收尾前后统一清干净，不靠逐个记住 tween 句柄。 */
  function settlePanel(state) {
    if (HAS_GSAP) { try { gsap.killTweensOf(pBox); } catch (e2) {} }
    panelHidden = (state === 'out');
    pBox.style.transition = 'none';
    if (state === 'in') { pBox.classList.remove('out'); pBox.classList.add('in'); }
    else { pBox.classList.remove('in'); pBox.classList.add('out'); }
    pBox.style.transform = state === 'in'
      ? 'translate3d(0px, 0px, 0) scale(1)'
      : 'translate3d(0px, 22px, 0) scale(0.94)';
    void pBox.offsetHeight;
    pBox.style.transition = '';
  }

  /* ⭐ 同步 FLIP：整个入场在同一个 task 里跑完，不依赖 rAF ——
     transition:none → 写起始态 → **强制 reflow 把起始态钉住** → 恢复过渡 → 写终态。
     为什么必须这么绕：直接写起始态再写终态，浏览器会把两次写入合并成一次，
     中间没有样式重算，过渡就**根本不启动**（面板看着就是「啪」地出现 = 闪两帧）。
     用 rAF 拆帧也能拆开，但弱 WebView / 后台标签里 rAF 会被饿死 ⇒ 面板永远停在起始态。
     读一次 offsetHeight 强制重算，两头都躲开了。 */
  function panelFlip(dx, dy, fromCls) {
    pBox.style.transition = 'none';
    pBox.classList.remove('in');
    pBox.classList.add(fromCls);
    pBox.style.transform = 'translate3d(' + dx + 'px,' + dy + 'px,0) scale(0.87)';
    void pBox.offsetHeight;
    pBox.style.transition = '';             /* 回到 CSS 里那条 .52s cubic-bezier */
    pBox.classList.remove(fromCls);
    pBox.classList.add('in');
    pBox.style.transform = 'translate3d(0px, 0px, 0) scale(1)';
  }

  function flyIn(k, lx, ly, sr) {
    flyLog('fly ' + k + ' hidden=' + panelHidden);
    var dx = 0, dy = 0;
    var slotEl = document.querySelector('.tv5-slot[data-slot="' + k + '"]');
    if (slotEl && stage) {
      var r = slotEl.getBoundingClientRect();
      /* 起点取「轨道那一项所在的位置」—— 面板就是从那一项长出来的。
         ⚠️ 必须钳进 stage：#tv5Stage 是 overflow:hidden，而轨道在 stage 外面，
         不钳的话面板会先在 stage 下边缘之外冒出来被裁成半个，看着像渲染出错。 */
      var fromY = clampN(r.top - sr.top, sr.height * 0.34, Math.max(sr.height * 0.34, sr.height - 40));
      var fromX = clampN(r.left + r.width / 2 - sr.left - PW / 2, 10, Math.max(10, sr.width - PW - 10));
      dx = fromX - lx;
      dy = fromY - ly;
      /* ⭐ 行程保底（用户反馈「还是 2 帧的过度」的真凶之一）：
         像「背景」这种内贴大块的情形，轨道项离面板终点只有 20~30px，
         位移被 clamp 掉后 == 原地淡入（只有 opacity + 8% 缩放），主观上就是「闪两帧」。
         这里沿主导方向把行程补足到 MIN_TRAVEL，方向不变（始终是从轨道指向面板）。 */
      var len = Math.sqrt(dx * dx + dy * dy);
      if (len < 0.5) { dx = 0; dy = MIN_TRAVEL; }
      else { var u = MIN_TRAVEL / len; dx = dx * u; dy = dy * u; }
    } else { dy = MIN_TRAVEL; }
    dx = clampN(dx, -320, 320);
    dy = clampN(dy, 40, sr.height * 0.7);
    /* ⚠️ 防抖：同一部位 280ms 内别重播（loadPage 期间 measure 会连着回包两次），
       每次都 fromTo 一次 ⇒ GSAP 每次把面板按回起始态，表现为反复重播不是补间没跑。 */
    var now = (window.performance && performance.now) ? performance.now() : new Date().getTime();
    if (panelKey === k && (now - panelFlyAt) < 280) return;
    panelFlyAt = now;
    clearTimeout(flyGuard);
    if (panelTween) { try { panelTween.kill(); } catch (e2) {} panelTween = null; }
    panelHidden = false;
    /* 起点取「轨道那一项」的方向 —— 面板就是从那一项长出来的 */
    panelFlip(dx, dy, 'out');
    /* 兜底：真·极端情况（合成器也没跑）时无条件落终态，绝不留面板在 opacity:0 */
    flyGuard = setTimeout(function () {
      if (!pBox || panelHidden) return;
      if (parseFloat(getComputedStyle(pBox).opacity || '0') > 0.05) return;
      flyLog('snap-in');
      settlePanel('in');
    }, 760);
  }
  function hidePanel() {
    flyLog('hide');
    panelKey = '';
    clearTimeout(flyGuard);          /* 入场兜底别在出场之后把面板又拽回来 */
    if (panelTween) { try { panelTween.kill(); } catch (e2) {} panelTween = null; }
    if (!pBox) return;
    /* ⚠️ 已经在收起状态就别重复起收起：paintAct 会被 measure / 回包反复调到。 */
    if (panelHidden) return;
    panelHidden = true;              /* 正在收起：别让回包再触发一次 */
    /* 出场同样交给 CSS：下移 + 缩一点 + 淡出（out 类 + 去掉 in） */
    pBox.style.transition = 'none';
    pBox.classList.add('out');
    pBox.style.transform = 'translate3d(0px, 22px, 0) scale(0.94)';
    void pBox.offsetHeight;
    pBox.style.transition = '';
    pBox.classList.remove('in');
    /* 同一条兜底：面板收不掉 = 「点回全局没反应」 */
    flyGuard = setTimeout(function () {
      if (!pBox) return;
      flyLog('snap-out');
      settlePanel('out');
    }, 430);
  }

  /* resize 之类低频事件：直接刷一次位置（旧版还要额外开一条 rAF，白白和镜头抢同一帧） */
  function trackAct() { paintAct(); }

  function paintAct() {
    if (!act) return;
    buildPanel();
    if (!pBox) return;
      var k = st.focus, b = boxOf(k), t = targetCam(), sr = stageSize();
      if (!k || !b) { hidePanel(); return; }
    var s = t.s;
    /* 目标块放大后的屏幕投影（host 中心 540,370 / stage 中心 sr.w/2, sr.h/2） */
    var x = sr.width / 2 + t.tx + s * (b.x - 540);
    var y = sr.height / 2 + t.ty + s * (b.y - 370);
    var w = s * b.w, h = s * b.h;
    if (!isFinite(x) || !isFinite(y) || !isFinite(w) || !isFinite(h)) { hidePanel(); return; }
    /* 优先停目标块右侧、垂直居中；右边放不下转左侧；都不行挂下方。
       ⭐ v5.7 补一条「占满型」分支：「背景」的锚点就是整页，外挂的话
       `x+w+18+236 <= 预览宽` 必然不成立 ⇒ 一路落到挂下方 ⇒ 被 clamp 到预览最底边。
       面板于是从 stage 下缘外面冒出来、被 overflow 裁掉大半 —— 用户看到的就是
       「全局→背景时添加文件在最底下进入，闪两帧就没了」。改成贴进目标块**内**侧。 */
    var full = (w >= sr.width * 0.82) || (h >= sr.height * 0.82);
    var ar, lx, ly;
    if (full) {
      if (x + w - GAP - PW >= 10) { ar = 'ar-l'; lx = x + w - GAP - PW; ly = y + h / 2 - PH / 2; }
      else if (x + GAP + PW <= sr.width - 10) { ar = 'ar-r'; lx = x + GAP; ly = y + h / 2 - PH / 2; }
      else { ar = 'ar-t'; lx = x + w / 2 - PW / 2; ly = y + GAP; }
    }
    else if (x + w + GAP + PW <= sr.width - 10) { ar = 'ar-l'; lx = x + w + GAP; ly = y + h / 2 - PH / 2; }
    else if (x - GAP - PW >= 10) { ar = 'ar-r'; lx = x - GAP - PW; ly = y + h / 2 - PH / 2; }
    else { ar = 'ar-t'; lx = x + w / 2 - PW / 2; ly = y + h + GAP; }
    lx = clampN(lx, 10, Math.max(10, sr.width - PW - 10));
    ly = clampN(ly, 10, Math.max(10, sr.height - PH - 10));

    /* 内容：只改文本 / 背景，绝不重建 DOM */
    var SL = metaOf(k), has = !!st.imgs[k];
    var w1 = SL.label, w2 = SL.spec, w3 = SL.hint;
    if (pName.textContent !== w1) pName.textContent = w1;
    if (pSize.textContent !== w2) pSize.textContent = w2;
    if (pDesc.textContent !== w3) pDesc.textContent = w3;
    /* ⭐ 修掉「图上套图」：st.imgs[k] 已经是 url("/local_img?…") 形态，
       原先又包一层 url("…") 变成 url("url("/local_img?…")") ⇒ 浏览器解析成无效串 ⇒
       聚焦后那个小卡里的缩略图永远空白（问题②的另一种表现）。直接用即可。 */
    var bg = has ? st.imgs[k] : '';
    if (pThumb.style.backgroundImage !== bg) pThumb.style.backgroundImage = bg;
    var mark = pThumb.textContent || '';
    if (!has && mark !== '空') pThumb.textContent = '空';
    else if (has && mark) pThumb.textContent = '';
    if (pAddLbl.textContent !== (has ? '更换文件' : '添加文件')) {
      pAddLbl.textContent = has ? '更换文件' : '添加文件';
    }
    if (pDel.hidden !== !has) pDel.hidden = !has;

    pBox.style.left = Math.round(lx) + 'px';
    pBox.style.top = Math.round(ly) + 'px';
    /* ⚠️ 只切箭头类，别整块重写 className —— 那会把 .in / .out 一起清掉，
       面板的淡入淡出状态就被偷偷抹了（表现为「第二次切过去面板不再出现」）。 */
    if (pBox.dataset.ar !== ar) {
      pBox.dataset.ar = ar;
      pBox.classList.remove('ar-l', 'ar-r', 'ar-t');
      pBox.classList.add(ar);
    }
    /* 只有「刚切到这一项 / 面板被关过」才飞；其余（measure 回包、resize、reassert）
       只刷新内容与位置 —— 否则每次回包都 fromTo 一次，面板看着就在不停闪。 */
    if (panelKey !== k && st.focus === k) flyIn(k, lx, ly, sr);
    panelKey = k;
  }

  /* ---------- 选文件 ----------
     ⭐ v5.9：产品链路**不再用浏览器 file input + FileReader**。
        浏览器选到的图只活在内存里，一关编辑器就没了，主题包里也不会有文件。
        正确做法是走后端 t_pick：它弹系统文件框、把图复制进
        data/char_banners/themes/<id>/<slot>/<slot>.png，并返回可直接用的 url。
        本地 file input 保留为「后端不可达」时的降级（离线草稿模式）。 */
  var file = document.createElement('input');
  file.type = 'file'; file.accept = 'image/*'; file.style.display = 'none';
  document.body.appendChild(file);
  file.addEventListener('change', function () {
    var f = file.files && file.files[0];
    if (!f) return;
    var fr = new FileReader();
    fr.onload = function () { fillLocal(st.slot, 'url(' + fr.result + ')', fr.result); };
    fr.readAsDataURL(f);
    file.value = '';
  });

  function pickFile() { pickSlot(st.slot || 'hero'); }

  /* 换某个部位的图：走后端落盘 */
  function pickSlot(k) {
    if (!k) return;
    var s = metaOf(k);
    if (!B) { file.click(); return; }
    if (!ONLINE) { toast2('还没连上后端，这次换图只在预览里看得到 · 关掉就没了', true); file.click(); return; }
    if (!st.tid) { toast2('先把上面「应用到角色」选好，我才知道这套主题存哪', true); return; }
    if (!canFill(k)) { toast2(s.needBind ? '这张卡属于某个角色，得先在上面绑定一个角色' : '这个部位现在用不了', true); return; }
    toast2('正在打开文件选择…');
    /* 壁纸槽位走后端 page 参数：落盘到该主题的 pages/<page>/，
       与 theme_store.slot_dir(page) 对齐 —— 这是「能换更多背景」真正落地的一环。 */
    var wall = isWallSlot(k);
    B.tPick({ id: st.tid, slot: wall ? 'wall' : k, page: wall ? wallPageOf(k) : '' }).then(function (r) {
      if (!r || !r.success) {
        /* error 为空串 = 用户自己取消了，不是错误，别弹红字 */
        if (r && r.error) toast2('换图没成功：' + r.error, true);
        return;
      }
      var rec = r.image || {};
      if (!rec.url) { toast2('这张图没能落盘，换一张试试', true); return; }
      applyRec(k, rec);
      autoSave('换好了「' + s.label + '」');
    });
  }

  /* 把后端图位记录接进状态：写 CSS 值 + 存记录 + 按需取色 */
  function applyRec(k, rec) {
    snapshot();
    var wasPreset = st.preset;          /* 记下原来套着哪套预设，用于提示 */
    var oldUrl = ((st.recs[k] || {}).url) || '';   /* 换图前这张位上的旧 url */
    st.recs[k] = rec;
    st.imgs[k] = 'url("' + rec.url + '")';
    /* ⭐ 传图 = 脱离预设。整套主题从这一刻起以「我的主题」为准，
       配色由图决定（下面 extractPalette 会重算）。
       这一步原来就有，但**一声不响**：预设卡的高亮灭了、我的主题亮了，
       用户不知道发生了什么。这里补一句人话。 */
    st.preset = null;
    applyAll(); renderLib(); renderSlots(); measure();
    /* ⚠️ 保存必须在这里立刻触发，不能等取色。
       取色是 canvas 异步（+3s 超时），而且失败/超时都不回调 ——
       早先只在 extractPalette 成功回调里 autoSave，于是「换了图但没存」
       在取色失败时必然发生：文件已经在磁盘上，主题包里却没有它，
       下次打开这张图人间蒸发（孤儿文件）。 */
    autoSave('已换「' + metaOf(k).label + '」');
    if (wasPreset) {
      tip('已切到「我的主题」· 原先的「' + presetName(wasPreset) + '」只是起点，配色改按你的图算');
    }
    /* 只有背景 / 立绘参与自动取色；头像和卡背景只是点缀，不该决定整站配色。
       在线时**只走 askThemeAI**（后端优先 VLM、失败自动回落新色调板）——
       若再跑一遍本地画布取色，会与 AI 结果互相覆盖、颜色来回跳、还多存一次盘。
       只有后端不可用/失败时才退回本地取色（保证「换完图必有色」）。 */
    /* 方案A：只有"最高优先级的种子位"参与取色（背景>立绘>封面>头像）；
       手动选过色 ⇒ 不自动改。 */
    var seedNow = pickSeedSlot();
    /* ⭐ 2026-10-13 修「换了图却没有 AI 配色（而且一声不响）」：
       原来 isSeed=false（换的不是种子位 / 被手动锁定）时，askThemeAI 会在
       `if (!applyColor) return;` 处**静默返回** —— 不上色、不提示、连本地兜底都不走，
       用户只会以为"AI 取色坏了"。现在：①换了一张**不同的种子图** = 用户就是想按新图
       重新配色 → 自动解除手动锁定；②换的是非种子位 → 明确告诉他配色跟谁走、怎么改。 */
    var changedImg = (rec.url !== oldUrl);
    var unlocked = false;
    if (changedImg && k === seedNow && palettePicked) {
      palettePicked = false;
      unlocked = true;
    }
    var isSeed = (k === seedNow) && !palettePicked;
    /* ⭐ 比例提醒（所有图位都给）+ AI 取色（仅种子位）：异步、不阻断换图。
       ⚠️ 壁纸槽位跳过这一步：它不参与取色（pickSeedSlot 只认 4 个内容图位），
         而后端 t_theme_ai 的 slot 不认 'wall@x' 这种 key —— 发过去只会白跑一趟。 */
    if (!isWallSlot(k)) {
      askThemeAI(k, rec, isSeed, function () {
        /* 后端没给出可用色板（离线/失败）→ 兜底本地取色，绝不出现"换了图却没色" */
        if (isSeed) extractPalette(rec.url).then(function (ok) { if (ok) renderLib(); });
      });
      if (unlocked) {
        tip('换了新的' + metaOf(k).label + ' · 已解除「手动配色」锁定，正按这张图重算配色');
      } else if (!isSeed && k === seedNow) {
        /* 种子位没换图 + 被手动锁定 → 说清"为什么不动"和"怎么解"，别静默 */
        tip('配色仍是「手动锁定」的（你点过上方的色板）· 换一张不同的图，就会按新图重算');
      } else if (!isSeed) {
        tip('配色只跟「' + ((metaOf(seedNow) || {}).label || '背景') + '」走 —— 你换的是「' +
            ((metaOf(k) || {}).label || k) + '」，整站配色不变；想按这张图配色，就把它设成背景（或换掉背景）');
      }
    }
    lastSeedSlot = seedNow;
    var t = targetCam(); moveCam(t.s, t.tx, t.ty);   /* 换完图保持聚焦，方便接着看效果 */
  }

  function fillLocal(k, cssValue, rawSrc) {
    snapshot();
    st.imgs[k] = cssValue;
    delete st.recs[k];
    st.preset = null;
    applyAll(); renderLib(); renderSlots(); measure();
    autoSave('已换「' + metaOf(k).label + '」');
    /* 填图（预设/图库）会清掉该位记录 ⇒ 种子可能变了；按方案A重算，手动锁定则跳过。 */
    syncSeedColor(k);
    var t = targetCam(); moveCam(t.s, t.tx, t.ty);
  }

  function fill(k, cssValue, rawSrc) { fillLocal(k, cssValue, rawSrc); }   /* 兼容旧调用 */


  /* ---------- AI 主题助手：几何体检 + 按角色气质配色 ----------
     分工（2026-10-06 与用户确认）：
       · 图片比例 → 纯本地规则算（在/不在框里会不会裁），只**提醒**，绝不自动搬图；
       · 取色 → 交给后端已绑定的视觉模型（读角色气质给语义色板）；
         视觉不可用/超时 → 后端自动回落本地色调板，前端拿到什么都照用。
     红线：这一步永远不能阻断「换图」。它只在图已落盘、存好之后才异步补一句提醒/换色。 */
  /* 手动锁定：用户点过着板 ⇒ 之后一律不自动改色（随主题 auto 字段持久化）。 */
  var palettePicked = false;
  var lastSeedSlot = '';       /* 记录上一次的取色种子，用于判断"种子是否变了" */

  /* 方案A 统一入口：图变了（新增/移除/被预设覆盖）后，按优先级重新定色。
     palettePicked ⇒ 直接跳过，绝不覆盖用户的选择。 */
  function syncSeedColor(touched) {
    if (palettePicked) return;
    var seed = pickSeedSlot();
    var changed = (seed !== lastSeedSlot) || (!!touched && touched === seed);
    if (!seed) {
      /* 没有可作种子的图：只清掉"AI 色板选项"与比例提醒，**不碰 st.colors**
         （预设色/用户色由各自调用方管理，这里擦掉会把刚套的预设色也一起毁了）。
         注意：这一步不能放在下面"种子没变就 return"之后，否则 lastSeedSlot 本来就空时
         清空分支永远到不了，旧色板会残留（实测踩到）。 */
      if (lastSeedSlot || schemes.length) { schemes = []; renderSwatches(); }
      showGeoNote('');
      lastSeedSlot = '';
      return;
    }
    if (!changed) return;
    lastSeedSlot = seed;
    if (!B || !ONLINE || !st.tid) {    /* 离线 → 本地兜底，绝不出现"换了图却没色" */
      var r0 = st.recs[seed];
      if (r0 && r0.url) extractPalette(r0.url).then(function (ok) {
        if (ok) { renderSwatches(); renderLib(); autoSave('配色已按你的图重算'); }
      });
      return;
    }
    askThemeAI(seed, st.recs[seed], true, null);
  }

  function showGeoNote(text, level) {
    var el = $('tv5Geo');
    if (!el) return;
    if (!text) { el.className = 'tv5-geonote'; el.textContent = ''; return; }
    el.className = 'tv5-geonote show' + (level === 'warn' ? ' warn' : '');
    el.textContent = text;
  }

  function askThemeAI(k, rec, applyColor, onFail) {
    if (!B || !ONLINE || !st.tid) { if (onFail) onFail(); return; }
    /* ⚠️ 不在这里重置 palettePicked：用户手动选过色后要"后面就不重算"。
       锁定状态由 loadTheme / resetAll 管理（并随主题 auto 字段持久化）。 */
    B.tThemeAi({ id: st.tid, slot: k, page: '',
      mode: MODE }).then(function (r) {   /* 用启动时解析出的真实明暗，别再读恒为 null 的 data-theme */
      if (!r || !r.success || !r.result) { if (onFail) onFail(); return; }
      var res = r.result || {}, fit = res.fit || {};
      /* 比例提醒：ok 就清掉旧提示；soft/warn 才挂出来 */
      if (fit.level && fit.level !== 'ok' && fit.message) {
        showGeoNote(fit.message, fit.level === 'warn' ? 'warn' : 'soft');
      } else if (fit.level === 'ok') {
        showGeoNote('');
      }
      /* 配色只对"参与取色的图位"生效；手动锁定时绝不覆盖。 */
      if (!applyColor) return;
      var sch = res.schemes || [];
      if (!sch.length) { if (onFail) onFail(); return; }
      if (palettePicked) return;      /* 在途时用户已手动定色 → 不覆盖、不重算 */
      schemes = sch;
      st.colors = [sch[0].c1, sch[0].c2];
      setAccent(st.colors[0], st.colors[1]);
      renderSwatches(); markDirty(); renderLib();
      tip(res.source === 'ai'
        ? ('AI 按这张图的气质配了色' + (res.mood ? '（' + res.mood + '）' : '')
          + (res.mix && res.mix.length > 1 ? ' · 综合了 ' + res.mix.join('/') : ''))
        : ('已用本地取色' + (res.mix && res.mix.length > 1 ? '（综合 ' + res.mix.join('/') + '）' : '')
          + (res.fallback ? ' · AI 不可用：' + res.fallback : '')));
      autoSave('配色已存');
    });
  }

  /* ---------- 自动取色 ---------- */
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function hsl2hex(h, s, l) {
    h = ((h % 1) + 1) % 1;
    var r, g, b;
    if (s === 0) { r = g = b = l; }
    else {
      var q = l < 0.5 ? l * (1 + s) : l + s - l * s;
      var p = 2 * l - q;
      var hk = [h + 1 / 3, h, h - 1 / 3];
      var v = hk.map(function (t) {
        t = ((t % 1) + 1) % 1;
        if (t < 1 / 6) return p + (q - p) * 6 * t;
        if (t < 1 / 2) return q;
        if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
        return p;
      });
      r = v[0]; g = v[1]; b = v[2];
    }
    var to = function (x) {
      var n = Math.round(clamp(x, 0, 1) * 255);
      return (n < 16 ? '0' : '') + n.toString(16);
    };
    return ('#' + to(r) + to(g) + to(b)).toUpperCase();
  }
  /* 从图片算 4 套配色。返回 Promise<boolean>（是否真的算出来了）——
     调用方要靠它决定要不要补一次保存，**不能只看有没有回调**。 */
  function extractPalette(src) {
    return new Promise(function (resolve) {
      var settled = false;
      var done = function (v) { if (!settled) { settled = true; resolve(!!v); } };
      var im = new Image();
      /* ⚠️ 双重保险：3s 主动放弃 + onload/onerror 兜底。
         某些 WebView/沙箱里 <img> 既不 onload 也不 onerror，
         裸 new Image() 的 Promise 会永远挂起 ⇒ 换完图编辑器就卡住不响应。 */
      var t = setTimeout(function () { done(false); }, 3000);
      im.onload = function () {
        clearTimeout(t);
        try { done(computePalette(im, resolve)); } catch (e) { done(false); }
      };
      im.onerror = function () { clearTimeout(t); done(false); };
      im.src = src;
    });
  }
  function computePalette(im, _resolve) {
      try {
        var c = document.createElement('canvas');
        c.width = 64; c.height = 64;
        var x = c.getContext('2d');
        x.drawImage(im, 0, 0, 64, 64);
        var d = x.getImageData(0, 0, 64, 64).data;
        var buckets = {}, best = null;
        for (var i = 0; i < d.length; i += 4) {
          var r = d[i] / 255, g = d[i + 1] / 255, b = d[i + 2] / 255;
          if (d[i + 3] < 125) continue;
          var mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2;
          if (l < 0.14 || l > 0.93) continue;
          var s = mx === mn ? 0 : (l > 0.5 ? (mx - mn) / (2 - mx - mn) : (mx - mn) / (mx + mn));
          if (s < 0.12) continue;
          var h = 0;
          if (mx !== mn) {
            if (mx === r) h = ((g - b) / (mx - mn)) % 6;
            else if (mx === g) h = (b - r) / (mx - mn) + 2;
            else h = (r - g) / (mx - mn) + 4;
            h /= 6;
          }
          var key = Math.round(h * 12) % 12;
          buckets[key] = buckets[key] || { n: 0, s: 0, l: 0, h: h };
          buckets[key].n++; buckets[key].s += s; buckets[key].l += l;
        }
        Object.keys(buckets).forEach(function (k) {
          if (!best || buckets[k].n > best.n) best = buckets[k];
        });
        if (!best) { schemes = []; renderSwatches(); return false; }
        var H = best.h, S = clamp(best.s / best.n, 0.34, 0.86), L = clamp(best.l / best.n, 0.46, 0.66);
        /* 一次给四套，覆盖不同口味：原图色 / 邻近色 / 撞色 / 淡雅。
           名字要说人话 —— 用户看到的是「哪一套适合我」，不是 HSL 参数。 */
        schemes = [
          { name: '贴近原图', note: '最像图里本来的味道', c1: hsl2hex(H, S, L), c2: hsl2hex(H + 0.09, S * 0.92, L - 0.06) },
          { name: '邻近色', note: '同一色系，稳、耐看', c1: hsl2hex(H + 0.07, S, L), c2: hsl2hex(H - 0.06, S * 0.8, L + 0.04) },
          { name: '对比色', note: '两个色差拉开，界面更跳', c1: hsl2hex(H, S, L), c2: hsl2hex(H + 0.5, S * 0.72, L - 0.02) },
          { name: '淡雅', note: '把饱和压低，久看不累', c1: hsl2hex(H, S * 0.34, L + 0.04), c2: hsl2hex(H + 0.05, S * 0.26, L - 0.04) }
        ];
        st.colors = [schemes[0].c1, schemes[0].c2];
        setAccent(st.colors[0], st.colors[1]);
        renderSwatches(); markDirty();
        tip('按这张图算了 4 套配色，在右下角 · 现在用的是「贴近原图」，点别的可换');
        return true;
      } catch (e) { schemes = []; renderSwatches(); return false; }
  }

  /* ---------- 成套预设 / 恢复默认 ---------- */
  function applyPreset(id) {
    snapshot();
    var p = null;
    PRESETS.forEach(function (x) { if (x.id === id) p = x; });
    if (!p) return;
    st.preset = id;
    /* ⚠️ 这里原来写的是 `st.imgs = { hero: p.hero }` —— 整份覆盖。
       后果：用户已经传好的立绘/头像/角色卡图**全被抹掉**，
       而界面上那一栏写的是「只换配色，不动你的图」—— 文案与行为直接相反，
       表现是"点了个主题，之前传的全没了"，用户只会以为软件有 bug。
       正确语义：预设 = 配色起点。已有图一律保留；
       只有**一个图都没有**时才用预设的渐变当占位背景，别让用户对着空白页。 */
    var hadImg = ORDER.some(function (k) { return !!st.imgs[k]; });
    if (!hadImg) st.imgs = { hero: p.hero };
    st.colors = [p.c1, p.c2];
    schemes = [];
    applyAll(); renderLib(); renderSlots(); renderSwatches(); measure();
    var t = targetCam(); moveCam(t.s, t.tx, t.ty);
    tip(hadImg
      ? ('已套用「' + p.name + '」配色 · 你的图都留着，导入新图时会按新图重算')
      : ('已套用「' + p.name + '」· 还没有图，往下选个部位传图吧'));
    autoSave('预设已存');
  }
  function resetAll() {
    snapshot();
    st.imgs = {}; st.recs = {}; st.colors = null; st.preset = null; schemes = [];
    st.stickers = []; st.selStk = null; renderStickers();
    palettePicked = false; lastSeedSlot = '';
    applyAll(); renderLib(); renderSlots(); renderSwatches(); measure(); setFocus(null);
    $('tv5Undo').disabled = !history.length;
    tip('已清空这套主题的图片与配色 · 想反悔点右上「撤销」');
    autoSave('已清空');
  }

  /* ============================================================
     贴纸 / 表情包（2026-10-12）
     ------------------------------------------------------------
     · 每枚贴纸 {id,url,kind,page,x,y,scale,w,h}：x/y 是 0~1 页面百分比（中心锚），
       scale 是缩放倍率，基准宽 = min(原图宽, 320)px —— 与生产 theme_manager.paintStickers
       完全同一套规则，编辑器里摆的位置/大小就是产品里出现的。
     · 交互：拖动 = 挪位置；滚轮 = 缩放；选中后出小工具条（放大 / 缩小 / 删除）。
     · 三个入口：顶栏「贴纸」按钮（系统文件框，支持 gif/视频表情包）；
       从资源管理器 / 微信 / QQ / 抖音【直接把图拖进预览】（dataTransfer File，base64 落盘）；
       拖到已聚焦的图位上 = 直接填那张图位。
     · 落盘：themes/<id>/stickers/st_<sid>.<ext>，一枚一个文件互不覆盖（与图位的
       「一图位一图」本质不同，见 theme_store.save_sticker_image）。
     ============================================================ */
  var stkLayer = null, stkBar = null, stkSig = '';
  function scenePageKey(sceneKey) {
    /* 编辑器场景 key → 产品页 key：角色页在产品里叫 characters（pageKeyOf 正则结果），
       壁纸版面 wall@memory 就是 memory。对不上号 = 「编辑里贴了、产品里不出现」。 */
    return isWallSlot(sceneKey) ? wallPageOf(sceneKey)
         : (sceneKey === 'chars' ? 'characters' : (sceneKey || 'chat'));
  }
  function stkBaseW(s) { return Math.min(+s.w || 220, 320); }
  function ensureStkLayer() {
    if (stkLayer) return stkLayer;
    stkLayer = document.createElement('div');
    stkLayer.id = 'tv5Stickers';
    stkLayer.className = 'tv5-stickers';
    host.insertBefore(stkLayer, $('tv5Hot'));
    return stkLayer;
  }
  function renderStickers() {
    var layer = ensureStkLayer();
    var cur = scenePageKey(st.page);
    var mine = st.stickers.filter(function (s) { return (s.page || '') === cur; });
    var sig = cur + '::' + st.selStk + '::' + mine.map(function (s) {
      return [s.id, s.url, s.x, s.y, s.scale].join('|');
    }).join(';;');
    if (sig === stkSig) return;               /* 镜头/无关操作不重建（video 别拆了重放） */
    stkSig = sig;
    layer.innerHTML = '';
    mine.forEach(function (s) {
      var el = document.createElement('div');
      el.className = 'tv5-stk' + (st.selStk === s.id ? ' sel' : '');
      el.dataset.stk = s.id;
      el.style.left = (s.x * 100).toFixed(3) + '%';
      el.style.top = (s.y * 100).toFixed(3) + '%';
      el.style.width = Math.max(24, Math.round(stkBaseW(s) * (s.scale || 1))) + 'px';
      var media;
      if (s.kind === 'video') {
        media = document.createElement('video');
        media.src = s.url; media.autoplay = true; media.loop = true; media.muted = true;
        media.setAttribute('playsinline', '');
      } else {
        media = document.createElement('img');
        media.src = s.url; media.draggable = false; media.alt = '';
      }
      el.appendChild(media);
      /* ★ 2026-10-13：选中态右下角出缩放手柄 —— 拖边框缩放（用户点名的手感），
         滚轮缩放保留；小工具条只留「删除」。 */
      if (st.selStk === s.id) {
        var hd = document.createElement('div');
        hd.className = 'tv5-stk-hd';
        hd.title = '拖动缩放（滚轮也可以）';
        hd.addEventListener('pointerdown', function (e) {
          e.preventDefault(); e.stopPropagation();
          var hr = host.getBoundingClientRect();
          var cx = hr.left + s.x * hr.width, cy = hr.top + s.y * hr.height;
          var d0 = Math.max(8, Math.hypot(e.clientX - cx, e.clientY - cy));
          var s0 = s.scale || 1;
          function mv(ev) {
            var d = Math.max(8, Math.hypot(ev.clientX - cx, ev.clientY - cy));
            s.scale = Math.max(0.15, Math.min(5, s0 * d / d0));
            el.style.width = Math.max(24, Math.round(stkBaseW(s) * s.scale)) + 'px';
            paintStkToolbar();
          }
          function up() {
            window.removeEventListener('pointermove', mv);
            window.removeEventListener('pointerup', up);
            stkSig = ''; renderStickers(); autoSave('贴纸大小已调整');
          }
          window.addEventListener('pointermove', mv);
          window.addEventListener('pointerup', up);
        });
        el.appendChild(hd);
      }
      /* 指针事件拖动：兼容鼠标 / 触屏；坐标按 host 实测 rect 归一成 0~1，
         与镜头缩放天然解耦（rect 已含 scale）。 */
      el.addEventListener('pointerdown', function (e) {
        e.preventDefault(); e.stopPropagation();
        st.selStk = s.id; renderStickers();
        var hr = host.getBoundingClientRect();
        var sx = e.clientX, sy = e.clientY, ox = s.x, oy = s.y, moved = false;
        function mv(ev) {
          var dx = (ev.clientX - sx) / hr.width, dy = (ev.clientY - sy) / hr.height;
          if (Math.abs(dx) + Math.abs(dy) > 0.002) moved = true;
          s.x = Math.max(0.02, Math.min(0.98, ox + dx));
          s.y = Math.max(0.02, Math.min(0.98, oy + dy));
          el.style.left = (s.x * 100).toFixed(3) + '%';
          el.style.top = (s.y * 100).toFixed(3) + '%';
          paintStkToolbar();
        }
        function up() {
          window.removeEventListener('pointermove', mv);
          window.removeEventListener('pointerup', up);
          if (moved) { stkSig = ''; renderStickers(); autoSave('贴纸挪了个位置'); }
        }
        window.addEventListener('pointermove', mv);
        window.addEventListener('pointerup', up);
      });
      el.addEventListener('wheel', function (e) {
        e.preventDefault(); e.stopPropagation();
        scaleSticker(s, e.deltaY < 0 ? 1.08 : 1 / 1.08);
      }, { passive: false });
      layer.appendChild(el);
    });
    paintStkToolbar();
  }
  function scaleSticker(s, mult) {
    s.scale = Math.max(0.15, Math.min(5, (s.scale || 1) * mult));
    stkSig = ''; renderStickers(); autoSave('贴纸大小已调整');
  }
  function removeSticker(s) {
    snapshot();
    st.stickers = st.stickers.filter(function (x) { return x !== s; });
    if (st.selStk === s.id) st.selStk = null;
    stkSig = ''; renderStickers(); autoSave('已移除贴纸');
    /* 落盘文件一并清掉，别留孤儿（幂等：删不到也算成功） */
    if (B && ONLINE && st.tid && s.name) B.tStickerDel({ id: st.tid, name: s.name });
  }
  /* 小工具条：不放进 host（会被镜头缩放），挂 stage 上按终态投影定位 ——
     ★ 2026-10-13 两处改：
     ① 去掉「＋／－」按钮：缩放走右下角手柄拖动 + 滚轮，工具条只留「删除」；
     ② 定位改按贴纸**实际渲染矩形**（getBoundingClientRect，含镜头缩放与图片真实
        长宽比）算上缘 —— 原来只按宽度的一半估高度，长图（h≫w）时工具条压在
        图中间「挡死」。测不到矩形时才回落投影公式。 */
  function paintStkToolbar() {
    var s = null;
    for (var i = 0; i < st.stickers.length; i++) if (st.stickers[i].id === st.selStk) s = st.stickers[i];
    if (!s || (s.page || '') !== scenePageKey(st.page)) { if (stkBar) stkBar.style.display = 'none'; return; }
    if (!stkBar) {
      stkBar = document.createElement('div');
      stkBar.className = 'tv5-stkbar';
      stkBar.innerHTML = '<button type="button" data-a="del" class="del" title="移除贴纸">删除</button>';
      stkBar.addEventListener('pointerdown', function (e) { e.stopPropagation(); });
      stkBar.addEventListener('click', function (e) {
        var b = e.target.closest('button'); if (!b) return;
        var cur = null;
        for (var j = 0; j < st.stickers.length; j++) if (st.stickers[j].id === st.selStk) cur = st.stickers[j];
        if (!cur) return;
        if (b.dataset.a === 'del') removeSticker(cur);
      });
      if (stage) stage.appendChild(stkBar);
    }
    var sr = stage.getBoundingClientRect();
    var el = stkLayer ? stkLayer.querySelector('.tv5-stk[data-stk="' + s.id + '"]') : null;
    var er = null;
    try { er = el && el.getBoundingClientRect(); } catch (e2) { er = null; }
    var x, y;
    if (er && er.width > 1 && er.height > 1 && sr.width > 1) {
      x = er.left + er.width / 2 - sr.left;
      y = er.top - sr.top;                       /* 贴纸上缘 */
    } else {
      var t = targetCam(), sc = t.s;
      var cx = s.x * fbSize.w, cy = s.y * fbSize.h;
      x = sr.width / 2 + t.tx + sc * (cx - 540);
      y = sr.height / 2 + t.ty + sc * (cy - 370);
      y -= sc * Math.max(24, stkBaseW(s) * (s.scale || 1)) / 2;
    }
    if (!isFinite(x) || !isFinite(y)) { stkBar.style.display = 'none'; return; }
    stkBar.style.display = '';
    var bw = stkBar.offsetWidth || 64;
    stkBar.style.left = Math.round(Math.max(8, Math.min(sr.width - bw - 8, x - bw / 2))) + 'px';
    stkBar.style.top = Math.round(Math.max(8, Math.min(sr.height - 36, y - 44))) + 'px';
  }
  function addSticker(rec, xf, yf) {
    snapshot();
    var s = {
      id: rec.stickerId || ('s' + Date.now()), url: rec.url, name: rec.name || '',
      kind: rec.kind || 'image', page: scenePageKey(st.page),
      x: Math.max(0.02, Math.min(0.98, xf || 0.72)), y: Math.max(0.02, Math.min(0.98, yf || 0.3)),
      scale: 1, w: rec.w || 0, h: rec.h || 0
    };
    st.stickers.push(s);
    st.selStk = s.id;
    stkSig = ''; renderStickers();
    autoSave('已添加贴纸');
    tip('拖动挪位置 · 滚轮或拖右下角手柄缩放 · 点空白处收起');
  }
  function addStickerPick() {
    if (!B) { toast2('离线草稿模式贴不了贴纸：先连上后端（要把文件存进主题包）', true); return; }
    if (!st.tid) { toast2('先在左边选好主题，我才知道贴纸存哪', true); return; }
    toast2('正在打开文件选择…');
    B.tPick({ id: st.tid, slot: 'sticker' }).then(function (r) {
      if (!r || !r.success) { if (r && r.error) toast2('贴纸没贴上：' + r.error, true); return; }
      var rec = r.image || {};
      if (!rec.url) { toast2('这张没能落盘，换一张试试', true); return; }
      addSticker(rec, 0.72, 0.3);
    });
  }
  /* 拖入（对应「抖音表情包拖进 QQ」那类手感）：文件从资源管理器 / 聊天窗口
     直接拖进预览。规则：当前聚焦的是内容图位（背景/立绘/头像/卡头图）→
     拖入的图直接填它；否则 → 在松手的位置新增一枚贴纸。 */
  function dropFile(f, slotKey, xf, yf) {
    if (!B || !ONLINE || !st.tid) { toast2('连上后端后才能拖入（要把文件存进主题包）', true); return; }
    var isImg = /^image\//.test(f.type || ''), isVid = /^video\//.test(f.type || '');
    if (!isImg && !isVid) { toast2('只认图片 / 视频 / 表情包文件', true); return; }
    if (slotKey && !isImg) { toast2('图位只收图片；视频表情包直接贴到页面上就好', true); return; }
    if (f.size > 48 * 1024 * 1024) { toast2('这个文件超过 48MB，先压缩一下再拖', true); return; }
    toast2('正在接收「' + f.name + '」…');
    var fr = new FileReader();
    fr.onload = function () {
      B.tStickerDrop({ id: st.tid, slot: slotKey || '', page: '', name: f.name, b64: String(fr.result) })
        .then(function (r) {
          if (!r || !r.success) { toast2('拖入失败：' + ((r && r.error) || '未知原因'), true); return; }
          var rec = r.image || {};
          if (!rec.url) { toast2('没能落盘，再试一次', true); return; }
          if (slotKey) { applyRec(slotKey, rec); tip('已把拖入的图填进「' + metaOf(slotKey).label + '」'); }
          else addSticker(rec, xf, yf);
        });
    };
    fr.onerror = function () { toast2('读文件失败，再试一次', true); };
    fr.readAsDataURL(f);
  }
  function wireStickerDrop() {
    if (!stage) return;
    ['dragenter', 'dragover'].forEach(function (t) {
      stage.addEventListener(t, function (e) {
        e.preventDefault(); e.stopPropagation();
        if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
        stage.classList.add('tv5-dropping');
      });
    });
    stage.addEventListener('dragleave', function (e) {
      if (e.target === stage) stage.classList.remove('tv5-dropping');
    });
    stage.addEventListener('drop', function (e) {
      e.preventDefault(); e.stopPropagation();
      stage.classList.remove('tv5-dropping');
      var files = (e.dataTransfer && e.dataTransfer.files) || [];
      if (!files.length) return;
      var hr = host.getBoundingClientRect();
      var xf = Math.max(0.05, Math.min(0.95, (e.clientX - hr.left) / Math.max(1, hr.width)));
      var yf = Math.max(0.05, Math.min(0.95, (e.clientY - hr.top) / Math.max(1, hr.height)));
      /* 聚焦的是内容图位 → 填图位；否则 → 贴纸（多张错开一点，别叠成一摞） */
      var k = (st.focus && !isWallSlot(st.focus) && !st.selStk) ? st.focus : '';
      Array.prototype.slice.call(files).slice(0, 6).forEach(function (f, i) {
        dropFile(f, k, xf + i * 0.07, yf + i * 0.06);
      });
    });
    /* 点空白处收起贴纸工具条（别跟槽位点击打架：只处理没点在贴纸上的情况） */
    stage.addEventListener('pointerdown', function (e) {
      if (e.target.closest && e.target.closest('.tv5-stk')) return;
      if (e.target.closest && e.target.closest('.tv5-stkbar')) return;
      if (st.selStk) { st.selStk = null; stkSig = ''; renderStickers(); }
    });
  }

  /* ---------- 轻提示：文案讲不清的那半句，用一次性 toast 补上 ---------- */
  var tipTimer = 0;
  function tip(msg) {
    var el = $('tv5Tip');
    if (!el) return;
    el.textContent = msg;
    el.classList.add('show');
    clearTimeout(tipTimer);
    tipTimer = setTimeout(function () { el.classList.remove('show'); }, 3200);
  }
  /* toast2：区分「正常进展」与「要用户处理」。err=true 时用警示色。
     另：优先走后端桥的 toast（挂在同一个 #tv5Tip 上，两处共用一个盒子）。 */
  function toast2(msg, err) {
    if (B && B.toast) { B.toast(msg, err ? 'err' : ''); return; }
    tip(msg);
  }

  /* ============================================================
     v5.9 落盘层：所有改动自动存，主题包即时生效
     ------------------------------------------------------------
     为什么是「自动存」而不是「完成时才存」：
       · 换图走 t_pick 时文件已经落到磁盘了，主题包里不写它 = 孤儿文件
       · 用户崩了/误关窗口，没存的主题就白配了（这是主题设置最常见的坑）
     所以：改完 600ms 合并一次 t_save；「完成」只负责关窗 + 回吐结果。
     ============================================================ */
  var saveT = 0, saving = false, pendingSave = false;
  function themePayload() {
    var images = {}, anyWall = false;
    ORDER.forEach(function (k) {
      if (st.recs[k]) { images[k] = st.recs[k]; return; }
      /* 预设里的渐变（不是文件）⇒ 存成 CSS 串的假记录，
         否则「恢复默认/套用预设」的效果在产品里不生效。 */
      if (st.imgs[k]) { images[k] = { url: st.imgs[k], focus: '50% 40%', name: '内置渐变' }; return; }
      /* ⭐ 显式移除：后端 t_save 按槽位【部分合并】——某槽位不在 images 里 = 不动原值。
         所以只 delete st.imgs 不够，必须发 null 后端才会把该槽位清掉，
         否则删了图却残留旧值，退出编辑器再进来还是「移除前」的样子（问题①）。 */
      images[k] = null;
    });
    /* 页面壁纸（2026-10-12）：与 images 同构，独立成 pages 字段（后端 t_save 直接接住，
       落盘到该主题的 pages/<key>/）。null = 显式清空，与上面同一个道理。
       ⚠️ 2026-10-13 修双包装：st.imgs[kk] 存的是 CSS 值（url("...") 或
       linear-gradient(...)），直接当 url 存进去，生产侧会再包一层 url()
       ⇒ url("url(...)") 无效 ⇒ 壁纸不显示。这里剥掉 url() 外壳只存裸地址；
       非 url() 开头的（渐变之类）对这一栏没有意义，按未配处理。 */
    var pages = {};
    WALL_PAGES.forEach(function (p) {
      var kk = WALL_PREFIX + p.key;
      if (st.recs[kk] && st.recs[kk].url) { pages[p.key] = st.recs[kk]; anyWall = true; return; }
      if (st.imgs[kk]) {
        var raw = String(st.imgs[kk]).replace(/^url\(["']?/, '').replace(/["']?\)\s*$/, '');
        if (raw && raw.indexOf('url(') !== 0) {
          pages[p.key] = { url: raw, focus: '50% 40%', name: '本地图片' };
          anyWall = true; return;
        }
      }
      pages[p.key] = null;
    });
      return {
        id: st.tid || '',
        name: st.name || '我的主题',
        images: images,
        pages: pages,
        stickers: st.stickers,          /* 贴纸/表情包：整组覆盖存盘 */
        pageWallpaper: anyWall,
        theme: {
        accent1: (st.colors && st.colors[0]) || '',
        accent2: (st.colors && st.colors[1]) || '',
        auto: !palettePicked,   /* 手动定过色 ⇒ false，下次打开不再自动改 */
        /* 一次算出的多套方案一起存：下次打开还能换回别的口味，
           不用重新导入图。字段名带 v5_ 是为了不与未来主线的取色字段撞车。 */
        v5_schemes: schemes.map(function (s) { return { name: s.name, c1: s.c1, c2: s.c2 }; })
      }
    };
  }
  function saveNow(silent) {
    if (!B) return Promise.resolve(false);
    if (!ONLINE) { if (!silent) toast2('还没连上后端，这次改动只留在预览里', true); return Promise.resolve(false); }
    if (!st.tid) { ensureTid().then(function (ok) { if (ok) saveNow(silent); }); return Promise.resolve(false); }
    return B.tSave(themePayload()).then(function (r) {
      if (!r || !r.success) {
        if (r && r.offline) ONLINE = false;
        if (!silent) toast2('没存上：' + ((r && r.error) || '后端没回话'), true);
        setSaveState('err', '没存上');
        return false;
      }
      if (r.id) st.tid = r.id;
      pendingSave = false;
      setSaveState('ok', '已存');
      return true;
    });
  }
  function autoSave(why) {
    if (!ONLINE) return;
    setSaveState('busy', why || '保存中');
    clearTimeout(saveT);
    saveT = setTimeout(function () { saveNow(true); }, 600);
  }
  var saveEl = null;
  function setSaveState(kind, text) {
    if (!saveEl) saveEl = $('tv5State');
    if (!saveEl) return;
    if (kind === 'busy') { saveEl.textContent = text; saveEl.classList.add('dirty'); return; }
    if (kind === 'err') { saveEl.textContent = text; saveEl.classList.add('dirty'); saveEl.classList.add('err'); return; }
    saveEl.classList.remove('err');
    saveEl.textContent = text;
    saveEl.classList.remove('dirty');
  }
  /* 新建一个空主题包拿到 id（t_save 无 id 时后端会自己生成） */
  function ensureTid() {
    if (st.tid) return Promise.resolve(true);
    if (!B || !ONLINE) return Promise.resolve(false);
    return B.tSave({ name: st.name || '我的主题' }).then(function (r) {
      if (r && r.success && r.id) { st.tid = r.id; return true; }
      return false;
    });
  }

  /* ---------- 载入：把 tList 的主题包回填进编辑器 ---------- */
  function loadTheme(tid) {
    if (!B) { setSaveState('busy', '离线草稿'); return Promise.resolve(false); }
    setSaveState('busy', '读取中');
    return B.tList().then(function (r) {
      if (!r || !r.success) {
        ONLINE = false;
        setSaveState('busy', '离线草稿');
        return false;
      }
      ONLINE = true;
      var t = null;
      (r.themes || []).forEach(function (x) { if (x.id === tid) t = x; });
      if (!t) {
        /* 没给 id 或 id 不存在：沿用后端现有的第一个，避免凭空多出一套空主题 */
        t = (r.themes || [])[0] || null;
      }
        if (!t) {
          st.tid = ''; st.name = '我的主题';
          st.bound = '';
          st.stickers = []; st.selStk = null; renderStickers();
          setSaveState('busy', '新主题');
        renderName(); renderChars(); renderSlots();
        return false;
      }
      st.tid = t.id || '';
      st.name = t.name || '我的主题';
      st.bound = t.boundCharacter || '';
        st.imgs = {}; st.recs = {}; st.preset = null;
        /* ★ 2026-10-13 修「保存了后回到预览页也不见」：载入从不回填贴纸，
           编辑器内存里 stickers 恒为 []，下次任何 autoSave 都会把后端整组覆盖成空。
           必须在这里从主题包原样接回来。 */
        st.stickers = Array.isArray(t.stickers) ? t.stickers.slice() : [];
        st.selStk = null; stkSig = ''; renderStickers();
        ORDER.forEach(function (k) {
          var rec = (t.images || {})[k];
          if (rec && rec.url) { st.recs[k] = rec; st.imgs[k] = 'url("' + rec.url + '")'; }
        });
        /* 页面壁纸回填：t_list 把 pages 放在主题顶层（images.pages 的转发）。
           两处都读一下，兼容不同版本的后端返回。 */
        var pgSrc = t.pages || (t.images || {}).pages || {};
        WALL_PAGES.forEach(function (p) {
          var kk = WALL_PREFIX + p.key;
          var rec2 = pgSrc[p.key];
          if (rec2 && rec2.url) { st.recs[kk] = rec2; st.imgs[kk] = 'url("' + rec2.url + '")'; }
        });
        st.pageWallpaper = !!t.pageWallpaper;
        var th = t.theme || {};
      st.colors = (th.accent1 && th.accent2) ? [th.accent1, th.accent2] : null;
    /* 方案A：主题里 auto=false = 用户曾手动定色 ⇒ 打开即锁定，AI 不再自动改。 */
    palettePicked = (th.auto === false);
    lastSeedSlot = pickSeedSlot();
      schemes = Array.isArray(th.v5_schemes) ? th.v5_schemes.slice() : [];
      applyAll(); renderName(); renderLib(); renderChars(); renderSlots(); renderSwatches();
      measure();
      setSaveState('ok', '已载入');
      return true;
    });
  }
  function renderName() {
    var el = $('tv5Name');
    if (el) el.textContent = st.name || '我的主题';
    document.title = (st.name || '我的主题') + ' · 主题编辑器';
  }
  /* 通知宿主（产品层 iframe）：主题已存 / 请求关闭 */
  function toHost(msg, data) {
    try {
      if (window.parent && window.parent !== window) {
        window.parent.postMessage(Object.assign({ __te5: msg }, data || {}), '*');
      }
    } catch (e) {}
  }

  /* ---------- 启动 ---------- */
  /* 版面轨（内容版面 + 各页背景）由 JS 渲染；点击处理在 renderScenes 内绑定 */
  renderScenes();
  renderStickers();
  wireStickerDrop();
  (function () { var b = $('tv5AddSticker'); if (b) b.addEventListener('click', addStickerPick); })();
  $('tv5Undo').addEventListener('click', undo);
  $('tv5Reset').addEventListener('click', resetAll);
  $('tv5Global').addEventListener('click', function () { setFocus(null); });
  /* 主题名可点改：产品链路里这是真实主题包的名字，改完立刻落盘 */
  $('tv5Name').addEventListener('click', function () {
    if (!B || !ONLINE || !st.tid) { tip('连上后端后才能改名字'); return; }
    var n = prompt('这套主题叫什么？', st.name || '我的主题');
    if (n === null) return;
    var nn = (n || '').trim();
    if (!nn) { toast2('名字不能为空', true); return; }
    if (nn === st.name) return;
    st.name = nn.slice(0, 60);
    renderName();
    autoSave('名字已改');
    tip('改名为「' + st.name + '」');
  });
  $('tv5Done').addEventListener('click', function () {
    clearTimeout(saveT);
    var go = function () {
      setSaveState('ok', '已保存');
      /* ⭐ 把刚保存的那套主题原样回传宿主：images 里 null = 已移除的槽位，
         宿主直接 applyTheme 套上，移除/改动退出即生效（不依赖宿主侧 list/getCharacters）。 */
      var pkg = themePayload();
      toHost('saved', { themeId: st.tid, name: st.name, theme: pkg });
      toHost('close', { themeId: st.tid, theme: pkg });
      if (INLINE) { try { window.close(); } catch (e) {} }
    };
    if (!B || !ONLINE) { go(); return; }        /* 离线草稿：直接放行，不假装存了 */
    setSaveState('busy', '保存中');
    ensureTid().then(function (ok) {
      if (!ok) { toast2('后端没连上，这次改动没存上', true); return; }
      saveNow(true).then(function (r) { if (r) go(); else setSaveState('err', '没存上'); });
    });
  });
  /* ⭐ 返回键（问题④）：用户只是进来看看、不想改时，得有「直接离开」的入口，
     不能只能「完成」（=真实保存）。逻辑与「完成」一致（退出前 flush 一次保存，
     保证已改的不会丢），但**只发 close 不送 saved** ⇒ 宿主不会弹「已保存」，
     体验上就是「静默离开」。对于纯浏览（没改过任何东西）的情况，autoSave 从没触发，
     后端根本没动过 ⇒ 等同于「不保存退出」。 */
  $('tv5Back').addEventListener('click', function () {
    clearTimeout(saveT);
    var leave = function () {
      /* ⭐ 同样把保存后的主题包回传，宿主直接套用（见「完成」按钮注释）。 */
      var pkg = themePayload();
      toHost('close', { themeId: st.tid, theme: pkg });   /* 只发 close，宿主不提示「已保存」 */
      if (INLINE) { try { window.close(); } catch (e) {} }
    };
    if (!B || !ONLINE) { leave(); return; }
    setSaveState('busy', '保存中');
    ensureTid().then(function (ok) {
      if (!ok) { leave(); return; }
      saveNow(true).then(function () { leave(); });
    });
  });
  /* 点预览的压暗区（非高亮块、非浮出按钮）⇒ 退回全局 */
  stage.addEventListener('click', function (e) {
    if (!st.focus) return;
    if (e.target && e.target.closest && e.target.closest('.tv5-hi')) return;
    if (e.target && e.target.closest && e.target.closest('.tv5-act')) return;
    setFocus(null);
  });

  renderLib(); renderChars(); renderSlots(); renderSwatches();
  renderName();
  loadPage('chat');
  /* ⭐ v5.9：先渲染空壳（预览立刻可用），再异步把后端主题包 + 角色列表灌进来。
     反过来做会让用户盯着白屏等 IO。 */
  loadTheme(PARAM_THEME).then(function () { loadChars(); });
  /* ⭐ v5.4：fit() 在聚焦态下不写 cam，所以尺寸变化 / 预览刚载入后要补一次对焦，
     否则窗口拉一下倍数就失准了（框还亮着，却不是"框内放大"那个比例）。 */
  function refit() {
    var k = st.focus;
    fit();
    if (k) setTimeout(function () {
      if (st.focus !== k) return;
      var t = targetCam(); moveCam(t.s, t.tx, t.ty);
    }, 0);
  }
  window.addEventListener('resize', function () { refit(); trackAct(); });
  var tries = 0;
  var ti = setInterval(function () { refit(); if (++tries > 10) clearInterval(ti); }, 200);

  /* 供 CDP / 单测驱动（不走系统文件框） */
  window.__v5 = {
    state: st,
    fill: fill,
    fillLocal: fillLocal,
    applyRec: applyRec,
    pickSlot: pickSlot,
    saveNow: saveNow,
    loadTheme: loadTheme,
    loadChars: loadChars,
    chars: function () { return CHARS; },
    schemes: function () { return schemes; },
    backend: function () { return { online: ONLINE, tid: st.tid, name: st.name, bound: st.bound, hasB: !!B }; },
    select: select,
    setFocus: setFocus,
    stickers: function () { return st.stickers; },
    addSticker: function (rec, x, y) { addSticker(rec, x, y); },
    selSticker: function (id) { st.selStk = id; stkSig = ''; renderStickers(); },
    scene: gotoScene,
    scenes: function () { return SCENES.map(function (s) { return s.key; }); },
    wallPages: function () { return WALL_PAGES.map(function (p) { return p.key; }); },
    applyPreset: applyPreset,
    reset: resetAll,
    bind: bindChar,
    boxes: function () { return boxes; },
    reals: function () { return reals; },
    vars: function (k) { return k ? bridgeVars[k] : bridgeVars; },
    diag: measureDiag,
    cam: function () { return { s: cam.s, tx: cam.tx, ty: cam.ty, base: baseScale }; },
    settle: function (ms) { return new Promise(function (r) { setTimeout(r, ms || 800); }); },
    hiRect: function () {
      var e = document.querySelector('.tv5-hi');
      return e ? [parseInt(e.style.left), parseInt(e.style.top), parseInt(e.style.width), parseInt(e.style.height)] : null;
    },
    /* 给「点了没反应」这类问题用的自检：把关键状态一次打全 */
    health: function () {
      return {
        page: st.page, slot: st.slot, focus: st.focus,
        base: +baseScale.toFixed(4), cam: +cam.s.toFixed(4),
        mult: +(cam.s / (baseScale || 1)).toFixed(2),
        transform: host.style.transform,
        actVisible: act ? act.classList.contains('show') : false,
        actCls: (function () { var e = document.querySelector('.tv5-act'); return e ? e.className : null; })(),
        actInlineTf: (function () { var e = document.querySelector('.tv5-act'); return e ? e.style.transform : null; })(),
        actInStage: (function () {
          var e = document.querySelector('.tv5-act'); if (!e) return null;
          var a = e.getBoundingClientRect(), g = stage.getBoundingClientRect();
          return a.width > 0 && a.left >= g.left - 2 && a.right <= g.right + 2 && a.top >= g.top - 2 && a.bottom <= g.bottom + 2;
        })(),
        actBox: (st.focus && boxes[st.focus]) ? boxes[st.focus] : null,
        boxes: Object.keys(boxes), reals: Object.keys(reals)
      };
    },
    actText: function () {
      var e = document.querySelector('.tv5-act');
      return e ? [].map.call(e.querySelectorAll('button'), function (b) { return b.textContent; }) : null;
    },
    actRect: function () {
      var e = document.querySelector('.tv5-act');
      if (!e) return null;
      var r = e.getBoundingClientRect();
      return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)];
    },
    tmVar: function (k) {
      if (!doc) return null;
      return getComputedStyle(doc.documentElement).getPropertyValue(SLOTS[k].varName).trim().slice(0, 34);
    },
    accent: function () {
      return document.documentElement.style.getPropertyValue('--accent-1') ||
             getComputedStyle(document.documentElement).getPropertyValue('--accent-1').trim();
    },
    previewAccent: function () {
      if (!doc) return null;
      return getComputedStyle(doc.documentElement).getPropertyValue('--accent-1').trim();
    },
    scale: function () { return cam.s; }
  };
})();
