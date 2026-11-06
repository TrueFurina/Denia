# -*- coding: utf-8 -*-
"""长任务护栏：把 mini-workbuddy 第 8 章的两条判据搬进 Denia。

设计来源：`datawhalechina/mini-workbuddy` 第 8 章「长任务不迷路」——
  · 8.2 任务清单：pending / in_progress / completed 三态，列表足够短，
    每次重读一遍都不心疼。
  · 8.6 注入：`tool-error` —— 同一工具、同样参数连续失败达到阈值时，
    给模型一条 hint，叫停「原样重试五遍」的死循环。

**只搬判据，不搬代码**：Denia 不用 AgentScope（无 AgentState / HintBlock /
TaskCreate 工具），所以这里做成进程内的轻量对象，由 `AgentBrain` 在
工具分发后喂结果、在下一轮请求前取 hint。不注册新工具，模型看不到它。

为什么值得搬（2026-11-07 实证）
--------------------------------
Denia 的 agent 会连跑十几步，且智谱 `glm-4-flash` 间歇 429。
旧行为：LLM 调用失败直接返回（外层有 `_post_llm` 有限重试），但**模型层**
仍可能把同一个工具、同样参数反复调用；工具返回值说失败时也没有累计，
模型不知道「这个方案已经试过三次了」。书 8.6 的注入正是为这个死循环准备的。

口径纪律
--------
- 判定失败**只看工具返回文本的前缀特征**，不猜、不把"措辞模糊"当成功。
- hint 只注入一次（达到阈值那次），不每轮刷屏；成功后清零重新计数。
- 不修改 `messages` 的人设/system 层，只追加独立消息。
"""
from __future__ import annotations

import json
import time
from typing import Any, Iterable, Optional

# ---- 8.2：任务清单三态 -------------------------------------------------
PENDING = "pending"
IN_PROGRESS = "in_progress"
COMPLETED = "completed"
_VALID_STATES = (PENDING, IN_PROGRESS, COMPLETED)

# ---- 8.6：工具重复失败 -------------------------------------------------
# 书里默认值 3。这里取 3，与 `tool_retries_limit` 对齐。
DEFAULT_FAILURE_LIMIT = 3

# 工具返回文本里表示"这次没成"的特征。宁可漏判（不注入）也不误伤——
# 误注入会让模型放弃一个其实还能救的方案，比死循环更难查。
_FAILURE_PREFIXES = (
    "工具 ", "电脑代理", "未知工具", "浏览器代理",
    "权限模式", "无法确认", "用户拒绝", "启动 ", "打开 ", "执行失败",
    "读取失败", "写入失败", "未检测到", "失败", "错误", "error", "timeout",
)

# 书 8.6 原模板（英文）。这里保留中文版用于面向用户的日志，注入给模型用
# 中文短句，避免弱模型对长英文段落复读（见 2026-11-03 假绿 #3）。
_HINT_TEMPLATE = (
    "（重试护栏）{tool} 这个调用已连续失败 {count} 次、参数完全相同。"
    "不要再原样重试；先看返回里的错误信息，换一个工具或换一组参数。"
    "如果确实做不到，直接如实说明卡在哪一步。"
)


def _norm_args(args: Any) -> str:
    """把工具参数归一成稳定字符串，用于判定「同样参数」。

    字典键顺序不影响结果；不可 JSON 序列化的对象退化成 repr。
    """
    try:
        return json.dumps(args or {}, sort_keys=True, ensure_ascii=False,
                          default=str)
    except Exception:  # noqa: BLE001
        return repr(args)


def is_failure(result: Any) -> bool:
    """工具返回是否算「这次失败」。保守：只有明确失败特征才认。"""
    s = str(result or "").strip()
    if not s:
        return True
    low = s.lower()
    return any(s.startswith(p) or low.startswith(p.lower())
               for p in _FAILURE_PREFIXES)


