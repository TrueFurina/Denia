# -*- coding: utf-8 -*-
"""agent_pro · 启动引擎（launch）。

实测定轨的事实（2026-10-16，六路对照实验）：
  · launcher 委托型应用（汽水音乐等）：cmd start / os.startfile / Popen / ShellExecute
    全部静默秒退，**唯一可靠通道是 explorer.exe 委托**（等价资源管理器双击）。
  · explorer 返回极快但子进程要几秒才出现（冷启动 ~3.5s）——所以启动后必须
    **轮询验证进程真的出现了**，否则就是谎报成功（这是 3 个模型被骗的共同根因）。
  · a11y（放歌场景）：解析出真实 exe 后直启带 --force-renderer-accessibility；
    launcher 型直启会失败（实测 exit 9/0），失败立即回落 explorer 普通启动。

本文件不含任何应用名硬编码——查名全部走 app_index。
"""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import time
from pathlib import Path

logger = logging.getLogger("agent_pro")

# ---------------------------------------------------------------------------
# 进程名快照（ctypes Toolhelp32，不依赖 psutil）
# ---------------------------------------------------------------------------
_TH32CS_SNAPPROCESS = 0x00000002


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_ulong),
                ("cntUsage", ctypes.c_ulong),
                ("th32ProcessID", ctypes.c_ulong),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", ctypes.c_ulong),
                ("cntThreads", ctypes.c_ulong),
                ("th32ParentProcessID", ctypes.c_ulong),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", ctypes.c_ulong),
                ("szExeFile", ctypes.c_wchar * 260)]


def proc_names() -> set[str]:
    """当前全部进程名（小写，含 .exe）。任何异常返回空集（调用方按"无法验证"处理）。"""
    try:
        k32 = ctypes.windll.kernel32
        snap = k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if snap == ctypes.c_void_p(-1).value or snap == -1:
            return set()
        out: set[str] = set()
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out.add((entry.szExeFile or "").lower())
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
        k32.CloseHandle(snap)
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning("[agent_pro] 进程快照失败：%s", e)
        return set()


def proc_exe_dirs() -> set[str]:
    """当前全部进程的可执行文件**所在目录**（小写，含尾分隔符）。

    用途：launcher 型应用（token 是 SodaMusicLauncher.exe，实际进程是
    SodaMusic.exe）名字对不上，但安装目录一致 —— 按目录判定"已在运行"。
    """
    out: set[str] = set()
    try:
        k32 = ctypes.windll.kernel32
        snap = k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if snap == ctypes.c_void_p(-1).value or snap == -1:
            return out
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        buf = ctypes.create_unicode_buffer(1024)
        while ok:
            pid = entry.th32ProcessID
            h = k32.OpenProcess(0x1000, 0, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if h:
                sz = ctypes.c_ulong(1024)
                if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(sz)):
                    p = (buf.value or "")
                    out.add(os.path.dirname(p).lower().rstrip("\\") + os.sep)
                k32.CloseHandle(h)
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
        k32.CloseHandle(snap)
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning("[agent_pro] 进程路径枚举失败：%s", e)
        return out


def proc_exe_pairs() -> "set[tuple[str, str]]":
    """(进程所在目录小写带尾分隔符, 进程名 stem 小写) 集合。"""
    out: "set[tuple[str, str]]" = set()
    try:
        k32 = ctypes.windll.kernel32
        snap = k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if snap == ctypes.c_void_p(-1).value or snap == -1:
            return out
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        buf = ctypes.create_unicode_buffer(1024)
        while ok:
            pid = entry.th32ProcessID
            h = k32.OpenProcess(0x1000, 0, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if h:
                sz = ctypes.c_ulong(1024)
                if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(sz)):
                    full = buf.value or ""
                    d = os.path.dirname(full).lower().rstrip("\\") + os.sep
                    stem = os.path.splitext(os.path.basename(full))[0].lower()
                    out.add((d, stem))
                k32.CloseHandle(h)
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
        k32.CloseHandle(snap)
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning("[agent_pro] 进程路径枚举失败：%s", e)
        return out


