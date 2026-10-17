"""软件关联档案系统（迈从式绑定 UI 的后端数据层）。

设计目标（用户 2026-09-07 提出）：
- 参照迈从(Mofii)电竞驱动「关联游戏/应用」UI：用户可从活跃进程添加软件、
  每条档案含图标/名称/路径/性能档位/类型/知识。
- 档案同时服务于：(1) 前端绑定 UI 展示与编辑；(2) 游戏锁检测匹配
  （替代纯名字白名单，修三角洲空名漏判）；(3) 通知弹窗带软件图标(后续)。
- 性能档位分两档：high_performance（高性能=需 GPU 保护/云端切走）、regular（常规）。

使用方式：
- proactive / webapp 创建 SoftwareProfileRegistry 单例；
- webapp.py 暴露 REST/bridge API 供前端 CRUD；
- proactive 游戏锁判定优先查 registry.is_high_performance(exe_name)；
- 前端调用 Backend.listSoftwareProfiles / addSoftwareProfile / deleteSoftwareProfile 等。
"""
from __future__ import annotations

import base64
import ctypes
import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────────────────────

# 性能档位
TIER_HIGH = "high_performance"   # 高性能：游戏/3D/渲染 → 需 GPU 保护、云端切走
TIER_REGULAR = "regular"         # 常规：办公/聊天/浏览器 → 正常陪伴
ALL_TIERS = (TIER_HIGH, TIER_REGULAR)
TIER_LABELS = {TIER_HIGH: "⚡ 高性能", TIER_REGULAR: "📋 常规"}

# 软件类型
KIND_GAME = "game"
KIND_APP = "app"
ALL_KINDS = (KIND_GAME, KIND_APP)
KIND_LABELS = {KIND_GAME: "游戏", KIND_APP: "软件"}

# 陪伴模式（2026-09-07 整合：OCR 调优 + 快照选择挪进软件档案）
# - snapshot：快照 + OCR（事件驱动，仿游戏方案，本地不占算力）
# - duplex：双工（真全双工，默认）
MODE_SNAPSHOT = "snapshot"
MODE_DUPLEX = "duplex"
ALL_MODES = (MODE_SNAPSHOT, MODE_DUPLEX)
MODE_LABELS = {MODE_SNAPSHOT: "OCR+快照", MODE_DUPLEX: "双工+三段式"}

# 默认持久化文件名
_DEFAULT_FILENAME = "software_profiles.json"

# OCR 区域用途（与 game_ocr.OCR_PURPOSES 对应，开发者面板调优页共用）
OCR_PURPOSES = (
    "kill", "died", "downed", "victory", "defeat",
    "round", "mode", "score", "teammate_down", "general",
)
OCR_PURPOSE_LABELS = {
    "kill": "击杀", "died": "阵亡", "downed": "倒地待救",
    "victory": "胜利", "defeat": "失败", "round": "回合",
    "mode": "模式", "score": "比分", "teammate_down": "队友倒下",
    "general": "通用",
}


# ─────────────────────────────────────────────────────────────────────────────
# 数据模型
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class SoftwareProfile:
    """单条软件档案。"""
    id: str                          # 唯一标识（slug，如 "deltaforce"）
    display_name: str                # 显示名（如 "三角洲行动"）
    exe_name: str = ""               # 进程名（用于前台检测匹配，如 "deltaforceclient-win64-shipping.exe"）
    exe_path: str = ""               # 完整路径（可选，用于图标提取）
    icon_b64: str = ""               # 图标 base64（PNG，从 exe 提取或用户上传）
    tier: str = TIER_REGULAR         # high_performance / regular
    kind: str = KIND_APP             # game / app
    keywords: List[str] = field(default_factory=list)
    knowledge: str = ""              # 专属知识文本
    bind_companion: bool = False     # 是否启用专属陪伴模式
    # ── 陪伴模式 + OCR 调优（2026-09-07 整合：从知识库/全局 config 挪入软件档案）──
    mode: str = MODE_DUPLEX          # snapshot(快照+OCR) / duplex(双工)
    ocr_regions: List[Dict[str, Any]] = field(default_factory=list)  # OCR 识别区块
    #   每项: {"name","purpose","x","y","w","h","keywords"} 归一化坐标 0~1
    player_name: str = ""            # 击杀归属判定用玩家名
    created_at: float = 0.0          # 创建时间戳
    updated_at: float = 0.0          # 更新时间戳

    def __post_init__(self):
        now = time.time()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # 不暴露内部时间戳给前端（可选）
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SoftwareProfile":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ─────────────────────────────────────────────────────────────────────────────
# 图标提取（Windows Shell32 SHGetFileInfoW）
# ─────────────────────────────────────────────────────────────────────────────


