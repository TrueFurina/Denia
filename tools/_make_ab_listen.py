# -*- coding: utf-8 -*-
"""生成「音色 A/B 试听包」：把达妮娅真源 / 当前产出 / MiniCPM 原生 放一起让人耳裁决。

背景：余弦指标在本项目（同一声码器 + 8kHz 带宽）下分辨力有限 —— 连被标注为
"原生底色"的样本对着达妮娅也有 0.696。⇒ 最终裁决交给耳朵。
"""
import os
import glob
import shutil

ROOT = r"G:\ai对话\11"
OUTDIR = os.path.join(ROOT, "data", "duplex_out")
PKG = os.path.join(ROOT, "data", "音色AB对比_20260923")

# 最新一次通话的产出
subs = sorted(os.listdir(OUTDIR))
LATEST = os.path.join(OUTDIR, subs[-1])

ITEMS = [
    ("00_达妮娅真源_参考.wav",
     os.path.join(ROOT, "assets", "voices", "daniya_ref.wav")),
    ("01_当前通话产出_你好呀漂泊者.wav",
     os.path.join(LATEST, "003_你好呀，漂泊者。.wav")),
    ("02_当前通话产出_我在这儿呢.wav",
     os.path.join(LATEST, "005_我在这儿呢，有什么事吗？.wav")),
    ("03_当前通话产出_嗯好的我叫达妮娅.wav",
     os.path.join(LATEST, "015_嗯，好的，我叫达妮娅。.wav")),
    ("04_MiniCPM原生底色_对照.wav",
     os.path.join(ROOT, "data", "native_audio_test", "final_sample", "B_NATIVE.wav")),
    ("05_MiniCPM原生签名_对照.wav",
     os.path.join(ROOT, "voice_baseline", "20260922_144014",
                  "02_ref_minicpm_signature.wav")),
]


def main():
    os.makedirs(PKG, exist_ok=True)
    print("产出来源会话：%s" % subs[-1])
    print("试听包：%s\n" % PKG)
    made = []
    for dst_name, src in ITEMS:
        if not os.path.isfile(src):
            print("  ⚠️ 缺 %s" % src)
            continue
        dst = os.path.join(PKG, dst_name)
        shutil.copy2(src, dst)
        made.append(dst)
        print("  %-42s %7.1f KB" % (dst_name, os.path.getsize(dst) / 1024))
    # 附上说明
    note = os.path.join(PKG, "怎么听.txt")
    with open(note, "w", encoding="utf-8") as f:
        f.write(
            "音色 A/B 裁决（2026-09-23）\n"
            "================================\n"
            "请按顺序听，重点听「嗓子的质地/厚度/亮度」，不要听内容。\n\n"
            "00 = 达妮娅真源（目标，24k 原始素材）\n"
            "01/02/03 = 当前通话的实际产出（同一通电话里的三句）\n"
            "04 = MiniCPM 原生底色 B_NATIVE（已知是原生嗓的产出）\n"
            "05 = MiniCPM 原生签名（默认参考音）\n\n"
            "判读：\n"
            "  若 01~03 听感和 00 接近、和 04/05 明显不同  ⇒ 克隆是生效的，\n"
            "      你觉得“诡异”是音质问题（8kHz 带宽天花板 + 帧接缝颤音）。\n"
            "  若 01~03 听感和 04/05 更像、和 00 明显不同  ⇒ 那跑的就是原生嗓，\n"
            "      余弦指标失效，必须改查声码器为什么没吃到 prompt_cache。\n\n"
            "机器测量的现状（供参考，不足以定论）：\n"
            "  产出 ↔达妮娅 均值 0.6439；产出 ↔原生签名 均值 0.2625；\n"
            "  异人基线（达妮娅↔原生签名）0.1958 ⇒ 倾向达妮娅，\n"
            "  但被标注“原生底色”的样本对着达妮娅也有 0.6961 ⇒ 指标分辨力有限。\n"
        )
    made.append(note)
    print("\n共 %d 个文件" % len(made))
    print("\n最新会话的全部产出在：%s（%d 句）"
          % (LATEST, len(glob.glob(os.path.join(LATEST, "*.wav")))))


if __name__ == "__main__":
    main()
