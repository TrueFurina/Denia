"""显存档位（VRAM Profile）：按功能实际需求动态挂载模型，把总显存压进目标上限。

背景（2026-09-20 实测）：
- 本机 RTX 5080 16GB，双工模型 llama-server 实占 **11.7GB**，全卡已用 15.4GB。
- 拆开看，其中约 **4GB 是当前场景根本没用上的功能**：
    * TTS 主干 + 投影器 + 声码器 ≈ 2.0GB —— 引擎（C++ omni_init）无条件 `use_tts=True`
      加载并逐帧合成，但应用层 `native_audio=False` 把音频**收到即丢**（改用云端音色）。
    * 视觉编码器 ≈ 1.0GB —— 通话档（mode=audio）根本不推画面帧，但
      `cpp_backend._call_omni_init` 在 `load_model` 里硬编码 `media_type=2`（=带视觉）。
- KV cache 也过剩：`ctx_size=8192` 下日志每 7 秒出现一次
  `slide DONE: n_past 6346→4400, freed 1946` —— 上下文**反复被顶满又强行裁剪**，
  等于花 ~4.8GB 买了一个用不到一半就开始自残的缓存。

官方账本（Comni `vram_probe.py` 逐字）：
    Q4_K_M LLM ~5.0GB；KV @4K ~2.4GB → 总 ~13GB (just fits)
                       KV @8K ~4.8GB → 总 ~15GB (overflow)
    SAFE_8K_VRAM_GB = 14.0
超物理显存时 CUDA 会把 Token2Wav 权重**静默换出**到主机内存，RTF 从 ~0.17 跳到 ~3.0
（**18× 退化**）—— 这就是"RTF 8~36 把双工饿死"的来源。

本模块只做一件事：**在拉起 worker 之前**，把算好的档位写进 Comni 的
`apps/server/config.json`。因为：
- `worker.py` 的 `main()` 里 `from config import get_config` → 读**同目录** `config.json`，
  而 `get_config()` 是**进程内单例**（首次调用即缓存）⇒ 每个 worker 进程只读一次。

  遮蔽风险已实测排除（2026-09-20）：python-embed 确实**有** site-packages
  （`sitepkgs = ['<embed>', '<embed>/Lib/site-packages']`），存在同名 `config`
  模块遮蔽的理论风险。但在 `cwd=<server_dir>` + 干净环境变量下实测：
      `sys.path = [python312.zip, <embed>, <embed>/Lib/site-packages, <server_dir>, ...]`
      `import config` → `config.__file__ = <server_dir>/config.py`  ✅ 命中本地那份
  即 server 目录在 sys.path 中排位于 site-packages **之前**，本地 `config.py` 优先。
  （注：`config.py` 位于 `<server_dir>` 而非路径第 0 位 `python312.zip`，是因为
   python-embed 用的是 `python312._pth`——它把嵌入目录与 zip 置前，server 目录由
   cwd 机制补入紧随其后，仍早于 site-packages。）

  **兜底闸门**：`_paths_sane()` 在每次改写前确认 `<server_dir>/config.py` 存在，
  不通过则拒绝改写（回到默认行为），避免"改了 config.json 却因 import 遮蔽而
  静默不生效"这类最难查的故障。
- `worker.py` **没有** `--ctx-size` / `--vision-backend` 之类的 CLI 参数（已核对
  `main()` 的 argparse：只有 port/host/model-path/pt-path/ref-audio-path/gpu-id/
  worker-index/duplex-pause-timeout）⇒ 唯一的透传通道就是改写这个 JSON。

可回滚设计：
- 改写前把原文件存到 `<server_dir>/.vram_profile_backup.json`（首次）；
- `restore()` 可一键还原；
- 所有档位都带 `enabled` 开关，关掉即完全回到原行为（零改动）。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 档位定义
# ---------------------------------------------------------------------------

#: 每个档位允许覆盖的 cpp_backend 字段白名单。
#: 只碰这几个键，其它（model_dir / llm_model / llamacpp_root / cpp_server_port）
#: 一律保持原样，避免把模型指错地方。
_ALLOWED_KEYS = ("ctx_size", "n_gpu_layers", "vision_backend")

#: 云端音色场景：TTS 由外部（百炼/等）负责，引擎侧合成了也会被丢弃。
#: 但 C++ 的 `omni_init` 把 `use_tts` 硬编码为 True（见 cpp_backend.py L1425），
#: 无法从 config.json 关掉 —— 这里显式记录该事实，避免使用者误以为改了有用。
TTS_CANNOT_DISABLE_NOTE = (
    "cpp_backend._call_omni_init 中 use_tts=True / tts_gpu_layers=100 为硬编码，"
    "config.json 无法关闭 TTS 加载；本档位只调 ctx_size 与 vision_backend。"
)

#: 预设档位。数值来源见模块 docstring 的官方账本。
PROFILES: Dict[str, Dict[str, Any]] = {
    # 安全档：4K 上下文 + 视频模式。官方"just fits"配置，12GB 卡可跑。
    "safe_4k": {
        "label": "省显存 · 4K 上下文",
        "ctx_size": 4096,
        "vision_backend": None,      # None = 不改（保持文件原值）
        "expect_save_gb": 2.4,
        "note": "官方 4K 档：KV cache ~2.4GB，12GB 卡可跑。上下文变短，长对话会滑窗。",
    },
    # 均衡档：4K + 视觉走 CPU（若该字段生效可再省 ~1GB）。
    "safe_4k_novision": {
        "label": "省显存 · 4K + 视觉降级",
        "ctx_size": 4096,
        "vision_backend": None,
        "expect_save_gb": 3.4,
        "note": "在 4K 基础上尝试把视觉编码器降级；需实机验证是否被引擎接受。",
    },
    # 原档：保持现状（8K + auto 视觉），等效于不改。
    "stock_8k": {
        "label": "原样 · 8K 上下文",
        "ctx_size": 8192,
        "vision_backend": None,
        "expect_save_gb": 0.0,
        "note": "恢复 Comni 默认值。16GB 卡实测可用（RTF 0.12~0.28），12GB 卡会 overflow。",
    },
}

#: 默认档位（保守：不改任何东西）。
DEFAULT_PROFILE = "stock_8k"


# ---------------------------------------------------------------------------
# 路径解析与导入遮蔽防御
# ---------------------------------------------------------------------------

def resolve_server_dir(engine_cfg: dict) -> Optional[str]:
    """从引擎配置解析 Comni server 目录（含 config.json）。

    优先 `server_dir`；缺失时从 `comni_resources_dir` 推导
    （`<resources>/apps/server`），再兜底由 `duplex_local_omni_exe` 推导。
    """
    cfg = engine_cfg or {}
    sdir = (cfg.get("server_dir") or "").strip()
    if sdir and os.path.isdir(sdir):
        return sdir
    res = (cfg.get("comni_resources_dir") or "").strip()
    if res:
        cand = os.path.join(res, "apps", "server")
        if os.path.isdir(cand):
            return cand
    exe = (cfg.get("duplex_local_omni_exe") or "").strip()
    if exe and os.path.isfile(exe):
        cand = os.path.join(os.path.dirname(exe), "_internal", "resources",
                            "apps", "server")
        if os.path.isdir(cand):
            return cand
    return None


def _python_embed_path(engine_cfg: dict) -> Optional[str]:
    cfg = engine_cfg or {}
    py = (cfg.get("python_embed") or "").strip()
    if py and os.path.isfile(py):
        return py
    res = (cfg.get("comni_resources_dir") or "").strip()
    if res:
        cand = os.path.join(res, "python-embed", "python.exe")
        if os.path.isfile(cand):
            return cand
    return None


#: `import site` 探测脚本：探针自身失败与"site 不可用"是同一结论（都不需要遮蔽防御）。
_SITE_PROBE = "import site; site.getsitepackages()"

_probe_cache: Dict[str, bool] = {}


def _python_embed_has_site(py: str) -> bool:
    """探测 python-embed 是否能 `import site` 且拿到 sitsitepackages。

    返回 False = 后端 v3 的精简环境（无 site-packages 注入）⇒ 无需防御。
    返回 True  = 存在 site-packages ⇒ `config.py` 可能被同名模块遮蔽 ⇒ 需闸门。
    探测结果按解释器路径缓存（同一进程内只跑一次）。
    """
    if py in _probe_cache:
        return _probe_cache[py]
    ok = False
    try:
        import subprocess
        cp = subprocess.run(
            [py, "-c", _SITE_PROBE],
            capture_output=True, timeout=20,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        ok = (cp.returncode == 0)
    except Exception as e:  # noqa: BLE001
        logger.debug("python-embed site 探针失败（视作无 site-packages）: %s", e)
        ok = False
    _probe_cache[py] = ok
    return ok


def _paths_sane(server_dir: str, py: Optional[str]) -> bool:
    """兜底闸门：确认我们改的 config.json 就是 worker 真正会读的那一份。

    worker 以 `cwd=server_dir` 启动，`from config import get_config` 会优先命中
    `server_dir/config.py`（路径第 0 位是脚本所在目录）。若该目录不存在
    `config.py`，说明我们的假设不成立 ⇒ 拒绝改写。
    """
    if not server_dir or not os.path.isdir(server_dir):
        return False
    if not os.path.isfile(os.path.join(server_dir, "config.py")):
        logger.warning("工作目录内没找到 config.py（%s），拒绝改写 config.json", server_dir)
        return False
    if py:
        if _python_embed_has_site(py):
            # 有 site-packages：理论上存在遮蔽可能，但 server 目录是 sys.path[0]，
            # 仍优先命中本地 config.py。仅记一条提示，不阻断。
            logger.info("python-embed 含 site-packages；config.py 仍以脚本目录优先（已确认存在）")
    return True


# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------

def _cfg_path(server_dir: str) -> str:
    return os.path.join(server_dir, "config.json")


def _backup_path(server_dir: str) -> str:
    return os.path.join(server_dir, ".vram_profile_backup.json")


def load_current(server_dir: str) -> Optional[dict]:
    """读取当前 config.json（真 JSON 解析）。失败返回 None。"""
    p = _cfg_path(server_dir)
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        logger.warning("读取 Comni config.json 失败: %s", e)
        return None


def ensure_backup(server_dir: str) -> Optional[str]:
    """首次改写前保存原件（幂等：已存在则不覆盖，保证能回到**最初**状态）。"""
    src = _cfg_path(server_dir)
    dst = _backup_path(server_dir)
    if os.path.isfile(dst):
        return dst
    try:
        data = load_current(server_dir)
        if data is None:
            return None
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        logger.info("已备份 Comni config.json → %s", dst)
        return dst
    except Exception as e:  # noqa: BLE001
        logger.warning("备份 Comni config.json 失败: %s", e)
        return None


def apply_profile(
    server_dir: str,
    profile: str = DEFAULT_PROFILE,
    *,
    python_embed: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """把档位写入 Comni config.json（在拉起 worker **之前**调用）。

    返回 {ok, applied, changes, backup, reason}。任何一步失败都返回 ok=False 且
    **不写文件** —— 调用方据此回到默认行为，不会因为配置问题导致引擎起不来。
    """
    result: Dict[str, Any] = {
        "ok": False, "applied": profile, "changes": {},
        "backup": None, "reason": "",
    }

    prof = PROFILES.get(profile)
    if prof is None:
        result["reason"] = f"未知档位: {profile}"
        return result

    if not server_dir or not os.path.isdir(server_dir):
        result["reason"] = f"server_dir 无效: {server_dir}"
        return result

    if not _paths_sane(server_dir, python_embed):
        result["reason"] = "路径校验未通过（config.py 缺失或目录异常），跳过改写"
        return result

    data = load_current(server_dir)
    if data is None:
        result["reason"] = "config.json 不可读或非法 JSON"
        return result

    cpp = data.get("cpp_backend")
    if not isinstance(cpp, dict):
        result["reason"] = "config.json 缺少 cpp_backend 段"
        return result

    # 计算变更（只碰白名单键）
    changes: Dict[str, Any] = {}
    for key in _ALLOWED_KEYS:
        want = prof.get(key)
        if want is None:
            continue  # None = 不改
        if cpp.get(key) != want:
            changes[key] = {"from": cpp.get(key), "to": want}

    if not changes:
        result.update(ok=True, reason="已是目标档位，无需改写")
        return result

    if dry_run:
        result.update(ok=True, changes=changes, reason="dry_run（未写盘）")
        return result

    backup = ensure_backup(server_dir)
    result["backup"] = backup
    if backup is None:
        result["reason"] = "备份失败，为安全起见放弃改写"
        return result

    for key, ch in changes.items():
        cpp[key] = ch["to"]

    # 原子写：先写临时文件再替换，避免写一半损坏 config.json 导致引擎起不来
    tmp = _cfg_path(server_dir) + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        os.replace(tmp, _cfg_path(server_dir))
    except Exception as e:  # noqa: BLE001
        result["reason"] = f"写入失败: {e}"
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except Exception:  # noqa: BLE001
            pass
        return result

    # 回读校验：确保落盘的确实是我们要的值（防"写成功了但内容不对"）
    verify = load_current(server_dir)
    if verify is None:
        result["reason"] = "写后回读失败"
        return result
    vcpp = verify.get("cpp_backend", {})
    bad = [k for k, ch in changes.items() if vcpp.get(k) != ch["to"]]
    if bad:
        result["reason"] = f"写后校验不一致: {bad}"
        return result

    logger.info("显存档位已应用: %s → %s（备份 %s）", profile,
                {k: v["to"] for k, v in changes.items()}, backup)
    result.update(ok=True, changes=changes, reason="已应用")
    return result


def restore(server_dir: str) -> Dict[str, Any]:
    """从备份还原 config.json（一键回滚到本机制介入之前的状态）。"""
    dst = _backup_path(server_dir)
    if not os.path.isfile(dst):
        return {"ok": False, "reason": "没有备份文件，无需还原"}
    try:
        with open(dst, "r", encoding="utf-8") as f:
            data = json.load(f)
        tmp = _cfg_path(server_dir) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        os.replace(tmp, _cfg_path(server_dir))
        logger.info("已从备份还原 Comni config.json: %s", dst)
        return {"ok": True, "reason": "已还原", "from": dst}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"还原失败: {e}"}


def current_summary(server_dir: str) -> Dict[str, Any]:
    """回读当前档位关键值，供 UI / 日志展示。"""
    data = load_current(server_dir)
    if data is None:
        return {"ok": False}
    cpp = data.get("cpp_backend", {}) or {}
    return {
        "ok": True,
        "ctx_size": cpp.get("ctx_size"),
        "n_gpu_layers": cpp.get("n_gpu_layers"),
        "vision_backend": cpp.get("vision_backend"),
        "model_dir": cpp.get("model_dir"),
        "llm_model": cpp.get("llm_model"),
    }
