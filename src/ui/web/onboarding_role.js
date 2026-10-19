/* ============================================================================
   引导页 · 第 4 步「建角色」UI 模块（2026-10-12 落地）
   ----------------------------------------------------------------------------
   设计稿：docs/mockups/引导页建角色_设计稿_v8_20261012.html（v8，已过 81 项真机验证）
   落地方式：CSS 全量作用域化到 #onbRoleRoot（原稿有 117 个短类名 + .mask/.btn/.on/.row/.pill
             等通用名，直接注入会污染引导页）；HTML 只保留功能主体（去掉设计稿的评审面板与
             导语/步骤条——步骤条由引导页自己提供）。
   后端接线（全部走已有桥，见 app_backend.js）：
     getCharacters / getCapabilitySlots / listVoiceProfiles / listCloneEngines
     testVoice + getVoiceTestStatus / setReferenceAudio / pickAudioFile
     setCapabilitySlot('tts') / setVoiceMode / saveCharacter / switchCharacter / generateCharacter
     downloadVoiceModel + getVoiceDownloadStatus / scanTtsModelDir / setTtsModelDir
   红线：桥不可用时保留稿内 mock 行为（设计稿/无桥场景仍可交互），有桥则走真实接口；
         不画假进度（下载/试听进度来自后端）。
   用法：OnboardingRole.mountInto(容器) —— 引导页 screenRole 用；OnboardingRole.open() 弹层预览。
   ============================================================================ */
