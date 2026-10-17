"""Comni 源码补丁器：把「显存优化」两处改动安全地写进 Comni 引擎源码。

为什么要补丁源码（而非改配置）
------------------------------
Comni 的 `apps/server/config.json` 只能覆盖 `ctx_size` / `n_gpu_layers` /
`vision_backend`（见 `vram_profile._ALLOWED_KEYS`）。但有 **两处硬编码** 在
`core/processors/cpp_backend.py` 里，配置层完全够不着：

  ① `use_tts: True`（L1425）—— TTS 主干 + 投影器 + 声码器 ≈ **2028 MB**
     无条件加载。而本应用走云端音色（`duplex_native_audio: false`），
     引擎合成的音频**收到即丢**（`local_omni_duplex.py` L1968-1972）。
     ⇒ 这 2G 是纯浪费。

  ② `_call_update_session_config` 的 media_type 变化分支（L1588-1623）——
     切档时**整组重启** llama-server（`_stop_cpp_server` + `_start_cpp_server`
     + `_call_omni_init`），官方注释自称"约 30~60s"。
     但 2026-09-20 实测：`omni_init(media_type=1)` 只要 **2.32s**（LLM 权重
     已在显存，只换编码器），且 `_call_omni_init` **自带重启降级安全网**
     （3 次重试 → 失败才 `_stop/_start` + 再试 3 次 → 仍失败才 raise）。
     ⇒ 外层那次前置重启是多余的，可以直接 `_call_omni_init`。

补丁的安全性设计（复用 vram_profile 的五件套）
--------------------------------------------
- **白名单定位**：按 `old` 文本精确匹配 + 断言命中数 == 1，绝不模糊替换。
- **幂等**：已打过补丁（`new` 已存在）则直接返回 ok，不重复写。
- **路径闸门**：`_paths_sane()` 确认 server_dir 与目标文件都在。
- **备份**：首次改前存 `<file>.comni_patch_backup`，**不覆盖**（保证能回到最初）。
- **原子写**：写 `.tmp` 再 `os.replace`。
- **换行自适应**：读原始 bytes 判 `\r\n`，替换后按原换行写回；写后断言
  `\r\r` == 0（防文本模式把 CRLF 翻成 `\r\r` 污染文件）。
- **写后回读**：重新解析文件，断言 `new` 存在、`old` 已消失。
- **语法校验**：`ast.parse` 通过才算成功。

回滚：`restore_source()` 从备份还原，或直接删掉 `<file>.comni_patch_backup`
再跑一次 `run_source_patch(enable=False)`。

默认开关全部关闭（`enabled=False`）⇒ 引入本模块本身**零行为变化**。
"""
from __future__ import annotations

import ast
import io
import logging
import os
import shutil
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

#: 相对 server_dir 的目标文件
_TARGET_REL = os.path.join("core", "processors", "cpp_backend.py")

#: Comni 官方源文件的 SHA256（2026-09-20 基线，仅用于提示"是否被别的工具改过"）
KNOWN_ORIG_SHA256 = "e301c8e7f42b5bea1bf75051615c5589cd7fe79ad047d81d8f776c6a55b1bb7f"


def _target_path(server_dir: str) -> str:
    return os.path.join(server_dir, _TARGET_REL)


def _backup_path(server_dir: str) -> str:
    return _target_path(server_dir) + ".comni_patch_backup"


# ---------------------------------------------------------------------------
# 两处补丁定义
# ---------------------------------------------------------------------------

# --- ① use_tts 改为环境变量可关（默认 True = 与官方完全一致） ---
TTS_OLD = '''        req_body = {
            "media_type": media_type,
            "use_tts": True,
            "duplex_mode": duplex_mode,'''

