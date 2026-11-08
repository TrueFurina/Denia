"""通知浮窗「return 之后的死代码」墓碑（2026-11-08）。

背景：src/ui/main_window.py 的 create_notify_window 曾被改成三行转调——

    try:
        from . import native_notify
        return native_notify.ensure_notify_window()
    except Exception as _e:
        logger.warning("[通知浮窗] 兼容壳 ensure 失败: %s", _e)
        return None
    import ctypes          # ← 从这里往后 329 行，永远执行不到
    ...
    return win

try 与 except **两条分支都 return**，其后的 WebView2 旧实现整段不可达。不可达段
里还有 `from .mode_notify import NotifyJsBridge`，而 NotifyJsBridge 在
src/ui/mode_notify.py 里从未存在（0 次命中，16 个顶层定义里没有它）——谁哪天删掉
那两行 return，第一句就是 ImportError，而不是"恢复到旧实现"。

五条判据（全部只读源码 + AST，**不 import 模块**：main_window 依赖 webview /
ctypes / Win32，CI 上根本 import 不了）：

  1. 函数体里**必返回语句之后不许再有语句**（不可达段回潮）。判据写成通用形状
     ——"找出第一条必定离开函数的语句，数它后面还剩几条"，而不是写死
     `body == [Try]`：将来把 try 简化成单个 `return` 是合法重构，不该被拦。
  2. 兼容签名还在（形参 api）。src/ui/webapp.py:77 与 main.py:169 按名字导入它，
     删函数或改签名会打断那两处导入。
  3. 旧实现的符号名不出现在**代码**里（剥掉 docstring 后判）。墓碑注释里提
     NotifyJsBridge 是允许的——那是"为什么删"的说明，不是代码。
  4. 委托目标还在：try 里调的确实是 native_notify.ensure_notify_window()。
     否则"删干净了"可以靠把函数改成 `return None` 达成，通知浮窗静默全灭。
  5. mode_notify.py 里确实没有 NotifyJsBridge——证明墓碑里的说法（"从未存在"）
     是事实而不是借口。

判据 3 必须剥 docstring：本仓在 check_bridge_guards 上踩过"判据被自己的注释
满足"。对照组见 tools/_qc_notify_dead_path_mutation.py 的 M3。

变异刀：tools/_qc_notify_dead_path_mutation.py（四刀 + 对照组）。
"""

from __future__ import annotations

import ast
import io
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MAIN_WINDOW = os.path.join(ROOT, "src", "ui", "main_window.py")
MODE_NOTIFY = os.path.join(ROOT, "src", "ui", "mode_notify.py")

# 旧 WebView2 通知窗的符号名。只允许出现在 docstring / 注释里。
DEAD_NAMES = (
    "NotifyJsBridge",
    "_notify_apply_clickthrough",
    "_install_gui_heartbeat",
    "_notify_selfcheck",
    "_notify_park_out",
)


def _read(path: str) -> str:
    with io.open(path, "r", encoding="utf-8") as f:
        return f.read()


def _parse(path: str) -> ast.Module:
    return ast.parse(_read(path), filename=path)


def _strip_docstrings(node: ast.AST) -> ast.AST:
    """把模块/函数/类首位的 docstring 常量从 AST 里摘掉。

    判据必须看**代码**，不能看说明文字：墓碑按本仓惯例写着"删了什么、为什么删"，
    里面必然出现被删符号名。不剥 docstring，判据 3 会被自己的墓碑满足。
    """
    for n in ast.walk(node):
        if not isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = list(n.body)
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body = body[1:]
        n.body = body or [ast.Pass()]
    return node


def _code_dump(node: ast.AST) -> str:
    """只含代码的 AST 序列化：注释天然不在 AST 里，docstring 被剥掉。"""
    return ast.dump(_strip_docstrings(node))


