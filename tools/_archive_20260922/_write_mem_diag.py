# -*- coding: utf-8 -*-
"""把本轮（重要）发现写入今日日志：CosyVoice2 产出"噢哦啊哦"的真因 + MEMORY.md 事故复盘。

⚠️ 两文件均按各自换行实测处理：
   - 2026-09-22.md : 纯 LF（append）
   - MEMORY.md     : CRLF 且含孤立 surrogate ⇒ 二进制 append，绝不整体重写
"""
import os
import shutil
import time

D = r"G:\ai对话\.workbuddy\memory"
LOG = os.path.join(D, "2026-09-22.md")
MEM = os.path.join(D, "MEMORY.md")

LOG_BLOCK = """

---

## 12:2x 【重大纠错】所有 CosyVoice2 产出都是坏的（"噢哦啊哦"），与我的修改无关

### 用户反馈
「音频根本不是人说话，搁那噢哦啊哦的一直，每个都这样，修复反而比不修复更差了貌似」

### 铁证（一把尺子量全部：谱平坦度 flat / 95% 能量带宽 f95）
```
类别                                      flat        f95
真人参考音（达妮娅）                       0.0139~0.0250   1828~1992   <- 正常
双工原生产出（APP_A / A_FIXED / 聆听_v3）   0.002 ~ 0.008   1000~1430   <- 正常
CosyVoice2 全部产出（08-15 至今）          0.0000 ~ 0.0001  516~867    <- 全坏
```
flat 低 100 倍 + f95 仅 820Hz ＝ **纯周期嗡鸣、零辅音**，即持续哼鸣。

### 🔴 三个必须记住的结论
1. **不是我的修改造成的**：`08-15 00:08 对比_cosy_短句.wav` 就已经 `f95=750Hz`。
   ⇒ 自我从 08-15 起用 CosyVoice2 生成的**每一个**样本都是坏的，此前从未察觉。
2. **上一轮 A/B 结论作废**：`99_对照_修复前16k` 与 `99b_对照_修复后24k` **两段都坏**
   ⇒ 在坏音频上比 "cos 0.5068 vs 0.5225" 毫无意义。
3. **回滚不能解决**：回滚到 `bak_sr24k_*` 后重跑，仍 `flat=0.0001 / f95=820Hz`（3/3 全坏）。

### 根因（直接调用 API 复现，异常被服务端吞掉）
```
RuntimeError: mat1 and mat2 must have the same dtype, but got Float and BFloat16
    at transformers/models/qwen2/modeling_qwen2.py:206  self.q_proj(hidden_states)
另：token2wav  "Calculated padded input size per channel: (2). Kernel size: (3)."
```
`cosyvoice_server.py:_get_tts()` 用 **`fp16=True`** 加载；LLM 某处传 float32 输入 ⇒ 前向崩溃。
而 `_pcm_stream` / `tts` 里的 `except Exception: traceback.print_exc(); return`
**把异常吞掉**，且 `stream=True` 已先吐出参考音的 176 个 prompt token
⇒ 声码器把这段 prompt 拉长到 20s ＝ 用户听到的"噢哦啊哦"。
🔑 **正是本仓最大一类隐性 bug：`log`/`except` 吞错 + 静默容错**（与心法 §0 完全吻合）。

### 已完成动作
- ✅ 回滚 `ref_audio_util.py` → md5 `be3ac34ae179`；`cosyvoice_server.py` → md5 `66c01654b053`
  （备份保留 `.baad_<ts>` 供复盘；`py_compile` 通过）
- ✅ 确认 HTML/服务层没有其它改动残留
- ⏳ 正在验证 `fp16=False` 是否产出正常语音（`tools/_verify_fp16_root.py`）

### 待修（下一步）
① `cosyvoice_server.py` 的 `fp16=True` → 需确认 fp16 修好后是否正常，否则默认关 fp16
② `_pcm_stream` 与 `/tts` 的 `except` 必须**上抛/落日志**，不能静默 `return`
③ 修完后用 `flat>0.002 且 f95>1200` 作为**产出合格线**，不达标不得交付
    
### 🔑 新增的通用判据（写入技能）
**任何 TTS 产出的第一道体检＝`flat` 与 `f95`**：
- `flat < 0.0005` ⇒ 纯音调/无辅音（坏）
- `f95 < 1200Hz` ⇒ 高频零内容（坏）
不需要听、不需要标注，两个数就能判死。**交付音频前必须先跑这一步。**
"""

