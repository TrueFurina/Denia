# -*- coding: utf-8 -*-
"""agent_pro · 新增工具的 OpenAI function-calling schema 与包内派发。"""
from __future__ import annotations

import os

PACK_TOOL_SPECS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "find_file",
            "description": "在用户电脑上按文件名模式搜索文件（支持通配符，如 *.png、报告*.docx）。"
                           "优先用 Everything（若安装），否则受限遍历常用目录。可指定 folder 缩小范围。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "文件名模式，如 *.png、简历*.pdf"},
                    "folder": {"type": "string", "description": "可选：限定检索目录（绝对路径或'桌面'等）"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait_for",
            "description": "轮询等待某个条件出现（最多 90 秒），替代盲目 sleep。"
                           "kind=window 等窗口标题/进程名出现；kind=process 等进程出现；"
                           "kind=screen 等屏幕上出现某文本（用视觉模型轮询，较慢）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["window", "process", "screen"]},
                    "target": {"type": "string", "description": "等待目标：窗口标题/进程名/屏幕文本"},
                    "timeout": {"type": "number", "description": "最长等待秒数，默认 10"},
                },
                "required": ["kind", "target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "state_snapshot",
            "description": "一次拿到电脑当前状态快照：前台窗口 + 全部可见窗口清单 +（默认）"
                           "视觉模型对屏幕内容的概括。在不确定该做什么/执行前后核实效果时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "visual": {"type": "boolean", "description": "是否包含视觉概括，默认 true"},
                    "question": {"type": "string", "description": "可选：想让视觉模型重点回答的问题"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ui_elements",
            "description": "枚举目标窗口里的可交互元素并编号列出（类型/名称/中心坐标）。"
                           "这是精确点击的首选路径：先 ui_elements 拿编号，再 click_element index=N。"
                           "对 UIA 树残缺的 Electron 应用它会明确告知改走视觉定位。",
            "parameters": {
                "type": "object",
                "properties": {
                    "app": {"type": "string", "description": "窗口标题子串（如 汽水音乐）；留空=前台"},
                    "max": {"type": "integer", "description": "最多列出几个元素，默认 60"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click_element",
            "description": "点击屏幕上的元素，两种方式二选一："
                           "① index=编号：点 ui_elements 刚列出的第 N 个元素（确定性，优先用）；"
                           "② desc=描述：视觉模型看屏定位后点击（如「右下角的搜索按钮」），"
                           "适合没有无障碍树的窗口。double=true 为双击（如播放列表第一首歌）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "方式①：ui_elements 清单中的编号"},
                    "desc": {"type": "string", "description": "方式②：要点击的东西的简短描述"},
                    "double": {"type": "boolean", "description": "是否双击，默认 false"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_in_app",
            "description": "在指定应用里执行搜索：自动定位该应用的输入框 → 点击聚焦 → "
                           "粘贴要搜的内容 → 回车。一个工具覆盖「汽水音乐搜歌、抖音搜索、"
                           "浏览器搜索」等场景，不需要手动 ui_elements/click_element 组合。"
                           "执行后请用 media_state（音乐类）或 wait_for/state_snapshot 验证结果。",
            "parameters": {
                "type": "object",
                "properties": {
                    "app": {"type": "string", "description": "应用名或窗口标题子串（如 汽水音乐）"},
                    "query": {"type": "string", "description": "要搜索的内容（如 晴天 周杰伦）"},
                    "confirmed": {"type": "boolean", "description": "内容命中危险词时需用户确认后置 true"},
                },
                "required": ["app", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "media_state",
            "description": "查询 Windows 系统媒体会话（SMTC）：哪些应用在报媒体、播放/暂停状态、"
                           "歌名和歌手。零视觉成本、确定性的「放歌成功了吗」验证方式，"
                           "也用于回答「现在放的什么歌」。音乐类操作的前后核实首选这个工具。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]

PACK_TOOL_NAMES = {t["function"]["name"] for t in PACK_TOOL_SPECS}

_FOLDER_WORDS = {"桌面": "Desktop", "下载": "Downloads", "文档": "Documents",
                 "图片": "Pictures", "视频": "Videos", "音乐": "Music"}


async def dispatch_pack_tool(name: str, args: dict, ctx: dict, flags: dict) -> str:
    """包内三工具的派发（已由 agent_tools.dispatch_tool 的统一超时/异常兜底包裹）。"""
    if name == "find_file":
        from .wait import find_file
        folder = str(args.get("folder") or "").strip()
        profile = os.environ.get("USERPROFILE", "")
        if folder and folder in _FOLDER_WORDS and profile:
            args["folder"] = os.path.join(profile, _FOLDER_WORDS[folder])
        elif folder and not os.path.isabs(folder) and profile:
            cand = os.path.join(profile, folder)
            if os.path.isdir(cand):
                args["folder"] = cand
        return find_file(args, flags)
    if name == "wait_for":
        from .wait import wait_for
        return await wait_for(
            str(args.get("kind", "")), str(args.get("target", "")),
            float(args.get("timeout", 10.0) or 10.0), 0.5, ctx)
    if name == "state_snapshot":
        from .wait import state_snapshot
        return await state_snapshot(args, ctx, flags)
    if name == "ui_elements":
        from .screen import ui_elements
        return ui_elements(args, flags)
    if name == "click_element":
        from .screen import click_element
        return await click_element(args, ctx, flags)
    if name == "search_in_app":
        from .screen import search_in_app
        return await search_in_app(args, ctx, flags)
    if name == "media_state":
        from .media import media_state
        return await media_state(args, ctx, flags)
    return f"未知包内工具：{name}"
