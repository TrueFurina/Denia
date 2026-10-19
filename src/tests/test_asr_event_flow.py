# -*- coding: utf-8 -*-
"""ASR 事件流闭环：「唤醒 → 切指令识别 → 发 USER_TEXT」的链路不静默断掉。

这是四段主链（想/看/说/听）之外、端到端「听到→想→说」的收口证据：
唤醒词命中后，asr._run() 是否真的（1）发出 WAKE_WORD 事件、（2）切到指令识别、
（3）识别出用户话后发出 USER_TEXT（喂给 LLM 的那一棒）。任何一节断了，表现都是
"她醒了一下又不理你"——静默失效。

【本轮修正】原测试只给 vosk 回退路径（cmd_rec.AcceptWaveform）打桩，但生产默认优先
走 sherpa 路径（engine="sherpa"），结果测试环境里 sherpa 可用 → cmd 分支走的是
sherpa.feed，cmd_rec 从未被调用 → 测出 "cmd_acc=0"、"USER_TEXT 没发"。那不是第 6 处
静默失效，是测试桩只模了回退路径、没模主路径。这里改成两种引擎都确定性覆盖：
  · 场景 A（主路径）：注入假 sherpa → 走 sherpa.feed 分支；
  · 场景 B（回退路径）：让 sherpa 导入失败 → 走 vosk cmd_rec 分支。
两者都必须发出 WAKE_WORD + USER_TEXT，否则判红。

桩约定：None = 这一刻没收到音频（真实静音间隙，audio.get 超时返回 None）；
非空字节 = 一段语音。FakeRec 收到非空音频即"端点收口"，返回 True 并吐对应文本。
sherpa 桩：feed 在收到 CMD 帧时吐文本，其余返回空（=没到句尾）。

运行：.venv/Scripts/python.exe src/tests/test_asr_event_flow.py
"""
import collections
import json
import os
import sys
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

FAIL = 0


