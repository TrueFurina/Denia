# -*- coding: utf-8 -*-
"""从 prompt_cache.gguf 里提取 spk_cb（说话人向量），并与 bundle 的 spk_f32.bin 对照。

判据：
  cache 的 spk_cb = 192 个 float32 = 768 字节
  bundle 的 spk_f32.bin 也应是 192 float32 = 768 字节
若两者一致 ⇒ cache 确实携带了该参考音的说话人向量；
若各 cache 的 spk_cb 互不相同 ⇒ 音色分量确实随参考音改变（烤制有效）。
"""
import os
import struct
import hashlib
import glob

M = r"H:\minicpm models\MiniCPM-o-4_5-gguf\token2wav-gguf"
B = r"H:\Comni\_internal\resources\tools\omni\assets\default_ref_audio"

UA = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?",
      10: "Q", 11: "q", 12: "d"}


class Rd:
    def __init__(self, fh):
        self.f = fh

    def raw(self, n):
        b = self.f.read(n)
        if len(b) != n:
            raise EOFError
        return b

    def u32(self):
        return struct.unpack("<I", self.raw(4))[0]

    def u64(self):
        return struct.unpack("<Q", self.raw(8))[0]

    def s(self):
        return self.raw(self.u64()).decode("utf-8", "replace")

    def val(self, t):
        if t == 8:
            return self.s()
        if t == 9:
            et = self.u32()
            n = self.u64()
            return [self.val(et) for _ in range(n)]
        fmt = UA[t]
        return struct.unpack("<" + fmt, self.raw(struct.calcsize(fmt)))[0]


def read_spk_cb(path):
    """返回 (kv, tensors, spk_bytes, spk_dims)"""
    fh = open(path, "rb")
    r = Rd(fh)
    if r.raw(4) != b"GGUF":
        raise ValueError("非 GGUF")
    ver = r.u32()
    nt = r.u64()
    nk = r.u64()
    kv = {}
    for _ in range(nk):
        k = r.s()
        t = r.u32()
        kv[k] = r.val(t)
    align = int(kv.get("general.alignment", 32) or 32)
    infos = []
    for _ in range(nt):
        name = r.s()
        nd = r.u32()
        dims = [r.u32() for _ in range(nd)]
        tt = r.u32()
        off = r.u64()
        infos.append((name, dims, tt, off))
    pos = fh.tell()
    data_start = (pos + align - 1) // align * align
    raw_all = None
    spk = None
    spk_dims = None
    for name, dims, tt, off in infos:
        if name.endswith("spk_cb"):
            fh.seek(data_start + off)
            n = 1
            for d in dims:
                n *= d
            spk = fh.read(n * 4)      # F32
            spk_dims = dims
    fh.close()
    return kv, infos, spk, spk_dims


def show(tag, path):
    if not os.path.exists(path):
        print("  [缺] %s" % path)
        return None
    try:
        kv, infos, spk, dims = read_spk_cb(path)
    except Exception as e:
        print("  [失败] %s : %r" % (os.path.basename(path), e))
        return None
    md5 = hashlib.md5(spk).hexdigest() if spk else "-"
    n = len(spk) // 4 if spk else 0
    head = struct.unpack("<5f", spk[:20]) if spk and len(spk) >= 20 else ()
    print("  %-46s %9d B  spk_cb dims=%s n=%d" % (tag, os.path.getsize(path), dims, n))
    print("        spk md5=%s" % md5)
    if head:
        print("        前5值 = %s" % ", ".join("%+.5f" % v for v in head))
    print("        KV: version=%s n_timesteps=%s pre_lookahead=%s chunk_main=%s chunk_total=%s up_rate=%s temp=%s"
          % (kv.get("mtmd.prompt_cache.version"), kv.get("mtmd.prompt_cache.n_timesteps"),
             kv.get("mtmd.prompt_cache.pre_lookahead"), kv.get("mtmd.prompt_cache.chunk_main"),
             kv.get("mtmd.prompt_cache.chunk_total"), kv.get("mtmd.prompt_cache.up_rate"),
             kv.get("mtmd.prompt_cache.temperature")))
    lens = [(nm, dd) for nm, dd, tt, of in infos if "att_cache" in nm]
    print("        att_cache 形状: %s" % "; ".join("%s=%s" % (a, b) for a, b in lens))
    return md5, n, head


def main():
    print("=" * 104)
    print("【1】各 prompt_cache.gguf 的 spk_cb（音色分量）")
    print("=" * 104)
    cands = [("LIVE(当前生效/微信精修版)", os.path.join(M, "prompt_cache.gguf"))]
    for pat, lab in [("prompt_cache.gguf.bak_before_wechat_*", "备份: 录屏332MB版"),
                     ("prompt_cache.gguf.bak_before_final_*", "备份: 历史247MB版"),
                     ("prompt_cache.gguf.quarantine_*", "隔离区")]:
        for p in sorted(glob.glob(os.path.join(M, pat))):
            cands.append((lab + " / " + os.path.basename(p)[-25:], p))
    res = {}
    for tag, p in cands:
        r = show(tag, p)
        if r:
            res[tag] = r

    print()
    print("=" * 104)
    print("【2】bundle 目录里的 spk_f32.bin（当前 live bundle = 原厂默认音）")
    print("=" * 104)
    for d in [B, r"H:\daniya_voice_tools\bundle_backup_final_20260921_211602",
              r"H:\daniya_voice_tools\tmp_wechat",
              r"H:\daniya_voice_tools\tmp_cosy_daniya_ref_wav"]:
        p = os.path.join(d, "spk_f32.bin")
        if not os.path.exists(p):
            print("  [缺] %s" % p)
            continue
        b = open(p, "rb").read()
        print("  %-56s %6d B  md5=%s  n_f32=%d" % (d, len(b), hashlib.md5(b).hexdigest(), len(b) // 4))
        if len(b) >= 20:
            print("        前5值 = %s" % ", ".join("%+.5f" % v for v in struct.unpack("<5f", b[:20])))

    print()
    print("=" * 104)
    print("【3】结论判据")
    print("=" * 104)
    uniq = set(v[0] for v in res.values())
    print("  各 cache 的 spk_cb md5 去重后共 %d 种" % len(uniq))
    for k, v in res.items():
        print("    %-60s %s" % (k[:60], v[0]))
    if len(uniq) == 1:
        print("  ⇒ 所有 cache 的说话人向量完全相同：音色分量【没有随参考音改变】")
    else:
        print("  ⇒ 说话人向量随参考音不同：音色分量【确实被写入了】")


if __name__ == "__main__":
    main()
