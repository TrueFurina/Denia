"""端到端验证桌宠壳的**自愈**（阶段 A2）——会真启动真杀掉 Electron。

单元测试 `src/tests/test_pet_shell_selfheal.py` 把 `_launch` 换成了假的，
所以它抓不到这一类回归：
    · 旧进程被杀后，同一个 userData 目录还能不能再起一个 Electron
      （Electron 有单实例限制，残留锁会让新进程**无输出地立刻退出**）；
    · 重启后窗口能不能自动恢复到"生前是显示着的"；
    · `_cleanup_proc → _launch` 这条真实组合路径通不通。

这几条恰恰是"桌宠会消失"的真身，所以必须拿真进程验一遍。

用法：
  .venv/Scripts/python.exe -u tools/pet_shell_selfheal_smoke.py
  .venv/Scripts/python.exe -u tools/pet_shell_selfheal_smoke.py --verbose
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.ui import pet_shell  # noqa: E402
from src.ui.rpc_server import RpcServer  # noqa: E402

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        _fails.append(msg)


class StubAPI:
    """只提供页面要用的最小接口。"""

    def ping(self) -> str:
        return "pong"

    def get_live2d_psd(self) -> str:
        return ""

    def get_live2d_prop(self) -> str:
        return ""


def _wait(cond, timeout: float, label: str) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if cond():
                return True
        except Exception:  # noqa: BLE001  轮询期间任何瞬时异常都不算失败
            pass
        time.sleep(0.3)
    print(f"    （等待超时：{label}）")
    return False


SKIP_RC = 77   # 约定：编排层（check_all.py）把 77 当「跳过」，绝不当「通过」


def shell_required() -> bool:
    """壳是不是「产品承诺」——决定本机起不来时该红还是该跳过。

    判据（任一成立即视为必需）：
      · 环境变量 `DANIYA_REQUIRE_SHELL=1`
      · `config.yaml` 的 `ui.pet_backend: shell`

    默认 `webview` 下壳只是实验特性。让整个门禁长期红着的结果是
    **所有人都学会忽略红色**（本项目已经吃过这个教训），所以这里分成
    「必需 → 红」与「非必需 → 跳过且写明原因」。跳过的原因会由
    check_all.py 原样打印出来，不会变成静默通过。
    """
    if os.environ.get("DANIYA_REQUIRE_SHELL", "").strip().lower() in (
            "1", "true", "yes", "on"):
        return True
    try:
        from src.config import load_config
        from src.ui.pet_backend import from_config, normalize

        cfg = load_config(os.path.join(ROOT, "config.yaml"))
        return normalize(from_config(cfg), source="smoke") == "shell"
    except Exception:  # noqa: BLE001  读不到配置就按「非必需」处理
        return False


def _skip(reason: str) -> int:
    print(f"[SKIP] {reason}")
    print("       这不是通过：端到端自愈**本轮未验证**。")
    print("       要让它当红项：设 DANIYA_REQUIRE_SHELL=1，"
          "或把 ui.pet_backend 设为 shell。")
    return SKIP_RC


def main() -> int:
    if "--verbose" in sys.argv:
        import logging

        logging.basicConfig(level=logging.DEBUG,
                            format="%(levelname)s %(name)s: %(message)s")

    required = shell_required()
    if not pet_shell.electron_binary():
        if required:
            print("[ERR] 找不到 Electron，先执行：cd shell && npm install electron")
            return 1
        return _skip("找不到 Electron（shell/node_modules/electron/dist/）")

    rpc = RpcServer(StubAPI())
    info = rpc.start()
    pet_shell.configure(info)
    print(f"[*] RPC: {info.get('url')}")

    ok = True
    try:
        print("[1] 启动壳桌宠并显示")
        client = pet_shell.create_pet_shell()
        if client is None:
            # 关键分叉：配置要求 shell → 承诺没兑现，红；否则跳过并说明原因。
            if required:
                check(False, "壳创建成功（配置要求 shell，起不来就是失败）")
                return 1
            return _skip("壳在本机起不来（rc / FATAL 行见上方日志）")
        check(True, "壳创建成功")
        check(client.alive(), "Electron 进程活着")
        client.show()
        check(client.wait_ready(40), "页面渲染就绪")
        check(client.is_visible(), "窗口可见")

        old_pid = client._proc.pid if client._proc else 0
        print(f"    进程 PID = {old_pid}")

        print("[2] 模拟崩溃：强杀整个进程树")
        try:
            client._proc.kill()          # 模拟被杀 / 崩溃，不走 close()
        except Exception as e:  # noqa: BLE001
            check(False, f"kill 失败：{e}")
            return 1
        check(_wait(lambda: not client.alive(), 10, "进程退出"),
               "进程确实死了（看门狗该介入了）")

        print("[3] 等看门狗自愈拉起（含 4MB PSD 解析，给足时间）")
        healed = _wait(lambda: client.restart_count() >= 1 and client.alive(),
                       90, "自愈重启完成")
        check(healed, "看门狗把壳重新拉起来了")
        new_pid = client._proc.pid if client._proc else 0
        check(new_pid and new_pid != old_pid, f"换成了新进程（{old_pid} → {new_pid}）")
        check(client.wait_ready(40), "重启后页面重新渲染就绪")
        check(client.is_visible(), "生前是显示着的 → 自愈后自动恢复可见")

        print("[4] 主动关闭后不应复活")
        client.close()
        check(not client.alive(), "进程已退出")
        before = client.restart_count()
        time.sleep(3)     # 超过一个看门狗周期
        check(client.restart_count() == before, "没有多余的重启（不会诈尸）")
        check(not os.path.exists(pet_shell.HANDSHAKE_PATH),
              "握手文件已清理（不含令牌残留）")
    finally:
        try:
            pet_shell.shutdown_all()
        except Exception:  # noqa: BLE001
            pass
        rpc.stop()

    print()
    if _fails:
        print(f"FAIL：{len(_fails)} 项未通过")
        for f in _fails:
            print("  -", f)
        return 1
    print("PASS：桌宠壳自愈端到端成立（真进程崩溃 → 自动恢复）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
