# -*- coding: utf-8 -*-
"""体检新参考素材（22s mp3），并与当前 daniya_ref.wav 对比，判断值不值得换。

判据（本项目已积累）：
  时长        >=10s 才好（现用 7.04s 偏短）
  起头        不能从字中间截（现用首 0.2s 峰值 0.7758 = 满幅，是硬切）
  电平        RMS 0.05~0.15，峰值 <0.99
  带宽        f95 越高越能带出亮度（真源 6453）
  F0          应与角色设定接近（达妮娅真源 ~240，素材 262~271）
"""
import os
import sys
import glob
import wave
import subprocess

import numpy as np

ROOT = r"G:\ai对话\11"
SRC_MP3 = r"G:\微信聊天文件\xwechat_files\wxid_2cu68rhp1igz12_1725\msg\file\2026-08\8月13日.mp3"
TMP = os.path.join(ROOT, "data", "_newref_probe")
CUR = os.path.join(ROOT, "assets", "voices", "daniya_ref.wav")


def find_ffmpeg():
    cands = []
    # 常见位置 + 项目内
    for base in (r"C:\ffmpeg\bin", r"C:\Program Files\ffmpeg\bin",
                 os.path.join(ROOT, "tools"), os.path.join(ROOT, "bin")):
        cands.append(os.path.join(base, "ffmpeg.exe"))
    cands += glob.glob(r"H:\**\ffmpeg.exe", recursive=False)
    for c in cands:
        if os.path.isfile(c):
            return c
    # 最后交给 PATH
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=10)
        if r.returncode == 0:
            return "ffmpeg"
    except Exception:
        pass
    return None


def load_wav(p):
    with wave.open(p, "rb") as w:
        nch, sw, sr = w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(w.getnframes())
    a = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    if nch > 1:
        a = a.reshape(-1, nch).mean(axis=1)
    return a, sr


def spectrum(a, sr):
    n = int(sr * 0.032)
    hop = int(sr * 0.016)
    if len(a) < n:
        return None, None
    win = np.hanning(n)
    acc = None
    cnt = 0
    for s in range(0, len(a) - n, hop):
        sp = np.abs(np.fft.rfft(a[s:s + n] * win, n=n * 2)) ** 2
        acc = sp if acc is None else acc + sp
        cnt += 1
    f = np.fft.rfftfreq(n * 2, 1.0 / sr)
    return f, acc / max(cnt, 1)


def report(tag, a, sr):
    f, P = spectrum(a, sr)
    tot = P.sum()
    cum = np.cumsum(P) / tot
    f95 = float(f[np.searchsorted(cum, 0.95)])
    above8 = float(P[f >= 8000].sum() / tot * 100) if (f >= 8000).any() else float("nan")

    def band(lo, hi):
        m = (f >= lo) & (f < min(hi, sr / 2))
        return float(10 * np.log10(max(P[m].sum(), 1e-20) / tot + 1e-20)) if m.any() else float("nan")

    head_pk = float(np.abs(a[:int(sr * 0.2)]).max())
    rms = float(np.sqrt((a ** 2).mean()))
    pk = float(np.abs(a).max())
    # 粗略 F0：自相关，只取有声帧
    best = []
    for s in range(0, len(a) - int(sr * 0.05), int(sr * 0.05)):
        seg = a[s:s + int(sr * 0.05)]
        if np.sqrt((seg ** 2).mean()) < 0.01:
            continue
        seg = seg - seg.mean()
        c = np.correlate(seg, seg, mode="full")[len(seg) - 1:]
        lo, hi = int(sr / 400), int(sr / 80)
        if hi < len(c):
            i = int(np.argmax(c[lo:hi])) + lo
            best.append(sr / i)
    f0 = float(np.median(best)) if best else float("nan")
    print("%-14s 时长%6.1fs  sr=%5d  RMS=%.3f  峰值=%.3f  首0.2s峰值=%.3f"
          % (tag, len(a) / sr, sr, rms, pk, head_pk))
    print("%-14s f95=%6.0f Hz   8k+占比=%5.2f%%   3.4-6k/6-8k/8-12k = %5.1f/%5.1f/%5.1f dB   中位F0=%.1f Hz"
          % ("", f95, above8, band(3400, 6000), band(6000, 8000), band(8000, 12000), f0))
    return dict(dur=len(a) / sr, rms=rms, pk=pk, head=head_pk, f95=f95, f0=f0)


def main():
    print("① 源文件")
    if not os.path.isfile(SRC_MP3):
        print("   ❌ 找不到：%s" % SRC_MP3)
        return 2
    print("   ✅ %s  (%.1f KB)" % (os.path.basename(SRC_MP3), os.path.getsize(SRC_MP3) / 1024))

    ff = find_ffmpeg()
    print("\n② ffmpeg：%s" % (ff or "未找到"))
    if not ff:
        return 3

    os.makedirs(TMP, exist_ok=True)
    out24 = os.path.join(TMP, "newref_24k.wav")
    cmd = [ff, "-y", "-i", SRC_MP3, "-ac", "1", "-ar", "24000",
           "-sample_fmt", "s16", out24]
    r = subprocess.run(cmd, capture_output=True, timeout=120)
    if r.returncode != 0 or not os.path.isfile(out24):
        print("   转换失败：%s" % r.stderr.decode("utf-8", "replace")[:300])
        return 4
    print("   已转 24k 单声道：%s (%.1f KB)" % (out24, os.path.getsize(out24) / 1024))

    print("\n③ 体检对比")
    print("-" * 100)
    a_new, sr_new = load_wav(out24)
    m_new = report("新素材22s", a_new, sr_new)
    print()
    if os.path.isfile(CUR):
        a_cur, sr_cur = load_wav(CUR)
        m_cur = report("现用ref", a_cur, sr_cur)
        print("\n④ 换不换？")
        print("   时长   %.1fs → %.1fs   %s" % (m_cur["dur"], m_new["dur"],
              "✅ 更长" if m_new["dur"] > m_cur["dur"] else "⚠️ 没变长"))
        print("   首0.2s峰值 %.3f → %.3f  %s" % (m_cur["head"], m_new["head"],
              "✅ 起头更干净" if m_new["head"] < m_cur["head"] else "⚠️ 仍是硬切"))
        print("   f95     %.0f → %.0f Hz  %s" % (m_cur["f95"], m_new["f95"],
              "✅ 带宽更足" if m_new["f95"] > m_cur["f95"] else "⚠️ 带宽更窄"))
        print("   中位F0  %.1f → %.1f Hz" % (m_cur["f0"], m_new["f0"]))
    print("-" * 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())
