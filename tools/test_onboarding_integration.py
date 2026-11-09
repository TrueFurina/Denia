# -*- coding: utf-8 -*-
"""引导页整体重构 —— 接入可行性与集成验证。

范围：**首启实际链路** + 回滚链路 + 每屏依赖的桥面/接口。
  【2026-11-10 更正·上面原来写的是"5 步全链路"，那已经不是现状了】
  2026-10-20 产品把首启简化成两屏，`screenSlots/screenVisionDl/screenMic` 全部退出流程
  （DOM 与 JS 一行没删，收进隐藏池可回滚）。现在真的会跑的是：

  首启实际 = screenRole（建角色，含声音）→ screenSummary（体检）
             建角色那屏挂 src/ui/web/onboarding_role.js
  回滚链路 = MODE_SEQ（本机免费 / 云端付费 / 混合推荐）+ buildFlow()
             —— **当前零调用者**，只作回滚备用；A4·回滚 那几条只验它没烂掉
  收尾      = doFinish() → validate_onboarding / save_first_run_config

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
    """记一条「已知但还没处理」的发现（不判红）。

    【2026-10-21 · 这个机制会腐烂，慎用】本文件原有三条发现 F1/F2/F3，实测**全部已经
    落地了**，却一直挂在报告里：
      · F1「缺 kind/repo/note，需新增克隆引擎清单接口」→ `list_clone_engines()` 早就有；
      · F2「后端不守非达妮娅 model_3d」→ `save_character` 早就兜底强制 False；
      · F3「空 api_key 会擦掉已存 Key」→ `merge_cloud_tts_provider` 早就改成"空值不改"。
    发现栏不红、也不会自己消失，于是它从"待办"变成了"背景噪音"——与"红着的门禁会被
    训练成噪音"同一个毛病，只是它连红都不红，更隐蔽。
    所以：**新加发现必须同时写明「复核日期」和「什么条件下该删掉它」**，并且尽量直接
    写成断言（能守的行为就别只记一条愿望）。
    """
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
    # 【2026-10-21 移出·它不在仓库里】原来这里还有一条
    #   "建角色 v8 设计稿": docs/mockups/引导页建角色_设计稿_v8_20261012.html
    # 而 `git log --all -- "docs/mockups/*"` **零命中**、.gitignore 也没忽略它 ——
    # 说明它是开发机上的本地过程稿，从没进过库。于是 A1「文件存在」在任何干净克隆上
    # 都必然红，等于**拿仓库里没有的东西当判据**。
    # 【2026-11-10 追加·「移出后 A8 会自动跳过」是句自欺】当时把 A8 写成 `if r8:`，
    # 理由写的是"存在才验、不存在自动跳过"。但 r8 从此**恒为 None**，A8 那两条断言
    # 就永久消失了 —— 不红、不报、不留痕。本文件开头的 finding() 文档早就写过这个病：
    # 「不红、也不会自己消失」；这里更糟，它连"发现"都不算，是**静默蒸发**。
    # 现在 A8 已经改判真身 `src/ui/web/onboarding_role.js`（设计稿落地后的实现），
    # 不再依赖这张表。
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

# 【2026-11-10 补·上面那三条可能是空转】`missing` 是"从 s 里正则抓到的调用"减去
# "桥面里有的名字"。如果正则**一个都没抓到**（比如前端改成 `Backend["x"]()`、
# 或者 `bk()` 包了一层），missing 就是空集 → 三条「全部存在」全部 PASS。
# 「零个东西全部满足条件」是真空真。补一条下限，让"没解析出来"变成红，而不是绿。
chk("[A1] 每个前端文件都真的解析出桥调用（否则上面的「全部存在」是空转）",
    bool(file_calls) and all(len(v[2]) >= 3 for v in file_calls.values()),
    str({k: len(v[2]) for k, v in file_calls.items()}))

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

# 引导页结构：**首启实际链路** + 回滚备用表
if fl:
    _, s, _ = fl
    # 【2026-10-21 修·判据盯着已不存在的形态】原来断言两件事，现在两件都不成立了：
    #   ① `let FLOW = [screenSlots]` —— 早改成 `let FLOW = []` + buildFlow() 动态拼
    #   ② 字面量 `'screenVisionDl','screenMic','screenRole','screenSummary'` 连续出现
    #      —— MODE_SEQ 里中间插了 screenVoice（2026-09-25 决定：本地双工的出声也是
    #      TTS 合成，不绑 TTS 就没声音），这个连续串永远不会再出现。
    # 判据改成守**语义**（谁在哪条链路上），而不是某个具体的字面写法。
    #
    # 【2026-11-10 再修·上面那次"改成守语义"，守的是一段没人调用的代码】
    # 2026-10-20 产品把首启简化成「建角色 → 体检」，唯一的 `buildFlow(mode)` 调用点
    # 被删了（1759 行还留着注释），只剩 MODE_SEQ 这张表当回滚备用。实测：
    #   · `buildFlow` 全仓**零调用**（grep 只剩定义 + 两处注释）；
    #   · 真正跑的首启链路是写死的两屏：
    #       let FLOW = [];  ['screenRole','screenSummary'].forEach(id=>{ ... FLOW.push(el) })
    #   · 而下面第四条断言叫「FLOW 由 buildFlow 动态拼（不再写死单条）」——**与现状正好相反**。
    # 后果：把 'screenRole' 从实际链路里删掉，本门**照样全绿**（已用探针验证）。
    # 改法：① 首启实际链路直接验那段字面量（这才是用户真的会看到的流程）；
    #       ② MODE_SEQ/buildFlow 如实降级成"回滚备用路径完好"，断言名字里就写明是回滚。
    m_init = re.search(r"let FLOW = \[\];\s*\n\s*\[([^\]]*)\]\.forEach", s)
    init_ids = re.findall(r"'([A-Za-z][A-Za-z0-9_]*)'", m_init.group(1)) if m_init else []
    chk("[A4] 首启实际链路 == 建角色 → 体检（改这里等于改产品流程，必须同步改判据）",
        init_ids == ["screenRole", "screenSummary"], "实际=%s" % init_ids)
    chk("[A4] 实际链路引用的屏 id 都在 DOM 里（写错一个就是白屏）",
        bool(init_ids) and all(('id="%s"' % i) in s for i in init_ids),
        "缺=%s" % [i for i in init_ids if ('id="%s"' % i) not in s])
    m_seq = re.search(r"const MODE_SEQ = \{(.*?)\n  \};", s, re.S)
    seqs = re.findall(r"\[([^\]]*)\]", m_seq.group(1)) if m_seq else []
    chk("[A4·回滚] MODE_SEQ 仍有 >=2 条链路且每条都含「建角色 + 体检」",
        len(seqs) >= 2 and all("screenRole" in x and "screenSummary" in x for x in seqs),
        "实际 %d 条" % len(seqs))
    chk("[A4·回滚] 至少一条链路要下本地模型（screenVisionDl）",
        any("screenVisionDl" in x for x in seqs))
    chk("[A4·回滚] buildFlow 仍在（改回动态链路只改一行）",
        "function buildFlow(" in s and "FLOW = [screenMode];" in s)
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

# 建角色步的「接线指向适配器，不自建」
# 【2026-11-10 修·两条断言静默蒸发了】原来这里验的是 `docs/mockups/…v8_20261012.html`，
# 而那份设计稿从没进过库（见 FILES 注释），于是：
#     r8 = file_calls.get("建角色 v8 设计稿")   # ← 恒为 None
#     if r8: ...                               # ← 两条断言永久不执行
# 不红、不报、不留痕 —— 比"发现栏腐烂"更隐蔽：发现至少还会打印一行 ⚠。
# 设计稿落地后的**真身**是 `src/ui/web/onboarding_role.js`（首启 FLOW 里的 screenRole
# 就挂它），所以判据搬到真身上，验两件事：
#   ① 写 TTS 走能力槽适配器（cap:"tts" + setCapabilitySlot），不自己拼请求；
#   ② 厂商清单来自 TTS_PROVIDERS（真相源 slot_config.TTS_PROVIDERS），不自建厂商表。
# **先剥注释再断言** —— 这两串字面量在文件注释里都出现过（第 425、1352 行），
# 不剥注释的话注释自己就能满足判据（本仓踩过：`"backup=True" in txt` 被注释满足）。
_role_src = open(os.path.join(PROJ, "src", "ui", "web", "onboarding_role.js"),
                 encoding="utf-8").read()
_role_nc = re.sub(r"/\*.*?\*/", "", _role_src, flags=re.S)
_role_nc = re.sub(r"(?m)^\s*//.*$", "", _role_nc)
chk("[A8] 建角色步写 TTS 走能力槽适配器（cap:\"tts\" + setCapabilitySlot）",
    re.search(r"""cap\s*:\s*["']tts["']""", _role_nc) is not None
    and re.search(r"\.setCapabilitySlot\s*\(", _role_nc) is not None)
