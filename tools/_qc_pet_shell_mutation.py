# -*- coding: utf-8 -*-
"""「壳自愈与退出清理」的变异验证：八刀，刀刀必须被抓住。

跑法：.venv/Scripts/python.exe -u tools/_qc_pet_shell_mutation.py

这道门有 15 节、四十来条断言，看着是本项目最扎实的一道。本轮先读判据，
**五处关键行为全都没有判据**（五处都实测过，删掉后自测照样全绿 exit 0）：

  1. `_try_restart` 里的 `if was_top: self.on_top = True` —— 正常重启路径的
     「恢复置顶」。原来 [1] 里有个 `calls = {"launch": 0, "show": 0, "top": 0}`，
     `top` 这个计数器**从头到尾没被 ++ 过**，也没有任何断言读它。想测的东西
     忘了写，但字典留了下来，看起来像测过了。
  2. `_cleanup_proc` 里的 `elif force: _kill_tree(proc.pid)` —— 主进程已退、
     子进程可能还在。这正是「孤儿 Electron 窗口滞留桌面 + 下次启动撞单实例限制」
     那个坑（Electron 会**无输出地立刻退出**，项目里最难查的一类故障）。
  3. 活进程 `wait(5)` 超时后的升级强杀 —— 删掉只剩 terminate，渲染进程留下。
  4. `if len(self._restarts) > 1: time.sleep(RESTART_BACKOFF)` —— 连续崩时的退避，
     作用是「别把 CPU 打满」。
  5. `close()` 里的 `self._sock.sendall(b"quit\\n")` —— 先礼后兵的第一步。

**为什么 15 节还是漏**：节数多不等于覆盖面广。前五条全在「进程回收」和
「重启时恢复状态」这两条支路上，而 15 节里有 9 节在测「配额 / 降级 / 握手文件」。
**分支比断言更值得数。**

所以本轮先补判据（[1] 加置顶、[1b] 加不置顶、[6] 加三条杀进程树分支、
[11] 加 quit、[16] 加退避），八刀才有东西可砍。**先有判据，才有刀。**
"""
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

P = "src/ui/pet_shell.py"
TEST = "src/tests/test_pet_shell_selfheal.py"
# 这道门不 spawn 真 Electron（_launch 全换成假的），但要用 src 里的模块，
# 有 .venv 就用它。
PY = ".venv/Scripts/python.exe"
if not os.path.exists(PY):
    PY = sys.executable


def _judged(r) -> bool:
    """判定是「判出了问题」而不是「崩了」。

    本门输出约定是每条 `✗ 名字`、末尾 `❌ FAIL：N 项`。
    崩溃（SyntaxError / ImportError / Traceback）同样非零退出，但不会打印
    `✗` / `❌` —— 那正是「崩了」与「认出来了」的分界。
    """
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode != 0 and ("✗" in out or "❌" in out)


def _run_test():
    return subprocess.run([PY, "-u", TEST], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _baseline_green() -> bool:
    """起点必须绿：恒红的判定能让每一把刀都变红，那种"全红"什么都不证明。"""
    r = _run_test()
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode == 0 and "✗" not in out and "❌" not in out


def B(s: str) -> bytes:
    return s.encode("utf-8")


MUTS = [
    # ---- 本轮新补判据才砍得动的五刀 ----
    ("A 重启后不恢复置顶（was_top 分支）",
     B("        if was_top:\n            self.on_top = True\n"),
     B("        if False:  # 变异刀：不恢复置顶\n            self.on_top = True\n")),

    ("B 主进程已退时不再杀子进程树（孤儿窗口）",
     B("            elif force:\n"
       "                _kill_tree(proc.pid)   # 主进程已退，但子进程可能还在\n"),
     B("            elif False:  # 变异刀：不管残留子进程\n"
       "                _kill_tree(proc.pid)   # 主进程已退，但子进程可能还在\n")),

    ("C 活进程 wait 超时后不再升级强杀",
     B("                    _kill_tree(proc.pid)\n"),
     B("                    pass  # 变异刀：不升级强杀\n")),

    ("D 连续崩不再退避（CPU 打满）",
     B("            time.sleep(RESTART_BACKOFF)   # 连续崩时退一步，别把 CPU 打满\n"),
     B("            pass  # 变异刀：不退避\n")),

    ("E close() 不再先发 quit（先礼后兵变直接强杀）",
     B('            self._sock.sendall(b"quit\\n")'
       "   # 先礼后兵：给壳一个干净退出的机会\n"),
     B("            pass  # 变异刀：不发 quit\n")),

    # ---- 老判据本来就守着的三刀（一起验，证明八刀不是只有新的那五刀在干活）----
    ("F 配额判断差一（>= 改成 >，多放一次）",
     B("        if len(self._restarts) >= RESTART_MAX:\n"),
     B("        if len(self._restarts) > RESTART_MAX:  # 变异刀：多放一次\n")),

    # G 的第一版只砍 `self._closed or` 那一半，结果**没红**：`close()` 会顺手
    # `_stop_watchdog()`，`_wd_stop` 也是置位的，两个条件互为备份。
    # 也就是说 `_closed` 这一半是**冗余**的（留着无害，防将来有人单独置位），
    # 单独砍它测不出来。所以这里砍整条守卫 —— 那才是「已关闭的壳不会被复活」
    # 这个判断的真身。
    ("G 拿掉「已关闭就别再重启」整条守卫",
     B("        if self._closed or self._wd_stop.is_set():\n            return False\n"),
     B("        if False:  # 变异刀：拿掉整条守卫\n            return False\n")),

    ("H 时间窗口不再滚动（一次历史崩溃永久剥夺自愈）",
     B("        self._restarts = [t for t in self._restarts"
       " if now - t < RESTART_WINDOW]\n"),
     B("        self._restarts = list(self._restarts)  # 变异刀：窗口不滚\n")),
]


def main() -> int:
    orig = open(P, "rb").read()
    h0 = hashlib.sha256(orig).hexdigest()
    bad = 0
    if not _baseline_green():
        print("FAIL 起点就不是绿的 —— 恒红的判定能让每把刀都变红，这种\"全红\"不证明任何事")
        return 1
    print("  PASS 起点绿")
    for name, old, new in MUTS:
        n = orig.count(old)
        if n != 1:
            print("[跳过] %s：锚点命中 %d 次（应恰好 1 次），锚点已腐烂" % (name, n))
            bad += 1
            continue
        open(P, "wb").write(orig.replace(old, new, 1))
        try:
            r = _run_test()
            caught = _judged(r)
            lines = [l.strip() for l in (r.stdout or "").splitlines() if l.strip()]
            hits = [l for l in lines if l.startswith("✗")]
            # 一把刀可能同时打中多条判据（B 会连坐三条）。只报第一条会让人以为
            # 它只打中一条，所以把命中的名字全列出来。
            names = [h[1:].split("（")[0].strip() for h in hits]
            why = ("%d 项：" % len(names)) + " / ".join(names) if names \
                else (lines[-1][:88] if lines else "<无输出>")
            why = why[:110]
            print(name)
            print("   -> " + ("已抓住：" + why if caught else "没抓住（测试失效）"))
            if not caught:
                bad += 1
        finally:
            open(P, "wb").write(orig)

    ok = hashlib.sha256(open(P, "rb").read()).hexdigest() == h0
    print("\n还原字节一致：%s" % ok)
    if not ok:
        bad += 1
    print("八刀全红" if bad == 0 else "有 %d 处异常" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
