# -*- coding: utf-8 -*-
"""跑关系面板「原型下拉 + 最近事件列表」的行为真值表（node 侧在 `test_relation_panel.js`）。

为什么要这么绕：判据说的是"界面变成什么样"（下拉有几个选项、改完调了谁、事件行什么顺序），
静态查源码只能证明字符串在不在。本仓规矩是**把真值表交给 node 跑页面里的真 JS** ——
JS 侧**原样加载** `src/ui/web/redesign/relation_panel.js` 整文件，在假 DOM + 假 pywebview
里跑，不是照着逻辑再写一份（那只证明"我两次写得一样"）。

退出码：0 = 全过；1 = 有失败；77 = 本机没有 node（check_all 约定：单列跳过，不计通过）。
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = os.environ.get("DANIYA_NODE") or shutil.which("node") or ""
if not NODE:
    print("[SKIP] 找不到 node（行为真值表需要它）；可用 DANIYA_NODE 指定")
    raise SystemExit(77)

r = subprocess.run([NODE, os.path.join("tools", "test_relation_panel.js")], cwd=ROOT,
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
sys.stdout.write(r.stdout or "")
sys.stderr.write(r.stderr or "")
raise SystemExit(r.returncode)
