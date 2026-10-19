"""
达娅云端克隆样片生成器
======================
单独跑这个脚本，先验证云端克隆出来的声音像不像达娅，再决定接进双工。

用法（在伴侣的 python 环境里）：
  set DANIYA_TTS_PROXY=http://127.0.0.1:7890      # 你的代理（连 HF 用）
  set MODELSCOPE_SDK_TOKEN=你的魔搭token           # 可选，兜底用
  python gen_daniya_sample.py

生成的样片在 deliverables/daniya_cloud_sample.wav
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cloud_clone_bridge import synthesize

LINES = [
    "达娅在吗？我刚打完一把游戏，想跟你说说话。",
    "你今天过得怎么样，有没有想我？",
    "刚才那把有点可惜，不过下一把肯定能赢。",
]

if __name__ == "__main__":
    out_dir = Path(r"deliverables")
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, line in enumerate(LINES):
        wav = synthesize(line)
        if wav:
            dst = out_dir / f"daniya_cloud_sample_{i}.wav"
            dst.write_bytes(wav)
            print(f"✅ [{i}] {line}  ->  {dst}")
        else:
            print(f"❌ [{i}] {line}  生成失败")
