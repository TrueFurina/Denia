# -*- coding: utf-8 -*-
"""失效 TTS 模型 / 音色档的启动期自动迁移（纯函数 + 原子写盘）。

**为什么单独成模块**

1. 原实现写在 `src/ui/webapp.py` 里，其中 `migrate_stale_voice_profiles`
   **从未被任何地方调用**——函数写完了、注释写得很清楚，就是没接线。
   2026-10-19 合并队友工作树时被静态检查抓出来（`grep` 全仓只有定义没有调用）。
   纯函数搬出 webapp 才能被轻量测试直接打（webapp 导入链太重）。
2. 迁移要写盘，而原实现是 `io.open(path, "w")` 裸写：非原子、不 fsync、
   不拿运行时锁。这与 `Config.save()` 的并发审计结论（P0：并发写出含 NULL
   字节的半个 YAML → 之后所有保存永久失败）是同一类事故，只是入口不同。
   现在统一走 `dump_config_atomic()`。

**为什么需要迁移**

百炼的 TTS 模型换过代：`qwen-audio-3.0-tts-*` 与 cosyvoice 全系列已下线
（实测 400/418），`qwen3-tts-flash` 是当时唯一 200 的模型。旧版一键配置
把这些已下线的名字写进了用户配置，用户升级后**试听永远失败**，还会以为
是自己音色 ID 填错了——"能说"这条链的典型静默失效。

迁移是**幂等**的：值已经是对的就不动，反复启动不会重复写盘。
"""
from __future__ import annotations

import copy
import logging
import os
import tempfile
import time
from typing import Any

logger = logging.getLogger(__name__)

# 已下线的 TTS 模型名（两个迁移入口各有一份历史清单，这里是并集）。
STALE_TTS_MODELS: tuple[str, ...] = (
    "qwen-audio-3.0-tts-plus",
    "qwen-audio-3.0-tts-flash",
    "qwen-audio-3.1-tts-flash",
    "cosyvoice-v3.5-plus",
    "cosyvoice-v3.5-flash",
    "cosyvoice-v3-flash",
    "cosyvoice-v3-plus",
    "cosyvoice-v3",
    "cosyvoice-v2",
    "cosyvoice-v1",
)

# 迁移目标：当时实测唯一可用的模型 + 一个通用音色。
TTS_MIGRATE_TO = "qwen3-tts-flash"
TTS_MIGRATE_VOICE = "Cherry"


def migrate_stale_tts_model(raw: dict) -> list[str]:
    """把配置里两处 TTS 模型名从已下线值换成 `qwen3-tts-flash`（幂等）。

    覆盖点：`voice.clone.cloud.dashscope_cosy.model_name`（角色内联音色）与
    `cloud_tts.dashscope_cosy.model_name`（全局云端 TTS 段）。两处都要改——
    只改一处的话，另一处仍会把失效模型名传给后端。

    返回被改动的字段路径列表；空列表表示无需迁移。
    """
    out: list[str] = []
    try:
        cosy = (((raw.get("voice") or {}).get("clone") or {}).get("cloud")
                or {}).get("dashscope_cosy")
        if isinstance(cosy, dict) and str(cosy.get("model_name") or "").strip() in STALE_TTS_MODELS:
            cosy["model_name"] = TTS_MIGRATE_TO
            out.append("voice.clone.cloud.dashscope_cosy.model_name")
        ct = raw.get("cloud_tts") or {}
        b = ct.get("dashscope_cosy")
        if isinstance(b, dict) and str(b.get("model_name") or "").strip() in STALE_TTS_MODELS:
            b["model_name"] = TTS_MIGRATE_TO
            out.append("cloud_tts.dashscope_cosy.model_name")
    except Exception:  # noqa: BLE001  配置结构畸形时宁可不迁移，也不能让启动崩
        logger.debug("[config_migrate] 模型迁移跳过（配置结构异常）", exc_info=True)
    return out


