# -*- coding: utf-8 -*-
"""「异步阻塞」的变异验证：五刀，刀刀必须被抓住。

跑法：.venv/Scripts/python.exe -u tools/_qc_async_blocking_mutation.py

这道门守的是「事件循环被同步调用占住」——症状是"卡一下"，不报错、不告警，
所以只能静态查。2026-10-18 一天内撞到四处（gewechat 回发 / qzone 发布 /
tts 的 time.sleep(0.6) / heavy_apps 的 urlopen(timeout=5)）。

它自己带一个自测（`src/tests/test_check_async_blocking.py`），七条正反例。
但**七条全部只调 `C.scan()`，从不碰 `C.main()`** —— 于是：

    A 刀（`return 1 if all_hits else 0` → `return 0`）在 2026-11-09 之前
    **完全没有判据**：闸门退化成「只打印不判红」，可疑点照样打印，全量里永远绿，
    而自测照样打印「全部通过 ✅」并 exit 0。

这不是假设，是实测过的（本轮先跑实验才动刀）。讽刺的是自测文件第 3 行自己写着
「闸门如果只会打印不会判红，比没有更危险（本项目吃过"文本编码闸门永远 exit 0"的亏）」
—— 那句话守住了 `scan()`，却没守住 `main()`。

所以本轮先给自测补了 `main()` 退出码的两条断言（有可疑点 → 1 / 干净 → 0），
A 刀才有东西可砍。**先有判据，才有刀。**
"""
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

P = "tools/check_async_blocking.py"
TEST = "src/tests/test_check_async_blocking.py"
# 判定只用 stdlib，所以哪个解释器都能跑：有 .venv 就用它（三方依赖最全），
# 没有（交付包 / CI 的嵌入式运行时）就退回当前解释器。写死 .venv 会让这道门
# 在干净环境里直接崩，看起来像「判据坏了」，其实是「解释器路径不存在」。
PY = ".venv/Scripts/python.exe"
if not os.path.exists(PY):
    PY = sys.executable


def _judged(r) -> bool:
    """判定是「判出了问题」而不是「崩了」。

    自测的输出约定是 `✗ 名字: 期望 N 处，实际 M 处` 与末尾的 `❌ N 项未通过`，
    不是本仓其它刀惯用的 `FAIL`。所以这里认 `✗` / `❌`。
    崩溃（SyntaxError / ImportError / Traceback）同样非零退出，但**不会**打印这两个
    符号 —— 那正是「崩了」与「认出来了」的分界。
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
    # A 是本轮的主角：它砍的不是检测能力，是「检测到了会不会说出来」。
    # 这一类退化在所有静态闸门里长得一模一样，而且它比「检测能力变弱」更隐蔽 ——
    # 检测能力变弱，可疑点会变少，至少还能从数量上看出来；退出码变 0，
    # 可疑点**一个不少地打印出来**，只是没人看，CI 也永远是绿的。
    ("A 退出码恒 0（只打印不判红）",
     B("    return 1 if all_hits else 0\n"),
     B("    return 0  # 变异刀：发现了也不判红\n")),

    # B 砍的是本轮修掉的那个缺陷的形状：给 `to_thread` / `run_in_executor` 的实参
    # 做「豁免」。实参是在**事件循环里先求值**的，`to_thread(urlopen(...))` 里的
    # `urlopen` 已经把循环卡住了 —— 豁免它等于把真阻塞标成安全。
    # 这条判据是本轮新加的（`to_thread 实参里的同步调用（真阻塞，必须报）`），
    # 修之前那道门对这种写法报 0 处。
    ("B 恢复 to_thread 实参豁免（把真阻塞标成 safe）",
     B("        nodes = list(_walk_own(fn))\n"
       "        for n in nodes:\n"
       "            if not isinstance(n, ast.Call):\n"
       "                continue\n"),
     B("        nodes = list(_walk_own(fn))\n"
       "        safe = {id(c) for x in nodes if isinstance(x, ast.Call)\n"
       "                and (_call_name(x) or (\"\", \"\"))[1] in (\"to_thread\", \"run_in_executor\")\n"
       "                for a in x.args for c in ast.walk(a) if isinstance(c, ast.Call)}\n"
       "        for n in nodes:\n"
       "            if not isinstance(n, ast.Call) or id(n) in safe:\n"
       "                continue\n")),

    # C 砍的是「不下钻嵌套 def」。嵌套同步函数通常是交给 to_thread 的 worker，
    # 在那里写阻塞代码是**对的**；下钻进去就会把正确写法报成可疑点，
    # 于是所有人开始无视这道门 —— 误报比漏报更能杀死一道闸门。
    ("C 下钻嵌套函数（把 to_thread 的 worker 也查）",
     B("        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,"
       " ast.Lambda, ast.ClassDef)):\n            continue\n"),
     B("        if False:  # 变异刀：嵌套上下文也一起扫\n            continue\n")),

    ("D 阻塞表里删掉 time.sleep",
     B('    ("time", "sleep"), ("urllib", "urlopen"), ("subprocess", "run"),\n'),
     B('    ("urllib", "urlopen"), ("subprocess", "run"),\n')),

    # E 是反方向：把同步函数也算进来。判据里「同步函数里 sleep（正常）」那条
    # 期望 0 处，只有它能抓住这个方向 —— 否则这道门会开始要求所有同步代码
    # 也不许 sleep，那是荒谬的。
    ("E 同步函数也一起扫（把正常代码报成可疑）",
     B("    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)]:\n"),
     B("    for fn in [n for n in ast.walk(tree)\n"
       "               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:\n")),
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
            # 一把刀可能同时打中多条判据（比如「同步函数也一起扫」会连坐
            # 「嵌套 worker」那条）。只报第一条会让人以为它只打中一条，
            # 所以把命中的名字全列出来，条数写在前面。
            names = [h[1:].split("：")[0].strip() for h in hits]
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
    print("五刀全红" if bad == 0 else "有 %d 处异常" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