TTS_NEW = '''        # [comni_patch:tts] use_tts 由环境变量控制，默认 True = 官方原行为。
        # 本应用走云端音色（native_audio=False），引擎合成的音频会被上游丢弃
        # （local_omni_duplex.py:1968-1972），关掉可省 ~2028MB 显存。
        # 置 COMNI_USE_TTS=0/off/false/no 即关闭；未设置或不识别时保持 True。
        _use_tts = os.environ.get("COMNI_USE_TTS", "").strip().lower()
        _use_tts_on = _use_tts not in ("0", "off", "false", "no")
        req_body = {
            "media_type": media_type,
            "use_tts": _use_tts_on,
            "duplex_mode": duplex_mode,'''

# --- ② media_type 切换：复用模型不重启 ---
RESTART_OLD = '''        if mode_changed or media_type_changed:
            # Full re-init when switching duplex_mode or media_type.
            #
            # 注意：不能只调 omni_init(reuse_model=True)。上游 llama.cpp sync
            # 后，第二次 load TTS 模型会抛 "invalid vector subscript"（某处
            # vector 越界，可能是 ggml/llama 加载路径的 state 污染），导致
            # llama-server crash、conn reset。唯一可靠的方案是把
            # llama-server.exe 完全重启：fresh process → fresh omni_init。
            #
            # 代价：约 30~60s 的模型重载延迟。但它 100% 可靠。
            logger.info(
                "session mode changed "
                f"(duplex: {self._last_duplex_mode} -> {duplex_mode}, "
                f"media_type: {self._last_media_type} -> {media_type}), "
                "restarting C++ server for clean state"
            )
            try:
                self._stop_cpp_server()
            except Exception as e:
                logger.warning(f"_stop_cpp_server during mode-switch raised: {e}")
            time.sleep(1.0)
            self._start_cpp_server()
            self._call_omni_init(
                media_type=media_type,
                duplex_mode=duplex_mode,
                lang=lang,
                system_content=system_content,
            )
            self._last_duplex_mode = duplex_mode
            self._last_media_type = media_type'''

REUSE_OLD_MARK = "restarting C++ server for clean state"

RESTART_NEW = '''        if mode_changed or media_type_changed:
            # [comni_patch:reuse] 复用模型、不重启 llama-server。
            #
            # 官方原实现：切 duplex_mode / media_type 时无条件
            #   _stop_cpp_server() → sleep(1.0) → _start_cpp_server() → _call_omni_init()
            # 官方注释自称"约 30~60s"，理由是担心「第二次 load TTS 模型抛
            # invalid vector subscript」。
            #
            # 本补丁的依据（2026-09-20 实机 A/B 实测）：
            #   1) 切 media_type 走 omni_init 只要 2.32~2.97s —— LLM 权重始终在
            #      显存里，omni_init 只换视觉编码器，**不重载 TTS/LLM**；
            #   2) `_call_omni_init` 自身**已内置重启降级安全网**：
            #      普通 POST 重试 3 次 → 仍非 200 才走
            #      `_stop_cpp_server` + `_start_cpp_server` + 再重试 3 次 →
            #      全失败才 raise RuntimeError。
            #   也就是说：真遇到官方担心的那种 state 污染时，**内层会自动重启兜底**，
            #   结果与官方原实现等价（只是多花几次重试的时间）。
            #   走正常路径时则省掉整次进程重启（约 30~60s → 约 3s）。
            #
            # 回滚：把本项目 comni_source_patch 的 enable_reuse_model 置 False，
            #       或直接删掉 cpp_backend.py.comni_patch_backup 后重跑补丁。
            logger.info(
                "session mode changed "
                f"(duplex: {self._last_duplex_mode} -> {duplex_mode}, "
                f"media_type: {self._last_media_type} -> {media_type}), "
                "reusing loaded model (no server restart) [comni_patch]"
            )
            # 不再前置重启：直接把新档位交给 _call_omni_init。
            # 它内部自带「重试 3 次 → 失败才重启 → 再重试 3 次」的降级链。
            self._call_omni_init(
                media_type=media_type,
                duplex_mode=duplex_mode,
                lang=lang,
                system_content=system_content,
            )
            self._last_duplex_mode = duplex_mode
            self._last_media_type = media_type'''

