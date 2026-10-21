# -*- coding: utf-8 -*-
"""拨号引擎解析回归测试

历史
----
**2026-10-09**（第一轮）：用户反馈「重进软件后，点了切换模式才成功接听」。日志实锤
02:09:38 拨号 engine=local_omni（没接通）/ 02:10:30 engine=realtime（接通）。根因是
`start_duplex_call` 只读 `pro._duplex_voice_engine` 这个**内存属性**，而启动时从未从
config 初始化 → 重启后第一次拨号恒为空。修法：启动时把落盘值写进运行态（本文件的组 4）。

**2026-10-09**（第二轮）：用户拍板「拨号引擎应该走云端，没有云端就应该门控拦截」。
那一轮的契约是「只有用户显式选本地才走本地」，本文件当时据此写了组 1/3/6。

**2026-10-16**（第三轮·当前契约）：用户进一步明确 **「语音电话恒走云端」**。代码里是
`webapp.start_duplex_call` 里那句无条件的 `target = "realtime"`（"按用户要求"）。于是
上一轮「显式选本地 → 走本地」的整条路径**被否决**了。

这个测试因此改写成守**当前**契约（而不是继续红着守已被否决的行为——本仓有过
`test_quota_alert` 的教训：那种测试长期是绿的，"通过"只证明"否决的东西还没被加回来"；
更糟的是谁"修好它"反而红，下一个人就会把用户停掉的行为加回来）：

  组 1  任何输入组合下 target 都是 realtime（config / 内存 / 档位说什么都不算数）
  组 2  没有云端双工 → 如实拦截：不发起连接 + success=False + no_cloud_duplex + 可读文案
  组 3  旧契约的墓碑：`no_cloud_duplex_cfg` / `fallback` / `action` / `no_cloud_tts`
        在**任何**输入组合下都不出现（10-16 之后它们不可达；谁把它们救活，就必须同时
        改这里、改 `start_duplex_call` 里那段注释，并确认用户是否真要恢复本地双工）
  组 4  启动恢复：把落盘引擎写进运行态属性（2026-10-09 第一轮的回归）
  组 5  锁定事实：mixed 预设反推出来的 duplex_mode 是 local_omni（与"拨号恒走云端"不冲突：
        它只描述档位能力，不决定拨号引擎）

本测试调用**真实的** `CharacterAPI.start_duplex_call` / `restore_saved_config_on_startup`
（不是复刻一份逻辑的桩），用假 self/pro 把外部依赖顶掉，断言真正落地的 `target`。
"""

import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.ui.webapp import CharacterAPI  # noqa: E402

FAILS = []
PASSES = []

