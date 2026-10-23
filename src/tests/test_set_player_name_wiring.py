# -*- coding: utf-8 -*-
"""游戏玩家名设置的接线审计（SET_PLAYER_NAME 死订阅清理回归）。

**为什么需要这组测试**

`GameOcrMonitor.set_player_name` 曾被设想走两条触发路径：
  ① WebUI「我的名字」填写 → webapp.ocr_set_player_name → set_player_name
     （唯一真实生效路径）；
  ② proactive / dialogue 从用户回复提取名字后 publish SET_PLAYER_NAME 事件，
     GameOcrMonitor 订阅 `_on_set_player_name` 后调 set_player_name。

2026-10-23 审计发现：路径 ② 全仓**没有任何发布者**——proactive.py 零
player_name 引用、dialogue.py 无名字提取逻辑；`_on_set_player_name` 成了永远
收不到事件的死监听器，而 docstring 与 run() 注释却声称 ② 存在——典型的
「写了没接线 / 假接口」。死订阅已清理，改名只走 ①。

本组测试守三件事：
  ① set_player_name 经 WebUI 直调路径正确写内存 + config（唯一真实路径不能坏）；
  ② 死事件处理器 `_on_set_player_name` 已不存在（防回归：有人再贴回死订阅）；
  ③ 构造后 bus 上**没有**任何 SET_PLAYER_NAME 订阅（死接线已移除）。
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from unittest.mock import MagicMock  # noqa: E402

from src.event_bus import EventBus, EventType  # noqa: E402
from src.perception.game_ocr import GameOcrMonitor  # noqa: E402

FAIL = 0


def check(cond, msg):
    global FAIL
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        FAIL += 1


def _make_cfg():
    cfg = SimpleNamespace()
    cfg.ns = SimpleNamespace()
    cfg.ns.perception = SimpleNamespace()
    cfg.ns.perception.ocr = SimpleNamespace(player_name="")
    cfg.raw = {}
    cfg.save = lambda: None
    return cfg


print("[1] WebUI 直调路径：set_player_name 写内存 + config")
cfg = _make_cfg()
monitor = GameOcrMonitor(bus=MagicMock(), cfg=cfg,
                         monitor=MagicMock(), sensor=MagicMock())
check(monitor.player_name == "", "初始未设置 → 玩家名为空")
monitor.set_player_name("  Alice  ")
check(monitor.player_name == "Alice", "前后空白自动 strip → 'Alice'")
check(cfg.raw.get("perception", {}).get("ocr", {}).get("player_name") == "Alice",
      "写回 config.yaml perception.ocr.player_name")
check(monitor.effective_player_name == "Alice", "effective_player_name 同步反映")
monitor.set_player_name("")
check(monitor.player_name == "", "清空后玩家名为空")
check(cfg.raw["perception"]["ocr"]["player_name"] == "", "清空也写回 config")

print()
print("[2] 死事件处理器已清理（防回归）")
check(not hasattr(monitor, "_on_set_player_name"),
      "实例上不存在 _on_set_player_name 死方法")
check(not hasattr(GameOcrMonitor, "_on_set_player_name"),
      "类上也不存在 _on_set_player_name 死方法")

print()
print("[3] 构造后 bus 上无任何 SET_PLAYER_NAME 订阅")
bus = EventBus()
GameOcrMonitor(bus=bus, cfg=_make_cfg(),
               monitor=MagicMock(), sensor=MagicMock())
check(EventType.SET_PLAYER_NAME not in bus._handlers,
      "构造未向 bus 注册 SET_PLAYER_NAME 订阅（死接线已移除）")

print()
if FAIL:
    print(f"FAIL: {FAIL} 项未通过")
    sys.exit(1)
print("ALL PASS")
