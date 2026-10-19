# -*- coding: utf-8 -*-
"""【阶段 E · 开机自启开关】src/autostart.py 的行为规格。

策略：
- 注册表操作走 winreg（HKCU\\...\\Run，每用户、无 UAC），测试用真实键但
  值名固定为 _DaniaAutostartTest（不碰真键 DaniaAI），finally 必清。
- root 一律显式注入（测试与生产共用同一入口，不依赖 sys 状态）。
- command 断言不 hardcode 全路径，只断言结构性要素（pythonw.exe、main.py、引号）。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src import autostart

TEST_VALUE = "_DaniaAutostartTest"
_checks: list[tuple[bool, str]] = []


def check(ok: bool, msg: str) -> None:
    _checks.append((ok, msg))
    print(("  ✅ " if ok else "  ✗ ") + msg)


def main() -> int:
    root = tempfile.mkdtemp(prefix="autostart_root_")
    # 伪造包根结构：root/python/pythonw.exe + root/main.py
    os.makedirs(os.path.join(root, "python"), exist_ok=True)
    open(os.path.join(root, "python", "pythonw.exe"), "w", encoding="utf-8").close()
    open(os.path.join(root, "main.py"), "w", encoding="utf-8").write("pass\n")

    try:
        # 快照真实键（防变异态污染）：测前记录 DaniaAI 原值，测后不一致则还原。
        # 教训（2026-10-18 实锤）：M2 变异让 enable 写死 VALUE_NAME，disable 删的
        # 却是 TEST_VALUE → 真实 Run 键泄漏一条指向已删 tempfile 的死命令。
        _snap = autostart.read_command(root, value_name=autostart.VALUE_NAME)
        print("== 1. command_for 结构 ==")
        cmd = autostart.command_for(root)
        check("pythonw.exe" in cmd.lower(), f"用 pythonw（无控制台）: {cmd}")
        check('"main.py"' in cmd or "main.py" in cmd, "指向包根 main.py")
        check(cmd.count('"') >= 2, "带空格路径须有引号包裹")
        check(cmd.startswith('"') and '" "' in cmd,
              "exe 必须整体引号包裹且双参数引号分隔（防含空格路径解析漂移）")
        check(not cmd.lower().startswith("python"), "不是裸命令（卸载搬家后路径不漂）")

        print("== 2. 初始态（无键） ==")
        autostart.disable(root, value_name=TEST_VALUE)  # 先清底
        check(autostart.is_enabled(root, value_name=TEST_VALUE) is False,
              "无注册表项 → is_enabled False（不抛异常）")

        print("== 3. enable/disable 往返（真实 HKCU） ==")
        autostart.enable(root, value_name=TEST_VALUE)
        check(autostart.is_enabled(root, value_name=TEST_VALUE) is True, "enable → True")
        autostart.enable(root, value_name=TEST_VALUE)  # 幂等
        check(autostart.is_enabled(root, value_name=TEST_VALUE) is True, "重复 enable 幂等")
        check(autostart.read_command(root, value_name=TEST_VALUE) == cmd,
              "注册的命令与 command_for 一致（无漂移）")
        autostart.disable(root, value_name=TEST_VALUE)
        check(autostart.is_enabled(root, value_name=TEST_VALUE) is False, "disable → False")
        autostart.disable(root, value_name=TEST_VALUE)  # 幂等
        check(autostart.is_enabled(root, value_name=TEST_VALUE) is False, "重复 disable 幂等")

        print("== 4. sync（config 单一真值对齐） ==")
        autostart.sync(True, root, value_name=TEST_VALUE)
        check(autostart.is_enabled(root, value_name=TEST_VALUE) is True, "sync(True) → 开")
        autostart.enable(root, value_name=os.path.join(root, "WRONG"))  # 残留旧值模拟
        autostart.sync(False, root, value_name=TEST_VALUE)
        check(autostart.is_enabled(root, value_name=TEST_VALUE) is False, "sync(False) → 关")
        # M2 变异的守卫：disable 时值名被换错，TEST_VALUE 仍在 → 这里必须已经是关
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                autostart.RUN_KEY, 0, winreg.KEY_READ) as k:
                winreg.QueryValueEx(k, TEST_VALUE)
            check(False, "sync(False) 后 TEST_VALUE 仍残留（disable 没删对值）")
        except FileNotFoundError:
            check(True, "sync(False) 后注册表无残留")

        print("== 5. is_enabled 异常路径（OSError → False 不误报） ==")
        import unittest.mock as mock
        with mock.patch("winreg.OpenKey", side_effect=PermissionError("denied")):
            check(autostart.is_enabled(root, value_name=TEST_VALUE) is False,
                  "OSError（权限拒绝等）→ False，不抛不误报")
        with mock.patch("winreg.OpenKey", side_effect=OSError("weird")):
            check(autostart.read_command(root, value_name=TEST_VALUE) is None,
                  "read_command 同路径 → None")
    finally:
        try:
            autostart.disable(root, value_name=TEST_VALUE)
        except Exception:  # noqa: BLE001
            pass
        # 变异态污染守卫：真实键若被测试改动，按快照还原（原本有值→恢复，原本无→删）
        try:
            _now = autostart.read_command(root, value_name=autostart.VALUE_NAME)
            if _now != _snap:
                print(f"  ⚠ 检测到真实键 {autostart.VALUE_NAME} 被测试污染（"
                      f"快照={_snap!r} 现在={_now!r}），已还原")
                if _snap is None:
                    autostart.disable(root, value_name=autostart.VALUE_NAME)
                else:
                    import winreg
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, autostart.RUN_KEY,
                                        0, winreg.KEY_SET_VALUE) as k:
                        winreg.SetValueEx(k, autostart.VALUE_NAME, 0,
                                          winreg.REG_SZ, _snap)
        except Exception as _e:  # noqa: BLE001
            print(f"  ⚠ 污染守卫自身失败: {_e}")

    print()
    if _fails := [m for ok, m in _checks if not ok]:
        print(f"❌ {len(_fails)} 项失败：")
        for m in _fails:
            print("  ✗", m)
        return 1
    print(f"✅ 全部通过（开机自启，{len(_checks)} 项断言）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