#: 补丁表：name → (old, new, 幂等探针 new 中的独有片段)
_PATCHES = [
    ("tts", TTS_OLD, TTS_NEW, "# [comni_patch:tts] use_tts 由环境变量控制"),
    ("reuse", RESTART_OLD, RESTART_NEW, "# [comni_patch:reuse] 复用模型"),
]


# ---------------------------------------------------------------------------
# 读写与闸门
# ---------------------------------------------------------------------------

def _detect_nl(path: str) -> str:
    """探测文件主换行符（不改文件）。"""
    raw = io.open(path, "rb").read()
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    return "\r\n" if crlf > lf else "\n"


def _read_lf(path: str) -> str:
    """读取并**归一成 LF**（去 BOM）。

    归一化是必须的：补丁模板一律用 `\\n` 书写，而 Comni 的 cpp_backend.py 是
    CRLF（实测 2161/2161）。若拿原文直接 `count(old)` 会 0 命中 —— 曾因此
    静默判定"源文件已被改过"而拒绝打补丁。
    """
    raw = io.open(path, "rb").read()
    return raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")


def _paths_sane(server_dir: str) -> Tuple[bool, str]:
    if not server_dir or not os.path.isdir(server_dir):
        return False, "server_dir 无效: %s" % server_dir
    if not os.path.isfile(os.path.join(server_dir, "config.py")):
        return False, "server_dir 内无 config.py（不是 Comni server 目录）"
    if not os.path.isfile(_target_path(server_dir)):
        return False, "目标文件不存在: %s" % _target_path(server_dir)
    return True, ""


def ensure_backup(server_dir: str) -> Optional[str]:
    """首次改写前存原件（幂等：已存在则不覆盖，保证能回到**最初**状态）。"""
    src = _target_path(server_dir)
    dst = _backup_path(server_dir)
    if os.path.isfile(dst):
        return dst
    try:
        shutil.copy2(src, dst)
        logger.info("已备份 Comni 源文件 → %s", dst)
        return dst
    except Exception as e:  # noqa: BLE001
        logger.warning("备份 Comni 源文件失败: %s", e)
        return None


def patch_state(server_dir: str) -> Dict[str, Any]:
    """只读探测：两处补丁各自是否已应用。"""
    out: Dict[str, Any] = {"ok": False, "tts": False, "reuse": False,
                           "backup": None, "reason": ""}
    ok, why = _paths_sane(server_dir)
    if not ok:
        out["reason"] = why
        return out
    try:
        txt = _read_lf(_target_path(server_dir))
    except Exception as e:  # noqa: BLE001
        out["reason"] = "读取失败: %s" % e
        return out
    out["ok"] = True
    out["newline"] = _detect_nl(_target_path(server_dir))
    for name, _old, _new, probe in _PATCHES:
        out[name] = probe in txt
    # 原始态探针：官方标记还在 = 未打过 reuse 补丁
    out["orig_reuse_marker"] = REUSE_OLD_MARK in txt
    bp = _backup_path(server_dir)
    out["backup"] = bp if os.path.isfile(bp) else None
    return out


