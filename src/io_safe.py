"""标准流安全化（GUI 无控制台启动的编解码兜底）。

为什么需要它（2026-10-18 实锤 crash_20261018_150448）
----------------------------------------------------
桌面快捷方式以 pythonw.exe 启动且无控制台时，`sys.stdout/stderr` 为
**None**（部分 Windows/Python 组合下甚至是 GBK 包装）。此时：

1. `reconfigure` 对 None 无话可说——main.py 原有的 utf-8 强转被跳过；
2. pywebview 的 `webview/http.py` 发现 stdout 是 None，会替换成
   `open(os.devnull, 'w')`——**devnull 按 locale 编码打开 = GBK**；
3. rich 往这个 GBK 流写 `✓` 等符号 → `UnicodeEncodeError` →
   asyncio 启动线程当场死亡，应用看起来就是「点了没反应/加载很久」。

修法：引导期（任何第三方库 import 之前）把 None 流补成 **UTF-8
devnull**。pythonw 下本来就没有控制台可看，丢弃输出即正确语义；
且 pywebview 见非 None 就不再自行替换，GBK devnull 不会出现。
非 None 的流维持 main.py 原有语义：`reconfigure(encoding="utf-8",
errors="replace")`。

约束：本模块只许 import 标准库——它在所有第三方库之前执行，
引依赖就等于白修。
"""
from __future__ import annotations

import os
import sys

__all__ = ["ensure_safe_stdio"]


def ensure_safe_stdio() -> None:
    """把 stdout/stderr 变成「打不炸」的流。幂等，可重复调用。"""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if stream is None:
            # pythonw 无控制台：输出本来就不可能被看见，
            # 给一个 UTF-8 的 devnull 顶住 pywebview 的 GBK 替换。
            try:
                setattr(sys, name,
                        open(os.devnull, "w", encoding="utf-8",
                             errors="replace"))
            except OSError:
                pass  # 连 devnull 都开不出来时只能听天由命，不再有更好的选择
        elif hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass  # 编码修不了就修不了，绝不能反向杀死启动
