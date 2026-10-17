#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置云端双工（语音/视频）自有端点与开销开关（无 GUI / 无头环境用）。

对应 config.yaml 的 `cloud_duplex` 块：
  cloud_duplex:
    prefer_cloud: true
    local_fallback: true
    video_enabled: true
    official_default: "wss://minicpmo45.modelbest.cn/v1/realtime?mode=video"
    voice:
      endpoint: ""
      api_key: ""
    video:
      endpoint: ""
      api_key: ""

只做"块级替换"——读取现有 cloud_duplex 块、按 CLI 参数覆盖、再把整块写回，
绝不触碰 config.yaml 其它内容（保留注释与结构）。未指定的字段保持文件中原有值。

用法示例：
  python tools/configure_cloud_duplex.py --voice-endpoint "wss://my.host/v1/realtime?mode=chat"
  python tools/configure_cloud_duplex.py --video-endpoint "wss://gpu.host/v1/realtime?mode=video" --video-key "Bearer-XXX"
  python tools/configure_cloud_duplex.py --video-enabled 0 --no-fallback
  python tools/configure_cloud_duplex.py --show
"""
import argparse
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG_PATH = os.path.join(REPO_ROOT, "config.yaml")

DEFAULTS = {
    "prefer_cloud": True,
    "local_fallback": True,
    "video_enabled": True,
    "official_default": "wss://minicpmo45.modelbest.cn/v1/realtime?mode=video",
    "voice_endpoint": "",
    "voice_key": "",
    "video_endpoint": "",
    "video_key": "",
}


def _current_values(text: str) -> dict:
    """从现有 config.yaml 文本提取 cloud_duplex 当前值（依赖-free 正则解析）。"""
    v = dict(DEFAULTS)
    m = re.search(r"^cloud_duplex:(?:\n(?:[ \t].*)?)*", text, re.MULTILINE)
    if not m:
        return v
    block = m.group(0)

    def g(key, default=""):
        mm = re.search(r"^[ \t]*" + re.escape(key) + r":\s*(.+?)\s*$", block, re.MULTILINE)
        if not mm:
            return default
        return mm.group(1).strip().strip('"').strip("'")

    def gb(key, default=True):
        raw = g(key)
        if raw == "":
            return default
        return raw.lower() in ("true", "1", "yes", "on")

    def nested(parent, key):
        mm = re.search(
            r"^[ \t]*" + re.escape(parent) + r":.*?\n[ \t]*" + re.escape(key)
            + r":\s*\"?([^\"\n]*)\"?",
            block, re.MULTILINE | re.DOTALL)
        if mm:
            return mm.group(1).strip()
        return ""

    v["prefer_cloud"] = gb("prefer_cloud", DEFAULTS["prefer_cloud"])
    v["local_fallback"] = gb("local_fallback", DEFAULTS["local_fallback"])
    v["video_enabled"] = gb("video_enabled", DEFAULTS["video_enabled"])
    v["official_default"] = g("official_default", DEFAULTS["official_default"])
    v["voice_endpoint"] = nested("voice", "endpoint")
    v["voice_key"] = nested("voice", "api_key")
    v["video_endpoint"] = nested("video", "endpoint")
    v["video_key"] = nested("video", "api_key")
    return v


def _build_block(v: dict) -> str:
    lines = [
        "cloud_duplex:",
        f"  prefer_cloud: {str(bool(v['prefer_cloud'])).lower()}",
        f"  local_fallback: {str(bool(v['local_fallback'])).lower()}",
        f"  video_enabled: {str(bool(v['video_enabled'])).lower()}",
        f'  official_default: "{v["official_default"]}"',
        "  voice:",
        f'    endpoint: "{v["voice_endpoint"]}"',
        f'    api_key: "{v["voice_key"]}"',
        "  video:",
        f'    endpoint: "{v["video_endpoint"]}"',
        f'    api_key: "{v["video_key"]}"',
        "",
    ]
    return "\n".join(lines)


def _replace_block(text: str, block: str) -> str:
    pat = re.compile(r"^cloud_duplex:(?:\n(?:[ \t].*)?)*", re.MULTILINE)
    if pat.search(text):
        return pat.sub(block.rstrip("\n"), text, count=1)
    # 没有该块：追加到末尾（确保前面有空行）。
    if text and not text.endswith("\n"):
        text += "\n"
    return text + block


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="配置云端双工（语音/视频）端点与开销开关")
    p.add_argument("--voice-endpoint", default=None, help="语音双工端点 (mode=chat)")
    p.add_argument("--voice-key", default=None, help="语音双工鉴权 Bearer Token")
    p.add_argument("--video-endpoint", default=None, help="视频双工端点 (mode=video)")
    p.add_argument("--video-key", default=None, help="视频双工鉴权 Bearer Token")
    p.add_argument("--official-default", default=None, help="官方/兜底默认端点 URL")
    p.add_argument("--video-enabled", default=None, choices=["0", "1"],
                   help="1=推屏(视频双工) 0=纯语音双工")
    p.add_argument("--prefer-cloud", default=None, choices=["0", "1"],
                   help="1=主对话默认云端优先")
    p.add_argument("--no-fallback", action="store_true", help="关闭云<->本地自动回退")
    p.add_argument("--fallback", action="store_true", help="显式开启云<->本地自动回退")
    p.add_argument("--show", action="store_true", help="只打印当前 cloud_duplex 配置后退出")
    args = p.parse_args(argv)

    if not os.path.isfile(CFG_PATH):
        print(f"[错误] 找不到 config.yaml: {CFG_PATH}", file=sys.stderr)
        return 2

    with open(CFG_PATH, "r", encoding="utf-8") as f:
        text = f.read()

    v = _current_values(text)

    if args.show:
        for k in ("prefer_cloud", "local_fallback", "video_enabled",
                  "official_default", "voice_endpoint", "voice_key",
                  "video_endpoint", "video_key"):
            val = v[k]
            if k.endswith("_key") and val:
                val = "<已设置>"
            print(f"{k:18s}: {val}")
        return 0

    if args.voice_endpoint is not None:
        v["voice_endpoint"] = args.voice_endpoint.strip()
    if args.voice_key is not None:
        v["voice_key"] = args.voice_key.strip()
    if args.video_endpoint is not None:
        v["video_endpoint"] = args.video_endpoint.strip()
    if args.video_key is not None:
        v["video_key"] = args.video_key.strip()
    if args.official_default is not None:
        v["official_default"] = args.official_default.strip()
    if args.video_enabled is not None:
        v["video_enabled"] = args.video_enabled == "1"
    if args.prefer_cloud is not None:
        v["prefer_cloud"] = args.prefer_cloud == "1"
    if args.no_fallback:
        v["local_fallback"] = False
    if args.fallback:
        v["local_fallback"] = True

    block = _build_block(v)
    new_text = _replace_block(text, block)
    with open(CFG_PATH, "w", encoding="utf-8", newline="") as f:
        f.write(new_text)

    print("[完成] 已更新 config.yaml 的 cloud_duplex 块：")
    print(block)
    print("提示：App 重启或设置页保存后，运行时会通过 proactive._load_cloud_duplex_cfg() 读取新值；")
    print("      若程序已在运行，可到设置页再点一次保存以热应用（无需重启双工循环）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