def _dir_has_process(dir_path: str, pairs=None, expected_stem: str = "") -> bool:
    """token 所在安装目录下是否有**同一家族**的进程在跑。

    必须叠加进程名前缀约束：System32 这类共享目录里永远有几百个进程，
    只按"同目录"判会把任何系统应用误判成"已在运行"（实测 notepad.exe 命中此坑，
    导致"打开记事本"直接回"已在运行"而不启动）。
    家族关系 = 互为前缀且公共前缀 >= 4（SodaMusicLauncher.exe ↔ SodaMusic.exe）。
    """
    """token 所在安装目录下是否有进程在跑（launcher 家族判定的通用依据）。"""
    if not dir_path:
        return False
    base = os.path.dirname(os.path.abspath(dir_path)).lower().rstrip("\\") + os.sep
    want = (expected_stem
            or os.path.splitext(os.path.basename(dir_path))[0]).lower()
    pairs = proc_exe_pairs() if pairs is None else pairs
    if not pairs:
        return False
    for d, stem in pairs:
        if not d.startswith(base):
            continue
        if not want or want == stem:
            return True
        n = 0
        for a, b in zip(want, stem):
            if a != b:
                break
            n += 1
        if n >= 4:
            return True
    return False


# ---------------------------------------------------------------------------
# lnk → 目标 exe（懒解析，进程内缓存；失败返回 ""）
# ---------------------------------------------------------------------------
_lnk_cache: dict[str, str] = {}


def _parse_lnk_binary(lnk: str) -> str:
    """纯二进制解析 .lnk 的 LocalBasePath（零外部依赖，不依赖 PowerShell）。

    格式：76 字节 ShellLinkHeader（LinkFlags 在偏移 20）→ 可选 LinkTargetIDList
    → 可选 LinkInfo（内含 LocalBasePath ANSI 串）。解析失败返回 ""。
    """
    try:
        import struct
        data = Path(lnk).read_bytes()
        if len(data) < 76 or data[:4] != b"L\x00\x00\x00":
            return ""
        flags = struct.unpack_from("<I", data, 20)[0]
        off = 76
        if flags & 0x00000001:  # HasLinkTargetIDList → 跳过 IDList（2 字节尺寸含终止符）
            if len(data) < off + 2:
                return ""
            idl = struct.unpack_from("<H", data, off)[0]
            off += 2 + idl
        if not (flags & 0x00000002):  # 无 LinkInfo
            return ""
        if len(data) < off + 20:
            return ""
        _size, _hdr, _lflags, _vol_off, lbp_off = struct.unpack_from("<IIIII", data, off)
        base = data[off + lbp_off: off + lbp_off + 1024]
        s = base.split(b"\x00")[0].decode("mbcs", errors="replace").strip()
        return s if s.lower().endswith(".exe") and os.path.exists(s) else ""
    except Exception:  # noqa: BLE001
        return ""


def resolve_lnk(lnk: str) -> str:
    if lnk in _lnk_cache:
        return _lnk_cache[lnk]
    exe = ""
    try:
        ps = ("$s=New-Object -ComObject WScript.Shell;"
              "$sc=$s.CreateShortcut('" + str(lnk).replace("'", "''") + "');"
              "if($sc){$sc.TargetPath}")
        o = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, errors="replace", timeout=20)
        txt = (o.stdout or "").strip()
        cand = txt.splitlines()[0].strip() if txt else ""
        if cand.lower().endswith(".exe") and os.path.exists(cand):
            exe = cand
    except Exception:  # noqa: BLE001
        exe = ""
    if not exe:
        # PowerShell 通道受限（沙箱/策略）时的零依赖兜底
        exe = _parse_lnk_binary(lnk)
    _lnk_cache[lnk] = exe
    return exe


def _electron_main_exe(exe: str) -> str:
    """launcher → 主 exe 启发式：同目录版本子目录里的同名 exe。"""
    try:
        d = Path(exe).parent
        stem = Path(exe).stem
        subs = sorted([x for x in d.iterdir() if x.is_dir() and x.name[:1].isdigit()],
                      key=lambda x: x.name, reverse=True)
        for sd in subs:
            cand = sd / f"{stem}.exe"
            if cand.exists():
                return str(cand)
    except Exception:  # noqa: BLE001
        pass
    return exe


