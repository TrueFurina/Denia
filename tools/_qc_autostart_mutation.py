# -*- coding: utf-8 -*-
"""「开机自启」闸门的变异验证：六刀，刀刀必须被抓住。

为什么补这把刀：`src/tests/test_autostart.py` 守的是**用户登录时到底跑什么**。
它有三处别处没有的讲究，全是踩过坑换来的：
  · 注册表写的是真 HKCU\\...\\Run，只是值名用 `_DaniaAutostartTest`，finally 必清；
  · 2026-10-18 实锤过一次：某个变异让 enable 写死 VALUE_NAME、disable 删的却是
    TEST_VALUE → 真实 Run 键泄漏一条指向已删 tempfile 的死命令，用户登录时弹
    「无法运行」。所以它自己带快照 + 污染守卫；
  · command 断言只查结构（pythonw / main.py / 引号），不 hardcode 全路径。
这道门 18 条断言从没配过刀。

六刀各打一条判据：
  A1 command_for 不加引号              → 「带空格路径须有引号包裹」红
  A2 用 python.exe 而不是 pythonw      → 「用 pythonw（无控制台）」红
  A3 is_enabled 遇 OSError 返回 True   → 「不抛不误报」红
  A4 sync(False) 也去 enable           → 「sync(False) → 关」红
  A5 read_command 异常时返回 "" 而非 None → 「read_command 同路径 → None」红
  A6 disable 删错值名                  → 「disable → False」红

【A6 差点写不出来，而它反过来暴露了判据自己的缺陷】
最初不敢写 A6：刀的 finally 只还原源码字节，删不掉已经被错误逻辑漏掉的注册表项，
而测试自己的 finally 调的正是**被变异的 disable** —— 于是每跑一次刀，本机
HKCU\\Run 里就多一条指向已删临时目录的死命令，用户登录时弹「无法运行」。
2026-10-18 那次泄漏就是这么发生的。

想清楚之后发现：这不是「刀不能写」，是**判据有缺陷** —— 清理路径依赖了被测代码。
`disable` 一旦真回归，`test_autostart` 自己每跑一次也会留一条残留。所以先改判据：
`finally` 换成 `_raw_delete()`（直接用 winreg，与 `autostart` 实现解耦），
A6 才变得安全。**先修判据，再加刀** —— 与 M5 那次「先有判据才有刀」是同一个顺序。

判据三件套（沿用本仓 2026-11-08 的修法）：
  · `_judged()` —— 非零退出**且**输出里有 `  ✗ ` 行，崩溃（Traceback）不算抓住；
  · `_baseline_green()` —— 起点必须先绿，恒红的判定能让每把刀都变红；
  · 每刀都备份字节、finally 还原并核对 sha256，刀自己不许把源码弄坏。

跑法：.venv/Scripts/python.exe -u tools/_qc_autostart_mutation.py
退出码：0 = 六刀全红；1 = 有刀没被抓住；77 = 本机跑不了（缺依赖等）。
"""
from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

PY = sys.executable
GATE = os.path.join("src", "tests", "test_autostart.py")
MOD = os.path.join("src", "autostart.py")

# (标签, 目标文件, 原文锚点, 替换文本, 期望出现的失败关键词)
MUTS = [
    ("A1 command_for 不加引号",
     MOD,
     """    return f'"{_pythonw_in(root)}" "{os.path.join(root, "main.py")}"'""",
     """    return f'{_pythonw_in(root)} {os.path.join(root, "main.py")}'  # 变异刀：不加引号""",
     "带空格路径须有引号包裹"),
    ("A2 用 python.exe 而不是 pythonw",
     MOD,
     '    return os.path.join(root, "python", "pythonw.exe")',
     '    return os.path.join(root, "python", "python.exe")  # 变异刀：会弹控制台',
     "用 pythonw（无控制台）"),
    ("A3 is_enabled 遇 OSError 返回 True",
     MOD,
     '        logger.debug("读自启状态失败: %s", e)',
     '        logger.debug("读自启状态失败: %s", e)\n'
     "        return True  # 变异刀：读不到就当开着",
     "不抛不误报"),
    ("A4 sync(False) 也去 enable",
     MOD,
     "    (enable if on else disable)(root, value_name=value_name)",
     "    enable(root, value_name=value_name)  # 变异刀：关也走开",
     "sync(False) → 关"),
    ("A5 read_command 异常时返回空串",
     MOD,
     '        logger.debug("读自启命令失败: %s", e)',
     '        return ""  # 变异刀：异常时返回空串而不是 None',
     "read_command 同路径"),
    ("A6 disable 删错值名",
     MOD,
     "            winreg.DeleteValue(k, value_name)",
     "            winreg.DeleteValue(k, VALUE_NAME)  # 变异刀：删错值名",
     "disable → False"),
]

# 给 `check_knife_noop.py` 认形状用：第 2 项是 old、第 3 项是 new（下标从 0 起）。
NOOP_SHAPE = ("idx", 2, 3)


def _read(p: str) -> bytes:
    with io.open(p, "rb") as f:
        return f.read()


def _write(p: str, data: bytes) -> None:
    with io.open(p, "wb") as f:
        f.write(data)


def _run() -> tuple:
    p = subprocess.run([PY, "-u", GATE], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _judged(rc: int, out: str) -> bool:
    """「判出了问题」而不是「崩了」：非零退出 **且** 有 `  ✗ ` 行。"""
    return rc != 0 and "  ✗ " in out


def _baseline_green() -> tuple:
    rc, out = _run()
    if rc == 77:
        return True, "77"
    return (rc == 0 and "✗" not in out), ""


def main() -> int:
    ok_green, why77 = _baseline_green()
    if why77:
        print("  [SKIP] 起点 exit 77：本机缺依赖，一道判据都没跑（不是通过）")
        return 77
    if not ok_green:
        print("  FAIL 起点就不是绿的 —— 恒红的判定能让每把刀都变红，"
              "这种\"全红\"不证明任何事")
        return 1
    print("  PASS 起点绿")

    bad = 0
    for label, target, old, new, keyword in MUTS:
        orig = _read(target)
        sha0 = hashlib.sha256(orig).hexdigest()
        text = orig.decode("utf-8")
        n = text.count(old)
        if n != 1:
            print("  FAIL %s：锚点命中 %d 次（应恰好 1 次），锚点已腐烂" % (label, n))
            bad += 1
            continue
        try:
            _write(target, text.replace(old, new).encode("utf-8"))
            rc, out = _run()
            caught = _judged(rc, out)
            hit = keyword in out
            if caught and hit:
                print("  PASS %s → 红了，且点名「%s」" % (label, keyword))
            elif caught:
                print("  FAIL %s → 红了，但没点名「%s」（可能是别的原因崩的）"
                      % (label, keyword))
                for ln in [x for x in out.splitlines() if "  ✗ " in x][:3]:
                    print("        | " + ln.strip()[:150])
                bad += 1
            else:
                print("  FAIL %s → 没抓住（闸门失效）" % label)
                bad += 1
        finally:
            _write(target, orig)
            if hashlib.sha256(_read(target)).hexdigest() != sha0:
                print("  FAIL %s：还原后字节不一致！" % label)
                bad += 1

    rc, out = _run()
    if rc == 0 and "✗" not in out:
        print("  PASS 还原后仍然绿")
    else:
        print("  FAIL 还原后不是绿的 —— 刀自己把源码弄坏了")
        bad += 1

    print("\n%s" % ("六刀全红" if bad == 0 else "有 %d 处异常" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
