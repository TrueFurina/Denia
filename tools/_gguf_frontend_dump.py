# -*- coding: utf-8 -*-
"""
_gguf_frontend_dump.py  —  直接读 GGUF 里的前端矩阵（mel 滤波器组 / 窗口）

为什么这么做：
  omni.dll 的字符串里有
      "Loaded mel filters: %d mel bins x %d fft bins = %d elements"
      "Missing mel filters data in model file"
  说明 mel 滤波器组是**存在模型文件里、运行时加载**的矩阵。
  把它读出来 → 可以直接量出 fmax / 频带覆盖，不必再用音频反推。

做法：手工解析 GGUF 头（magic/version/n_tensors/n_kv → KV → tensor info），
      再按 offset 定位 F32 数据，直接算每个 mel 三角滤波器的中心频率与上限。

只读，不写任何文件。
"""
import os
import struct
import sys
import numpy as np

GGUF_MAGIC = b"GGUF"

# GGUF value type ids
( U8, I8, U16, I16, U32, I32, F32, BOOL, STRING, ARRAY, U64, I64, F64 ) = range(13)

TYPE_SIZE = {U8: 1, I8: 1, U16: 2, I16: 2, U32: 4, I32: 4, F32: 4, BOOL: 1, U64: 8, I64: 8, F64: 8}

# ggml type ids -> (name, bytes per element) 只列常用的
GGML_TYPES = {
    0: ("F32", 4), 1: ("F16", 2), 2: ("Q4_0", None), 3: ("Q4_1", None),
    6: ("Q5_0", None), 7: ("Q5_1", None), 8: ("Q8_0", None), 9: ("Q8_1", None),
    10: ("Q2_K", None), 11: ("Q3_K", None), 12: ("Q4_K", None), 13: ("Q5_K", None),
    14: ("Q6_K", None), 15: ("Q8_K", None), 16: ("IQ2_XXS", None), 17: ("IQ2_XS", None),
    18: ("IQ3_XXS", None), 19: ("IQ1_S", None), 20: ("IQ4_NL", None), 21: ("IQ3_S", None),
    22: ("IQ2_S", None), 23: ("IQ4_XS", None), 24: ("I8", 1), 25: ("I16", 2),
    26: ("I32", 4), 27: ("I64", 8), 28: ("F64", 8), 30: ("BF16", 2),
}


class R:
    def __init__(self, b):
        self.b = b
        self.p = 0

    def u32(self):
        v = struct.unpack_from("<I", self.b, self.p)[0]
        self.p += 4
        return v

    def u64(self):
        v = struct.unpack_from("<Q", self.b, self.p)[0]
        self.p += 8
        return v

    def i64(self):
        v = struct.unpack_from("<q", self.b, self.p)[0]
        self.p += 8
        return v

    def f32(self):
        v = struct.unpack_from("<f", self.b, self.p)[0]
        self.p += 4
        return v

    def f64(self):
        v = struct.unpack_from("<d", self.b, self.p)[0]
        self.p += 8
        return v

    def s(self):
        n = self.u64()
        v = self.b[self.p:self.p + n].decode("utf-8", "replace")
        self.p += n
        return v

    def val(self, t):
        if t == U8:
            v = self.b[self.p]; self.p += 1; return v
        if t == I8:
            v = struct.unpack_from("<b", self.b, self.p)[0]; self.p += 1; return v
        if t == U16:
            v = struct.unpack_from("<H", self.b, self.p)[0]; self.p += 2; return v
        if t == I16:
            v = struct.unpack_from("<h", self.b, self.p)[0]; self.p += 2; return v
        if t == U32:
            return self.u32()
        if t == I32:
            v = struct.unpack_from("<i", self.b, self.p)[0]; self.p += 4; return v
        if t == F32:
            return self.f32()
        if t == BOOL:
            v = self.b[self.p]; self.p += 1; return bool(v)
        if t == STRING:
            return self.s()
        if t == U64:
            return self.u64()
        if t == I64:
            return self.i64()
        if t == F64:
            return self.f64()
        if t == ARRAY:
            et = self.u32()
            n = self.u64()
            if et == STRING:
                return [self.s() for _ in range(n)]
            if n > 64:                     # 大数组（如 tokenizer merges）只报长度
                self.p += TYPE_SIZE.get(et, 0) * n
                return "<array %d of type %d>" % (n, et)
            return [self.val(et) for _ in range(n)]
        raise ValueError("unknown gguf type %d at %d" % (t, self.p))