def check(cond, msg):
    global FAIL
    print(("  OK  " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL += 1


# 共享帧队列：测试脚本往里放音频帧，_run 内部 new 出来的 FakeAudioIO 从这里取。
# None 表示"此刻无音频"（真实静音/超时），非空字节表示一段语音。
SHARED_FRAMES = collections.deque()


# ── 假 vosk：Model 占位；KaldiRecognizer 区分"带语法=唤醒检测器"和"不带=指令识别器" ──
class FakeRec:
    def __init__(self, model=None, sample_rate=None, grammar=None):
        # 带 grammar 参数是唤醒检测器（WakeWordDetector 构造传了第三参）；
        # 不带则是指令识别器（cmd_rec = KaldiRecognizer(model, sr)）。
        self.kind = "wake" if grammar is not None else "cmd"

    def SetWords(self, v):
        pass

    def AcceptWaveform(self, data):
        # None = 没收到音频（静音/超时），不当作一次语音收口
        return data is not None

    def Result(self):
        text = "达妮娅" if self.kind == "wake" else "今天天气怎么样"
        return json.dumps({"text": text})

    def PartialResult(self):
        return json.dumps({"partial": ""})


class FakeVosk(types.ModuleType):
    class Model:
        def __init__(self, path=None, lang=None):
            pass

    @staticmethod
    def SetLogLevel(level):
        pass

    KaldiRecognizer = FakeRec


# ── 假 sherpa：feed 在收到 CMD 帧时吐文本，否则空串（模拟"没到句尾"） ──
class FakeSherpa:
    def __init__(self):
        pass

    def reset(self):
        pass

    def feed(self, data):
        return "今天天气怎么样" if data == b"CMD_SPEECH" else ""


def _install_sherpa(available):
    """注入 src.voice.sherpa_recognizer 桩：available=True 提供 SherpaRecognizer
    （主路径）；available=False 让该模块没有 SherpaRecognizer 属性 → _run 内的
    `from .sherpa_recognizer import SherpaRecognizer` 抛 ImportError → sherpa=None
    （回退 vosk 路径）。"""
    name = "src.voice.sherpa_recognizer"
    mod = types.ModuleType(name)
    if available:
        mod.SherpaRecognizer = FakeSherpa
    sys.modules[name] = mod


# ── 假 AudioIO：_run 内部会 new 它，帧从共享队列取，取空返回 None（静音/超时） ──
class FakeAudioIO:
    def __init__(self, sample_rate=16000, blocksize=8000, device=None):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def get(self, timeout=0.1):
        if SHARED_FRAMES:
            return SHARED_FRAMES.popleft()
        return None


# ── 假事件总线：跨线程安全收集 publish_threadsafe 投回的事件 ──
class FakeBus:
    def __init__(self):
        self.events = []
        self._lock = threading.Lock()

    def publish_threadsafe(self, event):
        with self._lock:
            self.events.append((event.type.value, event.data))

    def publish(self, event):
        self.publish_threadsafe(event)


print("ASR 事件流闭环：唤醒 → 切指令识别 → USER_TEXT（双引擎覆盖）")

# 必须先 stub vosk，再 import asr 模块（asr 顶部 from vosk import KaldiRecognizer）。
# 【关键】asr._run() 内部还会再 `from vosk import KaldiRecognizer`（局部绑定），
# 若这里把 vosk 从 sys.modules 摘掉，_run 会 import 到真 vosk → cmd_rec 变真、
# 假音频喂不进去 → 回退路径的 USER_TEXT 发不出（假阴性）。所以打完桩绝不摘。
sys.modules["vosk"] = FakeVosk("vosk")
import src.voice.asr as asr_mod  # noqa: E402

from src.config import load_config  # noqa: E402
from src.event_bus import EventType  # noqa: E402

# 用假 AudioIO 替身，避免开声卡；其余走真实 asr._run 逻辑
asr_mod.AudioIO = FakeAudioIO

cfg = load_config("config.yaml")

FRAME_SCRIPT = [b"WAKE_SPEECH", None, b"CMD_SPEECH", None] + [None] * 200


def run_scenario(label, sherpa_available):
    """跑一遍「唤醒语音 → 静音 → 指令语音 → 静音 → 空转」脚本，
    校验事件流闭环。返回是否有 FAIL。"""
    global FAIL
    print(f"\n=== 场景：{label} ===")
    SHARED_FRAMES.clear()
    SHARED_FRAMES.extend(FRAME_SCRIPT)
    _install_sherpa(sherpa_available)

    bus = FakeBus()
    asr = asr_mod.ASR(bus, cfg)
    asr._wake_words = ["达妮娅"]      # 与桩结果解耦，不依赖 config 内容
    asr.set_continuous(False)         # 关掉免唤醒，走"唤醒词→指令"流程
    asr.set_listening(True)           # 麦克风监听开着（否则 _run 开头挂起）

    asr.start()
    try:
        deadline = time.time() + 5.0
        while time.time() < deadline:
            with bus._lock:
                types_ = [e[0] for e in bus.events]
            if (EventType.WAKE_WORD.value in types_
                    and EventType.USER_TEXT.value in types_):
                break
            time.sleep(0.02)

        with bus._lock:
            captured = list(bus.events)
    finally:
        asr.stop()

    types_ = [e[0] for e in captured]
    check(EventType.WAKE_WORD.value in types_,
          "唤醒后发出 WAKE_WORD 事件（否则她醒不了）")
    check(EventType.USER_TEXT.value in types_,
          "切到指令识别后发出 USER_TEXT（否则她醒了却不听你说啥）")
    if EventType.USER_TEXT.value in types_:
        ut = next(e for e in captured if e[0] == EventType.USER_TEXT.value)
        check(bool(ut[1].get("text")),
              f"USER_TEXT 带非空文本：{ut[1].get('text')!r}（这一棒才喂得进 LLM）")
    if (EventType.WAKE_WORD.value in types_
            and EventType.USER_TEXT.value in types_):
        wi = types_.index(EventType.WAKE_WORD.value)
        ui = types_.index(EventType.USER_TEXT.value)
        check(wi < ui, "事件顺序正确：先 WAKE_WORD 后 USER_TEXT")


# 场景 A：主路径（sherpa 可用）→ 走 sherpa.feed 分支
run_scenario("主路径 sherpa 可用", sherpa_available=True)
# 场景 B：回退路径（sherpa 导入失败）→ 走 vosk cmd_rec 分支
run_scenario("回退路径 vosk 可用", sherpa_available=False)

print()
if FAIL:
    print(f"共 {FAIL} 处不符")
    sys.exit(1)
print("全部通过：听到→想→说 闭环的事件流不断（唤醒→指令识别→USER_TEXT，双引擎）")
