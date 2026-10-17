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
_CHILD = r'''
import json, sys
cfg = json.loads(sys.argv[1])
out = sys.argv[2]
p = ""
try:
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
            p = filedialog.askdirectory(title=cfg.get("title") or "选择文件夹",
                                        mustexist=bool(cfg.get("mustexist", False)))
        else:
            fts = cfg.get("filetypes") or [["所有文件", "*.*"]]
            p = filedialog.askopenfilename(title=cfg.get("title") or "选择文件",
                                           filetypes=[tuple(x) for x in fts])
    finally:
        try:
            root.destroy()
        except Exception:
            pass
except Exception as e:
    with open(out + ".err", "w", encoding="utf-8") as f:
        f.write(str(e))
    sys.exit(3)
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
            pass
