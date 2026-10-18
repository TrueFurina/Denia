# -*- coding: utf-8 -*-
"""对 doctor 的「语音合成」检查做变异验证：改坏它，测试必须抓住。

每一刀都对应一种"将来有人改松了"的真实可能。跑法：
.venv/Scripts/python.exe -u tools/_qc_voice_mutation.py

写法注意（2026-10-19 踩过三次）：
- 多行锚点用 J() 逐行拼，别在字符串里写 \n（heredoc 会把它换成字面 /n）；
- 中文不能进 bytes 字面量，一律走 E()/J() 编码。
"""
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

P = "tools/doctor.py"
TEST = "src/tests/test_doctor_voice_backend.py"
PY = ".venv/Scripts/python.exe"


def E(s: str) -> bytes:
    return s.encode("utf-8")


def J(*lines: str) -> bytes:
    return chr(10).join(lines).encode("utf-8")


MUTS = [
    ("A 空 engine 只报 WARN（回到「能收不能说」还不红）",
     E("        return (FAIL, \"语音合成\",\n"
       "                f\"{label}：未绑定任何声音，落到「静音后端」——她有回复但不说一个字\","),
     E("        return (WARN, \"语音合成\",\n"
       "                f\"{label}：未绑定任何声音，落到「静音后端」——她有回复但不说一个字\",")),

    ("B 静音后端不再判红（只记 PASS）",
     E("        return (FAIL, \"语音合成\",\n"
       "                f\"{label} 不在引擎表里 → 静音后端，她永远不说话\","),
     E("        return (PASS, \"语音合成\",\n"
       "                f\"{label} 不在引擎表里 → 静音后端，她永远不说话\",")),

    ("C 从 run_all 摘掉（写成工具却没接线）",
     # 注意：必须保持语法合法（保留 for 的冒号），否则测试是被 SyntaxError
     # 崩掉的，不是被断言抓住的——那种"红"没有区分力。
     E("                    (check_llm_channel, cfg),\n"
       "                    (check_voice_backend, cfg)):"),
     E("                    (check_llm_channel, cfg)):")),

    ("D 真合成失败也放过（后端说能出声就信）",
     E("            return (FAIL, \"语音合成\", f\"{label}：真合成失败 {type(e).__name__}: {e}\","),
     E("            return (PASS, \"语音合成\", f\"{label}：真合成失败 {type(e).__name__}: {e}\",")),

    ("E voxcpm 端口没听也判 FAIL（起服务就能好的事，过严=天天假红）",
     E("            return (WARN, \"语音合成\",\n"
       "                    f\"{label}：本机 {svc} 服务没在 {port} 端口监听 → 合成时会失败\","),
     E("            return (FAIL, \"语音合成\",\n"
       "                    f\"{label}：本机 {svc} 服务没在 {port} 端口监听 → 合成时会失败\",")),

    ("F 空文件也判 PASS（0 字节当「出了声」）",
     E("            if not (os.path.exists(out) and os.path.getsize(out) > 800):"),
     E("            if False:")),

    ("G 云端也真合成（欠费时 15s 超时卡死整个 doctor）",
     E("    if not api_url.startswith(\"http\"):"),
     E("    if True:")),
]


def main() -> int:
    orig = open(P, "rb").read()
    h0 = hashlib.sha256(orig).hexdigest()
    bad = 0

    for name, old, new in MUTS:
        if orig.count(old) != 1:
            print(f"[跳过] {name}：锚点命中 {orig.count(old)} 次（不是 1）")
            bad += 1
            continue
        open(P, "wb").write(orig.replace(old, new))
        try:
            r = subprocess.run([PY, "-u", TEST], capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
            caught = r.returncode != 0
            lines = [l for l in (r.stdout or "").splitlines() if l.strip()]
            tail = [l for l in lines if l.strip().startswith("FAIL")]
            why = (tail or lines or ["<无输出>"])[-1].strip()[:90]
            print(name)
            print("   -> " + ("已抓住：" + why if caught else "没抓住（测试失效）"))
            if not caught:
                bad += 1
        finally:
            open(P, "wb").write(orig)

    ok = hashlib.sha256(open(P, "rb").read()).hexdigest() == h0
    print(f"\n还原字节一致：{ok}")
    if not ok:
        bad += 1
    print("七刀全红" if bad == 0 else f"有 {bad} 处异常")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
