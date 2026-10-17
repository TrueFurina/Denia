# -*- coding: utf-8 -*-
"""修复「LLM 层原生嗓音残留」：让客户端把达妮娅参考音真正传给 Comni 引擎。

根因（日志实锤）：
  config.yaml:1409  duplex_local_omni_ref_audio: ''   → 客户端 self._ref_audio_path = None
  local_omni_duplex.py:214  self._generate_audio = False（全仓无 True 赋值）
  local_omni_duplex.py:1380 if self._generate_audio and self._ref_audio_path ... → 永远 False
  ⇒ prepare 消息不带 ref_audio
  ⇒ worker.py:2095 回落 _LANG_REF_AUDIO_PATHS["zh"] = apps/assets/ref_audio/ref_minicpm_signature.wav
  ⇒ 日志: "[Duplex RefAudio] LLM=None, TTS=None" + "prefilling voice_audio: ref_minicpm_signature.wav"
  ⇒ LLM 层音色 = MiniCPM 原生（F0≈286Hz），只有声码器层是我们烤的 cache（F0≈235Hz）
  ⇒ 听感 = 达妮娅的壳 + MiniCPM 原生的底

本脚本做两处最小改动：
  [1] config.yaml   L1409 '' → 达妮娅参考音绝对路径
  [2] local_omni_duplex.py L1380 门条件 (generate_audio) → (generate_audio or native_audio)
      —— 语义：「只要采纳 Comni 原生音频，就必须把参考音传下去」。
      不增加开销：现在每次会话本来就在 prefill 原生音（n_past=1211）。

红线：先备份；CRLF 原样保持；每处断言 count==1；写前写后断言无 \\r\\r。
"""
import os
import re
import shutil
import time

ROOT = r"G:\ai对话\11"
CFG = os.path.join(ROOT, "config.yaml")
SRC = os.path.join(ROOT, "src", "llm", "local_omni_duplex.py")
REF = r"G:\ai对话\11\assets\voices\daniya_ref.wav"
TS = time.strftime("%Y%m%d_%H%M%S")


def read_text(path):
    b = open(path, "rb").read()
    crlf = b"\r\n" in b
    t = b.decode("utf-8").replace("\r\n", "\n")
    return t, crlf


def write_text(path, text, crlf):
    assert "\r\r" not in text, "写入前发现 \\r\\r"
    out = text.replace("\n", "\r\n") if crlf else text
    assert "\r\r" not in out, "转换后出现 \\r\\r"
    with open(path, "wb") as f:
        f.write(out.encode("utf-8"))
    back = open(path, "rb").read().decode("utf-8")
    assert "\r\r" not in back, "落盘后出现 \\r\\r"


def main():
    print("=" * 100)
    print("修复：让 LLM 层也使用达妮娅参考音（彻底去掉 MiniCPM 原生底色）")
    print("=" * 100)

    # ---------- 前置检查 ----------
    assert os.path.isfile(REF), "参考音不存在: %s" % REF
    import soundfile as sf
    info = sf.info(REF)
    print("  参考音: %s" % REF)
    print("          sr=%d dur=%.3fs" % (info.samplerate, info.duration))

    # ---------- 备份 ----------
    bc = CFG + ".bak_before_refaudio_" + TS
    bs = SRC + ".bak_before_refaudio_" + TS
    shutil.copy2(CFG, bc)
    shutil.copy2(SRC, bs)
    print("  已备份: %s" % os.path.basename(bc))
    print("  已备份: %s" % os.path.basename(bs))

    # ---------- [1] config.yaml ----------
    print()
    print("-" * 100)
    print("[1] config.yaml L1409: duplex_local_omni_ref_audio")
    print("-" * 100)
    t, crlf = read_text(CFG)
    old1 = "  duplex_local_omni_ref_audio: ''"
    new1 = "  duplex_local_omni_ref_audio: " + REF
    n = t.count(old1)
    print("  匹配 '%s' → %d 次" % (old1.strip(), n))
    assert n == 1, "锚点不唯一，中止"
    t = t.replace(old1, new1, 1)
    write_text(CFG, t, crlf)
    print("  写入完成（换行保持 %s）" % ("CRLF" if crlf else "LF"))

    # ---------- [2] local_omni_duplex.py ----------
    print()
    print("-" * 100)
    print("[2] src/llm/local_omni_duplex.py:1380 门条件")
    print("-" * 100)
    t2, crlf2 = read_text(SRC)

    old_c = (
        "                    # 仅当 Comni 原生语音开启(generate_audio=True)才内嵌参考音做原生克隆；\n"
        "                    # 关掉时达妮娅声线由千问/VoxCPM 合成，Comni 参考音纯属浪费（且中文路径\n"
        "                    # 曾触发 stream_prefill(voice_audio) failed），故跳过，省 prefill 也避坑。\n"
    )
    new_c = (
        "                    # 【2026-09-21 修复·原生底色】此前门条件是 generate_audio，而该量在\n"
        "                    # 本文件只有 False 一处赋值（从无 True）⇒ 参考音【永远传不出去】⇒\n"
        "                    # Comni 回落到自带 ref_minicpm_signature.wav（F0≈286Hz 的 MiniCPM\n"
        "                    # 原生签名音），于是 LLM 层永远带原生底色，而声码器层却是我们烤好的\n"
        "                    # cache（F0≈235Hz）——两层打架，听感即「达妮娅的壳+原生的底」。\n"
        "                    # 日志实锤: '[Duplex RefAudio] LLM=None, TTS=None' +\n"
        "                    #           'prefilling voice_audio: .../ref_minicpm_signature.wav'。\n"
        "                    # 真正决定「是否采纳原生语音」的是 native_audio（config:\n"
        "                    # proactive.duplex_native_audio），故门条件改为二者取或：只要采纳原生\n"
        "                    # 音频，就必须把达妮娅参考音传下去。\n"
        "                    # 不增加开销：原生音频开启时引擎本来每次会话就在 prefill 参考音\n"
        "                    # （n_past≈1211），换成本参考音只是把这个 prefill 换成对的人。\n"
    )
    nc = t2.count(old_c)
    print("  注释块匹配 %d 次" % nc)
    assert nc == 1, "注释锚点不唯一，中止"
    t2 = t2.replace(old_c, new_c, 1)

    old_g = "                    if self._generate_audio and self._ref_audio_path and os.path.isfile(self._ref_audio_path):"
    new_g = ("                    if ((self._generate_audio or self._native_audio)\n"
             "                            and self._ref_audio_path\n"
             "                            and os.path.isfile(self._ref_audio_path)):")
    ng = t2.count(old_g)
    print("  门条件匹配 %d 次" % ng)
    assert ng == 1, "代码锚点不唯一，中止"
    t2 = t2.replace(old_g, new_g, 1)

    write_text(SRC, t2, crlf2)
    print("  写入完成（换行保持 %s）" % ("CRLF" if crlf2 else "LF"))

    # ---------- 落盘复核 ----------
    print()
    print("-" * 100)
    print("[3] 落盘复核")
    print("-" * 100)
    t3, _ = read_text(CFG)
    for i, l in enumerate(t3.split("\n"), 1):
        if "duplex_local_omni_ref_audio" in l:
            print("  config L%d: %s" % (i, l.strip()))
    t4, _ = read_text(SRC)
    for i, l in enumerate(t4.split("\n"), 1):
        if "self._generate_audio or self._native_audio" in l:
            print("  源码 L%d: %s" % (i, l.strip()))
    print()
    print("  完成。下一步必须【重启软件】才会生效（引擎进程持有旧会话）。")


if __name__ == "__main__":
    main()
