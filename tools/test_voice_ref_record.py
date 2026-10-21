# -*- coding: utf-8 -*-
"""音色克隆「录音」按钮的自测（2026-10-21 修的 bug）。

## 守的是什么

设置页那个「录音」按钮**原来每一次都失败**：

```js
Backend.recordAudio('reference', 2.5)
  → call('record_audio_reference', 'reference', 2.5)
  → 后端："不支持的事件类型: reference"      // 白名单只认 kill/headshot/died/victory/defeat
```

根因不是拼错名字，是**设备方向相反的两个动作共用一个名字**：`record_audio_reference`
录的是**系统输出**（游戏音效识别），而这里要录**麦克风**。修法不是放宽白名单，是给
麦克风录音一个自己的接口 `record_voice_reference(seconds)`。

所以本测试守四条：

  1. 录出来的东西是**单声道 16-bit wav**、帧数对得上时长（立体声要下混）；
  2. 时长被收进 `[1.5, 30]`——录 99 秒不能真的录 99 秒；
  3. 没设备 / 没音频库 / 设备被占用时**如实报错**，不抛异常、不谎报成功；
  4. `record_voice_reference` **绝不调用** `record_audio_reference`
     （这条是那个 bug 的墓碑：名字一混就复发）。

不碰真实设备：假 PyAudio 模块 + 假 `record_wav`，任何一台机器都能跑。
"""
import os
import sys
import tempfile
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.audio import voice_ref_record as vrr  # noqa: E402
from src.ui.webapp import CharacterAPI  # noqa: E402

OK = FAIL = 0


