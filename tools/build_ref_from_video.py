# -*- coding: utf-8 -*-
"""从 48kHz 录屏构建达妮娅新参考音（自然音高、无变调）。

要点：
 1. 帧级 VAD 挑"语音带占比高 + 低频低 + 够响"的连续段
 2. 按「起始顺序」拼接，段间 0.25s 静音，段边缘 15ms 淡入淡出
 3. ★ 避开筛选偏置：优先挑 F0 靠近全片中位音的段（喊叫句会拉高 F0）
 4. 每段 RMS 归一到该段集合中位数（±6dB 上限），再整体 RMS 对齐旧参考音（同带宽）
 5. 输出 48kHz（喂 bundle）与 16kHz（替换 daniya_ref.wav）两份
"""
import os, sys, subprocess
import numpy as np

FFMPEG = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
VID = r"G:\视频录制\Wuthering Waves\Wuthering Waves 2026.08.27 - 06.59.23.04.mp4"
OLD_REF = r"G:\ai对话\11\assets\voices\daniya_ref.wav"
OUTDIR = r"G:\ai对话\11\data\native_audio_test\ref_build"
SR = 48000

RMS_MIN = 0.016
SP_MIN = 0.78
LO_MAX = 0.09
RUN_MIN = 0.30
MERGE_GAP = 0.30
TARGET_S = 10.0
GAP_S = 0.25
FADE_MS = 15.0
F0_TOL = 0.22          # 只收 F0 在全片中位 ±22% 内的段（避开喊叫/极端句）


def decode(path, sr=SR, mono=True):
    cmd = [FFMPEG, "-v", "quiet", "-i", path, "-vn", "-ac", "1" if mono else "2",
           "-ar", str(sr), "-f", "s16le", "-"]
    r = subprocess.run(cmd, capture_output=True)
    if not r.stdout:
        raise RuntimeError("decode failed: " + r.stderr.decode("utf-8", "replace")[:300])
    return np.frombuffer(r.stdout, dtype="<i2").astype(np.float32) / 32768.0


def wav_write(path, x, sr):
    x = np.clip(x, -1.0, 1.0)
    d = (x * 32767.0).astype("<i2").tobytes()
    hdr = (b"RIFF" + (36 + len(d)).to_bytes(4, "little") + b"WAVEfmt " +
           (16).to_bytes(4, "little") + (1).to_bytes(2, "little") + (1).to_bytes(2, "little") +
           sr.to_bytes(4, "little") + (sr * 2).to_bytes(4, "little") + (2).to_bytes(2, "little") +
           (16).to_bytes(2, "little") + b"data" + len(d).to_bytes(4, "little"))
    open(path, "wb").write(hdr + d)


def resample(x, sr_in, sr_out):
    if sr_in == sr_out:
        return x.copy()
    cmd = [FFMPEG, "-v", "quiet", "-f", "f32le", "-ar", str(sr_in), "-ac", "1", "-i", "-",
           "-f", "f32le", "-ar", str(sr_out), "-ac", "1", "-"]
    r = subprocess.run(cmd, input=x.astype("<f4").tobytes(), capture_output=True)
    return np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)


def frame_feat(x, sr, n=8192, hop=2048):
    win = np.hanning(n).astype(np.float32)
    f = np.fft.rfftfreq(n, 1.0 / sr)
    idx = {"lo": (f >= 0) & (f < 120), "sp": (f >= 300) & (f < 3400),
           "mid": (f >= 120) & (f < 300), "hi": (f >= 6000) & (f < 8000),
           "xhi": (f >= 8000) & (f < 12000)}
    out = []
    for i in range(0, max(1, len(x) - n), hop):
        seg = x[i:i + n]
        if len(seg) < n:
            break
        psd = np.abs(np.fft.rfft(seg * win)) ** 2
        tot = psd.sum() or 1e-20
        d = {"t0": i / sr, "t1": (i + n) / sr, "rms": float(np.sqrt((seg ** 2).mean()))}
        for k, m in idx.items():
            d[k] = float(psd[m].sum() / tot) if m.any() else 0.0
        out.append(d)
    return out


def f0_of(x, sr, fmin=80, fmax=520):
    n = int(sr * 0.04); hop = int(sr * 0.02); v = []
    for i in range(0, max(1, len(x) - n), hop):
        seg = x[i:i + n]
        if np.sqrt((seg ** 2).mean()) < 0.012:
            continue
        seg = seg - seg.mean()
        ac = np.correlate(seg, seg, mode="full")[n - 1:]
        lo = int(sr / fmax); hi = min(int(sr / fmin), len(ac) - 1)
        if hi <= lo: continue
        k = lo + int(np.argmax(ac[lo:hi]))
        if ac[0] > 0 and ac[k] / ac[0] > 0.35:
            v.append(sr / k)
    return float(np.median(v)) if v else 0.0, len(v)


