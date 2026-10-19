# -*- coding: utf-8 -*-
"""窗口 / 运行时共享状态（2026-10-17 收敛）。

背景
----
webapp.py 原来用 15 个模块级可变全局，在「类方法 ↔ 模块级函数」之间传状态。
只要把函数搬到别的模块，`global X` 就指向**新模块**的 X，而原模块的写点还在旧 X 上
——两边各写各的，不报错，只在功能上表现为「读空字典 / 读到旧值」。

本次拆分已经踩到一次：`_safe_js_api` 里的 `global _WV2_CONTROLS` 迁到 wv2_env 后，
名字在新模块不存在，注册控件的下标赋值抛 NameError，又被 `except: pass` 吞掉，
结果是 WebView2 控件永远注册不上、后端原生推送全部静默失效。

收敛成一个进程级单例后，谁都能 `from .ui_state import STATE`，读写的是同一个对象，
再拆模块也不会有状态分裂。

边界说明
--------
本类只保证「引用唯一」，**不提供字段级锁**。notify_ui_state / wv2_controls 的原有用锁
与调用时序保持不变；需要原子性的新逻辑请自行加锁。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor


class WindowState:
    """进程内唯一的窗口/运行时状态（字段名沿用原全局语义，去掉下划线前缀）。"""

    __slots__ = (
        "main_win", "pet_win", "pet_topmost", "pet_keepalive", "pet_capture_hidden",
        "pet_recover_last", "notify_ui_state", "wv2_controls", "wv2_bc_executor",
        "local_img_extra_dirs", "web_server_base", "web_start_token",
        "gui_probe_started", "bailian_console_win", "bailian_inject_timer",
    )

    def __init__(self) -> None:
        self.main_win = None            # 主窗口 pywebview 引用
        self.pet_win = None             # 桌宠窗口引用（set_windows 时填充）
        self.pet_topmost = False        # 桌宠是否置顶
        self.pet_keepalive = False      # 置顶保活线程是否已启动
        self.pet_capture_hidden = False  # 桌宠是否对屏幕捕获隐身
        self.pet_recover_last = 0.0     # 看门狗重载冷却时间戳

        self.notify_ui_state = ["hidden"]   # 通知浮窗 UI 状态（单元素列表，沿用原写法）
        self.wv2_controls = {}              # uid -> WebView2 控件
        self.wv2_bc_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="wv2-bc")

        self.local_img_extra_dirs = []      # /local_img 白名单额外目录
        self.web_server_base = ""           # 本地静态服务基址
        self.web_start_token = ""           # 启动令牌（用于 HTML 时间戳化）
        self.gui_probe_started = False      # GUI 存活探针是否已启动

        self.bailian_console_win = None     # 百炼控制台窗口
        self.bailian_inject_timer = None    # 60s 周期注入定时器


STATE = WindowState()
