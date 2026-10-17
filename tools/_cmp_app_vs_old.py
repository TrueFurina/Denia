# -*- coding: utf-8 -*-
"""对比：应用保真版 APP_A vs 旧测试台 A_FIXED（同一把尺子）+ 声码器重启次数。"""
import os
import re
import sys
import glob
import numpy as np
import soundfile as sf

sys.path.insert(0, r"G:\ai对话\11\tools")
import smooth_speech as S  # noqa: E402

D = r"G:\ai对话\11\data\native_audio_test\final_sample"
REF = r"G:\ai对话\11\data\native_audio_test\ref_v2\mp3_8s_24k.wav"

PAT_TEXT = re.compile(r"LLM->TTS: text='([^']*)', n_tokens=(\d+)")
PAT_BOS = re.compile(r"在 prefill 前添加 audio_bos \(chunk_idx=(\d+)")
PAT_EOS = re.compile(r"is_end_of_turn=(\w+), 添加 text_eos_embed")


def frag_stats(logpath):
    t = open(logpath, encoding="utf-8", errors="replace").read()
    lines = t.splitlines()
    texts = [(m.group(1), int(m.group(2))) for l in lines for m in [PAT_TEXT.search(l)] if m]
    bos = [m.group(1) for l in lines for m in [PAT_BOS.search(l)] if m]
    eos = [m.group(1) for l in lines for m in [PAT_EOS.search(l)] if m]
    nz = [n for _, n in texts if n > 0]
    return dict(n_text=len(nz), tok_sum=sum(nz),
                tok_med=float(np.median(nz)) if nz else 0,
                bos0=sum(1 for c in bos if c == "0"),
                bos_all=len(bos), eos=len(eos))


def main():
    ref, rsr = sf.read(REF, dtype="float32")
    if ref.ndim > 1:
        ref = ref.mean(axis=1)
    print("=" * 120)
    print("① 碎块指标（自适应阈值，同一把尺子）")
    print("=" * 120)
    print("  " + S.fmt("【基准】天然参考音", S.stat_burst(ref)))
    print("  " + S.fmt("【真机区间】最近4次真实通话", dict(
        dur=60.3, n_v=0, v_med=0.0, v_frac=0.685, p_ge15=0, p_dens=0.90, p_max=0.70)))
    for f in ("A_FIXED.wav", "A_FIXED_r2.wav", "APP_A.wav"):
        p = os.path.join(D, f)
        if not os.path.exists(p):
            continue
        x, sr = sf.read(p, dtype="float32")
        if x.ndim > 1:
            x = x.mean(axis=1)
        s = S.stat_burst(x)
        print("  " + S.fmt("旧测试台 " + f if f.startswith("A_FIXED") else "★应用保真 " + f, s)
              + f"  F0={S.f0_median(x):.1f}Hz")

    print()
    print("=" * 120)
    print("② 文本分片 / 声码器重启次数（引擎日志）")
    print("=" * 120)
    for f in ("APP_A", "A_FIXED"):
        logs = sorted(glob.glob(os.path.join(D, "worker_%s_*.log" % f)))
        if not logs:
            continue
        for lp in logs[-2:]:
            fs = frag_stats(lp)
            print(f"  {os.path.basename(lp):<44s} 非空文本片={fs['n_text']:3d} "
                  f"平均每片{fs['tok_sum']/max(1,fs['n_text']):.1f}tok(中位{fs['tok_med']:.0f}) "
                  f"audio_bos={fs['bos_all']:3d}(chunk0={fs['bos0']:3d}) text_eos={fs['eos']:3d}")
    print("=" * 120)


if __name__ == "__main__":
    main()