chk("[A8] 建角色步的厂商清单来自 TTS_PROVIDERS（不自建厂商表）",
    re.search(r"TTS_PROVIDERS\s*=\s*\[", _role_nc) is not None)

# SHELL_GUARD 对 iframe 放行
# 【2026-10-21 修·代码搬家没同步搬判据】`_SHELL_GUARD` 已随 UI 拆分搬进
# `src/ui/main_window.py`（webapp.py 里现在一个字都没有），判据还在 webapp.py 里
# 找 → 恒红。同型错误：`/local_img` 搬家那次（test_character_card_images）。
# 改成**多文件拼接**：将来再搬一次也不会假红，而且它本来就该跟着"外壳注入"走。
_ui_srcs = []
for _rel in ("src/ui/webapp.py", "src/ui/main_window.py"):
    _f = os.path.join(PROJ, *_rel.split("/"))
    if os.path.exists(_f):
        _ui_srcs.append(open(_f, encoding="utf-8").read())
_app = "\n".join(_ui_srcs)
chk("[A9] _SHELL_GUARD 对 iframe 放行（window.top!==window 直接 return）",
    "window.top!==window" in _app or "window.top !== window" in _app)


# ============================================================================
# B. 真后端契约（沙盒）
# ============================================================================
section("B. 真后端契约（config 副本沙盒）")

