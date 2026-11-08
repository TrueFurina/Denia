# -*- coding: utf-8 -*-
"""闸门：async 函数里不许有同步阻塞调用（事件循环杀手）。

真实案例（2026-10-18，一天内撞到四处）：
  · `gewechat._on_decision` 里 urllib 同步发消息（超时 15s）→ 桌宠卡死；
  · `qzone._on_decision` 里 `publish()` 同样；
  · `tts._pyttsx3_speak` 里 `time.sleep(0.6)` → 每说一句卡 600ms；
  · `heavy_apps.pull_cloud / report_promoted` 里 `urlopen(timeout=5)` → 首启卡 5s。
症状都是"卡一下"，不报错、不告警，所以只能静态查。

判定：async 函数体内（**不下钻嵌套 def**——嵌套同步函数通常是交给 to_thread 的
worker，写阻塞代码是对的）出现 time.sleep / urllib.urlopen / subprocess.run /
requests.* / os.system / socket.* 等同步调用，且没有被 to_thread / run_in_executor
包裹。

用法：python tools/check_async_blocking.py [根目录]   # 缺省扫 src
退出码：0 = 未发现；1 = 发现可疑点。
"""
from __future__ import annotations

import ast
import os
import sys

SKIP_DIRS = {"__pycache__", ".venv", "python", ".git", "dist_stage", "node_modules", "tests"}

# (模块名, 属性名)；属性名为 None 表示该模块任意属性
BLOCKING = {
    ("time", "sleep"), ("urllib", "urlopen"), ("subprocess", "run"),
    ("subprocess", "check_output"), ("subprocess", "check_call"),
    ("subprocess", "Popen"), ("os", "system"), ("socket", None),
    ("requests", None), ("shutil", "copy"), ("shutil", "copy2"),
    ("shutil", "copyfile"),
    # 【2026-11-08 补盲】httpx 同步入口：与 requests 同级，是事件循环杀手。
    # 注意【只】列同步入口；httpx.AsyncClient（异步安全） deliberately 不列，避免误伤。
    ("httpx", "get"), ("httpx", "post"), ("httpx", "put"),
    ("httpx", "patch"), ("httpx", "delete"), ("httpx", "head"),
    ("httpx", "request"), ("httpx", "Client"),
}
WRAPPERS = {"to_thread", "run_in_executor"}


def _walk_own(node: ast.AST):
    """遍历 node 的子节点，但不进入嵌套的函数/类（那是另一个执行上下文）。"""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        yield child
        yield from _walk_own(child)


def _call_name(call: ast.Call) -> tuple[str, str] | None:
    f = call.func
    if isinstance(f, ast.Attribute):
        base = f.value
        if isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name):
            return (base.value.id, f.attr)      # urllib.request.urlopen
        if isinstance(base, ast.Name):
            return (base.id, f.attr)            # time.sleep
        return (getattr(base, "attr", ""), f.attr)
    if isinstance(f, ast.Name):
        return ("", f.id)
    return None


def scan(path: str) -> list[str]:
    try:
        tree = ast.parse(open(path, encoding="utf-8", errors="replace").read())
    except SyntaxError:
        return []
    hits: list[str] = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)]:
        nodes = list(_walk_own(fn))
        safe: set[int] = set()
        for n in nodes:
            if isinstance(n, ast.Call):
                nm = _call_name(n)
                if nm and nm[1] in WRAPPERS:
                    for a in n.args:
                        for c in ast.walk(a):
                            if isinstance(c, ast.Call):
                                safe.add(id(c))
        for n in nodes:
            if not isinstance(n, ast.Call) or id(n) in safe:
                continue
            nm = _call_name(n)
            if not nm:
                continue
            mod, attr = nm
            for bm, ba in BLOCKING:
                if bm == mod and (ba is None or ba == attr):
                    hits.append(f"{path}:{n.lineno}  async {fn.name}() 里同步调用 {mod}.{attr}()")
                    break
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
