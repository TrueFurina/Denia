# -*- coding: utf-8 -*-
"""版本号的**唯一定义处**（阶段 B1）。

为什么要有它：以前版本号散在三个地方、而且互相不一致——
  · `config.yaml` / `config.release.yaml` 的 `app.version: 0.1.0`
  · 安装包文件名里的 `v1.0`
  · 日志里根本没有版本号
出问题时无法回答"用户跑的是哪个构建"，而这是排障的第一个问题。

用法：其他地方一律 `from src.version import APP_VERSION`，不要另写一份常量，
也不要从配置里再读一个版本号。升级时只改这里的三个常量。
"""
from __future__ import annotations

APP_NAME = "达妮娅 AI 桌面伴侣"
APP_VERSION = "0.2.0"
# 构建号：发版时手动 +1，或用日期。日志/关于页/安装包三处共用它，方便对账。
APP_BUILD = "20261018"

# 兼容：有些代码/脚本用 __version__ 取版本号
__version__ = APP_VERSION


def name_version() -> str:
    """给日志首行用的全名：`达妮娅 AI 桌面伴侣 0.2.0 (build 20261018)`。"""
    return f"{APP_NAME} {APP_VERSION} (build {APP_BUILD})"


def version_string() -> str:
    """给人看的版本串：`0.2.0 (build 20261018)`。"""
    return f"{APP_VERSION} (build {APP_BUILD})"


def short() -> str:
    """给日志首行用的短串：`v0.2.0/20261018`。"""
    return f"v{APP_VERSION}/{APP_BUILD}"


__all__ = ["APP_NAME", "APP_VERSION", "APP_BUILD", "__version__",
           "name_version", "version_string", "short"]
