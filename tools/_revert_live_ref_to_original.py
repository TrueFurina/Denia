# -*- coding: utf-8 -*-
"""将 live 的 daniya_ref.wav（两处：项目 assets + Comni assets/ref_audio）回退为
原始微信清音 daniya_ref_clean_16k.wav（去静音+标准化，自然不污染）。
保留 .bak_prev_<TS> 备份，可随时还原。
注意：vocoder 缓存保持当前的 A（宽频，FINAL2 烘），以获得「自然说话人 + 亮带宽」组合。
若想要说话人与缓存完全一致，需另烤一版基于原始清音的缓存（会偏 16k/闷，按需再做）。
"""
import os, shutil, time

CLEAN = r"H:\daniya_voice_tools\daniya_ref_clean_16k.wav"
TARGETS = [
    r"G:\ai对话\11\assets\voices\daniya_ref.wav",
    r"H:\Comni\_internal\resources\apps\assets\ref_audio\daniya_ref.wav",
]

def main():
    for t in TARGETS:
        if not os.path.exists(t):
            print("SKIP 不存在:", t); continue
        bak = t + ".bak_prev_%s" % time.strftime("%Y%m%d_%H%M%S")
        shutil.copy2(t, bak)
        shutil.copy2(CLEAN, t)
        print("REVERT", t, "-> clean  (backup=%s)" % os.path.basename(bak))

if __name__ == "__main__":
    main()
