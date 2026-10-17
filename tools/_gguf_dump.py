# -*- coding: utf-8 -*-
"""解析 prompt_cache.gguf 内部结构：KV 元数据 + 张量清单 + 逐张量数据 md5。

用途：判定「重新烤制的音色是否真的写进了 cache」，以及 cache 里各分量
（prompt_tokens / prompt_mel / spk）是否随参考音改变。
纯 Python，无第三方依赖。
"""
import os
import sys
import glob
import hashlib
import struct

M = r"H:\minicpm models\MiniCPM-o-4_5-gguf\token2wav-gguf"

TYPE_NAME = {
    0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 4: "Q5_0", 5: "Q5_1",
    6: "Q8_0", 7: "Q8_1", 8: "Q2_K", 9: "Q3_K", 10: "Q4_K", 11: "Q5_K",
    12: "Q6_K", 13: "Q8_K", 14: "IQ2_XXS", 15: "IQ2_XS", 16: "IQ3_XXS",
    17: "IQ1_S", 18: "IQ4_NL", 19: "IQ3_S", 20: "IQ2_S", 21: "IQ4_XS",
    22: "I8", 23: "I16", 24: "I32", 25: "I64", 26: "F64", 27: "IQ1_M",
    28: "BF16", 30: "TQ1_0", 31: "TQ2_0",
}
TYPE_SIZE = {
    0: 4, 1: 2, 2: 0.5625, 3: 0.625, 4: 0.6875, 5: 0.75, 6: 1.0625,
    7: 0.0, 8: 0.0, 22: 1, 23: 2, 24: 4, 25: 8, 26: 8, 28: 2,
}
# K-quant 的 block 大小（每 256 个元素一块）
KQUANT = {8: 84, 9: 110, 10: 144, 11: 176, 12: 210, 13: 292, 14: 66,
          15: 74, 16: 98, 17: 34, 18: 18, 19: 110, 20: 82, 21: 136,
          27: 18, 30: 34, 31: 34}

UA = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?",
      10: "Q", 11: "q", 12: "d"}


class Rd:
    def __init__(self, fh):
        self.f = fh

    def raw(self, n):
        b = self.f.read(n)
        if len(b) != n:
            raise EOFError("短读 %d/%d" % (len(b), n))
        return b

    def u32(self):
        return struct.unpack("<I", self.raw(4))[0]

    def u64(self):
        return struct.unpack("<Q", self.raw(8))[0]

    def s(self):
        n = self.u64()
        return self.raw(n).decode("utf-8", "replace")

    def val(self, t):
        if t == 8:
            return self.s()
        if t == 9:
            et = self.u32()
            n = self.u64()
            if n > 100000:
                return "<array n=%d et=%s 过大，跳过>" % (n, TYPE_NAME.get(et, et))
            return [self.val(et) for _ in range(n)]
        fmt = UA.get(t)
        if fmt is None:
            raise ValueError("未知 gguf 类型 %d" % t)
        return struct.unpack("<" + fmt, self.raw(struct.calcsize(fmt)))[0]


def md5f(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def parse(path, show_tensors=True, tensor_md5=True):
    size = os.path.getsize(path)
    fh = open(path, "rb")
    r = Rd(fh)
    magic = r.raw(4)
    if magic != b"GGUF":
        print("   !! magic 不是 GGUF: %r" % magic)
        return
    ver = r.u32()
    n_tensor = r.u64()
    n_kv = r.u64()
    print("   文件 %s" % os.path.basename(path))
    print("   size=%,d B (%.2f MB)".replace(",", "") % (size, size / 1048576)
          if False else "   size=%s B (%.2f MB)" % (format(size, ","), size / 1048576))
    print("   gguf_version=%d  n_tensors=%d  n_kv=%d" % (ver, n_tensor, n_kv))

    print("   --- KV 元数据 ---")
    kv = {}
    for _ in range(n_kv):
        k = r.s()
        t = r.u32()
        v = r.val(t)
        kv[k] = v
        sv = repr(v)
        if len(sv) > 160:
            sv = sv[:160] + "...(len=%d)" % len(v)
        print("     %-40s [%s] %s" % (k, TYPE_NAME.get(t, t), sv))

    align = int(kv.get("general.alignment", 32) or 32)
    print("   alignment=%d" % align)

    infos = []
    for _ in range(n_tensor):
        name = r.s()
        nd = r.u32()
        dims = [r.u32() for _ in range(nd)]
        tt = r.u32()
        off = r.u64()
        infos.append((name, dims, tt, off))

    pos = fh.tell()
    data_start = (pos + align - 1) // align * align

    def nbytes(dims, tt):
        n = 1
        for d in dims:
            n *= d
        if tt in KQUANT:
            nb = (n + 255) // 256
            return nb * KQUANT[tt] if nb * 256 == n or n % 256 == 0 else nb * KQUANT[tt]
        if tt in (2, 3, 4, 5, 6, 7):
            blk = {2: 32, 3: 32, 4: 32, 5: 32, 6: 32, 7: 32}[tt]
            sz = {2: 18, 3: 20, 4: 22, 5: 24, 6: 34, 7: 36}[tt]
            return ((n + blk - 1) // blk) * sz
        return n * TYPE_SIZE.get(tt, 0)

    tot = 0
    if show_tensors:
        print("   --- 张量清单 ---")
        print("     %-46s %-10s %-7s %6s %10s  %s" % ("name", "dims", "type", "n_elem", "bytes", "md5[:12]"))
        for i, (name, dims, tt, off) in enumerate(infos):
            nb = nbytes(dims, tt)
            ne = 1
            for d in dims:
                ne *= d
            h = ""
            if tensor_md5 and nb > 0:
                fh.seek(data_start + off)
                blob = fh.read(nb)
                h = hashlib.md5(blob).hexdigest()[:12]
            tot += nb
            print("     %-46s %-10s %-7s %6d %10d  %s"
                  % (name, str(dims), TYPE_NAME.get(tt, tt), ne, nb, h))

    print("   张量数据合计 %.2f MB (占文件 %.1f%%)" % (tot / 1048576, 100.0 * tot / size))
    fh.close()


if __name__ == "__main__":
    cands = []
    for pat in ["prompt_cache.gguf",
                "prompt_cache.gguf.bak_*",
                "prompt_cache.gguf.quarantine_*"]:
        cands += sorted(glob.glob(os.path.join(M, pat)))
    if len(sys.argv) > 1:
        cands = [sys.argv[1]]
    for c in cands:
        try:
            print("=" * 96)
            parse(c)
        except Exception as e:
            print("   解析失败: %r" % e)
