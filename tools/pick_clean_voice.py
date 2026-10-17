# -*- coding: utf-8 -*-
"""从录屏里挑连续干净人声段，评估其与目标音色(daniya_ref)的接近度，并导出 48kHz 样本。
用法: python tools/pick_clean_voice.py "<video>" [前缀]
"""
import os, sys, math, subprocess
import numpy as np

FFMPEG = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
OUT = r"G:\ai对话\11\data\native_audio_test\src_candidates"
REF = r"G:\ai对话\11\assets\voices\daniya_ref.wav"

# 合格帧判据：够响 + 语音带占比高 + 低频(BGM/环境)<阈值
RMS_MIN = 0.016
SP_MIN = 0.78
LO_MAX = 0.09
RUN_MIN = 0.35
CMP_FMAX = 8000.0   # 与 16k 参考音对比只用到 8k


def decode(path, sr=48000, mono=True):
    cmd = [FFMPEG, "-v", "quiet", "-i", path, "-vn", "-ac", "1" if mono else "2", "-ar", str(sr), "-f", "s16le", "-"]
    r = subprocess.run(cmd, capture_output=True)
    return np.frombuffer(r.stdout, dtype="<i2").astype(np.float32) / 32768.0


def write_wav(path, x, sr=48000):
    x = np.clip(x, -1, 1)
    d = (x * 32767).astype("<i2").tobytes()
    hdr = (b"RIFF" + (36 + len(d)).to_bytes(4, "little") + b"WAVEfmt " +
           (16).to_bytes(4, "little") + (1).to_bytes(2, "little") + (1).to_bytes(2, "little") +
           sr.to_bytes(4, "little") + (sr * 2).to_bytes(4, "little") + (2).to_bytes(2, "little") +
           (16).to_bytes(2, "little") + b"data" + len(d).to_bytes(4, "little"))
    open(path, "wb").write(hdr + d)


def frame_feat(x, sr, n=8192, hop=2048):
    win = np.hanning(n).astype(np.float32)
    f = np.fft.rfftfreq(n, 1.0 / sr)
    idx = {k: ((f >= a) & (f < b)) for a, b, k in
           [(0, 120, "lo"), (120, 300, "mid"), (300, 3400, "sp"), (6000, 8000, "hf6"), (8000, 12000, "hf8")]}
    out = []
    for i in range(0, max(1, len(x) - n), hop):
        seg = x[i:i + n]
        if len(seg) < n: break
        rms = float(np.sqrt((seg ** 2).mean()))
        psd = np.abs(np.fft.rfft(seg * win)) ** 2
        tot = psd.sum() or 1e-20
        d = {"t": i / sr, "rms": rms}
        for k, m in idx.items():
            d[k] = float(psd[m].sum() / tot)
        out.append(d)
    return out


def f0_ac(x, sr, fmin=80, fmax=520):
    n = int(sr * 0.04); hop = int(sr * 0.02); v = []
    for i in range(0, max(1, len(x) - n), hop):
        seg = x[i:i + n]
        if np.sqrt((seg ** 2).mean()) < 0.012: continue
        seg = seg - seg.mean()
        ac = np.correlate(seg, seg, mode="full")[n - 1:]
        lo = int(sr / fmax); hi = min(int(sr / fmin), len(ac) - 1)
        if hi <= lo: continue
        k = lo + int(np.argmax(ac[lo:hi]))
        if ac[0] > 0 and ac[k] / ac[0] > 0.35: v.append(sr / k)
    return float(np.median(v)) if v else 0.0


def ltas(x, sr, nmel=40, fmax=CMP_FMAX):
    """对数 Mel 长时平均谱（0-fmax），均值方差归一化，用于音色相似度"""
    n = 8192
    if len(x) < n: return None
    win = np.hanning(n).astype(np.float32)
    psd = np.zeros(n // 2 + 1); c = 0
    for i in range(0, max(1, len(x) - n), n // 4):
        seg = x[i:i + n]
        if len(seg) < n: break
        psd += np.abs(np.fft.rfft(seg * win)) ** 2; c += 1
    if c == 0: return None
    psd /= c
    f = np.fft.rfftfreq(n, 1.0 / sr)
    m = f <= fmax
    fm, pm = f[m], psd[m]
    mel = 2595 * np.log10(1 + fm / 700.0)
    edges = np.linspace(mel.min(), mel.max(), nmel + 1)
    o = []
    for i in range(nmel):
        mm = (mel >= edges[i]) & (mel < edges[i + 1])
        o.append(pm[mm].sum() if mm.any() else 1e-12)
    lo = np.log(np.array(o) + 1e-12)
    return lo - lo.mean()


def cos(a, b):
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))


