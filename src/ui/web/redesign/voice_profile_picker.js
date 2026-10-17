/* =====================================================================
 *  voice_profile_picker.js —— 「出声音色」统一选择器的唯一实现
 *  2026-10-08 建立（P2 · 音色档三源收口）
 *
 *  【为什么要这个文件】
 *  在建立本文件之前，「出声音色」下拉在**三处各写了一份**，且已经分叉：
 *
 *    1) src/ui/web/redesign/ui_redesign_characters.html   角色卡编辑器
 *    2) src/ui/web/first_launch_setup.html                首启引导页
 *       ↑ 以上两份是 setupVoiceSelector 的逐字节复制（忽略空白后 MD5 相同），
 *         但**各自依赖的同名函数实现不同**，于是同一个下拉在两类页面里表现不一：
 *           - engineLabel：角色卡说「微软小薇」/「阿里云百炼」，引导页说
 *             「Edge 微软语音」/「百炼 CosyVoice」；且引导页那份缺 8 个真实
 *             本地引擎键（voxcpm / qwen3tts / cosyvoice / gpt_sovits / …），
 *             遇到它们会原样吐出小写英文 id（用户截图里 "gpt-sovits" 就是这么露馅的）。
 *           - esc：引导页那份只转义 & < "，漏了 > 和 ' → 音色名含这两个字符时
 *             会破坏 HTML 结构（属性逃逸风险）。
 *    3) src/ui/web/redesign/ui_redesign_settings.html     设置·声音屏
 *       ← 形态不同（走 voice_tier.js 的卡片式列表），本文件不接管它，
 *         但两边的引擎显示名从此共用同一张表（见 TTS_ENGINE_LABEL）。
 *
 *  【本文件做什么】
 *  把 setupVoiceSelector + engineLabel + esc 收敛为唯一真值，两个页面改为
 *  引用本文件。这样三处音色下拉的**数据源**（Backend.listVoiceProfiles /
 *  listCloudVoices）本来就相同，**展示层**也从此刻起相同。
 *
 *  【不改什么】
 *  - 不改后端接口、不改数据源、不改 optgroup 分组逻辑与顺序。
 *  - 不改 DOM 结构（仍认 #eVoiceSel / #eVoiceTip / #eVoiceTry / #eVoiceNew
 *    / #eVoiceNewBox / #eVNName / #eVNEngine / #eVNApiKey / #eVNVoiceId），
 *    故两页的 HTML 无需改动，只改调用一行为
 *      window.VoiceProfilePicker.setup(B, root, role, ctxP, deps)
 *  - 返回对象形状与原函数完全一致：{selected, getBinding, ensureProfile,
 *    needsRefAudio, llmReady, voiceCallReady}（后三个仅在 ctx 就绪时可用）。
 *
 *  【依赖注入】
 *  setup(B, root, role, ctxP, deps) 的 deps 可选：
 *    { toastMsg, allProfiles }
 *  ⚠️ esc / engineLabel **不接受注入**：它们是本模块的唯一真值。
 *     收口前两页各带一份不同实现（引擎名不一致、esc 转义集不一致），
 *     若允许注入就等于把旧分叉搬进新架构，收口失效。
 *  allProfiles 若由调用方传入（数组），本模块会在每次 fill() 后就地同步其
 *  长度/内容，保证调用方（角色页的 voicePillText、试听用的 doTest）仍能读到
 *  最新档列表。toastMsg 可注入（两页实现相同，仅为复用其视觉风格）。
 *
 *  【加载方式】
 *  普通 <script src>（非 module），挂到 window.VoiceProfilePicker，
 *  与 app_backend.js / voice_tier.js 同为全局脚本，避免 module 的 CSP/时序问题。
 * ===================================================================== */
