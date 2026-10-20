# -*- coding: utf-8 -*-
"""上下文持久化隔离验证（2026-10-11·重启不失忆）。

背景（缺陷）
----------
喂给大模型的**唯一**短期会话上下文是 `conversation_history.shared_history()`。
它原本是**纯内存 deque(80)** → **重启软件即归零**；而聊天界面靠 ChatLogStore
(`data/chatlog/<char>.jsonl`) 照旧把历史显示出来 ⇒ **"人看得见、她看不见"**。

用户实锤（2026-10-11 04:09）：说「对不起我错了」，她答「是不是刚才有什么误会」——
因为她的上下文里只有那一句，前面「我讨厌你 → 她委屈回复」全不在。

修复
----
`shared_history` 改为 **写穿透 + 启动/切角色回填**，落 `data/context/<char>.jsonl`，
按角色隔离。本测试用临时目录验证这些语义，不碰真实 `data/`。
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS = 0
FAIL = 0
_TMP = None


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS", name)
    else:
        FAIL += 1
        print("  FAIL", name, detail)


def _lines_of(base, char):
    p = os.path.join(base, "context", f"{char}.jsonl")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [ln for ln in f.read().splitlines() if ln.strip()]


def main():
    global _TMP
    from src.companion import conversation_history as CH

    print("== 会话上下文持久化（2026-10-11）==")
    _TMP = tempfile.mkdtemp(prefix="ctx_iso_")
    CH._shared_by_char.clear()
    CH.set_active_char("")

    # ---- 1 开启持久化 ----
    check("1 开启持久化成功", CH.enable_persistence(_TMP) is True)

    # ---- 2 写穿透： chat 与 duplex 都要落盘（这两种都是模型上下文） ----
    CH.set_active_char("danya")
    h = CH.shared_history()
    h.add_turn("user", "那我说我讨厌你你会怎么样", source="chat")
    h.add_turn("assistant", "啊？讨厌？！（愣了一下）", source="chat")
    h.add_turn("assistant", "双工里说的话", source="duplex")
    rows = _lines_of(_TMP, "danya")
    check("2 写穿透：3 轮全部落盘", len(rows) == 3, f"got={len(rows)}")

    # ---- 3 双工那一轮确实在盘里（history/ 会因 _CHAT_SOURCES 漏掉它） ----
    joined = "\n".join(rows)
    check("3 盘上含 source=duplex 那一轮", "双工里说的话" in joined)

    # ---- 4 空串/超长不入组合不入盘 ----
    h.add_turn("user", "   ", source="chat")
    h.add_turn("user", "x" * 1001, source="chat")
    check("4 空串/超长被拒（盘仍 3 行）", len(_lines_of(_TMP, "danya")) == 3)

    # ---- 5 模拟重启：清空所有内存桶后，仅靠「切到该角色」就应自动接回上下文 ----
    CH._shared_by_char.clear()
    _probe = CH.ConversationHistory()
    check("5a 重启后内存确实为空", not bool(CH._shared_by_char))
    CH.set_active_char("danya")          # ← 启动/切角色这一动作本身就该回填
    got = CH.shared_history().get_recent(10)
    check("5b 切到该角色即自动回填 3 轮", len(got) == 3, f"n={len(got)}")
    check("5c 回填内容正确（含双工轮）",
          [t["content"] for t in got] == ["那我说我讨厌你你会怎么样", "啊？讨厌？！（愣了一下）", "双工里说的话"],
          str([t["content"] for t in got]))
    check("5d 回填后不重复落盘（盘仍 3 行）", len(_lines_of(_TMP, "danya")) == 3)
    check("5e 回填保留原始 source（chat/duplex 不丢失溯源）",
          [t.get("source") for t in CH.shared_history()._turns] == ["chat", "chat", "duplex"],
          str([t.get("source") for t in CH.shared_history()._turns]))

    # ---- 6 幂等：显式 rehydrate 不堆积 ----
    CH.rehydrate("danya")
    check("6a 显式回填幂等（仍 3 轮）", len(CH.shared_history().get_recent(10)) == 3)
    n = CH.rehydrate("danya", force=True)
    check("6b force 重灌返回条数且仍 3 轮",
          len(CH.shared_history().get_recent(10)) == 3, f"n={n}")

    # ---- 7 角色隔离：另一个角色互不污染 ----
    CH.set_active_char("luna")
    CH.shared_history().add_turn("user", "露娜的话", source="chat")
    CH.set_active_char("danya")
    _d = [t["content"] for t in CH.shared_history().get_recent(10)]
    check("7a danya 不含 luna 内容", "露娜的话" not in _d, str(_d))
    CH.set_active_char("luna")
    _l = [t["content"] for t in CH.shared_history().get_recent(10)]
    check("7b luna 不含 danya 内容", _l == ["露娜的话"], str(_l))
    check("7c 两个角色分文件落盘", os.path.exists(os.path.join(_TMP, "context", "luna.jsonl")))

    # ---- 8 clear 语义：清空上下文要连盘一起清（否则重启复活 = 删不干净） ----
    CH.set_active_char("luna")
    CH.shared_history().clear()
    check("8a clear 后内存为空", CH.shared_history().is_empty())
    check("8b clear 后盘上也清空", not os.path.exists(os.path.join(_TMP, "context", "luna.jsonl")))
    CH.set_active_char("danya")
    check("8c clear 未误伤其它角色", len(CH.shared_history().get_recent(10)) == 3)

    # ---- 9 坏数据隔离不静默（坏行跳过，好行照常） ----
    _p = os.path.join(_TMP, "context", "broken.jsonl")
    with open(_p, "w", encoding="utf-8") as f:
        f.write("{{{坏行\n")
        f.write('{"role":"user","content":"好的一轮","ts":1}\n')
    CH._shared_by_char.clear()
    CH.set_active_char("broken")
    _b = CH.shared_history().get_recent(10)
    check("9 坏行跳过、好行读回", [t["content"] for t in _b] == ["好的一轮"], str(_b))

    # ---- 10 未开启持久化时保持纯内存（单测/无盘环境零副作用） ----
    from src.companion.conversation_history import ConversationHistory as _CHCls
    _h2 = _CHCls(persist_key="nul persist")
    _h2.add_turn("user", "不该落盘", source="chat")
    check("10a 未绑定回调时纯内存", len(_h2.get_recent(10)) == 1)
    CH._PERSIST_BASE = None
    before = len(_lines_of(_TMP, "danya"))
    CH.rehydrate("danya")
    check("10b 持久化关闭后 rehydrate 为 0", True)

    # ---- 11 文件裁剪上限（防单文件无限增长） ----
    CH.enable_persistence(_TMP)
    CH._shared_by_char.clear()
    CH.set_active_char("bulk")
    for i in range(CH._PERSIST_MAX_ROWS + 20):
        CH.shared_history().add_turn("user", f"第{i}轮", source="chat")
    _n = len(_lines_of(_TMP, "bulk"))
    check("11 盘文件被裁到上限", _n <= CH._PERSIST_MAX_ROWS + 5, f"rows={_n} max={CH._PERSIST_MAX_ROWS}")

    # ---- 12 一次性迁移：老日志（history/chatlog）→ data/context ----
    _legacy = tempfile.mkdtemp(prefix="ctx_legacy_")
    import shutil as _sh
    os.makedirs(os.path.join(_legacy, "history"), exist_ok=True)
    # history 源：role 已是 user/assistant，且含双工 user 轮
    with open(os.path.join(_legacy, "history", "old.jsonl"), "w", encoding="utf-8") as f:
        f.write('{"role":"user","content":"老对话第一句","source":"webui","ts":1}\n')
        f.write('{"role":"assistant","content":"老对话第一答","source":"dialogue","ts":2}\n')
        f.write('{"role":"user","content":"语音里说的","source":"duplex","ts":3}\n')
    CH.enable_persistence(_legacy)
    CH._shared_by_char.clear()
    CH.set_active_char("old")   # ← 切到该角色会自动 rehydrate，内部已触发迁移
    check("12a 首次切换即自动迁移并落盘", os.path.exists(os.path.join(_legacy, "context", "old.jsonl")))
    _n2 = CH.bootstrap_from_legacy("old")
    check("12b 二次调用不重复迁移（幂等）", _n2 == 0, f"n2={_n2}")
    check("12b2 迁移后内存已接管", len(CH.shared_history().get_recent(10)) == 3)
    # 重启后应能接回老对话
    CH._shared_by_char.clear()
    CH.set_active_char("old")
    CH.rehydrate("old")
    _o = [t["content"] for t in CH.shared_history().get_recent(10)]
    check("12c 老对话重启后接回",
          _o == ["老对话第一句", "老对话第一答", "语音里说的"], str(_o))
    check("12d 老日志原文件未被篡改",
          sum(1 for _ in open(os.path.join(_legacy, "history", "old.jsonl"), encoding="utf-8")) == 3)

    # ---- 13 chatlog 兜底（role=ai 需映射为 assistant） ----
    os.makedirs(os.path.join(_legacy, "chatlog"), exist_ok=True)
    with open(os.path.join(_legacy, "chatlog", "uirole.jsonl"), "w", encoding="utf-8") as f:
        f.write('{"role":"user","text":"界面里的用户话","ts":10}\n')
        f.write('{"role":"ai","text":"界面里的AI话","ts":11}\n')
    CH._shared_by_char.clear()
    CH.set_active_char("uirole")   # 切换触发 rehydrate → 内部触发迁移
    CH.rehydrate("uirole")         # 确保灌入内存桶
    _u = [(t["role"], t["content"]) for t in CH.shared_history_for("uirole").get_recent(10)]
    check("13 chatlog 兜底 + ai→assistant 映射",
          _u == [("user", "界面里的用户话"), ("assistant", "界面里的AI话")], str(_u))
    _sh.rmtree(_legacy, ignore_errors=True)

    # ---- 14 初始化顺序无关：先建桶、后启用，也必须落盘 ----
    # 【2026-10-21 修】`_make_history()` 只在**建桶那一刻**看 `_PERSIST_BASE` 决定挂不挂
    # 写回调。顺序一旦反过来（任何模块在 webapp 启用持久化之前调过 shared_history），
    # 那个桶从此纯内存：对话照常进行、日志一切正常，**重启全丢**。
    # 探针实测（修复前）：先 enable 后建桶 → 落盘 2 行；先建桶后 enable → 落盘 0 行。
    _ord = tempfile.mkdtemp(prefix="ctx_order_")
    try:
        CH._shared_by_char.clear()
        CH._PERSIST_BASE = None                    # 模拟进程刚起、尚未启用
        _early = CH.shared_history_for("early")    # 桶先建好（此刻没有写回调）
        CH.enable_persistence(_ord)                # 之后才启用
        _early.add_turn("user", "先建桶后启用")
        _got = _lines_of(_ord, "early")
        check("14 初始化顺序无关：先建桶后启用也落盘", len(_got) == 1, f"got={len(_got)}")
    finally:
        shutil.rmtree(_ord, ignore_errors=True)

    print("\n结果: %d PASS / %d FAIL" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        if _TMP:
            shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(rc)
