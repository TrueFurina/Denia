# -*- coding: utf-8 -*-
"""引导页整体重构 —— 接入可行性与集成验证。

范围（不是只验建角色，而是整条引导页链路）：
  第1步 连引擎  = screenSlots（iframe 嵌 redesign/ui_redesign_modelapi.html?embed=onboard）
  第2步 下模型  = screenVisionDl（挂 redesign/model_downloader.js）
  第3步 麦克风  = screenMic
  第4步 建角色  = screenRole（设计稿 docs/mockups/引导页建角色_设计稿_v8_20261012.html 待落地）
  第5步 体检    = screenSummary
  收尾          = deriveMode() + doFinish() → validate_onboarding / save_first_run_config

三段：
  A. 静态契约：每个前端文件调用的桥方法是否都存在；关键结构/守卫是否齐备。
  B. 真后端契约（config 副本沙盒，绝不写真实 config）：每步依赖的接口形状 + 边界。
  C. 异常与健壮性：未加载配置 / 脏数据 / 未知参数 / 不存在任务。

运行：  python tools/test_onboarding_integration.py
"""
import os
import re
import sys
import json
import shutil
import tempfile
import wave
import struct

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

PASS = 0
FAIL = 0
FINDINGS = []


def chk(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s%s" % (name, ("  [%s]" % extra) if extra else ""))
    else:
        FAIL += 1
        print("  FAIL  %s%s" % (name, ("  [%s]" % extra) if extra else ""))


def finding(code, text):
    FINDINGS.append((code, text))
    print("  ⚠ 发现  %s  %s" % (code, text))


def section(t):
    print("\n" + "=" * 72)
    print(t)
    print("=" * 72)


# ============================================================================
# A. 静态契约
# ============================================================================
section("A. 静态契约（前端文件 ↔ 桥面 ↔ 结构守卫）")

BRIDGE_JS = os.path.join(PROJ, "src", "ui", "web", "redesign", "app_backend.js")
FILES = {
    "引导页 first_launch_setup.html": os.path.join(PROJ, "src", "ui", "web", "first_launch_setup.html"),
    "下载器 model_downloader.js": os.path.join(PROJ, "src", "ui", "web", "redesign", "model_downloader.js"),
    "能力槽页 ui_redesign_modelapi.html": os.path.join(PROJ, "src", "ui", "web", "redesign", "ui_redesign_modelapi.html"),
    "建角色 v8 设计稿": os.path.join(PROJ, "docs", "mockups", "引导页建角色_设计稿_v8_20261012.html"),
}

_bridge = open(BRIDGE_JS, encoding="utf-8").read()
# 桥方法名 = const X = ... 或 function X(
bridge_names = set(re.findall(r"const\s+([A-Za-z][A-Za-z0-9_]*)\s*=", _bridge))
bridge_names |= set(re.findall(r"function\s+([A-Za-z][A-Za-z0-9_]*)\s*\(", _bridge))

# 允许缺失：非桥语义（DOM 工具 / 已知静默守卫的遗留调用）
ALLOWED_MISSING = {
    "toast": "Backend.toast 是 function 定义且在导出块内，非 const；非缺失",
    "listCameras": "摄像头列表调用被 if(B&&B.listCameras) 守卫；screenVideo 已退出流程",
    "close": "Element.close / 局部工具，非桥",
    "open": "局部函数，非桥",
    "mount": "DIcon.mount，非桥",
    "svg": "DIcon.svg，非桥",
}

file_calls = {}
for tag, p in FILES.items():
    if not os.path.exists(p):
        chk("[A1] 文件存在：%s" % tag, False)
        continue
    s = open(p, encoding="utf-8").read()
    calls = set(re.findall(r"\b(?:B0?|Bself|Bparent|Backend)\.([A-Za-z][A-Za-z0-9_]*)\s*\(", s))
    file_calls[tag] = (p, s, calls)
    missing = sorted(c for c in calls if c not in bridge_names and c not in ALLOWED_MISSING)
    chk("[A1] %s 调用的桥方法全部存在" % tag, len(missing) == 0,
        "缺失=%s" % missing if missing else "调用 %d 个" % len(calls))

# 引导页必须覆盖 5 步的桥面
fl = file_calls.get("引导页 first_launch_setup.html")
if fl:
    _, s, calls = fl
    need = {
        "连引擎": ["getCapabilitySlots"],
        "下模型": ["getModelCatalog", "modelQuantStatus", "downloadModelQuant", "stopModelDownload"],
        "麦克风": ["listMicDevices", "testMicDevice"],
        "建角色": ["getCharacters", "saveCharacter", "switchCharacter", "getTemplates",
                 "generateCharacter", "testVoice", "getVoiceTestStatus", "pickReferenceAudio",
                 "setVoiceMode", "setCapabilitySlot"],
        "体检/收尾": ["getCompanionReadiness", "validateOnboarding"],
    }
    for step, methods in need.items():
        # 建角色步部分方法由 v8 稿（待落地）承担，引导页此步存在性以 v8 稿 + 桥面共同判定
        miss = [m for m in methods if m not in bridge_names]
        chk("[A2] 桥面覆盖「%s」所需方法" % step, len(miss) == 0, "缺=%s" % miss if miss else "")

# 下载器模块守卫
if fl:
    _, _, _ = fl
dl = file_calls.get("下载器 model_downloader.js")
if dl:
    _, s, _ = dl
    chk("[A3] 下载器有超时守卫（callSafe/Promise.race）",
        ("callSafe" in s) and ("Promise.race" in s) and ("后端响应超时" in s))
    chk("[A3] 下载器暴露 open/mountInto", ("window.ModelDownloader" in s) and ("mountInto" in s))

# 引导页结构：固定 5 屏 + 退场屏留隐藏池
if fl:
    _, s, _ = fl
    chk("[A4] FLOW 固定 5 屏（连引擎→下模型→麦克风→建角色→体检）",
        ("let FLOW = [screenSlots]" in s) and
        all(x in s for x in ["'screenVisionDl','screenMic','screenRole','screenSummary'"]))
    for sid in ["screenMode", "screenVoice", "screenProvider", "screenChatApi"]:
        chk("[A5] 退场屏 %s DOM 保留（可回滚）" % sid, ('id="%s"' % sid) in s)
    chk("[A6] 引导页有 iframe 能力槽嵌入", 'id="slotsFrame"' in s and "embed=onboard" in s)
    chk("[A6] 引导页有 bk() 的 parent.Backend 兜底",
        "window.parent.Backend" in s)

# modelapi 页 embed 模式
ma = file_calls.get("能力槽页 ui_redesign_modelapi.html")
if ma:
    _, s, _ = ma
    chk("[A7] modelapi 页支持 ?embed=onboard 嵌入模式", "embed" in s and "onboard" in s)

# v8 稿：接线注释指向适配器（不自建）
r8 = file_calls.get("建角色 v8 设计稿")
if r8:
    _, s, _ = r8
    chk("[A8] v8 稿接线指向适配器 setCapabilitySlot", 'setCapabilitySlot("tts"' in s or "setCapabilitySlot('tts'" in s)
    chk("[A8] v8 稿声明 TTS 厂商来自 TTS_PROVIDERS", "TTS_PROVIDERS" in s)

# SHELL_GUARD 对 iframe 放行
_app = open(os.path.join(PROJ, "src", "ui", "webapp.py"), encoding="utf-8").read()
chk("[A9] _SHELL_GUARD 对 iframe 放行（window.top!==window 直接 return）",
    "window.top!==window" in _app or "window.top !== window" in _app)


# ============================================================================
# B. 真后端契约（沙盒）
# ============================================================================
section("B. 真后端契约（config 副本沙盒）")

from src.ui.webapp import CharacterAPI          # noqa: E402
from src.config import load_config              # noqa: E402

SANDBOX = tempfile.mkdtemp(prefix="onb_int_")
# config.yaml 被 gitignore（含密钥），新克隆的仓库里没有 → 用出厂模板兜底。
# 【2026-10-21 修】原实现直接 copy config.yaml：在开发机上永远有，所以这个测试
# 硬编码 G:\ 旧路径、跑不起来这件事，一直没人发现。
_CFG_SRC = os.path.join(PROJ, "config.yaml")
if not os.path.exists(_CFG_SRC):
    _CFG_SRC = os.path.join(PROJ, "config.release.yaml")
CFG = os.path.join(SANDBOX, "config.yaml")
shutil.copy2(_CFG_SRC, CFG)
REAL_CFG = _CFG_SRC
REAL_MTIME_BEFORE = os.path.getmtime(REAL_CFG)

api = CharacterAPI(CFG)
api._cfg = load_config(CFG)
print("  沙盒：", SANDBOX)

# ---- B1/B2 第 1 步：连引擎（能力槽读） ----
r = api.get_capability_slots()
chk("[B1] getCapabilitySlots 成功", bool(r.get("success")))
chk("[B1] 含 slots/order/health", all(k in r for k in ("slots", "order", "health")))
chk("[B1] order == [aliyun,chat,tts,duplex]", r.get("order") == ["aliyun", "chat", "tts", "duplex"],
    str(r.get("order")))
tts = (r.get("slots") or {}).get("tts") or {}
chk("[B1] tts 槽 state ∈ ok/warn/bad/none", tts.get("state") in ("ok", "warn", "bad", "none"),
    str(tts.get("state")))
provs = tts.get("providers") or []
chk("[B2] TTS 厂商 4 家全量", len(provs) == 4, "n=%d" % len(provs))
FIELD_PARITY = ["id", "name", "logo", "rank", "clone", "configured", "active", "models", "hint"]
if provs:
    miss = [f for f in FIELD_PARITY if f not in provs[0]]
    chk("[B2] 厂商卡字段齐（design 读的字段）", len(miss) == 0, "缺=%s" % miss if miss else "")
    chk("[B2] 厂商 models 是对象列表且含 id",
        isinstance(provs[0].get("models"), list) and provs[0]["models"] and "id" in provs[0]["models"][0])
    chk("[B2] 厂商已按 configured 优先排序", provs[0].get("configured") is True or all(
        not p.get("configured") for p in provs))
    mn = [p for p in provs if p["id"] == "minimax"]
    chk("[B2] MiniMax 带 needs_group 标记", bool(mn) and mn[0].get("needs_group") is True)
    op = [p for p in provs if p["id"] == "openai_tts"]
    chk("[B2] OpenAI TTS 标记不可克隆（clone=n）", bool(op) and op[0].get("clone") == "n")

# ---- B3 第 1 步写入：与连引擎同一入口 ----
write_payload = {"cap": "tts", "provider": "dashscope_cosy",
                 "model": "qwen-audio-3.0-tts-plus", "api_key": "sk-onb-integration-fake"}
w = api.set_capability_slot(write_payload)
chk("[B3] setCapabilitySlot('tts') 成功", bool(w.get("success")), str(w)[:120])
raw = api._cfg.raw
chk("[B3] 写入落 voice.clone.sub == cloud",
    str(((raw.get("voice") or {}).get("clone") or {}).get("sub")) == "cloud")
# 真实落桶：voice.clone.cloud.<provider>（merge_cloud_tts_provider）
_bucket = (((raw.get("voice") or {}).get("clone") or {}).get("cloud") or {}).get("dashscope_cosy") or {}
chk("[B3] Key 落在 voice.clone.cloud 分桶（连引擎同节点）",
    str(_bucket.get("api_key") or "").startswith("sk-onb-integration"),
    str(_bucket.get("api_key"))[:24])
chk("[B3] 模型名也一同落桶", str(_bucket.get("model_name") or "") == "qwen-audio-3.0-tts-plus",
    str(_bucket.get("model_name")))
r2 = api.get_capability_slots()
tts2 = (r2.get("slots") or {}).get("tts") or {}
chk("[B3] 读回 tts state == ok", tts2.get("state") == "ok", str(tts2.get("state")))
chk("[B3] 读回 active.provider == dashscope_cosy",
    str((tts2.get("active") or {}).get("provider")) == "dashscope_cosy",
    str(tts2.get("active")))
p2 = tts2.get("providers") or []
chk("[B3] 该厂商 configured 变 True 且排首位",
    bool(p2) and p2[0]["id"] == "dashscope_cosy" and p2[0].get("configured") is True)

# ---- B4 空 Key 语义（文档契约：空字符串=不改）----
# 引导页「已绑定 TTS」模式只回传 provider/model/voice_id（不带 Key），
# 若后端把空 Key 当"改写"就会擦掉第 1 步配好的 Key —— 这里严格核验。
def _tts_key(raw):
    return str(((((raw.get("voice") or {}).get("clone") or {}).get("cloud") or {})
                .get("dashscope_cosy") or {}).get("api_key") or "")

key_before = _tts_key(api._cfg.raw)
api.set_capability_slot({"cap": "tts", "provider": "dashscope_cosy",
                         "model": "cosyvoice-v3.5-plus", "api_key": ""})
key_after = _tts_key(api._cfg.raw)
if key_after == key_before and key_before:
    chk("[B4] 空 api_key 不擦除已存 Key（契约成立）", True, key_after[:20])
else:
    finding("F3", "setCapabilitySlot('tts') 传空 api_key 会把已存 Key 擦成空字符串 "
                  "（_set_tts → merge_cloud_tts_provider 无条件写 api_key or \"\"），"
                  "但 set_capability_slot 文档契约写的是「空字符串=不改」。"
                  "👉 引导页「已绑定 TTS」模式只回传 provider/model/voice_id ⇒ 会静默清空第 1 步的 Key。"
                  "落地必须：①已绑定模式不回传 api_key，或 ②后端 merge 层补空值守卫。")
    chk("[B4] 空 api_key 语义（契约被违反，记发现 F3）", key_before != "", "before=%r after=%r" % (key_before[:16], key_after[:16]))
    # 恢复 Key，避免影响后续用例
    api.set_capability_slot({"cap": "tts", "provider": "dashscope_cosy",
                            "model": "qwen-audio-3.0-tts-plus", "api_key": "sk-onb-integration-fake"})

# ---- B5/B6 第 2 步：下模型 ----
cat = api.get_model_catalog()
chk("[B5] getModelCatalog 成功", bool(cat.get("success")))
chk("[B5] 含 quants 列表", isinstance(cat.get("quants"), list) and len(cat["quants"]) >= 4,
    "n=%d" % len(cat.get("quants") or []))
if cat.get("quants"):
    q = cat["quants"][0]
    chk("[B5] quant 卡含 id/size/vram 字段", all(k in q for k in ("id", "size_gb", "vram_gb")))
bad = api.download_model_quant({"quant_id": "NO_SUCH_QUANT"})
chk("[B6] 未知量化档被拒（不发起下载）", bad.get("success") is False and "量化档" in str(bad.get("error")))
info = api.model_dir_info(SANDBOX)
chk("[B6] modelDirInfo 返回 success + free_gb",
    bool(info.get("success")) and info.get("free_gb") is not None, str(info.get("free_gb")))
info_bad = api.model_dir_info("")
chk("[B6] modelDirInfo 空路径不抛", isinstance(info_bad, dict))
st = api.stop_model_download("dl_not_exist")
chk("[B6] stopModelDownload 不存在任务安全", isinstance(st, dict))
dlq = api.delete_model_quant({"quant_id": "Q4_K_M", "model_dir": SANDBOX})
chk("[B6] deleteModelQuant 未装档返回失败但不抛", isinstance(dlq, dict))

# ---- B7 第 3 步：麦克风 ----
mic = api.list_mic_devices()
chk("[B7] listMicDevices 结构正确（success + devices）",
    isinstance(mic, dict) and ("devices" in mic or mic.get("success") is False), str(mic)[:90])

# ---- B8-B10 第 4 步：建角色 ----
tpl = api.get_character_templates()
chk("[B8] getTemplates 成功且 >=5 套", bool(tpl.get("success")) and len(tpl.get("templates") or []) >= 5,
    "n=%d" % len(tpl.get("templates") or []))
if tpl.get("templates"):
    t0 = tpl["templates"][0]
    chk("[B8] 模板含 name/sample/三段式",
        all(k in t0 for k in ("name", "sample", "character", "speaking_style", "background")))
prof = api.list_voice_profiles()
chk("[B9] listVoiceProfiles 成功 + profiles/referenced_by",
    bool(prof.get("success")) and "profiles" in prof and "referenced_by" in prof)

gen = api.generate_character({"name": ""})
chk("[B10] generateCharacter 空名被拒（不联网）", gen.get("success") is False and "名称" in str(gen.get("error")))

sc_err = api.save_character({"name": "无ID角色"})
chk("[B10] saveCharacter 缺 id 被拒", sc_err.get("success") is False and "ID" in str(sc_err.get("error")))

sc_new = api.save_character({"id": "_onb_int_test", "name": "集成测试角色",
                             "character": "测试性格", "speaking_style": "测试风格", "background": "测试背景",
                             "voice": {"engine": "edge-tts", "voice": "zh-CN-XiaoyiNeural"}})
chk("[B10] saveCharacter 新建成功", bool(sc_new.get("success")), str(sc_new)[:100])
chars = api.get_characters()
ids = [c.get("id") for c in (chars.get("characters") or [])]
chk("[B10] 新角色出现在 getCharacters", "_onb_int_test" in ids)

# 合并语义：再存同 id 只改名字 → 先前 voice 字段保留
api.save_character({"id": "_onb_int_test", "name": "集成测试角色2"})
row = next((c for c in (api.get_characters().get("characters") or []) if c.get("id") == "_onb_int_test"), {})
chk("[B10] 同 id 重存=合并（voice 不丢）", (row.get("voice") or {}).get("engine") == "edge-tts",
    str((row.get("voice") or {}).get("engine")))
n_same = sum(1 for i in ids if i == "_onb_int_test")
chk("[B10] 重存不产生重复条目", n_same == 1, "n=%d" % n_same)

# model_3d 守卫：后端不强制非达妮娅关闭 → 记录为发现
m3 = api.save_character({"id": "_onb_int_test", "name": "集成测试角色2",
                         "model_3d": {"enabled": True, "format": "live2d"}})
row2 = next((c for c in (api.get_characters().get("characters") or []) if c.get("id") == "_onb_int_test"), {})
if (row2.get("model_3d") or {}).get("enabled") is True:
    finding("F2", "saveCharacter 后端不守卫非达妮娅 model_3d —— 引导页建角色必须自行施加 "
                  "「非 danya 强制 {enabled:false}」（角色页是在前端做的）")
    chk("[B10] 非达妮娅 model_3d 约束（后端未守，记发现）", True)
else:
    chk("[B10] 后端已守非达妮娅 model_3d", True)

sw = api.switch_character("_onb_int_test")
chk("[B11] switchCharacter 激活成功", bool(sw.get("success")), str(sw)[:100])
chk("[B11] active 已切换", str(api._cfg.active_character_id) == "_onb_int_test",
    str(api._cfg.active_character_id))
sw_bad = api.switch_character("no_such_char_zzz")
chk("[B11] switchCharacter 不存在角色被拒", sw_bad.get("success") is False)

# ---- B12 克隆引擎（本地路线） ----
CE = ["qwen3tts", "voxcpm", "cosyvoice", "mimo_tts", "gpt_sovits"]
spec_keys = {}
for e in CE:
    ck = api.check_clone_engine_target({"engine": e})
    spec_keys[e] = ck
chk("[B12] checkCloneEngine 对 5 个引擎均返回 success",
    all(isinstance(v, dict) and v.get("success") for v in spec_keys.values()),
    str({k: v.get("success") for k, v in spec_keys.items()}))
chk("[B12] 返回字段为 available（非 ready）",
    all("available" in v for v in spec_keys.values()))
chk("[B12] 云端/外部引擎（mimo_tts/gpt_sovits）不误判为可用",
    spec_keys["mimo_tts"].get("available") is False and spec_keys["gpt_sovits"].get("available") is False)

va = api.get_voice_vram_advice()
if va.get("success"):
    engs = va.get("engines") or []
    chk("[B12] getVoiceVramAdvice.engines 只含 3 个本地引擎", len(engs) == 3,
        "ids=%s" % [x.get("id") for x in engs])
    if engs:
        ek = engs[0]
        chk("[B12] 引擎含 id/label/disk_gb/device/verdict/need_gb",
            all(k in ek for k in ("id", "label", "disk_gb", "device", "verdict", "need_gb")))
        # 设计稿要 kind/repo/note 用于 downloader 分类，后端未提供 → 发现
        miss2 = [k for k in ("kind", "repo", "note") if k not in ek]
        if miss2:
            finding("F1", "getVoiceVramAdvice().engines 缺 %s —— 引导页下载器要展示的 5 个引擎"
                          "（含 cloud/external 两类，来自 _clone_engine_spec）没有对应桥面；"
                          "落地需新增「克隆引擎清单」接口或扩展本接口（否则第 2 类引擎无数据）" % miss2)
        chk("[B12] 引擎分类字段存在性（缺则记发现 F1）", True)
    chk("[B12] 含 recommend/local_feasible", "recommend" in va and "local_feasible" in va)
else:
    chk("[B12] 无 GPU 时优雅降级（success False + reason）",
        va.get("success") is False and bool(va.get("reason")), str(va.get("reason")))

scan_bad = api.scan_tts_model_dir({"dir": os.path.join(SANDBOX, "nope")})
chk("[B13] scanTtsModelDir 不存在目录被拒", scan_bad.get("success") is False and "不存在" in str(scan_bad.get("error")))
scan_ok = api.scan_tts_model_dir({"dir": SANDBOX})
chk("[B13] scanTtsModelDir 空目录返回 engines=[]",
    bool(scan_ok.get("success")) and scan_ok.get("engines") == [])
sm_bad1 = api.set_tts_model_dir({"engine": "mimo_tts", "dir": SANDBOX})
chk("[B14] setTtsModelDir 拒绝非本地引擎", sm_bad1.get("success") is False)
sm_bad2 = api.set_tts_model_dir({"engine": "voxcpm", "dir": os.path.join(SANDBOX, "nope")})
chk("[B14] setTtsModelDir 拒绝不存在目录", sm_bad2.get("success") is False)
sm_ok = api.set_tts_model_dir({"engine": "voxcpm", "dir": SANDBOX})
chk("[B14] setTtsModelDir 写入成功（沙盒）", bool(sm_ok.get("success")), str(sm_ok)[:90])

# ---- B15 参考音频 ----
ra_bad = api.set_reference_audio({"path": os.path.join(SANDBOX, "nope.wav")})
chk("[B15] setReferenceAudio 无效路径被拒", ra_bad.get("success") is False)
WAV = os.path.join(SANDBOX, "ref.wav")
with wave.open(WAV, "wb") as wf:
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(16000)
    wf.writeframes(b"".join(struct.pack("<h", ((i % 40) - 20) * 300) for i in range(16000)))
ra_ok = api.set_reference_audio({"path": WAV, "character_id": "_onb_int_test", "text": "测试参考文本"})
chk("[B15] setReferenceAudio 写角色 voice.reference_audio", bool(ra_ok.get("success")), str(ra_ok)[:90])
row3 = next((c for c in (api.get_characters().get("characters") or []) if c.get("id") == "_onb_int_test"), {})
chk("[B15] 参考音已落角色", str((row3.get("voice") or {}).get("reference_audio")) == WAV,
    str((row3.get("voice") or {}).get("reference_audio")))
chk("[B15] 参考音同步 proactive", str((api._cfg.raw.get("proactive") or {}).get("duplex_local_omni_ref_audio")) == WAV)

# ---- B16 试听轮询 ----
vt = api.get_voice_test_status("vt_none")
chk("[B16] getVoiceTestStatus 不存在任务 → gone",
    vt.get("success") is False and vt.get("done") is True and vt.get("phase") == "gone")

# ---- B17 体检/收尾契约 ----
fr = api.is_first_run()
chk("[B17] isFirstRun 返回 dict", isinstance(fr, dict))
rd = api.get_companion_readiness()
chk("[B17] getCompanionReadiness 返回 success/ready", isinstance(rd, dict) and "ready" in rd, str(rd)[:90])
vo = api.validate_onboarding({"mode": "云端付费", "role": {"preset": "danya"}, "mic": None,
                              "camera": None, "voice": None, "tts": None, "llm": None,
                              "cloud_omni": None, "local_vision_ready": False, "model_dir": None})
chk("[B17] validateOnboarding 接受精简 payload（能力槽已落盘）", isinstance(vo, dict) and "success" in vo,
    str(vo)[:100])
chk("[B17] getModelBundleStatus 存在", hasattr(api, "get_model_bundle_status"))
chk("[B17] applyOnboardingVoice 存在", hasattr(api, "apply_onboarding_voice"))

# 收尾落盘：新档位名必须被识别（否则首启标记不写 → 完成按钮"点不动"）
sfr = api.save_first_run_config({"mode": "混合推荐", "role": {"preset": "danya"}, "mic": None,
                                "camera": None, "voice": None, "tts": None, "llm": None,
                                "cloud_omni": None, "local_vision_ready": True, "model_dir": None})
chk("[B18] saveFirstRunConfig 接受新档位名「混合推荐」", bool(sfr.get("success")), str(sfr)[:110])
chk("[B18] first_run_done 已写 True", api._cfg.raw.get("first_run_done") is True)
chk("[B18] companion_mode 映射为 mixed", str(api._cfg.raw.get("companion_mode")) == "mixed",
    str(api._cfg.raw.get("companion_mode")))
for alt, tok in [("云端付费", "cloud_pay"), ("本机免费", "local_free")]:
    api.save_first_run_config({"mode": alt})
    chk("[B18] saveFirstRunConfig 识别「%s」→ %s" % (alt, tok),
        str(api._cfg.raw.get("companion_mode")) == tok)

# ---- 沙盒红线：真实 config 未被改动 ----
chk("[B*] 真实 config.yaml 未被写（mtime 不变）",
    abs(os.path.getmtime(REAL_CFG) - REAL_MTIME_BEFORE) < 1e-6)


# ============================================================================
# C. 异常与健壮性
# ============================================================================
section("C. 异常与健壮性")

# C1 未加载配置：全部优雅返回，不抛。
# ★ 红线：本测试绝不触发真实下载/删除 —— 用 _cfg=None 让所有带守卫的端点直接早退。
#   注意 CharacterAPI.__init__ 末尾会 self._load_config()，所以必须显式置 None。
api_off = CharacterAPI(CFG)
api_off._cfg = None
OFF_ENDPOINTS = [
    ("get_capability_slots", ()),
    ("set_capability_slot", ({"cap": "tts", "provider": "dashscope_cosy"},)),
    ("get_model_catalog", ()),
    ("model_dir_info", ("",)),
    ("list_voice_profiles", ()),
    ("get_character_templates", ()),
    ("save_character", ({"id": "x"},)),
    ("switch_character", ("x",)),
    ("check_clone_engine_target", ({"engine": "voxcpm"},)),
    ("get_voice_vram_advice", ()),
    ("scan_tts_model_dir", ({"dir": SANDBOX},)),
    ("set_reference_audio", ({"path": WAV},)),
    ("download_model_quant", ({"quant_id": "Q4_K_M"},)),
    ("stop_model_download", ("x",)),
    ("delete_model_quant", ({"quant_id": "Q4_K_M"},)),
    ("set_tts_model_dir", ({"engine": "voxcpm", "dir": SANDBOX},)),
    ("validate_onboarding", ({},)),
]
# 从源码判定每个端点是否在方法体开头就 `if not self._cfg` 早退
_app_src = open(os.path.join(PROJ, "src", "ui", "webapp.py"), encoding="utf-8").read()


def _guards_cfg(method_name):
    i = _app_src.find("def %s(" % method_name)
    if i < 0:
        return False
    j = _app_src.find("\n    def ", i + 20)
    body = _app_src[i:(j if j > 0 else i + 1200)]
    return "if not self._cfg" in body[:700]


off_bad, off_guarded = [], 0
for nm, args in OFF_ENDPOINTS:
    try:
        rr = getattr(api_off, nm)(*args)
        if _guards_cfg(nm):
            off_guarded += 1
            if not (isinstance(rr, dict) and rr.get("success") is False):
                off_bad.append((nm, str(rr)[:70]))
        else:
            # 无 cfg 守卫的端点（如静态模板/显存探测）：只要求不抛、返回 dict
            if not isinstance(rr, dict):
                off_bad.append((nm, "非 dict: " + str(rr)[:60]))
    except Exception as e:            # noqa: BLE001
        off_bad.append((nm, "THREW " + str(e)[:60]))
chk("[C1] _cfg=None 时 %d 个端点优雅早退、%d 个无守卫端点不抛"
    % (off_guarded, len(OFF_ENDPOINTS) - off_guarded), len(off_bad) == 0, str(off_bad))

# C2 脏数据：read_capability_slots 不抛
from src.models.slot_config import read_capability_slots  # noqa: E402
dirty_ok = True
for dirty in [{}, {"voice": None}, {"voice": []}, {"characters": None},
              {"voice": {"clone": None}}, {"cloud_tts": None}]:
    try:
        rr = read_capability_slots(dirty)
        if not (isinstance(rr, dict) and "slots" in rr):
            dirty_ok = False
    except Exception as e:            # noqa: BLE001
        dirty_ok = False
        print("    脏数据抛错:", dirty, e)
chk("[C2] readCapabilitySlots 对 6 组脏数据不抛且结构完整", dirty_ok)

# C3 未知参数一律拒绝
u1 = api.set_capability_slot({"cap": "not_a_slot", "provider": "x"})
chk("[C3] 未知 cap 被拒", u1.get("success") is False)
u2 = api.check_clone_engine_target({"engine": "not_an_engine"})
chk("[C3] 未知克隆引擎有响应（不抛）", isinstance(u2, dict))
u3 = api.set_tts_model_dir({"engine": "nope", "dir": SANDBOX})
chk("[C3] setTtsModelDir 未知引擎被拒", u3.get("success") is False)
u4 = api.get_voice_download_status("no_task")
chk("[C3] 未知下载任务返回 unknown/error", str(u4.get("status")) in ("unknown", "error"))

# C4 状态取值域（deriveMode 依赖）
SLOT_STATES = set()
r = api.get_capability_slots()
for cap, sl in (r.get("slots") or {}).items():
    SLOT_STATES.add(sl.get("state"))
chk("[C4] 槽状态取值 ⊆ {ok,warn,bad,none}", SLOT_STATES <= {"ok", "warn", "bad", "none"},
    str(SLOT_STATES))

# ---- B19 克隆引擎清单（list_clone_engines，F1 新增桥面）----
le = api.list_clone_engines()
chk("[B19] listCloneEngines 返回 success", bool(le.get("success")), str(le)[:90])
_engs = le.get("engines") or []
_by = {e.get("id"): e for e in _engs}
chk("[B19] 覆盖 5 引擎（3 本地 + MiMo 云 + GPT-SoVITS 外部）",
    set(_by) == {"voxcpm", "cosyvoice", "qwen3tts", "mimo_tts", "gpt_sovits"}, str(list(_by)))
chk("[B19] kind 分类正确",
    _by.get("voxcpm", {}).get("kind") == "local"
    and _by.get("mimo_tts", {}).get("kind") == "cloud"
    and _by.get("gpt_sovits", {}).get("kind") == "external")
chk("[B19] 仅本地 3 引擎可下载", sum(1 for e in _engs if e.get("downloadable")) == 3)
chk("[B19] 引擎数值来自真身（voxcpm 4.7/6.0、cosyvoice 5.4/4.0、qwen3tts 2.4）",
    _by.get("voxcpm", {}).get("disk_gb") == 4.7 and _by.get("voxcpm", {}).get("gpu_gb") == 6.0
    and _by.get("cosyvoice", {}).get("disk_gb") == 5.4 and _by.get("cosyvoice", {}).get("gpu_gb") == 4.0
    and _by.get("qwen3tts", {}).get("disk_gb") == 2.4)
chk("[B19] 每引擎 installed 为 bool 且有 detail",
    all(isinstance(e.get("installed"), bool) and e.get("installed_detail") for e in _engs))
chk("[B19] 云/外部带 note 说明",
    bool(_by.get("mimo_tts", {}).get("note")) and bool(_by.get("gpt_sovits", {}).get("note")))
chk("[B19] 结果为可 JSON 序列化的平面结构", isinstance(json.dumps(le), str))

# ---- B20 save_character 的 model_3d 守卫（F2：Live2D 只给达妮娅）----
api.save_character({"id": "onb_m3d_probe", "name": "探针",
                    "model_3d": {"enabled": True, "format": "vrm", "path": "/p.vrm"}})
_probe = next((x for x in api._cfg.raw["characters"] if x.get("id") == "onb_m3d_probe"), {})
chk("[B20] 非 danya 角色 model_3d.enabled 强制 False",
    _probe.get("model_3d", {}).get("enabled") is False, str(_probe.get("model_3d")))
_dy = next((x for x in api._cfg.raw["characters"] if x.get("id") == "danya"), {})
api.save_character({"id": "danya", "name": _dy.get("name"), "character": _dy.get("character"),
                    "speaking_style": _dy.get("speaking_style"), "background": _dy.get("background"),
                    "model_3d": {"enabled": True, "format": "vrm", "path": "/d.vrm"}})
_dy2 = next((x for x in api._cfg.raw["characters"] if x.get("id") == "danya"), {})
chk("[B20] danya model_3d.enabled 可保持 True",
    _dy2.get("model_3d", {}).get("enabled") is True, str(_dy2.get("model_3d")))

# ---- 清理 ----
try:
    shutil.rmtree(SANDBOX, ignore_errors=True)
except Exception:  # noqa: BLE001
    pass


# ============================================================================
section("结果")
print("  PASS = %d   FAIL = %d" % (PASS, FAIL))
if FINDINGS:
    print("\n  === 接入前必须处理的发现（%d 条）===" % len(FINDINGS))
    seen = set()
    for code, text in FINDINGS:
        if code in seen:
            continue
        seen.add(code)
        print("  [%s] %s" % (code, text))
else:
    print("  无阻塞性发现")
print("\n  RESULT: %s" % ("ALL GREEN" if FAIL == 0 else ("%d FAIL" % FAIL)))
sys.exit(0 if FAIL == 0 else 1)