(function () {
  "use strict";

  /* 引导页场景 Backend 在 window.parent 上，且注入晚于脚本 → 实时代理解析（同 model_downloader.js） */
  function getB() {
    try {
      if (window.parent && window.parent !== window && window.parent.Backend) return window.parent.Backend;
    } catch (e) { /* 跨域忽略 */ }
    return window.Backend || null;
  }

  var _root = null;      /* 容器元素：$() 优先在其中查找，避免与引导页同名 id 冲突 */
  var _mounted = false;
  var _cssDone = false;

  var CSS = `#onbRoleRoot{
  /* ★ v8 修复（2026-10-12）：原基线是「深色」，浅色靠 [data-theme="light"] #onbRoleRoot 覆盖。
     但引导页只在能从 window.parent 读到 data-theme 时才会设置它，读不到就 removeAttribute
     ⇒ 页面没有 data-theme（应用「跟随系统·浅色」时就是这种情况）⇒ 模块整块走深色，
     深色条砸在浅色引导页上（用户实测「颜色有问题」）。
     改为：**基线=浅色**（与引导页自身默认一致），深色只在「显式 data-theme=dark」或
     「系统深色且未显式指定浅色」时生效 —— 与 design_tokens.css 的媒体查询默认保持一致。 */
  --bg:#F4F6FA; --bg2:#FFFFFF;
  --surface:rgba(15,22,40,.025); --surface-2:rgba(15,22,40,.05); --surface-3:rgba(15,22,40,.08);
  --surface-1:#FFFFFF;
  --line:rgba(15,22,40,.12); --line-1:rgba(15,22,40,.145); --line-2:rgba(15,22,40,.24);
  --ink:#141B2B; --ink-1:#141B2B; --ink-2:#4A5468; --ink-3:#828DA3;
  --a1:#2DD4FF; --a2:#A06BFF;
  --ok:#2faa6a; --warn:#d98a00; --bad:#e05252;
  --ease:cubic-bezier(.22,.61,.36,1);
  --font:"PingFang SC","Microsoft YaHei",system-ui,sans-serif;
}
html[data-theme="dark"] #onbRoleRoot, #onbRoleRoot[data-theme="dark"]{
  --bg:#0A0C12; --bg2:#0E1119;
  --surface:rgba(255,255,255,.035); --surface-2:rgba(255,255,255,.06); --surface-3:rgba(255,255,255,.09);
  --surface-1:rgba(255,255,255,.045);
  --line:rgba(255,255,255,.09); --line-1:rgba(255,255,255,.10); --line-2:rgba(255,255,255,.20);
  --ink:#E9EDF6; --ink-1:#E9EDF6; --ink-2:#AEB7C9; --ink-3:#6C7689;
}
@media (prefers-color-scheme: dark){
  html:not([data-theme="light"]) #onbRoleRoot{
    --bg:#0A0C12; --bg2:#0E1119;
    --surface:rgba(255,255,255,.035); --surface-2:rgba(255,255,255,.06); --surface-3:rgba(255,255,255,.09);
    --surface-1:rgba(255,255,255,.045);
    --line:rgba(255,255,255,.09); --line-1:rgba(255,255,255,.10); --line-2:rgba(255,255,255,.20);
    --ink:#E9EDF6; --ink-1:#E9EDF6; --ink-2:#AEB7C9; --ink-3:#6C7689;
  }
}#onbRoleRoot *{box-sizing:border-box;margin:0;padding:0}#onbRoleRoot body::before{content:"";position:fixed;inset:-20%;z-index:0;pointer-events:none;
  background:radial-gradient(42% 40% at 16% 16%,color-mix(in srgb,var(--a1) 13%,transparent),transparent 62%),
             radial-gradient(46% 44% at 86% 80%,color-mix(in srgb,var(--a2) 15%,transparent),transparent 64%)}#onbRoleRoot .wrap{position:relative;z-index:1;max-width:880px;margin:0 auto;padding:24px 24px 130px}#onbRoleRoot /* ===== 动效原子（与 src/ui/web/redesign/call_motion.css 同名同语义）===== */
@keyframes m-rise{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}@keyframes m-pop{0%{transform:scale(.96);opacity:0}60%{transform:scale(1.03)}100%{transform:scale(1);opacity:1}}@keyframes m-ping{0%{transform:scale(.6);opacity:.85}70%{transform:scale(1.9);opacity:0}100%{transform:scale(1.9);opacity:0}}@keyframes m-pulse-dot{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.45;transform:scale(.7)}}@keyframes m-shimmer{0%{background-position:-220% 0}100%{background-position:220% 0}}@keyframes m-expand{from{opacity:0;transform:translateY(-8px) scale(.985)}to{opacity:1;transform:none}}@keyframes m-item-in{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}@keyframes m-wave{0%,100%{transform:scaleY(.35)}50%{transform:scaleY(1)}}@keyframes m-toast-in{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}@keyframes m-stripe{to{background-position:44px 0}}#onbRoleRoot .an{animation:m-rise .5s var(--ease) both}#onbRoleRoot .pop{animation:m-pop .38s var(--ease) both}@media (prefers-reduced-motion:reduce){#onbRoleRoot *{animation-duration:.001ms!important;transition-duration:.001ms!important}}#onbRoleRoot /* ===== 顶部 ===== */
.kicker{font-size:11px;letter-spacing:.16em;color:var(--a1);font-weight:600}#onbRoleRoot h1{font-size:23px;margin:5px 0 7px;background:linear-gradient(90deg,var(--a1),var(--a2));-webkit-background-clip:text;background-clip:text;color:transparent;width:max-content}#onbRoleRoot .lead{color:var(--ink-2);font-size:13.5px;max-width:640px}#onbRoleRoot .steps{display:flex;gap:16px;flex-wrap:wrap;margin:14px 0 16px;color:var(--ink-3);font-size:12.5px}#onbRoleRoot .steps .s{display:inline-flex;align-items:center;gap:7px}#onbRoleRoot .steps .n{width:20px;height:20px;border-radius:50%;display:grid;place-items:center;font-size:11px;font-weight:600;background:var(--surface-2);color:var(--ink-3)}#onbRoleRoot .steps .s.done .n{background:color-mix(in srgb,var(--ok) 26%,transparent);color:var(--ok)}#onbRoleRoot .steps .s.cur{color:var(--ink);font-weight:600}#onbRoleRoot .steps .s.cur .n{background:color-mix(in srgb,var(--a1) 22%,transparent);color:var(--a1)}#onbRoleRoot /* ===== 引擎联动条（来自第 1 步「连引擎」）===== */
.ebar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:11px 14px;border-radius:14px;
  border:1px solid var(--line);background:var(--surface);margin-bottom:18px;animation:m-rise .5s var(--ease) both;animation-delay:.05s}#onbRoleRoot .ebar.warn{border-color:color-mix(in srgb,var(--warn) 40%,transparent);background:color-mix(in srgb,var(--warn) 8%,transparent)}#onbRoleRoot .eb-dot{width:9px;height:9px;border-radius:50%;flex:none;background:var(--ink-3);animation:m-pulse-dot 1.6s var(--ease) infinite}#onbRoleRoot .ebar.ok .eb-dot{background:var(--ok)}#onbRoleRoot .ebar.local .eb-dot{background:var(--a1)}#onbRoleRoot .ebar.warn .eb-dot{background:var(--warn)}#onbRoleRoot .eb-t{font-size:13px;font-weight:600}#onbRoleRoot .eb-from{font-size:11px;font-weight:400;color:var(--ink-3);margin-left:6px}#onbRoleRoot .eb-s{font-size:12px;color:var(--ink-2);margin-top:1px}#onbRoleRoot .eb-act{margin-left:auto;display:flex;align-items:center;gap:10px}#onbRoleRoot .eb-hint{font-size:11.5px;color:var(--ink-3);max-width:300px;line-height:1.4}#onbRoleRoot /* ===== 人物网格 ===== */
.pgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(216px,1fr));gap:13px;align-items:stretch}#onbRoleRoot .pcard{position:relative;border:1.5px solid var(--line);border-radius:16px;background:var(--surface);padding:14px;cursor:pointer;
  transition:transform .22s var(--ease),border-color .22s var(--ease),background .22s var(--ease);
  animation:m-rise .5s var(--ease) both;animation-delay:calc(var(--i,0)*55ms)}#onbRoleRoot .pcard:hover{transform:translateY(-3px);border-color:var(--line-2);background:var(--surface-2)}#onbRoleRoot .pcard.sel{border-color:var(--a1);background:var(--surface-2)}#onbRoleRoot /* 换人重播：pick() 摘挂 sel-anim/ping 类强制动画重新起跑（不重建 DOM，避免整页动效刷新） */
.pcard.sel-anim{animation:m-pop .38s var(--ease) both}#onbRoleRoot .pcard.sel::after{content:"";position:absolute;inset:-1.5px;border-radius:16px;pointer-events:none;
  box-shadow:0 0 0 3px color-mix(in srgb,var(--a1) 16%,transparent)}#onbRoleRoot .tick{position:absolute;top:10px;right:10px;width:22px;height:22px;border-radius:50%;
  background:color-mix(in srgb,var(--a1) 90%,transparent);display:none;place-items:center}#onbRoleRoot .pcard.sel .tick{display:grid}#onbRoleRoot .tick::before{content:"";position:absolute;inset:0;border-radius:50%;border:2px solid var(--a1)}#onbRoleRoot .pcard.sel .tick::before, #onbRoleRoot .tick.ping::before{animation:m-ping 1.1s var(--ease) 2}#onbRoleRoot .tick svg{width:13px;height:13px;stroke:#04121A;stroke-width:3;fill:none;stroke-linecap:round;stroke-linejoin:round}#onbRoleRoot .phead{display:flex;align-items:center;gap:10px;margin-bottom:9px}#onbRoleRoot /* 头像：忠于软件内角色卡解析链 —— 角色 avatar > 绑定主题 avatar > 人形占位（不用首字） */
.pav{width:40px;height:40px;border-radius:11px;flex:none;position:relative;overflow:hidden;display:grid;place-items:center;
  background:var(--surface-3);color:var(--ink-3)}#onbRoleRoot .pav img{width:100%;height:100%;object-fit:cover;display:block;position:absolute;inset:0}#onbRoleRoot .pav .ph-av{width:24px;height:24px;display:block}#onbRoleRoot .pav .ph-av svg{width:100%;height:100%;display:block;fill:currentColor}#onbRoleRoot .pname{font-size:15px;font-weight:600;line-height:1.25}#onbRoleRoot .ptag{font-size:11.5px;color:var(--ink-3);line-height:1.35}#onbRoleRoot .pdesc{font-size:12.5px;color:var(--ink-2);line-height:1.55;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;min-height:38px}#onbRoleRoot .pfoot{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:11px}#onbRoleRoot /* 语音分类 pill：只显示分类（云端音色/本地克隆/内置合成音/未配置），不显示模型名 */
.pill{display:inline-flex;align-items:center;gap:5px;font-size:11px;padding:3px 9px;border-radius:999px;
  background:var(--surface-2);color:var(--ink-2);border:1px solid var(--line);white-space:nowrap}#onbRoleRoot .pill.ok{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 34%,transparent);background:color-mix(in srgb,var(--ok) 12%,transparent)}#onbRoleRoot .pill.off{color:var(--warn);border-color:color-mix(in srgb,var(--warn) 34%,transparent);background:color-mix(in srgb,var(--warn) 10%,transparent)}#onbRoleRoot .pill b{font-weight:600}#onbRoleRoot .vbtn{width:28px;height:28px;border-radius:9px;border:1px solid var(--line);background:var(--surface-2);display:grid;place-items:center;
  cursor:pointer;color:var(--ink-2);transition:all .18s var(--ease);flex:none}#onbRoleRoot .vbtn:hover{border-color:var(--a1);color:var(--a1);background:color-mix(in srgb,var(--a1) 12%,transparent)}#onbRoleRoot .vbtn svg{width:14px;height:14px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}#onbRoleRoot .vbtn.playing{color:var(--a1);border-color:var(--a1)}#onbRoleRoot .vbtn.probing{color:var(--a1);border-color:var(--a1);animation:m-pulse-dot .9s var(--ease) infinite}#onbRoleRoot .vbtn.playing .bars{display:flex}#onbRoleRoot .vbtn.playing .ico{display:none}#onbRoleRoot .bars{display:none;gap:2px;align-items:flex-end;height:12px}#onbRoleRoot .bars i{width:2px;height:100%;background:currentColor;border-radius:1px;animation:m-wave .7s var(--ease) infinite}#onbRoleRoot .bars i:nth-child(2){animation-delay:.14s;height:70%}#onbRoleRoot .bars i:nth-child(3){animation-delay:.28s;height:85%}#onbRoleRoot /* 新建卡 */
.pnew{border:1.5px dashed var(--line-2);background:transparent;display:grid;place-items:center;text-align:center;min-height:152px;
  color:var(--ink-3);animation:m-rise .5s var(--ease) both;animation-delay:.33s;transition:all .22s var(--ease)}#onbRoleRoot .pnew:hover{border-color:var(--a1);color:var(--a1);background:color-mix(in srgb,var(--a1) 6%,transparent);transform:translateY(-3px)}#onbRoleRoot .pnew .plus{width:34px;height:34px;border-radius:50%;border:1.5px dashed currentColor;display:grid;place-items:center;margin:0 auto 8px}#onbRoleRoot .pnew .plus svg{width:16px;height:16px;stroke:currentColor;stroke-width:2;fill:none;stroke-linecap:round}#onbRoleRoot .pnew b{display:block;font-size:13.5px;color:var(--ink-2);font-weight:600}#onbRoleRoot .pnew span{font-size:11.5px;margin-top:2px;display:block}#onbRoleRoot /* ===== 新建 / 编辑 面板 ===== */
.panel{margin-top:6px;animation:m-expand .34s var(--ease) both}#onbRoleRoot .back{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;color:var(--ink-3);background:none;border:0;cursor:pointer;padding:4px 0;margin-bottom:10px}#onbRoleRoot .back:hover{color:var(--a1)}#onbRoleRoot .back svg{width:14px;height:14px;stroke:currentColor;stroke-width:2;fill:none;stroke-linecap:round;stroke-linejoin:round}#onbRoleRoot .sec{border:1.5px solid var(--line);border-radius:16px;background:var(--surface);padding:16px 16px 18px;margin-bottom:12px;animation:m-item-in .4s var(--ease) both}#onbRoleRoot .sec:nth-of-type(2){animation-delay:.06s}#onbRoleRoot .sec-h{display:flex;align-items:center;gap:9px;margin-bottom:12px}#onbRoleRoot .sec-n{width:22px;height:22px;border-radius:7px;display:grid;place-items:center;font-size:12px;font-weight:600;
  background:color-mix(in srgb,var(--a1) 18%,transparent);color:var(--a1)}#onbRoleRoot .sec-t{font-size:14.5px;font-weight:600}#onbRoleRoot .sec-s{font-size:12px;color:var(--ink-3)}#onbRoleRoot .row{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px}#onbRoleRoot .fld label{display:block;font-size:12px;color:var(--ink-3);margin-bottom:5px}#onbRoleRoot input, #onbRoleRoot select, #onbRoleRoot textarea{width:100%;font-family:inherit;font-size:13.5px;color:var(--ink);background:var(--surface-2);
  border:1px solid var(--line);border-radius:10px;padding:9px 11px;outline:none;transition:border-color .18s var(--ease)}#onbRoleRoot input:focus, #onbRoleRoot select:focus, #onbRoleRoot textarea:focus{border-color:var(--a1)}#onbRoleRoot textarea{resize:vertical;min-height:76px;line-height:1.6}#onbRoleRoot input[readonly]{color:var(--ink-3);cursor:default}#onbRoleRoot .idwrap{display:flex;align-items:center;gap:8px}#onbRoleRoot .idchip{font-size:11px;color:var(--ink-3);background:var(--surface-2);border:1px dashed var(--line-2);border-radius:8px;padding:3px 7px;white-space:nowrap}#onbRoleRoot .aibar{display:flex;gap:9px;align-items:center;margin-top:2px}#onbRoleRoot .aibar input{flex:1}#onbRoleRoot .btn{border:1px solid var(--line-2);background:var(--surface-2);color:var(--ink);border-radius:10px;padding:9px 15px;font-size:13px;
  font-family:inherit;cursor:pointer;transition:all .18s var(--ease);display:inline-flex;align-items:center;gap:7px;white-space:nowrap}#onbRoleRoot .btn:hover{border-color:var(--a1);color:var(--a1);transform:translateY(-1px);box-shadow:0 8px 18px rgba(0,0,0,.24)}#onbRoleRoot .btn:active{transform:translateY(0)}#onbRoleRoot .btn svg{width:14px;height:14px;stroke:currentColor;stroke-width:2;fill:none;stroke-linecap:round;stroke-linejoin:round}#onbRoleRoot .btn.primary{position:relative;overflow:hidden;border:0;color:#06121C;font-weight:600;
  background:linear-gradient(92deg,var(--a1),var(--a2));
  box-shadow:0 6px 18px color-mix(in srgb,var(--a1) 22%,transparent)}#onbRoleRoot .btn.primary::after{content:"";position:absolute;inset:0;pointer-events:none;opacity:0;
  background:linear-gradient(105deg,transparent 36%,rgba(255,255,255,.5) 50%,transparent 64%);
  background-size:220% 100%}#onbRoleRoot .btn.primary:hover{filter:brightness(1.07);color:#06121C;
  box-shadow:0 10px 24px color-mix(in srgb,var(--a1) 32%,transparent)}#onbRoleRoot .btn.primary:hover::after{opacity:1;animation:m-shimmer 1.1s linear}#onbRoleRoot .btn[disabled]{opacity:.45;pointer-events:none}#onbRoleRoot .btn.sm{padding:6px 11px;font-size:12px;border-radius:9px}#onbRoleRoot .aihint{font-size:11.5px;color:var(--ink-3);margin-top:7px}#onbRoleRoot .genline{height:2px;border-radius:2px;margin-top:10px;display:none;
  background:linear-gradient(90deg,transparent,var(--a1),var(--a2),transparent);background-size:220% 100%;animation:m-shimmer 1.15s linear infinite}#onbRoleRoot .genline.on{display:block}#onbRoleRoot /* ===== 底部条 ===== */
.dock{position:sticky;bottom:0;margin-top:18px;display:flex;align-items:center;gap:14px;flex-wrap:wrap;
  padding:13px 16px;border-radius:16px;border:1px solid var(--line);
  background:color-mix(in srgb,var(--bg2) 92%,transparent);backdrop-filter:blur(10px)}#onbRoleRoot .dock .info{flex:1;min-width:180px;font-size:13px}#onbRoleRoot .dock .info b{font-weight:600}#onbRoleRoot .dock .sub{font-size:11.5px;color:var(--ink-3)}#onbRoleRoot .tweak{background:none;border:0;color:var(--ink-3);font-size:12px;font-family:inherit;cursor:pointer;padding:0;text-decoration:underline dotted;text-underline-offset:3px}#onbRoleRoot .tweak:hover{color:var(--a1)}#onbRoleRoot .voiceact{display:none;background:none;border:0;color:var(--a1);font-size:12px;font-family:inherit;cursor:pointer;padding:0;margin:2px 0;text-decoration:underline dotted;text-underline-offset:3px}#onbRoleRoot .voiceact:hover{filter:brightness(1.15)}#onbRoleRoot .voiceact.on{display:inline-block;animation:m-item-in .3s var(--ease) both}#onbRoleRoot /* ===== 提示条 / toast ===== */
.note{display:none;gap:9px;align-items:flex-start;margin-top:12px;padding:11px 13px;border-radius:12px;font-size:12.5px;
  background:color-mix(in srgb,var(--warn) 10%,transparent);border:1px solid color-mix(in srgb,var(--warn) 30%,transparent);color:var(--ink-2)}#onbRoleRoot .note.on{display:flex;animation:m-item-in .3s var(--ease) both}#onbRoleRoot .note svg{width:15px;height:15px;flex:none;margin-top:2px;stroke:var(--warn);stroke-width:1.8;fill:none}#onbRoleRoot #toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%);z-index:120;display:none;
  padding:11px 17px;border-radius:12px;font-size:13px;background:var(--bg2);border:1px solid var(--line-2);color:var(--ink);max-width:82vw}#onbRoleRoot #toast.on{display:block;animation:m-toast-in .26s var(--ease) both}#onbRoleRoot #toast .dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--a1);margin-right:8px;animation:m-pulse-dot 1s var(--ease) infinite}#onbRoleRoot /* ===== 模态通用 ===== */
.mask{position:fixed;inset:0;z-index:110;display:none;place-items:center;padding:20px;
  background:color-mix(in srgb,#04121A 62%,transparent);backdrop-filter:blur(6px)}#onbRoleRoot .mask.on{display:grid;animation:m-toast-in .22s var(--ease) both}#onbRoleRoot /* v8：入场用 m-pop（缩放弹入），顶部叠两层极光——写在 background 里，天然在内容之下 */
.vmodal{width:min(480px,100%);max-height:88vh;overflow:auto;border:1px solid var(--line);
  border-radius:18px;padding:20px;box-shadow:0 24px 60px rgba(0,0,0,.42);
  background:
    radial-gradient(118% 82% at 14% -16%,color-mix(in srgb,var(--a1) 15%,transparent),transparent 58%),
    radial-gradient(108% 80% at 88% -20%,color-mix(in srgb,var(--a2) 13%,transparent),transparent 56%),
    var(--surface);
  animation:m-pop .34s var(--ease) both}#onbRoleRoot .vm-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}#onbRoleRoot .vm-hrow{display:flex;gap:11px;align-items:flex-start;min-width:0}#onbRoleRoot .vm-tico{width:34px;height:34px;border-radius:11px;flex:none;display:grid;place-items:center;color:#06121C;
  background:linear-gradient(140deg,var(--a1),var(--a2));box-shadow:0 6px 16px color-mix(in srgb,var(--a1) 26%,transparent);
  animation:m-pop .42s var(--ease) .06s both}#onbRoleRoot .vm-tico svg{width:17px;height:17px;fill:none;stroke:currentColor;stroke-width:1.9;stroke-linecap:round;stroke-linejoin:round}#onbRoleRoot .vm-title{font-size:17px;font-weight:650;letter-spacing:.2px}#onbRoleRoot .vm-sub{font-size:12.5px;color:var(--ink-3);margin-top:4px}#onbRoleRoot .vm-x{width:30px;height:30px;border-radius:9px;border:1px solid var(--line);background:var(--surface-2);
  display:grid;place-items:center;cursor:pointer;color:var(--ink-2);flex:none}#onbRoleRoot .vm-x{transition:border-color .2s var(--ease),color .2s var(--ease),transform .3s var(--ease)}#onbRoleRoot .vm-x:hover{border-color:var(--a1);color:var(--a1);transform:rotate(90deg)}#onbRoleRoot .vm-x:active{transform:rotate(90deg) scale(.92)}#onbRoleRoot .vm-x svg{width:14px;height:14px;stroke:currentColor;stroke-width:2;fill:none;stroke-linecap:round}#onbRoleRoot .vm-warn{display:none;margin-top:14px;padding:11px 12px;border-radius:12px;font-size:12.5px;line-height:1.5;
  background:color-mix(in srgb,var(--warn) 12%,transparent);border:1px solid color-mix(in srgb,var(--warn) 40%,transparent);
  color:var(--ink);align-items:center;gap:10px;flex-wrap:wrap}#onbRoleRoot .vm-warn.on{display:flex}#onbRoleRoot .vm-warn .btn{margin-left:auto}#onbRoleRoot .vm-reason{margin-top:14px;padding:11px 12px;border-radius:12px;font-size:12.5px;line-height:1.55;
  background:var(--surface-2);border:1px solid var(--line);color:var(--ink-2)}#onbRoleRoot .vm-reason b{color:var(--ink)}#onbRoleRoot .vm-steps{margin:14px 0 0;padding-left:20px;font-size:12.5px;color:var(--ink-2);line-height:1.75}#onbRoleRoot .vm-steps li{margin:5px 0}#onbRoleRoot .vm-steps code{font-size:11.5px;background:var(--surface-2);border:1px solid var(--line);border-radius:6px;padding:1px 6px;color:var(--a1)}#onbRoleRoot .vm-field{margin-top:14px}#onbRoleRoot .vm-field label{display:block;font-size:12px;color:var(--ink-3);margin-bottom:6px}#onbRoleRoot .vm-field input{width:100%;box-sizing:border-box;padding:10px 12px;border-radius:11px;border:1px solid var(--line);
  background:var(--surface-2);color:var(--ink);font-size:13px;font-family:ui-monospace,monospace}#onbRoleRoot .vm-field input{transition:border-color .18s var(--ease),box-shadow .22s var(--ease),background .18s var(--ease)}#onbRoleRoot .vm-field input:focus{outline:none;border-color:var(--a1);box-shadow:0 0 0 3px color-mix(in srgb,var(--a1) 16%,transparent)}#onbRoleRoot .vm-foot{margin-top:16px;display:flex;justify-content:flex-end;gap:9px;flex-wrap:wrap}#onbRoleRoot /* ===== 选路卡（内置合成音 → 专属声音）===== */
.routes{display:grid;gap:10px;margin-top:14px}#onbRoleRoot .route{display:flex;gap:12px;align-items:flex-start;text-align:left;width:100%;padding:13px 14px;border-radius:14px;
  border:1.5px solid var(--line);background:var(--surface-2);cursor:pointer;color:var(--ink);font-family:inherit;
  transition:all .18s var(--ease)}#onbRoleRoot .route:hover{border-color:var(--a1);background:color-mix(in srgb,var(--a1) 8%,transparent)}#onbRoleRoot .route.rec{border-color:color-mix(in srgb,var(--a1) 45%,transparent)}#onbRoleRoot .r-ic{width:34px;height:34px;border-radius:10px;flex:none;display:grid;place-items:center;
  background:color-mix(in srgb,var(--a1) 16%,transparent);color:var(--a1)}#onbRoleRoot .r-ic svg{width:17px;height:17px;stroke:currentColor;stroke-width:1.8;fill:none;stroke-linecap:round;stroke-linejoin:round}#onbRoleRoot .r-t{font-size:13.5px;font-weight:600;display:flex;align-items:center;gap:7px;flex-wrap:wrap}#onbRoleRoot .r-badge{font-size:10.5px;font-weight:600;color:var(--a1);background:color-mix(in srgb,var(--a1) 14%,transparent);
  border:1px solid color-mix(in srgb,var(--a1) 40%,transparent);border-radius:999px;padding:1px 7px}#onbRoleRoot .r-d{font-size:12px;color:var(--ink-3);margin-top:3px;line-height:1.5}#onbRoleRoot /* ===== 本地克隆卡 ===== */
.audio-row{display:flex;align-items:center;gap:11px;padding:11px 12px;border-radius:12px;background:var(--surface-2);
  border:1px solid var(--line);margin-top:6px}#onbRoleRoot .audio-row .a-ic{width:32px;height:32px;border-radius:9px;flex:none;display:grid;place-items:center;
  background:color-mix(in srgb,var(--a1) 14%,transparent);color:var(--a1)}#onbRoleRoot .audio-row .a-ic svg{width:15px;height:15px;stroke:currentColor;stroke-width:1.8;fill:none;stroke-linecap:round;stroke-linejoin:round}#onbRoleRoot .audio-row .a-meta{flex:1;min-width:0}#onbRoleRoot .audio-row .a-name{font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}#onbRoleRoot .audio-row .a-sub{font-size:11.5px;color:var(--ink-3);margin-top:1px}#onbRoleRoot .audio-row .a-play{width:30px;height:30px;border-radius:9px;border:1px solid var(--line-2);background:var(--surface);
  display:grid;place-items:center;cursor:pointer;color:var(--ink-2);flex:none;transition:all .18s var(--ease)}#onbRoleRoot .audio-row .a-play:hover{border-color:var(--a1);color:var(--a1)}#onbRoleRoot .audio-row .a-play svg{width:13px;height:13px;fill:currentColor}#onbRoleRoot .audio-row .a-play.playing{color:var(--a1);border-color:var(--a1)}#onbRoleRoot .autofill{display:inline-flex;align-items:center;gap:4px;font-size:10.5px;color:var(--ok);margin-left:6px}#onbRoleRoot .autofill svg{width:11px;height:11px;stroke:currentColor;stroke-width:2.4;fill:none;stroke-linecap:round;stroke-linejoin:round}#onbRoleRoot .engines{display:grid;gap:8px;margin-top:6px}#onbRoleRoot .eng{display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:12px;border:1.5px solid var(--line);
  background:var(--surface-2);cursor:pointer;transition:all .18s var(--ease);font-size:13px}#onbRoleRoot .eng:hover{border-color:var(--line-2)}#onbRoleRoot .eng.sel{border-color:var(--a1);background:color-mix(in srgb,var(--a1) 8%,transparent)}#onbRoleRoot .eng.locked{opacity:.5;cursor:not-allowed}#onbRoleRoot .eng .e-name{font-weight:600}#onbRoleRoot .eng .e-sub{font-size:11.5px;color:var(--ink-3);margin-left:auto;text-align:right;line-height:1.35}#onbRoleRoot .eng .e-sub b{color:var(--ok);font-weight:600}#onbRoleRoot .eng .e-sub .need{color:var(--warn)}#onbRoleRoot .e-dot{width:15px;height:15px;border-radius:50%;border:1.5px solid var(--line-2);flex:none;position:relative}#onbRoleRoot .eng.sel .e-dot{border-color:var(--a1)}#onbRoleRoot .eng.sel .e-dot::after{content:"";position:absolute;inset:2.5px;border-radius:50%;background:var(--a1)}#onbRoleRoot .progwrap{margin-top:14px;display:none}#onbRoleRoot .progwrap.on{display:block;animation:m-item-in .3s var(--ease) both}#onbRoleRoot .progbar{height:10px;border-radius:999px;background:var(--surface-2);border:1px solid var(--line);overflow:hidden;position:relative}#onbRoleRoot .progfill{height:100%;width:0%;border-radius:999px;background:linear-gradient(92deg,var(--a1),var(--a2));
  transition:width .18s linear;position:relative;overflow:hidden}#onbRoleRoot .progfill::after{content:"";position:absolute;inset:0;
  background:repeating-linear-gradient(45deg,rgba(255,255,255,.22) 0 10px,transparent 10px 20px);
  animation:m-stripe .9s linear infinite}#onbRoleRoot .progmeta{display:flex;justify-content:space-between;font-size:12px;color:var(--ink-2);margin-top:7px}#onbRoleRoot .progmeta b{color:var(--a1);font-weight:600}#onbRoleRoot .donebox{display:none;margin-top:14px;padding:13px;border-radius:12px;text-align:center;
  background:color-mix(in srgb,var(--ok) 12%,transparent);border:1px solid color-mix(in srgb,var(--ok) 36%,transparent)}#onbRoleRoot .donebox.on{display:block;animation:m-pop .34s var(--ease) both}#onbRoleRoot .donebox .d-t{font-size:14px;font-weight:600;color:var(--ok)}#onbRoleRoot .donebox .d-s{font-size:12px;color:var(--ink-2);margin-top:3px}#onbRoleRoot /* ===== v6 新增组件样式 ===== */
.pickref{margin-top:9px}#onbRoleRoot /* 选路卡：右侧箭头动作提示 */
.route{position:relative}#onbRoleRoot .r-go{display:block;margin-top:10px;font-size:13px;font-weight:600;color:var(--a1,#2DD4FF);letter-spacing:.02em}#onbRoleRoot .route.rec .r-go{color:var(--a1,#2DD4FF)}#onbRoleRoot .route:hover .r-go{filter:brightness(1.15)}#onbRoleRoot /* 云端详情卡 */
.cd-okbar{display:flex;gap:10px;align-items:flex-start;padding:11px 13px;border-radius:12px;
  background:color-mix(in srgb,var(--ok,#3ECF8E) 12%,transparent);
  border:1px solid color-mix(in srgb,var(--ok,#3ECF8E) 34%,transparent);
  font-size:13px;line-height:1.6;margin-bottom:16px}#onbRoleRoot .cd-okbar .dot{width:8px;height:8px;border-radius:50%;background:var(--ok,#3ECF8E);flex:none;margin-top:6px;
  box-shadow:0 0 0 3px color-mix(in srgb,var(--ok,#3ECF8E) 22%,transparent)}#onbRoleRoot .cd-steps{display:flex;flex-direction:column;gap:10px;margin:4px 0 16px}#onbRoleRoot .cd-step{display:flex;gap:11px;align-items:flex-start;font-size:13.5px;line-height:1.65;color:var(--ink-2);
  animation:m-item-in .42s var(--ease) both}#onbRoleRoot .cd-step b{color:var(--ink-1)}#onbRoleRoot .cs-n{width:21px;height:21px;border-radius:50%;flex:none;display:grid;place-items:center;font-size:11.5px;font-weight:700;
  background:color-mix(in srgb,var(--a1,#2DD4FF) 16%,transparent);color:var(--a1,#2DD4FF);margin-top:1px;
  box-shadow:0 0 0 3px color-mix(in srgb,var(--a1) 10%,transparent)}#onbRoleRoot /* 服务商 chips */
.provs{display:flex;gap:8px;flex-wrap:wrap}#onbRoleRoot .prov{padding:7px 14px;border-radius:999px;font-size:12.5px;font-weight:600;cursor:pointer;
  border:1px solid var(--line,rgba(128,128,128,.28));background:transparent;color:var(--ink-3);
  transition:all .18s var(--ease)}#onbRoleRoot .prov:hover{border-color:var(--a1,#2DD4FF);color:var(--ink-1)}#onbRoleRoot .prov.sel{border-color:var(--a1,#2DD4FF);color:var(--a1,#2DD4FF);
  background:color-mix(in srgb,var(--a1,#2DD4FF) 12%,transparent)}#onbRoleRoot /* ===== 本地克隆模型下载器 ===== */
.dlpanel{max-width:860px}#onbRoleRoot .dl-head{display:flex;gap:18px;align-items:flex-start;justify-content:space-between;flex-wrap:wrap;margin:6px 0 18px}#onbRoleRoot .dl-t{font-size:19px;font-weight:700;color:var(--ink-1);letter-spacing:.01em}#onbRoleRoot .dl-s{font-size:13px;color:var(--ink-3);margin-top:5px;line-height:1.6;max-width:520px}#onbRoleRoot .dl-disk{min-width:200px;flex:1;max-width:260px}#onbRoleRoot .dl-disk-t{display:flex;justify-content:space-between;font-size:12px;color:var(--ink-3);margin-bottom:6px}#onbRoleRoot .dl-disk-t b{color:var(--ink-1)}#onbRoleRoot .dl-diskbar{height:6px;border-radius:99px;background:var(--surface-3,rgba(128,128,128,.18));overflow:hidden}#onbRoleRoot .dl-diskfill{height:100%;border-radius:99px;background:linear-gradient(90deg,var(--a1,#2DD4FF),var(--a2,#A06BFF));
  transition:width .5s var(--ease)}#onbRoleRoot .dl-list{display:flex;flex-direction:column;gap:10px}#onbRoleRoot .dl-row{display:grid;grid-template-columns:26px 1fr auto auto auto;gap:12px;align-items:center;
  padding:13px 15px;border-radius:14px;border:1px solid var(--line,rgba(128,128,128,.22));
  background:var(--surface-1);transition:border-color .18s var(--ease),background .18s var(--ease)}#onbRoleRoot .dl-row:hover{border-color:color-mix(in srgb,var(--a1,#2DD4FF) 45%,transparent)}#onbRoleRoot .dl-row.pick{border-color:var(--a1,#2DD4FF);background:color-mix(in srgb,var(--a1,#2DD4FF) 7%,var(--surface-1))}#onbRoleRoot .dl-row.inst{background:color-mix(in srgb,var(--ok,#3ECF8E) 6%,var(--surface-1));
  border-color:color-mix(in srgb,var(--ok,#3ECF8E) 30%,transparent)}#onbRoleRoot .dl-sel{cursor:pointer;display:grid;place-items:center}#onbRoleRoot .radio{width:15px;height:15px;border-radius:50%;border:2px solid var(--ink-3);display:block;transition:all .18s}#onbRoleRoot .dl-row.pick .radio{border-color:var(--a1,#2DD4FF);background:radial-gradient(circle,var(--a1,#2DD4FF) 42%,transparent 46%)}#onbRoleRoot .radio.dis{opacity:.35;cursor:not-allowed}#onbRoleRoot .dl-name{font-size:14.5px;font-weight:650;color:var(--ink-1);display:flex;align-items:center;gap:8px}#onbRoleRoot .dl-rec{font-size:10.5px;font-weight:700;padding:2px 7px;border-radius:99px;letter-spacing:.04em;
  background:linear-gradient(90deg,var(--a1,#2DD4FF),var(--a2,#A06BFF));color:#fff}#onbRoleRoot .dl-tag{font-size:12px;color:var(--ink-3);margin-top:3px}#onbRoleRoot .dl-prog{margin-top:9px}#onbRoleRoot .dl-progbar{height:7px;border-radius:99px;background:var(--surface-3,rgba(128,128,128,.18));overflow:hidden}#onbRoleRoot .dl-progfill{height:100%;border-radius:99px;background:linear-gradient(90deg,var(--a1,#2DD4FF),var(--a2,#A06BFF));
  background-size:200% 100%;animation:m-shimmer 1.6s linear infinite;transition:width .22s linear}#onbRoleRoot .dl-progmeta{display:flex;justify-content:space-between;font-size:11.5px;color:var(--ink-3);margin-top:5px}#onbRoleRoot .dl-progmeta b{color:var(--a1,#2DD4FF);font-weight:600}#onbRoleRoot .dl-size{font-size:12.5px;color:var(--ink-3);white-space:nowrap}#onbRoleRoot .dl-act{display:flex;gap:6px}#onbRoleRoot .dl-done{grid-column:1/-1;display:flex;align-items:center;gap:6px;font-size:12px;font-weight:600;color:var(--ok,#3ECF8E)}#onbRoleRoot .dl-done svg{width:13px;height:13px;fill:none;stroke:currentColor;stroke-width:2.6;stroke-linecap:round;stroke-linejoin:round}#onbRoleRoot .dl-note{display:flex;gap:10px;align-items:flex-start;margin:16px 0 4px;padding:12px 14px;border-radius:12px;
  background:var(--surface-2);border:1px solid var(--line,rgba(128,128,128,.18));font-size:12.5px;line-height:1.65;color:var(--ink-3)}#onbRoleRoot .dl-note b{color:var(--ink-1)}#onbRoleRoot .dl-note svg{width:15px;height:15px;flex:none;margin-top:2px;fill:none;stroke:var(--a1,#2DD4FF);stroke-width:2;stroke-linecap:round}@media (max-width:640px){#onbRoleRoot .dl-row{grid-template-columns:22px 1fr auto;grid-template-rows:auto auto}#onbRoleRoot .dl-size{grid-column:2}#onbRoleRoot .dl-done{grid-column:1/-1}}#onbRoleRoot /* ===== v7 云端服务商网格（照能力页 cap-prov 语言） ===== */
.cd-state{display:flex;align-items:center;gap:9px;padding:10px 12px;border-radius:11px;
  background:var(--surface-2);border:1px solid var(--line-1);font-size:12.5px;color:var(--ink-2);margin-bottom:12px;
  animation:m-item-in .34s var(--ease) both}#onbRoleRoot .cd-state .dot{width:8px;height:8px;border-radius:50%;background:var(--warn,#E0A33E);flex:none;
  animation:m-pulse-dot 1.7s var(--ease) infinite;box-shadow:0 0 0 3px color-mix(in srgb,var(--warn,#E0A33E) 22%,transparent)}#onbRoleRoot .cd-state.ok{color:var(--ink);
  background:
    radial-gradient(120% 160% at 6% -40%,color-mix(in srgb,var(--ok,#3FA96B) 16%,transparent),transparent 62%),
    var(--surface-2);
  border-color:color-mix(in srgb,var(--ok,#3FA96B) 30%,transparent)}#onbRoleRoot .cd-state.ok .dot{background:var(--ok,#3FA96B);box-shadow:0 0 0 3px color-mix(in srgb,var(--ok,#3FA96B) 22%,transparent)}#onbRoleRoot .cd-state.bad{color:var(--ink);border-color:color-mix(in srgb,var(--bad,#E05C5C) 30%,transparent)}#onbRoleRoot .cd-state.bad .dot{background:var(--bad,#E05C5C);box-shadow:0 0 0 3px color-mix(in srgb,var(--bad,#E05C5C) 22%,transparent)}#onbRoleRoot .cd-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}#onbRoleRoot .cdv{border:1px solid var(--line-1);border-radius:12px;padding:11px 12px;background:var(--surface-1);
  cursor:pointer;transition:border-color .2s var(--ease),background .2s var(--ease),
    transform .22s var(--ease),box-shadow .26s var(--ease)}#onbRoleRoot .cdv:hover{border-color:color-mix(in srgb,var(--a1) 55%,var(--line-1));transform:translateY(-2px);
  box-shadow:0 10px 22px rgba(0,0,0,.30)}#onbRoleRoot .cdv:hover .vlg, #onbRoleRoot .cdv:hover img{transform:scale(1.07)}#onbRoleRoot .cdv.sel{border-color:var(--a1);transform:translateY(-1px);
  background:
    radial-gradient(130% 105% at 10% -22%,color-mix(in srgb,var(--a1) 15%,transparent),transparent 62%),
    var(--surface-2);
  box-shadow:0 0 0 1px color-mix(in srgb,var(--a1) 55%,transparent),
    0 12px 28px color-mix(in srgb,var(--a1) 15%,transparent)}#onbRoleRoot .cdv.off{opacity:.55}#onbRoleRoot /* 修正优先级：.cdv.sel 与 .cdv:hover 同权重且写在后面，会盖掉悬停的 transform；
   选中态再悬停时给更强的浮起（更高权重，确定性生效）。 */
.cdv.sel:hover{transform:translateY(-3px);
  box-shadow:0 0 0 1px color-mix(in srgb,var(--a1) 65%,transparent),
    0 16px 32px color-mix(in srgb,var(--a1) 20%,transparent)}#onbRoleRoot .cdv-h{display:flex;align-items:center;gap:8px;margin-bottom:8px}#onbRoleRoot .cdv-h img, #onbRoleRoot .cdv-h .vlg{width:22px;height:22px;border-radius:6px;flex:none;
  box-shadow:inset 0 0 0 1px rgba(255,255,255,.10);transition:transform .22s var(--ease)}#onbRoleRoot .cdv-n{font-size:13px;font-weight:600;color:var(--ink-1);flex:1;min-width:0;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}#onbRoleRoot /* 已选徽章：选中时 m-pop 弹入 + 一圈 m-ping 波纹（小范围，不喧哗） */
.cdv-pick{display:none;position:relative;flex:none;font-size:10px;font-weight:600;letter-spacing:.02em;
  padding:2px 8px;border-radius:999px;color:#06121C;background:linear-gradient(92deg,var(--a1),var(--a2))}#onbRoleRoot .cdv.sel .cdv-pick{display:inline-flex;animation:m-pop .34s var(--ease) both}#onbRoleRoot .cdv.sel .cdv-pick::before{content:"";position:absolute;inset:0;border-radius:inherit;
  border:2px solid color-mix(in srgb,var(--a1) 62%,transparent);animation:m-ping 1.05s var(--ease) 2}#onbRoleRoot .cdv-b{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:8px}#onbRoleRoot .cdv-b span{font-size:10px;padding:2px 6px;border-radius:5px;background:var(--surface-3);color:var(--ink-3)}#onbRoleRoot .cdv-b span.cy{background:color-mix(in srgb,var(--a1) 16%,transparent);color:var(--a1)}#onbRoleRoot .cdv-b span.cn{background:var(--surface-3);color:var(--ink-3)}#onbRoleRoot .cdv-b span.cfg{background:color-mix(in srgb,var(--ok,#3FA96B) 18%,transparent);color:var(--ok,#3FA96B)}#onbRoleRoot .cdv-b span.cfg.no{background:var(--surface-3);color:var(--ink-3)}#onbRoleRoot /* 「已配置 · 正在用」加一道慢慢扫过的高光（只亮不闪，克制） */
.cdv-b span.cfg:not(.no){background-image:linear-gradient(105deg,transparent 34%,rgba(255,255,255,.26) 50%,transparent 66%);
  background-size:220% 100%;animation:m-shimmer 3.4s linear infinite}#onbRoleRoot /* 已绑定 TTS：模型只读展示，不再出现 Key 输入框 */
.cdv-f.cdv-ro{margin-bottom:7px}#onbRoleRoot .cdv-f.cdv-ro label{display:block;font-size:10.5px;color:var(--ink-3);margin-bottom:3px}#onbRoleRoot .cdv-roval{font-size:12px;padding:6px 8px;border-radius:8px;border:1px dashed var(--line-1);
  background:var(--surface-2);color:var(--ink-2);font-family:ui-monospace,Menlo,Consolas,monospace}#onbRoleRoot .cdv-boundok{font-size:11px;line-height:1.5;color:var(--ok,#3FA96B);
  background:color-mix(in srgb,var(--ok,#3FA96B) 10%,transparent);
  border:1px solid color-mix(in srgb,var(--ok,#3FA96B) 26%,transparent);
  border-radius:8px;padding:6px 9px;margin-top:2px}#onbRoleRoot .cdv-boundno{font-size:11px;line-height:1.5;color:var(--warn,#E0A33E);
  background:color-mix(in srgb,var(--warn,#E0A33E) 10%,transparent);
  border:1px solid color-mix(in srgb,var(--warn,#E0A33E) 26%,transparent);
  border-radius:8px;padding:6px 9px;margin-top:2px}#onbRoleRoot .cdv-f{margin-bottom:7px}#onbRoleRoot .cdv-f label{display:block;font-size:10.5px;color:var(--ink-3);margin-bottom:3px}#onbRoleRoot .cdv-f select, #onbRoleRoot .cdv-f input{width:100%;font-size:12px;padding:6px 8px;border-radius:8px;
  border:1px solid var(--line-1);background:var(--surface-2);color:var(--ink-1);
  transition:border-color .18s var(--ease),box-shadow .22s var(--ease)}#onbRoleRoot .cdv-f select:focus, #onbRoleRoot .cdv-f input:focus{outline:none;border-color:var(--a1);
  box-shadow:0 0 0 3px color-mix(in srgb,var(--a1) 14%,transparent)}#onbRoleRoot .cdv-boundok::before{content:"";display:inline-block;width:6px;height:6px;border-radius:50%;
  background:var(--ok,#3FA96B);margin-right:6px;vertical-align:middle;
  animation:m-pulse-dot 1.7s var(--ease) infinite}#onbRoleRoot .cdv-boundno::before{content:"";display:inline-block;width:6px;height:6px;border-radius:50%;
  background:var(--warn,#E0A33E);margin-right:6px;vertical-align:middle;
  animation:m-pulse-dot 1.7s var(--ease) infinite}#onbRoleRoot .cdv-hint{font-size:10.5px;color:var(--ink-3);line-height:1.5;display:flex;gap:5px;align-items:flex-start}#onbRoleRoot .cdv-hint svg{width:12px;height:12px;flex:none;margin-top:2px}#onbRoleRoot .dl-have{display:flex;align-items:center;gap:9px;width:100%;margin-top:10px;padding:9px 12px;
  border:1px dashed var(--line-2);border-radius:10px;background:transparent;color:var(--ink-3);
  font-size:12px;cursor:pointer;text-align:left;transition:border-color .18s var(--ease),color .18s var(--ease)}#onbRoleRoot .dl-have:hover{border-color:var(--a1);color:var(--ink-2)}#onbRoleRoot .dl-have svg{width:16px;height:16px;flex:none}#onbRoleRoot .dl-have b{color:var(--ink-2);font-weight:600}#onbRoleRoot .dl-chips{display:flex;gap:4px;flex-wrap:wrap;margin:3px 0 4px}#onbRoleRoot .dl-chip{font-size:10px;padding:1.5px 6px;border-radius:5px;background:var(--surface-3);color:var(--ink-3)}#onbRoleRoot .dl-chip.k-dl{background:color-mix(in srgb,var(--a1) 15%,transparent);color:var(--a1)}#onbRoleRoot .dl-chip.k-cloud{background:color-mix(in srgb,#7F77DD 18%,transparent);color:#9F97EE}#onbRoleRoot .dl-chip.k-ext{background:var(--surface-3);color:var(--ink-3)}#onbRoleRoot .dl-chip.v-ok{background:color-mix(in srgb,var(--ok,#3FA96B) 16%,transparent);color:var(--ok,#3FA96B)}#onbRoleRoot .dl-chip.v-tight{background:color-mix(in srgb,var(--warn,#E0A33E) 18%,transparent);color:var(--warn,#E0A33E)}#onbRoleRoot .dl-chip.v-fail{background:color-mix(in srgb,var(--bad,#E05C5C) 16%,transparent);color:var(--bad,#E05C5C)}#onbRoleRoot .dl-chip.v-key, #onbRoleRoot .dl-chip.v-train{background:color-mix(in srgb,var(--warn,#E0A33E) 14%,transparent);color:var(--warn,#E0A33E)}#onbRoleRoot .cd-clone{margin-top:12px;border:1px solid var(--line-1);border-radius:12px;padding:12px;
  background:
    radial-gradient(120% 120% at 8% -30%,color-mix(in srgb,var(--a1) 10%,transparent),transparent 60%),
    var(--surface-1);
  animation:m-expand .38s var(--ease) both}#onbRoleRoot .cd-clone-h{display:flex;gap:10px;align-items:center;margin-bottom:10px}#onbRoleRoot .cd-clone-logo{width:30px;height:30px;border-radius:9px;flex:none;display:grid;place-items:center;overflow:hidden;
  background:var(--surface-3);font-size:11px;color:var(--ink-3);
  box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--a1) 22%,transparent)}#onbRoleRoot .cd-clone-t{font-size:13px;font-weight:600;color:var(--ink-1)}#onbRoleRoot .cd-clone-s{font-size:11px;color:var(--ink-3);margin-top:1px}#onbRoleRoot /* 声波指示（出现时跳 3 下，之后静止——不长期占用注意力） */
.cd-wave{display:inline-flex;align-items:flex-end;gap:2.5px;height:15px;margin-left:auto;padding-right:2px;flex:none}#onbRoleRoot .cd-wave i{width:2.5px;height:15px;border-radius:2px;background:linear-gradient(180deg,var(--a1),var(--a2));
  transform-origin:bottom center;transform:scaleY(.35);animation:m-wave .78s var(--ease) 3}#onbRoleRoot .cd-wave i:nth-child(2){animation-delay:.11s}#onbRoleRoot .cd-wave i:nth-child(3){animation-delay:.22s}#onbRoleRoot .cd-noclone{margin-top:12px}#onbRoleRoot /* 底部操作区：与本模态内容拉开层次 */
#cdMask .vm-foot{margin-top:18px;padding-top:14px;border-top:1px solid var(--line-1)}#onbRoleRoot #cdMask .btn.primary{padding-left:18px;padding-right:18px}#onbRoleRoot /* ===== 评审面板 ===== */
.dev{position:fixed;top:12px;right:12px;z-index:130;font-size:11.5px;color:var(--ink-3);
  background:var(--surface-2);border:1px solid var(--line);border-radius:12px;padding:9px 11px;backdrop-filter:blur(8px)}#onbRoleRoot .dev .t{color:var(--a1);font-weight:600;margin-bottom:6px}#onbRoleRoot .dev label{display:flex;align-items:center;gap:6px;cursor:pointer;padding:2px 0}#onbRoleRoot .dev select{width:auto;font-size:11.5px;padding:3px 6px;border-radius:7px}@media(max-width:760px){.dev{display:none}.row{grid-template-columns:1fr}}`;

  var HTML = `

<div class="wrap">
  
  
  

  

  <!-- 引擎联动条：第 1 步「连引擎」配的 TTS 状态，决定下面每个人的声音分类 -->
  <div class="ebar ok" id="ebar">
    <span class="eb-dot"></span>
    <div>
      <div class="eb-t">TTS 引擎<span class="eb-from">来自第 1 步「连引擎」</span></div>
      <div class="eb-s" id="ebState">云端已连 · 阿里云百炼</div>
    </div>
    <div class="eb-act">
      <span class="eb-hint" id="ebHint">每个人的声音分类由它决定</span>
      <button class="btn sm" id="ebEdit" type="button">改引擎</button>
    </div>
  </div>

  <!-- ============ 视图 A：挑人（默认） ============ -->
  <div id="viewPick">
    <div class="pgrid" id="pgrid"></div>

    <div class="dock">
      <div class="info">
        <!-- v8 修（反馈⑤）：克隆入口不再跟名字挤在同一行，单独放一行 + 上下留白，避免「已选 达妮娅给她一个专属声音」黏成一坨 -->
        <div class="chosen">已选 <b id="dockName">达妮娅</b></div>
        <button class="voiceact" id="voiceActBtn" type="button">给她一个专属声音</button>
        <div class="sub" id="dockSub">进软件后她就是当前角色，随时可以在「角色」页换</div>
      </div>
      <button class="tweak" id="tweakBtn" type="button">想微调她的性格 / 声音</button>
      <button class="btn primary" id="useBtn" type="button">就用她</button>
    </div>
  </div>

  <!-- ============ 视图 B：新建 / 编辑 ============ -->
  <div id="viewNew" style="display:none">
    <div class="panel">
      <button class="back" id="backBtn" type="button">
        <svg viewBox="0 0 24 24"><path d="M15 18l-6-6 6-6"/></svg>回到挑人
      </button>

      <div class="sec">
        <div class="sec-h">
          <span class="sec-n">1</span>
          <div>
            <div class="sec-t">她是谁</div>
            <div class="sec-s">三段式，和软件内角色卡完全一致</div>
          </div>
        </div>
        <div class="row">
          <div class="fld">
            <label>角色名称</label>
            <input id="fName" placeholder="例如 达妮娅" value="">
          </div>
          <div class="fld">
            <label>角色 ID</label>
            <div class="idwrap">
              <input id="fId" readonly value="">
              <span class="idchip" id="fIdChip">自动生成</span>
            </div>
          </div>
        </div>
        <div class="fld" style="margin-bottom:12px">
          <label>一句话描述（给 AI 用，可留空）</label>
          <div class="aibar">
            <input id="fHint" placeholder="例如 崩坏星穹铁道的狐人捉鬼师，胆小怕鬼">
            <button class="btn" id="aiBtn" type="button">
              <svg viewBox="0 0 24 24"><path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/></svg>AI 帮我写
            </button>
          </div>
          <div class="genline" id="genLine"></div>
          <div class="aihint">点一下，AI 会把下面三段一次写完，你再随意改。</div>
        </div>
        <div class="fld" style="margin-bottom:10px">
          <label>性格特征</label>
          <textarea id="fChar" placeholder="她是什么样的人"></textarea>
        </div>
        <div class="fld" style="margin-bottom:10px">
          <label>说话风格</label>
          <textarea id="fStyle" placeholder="她怎么说话"></textarea>
        </div>
        <div class="fld">
          <label>背景设定</label>
          <textarea id="fBg" placeholder="她从哪来、你们什么关系"></textarea>
        </div>
      </div>

      <div class="sec">
        <div class="sec-h">
          <span class="sec-n">2</span>
          <div>
            <div class="sec-t">她的声音</div>
            <div class="sec-s">挑好人之后，点头像右下角的喇叭就能配</div>
          </div>
        </div>
        <div class="fld">
          <label>音色档</label>
          <select id="fVoice">
            <option value="">暂不设置（用系统默认）</option>
          </select>
        </div>
        <div class="note" id="voiceNote">
          <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 8v4M12 16h.01"/></svg>
          <div>没配也能先用：出声走内置合成音。想要她的专属声音，保存后在角色卡上点喇叭，本地克隆免费、云端克隆去服务商拿音色编号。</div>
        </div>
      </div>

      <div class="dock">
        <div class="info">
          <div id="newInfo">新建角色</div>
          <div class="sub">保存后立刻设为当前角色，不用重启</div>
        </div>
        <button class="btn" id="cancelBtn" type="button">取消</button>
        <button class="btn primary" id="saveBtn" type="button">创建并使用她</button>
      </div>
    </div>
  </div>

  <!-- ============ 视图 C：本地克隆模型下载器（点「去下载」进入） ============ -->
  <div id="viewDl" style="display:none">
    <div class="panel dlpanel">
      <button class="back" id="dlBack" type="button">
        <svg viewBox="0 0 24 24"><path d="M15 18l-6-6 6-6"/></svg>返回配声音
      </button>
      <div class="dl-head">
        <div>
          <div class="dl-t">本地语音克隆模型</div>
          <div class="dl-s">下一次，之后所有角色都能永久免费克隆——不用注册、不花额度、音频不出电脑。</div>
        </div>
        <div class="dl-disk">
          <div class="dl-disk-t"><span>安装磁盘剩余</span><b id="dlFree">118.4 GB</b></div>
          <div class="dl-diskbar"><div class="dl-diskfill" id="dlFreeBar" style="width:64%"></div></div>
        </div>
      </div>

      <div class="dl-list" id="dlList"></div>

      <div class="dl-note">
        <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 8v4M12 16h.01"/></svg>
        <div><b>下哪一个？</b>只想快点有声音 → <b>VoxCPM2</b>（中文零样本，速度和音质均衡）；只想不占显存 → <b>千问3-TTS</b>（走 CPU）。占用和能不能跑，按你显卡实时算（来自 <code>getVoiceVramAdvice</code>）。模型只下一次，换角色不用重下。</div>
      </div>

      <button class="dl-have" id="dlHaveBtn" type="button">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 7v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2H3z"/><path d="M3 7l2-3h4l2 3M9 7l2-3h4l2 3"/></svg>
        <span><b>电脑里已经下过模型？</b>指认目录让它认（不用重下）</span>
      </button>

      <div class="dock">
        <div class="info">
          <div id="dlInfo">选一个引擎下载</div>
          <div class="sub">下载随时可暂停、可取消；已下载的引擎可以卸载回收空间（真实进度走 getVoiceDownloadStatus 轮询）</div>
        </div>
        <button class="btn primary" id="dlStartBtn" type="button" disabled>下载所选引擎</button>
      </div>
    </div>
  </div>
</div>

<div id="toast"><span class="dot"></span><span id="toastTxt"></span></div>

<!-- ============ 模态 1：选路（点喇叭 → 云端 / 本地 二选一） ============ -->
<div class="mask" id="routeMask">
  <div class="vmodal" role="dialog" aria-modal="true" style="width:min(520px,100%)">
    <div class="vm-head">
      <div>
        <div class="vm-title">给「<span id="rtName">她</span>」配一个专属声音</div>
        <div class="vm-sub">她现在还没有声音。两条路都能拿到「只属于她」的音色，选一条：</div>
      </div>
      <button class="vm-x" id="rtClose" type="button" aria-label="关闭"><svg viewBox="0 0 24 24"><path d="M18 6L6 18M6 6l12 12"/></svg></button>
    </div>
    <div class="routes">
      <button class="route rec" id="rtLocal" type="button">
        <span class="r-ic"><svg viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16" rx="3"/><path d="M9 9h6v6H9z"/></svg></span>
        <span>
          <span class="r-t">在电脑上克隆 <span class="r-badge">免费 · 推荐</span></span>
          <span class="r-d">传一段 10 秒左右的清晰人声，本地引擎<strong>直接就能用</strong>——不用注册、不花额度、音频不出电脑。</span>
        </span>
        <span class="r-go" id="rtLocalGo">开始配置 →</span>
      </button>
      <button class="route" id="rtCloud" type="button">
        <span class="r-ic"><svg viewBox="0 0 24 24"><path d="M17 18a4 4 0 0 0 .8-7.9A6 6 0 0 0 6.2 9.4 3.5 3.5 0 0 0 7 18z"/></svg></span>
        <span>
          <span class="r-t">去云端克隆</span>
          <span class="r-d">在服务商后台上传素材克隆，拿一个音色编号填回来。音质最自然，按量计费。</span>
        </span>
        <span class="r-go">查看详情 →</span>
      </button>
    </div>
    <div class="vm-foot">
      <button class="btn" id="rtLater" type="button">稍后再说</button>
    </div>
  </div>
</div>

<!-- ============ 模态 1b：云端音色 · 服务商网格（v7：照能力引擎页，全走适配器） ============ -->
<div class="mask" id="cdMask">
  <div class="vmodal" role="dialog" aria-modal="true" style="width:min(680px,100%)">
    <div class="vm-head">
      <div class="vm-hrow">
        <span class="vm-tico" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 3.5v10.2"/><circle cx="8.6" cy="16.9" r="3.4"/><path d="M12 5.8l6.2-1.6v8.6"/><circle cx="15.4" cy="15.4" r="3.4"/></svg></span>
        <div>
          <div class="vm-title">给「<span id="cdTitle">她</span>」配一个云端音色</div>
          <div class="vm-sub">这些就是「连引擎」页支持的全部 TTS 服务商——在这里填 Key，和那边写的是同一处。</div>
        </div>
      </div>
      <button class="vm-x" id="cdClose" type="button" aria-label="关闭"><svg viewBox="0 0 24 24"><path d="M18 6L6 18M6 6l12 12"/></svg></button>
    </div>

    <!-- 适配器状态条：slots.tts.state（ok/warn/bad/none） -->
    <div class="cd-state" id="cdState">
      <span class="dot"></span><span id="cdStateTxt">读取能力槽状态…</span>
    </div>

    <!-- 服务商网格：TTS_PROVIDERS 全量（含不可克隆的，诚实展示） -->
    <div class="cd-grid" id="cdGrid"></div>

    <!-- 选中可克隆商家 → 克隆教学 + voice_id -->
    <div class="cd-clone" id="cdClone" style="display:none">
      <div class="cd-clone-h">
        <span class="cd-clone-logo" id="cdCloneLogo"></span>
        <div>
          <div class="cd-clone-t" id="cdCloneName">去克隆</div>
          <div class="cd-clone-s" id="cdCloneHint"></div>
        </div>
        <span class="cd-wave" aria-hidden="true"><i></i><i></i><i></i></span>
      </div>
      <div class="cd-steps" id="cdSteps"></div>
      <div class="vm-field">
        <label for="cdVid">音色编号 voice_id（克隆完成后在服务商后台复制）</label>
        <!-- 【2026-10-18 改版·自动拉取】选阿里云时自动读取账号下已克隆音色，
             渲染成下拉选择，免手输；手动输入框保留兜底。 -->
        <select id="cdVidSelect" style="display:none;width:100%"></select>
        <input id="cdVid" type="text" placeholder="qwen-audio-3.0-tts-plus-bailian-..." spellcheck="false">
        <div class="aihint" id="cdVidHint">形如 <code>qwen-audio-3.0-tts-plus-bailian-c64fdf07</code>；Fish Audio 填克隆模型的 <code>_id</code>（上方「模型」一起改）</div>
      </div>
    </div>

    <!-- 选中不可克隆商家 → 诚实说明，不装能克隆 -->
    <div class="cd-noclone" id="cdNoClone" style="display:none">
      <div class="vm-warn on">
        <span id="cdNoCloneTxt"></span>
      </div>
    </div>

    <div class="vm-foot">
      <button class="btn" id="cdLater" type="button">稍后再说</button>
      <button class="btn primary" id="cdSave" type="button">保存并试听</button>
    </div>
  </div>
</div>


<!-- ============ 模态 2：本地克隆（预填她的参考音频，直接克隆） ============ -->
<div class="mask" id="cloneMask">
  <div class="vmodal" role="dialog" aria-modal="true">
    <div class="vm-head">
      <div>
        <div class="vm-title">给「<span id="clName">她</span>」克隆声音</div>
        <div class="vm-sub">在电脑上跑：免费、音频不出电脑、不花额度，传一段音频直接就能用</div>
      </div>
      <button class="vm-x" id="clClose" type="button" aria-label="关闭"><svg viewBox="0 0 24 24"><path d="M18 6L6 18M6 6l12 12"/></svg></button>
    </div>

    <div class="vm-warn" id="clModelWarn">
      <span>还差本地语音模型（约 2.4~5.4GB，按引擎不同）——本地克隆要在电脑上跑，先下一个，只下一次。</span>
      <button class="btn sm" id="clGoDl" type="button">去下载</button>
    </div>

    <div class="vm-field">
      <label>参考音频（已自动填入她的）<span class="autofill"><svg viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg>自动填入</span></label>
      <div class="audio-row" id="clAudioRow">
        <span class="a-ic"><svg viewBox="0 0 24 24"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg></span>
        <div class="a-meta">
          <div class="a-name" id="clAudioName">xiaowei.wav</div>
          <div class="a-sub" id="clAudioSub">她的原始录音 · 9 秒</div>
        </div>
        <button class="a-play" id="clAudioPlay" type="button" aria-label="试听参考音频">
          <svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
        </button>
      </div>
      <div class="aihint" id="clAudioHint" style="display:none">她还没有参考音频。本地克隆零门槛：选一段 10 秒左右的清晰人声（mp3/wav），引擎直接就能用。</div>
      <button class="btn sm pickref" id="clPickAudio" type="button" style="display:none">
        <svg viewBox="0 0 24 24"><path d="M12 3v12M8 7l4-4 4 4"/><path d="M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/></svg>选择音频文件
      </button>
    </div>

    <div class="vm-field">
      <label>参考文本（音频里说的话，帮引擎对齐）</label>
      <textarea id="clRefText" placeholder="音频里她说的内容"></textarea>
    </div>

    <div class="vm-field">
      <label>克隆引擎（来自适配器 _clone_engine_spec · 5 个）</label>
      <div class="engines" id="clEngines"></div>
    </div>

    <div class="progwrap" id="clProgWrap">
      <div class="progbar"><div class="progfill" id="clProgFill"></div></div>
      <div class="progmeta"><span id="clProgTxt">正在本地克隆…</span><b id="clProgPct">0%</b></div>
    </div>

    <div class="donebox" id="clDone">
      <div class="d-t">克隆完成</div>
      <div class="d-s" id="clDoneSub">音色已写入「小薇」，她说的话以后都是这个声音</div>
    </div>

    <div class="vm-foot">
      <button class="btn" id="clLater" type="button">稍后再说</button>
      <button class="btn primary" id="clStart" type="button">开始克隆</button>
    </div>
  </div>
</div>`;

  function injectCss() {
    if (_cssDone) return;
    _cssDone = true;
    var st = document.createElement('style');
    st.id = 'onbRoleCss';
    st.textContent = CSS;
    document.head.appendChild(st);
    /* ★ 2026-10-13 修「专属声音点了也无效」：真实引导页里模块挂在带 transform 的
       滑轨(#track)里，position:fixed 的弹层(.mask/#toast)会以滑轨为包含块 ——
       第 4 步时整层弹层被平移到视口左侧三千多像素外，用户看到的就是「点了没反应」
       （CDP 实测 routeMask rect.x=-3630）。修法：弹层/Toast 挂到 body 上的
       #onbRolePortal（脱离 transform 链），并把同一份 CSS 换个作用域根再注入一份。 */
    var st2 = document.createElement('style');
    st2.id = 'onbRolePortalCss';
    st2.textContent = CSS.split('#onbRoleRoot').join('#onbRolePortal');
    document.head.appendChild(st2);
  }
  /* 把 .mask / #toast 搬进 body 上的 portal（幂等；主题属性跟随根节点同步） */
  function ensurePortal() {
    if (!_root) return null;
    var portal = document.getElementById('onbRolePortal');
    if (!portal) {
      portal = document.createElement('div');
      portal.id = 'onbRolePortal';
      document.body.appendChild(portal);
      new MutationObserver(function () {
        var dt = _root && _root.getAttribute('data-theme');
        if (dt) portal.setAttribute('data-theme', dt); else portal.removeAttribute('data-theme');
      }).observe(_root, { attributes: true, attributeFilter: ['data-theme'] });
    }
    var dt = _root.getAttribute('data-theme');
    if (dt) portal.setAttribute('data-theme', dt); else portal.removeAttribute('data-theme');
    Array.prototype.slice.call(_root.querySelectorAll('.mask, #toast')).forEach(function (el) {
      portal.appendChild(el);
    });
    return portal;
  }

  /* ==================== 主逻辑（原设计稿 JS，数据源接入真实桥） ==================== */
  function mainLogic() {

/* ================= 数据（config.yaml 真实结构） =================
   avatar 解析链与软件内角色卡完全一致：
     角色自己的 avatar > 该角色绑定主题的 avatar 图位 > 人形占位（不用首字、不乱对应）
   voice.kind 分类：cloud=云端音色 / local=本地克隆 / builtin=内置合成音（edge-tts 免费）      */
var PRESETS = [
  /* 2026-10-12 v6：所有角色默认「未配置」——声音都要用户手动配（开发者拍板）。
     demo 字段=config.yaml 真实数据，仅评审开关「按真实数据」时用于演示。 */
  {id:'danya', name:'达妮娅', c1:'#FF8FC7', c2:'#A06BFF', avatar:'_role_assets/default.png',
   tag:'语气轻缓慢悠悠，爱说俏皮话',
   desc:'星炬学院虚质科学部学生，粉发蓝眸，总挂着温柔的微笑。表面温柔无害，其实藏着自己的小心思。',
   voice:{kind:'none'},
   demo:{kind:'cloud', vid:'qwen-audio-3.0-tts-plus-bailian-c64fdf07'}},
  {id:'xiaowei', name:'小薇', c1:'#2DD4FF', c2:'#5DCAA5', avatar:'',
   tag:'会喊你「亲爱的」，偶尔撒娇',
   desc:'温柔体贴的二次元女友，偶尔傲娇，关心你的健康和作息，能看见屏幕活动陪你工作。',
   voice:{kind:'none'},
   demo:{kind:'local', engine:'voxcpm',
     ref:{name:'xiaowei.wav', path:'D:/GPT-SoVITS/ref_audio/xiaowei.wav', url:'_role_assets/ref/xiaowei.wav',
          secs:9, text:'今天过得怎么样呀？不要太辛苦哦，我会一直陪在你身边的，有什么想说的都可以告诉我。'}}},
  {id:'ai_teacher', name:'AI老师', c1:'#7F77DD', c2:'#378ADD', avatar:'',
   tag:'专业但亲切，爱用类比讲概念',
   desc:'耐心细致的学习伙伴，擅长讲解编程和技术概念，风格严谨但不生硬。',
   voice:{kind:'none'}, demo:{kind:'builtin', engine:'edge-tts'}},
  {id:'cute_friend', name:'萌友', c1:'#EF9F27', c2:'#D4537E', avatar:'',
   tag:'活泼俏皮，网络用语和颜文字',
   desc:'元气满满的虚拟朋友，喜欢吐槽和卖萌，总能给你带来快乐。',
   voice:{kind:'none'}, demo:{kind:'builtin', engine:'edge-tts'}},
  {id:'蓝色大肥鱼', name:'大肥鱼', c1:'#378ADD', c2:'#1D9E75', avatar:'_role_assets/fatfish.jpg',
   tag:'中文短句，干净利落，叫你「你」',
   desc:'一只住在电脑里的蓝色小鲸鱼。聪明、贪吃、爱面子，喜欢解题，遇到难题会眼睛一亮。',
   voice:{kind:'none'}, demo:{kind:'builtin', engine:'edge-tts'}}
];

/* S.engine：第 1 步「连引擎」配的 TTS 状态（真实环境=能力槽 tts 槽 health）
   cloud=云端已连(阿里云Key) / local=本地语音模型就绪 / none=未配
   S.cloneModel：本地克隆模型是否已下载（voxcpm/qwen3tts…）
   S.sim：云端合成探测结果（真实环境=百炼 4xx/418，如 voice_id 过期） */
var S = {picked:'danya', mode:'pick', engine:'cloud', cloneModel:false, sim:'ok', cloned:{}, voiceDemo:false, dl:{}};

function $(id){
  var el = _root ? _root.querySelector('[id="' + id + '"]') : null;
  /* ★ 弹层/Toast 已搬进 body 的 #onbRolePortal（见 ensurePortal），根里查不到时回退全文档 */
  return el || document.getElementById(id);
}
function esc(s){ return String(s==null?'':s).replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m])); }
function findChar(id){ return PRESETS.filter(function(x){ return x.id===id; })[0]; }

