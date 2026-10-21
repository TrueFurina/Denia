// app_backend.js — 设计稿 UI 与 Python 后端(CharacterAPI)的桥接层。
// 通过 window.pywebview.api 调用真实后端；在预览服务器(pywebview 未注入)或缺方法时
// 优雅降级，绝不抛错导致界面白屏。所有方法均返回 Promise<dict>。
// 设计原则：UI 调用 Backend.xxx()，统一用 .success / .offline 判断结果。

const Backend = (() => {
  // pywebview 在窗口 load 后才注入 api，这里每次调用时实时取，避免缓存到 undefined。
  function api() {
    try {
      // 沿帧链一路向上找 pywebview.api（同源可访问）。
      // ⚠️ 2026-10-12 实锤 bug：引导页里的「能力槽页」是**嵌套两层的孙子帧**：
      //     index.html(顶层 · pywebview 只注入这里)
      //       └ first_launch_setup.html(中间帧 · 自己**没有** window.pywebview)
      //           └ ui_redesign_modelapi.html(本页)
      //   旧实现只查 window.parent 一层 → 在中间帧上取不到 pywebview → 本页 api() 恒 null
      //   → call() 等满 whenReady(12s) 后返回 {offline:true} → 页面渲染内置 STATIC 假数据。
      //   用户实测表现：阿里云一键的下拉只有 2/2/1 个假模型、点「刷新」也刷不出真实数据。
      //   现在改为沿 parent 链查到顶层；并额外接受祖先帧上的取 api 钩子 __getOnboardApi
      //   （引导页会挂这个钩子，作为 pywebview 不注入子帧时的可靠兜底）。
      var w = window, i = 0;
      while (w && i++ < 8) {
        try { if (w.pywebview && w.pywebview.api) return w.pywebview.api; } catch (e) {}
        try {
          if (typeof w.__getOnboardApi === 'function') {
            var got = w.__getOnboardApi();
            if (got) return got;
          }
        } catch (e) {}
        var up = null;
        try { up = w.parent; } catch (e) { up = null; }
        if (!up || up === w) break;
        w = up;
      }
      return null;
    } catch (e) { return null; }
  }

  // 等待 pywebview 桥接就绪（与旧版 app.js 的 awaitPywebviewReady 行为一致）。
  // pywebview 在窗口 load 后才注入 window.pywebview.api，若页面/脚本一加载就发请求会踩空。
  // 这里每次调用前先轮询 api 是否存在（最长 ~12s），就绪后再真正发起调用；超时则降级 offline。
  let _readyPromise = null;
  function whenReady(timeoutMs) {
    if (api()) return Promise.resolve(true);
    if (_readyPromise) return _readyPromise;
    _readyPromise = new Promise((resolve) => {
      const t0 = Date.now();
      const tick = () => {
        if (api()) { _readyPromise = null; resolve(true); }
        else if (Date.now() - t0 > (timeoutMs || 12000)) { _readyPromise = null; resolve(false); }
        else setTimeout(tick, 120);
      };
      tick();
    });
    return _readyPromise;
  }

  async function call(method, ...args) {
    await whenReady(12000);
    const a = api();
    if (!a || typeof a[method] !== 'function') {
      // 预览模式 / 后端未就绪：降级，UI 显示「未连接」即可，不报错。
      // stale（2026-10-15）：pywebview 桥在、但没有这个方法 → 后端是改动前启动的
      // 旧进程（改 webapp.py 必须重启才生成新桥），与「浏览器直开的预览模式」
      // 区分开，避免旧进程误报「预览模式」。
      const stale = !!a;
      // 源头脱敏：把内部 token（backend_unavailable:method）换成人话，避免全站 40+ 处
      // toast 在离线时把 'backend_unavailable:xxx' 直接露给用户。stale / offline 布尔保留给逻辑用。
      const msg = stale
        ? '后端未连接：达妮娅后端是旧进程（缺少新接口），请完全退出并重启软件'
        : '后端未连接：请确认达妮娅主程序正在运行';
      return { success: false, offline: true, stale: stale, error: msg };
    }
    try {
      const r = await a[method](...args);
      if (r && typeof r === 'object') return r;
      return { success: true, result: r };
    } catch (e) {
      const msg = (e && (e.message || e.error)) || String(e);
      console.warn('[Backend] ' + method + ' 失败:', msg);
      return { success: false, error: msg };
    }
  }

  function ready() { return !!api(); }

  // 轻量 toast（不依赖任何库），用于保存成功/失败提示。
  function toast(msg, kind) {
    try {
      let t = document.getElementById('appToast');
      if (!t) {
        t = document.createElement('div');
        t.id = 'appToast';
        t.style.cssText = 'position:fixed;left:50%;bottom:28px;transform:translateX(-50%);' +
          'z-index:9999;padding:10px 16px;border-radius:10px;font-size:13px;pointer-events:none;' +
          'background:var(--surface-1);color:var(--ink);box-shadow:var(--shadow-pop);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);' +
          'border:1px solid var(--line-2);opacity:0;transition:opacity .25s,transform .25s;';
        document.body.appendChild(t);
      }
      t.textContent = msg;
      t.style.borderColor = kind === 'err' ? 'color-mix(in srgb, var(--err) 60%, transparent)'
        : (kind === 'warn' ? 'color-mix(in srgb, var(--warn) 60%, transparent)' : 'color-mix(in srgb, var(--accent-1) 55%, transparent)');
      t.style.opacity = '1';
      t.style.transform = 'translateX(-50%) translateY(0)';
      clearTimeout(t._tm);
      t._tm = setTimeout(() => { t.style.opacity = '0'; }, 1700);
    } catch (e) { /* ignore */ }
  }

  /* ============ 主页面：状态 + 对话 ============ */
  const getStatus = () => call('get_runtime_stats');
  const getDuplex = () => call('get_duplex_settings');
  const getCapacity = () => call('get_duplex_capacity');
  const saveDuplex = (data) => call('save_duplex_settings', data);
  /* 首启引导（first_launch_setup）：是否首启 + 完成后落盘 */
  const isFirstRun = () => call('is_first_run');
  const saveFirstRunConfig = (payload) => call('save_first_run_config', payload || {});
  // 统一「功能集」：引导页与设置页共用唯一真相源
  const getCompanionFeatures = () => call('get_companion_features');
  const saveCompanionFeatures = (features) => call('save_companion_features', { features: (features || []) });
  /* 社交渠道（QQ / 微信 / QQ空间 / 桌面媒体摄入）：与「社交渠道」详情页同源 */
  const getSocialChannels = () => call('get_social_channels');
  const saveSocialChannels = (data) => call('save_social_channels', data || {});
  /* 切换前预检：先问后端"目标模式此刻连不连得上"，避免切过去才发现是死的 */
  const checkDuplexTarget = (mode) => call('check_duplex_target', { mode: mode || 'cloud' });
  /* 切换后的后台任务（卸载/加载本地模型）进度轮询 */
  const getSwitchProgress = (taskId) => call('get_switch_progress', taskId || '');
  const getVision = () => call('get_vision_models');
  const getVisionSettings = () => call('get_vision_settings');
  const setVisionEnabled = (on) => call('set_vision_enabled', !!on);
  /* 桌面看屏互动（持续盯桌面）开关——与 setVisionEnabled（视觉感知/能否用视觉模型）分离 */
  const setDesktopWatch = (on) => call('set_desktop_watch_enabled', !!on);
  /* 游戏时云端看屏开关（2026-09-28）：开启后游戏/重负载时，独立 VLM 看屏管线不再节流，
     由云端视觉模型接管持续读屏（语音双工不受影响）。 */
  const setGameCloudWatch = (on) => call('set_game_cloud_watch', !!on);
  /* 开关健康状态（2026-09-29 UX #1）：返回每个开关 {on, effective, blocked_by, reason}，
     用于把"开了却不生效"显式呈现出来（判定集中在后端，前端不得手抄）。 */
  const getSettingsHealth = () => call('get_settings_health');
  /* 【2026-10-19 新增】模型额度 / Key 可用性状态（非打扰式提示）。
     返回 {items:[{kind,model,status,impact,fix}], has_problem, summary}；
     status ∈ ok/quota/auth/rate/error/unconfigured。后端结果缓存 30 分钟。 */
  const getQuotaStatus = (force) => call('get_quota_status', { force: !!force });
  /* 【2026-10-19】取某个 TTS 服务商已绑定的 API Key（引导页克隆弹窗自动拉音色用）。
     Key 不出本机：只在本机 WebView 与后端之间传递。 */
  const getTtsProviderKey = (provider) => call('get_tts_provider_key', { provider: provider || 'dashscope_cosy' });
  /* 能力槽中央配置模型（2026-10-09 重做「模型与 API」）：四槽状态 + 推荐服务商 */
  const getCapabilitySlots = () => call('get_capability_slots');
  const setCapabilitySlot = (data) => call('set_capability_slot', data || {});
  const getVoice = () => call('get_voice_settings');
  const getVoiceMode = () => call('get_voice_mode');
  const setVoiceMode = (mode, cloud) => call('set_voice_mode', mode, cloud || null);
  const setBargeSensitivity = (level) => call('set_barge_sensitivity', level || 'mid');
  const setMicListening = (on) => call('set_mic_listening', !!on);
  const setContinuousVoice = (on) => call('set_continuous_voice', !!on);
  const getDesktopTtsEnabled = () => call('get_desktop_tts_enabled');
  const setDesktopTtsEnabled = (on) => call('set_desktop_tts_enabled', !!on);
  // 全局音色档（voice_profiles）管理（2026-09-13）
  const listVoiceProfiles = () => call('list_voice_profiles');
  const saveVoiceProfile = (data) => call('save_voice_profile', data || {});
  const deleteVoiceProfile = (data) => call('delete_voice_profile', data || {});
  const assignVoiceProfile = (charId, profileId) => call('assign_voice_profile', charId, profileId || '');

  /* 聊天发图：第二参 images（base64 data URL / 纯 base64 列表）必须透传，否则后端收不到图、看图功能静默失效 */
  const sendChat = (text, images) => call('send_chat', text, images);
  const transcribeAndChat = (audioB64, mime) => call('transcribe_and_chat', audioB64, mime || 'pcm');
  const getChat = () => call('get_chat_messages');
  const getTextChatStatus = () => call('get_text_chat_status');
  const setTextChatMode = (mode, manualProvider) => call('set_text_chat_mode', mode, manualProvider || null);
  const clearChat = () => call('clear_chat');
  /* 导出聊天记录（2026-10-21）：后端读 data/chatlog 渲染成 Markdown，写盘到
     桌面「AI桌面伴侣报告」并返回**真实路径**。走后端而不是前端 Blob 下载 ——
     WebView2 里 `a.download` 会被静默拦截（10-08 日报导出踩过：前端弹"已导出"，
     文件根本没落盘）。 */
  const exportChatHistory = (data) => call('export_chat_history', data || {});
  /* 聊天页模型切换器（2026-09-27）：拉百炼免费模型快捷列表 + 点选后改某端口模型并热切换 manual */
  const getChatModelPresets = () => call('get_chat_model_presets');
  const setChatProviderModel = (providerId, model) => call('set_chat_provider_model', providerId, model);
  /* 聊天页模型切换器：视觉推理模型（看屏用）热切换 */
  const switchVisionModel = (modelId) => call('switch_vision_model', modelId || '');
  /* 模型能力：拉取所有模型推断/手填的能力（caps），供选择器悬浮卡 + 设置页能力编辑 */
  const getModelCaps = () => call('get_model_caps');
  /* 模型能力：真实探测（填了 API 后发请求试图片/音频是否被接受）。providerId 或内联 config */
  const probeModelCaps = (providerId, config) => call('probe_model_caps', providerId || '', config || null);

  /* ============ 设置页：感知 / 双工 / 记忆 ============ */
  const getMemory = () => call('get_memory_settings');
  /* 【2026-10-13】轻量记忆条数：右侧状态栏轮询用（只要计数，不要 80 条全文） */
  const getMemoryCount = () => call('get_memory_count');
  const saveMemory = (data) => call('save_memory_settings', data);
  const clearMemory = () => call('clear_memory');
  /* 【2026-10-13】删除单条长期记忆（记忆库列表行的「删除」按钮） */
  const deleteMemory = (memId) => call('delete_memory', String(memId || ''));
  /* 【2026-10-11】手动整段提炼：把整个对话上下文交 LLM 抽出记忆条目（30~37s）。 */
  const synthesizeMemory = () => call('synthesize_memory');
  /* ============ 知识库页（游戏/软件）增删改 ============ */
  const listKnowledge = () => call('list_knowledge');
  const checkKnowledgeDetection = () => call('check_knowledge_detection');
  const saveKnowledge = (data) => call('save_knowledge', data || {});
  const getForegroundExe = () => call('get_foreground_exe');
  const pickExeFile = () => call('pick_exe_file');
  // 注意：Python 端签名是 list_installed_exes(self, query: str)，pywebview 会把参数按位置传给方法，
  // 因此这里必须直接传字符串，不能包成 {query: ...}（否则 Python 收到的是 dict，query.strip() 会抛错）。
  const listInstalledExes = (query) => call('list_installed_exes', query || '');
  const deleteKnowledge = (data) => call('delete_knowledge', data || {});
  const getAiProviders = () => call('get_ai_providers');
  const generateKnowledge = (data) => call('generate_knowledge', data || {});
  const savePerception = (data) => call('save_perception', data || {});
  const getPerception = () => call('get_perception');

  /* ============ 软件关联档案（迈从式绑定） ============ */
  const listSoftwareProfiles = () => call('list_software_profiles');
  const addSoftwareProfile = (data) => call('add_software_profile', data || {});
  const updateSoftwareProfile = (data) => call('update_software_profile', data || {});
  const deleteSoftwareProfile = (data) => call('delete_software_profile', data || {});
  const enumerateProcesses = () => call('enumerate_processes');
  const extractIcon = (data) => call('extract_icon', data || {});
  const getActiveSoftwareProfileOcr = () => call('get_active_software_profile_ocr');
  const saveActiveSoftwareProfileOcr = (data) => call('save_active_software_profile_ocr', data || {});

  /* ============ 语音页：模型下载 ============ */
  const downloadModel = (data) => call('download_voice_model', data || {});
  const modelStatus = (taskId) => call('get_voice_download_status', taskId);
  const getModelCatalog = () => call('get_model_catalog');
  const checkLocalModels = () => call('check_local_models');
  /* 双工量化包下载（MiniCPM-o 4.5 本地模型）：与语音模型下载对称 */
  const downloadModelQuant = (data) => call('download_model_quant', data || {});
  const modelQuantStatus = (taskId) => call('get_model_download_status', taskId);
  /* 下载器控制（2026-10-12 本地模型下载器 UI）：
     stopModelDownload(taskId, keepPartial) —— keepPartial=true 只停进程保留断点（暂停语义）；
     false 终止并清理未完成的临时文件（取消语义）。 */
  const stopModelDownload = (taskId, keepPartial) =>
    call('stop_model_download', String(taskId || ''), keepPartial !== false);
  const deleteModelQuant = (data) => call('delete_model_quant', data || {});
  const modelDirInfo = (path) => call('model_dir_info', path || '');
  /* 下载总览（2026-10-13）：进行中的任务 + 断点残留。
     供「进软件后的全局小窗口」与下载器「切档/重进不丢进度」共用：
     不带参数 = 全局视图；传 {model_dir} = 该目录的断点信息。 */
  const listModelDownloads = (data) => call('list_model_downloads', data || {});
  const setCloneEngine = (engine) => call('set_clone_engine', engine || 'qwen3tts');
  /* 语音通话/视频通话推理模型（云端双工/本地双工/自动/关闭）独立保存 */
  const saveCallInference = (data) => call('save_call_inference', data || {});
  /* 呼叫面板：通话类型 + 麦克风/摄像头设备选择（即时热切换） */
  const getCallSettings = () => call('get_call_settings');
  const saveCallSettings = (data) => call('save_call_settings', data || {});
  /* 电脑控制 / 智能体 设置（2026-09-27） */
  const getAgentSettings = () => call('get_agent_settings');
  const saveAgentSettings = (data) => call('save_agent_settings', data || {});
  /* 电脑操作代理·逐步确认回执：确认框点允许/拒绝后回传后端 */
  const resolveAgentConfirm = (key, allow) => call('resolve_agent_confirm', key, !!allow);
  const cameraFrame = (device) => call('camera_frame', { device: (device === undefined ? 'default' : device) });
  /* 【2026-10-21 补·漏导出】引导页视频屏（first_launch_setup.html 的 enterVideo）用
     `if (B && B.listCameras)` 守卫取设备列表 —— 适配层从没导出过它，守卫恒 false，
     于是摄像头下拉框**永远只有「系统默认设备」一项**，用户以为电脑没摄像头。
     后端 list_cameras 一直在（还带 current 回显），只是没人接上。 */
  const listCameras = () => call('list_cameras');
  const setCameraDevice = (data) => call('set_camera_device', data || {});
  /* 语音/视频双工通话：按需拉起/释放本地双工引擎（懒启动下点击通话即连、挂断即退）。 */
  const startDuplexCall = (data) => call('start_duplex_call', data || {});
  const endDuplexCall = (data) => call('end_duplex_call', data || {});
  // 取消拨号：与挂断区别在**不写通话记录**（走 end_duplex_call 会落一条 0 秒假记录）
  const cancelDuplexCall = (data) => call('cancel_duplex_call', data || {});
  /* 通话中热切换屏幕共享（视频双工专属）：开→视觉源切屏幕、音频仍麦克风 */
  const setCallScreenShare = (data) => call('set_call_screen_share', data || {});
  /* 通话状态轮询（前端拨号振铃态 / 屏幕共享等待重连用）。
     注：此前 app_backend 漏暴露该方法，导致 Backend.getCallStatus 为 undefined →
     屏幕共享热切换的 waitDuplexReadySince 永远拿不到状态 → 共享开关失败。 */
  const getCallStatus = (data) => call('get_call_status', data || {});
  /* 通话字幕源（取代前端刮聊天 DOM）：返回通话起点后双方文本（her=双工日志+聊天ai；me=聊天user），
     按 ts 排序带稳定 seq，前端按 seq 增量取，切页/最小化不丢不重。 */
  const getCallSubtitles = (data) => call('get_call_subtitles', data || {});
  /* 通话中控制：麦克风禁音 / 免提(关AI出声) / 摄像头开合。
     注：此前 app_backend 漏暴露这三个方法，导致前端 toggleMute/toggleAiAudio/toggleCamera
     里的 Backend.setCallMic/setCallAiAudio/setCallCamera 为 undefined → 控制永远打不到后端。 */
  const setCallMic = (data) => call('set_call_mic', data || {});
  const setCallAiAudio = (data) => call('set_call_ai_audio', data || {});
  const setCallCamera = (data) => call('set_call_camera', data || {});
  /* 通话麦克风实时电平（律动条）：只读轻量轮询（~100ms/次），数据来自采集回调的平滑 RMS。
     返回 {success,in_call,active,muted,level(0..1),rms,voice,peak,ts}，UI 据此画律动条。 */
  const getCallMicLevel = (data) => call('get_call_mic_level', data || {});
  /* 通话记录（2026-10-02 新增·独立于聊天页）：双方文本 + 达妮娅语音回放 URL。
     返回 {success, records:[{id,speaker:'user'|'assistant',text,audio_url,engine,ts,char_name}], in_call} */
  const getCallRecords = (data) => call('get_call_records', data || {});
  /* 清空通话记录（「通话记录」页的清空按钮） */
  const clearCallRecords = (data) => call('clear_call_records', data || {});
  /* 通话记录·会话列表（按时间间隙聚合为「一次通话」），每项含时间/主题/角色名 */
  const getCallSessions = (data) => call('get_call_sessions', data || {});
  /* 某次通话会话的详情（双方逐句文本 + 语音 URL） */
  const getCallSessionDetail = (data) => call('get_call_session_detail', data || {});
  /* 导出某次通话会话为 zip（transcript.txt + 每句 wav），返回 base64 供前端下载 */
  const exportCallSession = (data) => call('export_call_session', data || {});
  const openExportFolder = (data) => call('open_export_folder', data || {});
  const mergeCallAudio = (data) => call('merge_call_audio_only', data || {});
  /* 混合模式（2026-10-02）：云端 Qwen-Omni 出语音 + 本地 Comni 看屏（两条并行）。
     Comni 本身带视觉，混合就该让它参与看屏。返回 {success, enabled, bystander_running} */
  const getMixedMode = () => call('get_mixed_mode');
  const setMixedMode = (enabled) => call('set_mixed_mode', { enabled: !!enabled });
  /* 【2026-10-08 P1·原子化】一次点击完成「双工引擎 + 解耦 + 混合开关 + 视觉管线」，
     后端只在最后写一次盘，中途失败回滚内存态。替代前端串 3 次 RPC 的旧写法。
     入参 {enabled, decouple}。返回 {success, enabled, atomic, steps}。 */
  const setMixedModeAtomic = (enabled) => call('set_mixed_mode', { enabled: !!enabled, atomic: true });
  /* 弹窗选择记忆（2026-10-02）：查看/重置各场景"上次选了什么"。
     返回 {success, items:[{key,choice,desc}]}；reset 传 key='' 清空全部。 */
  const getNotifyChoices = () => call('get_notify_choices');
  const resetNotifyChoices = (key) => call('reset_notify_choices', { key: key || '' });
  /* 通话界面设备菜单：悬停麦克风/免提按钮展开时，一次取回输入+输出设备与当前选择。
     返回 {success, mic_devices[], speaker_devices[], mic_current, speaker_current}。 */
  const listAudioDevices = (data) => call('list_audio_devices', data || {});
  /* 通话内热切换设备：{kind:'mic'|'speaker', device:'default'|设备名}（写盘并即时生效） */
  const setCallAudioDevice = (data) => call('set_call_audio_device', data || {});
  /* 通话音视频追踪：前端媒体流事件（getDisplayMedia 授权/被拒、track.onended、各开关切换）
     持久化到后端 data/call_av_log.jsonl。失败静默降级（预览模式/未连接不影响体验）。 */
  const logCallAv = (data) => call('log_call_av', data || {});
  /* 聊天页通话按钮档位门控：拉取当前档位是否允许双工通话；点锁死按钮时弹右上角提醒 */
  const getTierCallStatus = () => call('get_tier_call_status');
  const notifyTierCallBlocked = () => call('notify_tier_call_blocked');
  /* 档位×功能 门槛矩阵（后端 mode_gates 为单一真相源）：
     getModeGateTable 拿整张表（渲染「哪些能用·哪些被卡+为什么+怎么解」）；
     checkModeGate 单查一个功能，被点前先问「我能用吗」，blocked 时 combined 可直接 toast。 */
  const getModeGateTable = () => call('get_mode_gate_table');
  const checkModeGate = (feature) => call('check_mode_gate', { feature: feature || '' });
  const notifyModeGate = (payload) => call('notify_mode_gate', payload || {});
  /* 切换前预检：目标克隆引擎的模型是否已下载就绪 */
  const checkCloneEngine = (engine) => call('check_clone_engine_target', { engine: engine || 'qwen3tts' });
  const pickAudioFile = () => call('pick_audio_file');
  const recordAudio = (type, sec) => call('record_audio_reference', type || 'reference', sec || 2.5);
  const testVoice = (cfg, text) => call('test_voice', cfg || {}, text || '');
  /* 试听任务轮询：test_voice 返回 task_id 后，前端轮询真实进度（修「假加载」UI） */
  const getVoiceTestStatus = (taskId) => call('get_voice_test_status', taskId || '');
const getVoiceCapability = (charId) => call('get_voice_capability', charId || '');
const previewCloudVoice = (text, charId) => call('preview_cloud_voice', text || '', charId || '');
/* 角色卡「出声音色」统一选择器（2026-09-25）：按 context 查本地双工出声用的云端 TTS / 云端双工 S2S 音色清单；
   为选中的云端 TTS 音色找/建一条 voice_profile（Key 在服务端取，绝不下发前端）。 */
const listCloudVoices = (opts) => call('list_cloud_voices', opts || {});
const ensureCloudVoiceProfile = (data) => call('ensure_cloud_voice_profile', data || {});
/* 角色卡模式真相源：读 proactive.duplex_mode，返回 mode_label/banner/recommended_kind/needs_ref_audio，
   供引导页与软件内角色编辑器按本地双工/云端双工自适应并显示「为什么不一样」。 */
const getRoleVoiceContext = () => call('get_role_voice_context', {});
const getOnboardingDefaults = (p) => call('get_onboarding_defaults', p || {});
  /* 角色「AI 生成人设」：由角色名(+可选提示)生成 性格特征/说话风格/背景设定 三段式。
     未绑 LLM 且非云端双工时调用方应先弹提醒（get_ai_providers 会标 cloud_llm）。 */
  const generateCharacter = (data) => call('generate_character', data || {});

  /* ============ 角色页 ============ */
  const getCharacters = () => call('get_characters');
  /* ============ 角色图片主题（2026-10-05，见 _design.txt） ============ */
  const tList = () => call('t_list');
  const tSave = (data) => call('t_save', data || {});
  const tDelete = (data) => call('t_delete', data || {});
  const tRename = (data) => call('t_rename', data || {});
  const tBind = (data) => call('t_bind', data || {});
  const tSlotFiles = (data) => call('t_slot_files', data || {});
  const tPick = (data) => call('t_pick', data || {});
  const switchCharacter = (id) => call('switch_character', id).then((r) => {
    /* ⭐ 换角色 = 换它绑定的那套主题。以前只切后端 active，没人重跑
       ThemeManager.applyForActive() ⇒ hero / 头像 / 立绘还停在上一个角色的
       （用户报「换角色后头像没跟着变」）。这里在切换成功后立即重应用，
       并广播 charswitched 让各页按需刷新。 */
    try {
      if (r && r.success !== false) {
        const M = window.ThemeManager;
        if (M && typeof M.applyForActive === 'function') {
          Promise.resolve(M.applyForActive()).then(() => {
            try { window.dispatchEvent(new CustomEvent('charswitched', { detail: { id: id } })); } catch (e) {}
          }).catch(() => {});
        }
        /* 【2026-10-11 换角色·聊天页隔离】清空聊天视图并重拉新角色会话。
           后端 switch_character 已 clear _chat_buffer + 置 _chat_history_loaded=False，
           但前端 rendered/DOM 仍留着旧角色的行（切角色多在角色页触发、#scroll 不在场，
           pollChat 的对账删除被跳过 ⇒ 切回聊天页新旧混排）。这里强制重置视图。
           经 window.__chat 调用（resetChatView 定义在另一个 IIFE 作用域内）。 */
        try {
          const C = window.__chat;
          if (C && typeof C.resetChatView === 'function') C.resetChatView();
          if (typeof window.pollChat === 'function') window.pollChat();
        } catch (e) { /* 视图重置失败绝不阻断角色切换 */ }
      }
    } catch (e) { /* 主题重应用失败绝不阻断角色切换 */ }
    return r;
  });
  const saveCharacter = (data) => call('save_character', data);
  const deleteCharacter = (id) => call('delete_character', id);
  const getTemplates = () => call('get_character_templates');

  /* ============ 日报 / 周报 ============ */
  const getReports = () => call('get_activity_reports');
  const getReport = (kind, key) => call('get_report_markdown', kind, key);

  /* ============ 开发者页 ============ */
  const getVisionDebug = () => call('get_vision_debug');
  /* ===== 表情包管理（2026-10-13 接入）=====
     后端方法名见 webapp.py CharacterAPI 的 sticker* 一族，**同名转发**即可
     （本页是新增功能，不套用旧的 snake_case 历史约定，避免两边对不上）。
     所有调用都兜底成 {ok:false,...}，后端未接时页面显示提示而不是白屏。 */
  const stickerSelfcheck   = ()                    => call('stickerSelfcheck');
  const stickerPacks       = ()                    => call('stickerPacks');
  const stickerCreatePack  = (name)                => call('stickerCreatePack', name || '');
  const stickerRenamePack  = (pid, name)           => call('stickerRenamePack', pid || '', name || '');
  const stickerDeletePack  = (pid)                 => call('stickerDeletePack', pid || '');
  const stickerBindings    = (pid, mode, chars)    => call('stickerBindings', pid || '', mode || 'none', chars || []);
  const stickerMemes       = (pid)                 => call('stickerMemes', pid || '');
  const stickerImageUrl    = (pid, mid)            => call('stickerImageUrl', pid || '', mid || '');
  const stickerDeleteMeme  = (pid, mid)            => call('stickerDeleteMeme', pid || '', mid || '');
  const stickerVocab       = ()                    => call('stickerVocab');
  const stickerUpdateLabels= (pid, mid, fields)    => call('stickerUpdateLabels', pid || '', mid || '', fields || {});
  const stickerAutotagOne  = (pid, mid, force)     => call('stickerAutotagOne', pid || '', mid || '', !!force);
  const stickerAutotagPack = (pid, limit, miss)    => call('stickerAutotagPack', pid || '', limit || 30, miss !== false);
  const stickerAutotagProgress = ()                => call('stickerAutotagProgress');
  const stickerCharacters  = ()                    => call('stickerCharacters');
  const stickerMarketplace = ()                    => call('stickerMarketplace');
  const stickerInstall     = (entry)               => call('stickerInstall', entry || {});
  const stickerInstallStart= (entry, limit)        => call('stickerInstallStart', entry || {}, limit || 120);
  const stickerInstallProgress = (taskId)          => call('stickerInstallProgress', taskId || '');
  const stickerInstallCancel = (taskId, keep)      => call('stickerInstallCancel', taskId || '', keep !== false);
  const stickerInstallContinue = (taskId)          => call('stickerInstallContinue', taskId || '');
  const stickerPick        = (character, conv)     => call('stickerPick', character || '', conv || []);
  const stickerUpload      = (pid, filename, b64)  => call('stickerUpload', pid || '', filename || '', b64 || '');
  /* 【2026-10-18 补接线】设置页「打开状态文件」按钮调的是 Backend.open_data_file
     （snake_case，页面 5845 行就这么写的，且带 typeof 守卫）。后端
     CharacterAPI.open_data_file 早就实现了（含防路径穿越），但适配层**从来没导出过**
     → 守卫恒为 false → 按钮永远走"只 toast 一个路径"的降级分支，
     用户点了不会打开资源管理器。这里按页面已有的名字同名转发，不改页面。 */
  const open_data_file = (filename) => call('open_data_file', filename || '');
  // 【2026-10-01 D】记忆性能观测仪快照（开发者页面板轮询）
  const getMemoryProfile = () => call('get_memory_profile');
  const startOcrCalibration = () => call('start_ocr_calibration');
  const stopOcrCalibration = () => call('stop_ocr_calibration');
  const startChatOverlay = () => call('start_chat_overlay');
  const stopChatOverlay = () => call('stop_chat_overlay');
  const getPrivacyMode = () => call('get_privacy_mode');
  const togglePrivacyMode = () => call('toggle_privacy_mode');
  const getRuntime = () => call('get_runtime_stats');
  const getLlm = () => call('get_llm_settings');
  const saveLlm = (data) => call('save_llm_settings', data);
  /* 多厂商纯文本聊天配置（text_chat.providers）：设置·模型与 API 列表共用 */
  const saveTextChatProviders = (data) => call('save_text_chat_providers', data || {});
  /* 【2026-10-08 P1·消除全量覆盖】按 id 的增量写：
     · upsertTextChatProvider({provider:{…}}) —— 只改目标那一条，未给的字段（尤其
       api_key）原样保留；id 不存在则追加。不再需要 __KEEP__ 哨兵。
     · deleteTextChatProvider(id) —— 只删目标那一条；删的是当前 manual 端口时后端自动回落。
     老接口 saveTextChatProviders 保留不动（引导页等仍在用）。 */
  const upsertTextChatProvider = (data) => call('upsert_text_chat_provider', data || {});
  const deleteTextChatProvider = (id) => call('delete_text_chat_provider', id || '');
  /* 手动停止本地 TTS 服务进程（释放常驻的显存/内存） */
  const stopLocalTts = (engine) => call('stop_local_tts_engine', engine || '');
  /* 手动填写卡片：一次性校验 base_url / Key / 模型名（拉 GET {base_url}/models） */
  const validateLlmEndpoint = (data) => call('validate_llm_endpoint', data || {});
  /* 查询阿里云百炼账号下全部克隆音色（voice_id），供一键回填 */
  const listBailianVoices = (apiKey) => call('list_bailian_voices', apiKey || '');
  const testLlm = (provider, data) => call('test_llm_connection', provider || '', data || null);
  /* 设置·我的模型配置：测试某个已存端口（按 id）的连通性，复用 test_llm_connection 探测 */
  const testTextChatProvider = (id) => call('test_text_chat_provider', {id: id || ''});
  const getGpuVram = () => call('get_gpu_vram');
  // 【2026-09-20】开发者面板「显存占比」实时快照（1s 轮询）
  const getVramLive = () => call('get_vram_live');
  /* 陪你看视频·诊断（2026-10-01）：依赖可用性 + 开关 + 最近观看记录，开发者页排查用 */
  const getVideoWatchDiag = () => call('get_video_watch_diag');
  /* 陪你看视频·实测（2026-10-02）：输入链接真跑一遍 下载→抽帧→VLM看画面，
     分阶段回报卡在哪一环。返回 {success,url,stages:[{stage,ok,elapsed,info}],conclusion,elapsed}
     ⚠️ 后端是 async 方法。个别 pywebview 版本对 async js_api 的返回不 await，会把
     coroutine 对象直接丢回前端（报 "Object of type coroutine is not JSON serializable"）。
     故这里显式 Promise.resolve 包一层：无论后端返回 dict 还是 coroutine，都收敛成可 await 的值。 */
  const testVideoLink = (url) => Promise.resolve(call('test_video_link', { url: url || '' }))
    .catch(e => ({ success: false, error: String((e && e.message) || e) }));
  /* 聊天拖入本地视频 → 陪你看（2026-10-13）：后端抽帧+VLM 看画面，进度走
     VIDEO_WATCH_PROGRESS 上同一张 UI 卡；看完自动以画面内容驱动一条聊天回复。 */
  const watchLocalVideo = (d) => Promise.resolve(call('watch_local_video', d || {}))
    .catch(e => ({ success: false, error: String((e && e.message) || e) }));
  const getCompanionReadiness = () => call('get_companion_readiness');
  const getVoiceDownloadStatus = (taskId) => call('get_voice_download_status', taskId || '');
  /* 本地 TTS 显存评估 + 已有模型目录认领（引导页本地模型卡） */
  const getVoiceVramAdvice = () => call('get_voice_vram_advice');
  const scanTtsModelDir = (d) => call('scan_tts_model_dir', d || {});
  const setTtsModelDir = (d) => call('set_tts_model_dir', d || {});
  const listCloneEngines = () => call('list_clone_engines');
  /* 模型目录自定义：自选盘符/路径 + 认领已有模型 */
  const getModelDirOptions = () => call('get_model_dir_options');
  const scanModelDir = (dir) => call('scan_model_dir', { dir: dir || '' });
  const setModelDir = (dir, quantId) => call('set_model_dir', { dir: dir || '', quant_id: quantId || '' });
  const pickModelDir = () => call('pick_model_dir');
  /* 本地模型暖机：进主页/引导完成后按所选模式自动挂载模型 */
  const warmLocalModel = () => call('warm_local_model');
  const getLocalModelState = () => call('get_local_model_state');
  /* 新人引导链路（Apply 阶段真正生效） */
  const getOnboardingEnv = () => call('get_onboarding_env');
  const ensureComniLayout = (d) => call('ensure_comni_layout', d || {});
  const listMicDevices = () => call('list_mic_devices');
  const testMicDevice = (d) => call('test_mic_device', d || {});
  const setMicDevice = (d) => call('set_mic_device', d || {});
  /* 【2026-10-17】麦克风验证结果轮询（切设备后后端会开 3 秒检测窗口） */
  const getMicVerifyResult = (d) => call('get_mic_verify_result', d || {});
  /* 【2026-10-17 开发者页·音频自测台】麦克风实时电平 + 指定设备播放测试音
     （listAudioDevices / setCallAudioDevice 本文件 288/290 行已有，勿重复声明） */
  const devMicLevel = (d) => call('dev_mic_level', d || {});
  const devPlayTone = (d) => call('dev_play_tone', d || {});
  const pickReferenceAudio = () => call('pick_reference_audio');
  /* 角色卡头图（2026-09-29） */
  const pickCharacterBanner = () => call('pick_character_banner');
  /* 角色头像（2026-10-11）：1:1 方图，角色卡 + 聊天页 AI 头像共用 */
  const pickCharacterAvatar = () => call('pick_character_avatar');
  const setReferenceAudio = (d) => call('set_reference_audio', d || {});
  const applyOnboardingVoice = (d) => call('apply_onboarding_voice', d || {});
  const getModelBundleStatus = (d) => call('get_model_bundle_status', d || {});
  const downloadModelBundle = (d) => call('download_model_bundle', d || {});
  const validateOnboarding = (d) => call('validate_onboarding', d || {});
  const saveCustomConfig = (d) => call('save_custom_config', d || {});
  /* Comni 引擎自动获取（修「分发需自备 Comni」缺陷） */
  const getComniEngineStatus = () => call('get_comni_engine_status');
  const downloadComniEngine = (d) => call('download_comni_engine', d || {});
  const getComniEngineDownloadStatus = () => call('get_comni_engine_download_status');
  /* 百炼控制台「应用内嵌浏览器」（2026-09-29）：打开内嵌窗 / 系统浏览器兜底 / 状态轮询 */
  const openBailianConsole = (urlKey) => call('open_bailian_console', urlKey || 'free_quota');
  const openBailianConsoleExternal = (urlKey) => call('open_bailian_console_external', urlKey || 'free_quota');
  const getBailianConsoleStatus = () => call('get_bailian_console_status');
  /* Step2：注入脚本从控制台页抓回的真实额度快照 */
  const getBailianRealQuota = () => call('get_bailian_real_quota');

  /* ============ 切换预检守卫（2026-08-31） ============
     切双工模式 / 切语音克隆引擎前，先问后端"目标此刻连不连得上"：
       连不上 → 弹窗拦一道（用户仍可强行继续，不硬拦）
       连得上 → 正常切
     全程用贴在卡片下沿的进度条给反馈，保存成功后走到 100% 再收起。
     后端没连上或接口缺失时不拦截（本地预览场景下不能把人卡死）。 */
  const _esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
    (m) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));

  function _progEl(host){
    if (!host) return null;
    host.classList.add('sw-host');
    let p = host.querySelector('.sw-prog');
    if (!p){
      p = document.createElement('div');
      p.className = 'sw-prog';
      p.innerHTML = '<i></i>';
      host.appendChild(p);
    }
    return p;
  }
  function _progSet(p, pct, indet){
    if (!p) return;
    p.classList.add('on');
    p.classList.toggle('indet', !!indet);
    const bar = p.querySelector('i');
    if (bar && !indet) bar.style.width = Math.max(0, Math.min(100, pct)) + '%';
  }
  function _progHide(p, delay){
    if (!p) return;
    setTimeout(() => { p.classList.remove('on', 'indet'); }, delay || 0);
  }
  function _confirmRisk(o){
    return new Promise((resolve) => {
      const mask = document.createElement('div');
      mask.className = 'sw-mask';
      mask.innerHTML =
        '<div class="sw-modal" role="dialog" aria-modal="true">' +
          '<h3><span class="sw-ico">!</span>' + _esc(o.title || '目标当前不可用') + '</h3>' +
          '<p>' + _esc(o.message || '') + '</p>' +
          (o.detail ? '<div class="sw-detail">' + _esc(o.detail) + '</div>' : '') +
          (o.hint ? '<p class="sw-hint">' + _esc(o.hint) + '</p>' : '') +
          '<div class="sw-btns">' +
            '<button class="sw-cancel">' + _esc(o.cancelText || '取消切换') + '</button>' +
            '<button class="sw-ok">' + _esc(o.okText || '仍要切换') + '</button>' +
          '</div>' +
        '</div>';
      const close = (v) => {
        mask.classList.remove('on');
        setTimeout(() => mask.remove(), 220);
        resolve(v);
      };
      mask.querySelector('.sw-cancel').addEventListener('click', () => close(false));
      mask.querySelector('.sw-ok').addEventListener('click', () => close(true));
      mask.addEventListener('click', (e) => { if (e.target === mask) close(false); });
      document.body.appendChild(mask);
      requestAnimationFrame(() => mask.classList.add('on'));
    });
  }

  /* agent 逐步确认请求（后端 agent_confirm_broker 经 _wv2_broadcast 推送）：
     {"type":"agent_confirm","key":<hex>,"desc":<str>}。redesign 此前漏接此分支，
     导致对话触发的 agent 需用户确认时确认框永不弹出 → 60s 超时 fail-closed → 操作被拒。
     ✅ 2026-10-08 修复：复用 sw-mask/sw-modal 弹窗，点击允许/拒绝后回传 resolveAgentConfirm。 */
  function showAgentConfirm(key, desc){
    return new Promise((resolve) => {
      const mask = document.createElement('div');
      mask.className = 'sw-mask';
      mask.innerHTML =
        '<div class="sw-modal" role="dialog" aria-modal="true">' +
          '<h3><span class="sw-ico">⚙</span>需要你确认</h3>' +
          '<p>' + _esc(desc || 'AI 助手请求执行一个操作，需要你的许可。') + '</p>' +
          '<p class="sw-hint">60 秒内未选择将自动拒绝（安全默认）。</p>' +
          '<div class="sw-btns">' +
            '<button class="sw-cancel">' + _esc('拒绝') + '</button>' +
            '<button class="sw-ok">' + _esc('允许') + '</button>' +
          '</div>' +
        '</div>';
      const done = (allow) => {
        mask.classList.remove('on');
        setTimeout(() => mask.remove(), 220);
        try { resolveAgentConfirm(key, allow); } catch (_) {}
        resolve(allow);
      };
      mask.querySelector('.sw-cancel').addEventListener('click', () => done(false));
      mask.querySelector('.sw-ok').addEventListener('click', () => done(true));
      mask.addEventListener('click', (e) => { if (e.target === mask) done(false); });
      document.body.appendChild(mask);
      requestAnimationFrame(() => mask.classList.add('on'));
    });
  }

  /* opt: { host, title, check(), apply(), okMsg, onDone(), onFail(), onCancel() } */
  async function guardSwitch(opt){
    const p = _progEl(opt.host);
    _progSet(p, 0, true);                       // 预检中：不确定进度
    let chk = null;
    try { chk = await (opt.check ? opt.check() : null); } catch (_) { chk = null; }
    const known = !!(chk && chk.success);

    if (known && chk.available === false){
      _progHide(p, 0);                          // 等用户决策，别一直转
      const go = await _confirmRisk({
        title: opt.title || '目标当前不可用',
        message: (chk.detail || '目标模式当前无法连通') + '，切换后可能无法正常工作。',
        detail: chk.reason ? ('判定：' + chk.reason) : '',
        hint: chk.hint || '',
      });
      if (!go){ if (opt.onCancel) opt.onCancel(); return { success: false, cancelled: true }; }
    }
    _progSet(p, 15, false);                     // 预检通过/用户坚持 → 进入切换
    if (known && chk.need_pull && chk.hint) toast(chk.hint);   // 提前告知"要拉模型，会慢"

    // 切换期间进度条缓慢爬升到 90% 封顶等结果——真实进度拿不到（拉本地模型
    // 可能要 1~3 分钟），渐进爬升比干停在某个百分比更像"在干活"。
    let climb = null, v = 15;
    const stopClimb = () => { if (climb){ clearInterval(climb); climb = null; } };
    climb = setInterval(() => { v = Math.min(90, v + (90 - v) * 0.12 + 1.5); _progSet(p, v, false); }, 320);

    let res = null;
    try { res = await opt.apply(); } catch (e) { res = { success: false, error: String(e) }; }
    stopClimb();
    if (!res || !res.success){
      _progHide(p, 0);
      toast(res && res.error ? ('切换失败：' + res.error) : '后端未连接（仅本地预览）', 'err');
      if (opt.onFail) opt.onFail(res);
      return res || { success: false };
    }

    // 切换后若带后台任务（卸载/加载本地模型，耗时可达数分钟）→ 轮询真实进度，
    // 用后端给的百分比驱动进度条，而不是前面那个"假爬升"。
    let finalMsg = opt.okMsg || '已切换';
    const tid = res.switch_task_id;
    if (tid){
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      let failed = false;
      for (let i = 0; i < 400; i++){            // 上限约 5 分钟
        let pg = null;
        try { pg = await getSwitchProgress(tid); } catch (_) { pg = null; }
        if (pg && pg.success){
          _progSet(p, pg.pct || 0, false);
          if (pg.done){
            if (pg.state === 'failed'){ failed = true; if (pg.message) toast(pg.message, 'err'); }
            else if (pg.message) finalMsg = pg.message;
            break;
          }
        }
        await sleep(800);
      }
      if (failed && opt.onFail) opt.onFail(res);
    }
    _progSet(p, 100, false);                    // 收尾走满
    _progHide(p, 750);
    toast(finalMsg);
    if (opt.onDone) opt.onDone(res);
    return res;
  }

  /* 云端双工容量徽标：设置 / 运行模式 / 开发者三页共用同一份格式化逻辑 */
  function capacityBadge(c){
    if (!c || c.success === false) return { text: '容量未知 · 后端未连接', cls: 'cap-unknown' };
    if (c.local_omni_active) return { text: '本地双工 · 零云占用（不占云端端口）', cls: 'cap-local' };
    if (!c.reachable) return { text: '云端网关不可达 · 容量未知', cls: 'cap-unknown' };
    const total = c.total_workers || 0, idle = c.idle_workers || 0, q = c.queue_length || 0;
    if (c.is_full) return { text: '云端双工已满 · 排队 ' + q + ' 人（共 ' + total + ' 端口）', cls: 'cap-full' };
    return { text: '云端双工 ' + idle + '/' + total + ' 空闲 · 排队 ' + q + ' 人', cls: 'cap-ok' };
  }

  // —— UI 心跳（2026-09-03 卡死排查）：前端每 5s 调后端 ui_ping() ——
  // 若前端渲染进程挂死，JS 定时器停摆 → 后端心跳中断 → 打 WARNING。
  // call() 已自带 offline 降级（预览/未连接时静默返回），不会报错刷屏。
  const uiPing = () => call('ui_ping');
  // UI 健康诊断快照（开发者页健康面板每 1s 调用）：GUI 活性 / 前端心跳 / WebView2 窗口数 / 最近发送 / 冻结现场
  const getUiHealth = () => call('get_ui_health');

  /* —— 关于（设置页「关于」分类）：版本号 + 开机自启（2026-10-21 补出口）——
     后端 get_version / get_autostart / set_autostart 早就写好了，适配层一直没导出，
     于是界面上没有任何入口。tools/check_api_wiring.py（反向接线闸门）就是为这种
     「有实现、没出口」加的。无参方法不能塞 {}（见 call() 上方注释）。 */
  const getVersion = () => call('get_version');
  const getAutostart = () => call('get_autostart');
  const setAutostart = (on) => call('set_autostart', !!on);

  /* ============ 开发者页：OCR 分块调优 + 文字锚点 ============ */
  /* 读取复用已有 getVisionDebug()（其 d.ocr 含 regions / region_texts /
     anchors / player_name / anchor_hit_*），这里只暴露「写」接口。 */
  const ocrAddRegion = (name, purpose) => call('ocr_add_region', name, purpose || 'general');
  const ocrRemoveRegion = (name) => call('ocr_remove_region', name);
  const ocrSetRegionPurpose = (name, purpose) => call('ocr_set_region_purpose', name, purpose);
  const ocrSetRegionRect = (name, rect) => call('ocr_set_region_rect', name, rect);
  const ocrSetPlayerName = (name) => call('ocr_set_player_name', name);
  const ocrSetAnchors = (anchors) => call('ocr_set_anchors', anchors);

  return {
    call, ready, toast, capacityBadge,
    getStatus, getDuplex, getCapacity, saveDuplex, checkDuplexTarget, getSwitchProgress, getVision, getVisionSettings, setVisionEnabled, setDesktopWatch, setGameCloudWatch, getSettingsHealth,
    /* 【2026-10-19】额度/Key 可用性状态（非打扰提示用）。同样必须进导出对象，
       否则页面里 Backend.getQuotaStatus 为 undefined → 角标永远不显示。 */
    getQuotaStatus,
    getTtsProviderKey,
    /* 能力槽中央配置（2026-10-09 重做「模型与 API」）——此前只在上面写了 const 定义、
       漏进这个导出对象，导致能力槽页/引导页的 hasB() 恒为 false → 页面永远显示
       「后端未连接，仅显示布局预览」、点「启用」静默无效。补进导出即修复。 */
    getCapabilitySlots, setCapabilitySlot,
    /* 退出程序 / 最小化到托盘（外壳左上角「退出」按钮用；quit_app 会真正 destroy 全部窗口） */
    quitApp: () => call('quit_app'),
    minimizeToTray: () => call('minimize_to_tray'),
    /* 其余"前端在调、历史上漏导出"的桥（后端方法一直存在，属同类漏导出 bug）：
       · switchVisionModel  → 外壳模型切换菜单(index.html) + 本文件 1400 行内部自用
                              （那句 typeof Backend.switchVisionModel==='function' 因漏导出恒 false
                               → 视觉模型切换静默失效）；const 早已定义在 137 行，这里补进导出。
       · exportMarkdown     → 日报/记忆页「导出」按钮（未加守卫，漏了点击即 TypeError）。
       · startSimplexServer → 外壳单工服务自动拉起（有守卫，漏了只是不自动拉起）。
       · test_model_slot / get_model_diagnose → 设置页旧「模型槽」诊断（该 tier 已下线，兜底保留）。 */
    switchVisionModel,
    exportMarkdown: (data) => call('export_markdown', data || {}),
    startSimplexServer: (data) => call('start_simplex_server', data || {}),
    test_model_slot: (data) => call('test_model_slot', data || {}),
    get_model_diagnose: () => call('get_model_diagnose'),
    uiPing, getUiHealth,
    isFirstRun, saveFirstRunConfig,
    getCompanionFeatures, saveCompanionFeatures,
    getSocialChannels, saveSocialChannels,
    getVoice, getVoiceMode, setVoiceMode, setBargeSensitivity, setMicListening, setContinuousVoice, getDesktopTtsEnabled, setDesktopTtsEnabled,
    sendChat, transcribeAndChat, getChat, getTextChatStatus, setTextChatMode, clearChat,
    exportChatHistory,
    getChatModelPresets, setChatProviderModel, getModelCaps, probeModelCaps, getModelCaps,
    get_model_slots: () => call('get_model_slots'),
    set_model_slot: (data) => call('set_model_slot', data),
    /* 气泡侧用的是驼峰名；两个名字都给上，避免"前端驼峰/后端下划线"对不上
       —— 曾只导出下划线版，气泡里 typeof B.setModelSlot==='undefined' → 点了没反应。 */
    setModelSlot: (data) => call('set_model_slot', data),
    getMemory, getMemoryCount, saveMemory, clearMemory, deleteMemory, synthesizeMemory, listKnowledge, checkKnowledgeDetection, saveKnowledge, deleteKnowledge, getForegroundExe, pickExeFile, listInstalledExes, enumerateProcesses, getAiProviders, generateKnowledge, savePerception, getPerception,
    downloadModel, modelStatus, getModelCatalog, checkLocalModels, setCloneEngine, checkCloneEngine, pickAudioFile, recordAudio, testVoice, getVoiceTestStatus,
    downloadModelQuant, modelQuantStatus, stopModelDownload, deleteModelQuant, modelDirInfo, listModelDownloads,
    getCharacters, switchCharacter, saveCharacter, deleteCharacter, getTemplates,
    getReports, getReport,
    getVisionDebug, startOcrCalibration, stopOcrCalibration,
    ocrAddRegion, ocrRemoveRegion, ocrSetRegionPurpose, ocrSetRegionRect, ocrSetPlayerName, ocrSetAnchors,
    startChatOverlay, stopChatOverlay, getPrivacyMode, togglePrivacyMode,
    getRuntime, getLlm, saveLlm, saveTextChatProviders, upsertTextChatProvider, deleteTextChatProvider, validateLlmEndpoint, listBailianVoices, stopLocalTts, testLlm, testTextChatProvider, getGpuVram, getCompanionReadiness, getVoiceDownloadStatus, getVoiceVramAdvice, scanTtsModelDir, setTtsModelDir, listCloneEngines,
    getVramLive,
    getMemoryProfile,
    getVideoWatchDiag, testVideoLink, watchLocalVideo,
    getModelDirOptions, scanModelDir, setModelDir, pickModelDir, warmLocalModel, getLocalModelState,
    getOnboardingEnv, ensureComniLayout, listMicDevices, testMicDevice, setMicDevice,
    devMicLevel, devPlayTone,
    pickReferenceAudio, setReferenceAudio, applyOnboardingVoice, getModelBundleStatus,
    /* 统一声音绑定（2026-09-25）：能力探针 + 云端试听 + 云端音色清单/建档 */
    getVoiceCapability, previewCloudVoice, listCloudVoices, ensureCloudVoiceProfile,
    getRoleVoiceContext, generateCharacter, getOnboardingDefaults,
    /* 音色档（voice_profiles）——定义在本文件 106-109 行，必须导出，否则页面调用即 TypeError */
    listVoiceProfiles, saveVoiceProfile, deleteVoiceProfile, assignVoiceProfile,
    downloadModelBundle, validateOnboarding, saveCustomConfig,
    /* 角色卡头图（2026-09-29）：弹系统文件框选图 + 复制进 data/char_banners */
    pickCharacterBanner,
    /* 角色头像（2026-10-11）：弹系统文件框选 1:1 方图 + 复制进 data/char_avatars */
    pickCharacterAvatar,
    /* 角色图片主题（2026-10-05）：主题包 CRUD + 双向绑定 + 图位落盘。
       后端方法名 t_*（见 webapp.py CharacterAPI），存储在 src/ui/theme_store.py。 */
    tList, tSave, tDelete, tRename, tBind, tSlotFiles, tPick,
    getComniEngineStatus, downloadComniEngine, getComniEngineDownloadStatus,
    openBailianConsole, openBailianConsoleExternal, getBailianConsoleStatus, getBailianRealQuota,
    guardSwitch,
    saveCallInference,
    getCallSettings, saveCallSettings, cameraFrame, listCameras, setCameraDevice,
    startDuplexCall, endDuplexCall, cancelDuplexCall, setCallScreenShare,
    getCallStatus, getCallSubtitles, setCallMic, setCallAiAudio, setCallCamera, getCallMicLevel, logCallAv, getTierCallStatus, notifyTierCallBlocked,
    getModeGateTable, checkModeGate, notifyModeGate,
    getCallRecords, clearCallRecords,
    getCallSessions, getCallSessionDetail, exportCallSession, openExportFolder, mergeCallAudio,
    getMixedMode, setMixedMode, setMixedModeAtomic,
    getNotifyChoices, resetNotifyChoices,
    listAudioDevices, setCallAudioDevice,
    getAgentSettings, saveAgentSettings, resolveAgentConfirm,
    /* 软件档案 */
    listSoftwareProfiles, addSoftwareProfile, updateSoftwareProfile, deleteSoftwareProfile,
    enumerateProcesses, extractIcon, getActiveSoftwareProfileOcr, saveActiveSoftwareProfileOcr,
    /* 表情包管理（2026-10-13 接入）：薄转发到 src/ui/sticker_bridge.py
       —— 后端方法名见 webapp.py CharacterAPI 的 sticker* 一族。 */
    stickerSelfcheck, stickerPacks, stickerCreatePack, stickerRenamePack, stickerDeletePack,
    stickerBindings, stickerMemes, stickerImageUrl, stickerDeleteMeme, stickerVocab,
    stickerUpdateLabels, stickerAutotagOne, stickerAutotagPack,
    stickerAutotagProgress, stickerCharacters,
    stickerMarketplace, stickerInstall, stickerInstallStart, stickerInstallProgress,
    stickerPick, stickerUpload,
    open_data_file,
    /* 【2026-10-17】麦克风验证结果轮询（切设备后后端开 3 秒检测窗口） */
    getMicVerifyResult,
    /* 关于页：版本号 + 开机自启 */
    getVersion, getAutostart, setAutostart,
  };
})();

