// AI 桌面伴侣 - 角色管理中心前端
// 粉色二次元 + 毛玻璃风格

let characters = [];
let activeId = "";
let editingId = null;
let selectedId = "";   // 当前选中查看的角色 id（详情面板/编辑/删除/试听都靠它）
let voiceOptions = [];
let personaTemplates = [];

// 【2026-09-04 修复·窗口凭空消失排查】原实现把 is_tray_enabled 函数引用当真值判断
//（window.pywebview.api.is_tray_enabled 不带括号，方法存在即恒真）→ 恒等于
//「托盘模式永远开启」，下面 visibilitychange 会在窗口被其他窗口完全遮挡
//（Chromium 原生遮挡检测 → document.hidden=true）时自动 hide_window()，
// 主窗口就凭空消失、任务栏预览黑屏、点任务栏也打不开。三处一并修复：
// ① 标志位只在后端明确返回 tray=true 时才置位；② 彻底移除"被遮挡自动藏窗口"
// 逻辑（托盘化只应由点 X 的 closing 拦截触发，见 main.py _on_main_closing）。

// 异步获取真实托盘开关（is_tray_enabled 返回 bool）
if (window.pywebview && window.pywebview.api && window.pywebview.api.is_tray_enabled) {
    Promise.resolve(window.pywebview.api.is_tray_enabled()).then(function(r) {
        window.is_tray_mode = (r === true || (r && r.enabled === true));
    }).catch(function(){ window.is_tray_mode = false; });
}

// 看板娘悬浮窗折叠/展开（聊天界面右下角 Anime2.5DRig）
function toggleLive2dDock() {
    const dock = document.getElementById("live2dDock");
    if (dock) dock.classList.toggle("collapsed");
}

// 桌宠模式开关（桌面透明浮窗看板娘）
async function togglePetMode() {
    try {
        const res = await pywebview.api.pet_toggle();
        syncPetToggle(!!(res && res.visible));
    } catch (e) {
        console.error("切换桌宠模式失败:", e);
    }
}
function syncPetToggle(visible) {
    const btn = document.getElementById("petToggle");
    const txt = document.getElementById("petToggleText");
    if (!btn) return;
    btn.classList.toggle("active", !!visible);
    txt.textContent = visible ? "桌宠模式 · 开" : "桌宠模式";
}

// 托盘模式开关（最小化到系统托盘）
async function toggleTrayMode() {
    try {
        const res = await pywebview.api.minimize_to_tray();
        if (res && res.ok) {
            syncTrayToggle(true);
            showTrayStatus("已最小化到托盘");
        } else {
            console.error("最小化到托盘失败:", res ? res.error : "未知错误");
        }
    } catch (e) {
        console.error("切换托盘模式失败:", e);
    }
}
function syncTrayToggle(minimized) {
    const btn = document.getElementById("trayToggle");
    const status = document.getElementById("trayStatus");
    const statusText = document.getElementById("trayStatusText");
    
    if (!btn) return;
    
    btn.classList.toggle("active", !!minimized);
    btn.textContent = minimized ? "托盘模式 · 开" : "托盘模式";
    
    if (minimized) {
        status.style.display = "flex";
        statusText.textContent = "已最小化";
    } else {
        status.style.display = "none";
    }
}
function showTrayStatus(message) {
    const statusText = document.getElementById("trayStatusText");
    if (statusText) {
        statusText.textContent = message;
        const status = document.getElementById("trayStatus");
        status.style.display = "flex";
        
        // 3秒后自动隐藏状态提示
        setTimeout(() => {
            status.style.display = "none";
        }, 3000);
    }
}

// 初始化
async function init() {
    try {
        // 等待 pywebview JS bridge 注入完成（防御性：window.pywebview 可能晚于 DOM 出现）
        await waitForPywebview();

        // 同步桌宠模式开关状态（隐藏的 pywebview 窗口不会收走焦点，直接查询即可）
        try {
            syncPetToggle(!!(await pywebview.api.pet_is_visible()));
        } catch (e) {
            console.warn("查询桌宠状态失败:", e);
        }

        // 同步系统托盘状态
        try {
            const trayEnabled = await pywebview.api.is_tray_enabled();
            if (trayEnabled) {
                syncTrayToggle(false); // 默认未最小化状态
            }
        } catch (e) {
            console.warn("查询系统托盘状态失败:", e);
        }

        // 获取音色选项
        const voiceResult = await pywebview.api.get_voice_options();
        if (voiceResult.success) {
            voiceOptions = voiceResult.voices;
            populateVoiceSelect();
        }

        // 获取人格模板
        try {
            const tplResult = await pywebview.api.get_character_templates();
            if (tplResult.success) {
                personaTemplates = tplResult.templates || [];
                renderPersonaTemplates();
            }
        } catch (e) {
            console.error("加载人格模板失败:", e);
        }

        // 加载角色列表
        await loadCharacters();

        setStatus("准备就绪");
    } catch (e) {
        console.error("初始化失败:", e);
        setStatus("初始化失败: " + e.message);
    }
}

// 等待 pywebview bridge 就绪
// 1) 优先监听官方 'pywebviewready' 事件
// 2) 兜底轮询 window.pywebview.api 是否存在（最长 ~10s）
function waitForPywebview() {
    return new Promise((resolve, reject) => {
        // 已就绪
        if (window.pywebview && window.pywebview.api) {
            resolve();
            return;
        }
        let resolved = false;
        const done = () => {
            if (!resolved) {
                resolved = true;
                resolve();
            }
        };
        // 监听官方事件
        window.addEventListener("pywebviewready", done, { once: true });
        // 兜底轮询
        const start = Date.now();
        const timer = setInterval(() => {
            if (window.pywebview && window.pywebview.api) {
                clearInterval(timer);
                done();
            } else if (Date.now() - start > 10000) {
                clearInterval(timer);
                if (!resolved) {
                    resolved = true;
                    reject(new Error("pywebview bridge 超时未就绪"));
                }
            }
        }, 50);
    });
}

// 加载角色列表
async function loadCharacters() {
    try {
        const result = await pywebview.api.get_characters();
        if (result.success) {
            characters = result.characters;
            rebuildCharAvatars();
            // 主题层（通话页/侧栏）也按最新头像重铺一次：
            // 此前改完头像只刷新了角色表，通话页仍挂旧图。
            try {
                if (window.ThemeManager && window.ThemeManager.applyForActive) {
                    window.ThemeManager.applyForActive();
                }
            } catch (e) { /* 主题层不可用时不影响角色表 */ }
            activeId = result.active_id;
            window.setCompanionName && window.setCompanionName((characters.find(c => c.id === activeId) || {}).name || "达妮娅");
            renderCharacterList();
            updateActiveIndicator();
            updateChatHeader();
        }
    } catch (e) {
        console.error("加载角色失败:", e);
        showToast("加载角色失败", "error");
    }
}

// 渲染角色列表
function renderCharacterList() {
    const list = document.getElementById("characterList");
    list.innerHTML = "";

    if (characters.length === 0) {
        list.innerHTML = `<div style="padding: 40px; text-align: center; color: var(--text-muted);">暂无角色，点击上方按钮新增</div>`;
        return;
    }

    characters.forEach(char => {
        const card = document.createElement("div");
        card.className = "character-card" + (char.is_active ? " selected" : "");
        card.onclick = (e) => selectCharacter(char.id, e);

        const avatar = document.createElement("div");
        avatar.className = "character-avatar";
        avatar.textContent = char.name.charAt(0);

        const info = document.createElement("div");
        info.className = "character-info";

        const name = document.createElement("div");
        name.className = "character-name";
        name.textContent = char.name;

        const meta = document.createElement("div");
        meta.className = "character-meta";
        
        const voiceTag = document.createElement("span");
        const voiceName = (char.voice && char.voice.voice) ? char.voice.voice : "未配置";
        voiceTag.textContent = voiceName.split("-")[2] || voiceName;
        meta.appendChild(voiceTag);

        if (char.is_active) {
            const badge = document.createElement("span");
            badge.className = "character-badge";
            badge.textContent = "当前";
            meta.appendChild(badge);
        }

        info.appendChild(name);
        info.appendChild(meta);

        card.appendChild(avatar);
        card.appendChild(info);
        list.appendChild(card);
    });
}

// 选中角色
function selectCharacter(charId, ev) {
    const char = characters.find(c => c.id === charId);
    if (!char) return;

    selectedId = charId;

    // 更新列表选中状态
    document.querySelectorAll(".character-card").forEach(card => {
        card.classList.remove("selected");
    });
    if (ev && ev.currentTarget) {
        ev.currentTarget.classList.add("selected");
    } else {
        const card = Array.from(document.querySelectorAll(".character-card"))
            .find(c => c.textContent.includes(char.name));
        if (card) card.classList.add("selected");
    }

    // 显示详情
    showDetail(char);
}

// 当前选中的角色对象（供编辑/删除/试听复用）
function getSelectedChar() {
    if (selectedId) {
        const c = characters.find(c => c.id === selectedId);
        if (c) return c;
    }
    return null;
}

// 显示角色详情
function showDetail(char) {
    document.getElementById("emptyState").classList.add("hidden");
    document.getElementById("detailContent").classList.remove("hidden");

    document.getElementById("detailName").textContent = char.name;
    document.getElementById("detailId").textContent = char.id;
    document.getElementById("detailName2").textContent = char.name;
    document.getElementById("detailCharacter").textContent = char.character || "-";
    document.getElementById("detailStyle").textContent = char.speaking_style || "-";
    document.getElementById("detailBackground").textContent = char.background || "-";

    document.getElementById("detailEngine").textContent = char.voice.engine;
    document.getElementById("detailVoice").textContent = char.voice.voice;
    // MiniMax 引擎时展示云端音色 ID 行
    const isMini = char.voice.engine === "minimax";
    document.getElementById("detailVoiceIdRow").style.display = isMini ? "" : "none";
    document.getElementById("detailVoiceId").textContent = isMini ? (char.voice.voice_id || "-") : "-";
    document.getElementById("detailRate").textContent = char.voice.rate;
    document.getElementById("detailPitch").textContent = char.voice.pitch;
    document.getElementById("detailVolume").textContent = char.voice.volume;
    const refAudio = char.voice.reference_audio || "";
    document.getElementById("detailRefAudio").textContent = refAudio
        ? refAudio.split(/[\\/]/).pop() : "-";
    const aux = (char.voice.aux_reference_audio_paths || []).filter(Boolean);
    document.getElementById("detailAuxRefAudio").textContent = aux.length
        ? aux.map(p => p.split(/[\\/]/).pop()).join("、") : "未配置";

    document.getElementById("detailModel3dEnabled").textContent = char.model_3d.enabled ? "是" : "否";
    document.getElementById("detailModelFormat").textContent = char.model_3d.format || "-";
    document.getElementById("detailModelPath").textContent = char.model_3d.path || "-";
    document.getElementById("detailModelDesc").textContent = char.model_3d.description || "-";

    // 更新切换按钮
    const switchBtn = document.getElementById("switchBtn");
    if (char.is_active) {
        switchBtn.textContent = " 当前角色";
        switchBtn.classList.add("active");
        switchBtn.disabled = true;
    } else {
        switchBtn.textContent = "设为当前";
        switchBtn.classList.remove("active");
        switchBtn.disabled = false;
    }
}

// 切换当前角色
async function switchActive() {
    const char = getSelectedChar();
    if (!char) return;

    try {
        const result = await pywebview.api.switch_character(char.id);
        if (result.success) {
            activeId = char.id;
            window.setCompanionName && window.setCompanionName(char.name);
            characters.forEach(c => c.is_active = c.id === char.id);
            renderCharacterList();
            showDetail(char);
            updateActiveIndicator();
            showToast(`已切换到 ${char.name}`, "success");
        } else {
            showToast(result.error || "切换失败", "error");
        }
    } catch (e) {
        showToast("切换失败: " + e.message, "error");
    }
}

// 更新当前角色指示
function updateActiveIndicator() {
    const activeChar = characters.find(c => c.id === activeId);
    const nameEl = document.getElementById("activeCharName");
    if (activeChar) {
        nameEl.textContent = `当前: ${activeChar.name}`;
    } else {
        nameEl.textContent = "无";
    }
}

// 打开编辑模态框
function openEditModal(charId) {
    editingId = charId || null;
    const modal = document.getElementById("editModal");
    const title = document.getElementById("modalTitle");

    if (editingId) {
        title.textContent = "编辑角色";
        const char = characters.find(c => c.id === editingId);
        if (char) {
            document.getElementById("charId").value = char.id;
            document.getElementById("charName").value = char.name;
            document.getElementById("charCharacter").value = char.character || "";
            document.getElementById("charStyle").value = char.speaking_style || "";
            document.getElementById("charBackground").value = char.background || "";
            document.getElementById("voiceEngine").value = char.voice.engine;
            document.getElementById("voiceVoice").value = char.voice.voice;
            document.getElementById("voiceRate").value = char.voice.rate;
            document.getElementById("voicePitch").value = char.voice.pitch;
            document.getElementById("voiceVolume").value = char.voice.volume;
            const v = char.voice || {};
            document.getElementById("gptApiUrl").value = v.api_url || "http://127.0.0.1:9880";
            document.getElementById("gptRefAudio").value = v.reference_audio || "";
            document.getElementById("gptRefText").value = v.reference_text || "";
            document.getElementById("gptAuxRefAudio").value = (v.aux_reference_audio_paths || []).join("\n");
            document.getElementById("gptLang").value = v.language || "zh";
            document.getElementById("minimaxApiKey").value = v.api_key || "";
            document.getElementById("minimaxGroupId").value = v.group_id || "";
            document.getElementById("minimaxVoiceId").value = v.voice_id || "female-shaonv";
            document.getElementById("minimaxModel").value = v.model || "speech-02-turbo";
            document.getElementById("minimaxSpeed").value = v.speed != null ? String(v.speed) : "1.0";
            document.getElementById("mimoApiKey").value = v.mimo_api_key || "";
            document.getElementById("mimoRefAudio").value = v.reference_audio || "";
        }
        // ID 字段在编辑模式下只读
        document.getElementById("charId").readOnly = true;
    } else {
        title.textContent = "新增角色";
        document.getElementById("editForm").reset();
        document.getElementById("voiceRate").value = "+0%";
        document.getElementById("voicePitch").value = "+0Hz";
        document.getElementById("voiceVolume").value = "+0%";
        document.getElementById("voiceEngine").value = "edge-tts";
        document.getElementById("gptApiUrl").value = "http://127.0.0.1:9880";
        document.getElementById("gptRefAudio").value = "";
        document.getElementById("gptRefText").value = "";
        document.getElementById("gptAuxRefAudio").value = "";
        document.getElementById("gptLang").value = "zh";
        document.getElementById("minimaxApiKey").value = "";
        document.getElementById("minimaxGroupId").value = "";
        document.getElementById("minimaxVoiceId").value = "female-shaonv";
        document.getElementById("minimaxModel").value = "speech-02-turbo";
        document.getElementById("minimaxSpeed").value = "1.0";
        document.getElementById("charId").readOnly = false;
    }

    toggleGptSovits();
    // 清理上一次的试听/试聊残留
    const prevResult = document.getElementById("personaTestResult");
    if (prevResult) { prevResult.textContent = "填好上面的人设后点这里，让 AI 以这个角色说句话给你听（不保存）"; prevResult.style.color = ""; }
    const voiceResult = document.getElementById("voicePreviewResult");
    if (voiceResult) { voiceResult.textContent = "选好引擎和音色后点这里，用当前设置念一句给你听"; voiceResult.style.color = ""; }
    modal.classList.remove("hidden");
}

// 关闭编辑模态框
function closeEditModal() {
    document.getElementById("editModal").classList.add("hidden");
    editingId = null;
}

// 保存角色
async function saveCharacter(event) {
    event.preventDefault();

    const charData = {
        id: document.getElementById("charId").value.trim(),
        name: document.getElementById("charName").value.trim(),
        character: document.getElementById("charCharacter").value.trim(),
        speaking_style: document.getElementById("charStyle").value.trim(),
        background: document.getElementById("charBackground").value.trim(),
        voice: {
            engine: document.getElementById("voiceEngine").value,
            voice: document.getElementById("voiceVoice").value,
            rate: document.getElementById("voiceRate").value.trim() || "+0%",
            pitch: document.getElementById("voicePitch").value.trim() || "+0Hz",
            volume: document.getElementById("voiceVolume").value.trim() || "+0%",
            fallback_pyttsx3: false,
            api_url: document.getElementById("gptApiUrl").value.trim() || "http://127.0.0.1:9880",
            reference_audio: (document.getElementById("voiceEngine").value === "mimo_tts"
                ? document.getElementById("mimoRefAudio").value.trim()
                : document.getElementById("gptRefAudio").value.trim()),
            reference_text: document.getElementById("gptRefText").value.trim(),
            aux_reference_audio_paths: document.getElementById("gptAuxRefAudio").value
                .split("\n").map(s => s.trim()).filter(Boolean),
            language: document.getElementById("gptLang").value || "zh",
            temperature: 0.7,
            top_k: 15,
            top_p: 0.9,
            api_key: document.getElementById("minimaxApiKey").value.trim(),
            mimo_api_key: document.getElementById("mimoApiKey").value.trim(),
            group_id: document.getElementById("minimaxGroupId").value.trim(),
            voice_id: document.getElementById("minimaxVoiceId").value.trim() || "female-shaonv",
            model: document.getElementById("minimaxModel").value || "speech-02-turbo",
            speed: parseFloat(document.getElementById("minimaxSpeed").value) || 1.0,
        },
        model: {
            vision_model: null,
            fallback_model: null,
        },
        model_3d: {
            enabled: false,
            format: null,
            path: null,
            thumbnail: null,
            description: null,
        },
    };

    if (!charData.id) {
        showToast("角色 ID 不能为空", "error");
        return;
    }
    if (!charData.name) {
        showToast("角色名称不能为空", "error");
        return;
    }

    try {
        const result = await pywebview.api.save_character(charData);
        if (result.success) {
            const savedId = charData.id;
            showToast(editingId ? "保存成功" : "新增成功", "success");
            closeEditModal();
            await loadCharacters();

            // 保存后自动选中该角色（新增/编辑都适用）
            setTimeout(() => {
                const char = characters.find(c => c.id === savedId);
                if (char) selectCharacter(savedId);
            }, 100);
        } else {
            showToast(result.error || "保存失败", "error");
        }
    } catch (e) {
        showToast("保存失败: " + e.message, "error");
    }
}

// 删除角色
async function deleteCharacter() {
    const char = getSelectedChar();
    if (!char) return;

    if (char.is_active) {
        showToast("不能删除当前激活的角色", "error");
        return;
    }

    if (!confirm(`确定删除角色「${char.name}」吗？`)) return;

    try {
        const result = await pywebview.api.delete_character(char.id);
        if (result.success) {
            showToast("删除成功", "success");
            selectedId = "";
            document.getElementById("emptyState").classList.remove("hidden");
            document.getElementById("detailContent").classList.add("hidden");
            await loadCharacters();
        } else {
            showToast(result.error || "删除失败", "error");
        }
    } catch (e) {
        showToast("删除失败: " + e.message, "error");
    }
}

// 填充音色选项
function populateVoiceSelect() {
    const select = document.getElementById("voiceVoice");
    select.innerHTML = "";
    voiceOptions.forEach(voice => {
        const option = document.createElement("option");
        option.value = voice.id;
        option.textContent = voice.name;
        select.appendChild(option);
    });
}

// 根据 TTS 引擎切换显示对应的语音配置区
function toggleGptSovits() {
    const engine = document.getElementById("voiceEngine").value;
    const gptSection = document.getElementById("gptSovitsSection");
    const miniSection = document.getElementById("minimaxSection");
    const mimoSection = document.getElementById("mimoSection");
    const hint = document.getElementById("voiceVoiceHint");
    const isClone = engine === "gpt-sovits" || engine === "voxcpm" || engine === "qwen3tts" || engine === "cosyvoice";
    gptSection.classList.toggle("hidden", !isClone);
    miniSection.classList.toggle("hidden", engine !== "minimax");
    if (mimoSection) mimoSection.classList.toggle("hidden", engine !== "mimo_tts");
    const title = document.getElementById("cloneSectionTitle");
    if (title) {
        title.textContent = engine === "voxcpm" ? "VoxCPM2 本地语音克隆配置"
            : engine === "qwen3tts" ? "Qwen3-TTS 本地语音克隆配置"
            : engine === "cosyvoice" ? "CosyVoice2 本地语音克隆配置"
            : "GPT-SoVITS 语音克隆配置";
    }
    const apiUrl = document.getElementById("gptApiUrl");
    if (isClone && apiUrl && (!apiUrl.dataset.touched || apiUrl.value === "")) {
        apiUrl.value = engine === "voxcpm" ? "http://127.0.0.1:9881"
            : engine === "qwen3tts" ? "http://127.0.0.1:9882"
            : engine === "cosyvoice" ? "http://127.0.0.1:9883"
            : "http://127.0.0.1:9880";
    }
    if (isClone) {
        hint.textContent = "（本地克隆时的兜底音色）";
    } else {
        hint.textContent = "";
    }
    // 小米 MiMo：音色直接用参考音频合成，下拉只给一个选项；切回时还原 edge 音色列表
    const voiceSel = document.getElementById("voiceVoice");
    if (engine === "mimo_tts") {
        voiceSel.innerHTML = '<option value="mimo-v2.5-tts-voiceclone">达娅克隆（用参考音频直接合成）</option>';
        voiceSel.dataset.mimo = "1";
    } else if (voiceSel.dataset.mimo === "1") {
        voiceSel.innerHTML = "";
        voiceOptions.forEach(v => {
            const o = document.createElement("option");
            o.value = v.id; o.textContent = v.name;
            voiceSel.appendChild(o);
        });
        voiceSel.dataset.mimo = "";
    }
}

