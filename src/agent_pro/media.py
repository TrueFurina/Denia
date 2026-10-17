# -*- coding: utf-8 -*-
"""agent_pro · 系统媒体会话查询（借 dsh-computer-use 的 media controls 思路）。

media_state：读 Windows 系统媒体传输控制（SMTC，即音量浮层/媒体键看到的那个
状态源），返回每个在报媒体的应用、播放状态、歌名/艺人——**零视觉 token、
确定性**。用途：
  ① 「放歌成功了吗」的客观验证，替代 VLM 截图判断；
  ② 「现在放的什么歌」这类问题不用看屏直接答。
winsdk 缺失时如实说明（不静默装死）。只读，绝不控制（暂停/切歌留给用户）。
"""
from __future__ import annotations

import logging

logger = logging.getLogger("agent_pro")


_SMTC_STATUS = {0: "closed", 1: "opened", 2: "changed", 3: "stopped",
                4: "paused", 5: "playing"}


def _status_text(raw) -> str:
    """winsdk 枚举 → 小写词。优先 .name，退化为数值映射，再退化 unknown。"""
    try:
        name = getattr(raw, "name", None)
        if name:
            return str(name).lower()
        try:
            return _SMTC_STATUS.get(int(raw), "unknown")
        except Exception:  # noqa: BLE001
            s = str(raw)
            return s.rsplit(".", 1)[-1].lower()
    except Exception:  # noqa: BLE001
        return "unknown"


async def media_state(args: dict, ctx: dict, flags: dict) -> str:
    try:
        from winsdk.windows.media.control import (  # type: ignore
            GlobalSystemMediaTransportControlsSessionManager as Mgr)
    except Exception as e:  # noqa: BLE001
        return (f"媒体会话查询不可用：当前 Python 缺少 winsdk（import 失败：{e}）。\n"
                f"在主程序使用的解释器里 pip install winsdk 后自动可用。")
    try:
        mgr = await Mgr.request_async()
        sessions = mgr.get_sessions()
        n = int(sessions.size)
    except Exception as e:  # noqa: BLE001
        return f"媒体会话查询失败（系统接口异常，不谎报）：{e}"
    if n == 0:
        return ("当前没有任何应用向系统报告媒体播放——多半没在放歌"
                "（或该应用未接入系统媒体控件，这种情况请改用 state_snapshot 视觉核实）。")
    lines = [f"检测到 {n} 个媒体会话："]
    for i in range(n):
        try:
            s = sessions.get_at(i)
            app = s.source_app_user_model_id or "未知应用"
            status = _status_text(s.get_playback_info().playback_status)
            try:
                mp = await s.try_get_media_properties_async()
                title = (mp.title or "").strip()
                artist = (mp.artist or "").strip()
                song = f"{title} - {artist}".strip(" -") or "（无曲目信息）"
            except Exception:  # noqa: BLE001
                song = "（曲目信息不可读）"
            mark = "▶" if status == "playing" else ("⏸" if status == "paused" else "·")
            lines.append(f"{i + 1}. [{mark} {status}] {app} ｜ {song}")
        except Exception as e:  # noqa: BLE001
            lines.append(f"{i + 1}.（会话读取失败：{e}）")
    lines.append("（状态源=Windows 系统媒体控件；playing=正在播放）")
    return "\n".join(lines)
