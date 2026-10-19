"""通话链路冒烟测试（无 GUI / 无真实音频设备 / 不联网）。

覆盖两块「改错了用户直接感知、但日志里看不出来」的逻辑：

A. 音频设备列表收口（_finalize_device_list）
   - WDM-KS 整类剔除（Windows 声音面板不展示）
   - 空名端点 / "Speakers ()" 剔除
   - 31 字符截断重名丢弃（同前缀有更长名字时）
   - 同名多 host 合并为一条，id = 纯设备名，顺序保持首次出现

B. 陪伴特性 → 引擎配置（_features_to_engine_config）
   - 空清单 = 纯聊天（duplex_mode=False, runtime_on=False）
   - 本地双工 → local_omni + 量化档
   - 云端双工默认不开屏（screen_watch_enabled=False）
   - 解耦开关下「云端语音 + 本地看屏」可并存
   - vision_models.enabled 恒为 True（选云端档不能连带关掉发图能力）
   - 本地 TTS 优先于云端 TTS

运行：python src/ui/tests/test_call_smoke.py
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.ui.webapp import CharacterAPI  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name} {detail}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


# ---------------------------------------------------------------- A. 设备列表
def test_device_list() -> None:
    print("== A. 音频设备列表收口 ==")
    finalize = CharacterAPI._finalize_device_list

    rows = [
        {"index": 0, "name": "Speakers (Realtek(R) Audio)", "hostapi_name": "MME",
         "hostapi_index": 0, "default_samplerate": 48000},
        {"index": 5, "name": "Speakers (Realtek(R) Audio)", "hostapi_name": "Windows WASAPI",
         "hostapi_index": 2, "default_samplerate": 48000},
        {"index": 9, "name": "HDMI (NVIDIA High Definition Audio)", "hostapi_name": "Windows WDM-KS",
         "hostapi_index": 3, "default_samplerate": 48000},
        {"index": 10, "name": "Speakers ()", "hostapi_name": "MME",
         "hostapi_index": 0, "default_samplerate": 44100},
        {"index": 11, "name": "M27P6 (NVIDIA High Definition A", "hostapi_name": "MME",
         "hostapi_index": 0, "default_samplerate": 48000},
        {"index": 12, "name": "M27P6 (NVIDIA High Definition Audio)", "hostapi_name": "Windows WASAPI",
         "hostapi_index": 2, "default_samplerate": 48000},
    ]
    out = finalize(rows, "输出")

    names = [d["name"] for d in out]
    check("去重后只剩 2 条物理设备", len(out) == 2, f"got={names}")
    check("WDM-KS 被剔除", all("HDMI" not in n for n in names), f"got={names}")
    check("空名端点被剔除", all("()" not in n for n in names), f"got={names}")
    check("31 字符截断名被丢弃、保留完整名",
          "M27P6 (NVIDIA High Definition A" not in names and
          "M27P6 (NVIDIA High Definition Audio)" in names, f"got={names}")
    check("id 等于纯设备名（兼容老配置）",
          all(d["id"] == d["name"] for d in out))
    check("同名多 host 合并，hostapis 记录 2 条",
          next(d for d in out if d["name"].startswith("Speakers"))["hostapis"].__len__() == 2)
    check("顺序保持首次出现（Realtek 在前）", names[0].startswith("Speakers"), f"got={names}")


# ------------------------------------------------------- B. 特性 → 引擎配置
def _map(features: list, raw: dict | None = None) -> tuple[set, dict]:
    fake_self = SimpleNamespace(COMPANION_FEATURE_DEFS=CharacterAPI.COMPANION_FEATURE_DEFS)
    raw = {} if raw is None else raw
    ids = CharacterAPI._features_to_engine_config(fake_self, features, raw)
    return ids, raw


def test_feature_mapping() -> None:
    print("== B. 陪伴特性 → 引擎配置 ==")

    _, raw = _map([])
    check("空清单 → 纯聊天", raw["proactive"]["duplex_mode"] is False
          and raw["proactive"]["runtime_on"] is False)
    check("空清单也不会关掉看图能力", raw["vision_models"]["enabled"] is True)

    _, raw = _map([{"id": "local_duplex", "opts": {"quant": "Q4_K_M"}}])
    pro = raw["proactive"]
    check("本地双工 → local_omni", pro["duplex_mode"] == "local_omni")
    check("量化档写入", pro.get("duplex_local_omni_quant") == "Q4_K_M")
    check("本地双工开屏", raw["vision_models"]["screen_watch_enabled"] is True)

    _, raw = _map([{"id": "cloud_duplex"}])
    pro = raw["proactive"]
    check("云端双工 → realtime", pro["duplex_mode"] == "realtime")
    check("云端双工默认不开屏", raw["vision_models"]["screen_watch_enabled"] is False)
    check("云端双工仍可发图", raw["vision_models"]["enabled"] is True)

    # 关键回归：解耦开关下「云端语音 + 本地看屏」必须能并存
    _, raw = _map([{"id": "cloud_duplex"}, {"id": "local_vision"}],
                  {"proactive": {"decouple_screen_voice": True}})
    pro = raw["proactive"]
    check("解耦：语音轴仍是 realtime", pro["duplex_mode"] == "realtime")
    check("解耦：看屏不被语音轴覆盖", raw["vision_models"]["screen_watch_enabled"] is True)
    check("解耦：仍可发图", raw["vision_models"]["enabled"] is True)

    _, raw = _map([{"id": "local_vision"}])
    check("仅本地看屏 → duplex_mode=True（快照循环）", raw["proactive"]["duplex_mode"] is True)

    _, raw = _map([{"id": "cloud_snapshot"}])
    check("云端快照 → realtime + 快照开关",
          raw["proactive"]["duplex_mode"] == "realtime"
          and raw["proactive"]["duplex_snapshot_enabled"] is True)

    _, raw = _map([{"id": "local_qwen_tts"}, {"id": "cloud_tts"}])
    check("本地 TTS 优先于云端", raw["voice"]["clone"]["local_engine"] == "qwen3tts"
          and raw["voice"]["clone"]["sub"] == "local")

    _, raw = _map([{"id": "cloud_tts"}])
    check("仅云端 TTS → sub=cloud", raw["voice"]["clone"]["sub"] == "cloud")


def main() -> None:
    test_device_list()
    print()
    test_feature_mapping()
    print(f"\n总计: PASS={PASS} FAIL={FAIL}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
