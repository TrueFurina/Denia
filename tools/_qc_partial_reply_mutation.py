# -*- coding: utf-8 -*-
"""「LLM 收尾失败本地事实汇报」的变异验证：每把刀都必须让它变红。

三把刀分别钉住三条纪律：
  A 不列工具事实（退回把原始错误串丢给用户）—— 本次要修的缺陷本身；
  B 丢掉「LLM 调用失败」标记 —— 账本 partial_execution 会静默漏记；
  C 主循环不调用 helper —— 判据只在纯函数层绿、真实链路拿不到。

跑法：.venv/Scripts/python.exe -u tools/_qc_partial_reply_mutation.py
"""
from __future__ import annotations

import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
PY = ".venv/Scripts/python.exe"
TEST = "tools/check_partial_reply.py"
BRAIN = "src/llm/agent_brain.py"


def read(rel):
    with io.open(rel, "r", encoding="utf-8", newline="") as f:
        return f.read()


def write(rel, text):
    with io.open(rel, "wb") as f:
        f.write(text.encode("utf-8"))


def run_test():
    r = subprocess.run([PY, "-u", TEST], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


MUTS = [
    ("A helper 直接返回原始错误串（不列工具事实）",
     '    names = "、".join(dict.fromkeys(tools))  # 保序去重，次数仍用原始长度\n'
     '    return ("已实际执行 %d 次工具调用（%s）。但%s —— 最终回复没能生成；"\n'
     '            "上述动作是否达成目标，未做复核。"\n'
     '            % (len(tools), names, base))\n',
     '    names = "、".join(dict.fromkeys(tools))\n'
     '    return base\n'),
    ("B 丢掉「LLM 调用失败」标记（账本耦合断掉）",
     '    base = ("电脑代理 LLM 调用失败（HTTP %d）：%s"\n',
     '    base = ("电脑代理 LLM 未响应（HTTP %d）：%s"\n'),
    ("C 主循环改回原始 return（真实链路拿不到）",
     '                        return _llm_failure_reply(\n'
     '                            resp.status_code, resp.text,\n'
     '                            list(getattr(self, "_last_tools_used", []) or []))\n',
     '                        return f"电脑代理 LLM 调用失败（HTTP {resp.status_code}）：" \\\n'
     '                               f"{resp.text[:200]}"\n'),
]


def main() -> int:
    rc, out = run_test()
    print("基线:", "全绿 ✓" if rc == 0 else "已红 ✗（无法做变异验证）")
    if rc != 0:
        print(out[-500:])
        return 1
    print()

    raw = read(BRAIN)
    bad = 0
    for label, old, new in MUTS:
        if raw.count(old) != 1:
            print("  ✗ 锚点命中 %d 次: %s" % (raw.count(old), label))
            bad += 1
            continue
        try:
            write(BRAIN, raw.replace(old, new, 1))
            rc2, out2 = run_test()
            caught = rc2 != 0
            print(("  ✓ 变红 " if caught else "  ✗ 没红 ") + label)
            if not caught:
                bad += 1
                print("     ", out2[-220:].replace("\n", " "))
        finally:
            write(BRAIN, raw)

    rc3, _ = run_test()
    print("\n还原后基线:", "全绿 ✓" if rc3 == 0 else "仍红 ✗")
    if rc3 != 0:
        bad += 1
    print("结果: %d 把刀，%d 把没被抓住" % (len(MUTS), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
