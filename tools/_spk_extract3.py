# -*- coding: utf-8 -*-
"""按【已验证的固定偏移】读取 prompt_cache.gguf 的 spk_cb（说话人向量）。

data_start=736 的依据（自洽验证）：
  文件 247,821,792 - 736 = 247,821,056
    = estimator_att_cache.off(15,823,616) + 128*354*640*2*4(231,997,440)   ✓
  文件 242,251,232 - 736 = 242,250,496
    = 15,495,936 + 128*346*640*2*4(226,754,560)                            ✓
故 spk_cb（第一张量 off=0，192×f32=768B）就在偏移 736 处。
"""
import os
import glob
import struct
import hashlib

M = r"H:\minicpm models\MiniCPM-o-4_5-gguf\token2wav-gguf"
DATA_START = 736
SPK_BYTES = 768


def read_spk(path):
    with open(path, "rb") as f:
        head = f.read(4)
        if head != b"GGUF":
            raise ValueError("非 GGUF: %r" % head)
        f.seek(DATA_START)
        return f.read(SPK_BYTES)


def describe(path, label):
    size = os.path.getsize(path)
    spk = read_spk(path)
    v = struct.unpack("<192f", spk)
    md5 = hashlib.md5(spk).hexdigest()
    norm = sum(x * x for x in v) ** 0.5
    mean = sum(v) / 192
    sd = (sum((x - mean) ** 2 for x in v) / 192) ** 0.5
    mn, mx = min(v), max(v)
    print()
    print("  %s" % label)
    print("     文件: %s" % os.path.basename(path))
    print("     size=%s  spk_cb@%d  md5=%s" % (format(size, ","), DATA_START, md5))
    print("     前6值 = %s" % ", ".join("%+.6f" % x for x in v[:6]))
    print("     |v|=%.5f  mean=%+.6f  sd=%.6f  min=%+.4f max=%+.4f" % (norm, mean, sd, mn, mx))
    # 由文件大小反推 att_cache 时间维，验证布局
    tail = size - DATA_START
    for T in range(200, 500):
        if 768 + 12288 + (128 * T * 8 * 10 * 4) + (1024 * 2 * 80 * 2 * 4) + (128 * T * 640 * 2 * 4) == tail:
            print("     反推 att_cache 时间维 T=%d  (对应参考音时长 ≈ %.2fs @20ms/帧)" % (T, T * 0.02))
            break
    return md5, v


def main():
    print("=" * 104)
    print("spk_cb（192 维说话人向量）逐份对照 —— 判断音色分量是否随参考音改变")
    print("=" * 104)
    items = [("LIVE 当前生效（微信精修版 242MB）", os.path.join(M, "prompt_cache.gguf"))]
    for pat, lab in [("prompt_cache.gguf.bak_before_wechat_*", "备份前身（录屏332MB）"),
                     ("prompt_cache.gguf.bak_before_final_*", "备份前身（历史247MB）"),
                     ("prompt_cache.gguf.bak_final2_*", "备份（9/21 失败尝试 221MB）")]:
        for p in sorted(glob.glob(os.path.join(M, pat))):
            items.append((lab, p))

    res = []
    for lab, p in items:
        if not os.path.exists(p):
            print("\n  [缺] %s" % p)
            continue
        try:
            md5, v = describe(p, lab)
            res.append((lab, md5, v))
        except Exception as e:
            print("\n  [失败] %s : %r" % (lab, e))

    print()
    print("=" * 104)
    print("判据")
    print("=" * 104)
    for lab, md5, v in res:
        print("   %-40s %s" % (lab[:40], md5))
    uniq = set(m for _, m, _ in res)
    print()
    print("   去重后 %d 种" % len(uniq))
    if len(uniq) == 1:
        print("   ⇒ 全部相同：说话人向量【没有随参考音改变】")
    else:
        print("   ⇒ 互不相同：说话人向量【随参考音改变】，音色分量确已被写入 cache")
    # 两两余弦
    print()
    print("   两两余弦相似度（越接近 1 越像同一人）：")
    for i in range(len(res)):
        for j in range(i + 1, len(res)):
            a, b = res[i][2], res[j][2]
            dot = sum(x * y for x, y in zip(a, b))
            na = sum(x * x for x in a) ** 0.5
            nb = sum(x * x for x in b) ** 0.5
            print("     %-32s vs %-32s cos=%+.4f"
                  % (res[i][0][:32], res[j][0][:32], dot / (na * nb)))


if __name__ == "__main__":
    main()
