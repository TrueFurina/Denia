# -*- coding: utf-8 -*-
"""把本轮结论写入项目记忆（含对旧结论的推翻）。"""
import os, time, io

MEM = r"G:\ai对话\.workbuddy\memory\MEMORY.md"
LOG = r"G:\ai对话\.workbuddy\memory\2026-09-21.md"

OLD_2S = (
    "- **2s「音色糊」= 素材链全 16kHz**：`daniya_ref.wav` >8k 恒 0.00%、-40dB 截止 7977Hz、"
    "md5＝`wechat_ref.wav`；整条派生链全 16kHz（`daniya_dataset\\*` 亦全 0.00%），"
    "`pitch_shift_real.py` 自述把真素材降调对齐 238.8Hz ⇒ **从无宽频素材**。降采样一次就永久糊。"
    "**唯一宽频源＝`G:\\视频录制\\Wuthering Waves\\` 29 个 48k 录屏**；`ww.wav` 即该录屏降 16k 版"
    "（RMS 相关 1.0000）。②声码器过平滑（6-8k 0.52% vs 1.59%）。"
)

NEW_2S = (
    "- **2s「音色糊」——2026-09-21 已推翻旧结论（原判「16k素材链导致糊」是错的）**："
    "🔴 **烘焙链路只看得见 0~8kHz**：`extract_prompt_bundle.py` 的 mel 参数是 "
    "`sr=24000,n_fft=1920,hop=480,n_mels=80,fmax=8000`；语音 token 走 s3tokenizer 的 16k 输入；"
    "说话人走 `kaldi.fbank(sample_frequency=16000)`（high_freq=-400 ⇒ 截止 7.6k）。"
    "**⇒ 48k 录屏的宽频对本地克隆零增益**，只会带进战斗 BGM/杂人声。"
    "实测频谱反而是微信源最亮（3.4-6k −8.4dB / 6-8k −12.3dB vs 录屏48k −15.1/−19.7dB）"
    "⇒ **素材裁决＝微信源（最纯且最亮）**。`daniya_ref.wav` md5＝`wechat_ref.wav`＝"
    "`daniya_ref_ORIGINAL_16k.wav`（8.034s，RMS −28.8dBFS，含 0.45s+0.60s **精确全零**首尾静音）。"
    "⚠️ 用户已用 Qwen 本地 TTS + 百炼云端验证该素材「克隆正常」⇒ **素材无责，责任在预处理与链路**。"
    "附：campplus 前置 `feat-feat.mean(dim=0)`（CMN）⇒ **说话人 embedding 天然电平无关**"
    "（归一化对它 cos 0.9991 无影响），但 `prompt_mel` 取 `log(幅度)` 绝对值、不做归一 "
    "⇒ **电平只从 mel 条件这条路起作用**。"
)

NEW_2V = (
    "- **2v 微信纯源烘焙（2026-09-21 21:31 成功，一次通过，全程 23s）**："
    "错误根因＝**历史 bundle 用的是「微信原文件原封不动」**："
    "`H:\\daniya_voice_tools\\tmp_wechat\\` 与 `tmp_cosy_daniya_ref_wav\\` 的 `spk_f32.bin` md5 同为 "
    "`619d39a2f677`、`T_token=201`（=8.04s）⇒ 含 1.05s 全零首尾静音、未归一（−28.8dBFS，低 8.8dB）。"
    "修法（**只修这两个客观缺陷，不做任何美化式干预**）：裁首尾静音（0.45/0.60 → 0.02/0.01s）"
    "+ 归一 −20dBFS RMS；送 extract 用**原生 16k**（零重采样，token/spk 路径无插值损失），"
    "bundle wav 用 24k。**审核点**：`T_token` 应 =176（7.04s）、mel=346 帧；"
    "`F0` 必须原样保持 **238.8Hz**（任何变调都会毁音色）。"
    "工具 `tools/prep_wechat_final.py` + `tools/bake_wechat_final.py`；"
    "live cache 242,251,232B / md5 `e6bbe6c3`。"
    "🔑 参考音**就地升级** `assets/voices/daniya_ref.wav`（8.034s md5 `174c0faa` → 7.040s md5 `8168000c`）"
    "⇒ **config.yaml 无需改动**，所有消费方（云端档 reference_audio / prepare）自动生效，规避改配置的风险。"
    "三方盲听对照 `tools/ab_wechat_3way.py`（同一输入+同一 prepare 参考音，**唯一变量=声码器 cache**）。"
    "⚠️ 历史版对照：247.82MB / md5 `146fdbe7`（= 微信原文件未精修，用户吐槽的那版）。"
)


