/* 设置页「关于」分类（版本号 + 开机自启）的行为真值表。
 *
 * 为什么要有它：这两个开关的判据全是"点了以后界面变成什么样"，静态查源码只能证明
 * "字符串在不在"，证明不了"在浏览器里生效"。本仓的规矩是**把真值表交给 node 跑页面里
 * 的真 JS**（不是照着逻辑再写一遍 Python —— 那只证明"我两次写得一样"）。
 *
 * 做法：从 `ui_redesign_settings.html` 里**原样抽出** `aboutTier` 那个 IIFE，
 * 在一个假 DOM + 假 Backend 的沙箱里跑，然后断言 DOM 变化与后端调用次数。
 * 所以页面里的代码改了，这里立刻跟着变；不会出现"测试里另有一份实现"。
 *
 * 覆盖 5 个场景（含两个"不许假装成功"的对照组）：
 *   S1 支持且已开 → 开关亮、reason 空
 *   S2 不支持（开发环境）→ 置灰 + 说清原因；**点了不许调后端**（对照组）
 *   S3 后端没连上（三态里的"问不出来"）→ 报读取失败，**不许说成"开发环境"**（对照组）
 *   S4 支持且关着 → 点击调 set_autostart(true)，成功则保持亮
 *   S5 写入失败 → 开关翻回去 + 红字提示（不谎报成功）
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const PAGE = path.join(ROOT, 'src', 'ui', 'web', 'redesign', 'ui_redesign_settings.html');

let ok = 0, bad = 0;
function chk(name, cond, extra) {
  if (cond) { ok++; console.log('  PASS ' + name + (extra ? '  ' + extra : '')); }
  else { bad++; console.log('  FAIL ' + name + (extra ? '  ' + extra : '')); }
}

/* ---------- 从真实页面里抽 IIFE ---------- */
const html = fs.readFileSync(PAGE, 'utf8');
const START = '(function aboutTier(){';
const END = '\n  })();';
const i = html.indexOf(START);
if (i < 0) { console.log('  FAIL 页面里找不到 aboutTier IIFE（被改名/删掉了？）'); process.exit(1); }
const j = html.indexOf(END, i);
if (j < 0) { console.log('  FAIL 找不到 aboutTier 的结尾'); process.exit(1); }
const SRC = html.slice(i, j + END.length);

/* ---------- 假 DOM ---------- */
function mkEl(id) {
  const cls = new Set();
  const el = {
    id, dataset: {}, textContent: '', _on: null, attrs: {},
    classList: {
      add: (c) => cls.add(c),
      remove: (c) => cls.delete(c),
      contains: (c) => cls.has(c),
      toggle: (c, on) => {
        const v = (on === undefined) ? !cls.has(c) : !!on;
        if (v) cls.add(c); else cls.delete(c);
        return v;
      }
    },
    setAttribute(k, v) { el.attrs[k] = v; },
    getAttribute(k) { return el.attrs[k]; },
    addEventListener(ev, fn) { el._on = fn; },
    click() { if (el._on) el._on(); }
  };
  return el;
}

function runScenario(opts) {
  const ver = mkEl('aboutVersion');
  const sw = mkEl('autostartSwitch');
  const why = mkEl('autostartReason');
  const els = { aboutVersion: ver, autostartSwitch: sw, autostartReason: why };

  const calls = { setAutostart: [] };
  const toasts = [];
  const alerts = [];

  const Backend = {
    getVersion: () => Promise.resolve(opts.version),
    getAutostart: () => Promise.resolve(opts.autostart),
    setAutostart: (on) => { calls.setAutostart.push(on); return Promise.resolve(opts.setResult); }
  };
  const sandbox = {
    window: { Backend },
    Backend,
    document: { getElementById: (id) => els[id] || null },
    Promise,
    setInterval: () => 0,          // 不跑轮询：末尾那两行显式调用已经够
    clearInterval: () => {},
    console,
    toast: (m, k) => toasts.push([m, k]),
    uiAlert: (m, o) => alerts.push([m, o])
  };
  vm.runInNewContext(SRC, sandbox, { filename: 'aboutTier.js' });
  return { ver, sw, why, calls, toasts, alerts, tick: () => new Promise(r => setTimeout(r, 5)) };
}