def extract_icon_from_exe(exe_path: str, size: int = 128) -> Optional[str]:
    """从 exe 文件提取图标，返回 base64 编码的 PNG；失败返回 None。

    高清策略：
    1. 优先 shell32.ExtractIconExW 取 .ico 原生最大图标（现代 app 多含 256px），
       再 DrawIconEx 缩放到 size —— 比 SHGetFileInfoW 的 32px 大图标清晰得多；
    2. 失败回退 SHGetFileInfoW(LARGEICON) 取系统大图标。
    图标画到白底位图（Windows 图标标准渲染），经临时文件中转给 PIL 转 PNG。
    """
    import struct
    import tempfile
    import os as _os

    try:
        from PIL import Image

        shell32 = ctypes.windll.shell32
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        # 64 位下必须显式声明 argtypes，否则句柄(指针)会被 c_int 截断导致静默失败
        VOIDP = ctypes.c_void_p
        INT = ctypes.c_int
        UINT = ctypes.c_uint
        try:
            user32.GetDC.argtypes = [VOIDP]
            user32.GetDC.restype = VOIDP
            user32.ReleaseDC.argtypes = [VOIDP, VOIDP]
            user32.ReleaseDC.restype = INT
            user32.DrawIconEx.argtypes = [
                VOIDP, INT, INT, VOIDP, INT, INT, UINT, VOIDP, UINT,
            ]
            user32.DrawIconEx.restype = INT
            user32.DestroyIcon.argtypes = [VOIDP]
            user32.DestroyIcon.restype = INT
        except Exception:
            pass
        try:
            gdi32.PatBlt.argtypes = [VOIDP, INT, INT, INT, INT, ctypes.c_uint32]
            gdi32.PatBlt.restype = INT
            gdi32.GetDIBits.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
            ]
            gdi32.GetDIBits.restype = ctypes.c_int
            gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
            gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
            gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
            gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
            gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            gdi32.SelectObject.restype = ctypes.c_void_p
            gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
            gdi32.DeleteDC.restype = ctypes.c_int
            gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
            gdi32.DeleteObject.restype = ctypes.c_int
        except Exception:
            pass

        hicon = None
        # ── 策略 1：ExtractIconExW 原生高清 ──
        try:
            HICON = ctypes.c_void_p
            shell32.ExtractIconExW.argtypes = [
                ctypes.c_wchar_p, ctypes.c_int, ctypes.POINTER(HICON), ctypes.POINTER(HICON), ctypes.c_uint,
            ]
            shell32.ExtractIconExW.restype = ctypes.c_uint
            h_large = HICON(0)
            h_small = HICON(0)
            n = shell32.ExtractIconExW(
                exe_path, 0, ctypes.byref(h_large), ctypes.byref(h_small), 1,
            )
            if n and h_large.value:
                hicon = h_large.value
            elif h_small.value:
                hicon = h_small.value
        except Exception:
            hicon = None

        # ── 策略 2：回退 SHGetFileInfoW ──
        if not hicon:
            try:
                SHGFI_ICON = 0x100
                shell32.SHGetFileInfoW.restype = ctypes.c_uint
                shell32.SHGetFileInfoW.argtypes = [
                    ctypes.c_wchar_p, ctypes.c_uint, VOIDP, ctypes.c_uint, ctypes.c_uint,
                ]
                class SHFILEINFOW(ctypes.Structure):
                    _fields_ = [
                        ("hIcon", ctypes.c_void_p),
                        ("iIcon", ctypes.c_int),
                        ("dwAttributes", ctypes.c_uint),
                        ("szDisplayName", ctypes.c_wchar * 260),
                        ("szTypeName", ctypes.c_wchar * 80),
                    ]
                sfi = SHFILEINFOW()
                if shell32.SHGetFileInfoW(
                    exe_path, 0, ctypes.byref(sfi), ctypes.sizeof(sfi), SHGFI_ICON | 0x0
                ) and sfi.hIcon:
                    hicon = sfi.hIcon
            except Exception:
                hicon = None

        if not hicon:
            return None

        try:
            hdc_screen = user32.GetDC(0)
            hbitmap = gdi32.CreateCompatibleBitmap(hdc_screen, size, size)
            hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
            h_old = gdi32.SelectObject(hdc_mem, hbitmap)

            # 白色背景 + 居中绘制图标
            gdi32.PatBlt(hdc_mem, 0, 0, size, size, 0x000000FF)  # WHITENESS
            user32.DrawIconEx(
                hdc_mem, 0, 0, hicon,
                size, size, 0, None, 0x0003  # DI_NORMAL
            )

            w, h = size, size
            bpp = 32
            stride = ((w * bpp + 31) // 32) * 4
            buf_size = stride * h
            buf = ctypes.create_string_buffer(buf_size)

            bmi_header = struct.pack(
                "<IiiHHIIiiII",
                40, w, -h, 1, bpp, 0, stride * h, 0, 0, 0, 0,
            )
            bmi = bmi_header + b"\x00" * 64
            gdi32.GetDIBits(hdc_mem, hbitmap, 0, h, buf, bmi, 0)

            gdi32.SelectObject(hdc_mem, h_old)
            gdi32.DeleteDC(hdc_mem)
            gdi32.DeleteObject(hbitmap)
            user32.ReleaseDC(0, hdc_screen)

            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".bmp")
            try:
                with _os.fdopen(tmp_fd, "wb") as f:
                    final_bmi = struct.pack(
                        "<IiiHHIIiiII",
                        40, w, -h, 1, bpp, 0, stride * h, 0, 0, 0, 0,
                    )
                    file_hdr = struct.pack(
                        "<2sIHHI", b"BM",
                        14 + 40 + buf_size, 0, 0, 14 + 40,
                    )
                    f.write(file_hdr)
                    f.write(final_bmi)
                    f.write(buf.raw)

                img = Image.open(tmp_path).convert("RGBA")
                img = img.resize((size, size), Image.LANCZOS)

                out = BytesIO()
                img.save(out, format="PNG")
                return base64.b64encode(out.getvalue()).decode("ascii")
            finally:
                try:
                    _os.unlink(tmp_path)
                except OSError:
                    pass
        finally:
            try:
                user32.DestroyIcon(hicon)
            except Exception:
                pass
    except Exception as e:
        logger.warning("[软件档案] 提取图标失败 %s: %s", exe_path, e)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 活跃进程枚举（带图标）