/* 人形占位（软件内角色卡同款） */
var PH_AV = '<span class="ph-av" aria-hidden="true"><svg viewBox="0 0 24 24"><circle cx="12" cy="8.2" r="3.7"/><path d="M12 13.7c-4.15 0-7.5 2.34-7.5 5.23 0 .63.53 1.14 1.18 1.14h12.64c.65 0 1.18-.51 1.18-1.14 0-2.89-3.35-5.23-7.5-5.23z"/></svg></span>';

/* 语音分类 pill：只显示分类，不显示模型名；与第 1 步引擎状态联动 */
function voiceOf(c){
  /* voiceDemo=false（默认）：所有角色一律「未配置」，声音要用户手动配（开发者拍板）
     voiceDemo=true：按 config.yaml 真实数据演示各分类 */
  if(S.voiceDemo && c.demo) return c.demo;
  return c.voice || {kind:'none'};
}
function pillOf(c){
  var v = voiceOf(c);
  if(S.cloned[c.id]) return {cls:'ok', text:'专属音色 · 已配置'};
  if(v.kind==='cloud'){
    if(S.engine==='none') return {cls:'off', text:'云端音色 · 待配置'};
    return {cls:'ok', text:'云端音色'};
  }
  if(v.kind==='local'){
    if(!S.cloneModel) return {cls:'off', text:'本地克隆 · 模型未下'};
    return {cls:'ok', text:'本地克隆'};
  }
  if(v.kind==='builtin') return {cls:'', text:'内置合成音'};
  return {cls:'off', text:'未配置'};
}

