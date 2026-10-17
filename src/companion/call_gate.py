# -*- coding: utf-8 -*-
"""通话档闸门 —— 与「桌宠看屏搭话」那套质检**彻底分开**。

【2026-09-23 独立拆分】为什么要分开
--------------------------------
`proactive._on_duplex_speak` 里那一整套闸门是"桌宠看屏搭话"时代的产物：她盯着
屏幕主动吐槽，所以需要防话痨、防复读、防对旧画面说话 —— 于是有了：
    相似去重 / 去重窗口 / 重复话术 / 重复开头 / 空泛应和 / 场景切换静默 / 断节兜底
但**通话是完全相反的场景**：你说一句她答一句，答得短（"嗯，在的"）、会重复
（你问两次名字她就答两次）、"嗯/啊"是正常应和。上面每一条杀掉的恰好都
是**正常通话行为**。2026-09-23 实测一通电话：模型开口 26 次，被这些闸门丢掉
20 次，只有 6~7 次真播出来 —— 用户体感就是"哑巴、说半天不回话"。

⇒ 通话档只保留"防真垃圾"的三条，其余一律放行。改本文件**不会影响桌宠**。

【2026-09-23 二轮·复读用户话】新增 `user_echo`
--------------------------------------------
21:42 那通：用户连问 4 遍"你的名字叫什么"，模型 SPEAK 了 4 次相关产出，其中
**3 次是把用户的话原样复述回来**（"你的名字叫什么？" ×2、"好的，你的名字叫什么？
就是我啊，达妮娅。" ×1），只有 1 次真答（"达妮娅。叫我达妮娅就好。"）。

注意这几句**是模型自己产的**（服务端录像 `chunks[].result.text` 逐块证实），
不是客户端拼接错误；system_prompt 里也早写了「绝不重复他的话」，对模型无效。

这个复读有恶性循环，是本判据要打断的目标：
    复读 → 用户听到自己的话被念回来 → 以为没听懂 → 再问一遍 → 又复读 → …
而且复读会**消耗模型本轮唯一的开口机会**：21:42 实测"复读之后紧跟着 8 秒哑火"
（模型在 KV 里已经有"自己刚说过这句"的上下文，于是转 LISTEN）—— 用户感知就是
"不理我 / 慢理我"，下一轮又听到错位复读 ⇒「语言错乱」。

历史统计（全部通话 trace，与用户话相似度 ≥0.8 记一次）：
    lps=0.5 → 0/56    lps=0.7 → 0/257    lps=1.0 → 3/89 (3~5%)
⇒ 复读率**只与 lps 正相关**（越倾向聆听越容易复读：平时不开口，一开口就退化成
"转写刚听到的内容"），与节拍 fi 无关。故本案**不动旋钮**（lps 有强非线性，动它
会同时挪动"哑火/井喷"两头），改由闸门在客户侧拦掉。

【2026-09-23 三轮·修井喷】加回 `self_echo`（复读自己）
--------------------------------------------------
21:59 那通 39.6 秒：SPEAK 占比 **42.7%**（健康区 20~35%），13 次产出里 **7 次是
模板复读** ——
    「你好呀漂泊者，有什么想聊的吗？」×4
    「你最近看起来好累呀。」        ×2
    「没事我在这儿陪着你呢~」      ×2
用户体感「又开始井喷发言了」。

根因是**上一轮（二轮）放得太宽**：二轮为了修"正常短答被误杀"，把桌宠闸门整片
放行，顺手把三条**防自复读**判据也放行了。日志铁证：
    21:59:33 通话档复核放行（桌宠闸门「gate」不适用于通话）：'你好呀漂泊者，…'
    21:59:43 通话档复核放行（桌宠闸门「gate」不适用于通话）：'你好呀漂泊者，…'
    21:59:54 通话档复核放行（桌宠闸门「重复开头」不适用于通话）：'你好呀漂泊者，…'

**二轮的归类错误**：把这三个当成"防话痨"——
    `_dup_prefix_repeat`   固定起头复读   → 其实比的是 **AI 自己说过的话**，防复读
    `_duplex_dedup_window` 10s 去重窗口   → 同上，防复读
    `_is_repetitive_speech` 重复话术      → 同上，防复读
它们与 `user_echo` 是同一族（二轮只补了"复读用户"这一半），**通话里同样需要**。
真正该放行的只有"防话痨"那几条（空泛应和 / 断节）—— 那才是"你说一句她答一句、
答得短『嗯，好呀』也正常"的场景。

本轮加回的是**整句相似度**判据（复用 `_dup_sim`，字符级 Jaccard > 0.7），
比对窗口沿用真实实现 `_recent_messages`(5) + `_duplex_ai_lines`(12)，
只读不写状态（满足本模块的纯函数约束）。
⚠️ 一开始用的是 `_dup_prefix_repeat`（前 5 字相同），回放发现对中文问候语过敏
   （"你好呀漂泊者"是高频开头，正常回答也复用它）⇒ 已改为整句比对，见下。

⚠️ 与 lps 无关，**不要动旋钮**：947 通录像实测，2Hz(fi=0.5) 档下
    lps=0.30 → SPEAK 中位 90.2%、自复读 56.9%（**井喷峰**）
    lps=0.50 → 52.4% / 12.9%
    lps=0.70 → 41.5% / 24.7%
    lps=1.00 → 44.4% /  **2.9%**（复读最低）
⇒ 在 2Hz 档降 lps 是**反向操作**（越降越井喷）。复读是"说太多"的下游症状，
   由客户侧闸门拦截；旋钮保持 fi=0.5 / lps=1.0 不动。

设计约束
--------
* 本模块**不做**任何状态修改，只做裁决（纯函数式），副作用留给调用方。
  （`_dup_prefix_repeat` 只读 `_recent_messages` / `_duplex_ai_lines`，符合约束）
* 桌宠路径完全不经过这里，改坏了最坏也只是通话变哑巴，桌宠不受影响。
* 回滚：删掉 `proactive._on_duplex_speak` 里那段 `if self._in_call():` 复核
  即可恢复原行为；或覆盖回 `_bak_echo_*` / `_bak_burst_*` 里的备份。
"""
from __future__ import annotations

