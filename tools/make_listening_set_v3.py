# -*- coding: utf-8 -*-
"""v3 试听集：修好测试台后的产出 + 停顿规整 + 高频补偿，全部等电平。

顺序设计（听的人只需依次往下）：
  01 目标参考音         —— 达妮娅本人（记住这个音色/清晰度）
  02 应用同构_自然节奏   —— 引擎真实行为（测试台与 app 同构后）
  03 应用同构_停顿规整   —— 只删静音，最流畅，最利于判音色
  04 停顿规整+高频补偿   —— 在 03 基础上补回 8-12k 空气感（清晰度）
  05 旧测试台(结巴原样)  —— 你之前听到的那批，作为问题对照
  06 旧测试台_停顿规整   —— 同样的音频，仅规整停顿
  07 原生底色对照        —— 不传参考音时的 MiniCPM 原生嗓
"""
import os
import subprocess
import sys
import numpy as np
import soundfile as sf

sys.path.insert(0, r"G:\ai对话\11\tools")
import hf_restore as HF  # noqa: E402

FF = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
FS = r"G:\ai对话\11\data\native_audio_test\final_sample"
REF = r"G:\ai对话\11\data\native_audio_test\ref_v2\mp3_8s_24k.wav"
DST = os.path.join(FS, "聆听_v3")
SR = 24000
TARGET_RMS_DB = -20.0
CLIP_S = 14.0


def trim(x, sr, thr_rel=0.02, pad=0.03):
    w = int(sr * 0.01)
    m = len(x) // w
    if m < 2:
        return x
    e = np.sqrt(np.mean(x[:m * w].reshape(m, w) ** 2, axis=1))
    idx = np.where(e > max(1e-5, e.max() * thr_rel))[0]
    if not len(idx):
        return x
    a = max(0, int((idx[0] * 0.01 - pad) * sr))
    b = min(len(x), int(((idx[-1] + 1) * 0.01 + pad) * sr))
    return x[a:b]


def norm(x, target_db=TARGET_RMS_DB):
    r = float(np.sqrt((x ** 2).mean()))
    if r < 1e-9:
        return x
    y = x * 10 ** ((target_db - 20 * np.log10(r)) / 20.0)
    pk = float(np.abs(y).max())
    if pk > 0.97:
        y = y / pk * 0.97
    return y


def load0(p):
    x, sr = sf.read(p, dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    return x, sr


def main():
    os.makedirs(DST, exist_ok=True)

    # 04：对 03 做高频补偿（EQ1 全量 + EQ3 温和）
    p03 = os.path.join(FS, "APP_A_smooth.wav")
    x03, sr03 = load0(p03)
    ref, _ = load0(REF)
    g1, _ = HF.build_gain(ref, x03, scale=1.0)
    y1 = HF.apply_gain(x03, g1)
    g3, _ = HF.build_gain(ref, x03, scale=0.7)
    y3 = HF.apply_gain(x03, g3)
    eq1 = os.path.join(FS, "APP_A_smooth_EQ1.wav")
    eq3 = os.path.join(FS, "APP_A_smooth_EQ3.wav")
    sf.write(eq1, y1, SR, subtype="PCM_16")
    sf.write(eq3, y3, SR, subtype="PCM_16")
    b0, f0 = HF.bandprof(x03)
    b1, f1 = HF.bandprof(y1)
    print("高频补偿: 8-12k  %+.1f → %+.1f dB   f99 %.0f → %.0f   (真源 %+.1f / f99 %.0f)"
          % (b0[2], b1[2], f0, f1, HF.bandprof(ref)[0][2], HF.bandprof(ref)[1]))

    ITEMS = [
        ("01_目标参考音_达妮娅本人", REF, None),
        ("02_应用同构_引擎自然节奏", os.path.join(FS, "APP_A.wav"), None),
        ("03_应用同构_停顿规整_最流畅", p03, None),
        ("04_停顿规整+高频补偿_EQ1", eq1, None),
        ("05_停顿规整+高频补偿_EQ3温和", eq3, None),
        ("06_旧测试台_结巴原样对照", os.path.join(FS, "A_FIXED.wav"), None),
        ("07_旧测试台_仅停顿规整", os.path.join(FS, "A_FIXED_smooth.wav"), None),
        ("08_原生底色对照_不传参考音", os.path.join(FS, "B_NATIVE_smooth.wav"), None),
    ]

    seq, made = [], []
    print()
    for label, p, _ in ITEMS:
        if not os.path.exists(p):
            print("  缺失 %s" % p)
            continue
        x, sr = load0(p)
        x = trim(x, SR)
        if len(x) > int(CLIP_S * SR):
            x = x[:int(CLIP_S * SR)]
        x = norm(x)
        x = np.concatenate([np.zeros(int(SR * 0.2), np.float32), x,
                            np.zeros(int(SR * 0.2), np.float32)])
        out = os.path.join(DST, label + ".wav")
        sf.write(out, x, SR, subtype="PCM_16")
        made.append(out)
        seq.append(x)
        seq.append(np.zeros(int(SR * 1.2), np.float32))
        print("  [%s] %.2fs rms=%.1f dBFS" % (label, len(x) / SR,
              20 * np.log10(np.sqrt((x ** 2).mean()))))

    p = os.path.join(DST, "试听_顺序_01至08.wav")
    sf.write(p, np.concatenate(seq[:-1]), SR, subtype="PCM_16")
    print()
    print("  顺序试听: %s  (%.1fs)" % (os.path.basename(p), os.path.getsize(p) / 2 / SR))
    print("  单段目录: %s  (%d 个)" % (DST, len(made)))
    print()
    print("  判读: 01 记住基准 → 02/03 音色应与 01 同一人 → 04/05 更清亮")
    print("        06 是你听过的结巴原样 → 07 同一音频仅规整停顿 → 08 原生嗓（应明显不同）")


if __name__ == "__main__":
    main()