# ─────────────────────────────────────────────────────────────────────────────


def enumerate_running_processes() -> List[Dict[str, Any]]:
    """枚举当前运行的非系统进程，每条返回 {pid, name, exe_path, icon_b64}。

    用于前端「从活跃进程添加」功能。
    只枚举有窗口的前台级进程（排除系统服务/后台守护）。
    """
    results = []
    try:
        import ctypes.wintypes

        # EnumWindows 回调（用基础 ctypes 类型，避免平台差异）
        WNDENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.c_void_p, ctypes.c_long
        )

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # 已收集的 PID 集合（去重）
        seen_pids = set()

        def _is_light_proc(name: str) -> bool:
            """轻量/系统进程过滤。"""
            n = name.lower().strip()
            if not n:
                return True
            light = {
                "explorer.exe", "dwm.exe", "svchost.exe", "rundll32.exe",
                "searchui.exe", "shellhost.exe", "taskmgr.exe", "conhost.exe",
                "system", "idle", "audiodg.exe", "csrss.exe", "winlogon.exe",
                "lsass.exe", "services.exe", "fontdrvhost.exe", "dllhost.exe",
                "sihost.exe", "taskhostw.exe", "smss.exe", "wininit.exe",
            }
            light_prefixes = (
                "chrome", "msedge", "firefox", "brave", "opera",
                "explorer", "code", "electron", "svchost", "dwm", "rundll",
                "searchui", "shell", "taskmgr", "conhost", "python", "pythonw",
                "node", "nodejs", "cmd", "powershell", "wegame", "steam",
                "epicgameslauncher", "egs", "wgame", "origin", "eaapp",
                "battlenet", "xboxapp", "gamingservices", "crashpad",
                "helper", "service", "setup", "installer", "uninstall",
                "update", "launcher", "agent", "daemon", "wps", "qq", "wechat",
                "wxwork", "dingtalk", "feishu", "aliwork",                 "bytedance",
                # 注意：不在此处排除 workbuddy/comni/llama/ollama——用户可能想把这些加入知识库；
                # 自身进程由下方 pid_val == os.getpid() 兜底过滤。
            )
            if n in light:
                return True
            for pfx in light_prefixes:
                if n.startswith(pfx):
                    return True
            return False

        def _enum_cb(hwnd, lp):
            try:
                # 跳过不可见窗口
                if not user32.IsWindowVisible(hwnd):
                    return True

                # 获取窗口所属进程
                pid = ctypes.wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                pid_val = pid.value
                if not pid_val or pid_val in seen_pids or pid_val == os.getpid():
                    return True

                # 打开进程获取映像名
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                hproc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid_val)
                if not hproc:
                    return True

                try:
                    buf = ctypes.create_unicode_buffer(260)
                    nsize = ctypes.c_uint(260)
                    # QueryFullProcessImageNameW
                    kernel32.QueryFullProcessImageNameW(hproc, 0, buf, ctypes.byref(nsize))
                    path_str = buf.value
                    if not path_str:
                        return True

                    name = os.path.basename(path_str)
                    if _is_light_proc(name):
                        return True

                    seen_pids.add(pid_val)

                    # 提取图标（异步/缓存友好：只在明确请求时提取）
                    entry = {
                        "pid": pid_val,
                        "name": name,
                        "exe_path": path_str,
                        "icon_b64": "",  # 延迟提取，前端按需请求
                    }
                    results.append(entry)
                finally:
                    kernel32.CloseHandle(hproc)
            except Exception:
                pass  # 单个进程失败不阻断枚举
            return True

        user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
    except Exception as e:
        logger.warning("[软件档案] 枚举进程异常: %s", e)

    # 提取活跃进程图标：之前图省事把 icon_b64 留空、指望前端按需拉，
    # 但前端并未实现按需拉取 → 活跃进程全显示 📦 兜底。这里直接批量提取
    # （可见窗口的非系统进程数量有限，封顶 80 避免卡顿；失败则留空走前端兜底）。
    try:
        for r in results[:80]:
            try:
                r["icon_b64"] = extract_icon_from_exe(r["exe_path"]) or ""
            except Exception:
                r["icon_b64"] = ""
    except Exception:
        pass

    # 按 name 排序，去重（同名保留第一个）
    unique = {}
    for r in results:
        key = r["name"].lower()
        if key not in unique:
            unique[key] = r
    return sorted(unique.values(), key=lambda x: x["name"].lower())


