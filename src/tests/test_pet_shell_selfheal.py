"""桌宠壳的自愈与退出清理测试（阶段 A2 / A3，src/ui/pet_shell.py）。

为什么值得测：这两处写错的后果都是"用户看不懂"型故障——
  · 自愈失效      → 桌宠无声消失，主窗口一切正常，无从下手；
  · 自愈没有配额  → 崩一次就无限 spawn Electron，风扇狂转、日志刷屏；
  · 退出不清理    → 孤儿 Electron 窗口滞留桌面，且下次启动撞单实例限制
                    （Electron 会**无输出地立刻退出**，是本项目最难查的坑之一）；
  · 令牌留在磁盘  → 安全问题。

跑法：.venv/Scripts/python.exe src/tests/test_pet_shell_selfheal.py
（不 spawn 真 Electron：`_launch` 全部替换为假的）
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.ui import pet_shell  # noqa: E402

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        _fails.append(msg)


class _FakeProc:
    """够 `_cleanup_proc` 用的假进程：已退出，且带 pid 供 kill_tree 调用。"""

    def __init__(self, pid: int = 0, rc: int = 0) -> None:
        self.pid = pid
        self._rc = rc

    def poll(self):
        return self._rc


class _LiveProc:
    """活着、但 wait 永远超时的假进程：测「先 terminate，等不到就升级强杀」。"""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.terminated = 0

    def poll(self):
        return None

    def terminate(self) -> None:
        self.terminated += 1

    def wait(self, timeout=None):
        raise TimeoutError("模拟 wait 超时（进程赖着不走）")


class _TopProbe(pet_shell.PetShellClient):
    """把 on_top 换成可数的探针。

    【2026-11-10 加】原来的 [1] 里有个 `calls = {"launch": 0, "show": 0, "top": 0}`
    —— `top` 这个计数器**从头到尾没被 ++ 过**，也没有任何断言读它。想测的东西
    忘了写，但字典留了下来，看起来像测过了。实测：把 `_try_restart` 里的
    `if was_top: self.on_top = True` 整段删掉，本文件**照样全绿 exit 0**。
    """

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.top_calls: list[bool] = []

    @property
    def on_top(self) -> bool:
        return False

    @on_top.setter
    def on_top(self, value: bool) -> None:
        self.top_calls.append(bool(value))


class _RecSock:
    """记录 sendall 的假 socket：测 close() 里那句「先礼后兵」的 quit。"""

    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.closed = False

    def sendall(self, data: bytes) -> None:
        self.sent.append(bytes(data))

    def close(self) -> None:
        self.closed = True


def _make_client(cls=pet_shell.PetShellClient, **kw) -> pet_shell.PetShellClient:
    params = dict(rpc_info={"url": "http://127.0.0.1:1/rpc", "token": "t"},
                 web_base="http://127.0.0.1:1/",
                 x=100, y=200, width=420, height=540)
    params.update(kw)
    return cls(**params)


def main() -> int:
    print("[1] 壳进程意外退出 → 能自愈重启，并恢复生前可见状态")
    client = _make_client(cls=_TopProbe)
    calls = {"launch": 0, "show": 0}
    client._visible = True
    client._topmost = True

    def _fake_launch(timeout: float = 40.0) -> bool:
        calls["launch"] += 1
        return True

    client._launch = _fake_launch

    def _fake_show() -> None:
        calls["show"] += 1
        client._visible = True

    client.show = _fake_show
    ok = client._try_restart()
    check(ok, "_try_restart 返回 True")
    check(calls["launch"] == 1, "确实重新拉起了一次壳进程")
    check(calls["show"] == 1, "生前是显示着的 → 重启后自动恢复显示")
    # 【2026-11-10 加】生前置顶也要恢复。原来这里只有一个从没被 ++ 过的
    # `calls["top"]` 计数器 —— 想测的东西忘了写。
    check(client.top_calls == [True], "生前置顶 → 重启后重新置顶（%r）"
          % (client.top_calls,))

    print("[1b] 生前没置顶 → 重启后不去动置顶状态")
    c1b = _make_client(cls=_TopProbe)
    c1b._launch = lambda *a, **k: True
    c1b._try_restart()
    check(c1b.top_calls == [], "没置顶过就不碰 on_top（%r）" % (c1b.top_calls,))

    print("[2] 反复崩溃 → 配额用尽后停手，不再无限重启")
    client2 = _make_client()
    launches = {"n": 0}

    def _fake_launch2(timeout: float = 40.0) -> bool:
        launches["n"] += 1
        return True

    client2._launch = _fake_launch2
    results = [client2._try_restart() for _ in range(pet_shell.RESTART_MAX + 3)]
    check(results[:pet_shell.RESTART_MAX] == [True] * pet_shell.RESTART_MAX,
          f"前 {pet_shell.RESTART_MAX} 次都尝试重启")
    check(not any(results[pet_shell.RESTART_MAX:]),
          "超过配额后一律放弃（不会无限 spawn）")
    check(launches["n"] == pet_shell.RESTART_MAX,
          f"实际拉起次数 = 配额上限（{launches['n']}）")

    print("[3] 配额按时间窗口滚动：窗口内的算数，窗口外的作废")
    # 场景：很久以前崩过 RESTART_MAX-1 次（已过窗口），现在再崩一次应该还能自愈，
    # 否则一次历史崩溃会永久剥夺自愈能力。
    client3 = _make_client()
    client3._launch = lambda *a, **k: True
    stale = time.time() - (pet_shell.RESTART_WINDOW + 5)
    client3._restarts = [stale] * (pet_shell.RESTART_MAX - 1)
    ok3 = client3._try_restart()
    check(ok3, "窗口外的旧记录被丢弃 → 这次仍能重启")
    check(len(client3._restarts) == 1, f"旧记录已清，只剩本次（{len(client3._restarts)}）")

    print("[4] 主动 close() 之后不复活")
    client4 = _make_client()
    launched = {"n": 0}

    def _fake_launch4(timeout: float = 40.0) -> bool:
        launched["n"] += 1
        return True

    client4._launch = _fake_launch4
    client4.close()
    check(client4._closed, "close() 后 _closed 置位")
    check(not client4._try_restart(), "已关闭的壳不会被看门狗复活")
    check(launched["n"] == 0, "没发生多余 spawn")

    print("[5] 看门狗线程：检测到死亡 → 自愈，配额耗尽后线程自行退出")
    client5 = _make_client()
    hits = {"n": 0}

    def _fake_launch5(timeout: float = 40.0) -> bool:
        hits["n"] += 1
        return True

    client5._launch = _fake_launch5
    old_interval, old_max = pet_shell.WATCHDOG_INTERVAL, pet_shell.RESTART_MAX
    try:
        pet_shell.WATCHDOG_INTERVAL = 0.15
        pet_shell.RESTART_MAX = 2
        client5._closed = False
        client5._start_watchdog()
        t = client5._wd_thread
        t.join(timeout=15)
        check(not t.is_alive(), "配额耗尽后看门狗线程自行退出（不会常驻空转）")
        check(hits["n"] == 2, f"重启次数等于配额上限（{hits['n']}）")
    finally:
        pet_shell.WATCHDOG_INTERVAL, pet_shell.RESTART_MAX = old_interval, old_max
    client5._closed = True
    client5._stop_watchdog()

    print("[6] _cleanup_proc 会清空残留输出队列（防止串读到旧 CONTROL 行）")
    client6 = _make_client()
    client6._lines.put("CONTROL 4444")
    client6._lines.put("SHOWN 1")
    client6._proc = _FakeProc()
    client6._cleanup_proc(force=True)
    check(client6._lines.empty(), "队列已清空")
    check(client6._proc is None, "进程引用已释放")

    # 【2026-11-10 加】杀进程树的三条分支。此前一条判据都没有 ——
    # 把 `elif force: _kill_tree(proc.pid)` 或升级强杀那行删掉，本文件全绿。
    # 而这两处正是「孤儿 Electron 窗口滞留桌面 + 下次启动撞单实例限制」那个坑
    # （Electron 会**无输出地立刻退出**，项目里最难查的一类故障）。
    killed: list[int] = []
    old_kt = pet_shell._kill_tree
    try:
        pet_shell._kill_tree = lambda pid: killed.append(pid)

        c6b = _make_client()
        c6b._proc = _FakeProc(pid=4242)          # 主进程已退（rc=0）
        c6b._cleanup_proc(force=True)
        check(killed == [4242], "主进程已退 + force → 杀子进程树（%r）" % (killed,))

        c6c = _make_client()
        c6c._proc = _FakeProc(pid=4343)
        c6c._cleanup_proc(force=False)
        check(killed == [4242], "force=False 时不越权杀（先礼后兵的分界，%r）"
              % (killed,))

        live = _LiveProc(pid=4444)
        c6d = _make_client()
        c6d._proc = live
        c6d._cleanup_proc(force=True)
        check(live.terminated == 1, "活进程先 terminate（先礼）")
        check(killed == [4242, 4444],
              "wait 等不到 → 升级强杀（后兵，%r）" % (killed,))
    finally:
        pet_shell._kill_tree = old_kt

    print("[7] _kill_tree 对垃圾 PID / 非 Windows 都不抛异常")
    try:
        pet_shell._kill_tree(0)
        pet_shell._kill_tree(999999)
        check(True, "非法 PID 静默忽略（不会把调用方带崩）")
    except Exception as e:  # noqa: BLE001
        check(False, f"_kill_tree 抛异常了：{e}")

    print("[8] 握手文件清理（含令牌，不该留在磁盘上）")
    hs = pet_shell.HANDSHAKE_PATH
    existed = os.path.exists(hs)
    if existed:
        os.replace(hs, hs + ".bak")
    try:
        with open(hs, "w", encoding="utf-8") as fh:
            fh.write('{"token":"secret"}')
        pet_shell._remove_handshake()
        check(not os.path.exists(hs), "握手文件已删除")
        pet_shell._remove_handshake()      # 幂等
        check(True, "重复删除不抛异常（幂等，可安全挂 atexit）")
    finally:
        if os.path.exists(hs):
            os.remove(hs)
        if existed and os.path.exists(hs + ".bak"):
            os.replace(hs + ".bak", hs)

    print("[9] shutdown_all：带走活动壳 + 清令牌文件 + 幂等")
    hs9 = pet_shell.HANDSHAKE_PATH
    existed9 = os.path.exists(hs9)
    if existed9:
        os.replace(hs9, hs9 + ".bak")
    try:
        client9 = _make_client()
        client9._proc = _FakeProc()
        pet_shell._active = client9
        with open(hs9, "w", encoding="utf-8") as fh:
            fh.write('{"token":"secret"}')
        pet_shell.shutdown_all()
        check(pet_shell._active is None, "_active 已清空")
        check(client9._closed, "活动壳已标记关闭")
        check(not os.path.exists(hs9), "收尾后磁盘上不残留含令牌的握手文件")
        pet_shell.shutdown_all()
        pet_shell.shutdown_all()
        check(True, "重复调用 shutdown_all 安全")
    finally:
        if os.path.exists(hs9):
            os.remove(hs9)
        if existed9 and os.path.exists(hs9 + ".bak"):
            os.replace(hs9 + ".bak", hs9)

    print("[10] show/hide 会维护 _visible（自愈恢复状态的依据）")
    client10 = _make_client()

    def _fake_cmd(command: str, expect: str, timeout: float = 10.0) -> str:
        return "SHOWN 1" if expect == "SHOWN" else "HIDDEN 0"

    client10._cmd = _fake_cmd
    client10._refresh_pos = lambda: None
    check(not client10._visible, "初始为隐藏")
    client10.show()
    check(client10._visible, "show() 后置为可见")
    client10.hide()
    check(not client10._visible, "hide() 后置为隐藏")

    print("[11] close() 自己也要清令牌文件（不只靠 shutdown_all 兜底）")
    # 为什么单测这一条：将来若有人直接 `pet_win.close()`（不走 shutdown_all），
    # 令牌就会留在磁盘上。清理必须嵌在最底层，不能只挂在某个收尾函数里。
    hs11 = pet_shell.HANDSHAKE_PATH
    existed11 = os.path.exists(hs11)
    if existed11:
        os.replace(hs11, hs11 + ".bak")
    try:
        client11 = _make_client()
        client11._proc = _FakeProc()
        sock11 = _RecSock()
        client11._sock = sock11
        with open(hs11, "w", encoding="utf-8") as fh:
            fh.write('{"token":"secret"}')
        client11.close()
        check(not os.path.exists(hs11), "close() 后握手文件被删除")
        # 【2026-11-10 加】先礼后兵的第一步：关之前先给壳发一句 quit。
        # 少了它，壳只能被 terminate/kill，Electron 的渲染进程就更容易留成孤儿。
        check(b"quit\n" in sock11.sent, "close() 先发 quit 让壳自己退（%r）"
              % (sock11.sent,))
        check(sock11.closed, "socket 随后被关闭")
    finally:
        if os.path.exists(hs11):
            os.remove(hs11)
        if existed11 and os.path.exists(hs11 + ".bak"):
            os.replace(hs11 + ".bak", hs11)

    print("[12] 降级兜底：壳反复崩溃 → 造 pywebview 窗顶上，且调用方无感")
    fake_win = {"show": 0, "move": 0, "top": None}

    class _Degraded:
        def show(self) -> None:
            fake_win["show"] += 1

        def move(self, x: int, y: int) -> None:
            fake_win["move"] += 1
            fake_win["pos"] = (x, y)

        @property
        def on_top(self) -> bool:
            return bool(fake_win["top"])

        @on_top.setter
        def on_top(self, v: bool) -> None:
            fake_win["top"] = v

    made = {"n": 0}

    def _factory():
        made["n"] += 1
        return _Degraded()

    client12 = _make_client(on_degrade=_factory)
    client12._visible = True
    client12._topmost = True
    # 灌满配额，让下一次 _try_restart 走到"放弃自愈"分支
    now12 = time.time()
    client12._restarts = [now12] * pet_shell.RESTART_MAX
    client12._launch = lambda *a, **k: True
    check(not client12._try_restart(), "配额耗尽 → 放弃自愈")
    check(client12._degraded is not None, "已降级到 pywebview 窗")
    check(fake_win["show"] == 1, "生前可见 → 降级窗自动 show")
    check(fake_win["top"] is True, "生前置顶 → 降级窗保持置顶")
    check(client12.alive(), "降级后 alive() 仍为 True（窗口还在）")
    client12.show()
    check(fake_win["show"] == 2, "后续 show() 透明转发到降级窗")
    client12._degrade()
    check(made["n"] == 1, f"降级只发生一次（回调被调 {made['n']} 次）")

    print("[13] 没有降级回调时，放弃自愈只是不再重启（不炸）")
    client13 = _make_client()
    client13._launch = lambda *a, **k: True
    client13._restarts = [time.time()] * pet_shell.RESTART_MAX
    check(not client13._try_restart(), "无回调也能安全放弃")
    check(client13._degraded is None, "没有凭空造出降级窗")
    check(not client13.alive(), "无进程且未降级 → alive() False")

    print("[14] 单次重启失败 ≠ 终局：看门狗继续重试，配额耗尽才降级")
    # 回归：以前 `_watchdog_loop` 写的是 `if not self._try_restart(): return`，
    # 而 `_try_restart` 在"这次没起来"时也返回 False —— 于是一次失败就把
    # 看门狗弄死，既不重试也不降级，桌宠静默消失（2026-10-18 质检 P1）。
    client14 = _make_client(on_degrade=_factory)
    tries = {"n": 0}

    def _fail_launch(timeout: float = 40.0) -> bool:
        tries["n"] += 1
        return False

    client14._launch = _fail_launch
    old_i, old_m, old_b = (pet_shell.WATCHDOG_INTERVAL, pet_shell.RESTART_MAX,
                           pet_shell.RESTART_BACKOFF)
    try:
        pet_shell.RESTART_BACKOFF = 0          # 测试里别真睡 1.5s
        # ① 单次失败：返回 False，但**不是**终局（这一条是确定性的，不靠线程时序）
        check(not client14._try_restart(), "这次没起来 → 返回 False")
        check(client14._give_up is False, "但 _give_up 仍为 False（可重试，留给下一轮）")
        check(len(client14._restarts) == 1, "失败也计入配额（防止无限 spawn）")

        # ② 看门狗从零跑满配额：必须**每次都试**，而不是第一次失败就收工。
        #    从零开始是刻意的：带着上面那次直调去数，修好与没修好的次数会一样，
        #    这条断言就废了（第一版就写成那样，变异没抓住）。
        client14._restarts = []
        pet_shell.WATCHDOG_INTERVAL, pet_shell.RESTART_MAX = 0.1, 3
        client14._start_watchdog()
        t14 = client14._wd_thread
        t14.join(timeout=15)
        check(tries["n"] == 4,
              f"1 次直调 + 看门狗 3 次重试（实际 {tries['n']}；"
              f"旧代码只会是 2）")
        check(not t14.is_alive(), "配额耗尽后看门狗才收工")
        check(client14._degraded is not None, "放弃时降级到 pywebview（人还在）")
    finally:
        pet_shell.WATCHDOG_INTERVAL, pet_shell.RESTART_MAX = old_i, old_m
        pet_shell.RESTART_BACKOFF = old_b
        client14._closed = True
        client14._stop_watchdog()

    print("[15] GPU 崩溃 → 自动带软件合成开关重试一次（而不是直接把壳判死）")
    # 这一条必须跑**真的** _launch（不是替换成假的），否则测不到开关是否真的加上了。
    # 只把最外层三个副作用换掉：Popen / create_connection / 握手文件路径。
    import gc
    import tempfile

    # 前面用例建的客户端可能还有看门狗在跑，它们也会调 _launch，会污染计数。
    for _obj in gc.get_objects():
        if isinstance(_obj, pet_shell.PetShellClient):
            _obj._closed = True
            try:
                _obj._stop_watchdog()
            except Exception:  # noqa: BLE001
                pass
    time.sleep(0.4)

    launches: list[list[str]] = []

    class _FakeStdout:
        def __init__(self, lines: list[str]) -> None:
            self._lines = lines

        def __iter__(self):
            return iter(self._lines)

    class _FakePopen:
        def __init__(self, args, **kw) -> None:
            launches.append(list(args))
            self.args = args
            self.pid = 9000 + len(launches)
            first = len(launches) == 1
            if first:
                # 第一次：复刻本机真实的 GPU 崩溃尾巴，进程随即退出
                self.stdout = _FakeStdout([
                    "ERROR: GPU process exited unexpectedly: exit_code=-1073741819\n",
                    "FATAL: GPU process isn't usable. Goodbye.\n",
                ])
                self._rc = -2147483645          # 0x80000003
            else:
                self.stdout = _FakeStdout(["READY\n", "CONTROL 4321\n"])
                self._rc = None

        def poll(self):
            return self._rc

    class _FakeSock:
        def settimeout(self, _t):
            pass

        def close(self):
            pass

    old_popen = pet_shell.subprocess.Popen
    old_conn = pet_shell.socket.create_connection
    old_hs = pet_shell.HANDSHAKE_PATH
    old_rm = pet_shell._remove_handshake
    old_bin = pet_shell.electron_binary
    try:
        pet_shell.subprocess.Popen = _FakePopen
        pet_shell.socket.create_connection = lambda *a, **k: _FakeSock()
        pet_shell.HANDSHAKE_PATH = os.path.join(tempfile.gettempdir(),
                                                "daniya_test_hs.json")
        pet_shell._remove_handshake = lambda: None
        pet_shell.electron_binary = lambda: "C:/fake/electron.exe"
        # 给这个客户端一个独有的标题：用它在 launches 里筛出"我自己起的进程"，
        # 免得别的客户端（或残留看门狗）混进来把计数带偏。
        tag = "QC-GPU-FALLBACK-15"
        client15 = _make_client(title=tag)
        ok15 = client15._launch(timeout=3)
        mine = [a for a in launches if tag in a]
        launches[:] = mine
        check(ok15, "带开关重试后握手成功")
        check(len(launches) == 2, f"一共起了 2 次（实际 {len(launches)}）")
        check(bool(launches) and not any(f in launches[0]
                                         for f in pet_shell.GPU_FALLBACK_FLAGS),
              "第一次按原样启动（不白吃软件合成的性能损失）")
        check(len(launches) >= 2 and all(f in launches[1]
                                         for f in pet_shell.GPU_FALLBACK_FLAGS),
              "第二次带上了 GPU 回退开关")
        check(client15._gpu_fallback, "回退状态被记住（后续重启不用再白崩一次）")
        client15._closed = True
    finally:
        pet_shell.subprocess.Popen = old_popen
        pet_shell.socket.create_connection = old_conn
        pet_shell.HANDSHAKE_PATH = old_hs
        pet_shell._remove_handshake = old_rm
        pet_shell.electron_binary = old_bin

    print("[16] 连续崩溃要退避：第一次不白等，第二次起等 RESTART_BACKOFF")
    # 【2026-11-10 加】`_try_restart` 里 `if len(self._restarts) > 1:
    # time.sleep(RESTART_BACKOFF)` 此前零判据 —— 删掉它本文件照样全绿。
    # 而它的作用正是「一个必然崩的配置不要把 CPU 打满」（模块注释原话）。
    # 只断言下界（sleep 至少睡够），不赌上界，避免在慢机器上抖。
    old_bk = pet_shell.RESTART_BACKOFF
    try:
        pet_shell.RESTART_BACKOFF = 0.35
        c16 = _make_client()
        c16._launch = lambda *a, **k: True
        t0 = time.time()
        c16._try_restart()
        d1 = time.time() - t0
        t0 = time.time()
        c16._try_restart()
        d2 = time.time() - t0
        check(d1 < 0.35, "第一次重启不白等（%.3fs < 0.35s）" % d1)
        check(d2 >= 0.35, "第二次起退避生效（%.3fs ≥ 0.35s）" % d2)
    finally:
        pet_shell.RESTART_BACKOFF = old_bk

    print()
    if _fails:
        print(f"❌ FAIL：{len(_fails)} 项")
        for f in _fails:
            print("   - " + f)
        return 1
    print("✅ 全部通过（桌宠壳自愈 / 退出清理）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
