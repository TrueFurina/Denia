# -*- coding: utf-8 -*-
"""频段归属听测包：把"混合里的原生味"定位到具体频段。

逻辑：整条链路对 8kHz 以上是"失明"的
  · 参考音进模型前被 librosa 无条件降到 16k（= 丢掉 8-16k）
  · 声码器条件带宽实测 fmax=8000
=> 输出的 8-16kHz 只能由声码器自己的先验生成（= MiniCPM 的味道）。
若这个判断成立：把克隆输出限带 8k 之后，"原生味"应当明显减弱，
且此时它与"达妮娅本人限带 8k"应当很接近。

产物（全部等响度 -20dBFS，方便直接 A/B）：
  01 达妮娅本人           全频带
  02 达妮娅本人           限带8k
  03 克隆输出             全频带
  04 克隆输出             限带8k
  05 原生签名音           限带8k
  06 顺序： 01→03→02→04   连续对照
"""
import os
import subprocess
import numpy as np
import soundfile as sf

FF = r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
FS = r"G:\ai对话\11\data\native_audio_test\final_sample"
OUT = os.path.join(FS, "聆听_v4_频段归属")
SRC = r"G:\ai对话\11\data\native_audio_test\ref_v2\mp3_8s_24k.wav"
SIG = r"H:\Comni\_internal\resources\apps\assets\ref_audio\ref_minicpm_signature.wav"
CLONE = os.path.join(FS, "APP_A_smooth.wav")
TARGET_DB = -20.0
SR = 24000


def resample(x, sr_from, sr_to):
    r = subprocess.run([FF, "-v", "quiet", "-f", "f32le", "-ar", str(sr_from), "-ac", "1", "-i", "-",
                        "-f", "f32le", "-ar", str(sr_to), "-ac", "1", "-"],
                       input=np.asarray(x, dtype="<f4").tobytes(), capture_output=True)
    return np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)


def band_limit_8k(x, sr):
    """先降到 16k（抗混叠 = 物理上只能保留 0-8k），再升回 24k 便于 A/B 同采样率"""
    y = resample(x, sr, 16000)
    return resample(y, 16000, SR)


def load24(p):
    x, sr = sf.read(p, dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    if sr != SR:
        x = resample(x, sr, SR)
    return np.asarray(x, dtype=np.float32)


def norm(x):
    r = float(np.sqrt((x.astype(np.float64) ** 2).mean()) + 1e-12)
    return np.clip(x * (10 ** (TARGET_DB / 20) / r), -1.0, 1.0)


def save(p, x):
    sf.write(p, norm(x), SR, subtype="PCM_16")


def main():
    os.makedirs(OUT, exist_ok=True)
    src = load24(SRC)
    sig = load24(SIG)
    clo = load24(CLONE)
    items = [
        ("01_达妮娅本人_全频带.wav", src),
        ("02_达妮娅本人_限带8k.wav", band_limit_8k(src, SR)),
        ("03_克隆输出_全频带.wav", clo),
        ("04_克隆输出_限带8k.wav", band_limit_8k(clo, SR)),
        ("05_原生签名音_限带8k.wav", band_limit_8k(sig, SR)),
    ]
    for name, x in items:
        save(os.path.join(OUT, name), x)
        print("  %-28s %.2fs" % (name, len(x) / SR))

    # 顺序对照：真人 → 克隆（全频带）→ 真人 → 克隆（限带）
    gap = np.zeros(int(SR * 0.55), dtype=np.float32)
    seq = np.concatenate([norm(src), gap, norm(clo), gap, gap,
                          norm(band_limit_8k(src, SR)), gap, norm(band_limit_8k(clo, SR)), gap])
    sp = os.path.join(OUT, "06_顺序_真人到克隆_全频带与限带.wav")
    sf.write(sp, seq, SR, subtype="PCM_16")
    print("  %-28s %.2fs" % (os.path.basename(sp), len(seq) / SR))
    print("\n产物目录:", OUT)


if __name__ == "__main__":
    main()
