import { createMorph } from './morphicons/dom.js';

/* 状态切换图标数据（24×24 stroke，morphicons 兼容任意两点数路径） */
export const ICONS = {
  mic: "M12 3a3 3 0 0 1 3 3v5l3 3H6l3-3V6a3 3 0 0 1 3-3z M10 19a2 2 0 0 0 4 0",
  micOff: "M12 3a3 3 0 0 1 3 3v5l3 3H6l3-3V6a3 3 0 0 1 3-3z M4 4l16 16",
  eye: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
  eyeOff: "M3 3l18 18 M10.6 5.1A10.9 10.9 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3.3 3.9 M6.6 6.6A17 17 0 0 0 2 12s3.5 7 10 7a10.8 10 0 0 0 4.3-.9 M9.9 9.9a3 3 0 0 0 4.2 4.2",
  download: "M12 3v12 M7 11l5 5 5-5 M5 21h14",
  check: "M5 13l4 4L19 7",
  chevronRight: "M9 6l6 6-6 6",
  chevronLeft: "M15 6l-6 6 6 6",
  chevronDown: "M6 9l6 6 6-6",
  play: "M7 4l13 8-13 8z",
  pause: "M8 5v14 M16 5v14",
  volume: "M4 9v6h4l5 4V5L8 9z M16 9a4 4 0 0 1 0 6 M18.5 7a7 7 0 0 1 0 10",
  volumeOff: "M4 9v6h4l5 4V5L8 9z M22 9l-6 6 M16 9l6 6",
  /* 语音·音色（扬声器，2 子路径；active=声波 / mute=斜杠，morphicons 可形变） */
  sound: "M4 9h4l5-4v14l-5-4H4z M14 9.5c1.8 1.5 1.8 3.5 0 5",
  soundOff: "M4 9h4l5-4v14l-5-4H4z M13 7.5l7 9",
  /* 双工（纯文本互动） */
  type: "M5 6h14 M5 11h10 M5 16h12",
  typeOff: "M5 6h14 M5 11h10 M5 16h12 M3 3l18 18",
  /* 通用小圆点（用于未激活状态占位） */
  dot: "M12 12m-1.1 0a1.1 1.1 0 1 0 2.2 0a1.1 1.1 0 1 0 -2.2 0",
  /* 对话页头部按钮（morphicons 单路径形变，on/off 切换动一下） */
  cont: "M4 5h16v10H9.5L5 19V15H4z",
  contOff: "M4 5h16v10H9.5L5 19V15H4z M4 4l16 16",
  trash: "M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13",
  expand: "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5",
  collapse: "M9 4H4v5M15 4h5v5M9 20H4v-5M15 20h5v-5"
};

/* 侧栏导航图标（单一连续路径，morphicons 可形变；旋转由 gsap 处理） */
const NAV_ICONS = {
  'index.html': 'M4 5h16v10H9l-4 4V5z',
  'ui_redesign_daily.html': 'M6 3h9l3 3v15H6z',
  'ui_redesign_characters.html': 'M12 4c-2.2 0-4 1.8-4 4 0 1.5.8 2.8 2 3.4C8.5 12.4 6 14.5 6 18h12c0-3.5-2.5-5.6-4-6.6 1.2-.6 2-1.9 2-3.4 0-2.2-1.8-4-4-4z',
  'ui_redesign_settings.html': 'M14 4a4 4 0 0 0-5 5l-6 6 2 2 6-6a4 4 0 0 0 5-5l-2 2-2-2 2-2z',
  'ui_redesign_developer.html': 'M6 5h12v14H6z',
  'ui_redesign_memory.html': 'M5 4h12a2 2 0 0 1 2 2v14H7a2 2 0 0 1-2-2z',
  'kb.html': 'M5 4h11a2 2 0 0 1 2 2v14H7a2 2 0 0 1-2-2z M4 9h11 M4 13h11'
};
function navIconFor(href){
  const key = (href || '').split('/').pop();
  return NAV_ICONS[key] || 'M5 5h14v14H5z';
}
const NAV_DOT = 'M10.5 12h3';

