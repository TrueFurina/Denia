"""全局热键：把**已有**能力（静音 / 桌宠显隐 / 看屏）绑到系统级组合键。

【2026-11-04 加·P0-2 最后一块空白】
四项在场能力（静音、隐身、全屏退场、托盘）里，前两项此前只能从设置页或托盘右键
走——用户得先"找到她"才能关掉她。全局热键是唯一真空白。

设计约束：
  1. **不新写一份能力**：回调直接复用托盘那套（on_toggle_mute / on_show_pet /
     on_toggle_watch），避免出现"热键静音和托盘静音状态不一致"这种双写。
  2. **不在 GUI 线程上钩子**：复用 pynput 自己的 listener 线程，和
     `src/perception/input_sensor.py` 同一套写法；回调里只做配置写入 / 已封装的窗口操作。
  3. **失败不能拖垮启动**：pynput 没装、组合键写错、被别的程序占用，一律记 warning
     并降级为"没有热键"，绝不抛到 main。
  4. **逐条校验**：一条组合键写错不该让其它热键全失效。

不进闸门：无（对应 tools/test_presence_controls.py）
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger("src.system.hotkeys")

# 允许的 action 名 → 说明。配置里出现别的一律忽略（拼错不能变成"悄悄绑到别的动作"）。
ACTIONS = ("mute", "pet", "watch")

# 配置键写全名（不拼字符串）：`check_config_keys.py` 要能在源码 token 里找到每个叶子键，
# 拼出来的 "hotkey_xxx" 不在源码里的话，出厂模板会被判成"写了没人读的死键"。
KEY_BY_ACTION = {
    "mute": "hotkey_mute",
    "pet": "hotkey_pet",
    "watch": "hotkey_watch",
}


def _validate(combo: str) -> bool:
    """组合键字符串是否合法。pynput 在时用它自己的 parser，保证和运行时一致。"""
    if not isinstance(combo, str) or not combo.strip() or "+" not in combo:
        return False
    try:
        from pynput.keyboard import HotKey
        HotKey.parse(combo.strip())
        return True
    except Exception:  # noqa: BLE001  解析器不可用 / 组合非法
        # pynput 不在时只做形状校验，别让"没装依赖"变成"配置一定错"。
        try:
            import pynput  # noqa: F401
            return False
        except Exception:  # noqa: BLE001
            return all(part.strip() for part in combo.split("+"))


def build_hotkey_map(
    spec: dict[str, Any],
    actions: dict[str, Callable[[], Any]],
) -> tuple[dict[str, Callable[[], Any]], list[str]]:
    """把配置映射成 pynput 要的 {组合键: 回调}，返回 (映射, 被跳过的原因列表)。

    纯函数，不起线程、不碰键盘，可以离线单测。
    """
    out: dict[str, Callable[[], Any]] = {}
    skipped: list[str] = []
    for name, combo in (spec or {}).items():
        if name not in ACTIONS:
            skipped.append("%s: 未知动作" % name)
            continue
        fn = actions.get(name)
        if fn is None:
            skipped.append("%s: 没有绑定回调" % name)
            continue
        if combo is None or combo is False or (isinstance(combo, str) and not combo.strip()):
            skipped.append("%s: 未配置（空=关闭）" % name)
            continue
        if not _validate(str(combo)):
            skipped.append("%s: 组合键非法 %r" % (name, combo))
            continue
        key = str(combo).strip()
        if key in out:
            skipped.append("%s: 与已有热键重复 %s" % (name, key))
            continue
        out[key] = _safe(fn, name)
    return out, skipped


def _safe(fn: Callable[[], Any], name: str) -> Callable[[], None]:
    """一个回调炸了不能弄死 listener 线程（pynput 会吞异常但不保证继续收键）。"""
    def _run() -> None:
        try:
            fn()
        except Exception:  # noqa: BLE001
            logger.warning("热键 %s 回调失败", name, exc_info=True)
    return _run


class HotkeyManager:
    """系统级热键的生命周期。幂等 start/stop，失败一律降级。"""

    def __init__(
        self,
        spec: dict[str, Any],
        actions: dict[str, Callable[[], Any]],
    ) -> None:
        self._spec = spec or {}
        self._actions = actions or {}
        self._listener = None
        self._lock = threading.Lock()
        self._ok = False
        self._error = ""
        self._mapping, self._skipped = build_hotkey_map(self._spec, self._actions)

    @property
    def ok(self) -> bool:
        return self._ok

    @property
    def error(self) -> str:
        return self._error

    @property
    def mapping(self) -> dict[str, Callable[[], Any]]:
        return dict(self._mapping)

    @property
    def skipped(self) -> list[str]:
        return list(self._skipped)

    def start(self) -> bool:
        with self._lock:
            if self._ok:
                return True
            if not self._mapping:
                self._error = "没有可用热键（全部被关闭或非法）"
                logger.info("全局热键未启用：%s", self._error)
                return False
            try:
                from pynput import keyboard
                self._listener = keyboard.GlobalHotKeys(self._mapping)
                self._listener.daemon = True
                self._listener.start()
                self._ok = True
                logger.info("全局热键已启用：%s", ", ".join(self._mapping))
                for why in self._skipped:
                    logger.info("  跳过：%s", why)
                return True
            except Exception as e:  # noqa: BLE001
                self._error = str(e)
                logger.warning("全局热键启动失败（%s）——其余功能不受影响", e)
                return False

    def stop(self) -> None:
        with self._lock:
            if self._listener is not None:
                try:
                    self._listener.stop()
                except Exception:  # noqa: BLE001
                    pass
                self._listener = None
            self._ok = False


def start_from_config(cfg: Any, actions: dict[str, Callable[[], Any]]) -> Optional[HotkeyManager]:
    """从 config.raw['presence'] 起一套热键；关掉或出错都返回 None/未启用对象。"""
    raw = getattr(cfg, "raw", None)
    section = (raw or {}).get("presence") or {} if isinstance(raw, dict) else {}
    if not isinstance(section, dict) or not section.get("hotkeys_enabled", False):
        logger.info("全局热键按配置关闭")
        return None
    spec = {action: section.get(key) for action, key in KEY_BY_ACTION.items()}
    mgr = HotkeyManager(spec, actions)
    mgr.start()
    return mgr