window.Backend = Backend;
// 注意：以「经典脚本」方式加载（<script src=...>，非 module），
// 保证在页面解析期即同步执行、早于各页内联脚本，使 window.Backend 全局可用。
// 故此处不保留 export（export 仅对 module 有效，且会令经典脚本语法报错）。

/* UI 心跳上报：每 5s 调一次后端 ui_ping()，供后端看门狗判定前端是否卡死。
   包 try/catch 是因为预览模式/后端未就绪时 call() 可能抛或返回 offline，均忽略。 */
setInterval(() => { try { Backend.uiPing(); } catch (e) {} }, 5000);

/* ============================================================
   主题（深浅模式 + 主题色）
   经典脚本、早于各页内联脚本执行 → 首屏就完成配色，不会闪一下默认色。
   ============================================================ */
const Theme = (() => {
  const root = document.documentElement;
  const mqLight = window.matchMedia ? window.matchMedia('(prefers-color-scheme: light)') : null;
  /* localStorage 在个别环境（file:// 限制）会抛异常，包一层避免拖垮整份脚本 */
  function ls(k, v){
    try { return (v === undefined) ? localStorage.getItem(k) : localStorage.setItem(k, v); }
    catch (e) { return null; }
  }
  function mode(){ return ls('themeMode') || 'system'; }
  function accent(){ return ls('accent') || 'aurora'; }
  function resolved(){
    const t = mode();
    if (t === 'light' || t === 'dark') return t;
    return (mqLight && mqLight.matches) ? 'light' : 'dark';
  }
  function applyTheme(){ root.setAttribute('data-theme', resolved()); }
  function applyAccent(){
    const a = accent();
    if (a === 'custom'){
      root.removeAttribute('data-accent');
      root.style.setProperty('--accent-1', ls('accent1') || '#2DD4FF');
      root.style.setProperty('--accent-2', ls('accent2') || '#A06BFF');
    } else {
      /* 关键：切回预设色之前，必须清掉「自定义」写进来的内联变量。
         内联样式优先级高于 [data-accent="x"] 规则，不清就永远盖住预设色
         —— 这正是「点过一次应用自定义渐变后，预设主题色全失效」的根因。 */
      root.style.removeProperty('--accent-1');
      root.style.removeProperty('--accent-2');
      if (a) root.setAttribute('data-accent', a);
      else root.removeAttribute('data-accent');
    }
  }
  function init(){ applyTheme(); applyAccent(); }
  function setTheme(m){ ls('themeMode', m); applyTheme(); }
  function setAccent(v){ ls('accent', v); applyAccent(); }
  function setCustom(c1, c2){
    ls('accent', 'custom'); ls('accent1', c1); ls('accent2', c2); applyAccent();
  }
  /* 选「跟随系统」时，系统切深浅要实时跟上 */
  if (mqLight && mqLight.addEventListener){
    mqLight.addEventListener('change', () => { if (mode() === 'system') applyTheme(); });
  }
  return { init, applyTheme, applyAccent, setTheme, setAccent, setCustom, mode, accent };
})();
window.Theme = Theme;
Theme.init();   /* 首屏立即应用，避免闪色 */

