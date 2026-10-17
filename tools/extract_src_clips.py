# -*- coding: utf-8 -*-
"""细扫一个录屏，挑出最干净的人声区间并切成交付样本（48kHz）。
用法: python tools/extract_src_clips.py "<video>" <起始> <时长> [标签]
"""
import os, sys, subprocess, math
import numpy as np

FFMPEG = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
OUT = r"G:\ai对话\11\data\native_audio_test\src_candidates"


def decode(path, sr=48000, t0=None, dur=None):
    cmd = [FFMPEG, "-v", "quiet"]
    if t0 is not None: cmd += ["-ss", str(t0)]
    cmd += ["-i", path]
    if dur is not None: cmd += ["-t", str(dur)]
    cmd += ["-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    r = subprocess.run(cmd, capture_output=True)
    return np.frombuffer(r.stdout, dtype="<i2").astype(np.float32) / 32768.0


def bands(x, sr):
    L = len(x)
    if L < 2048: return {}
    n = 1 << int(math.floor(math.log2(min(L, 32768))))
    if n < 2048: return {}
    win = np.hanning(n).astype(np.float32)
    psd = np.zeros(n // 2 + 1)
    cnt = 0
    hop = max(1, n // 2)
    for i in range(0, max(1, L - n + 1), hop):
        seg = x[i:i + n]
        if len(seg) < n: break
        psd += np.abs(np.fft.rfft(seg * win)) ** 2
        cnt += 1
    if cnt == 0:
        seg = x[:n] * win
        psd = np.abs(np.fft.rfft(seg)) ** 2
        cnt = 1
    psd /= cnt
    f = np.fft.rfftfreq(n, 1.0 / sr)
    tot = psd.sum() or 1e-20
    o = {}
    for lo, hi, k in [(0, 120, "b0_120"), (120, 300, "b120_300"), (300, 3400, "b300_3400"),
                      (3400, 6000, "b3400_6000"), (6000, 8000, "b6000_8000"), (8000, 12000, "b8000_12000"),
                      (12000, 16000, "b12000_16000")]:
        m = (f >= lo) & (f < hi)
        o[k] = float(psd[m].sum() / tot)
    return o


def f0(x, sr, fmin=80, fmax=500):
    n = int(sr * 0.04); hop = int(sr * 0.02)
    v = []
    for i in range(0, max(1, len(x) - n), hop):
        seg = x[i:i + n]
        if np.sqrt((seg ** 2).mean()) < 0.012: continue
        seg = seg - seg.mean()
        ac = np.correlate(seg, seg, mode="full")[n - 1:]
        lo = int(sr / fmax); hi = min(int(sr / fmin), len(ac) - 1)
        if hi <= lo: continue
        k = lo + int(np.argmax(ac[lo:hi]))
        if ac[0] > 0 and ac[k] / ac[0] > 0.35:
            v.append(sr / k)
    return float(np.median(v)) if v else 0.0


def mel_env(x, sr, nmel=40):
    """Mel 谱包络（对数值），用于跨采样率比音色"""
    n = 1 << 14
    win = np.hanning(n).astype(np.float32)
    psd = np.zeros(n // 2 + 1); cnt = 0
    for i in range(0, max(1, len(x) - n), n // 4):
        seg = x[i:i + n]
        if len(seg) < n: break
        psd += np.abs(np.fft.rfft(seg * win)) ** 2; cnt += 1
    if cnt == 0: return None
    psd /= cnt
    f = np.fft.rfftfreq(n, 1.0 / sr)
    mel = 2595 * np.log10(1 + f / 700.0)
    edges = np.linspace(mel.min(), mel.max(), nmel + 1)
    out = []
    for i in range(nmel):
        m = (mel >= edges[i]) & (mel < edges[i + 1])
        out.append(psd[m].sum() if m.any() else 1e-12)
    o = np.log(np.array(out) + 1e-12)
    return (o - o.mean()) / (o.std() + 1e-12)


def write_wav(path, x, sr=48000):
    x = np.clip(x, -1, 1)
    d = (x * 32767).astype("<i2").tobytes()
    hdr = b"RIFF" + (36 + len(d)).to_bytes(4, "little") + b"WAVEfmt " + \
          (16).to_bytes(4, "little") + (1).to_bytes(2, "little") + (1).to_bytes(2, "little") + \
          sr.to_bytes(4, "little") + (sr * 2).to_bytes(4, "little") + (2).to_bytes(2, "little") + \
          (16).to_bytes(2, "little") + b"data" + len(d).to_bytes(4, "little")
    open(path, "wb").write(hdr + d)


def main():
    path = sys.argv[1]
    if len(sys.argv) >= 4:
        t0 = float(sys.argv[2]); dur = float(sys.argv[3])
        tag = sys.argv[4] if len(sys.argv) > 4 else "clip"
        x = decode(path, 48000, t0, dur)
        b = bands(x, 48000)
        print("t=%.1f dur=%.1f  F0=%.1f  RMS=%.4f  语音带=%.2f%%  8-12k=%.2f%%" % (
            t0, dur, f0(x, 48000), np.sqrt((x ** 2).mean()),
            b.get("b300_3400", 0) * 100, b.get("b8000_12000", 0) * 100))
        out = os.path.join(OUT, tag + ".wav")
        write_wav(out, x)
        print("->", out)
        return

    # 细节扫描模式
    x = decode(path, 48000)
    sr = 48000
    print("时长 %.2fs  全片 F0=%.1f Hz" % (len(x) / sr, f0(x, sr)))
    W = 0.5; step = 0.25
    rows = []
    for t in np.arange(0, max(0.01, len(x) / sr - W), step):
        seg = x[int(t * sr):int((t + W) * sr)]
        r = np.sqrt((seg ** 2).mean())
        if r < 0.006: continue
        b = bands(seg, sr)
        rows.append((t, r, b.get("b0_120", 0), b.get("b120_300", 0), b.get("b300_3400", 0),
                     b.get("b6000_8000", 0), b.get("b8000_12000", 0)))
    print("t\tRMS\t<120\t120-300\t语音\t6-8k\t8-12k")
    for r in rows:
        print("%5.2f\t%.4f\t%5.1f%%\t%5.1f%%\t%5.1f%%\t%4.2f%%\t%4.2f%%" % (
            r[0], r[1], r[2] * 100, r[3] * 100, r[4] * 100, r[5] * 100, r[6] * 100))


if __name__ == "__main__":
    main()
