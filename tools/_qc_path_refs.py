# -*- coding: utf-8 -*-
"""按类扫描：配置里指向**不存在**的本地路径 / 角色没绑 TTS 引擎。

【2026-10-19 的来由】修 ASR 时发现 `voice.asr.model_path` 写着一个不存在的
目录，而"非空值会把 vosk 的自动下载堵死"。同一类毛病在别的路径字段上完全
可能还有——它和"能想/能说/能听"三处静默失效同型：**不是功能写错，
是验证缺失**（配置看着都填了，从没被验证过）。所以按类扫一遍。

扫三类：
  A. 路径类字段（白名单 PATH_KEYS）→ 值就必须真实存在
  B. 角色 voice.engine → 必须能造出非静音后端（能收不能说）
  C. 指向本机的服务 URL → 端口最好在听（默认只提示，见 --strict-ports）

可选功能（auto_start / enabled 为 false 的子树）下的 A/C 一律降为提示：
那种机器上没装是正常的，判红的话每次跑门禁都假红，门禁就废了。

用法：.venv/Scripts/python.exe -u tools/_qc_path_refs.py [config] [--strict-ports]
"""
import os
import re
import socket
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
# 别漏了这条：check_engines 要 import src.voice.tts_backends。测试里是测试自己
# insert 的 ROOT，所以脚本裸跑会 ModuleNotFoundError 而测试全绿——
# "测试能跑"和"脚本能跑"是两件事。2026-10-19 实测踩到。
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
CFG = ARGS[0] if ARGS else "config.yaml"
# C 类（本机服务端口）默认**只提示不判红**：端口状态是运行环境决定的
# （网关常装在 Docker 里、没起很正常），判红会天天假红。想看红就传它。
STRICT_PORTS = "--strict-ports" in sys.argv

# ---- A 类：白名单。只列"明确应该是本地路径"的键 ----------------------------
PATH_KEYS = {
    "model_path", "reference_audio", "ref_audio", "path", "file", "dir",
    "model_dir", "audio_path", "wav_path", "icon_path", "exe_path",
    "handshake_path", "log_dir", "cache_dir", "data_dir",
}

url_re = re.compile(r"^(https?|wss?)://", re.I)
win_drive_re = re.compile(r"^[A-Za-z]:[\\/]")

# 模块级收集器：**必须显式声明**。早先用 globals() 在 scan() 里动态注入，
# 虽然能跑，但 tools/check_module_globals.py 会报 "walk 引用了未定义的全局
# '_checked'"——静态检查抓得到，就别跟它斗。
_problems: list = []
_notes: list = []
_checked: list = [0]


def looks_like_path(v, key: str = "") -> bool:
    """是否该按"本地路径"校验。**白名单制**：只查 PATH_KEYS 里的键。

    三次改版的历史（都踩过，记下来省得再踩）：
      一版按"值里有 / 或 \\\\"判 → `model_path: foo` 这种相对目录名漏报；
      二版未知键也查           → `label`（OCR 锚点文字）、`realtime_model`
                                 （模型 ID）、`ws_url` 全被当本地路径，
                                 噪声淹没问题；
      三版（现版）白名单        → 只查已登记键，其余一概不查。
                                 宁可漏几个没登记的键，也不要一片红。
    """
    if not isinstance(v, str) or not v.strip() or not key:
        return False
    if key not in PATH_KEYS:
        return False
    s = v.strip()
    if url_re.match(s) or s.startswith(("dpapi:", "${", "$")):
        return False
    if s.lower() in ("default", "auto", "none", "null"):
        return False
    return True


def _optional(node) -> bool:
    """这棵子树挂着的功能是否默认关闭（auto_start / enabled 为 false）。"""
    if not isinstance(node, dict):
        return False
    for k in ("auto_start", "enabled"):
        v = node.get(k)
        if isinstance(v, bool) and v is False:
            return True
        if isinstance(v, str) and v.strip().lower() in ("false", "0", "no", "off"):
            return True
    return False


def _resolve(p: str) -> str:
    return p if os.path.isabs(p) else os.path.join(ROOT, p)