from src.ui.webapp import CharacterAPI          # noqa: E402
from src.config import load_config              # noqa: E402

SANDBOX = tempfile.mkdtemp(prefix="onb_int_")
# 【2026-10-21 修·该读出厂模板】原实现优先 copy config.yaml —— 那是**开发机实配**
# （被 gitignore、含密钥、带本机绝对路径）。引导页要验的恰恰是"新用户装完是什么样"，
# 拿开发机实配当输入等于在验"我这台机器现在什么样"，而且队友/CI 上根本没有那个文件。
# 与 `test_config_wiring` 那次同一个坑（读实配 → 验不出出厂状态）。改成优先出厂模板，
# 只有模板缺失时才退回实配（保住开发机上仍可跑）。
_CFG_SRC = os.path.join(PROJ, "config.release.yaml")
if not os.path.exists(_CFG_SRC):
    _CFG_SRC = os.path.join(PROJ, "config.yaml")
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

# 【2026-10-21 修·这条"发现"是假警】原来直接拿出厂模板里现有的 Key 来比：
#   key_before = _tts_key(...)   # 出厂模板里**没有** Key → 空
#   ... 传空 api_key ...
#   if key_after == key_before and key_before:   # ← 恒 False（key_before 是空）
#       PASS
#   else: finding("F3", "会把已存 Key 擦成空字符串")   # ← 于是**永远**记一条发现
# 二态判据把"没有 Key 可擦"压成了"Key 被擦掉了"。实测（手动探针）后端**不擦**：
# 先写入 sk-fake-1234567890，再传 api_key="" → Key 原样还在，契约成立。
# 改成先造一个 Key 再验 —— 这样"能不能擦"真的被验到了，而且不再需要发现栏。
api.set_capability_slot({"cap": "tts", "provider": "dashscope_cosy",
                         "model": "qwen-audio-3.0-tts-plus",
                         "api_key": "sk-onb-integration-probe"})
key_before = _tts_key(api._cfg.raw)
chk("[B4] 预置 Key 成功（否则下一条无从验证）", key_before == "sk-onb-integration-probe",
    key_before[:20])
api.set_capability_slot({"cap": "tts", "provider": "dashscope_cosy",
                         "model": "cosyvoice-v3.5-plus", "api_key": ""})
key_after = _tts_key(api._cfg.raw)
chk("[B4] 空 api_key 不擦除已存 Key（契约成立）", key_after == key_before and bool(key_before),
    "before=%r after=%r" % (key_before[:16], key_after[:16]))

