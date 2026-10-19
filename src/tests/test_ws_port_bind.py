"""WS 端口绑定的两种命运：空闲要能起，被占要说人话（并且绝不静默）。

背景（2026-10-20 03:11 崩溃实锤）：上一个实例退出几秒后重启，bind 8765 直接
OSError 10048，进而触发「初始化失败 → 监督线程跨线程拆 pywebview 窗口 →
fatal COM 0x8001010d」崩溃链，用户侧只剩 crash dump。现在的契约是：
  1. 空闲端口：start() 必须成功，且零重试 warning（重试只属于异常路径）；
  2. 被占端口：先重试 _BIND_ATTEMPTS 次再报错；报错信息必须带端口号、占用者
     PID/进程名、以及「先去托盘退出另一个实例」的指引——不是裸 traceback；
  3. _describe_port_holder：该听的时候有名字（带 PID），不该听的时候闭嘴（空串）。
哪天有人把重试删了、把报错改回裸 OSError、或让占用探测抛异常，这个测试必须红。

运行：.venv/Scripts/python.exe src/tests/test_ws_port_bind.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.event_bus import EventBus  # noqa: E402
from src.ws_server import (  # noqa: E402
    _BIND_ATTEMPTS,
    WebSocketServer,
    _describe_port_holder,
)

TEST_PORT = 18794  # 冷门端口，避开 8765 真服务

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        _fails.append(msg)


class _WarnCatcher(logging.Handler):
    """抓住 start() 期间的所有 warning：空闲路径下必须一条都没有。"""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record.getMessage())


def _listen_blocker(port: int) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.listen(1)
    return s


def test_free_port() -> None:
    """契约 1：空闲端口一次成功，无重试噪声，客户端可连，stop 干净。

    注意：start/连接/stop 必须在**同一个**事件循环里做完——asyncio.run 返回时会
    关掉循环，server 的监听 socket 绑在那个循环上，分段 run 等于把服务杀了。
    """
    catcher = _WarnCatcher()
    logging.getLogger("src.ws_server").addHandler(catcher)

    async def _scenario() -> None:
        import websockets

        srv = WebSocketServer(EventBus(), "127.0.0.1", TEST_PORT)
        await srv.start()
        check(True, "空闲端口 start() 成功")
        check(not catcher.records,
              "空闲路径零 warning（重试只属于异常路径）：%r" % (catcher.records,))
        async with websockets.connect("ws://127.0.0.1:%d" % TEST_PORT):
            pass
        check(True, "客户端可连接并正常关闭")
        await srv.stop()
        check(True, "stop() 正常")

    try:
        asyncio.run(_scenario())
    finally:
        logging.getLogger("src.ws_server").removeHandler(catcher)


def test_occupied_port() -> None:
    """契约 2：被占时先重试再报错，报错说人话（端口/PID/退出指引/data/ 冲突）。"""
    blocker = _listen_blocker(TEST_PORT)
    try:
        srv = WebSocketServer(EventBus(), "127.0.0.1", TEST_PORT)
        t0 = time.time()
        raised = None
        try:
            asyncio.run(srv.start())
        except OSError as e:
            raised = e
        dt = time.time() - t0
        check(raised is not None, "被占端口最终抛 OSError（而不是假启动成功）")
        if raised is None:
            return
        msg = str(raised)
        check(dt >= (_BIND_ATTEMPTS - 1) - 0.5,
              "确有重试（耗时 %.1fs ≥ %ds）" % (dt, _BIND_ATTEMPTS - 1))
        check(str(TEST_PORT) in msg, "报错带端口号")
        check("PID=" in msg, "报错带占用者 PID")
        check("托盘" in msg and "退出" in msg, "报错带「先去托盘退出另一个实例」指引")
        check("data/" in msg, "报错带 data/ 目录冲突说明")
        check(srv._server is None, "失败后未残留半启动的 server 对象")
    finally:
        blocker.close()


def test_holder_desc() -> None:
    """契约 3：占用探测只在该开口时开口。"""
    blocker = _listen_blocker(TEST_PORT)
    try:
        info = _describe_port_holder(TEST_PORT)
        check("PID=" in info and str(TEST_PORT) in info,
              "占用中：带端口号+PID -> %s" % info)
    finally:
        blocker.close()
    check(_describe_port_holder(18799) == "", "空闲时返回空串（不制造噪声）")


def main() -> int:
    print("WS 端口绑定契约（空闲能起 / 被占说人话 / 探测不扰民）")
    test_holder_desc()
    test_free_port()
    test_occupied_port()
    print()
    if _fails:
        print("FAIL：%d 项未通过" % len(_fails))
        for f in _fails:
            print("  -", f)
        return 1
    print("PASS：WS 端口绑定契约全部符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
