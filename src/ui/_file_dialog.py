# -*- coding: utf-8 -*-
"""系统文件框（在**独立子进程**里弹）—— 2026-10-13 修「加图片后主进程卡死」。

为什么必须放子进程
------------------
原实现是「在主进程里开一个后台线程，线程内 `tk.Tk()` + `filedialog.askopenfilename`」。
这条路径实测会把主进程整体拖死：

· 真实症状（2026-10-13 两次实测，均发生在主题编辑器「选择主页背景」弹框后 3 秒内）：
  `logs/run_*.log` **整体停止增长**（不是某个线程停，是所有 logger 一起停），
  而进程**仍然活着**（8777 仍在监听、静态页仍返回 200）。
  → 典型的"某线程持着 logging 全局锁、卡在原生调用里"的形态。
· 机理：tkinter 属于**必须在拥有它的那个解释器/线程里跑**的 GUI 工具包。在非主线程
  里建 Tk root，Windows 上会再起一套 Tcl/窗口消息循环；与主进程已有的
  WinForms + WebView2 消息泵共存时，模态框会抢走消息泵 → GUI 线程停摆 →
  pywebview 的 BeginInvoke 心跳停 → 依赖 GUI 的心跳线程持锁阻塞。

隔离后的收益（与 `diagnose-gui-crash-no-traceback` 里"危险原生调用丢子进程"同款范式）：
  ① 主进程**完全不碰 tkinter**（不 import、不建 Tk），彻底消除 Tk×WinForms 共存；
  ② 子进程崩了/卡死只死子进程，主进程由 `subprocess.run(timeout=...)` 兜底；
  ③ 用户可见行为不变：还是同一个系统文件框、同样的标题与过滤器。

配套：**不要**在调用方再做 in-process 的 tkinter 兜底（那会把 bug 重新引回来）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from typing import Optional, Sequence, Tuple

__all__ = ["pick_path"]

# 子进程脚本：自己起 Tk、弹框、把结果写进 out 文件（用文件而非 stdout，避免 GBK 控制台编码坑）
# 【2026-10-18 E2E 修复·系统对话框全断】
# 根因：便携 Python 未打包 Tcl/Tk（python 目录无 tkinter / tcl86t.dll），
#       _CHILD 只用 tkinter → import 失败 → exit(3) → 所有
#       「选图片 / 选文件夹 / 选参考音 / 选模型目录」点了没反应。
# 修法：主实现改 **PowerShell + .NET WinForms**（Windows 自带，必可用）；
#       仍用子进程隔离（主线程不碰 GUI，绝不卡死主进程）。
#       tkinter 保留为回退（万一 PowerShell 被策略禁）。
_CHILD = r'''
import json, sys, os, subprocess, tempfile
cfg = json.loads(sys.argv[1])
out = sys.argv[2]
p = ""

def _via_powershell():
    # PowerShell + .NET: FolderBrowserDialog / OpenFileDialog
    kind = "dir" if cfg.get("kind") == "dir" else "file"
    title = (cfg.get("title") or "").replace('"', "'")
    if kind == "dir":
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms | Out-Null; "
            "$d=New-Object System.Windows.Forms.FolderBrowserDialog; "
            "$d.Description='%s'; "
            "$r=$d.ShowDialog(); "
            "if($r -eq [System.Windows.Forms.DialogResult]::OK){Write-Output $d.SelectedPath}"
            % title
        )
    else:
        fts = cfg.get("filetypes") or [["All files", "*.*"]]
        # 转成 .NET 的 filter 串："Text|*.txt|Images|*.png;*.jpg"
        parts = []
        for f in fts:
            label = str(f[0]) if f and f[0] else "Files"
            pat = str(f[1]) if f and len(f) > 1 else "*.*"
            if pat.lower() in ("*.*", "*"):
                label, pat = "All files", "*.*"
            parts.append("%s|%s" % (label.replace("|", " "), pat))
        filt = "|".join(parts) if parts else "All files|*.*"
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms | Out-Null; "
            "$d=New-Object System.Windows.Forms.OpenFileDialog; "
            "$d.Title='%s'; $d.Filter='%s'; $d.CheckFileExists=$%s; "
            "$r=$d.ShowDialog(); "
            "if($r -eq [System.Windows.Forms.DialogResult]::OK){Write-Output $d.FileName}"
            % (title, filt, "true" if cfg.get("mustexist") else "false")
        )
    r = subprocess.run(
        ["powershell", "-NoProfile", "-STA", "-Command", ps],
        capture_output=True, text=True, timeout=300,
        creationflags=0x08000000,
    )
    return (r.stdout or "").strip().splitlines()[-1].strip() if (r.stdout or "").strip() else ""

def _via_tkinter():
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    try:
        if cfg.get("kind") == "dir":
            return filedialog.askdirectory(
                title=cfg.get("title") or "Select folder",
                mustexist=bool(cfg.get("mustexist", False))) or ""
        fts = cfg.get("filetypes") or [["All files", "*.*"]]
        return filedialog.askopenfilename(
            title=cfg.get("title") or "Select file",
            filetypes=[tuple(x) for x in fts]) or ""
    finally:
        try:
            root.destroy()
        except Exception:
            pass

# PowerShell 优先，tkinter 回退
try:
    p = _via_powershell()
except Exception:
    p = ""
if not p:
    try:
        p = _via_tkinter()
    except Exception:
        pass
with open(out, "w", encoding="utf-8") as f:
    f.write(p or "")
'''


def pick_path(title: str = "选择文件",
              filetypes: Optional[Sequence[Sequence[str]]] = None,
              kind: str = "file",
              mustexist: bool = False,
              timeout: float = 180.0) -> Tuple[str, str]:
    """弹系统文件/目录框（子进程内）。

    返回 `(path, error)`：
      · 用户选中        → (路径, "")
      · 用户取消        → ("", "")            （**不是**错误，调用方按"取消"处理）
      · 超时 / 子进程崩 → ("", "原因")        （调用方转成用户可读提示）

    绝不抛异常。
    """
    tmpdir = tempfile.mkdtemp(prefix="dlgpick_")
    script = os.path.join(tmpdir, "_child.py")
    out = os.path.join(tmpdir, "out.txt")
    try:
        with open(script, "w", encoding="utf-8") as f:
            f.write(_CHILD)
        cfg = json.dumps({
            "title": title,
            "kind": kind,
            "mustexist": bool(mustexist),
            "filetypes": [list(x) for x in (filetypes or [])],
        }, ensure_ascii=False)

        kwargs = {}
        if os.name == "nt":
            # 不弹额外的黑色控制台窗口
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        try:
            r = subprocess.run([sys.executable, script, cfg, out],
                               capture_output=True, timeout=timeout, **kwargs)
        except subprocess.TimeoutExpired:
            return "", "文件选择超时，请重试"
        except Exception as e:  # noqa: BLE001
            return "", "文件选择失败: %s" % e

        errf = out + ".err"
        if os.path.isfile(errf):
            try:
                with open(errf, encoding="utf-8") as f:
                    msg = f.read().strip()
            except OSError:
                msg = ""
            if not msg and r.returncode != 0:
                msg = (r.stderr or b"").decode("utf-8", "replace").strip()[-200:] or "子进程异常退出"
            return "", msg or "文件选择失败"
        if r.returncode != 0:
            tail = (r.stderr or b"").decode("utf-8", "replace").strip()[-200:]
            return "", tail or "文件选择失败（子进程退出码 %s）" % r.returncode

        if os.path.isfile(out):
            with open(out, encoding="utf-8") as f:
                return f.read().strip(), ""
        return "", ""
    except Exception as e:  # noqa: BLE001
        return "", "文件选择失败: %s" % e
    finally:
        try:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            # 临时目录删不掉由系统回收（对话框结果已拿到）
            pass
