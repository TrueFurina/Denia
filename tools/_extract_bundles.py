# -*- coding: utf-8 -*-
"""对多个候选参考音提取 prompt_bundle（spk/tokens/mel），用于与现有 bundle 做标定对比。"""
import os, sys, subprocess, time

VENV_PY = r"H:\daniya_voice_tools\client_venv\Scripts\python.exe"
SCRIPT = r"H:\daniya_voice_tools\extract_prompt_bundle.py"
MODEL_DIR = r"H:\daniya_voice_tools"

JOBS = [
    ("truevoice27s", r"G:\ai对话\11\data\native_audio_test\src_candidates\V0827_全片27.0s_48k.wav"),
    ("sapi_neutral", r"G:\ai对话\11\data\native_audio_test\INPUT_sapi_neutral.wav"),
    ("real_clean16k", r"H:\daniya_voice_tools\daniya_real_clean.wav"),
    ("old_ref", r"G:\ai对话\11\assets\voices\daniya_ref.wav"),
]

for tag, ref in JOBS:
    out = os.path.join(MODEL_DIR, "tmp_probe_" + tag)
    if not os.path.exists(ref):
        print("SKIP (missing): %s" % ref, flush=True)
        continue
    os.makedirs(out, exist_ok=True)
    t0 = time.time()
    cmd = [VENV_PY, SCRIPT, "--ref-audio", ref, "--model-dir", MODEL_DIR, "--output-dir", out]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    ok = r.returncode == 0
    print("[%s] rc=%s  %.1fs" % (tag, r.returncode, time.time() - t0), flush=True)
    print("   stdout:", (r.stdout or "").strip().replace("\n", " | ")[:400], flush=True)
    if not ok:
        print("   stderr:", (r.stderr or "").strip()[-800:], flush=True)
print("ALL DONE", flush=True)