# ---------------------------------------------------------------------------
# 验证启动：轮询进程名出现
# ---------------------------------------------------------------------------
def verify_started(expected_exe: str, before: set[str], timeout_s: float = 10.0,
                   interval_s: float = 0.5) -> tuple[bool, str]:
    """启动命令发出后轮询：期望 exe（或任意新进程）出现即算成功。

    返回 (成功?, 描述)。expected_exe 为空时放宽为"出现任意新进程"。
    进程快照不可用（空集）→ 无法验证，如实说明。
    """
    if not before:  # 基线拿不到 → 没法对比，等满预算后看有没有目标进程
        deadline = time.time() + timeout_s
        exp = (expected_exe or "").lower()
        while time.time() < deadline:
            now = proc_names()
            if exp and exp in now:
                return True, f"已验证进程 {expected_exe} 在运行"
            time.sleep(interval_s)
        return False, "无法验证（进程快照不可用）"
    deadline = time.time() + timeout_s
    exp = (expected_exe or "").lower()
    while time.time() < deadline:
        now = proc_names()
        if exp and exp in now:
            return True, f"已验证进程 {expected_exe} 在运行"
        new = now - before
        # 排除 explorer 自己 / 常见系统噪音
        new = {n for n in new if n not in ("explorer.exe", "cmd.exe", "conhost.exe",
                                           "powershell.exe", "pwsh.exe", "runtimebroker.exe")}
        if new:
            return True, f"已验证新进程启动：{', '.join(sorted(new)[:3])}"
        time.sleep(interval_s)
    return False, f"{timeout_s:.0f}s 内未检测到新进程（启动大概率失败）"


# ---------------------------------------------------------------------------
# 启动通道（按 token 类型选已验证的通道）
# ---------------------------------------------------------------------------
def _launch_token_verified(token: dict, verify_timeout: float) -> tuple[bool, str, str]:
    """发启动命令 + 验证。返回 (成功?, 描述, 期望exe名)。"""
    before = proc_names()
    ttype = token.get("type")
    val = token.get("value", "")
    # token 的"锚定目录"：exe 通道用它判 launcher 家族是否已在运行
    anchor_dir = val if ttype == "exe" else ""
    expected = ""
    if ttype == "exe":
        expected = Path(val).name
    elif ttype == "lnk":
        exe = resolve_lnk(val)
        if exe:
            expected = Path(exe).name
            anchor_dir = exe
    # 已在运行 → 不重复拉起，直接如实报告（避免"未检测到新进程"误导）。
    # 两层判定：① 期望进程名在跑；② launcher 家族：同安装目录下有任意进程。
    _estem = Path(expected).stem if expected else ""
    if ((expected and expected.lower() in before)
            or _dir_has_process(anchor_dir, None, _estem)):
        return True, f"应用已在运行（{expected or Path(anchor_dir).name}），未重复启动", expected
    if ttype == "lnk":
        subprocess.Popen(["explorer.exe", val], shell=False)
    elif ttype == "appid":
        subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + val], shell=False)
        expected = ""  # UWP 进程名未知，放宽为任意新进程
    elif ttype == "exe":
        subprocess.Popen(["explorer.exe", val], shell=False)
    else:
        return False, f"未知启动 token 类型：{ttype}", ""
    ok, desc = verify_started(expected, before, timeout_s=verify_timeout)
    if not ok and anchor_dir and _dir_has_process(anchor_dir, None, _estem):
        # 启动没产生新进程，但同安装目录下有进程 → 单实例应用已在运行（launcher 会话合并）
        return True, "应用已在运行（单实例，启动命令被合并到已有会话）", expected
    return ok, desc, expected


