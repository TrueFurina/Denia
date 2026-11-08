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


# 【2026-11-08 修·刀自己会空转】原来只判 `r.returncode != 0`，于是闸门**崩了**
# 也被算成"抓住"。实测：把 check_runtime_audio_guard.py 整个换成一句
# `raise RuntimeError("闸门自己崩了")`，本刀仍然逐条打印「已抓住：<无输出>」、
# 最后打印「N 刀全红」并 exit 0 —— **一个根本跑不起来的闸门被认证成"四刀全抓"**。
# 崩了和识别是两件事：前者说明闸门没跑，后者才说明判据在工作。
# 起点也必须先绿：恒红的闸门能让**每一把**刀都变红，那种"全红"什么都不证明。
def _judged(r) -> bool:
    """判定文件是「判出了问题」而不是「崩了」或「什么都没说」。

    【2026-11-08 第二版·第一版太严】第一版还要求「输出里没有 Traceback」，
    实测把**先印 FAIL、随后收尾时崩掉**的判定判成了"没抓住"：
      · `_qc_asr_mutation` ①②：印了 `FAIL lang='zh-cn'`，随后 vosk 桩抛
        `AttributeError: 'Model' object has no attribute '_handle'`
      · `_qc_config_bootstrap_mutation` B：印了 `FAIL 状态 created`，随后读临时
        `config.yaml` 抛 `FileNotFoundError`
    两次都是**仪器太窄**（本轮第三次）。真正的判别是**有没有点名**：
    退化桩只印一个 `exit 1`，一个字都没有 —— 那才叫"什么都没说"。
    """
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode != 0 and ("FAIL" in out or "✗" in out)


def _run_test():
    return subprocess.run([PY, "-u", TEST], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _baseline_green() -> bool:
    r = _run_test()
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode == 0 and "Traceback" not in out


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
    if not _baseline_green():
        print("FAIL 起点就不是绿的 —— 恒红的闸门能让每把刀都变红，这种\"全红\"不证明任何事")
        return 1
    print("  PASS 起点绿")
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
            caught = _judged(r)
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