def parse(path):
    with open(path, "rb") as f:
        head = f.read(4)
        if head != GGUF_MAGIC:
            raise ValueError("not gguf: %s" % head)
        ver = struct.unpack("<I", f.read(4))[0]
        n_tensors = struct.unpack("<Q", f.read(8))[0]
        n_kv = struct.unpack("<Q", f.read(8))[0]
        # 先粗略读 4MB 头部（KV + tensor info 都在头部）
        f.seek(0)
        blob = f.read(min(os.path.getsize(path), 24 * 1024 * 1024))
        data_start = None

    r = R(blob)
    r.p = 4 + 4 + 8 + 8
    meta = {}
    for _ in range(n_kv):
        k = r.s()
        t = r.u32()
        meta[k] = r.val(t)
    tensors = []
    for _ in range(n_tensors):
        name = r.s()
        nd = r.u32()
        dims = [r.u64() for _ in range(nd)]
        ttype = r.u32()
        off = r.u64()
        tensors.append((name, dims, ttype, off))
        if r.p > len(blob) - 8:
            break
    header_end = r.p
    align = meta.get("general.alignment", 32)
    data_start = (header_end + align - 1) // align * align
    return meta, tensors, data_start


def read_f32(path, data_start, offset, shape):
    n = 1
    for d in shape:
        n *= d
    with open(path, "rb") as f:
        f.seek(data_start + offset)
        raw = f.read(4 * n)
    return np.frombuffer(raw, dtype="<f4").astype(np.float64).reshape(shape)


def mel_geometry(fb, sr, n_fft):
    """从滤波器组矩阵反推每个三角的峰值位置 → 覆盖上限。"""
    n_mels, n_bins = fb.shape
    freqs = np.linspace(0, sr / 2.0, n_bins)           # 假设 fft 频点线性
    peaks, edges = [], []
    for i in range(n_mels):
        w = fb[i]
        if w.max() <= 0:
            peaks.append(np.nan)
            edges.append((np.nan, np.nan))
            continue
        pk = int(np.argmax(w))
        peaks.append(freqs[pk])
        idx = np.nonzero(w > 0)[0]
        edges.append((freqs[idx[0]], freqs[idx[-1]]))
    return np.array(peaks), edges, freqs


def report(path, only_mel=True):
    print("=" * 78)
    print("FILE:", path, "  %.1f MB" % (os.path.getsize(path) / 1024 ** 2))
    meta, tensors, ds = parse(path)
    arch = meta.get("general.architecture") or meta.get("general.name")
    print("  arch/name:", arch, "| tensors:", len(tensors), "| data_start:", ds)

    interesting = [(n, d, t, o) for (n, d, t, o) in tensors
                   if ("mel" in n.lower() or "filter" in n.lower() or "window" in n.lower()
                       or "hann" in n.lower() or "fft" in n.lower())]
    if not interesting:
        print("   (无 mel/filter/window 张量)")
    for (n, d, t, o) in interesting:
        tn = GGML_TYPES.get(t, ("type%d" % t, None))[0]
        print("   %-46s shape=%-18s type=%s" % (n, str(d), tn))
        if tn == "F32" and len(d) == 2 and 0 < int(np.prod(d)) < 2_000_000:
            try:
                fb = read_f32(path, ds, o, list(d))
                print("      -> 矩阵 min=%.4g max=%.4g  非零行=%d/%d"
                      % (fb.min(), fb.max(), int((fb.max(axis=1) > 0).sum()), fb.shape[0]))
                for sr in (16000, 24000):
                    pk, edges, freqs = mel_geometry(fb, sr, (int(d[1]) - 1) * 2)
                    top = pk[~np.isnan(pk)]
                    print("      -> 若 sr=%d：三角峰值 %0.1f~%0.1f Hz；最右非零边 %.1f Hz"
                          % (sr, top.min(), top.max(), max(e[1] for e in edges if not np.isnan(e[1]))))
            except Exception as e:
                print("      (读数据失败: %s)" % e)
    return meta, tensors, ds


def all_tensor_names(path, maxn=40, keep=None):
    meta, tensors, ds = parse(path)
    print("=" * 78)
    print("TENSOR LIST:", path, "(%d)" % len(tensors))
    for (n, d, t, o) in tensors:
        tn = GGML_TYPES.get(t, ("type%d" % t, None))[0]
        if keep and not any(k in n.lower() for k in keep):
            continue
        print("   %-52s %-16s %s" % (n, str(d), tn))


if __name__ == "__main__":
    root = r"H:\minicpm models\MiniCPM-o-4_5-gguf"
    targets = [
        os.path.join(root, "tts", "MiniCPM-o-4_5-tts-F16.gguf"),
        os.path.join(root, "audio", "MiniCPM-o-4_5-audio-F16.gguf"),
        os.path.join(root, "token2wav-gguf", "encoder.gguf"),
        os.path.join(root, "token2wav-gguf", "hifigan2.gguf"),
        os.path.join(root, "token2wav-gguf", "flow_matching.gguf"),
        os.path.join(root, "token2wav-gguf", "prompt_cache.gguf"),
    ]
    for p in targets:
        if not os.path.exists(p):
            print("MISSING", p)
            continue
        try:
            report(p)
        except Exception as e:
            print("!! %s -> %s: %s" % (p, type(e).__name__, e))
    print()
    print("### token2wav 侧与前端/条件相关的张量名 ###")
    for p in targets:
        if os.path.exists(p):
            try:
                all_tensor_names(p, keep=["mel", "filter", "window", "spk", "prompt", "cond", "embed", "token"])
            except Exception as e:
                print("!!", p, e)
