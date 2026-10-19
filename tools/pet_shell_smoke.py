"""验证壳桌宠客户端（src/ui/pet_shell.py）——不启动整个应用。

验的是"替换 pywebview 窗口对象后，调用方用的那几个成员是否真的能用"：
    show / hide / is_visible / on_top(读+写) / x / y / move / resize
以及窗口是否真透明（隐藏两次量化背景噪声，再看显示态是否落在噪声内）。

用法：
  .venv/Scripts/python.exe -u tools/pet_shell_smoke.py
  .venv/Scripts/python.exe -u tools/pet_shell_smoke.py --keep
"""
from __future__ import annotations

import base64
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from shell_control import grab  # noqa: E402
from src.ui import pet_shell  # noqa: E402
from src.ui.rpc_server import RpcServer  # noqa: E402

WEB_ROOT = os.path.join(ROOT, "src", "ui", "web")
PSD = os.path.join(WEB_ROOT, "live2d", "models", "model.psd")

calls: list[str] = []
_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        _fails.append(msg)


class StubAPI:
    def ping(self) -> str:
        calls.append("ping")
        return "pong"

    def get_live2d_psd(self) -> str:
        calls.append("get_live2d_psd")
        if not os.path.exists(PSD):
            return ""
        with open(PSD, "rb") as fh:
            return ("data:application/octet-stream;base64,"
                    + base64.b64encode(fh.read()).decode("ascii"))

    def get_live2d_prop(self) -> str:
        return ""


def main() -> int:
    keep = "--keep" in sys.argv
    if "--verbose" in sys.argv:
        import logging

        logging.basicConfig(level=logging.DEBUG,
                            format="%(levelname)s %(name)s: %(message)s")
    if not pet_shell.electron_binary():
        print("[ERR] 找不到 Electron，先执行：cd shell && npm install electron")
        return 1

    rpc = RpcServer(StubAPI())
    info = rpc.start()
    pet_shell.configure(info)
    print(f"RPC：{info['url']}")

    # 每次冒烟用独立 profile：Electron 对同一 userData 目录有单实例限制，
    # 残留实例会让新进程无输出地立刻退出（上一版就是被这个坑住的）。
    import tempfile

    profile = tempfile.mkdtemp(prefix="daniya_pet_profile_")
    client = pet_shell.create_pet_shell(profile_dir=profile)
    if client is None:
        print("[ERR] create_pet_shell 返回 None（看日志里的原因）")
        rpc.stop()
        return 1

    try:
        print("[1] 窗口身份")
        check(client.alive(), "Electron 进程在运行")
        check(client.uid == "pet-shell", f"uid 存在（{client.uid}）")
        check(client.native is None, "native 为 None（pet_hit_rect 会安全跳过）")

        print("[2] 显示 / 隐藏")
        check(not client.is_visible(), "初始是隐藏的（与 pywebview 版一致）")
        client.show()
        time.sleep(1.0)
        check(client.is_visible(), "show() 后可见")
        ready = client.wait_ready(timeout=30)
        check(ready, f"页面渲染就绪（title={client.evaluate_js('document.title')!r}）")
        client.hide()
        time.sleep(0.5)
        check(not client.is_visible(), "hide() 后不可见")
        client.show()
        time.sleep(1.0)

        print("[3] 置顶读写")
        client.show()          # 隐藏态下窗口管理器不一定应用 TopMost，先显示再测
        time.sleep(0.6)
        client.on_top = True
        check(client.on_top is True, "设为置顶后读回 True")
        client.on_top = False
        check(client.on_top is False, "取消后读回 False")

        print("[4] 位置 / 尺寸")
        x0, y0 = client.x, client.y
        print(f"    初始位置 {x0},{y0}")
        check(isinstance(x0, int) and x0 > 0, "x 可读")
        client.move(200, 160)
        time.sleep(0.6)
        x1, y1 = client.x, client.y
        print(f"    移动后 {x1},{y1}")
        check((x1, y1) == (200, 160), f"move 生效（实际 {x1},{y1}）")
        w0, h0 = client.width, client.height
        check(w0 > 100 and h0 > 100, f"宽高可读（{w0}x{h0}）")

        print("[5] 数据链路")
        psd_probe = client.evaluate_js(
            "(async()=>{try{const d=await window.pywebview.api.get_live2d_psd();"
            "return 'LEN:'+String(d).length}catch(e){return 'ERR:'+((e&&e.message)||e)}})()")
        print(f"    页面侧调用结果：{psd_probe!r}")
        check("get_live2d_psd" in calls,
              f"后端收到 RPC 调用（{calls}，页面侧={psd_probe!r}）")

        print("[6] 真透明")
        # 不再只采四个角：角色本体可能正好压在角上（实测踩过，差值 195 是头发不是底色）。
        # 改为整区域逐像素统计：
        #   match  = 与隐藏态同位置像素一致的比例（透明区应占相当一部分）
        #   flat   = 显示态里几乎恒为 #F0F0F0 的比例（WinForms 自绘底色的特征）
        box = (int(x1 * 1.5), int(y1 * 1.5),
               int((x1 + w0) * 1.5), int((y1 + h0) * 1.5))
        client.hide()
        time.sleep(0.6)
        _pts, hidden_img = grab(box)
        client.show()
        time.sleep(1.4)
        _pts2, shown_img = grab(box)
        if not keep:
            shown_img.save(os.path.join(ROOT, "tools", "_pet_shell.png"))

        from PIL import ImageChops

        hb = hidden_img.tobytes()
        sb = shown_img.tobytes()
        total = len(hb) // 3
        match = 0
        flat = 0
        for i in range(0, len(hb), 3):
            hr, hg, hb2 = hb[i], hb[i + 1], hb[i + 2]
            sr, sg, sb2 = sb[i], sb[i + 1], sb[i + 2]
            if abs(sr - hr) <= 8 and abs(sg - hg) <= 8 and abs(sb2 - hb2) <= 8:
                match += 1
            if 236 <= sr <= 244 and 236 <= sg <= 244 and 236 <= sb2 <= 244:
                flat += 1
        match_ratio = match / max(total, 1)
        flat_ratio = flat / max(total, 1)
        print(f"    与背景一致像素 {match_ratio:.1%}；接近 #F0F0F0 像素 {flat_ratio:.1%}")
        # 阈值来自实测：透明窗的匹配率约 30%~70%（角色占其余）；
        # 不透明窗匹配率接近 0，且 flat 接近 100%。
        check(match_ratio > 0.20, "相当比例的像素与桌面一致（窗口没铺底色）")
        check(not (flat_ratio > 0.5 and match_ratio < 0.20), "没有整片 #F0F0F0 底色")
    finally:
        if not keep:
            client.close()
        rpc.stop()

    print()
    if _fails:
        print(f"FAIL：{len(_fails)} 项未通过")
        for f in _fails:
            print("  -", f)
        return 1
    print("PASS：壳桌宠客户端全部可用")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