def main():
    path = sys.argv[1]
    pre = sys.argv[2] if len(sys.argv) > 2 else "V"
    x = decode(path)
    sr = 48000
    print("素材: %s" % os.path.basename(path))
    print("时长 %.2fs  码率参考见体检输出" % (len(x) / sr))

    ref = decode(REF, 16000)
    ref_l = ltas(ref, 16000)
    ref_f0 = f0_ac(ref, 16000)
    print("目标参考音 daniya_ref.wav: 8.03s  F0=%.1f Hz (16kHz 窄带)" % ref_f0)

    # 用整段有声音频做 LTAS 基线的信噪参考
    feats = frame_feat(x, sr)
    ok = [d for d in feats if d["rms"] > RMS_MIN and d["sp"] > SP_MIN and d["lo"] < LO_MAX]
    runs = []
    for d in feats:
        if d["rms"] > RMS_MIN and d["sp"] > SP_MIN and d["lo"] < LO_MAX:
            if runs and d["t"] - runs[-1][1] < 0.06:
                runs[-1][1] = d["t"]
            else:
                runs.append([d["t"], d["t"]])
    runs = [r for r in runs if r[1] - r[0] >= RUN_MIN - 0.06]
    print("合格帧 %d/%d → 连续段 %d 个 (判据 rms>%.3f 语音带>%.0f%% <120Hz<%.0f%%)" %
          (len(ok), len(feats), len(runs), RMS_MIN, SP_MIN * 100, LO_MAX * 100))
    print()
    print("idx  起(s)   止(s)  长(s)  F0(Hz) LTAS余弦  RMS    语音带  6-8k   8-12k  判定")
    picked = []
    for i, (a, b) in enumerate(runs):
        pad = 0.10
        a2 = max(0.0, a - pad); b2 = min(len(x) / sr, b + pad + 0.15)
        seg = x[int(a2 * sr):int(b2 * sr)]
        f0v = f0_ac(seg, sr)
        l = ltas(seg, sr)
        c = cos(l, ref_l) if l is not None else 0.0
        ff = [d for d in feats if a - 1e-6 <= d["t"] <= b + 1e-6]
        sp = np.mean([d["sp"] for d in ff]) if ff else 0
        h6 = np.mean([d["hf6"] for d in ff]) if ff else 0
        h8 = np.mean([d["hf8"] for d in ff]) if ff else 0
        rms = float(np.sqrt((seg ** 2).mean()))
        verdict = "★很像" if (c > 0.93 and abs(f0v - ref_f0) < 30) else ("~接近" if c > 0.88 else "✗不像")
        print("%3d  %6.2f  %6.2f  %5.2f  %6.1f  %7.4f  %.4f  %5.1f%%  %4.2f%%  %4.2f%%  %s" %
              (i, a2, b2, b2 - a2, f0v, c, rms, sp * 100, h6 * 100, h8 * 100, verdict))
        picked.append((i, a2, b2, f0v, c, sp, h6, h8, rms, seg))

    os.makedirs(OUT, exist_ok=True)
    print("\n--- 导出全部合格段（48kHz, 供人工试听）---")
    for i, a2, b2, f0v, c, sp, h6, h8, rms, seg in picked:
        name = "%s_%02d_%.1f-%.1fs_F0%.0f_cos%.2f.wav" % (pre, i, a2, b2, f0v, c)
        write_wav(os.path.join(OUT, name), seg)
        print("  %s" % name)

    # 总装：优先"像 + 干净 + 长"，拼到 ~10s
    print("\n--- 总装（像+干净，拼到 ~10s，段间 0.35s 静音）---")
    cand = sorted(picked, key=lambda r: (-(r[4] * 0.6 + r[5] * 0.4), -r[8]))
    buf, total, chosen = [], 0.0, []
    for i, a2, b2, f0v, c, sp, h6, h8, rms, seg in sorted(cand, key=lambda r: r[1]):
        if total >= 10.0: break
        buf.append(seg); buf.append(np.zeros(int(sr * 0.35), np.float32))
        total += (b2 - a2) + 0.35
        chosen.append(i)
    if buf:
        st = np.concatenate(buf)
        nm = "%s_总装_%.1fs.wav" % (pre, len(st) / sr)
        write_wav(os.path.join(OUT, nm), st)
        print("  %s  (用了段 %s)  F0=%.1f  LTAS余弦=%.4f" %
              (nm, chosen, f0_ac(st, sr), cos(ltas(st, sr), ref_l)))

    print("\n--- 参考音对照（当前线上 16kHz 参考音）---")
    print("  daniya_ref.wav  F0=%.1fHz  RMS=%.4f  peak=%.4f" %
          (ref_f0, np.sqrt((ref ** 2).mean()), np.abs(ref).max()))


if __name__ == "__main__":
    main()
