"""桌宠窗"用哪套渲染宿主"的唯一决策点。

为什么单独抽一个模块：桌宠有两条实现路径——
  · `shell`   Electron 壳（真透明，见 AGENTS.md 第四条）
  · `webview` pywebview + WebView2（背景会有 #F0F0F0 白底）
之前这个决策散落在两个地方：`main.py` 靠 `--pet-shell` 开关，`run_webview()` 则是
"无条件先试壳"。结果是**真实入口 `main.py` 里那个 `--pet-shell` 只对 RPC 生效，
桌宠窗照旧走 pywebview**——开关看着能用，实际没接到线上。

本模块把决策收成一个函数 `resolve()` + 一个入口 `create_pet_window()`，
两条启动路径都调它，避免再出现"开关接线了但没通电"。

优先级：命令行 > 配置项 `ui.pet_backend` > 默认 `auto`。
取值：
  · auto    —— 壳可用就用，不可用就回退（回退原因记 INFO）；**默认值**
  · webview —— 明确不走壳（连 RPC 都不用起）
  · shell   —— 明确要壳；壳起不来仍然回退，但记 ERROR 并给出原因

默认值为什么从 webview 改回 auto（2026-10-18 第二轮）：
  上一轮把壳判成"本机不可用"、默认降到 webview，理由是壳渲染真实 rig 页面时
  GPU 进程必崩（`0xC0000005` → `FATAL: GPU process isn't usable`）。但后来查清：
  壳**只是起不来，不是做不到**——`--in-process-gpu --disable-gpu-compositing`
  这一组开关下既能出帧、又保持逐像素透明（`tools/_qc_shell_transparency.py`
  三关验证，对照组证明判据有区分力），并且 `pet_shell` 已经做成
  **崩溃后自动退让**（先裸启动，撞上 GPU 崩溃特征才带开关重试一次）。
  于是"auto 会先崩一次再静默回退"这个反对理由不再成立：
  壳真起不来时，auto 仍然静默回退到 webview，用户看到的就是白底桌宠，和默认
  webview 的结果一样，只是多花了约一次启动的时间。
  而壳存在的唯一理由就是真透明——默认不试壳，等于把这个能力默认关掉。

**回退不是可选项**：宁可带着白底出人，也不能出现"连人都看不到"。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("src.ui.pet_backend")

AUTO = "auto"
WEBVIEW = "webview"
SHELL = "shell"
VALID = (AUTO, WEBVIEW, SHELL)
# 默认 auto：先试壳（真透明），起不来再静默回退 webview（白底）。
# 壳起不来时结果与默认 webview 相同，只是多花一次启动时间——但默认不试，
# 就等于把"真透明"这个唯一卖点默认关掉（见模块注释）。
DEFAULT = AUTO

# 配置文件里的路径（config.yaml 顶层 ui: 段）
CONFIG_SECTION = "ui"
CONFIG_KEY = "pet_backend"


def normalize(value: Any, source: str = "配置") -> str:
    """把任意输入收敛成合法取值；非法值告警并回落到默认值（webview）。

    不抛异常是刻意的：一个拼错的配置项不该让软件起不来。
    回落目标用 DEFAULT 而不是写死 auto——默认值改过一次，写死就会漂。
    """
    if value is None:
        return DEFAULT
    text = str(value).strip().lower()
    if text in VALID:
        return text
    logger.warning("[桌宠] %s 的取值 %r 不合法（可选 %s）→ 按 %s 处理",
                   source, value, "/".join(VALID), DEFAULT)
    return DEFAULT


def from_config(cfg: Any) -> Optional[str]:
    """从配置对象读 `ui.pet_backend`；没有该段返回 None（交给调用方回落）。"""
    if cfg is None:
        return None
    ns = getattr(cfg, "ns", cfg)
    section = getattr(ns, CONFIG_SECTION, None)
    if section is None:
        return None
    raw = getattr(section, CONFIG_KEY, None)
    return None if raw is None else str(raw)


def resolve(cli_value: Any = None, cfg: Any = None) -> str:
    """命令行优先，其次配置，最后默认值（webview）。"""
    if cli_value:
        return normalize(cli_value, source="命令行")
    raw = from_config(cfg)
    if raw is not None:
        return normalize(raw, source="配置项 ui.pet_backend")
    return DEFAULT


def resolve_from_args(args: Any, cfg: Any = None) -> str:
    """兼容 argparse 命名空间：`--pet-backend` 优先，`--pet-shell` 作为历史别名。"""
    backend = getattr(args, "pet_backend", None)
    if backend:
        return normalize(backend, source="命令行 --pet-backend")
    if getattr(args, "pet_shell", False):
        # 历史开关：等价于强制走壳
        return SHELL
    return resolve(None, cfg)


def wants_rpc(backend: str, explicit_rpc: bool = False,
              handshake_path: Optional[str] = None) -> bool:
    """是否要起本地 RPC。

    webview 模式下不需要——除非用户显式要（外部壳要靠它连后端）。
    """
    b = normalize(backend, source="wants_rpc")
    if b == WEBVIEW:
        return bool(explicit_rpc or handshake_path)
    return True


def _create_webview_pet(api: Any):
    from .main_window import create_desktop_companion  # 延迟导入：避免与 main_window 循环依赖

    return create_desktop_companion(api=api)


def create_pet_window(backend: str, api: Any = None,
                      profile_dir: Optional[str] = None):
    """按 backend 创建桌宠窗，返回 `(窗口, 实际使用的后端, 回退原因)`。

    调用方拿到的窗口对象在两条路径上是**同一组成员**
    （show/hide/x/y/on_top/move/resize/evaluate_js），所以上层无需分支。
    """
    from . import pet_shell

    b = normalize(backend, source="create_pet_window")

    if b == WEBVIEW:
        return _create_webview_pet(api), WEBVIEW, "配置为 webview，不启用壳"

    ok, why = pet_shell.shell_readiness()
    if not ok:
        # 显式要求壳却起不来 → 这是用户该知道的事，日志级别抬高。
        # auto 模式下壳不可用是常态（没装 electron），INFO 就够。
        if b == SHELL:
            logger.error("[桌宠] 已配置 shell，但壳不可用（%s）→ 回退 pywebview 桌宠", why)
        else:
            logger.info("[桌宠] 壳不可用（%s）→ 使用 pywebview 桌宠（背景会有白底）", why)
        return _create_webview_pet(api), WEBVIEW, why

    try:
        # on_degrade：壳反复崩溃到配额用尽时的退路，让它退回 pywebview 窗而不是消失。
        # 必须把 api 带过去，否则降级窗拿不到 js_api，点了没反应。
        win = pet_shell.create_pet_shell(
            profile_dir=profile_dir,
            on_degrade=(lambda: _create_webview_pet(api)))
    except Exception as _e:  # noqa: BLE001  壳起不来绝不能拦住主窗口
        logger.error("[桌宠] 壳启动异常（回退 pywebview 桌宠）：%s", _e, exc_info=True)
        win = None

    if win is None:
        return _create_webview_pet(api), WEBVIEW, "壳启动失败"
    return win, SHELL, ""


__all__ = ["AUTO", "WEBVIEW", "SHELL", "VALID", "DEFAULT", "CONFIG_SECTION",
           "CONFIG_KEY", "normalize", "from_config", "resolve",
           "resolve_from_args", "wants_rpc", "create_pet_window"]