def bands(x, sr):
    n = 16384
    if len(x) < n:
        n = 1 << int(np.log2(max(256, len(x))))
    win = np.hanning(n).astype(np.float32)
    psd = np.zeros(n // 2 + 1); c = 0
    for i in range(0, max(1, len(x) - n), n // 2):
        s = x[i:i + n]
        if len(s) < n: break
        psd += np.abs(np.fft.rfft(s * win)) ** 2; c += 1
    psd /= max(1, c)
    f = np.fft.rfftfreq(n, 1.0 / sr)
    tot = psd.sum() or 1e-20
    bnds = [(0, 120), (120, 300), (300, 1000), (1000, 3400), (3400, 6000), (6000, 8000),
            (8000, 12000), (12000, 24000)]
    return [float(psd[(f >= a) & (f < b)].sum() / tot * 100) for a, b in bnds]


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    x = decode(VID)
    print("视频音轨: %.2fs @ %dHz" % (len(x) / SR, SR))

    feats = frame_feat(x, SR)
    runs = []
    for d in feats:
        ok = d["rms"] > RMS_MIN and d["sp"] > SP_MIN and d["lo"] < LO_MAX
        if ok:
            if runs and d["t0"] - runs[-1][1] <= MERGE_GAP:
                runs[-1][1] = d["t1"]
                runs[-1][2] += 1
            else:
                runs.append([d["t0"], d["t1"], 1])
    runs = [r for r in runs if r[1] - r[0] >= RUN_MIN]
    print("合格连续段 %d 个, 合计 %.2fs" % (len(runs), sum(b - a for a, b, _ in runs)))

    # 全片中位 F0（用所有合格段拼起来算，避免单段偏置）
    allseg = np.concatenate([x[int(a * SR):int(b * SR)] for a, b, _ in runs]) if runs else x
    med_f0, nvo = f0_of(allseg, SR)
    print("全片合格段中位 F0 = %.1f Hz (voiced frames=%d)" % (med_f0, nvo))
    print()

    rows = []
    for i, (a, b, _nfr) in enumerate(runs):
        s = x[int(a * SR):int(b * SR)]
        f0v, nv = f0_of(s, SR)
        br = bands(s, SR)
        rms = float(np.sqrt((s ** 2).mean()))
        rows.append(dict(i=i, a=a, b=b, dur=b - a, f0=f0v, rms=rms, br=br))
        print("  #%02d %6.2f-%6.2fs (%.2fs) F0=%6.1f RMS=%.4f  语音带=%5.1f%% 6-8k=%4.2f%% 8-12k=%4.2f%% %s"
              % (i, a, b, b - a, f0v, rms, br[2] + br[3], br[5], br[6],
                 "★" if (med_f0 and abs(f0v - med_f0) / med_f0 <= F0_TOL) else ""))

    # 选段：F0 在容差内 + 干净 + 稍长优先；按时间顺序拼
    pool = [r for r in rows if r["f0"] > 0 and med_f0 and abs(r["f0"] - med_f0) / med_f0 <= F0_TOL]
    if not pool:
        pool = [r for r in rows if r["f0"] > 0]
    print()
    print("进入拼接的段（F0 在 ±%.0f%% 内）: %s" % (F0_TOL * 100, [r["i"] for r in pool]))
    chosen, total = [], 0.0
    for r in sorted(pool, key=lambda r: r["a"]):
        if total >= TARGET_S:
            break
        chosen.append(r); total += r["dur"] + GAP_S
    print("选中 %d 段，预计 %.2fs" % (len(chosen), total))

    # 每段 RMS 归一到中位（限幅 ±6dB）
    med_rms = float(np.median([r["rms"] for r in chosen])) if chosen else 1.0
    buf = []
    fade = int(SR * FADE_MS / 1000)
    for r in chosen:
        s = x[int(r["a"] * SR):int(r["b"] * SR)].copy()
        g = np.clip(med_rms / (r["rms"] + 1e-9), 0.5, 2.0)
        s = s * g
        if len(s) > 2 * fade:
            s[:fade] *= np.linspace(0, 1, fade)
            s[-fade:] *= np.linspace(1, 0, fade)
        buf.append(s)
        buf.append(np.zeros(int(SR * GAP_S), np.float32))
    clip = np.concatenate(buf).astype(np.float32) if buf else x[:int(TARGET_S * SR)]
    print("拼接后 %.2fs" % (len(clip) / SR))

    # 整体 RMS 对齐旧参考音（在 16k 带宽下对齐，避免 8k+ 内容抬高整体 RMS）
    old = decode(OLD_REF, 16000)
    r_old = float(np.sqrt((old ** 2).mean()))
    clip16 = resample(clip, SR, 16000)
    r_new = float(np.sqrt((clip16 ** 2).mean()))
    g = r_old / (r_new + 1e-9)
    g = float(np.clip(g, 0.05, 20.0))
    print("RMS 对齐: 旧参考 %.4f / 新(0-8k) %.4f -> 增益 %.3f" % (r_old, r_new, g))
    clip = clip * g
    peak = float(np.abs(clip).max())
    if peak > 0.99:
        clip *= 0.99 / peak
        print("  峰值限幅 -> %.3f" % peak)

    p48 = os.path.join(OUTDIR, "daniya_ref_new_48k.wav")
    wav_write(p48, clip, SR)
    p16 = os.path.join(OUTDIR, "daniya_ref_new_16k.wav")
    wav_write(p16, resample(clip, SR, 16000), 16000)

    f0f, _ = f0_of(clip, SR)
    br = bands(clip, SR)
    print()
    print("=" * 100)
    print("产出: %s  (%.2fs @48k, peak=%.3f)" % (p48, len(clip) / SR, float(np.abs(clip).max())))
    print("      %s" % p16)
    print("最终 F0 = %.1f Hz (旧参考 238.8 / 视频全片 263.7)" % f0f)
    print("频带: " + "  ".join("%s=%.2f%%" % (n, v) for n, v in
          zip(["0-120", "120-300", "300-1k", "1-3.4k", "3.4-6k", "6-8k", "8-12k", "12-24k"], br) if v > 0.001))
    print("=" * 100)


if __name__ == "__main__":
    main()
