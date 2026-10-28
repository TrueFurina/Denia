"""pytest 收集策略。

`src/tests/` 下混着两类文件：

1. **pytest 用例** —— 定义 `def test_*()`，由 pytest 收集执行；
2. **独立自检脚本** —— import 阶段就跑完整检查、失败时 `sys.exit(1)`。
   典型是 `test_doctor_asr.py`（7 段自检，末尾汇总"共 N 处不符"后退出）。

第 2 类一旦被 pytest 收集，会在 import 阶段抛出 `SystemExit`，pytest 无法把它
当成普通失败，只能以 `INTERNALERROR` 中断整个会话——结果是所有正常用例都跑不到。
（2026-10-28 首次接入 CI 时实测到的就是这个。）

所以这里显式排除它们。排除不影响单独执行：

    python src/tests/test_doctor_asr.py

如果要把它改造成真正的 pytest 用例，做法是把自检体包进 `def test_doctor_asr():`
并把 `sys.exit(1)` 换成 `assert` / `pytest.fail`，然后从下面的列表里删掉。
"""

collect_ignore = [
    "test_doctor_asr.py",
    "_dlg_voice_harness.py",
]