// 测试 MiniMax 连接（用当前表单里的 key/group/音色 试合成一句）
async function testMinimaxVoice() {
    const btn = document.querySelector("#minimaxSection .input-with-btn .btn-test");
    const result = document.getElementById("minimaxTestResult");
    const apiKey = document.getElementById("minimaxApiKey").value.trim();
    const groupId = document.getElementById("minimaxGroupId").value.trim();
    if (!apiKey || !groupId) {
        result.textContent = " 请先填 API Key 和 GroupId";
        result.style.color = "#e74c3c";
        return;
    }
    const cfg = collectModalVoiceCfg();
    btn.disabled = true;
    btn.textContent = "测试中...";
    result.textContent = "正在请求 MiniMax 云端合成…";
    result.style.color = "var(--text-muted)";
    try {
        const res = await pywebview.api.test_voice(cfg, "你好呀，我是达妮娅，很高兴认识你～");
        if (res.success) {
            result.textContent = " MiniMax 连接正常，正在播放合成结果～";
            result.style.color = "#2ecc71";
        } else {
            result.textContent = " " + (res.error || "测试失败");
            result.style.color = "#e74c3c";
        }
    } catch (e) {
        result.textContent = " 调用失败: " + e.message;
        result.style.color = "#e74c3c";
    } finally {
        btn.disabled = false;
        btn.textContent = "测试连接";
    }
}

// 从本地音频克隆 MiniMax 音色（上传→复刻→回填 voice_id）
async function cloneMinimaxVoice() {
    const btn = document.querySelector("#minimaxSection .btn-clone");
    const result = document.getElementById("minimaxCloneResult");
    const fileInput = document.getElementById("minimaxCloneFile");
    const newIdInput = document.getElementById("minimaxCloneVoiceId");
    const file = fileInput.files && fileInput.files[0];
    if (!file) {
        result.textContent = " 请先选择要克隆的音频文件";
        result.style.color = "#e74c3c";
        return;
    }
    const cfg = collectModalVoiceCfg();
    if (!cfg.api_key || !cfg.group_id) {
        result.textContent = " 请先填 API Key 和 GroupId";
        result.style.color = "#e74c3c";
        return;
    }
    btn.disabled = true;
    btn.textContent = "克隆中...";
    result.textContent = "正在上传音频并复刻音色（约几十秒）…";
    result.style.color = "var(--text-muted)";
    try {
        const b64 = await readFileAsBase64(file);
        const res = await pywebview.api.clone_minimax_voice(
            cfg, file.name, b64, newIdInput.value.trim());
        if (res.success) {
            document.getElementById("minimaxVoiceId").value = res.voice_id;
            result.textContent = " " + (res.message || "克隆成功，已回填音色框");
            result.style.color = "#2ecc71";
        } else {
            result.textContent = " " + (res.error || "克隆失败");
            result.style.color = "#e74c3c";
        }
    } catch (e) {
        result.textContent = " 调用失败: " + e.message;
        result.style.color = "#e74c3c";
    } finally {
        btn.disabled = false;
        btn.textContent = "克隆";
    }
}

// 测试小米 MiMo 连接（用当前表单里的 key + 参考音频 试合成一句）
async function testMimoVoice() {
    const btn = document.getElementById("mimoTestBtn");
    const result = document.getElementById("mimoTestResult");
    const apiKey = document.getElementById("mimoApiKey").value.trim();
    const refAudio = document.getElementById("mimoRefAudio").value.trim();
    if (!apiKey || !refAudio) {
        result.textContent = " 请先填 API Key 和达娅参考音频路径";
        result.style.color = "#e74c3c";
        return;
    }
    const cfg = collectModalVoiceCfg();
    btn.disabled = true;
    btn.textContent = "测试中...";
    result.textContent = "正在请求小米 MiMo 云端合成…";
    result.style.color = "var(--text-muted)";
    try {
        const res = await pywebview.api.test_voice(cfg, "你好呀，我是达妮娅，很高兴认识你～");
        if (res.success) {
            result.textContent = " MiMo 连接正常，正在播放合成结果～";
            result.style.color = "#2ecc71";
        } else {
            result.textContent = " " + (res.error || "测试失败");
            result.style.color = "#e74c3c";
        }
    } catch (e) {
        result.textContent = " 调用失败: " + e.message;
        result.style.color = "#e74c3c";
    } finally {
        btn.disabled = false;
        btn.textContent = "测试连接（播放声音）";
    }
}

// 选达娅参考音频（填到 mimoRefAudio，不裁剪——MiMo 接受数秒~数十秒参考音）
async function pickMimoAudioFile() {
    const input = document.getElementById("mimoRefAudio");
    const btn = input.parentElement.querySelector(".btn-test");
    if (btn) { btn.disabled = true; btn.textContent = "选择中..."; }
    try {
        const res = await pywebview.api.pick_audio_file();
        if (res.success && res.path) {
            input.value = res.path;
            if (res.duration) {
                showToast(`已选 ${res.duration.toFixed(1)} 秒参考音频`, "success");
            }
        } else if (res.error) {
            showToast("选择失败: " + res.error, "error");
        }
    } catch (e) {
        showToast("选择失败: " + e.message, "error");
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = " 浏览…"; }
    }
}

function readFileAsBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
            const data = reader.result;
            const comma = data.indexOf(",");
            resolve(comma >= 0 ? data.slice(comma + 1) : data);
        };
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
    });
}

// ===================== MiniMax 接入向导 =====================
const WIZARD_TOTAL = 5;
let wizardStepIdx = 0;

function openMinimaxWizard() {
    // 预填已有的 key/group
    document.getElementById("wizardGroupId").value = document.getElementById("minimaxGroupId").value.trim();
    document.getElementById("wizardApiKey").value = document.getElementById("minimaxApiKey").value.trim();
    document.getElementById("minimaxWizard").classList.remove("hidden");
    wizardStepIdx = 0;
    renderWizard();
}

function closeMinimaxWizard() {
    // 关闭前把向导里填的同步回语音设置区
    syncWizardToSettings();
    document.getElementById("minimaxWizard").classList.add("hidden");
}

function openMiniMaxPage(page) {
    pywebview.api.open_minimax_page(page).then(res => {
        if (res && !res.success) {
            alert("打开浏览器失败：" + (res.error || "未知错误") + "\n请手动访问 " + res.url);
        }
    }).catch(e => {
        alert("调用失败：" + e.message);
    });
}

function syncWizardToSettings() {
    document.getElementById("minimaxGroupId").value = document.getElementById("wizardGroupId").value.trim();
    document.getElementById("minimaxApiKey").value = document.getElementById("wizardApiKey").value.trim();
}

function renderWizard() {
    for (let i = 0; i < WIZARD_TOTAL; i++) {
        document.getElementById("wzCard" + i).classList.toggle("hidden", i !== wizardStepIdx);
    }
    // 步骤指示点
    const dots = document.querySelectorAll("#wizardDots .wizard-dot");
    dots.forEach((d, i) => d.classList.toggle("active", i === wizardStepIdx));
    // 导航按钮
    document.getElementById("wizardPrevBtn").disabled = wizardStepIdx === 0;
    document.getElementById("wizardCounter").textContent = (wizardStepIdx + 1) + " / " + WIZARD_TOTAL;
    document.getElementById("wizardNextBtn").textContent = wizardStepIdx === WIZARD_TOTAL - 1 ? "完成" : "下一步";
}

function wizardStep(delta) {
    const next = wizardStepIdx + delta;
    if (next < 0 || next >= WIZARD_TOTAL) {
        if (next >= WIZARD_TOTAL) {
            // 最后一步"完成"→ 同步并关闭
            syncWizardToSettings();
            closeMinimaxWizard();
        }
        return;
    }
    syncWizardToSettings();
    wizardStepIdx = next;
    renderWizard();
}

// 向导内克隆（用向导自己的输入，不依赖主区表单）
async function wizardCloneVoice() {
    const btn = document.querySelector("#wzCard3 .btn-clone");
    const result = document.getElementById("wizardCloneResult");
    const file = document.getElementById("wizardCloneFile").files && document.getElementById("wizardCloneFile").files[0];
    if (!file) {
        result.textContent = " 请先选择要克隆的录音文件";
        result.style.color = "#e74c3c";
        return;
    }
    const cfg = {
        engine: "minimax",
        api_key: document.getElementById("wizardApiKey").value.trim(),
        group_id: document.getElementById("wizardGroupId").value.trim(),
        voice_id: "female-shaonv",
        model: document.getElementById("minimaxModel").value || "speech-02-turbo",
        speed: parseFloat(document.getElementById("minimaxSpeed").value) || 1.0,
    };
    if (!cfg.api_key || !cfg.group_id) {
        result.textContent = " 请先在第 2、3 步填好 GroupId 和 API Key";
        result.style.color = "#e74c3c";
        return;
    }
    btn.disabled = true;
    btn.textContent = "克隆中...";
    result.textContent = "正在上传音频并复刻音色（约几十秒）…";
    result.style.color = "var(--text-muted)";
    try {
        const b64 = await readFileAsBase64(file);
        const res = await pywebview.api.clone_minimax_voice(
            cfg, file.name, b64, document.getElementById("wizardCloneVoiceId").value.trim());
        if (res.success) {
            document.getElementById("minimaxVoiceId").value = res.voice_id;
            result.textContent = " " + (res.message || "克隆成功，音色已回填");
            result.style.color = "#2ecc71";
            setTimeout(() => wizardStep(1), 800);
        } else {
            result.textContent = " " + (res.error || "克隆失败");
            result.style.color = "#e74c3c";
        }
    } catch (e) {
        result.textContent = " 调用失败: " + e.message;
        result.style.color = "#e74c3c";
    } finally {
        btn.disabled = false;
        btn.textContent = "开始克隆";
    }
}

// 向导第 5 步测试：同步输入到主区后复用 testMinimaxVoice
function testMinimaxVoiceFromWizard() {
    syncWizardToSettings();
    testMinimaxVoice();
    setTimeout(() => {
        const src = document.getElementById("minimaxTestResult");
        const dst = document.getElementById("wizardTestResult");
        dst.textContent = src.textContent;
        dst.style.color = src.style.color;
    }, 300);
}

// 测试 GPT-SoVITS 服务连接
async function testGptSovits() {
    const btn = document.getElementById("gptTestBtn");
    const result = document.getElementById("gptTestResult");
    const url = document.getElementById("gptApiUrl").value.trim() || "http://127.0.0.1:9880";
    btn.disabled = true;
    btn.textContent = "测试中...";
    result.textContent = "正在连接 " + url + " ...";
    try {
        const res = await pywebview.api.gpt_sovits_status(url);
        if (res.success && res.reachable) {
            result.textContent = " 服务可达 (HTTP " + res.status_code + ")";
            result.style.color = "#2ecc71";
        } else {
            result.textContent = " 无法连接: " + (res.error || "未知错误");
            result.style.color = "#e74c3c";
        }
    } catch (e) {
        result.textContent = " 调用失败: " + e.message;
        result.style.color = "#e74c3c";
    } finally {
        btn.disabled = false;
        btn.textContent = "测试连接";
    }
}

// 打开系统文件选择框挑选参考音频
async function pickAudioFile() {
    const input = document.getElementById("gptRefAudio");
    const btn = input.parentElement.querySelector(".btn-test");
    if (btn) {
        btn.disabled = true;
        btn.textContent = "选择中...";
    }
    try {
        const res = await pywebview.api.pick_audio_file();
        if (res.success && res.path) {
            input.value = res.path;
            const dur = res.duration;
            if (dur && (dur < 3 || dur > 10)) {
                // 超出 GPT-SoVITS 3~10 秒要求 → 自动裁剪
                const ok = window.confirm(
                    `这段音频时长 ${dur.toFixed(1)} 秒，超出 GPT-SoVITS 要求的 3~10 秒。` +
                    `是否自动裁剪成 9 秒？（生成 *_trim.wav，原文件不动）`
                );
                if (ok) {
                    const r = await pywebview.api.trim_audio(res.path);
                    if (r.success) {
                        input.value = r.path;
                        showToast(`已裁剪为 ${(r.duration || 0).toFixed(1)} 秒`, "success");
                    } else {
                        showToast("裁剪失败: " + (r.error || "未知错误"), "error");
                    }
                } else {
                    showToast("音频需 3~10 秒，超长会报错", "error");
                }
            } else {
                showToast("已选择参考音频，记得填对应文本", "success");
            }
        } else if (res.error) {
            showToast("选择失败: " + res.error, "error");
        }
    } catch (e) {
        showToast("调用失败: " + e.message, "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = " 浏览…";
        }
    }
}

// ======== 人格模板 ========
// 渲染模板 chips（点一下填充人设三字段）
function renderPersonaTemplates() {
    const box = document.getElementById("personaTemplates");
    if (!box) return;
    box.innerHTML = "";
    personaTemplates.forEach(tpl => {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "template-chip";
        chip.textContent = tpl.name;
        chip.title = tpl.sample || "";
        chip.onclick = () => applyPersonaTemplate(tpl);
        box.appendChild(chip);
    });
}

// 应用模板到表单
function applyPersonaTemplate(tpl) {
    document.getElementById("charCharacter").value = tpl.character || "";
    document.getElementById("charStyle").value = tpl.speaking_style || "";
    document.getElementById("charBackground").value = tpl.background || "";
    const sampleBox = document.getElementById("personaTestResult");
    if (tpl.sample) {
        sampleBox.textContent = "模板示例口吻：" + tpl.sample;
        sampleBox.style.color = "var(--pink-dark)";
    }
    showToast(`已填充「${tpl.name}」模板，按你的想法微调吧～`, "success");
}

// ======== 试听音色（模态框里用当前表单设置） ========
function collectModalVoiceCfg() {
    const engine = document.getElementById("voiceEngine").value;
    const defaultApi = engine === "voxcpm" ? "http://127.0.0.1:9881"
        : engine === "qwen3tts" ? "http://127.0.0.1:9882"
        : engine === "cosyvoice" ? "http://127.0.0.1:9883"
        : "http://127.0.0.1:9880";
    return {
        engine: engine,
        voice: document.getElementById("voiceVoice").value,
        rate: document.getElementById("voiceRate").value.trim() || "+0%",
        pitch: document.getElementById("voicePitch").value.trim() || "+0Hz",
        volume: document.getElementById("voiceVolume").value.trim() || "+0%",
        fallback_pyttsx3: false,
        api_url: document.getElementById("gptApiUrl").value.trim() || defaultApi,
        reference_audio: (engine === "mimo_tts"
            ? document.getElementById("mimoRefAudio").value.trim()
            : document.getElementById("gptRefAudio").value.trim()),
        reference_text: document.getElementById("gptRefText").value.trim(),
        aux_reference_audio_paths: document.getElementById("gptAuxRefAudio").value
            .split("\n").map(s => s.trim()).filter(Boolean),
        language: document.getElementById("gptLang").value || "zh",
        temperature: 0.7,
        top_k: 15,
        top_p: 0.9,
        api_key: document.getElementById("minimaxApiKey").value.trim(),
        mimo_api_key: document.getElementById("mimoApiKey").value.trim(),
        group_id: document.getElementById("minimaxGroupId").value.trim(),
        voice_id: document.getElementById("minimaxVoiceId").value.trim() || "female-shaonv",
        model: document.getElementById("minimaxModel").value || "speech-02-turbo",
        speed: parseFloat(document.getElementById("minimaxSpeed").value) || 1.0,
    };
}

async function previewVoice() {
    const btn = document.querySelector(".btn-voice-test");
    const result = document.getElementById("voicePreviewResult");
    const cfg = collectModalVoiceCfg();

    const isLocalClone = cfg.engine === "gpt-sovits" || cfg.engine === "voxcpm"
        || cfg.engine === "qwen3tts" || cfg.engine === "cosyvoice";
    if (isLocalClone) {
        if (!cfg.reference_audio) {
            result.textContent = "先填写本地克隆参考音频路径再试听哦";
            result.style.color = "#e74c3c";
            return;
        }
        if (!cfg.reference_text) {
            result.textContent = "参考音频文本也要填（参考音频里说的话）";
            result.style.color = "#e74c3c";
            return;
        }
    }
    if (cfg.engine === "minimax") {
        if (!cfg.api_key || !cfg.group_id) {
            result.textContent = "MiniMax 需要先填 API Key 和 GroupId 才能试听";
            result.style.color = "#e74c3c";
            return;
        }
    }
    if (cfg.engine === "mimo_tts") {
        if (!cfg.mimo_api_key || !cfg.reference_audio) {
            result.textContent = "MiMo 需要先填 API Key 和达娅参考音频才能试听";
            result.style.color = "#e74c3c";
            return;
        }
    }

    btn.disabled = true;
    btn.textContent = "试听中...";
    result.textContent = "正在合成，稍等一下～";
    result.style.color = "var(--text-muted)";
    try {
        const res = await pywebview.api.test_voice(cfg);
        if (res.success) {
            result.textContent = " 已开始播放，听听看～";
            result.style.color = "#2ecc71";
        } else {
            result.textContent = " " + (res.error || "试听失败");
            result.style.color = "#e74c3c";
        }
    } catch (e) {
        result.textContent = " 调用失败: " + e.message;
        result.style.color = "#e74c3c";
    } finally {
        btn.disabled = false;
        btn.textContent = " 试听音色";
    }
}

// ======== 试听音色（详情面板：用当前角色已保存的配置） ========
async function previewDetailVoice() {
    const char = getSelectedChar();
    if (!char || !char.voice) {
        showToast("该角色没有语音配置", "error");
        return;
    }
    const btn = document.querySelector(".btn-detail-test");
    const hint = document.getElementById("detailVoiceHint");
    hint.classList.remove("hidden");

    const cfg = char.voice;
    if ((cfg.engine === "gpt-sovits" || cfg.engine === "cosyvoice" || cfg.engine === "qwen3tts")
            && (!cfg.reference_audio || !cfg.reference_text)) {
        const ename = cfg.engine === "cosyvoice" ? "CosyVoice2"
            : cfg.engine === "qwen3tts" ? "Qwen3-TTS" : "GPT-SoVITS";
        hint.textContent = `这个角色用 ${ename}，但还没配置参考音频/文本，试听不了哦`;
        hint.style.color = "#e74c3c";
        return;
    }
    if (cfg.engine === "mimo_tts" && (!cfg.mimo_api_key || !cfg.reference_audio)) {
        hint.textContent = "这个角色用小米 MiMo，但还没配置 API Key 或达娅参考音频，试听不了哦";
        hint.style.color = "#e74c3c";
        return;
    }

    btn.disabled = true;
    btn.textContent = "试听中...";
    hint.textContent = "正在合成，稍等一下～";
    hint.style.color = "var(--text-muted)";
    try {
        const res = await pywebview.api.test_voice(cfg);
        if (res.success) {
            hint.textContent = " 已开始播放，听听看～";
            hint.style.color = "#2ecc71";
        } else {
            hint.textContent = " " + (res.error || "试听失败");
            hint.style.color = "#e74c3c";
        }
    } catch (e) {
        hint.textContent = " 调用失败: " + e.message;
        hint.style.color = "#e74c3c";
    } finally {
        btn.disabled = false;
        btn.textContent = " 试听音色";
    }
}

