# -*- coding: utf-8 -*-
"""停顿压缩力度扫描：选一个既连贯又不像"机关枪"的档位。"""
import os
import sys
import numpy as np
import soundfile as sf

sys.path.insert(0, r"G:\ai对话\11\tools")
import smooth_speech as S  # noqa: E402

MINE = r"G:\ai对话\11\data\native_audio_test\final_sample"
REF = r"G:\ai对话\11\data\native_audio_test\ref_v2\mp3_8s_24k.wav"

PRESETS = [
    (0.12, 0.20, 0.34),
    (0.10, 0.16, 0.28),
    (0.08, 0.13, 0.24),
    (0.06, 0.10, 0.20),
]

TARGETS = ["A_FIXED.wav", "A_FIXED_r2.wav", "A_FIXED_v2a.wav", "B_NATIVE.wav"]


def main():
    ref, rsr = sf.read(REF, dtype="float32")
    if ref.ndim > 1:
        ref = ref.mean(axis=1)
    print("=" * 118)
    print("基准（同一把尺子）")
    print("  天然参考音                    " + S.fmt("", S.stat_burst(ref)).strip())
    print("  [真机实测区间] 浊音中位 0.135~0.48s / 浊音占 63~74% / ≥0.15s静音 0.67~1.12 个/秒 / 最长 0.475~1.510s")
    print("=" * 118)
    for f in TARGETS:
        p = os.path.join(MINE, f)
        if not os.path.exists(p):
            continue
        x, sr = sf.read(p, dtype="float32")
        if x.ndim > 1:
            x = x.mean(axis=1)
        print()
        print(f"### {f}")
        print("  " + S.fmt("原始", S.stat_burst(x)))
        f0b = S.f0_median(x)
        for keep in PRESETS:
            y, removed = S.apply_cuts(x, S.plan_cuts(S.envelope(x), keep=keep))
            s = S.stat_burst(y)
            f0a = S.f0_median(y)
            print("  " + S.fmt(f"keep={keep} 删{removed/sr:.2f}s", s) + f"  ΔF0={f0a-f0b:+.1f}")
    print("=" * 118)


if __name__ == "__main__":
    main()