def replace_once(t, old, new, tag):
    n = t.count(old)
    assert n == 1, "锚点 %s 出现 %d 次（应为 1）" % (tag, n)
    return t.replace(old, new, 1)


def main():
    for p in (MEM, LOG):
        assert os.path.exists(p), "缺失 " + p

    b = open(MEM, "rb").read()
    nl = "\r\n" if b"\r\n" in b else "\n"
    t = b.decode("utf-8").replace("\r\n", "\n")
    print("MEMORY.md 换行 = %s  (%d 字节)" % ("CRLF" if nl == "\r\n" else "LF", len(b)))

    t = replace_once(t, OLD_2S, NEW_2S, "§2s")

    anchor = "工具 `H:\\daniya_voice_tools\\bake_cache.py`／`G:\\ai对话\\11\\tools\\_do_bake2.py`。\n"
    assert t.count(anchor) == 1, "§2u 锚点 %d 次" % t.count(anchor)
    t = t.replace(anchor, anchor + NEW_2V, 1)

    # 头部索引导航补一句
    head_old = "；设计 `11/docs/`。\n"
    assert t.count(head_old) == 1, "头部锚点"
    t = t.replace(head_old, "；设计 `11/docs/`。**§2s 已于 09-21 推翻重写，看前先读。**\n", 1)

    out = t.replace("\n", nl)
    assert "\r\r" not in out, "换行翻倍！"
    open(MEM, "wb").write(out.encode("utf-8"))
    print("MEMORY.md 已更新 (%d 字节)" % len(out))

    # ---- 今日日志 ----
    lb = open(LOG, "rb").read()
    lnl = "\r\n" if b"\r\n" in lb else "\n"
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    entry = """
## %s 达妮娅音色克隆：素材裁决翻案 + 微信纯源重烤

**用户纠正**：录屏里混着战斗BGM/杂人声，微信那条才是最纯的；且该素材已被 Qwen 本地 TTS 与百炼云端验证克隆正常。

**推翻的旧结论**（记于 §2s）："必须用 48k 录屏宽频素材修复糊感" —— 错。读 `extract_prompt_bundle.py` 源码发现整条烘焙链只看得见 0~8kHz（mel `fmax=8000`；token/spk 均从 16k 算）⇒ 48k 宽频零增益、只带来污染。实测频谱微信源反而最亮。

**定位到的真因**：历史 bundle 用的是"微信原文件原封不动"——`tmp_wechat/` 与 `tmp_cosy_daniya_ref_wav/` 的 spk md5 相同、`T_token=201`(=8.04s)，含 1.05s **精确全零**首尾静音 + 电平 −28.8dBFS（低 8.8dB）。即：好素材被预处理喂坏了。

**修复与产出**：
- `tools/prep_wechat_final.py` → 裁首尾静音 + 归一 −20dBFS；16k 送 extract（零重采样）、24k 给 bundle
- `tools/bake_wechat_final.py` → 烘焙并**装回 live**；live cache 242,251,232B / md5 `e6bbe6c3`（原 332,772,832B / `29b1c6d4`）
- 参考音就地升级 `assets/voices/daniya_ref.wav`（md5 `174c0faa`→`8168000c`），config.yaml 未改
- `tools/ab_wechat_3way.py` → 三方盲听对照（唯一变量=cache：新微信 / 历史247MB / 录屏332MB）
- 存档：`tools/_inventory_wechat.py`、`_profile_wechat.py`、`_bandwidth_audit.py`、`prep_wechat_variants.py`

**未完成**：需重启 companion/Comni 让新 cache 生效（`/v1/stream/break` 不重载缓存）。
""" % ts
    outb = lb.decode("utf-8").replace("\r\n", "\n") + entry
    outb = outb.replace("\n", lnl)
    assert "\r\r" not in outb, "日志换行翻倍！"
    open(LOG, "wb").write(outb.encode("utf-8"))
    print("已追加今日日志 (%d 字节)" % len(outb))


if __name__ == "__main__":
    main()
