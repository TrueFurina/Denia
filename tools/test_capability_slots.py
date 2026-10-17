"""能力槽中央配置模型 + 生效同步 单测（2026-10-10 改版：chat 合并 + 阿里云一键）。

验证点：
  1) read_capability_slots 空配置 → 4 槽（aliyun/chat/tts/duplex）皆 none，推荐商家 configured=False。
  2) set chat（deepseek-flash，带视觉）→ chat=ok，且**模型被镜像进 vision_models**（看屏管线读得到）。
  3) set chat（纯文本模型）→ vision 不写（沿用原模型），message 提示不支持看图。
  4) set tts → 落 cloud_tts 分桶 + voice.clone.sub=cloud。
  5) set duplex local_comni → proactive.duplex_mode=local_omni。
  6) set aliyun 一键 → 同时写 chat + tts + duplex，parts 全 True。
  7) set chat 自定义 → text_chat.providers 出现 custom_chat。
  8) 双工规划中商家（volcano）写入被拒（UI 置灰的兜底）。
  9) 空配置读 → order 为 [aliyun,chat,tts,duplex]。

不依赖 giant Backend / 不联网。运行：python tools/test_capability_slots.py
"""
from __future__ import annotations
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)  # 项目根：src 是顶层包（与软件运行上下文一致）

from src.models.slot_config import read_capability_slots, set_capability_slot

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  \u2713 {name}")
    else:
        FAIL += 1
        print(f"  \u2717 {name}")


print("== 1) 空配置读 ==")
raw = {}
res = read_capability_slots(raw)
for cap in ("aliyun", "chat", "tts", "duplex"):
    s = res["slots"][cap]
    check(f"{cap} state=none", s["state"] == "none")
    check(f"{cap} 推荐商家都有且 configured=False",
          all((not p["configured"]) for p in s["providers"]))
check("health 四槽齐全", set(res["health"].keys()) == {"aliyun", "chat", "tts", "duplex"})
check("order = [aliyun,chat,tts,duplex]", res["order"] == ["aliyun", "chat", "tts", "duplex"])

print("== 2) set chat 带视觉模型 → 镜像进 vision_models ==")
raw = {}
r = set_capability_slot(raw, "chat", {"provider": "deepseek", "model": "deepseek-flash",
                                      "api_key": "sk-test123456",
                                      "base_url": "https://api.deepseek.com/v1"})
check("set chat ok", r["ok"] is True)
check("vision_mirrored=True", r.get("vision_mirrored") is True)
vm = raw.get("vision_models") or {}
vis_models = [m for m in (vm.get("models") or []) if isinstance(m, dict)]
check("vision_models 出现该模型",
      any(m.get("model") == "deepseek-flash" for m in vis_models))
check("vision_models.active 指向它", bool(vm.get("active")))
res = read_capability_slots(raw)
check("chat state=ok", res["slots"]["chat"]["state"] == "ok")

print("== 3) set chat 纯文本模型 → 不写 vision ==")
raw = {}
r = set_capability_slot(raw, "chat", {"provider": "deepseek", "model": "deepseek-v4-pro",
                                      "api_key": "sk-test123456",
                                      "base_url": "https://api.deepseek.com/v1"})
check("set chat ok", r["ok"] is True)
check("vision_mirrored=False", r.get("vision_mirrored") is False)
check("message 提示不支持看图", "不支持看图" in (r.get("message") or ""))
check("vision_models 未被创建", not raw.get("vision_models"))

print("== 4) set tts ==")
# 预置"全局静音"，验证配了 TTS 会自动解除（否则就是"配了音色却没声"的经典坑）
raw = {"voice": {"muted": True, "mode": "mute"}}
r = set_capability_slot(raw, "tts", {"provider": "dashscope_cosy",
                                     "model": "cosyvoice-v3.5-plus", "api_key": "sk-tts"})
check("set tts ok", r["ok"] is True)
res = read_capability_slots(raw)
check("set 后 tts ok", res["slots"]["tts"]["state"] == "ok")
check("voice.clone.sub=cloud", raw["voice"]["clone"]["sub"] == "cloud")
_ttsbucket = (raw.get("voice", {}).get("clone", {}).get("cloud", {}) or {}).get("dashscope_cosy", {})
check("TTS 分桶 model_name 正确落盘（不是丢失）",
      _ttsbucket.get("model_name") == "cosyvoice-v3.5-plus")
check("TTS 分桶 api_key 是字符串（非 dict）",
      isinstance(_ttsbucket.get("api_key"), str) and _ttsbucket.get("api_key") == "sk-tts")
check("配 TTS 后自动解除静音", raw.get("voice", {}).get("muted") is False)

print("== 5) set duplex local_comni ==")
raw = {}
r = set_capability_slot(raw, "duplex", {"provider": "local_comni"})
check("set duplex local ok", r["ok"] is True)
check("proactive.duplex_mode=local_omni", raw["proactive"]["duplex_mode"] == "local_omni")

print("== 6) set aliyun 一键（一个 Key 三槽全配）＝=")
raw = {}
r = set_capability_slot(raw, "aliyun", {"api_key": "sk-bailian-xyz"})
check("set aliyun ok", r["ok"] is True)
check("parts.chat True", r["parts"]["chat"] is True)
check("parts.tts True", r["parts"]["tts"] is True)
check("parts.duplex True", r["parts"]["duplex"] is True)
res = read_capability_slots(raw)
check("aliyun state=ok", res["slots"]["aliyun"]["state"] == "ok")
check("aliyun state_text=三槽全通", res["slots"]["aliyun"]["state_text"] == "三槽全通")
check("下游 chat 也 ok", res["slots"]["chat"]["state"] == "ok")
check("下游 tts 也 ok", res["slots"]["tts"]["state"] == "ok")
check("下游 duplex 也 ok", res["slots"]["duplex"]["state"] == "ok")
check("cloud_omni 有 model", bool((raw.get("cloud_omni") or {}).get("model")))

print("== 7) set chat 自定义 ==")
raw = {}
r = set_capability_slot(raw, "chat", {"is_custom": True, "custom_url": "https://my-llm/v1",
                                      "custom_model": "my-model", "custom_key": "sk-x"})
check("set chat custom ok", r["ok"] is True)
provs = raw["text_chat"]["providers"]
cust = next((p for p in provs if p["id"] == "custom_chat"), None)
check("custom_chat 端口存在", cust is not None)
check("custom 模型名写入", cust and cust["model"] == "my-model")

print("== 8) 双工规划中商家被拒 ==")
raw = {}
r = set_capability_slot(raw, "duplex", {"provider": "volcano", "api_key": "x"})
check("volcano 被拒", r["ok"] is False)

print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
