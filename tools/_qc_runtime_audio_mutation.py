# -*- coding: utf-8 -*-
"""「通话录音不进包」闸门的变异验证：每把刀都必须被抓住。

跑法：.venv/Scripts/python.exe -u tools/_qc_runtime_audio_mutation.py
"""
from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
PY = ".venv/Scripts/python.exe"
TEST = "tools/check_runtime_audio_guard.py"


def E(s: str) -> bytes:
    return s.encode("utf-8")


MUTS = [
    ("A 把录音降级回 WARN（发布红线失守）",
     "tools/check_package_secrets.py",
     E('                blocking.append(f"包内含通话录音（用户语音，严禁发布）：{rel}")\n'),
     E('                warnings.append(f"包内含通话录音：{rel}")\n')),

    ("B 装配时不再排除音频文件",
     "tools/make_dist.py",
     E("        elif in_audio_dir and n.lower().endswith(RUNTIME_AUDIO_SUFFIX):\n"),
     E("        elif False:\n")),

    ("C 清理函数变成空转（--force 残留留在包里）",
     "tools/make_dist.py",
     E("                os.remove(p)\n                killed.append(os.path.relpath(p, out_dir))\n"),
     E("                pass\n")),

    ("D main() 不再调用清理",
     "tools/make_dist.py",
     E("    _audio = purge_runtime_audio(out)\n"),
     E("    _audio = []\n")),
]


def main() -> int:
    files = sorted({m[1] for m in MUTS})
    orig = {p: io.open(p, "rb").read() for p in files}
    h0 = {p: hashlib.sha256(b).hexdigest() for p, b in orig.items()}
    bad = 0
    for name, path, old, new in MUTS:
        n = orig[path].count(old)
        if n != 1:
            print("[跳过] %s：锚点命中 %d 次" % (name, n))
            bad += 1
            continue
        io.open(path, "wb").write(orig[path].replace(old, new, 1))
        try:
            r = subprocess.run([PY, "-u", TEST], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=180)
            caught = r.returncode != 0
            lines = [l.strip() for l in (r.stdout or "").splitlines() if l.strip()]
            why = [l for l in lines if l.startswith("FAIL")] or lines or ["<无输出>"]
            print(name)
            print("   -> " + ("已抓住：" + why[-1][:96] if caught else "没抓住（闸门失效）"))
            if not caught:
                bad += 1
        finally:
            for p in files:
                io.open(p, "wb").write(orig[p])

    ok = all(hashlib.sha256(io.open(p, "rb").read()).hexdigest() == h
             for p, h in h0.items())
    print("\n还原字节一致：%s" % ok)
    if not ok:
        bad += 1
    print("%d 刀全红" % len(MUTS) if bad == 0 else "有 %d 处异常" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