function renderGrid(){
  var box = $('pgrid');
  box.innerHTML = '';
  PRESETS.forEach(function(c, i){
    var v = c.voice || {};
    var p = pillOf(c);
    var av = c.avatar
      ? '<img src="'+esc(c.avatar)+'" alt="'+esc(c.name)+'">'
      : PH_AV;
    var card = document.createElement('article');
    card.className = 'pcard' + (c.id===S.picked ? ' sel' : '');
    card.setAttribute('data-char-id', c.id);
    card.style.setProperty('--i', i);
    card.innerHTML =
      '<div class="tick"><svg viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg></div>'+
      '<div class="phead">'+
        '<div class="pav">'+av+'</div>'+
        '<div><div class="pname">'+esc(c.name)+'</div><span class="ptag">'+esc(c.tag)+'</span></div>'+
      '</div>'+
      '<div class="pdesc">'+esc(c.desc)+'</div>'+
      '<div class="pfoot">'+
        '<span class="pill '+p.cls+'">'+esc(p.text)+'</span>'+
        '<button class="vbtn" type="button" aria-label="试听 '+esc(c.name)+' 的声音">'+
          '<svg class="ico" viewBox="0 0 24 24"><path d="M11 5L6 9H2v6h4l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.4 5.6a9 9 0 0 1 0 12.8"/></svg>'+
          '<span class="bars"><i></i><i></i><i></i></span>'+
        '</button>'+
      '</div>';
    card.addEventListener('click', function(ev){
      if(ev.target.closest('.vbtn')) return;
      pick(c.id);
    });
    card.querySelector('.vbtn').addEventListener('click', function(ev){
      ev.stopPropagation();
      tryVoice(c, this);
    });
    box.appendChild(card);
  });

  /* 末尾虚线卡：自己造一个 */
  var nw = document.createElement('article');
  nw.className = 'pnew';
  nw.innerHTML = '<div><div class="plus"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg></div><b>自己造一个</b><span>起个名字，AI 帮你写人设</span></div>';
  nw.addEventListener('click', openNew);
  box.appendChild(nw);
}

