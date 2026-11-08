#!/usr/bin/env python
"""test_voice_task_cleanup 的变异刀（2026-11-08）。

判定文件守的是「语音试听任务表必须有人清理」。四把刀各打一种回潮方式，
每把都必须让**对应那一条**判据红（而不是随便红一条 —— 红在别处等于没验到）：

  K1 云端试听去掉清理（原 bug 原样）        → [1] 红
  K2 清理助手退化成空壳（pop 删掉）         → [3] 红
  K3 finally 里把 return 加回去             → [2] 红
  K4 缺失任务的 phase 改回不带 "gone"       → [4] 红
  K0 对照组（原样副本）                     → 全绿

刀只在 tempfile 里改**副本**：判定文件认 `QC_WEBAPP_PATH` 环境变量，仓库一个字节都不动。
（前几轮有刀靠「改源码再还原」，还原校验本身成了新的失败面。）

用法：python tools/_qc_voice_task_cleanup_mutation.py
退出码：0 = 五刀全部符合预期。
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src", "ui", "webapp.py")
TEST = os.path.join(ROOT, "src", "tests", "test_voice_task_cleanup.py")
PY = sys.executable

# 三个锚点都取自本 PR 落地的文本，锚点漂了要立刻报错，不能静默「改不动就算过」
ANCHOR_CLOUD_FINALLY = (
    "        finally:\n"
    "            # 【2026-11-08】云端试听原来没有任何清理：全文件的 _voice_tasks.pop 只有一处，\n"
    "            # 在本地试听的 _cleanup 里 —— 这条路每点一次就往任务表里留一条，永不回收。\n"
    "            # 与本地试听共用同一个清理助手，两条路不再各自长。\n"
    "            self._schedule_voice_task_cleanup(task_id)\n"
)
ANCHOR_POP = "            self._voice_tasks.pop(task_id, None)"
ANCHOR_GUARD = "            if backend is not None and backend.name in self._LOCAL_MOUNT_ENGINES:"
ANCHOR_GONE = '"phase": "gone"'


def _run(path: str) -> tuple[int, str]:
    env = dict(os.environ)
    env["QC_WEBAPP_PATH"] = path
    p = subprocess.run([PY, TEST], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _mutate(text: str, old: str, new: str) -> str:
    assert old in text, "锚点漂了，先更新刀：%r" % old[:60]
    return text.replace(old, new, 1)


def main() -> int:
    with io.open(SRC, "r", encoding="utf-8") as f:
        original = f.read()

    tmp = tempfile.mkdtemp(prefix="qc_vtc_")
    bad = 0
    try:
        # K0 对照组
        base = os.path.join(tmp, "base.py")
        with io.open(base, "w", encoding="utf-8", newline="") as f:
            f.write(original)
        code, out = _run(base)
        ok = code == 0
        print("PASS K0 对照组（原样副本）-> exit 0" if ok else "FAIL K0 对照组 -> rc=%d" % code)
        if not ok:
            bad += 1
            print(out[-600:])

        cases = [
            ("K1", "云端试听去掉清理", ANCHOR_CLOUD_FINALLY, "", "_run_cloud_preview 体内有清理"),
            ("K2", "清理助手退化成空壳", ANCHOR_POP, "            pass", "助手真的调"),
            ("K3", "finally 里加回 return", ANCHOR_GUARD,
             "            if backend is None:\n"
             "                return\n"
             "            if backend.name in self._LOCAL_MOUNT_ENGINES:",
             "finally 里没有 return"),
            ("K4", "缺失任务的 phase 改名", ANCHOR_GONE, '"phase": "missing"', 'phase="gone"'),
        ]
        for tag, label, old, new, must_in in cases:
            text = _mutate(original, old, new)
            path = os.path.join(tmp, tag.lower() + ".py")
            with io.open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            code, out = _run(path)
            ok = code == 1 and must_in in out
            if ok:
                print("PASS %s %s -> exit 1 且点名「%s」" % (tag, label, must_in))
            else:
                print("FAIL %s %s -> rc=%d（应 1 且点名「%s」）" % (tag, label, code, must_in))
                bad += 1
                print(out[-600:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 60)
    print("PASS：五刀全部符合预期" if not bad else "FAIL：%d 把刀不符预期" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
