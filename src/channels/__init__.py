"""社交通道（G 脚手架）：把外部 IM（QQ / 微信 / QQ空间 …）接入同一个 AI 大脑。

设计核心：大脑 Dialogue 与通道解耦。通道只做两件事——
  1) 把用户文本喂给 dialogue.respond(text, source=..., channel=本通道名, reply_target=<peer>)
  2) 订阅 AI_DECISION，把带本通道标记(channel==本名)的决策文本发回对应 peer

桌面（ws_server → Unity/WebView）只播放 channel 为空（桌面默认）的决策，互不串台。
默认全部关闭：config.channels.<name>.enabled=false；未配置则不启动任何通道。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from .base import Channel, ChannelError
from .onebot import OneBotChannel
from .gewechat import GewechatChannel
from .qzone import QzonePublisher

if TYPE_CHECKING:  # 避免循环导入，仅用于类型提示
    from ..config import Config  # noqa: F401
    from ..event_bus import EventBus  # noqa: F401
    from ..companion.dialogue import Dialogue  # noqa: F401
    from ..llm.base import LLMClient  # noqa: F401

__all__ = ["Channel", "ChannelError", "OneBotChannel", "GewechatChannel",
           "QzonePublisher", "build_channels", "CHANNEL_REGISTRY"]

logger = logging.getLogger("channels")

# 通道注册表：config.channels.<name> -> 通道类
#   qq     → OneBot(NapCat) 双向通道
#   wechat → Gewechat 微信个人号通道
#   qzone  → QQ空间发布器（仅出站）
CHANNEL_REGISTRY = {
    "qq": OneBotChannel,
    "wechat": GewechatChannel,
    "qzone": QzonePublisher,
}


def build_channels(bus, dialogue, llm, cfg_ns) -> list[Channel]:
    """按 config.channels.<name> 构建已启用的通道实例（不启动）。

    cfg_ns 即 Config.ns（SimpleNamespace 树），用 getattr 链安全读取，缺失即 None。
    返回已构建（enabled=true）的通道实例列表；调用方负责 await ch.start()。
    """
    channels_cfg = getattr(cfg_ns, "channels", None)
    if not channels_cfg:
        logger.info("社交通道：config.channels 未配置，不启动任何通道")
        return []
    out: list[Channel] = []
    for name, cls in CHANNEL_REGISTRY.items():
        sub = getattr(channels_cfg, name, None)
        if not sub:
            continue
        if not bool(getattr(sub, "enabled", False)):
            logger.info("社交通道 %s：enabled=false，跳过", name)
            continue
        try:
            ch = cls(bus=bus, dialogue=dialogue, llm=llm, cfg_section=sub)
            out.append(ch)
            logger.info("社交通道 %s：已构建（待启动）", name)
        except Exception:  # noqa: BLE001
            logger.exception("构建社交通道 %s 失败，跳过", name)
    return out