/* ============================================================
   桌宠 / 运行模式面板：常驻实时刷新（与当前栏目无关）
   背景：SPA 只换 .app > .main，右侧 .stage（达妮娅桌宠）常驻不重载。
   对话引擎弹窗原本内联在 .stage、由聊天页脚本重跑，每次切栏会被 SPA 用
   new Function 重跑 → 重复绑定 / 错位。故弹窗改为本函数动态创建于 body、
   只绑定一次（见下方「对话引擎」段），彻底脱离聊天页脚本。此处只负责
   桌宠实时刷新 + #modeBtn 副标题同步，整页加载只跑一次。
   ============================================================ */
function startPetLive(){
  if (window.__petLiveStarted) return;
  window.__petLiveStarted = true;

  const ENGINE_TXT = { cloud: '云端', local: '本地', off: '已关闭', auto: '自动' };
  function engineLabel(dup){
    if (dup === 'auto') return '自动 · 系统选择';
    if (dup === 'local' || dup === 'local_omni') return '本地双工';
    if (dup === 'cloud') return '云端双工';
    if (dup === 'off') return '双工已关闭';
    return dup;
  }
  function applyStatus(d){
    if (!d) return;
    const dup = (d && d.engine) || '';
    // 【2026-10-08 修复】引擎为空/未知时不再兜底成"云端双工"，显示"按档位"避免误导
    const runTxt = (!dup) ? '按档位'
                 : (dup === 'local' || dup === 'local_omni') ? '本地双工'
                 : (dup === 'cloud') ? '云端双工'
                 : (dup === 'off') ? '纯文本聊天'
                 : (dup === 'auto') ? '自动（按档位）' : (ENGINE_TXT[dup] || dup);
    const rm = document.getElementById('runModeVal'); if (rm) rm.textContent = runTxt;
    const subEl = document.querySelector('.top .sub');
    if (subEl) subEl.textContent = 'SESSION · ' + runTxt;
    const sub2 = document.getElementById('modeBtnSub');
    if (sub2) sub2.textContent = engineLabel(dup);
  }
  async function refresh(){
    try {
      const [dup, vis] = await Promise.all([Backend.getDuplex(), Backend.getVisionSettings()]);
      applyStatus(dup && dup.success ? dup : null);
      const vel = document.getElementById('visVal');
      const on = !!(vis && vis.success && vis.enabled);
      if (vel){ vel.textContent = on ? (vis.active ? '读取中' : '已开启') : '已关闭'; vel.className = 'badge ' + (on ? 'on' : 'off'); vel.title = on ? '开启后快照与双工读取屏幕' : '视觉感知已关闭'; }
    } catch(e){}
    /* 云端双工容量徽标 */
    try {
      if (typeof Backend.getCapacity === 'function'){
        const c = await Backend.getCapacity();
        const el = document.getElementById('modeCap');
        if (el){ const b = Backend.capacityBadge(c); el.textContent = b.text; el.className = 'cap-badge ' + b.cls + ' msec-cap'; }
      }
    } catch(e){}
    /* 本会话消耗：仅当当前页有对话气泡时重算；切到其他页保留上一次数值，不归零 */
    try {
      if (document.querySelectorAll('.row').length){
        let tok = 0, tts = 0;
        document.querySelectorAll('.row:not(.me) .bub').forEach(b => { const t = b.textContent; tok += Math.max(1, Math.ceil([...t].length / 1.6)); tts += [...t].length; });
        const tk = document.getElementById('tok'); if (tk) tk.textContent = tok.toLocaleString();
        const tt = document.getElementById('tts'); if (tt) tt.innerHTML = tts + '<em>字</em>';
        const rn = document.getElementById('rnd'); if (rn) rn.textContent = document.querySelectorAll('.row.me').length;
      }
    } catch(e){}
    /* 【2026-10-13】记忆库条数：右栏此前是**写死的 1,240 条 / 1.2k**，与记忆页
       真实统计（如 5 条）差两个数量级 —— 同一屏两个数字自相矛盾。
       现在按真实计数绘制；读不到就显示 "—"，绝不继续展示假数字。 */
    try {
      if (typeof Backend.getMemoryCount === 'function'){
        const m = await Backend.getMemoryCount();
        const cnt = (m && m.success && m.count != null) ? Number(m.count) : null;
        const b = document.getElementById('memVal');
        if (b) b.innerHTML = (cnt == null) ? '—' : (cnt.toLocaleString() + ' <em>条</em>');
        const ro = document.getElementById('memRo');
        if (ro) ro.textContent = (cnt == null) ? '—'
          : (cnt >= 1000 ? (cnt / 1000).toFixed(1).replace(/\.0$/, '') + 'k' : String(cnt));
      }
    } catch(e){}
  }
  /* —— 对话引擎：WorkBuddy 式居中弹窗（由本函数动态创建于 document.body，整页只绑定一次）——
     彻底脱离聊天页内联脚本：聊天页内联脚本每次切栏会被 SPA 用 new Function 重跑，
     旧的内联 #enginePop 躲不开这条重跑路径，正是"切设置再切回就错位"的隐患。
     现在弹窗与绑定都挂在 body 级、只跑一次，SPA 切栏（只换 .main）完全碰不到它。
       交互：自动开关（#engAutoSw）→ 开启时下方选项灰显且不可点；关闭时可选云端双工 / 本地双工。 */
  const modeBtn = document.getElementById('modeBtn');
  if (modeBtn && !window.__engModalBound){
    window.__engModalBound = true;

    const mask = document.createElement('div');
    mask.className = 'eng-modal-mask';
    mask.id = 'engModalMask';
    mask.innerHTML =
      '<div class="eng-modal" role="dialog" aria-modal="true" aria-label="对话引擎">' +
        '<div class="eng-modal-head"><span>对话引擎</span>' +
          '<button class="x" id="engModalClose" type="button" aria-label="关闭">' + DIcon.svg('x') + '</button></div>' +
        '<div class="eng-modal-body">' +
          '<div class="eng-auto-row">' +
            '<div class="eng-row-t"><div class="er-t">自动切换</div>' +
              '<div class="er-s">系统按场景选：空闲本地双工，游戏 / 重载时切云端</div></div>' +
            '<span class="eng-switch" id="engAutoSw" role="switch" aria-checked="true" tabindex="0"></span>' +
          '</div>' +
          '<div class="eng-divider"></div>' +
          '<div class="eng-opts" id="engOpts">' +
            '<button class="eng-opt" type="button" data-mode="cloud"><div class="eo-t"><b>云端双工</b>' +
              '<span class="eo-sub">M2 / M3 · 实时推流，游戏首选</span></div><span class="pick"></span></button>' +
            '<button class="eng-opt" type="button" data-mode="local"><div class="eo-t"><b>本地双工</b>' +
              '<span class="eo-sub">local_omni · 零云费 / 永不断线</span></div><span class="pick"></span></button>' +
          '</div>' +
          '<div class="eng-note" id="engNote"></div>' +
        '</div>' +
        '<div class="eng-modal-foot"><div class="sw-host" id="engProgHost"></div></div>' +
      '</div>';
    document.body.appendChild(mask);
    const modal = mask.querySelector('.eng-modal');
    const autoSw = document.getElementById('engAutoSw');
    const engOpts = document.getElementById('engOpts');
    const engNote = document.getElementById('engNote');
    const engProgHost = document.getElementById('engProgHost');
    let curEngine = 'auto';

    function syncUI(engine){
      curEngine = engine;
      // UI「本地双工」= Comni(local_omni)；运行态可能是 "local_omni"，需映射到 data-mode="local" 高亮
      const disp = (engine === 'local_omni') ? 'local' : engine;
      const auto = (engine === 'auto');
      if (autoSw){ autoSw.classList.toggle('on', auto); autoSw.setAttribute('aria-checked', String(auto)); }
      if (engOpts) engOpts.querySelectorAll('.eng-opt').forEach(o => {
        const m = o.dataset.mode;
        o.classList.toggle('sel', !auto && m === disp);
        o.classList.toggle('dis', auto);   /* auto 开启 → 灰显 + 不可点 */
      });
      if (engNote) engNote.textContent = auto
        ? '系统将按场景自动选择：空闲本地双工，游戏 / 重载时切云端。'
        : '手动模式：你选择的引擎固定生效（游戏中仍会自动切云端以保证流畅）。';
    }
    function setEngine(mode){
      if (mode === 'auto'){
        Backend.saveDuplex({ engine: 'auto' }).then(r => {
          if (r && r.success){ syncUI('auto'); Backend.toast('已设为自动切换'); }
          else Backend.toast(r && r.error ? ('保存失败：' + r.error) : '后端未连接（仅本地预览）', 'err');
        });
        return;
      }
      /* 手动选具体引擎：走 guardSwitch 预检 + 进度条（与设置页同套稳健逻辑） */
      Backend.guardSwitch({
        host: engProgHost,
        title: '无法切换到「' + (mode === 'cloud' ? '云端双工' : '本地双工') + '」',
        check: () => Backend.checkDuplexTarget(mode),
        apply: () => Backend.saveDuplex({ engine: mode }),
        okMsg: '引擎已切换：' + (mode === 'cloud' ? '云端双工' : '本地双工'),
        onCancel: () => syncUI(curEngine),
        onFail: () => syncUI(curEngine),
      });
    }
    function openPop(){
      Backend.getDuplex().then(d => {
        const e = (d && d.success && d.engine) ? d.engine : 'auto';
        syncUI(e === 'off' ? 'off' : e);
      });
      mask.classList.add('on');   /* 入场靠 CSS 过渡（无 gsap/opacity:0，绝不卡透明态） */
    }
    /* 【2026-10-08·P2 修「全字段覆盖」泄漏】
       旧实现在**页面加载时**读一次 getDuplex()，然后把那一刻的快照当成"当前值"，
       保存时把 5 个字段**全部**发回去。于是：
         · 若读回时 `prefer_cloud` 实际是 false，`chk('cdnPreferCloud', true)` 会
           拿"元素不存在"当例外 → 返回默认 true → 把用户的 false 写成 true；
         · 设置页早就按用户裁决**移除了**这三个 checkbox，所以 `getElementById` 必然
           是 null → 每次点保存都把 prefer_cloud/local_fallback/video_enabled 重置为 true。
       修法：保存前**现读现写**（read-modify-write），只发生用户本次真正改动的字段。
       代价是保存从"一次异步调用"变成"读一次 + 写一次"，但换来的是不会静默篡改
       用户没碰过的开关 —— 与 save_duplex_settings 后端的 `"x" in data` 字段级 merge 对齐。 */
    function saveCloudDuplexNow(){
      const val = (id) => { const el = document.getElementById(id); return el ? (el.value || '').trim() : null; };
      return Backend.getDuplex().then(d => {
        const cd = (d && d.success && d.cloud_duplex) || {};
        const payload = {};
        /* 端点：元素存在 → 以输入框为准；元素不在 → 根本不发这个键（后端保留原值）。 */
        const ve = val('cdnVoiceEndpoint'); if (ve !== null) payload.voice_endpoint = ve;
        const de = val('cdnVideoEndpoint'); if (de !== null) payload.video_endpoint = de;
        /* 三个开关：设置页已不暴露（用户裁决移除）。它们**不进 payload**，
           后端 "x" in data 为假 → 保留用户既有配置。这里显式注释，防止将来有人
           "顺手补回来"又踩同一个坑。 */
        return Backend.saveDuplex(payload);
      });
    }
    window.saveCloudDuplex = function () {
      saveCloudDuplexNow().then(r => {
        const el = document.getElementById('cdnSaveResult');
        if (el) el.textContent = (r && r.success) ? ' 已保存（立即生效）' : (' ' + ((r && r.error) || '保存失败'));
      }).catch(() => {
        const el = document.getElementById('cdnSaveResult');
        if (el) el.textContent = ' 保存失败（后端未连接）';
      });
    };
    function closePop(){ mask.classList.remove('on'); }

    /* 【2026-10-16 修复·初始状态会停在 HTML 写死的 "on"】
       旧实现只在这里 **一次性** 读 getDuplex()：桥未就绪 / 读失败时 syncUI 不会被调用，
       于是开关保留 innerHTML 里写死的 class="eng-switch on" + aria-checked="true" ——
       实测出现「开关显示开，而配置 duplex_mode='realtime'（手动云端）」这种界面骗人。
       改为带回退重试的拉取，确保最终一定与后端对齐。 */
    (function pullEngineState(tries){
      const retry = () => { if (tries < 12) setTimeout(() => pullEngineState(tries + 1), 500); };
      try {
        Backend.getDuplex().then(d => {
          if (d && d.success){ const e = d.engine || 'auto'; syncUI(e === 'off' ? 'off' : e); }
          else retry();
        }).catch(retry);
      } catch (e) { retry(); }
    })(0);
    modeBtn.addEventListener('click', openPop);
    document.getElementById('engModalClose').addEventListener('click', closePop);
    mask.addEventListener('click', (e) => { if (e.target === mask) closePop(); });   /* 点遮罩关闭 */
    if (autoSw){
      autoSw.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' '){ e.preventDefault(); autoSw.click(); } });
      autoSw.addEventListener('click', () => {
        const next = !autoSw.classList.contains('on');
        /* 关闭自动时落到当前手动引擎（若是 auto 则默认本地）；开启自动立即保存 */
        const target = next ? 'auto' : (curEngine === 'auto' ? 'local' : curEngine);
        syncUI(target);
        /* 【2026-10-16 修复·"关掉自动切换"永远不落盘】旧实现只有 `if (next) setEngine('auto')`，
           于是**关闭方向**只改界面、不写配置：用户以为关掉了，后端 duplex_mode 仍是 auto，
           下次打开又"自己变回开"。现在**开/关都落盘**（关闭时落到上面算出的 target 手动引擎）。 */
        setEngine(target);
      });
    }
    if (engOpts) engOpts.querySelectorAll('.eng-opt').forEach(o => {
      o.addEventListener('click', () => { if (o.classList.contains('dis')) return; setEngine(o.dataset.mode); });
    });
  }
  refresh();
  setInterval(refresh, 2000);                 /* 桌宠常驻，任意栏目下都实时刷新 */
  setInterval(() => { try { if (typeof Backend.getCapacity !== 'function') return; Backend.getCapacity().then(c => { const el = document.getElementById('modeCap'); if (el){ const b = Backend.capacityBadge(c); el.textContent = b.text; el.className = 'cap-badge ' + b.cls + ' msec-cap'; } }); } catch(e){} }, 15000);
  document.addEventListener('morph-ready', () => { refresh(); });   /* 切栏即时刷一次 */
}
startPetLive();

/* ============================================================
   聊天面板：常驻实时刷新（与当前栏目无关）
   背景：聊天页（首页）内联脚本里的 pollChat / pollChatLock 定时器在 SPA 切栏或
   事件循环被双工/GPU 负载拖垮时会停摆，导致：
     ① 双工开口消息拉不到 → UI 不显示（用户感知"打字后双工没了"）；
     ② 输入框锁状态冻在某一刻 → 残留锁（某次瞬态端口冷却置 false 后再也不恢复）。
   与 startPetLive 同思路：把聊天轮询搬到外壳层、整页只跑一次、永不死，并挂到
   window.pollChat / window.pollChatLock，使后端 _wv2_broadcast({type:'chat_push'})
   的即时刷新监听（index.html 里）能直接驱动 → 双工开口即时上屏、锁状态实时反映、
   端口恢复即自动解锁。
   ============================================================ */
