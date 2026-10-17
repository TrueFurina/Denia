"""把 CharacterAPI 暴露成本地 RPC —— 换壳（Electron）与 pywebview 共用同一个对象。

为什么要这一层：
    桌宠要真透明就必须换渲染宿主（见 `产品化路线_2026-10-18.md`）。换宿主时最大的坑是
    "前端调用的 window.pywebview.api 没有实现"。与其把几十个方法在壳里重写一遍，
    不如让壳直接调用同一个 Python 对象——所以这里把 CharacterAPI 挂到一个仅本机可见的
    HTTP 端点上，`js_api` 与 RPC 指向**同一个实例**，行为才可能一致。

安全边界（三条都必须成立，缺一条就等于开了后门）：
    1. 只绑 127.0.0.1（端口随机），不对外网暴露；
    2. 每个请求必须带 `X-Daniya-Token`，令牌每次启动随机生成；
    3. 只允许调用 api 对象上的**公开可调用成员**（拒绝下划线开头、拒绝 dunder）。

线程模型：pywebview 的 js_api 本来就是在独立线程里执行（`webview/util.py:247`
的注释与实现），所以从 RPC 线程调用 api 与现有行为属于同一模型，不额外引入跨线程风险。
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

logger = logging.getLogger(__name__)

# 请求体上限：前端可能回传截图/音频的 base64，留 32MB 余量，超出直接拒绝
MAX_BODY = 32 * 1024 * 1024


class RpcError(Exception):
    """业务层错误：会被序列化成 {"ok": false, "error": {...}} 返回给前端。"""

    def __init__(self, message: str, status: int = 400, kind: str = "BadRequest") -> None:
        super().__init__(message)
        self.status = status
        self.kind = kind


def _resolve(api: Any, dotted: str) -> Any:
    """按点号解析成员（与 pywebview 的 get_nested_attribute 一致，支持子对象）。"""
    obj = api
    for part in dotted.split("."):
        if not part:
            return None
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return obj


def _is_exposed(name: str) -> bool:
    """公开成员才允许远程调用——下划线开头一律视为内部实现。"""
    return bool(name) and not name.startswith("_") and "__" not in name


class _Handler(BaseHTTPRequestHandler):
    server_version = "DaniyaRPC/1.0"
    protocol_version = "HTTP/1.1"

    # 由 RpcServer 注入
    api: Any = None
    token: str = ""

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        # 默认实现会把每个请求打到 stderr；这里降级到 debug，避免刷屏
        logger.debug("[RPC] " + fmt, *args)

    # ---------- 响应工具 ----------
    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # 壳的页面来源是另外一个端口/file://，属跨源；令牌才是真正的门禁
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "content-type, x-daniya-token")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        got = self.headers.get("X-Daniya-Token", "")
        # compare_digest：避免按字节比较带来的时序差异
        return bool(self.token) and secrets.compare_digest(got, self.token)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "content-type, x-daniya-token")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?")[0] != "/health":
            self._send_json({"ok": False, "error": {"type": "NotFound",
                                                    "message": "只支持 POST /rpc 与 GET /health"}}, 404)
            return
        self._send_json({"ok": True, "result": {"app": "daniya", "pid": os.getpid()}})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?")[0] != "/rpc":
            self._send_json({"ok": False, "error": {"type": "NotFound",
                                                    "message": "只支持 POST /rpc"}}, 404)
            return
        if not self._authorized():
            self._send_json({"ok": False, "error": {"type": "Unauthorized",
                                                    "message": "缺少或错误的 X-Daniya-Token"}}, 401)
            return
        try:
            payload = self._read_json()
        except RpcError as e:
            self._send_json({"ok": False, "error": {"type": e.kind, "message": str(e)}}, e.status)
            return

        method = str(payload.get("method") or "")
        args = payload.get("args") or []
        kwargs = payload.get("kwargs") or {}
        if not isinstance(args, list) or not isinstance(kwargs, dict):
            self._send_json({"ok": False, "error": {"type": "BadRequest",
                                                    "message": "args 必须是数组、kwargs 必须是对象"}}, 400)
            return
        if not method or not all(_is_exposed(p) for p in method.split(".")):
            self._send_json({"ok": False, "error": {"type": "Forbidden",
                                                    "message": f"方法不可调用：{method!r}"}}, 403)
            return

        target = _resolve(self.api, method)
        if target is None or not callable(target):
            self._send_json({"ok": False, "error": {"type": "NotFound",
                                                    "message": f"方法不存在：{method}"}}, 404)
            return

        t0 = time.time()
        try:
            result = target(*args, **kwargs)
        except Exception as e:  # noqa: BLE001  业务异常必须原样回报，不能吞
            logger.warning("[RPC] %s 抛异常：%s: %s", method, type(e).__name__, e)
            self._send_json({
                "ok": False,
                "error": {"type": type(e).__name__, "message": str(e),
                          "method": method},
            }, 500)
            return

        cost = (time.time() - t0) * 1000
        if cost > 2000:
            logger.info("[RPC] %s 耗时 %.0fms", method, cost)
        # 严格 JSON 优先；遇到不可序列化对象时降级成字符串，并留下告警
        try:
            json.dumps(result)
            self._send_json({"ok": True, "result": result})
        except (TypeError, ValueError):
            json.dumps(result, default=str)
            logger.warning("[RPC] %s 返回值含不可 JSON 化对象，已降级为字符串", method)
            self._send_json({"ok": True, "result": result, "degraded": True})

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise RpcError("Content-Length 非法") from None
        if length <= 0:
            raise RpcError("请求体为空")
        if length > MAX_BODY:
            raise RpcError(f"请求体过大（{length} 字节，上限 {MAX_BODY}）", status=413, kind="TooLarge")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            raise RpcError("请求体不是合法 JSON") from None
        if not isinstance(data, dict):
            raise RpcError("请求体必须是 JSON 对象")
        return data


class _QuietServer(ThreadingHTTPServer):
    """前端被销毁/刷新时连接会被强制关闭，默认实现会打印整段 traceback 刷屏。

    这类断开是正常生命周期的一部分（壳退出、页面刷新），不是服务异常，
    所以只吞 ConnectionReset/BrokenPipe，其他错误照常抛出。
    """

    daemon_threads = True

    def handle_error(self, request, client_address) -> None:  # noqa: D401
        import sys as _sys

        exc = _sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            logger.debug("[RPC] 客户端断开：%s", client_address)
            return
        super().handle_error(request, client_address)


class RpcServer:
    """仅本机可见的 RPC 服务。`start()` 后用 `info()` 拿连接信息。"""

    def __init__(self, api: Any, token: str | None = None,
                 host: str = "127.0.0.1", port: int = 0) -> None:
        self.api = api
        self.token = token or secrets.token_urlsafe(32)
        self._host = host
        self._port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> dict:
        handler = type("_BoundHandler", (_Handler,), {"api": self.api, "token": self.token})
        httpd = _QuietServer((self._host, self._port), handler)
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever,
                                        name="daniya-rpc", daemon=True)
        self._thread.start()
        host, port = httpd.server_address[0], httpd.server_address[1]
        logger.info("[RPC] 已启动 http://%s:%s/rpc（仅本机，需 token）", host, port)
        return {"url": f"http://{host}:{port}/rpc", "host": host, "port": port,
                "token": self.token}

    def info(self) -> dict:
        if not self._httpd:
            return {}
        host, port = self._httpd.server_address[0], self._httpd.server_address[1]
        return {"url": f"http://{host}:{port}/rpc", "host": host, "port": port,
                "token": self.token}

    def stop(self) -> None:
        if self._httpd:
            try:
                self._httpd.shutdown()
            except Exception:  # noqa: BLE001
                pass
            try:
                self._httpd.server_close()
            except Exception:  # noqa: BLE001
                pass
            self._httpd = None


def write_handshake(path: str, info: dict) -> None:
    """把连接信息写给外壳（Electron）。写临时文件再改名，避免外壳读到半截 JSON。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(info, fh, ensure_ascii=False)
    os.replace(tmp, path)
    logger.info("[RPC] 握手文件已写入 %s", path)


__all__ = ["RpcServer", "write_handshake", "MAX_BODY"]
