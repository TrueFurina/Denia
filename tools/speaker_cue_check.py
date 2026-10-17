# -*- coding: utf-8 -*-
"""与音高无关的说话人线索：
  A) 共振峰(F1/F2/F3)对比 —— 共振峰不随音高变化，是判断"同一个人"的关键
  B) 参考音 vs 录屏：倒谱包络的相关系数（在 0-4kHz 共同带宽上）
  C) 旧候选集(01~08) 的逐帧 F0 分布，看是否存在两个音高簇
"""
import os, subprocess
import numpy as np

FF = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
D = r"G:\ai对话\11\data\native_audio_test\src_candidates"
REF = r"G:\ai对话\11\assets\voices\daniya_ref.wav"
AN = 16000
NW = 512   # 32ms @16k，必须 <= 帧长 40ms（否则一帧都取不到）


def decode(p, sr=AN):
    r = subprocess.run([FF, "-v", "quiet", "-i", p, "-vn", "-ac", "1", "-ar", str(sr),
                        "-f", "s16le", "-"], capture_output=True)
    return np.frombuffer(r.stdout, dtype="<i2").astype(np.float32) / 32768.0


def voiced_frames(x, sr):
    n = int(sr * 0.04); hop = int(sr * 0.01); out = []
    for i in range(0, max(1, len(x) - n), hop):
        s = x[i:i + n]
        if np.sqrt((s ** 2).mean()) < 0.015:
            continue
        s = s - s.mean()
        ac = np.correlate(s, s, mode="full")[n - 1:]
        lo, hi = int(sr / 500), min(int(sr / 90), len(ac) - 1)
        if hi <= lo:
            continue
        k = lo + int(np.argmax(ac[lo:hi]))
        if ac[0] > 0 and ac[k] / ac[0] > 0.42:
            out.append((s, sr / k, ac[k] / ac[0]))
    return out


def avg_spectrum(x, sr):
    n = NW
    win = np.hanning(n).astype(np.float32)
    psd = np.zeros(n // 2 + 1); c = 0
    for s, f0, per in voiced_frames(x, sr):
        for i in range(0, max(1, len(s) - n), n // 2):
            seg = s[i:i + n]
            if len(seg) < n:
                break
            psd += np.abs(np.fft.rfft(seg * win)) ** 2; c += 1
    if c == 0:
        return None, None, 0, []
    psd /= c
    return psd, np.fft.rfftfreq(n, 1.0 / sr), c, None


def envelope_from_psd(psd, sr, n=NW, lifter_ms=1.35):
    S = np.log(psd + 1e-20)
    cep = np.fft.irfft(S, n=n)
    L = max(4, int(sr * lifter_ms / 1000.0))
    cep[L + 1:n - L] = 0.0
    return np.fft.rfft(cep).real, np.fft.rfftfreq(n, 1.0 / sr)


def peaks(f, env, lo=200, hi=4000, k=3):
    m = (f >= lo) & (f <= hi)
    fh, eh = f[m], env[m]
    idx = [i for i in range(1, len(eh) - 1) if eh[i] >= eh[i - 1] and eh[i] >= eh[i + 1]]
    idx = sorted(idx, key=lambda i: -eh[i])
    out = []
    for i in idx:
        if all(abs(fh[i] - x) > 250 for x in out):
            out.append(float(fh[i]))
        if len(out) >= k:
            break
    return sorted(out)


def report(title, x, sr, psd=None):
    if psd is None:
        psd, f, c, _ = avg_spectrum(x, sr)
    else:
        f = np.fft.rfftfreq(NW, 1.0 / sr)
    if psd is None:
        print("  %s: 无浊音帧" % title)
        return None, None
    env, fe = envelope_from_psd(psd, sr, NW)
    pk = peaks(fe, env)
    f0s = [p[1] for p in voiced_frames(x, sr)]
    f0 = float(np.median(f0s)) if f0s else 0.0
    print("  %-26s F0=%6.1fHz  帧数=%4d  共振峰: %s" %
          (title, f0, len(f0s), " / ".join("%.0f" % p for p in pk)))
    return env, fe


def corr_on(a, b, fe, hi=4000):
    m = fe <= hi
    a2 = a[m] - a[m].mean(); b2 = b[m] - b[m].mean()
    return float(a2 @ b2 / (np.linalg.norm(a2) * np.linalg.norm(b2) + 1e-12))


def main():
    print("=== A/B) 参考音 vs 录屏人声：共振峰与包络（都在 16kHz 下分析，带宽对齐）===")
    ref = decode(REF, AN)
    vid = decode(os.path.join(D, "V0827_r07_16.0-17.1s_F0390.wav"), AN)
    vid2 = decode(os.path.join(D, "V0827_全片27.0s_48k.wav"), AN)
    p_ref, f, _, _ = avg_spectrum(ref, AN)
    e_ref, fe = report("参考音 daniya_ref", ref, AN, p_ref)
    e_vid, _ = report("录屏 r07(16.0-17.1s)", vid, AN)
    e_all, _ = report("录屏 全片27s", vid2, AN)
    if e_ref is not None:
        print("  倒谱包络相关(0-4kHz):  参考vs r07 = %.3f   参考vs 全片 = %.3f" %
              (corr_on(e_ref, e_vid, fe), corr_on(e_ref, e_all, fe)))
        h1, h2 = ref[:len(ref) // 2], ref[len(ref) // 2:]
        p1, _, _, _ = avg_spectrum(h1, AN)
        p2, _, _, _ = avg_spectrum(h2, AN)
        if p1 is not None and p2 is not None:
            e1, _ = envelope_from_psd(p1, AN)
            e2, _ = envelope_from_psd(p2, AN)
            print("  【自比标定】参考音前半 vs 后半 = %.3f   录屏 r07 vs 全片 = %.3f" %
                  (corr_on(e1, e2, fe), corr_on(e_vid, e_all, fe)))

    print("\n=== C) 旧候选集 F0（看有没有两个音高簇）===")
    for fn in sorted(os.listdir(D)):
        if fn[:2] in ("01", "02", "03", "04", "05", "06", "07", "08"):
            x = decode(os.path.join(D, fn), AN)
            f0s = np.array([p[1] for p in voiced_frames(x, AN)])
            if len(f0s) == 0:
                print("  %-40s 无浊音帧" % fn); continue
            print("  %-40s 中位=%6.1f  10%%=%6.1f  90%%=%6.1f  帧=%d" %
                  (fn[:40], np.median(f0s), np.percentile(f0s, 10), np.percentile(f0s, 90), len(f0s)))
            hist, _ = np.histogram(f0s, bins=np.arange(100, 601, 50))
            print("     100-600Hz 直方(50Hz/格): %s" % " ".join(str(int(v)) for v in hist))


if __name__ == "__main__":
    main()
