# -*- coding: utf-8 -*-
"""写入本轮（fmax=8000 实锤 / 带宽天花板 / 参考音选型三判据）到项目记忆。"""
import os
import time

MEM = r"G:\ai对话\.workbuddy\memory\MEMORY.md"
LOG = r"G:\ai对话\.workbuddy\memory\2026-09-21.md"

A_S2 = "**⇒ 48k 录屏的宽频对本地克隆零增益**，只会带进战斗 BGM/杂人声。"
A_S2_NEW = (A_S2 + " **【09-21 22:10 实锤补强】** `fmax=8000` 已用「拿原厂 bundle 自带的 "
            "`default_ref_audio.wav` 反算 log-mel，与原厂 `prompt_mel_btc_f32.bin` 对拍」证明："
            "fmax=8000 → **corr 1.00000**（平均绝对差 0.0002）；fmax=10000 → 0.95993；fmax=12000 → 0.93568。"
            "旁证：`omni.dll` 里与 mel 有关的符号**全是 `whisper_mel`**，只有一条 `/prompt_mel_btc_f32.bin` 路径串 "
            "⇒ **T2W 的 prompt_mel 完全由我们装配的 bundle 决定，C++ 自己不算 mel**。"
            "真源＝微信 `msg\\file\\` 里的 `8月13日 (1).mp3`（44.1kHz/128kbps，8.03s）；"
            "`wechat_ref.wav` 就是它的 16k 降采样（cos 0.9952、结构指纹全同），但**两者在 0-8k 只差约 1dB** "
            "⇒ **换真源对输出几乎无改善**（实测 ΔF0 0.0，但 cos 0.6885 vs 旧版 0.6958，无显著差异）。")

B_NEW = """

- **2ac 引擎输出带宽天花板 ≈8kHz（2026-09-21 22:10 定案 —— 这才是"糊/闷/不像"的总根因）**：
  条件带宽只到 8kHz（见 §2s 补强）⇒ **产出 8-12k 只有模型先验**。实测：产出 8-12k = **−22.2dB**
  （真源 −7.8dB）、f99 **5400Hz**（真源 10168Hz）；连模型自带的 `ref_minicpm_signature.wav` 自身
  8-12k = −76dB、f99 = 6536 ⇒ **整族是窄带（≈16k 采样语音）模型**。
  🔴 **结论：在这颗引擎上"和原素材一样清晰"做不到**；换参考音、换 48kHz 源都无济于事（0-8k 之外进不了条件）。
  ⭐ **唯一有效的清晰度手段＝输出侧补偿** `tools/hf_restore.py`（真源 LTAS → STFT 增益掩码；
  EQ2 档另加 4-8k→8-12k 谐波折叠 SBR）。实测 8-12k −22.2→**−9.2dB**（目标 −7.8）、f99 5400→**10059**、
  campplus `cos↔真源` 0.6958→**0.7008（全场最高）**、该频段谱平坦度 0.8988→0.6621（≈真源 0.6688，
  说明抬上来的不是纯噪声）。⚠️ 补出来的是**模型先验的高频，不是本人的真实齿音/气声**。
- **2ad 参考音选型三条硬判据（2026-09-21 实测，比"挑最清晰的"重要得多）**：
  ① **真源先找对**：微信 `msg\\file\\` 里那条 44.1k mp3 才是原档（本次才找到，见 §2s 补强）；
     但 0-8k 几乎相同 ⇒ 找对源**不是解法**，别再在这上面花时间。
  ② **电平是绝对尺度**：`prompt_mel = log(clamp(mel,1e-5))`，链路不做归一化 ⇒ 参考音绝对电平直接进条件。
     标定靶 = 原厂 `default_ref_audio` 的**浊音帧 mel 中位均值 −3.213**（对应 RMS −17.0dBFS）。
     历史线上版 −5.678（**低 6dB**）、16k 版 −3.407、本次标定后 −3.232。
     求增益 `g = exp(目标 − 实测)`（`tools/prep_ref_v2.py` 已实现）。
  ③ 🔴 **语域会被复制**：拿 F0 269.7Hz 的台词做参考 → 产出 F0 **266.7**（+26.7Hz，语气变激动）；
     拿 F0 240Hz 的平静片段 → 产出 F0 **240.0（Δ 0.0）**。**参考音要挑"你想要的说话语气"**。
  ④ 实测 `cos↔真源` 的天花板约 **0.70**（参考音自比 1.0000、v2 参考音与真源 0.9839；历史标注 自比.900/异人.306）
     ⇒ 0.70 = "同一个人但不够贴合"，这是本引擎的身份保真上限。
- **2ae 本轮新增可复用工具（G:\\ai对话\\11\\tools\\）**：
  `bake_ref.py`（通用烘焙，`--ref24/--tag`，含"装回 live"修正）｜`prep_ref_v2.py`（真源→24k+电平标定）｜
  `run_v2.py`（烘焙+合成一条龙控制实验）｜`check_v2.py`（同一把尺子+频段剖面）｜`hf_restore.py`（高频补偿）｜
  `make_listening_set_v2.py`（等电平顺序试听集）｜`_probe_mel_fmax.py`（**fmax 判定，最有价值**）｜
  `_probe_mp3_source.py`｜`_probe_ceiling.py`｜`gen_timbre_sample.py`（新增 `--ref=`/`--tag=`）。
  本轮 live 收尾：cache = v2a（242,251,232B md5 `f3c56d70`）；`assets/voices/daniya_ref.wav` 升为
  24k 真源标定版（md5 `9e2a7206`）。回滚点：`prompt_cache.gguf.bak_before_v2a_*`（=v2a 前）、
  `prompt_cache.gguf.V2B_*.keep`（v2b）、`daniya_ref.wav.bak_16k_*`。
"""