// ======== 试聊一句（用表单里当前的人设让 AI 说句话） ========
async function testPersonaTalk() {
    const btn = document.querySelector(".btn-try-talk");
    const result = document.getElementById("personaTestResult");
    const charData = {
        name: document.getElementById("charName").value.trim() || "AI",
        character: document.getElementById("charCharacter").value.trim(),
        speaking_style: document.getElementById("charStyle").value.trim(),
        background: document.getElementById("charBackground").value.trim(),
    };
    if (!charData.character && !charData.speaking_style && !charData.background) {
        result.textContent = "先填点人设内容（性格/风格/背景），我才能替你开口哦";
        result.style.color = "#e74c3c";
        return;
    }

    btn.disabled = true;
    btn.textContent = "思考中...";
    result.textContent = "正在让 AI 以这个角色开口，稍等一下～";
    result.style.color = "var(--text-muted)";
    try {
        const res = await pywebview.api.test_persona(charData);
        if (res.success) {
            const name = charData.name;
            result.textContent = `「${name}」说：${res.text}`;
            result.style.color = "var(--pink-dark)";
        } else {
            result.textContent = " " + (res.error || "试聊失败");
            result.style.color = "#e74c3c";
        }
    } catch (e) {
        result.textContent = " 调用失败: " + e.message;
        result.style.color = "#e74c3c";
    } finally {
        btn.disabled = false;
        btn.textContent = " 试聊一句";
    }
}

// 显示 Toast
function showToast(message, type = "success", duration = 2500) {
    const toast = document.getElementById("toast");
    toast.textContent = message;
    toast.className = "toast " + type;
    toast.classList.remove("hidden");
    if (toast._timer) clearTimeout(toast._timer);
    // loading 型常驻显示（模型加载完成/失败时会替换文本并自行隐藏）
    if (type !== "loading") {
        toast._timer = setTimeout(() => {
            toast.classList.add("hidden");
        }, duration);
    }
}

// 设置状态
function setStatus(message) {
    document.getElementById("statusText").textContent = message;
}

// ================================
// Tab 切换 (角色管理 / Unity / 调试台)
// ================================
let unityTabActivated = false;
let debugTabActivated = false;
let chatTabActivated = false;
let llmTabActivated = false;
let memoryTabActivated = false;
let activityTabActivated = false;
let coursesTabActivated = false;
function switchTab(name) {
    // 按钮高亮
    document.querySelectorAll(".tab-btn").forEach(b => {
        b.classList.toggle("active", b.dataset.tab === name);
    });
    // 面板显隐
    document.querySelectorAll(".tab-panel").forEach(p => {
        p.classList.toggle("active", p.id === "tab-" + name);
    });
    // 切到聊天时启动 1s 轮询；已启动过则立即刷一次（pywebview 后台会节流定时器，
    // 切回页面必须主动刷新，否则消息要等下一轮才出来）
    if (name === "chat") {
        chatPoll();
        if (!chatTabActivated) {
            chatTabActivated = true;
            chatTimer = setInterval(chatPoll, 800);
        }
    }
    // 切到模型设置时加载 LLM 提供商配置
    if (name === "llm" && !llmTabActivated) {
        llmTabActivated = true;
        loadLlmSettings();
    }
    // 切到记忆设置时加载记忆配置 + 统计
    if (name === "memory" && !memoryTabActivated) {
        memoryTabActivated = true;
        loadMemorySettings();
    }
    // 切到日报周报时加载历史报告列表
    if (name === "activity" && !activityTabActivated) {
        activityTabActivated = true;
        loadActivityReports();
    }
    // 切到课程记录时加载课程列表 + 录制状态
    if (name === "courses" && !coursesTabActivated) {
        coursesTabActivated = true;
        loadCourses();
        refreshCourseStatus();
    }
    // 切到 Unity 时懒加载 MJPEG 流 + 启动 WS
    if (name === "unity" && !unityTabActivated) {
        unityTabActivated = true;
        startUnityStream();
        connectAiWs();
    }
    // 切到调试台时启动 1s 轮询
    if (name === "debug" && !debugTabActivated) {
        debugTabActivated = true;
        startDebugPoll();
    }
}

// ================================
// 聊天 Tab（1s 轮询 get_chat_messages）
// ================================
let chatTimer = null;
let chatLastId = 0;
let chatLastDate = null;   // 最近一条消息的日期键（跨天时插入"今天/昨天"分隔条）

// 角色 → 头像素材（目前只有达妮娅有 Unity 渲染头像；其余角色用首字母渐变占位）
// 角色头像表：由后端角色表重建，不再写死。
// 【2026-11-04 修】此前是写死常量 { danya: "assets/danya_avatar.png" }，
// 导致三件事：① 聊天页/通话记录永远显示那张 10-17 的静态图（改了头像也不变）；
// ② 换角色（如大肥鱼）查表恒为 undefined → 头像永远不显示；
// ③ 重载后首帧 activeId 未就绪时会被置 hidden 且无人恢复。
// 现在由 loadCharacters() 按后端 avatar 字段重建，静态图只作最后兜底。
const CHAR_AVATARS = { danya: "assets/danya_avatar.png" };
function rebuildCharAvatars() {
    // 先清掉非 danya 的旧条目，避免删角色后尸魂残留
    for (const k of Object.keys(CHAR_AVATARS)) {
        if (k !== "danya") delete CHAR_AVATARS[k];
    }
    for (const c of (characters || [])) {
        const a = String((c && c.avatar) || "").trim();
        if (!a) continue;
        // 已经是 URL / 绝对路径的照旧用，否则走 /local_img 白名单
        CHAR_AVATARS[c.id] = /^(https?:|data:|\/)/.test(a)
            ? a
            : "/local_img?path=" + encodeURIComponent(a);
    }
}

function startChatPoll() {
    chatPoll();
    chatTimer = setInterval(chatPoll, 1000);
}

function escHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
}

function triggerLabel(t) {
    return { screen_batch: "屏幕累计", idle: "空闲关心", screen_change: "屏幕变化", moment: "游戏时刻" }[t] || t || "";
}

// 消息日期键（跨天时插入"今天/昨天"分隔条用）
function fmtDay(ts) {
    if (!ts) return "";
    const d = new Date(ts * 1000);
    return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;
}
function dayLabel(ts) {
    if (!ts) return "";
    const d = new Date(ts * 1000);
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const that = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const diff = Math.round((today - that) / 86400000);
    if (diff === 0) return "今天";
    if (diff === 1) return "昨天";
    return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
}

async function chatPoll() {
    try {
        const res = await pywebview.api.get_chat_messages();
        if (!res || !res.success) return;
        const el = document.getElementById("chatMessages");
        if (!el) return;
        const msgs = res.messages || [];
        // 空状态
        if (!msgs.length) {
            chatLastDate = null;
            const name = res.active_name || "达妮娅";
            const avSrc = CHAR_AVATARS[activeId] || "assets/danya_avatar.png";
            el.innerHTML = `<div class="chat-empty">
                <div class="chat-empty-avatar">
                    <img id="chatEmptyAvatar" src="${avSrc}" alt="角色头像" onerror="this.style.visibility='hidden'">
                </div>
                <div class="chat-empty-title">${escHtml(name)}在等你</div>
                <div class="chat-empty-text">轻轻说一句，她就会回应你</div>
            </div>`;
            return;
        }
        const emptyEl = el.querySelector(".chat-empty");
        if (emptyEl) emptyEl.remove();
        const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
        let appended = 0;
        for (const m of msgs) {
            // 流式消息：节点已存在 → 原地更新文本（打字机效果），不重复追加
            const existing = m.id != null ? el.querySelector(`[data-id="${m.id}"]`) : null;
            if (existing) {
                if (m.streaming) {
                    const txtEl = existing.querySelector(".msg-text");
                    if (txtEl && txtEl.textContent !== m.text) {
                        txtEl.textContent = m.text;
                        // 流式追加文字：触发一次轻微"挪出"动画
                        txtEl.classList.remove("tick");
                        void txtEl.offsetWidth;
                        txtEl.classList.add("tick");
                        if (nearBottom) el.scrollTop = el.scrollHeight;
                    }
                }
                // decision 到达后 emotion 才确定：同步心情标签
                const meta = existing.querySelector(".msg-meta");
                const newMood = moodTag(m.emotion);
                if (meta && newMood) {
                    const moodEl = meta.querySelector(".msg-mood");
                    if (moodEl) { if (moodEl.outerHTML !== newMood) moodEl.outerHTML = newMood; }
                    else { meta.querySelector(".msg-name").insertAdjacentHTML("afterend", newMood); }
                }
                // 语音存档晚于消息到达：补插回放播放器
                if (!m.streaming && m.audio && !existing.querySelector(".msg-audio")) {
                    const bubble = existing.querySelector(".msg-bubble");
                    if (bubble) bubble.insertAdjacentHTML("beforeend", audioBarHtml(m.audio));
                }
                if (m.id > chatLastId) chatLastId = m.id;
                continue;
            }
            // 本地即时回显占位：后端同文本消息到达时，先移除占位再追加，避免重复
            if (m.role === "user" && !m.is_proactive && m.source === "webui") {
                const local = el.querySelector(".msg-local-echo");
                if (local) {
                    const t = local.querySelector(".msg-text");
                    if (t && t.textContent === m.text) local.remove();
                }
            }
            if (m.id <= chatLastId) continue;
            chatLastId = m.id;
            // 跨天时插入"今天/昨天"分隔条
            const dd = fmtDay(m.ts);
            if (dd && chatLastDate !== null && dd !== chatLastDate) {
                const div = document.createElement("div");
                div.className = "chat-day-divider";
                div.innerHTML = `<span>${dayLabel(m.ts)}</span>`;
                el.appendChild(div);
            }
            if (dd) chatLastDate = dd;
            el.appendChild(buildChatMsg(m));
            appended++;
        }
        if (appended && nearBottom) el.scrollTop = el.scrollHeight;
        updateChatHeader(res.active_name);
    } catch (e) {
        console.error("聊天轮询失败:", e);
    }
}

// 聊天头部：角色名 + 徽章 + 角色描述 + 头像（达妮娅等有头像的角色显示渲染头像）
function updateChatHeader(activeName) {
    const hName = document.getElementById("chatHeaderName");
    if (hName && activeName) hName.textContent = activeName;
    const badge = document.getElementById("chatTitleBadge");
    if (badge && activeName) {
        badge.textContent = (window.COMPANION_NAME || "达妮娅") === "达妮娅" ? "伴星" : "AI 伙伴";
    }
    const sub = document.getElementById("chatCharName");
    const char = characters.find(c => c.id === activeId);
    if (sub) {
        if (char && char.character) sub.textContent = char.character;
        else sub.textContent = activeName ? "正陪在你身边" : "—";
    }
    const hAv = document.getElementById("chatAvatarImg");
    if (!hAv) return;
    // activeId 未就绪时不要把头像藏掉（重载后首帧的竞态）
    if (!activeId) return;
    const src = CHAR_AVATARS[activeId];
    hAv.style.visibility = src ? "" : "hidden";
    if (src && hAv.getAttribute("src") !== src) hAv.setAttribute("src", src);
}

// AI 头像：达妮娅等有头像的角色显示渲染图，其余回退首字母
function aiAvatarHtml(charName) {
    const src = CHAR_AVATARS[activeId];
    const fb = escHtml((charName || "AI").charAt(0));
    if (src) {
        return `<img class="msg-avatar-img" src="${src}" alt="" onerror="this.remove()"><span class="msg-avatar-fallback">${fb}</span>`;
    }
    return `<span class="msg-avatar-fallback">${fb}</span>`;
}

// ===== 语音回放：悬停展开迷你播放器 + 线性进度条 =====
let chatPlayer = null;      // 共享的 <audio> 实例（一次只播一条）
let chatPlayerBox = null;   // 当前正在播放的 .msg-audio
let audioShowTimer = null;  // 悬停延迟计时器（0.6s 后展开）

function fmtDur(s) {
    s = Math.max(0, Math.floor(s || 0));
    const m = Math.floor(s / 60);
    const sec = String(s % 60).padStart(2, "0");
    return `${m}:${sec}`;
}

function updateAudioUi(bar) {
    if (!chatPlayer || chatPlayerBox !== bar) return;
    const fill = bar.querySelector(".audio-fill");
    const t = bar.querySelector(".audio-time");
    const dur = chatPlayer.duration || 0;
    const pct = dur ? (chatPlayer.currentTime / dur) * 100 : 0;
    if (fill) fill.style.width = pct + "%";
    if (t) t.textContent = dur ? `${fmtDur(chatPlayer.currentTime)} / ${fmtDur(dur)}` : "语音";
    const playing = !chatPlayer.paused && chatPlayerBox === bar;
    bar.classList.toggle("playing", playing);
    if (playing) bar.classList.add("show"); // 播放时保持展开
}

function toggleMsgAudio(btn) {
    const bar = btn.closest(".msg-audio");
    if (!bar) return;
    const url = bar.dataset.url;
    if (!chatPlayer) {
        chatPlayer = new Audio();
        chatPlayer.addEventListener("timeupdate", () => updateAudioUi(chatPlayerBox));
        chatPlayer.addEventListener("ended", () => {
            if (chatPlayerBox) {
                chatPlayerBox.classList.remove("playing");
                chatPlayerBox = null;
            }
        });
    }
    if (chatPlayerBox === bar && !chatPlayer.paused) {
        chatPlayer.pause();               // 点击已播放项 → 暂停
        bar.classList.remove("playing");
        updateAudioUi(bar);
        return;
    }
    if (chatPlayerBox && chatPlayerBox !== bar) {
        chatPlayerBox.classList.remove("playing");
    }
    chatPlayer.src = url;
    chatPlayerBox = bar;
    chatPlayer.currentTime = 0;
    chatPlayer.play();
    updateAudioUi(bar);
}

function audioBarHtml(url) {
    return `<div class="msg-audio" data-url="${escHtml(url)}">
        <button class="audio-play" type="button" title="语音回放" onclick="toggleMsgAudio(this)">
            <svg class="ic-play" width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
            <svg class="ic-pause" width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M6 5h4v14H6zM14 5h4v14h-4z"/></svg>
        </button>
        <div class="audio-track"><div class="audio-fill"></div></div>
        <span class="audio-time">语音</span>
    </div>`;
}

// 心情标签：AI 回复名字旁显示 LLM 决策情绪，方便对照看板娘表情
function moodTag(emotion) {
    const label = emotionLabel(emotion);
    if (!label) return "";
    return `<span class="msg-mood msg-mood-${String(emotion).toLowerCase()}">${label}</span>`;
}

function buildChatMsg(m) {
    const row = document.createElement("div");
    const text = escHtml(m.text);
    if (m.id != null) row.dataset.id = m.id;
    if (m.is_proactive) {
        // 主动互动记录：AI 侧，带"主动"标记
        row.className = "chat-msg msg-ai msg-proactive";
        row.innerHTML = `<div class="msg-avatar msg-avatar-ai">${aiAvatarHtml(m.char_name)}</div>
            <div class="msg-bubble">
                <div class="msg-meta">
                    <span class="msg-name">${escHtml(m.char_name || "AI")}</span>
                    <span class="msg-tag msg-tag-heart">主动</span>
                    ${moodTag(m.emotion)}
                    ${triggerLabel(m.trigger) ? `<span class="msg-tag">${escHtml(triggerLabel(m.trigger))}</span>` : ""}
                    <span class="msg-time">${fmtClock(m.ts)}</span>
                </div>
                <div class="msg-text">${text}</div>
                ${m.audio && !m.streaming ? audioBarHtml(m.audio) : ""}
            </div>`;
    } else if (m.role === "ai") {
        // 【2026-10-11 分段输出】seg_index>0 = 同一轮回复的"连发续条"（隐藏冗余头像/名字，贴近上一条）
        row.className = "chat-msg msg-ai" + (m.seg_index > 0 ? " msg-seg-cont" : "") + (m.failed ? " msg-failed" : "") + (m.streaming ? " msg-streaming" : "") + (m.blocked ? " msg-blocked" : "");
        row.innerHTML = `<div class="msg-avatar msg-avatar-ai">${aiAvatarHtml(m.char_name)}</div>
            <div class="msg-bubble">
                <div class="msg-meta">
                    <span class="msg-name">${escHtml(m.char_name || "AI")}</span>
                    ${moodTag(m.emotion)}
                    ${m.blocked ? `<span class="msg-tag msg-tag-blocked">被拦截${m.block_reason ? "·" + escHtml(m.block_reason) : ""}</span>` : ""}
                    ${m.failed ? '<span class="msg-tag msg-tag-error">发送失败</span>' : ""}
                    <span class="msg-time">${fmtClock(m.ts)}</span>
                </div>
                <div class="msg-text">${text}${m.streaming ? '<span class="stream-cursor">▍</span>' : ""}</div>
                ${m.audio && !m.streaming ? audioBarHtml(m.audio) : ""}
            </div>`;
    } else {
        row.className = "chat-msg msg-user" + (m.local ? " msg-local-echo" : "");
        const srcTag = m.source === "webui" ? "" : (m.source === "unity" ? `<span class="msg-tag">Unity</span>` : "");
        row.innerHTML = `<div class="msg-bubble">
                <div class="msg-meta">
                    <span class="msg-name">你</span>${srcTag}
                    <span class="msg-time">${fmtClock(m.ts)}</span>
                </div>
                <div class="msg-text">${text}</div>
            </div>
            <div class="msg-avatar msg-avatar-user">你</div>`;
    }
    return row;
}

let localEchoSeq = 0;
async function sendChat() {
    const input = document.getElementById("chatInput");
    const text = (input.value || "").trim();
    if (!text) return;
    input.value = "";
    const el = document.getElementById("chatMessages");
    // 本地即时回显：不等后端轮询，发送瞬间就上屏（治"用户消息不及时"）。
    // 用负 id 占位，后端轮询到达同文本消息时自动替换为真实记录。
    if (el) {
        const emptyEl = el.querySelector(".chat-empty");
        if (emptyEl) emptyEl.remove();
        el.appendChild(buildChatMsg({
            id: -(++localEchoSeq),
            role: "user",
            text,
            source: "webui",
            ts: Date.now() / 1000,
            is_proactive: false,
            local: true,
        }));
        el.scrollTop = el.scrollHeight;
    }
    try {
        await pywebview.api.send_chat(text);
    } catch (e) {
        console.error("发送失败:", e);
        if (el) {
            const local = el.querySelector(".msg-local-echo");
            if (local) local.remove();
        }
        return;
    }
    // 发送后立即拉一次后端记录，把占位替换成真实消息
    await chatPoll();
}

async function clearChat() {
    chatLastId = 0;
    chatLastDate = null;
    try {
        await pywebview.api.clear_chat();
    } catch (e) { /* 忽略 */ }
    const el = document.getElementById("chatMessages");
    if (el) {
        const avSrc = CHAR_AVATARS[activeId] || "assets/danya_avatar.png";
        el.innerHTML = `<div class="chat-empty">
            <div class="chat-empty-avatar">
                <img id="chatEmptyAvatar" src="${avSrc}" alt="角色头像" onerror="this.style.visibility='hidden'">
            </div>
            <div class="chat-empty-title">达妮娅在等你</div>
            <div class="chat-empty-text">轻轻说一句，她就会回应你</div>
        </div>`;
    }
}

// 持续对话开关：语音免唤醒持续识别（运行时立即生效 + 写回配置文件）
async function loadVoiceToggle() {
    try {
        const res = await pywebview.api.get_voice_settings();
        if (res && res.success) {
            const cb = document.getElementById("chatContinuousToggle");
            if (cb) cb.checked = !!res.continuous;
            const mic = document.getElementById("chatMicToggle");
            if (mic) mic.checked = !!res.listening;
        }
    } catch (e) {
        console.error("读取语音开关失败:", e);
    }
}

// 麦克风监听总开关：关闭后释放麦克风设备（唤醒词/持续识别全停），开启恢复
async function setMicListening(checked) {
    try {
        const res = await pywebview.api.set_mic_listening(!!checked);
        if (!res || !res.success) {
            const cb = document.getElementById("chatMicToggle");
            if (cb) cb.checked = !checked;  // 失败回滚
        }
    } catch (e) {
        console.error("设置麦克风监听失败:", e);
        const cb = document.getElementById("chatMicToggle");
        if (cb) cb.checked = !checked;
    }
}

async function setContinuousVoice(checked) {
    try {
        const res = await pywebview.api.set_continuous_voice(!!checked);
        if (!res || !res.success) {
            const cb = document.getElementById("chatContinuousToggle");
            if (cb) cb.checked = !checked;  // 失败回滚
        }
    } catch (e) {
        console.error("设置持续对话失败:", e);
        const cb = document.getElementById("chatContinuousToggle");
        if (cb) cb.checked = !checked;
    }
}

