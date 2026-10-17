"""
达娅云端克隆 TTS 桥接层（独立开发/样片工具，不接入软件运行时）
===============================================================
用途：拦截 omni 双工产出的文本 -> 用你自己的云端 CosyVoice2 克隆达娅声线 -> 返回 wav 字节

⚠️ 本文件是**独立开发脚本**（仅被 gen_daniya_sample.py 调用），不接入软件运行时，
   不会被伴侣在通话/陪伴中静默调用。所有第三方端点都必须由你自己通过环境变量显式配置，
   默认不再写死任何免费第三方服务，杜绝「静默用免费共享服务」的隐患。

后端优先级（均需你自己的资源 + 显式环境变量，未配置则跳过）：
  1) HuggingFace CosyVoice2 Space  —— 设 DANIYA_HF_SPACE + 代理 DANIYA_TTS_PROXY
  2) 魔搭 ModelScope CosyVoice2 API —— 设 DANIYA_MS_MODEL + MODELSCOPE_SDK_TOKEN

注意：
  - "零样本克隆" 仍需参考音频：H:\\daniya_ref.wav（达娅干净人声，16k 单声道）
  - 同一文本会写盘缓存，显著降低调用次数
  - 代理通过环境变量 DANIYA_TTS_PROXY 注入（如 http://127.0.0.1:7890）

依赖：pip install gradio-client requests
"""

import os
import io
import base64
import hashlib
from pathlib import Path

# ----------------------------- 配置（必须由用户显式提供，不写死任何免费第三方） -----------------------------
# 路径默认值一律相对仓库根，不写死任何本机盘符：项目曾位于 G:\ai对话\11，
# 搬家到 D:\Denia 后旧默认值让 import 直接 FileNotFoundError（G:\ 不存在）。
_ROOT = Path(__file__).resolve().parents[2]
DANIYA_REF = os.getenv("DANIYA_REF_WAV") or str(_ROOT / "assets" / "voices" / "daniya_ref.wav")
CACHE_DIR = Path(os.getenv("DANIYA_TTS_CACHE") or (_ROOT / "deliverables" / "tts_cache"))
# 以下两个端点**必须**由你自己的环境变量显式提供；默认留空，绝不写死免费第三方服务。
HF_SPACE = os.getenv("DANIYA_HF_SPACE", "").strip()        # 你自己的 CosyVoice2 Space（需代理）
MODESCOPE_MODEL = os.getenv("DANIYA_MS_MODEL", "").strip()  # 你自己的 ModelScope 模型
MODESCOPE_TOKEN = os.getenv("MODELSCOPE_SDK_TOKEN", "").strip()  # 你自己的 token
PROXY = os.getenv("DANIYA_TTS_PROXY", "").strip()  # 如 http://127.0.0.1:7890

if PROXY:
    os.environ["HTTPS_PROXY"] = PROXY
    os.environ["HTTP_PROXY"] = PROXY

def _ensure_cache_dir() -> Path:
    """惰性创建缓存目录。

    原实现在 import 阶段就 mkdir，导致「只要盘符不可用，连 import 都失败」——
    一个可选的 TTS 辅助模块不该有能力在导入时炸掉调用方。
    """
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:  # noqa: BLE001  目录不可写时降级为不缓存，调用方仍能合成
        print(f"[TTS] 缓存目录不可用（{CACHE_DIR}）：{e}，本次不写缓存")
    return CACHE_DIR


def _configured() -> str:
    """返回未配置端点时的明确提示；两个后端都未显式配置则调用方应直接跳过。"""
    if not HF_SPACE and not (MODESCOPE_MODEL and MODESCOPE_TOKEN):
        return ("未配置任何云端克隆后端：请设置 DANIYA_HF_SPACE（HF Space）"
                "或 DANIYA_MS_MODEL + MODELSCOPE_SDK_TOKEN（魔搭）后重试；"
                "本工具不再写死免费第三方，需你自己的资源。")
    return ""


def _cache_path(text: str) -> Path:
    h = hashlib.md5(text.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{h}.wav"


def synthesize(text: str, use_cache: bool = True) -> bytes | None:
    """返回 wav 字节；两个后端都失败则返回 None"""
    if use_cache:
        cp = _cache_path(text)
        if cp.exists():
            return cp.read_bytes()
    wav = _try_hf(text) or _try_modelscope(text)
    if wav and use_cache:
        try:
            _ensure_cache_dir()
            _cache_path(text).write_bytes(wav)
        except OSError as e:  # noqa: BLE001  缓存失败不影响本次结果
            print(f"[TTS] 写缓存失败：{e}")
    return wav


def _try_hf(text: str) -> bytes | None:
    """HuggingFace CosyVoice2 Space（你自己的 Space，需代理）"""
    if not HF_SPACE:
        return None
    try:
        from gradio_client import Client
        client = Client(HF_SPACE)
        # 多数 CosyVoice2 Space 暴露 (text, ref_audio, ref_text) -> audio 的克隆接口
        # api_name 可能因 Space 而异，若失败请改成该 Space 实际的 fn 名
        result = client.predict(text, DANIYA_REF, "", api_name="/clone_voice")
        path = result[0] if isinstance(result, (tuple, list)) else result
        if isinstance(path, str) and os.path.exists(path):
            with open(path, "rb") as f:
                return f.read()
    except Exception as e:
        print(f"[HF:{HF_SPACE}] 失败: {e}")
    return None


def _try_modelscope(text: str) -> bytes | None:
    """魔搭 ModelScope CosyVoice2 API（你自己的模型 + token）"""
    if not MODESCOPE_MODEL or not MODESCOPE_TOKEN:
        return None
    try:
        import requests
        url = f"https://api-inference.modelscope.cn/api-inference/v1/models/{MODESCOPE_MODEL}"
        headers = {"Authorization": f"Bearer {MODESCOPE_TOKEN}"}
        with open(DANIYA_REF, "rb") as f:
            ref_b64 = base64.b64encode(f.read()).decode()
        payload = {
            "input": {
                "text": text,
                "prompt_audio": ref_b64,
                "prompt_text": "",
            },
            "parameters": {"stream": False},
        }
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        if r.status_code == 200:
            data = r.json()
            wav_b64 = (
                data.get("Data", {}).get("output_wav")
                or data.get("output", {}).get("wav")
                or data.get("wav")
            )
            if wav_b64:
                return base64.b64decode(wav_b64)
        else:
            print(f"[ModelScope] {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[ModelScope] 失败: {e}")
    return None


if __name__ == "__main__":
    sample = "达娅在吗？我刚打完一把游戏，想跟你说说话。"
    out = synthesize(sample)
    if out:
        dst = _ensure_cache_dir().parent / "daniya_cloud_sample.wav"
        dst.write_bytes(out)
        print(f"✅ 已生成达娅云端样片: {dst} ({len(out)} bytes)")
    else:
        _msg = _configured()
        print("❌ 两个后端都失败。" + (_msg if _msg else "检查：代理 PROXY / 魔搭 Token / HF Space 是否可用"))
