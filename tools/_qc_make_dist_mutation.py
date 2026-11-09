# -*- coding: utf-8 -*-
"""「打包依赖分级」的变异验证：十二刀，刀刀必须被抓住。

跑法：.venv/Scripts/python.exe -u tools/_qc_make_dist_mutation.py

这道门有 1047 行、六十来条断言，是本项目最厚的一道，而且**写得比大多数门都讲究**
（[14] 甚至专门写了「先判 env 是否存在，再判内容：否则 `"PYTHONPATH" not in {}`
会恒真」这种防空洞通过的话）。但按「有没有刀」的读数是「没有刀」。

本轮先按纪律探了三处，三处全是空转（全部实测，改完门仍 EXIT 0 全绿）：

  1. `[C10-6]` 配了凭证但没 signtool → 应当拒绝。原写法在"没抛异常"的分支里
     断言 `not os.path.isfile("signtool.exe")` —— 那是在断言**当前目录的文件系统**，
     不是在断言代码。把 `if not cfg["tool"] or not os.path.isfile(...): raise`
     改成 `if False:`，全门全绿。
  2. `[C10-14]` `--sign` 必须连着 `--installer`。原判据只是 grep 源码里有没有那句
     错误提示。把守卫改成 `if False:`，提示原样保留、门全绿，而 `--sign` 单跑
     已经不再报错。
  3. `install_deps` 末尾 `if verify: return verify_deps(...)`。**整段没有判据**：
     换成 `return True`（装完不验收）全门全绿。这是本文件 [14] 讲的那件事的镜像
     ——「规矩没写进 env=，等于没写」；**「验收没被调用，等于没验收」**。
     [14] 只直接测 verify_deps 本身，从没测过 install_deps 到底调不调它。

三处修法都写在门里（判据先行）：
  · C10-6 改成「显式给一个不存在的 DANIA_SIGN_TOOL」——任何机器上都必抛；
  · C10-14 改成用 AST 找到含那句提示的 if，直接验它的**条件**；
  · 新增 [26]：verify=True 必须调一次、verify=False / defer_verify 不许调、
    验收不过必须让 install_deps 返回 False。

**先有判据，才有刀。** 其余九刀砍的是本来就有的判据，用来证明这道门的牙是真牙。

纪律：二进制读写；锚点按实测 EOL 拼；锚点必须命中且只命中一次；还原后字节核对。
判定用 `❌`（门自己的汇总）而不是 `✗` —— **make_dist 的负向场景也会打印 `✗`**，
按 `✗` 过滤会把「门在验证某个失败场景」误当成「门失败了」。这个坑本轮踩过一次。
"""
from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

P = "tools/make_dist.py"
TEST = "src/tests/test_make_dist_deps.py"
PY = ".venv/Scripts/python.exe"
if not os.path.exists(PY):
    PY = sys.executable


def _judged(r) -> bool:
    """判定是「判出了问题」而不是「崩了」。

    本门输出约定：每条 `✓ / ✗ 名字`，末尾 `❌ N 项未通过：` + 逐条 `- 名字`。
    崩溃（SyntaxError / Traceback）同样非零退出，但到不了汇总行 ——
    `❌` 就是「走到了最后并认出了失败」的标志。
    """
    out = (r.stdout or "") + (r.stderr or "")
    if "Traceback (most recent call last)" in out:
        return False
    return r.returncode != 0 and "❌" in out