LOG_NEW = """

## 22:10 补：把"糊"的总根因钉死 —— 引擎条件带宽 8kHz（附"找对真源"路线被否证）

### 起因
用户指出：目标参考音 `G:\\微信聊天文件\\...\\2026-08\\8月13日 (1).mp3` 本身很清晰，
但克隆出来（连我交付的 01 目标参考音）都"模糊朦胧"，且不像达妮娅。

### 决定性发现
1. **找对了真源，但没用**：`msg\\file\\` 下 `8月13日 (1).mp3`（44.1k/128kbps/8.03s）是原档；
   `wechat_ref.wav` = 它的 16k 降采样（cos 0.9952，结构指纹全同）。真源 8-12k=−7.4dB、
   f99=10910，而降采样版 8-12k=−33.3dB。**但 0-8k 两版只差约 1dB** ⇒ 换真源对输出几乎无影响（实测确认）。
   另外 `8月13日.mp3`(22.15s) 与微信目录 `daniya_ref.wav`(26.97s) 都是**达妮娅游戏台词**（ASR 已出文本）。
2. **fmax=8000 实锤**（`tools/_probe_mel_fmax.py`）：拿原厂 bundle 自带 wav 反算 mel 对拍原厂 mel，
   fmax=8000 → corr **1.00000**；10000 → 0.96；12000 → 0.94 ⇒ 条件只到 8kHz。
   且 `omni.dll` 里 mel 符号全是 whisper_mel、只有 `/prompt_mel_btc_f32.bin` 一个路径串
   ⇒ **prompt_mel 完全由我们的 bundle 决定，C++ 不算 mel**。
3. **输出带宽天花板**：产出 8-12k=−22.2dB（真源 −7.8）、f99 5400（真源 10168）；
   连模型自带签名音自身 8-12k=−76dB、f99=6536 ⇒ 整族窄带。
4. **参考音电平是绝对尺度**：原厂浊音帧 mel 中位均值 −3.213（RMS −17.0dBFS）；历史线上版 −5.678（低 6dB）。
   已按 `g=exp(目标−实测)` 标定（v2a 达 −3.232）。
5. **参考音语域会被复制**：v2b（台词，F0 269.7）→ 产出 266.7（+26.7）；v2a（平静，240.0）→ 240.0（Δ0.0）。
6. **补偿有效**：`hf_restore.py` 静态 LTAS 匹配把 8-12k 拉到 −9.2dB、f99 10059，
   `cos↔真源` 0.6958→0.7008（最高），谱平坦度 0.8988→0.6621（≈真源 0.6688）。

### 本轮产物
- 试听集：`data/native_audio_test/final_sample/聆听_v2/`（01 真源 → 08，全部 −20dBFS 等电平，
  含 `试听_顺序_01至07.wav` 101.8s）
- 高频补偿：`final_sample/hf/EQ{1,2,3}_{A_FIXED,v2a,v2b,B_NATIVE}.wav`
- 新参考音：`data/native_audio_test/ref_v2/{mp3_8s_24k,dialogue_24k}.wav`
- live 收尾：cache=v2a（242,251,232B md5 f3c56d70）、`assets/voices/daniya_ref.wav`=24k 真源版（md5 9e2a7206）
"""


def fix_crlf(b):
    return b.decode("utf-8").replace("\r\n", "\n")


def main():
    # 1) MEMORY.md
    b = open(MEM, "rb").read()
    t = fix_crlf(b)
    n0 = len(b)
    assert t.count(A_S2) == 1, "锚点 S2 count=%d" % t.count(A_S2)
    t = t.replace(A_S2, A_S2_NEW)
    assert "\u2028" not in t
    if "- **2ac " not in t:
        t = t.rstrip("\n") + B_NEW + "\n"
    out = t.replace("\n", "\r\n")
    assert "\r\r" not in out
    open(MEM, "wb").write(out.encode("utf-8"))
    b1 = open(MEM, "rb").read()
    print("MEMORY.md %d → %d bytes  (2ac=%d 2ad=%d 2ae=%d)"
          % (n0, len(b1), fix_crlf(b1).count("2ac "), fix_crlf(b1).count("2ad "),
             fix_crlf(b1).count("2ae ")))

    # 2) 今日日志
    lb = open(LOG, "rb").read()
    lt = fix_crlf(lb)
    nl = "CRLF" if b"\r\n" in lb else "LF"
    if "引擎条件带宽 8kHz" not in lt:
        lt = lt.rstrip("\n") + LOG_NEW + "\n"
    lo = lt if nl == "LF" else lt.replace("\n", "\r\n")
    assert "\r\r" not in lo
    open(LOG, "wb").write(lo.encode("utf-8"))
    print("日志 %d → %d bytes  (换行=%s)" % (len(lb), os.path.getsize(LOG), nl))
    q = fix_crlf(open(LOG, "rb").read())
    print("  日志含 fmax=8000:", "fmax=8000 实锤" in q, " 含 天花板:", "输出带宽天花板" in q)
    print("  时间戳:", time.strftime("%Y-%m-%d %H:%M:%S"))


if __name__ == "__main__":
    main()
