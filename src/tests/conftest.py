"""pytest 收集策略。

`src/tests/` 下的文件分三类，只有第三类该进 pytest：

1. **纯自检脚本** —— 模块级顺序执行、打印 OK/FAIL、末尾 `sys.exit(1)`；
2. **带 main() 的混合文件** —— 定义 `def main()` 自己调度，里面的 `test_*` 往往
   只是辅助函数（例如 `test_parity(api, info: dict)` 需要 info 字典，
   pytest 会把它当 fixture 请求，报 `fixture 'info' not found`）；
3. **纯 pytest 用例模块** —— 有 `def test_`、没有 `def main()`，用例自包含。

第 1、2 类被 pytest import 时，要么抛 SystemExit 中断整个会话，要么满屏 fixture 报错。
所以按"内容特征"动态排除，而不是人工维护名单：

    没有 def test_   → 排除（脚本）
    有 def main()    → 排除（自己的调度器，脚本用法才是原生用法）
    其余             → 收集

这两类文件请直接当脚本跑，退出码即结论：

    python src/tests/test_rpc_server.py

要把某个文件改造成真用例：删掉 `def main()` 与 `sys.exit`，让 `def test_*` 自包含，
改完会自动被收集。
"""

import pathlib

_HERE = pathlib.Path(__file__).parent


def _is_script(path: pathlib.Path) -> bool:
    if path.suffix != ".py":
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return True
    return ("def test_" not in text) or ("def main(" in text)


collect_ignore = sorted(p.name for p in _HERE.glob("*.py") if _is_script(p))
