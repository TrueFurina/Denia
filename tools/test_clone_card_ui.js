/* 首启克隆面板「预填她的」行为真值表（2026-10-22）。
 *
 * 背景（修的是真缺陷，不是加装饰）：预置角色「温柔女友」的参考音频随包分发
 * （_role_assets/ref/xiaowei.wav），但克隆面板原来**预填写一份、按钮读另一份**：
 *   · 预填：voice.ref || demo.ref（预置角色只有 demo.ref，path 还是开发机绝对路径）；
 *   · 试听 clAudioPlay / 开始克隆 clStart：只读 c.voice.ref（预置角色里没有）。
 * 于是新用户走「选路 → 本地 → 克隆面板」后：试听按钮静默无反应；「开始克隆」
 * 取到空 refPath → 跳过真实克隆，落到「无桥降级」画一条假进度条，再弹
 * 「克隆完成」——机器上什么都没发生（本文件红线：不画假进度）。
 *
 * 为什么这么绕：判据是"点了以后界面/桥接发生什么"，静态查源码只能证明字符串
 * 在不在。这里用 jsdom **原样加载** src/ui/web/onboarding_role.js 整文件
 * （整文件 vm/eval 加载，不抽片段——抽片段只证明"我两次写得一样"），mock
 * window.Backend 后真点真走。
 *
 * 场景（含两个对照组）：
 *   S1 预置角色（随包资产）：预填来自 demo.ref；试听播随包 url；开始克隆先经
 *      use_bundled_ref_audio 物化成磁盘路径，再走 testVoice 真桥；失败如实报
 *   S2 对照组·用户自选文件：试听走 /local_img；开始克隆直接用持久化路径，
 *      **不**物化（不调 use_bundled_ref_audio）
 *   S3 对照组·无桥：明确告知"当前环境没有语音桥"，不画假进度、不谎报完成
 *   S4 墓碑（源码级）：假进度条分支与开发机绝对路径都不在文件里
 *      （配 _qc_clone_card_mutation.py 的刀；注释里不复述这些字面量）
 */
'use strict';
const fs = require('fs');
const path = require('path');
const { JSDOM, VirtualConsole } = require('jsdom');

const ROOT = path.resolve(__dirname, '..');
const SRC = path.join(ROOT, 'src', 'ui', 'web', 'onboarding_role.js');

