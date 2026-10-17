/* 通话模式弹窗（2026-10-17 起：**只保留云端双工**）。
 *
 * 作用：拦截"拨打语音/拨打视频"入口，让用户确认后进入云端双工：
 *   - 云端双工（唯一选项）：调用 startDuplexBridged(media, "realtime") → 后端云端实时全双工
 *     （模型名跟随 cloud_omni 配置，不写死）。
 *   （旧"单工轮询"与"本地双工"均已下线：实测云端体验更好；且后端 start_duplex_call
 *     已强制 target=realtime，弹窗再留本地选项就是误导用户。）
 *
 * 用法（在 index.html 引入本脚本，并把 startCall 改为调用 showCallModeModal）：
 *   <script src="call_mode_modal.js"></script>
 *   function startCall(media){ showCallModeModal(media); }
 */
(function () {
  const SIMPLEX_URL = "http://127.0.0.1:9871/";
  let mediaArg = "voice";
  let built = false;

  function ensureDom() {
    if (built) return;
    const css = `
      #callModeMask{position:fixed;inset:0;background:var(--mask);display:none;
        align-items:center;justify-content:center;z-index:9999;font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;}
      #callModeMask.open{display:flex;}
      .cm-card{width:340px;max-width:88vw;background:var(--surface-1);color:var(--ink);border-radius:16px;
        padding:22px 20px;box-shadow:var(--shadow-pop);border:1px solid var(--line);}
      .cm-title{font-size:17px;font-weight:600;margin:0 0 4px;}
      .cm-sub{font-size:12px;color:var(--ink-3);margin:0 0 16px;line-height:1.5;}
      .cm-opt{display:block;width:100%;text-align:left;background:var(--surface-2);border:1.5px solid var(--line);
        border-radius:12px;padding:13px 15px;margin-bottom:11px;cursor:pointer;color:var(--ink);transition:.15s;}
      .cm-opt:hover{border-color:var(--accent-1);}
      .cm-opt:disabled{opacity:.45;cursor:not-allowed;}
      .cm-opt .t{font-size:15px;font-weight:600;display:flex;align-items:center;gap:8px;}
      .cm-opt .d{font-size:12px;color:var(--ink-3);margin-top:4px;line-height:1.45;}
      .cm-tag{font-size:10px;padding:2px 7px;border-radius:999px;background:var(--accent-soft-1);color:var(--accent-1);}
      .cm-tag.dup{background:color-mix(in srgb, var(--ok) 16%, transparent);color:var(--ok);}
      .cm-tag.rec{background:var(--accent-1);color:var(--on-accent);}
      .cm-cancel{margin-top:4px;width:100%;text-align:center;background:none;border:none;
        color:var(--ink-3);font-size:13px;cursor:pointer;padding:8px;}
    `;
    const style = document.createElement("style");
    style.textContent = css;
    document.head.appendChild(style);

    const mask = document.createElement("div");
    mask.id = "callModeMask";
    mask.innerHTML = `
      <div class="cm-card">
        <p class="cm-title">选择通话模式</p>
        <p class="cm-sub"><b>云端双工</b>最低延迟、声音最像本人（真·边听边说，需你自己的阿里云实时模型 Key）。</p>
        <button class="cm-opt" id="cmCloud" data-mode="realtime">
          <div class="t">云端双工（推荐）<span class="cm-tag rec">推荐 · 低延迟</span></div>
          <div class="d">真·云端全双工（<span id="cmCloudModel">阿里云实时模型</span>）：麦克风上行、模型听想说一气呵成，
            <b>真·边听边说、随时插话</b>，延迟最低、声音最像达妮娅（原生克隆声）。需你自己的阿里云实时模型 Key。</div>
        </button>
        <!-- 【2026-10-17 按用户要求】移除「本地双工」选项：产品口径是语音电话恒走云端
             （后端 start_duplex_call 已强制 target=realtime），保留本地选项属界面误导。 -->
        <button class="cm-cancel" id="cmCancel">取消</button>
      </div>`;
    document.body.appendChild(mask);

    mask.querySelector("#cmCloud").addEventListener("click", () => {
      close();
      // 真·云端实时全双工：经通话桥自愈；后端按 engine=realtime 连（模型名跟随 cloud_omni 配置）
      if (window.startDuplexBridged) window.startDuplexBridged(mediaArg, "realtime");
      else if (window.startDuplex) window.startDuplex(mediaArg, "realtime");
    });
    /* 【2026-10-17】#cmLocal 的点击处理已随「本地双工」选项一并移除（后端恒走云端）。 */
    mask.querySelector("#cmCancel").addEventListener("click", close);
    mask.addEventListener("click", (e) => { if (e.target === mask) close(); });
    built = true;
  }

  // 注：旧"单工轮询"入口（openSimplex / enterSimplexView / simplex_voice_server）已不再作为
  // 通话选项——实测云端双工体验远好于本地双工，弹窗只保留云端/本地双工（云端模型名跟随配置，不写死）。

  function close() {
    const m = document.getElementById("callModeMask");
    if (m) m.classList.remove("open");
  }

  window.showCallModeModal = function (media) {
    ensureDom();
    mediaArg = media || "voice";
    // 云端双工的真实模型名跟随用户在「双工引擎」绑定的 cloud_omni.model（占位符，不写死）
    let _cmModelName = "阿里云实时模型";
    try {
      if (window.Backend && typeof Backend.getDuplex === "function") {
        Backend.getDuplex().then(function (r) {
          if (r && r.success && r.cloud_omni && r.cloud_omni.model) {
            var mn = r.cloud_omni.model;
            var el = document.getElementById("cmCloudModel");
            if (el) el.textContent = mn;
            var c2 = document.getElementById("cmCloud");
            if (c2 && !c2.disabled) {
              c2.querySelector(".d").innerHTML =
                "真·云端全双工（" + mn + "）：真·边听边说、随时插话，延迟最低、" +
                "声音最像达妮娅（原生克隆声）。需你自己的阿里云实时模型 Key。";
            }
          }
        }).catch(function () {});
      }
    } catch (e) {}
    /* 【2026-10-17 按用户要求·通话模式只保留云端】本地双工已从弹窗移除：
       ① 产品口径是"语音电话恒走云端"（后端 start_duplex_call 已强制 target=realtime）；
       ② 旧逻辑把「视频 / 屏幕共享」判给本地、并把云端选项置灰并提示"请选本地双工" ——
          等于把用户往一个后端根本不会走的分支上引，属界面误导。
       现在云端永远可选；视频/屏幕是否真能用由后端如实回报，不用文案替后端下判断。 */
    const cloud = document.getElementById("cmCloud");
    if (cloud) {
      cloud.disabled = false;
      cloud.querySelector(".d").innerHTML =
        "真·云端全双工（" + _cmModelName + "）：真·边听边说、随时插话，延迟最低、" +
        "声音最像达妮娅（原生克隆声）。需你自己的阿里云实时模型 Key。";
    }
    const m = document.getElementById("callModeMask");
    if (m) m.classList.add("open");
  };
})();