window.Morph = {
  createMorph,
  ICONS,
  make(pathEl, name, spring){
    if (!pathEl) return null;
    const m = createMorph(pathEl, ICONS[name] || name, { reducedMotion: 'user' });
    pathEl._morph = m;
    return m;
  },
  to(pathEl, name, spring){
    if (!pathEl) return;
    const target = ICONS[name] || name;
    try {
      if (!pathEl._morph) pathEl._morph = createMorph(pathEl, target, { reducedMotion: 'user' });
      pathEl._morph.morphTo(target, spring);
    } catch (err) {
      /* 兜底：形变库异常时直接落到目标路径，保证图标可见 */
      console.warn('[morph] 形变失败，回退静态路径：', err);
      try { pathEl.setAttribute('d', target); } catch (_) {}
    }
  },
  /* 侧栏导航动效（reveal-on-active）：
     默认只显示文字、不显示图标；进入对应页面时，该栏目图标结合 morphicons 形变 + 弹簧入场显示出来。
     语音导航改为「扬声器」图标（更贴合语音·音色，自然朝向，不再 180° 倒置）。 */
  setupNav(){
    if (typeof gsap === 'undefined'){ console.warn('[morph] gsap 未加载，导航动效跳过'); return; }
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const navs = Array.from(document.querySelectorAll('.side .nav'));
    const active = document.querySelector('.side .nav.on') || navs[0];
    navs.forEach((nav, i) => {
      const href = nav.getAttribute('href') || '';
      const label = nav.textContent.trim();
      const d = navIconFor(href);
      /* 重建 svg：单一路径 + id，默认隐藏（opacity 0），仅当前栏目显示 */
      nav.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path id="navPath-' + i + '" d="' + NAV_DOT + '"></path></svg>' + label;
      const svg = nav.querySelector('svg');
      const isSettings = href.includes('setting');
      const rot = isSettings ? 45 : 0;   /* 设置 45°，其余 0（语音用扬声器，自然朝向） */
      gsap.set(svg, { transformOrigin: '50% 50%', rotation: rot, opacity: 0, scale: 0.4 });
      if (reduce) return;
      /* 整条导航（文字）从左侧弹力滑入；图标保持隐藏，等进入该页再显现 */
      gsap.from(nav, { opacity: 0, x: -14, duration: .5, delay: .05 + i * .05, ease: 'power3.out' });
      /* 仅当前栏目的图标（hover 时弹簧缩放） */
      nav.addEventListener('mouseenter', () => { if (nav.classList.contains('on')) gsap.to(svg, { scale: 1.18, duration: .35, ease: 'back.out(2.2)' }); });
      nav.addEventListener('mouseleave', () => { if (nav.classList.contains('on')) gsap.to(svg, { scale: 1, duration: .3, ease: 'power2.out' }); });
    });
    /* 只显示当前栏目的图标（结合 morph 形变 + 弹簧入场） */
    if (active) window.Morph.revealNav(active);
  },
  /* 显现某栏目图标：dot → 真实图标 morph，配合弹簧放大 */
  revealNav(nav){
    if (typeof gsap === 'undefined' || !nav) return;
    const svg = nav.querySelector('svg');
    const path = nav.querySelector('path');
    if (!svg) return;
    const d = navIconFor(nav.getAttribute('href'));
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduce){ gsap.set(svg, { opacity: 1, scale: 1 }); if (path) path.setAttribute('d', d); return; }
    if (path) window.Morph.to(path, NAV_DOT);
    gsap.to(svg, { opacity: 1, scale: 1, duration: .5, ease: 'back.out(2.4)' });
    if (path) setTimeout(() => window.Morph.to(path, d), 150);
  },
  /* 隐藏某栏目图标 */
  hideNav(nav){
    if (typeof gsap === 'undefined' || !nav) return;
    const svg = nav.querySelector('svg');
    if (svg) gsap.to(svg, { opacity: 0, scale: 0.4, duration: .3, ease: 'power2.out' });
  },
  /* 切栏时：隐藏其余栏目图标，仅当前栏目图标脉冲显示 */
  pulseNav(nav){
    if (!nav) return;
    document.querySelectorAll('.side .nav').forEach(n => { if (n !== nav) window.Morph.hideNav(n); });
    window.Morph.revealNav(nav);
  },
  ready(cb){
    if (document.readyState !== 'loading') cb();
    else document.addEventListener('DOMContentLoaded', cb);
  }
};

/* ============================================================
   侧栏导航图标初始化（setupNav）由 window.load 触发。
   SPA 路由已独立到 spa.js（普通 <script>，不依赖本模块加载成败）：
   即使 morphicons 模块加载失败，导航与「左/中/右」常驻布局仍正常。
   ============================================================ */
window.addEventListener('load', () => {
  if (window.Morph) window.Morph.setupNav();
});