def walk(node, trail: str = "", optional: bool = False) -> None:
    if isinstance(node, dict):
        optional = optional or _optional(node)
        for k, v in node.items():
            t = f"{trail}.{k}" if trail else str(k)
            if looks_like_path(v, k):
                _checked[0] += 1
                rp = _resolve(v.strip())
                if os.path.exists(rp):
                    _notes.append(f"[A ok] {t} = {v.strip()}")
                else:
                    msg = f"[A 路径不存在] {t} = {v!r} → {rp}"
                    (_notes if optional else _problems).append(msg)
            elif isinstance(v, str) and k in ("api_url", "base_url") and v.strip():
                m = url_re.match(v.strip())
                if m and m.group(1) in ("http", "https"):
                    host_part = v.strip().split("//", 1)[1]
                    if host_part.startswith(("127.0.0.1", "localhost", "[::1]")):
                        port = 80
                        for seg in host_part.split("/")[0].lstrip("[").split("]"):
                            if ":" in seg:
                                try:
                                    port = int(seg.rsplit(":", 1)[1])
                                except ValueError:
                                    pass
                        _checked[0] += 1
                        try:
                            with socket.create_connection(("127.0.0.1", port),
                                                          timeout=0.4):
                                _notes.append(f"[C ok] {t} → 本机 {port} 在听")
                        except OSError:
                            msg = (f"[C 本机服务没起] {t} = {v.strip()}"
                                   f"（端口 {port} 没在听；功能已关闭则无碍，"
                                   "否则相关能力会静默失效）")
                            if optional or not STRICT_PORTS:
                                _notes.append(msg)
                            else:
                                _problems.append(msg)
            walk(v, t, optional)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk(v, f"{trail}[{i}]", optional)


def check_engines(data: dict) -> int:
    """B 类：角色 voice.engine 必须能造出非静音后端。返回校验处数。"""
    from types import SimpleNamespace

    from src.voice.tts_backends import create_backend

    probe = SimpleNamespace(
        engine="", voice="", rate="+0%", pitch="+0Hz", volume="+0%",
        fallback_pyttsx3=True, api_url="", reference_audio="", reference_text="",
        language="zh", api_key="", group_id="", voice_id="", model="", speed=1,
        emotion_tags={},
    )
    n = 0
    for ch in data.get("characters") or []:
        n += 1
        name = ch.get("name")
        eng = ((ch.get("voice") or {}).get("engine") or "").strip()
        if not eng:
            _problems.append(f"[B 未绑引擎] 角色 {name!r} 的 voice.engine 为空 "
                             "→ 落到静音后端（不出声/不联网/零日志）")
            continue
        probe.engine = eng
        try:
            be = create_backend(eng, probe)
        except Exception as e:  # noqa: BLE001
            _problems.append(f"[B 构造失败] 角色 {name!r} engine={eng!r}: {e}")
            continue
        if type(be).__name__ == "_SilentBackend":
            _problems.append(f"[B 引擎不认识] 角色 {name!r} engine={eng!r}")
        else:
            _notes.append(f"[B ok] 角色 {name!r} engine={eng!r} → {type(be).__name__}")
    return n


def scan(data: dict, strict_ports: bool = False) -> tuple:
    """扫一份配置字典，返回 (problems, notes, checked)。供测试直接调用。"""
    global _problems, _notes, _checked, STRICT_PORTS
    _problems, _notes, _checked = [], [], [0]
    STRICT_PORTS = strict_ports
    walk(data)
    n = _checked[0] + check_engines(data)
    return _problems, _notes, n


def main() -> int:
    import yaml

    if not os.path.exists(CFG):
        print(f"配置文件不存在：{CFG}")
        return 1
    data = yaml.safe_load(open(CFG, encoding="utf-8"))
    problems, notes, n_checked = scan(data, STRICT_PORTS)

    print("=" * 72)
    print(f"配置路径/服务引用体检：{CFG}")
    print("=" * 72)
    print(f"共校验 {n_checked} 处\n")
    for x in notes:
        print("  ", x)
    print()
    if problems:
        print(f"发现 {len(problems)} 处问题：")
        for p in problems:
            print("  X", p)
        return 1
    print("未发现问题。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