def _run_test():
    return subprocess.run([PY, TEST], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _baseline_green() -> bool:
    """起点必须绿：恒红的判定能让每一把刀都变红，那种"全红"什么都不证明。"""
    r = _run_test()
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode == 0 and "✅ 依赖分级安装测试全部通过" in out


def _eol_of(raw: bytes) -> bytes:
    return b"\r\n" if b"\r\n" in raw else b"\n"


def _b(s: str, eol: bytes) -> bytes:
    return s.replace("\n", eol.decode("ascii")).encode("utf-8")


# (名字, 原锚点, 变异后, 期望红在哪条)
MUTS = [
    # ---- 本轮补判据才砍得动的三刀 ----
    ("A 配了凭证但不查 signtool（在解析期就放行）",
     '    if not cfg["tool"] or not os.path.isfile(cfg["tool"]):\n',
     '    if False:  # 变异刀：不查工具\n',
     "DANIA_SIGN_TOOL 指向不存在的文件"),

    ("B --sign 守卫失效（错误提示仍留在源码里）",
     "    if args.sign and not args.installer:\n",
     "    if False:  # 变异刀：守卫失效\n",
     "--sign 的守卫条件真在同时检查"),

    ("C 装完不验收（verify 形同虚设）",
     "    if verify:\n"
     "        return verify_deps(out_dir, python_exe)\n"
     "    return True\n",
     "    return True  # 变异刀：装完不验收\n",
     "verify=True 时调了一次 verify_deps"),

    # ---- 本来就有的判据（一起验，证明牙是真牙）----
    ("D _pth 写 LF 而不是 CRLF",
     '        f.write("\\r\\n".join(lines) + "\\r\\n")\n',
     '        f.write("\\n".join(lines) + "\\n")\n',
     "用 CRLF"),

    ("E pywin32 DLL 落到 DLLs/ 而不是解释器根",
     '    dst = os.path.join(out_dir, "python")\n',
     '    dst = os.path.join(out_dir, "python", "DLLs")\n',
     "落点是 python/ 根"),

    ("F 非 DLL 文件也被搬进解释器目录",
     '        if n.lower().endswith(".dll"):\n',
     '        if True:  # 变异刀：什么都搬\n',
     "只搬 .dll"),

    ("G enable=False 不删旧 _pth（隔离开关变成单向）",
     "        if os.path.exists(path):\n"
     "            os.remove(path)\n",
     "        if False:  # 变异刀：不删\n"
     "            os.remove(path)\n",
     "enable=False 要把旧 _pth 删掉"),

    ("H tier_specs 永不 break（「到此为止」语义失效）",
     "        if t == stop:\n            break\n",
     "        if t == stop:\n            pass  # 变异刀：不 break\n",
     "递进正确"),

    ("I purge 连 python/ 下的字节码也清（做过头）",
     "        if os.path.normcase(os.path.abspath(cur)).startswith(\n"
     "                os.path.normcase(os.path.abspath(pyroot)) + os.sep):\n"
     "            dirs[:] = []\n"
     "            continue\n",
     "        if False:  # 变异刀：python/ 也一起清\n"
     "            dirs[:] = []\n"
     "            continue\n",
     "python/ 下的字节码不动"),

    ("J 指纹不含壳脚本（改了 main.js 也能出旧包）",
     '    for rel in ("shell/main.js", "shell/preload.js"):\n',
     "    for rel in ():  # 变异刀：壳脚本不进指纹\n",
     "壳脚本改动进指纹"),

    ("K normalize_tier 未知档位静默降级（参数名骗人）",
     "    raise ValueError(\n"
     "        f\"未知依赖档位 {upto!r}（可选：{', '.join(_TIER_ORDER)}；\"\n"
     "        f\"all 是 {_TIER_ORDER[-1]} 的别名）\")\n",
     '    return "important"  # 变异刀：静默降级\n',
     "应当抛 ValueError"),

    ("L copy_shell 不裁 locales（白装 ~46MB 语言包）",
     "            filenames = [f for f in filenames if f in SHELL_LOCALE_KEEP]\n",
     "            pass  # 变异刀：不裁\n",
     "无关语言包已裁"),
]


def _short(out: str) -> str:
    names = []
    if "项未通过" in out:
        for ln in out.split("项未通过", 1)[1].splitlines():
            s = ln.strip()
            if s.startswith("- "):
                names.append(s[2:][:30])
    if not names:
        return "<无失败清单>"
    return "%d 项：%s" % (len(names), " / ".join(names))


def main() -> int:
    if not _baseline_green():
        print("FAIL 起点就不是绿的 —— 恒红的判定能让每把刀都变红，这种\"全红\"不证明任何事")
        return 1
    print("  PASS 起点绿（%s）" % TEST)

    orig = io.open(P, "rb").read()
    h0 = hashlib.sha256(orig).hexdigest()
    eol = _eol_of(orig)

    bad = 0
    for name, old_s, new_s, expect in MUTS:
        old = _b(old_s, eol)
        new = _b(new_s, eol)
        n = orig.count(old)
        if n != 1:
            print("[跳过] %s：锚点命中 %d 次（应恰好 1 次），锚点已腐烂" % (name, n))
            bad += 1
            continue
        io.open(P, "wb").write(orig.replace(old, new, 1))
        try:
            r = _run_test()
            out = (r.stdout or "") + (r.stderr or "")
            caught = _judged(r)
            if caught and expect not in out:
                print("%s\n   -> 红了但没红在预期那条（期望含 %r）：%s"
                      % (name, expect, _short(out)))
                bad += 1
                continue
            print("%s\n   -> %s" % (name, "已抓住：" + _short(out) if caught
                                    else "没抓住（测试失效）"))
            if not caught:
                bad += 1
        finally:
            io.open(P, "wb").write(orig)

    ok = hashlib.sha256(io.open(P, "rb").read()).hexdigest() == h0
    print("\n还原字节一致：%s" % ok)
    if not ok:
        bad += 1
    print("十二刀全红" if bad == 0 else "有 %d 处异常" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
