"""config.channels 接线测试：验证按配置构建/跳过通道（需 pyyaml，缺失则跳过）。

运行：python src/channels/tests/test_config_wiring.py
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _to_ns(o):
    if isinstance(o, dict):
        return SimpleNamespace(**{k: _to_ns(v) for k, v in o.items()})
    if isinstance(o, list):
        return [_to_ns(x) for x in o]
    return o


def main() -> int:
    try:
        import yaml
    except Exception:  # noqa: BLE001
        print("⚠ pyyaml 缺失，跳过 config 接线测试")
        return 0
    from src.channels import build_channels

    # 【2026-10-21 修·读错文件】原来读 config.yaml —— 那是**开发机实配**（被 gitignore）。
    # 本机把通道启用了，于是这条"出厂默认不建通道"必然红；而它想验的恰恰是**出厂行为**。
    # 出厂行为只能拿 config.release.yaml 验（那才是新用户装完拿到的那份）。
    cfg_path = os.path.join(ROOT, "config.release.yaml")
    if not os.path.exists(cfg_path):
        print("⚠ 缺 config.release.yaml，跳过 config 接线测试")
        return 0
    raw = yaml.safe_load(open(cfg_path, encoding="utf-8"))
    ns = _to_ns(raw)

    # 1) 出厂默认 disabled → 不构建任何通道
    #    新用户没有 OneBot(3001) / Gewechat(2531) / QQ 空间 cookie，默认开启等于
    #    让它们各自去连一个不存在的本地服务：OneBot 每 5s 刷一条 warning 永不停。
    chs = build_channels(None, None, None, ns)
    assert chs == [], f"出厂默认应无通道，实际构建了 {[c.name for c in chs]}"

    # 2) 开启 qq → 构建出 OneBotChannel，且读取到正确 mode
    ns.channels.qq.enabled = True
    ns.channels.qq.onebot.mode = "reverse"
    chs = build_channels(None, None, None, ns)
    assert len(chs) == 1 and chs[0].name == "qq", f"应构建 qq 通道，实际 {chs}"
    assert chs[0]._mode == "reverse", chs[0]._mode
    print("✓ config.channels 接线：disabled→[]，enabled→OneBotChannel(mode=reverse)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
