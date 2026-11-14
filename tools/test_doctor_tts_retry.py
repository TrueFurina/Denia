"""语音合成判据：瞬时 0 字节不许直接报 FAIL，但真故障仍必须红。

背景（2026-11-10）：`main.py --doctor` 在 6 次连跑里出现过 1 次
`[FAIL] 语音合成 engine='pyttsx3'：合成返回了但产物为空/过小（0 字节）`，
随后连续 3 次复跑全绿；2026-11-08 也记录过一次同样形状的「15 PASS / 3 WARN / 1 FAIL」。
两次都是**单次抖动**，不是合成管线坏 —— 直接把抖动判成 FAIL，会让用户以为「她永远不说话」。

本测试**直接驱动生产函数** `tools/doctor.py:check_voice_backend`，只替换
`src.voice.tts_backends.create_backend` 这一个依赖，不复制判据逻辑（复刻逻辑测的是抄件）。

用法：
    python tools/test_doctor_tts_retry.py                        # 验仓内 tools/doctor.py
    DOCTOR_PATH=<file> python tools/test_doctor_tts_retry.py     # 验指定文件（变异/对照用）

退出码：0 = 全过；1 = 有失败。
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_root() -> str:
    """向上找同时含 `tools/` 与 `src/` 的目录（本文件可能在 tools/ 或临时目录里）。"""
    cur = HERE
    for _ in range(4):
        if os.path.isdir(os.path.join(cur, "src")) and os.path.isdir(os.path.join(cur, "tools")):
            return cur
        cur = os.path.dirname(cur)
    return os.path.dirname(HERE)


ROOT = _find_root()
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _default_doctor() -> str:
    for cand in (
        os.path.join(ROOT, "tools", "doctor.py"),
        os.path.join(HERE, "doctor.py"),
    ):
        if os.path.isfile(cand):
            return cand
    return os.path.join(ROOT, "tools", "doctor.py")


CHECKS: list[tuple[str, bool, str]] = []


def chk(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + (("  " + detail) if detail else ""))


def load_doctor(path: str):
    spec = importlib.util.spec_from_file_location("_doctor_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_doctor_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def make_cfg():
    class _VC:
        engine = "pyttsx3"
        api_url = ""
        api_key = ""

    class _CFG:
        def get_active_voice_config(self):
            return _VC()

    return _CFG()


def install_stub(behaviour: str):
    """把 create_backend 换成桩。

    behaviour:
      "ok"             每次都写出合法大小的音频
      "empty"          每次都返回但产物 0 字节（合成管线真坏）
      "empty_then_ok"  第一次 0 字节、之后正常（**真实抖动的形状**）
      "raise"          每次抛异常
    """
    from src.voice import tts_backends  # noqa: PLC0415

    state = {"calls": 0}

    class StubBackend:
        def speak(self, text, out):
            state["calls"] += 1
            if behaviour == "raise":
                raise RuntimeError("合成后端炸了（测试桩）")
            write_empty = (behaviour == "empty") or (
                behaviour == "empty_then_ok" and state["calls"] == 1
            )
            if write_empty:
                open(out, "wb").close()          # 返回了，但产物 0 字节
            else:
                with open(out, "wb") as f:       # 真实形状：RIFF/WAVE 头 + 若干字节
                    f.write(b"RIFF" + b"\x00" * 4 + b"WAVEfmt " + b"\x00" * 48000)

    tts_backends.create_backend = lambda engine, cfg: StubBackend()
    return state


def run_case(doctor, behaviour: str):
    state = install_stub(behaviour)
    return doctor.check_voice_backend(make_cfg()), state["calls"]


def main() -> int:
    path = os.environ.get("DOCTOR_PATH") or _default_doctor()
    print(f"== 被测实现：{path} ==")
    if not os.path.isfile(path):
        print("  FAIL  找不到被测文件")
        return 1
    doctor = load_doctor(path)

    # [0] 反空转：拿不到函数时后面全是真空真
    chk("[D0] 被测函数存在（否则本测试空转）",
        callable(getattr(doctor, "check_voice_backend", None)),
        f"type={type(getattr(doctor, 'check_voice_backend', None)).__name__}")

    # D1 正常：一次就够，不许白跑第二次
    (level, _i, detail, _h), calls = run_case(doctor, "ok")
    chk("[D1] 正常合成 → PASS 且只合成一次",
        level == doctor.PASS and calls == 1, f"level={level} calls={calls}")

    # D2 恒空：真故障必须仍然红，且重试次数恰为 2（不许无限重试）
    (level, _i, detail, _h), calls = run_case(doctor, "empty")
    chk("[D2] 恒为空 → FAIL（重试不许掩盖真故障）",
        level == doctor.FAIL and calls == 2, f"level={level} calls={calls} detail={detail}")

    # D3 抖动：第一次空、第二次正常 → PASS（本次修复的目的就是这一条）
    (level, _i, detail, _h), calls = run_case(doctor, "empty_then_ok")
    chk("[D3] 单次抖动 → PASS（重试一次救回）",
        level == doctor.PASS and calls == 2, f"level={level} calls={calls} detail={detail}")

    # D4 抛异常：异常路径不受重试影响，仍 FAIL 且点名异常类型
    (level, _i, detail, _h), calls = run_case(doctor, "raise")
    chk("[D4] 后端抛异常 → FAIL 且点名异常类型",
        level == doctor.FAIL and "RuntimeError" in detail, f"level={level} detail={detail}")

    # D5 临时文件不许留垃圾
    tmp = tempfile.gettempdir()
    leftover = [n for n in os.listdir(tmp) if n.startswith("daniya_doctor_tts_")]
    chk("[D5] 不留临时音频残留", not leftover, f"残留={leftover[:3]}")

    bad = [n for n, ok, _ in CHECKS if not ok]
    print()
    print(f"RESULT: {len(CHECKS) - len(bad)} PASS / {len(bad)} FAIL")
    if bad:
        print("失败项：" + ", ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
