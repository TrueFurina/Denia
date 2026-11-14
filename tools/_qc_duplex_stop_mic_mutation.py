# -*- coding: utf-8 -*-
"""「双工停止与麦克风」的变异验证：十刀，刀刀必须被抓住。

跑法：.venv/Scripts/python.exe -u tools/_qc_duplex_stop_mic_mutation.py

这道门以前的问题不是"没有刀"，是**它测的是自己抄的一份副本**：
A/B/C 三段的 `_dispatch()` / `track()` / `resolve_engine_ui()` 注释里明写
「复刻 …的分派核心 / 电平滑化 / engine_ui 判定」，把生产实现手抄进测试，
然后测抄件。生产代码怎么改，这三段都不会红。
讽刺的是同一个文件 D 段的注释早就写对了：「不复制逻辑，避免"假绿"——
复刻逻辑两次写得一样不算测试」。

本轮先把 A/B/C 改成调真实实现（`ProactiveStrategy._stop_duplex_client` /
`CloudOmniRealtime._track_mic_level` / `CharacterAPI.get_role_voice_context`），
**然后**才有东西可砍 —— 先有判据，才有刀。

换真实现时又挖出两件事，两件都是"注释在骗人"：

  ① A1 的括号写着「旧代码 await None → TypeError → stopped 恒 False」。
     假的。`await f()` 必然先求值 `f()`，所以 stop() 真的执行了、客户端真的停了。
     实测把生产代码回退成 BUG-1 原形，**A1/A2 照样绿**。真正会红的只有 A5
     （那条 exception 日志）。生产代码里同一条假因果（「旧客户端从未停止」）
     本轮一并改正。
  ② `_watchdog_tick` 里的 `and self._mic_proc is proc`（换轮保护）**一条判据
     都没有**：D5/D6 都是 `_mic_proc is proc` 的情形，D7 把判据桩成 False 又不
     经过它。删掉这行守卫，全门照样绿。所以本轮补了 D9。

十刀分布：proactive.py 1 刀、cloud_omni_realtime.py 8 刀、webapp.py 1 刀。

纪律：二进制读写（文本模式在 Windows 会把 \\n 写成 \\r\\n → 全文件 CRLF →
锚点全失效）；锚点按实测 EOL 拼；锚点必须命中且只命中一次；还原后字节逐字节核对。
"""
from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

P_PROACTIVE = "src/companion/proactive.py"
P_OMNI = "src/llm/cloud_omni_realtime.py"
P_WEBAPP = "src/ui/webapp.py"
TEST = "tools/test_duplex_stop_and_mic.py"

PY = ".venv/Scripts/python.exe"
if not os.path.exists(PY):
    PY = sys.executable


def _judged(r) -> bool:
    """判定是「判出了问题」而不是「崩了」。

    本门输出约定是每条 `[FAIL] 名字`、末尾 `汇总：PASS=n  FAIL=m`，退出码 1。
    崩溃（SyntaxError / ImportError / Traceback）同样非零退出，但不会打印
    `[FAIL]` —— 那正是「崩了」与「认出来了」的分界。
    """
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode != 0 and "[FAIL]" in out


