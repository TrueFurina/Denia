# -*- coding: utf-8 -*-
"""找音色基准文件：达妮娅真源 / MiniCPM 原生签名 / 任何疑似原生产出。"""
import os
import glob

ROOT = r"G:\ai对话\11"
HINTS = ("signature", "native", "minicpm", "sig", "default", "factory", "origin")


def main():
    print("① assets/voices 下的 wav：")
    for p in sorted(glob.glob(os.path.join(ROOT, "assets", "voices", "*.wav"))):
        print("   %-46s %8.1f KB" % (os.path.basename(p), os.path.getsize(p) / 1024))

    print("\n② 全盘搜含 signature/native/minicpm/default/factory 的 wav：")
    hits = []
    for base in (ROOT, os.path.join(ROOT, "data"), os.path.join(ROOT, "assets")):
        for dp, dn, fn in os.walk(base):
            if any(x in dp.lower() for x in ("node_modules", ".git", "__pycache__")):
                continue
            for f in fn:
                if f.lower().endswith(".wav") and any(h in f.lower() for h in HINTS):
                    hits.append(os.path.join(dp, f))
    if not hits:
        print("   （未找到）")
    for p in sorted(hits, key=os.path.getmtime)[-25:]:
        print("   %-64s %8.1f KB" % (os.path.relpath(p, ROOT), os.path.getsize(p) / 1024))

    print("\n③ 早期「原生嗓」产出候选（data 下名字带 native/sig 的目录）：")
    for p in sorted(glob.glob(os.path.join(ROOT, "data", "*", "*.wav"))):
        low = p.lower()
        if any(h in low for h in ("native", "sig", "原声", "原生")):
            print("   %s" % os.path.relpath(p, ROOT))
    return 0


if __name__ == "__main__":
    main()