# ---- B5/B6 第 2 步：下模型 ----
cat = api.get_model_catalog()
chk("[B5] getModelCatalog 成功", bool(cat.get("success")))
# 【2026-10-21 修·判据依赖本机显存】原来无条件断言 `len(quants) >= 4`。
# 但 model_catalog 在显存 <12GB 时返回 cloud_only=True + quants=[]（本地全双工模型
# 跑不动，如实告诉你选云端）——那是**正确行为**，不是缺数据。这台机器就是 8GB
# （实测 vram_gb=8.0 / cloud_only=True / warning="检测到显存约 8.0GB（<12GB）…"），
# 所以这条在任何低显存机器上都必然红。判据改成按 cloud_only 分两路：
#   · cloud_only → quants 可以为空，但**必须给出 warning**（不能静默空着）
#   · 否则       → 必须 >= 4 档
_cat_quants = cat.get("quants") or []
if cat.get("cloud_only"):
    chk("[B5] 显存不足 → cloud_only 且给出 warning（不静默空着）",
        bool(cat.get("warning")), "vram=%.1f" % float(cat.get("vram_gb") or 0))
else:
    chk("[B5] 含 quants 列表", isinstance(cat.get("quants"), list) and len(_cat_quants) >= 4,
        "n=%d" % len(_cat_quants))
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
# 【2026-11-10 修·断言名承诺了"返回失败"，条件只查了"是不是 dict"】
# 原来是 `chk("…未装档返回失败但不抛", isinstance(dlq, dict))` —— 名字里写着「返回失败」，
# 判据里一个失败都没验。探针实测：把 `delete_model_quant` 的"文件不存在"分支改成
# `return {"success": True}`，本门**照样全绿**。
# 生产实现（model_catalog.py）这条分支返回
#   {"success": False, "error": "该档位文件不存在（可能未下载或已删除）"}
# 所以两件事都验：success 必须是 False，error 必须说清是"不存在"（不是别的失败）。
chk("[B6] deleteModelQuant 未装档返回失败但不抛",
    isinstance(dlq, dict) and dlq.get("success") is False and "不存在" in str(dlq.get("error")),
    str(dlq)[:100])
dlq_bad = api.delete_model_quant({"quant_id": "NO_SUCH_QUANT", "model_dir": SANDBOX})
chk("[B6] deleteModelQuant 未知量化档被拒（不误删）",
    dlq_bad.get("success") is False and "量化档" in str(dlq_bad.get("error")),
    str(dlq_bad)[:100])

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

# model_3d 守卫：**后端已经守了**（`save_character` 里 `if char_id != "danya": enabled=False`）。
# 【2026-10-21 修·这条"发现"是过时的】原来这里记一条 F2「后端不守卫非达妮娅 model_3d ——
# 引导页建角色必须自行施加」。实测：传 enabled:true 存回来是 False，后端**有**兜底。
# 那条注释多半写在兜底之前，之后没人回头删——发现栏不红也不会消失，只会变成背景噪音。
# 现在改成两条真断言：①后端兜底还在（谁删了会红）②引导页那行显式声明也还在
# （双保险，别指望单靠后端）。
# 注意**先剥注释再断言** —— 否则我自己在注释里复述一遍那串字面量就能满足判据
# （本仓踩过：`"backup=True" in txt` 被注释满足）。
m3 = api.save_character({"id": "_onb_int_test", "name": "集成测试角色2",
                         "model_3d": {"enabled": True, "format": "live2d"}})
row2 = next((c for c in (api.get_characters().get("characters") or []) if c.get("id") == "_onb_int_test"), {})
chk("[B10] 后端兜底：非达妮娅传 enabled:true 也存成 false",
    (row2.get("model_3d") or {}).get("enabled") is False,
    "enabled=%r" % (row2.get("model_3d") or {}).get("enabled"))
_role_js_path = os.path.join(PROJ, "src", "ui", "web", "onboarding_role.js")
_role_js = open(_role_js_path, encoding="utf-8").read()
_role_js_nc = re.sub(r"/\*.*?\*/", "", _role_js, flags=re.S)
_role_js_nc = re.sub(r"(?m)^\s*//.*$", "", _role_js_nc)
chk("[B10] 引导页建角色 payload 也显式声明 model_3d.enabled=false（双保险）",
    re.search(r"model_3d\s*:\s*\{\s*enabled\s*:\s*false\s*\}", _role_js_nc) is not None)

