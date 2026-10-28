"""pytest 收集策略。

`src/tests/` 下混着两类文件：

1. **pytest 用例** —— 含 `def test_*()`，由 pytest 收集执行（当前 5 个）；
2. **自检脚本** —— 模块级顺序执行一段检查，打印 `OK/FAIL`，末尾 `sys.exit(1)` 收场。
   例如 `test_doctor_asr.py`、`test_doctor_voice_backend.py`。

第 2 类被 pytest 当成用例模块 import 时，会在 import 阶段抛出 `SystemExit`。
pytest 无法把 import 期的 SystemExit 记成普通失败，只能 `INTERNALERROR` **中断整个会话**
——结果是所有正常用例都跑不到。（2026-10-28 接入 CI 时实测：55 个文件 0 个用例执行。）

处置：下面按"文件里有没有 `def test_`"动态生成忽略名单。
新增脚本式自检会自动被排除，新增真用例会自动被收集，不用维护白名单。

脚本仍可单独执行，失败与否看退出码：

    python src/tests/test_doctor_asr.py

要把某个脚本改造成真用例：把检查体包进 `def test_xxx():`，`sys.exit(1)` 换成
`assert` / `pytest.fail`，改完这个文件就会自动被收集。
"""

import pathlib

_HERE = pathlib.Path(__file__).parent


def _is_script(path: pathlib.Path) -> bool:
    if not path.name.endswith(".py"):
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return True
    return "def test_" not in text


# 脚本式自检（含辅助 harness）不参与 pytest 收集
collect_ignore = sorted(
    p.name for p in _HERE.glob("*.py") if _is_script(p)
)
