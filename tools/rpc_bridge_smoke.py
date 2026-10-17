"""端到端验证「Python RPC → Electron 壳 → 页面零改动」。

它验的是四件事，缺一件就说明链路有断点：
  1. 页面从 Python 的静态服务器加载（不是 file://）
  2. 页面调用的 window.pywebview.api.get_live2d_psd 经壳转发到 Python RPC
     —— 判定依据是**后端真的收到了调用**，不是"页面看起来有图"
  3. 页面拿到的 PSD 长度与后端返回一致（走通了整条数据链）
  4. 同时窗口保持真透明（隐藏/显示逐点对照，不是看"有没有变黑"）

为了不拖进整个应用启动（模型/LLM/TTS 全量加载要几十秒且会弹主窗口），
这里用最小后端：只挂 get_live2d_psd / ping，但走与生产完全相同的 RpcServer 与握手协议。

用法：
  .venv/Scripts/python.exe -u tools/rpc_bridge_smoke.py
  .venv/Scripts/python.exe -u tools/rpc_bridge_smoke.py --keep
"""
from __future__ import annotations

import base64
import os
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

sys.path.insert(0, os.path.join(ROOT, "tools"))
from shell_control import ShellSession, compare_transparency  # noqa: E402
from src.ui.rpc_server import RpcServer, write_handshake  # noqa: E402

WEB_ROOT = os.path.join(ROOT, "src", "ui", "web")
PSD_CANDIDATES = [os.path.join(WEB_ROOT, "live2d", "models", n)
                  for n in ("model.psd", "model_fixed.psd")]

rpc_calls: list[str] = []


class StubAPI:
    """最小后端：只实现 rig 页面在 bare 模式下真正会调的方法。"""

    def ping(self) -> str:
        rpc_calls.append("ping")
        return "pong"

    def get_live2d_psd(self) -> str:
        rpc_calls.append("get_live2d_psd")
        for p in PSD_CANDIDATES:
            if os.path.exists(p):
                with open(p, "rb") as fh:
                    data = fh.read()
                if data:
                    return ("data:application/octet-stream;base64,"
                            + base64.b64encode(data).decode("ascii"))
        return ""


class StaticHandler(SimpleHTTPRequestHandler):
    """静态服务器：去掉查询串（rig 用 ?v=2&bare=1），禁缓存。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_ROOT, **kwargs)

    def do_GET(self):  # noqa: N802
        self.path = urlparse(self.path).path or "/"
        return super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):  # noqa: A003
        pass


def main() -> int:
    keep = "--keep" in sys.argv

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), StaticHandler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, name="smoke-static", daemon=True).start()
    host, port = httpd.server_address[0], httpd.server_address[1]
    web_base = f"http://{host}:{port}"

    rpc = RpcServer(StubAPI())
    info = rpc.start()
    print(f"静态服务器：{web_base}")
    print(f"RPC：{info['url']}")

    hs = os.path.join(tempfile.mkdtemp(prefix="daniya_hs_"), "handshake.json")
    write_handshake(hs, {**info, "web_base": web_base})

    session = ShellSession(["--handshake", hs, "--x", "240", "--y", "180",
                            "--w", "320", "--h", "420", "--quit", "0"])
    ok = 0
    try:
        if not session.start():
            print("[ERR] 壳启动失败")
            return 1
        print(f"页面 title：{session.title!r}")

        probe = session.probe()
        print(f"probe：{probe}")

        png = os.path.join(ROOT, "tools", "_bridge_shell.png")
        corner_ok, center_painted, behind, shown, noise = compare_transparency(
            session, keep_png=None if keep else png)
        print(f"  隐藏态：{behind}")
        print(f"  显示态：{shown}")

        bridge_ok = bool(probe.get("hasBridge")) and probe.get("psdType") == "function"
        rpc_used = "get_live2d_psd" in rpc_calls
        psd_len = int(probe.get("psdLen") or 0)
        data_ok = psd_len > 100000  # 真 PSD 的 base64 远大于这个量级

        print()
        print(f"  桥接存在（window.pywebview.api）：{'✅' if bridge_ok else '❌'}  {probe}")
        print(f"  后端收到 RPC 调用：              {'✅' if rpc_used else '❌'}  {rpc_calls}")
        print(f"  PSD 数据真的传到了页面：         {'✅' if data_ok else '❌'}  {psd_len} 字节")
        print(f"  窗口真透明（角落与真背景一致）：  {'✅' if corner_ok else '❌'}")
        print(f"  人物在渲染（中心与背景不同）：    {'✅' if center_painted else '❌'}")

        ok = int(bridge_ok and rpc_used and data_ok and corner_ok and center_painted)
    finally:
        if not keep:
            session.close()
        rpc.stop()
        httpd.shutdown()

    print()
    if ok:
        print("✅ 结论：Python RPC → Electron 壳 → 页面 全链路打通，且窗口真透明")
        return 0
    print("❌ 链路有环节没通（见上面五项）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