MEM_BLOCK = (
    "- **2as\uff08\u4fee\u6b63\u7248\uff09CosyVoice2 \u4ea7\u51fa\u5168\u574f\uff1a\"\u5662\u54e6\u554a\u54e6\"\u7684\u771f\u56e0\uff0809-22 \u5b9a\u6848\uff09**\uff1a"
    "**\u5224\u636e\uff08\u514d\u542c\u97f3\u9891\uff0c\u4e24\u4e2a\u6570\u5c31\u80fd\u5224\u6b7b\uff09**\uff1a"
    "`flat`\uff08\u8c31\u5e73\u5766\u5ea6\uff09< **0.0005** \u4e14 `f95`\uff0895%\u80fd\u91cf\u5e26\u5bbd\uff09< **1200Hz** \u21d2 \u7eaf\u5468\u671f\u55e7\u9e23\u3001\u96f6\u8f85\u97f3\u3002"
    "\u5b9e\u6d4b\uff1a\u771f\u4eba\u53c2\u8003\u97f3 flat 0.0139~0.0250 / f95 1828~1992\uff5c\u53cc\u5de5\u539f\u751f\u4ea7\u51fa 0.002~0.008 / 1000~1430\uff5c"
    "**CosyVoice2 \u5168\u90e8\u4ea7\u51fa 0.0000~0.0001 / 516~867**\uff5c`08-15 00:08 \u5bf9\u6bd4_cosy_\u77ed\u53e5.wav` \u5df2 f95=750 \u21d2 **\u81ea 08-15 \u8d77\u6bcf\u4e2a CV2 \u6837\u672c\u90fd\u662f\u574f\u7684\uff0c\u6b64\u524d\u4ece\u672a\u5bdf\u89c9**\u3002"
    "**\u6839\u56e0**\uff1a`cosyvoice_server.py:_get_tts()` \u7528 **`fp16=True`** \u52a0\u8f7d\uff0cLLM \u524d\u5411\u629b "
    "`RuntimeError: mat1 and mat2 must have the same dtype, but got Float and BFloat16`\uff08qwen2 modeling:206 `self.q_proj`\uff09\uff1b"
    "\u800c `_pcm_stream`/`/tts` \u7684 `except Exception: traceback.print_exc(); return` **\u628a\u5f02\u5e38\u541e\u6389**\uff0c"
    "\u4e14 `stream=True` \u5df2\u5148\u5410\u51fa\u53c2\u8003\u97f3\u7684 176 \u4e2a prompt token \u21d2 \u58f0\u7801\u5668\u628a\u5b83\u62c9\u957f\u5230 20s \u21d2 **\u5c31\u662f\u201c\u5662\u54e6\u554a\u54e6\u201d**\u3002"
    "**\u4e0e\u672c\u8f6e 16k/24k \u4fee\u6539\u65e0\u5173**\uff1a\u56de\u6eda\u540e\u91cd\u8dd1\u4ecd 3/3 `flat=0.0001/f95=820`\u3002"
    "**\u4e0a\u4e00\u8f6e A/B \u7ed3\u8bba\u4f5c\u5e9f**\uff08\u4e24\u6bb5\u90fd\u574f\uff09\u3002"
    "\u5f85\u4fee\uff1a\u2460 \u786e\u8ba4/\u5173\u6389 `fp16` \u2461 `except` \u5fc5\u987b\u4e0a\u629b\u6216\u843d\u65e5\u5fd7\uff0c\u4e0d\u80fd\u9759\u9ed8 return \u2462 "
    "**\u4ea4\u4ed8\u524d\u5fc5\u8dd1 `flat`/`f95` \u5408\u683c\u7ebf\uff08flat>0.002 \u4e14 f95>1200\uff09**\u3002"
    "\u5de5\u5177 `tools/_verify_fp16_root.py`\u3002\u2192 \u00a725.15\r\n"
)


def append_bin(path, block_bytes, probe):
    b = open(path, "rb").read()
    if probe in b:
        print("  skip (probe found):", os.path.basename(path))
        return
    if b.count(b"\r\r"):
        raise SystemExit("  abort: \\r\\r in " + path)
    bak = path + ".bak_diag_" + time.strftime("%Y%m%d_%H%M%S")
    shutil.copy2(path, bak)
    need = not b.endswith(b"\r\n") and path.endswith(".md")
    payload = (b"\r\n" if need else b"") + block_bytes
    with open(path, "ab") as f:
        f.write(payload)
    b2 = open(path, "rb").read()
    assert b2[:len(b)] == b, "  original modified!"
    assert b2.count(b"\r\r") == 0, "  \\r\\r introduced!"
    print(f"  {os.path.basename(path)}: {len(b)} -> {len(b2)}  (bak {os.path.basename(bak)})")


def main():
    print("append log (LF):")
    append_bin(LOG, LOG_BLOCK.encode("utf-8"), "【重大纠错】".encode("utf-8"))
    print("append MEMORY (CRLF, binary):")
    append_bin(MEM, MEM_BLOCK.encode("utf-8"), "2as\uff08\u4fee\u6b63\u7248\uff09".encode("utf-8"))


if __name__ == "__main__":
    main()
