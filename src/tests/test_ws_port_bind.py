"""WS 端口绑定的两种命运：空闲要能起，被占要说人话（并且绝不静默）。

背景（2026-10-20 03:11 崩溃实锤）：上一个实例退出几秒后重启，bind 8765 直接
OSError 10048，进而触发「初始化失败 → 监督线程跨线程拆 pywebview 窗口 →
fatal COM 0x8001010d」崩溃链，用户侧只剩 crash dump。现在的契约是：
  1. 空闲端口：start() 必须成功，且零重试 warning（重试只属于异常路径）；
  2. 被占端口：先重试 _BIND_ATTEMPTS 次再报错；报错信息必须带端口号、占用者
     PID/进程名、以及「先去托盘退出另一个实例」的指引——不是裸 traceback；
     每次重试都要在日志里留痕（否则用户事后翻日志看不到「试过 6 次」）；
  3. _describe_port_holder：该听的时候有名字（带 PID），不该听的时候闭嘴（空串）；
  4. **端口中途释放**（2026-10-20 崩溃的真实形状）：占用者 1~2s 后退出，重试必须
     真的**重新 bind** 并成功起来。
哪天有人把重试删了、把报错改回裸 OSError、或让占用探测抛异常，这个测试必须红。

【2026-11-09 加·契约 2 的日志判据 + 契约 4】原文件只测「一直空闲」和「一直被占」
两端，中间那一段——旧实例正在退出、1~2s 后释放端口——从来没测过。实测两处空转：
  · 把 `logger.warning(...)` 整段删掉，本文件**照样全绿 exit 0**（重试静默无声）；
  · 把 `serve()` 提到循环外（只真的 bind 一次，然后空睡 5 次再抛同样的错），
    本文件**照样全绿**——耗时 5.0s 骗过了「确有重试」，而那个 bug 一点没修。
第二条是致命的：闸门测的「重试」只是「等了 5 秒」，不是「又 bind 了 5 次」。

运行：.venv/Scripts/python.exe src/tests/test_ws_port_bind.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.event_bus import EventBus  # noqa: E402
from src.ws_server import (  # noqa: E402
    _BIND_ATTEMPTS,
    WebSocketServer,
    _describe_port_holder,
    logger as _ws_logger,
)

TEST_PORT = 18794  # 冷门端口，避开 8765 真服务

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        _fails.append(msg)


class _WarnCatcher(logging.Handler):
    """抓住 start() 期间的所有 warning：空闲路径下必须一条都没有。

    【2026-11-09】挂的是**模块自己的 logger 对象**（`_ws_logger`），不是
    `logging.getLogger("src.ws_server")` 这个字符串名。字符串名一改（模块挪目录、
    logger 改名）判据就静默变空转——「零 warning」永远是绿的，因为根本没人往
    这个 handler 里塞东西。用对象就没有「名字对不上」这回事。
    """

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
    _ws_logger.addHandler(catcher)

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
        _ws_logger.removeHandler(catcher)


def test_occupied_port() -> None:
    """契约 2：被占时先重试再报错，报错说人话（端口/PID/退出指引/data/ 冲突）。"""
    blocker = _listen_blocker(TEST_PORT)
    catcher = _WarnCatcher()
    _ws_logger.addHandler(catcher)
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
        # 【2026-11-09 加】系统错误串与人工指引之间必须有句读。原来 rstrip("。")
        # 把句号吃掉不再补，实测拼出「…只允许使用一次若这是第二个达妮娅实例…」，
        # 崩溃弹窗里就是一整句读不通的话。
        #
        # 注意**不能**直接判 `"。若" in msg`：holder 自己就带句号
        # （「端口 18794 正被进程 PID=… 占用。」），拼在「若这是」前面，
        # 于是整条消息永远有「。若」—— 那条断言恒真，是第二份真相。
        # 必须把 last_err 那一段单独切出来看。
        marker = "端口 %s 正被" % TEST_PORT
        head = msg.split("次）：", 1)[-1].split(marker, 1)[0]
        check(marker in msg and head.rstrip().endswith("。"),
              "系统错误与人工指引之间有句读（不是「…一次若这是…」粘在一起）：…%s"
              % head[-14:])
        check(srv._server is None, "失败后未残留半启动的 server 对象")
        # 【2026-11-09 加】重试不能静默：用户事后翻日志要能数出「试了几次」。
        # 原文件对这一段零判据——把 logger.warning 整段删掉照样全绿（实测）。
        check(len(catcher.records) >= _BIND_ATTEMPTS - 1,
              "每次重试都留了日志（%d 条 ≥ %d）"
              % (len(catcher.records), _BIND_ATTEMPTS - 1))
        joined = " ".join(catcher.records)
        check(str(TEST_PORT) in joined and "重试" in joined,
              "重试日志带端口号 + 「重试」字样")
    finally:
        _ws_logger.removeHandler(catcher)
        blocker.close()


def test_retry_then_success() -> None:
    """契约 4：端口中途释放时，重试必须**真的重新 bind** 并起来。

    【2026-11-09 加·这才是 2026-10-20 那个 bug 的形状】旧实例退出不是「一直占着」，
    而是「1~2s 后释放」。原测试只有两端（一直空闲 / 一直被占），中间这段空白，
    于是「只 bind 一次 + 空睡 5 秒 + 抛同样的错」也能全绿——耗时断言数的是**等待**，
    不是**重绑次数**。这里用一个 1.2s 的定时器把监听 socket 关掉，模拟旧实例退出。
    """
    blocker = _listen_blocker(TEST_PORT)
    catcher = _WarnCatcher()
    _ws_logger.addHandler(catcher)
    timer = threading.Timer(1.2, blocker.close)
    srv = WebSocketServer(EventBus(), "127.0.0.1", TEST_PORT)

    async def _start_and_stop() -> None:
        # start/stop 必须同一个事件循环（见 test_free_port 的注释）
        await srv.start()
        await srv.stop()

    t0 = time.time()
    err = None
    timer.start()
    try:
        asyncio.run(_start_and_stop())
    except OSError as e:
        err = e
    finally:
        timer.cancel()
        blocker.close()
        _ws_logger.removeHandler(catcher)
    dt = time.time() - t0
    check(err is None, "端口中途释放后 start() 成功（重试真的重绑了）：%r"
          % (err,))
    if err is not None:
        return
    check(len(catcher.records) >= 1,
          "确实经历了至少一次重试 warning（%d 条）" % len(catcher.records))
    check(dt < (_BIND_ATTEMPTS - 1),
          "没走满 %d 次预算就起来了（%.1fs < %ds）"
          % (_BIND_ATTEMPTS, dt, _BIND_ATTEMPTS - 1))


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
    print("WS 端口绑定契约（空闲能起 / 被占说人话 / 中途释放能重绑 / 探测不扰民）")
    test_holder_desc()
    test_free_port()
    test_occupied_port()
    test_retry_then_success()
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
