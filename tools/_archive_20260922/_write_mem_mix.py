# -*- coding: utf-8 -*-
"""追加「混合音色」判定结论到项目记忆（MEMORY.md 追加 §2ah + 当日日志）"""
import io
import time

TS = time.strftime("%Y-%m-%d %H:%M")
MEM = r"G:\ai对话\.workbuddy\memory\MEMORY.md"
LOG = r"G:\ai对话\.workbuddy\memory\2026-09-21.md"

SEC = """
### 2ah 「像原生音色混合」判定＝模型固有为主（已实测，勿重查）
- 🔑 **先建刻度再下判断**：campplus 同人上限（同录音劈两半）= **0.7280**／异人下限（达妮娅 vs 原生签名）= **0.1947**／同录音只降采样 = 0.9957（工具 `tools/_calib_spk.py`）。**单看绝对值毫无意义**，必须换算成区间占比：克隆产出 0.7103 ⇒ (0.7103-0.1947)/(0.7280-0.1947)=**96.7%** ⇒ **说话人身份已基本完全迁移**，"混合"不是身份串味。
- 🔑 **2×2 消融矩阵**（`tools/_run_mix_ablation.py` + `tools/gen_ablation_mix.py`，由 gen_sample_app.py 派生，`ABL_REF=0/1`）：数据 = cos↔达妮娅 / cos↔原生
  | 缓存 | 传参考音 | cos↔达妮娅 | cos↔原生 |
  |---|---|---|---|
  | 达妮娅 | 传 | **0.7103** | 0.3054 |
  | 达妮娅 | 不传 | 0.6525 | 0.3497 |
  | 原生 | 传 | 0.2257 | 0.6653 |
  | 原生 | 不传 | 0.1347 | 0.7679 |
  ⇒ **声码器缓存是音色主控**（换原生缓存→塌到异人地板 0.13~0.23）；**参考音是真实但次控**（同缓存下 +0.06~+0.09）。两层都生效、方向都对，**没有"原生通道在偷偷覆盖"的证据**。⚠️ 早年用 `B_NATIVE`（不传参考音）当"原生底色对照"是**污染对照**——它的缓存当时已是达妮娅烤的，所以它也 0.689。
- 🔑 **判"是不是混合体"要用线性混合拟合度**：out ≈ normalize(α·达妮娅+(1-α)·原生)，最优 α_原生≈0.8 但**拟合度仅 0.67~0.73**（真混合应 >0.9）⇒ 输出**不是两者的线性混合，是"以达妮娅为底的第三种音色"**。这一步能把"听感像混了"与"真的是混了"分开。
- 🔑 **原生成的物理来源＝模型对 8kHz 以上完全失明**（架构级根因，两处硬证据）：
  1. `core/processors/base.py:271` `librosa.load(audio_path, sr=16000, mono=True)` ⇒ 参考音进模型前被**无条件降采样，永久丢掉 8-16k**；
  2. 声码器条件带宽实测 **fmax=8000**（§2s）。
  ⇒ 输出的 8-16kHz **只能由声码器自己的先验生成**。实测（`tools/_band_attribution.py`）：真源 8-12k 相对本带 **-6.6dB**、8k+ 能量占比 **5.27%**；全部产出 **-15.8~-24.6dB / 0.10~0.22%** ⇒ 高频比真人低 **9~18dB**、占比只有真人的 **1/20~1/50**。人耳判音色对高频质感极敏感 ⇒ 这就是"模型自己的嗓音质地"（听感上的"原生味"），**不是身份串味**。
- 🔑 另两成来自模型固有但不可通过参考音解决：声码器过平滑（6-8k 能量比真源低 ~3 倍，§2s）；**韵律/断句/呼吸完全由 LLM 决定，参考音不传递韵律**。
- ⚠️ **唯一配置层残留**（建议清理，零风险，预期收益小）：`apps/server/config.json` → `audio.ref_audio_path="assets/ref_audio/ref_minicpm_signature.wav"`，被 `cpp_backend.py:1456` 在**每次 omni_init** 当 `voice_audio` 下发；而 `cpp_backend.py:1616` 在**模式切换时必走完整重初始**（通话 2→1 档），日志实锤 22:40:54 先原生、22:41:10 才 prefilling 我们的。修法优先级：① 启动 worker 加 `--ref-audio-path`（worker.py:2398 优先于 cfg）② 改 config.json 该字段（⚠️ 会同时改掉**所有角色**的默认音色，非达妮娅角色会被带偏）。
- 可复现工具链：`_calib_spk.py`（刻度）→ `_run_mix_ablation.py`（四格）→ `_mix_forensics.py`（几何）→ `_band_attribution.py`（频段归属）→ `_make_band_pack.py`（`聆听_v4_频段归属/` 听感定位：真人限带8k vs 克隆限带8k）。⚠️ 消融会临时替换 live 缓存，脚本已带备份+finally还原+md5 校验（live v2a md5 `f3c56d7099ed`）。
"""


def main():
    s = io.open(MEM, encoding="utf-8").read()
    if "### 2ah" in s:
        print("已存在 §2ah，跳过")
    else:
        io.open(MEM, "a", encoding="utf-8").write(SEC)
        print("MEMORY.md 追加 §2ah，%d -> %d bytes" % (len(s.encode("utf-8")),
                                                 len(io.open(MEM, encoding="utf-8").read().encode("utf-8"))))

    entry = """
## __TS__ 混合音色定性：不是串味，是模型对 8k 以上失明
- 建刻度：同人上限 0.728／异人下限 0.195；克隆产出 0.7103 ⇒ 身份迁移达 96.7%。
- 2×2 消融（缓存×参考音）：声码器缓存主控（原生缓存→cos↔达妮娅塌到 0.13~0.23），参考音次控（+0.06~+0.09）；线性混合拟合度仅 0.67~0.73 ⇒ 输出是"以达妮娅为底的第三种音色"，不是混合体。
- 架构级根因：`base.py:271` librosa 把参考音降到 16k + 声码器条件 fmax=8000 ⇒ 8-16k 全由模型先验生成。实测产出 8-12k 比真人低 9~18dB、8k+ 能量占比仅真人 1/20~1/50。
- 配置残留 1 处：config.json 原生签名音在每次 omni_init 被当 voice_audio 下发（worker.py:1616 模式切换必走），建议改 --ref-audio-path。
- 新增工具：_calib_spk / _run_mix_ablation + gen_ablation_mix / _mix_forensics / _band_attribution / _make_band_pack（聆听_v4_频段归属）。
""".replace("__TS__", TS)
    cur = io.open(LOG, encoding="utf-8").read() if __import__("os").path.exists(LOG) else ""
    io.open(LOG, "a", encoding="utf-8").write(entry)
    print("日志追加完成")


if __name__ == "__main__":
    main()
