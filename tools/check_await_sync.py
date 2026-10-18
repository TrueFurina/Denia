# -*- coding: utf-8 -*-
"""闸门：协程调用的两个方向都要对。

规则 1（await 同步方法）：`await self.f()` 而 f 是同步 def → `await None` 抛 TypeError，
常被上层 `except` 吞掉，功能静默失效。
真实案例（2026-10-18）：`gewechat._on_decision` 里 `await self._safe_post(...)`，
而 `_safe_post` 是同步 def → 每次回微信都抛
`TypeError: object NoneType can't be used in 'await' expression`，被事件总线 catch
后只剩一行日志，**微信回复从来没真正发出去过**（症状像"AI 不理人"）。

规则 2（async 方法没被调度）：`self.f()` 而 f 是 async def → 得到一个协程对象，
没人 await 就丢掉了，函数体一行都不执行，最多留一条 "coroutine was never awaited"
的 RuntimeWarning。属于同一类"写了但没接线"。

已知的非问题（规则 2 必须排除，否则是噪音）：
  · 异步生成器（body 里有 yield）——调用它本来就该直接拿生成器去 `async for`；
  · 交给 `asyncio.run` / `run_until_complete` / `create_task` / `gather` /
    `wait_for` / `ensure_future` / `run_coroutine_threadsafe` 的调用。

用法：python tools/check_await_sync.py [根目录]   # 缺省扫 src
退出码：0 = 未发现；1 = 发现可疑点。
"""
from __future__ import annotations

import ast
import os
import sys

SKIP_DIRS = {"__pycache__", ".venv", "python", ".git", "dist_stage", "node_modules", "tests"}

# 这些调用的实参 = 协程被真正消费掉了
CONSUMERS = {
    "run", "run_until_complete", "create_task", "ensure_future", "gather",
    "wait_for", "run_coroutine_threadsafe", "shield", "as_completed",
}


def _is_async_gen(fn: ast.AST) -> bool:
    return any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(fn))


def classes_of(tree: ast.AST):
    """yield (ClassDef, 同步方法名集合, 需要被 await 的 async 方法名集合)。"""
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        sync: set[str] = set()
        need_await: set[str] = set()
        for item in node.body:
            if isinstance(item, ast.AsyncFunctionDef):
                if not _is_async_gen(item):
                    need_await.add(item.name)
            elif isinstance(item, ast.FunctionDef):
                sync.add(item.name)
        yield node, sync - need_await, need_await


def _consumed_ids(cls: ast.AST) -> set[int]:
    """被 await / 交给消费者函数的 Call 节点 id。"""
    ids: set[int] = set()
    for n in ast.walk(cls):
        if isinstance(n, ast.Await):
            for c in ast.walk(n.value):
                if isinstance(c, ast.Call):
                    ids.add(id(c))
        elif isinstance(n, ast.Call):
            f = n.func
            name = getattr(f, "attr", "") or getattr(f, "id", "")
            if name in CONSUMERS:
                for a in n.args:
                    for c in ast.walk(a):
                        if isinstance(c, ast.Call):
                            ids.add(id(c))
    return ids


def scan(path: str) -> list[str]:
    try:
        tree = ast.parse(open(path, encoding="utf-8", errors="replace").read())
    except SyntaxError:
        return []
    hits: list[str] = []
    for cls, sync_names, async_names in classes_of(tree):
        if not sync_names and not async_names:
            continue
        consumed = _consumed_ids(cls)
        for node in ast.walk(cls):
            if isinstance(node, ast.Await):
                call = node.value
                if not isinstance(call, ast.Call):
                    continue
                f = call.func
                if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
                        and f.value.id == "self" and f.attr in sync_names:
                    hits.append(f"{path}:{node.lineno}  {cls.name}.{f.attr}() 是同步方法却被 await")
            elif isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
                        and f.value.id == "self" and f.attr in async_names \
                        and id(node) not in consumed:
                    hits.append(f"{path}:{node.lineno}  {cls.name}.{f.attr}() 是 async 但没 await/调度")
    return hits


def main(argv: list[str]) -> int:
    root = argv[0] if argv else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    all_hits: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(".py"):
                all_hits += scan(os.path.join(dirpath, fn))
    for h in all_hits:
        print(h)
    print(f"\n可疑点 {len(all_hits)} 处")
    return 1 if all_hits else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