import re
import time
from typing import Any, Optional, Tuple

__all__ = ["call_gate_check", "CALL_GATE_KEEP"]

#: 通话档保留（拦截）的原因码 → 展示名
CALL_GATE_KEEP = {
    "too_short": "过短(<2字)",
    "format_corrupted": "格式损坏",
    "garbled": "乱语",
    "user_echo": "复读用户的话",
    "self_echo": "复读自己的话",
}

#: 判定"复读用户话"的窗口（秒）与最短用户句长（字）
_ECHO_WINDOW_S = 12.0
_ECHO_MIN_USER_CHARS = 5
#: 「应答词 + 复读用户话」里允许出现在用户话之前的前导字数上限（"好的，"+user）
_ECHO_LEAD_MAX = 4
#: 归一化时剔除的标点/空白
_PUNC = re.compile(r'[，。？！、,.?!~:：;；\s“”"\'‘’…—\-（）()\[\]【】]')


def _norm(s: str) -> str:
    """去掉标点空白，只留实义字符（用于相似度与定位）。"""
    return _PUNC.sub('', s or '')


def _lcs_len(a: str, b: str) -> int:
    """最长公共**连续**子串长度（抓"整句照搬"比编辑距离稳）。"""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def _cut_span(text: str, a: int, b: int) -> Optional[str]:
    """按**归一化索引** [a, b) 从原串里删掉这一段（保留两侧标点习惯）。"""
    pos = [i for i, ch in enumerate(text) if not _PUNC.match(ch)]
    if b > len(pos) or a >= b:
        return None
    s = pos[a]
    e = pos[b - 1] + 1
    left = text[:s]
    right = text[e:].lstrip('，。？！、,.?!~:：;； ')
    if not right:
        return None
    return (left + right).strip()


def _echo_check(st: Any, text: str) -> Tuple[Optional[str], Optional[str]]:
    """检测 text 是否复读了用户刚说的话。

    :return: (原因码, 替换文本)
             * ('user_echo', '')      —— 整句复读，整条丢弃
             * ('user_echo_trim', x)  —— "应答词+复读+真答案"，裁掉复读部分后用 x
             * (None, None)           —— 不是复读
    """
    user_raw = str(getattr(st, '_duplex_last_user_text', '') or '').strip()
    if not user_raw:
        return None, None
    ts = float(getattr(st, '_duplex_last_user_speech_ts', 0.0) or 0.0)
    if ts <= 0 or (time.time() - ts) > _ECHO_WINDOW_S:
        return None, None
    user = _norm(user_raw)
    if len(user) < _ECHO_MIN_USER_CHARS:
        return None, None          # "你好""嗯"这种短句答回来是正常对话，不判
    ai = _norm(text)
    if not ai:
        return None, None

    # ① 整句复读：与用户话高度重合
    if _lcs_len(ai, user) / float(max(len(ai), len(user))) >= 0.8:
        return 'user_echo', ''

    # ② 「应答词 + 复读用户话 + 真答案」：用户话完整出现在很靠前的位置，
    #    后面还跟着真内容 ⇒ 只裁掉复读段，保住答案（21:42 实测
    #    "好的，你的名字叫什么？就是我啊，达妮娅。" → "好的，就是我啊，达妮娅。"）
    idx = ai.find(user)
    if 0 <= idx <= _ECHO_LEAD_MAX:
        tail_len = len(ai) - (idx + len(user))
        if tail_len >= 2 and len(ai) <= len(user) * 4:
            trimmed = _cut_span(text, idx, idx + len(user))
            if trimmed and len(_norm(trimmed)) >= 2:
                return 'user_echo_trim', trimmed
    return None, None


