# -*- coding: utf-8 -*-
"""导出一份录屏的全部可试听材料：全片 + 逐段有声音区（48kHz）。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from pick_clean_voice import decode, frame_feat, f0_ac, write_wav

OUT = r"G:\ai对话\11\data\native_audio_test\src_candidates"
RUNS = [
    (0.56, 1.00), (0.94, 1.38), (1.41, 1.85), (7.39, 7.91),
    (8.75, 9.32), (11.57, 12.09), (13.53, 14.05), (16.13, 17.00),
    (18.86, 19.30), (20.70, 21.09), (24.67, 25.06), (25.26, 25.74),
]


def main():
    path = sys.argv[1]
    pre = sys.argv[2] if len(sys.argv) > 2 else "V0827"
    x = decode(path)
    sr = 48000
    os.makedirs(OUT, exist_ok=True)

    # 1) 全片
    p = os.path.join(OUT, "%s_全片%.1fs_48k.wav" % (pre, len(x) / sr))
    write_wav(p, x)
    print("[全片] %s  %.2fs  48kHz mono" % (os.path.basename(p), len(x) / sr))

    # 2) 逐段
    print("\n[逐段有声音区]")
    for i, (a, b) in enumerate(RUNS):
        a2 = max(0.0, a - 0.10); b2 = min(len(x) / sr, b + 0.15)
        seg = x[int(a2 * sr):int(b2 * sr)]
        f0 = f0_ac(seg, sr)
        name = "%s_r%02d_%.1f-%.1fs_F0%.0f.wav" % (pre, i, a2, b2, f0)
        write_wav(os.path.join(OUT, name), seg)
        print("  %-40s %.2fs  F0=%6.1fHz  RMS=%.4f" %
              (name, b2 - a2, f0, np.sqrt((seg ** 2).mean())))

    # 3) 最干净的两段拼一个短参考（5s 级）
    best = [(16.13, 17.00), (11.57, 12.09)]
    buf = []
    for a, b in best:
        buf.append(x[int(a * sr):int(b * sr)]); buf.append(np.zeros(int(sr * 0.30), np.float32))
    st = np.concatenate(buf)
    p3 = os.path.join(OUT, "%s_试拼_%.1fs.wav" % (pre, len(st) / sr))
    write_wav(p3, st)
    print("\n[试拼] %s  %.2fs  F0=%.1fHz" % (os.path.basename(p3), len(st) / sr, f0_ac(st, sr)))


if __name__ == "__main__":
    main()
