# -*- coding: utf-8 -*-
"""回归：course_recorder 不得在主循环线程自死锁（asyncio 主循环反复冻结根因）。

【背景】原实现 _maybe_summarize / _maybe_keyframe_note 用
run_coroutine_threadsafe(coro, self._loop).result(timeout=60/20) 从【主循环线程】
（经 async def run → _tick 调用）同步等自己的协程——循环线程等「要循环才能跑的协程」，
自死锁，把 asyncio 主循环冻 20~60s。网课录制时每次新转写/画面变化就触发一次，38h 累计
数千次 >30s 停顿（看门狗 3625 次「主循环疑似卡死」的真凶）。

【修复】这些方法改 async、直接 await 协程（await 正常让出、不冻循环）。
本测试在真实事件循环上 await 这两个方法（复刻它们经 _tick 跑在循环线程的路径），用
0.1s 即返回的 stub 替换真实 LLM/视觉调用，断言它们在 <2s 内返回（而非 60s/20s 卡死），
且确实把协程 await 完（返回了真实结果，而非因 .result() 超时被吞）。

运行：.venv/Scripts/python.exe src/tests/test_course_recorder_deadlock.py
"""
import asyncio
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from src.companion.course_recorder import CourseRecorder  # noqa: E402

FAIL = []


def check(cond, msg):
    if cond:
        print(f"  [OK] {msg}")
    else:
        print(f"  [FAIL] {msg}")
        FAIL.append(msg)


class _StubLLM:
    async def chat(self, messages, gen_kwargs):
        await asyncio.sleep(0.1)
        return "要点1\n要点2"


class _StubVision:
    async def vision_describe(self, frame, prompt):
        await asyncio.sleep(0.1)
        return "这是一页网课 PPT"


class _StubBus:
    def publish(self, *a, **k):
        return None


def _make_rec():
    cfg = type("C", (), {"ns": type("N", (), {})()})()
    return CourseRecorder(_StubBus(), cfg, llm=_StubLLM(), vision=_StubVision())


async def main():
    rec = _make_rec()
    rec._loop = asyncio.get_running_loop()

    # 最小 session：_maybe_summarize 会读 session.transcript_path 并调 update_summary
    tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w", encoding="utf-8")
    tmp.write("老师讲了第一点。老师讲了第二点。")
    tmp.close()
    session = type("S", (), {})()
    session.transcript_path = tmp.name
    session.update_summary = lambda s: None

    # 1) _maybe_summarize（旧实现会自死锁 60s / 卡死）
    rec._transcripted_lines = 5
    rec._last_summarize_lines = 0
    rec._last_summarize_ts = 0.0
    t0 = time.perf_counter()
    await rec._maybe_summarize(force=True, session=session)
    dt1 = time.perf_counter() - t0
    os.unlink(tmp.name)
    check(dt1 < 2.0, f"_maybe_summarize 返回耗时 {dt1*1000:.0f}ms < 2s（旧实现会卡 60s/自死锁）")

    # 2) _maybe_keyframe_note（旧实现会自死锁 20s / 卡死）
    rec._last_note_ts = 0.0
    t0 = time.perf_counter()
    note = await rec._maybe_keyframe_note(b"\x00\x01")
    dt2 = time.perf_counter() - t0
    check(dt2 < 2.0, f"_maybe_keyframe_note 返回耗时 {dt2*1000:.0f}ms < 2s（旧实现会卡 20s/自死锁）")
    check(note == "这是一页网课 PPT", f"_maybe_keyframe_note 确实 await 完视觉协程并返回结果：{note!r}")


if __name__ == "__main__":
    asyncio.run(main())
    print()
    if FAIL:
        print(f"共 {len(FAIL)} 处不符")
        sys.exit(1)
    print("全部通过：course_recorder 主循环线程自死锁已消除（_tick 路径 await 协程不冻循环）")
