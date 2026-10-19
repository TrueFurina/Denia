"""配置：路径、LLM（测试期=智谱免费端口）、已知角色、资源广场索引。

接入达妮娅时：
- ZHIPU_* 改为读取达妮娅「用户设置的主 LLM/VLM」即可（见 llm.py 的 DaniyaLLMBackend 桩）。
- KNOWN_CHARACTERS 改为从达妮娅角色名册读取。
- MARKETPLACE_INDEX_URL 可保留 astrbot 公开索引，或换达妮娅自有索引。
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent          # daniya_sticker_skill/
DATA_DIR = BASE_DIR / "data"
PACKS_DIR = DATA_DIR / "packs"
FRONTEND_DIR = BASE_DIR / "frontend"

# ---- LLM（测试期：智谱免费端口）----
ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY", "")
ZHIPU_BASE_URL = os.environ.get("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
ZHIPU_TEXT_MODEL = os.environ.get("ZHIPU_TEXT_MODEL", "glm-4-flash")
ZHIPU_VISION_MODEL = os.environ.get("ZHIPU_VISION_MODEL", "glm-4v-flash")

# ---- 角色 ----
# 接入时改为从达妮娅角色名册读取
KNOWN_CHARACTERS = ["达妮娅"]

# ---- 资源广场索引（astrbot 官方「表情包广场」协议）----
# 注意：真实索引文件叫 community-index.json（不是 index.json），结构见 marketplace.py 顶部说明。
# raw.githubusercontent 在部分网络下不可达，故用 jsDelivr 镜像 + 原站双备份。
MARKETPLACE_INDEX_URL = os.environ.get(
    "MARKETPLACE_INDEX_URL",
    "https://cdn.jsdelivr.net/gh/anka-afk/astrbot-meme-pack-index@main/community-index.json",
)
MARKETPLACE_INDEX_MIRRORS = (
    "https://raw.githubusercontent.com/anka-afk/astrbot-meme-pack-index/main/community-index.json",
)

HOST = "127.0.0.1"
PORT = 8787

# ---- 自动标注 ----
# 抽帧策略：本库全是 GIF（19/24 首末帧差异显著），只取首帧会漏掉关键情绪。
#   "first"  只取首帧（旧行为）
#   "motion" 按动作差异取多帧横向拼接（实测免费模型 45%→50%，略优）
#   "mid" / "last" / "uniform" 其他策略
# 注：换强视觉模型后抽帧收益不明显（强模型读首帧就够准），故默认 motion 但不激进。
AUTOTAG_FRAMES = os.environ.get("AUTOTAG_FRAMES", "motion")
AUTOTAG_FRAMES_K = int(os.environ.get("AUTOTAG_FRAMES_K", "3"))

# 视觉模型选择：留空=用 llm.py 里配置的（测试期智谱免费）。
# 换成强模型（qwen3-vl-plus）可显著提升打标准确度。
VISION_BACKEND = os.environ.get("VISION_BACKEND", "zhipu")