(function (root) {
  'use strict';

  /* ------------------------------------------------------------------
   * 引擎显示名唯一真值表
   * 取三份实现的**并集**：
   *  - 角色页原有 12 键（含 8 个真实本地引擎，引导页那份没有）
   *  - 引导页原有 8 个云端厂商别名（aliyun/azure/azure_cosy/bailian/fish/
   *    fish_audio/glm/mimo）
   * 值统一采用**角色页那份**：它对齐了设置页 voice_tier.js 的 TTS_VENDORS
   * 命名习惯，且原注释记录过修正理由（补 openai_tts / dashscope_cosy）。
   * 新增引擎请**只改这里**。
   * ------------------------------------------------------------------ */
  var TTS_ENGINE_LABEL = {
    /* —— 本地引擎 —— */
    'edge-tts':     '微软小薇',
    'gpt_sovits':   'GPT-SoVITS',
    'gpt-sovits':   'GPT-SoVITS',
    'voxcpm':       'VoxCPM',
    'qwen3tts':     'Qwen3-TTS',
    'cosyvoice':    'CosyVoice',
    'local':        '本地克隆',
    'pyttsx3':      '离线降级',
    'mimo_tts':     '小米MiMo',
    /* —— 云端引擎（含本地 TTS 引擎名与云端厂商别名两种写法）—— */
    'openai_tts':   '云端OpenAI兼容',
    'dashscope_cosy':'阿里云百炼',
    'minimax':      'MiniMax',
    /* —— 云端厂商别名（来自引导页那份，历史数据里出现过）—— */
    'aliyun':       '阿里云',
    'azure':        'Azure',
    'azure_cosy':   'Azure CosyVoice',
    'fish':         'Fish Audio',
    'fish_audio':   'Fish Audio',
    'mimo':         'MiniMax',
    'glm':          '智谱',
    'bailian':      '百炼'
  };

  /* 唯一真值：引擎 id → 显示名。未知引擎原样返回（不再兜底成「未知引擎」，
     避免把「引擎名就是它本身」这种情况抹掉）。 */
  function engineLabel(engine) {
    if (engine == null || engine === '') return '默认';
    return TTS_ENGINE_LABEL[engine] || engine;
  }

  /* 唯一真值：HTML 转义。转义 [&<>"'] 全部 5 个字符
     （引导页旧版缺 > 和 '，会把音色名里的 ' 直接吐进属性值里）。 */
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (m) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m];
    });
  }

  /* 兜底提示出口：优先走 Backend.toast，然后在当前文档里造一个轻提示。 */
  function defaultToast(text) {
    try {
      if (root.Backend && typeof root.Backend.toast === 'function') { root.Backend.toast(text); return; }
    } catch (e) { /* 忽略 */ }
    try {
      var d = root.document.createElement('div');
      d.textContent = text;
      d.style.cssText = 'position:fixed;left:50%;bottom:12%;transform:translateX(-50%);' +
        'background:rgba(0,0,0,.82);color:#fff;padding:10px 16px;border-radius:10px;' +
        'font-size:13px;z-index:99999;pointer-events:none';
      root.document.body.appendChild(d);
      setTimeout(function () { try { d.remove(); } catch (e) {} }, 2200);
    } catch (e) { /* 忽略 */ }
  }

  /* ==================================================================
   * setup(B, root, role, ctxP, deps)
   *   B      后端桥（角色页传 Backend，引导页传 BK）
   *   root   编辑器容器元素（在其内部 querySelector）
   *   role   被编辑角色对象，可空
   *   ctxP   上下文：对象或 Promise，形如
   *          { recommended_kind:'local_profile'|'cloud_duplex',
   *            needs_ref_audio, llm_ready, voice_call_ready, banner, mode }
   *   deps   { esc, engineLabel, toastMsg, allProfiles } 均可选
   * 返回 { selected, getBinding, ensureProfile, needsRefAudio, llmReady, voiceCallReady }
   * ================================================================== */
  function setup(B, rootEl, role, ctxP, deps) {
    deps = deps || {};
    /* ★ 关键（2026-10-08 首次收口纠正）：
       这里**故意不采用** deps.esc / deps.engineLabel。
       收口前角色页与引导页各自带了**不同**的 esc / engineLabel 实现，
       若让它们继续注入，等于把旧分叉原样搬进新架构——收口就白做了。
       本模块的 esc / engineLabel 是唯一真值（engineLabel 取两份实现的并集）。 */
    var escFn = esc;
    var lblFn = engineLabel;
    var toastFn = typeof deps.toastMsg === 'function' ? deps.toastMsg : defaultToast;
    /* allProfiles：调用方若传数组，本模块把结果**就地写入**该数组，
       使调用方（角色页 voicePillText / doTest）读到的是最新档列表。 */
    var externalProfiles = (deps.allProfiles && typeof deps.allProfiles.length === 'number') ? deps.allProfiles : null;
    var localProfiles = [];
    function setProfiles(list) {
      localProfiles = list || [];
      if (externalProfiles) {
        try { externalProfiles.length = 0; for (var i = 0; i < localProfiles.length; i++) externalProfiles.push(localProfiles[i]); }
        catch (e) { /* 只读数组则忽略 */ }
      }
    }
    function getProfiles() { return externalProfiles || localProfiles; }

    var sel = rootEl.querySelector('#eVoiceSel');
    var tip = rootEl.querySelector('#eVoiceTip');
    var tryBtn = rootEl.querySelector('#eVoiceTry');
    var newBtn = rootEl.querySelector('#eVoiceNew');
    var newBox = rootEl.querySelector('#eVoiceNewBox');

    if (typeof B === 'undefined' || !B) {
      if (sel) sel.innerHTML = '<option value="">— 后端未连接（预览）—</option>';
      return {
        selected: function () { return null; },
        getBinding: function () { return { voice_profile: (role && role.voice_profile) || '', cloud_voice_id: (role && role.cloud_voice_id) || '' }; },
        ensureProfile: function () { return Promise.resolve(null); },
        needsRefAudio: function () { return false; },
        llmReady: function () { return false; },
        voiceCallReady: function () { return false; }
      };
    }

    function optData(o) {
      return o ? {
        kind: o.dataset.kind, engine: o.dataset.engine,
        voiceId: (o.dataset.voiceId || ''), name: (o.dataset.name || ''), model: (o.dataset.model || '')
      } : null;
    }
    function selected() {
      var o = (sel && sel.selectedOptions && sel.selectedOptions[0]) || null;
      return optData(o);
    }

    function tipFor(d) {
      if (!tip) return;
      if (!d) { tip.textContent = '未选择：本地用默认 edge-tts，云端双工用官方预设声。'; return; }
      if (d.kind === 'profile') tip.textContent = '本地音色档：' + (d.name || d.engine) + '（本地双工通话 / 本地 TTS 都走这里）。';
      else if (d.kind === 'cloud_tts') tip.textContent = (d.name || '云端 TTS') + '（云端 TTS，需声音的档已绑 Key；本地双工出声也走它）。';
      else if (d.kind === 'cloud_duplex') tip.textContent = (d.name || '云端双工') + '（云端全双工 S2S，只需一个 Key）。';
      else tip.textContent = '';
    }

    function poll(taskId) {
      var n = 0;
      var PH = { queued: '已提交…', loading: '正在加载语音模型…', synthesizing: '正在合成…', playing: '正在播放…' };
      var t = setInterval(function () {
        if (++n > 140) { clearInterval(t); if (tryBtn) tryBtn.disabled = false; if (tip) tip.textContent = ' 试听超时（70s 无响应）'; return; }
        if (typeof B.getVoiceTestStatus !== 'function') return;
        B.getVoiceTestStatus(taskId).then(function (s) {
          if (!(s && s.success)) return;
          if (tip) tip.textContent = PH[s.phase] || '处理中…';
          if (s.done) {
            clearInterval(t); if (tryBtn) tryBtn.disabled = false;
            if (tip) tip.textContent = s.result_success ? ' 试听完成（声音从本机播放）' : ' 试听失败：' + (s.error || '未知错误');
          }
        });
      }, 500);
    }

    function fill() {
      if (!sel) return;
      if (typeof B.listVoiceProfiles !== 'function') { sel.innerHTML = '<option value="">— 未绑定 —</option>'; return; }
      var curVP = (role && role.voice_profile) || '', curCV = (role && role.cloud_voice_id) || '';

      var profsP = Promise.resolve(B.listVoiceProfiles().then(function (r) { return (r && r.success) ? (r.profiles || []) : []; }));
      var ttsP = (typeof B.listCloudVoices === 'function') ? B.listCloudVoices({ context: 'tts' }).catch(function () { return null; }) : Promise.resolve(null);
      var dupP = (typeof B.listCloudVoices === 'function') ? B.listCloudVoices({ context: 'duplex' }).catch(function () { return null; }) : Promise.resolve(null);

      Promise.all([profsP, ttsP, dupP]).then(function (res) {
        var profs = res[0], tts = res[1], dup = res[2];
        setProfiles(profs || []);

        var localLabel = (ctx && ctx.recommended_kind === 'local_profile') ? '本地 · 音色档（推荐）' : '本地 · 音色档';
        var dupLabel = (ctx && ctx.recommended_kind === 'cloud_duplex') ? '云端 · 双工（推荐·只需 Key）' : '云端 · 双工（只需 Key）';

        var localOpts = (profs || []).map(function (p) {
          return '<option value="' + escFn(p.id) + '" data-kind="profile">' + escFn(p.name || p.id) + ' · ' + escFn(lblFn(p.engine)) + '</option>';
        }).join('');
        var gLocal = '<optgroup label="' + localLabel + '">' + (localOpts || '<option disabled>— 暂无音色档 —</option>') + '</optgroup>';

        var gTts = '';
        if (tts && tts.success) {
          // 多服务商：每个 provider 一个 optgroup；兼容旧「单 provider」响应（provider/voices）。
          var provs = (tts.providers && tts.providers.length) ? tts.providers
            : (tts.provider ? [{ provider: tts.provider, label: tts.provider, need_key: !!tts.need_key, voices: (tts.voices || []) }] : []);
          if (!provs.length) {
            gTts = '<optgroup label="云端 · TTS（需 Key+实名）"><option disabled>— 声音屏未绑 TTS Key，去绑定后可查 —</option></optgroup>';
          } else {
            gTts = provs.map(function (pr) {
              var ph = '';
              if (pr.need_key && !(pr.voices && pr.voices.length)) {
                ph = '<option disabled>— ' + escFn(pr.label || pr.provider) + '：未绑 Key —</option>';
              } else {
                ph = (pr.voices || []).map(function (v) {
                  return '<option value="ctts:' + escFn(v.id) + '" data-kind="cloud_tts" data-engine="' + escFn(pr.provider) + '" data-voice-id="' + escFn(v.id) + '" data-name="' + escFn(v.name) + '" data-model="' + escFn(v.model || '') + '">' + (v.type === 'cloned' ? '克隆 ' : '预设 ') + escFn(v.name) + '</option>';
                }).join('');
                if (!ph) ph = '<option disabled>— ' + escFn(pr.label || pr.provider) + '：无可用音色 —</option>';
              }
              return '<optgroup label="云端 · TTS · ' + escFn(pr.label || pr.provider) + (pr.need_key ? '（需 Key）' : '') + '">' + ph + '</optgroup>';
            }).join('');
          }
        } else {
          gTts = '<optgroup label="云端 · TTS（需 Key+实名）"><option disabled>— 云端 TTS 暂不可用 —</option></optgroup>';
        }

        var dupHtml = '';
        if (dup && dup.success) {
          if (dup.need_key && !(dup.voices && dup.voices.length)) dupHtml = '<option disabled>— 云端双工未绑 Key —</option>';
          else dupHtml = (dup.voices || []).map(function (v) {
            return '<option value="cdup:' + escFn(v.id) + '" data-kind="cloud_duplex" data-engine="' + escFn(dup.provider) + '" data-voice-id="' + escFn(v.id) + '" data-name="' + escFn(v.name) + '">' + escFn(v.name) + '</option>';
          }).join('');
        } else dupHtml = '<option disabled>— 云端双工暂不可用 —</option>';
        var gDup = '<optgroup label="' + dupLabel + '">' + dupHtml + '</optgroup>';

        var groups;
        var rec = (ctx && ctx.recommended_kind) || 'local_profile';
        if (rec === 'cloud_duplex') groups = [gDup, gLocal, gTts];
        else groups = [gLocal, gDup, gTts];

        sel.innerHTML = '<option value="">— 未绑定（edge-tts 兜底）—</option>' + groups.join('');

        var pick = '';
        if (curCV) pick = 'cdup:' + curCV; else if (curVP) pick = curVP;
        if (pick && Array.prototype.some.call(sel.options, function (o) { return o.value === pick; })) sel.value = pick;

        tipFor(selected());
      });
    }

    if (sel) sel.addEventListener('change', function () { tipFor(selected()); });

    if (tryBtn) tryBtn.addEventListener('click', function () {
      var d = selected();
      if (!d) { toastFn('先选一个音色再试听', 'err'); return; }

      if (d.kind === 'cloud_duplex') {
        if (typeof B.previewCloudVoice !== 'function') { if (tip) tip.textContent = '云端试听不可用（需软件内+已配云端双工）'; return; }
        tryBtn.disabled = true; if (tip) tip.textContent = '连接云端…';
        B.previewCloudVoice('你好呀，我是达妮娅，很高兴认识你～', (role && role.id) || '').then(function (r) {
          tryBtn.disabled = false;
          if (!(r && r.success && r.task_id)) { if (tip) tip.textContent = ' ' + ((r && (r.error || r.message)) || '无返回'); return; }
          poll(r.task_id);
        });
        return;
      }

      function doTest(pid) {
        var p = (getProfiles() || []).filter(function (x) { return x.id === pid; })[0];
        var cfg = Object.assign({ rate: '+0%', pitch: '+0Hz', volume: '+0%', speed: 1.0 }, p || {});
        tryBtn.disabled = true; if (tip) tip.textContent = '提交试听…';
        B.testVoice(cfg, '你好呀，我是你的桌面伙伴，很高兴认识你～').then(function (r) {
          if (!(r && r.success && r.task_id)) { tryBtn.disabled = false; if (tip) tip.textContent = ' ' + ((r && (r.error || r.message)) || '无返回'); return; }
          poll(r.task_id);
        });
      }

      if (d.kind === 'profile') doTest(sel.value);
      else {
        if (typeof B.ensureCloudVoiceProfile !== 'function') { if (tip) tip.textContent = '云端音色档接口不可用'; return; }
        B.ensureCloudVoiceProfile({ provider: d.engine, voice_id: d.voiceId, name: d.name }).then(function (r) {
          if (!(r && r.success)) { if (tip) tip.textContent = ' ' + ((r && r.error) || '未知'); return; }
          doTest(r.profile_id);
        });
      }
    });

    if (newBtn && newBox) {
      newBtn.addEventListener('click', function () { newBox.style.display = (newBox.style.display === 'none' ? 'block' : 'none'); });
      var cancel = newBox.querySelector('#eVoiceNewCancel'), save = newBox.querySelector('#eVoiceNewSave'), nTip = newBox.querySelector('#eVoiceNewTip');
      if (cancel) cancel.addEventListener('click', function () { newBox.style.display = 'none'; });
      if (save) save.addEventListener('click', function () {
        var name = newBox.querySelector('#eVNName').value.trim(), engine = newBox.querySelector('#eVNEngine').value;
        if (!name) { if (nTip) nTip.textContent = '请填名称'; return; }
        var prof = { id: 'vp_' + Date.now().toString(36), name: name, engine: engine, rate: '+0%', pitch: '+0Hz', volume: '+0%' };
        var k = newBox.querySelector('#eVNApiKey').value.trim(), vid = newBox.querySelector('#eVNVoiceId').value.trim();
        if (engine === 'dashscope_cosy' || engine === 'minimax' || engine === 'openai_tts' || engine === 'fish_audio') { prof.api_key = k; prof.voice_id = vid; }
        else if (engine === 'gpt-sovits' || engine === 'voxcpm' || engine === 'mimo_tts') { prof.reference_audio = k; prof.voice = vid; }
        else { prof.voice = k; }
        if (typeof B.saveVoiceProfile !== 'function') { if (nTip) nTip.textContent = '保存接口不可用（需软件内）'; return; }
        save.disabled = true;
        B.saveVoiceProfile(prof).then(function (r) {
          save.disabled = false;
          if (!(r && r.success)) { if (nTip) nTip.textContent = ' ' + ((r && r.error) || '未知'); return; }
          if (nTip) nTip.textContent = ' 已建「' + name + '」';
          newBox.style.display = 'none';
          fill();
          setTimeout(function () {
            var nv = null;
            for (var i = 0; i < sel.options.length; i++) { if (sel.options[i].value === prof.id) { nv = sel.options[i]; break; } }
            if (nv) sel.value = prof.id;
            tipFor(selected());
          }, 60);
        }).catch(function () { save.disabled = false; if (nTip) nTip.textContent = ' 保存失败'; });
      });
    }

    function getBinding(pendingPid) {
      var d = selected();
      var vp = (role && role.voice_profile) || '', cv = (role && role.cloud_voice_id) || '';
      if (d) {
        if (d.kind === 'profile') vp = sel.value;
        else if (d.kind === 'cloud_duplex') cv = d.voiceId;
        else if (d.kind === 'cloud_tts') vp = pendingPid || vp;
      }
      return { voice_profile: vp, cloud_voice_id: cv };
    }

    function ensureProfile() {
      var d = selected();
      if (d && d.kind === 'cloud_tts') {
        return B.ensureCloudVoiceProfile({ provider: d.engine, voice_id: d.voiceId, name: d.name })
          .then(function (r) { return (r && r.success) ? r.profile_id : null; });
      }
      return Promise.resolve(null);
    }

    var ctx = (ctxP && typeof ctxP.then !== 'function') ? ctxP : null;
    if (ctxP && typeof ctxP.then === 'function') {
      ctxP.then(function (x) { ctx = x; if (sel) fill(); }).catch(function () { /* 忽略 */ });
    }
    fill();

    return {
      selected: selected,
      getBinding: getBinding,
      ensureProfile: ensureProfile,
      needsRefAudio: function (d) { return !!(ctx && ctx.needs_ref_audio); },
      llmReady: function () { return !!(ctx && ctx.llm_ready); },
      voiceCallReady: function () { return !!(ctx && ctx.voice_call_ready); }
    };
  }

  root.VoiceProfilePicker = {
    version: '2026-10-08',
    TTS_ENGINE_LABEL: TTS_ENGINE_LABEL,
    engineLabel: engineLabel,
    esc: esc,
    setup: setup
  };
})(typeof window !== 'undefined' ? window : this);