let ok = 0, bad = 0;
function chk(name, cond, extra) {
  if (cond) { ok++; console.log('  PASS ' + name + (extra !== undefined ? '  | ' + extra : '')); }
  else { bad++; console.log('  FAIL ' + name + (extra !== undefined ? '  | ' + extra : '')); }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* 物化后的真实路径（mock use_bundled_ref_audio 的返回值） */
const STORED = 'D:\\Denia\\data\\voice_ref\\xiaowei.wav';
const PICKED_STORED = 'D:\\Denia\\data\\voice_ref\\my.wav';

/* 本地克隆引擎已安装 → S.cloneModel=true（克隆面板才打得准） */
const ENGINES = { success: true, engines: [
  { id: 'voxcpm', kind: 'local', label: 'VoxCPM2', installed: true,
    disk_gb: 2.4, gpu_gb: 0, device: 'CPU', verdict: 'ok', note: '' }
] };

function boot(opts) {
  opts = opts || {};
  /* jsdom 的 CSS 解析器吃不下模块样式里的变量/嵌套语法，会往 console 打
     "Could not parse CSS stylesheet"——与本测试无关，单独收起来不刷屏。 */
  const vc = new VirtualConsole();
  const cssErrs = [];
  vc.on('jsdomError', (e) => cssErrs.push(String(e && e.message || e)));
  const dom = new JSDOM('<!DOCTYPE html><html><body><div id="host"></div></body></html>', {
    runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/',
    virtualConsole: vc
  });
  const { window } = dom;
  const { document } = window;
  const errs = [];
  window.addEventListener('error', (e) => errs.push(String((e.error && e.error.stack) || e.message)));

  const calls = { bundled: [], testVoice: [], save: [], setRef: [], pick: [], audio: [] };
  const B = { ready: () => true, toast: () => {} };
  /* 只给 listCloneEngines：loadFromBackend 据此把 S.cloneModel 置 true；
     故意不给 getCharacters —— 它会用真实角色**整体替换** PRESETS，
     预置角色（温柔女友 + 随包资产）就测不到了。 */
  B.listCloneEngines = () => Promise.resolve(ENGINES);
  if (opts.bridge !== 'nobridge') {
    B.useBundledRefAudio = (d) => {
      calls.bundled.push(d);
      return Promise.resolve({ success: true, path: STORED, stored_path: STORED,
                               copied: true, name: 'xiaowei.wav', duration_s: 9 });
    };
    B.testVoice = (cfg, text) => {
      calls.testVoice.push([cfg, text]);
      return Promise.resolve({ success: true, task_id: 'vt_1' });
    };
    /* 试听任务立即以失败结束：用来验"失败如实报、不谎报完成" */
    B.getVoiceTestStatus = () => Promise.resolve(
      { success: true, phase: 'error', done: true, result_success: false, error: '模拟引擎不可用' });
    B.saveCharacter = (d) => { calls.save.push(d); return Promise.resolve({ success: true }); };
    B.setReferenceAudio = (d) => { calls.setRef.push(d); return Promise.resolve({ success: true }); };
    B.pickReferenceAudio = () => {
      calls.pick.push(1);
      return Promise.resolve({ success: true, path: 'C:\\Users\\x\\Downloads\\my.wav',
                               stored_path: PICKED_STORED, copied: true,
                               name: 'my.wav', duration_s: 8 });
    };
  }
  window.Backend = B;
  /* jsdom 不实现 HTMLMediaElement：Audio 构造函数换成记录器 */
  window.Audio = function (src) {
    calls.audio.push(src);
    this.src = src;
    this.play = () => Promise.resolve();
    this.onended = null;
  };
  window.confirm = () => true;

  const src = fs.readFileSync(SRC, 'utf8');
  window.eval(src);
  window.OnboardingRole.mountInto(document.getElementById('host'));
  return { window, document, calls, errs };
}

/* 预置角色「温柔女友」→ 点声音按钮 → 选路 → 本地 → 克隆面板 */
async function openPresetCard(w) {
  const { document } = w;
  const card = document.querySelector('.pcard[data-char-id="xiaowei"]');
  if (!card) throw new Error('找不到预置角色卡片（xiaowei）');
  card.querySelector('.vbtn').click();          // tryVoice → 未配置 → openRoute
  await sleep(10);
  document.querySelector('#rtLocal').click();    // 委托在 document 上 → S.cloneModel → openCloneCard
  await sleep(10);
}

async function main() {
  /* ---------- S1 预置角色：随包资产两端都真能跑 ---------- */
  console.log('[S1] 预置角色（温柔女友）：预填她的 → 试听播随包资产 → 克隆走真桥');
  {
    const w = boot();
    await sleep(30);                              // 等 loadFromBackend 的 listCloneEngines
    await openPresetCard(w);
    const { document, calls } = w;
    chk('没有未捕获错误', w.errs.length === 0, w.errs[0] || '');
    chk('预填文件名 = xiaowei.wav',
      document.getElementById('clAudioName').textContent === 'xiaowei.wav',
      document.getElementById('clAudioName').textContent);
    chk('副标题不带任何开发机路径（只有名字和秒数）',
      document.getElementById('clAudioSub').textContent === '她的原始录音 · 9 秒',
      document.getElementById('clAudioSub').textContent);

    document.getElementById('clAudioPlay').click();
    await sleep(10);
    chk('试听播的是随包 url（_role_assets/ref/xiaowei.wav）',
      calls.audio.length === 1 && calls.audio[0] === '_role_assets/ref/xiaowei.wav',
      JSON.stringify(calls.audio));

    document.getElementById('clStart').click();
    await sleep(80);
    chk('随包资产经后端物化了一次，参数是 url',
      calls.bundled.length === 1 && calls.bundled[0] && calls.bundled[0].url === '_role_assets/ref/xiaowei.wav',
      JSON.stringify(calls.bundled));
    chk('试听任务带的是物化后的真实磁盘路径',
      calls.testVoice.length === 1 && calls.testVoice[0][0].reference_audio === STORED,
      JSON.stringify(calls.testVoice[0] && calls.testVoice[0][0]));
    chk('失败如实报（试合成失败 + 后端错误）',
      document.getElementById('toastTxt').textContent.indexOf('试合成失败') >= 0,
      document.getElementById('toastTxt').textContent);
    chk('**没有**谎报"克隆完成"',
      document.getElementById('toastTxt').textContent.indexOf('克隆完成') < 0,
      document.getElementById('toastTxt').textContent);
    chk('完成区没有亮（clDone）',
      !document.getElementById('clDone').classList.contains('on'));
    chk('进度文案是真实试合成（不是假进度条的"正在分析参考音频…"）',
      document.getElementById('clProgTxt').textContent === '正在用参考音频试合成…',
      document.getElementById('clProgTxt').textContent);
  }

  /* ---------- S2 对照组：用户自选文件 ---------- */
  console.log('[S2] 对照组：用户自选文件 → 试听走 /local_img，克隆直接用持久化路径（不物化）');
  {
    const w = boot();
    await sleep(30);
    await openPresetCard(w);
    const { document, calls } = w;
    document.getElementById('clPickAudio').click();
    await sleep(40);
    chk('选文件走了 pickReferenceAudio（带持久化的那个）', calls.pick.length === 1);
    chk('预填换成用户文件（持久化副本路径）',
      document.getElementById('clAudioName').textContent === 'my.wav',
      document.getElementById('clAudioName').textContent);
    chk('参考音落进角色（set_reference_audio 用的是持久化副本）',
      calls.setRef.length === 1 && calls.setRef[0].path === PICKED_STORED,
      JSON.stringify(calls.setRef));

    calls.audio.length = 0;
    document.getElementById('clAudioPlay').click();
    await sleep(10);
    chk('试听走 /local_img?path=（用户文件没有 url）',
      calls.audio.length === 1 &&
      calls.audio[0] === '/local_img?path=' + encodeURIComponent(PICKED_STORED),
      JSON.stringify(calls.audio));

    document.getElementById('clStart').click();
    await sleep(80);
    chk('用户文件**不**物化（use_bundled_ref_audio 零调用）', calls.bundled.length === 0);
    chk('试听任务直接带用户文件路径',
      calls.testVoice.length === 1 && calls.testVoice[0][0].reference_audio === PICKED_STORED,
      JSON.stringify(calls.testVoice[0] && calls.testVoice[0][0]));
  }

  /* ---------- S3 对照组：无桥 ---------- */
  console.log('[S3] 对照组：没有语音桥 → 明确告知，不画假进度、不谎报完成');
  {
    const w = boot({ bridge: 'nobridge' });
    await sleep(30);
    await openPresetCard(w);
    const { document } = w;
    document.getElementById('clStart').click();
    await sleep(80);
    const toast = document.getElementById('toastTxt').textContent;
    chk('toast 明说没有语音桥', toast.indexOf('没有语音桥') >= 0, toast);
    chk('**没有**谎报"克隆完成"', toast.indexOf('克隆完成') < 0, toast);
    chk('完成区没有亮', !document.getElementById('clDone').classList.contains('on'));
    chk('假进度条没有展开（clProgWrap 不带 on）',
      !document.getElementById('clProgWrap').classList.contains('on'));
    chk('进度文案不是假进度条那一串',
      document.getElementById('clProgTxt').textContent.indexOf('正在分析参考音频') < 0,
      document.getElementById('clProgTxt').textContent);
  }

  /* ---------- S4 墓碑（源码级，配变异刀） ---------- */
  console.log('[S4] 墓碑：假进度条分支与开发机绝对路径都不在源码里');
  {
    const src = fs.readFileSync(SRC, 'utf8');
    chk('假进度条分支（setInterval 画 pct 那段）已删除',
      src.indexOf('pct = Math.min(100, pct + (6 + Math.random()*9))') < 0);
    chk('预置数据里的开发机绝对路径已删除', src.indexOf('D:/GPT-SoVITS') < 0);
    chk('clStart 不再只读 c.voice.ref.path（分歧根源）',
      src.indexOf("var refPath = (c.voice && c.voice.ref && c.voice.ref.path) || '';") < 0);
  }

  console.log('\n结果: ' + ok + ' PASS / ' + bad + ' FAIL');
  return bad === 0 ? 0 : 1;
}

main().then((c) => process.exit(c)).catch((e) => {
  console.log('  FAIL 测试自己崩了：' + ((e && e.stack) || e));
  process.exit(1);
});
