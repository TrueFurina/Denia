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

# 「不看」清单默认值（2026-10-20 立）。
#
# 为什么要有这个：屏幕采集默认每 2s 抓一帧全屏（`perception.screenshot.interval_s`），
# 抓到的画面会进模型上下文。而密码管理器窗口、网银页面、支付确认页一旦进了上下文
# 就是**不可撤回**的泄露——没有"删掉这一帧"的说法，只有"当初别抓"。
#
# 为什么默认开着：这类画面用户从不期待被看见，而"她没看到我的网银"对体验几乎无感。
# 与「隐私模式」的区别：隐私模式是**全停**（她瞎了），这份清单是**局部让位**
# （她只是没看这一个窗口）。用户可在 config.yaml 里增删，清空列表即关闭本功能。
#
# 匹配方式是**包含**（不区分大小写），进程名和窗口标题都查：
#   - 进程名查得到密码管理器（keepass.exe / 1Password.exe）
#   - 标题查得到网银支付（浏览器里开网银时进程名是 chrome.exe，只有标题含
#     「网上银行」「转账」这类词）
# 刻意**不含**微信 / QQ / 钉钉：那是产品核心能力（她要能陪你聊天），
# 且用户对它已有明确预期。
DEFAULT_SKIP_APPS: tuple[str, ...] = (
    # 密码管理器
    "keepass", "1password", "bitwarden", "lastpass", "dashlane",
    # 网银 / 支付 / 凭据（标题关键词为主）
    "网上银行", "网银", "转账", "支付密码", "支付宝", "微信支付",
    "银行卡", "凭据管理器",
)


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


def sensitive_skip_reason(proc: str, title: str,
                          patterns=None) -> str:
    """前台是否命中「不看」清单；命中返回命中的那一条，否则返回空串。

    纯函数（不读全局、不碰配置），便于直接喂参数测——采集循环里那句
    "命中就整帧不抓"是本功能唯一的实际效果，它必须能被单独验证。

    参数：
      proc     —— 前台进程名（小写 exe 名，如 "keepass.exe"）
      title    —— 前台窗口标题（如 "中国工商银行 - 网上银行"）
      patterns —— 清单；**None 用默认值，空列表表示关闭本功能**。
                  这个区别是故意的：`skip_apps: []` 是用户明确说"都别拦"，
                  不能被默认值悄悄覆盖回来。

    匹配：对每一条做包含匹配，进程名与标题都查，两边都不区分大小写。
    """
    pats = DEFAULT_SKIP_APPS if patterns is None else tuple(patterns)
    p = (proc or "").strip().lower()
    t = (title or "").strip().lower()
    if not p and not t:
        return ""
    for raw in pats:
        key = str(raw or "").strip().lower()
        if not key:
            continue
        if key in p or key in t:
            return str(raw)
    return ""
