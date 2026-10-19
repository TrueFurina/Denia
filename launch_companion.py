"""便携启动器：定位项目目录 → 选解释器 → 关 GPU → 启动 main.py。

与旧版的区别
------------
旧版写死了三样东西，换机器必然失败：
  · `glob.glob("G:/ai*")`           —— 项目曾位于 G:\ai对话\11
  · `C://Users//Administrator/.../python.exe` —— 别人机器上的解释器路径
  （第三个是日志文件名，无所谓）

现在按 `__file__` 定位，解释器优先用随包附带的 `python/python.exe`，
没有则退回当前解释器。这样"便携目录 + 双击启动"在任何盘符都能用。
"""
from __future__ import annotations

import os
import subprocess
import sys

PROJ = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(PROJ, "main.py")
LOG = os.path.join(PROJ, "logs", "launcher.log")


def pick_python() -> str:
    """优先用随包内置运行时；否则用跑本脚本的解释器。"""
    bundled = os.path.join(PROJ, "python", "python.exe")
    if os.path.isfile(bundled):
        return bundled
    return sys.executable


def append_log(text: str) -> None:
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except OSError:
        pass  # 日志写不了不影响启动


def main() -> int:
    if not os.path.isfile(MAIN):
        print(f"[ERR] 找不到 main.py：{MAIN}")
        return 1

    os.chdir(PROJ)
    # WebView2 在部分无显卡/远程桌面的机器上会因 GPU 初始化失败白屏
    os.environ["WEBVIEW2_ADDITIONAL_ARGS"] = (
        "--disable-gpu --disable-software-rasterizer --no-sandbox")

    py = pick_python()
    append_log(f"start | python={py}")
    rc = subprocess.call([py, MAIN], cwd=PROJ)
    append_log(f"exit {rc}")
    print("EXIT", rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