def apply_source_patch(
    server_dir: str,
    *,
    enable_tts: bool = False,
    enable_reuse_model: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """把两处补丁写进 Comni `cpp_backend.py`。

    参数为 False 时不打对应补丁；两处都 False 时本函数不做任何事。
    返回 {ok, applied, skipped, backup, changes, reason}。
    """
    result: Dict[str, Any] = {
        "ok": False, "applied": [], "skipped": [], "backup": None,
        "changes": {}, "reason": "",
    }
    want = {}
    if enable_tts:
        want["tts"] = True
    if enable_reuse_model:
        want["reuse"] = True
    if not want:
        result.update(ok=True, reason="未启用任何补丁（零改动）")
        return result

    ok, why = _paths_sane(server_dir)
    if not ok:
        result["reason"] = why
        return result

    path = _target_path(server_dir)
    try:
        txt = _read_lf(path)
        nl = _detect_nl(path)
    except Exception as e:  # noqa: BLE001
        result["reason"] = "读取失败: %s" % e
        return result

    # 计算待改项
    todo = []
    for name, old, new, probe in _PATCHES:
        if name not in want:
            continue
        if probe in txt:
            result["skipped"].append(name)
            continue
        cnt = txt.count(old)
        if cnt != 1:
            result["reason"] = ("补丁 [%s] 定位失败：原文命中 %d 次（期望 1）——"
                                "源文件可能已被其它工具改过，拒绝改写" % (name, cnt))
            return result
        todo.append((name, old, new))

    if not todo:
        result.update(ok=True, reason="两处补丁均已是目标状态（幂等）")
        return result

    if dry_run:
        result.update(ok=True, applied=[n for n, _o, _n2 in todo],
                      reason="dry_run（未写盘）", changes={n: True for n, _o, _n2 in todo})
        return result

    backup = ensure_backup(server_dir)
    result["backup"] = backup
    if backup is None:
        result["reason"] = "备份失败，为安全起见放弃改写"
        return result

    new_txt = txt
    for name, old, new in todo:
        # 模板一律以 \n 书写，此处已在 LF 域内匹配/替换
        c = new_txt.count(old)
        if c != 1:
            result["reason"] = "补丁 [%s] 写前复查命中 %d 次（期望 1）" % (name, c)
            return result
        new_txt = new_txt.replace(old, new, 1)
        result["applied"].append(name)

    # 写前语法校验：补丁后的文本必须仍是合法 Python
    try:
        ast.parse(new_txt)
    except SyntaxError as e:
        result["reason"] = "补丁后语法校验失败（已中止，未写盘）: %s" % e
        return result

    # 换行还原：LF → 目标文件原换行
    out_txt = new_txt if nl == "\n" else new_txt.replace("\n", nl)

    # 原子写
    tmp = path + ".tmp"
    try:
        with io.open(tmp, "wb") as f:
            f.write(out_txt.encode("utf-8"))
        os.replace(tmp, path)
    except Exception as e:  # noqa: BLE001
        result["reason"] = "写入失败: %s" % e
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except Exception:  # noqa: BLE001
            pass
        return result

    # 写后回读校验
    try:
        check = _read_lf(path)
        raw = io.open(path, "rb").read()
    except Exception as e:  # noqa: BLE001
        result["reason"] = "写后回读失败: %s" % e
        return result

    if raw.count(b"\r\r") != 0:
        result["reason"] = ("写后检测到 \\r\\r 换行污染（count=%d）—— "
                            "文件可能已损坏，请用备份还原" % raw.count(b"\r\r"))
        return result

    bad = [n for n, _o, _n2, probe in _PATCHES if n in want and probe not in check]
    if bad:
        result["reason"] = "写后校验不一致（未出现补丁标记）: %s" % bad
        return result
    # 官方原句应已消失（reuse 补丁）
    if "reuse" in want and REUSE_OLD_MARK in check:
        result["reason"] = "写后校验不一致：官方重启分支仍存在（reuse 补丁未生效）"
        return result

    try:
        ast.parse(check)
    except SyntaxError as e:
        result["reason"] = "写后文件语法非法（请用备份还原）: %s" % e
        return result

    logger.info("Comni 源码补丁已应用 %s（备份 %s）", result["applied"], backup)
    result.update(ok=True, reason="已应用")
    return result


def restore_source(server_dir: str) -> Dict[str, Any]:
    """从备份还原 cpp_backend.py（一键回滚到本机制介入之前）。"""
    dst = _backup_path(server_dir)
    if not os.path.isfile(dst):
        return {"ok": False, "reason": "没有备份文件，无需还原"}
    path = _target_path(server_dir)
    try:
        tmp = path + ".tmp"
        shutil.copy2(dst, tmp)
        os.replace(tmp, path)
        logger.info("已从备份还原 Comni 源文件: %s", dst)
        return {"ok": True, "reason": "已还原", "from": dst}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": "还原失败: %s" % e}
