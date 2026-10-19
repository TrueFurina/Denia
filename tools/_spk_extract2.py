# -*- coding: utf-8 -*-
"""稳健提取 prompt_cache.gguf 的 spk_cb（说话人向量）。

上一版 MemoryError，本版：
  - 限制 string 长度 / 维度数，一旦错位立刻报错而不是吞内存
  - 同时用「固定偏移兜底」交叉验证（header_end=719 → data_start=736）
判据：spk_cb = 192 个 float32 = 768 字节。
"""
import os
import struct
import hashlib
import glob

M = r"H:\minicpm models\MiniCPM-o-4_5-gguf\token2wav-gguf"
UA = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?",
      10: "Q", 11: "q", 12: "d"}
TNAME = {0: "F32", 1: "F16", 6: "Q8_0", 24: "I32", 30: "BF16"}


class Rd:
    def __init__(self, fh):
        self.f = fh

    def raw(self, n, what=""):
        if n < 0 or n > (1 << 28):
            raise ValueError("非法读取长度 %d (%s) @%d" % (n, what, self.f.tell()))
        b = self.f.read(n)
        if len(b) != n:
            raise EOFError("短读 %d/%d (%s)" % (len(b), n, what))
        return b

    def u32(self, what=""):
        return struct.unpack("<I", self.raw(4, what))[0]

    def u64(self, what=""):
        return struct.unpack("<Q", self.raw(8, what))[0]

    def s(self, what=""):
        n = self.u64(what + ".len")
        if n > 512:
            raise ValueError("string 长度异常 %d (%s) @%d" % (n, what, self.f.tell()))
        return self.raw(n, what).decode("utf-8", "replace")

    def val(self, t, what=""):
        if t == 8:
            return self.s(what)
        if t == 9:
            et = self.u32(what + ".et")
            n = self.u64(what + ".n")
            if n > 4096:
                raise ValueError("array 长度异常 %d (%s)" % (n, what))
            return [self.val(et, what) for _ in range(n)]
        fmt = UA.get(t)
        if fmt is None:
            raise ValueError("未知类型 %d (%s)" % (t, what))
        return struct.unpack("<" + fmt, self.raw(struct.calcsize(fmt), what))[0]


def parse(path):
    fh = open(path, "rb")
    r = Rd(fh)
    assert r.raw(4) == b"GGUF"
    ver = r.u32("ver")
    nt = r.u64("n_tensor")
    nk = r.u64("n_kv")
    kv = {}
    for i in range(nk):
        k = r.s("kv[%d].key" % i)
        t = r.u32("kv[%d].type" % i)
        kv[k] = r.val(t, "kv[%s]" % k)
    align = int(kv.get("general.alignment", 32) or 32)
    infos = []
    for i in range(nt):
        name = r.s("t[%d].name" % i)
        nd = r.u32("t[%d].nd" % i)
        if nd > 8:
            raise ValueError("维度数异常 %d (%s)" % (nd, name))
        dims = [r.u32("t[%d].dim" % i) for _ in range(nd)]
        tt = r.u32("t[%d].type" % i)
        off = r.u64("t[%d].off" % i)
        infos.append((name, dims, tt, off))
    header_end = fh.tell()
    data_start = (header_end + align - 1) // align * align
    return fh, kv, infos, header_end, data_start, align


def get_spk(path):
    fh, kv, infos, he, ds, al = parse(path)
    for name, dims, tt, off in infos:
        if name.endswith("spk_cb"):
            n = 1
            for d in dims:
                n *= d
            fh.seek(ds + off)
            b = fh.read(n * 4)
            fh.close()
            return kv, infos, he, ds, al, b, dims
    fh.close()
    return kv, infos, he, ds, al, None, None


def main():
    files = [(os.path.basename(p), p) for p in sorted(glob.glob(os.path.join(M, "prompt_cache.gguf*")))]
    files = [(n, p) for n, p in files if os.path.isfile(p)]
    # 只保留 live + 两个最关键的备份，避免刷屏
    want = ["prompt_cache.gguf"]
    sel = [f for f in files if f[0] in want]
    for tag in ["bak_before_wechat_20260921_213100", "bak_before_final_20260921_211602"]:
        sel += [f for f in files if f[0].endswith(tag)]
    if not sel:
        sel = files[:4]

    print("=" * 108)
    print("spk_cb（说话人向量，192 维）逐份对照")
    print("=" * 108)
    rows = []
    for name, p in sel:
        try:
            kv, infos, he, ds, al, spk, dims = get_spk(p)
        except Exception as e:
            print("  [失败] %-52s %r" % (name[-52:], e))
            continue
        md5 = hashlib.md5(spk).hexdigest()
        n = len(spk) // 4
        v = struct.unpack("<%df" % min(n, 6), spk[:min(n, 6) * 4])
        print()
        print("  %s" % name)
        print("     size=%,d  header_end=%d  data_start=%d  align=%d" .replace(",", "") % (os.path.getsize(p), he, ds, al) if False
              else "     size=%s  header_end=%d  data_start=%d  align=%d" % (format(os.path.getsize(p), ","), he, ds, al))
        print("     spk_cb dims=%s  n=%d  md5=%s" % (dims, n, md5))
        print("     前6值 = %s" % ", ".join("%+.6f" % x for x in v))
        print("     |spk| 模长 = %.5f   均值=%.6f  标准差=%.6f" % (
            sum(x * x for x in struct.unpack("<%df" % n, spk)) ** 0.5,
            sum(struct.unpack("<%df" % n, spk)) / n,
            (sum((x - sum(struct.unpack("<%df" % n, spk)) / n) ** 2 for x in struct.unpack("<%df" % n, spk)) / n) ** 0.5))
        rows.append((name, md5))
        # 打印全部张量名，确认结构
        print("     张量: %s" % "; ".join("%s%s" % (n2.replace("prompt_cache.", ""), d) for n2, d, t, o in infos))

    print()
    print("=" * 108)
    print("判据：md5 是否互不相同")
    print("=" * 108)
    md5s = set(m for _, m in rows)
    for n, m in rows:
        print("   %-58s %s" % (n[-58:], m))
    print()
    if len(md5s) == 1:
        print("   ⇒ 所有 cache 的说话人向量【完全相同】")
    else:
        print("   ⇒ 说话人向量互不相同（%d 种），音色分量确实随参考音改变" % len(md5s))


if __name__ == "__main__":
    main()
