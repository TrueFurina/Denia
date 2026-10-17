"""RPC 通道与 js_api 通道的等价性 + 安全边界测试。

验收标准（来自产品化路线第 1 步）：
  同一个 CharacterAPI 实例，两条通道调用同一批方法必须返回**完全相同**的结果。
  此外三条安全边界必须成立：只绑回环、缺令牌拒绝、内部方法不可调用。

运行：.venv/Scripts/python.exe src/tests/test_rpc_server.py
"""
from __future__ import annotations

import json
import os
import socket
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.ui import rpc_server  # noqa: E402
from src.ui.rpc_server import RpcServer  # noqa: E402

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        _fails.append(msg)


class FakeAPI:
    """故意覆盖各种返回类型：字符串、数字、布尔、None、嵌套、中文、长 base64。"""

    def __init__(self) -> None:
        self._secret = "不应被外部调用"

    # --- 20 个公开方法（模拟真实 CharacterAPI 的形态） ---
    def ping(self) -> str:
        return "pong"

    def get_live2d_psd(self) -> str:
        return "data:application/octet-stream;base64," + "A" * 4096

    def get_live2d_prop(self) -> str:
        return "prop_bowl.png"

    def get_status(self) -> dict:
        return {"mood": "正常", "energy": 87, "busy": False, "tags": ["a", "b"]}

    def get_count(self) -> int:
        return 42

    def get_ratio(self) -> float:
        return 0.6180339887

    def is_ready(self) -> bool:
        return True

    def get_nothing(self) -> None:
        return None

    def echo(self, text: str = "") -> str:
        return text

    def add(self, a: int = 0, b: int = 0) -> int:
        return a + b

    def join(self, items: list | None = None, sep: str = "-") -> str:
        return sep.join(str(x) for x in (items or []))

    def nested(self, payload: dict | None = None) -> dict:
        return {"got": payload, "len": len(payload or {})}

    def unicode_echo(self, text: str = "") -> str:
        return "回显：" + text

    def big_list(self) -> list:
        return list(range(500))

    def nested_object(self) -> dict:
        return {"a": {"b": {"c": [1, 2, {"d": "达妮娅"}]}}}

    def bool_arg(self, flag: bool = False) -> bool:
        return not flag

    def kwargs_only(self, **kw) -> dict:
        return kw

    def many_args(self, a=1, b=2, c=3, d=4, e=5) -> int:
        return a + b + c + d + e

    def raises(self) -> str:
        raise ValueError("这是预期内的失败")

    def returns_object(self) -> object:
        return object()  # 故意不可 JSON 化，用于验证降级路径

    def _private(self) -> str:
        return "不该被看到"


def call(info: dict, method: str, args=None, kwargs=None, token: str | None = None):
    body = json.dumps({"method": method, "args": args or [], "kwargs": kwargs or {}}).encode()
    req = urllib.request.Request(info["url"], data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if token is not False:
        req.add_header("X-Daniya-Token", token if token is not None else info["token"])
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


def get(info: dict, path: str):
    url = info["url"].replace("/rpc", path)
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, {}


def test_parity(api: FakeAPI, info: dict) -> None:
    print("[1] 通道等价性（RPC 结果 == 直接调用结果）")
    cases = [
        ("ping", [], {}),
        ("get_live2d_psd", [], {}),
        ("get_live2d_prop", [], {}),
        ("get_status", [], {}),
        ("get_count", [], {}),
        ("get_ratio", [], {}),
        ("is_ready", [], {}),
        ("get_nothing", [], {}),
        ("echo", ["你好"], {}),
        ("add", [3, 4], {}),
        ("add", [], {"a": 10, "b": 20}),
        ("join", [["x", "y", "z"]], {}),
        ("nested", [{"k": "v"}], {}),
        ("unicode_echo", ["达妮娅"], {}),
        ("big_list", [], {}),
        ("nested_object", [], {}),
        ("bool_arg", [True], {}),
        ("kwargs_only", [], {"x": 1, "y": "二"}),
        ("many_args", [1, 2, 3, 4, 5], {}),
    ]
    bad = 0
    for method, args, kwargs in cases:
        try:
            direct = getattr(api, method)(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            direct = {"__exception__": type(e).__name__}
        status, payload = call(info, method, args, kwargs)
        same = status == 200 and payload.get("ok") is True and payload.get("result") == direct
        if not same:
            bad += 1
            print(f"    差异 {method}{args}{kwargs}: RPC={payload.get('result')!r} 直调={direct!r}")
    check(bad == 0, f"{len(cases)} 个方法两条通道结果完全一致（差异 {bad} 个）")


def test_security(info: dict) -> None:
    print("[2] 安全边界")
    status, _ = call(info, "ping", token=False)
    check(status == 401, f"缺令牌被拒绝（{status}）")
    status, _ = call(info, "ping", token="wrong-token-xxx")
    check(status == 401, f"错令牌被拒绝（{status}）")
    status, payload = call(info, "_private")
    check(status in (403, 404), f"下划线方法不可调用（{status}）")
    status, payload = call(info, "__init__")
    check(status in (403, 404), f"dunder 不可调用（{status}）")
    status, _ = call(info, "no_such_method")
    check(status == 404, f"不存在的方法返回 404（{status}）")

    host = info["host"]
    check(host in ("127.0.0.1", "localhost"), f"只绑回环地址（{host}）")


def test_http_shape(info: dict) -> None:
    print("[3] 协议形态")
    status, payload = get(info, "/health")
    check(status == 200 and payload.get("ok") is True, "GET /health 可用")
    status, payload = get(info, "/nope")
    check(status == 404, "未知路径 404")

    # OPTIONS 预检（跨源 fetch 需要）
    req = urllib.request.Request(info["url"], method="OPTIONS")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            check(resp.status == 204, f"OPTIONS 预检返回 {resp.status}")
    except urllib.error.HTTPError as e:
        check(False, f"OPTIONS 预检失败（{e.code}）")

    status, payload = call(info, "raises")
    check(status == 500 and payload.get("error", {}).get("type") == "ValueError",
          "业务异常回 500 且保留异常类型")

    status, payload = call(info, "returns_object")
    check(status == 200 and payload.get("degraded") is True
          and isinstance(payload.get("result"), str),
          "不可 JSON 化返回值降级为字符串并标记 degraded")

    # 请求体过大：临时压低上限，避免真的传 32MB
    old = rpc_server.MAX_BODY
    try:
        rpc_server.MAX_BODY = 64
        status, _ = call(info, "echo", ["x" * 200])
        check(status == 413, f"超大请求体被拒绝（{status}）")
    finally:
        rpc_server.MAX_BODY = old


def main() -> int:
    api = FakeAPI()
    srv = RpcServer(api)
    info = srv.start()
    print(f"RPC 已启动：{info['url']}\n")
    try:
        test_parity(api, info)
        test_security(info)
        test_http_shape(info)
    finally:
        srv.stop()

    # 停掉后端口必须真的关掉，否则换壳时会出现"端口占用"误判
    print("[4] 关闭")
    with socket.socket() as s:
        alive = s.connect_ex((info["host"], info["port"])) == 0
    check(not alive, "stop() 后端口已关闭")

    print()
    if _fails:
        print(f"FAIL：{len(_fails)} 项未通过")
        for f in _fails:
            print("  -", f)
        return 1
    print("PASS：RPC 通道与安全边界全部符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
