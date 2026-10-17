"""启动角色管理 UI（pywebview 桌面应用）。

用法:
    python run_webui.py
    python run_webui.py --debug
"""
import sys
import os

# 确保能从项目根目录导入 src
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.ui.webapp import run_webview

if __name__ == "__main__":
    debug = "--debug" in sys.argv
    run_webview(debug=debug)
