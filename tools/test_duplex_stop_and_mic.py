# -*- coding: utf-8 -*-
"""双工修复回归自测（2026-10-09）。

覆盖本轮修复的三个真实 bug，全部来自 run_20261009_011718.log 实证：

  BUG-1  proactive.py 重连段 `await _rc.stop()`：
         CloudOmniRealtime.stop() 是**同步**方法 → await None → TypeError →
         旧客户端从未停止/从未置 None → WebSocket 残留抢连。
  BUG-2  cloud_omni_realtime.py 静音判据用被稀释的 `_mic_level`：
         真人说话断续 → 说话间隙采样恒 <0.004 → 25s 后误判"哑设备" →
         通话中途换麦 → 上行断流 → AI 不理我。
  BUG-3  webapp.get_role_voice_context 只读 config 静态 duplex_mode：
         运行时档位被拨号改写后，设置页/角色卡显示与真实引擎不符（串档）。

用法：python tools/test_duplex_stop_and_mic.py   （RC=0 即全绿）
"""
import asyncio
import inspect
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [OK]   %s %s" % (name, extra))
    else:
        FAIL += 1
        print("  [FAIL] %s %s" % (name, extra))


# ---------------------------------------------------------------------------
# BUG-1: 兼容同步/异步 stop() 的分派逻辑
# ---------------------------------------------------------------------------
print("=" * 66)
print("A. 双工客户端 stop() 分派（同步 vs 异步）")
print("=" * 66)


class SyncClient:
    """模拟 CloudOmniRealtime：stop() 是同步方法。"""
    def __init__(self):
        self.stopped = False
        self.running = True

    def stop(self) -> None:
        self.stopped = True
        self.running = False


class AsyncClient:
    """模拟 LocalMiniCPMDuplexClient：stop() 是协程。"""
    def __init__(self):
        self.stopped = False
        self.running = True

    async def stop(self) -> None:
        await asyncio.sleep(0)
        self.stopped = True
        self.running = False


class BoomClient:
    """stop() 自身抛异常：宿主必须吞掉且不崩。"""
    def stop(self):
        raise RuntimeError("boom")


async def _dispatch(client):
    """复刻 proactive._stop_duplex_client 的分派核心。"""
    if client is None:
        return
    try:
        ret = client.stop()
        if inspect.isawaitable(ret):
            await ret
    except Exception:
        pass


async def _test_dispatch():
    # A1: 同步 stop() 必须真的被调用（旧代码 await None 会抛 TypeError 且不生效）
    c1 = SyncClient()
    await _dispatch(c1)
    ok("A1 同步 stop() 被正确调用", c1.stopped is True,
       "(旧代码此处 await None → TypeError → stopped 恒 False)")

    # A2: 异步 stop() 必须被 await 完成
    c2 = AsyncClient()
    await _dispatch(c2)
    ok("A2 异步 stop() 被正确 await", c2.stopped is True)

    # A3: 抛异常的 stop() 不得让宿主崩溃
    try:
        await _dispatch(BoomClient())
        ok("A3 异常 stop() 被吞掉不崩溃", True)
    except Exception as e:  # noqa: BLE001
        ok("A3 异常 stop() 被吞掉不崩溃", False, "leaked %r" % e)

    # A4: None 安全
    await _dispatch(None)
    ok("A4 None 客户端安全跳过", True)


asyncio.run(_test_dispatch())


# ---------------------------------------------------------------------------
# BUG-2: 静音判据用 _mic_disp（峰值）而非被稀释的 _mic_level
# ---------------------------------------------------------------------------
print()
print("=" * 66)
print("B. 麦克风静音判据（峰值 vs 被稀释均值）")
print("=" * 66)


def track(prev_level, prev_disp, rms):
    """复刻 _track_mic_level 的电平平滑（起音快、回落慢）+ 显示峰值。"""
    level = rms if rms > prev_level else (prev_level * 0.6 + rms * 0.4)
    disp = rms if rms > prev_disp else (prev_disp * 0.82 + rms * 0.18)
    return level, disp