document.addEventListener("DOMContentLoaded", () => {
    const input = document.getElementById("chatInput");
    if (input) {
        input.addEventListener("keydown", e => {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                sendChat();
            }
        });
    }
    // 聊天是默认激活的 Tab：页面加载即启动轮询，不点 Tab 按钮也能即时刷新
    //（原实现只在点击 Tab 时才启动定时器，而聊天 Tab 默认激活很少被点 → 消息"不及时"）
    const chatPanel = document.getElementById("tab-chat");
    if (chatPanel && chatPanel.classList.contains("active")) {
        chatTabActivated = true;
        chatTimer = setInterval(chatPoll, 800);
        chatPoll();
    }
    // 读取语音开关状态必须在 pywebview bridge 就绪后执行：DOMContentLoaded 时
    // API 往往还没注入，直接调用会失败 → 开关保持 HTML 默认"关闭"，而实际 ASR
    // 按 config 启动（可能正在监听）→ UI 与实际不同步。等就绪再读，失败不覆盖。
    waitForPywebview().then(loadVoiceToggle).catch(() => {
        console.error("pywebview 未就绪，语音开关状态保持默认");
    });
    // 语音输入按钮：提示可用语音方式（开启持续对话后可直接说话），并聚焦输入框
    const voiceBtn = document.getElementById("voiceInputBtn");
    if (voiceBtn) {
        voiceBtn.addEventListener("click", () => {
            const input = document.getElementById("chatInput");
            if (input) input.focus();
            const toggle = document.getElementById("chatContinuousToggle");
            showToast(toggle && toggle.checked ? "持续对话已开启，直接说话就能输入～" : "打字直接说，或开启「持续对话」后用语音输入", "info");
        });
    }
    // 聊天消息区鼠标跟随白光（CSS 变量驱动，不抢交互）
    const msgBox = document.getElementById("chatMessages");
    if (msgBox) {
        msgBox.addEventListener("mousemove", (e) => {
            const r = msgBox.getBoundingClientRect();
            msgBox.style.setProperty("--mx", (e.clientX - r.left) + "px");
            msgBox.style.setProperty("--my", (e.clientY - r.top) + "px");
        });
        // 语音回放：鼠标停在该条消息上 0.6s 后展开迷你播放器（线性动画），移出且未播放则收起
        // 注意：播放器收起时不可见不可命中，hover 判定必须以整个消息行为单位
        msgBox.addEventListener("mouseover", (e) => {
            const row = e.target.closest(".chat-msg");
            if (!row) return;
            const bar = row.querySelector(".msg-audio");
            if (!bar) return;
            clearTimeout(audioShowTimer);
            audioShowTimer = setTimeout(() => bar.classList.add("show"), 600);
        });
        msgBox.addEventListener("mouseout", (e) => {
            const row = e.target.closest(".chat-msg");
            if (!row) return;
            const to = e.relatedTarget;
            if (to && row.contains(to)) return; // 移入本消息内部，保持展开
            clearTimeout(audioShowTimer);
            const bar = row.querySelector(".msg-audio");
            if (bar && (chatPlayerBox !== bar || !chatPlayer || chatPlayer.paused)) {
                bar.classList.remove("show");
            }
        });
    }
    // 窗口获得焦点时立即刷新聊天（pywebview 后台/切窗口会节流定时器，聚焦时主动拉一次）
    window.addEventListener("focus", () => {
        const panel = document.getElementById("tab-chat");
        if (panel && panel.classList.contains("active")) chatPoll();
    });
});

// ================================
// 感知调试台（1s 轮询 get_vision_debug）
// ================================
let dbgTimer = null;
let dbgRunning = false;
let _lastFrameSig = "";   // 感知调试台帧签名：仅在帧真正变化时重解码，避免每秒重建 <img> 卡死主线程

function startDebugPoll() {
    dbgPollNow();
    dbgTimer = setInterval(() => dbgPollNow(), 1000);
    setDbgPollState(true);
}

function stopDebugPoll() {
    if (dbgTimer) { clearInterval(dbgTimer); dbgTimer = null; }
    setDbgPollState(false);
}

function setDbgPollState(on) {
    const el = document.getElementById("dbgPollState");
    if (el) {
        el.classList.toggle("online", on);
        el.textContent = on ? "● 轮询中" : "● 已停止";
    }
}

async function dbgPollNow() {
    // 调试台切到后台时跳过整轮轮询：省一次 Python 往返 + 大块 DOM 重建/帧解码，
    // 避免后台常驻把主线程吃满导致整个软件 UI 卡死（调试台轮询从不自动停）。
    const panel = document.getElementById("tab-debug");
    if (!panel || !panel.classList.contains("active")) {
        setDbgPollState(true);
        return;
    }
    setDbgPollState(true);
    try {
        const data = await pywebview.api.get_vision_debug();
        document.getElementById("dbgLastRefresh").textContent =
            new Date().toLocaleTimeString("zh-CN", { hour12: false }) + " · " + data.ts;
        renderDebug(data);
    } catch (e) {
        console.error("调试台轮询失败:", e);
        const body = document.getElementById("dbgCapture");
        if (body) body.innerHTML = `<div class="err">调试台需要 GUI 模式运行<br>（pywebview bridge 不可用）</div>`;
    }
}

// 对话悬浮层：游戏画面上透明显示 AI 说的话 + 你语音说过去的话
async function toggleChatOverlay() {
    const btn = document.getElementById("chatOverlayBtn");
    const on = !!btn && btn.classList.contains("btn-active");
    try {
        if (!on) {
            const r = await pywebview.api.start_chat_overlay();
            if (r && r.success) {
                showToast("已打开对话悬浮层：AI 的话和你的语音会透明显示在屏幕上（拖动可调位置，Esc 关闭）", "success");
                if (btn) btn.classList.add("btn-active");
            } else {
                showToast((r && r.error) || "打开失败", "error");
            }
        } else {
            await pywebview.api.stop_chat_overlay();
            showToast("已关闭对话悬浮层", "success");
            if (btn) btn.classList.remove("btn-active");
        }
    } catch (e) {
        console.error("切换对话悬浮层失败:", e);
        showToast("切换失败（需主程序 GUI 模式运行）", "error");
    }
}

// 打开 OCR 区域校准悬浮窗：把击杀/阵亡识别区域画到真实屏幕上拖动调整
async function startOcrCalibration() {
    try {
        const r = await pywebview.api.start_ocr_calibration();
        if (r && r.success) {
            showToast("已打开 OCR 区域校准：拖动色块=移动，右下角白点=缩放，S 保存，R 恢复默认，Esc 关闭", "success");
        } else {
            showToast((r && r.error) || "打开失败", "error");
        }
    } catch (e) {
        console.error("启动 OCR 区域校准失败:", e);
        showToast("启动失败（需主程序 GUI 模式运行）", "error");
    }
}

// OCR 设置里的校准开关：打开 → 弹出校准悬浮窗；关闭 → 收起悬浮窗
async function toggleOcrCalibration(el) {
    const on = !!el.checked;
    try {
        if (on) {
            const r = await pywebview.api.start_ocr_calibration();
            if (r && r.success) {
                showToast("校准模式已开启：拖动色块=移动，右下角白点=缩放，S 保存锁定位置，Esc 关闭", "success");
            } else {
                el.checked = false; // 打开失败回弹
                showToast((r && r.error) || "打开失败", "error");
            }
        } else {
            await pywebview.api.stop_ocr_calibration();
            showToast("已收起校准悬浮窗（识别位置已锁定）", "success");
        }
    } catch (e) {
        el.checked = !on; // 异常回弹
        console.error("切换 OCR 校准失败:", e);
        showToast("切换失败（需主程序 GUI 模式运行）", "error");
    }
}

// ---- 自定义识别区域管理（新增/删除/改用途/逐区域实时文字） ----
const OCR_PURPOSE_LABEL = { kill: "击杀播报", kill_hint: "击杀提示(中下)", page: "页面名", settle: "结算/胜负", hud: "顶部HUD", death: "阵亡名片", general: "通用" };

