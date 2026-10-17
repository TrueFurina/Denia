# -*- coding: utf-8 -*-
"""验证 GGUF 文件完整性 + 找出 invalid vector subscript 的元凶。

llama.cpp 报 "invalid vector subscript" 通常是元数据里某个数组索引越界：
比如 tokenizer 的 token_type 数组长度 < vocab size，或某个 kv 的长度与实际不符。
本脚本手动解析 GGUF header + KV + tensor 目录，逐项体检。
"""
import os, struct, sys, json

GGUF_MAGIC = 0x46554747

FMT = {
    0: ("B", 1), 1: ("b", 1), 2: ("H", 2), 3: ("h", 2),
    4: ("I", 4), 5: ("i", 4), 6: ("f", 4), 7: ("?", 1),
    10: ("Q", 8), 11: ("q", 8), 12: ("d", 8),
}

def read_str(f):
    n = struct.unpack("<Q", f.read(8))[0]
    return f.read(n).decode("utf-8", "replace")

def read_val(f, t):
    if t == 8:      # string
        return read_str(f)
    if t == 9:      # array
        et = struct.unpack("<I", f.read(4))[0]
        n  = struct.unpack("<Q", f.read(8))[0]
        if n > 100_000_000:
            raise ValueError("array len too big: %d (type %d)" % (n, et))
        out = []
        for _ in range(n):
            out.append(read_val(f, et))
        return out
    if t not in FMT:
        raise ValueError("unknown gguf type %d" % t)
    c, sz = FMT[t]
    return struct.unpack("<" + c, f.read(sz))[0]

def parse(path, verbose=True):
    size = os.path.getsize(path)
    f = open(path, "rb")
    magic = struct.unpack("<I", f.read(4))[0]
    ver   = struct.unpack("<I", f.read(4))[0]
    n_t   = struct.unpack("<Q", f.read(8))[0]
    n_kv  = struct.unpack("<Q", f.read(8))[0]
    print("=" * 78)
    print("文件 %s" % path)
    print("  size        %d (%.2f MB)" % (size, size / 1048576))
    print("  magic       %s" % (hex(magic), ))
    print("  version     %d" % ver)
    print("  n_tensors   %d" % n_t)
    print("  n_kv        %d" % n_kv)

    kvs = {}
    print("\n--- KV 元数据 ---")
    try:
        for _ in range(n_kv):
            k = read_str(f)
            t = struct.unpack("<I", f.read(4))[0]
            v = read_val(f, t)
            kvs[k] = v
            if isinstance(v, list):
                s = "list(len=%d) head=%r" % (len(v), v[:4])
            elif isinstance(v, str):
                s = repr(v[:60])
            else:
                s = repr(v)
            print("  %-44s %s" % (k, s))
    except Exception as e:
        print("  !! KV 解析失败: %s" % e)
        print("     已解析 %d/%d 项，文件很可能被截断" % (len(kvs), n_kv))
        return kvs, None, size

    # 交叉校验：常见越界源
    print("\n--- 一致性校验 ---")
    checks = []
    n_vocab = kvs.get("tokenizer.ggml.tokens")
    if isinstance(n_vocab, list):
        V = len(n_vocab)
        checks.append(("tokenizer.ggml.tokens 长度", V))
        for key in ("tokenizer.ggml.scores", "tokenizer.ggml.token_type",
                    "tokenizer.ggml.merges"):
            a = kvs.get(key)
            if isinstance(a, list):
                ok = (len(a) == V) if key != "tokenizer.ggml.merges" else True
                checks.append(("%s 长度=%d (%s)" % (key, len(a),
                              "OK" if ok else "!! 应=%d" % V), None))
    for name, val in checks:
        if val is not None:
            print("  %s = %s" % (name, val))
        else:
            print("  %s" % name)

    # tensor 目录
    print("\n--- 张量目录（前 12 + 后 5）---")
    tensors = []
    try:
        for i in range(n_t):
            nm = read_str(f)
            nd = struct.unpack("<I", f.read(4))[0]
            dims = [struct.unpack("<Q", f.read(8))[0] for _ in range(nd)]
            tt = struct.unpack("<I", f.read(4))[0]
            off = struct.unpack("<Q", f.read(8))[0]
            tensors.append((nm, dims, tt, off))
    except Exception as e:
        print("  !! 张量目录解析失败于第 %d 个: %s" % (len(tensors), e))
        print("     文件在张量目录区就被截断了 —— 这就是 invalid vector subscript 的成因")
        return kvs, None, size

    for nm, dims, tt, off in tensors[:12]:
        print("  %-52s %s type=%d off=%d" % (nm[:52], dims, tt, off))
    if len(tensors) > 17:
        print("  ...")
        for nm, dims, tt, off in tensors[-5:]:
            print("  %-52s %s type=%d off=%d" % (nm[:52], dims, tt, off))

    # 数据区起点 + 最大 offset
    data_start = f.tell()
    # 对齐
    al = kvs.get("general.alignment", 32)
    data_start = (data_start + al - 1) // al * al
    max_need = 0
    for nm, dims, tt, off in tensors:
        ne = 1
        for d in dims:
            ne *= d
        # 粗略按 2 字节估（F16 最常见），只用于判断"是否明显越界"
        max_need = max(max_need, data_start + off + ne * 2)
    print("\n--- 数据完整性 ---")
    print("  data_start  %d" % data_start)
    print("  文件大小    %d" % size)
    print("  数据区需要  >=%d (%.2f MB)" % (max_need, max_need / 1048576))
    verdict = "OK 尺寸合理" if size >= max_need else "!! 文件小于张量所需，被截断"
    print("  => %s" % verdict)
    return kvs, tensors, size


if __name__ == "__main__":
    paths = sys.argv[1:] or [
        r"H:\minicpm models\MiniCPM-o-4_5-gguf\tts\MiniCPM-o-4_5-tts-F16.gguf",
    ]
    for p in paths:
        if os.path.exists(p):
            try:
                parse(p)
            except Exception as e:
                import traceback; traceback.print_exc()
        else:
            print("MISSING", p)
        print()