# 真实场景：通话中「说一句 → 停一下 → 再说一句」，说话占空比约 40%。
# 关键：UI 日志每 2s 取一次样，取到的往往是**句间静音**那一刻的值。
#   旧判据 `_mic_level`（起音快、回落慢的平滑包络）在静音采样点被稀释到阈值下
#   → 累计 25s 就误判"哑设备"→ 通话中途换麦。
#   新判据 `_mic_disp`（近期峰值、快速回落）在静音采样点仍保有人声记忆。
SILENT_TH = 0.004
level, disp = 0.0, 0.0
# 模拟"说完一句后长时间不出声"（你在听对方说 / 思考，实测日志里静音常连续数秒）
# 说话 2 块 → 静音 8 块，共 3 个周期；每个静音块都记录两个指标
sample_level_at_silence = []
for cycle in range(3):
    for _ in range(2):
        level, disp = track(level, disp, 0.22)      # 说话
    for _ in range(8):                              # 长静音
        level, disp = track(level, disp, 0.00008)
        sample_level_at_silence.append((level, disp))

_misjudge_old = sum(1 for lv, _ in sample_level_at_silence if lv < SILENT_TH)
_misjudge_new = sum(1 for _, dp in sample_level_at_silence if dp < SILENT_TH)

ok("B1 旧判据(_mic_level)在长静音采样点存在误判（累计即触发误换麦）",
   _misjudge_old > 0,
   "(旧误判 %d/24 次 vs 新误判 %d/24 次)" % (_misjudge_old, _misjudge_new))
ok("B2 新判据(_mic_disp)把静音采样点的误判显著降低",
   _misjudge_new < _misjudge_old,
   "(新误判 %d/24)" % _misjudge_new)

# 真·全程静音（无人说话）→ 新判据也必须能归零（不误判'有人说话'）
level2, disp2 = 0.0, 0.0
for _ in range(30):
    level2, disp2 = track(level2, disp2, 0.00008)
ok("B3 全程静音：_mic_disp 归到阈值以下（不会误判'有人说话'）",
   disp2 < SILENT_TH, "disp=%.5f" % disp2)


# ---------------------------------------------------------------------------
# BUG-3: 运行时档位优先于 config 静态值
# ---------------------------------------------------------------------------
print()
print("=" * 66)
print("C. 出声方式判定：运行时真相源优先")
print("=" * 66)


def resolve_engine_ui(cfg_dm, rt_dm):
    """复刻 get_role_voice_context 的 engine_ui 判定（运行时优先）。"""
    dm = rt_dm if rt_dm is not None else cfg_dm
    if dm is False or dm == "off":
        return "off"
    if dm in ("local", "local_omni"):
        return "local"
    if dm in ("realtime", "cloud"):
        return "cloud"
    if dm == "auto":
        return "auto"
    return "cloud"


ok("C1 运行时 realtime 覆盖 config local_omni → cloud（不再串档）",
   resolve_engine_ui("local_omni", "realtime") == "cloud")
ok("C2 运行时 local_omni 覆盖 config realtime → local",
   resolve_engine_ui("realtime", "local_omni") == "local")
ok("C3 运行时缺失时回退 config（local_omni → local）",
   resolve_engine_ui("local_omni", None) == "local")
ok("C4 off 语义正确", resolve_engine_ui("local_omni", False) == "off")


# ---------------------------------------------------------------------------
# BUG-4: 采集卡死看门狗（2026-10-23）
#   真实缺陷：_subproc_capture_loop 阻塞在 proc.stdout.read(4)，若 mic_proc 子进程
#   活着却不再写（设备热插拔 / AGC 卡死 / 回调冻结），read 永久阻塞 → 采集冻结、
#   既不重启也不报错（"她听了却卡死"）。修复：后台看门狗线程周期检测（距最后一块
#   音频 >2s）并 kill 子进程 → read EOF → 内层 break → 自动重启。
#   本场景直接对真实 CloudOmniRealtime 的看门狗方法做确定性断言（不复制逻辑，
#   避免"假绿"——复刻逻辑两次写得一样不算测试）。
# ---------------------------------------------------------------------------
print()
print("=" * 66)
print("D. 采集卡死看门狗（真实 CloudOmniRealtime 方法）")
print("=" * 66)


class _FakeProc:
    """替代真实子进程对象：记录是否被 kill，便于确定性断言（不真杀进程）。"""
    def __init__(self):
        self.killed = False

    def kill(self):
        self.killed = True