# ─────────────────────────────────────────────────────────────────────────────
# 注册表（单例 + 持久化）
# ─────────────────────────────────────────────────────────────────────────────


class SoftwareProfileRegistry:
    """软件档案注册表：CRUD + 持久化 + 查询接口。"""

    def __init__(self, data_dir: str = ""):
        self._lock = threading.RLock()
        self._profiles: Dict[str, SoftwareProfile] = {}  # id -> profile
        self._data_dir = data_dir or ""
        self._file_path = ""
        if data_dir:
            self._file_path = os.path.join(data_dir, _DEFAULT_FILENAME)
            self._load()

    # ── 持久化 ──

    def _load(self) -> None:
        """从 JSON 文件加载档案。"""
        if not self._file_path or not os.path.isfile(self._file_path):
            return
        try:
            with open(self._file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for item in data:
                    try:
                        p = SoftwareProfile.from_dict(item)
                        self._profiles[p.id] = p
                    except Exception as e:
                        logger.warning("[软件档案] 加载条目失败: %s", e)
            logger.info("[软件档案] 已加载 %d 条档案 from %s",
                       len(self._profiles), self._file_path)
        except Exception as e:
            logger.warning("[软件档案] 加载文件失败 %s: %s", self._file_path, e)

    def _save(self) -> None:
        """保存到 JSON 文件。"""
        if not self._file_path:
            return
        try:
            data = [p.to_dict() for p in self._profiles.values()]
            # 确保目录存在
            os.makedirs(os.path.dirname(self._file_path), exist_ok=True)
            with open(self._file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("[软件档案] 保存文件失败 %s: %s", self._file_path, e)

    # ── CRUD ──

    def list_all(self) -> List[Dict[str, Any]]:
        """返回所有档案（字典列表，供前端直接用）。"""
        with self._lock:
            return [p.to_dict() for p in self._profiles.values()]

    def get(self, profile_id: str) -> Optional[Dict[str, Any]]:
        """按 ID 查询单条。"""
        with self._lock:
            p = self._profiles.get(profile_id)
            return p.to_dict() if p else None

    def add(self, profile: SoftwareProfile) -> Dict[str, Any]:
        """新增档案。返回保存后的字典。"""
        with self._lock:
            self._profiles[profile.id] = profile
            self._save()
            return profile.to_dict()

    def update(self, profile_id: str, **kwargs) -> Optional[Dict[str, Any]]:
        """更新档案部分字段。返回更新后的字典；不存在则返回 None。"""
        with self._lock:
            p = self._profiles.get(profile_id)
            if not p:
                return None
            for k, v in kwargs.items():
                if k in p.__dataclass_fields__:
                    setattr(p, k, v)
            p.updated_at = time.time()
            self._save()
            return p.to_dict()

    def delete(self, profile_id: str) -> bool:
        """删除档案。成功返回 True。"""
        with self._lock:
            if profile_id in self._profiles:
                del self._profiles[profile_id]
                self._save()
                return True
            return False

    def add_from_process(self, exe_path: str, display_name: str = "",
                         tier: str = TIER_REGULAR, kind: str = KIND_APP) -> Dict[str, Any]:
        """从进程路径快捷创建档案（自动提取图标、生成 ID）。"""
        name = os.path.basename(exe_path)
        slug = os.path.splitext(name)[0].lower().replace("-", "_").replace(".", "_")
        if not display_name:
            display_name = os.path.splitext(name)[0]

        # 提取图标
        icon_b64 = extract_icon_from_exe(exe_path) or ""

        profile = SoftwareProfile(
            id=slug,
            display_name=display_name,
            exe_name=name,
            exe_path=exe_path,
            icon_b64=icon_b64,
            tier=tier,
            kind=kind,
        )
        return self.add(profile)

    # ── 查询接口（供检测层用）──

    def is_high_performance(self, exe_name: str) -> bool:
        """给定进程名是否属于高性能档位？"""
        with self._lock:
            for p in self._profiles.values():
                if p.exe_name and p.exe_name.lower() == exe_name.lower():
                    return p.tier == TIER_HIGH
            return False

    def get_tier(self, exe_name: str) -> str:
        """获取进程名的性能档位；未找到返回 regular。"""
        with self._lock:
            for p in self._profiles.values():
                if p.exe_name and p.exe_name.lower() == exe_name.lower():
                    return p.tier
            return TIER_REGULAR

    def find_by_exe_name(self, exe_name: str) -> Optional[SoftwareProfile]:
        """按进程名查找档案。"""
        with self._lock:
            for p in self._profiles.values():
                if p.exe_name and p.exe_name.lower() == exe_name.lower():
                    return p
            return None

    def get_mode(self, exe_name: str) -> Optional[str]:
        """返回进程名对应档案的陪伴模式（snapshot/duplex）；未找到返回 None。"""
        p = self.find_by_exe_name(exe_name)
        return p.mode if p is not None else None

    def find_high_performance_procs(self) -> List[str]:
        """返回所有高性能档位的进程名列表（供游戏锁快速匹配）。"""
        with self._lock:
            return [
                p.exe_name.lower()
                for p in self._profiles.values()
                if p.exe_name and p.tier == TIER_HIGH
            ]

    def count(self) -> int:
        """档案总数。"""
        with self._lock:
            return len(self._profiles)


# ─────────────────────────────────────────────────────────────────────────────
# 模块级单例（懒初始化，由 proactive/webapp 在启动时 set）
# ─────────────────────────────────────────────────────────────────────────────

_REGISTRY: Optional[SoftwareProfileRegistry] = None


def get_registry() -> Optional[SoftwareProfileRegistry]:
    return _REGISTRY


def set_registry(reg: Optional[SoftwareProfileRegistry]) -> None:
    global _REGISTRY
    _REGISTRY = reg


def get_or_create_registry(data_dir: str = "") -> SoftwareProfileRegistry:
    """取单例；不存在则创建并注册（幂等）。

    【2026-09-10 修复·重要接线遗漏】原实现里 `_REGISTRY` 恒为 None：
    `set_registry()` 全项目无人调用，webapp 只建了自己的 `self._spr`（不注册单例），
    而 `proactive._software_profiles` 从未赋值 → 恒 None。后果：proactive 侧
    **软件档案的全部用途失效**——`is_high_performance`（高性能兜底锁）、`get_mode`
    （游戏锁按 exe 判快照/双工）、`find_by_exe_name` 全部跳过，导致"进游戏不切云端/不进
    高性能陪伴"（三角洲就靠这个兜底命中，因为它的窗口标题常为空、外部显存也不涨）。
    统一入口后 webapp 与 proactive 共用同一实例。
    """
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = SoftwareProfileRegistry(data_dir=data_dir)
    return _REGISTRY
