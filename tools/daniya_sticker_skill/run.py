"""启动器：在项目根目录运行 `python run.py` 即可。

需要 LLM（智谱）能力时，先设置环境变量：
  export ZHIPU_API_KEY=你的key
（Windows PowerShell:  $env:ZHIPU_API_KEY="你的key"）
不设置也能跑——只是自动描述/语义检索/真人感选贴会降级为本地模式。
"""
import sys
from pathlib import Path

# 保证项目根在 sys.path（脚本目录会被自动加入，这里显式再保险一次）
sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn
from backend import config
from backend.main import app

if __name__ == "__main__":
    print(f"表情包技能包测试服务: http://{config.HOST}:{config.PORT}")
    uvicorn.run(app, host=config.HOST, port=config.PORT, reload=False)
