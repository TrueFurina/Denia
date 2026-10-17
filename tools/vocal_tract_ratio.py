# -*- coding: utf-8 -*-
"""声道长度比检验（对音高不敏感）：
   求 r 使得 参考音包络(f) 与 对方包络(f*r) 最相似。
     r ≈ 1.00  -> 声道尺寸相同（同一个人，只是音高/风格不同）
     r ≈ 1.63  -> 参考音恰好是对方"整体被重采样"的版本（共振峰一起缩了）
     其它值    -> 声道尺寸不同（很可能是另一个人）
   同时给出峰值相关高度：低峰值说明形状本就对不上（不同人）。
"""
import os, sys
import numpy as np
sys.path.insert(0, r"G:\ai对话\11\tools")
import speaker_cue_check as S

D = r"G:\ai对话\11\data\native_audio_test"
REF = r"G:\ai对话\11\assets\voices\daniya_ref.wav"
AN = S.AN


def env_on_loggrid(p, flo=300.0, fhi=4200.0, npts=64, lifter_ms=1.8):
    x = S.decode(p, AN)
    psd, f, c, _ = S.avg_spectrum(x, AN)
    if psd is None:
        return None
    env, fe = S.envelope_from_psd(psd, AN, S.NW, lifter_ms=lifter_ms)
    g = np.geomspace(flo, fhi, npts)
    v = np.interp(g, fe, env)
    return v


def detrend(v):
    t = np.arange(len(v), dtype=np.float64)
    A = np.vstack([t, np.ones_like(t)]).T
    coef, *_ = np.linalg.lstsq(A, v, rcond=None)
    return v - A @ coef


def best_ratio(a, b, rmin=0.80, rmax=1.90, step=0.01):
    """a=参考(基准)，b=待比：求 r 使 a(f) ≈ b(f*r)"""
    ta = detrend(a)
    g = np.arange(len(a))
    out = []
    for r in np.arange(rmin, rmax + 1e-9, step):
        # b 曲线在对数频率轴上平移 log(r)
        shift = np.log(r) / (np.log(4200.0 / 300.0) / (len(a) - 1))
        tb = detrend(b)
        tb_s = np.interp(g + shift, g, tb, left=np.nan, right=np.nan)
        m = ~np.isnan(tb_s)
        if m.sum() < 20:
            continue
        s = float(np.corrcoef(ta[m], tb_s[m])[0, 1])
        out.append((s, r))
    out.sort(reverse=True)
    return out[0][1], out[0][0], {round(r, 2): s for s, r in out}


def main():
    pairs = [
        ("【自比·上界】参考前半 vs 参考后半", REF, REF, 2),
        ("【同一条流水线】参考音 vs MiniCPM原生输出", REF,
         os.path.join(D, "daniya_native.wav"), 0),
        ("【不同人·下界】参考音 vs SAPI中性女声", REF,
         os.path.join(D, "INPUT_sapi_neutral.wav"), 0),
        ("★ 参考音 vs 录屏 r07(16.0-17.1s,390Hz)", REF,
         os.path.join(D, "src_candidates", "V0827_r07_16.0-17.1s_F0390.wav"), 0),
        ("★ 参考音 vs 录屏 r10(24.6-25.2s,304Hz)", REF,
         os.path.join(D, "src_candidates", "V0827_r10_24.6-25.2s_F0304.wav"), 0),
        ("★ 参考音 vs 录屏 全片27s", REF,
         os.path.join(D, "src_candidates", "V0827_全片27.0s_48k.wav"), 0),
        ("   录屏 r07 vs 录屏 r10（同一视频内不同段落）",
         os.path.join(D, "src_candidates", "V0827_r07_16.0-17.1s_F0390.wav"),
         os.path.join(D, "src_candidates", "V0827_r10_24.6-25.2s_F0304.wav"), 0),
    ]
    print("=== 声道长度比 r（对音高不敏感）===")
    for name, pa, pb, split in pairs:
        if split:
            x = S.decode(pa, AN)
            half = len(x) // 2

            def env_of_sig(sig, lifter_ms=1.8):
                psd, f, c, _ = S.avg_spectrum(sig, AN)
                if psd is None:
                    return None
                e, fe = S.envelope_from_psd(psd, AN, S.NW, lifter_ms=lifter_ms)
                g = np.geomspace(300.0, 4200.0, 64)
                return np.interp(g, fe, e)
            a, b = env_of_sig(x[:half]), env_of_sig(x[half:])
        else:
            a, b = env_on_loggrid(pa), env_on_loggrid(pb)
        if a is None or b is None:
            print("  %-46s 取不到浊音" % name[:46]); continue
        r, score, table = best_ratio(a, b)
        s163 = min(table.items(), key=lambda kv: abs(kv[0] - 1.63))[1]
        s100 = min(table.items(), key=lambda kv: abs(kv[0] - 1.0))[1]
        print("  %-46s 最佳r=%5.2f (相似度%.3f) | r=1.00:%.3f | r=1.63:%.3f" %
              (name[:46], r, score, s100, s163))
    print("\n判读：r≈1.00 且相似度高 => 声道尺寸相同（同一人，音高差异来自发声方式/风格）；")
    print("      r≈1.63 且显著更优 => 参考音是整体重采样产物（同一个录音被变调变共振峰）。")


if __name__ == "__main__":
    main()