function startChatLive(){
  try {
    if (window.__chatLiveStarted) return;
    window.__chatLiveStarted = true;

    /* 全局已渲染 ID 集合：即使有旧版内联脚本残留（浏览器缓存）导致双重轮询，
       同一条消息也只会出现一次（DOM 级去重兜底 Map 去重）。 */
    const rendered = new Map();
    /* 【2026-10-08 治"看倒数第二条被拽到底 + 自动滚动卡顿"】
       钉底锁 chatPinned：用户手动上翻即解除钉底，新消息只在钉底态才平滑贴底。
       用 rAF 合并多次滚动请求，避免流式期每轮 poll / chat_push 反复重启 smooth 动画。 */
    let chatPinned = true;
    let chatPinRAF = 0;
    function ensureChatPin(scroll){
      if (!scroll || scroll.__pinBound) return;
      scroll.__pinBound = true;
      scroll.addEventListener('scroll', function(){
        const dist = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight;
        chatPinned = dist < 24;           // 离底超 24px 即解除钉底；回到底部 24px 内重新钉住（小阈值，避免"滚到倒数第二条仍被拽回底"）
      }, { passive: true });
    }
    function stickToBottom(scroll){
      if (!chatPinned) return;            // 用户在上翻看历史 → 不打扰
      if (scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 4) return; // 已在底部，跳过避免重启动画
      if (chatPinRAF) return;             // rAF 合并，不叠加 smooth
      chatPinRAF = requestAnimationFrame(function(){
        chatPinRAF = 0;
        const REDUCE = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
        try { scroll.scrollTo({ top: scroll.scrollHeight, behavior: REDUCE ? 'auto' : 'smooth' }); }
        catch (e) { scroll.scrollTop = scroll.scrollHeight; }
      });
    }
    /* 聊天消息时间：用真实 m.ts（后端 Unix 秒）算相对时间，且每次渲染都刷新——
       修「时间永远是『刚刚』」的假固定 bug。⭐ 名字回退统一走 window.__charName
       （由 applyCharacterName 在角色切换时刷新），不再写死『达妮娅』。 */
    function fmtAgo(ts){
      if (ts == null || ts === '') return '刚刚';
      var d = new Date(Number(ts) * 1000);
      if (isNaN(d.getTime())) return '刚刚';
      var now = Date.now();
      var diff = Math.floor((now - d.getTime()) / 1000);
      if (diff < 0) diff = 0;
      if (diff < 10) return '刚刚';
      if (diff < 60) return diff + '秒前';
      if (diff < 3600) return Math.floor(diff / 60) + '分钟前';
      var hh = ('0' + d.getHours()).slice(-2), mm = ('0' + d.getMinutes()).slice(-2);
      var today = new Date(now);
      if (diff < 86400 && d.getFullYear() === today.getFullYear() && d.getMonth() === today.getMonth() && d.getDate() === today.getDate()) return hh + ':' + mm;
      var mo = ('0' + (d.getMonth() + 1)).slice(-2), da = ('0' + d.getDate()).slice(-2);
      return mo + '-' + da + ' ' + hh + ':' + mm;
    }
    function liveName(fallbackName){ return window.__charName || fallbackName || '达妮娅'; }
    /* 用户自己发了消息 → 贴底并重钉：让 chatPinned 复位，后续 AI 回复能自动跟随可见。
       供 index.html 的 send() 调用（发完立即滚到底，且不破坏"阅读历史时不上翻"的钉底锁）。 */
    function scrollToBottom(){
      const sc = document.getElementById('scroll');
      if (!sc) return;
      chatPinned = true;
      try { sc.scrollTop = sc.scrollHeight; } catch (e) {}
    }
    /* 用户消息图片（2026-10-03）：气泡文字下方的缩略图条。
       - 乐观阶段：send() 传入 dataURL 列表并打 dataset.keep 标记（后端确认前不拆）；
       - 后端轮询回来带 /local_img URL → 按内容替换（dataURL 换成落盘 URL）；
       - 后端没带 images（落盘全失败/旧后端）→ 保留乐观图，不闪没。 */
  /* 【2026-10-05 治"图片撑破聊天页"】用户在聊天里发图后，整张原图按**原始像素**
     渲染（实测一张 143KB 的图直接盖住半个对话区、把气泡挤出可视范围）。
     根因：`syncMsgImgs` 建出的 <img> 没有任何尺寸约束，而全项目 CSS 里
     `.msg-imgs img` 规则数 = 0（三个网页文件实测均为 0 条）。
     这里补一条"缩略图"样式：限制最大宽高 + object-fit:cover，点击仍走原图预览。 */
  var MSG_IMG_CSS = [
    '.msg-imgs{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px;max-width:100%;}',
    '.msg-imgs.solo{margin-top:0;}',
    '.msg-imgs img{display:block;width:168px;max-width:100%;height:auto;',
    'max-height:196px;object-fit:cover;border-radius:10px;cursor:zoom-in;',
    'border:1px solid var(--line);background:var(--surface-2);}',
    '.msg-imgs img:hover{filter:brightness(1.04);}',
  ].join('');

  function syncMsgImgs(bub, imgs){
    if (!bub) return;
    try { __daniInjectStyle('msgImgStyle', MSG_IMG_CSS); } catch (e) {}
    let wrap = bub.querySelector('.msg-imgs');
    const want = (Array.isArray(imgs) && imgs.length) ? JSON.stringify(imgs) : '';
    if (!want){
      if (wrap && !wrap.dataset.keep) wrap.remove();
      return;
    }
      if (wrap && wrap.dataset.srcs === want) { wrap.classList.toggle('solo', !(bub.textContent || '').trim()); return; }
      if (wrap && wrap.children.length === imgs.length){
        /* 数量一致 → 原地换 src（乐观 dataURL → 后端落盘 URL），不重建 DOM、不重播入场动画 */
        wrap.dataset.srcs = want; delete wrap.dataset.keep;
        wrap.classList.toggle('solo', !(bub.textContent || '').trim());
        for (let _i = 0; _i < imgs.length; _i++){ if (wrap.children[_i].src !== imgs[_i]) wrap.children[_i].src = imgs[_i]; }
        return;
      }
      if (!wrap){ wrap = document.createElement('div'); wrap.className = 'msg-imgs'; bub.appendChild(wrap); }
      wrap.dataset.srcs = want; delete wrap.dataset.keep;
      wrap.classList.toggle('solo', !(bub.textContent || '').trim());
      wrap.innerHTML = '';
      imgs.forEach(function(u, i){
        const im = document.createElement('img');
        im.src = u; im.loading = 'lazy'; im.alt = '图片';
        im.style.setProperty('--i', i);
        im.addEventListener('click', function(){ try { window.open(u, '_blank'); } catch(e){} });
        wrap.appendChild(im);
      });
    }
    function attachOptimisticImages(row, imgs){
      if (!row || !Array.isArray(imgs) || !imgs.length) return;
      const bub = row.querySelector('.bub'); if (!bub) return;
      syncMsgImgs(bub, imgs);
      const wrap = bub.querySelector('.msg-imgs');
      if (wrap) wrap.dataset.keep = '1';
    }
    /* ============ 好感度「关系思考」面板（2026-10-10） ============
       每条 AI 回复下方一个可折叠块（默认收起，仿 DeepSeek「深度思考」）。
       数据来自后端 get_chat_messages 给该条消息挂的 m.rel（只读快照，无模型调用）。
       样式**自带作用域前缀 relthink**，不写进共享 CSS，避免跨页串味。 */
    function _relEnsureCss(){
      if (document.getElementById('relThinkCss')) return;
      var st = document.createElement('style');
      st.id = 'relThinkCss';
      st.textContent = [
        '.relthink{margin:8px 0 2px;border:1px solid var(--line,rgba(140,150,170,.28));',
        'border-radius:var(--r-md,12px);background:var(--surface,rgba(127,140,170,.06));',
        'overflow:hidden;font-size:12px;max-width:100%;box-sizing:border-box}',
        '.relthink-head{display:flex;align-items:center;gap:8px;width:100%;padding:7px 12px;',
        'background:transparent;border:0;cursor:pointer;color:var(--ink-2,#8b95ab);',
        'font:inherit;text-align:left}',
        '.relthink-head:hover{background:var(--surface-2,rgba(127,140,170,.10))}',
        '.relthink-title{font-weight:600;color:var(--ink-3,#7b869b)}',
        '.relthink-chip{padding:1px 8px;border-radius:999px;font-weight:600;',
        'background:var(--accent-soft-1,rgba(45,212,255,.14));color:var(--accent-1,#2dd4ff)}',
        '.relthink-caret{margin-left:auto;transition:transform .18s ease;opacity:.7;font-size:10px}',
        '.relthink.open .relthink-caret{transform:rotate(180deg)}',
        '.relthink-body{display:none;padding:2px 12px 11px}',
        '.relthink.open .relthink-body{display:block}',
        '.relthink-sec{margin-top:9px}',
        '.relthink-k{color:var(--ink-3,#7b869b);margin-bottom:5px;font-weight:600}',
        '.relthink-flip{font-weight:600;color:var(--ink-2,#9aa4b8)}',
        '.relthink-rows{display:grid;grid-template-columns:auto 1fr auto;gap:5px 9px;align-items:center}',
        '.relthink-name{color:var(--ink-3,#7b869b);white-space:nowrap}',
        '.relthink-bar{height:6px;border-radius:999px;background:var(--surface-3,rgba(127,140,170,.16));overflow:hidden}',
        '.relthink-bar>i{display:block;height:100%;border-radius:999px;',
        'background:linear-gradient(90deg,var(--accent-1,#2dd4ff),var(--accent-2,#a06bff))}',
        '.relthink-val{font-variant-numeric:tabular-nums;color:var(--ink-2,#9aa4b8)}',
        '.relthink-sig{display:flex;flex-wrap:wrap;gap:6px}',
        '.relthink-tag{padding:1px 8px;border-radius:999px;border:1px solid var(--line,rgba(140,150,170,.28));',
        'color:var(--ink-2,#8b95ab);font-variant-numeric:tabular-nums}',
        '.relthink-tag.hot{color:var(--accent-1,#2dd4ff);border-color:rgba(45,212,255,.4)}',
        '.relthink-tag.bad{color:var(--err,#ff6363);border-color:rgba(255,99,99,.4)}',
        '.relthink-note{margin-top:9px;color:var(--ink-4,#6b7484);line-height:1.55}'
      ].join('');
      (document.head || document.documentElement).appendChild(st);
    }
    function _relEsc(s){
      return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){
        return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];
      });
    }
    function _relClamp(v){ v = Number(v) || 0; return v < 0 ? 0 : (v > 100 ? 100 : v); }
    function relThinkHTML(rel){
      var sig = rel.signals || {}, mood = rel.mood || {}, dims = rel.dims || {};
      var h = '';
      /* 心情（有变化就显示 前→后） */
      var flip = (sig.mood_before && sig.mood_after && sig.mood_before !== sig.mood_after)
        ? _relEsc(sig.mood_before) + ' → ' + _relEsc(sig.mood_after)
        : _relEsc(mood.label || '平静');
      h += '<div class="relthink-sec"><div class="relthink-k">心情</div>'
         + '<div class="relthink-flip">' + flip + '</div></div>';
      /* 本轮判定 tags */
      if (sig.valence != null){
        h += '<div class="relthink-sec"><div class="relthink-k">本轮判定（裁判给的信号）</div><div class="relthink-sig">';
        var v = Number(sig.applied_valence != null ? sig.applied_valence : sig.valence);
        var vCls = v > 0.001 ? ' hot' : (v < -0.001 ? ' bad' : '');
        h += '<span class="relthink-tag' + vCls + '">情绪价 ' + (v >= 0 ? '+' : '') + v.toFixed(2) + '</span>';
        if (Number(sig.care) > 0)        h += '<span class="relthink-tag hot">关心 ' + Number(sig.care).toFixed(2) + '</span>';
        if (Number(sig.perfunctory) > 0) h += '<span class="relthink-tag bad">敷衍 ' + Number(sig.perfunctory).toFixed(2) + '</span>';
        if (Number(sig.repair) > 0)      h += '<span class="relthink-tag hot">挽回 ' + Number(sig.repair).toFixed(2) + '</span>';
        if (Number(sig.harsh) > 0)       h += '<span class="relthink-tag bad">伤人 ' + Number(sig.harsh).toFixed(2) + '</span>';
        if (sig.newbie)                  h += '<span class="relthink-tag">新手期·免负</span>';
        h += '</div>';
        if (sig.newbie && Number(sig.valence) < -0.001 && Number(sig.applied_valence) > -0.001){
          h += '<div class="relthink-note">裁判原本判了 ' + Number(sig.valence).toFixed(2)
             + '，但新手期保护把它清零了（不扣分）。</div>';
        }
        if (sig.user_text) h += '<div class="relthink-note">这句话：' + _relEsc(sig.user_text) + '</div>';
        if (sig.reason) h += '<div class="relthink-note">判定理由：' + _relEsc(sig.reason)
          + (sig.judge ? '（' + _relEsc(sig.judge) + ' 裁判）' : '') + '</div>';
        h += '</div>';
      }
      /* 三维好感 + 阶段 */
      var order = [['trust','信任'],['intimacy','亲密'],['warmth','热络']];
      h += '<div class="relthink-sec"><div class="relthink-k">好感度（隐藏数值，仅供检视）</div><div class="relthink-rows">';
      order.forEach(function(p){
        var val = _relClamp(dims[p[0]]);
        h += '<span class="relthink-name">' + p[1] + '</span>'
           + '<span class="relthink-bar"><i style="width:' + val.toFixed(1) + '%"></i></span>'
           + '<span class="relthink-val">' + val.toFixed(1) + '</span>';
      });
      h += '</div>';
      var meta = [];
      if (rel.stage) meta.push('阶段 ' + _relEsc(rel.stage));
      if (rel.archetype) meta.push('原型 ' + _relEsc(rel.archetype) + (rel.archetype_inferred ? '(自动)' : ''));
      if (rel.turns) meta.push('第 ' + rel.turns + ' 轮');
      if (meta.length) h += '<div class="relthink-note">' + meta.join(' · ') + '</div>';
      h += '</div>';
      return h;
    }
    function renderRelThink(row, m){
      if (!row || !m) return;
      var stack = row.querySelector('.stack');
      if (!stack) return;
      var rel = (m.role === 'user') ? null : m.rel;
      var box = stack.querySelector('.relthink');
      if (!rel){
        if (box) box.remove();
        return;
      }
      _relEnsureCss();
      if (!box){
        box = document.createElement('div');
        box.className = 'relthink';
        box.innerHTML = '<button class="relthink-head" type="button">'
          + '<span class="relthink-title">关系思考</span>'
          + '<span class="relthink-chip"></span>'
          + '<span class="relthink-caret">▾</span></button>'
          + '<div class="relthink-body"></div>';
        box.querySelector('.relthink-head').addEventListener('click', function(){
          box.classList.toggle('open');
        });
        stack.appendChild(box);
      }
      var chip = box.querySelector('.relthink-chip');
      if (chip) chip.textContent = '心情 · ' + ((rel.mood && rel.mood.label) || '平静');
      var body = box.querySelector('.relthink-body');
      if (body) body.innerHTML = relThinkHTML(rel);
    }

    function upsertRow(m){
      const scroll = document.getElementById('scroll');
      if (!scroll) return;                          // 聊天页不在 DOM（切到子页）→ 跳过，不报错
      /* 【2026-10-13 修】空状态移除：index.html 内联降级版 upsertRow 会移除
         #chatEmpty，但生产路径首选的是本函数（startChatLive 的
         window.__chat.upsertRow）——它没移除过 ⇒ 有消息时空态卡片/提示
         一直挂在消息上方（升级成空状态卡片后并存更显眼）。这里补上。 */
      const _ce = document.getElementById('chatEmpty');
      if (_ce) _ce.remove();
      /* DOM 级去重：防止双重轮询（旧缓存脚本 + 新 startChatLive）同消息渲染两次 */
      let row = scroll.querySelector('.row[data-msg-id="' + m.id + '"]') || null;
      /* 【2026-10-10 修"重启才出现 / 打字不出新消息"】SPA 切栏、换肤，或外壳重建聊天
         DOM 时，旧行会从文档脱离，但 rendered Map 仍持有其引用。若直接复用该"僵尸节点"，
         后续流式/文本更新都写进看不见的旧节点，活 DOM 永远没有这一行 —— 用户感知就是
         "打字后没新消息、重启（整页重建）才出来"。这里凡发现缓存节点已脱离文档，
         一律丢弃并在活 DOM 按真实 id 重建；若活 DOM 已有同 id 行则以它为准。 */
      let _cached = rendered.get(m.id) || null;
      if (_cached && !_cached.isConnected) { rendered.delete(m.id); _cached = null; }
      if (_cached && row && _cached !== row) { rendered.delete(m.id); _cached = null; }
      row = row || _cached || null;
      /* 用户消息去重（2026-09-04 修复"同一句刷出两遍"）：发送瞬间前端乐观插入
         id='me-<ts>' 的气泡；后端轮询回来的真实消息是自增整数 id，两者 id 不同会让
         同一句「我」渲染成两行。对 role=user 的新消息，按文本匹配复用乐观气泡并把
         其 data-msg-id「转正」为真实 id，消除重复。同时兜底查 DOM（降级内联乐观渲染
         的 me-* 气泡可能没进 rendered Map）。 */
      if (!row && m.role === 'user') {
        const tx = (m.text || '').trim();
        const imgs = (Array.isArray(m.images) && m.images.length) ? m.images : null;
        /* 同一句「我」能否复用乐观气泡：① 文本全等；② 纯图消息（文本空）按图片数量指纹匹配，
           避免乐观图行被漏配而新建第二行（2026-10-08 治"发图重复图片"）。 */
        function userRowMatches(r){
          const b = r.querySelector ? r.querySelector('.bub') : null; if (!b) return false;
          if ((b.textContent || '').trim() === tx) return true;
          if (!tx && imgs){
            const wrap = b.querySelector('.msg-imgs');
            if (wrap && wrap.children.length === imgs.length) return true;
          }
          return false;
        }
        for (const [k, r] of rendered) {
          if (typeof k === 'string' && k.indexOf('me-') === 0 && userRowMatches(r)) { row = r; rendered.delete(k); break; }
        }
        if (!row) {
          const cand = scroll.querySelectorAll('.row.me[data-msg-id^="me-"]');
          for (const c of cand) { if (userRowMatches(c)) { row = c; break; } }
        }
      }
      const who = m.role === 'user' ? '我' : (m.char_name || liveName('达妮娅'));
      /* 【2026-10-05】'status_done' = 后端把"已完成的操作/观看卡"转成的持久历史卡
         （治"刷新后卡片彻底消失"）。它与进行中的卡共用一套渲染，只是不再更新，
         折叠成一条静态的"✓ 摘要"。 */
      const isAgentCard = !!((m.kind === 'status' || m.kind === 'status_done') && m.agent_meta);
      const isDoneCard = (m.kind === 'status_done');
      const isMilestone = (m.kind === 'milestone');
      if (!row){
        row = document.createElement('div');
        row.className = 'row' + (m.role === 'user' ? ' me' : '') + (isAgentCard ? ' status-card' : '')
          + (isDoneCard ? ' status-done' : '') + (isMilestone ? ' milestone-row' : '')
          /* 【2026-10-16·C8】失败/被拦截的消息此前与"她正常说的话"长得一模一样
             （后端会带 failed:true 落进普通 ai 条目，而渲染器没有 failed 分支），
             用户会以为达妮娅在说"消息发送失败…"。这里加成醒目样式。 */
          + (m.failed ? ' is-error' : '');
        row.setAttribute('data-msg-id', m.id);       // DOM 去重标记
        /* 头像框里放「通用人形占位」，不再塞名字（名字本来就写在 .meta .nm 上，
           塞进 34px 的头像框只会被裁成两行、看着像排版坏了）。
           配了主题头像时 .av.has-img 用背景图覆盖并隐藏占位。 */
        row.innerHTML = '<div class="av' + (m.role === 'user' ? ' me' : '') + '">' +
            '<span class="ph-av" aria-hidden="true"><i><svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="8.2" r="3.7"/><path d="M12 13.7c-4.15 0-7.5 2.34-7.5 5.23 0 .63.53 1.14 1.18 1.14h12.64c.65 0 1.18-.51 1.18-1.14 0-2.89-3.35-5.23-7.5-5.23z"/></svg></i></span>' +
          '</div>' +
          '<div class="stack"><div class="meta"><span class="nm">' + who + '</span><span class="tm">' + fmtAgo(m.ts) + '</span></div><div class="bub"></div></div>';
        scroll.appendChild(row);
        rendered.set(m.id, row);
        /* 入场动画：CSS keyframes（bubRowIn 上浮淡入 + bubGrowIn 气泡一点点长大），
           动画结束即摘掉 class，避免残留影响后续样式/重排。keyframes 对新建元素必播放。 */
        row.classList.add('row-in');
        row.addEventListener('animationend', function _done(){ row.classList.remove('row-in'); }, { once: true });
      } else if (m.role === 'user') {
        // 复用乐观气泡：把其 id 转正，避免后续 pollChat 再建一行
        row.setAttribute('data-msg-id', m.id);
        rendered.set(m.id, row);
      }
      /* —— Agent 执行卡片：结构化状态 → 玻璃卡片，跳过普通气泡的流式/图集逻辑 ——
         【2026-10-08 健壮性】单条 agent 消息渲染异常（agent_meta shape 异常 /
         __agentCard 偶发未就绪）绝不能拖垮整轮 upsertRow 刷新，也绝不能静默留空白。
         包 try/catch + 文本降级：渲染失败也能让用户看到一句可读提示，而非空白卡片。 */
      if (isAgentCard){
        const bub = row.querySelector('.bub');
        const _alt = (m.agent_meta && (m.agent_meta.plan || m.agent_meta.title))
          || (m.text || '达妮娅正在处理…');
        try {
          if (bub && window.__agentCard) {
            window.__agentCard.render(bub, m.agent_meta || {}, isDoneCard);
          } else if (bub && bub.textContent !== _alt) {
            bub.textContent = _alt;   // 组件未就绪：降级为可读文本，不留空白
          }
        } catch (e) {
          if (bub && bub.textContent !== _alt) bub.textContent = _alt;
        }
        ensureChatPin(scroll); stickToBottom(scroll);
        return row;
      }
      /* —— 【2026-10-11·P1 里程碑】关系质变时刻：她首次跨过阶段门槛说的那句真心话 ——
         与普通气泡区分：一个低调的「关系质变 · 抵达XX」小标 + 引用感的正文。
         反 PUA：只呈现"她说了什么"，绝无数字/进度/进度条；不弹窗、不打断。 */
      if (isMilestone){
        const bub = row.querySelector('.bub');
        if (bub){
          const STAGE_CAP = { '熟络': '你们变熟了', '交心': '她开始对你交心', '离不开': '她已经离不开你' };
          const cap = STAGE_CAP[m.milestone_stage] || '关系更进一步';
          const html = '<span class="ms-cap">关系质变 · ' + esc(cap) + '</span>'
                     + '<span class="ms-text">' + esc(m.text || '') + '</span>';
          if (bub.innerHTML !== html) bub.innerHTML = html;
        }
        ensureChatPin(scroll); stickToBottom(scroll);
        return row;
      }
      /* ---- 流式输出适配（2026-09-29）----
         ① 文本增长时把气泡高度从旧值过渡到新值（显式 height + transition），
            避免逐句追加时高度"跳一下"；过渡完立刻还原为 auto，不锁死高度。
         ② 流式期间在文本末尾挂一个闪烁光标（.caret），收尾自动摘掉。
         ③ 自动滚动改为"贴近底部才滚 + 平滑滚动"：用户上翻看历史时不再被强行拽回。 */
      const bub = row.querySelector('.bub');
      const nextText = m.text || '';
      const isStreaming = !!m.streaming;
      if (bub && bub.textContent !== nextText) {
        const REDUCE = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
        const h0 = bub.offsetHeight;
        bub.textContent = nextText;
        if (!REDUCE && h0 > 0) {
          const h1 = bub.offsetHeight;
          if (h1 !== h0) {
            bub.style.height = h0 + 'px';
            void bub.offsetHeight;                    // 强制回流，让起始高度生效
            bub.style.transition = 'height .24s cubic-bezier(.22,.61,.36,1)';
            bub.style.height = h1 + 'px';
            // 句柄互斥：连续流式更新时，若上一轮还原定时器还没触发，先取消，
            // 否则它会在新一轮过渡中途把 height 清空 → 高度突然跳回 auto。
            if (bub.__hTimer) clearTimeout(bub.__hTimer);
            bub.__hTimer = setTimeout(function () {
              bub.style.height = ''; bub.style.transition = ''; bub.__hTimer = 0;
            }, 260);
          }
        }
      }
      if (bub && isStreaming) {
        if (!bub.querySelector('.caret')) bub.insertAdjacentHTML('beforeend', '<span class="caret"></span>');
      } else if (bub) {
        const c = bub.querySelector('.caret'); if (c) c.remove();
      }
      /* 消息图片（2026-10-03）：文字下方的缩略图条（乐观 dataURL ↔ 后端落盘 URL 平滑替换） */
      syncMsgImgs(bub, m.images);
      /* 时间每次渲染刷新（修「永远刚刚」）：相对时间随轮询推进 刚刚→X分钟前→HH:MM */
      const _tm = row.querySelector('.meta .tm');
      if (_tm) _tm.textContent = fmtAgo(m.ts);
      if (row) row.classList.toggle('streaming', isStreaming);
      renderRelThink(row, m);   /* 好感度「关系思考」折叠面板（默认收起，仅 AI 行有 m.rel 时出现） */
      ensureChatPin(scroll); stickToBottom(scroll);
      return row;
    }
  function pollChat(){
    Backend.getChat().then(r => {
      if (!r || !r.success) return;
      const active = r.active_name || '达妮娅';
      const msgs = r.messages || [];
      /* 【2026-10-05 修"状态卡卡死在执行中"】对账删除：
         后端在真实回复到达时会 _drop_chat_status 把状态卡从缓冲里删掉
         （设计稿语义："完成后缩回小卡片再由回复接管"），但本循环此前**只增不删**
         ⇒ DOM 里那行永远留着，冻结在最后一次渲染的样子（往往是第一帧：
         只有"执行中"头部、步骤还没画上去），之后再也不更新 ——
         用户看到的就是"卡死、没步骤、没进度、永远不变完成"。
         现在：以服务端快照为准，删掉"已从缓冲消失"的行。
         ⚠️ 只对数字 id 对账：'me-*' 乐观气泡还没转正，不能误删。 */
      const seen = {};
      msgs.forEach(m => { seen[String(m.id)] = true; });
      const scrollEl = document.getElementById('scroll');
      rendered.forEach((row, id) => {
        const k = String(id);
        if (k.indexOf('me-') === 0) return;            // 乐观气泡另行转正，不动
        if (seen[k]) return;
        /* 【2026-10-08 对齐设计稿"分镜播放"】状态卡在展开分镜窗口内不被回复对账删除：
           让"计划 + 逐条勾选"演完再退场（否则快任务 0.2s 就被回复接管删掉）。 */
        if (deferStatusCardExit(row)){ rendered.delete(id); return; }
        /* 【2026-10-05】平滑退场：直接 removeChild 会让上面的行"啪"地抽走、
           下面的回复瞬间上跳。改成固定高度→收起→淡出（约 .42s）。 */
        if (row && row.parentNode) acRowExit(row, null);
        rendered.delete(id);
      });
      if (scrollEl){
        // 兜底：清空聊天等场景可能留下不在 rendered 里的行，一并按 data-msg-id 对账
        scrollEl.querySelectorAll('.row[data-msg-id]').forEach(row => {
          const k = row.getAttribute('data-msg-id') || '';
          if (!k || k.indexOf('me-') === 0) return;
          if (seen[k]) return;
          if (rendered.has(k) || rendered.has(Number(k))) return;
          /* 【2026-10-08 同上】分镜未演完的状态卡行延时退场，不被立即删 */
          if (deferStatusCardExit(row)) return;
          acRowExit(row, null);
        });
      }
      msgs.forEach(m => {
        try {
          const isNew = !rendered.has(m.id);
          upsertRow(Object.assign({}, m, { char_name: m.char_name || active }));
          if (isNew && m.role !== 'user') hideChatSwitch();   // AI 已开口 → 收起"切换模型"进度条
        } catch (e) {
          /* 【2026-10-05】单条渲染失败绝不能吞掉 forEach 后续消息——
             否则一条坏消息会让"后面的回复永远不上屏"，表现就是"她不理我"。 */
          try { console.warn('[chat] 行渲染失败 id=' + m.id, e); } catch (e2) {}
        }
      });
      alignRowOrder(msgs, scrollEl);
      /* 【2026-10-08 思考中占位】真实 AI 回复（含流式首句）已上屏 → 移除"思考中"占位。
         占位本身是 .row.ai 但带 .think-ph，这里排除它，只认真实 AI 行。 */
      (function(){
        var sc = document.getElementById('scroll'); if (!sc) return;
        if (sc.querySelector('.row.ai:not(.think-ph)')) removeThinking();
      })();
    }).catch(()=>{});
  }
  /* 【2026-10-05 修"时序乱"】乱序根因：乐观气泡（me-*）发送瞬间先上屏，而
     upsertRow 对新行一律 append 尾部 ⇒ 当「上一轮回复」与「下一条用户消息」
     在同一轮快照里出现（回复慢、用户连发很常见），回复行会被排到乐观气泡
     **之后** —— 每条 AI 回复都错位到更晚的用户消息下方（01:39 截图实锤：
     「汽水音乐已经打开啦」排在了「帮我打开浏览器」后面）。
     这里以**服务端快照顺序**为准做对齐：先比对 DOM 行序与快照序，**只有
     不一致时才重排**（appendChild 移动已有节点会重播 CSS 入场动画，
     按序到达的常态下零 DOM 操作、零动画打扰）。 */
  function alignRowOrder(msgs, scrollEl){
    if (!scrollEl) return;
    /* 参与排序的行 = 已在快照里的非退场行。退场(ac-exit)行与 me-* 乐观行都不参与，
       它们在最后单独处理。 */
    const rows = [];
    scrollEl.querySelectorAll('.row[data-msg-id]').forEach(r => {
      const k = r.getAttribute('data-msg-id') || '';
      if (!k || k.indexOf('me-') === 0) return;
      if (r.classList.contains('ac-exit')) return;
      rows.push({ k: k, el: r });
    });
    if (!rows.length) return;
    /* 【2026-10-05 修"排序从未生效"】⚠️ 旧实现有个让整个函数**永远提前 return**
       的守卫：`if (want.length !== domOrder.length) return;`。
       原因：退场中的卡片行已被 `domOrder` 排除，但服务端快照里也已经没有它 ⇒
       只要有一张卡正在退场，长度就不相等 ⇒ 排序**一次都没跑过**（这正是 02:12
       截图「汽水音乐回复排到抖音消息后面」的真因）。
       现在改为**不依赖长度相等**：只把「快照里存在且当前在 DOM 的行」按快照序
       重排，缺失/多余的行交给对账删除与退场动画各自处理。 */
    const inSnap = {};
    for (const m of msgs) inSnap[String(m.id)] = true;
    /* 目标序列 = 服务端快照顺序里、且当前确实在 DOM 中的行 */
    const byId = {};
    rows.forEach(r => { byId[r.k] = r.el; });
    const wantOrder = [];
    for (const m of msgs){
      const k = String(m.id);
      if (inSnap[k] && byId[k]) wantOrder.push(k);
    }
    if (wantOrder.length < 2) return;                        // 不足两行谈不上排序
    /* 【最小搬移】用「后继游标」逐个把行挪到正确位置：
       对每一行，若它的下一个兄弟不是「期望的下一行」，就把期望行插到它后面。
       顺序已对的相邻对完全不动 ⇒ 常态零 DOM 操作、不会重播 CSS 入场动画
       （appendChild 全量重排会重播，故不用它）。 */
    for (let i = 0; i < wantOrder.length - 1; i++){
      const cur = byId[wantOrder[i]];
      const nxt = byId[wantOrder[i + 1]];
      if (!cur.parentNode || !nxt.parentNode) continue;
      if (cur.nextSibling !== nxt) scrollEl.insertBefore(nxt, cur.nextSibling);
    }
    // 未转正的乐观行按原相对顺序垫到最尾（它们总比快照里的行新）
    scrollEl.querySelectorAll('.row[data-msg-id^="me-"]').forEach(r => {
      if (!r.classList.contains('ac-exit') && r.parentNode) scrollEl.appendChild(r);
    });
  }
  function pollChatLock(){
    const composer = document.querySelector('.composer');
    const lockEl = document.getElementById('chatLock');
    if (!composer || !lockEl) return;
    Backend.getTextChatStatus().then(r => {
      if (!r || r.success !== true) return;
      /* fail-open：仅后端明确返回 usable===false 才锁，避免瞬态/字段缺失误锁输入框 */
      const locked = (r.usable === false);
      const input = document.getElementById('input');
      const sbtn = document.getElementById('send');
      if (input) input.disabled = locked;
      if (sbtn) sbtn.disabled = locked;
      composer.classList.toggle('locked', locked);
      lockEl.classList.toggle('show', locked);
      lockEl.setAttribute('aria-hidden', locked ? 'false' : 'true');
      /* 【2026-10-16·C8】锁定时必须告诉用户"为什么 + 还要等多久"。
         此前只锁不解释（且 index.html 里根本没有 #chatLock 元素，等于永不生效），
         用户看到的是"消息发出去没人回"。后端已回 reason / retry_after_s。 */
      if (locked){
        const sec = Math.ceil(Number(r.retry_after_s) || 0);
        lockEl.textContent = (r.reason || '聊天端口暂不可用')
          + (sec > 0 ? ('（约 ' + sec + ' 秒后自动解锁）') : '');
      }
    }).catch(()=>{});
  }
  let chatSwitchTimer = null;
  function hideChatSwitch(){
    const bar = document.getElementById('chatSwitchBar');
    if (bar) bar.classList.remove('show');
    if (chatSwitchTimer){ clearInterval(chatSwitchTimer); chatSwitchTimer = null; }
    if (window.__chatSwitchTimer){ clearInterval(window.__chatSwitchTimer); window.__chatSwitchTimer = null; }
  }
  function showChatSwitch(){
    const bar = document.getElementById('chatSwitchBar');
    const txt = document.getElementById('chatSwitchTxt');
    if (bar) bar.classList.add('show');
    if (txt) txt.textContent = '正在连接聊天模型…';
    if (chatSwitchTimer){ clearInterval(chatSwitchTimer); chatSwitchTimer = null; }
    chatSwitchTimer = setInterval(() => {
      Backend.getTextChatStatus().then(s => {
        if (!s || s.success !== true) return;
        const t = document.getElementById('chatSwitchTxt');
        if (!t) return;
        if (s.mode === 'manual'){
          /* 2026-10-08 修复：manual_name 取的是 providers[].name（人工展示名，换 model 不跟着改），
             显示会永久停在旧模型名 → 与设置页对不上。改为优先取该端口的真实 model（selectable[].model）。
             端口名保留在 title 里，不丢信息。 */
          const mp = (s.selectable || []).find(p => p.id === s.manual_provider) || {};
          const shown = mp.model || s.manual_name || s.manual_provider || '手动端口';
          t.textContent = '正在连接「' + shown + '」（手动模式）';
          t.title = (mp.name || s.manual_name || s.manual_provider || '') + (mp.model ? ('　模型：' + mp.model) : '');
        } else if (s.last_used){
          const ent = (s.ports || []).find(p => p.id === s.last_used);
          t.textContent = '已切换至：' + (ent ? (ent.model || ent.name) : s.last_used);
          t.title = ent ? (ent.name || '') : '';
        } else {
          t.textContent = '正在自动选择可用聊天模型…';
        }
      }).catch(() => {});
    }, 500);
    window.__chatSwitchTimer = chatSwitchTimer;
    setTimeout(hideChatSwitch, 25000);           // 安全兜底：25s 内没回复也收起，不卡 UI
  }
  /* 【2026-10-08 思考中占位】发消息立即显示"思考中…"，首 token / 真实回复到达后由 removeThinking 移除 */
  function showThinking(){
    var sc = document.getElementById('scroll'); if (!sc) return;
    if (sc.querySelector('.think-ph')) return;          // 已显示，不重复插
    var row = document.createElement('div');
    row.className = 'row ai think-ph';
    row.setAttribute('data-think', '1');
    row.innerHTML = '<div class="av me">AI</div><div class="stack"><div class="meta">'
      + '<span class="nm">' + liveName('达妮娅') + '</span><span class="tm">' + fmtAgo(Date.now() / 1000) + '</span></div>'
      + '<div class="bub"><span class="think-dots"><i></i><i></i><i></i></span></div></div>';
    sc.appendChild(row);
    stickToBottom(sc);
    // 安全兜底：30s 内若还没被真实回复替换（异常 / 无网络），自动移除，不卡死"思考中"
    row.__t = setTimeout(function(){ removeThinking(); }, 30000);
  }
  function removeThinking(){
    var sc = document.getElementById('scroll'); if (!sc) return;
    var ph = sc.querySelector('.think-ph'); if (!ph) return;
    if (ph.__t) clearTimeout(ph.__t);
    ph.remove();
  }
  /* 【2026-10-11 换角色·聊天页隔离】切角色后强制"清空并重拉"聊天视图。
     背景（用户实锤）：切角色后聊天页仍显示**上一个角色**的消息。
     根因：pollChat 靠"服务端快照 - rendered 集合"对账删除，但切角色常在
     角色页（SPA 已离开聊天页）触发，此时 #scroll 不在 DOM / 或新旧 id 撞号，
     对账删除被跳过；切回聊天页时旧行还在、新角色消息又 append 到后面 ⇒ 新旧混排。
     修法：不依赖对账边角情况——切角色成功即**同步清空** rendered 与 DOM 行，
     再由 pollChat 从后端（已 clear + 置 _chat_history_loaded=False）读回新角色历史。
     纯前端视图操作，不碰后端数据（历史仍按 char 分文件隔离在 data/chatlog）。 */
  function resetChatView(){
    try { rendered.clear(); } catch (e) {}
    var sc = document.getElementById('scroll');
    if (sc){
      sc.querySelectorAll('.row[data-msg-id]').forEach(function(row){
        try { row.remove(); } catch (e) {}
      });
    }
    try { removeThinking(); } catch (e) {}
  }
  /* 暴露给聊天页内联脚本（send 乐观渲染 / 发送后显示进度条 / 思考中占位） */
  window.__chat = { upsertRow, pollChat, pollChatLock, hideChatSwitch, showChatSwitch, attachOptimisticImages, scrollToBottom, showThinking, removeThinking, resetChatView };
  /* 暴露为全局，供 index.html 的 chat_push WebView2 消息监听即时刷新双工 */
  window.pollChat = pollChat;
  window.pollChatLock = pollChatLock;

  /* ============================================================
     免费额度耗尽 · 询问浮层（2026-10-02）
     后端 vision_router 判定某模型额度耗尽 → 广播 quota_alert → 这里弹一个挂 body 的
     询问条：告知哪个模型额度用完、列出还没耗尽的候选，让用户决定"切换 / 暂不"。
     ⚠️ 只询问、不自动切换（用户明确要求）。跟随 design_tokens，深浅主题自动适配。
     ============================================================ */
  window.__quotaAlert = (function(){
    var box = null;
    function ensure(){
      if (box && box.isConnected) return box;
      box = document.getElementById('quotaAlertBox');
      if (!box){
        box = document.createElement('div');
        box.id = 'quotaAlertBox';
        box.className = 'qa-box';
        box.innerHTML =
          '<div class="qa-ic"><span class="qa-dot"></span></div>' +
          '<div class="qa-main">' +
            '<div class="qa-t"></div>' +
            '<div class="qa-s"></div>' +
          '</div>' +
          '<div class="qa-ops">' +
            '<select class="qa-sel"></select>' +
            '<button class="btn sm primary qa-ok" type="button">切换</button>' +
            '<button class="btn sm qa-retry" type="button">再试一次</button>' +
            '<button class="btn sm qa-later" type="button">暂不</button>' +
          '</div>';
        (document.body || document.documentElement).appendChild(box);
        box.querySelector('.qa-later').addEventListener('click', function(){ hide(); });
        box.querySelector('.qa-retry').addEventListener('click', function(){
          var btn = box.querySelector('.qa-retry');
          var mid = btn.getAttribute('data-mid') || '';
          if (!mid) return;
          btn.disabled = true; btn.textContent = '探测中…';
          var done = function(r){
            btn.disabled = false; btn.textContent = '再试一次';
            if (r && r.ok){
              hide();
              if (typeof Backend.toast === 'function') Backend.toast('「' + mid + '」已恢复，可以继续看图/看视频了');
            } else if (typeof Backend.toast === 'function'){
              Backend.toast('仍未恢复：' + ((r && (r.hint || r.error)) || '未知原因'), 'err');
            }
          };
          try {
            if (typeof Backend.probeVisionModel === 'function'){
              /* pywebview js_api 方法名自动 snake→camel：probe_vision_model → probeVisionModel */
              Backend.probeVisionModel(mid).then(done).catch(function(e){
                btn.disabled = false; btn.textContent = '再试一次';
                if (typeof Backend.toast === 'function') Backend.toast('探测异常：' + e, 'err');
              });
            } else {
              btn.disabled = false; btn.textContent = '再试一次';
            }
          } catch(e){
            btn.disabled = false; btn.textContent = '再试一次';
          }
        });
        box.querySelector('.qa-ok').addEventListener('click', function(){
          var sel = box.querySelector('.qa-sel');
          var target = sel && sel.value;
          if (!target){ hide(); return; }
          hide();
          try {
            if (typeof Backend.switchVisionModel === 'function'){
              Backend.switchVisionModel(target).then(function(r){
                if (r && r.success){
                  if (typeof Backend.toast === 'function') Backend.toast('已切换视觉模型：' + target);
                } else if (typeof Backend.toast === 'function'){
                  Backend.toast('切换失败：' + ((r && r.error) || '未知原因'), 'err');
                }
              }).catch(function(e){
                if (typeof Backend.toast === 'function') Backend.toast('切换异常：' + e, 'err');
              });
            }
          } catch(e){}
        });
      }
      return box;
    }
    function hide(){ if (box) box.classList.remove('qa-show'); }
    return function(d){
      try {
        if (!d) return;
        var b = ensure();
        var name = d.model_name || d.model_id || '该模型';
        var mid = d.model_id || '';
        b.querySelector('.qa-t').textContent = '「' + name + '」免费额度已用完';
        var retryBtn = b.querySelector('.qa-retry');
        if (retryBtn) retryBtn.setAttribute('data-mid', mid);
        var cands = d.candidates || [];
        var sel = b.querySelector('.qa-sel');
        if (sel){
          sel.innerHTML = '';
          cands.forEach(function(c){
            var o = document.createElement('option');
            o.value = c.id;
            o.textContent = (c.name || c.id) + (c.quota_exhausted ? '（额度已耗尽，可切回重试）' : '（还有免费额度）');
            sel.appendChild(o);
          });
          var usable = cands.filter(function(c){ return !c.quota_exhausted; });
          sel.style.display = cands.length ? '' : 'none';
          var okBtn = b.querySelector('.qa-ok');
          if (okBtn) okBtn.style.display = usable.length ? '' : 'none';
        } else {
          var okBtn2 = b.querySelector('.qa-ok');
          if (okBtn2) okBtn2.style.display = 'none';
        }
        var hasCand = cands.some(function(c){ return !c.quota_exhausted; });
        b.querySelector('.qa-s').textContent = hasCand
          ? '可选：切换到还有免费额度的模型（也可去阿里云控制台处理后点「再试一次」）'
          : '当前清单里其它候选也暂不可用：阿里云每个模型各有独立免费额度，可去控制台开通新模型后加进设置，或充值后点「再试一次」验证恢复';
        b.classList.add('qa-show');
      } catch(e){}
    };
  })();

  /* ============================================================
     达妮娅「在场感」浮层 · 共享样式注入器
     两个浮层（正在看 / Agent 执行）共用同一套 design_tokens，深浅主题 + 强调色自动适配。
     样式注入 document.head（SPA 安全：随 app_backend.js 分发到所有子页）。
     —— 动效规范（GitHub 调研沉淀，见下方 agent card 注释）：
        · 物理属性（位移/缩放）用弹簧曲线 cubic-bezier(.34,1.56,.64,1)；
        · 视觉属性（颜色/透明度）用缓动过渡，绝不对 opacity 用弹簧（会显"迟滞"）；
        · 退场比入场快（约 2:3）；
        · 状态切换 200–300ms；尊重 prefers-reduced-motion（只留交叉淡入，去掉位移/缩放）。 */
  function __daniInjectStyle(id, css){
    if (document.getElementById(id)) return;
    var st = document.createElement('style');
    st.id = id;
    st.textContent = css;
    (document.head || document.documentElement).appendChild(st);
  }
  /* 下一帧执行（双 rAF）：新插入的元素若在「同一帧」改 class，
     浏览器没有 before-change style，transition 会静默不跑 → 必须在下一帧再加类。 */
  function __daniNextFrame(fn){
    try {
      var raf = (typeof requestAnimationFrame === 'function') ? requestAnimationFrame : null;
      if (raf){ raf(function(){ raf(function(){ fn(); }); }); }
      else { setTimeout(fn, 32); }
    } catch(e){ fn(); }
  }

  /* ——「正在看」链接/视频 气泡（2026-10-03 重做）——
     后端 video_watch 阶段事件 → 输入框上方玻璃气泡：头像光环 + 眼睛图标 +
     阶段文字交叉淡入 + 下载确定进度 / 其它阶段流光不定态 + 弹簧入场 + 完成打勾 / 失败变红退场。 */
  /* ——「正在看」进度气泡（2026-10-03 增强：更详细 + 更进进度）——
     后端 video_watch / media_ingest 各阶段（resolve→download→frame→describe / page）
     经总线 VIDEO_WATCH_PROGRESS → webapp 广播 {stage,message,pct,done,kind,title,url}。
     气泡升级为：来源图标 + 标题（在看什么）+ 多阶段步进器（已完成打勾 / 当前高亮 /
     出错变红）+ 详情行 + 下载阶段百分比进度条。复用 design_tokens、玻璃质感、弹簧入场、
     退场比入场快（2:3）、prefers-reduced-motion 降级只留淡入；不引入无限呼吸/霓虹。 */
  window.__videoWatch = (function(){
    var bubble = null, hideTimer = null;
    /* 看抖音气泡的 GSAP 守卫（与 __agentCard 同源）：M2 非空才接管弹簧动效；
       window.gsap 缺席或系统「减少动态」→ M2=null，全程纯 CSS（与今天行为一致）。 */
    var M2 = (window.gsap && !(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches)) ? window.gsap : null;
    var EYE = '<svg class="vw-eye" viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,.92)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.5-6.5 10-6.5S22 12 22 12s-3.5 6.5-10 6.5S2 12 2 12z"/><circle cx="12" cy="12" r="2.6" fill="rgba(255,255,255,.92)" stroke="none"/></svg>';
    var CHECK = '<svg class="vw-check" viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,.95)" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4.5 12.5l5 5 10-11"/></svg>';
    var ICONS = {
      video: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="3"/><path d="M10 9l5 3-5 3z" fill="currentColor" stroke="none"/></svg>',
      link:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 1 0-5.66-5.66L11.5 7"/><path d="M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 1 0 5.66 5.66L12.5 17"/></svg>',
      image: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="3"/><circle cx="8.5" cy="9.5" r="1.6" fill="currentColor" stroke="none"/><path d="M21 16l-5-5-4 4-2-2-7 7"/></svg>'
    };
    var KIND = {
      video: { stages: [['resolve','解析'],['download','下载'],['frame','抽帧'],['describe','看画面']] },
      link:  { stages: [['page','读取']] },
      image: { stages: [['download','下载'],['describe','看画面']] }
    };
    var CSS = [
      /* 锚定头像的展开方向：左缘固定在「展开后卡片应居中的位置」，宽度从头像处向右生长 */
      '#vwBubble{--vw-w:min(440px,calc(100vw - 48px));position:fixed;left:calc(50% - (var(--vw-w) / 2));bottom:96px;',
      'display:flex;align-items:flex-start;gap:12px;max-width:min(540px,calc(100vw - 48px));',
      /* 尺寸恒定：折叠态 152px、展开态固定 --vw-w，均由显式宽度决定，不随文字长短变化 */
      'width:152px;box-sizing:border-box;overflow:hidden;',
      'transition:width .52s cubic-bezier(.22,1,.36,1),box-shadow .3s ease;',
      'padding:12px 16px 12px 12px;border-radius:var(--r-lg,16px);',
      'background:color-mix(in srgb,var(--surface-1) 88%,transparent);',
      'backdrop-filter:blur(16px) saturate(150%);-webkit-backdrop-filter:blur(16px) saturate(150%);',
      'border:1px solid var(--line-2);box-shadow:var(--shadow-pop);',
      'color:var(--ink-1);font-size:13px;line-height:1.35;opacity:0;pointer-events:none;z-index:9000;',
      'animation:vwIn .42s cubic-bezier(.34,1.56,.64,1) both;}',
      '#vwBubble.vw-out{animation:vwOut .28s cubic-bezier(.4,0,.2,1) both;}',
      '#vwBubble.vw-expanded{width:var(--vw-w);}',
      '#vwBubble.vw-gone{display:none;}',
      '#vwBubble.vw-err{border-color:color-mix(in srgb,var(--bad) 60%,transparent);}',
      '#vwBubble.vw-ok{border-color:var(--line-strong);}',
      '@keyframes vwIn{from{opacity:0;transform:translateY(16px) scale(.94);}',
      'to{opacity:1;transform:translateY(0) scale(1);}}',
      '@keyframes vwOut{from{opacity:1;transform:translateY(0) scale(1);}',
      'to{opacity:0;transform:translateY(-10px) scale(.97);}}',
      '.vw-ava{position:relative;width:32px;height:32px;flex:0 0 auto;border-radius:50%;margin-top:1px;',
      'display:flex;align-items:center;justify-content:center;background:var(--accent-grad);',
      'box-shadow:0 0 0 0 var(--accent-soft-1);}',
      '.vw-ava::after{content:"";position:absolute;inset:-5px;border-radius:50%;',
      'background:radial-gradient(circle,var(--accent-soft-1),transparent 70%);opacity:.8;}',
      '.vw-ava svg{width:18px;height:18px;display:block;position:relative;z-index:1;}',
      '.vw-ava .vw-check{display:none;}',
      '.vw-ava.vw-done-ava .vw-eye{display:none;}',
      '.vw-ava.vw-done-ava .vw-check{display:block;animation:vwPop .4s cubic-bezier(.34,1.56,.64,1) both;}',
      '.vw-ava.vw-err-ava{background:var(--bad);}',
      '.vw-ava.vw-err-ava .vw-eye{display:none;}',
      '@keyframes vwPop{from{opacity:0;transform:scale(.4);}to{opacity:1;transform:scale(1);}}',
      '.vw-main{display:flex;flex-direction:column;gap:0;min-width:0;flex:1 1 auto;}',
      /* 顶行定高：无论如何文字都不会换行/撑高 */
      '.vw-top{display:flex;align-items:center;gap:7px;min-width:0;height:18px;}',
      /* 展开揭幕 = grid 0fr→1fr 的 keyframes 动画（对同帧加类也真实播放），
         1fr 自动贴合内容真实高度 → 不量高度，文字永远不会被裁一半 */
      /* 【2026-10-08 同步设计稿】出血位：向左挪 6px 再内补 6px，
         让最左 vw-dot 的 scale(1.3)+3px 环有空间漏出，不被气泡圆角/边界裁。 */
      '.vw-bodywrap{display:grid;grid-template-rows:0fr;padding-left:6px;margin-left:-6px;',
      'transition:grid-template-rows .45s cubic-bezier(.22,1,.36,1),margin-top .45s cubic-bezier(.22,1,.36,1);}',
      '#vwBubble.vw-expanded .vw-bodywrap{grid-template-rows:1fr;margin-top:8px;',
      'animation:vwReveal .5s cubic-bezier(.22,1,.36,1);}',
      '@keyframes vwReveal{from{grid-template-rows:0fr;margin-top:0;}to{grid-template-rows:1fr;margin-top:8px;}}',
      /* 【2026-10-08 同步设计稿】放开 overflow：原 hidden 会啃掉 vw-ava 光晕(inset:-5px)
         与最左 dot 环的左缘。折叠态靠 .vw-bodywrap 的 grid 0fr 收起，不漏内容。 */
      '.vw-body{min-height:0;overflow:visible;display:flex;flex-direction:column;gap:8px;}',
      '.vw-src{width:16px;height:16px;color:var(--accent-1);flex:0 0 auto;display:inline-flex;align-items:center;}',
      '.vw-src svg{width:16px;height:16px;display:block;}',
      '.vw-label{font-size:10.5px;letter-spacing:.04em;text-transform:uppercase;color:var(--accent-1);opacity:.85;flex:0 0 auto;}',
      /* 标题吃掉剩余宽度再省略号（不再写死 300px，宽也不影响高——父级 .vw-top 已定高） */
      '.vw-title{font-weight:600;color:var(--ink-1);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;',
      'min-width:0;flex:1 1 auto;display:none;}',
      '#vwBubble.vw-expanded .vw-title{display:block;animation:vwTitleIn .34s ease .06s both;}',
      '@keyframes vwTitleIn{from{opacity:0;transform:translateY(-3px);}to{opacity:1;transform:none;}}',
      /* 步进器恒定单行：禁止换行（否则窄窗或阶段多时会折成两行 → 卡片变高） */
      '.vw-steps{display:flex;align-items:center;flex-wrap:nowrap;overflow:visible;height:16px;}',
      '.vw-step{display:flex;align-items:center;gap:5px;font-size:11px;line-height:1;color:var(--ink-3);white-space:nowrap;flex:0 0 auto;}',
      '.vw-step:not(:last-child)::after{content:"";width:16px;height:1.5px;background:var(--line-2);margin:0 7px;border-radius:2px;opacity:.6;}',
      '.vw-dot{width:8px;height:8px;border-radius:50%;background:var(--surface-3);border:1.5px solid var(--line-2);box-sizing:border-box;flex:0 0 auto;transition:background .25s ease,border-color .25s ease,transform .25s cubic-bezier(.34,1.56,.64,1);}',
      '.vw-step.vw-done .vw-dot{background:var(--accent-grad);border-color:transparent;}',
      '.vw-step.vw-done .vw-cap{color:var(--ink-2);}',
      '.vw-step.vw-act .vw-dot{background:var(--accent-1);border-color:var(--accent-1);transform:scale(1.3);box-shadow:0 0 0 3px var(--accent-soft-1);}',
      '.vw-step.vw-act .vw-cap{color:var(--ink-1);font-weight:600;}',
      '.vw-step.vw-err .vw-dot{background:var(--bad);border-color:var(--bad);box-shadow:0 0 0 3px color-mix(in srgb,var(--bad) 25%,transparent);}',
      '.vw-step.vw-err .vw-cap{color:var(--bad);}',
      /* 详情行恒定单行 + 定高：文案再长也只省略号，不再撑高 */
      '.vw-detail{display:flex;align-items:center;gap:8px;min-width:0;height:18px;}',
      '.vw-text{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:1 1 auto;',
      'max-width:360px;line-height:1.35;font-weight:600;color:var(--ink-1);}',
      '.vw-text.vw-swap{animation:vwSwap .32s ease;}',
      '@keyframes vwSwap{0%{opacity:0;transform:translateY(4px);}100%{opacity:1;transform:translateY(0);}}',
      /* 进度条恒定占位：只用 opacity 显隐（原来 none↔block 会让卡片每次进出下载阶段都跳 12px） */
      '.vw-track{position:relative;height:4px;flex:0 0 auto;border-radius:99px;background:var(--surface-3);',
      'overflow:hidden;display:block;opacity:0;transition:opacity .28s ease;}',
      '.vw-track.vw-live{opacity:1;}',
      '.vw-fill{position:absolute;top:0;bottom:0;left:0;width:0;border-radius:99px;background:var(--accent-grad);transition:width .35s cubic-bezier(.22,.61,.36,1);}',
      '.vw-track.vw-indet .vw-fill{width:34%;animation:vwIndet 1.15s ease-in-out infinite;}',
      '@keyframes vwIndet{0%{left:-34%;}100%{left:100%;}}',
      '.vw-pct{font-size:11px;color:var(--accent-1);font-variant-numeric:tabular-nums;flex:0 0 auto;min-width:34px;text-align:right;}',
      '@media (prefers-reduced-motion: reduce){',
      '#vwBubble{animation:vwFade .18s ease both;transition:none!important;}#vwBubble.vw-out{animation:vwFadeOut .16s ease both;}',
      '.vw-ava.vw-done-ava .vw-check{animation:none;}.vw-text.vw-swap{animation:none;}',
      '.vw-track.vw-indet .vw-fill{animation:none;width:100%;}',
      '.vw-dot{transition:none;}.vw-step.vw-act .vw-dot{box-shadow:none;transform:scale(1.15);}',
      '.vw-bodywrap{transition:none!important;}#vwBubble.vw-expanded .vw-bodywrap{animation:none;}',
      '@keyframes vwFade{from{opacity:0;}to{opacity:1;}}@keyframes vwFadeOut{from{opacity:1;}to{opacity:0;}}}'
    ].join('');
    function buildSteps(b, kind){
      var host = b.querySelector('.vw-steps');
      var stages = (KIND[kind] || KIND.video).stages;
      host.innerHTML = stages.map(function(s){
        return '<span class="vw-step" data-stage="' + s[0] + '">' +
               '<i class="vw-dot"></i><span class="vw-cap">' + s[1] + '</span></span>';
      }).join('');
    }
    function kindOf(d){
      if (d.kind) return d.kind;
      if (d.stage === 'page') return 'link';
      if (['resolve','download','frame','describe'].indexOf(d.stage) >= 0) return 'video';
      return 'video';
    }
    function ensure(){
      if (bubble && bubble.isConnected) return bubble;
      __daniInjectStyle('vwBubbleStyle', CSS);
      bubble = document.getElementById('vwBubble');
      if (!bubble){
        bubble = document.createElement('div');
        bubble.id = 'vwBubble';
        bubble.innerHTML =
          '<span class="vw-ava" aria-hidden="true">' + EYE + CHECK + '</span>' +
          '<span class="vw-main">' +
            '<span class="vw-top">' +
              '<span class="vw-src"></span>' +
              '<span class="vw-label">正在看</span>' +
              '<span class="vw-title"></span>' +
            '</span>' +
            '<span class="vw-bodywrap"><span class="vw-body">' +
              '<span class="vw-steps"></span>' +
              '<span class="vw-detail">' +
                '<span class="vw-text"></span><span class="vw-pct"></span>' +
              '</span>' +
              '<span class="vw-track"><span class="vw-fill"></span></span>' +
            '</span></span>' +
          '</span>';
        (document.body || document.documentElement).appendChild(bubble);
      }
      return bubble;
    }
    return function(d){
      try {
        if (!d) return;
        var b = ensure();
        clearTimeout(hideTimer);
        b.classList.remove('vw-gone');   // 上一轮结束后气泡被藏起，本轮重新亮相（入场动画会重播）
        /* 自愈式判定：「当前没展开 + 本次未完成」就该展开。
           不能只靠 _vwExpanded 一次性标记——那样第二次观看会因为标记残留而永远不再展开。 */
        if (!d.done && !b.classList.contains('vw-expanded') && !b._vwExpanding){
          b._vwExpanding = true;
          /* 目标宽度必须是纯 px：min()/calc() 作为 transition 终值在很多引擎里无法插值 → 直接跳变 */
          var _iw = (typeof window !== 'undefined' && window.innerWidth) ? window.innerWidth : 1024;
          b.style.setProperty('--vw-w', Math.max(152, Math.min(440, _iw - 48)) + 'px');
          try { void b.offsetWidth; } catch(e){}   // 强制回流，确立「小胶囊」初始态
          __daniNextFrame(function(){ b.classList.add('vw-expanded'); b._vwExpanding = false; });
        }
        var isDone = !!d.done;
        if (!isDone){ var _lb = b.querySelector('.vw-label'); if (_lb) _lb.textContent = '正在看'; }
        var isErr = isDone && d.stage === 'error';
        var kind = kindOf(d);
        if (b._vwKind !== kind){
          b._vwKind = kind;
          buildSteps(b, kind);
        }
        // 来源图标
        var srcEl = b.querySelector('.vw-src');
        if (srcEl && srcEl.getAttribute('data-k') !== kind){
          srcEl.setAttribute('data-k', kind);
          srcEl.innerHTML = ICONS[kind] || ICONS.video;
        }
        // 标题（在看什么）
        var titleEl = b.querySelector('.vw-title');
        var t = d.title;
        if (!t){
          if (kind === 'link') t = '这条链接';
          else if (kind === 'image') t = '这组图片';
          else t = '这个视频';
        }
        if (titleEl.textContent !== t) titleEl.textContent = t;
        // 阶段步进器
        var stages = (KIND[kind] || KIND.video).stages;
        var curIdx = -1;
        for (var i = 0; i < stages.length; i++){
          if (stages[i][0] === d.stage){ curIdx = i; break; }
        }
        if (curIdx < 0) curIdx = isDone ? stages.length - 1 : 0;
        if (!isDone && !isErr) b._vwActive = curIdx;
        var activeIdx = isErr
          ? (typeof b._vwActive === 'number' ? b._vwActive : curIdx)
          : curIdx;
        var stepEls = b.querySelectorAll('.vw-step');
        for (var j = 0; j < stepEls.length; j++){
          var s = stepEls[j];
          var ns = '';
          if (isErr){
            // 出错优先：进行中的阶段标红，之前的阶段保持已完成
            if (j < activeIdx) ns = 'vw-done';
            else if (j === activeIdx) ns = 'vw-err';
          } else if (isDone){
            ns = 'vw-done';
          } else {
            if (j < curIdx) ns = 'vw-done';
            else if (j === curIdx) ns = 'vw-act';
          }
          if (s.__vwState !== ns){
            s.classList.remove('vw-done','vw-act','vw-err');
            if (ns) s.classList.add(ns);
            /* GSAP 弹簧：阶段点亮/完成时徽标弹一下（M2 为空则退回 CSS 过渡） */
            if (M2){
              var dot = s.querySelector('.vw-dot');
              if (dot){
                if (ns === 'vw-act') M2.fromTo(dot, { scale:1 }, { scale:1.34, duration:.45, ease:'back.out(3)' });
                else if (ns === 'vw-done') M2.fromTo(dot, { scale:.55 }, { scale:1.15, duration:.4, ease:'back.out(2.4)' });
                else if (ns === 'vw-err') M2.fromTo(dot, { scale:1 }, { scale:1.2, duration:.4, ease:'back.out(3)' });
              }
            }
            s.__vwState = ns;
          }
        }
        // 详情 + 百分比
        var msg = d.message || (isDone ? '看完了～' : '达妮娅正在看…');
        var txt = b.querySelector('.vw-text');
        if (txt.textContent !== msg){
          txt.textContent = msg;
          txt.classList.remove('vw-swap');
          void txt.offsetWidth;
          txt.classList.add('vw-swap');
        }
        var pctEl = b.querySelector('.vw-pct');
        var track = b.querySelector('.vw-track');
        var fill = b.querySelector('.vw-fill');
        var showPct = (!isDone && !isErr && d.stage === 'download'
                       && d.pct != null && d.pct !== '');
        if (isDone){
          track.className = 'vw-track vw-live';
          fill.style.width = '100%';
          pctEl.textContent = '';
        } else if (showPct){
          track.className = 'vw-track vw-live';
          fill.style.width = Math.max(0, Math.min(100, parseFloat(d.pct) || 0)) + '%';
          pctEl.textContent = Math.round(parseFloat(d.pct)) + '%';
        } else {
          track.className = 'vw-track';
          fill.style.width = '';
          pctEl.textContent = '';
        }
        // 头像 + 卡片态
        var ava = b.querySelector('.vw-ava');
        b.classList.toggle('vw-err', isErr);
        b.classList.toggle('vw-ok', isDone && !isErr);
        if (ava){
          ava.classList.toggle('vw-done-ava', isDone && !isErr);
          ava.classList.toggle('vw-err-ava', isErr);
        }
        b.classList.remove('vw-out');
        if (isDone){
          hideTimer = setTimeout(function(){
            b.classList.remove('vw-expanded');   // 柔顺缩回小胶囊
            var lb = b.querySelector('.vw-label');
            if (lb) lb.textContent = isErr ? '出错了' : '看完了';
            hideTimer = setTimeout(function(){
              b.classList.add('vw-out');
              hideTimer = setTimeout(function(){
                b.classList.remove('vw-out');
                b.classList.add('vw-gone');   // 彻底藏起，避免 vwIn 重播导致「幽灵气泡」复活
              }, 300);
            }, 1300);
          }, isErr ? 2600 : 1700);
        }
      } catch(e){}
    };
  })();

  /* ——「Agent 执行」卡片（2026-10-03 重做）——
     后端 agent 结构化状态（plan / steps 时间线 / done）→ 聊天流内玻璃卡片：
        · 头部：达妮娅头像 + 标题 + 状态药丸（执行中 / 完成 / 出错）
        · 计划：可折叠的「执行计划」面板（仅在有计划时显示）
        · 步骤：竖向时间线，每步带序号徽标（执行中旋转环 / 完成打勾 / 出错红叉）+ 工具名 + 参数摘要 + 结果
        · 底部：第 N/M 步 进度条
     动效：卡片弹簧入场；新步骤滑入（stagger）；状态色用 250ms 缓动过渡；
        退场比入场快（2:3）；尊重 prefers-reduced-motion（只留交叉淡入）。 */
  window.__agentCard = (function(){
    /* ===== 2026-10-08 移植 GSAP 高级动效层（设计稿 agent_ui_preview.html）=====
       M 非空 = 启用 GSAP 编排；window.gsap 缺席或系统「减少动态效果」→ M=null，全程纯 CSS（与今天行为一致）。
       安全网：① 所有 M 用法均 if(M) 守卫；② 入场用 from(opacity:0) 时额外挂 _acSafeReveal 兜底定时器，
       防 WebView rAF 被双工饿死把卡片卡在透明态（外壳 ui_redesign_main.html 的 safeReveal 同源范式）。 */
    var REDUCE = (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    var M = (window.gsap && !REDUCE) ? window.gsap : null;
    if (M) document.documentElement.classList.add('gsap-on');
    function _acSafeReveal(card){
      if (!card) return;
      setTimeout(function(){
        try {
          if (card.style.opacity !== '' && parseFloat(card.style.opacity || '1') < 1) card.style.opacity = '';
          if (card.style.transform) card.style.transform = '';
        } catch (_) {}
      }, 1100);
    }
    var CSS = [
      '.agent-card{position:relative;width:100%;box-sizing:border-box;',
      'background:color-mix(in srgb,var(--surface-1) 88%,transparent);',
      'backdrop-filter:blur(16px) saturate(150%);-webkit-backdrop-filter:blur(16px) saturate(150%);',
      'border:1px solid var(--line);border-radius:var(--r-lg,16px);box-shadow:var(--shadow-2);',
      'padding:14px 16px;display:flex;flex-direction:column;gap:0;overflow:visible;}',
      /* status-card 行：保留头像/署名，挂在达妮娅一侧（像她的发言一样随进度变化），只去掉气泡壳 */
      '.row.status-card .bub{background:transparent;border:none;box-shadow:none;',
      'padding:0;border-radius:0;max-width:100%;min-height:0;}',
      /* 【2026-10-05】已完成的历史卡（后端把完成的操作/观看卡转成 status_done 保留，
         治"刷新后卡片彻底消失"）：降饱和 + 静态，永不自动展开。 */
      '.row.status-done .bub{opacity:.82;}',
      '.row.status-done .agent-card{background:color-mix(in srgb,var(--surface-1) 70%,transparent);',
      'box-shadow:none;border-color:color-mix(in srgb,var(--line) 70%,transparent);',
      'padding:9px 13px;cursor:default;}',
      '.row.status-done .agent-card::before{opacity:.45;}',
      '.row.status-done .ac-bodywrap{display:none!important;}',   /* 折起步骤区，只留摘要行 */
      '.row.status-done .ac-title{font-size:12.5px;}',
      '.agent-card::before{content:"";position:absolute;inset:0 0 auto 0;height:2px;',
      'background:var(--accent-grad);opacity:.9;}',
      '.agent-card.ac-done::before{background:linear-gradient(90deg,var(--ok),color-mix(in srgb,var(--ok) 40%,var(--accent-1)));}',
      '.agent-card.ac-err::before{background:var(--bad);}',
      /* 【2026-10-04 20:12 治"假完成"】"部分完成/未全部完成"用**琥珀警告色**，
         与绿色「完成」明确区分。20:03 实锤：play_song 只做到"应用已运行"、
         click_at 被安全确认超时拒绝，实际什么都没播，UI 却显示绿色「完成」。 */
      '.agent-card.ac-warn::before{background:linear-gradient(90deg,#d97706,color-mix(in srgb,#d97706 40%,var(--accent-1)));}',
      '.ac-pill.ac-warn{color:#b45309;background:color-mix(in srgb,#d97706 16%,transparent);}',
      '.ac-orb.ac-warn .ac-run-ic{display:none;}',
      '.ac-titles .ac-title.warn{color:#b45309;}',
      /* 展开 = grid 0fr→1fr 的 keyframes 动画：
         ① keyframes 对「新建元素 / 同帧加类」都会真实播放（transition 需要 before 状态，
            新建元素没有 → 之前展开动画静默失效的根因）；
         ② 1fr 自动贴合内容真实高度，不量高度 → 文字永远不会被裁一半。 */
      '.ac-bodywrap{display:grid;grid-template-rows:0fr;',
      'transition:grid-template-rows .45s cubic-bezier(.22,1,.36,1),margin-top .45s cubic-bezier(.22,1,.36,1);}',
      '.agent-card.ac-expanded .ac-bodywrap{grid-template-rows:1fr;margin-top:12px;',
      'animation:acReveal .5s cubic-bezier(.22,1,.36,1);}',
      '@keyframes acReveal{from{grid-template-rows:0fr;margin-top:0;}to{grid-template-rows:1fr;margin-top:12px;}}',
      /* 改名 .ac-inner：原与「每一步的 .ac-body」同名冲突，被后面的规则覆盖成 gap:2px */
      '.ac-inner{min-height:0;overflow:hidden;display:flex;flex-direction:column;gap:12px;}',
      '.ac-head{display:flex;align-items:center;gap:11px;}',
      '.ac-orb{position:relative;width:30px;height:30px;flex:0 0 auto;border-radius:50%;',
      'background:var(--accent-grad);display:flex;align-items:center;justify-content:center;}',
      '.ac-orb svg{width:17px;height:17px;display:block;color:#06121a;}',
      '.ac-orb .ac-done-ic{display:none;}.ac-orb .ac-err-ic{display:none;}',
      '.ac-orb.ac-done .ac-run-ic{display:none;}.ac-orb.ac-done .ac-done-ic{display:block;}',
      '.ac-orb.ac-err .ac-run-ic{display:none;}.ac-orb.ac-err .ac-err-ic{display:block;background:var(--bad);}',
      '.ac-orb.ac-run{animation:acOrb 2.4s ease-in-out infinite;}',
      '@keyframes acOrb{0%,100%{box-shadow:0 0 0 0 var(--accent-soft-1);}50%{box-shadow:0 0 0 7px transparent;}}',
      '.ac-titles{display:flex;flex-direction:column;gap:1px;min-width:0;flex:1 1 auto;}',
      '.ac-title{font-weight:650;font-size:13.5px;color:var(--ink-1);line-height:1.2;}',
      '.ac-sub{font-size:11px;color:var(--ink-3);}',
      '.ac-pill{font-size:10.5px;font-weight:600;padding:3px 9px;border-radius:99px;white-space:nowrap;',
      'color:var(--accent-1);background:var(--accent-soft-1);transition:all .25s ease;}',
      '.ac-pill.ac-done{color:var(--ok);background:color-mix(in srgb,var(--ok) 16%,transparent);}',
      '.ac-pill.ac-err{color:var(--err);background:color-mix(in srgb,var(--bad) 16%,transparent);}',
      '.ac-plan{font-size:12px;line-height:1.55;color:var(--ink-2);background:var(--surface-2);',
      'border:1px solid var(--line);border-radius:var(--r-sm,8px);padding:9px 11px;',
      'max-height:160px;overflow:auto;white-space:pre-wrap;',
      /* 隐藏滚动条（长计划仍可滚）：WebView2/Chromium 与 Firefox 两种写法都要 */
      'scrollbar-width:none;-ms-overflow-style:none;}',
      '.ac-plan::-webkit-scrollbar{width:0;height:0;display:none;}',
      '.ac-plan b{color:var(--ink-1);font-weight:650;}',
      /* ——「正在看」进度区（2026-10-04 进聊天流）：标题行 + 阶段步进器，随观看进度更新 —— */
      '.ac-watch{display:flex;flex-direction:column;gap:7px;background:var(--surface-2);',
      'border:1px solid var(--line);border-radius:var(--r-sm,8px);padding:9px 11px;}',
      '.acw-top{display:flex;align-items:center;gap:7px;min-width:0;}',
      '.acw-src{width:15px;height:15px;color:var(--accent-1);flex:0 0 auto;display:inline-flex;align-items:center;}',
      '.acw-src svg{width:15px;height:15px;display:block;}',
      '.acw-title{font-size:12px;font-weight:600;color:var(--ink-1);white-space:nowrap;overflow:hidden;',
      'text-overflow:ellipsis;min-width:0;flex:1 1 auto;}',
      '.acw-pct{font-size:11px;color:var(--accent-1);font-variant-numeric:tabular-nums;flex:0 0 auto;}',
      '.acw-steps{display:flex;align-items:center;flex-wrap:nowrap;overflow:hidden;}',
      '.acw-step{display:flex;align-items:center;gap:5px;font-size:11px;line-height:1;color:var(--ink-3);white-space:nowrap;flex:0 0 auto;}',
      '.acw-step:not(:last-child)::after{content:"";width:14px;height:1.5px;background:var(--line-2);margin:0 6px;border-radius:2px;opacity:.6;}',
      '.acw-dot{width:7px;height:7px;border-radius:50%;background:var(--surface-3);border:1.5px solid var(--line-2);',
      'box-sizing:border-box;flex:0 0 auto;transition:background .25s ease,border-color .25s ease,transform .25s cubic-bezier(.34,1.56,.64,1);}',
      '.acw-step.done .acw-dot{background:var(--accent-grad);border-color:transparent;}',
      '.acw-step.done .acw-cap{color:var(--ink-2);}',
      '.acw-step.act .acw-dot{background:var(--accent-1);border-color:var(--accent-1);transform:scale(1.3);box-shadow:0 0 0 3px var(--accent-soft-1);}',
      '.acw-step.act .acw-cap{color:var(--ink-1);font-weight:600;}',
      '.acw-step.err .acw-dot{background:var(--bad);border-color:var(--bad);box-shadow:0 0 0 3px color-mix(in srgb,var(--bad) 25%,transparent);}',
      '.acw-step.err .acw-cap{color:var(--bad);}',
      '.ac-steps{display:flex;flex-direction:column;gap:9px;}',
      '.ac-step{display:flex;gap:10px;align-items:flex-start;animation:acStepIn .3s ease both;}',
      '@keyframes acStepIn{from{opacity:0;transform:translateX(-6px);}to{opacity:1;transform:none;}}',
      '.ac-badge{flex:0 0 auto;width:22px;height:22px;border-radius:50%;display:flex;align-items:center;',
      'justify-content:center;font-size:11px;font-weight:700;color:var(--ink-3);',
      'background:var(--surface-3);border:1px solid var(--line);transition:all .25s ease;}',
      '.ac-badge svg{width:13px;height:13px;display:none;}',
      /* ⚠️【2026-10-05 修静默失效】后端 openai_client 发的 state 是 **"running"**，
         而这里原只写 `.ac-step.run` ⇒ 规则永远匹配不上 ⇒ **执行中的步骤既不高亮、
         旋转环也不转**（表现为"步骤跟进没动静"）。下面给 running 补同名样式。 */
      '.ac-step.run .ac-badge,.ac-step.running .ac-badge{color:var(--accent-1);border-color:var(--line-strong);background:var(--accent-soft-1);}',
      '.ac-step.run .ac-badge .ac-run-ic,.ac-step.running .ac-badge .ac-run-ic{display:block;}',
      '.ac-step.run .ac-badge .ac-spin,.ac-step.running .ac-badge .ac-spin{display:block;transform-box:view-box;transform-origin:12px 12px;animation:acSpin .8s linear infinite;}',
      '.ac-step.done .ac-badge{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 50%,transparent);',
      'background:color-mix(in srgb,var(--ok) 14%,transparent);}',
      '.ac-step.done .ac-badge .ac-check{display:block;}',
      '.ac-step.err .ac-badge{color:var(--err);border-color:color-mix(in srgb,var(--bad) 50%,transparent);',
      'background:color-mix(in srgb,var(--bad) 14%,transparent);}',
      '.ac-step.err .ac-badge .ac-x{display:block;}',
      '@keyframes acSpin{to{transform:rotate(360deg);}}',
      /* 【23:20】后端已把 failed 归一为 err，这里再兜一层：万一拿到旧数据仍写
         `failed`，也照红叉样式渲染，不出现"无样式的一行"。 */
      '.ac-step.failed .ac-badge{color:var(--err);border-color:color-mix(in srgb,var(--bad) 50%,transparent);',
      'background:color-mix(in srgb,var(--bad) 14%,transparent);}',
      '.ac-step.failed .ac-badge .ac-x{display:block;}',
      '.ac-body{min-width:0;flex:1 1 auto;display:flex;flex-direction:column;gap:2px;}',
      '.ac-tool{font-size:12.5px;font-weight:600;color:var(--ink-1);}',
      '.ac-tool code{font-family:var(--font-mono);font-size:11px;color:var(--accent-1);',
      'background:var(--surface-2);padding:1px 6px;border-radius:6px;}',
      '.ac-result{font-size:11px;color:var(--ink-3);line-height:1.45;}',
      '.ac-progress{display:flex;align-items:center;gap:9px;}',
      '.ac-bar{flex:1 1 auto;height:4px;border-radius:99px;background:var(--surface-3);overflow:hidden;}',
      '.ac-bar i{display:block;height:100%;width:0;border-radius:99px;background:var(--accent-grad);',
      'transition:width .4s cubic-bezier(.22,.61,.36,1);}',
      /* 【23:20 修】原写 `.ac-card.ac-done` —— 卡片根元素的类是 `.agent-card`，
         `ac-card` 全仓不存在 ⇒ **完成后进度条不变绿**（设计稿：完成用绿色渐变）。 */
      '.agent-card.ac-done .ac-bar i{background:linear-gradient(90deg,var(--ok),color-mix(in srgb,var(--ok) 40%,var(--accent-1)));}',
      '.agent-card.ac-warn .ac-bar i{background:linear-gradient(90deg,#d97706,#f59e0b);}',
      /* 【23:20 修】orb 三态图标互斥。原 CSS 用 `.ac-done-ic` / `.ac-err-ic` 控制显隐，
         但 SVG 上根本没有这两个类（只有 ac-run-ic / ac-check / ac-x）⇒ 规则全落空，
         **执行中/完成态会把 旋转环 + 勾 + 叉 三个图标叠在一起显示**。
         下面给 check/x 补上对应类，并补 warn（琥珀感叹号）图标。 */
      '.ac-orb .ac-warn-ic{display:none;}',
      '.ac-orb.ac-warn{background:linear-gradient(135deg,#d97706,#f59e0b);}',
      '.ac-orb.ac-warn .ac-warn-ic{display:block;}',
      '.ac-count{font-size:11px;color:var(--ink-3);font-variant-numeric:tabular-nums;white-space:nowrap;}',
      /* 【2026-10-05 治"卡片一闪就没"·退场】状态卡被回复接管时不再"啪"地消失：
         先量一次真实高度写进 style.height，再过渡到 0（配合淡出+左移），
         让"卡片退场 → 回复补位"是平滑的，而不是行突然抽走造成的跳动。 */
      '.row.ac-exit{overflow:hidden;pointer-events:none;',
      'transition:height .38s cubic-bezier(.4,0,.2,1),opacity .26s ease,',
      'margin .38s cubic-bezier(.4,0,.2,1),padding .38s cubic-bezier(.4,0,.2,1),',
      'transform .38s cubic-bezier(.4,0,.2,1);}',
      '.row.ac-exit.ac-exit-go{opacity:0;transform:translateX(-10px);}',
      /* ===== 2026-10-08 移植设计稿：GSAP 动效层（仅 window.gsap 且非减少动态时启用） ===== */
      /* orb 液态同心圆涟漪（执行中常驻）：配合 .agent-card{overflow:visible} 让辉光自然溢出玻璃卡 */
      '.ac-orb .ac-ripple{position:absolute;inset:0;border-radius:50%;border:1.6px solid var(--accent-1);opacity:0;pointer-events:none;}',
      /* 悬停光斑：鼠标在卡上移动时一层极淡高光跟随（高级感常用手法） */
      '.agent-card .ac-spot{position:absolute;inset:0;border-radius:inherit;pointer-events:none;opacity:0;',
      'transition:opacity .35s ease;',
      'background:radial-gradient(240px circle at var(--mx,50%) var(--my,0%),color-mix(in srgb,var(--accent-1) 9%,transparent),transparent 62%);}',
      '.agent-card:hover .ac-spot{opacity:1;}',
      /* 结果行滑入（非 GSAP 时 CSS 兜底；gsap-on 下禁用，改由 GSAP 接管） */
      '.ac-result{animation:acResultIn .45s cubic-bezier(.22,.61,.36,1) both;}',
      '@keyframes acResultIn{from{opacity:0;transform:translate(-6px,4px);}to{opacity:1;transform:none;}}',
      /* 进度条 idle：尚无步骤时填充为 0、高光看不见 → 流光扫整条槽作"准备中"常驻动效 */
      '.ac-bar{position:relative;}',
      '.ac-bar::after{content:"";position:absolute;inset:0;pointer-events:none;',
      'background:linear-gradient(100deg,transparent 30%,rgba(255,255,255,.4) 50%,transparent 70%);',
      'background-size:200% 100%;transform:translateX(-110%);opacity:0;}',
      '.ac-bar.idle::after{opacity:1;animation:acSweep 1.6s ease-in-out infinite;}',
      '.gsap-on .ac-bar i{transition:none!important;}',
      '@keyframes acSweep{to{transform:translateX(110%);}}',
      /* 进度条 / 卡片完成瞬间闪一下定格（一次性），替代"填满后毫无反应" */
      '.ac-bar.settle i{animation:acBarSettle .9s ease-out 1;}',
      '@keyframes acBarSettle{0%{filter:brightness(1.6) saturate(1.35);}100%{filter:brightness(1);}}',
      '.agent-card.ac-settled::before{animation:acEdgeSweep 1.1s ease-out 1;}',
      '.agent-card.ac-warn.ac-settled::before{animation:acEdgeSweep 1.1s ease-out 1;}',
      '.agent-card.ac-err.ac-settled::before{animation:acErrEdgeSweep 1.1s ease-out 1;}',
      '@keyframes acEdgeSweep{0%{filter:brightness(2.4);box-shadow:0 0 14px var(--ok);}100%{filter:brightness(1);box-shadow:none;}}',
      '@keyframes acErrEdgeSweep{0%{filter:brightness(2.4);box-shadow:0 0 14px var(--bad);}100%{filter:brightness(1);box-shadow:none;}}',
      /* ——「计划即步骤清单」脊柱：把模型计划文本解析成待办行，执行时就地点亮（零位移） —— */
      '.ac-pstep{position:relative;display:flex;gap:10px;align-items:flex-start;}',
      '.ac-pbody{min-width:0;flex:1 1 auto;display:flex;flex-direction:column;gap:1px;padding-top:1px;}',
      '.ac-plabel{font-size:12.5px;line-height:1.45;color:var(--ink-3);transition:color .3s ease;}',
      '.ac-pdetail{font-size:11.5px;color:var(--ink-3);line-height:1.4;}',
      '.ac-pdetail code{font-family:var(--font-mono);font-size:11px;color:var(--accent-1);background:var(--surface-2);padding:1px 6px;border-radius:6px;}',
      '.ac-pargs{color:var(--ink-3);}',
      '.ac-presult{font-size:11px;color:var(--ink-3);line-height:1.45;}',
      '.ac-pnum{font-size:11px;font-weight:700;color:var(--ink-3);}',
      '.ac-pstep.exec .ac-plabel{color:var(--ink-1);font-weight:600;}',
      '.ac-pstep.done .ac-plabel{color:var(--ink-2);}',
      '.ac-pstep.run .ac-badge{color:var(--accent-1);border-color:var(--line-strong);background:var(--accent-soft-1);}',
      '.ac-pstep.run .ac-badge .ac-spin{display:block;transform-box:view-box;transform-origin:12px 12px;animation:acSpin .8s linear infinite;}',
      '.ac-pstep.done .ac-badge{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 50%,transparent);background:color-mix(in srgb,var(--ok) 14%,transparent);}',
      '.ac-pstep.done .ac-badge .ac-check{display:block;}',
      '.ac-pstep.err .ac-badge{color:var(--err);border-color:color-mix(in srgb,var(--bad) 50%,transparent);background:color-mix(in srgb,var(--bad) 14%,transparent);}',
      '.ac-pstep.err .ac-badge .ac-x{display:block;}',
      /* 脊柱徽标里的 SVG 在 .ac-badge svg{display:none} 规则下默认隐藏；
         这里按状态显隐（run 的 .ac-run-ic 也需显式 display:block，否则旋转环父级不可见） */
      '.ac-pstep .ac-badge svg{width:14px;height:14px;}',
      '.ac-pstep.run .ac-badge .ac-run-ic{display:block;}',
      /* gsap-on：GSAP 接管后关掉 CSS 入场关键帧，避免双动画打架（保留 .ac-expanded 的 grid 展开） */
      '.gsap-on .ac-step{animation:none!important;}',
      '.gsap-on .ac-result{animation:none!important;}',
      '.gsap-on .vw-title{animation:none!important;}',
      /* 【2026-10-11·P1 里程碑】她首次跨过关系阶段时的那句"有分量的话"——
         渲染成一条**特殊气泡**（不是普通聊天气泡，也不是执行卡）：左侧留一点暖色竖条 +
         低调的"关系质变"小标 + 引用感的排版。反 PUA：不出现任何数字/进度/进度条。 */
      '.row.milestone-row .bub{position:relative;background:color-mix(in srgb,var(--accent-1) 8%,var(--surface-2));',
      'border:1px solid color-mix(in srgb,var(--accent-1) 30%,var(--line));border-radius:var(--r-lg,16px);',
      'padding:13px 16px 13px 18px;box-shadow:none;max-width:100%;}',
      '.row.milestone-row .bub::before{content:"";position:absolute;left:0;top:10px;bottom:10px;width:3px;border-radius:3px;',
      'background:linear-gradient(180deg,var(--accent-1),color-mix(in srgb,var(--accent-1) 35%,var(--accent-2,#7c5cff)));}',
      '.row.milestone-row .bub .ms-cap{display:block;font-size:11.5px;letter-spacing:.02em;color:var(--accent-1);',
      'margin-bottom:5px;font-weight:500;}',
      '.row.milestone-row .bub .ms-text{display:block;font-size:14px;line-height:1.7;color:var(--ink-1);}',
      '.row.milestone-row .meta .nm{color:var(--accent-1);}',
      '@media (prefers-reduced-motion: reduce){',
      '.agent-card{animation:none!important;}.ac-step{animation:none!important;}',
      '.ac-orb.ac-run{animation:none;}.ac-badge .ac-spin{animation:none;display:block;}',
      '.ac-bodywrap{transition:none!important;}.agent-card.ac-expanded .ac-bodywrap{animation:none;}}'
    ].join('');
    var IC = {
      run: '<svg class="ac-run-ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path class="ac-spin" d="M12 3a9 9 0 0 1 9 9" stroke="currentColor"/></svg>',
      /* ac-done-ic / ac-err-ic / ac-warn-ic 是「圆球 orb」里的三态互斥开关类
         （23:20 补：原 SVG 缺这几个类 ⇒ .ac-orb 的显隐规则全部落空、图标重叠） */
      check: '<svg class="ac-check ac-done-ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7"/></svg>',
      x: '<svg class="ac-x ac-err-ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>',
      warn: '<svg class="ac-warn-ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 7.5v6"/><circle cx="12" cy="17" r="1.15" fill="currentColor" stroke="none"/></svg>'
    };
    /* 「正在看」阶段/图标（2026-10-04 观看进度进聊天流，与悬浮气泡 __videoWatch 同源） */
    var WATCH_ICONS = {
      video: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="3"/><path d="M10 9l5 3-5 3z" fill="currentColor" stroke="none"/></svg>',
      link:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 1 0-5.66-5.66L11.5 7"/><path d="M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 1 0 5.66 5.66L12.5 17"/></svg>',
      image: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="3"/><circle cx="8.5" cy="9.5" r="1.6" fill="currentColor" stroke="none"/><path d="M21 16l-5-5-4 4-2-2-7 7"/></svg>'
    };
    var WATCH_STAGES = {
      video: [['resolve','解析'],['download','下载'],['frame','抽帧'],['describe','看画面']],
      link:  [['page','读取']],
      image: [['download','下载'],['describe','看画面']]
    };
    function esc(s){ return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
    function argsSummary(args){
      if (!args || typeof args !== 'object') return '';
      var parts = [];
      for (var k in args){ if (Object.prototype.hasOwnProperty.call(args, k)){
        var v = args[k];
        if (typeof v === 'object') v = JSON.stringify(v);
        parts.push(k + '=' + v);
      }}
      var s = parts.join(' · ');
      return s.length > 80 ? s.slice(0, 80) + '…' : s;
    }
    /* 【23:20】状态归一化：后端旧数据/其它链路可能仍写 `failed`，而本卡片的 CSS
       只有 `.ac-step.err`（红叉 + 红字）。不归一的话增量更新会把 className 写成
       `ac-step failed` ⇒ **红叉样式整段丢失**，且不计入进度。 */
    function normState(s){ s = s || 'pending'; return (s === 'failed') ? 'err' : s; }
    function isFailState(s){ s = s || ''; return (s === 'err' || s === 'failed'); }
    /* ===== GSAP 编排函数（全部 if(M) 守卫；M 为空则无操作，退回纯 CSS） ===== */
    /* 入场错峰 + 上浮物理落定 */
    function acEnter(card){
      if (!M || !card) return;
      M.fromTo(card, { opacity:0, y:22, scale:.94 }, { opacity:1, y:0, scale:1, duration:.6, ease:'back.out(2)',
        onComplete:function(){ M.to(card, { y:-5, duration:.5, ease:'sine.inOut', yoyo:true, repeat:1 }); } });
      _acSafeReveal(card);
      var parts = card.querySelectorAll('.ac-head,.ac-steps,.ac-progress');
      if (parts.length) M.from(parts, { opacity:0, y:10, duration:.55, stagger:.09, ease:'power3.out', delay:.12 });
      bindSpotlight(card);
    }
    /* 结果行滑入 */
    function acResultIn(el){
      if (!el) return;
      if (M) M.fromTo(el, { opacity:0, x:-6, y:4 }, { opacity:1, x:0, y:0, duration:.45, ease:'power3.out', delay:.06 });
    }
    /* 完成"落定"：整卡轻微过冲后弹回 + 顶边高光扫 + 进度条闪一下 */
    function acSettle(card){
      if (!M || !card) return;
      card.classList.add('ac-settled');
      var bar = card.querySelector('.ac-bar'); if (bar){ bar.classList.add('settle'); setTimeout(function(){ if (bar) bar.classList.remove('settle'); }, 1000); }
      M.fromTo(card, { scale:1 }, { scale:1.012, duration:.18, ease:'power2.out',
        onComplete:function(){ M.to(card, { scale:1, duration:.5, ease:'elastic.out(1,0.6)',
          onComplete:function(){ card.classList.remove('ac-settled'); } }); } });
    }
    /* 悬停光斑：鼠标位置写进 CSS 变量，交给 .ac-spot 的径向渐变 */
    function bindSpotlight(card){
      if (!card || card.__acSpot) return; card.__acSpot = true;
      card.insertAdjacentHTML('beforeend', '<span class="ac-spot"></span>');
      card.addEventListener('mousemove', function(e){
        var r = card.getBoundingClientRect();
        card.style.setProperty('--mx', (((e.clientX - r.left) / r.width) * 100) + '%');
        card.style.setProperty('--my', (((e.clientY - r.top) / r.height) * 100) + '%');
      });
    }
    /* 步骤滑入 + 完成描边打勾 */
    function acStepIn(node, stt){
      if (!M || !node) return;
      M.from(node, { opacity:0, y:8, scale:.96, duration:.45, ease:'back.out(1.8)' });
      if (stt === 'done'){
        var badge = node.querySelector('.ac-badge');
        if (badge) M.fromTo(badge, { scale:.5 }, { scale:1, duration:.5, ease:'back.out(3)' });
        var ck = node.querySelector('.ac-check'); if (ck){ var p = ck.querySelector('path'); if (p){ try {
          var L = p.getTotalLength(); M.set(p, { strokeDasharray:L, strokeDashoffset:L }); M.to(p, { strokeDashoffset:0, duration:.5, ease:'power2.out' }); } catch(e){} } }
      }
    }
    /* 执行中：液态同心圆扩散 + 常驻上下浮动（完成前一直一上一下） */
    function acAlive(orb){
      if (!M || !orb) return;
      M.to(orb, { y:-3, scale:1.05, duration:1.7, repeat:-1, yoyo:true, ease:'sine.inOut', transformOrigin:'50% 50%' });
      var rings = orb.querySelectorAll('.ac-ripple');
      if (rings.length){ rings.forEach(function(r,i){ M.fromTo(r, { scale:1, opacity:.55 }, { scale:2.4, opacity:0, duration:2.2, ease:'sine.out', repeat:-1, delay:i*0.7 }); }); }
      var run = orb.querySelector('.ac-run-ic path'); if (run){ M.to(run, { strokeDashoffset:-44, duration:1.4, repeat:-1, ease:'none' }); }
    }
    /* 完成/出错：停止呼吸与涟漪，orb 回弹归位 */
    function acStop(orb){
      if (!M || !orb) return;
      var rs = orb.querySelectorAll('.ac-ripple');
      M.killTweensOf(orb); M.killTweensOf(rs);
      M.to(orb, { scale:1, y:0, duration:.5, ease:'back.out(2)' });
      for (var i=0;i<rs.length;i++){ M.set(rs[i], { opacity:0, scale:1 }); }
    }
    /* 进度 elastic 缓动 + 数字滚动（无数字时退回纯文本）。
       注意：必须用「持久对象 + killTweensOf」杀掉上一帧尚未跑完的 tween，
       否则上一帧的数字滚动 onUpdate 会覆盖本帧刚写入的终值（done 帧本该显示「完成」却被旧 tween 写回「第 1/3 步」）。 */
    function acBar(bar, pct, countEl, countTxt){
      if (!M){
        if (bar) bar.style.width = pct + '%';
        if (countEl) countEl.textContent = countTxt;
        return;
      }
      if (bar){ M.killTweensOf(bar); M.to(bar, { width:pct + '%', duration:.7, ease:'elastic.out(1,0.78)' }); }
      if (countEl){
        var _mm = String(countTxt).match(/^(.*?)(\d+)([\s\S]*)$/);
        if (!_mm){ if (countEl.__roll) M.killTweensOf(countEl.__roll); countEl.__v = null; countEl.textContent = countTxt; return; }
        var _from = (countEl.__v == null ? 0 : countEl.__v), _tgt = parseInt(_mm[2], 10);
        var _o = countEl.__roll || (countEl.__roll = { v:_from });
        _o.v = _from;
        M.killTweensOf(_o);
        M.to(_o, { v:_tgt, duration:.6, ease:'power1.out',
          onUpdate:function(){ countEl.textContent = _mm[1] + Math.round(_o.v) + _mm[3]; },
          onComplete:function(){ countEl.textContent = countTxt; countEl.__v = _tgt; } });
      }
    }
    /* 把模型「计划」文本解析成清单条目（去 计划：前缀 + emoji，按编号切分；无编号按尾部兜底；上限 8） */
    function planItems(plan){
      var t = String(plan || '').replace(/📋/g, '').replace(/计划[:：]?/, '');
      var re = /\b\d{1,2}\s*[)）.．、]/g, out = [], last = 0, m;
      while ((m = re.exec(t))){
        var _seg = t.slice(last, m.index).trim(); if (_seg) out.push(_seg); last = re.lastIndex;
      }
      var _tail = t.slice(last).trim(); if (_tail) out.push(_tail);
      if (!out.length) out = [t.trim()].filter(Boolean);
      return out.slice(0, 8);
    }
    /* 待办清单行（pending）：真实计划文本 + 空心编号徽标 */
    function pstepDom(item, i){
      return '<div class="ac-pstep" data-i="' + i + '"><span class="ac-badge"><span class="ac-pnum">' + (i+1) + '</span></span>' +
        '<span class="ac-pbody"><span class="ac-plabel">' + esc(item) + '</span></span></div>';
    }
    /* 就地点亮清单行：pending → running → done/err；不增删行（零位移）；状态变化才做徽标弹跳 + 子行滑入 */
    function morphPstep(row, step, i){
      if (!row) return;
      var stt = normState(step.state);
      var badge = row.querySelector('.ac-badge');
      var changed = (row.__acCls !== stt);
      row.__acCls = stt;
      row.className = 'ac-pstep' + (stt === 'pending' ? '' : (' exec ' + stt));
      var wantBadge = stt === 'done' ? IC.check : (stt === 'err' ? IC.x : (stt === 'run' ? IC.run : '<span class="ac-pnum">' + (i+1) + '</span>'));
      if (badge.innerHTML !== wantBadge) badge.innerHTML = wantBadge;
      badge.className = 'ac-badge' + (stt === 'pending' ? '' : (' ' + stt));
      var body = row.querySelector('.ac-pbody');
      var tt = esc(step.tool || '操作'), as = argsSummary(step.args);
      var dhtml = '<code>' + tt + '</code>' + (as ? ' <span class="ac-pargs">' + esc(as) + '</span>' : '');
      var detail = row.querySelector('.ac-pdetail');
      if (!detail){ detail = document.createElement('span'); detail.className = 'ac-pdetail'; body.appendChild(detail); }
      if (detail.__h !== dhtml){ detail.innerHTML = dhtml; detail.__h = dhtml;
        if (M) M.fromTo(detail, { opacity:0, y:-4 }, { opacity:1, y:0, duration:.4, ease:'power3.out' }); }
      if ((stt === 'done' || stt === 'err') && step.result){
        var res = row.querySelector('.ac-presult');
        if (!res){ res = document.createElement('span'); res.className = 'ac-presult'; body.appendChild(res); }
        var rh = '↳ ' + esc(step.result);
        if (res.__h !== rh){ res.textContent = rh; res.__h = rh; acResultIn(res); }
      }
      if (changed && M){
        if (stt === 'done'){ var ck = badge.querySelector('.ac-check path'); if (ck){ try {
          var L = ck.getTotalLength(); M.set(ck, { strokeDasharray:L, strokeDashoffset:L }); M.to(ck, { strokeDashoffset:0, duration:.5, ease:'power2.out' }); } catch(e){} } }
        M.fromTo(badge, { scale:.4 }, { scale:1, duration:.5, ease:'back.out(3)' });
      }
    }
    /* 单步注入：脊柱卡按 data-i 点亮对应待办行；否则（无脊柱 / 计划步数<实际）追加 .ac-step */
    function pumpOne(bub, el, step, idx){
      var st = bub.__acState;
      if (st && st.hasSpine && st.planItems && idx < st.planItems.length){
        var row = el.querySelector('.ac-pstep[data-i="' + idx + '"]');
        if (row){ morphPstep(row, step, idx); return; }
      }
      el.insertAdjacentHTML('beforeend', stepDom(step, idx));
      var _nd = el.lastElementChild;
      _nd.__acCls = normState(step.state);
      if (M && _nd.__acCls !== 'pending') acStepIn(_nd, _nd.__acCls);
    }
    function stepDom(step, idx){
      var state = normState(step.state);
      /* 【2026-10-04 20:12】失败步骤用红叉 + 原因，不与"完成"同色 */
      var isFail = isFailState(state);
      var badge = isFail ? IC.x : (state === 'done' ? IC.check : (state === 'running' ? IC.run : '<span>' + (idx + 1) + '</span>'));
      /* 【2026-10-05】观看卡的步骤由后端带中文 label（解析链接/下载视频…），
         优先用 label；操作卡沿用 tool 名。 */
      var tool = esc(step.label || step.tool || '操作');
      var as = argsSummary(step.args);
      var res = ((state === 'done' || isFail) && step.result) ? '<div class="ac-result">' + esc(step.result) + '</div>' : '';
      if (isFail && step.reason && !step.result){
        res = '<div class="ac-result" style="color:#b91c1c">↳ 未达成：' + esc(step.reason) + '</div>';
      }
      return '<div class="ac-step ' + (isFail ? 'err' : state) + '">' +
        '<span class="ac-badge">' + badge + '</span>' +
        '<span class="ac-body"><span class="ac-tool"><code>' + tool + '</code>' +
        (as ? ' <span style="color:var(--ink-3)">' + esc(as) + '</span>' : '') + '</span>' + res + '</span></div>';
    }

    /* 【2026-10-04 20:12 治"假完成"】由 agent_meta 算出卡片该显示什么。
       关键：`done` **不等于成功**——上游会带 partial / failed_tools，
       有失败时用琥珀警告色 +「未全部完成」+ 点名卡住的工具。
       20:03 实锤：什么都没播成，UI 却显示绿色「完成」，用户被误导。 */
    function acState(meta){
      meta = meta || {};
      var done = !!meta.done, err = !!meta.error;
      var failed = meta.failed_tools || [];
      var partial = !!meta.partial || (failed.length > 0);
      var ftxt = failed.slice(0, 3).join('、');
      /* 【2026-10-05】观看卡（一起看视频）专属文案 —— 之前无论完成没完成都走
         通用分支，用户看到的是「达妮娅正在操作电脑 / 执行中」并永远停在执行中
         （04:17 实锤）。这里按 watch 状态给出正确标题/副标题/徽章。 */
      var wk = meta.watch || null;
      if (wk){
        var kindTxt = (wk.kind === 'image') ? '图片' : '视频';
        /* 后端 video_watch 阶段顺序：resolve→download→frame→describe→done
           （见 video_watch.py:_prog）。旧 map 写成了 frames（复数）且漏了 describe，
           导致 frame/describe 两阶段都命中不到 → sub 回退到 wk.message（"正在看画面，
           稍等一下～"），看起来像"一直卡在看画面"。这里补齐 frame/describe 正确文案。 */
        var stageTxt = { resolve: '正在解析链接', download: '正在下载',
                         frame: '正在抽帧', describe: '正在看画面', done: '已看完' }[wk.stage] || '';
        if (err){
          /* 诚实降级提示：若标题已抓到，说明"只看标题"仍可用，明确告知用户已降级，
             避免笼统的"解析失败"让用户以为什么都没拿到（抖音等链接常见部分失败）。 */
          var _degr = wk.title ? '（已降级为只看标题）' : '';
          return { cls: ' ac-err', orb: 'ac-err', pillCls: 'ac-err',
                   title: '达妮娅没能看完这个' + kindTxt,
                   sub: (wk.message ? (wk.message + _degr) : ('链接解析失败，稍后再试' + _degr)), pill: '出错' };
        }
        if (done){
          var nSteps = (meta.steps || []).length;
          return { cls: ' ac-done', orb: 'ac-done', pillCls: 'ac-done',
                   title: '达妮娅看完这个' + kindTxt + '了',
                   sub: (wk.title ? ('「' + String(wk.title).slice(0, 24) + '」') : '') ||
                        ('已完成' + nSteps + '步'),
                   pill: '已看完' };
        }
        return { cls: '', orb: 'ac-run', pillCls: '',
                 title: '达妮娅正在一起看' + kindTxt,
                 sub: (stageTxt || wk.message || '马上开始'), pill: '执行中' };
      }
      if (err) return { cls: ' ac-err', orb: 'ac-err', pillCls: 'ac-err',
                        title: '达妮娅操作电脑时出错了', sub: '没能完成，请看下面说明',
                        pill: '出错' };
      if (done && partial) return { cls: ' ac-warn', orb: 'ac-warn', pillCls: 'ac-warn',
                        title: '达妮娅没能完全搞定', sub: (ftxt ? ('卡在：' + ftxt) : '部分步骤未达成'),
                        pill: '未全部完成' };
      if (done) return { cls: ' ac-done', orb: 'ac-done', pillCls: 'ac-done',
                        title: '达妮娅完成了操作', sub: 'AI 自主调用工具完成任务', pill: '完成' };
      return { cls: '', orb: 'ac-run', pillCls: '',
               title: '达妮娅正在操作电脑', sub: 'AI 自主调用工具完成任务', pill: '执行中' };
    }
    /* —— 快节奏兜底（2026-10-05）——
       短操作（open_app 之类 200~600ms 就返回）会让卡片"出生即死亡"：
       展开动画（约 .5s）+ 步骤滑入 + 终态打勾 根本来不及演完就被回复接管删掉，
       观感 = "闪一下就没了、UI 跟不上"。三层兜底：
         ① AC_MIN_VISIBLE：卡片最短在屏时间（与后端 _AGENT_CARD_MIN_LIFE 对齐），
            未到点不让步骤区收起；
         ② AC_STEP_GAP：同一轮 poll 里同时到达的步骤**逐条**滑入，进度跟得上眼睛；
         ③ acRowExit()：被接管时平滑退场，不做"行突然抽走"的跳动。 */
    var AC_MIN_VISIBLE = 2800;   // ms，卡片最短在屏时间（与后端 _AGENT_CARD_MIN_LIFE 对齐）
    var AC_STEP_GAP = 320;       // ms，相邻两步的最小间隔
    var AC_EXIT_MS = 420;        // ms，退场动画时长
    var AC_READ_AFTER_DONE = 2000;  // ms，终态（完成/出错）后额外停留，确保看清打勾/摘要
    var AC_SCENARIO_LIVE = 9000;    // ms，状态卡在聊天流内最短存活（defer 退场判定用），保证「分镜播放」演完
    /* 退场：先固定高度再收，避免瞬间塌陷 */
    function acRowExit(row, done){
      try {
        if (!row || !row.parentNode){ if (done) done(); return; }
        if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches){
          row.parentNode.removeChild(row); if (done) done(); return;
        }
        var h = row.offsetHeight || 0;
        row.style.overflow = 'hidden';
        if (h) row.style.height = h + 'px';
        row.classList.add('ac-exit');
        requestAnimationFrame(function(){
          requestAnimationFrame(function(){
            row.style.height = '0px';
            row.style.marginTop = '0px';
            row.style.marginBottom = '0px';
            row.style.paddingTop = '0px';
            row.style.paddingBottom = '0px';
            row.classList.add('ac-exit-go');
          });
        });
        setTimeout(function(){
          if (row.parentNode) row.parentNode.removeChild(row);
          if (done) done();
        }, AC_EXIT_MS);
      } catch (e) {
        try { if (row && row.parentNode) row.parentNode.removeChild(row); } catch (e2) {}
        if (done) done();
      }
    }
    /* 【2026-10-08 对齐设计稿"分镜播放"】状态卡行在展开分镜窗口内不被回复对账删除：
       让"计划 + 逐条勾选"演完再退场（治"快任务 0.2s 就消失"）。返回 true = 已安排延时退场。 */
    function deferStatusCardExit(row){
      if (!row || !row.classList || !row.classList.contains('status-card')) return false;
      var bs = row.querySelector('.bub');
      var st = bs && bs.__acState;
      if (st && st.liveUntil && Date.now() < st.liveUntil && !row.__acExitScheduled){
        row.__acExitScheduled = true;
        var _remain = st.liveUntil - Date.now();
        setTimeout(function(){ if (row && row.parentNode) acRowExit(row, null); }, _remain + 250);
        return true;
      }
      return false;
    }
    /* 步骤状态同步（与节奏化插入解耦：只更新已上屏节点） */
    function acSyncSteps(bub, steps){
      var stepsEl = bub.querySelector('.ac-steps');
      if (!stepsEl) return;
      var nodes = stepsEl.children;
      for (var j = 0; j < nodes.length && j < steps.length; j++){
        var node = nodes[j];
        if (node.classList.contains('ac-pstep')){
          /* 脊柱行：就地点亮（只改状态，不增删行 → 零位移） */
          morphPstep(node, steps[j], j);
        } else {
          var ns = normState(steps[j].state);
          if (node.className.indexOf(' ' + ns) === -1 && node.className !== ('ac-step ' + ns)){
            node.className = 'ac-step ' + ns;
            if ((ns === 'done' || ns === 'err') && steps[j].result){
              var bd = node.querySelector('.ac-body');
              if (bd && !bd.querySelector('.ac-result'))
                bd.insertAdjacentHTML('beforeend', '<div class="ac-result">' + (ns === 'err' ? '↳ 未达成：' : '↳ ') + esc(steps[j].result) + '</div>');
            }
          }
        }
      }
    }
    /* 节奏化播放：把"同时到达"的步骤摊开成逐条滑入；终态(flush)时立即补完 */
    function acPump(bub, flush){
      var st = bub.__acState;
      if (!st || !st.queue) return;
      var el = bub.querySelector('.ac-steps');
      if (!el) return;
      if (flush){
        while (st.queue.length){
          var f = st.queue.shift();
          pumpOne(bub, el, f.step, f.idx);
        }
        if (st.pumpTimer){ clearTimeout(st.pumpTimer); st.pumpTimer = null; }
        st.pumping = false;
        acSyncSteps(bub, st.allSteps || []);
        return;
      }
      if (st.pumping) return;
      st.pumping = true;
      var tick = function(){
        var item = st.queue.shift();
        var e2 = bub.querySelector('.ac-steps');
        if (item && e2) pumpOne(bub, e2, item.step, item.idx);
        acSyncSteps(bub, st.allSteps || []);
        if (st.queue.length){ st.pumpTimer = setTimeout(tick, AC_STEP_GAP); }
        else { st.pumping = false; st.pumpTimer = null; }
      };
      /* 首条稍等 240ms：让"卡片展开"先演完，步骤再进场，层次才清楚 */
      st.pumpTimer = setTimeout(tick, st.firstPaint ? 240 : 0);
      st.firstPaint = false;
    }
    /* 收起后的小卡片摘要：让缩回状态也"有话说"（做了什么，而不是一句套话） */
    function acSummary(meta){
      try {
        var st = (meta && meta.steps) || [];
        var got = [];
        for (var i = 0; i < st.length; i++){
          var s = st[i] || {};
          var r = String(s.result || '').trim();
          if (r && got.indexOf(r) === -1) got.push(r);
        }
        if (!got.length) return '';
        var out = got.slice(0, 2).join(' · ');
        if (got.length > 2) out += ' 等 ' + got.length + ' 项';
        return out.length > 34 ? out.slice(0, 34) + '…' : out;
      } catch (e) { return ''; }
    }
    /* —— 展开 ——
       keyframes 动画对新建元素 / 同帧加类都会真实播放，且 1fr 自动贴合内容高度，
       无需 JS 量高度（旧的「量高度写 px」方案会因取整/内容增长把最后一行文字裁一半）。 */
    function acExpand(bub){
      var c = bub && bub.querySelector('.agent-card');
      if (!c || !bub.__acState || bub.__acState.expanded) return;
      bub.__acState.expanded = true;
      /* 【23:20 治"展开动画没生效"】卡片 DOM 是本次 render 才 innerHTML 出来的，
         同一帧内立刻加 `ac-expanded` 时部分内核（含 WebView2 某些版本）会把
         keyframes 折叠掉、直接跳到终态（视觉上就是"没有展开动画、一出来就是
         最终样子"）。与 __videoWatch 气泡同一招：**下一帧再加类**，
         让浏览器先以折叠态完成一次样式计算，keyframes 才会真实播放。 */
      if (bub.__acState.defer && typeof requestAnimationFrame === 'function'){
        bub.__acState.defer = false;
        requestAnimationFrame(function(){
          requestAnimationFrame(function(){
            var cc = bub.querySelector('.agent-card');
            if (cc) cc.classList.add('ac-expanded');
          });
        });
        return;
      }
      c.classList.add('ac-expanded');
    }
    function render(bub, meta, isDone){
      try {
        if (!bub || !meta) return;
        __daniInjectStyle('agentCardStyle', CSS);
        var first = !bub.__acBuilt;
        var done = !!meta.done, err = !!meta.error;
        var expandLater = false;
        /* 【2026-10-05】历史卡（已完成、刷新后仍在）：直接以终态渲染，
           不展开步骤区、不排滑入动画、不排收起定时器 —— 保持稳定静态。 */
        if (isDone) done = true;
        if (first){
          /* 关键：不把 ac-expanded 拼进初始 HTML。元素「出生即展开」就没有 before 状态，
             transition 永远不会跑 —— 必须先以折叠态上屏，下一帧再展开。 */
          /* 【2026-10-04 20:12 治"假完成"】done 不再直接渲染成绿色「完成」：
             当有工具失败（meta.partial）时用**警告色 +「未全部完成」**，
             并把失败的工具名写进副标题，不再"什么都没做成却显示完成"。 */
          var _st = acState(meta);
          /* 计划即待办清单（脊柱）：把模型 plan 文本解析成 N 行待办，执行时就地点亮（零位移）。
             解析出 ≥1 行才建脊柱；否则回退旧 .ac-plan 灰面板（兜底，极少触发）。 */
          var _its = (meta.plan ? planItems(meta.plan) : []);
          var _spineHtml = _its.length ? _its.map(function(it, i){ return pstepDom(it, i); }).join('') : '';
          var _planHtml = (_its.length ? '' : (meta.plan ? '<div class="ac-plan">' + esc(meta.plan) + '</div>' : ''));
          bub.innerHTML =
            '<div class="agent-card' + (_st.cls) + '">' +
              '<div class="ac-head">' +
                '<span class="ac-orb ' + (_st.orb) + '">' +
                  IC.run + IC.check + IC.x + IC.warn +
                  '<span class="ac-ripple"></span><span class="ac-ripple"></span>' +
                '</span>' +
                '<span class="ac-titles"><span class="ac-title">' + (_st.title) + '</span>' +
                  '<span class="ac-sub">' + (_st.sub) + '</span></span>' +
                '<span class="ac-pill ' + (_st.pillCls) + '">' +
                  (_st.pill) + '</span>' +
              '</div>' +
              '<div class="ac-bodywrap"><div class="ac-inner">' +
                _planHtml +
                '<div class="ac-steps">' + _spineHtml + '</div>' +
                '<div class="ac-progress"><span class="ac-bar"><i></i></span>' +
                  '<span class="ac-count"></span></div>' +
              '</div></div>' +
            '</div>';
          bub.__acBuilt = true;
          /* defer:true → 首帧展开走 requestAnimationFrame，保证 keyframes 真的播放 */
          bub.__acState = { plan: !!meta.plan, steps: 0, expanded: false, defer: true,
                            queue: [], allSteps: meta.steps || [], firstPaint: true,
                            liveUntil: Date.now() + AC_SCENARIO_LIVE,
                            spineBuilt: _its.length > 0, planItems: _its, hasSpine: _its.length > 0 };
          bub.__acBorn = Date.now();   // 用于"最短在屏时间"计算
          expandLater = !isDone && !!(meta.plan || (meta.steps && meta.steps.length) || meta.watch);
          /* 入场动画 + orb 常驻呼吸（仅 GSAP 启用时；M 为空则纯 CSS，无副作用） */
          var _card0 = bub.querySelector('.agent-card');
          var _orb0 = _card0 && _card0.querySelector('.ac-orb');
          acEnter(_card0);
          if (_orb0 && _st.orb === 'ac-run') acAlive(_orb0);
        } else {
          var card = bub.querySelector('.agent-card');
          if (card){
            var _st2 = acState(meta);
            /* ★【2026-10-08 移植期暴露的老 bug】原实现整串覆盖 className：
               `card.className = 'agent-card' + _st2.cls` ⇒ 把运行期动态类一并抹掉。
               其中 `ac-expanded` 是"展开态"的唯一载体，被抹掉后 .ac-bodywrap 退回
               grid-template-rows:0fr ⇒ 脊柱待办区塌陷、卡片"神秘缩回小长条"，
               而且 acExpand 有 `expanded` 幂等守卫、不会再加回来 ⇒ 永久塌陷。
               现改为【保留动态类 + 只替换状态类】。ac-settled 同理（避免 render 打断落定回弹）。 */
            var _keep = '';
            if (card.classList.contains('ac-expanded')) _keep += ' ac-expanded';
            if (card.classList.contains('ac-settled')) _keep += ' ac-settled';
            card.className = 'agent-card' + _st2.cls + _keep;
            var orb = card.querySelector('.ac-orb');
            orb.className = 'ac-orb ' + _st2.orb;
            var pill = card.querySelector('.ac-pill');
            pill.className = 'ac-pill ' + _st2.pillCls;
            pill.textContent = _st2.pill;
            var _tsub = card.querySelector('.ac-titles .ac-sub');
            if (_tsub) _tsub.textContent = _st2.sub;
            var _ttitle = card.querySelector('.ac-titles .ac-title');
            if (_ttitle) _ttitle.textContent = _st2.title;
            /* 【2026-10-05 对齐设计稿"完成后缩回小卡片再由回复接管"】
               done/err 到达时让用户看清终态（打勾/红叉 + 完成徽章）约 1.4s，
               然后收起步骤区（.ac-bodywrap 的 grid 0fr↔1fr transition 会平滑缩回）；
               紧接着真实回复到达，后端把状态卡从缓冲删掉、前端对账删除该行。 */
            /* 收起统一由下方「done 后安定窗口」处理（避免双定时器重复收起同一张卡） */
            if (meta.plan && !bub.__acState.plan){
              var ph = document.createElement('div');
              ph.className = 'ac-plan';
              ph.textContent = meta.plan;
              /* 注意：.ac-steps 在 .ac-bodywrap>.ac-inner 里，不是 card 的直接子节点；
                 必须 插进 .ac-inner（2026-10-04 修：原写 card.insertBefore 会抛 NotFoundError，
                 整个 render 被静默吞掉 → 卡片冻结不更新） */
              var _acInner = card.querySelector('.ac-inner');
              if (_acInner) _acInner.insertBefore(ph, card.querySelector('.ac-steps'));
              bub.__acState.plan = true;
            }
          }
        }
        // 步骤增量渲染：只追加新步、更新已有步状态（避免每帧重播动画）
        var stepsEl = bub.querySelector('.ac-steps');
        var prev = bub.__acState ? bub.__acState.steps : 0;
        var steps = meta.steps || [];
        if (stepsEl){
          /* 入队而非直接插入：由 acPump 按 AC_STEP_GAP 逐条滑入。
             【2026-10-05】历史卡（isDone）跳过排队/动画：一次性把步骤写全，
             反正 CSS 已把 .ac-bodywrap 折起（display:none），不参与动画。 */
          var stq = bub.__acState;
          if (!stq.queue) stq.queue = [];
          if (isDone){
            stepsEl.innerHTML = steps.map(function(sp, i2){ return stepDom(sp, i2); }).join('');
            stq.steps = steps.length;
            stq.allSteps = steps;
            stq.queue.length = 0;
            stq.flush = true;
          } else {
            for (var i = prev; i < steps.length; i++) stq.queue.push({ step: steps[i], idx: i });
            stq.steps = steps.length;
            stq.allSteps = steps;
            acPump(bub, done || err);
          }
          if (!isDone && (expandLater || steps.length)){ acExpand(bub); }
        }
        if (expandLater) acExpand(bub);   // 首帧无 steps 但有 plan 时也要展开
        /* ——「正在看」进度区（2026-10-04 观看进度进聊天流）：标题行 + 阶段步进器 —— */
        var w = meta.watch;
        var inner = bub.querySelector('.ac-inner');
        var watchEl = bub.querySelector('.ac-watch');
        if (w && inner){
          var wk = w.kind || 'video';
          if (!watchEl){
            watchEl = document.createElement('div');
            watchEl.className = 'ac-watch';
            watchEl.innerHTML = '<div class="acw-top"><span class="acw-src"></span>' +
              '<span class="acw-title"></span><span class="acw-pct"></span></div>' +
              '<div class="acw-steps"></div>';
            inner.insertBefore(watchEl, stepsEl);
          }
          if (watchEl._kind !== wk){
            watchEl._kind = wk;
            var wst0 = (WATCH_STAGES[wk] || WATCH_STAGES.video);
            watchEl.querySelector('.acw-steps').innerHTML = wst0.map(function(s){
              return '<span class="acw-step" data-stage="' + s[0] + '">' +
                     '<i class="acw-dot"></i><span class="acw-cap">' + s[1] + '</span></span>';
            }).join('');
            watchEl.querySelector('.acw-src').innerHTML = WATCH_ICONS[wk] || WATCH_ICONS.video;
          }
          var wdone = !!w.done, werr = !!w.error;
          var wst = (WATCH_STAGES[wk] || WATCH_STAGES.video);
          var wcur = -1;
          for (var wi = 0; wi < wst.length; wi++){ if (wst[wi][0] === w.stage){ wcur = wi; break; } }
          if (wcur < 0) wcur = wdone ? wst.length - 1 : 0;
          var wnodes = watchEl.querySelectorAll('.acw-step');
          for (var wj = 0; wj < wnodes.length; wj++){
            var wn = wnodes[wj];
            wn.classList.remove('done','act','err');
            if (werr){ if (wj < wcur) wn.classList.add('done'); else if (wj === wcur) wn.classList.add('err'); }
            else if (wdone){ wn.classList.add('done'); }
            else { if (wj < wcur) wn.classList.add('done'); else if (wj === wcur) wn.classList.add('act'); }
          }
          var wttl = w.title || (wk === 'link' ? '这条链接' : (wk === 'image' ? '这组图片' : '这个视频'));
          var wT = watchEl.querySelector('.acw-title');
          if (wT.textContent !== wttl) wT.textContent = wttl;
          var wP = watchEl.querySelector('.acw-pct');
          wP.textContent = (!wdone && !werr && w.stage === 'download' && w.pct != null && w.pct !== '')
            ? (Math.round(parseFloat(w.pct)) + '%') : '';
          watchEl.style.display = '';
        } else if (watchEl){
          watchEl.style.display = 'none';
        }
        if (w) acExpand(bub);   // 卡片先建、watch 后到（或先前无任何展开理由）时也要展开
        /* 卡片标题/副标题**一律取自 acState**（单一真相源）。
           ⚠️【2026-10-08 修·"解析抖音却显示 agent 操作 UI"）】
           原实现是 `(meta.watch && !steps.length) ? '正在看' : '正在操作电脑'`。
           但观看卡**走到 done/error 时后端会把 watch 时间线写进 meta.steps**
           （webapp._on_video_watch_progress 补步骤），于是 steps.length>0 → 标题被
           覆盖成「达妮娅正在操作电脑 / AI 自主调用工具完成任务」。
           实测日志：`[观看卡] 更新 … steps=8` 之后卡片就变成了"操作电脑"。
           改用 acState 后，watch 场景的标题（正在一起看 / 看完 / 没看完）不再被 steps 影响。 */
        var _stNow = acState(meta);
        var acTitle = bub.querySelector('.ac-title');
        if (acTitle && acTitle.textContent !== _stNow.title) acTitle.textContent = _stNow.title;
        var acSub = bub.querySelector('.ac-sub');
        if (acSub && acSub.textContent !== _stNow.sub) acSub.textContent = _stNow.sub;
        /* 进度条：还没有任何步骤时不显示（否则孤零零一条线，像渲染残缺） */
        var progEl = bub.querySelector('.ac-progress');
        if (progEl) progEl.style.display = steps.length ? '' : 'none';
        // 进度条
        var total = 0;
        for (var t = 0; t < steps.length; t++){ if (steps[t].total) total = steps[t].total; }
        if (!total) total = steps.length;   // 兜底：后端未按步带 total 时按已见步数计
        var doneCount = steps.filter(function(s){ return s.state === 'done' || isFailState(s.state); }).length;
        var bar = bub.querySelector('.ac-bar i');
        var cnt = bub.querySelector('.ac-count');
        var pct = total ? Math.round((doneCount / total) * 100) : (done ? 100 : 0);
        /* 【2026-10-04 20:12】有失败步骤时计步器不写"完成"，改写"X/Y 步达成"。 */
        var _hasFail = !!(meta.partial || (meta.failed_tools || []).length);
        var countTxt = done
          ? (_hasFail ? (total ? (doneCount + '/' + total + ' 步达成') : '部分达成') : '完成')
          : (total ? ('第 ' + (doneCount + (doneCount < total && !err ? 1 : 0)) + '/' + total + ' 步') : '');
        /* GSAP：进度条 elastic 缓动 + 数字滚动；M 为空则退回纯 CSS 直写 */
        acBar(bar, pct, cnt, countTxt);
        // 完成后：先柔顺缩回「小卡片」（仅头部），再由真实回复接管 / 淡出
        if (done && !bub.__acCollapseScheduled){
          bub.__acCollapseScheduled = true;
          /* GSAP「落定」回弹 + 停 orb 呼吸/涟漪（仅 GSAP 启用时；M 为空则纯 CSS，无副作用） */
          var _dc = bub.querySelector('.agent-card');
          var _do = _dc && _dc.querySelector('.ac-orb');
          acSettle(_dc);
          if (_do) acStop(_do);
          /* 脊柱卡：若实际步数少于计划条数，把仍 pending（未点亮）的待办行淡出，避免完成时还挂着"待办" */
          if (bub.__acState && bub.__acState.hasSpine){
            var _pend = bub.querySelectorAll('.ac-steps .ac-pstep:not(.exec)');
            for (var _pi = 0; _pi < _pend.length; _pi++){
              (function(rw){ if (M) M.to(rw, { opacity:.4, duration:.4 }); else rw.style.opacity = '.4'; })(_pend[_pi]);
            }
          }
          /* 【2026-10-08 修"展开后立马缩回小长条"】收起时机 = max(最短在屏时间-已存活,
             终态后安定窗口)。两条都要保：前者保证短操作也露脸 ≥2.2s；后者保证
             "执行中→完成"后无论操作花了多久，都再多留 ~2s 让用户看清打勾/摘要，
             不再出现"才展开就缩回"。 */
          var _el2 = Date.now() - (bub.__acBorn || Date.now());
          var _hold2 = Math.max(AC_MIN_VISIBLE - _el2, AC_READ_AFTER_DONE);
          setTimeout(function(){
            var _c3 = bub.querySelector('.agent-card');
            if (!_c3) return;
            _c3.classList.remove('ac-expanded');
            var _s3 = _c3.querySelector('.ac-titles .ac-sub');
            var _m3 = acSummary(meta);
            if (_s3 && _m3) _s3.textContent = _m3;
          }, _hold2);
        }
      } catch(e){}
    }
    return { render: render };
  })();


  /* 后端 _wv2_broadcast({type:'chat_push'}) 即时刷新：双工开口/AI 回复立即上屏，
     不等 800ms 轮询。若外壳已有同名监听也不冲突（pollChat 按 id 去重，重复调用幂等）。 */
  try {
    const wv = window.chrome && window.chrome.webview;
    if (wv && wv.addEventListener){
      wv.addEventListener('message', function(ev){
        try {
          const d = ev && (ev.data || (ev.detail && ev.detail.data));
          if (!d) return;
          if (d.type === 'chat_push') pollChat();
          else if (d.type === 'video_watch') {
            /* 2026-10-04：观看进度已进聊天流状态卡（挂在达妮娅头像下）；
               仅无聊天流的页面（通话/开发者）继续用底部悬浮气泡展示。 */
            if (window.__videoWatch && !document.getElementById('scroll')) window.__videoWatch(d);
          }
          else if (d.type === 'agent_confirm') {
            /* 2026-10-08 修复：对话触发的 agent 需用户确认时弹出许可框，
               结果回传 resolveAgentConfirm；此前 redesign 漏接此分支→确认框不弹→60s 超时拒绝。 */
            if (typeof showAgentConfirm === 'function') showAgentConfirm(d.key, d.desc);
          }
          else if (d.type === 'quota_alert') { if (window.__quotaAlert) window.__quotaAlert(d); }
        } catch(e){}
      });
    }
  } catch(e){}
  } catch(e){}  /* startChatLive 全局容错：任何初始化异常不阻塞后续脚本 */

  pollChat(); pollChatLock();
  setInterval(pollChat, 800);                    /* 即时刷新：双工/AI 回复实时上屏 */
  setInterval(pollChatLock, 4000);               /* 锁状态实时反映，端口恢复即自动解锁 */
  document.addEventListener('morph-ready', () => { pollChat(); pollChatLock(); });
}
startChatLive();

