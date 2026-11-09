# -*- coding: utf-8 -*-
"""双工修复回归自测（2026-10-09）。

覆盖本轮修复的三个真实 bug，全部来自 run_20261009_011718.log 实证：

  BUG-1  proactive.py 重连段 `await _rc.stop()`：
         CloudOmniRealtime.stop() 是**同步**方法 → await None → TypeError。
         【2026-11-10 更正·原文写「旧客户端从未停止」，是假的】`await f()` 必然
         先求值 `f()`，所以 stop() **执行了**、客户端真的停了。坏掉的是它**后面
         那行**：异常从分派处逃逸，调用方 `self._duplex_client = None` 与收尾全
         被跳过 → 引用仍指着已停的客户端 → 重连时旧 WebSocket 被复用/抢连。
         真正拦住这条的是同一批加进来的 try/except，不是 `isawaitable` 分支——
         后者只消除"每次重连必刷一条 exception"的噪音。证据：把生产代码回退成
         `await client.stop()`，A1/A2 **照样绿**（客户端照停），唯一会红的是 A5。
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
#   【2026-11-10 修·本段以前是"抄了一遍生产代码再测抄件"】
#   旧版 `_dispatch()` 的注释写着「复刻 proactive._stop_duplex_client 的分派核心」——
#   它把生产实现**复制**到测试里，然后测这份复制品。生产代码怎么改，本段都不会红。
#   现在直接调 `ProactiveStrategy._stop_duplex_client`（`__new__` 绕过 __init__，
#   该方法只用到模块级 logger，不碰实例字段）。
#   讽刺的是本文件 D 段的注释早就写对了：「不复制逻辑，避免"假绿"——复刻逻辑
#   两次写得一样不算测试」。A/B/C 三段违反了 D 段自己写下的规矩。
#   【换了真实现之后还发现】A1/A2 并不能区分修复与回归（见 A1 旁的更正），
#   所以本段补了 A5 —— 那条日志才是"分派写对了"的唯一可观测证据。
# ---------------------------------------------------------------------------
print("=" * 66)
print("A. 双工客户端 stop() 分派（同步 vs 异步）— 真实实现")
print("=" * 66)

try:
    from src.companion import proactive as _proactive_mod  # noqa: E402
    from src.companion.proactive import ProactiveStrategy  # noqa: E402
    A_IMPORT_OK = True
except Exception as _e:  # noqa: BLE001
    A_IMPORT_OK = False
    _proactive_mod = None
    print("  [WARN] 无法导入 ProactiveStrategy：%r（场景 A 判红）" % _e)


class _ErrCatcher(__import__("logging").Handler):
    """只收 ERROR 及以上：`_stop_duplex_client` 的异常分支走 logger.exception。"""

    def __init__(self) -> None:
        super().__init__(level=__import__("logging").ERROR)
        self.records: list[str] = []

    def emit(self, record) -> None:
        self.records.append(record.getMessage())


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
    """调**生产**的分派实现（不再复刻）。"""
    o = ProactiveStrategy.__new__(ProactiveStrategy)
    await o._stop_duplex_client(client, why="自测")


async def _test_dispatch():
    # 【2026-11-10 加】A5 的判据：**正确的分派不该产生任何异常日志**。
    # 为什么需要它：把生产代码回退成 BUG-1 原形 `await client.stop()`，同步客户端的
    # `stop()` **仍然会被调用**（先调用、再 await None），于是 A1/A2 照样绿。
    # 剩下的差别只有一条：TypeError 被 except 吞掉，日志里多一条 exception
    #（原始 bug 里没有这个 except，所以异常会逃到调用方、跳过"置 None / 收尾"；
    # 现在 except 已经挡住了逃逸，因此可观测的后果只剩日志噪音）。
    _catcher = _ErrCatcher()
    _pro_logger = _proactive_mod.logger if A_IMPORT_OK else None
    if _pro_logger is not None:
        _pro_logger.addHandler(_catcher)

    # A1: 同步 stop() 必须真的被调用。
    # 【2026-11-10 更正】原括号里写着「旧代码 await None → TypeError → stopped 恒
    # False」——这是**假的**。`await client.stop()` 先求值 `client.stop()`，客户端
    # 真的停了，stopped 会变 True。实测把生产代码回退成 BUG-1 原形，A1/A2 全绿。
    # 所以 A1/A2 测的是「分派确实调了 stop()」，不是「分派兼容同步/异步」。
    c1 = SyncClient()
    await _dispatch(c1)
    ok("A1 同步 stop() 被正确调用", c1.stopped is True,
       "(注：回退成 await client.stop() 它仍然绿 —— 见上方更正)")

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

    if _pro_logger is not None:
        _pro_logger.removeHandler(_catcher)
        # BoomClient 那条本来就该记一条；所以只数「正常客户端」产生的日志：
        # 上面 A1/A2/A4 用的都是正常客户端，而 catcher 从 A1 开始就在听，
        # 因此这里单独重跑一遍干净的同步/异步分派来判定。
        _catcher.records.clear()
        _pro_logger.addHandler(_catcher)
        await _dispatch(SyncClient())
        await _dispatch(AsyncClient())
        _pro_logger.removeHandler(_catcher)
        ok("A5 正常分派零异常日志（回退成 await client.stop() 会记一条）",
           not _catcher.records, "%r" % (_catcher.records[:1],))


if A_IMPORT_OK:
    asyncio.run(_test_dispatch())
else:
    ok("A0 真实模块导入", False, "（导入失败，场景 A 无法执行）")


# ---------------------------------------------------------------------------
# BUG-2: 静音判据用 _mic_disp（峰值）而非被稀释的 _mic_level
#   【2026-11-10 修·同上】旧版 `track()` 是 `_track_mic_level` 的手抄副本。
#   现在直接喂 16-bit PCM 给真实方法，读它算出来的 `_mic_level` / `_mic_disp`。
#   实测（真实实现，2 块说话 + 8 块静音）：
#     level=0.00375（< 0.004 阈值 → 判"没声音"）
#     disp =0.04501（> 0.004 → 判"刚才有人说话"）
#   这就是 BUG-2 的形状，只是以前证明的是抄件的行为，不是生产代码的行为。
# ---------------------------------------------------------------------------
print()
print("=" * 66)
print("B. 麦克风静音判据（峰值 vs 被稀释均值）— 真实实现")
print("=" * 66)

try:
    from src.llm.cloud_omni_realtime import CloudOmniRealtime  # noqa: E402
    B_IMPORT_OK = True
except Exception as _e:  # noqa: BLE001
    B_IMPORT_OK = False
    print("  [WARN] 无法导入 CloudOmniRealtime：%r（场景 B 判红）" % _e)


def _pcm(amp: float, n: int = 320) -> bytes:
    """造一块 16-bit PCM 单声道小端样本：amp 是归一化幅度（0..1）。"""
    import array as _arr
    v = int(max(-1.0, min(1.0, amp)) * 32767)
    return _arr.array("h", [v] * n).tobytes()


def _mic_probe():
    """绕过 __init__ 造一个只带电平字段的实例。"""
    o = CloudOmniRealtime.__new__(CloudOmniRealtime)
    o._mic_level = 0.0
    o._mic_disp = 0.0
    o._mic_peak = 0.0
    o._mic_voice = False
    o._mic_level_ts = 0.0
    o._clip_warned = ""
    return o


SILENT_TH = 0.004

if B_IMPORT_OK:
    # 真实场景：通话中「说一句 → 停一下 → 再说一句」，说话占空比约 40%。
    # 关键：UI 日志每 2s 取一次样，取到的往往是**句间静音**那一刻的值。
    #   旧判据 `_mic_level`（起音快、回落慢的平滑包络）在静音采样点被稀释到阈值下
    #   → 累计 25s 就误判"哑设备"→ 通话中途换麦。
    #   新判据 `_mic_disp`（近期峰值、快速回落）在静音采样点仍保有人声记忆。
    o = _mic_probe()
    sample_at_silence = []
    for _cycle in range(3):
        for _ in range(2):
            o._track_mic_level(_pcm(0.22))          # 说话
        for _ in range(8):                          # 长静音
            o._track_mic_level(_pcm(0.00008))
            sample_at_silence.append((o._mic_level, o._mic_disp))

    _misjudge_old = sum(1 for lv, _ in sample_at_silence if lv < SILENT_TH)
    _misjudge_new = sum(1 for _, dp in sample_at_silence if dp < SILENT_TH)

    ok("B1 旧判据(_mic_level)在长静音采样点存在误判（累计即触发误换麦）",
       _misjudge_old > 0,
       "(旧误判 %d/24 次 vs 新误判 %d/24 次)" % (_misjudge_old, _misjudge_new))
    ok("B2 新判据(_mic_disp)把静音采样点的误判显著降低",
       _misjudge_new < _misjudge_old,
       "(新误判 %d/24)" % _misjudge_new)

    # 真·全程静音（无人说话）→ 新判据也必须能归零（不误判'有人说话'）
    o2 = _mic_probe()
    for _ in range(30):
        o2._track_mic_level(_pcm(0.00008))
    ok("B3 全程静音：_mic_disp 归到阈值以下（不会误判'有人说话'）",
       o2._mic_disp < SILENT_TH, "disp=%.5f" % o2._mic_disp)

    # B4: 真人声必须被认出来（反方向：别把"能归零"做成"永远为零"）
    o3 = _mic_probe()
    o3._track_mic_level(_pcm(0.22))
    ok("B4 真人声：_mic_disp 高于阈值且 _mic_voice 置位",
       o3._mic_disp > SILENT_TH and o3._mic_voice is True,
       "disp=%.4f voice=%r" % (o3._mic_disp, o3._mic_voice))
else:
    ok("B0 真实模块导入", False, "（导入失败，场景 B 无法执行）")


# ---------------------------------------------------------------------------
# BUG-3: 运行时档位优先于 config 静态值
#   【2026-11-10 修·同上】旧版 `resolve_engine_ui()` 是 `get_role_voice_context`
#   判定链的手抄副本。现在造一个只带 `_cfg` / `_proactive` 的 CharacterAPI，
#   调真实方法读它返回的 `mode`。
# ---------------------------------------------------------------------------
print()
print("=" * 66)
print("C. 出声方式判定：运行时真相源优先 — 真实实现")
print("=" * 66)

try:
    from src.ui.webapp import CharacterAPI  # noqa: E402
    C_IMPORT_OK = True
except Exception as _e:  # noqa: BLE001
    C_IMPORT_OK = False
    print("  [WARN] 无法导入 CharacterAPI：%r（场景 C 判红）" % _e)


class _Cfg:
    def __init__(self, raw):
        self.raw = raw


class _ProSnap:
    def __init__(self, dm):
        self._dm = dm

    def snapshot(self):
        return {"duplex_mode": self._dm}


def _voice_mode(cfg_dm, rt_dm):
    """调真实 `get_role_voice_context`，返回它算出的 `mode`。"""
    w = CharacterAPI.__new__(CharacterAPI)
    w._cfg = _Cfg({"proactive": {"duplex_mode": cfg_dm}})
    w._proactive = _ProSnap(rt_dm) if rt_dm is not None else None
    return (w.get_role_voice_context() or {}).get("mode")


if C_IMPORT_OK:
    ok("C1 运行时 realtime 覆盖 config local_omni → cloud（不再串档）",
       _voice_mode("local_omni", "realtime") == "cloud")
    ok("C2 运行时 local_omni 覆盖 config realtime → local",
       _voice_mode("realtime", "local_omni") == "local")
    ok("C3 运行时缺失时回退 config（local_omni → local）",
       _voice_mode("local_omni", None) == "local")
    ok("C4 off 语义正确", _voice_mode("local_omni", False) == "off")
else:
    ok("C0 真实模块导入", False, "（导入失败，场景 C 无法执行）")


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

    # D9: 换轮保护 —— 【2026-11-10 加】`_watchdog_tick` 里那句
    #     `and self._mic_proc is proc` 以前**一条判据都没有**：D5/D6 都是
    #     `_mic_proc is proc` 的情形，D7 把判据桩成 False 又不经过它。也就是说
    #     「上一轮的看门狗线程别把新一轮的子进程杀掉」这行守卫，删掉也全绿。
    #     这里造出"看门狗手里拿着旧 proc、实例上已经是新 proc"的真实竞态。
    o = _make_o()
    _old_proc = _FakeProc()
    _new_proc = _FakeProc()
    o._mic_proc = _new_proc                      # 已经换轮
    o._last_blk_ts = _t.time() - 5.0             # 且确实停滞
    o._watchdog_tick(_old_proc)                  # 旧看门狗线程还在跑
    ok("D9 换轮保护：旧看门狗不得杀新一轮的子进程",
       _old_proc.killed is False and _new_proc.killed is False and o._mic_proc is _new_proc,
       "(old.killed=%r new.killed=%r)" % (_old_proc.killed, _new_proc.killed))
else:
    ok("D0 真实模块导入", False, "（导入失败，场景 D 无法执行）")


# ---------------------------------------------------------------------------
print()
print("=" * 66)
print("汇总：PASS=%d  FAIL=%d" % (PASS, FAIL))
print("=" * 66)
sys.exit(0 if FAIL == 0 else 1)