def launch_with_token(token: dict, a11y: bool = False, verify_timeout: float = 10.0) -> str:
    """带验证的启动。返回给模型看的说明文本（含验证结果，绝不谎报）。"""
    name_hint = token.get("_name", "")
    # a11y 直启路径（放歌场景）：仅 exe/lnk 且解析得出主 exe
    if a11y:
        exe = token.get("value", "") if token.get("type") == "exe" else \
            (resolve_lnk(token.get("value", "")) if token.get("type") == "lnk" else "")
        if exe:
            main = _electron_main_exe(exe)
            try:
                subprocess.Popen([main, "--force-renderer-accessibility"],
                                 cwd=os.path.dirname(main))
                before = proc_names()
                ok, desc = verify_started(Path(main).name, before, timeout_s=verify_timeout)
                if ok:
                    return f"已带无障碍参数启动 {name_hint or main}（{desc}）"
                logger.info("[agent_pro] a11y 直启未验证成功，回落 explorer：%s", desc)
            except Exception as e:  # noqa: BLE001
                logger.info("[agent_pro] a11y 直启异常，回落 explorer：%s", e)
    ok, desc, _exp = _launch_token_verified(token, verify_timeout)
    head = f"已启动 {name_hint}：" if name_hint else "已启动："
    if ok:
        return head + desc
    return (head + f"启动命令已发出，但{desc}。应用可能未成功打开，"
            "请改用 state_snapshot / see_screen 核实，或换个说法再试。")


def _looks_like_path(v: str) -> bool:
    return ("\\" in (v or "")) or (":" in (v or ""))


def _token_order(rec: dict) -> list[dict]:
    """该记录的候选启动 token，**真包名 appid 优先**（打包应用经 AppsFolder 启动最可靠）。

    注意排除"路径型 appid"：Get-StartApps 对 Win32 应用会把 AppID 直接给成 exe 路径，
    这种值走 AppsFolder 反而多一次失败（还会白等一个验证超时），所以不优先。
    """
    toks = [rec.get("token")] + list(rec.get("_alts") or [])
    seen: set[tuple] = set()
    out: list[dict] = []
    for t in toks:
        if not (t and t.get("value")):
            continue
        k = (t.get("type"), t.get("value"))
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    out.sort(key=lambda t: 0 if (t.get("type") == "appid"
                                 and not _looks_like_path(t.get("value", ""))) else 1)
    return out


def _is_launch_ok(r: str) -> bool:
    return ("已验证" in r) or ("已在运行" in r)


def index_launch(target: str, a11y: bool = False, flags: dict | None = None) -> str:
    """open_app/_fuzzy_launch 的通用前置：应用索引匹配 → 带验证启动。

    匹配不到（分数不足）返回空串，交回调用方原逻辑（命令直启等）。
    """
    flags = flags or {}
    target = (target or "").strip()
    if not target or target.startswith(("http://", "https://", "ms-settings:")):
        return ""
    from . import app_index as ai
    from .config import DEFAULT_BASE_DIR, load_aliases
    aliases = load_aliases()
    if target in aliases:
        target = aliases[target]
    apps = ai.load_index(ttl_h=float(flags.get("index_ttl_h", 24.0)), base_dir=DEFAULT_BASE_DIR)
    if not apps:
        logger.warning("[agent_pro] 应用索引为空（扫描失败？），交回原逻辑")
        return ""
    hits = ai.match(target, apps)
    if not hits:
        return ""
    best, sc = hits[0]
    logger.info("[agent_pro] 应用索引命中 %s → %s（%.0f 分，来源 %s）",
                target, best["name"], sc, best["source"])
    cands = _token_order(best)
    if not cands:
        # 防御：新来源若漏了 token 字段，用 exe 兜一个，避免整条启动链 KeyError 崩掉
        exe = best.get("exe") or ""
        if exe:
            cands = [{"type": "exe", "value": exe}]
        else:
            logger.warning("[agent_pro] 索引记录 %s 无可启动信息，交回原逻辑",
                           best.get("name"))
            return ""
    vt = float(flags.get("verify_timeout_s", 10.0))
    last = ""
    for i, tk in enumerate(cands):
        token = dict(tk)
        token["_name"] = best["name"]
        r = launch_with_token(token, a11y=a11y, verify_timeout=vt)
        last = r
        if _is_launch_ok(r):
            if i:
                r += ("（首选启动通道未验证成功，已自动换第 " + str(i + 1)
                      + " 条通道重试）")
            return r
        logger.info("[agent_pro] 第 %d 条启动通道未验证成功（%s），继续尝试下一条",
                    i + 1, tk.get("type"))
    return last
