# -*- coding: utf-8 -*-
"""追加 (E) 轮记忆到今日日志（append-only）。"""
import os

P = r"G:\ai对话\.workbuddy\memory\2026-09-22.md"

ADD = """

---

## 22:5x (E) 轮：修复 16k→24k 降采样 —— 代码生效，但【实测未提升相似度】（诚实负结果）

### 做了什么（3 处改动，均已落盘 + py_compile 通过）
- `src/voice/ref_audio_util.py`：`ensure_usable_wav(path, target_sr=16000)` 新增参数（默认 16000 保兼容）；`_maybe_gain(tmp, target_sr)` 透传 `-ar`。备份 `.bak_sr24k_20260922_115958`（md5 `be3ac34ae179`）
- `src/voice/cosyvoice_server.py`：调用处改为 `ensure_usable_wav(ref_wav, target_sr=SAMPLE_RATE)`（SAMPLE_RATE=24000）。备份 `.bak_sr24k_20260922_115958`（md5 `66c01654b053`）
- 验证：`tsr=24000→sr=24000`、`tsr=16000→sr=16000`；频谱 8-12k 从 **-78.7dB（全空）恢复为 -7.8dB** ⇒ 修复**确实生效**

### 对照实验（同参考音/同句/只改链路）三项全部略差
```
配置                  F0      ΔF0     6-8k     cos↔SRC   cos↔SIG
A 修复后 tsr=24000   203.4   -36.6   -37.2     0.5068    0.2737
B 修复前 tsr=16000   198.3   -41.7   -28.3     0.5225    0.2868
真源参照             240.0     0.0   -11.1     1.0000        -
```
**机理**：CosyVoice2 音色主通道是 **16k campplus spk 向量**（`_extract_spk_embedding → load_wav(wav,16000)`），**物理上看不到 8k 以上**；只有 flow prompt 的 mel 要 24k（承韵律/内容）。⇒ 提高 flow 采样率边际贡献极小。
🔑 **可推广判据：先问「这条通道物理上能看见多少带宽」再谈优化** —— 别只看代码写着 24000 就以为高频有用。

### 新发现（更值得排查，也是「不像本人」的主因）
**CosyVoice2 零样本 ΔF0 系统性偏低 -34~-40Hz（约 -3~-4 半音）**：参考音 240Hz → 产出仅 200-207Hz。
⚠️ **方向与双工路径相反**：双工路径 ΔF0 = **+10.0**（偏高）。
👉 待查：① 参考音从长句中间截（首 0.2s 满幅 0.7758）② 只有 7.04s 太短（试 9.62s）③ 试 48k 参考音 ④ 试 speed/pitch 推理参数。

### 口径教训
双工 0.7103 vs CV2 0.51 **不是同一段音频上的同一句话**，直接比大小是错的。**A/B 必须锁「同参考音+同文本+只改一个变量」**。

### 交付物
- `11/docs/16k到24k参考音修复_实施与验收报告.md`（8,238 B / 190 行 / 纯 LF）
- `11/data/验收试听包_24k/`（8 个 wav，全部 24kHz 单声道 PCM_16）：`00_目标音色_参考音` / `01~04_克隆_*` / `05_克隆_情绪指令_慵懒` / `99_对照_修复前16k链路` / `99b_对照_修复后24k链路`
- 原始产出目录 `11/data/acceptance_24k/`
- 技能修正：`comni-voice-clone-audit` 中「先查 16k 降采样」一节已标注**已证伪**，ΔF0 偏低升为**第 1 号原因**（92,384 → 94,712 B，纯 LF）

### 待拍板
1. 是否保留本次 24k 修复（倾向保留；可回退 `.bak_sr24k_*`）—— 请听 `99` vs `99b` 定
2. 是否投入排查 ΔF0 偏低
"""

def main():
    b = open(P, "rb").read()
    if b.count(b"\r\n") != 0:
        raise SystemExit("today log is CRLF, refuse")
    if "\u8bda\u5b9e\u8d1f\u7ed3\u679c" in b.decode("utf-8"):
        print("already appended, skip")
        return
    with open(P, "ab") as f:
        f.write(ADD.encode("utf-8"))
    b2 = open(P, "rb").read()
    assert b2.count(b"\r\r") == 0
    print("today log new size", len(b2), "crr", b2.count(b"\r\r"))


if __name__ == "__main__":
    main()