# 2026-10-16 之后**不应再出现**的错误码 / 字段（见模块 docstring 组 3）
RETIRED_CODES = ("no_cloud_duplex_cfg", "no_cloud_tts")


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
    cloud_tts : 是否绑了任一云端 TTS
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
    # 组 3 会跑 240 次拨号，每次失败路径都有一条 `logger.warning("拨号被拒…")` —— 不关掉
    # 会刷 240 行噪音，把真正的 PASS/FAIL 埋掉。只压 WARNING 及以下，ERROR 仍可见
    # （真出异常时还是要看得见）。
    logging.disable(logging.WARNING)
    print("=" * 72)
    print("拨号引擎解析回归测试（真实 start_duplex_call，非桩复刻）")
    print("=" * 72)

    print("\n[组 1] 2026-10-16 契约：语音电话**恒走云端**（任何输入都不改 target）")
    cases = [
        ("1.1 内存空 + config=cloud", dict(engine_attr=None, cfg_engine="cloud",
                                          duplex_mode="local_omni")),
        ("1.2 内存空 + config=local_omni（旧契约曾走本地，已否决）",
         dict(engine_attr=None, cfg_engine="local_omni", duplex_mode="realtime")),
        ("1.3 内存 local_omni + config=cloud（旧契约曾走本地，已否决）",
         dict(engine_attr="local_omni", cfg_engine="cloud", duplex_mode="realtime")),
        ("1.4 内存 local_omni + config=local_omni（双重显式选本地，照样走云端）",
         dict(engine_attr="local_omni", cfg_engine="local_omni",
              duplex_mode="local_omni")),
        ("1.5 全空 + 档位反推 local_omni", dict(engine_attr=None, cfg_engine=None,
                                              duplex_mode="local_omni")),
        ("1.6 config=auto", dict(engine_attr=None, cfg_engine="auto",
                                 duplex_mode="local_omni")),
        ("1.7 config=off", dict(engine_attr=None, cfg_engine="off",
                                duplex_mode="local_omni")),
    ]
    for name, kw in cases:
        r, _ = _dial(**kw)
        check("%s → realtime" % name, r == ["realtime"], "实际=%r" % r)

    print("\n[组 2] 没有云端双工 → 如实拦截（不发起连接、不静默回落本地）")
    r, res = _dial(None, "local_omni", "local_omni", cloud_ok=False, cloud_cfg=False,
                   cloud_tts=True)
    check("2.1 云端不可用 → 拨号不落到任何引擎（未发起连接）", r == [], "实际=%r" % r)
    check("2.2 返回 success=False", res.get("success") is False, "实际=%r" % res)
    check("2.3 error_code=='no_cloud_duplex'",
          res.get("error_code") == "no_cloud_duplex", "实际=%r" % res.get("error_code"))
    check("2.4 给出可读文案（不是一句 '失败'）",
          len(str(res.get("error") or "")) >= 20, "error=%r" % res.get("error"))
    check("2.5 带 diag（事后可归因）", isinstance(res.get("diag"), dict),
          "diag=%r" % res.get("diag"))
    # 即使绑了云端 TTS、即使显式选本地 —— 只要云端双工没配，就拦。
    # （旧契约在这里会放行到 local_omni；那是被 10-16 否决的行为。）
    check("2.6 显式选本地 + 已绑云端TTS 也不再放行到本地（旧契约已否决）",
          r == [] and res.get("error_code") == "no_cloud_duplex",
          "实际=%r res=%r" % (r, res.get("error_code")))

    print("\n[组 3] 旧契约的墓碑：被否决的错误码/字段在任何组合下都不出现")
    seen_codes, saw_fallback, saw_action, n = set(), False, False, 0
    for eng in (None, "cloud", "local_omni"):
        for cfg_eng in (None, "cloud", "local_omni", "auto", "off"):
            for mode in ("realtime", "local_omni"):
                for ok in (True, False):
                    for ccfg in (True, False):
                        for tts in (False, True):
                            r, res = _dial(eng, cfg_eng, mode, cloud_ok=ok,
                                           cloud_cfg=ccfg, cloud_tts=tts)
                            n += 1
                            code = res.get("error_code")
                            if code:
                                seen_codes.add(code)
                            if "fallback" in res:
                                saw_fallback = True
                            if "action" in res:
                                saw_action = True
    print("  扫描组合数=%d，出现过的 error_code=%s" % (n, sorted(seen_codes)))
    check("3.1 任何组合都不返回 %s（10-16 后不可达）" % (RETIRED_CODES,),
          not (set(RETIRED_CODES) & seen_codes),
          "实际出现过=%r" % sorted(set(RETIRED_CODES) & seen_codes))
    check("3.2 任何组合都不带 fallback（「改用本地双工」按钮已随决策下线）",
          saw_fallback is False)
    check("3.3 任何组合都不带 action（旧的两段式文案字段）", saw_action is False)
    check("3.4 组合确实覆盖到了失败路径（否则上面三条是空断言）",
          "no_cloud_duplex" in seen_codes, "seen=%r" % sorted(seen_codes))

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
        check("5.1 mixed 预设 → duplex_mode=='local_omni'（档位能力描述，不决定拨号引擎）",
              raw3["proactive"].get("duplex_mode") == "local_omni",
              "实际=%r" % raw3["proactive"].get("duplex_mode"))
        check("5.2 反推返回的功能 id 含 local_duplex",
              "local_duplex" in (ids or set()), "实际=%r" % sorted(ids or []))
    except Exception as e:  # noqa: BLE001
        check("5.1 mixed 预设 → duplex_mode=='local_omni'", False,
              "EXC=%s: %s" % (type(e).__name__, e))

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