/* ============================================================
   角色名跟随真实激活角色（2026-10-02）
   ------------------------------------------------------------
   问题：界面大量位置把"达妮娅"写死在 HTML 里（通话页名字/头像、
   右侧角色卡、屏幕共享标题…）。用户切换角色后这些仍显示旧名，
   与气泡里真实角色名不一致，看着像 bug。

   做法：HTML 里给这些节点打 data-char-name / data-char-initial 标记，
   由 applyCharacterName(name) 统一改写。名字来源优先 getChat() 的
   active_name（对话运行时真值），失败再退 getCharacters() 的 active。
   角色切换事件（character_switched）到达时自动重刷。
   ============================================================ */
(function applyCharacterNameBoot(){
  const FALLBACK = '达妮娅';
  let cur = '';
  function setText(el, txt){ if(el && el.textContent !== txt) el.textContent = txt; }

  function apply(name){
    name = (name || '').trim() || FALLBACK;
    cur = name;
    const initial = name.charAt(0) || FALLBACK.charAt(0);
    document.querySelectorAll('[data-char-name]').forEach(function(el){ setText(el, name); });
    document.querySelectorAll('[data-char-initial]').forEach(function(el){ setText(el, initial); });
    // 输入框 placeholder 里的角色名也跟着变
    const inp = document.getElementById('input');
    if(inp) inp.placeholder = '和' + name + '说点什么…';
    // 供其它脚本/子页复用
    window.__charName = name;
  }

  function refresh(){
    if(!window.Backend) return Promise.resolve();
    const p1 = (Backend.getChat ? Backend.getChat() : Promise.resolve({}))
      .catch(function(){ return {}; });
    return p1.then(function(r){
      if(r && r.active_name) { apply(r.active_name); return; }
      if(Backend.getCharacters){
        return Backend.getCharacters().catch(function(){ return {}; }).then(function(c){
          if(c && c.success && c.active_name) { apply(c.active_name); }
        });
      }
    }).catch(function(){});
  }

  window.applyCharacterName = apply;
  window.refreshCharacterName = refresh;
  refresh();
  // 角色切换时重刷（总线广播 character_switched）
  try{
    const wv = window.chrome && window.chrome.webview;
    if(wv && wv.addEventListener){
      wv.addEventListener('message', function(ev){
        const d = ev && ev.data;
        if(!d) return;
        if(d.type === 'character_switched'){ refresh(); }
        else if(d.type === 'chat_push'){ if(!cur) refresh(); }
      });
    }
  }catch(e){}
  setInterval(function(){ if(!cur) refresh(); }, 3000);
})();

