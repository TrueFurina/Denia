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

    cfg_path = os.path.join(ROOT, "config.yaml")
    raw = yaml.safe_load(open(cfg_path, encoding="utf-8"))
    ns = _to_ns(raw)

    # 1) 默认 disabled → 不构建任何通道
    chs = build_channels(None, None, None, ns)
    assert chs == [], f"默认应无通道，实际 {chs}"

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
