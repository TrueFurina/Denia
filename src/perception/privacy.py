"""隐私模式：一键暂停/恢复屏幕与音频感知（参照 pub-local-jarvis「隐私模式」）。

暂停后各感知循环（屏幕截图 / 视觉分析 / 游戏 OCR / 游戏音效 / 云端双工推流）
在采集与推理前检查本模块标志并直接跳过——模型无法继续获取新的画面和声音，
原始数据也不再进入任何分析链路；恢复后自动继续，无需重启。

开关入口：
  - WebUI 感知调试台「🛡️ 隐私模式」按钮
  - 对话悬浮层窗口内双击（正在游戏里时快捷暂停）
"""
import threading

_lock = threading.Lock()
_PRIVACY = False


def set_privacy_mode(on: bool) -> None:
    """开启/关闭隐私模式（线程安全，任何线程可调）。"""
    global _PRIVACY
    with _lock:
        _PRIVACY = bool(on)


def privacy_mode() -> bool:
    """当前是否处于隐私模式（感知循环每帧检查）。"""
    return _PRIVACY


def toggle_privacy_mode() -> bool:
    """翻转隐私模式，返回翻转后的状态。"""
    set_privacy_mode(not privacy_mode())
    return privacy_mode()