(async function main() {
  console.log('[S1] 支持且已开');
  {
    const s = runScenario({
      version: { ok: true, text: 'v0.2.0 (build 20261018)' },
      autostart: { ok: true, supported: true, enabled: true },
      setResult: { success: true }
    });
    await s.tick();
    chk('版本号显示后端给的 text', s.ver.textContent === 'v0.2.0 (build 20261018)', s.ver.textContent);
    chk('开关是亮的', s.sw.classList.contains('on'));
    chk('aria-checked=true', s.sw.attrs['aria-checked'] === 'true');
    chk('没有原因行（支持且正常）', s.why.textContent === '');
    chk('没有置灰', !s.sw.classList.contains('inert'));
  }

  console.log('[S2] 不支持（开发环境 .venv）');
  {
    const s = runScenario({
      version: { ok: true, text: 'v0.2.0' },
      autostart: { ok: true, supported: false, enabled: false },
      setResult: { success: false, error: '开发环境不支持（仅安装版可用）' }
    });
    await s.tick();
    chk('置灰（inert）', s.sw.classList.contains('inert'));
    chk('原因行说清是开发环境', s.why.textContent.indexOf('开发环境') >= 0, s.why.textContent);
    chk('原因行可见（show）', s.why.classList.contains('show'));
    s.sw.click();
    await s.tick();
    chk('**点了不调后端**（对照组：不许写注册表）', s.calls.setAutostart.length === 0,
        '调用 ' + s.calls.setAutostart.length + ' 次');
    chk('开关没被翻亮（不谎报）', !s.sw.classList.contains('on'));
  }

  console.log('[S3] 后端没连上（三态里的"问不出来"）');
  {
    const s = runScenario({
      version: { success: false, offline: true, error: '后端未连接：请确认达妮娅主程序正在运行' },
      autostart: { success: false, offline: true, error: '后端未连接：请确认达妮娅主程序正在运行' },
      setResult: { success: false, error: '后端未连接' }
    });
    await s.tick();
    chk('版本号报读取失败', s.ver.textContent.indexOf('读取失败') === 0, s.ver.textContent);
    chk('原因行报读取失败', s.why.textContent.indexOf('读取开机自启状态失败') === 0, s.why.textContent);
    chk('**不许说成"开发环境"**（对照组：那是假话）',
        s.why.textContent.indexOf('开发环境') < 0);
    chk('没置灰（状态未知不等于不支持）', !s.sw.classList.contains('inert'));
  }

  console.log('[S4] 支持且关着 → 点开');
  {
    const s = runScenario({
      version: { ok: true, text: 'v0.2.0' },
      autostart: { ok: true, supported: true, enabled: false },
      setResult: { success: true, enabled: true }
    });
    await s.tick();
    chk('初始是关的', !s.sw.classList.contains('on'));
    s.sw.click();
    await s.tick();
    chk('调了 setAutostart(true)', s.calls.setAutostart.length === 1 && s.calls.setAutostart[0] === true,
        JSON.stringify(s.calls.setAutostart));
    chk('成功后开关保持亮', s.sw.classList.contains('on'));
    chk('给了成功提示', s.toasts.length === 1 && s.toasts[0][1] === undefined,
        JSON.stringify(s.toasts));
  }

  console.log('[S5] 写入失败 → 翻回去');
  {
    const s = runScenario({
      version: { ok: true, text: 'v0.2.0' },
      autostart: { ok: true, supported: true, enabled: false },
      setResult: { success: false, error: '注册表写入被拒绝' }
    });
    await s.tick();
    s.sw.click();
    await s.tick();
    chk('开关翻回关（不谎报成功）', !s.sw.classList.contains('on'));
    chk('红字提示带上了后端原因',
        s.toasts.length === 1 && s.toasts[0][1] === 'err'
        && String(s.toasts[0][0]).indexOf('注册表写入被拒绝') >= 0,
        JSON.stringify(s.toasts));
  }

  console.log('\n结果: ' + ok + ' PASS / ' + bad + ' FAIL');
  process.exit(bad === 0 ? 0 : 1);
})();
