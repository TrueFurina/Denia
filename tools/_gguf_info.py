# -*- coding: utf-8 -*-
"""最小 GGUF 头解析：列出 prompt_cache.gguf 的 KV 与张量（看什么随 prompt 长度变化）。"""
import os, struct, sys

TYPES = {0: ("u8", 1), 1: ("i8", 1), 2: ("u16", 2), 3: ("i16", 2), 4: ("u32", 4), 5: ("i32", 4),
         6: ("f32", 4), 7: ("bool", 1), 8: ("str", 0), 9: ("arr", 0), 10: ("u64", 8), 11: ("i64", 8),
         12: ("f64", 8)}
# 量化类型
GGML = {0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 6: "Q5_0", 7: "Q5_1", 8: "Q8_0", 9: "Q8_1",
        10: "Q2_K", 11: "Q3_K", 12: "Q4_K", 13: "Q5_K", 14: "Q6_K", 15: "Q8_K", 16: "IQ2_XXS"}


def read_str(f):
    n = struct.unpack("<Q", f.read(8))[0]
    return f.read(n).decode("utf-8", "replace")


def read_val(f, t):
    if t == 8:
        return read_str(f)
    if t == 9:
        et = struct.unpack("<I", f.read(4))[0]
        n = struct.unpack("<Q", f.read(8))[0]
        if n > 64:
            return "<array of %d (%s)>" % (n, TYPES.get(et, ("?",))[0])
        return [read_val(f, et) for _ in range(n)]
    fmt, sz = TYPES[t]
    return struct.unpack("<" + {"u8": "B", "i8": "b", "u16": "H", "i16": "h", "u32": "I", "i32": "i",
                                "f32": "f", "bool": "?", "u64": "Q", "i64": "q", "f64": "d"}[fmt], f.read(sz))[0]


def info(path):
    with open(path, "rb") as f:
        mg = f.read(4)
        if mg != b"GGUF":
            print("  非 GGUF:", mg); return
        ver = struct.unpack("<I", f.read(4))[0]
        n_t = struct.unpack("<Q", f.read(8))[0]
        n_k = struct.unpack("<Q", f.read(8))[0]
        print("  %s  ver=%d  tensors=%d  kvs=%d  size=%.1fMB" %
              (os.path.basename(path), ver, n_t, n_k, os.path.getsize(path) / 1048576))
        print("  --- KV ---")
        for _ in range(n_k):
            k = read_str(f)
            t = struct.unpack("<I", f.read(4))[0]
            v = read_val(f, t)
            sv = str(v)
            if len(sv) > 110:
                sv = sv[:110] + "..."
            print("     %-40s = %s" % (k, sv))
        print("  --- tensors ---")
        tot = 0
        for _ in range(n_t):
            name = read_str(f)
            nd = struct.unpack("<I", f.read(4))[0]
            dims = [struct.unpack("<Q", f.read(8))[0] for _ in range(nd)]
            qt = struct.unpack("<I", f.read(4))[0]
            off = struct.unpack("<Q", f.read(8))[0]
            n_el = 1
            for d in dims: n_el *= d
            print("     %-44s %-6s dims=%s" % (name, GGML.get(qt, str(qt)), dims))
            tot += n_el
        print("  元素总数 %d" % tot)


for p in sys.argv[1:]:
    print("=" * 90)
    if os.path.exists(p):
        info(p)
    else:
        print("MISSING:", p)
