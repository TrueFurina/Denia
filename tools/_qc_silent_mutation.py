# -*- coding: utf-8 -*-
"""变异验证：主链路静默异常闸门（check_silent_except --main --strict）。

四刀：
  A 注入一个新的"真空吞"（except: pass 且块内无注释）到 tts.py → 必须 FAIL 并点名
  B 把主链路文件改成语法错误 → 必须 FAIL（历史缺陷：解析失败会被静默跳过，
    闸门反而更绿）
  C 主链路文件不存在 → 必须 FAIL（历史缺陷：os.path.exists 为假时直接 continue）
  D 【2026-10-24 新增】往 proactive.py 注入真空吞 → 必须 FAIL 并点名
    （proactive.py 本轮刚纳入主链，证明确实有牙齿；不验的话"扩了范围"和
    "没扩"在汇总里长得一模一样）

用法：python tools/_qc_silent_mutation.py
退出码：0 = 四刀全被抓住且还原成功。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "src", "voice", "tts.py")
TARGET_D = os.path.join(ROOT, "src", "companion", "proactive.py")
TOOL = os.path.join(ROOT, "tools", "check_silent_except.py")
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
if not os.path.exists(PY):
    PY = sys.executable
NL = chr(10)


def run_tool() -> tuple[int, str]:
    r = subprocess.run([PY, "-u", TOOL, "--main", "--strict", "--list"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=ROOT)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main() -> int:
    orig = open(TARGET, "rb").read()
    h0 = hashlib.sha256(orig).hexdigest()
    bad = 0

    # A：注入真空吞
    anchor = "            self._save_wav_copy(playable)  # 存档供聊天页语音回放".encode("utf-8")
    injected = ("            try:" + NL +
                "                _ = 1 / 0" + NL +
                "            except Exception:" + NL +
                "                pass" + NL).encode("utf-8")
    assert orig.count(anchor) == 1
    open(TARGET, "wb").write(orig.replace(anchor, injected + anchor))
    rc, out = run_tool()
    hit = "tts.py" in out and "真静默" in out
    print(f"A 注入真空吞 -> rc={rc} {'✓ 抓住' if rc == 1 and hit else '❌ 未抓住'}")
    if rc != 1 or not hit:
        bad += 1
        print(out[-400:])
    open(TARGET, "wb").write(orig)

    # B：语法错误（模拟文件被改坏）
    open(TARGET, "wb").write(orig + ("def broken(:" + NL).encode("utf-8"))
    rc, out = run_tool()
    hit = rc == 1 and "SyntaxError" in out
    print(f"B 文件语法错误 -> rc={rc} {'✓ 抓住' if hit else '❌ 未抓住（闸门沉默了）'}")
    if not hit:
        bad += 1
        print(out[-400:])
    open(TARGET, "wb").write(orig)

    # C：主链路文件不存在（用 in-process 打桩，不动真实文件）
    code = (
        "import sys" + NL +
        "sys.path.insert(0, 'tools')" + NL +
        "import check_silent_except as C" + NL +
        "C.MAIN_CHAIN = ['src/voice/__not_exist__.py']" + NL +
        "sys.argv = ['x', '--main', '--strict']" + NL +
        "raise SystemExit(C.main())" + NL
    )
    r = subprocess.run([PY, "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=ROOT)
    out = (r.stdout or "") + (r.stderr or "")
    hit = r.returncode == 1 and "不存在" in out
    print(f"C 主链路文件缺失 -> rc={r.returncode} {'✓ 抓住' if hit else '❌ 未抓住'}")
    if not hit:
        bad += 1
        print(out[-400:])

    assert hashlib.sha256(open(TARGET, "rb").read()).hexdigest() == h0, "还原后字节不一致！"
    print("还原校验：字节一致 ✓")

    # D：往 proactive.py 注入真空吞（验证 10-24 新纳入的主链文件确实有牙齿）
    orig_d = open(TARGET_D, "rb").read()
    h0_d = hashlib.sha256(orig_d).hexdigest()
    anchor_d = "    async def _screen_batch_loop(self):".encode("utf-8")
    injected_d = ("    async def _screen_batch_loop(self):" + NL +
                  "        try:" + NL +
                  "            _ = 1 / 0" + NL +
                  "        except Exception:" + NL +
                  "            pass" + NL).encode("utf-8")
    if orig_d.count(anchor_d) != 1:
        print("D 锚点命中 %d 次（期望 1）❌" % orig_d.count(anchor_d))
        bad += 1
    else:
        open(TARGET_D, "wb").write(orig_d.replace(anchor_d, injected_d))
        rc, out = run_tool()
        hit = "proactive.py" in out and "真静默" in out
        print(f"D proactive.py 注入真空吞 -> rc={rc} {'✓ 抓住' if rc == 1 and hit else '❌ 未抓住'}")
        if rc != 1 or not hit:
            bad += 1
            print(out[-400:])
        open(TARGET_D, "wb").write(orig_d)
        assert hashlib.sha256(open(TARGET_D, "rb").read()).hexdigest() == h0_d, \
            "D 还原后字节不一致！"
        print("D 还原校验：字节一致 ✓")

    print("PASS：四刀全被抓住" if not bad else f"FAIL：{bad} 刀未抓住")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
