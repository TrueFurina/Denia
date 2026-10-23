"""MiniCPM-o-4.5 GGUF 量化包目录 + 设备感知推荐。

设计目标（2026-08-27 用户需求）：
- 用户在「双工引擎」选 local_omni 后，按本机显卡显存推荐可选量化档；
- 5080 以下（含 5070Ti/5080 的 16GB、5070 的 12GB）：默认 Q4（官方实时双工流畅基准）；
- 5090 等 24GB+：Q6/Q8 双推荐，默认 Q8 高保真；
- Q6/Q8 在 16GB 仍可选但标「偏重可能掉帧」，让用户在 UI 里自行决定；
- 每档给出魔塔(ModelScope)链接 + 可直接复制的下载命令（想下大的下大的、想下小的下小的）。

数据来源：
- 魔塔官方仓库：https://modelscope.cn/models/OpenBMB/MiniCPM-o-4_5-gguf
- 官方下载命令：modelscope download --model OpenBMB/MiniCPM-o-4_5-gguf --local_dir <DIR>
- 真实测量：本机 Q6_K.gguf = 6.72GB（文件体积）；其余档位为常见 llama.cpp 量化估算，标注"约"。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 目录（单一事实来源）
# ---------------------------------------------------------------------------

REPO_ID = "OpenBMB/MiniCPM-o-4_5-gguf"
MODELSCOPE_URL = f"https://modelscope.cn/models/{REPO_ID}"

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def default_model_dir() -> str:
    """默认模型目录：`<程序目录>/models/MiniCPM-o-4_5-gguf`（跟着程序走）。

    【2026-10-21 修·出厂缺陷】原来三处（`_build_cmd` / `download_model_quant` /
    `download_bundle`）在"用户还没选目录"时都回落到开发机的
    `H:\\minicpm models\\MiniCPM-o-4_5-gguf`。新用户没有 H 盘，于是：
      · 点下载 → `modelscope download --local_dir "H:\\minicpm models\\..."` → 直接失败；
      · 复制"可手动执行的命令" → 也是一条指着 H 盘的命令，照着敲必然失败。
    这与「出厂模板里躺着开发机绝对路径」同一类：开发机上永远是对的，只有新用户才撞。
    改成按程序所在目录推，谁装在哪就下在哪（不自动建目录，由 modelscope 自己建）。
    """
    return os.path.join(_APP_ROOT, "models", "MiniCPM-o-4_5-gguf")

# 各量化档。
#   file      : GGUF 文件名（下载到 Comni 的 model_dir 后，填进 cpp_backend.llm_model）
#   size_gb   : 文件体积（GB，约）。Q6_K 为本机实测 6.72GB，其余为估算
#   vram_gb   : 运行所需显存（GB，约）= 文件体积 + omni 子模型(vision/audio/tts) + KV 缓存
#   quality   : 质量档描述
#   min_card  : 硬下限（GB）—— 低于此卡基本跑不动（乐观基线）
#   rec_card  : 舒适推荐下限（GB）—— 达到才标记为"推荐"；999=仅手动可选
QUANTS: List[Dict[str, Any]] = [
    {
        "id": "Q4_K_M",
        "file": "MiniCPM-o-4_5-Q4_K_M.gguf",
        "size_gb": 4.8,
        "vram_gb": 7.0,
        "quality": "流畅基准·官方推荐",
        "min_card": 12,
        "rec_card": 12,
        "note": "最轻量，实时双工流畅档（官方 RTF 0.4）；低/中显存卡首选，质量略低于 Q6",
    },
    {
        "id": "Q6_K",
        "file": "MiniCPM-o-4_5-Q6_K.gguf",
        "size_gb": 6.72,  # 本机实测
        "vram_gb": 9.0,
        "quality": "均衡",
        "min_card": 16,
        "rec_card": 24,
        "note": "质量/显存平衡；24GB 卡舒适档，16GB 卡能跑但偏重、可能掉帧，不建议作默认",
    },
    {
        "id": "Q8_0",
        "file": "MiniCPM-o-4_5-Q8_0.gguf",
        "size_gb": 8.5,
        "vram_gb": 11.0,
        "quality": "高保真",
        "min_card": 16,
        "rec_card": 24,
        "note": "质量高；仅 24GB 卡舒适，16GB 卡会贴边",
    },
    {
        "id": "F16",
        "file": "MiniCPM-o-4_5-F16.gguf",
        "size_gb": 17.0,
        "vram_gb": 19.0,
        "quality": "满精度",
        "min_card": 24,
        "rec_card": 999,  # 仅手动可选，不自动推荐
        "note": "几乎无损但极大；仅 24GB+ 且不在乎占用时手动选",
    },
]

# ---------------------------------------------------------------------------
# 设备显存检测
# ---------------------------------------------------------------------------

# 标称显存档位（GiB）。nvidia-smi 的 memory.total 是**二进制 GiB 且扣掉驱动预留**，
# 实测 RTX 5080（标称 16GB）= 16303 MiB = 15.92 —— 直接拿它比整数阈值会把
# 16GB 卡判成「12GB 档」（Q6/Q8 被置灰、提示文案走 12~16 分支），用户实测踩到。
# 归一规则：检测值落在某标称档「下方 1.0GB 以内」→ 上取到该档；差得远（真 15GB
# 卡之类）保持原值不动，不虚标。
_NOMINAL_VRAM_GB = (4, 6, 8, 10, 11, 12, 16, 20, 24, 32, 40, 48, 80, 96)


def normalize_vram_gb(detected: Optional[float]) -> Optional[float]:
    """把 nvidia-smi 报的 memory.total（GiB，通常略小于标称）归一到标称容量。

    例：16303 MiB → 15.92 → 归一为 16.0（RTX 5080）。
    仅当 0 < (标称 - 检测) <= 1.0 时上取；检测不到返回 None。
    """
    if detected is None:
        return None
    try:
        v = float(detected)
    except (TypeError, ValueError):
        # 探测返回值不是数字（驱动异常文案等）→ 按"未知显存"处理
        return None
    for n in _NOMINAL_VRAM_GB:
        if 0 < (n - v) <= 1.0:
            return float(n)
    return v


def detect_total_vram_gb() -> Optional[float]:
    """返回本机 NVIDIA 独显总显存（GB，**按标称容量归一**）；检测不到返回 None。

    2026-10-12 修正：原样返回 nvidia-smi 的二进制 GiB（16GB 卡 = 15.9），
    与 QUANTS 的整数 min_card（16）比较时把 5080 判成「12GB 档」——
    Q6/Q8 被置灰、下载器显示「需要约 16GB」整列拒绝。现在统一归一到标称
    （15.9→16.0），UI 显示与档位判定都按标称卡容量走。
    """
    try:
        from src.perception.gpu_load import gpu_load
        g = gpu_load()
        if g and g.get("total_mb"):
            return normalize_vram_gb(round(g["total_mb"] / 1024.0, 1))
    except Exception:
        # 第一条路（gpu_load）失败 → 走下面的 nvidia-smi 兜底
        pass
    # 兜底：直接调 nvidia-smi
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        val = float(str(out).split("\\n")[0].strip().split(".")[0])
        return normalize_vram_gb(round(val / 1024.0, 1))
    except Exception:
        # 两条路都失败（无 N 卡/smi 不在 PATH）→ 显存未知，调用方按"不推荐档"处理
        return None


# ---------------------------------------------------------------------------
# 推荐逻辑
# ---------------------------------------------------------------------------

def recommend_quants(total_vram_gb: Optional[float]) -> Dict[str, Any]:
    """按显存分档返回可选量化包 + 推荐项 + 提示。

    返回结构（直接喂 UI）：
      {
        "vram_gb": 16.0,                 # 检测到的显存；None=未检测到
        "cloud_only": False,             # True=显存太小只能走云端
        "recommended": "Q4_K_M",         # 推荐档 id；cloud_only 时为 None
        "quants": [ {id,file,size_gb,vram_gb,quality,note,
                     modelscope_url, download_cmd, available, recommended}, ... ]
        "warning": "..."                 # 给用户的提示文案
      }
    """
    vram = total_vram_gb
    result: Dict[str, Any] = {
        "vram_gb": vram,
        "cloud_only": False,
        "recommended": None,
        "quants": [],
        "warning": "",
    }

    if vram is None:
        result["warning"] = "未检测到 NVIDIA 独显，本地双工建议走云端(realtime)。"
        result["cloud_only"] = True
        return result

    if vram < 12:
        result["cloud_only"] = True
        result["warning"] = (
            f"检测到显存约 {vram}GB（<12GB），本地全双工模型跑不动，"
            "建议双工引擎选「云端」；或仅做轻量截图陪伴。"
        )
        return result

    # 计算每个档的可用/推荐状态
    #   available   : 达到硬下限 min_card（乐观基线，能跑）
    #   recommended : 达到舒适下限 rec_card（不卡、留有余量）
    #   tight       : 能跑但没到舒适档（偏紧，建议关后台）
    #   manual_only : 仅手动可选（如 F16，rec_card=999）
    best_available = None
    recommended = None
    for q in QUANTS:
        is_available = vram >= q["min_card"]
        is_recommended = is_available and vram >= q["rec_card"]
        manual_only = is_available and q["rec_card"] >= 999
        tight = is_available and (not is_recommended) and (not manual_only)
        entry = dict(q)
        entry["available"] = bool(is_available)
        entry["recommended"] = bool(is_recommended)
        entry["tight"] = bool(tight)
        entry["manual_only"] = bool(manual_only)
        entry["modelscope_url"] = MODELSCOPE_URL
        entry["download_cmd"] = _build_cmd(q["file"])
        result["quants"].append(entry)
        if is_available:
            best_available = q["id"]
        if is_recommended:
            recommended = q["id"]  # 取最高质量推荐档

    result["recommended"] = recommended or best_available

    if vram >= 16 and vram < 24:
        result["warning"] = (
            f"显存约 {vram}GB（5070Ti/5080 等 16GB 卡）：推荐 Q4 流畅档（官方实时双工基准，RTF 0.4）；"
            f"Q6/Q8 也能跑但偏重、可能掉帧，不推荐作默认；想最顺选 Q4。"
        )
    elif vram >= 24:
        result["warning"] = (
            f"显存约 {vram}GB（5090 等 24GB+ 卡）：Q6/Q8 都舒适，推荐 Q8 高保真；"
            f"Q6 更轻更快也够用；F16 仅极限手动选。"
        )
    else:  # 12~16 之间
        result["warning"] = (
            f"显存约 {vram}GB（12GB 档）：仅 Q4 能跑且偏紧，建议关闭壁纸引擎/多余后台后再用本地双工。"
        )
    return result


def _build_cmd(file_name: str, model_dir: Optional[str] = None) -> str:
    """生成 modelscope 单文件下载命令（装到 Comni 的 model_dir）。"""
    d = model_dir or default_model_dir()
    d = d.replace('"', '\\"')
    return (
        f'modelscope download --model {REPO_ID} {file_name} '
        f'--local_dir "{d}"'
    )


def _modelscope_exe() -> Optional[List[str]]:
    """返回可调用的 modelscope CLI 前缀（列表形式）；**不可用返回 None**。

    坑1（2026-09-09 真 bug）：modelscope 包**没有** ``__main__.py``，直接
    ``python -m modelscope download`` 会报
    ``No module named modelscope.__main__; 'modelscope' is a package and cannot
    be directly executed`` 并以**返回码 1** 退出——这就是此前自动下载永远失败、
    而用户手动 ``modelscope download ...`` 却正常的根因。

    正确且稳健的调用顺序：
      1) 优先用安装时生成的 console-script 启动器 ``<py>/Scripts/modelscope.exe``
         （与用户手动敲的 ``modelscope download`` 完全等价）；
      2) 兜底用 ``python -m modelscope.cli.cli download``
         （cli.py 自带 ``if __name__ == '__main__'`` 入口）。

    坑2（2026-10-13 真 bug，截图"点了下载无法下载"）：本机 site-packages 里的
    ``modelscope`` 是一个**残缺命名空间包**（无 ``__init__.py``、无 ``cli`` 子模块，
    疑似卸载/安装中断残留）。``import modelscope`` 能成功 → 旧的可用性守卫被骗过，
    落到 ``-m modelscope.cli.cli`` 后必然 ``ModuleNotFoundError: No module named
    'modelscope.cli'``。所以这里**必须真实探测 CLI 模块是否存在**（find_spec），
    而不是只看包能不能 import；探测失败返回 None，由调用方走直链下载兜底。
    """
    py_dir = os.path.dirname(sys.executable)
    cand = os.path.join(py_dir, "Scripts", "modelscope.exe")
    if os.path.isfile(cand):
        return [cand]
    try:
        import importlib.util
        if importlib.util.find_spec("modelscope.cli.cli") is not None:
            return [sys.executable, "-m", "modelscope.cli.cli"]
    except Exception:  # noqa: BLE001
        pass
    return None


class _CancelHandle:
    """直链下载的取消句柄：暴露 terminate()/kill() 以兼容 stop_model_download
    对 Popen 的终止逻辑（句柄未必是进程，事件即可协作取消）。"""

    def __init__(self) -> None:
        self.ev = threading.Event()

    def terminate(self) -> None:
        self.ev.set()

    def kill(self) -> None:
        self.ev.set()


def _direct_url(rel: str) -> str:
    return f"https://www.modelscope.cn/models/{REPO_ID}/resolve/master/{rel}"


def _direct_download_file(
    rel: str,
    model_dir: str,
    cancel_ev: Any = None,
    on_progress: Optional[Any] = None,
    log: Optional[Any] = None,
) -> bool:
    """魔塔直链单文件下载（requests 流式 + Range 断点续传），不依赖 modelscope 包。

    - 写 ``<file>.part``，完成后原子改名成目标文件；
    - 服务器返回 206 → 从已有 .part 续传；返回 200（不支持 Range）→ 从头下；
    - 每秒回调一次 ``on_progress(done, total, speed_bps)``（进度条用）；
    - ``cancel_ev`` 被置位 → 立即返回 False 且 **.part 保留**（暂停=断点续传语义）；
    - 失败返回 False（.part 同样保留，重试可续传）。
    """
    import time as _time

    import requests

    url = _direct_url(rel)
    target = os.path.join(model_dir, rel)
    part = target + ".part"
    os.makedirs(os.path.dirname(target) or model_dir, exist_ok=True)
    try:
        # 自动重试：网络中断/CDN 掐断每轮都从 .part 已写字节重新发起 Range 续传，
        # 最多 6 轮；服务器不认 Range（一直 200）则每轮从头下（保正确性优先）。
        for attempt in range(1, 7):
            offset = os.path.getsize(part) if os.path.isfile(part) else 0
            headers = {"Range": f"bytes={offset}-"} if offset > 0 else {}
            if log:
                log((f"检测到断点（{offset/1024/1024:.0f}MB），续传中…" if offset > 0
                     else ("重试下载（第 %d/6 轮）…" % attempt if attempt > 1 else "连接魔塔直链…")))
            try:
                with requests.get(url, headers=headers, stream=True,
                                  timeout=(15, 60), allow_redirects=True) as r:
                    resume = (r.status_code == 206)
                    if not resume and offset > 0:
                        offset = 0  # 服务器不支持 Range（200），从头下
                    if r.status_code not in (200, 206):
                        if log:
                            log(f"直链下载失败：HTTP {r.status_code}")
                        return False
                    if resume:
                        cr = r.headers.get("Content-Range", "")
                        total = int(cr.rsplit("/", 1)[-1]) if "/" in cr else 0
                    else:
                        total = int(r.headers.get("Content-Length", "0") or 0)
                    done = offset if resume else 0
                    last_done, last_t = done, _time.time()
                    mode = "ab" if resume else "wb"
                    with open(part, mode) as f:
                        for chunk in r.iter_content(chunk_size=512 * 1024):
                            if cancel_ev is not None and cancel_ev.is_set():
                                return False  # 已被暂停/取消：.part 保留，状态由 stop 写
                            if not chunk:
                                continue
                            f.write(chunk)
                            done += len(chunk)
                            now = _time.time()
                            if on_progress and now - last_t >= 1.0:
                                dt = max(0.01, now - last_t)
                                spd = (done - last_done) / dt
                                try:
                                    on_progress(done, total, spd)
                                except Exception:  # noqa: BLE001
                                    pass
                                last_done, last_t = done, now
                if cancel_ev is not None and cancel_ev.is_set():
                    return False
                os.replace(part, target)
                return True
            except (requests.exceptions.RequestException, OSError) as e:
                # 网络层异常：.part 已保留本次写入，下一轮从断点续传
                if cancel_ev is not None and cancel_ev.is_set():
                    return False
                have = os.path.getsize(part) / 1024 / 1024 if os.path.isfile(part) else 0
                if log:
                    log(f"网络中断（已下 {have:.0f}MB，{e}），{'自动续传重试' if attempt < 6 else '重试次数用尽'}…")
                _time.sleep(min(2 * attempt, 6))
        return False
    except Exception as e:  # noqa: BLE001
        if log:
            log(f"直链下载异常（{e}）。已保留断点，重新下载可续传。")
        return False


def get_model_catalog(model_dir: Optional[str] = None) -> Dict[str, Any]:
    """给 UI 的完整载荷：设备显存 + 推荐 + 各档链接/命令。"""
    vram = detect_total_vram_gb()
    payload = recommend_quants(vram)
    if model_dir:
        for q in payload["quants"]:
            q["download_cmd"] = _build_cmd(q["file"], model_dir)
    payload["repo_id"] = REPO_ID
    payload["modelscope_url"] = MODELSCOPE_URL
    payload["model_dir"] = model_dir
    return payload


# ---------------------------------------------------------------------------
# 下载（尽力自动 + 始终返回命令供手动）
# ---------------------------------------------------------------------------

_download_state: Dict[str, Any] = {}
_download_lock = threading.Lock()

# 并发下载守卫：同一 (quant_id, model_dir) 只允许一个进程在跑，避免「自动准备」与
# 「引导页手动点下载」同时触发导致两个 modelscope 进程写同一个 .incomplete 文件互相踩。
_active_dl: Dict[tuple, str] = {}


def _dl_key(quant_id: str, model_dir: str) -> tuple:
    return (str(quant_id), os.path.normcase(os.path.abspath(model_dir or "")))


def _dl_register(quant_id: str, model_dir: str, task_id: str) -> None:
    with _download_lock:
        _active_dl[_dl_key(quant_id, model_dir)] = task_id


def _dl_unregister(quant_id: str, model_dir: str) -> None:
    with _download_lock:
        _active_dl.pop(_dl_key(quant_id, model_dir), None)


def _dl_active_task(quant_id: str, model_dir: str) -> Optional[str]:
    with _download_lock:
        return _active_dl.get(_dl_key(quant_id, model_dir))


def _load_proactive_cfg() -> dict:
    """返回 proactive 配置段（dict）。

    优先运行时配置单例 config._CFG（主程序已在启动时挂载）；缺失时从磁盘
    config.yaml 兜底。旧实现直接 `from config import _CFG`，但 config 模块
    从未定义 _CFG，该 import 抛 ImportError 被吞 → _comni_model_dir 永远
    返回 None（UI 模型就绪度恒「未就绪」、首启自动下载被跳过）。现健壮解析。
    """
    try:
        from config import _CFG
        if _CFG is not None:
            return (_CFG.raw.get("proactive", {}) or {})
    except Exception:  # noqa: BLE001
        pass
    try:
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        p = os.path.join(root, "config.yaml")
        if os.path.isfile(p):
            try:
                import yaml  # noqa: WPS433
                with open(p, "r", encoding="utf-8") as f:
                    return (yaml.safe_load(f) or {}).get("proactive", {}) or {}
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    return {}


def _comni_server_dir(pro: dict) -> str:
    """解析 Comni server 目录（含 config.json）。

    优先 proactive.local_omni_engine.server_dir；缺失时从 duplex_local_omni_exe
    推导（Comni.exe 同级的 _internal/resources/apps/server）——与 OwnLlamaServerEngine
    实际拉起 worker/gateway 的路径一致，保证 app 侧读到的 model_dir 与引擎运行时一致。
    """
    server_dir = (pro.get("local_omni_engine", {}) or {}).get("server_dir", "") or ""
    if not server_dir:
        exe = (pro.get("duplex_local_omni_exe") or "").strip()
        if exe and os.path.isfile(exe):
            server_dir = os.path.join(os.path.dirname(exe),
                                      "_internal", "resources", "apps", "server")
    return (server_dir or "").strip()


def _comni_model_dir() -> Optional[str]:
    """从 Comni 的 config.json 读 cpp_backend.model_dir。"""
    try:
        pro = _load_proactive_cfg()
        server_dir = _comni_server_dir(pro)
        if not server_dir:
            return None
        cfg_path = os.path.join(server_dir, "config.json")
        if not os.path.isfile(cfg_path):
            return None
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("cpp_backend", {}).get("model_dir") or None
    except Exception:  # noqa: BLE001
        pass
    return None


def _set_comni_llm_model(model_dir: str, file_name: str) -> bool:
    """下载完成后回填 Comni config.json 的 cpp_backend.llm_model。"""
    try:
        pro = _load_proactive_cfg()
        server_dir = _comni_server_dir(pro)
        cfg_path = os.path.join(server_dir, "config.json") if server_dir else ""
        if not cfg_path or not os.path.isfile(cfg_path):
            return False
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("cpp_backend", {})["llm_model"] = file_name
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:  # noqa: BLE001
        return False


def _run_download(quant_id: str, model_dir: str, task_id: str) -> None:
    """后台线程：尝试用 modelscope 下载指定 GGUF；结果写入 _download_state。

    2026-09-09 增强：用 subprocess.Popen + 轮询文件大小，给前端提供
    实时百分比、下载速度（MB/s）和预估剩余时间（s）。
    """
    import time

    with _download_lock:
        _download_state[task_id] = {"status": "running", "log": "准备下载…"}

    # 任务上下文（写进状态里，供停止/卸载定位文件；file 在拿到 quant 后补）
    _ctx = {"model_dir": model_dir, "file": "", "quant_id": quant_id}

    def setlog(msg: str, status: str = "running", **extra) -> None:
        with _download_lock:
            payload = {"status": status, "log": msg, **_ctx, **extra}
            _download_state[task_id] = payload

    quant = next((q for q in QUANTS if q["id"] == quant_id), None)
    if quant is None:
        setlog(f"未知量化档: {quant_id}", "error")
        return

    file_name = quant["file"]
    _ctx["file"] = file_name
    target = os.path.join(model_dir, file_name)
    if os.path.isfile(target):
        setlog(f"已存在，跳过下载：{file_name}", "done", overall_pct=100)
        _set_comni_llm_model(model_dir, file_name)
        return

    # modelscope CLI 真实可用性探测（import modelscope 会骗过守卫——假命名空间包，
    # 2026-10-13 截图"点了下载无法下载"的根因）；CLI 不可用 → 直链下载兜底。
    exe_prefix = _modelscope_exe()
    if exe_prefix is None:
        setlog("modelscope CLI 不可用（本机安装残缺），改用魔塔直链下载…", "running")
        handle = _CancelHandle()
        _dl_proc_set(task_id, handle)

        def _prog(done: int, total: int, spd: float) -> None:
            pct = min(99.0, done / total * 100) if total > 0 else 0.0
            eta = int((total - done) / spd) if (spd > 0 and total > 0) else None
            setlog(
                f"下载中：{file_name} ｜ {done/1024/1024:.1f}MB / {total/1024/1024/1024:.2f}GB"
                f" ｜ {spd/1024/1024:.1f} MB/s"
                + (f" ｜ 约 {eta//60} 分" if eta else ""),
                "running",
                overall_pct=pct, speed_mbps=spd / 1024 / 1024, eta_s=eta,
                phase="downloading",
            )

        ok = _direct_download_file(file_name, model_dir, handle.ev,
                                   on_progress=_prog)
        _dl_proc_pop(task_id)
        _dl_unregister(quant_id, model_dir)
        if ok:
            _set_comni_llm_model(model_dir, file_name)
            setlog(
                f"下载完成：{file_name}。已写入 Comni 配置，重启引擎后生效。",
                "done", overall_pct=100, phase="done",
            )
        elif handle.ev.is_set():
            return  # 暂停/取消：终态已由 stop_model_download 写入，勿覆盖
        else:
            setlog(
                f"直链下载失败（网络中断）。已保留断点，重新点击会从断点续传。"
                f"也可手动运行：{_build_cmd(file_name, model_dir)}",
                "manual", phase="error",
            )
        return

    os.makedirs(model_dir, exist_ok=True)
    total_bytes = int(quant["size_gb"] * 1024 * 1024 * 1024)
    setlog(f"下载中：{file_name}（约 {quant['size_gb']}GB，请稍候）…", "running")
    cmd = exe_prefix + [
        "download", "--model", REPO_ID, file_name, "--local_dir", model_dir,
    ]
    logger.info("[model_catalog] 启动下载(单文件): %s", " ".join(cmd))
    _dl_register(quant_id, model_dir, task_id)
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, errors="replace",
        )
        _dl_proc_set(task_id, proc)   # 登记句柄：供"暂停/取消"终止子进程
    except Exception as e:
        _dl_unregister(quant_id, model_dir)
        setlog(f"无法启动下载进程（{e}）。", "manual")
        return

    start_time = time.time()
    last_size = 0
    last_time = start_time
    overall_pct = 0.0
    speed_mbps = 0.0
    eta_s = None
    finished_normally = False

    # 轮询：每隔约 1.2s 检查文件大小与进程状态
    while True:
        try:
            rc = proc.poll()
        except Exception:
            rc = -1

        now = time.time()
        current_size = os.path.getsize(target) if os.path.isfile(target) else 0
        elapsed = now - last_time
        if elapsed >= 1.0:
            delta_bytes = current_size - last_size
            speed_bps = delta_bytes / elapsed if elapsed > 0 else 0
            speed_mbps = speed_bps / (1024 * 1024)
            last_size = current_size
            last_time = now
            if total_bytes > 0:
                overall_pct = min(99.0, current_size / total_bytes * 100)
                remaining = max(0, total_bytes - current_size)
                eta_s = int(remaining / speed_bps) if speed_bps > 0 else None

        setlog(
            f"下载中：{file_name} ｜ {current_size/1024/1024:.1f}MB / {quant['size_gb']}GB"
            f" ｜ {speed_mbps:.1f} MB/s" + (f" ｜ 约 {int(eta_s/60)} 分" if eta_s else ""),
            "running",
            overall_pct=overall_pct,
            speed_mbps=speed_mbps,
            eta_s=eta_s,
            phase="downloading",
        )

        if rc is not None:
            finished_normally = (rc == 0)
            break

        # 休眠并读取一点 stdout，避免 PIPE 缓冲区满（modelscope 可能写很多日志）
        try:
            proc.stdout.read(1) if proc.stdout else None
        except Exception:
            # 读 1 字节失败（管道已断=进程刚退出）→ 下循环由 rc 判结束
            pass
        time.sleep(1.2)

    # 进程已结束，读取剩余输出
    out, err = "", ""
    try:
        if proc.stdout:
            out = proc.stdout.read() or ""
        if proc.stderr:
            err = proc.stderr.read() or ""
    except Exception:
        # 读剩余输出失败只丢错误文案（成败由 rc + 目标文件存在性判定）
        pass

    if finished_normally and os.path.isfile(target):
        _set_comni_llm_model(model_dir, file_name)
        setlog(
            f"下载完成：{file_name}。已写入 Comni 配置，重启引擎后生效。",
            "done",
            overall_pct=100,
            phase="done",
        )
    elif _dl_was_stopped(task_id):
        # 已被「暂停/取消」：stop_model_download 已写好终态（paused/canceled），
        # 且已摘掉 _active_dl 条目；这里只做线程侧收尾，绝不覆盖终态。
        _dl_unregister(quant_id, model_dir)
        _dl_proc_pop(task_id)
        return
    else:
        log = (err or out or "")[:500]
        logger.warning("[model_catalog] 下载失败: %s rc=%s %s",
                       file_name, proc.returncode, log)
        setlog(
            f"下载命令执行失败（退出码 {proc.returncode}，{log}）。请手动复制命令下载，"
            f"下完把 cpp_backend.llm_model 设为 {file_name}。",
            "manual",
            phase="error",
        )
    _dl_unregister(quant_id, model_dir)
    _dl_proc_pop(task_id)


def download_model_quant(data: Dict[str, Any]) -> Dict[str, Any]:
    """触发下载（后台线程）。立即返回命令 + 任务 id；结果轮询 get_download_status。"""
    quant_id = str(data.get("quant_id") or "").strip()
    model_dir = data.get("model_dir") or _comni_model_dir()
    if not model_dir:
        model_dir = default_model_dir()
    quant = next((q for q in QUANTS if q["id"] == quant_id), None)
    if quant is None:
        return {"success": False, "error": f"未知量化档: {quant_id}"}
    # 并发守卫：同一 (quant, dir) 已在下则直接返回已有任务，避免双进程踩同一文件
    existing = _dl_active_task(quant_id, model_dir)
    if existing:
        return {
            "success": True,
            "task_id": existing,
            "quant_id": quant_id,
            "model_dir": model_dir,
            "file": quant["file"],
            "already_running": True,
            "message": "该量化包已在后台下载中（自动准备与手动点击重复触发），复用同一任务。",
        }
    import time
    task_id = f"dl_{quant_id}_{int(time.time())}"
    t = threading.Thread(
        target=_run_download, args=(quant_id, model_dir, task_id),
        daemon=True,
    )
    t.start()
    return {
        "success": True,
        "task_id": task_id,
        "quant_id": quant_id,
        "model_dir": model_dir,
        "file": quant["file"],
        "download_cmd": _build_cmd(quant["file"], model_dir),
        "modelscope_url": MODELSCOPE_URL,
        "message": "已在后台开始下载；可复制命令手动下载，或轮询下载状态。",
    }


def get_download_status(task_id: str) -> Dict[str, Any]:
    with _download_lock:
        return dict(_download_state.get(task_id, {"status": "unknown", "log": ""}))


def _partial_bytes(model_dir: str, quant_id: str) -> tuple:
    """某档**主模型 .gguf** 在目录里已落盘的字节（含 <file>.incomplete 等兄弟文件）。

    返回 (已写字节, 期望字节, 是否完整)。只算主模型这一个文件——下载器
    「继续下载」续的就是它，整套子模型（audio/tts/vision）的断点由 bundle
    任务的实时进度负责，不在此处重复报（否则分母混入子模型体积，
    「断点保留 47%」会比真实主模型进度小一半，2026-10-13 实测修正）。
    modelscope 下载途中把分块写到 ``<file>.incomplete``，下完才重命名为最终
    文件；兄弟临时文件必须算进去，否则断点提示永远是 0%。
    """
    quant = next((q for q in QUANTS if q["id"] == quant_id), None)
    if not quant:
        return (0, 0, False)
    base = quant["file"]
    target = os.path.join(model_dir, base)
    size_b = int(quant["size_gb"] * (1024 ** 3))
    got = 0
    complete = False
    if os.path.isfile(target):
        got = os.path.getsize(target)
        complete = got >= size_b * 0.8
        if complete:
            return (got, size_b, True)
    try:
        for nm in os.listdir(model_dir):
            if nm == base or not nm.startswith(base + "."):
                continue
            p = os.path.join(model_dir, nm)
            if os.path.isfile(p):
                got += os.path.getsize(p)
    except Exception:  # noqa: BLE001
        pass
    return (got, size_b, complete)


def list_download_tasks(data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """下载总览：进行中的任务 + 断点残留（全局小窗口 / 下载器切档保留共用）。

    - tasks:    ``_active_dl`` 注册表里仍活着的任务，合并 ``_download_state``
                的实时进度（status/overall_pct/speed_mbps/eta_s/log/phase）。
                前端切档/重进软件后靠它**接管**正在跑的任务，而不是丢掉 task_id
                重新点下载（2026-10-13 用户报「切回去进度没了」的根因）。
    - partials: 磁盘上有 ``<file>.incomplete`` 等残留、但没有进行中任务的档位
                （上次下载中断，重新点击可从断点续传）。
    """
    d = data or {}
    want_dir = str(d.get("model_dir") or "").strip()
    with _download_lock:
        active = dict(_active_dl)
        states = {k: dict(v) for k, v in _download_state.items()}
    tasks = []
    for (qid, mdir), tid in active.items():
        st = states.get(tid, {})
        tasks.append({
            "quant_id": qid,
            "model_dir": mdir,
            "task_id": tid,
            "kind": "bundle" if str(tid).startswith("bundle_") else "quant",
            "status": st.get("status", "running"),
            "phase": st.get("phase", ""),
            "overall_pct": st.get("overall_pct"),
            "speed_mbps": st.get("speed_mbps"),
            "eta_s": st.get("eta_s"),
            "log": st.get("log", ""),
        })
    # 断点残留扫描目录：请求的目录 + Comni 配置目录 + 进行中任务所在目录
    dirs: List[str] = []
    for cand in [want_dir, _comni_model_dir()] + [t["model_dir"] for t in tasks]:
        if cand and cand not in dirs:
            dirs.append(cand)
    partials = []
    seen = set()
    for dd in dirs:
        if not os.path.isdir(dd):
            continue
        dd_key = os.path.normcase(os.path.abspath(dd))
        for q in QUANTS:
            key = (q["id"], dd_key)
            if key in seen:
                continue
            seen.add(key)
            # 已有进行中任务的档位不重复报（那边有实时进度）
            if any(t["quant_id"] == q["id"] and
                   os.path.normcase(os.path.abspath(t["model_dir"] or "")) == dd_key
                   for t in tasks):
                continue
            got, expect, complete = _partial_bytes(dd, q["id"])
            if complete or got <= 1024 * 1024:
                continue
            partials.append({
                "quant_id": q["id"],
                "model_dir": dd,
                "partial_gb": round(got / (1024 ** 3), 2),
                "size_gb": q["size_gb"],
                "pct": round(min(99.0, got / expect * 100), 1) if expect > 0 else None,
            })
    return {"success": True, "tasks": tasks, "partials": partials}


# ---------------------------------------------------------------------------
# 下载控制：停止 / 卸载 / 目录信息（2026-10-12 · 本地模型下载器 UI 接入）
#   ⚠️ 均为**新增**，不改 _run_download 既有行为：只在其中多登记一个 Popen 句柄。
# ---------------------------------------------------------------------------

# task_id → subprocess.Popen。停止下载时靠它终止 modelscope 子进程。
_dl_procs: Dict[str, Any] = {}


def _dl_proc_set(task_id: str, proc: Any) -> None:
    with _download_lock:
        _dl_procs[task_id] = proc


def _dl_proc_pop(task_id: str) -> Any:
    with _download_lock:
        return _dl_procs.pop(task_id, None)


# 已被「暂停/取消」显式终止的任务：下载线程收尾时看到这个标记，就**不再覆盖**
# stop_model_download 写好的终态（paused/canceled）。否则线程睡醒发现 rc!=0
# 会把状态改写成 manual/error —— 用户点「暂停」却看到「下载失败」，
# 且 _active_dl 条目被清掉，UI 再也接不回这个任务（2026-10-13 实测）。
_dl_stopped: set = set()


def _dl_mark_stopped(task_id: str) -> None:
    with _download_lock:
        _dl_stopped.add(task_id)


def _dl_was_stopped(task_id: str) -> bool:
    with _download_lock:
        return task_id in _dl_stopped


def stop_model_download(task_id: str, delete_partial: bool = False) -> Dict[str, Any]:
    """停止后台下载任务。

    delete_partial=False（暂停/断点）：只终止进程，**保留已下部分**——下次点同一档
      会从断点继续（modelscope 续传），这是"暂停"的真实语义。
    delete_partial=True（彻底取消）：终止进程并尝试删除未完成的临时文件。
      只删形如 <目标文件>* 且**不等于**目标文件名的残留（.incomplete / 分片），
      已成形的 .gguf 绝不动，避免误删用户已下好的模型。
    """
    tid = str(task_id or "").strip()
    if not tid:
        return {"success": False, "error": "缺少 task_id"}
    with _download_lock:
        st = dict(_download_state.get(tid, {}))
    if not st:
        return {"success": False, "error": "任务不存在或已结束"}
    if st.get("status") == "done":
        return {"success": False, "error": "任务已完成，无需停止"}
    _dl_mark_stopped(tid)   # 下载线程收尾时据此保留 paused/canceled 终态
    proc = _dl_proc_pop(tid)
    killed = False
    if proc is not None:
        try:
            proc.terminate()
            killed = True
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
                killed = True
            except Exception:  # noqa: BLE001
                killed = False
    removed: List[str] = []
    if delete_partial:
        try:
            model_dir = st.get("model_dir") or ""
            file_name = st.get("file") or ""
            if model_dir and file_name and os.path.isdir(model_dir):
                for name in os.listdir(model_dir):
                    if not name.startswith(file_name):
                        continue
                    if name == file_name:
                        continue  # 完整文件不动
                    try:
                        os.remove(os.path.join(model_dir, name))
                        removed.append(name)
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            pass
    with _download_lock:
        # 合并写而非整字典覆盖：保留 model_dir/file 等任务上下文——
        # 否则第二次"取消并清理断点"拿不到目录，.part 永远清不掉（2026-10-13 实测）。
        prev = dict(_download_state.get(tid, {}))
        prev.update({
            "status": "canceled" if delete_partial else "paused",
            "log": ("已取消，临时文件已清理" if delete_partial else
                    "已暂停；已下部分保留，重新点击会从断点继续"),
            "phase": "canceled" if delete_partial else "paused",
        })
        _download_state[tid] = prev
    # 摘掉并发注册表条目：否则下次点同一下载会拿到这个已停的任务 id
    # （already_running 接管一个死任务 → UI 永远停在"已暂停"）。
    # 单文件任务的 state 里带 quant_id/model_dir（_ctx 合并写入）；bundle 任务
    # 由 _run_download_bundle 的 finally 自行摘除。
    qid, mdir = prev.get("quant_id") or "", prev.get("model_dir") or ""
    if qid and mdir:
        _dl_unregister(qid, mdir)
    return {"success": True, "killed": killed, "removed": removed}


def delete_model_quant(quant_id: str, model_dir: str = "") -> Dict[str, Any]:
    """卸载（删除）某个已下载的量化包文件。只删精确匹配的 .gguf，不碰其它文件。"""
    quant = next((q for q in QUANTS if q["id"] == str(quant_id or "").strip()), None)
    if quant is None:
        return {"success": False, "error": f"未知量化档: {quant_id}"}
    d = model_dir or _comni_model_dir()
    if not d:
        return {"success": False, "error": "未指定模型目录"}
    target = os.path.join(d, quant["file"])
    if not os.path.isfile(target):
        return {"success": False, "error": "该档位文件不存在（可能未下载或已删除）"}
    try:
        size_gb = round(os.path.getsize(target) / (1024 ** 3), 2)
        os.remove(target)
        return {"success": True, "deleted": quant["file"], "freed_gb": size_gb}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"删除失败: {e}"}


def model_dir_info(path: str = "") -> Dict[str, Any]:
    """下载目录体检：剩余空间 + 各档是否已存在。给下载器 UI 做空间校验与"已安装"态。"""
    d = str(path or "").strip() or _comni_model_dir() or ""
    if not d:
        return {"success": False, "error": "未指定目录"}
    info: Dict[str, Any] = {
        "success": True, "path": d,
        "exists": os.path.isdir(d),
        "free_gb": None, "total_gb": None,
        "installed": [],
    }
    # 剩余空间：目录不存在时往上找最近的已存在父目录（新建目录的场景）
    probe = d
    while probe and not os.path.isdir(probe):
        parent = os.path.dirname(probe)
        if not parent or parent == probe:
            break
        probe = parent
    try:
        if probe and os.path.isdir(probe):
            usage = shutil.disk_usage(probe)
            info["free_gb"] = round(usage.free / (1024 ** 3), 1)
            info["total_gb"] = round(usage.total / (1024 ** 3), 1)
            info["probed_path"] = probe
    except Exception:  # noqa: BLE001
        pass
    try:
        if os.path.isdir(d):
            for q in QUANTS:
                p = os.path.join(d, q["file"])
                if os.path.isfile(p):
                    info["installed"].append({
                        "id": q["id"], "file": q["file"],
                        "size_gb": q["size_gb"],
                        "on_disk_gb": round(os.path.getsize(p) / (1024 ** 3), 2),
                    })
    except Exception:  # noqa: BLE001
        pass
    return info


# ---------------------------------------------------------------------------
# 整套子模型清单（新人引导「进去就能用」的关键：主模型 + audio/vision/tts/token2wav）
# ---------------------------------------------------------------------------
# Comni 按目录名发现组件（model_hub.py: audio / tts / vision / token2wav），
# 体积取本机已验证完整副本的实测字节数（GiB），阈值 = expect * 0.8（与现有判定一致）。
OMNI_SUBMODEL_MANIFEST: List[Dict[str, Any]] = [
    {
        "key": "audio",
        "dir": "audio",
        "label": "听觉（让达妮娅听得见你）",
        "files": [{"name": "MiniCPM-o-4_5-audio-F16.gguf", "expect_gb": 0.615}],
    },
    {
        "key": "tts",
        "dir": "tts",
        "label": "语音合成（让达妮娅出声）",
        "files": [
            {"name": "MiniCPM-o-4_5-projector-F16.gguf", "expect_gb": 0.014},
            {"name": "MiniCPM-o-4_5-tts-F16.gguf", "expect_gb": 1.078},
        ],
    },
    {
        "key": "vision",
        "dir": "vision",
        "label": "视觉（让达妮娅看得见屏幕）",
        "files": [{"name": "MiniCPM-o-4_5-vision-F16.gguf", "expect_gb": 1.020}],
    },
    {
        "key": "token2wav",
        "dir": "token2wav-gguf",
        "label": "声码器（把声音合成出来）",
        "files": [
            {"name": "encoder.gguf", "expect_gb": 0.141},
            {"name": "flow_extra.gguf", "expect_gb": 0.013},
            {"name": "flow_matching.gguf", "expect_gb": 0.427},
            {"name": "hifigan2.gguf", "expect_gb": 0.078},
        ],
    },
]
# 明确排除（Comni 运行时生成，不校验不下）
OMNI_BUNDLE_EXCLUDE_NAME = ("prompt_cache.gguf",)


def bundle_files(quant_id: str) -> List[Dict[str, Any]]:
    """整套模型文件清单（主模型 + 子模型），供下载与完整性校验共用。"""
    quant = next((q for q in QUANTS if q["id"] == quant_id), None)
    if not quant:
        return []
    files: List[Dict[str, Any]] = [{
        "key": "llm", "kind": "llm", "dir": "",
        "rel": quant["file"], "name": quant["file"],
        "size_gb": quant["size_gb"],
    }]
    for m in OMNI_SUBMODEL_MANIFEST:
        for f in m["files"]:
            files.append({
                "key": m["key"], "kind": m["key"], "dir": m["dir"],
                "rel": f"{m['dir']}/{f['name']}", "name": f["name"],
                "size_gb": f["expect_gb"],
            })
    return files


def verify_bundle(model_dir: Optional[str], quant_id: str) -> Dict[str, Any]:
    """校验模型目录里整套模型是否完整（主模型 + 各子模型目录）。

    判定：主模型文件存在且 >=80% 体积；每个子模型目录 dir 下存在 *.gguf 且
    声明的文件齐全（按目录总体积兜底）。返回结构供引导页「体检」与「认领」。
    """
    if not model_dir or not os.path.isdir(model_dir):
        return {"success": True, "complete": False, "missing": ["(model_dir)"],
                "items": [], "total_gb": 0.0, "expect_total_gb": 0.0,
                "model_dir": model_dir or ""}
    items: List[Dict[str, Any]] = []
    missing: List[str] = []
    total = 0.0
    expect = 0.0
    for f in bundle_files(quant_id):
        fp = os.path.join(model_dir, f["rel"])
        ok = False
        got = 0.0
        if os.path.isfile(fp):
            try:
                got = round(os.path.getsize(fp) / (1024 ** 3), 3)
            except Exception:
                got = 0.0
            ok = got >= f["size_gb"] * 0.8
        total += got
        expect += f["size_gb"]
        if not ok:
            missing.append(f["key"])
        items.append({
            "key": f["key"], "label": f["rel"], "dir": f["dir"],
            "files": [{"name": f["name"], "size_gb": f["size_gb"],
                       "ok": ok, "expect_gb": f["size_gb"]}],
            "ok": ok, "got_gb": got, "expect_gb": f["size_gb"],
        })
    missing = sorted(set(missing))
    return {
        "success": True,
        "complete": len(missing) == 0,
        "items": items,
        "missing": missing,
        "total_gb": round(total, 2),
        "expect_total_gb": round(expect, 2),
        "model_dir": model_dir,
    }


def _run_download_bundle(quant_id: str, model_dir: str, only_missing: bool,
                         task_id: str, timeout: int = 3600) -> None:
    """后台线程：下载整套模型（主模型 + 子模型），逐文件走已验证的单文件命令。

    状态写入 _download_state，含 phase / overall_pct / speed_mbps / eta_s /
    downloaded_mb / files / log / download_cmd。用 Popen 非阻塞 + 监控线程轮询
    磁盘实际写入字节，给出真实进度/速度/预估时间（不再卡在 0%）。
    """
    import time

    def setst(**kw: Any) -> None:
        with _download_lock:
            s = dict(_download_state.get(task_id, {}))
            s.update(kw)
            _download_state[task_id] = s

    def current_bytes(files: List[Dict[str, Any]]) -> int:
        """统计 model_dir 下文件实际已写字节（含 modelscope 临时文件）。

        注意：modelscope 1.39.1 下载途中把分块写到 ``<file>.incomplete``（不是
        ``.cache``），下载完才重命名为最终文件。旧版/其它情况也可能用 ``.cache``/
        ``.tmp``/``.part``。所以凡是以 ``base + '.'`` 开头的兄弟文件都算已写字节，
        否则进度条会一直卡 0%（用户看到"下载没动"其实是真在下）。
        """
        total = 0
        for f in files:
            fp = os.path.join(model_dir, f["rel"])
            if os.path.isfile(fp):
                total += os.path.getsize(fp)
            d = os.path.dirname(fp)
            base = os.path.basename(fp)
            try:
                for nm in os.listdir(d):
                    if nm == base:
                        continue
                    if nm.startswith(base + "."):
                        total += os.path.getsize(os.path.join(d, nm))
            except Exception:  # noqa: BLE001
                pass
        return total

    setst(status="running", phase="prepare", overall_pct=0.0,
          speed_mbps=0.0, eta_s=None, downloaded_mb=0.0,
          log="准备下载…", files=[])
    _dl_register(quant_id, model_dir, task_id)
    try:
        os.makedirs(model_dir, exist_ok=True)
    except Exception as e:  # noqa: BLE001
        setst(status="error", log=f"目录无法创建：{e}")
        return

    # modelscope CLI 真实可用性探测（同 _run_download：import modelscope 会骗过守卫）。
    exe_prefix = _modelscope_exe()
    if exe_prefix is None:
        setst(log="modelscope CLI 不可用（本机安装残缺），改用魔塔直链下载…")

    files = bundle_files(quant_id)
    if only_missing:
        st = verify_bundle(model_dir, quant_id)
        miss_keys = set(st.get("missing", []))
        files = [f for f in files if f["key"] in miss_keys]
    total = len(files)
    if total == 0:
        setst(status="done", phase="verify", overall_pct=100.0,
              log="无需下载，模型已完整。")
        return
    total_bytes = int(sum(f["size_gb"] for f in files) * (1024 ** 3))
    done = 0
    manual_hint = ""

    # 进度监控线程：轮询磁盘实际写入字节
    stop_ev = threading.Event()

    def _monitor() -> None:
        last = 0
        last_t = time.time()
        while not stop_ev.is_set():
            cur = current_bytes(files)
            now = time.time()
            dt = now - last_t
            pct = min(99.0, cur / total_bytes * 100) if total_bytes > 0 else 0.0
            speed = (cur - last) / dt if dt > 0.01 else 0.0
            eta = (total_bytes - cur) / speed if speed > 0 else None
            with _download_lock:
                s = dict(_download_state.get(task_id, {}))
                if s.get("status") == "running":
                    s["overall_pct"] = round(pct, 1)
                    s["speed_mbps"] = round(speed / (1024 * 1024), 2)
                    s["eta_s"] = int(eta) if eta else None
                    s["downloaded_mb"] = round(cur / (1024 * 1024), 1)
                    _download_state[task_id] = s
            last = cur
            last_t = now
            time.sleep(0.5)

    mon = threading.Thread(target=_monitor, daemon=True)
    mon.start()
    try:
        for f in files:
            if _dl_was_stopped(task_id):
                break  # 已暂停/取消：终态由 stop_model_download 写入，勿覆盖
            rel = f["rel"]
            fp = os.path.join(model_dir, rel)
            if os.path.isfile(fp):
                try:
                    if os.path.getsize(fp) / (1024 ** 3) >= f["size_gb"] * 0.8:
                        done += 1
                        setst(phase=("llm" if f["kind"] == "llm" else "submodels"),
                              overall_pct=round(done / total * 100, 1) if total else 100.0,
                              log=f"已存在跳过：{rel}")
                        continue
                except Exception:  # noqa: BLE001
                    pass
            phase = "llm" if f["kind"] == "llm" else "submodels"
            setst(phase=phase,
                  log=f"下载中：{rel}（约 {f['size_gb']}GB）…")
            if exe_prefix is None:
                # 直链下载兜底（.part 文件会被上面的监控线程自动计入已写字节）
                handle = _CancelHandle()
                _dl_proc_set(task_id, handle)
                ok = _direct_download_file(rel, model_dir, handle.ev,
                                           log=lambda m: setst(log=m))
                _dl_proc_pop(task_id)
                if handle.ev.is_set():
                    break  # 已暂停/取消：终态由 stop_model_download 写入
                if ok:
                    done += 1
                    setst(overall_pct=round(done / total * 100, 1) if total else 100.0)
                    continue
                setst(status="manual",
                      log=f"直链下载 {rel} 失败（网络中断），已保留断点，重试可续传。",
                      download_cmd=_build_cmd(rel, model_dir))
                break
            cmd = exe_prefix + [
                "download", "--model", REPO_ID, rel, "--local_dir", model_dir
            ]
            logger.info("[model_catalog] 启动下载: %s", " ".join(cmd))
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True,
                                        errors="replace", bufsize=1)

                def _drain() -> None:
                    try:
                        for line in proc.stdout:
                            line = (line or "").strip()
                            if not line:
                                continue
                            with _download_lock:
                                s = dict(_download_state.get(task_id, {}))
                                prev = s.get("log", "")
                                s["log"] = (prev + "\n" + line)[-600:]
                                _download_state[task_id] = s
                    except Exception:  # noqa: BLE001
                        pass

                drain = threading.Thread(target=_drain, daemon=True)
                drain.start()
                rc = proc.wait(timeout=timeout)
                logger.info("[model_catalog] 下载结束: %s rc=%s 文件存在=%s",
                            rel, rc, os.path.isfile(fp))
                if rc == 0 and os.path.isfile(fp):
                    done += 1
                elif _dl_was_stopped(task_id):
                    break  # 已暂停/取消：rc!=0 是 terminate 造成的，不是失败
                else:
                    manual_hint = (f"下载 {rel} 失败（返回码 {rc}）。可手动补全：\n"
                                   f"modelscope download --model {REPO_ID} {rel} "
                                   f'--local_dir "{model_dir}"')
                    setst(status="manual", log=manual_hint,
                          download_cmd=_build_cmd(rel, model_dir))
                    break
            except Exception as e:  # noqa: BLE001
                manual_hint = f"下载 {rel} 异常（{e}）。可手动补全：\n{_build_cmd(rel, model_dir)}"
                setst(status="manual", log=manual_hint,
                      download_cmd=_build_cmd(rel, model_dir))
                break
            setst(overall_pct=round(done / total * 100, 1) if total else 100.0)
    finally:
        stop_ev.set()
        try:
            mon.join(timeout=2)
        except Exception:  # noqa: BLE001
            pass
        _dl_unregister(quant_id, model_dir)

    if _dl_was_stopped(task_id):
        # 已暂停/取消：保留 stop_model_download 写好的终态，不做完成度判定
        # （否则 verify_bundle 的 incomplete 会把状态改写成 manual）。
        return

    st2 = verify_bundle(model_dir, quant_id)
    qfile = next((q["file"] for q in QUANTS if q["id"] == quant_id), "")
    if st2.get("complete"):
        _set_comni_llm_model(model_dir, qfile)
        setst(status="done", phase="verify", overall_pct=100.0,
              speed_mbps=0.0, eta_s=None,
              log="下载完成：全套模型已就绪。", items=st2.get("items", []),
              missing=[], files=[])
    else:
        miss = st2.get("missing", [])
        setst(status="manual", phase="verify", overall_pct=100.0,
              log=(manual_hint or f"下载后仍有缺失：{miss}。请手动补全后点检测。"),
              missing=miss,
              download_cmd=_build_cmd(qfile, model_dir))


def download_bundle(data: Dict[str, Any]) -> Dict[str, Any]:
    """触发整套模型下载（后台线程）。立即返回任务 id + 可复制命令；轮询 get_download_status。"""
    import time
    quant_id = str(data.get("quant_id") or "Q4_K_M").strip() or "Q4_K_M"
    model_dir = data.get("model_dir") or _comni_model_dir()
    if not model_dir:
        model_dir = default_model_dir()
    if not next((q for q in QUANTS if q["id"] == quant_id), None):
        return {"success": False, "error": f"未知量化档: {quant_id}"}
    # 并发守卫：同一 (quant, dir) 已在下则复用已有任务，避免双进程踩同一文件
    existing = _dl_active_task(quant_id, model_dir)
    if existing:
        qfile = next((q for q in QUANTS if q["id"] == quant_id), {}).get("file", "")
        return {
            "success": True,
            "task_id": existing,
            "quant_id": quant_id,
            "model_dir": model_dir,
            "files": bundle_files(quant_id),
            "already_running": True,
            "message": "该量化包已在后台下载中（自动准备与手动点击重复触发），复用同一任务。",
        }
    only_missing = bool(data.get("only_missing", True))
    task_id = f"bundle_{quant_id}_{int(time.time())}"
    threading.Thread(
        target=_run_download_bundle,
        args=(quant_id, model_dir, only_missing, task_id),
        daemon=True,
    ).start()
    qfile = next((q for q in QUANTS if q["id"] == quant_id), {}).get("file", "")
    return {
        "success": True,
        "task_id": task_id,
        "quant_id": quant_id,
        "model_dir": model_dir,
        "files": bundle_files(quant_id),
        "total_gb": round(sum(f["size_gb"] for f in bundle_files(quant_id)), 2),
        "download_cmd": _build_cmd(qfile, model_dir),
        "message": "已在后台开始下载全套模型（主模型 + 子模型），可复制命令手动下载，或轮询进度。",
    }


if __name__ == "__main__":
    # 自测：打印当前设备推荐
    cat = get_model_catalog()
    print(json.dumps(cat, ensure_ascii=False, indent=2))