/* 换人：只切 class + 新卡摘挂动画类重播 m-pop/m-ping，绝不重建 DOM（修「换人整页动效刷新」） */
function pick(id){
  S.picked = id;
  var cards = document.querySelectorAll('.pcard');
  for(var i=0;i<cards.length;i++){
    var el = cards[i];
    var on = el.getAttribute('data-char-id')===id;
    el.classList.toggle('sel', on);
    if(on){
      var tick = el.querySelector('.tick');
      el.classList.remove('sel-anim'); void el.offsetWidth; el.classList.add('sel-anim');
      tick.classList.remove('ping'); void tick.offsetWidth; tick.classList.add('ping');
    }
  }
  var c = findChar(id);
  if(c) $('dockName').textContent = c.name;
  syncVoiceAct();
}

/* pill 同步（评审开关切换时用，不重建 DOM） */
function syncPills(){
  var cards = document.querySelectorAll('.pcard');
  for(var i=0;i<cards.length;i++){
    var c = findChar(cards[i].getAttribute('data-char-id'));
    if(!c) continue;
    var p = pillOf(c);
    var pill = cards[i].querySelector('.pill');
    pill.className = 'pill ' + p.cls;
    pill.textContent = p.text;
  }
}

/* ================= 引擎联动条 ================= */
function renderEngineBar(){
  var bar = $('ebar');
  bar.className = 'ebar ' + (S.engine==='cloud' ? 'ok' : S.engine==='local' ? 'local' : 'warn');
  if(S.engine==='cloud'){
    $('ebState').textContent = '云端已连 · 阿里云百炼';
    $('ebHint').textContent = '云端角色可以直接合成；本地克隆随时也能用';
  }else if(S.engine==='local'){
    $('ebState').textContent = '本地语音模型就绪 · VoxCPM2';
    $('ebHint').textContent = '本地克隆可用；云端角色要用了再配 Key';
  }else{
    $('ebState').textContent = '未配置 TTS 引擎';
    $('ebHint').textContent = '回到第 1 步「连引擎」配一个，她才能开口';
  }
}

/* ================= 试听：四态分流 ================= */
var _audio = null;
function stopAudio(){ if(_audio){ try{ _audio.pause(); }catch(e){} _audio = null; } }

function play(btn, c){
  stopAudio();
  [].forEach.call(document.querySelectorAll('.vbtn.playing'), function(b){ b.classList.remove('playing'); });
  btn.classList.add('playing');
  toast('正在播放…「你好呀，我是'+c.name+'～」');
  setTimeout(function(){ btn.classList.remove('playing'); }, 2600);
}

/* ---- 真实试听（test_voice 异步任务 + get_voice_test_status 轮询） ---- */
function ttsCfgOf(c){
  /* 与设置页 buildTestCfg 同款字段；云端走 engine/api_key/model/voice_id，本地走引擎 + 参考音 */
  var v = c.voice || {}, rv = (c._raw && c._raw.voice) || {};
  var eng = v.engine || rv.engine || 'dashscope_cosy';
  return {
    engine: eng,
    api_key: rv.api_key || '',
    model: rv.model || '',
    voice_id: v.vid || rv.voice_id || (c._raw && c._raw.cloud_voice_id) || '',
    api_url: rv.api_url || '',
    reference_audio: rv.reference_audio || '',
    reference_text: rv.reference_text || '',
    rate: '+0%', pitch: '+0Hz', volume: '+0%', speed: 1.0
  };
}
function playAudioB64(b64, ext){
  if(!b64) return false;
  stopAudio();
  try {
    var a = new Audio('data:audio/' + (ext || 'mp3') + ';base64,' + b64);
    _audio = a; a.play().catch(function(){});
    return true;
  } catch(e){ return false; }
}
function realTryVoice(c, btn){
  var B = _b();
  Promise.resolve(B.testVoice(ttsCfgOf(c), '你好呀，我是' + c.name + '～')).then(function(r){
    if(!(r && r.success && r.task_id)){
      btn.classList.remove('probing');
      toast('试听启动失败：' + ((r && (r.error || r.message)) || '无返回'));
      return;
    }
    pollVoiceTest(r.task_id, btn, c, 0);
  }).catch(function(e){
    btn.classList.remove('probing');
    toast('试听失败：' + e);
  });
}
function pollVoiceTest(tid, btn, c, n){
  if(n > 120){ btn.classList.remove('probing'); toast('试听超时，稍后再试'); return; }
  var B = _b();
  Promise.resolve(B.getVoiceTestStatus(tid)).then(function(r){
    if(!r || r.phase === 'gone' || r.done){
      btn.classList.remove('probing');
      if(r && r.result_success && r.audio_b64){ playAudioB64(r.audio_b64, r.audio_ext); }
      else {
        toast('合成没成功：' + ((r && r.error) || '编号可能失效，或 Key 没有这个音色的权限'));
        openCloudDetail(c);
      }
      return;
    }
    setTimeout(function(){ pollVoiceTest(tid, btn, c, n + 1); }, 500);
  }).catch(function(){ btn.classList.remove('probing'); });
}

function tryVoice(c, btn){
  /* v7：默认「未配置」→ 选路卡；演示模式保留四态分流（都走新组件，无旧卡） */
  var v = voiceOf(c);
  if(!S.voiceDemo && v.kind==='none'){ openRoute(c); return; }
  /* ① 云端音色角色：真实环境走 testVoice 轮询；无桥时保留稿内探测演示 */
  if(v.kind==='cloud'){
    if(S.engine==='none'){ openCloudDetail(c); return; }
    btn.classList.add('probing');
    var Bv = _b();
    if(Bv && Bv.testVoice && Bv.getVoiceTestStatus){ realTryVoice(c, btn); return; }
    setTimeout(function(){
      btn.classList.remove('probing');
      if(S.sim==='ok') play(btn, c);
      else { toast('试着合成了一小句没成功：编号可能失效，或 Key 没有这个音色的权限'); openCloudDetail(c); }
    }, 900);
    return;
  }
  /* ② 本地克隆角色：直接开克隆面板（参考音频已自动填入她的） */
  if(v.kind==='local'){
    openCloneCard(c);
    return;
  }
  /* ③ 内置合成音：免费直接响；想要专属声音给明确出口 */
  if(v.kind==='builtin'){
    play(btn, c);
    setTimeout(function(){ toast('这是通用合成音。想要只属于她的声音？点下方「给她一个专属声音」'); }, 700);
    return;
  }
  /* ④ 未配置 */
  openRoute(c);
}

/* dock 里「给她一个专属声音」：角色还没有真正可用的专属声音时都显示 */
function syncVoiceAct(){
  var c = findChar(S.picked);
  var btn = $('voiceActBtn');
  if(!c || !btn) return;
  var v = voiceOf(c);
  /* v6：默认未配置时显示入口（引导的核心动作）。
     v8 修（2026-10-12）：原判据只看 kind=builtin/none，于是
       「角色按云端音色（kind=cloud）+ 第 1 步没连云端引擎（S.engine!=='cloud'）」
     时，pill 已经显示「云端音色 · 待配置」，可 dock 里却没有任何配置入口可点
     —— 用户实测「没有云端引擎也应该有配声音的地方，可是没出现」。
     改为：只要这个角色的声音还没真正可用（内置/未配/云端待配/本地缺模型），就给入口，
     点进去仍是 云 / 本地 两条路选（openRoute），已绑云端时表单自动只读、只引导去拿 voice_id。 */
  /* 2026-10-12 修（反馈⑤）：克隆入口「给她一个专属声音」在角色尚未克隆时始终显示，
     不再受 needsCfg 门控——用户实测达妮娅默认云端音色 + 云端引擎已连 ⇒ needsCfg=false
     ⇒ 入口被隐藏，导致「点开本地克隆/云端克隆的按钮」消失。
     现在：只要 S.cloned[c.id] 还没置位，入口就常驻（点了仍走 本地/云端 两条路）。 */
  var on = !S.cloned[c.id];
  btn.classList.toggle('on', on);
}

/* ================= 模态 1：选路 ================= */
/* v10（2026-10-12 反馈⑤）：openRoute 全程 try/catch——任何一步失败都 toast 原因，
   绝不再「点了没反应」式静默失败（用户实测真机点击无响应且无任何提示）。 */
function openRoute(c){
  try{
    _rtChar = c;
    $('rtName').textContent = c.name;
    /* 本地路线按钮文案随模型状态变化 */
    $('rtLocalGo').textContent = S.cloneModel ? '开始配置 →' : '去下载模型 →';
    $('routeMask').classList.add('on');
  }catch(e){
    toast('打开选路弹层失败：' + ((e && e.message) || e));
  }
}
/* v10（2026-10-12 反馈⑤）：选路弹层按钮改**事件委托**绑定在模块根上。
   原 addEventListener 直绑在「任何导致子树重建/替换的时序」下会静默失效；
   委托在 _root 上，子树怎么变都命中。 */
(function(){
  /* ★ 2026-10-13：弹层已 portal 到 body（ensurePortal），委托必须挂 document
     才能同时吃到根内与 portal 内的点击。 */
  var host = document;
  host.addEventListener('click', function(ev){
    var t = ev.target;
    if(!(t && t.closest)) return;
    if(t.closest('#rtClose') || t.closest('#rtLater')){
      $('routeMask').classList.remove('on'); return;
    }
    if(t.closest('#rtLocal')){
      $('routeMask').classList.remove('on');
      var c = _rtChar || findChar(S.picked);
      if(S.cloneModel){ openCloneCard(c); } else { openDlView(); }
      return;
    }
    if(t.closest('#rtCloud')){
      $('routeMask').classList.remove('on');
      openCloudDetail(_rtChar || findChar(S.picked));
    }
  });
})();

/* ================= 模态 2：本地克隆 ================= */
var _clChar = null;
function openCloneCard(c){
  _clChar = c;
  stopAudio();
  $('clName').textContent = c.name;
  /* 模型门控 + 引擎列表（适配器数据渲染） */
  $('clModelWarn').classList.toggle('on', !S.cloneModel);
  renderClEngines();
  /* 参考音频：预填她的 */
  var ref = (c.voice && c.voice.ref) || (c.demo && c.demo.ref) || null;
  if(ref){
    $('clAudioName').textContent = ref.name;
    $('clAudioSub').textContent = '她的原始录音 · ' + (ref.secs||'?') + ' 秒 · ' + ref.path;
    $('clRefText').value = ref.text || '';
    $('clAudioRow').style.display = '';
    $('clAudioHint').style.display = 'none';
    $('clPickAudio').style.display = 'none';
  }else{
    $('clAudioRow').style.display = 'none';
    $('clAudioHint').style.display = 'block';
    $('clPickAudio').style.display = '';
    $('clRefText').value = '';
  }
  $('clProgWrap').classList.remove('on');
  $('clProgFill').style.width = '0%';
  $('clProgPct').textContent = '0%';
  $('clDone').classList.remove('on');
  $('clStart').disabled = false;
  $('clStart').textContent = '开始克隆';
  $('cloneMask').classList.add('on');
}
/* 克隆引擎列表：来自适配器（DL_ENGINES = _clone_engine_spec + getVoiceVramAdvice）
   kind=local 可下载选用（未装=锁）；cloud=需 Key；external=要训练 */
function renderClEngines(){
  var box = $('clEngines');
  box.innerHTML = '';
  var anySel = false;
  DL_ENGINES.forEach(function(e){
    var st = dlState(e.id);
    var installed = (st.st === 'installed');
    var usable = (e.kind === 'local') && (installed || S.cloneModel);
    var el = document.createElement('div');
    el.className = 'eng' + (usable ? '' : ' locked') + ((usable && !anySel) ? ' sel' : '');
    el.setAttribute('data-eng', e.id);
    if (usable && !anySel) anySel = true;
    var sub = '';
    if (e.kind === 'local'){
      sub = installed ? '<b>已就绪</b>' : (S.cloneModel ? '可用' : '<span class="need">需先下载模型</span>');
    } else if (e.kind === 'cloud'){
      sub = '<span class="need">云端 API · 需 Key</span>';
    } else {
      sub = '<span class="need">外部服务 · 要训练</span>';
    }
    el.innerHTML = '<span class="e-dot"></span><span class="e-name">' + esc(e.name) + '</span>' +
      '<span class="e-sub">' + sub + '</span>';
    el.addEventListener('click', function(){
      if (el.classList.contains('locked')){
        toast(e.kind === 'local' ? '这个引擎要先下载模型才能用' :
              (e.kind === 'cloud' ? 'MiMo 是小米云端 API：去上面「去填 Key」' : 'GPT-SoVITS：先去起外部服务（127.0.0.1:9880）'));
        return;
      }
      [].forEach.call(box.querySelectorAll('.eng'), function(x){ x.classList.remove('sel'); });
      el.classList.add('sel');
    });
    box.appendChild(el);
  });
}
$('clClose').addEventListener('click', function(){ $('cloneMask').classList.remove('on'); });
$('clLater').addEventListener('click', function(){ $('cloneMask').classList.remove('on'); });
$('clGoDl').addEventListener('click', function(){
  $('cloneMask').classList.remove('on');
  openDlView();
});
/* 参考音频试听（真实播放） */
$('clAudioPlay').addEventListener('click', function(){
  var c = _clChar; if(!c || !c.voice || !c.voice.ref) return;
  var src = c.voice.ref.url || (c.voice.ref.path ? ('/local_img?path=' + encodeURIComponent(c.voice.ref.path)) : '');
  if(!src){ toast('这段音频没法直接播放——重新选一个本地文件即可'); return; }
  stopAudio();
  var a = new Audio(src);
  _audio = a;
  this.classList.add('playing');
  a.play().catch(function(){});
  a.onended = function(){ $('clAudioPlay').classList.remove('playing'); };
});
/* 开始克隆（本地跑，模拟进度；真实环境=本地引擎克隆任务） */
/* 本地克隆=零样本：直接拿参考音频试合成一句，成功即可用（无需训练/注册/额度） */
function realClone(c, eng){
  var B = _b();
  var cfg = {
    engine: eng,
    reference_audio: (c.voice && c.voice.ref && c.voice.ref.path) || '',
    reference_text: (($('clRefText') && $('clRefText').value.trim()) ||
                     (c.voice && c.voice.ref && c.voice.ref.text) || ''),
    rate: '+0%', pitch: '+0Hz', volume: '+0%', speed: 1.0
  };
  return Promise.resolve(B.testVoice(cfg, '你好呀，我是' + c.name + '～')).then(function(r){
    if(!(r && r.success && r.task_id)) throw new Error((r && (r.error || r.message)) || '提交失败');
    return new Promise(function(resolve, reject){
      var n = 0;
      var tick = function(){
        n++;
        if(n > 180) { reject(new Error('合成超时')); return; }
        Promise.resolve(B.getVoiceTestStatus(r.task_id)).then(function(st){
          if(!st || st.phase === 'gone' || st.done){
            if(st && st.result_success) resolve(st);
            else reject(new Error((st && st.error) || '合成失败'));
            return;
          }
          setTimeout(tick, 700);
        }).catch(reject);
      };
      tick();
    });
  });
}

$('clStart').addEventListener('click', function(){
  if(!S.cloneModel){ toast('先下载本地语音模型'); return; }
  var c = _clChar; if(!c) return;
  var btn = this;
  var B = _b();
  var engEl = _root ? _root.querySelector('#clEngines .eng.sel') : null;
  var eng = (engEl && engEl.getAttribute('data-eng')) || 'voxcpm';
  var refPath = (c.voice && c.voice.ref && c.voice.ref.path) || '';
  /* 真实环境：本地克隆走零样本试合成验证（不画假训练进度） */
  if(B && B.testVoice && B.getVoiceTestStatus && refPath){
    btn.disabled = true; btn.textContent = '验证中…';
    $('clProgWrap').classList.add('on');
    $('clProgFill').style.width = '35%'; $('clProgPct').textContent = '35%';
    $('clProgTxt').textContent = '正在用参考音频试合成…';
    realClone(c, eng).then(function(){
      btn.disabled = false; btn.textContent = '开始克隆';
      $('clProgFill').style.width = '100%'; $('clProgPct').textContent = '100%';
      $('clProgTxt').textContent = '可用（本地零样本，无需训练）';
      S.cloned[c.id] = true;
      $('clDoneSub').textContent = '音色已可用于「' + c.name + '」，她说的话以后都是这个声音';
      $('clDone').classList.add('on');
      if(B.saveCharacter){
        Promise.resolve(B.saveCharacter({
          id: c.id, name: c.name,
          voice: {engine: eng, reference_audio: refPath,
                  reference_text: (($('clRefText') && $('clRefText').value.trim()) || '')}
        })).catch(function(){});
      }
      syncPills();
      toast('本地声音已就绪：' + c.name + '（免费 · 音频不出电脑）');
    }).catch(function(err){
      btn.disabled = false; btn.textContent = '开始克隆';
      $('clProgWrap').classList.remove('on');
      toast('试合成失败：' + ((err && err.message) ? err.message : err));
    });
    return;
  }
  /* 无桥降级：稿内模拟进度 */
  btn.disabled = true;
  btn.textContent = '克隆中…';
  $('clProgWrap').classList.add('on');
  var pct = 0;
  var t = setInterval(function(){
    pct = Math.min(100, pct + (6 + Math.random()*9));
    $('clProgFill').style.width = pct + '%';
    $('clProgPct').textContent = Math.round(pct) + '%';
    $('clProgTxt').textContent = pct < 45 ? '正在分析参考音频…' : (pct < 85 ? '正在本地克隆音色…' : '正在写入角色…');
    if(pct >= 100){
      clearInterval(t);
      S.cloned[c.id] = true;
      $('clDoneSub').textContent = '音色已写入「' + c.name + '」，她说的话以后都是这个声音';
      $('clDone').classList.add('on');
      syncPills();
      toast('克隆完成：' + c.name + ' 现在有专属声音了');
      setTimeout(function(){
        $('cloneMask').classList.remove('on');
        var card = document.querySelector('.pcard[data-char-id="'+c.id+'"]');
        if(card){ var vb = card.querySelector('.vbtn'); play(vb, c); }
      }, 1400);
    }
  }, 220);
});

