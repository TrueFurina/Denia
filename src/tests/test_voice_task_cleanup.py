# -*- coding: utf-8 -*-
"""语音试听任务表「只进不出」的接线回归（2026-11-08）。

修的是什么
----------
`webapp._voice_tasks` 是「异步任务 + 前端轮询」的共享任务表，两条路往里写：
  · `test_voice`          → 线程 `_run_voice_test`（本地/克隆引擎试听）
  · `preview_cloud_voice` → 线程 `_run_cloud_preview`（云端双工试听）

全文件只有**一处** pop，在 `_run_voice_test` 的 finally 末尾那个内联 `_cleanup` 里。
于是：
  [1] 云端试听这条路**根本没有清理** —— 每点一次「试听」就往表里留一条，进程活多久留多久；
  [2] 本地试听那条虽然写了清理，但 finally 开头是
          if backend is None:
              return
      —— return 退出的是整个函数，「没配引擎就点试听」这条早退路径每次都跳过 _cleanup。

两处都不报错、不占 CPU、日志无痕，只能靠「读 pop 的调用点」发现。

本测试守什么（全部 AST，不 import webapp —— 它依赖 webview/Win32，CI 上 import 不了）
  [0] 反空转：写 _voice_tasks 的启动方法必须正好是 {preview_cloud_voice, test_voice}。
      少一个/多一个都红——新增第三条试听路时必须有人来这看一眼，否则下面几条会
      因为「扫不到东西」而全部空转通过。
  [1] 接线：每个启动方法里 `threading.Thread(target=self.X)` 的 X，函数体里必须有清理
      （调 `_schedule_voice_task_cleanup` 或直接 `self._voice_tasks.pop`）。
  [2] finally 里不许跳出：`_run_voice_test` 的 finally 里出现 return/break/continue 就红。
  [3] 清理助手不是空壳：`_schedule_voice_task_cleanup` 真的 pop(task_id) 且线程是 daemon。
  [4] 清理之后前端不炸：`get_voice_test_status` 对缺失的 task 仍返回 phase="gone"。

【2026-11-08 为什么不用行号】行号会漂，闸门会静默失效（check_web_js_syntax 就这么烂过）。
这里全部按「函数名 + AST 形状」定位。
"""
import ast
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 变异刀把它指到临时副本上，从不改仓库里的文件
SRC = os.environ.get("QC_WEBAPP_PATH") or os.path.join(ROOT, "src", "ui", "webapp.py")

FAIL = 0


def check(cond, msg):
    global FAIL
    if cond:
        print("  ✓", msg)
    else:
        FAIL += 1
        print("  ✗ FAIL:", msg)


def _methods(tree):
    """函数名 → FunctionDef（同名取最后一处，与运行时绑定一致）。"""
    out = {}
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[n.name] = n
    return out


def _writes_task_table(fn):
    """函数体里是否出现 self._voice_tasks[...] = ..."""
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Attribute):
                    if t.value.attr == "_voice_tasks":
                        return True
    return False


def _has_cleanup(fn):
    """函数体里是否有清理：调 _schedule_voice_task_cleanup，或直接 self._voice_tasks.pop(...)"""
    for n in ast.walk(fn):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if isinstance(f, ast.Attribute) and f.attr == "_schedule_voice_task_cleanup":
            return True
        if isinstance(f, ast.Attribute) and f.attr == "pop":
            if isinstance(f.value, ast.Attribute) and f.value.attr == "_voice_tasks":
                return True
    return False


def _thread_targets(fn):
    """函数体里 threading.Thread(target=self.X, ...) 的 X。"""
    out = []
    for n in ast.walk(fn):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if not (isinstance(f, ast.Attribute) and f.attr == "Thread"):
            continue
        for kw in n.keywords:
            if kw.arg == "target":
                v = kw.value
                if isinstance(v, ast.Attribute):
                    out.append(v.attr)
    return out


