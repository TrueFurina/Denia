# -*- coding: utf-8 -*-
"""渠道探活判级的变异验证：七刀，每刀都必须让测试变红。"""
import hashlib
import subprocess
import sys

P = "tools/doctor.py"
TEST = "src/tests/test_doctor_llm_channel.py"
PY = ".venv/Scripts/python.exe"


def E(s: str) -> bytes:
    """中文不能写进 bytes 字面量，统一在这里转。"""
    return s.encode("utf-8")

def J(*lines: str) -> bytes:
    """逐行拼成 CRLF 再编码。

    两个坑都在 2026-10-18 踩过：① 多行锚点必须跟目标文件同一换行风格——
    tools/doctor.py 是 CRLF，用 "\n" 拼就永远命中 0 次；② Git Bash 的 heredoc 会把
    `\n` 换成字面 `/n`，所以这里用 chr(13)/chr(10) 拼，不用转义序列。
    """
    sep = bytes((13, 10))
    return sep.join(x.encode("utf-8") for x in lines)


MUTS = [
    ("A 400 不判红（回到老问题：账户欠费只报 WARN）", [
        (b"            elif e.code in (400, 401, 402, 403, 404):", b"            elif False:"),
    ]),
    ("B 429 也算失败（限流被当故障，doctor 假红）", [
        (b"            if e.code == 429:", b"            if False:"),
    ]),
    ("C 断网也判红（没网时 doctor 全红）", [
        (J("            if level == PASS:",
           "                level = WARN",
           "            details.append(f\"{label}（{model}）? {type(e).__name__}"
           "（网络不通或超时）\")"),
         J("            level = FAIL",
           "            details.append(f\"{label}（{model}）? {type(e).__name__}\")")),
    ]),
    ("D 从 run_all 摘掉（写成工具却没接线）", [
        (b"                    (check_llm_channel, cfg)):", b"                    )"),
    ]),
    ("E 探活去重失效（同一端点发两遍请求）", [
        (b"        if sig not in seen:", b"        if True:"),
    ]),
    ("F 空 key 也发请求（没 key 还打网络，必然 401）", [
        (J("        if not key:",
           "            if level == PASS:",
           "                level = WARN"),
         J("        if False:",
           "            if level == PASS:",
           "                level = WARN")),
    ]),
    ("G 5xx 也判 FAIL（服务商自己抽风，不该赖用户头上）", [
        (J("            else:",
           "                if level == PASS:",
           "                    level = WARN",
           "                details.append(f\"{label}（{model}）? HTTP {e.code} {msg}\")"),
         J("            else:",
           "                level = FAIL",
           "                details.append(f\"x HTTP {e.code} {msg}\")")),
    ])
]


def run_test():
    r = subprocess.run([PY, "-u", TEST], capture_output=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "")


def main() -> int:
    orig = open(P, "rb").read()
    h0 = hashlib.sha256(orig).hexdigest()
    rc0, out0 = run_test()
    if rc0 != 0:
        print("✗ 基线测试就没过，先修测试再谈变异")
        print("\n".join(out0.splitlines()[-10:]))
        return 1
    print("基线：测试通过 ✓\n")

    misses = 0
    for name, subs in MUTS:
        b = orig
        hit = all(b.count(o) == 1 for o, _ in subs)
        if not hit:
            print(f"[跳过] {name}：锚点命中 "
                  f"{[b.count(o) for o, _ in subs]}")
            misses += 1
            continue
        for o, n in subs:
            b = b.replace(o, n)
        open(P, "wb").write(b)
        rc, out = run_test()
        caught = rc != 0
        why = ""
        if caught:
            bad = [l.strip() for l in out.splitlines() if "❌" in l]
            if bad:
                why = "　" + bad[-1][:90]
        else:
            misses += 1
        print(f"{'✓ 已红' if caught else '❌ 未抓住'}  {name}{why}")
        open(P, "wb").write(orig)

    ok = hashlib.sha256(open(P, "rb").read()).hexdigest() == h0
    print(f"\n还原字节一致：{ok}")
    if misses or not ok:
        print(f"✗ {misses} 刀没抓住")
        return 1
    print("✅ 七刀全红，判级逻辑有测试守着")
    return 0


if __name__ == "__main__":
    sys.exit(main())