function ocrEsc(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// 逐区域渲染：区域名 · 用途(下拉) · 坐标 · 实时扫描文字 · 删除
function renderOcrRegions(ocr) {
    const el = document.getElementById("dbgOcrRegions");
    if (!el) return;
    const rlist = ocr.regions || {};
    const texts = ocr.region_texts || {};
    const names = Object.keys(rlist);
    if (!names.length) {
        el.innerHTML = '<div class="ocr-region-empty">暂无识别区域 —— 在上方输入名字点「＋新增区域」添加，再开校准悬浮窗拖动位置</div>';
        return;
    }
    const rows = names.map(n => {
        const meta = rlist[n] || {};
        const rect = meta.rect || [];
        const pur = meta.purpose || "general";
        const coord = rect.length === 4
            ? rect.map(v => Number(v).toFixed(2)).join(", ")
            : "—";
        const raw = texts[n] || "";
        const txt = raw
            ? `<span class="ocr-region-txt">${ocrEsc(raw.slice(0, 90))}</span>`
            : '<span class="ocr-region-empty">（无文字）</span>';
        const opts = Object.keys(OCR_PURPOSE_LABEL).map(p =>
            `<option value="${p}" ${p === pur ? "selected" : ""}>${OCR_PURPOSE_LABEL[p]}</option>`).join("");
        return `<div class="ocr-region-row">
            <span class="ocr-region-name" title="${ocrEsc(n)}">${ocrEsc(n)}</span>
            <select class="ocr-region-purpose" title="用途：该区域文字用于什么判断（击杀/页面名/结算/顶部HUD/通用）"
                    onchange="ocrSetRegionPurpose('${ocrEsc(n)}', this.value)">${opts}</select>
            <span class="ocr-region-coord" title="相对坐标 x,y,w,h (0~1)">${coord}</span>
            <span class="ocr-region-text">${txt}</span>
            <button class="btn btn-mini ocr-region-del" title="删除该区域" onclick="ocrRemoveRegion('${ocrEsc(n)}')">${DIcon.svg('x')}</button>
        </div>`;
    }).join("");
    el.innerHTML = '<div class="ocr-region-head">区域名 · 用途 · 坐标(x,y,w,h) · 实时扫描文字</div>' + rows;
}

// 文字锚点训练器：逐条渲染锚点规则（区域 + 关键词 → 页面状态），可编辑/删除/新增
function renderOcrAnchors(ocr) {
    const el = document.getElementById("dbgOcrAnchors");
    const stEl = document.getElementById("dbgAnchorState");
    if (stEl) {
        const labels = (ocr.anchor_hit_labels || []).filter(Boolean);
        stEl.innerHTML = labels.length
            ? `<span class="ok">${DIcon.svg('circle-check')} ${labels.map(l => ocrEsc(l)).join("　·　")}</span>`
            : '<span class="muted">未命中（等 OCR 扫描到区域文字）</span>';
    }
    if (!el) return;
    // 用户正在编辑（新增/删除/改规则）时不要重绘：调试台每 1s 轮询一次，
    // 若每次都用服务器数据重绘，本地还没保存的修改（新加的行/删掉的行）
    // 会被瞬间冲掉——"训练器加不了东西也删不了东西"的根因。
    if (window._anchorDirty) return;
    const anchors = (ocr.anchors || []);
    const texts = ocr.region_texts || {};
    const regions = ocr.regions || {};
    window._dbgRegions = regions; // 供 anchorAdd 新加行时填充区域下拉
    const regionNames = Object.keys(regions);
    const row = (a, i) => {
        // 规则原区域可能不在当前区域列表里（如区域被删除/区域下拉数据不全），
        // 补进选项避免保存时被静默改成第一个区域（实测：区域被覆盖后保存，
        // 4 条规则全被改成了同一区域）
        const names = regionNames.includes(a.region) ? regionNames : [a.region, ...regionNames];
        const rSel = names.map(r =>
            `<option value="${ocrEsc(r)}" ${r === a.region ? "selected" : ""}>${ocrEsc(r)}</option>`).join("");
        const txt = texts[a.region] || "";
        const gSel = [["", "通用"], ["menu", "菜单/主页"], ["match", "对局内状态"]].map(g =>
            `<option value="${g[0]}" ${(a.group || "") === g[0] ? "selected" : ""}>${g[1]}</option>`).join("");
        // 本帧该规则是否命中（后端 match_anchors 的最终结果，含互斥过滤）：
        // 高亮出来，方便用户确认"OCR 扫到长弓溪谷 → 锚点到底生效没"
        const hit = (ocr.anchor_hit_states || []).includes(a.state);
        // 规则指向的区域已不存在（被改名/重加）→ 高亮警告，提醒用户核对
        const orphaned = a.region && !regionNames.includes(a.region) && !(a.region_purpose && Object.values(regions).some(m => m.purpose === a.region_purpose));
        return `<div class="ocr-anchor-row ${hit ? "ocr-anchor-row-hit" : ""}" data-idx="${i}">
            <span class="ocr-anchor-hit-badge" title="${hit ? "这条规则本帧命中（OCR 扫到了关键词）" : "本帧未命中"}">${hit ? "" : "○"}</span>
            <select class="ocr-anchor-region ${orphaned ? "ocr-anchor-orphan" : ""}" title="${orphaned ? " 该区域已不存在，保存后后端会按用途自动匹配；建议在下拉里重新选一次" : ""}" onchange="anchorEdited()">${rSel || '<option value="">（无区域）</option>'}</select>
            <input class="ocr-anchor-keywords" value="${ocrEsc((a.keywords || []).join(","))}"
                   placeholder="关键词，逗号分隔" oninput="anchorEdited()">
            <input class="ocr-anchor-label" value="${ocrEsc(a.label)}"
                   placeholder="页面状态描述，如：正在撤离倒计时" oninput="anchorEdited()">
            <select class="ocr-anchor-group" title="页面上下文：菜单/主页命中时会压掉对局内状态的识别（主页不该有撤离成功/失败）" onchange="anchorEdited()">${gSel}</select>
            <button class="btn btn-mini ocr-anchor-del" title="删除这条规则" onclick="anchorDel(this)">${DIcon.svg('x')}</button>
            <div class="ocr-anchor-region-text">该区域实时文字：${txt ? ocrEsc(txt.slice(0, 60)) : '<span class="muted">（无文字）</span>'}</div>
        </div>`;
    };
    const list = anchors.length
        ? anchors.map((a, i) => row(a, i)).join("")
        : '<div class="ocr-anchor-empty">暂无锚点规则 —— 点下方「＋新增锚点」添加，例如：区域 top_hud 出现"撤离倒计时"→ 判定"正在撤离倒计时"</div>';
    el.innerHTML = list + `
        <div class="ocr-anchor-add">
            <button class="btn ocr-add-btn" onclick="anchorAdd()">＋ 新增锚点</button>
            <button class="btn ocr-add-btn" onclick="anchorSave()" title="保存全部锚点规则到 config.yaml 并热生效">保存锚点</button>
        </div>`;
}

// 新增空锚点规则（默认选中第一个区域，关键词/描述留空等用户填）
function anchorAdd() {
    const el = document.getElementById("dbgOcrAnchors");
    if (!el) return;
    window._anchorDirty = true; // 标记本地有未保存修改，轮询不重绘
    el.insertAdjacentHTML("beforeend", `
        <div class="ocr-anchor-row" data-idx="">
            <span class="ocr-anchor-hit-badge">○</span>
            <select class="ocr-anchor-region" onchange="anchorEdited()"></select>
            <input class="ocr-anchor-keywords" value="" placeholder="关键词，逗号分隔" oninput="anchorEdited()">
            <input class="ocr-anchor-label" value="" placeholder="页面状态描述，如：正在撤离倒计时" oninput="anchorEdited()">
            <select class="ocr-anchor-group" title="页面上下文：菜单/主页命中时会压掉对局内状态的识别" onchange="anchorEdited()">
                <option value="">通用</option><option value="menu">菜单/主页</option><option value="match">对局内状态</option>
            </select>
            <button class="btn btn-mini ocr-anchor-del" title="删除这条规则" onclick="anchorDel(this)">${DIcon.svg('x')}</button>
            <div class="ocr-anchor-region-text"></div>
        </div>`);
    const sel = el.querySelector(".ocr-anchor-row:last-child .ocr-anchor-region");
    const regions = window._dbgRegions || {};
    sel.innerHTML = Object.keys(regions).map(r =>
        `<option value="${ocrEsc(r)}">${ocrEsc(r)}</option>`).join("") || '<option value="">（无区域）</option>';
    const txt = regions[sel.value] ? "" : "";
    const rowDiv = sel.closest(".ocr-anchor-row");
    const txtDiv = rowDiv.querySelector(".ocr-anchor-region-text");
    if (txtDiv) txtDiv.textContent = txt;
    showToast("已新增一条锚点规则：选区域、填关键词和页面状态描述", "info");
}

// 收集当前所有锚点规则（区域/关键词/描述），供保存
function anchorCollect() {
    const el = document.getElementById("dbgOcrAnchors");
    if (!el) return [];
    const out = [];
    el.querySelectorAll(".ocr-anchor-row").forEach((r, i) => {
        const region = r.querySelector(".ocr-anchor-region")?.value || "";
        const kws = (r.querySelector(".ocr-anchor-keywords")?.value || "")
            .split(/[,，]/).map(s => s.trim()).filter(Boolean);
        const label = (r.querySelector(".ocr-anchor-label")?.value || "").trim();
        const group = (r.querySelector(".ocr-anchor-group")?.value || "").trim();
        if (region && kws.length) {
            // 记录区域用途：区域名可能被校准改名（页面名字→页面→页面名字），
            // 名字失联时后端按用途自动找唯一同用途区域，规则不会永久失效
            const regMeta = (window._dbgRegions || {})[region];
            const region_purpose = regMeta && regMeta.purpose ? String(regMeta.purpose) : "";
            out.push({ region, region_purpose, keywords: kws, label: label || kws[0], state: "anchor_" + i, group });
        }
    });
    return out;
}

function anchorEdited() {
    // 输入即标记：保存时统一收集（无需每击一次调后端）；同时让 1s 轮询
    // 不要重绘冲掉正在编辑的内容
    window._anchorDirty = true;
}

async function anchorDel(btn) {
    const row = btn.closest(".ocr-anchor-row");
    if (row) {
        row.remove();
        window._anchorDirty = true; // 标记本地有未保存修改
    }
    showToast("已删除（记得点「保存锚点」生效）", "info");
}

async function anchorSave() {
    const anchors = anchorCollect();
    try {
        const r = await pywebview.api.ocr_set_anchors(anchors);
        if (r && r.success) {
            window._anchorDirty = false; // 已保存，恢复轮询重绘
            showToast(`已保存 ${(r.anchors || []).length} 条锚点规则，OCR 扫描到关键词即判定页面状态`, "success");
            dbgPollNow();
        } else {
            showToast((r && r.error) || "保存锚点失败", "error");
        }
    } catch (e) {
        console.error("保存锚点失败:", e);
        showToast("保存失败（需主程序 GUI 模式运行）", "error");
    }
}

// 新增区域：默认全屏，开校准悬浮窗拖动到目标位置后 S 保存
async function ocrAddRegion() {
    const input = document.getElementById("ocrNewRegionName");
    const name = (input.value || "").trim();
    if (!name) { showToast("请输入区域名", "error"); return; }
    const purpose = document.getElementById("ocrNewRegionPurpose").value;
    try {
        const r = await pywebview.api.ocr_add_region(name, purpose);
        if (r && r.success) {
            input.value = "";
            showToast(`已新增区域「${name}」（点上方校准开关在屏幕上拖动位置，S 保存）`, "success");
            dbgPollNow();
        } else {
            showToast((r && r.error) || "新增区域失败", "error");
        }
    } catch (e) {
        console.error("新增 OCR 区域失败:", e);
        showToast("新增失败（需主程序 GUI 模式运行）", "error");
    }
}

async function ocrRemoveRegion(name) {
    if (!confirm(`删除识别区域「${name}」？`)) return;
    try {
        const r = await pywebview.api.ocr_remove_region(name);
        if (r && r.success) showToast(`已删除区域「${name}」`, "success");
        else showToast((r && r.error) || "删除失败", "error");
        dbgPollNow();
    } catch (e) {
        console.error("删除 OCR 区域失败:", e);
        showToast("删除失败（需主程序 GUI 模式运行）", "error");
    }
}

async function ocrSetRegionPurpose(name, purpose) {
    try {
        const r = await pywebview.api.ocr_set_region_purpose(name, purpose);
        if (r && r.success) {
            showToast(`「${name}」用途已设为 ${OCR_PURPOSE_LABEL[purpose] || purpose}`, "success");
            dbgPollNow();
        } else {
            showToast((r && r.error) || "修改用途失败", "error");
        }
    } catch (e) {
        console.error("修改 OCR 区域用途失败:", e);
        showToast("修改失败（需主程序 GUI 模式运行）", "error");
    }
}

// 保存游戏玩家名：击杀播报「击杀者A 枪械图标 被杀者B」纯 OCR 归属判断的钥匙。
// 左侧匹配玩家名 → 你击杀（kill）；右侧匹配 → 你阵亡（died）；不涉及 → 别人互杀静默。
async function ocrSavePlayerName() {
    const input = document.getElementById("ocrPlayerName");
    const name = (input.value || "").trim();
    try {
        const r = await pywebview.api.ocr_set_player_name(name);
        if (r && r.success) {
            showToast(name
                ? `已记住你的游戏名字「${r.player_name}」，击杀播报出现就能认出你啦`
                : "已清空玩家名（会改用 AI 看图确认击杀）", "success");
            dbgPollNow();
        } else {
            showToast((r && r.error) || "保存失败", "error");
        }
    } catch (e) {
        console.error("保存玩家名失败:", e);
        showToast("保存失败（需主程序 GUI 模式运行）", "error");
    }
}

function fmtAgo(ts, now) {
    if (!ts) return "—";
    const d = (now - ts);
    if (d < 0) return "0s";
    if (d < 60) return Math.round(d) + "s前";
    if (d < 3600) return Math.round(d / 60) + "m前";
    return Math.round(d / 3600) + "h前";
}
function fmtClock(ts) {
    if (!ts) return "—";
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString("zh-CN", { hour12: false });
}

function kvRow(k, v, cls) {
    return `<div class="kv"><span class="k">${k}</span><span class="v ${cls || ""}">${v}</span></div>`;
}

// 录制游戏音效参考音：点按钮 → 立即录 2.5s 当前系统输出音频 → 保存模板并热生效
async function recordAudioRef(btn) {
    const type = btn.dataset.type || "";
    if (!type) return;
    btn.disabled = true;
    btn.textContent = "录制中…";
    try {
        const r = await pywebview.api.record_audio_reference(type, 2.5);
        if (r && r.success) {
            showToast(`已录制${type}参考音（${r.info.duration_s}s），识别已热生效`, "success");
        } else {
            showToast((r && r.error) || "录制失败", "error");
        }
    } catch (e) {
        console.error("录制参考音失败:", e);
        showToast("录制失败（需主程序 GUI 模式运行）", "error");
    } finally {
        btn.disabled = false;
        btn.textContent = btn.dataset.type === "kill" ? "录制击杀音" :
            btn.dataset.type === "headshot" ? "录制爆头音" :
            btn.dataset.type === "died" ? "录制阵亡音" :
            btn.dataset.type === "victory" ? "录制胜利音" : "录制失败音";
    }
}

function renderDebug(data) {
    const now = data.ts;

    // ---- 云端所见画面（真全双工推给云端模型的那一帧） ----
    const dpro = data.proactive;
    const dfEl = document.getElementById("dbgDuplexFrame");
    if (dfEl) {
        if (dpro && dpro.duplex_frame_b64) {
            const status = dpro.duplex_status || "—";
            const isCloud = dpro.duplex_mode === "realtime" || dpro.duplex_mode === "cloud";
            const modeTxt = isCloud ? "云端真全双工"
                : dpro.duplex_mode === "local_omni" ? "本地真全双工（Comni 本地模型）"
                : dpro.duplex_mode === "local" ? "本地真全双工（本地AI模型）"
                : dpro.duplex_mode === true ? "快照双工" : "规则引信";
            const nativeTxt = dpro.duplex_native_audio
                ? (isCloud ? "云端原生语音" : "本地原生语音（Comni）")
                : "本地TTS合成";
            const rpt = dpro.duplex_report_last
                ? `<div style="margin-top:4px;font-size:12px;color:#8be9fd">${DIcon.svg('brain')} 模型理解: ${ocrEsc(dpro.duplex_report_last)}` +
                  (dpro.duplex_report_ago != null ? ` <span style="color:var(--text-muted)">（${dpro.duplex_report_ago}s 前）</span>` : "") + `</div>`
                : `<div style="margin-top:4px;font-size:12px;color:var(--text-muted)">模型理解: 尚未收到观察汇报` +
                  `（${dpro.duplex_report_s > 0 ? "模型还没发，调试用，正常" : "已关闭 config duplex_report_s=0"}）</div>`;
            // 帧只在“真正变化”时重解码：用 长度+尾部64字符 做廉价签名，
            // 避免每秒把整张 base64 JPEG 塞进 innerHTML 重新创建 <img> 导致主线程卡死。
            let img = dfEl.querySelector("img.dbg-duplex-img");
            let meta = dfEl.querySelector(".dbg-duplex-meta");
            if (!img) {
                dfEl.innerHTML =
                    `<img class="dbg-duplex-img" alt="云端所见画面" style="max-width:100%;max-height:280px;border-radius:8px;border:1px solid rgba(255,107,157,.25)" title="这就是推给模型看的那帧画面（本地 Comni / 云端）">` +
                    `<div class="dbg-duplex-meta" style="margin-top:6px;font-size:12px;color:var(--text-muted)"></div>`;
                img = dfEl.querySelector("img.dbg-duplex-img");
                meta = dfEl.querySelector(".dbg-duplex-meta");
            }
            const sig = dpro.duplex_frame_b64.length + ":" + dpro.duplex_frame_b64.slice(-64);
            if (sig !== _lastFrameSig) {
                _lastFrameSig = sig;
                img.src = "data:image/jpeg;base64," + dpro.duplex_frame_b64;
            }
            meta.innerHTML =
                `模式: ${modeTxt} · 状态: ${ocrEsc(status)} · 语音: ${nativeTxt}` +
                ` · 最近推流帧 ${dpro.duplex_frame_ago != null ? `${dpro.duplex_frame_ago}s 前` : "—"}` + rpt;
        } else {
            _lastFrameSig = "";
            dfEl.innerHTML = `<div style="color:var(--text-muted)">未在推流（真全双工未启动或尚未抓到第一帧）<br>` +
                (dpro ? `当前 duplex_mode = ${String(dpro.duplex_mode)}` : "proactive 未注入") + `</div>`;
        }
    }

    // ---- 屏幕采样 ----
    const cap = data.capture;
    const capEl = document.getElementById("dbgCapture");
    if (capEl && cap) {
        const staticPct = cap.static_streak_s > 0
            ? Math.min(100, Math.round((cap.static_seconds / cap.static_streak_s) * 100)) : 0;
        const staticCls = staticPct >= 100 ? "warn" : "";
        const bar = `<div style="margin-top:6px;height:5px;background:rgba(255,107,157,.12);border-radius:3px;overflow:hidden">
            <div style="width:${staticPct}%;height:100%;background:linear-gradient(90deg,#ff6b9d,#ffb3c8);transition:width .3s"></div></div>`;
        capEl.innerHTML =
            kvRow("运行状态", cap.enabled ? '<span class="ok">● 采样中</span>' : '<span class="err">● 已禁用</span>') +
            kvRow("采样次数", `${cap.sample_count}（变化 ${cap.change_count} / 送分析 ${cap.analyze_count}）`) +
            kvRow("基础间隔", `${cap.interval_s}s ±${Math.round(cap.jitter * 100)}%（当前 ${cap.last_interval}s）`) +
            kvRow("上次采样", fmtClock(cap.last_ts) + " · " + fmtAgo(cap.last_ts, now)) +
            kvRow("静态画面", `${cap.static_seconds}s / ${cap.static_streak_s}s`, staticCls) +
            kvRow("静态加速", `提速 ${cap.static_interval_scale}x · 每${cap.static_analyze_every}次补分析`) +
            bar;
    } else if (capEl && data.capture_error) {
        capEl.innerHTML = `<div class="err">${data.capture_error}</div>`;
    }

    // ---- 视觉分析 ----
    const ana = data.analyzer;
    const anaEl = document.getElementById("dbgAnalyzer");
    if (anaEl && ana) {
        const failCls = ana.fail_count >= ana.fail_limit ? "err" : (ana.fail_count > 0 ? "warn" : "");
        // 屏幕识别记录列表（用户可见的识别结果）
        const anaList = (ana.recent || []).slice().reverse().map(it =>
            `<div class="tl-item"><span class="tl-time">${fmtClock(it.ts)}</span><div class="tl-main">
               <div class="tl-summary">${it.summary || ""}</div>
               <div class="tl-meta"><span class="tl-tag">${it.app_hint || "?"}</span>${fmtAgo(it.ts, now)}</div></div></div>`).join("");
        anaEl.innerHTML =
            kvRow("LLM 视觉", ana.vision_available ? '<span class="ok">可用</span>' : '<span class="err">不可用</span>') +
            kvRow("分析器", ana.disabled ? '<span class="err">已禁用（连续失败）</span>' : '<span class="ok">正常</span>') +
            kvRow("调用次数", `${ana.call_count} 次（去重跳过 ${ana.dedup_skip} 次）`) +
            kvRow("上次调用", (ana.last_call_ts ? fmtClock(ana.last_call_ts) + " · " + fmtAgo(ana.last_call_ts, now) : "从未调用")) +
            kvRow("失败", `${ana.fail_count} / ${ana.fail_limit}`, failCls) +
            kvRow("节流", `LLM 最小间隔 ${ana.min_interval_s}s · 去重 TTL ${ana.cache_ttl_s || "?"}s`) +
            `<div style="margin-top:8px;font-size:12px;color:var(--text-muted)">最近屏幕识别（${(ana.recent || []).length} 条，新→旧）</div>` +
            (anaList || '<div style="color:var(--text-muted);font-size:12px">暂无记录（画面无变化或视觉未触发）</div>');
    } else if (anaEl && data.analyzer_error) {
        anaEl.innerHTML = `<div class="err">${data.analyzer_error}</div>`;
    }

    // ---- 主动搭话 ----
    const pro = data.proactive;
    const proEl = document.getElementById("dbgProactive");
    if (proEl && pro) {
        proEl.innerHTML =
            kvRow("策略", pro.enabled ? '<span class="ok">启用</span>' : '<span class="err">禁用</span>', "") +
            kvRow("累计模式", pro.accumulate_screen ? "开启（先累计后批量互动）" : "关闭") +
            kvRow("累计缓冲", `${pro.buffer_len} / ${pro.batch_max_entries} 条`) +
            kvRow("下次批量互动", `${pro.next_batch_in}s 后（每 ${pro.batch_interval_s}s）`) +
            kvRow("屏幕活动", `${pro.last_screen_activity_ago}s 前（需在 ${pro.batch_interval_s}s 内才批量互动）`) +
            kvRow("状态机", (pro.state || "—") + ` · 距上次互动 ${pro.last_proactive_ago}s`) +
            kvRow("今日互动", `${pro.count_today} 次` + (pro.max_per_day > 0 ? ` / ${pro.max_per_day} 次上限` : "（不限）") + (Object.keys(pro.type_count_today).length ? ` · 按类: ${JSON.stringify(pro.type_count_today)}` : "")) +
            kvRow("游戏沉浸", pro.game_immersive ? '<span class="ok">开启（进游戏后无视频率限制，持续互动）</span>' : '<span>关闭</span>') +
            kvRow("用户空闲", `${pro.last_user_activity_ago}s（阈值 ${pro.idle_threshold_s}s）`);
    } else if (proEl && data.proactive_error) {
        proEl.innerHTML = `<div class="err">${data.proactive_error}</div>`;
    }

    // ---- 前台监听 ----
    const fg = data.foreground;
    const fgEl = document.getElementById("dbgForeground");
    if (fgEl && fg) {
        const hintCls = fg.last_hint === "game" ? "ok" : "";
        fgEl.innerHTML =
            kvRow("监听", fg.enabled ? '<span class="ok">启用</span>' : '<span class="err">禁用</span>') +
            kvRow("前台窗口", `<span title="${(fg.last_title || "").replace(/"/g, "&quot;")}">${(fg.last_title || "—").slice(0, 24) || "—"}</span>`) +
            kvRow("前台类型", fg.last_hint ? `<span class="${hintCls}">${fg.last_hint}</span>` : '<span class="warn">未识别</span>') +
            kvRow("系统空闲", (fg.idle_seconds != null ? `${fg.idle_seconds}s` : "—")) +
            kvRow("切换次数", `${fg.switch_count} 次`);
    } else if (fgEl && data.foreground_error) {
        fgEl.innerHTML = `<div class="err">${data.foreground_error}</div>`;
    }

    // ---- 游戏 OCR ----
    const ocr = data.ocr;
    const ocrEl = document.getElementById("dbgOcr");
    // 校准悬浮窗开关与后端真实状态同步（Esc 关掉悬浮窗时开关自动回弹）
    const calibSw = document.getElementById("ocrCalibSwitch");
    if (calibSw) calibSw.checked = !!data.ocr_calibration_open;
    // 对话悬浮层按钮状态同步（Esc 关掉时按钮自动回弹）
    const ovBtn = document.getElementById("chatOverlayBtn");
    if (ovBtn) ovBtn.classList.toggle("btn-active", !!data.chat_overlay_open);
    if (ocrEl && ocr) {
        const scanCls = (ocr.foreground_hint === "game") ? "ok" : "warn";
        const hits = (ocr.recent || []).map(h =>
            `<div class="tl-item"><span class="tl-time">${fmtClock(h.ts)}</span><div class="tl-main">
               <div class="tl-summary">${h.summary || ""}</div>
               <div class="tl-meta"><span class="tl-tag">${h.moment || "?"}</span>${h.source === "ocr+vision" ? '<span class="tl-tag">多模态</span>' : ""}${fmtAgo(h.ts, now)}</div></div></div>`).join("");
        const scans = (ocr.recent_scans || []).slice().reverse().map(s =>
            `<div class="tl-item"><span class="tl-time">${fmtClock(s.ts)}</span><div class="tl-main">
               <div class="tl-summary" style="color:var(--text-muted);font-size:12px">${(s.text || "").slice(0, 60)}</div></div></div>`).join("");
        const nReg = Object.keys(ocr.regions || {}).length;
        // 同步"我的游戏名字"输入框（用户正在输入时不覆盖）
        const pnInput = document.getElementById("ocrPlayerName");
        if (pnInput && document.activeElement !== pnInput) {
            pnInput.value = ocr.player_name || "";
        }
        ocrEl.innerHTML =
            kvRow("我的名字", (ocr.player_name
                ? `<span class="ok">${ocrEsc(ocr.player_name)}</span>（击杀播报左侧=你击杀，右侧=你阵亡）`
                : '<span class="warn">未设置 — AI 会在击杀播报出现时主动问你，或在上方输入保存</span>')) +
            kvRow("状态", ocr.enabled ? '<span class="ok">启用</span>' : '<span class="err">禁用</span>') +
            (ocr.occluded
                ? kvRow("置顶窗口遮挡", `<span class="warn">腾讯会议置顶窗口盖住了游戏画面，OCR 已暂停（跳过 ${ocr.occlusion_skips || 0} 帧）。把会议/共享窗口移到副屏、最小化或取消置顶后自动恢复</span>`)
                : "") +
            kvRow("前台类型", ocr.foreground_hint ? `<span class="${scanCls}">${ocr.foreground_hint}（决定是否扫描）</span>` : '<span class="warn">非游戏，暂停扫描</span>') +
            kvRow("扫描/命中", `${ocr.scan_count} 次扫描 · ${ocr.hit_count} 次命中`) +
            kvRow("输入证据门", (() => {
                const ev = ocr.evidence || {};
                const ok = ev.ok ? '<span class="ok">正常</span>' : '<span class="err">不可用</span>';
                const left = ev.left_clicks_recent ?? 0;
                const any = ev.any_input_recent ?? 0;
                const err = ev.err ? `<br><span style="color:var(--danger)">${ocrEsc(ev.err)}</span>` : "";
                return `${ok} · 最近${left}次左键 · ${any}次任意输入 · 拦截 ${ocr.evidence_blocked || 0} 次${err}`;
            })()) +
            kvRow("节流", `时刻冷却 ${ocr.cooldown_s}s · 扫描间隔 ${ocr.interval_s}s`) +
            kvRow("识别区域", `${nReg} 个自定义区域（下方逐个显示实时扫描文字）`) +
            `<div style="margin-top:8px;font-size:12px;color:var(--text-muted)">最近命中（${(ocr.recent || []).length}）</div>` + (hits || '<div style="color:var(--text-muted);font-size:12px">暂无命中</div>') +
            `<div style="margin-top:8px;font-size:12px;color:var(--text-muted)">最近扫描文字（诊断用，新→旧）</div>` + (scans || '<div style="color:var(--text-muted);font-size:12px">暂无扫描</div>');
        renderOcrRegions(ocr);
        renderOcrAnchors(ocr);
    } else if (ocrEl && data.ocr_error) {
        ocrEl.innerHTML = `<div class="err">${data.ocr_error}</div>`;
        const regEl = document.getElementById("dbgOcrRegions");
        if (regEl) regEl.innerHTML = "";
    }

    // ---- 游戏音效 ----
    const aud = data.audio;
    const audEl = document.getElementById("dbgAudio");
    if (audEl && aud) {
        const refs = aud.references || {};
        const refRows = Object.entries(refs).map(([ev, r]) =>
            kvRow(ev, r && r.exists
                ? `<span class="ok">已录制</span> · ${r.file} · ${r.duration_s}s`
                : '<span class="warn">未录制</span>')).join("");
        const rec = (aud.recent || []).slice().reverse().map(h =>
            `<div class="tl-item"><span class="tl-time">${fmtClock(h.ts)}</span><div class="tl-main">
               <div class="tl-summary">${h.summary || ""}</div>
               <div class="tl-meta"><span class="tl-tag">${h.moment || "?"}</span>${h.similarity != null ? `<span class="tl-tag">${Math.round(h.similarity * 100)}%</span>` : ""}${fmtAgo(h.ts, now)}</div></div></div>`).join("");
        audEl.innerHTML =
            kvRow("状态", aud.enabled ? '<span class="ok">启用</span>' : '<span class="err">禁用</span>' + (aud.running ? "" : " · 未运行")) +
            kvRow("环路设备", aud.device || '<span class="warn">未找到（需 WASAPI 环路输出）</span>') +
            kvRow("匹配", `命中 ${aud.match_count} · 起点检测 ${aud.onset_count} · 背景音量 ${aud.background_rms}`) +
            kvRow("节流", `阈值 ${aud.threshold} · 冷却 ${aud.cooldown_s}s`) +
            kvRow("参考音", (aud.loaded ? JSON.stringify(aud.loaded) : "无模板"), (aud.loaded && Object.values(aud.loaded).some(n => n > 0)) ? "" : "warn") +
            `<div style="margin-top:8px;font-size:12px;color:var(--text-muted)">参考音状态</div>` + refRows +
            `<div style="margin-top:8px;font-size:12px;color:var(--text-muted)">最近命中（${(aud.recent || []).length}）</div>` + (rec || '<div style="color:var(--text-muted);font-size:12px">暂无命中</div>');
    } else if (audEl && data.audio_error) {
        audEl.innerHTML = `<div class="err">${data.audio_error}</div>`;
    }

    // ---- 时间线（视觉识别 + OCR/多模态命中 合并，按时间倒序） ----
    const tl = document.getElementById("dbgTimeline");
    if (tl) {
        const items = [];
        (ana && ana.recent || []).forEach(it =>
            items.push({ ts: it.ts, summary: it.summary, tag: it.app_hint || "视觉", meta: fmtAgo(it.ts, now) }));
        (ocr && ocr.recent || []).forEach(it => {
            const src = it.source === "ocr+vision" ? "多模态" : "OCR";
            items.push({ ts: it.ts, summary: it.summary, tag: src, meta: fmtAgo(it.ts, now) });
        });
        (aud && aud.recent || []).forEach(it => {
            items.push({ ts: it.ts, summary: it.summary, tag: "音效", meta: fmtAgo(it.ts, now) });
        });
        items.sort((a, b) => b.ts - a.ts);
        if (!items.length) {
            tl.innerHTML = `<div style="color:var(--text-muted);font-size:12px">暂无感知活动（画面无变化或视觉未触发）</div>`;
            return;
        }
        tl.innerHTML = items.slice(0, 20).map(it =>
            `<div class="tl-item">
                <span class="tl-time">${fmtClock(it.ts)}</span>
                <div class="tl-main">
                    <div class="tl-summary">${it.summary || ""}</div>
                    <div class="tl-meta"><span class="tl-tag">${it.tag}</span>${it.meta}</div>
                </div>
            </div>`).join("");
    }
}

// ================================
// 模型设置 Tab（LLM Provider 自定义模型）
// ================================
let llmSettings = null;

async function loadLlmSettings() {
    try {
        const res = await pywebview.api.get_llm_settings();
        if (!res || !res.success) { setStatus("模型设置加载失败: " + (res.error || "")); return; }
        llmSettings = res;
        // provider 单选
        const provider = res.provider || "ollama";
        document.querySelectorAll('input[name="llmProvider"]').forEach(r => {
            r.checked = r.value === provider;
        });
        // OpenAI 兼容
        document.getElementById("llmOpenaiBaseUrl").value = res.openai.base_url || "";
        document.getElementById("llmOpenaiApiKey").value = res.openai.api_key || "";
        document.getElementById("llmOpenaiModel").value = res.openai.model || "";
        document.getElementById("llmOpenaiTimeout").value = res.openai.timeout_s || 60;
        document.getElementById("llmOpenaiVision").checked = !!res.openai.vision;
        // MiniCPM
        document.getElementById("llmMinicpmEndpoint").value = res.minicpm.endpoint || "";
        document.getElementById("llmMinicpmMaxTokens").value = res.minicpm.max_new_tokens || 512;
        document.getElementById("llmMinicpmTimeout").value = res.minicpm.timeout_s || 60;
        onProviderChange();
        // 运行时状态徽章
        const rt = res.runtime || {};
        const badge = document.getElementById("llmRuntimeState");
        if (badge) {
            badge.classList.toggle("online", !!rt.llm_ok);
            badge.textContent = (rt.llm_ok ? "● 运行中" : "● 不可用") + " · " + (rt.provider || "ollama");
        }
        const saveEl = document.getElementById("llmSaveResult");
        if (saveEl) {
            const deg = (rt.degradations || []).join("；");
            saveEl.textContent = deg ? " " + deg : "";
        }
        await loadVisionModels();
        await loadGamePlans();
        await loadDuplexSettings();
    } catch (e) {
        console.error("加载模型设置失败:", e);
    }
}

// ================================
// 双工引擎（云端 / 本地 / 关闭）：用户自选 + 显卡门槛检测 + 性能提醒
// ================================
async function loadDuplexSettings(force) {
    try {
        const res = await pywebview.api.get_duplex_settings();
        if (!res || !res.success) {
            const el = document.getElementById("duplexGpuStatus");
            if (el) el.textContent = "加载失败: " + ((res && res.error) || "");
            return;
        }
        const engine = res.engine || "cloud";
        document.querySelectorAll('input[name="duplexEngine"]').forEach(r => {
            r.checked = r.value === engine;
        });
        const epEl = document.getElementById("duplexEndpoint");
        if (epEl) epEl.value = res.endpoint || "";
        // 【2026-09-24】云端双工用户自有端点：渲染到设置页
        const cd = res.cloud_duplex || {};
        const cdv = document.getElementById("cdnVoiceEndpoint"); if (cdv) cdv.value = cd.voice_endpoint || "";
        const cdvp = document.getElementById("cdnVideoEndpoint"); if (cdvp) cdvp.value = cd.video_endpoint || "";
        const cve = document.getElementById("cdnVideoEnabled"); if (cve) cve.checked = cd.video_enabled !== false;
        const clf = document.getElementById("cdnLocalFallback"); if (clf) clf.checked = cd.local_fallback !== false;
        const cpc = document.getElementById("cdnPreferCloud"); if (cpc) cpc.checked = cd.prefer_cloud !== false;
        onDuplexEngineChange();
        // 显卡门槛检测结果
        const gpu = res.gpu || {};
        const gpuEl = document.getElementById("duplexGpuStatus");
        if (gpuEl) {
            gpuEl.textContent = " " + (gpu.reason || "显存检测失败");
            gpuEl.style.color = gpu.local_ok ? "#2ecc71" : "#f5a623";
        }
    } catch (e) {
        console.error("加载双工引擎设置失败:", e);
    }
}

function onDuplexEngineChange() {
    const sel = document.querySelector('input[name="duplexEngine"]:checked');
    const mode = sel ? sel.value : "cloud";
    document.querySelectorAll(".provider-card[data-engine]").forEach(c => {
        c.classList.toggle("selected", c.dataset.engine === mode);
    });
    const epRow = document.getElementById("duplexEndpointRow");
    if (epRow) epRow.style.display = (mode === "off") ? "none" : "";
    const warn = document.querySelector(".duplex-warn");
    if (warn) warn.style.display = (mode === "local") ? "" : "none";
    const mp = document.getElementById("modelQuantPanel");
    if (mp) {
        mp.style.display = (mode === "local") ? "" : "none";
        if (mode === "local") loadModelCatalog();
    }
}

async function saveDuplexSettings() {
    const btn = document.querySelector('button[onclick="saveDuplexSettings()"]');
    const resEl = document.getElementById("duplexSaveResult");
    if (btn) { btn.disabled = true; btn.textContent = "保存中..."; }
    if (resEl) resEl.textContent = "";
    try {
        const sel = document.querySelector('input[name="duplexEngine"]:checked');
        const mode = sel ? sel.value : "cloud";
        const endpoint = ((document.getElementById("duplexEndpoint") || {}).value || "").trim();
        // 【2026-09-24】云端双工用户自有端点
        const vEp = ((document.getElementById("cdnVoiceEndpoint") || {}).value || "").trim();
        const vVp = ((document.getElementById("cdnVideoEndpoint") || {}).value || "").trim();
        const vEn = document.getElementById("cdnVideoEnabled"); const vVe = vEn ? vEn.checked : true;
        const vLf = document.getElementById("cdnLocalFallback"); const vFl = vLf ? vLf.checked : true;
        const vPc = document.getElementById("cdnPreferCloud"); const vPcf = vPc ? vPc.checked : true;
        const res = await pywebview.api.save_duplex_settings({
            engine: mode, endpoint,
            voice_endpoint: vEp, video_endpoint: vVp,
            video_enabled: vVe, local_fallback: vFl, prefer_cloud: vPcf,
        });
        if (resEl && res && res.success) {
            resEl.textContent = mode === "cloud" ? " 已切换云端全双工（立即生效）"
                : mode === "off" ? " 已关闭双工（立即生效）"
                : " 已选择本地全双工（引擎尚未内置，暂用云端）";
            resEl.style.color = "#2ecc71";
            await loadDuplexSettings();
        } else if (resEl) {
            resEl.textContent = " 保存失败: " + ((res && res.error) || "");
            resEl.style.color = "#e74c3c";
        }
    } catch (e) {
        if (resEl) { resEl.textContent = " 保存异常: " + e.message; resEl.style.color = "#e74c3c"; }
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = "保存并立即生效"; }
    }
}

