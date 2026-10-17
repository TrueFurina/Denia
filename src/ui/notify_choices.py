"""弹窗用户选择记忆（2026-10-02 新增）。

【为什么需要】游戏锁/云端失败这类事件，用户每次都被弹窗打断却**无法表达意愿**——
只能被动接受。真正人性化的做法是：弹窗带按钮，用户的第一次选择被记住，后续按选择执行。

设计要点（与本项目其它持久化一致）：
  - 落盘在 data/notify_choices.json，**只存用户明确表达过的选择**，不存任何推断；
  - 记忆**按场景**（scene key），不同场景互不影响；
  - 任何读取失败一律返回"未选择"（None），绝不阻塞主流程（本项目铁律：静默容错）；
  - 提供"忘掉选择"接口，供设置页一键恢复默认询问。

schema（极简，便于人工查看/修正）：
{
  "version": 1,
  "choices": {
    "game_lock_unload": {"action": "silent", "ts": 1730000000.0},
    "cloud_fail":      {"action": "local",  "ts": 1730000001.0}
  }
}
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PATH = os.path.join(_ROOT, "data", "notify_choices.json")
_LOCK = threading.Lock()

# 允许的取值（防止手改 json 写进非法值导致主流程判断错乱）
ACTIONS = ("silent", "cloud", "local", "retry", "open_settings", "ask")

_LABEL = {
    "silent": "不提醒，直接执行",
    "cloud": "自动用云端",
    "local": "自动用本机",
    "retry": "自动重试",
    "open_settings": "自动打开设置",
    "ask": "每次都会询问",
}


def _load() -> dict:
    try:
        with open(_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return {}
        ch = d.get("choices")
        return ch if isinstance(ch, dict) else {}
    except Exception:  # noqa: BLE001 —— 读失败一律当"没选择过"
        return {}


def _save(choices: dict) -> bool:
    try:
        os.makedirs(os.path.dirname(_PATH), exist_ok=True)
        tmp = _PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "choices": choices}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _PATH)          # 原子替换，避免半截文件
        return True
    except Exception:  # noqa: BLE001
        logger.debug("弹窗选择记忆写入失败（忽略）", exc_info=True)
        return False


def get_choice(scene_key: str) -> Optional[str]:
    """该场景用户上次的选择；没有或非法则 None（= 下次仍要问）。"""
    if not scene_key:
        return None
    with _LOCK:
        rec = _load().get(scene_key)
    if not isinstance(rec, dict):
        return None
    a = rec.get("action")
    return a if a in ACTIONS else None


def remember(scene_key: str, action: str) -> bool:
    """记住用户在该场景的选择。非法 action 忽略（不写脏数据）。"""
    if not scene_key or action not in ACTIONS:
        return False
    with _LOCK:
        ch = _load()
        ch[scene_key] = {"action": action, "ts": time.time()}
        return _save(ch)


def forget(scene_key: str = "") -> bool:
    """忘掉某个场景（或全部）的选择，供设置页"恢复默认询问"。"""
    with _LOCK:
        if not scene_key:
            ch = _load()
            ch.clear()
            return _save(ch)
        ch = _load()
        ch.pop(scene_key, None)
        return _save(ch)


def all_choices() -> dict:
    """当前全部记忆（设置页展示用）。"""
    with _LOCK:
        return dict(_load())


def describe(scene_key: str) -> str:
    """给设置页/日志用的一句话描述。"""
    a = get_choice(scene_key)
    return _LABEL.get(a or "", "每次都会询问")


# ---- 场景 key 常量（与调用方保持一致，避免拼错）----
K_GAME_LOCK_UNLOAD = "game_lock_unload"
K_GAME_UNLOCK_RESTORE = "game_unlock_restore"
K_CLOUD_FAIL = "cloud_fail"
K_TIER_BLOCKED = "tier_blocked"
K_MIXED_BANNER = "mixed_mode_banner"
