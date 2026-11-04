# -*- coding: utf-8 -*-
"""工具权限五档：决定一次工具调用是"直接执行 / 先问 / 直接拒"。

设计来源：mini-workbuddy（datawhalechina）第 7 章「别让它乱删东西」对
AgentScope 2.x `PermissionMode` 的对照说明。这里不搬代码（Denia 不用 AgentScope），
只搬那条判据：**每次工具调用都必须被判定一次，不存在"没归类就默认放行"**。

五档与 Denia 三分类（SAFE / WRITE / EXEC，见 agent_tools.py）的对应：

| 模式 | 读/看（SAFE） | 写文件（WRITE） | 执行/键鼠（EXEC） | 适用场景 |
|---|---|---|---|---|
| `explore`      | 放行 | **拒绝** | **拒绝** | 只想让她看，不想让她动 |
| `default`      | 放行 | 询问 | 询问 | 日常（默认档，最接近人类预期） |
| `accept_edits` | 放行 | 放行 | 询问 | 你正盯着她改文件 |
| `dont_ask`     | 放行 | 放行 | **拒绝** | 无人值守/定时任务：问不出来就拒绝 |
| `bypass`       | 放行 | 放行 | 放行 | 一次性沙箱 / 完全信任 |

三条容易搞错的地方（都来自那本书的实证）：

1. **`dont_ask` 不是"全部放行"**。它的语义是"询问一律转成拒绝"——
   无人值守时最危险的恰恰是"没人应答却照样执行"。
2. **`bypass` 才是全放行**，包括 `rm -rf /` 这类。只该用在容器或一次性环境。
3. **`explore` 连写文件都拒**，不只是拒执行。"只读模式"要能真的只读。

向后兼容
--------
`agent.permission_mode` 为空/未配置时，`decide_tool()` 不参与判定，调用方走老逻辑
（`require_confirm` + 高危名单）。这样已有配置的行为一点不变。
"""
from __future__ import annotations

from .agent_tools import EXEC_TOOLS, SAFE_TOOLS, WRITE_TOOLS

MODE_EXPLORE = "explore"
MODE_DEFAULT = "default"
MODE_ACCEPT_EDITS = "accept_edits"
MODE_DONT_ASK = "dont_ask"
MODE_BYPASS = "bypass"

MODES = (MODE_EXPLORE, MODE_DEFAULT, MODE_ACCEPT_EDITS, MODE_DONT_ASK, MODE_BYPASS)

# 常见别名的归一（方便命令行/配置文件里写人话）
_ALIASES = {
    "read_only": MODE_EXPLORE,
    "readonly": MODE_EXPLORE,
    "只读": MODE_EXPLORE,
    "ask": MODE_DEFAULT,
    "auto": MODE_ACCEPT_EDITS,
    "accept": MODE_ACCEPT_EDITS,
    "never_ask": MODE_DONT_ASK,
    "no_ask": MODE_DONT_ASK,
    "yolo": MODE_BYPASS,
    "trust": MODE_BYPASS,
}

ALLOW, ASK, DENY = "allow", "ask", "deny"


def normalize_mode(raw) -> str:
    """把配置值归一成五档之一；认不出来一律回 `default`（保守）。

    为什么未知值不报错也不放行：配置里写错一个字母（`explorer`）时，
    静默变成"全放行"是最坏的结果；变成"照常询问"最多是烦一点。
    """
    s = str(raw or "").strip().lower()
    if not s:
        return ""
    s = _ALIASES.get(s, s)
    return s if s in MODES else MODE_DEFAULT


def is_configured(raw) -> bool:
    """是否显式配置了模式（空 = 走老逻辑，保持向后兼容）。"""
    return bool(str(raw or "").strip())


def decide_tool(mode: str, tool_name: str) -> str:
    """判定一次工具调用：`allow` / `ask` / `deny`。

    未分类的工具名按 **EXEC** 处理（最保守）——真出现说明工具表加了新东西
    却忘了归类，此时宁可多问一句。
    """
    m = normalize_mode(mode)
    if not m:
        return ALLOW
    name = str(tool_name or "")

    if name in SAFE_TOOLS:
        return ALLOW
    if m == MODE_BYPASS:
        return ALLOW
    if m == MODE_EXPLORE:
        return DENY
    if name in WRITE_TOOLS:
        # 写入类：accept_edits / dont_ask 放行；default 询问
        return ALLOW if m in (MODE_ACCEPT_EDITS, MODE_DONT_ASK) else ASK
    # 其余（EXEC 或未归类）：default/accept_edits 询问；dont_ask 拒绝
    if m == MODE_DONT_ASK:
        return DENY
    return ASK


def describe(mode: str) -> str:
    """给日志/横幅用的一句话说明。"""
    m = normalize_mode(mode)
    return {
        MODE_EXPLORE: "只读：只允许看和查，写文件与执行一律拒绝",
        MODE_DEFAULT: "日常：写文件与执行前询问",
        MODE_ACCEPT_EDITS: "改文件放行，执行前询问",
        MODE_DONT_ASK: "无人值守：写文件放行，执行一律拒绝（不询问）",
        MODE_BYPASS: "全放行（仅限沙箱/完全信任）",
    }.get(m, "未配置：沿用 require_confirm 老逻辑")
