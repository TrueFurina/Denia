# -*- coding: utf-8 -*-
"""标定 danya 角色的高频补偿曲线 → assets/voice_eq/danya.json

hf_eq.py 的接线已落地（config duplex_hf_eq:true，键=角色id=danya），
只差曲线文件：没有曲线 ⇒ 原样播（fail-safe）。本脚本把曲线算出来并落盘，
重启后即对通话产出生效，且**不影响双工交互**。

【亮度目标为什么用"旧 ref"而不是新素材】
新素材(8月13日) f95=5672 比旧 ref 6453 略暗（mp3 压缩所致）。若拿它当 EQ 目标，
补偿后也会跟着偏暗 ⇒ 用**同一角色、更亮、更接近真源**的旧 ref 作亮度基准，
新素材只负责"音色/韵律/内容"的条件化。这就是前面说的"混合目标"。

用法（须 client_venv）：
    H:\\daniya_voice_tools\\client_venv\\Scripts\\python.exe tools/_calib_eq_danya.py
"""
import os
import sys
import glob
import json
import wave
import shutil
from datetime import datetime

import numpy as np

ROOT = r"G:\ai对话\11"
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "src"))
import hf_restore as HF  # noqa: E402

KEY = "danya"
CURVE_DIR = os.path.join(ROOT, "assets", "voice_eq")
CURVE = os.path.join(CURVE_DIR, "%s.json" % KEY)
OUTDIR = os.path.join(ROOT, "data", "duplex_out")

# 亮度基准：同一角色、更亮的那份
REF_BRIGHT = os.path.join(ROOT, "assets", "voices", "daniya_ref.wav")
# 条件化用的新素材（仅作对照打印，不参与曲线）
REF_NEW = os.path.join(ROOT, "assets", "voices", "daniya_ref_0813_24k.wav")

SCALE = 0.7          # EQ3 温和档（可调：0.6 更保守 / 1.0 更猛）
SR = 24000
MAX_TOTAL = 26.0


def load_wav(p):
    with wave.open(p, "rb") as w:
        nch, sw, sr = w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(w.getnframes())
    a = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    if nch > 1:
        a = a.reshape(-1, nch).mean(axis=1)
    return a, sr


def build_output_sample():
    """把最近一通电话的产出拼成一段，作为"待补偿侧"的统计样本。"""
    subs = sorted(os.listdir(OUTDIR))
    latest = os.path.join(OUTDIR, subs[-1])
    ws = sorted(glob.glob(os.path.join(latest, "*.wav")))
    parts, total = [], 0.0
    gap = np.zeros(int(SR * 0.35), np.float64)
    for p in ws:
        a, sr = load_wav(p)
        if sr != SR or len(a) / sr < 1.2:
            continue
        if total + len(a) / sr > MAX_TOTAL:
            break
        parts.append(a)
        parts.append(gap)
        total += len(a) / sr + 0.35
    if not parts:
        return None, 0
    return np.concatenate(parts).astype(np.float32), len(parts) // 2


def main():
    os.makedirs(CURVE_DIR, exist_ok=True)
    ref, rsr = load_wav(REF_BRIGHT)
    print("① 亮度基准：%s  sr=%d  %.1fs" % (os.path.basename(REF_BRIGHT), rsr, len(ref) / rsr))
    rb, rf99 = HF.bandprof(ref)
    print("   真源 3.4-6k/6-8k/8-12k = %s  f99=%.0f" % (np.round(rb, 1), rf99))
    if os.path.isfile(REF_NEW):
        nw, nsr = load_wav(REF_NEW)
        nb, nf99 = HF.bandprof(nw)
        print("   （新素材仅作条件化用，不参与曲线：f99=%.0f）" % nf99)

    out, n = build_output_sample()
    if out is None:
        print("❌ 没有可用的产出样本")
        return 2
    ob, of99 = HF.bandprof(out)
    print("\n② 待补偿侧：拼接 %d 句  f99=%.0f  3.4-6k/6-8k/8-12k = %s"
          % (n, of99, np.round(ob, 1)))

    print("\n③ 计算曲线（scale=%.1f）" % SCALE)
    g, f = HF.build_gain(ref, out, scale=SCALE)
    fq = [500, 1000, 2000, 3000, 4000, 6000, 8000, 10000, 11000]
    print("   " + "  ".join("%dHz:%+.1f" % (q, float(g[np.searchsorted(f, q)])) for q in fq))

    # 落盘
    if os.path.isfile(CURVE):
        bak = CURVE + ".bak_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(CURVE, bak)
        print("\n   已存在的曲线先备份：%s" % os.path.basename(bak))
    payload = {
        "key": KEY,
        "freqs": [float(x) for x in f],
        "gains_db": [float(x) for x in g],
        "trim_db": 0.0,
        "source": {
            "ref_brightness": os.path.basename(REF_BRIGHT),
            "scale": SCALE,
            "built_at": datetime.now().isoformat(timespec="seconds"),
        },
    }
    with open(CURVE, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False)
    print("   ✅ 曲线已写入 %s  (%d bins, %.1f KB)"
          % (CURVE, len(f), os.path.getsize(CURVE) / 1024))

    # 自检：用 hf_eq 的接口读回来，并试补偿一段
    sys.path.insert(0, os.path.join(ROOT, "src", "voice"))
    try:
        import hf_eq
        st = hf_eq.curve_status(KEY)
        print("\n④ 自检（通过 hf_eq.curve_status 读回）")
        print("   available=%s  bins=%s  gain@4k=%s  gain@8k=%s  gain@11k=%s"
              % (st.get("available"), st.get("bins"), st.get("gain_at_4k"),
                 st.get("gain_at_8k"), st.get("gain_at_11k")))
        y, status = hf_eq.apply_pcm_f32_safe(out.astype(np.float32).tobytes(), KEY)
        print("   apply_pcm_f32_safe → status=%s" % status)
        if status == "applied":
            yy = np.frombuffer(y, dtype=np.float32)
            yb, yf99 = HF.bandprof(yy)
            print("   补偿后 3.4-6k/6-8k/8-12k = %s  f99=%.0f  （补偿前 %s / %.0f）"
                  % (np.round(yb, 1), yf99, np.round(ob, 1), of99))
            prev = os.path.join(ROOT, "data", "高频补偿试听_长样本_20260923",
                                "03_用线上曲线补偿后_长样本.wav")
            import soundfile as sf
            sf.write(prev, yy, SR, subtype="PCM_16")
            print("   试听样本：%s" % prev)
    except Exception as e:
        print("   自检失败：%r" % e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