def _always_exits(stmt: ast.stmt) -> bool:
    """这条语句执行完是否**必定**离开当前块（return/raise/continue/break，或全分支必离开的 if/try）。"""
    if isinstance(stmt, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
        return True
    if isinstance(stmt, ast.If):
        return bool(stmt.orelse) and _always_exits(stmt.body[-1]) and _always_exits(stmt.orelse[-1])
    if isinstance(stmt, ast.Try):
        ok = bool(stmt.body) and _always_exits(stmt.body[-1])
        ok = ok and all(h.body and _always_exits(h.body[-1]) for h in stmt.handlers)
        if stmt.orelse:
            ok = ok and _always_exits(stmt.orelse[-1])
        return ok
    return False


def _unreachable_after(stmts: list) -> int:
    """第一条"必离开"语句之后还剩几条语句（正常应为 0）。"""
    for i, s in enumerate(stmts):
        if _always_exits(s):
            return len(stmts) - i - 1
    return 0


def _find_func(tree: ast.Module, name: str):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


class NotifyDeadPathTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tree = _parse(MAIN_WINDOW)
        self.fn = _find_func(self.tree, "create_notify_window")
        self.assertIsNotNone(
            self.fn,
            "create_notify_window 不见了：src/ui/webapp.py:77 与 main.py:169 仍按名字导入它，"
            "删函数会让那两处 ImportError。")

    def test_no_statement_after_always_return(self) -> None:
        """判据 1：必返回语句之后不许再有语句。"""
        body = [s for s in self.fn.body
                if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
        n = _unreachable_after(body)
        self.assertEqual(
            n, 0,
            "create_notify_window 在必定 return 的语句之后还有 %d 条语句——"
            "2026-11-08 删掉的 329 行 WebView2 旧实现又回来了。"
            "旧段引用的 mode_notify.NotifyJsBridge 根本不存在，回潮即 ImportError。" % n)

    def test_compat_signature_kept(self) -> None:
        """判据 2：形参 api 保留（webapp.py / main.py 按位置传）。"""
        args = [a.arg for a in self.fn.args.args]
        self.assertEqual(
            args, ["api"],
            "create_notify_window 的签名变了（现在 %r）。它是兼容壳，"
            "src/ui/webapp.py:77 与 main.py:169 依赖这个名字与位置参数。" % (args,))

    def test_dead_symbols_absent_from_code(self) -> None:
        """判据 3：旧符号名不出现在代码里（docstring 与注释豁免）。"""
        dump = _code_dump(_parse(MAIN_WINDOW))
        hits = [n for n in DEAD_NAMES if n in dump]
        self.assertEqual(
            hits, [],
            "旧 WebView2 通知窗的符号回到了 src/ui/main_window.py 的**代码**里：%s。"
            "其中 NotifyJsBridge 在 mode_notify.py 中从未存在，写进代码就是 ImportError。"
            "（写在 docstring/注释里说明历史不算违规，本判据已剥掉 docstring。）" % (hits,))

    def test_delegates_to_native_notify(self) -> None:
        """判据 4：仍然转调 native_notify.ensure_notify_window()。"""
        src = _code_dump(self.fn)
        self.assertIn(
            "ensure_notify_window", src,
            "create_notify_window 不再调用 native_notify.ensure_notify_window()。"
            "把函数清成 `return None` 也算「删干净了」，但通知浮窗会静默全灭——"
            "本仓的 WPF 通知窗是单后端，没有兜底。")

    def test_mode_notify_never_had_bridge(self) -> None:
        """判据 5：mode_notify.py 确实没有 NotifyJsBridge（墓碑说法属实）。"""
        src = _read(MODE_NOTIFY)
        self.assertNotIn(
            "NotifyJsBridge", src,
            "src/ui/mode_notify.py 里出现了 NotifyJsBridge。若这是**有意**恢复旧桥接，"
            "请同时更新本判据与 tools/_qc_notify_dead_path_mutation.py 的墓碑说明。")

    def test_control_docstring_mentions_are_allowed(self) -> None:
        """对照组：墓碑 docstring 里提到这些名字，必须**仍然全绿**。

        这条不是走过场——判据 3 若忘了剥 docstring，会被本函数自己的墓碑满足，
        于是"删干净了"恒真，变异刀 M1/M2 全哑。此处显式断言豁免生效。
        """
        doc = ast.get_docstring(self.fn) or ""
        self.assertIn("NotifyJsBridge", doc,
                      "墓碑 docstring 里应当保留「为什么删」的说明（含 NotifyJsBridge）；"
                      "说明没了，下一个人只能靠 git log 考古。")
        dump = _code_dump(_parse(MAIN_WINDOW))
        self.assertNotIn("NotifyJsBridge", dump,
                         "判据 3 的剥 docstring 失效了：墓碑说明被当成了代码。")


if __name__ == "__main__":
    unittest.main(verbosity=2)
