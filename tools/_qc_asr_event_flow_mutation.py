# -*- coding: utf-8 -*-
"""ASR 事件流闭环 · 变异测试：证明 test_asr_event_flow.py 有鉴别力。

思路（与 _qc_wake_mutation.py 一致）：把 asr.py 源文件按"刀"改出 BUG，再跑
test_asr_event_flow 同款事件流脚本，断言 USER_TEXT 应当「消失」（即测试会判红）。
若某把刀改完 USER_TEXT 仍在 → 这把刀没鉴别力 → 变异测试本身判红。

三把刀（都精准命中 asr.py 唯一位置，不会误伤其它分支）：
  A  唤醒后不切指令模式：注释掉 `mode = "cmd"` → 指令分支永不进入 → USER_TEXT 不出。
  B1 sherpa 主路径：把 `text = sherpa.feed(data)` 改成 `text = ""` → 识别结果被吞。
  B2 vosk 回退路径：把 `text = json.loads(cmd_rec.Result())...` 改成 `text = ""` → 同上。

另外先跑「基线」（干净 asr.py）：USER_TEXT 必须出现，否则说明桩/脚本本身坏了。

用法：.venv/Scripts/python.exe tools/_qc_asr_event_flow_mutation.py
退出码 0=全部刀有鉴别力；1=有刀没鉴别力或基线异常。
"""
import importlib
import json
import os
import sys
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

ASR_PATH = os.path.join(ROOT, "src", "voice", "asr.py")

FAIL = 0


def check(cond, msg):
    global FAIL
    print(("  OK  " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL += 1


# ── 桩（与 test_asr_event_flow.py 同款） ──
SHARED_FRAMES = __import__("collections").deque()


class FakeRec:
    def __init__(self, model=None, sample_rate=None, grammar=None):
        self.kind = "wake" if grammar is not None else "cmd"

    def SetWords(self, v):
        pass

    def AcceptWaveform(self, data):
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


class FakeSherpa:
    def __init__(self):
        pass

    def reset(self):
        pass

    def feed(self, data):
        return "今天天气怎么样" if data == b"CMD_SPEECH" else ""


def _install_sherpa(available):
    name = "src.voice.sherpa_recognizer"
    mod = types.ModuleType(name)
    if available:
        mod.SherpaRecognizer = FakeSherpa
    sys.modules[name] = mod


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


class FakeBus:
    def __init__(self):
        self.events = []
        self._lock = threading.Lock()

    def publish_threadsafe(self, event):
        with self._lock:
            self.events.append((event.type.value, event.data))

    def publish(self, event):
        self.publish_threadsafe(event)


# 先把 vosk 桩挂上（asr 模块顶部 from vosk import KaldiRecognizer）
sys.modules["vosk"] = FakeVosk("vosk")

from src.config import load_config  # noqa: E402
from src.event_bus import EventType  # noqa: E402

cfg = load_config("config.yaml")
FRAME_SCRIPT = [b"WAKE_SPEECH", None, b"CMD_SPEECH", None] + [None] * 200


def run_on_module(mod, sherpa_available):
    """用给定 asr 模块跑一遍事件流脚本，返回事件类型列表。"""
    mod.AudioIO = FakeAudioIO
    _install_sherpa(sherpa_available)
    SHARED_FRAMES.clear()
    SHARED_FRAMES.extend(FRAME_SCRIPT)

    bus = FakeBus()
    asr = mod.ASR(bus, cfg)
    asr._wake_words = ["达妮娅"]
    asr.set_continuous(False)
    asr.set_listening(True)
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
    return [e[0] for e in captured]


def load_patched(knife_name, source):
    """把改过的 asr 源写到 src/voice/_mut_asr_<name>.py 并导入。"""
    fname = f"_mut_asr_{knife_name}.py"
    fpath = os.path.join(ROOT, "src", "voice", fname)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(source)
    modname = f"src.voice.{fname[:-3]}"
    sys.modules.pop(modname, None)
    try:
        return importlib.import_module(modname), fpath
    except Exception:
        os.remove(fpath)
        raise


# ── 变异刀：每把 (名称, 替换前, 替换后, 该刀应在哪个引擎路径上验证) ──
KNIVES = [
    # A：唤醒后不切指令模式（命中唯一位置 mode = "cmd"）
    ("A_mode_switch", 'mode = "cmd"', '# mode = "cmd"  # 变异：不切指令模式',
     "sherpa"),
    # B1：sherpa 主路径 cmd 分支识别结果被吞。注意 asr.py 有两处
    # `text = sherpa.feed(data)`：一处是免唤醒持续识别（line ~201），一处是
    # 指令识别 cmd 分支（line ~230）。事件流测试走 cmd 分支，必须用「识别指令」
    # 上下文唯一定位，否则会改到持续识别那处（测试不触发，刀没鉴别力）。
    ("B1_sherpa_text",
     '                    text = sherpa.feed(data)\n                    if text:\n'
     '                        logger.info("识别指令: %s", text)',
     '                    text = ""  # 变异：吞掉识别结果\n                    if text:\n'
     '                        logger.info("识别指令: %s", text)',
     "sherpa"),
    # B2：vosk 回退路径识别结果被吞（命中唯一位置 cmd_rec.Result() 取值那行）
    ("B2_vosk_text",
     'text = json.loads(cmd_rec.Result()).get("text", "")',
     'text = ""  # 变异：吞掉识别结果', "vosk"),
]


def main():
    print("ASR 事件流闭环 · 变异测试")
    base_src = open(ASR_PATH, "r", encoding="utf-8").read()

    # 基线：干净 asr.py，两个引擎都得发 USER_TEXT
    print("\n=== 基线（干净 asr.py，应双双发出 USER_TEXT） ===")
    import src.voice.asr as real_asr
    for label, avail in (("sherpa", True), ("vosk", False)):
        ev = run_on_module(real_asr, avail)
        ok = (EventType.WAKE_WORD.value in ev and EventType.USER_TEXT.value in ev)
        check(ok, f"基线 {label}：USER_TEXT 出现（否则桩/脚本坏了）")

    # 各把刀：改完 USER_TEXT 必须消失
    written = []
    try:
        for name, old, new, engine in KNIVES:
            print(f"\n=== 刀 {name}（引擎={engine}，USER_TEXT 应消失） ===")
            if old not in base_src:
                check(False, f"刀 {name}：找不到锚点 {old!r}（源已变，需重对）")
                continue
            patched = base_src.replace(old, new, 1)
            mod, fpath = load_patched(name, patched)
            written.append(fpath)
            avail = (engine == "sherpa")
            ev = run_on_module(mod, avail)
            has_ut = EventType.USER_TEXT.value in ev
            has_wake = EventType.WAKE_WORD.value in ev
            # A 刀：唤醒仍在、但 USER_TEXT 消失（"她醒了又不理你"）
            # B 刀：唤醒仍在、USER_TEXT 消失（"她听了但没传给 LLM"）
            check((not has_ut) and has_wake,
                  f"刀 {name}：USER_TEXT 消失且有 WAKE_WORD（鉴别力成立）")
    finally:
        for fpath in written:
            try:
                os.remove(fpath)
            except OSError:
                pass

    print()
    if FAIL:
        print(f"共 {FAIL} 处不符")
        sys.exit(1)
    print("全部通过：事件流测试对 wake→cmd 切换 与 USER_TEXT 发射 均有鉴别力")


if __name__ == "__main__":
    main()
