# -*- coding: utf-8 -*-
"""诊断：两个原生双工产出的亮度差异是「内容/选帧」造成，还是真的不同。"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, PROJ); sys.path.insert(0, HERE)
import numpy as np
import hf_restore as HR
import voice_eq_calib as C

REF = os.path.join(PROJ, "assets", "voices", "daniya_ref.wav")
FILES = ["20260921-174951-0.wav", "20260922-174709-0.wav"]
ARCH = os.path.join(PROJ, "src", "ui", "web", "assets", "chat_audio")

ref = C.read_mono24(REF)
rl, rfa = C._ltas_db(ref)
print("真源 %.2fs  LTAS距自身=0  谱心=%.0f f95=%.0f" % (len(ref)/24000, C.metrics(ref)["cent"], C.metrics(ref)["f95"]))
print()
for fn in FILES:
    x = C.read_mono24(os.path.join(ARCH, fn))
    if x is None:
        continue
    m = C.metrics(x)
    L, f, nsel = HR.ltas(x)
    Ldb = 20*np.log10(L + 1e-12)
    # 与真源的 voiced-frame LTAS 距离
    d = C.ltas_dist(Ldb, f, rl, rfa)
    nf = HR.stft(x)[0].shape[0]
    rms = float(np.sqrt(np.mean(x**2)))
    # 噪声底占比：低于峰值 1% 的样本比例
    quiet = float(np.mean(np.abs(x) < 0.005*np.abs(x).max()))
    print("%-32s" % fn)
    print("   时长=%.2fs rms=%.4f 静音占比=%.1f%%" % (len(x)/24000, rms, 100*quiet))
    print("   整段：3.4-6k=%.1f 6-8k=%.1f 8-12k=%.1f 谱心=%.0f f95=%.0f 斜率=%.2f"
          % (m["b3400"], m["b6000"], m["b8000"], m["cent"], m["f95"], m["slope"]))
    print("   voiced 帧选取：%d/%d（%.0f%%）  该口径 LTAS 距真源=%.2f dB" % (nsel, nf, 100*nsel/max(nf,1), d))
    # 只看有声段（去静音）再算一遍
    seg = np.abs(x)
    thr = 0.02 * seg.max()
    idx = np.where(seg > thr)[0]
    if idx.size > 24000:
        a, b = idx[0], idx[-1]
        y = x[a:b]
        m2 = C.metrics(y)
        print("   仅有声段 %.2fs：3.4-6k=%.1f 6-8k=%.1f 8-12k=%.1f 谱心=%.0f f95=%.0f"
              % (len(y)/24000, m2["b3400"], m2["b6000"], m2["b8000"], m2["cent"], m2["f95"]))
        y2 = y[y != 0]
    print()
print("参考：真源 3.4-6k=%.1f 6-8k=%.1f 8-12k=%.1f 谱心=%.0f f95=%.0f 斜率=%.2f"
      % (C.metrics(ref)["b3400"], C.metrics(ref)["b6000"], C.metrics(ref)["b8000"],
         C.metrics(ref)["cent"], C.metrics(ref)["f95"], C.metrics(ref)["slope"]))
