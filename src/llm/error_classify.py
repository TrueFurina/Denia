"""共享 LLM 错误分类（2026-10-02）。

从 vision_router 抽出，供视觉路由、聊天链路（dialogue）等所有调用 LLM 的
地方统一使用——**不是所有失败都一样**：

  auth/quota 是"确定性失败"，不会自愈，重试只会反复撞墙；
  rate/timeout 是"瞬时失败"，会自愈，可退避重试。

human_reason() 把内部错误翻译成用户能看懂的一句话原因 + 建议，
供聊天页失败气泡展示（治"消息发送失败（回复超时）"这种既笼统又
不告诉用户真因的体验）。
"""
from __future__ import annotations

from typing import Optional, Tuple

# ---- 额度耗尽特征（AllocationQuota.FreeTierOnly = 免费额度用完且开了
# "仅用免费额度"模式）。匹配错误码优先，其次匹配文案，兼容各家。 ----
_QUOTA_MARKERS = (
    "allocationquota.freetieronly",
    "free quota exhausted",
    "quota exhausted",
    "insufficient_quota",
    # ⚠️ 阿里云返回的是驼峰 "ExceedQuota"（免费额度超限），不是下划线的
    # "exceeded_quota" —— 两种写法都要列，否则漏判成 other（冷却短但会反复重试）。
    "exceeded_quota",
    "exceedquota",
    "exceed quota",
    "quota has been exceeded",
    "out of quota",
    "arrearage",           # 欠费（余额为负，明确不可用）
    "insufficient balance",
    "余额不足",
    # ⚠️【2026-10-02 收紧】原先还有中文泛词 "额度已用完" / "免费额度" / "欠费"。
    # 服务端**任何**提到这些词的说明性文案（如"当前免费额度剩余 X"、"可关闭仅免费
    # 额度模式以按量计费"）都会被判成"额度耗尽"→ 整个视觉能力被锁 30 分钟
    # （用户实锤：弹窗误报成"所有候选都暂不可用"）。
    # 原则：关键词必须是**明确的耗尽表述**。宁可漏判（退化成 other，冷却短、可自愈）
    # 也不能误判成 quota（冷却 30 分钟、需用户手动干预）。
)

_AUTH_MARKERS = (
    "401 unauthorized", "'401'", "invalid api key", "invalid_api_key",
    "incorrect api key", "authentication", "unauthorized", "forbidden",
    "permission denied", "api key 无效", "无权限",
)

_RATE_MARKERS = (
    "429", "rate limit", "rate_limit", "too many requests",
    "requests per minute", "tpm", "rpm", "限流",
)

_TIMEOUT_MARKERS = (
    "timeout", "timed out", "connection", "network", "unreachable",
    "temporarily unavailable", "503", "502", "504", "超时", "连接",
)

# 失败类别
KIND_AUTH = "auth"          # 401/403 无效 Key、无权限 → 不会自愈
KIND_QUOTA = "quota"        # 免费额度耗尽 → 需用户去控制台处理
KIND_RATE = "rate"          # 429 限流 → 会自愈，退避后重试
KIND_TIMEOUT = "timeout"    # 超时/网络抖动 → 会自愈
KIND_OTHER = "other"


def is_quota_exhausted(err: Optional[str]) -> bool:
    """判断一次失败是否为"免费额度耗尽"（而非网络抖动/Key 错误）。

    依据：服务端返回的错误码/文案（openai_client._http_err_detail 会把它们
    透传到异常文案里）。
    """
    if not err:
        return False
    low = str(err).lower()
    return any(m in low for m in _QUOTA_MARKERS)


def classify_error(err: Optional[str]) -> str:
    """把一次失败归类，决定冷却/重试策略。

    顺序很重要：先判确定性（quota/auth），再判瞬时（rate/timeout）。
    """
    if not err:
        return KIND_OTHER
    low = str(err).lower()
    if is_quota_exhausted(err):
        return KIND_QUOTA
    if any(m in low for m in _AUTH_MARKERS):
        return KIND_AUTH
    if any(m in low for m in _RATE_MARKERS):
        return KIND_RATE
    if any(m in low for m in _TIMEOUT_MARKERS):
        return KIND_TIMEOUT
    return KIND_OTHER


# 每类失败给用户看的一句话原因 + 建议（聊天页失败气泡用）
_REASON_BY_KIND = {
    KIND_QUOTA: ("模型免费额度已用尽", "去阿里云控制台关闭「仅免费额度」模式或充值，本模型 30 分钟内不再重试"),
    KIND_AUTH: ("API Key 无效或无权限", "检查设置里的 API Key 是否填对、是否开通了该模型权限"),
    KIND_RATE: ("模型限流中（请求太频繁）", "稍等一分钟会自动恢复，不用重发"),
    KIND_TIMEOUT: ("网络超时或服务无响应", "多为网络抖动或服务端忙，稍后再试"),
    KIND_OTHER: ("服务暂时不可用", "稍后再试；若持续出现请看日志排查"),
}


def human_reason(err: Optional[str]) -> Tuple[str, str]:
    """把内部错误翻译成 (用户可读原因, 建议)。

    优先识别服务端透传的具体错误码/文案（如 AllocationQuota.FreeTierOnly），
    识别不出再按类别兜底。永不抛异常。
    """
    try:
        s = str(err or "")
        # 【2026-10-04】视觉总闸关闭（vision_models.enabled=false）不是"服务故障"，
        # 必须直说，否则用户发图只看到"服务暂时不可用"，以为功能坏了
        # （05:18 会话实锤：发图 → RuntimeError("视觉能力已关闭...") → 被判
        # KIND_OTHER → 聊天页显示"服务暂时不可用"，用户完全不知道要去开开关）。
        if "视觉能力已关闭" in s or "vision_models.enabled" in s:
            return ("视觉感知总开关当前是关闭的", "到 设置→视觉感知 打开开关后再发图，我就能看图了")
        kind = classify_error(err)
        reason, hint = _REASON_BY_KIND[kind]
        # 带上服务端透传的简短错误码（若有），方便定位又不吓人
        marker = ""
        for m in ("AllocationQuota.FreeTierOnly", "invalid_api_key",
                  "insufficient_quota"):
            if m.lower() in s.lower():
                marker = m
                break
        if marker:
            reason = f"{reason}（{marker}）"
        return reason, hint
    except Exception:  # noqa: BLE001
        return _REASON_BY_KIND[KIND_OTHER]