def _finally_jumps(fn):
    """函数内每个 try 的 finally 里是否有 return/break/continue（不跨嵌套作用域）。"""
    hits = []

    def walk(stmts, in_loop):
        for s in stmts:
            if isinstance(s, ast.Return):
                hits.append((s.lineno, "return"))
                continue
            if isinstance(s, (ast.Break, ast.Continue)):
                if not in_loop:
                    hits.append((s.lineno, "break" if isinstance(s, ast.Break) else "continue"))
                continue
            if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            inner = in_loop or isinstance(s, (ast.For, ast.AsyncFor, ast.While))
            for field in ("body", "orelse", "finalbody"):
                sub = getattr(s, field, None)
                if isinstance(sub, list):
                    walk(sub, inner)
            for h in getattr(s, "handlers", None) or []:
                walk(h.body, in_loop)

    for n in ast.walk(fn):
        if isinstance(n, ast.Try) or type(n).__name__ == "TryStar":
            walk(n.finalbody, False)
    return hits


def load():
    with io.open(SRC, "r", encoding="utf-8") as f:
        return ast.parse(f.read(), filename=SRC)


_TREE = None


def tree():
    """解析一次缓存起来（webapp.py 一万七千行，五条判据各解析一遍纯属浪费）。

    注意：下面五个判据都写成**无参**函数。带 `tree` 参数会被 pytest 当成要一个
    叫 tree 的 fixture，收集阶段就报错——而这个目录里 pytest 是会进来收的。
    """
    global _TREE
    if _TREE is None:
        _TREE = load()
    return _TREE


def test_task_table_writers_are_known():
    """[0] 反空转：写任务表的启动方法必须正好是这两个。"""
    print("[0] 反空转：任务表写入点集合")
    writers = {name for name, fn in _methods(tree()).items() if _writes_task_table(fn)}
    check(writers == {"preview_cloud_voice", "test_voice"},
          "写 _voice_tasks 的方法 == {preview_cloud_voice, test_voice}（实际 %s）" % sorted(writers))


def test_every_runner_cleans_up():
    """[1] 接线：线程体必须清理任务表。"""
    print("[1] 接线：每个试听线程都要清理")
    ms = _methods(tree())
    for name in ("preview_cloud_voice", "test_voice"):
        fn = ms.get(name)
        if fn is None:
            check(False, "%s 不存在" % name)
            continue
        targets = _thread_targets(fn)
        check(bool(targets), "%s 起了后台线程（target 能解析出来）" % name)
        for t in targets:
            runner = ms.get(t)
            if runner is None:
                check(False, "%s 的线程目标 %s 在文件里找不到" % (name, t))
                continue
            check(_has_cleanup(runner),
                  "%s → %s 体内有清理（_schedule_voice_task_cleanup 或 _voice_tasks.pop）"
                  % (name, t))


def test_no_jump_in_finally():
    """[2] _run_voice_test 的 finally 里不许跳出。"""
    print("[2] finally 里不许跳出")
    fn = _methods(tree()).get("_run_voice_test")
    if fn is None:
        check(False, "_run_voice_test 不存在")
        return
    jumps = _finally_jumps(fn)
    check(not jumps, "_run_voice_test 的 finally 里没有 return/break/continue（实际 %s）" % jumps)


def test_cleanup_helper_is_real():
    """[3] 清理助手不是空壳。"""
    print("[3] 清理助手不是空壳")
    fn = _methods(tree()).get("_schedule_voice_task_cleanup")
    if fn is None:
        check(False, "_schedule_voice_task_cleanup 不存在")
        return
    pops = False
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "pop":
            if isinstance(n.func.value, ast.Attribute) and n.func.value.attr == "_voice_tasks":
                pops = True
    check(pops, "助手真的调 self._voice_tasks.pop(...)")
    daemon = False
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "Thread":
            for kw in n.keywords:
                if kw.arg == "daemon" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                    daemon = True
    check(daemon, "起的线程是 daemon=True（否则进程退出时卡住）")


def test_missing_task_is_gone():
    """[4] 清理之后前端轮询不炸。"""
    print("[4] 清理后前端轮询不炸")
    fn = _methods(tree()).get("get_voice_test_status")
    if fn is None:
        check(False, "get_voice_test_status 不存在")
        return
    gone = any(isinstance(n, ast.Constant) and n.value == "gone" for n in ast.walk(fn))
    check(gone, '对缺失任务返回 phase="gone"（不是 KeyError）')


def main():
    print("源文件：%s" % SRC)
    test_task_table_writers_are_known()
    test_every_runner_cleans_up()
    test_no_jump_in_finally()
    test_cleanup_helper_is_real()
    test_missing_task_is_gone()
    print()
    if FAIL:
        print("❌ %d 项失败" % FAIL)
        return 1
    print("✅ 全部通过（语音试听任务表清理 接线）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
