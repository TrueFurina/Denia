# -*- coding: utf-8 -*-
"""侦察 tools/omni 参考实现，确认 C++ 读取 bundle 的路径与格式约定。"""
import os, glob, time

for root in [r"H:\Comni\_internal\resources\tools\omni",
             r"H:\Comni\_internal\resources\build\bin\Release\tools\omni",
             r"H:\Comni\_internal\resources\build\bin\Release\tools"]:
    print("=" * 78)
    print(root, os.path.isdir(root))
    if not os.path.isdir(root):
        continue
    for dp, dns, fns in os.walk(root):
        for fn in sorted(fns):
            p = os.path.join(dp, fn)
            try:
                sz = os.path.getsize(p)
                mt = time.strftime('%m-%d %H:%M', time.localtime(os.path.getmtime(p)))
            except Exception:
                sz, mt = -1, "?"
            print("   %10d  %s  %s" % (sz, mt, os.path.relpath(p, root)))
        # 只下探两层
        if dp.count(os.sep) - root.count(os.sep) >= 2:
            dns[:] = []