sw = api.switch_character("_onb_int_test")
chk("[B11] switchCharacter 激活成功", bool(sw.get("success")), str(sw)[:100])
chk("[B11] active 已切换", str(api._cfg.active_character_id) == "_onb_int_test",
    str(api._cfg.active_character_id))
sw_bad = api.switch_character("no_such_char_zzz")
chk("[B11] switchCharacter 不存在角色被拒", sw_bad.get("success") is False)

# 【2026-11-10 补·运行时分支一条判据都没有】上面三条只走了「无运行时 → 配置级回退」
# 那条路：`self._dialogue` 是 None，`_run_coro` 立刻返回 None，于是
#     if result is False:  return {"success": False, "error": "角色不存在"}
# 这段**一次都没执行过**。探针实测：把这段的拒绝改成 `return {"success": True}`，
# 本门照样全绿 —— 因为桩根本没进那个分支。
# 这里造一个最小运行时（真事件循环 + 桩 dialogue），让那条分支真的跑一次。
import asyncio as _aio            # noqa: E402
import threading as _thr          # noqa: E402


class _StubDialogue:
    """只实现 switch_character；verdict=False 用来模拟"运行时说角色不存在"。"""

    def __init__(self, verdict):
        self.verdict = verdict
        self.calls = []

    async def switch_character(self, cid):
        self.calls.append(cid)
        return self.verdict


_rt_loop = _aio.new_event_loop()
_rt_thr = _thr.Thread(target=_rt_loop.run_forever, daemon=True)
_rt_thr.start()
_stub = _StubDialogue(False)
sw_rt = {}
try:
    api.set_runtime(dialogue=_stub, loop=_rt_loop)
    sw_rt = api.switch_character("no_such_char_zzz")
finally:
    api.set_runtime()
    _rt_loop.call_soon_threadsafe(_rt_loop.stop)
    _rt_thr.join(timeout=3)
chk("[B11] 运行时桥返回 False → 拒绝（这段之前一条判据都没有）",
    isinstance(sw_rt, dict) and sw_rt.get("success") is False
    and "不存在" in str(sw_rt.get("error")), str(sw_rt)[:100])
chk("[B11] 桩确实被问到过一次（证明走的是运行时分支，不是回退路径蒙对）",
    _stub.calls == ["no_such_char_zzz"], str(_stub.calls))

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
        # 【2026-10-21 撤回 F1】这里原来记一条"发现：缺 kind/repo/note，落地需新增
        # 「克隆引擎清单」接口"。那个接口**早就有了** —— `list_clone_engines()`
        # （下方 B19 在逐项验）。也就是说 F1 从落地那天起就是过时的，但它以「⚠ 发现」
        # 的形式一直挂着：不红、也不会消失，只会训练人忽略「发现」那一栏。
        # 与"红着的门禁变成噪音"是同一个毛病，只是它连红都不红。
        # 改成守**职责划分**：显存建议只回答"跑不跑得动"（3 个本地引擎）；
        # 下载器清单负责"展示与下载"（5 个引擎，带 kind/repo/note）。
        _dl = api.list_clone_engines()
        chk("[B12] 下载器清单接口存在且覆盖 5 引擎（F1 已落地，细节见 B19）",
            bool(_dl.get("success")) and len(_dl.get("engines") or []) == 5,
            "n=%d" % len(_dl.get("engines") or []))
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
# 【2026-11-10 修·「接受」被写成了「有个 success 键就算」】
# 原来是 `isinstance(vo, dict) and "success" in vo` —— `{"success": False, "error": …}`
# 也满足。名字叫「接受精简 payload」，实际连"复核有没有跑完"都没验。
# 探针实测：把正常路径改成 `return {"success": False, "ok": False, "checks": checks}`，
# 本门照样全绿。生产契约是 {"success": True, "ok": …, "checks": [...]}，
# 而 ok 的定义是「没有任何一条 fail」——这两件都验。
chk("[B17] validateOnboarding 接受精简 payload 且复核跑完（success True + checks）",
    isinstance(vo, dict) and vo.get("success") is True
    and isinstance(vo.get("checks"), list) and len(vo["checks"]) >= 3,
    str(vo)[:140])
