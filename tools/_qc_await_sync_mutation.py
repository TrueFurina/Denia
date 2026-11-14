# -*- coding: utf-8 -*-
"""「协程调度」的变异验证：五刀，刀刀必须被抓住。

跑法：.venv/Scripts/python.exe -u tools/_qc_await_sync_mutation.py

这道门守两个方向：
  · `await self.f()` 而 f 是同步 def → `await None` 抛 TypeError，被事件总线吞掉，
    功能静默失效。真实事故（2026-10-18）：`gewechat._on_decision` 里
    `await self._safe_post(...)`，**微信回复从来没真正发出去过**。
  · `self.f()` 而 f 是 async def → 协程对象被丢弃，函数体一行不执行。

它自己带一个自测（`src/tests/test_check_await_sync.py`），七条正反例。
和隔壁 `_qc_async_blocking_mutation` 同一个洞：**七条全部只调 `C.scan()`，
从不碰 `C.main()`** —— 退出码那一行没有任何判据。本轮先给自测补了
「有可疑点 → 1 / 干净 → 0」两条断言，A 刀才有东西可砍。
"""
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

P = "tools/check_await_sync.py"
TEST = "src/tests/test_check_await_sync.py"
# 判定只用 stdlib，所以哪个解释器都能跑：有 .venv 就用它（三方依赖最全），
# 没有（交付包 / CI 的嵌入式运行时）就退回当前解释器。写死 .venv 会让这道门
# 在干净环境里直接崩，看起来像「判据坏了」，其实是「解释器路径不存在」。
PY = ".venv/Scripts/python.exe"
if not os.path.exists(PY):
    PY = sys.executable


def _judged(r) -> bool:
    """判定是「判出了问题」而不是「崩了」。自测的输出约定是 `✗` / `❌`。"""
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode != 0 and ("✗" in out or "❌" in out)


def _run_test():
    return subprocess.run([PY, "-u", TEST], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _baseline_green() -> bool:
    r = _run_test()
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode == 0 and "✗" not in out and "❌" not in out


def B(s: str) -> bytes:
    return s.encode("utf-8")


MUTS = [
    # A 砍的是「发现了会不会说出来」。这类退化在 CI 里是一道永远绿的装饰，
    # 而且它比「检测能力变弱」更隐蔽：可疑点一个不少地打印出来，只是没人看。
    ("A 退出码恒 0（只打印不判红）",
     B("    return 1 if all_hits else 0\n"),
     B("    return 0  # 变异刀：发现了也不判红\n")),

    # B 砍规则 2 的「已知非问题」排除。`create_task` / `asyncio.run` / `gather`
    # 都是在消费协程，把它们从 CONSUMERS 里删掉，这三种正确写法会全部变成
    # 「async 但没 await/调度」—— 误报比漏报更快杀死一道闸门（所有人开始无视它）。
    ("B 消费者表删掉 create_task / run / gather",
     B('    "run", "run_until_complete", "create_task", "ensure_future", "gather",\n'),
     B('    "run_until_complete", "ensure_future",\n')),

    # C 砍规则 1 本身：`await self.同步方法()` 不再报。这条一坏，
    # 「微信回复从来没发出去过」那类事故就重新变成看不见的。
    ("C await 同步方法不再报（规则 1 失效）",
     B('                        and f.value.id == "self" and f.attr in sync_names:\n'),
     B('                        and f.value.id == "self" and False:\n')),

    # D 砍规则 2 本身：`self.异步方法()` 没被调度不再报。
    ("D async 方法没调度不再报（规则 2 失效）",
     B('                        and f.value.id == "self" and f.attr in async_names \\\n'
       '                        and id(node) not in consumed:\n'),
     B('                        and f.value.id == "self" and False \\\n'
       '                        and id(node) not in consumed:\n')),

    # E 砍「异步生成器」的排除。异步生成器 body 里有 yield，调用它本来就该
    # 直接拿生成器去 `async for`；不排除就会把 `async for x in self.gen()`
    # 报成「协程没被调度」—— 又一处误报。
    ("E 不再排除异步生成器",
     B("def _is_async_gen(fn: ast.AST) -> bool:\n"
       "    return any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(fn))\n"),
     B("def _is_async_gen(fn: ast.AST) -> bool:\n"
       "    return False  # 变异刀：异步生成器也当普通协程查\n")),
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
            names = [h[1:].split("：")[0].strip() for h in hits]
            why = ("%d 项：" % len(names)) + " / ".join(names) if names \
                else (lines[-1][:88] if lines else "<无输出>")
            print(name)
            print("   -> " + ("已抓住：" + why[:110] if caught else "没抓住（测试失效）"))
            if not caught:
                bad += 1
        finally:
            open(P, "wb").write(orig)

    ok = hashlib.sha256(open(P, "rb").read()).hexdigest() == h0
    print("\n还原字节一致：%s" % ok)
    if not ok:
        bad += 1
    print("五刀全红" if bad == 0 else "有 %d 处异常" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
