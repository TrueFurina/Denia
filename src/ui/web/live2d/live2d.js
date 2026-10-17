// AI 桌面伴侣 - 看板娘（Anime2.5DRig PSD 纸片人）
// =============================================================
// 怎么把你做的模型放进来：
//   1. 用 see-through 拆好的 PSD 命名为 model.psd
//   2. 放到 src/ui/web/live2d/models/model.psd
//   3. 重启软件 / 刷新页面，右下角看板娘自动加载并驱动（无需手动拖拽）
// 备注：模型文件不存在时静默跳过，不影响聊天界面。
// =============================================================
window.Live2DCompanion = (function () {
    const MODEL = "live2d/models/model.psd";

    let pendingSpeak = null;   // TTS 说话状态（rig 就绪后推送）
    let pendingPsd = null;     // 待加载的 PSD base64（rig 就绪后推送）
    let pendingExpr = null;    // 最近表情（rig 就绪后补发）{name, intensity}
    let rigReady = false;

    function getFrame() {
        return document.getElementById("live2dRigFrame");
    }

    function send() {
        const f = getFrame();
        if (!f || !f.contentWindow) return;
        try {
            // 模型优先推送（rig 确认就绪后才清空，防止 iframe 未加载完消息丢失）
            if (pendingPsd) {
                f.contentWindow.postMessage({ type: "live2d-psd", data: pendingPsd }, "*");
                if (rigReady) pendingPsd = null;
            }
            // 说话状态
            if (rigReady && pendingSpeak !== null) {
                f.contentWindow.postMessage({ type: "live2d-speak", on: !!pendingSpeak }, "*");
            }
            // 表情（持续状态，rig 端重复应用无副作用）
            if (rigReady && pendingExpr) {
                f.contentWindow.postMessage({
                    type: "live2d-expr",
                    name: pendingExpr.name,
                    intensity: pendingExpr.intensity
                }, "*");
            }
            // 特效定位模式（rig 就绪后补发，确保模式开启不丢失）
            if (rigReady && pendingFxMode !== null) {
                f.contentWindow.postMessage({ type: "live2d-fx-mode", on: pendingFxMode }, "*");
            }
        } catch (e) { }
    }

    // 取 PSD：优先 pywebview JS API（软件 file:// 环境），浏览器调试时 fallback 到 fetch
    async function loadPsd() {
        try {
            if (window.pywebview && window.pywebview.api && window.pywebview.api.get_live2d_psd) {
                const d = await window.pywebview.api.get_live2d_psd();
                if (d) return d;
            }
        } catch (e) { }
        try {
            const r = await fetch(MODEL);
            if (r.ok) {
                const buf = await r.arrayBuffer();
                const bin = new Uint8Array(buf);
                let b64 = "";
                for (let i = 0; i < bin.length; i++) b64 += String.fromCharCode(bin[i]);
                return "data:application/octet-stream;base64," + btoa(b64);
            }
        } catch (e) { }
        return "";
    }

    async function init() {
        const psdData = await loadPsd();
        if (!psdData) {
            console.warn(
                "[看板娘] 未找到 " + MODEL +
                "，看板娘跳过。把拆好的 PSD 命名为 model.psd 放入 live2d/models/"
            );
            return;
        }
        // 确保 dock 展开（默认即展开，防御性去掉 collapsed）
        const dock = document.getElementById("live2dDock");
        if (dock) dock.classList.remove("collapsed");
        pendingPsd = psdData;
        send();  // iframe 就绪后会再补发一次
        console.log("[看板娘] PSD 已就绪，等待 rig 加载模型");
    }

    // TTS 嘴型：说话时持续口型开合，闭嘴后恢复随机
    function setSpeaking(on) {
        pendingSpeak = !!on;
        send();
        if (!pendingSpeak) setTimeout(send, 300); // 闭嘴时补发一次，确保生效
    }

    // 表情：neutral/smile/surprise/jito/usume/sleepy/curious/winkL/winkR（rig 端 preset）
    // intensity: 0~1 表情幅度（可选，来自 LLM 情绪强度；不传时 rig 端默认 0.8）
    function setExpression(name, intensity) {
        pendingExpr = { name: name || "neutral", intensity: intensity };
        send();
    }

    // 特效定位模式（泪滴/zzz 拖拽）：开启后 rig 端特效可拖拽，松手自动保存
    // 用 pending 机制：rig 未就绪时缓存，就绪后由 send() 补发（避免消息丢失导致模式没开）
    let pendingFxMode = null;
    function setFxPosMode(on) {
        pendingFxMode = !!on;
        send();
    }

    // 动作：nod/shake/thinking/wave/cheer/excited/sigh/proud/surprised/yawn/plea（rig 端 ACTIONS）
    // 肢体动作，与表情并存；rig 未就绪时丢弃（一次性动画，不需要补发）
    function setAction(name) {
        if (rigReady && name) {
            const f = getFrame();
            if (f && f.contentWindow) {
                try {
                    f.contentWindow.postMessage({ type: "live2d-action", name: name }, "*");
                } catch (e) { }
            }
        }
    }

    // rig 页面加载完成后，把 pending 说话状态推过去
    window.addEventListener("message", (ev) => {
        if (!ev.data) return;
        if (ev.data.type === "live2d-rig-ready") {
            rigReady = true;
            send();
        }
        // 看板娘时间性互动提示（到饭点/深夜），用聊天区字幕显示
        if (ev.data.type === "live2d-tip" && ev.data.text && window.showSubtitle) {
            window.showSubtitle(ev.data.text, 6000);
        }
    });

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }

    return { setSpeaking, setExpression, setAction, setFxPosMode };
})();
