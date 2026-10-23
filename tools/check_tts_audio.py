# -*- coding: utf-8 -*-
"""TTS 音频合格体检器 —— 交付任何合成音频之前必须跑这道闸门。
不进闸门：人工体检仪器——要喂待测音频文件、交付前手动跑，不是自动闸门
（2026-10-22 普查定性，见 tools/check_gate_registry.py）。

判据（帧长1024 / hop256 / Hann）：
    flat = median( exp(mean(log P)) / mean(P) )    谱平坦度
    f95  = median( 累计能量达 95% 处的频率 )        有效带宽

合格线: flat > 0.002 且 f95 > 1200 Hz  -> 可交付
判死线: flat < 0.0005 或 f95 < 1000 Hz -> 绝不交付（哼鸣 / 辅音缺失）

实测刻度：
    真人参考音                      flat 0.0139~0.0250   f95 1828~1992
    双工原生产出                    flat 0.0020~0.0080   f95 1000~1430
    坏音频（CosyVoice2 哼鸣）        flat 0.0000~0.0001   f95  516~867

用法：
    python check_tts_audio.py <文件或目录> [更多...]
    python check_tts_audio.py --json out.json <目录>
"""
import sys, os, glob, json
import numpy as np


def read_wav_mono(path):
    import soundfile as sf
    x, sr = sf.read(path, dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    return x, sr


def spec_stats(x, sr, n=1024, hop=256):
    """返回 (flat, f95, rms, dur)。样本不足时返回 None。"""
    if len(x) < n:
        return None
    nf = 1 + (len(x) - n) // hop
    idx = np.arange(n)[None, :] + hop * np.arange(nf)[:, None]
    fr = x[idx] * np.hanning(n)[None, :]
    P = np.abs(np.fft.rfft(fr, axis=1)) ** 2
    P = P[:, : n // 2 + 1]
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    flat = float(np.median(np.exp(np.mean(np.log(P + 1e-12), axis=1)) /
                           (np.mean(P, axis=1) + 1e-12)))
    cs = np.cumsum(P, axis=1) / (P.sum(axis=1, keepdims=True) + 1e-12)
    f95 = float(np.median(freqs[np.argmax(cs >= 0.95, axis=1)]))
    rms = float(np.sqrt(np.mean(x ** 2)) + 1e-12)
    return flat, f95, rms, len(x) / float(sr)


def verdict(flat, f95):
    if flat < 0.0005 or f95 < 1000:
        return "FAIL  哼鸣/辅音缺失，绝不交付"
    if flat > 0.002 and f95 > 1200:
        return "PASS  可交付"
    return "WARN  临界，建议人工试听"


def check_one(path):
    try:
        x, sr = read_wav_mono(path)
    except Exception as e:
        return {"file": path, "error": str(e)}
    st = spec_stats(x, sr)
    if st is None:
        return {"file": path, "error": "too short", "sr": sr, "dur": len(x) / float(sr)}
    flat, f95, rms, dur = st
    return {"file": path, "sr": sr, "dur": round(dur, 3), "rms": round(rms, 5),
            "flat": round(flat, 6), "f95": round(f95, 1),
            "db": round(20 * np.log10(rms), 1), "verdict": verdict(flat, f95)}


def collect(targets):
    out = []
    for t in targets:
        if os.path.isdir(t):
            out.extend(sorted(glob.glob(os.path.join(t, "**", "*.wav"), recursive=True)))
        elif os.path.isfile(t):
            out.append(t)
        else:
            g = glob.glob(t)
            if g:
                out.extend(sorted(g))
            else:
                out.append(t)  # 让它报到 error
    return out


def main():
    args = [a for a in sys.argv[1:]]
    jout = None
    if "--json" in args:
        i = args.index("--json")
        jout = args[i + 1]
        del args[i:i + 2]
    if not args:
        here = os.path.dirname(os.path.abspath(__file__))
        args = [os.path.join(os.path.dirname(here), "data")]
    files = collect(args)
    results = [check_one(f) for f in files]
    print("%-58s %8s %8s %7s %7s  %s" % ("file", "dur(s)", "flat", "f95", "dB", "verdict"))
    print("-" * 118)
    for r in results:
        if "error" in r:
            print("%-58s %s" % (os.path.basename(r["file"])[:58], "ERR " + r["error"]))
        else:
            print("%-58s %8.2f %8.5f %7.0f %7.1f  %s" % (
                os.path.basename(r["file"])[:58], r["dur"], r["flat"], r["f95"], r["db"], r["verdict"]))
    npass = sum(1 for r in results if r.get("verdict", "").startswith("PASS"))
    nfail = sum(1 for r in results if r.get("verdict", "").startswith("FAIL"))
    print("-" * 118)
    print("合计 %d 个：PASS %d / FAIL %d / 其他 %d" % (
        len(results), npass, nfail, len(results) - npass - nfail))
    if jout:
        with open(jout, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print("json ->", jout)


if __name__ == "__main__":
    main()