// ================================
// 本地模型量化包（设备感知 + 魔塔下载）
// ================================
async function loadModelCatalog() {
    const panel = document.getElementById("modelQuantPanel");
    const list = document.getElementById("modelQuantList");
    const warn = document.getElementById("modelQuantWarn");
    if (!panel || panel.style.display === "none") return;
    try {
        const res = await pywebview.api.get_model_catalog();
        if (!res || !res.success) { if (warn) warn.textContent = "加载模型目录失败: " + ((res && res.error) || ""); return; }
        if (warn) warn.textContent = res.warning || "";
        if (res.cloud_only) {
            if (list) list.innerHTML = '<div style="color:#f5a623">显存过低，本地双工跑不动，建议选「云端全双工」。</div>';
            return;
        }
        renderQuantCards(res);
    } catch (e) { if (warn) warn.textContent = "加载模型目录异常: " + e.message; }
}

function renderQuantCards(cat) {
    const list = document.getElementById("modelQuantList");
    if (!list) return;
    list.innerHTML = "";
    (cat.quants || []).forEach(q => {
        if (!q.available) return;
        const div = document.createElement("div");
        let cls = "quant-card", badge = "";
        if (q.recommended) { cls += " recommended"; badge = '<span class="quant-badge">★ 推荐</span>'; }
        else if (q.tight) { cls += " tight"; badge = '<span class="quant-badge tight">偏紧</span>'; }
        else if (q.manual_only) { cls += " manual"; badge = '<span class="quant-badge manual">手动</span>'; }
        const cmd = (q.download_cmd || "").replace(/"/g, "&quot;");
        div.className = cls;
        div.innerHTML =
            '<div class="quant-head"><span class="quant-id">' + q.id + '</span>' + badge + '</div>' +
            '<div class="quant-meta">文件~' + q.size_gb + 'GB · 运行~' + q.vram_gb + 'GB · ' + q.quality + '</div>' +
            '<div class="quant-note">' + q.note + '</div>' +
            '<div class="quant-actions">' +
                '<a class="btn-mini" href="' + q.modelscope_url + '" target="_blank" rel="noopener">魔塔链接</a>' +
                '<button class="btn-mini" onclick="copyQuantCmd(this, \'' + cmd + '\')">复制命令</button>' +
                '<button class="btn-mini primary" onclick="downloadQuant(\'' + q.id + '\', this)">下载</button>' +
            '</div>' +
            '<div class="quant-status" id="qs_' + q.id + '"></div>';
        list.appendChild(div);
    });
}

function copyQuantCmd(btn, cmd) {
    navigator.clipboard.writeText(cmd).then(() => {
        const old = btn.textContent; btn.textContent = "已复制"; setTimeout(() => btn.textContent = old, 1500);
    }).catch(() => { prompt("复制以下命令：", cmd); });
}

async function downloadQuant(quantId, btn) {
    const status = document.getElementById("qs_" + quantId);
    if (status) status.textContent = "请求下载中…";
    try {
        const res = await pywebview.api.download_model_quant({ quant_id: quantId });
        if (!res || !res.success) { if (status) status.textContent = " " + ((res && res.error) || "失败"); return; }
        if (status) status.textContent = "已在后台下载。可手动执行的命令：\n" + res.download_cmd;
        showToast("已开始下载 " + quantId + "，完成后重启引擎生效", "info");
        pollQuantDownload(res.task_id, quantId);
    } catch (e) { if (status) status.textContent = " 异常: " + e.message; }
}

function pollQuantDownload(taskId, quantId) {
    const status = document.getElementById("qs_" + quantId);
    const timer = setInterval(async () => {
        try {
            const r = await pywebview.api.get_model_download_status(taskId);
            if (!r) return;
            if (status) status.textContent = r.log || r.status;
            if (r.status === "done" || r.status === "manual" || r.status === "error") {
                clearInterval(timer);
                if (r.status === "done") showToast(quantId + " 下载完成，重启引擎生效", "success");
            }
        } catch (e) { clearInterval(timer); }
    }, 3000);
}

// ================================
// 视觉模型（屏幕识别）切换：云端优先、本地兜底
// ================================
let visionModels = null;

function setVisionStatus(text, color) {
    const el = document.getElementById("visionModelStatus");
    if (el) { el.textContent = text || ""; el.style.color = color || ""; }
}

async function loadVisionModels() {
    try {
        const res = await pywebview.api.get_vision_models();
        if (!res || !res.success) {
            setVisionStatus(res && res.error || "视觉模型路由未注入（重启后生效）");
            return;
        }
        visionModels = res;
        const sel = document.getElementById("visionModelSelect");
        if (!sel) return;
        sel.innerHTML = "";
        for (const m of (res.models || [])) {
            const opt = document.createElement("option");
            opt.value = m.id;
            const flag = m.vision_available ? "可用" : "不可用";
            const cd = m.cooldown_left > 0 ? "·冷却中" : "";
            opt.textContent = `${m.name}（${flag}${cd}）`;
            if (m.id === res.active) opt.selected = true;
            sel.appendChild(opt);
        }
        const active = (res.models || []).find(m => m.id === res.active);
        if (!active) {
            setVisionStatus("未配置视觉模型（config.yaml vision_models）");
            return;
        }
        // 仅 OpenAI 兼容云端条目（智谱等）显示 API Key 输入行
        const keyRow = document.getElementById("visionKeyRow");
        const keyInput = document.getElementById("visionModelApiKey");
        if (active.provider === "openai") {
            if (keyRow) keyRow.style.display = "";
            if (keyInput) keyInput.value = active.api_key || "";
        } else {
            if (keyRow) keyRow.style.display = "none";
            if (keyInput) keyInput.value = "";
        }
        if (active.id === res.last_used) {
            setVisionStatus(`当前: ${active.name} · 正在使用中`);
        } else if (active.vision_available) {
            setVisionStatus(`当前: ${active.name} · 就绪（失败自动回退本地）`);
        } else {
            setVisionStatus(`当前: ${active.name} · 不可用，将自动使用本地模型`);
        }
    } catch (e) {
        console.error("加载视觉模型失败:", e);
    }
}

async function saveVisionModelKey() {
    const sel = document.getElementById("visionModelSelect");
    const keyInput = document.getElementById("visionModelApiKey");
    if (!sel || !keyInput) return;
    const res = await pywebview.api.set_vision_model_key(sel.value, keyInput.value.trim());
    if (res && res.success) {
        showToast("视觉模型 API Key 已保存并生效", "success");
    } else {
        showToast("保存失败: " + (res && (res.error || res.message) || "未知错误"), "error");
    }
    await loadVisionModels();
}

async function switchVisionModel() {
    const sel = document.getElementById("visionModelSelect");
    if (!sel || !sel.value) return;
    const res = await pywebview.api.switch_vision_model(sel.value);
    if (res && res.success) {
        showToast("视觉模型已切换: " + sel.value, "success");
    } else {
        showToast("切换失败: " + (res && (res.error || res.message) || "未知错误"), "error");
    }
    await loadVisionModels();
}

async function testVisionModel() {
    const btn = document.querySelector('.section .btn-test[onclick*="testVisionModel"]');
    const resultEl = document.getElementById("visionModelTestResult");
    if (btn) { btn.disabled = true; btn.textContent = "测试中..."; }
    if (resultEl) { resultEl.textContent = "正在测试（约需几秒）..."; resultEl.style.color = ""; }
    try {
        const res = await pywebview.api.test_vision_model();
        if (resultEl) {
            resultEl.textContent = (res.success ? " " : " ") + (res.message || res.error || "");
            resultEl.style.color = res.success ? "#2ecc71" : "#e74c3c";
        }
    } catch (e) {
        if (resultEl) { resultEl.textContent = " 调用失败: " + e.message; resultEl.style.color = "#e74c3c"; }
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = "测试当前"; }
    }
}

// ================================
// 游戏陪伴方案（config proactive.game_plans）：按游戏配双工陪伴提示词
// ================================
let gamePlansCache = [];

async function loadGamePlans() {
    try {
        const res = await pywebview.api.get_game_plans();
        if (!res || !res.success) { return; }
        gamePlansCache = (res.plans || []).map(p => ({
            name: p.name || "",
            keywords: Array.isArray(p.keywords) ? p.keywords.join(",") : (p.keywords || ""),
            prompt: p.prompt || "",
        }));
        renderGamePlans();
    } catch (e) {
        console.error("加载游戏陪伴方案失败:", e);
    }
}

function renderGamePlans() {
    const wrap = document.getElementById("gamePlansWrap");
    if (!wrap) return;
    if (!gamePlansCache.length) {
        wrap.innerHTML = '<div class="form-group"><span class="field-hint">还没配过。点「＋ 新增方案」，告诉达妮娅你玩某款游戏时她该怎么陪——下次她认出这款游戏，就会用你写的方式陪你（不填就按平时的风格来）。</span></div>';
        return;
    }
    wrap.innerHTML = gamePlansCache.map((p, i) => `
        <div class="game-plan-item">
            <div class="game-plan-head">
                <input type="text" class="game-plan-name" value="${escHtml(p.name)}"
                       placeholder="游戏名，如 三角洲行动" title="填游戏的名字。当你打开这个游戏、达妮娅认出它时，就会用下面写的陪法。">
                <input type="text" class="game-plan-keywords" value="${escHtml(p.keywords)}"
                       placeholder="额外识别词，逗号分隔（可空）" title="如果游戏窗口上显示的名字跟这里不一样，可以补几个关键词帮达妮娅认出来（不区分大小写）。">
            </div>
            <textarea class="game-plan-prompt" rows="3" placeholder="达妮娅怎么陪你：写写她该留意画面里的什么、用什么语气跟你聊、什么情况别打扰（比如打团时保持安静）。">${escHtml(p.prompt)}</textarea>
            <div class="game-plan-actions">
                <button type="button" class="btn-test" onclick="saveGamePlan(${i})">${DIcon.svg('save')} 保存</button>
                <button type="button" class="btn-test btn-del" onclick="deleteGamePlan(${i})">${DIcon.svg('trash-2')} 删除</button>
            </div>
        </div>`).join("");
}

async function saveGamePlan(idx) {
    const items = document.querySelectorAll(".game-plan-item");
    if (idx < 0 || idx >= items.length) return;
    const item = items[idx];
    const name = item.querySelector(".game-plan-name").value.trim();
    const keywords = item.querySelector(".game-plan-keywords").value;
    const prompt = item.querySelector(".game-plan-prompt").value.trim();
    if (!name) { showToast("请填游戏名", "error"); return; }
    if (!prompt) { showToast("先写写达妮娅该怎么陪你吧", "error"); return; }
    gamePlansCache[idx] = {
        name,
        keywords: keywords.split(/[,，\s]+/).filter(Boolean),
        prompt,
    };
    const res = await pywebview.api.save_game_plans(gamePlansCache.map(p => ({
        name: p.name,
        keywords: Array.isArray(p.keywords) ? p.keywords : String(p.keywords).split(/[,，\s]+/).filter(Boolean),
        prompt: p.prompt,
    })));
    if (res && res.success) {
        showToast("方案已保存，达妮娅下次陪你玩这个游戏就用新方式啦", "success");
    } else {
        showToast("保存失败: " + (res && res.error || "未知错误"), "error");
    }
    await loadGamePlans();
}

function addGamePlan() {
    gamePlansCache.push({ name: "", keywords: "", prompt: "" });
    renderGamePlans();
    const items = document.querySelectorAll(".game-plan-item");
    const last = items[items.length - 1];
    if (last) { last.scrollIntoView({ behavior: "smooth", block: "center" }); last.querySelector(".game-plan-name").focus(); }
}

async function deleteGamePlan(idx) {
    if (idx < 0 || idx >= gamePlansCache.length) return;
    gamePlansCache.splice(idx, 1);
    const res = await pywebview.api.save_game_plans(gamePlansCache.map(p => ({
        name: p.name,
        keywords: Array.isArray(p.keywords) ? p.keywords : String(p.keywords).split(/[,，\s]+/).filter(Boolean),
        prompt: p.prompt,
    })));
    if (res && res.success) {
        showToast("方案已删除", "success");
    } else {
        showToast("删除失败: " + (res && res.error || "未知错误"), "error");
    }
    await loadGamePlans();
}

function onProviderChange() {
    const provider = document.querySelector('input[name="llmProvider"]:checked')?.value || "ollama";
    document.getElementById("llmOllamaSection").classList.toggle("hidden", provider !== "ollama");
    document.getElementById("llmOpenaiSection").classList.toggle("hidden", provider !== "openai");
    document.getElementById("llmMinicpmSection").classList.toggle("hidden", provider !== "minicpm");
    document.querySelectorAll(".provider-card").forEach(c => {
        c.classList.toggle("selected", c.dataset.provider === provider);
    });
}

function gatherLlmForm() {
    return {
        provider: document.querySelector('input[name="llmProvider"]:checked')?.value || "ollama",
        openai: {
            base_url: document.getElementById("llmOpenaiBaseUrl").value.trim(),
            api_key: document.getElementById("llmOpenaiApiKey").value.trim(),
            model: document.getElementById("llmOpenaiModel").value.trim(),
            vision: document.getElementById("llmOpenaiVision").checked,
            timeout_s: parseInt(document.getElementById("llmOpenaiTimeout").value) || 60,
        },
        minicpm: {
            endpoint: document.getElementById("llmMinicpmEndpoint").value.trim(),
            max_new_tokens: parseInt(document.getElementById("llmMinicpmMaxTokens").value) || 512,
            timeout_s: parseInt(document.getElementById("llmMinicpmTimeout").value) || 60,
        },
    };
}

async function testLlm(provider) {
    const btn = document.querySelector(`.section .btn-test[onclick*="${provider}"]`);
    const resultEl = document.getElementById(provider === "openai" ? "llmOpenaiTestResult" : "llmMinicpmTestResult");
    if (btn) { btn.disabled = true; btn.textContent = "测试中..."; }
    if (resultEl) { resultEl.textContent = "正在测试..."; resultEl.style.color = ""; }
    try {
        const data = gatherLlmForm();
        const payload = provider === "openai" ? data.openai : data.minicpm;
        const res = await pywebview.api.test_llm_connection(provider, payload);
        if (resultEl) {
            resultEl.textContent = (res.success ? " " : " ") + (res.message || "");
            resultEl.style.color = res.success ? "#2ecc71" : "#e74c3c";
        }
    } catch (e) {
        if (resultEl) { resultEl.textContent = " 调用失败: " + e.message; resultEl.style.color = "#e74c3c"; }
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = "测试连接"; }
    }
}

