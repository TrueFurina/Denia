# -*- coding: utf-8 -*-
"""拨号引擎解析回归测试（2026-10-09）

定位的症状
----------
用户反馈：「重进软件后，点击了切换模式（原本混合，重新点了一次混合），才成功接听」。
日志实锤（run_20261009_020420.log:940 / run_20261009_020956.log:260）：
    · 02:09:38 拨号  engine=local_omni   ← 没接通
    · 02:10:30 拨号  engine=realtime     ← 接通
两次之间唯一的差别是"用户在设置页又点了一次档位"。

根因（第一轮修复 e168af6）
--------------------------
`webapp.start_duplex_call` 用 `pro._duplex_voice_engine` 这个**内存属性**选引擎，
而全代码库只有 `save_call_settings`（设置页引擎卡）会写它 —— **启动时从未从 config
初始化**。于是每次重启后第一次拨号 getattr 恒为 ''，落到 else 兜底，与用户真正设的
引擎脱节；只有用户再点一次档位/引擎卡（顺带 saveCallSettings）才对齐。

第二轮策略变更（本文件的组 2/组 6）
----------------------------------
用户拍板：「拨号引擎应该走云端，没有云端就应该门控拦截」。
即：**只有用户显式选择本地双工才走本地**；空 / auto / off / 未知 / 档位反推值一律
走云端；云端没配 Key+模型时**如实拦下**（error_code=no_cloud_duplex_cfg），
绝不静默回落本地链路。

本测试调用**真实的** `CharacterAPI.start_duplex_call` / `restore_saved_config_on_startup`
（不是复刻一份逻辑的桩），用假 self/pro 把外部依赖顶掉，断言真正落地的 `target`。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.ui.webapp import CharacterAPI  # noqa: E402

FAILS = []
PASSES = []


def check(name, cond, detail=""):
    if cond:
        PASSES.append(name)
        print("  PASS  %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL  %s  %s" % (name, detail))


class _Cfg:
    def __init__(self, raw):
        self.raw = raw
        self.ns = None


class _Client:
    """假双工客户端：只提供 _connected，让"已连上直接置拨通"的兜底不触发。"""
    def __init__(self):
        self._connected = False
        self._mic_dev_name = ""
        self._mic_health = ""


class _Pro:
    """假主动策略：记录真正落地的拨号 target。"""

    def __init__(self, engine_attr=None, duplex_mode="local_omni", cloud_ok=True):
        if engine_attr is not None:
            self._duplex_voice_engine = engine_attr
            self._duplex_video_engine = engine_attr
        self._duplex_mode = duplex_mode
        self._cloud_ok = cloud_ok
        self.calls = []          # _switch_duplex_mode(target, msg, ...)
        self.tier_calls = []
        self._client = _Client()

    # --- 档位能力 ---
    def capabilities(self):
        return {"local_duplex", "cloud_snapshot", "long_memory"}

    def tier_diag(self):
        return {"caps": sorted(self.capabilities())}

    def _emit_tier_blocked(self, mode):
        pass

    def _cloud_omni_enabled(self):
        return self._cloud_ok

    def _active_duplex_client(self):
        return self._client

    def set_call_profile(self, call_mode, screen_share):
        pass

    def _switch_duplex_mode(self, target, *a, **kw):
        self.calls.append(target)

    def _mark_duplex_ready_from_cloud(self):
        pass

    def set_tier(self, token="", feature_ids=None):
        self.tier_calls.append((token, list(feature_ids or [])))

    def _sync_duplex_mode_from_cfg(self):
        pass

    def apply_cloud_duplex_config(self, d):
        pass


def _make_app(raw):
    """用 __new__ 造一个 CharacterAPI 实例，只挂被用到的属性（避开重构造）。"""
    app = CharacterAPI.__new__(CharacterAPI)
    app._cfg = _Cfg(raw)
    app._proactive = None
    app._cur_call_session_id = ""
    app._cur_call_session_start = 0.0
    return app


def _base_raw():
    return {
        "companion_mode": "mixed",
        "companion_features": [
            {"id": "local_duplex"}, {"id": "cloud_snapshot"}, {"id": "long_memory"},
        ],
        "cloud_omni": {"api_key": "sk-test", "model": "qwen3.5-omni-flash-realtime"},
        "proactive": {"duplex_mode": "local_omni"},
        "vision_models": {},
    }


def _dial(engine_attr, cfg_engine, duplex_mode, cloud_ok=True, cloud_cfg=True,
          cloud_tts=False):
    """拨号并返回 (真正落地的 target 列表, 原始返回值)。

    cloud_ok  : pro._cloud_omni_enabled() 的返回值（运行态侧）
    cloud_cfg : config.yaml 顶层 cloud_omni 是否带 api_key+model（配置侧）
    cloud_tts : 是否绑了任一云端 TTS（本地双工出声需要它，见 no_cloud_tts 门控）
    后端对云端可用性是**双通道**判定（运行态 OR 配置），造"真的没有云端"必须两个都关，
    否则会被配置兜底判定为可用、门控不触发（假阴性）。
    """
    raw = _base_raw()
    if not cloud_cfg:
        raw["cloud_omni"] = {}
    if cloud_tts:
        raw["voice_profiles"] = [
            {"engine": "dashscope_cosy", "api_key": "sk-tts", "voice": "longwan"},
        ]
    if cfg_engine is not None:
        raw["proactive"]["duplex_voice_engine"] = cfg_engine
        raw["proactive"]["duplex_video_engine"] = cfg_engine
    app = _make_app(raw)
    app._proactive = _Pro(engine_attr=engine_attr, duplex_mode=duplex_mode,
                          cloud_ok=cloud_ok)
    try:
        res = CharacterAPI.start_duplex_call(app, {"call_mode": "voice"})
    except Exception as e:  # noqa: BLE001
        return ["EXC:%s" % e], {"success": False, "error": "EXC:%s" % e}
    return app._proactive.calls, (res or {})


def main():
    print("=" * 72)
    print("拨号引擎解析回归测试（真实 start_duplex_call，非桩复刻）")
    print("=" * 72)

    print("\n[组 1] 运行态属性为空 → 必须按 config 回落（e168af6 修复）")
    r, _ = _dial(None, "cloud", "local_omni")
    check("1.1 内存空 + config=cloud → realtime", r == ["realtime"], "实际=%r" % r)
    r, _ = _dial(None, "local_omni", "realtime")
    check("1.2 内存空 + config=local_omni → local_omni", r == ["local_omni"], "实际=%r" % r)

    print("\n[组 2] 策略变更：没显式选本地 → 一律走云端（不静默回落本地）")
    r, _ = _dial(None, None, "realtime")
    check("2.1 全空 + 运行态 realtime → realtime", r == ["realtime"], "实际=%r" % r)
    r, _ = _dial(None, None, "local_omni")
    check("2.2 全空 + 运行态 local_omni（档位反推）→ realtime（★新策略）",
          r == ["realtime"], "实际=%r" % r)
    r, _ = _dial(None, "auto", "local_omni")
    check("2.3 config=auto → realtime（auto 不算显式选本地）",
          r == ["realtime"], "实际=%r" % r)
    r, _ = _dial(None, "off", "local_omni")
    check("2.4 config=off → realtime（off 不算显式选本地）",
          r == ["realtime"], "实际=%r" % r)

    print("\n[组 3] 运行态属性有值 → 行为零回归（仍以内存值为准）")
    r, _ = _dial("cloud", "local_omni", "local_omni")
    check("3.1 内存 cloud（config 写 local_omni）→ realtime", r == ["realtime"], "实际=%r" % r)
    r, _ = _dial("local_omni", "cloud", "realtime")
    check("3.2 内存 local_omni（config 写 cloud）→ local_omni",
          r == ["local_omni"], "实际=%r" % r)

    print("\n[组 4] 启动恢复：把落盘引擎写进运行态属性")
    raw = _base_raw()
    raw["proactive"]["duplex_voice_engine"] = "cloud"
    raw["proactive"]["duplex_video_engine"] = "cloud"
    app = _make_app(raw)
    pro = _Pro()
    app._proactive = pro
    app._save_config = lambda: None
    app.warm_local_model = lambda: None
    try:
        CharacterAPI.restore_saved_config_on_startup(app, reconcile=True)
        check("4.1 启动恢复后 _duplex_voice_engine=='cloud'",
              getattr(pro, "_duplex_voice_engine", "") == "cloud",
              "实际=%r" % getattr(pro, "_duplex_voice_engine", ""))
        check("4.2 启动恢复后 _duplex_video_engine=='cloud'",
              getattr(pro, "_duplex_video_engine", "") == "cloud",
              "实际=%r" % getattr(pro, "_duplex_video_engine", ""))
    except Exception as e:  # noqa: BLE001
        check("4.1 启动恢复后 _duplex_voice_engine=='cloud'", False,
              "EXC=%s: %s" % (type(e).__name__, e))

    raw2 = _base_raw()   # proactive 里没有 duplex_voice_engine
    app2 = _make_app(raw2)
    pro2 = _Pro()
    app2._proactive = pro2
    app2._save_config = lambda: None
    app2.warm_local_model = lambda: None
    try:
        CharacterAPI.restore_saved_config_on_startup(app2, reconcile=True)
        check("4.3 未配置时**不臆造**引擎值",
              not hasattr(pro2, "_duplex_voice_engine"),
              "实际=%r" % getattr(pro2, "_duplex_voice_engine", "<未设置>"))
    except Exception as e:  # noqa: BLE001
        check("4.3 未配置时**不臆造**引擎值", False, "EXC=%s: %s" % (type(e).__name__, e))

    print("\n[组 5] 锁定事实：混合档(mixed) 反推出来的 duplex_mode 是 local_omni")
    raw3 = _base_raw()
    app3 = _make_app(raw3)
    try:
        ids = CharacterAPI._features_to_engine_config(
            app3, raw3["companion_features"], raw3)
        check("5.1 mixed 预设 → duplex_mode=='local_omni'（本地语音双工 + 云端看屏）",
              raw3["proactive"].get("duplex_mode") == "local_omni",
              "实际=%r" % raw3["proactive"].get("duplex_mode"))
        check("5.2 反推返回的功能 id 含 local_duplex",
              "local_duplex" in (ids or set()), "实际=%r" % sorted(ids or []))
    except Exception as e:  # noqa: BLE001
        check("5.1 mixed 预设 → duplex_mode=='local_omni'", False,
              "EXC=%s: %s" % (type(e).__name__, e))

    print("\n[组 6] 门控：没有云端 → 如实拦截，绝不静默回落本地")
    r, res = _dial(None, None, "local_omni", cloud_ok=False, cloud_cfg=False)
    check("6.1 云端不可用 → 拨号不落到任何引擎（未发起连接）",
          r == [], "实际=%r" % r)
    check("6.2 返回 success=False", res.get("success") is False, "实际=%r" % res)
    check("6.3 error_code=='no_cloud_duplex_cfg'",
          res.get("error_code") == "no_cloud_duplex_cfg", "实际=%r" % res.get("error_code"))
    check("6.4 带 fallback='local_omni'（前端据此给「改用本地双工」按钮）",
          res.get("fallback") == "local_omni", "实际=%r" % res.get("fallback"))
    check("6.5 同时给出「为什么」与「怎么解」两段文案",
          bool(res.get("error")) and bool(res.get("action")),
          "error=%r action=%r" % (res.get("error"), res.get("action")))
    # 用户显式选本地：即便云端双工不可用也应按本地走（显式选择优先，不被云端门控拦），
    # 但语音电话出声仍需云端 TTS —— 这是既有且正确的规则，如实断言。
    r, res = _dial(None, "local_omni", "local_omni", cloud_ok=False, cloud_cfg=False,
                   cloud_tts=True)
    check("6.6 显式选本地 + 无云端双工但已绑云端TTS → 走 local_omni",
          r == ["local_omni"], "实际=%r res=%r" % (r, res.get("error_code")))
    r, res = _dial(None, "local_omni", "local_omni", cloud_ok=False, cloud_cfg=False,
                   cloud_tts=False)
    check("6.7 显式选本地 + 完全没有云端（连 TTS 都没有）→ 拦 no_cloud_tts",
          r == [] and res.get("error_code") == "no_cloud_tts",
          "实际=%r res=%r" % (r, res.get("error_code")))

    print("\n" + "=" * 72)
    total = len(PASSES) + len(FAILS)
    print("结果: %d/%d PASS" % (len(PASSES), total))
    if FAILS:
        print("失败项: %s" % FAILS)
        return 1
    print("ALL GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
