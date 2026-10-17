# -*- coding: utf-8 -*-
"""v2 控制实验：用真源参考音重烤 → 用真实通话链路生成 → 与线上版本对照。

两臂参考音：
  v2a = mp3_8s_24k     同一句话，但源头换成 44.1kHz 真源 + 电平标定到原厂基准
  v2b = dialogue_24k   达妮娅游戏台词 9.5s 窗（更长、更连续）

对照基准：现有 A_FIXED.wav（16k 派生参考音 + 未标定电平）
唯一变量 = 参考音（cache 与 LLM 层参考音同步更换）
"""
import os
import shutil
import subprocess
import sys
import time

PY = r"H:\daniya_voice_tools\client_venv\Scripts\python.exe"
R = r"G:\ai对话\11\tools"
REFDIR = r"G:\ai对话\11\data\native_audio_test\ref_v2"
CACHE = r"H:\minicpm models\MiniCPM-o-4_5-gguf\token2wav-gguf\prompt_cache.gguf"

VARIANTS = [
    ("v2a", os.path.join(REFDIR, "mp3_8s_24k.wav")),
    ("v2b", os.path.join(REFDIR, "dialogue_24k.wav")),
]


def log(m):
    print("[run_v2] " + m, flush=True)


def md5(p):
    import hashlib
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    log("=" * 96)
    log("v2 控制实验  TS=%s" % time.strftime("%Y%m%d_%H%M%S"))
    log("=" * 96)
    orig = md5(CACHE)
    log("实验前 live cache: %d B md5=%s" % (os.path.getsize(CACHE), orig))

    for tag, ref24 in VARIANTS:
        if not os.path.exists(ref24):
            log("!! 缺参考音 %s" % ref24); continue
        log("")
        log("#" * 96)
        log("# [%s] 参考音 = %s" % (tag, ref24))
        log("#" * 96)
        r = subprocess.run([PY, os.path.join(R, "bake_ref.py"), "--ref24", ref24, "--tag", tag])
        log("bake rc=%s" % r.returncode)
        if r.returncode != 0:
            log("!! 烘焙失败，跳过该臂"); continue
        log("烘焙后 live cache md5=%s" % md5(CACHE))

        import socket
        for _ in range(30):
            s = socket.socket(); s.settimeout(0.5)
            try:
                s.connect(("127.0.0.1", 22733)); s.close(); time.sleep(3)
            except Exception:
                break
        r = subprocess.run([PY, os.path.join(R, "gen_timbre_sample.py"), "A_FIXED",
                            "--ref=" + ref24, "--tag=" + tag])
        log("gen rc=%s" % r.returncode)
        time.sleep(5)

    log("")
    log("=" * 96)
    log("实验结束。live cache = %d B md5=%s（未还原，由上层决定最终装哪个）"
        % (os.path.getsize(CACHE), md5(CACHE)))
    log("实验前 live cache md5 = %s" % orig)
    log("=" * 96)


if __name__ == "__main__":
    main()
