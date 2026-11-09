# -*- coding: utf-8 -*-
"""回归：试听播放生命周期（防孤儿 ffplay / 叠播 / voice_test 线程堆积）。

【背景】原试听路径 `_run_voice_test`（voice_test_<tid> daemon 线程）同步调
`_synthesize_and_play` → `_play_audio_system`，其中 ffplay 分支用
`subprocess.run(ffplay, timeout=dur+60)`、sounddevice 用 `_sd.wait()`，阻塞到整段
播放结束（30s 样本≈90s）。两个真 bug：
  ① **孤儿 ffplay**：App 退出时该 daemon 线程被强杀，ffplay 子进程是无独立生命周期的
     独立进程，不会被一并带走 → 关 App 后继续出声到播完；
  ② **叠播 / 线程堆积**：重复点试听，旧 voice_test 线程仍卡在上一段播放里，新线程又起
     一个 ffplay → 多个 ffplay 同时响、线程越堆越多。
（注：不冻 asyncio 主循环 / UI 线程——2026-09-10 已把试听线程化；这里冻的是该后台线程
本身，与 course_recorder 那次「主循环自死锁」是不同问题。）

【修复】ffplay 分支 `subprocess.run` → `Popen`，句柄存 `self._voice_play_proc`；
`_kill_voice_playback()` 在新试听开始 / 进程退出(atexit) 时显式终结它；
`_start_voice_playback()` 把播放放进独立 `voice_playback` daemon 线程 → 调用方立即返回。

【判据】全部用**真实 OS 进程 / 真实线程**验证可观测后果，不靠「标志位被设成 None」：
  A. 哨兵法：先造一个真实存活子进程当 ffplay 替身，kill 后断言 `poll()` 变非 None
     （本机没有真 ffplay 时，忘 kill 也会「句柄清空」全绿——必须先造可控污染源）。
  B. 新试听抢占：旧哨兵在 `_start_voice_playback` 后必须真死（无叠播）。
  C. 非阻塞：播放桩 sleep 0.4s，`_start_voice_playback` 必须在 0.2s 内返回，且桩确实
     在独立线程里被执行（done 事件）——旧同步实现会卡 ~0.4s 才返回。

【诚实边界】headless 沙箱无 GUI/音频设备，不真起 ffplay 出声；这里验的是「句柄可被
终结、抢占、非阻塞调度」这一层契约，真实出声路径仍需真机试听一次人工确认。

运行：.venv/Scripts/python.exe src/tests/test_voice_playback_lifecycle.py
"""
import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from src.ui.webapp import CharacterAPI  # noqa: E402

FAIL = []


def check(cond, msg):
    if cond:
        print(f"  [OK] {msg}")
    else:
        print(f"  [FAIL] {msg}")
        FAIL.append(msg)


def _bare():
    """绕开 GUI 版 __init__：只装播放生命周期方法真正用到的两个属性。"""
    obj = object.__new__(CharacterAPI)
    obj._voice_play_lock = threading.Lock()
    obj._voice_play_proc = None
    return obj


def _spawn_sleeper():
    """真实存活子进程：当 ffplay 替身（哨兵 / 可控污染源）。"""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _is_dead(proc, timeout=5.0):
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        if proc.poll() is not None:
            return True
        time.sleep(0.02)
    return proc.poll() is not None


def test_kill_terminates_orphan():
    """A：kill 必须真的终结存活子进程，并清空句柄（防孤儿 ffplay 关 App 后继续响）。"""
    obj = _bare()
    proc = _spawn_sleeper()
    obj._voice_play_proc = proc
    check(proc.poll() is None, "哨兵子进程确实存活（若一开始就死了，本判据无意义）")
    obj._kill_voice_playback()
    check(_is_dead(proc), "_kill_voice_playback 真的终结了播放子进程（防孤儿 ffplay）")
    check(obj._voice_play_proc is None, "kill 后 _voice_play_proc 句柄已清空")
    # 幂等：重复调用（atexit 与新试听可能都触发）不得抛异常
    obj._kill_voice_playback()
    check(obj._voice_play_proc is None, "重复 _kill_voice_playback 幂等且句柄仍为 None")


def test_preempt_kills_previous():
    """B：新试听启动必须抢占（终结）上一段播放，避免多个 ffplay 叠播。"""
    obj = _bare()
    old = _spawn_sleeper()
    obj._voice_play_proc = old
    # 新播放用桩立即返回，不碰真实音频设备；_play_audio_system 是实例属性，可覆盖。
    obj._play_audio_system = lambda path: None
    check(old.poll() is None, "上一段播放子进程确实存活（抢占判据的哨兵）")
    obj._start_voice_playback("new.wav")
    check(_is_dead(old), "新试听抢占：上一段 ffplay 被终结（无叠播 / 不残留污染源）")


def test_start_is_nonblocking():
    """C：播放必须在独立线程跑，_start_voice_playback 立即返回，不卡 voice_test 线程。"""
    obj = _bare()
    done = threading.Event()

    def _slow_play(path):
        time.sleep(0.4)   # 模拟整段播放耗时（旧同步实现会让调用方卡这么久）
        done.set()        # 桩真做出来：可观测后果，不是只记个标志位

    obj._play_audio_system = _slow_play
    t0 = time.perf_counter()
    obj._start_voice_playback("fake.wav")
    dt = time.perf_counter() - t0
    check(dt < 0.2, f"_start_voice_playback 非阻塞：{dt * 1000:.0f}ms 返回 < 200ms")
    check(done.wait(timeout=3.0), "播放桩确实在独立线程里被执行（非阻塞且真起线程）")


if __name__ == "__main__":
    test_kill_terminates_orphan()
    test_preempt_kills_previous()
    test_start_is_nonblocking()
    print()
    if FAIL:
        print(f"共 {len(FAIL)} 处不符")
        sys.exit(1)
    print("全部通过：试听播放生命周期契约成立（无孤儿 ffplay / 无叠播 / 非阻塞）")
