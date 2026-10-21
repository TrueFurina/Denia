# -*- coding: utf-8 -*-
"""渠道探活判级的变异验证：七刀，每刀都必须让测试变红。

【2026-10-22 修】这把刀**从没进过闸门**，于是悄悄烂了：七刀里 C/D/F/G 四把的锚点
命中 0 次、被 "[跳过]" 静默放过，而它们验证的判级逻辑（断网不判红 / 空 key 不发包 /
5xx 不判红 / run_all 必须接线）一直在 `test_doctor_llm_channel.py` 里被守着——
也就是说那几组断言其实没有牙齿。两个根因：

  ① `J()` 把 CRLF **写死**了，而 `tools/doctor.py` 后来变成了 LF
     → C/F/G 三把多行锚点全部落空。判据同 MEMORY：**锚点不许假设目标文件的换行风格**，
     必须实测（`_eol()`）。单行锚点（A/B/E）不受影响，所以坏得很隐蔽。
  ② D 的锚点还是老文本 `(check_llm_channel, cfg)):`，run_all 那行早变成了
     `(check_llm_channel, cfg),`。

现在已登记进 `check_all.py`（"渠道探活判级变异"），锚点失效会直接让全量门禁变红。
"""
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "tools", "doctor.py")
TEST = os.path.join("src", "tests", "test_doctor_llm_channel.py")
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")


def E(s: str) -> bytes:
    """中文不能写进 bytes 字面量，统一在这里转。"""
    return s.encode("utf-8")


def _eol() -> bytes:
    """按 doctor.py **实测**的换行符拼多行锚点。

    原来这里写死 CRLF（doctor.py 当年是 CRLF），后来文件变成 LF → 三把多行锚点
    全部命中 0 次、被静默跳过。**换行符必须实测，别猜。**
    """
    with open(P, "rb") as f:
        head = f.read()
    return b"\r\n" if b"\r\n" in head else b"\n"


def J(*lines: str) -> bytes:
    """逐行拼成目标文件的实际换行符再编码。

    两个坑都在 2026-10-18 踩过：① 多行锚点必须跟目标文件同一换行风格——写死就
    会在文件换行符变化后永远命中 0 次；② Git Bash 的 heredoc 会把 `\n` 换成字面
    `/n`，所以这里用 chr(13)/chr(10) 拼，不用转义序列。
    """
    sep = _eol()
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
           "            details.append(f\"{label}（{model}）? {type(e).__name__}（网络不通或超时）\")"),
         J("            level = FAIL",
           "            details.append(f\"{label}（{model}）? {type(e).__name__}（网络不通或超时）\")")),
    ]),
    ("D 从 run_all 摘掉（写成工具却没接线）", [
        # 整行删掉（连换行符一起）→ inspect.getsource(run_all) 里不再有这个名字
        (J("                    (check_llm_channel, cfg),"), b""),
    ]),
    ("E 探活去重失效（同一端点发两遍请求）", [
        (b"        if sig not in seen:", b"        if True:"),
    ]),
    ("F 空 key 也发请求（没 key 还打网络，必然 401）", [
        (J("        if not key:",
           "            if level == PASS:",
           "                level = WARN",
           "            details.append(f\"{label}（{model}）未填 api_key\")"),
         J("        if False:",
           "            if level == PASS:",
           "                level = WARN",
           "            details.append(f\"{label}（{model}）未填 api_key\")")),
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
    r = subprocess.run([PY, "-u", TEST], cwd=ROOT, capture_output=True,
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
            # 锚点失效 = 刀烂了。它验证的检查器还在闸门里，所以必须算失败、
            # 必须让全量门禁变红——不能 silently skip（本项目踩过三次的同个坑）。
            print(f"[跳过] {name}：锚点命中 "
                  f"{[b.count(o) for o, _ in subs]} ← 刀已失效，不是通过")
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