def _self_echo_check(st: Any, text: str) -> bool:
    """检测 text 是否复读了**自己**近期说过的话（整句高度相似）。

    与 `_echo_check` 的区别：那个比的是"用户刚说了什么"，这个比的是"她刚说了什么"。
    21:59 那通 13 次产出里 7 次是同一模板反复念（"你好呀漂泊者，有什么想聊的吗？"
    ×4），属于后者，二轮时被误当"防话痨"放行了。

    ⚠️ **为什么不用 `_dup_prefix_repeat`（前 5 字相同）**：
    离线回放 53 通 trace 实测它有真实误伤 ——
        AI='你好呀漂泊者，我是达妮娅。星炬学院虚质科学部的学生…'  用户问='能做个自我介绍吗'
        AI='我是达妮娅。'                                        用户问='名字叫什么'
    这两条是正常回答，只因"前 5 字与她之前说过的某句相同"就被拦。
    中文问候语（"你好呀漂泊者"）天然是高频开头，前缀判据对它过敏。
    改用 `_dup_sim`（字符级 Jaccard > 0.7）做**整句**比对即可两者兼顾：
        完整复读「你好呀漂泊者，有什么想聊的吗？」↔ 同句          → 1.00 → 拦 ✓
        「你好呀漂泊者，…想聊的吗？」↔「你好呀漂泊者，我是达妮娅。…」→ 0.43 → 放行 ✓

    窗口 = `_recent_messages`(5) + `_duplex_ai_lines`(12)，沿用真实实现，
    不另造状态；只读不写，符合本模块的纯函数约束。

    :return: True 表示是自复读，应整条丢弃。
    """
    try:
        sim = getattr(st, '_dup_sim', None)
        if not callable(sim):
            return False
        t = (text or '').strip()
        if not t:
            return False
        hist = (list(getattr(st, '_recent_messages', None) or [])
                + list(getattr(st, '_duplex_ai_lines', None) or []))
        for prev in hist:
            if prev and sim(t, prev):
                return True
        return False
    except Exception:  # noqa: BLE001
        return False          # 判据内部异常不得吞真回答


def call_gate_check(st: Any, text: str, quality_gate: bool = True) -> Tuple[bool, str, Optional[str]]:
    """通话档裁决：是否放行。

    :param st:   ProactiveStrategy 实例（仅读取它的判定方法与"用户刚说了什么"）
    :param text: 已剥离控制tokens、已过 settle 的文本
    :param quality_gate: 是否启用"乱语"质量门（沿用 _DUPLEX_QUALITY_GATE）
    :return: (放行?, 原因码, 替换文本)；放行且无需改写时原因码为 ''、替换文本为 None。

    刻意**不引入**的判据（这些才是桌宠专用的"防话痨"，通话里会误杀）：
        _is_empty_filler（空泛应和：通话里"嗯，好呀"是正常应答）/
        _is_truncated_speech（断节：通话逐块产出，短句天然无句末标点）/
        _similar_to_recent / 场景切换静默 / pubjavis 前台闸 / 弹幕闸
    **已引入**的两类"防复读"（它们不是防话痨，通话同样需要）：
        `user_echo`   —— 防复读**用户**的话（二轮）
        `self_echo`   —— 防复读**自己**的话（三轮；经 `_dup_prefix_repeat`）
    """
    if not text:
        return False, "too_short", None
    if len(text) < 2:
        return False, "too_short", None
    # 格式损坏：裸转义序列/反斜杠占比过高/几乎无有效字符（prepare 后 warmup 常见）
    try:
        if st._is_format_corrupted(text):
            return False, "format_corrupted", None
    except Exception:  # noqa: BLE001
        pass
    if quality_gate:
        try:
            if st._is_garbled_speech(text):
                return False, "garbled", None
        except Exception:  # noqa: BLE001
            pass
    # 复读用户的话（详见模块 docstring）
    try:
        _why, _new = _echo_check(st, text)
    except Exception:  # noqa: BLE001
        _why, _new = None, None
    if _why == 'user_echo':
        return False, 'user_echo', None
    if _why == 'user_echo_trim':
        return True, '', _new
    # 复读自己的话（固定起头）—— 井喷的主要构成，二轮漏放行了这一条
    if _self_echo_check(st, text):
        return False, 'self_echo', None
    return True, "", None