async function saveLlmSettings() {
    const btn = document.querySelector(".llm-actions .btn-save");
    const saveEl = document.getElementById("llmSaveResult");
    if (btn) { btn.disabled = true; btn.textContent = "保存中..."; }
    if (saveEl) saveEl.textContent = "";
    try {
        const data = gatherLlmForm();
        const res = await pywebview.api.save_llm_settings(data);
        if (res.success) {
            const live = res.applied_live ? "已立即生效" : "已保存（重启后生效）";
            if (saveEl) { saveEl.textContent = " " + live; saveEl.style.color = "#2ecc71"; }
            showToast("模型设置已保存 · " + live, "success");
            await loadLlmSettings();
        } else {
            if (saveEl) { saveEl.textContent = " " + (res.error || "保存失败"); saveEl.style.color = "#e74c3c"; }
        }
    } catch (e) {
        if (saveEl) { saveEl.textContent = " 保存失败: " + e.message; saveEl.style.color = "#e74c3c"; }
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = "保存并立即生效"; }
    }
}

// ================================
// 记忆设置 Tab（jarvis / legacy 方案对比开关）
// ================================
const MEM_TYPE_LABEL = { directive: " 指令", user: " 偏好", world: " 事实" };
const MEM_TYPE_TAG = { directive: "mem-tag-directive", user: "mem-tag-user", world: "mem-tag-world" };

function onMemModeChange() {
    const mode = document.querySelector('input[name="memMode"]:checked')?.value || "jarvis";
    document.querySelectorAll('.provider-card[data-provider]').forEach(c => {
        c.classList.toggle("selected", c.dataset.provider === mode);
    });
}

function fmtMemTime(ts) {
    if (!ts) return "—";
    const d = new Date(ts * 1000);
    const p = n => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function renderMemList(items) {
    const el = document.getElementById("memList");
    if (!el) return;
    if (!items || !items.length) {
        el.innerHTML = `<div class="mem-empty">暂无记忆条目。AI 记住用户纠正/偏好后，会在这里展示。</div>`;
        return;
    }
    el.innerHTML = items.map(m => {
        const typ = m.type || "user";
        return `<div class="mem-item">
            <span class="mem-item-tag ${MEM_TYPE_TAG[typ] || "mem-tag-user"}">${MEM_TYPE_LABEL[typ] || " 偏好"}</span>
            <span class="mem-item-text">${escHtml(m.text)}</span>
            <span class="mem-item-meta">提及 ×${m.hits || 1} · ${fmtMemTime(m.updated_at)}</span>
        </div>`;
    }).join("");
}

async function loadMemorySettings() {
    try {
        const res = await pywebview.api.get_memory_settings();
        if (!res || !res.success) { setStatus("记忆设置加载失败: " + (res.error || "")); return; }
        // 方案单选
        document.querySelectorAll('input[name="memMode"]').forEach(r => {
            r.checked = r.value === (res.mode || "jarvis");
        });
        onMemModeChange();
        // 开关
        document.getElementById("memSemantic").checked = !!res.semantic_search;
        document.getElementById("memIntent").checked = !!res.intent_judge;
        document.getElementById("memEnabled").checked = !!res.enabled;
        document.getElementById("memInject").checked = !!res.inject_context;
        document.getElementById("memMaxItems").value = res.max_items || 100;
        // 统计
        const st = res.stats || {};
        document.getElementById("memCountTotal").textContent = st.count ?? 0;
        const cbt = st.count_by_type || {};
        document.getElementById("memCountDirective").textContent = cbt.directive ?? 0;
        document.getElementById("memCountUser").textContent = cbt.user ?? 0;
        document.getElementById("memCountWorld").textContent = cbt.world ?? 0;
        renderMemList(st.items);
        // 语义检索状态 + 运行时徽章
        const embedEl = document.getElementById("memEmbedState");
        if (embedEl) {
            if (st.disabled) {
                embedEl.textContent = " 长期记忆当前为关闭状态（config.yaml memory.enabled=false），保存开启后需重启程序。";
            } else if (res.semantic_search) {
                embedEl.textContent = st.has_embed ? " 语义检索就绪（nomic-embed-text 可用）" : " 语义检索未就绪：需 ollama pull nomic-embed-text，当前已降级为关键词召回";
            } else {
                embedEl.textContent = "语义检索已关闭，使用关键词/重要度召回";
            }
        }
        const badge = document.getElementById("memRuntimeState");
        if (badge) {
            badge.classList.toggle("online", !st.disabled);
            badge.textContent = (st.disabled ? "● 未启用" : "● 运行中") + " · " + (res.mode || "jarvis");
        }
    } catch (e) {
        console.error("加载记忆设置失败:", e);
    }
}

function gatherMemoryForm() {
    return {
        mode: document.querySelector('input[name="memMode"]:checked')?.value || "jarvis",
        semantic_search: document.getElementById("memSemantic").checked,
        intent_judge: document.getElementById("memIntent").checked,
        enabled: document.getElementById("memEnabled").checked,
        inject_context: document.getElementById("memInject").checked,
        max_items: parseInt(document.getElementById("memMaxItems").value) || 100,
    };
}

async function saveMemorySettings() {
    const btn = document.querySelector(".memory-panel .btn-save");
    const saveEl = document.getElementById("memSaveResult");
    if (btn) { btn.disabled = true; btn.textContent = "保存中..."; }
    if (saveEl) saveEl.textContent = "";
    try {
        const data = gatherMemoryForm();
        const res = await pywebview.api.save_memory_settings(data);
        if (res.success) {
            const live = res.applied_live ? "已立即生效" : "已保存（重启后生效）";
            if (saveEl) { saveEl.textContent = " " + live; saveEl.style.color = "#2ecc71"; }
            showToast("记忆设置已保存 · " + live, "success");
            await loadMemorySettings();
        } else {
            if (saveEl) { saveEl.textContent = " " + (res.error || "保存失败"); saveEl.style.color = "#e74c3c"; }
        }
    } catch (e) {
        if (saveEl) { saveEl.textContent = " 保存失败: " + e.message; saveEl.style.color = "#e74c3c"; }
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = "保存并立即生效"; }
    }
}

async function clearMemory() {
    if (!confirm("确定清空所有长期记忆吗？此操作不可恢复。")) return;
    try {
        const res = await pywebview.api.clear_memory();
        if (res.success) {
            showToast("记忆已清空", "success");
            await loadMemorySettings();
        } else {
            showToast("清空失败: " + (res.error || ""), "error");
        }
    } catch (e) {
        showToast("清空失败: " + e.message, "error");
    }
}

// ================================
// Unity 2K 流 (MJPEG HTTP:8767)
// ================================
const UNITY_BASE = "http://localhost:8767";
const UNITY_STREAM = UNITY_BASE + "/stream.mjpg";
const UNITY_SNAPSHOT = UNITY_BASE + "/snapshot.jpg";

function setUnityHttpBadge(online, text) {
    const el = document.getElementById("unityHttpStatus");
    const tabBadge = document.getElementById("unityBadge");
    if (el) el.classList.toggle("online", !!online);
    if (tabBadge) {
        if (online === true) { tabBadge.classList.remove("err"); tabBadge.classList.add("ok"); tabBadge.textContent = "在线"; }
        else if (online === false) { tabBadge.classList.remove("ok"); tabBadge.classList.add("err"); tabBadge.textContent = "离线"; }
        else { tabBadge.classList.remove("ok","err"); tabBadge.textContent = text || "检查中"; }
    }
}

function setUnityWsBadge(online) {
    const el = document.getElementById("unityWsStatus");
    if (el) el.classList.toggle("online", !!online);
}

function showOffline(show) {
    const off = document.getElementById("unityStreamOffline");
    if (off) off.classList.toggle("hidden", !show);
}

function probeUnityOnline(cb) {
    // 用 <img> 探测，绕过 CORS（即便 Unity 不加 CORS 头也能测通）
    const probe = new Image();
    let done = false;
    const finish = (ok) => { if (done) return; done = true; cb(ok); probe.src = ""; };
    probe.onload = () => finish(true);
    probe.onerror = () => finish(false);
    probe.onabort = () => finish(false);
    // 3s 超时判失败
    setTimeout(() => finish(false), 3000);
    probe.src = UNITY_SNAPSHOT + "?t=" + Date.now();
}

function startUnityStream() {
    const img = document.getElementById("unityStreamImg");
    if (!img) return;
    restartUnityStream();
    initCamState();
    // MJPEG multipart 流每收到一帧会触发一次 load 事件，可据此做帧心跳：
    // 若长时间没有新帧，说明 MJPEG 连接已断开、画面冻结在旧帧，需强制重连。
    let loadFired = false;
    let lastFrameAt = Date.now();
    img.addEventListener("load", () => {
        loadFired = true;
        lastFrameAt = Date.now();
        showOffline(false);
        setUnityHttpBadge(true);
    });
    img.addEventListener("error", () => {
        showOffline(true);
        setUnityHttpBadge(false);
    });
    // 兜底：若 3 秒后 img src 已填且未触发 error，则认为流已建立，隐藏 offline 盖层
    setTimeout(() => {
        if (!loadFired && img.src && img.src.indexOf(UNITY_BASE) === 0) {
            probeUnityOnline((ok) => {
                if (ok) showOffline(false);
            });
        }
    }, 3000);

    // 定期健康检测（每 4s）：<img> 探测 snapshot.jpg 避免 CORS 问题
    setInterval(() => {
        probeUnityOnline((ok) => {
            if (ok) {
                setUnityHttpBadge(true);
                const cur = img.src;
                if (!cur || cur.indexOf("http") !== 0) restartUnityStream();
                else showOffline(false);
            } else {
                setUnityHttpBadge(false);
                showOffline(true);
            }
        });
    }, 4000);

    // 帧心跳：若 3 秒内没收到新帧（MJPEG 连接断开/画面冻结在旧帧），强制重建流连接
    setInterval(() => {
        if (Date.now() - lastFrameAt > 3000) {
            lastFrameAt = Date.now(); // 重置防止重连风暴
            restartUnityStream();
        }
    }, 1500);

    // 鼠标交互控制相机：拖拽旋转（左右=转向 yaw，上下=高度 height），滚轮缩放（dist）
    initUnityCamMouse(img);
}

// ---- Unity 相机鼠标控制（拖拽旋转 + 滚轮缩放）----
let _camDrag = { active: false, lastX: 0, lastY: 0 };
let _camWheelTimer = null;
let _camDragTimer = null;

function initUnityCamMouse(img) {
    if (!img || img.dataset.camMouse) return; // 只绑定一次
    img.dataset.camMouse = "1";
    img.style.cursor = "grab";

    img.addEventListener("mousedown", (e) => {
        if (e.button !== 0) return; // 仅左键
        _camDrag.active = true;
        _camDrag.lastX = e.clientX;
        _camDrag.lastY = e.clientY;
        img.style.cursor = "grabbing";
        e.preventDefault();
    });

    window.addEventListener("mousemove", (e) => {
        if (!_camDrag.active) return;
        const dx = e.clientX - _camDrag.lastX;
        const dy = e.clientY - _camDrag.lastY;
        _camDrag.lastX = e.clientX;
        _camDrag.lastY = e.clientY;

        const yawEl = document.getElementById("camYaw");
        const hEl = document.getElementById("camHeight");
        const yv = document.getElementById("camYawVal");
        const hv = document.getElementById("camHeightVal");
        if (yawEl) {
            const v = Math.max(-180, Math.min(180, (parseFloat(yawEl.value) || 0) + dx * 0.5));
            yawEl.value = Math.round(v);
            if (yv) yv.textContent = Math.round(v) + "°";
        }
        if (hEl) {
            const v = Math.max(0.5, Math.min(1.8, (parseFloat(hEl.value) || 1.3) + dy * 0.005));
            hEl.value = v.toFixed(2);
            if (hv) hv.textContent = v.toFixed(2);
        }
        // 节流发送（拖动过程中每 ~60ms 推一次）
        if (_camDragTimer) clearTimeout(_camDragTimer);
        _camDragTimer = setTimeout(sendCamParams, 60);
    });

    window.addEventListener("mouseup", () => {
        if (!_camDrag.active) return;
        _camDrag.active = false;
        img.style.cursor = "grab";
        if (_camDragTimer) { clearTimeout(_camDragTimer); _camDragTimer = null; }
        sendCamParams(); // 松手时确保最后一次指令发出
    });

    img.addEventListener("wheel", (e) => {
        e.preventDefault();
        const distEl = document.getElementById("camDist");
        const dv = document.getElementById("camDistVal");
        if (!distEl) return;
        const step = e.deltaY > 0 ? 0.1 : -0.1; // 下滚拉远 / 上滚拉近
        const v = Math.max(1.4, Math.min(4.5, (parseFloat(distEl.value) || 2.2) + step));
        distEl.value = v.toFixed(2);
        if (dv) dv.textContent = v.toFixed(1);
        if (_camWheelTimer) clearTimeout(_camWheelTimer);
        _camWheelTimer = setTimeout(sendCamParams, 60);
    }, { passive: false });
}

function restartUnityStream() {
    const img = document.getElementById("unityStreamImg");
    if (!img) return;
    // 加时间戳让浏览器断开旧 MJPEG 拉取,重新建立连接
    const cachebuster = "?t=" + Date.now();
    img.src = UNITY_STREAM + cachebuster;
}

// ---- 测试全部动画（逐个播放，Unity 播完一个再切下一个）----
let _animTestRunning = false;
let _animDoneResolver = null;   // 当前等待 anim_done 的 resolver
let _animDoneTimeout = null;

function onAnimDone() {
    if (_animDoneResolver) {
        const r = _animDoneResolver;
        _animDoneResolver = null;
        if (_animDoneTimeout) { clearTimeout(_animDoneTimeout); _animDoneTimeout = null; }
        r();
    }
}

function waitAnimDone(timeoutMs) {
    return new Promise(resolve => {
        _animDoneResolver = resolve;
        _animDoneTimeout = setTimeout(() => {
            _animDoneResolver = null;
            _animDoneTimeout = null;
            resolve();
        }, timeoutMs);
    });
}

function updateMetaAction(action) {
    const el = document.getElementById("metaAction");
    if (el) el.textContent = action || "idle";
}

async function testAllAnimations() {
    if (_animTestRunning) return;
    _animTestRunning = true;
    // idle 是循环动画没有"播完"，固定 5s；
    // 单次动作等 Unity 的 anim_done（播完自动切下一个），15s 超时兜底防卡死
    const seq = [
        { name: "idle",   fixed: true,  ms: 5000,  label: "挂机" },
        { name: "greet",  fixed: false, timeout: 15000, label: "挥手你好" },
        { name: "laugh",  fixed: false, timeout: 15000, label: "大笑" },
        { name: "think",  fixed: false, timeout: 15000, label: "思考" },
        { name: "yawn",   fixed: false, timeout: 15000, label: "哈欠" },
        { name: "nod",    fixed: false, timeout: 15000, label: "点头" },
    ];
    try {
        for (const s of seq) {
            updateMetaAction(s.name);
            const res = await pywebview.api.test_animation(s.name);
            if (!res || res.success !== true) {
                showToast("发送动作失败: " + s.label, "error");
                break;
            }
            showToast("播放: " + s.label, "info");
            if (s.fixed) {
                await new Promise(r => setTimeout(r, s.ms));
            } else {
                await waitAnimDone(s.timeout);
            }
        }
    } catch (e) {
        console.error("[testAllAnimations]", e);
        showToast("测试动画出错: " + e.message, "error");
    } finally {
        if (_animDoneTimeout) { clearTimeout(_animDoneTimeout); _animDoneTimeout = null; }
        _animDoneResolver = null;
        updateMetaAction("idle");
        _animTestRunning = false;
    }
}

// VRC 衣服/配饰开关（前端按钮 → set_clothing API）
// 三套衣服互斥：开启某套内的任意物品时，其它两套按钮高亮全部取消（与 Unity 套装级互斥一致）
const CLOTH_SET_OF = {
    // 泳装
    "泳装": "泳装", "胸罩": "泳装", "内裤": "泳装", "裙子": "泳装", "帽子": "泳装",
    "鞋子": "泳装", "头饰": "泳装", "墨镜": "泳装", "头戴花朵": "泳装", "手上饰品": "泳装",
    "脚上的装饰": "泳装", "脸上贝壳": "泳装", "肩膀海豹": "泳装", "腿环": "泳装",
    // 毛绒派对
    "毛绒派对": "毛绒派对", "毛绒衣服": "毛绒派对", "毛绒帽子": "毛绒派对", "毛绒内裤": "毛绒派对",
    "毛绒鞋子": "毛绒派对", "项圈": "毛绒派对", "蝴蝶结": "毛绒派对", "丝袜": "毛绒派对",
    // 默认衣服
    "默认衣服": "默认衣服",
};
function syncClothSetState(btn) {
    const item = btn.dataset.item;
    const set = CLOTH_SET_OF[item];
    if (!set || !btn.classList.contains("on")) return;
    const wrap = document.getElementById("clothingPanel");
    if (!wrap) return;
    wrap.querySelectorAll(".cloth-btn").forEach(b => {
        if (b === btn) return;
        const bs = CLOTH_SET_OF[b.dataset.item];
        if (bs && bs !== set) b.classList.remove("on");
    });
}
function syncClothButton(item, on) {
    if (!item) return;
    const wrap = document.getElementById("clothingPanel");
    if (!wrap) return;
    wrap.querySelectorAll(".cloth-btn").forEach(b => {
        if (b.dataset.item === item) b.classList.toggle("on", !!on);
    });
}
function syncClothButtons(states) {
    const wrap = document.getElementById("clothingPanel");
    if (!wrap) return;
    wrap.querySelectorAll(".cloth-btn").forEach(b => {
        b.classList.remove("on");
    });
    (Array.isArray(states) ? states : []).forEach(s => {
        if (s && s.item) syncClothButton(s.item, s.on);
    });
}
function setupEmotionButtons() {
    const wrap = document.getElementById("clothingPanel");
    if (!wrap) return;
    wrap.addEventListener("click", async (e) => {
        const btn = e.target.closest(".emo-btn");
        if (!btn) return;
        const name = btn.dataset.emotion;
        const on = !btn.classList.contains("on");
        try {
            const res = await pywebview.api.set_emotion(name, on);
            if (res && res.success) {
                // 表情互斥：一次只显示一个
                if (on) {
                    wrap.querySelectorAll(".emo-btn").forEach(b => b.classList.remove("on"));
                    btn.classList.add("on");
                } else {
                    btn.classList.remove("on");
                }
                showToast((on ? "表情: " : "恢复: ") + name, "info");
            } else {
                showToast("表情切换失败: " + (res && res.error || ""), "error");
            }
        } catch (err) {
            console.error("[setEmotion]", err);
            showToast("表情切换出错: " + err.message, "error");
        }
    });
}
function setupClothingButtons() {
    const wrap = document.getElementById("clothingPanel");
    if (!wrap) return;
    wrap.addEventListener("click", async (e) => {
        const btn = e.target.closest(".cloth-btn");
        if (!btn) return;
        const item = btn.dataset.item;
        const on = !btn.classList.contains("on");
        try {
            const res = await pywebview.api.set_clothing(item, on);
            if (res && res.success) {
                btn.classList.toggle("on", on);
                syncClothSetState(btn);
                showToast((on ? "穿上: " : "脱下: ") + item, "info");
            } else {
                showToast("切换失败: " + (res && res.error || ""), "error");
            }
        } catch (err) {
            console.error("[setClothing]", err);
            showToast("切换出错: " + err.message, "error");
        }
    });
}

// 相机控制（Unity /api/camera，缩放/转向/高度）
let camSendTimer = null;

function onCamChange() {
    const distEl = document.getElementById("camDist");
    const yawEl = document.getElementById("camYaw");
    const hEl = document.getElementById("camHeight");
    const dv = document.getElementById("camDistVal");
    const yv = document.getElementById("camYawVal");
    const hv = document.getElementById("camHeightVal");
    if (dv) dv.textContent = parseFloat(distEl.value).toFixed(1);
    if (yv) yv.textContent = parseInt(yawEl.value) + "°";
    if (hv) hv.textContent = parseFloat(hEl.value).toFixed(2);
    if (camSendTimer) clearTimeout(camSendTimer);
    camSendTimer = setTimeout(sendCamParams, 120);
}

function sendCamParams() {
    const dist = document.getElementById("camDist")?.value ?? "2.2";
    const yaw = document.getElementById("camYaw")?.value ?? "0";
    const height = document.getElementById("camHeight")?.value ?? "1.3";
    fetch(`${UNITY_BASE}/api/camera?yaw=${yaw}&dist=${dist}&height=${height}`, { cache: "no-store" })
        .then(r => r.json())
        .then(j => {
            // 回写 Unity 实际生效值（Clamp 后）
            const d = document.getElementById("camDist");
            const y = document.getElementById("camYaw");
            const h = document.getElementById("camHeight");
            if (d && d.value !== String(j.dist)) d.value = j.dist;
            if (y && y.value !== String(j.yaw)) y.value = j.yaw;
            if (h && h.value !== String(j.height)) h.value = j.height;
        })
        .catch(() => {});
}

