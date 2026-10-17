# -*- coding: utf-8 -*-
"""新用户引导体验开关（不触碰任何已有数据）。

用法:
    python tools/toggle_first_run.py on      开启：每次启动都重走首启引导（体验/调试用）
    python tools/toggle_first_run.py off     关闭：恢复正常（老用户不再弹引导）
    python tools/toggle_first_run.py status  查看当前状态

原理: config.yaml 的 force_first_run 开关（官方调试位）
    is_first_run = force_first_run or (not first_run_done)
开启只影响「是否弹引导」，角色 / 音色 / 模型 / 密钥等数据一律不动。
"""
from __future__ import annotations

import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "config.yaml")
KEY = "force_first_run"


def _nl(path: str) -> str:
    raw = open(path, "rb").read()
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    return "\r\n" if crlf > lf else "\n"


def read_state() -> bool:
    """返回 force_first_run 当前值。

    ⚠️ 必须用 splitlines()，不能 split(_nl())：open() 默认用「通用换行」把 CRLF 归一成
    LF，再按 "\\r\\n" 去 split 会切不开 → 整份文件当成一行 → 永远读成 False
    （表现为「明明 yaml 里是 true，status 却说 false」）。
    """
    for line in open(CFG, encoding="utf-8").read().splitlines():
        s = line.strip()
        if s.startswith(KEY + ":"):
            return s.split(":", 1)[1].strip().lower() in ("true", "yes", "1", "on")
    return False


def set_state(value: bool) -> None:
    nl = _nl(CFG)
    text = open(CFG, encoding="utf-8").read()   # 通用换行 → 正文只有 "\n"
    old_txt = "%s: %s" % (KEY, "true" if read_state() else "false")
    new_txt = "%s: %s" % (KEY, "true" if value else "false")
    cnt = text.count(old_txt)
    if cnt != 1:
        print("[FAIL] 期望恰好 1 处 '%s'，实际 %d 处 —— 未做任何改动" % (old_txt, cnt))
        sys.exit(1)
    text = text.replace(old_txt, new_txt)
    # 保住原换行风格：写回时若原文件是 CRLF，就还原成 CRLF
    # （否则一次 toggle 会把整份 config.yaml 降级成 LF，git/编辑器全量 diff）。
    if nl == "\r\n":
        text = text.replace("\n", "\r\n")
    with io.open(CFG, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    # 换行风格校验
    raw = open(CFG, "rb").read()
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    if not ((crlf > lf) == (nl == "\r\n")):
        print("[WARN] 换行风格发生变化，请检查 config.yaml")
    # YAML 真解析校验
    try:
        import yaml  # noqa: WPS433
        d = yaml.safe_load(open(CFG, encoding="utf-8"))
        assert bool(d.get(KEY)) is value
        print("[OK] YAML 解析校验通过：first_run_done=%s force_first_run=%s"
              % (d.get("first_run_done"), d.get(KEY)))
    except Exception as exc:  # noqa: BLE001
        print("[WARN] 跳过 YAML 校验（%s: %s）" % (type(exc).__name__, str(exc)[:60]))


def main() -> None:
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "status").lower()
    if cmd in ("on", "true", "1", "enable"):
        set_state(True)
        print("已开启：下次启动会重走首启引导（数据未改动）。重启程序生效。")
    elif cmd in ("off", "false", "0", "disable"):
        set_state(False)
        print("已关闭：恢复为老用户，不再弹引导。重启程序生效。")
    else:
        st = read_state()
        print("force_first_run = %s" % st)
        print("  → %s" % ("每次启动都弹首启引导（体验模式）" if st else "正常：老用户不弹引导"))


if __name__ == "__main__":
    main()