chk("[B17] 每条 check 都有 id/label/status（体检页按这三字段渲染）",
    isinstance(vo.get("checks"), list)
    and all(isinstance(c, dict) and {"id", "label", "status"} <= set(c) for c in vo["checks"]),
    str([c.get("id") for c in (vo.get("checks") or []) if isinstance(c, dict)]))
chk("[B17] ok 的定义自洽：没有任何一条 fail 才为 True",
    isinstance(vo.get("checks"), list)
    and vo.get("ok") == (not any(c.get("status") == "fail" for c in vo["checks"])),
    "ok=%r fails=%s" % (vo.get("ok"),
                        [c.get("id") for c in (vo.get("checks") or [])
                         if isinstance(c, dict) and c.get("status") == "fail"]))
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
# 【2026-11-10 修·判据是从被测代码里读出来的，于是"删守卫"= "删判据"】
# 原来这里是 `_guards_cfg(nm)`：去 webapp.py 里找 `if not self._cfg`，**用它决定**
# 这个端点该不该断言 `success is False`。等于让被测代码自己出考卷 ——
# 把守卫删掉，端点就被静默降级成"无守卫端点"，只剩一句"返回 dict 就行"。
# 探针实测：删掉 `get_model_catalog` 的 `if not self._cfg` 早退，本门**照样全绿**。
# 改法：契约**手写**在下面两张表里（哪 14 个必须守 cfg、哪 3 个本来就不需要），
# 行为断言只认这两张表；源码一致性另立一条断言 —— 删守卫会让它**红**，而不是让它消失。
CFG_GUARDED = [
    # 读写配置的端点：没有 cfg 就必须早退成 success=False，不能半路 AttributeError
    "get_capability_slots", "set_capability_slot", "get_model_catalog", "model_dir_info",
    "list_voice_profiles", "save_character", "switch_character", "check_clone_engine_target",
    "set_reference_audio", "download_model_quant", "stop_model_download", "delete_model_quant",
    "set_tts_model_dir", "validate_onboarding",
]
CFG_FREE = {
    # 静态/硬件类：本来就不需要 cfg，要求"不抛且返回 dict"即可
    "get_character_templates",   # 模板是常量表
    "get_voice_vram_advice",     # 探测本机显存
    "scan_tts_model_dir",        # 扫的是传进来的目录
}
_app_src = open(os.path.join(PROJ, "src", "ui", "webapp.py"), encoding="utf-8").read()


def _guards_cfg(method_name):
    """仅用于「手写契约 vs 源码」的一致性核对，**不再**用来决定断言口径。"""
    i = _app_src.find("def %s(" % method_name)
    if i < 0:
        return False
    j = _app_src.find("\n    def ", i + 20)
    body = _app_src[i:(j if j > 0 else i + 1200)]
    return "if not self._cfg" in body[:700]


off_bad = []
for nm, args in OFF_ENDPOINTS:
    try:
        rr = getattr(api_off, nm)(*args)
    except Exception as e:            # noqa: BLE001
        off_bad.append((nm, "THREW " + str(e)[:60]))
        continue
    if nm in CFG_GUARDED:
        if not (isinstance(rr, dict) and rr.get("success") is False):
            off_bad.append((nm, str(rr)[:70]))
    elif not isinstance(rr, dict):
        off_bad.append((nm, "非 dict: " + str(rr)[:60]))
chk("[C1] _cfg=None 时 %d 个必须守 cfg 的端点全部 success=False、%d 个 cfg 无关端点不抛"
    % (len(CFG_GUARDED), len(CFG_FREE)), len(off_bad) == 0, str(off_bad))
chk("[C1] 契约表恰好覆盖全部探针端点（新增端点必须显式归类，不许默认放行）",
    set(CFG_GUARDED) | CFG_FREE == {nm for nm, _ in OFF_ENDPOINTS}
    and not (set(CFG_GUARDED) & CFG_FREE),
    "表=%d 探针=%d 交集=%s" % (len(CFG_GUARDED) + len(CFG_FREE), len(OFF_ENDPOINTS),
                               sorted(set(CFG_GUARDED) & CFG_FREE)))
chk("[C1] 这 %d 个端点的源码里确实写着 `if not self._cfg` 早退（删了会红）"
    % len(CFG_GUARDED),
    [nm for nm in CFG_GUARDED if not _guards_cfg(nm)] == [],
    "缺守卫=%s" % [nm for nm in CFG_GUARDED if not _guards_cfg(nm)])

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