/* ================= 模态 3：云端克隆卡 ================= */
/* ================= 新建 / 微调 ================= */
function openNew(){
  S.mode = 'new';
  $('viewPick').style.display = 'none';
  $('viewNew').style.display = '';
  $('fId').dataset.locked = '';
  $('fIdChip').textContent = '自动生成';
  $('fName').value = ''; $('fHint').value = '';
  $('fChar').value = ''; $('fStyle').value = ''; $('fBg').value = '';
  $('fId').value = 'char_' + Date.now().toString(36);
  $('newInfo').textContent = '新建角色';
  $('saveBtn').textContent = '创建并使用她';
  $('voiceNote').classList.remove('on');
}
function openTweak(){
  var c = findChar(S.picked); if(!c) return;
  S.mode = 'tweak';
  $('viewPick').style.display = 'none';
  $('viewNew').style.display = '';
  $('fName').value = c.name;
  $('fId').value = c.id;
  $('fId').dataset.locked = '1';       /* 微调不重新生成 ID（防改名字把 ID 也改了） */
  $('fIdChip').textContent = '已锁定';
  $('fHint').value = '';
  $('fChar').value = ''; $('fStyle').value = ''; $('fBg').value = '';
  $('newInfo').textContent = '正在微调「' + c.name + '」';
  $('saveBtn').textContent = '保存修改';
  $('voiceNote').classList.add('on');
}
$('tweakBtn').addEventListener('click', openTweak);
$('backBtn').addEventListener('click', function(){
  $('viewNew').style.display = 'none';
  $('viewPick').style.display = '';
});
$('cancelBtn').addEventListener('click', function(){
  $('viewNew').style.display = 'none';
  $('viewPick').style.display = '';
});
$('aiBtn').addEventListener('click', function(){
  var name = $('fName').value.trim();
  var hint = $('fHint').value.trim();
  if(!name && !hint){ toast('先起个名字，或写一句话描述'); return; }
  var B = _b(), btn = this;
  $('genLine').classList.add('on');
  btn.disabled = true;
  /* 真实环境：generate_character（文本 LLM 生成三段人设） */
  if(B && B.generateCharacter){
    Promise.resolve(B.generateCharacter({name:name, hint:hint, search:false})).then(function(r){
      btn.disabled = false; $('genLine').classList.remove('on');
      if(r && r.success){
        $('fChar').value = r.character || '';
        $('fStyle').value = r.speaking_style || '';
        $('fBg').value = r.background || '';
        toast('AI 写好了，三段都可以随便改');
      } else {
        toast('AI 代写失败：' + ((r && r.error) || '请稍后再试'));
      }
    }).catch(function(e){
      btn.disabled = false; $('genLine').classList.remove('on');
      toast('AI 代写失败：' + e);
    });
    return;
  }
  setTimeout(function(){
    btn.disabled = false;
    $('genLine').classList.remove('on');
    var nm = name || '她';
    $('fChar').value = nm + '性格里最显眼的是"反差感"：表面看起来淡定，其实心里活动很密，碰到喜欢的话题会忍不住多说两句，被夸了会假装不在意但记得很清楚。';
    $('fStyle').value = '说话短句多、爱用语气词，开心时句尾会上扬；安静的时候会自动放轻声音，像怕打扰到别人。';
    $('fBg').value = (hint ? (hint + '。') : '') + '和你是很熟的关系，不用客套，会直接说想法，也会在你状态不好的时候安静陪着。';
    toast('AI 写好了，三段都可以随便改');
  }, 1500);
});
$('saveBtn').addEventListener('click', function(){
  var name = $('fName').value.trim();
  if(!name){ toast('给她起个名字'); return; }
  var B = _b();
  var newId = ($('fId') && $('fId').value.trim()) || ('c_' + Date.now().toString(36));
  var back = function(){
    $('viewNew').style.display = 'none';
    $('viewPick').style.display = '';
  };
  /* 无桥：保留稿内演示 */
  if(!(B && B.saveCharacter)){
    back(); toast('已保存「' + name + '」并设为当前角色');
    return;
  }
  var payload = {
    id: newId, name: name,
    character: $('fChar').value.trim(),
    speaking_style: $('fStyle').value.trim(),
    background: $('fBg').value.trim(),
    model_3d: { enabled: false }        /* 非达妮娅：后端也会强制关，这里显式声明 */
  };
  var vid = $('fVoice') ? $('fVoice').value : '';
  if(vid) payload.voice_profile = vid;
  Promise.resolve(B.saveCharacter(payload)).then(function(r){
    if(!(r && r.success)){ toast('保存失败：' + ((r && r.error) || '未知错误')); return; }
    /* 保存不会自动激活 —— 必须补 switch_character，否则「建完不生效」 */
    var done = function(){
      back();
      markRoleConfirmed({ id: newId });   /* v10：自建并使用她也算「角色已确认」 */
      toast('已保存「' + name + '」并设为当前角色');
      loadFromBackend();
    };
    if(B.switchCharacter){ Promise.resolve(B.switchCharacter(newId)).then(done).catch(done); }
    else { done(); }
  }).catch(function(e){ toast('保存失败：' + e); });
});

/* ================= dock ================= */
/* v10（2026-10-12 反馈⑥⑦）：「就用她」成功后给出可见成功态（卡片勾选 ping + 按钮 ✓），
   并置位 window.__onboardRoleConfirmed 通知引导页 refreshNav —— 右下角「下一步」据此放行。 */
function markRoleConfirmed(c){
  try{
    window.__onboardRoleConfirmed = true;
    var w = window, i = 0;
    while(w && i++ < 5){
      if(w && typeof w.__wizardRefreshNav === 'function'){ w.__wizardRefreshNav(); break; }
      var up = w.parent; if(!up || up === w) break; w = up;
    }
  }catch(e){}
  try{
    var card = _root.querySelector('.pcard[data-char-id="' + c.id + '"]');
    if(card){
      card.classList.add('sel', 'sel-anim');
      var tk = card.querySelector('.tick');
      if(tk){ tk.classList.add('ping'); setTimeout(function(){ tk.classList.remove('ping'); }, 1300); }
    }
  }catch(e){}
}
function useHer(){
  var c = findChar(S.picked);
  if(!c) return;
  var B = _b();
  if(!(B && B.switchCharacter)){
    /* 无桥（独立预览）：视作已确认，流程走得通 */
    markRoleConfirmed(c);
    toast('就用「' + c.name + '」——保存并设为当前角色');
    return;
  }
  var btn = $('useBtn');
  Promise.resolve(B.switchCharacter(c.id)).then(function(r){
    if(r && r.offline){
      markRoleConfirmed(c);
      toast('预览：已选定「' + c.name + '」（后端未连接）');
      return;
    }
    if(r && r.success === false){ toast('切换失败：' + ((r && r.error) || '未知错误')); return; }
    markRoleConfirmed(c);
    if(btn){
      btn.textContent = '✓ 已选定她';
      setTimeout(function(){ btn.textContent = '就用她'; }, 2600);
    }
    toast('已用「' + c.name + '」——现在她就是当前角色');
  }).catch(function(e){ toast('切换失败：' + e); });
}
/* v10：dock 两键也改事件委托（与选路弹层同理，杜绝直绑静默失效） */
(function(){
  /* ★ 2026-10-13：同上，委托挂 document（dock 两键本体仍在根内，portal 不含它们） */
  var host = document;
  host.addEventListener('click', function(ev){
    var t = ev.target;
    if(!(t && t.closest)) return;
    if(t.closest('#voiceActBtn')){
      var c = findChar(S.picked);
      if(c) openRoute(c);
      return;
    }
    if(t.closest('#useBtn')){ useHer(); return; }
  });
})();
$('ebEdit').addEventListener('click', function(){
  /* ★ v8 修复（2026-10-12）：原来只弹一句「引导页里不改引擎」= 死路，
     用户实测「配置 tts 引擎点不了，相当于两面锁死」。
     现在真的跳到第 1 步「连引擎」（由引导页暴露 __wizardGoToScreen）；跳不动才退回提示。 */
  var go = null;
  try { if (typeof window.__wizardGoToScreen === 'function') go = window.__wizardGoToScreen; } catch(e){}
  if (!go) { try { var p = window.parent; if (p && typeof p.__wizardGoToScreen === 'function') go = p.__wizardGoToScreen; } catch(e){} }
  if (go) { try { if (go('screenSlots') !== false) { toast('去第 1 步「连引擎」配 TTS 引擎 —— 配好回来她的声音就能用了'); return; } } catch(e){} }
  toast('引导页里不改引擎——回到第 1 步「连引擎」，或之后到 设置 → 模型与API');
});

/* ================= toast ================= */
var _toastT = null;
function toast(msg){
  $('toastTxt').textContent = msg;
  $('toast').classList.add('on');
  clearTimeout(_toastT);
  _toastT = setTimeout(function(){ $('toast').classList.remove('on'); }, 2800);
}

/* ============================================================
   v6 新增逻辑：云端克隆详情（两模式）+ 本地克隆模型下载器
   ============================================================ */
var _rtChar = null, _cdChar = null;

/* ---------- 云端克隆详情卡 ---------- */
/* ── 适配器形状 mock：getCapabilitySlots() → slots.tts.providers ──
   真相源 slot_config.TTS_PROVIDERS（4 家，字段原样：id/name/logo/rank/clone/base_url/models/hint/needs_group）*/
var TTS_PROVIDERS = [
  {id:"dashscope_cosy", name:"阿里云 CosyVoice", logo:"alibaba", rank:1, clone:"y",
   base_url:"https://dashscope.aliyuncs.com/api-ws/v1/inference",
   models:[{id:"qwen-audio-3.0-tts-plus"},{id:"cosyvoice-v3.5-plus"},{id:"cosyvoice-v3.5-flash"}],
   hint:"克隆走 voice-enrollment；复刻音色有有效期",
   configured:true, active:true},   /* 默认「云端已连」=第1步连引擎已绑百炼（与 devTts 默认值自洽） */
  {id:"minimax", name:"MiniMax Speech", logo:"minimax", rank:2, clone:"y",
   base_url:"https://api.minimaxi.com/v1/t2a_v2",
   models:[{id:"speech-02-hd"},{id:"speech-02-turbo"}],
   needs_group:true, hint:"除 Key 外还需 GroupId；域名 api.minimaxi.com",
   configured:false, active:false},
  {id:"fish_audio", name:"Fish Audio", logo:"fishaudio", rank:3, clone:"y",
   base_url:"https://api.fish.audio/v1/tts",
   models:[{id:"(克隆模型 _id)"}],
   hint:"模型名填克隆得到的 _id；GET /v1/model?self=true 查",
   configured:false, active:false},
  {id:"openai_tts", name:"OpenAI TTS", logo:"openai", rank:null, clone:"n",
   base_url:"https://api.openai.com/v1/audio/speech",
   models:[{id:"gpt-4o-mini-tts"},{id:"tts-1"}],
   hint:"不可克隆，仅基础合成",
   configured:false, active:false}
];
/* 克隆教学文案（按厂商 id 查；缺省用通用三步）。厂商身份/模型全部来自上表，不另建清单 */
var CLONE_STEPS = {
  dashscope_cosy:[
    "打开 <b>阿里云百炼控制台</b> → 语音合成 → <b>声音克隆</b>",
    "上传 10~30 秒干净人声，提交后等几分钟",
    "克隆完成后在音色列表 <b>复制 voice_id</b>，粘贴到下面"
  ],
  minimax:[
    "打开 <b>MiniMax 开放平台</b> → 语音 → <b>声音复刻</b>",
    "上传 10 秒~5 分钟音频（需完成平台实名认证）",
    "复刻完成后复制 <b>voice_id</b>，粘贴到下面"
  ],
  fish_audio:[
    "打开 <b>Fish Audio</b> → 我的模型（My Models）",
    "上传参考音频创建克隆模型，复制它的 <b>_id</b>",
    "把 _id 填进上方「模型」下拉（这家模型名就是克隆 id）"
  ],
  _default:[
    "登录你常用的 TTS 服务商后台，找到 <b>声音克隆 / 音色复刻</b> 入口",
    "按它的要求上传参考音频，完成克隆",
    "复制它给的 <b>音色编号</b>，粘贴到下面"
  ]
};
/* ============================================================
   v7 云端音色卡：服务商网格 = 适配器 TTS_PROVIDERS 全量
   读：getCapabilitySlots() → slots.tts.{state,state_text,providers}
   写：setCapabilitySlot("tts", {provider, model, api_key, group_id})
       + saveCharacter({voice:{...voice_id}})  （音色编号落在角色上）
   ============================================================ */
var _cdChar = null, _cdVendor = null;

function vlgTile(p){
  /* mock 用首字色块；真实落地换 provider_logos.js 的 logo(p.logo, 22) */
  var palette = {dashscope_cosy:"#FF6A00", minimax:"#E4432F", fish_audio:"#7C5CFF", openai_tts:"#10A37F"};
  return '<span class="vlg" style="background:'+(palette[p.id]||"#666")+'22;color:'+(palette[p.id]||"#999")+'">'+esc(p.name.slice(0,1))+'</span>';
}

function renderCdGrid(){
  var box = $("cdGrid");
  box.innerHTML = "";
  /* 关键分流：第 1 步「连引擎」有没有绑 TTS。
     已绑 ⇒ 不再出现绑定表单（API Key / 模型下拉），只读展示 + 直接引导去克隆；
     未绑 ⇒ 完整绑定表单（和第 1 步写同一处：能力槽 tts）。 */
  var bound = (S.engine === "cloud");
  TTS_PROVIDERS.forEach(function(p){
    var card = document.createElement("div");
    card.className = "cdv" + (p.active ? " sel" : "");
    card.setAttribute("data-vid", p.id);
    var bs = "";
    if (p.rank != null && p.rank <= 3) bs += '<span class="cy">第'+p.rank+(p.rank===1?" · 首选":"")+'</span>';
    bs += '<span class="'+(p.clone==="y"?"cy":"cn")+'">'+(p.clone==="y"?"可克隆":"不可克隆")+'</span>';
    if (bound){
      bs += '<span class="cfg'+(p.configured?"":" no")+'">'+(p.configured?"已配置 · 正在用":"未配置")+'</span>';
    } else if (p.configured){
      bs += '<span class="cfg">已配置</span>';
    }
    var mid = (p.models && p.models[0]) ? ((typeof p.models[0] === "string") ? p.models[0] : p.models[0].id) : "";
    var body;
    if (bound){
      body = '<div class="cdv-f cdv-ro"><label>模型</label><div class="cdv-roval">'+esc(mid)+'</div></div>'+
        (p.configured
          ? '<div class="cdv-boundok">TTS 已绑定（第 1 步「连引擎」配过）——直接去克隆，拿编号回来填</div>'
          : '<div class="cdv-boundno">这家还没配 Key。选中后若要用它，保存时会提示你回「连引擎」配</div>');
    } else {
      var opts = (p.models||[]).map(function(m){
        var id = (typeof m === "string") ? m : m.id;
        return '<option value="'+esc(id)+'">'+esc(id)+'</option>';
      }).join("");
      var keyFld = '<div class="cdv-f"><label>API Key</label>'+
        '<input class="cdv-key" type="password" placeholder="sk-... 填入即连" autocomplete="off" value=""></div>';
      if (p.needs_group) keyFld += '<div class="cdv-f"><label>GroupId（MiniMax 需要）</label>'+
        '<input class="cdv-group" type="text" placeholder="GroupId" autocomplete="off"></div>';
      body = '<div class="cdv-f"><label>模型（同 Key 任选）</label><select class="cdv-model">'+opts+'</select></div>'+
        keyFld;
    }
    card.innerHTML =
      '<div class="cdv-h">'+vlgTile(p)+'<div class="cdv-n">'+esc(p.name)+'</div>'+
        (p.active ? '<span class="cdv-pick">已选</span>' : '')+'</div>'+
      '<div class="cdv-b">'+bs+'</div>'+
      body+
      (p.hint ? '<div class="cdv-hint"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 8v4M12 16h.01"/></svg><span>'+esc(p.hint)+'</span></div>' : "");
    card.addEventListener("click", function(ev){
      if (ev.target.tagName === "INPUT" || ev.target.tagName === "SELECT") return;
      selectCdVendor(p.id);
    });
    /* 错峰入场：复用全局 .an（m-rise）；动画跑完摘掉，否则 fill:both 会锁死 transform、悬停浮起失效 */
    card.classList.add("an");
    card.style.animationDelay = (TTS_PROVIDERS.indexOf(p) * 0.055) + "s";
    card.addEventListener("animationend", function onEnd(ev){
      if (ev.animationName !== "m-rise") return;
      card.classList.remove("an"); card.style.animationDelay = "";
      card.removeEventListener("animationend", onEnd);
    });
    box.appendChild(card);
  });
}

function findProv(id){ return TTS_PROVIDERS.filter(function(x){ return x.id===id; })[0] || null; }

function selectCdVendor(id){
  _cdVendor = id;
  [].forEach.call(document.querySelectorAll(".cdv"), function(x){
    var on = (x.getAttribute("data-vid") === id);
    x.classList.toggle("sel", on);
    /* 已选徽章跟随：挂到新选中的卡（新建元素 → m-pop 会重放）、从其它卡摘掉 */
    var old = x.querySelector(".cdv-h .cdv-pick");
    if (on && !old){
      var b = document.createElement("span");
      b.className = "cdv-pick"; b.textContent = "已选";
      x.querySelector(".cdv-h").appendChild(b);
    } else if (!on && old){
      old.parentNode.removeChild(old);
    }
  });
  var p = findProv(id);
  if (!p) return;
  if (S.engine === "cloud" && !p.configured){
    toast("「" + p.name + "」还没配 Key——保存时会提示你回第 1 步「连引擎」配");
  }
  if (p.clone === "y"){
    $("cdClone").style.display = "";
    $("cdNoClone").style.display = "none";
    $("cdCloneLogo").innerHTML = vlgTile(p);
    $("cdCloneName").textContent = "去「" + p.name + "」克隆她的声音";
    $("cdCloneHint").textContent = p.hint || "";
    /* 声波指示重放：重建节点让 m-wave 重新计时 */
    (function(){
      var w = document.querySelector("#cdClone .cd-wave");
      if (!w) return;
      var clone = w.cloneNode(true);
      w.parentNode.replaceChild(clone, w);
    })();
    var steps = CLONE_STEPS[p.id] || CLONE_STEPS._default;
    $("cdSteps").innerHTML = steps.map(function(t, i){
      return '<div class="cd-step" style="animation-delay:'+(i*0.075)+'s"><span class="cs-n">'+(i+1)+'</span><div>'+t+'</div></div>';
    }).join("");
    $("cdVid").value = (_cdChar && _cdChar.demo && _cdChar.demo.vid) || "";
    /* 【2026-10-18】选中百炼后自动读取账号下克隆音色（下拉），免手输 */
    setTimeout(function(){ try{ fetchCdVoices(true); }catch(e){} }, 120);
  } else {
    $("cdClone").style.display = "none";
    $("cdNoClone").style.display = "";
    $("cdNoCloneTxt").textContent = p.name + " 只有通用合成音，不能克隆——想要「只属于她」的声音，请换上面标了「可克隆」的商家（阿里云 / MiniMax / Fish Audio 都可以）。";
  }
}

