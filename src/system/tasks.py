# -*- coding: utf-8 -*-
"""任务账本：把「让它干一件事」变成一个可追溯的对象。

为什么单独有这个模块
--------------------
Denia 原本的一等公民是**关系**（``affinity`` / ``mood`` / ``stage``，见
``src/companion/relationship*.py``）——没有「任务」这个对象，所以它永远排不进
「我今天要干什么」那个队列。工具形态恰好相反：一等公民是任务本身
（谁让它干的、干了什么、成没成、人介入没、花了多久）。

这个文件只做一件事：把一次执行落成 ``data/tasks.jsonl`` 的一行。

为什么 ``ok`` 是三态而不是布尔
------------------------------
``run_agent_task()`` 的返回值是一段自然语言（"已经帮你打开了记事本"），
**没有可靠的成败信号**。把"调用没报错"当成成功，就是本项目反复踩过的假绿
（AGENTS.md 第一条：结论必须有证据；第二条：门禁要能 FAIL）。

所以这里只允许三种值：

===========  ====================================================
``True``     有正向证据：工具真的执行过，且返回文本无失败特征
``False``    有负向证据：返回文本命中失败特征，或执行抛异常
``None``     **无法判定**（默认）。宁可空着，也不要编一个 True
===========  ====================================================

``None`` 占多数是**正常且诚实**的状态。想把它变成 ``True`` 只有一条路：
给工具加上可验证的复检（``_BASE_PROMPT`` 规则 10 要求的"动作后必须复核"），
而不是在这里放宽判定。

``partial_execution`` / ``reply_failed``（2026-11-04）
------------------------------------------------------
可选字段，只在「工具真执行过、但最终回复没回来」时出现。现场：工具已把记事本
打开，收尾那次 LLM 调用却撞 429 —— 旧记录只有 ``ok=False`` +
``outcome="HTTP 429"``，看起来像完全没执行。

**``ok`` 仍是 False**：用户确实没拿到结果，这点不能撒谎。
新增字段只负责让第二个事实（工具跑过）同时可见，两个事实互不掩盖。
由 ``run_agent_task()`` 通过 ``extra=`` 传入；正常记录不带这两个字段。
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from typing import Any, Iterable, Iterator, Optional

# 落盘位置：data/ 已在 .gitignore:37 被排除——任务账本是本地运行数据，不进仓库。
_DEFAULT_REL = os.path.join("data", "tasks.jsonl")

# ── 成败判定：正则优先，固定词表兜底 ────────────────────────────────
# 为什么不上纯词表：LLM 的自然措辞变体太多（"已帮你打开" / "已为你打开" /
# "已经打开" / "打开完成" / "任务已完成"……），词表漏掉一个变体，那条记录就
# 掉进 None —— 账本数字随之漂移，这就是「口径唯一」要治的病。
# 三条规则（顺序即优先级）：
#   1. 失败特征优先于成功特征（宁判 False，不作假绿）；
#   2. 「同动作否定」压过完成式措辞（"已完成A，另有1项未完成" → False）；
#   3. 附注里的瑕疵不牵连主任务（"已打开记事本，但无法保存到剪贴板" → True）。

# 失败特征：注意不要用"确认"这种会误伤「已确认正在播放」的词，只匹配否定式。
_FAIL_MARKERS = (
    "无法加载配置", "无法判定", "未配置", "需要安装", "未启用",
    "未确认播放", "未找到", "找不到", "不可用", "没有收到",
    "LLM 调用失败", "失败（HTTP", "需要 httpx",
    # —— 通用失败特征（2026-11-03 补）：原先只覆盖配置/LLM 装载类，
    #    真实任务失败（"执行失败" / "没能打开"）会漏判成 None。
    "失败", "报错", "错误", "异常", "未能", "没能", "未成功", "不成功",
    "被拒绝", "拒绝", "超时", "不存在", "缺少", "已取消", "未完成",
    "打不开", "关不掉", "删不掉", "建不了", "取不到",
)

# 动作动词表：成功式与否定式共用同一份，保证"能配对"。
_VERBS = (
    r"打开|关闭|创建|新建|删除|移除|重命名|改名|启动|运行|执行|设置|设定|"
    r"切换|完成|处理|生成|写入|保存|复制|移动|发送|下载|上传|安装|导出|导入|"
    r"清空|整理|归档|读取|找到|定位|搜索|查询"
)

# 完成式：已 / 已经 / 成功（+ 可选"为你|帮你|给…"）+ 动作动词。
# 这一条覆盖绝大多数自然变体，是判 True 的主力。
_OK_RE = re.compile(rf"(已|已经|成功的?)\s*(为你|帮你|为您|替您|给[你您])?\s*({_VERBS})")

# 否定式：未 / 没 / 不 / 无法 / 未能 / 没能（+ 可选"能"）+ 动作动词。
_NEG_RE = re.compile(rf"(未|没|不|无法|未能|没能)\s*(?:能)?\s*({_VERBS})")

# 固定词表兜底：正则覆盖不到的完成式短语。
_OK_MARKERS = (
    "已帮你", "已打开", "已关闭", "已创建", "已删除", "已重命名",
    "已完成", "成功", "已经帮你", "已经打开", "已经创建", "已经删除",
    "已启动", "已运行", "已设置", "已切换", "打开完成", "处理完成",
    "任务完成", "全部完成", "处理完毕", "搞定", "办好了",
)

# 【2026-11-03】闲聊型任务的能力/认知类否定措辞。
# 这类词不是动作动词，_NEG_RE 抓不到；但"明确说做不到"绝不能记成成功。
_CHAT_NEG_MARKERS = (
    "无法做到", "做不到", "不能做到", "无法完成", "没法做到",
    "不太确定", "不确定", "不知道", "不清楚", "没有这个能力",
    "不支持", "无法识别",
)


def repo_root() -> str:
    """向上找到含 config.yaml 的工程根（与 agent_brain._repo_root 同口径）。"""
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        if os.path.exists(os.path.join(d, "config.yaml")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return os.getcwd()


def log_path(root: str = "") -> str:
    return os.path.join(root or repo_root(), _DEFAULT_REL)


def judge_ok(outcome: str, tools_used: Iterable[str], error: str = "",
             expect_tools: bool = True) -> Optional[bool]:
    """三态判定。**不要**为了好看把它改成 ``return True``。

    - 有 error / 命中失败特征 / 该用工具却一个都没调 → ``False``
    - 调了工具 + 出现明确完成式措辞 → ``True``
    - 调了工具但措辞模糊 → ``None``（宁可空着，也不要编）

    判定优先级（2026-11-03，见上方「成正败判定」注释）：
      1. 失败特征优先 → 宁判 False，不作假绿；
      2. 同动作被否定，压过完成式措辞；
      3. 附注里的瑕疵不牵连主任务；
      4. 措辞模糊 → None。

    expect_tools（2026-11-03）：请求是否**应当**用到工具。
      - True（默认，操作型任务）：一个工具都没调 = 没干活 → False。
      - False（闲聊/提问型请求）：不调工具才是正确行为，此时"给出了一个像样的
        回答"就算成功 ⇒ True；只有明确报错才判 False。
        （实测 2026-11-03：用户输入"你是"，模型正确回答却因"没调工具"被判 False，
         —— 口径错位，不是事实。）
    """
    if error:
        return False
    text = outcome or ""
    tools = list(tools_used)

    # 1) 失败特征优先（假绿是最大的敌人）
    if any(mk in text for mk in _FAIL_MARKERS):
        return False

    if not tools:
        if expect_tools:
            # 该用工具却一个都没调 → 它只是在聊天，没干活。不予判成功。
            return False
        # 闲聊型请求：不调工具是正确行为。有实质回答即算成功。
        if not text.strip():
            return None
        # 能力/认知类否定（"抱歉，无法做到" / "我不确定…"）：这类措辞不属于动作动词，
        # 不会被 _NEG_RE 捕获，必须单独判 —— 不能把"明确说做不到"记成成功。
        if any(mk in text for mk in _CHAT_NEG_MARKERS):
            return False
        if _NEG_RE.search(text):
            return False
        return True

    # 2) 完成式措辞：正则优先，固定词表兜底
    m = _OK_RE.search(text)
    if m:
        ok_verb = m.group(3)
        neg = _NEG_RE.search(text)
        # 3) 同一个动作被否定 → 失败压过完成式（"已完成A，另有1项未完成"）
        if neg is not None and neg.group(2) == ok_verb:
            return False
        # "重命名失败" 这类「动词在前、失败在后」的同动作否定
        if re.search(rf"{ok_verb}[^。；\n]{{0,6}}(失败|报错|错误)", text):
            return False
        # 附注里别的动作瑕疵（"已打开X，但无法保存到剪贴板"）不牵连主任务
        return True

    # 4) 没有完成式措辞，但有否定式（"无法打开" / "未能启动"）→ 失败
    if _NEG_RE.search(text):
        return False

    # 5) 固定词表兜底（"打开完成" / "任务完成" 这类无「已」前缀的短语）
    if any(mk in text for mk in _OK_MARKERS):
        return True

    return None


def record(
    instruction: str,
    outcome: str = "",
    *,
    ok: Optional[bool] = None,
    duration_s: float = 0.0,
    tools_used: Optional[list[str]] = None,
    steps: int = 0,
    confirmed: int = 0,
    error: str = "",
    expect_tools: bool = True,
    extra: Optional[dict[str, Any]] = None,
    root: str = "",
) -> dict[str, Any]:
    """追加一条任务记录并返回它。写入失败不抛异常（记账不能拖垮干活）。"""
    tools_used = list(tools_used or [])
    if ok is None:
        ok = judge_ok(outcome, tools_used, error, expect_tools=expect_tools)
    rec: dict[str, Any] = {
        "ts": time.time(),
        "task_id": uuid.uuid4().hex[:12],
        "instruction": instruction,
        "ok": ok,
        "outcome": (outcome or "")[:2000],
        "duration_s": round(float(duration_s), 2),
        "tools_used": tools_used,
        "steps": int(steps),
        "confirmed": int(confirmed),
        "human_intervened": bool(confirmed),
        "error": error or "",
    }
    if extra:
        rec.update(extra)
    path = log_path(root)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        # 二进制追加 + O_APPEND：多进程同时写不会互相截断
        with open(path, "ab") as f:
            f.write(line.encode("utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return rec


def iter_records(limit: int = 0, root: str = "") -> Iterator[dict[str, Any]]:
    """按时间正序产出记录。``limit`` > 0 时只读末尾 N 条（倒着读，不整文件载入）。"""
    path = log_path(root)
    if not os.path.exists(path):
        return
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            block, chunk = 64 * 1024, b""
            data = b""
            # 从尾部按块回读，凑够 limit 行就停
            while size > 0 and (limit <= 0 or data.count(b"\n") <= (limit if limit > 0 else 0)):
                size = max(0, size - block)
                f.seek(size)
                data = f.read(min(block, os.path.getsize(path) - size)) + data
                if size == 0:
                    break
    except Exception:  # noqa: BLE001
        return
    lines = [ln for ln in data.decode("utf-8", "ignore").splitlines() if ln.strip()]
    if limit > 0:
        lines = lines[-limit:]
    for ln in lines:
        try:
            yield json.loads(ln)
        except Exception:  # noqa: BLE001
            continue


def stats(root: str = "") -> dict[str, Any]:
    """汇总。默认口径：除 ``cancelled`` 外全部计入分母（见 ``--stats`` 输出）。"""
    total = ok_true = ok_false = ok_none = intervened = 0
    tool_counter: dict[str, int] = {}
    dur = 0.0
    days: set[str] = set()
    for r in iter_records(root=root):
        total += 1
        v = r.get("ok")
        if v is True:
            ok_true += 1
        elif v is False:
            ok_false += 1
        else:
            ok_none += 1
        if r.get("human_intervened"):
            intervened += 1
        for t in r.get("tools_used") or []:
            tool_counter[t] = tool_counter.get(t, 0) + 1
        dur += float(r.get("duration_s") or 0)
        ts = r.get("ts")
        if isinstance(ts, (int, float)):
            days.add(time.strftime("%Y-%m-%d", time.localtime(ts)))
    return {
        "total": total,
        "ok_true": ok_true,
        "ok_false": ok_false,
        "ok_none": ok_none,
        "human_intervened": intervened,
        "duration_total_s": round(dur, 1),
        "days": len(days),
        "tools": sorted(tool_counter.items(), key=lambda kv: -kv[1])[:12],
        "ok_rate_decided": (round(ok_true / (ok_true + ok_false), 3)
                            if (ok_true + ok_false) else None),
    }