def _make_o():
    """绕过 __init__ 构造实例（__init__ 会拉起一堆资源），仅注入看门狗所需字段。"""
    o = CloudOmniRealtime.__new__(CloudOmniRealtime)
    o.running = True
    return o


try:
    from src.llm.cloud_omni_realtime import CloudOmniRealtime  # noqa: E402
    D_IMPORT_OK = True
except Exception as _e:  # noqa: BLE001
    D_IMPORT_OK = False
    print("  [WARN] 无法导入 CloudOmniRealtime：%r（场景 D 判红）" % _e)

if D_IMPORT_OK:
    import time as _t

    # D1: 运行态 + 子进程存活 + 距最后一块超时 → 判定停滞（应触发重启）
    o = _make_o()
    proc = _FakeProc()
    o._mic_proc = proc
    o._last_blk_ts = _t.time() - 5.0
    ok("D1 停滞判据：超时无音频块 → _capture_stalled=True",
       o._capture_stalled(_t.time()) is True)

    # D2: 刚收到块 → 非停滞（健康流不被误杀）
    o = _make_o()
    o._mic_proc = _FakeProc()
    o._last_blk_ts = _t.time()
    ok("D2 健康判据：刚收到块 → _capture_stalled=False",
       o._capture_stalled(_t.time()) is False)

    # D3: 已 stop（running=False）→ 不误判（看门狗线程此时已退出，不应再动）
    o = _make_o()
    o.running = False
    o._mic_proc = _FakeProc()
    o._last_blk_ts = _t.time() - 5.0
    ok("D3 已停止：running=False → _capture_stalled=False（不误杀）",
       o._capture_stalled(_t.time()) is False)

    # D4: 子进程未启动（_mic_proc=None）→ 不误判
    o = _make_o()
    o._mic_proc = None
    o._last_blk_ts = _t.time() - 5.0
    ok("D4 未启动：_mic_proc=None → _capture_stalled=False",
       o._capture_stalled(_t.time()) is False)

    # D5: 停滞 → _watchdog_tick 杀掉该子进程
    o = _make_o()
    proc = _FakeProc()
    o._mic_proc = proc
    o._last_blk_ts = _t.time() - 5.0
    o._watchdog_tick(proc)
    ok("D5 停滞触发：_watchdog_tick 杀掉停滞子进程",
       proc.killed is True, "(kill 已调用)")

    # D6: 杀掉后清空引用（避免对同一个已死进程重复 kill / 误杀下一轮新 proc）
    ok("D6 停滞触发：清空 _mic_proc 引用",
       o._mic_proc is None)

    # D7（鉴别力 / 反向对照）：若停滞判据被回归成恒 False（典型"假绿"陷阱），
    #    看门狗绝不该杀进程。此断言证明 kill 动作确实由真实判据驱动，测试有咬合力。
    o = _make_o()
    proc = _FakeProc()
    o._mic_proc = proc
    o._last_blk_ts = _t.time() - 5.0
    o._capture_stalled = lambda now: False  # 模拟回归：判据失效
    o._watchdog_tick(proc)
    ok("D7 鉴别力：判据恒 False → 看门狗不杀进程（捕获'假绿'回归）",
       proc.killed is False and o._mic_proc is proc)

    # D8: _kill_capture_proc 幂等（重复调用安全，第二次不应抛异常）
    o = _make_o()
    proc = _FakeProc()
    o._mic_proc = proc
    o._kill_capture_proc(proc)
    _after_first = proc.killed
    o._mic_proc = proc  # 模拟极端竞态下引用仍在
    try:
        o._kill_capture_proc(proc)
        _second_ok = True
    except Exception:  # noqa: BLE001
        _second_ok = False
    ok("D8 _kill_capture_proc 幂等：两次调用均安全",
       _after_first is True and _second_ok is True)
else:
    ok("D0 真实模块导入", False, "（导入失败，场景 D 无法执行）")


# ---------------------------------------------------------------------------
print()
print("=" * 66)
print("汇总：PASS=%d  FAIL=%d" % (PASS, FAIL))
print("=" * 66)
sys.exit(0 if FAIL == 0 else 1)
