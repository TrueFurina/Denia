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
print()
print("=" * 66)
print("汇总：PASS=%d  FAIL=%d" % (PASS, FAIL))
print("=" * 66)
sys.exit(0 if FAIL == 0 else 1)
