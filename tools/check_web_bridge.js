/* 在最小沙箱里真实执行 app_backend.js：
   - 若执行抛错 → window.Backend 根本不会被赋值（页面会报"后端未暴露 xxx"）
   - 若成功 → 打印真实导出的键，并与 index.html 实际调用的方法求差集
   这能一次性区分「源码缺失」与「加载/执行失败」。 */
const fs = require('fs');
const vm = require('vm');

const ROOT = 'G:/ai对话/11';
const BRIDGE = ROOT + '/src/ui/web/redesign/app_backend.js';

const code = fs.readFileSync(BRIDGE, 'utf8');

function mkEl() {
  return {
    style: {}, dataset: {}, classList: { add(){}, remove(){}, toggle(){}, contains(){ return false; } },
    appendChild(){}, removeChild(){}, insertBefore(){}, remove(){},
    setAttribute(){}, getAttribute(){ return null; }, hasAttribute(){ return false; },
    addEventListener(){}, removeEventListener(){}, dispatchEvent(){ return true; },
    querySelector(){ return null; }, querySelectorAll(){ return []; },
    focus(){}, blur(){}, click(){}, cloneNode(){ return mkEl(); },
    hidden: false, textContent: '', innerHTML: '', value: '', title: '', id: '',
    getBoundingClientRect(){ return { top:0,left:0,width:0,height:0 }; },
    children: [], childNodes: [], parentNode: null, firstChild: null, nextSibling: null,
  };
}

const sandbox = {
  console,
  setTimeout, clearTimeout, setInterval, clearInterval, queueMicrotask,
  requestAnimationFrame: (cb) => setTimeout(cb, 0),
  cancelAnimationFrame() {},
  document: {
    addEventListener(){}, removeEventListener(){},
    getElementById(){ return null; }, querySelector(){ return null; }, querySelectorAll(){ return []; },
    createElement(){ return mkEl(); }, createDocumentFragment(){ return mkEl(); },
    documentElement: mkEl(), head: mkEl(), body: mkEl(),
    readyState: 'complete', hidden: false, title: '',
    dispatchEvent(){ return true; },
  },
  navigator: { userAgent: 'node', language: 'zh-CN', platform: 'Win32' },
  location: { href: 'http://127.0.0.1:8766/index.html', search: '', hash: '', pathname: '/index.html' },
  history: { pushState(){}, replaceState(){} },
  localStorage: { getItem(){ return null; }, setItem(){}, removeItem(){}, clear(){} },
  sessionStorage: { getItem(){ return null; }, setItem(){}, removeItem(){}, clear(){} },
  matchMedia: () => ({ matches:false, addEventListener(){}, removeEventListener(){}, addListener(){}, removeListener(){} }),
  getComputedStyle: () => mkEl().style,
  fetch: () => Promise.reject(new Error('no fetch in sandbox')),
  XMLHttpRequest: function () { this.open = () => {}; this.send = () => {}; },
  MutationObserver: function () { this.observe = () => {}; this.disconnect = () => {}; },
  CustomEvent: function () {}, Event: function () {},
  alert(){}, confirm(){ return false; }, prompt(){ return null; },
  Image: function () { return mkEl(); },
  Blob: function () {}, URL: { createObjectURL(){ return ''; }, revokeObjectURL(){} },
};
sandbox.window = sandbox;
sandbox.self = sandbox;
sandbox.globalThis = sandbox;

const ctx = vm.createContext(sandbox);
let err = null;
try { vm.runInContext(code, ctx, { filename: 'app_backend.js' }); }
catch (e) { err = e; }

console.log('='.repeat(68));
console.log('app_backend.js 执行结果');
console.log('='.repeat(68));
console.log('是否抛异常 :', err ? (err.name + ': ' + err.message) : '否（执行完整）');
if (err && err.stack) {
  const first = err.stack.split('\n').slice(0, 4).join('\n   ');
  console.log('位置        :\n   ' + first);
}

const B = ctx.window && ctx.window.Backend;
console.log('window.Backend 存在 :', !!B);

if (B) {
  const keys = Object.keys(B).sort();
  console.log('导出方法数 :', keys.length);
  console.log('含 startDuplexCall :', keys.indexOf('startDuplexCall') >= 0);
  console.log('含 logCallAv       :', keys.indexOf('logCallAv') >= 0);
  console.log('含 getTierCallStatus:', keys.indexOf('getTierCallStatus') >= 0);

  // index.html 内联脚本真实调用到的方法
  const html = fs.readFileSync(ROOT + '/src/ui/web/index.html', 'utf8');
  const used = new Set();
  const re = /\bBackend\s*\.\s*([A-Za-z_$][A-Za-z0-9_$]*)/g;
  let m;
  while ((m = re.exec(html)) !== null) used.add(m[1]);
  console.log('\nindex.html 调用到的方法数 :', used.size);
  const missing = [...used].filter(k => keys.indexOf(k) < 0).sort();
  console.log('桥里缺失的方法 :', missing.length ? missing.join(', ') : '无');
} else {
  console.log('\n!! window.Backend 未赋值 —— 页面里所有 Backend.xxx 调用都会走"后端未暴露"分支。');
}

// 顺便报告 index.html 资源引用的版本号（核对版本错配）
console.log('\nindex.html 资源引用版本号:');
for (const line of fs.readFileSync(ROOT + '/src/ui/web/index.html', 'utf8').split('\n')) {
  if (/<script[^>]*src=|<link[^>]*href=/.test(line)) {
    const t = line.trim();
    if (t.length < 220) console.log('   ' + t);
  }
}
