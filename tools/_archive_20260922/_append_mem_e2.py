# -*- coding: utf-8 -*-
"""在 MEMORY.md 末尾追加 §2as。
⚠️ 该文件历史含孤立代理对（surrogate），任何 decode->encode 往返都会破坏它。
本脚本安全做法：**只读尾部做校验，直接以二进制 append，从不整体重写**。
"""
P = r"G:\ai对话\.workbuddy\memory\MEMORY.md"

BLOCK = (
    "- **2as 16k->24k \u4fee\u590d\uff1a\u4ee3\u7801\u751f\u6548\u4f46\u3010\u5b9e\u6d4b\u672a\u63d0\u5347\u76f8\u4f3c\u5ea6\u3011\uff08\u8bda\u5b9e\u8d1f\u7ed3\u679c\u00b709-22\uff09**\uff1a"
    "`ensure_usable_wav(path,target_sr=16000)` \u65b0\u589e\u53c2\u6570 + `cosyvoice_server.py` \u4f20 24000\uff08\u5907\u4efd `.bak_sr24k_20260922_115958`\uff0c"
    "md5 `be3ac34ae179`/`66c01654b053`\uff09\u3002\u4fee\u590d**\u786e\u5b9e\u751f\u6548**\uff1a8-12k \u4ece **-78.7dB \u6062\u590d\u4e3a -7.8dB**\u3002"
    "**\u4f46\u5bf9\u7167\uff08\u540c\u53c2\u8003\u97f3/\u540c\u53e5/\u53ea\u6539\u94fe\u8def\uff09\u4e09\u9879\u5168\u90e8\u7565\u5dee**\uff1a"
    "A(tsr=24000) F0=203.4 \u0394F0=-36.6 6-8k=-37.2 cos\u2194SRC=0.5068\uff5cB(tsr=16000) 198.3/-41.7/-28.3/0.5225\u3002"
    "\u21d2 \u4e0a\u4e00\u8f6e\u300c16k \u964d\u91c7\u6837\u662f\u4e3b\u56e0\u300d\u5047\u8bbe**\u5df2\u8bc1\u4f2a**\u3002"
    "\u673a\u7406\uff1aCV2 \u97f3\u8272\u4e3b\u901a\u9053\u662f **16k campplus spk**\uff08`_extract_spk_embedding\u2192load_wav(wav,16000)`\uff09**\u7269\u7406\u4e0a\u770b\u4e0d\u5230 >8k**\uff0c"
    "flow mel \u8981 24k \u4f46\u53ea\u627f\u97f5\u5f8b/\u5185\u5bb9\u3002 KEY **\u53ef\u63a8\u5e7f\u5224\u636e\uff1a\u5148\u95ee\u300c\u8fd9\u6761\u901a\u9053\u7269\u7406\u4e0a\u80fd\u770b\u89c1\u591a\u5c11\u5e26\u5bbd\u300d\u518d\u8c08\u4f18\u5316**\u3002"
    "!!! **\u65b0\u53d1\u73b0\uff08\u66f4\u503c\u5f97\u6392\u67e5\uff09\uff1aCV2 \u96f6\u6837\u672c \u0394F0 \u7cfb\u7edf\u6027\u504f\u4f4e -34~-40Hz\uff08\u7ea6 -3~-4 \u534a\u97f3\uff09**\uff0c"
    "240Hz\u2192200-207Hz\uff1b\u800c**\u53cc\u5de5\u8def\u5f84 \u0394F0=+10.0**\uff08\u65b9\u5411\u76f8\u53cd\uff09\u3002\u5f85\u67e5\uff1a\u53c2\u8003\u97f3\u622a\u4e2d\u95f4/\u592a\u77ed(7.04s)/48k \u53c2\u8003\u97f3/speed-pitch \u53c2\u6570\u3002"
    "**\u53e3\u5f84\u7ea2\u7ebf**\uff1a\u53cc\u5de5 0.7103 vs CV2 0.51 **\u4e0d\u662f\u540c\u4e00\u53e5\u8bdd**\uff0c\u76f4\u63a5\u6bd4\u5927\u5c0f\u662f\u9519\u7684\u3002\u2192 \u00a725.15\uff0c"
    "\u62a5\u544a `11/docs/16k\u523024k\u53c2\u8003\u97f3\u4fee\u590d_\u5b9e\u65bd\u4e0e\u9a8c\u6536\u62a5\u544a.md`\uff0c"
    "\u8bd5\u542c\u5305 `11/data/\u9a8c\u6536\u8bd5\u542c\u5305_24k/`\uff088 wav\uff0c\u542b `99_\u4fee\u590d\u524d` / `99b_\u4fee\u590d\u540e` A/B\uff09\u3002"
    "**\u4fdd\u7559\u672c\u6b21\u4fee\u590d\uff08\u4e0d\u56de\u9000\uff09\uff0c\u4f46\u4e0d\u518d\u628a\u5b83\u5f53\u4f5c\u201c\u5feb\u901f\u63d0\u5347\u201d\u8def\u7ebf\u3002**"
)


def main():
    b = open(P, "rb").read()
    if b.count(b"\r\r") != 0:
        raise SystemExit("MEMORY.md already has \\r\\r, abort")
    if b"\x32\x61\x73\x20\x31\x36\x6b".decode("ascii") in b.decode("latin-1"):
        pass
    # 检测是否已追加（用 ASCII 探针，避免解码整个文件）
    if b.find("2as 16k".encode("utf-8")) >= 0:
        print("already has 2as, skip")
        return
    need_nl = not b.endswith(b"\r\n")
    payload = (b"\r\n" if need_nl else b"") + BLOCK.encode("utf-8") + b"\r\n"
    with open(P, "ab") as f:
        f.write(payload)
    b2 = open(P, "rb").read()
    assert b2.count(b"\r\r") == 0, "rr introduced!"
    assert b2[: len(b)] == b, "original bytes were modified!"
    print("MEMORY.md", len(b), "->", len(b2), "crr", b2.count(b"\r\r"))
    print("2as probe found:", b2.find("2as 16k".encode("utf-8")) >= 0)


if __name__ == "__main__":
    main()
