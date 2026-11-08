# -*- coding: utf-8 -*-
"""「工具权限分类完备性」的变异验证：三刀，刀刀必须被抓住。

跑法：.venv/Scripts/python.exe -u tools/_qc_tool_permission_mutation.py
"""
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

P_TOOLS = "src/llm/agent_tools.py"
TEST = "tools/check_tool_permission_coverage.py"
PY = ".venv/Scripts/python.exe"


# 【2026-11-08 修·刀自己会空转】原来只判 `r.returncode != 0`，于是闸门**崩了**
# 也被算成"抓住"。实测：把 check_tool_permission_coverage.py 整个换成一句
# `raise RuntimeError("闸门自己崩了")`，本刀仍然逐条打印「已抓住：<无输出>」、
# 最后打印「N 刀全红」并 exit 0 —— **一个根本跑不起来的闸门被认证成"三刀全抓"**。
# 崩了和识别是两件事：前者说明闸门没跑，后者才说明判据在工作。
# 起点也必须先绿：恒红的闸门能让**每一把**刀都变红，那种"全红"什么都不证明。
def _judged(r) -> bool:
    """判定文件是「判出了问题」而不是「崩了」或「什么都没说」。

    【2026-11-08 第二版·第一版太严】第一版还要求「输出里没有 Traceback」，
    实测把**先印 FAIL、随后收尾时崩掉**的判定判成了"没抓住"：
      · `_qc_asr_mutation` ①②：印了 `FAIL lang='zh-cn'`，随后 vosk 桩抛
        `AttributeError: 'Model' object has no attribute '_handle'`
      · `_qc_config_bootstrap_mutation` B：印了 `FAIL 状态 created`，随后读临时
        `config.yaml` 抛 `FileNotFoundError`
    两次都是**仪器太窄**（本轮第三次）。真正的判别是**有没有点名**：
    退化桩只印一个 `exit 1`，一个字都没有 —— 那才叫"什么都没说"。
    """
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode != 0 and ("FAIL" in out or "✗" in out)


def _run_test():
    return subprocess.run([PY, "-u", TEST], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _baseline_green() -> bool:
    r = _run_test()
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode == 0 and "Traceback" not in out


def E(s: str) -> bytes:
    return s.encode("utf-8")


MUTS = [
    (P_TOOLS, "A 安全类漏一个（新工具忘了归类 → 默认放行）",
     E('    "read_file", "file_search", "list_windows", "list_processes",'),
     E('    "file_search", "list_windows", "list_processes",')),

    (P_TOOLS, "B 高危类漏一个（杀进程不再询问）",
     E('    "run_command", "start_process", "kill_process",'),
     E('    "run_command", "start_process",')),

    (P_TOOLS, "C 两边重复归类（要不要确认没有答案）",
     E('WRITE_TOOLS = {\n    "write_file", "append_file", "write_clipboard",'),
     E('WRITE_TOOLS = {\n    "read_file",\n    "write_file", "append_file", "write_clipboard",')),
]


def main() -> int:
    originals = {}
    for path, *_ in MUTS:
        if path not in originals:
            originals[path] = open(path, "rb").read()
    hashes = {p: hashlib.sha256(b).hexdigest() for p, b in originals.items()}
    bad = 0
    if not _baseline_green():
        print("FAIL 起点就不是绿的 —— 恒红的闸门能让每把刀都变红，这种\"全红\"不证明任何事")
        return 1
    print("  PASS 起点绿")

    for path, name, old, new in MUTS:
        orig = originals[path]
        n = orig.count(old)
        if n != 1:
            print("[跳过] %s：锚点命中 %d 次" % (name, n))
            bad += 1
            continue
        open(path, "wb").write(orig.replace(old, new, 1))
        try:
            r = subprocess.run([PY, "-u", TEST], capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
            caught = _judged(r)
            lines = [l for l in (r.stdout or "").splitlines() if l.strip()]
            why = [l for l in lines if l.strip().startswith("FAIL")]
            why = (why or lines or ["<无输出>"])[-1].strip()[:88]
            print(name)
            print("   -> " + ("已抓住：" + why if caught else "没抓住（测试失效）"))
            if not caught:
                bad += 1
        finally:
            open(path, "wb").write(orig)

    ok = all(hashlib.sha256(open(p, "rb").read()).hexdigest() == h
             for p, h in hashes.items())
    print("\n还原字节一致：%s" % ok)
    if not ok:
        bad += 1
    print("三刀全红" if bad == 0 else "有 %d 处异常" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
