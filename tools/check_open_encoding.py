"""扫描文本模式 open() / Path.read_text() / write_text() 缺少 encoding 的调用。

背景：Windows 中文环境的 locale 编码是 cp936(GBK)，`open(p)` 默认走 locale。
同一份 UTF-8 JSON 在开发机（已设 UTF-8）读得动，在用户机（GBK）上必然
UnicodeDecodeError 或中文乱码 —— 这是「换台电脑就炸」的典型成因。

判定规则：
  · open(...)  mode 不含 'b' 且没有 encoding=  → 违规
  · Path(...).read_text() / write_text() 没有 encoding= → 违规
  · 行尾带 `# noqa: ENC` 或上一行带 → 豁免（必须人工确认过）

用法：
  python tools/check_open_encoding.py --all src main.py tools
  python tools/check_open_encoding.py --all src --list
  python tools/check_open_encoding.py --all src --strict   # CI 闸门，有问题 exit 1
"""
from __future__ import annotations

import ast
import os
import sys

SKIP_DIRS = {"__pycache__", ".git", ".venv", "python", "node_modules"}


def _mode_of(call: ast.Call) -> str | None:
    """取 open() 的 mode；无法静态确定时返回 None（不判定，避免二进制误报）。"""
    mode: str | None = "r"
    has_mode_arg = False
    if len(call.args) >= 2:
        has_mode_arg = True
        mode = call.args[1].value if isinstance(call.args[1], ast.Constant) else None
    for kw in call.keywords:
        if kw.arg == "mode":
            has_mode_arg = True
            mode = kw.value.value if isinstance(kw.value, ast.Constant) else None
    if has_mode_arg and not isinstance(mode, str):
        return None
    return mode if isinstance(mode, str) else "r"


def _has_encoding(call: ast.Call) -> bool:
    return any(kw.arg == "encoding" for kw in call.keywords)


# 这些 .open() 与文件文本编码无关，必须排除，否则误报淹掉真问题：
#   Image.open / pa.open / webbrowser.open / opener.open / wave.open / os.open(fd)
_NON_FILE_OPEN_RECEIVERS = {
    "Image", "_PILImage", "PIL", "pa", "pyaudio", "wave", "os",
    "webbrowser", "opener", "socket", "zipfile", "tarfile", "gzip", "bz2", "lzma",
}


def _is_open_call(call: ast.Call) -> bool:
    f = call.func
    if isinstance(f, ast.Name) and f.id == "open":
        return True
    if not isinstance(f, ast.Attribute) or f.attr != "open":
        return False
    recv = f.value
    if isinstance(recv, ast.Name):
        # io.open / builtins.open 是文件 IO；其余按接收者名字排除
        return recv.id in ("io", "builtins", "codecs")
    # Path(...).open(...) 这类调用式接收者才算
    if isinstance(recv, ast.Call):
        fn = recv.func
        name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else "")
        return name == "Path"
    return False


def _is_path_text_io(call: ast.Call) -> bool:
    f = call.func
    if not isinstance(f, ast.Attribute) or f.attr not in ("read_text", "write_text"):
        return False
    # 只认 Path 对象（Name 或 Path(...) 调用），排除自定义类同名方法
    return isinstance(f.value, (ast.Name, ast.Call))


def check_file(path: str) -> list[str]:
    try:
        src = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []

    lines = src.split("\n")
    out: list[str] = []

    def exempt(lineno: int) -> bool:
        for i in (lineno - 1, lineno - 2):
            if 0 <= i < len(lines) and "noqa: ENC" in lines[i]:
                return True
        return False

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _is_open_call(node):
            mode = _mode_of(node)
            if mode is None or "b" in mode:
                continue
            if _has_encoding(node):
                continue
            if exempt(node.lineno):
                continue
            snippet = lines[node.lineno - 1].strip()[:90]
            out.append(f"{path}:{node.lineno} open(mode={mode!r}) 缺 encoding → {snippet}")
        elif _is_path_text_io(node):
            if _has_encoding(node):
                continue
            if exempt(node.lineno):
                continue
            snippet = lines[node.lineno - 1].strip()[:90]
            out.append(f"{path}:{node.lineno} {node.func.attr}() 缺 encoding → {snippet}")
    return out


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    show_list = "--list" in argv
    strict = "--strict" in argv
    if not args:
        print(__doc__)
        return 2

    files: list[str] = []
    for a in args:
        if os.path.isfile(a):
            files.append(a)
        else:
            for root, dirs, names in os.walk(a):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                files += [os.path.join(root, n) for n in names if n.endswith(".py")]

    problems: list[str] = []
    for f in files:
        problems += check_file(f)

    print(f"扫描 {len(files)} 个文件，文本模式缺 encoding：{len(problems)} 处")
    if problems and show_list:
        by_file: dict[str, int] = {}
        for p in problems:
            by_file[p.split(":")[0]] = by_file.get(p.split(":")[0], 0) + 1
        print("\n=== 按文件分布（前 20）===")
        for f, c in sorted(by_file.items(), key=lambda x: -x[1])[:20]:
            print(f"{c:4d}  {f}")
        print("\n=== 明细 ===")
        for p in problems:
            print("  " + p)
    if strict and problems:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
