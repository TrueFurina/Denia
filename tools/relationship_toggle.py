"""安全切换「好感度系统」的开关（**原地改 config.yaml，保留注释与排版**）。

为什么不用 yaml.dump 重写：那会把整份 config.yaml 的注释/顺序全冲掉。这里只做
**基于行的定点替换**，只动 relationship 段里的那几行。

用法：
    python tools\\relationship_toggle.py status                 # 看当前状态
    python tools\\relationship_toggle.py on | off               # 总开关
    python tools\\relationship_toggle.py newbie off             # 关掉新手期保护（实测用）
    python tools\\relationship_toggle.py newbie on              # 恢复默认（6 轮 / 2 天）
    python tools\\relationship_toggle.py newbie 3 0.5           # 自定义：3 轮 / 0.5 天

⚠️ 改 config.yaml 前请**先完全关闭软件**：软件在跑时它的下一次 Config.save() 会整份覆盖。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

CFG = Path(__file__).resolve().parents[1] / "config.yaml"


def _read() -> list[str]:
    return CFG.read_text(encoding="utf-8").splitlines()


def _write(lines: list[str]) -> None:
    CFG.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _find_block(lines: list[str]) -> tuple[int, int]:
    """返回 relationship: 段的 (起, 止) 行号（止=下一处顶格键之前）。无则 (-1,-1)。"""
    start = -1
    for i, l in enumerate(lines):
        if re.match(r"^relationship:\s*(#.*)?$", l):
            start = i
            break
    if start < 0:
        return -1, -1
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].strip() and not lines[j][:1].isspace():
            end = j
            break
    return start, end


def _strip_comment(v: str) -> str:
    return re.split(r"\s+#", v, 1)[0].strip()


def _get(lines: list[str], key: str) -> str:
    a, b = _find_block(lines)
    if a < 0:
        return ""
    for i in range(a + 1, b):
        m = re.match(r"^\s+" + re.escape(key) + r":\s*(.*?)\s*$", lines[i])
        if m:
            return _strip_comment(m.group(1))
    return ""


def _set(lines: list[str], key: str, val: str) -> bool:
    a, b = _find_block(lines)
    if a < 0:
        return False
    for i in range(a + 1, b):
        m = re.match(r"^(\s+)" + re.escape(key) + r":\s*(.*)$", lines[i])
        if m:
            # 保留行尾注释
            cm = re.search(r"\s+#", m.group(2))
            tail = m.group(2)[cm.start():] if cm else ""
            lines[i] = f"{m.group(1)}{key}: {val}{tail}"
            return True
    return False


def _status(lines: list[str]) -> str:
    a, _ = _find_block(lines)
    if a < 0:
        return "config.yaml 里没有 relationship 段（未配置）"
    return ("relationship = enabled: {} | newbie_turns: {} | newbie_days: {}"
            .format(_get(lines, "enabled") or "(缺)",
                    _get(lines, "newbie_turns") or "(缺)",
                    _get(lines, "newbie_days") or "(缺)"))


def main(argv: list[str]) -> int:
    args = [a.lower() for a in argv[1:]]
    if not CFG.exists():
        print(f"[rel-toggle] 找不到 {CFG}")
        return 1
    lines = _read()
    if not args or args[0] == "status":
        print("[rel-toggle] " + _status(lines))
        print("              用法: on|off | newbie on|off | newbie <turns> <days>")
        return 0

    a, _ = _find_block(lines)
    if a < 0:
        print("[rel-toggle] ❌ config.yaml 没有 relationship 段，请先手动加上（见 P0 文档第十章）")
        return 1

    cmd = args[0]
    if cmd in ("on", "off"):
        ok = _set(lines, "enabled", "true" if cmd == "on" else "false")
        print(f"[rel-toggle] enabled -> {cmd}" + ("" if ok else "  (⚠️ 没找到 enabled 键)"))
    elif cmd == "newbie":
        sub = args[1] if len(args) > 1 else "status"
        if sub == "on":
            _set(lines, "newbie_turns", "6")
            _set(lines, "newbie_days", "2.0")
            print("[rel-toggle] 新手期 → 默认（6 轮 / 2 天）")
        elif sub == "off":
            _set(lines, "newbie_turns", "0")
            _set(lines, "newbie_days", "0")
            print("[rel-toggle] 新手期 → 关闭（0 轮 / 0 天，实测用）")
        elif sub == "status":
            print("[rel-toggle] newbie_turns=" + (_get(lines, "newbie_turns") or "(缺)")
                  + " newbie_days=" + (_get(lines, "newbie_days") or "(缺)"))
        elif len(args) >= 3:
            _set(lines, "newbie_turns", args[1])
            _set(lines, "newbie_days", args[2])
            print(f"[rel-toggle] 新手期 → {args[1]} 轮 / {args[2]} 天")
        else:
            print("[rel-toggle] 用法: newbie on|off|status | newbie <turns> <days>")
            return 1
    else:
        print(f"[rel-toggle] 未知命令：{cmd}（可用 on|off|newbie|status）")
        return 1

    _write(lines)
    print("[rel-toggle] " + _status(lines))
    print("[rel-toggle] ⚠️ 若软件正在运行，请先关闭再改；改完需重启软件生效。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