def chk(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print("  PASS", name, extra)
    else:
        FAIL += 1
        print("  FAIL", name, extra)


# ---------------- 假 PyAudio ----------------
class _FakeStream:
    def __init__(self, channels, chunk):
        self.channels = channels
        self.chunk = chunk
        self.reads = 0
        self.stopped = False
        self.closed = False

    def read(self, chunk, exception_on_overflow=False):
        self.reads += 1
        # 左右声道给不同的值，下混才有东西可验证
        if self.channels == 2:
            return (b"\x00\x00\x10\x00") * chunk
        return b"\x00\x00" * chunk

    def stop_stream(self):
        self.stopped = True

    def close(self):
        self.closed = True


class _FakePa:
    """假 pyaudio 模块。`PyAudio` 是"类"→实例，模仿真库的用法 `pa.PyAudio()`。"""

    paInt16 = 8

    def __init__(self, rate=16000, channels=1, fail="", name="假麦克风"):
        self.rate = rate
        self.channels = channels
        self.fail = fail
        self.name = name
        self.streams = []
        self.terminated = False

    def PyAudio(self):  # noqa: N802  模仿真库的类名
        return self

    def get_default_input_device_info(self):
        if self.fail:
            raise RuntimeError(self.fail)
        return {"index": 0, "name": self.name,
                "defaultSampleRate": self.rate, "maxInputChannels": self.channels}

    def open(self, **kw):
        st = _FakeStream(kw["channels"], kw["frames_per_buffer"])
        self.streams.append(st)
        return st

    def terminate(self):
        self.terminated = True


def _frames(rate, sec):
    """期望的 wav 帧数：整块读，所以是"块数 × 每块帧数"（不是块数）。"""
    return int(round(rate * sec / float(vrr._CHUNK))) * vrr._CHUNK


def main():
    tmp = tempfile.mkdtemp(prefix="daniya_vref_")

    # ---------------- 组 1：时长窗口 ----------------
    print("[1] 时长窗口 [1.5, 30]")
    chk("None → 默认 2.5", vrr.clamp_seconds(None) == 2.5)
    chk("0 → 默认 2.5", vrr.clamp_seconds(0) == 2.5)
    chk("负数 → 默认 2.5", vrr.clamp_seconds(-3) == 2.5)
    chk("乱字符串 → 默认 2.5", vrr.clamp_seconds("abc") == 2.5)
    chk("0.2 抬到下限 1.5", vrr.clamp_seconds(0.2) == 1.5)
    chk("99 压到上限 30", vrr.clamp_seconds(99) == 30.0)
    chk("3.0 原样", vrr.clamp_seconds("3.0") == 3.0)
    chk("NaN → 默认 2.5", vrr.clamp_seconds(float("nan")) == 2.5)

    # ---------------- 组 2：真的写出单声道 wav ----------------
    print("[2] 单声道设备：帧数与格式")
    p_mono = os.path.join(tmp, "mono.wav")
    pa1 = _FakePa(rate=16000, channels=1)
    r = vrr.record_wav(p_mono, 2.5, pa_mod=pa1)
    chk("返回成功", r.get("success") is True, str(r.get("error") or ""))
    chk("文件已生成", os.path.isfile(p_mono))
    if os.path.isfile(p_mono):
        with wave.open(p_mono, "rb") as w:
            chk("单声道", w.getnchannels() == 1, w.getnchannels())
            chk("16-bit", w.getsampwidth() == 2, w.getsampwidth())
            chk("采样率跟设备走", w.getframerate() == 16000, w.getframerate())
            chk("帧数 = 时长×采样率", w.getnframes() == _frames(16000, 2.5),
                "%d vs %d" % (w.getnframes(), _frames(16000, 2.5)))
    chk("流被关闭", pa1.streams and pa1.streams[0].closed)
    chk("实例被释放", pa1.terminated)
    chk("回报的实际时长≈2.5", abs(float(r.get("seconds") or 0) - 2.5) < 0.2,
        r.get("seconds"))

    # ---------------- 组 3：立体声要下混，不能把双声道字节当单声道写 ----------------
    print("[3] 立体声设备：下混成单声道（字节数减半）")
    p_st = os.path.join(tmp, "stereo.wav")
    pa2 = _FakePa(rate=16000, channels=2)
    r2 = vrr.record_wav(p_st, 2.5, pa_mod=pa2)
    chk("返回成功", r2.get("success") is True, str(r2.get("error") or ""))
    if os.path.isfile(p_st):
        with wave.open(p_st, "rb") as w:
            chk("仍然是单声道", w.getnchannels() == 1)
            chk("帧数与单声道一致", w.getnframes() == _frames(16000, 2.5),
                w.getnframes())
        chk("字节数 = 帧数×2（没把双声道字节塞进单声道）",
            os.path.getsize(p_st) == 44 + _frames(16000, 2.5) * 2,
            os.path.getsize(p_st))

    # ---------------- 组 4：上限真的生效 ----------------
    print("[4] 要 99 秒，实际只录 30 秒")
    p_big = os.path.join(tmp, "big.wav")
    pa3 = _FakePa(rate=8000, channels=1)
    r3 = vrr.record_wav(p_big, 99, pa_mod=pa3)
    chk("返回成功", r3.get("success") is True, str(r3.get("error") or ""))
    if os.path.isfile(p_big):
        with wave.open(p_big, "rb") as w:
            chk("帧数 = 30 秒（不是 99 秒）", w.getnframes() == _frames(8000, 30),
                "%d vs %d" % (w.getnframes(), _frames(8000, 30)))

    # ---------------- 组 5：失败必须如实报，不抛异常 ----------------
    print("[5] 失败路径：没设备 / 没音频库")
    pa4 = _FakePa(fail="没有可用的输入设备")
    r4 = vrr.record_wav(os.path.join(tmp, "x.wav"), 2.5, pa_mod=pa4)
    chk("没设备 → success=False", r4.get("success") is False)
    chk("错误里带原因", "没有可用的输入设备" in (r4.get("error") or ""), r4.get("error"))
    chk("失败也把实例释放了", pa4.terminated)
    chk("没留下空文件", not os.path.isfile(os.path.join(tmp, "x.wav")))

    _orig = vrr._load_pa
    vrr._load_pa = lambda: (None, "缺少音频库 pyaudiowpatch/pyaudio（假的）")
    try:
        r5 = vrr.record_wav(os.path.join(tmp, "y.wav"), 2.5)
    finally:
        vrr._load_pa = _orig
    chk("没音频库 → success=False", r5.get("success") is False)
    chk("错误里说清缺什么", "缺少音频库" in (r5.get("error") or ""), r5.get("error"))

    # ---------------- 组 6：桥方法（含那个 bug 的墓碑） ----------------
    print("[6] CharacterAPI.record_voice_reference")

    class _Api:
        record_voice_reference = CharacterAPI.record_voice_reference

        def record_audio_reference(self, *a, **kw):
            raise AssertionError("又走了录系统输出的那个接口（'reference' 会被白名单拒掉）")

    made = {}
    # 假实现**不许往真仓库目录写产物**（否则测试在 data/voice_ref 里留垃圾）：
    # 记下"产品想写到哪"，真文件落到临时目录。
    seq = [0]

    def fake_ok(path, seconds=None, pa_mod=None):
        seq[0] += 1
        made["asked_%d" % seq[0]] = path
        real = os.path.join(tmp, "bridge_%d.wav" % seq[0])
        with wave.open(real, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x00\x00" * 1600)
        return {"success": True, "path": real, "seconds": 2.5, "device": "假麦克风"}

    orig = vrr.record_wav
    vrr.record_wav = fake_ok
    try:
        r6 = _Api().record_voice_reference(2.5)
        r7 = _Api().record_voice_reference("不是数字")
    finally:
        vrr.record_wav = orig
    chk("成功路径 success=True", r6.get("success") is True, str(r6.get("error") or ""))
    chk("返回绝对路径", os.path.isabs(r6.get("path") or ""), r6.get("path"))
    chk("文件真的存在", os.path.isfile(r6.get("path") or ""))
    chk("请求写到的目录是 data/voice_ref（与上传的参考音同一目录）",
        os.sep + os.path.join("data", "voice_ref") + os.sep in (made.get("asked_1") or ""),
        made.get("asked_1"))
    chk("请求的文件名是 wav", (made.get("asked_1") or "").endswith(".wav"))
    chk("乱参数也不炸（回落默认时长）", r7.get("success") is True, str(r7.get("error") or ""))
    chk("两次不撞名", made.get("asked_1") != made.get("asked_2"),
        "%s vs %s" % (made.get("asked_1"), made.get("asked_2")))

    vrr.record_wav = lambda *a, **k: {"success": False, "error": "设备被占用"}
    try:
        r8 = _Api().record_voice_reference(2.5)
    finally:
        vrr.record_wav = orig
    chk("录音失败 → success=False（不谎报）", r8.get("success") is False)
    chk("把后端原因原样带给前端", r8.get("error") == "设备被占用", r8.get("error"))

    print()
    print("结果: %d PASS / %d FAIL" % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