class TaskList:
    """8.2 任务清单：三态、可序列化、返回给模型的是短文本。

    与 `src/system/tasks.py` 的分工：那边是**事后账本**（一次执行落一行
    JSONL），这里是**执行中的计划**（当前做到哪一步）。两者不互换。
    """

    def __init__(self) -> None:
        self._items: list[dict] = []
        self._next_id = 1

    def create(self, subject: str, description: str = "") -> str:
        tid = str(self._next_id)
        self._next_id += 1
        self._items.append({"id": tid, "subject": subject,
                            "description": description, "state": PENDING})
        return tid

    def update(self, tid: str, state: str) -> bool:
        if state not in _VALID_STATES:
            return False
        for it in self._items:
            if it["id"] == str(tid):
                it["state"] = state
                return True
        return False

    def delete(self, tid: str) -> bool:
        n = len(self._items)
        self._items = [i for i in self._items if i["id"] != str(tid)]
        return len(self._items) != n

    def counts(self) -> dict[str, int]:
        return {s: sum(1 for i in self._items if i["state"] == s)
                for s in _VALID_STATES}

    def render(self) -> str:
        """书里 `TaskList` 的返回值：两行文本，短到每轮重读都不心疼。"""
        if not self._items:
            return "（任务清单为空）"
        return "\n".join(
            "%s [%s] %s" % (i["id"], i["state"], i["subject"])
            for i in self._items
        )

    def to_list(self) -> list[dict]:
        return [dict(i) for i in self._items]

    def load(self, items: Iterable[dict]) -> None:
        self._items = [dict(i) for i in items if isinstance(i, dict)
                       and i.get("state") in _VALID_STATES]
        ids = [int(i["id"]) for i in self._items
               if str(i.get("id", "")).isdigit()]
        self._next_id = max(ids, default=0) + 1


class RepeatFailureGuard:
    """8.6 `tool-error`：同一工具 + 同样参数连续失败 N 次 → 生成一次 hint。

    成功后清零；参数变化也清零（换参数 = 换方案，不算原样重试）。
    """

    def __init__(self, limit: int = DEFAULT_FAILURE_LIMIT) -> None:
        self.limit = max(1, int(limit or DEFAULT_FAILURE_LIMIT))
        self._key: Optional[tuple[str, str]] = None
        self._count = 0
        self._emitted = False
        self.last_hint: str = ""

    def observe(self, tool_name: str, args: Any, result: Any) -> Optional[str]:
        """喂一次工具结果；达到阈值时返回 hint（只返回一次），否则 None。"""
        key = (str(tool_name or ""), _norm_args(args))
        if not is_failure(result):
            # 成功或措辞不明确的，都按"没在重复同一个失败"处理
            self._key = None
            self._count = 0
            self._emitted = False
            self.last_hint = ""
            return None
        if self._key != key:
            self._key = key
            self._count = 1
            self._emitted = False
            self.last_hint = ""
            return None
        self._count += 1
        if self._count >= self.limit and not self._emitted:
            self._emitted = True
            self.last_hint = _HINT_TEMPLATE.format(tool=key[0], count=self._count)
            return self.last_hint
        return None

    def state(self) -> dict:
        return {"key": self._key, "count": self._count,
                "emitted": self._emitted, "limit": self.limit}


class ContextBudget:
    """8.4 的简化版：只做「用量进入预警带」的判据，不做真压缩。

    Denia 目前没有上下文压缩实现；先给一个**可验证的阈值判据**，
    免得直接搬压缩逻辑却在弱模型上压掉关键工具结果。
    """

    def __init__(self, window: int = 32_768, trigger_ratio: float = 0.8,
                 buffer_ratio: float = 0.2) -> None:
        self.window = max(1, int(window))
        # 书里写死 <= 0.9：太大来不及压
        self.trigger_ratio = min(0.9, max(0.1, float(trigger_ratio)))
        self.buffer_ratio = max(0.0, float(buffer_ratio))
        self.threshold = int(self.window * self.trigger_ratio)
        self.warn_line = int(self.threshold * (1 - self.buffer_ratio))

    def hint(self, used_tokens: int) -> Optional[str]:
        if used_tokens < self.warn_line:
            return None
        if used_tokens >= self.threshold:
            return ("（上下文预警）当前已用 %d token，达到压缩线 %d。"
                    "请尽快收口：先汇报已完成的部分，不要再开新的多步操作。"
                    % (used_tokens, self.threshold))
        return ("（上下文预警）当前已用 %d token，接近压缩线 %d。"
                "注意控制后续步骤。" % (used_tokens, self.threshold))


class TimeHint:
    """8.6 时间注入的节流：距上次超过 interval 才注入一次。"""

    def __init__(self, interval_s: float = 1800.0) -> None:
        self.interval = float(interval_s)
        self._last = 0.0

    def hint(self, now: Optional[float] = None) -> Optional[str]:
        t = time.time() if now is None else float(now)
        if self._last and t - self._last < self.interval:
            return None
        self._last = t
        return ("（时间）当前 " + time.strftime("%Y-%m-%d %H:%M",
                                              time.localtime(t)))
