# -*- coding: utf-8 -*-
"""在 SKILL.md 的「交付物与交叉引用」之前插入「哼鸣体检」章节。

该技能文件为纯 LF，直接整体重写（无 surrogate 问题）。
"""
import os
import time
import shutil

P = r"C:\Users\Administrator\.workbuddy\skills\comni-voice-clone-audit\SKILL.md"

SECTION = """## 🔴🔴 交付任何 TTS 音频之前，先跑这道体检（2026-09-22 血案，必看）

**背景**：本项目从 2026-08-15 起用 CosyVoice2 生成的**每一个**样本都是坏的
（持续哼鸣、听懂是"噢哦啊哦"），**连续几周无人察觉**，还被拿去做 A/B 对照得出错误结论
（在两段同样坏的音频上比余弦 0.5068 vs 0.5225，毫无意义）。
教训：**听感必须先被两个客观数字拦住，不能靠"我听着还行"。**

### 判据：只看两个数（0.3s 就能算，不用听、不用标注）

```python
# 帧长 1024、hop 256、Hann 窗
flat = median( exp(mean(log(P))) / mean(P) )        # 谱平坦度
f95  = median( freq[ cumsum(P)/sum(P) >= 0.95 的首帧 ] )   # 95% 能量带宽

# 合格线
flat > 0.002   且   f95 > 1200 Hz     -> 可交付
flat < 0.0005  或   f95 < 1000 Hz     -> 判死，绝不交付
```

### 参考刻度（2026-09-22 实测，本项目）

| 类别 | flat | f95 |
|---|---|---|
| 真人参考音（达妮娅） | 0.0139 ~ 0.0250 | 1828 ~ 1992 |
| 双工原生产出（正常语音） | 0.002 ~ 0.008 | 1000 ~ 1430 |
| **CosyVoice2 全部产出（坏）** | **0.0000 ~ 0.0001** | **516 ~ 867** |

`flat` 低两个数量级 ＝ 纯周期音、零辅音噪声 ⇒ 听起来就是持续元音/哼鸣。

### ⚠️ 为什么"哼鸣"特别容易骗过耳朵

哼鸣有完整的音高、音量、甚至情绪起伏，**长度也正常**（20s 对 20s），
唯一的破绽是**没有辅音**。中文的辅音（s/sh/x/f/h）几乎全在 2kHz 以上
⇒ `f95` 掉到 820Hz 就是**辅音整段消失**的直接证据。

### 服务端「吞异常」是这类坏音频的典型制造机

`cosyvoice_server.py` 里两处：
```python
# _pcm_stream()
except Exception:
    traceback.print_exc()
    return          # <- 静默结束流，客户端拿到"半截但合法"的音频
# /tts
except Exception as e:
    traceback.print_exc()
    return _err(str(e), 500)
```
再叠加 `stream=True`：**prompt 段（参考音的 176 个 token）会先被吐出来**，
声码器把这 176 个 token 拉长到整段时长 ⇒ **正好就是"噢哦啊哦"**。

🔑 **修法**：产物侧必须校验；`except` 必须让上层知道失败，不能"返回一个听起来像音频的东西"。

### 排查「哼鸣」的固定顺序

1. **先量 `flat` / `f95`** —— 确认它确实是哼鸣而不是别的问题
2. **横向扫全项目历史产出**（`glob` 所有 wav 一起量）——
   本项目就是靠这一步发现"从 08-15 起全坏"，从而洗清"是不是我这次改坏的"
3. **回滚 + 重跑** —— 若回滚后仍坏，问题在链路本身，不在本次改动
4. **直接调 API 绕过 HTTP**，让异常**暴露出来**（服务端会吞掉）
   ```python
   from cosyvoice.cli.cosyvoice import CosyVoice2
   tts = CosyVoice2(MODEL_DIR, load_jit=False, load_trt=False, fp16=True)
   for r in tts.inference_zero_shot(text, ref_text, ref_wav, stream=False,
                                    text_frontend=False):
       ...
   ```
5. **按嫌疑逐个替换变量**：`fp16` / `ref_text` 对齐 / 采样率 / 前端

### 本轮已排除 & 已确认的嫌疑

| 嫌疑 | 结论 | 证据 |
|---|---|---|
| 我改的 16k→24k | **无关** | 回滚后重跑 3/3 仍 `flat=0.0001 / f95=820` |
| `fp16=True` | **不是唯一因** | fp16=True 不崩但产出坏；fp16=False 直接崩 `mat1/mat2 dtype` |
| `ref_text` 与参考音错配 | **不是主因** | 用 ASR 真实文本重跑，仍 `flat=0.0001 / f95=891` |
| `text_frontend=''`（缺 wetext） | **待验证** | `pip install wetext` 已装，需重启服务复测 |

### 环境坑（本轮新增）

- 🔴 `torchaudio 2.11` **移除了 `backend='soundfile'` 参数**，强制走 `torchcodec`；
  未装则 `importlib` 报 `ImportError: TorchCodec is required`。
  ⇒ `cosyvoice_server.py` 已用 monkey-patch 绕过（`_torchaudio_load_soundfile`）；
  自己写诊断脚本时要**复制同一段补丁**，否则 `load_wav` 全线失败。
- 🔴 `cosyvoice_venv` 原生**缺 `wetext`** ⇒ 前端降级为 `''`，日志打印
  `INFO no frontend is avaliable`。已 `pip install wetext`（带 kaldifst 等 6 个依赖）。
- ⚠️ 诊断脚本里 `CosyVoice2(..., fp16=False)` 会**直接崩**（`Float vs BFloat16`），
  必须用 `fp16=True` 才能跑通。

"""


def main():
    b = open(P, "rb").read()
    assert b.count(b"\r\n") == 0, "SKILL.md should be LF-only"
    t = b.decode("utf-8")
    if "交付任何 TTS 音频之前" in t:
        print("already inserted, skip")
        return
    anchor = "### 交付物与交叉引用"
    i = t.find(anchor)
    if i < 0:
        raise SystemExit("anchor not found")
    shutil.copy2(P, P + ".bak_nonspeech_" + time.strftime("%Y%m%d_%H%M%S"))
    new = t[:i] + SECTION + t[i:]
    with open(P, "wb") as f:
        f.write(new.encode("utf-8"))
    b2 = open(P, "rb").read()
    assert b2[:i] == b[:i], "prefix modified!"
    print("SKILL.md", len(b), "->", len(b2))
    print("probe:", b2.decode("utf-8").count("交付任何 TTS 音频之前"))


if __name__ == "__main__":
    main()