/* ============================================================
   通用交互特效：水波纹（纵向大卡片点击）+ 背景位移（横向紧挨小卡片切换）
   颜色一律走 --accent-1 / --accent-2，自动跟随用户设定的主题色。
   ============================================================ */
const Fx = (() => {
  const reduce = () => !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

  /* ---- 水波纹：从点击点扩散的柔和高光环（羽化边缘，不是老土的实心墨点） ---- */
  function wave(host, ev){
    if (!host || reduce()) return;
    const r = host.getBoundingClientRect();
    if (!r.width || !r.height) return;
    const cs = getComputedStyle(host);
    if (cs.position === 'static') host.style.position = 'relative';
    if (cs.overflow === 'visible') host.style.overflow = 'hidden';
    const px = (ev && ev.clientX != null) ? (ev.clientX - r.left) : r.width / 2;
    const py = (ev && ev.clientY != null) ? (ev.clientY - r.top) : r.height / 2;
    /* 半径取「落点到最远角」的距离，正好扫满整张卡；
       按 max(宽,高) 估会过大，高光环会跑到卡片外面，看不到波纹扫过的感觉 */
    const dx = Math.max(px, r.width - px), dy = Math.max(py, r.height - py);
    const size = Math.hypot(dx, dy) * 2;
    const el = document.createElement('span');
    el.className = 'fx-wave';
    el.style.left = px + 'px'; el.style.top = py + 'px';
    el.style.width = el.style.height = size + 'px';
    host.appendChild(el);
    setTimeout(() => el.remove(), 720);
  }
  /* 给一批「大卡片 / 大板块」挂上点击水波纹 */
  function attachWave(sel, scope){
    let n = 0;
    (scope || document).querySelectorAll(sel).forEach(el => {
      el.addEventListener('pointerdown', e => wave(el, e));
      n++;
    });
    return n;
  }

  /* ---- 横向紧挨的小卡片：背景块横向位移到选中项 ---- */
  const segs = new Set();
  function segSync(container){
    if (!container) return;
    if (!document.body.contains(container)){ segs.delete(container); return; }  /* 页面已切走，自动注销 */
    const pill = container.querySelector('.fx-seg-pill');
    const active = container.querySelector('.on') || container.querySelector('button');
    if (!pill || !active) return;
    /* offsetLeft/Top 与绝对定位 left/top:0 同以「内边距盒」为原点，
       天然兼容容器的 padding、gap 与换行（.seg 是 flex-wrap 的）。 */
    pill.style.width = active.offsetWidth + 'px';
    pill.style.height = active.offsetHeight + 'px';
    pill.style.transform = 'translate(' + active.offsetLeft + 'px,' + active.offsetTop + 'px)';
    pill.style.opacity = '1';
  }
  function segInit(container){
    if (!container) return;
    if (!container.querySelector('.fx-seg-pill')){
      if (getComputedStyle(container).position === 'static') container.style.position = 'relative';
      container.classList.add('fx-seg');
      const pill = document.createElement('span');
      pill.className = 'fx-seg-pill';
      container.insertBefore(pill, container.firstChild);
    }
    segs.add(container);
    segSync(container);
    /* 选中项 class 变化（.on 转移）时自动跟着位移，页面无需手工调用 */
    if (window.MutationObserver && !container._fxMo){
      let queued = false;
      container._fxMo = new MutationObserver(() => {
        if (queued) return; queued = true;
        requestAnimationFrame(() => { queued = false; segSync(container); });
      });
      container._fxMo.observe(container, { attributes: true, subtree: true, attributeFilter: ['class'] });
    }
  }
  window.addEventListener('resize', () => segs.forEach(segSync));

  return { wave, attachWave, segInit, segSync, reduce };
})();
window.Fx = Fx;
