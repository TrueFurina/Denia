# -*- coding: utf-8 -*-
"""跑设置页「关于」分类的行为真值表（node 侧实现在 `tools/test_about_tier.js`）。

为什么要这么绕：那两条判据（版本号显示、开机自启开关）说的都是"点了以后界面变成
什么样"，静态查源码只能证明字符串在不在。本仓的规矩是**把真值表交给 node 跑页面里
的真 JS** —— JS 测试会从 `ui_redesign_settings.html` 里**原样抽出** `aboutTier`
那个 IIFE 在假 DOM 里跑，不是照着逻辑再写一份（那只证明"我两次写得一样"）。

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

r = subprocess.run([NODE, os.path.join("tools", "test_about_tier.js")], cwd=ROOT,
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
sys.stdout.write(r.stdout or "")
sys.stderr.write(r.stderr or "")
raise SystemExit(r.returncode)
