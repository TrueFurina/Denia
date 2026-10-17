(function(){

  const root = document.documentElement;
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const $ = s => document.querySelector(s);
  function morphIcon(pathEl, name){ if (window.Morph && pathEl) window.Morph.to(pathEl, name); }

  /* 主题色 / 深浅模式由 app_backend.js 的 window.Theme 统一处理（首屏前执行，并会清理自定义色残留的内联变量），此处不再重复实现。 */

  /* ---- 语音模式：mute / edge / clone（接入真实后端） ---- */
  const MODE_LABEL = { mute:'无声音', edge:'微软小薇 · EdgeTTS', clone:'全克隆 · 达妮娅本音' };
  const vlist = document.getElementById('vlist');
  const clonePane = document.getElementById('clonePane');

  /* 2026-10-08 修复：curMode 初值不再硬编码 'clone'。
     此处与 ui_redesign_settings.html 内联的同名逻辑是**第二份/更早执行的副本**
     （本文件由该页 1202 行 <script src> 先加载），两份都会绑 .vcard 点击并回写后端。
     旧代码 curMode='clone' + saveMode() 无守卫 ⇒ 页面刚打开就点卡片会把后端真值
     （如 mute）覆盖成 clone ——「刷新页面就能把 mute 变回 clone」的根因之一。
     修法：null 作"尚无值" + modeLoaded 作"是否已成功载入"的唯一可写前提。 */
  let curMode = null, curSub = 'local', modeLoaded = false;
  let dlTimer = null, dlTask = null;
  let curCloudPayload = null;   // 当前云端语音（来自 setVoiceMode(clone,cloud) 或选中的 voice_profile）

  function syncMode(){
    document.querySelectorAll('.vcard').forEach(c => c.classList.toggle('on', c.dataset.mode === curMode));
    clonePane.classList.toggle('show', curMode === 'clone');
    document.querySelectorAll('.sub').forEach(s => s.classList.toggle('on', s.dataset.sub === curSub));
    let label = MODE_LABEL[curMode] || curMode || '读取中…';
    if (curMode === 'clone') label += ' · ' + (curSub === 'local' ? '本地' : '云端');
    const vn = document.getElementById('voiceNow'); if (vn) vn.textContent = label;
    morphIcon($('#modePath'), curMode === 'mute' ? 'soundOff' : 'sound');
  }
  function saveMode(){
    /* 2026-10-08 修复：未成功载入真值前一律拒绝写入。
       必须用独立的 modeLoaded 标志 —— 点击处理器会先给 curMode 赋值再调本函数，
       那时判断 curMode===null 已失效。 */
    if (!modeLoaded){
      console.warn('[voice_tier] 语音模式尚未从后端载入，已忽略本次写入（防覆盖真实配置）');
      Backend.toast('正在读取语音设置，请稍候再试', 'err');
      return;
    }
    const data = curMode === 'clone'
      ? (curSub === 'cloud' ? Object.assign({sub:'cloud'}, curCloudPayload || {}) : {sub: curSub})
      : null;
    Backend.setVoiceMode(curMode, data).then(r => {
      if (r && r.success) Backend.toast('语音模式已保存');
      else Backend.toast(r && r.error ? ('保存失败：' + r.error) : '后端未连接（仅本地预览）', 'err');
    });
  }

  Backend.getVoiceMode().then(d => {
    if (d && d.success){
      /* mode 缺失时不再退回 'clone'（会把 mute 覆盖成 clone）→ 保持 null 未就绪 */
      curMode = d.mode || null;
      curSub = d.clone_sub || 'local';
      const c = d.clone_cloud || {};
      // 云端语音：优先用 clone_cloud 还原当前选中项（来自 setVoiceMode(clone,cloud)）
      curCloudPayload = (curSub === 'cloud' && (c.provider || c.model_name)) ? {
        provider: c.provider, model_name: c.model_name, api_url: c.api_url, api_key: c.api_key
      } : null;
      /* 只有后端确实给了明确 mode 才算载入完成、才允许写回 */
      modeLoaded = (curMode !== null);
    } else {
      console.warn('[voice_tier] getVoiceMode 失败，语音模式保持未就绪（本次不写盘）', d && d.error);
    }
    syncMode();
    const le = document.getElementById('localEngine');
    if (le){ le.value = (d && d.success && d.local_engine) || 'qwen3tts'; le.dataset.prev = le.value; }
    checkLocal();
    loadRef();
    loadCloudProfiles();
  }).catch(e => {
    console.warn('[voice_tier] getVoiceMode 异常，语音模式保持未就绪（本次不写盘）', e);
  });

  /* ---- 本地引擎 / 下载检测 / 参考音（接回本地千问逻辑） ---- */
  function esc(s){ return String(s==null?'':s).replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m])); }
  function checkLocal(){
    Backend.checkLocalModels().then(r => {
      const tag = document.getElementById('localStateTag');
      const dl = document.getElementById('dlStartLocal');
      const info = document.getElementById('dlEngineInfo');
      const ms = document.getElementById('msLink');
      if (!r || !r.success){ if (tag) tag.textContent = '检测失败'; if (info) info.textContent = ''; return; }
      const ready = !!r.ready;
      if (tag) tag.textContent = ready ? '已就绪 ' : '未下载 · 需下载';
      if (tag) tag.style.color = ready ? 'var(--ok, #2faa6a)' : 'var(--warn, #d98a00)';
      if (info) info.textContent = (r.engine_label || r.engine || '');
      if (ms){ if (r.modelscope_url){ ms.href = r.modelscope_url; ms.style.display = ''; } else { ms.style.display = 'none'; } }
      if (dl){
        dl.disabled = ready;
        const sp = dl.querySelector('span');
        if (sp) sp.textContent = ready ? '已下载' : '下载模型';
      }
    });
  }
  /* 本地克隆引擎切换：先预检该引擎的模型是否已下载（没模型切过去会静默失败
     或长时间卡在加载），未就绪弹窗提示先下载；切换期间卡片下沿走进度条。 */
  const le = document.getElementById('localEngine');
  const localCard = document.getElementById('localCard');
  if (le) le.addEventListener('change', () => {
    const next = le.value;
    const prev = le.dataset.prev || next;
    const opt = le.options[le.selectedIndex];
    const label = (opt && opt.text ? opt.text : next).trim();
    Backend.guardSwitch({
      host: localCard,
      title: '无法切换到「' + label + '」',
      check: () => Backend.checkCloneEngine(next),
      apply: () => Backend.setCloneEngine(next),
      okMsg: '本地克隆引擎已切换：' + label,
      onCancel: () => { le.value = prev; },
      onFail: () => { le.value = prev; },
      onDone: () => { le.dataset.prev = next; checkLocal(); },
    });
  });
  let activeChar = null;
  function loadRef(){
    Backend.getCharacters().then(r => {
      if (!r || !r.success) return;
      const list = r.characters || [];
      activeChar = list.find(c => c.id === r.active_id) || list[0] || null;
      renderRef(activeChar);
    });
  }
  function renderRef(c){
    const box = document.getElementById('refList'); if (!box) return;
    const v = (c && c.voice) || {};
    const paths = [v.reference_audio].filter(Boolean).concat((v.aux_reference_audio_paths || []).filter(Boolean));
    if (!paths.length){ box.innerHTML = '<span class="empty-tip">未设置</span>'; return; }
    box.innerHTML = paths.map(p => '<div class="ref-item">'+esc(p)+'</div>').join('');
  }
  function saveRef(path){
    if (!activeChar) return;
    const v = Object.assign({}, activeChar.voice || {});
    v.reference_audio = path;
    const data = Object.assign({}, activeChar, { voice: v });
    Backend.saveCharacter(data).then(res => {
      if (res && res.success){ Backend.toast('参考音已保存'); activeChar = Object.assign({}, activeChar, { voice: v }); renderRef(activeChar); }
      else Backend.toast(res && res.error ? ('保存失败：' + res.error) : '后端未连接', 'err');
    });
  }
  const pickRef = document.getElementById('pickRef'), recRef = document.getElementById('recRef');
  if (pickRef) pickRef.addEventListener('click', () => {
    Backend.pickAudioFile().then(r => { if (r && r.success && r.path) saveRef(r.path); else if (!r || !r.offline) Backend.toast('未选择音频', 'err'); });
  });
  if (recRef) recRef.addEventListener('click', () => {
    Backend.recordAudio('reference', 2.5).then(r => { if (r && r.success && r.path) saveRef(r.path); else Backend.toast(r && r.error ? ('录音失败：' + r.error) : '后端未连接', 'err'); });
  });

  vlist.querySelectorAll('.vcard').forEach(c => c.addEventListener('click', () => {
    curMode = c.dataset.mode;
    syncMode();
    saveMode();
  }));

  document.querySelectorAll('.sub').forEach(s => s.addEventListener('click', (e) => {
    if (e.target.closest('.form, .vlist, .addbtn')) return;
    curSub = s.dataset.sub;
    syncMode();
    if (curMode === 'clone') saveMode();
  }));

  /* ---- 本地模型下载（真实后端进度轮询） ---- */
  const fill = $('#dlFillLocal'), pct = $('#dlPctLocal'), state = $('#dlStateLocal'), startBtn = $('#dlStartLocal');
  function parseProgress(log){
    if (!log) return 0;
    const m = log.match(/(\d{1,3})%/);
    return m ? parseInt(m[1], 10) : 0;
  }
  function renderDl(p, done, label){
    if (fill) fill.style.width = p + '%';
    if (pct) pct.textContent = p + '%';
    if (state) state.textContent = label;
    if (startBtn){ const sp = startBtn.querySelector('span'); if (sp) sp.textContent = done ? '重新下载' : '下载中…'; }
    morphIcon($('#dlIcon'), done ? 'check' : 'download');
  }
  function pollDl(){
    if (!dlTask) return;
    Backend.modelStatus(dlTask).then(r => {
      if (!r || !r.success){ stopDl(); return; }
      const st = r.status || 'running';
      const p = Math.max(0, Math.min(100, parseProgress(r.log || '')));
      if (st === 'done'){ renderDl(100, true, '已完成 · 模型就绪'); Backend.toast('模型下载完成'); checkLocal(); stopDl(); }
      else if (st === 'error'){ renderDl(p, false, '下载失败'); Backend.toast('模型下载失败', 'err'); stopDl(); }
      else if (st === 'manual'){ renderDl(p, false, '请按提示手动下载'); stopDl(); }
      else { renderDl(p, false, '下载中…'); }
    });
  }
  function stopDl(){ if (dlTimer){ clearInterval(dlTimer); dlTimer = null; } dlTask = null; if (startBtn) startBtn.disabled = false; }
  if (startBtn) startBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (dlTask) return;
    const engine = (document.getElementById('localEngine') || {}).value || 'qwen3tts';
    renderDl(0, false, '下载中…');
    startBtn.disabled = true;
    Backend.downloadModel({ engine: engine }).then(r => {
      if (r && r.success && r.task_id){ dlTask = r.task_id; dlTimer = setInterval(pollDl, 1000); }
      else if (r && r.already){ startBtn.disabled = false; renderDl(100, true, '已就绪 · 无需下载'); Backend.toast(r.engine_label ? (r.engine_label + ' 已就绪') : '模型已就绪'); checkLocal(); }
      else {
        startBtn.disabled = false;
        Backend.toast(r && r.error ? ('下载失败：' + r.error) : '后端未连接（仅本地预览）', 'err');
      }
    });
  });

  /* ================= 云端语音多套并存（接入 listVoiceProfiles / saveVoiceProfile / setVoiceMode(clone,cloud)） ================= */
  const TTS_VENDORS = [
    {id:'edge', name:'Edge TTS（内置）', color:'#0F7FFF', letter:'E', kind:'builtin', free:true, clone:false,
     models:[['zh-CN-XiaoxiaoNeural','微软晓晓 · 通用女声'],['zh-CN-XiaoyiNeural','晓伊']], note:'免 Key、已内置；不克隆本音'},
    {id:'minimax', name:'MiniMax', color:'#FF3B5C', letter:'M', kind:'custom', clone:true, api:'https://api.minimaxi.com', env:'MINIMAX_API_KEY',
     models:[['speech-02-hd','高保真'],['speech-02-turbo','低延迟']], note:'3 个克隆位 + 试用 credits'},
    {id:'volcengine-tts', name:'火山引擎（豆包语音）', color:'#1664FF', letter:'V', kind:'custom', clone:true, api:'https://openspeech.bytedance.com/api/v3/tts', env:'APPID/TOKEN',
     models:[['声音复刻 2.0','5–10 秒素材'],['大模型语音合成','通用音色']], note:'中文自然度最好；试用 2 万字符；鉴权较特殊'},
    {id:'dashscope-tts', name:'阿里云百炼', color:'#FF6A00', letter:'Q', kind:'custom', clone:true, api:'https://dashscope.aliyuncs.com', env:'DASHSCOPE_API_KEY',
     models:[['qwen3-tts-flash','官方·推荐'],['cosyvoice-v3-flash','快'],['cosyvoice-v3.5-plus','质感'],['cosyvoice-v2','经典'],['qwen-audio-3.0-tts-plus','通义·推荐'],['qwen-audio-3.0-tts-flash','通义·轻量']], note:'声音复刻 10–20 秒素材'},
    {id:'siliconflow', name:'硅基流动', color:'#0EA5E9', letter:'S', kind:'openai_compat', clone:true, api:'https://api.siliconflow.cn/v1/audio/speech', env:'SILICONFLOW_CN_API_KEY',
     models:[['FunAudioLLM/CosyVoice2-0.5B','零样本克隆'],['fnlp/MOSS-TTSD-v0.5','对话语音']], note:'¥0.05/千字符起'},
    {id:'openai', name:'OpenAI', color:'#10A37F', letter:'O', kind:'openai_compat', clone:false, api:'https://api.openai.com/v1/audio/speech', env:'OPENAI_API_KEY',
     models:[['gpt-4o-mini-tts','支持指令控风格'],['tts-1','经典']]},
    {id:'azure', name:'Azure 语音', color:'#0078D4', letter:'A', kind:'custom', clone:true, api:'https://{region}.tts.speech.microsoft.com', env:'AZURE_SPEECH_KEY',
     models:[['zh-CN-XiaoxiaoNeural','通用'],['自定义神经语音','需训练']], note:'有免费额度；自定义语音门槛较高'},
    {id:'custom', name:'自定义 / 其它', color:'#6C7689', letter:'C', kind:'custom', clone:true, api:'', env:'',
     models:[['（手填）','任何 OpenAI 兼容 /audio/speech']], note:'自建网关、One-API 等'}
  ];
  /* 对齐真实 config.yaml 的 voice 引擎名（见 tts_llm_vendor_preview 的任务说明） */
  const ENGINE_OF = { minimax:'minimax', 'volcengine-tts':'gpt-sovits', 'dashscope-tts':'dashscope_cosy',
                      openai:'openai_tts', edge:'edge-tts', siliconflow:'gpt-sovits', azure:'azure', custom:'gpt-sovits' };
  const TV = id => TTS_VENDORS.find(x => x.id === id) || {id, color:'#6C7689', letter:'?', name:id, models:[['','']], kind:'custom', clone:false, api:''};
  function vlogoHTML(v, cls){ const c = cls || 'logo';
    const fb = `<span style="width:100%;height:100%;display:grid;place-items:center;background:${v.color};color:#fff">${v.letter}</span>`;
    return `<span class="${c}" data-fb="${encodeURIComponent(fb)}"><img src="https://models.dev/logos/${v.id}.svg" alt="" onerror="this.parentNode.innerHTML=decodeURIComponent(this.parentNode.dataset.fb)"></span>`; }

  let cloudProfiles = [];   // 来自 Backend.listVoiceProfiles()
  function loadCloudProfiles(){
    const box = document.getElementById('cloudVoiceList'); if (!box) return;
    Backend.listVoiceProfiles().then(r => {
      cloudProfiles = (r && r.success && Array.isArray(r.profiles)) ? r.profiles : [];
      renderCloudList();
    }).catch(() => { cloudProfiles = []; renderCloudList(); });
  }
  function renderCloudList(){
    const box = document.getElementById('cloudVoiceList'); if (!box) return;
    if (!cloudProfiles.length){ box.innerHTML = '<div class="empty-tip">还没有云端语音配置，点下面按钮添加</div>'; return; }
    box.innerHTML = cloudProfiles.map(p => {
      const v = TV(p.vendor);
      const active = curMode === 'clone' && curSub === 'cloud' && curCloudPayload
                  && curCloudPayload.provider === p.vendor && curCloudPayload.model_name === p.model_name;
      const refBy = (p.referenced_by && p.referenced_by.length) ? p.referenced_by : [];
      return `<div class="vcard">
        ${vlogoHTML(v)}
        <div class="vmeta"><div class="vn">${esc(p.name || p.model_name)} <span class="vtier">${esc(v.name)}</span>${active?'<span class="vtier act">使用中</span>':''}</div>
          <div class="vd">${esc(p.model_name || '')} · ${p.api_key ? '已填 Key' : '无需 Key'}${v.clone ? ' · 可克隆' : ''}</div></div>
        <div class="vacts">
          ${active ? '' : `<button class="act" data-act="cloud-use" data-id="${esc(p.id)}">设为当前</button>`}
          <button class="act" data-act="cloud-edit" data-id="${esc(p.id)}">编辑</button>
          <button class="act danger" data-act="cloud-del" data-id="${esc(p.id)}">删除</button>
          ${refBy.length ? `<span class="ref-by" title="${esc(refBy.join('、'))}">被「${esc(refBy.join('、'))}」引用</span>` : ''}
        </div></div>`;
    }).join('');
  }

  /* ---- 云端语音添加/编辑弹窗（#vcloudPickModal，data-close="vcloudPick"） ---- */
  let pickV = TTS_VENDORS[0], pickModel = pickV.models[0][0], pickEditId = null;
  function openCloudPicker(editId){
    pickEditId = editId || null;
    const t = pickEditId ? cloudProfiles.find(p => p.id === pickEditId) : null;
    pickV = t ? TV(t.vendor) : TTS_VENDORS[0];
    pickModel = t ? (t.model_name || pickV.models[0][0]) : pickV.models[0][0];
    renderCloudPicker();
    openModal('vcloudPick');
  }
  function renderCloudPicker(){
    const v = pickV;
    const chips = document.getElementById('vcloudPickChips');
    const logo = document.getElementById('vcloudPickLogo');
    const title = document.getElementById('vcloudPickTitle');
    const hint = document.getElementById('vcloudPickHint');
    const modelSel = document.getElementById('vcloudPickModel');
    const nameI = document.getElementById('vcloudPickName');
    const urlI = document.getElementById('vcloudPickUrl');
    const keyI = document.getElementById('vcloudPickKey');
    const refField = document.getElementById('vcloudPickRefField');
    if (chips) chips.innerHTML = TTS_VENDORS.map(x => `<button type="button" class="vc ${x.id === v.id ? 'on' : ''}" data-vc="${x.id}">${vlogoHTML(x, 'logo')} <span>${esc(x.name)}</span></button>`).join('');
    if (logo) logo.innerHTML = vlogoHTML(v);
    if (title) title.textContent = (pickEditId ? '编辑' : '添加') + '云端语音';
    if (hint) hint.innerHTML = esc(v.note || '');
    if (modelSel) modelSel.innerHTML = v.models.map(([m, d]) => `<option value="${esc(m)}" ${pickModel === m ? 'selected' : ''}>${esc(m)}${d ? ' — ' + esc(d) : ''}</option>`).join('');
    const t = pickEditId ? cloudProfiles.find(p => p.id === pickEditId) : null;
    if (nameI) nameI.value = t ? (t.name || '') : '';
    if (urlI) urlI.value = t ? (t.api_url || v.api || '') : (v.api || '');
    if (keyI) keyI.value = t ? (t.api_key || '') : '';
    if (refField) refField.style.display = v.clone ? '' : 'none';
  }
  function saveCloudPicker(){
    const v = pickV;
    const nameI = document.getElementById('vcloudPickName');
    const urlI = document.getElementById('vcloudPickUrl');
    const keyI = document.getElementById('vcloudPickKey');
    const refI = document.getElementById('vcloudPickRef');
    const modelSel = document.getElementById('vcloudPickModel');
    const model = (modelSel && modelSel.value) || pickModel;
    const payload = {
      vendor: v.id,
      engine: ENGINE_OF[v.id] || 'gpt-sovits',
      name: (nameI && nameI.value) || model,
      model_name: model,
      api_url: (urlI && urlI.value) || '',
      api_key: (keyI && keyI.value) || ''
    };
    if (v.clone) payload.reference_audio = (refI && refI.value) || '';
    const fn = pickEditId
      ? Backend.saveVoiceProfile(Object.assign({ id: pickEditId }, payload))
      : Backend.saveVoiceProfile(payload);
    fn.then(r => {
      if (r && r.success){ closeModal('vcloudPick'); Backend.toast('云端语音已保存'); loadCloudProfiles(); }
      else Backend.toast(r && r.error ? ('保存失败：' + r.error) : '后端未连接', 'err');
    });
  }

  function openModal(name){ const m = document.getElementById(name + 'Modal'); if (m) m.classList.add('open'); }
  function closeModal(name){ const m = document.getElementById(name + 'Modal'); if (m) m.classList.remove('open'); }

  const cloudAdd = document.getElementById('cloudVoiceAdd');
  if (cloudAdd) cloudAdd.addEventListener('click', () => openCloudPicker(null));
  const cloudBox = document.getElementById('cloudVoiceList');
  if (cloudBox) cloudBox.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-act]'); if (!b) return;
    const id = b.dataset.id; const p = cloudProfiles.find(x => x.id === id); if (!p) return;
    if (b.dataset.act === 'cloud-use'){
      curMode = 'clone'; curSub = 'cloud';
      /* 这是用户主动"设为当前云端语音"，语义明确 → 视为已知真值，允许写回 */
      modeLoaded = true;
      curCloudPayload = { provider: p.vendor, model_name: p.model_name, api_url: p.api_url, api_key: p.api_key,
                          engine: ENGINE_OF[p.vendor] || 'gpt-sovits', name: p.name };
      syncMode(); saveMode(); renderCloudList();
    } else if (b.dataset.act === 'cloud-edit'){ openCloudPicker(id); }
    else if (b.dataset.act === 'cloud-del'){
      let msg = '确定删除云端语音「' + (p.name || p.model_name) + '」？';
      const refBy = (p.referenced_by || []);
      if (refBy.length) msg += '\n\n注意：该音色正被「' + refBy.join('、') + '」引用，删除后这些角色将回退到默认音色。';
      if (await window.uiConfirm(msg, { danger: true, okText: '删除' })){
        Backend.deleteVoiceProfile({ id: id }).then(r => {
          if (r && r.success){
            Backend.toast('已删除');
            if (curCloudPayload && curCloudPayload.provider === p.vendor && curCloudPayload.model_name === p.model_name){ curSub = 'local'; curCloudPayload = null; syncMode(); }
            loadCloudProfiles();
          } else Backend.toast(r && r.error ? ('删除失败：' + r.error) : '后端未连接', 'err');
        });
      }
    }
  });
  const pickChips = document.getElementById('vcloudPickChips');
  if (pickChips) pickChips.addEventListener('click', (e) => {
    const b = e.target.closest('.vc'); if (!b) return;
    pickV = TV(b.dataset.vc); pickModel = pickV.models[0][0]; renderCloudPicker();
  });
  const pickModelSel = document.getElementById('vcloudPickModel');
  if (pickModelSel) pickModelSel.addEventListener('change', () => { pickModel = pickModelSel.value; });
  const pickSave = document.getElementById('vcloudPickSave');
  if (pickSave) pickSave.addEventListener('click', saveCloudPicker);
  document.querySelectorAll('[data-close="vcloudPick"]').forEach(el => el.addEventListener('click', () => closeModal('vcloudPick')));

  /* ---- 语速（本地偏好） ---- */
  const rate = $('#rate'), rateVal = $('#rateVal');
  rate.value = localStorage.getItem('voiceRate') || '1';
  rateVal.textContent = parseFloat(rate.value).toFixed(1) + '×';
  rate.addEventListener('input', () => { rateVal.textContent = parseFloat(rate.value).toFixed(1) + '×'; localStorage.setItem('voiceRate', rate.value); });

  /* ---- 纵向排开的大卡片 / 大板块：点击落点扩散主题色水波纹 ----
         横向紧挨的小卡片不走这条，它们用背景块位移过渡。 */
  if (window.Fx) window.Fx.attachWave('.vcard, .sub');

  if (window.gsap && !reduce) {
    gsap.from('.top', { opacity:0, y:-6, duration:.4, ease:'power2.out' });
    gsap.from('.group', { opacity:0, y:14, duration:.5, delay:.12, ease:'power3.out' });
  }

  /* ---- 通话（仿微信）：通话类型 + 双工引擎 + 设备 + 摄像头预览 ---- */
  const ENGINE_LABEL = { auto:'自动', cloud:'云端双工', local:'本地全双工', local_omni:'本地全双工', off:'关闭' };
  const TTS_LABEL = { mimo_tts:'MiMo-TTS', qwen3tts:'Qwen3-TTS', cosyvoice:'CosyVoice2', gpt_sovits:'GPT-SoVITS', voxcpm:'VoxCPM2' };
  const callEngine = document.getElementById('callEngine');
  const ttsEngine = document.getElementById('ttsEngine');
  const micSel = document.getElementById('micSel');
  const camSel = document.getElementById('camSel');
  const camPreview = document.getElementById('camPreview');
  const camPh = document.getElementById('camPh');
  const camWrap = document.getElementById('camPreviewWrap');
  let _settings = null, _previewTimer = null;

  const VISION_LABEL = { screen:'屏幕（看游戏/桌面）', camera:'摄像头（达妮娅看你）', none:'纯语音（无画面）' };

  function markEl(seg, val){ if(!seg) return; seg.querySelectorAll('[data-m],[data-e],[data-v]').forEach(b => b.classList.toggle('on', b.dataset.m === val || b.dataset.e === val || b.dataset.v === val)); }
  function selEl(seg){ if(!seg) return ''; const b = seg.querySelector('.on'); return b ? (b.dataset.m || b.dataset.e || b.dataset.v || '') : ''; }

  function fillSel(sel, list, current, defLabel){
    if (!sel) return;
    sel.innerHTML = '';
    const o0 = document.createElement('option'); o0.value = 'default'; o0.textContent = defLabel; sel.appendChild(o0);
    (list || []).forEach(d => {
      const o = document.createElement('option');
      o.value = String(d.id != null ? d.id : d.index);
      o.dataset.idx = String(d.index);
      o.textContent = d.name;
      sel.appendChild(o);
    });
    const cur = (current == null ? 'default' : String(current));
    sel.value = cur;
    if (sel.selectedIndex < 0 || sel.value !== cur) {
      const hit = Array.from(sel.options).find(o => o.dataset.idx === cur);
      sel.value = hit ? hit.value : 'default';
    }
  }
  function applySettings(d){
    markEl(callEngine, d.video_engine || d.voice_engine || 'cloud');
    const vs = document.getElementById('visionNow');
    const en = document.getElementById('engineNow');
    if (en) en.textContent = ENGINE_LABEL[d.video_engine || d.voice_engine || 'cloud'] || '云端双工';
    if (vs) vs.textContent = VISION_LABEL[d.vision_source] || '屏幕（看游戏/桌面）';
    fillSel(micSel, d.mic_devices, d.mic_device, '系统默认');
    fillSel(camSel, d.camera_devices, d.camera_device, '系统默认');
    // 预览仅当实际视觉源为摄像头（独立于通话类型）
    if (d.vision_source === 'camera') startPreview(); else stopPreview();
    updateBargeHint(d.video_engine || d.voice_engine || 'cloud');
  }
  function startPreview(){
    stopPreview();
    const dev = (camSel && camSel.value) || 'default';
    _previewTimer = setInterval(async () => {
      try {
        const r = await Backend.cameraFrame(dev);
        if (r && r.success && r.jpeg){ if (camPh) camPh.style.display = 'none'; camPreview.src = 'data:image/jpeg;base64,' + r.jpeg; }
        else { if (camPh) camPh.style.display = 'flex'; }
      } catch (e) { if (camPh) camPh.style.display = 'flex'; }
    }, 250);
  }
  function stopPreview(){
    if (_previewTimer){ clearInterval(_previewTimer); _previewTimer = null; }
    if (camPreview) camPreview.src = '';
    if (camPh) camPh.style.display = 'flex';
  }

  async function loadCallSettings(){
    const d = await Backend.getCallSettings();
    if (!d || !d.success) return;
    _settings = d;
    applySettings(d);
  }
  async function refreshVision(){
    const d = await Backend.getCallSettings();
    if (d && d.success){ _settings = d; applySettings(d); }
  }

  /* 2026-10-08：加空值守卫。原 voice_tier.js 无守卫地调 callEngine.querySelectorAll，
     一旦 #callEngine 被移除/改名即抛 TypeError（设置页审核报告 §五 #1 指出的脆弱点）。
     守卫后该元素可安全移除，且本段行为不变（元素存在时仍照常绑定）。 */
  if (callEngine) callEngine.querySelectorAll('button').forEach(b => b.addEventListener('click', () => {
    const next = b.dataset.e;
    markEl(callEngine, next);
    const en = document.getElementById('engineNow'); if (en) en.textContent = ENGINE_LABEL[next] || next;
    updateBargeHint(next);
    Backend.saveCallSettings({ voice_engine: next, video_engine: next }).then(refreshVision);
  }));

  /* 打断灵敏度（低/中/高）→ 同时写本地+云端两套参数；提示随引擎切换 */
  const bargeSeg = document.getElementById('bargeSeg');
  const BARGE_LABEL = { low:'低 · 难插话', mid:'中 · 默认', high:'高 · 易打断' };
  function updateBargeHint(engine){
    const hint = document.getElementById('bargeHint');
    if (!hint) return;
    if (engine === 'local' || engine === 'local_omni'){
      hint.textContent = '本地双工：她说话时你插话会打断她（闭嘴计数 / 尾静音 / 主动开口）。调高=你一开口她就停。';
    } else {
      hint.textContent = '云端双工（Qwen-Omni）：服务端 VAD 判断你何时开口（VAD 阈值 / 静音时长）。调高=你一开口她就停。';
    }
  }
  if (bargeSeg){
    // 初始化默认值（配置缺省=中）
    bargeSeg.querySelectorAll('button').forEach(x => x.classList.toggle('on', x.dataset.l === 'mid'));
    bargeSeg.querySelectorAll('button').forEach(b => b.addEventListener('click', () => {
      const lvl = b.dataset.l;
      bargeSeg.querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b));
      const bn = document.getElementById('bargeNow'); if (bn) bn.textContent = BARGE_LABEL[lvl] || lvl;
      Backend.setBargeSensitivity(lvl).then(r => {
        if (!r || !r.success) Backend.toast(r && r.error ? ('保存失败：'+r.error) : '后端未连接（仅本地预览）', 'err');
        else Backend.toast('打断灵敏度已设为『' + (BARGE_LABEL[lvl]||lvl) + '』');
      });
    }));
  }
  micSel.addEventListener('change', () => {
    Backend.saveCallSettings({ mic_device: micSel.value }).then(r => {
      if (r && r.success) Backend.toast('麦克风设备已保存');
      else Backend.toast(r && r.error ? ('保存失败：'+r.error) : '后端未连接', 'err');
    });
  });
  if (camSel) camSel.addEventListener('change', () => {
    const dev = camSel.value;
    Backend.saveCallSettings({ camera_device: dev }).then(r => {
      if (r && r.success) Backend.toast('摄像头设备已保存');
      else Backend.toast(r && r.error ? ('保存失败：'+r.error) : '后端未连接', 'err');
    });
    if ((_settings && _settings.vision_source === 'camera')) startPreview();
  });
  if (ttsEngine) ttsEngine.querySelectorAll('button').forEach(b => b.addEventListener('click', () => {
    const next = b.dataset.e;
    const prev = selEl(ttsEngine) || next;
    markEl(ttsEngine, next);
    const tn = document.getElementById('ttsNow'); if (tn) tn.textContent = TTS_LABEL[next] || next;
    Backend.guardSwitch({
      host: ttsEngine,
      title: '无法切换到「'+(TTS_LABEL[next]||next)+'」',
      check: () => Backend.checkCloneEngine(next),
      apply: () => Backend.setCloneEngine(next),
      okMsg: '语音合成模型已切换：'+(TTS_LABEL[next]||next),
      onCancel: () => markEl(ttsEngine, prev),
      onFail: () => markEl(ttsEngine, prev),
      onDone: () => {},
    });
  }));
  loadCallSettings();
  window.addEventListener('pagehide', stopPreview);

})();

