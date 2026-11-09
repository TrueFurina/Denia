"""变异刀：`关系集成` 闸门（tools/test_relationship_integration.py）的三处「只有一侧」缺口。

每把刀都是**真改生产代码**（src/companion/relationship_reporter.py），跑完立刻还原并校验 sha256。
判据：闸门退出码非 0 **且** 指定的那条断言变红（不是「随便红一个」）。

    python tools/_qc_relationship_integration_mutation.py

背景：2026-11-10 探针发现原版有三处单侧断言 ——
  · test_04 只写 `turns <= 1`（turns=0 也成立），且场景里只投 1 句用户话，
    队列为空正好替去重打掩护：去掉去重照样绿；
  · test_20 没启动 `EventBus.run()`（`publish` 只是 `queue.put`），量的是入队耗时，
    两个事件从头到尾没被处理过；
  · test_03 全是负向断言（proactive 不判定、不动队列），判定整条链路不启动也绿。
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
REPORTER = os.path.join("src", "companion", "relationship_reporter.py")
GATE = os.path.join("tools", "test_relationship_integration.py")

SPAWN_LINE = "        self._spawn_judge(cid, ut)"
DEDUP_BLOCK = '''        # 重复投递去重（同一句 5s 内只记一次）：防重试/双路径把一轮记成两轮
        _now = time.time()
        if text == self._last_ai_text and (_now - self._last_ai_ts) < 5.0:
            return
        self._last_ai_text = text
        self._last_ai_ts = _now
'''

# (名字, old, new, 必须变红的那条断言所在用例)
MUTS = [
    ("A 判定永不启动",
     SPAWN_LINE,
     "        return  # MUT-A：配对照做，判定永不启动",
     ["test_03_proactive_not_counted",
      "test_04_dedup_same_ai_text",
      "test_20_publish_returns_before_judge_done"]),
    ("B 去掉重复投递去重",
     DEDUP_BLOCK,
     "        # MUT-B：去重整段删除\n",
     ["test_04_dedup_same_ai_text"]),
    ("C 裁判拿到的是 AI 那句话（张冠李戴）",
     SPAWN_LINE,
     "        self._spawn_judge(cid, text)  # MUT-C：待判文本被换成她自己的回复",
     ["test_06_judge_sees_user_text_not_ai"]),
]


def sha(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def run_gate() -> tuple[int, str]:
    p = subprocess.run([PY, GATE], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=900)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    path = os.path.join(ROOT, REPORTER)
    with open(path, "rb") as f:
        original = f.read()
    h0 = hashlib.sha256(original).hexdigest()
    text = io.TextIOWrapper(io.BytesIO(original), encoding="utf-8", newline="").read()

    print("== 起点必须绿 ==")
    rc, out = run_gate()
    print(f"  基线退出码={rc}")
    if rc != 0:
        print("  起点不绿 → 百分之百的失败都不可信，直接退")
        print("\n".join(out.strip().splitlines()[-12:]))
        return 1

    failures = []
    try:
        for name, old, new, must_red in MUTS:
            if text.count(old) != 1:
                print(f"  ✗ {name}: 锚点命中 {text.count(old)} 次（应为 1）")
                failures.append(name)
                continue
            with open(path, "wb") as f:
                f.write(text.replace(old, new).encode("utf-8"))
            try:
                rc, out = run_gate()
            finally:
                with open(path, "wb") as f:
                    f.write(original)
            caught = rc != 0 and all(
                (f"FAIL: {n}" in out) or (f"ERROR: {n}" in out) for n in must_red
            )
            detail = "变红" if rc != 0 else "仍然全绿"
            print(f"  {'✓' if caught else '✗'} {name}: 退出码={rc}（{detail}）")
            if not caught:
                missing = [n for n in must_red
                           if (f"FAIL: {n}" not in out) and (f"ERROR: {n}" not in out)]
                print(f"      未被点名（说明这条判据对该破坏无感）：{missing}")
                failures.append(name)
    finally:
        with open(path, "wb") as f:
            f.write(original)
        h1 = sha(path)
        print(f"\n还原 sha256 一致={h1 == h0}")

    print(f"\nMUTATION_RESULT: {'ALL PASS' if not failures else 'FAILED ' + str(failures)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