function paintCdState(){
  var el = $("cdState"), txt = $("cdStateTxt");
  /* mock：真实为 slots.tts.state ∈ ok/warn/bad/none + state_text */
  var st = (S.engine === "cloud") ? {c:"ok", t:"TTS 引擎已连通（能力槽 tts · 阿里云 CosyVoice）——直接去克隆，拿编号回来填就行"}
                                  : {c:"", t:"还没配 TTS：下面任选一家填 Key 就好——和「连引擎」页写的是同一处（能力槽 tts）"};
  el.className = "cd-state " + st.c;
  txt.textContent = st.t;
}

function openCloudDetail(c){
  _cdChar = c;
  _cdVendor = null;
  $("cdTitle").textContent = c.name;
  paintCdState();
  renderCdGrid();
  $("cdClone").style.display = "none";
  $("cdNoClone").style.display = "none";
  $("cdMask").classList.add("on");
  var _vm = document.querySelector("#cdMask .vmodal"); if (_vm) _vm.scrollTop = 0;
  /* 【2026-10-19 修复·"打开看不到音色选择"】
     旧实现打开弹窗后**不选中任何商家**，cdClone 区（含 voice_id 输入框）一直
     隐藏 —— 用户必须先手动点一下卡片才展开，很容易以为"这里没有音色可选"。
     现在自动选中一家：优先"已配置 + 可克隆"（通常就是已绑好的阿里云），
     其次任意可克隆的。选中后立刻触发克隆区展示 + 自动拉取音色下拉。 */
  setTimeout(function(){
    try{
      var list = TTS_PROVIDERS || [];
      var pick = null;
      for (var i=0;i<list.length;i++){
        if (list[i].clone === "y" && list[i].configured){ pick = list[i]; break; }
      }
      if (!pick){
        for (var j=0;j<list.length;j++){
          if (list[j].clone === "y"){ pick = list[j]; break; }
        }
      }
      if (pick) selectCdVendor(pick.id);
    }catch(e){}
  }, 60);
}
$("cdClose").addEventListener("click", function(){ $("cdMask").classList.remove("on"); });
$("cdLater").addEventListener("click", function(){ $("cdMask").classList.remove("on"); });

/* ---------- 【2026-10-18 新增】自动拉取百炼克隆音色 → 下拉选择 ----------
   原先 voice_id 只能手动粘贴，用户要去控制台复制一长串 id，容易贴错。
   现在：选中阿里云 + 有 Key → 自动调 listBailianVoices，拉到就渲染下拉。
   下拉含「手动输入其它 voice_id…」逃生口；拉不到自动退回输入框。 */
function cdVendorKey(p){
  var card = p ? document.querySelector('.cdv[data-vid="'+p.id+'"]') : null;
  var k = card && card.querySelector(".cdv-key") ? card.querySelector(".cdv-key").value.trim() : "";
  if (k) return k;
  /* 已绑定 TTS（表单隐藏）时没有 Key 输入框：
     路径① S.slots（getCapabilitySlots 映射缓存，api_key 已随映射保留）
     路径② TTS_PROVIDERS（同一数据源）
     路径③ 后端 get_tts_provider_key（voice.clone.cloud 配置同源——最可靠，
            因为能力槽接口**故意**不回传 Key，只有这里能拿到真值） */
  try{
    var slots = S.slots || {};
    var tts = slots.tts || {};
    var provs = tts.providers || [];
    for (var i=0;i<provs.length;i++){
      if (provs[i] && provs[i].id === (p && p.id) && provs[i].api_key) return String(provs[i].api_key).trim();
    }
    if (tts.api_key) return String(tts.api_key).trim();
  }catch(e){}
  try{
    for (var j=0;j<(TTS_PROVIDERS||[]).length;j++){
      if (TTS_PROVIDERS[j] && TTS_PROVIDERS[j].id === (p && p.id) && TTS_PROVIDERS[j].api_key)
        return String(TTS_PROVIDERS[j].api_key).trim();
    }
  }catch(e){}
  return "";   /* ③ 是异步的，由 fetchCdVoices 直接调后端，不在这里走 */
}
var _cdVidFetching = false;
function fetchCdVoices(silent){
  var p = _cdVendor ? findProv(_cdVendor) : null;
  if (!p || p.id !== "dashscope_cosy") return;      /* 只有百炼有"列举已克隆音色"接口 */
  var B = _b();
  var sel = $("cdVidSelect"), inp = $("cdVid"), hint = $("cdVidHint");
  if (!B || !B.listBailianVoices){ return; }
  var key = cdVendorKey(p);
  /* 【2026-10-19 关键修复】本地路径拿不到 Key 时，直接问后端要
     （voice.clone.cloud 同源，与"测试语音"用的同一个 Key）。
     此前这里直接 return → 已绑定 TTS 的用户（绝大多数）下拉永远不出现。 */
  if (!key){
    if (B.getTtsProviderKey){
      if (!silent && hint) hint.textContent = "正在读取已绑定的百炼 Key…";
      Promise.resolve(B.getTtsProviderKey(p.id)).then(function(kr){
        var kk = kr && kr.success ? String(kr.api_key || "").trim() : "";
        if (kk){ fetchCdVoicesWithKey(p, kk, silent); }
        else if (hint) hint.textContent = (kr && kr.error || "尚未绑定百炼 Key")
          + "——请先在第 1 步「连引擎」绑定，或在上方填 Key。也可直接粘贴 voice_id。";
      }).catch(function(){
        if (hint) hint.textContent = "读取 Key 失败 · 可直接粘贴 voice_id";
      });
    } else if (hint){
      hint.textContent = "还没读到百炼 Key——请先在第 1 步「连引擎」绑定阿里云，或在上方填 Key。也可以直接粘贴 voice_id。";
    }
    return;
  }
  fetchCdVoicesWithKey(p, key, silent);
}

