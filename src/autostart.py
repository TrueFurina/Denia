# -*- coding: utf-8 -*-
"""【阶段 E · 开机自启开关】HKCU Run 键的写入/清除/查询。

设计约束：
- **每用户、无 UAC**：写 `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`，
  与 C2-A 安装器（%LOCALAPPDATA%\\DaniaAI，RequestExecutionLevel user）同一理念。
- **config 是单一真值**：main.py 启动时用 `sync(cfg.autostart)` 把注册表对齐到
  配置；RPC 的 set_autostart 改完注册表后也应把配置写回（谁改都收敛到一致）。
- **测试可注入**：所有入口带 value_name 参数，测试用 `_DaniaAutostartTest`
  不碰真键；root 显式注入，不读 sys 状态。
- 卸载器（installer.nsi）负责删 `DaniaAI` 值；本模块只管运行期开关。
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "DaniaAI"


def _pythonw_in(root: str) -> str:
    """包根下的 pythonw.exe（python/pythonw.exe）。

    开机自启绝不能用 python.exe——会弹常驻控制台窗。
    """
    return os.path.join(root, "python", "pythonw.exe")


def command_for(root: str) -> str:
    """自启命令行：`"<root>\\python\\pythonw.exe" "<root>\\main.py"`。

    全路径 + 引号包裹：卸载搬家后不会解析到别的 python；
    main.py 用相对自己的路径定位资源，cwd 无关紧要。
    """
    return f'"{_pythonw_in(root)}" "{os.path.join(root, "main.py")}"'


def is_enabled(root: str, value_name: str = VALUE_NAME) -> bool:
    """注册表里有非空命令 → True。键不存在 / 无权限 / 值为空 → False（不抛）。"""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_READ) as k:
            cmd, _typ = winreg.QueryValueEx(k, value_name)
            return bool(cmd)
    except FileNotFoundError:
        return False
    except OSError as e:  # noqa: PERF203
        logger.debug("读自启状态失败: %s", e)
        return False


def read_command(root: str, value_name: str = VALUE_NAME) -> str | None:
    """读注册的命令原文（与 command_for 比对可发现漂移）。无 → None。"""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_READ) as k:
            cmd, _typ = winreg.QueryValueEx(k, value_name)
            return cmd if cmd else None
    except FileNotFoundError:
        # 没设过开机自启（值不存在）→ 按未启用处理
        return None
    except OSError as e:  # noqa: PERF203
        logger.debug("读自启命令失败: %s", e)
        return None


def enable(root: str, value_name: str = VALUE_NAME) -> None:
    """写入/覆盖自启命令（幂等）。"""
    import winreg

    cmd = command_for(root)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                        winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, value_name, 0, winreg.REG_SZ, cmd)
    logger.debug("开机自启已开启: %s", cmd)


def disable(root: str, value_name: str = VALUE_NAME) -> None:
    """删除自启项（不存在也是成功，幂等）。root 参数仅为接口对称。"""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, value_name)
        logger.debug("开机自启已关闭")
    except FileNotFoundError:
        # 本来就没设过开机自启，无需删（目标态已达）
        pass


def sync(on: bool, root: str, value_name: str = VALUE_NAME) -> None:
    """把注册表对齐到给定状态（main.py 启动时按 config 调用）。

    enable/disable 本身幂等，sync 直接转发；显式函数便于调用方表意。
    """
    (enable if on else disable)(root, value_name=value_name)