function resetUnityCamera() {
    const d = document.getElementById("camDist");
    const y = document.getElementById("camYaw");
    const h = document.getElementById("camHeight");
    if (d) d.value = "2.2";
    if (y) y.value = "0";
    if (h) h.value = "1.3";
    onCamChange();
    sendCamParams();
}

async function initCamState() {
    try {
        const r = await fetch(UNITY_BASE + "/api/camera", { cache: "no-store" });
        const j = await r.json();
        const d = document.getElementById("camDist");
        const y = document.getElementById("camYaw");
        const h = document.getElementById("camHeight");
        if (d) d.value = j.dist;
        if (y) y.value = j.yaw;
        if (h) h.value = j.height;
        const dv = document.getElementById("camDistVal");
        const yv = document.getElementById("camYawVal");
        const hv = document.getElementById("camHeightVal");
        if (dv) dv.textContent = parseFloat(j.dist).toFixed(1);
        if (yv) yv.textContent = parseInt(j.yaw) + "°";
        if (hv) hv.textContent = parseFloat(j.height).toFixed(2);
    } catch (e) { /* Unity 离线时忽略 */ }
}

function openUnityFullscreen() {
    const w = window.open(UNITY_STREAM + "?t=" + Date.now(),
        "unityFullscreen",
        "width=2560,height=1440,resizable=yes,scrollbars=no,menubar=no,toolbar=no,location=no");
    if (!w) showToast("请允许弹出窗口", "error");
}

// ================================
// 字幕 + 情绪/动作元信息
// ================================
let subTimer = null;
function showSubtitle(text, durationMs) {
    const el = document.getElementById("unitySubtitle");
    if (!el || !text) return;
    el.textContent = text;
    el.classList.remove("hidden");
    if (subTimer) clearTimeout(subTimer);
    if (durationMs && durationMs > 0) {
        subTimer = setTimeout(() => el.classList.add("hidden"), durationMs);
    }
}

function setMeta(key, value) {
    const el = document.getElementById("meta" + key);
    if (el && value !== undefined && value !== null) el.textContent = String(value);
}

// ================================
// 看板娘表情：LLM 决策 emotion 映射 + 表情调试面板（顶层定义，供全局 onclick/聊天渲染使用）
// ================================
// 看板娘表情：由 LLM 决策的 emotion（理解性判断）映射到 Anime2.5DRig 表情 preset，
// 不做关键词匹配，避免"提到生气就摆生气脸"的机械触发。
// shy 害羞时雌小鬼抛媚眼（winkL），俏皮人设。
const EMOTION_TO_LIVE2D = {
    happy: "smile", joyful: "smile", excited: "smile", cheer: "smile",
    shy: "winkL", embarrassed: "winkL", gentle: "smile",
    surprised: "surprise", shocked: "surprise", amazed: "surprise",
    sad: "usume", sorrow: "usume", upset: "usume",
    angry: "jito", annoyed: "jito", irritated: "jito",
    sleepy: "sleepy", tired: "sleepy", bored: "sleepy",
    curious: "curious", interested: "curious", confused: "curious",
    calm: "neutral", neutral: "neutral",
};
// LLM 情绪强度(1~5) → 表情幅度(0~1)，缺失默认 3
function moodIntensity(v) {
    const n = Number(v);
    if (isFinite(n) && n >= 1 && n <= 5) return (n - 1) / 4;
    return 0.8;
}
function emotionToLive2d(emotion) {
    return EMOTION_TO_LIVE2D[String(emotion || "neutral").toLowerCase()] || "neutral";
}

// 心情中文标签（聊天消息名字旁显示，与表情映射一一对应）
const EMOTION_LABELS = {
    neutral: "平静", happy: "开心", shy: "害羞", surprised: "惊讶",
    sad: "难过", angry: "生气", sleepy: "犯困", curious: "好奇",
    gentle: "温柔", calm: "平静",
};
function emotionLabel(emotion) {
    return EMOTION_LABELS[String(emotion || "").toLowerCase()] || "";
}

// ===== 表情调试面板：手动预览看板娘每个表情 =====
const EXPR_PANEL_ITEMS = [
    ["neutral", "平静"], ["smile", "微笑"], ["surprise", "惊讶"],
    ["jito", "死鱼眼"], ["usume", "委屈"], ["sleepy", "犯困"],
    ["curious", "好奇"], ["winkL", "眨眼L"], ["winkR", "眨眼R"],
];
let curExpr = null;   // 当前看板娘表情（面板高亮用）

function toggleExprPanel() {
    const panel = document.getElementById("exprPanel");
    const btn = document.querySelector(".btn-expr-toggle");
    if (!panel) return;
    const show = panel.style.display === "none";
    panel.style.display = show ? "block" : "none";
    if (btn) btn.classList.toggle("active", show);
    if (show) renderExprPanel();
}
function renderExprPanel() {
    const grid = document.getElementById("exprPanelGrid");
    if (!grid) return;
    grid.innerHTML = EXPR_PANEL_ITEMS.map(([name, label]) =>
        `<button type="button" class="expr-panel-btn${curExpr === name ? " active" : ""}"
                 onclick="pickExpr('${name}')">${label}</button>`).join("");
}
function pickExpr(name) {
    curExpr = name;
    if (window.Live2DCompanion) window.Live2DCompanion.setExpression(name, 1);
    renderExprPanel();
}
// 特效定位模式：开启后看板娘身上的泪滴/zzz 可拖拽，松手自动保存位置
let fxPosModeOn = false;
function toggleFxPosMode() {
    fxPosModeOn = !fxPosModeOn;
    const btn = document.getElementById("exprFxToggle");
    if (btn) btn.classList.toggle("active", fxPosModeOn);
    if (window.Live2DCompanion) window.Live2DCompanion.setFxPosMode(fxPosModeOn);
}
function refreshExprPanelHighlight() {
    const panel = document.getElementById("exprPanel");
    if (panel && panel.style.display !== "none") renderExprPanel();
}

// ================================
// AI WebSocket (Python:8765) 订阅决策事件 -> UI 字幕/元信息同步
// ================================
let aiWs = null;
let aiReconn = null;
function connectAiWs() {
    if (aiWs && (aiWs.readyState === WebSocket.OPEN || aiWs.readyState === WebSocket.CONNECTING)) return;
    try {
        aiWs = new WebSocket("ws://localhost:8765/");
    } catch (e) {
        setUnityWsBadge(false);
        scheduleAiWsReconnect();
        return;
    }
    aiWs.onopen = () => setUnityWsBadge(true);
    aiWs.onclose = () => { setUnityWsBadge(false); scheduleAiWsReconnect(); };
    aiWs.onerror = () => setUnityWsBadge(false);

    // 看板娘表情：由 LLM 决策的 emotion（理解性判断）映射（映射表在顶层 EMOTION_TO_LIVE2D 定义）

    aiWs.onmessage = (ev) => {
        let env;
        try { env = JSON.parse(ev.data); } catch { return; }
        const type = env.message_type;
        const payload = env.payload || {};
        switch (type) {
            case "decision": {
                const d = payload.decision || payload;
                if (d.text) showSubtitle(d.text, Math.max(5000, (d.text.length || 0) * 180));
                if (d.emotion) setMeta("Emotion", d.emotion);
                if (d.action) setMeta("Action", d.action);
                // 看板娘表情：优先用指令性表情（live2d_expr），否则由 LLM 决策的情绪（理解性）驱动；
                // 强度用 LLM 输出的 intensity(1~5)，指令性表情默认全量
                if (window.Live2DCompanion) {
                    const expr = d.live2d_expr || emotionToLive2d(d.emotion);
                    const inten = d.live2d_expr ? 1 : moodIntensity(d.intensity);
                    window.Live2DCompanion.setExpression(expr, inten);
                    curExpr = expr;
                    refreshExprPanelHighlight();
                    // 肢体动作：LLM 决策的 action（idle/greet/think/talk/laugh/wave/yawn/comfort）
                    // 映射到 rig 动作库驱动看板娘（不再只是显示在元信息里）
                    if (d.action) {
                        const A2L = { greet: "wave", listen: "thinking", think: "thinking",
                                      talk: "nod", laugh: "excited", wave: "wave",
                                      yawn: "yawn", comfort: "nod" };
                        window.Live2DCompanion.setAction(A2L[d.action] || d.action);
                    }
                }
                break;
            }
            case "partial_text": {
                if (payload.text) showSubtitle(payload.text, 6000);
                break;
            }
            case "state_change": {
                if (payload.new_state) setMeta("State", payload.new_state);
                break;
            }
            case "user_activity_detected": {
                const a = payload.activity || payload;
                if (a && a.type) setMeta("Activity", a.type + (a.desc ? " · " + a.desc : ""));
                else if (typeof a === "string") setMeta("Activity", a);
                break;
            }
            case "tts_started": {
                if (payload.text) showSubtitle(payload.text, (payload.text.length || 0) * 180 + 3000);
                // 看板娘张嘴；表情由 decision（LLM 情绪）驱动，这里不再覆盖
                if (window.Live2DCompanion) window.Live2DCompanion.setSpeaking(true);
                break;
            }
            case "tts_finished": {
                if (subTimer) { clearTimeout(subTimer); subTimer = null; }
                const el = document.getElementById("unitySubtitle");
                if (el) setTimeout(() => el.classList.add("hidden"), 1200);
                if (window.Live2DCompanion) window.Live2DCompanion.setSpeaking(false);  // 看板娘闭嘴
                break;
            }
            case "clothing": {
                // 单个物品状态（语音/文字触发时也同步按钮）
                syncClothButton(payload.item, !!payload.on);
                break;
            }
            case "clothing_state": {
                // Unity 连接/变更时推送全量状态：进入时同步所有按钮高亮
                syncClothButtons(payload.states || []);
                break;
            }
            case "anim_done": {
                // Unity 单次动作播完：推进"测试全部动画"到下一个
                onAnimDone();
                break;
            }
            case "agent_confirm": {
                // 电脑控制安全确认：弹窗让用户允许/拒绝高危操作（点/按/打字/命令行）
                const desc = payload.desc || "执行一个电脑操作";
                const key = payload.key || "";
                const ok = window.confirm("达妮娅想操作电脑：\n" + desc + "\n\n允许执行吗？");
                try { ev.target.send(JSON.stringify({ message_type: "agent_confirm_reply", payload: { key: key, allow: ok } })); } catch (e) {}
                break;
            }
        }
    };
}
function scheduleAiWsReconnect() {
    if (aiReconn) clearTimeout(aiReconn);
    aiReconn = setTimeout(connectAiWs, 3000);
}

// 绑定表单提交
document.addEventListener("DOMContentLoaded", () => {
    const form = document.getElementById("editForm");
    if (form) {
        form.addEventListener("submit", saveCharacter);
    }
    // 首次进入后台先预检测 Unity 端口状态（用 img probe 规避 CORS）
    probeUnityOnline((ok) => {
        if (ok) setUnityHttpBadge(true, "在线");
        else setUnityHttpBadge(false, "离线");
    });
    setupClothingButtons();
    setupEmotionButtons();
    init();
    // 页面加载即连接 AI WebSocket（原先只在切到 Unity Tab 时连接），
    // 否则聊天界面收不到 tts_started，看板娘不会张嘴/变表情。
    connectAiWs();
});

// ================================
// 日报周报 Tab（活动记忆 → 浏览 / 高级感图片 / 导出）
// ================================
let currentReport = null;   // {kind, key, content}

function escHtml(s) {
    return String(s == null ? "" : s)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

// 轻量 Markdown → HTML（标题/列表/粗体/换行），只读展示
function mdToHtml(md) {
    const lines = (md || "").split("\n");
    const out = [];
    let inList = false;
    for (const raw of lines) {
        const line = raw.trimEnd();
        if (!line.trim()) { if (inList) { out.push("</ul>"); inList = false; } continue; }
        let m;
        if ((m = line.match(/^#{1,6}\s+(.*)/))) {
            if (inList) { out.push("</ul>"); inList = false; }
            const lvl = Math.min(m[1] ? 6 : 1, 6);
            const txt = escHtml(m[0].replace(/^#+\s*/, ""));
            out.push(`<h${lvl}>${txt}</h${lvl}>`);
        } else if (/^\s*[-*]\s+/.test(line) || /^\s*\d+[.、)]\s+/.test(line)) {
            if (!inList) { out.push("<ul>"); inList = true; }
            const txt = escHtml(line.replace(/^\s*([-*]|\d+[.、)])\s+/, ""));
            out.push(`<li>${txt}</li>`);
        } else {
            if (inList) { out.push("</ul>"); inList = false; }
            out.push(`<p>${escHtml(line)}</p>`);
        }
    }
    if (inList) out.push("</ul>");
    const html = out.join("\n")
        .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
        .replace(/`(.+?)`/g, "<code>$1</code>");
    return `<div class="report-md">${html}</div>`;
}

async function loadActivityReports() {
    try {
        const res = await pywebview.api.get_activity_reports();
        if (!res || !res.success) {
            document.getElementById("dailyList").textContent = "加载失败";
            document.getElementById("weeklyList").textContent = "加载失败";
            return;
        }
        const daily = document.getElementById("dailyList");
        const weekly = document.getElementById("weeklyList");
        daily.innerHTML = "";
        weekly.innerHTML = "";
        const mk = (kind, key) => {
            const el = document.createElement("div");
            el.className = "mem-item report-item";
            el.innerHTML = `<div class="mem-item-text">${escHtml(key)}</div>`;
            el.style.cursor = "pointer";
            el.onclick = () => selectReport(kind, key, el);
            return el;
        };
        if (!res.daily || res.daily.length === 0) {
            daily.innerHTML = `<div style="padding:16px;text-align:center;color:var(--text-muted);">暂无日报（跨天后自动生成）</div>`;
        } else {
            res.daily.forEach(d => daily.appendChild(mk("daily", d)));
        }
        if (!res.weekly || res.weekly.length === 0) {
            weekly.innerHTML = `<div style="padding:16px;text-align:center;color:var(--text-muted);">暂无周报（跨周后自动生成）</div>`;
        } else {
            res.weekly.forEach(w => weekly.appendChild(mk("weekly", w)));
        }
    } catch (e) {
        showToast("加载日报周报失败: " + e.message, "error");
    }
}

function setReportButtons(enabled) {
    ["btnRenderImage", "btnAiImage", "btnExportReport"].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.disabled = !enabled;
    });
}

async function selectReport(kind, key, itemEl) {
    document.querySelectorAll(".report-item").forEach(el => el.classList.remove("selected"));
    if (itemEl) itemEl.classList.add("selected");
    try {
        const res = await pywebview.api.get_report_markdown(kind, key);
        if (!res || !res.success) {
            showToast(res && res.error ? res.error : "读取报告失败", "error");
            return;
        }
        currentReport = { kind: res.kind, key: res.key, content: res.content };
        const box = document.getElementById("reportPreview");
        box.className = "report-preview";
        box.innerHTML = mdToHtml(res.content);
        setReportButtons(true);
    } catch (e) {
        showToast("读取报告失败: " + e.message, "error");
    }
}

async function renderReportImage() {
    if (!currentReport) return;
    const btn = document.getElementById("btnRenderImage");
    if (btn) { btn.disabled = true; btn.textContent = "渲染中..."; }
    try {
        const res = await pywebview.api.render_report_image(currentReport.kind, currentReport.key);
        if (res && res.success && res.data_url) {
            showReportImage(res.data_url, "本地渲染 · 高级感日程卡");
            showToast("图片已生成", "success");
        } else {
            showToast((res && res.error) || "渲染失败", "error");
        }
    } catch (e) {
        showToast("渲染失败: " + e.message, "error");
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = " 本地渲染图片"; }
    }
}

async function generateAiReportImage() {
    if (!currentReport) return;
    const btn = document.getElementById("btnAiImage");
    if (btn) { btn.disabled = true; btn.textContent = "生图中（约1分钟）..."; }
    try {
        const res = await pywebview.api.generate_report_ai_image(currentReport.kind, currentReport.key);
        if (res && res.success && res.data_url) {
            showReportImage(res.data_url, "AI 生图封面（CogView-3-Flash 免费）");
            showToast("AI 封面已生成", "success");
        } else {
            showToast((res && res.error) || "生图失败", "error");
        }
    } catch (e) {
        showToast("生图失败: " + e.message, "error");
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = " AI 生图封面"; }
    }
}

function showReportImage(dataUrl, caption) {
    const box = document.getElementById("reportPreview");
    box.className = "report-preview";
    box.innerHTML = `<div style="text-align:center;">
        <img src="${dataUrl}" alt="报告图片" style="max-width:100%;max-height:70vh;border-radius:12px;box-shadow:0 8px 30px rgba(255,107,157,0.25);"/>
        <div style="margin-top:10px;color:var(--text-muted);font-size:12px;">${escHtml(caption)}</div>
    </div>`;
}

async function exportReport() {
    if (!currentReport) return;
    const btn = document.getElementById("btnExportReport");
    if (btn) { btn.disabled = true; btn.textContent = "导出中..."; }
    try {
        const res = await pywebview.api.export_report(currentReport.kind, currentReport.key);
        if (res && res.success) {
            showToast("已导出到: " + (res.folder || ""), "success");
        } else {
            showToast((res && res.error) || "导出失败", "error");
        }
    } catch (e) {
        showToast("导出失败: " + e.message, "error");
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = " 导出"; }
    }
}

// ================================
// 课程记录 Tab（网课自动记录 → 笔记列表）
// ================================
const COURSE_STATUS_LABEL = { recording: "录制中", finalizing: "收尾中", complete: "已完成", failed: "失败" };

async function refreshCourseStatus() {
    try {
        const res = await pywebview.api.get_course_status();
        const badge = document.getElementById("courseStatusBadge");
        if (!badge) return;
        if (res && res.success) {
            if (res.recording) {
                badge.classList.add("online");
                badge.textContent = "● 正在录制: " + res.title;
            } else if (!res.enabled) {
                badge.classList.remove("online");
                badge.textContent = "● 已关闭 (config course.enabled=false)";
            } else {
                badge.classList.remove("online");
                badge.textContent = "● 待机";
            }
        }
    } catch (e) { /* 忽略状态刷新失败 */ }
}

async function loadCourses() {
    const list = document.getElementById("courseList");
    try {
        const res = await pywebview.api.list_courses();
        if (!res || !res.success) {
            list.innerHTML = `<div style="padding:16px;text-align:center;color:var(--text-muted);">${escHtml(res && res.error || "加载失败")}</div>`;
            return;
        }
        list.innerHTML = "";
        if (!res.courses || res.courses.length === 0) {
            list.innerHTML = `<div style="padding:16px;text-align:center;color:var(--text-muted);">暂无课程记录：打开网课（网课/课堂/慕课等）会自动开始记录，结束时生成笔记</div>`;
            return;
        }
        res.courses.forEach(c => {
            const card = document.createElement("div");
            card.className = "mem-item course-item";
            const label = COURSE_STATUS_LABEL[c.status] || c.status;
            const date = (c.created_at || "").slice(0, 16).replace("T", " ");
            const hasNote = c.output ? "有笔记" : "未渲染";
            const statusCls = c.status === "complete" ? "course-ok" : (c.status === "failed" ? "course-bad" : "course-run");
            card.innerHTML = `
                <div class="mem-item-text" style="flex:1;min-width:0;">
                    <div class="course-title">${escHtml(c.title)}</div>
                    <div class="course-meta">${escHtml(date)} · ${escHtml(label)} · 关键画面 ${c.keyframes} 张 · ${hasNote}</div>
                    ${c.summary ? `<div class="course-summary">${escHtml(c.summary)}</div>` : ""}
                </div>
                ${c.output ? `<button class="btn-refresh" onclick="openCourseNote('${escHtml(c.output.replace(/\\/g, "\\\\").replace(/'/g, "\\'"))}')">${DIcon.svg('file-text')} 打开笔记</button>` : ""}
            `;
            card.querySelector(".course-title").classList.add(statusCls);
            list.appendChild(card);
        });
    } catch (e) {
        list.innerHTML = `<div style="padding:16px;text-align:center;color:var(--text-muted);">加载失败: ${escHtml(e.message)}</div>`;
    }
}

async function openCourseNote(path) {
    try {
        await pywebview.api.open_course_dir();  // 打开目录（笔记 README.md 在子目录）
        showToast("已打开课程目录: " + path, "success");
    } catch (e) {
        showToast("打开失败: " + e.message, "error");
    }
}

async function openCourseDir() {
    try {
        const res = await pywebview.api.open_course_dir();
        if (res && res.success) {
            showToast("已打开: " + res.path, "success");
        } else {
            showToast((res && res.error) || "打开失败", "error");
        }
    } catch (e) {
        showToast("打开失败: " + e.message, "error");
    }
}