function fetchCdVoicesWithKey(p, key, silent){
  var B = _b();
  var sel = $("cdVidSelect"), inp = $("cdVid"), hint = $("cdVidHint");
  if (!B || !B.listBailianVoices || !key) return;
  if (_cdVidFetching) return;
  _cdVidFetching = true;
  if (!silent && hint) hint.textContent = "正在自动读取你账号下的克隆音色…";
  Promise.resolve(B.listBailianVoices(key)).then(function(r){
    _cdVidFetching = false;
    if (r && r.success){
      var vs = r.voices || [];
      if (!vs.length){
        if (hint) hint.textContent = (r.message || "该账号下没有克隆音色") + " · 可手动粘贴 voice_id";
        if (sel) sel.style.display = "none";
        if (inp) inp.style.display = "";
        return;
      }
      if (!sel) return;
      sel.innerHTML = vs.map(function(x){
        var label = (x.target_model || "模型未知") + (x.gmt_create ? (" · " + x.gmt_create) : "") + " · " + String(x.voice_id).slice(-8);
        return '<option value="' + String(x.voice_id).replace(/"/g,"&quot;") + '" data-m="' +
               String(x.target_model || "").replace(/"/g,"&quot;") + '">' + label + '</option>';
      }).join("") + '<option value="__manual__">手动输入其它 voice_id…</option>';
      sel.style.display = "";
      /* 初始只有下拉可见；若输入框已有值（编辑旧角色）则匹配下拉，
         匹配不上时两个都显示（下拉作导航、输入框保留原值可编辑） */
      var cur = inp && inp.value.trim();
      if (cur){
        var matched = false;
        [].forEach.call(sel.options, function(o){ if (o.value === cur) matched = true; });
        if (matched){ sel.value = cur; if (inp) inp.style.display = "none"; }
        else { sel.value = "__manual__"; if (inp) inp.style.display = ""; }
      } else {
        if (inp) inp.style.display = "none";
      }
      if (hint) hint.textContent = "已读取到 " + vs.length + " 个克隆音色，选一个即可（也可切回手动输入）";
      sel.onchange = function(){
        if (sel.value === "__manual__"){
          /* 【2026-10-19 修复·"选了手动输入就回不去"】
             旧实现把下拉隐藏掉 → 没有任何路回到选择器。
             现在两个控件**并存**：下拉留在原处（选中项停在"手动输入"上，
             随时可以改选其它音色），只把输入框显示出来聚焦。 */
          if (inp){ inp.style.display = ""; inp.focus(); }
          if (hint) hint.textContent = "手动输入模式：粘贴 voice_id 即可；改选上面的下拉可随时切回已克隆音色";
        } else {
          if (inp){ inp.value = sel.value; inp.style.display = "none"; }
          var op = sel.options[sel.selectedIndex];
          var m = op ? op.getAttribute("data-m") : "";
          /* 同步上方「模型」下拉：百炼要求合成模型与克隆模型一致，否则 400 */
          if (m){
            var card = document.querySelector('.cdv[data-vid="'+p.id+'"]');
            var msel = card && card.querySelector(".cdv-model");
            if (msel){
              var hit = false;
              [].forEach.call(msel.options, function(o){ if (o.value === m){ msel.value = m; hit = true; } });
              if (!hit){ try{ msel.value = m; }catch(e){} }
            }
          }
          if (hint) hint.textContent = "已选择：" + sel.value + (m ? ("（模型 " + m + "）") : "");
        }
      };
      /* 手动输入与下拉双向同步：输入框打字时，下拉切到"手动输入"项，
         保证保存逻辑（读 inp）与显示状态一致，也方便随时改选回下拉。 */
      if (inp) inp.addEventListener('input', function(){
        var hasManualOpt = false;
        [].forEach.call(sel.options, function(o){ if (o.value === '__manual__') hasManualOpt = true; });
        if (!hasManualOpt) return;
        sel.value = '__manual__';
      });
    } else {
      if (hint) hint.textContent = " " + ((r && r.error) || "读取失败") + " · 可手动粘贴 voice_id";
    }
  }).catch(function(){
    _cdVidFetching = false;
    if (hint) hint.textContent = " 读取失败（后端未连接）· 可手动粘贴 voice_id";
  });
}

/* 保存：先写能力槽（setCapabilitySlot），可克隆的再把 voice_id 落角色（saveCharacter） */
$("cdSave").addEventListener("click", function(){
  var p = _cdVendor ? findProv(_cdVendor) : null;
  if (!p){ toast("先选一家服务商"); return; }
  var bound = (S.engine === "cloud");
  var card = document.querySelector('.cdv[data-vid="'+p.id+'"]');
  /* 已绑定 TTS 时表单已隐藏：无 Key 输入框，取到空即走 configured 分支 */
  var key = card && card.querySelector(".cdv-key") ? card.querySelector(".cdv-key").value.trim() : "";
  if (bound && !p.configured){
    toast("这家还没绑定：回到第 1 步「连引擎」给它配 Key，再回来克隆");
    return;
  }
  if (!bound && !key){ toast("先填 API Key"); return; }
  var model = card && card.querySelector(".cdv-model")
    ? card.querySelector(".cdv-model").value
    : ((p.models && p.models[0]) ? ((typeof p.models[0] === "string") ? p.models[0] : p.models[0].id) : "");
  var group = card && card.querySelector(".cdv-group") ? card.querySelector(".cdv-group").value.trim() : "";

  /* 真实落地：写能力槽 tts 槽 —— 与「连引擎」页同一写入入口（_set_tts → merge_cloud_tts_provider） */
  var B = _b();
  var vm = $("cdVid") ? $("cdVid").value.trim() : "";
  var _finish = function(){
    p.configured = true;
    TTS_PROVIDERS.forEach(function(x){ x.active = (x.id === p.id); });
    S.engine = "cloud";
    renderCdGrid(); renderEngineBar();
    if (p.clone === "y"){
      /* 真实环境：把音色编号落进角色（save_character），她说的话才用得上这个音色 */
      if (_cdChar && v_ok && B && B.saveCharacter){
        var _upd = {id:_cdChar.id, name:_cdChar.name, cloud_voice_id: vm,
                    voice:{engine:p.id, voice_id:vm, model:model}};
        Promise.resolve(B.saveCharacter(_upd)).catch(function(){});
      }
      if (!vm){ toast("TTS 已写入。现在去「" + p.name + "」后台克隆，拿到 voice_id 填进来"); return; }
      if (_cdChar){
        _cdChar.voice = {kind:"cloud", vid:vm, engine:p.id};
        if(_cdChar.demo) _cdChar.demo = Object.assign({}, _cdChar.demo, {kind:"cloud", vid:vm});
        S.cloned[_cdChar.id] = true;
      }
      $("cdMask").classList.remove("on");
      syncPills();
      toast("已写入 TTS 槽 + 音色编号，试听一下");
      /* 快照角色：setTimeout 回调读全局 _cdChar 会被后续 openCloudDetail 覆盖（实测 bug） */
      var _pc = _cdChar;
      setTimeout(function(){
        var pc = _root ? _root.querySelector('.pcard[data-char-id="'+(_pc?_pc.id:"")+'"]') : null;
        if (pc){ var vb = pc.querySelector(".vbtn"); if(vb) tryVoice(_pc, vb); }
      }, 400);
    } else {
      $("cdMask").classList.remove("on");
      syncPills();
      toast("已写入 TTS 槽（" + p.name + " 只有通用合成音）");
    }
  };
  var v_ok = !!(_cdChar && _cdChar._raw);
  if (B && B.setCapabilitySlot){
    var _payload = {cap:"tts", provider:p.id, model:model};
    if (key) _payload.api_key = key;
    if (group) _payload.group_id = group;
    Promise.resolve(B.setCapabilitySlot(_payload)).then(function(r){
      if (r && r.success === false){ toast("写入失败：" + ((r && (r.error || r.message)) || "未知错误")); return; }
      _finish();
    }).catch(function(e){ toast("写入失败：" + e); });
    return;
  }
  _finish();
});


/* ---------- 本地克隆模型下载器（游戏启动器式） ---------- */
/* ── 适配器形状 mock：getVoiceVramAdvice().engines + _clone_engine_spec ──
   真相源 webapp._clone_engine_spec（5 个引擎）+ get_voice_vram_advice（体积/显存/判定）。
   注意 kind：local=真要下载；cloud=小米云端 API（要 Key，不占磁盘）；external=外部服务（要训练）。 */
var DL_ENGINES = [
  {id:"voxcpm", kind:"local", name:"VoxCPM2", repo:"openbmb/VoxCPM2",
   disk_gb:4.7, gpu_gb:6.0, device:"GPU", verdict:"ok", note:"中文零样本 · 速度音质均衡"},
  {id:"qwen3tts", kind:"local", name:"千问3-TTS", repo:"Qwen/Qwen3-TTS-12Hz-0.6B-Base",
   disk_gb:2.4, gpu_gb:0, device:"CPU", verdict:"ok", note:"默认走 CPU，不抢双工显存"},
  {id:"cosyvoice", kind:"local", name:"CosyVoice2-0.5B", repo:"iic/CosyVoice2-0.5B",
   disk_gb:5.4, gpu_gb:4.0, device:"GPU", verdict:"ok", note:"老牌引擎 · 多语言"},
  {id:"mimo_tts", kind:"cloud", name:"MiMo-TTS（小米云端克隆）", repo:"云端 API",
   disk_gb:0, gpu_gb:0, device:"—", verdict:"key", note:"限时免费 · 需填 mimo_api_key，不占本机磁盘"},
  {id:"gpt_sovits", kind:"external", name:"GPT-SoVITS", repo:"外部服务 http://127.0.0.1:9880",
   disk_gb:0, gpu_gb:0, device:"—", verdict:"train", note:"唯一要训练的路线 · 先装外部服务再微调"}
];
var VERDICT_TXT = {ok:"显存够", tight:"有点紧", fail:"显存不够", key:"需 Key", train:"要训练"};


function dlState(id){ return S.dl[id] || {st:"idle", pct:0, speed:""}; }
function renderDlList(){
  var box = $("dlList");
  box.innerHTML = "";
  DL_ENGINES.forEach(function(e){
    var st = dlState(e.id);
    var row = document.createElement("div");
    row.className = "dl-row" + (st.st==="installed" ? " inst" : "");
    row.setAttribute("data-eng", e.id);
    /*  chips：来源（下载/云端API/外部服务）+ 设备 + 显存判定（getVoiceVramAdvice） */
    var chips = '<span class="dl-chip k-'+(e.kind==="local"?"dl":(e.kind==="cloud"?"cloud":"ext"))+'">'+
      (e.kind==="local"?"本机下载":(e.kind==="cloud"?"云端 API":"外部服务"))+'</span>';
    if (e.kind === "local"){
      chips += '<span class="dl-chip">'+e.device+'</span>'+
        '<span class="dl-chip v-'+(st.st==="installed"?"ok":e.verdict)+'">'+
        (st.st==="installed" ? "已就绪" : (VERDICT_TXT[e.verdict]||""))+'</span>';
    } else if (e.kind === "cloud"){
      chips += '<span class="dl-chip v-key">需填 Key</span>';
    } else {
      chips += '<span class="dl-chip v-train">要训练</span>';
    }
    var act = "";
    if (e.kind === "cloud"){
      act = '<button class="btn sm dl-key" type="button">去填 Key</button>';
    } else if (e.kind === "external"){
      act = '<button class="btn sm dl-ext" type="button">去设置</button>';
    } else if(st.st==="idle"){
      act = '<button class="btn sm dl-go" type="button">下载</button>';
    }else if(st.st==="downloading"){
      act = '<button class="btn sm dl-pause" type="button">暂停</button><button class="btn sm dl-cancel" type="button">取消</button>';
    }else if(st.st==="paused"){
      act = '<button class="btn sm dl-go" type="button">继续</button><button class="btn sm dl-cancel" type="button">取消</button>';
    }else if(st.st==="installed"){
      act = '<button class="btn sm dl-del" type="button">卸载</button>';
    }
    var sizeTxt = e.kind === "local" ? (e.disk_gb.toFixed(1) + " GB") : "—";
    row.innerHTML =
      '<div class="dl-sel"><span class="radio'+((e.kind==="local"&&(st.st==="idle"||st.st==="paused"))?"":" dis")+'"></span></div>'+
      '<div class="dl-main">'+
        '<div class="dl-name">'+esc(e.name)+'</div>'+
        '<div class="dl-chips">'+chips+'</div>'+
        '<div class="dl-tag">'+esc(e.note)+' · <code>'+esc(e.repo)+'</code></div>'+
        (st.st==="downloading"||st.st==="paused" ? (
          '<div class="dl-prog"><div class="dl-progbar"><div class="dl-progfill" style="width:'+Math.round(st.pct)+'%"></div></div>'+
          '<div class="dl-progmeta"><span>'+(st.st==="paused"?"已暂停":"下载中")+" · "+Math.round(st.pct)+"% · "+sizeTxt+'</span><b>'+(st.speed||"")+'</b></div></div>'
        ) : "")+
      '</div>'+
      '<div class="dl-size">'+sizeTxt+'</div>'+
      '<div class="dl-act">'+act+'</div>'+
      (st.st==="installed" ? '<div class="dl-done"><svg viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg>已安装</div>' : "");
    var selEl = row.querySelector(".dl-sel");
    selEl.addEventListener("click", function(){
      if (e.kind !== "local"){ toast(e.kind==="cloud" ? "MiMo 是小米云端 API：不占磁盘，填 Key 就能用" : "GPT-SoVITS 是外部服务：先去 127.0.0.1:9880 起服务"); return; }
      if(st.st==="installed"){ toast("已安装的引擎可以直接用；想换就先卸载"); return; }
      [].forEach.call(document.querySelectorAll(".dl-row"), function(x){ x.classList.remove("pick"); });
      row.classList.add("pick");
      $("dlStartBtn").disabled = false;
      $("dlStartBtn").textContent = (st.st==="paused" ? "继续下载" : "下载") + " " + e.name;
      $("dlInfo").textContent = "已选 " + e.name + "（" + e.disk_gb.toFixed(1) + " GB · " + e.device + "）";
    });
    var go = row.querySelector(".dl-go");
    if(go) go.addEventListener("click", function(ev){ ev.stopPropagation(); startDl(e.id); });
    var pa = row.querySelector(".dl-pause");
    if(pa) pa.addEventListener("click", function(ev){ ev.stopPropagation(); pauseDl(e.id); });
    var ca = row.querySelector(".dl-cancel");
    if(ca) ca.addEventListener("click", function(ev){ ev.stopPropagation(); cancelDl(e.id); });
    var de = row.querySelector(".dl-del");
    if(de) de.addEventListener("click", function(ev){ ev.stopPropagation(); uninstallDl(e.id); });
    var kb = row.querySelector(".dl-key");
    if(kb) kb.addEventListener("click", function(ev){ ev.stopPropagation(); toast("真实环境：弹小米 API Key 输入（mimo_api_key），限时免费"); });
    var eb = row.querySelector(".dl-ext");
    if(eb) eb.addEventListener("click", function(ev){ ev.stopPropagation(); toast("真实环境：引导安装 GPT-SoVITS 外部服务（127.0.0.1:9880），这条路要训练"); });
    box.appendChild(row);
  });
  /* 安装磁盘条：占用只累本机下载型 */
  var used = 0;
  DL_ENGINES.forEach(function(e){ if(e.kind==="local" && dlState(e.id).st==="installed") used += e.disk_gb; });
  var pct = Math.min(100, 12 + used*1.4);
  $("dlFreeBar").style.width = pct + "%";
  $("dlFree").textContent = (118.4 - used).toFixed(1) + " GB";
}

/* 电脑里已有模型：指认目录（真实=scanTtsModelDir 扫描 + setTtsModelDir 写入） */
$("dlHaveBtn").addEventListener("click", function(){
  var B = _b();
  if(!(B && B.pickModelDir && B.scanTtsModelDir && B.setTtsModelDir)){
    toast("预览模式：软件运行时弹出目录选择框，指认已下过的本地语音模型");
    var e0 = null;
    for (var i0=0;i0<DL_ENGINES.length;i0++){
      var x0 = DL_ENGINES[i0];
      if (x0.kind==="local" && dlState(x0.id).st!=="installed"){ e0 = x0; break; }
    }
    if (!e0){ toast("本机引擎都已就绪，不用再认领"); return; }
    S.dl[e0.id] = {st:"installed", pct:100, speed:""};
    renderDlList();
    return;
  }
  Promise.resolve(B.pickModelDir()).then(function(d){
    var dir = d && (d.path || d.dir);
    if(!dir) return;
    return Promise.resolve(B.scanTtsModelDir({dir:dir})).then(function(sr){
      var hit = (sr && sr.engines && sr.engines[0]) || null;
      if(!hit){ toast("这个目录里没找到可认领的本地语音模型"); return; }
      return Promise.resolve(B.setTtsModelDir({engine:hit.engine, dir:(hit.dir || dir)})).then(function(w){
        if(!(w && w.success)){ toast("认领失败：" + ((w && w.error) || "未知错误")); return; }
        S.dl[hit.engine] = {st:"installed", pct:100, speed:""};
        S.cloneModel = true;
        renderDlList();
        toast("已认领：" + (hit.label || hit.engine) + " 就绪");
      });
    });
  }).catch(function(e){ toast("认领失败：" + e); });
});

function openDlView(){
  $("viewPick").style.display = "none";
  $("viewNew").style.display = "none";
  $("viewDl").style.display = "";
  renderDlList();
}
$("dlBack").addEventListener("click", function(){
  $("viewDl").style.display = "none";
  $("viewPick").style.display = "";
  if(S.cloneModel && _rtChar){ openCloneCard(_rtChar); }
});
var _dlTimers = {};
/* 真实下载轮询：get_voice_download_status(task_id) → {status:running|done|manual|error, log} */
function pollDl(id, tid, n){
  if(n > 900) return;
  var B = _b();
  Promise.resolve(B.getVoiceDownloadStatus(tid)).then(function(r){
    var st = dlState(id);
    var status = (r && r.status) || "";
    var log = String((r && r.log) || "");
    if(status === "done"){
      st.st = "installed"; st.pct = 100; st.speed = ""; S.cloneModel = true;
      renderDlList();
      toast(((DL_ENGINES.filter(function(x){ return x.id===id; })[0] || {}).name || id) + " 下载完成，可以开始克隆了");
      return;
    }
    if(status === "error"){ st.st = "error"; st.speed = ""; renderDlList(); toast("下载失败：" + log.slice(-80)); return; }
    if(status === "manual"){ st.st = "manual"; st.speed = ""; renderDlList(); toast("自动下载不可用，可按提示手动下载"); return; }
    var m = /(\d{1,3}(?:\.\d+)?)\s*%/.exec(log);
    st.st = "downloading";
    st.pct = m ? Math.min(99, parseFloat(m[1])) : (st.pct || 1);
    st.speed = "";
    st.log = log.slice(-200);
    S.dl[id] = st;
    renderDlList();
    setTimeout(function(){ pollDl(id, tid, n + 1); }, 1000);
  }).catch(function(){ setTimeout(function(){ pollDl(id, tid, n + 1); }, 1500); });
}

function startDl(id){
  var e = DL_ENGINES.filter(function(x){ return x.id===id; })[0];
  if(!e) return;
  var B = _b();
  var st = dlState(id);
  /* 真实环境：download_voice_model 起后台任务 → 轮询真实进度（不画假进度） */
  if(B && B.downloadVoiceModel){
    st.st = "downloading"; st.pct = st.pct || 0; st.speed = "";
    S.dl[id] = st;
    renderDlList();
    Promise.resolve(B.downloadVoiceModel({engine:id})).then(function(r){
      if(r && r.already){ st.st = "installed"; st.pct = 100; S.cloneModel = true; renderDlList(); toast(e.name + " 已就绪，无需下载"); return; }
      var tid = r && r.task_id;
      if(!tid){ st.st = "error"; renderDlList(); toast("下载启动失败：" + ((r && r.error) || "无返回")); return; }
      pollDl(id, tid, 0);
    }).catch(function(err){ st.st = "error"; renderDlList(); toast("下载失败：" + err); });
    return;
  }
  /* 无桥降级：稿内模拟进度 */
  st.st = "downloading"; st.pct = st.pct || 0; st.speed = "8.2 MB/s";
  S.dl[id] = st;
  renderDlList();
  clearInterval(_dlTimers[id]);
  _dlTimers[id] = setInterval(function(){
    st.pct = Math.min(100, st.pct + (3 + Math.random()*7));
    st.speed = (6 + Math.random()*5).toFixed(1) + " MB/s";
    if(st.pct >= 100){
      clearInterval(_dlTimers[id]);
      st.st = "installed"; st.speed = "";
      S.cloneModel = true;
      renderDlList();
      toast(e.name + " 下载完成，可以开始克隆了");
      return;
    }
    var row = document.querySelector('.dl-row[data-eng="'+id+'"]');
    if(row){
      var fill = row.querySelector(".dl-progfill");
      var meta = row.querySelector(".dl-progmeta span");
      if(fill) fill.style.width = Math.round(st.pct) + "%";
      if(meta) meta.textContent = "下载中 · " + Math.round(st.pct) + "% · " + e.disk_gb.toFixed(1) + " GB";
    }
  }, 240);
}
function pauseDl(id){
  var st = dlState(id);
  clearInterval(_dlTimers[id]);
  st.st = "paused";
  renderDlList();
  toast("已暂停：随时可以继续");
}
function cancelDl(id){
  clearInterval(_dlTimers[id]);
  delete S.dl[id];
  renderDlList();
  toast("已取消，临时文件已清理");
}
function uninstallDl(id){
  delete S.dl[id];
  renderDlList();
  toast("已卸载，空间已释放");
}
$("dlStartBtn").addEventListener("click", function(){
  var row = document.querySelector(".dl-row.pick");
  if(!row){ toast("先选一个引擎"); return; }
  startDl(row.getAttribute("data-eng"));
});

/* ---------- 克隆卡：没有参考音频时给「选择音频文件」 ---------- */
$("clPickAudio").addEventListener("click", function(){
  var c = _clChar;
  if(!c) return;
  var B = _b();
  /* 无桥：保留稿内演示 */
  if(!(B && B.pickAudioFile)){
    toast("预览模式：软件运行时弹出系统文件框，选一段 10 秒左右的清晰人声");
    c.voice = c.voice || {};
    c.voice.ref = {name:"示例录音.wav", path:"", url:"", secs:12, text:""};
    openCloneCard(c);
    return;
  }
  Promise.resolve(B.pickAudioFile()).then(function(r){
    if(!(r && r.success && r.path)){
      if(r && r.error) toast("选音频失败：" + r.error);
      return;
    }
    var fn = r.path.split(/[\\/]/).pop() || "参考音频";
    c.voice = c.voice || {};
    c.voice.ref = {name:fn, path:r.path, url:"", secs:Math.round(r.duration || 0), text:""};
    openCloneCard(c);
    /* 落进角色：后端 set_reference_audio（写 voice.reference_audio + 同步 proactive） */
    if(B.setReferenceAudio){
      var txt = ($("clRefText") && $("clRefText").value.trim()) || "";
      Promise.resolve(B.setReferenceAudio({path:r.path, character_id:c.id, text:txt})).catch(function(){});
    }
    toast("已选：" + fn + (c.voice.ref.secs ? ("（" + c.voice.ref.secs + "s）") : ""));
  }).catch(function(e){ toast("选音频失败：" + e); });
});
/* ================= 真实数据接入（引导页场景；无桥时保留稿内 mock） ================= */
function _b(){ return getB(); }

/* 内置兜底头像表（web 根静态资源 _role_assets/*，来自原型稿）：
   角色在 config 里没配 avatar（实测达妮娅就没配）时兜一层，
   避免整张卡退回灰色人形占位（用户实测「达妮娅头像异常」）。
   注意：这些是 web 静态路径，★不能★塞进 /local_img?path=（那需要真实文件路径）。 */
var BUNDLED_AVATAR = (function(){
  var m = {};
  (typeof PRESETS !== 'undefined' && PRESETS ? PRESETS : []).forEach(function(c){
    if(c && c.avatar) m[c.id] = c.avatar;
  });
  return m;
})();
function avatarUrlOf(c){
  /* 与软件内角色卡同款解析链：角色 avatar > 3D 缩略图；都没有则用内置兜底图；再没有才留空走人形占位 */
  if(!c) return '';
  var p = c.avatar || (c.model_3d && c.model_3d.thumbnail) || '';
  if(p) return '/local_img?path=' + encodeURIComponent(p);
  return BUNDLED_AVATAR[c.id] || '';
}

function voiceKindOf(c){
  /* 真实声音分类：local=本地克隆 / cloud=云端音色 / builtin=内置合成音 / none=未配置 */
  var v = c.voice || {};
  var eng = String(v.engine || '').toLowerCase();
  if(/^(voxcpm|qwen3tts|cosyvoice|gpt-sovits|gpt_sovits)$/.test(eng)) return {kind:'local', engine:eng};
  if((c.cloud_voice_id || '').trim()) return {kind:'cloud', vid:String(c.cloud_voice_id).trim(), engine:eng};
  if(/^(dashscope|minimax|fish|mimo|openai_tts)/.test(eng)) return {kind:'cloud', vid:'', engine:eng};
  if(eng === 'edge-tts' || v.voice) return {kind:'builtin', engine:eng || 'edge-tts'};
  return {kind:'none', engine:eng};
}

function loadFromBackend(){
  var B = _b();
  if(!B) return;                       /* 设计稿/独立预览：保留稿内数据 */
  /* ① 角色网格：config.yaml 真实 characters（过滤 *_test 副本） */
  if(B.getCharacters){
    Promise.resolve(B.getCharacters()).then(function(r){
      var list = (r && r.characters) || [];
      var keep = list.filter(function(c){ return c && c.id && !/_test$/.test(c.id); });
      if(!keep.length) return;
      PRESETS = keep.map(function(c){
        return {
          id: c.id, name: c.name || c.id,
          c1: c.c1 || '#2DD4FF', c2: c.c2 || '#A06BFF',
          avatar: avatarUrlOf(c),
          tag: String(c.speaking_style || '').slice(0, 24) || '（还没写说话风格）',
          desc: String(c.character || '').slice(0, 70) || '（还没写性格特征）',
          voice: voiceKindOf(c),
          _raw: c
        };
      });
      if(!findChar(S.picked)) S.picked = PRESETS[0].id;
      renderGrid(); syncVoiceAct();
    }).catch(function(){});
  }
  /* ② TTS 厂商 + 引擎状态：能力槽 tts 槽（与「连引擎」同一真相源） */
  if(B.getCapabilitySlots){
    Promise.resolve(B.getCapabilitySlots()).then(function(r){
      var tts = (r && r.slots && r.slots.tts) || null;
      if(!tts) return;
      if(tts.providers && tts.providers.length){
        TTS_PROVIDERS = tts.providers.map(function(p){
          return {
            id: p.id, name: p.name || p.id, logo: p.logo || '',
            rank: p.rank || 99, clone: p.clone || 'n',
            base_url: p.base_url || '',
            models: (p.models || []).map(function(m){ return (typeof m === 'string') ? {id:m} : m; }),
            needs_group: !!p.needs_group, hint: p.hint || '',
            configured: !!p.configured, active: !!p.active,
            /* 【2026-10-19 关键修复·"已绑定时自动拉取音色失效"的根因】
               cdVendorKey() 从 S.slots.tts.providers 读 Key，但此前映射时
               **丢掉了 api_key 字段** → S.slots 又从未赋值 → 已绑定 TTS 的
               用户（引导页第 1 步绑过百炼的，正是绝大多数人）走到克隆弹窗时
               拿不到 Key → fetchCdVoices 静默返回 → 下拉永远不出现，
               用户只能手填 voice_id。
               现在把 Key 一并映射进 TTS_PROVIDERS，并同步存到 S.slots，
               cdVendorKey 两条读取路径都通了。 */
            api_key: p.api_key || ''
          };
        });
        /* 同步缓存到 S.slots，供 cdVendorKey 在"已绑定、无 Key 输入框"时读取 */
        S.slots = { tts: { providers: TTS_PROVIDERS, api_key: (tts.api_key || '') } };
      }
      var st = String(tts.state || 'none');
      S.engine = (st === 'ok') ? 'cloud' : (st === 'warn' ? 'local' : 'none');
      renderEngineBar(); syncPills(); syncVoiceAct();
      if($('cdMask') && $('cdMask').classList.contains('on')) renderCdGrid();
    }).catch(function(){});
  }
  /* ③ 克隆引擎清单：list_clone_engines（适配器 5 引擎 + 已装检测） */
  if(B.listCloneEngines){
    Promise.resolve(B.listCloneEngines()).then(function(r){
      var es = (r && r.engines) || [];
      if(!es.length) return;
      DL_ENGINES = es.map(function(e){
        return {
          id: e.id, kind: e.kind, name: e.label || e.id, repo: e.repo || '',
          disk_gb: e.disk_gb || 0, gpu_gb: e.gpu_gb || 0,
          device: e.device || '—',
          verdict: e.verdict || (e.kind === 'cloud' ? 'key' : (e.kind === 'external' ? 'train' : 'ok')),
          note: e.note || '', installed: !!e.installed
        };
      });
      DL_ENGINES.forEach(function(e){
        if(e.installed){ S.dl[e.id] = {st:'installed', pct:100, speed:''}; if(e.kind === 'local') S.cloneModel = true; }
      });
      if($('viewDl') && $('viewDl').style.display !== 'none') renderDlList();
      if($('cloneMask') && $('cloneMask').classList.contains('on')) renderClEngines();
    }).catch(function(){});
  }
}

/* ================= 启动 ================= */
renderEngineBar();
renderGrid();
syncVoiceAct();
loadFromBackend();

/* 对外 API：引导页 doFinish 读当前选择；reload 供再次进入时刷新真实数据 */
window.OnboardingRole.getSelection = function(){
  var c = findChar(S.picked);
  return {id: S.picked, name: (c && c.name) || '', cloned: !!S.cloned[S.picked]};
};
window.OnboardingRole.reload = function(){ try { loadFromBackend(); } catch(e){} };

  }

  function mountInto(el) {
    if (!el || _mounted) return;
    _mounted = true;
    injectCss();
    el.innerHTML = '';
    var root = document.createElement('div');
    root.id = 'onbRoleRoot';       /* CSS 作用域根：所有选择器都以 #onbRoleRoot 前缀限定 */
    el.appendChild(root);
    _root = root;
    root.innerHTML = HTML;
    ensurePortal();               /* ★ 弹层/Toast 出滑轨：真实引导页 transform 链会让 fixed 失真 */
    mainLogic();
  }

  function open() {
    var host = document.getElementById('onbRoleOverlay');
    if (!host) {
      host = document.createElement('div');
      host.id = 'onbRoleOverlay';
      host.style.cssText = 'position:fixed;inset:0;z-index:9999;overflow:auto;background:var(--bg,#0A0C12)';
      document.body.appendChild(host);
    }
    mountInto(host);
    var el = document.getElementById('onbRoleRoot');
    if (el) el.style.minHeight = '100%';
    /* ★ portal 跟着宿主走：独立预览模式下弹层若留在 body（z:110）会被
       覆盖层（z:9999）压住。宿主是 fixed 且无 transform，portal 挂进去 fixed 仍按视口算。 */
    var portal = document.getElementById('onbRolePortal');
    if (portal && portal.parentElement !== host) host.appendChild(portal);
    return el;
  }

  window.OnboardingRole = { mountInto: mountInto, open: open, getBackend: getB, _debugRoot: function () { return _root; } };
})();
