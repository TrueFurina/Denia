# -*- coding: utf-8 -*-
"""素材溯源审计：逐个交付文件核验「采样率/带宽/基频/md5」，并独立复算证明来源。
纯读，不写任何素材。
"""
import os, sys, wave, hashlib, subprocess
import numpy as np

FF = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
D = r"G:\ai对话\11\data\native_audio_test\src_candidates"
REF = r"G:\ai对话\11\assets\voices\daniya_ref.wav"
VID = r"G:\视频录制\Wuthering Waves\Wuthering Waves 2026.08.27 - 06.59.23.04.mp4"


def md5(p, n=1 << 20):
    h = hashlib.md5()
    with open(p, "rb") as f:
        while True:
            b = f.read(n)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def decode(path, sr=48000):
    r = subprocess.run([FF, "-v", "quiet", "-i", path, "-vn", "-ac", "1", "-ar", str(sr),
                        "-f", "s16le", "-"], capture_output=True)
    return np.frombuffer(r.stdout, dtype="<i2").astype(np.float32) / 32768.0


def wav_hdr(p):
    try:
        with wave.open(p, "rb") as w:
            return w.getnchannels(), w.getframerate(), w.getnframes()
    except Exception as e:
        return None


def band_ratio(x, sr, lo, hi, n=8192):
    if len(x) < n:
        return 0.0
    win = np.hanning(n).astype(np.float32)
    f = np.fft.rfftfreq(n, 1.0 / sr)
    m = (f >= lo) & (f < hi)
    tot = 0.0
    sel = 0.0
    c = 0
    for i in range(0, len(x) - n, n // 2):
        seg = x[i:i + n]
        psd = np.abs(np.fft.rfft(seg * win)) ** 2
        tot += psd.sum()
        sel += psd[m].sum()
        c += 1
        if c >= 120:
            break
    return float(sel / (tot or 1e-20))


def f0_ac(x, sr, fmin=80, fmax=520):
    n = int(sr * 0.04); hop = int(sr * 0.02); v = []
    for i in range(0, max(1, len(x) - n), hop):
        seg = x[i:i + n]
        if np.sqrt((seg ** 2).mean()) < 0.012:
            continue
        seg = seg - seg.mean()
        ac = np.correlate(seg, seg, mode="full")[n - 1:]
        lo = int(sr / fmax); hi = min(int(sr / fmin), len(ac) - 1)
        if hi <= lo:
            continue
        k = lo + int(np.argmax(ac[lo:hi]))
        if ac[0] > 0 and ac[k] / ac[0] > 0.35:
            v.append(sr / k)
    return float(np.median(v)) if v else 0.0


def cutoff(x, sr, thresh=-40.0, n=8192):
    """累计能量达 99.5% / 相对峰值跌到 thresh dB 的频率"""
    if len(x) < n:
        return 0, 0
    win = np.hanning(n).astype(np.float32)
    f = np.fft.rfftfreq(n, 1.0 / sr)
    psd = np.zeros(len(f))
    c = 0
    for i in range(0, len(x) - n, n // 2):
        psd += np.abs(np.fft.rfft(x[i:i + n] * win)) ** 2
        c += 1
        if c >= 120:
            break
    if c == 0:
        return 0, 0
    psd /= c
    db = 10 * np.log10(psd / psd.max() + 1e-20)
    above = np.where(db > thresh)[0]
    hard = float(f[above[-1]]) if len(above) else 0.0
    cum = np.cumsum(psd) / psd.sum()
    p995 = float(f[np.searchsorted(cum, 0.995)])
    return hard, p995


def main():
    print("=== 0) ffmpeg 能力 ===")
    v = subprocess.run([FF, "-hide_banner", "-version"], capture_output=True, text=True,
                       errors="replace").stdout
    for line in v.split("\n")[:2]:
        print("   ", line.strip())
    fl = subprocess.run([FF, "-hide_banner", "-filters"], capture_output=True, text=True,
                        errors="replace").stdout
    hits = [l.strip() for l in fl.split("\n") if "rubberband" in l.lower()]
    print("    rubberband 支持:", hits if hits else "无")
    lat = [l.strip() for l in fl.split("\n") if "librubberband" in l.lower()]
    if lat:
        print("   ", lat)

    print("\n=== 1) 参考音 ===")
    c, sr, nf = wav_hdr(REF)
    x = decode(REF, sr)
    h, p = cutoff(x, sr)
    print("    daniya_ref.wav  %dHz ch=%d  %.2fs  md5=%s" % (sr, c, nf / sr, md5(REF)[:12]))
    print("    F0=%.1fHz   -40dB截止=%.0fHz  99.5%%=%.0fHz  8-12k占比=%.4f%%" %
          (f0_ac(x, sr), h, p, band_ratio(x, sr, 8000, 12000) * 100))

    print("\n=== 2) src_candidates 全部文件溯源 ===")
    fs = sorted(os.listdir(D))
    print("    %-52s %8s %6s %6s %8s %7s %7s" % ("文件", "KB", "sr", "秒", "F0", ">8k%", ">12k%"))
    for f in fs:
        p_ = os.path.join(D, f)
        if not f.lower().endswith(".wav"):
            print("    %-52s %8.1f  (非wav)" % (f, os.path.getsize(p_) / 1024))
            continue
        hd = wav_hdr(p_)
        ch_, sr_, nf_ = hd if hd else (0, 0, 0)
        xx = decode(p_, sr_ or 48000)
        b8 = band_ratio(xx, sr_, 8000, 12000)
        b12 = band_ratio(xx, sr_, 12000, 16000)
        print("    %-52s %8.1f %6d %6.2f %8.1f %6.2f%% %6.2f%%" %
              (f[:52], os.path.getsize(p_) / 1024, sr_, len(xx) / (sr_ or 48000),
               f0_ac(xx, sr_ or 48000), b8 * 100, b12 * 100))

    print("\n=== 3) 独立复算：r07/r08 是否真出自录屏 ===")
    xv = decode(VID, 48000)
    sr = 48000
    for name, a, b in [("V0827_r07_16.0-17.1s_F0390.wav", 16.13, 17.00),
                       ("V0827_r08_18.8-19.4s_F0298.wav", 18.86, 19.30)]:
        a2 = max(0.0, a - 0.10); b2 = min(len(xv) / sr, b + 0.15)
        seg = xv[int(a2 * sr):int(b2 * sr)]
        tmp = os.path.join(os.environ.get("TEMP", "."), "_chk.wav")
        d = (np.clip(seg, -1, 1) * 32767).astype("<i2").tobytes()
        hdr = (b"RIFF" + (36 + len(d)).to_bytes(4, "little") + b"WAVEfmt " +
               (16).to_bytes(4, "little") + (1).to_bytes(2, "little") + (1).to_bytes(2, "little") +
               (48000).to_bytes(4, "little") + (96000).to_bytes(4, "little") + (2).to_bytes(2, "little") +
               (16).to_bytes(2, "little") + b"data" + len(d).to_bytes(4, "little"))
        open(tmp, "wb").write(hdr + d)
        m_now = md5(tmp)
        p_ = os.path.join(D, name)
        m_old = md5(p_) if os.path.exists(p_) else "缺失"
        print("    %s" % name)
        print("       独立从录屏切片 md5=%s" % m_now[:16])
        print("       实际交付文件   md5=%s   ->%s" %
              (m_old[:16], "一致，确实出自录屏" if m_now == m_old else "不一致！"))
        os.remove(tmp)

    print("\n=== 4) 录屏本段 vs 参考音：8k 以上有无能量（16k 源不可能有）===")
    segv = xv[int(16.03 * sr):int(17.15 * sr)]
    print("    录屏 r07 段 8-12k 占比 = %.3f%%" % (band_ratio(segv, sr, 8000, 12000) * 100))
    print("    参考音       8-12k 占比 = %.3f%%" % (band_ratio(x, sr, 8000, 12000) * 100))

    print("\n=== 5) MEMORY.md 体量 ===")
    mem = r"G:\ai对话\.workbuddy\memory\MEMORY.md"
    t = open(mem, "rb").read()
    print("    %d 字节 / %d 字符" % (len(t), len(t.decode("utf-8"))))


if __name__ == "__main__":
    main()