def _run_test():
    return subprocess.run([PY, "-u", TEST], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _baseline_green() -> bool:
    """起点必须绿：恒红的判定能让每一把刀都变红，那种"全红"什么都不证明。"""
    r = _run_test()
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode == 0 and "[FAIL]" not in out


def _eol_of(raw: bytes) -> bytes:
    return b"\r\n" if b"\r\n" in raw else b"\n"


def _b(s: str, eol: bytes) -> bytes:
    """把用 \\n 写成的锚点换成文件实际 EOL 的字节串。"""
    return s.replace("\n", eol.decode("ascii")).encode("utf-8")


# (名字, 文件, 原锚点, 变异后, 期望红在哪条)
MUTS = [
    # ---------------- proactive.py：BUG-1 原形 ----------------
    ("A 分派回退成 await client.stop()（BUG-1 原形）", P_PROACTIVE,
     "            ret = client.stop()\n"
     "            if inspect.isawaitable(ret):\n"
     "                await ret\n",
     "            await client.stop()  # 变异刀：回退成原 bug\n",
     "A5"),

    # ---------------- cloud_omni_realtime.py：麦克风电平 ----------------
    ("B _mic_disp 退化成被稀释的 _mic_level", P_OMNI,
     "            if rms > self._mic_disp:\n"
     "                self._mic_disp = rms\n"
     "            else:\n"
     "                # 回落快一些（约 0.35s 降到一半），保证句间能及时归零、不拖影\n"
     "                self._mic_disp = self._mic_disp * 0.82 + rms * 0.18\n",
     "            self._mic_disp = self._mic_level  # 变异刀：显示值也用稀释均值\n",
     "B2"),

    # ---------------- cloud_omni_realtime.py：停滞判据 ----------------
    ("C 停滞判据超时比较反向（> 改成 <）", P_OMNI,
     "> self.MIC_STALL_TIMEOUT_S",
     "< self.MIC_STALL_TIMEOUT_S  # 变异刀：比较反向",
     "D1"),

    ("D 停滞判据不再看 running（已停止的会话被误杀）", P_OMNI,
     "        return bool(self.running) and self._mic_proc is not None and \\\n",
     "        return self._mic_proc is not None and \\\n",
     "D3"),

    ("E 停滞判据不再看 _mic_proc（未启动也判停滞）", P_OMNI,
     "        return bool(self.running) and self._mic_proc is not None and \\\n",
     "        return bool(self.running) and \\\n",
     "D4"),

    # ---------------- cloud_omni_realtime.py：看门狗 ----------------
    ("F 看门狗不再做换轮保护（旧线程杀新一轮子进程）", P_OMNI,
     "        if self._capture_stalled(time.time()) and self._mic_proc is proc:\n",
     "        if self._capture_stalled(time.time()):  # 变异刀：去掉换轮保护\n",
     "D9"),

    ("G 看门狗检测到停滞也不杀进程", P_OMNI,
     "                           self.MIC_STALL_TIMEOUT_S)\n"
     "            self._kill_capture_proc(proc)\n",
     "                           self.MIC_STALL_TIMEOUT_S)\n"
     "            pass  # 变异刀：不杀\n",
     "D5"),

    ("H 杀完不清 _mic_proc 引用（重复 kill / 误杀下一轮）", P_OMNI,
     "        if self._mic_proc is target:\n"
     "            self._mic_proc = None\n",
     "        if False:  # 变异刀：不清引用\n"
     "            self._mic_proc = None\n",
     "D6"),

    ("I 只清引用不真 kill 子进程", P_OMNI,
     "                target.kill()\n",
     "                pass  # 变异刀：不 kill\n",
     "D5"),

    # ---------------- webapp.py：BUG-3 原形 ----------------
    ("J 出声方式只读静态 config（BUG-3 原形）", P_WEBAPP,
     "            if _rt_dm is not None:\n"
     "                dm = _rt_dm\n",
     "            if False:  # 变异刀：只读静态配置\n"
     "                dm = _rt_dm\n",
     "C1"),
]


def _short(out: str) -> str:
    lines = [l.strip() for l in out.splitlines() if "[FAIL]" in l]
    if not lines:
        return "<无 [FAIL] 行>"
    names = []
    for l in lines:
        body = l.split("[FAIL]", 1)[-1].strip()
        names.append(body.split("（")[0].split("(")[0].strip()[:28])
    return "%d 项：%s" % (len(names), " / ".join(names))


def main() -> int:
    if not _baseline_green():
        print("FAIL 起点就不是绿的 —— 恒红的判定能让每把刀都变红，这种\"全红\"不证明任何事")
        return 1
    print("  PASS 起点绿（%s）" % TEST)

    originals: dict[str, bytes] = {}
    for path in (P_PROACTIVE, P_OMNI, P_WEBAPP):
        originals[path] = io.open(path, "rb").read()

    bad = 0
    for name, path, old_s, new_s, expect in MUTS:
        orig = originals[path]
        eol = _eol_of(orig)
        old = _b(old_s, eol)
        new = _b(new_s, eol)
        n = orig.count(old)
        if n != 1:
            print("[跳过] %s：锚点命中 %d 次（应恰好 1 次），锚点已腐烂" % (name, n))
            bad += 1
            continue
        io.open(path, "wb").write(orig.replace(old, new, 1))
        try:
            r = _run_test()
            out = (r.stdout or "") + (r.stderr or "")
            caught = _judged(r)
            if caught and expect not in out:
                # 红了但不是红在预期那条：说明这把刀砍歪了，或判据改名了。
                print("%s\n   -> 红了但没红在 %s（%s）" % (name, expect, _short(out)))
                bad += 1
                continue
            print("%s\n   -> %s" % (name, "已抓住：" + _short(out) if caught
                                    else "没抓住（测试失效）"))
            if not caught:
                bad += 1
        finally:
            io.open(path, "wb").write(orig)

    ok = all(hashlib.sha256(io.open(p, "rb").read()).hexdigest()
             == hashlib.sha256(originals[p]).hexdigest()
             for p in originals)
    print("\n还原字节一致：%s" % ok)
    if not ok:
        bad += 1
    print("十刀全红" if bad == 0 else "有 %d 处异常" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