def migrate_stale_voice_profiles(raw: dict) -> list[str]:
    """把绑着失效模型的音色档（`voice_profiles`）迁到 qwen3-tts-flash + Cherry。

    【2026-10-19 接线】本函数在合并前是**死代码**：定义完整、注释完整，
    但全仓没有任何调用点。用户已建的音色档若绑着 cosyvoice 系列，试听/聊天
    必然 400，而启动期从来没人去修它。

    判定比模型迁移更宽：除了精确命中清单，音色字段里只要出现
    `cosyvoice` / `qwen-audio` 子串也算失效——那是把模型名填进 voice 的
    历史脏数据（用户照着一键配置的旧默认值抄进去的）。

    返回被改动的档标识列表；空列表表示无需迁移。
    """
    out: list[str] = []
    try:
        for prof in (raw.get("voice_profiles") or []):
            if not isinstance(prof, dict) or prof.get("engine") != "dashscope_cosy":
                continue
            # 音色字段是 `voice_id`（tts_backends 用 getattr(config, "voice_id", ...)
            # 取），`voice` 在其它后端是 fallback_voice。两个都读，兼容历史脏数据。
            v = str(prof.get("voice_id") or prof.get("voice") or "").strip().lower()
            m = str(prof.get("model") or "").strip().lower()
            if (v in STALE_TTS_MODELS or m in STALE_TTS_MODELS
                    or "cosyvoice" in v or "qwen-audio" in v):
                prof["model"] = TTS_MIGRATE_TO
                # 【2026-10-20 修复·字段名写错，迁移等于白做】
                # 原实现写的是 prof["voice"] = "Cherry"——音色档根本不读这个字段，
                # 真正的 voice_id 仍是 `cosyvoice-v3.5-plus-bailian-…`。
                # 结果：model 换成了 qwen3-tts-flash，音色还是已失效的 cosyvoice
                # 克隆 id → 请求照样 418（tts_backends.py 有实测注释：418 是
                # voice_id 不可用，与 model 匹配无关，排查时别在 model 上打转）。
                # 用真机配置实测确认过：修复前 voice_id 原封不动。
                prof["voice_id"] = TTS_MIGRATE_VOICE
                out.append("voice_profile:%s" % prof.get("id"))
    except Exception:  # noqa: BLE001
        logger.debug("[config_migrate] 音色档迁移跳过（配置结构异常）", exc_info=True)
    return out


def migrate_stale_tts_config(raw: dict) -> list[str]:
    """启动期一次性跑完全部 TTS 迁移，返回变更列表（空 = 无需写盘）。

    **这是唯一应该被调用的入口**。单独调上面两个函数中的任何一个都会漏掉
    另一半——`migrate_stale_voice_profiles` 就是这样变成死代码的。
    """
    return migrate_stale_tts_model(raw) + migrate_stale_voice_profiles(raw)


def dump_config_atomic(path: str, data: dict, *, seal: bool = True) -> bool:
    """原子写 YAML 配置：临时文件 + fsync + `os.replace`，Windows 带退避重试。

    与 `src/config.py::Config.save()` 同一套纪律（那边有完整的审计记录：
    并发裸写会产出含 NULL 字节的半个 YAML，之后所有保存永久失败）。这里不
    复用 Config.save() 是因为迁移发生在 `Config` 实例建立**之前**——必须先
    改盘上文件，后续加载才读得到新值。

    `seal=True` 时对**写盘副本**做密钥加密，不动调用方的 dict。加密幂等：
    已是 `dpapi:` 的字段会被跳过，不会套两层。

    返回 True 表示已落盘；False 表示失败（调用方自行决定是否告警，不抛异常——
    迁移失败不该让软件打不开）。
    """
    tmp = ""
    try:
        payload: Any = copy.deepcopy(data)
        if seal:
            try:
                from . import secret_box

                secret_box.seal_tree(payload)
            except Exception:  # noqa: BLE001  加密失败降级为明文（与 Config.save 一致）
                logger.warning("[config_migrate] 写盘前加密失败，按明文写出",
                               exc_info=True)
        import yaml

        _dir = os.path.dirname(os.path.abspath(path)) or "."
        _fd, tmp = tempfile.mkstemp(prefix=".config_", suffix=".tmp", dir=_dir)
        with os.fdopen(_fd, "w", encoding="utf-8") as f:
            yaml.dump(payload, f, allow_unicode=True,
                      default_flow_style=False, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        last: Exception | None = None
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                last = None
                break
            except PermissionError as e:   # 目标正被别的线程/进程读取
                last = e
                time.sleep(0.05 * (2 ** attempt))
        if last is not None:
            raise last
        return True
    except Exception:  # noqa: BLE001
        logger.warning("[config_migrate] 配置迁移写盘失败: %s", path, exc_info=True)
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return False
