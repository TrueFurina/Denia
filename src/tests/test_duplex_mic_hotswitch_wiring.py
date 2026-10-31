# -*- coding: utf-8 -*-
"""设置页切麦「热切换」接线回归（2026-10-31）。

修复前：webapp.setCallAudioDevice 里写的是
    hasattr(self._proactive, "set_duplex_mic") → self._proactive.set_duplex_mic(...)
但 ProactiveStrategy 上**没有** set_duplex_mic（全仓零定义），守卫恒 False →
注释承诺的「让运行中的双工立即用新设备（不必重开整通电话）」从未生效。
真名是 apply_call_settings(mic_device=...)（内部更新 _duplex_mic_device 并对当前
双工客户端调 set_mic_device 热切换）。

本测试守两件事：
  [A] 真方法行为：apply_call_settings(mic_device=...) 确实写字段并调 client.set_mic_device
  [B] 源码接线：webapp.py 的**代码**（tokenize 剥掉注释后）调用真名，旧名 set_duplex_mic
      不再出现在代码里（注释墓碑不算——避免"源码断言被自己注释满足"）。
"""
import io
import os
import sys
import tokenize

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

FAIL = 0


def check(cond, msg):
    global FAIL
    if cond:
        print("  ✓", msg)
    else:
        FAIL += 1
        print("  ✗ FAIL:", msg)


from src.companion import proactive


class FakeClient:
    def __init__(self):
        self.mic = None

    def set_mic_device(self, dev):
        self.mic = dev


class FakeSelf:
    """供 unbound 方法调用的最小 self（只覆盖 mic 路径）。"""

    def __init__(self, client):
        self._duplex_mic_device = None
        self._duplex_camera_device = None
        self._client = client

    def _active_duplex_client(self):
        return self._client


def test_apply_mic_hotswitch():
    print("[A1] apply_call_settings(mic_device=...) 写字段 + 调 client.set_mic_device")
    c = FakeClient()
    fs = FakeSelf(c)
    proactive.ProactiveStrategy.apply_call_settings(fs, mic_device="USB 麦克风")
    check(fs._duplex_mic_device == "USB 麦克风", "_duplex_mic_device 已更新")
    check(c.mic == "USB 麦克风", "当前双工客户端收到 set_mic_device('USB 麦克风')")


def test_apply_mic_none_noop():
    print("[A2] mic_device=None → 不动客户端（只切麦时才热切换）")
    c = FakeClient()
    fs = FakeSelf(c)
    proactive.ProactiveStrategy.apply_call_settings(fs, mic_device=None)
    check(c.mic is None, "未传 mic_device 时不调 set_mic_device")
    check(fs._duplex_mic_device is None, "未传 mic_device 时不改字段")


def test_apply_no_client_safe():
    print("[A3] 当前无活跃双工客户端 → 只记字段，不炸")
    fs = FakeSelf(None)
    proactive.ProactiveStrategy.apply_call_settings(fs, mic_device="X")
    check(fs._duplex_mic_device == "X", "无客户端时仍记下设备（下次构造用）")


def _code_only(path):
    """用 tokenize 剥掉注释，返回代码 token 串（注释墓碑不算接线）。"""
    parts = []
    with open(path, "rb") as f:
        for tok in tokenize.tokenize(f.readline):
            if tok.type == tokenize.COMMENT:
                continue
            parts.append(tok.string)
    return " ".join(parts)


def test_webapp_source_wiring():
    print("[B1] webapp.py 代码里调用真名 apply_call_settings(mic_device=...)")
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(root, "src", "ui", "webapp.py")
    code = _code_only(path)
    # 精确到 picked 实参——webapp 另有三处 apply_call_settings(dev)（扬声器/通用），
    # 只断言方法名会被它们满足，故必须匹配本条路径的完整调用串。
    check("apply_call_settings ( mic_device = str ( picked ) )" in code,
          "切麦路径精确调用 apply_call_settings(mic_device=str(picked))")
    print("[B2] 旧名 set_duplex_mic 已不在代码中（只在注释墓碑里）")
    check("set_duplex_mic" not in code, "代码中不再有 set_duplex_mic（守卫写错名已清除）")


if __name__ == "__main__":
    test_apply_mic_hotswitch()
    test_apply_mic_none_noop()
    test_apply_no_client_safe()
    test_webapp_source_wiring()
    print()
    if FAIL:
        print(f"❌ {FAIL} 项失败")
        sys.exit(1)
    print("✅ 全部通过（切麦热切换 接线）")
