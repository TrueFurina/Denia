"""复现并验证 _safe_js_api 状态分叉 bug（2026-10-10）。

场景：js_api 包装层若浅拷贝 __dict__，总线事件处理（原对象）与前端轮询（wrapper）
会看到两个不同的 _chat_seq / _chat_buffer —— 「agent卡/新消息永不上屏」的根因。

运行：python tools/test_jsapi_state_split.py
"""

import sys
from collections import deque

sys.path.insert(0, r"G:/ai对话/11")

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS", name)
    else:
        FAIL += 1
        print("FAIL", name, detail)


def main():
    from src.ui.webapp import CharacterAPI, _safe_js_api

    wa = CharacterAPI.__new__(CharacterAPI)
    wa._chat_buffer = deque(maxlen=200)
    wa._chat_seq = 0
    wa._chat_history_loaded = False
    wa._dialogue = None   # get_chat_messages 读 current_character_name 时的兜底

    wrapped = _safe_js_api(wa, "T")

    # 1) 共享命名空间：标量写入双方可见
    wa._chat_seq = 41
    check("seq 原对象写入 → wrapper 可见", wrapped._chat_seq == 41,
          f"wrapper._chat_seq={wrapped._chat_seq!r}")
    wrapped._chat_seq = 42
    check("seq wrapper 写入 → 原对象可见", wa._chat_seq == 42)

    # 2) 共享缓冲：原对象 append（模拟 _on_chat_user_text）→ wrapper 轮询可见
    wa._chat_buffer.append({"id": wa._next_chat_id(), "role": "user", "text": "x"})
    check("缓冲 append → wrapper 可见", len(list(wrapped._chat_buffer)) == 1,
          f"wrapper 缓冲 {len(list(wrapped._chat_buffer))} 条")

    # 3) 历史恢复路径不再 rebinding：_load_chat_history 后双方仍同一对象
    import src.ui.webapp as W
    orig_load = CharacterAPI._load_chat_history

    def _fake_load(self, char_id):
        loaded = [{"id": 900, "role": "ai", "text": "历史"}]
        self._chat_buffer.extendleft(reversed(loaded))

    CharacterAPI._load_chat_history = _fake_load
    try:
        before = id(wa._chat_buffer)
        wrapped._chat_history_loaded = False
        wrapped.get_chat_messages()
        check("历史恢复未更换 deque 对象", id(wa._chat_buffer) == before,
              "deque 被 rebinding（两方将看到不同缓冲）")
        check("历史条目原对象可见", any(m.get("id") == 900 for m in wa._chat_buffer))
        check("历史条目 wrapper 可见", any(m.get("id") == 900 for m in wrapped._chat_buffer))
    finally:
        CharacterAPI._load_chat_history = orig_load

    # 4) guard 降级逻辑仍在（不可序列化返回 → 降级）
    class D:
        def bad(self):
            return {"fn": self.bad}

    w2 = _safe_js_api(D(), "D")
    r = w2.bad()
    check("守卫降级仍生效", (not r.get("success")) and ("not JSON serializable" in r.get("error", "")))

    print(f"\nRESULT: PASS={PASS} FAIL={FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
