# -*- coding: utf-8 -*-
"""用 rubberband(保共振峰) 重做 A/B：
  1) 参考音升调 1.6342x 至录屏音高（保共振峰，不再是"小黄人"）
  2) 录屏人声降调 0.6119x 至参考音音高（保共振峰）
  3) 拼一份四段对比：原参考 / 升调参考 / 原录屏 / 降调录屏
"""
import os, subprocess
import numpy as np

FF = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
D = r"G:\ai对话\11\data\native_audio_test\src_candidates"
REF = r"G:\ai对话\11\assets\voices\daniya_ref.wav"
SRC = os.path.join(D, "V0827_r07_16.0-17.1s_F0390.wav")
TMP = r"G:\ai对话\11\data\native_audio_test\_rb_tmp"

UP = 390.2 / 238.8      # 参考音 -> 录屏音高
DOWN = 238.8 / 390.2    # 录屏 -> 参考音音高


def run(args):
    return subprocess.run([FF, "-v", "error", "-y"] + args, capture_output=True,
                          text=True, errors="replace")


def decode(p, sr=48000):
    r = subprocess.run([FF, "-v", "quiet", "-i", p, "-vn", "-ac", "1", "-ar", str(sr),
                        "-f", "s16le", "-"], capture_output=True)
    return np.frombuffer(r.stdout, dtype="<i2").astype(np.float32) / 32768.0


def write_wav(p, x, sr=48000):
    x = np.clip(x, -1, 1)
    d = (x * 32767).astype("<i2").tobytes()
    hdr = (b"RIFF" + (36 + len(d)).to_bytes(4, "little") + b"WAVEfmt " +
           (16).to_bytes(4, "little") + (1).to_bytes(2, "little") + (1).to_bytes(2, "little") +
           sr.to_bytes(4, "little") + (sr * 2).to_bytes(4, "little") + (2).to_bytes(2, "little") +
           (16).to_bytes(2, "little") + b"data" + len(d).to_bytes(4, "little"))
    open(p, "wb").write(hdr + d)


def pitch_shift(src, dst, ratio, formant="preserved"):
    """优先 rubberband(保共振峰)，失败退回 asetrate(会变尖)"""
    af = "rubberband=pitch=%.5f:formant=%s" % (ratio, formant)
    r = run(["-i", src, "-af", af, "-ac", "1", "-ar", "48000", dst])
    if r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 1000:
        print("  [rubberband] formant=%s  %.5f  -> %s" % (formant, ratio, os.path.basename(dst)))
        return "rubberband:" + formant
    print("  [rubberband 失败] %s" % (r.stderr or "")[:200])
    af = "asetrate=48000*%.5f,aresample=48000,atempo=%.5f" % (ratio, 1.0 / ratio)
    r2 = run(["-i", src, "-af", af, "-ac", "1", "-ar", "48000", dst])
    print("  [asetrate 回退] -> %s" % os.path.basename(dst))
    return "asetrate"


def main():
    os.makedirs(TMP, exist_ok=True)
    a = os.path.join(TMP, "ref_up.wav")
    b = os.path.join(TMP, "vid_down.wav")

    print("=== 变调（保共振峰）===")
    m1 = pitch_shift(REF, a, UP)
    m2 = pitch_shift(SRC, b, DOWN)

    # 片段统一 1.5s，峰值对齐
    def seg(p, sec=1.5, sr=48000):
        x = decode(p, sr)[:int(sec * sr)]
        return x / (np.abs(x).max() + 1e-9) * 0.30

    r_raw = seg(REF)
    r_up = seg(a)
    v_raw = seg(SRC)
    v_dn = seg(b)
    gap = np.zeros(int(48000 * 0.55), np.float32)
    parts = [r_raw, gap, r_up, gap, v_raw, gap, v_dn]
    starts = []
    t = 0.0
    for p in parts:
        starts.append(t)
        t += len(p) / 48000.0
    st = np.concatenate(parts)
    outp = os.path.join(D, "_AB2_四段_原参考_升调参考_原录屏_降调录屏.wav")
    write_wav(outp, st)
    marks = ["原参考音(238.8Hz)", "升调参考音(390Hz,保共振峰)", "原录屏人声(390.2Hz)",
             "降调录屏(238.8Hz,保共振峰)"]
    print("\n四段对比已生成: %s  (%.2fs)" % (os.path.basename(outp), len(st) / 48000))
    for m, s in zip(marks, starts[::2]):
        print("    %5.2fs  %s" % (s, m))

    # 另存两个单段，便于单独试听
    p1 = os.path.join(D, "_AB1_参考音升调至390_保共振峰.wav")
    p2 = os.path.join(D, "_AB1_录屏降调至239_保共振峰.wav")
    write_wav(p1, r_up)
    write_wav(p2, v_dn)
    print("  单段: %s / %s" % (os.path.basename(p1), os.path.basename(p2)))
    print("  模式: up=%s  down=%s" % (m1, m2))


if __name__ == "__main__":
    main()
