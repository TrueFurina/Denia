"""摄像头视频采集：cv2.VideoCapture（DirectShow 后端）逐帧取 JPEG，供双工视觉源切换。

设计要点：
- CameraSource 持有一个持久 VideoCapture（不每帧开关，避免重复打开的几百 ms 开销）。
- frame() 线程安全：双工主循环用 asyncio.to_thread 调它，内部加锁。
- 视觉源切换（屏幕 ↔ 摄像头）由 proactive._grab_duplex_screenshot 按 self._vision_source
  决定调本模块还是 mss 抓屏；本模块只负责"读摄像头→JPEG"，不关心业务语义。
- 预览用独立单例 _PREVIEW_SRC，与双工各自持有设备，避免互相抢占（同一物理摄像头在
  Windows 上部分型号允许双开，部分不允许；设置页预览与通话同时开时若冲突，预览侧会
  拿到 None，属预期降级）。
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)


def _resolve_index(device) -> Optional[int]:
    """'default'/None/'0' 等 → int 索引或 None（默认设备）。"""
    if device is None or device == "" or device == "default":
        return None
    try:
        return int(device)
    except (TypeError, ValueError):
        # 设备号不是整数 → 按未指定处理（用默认摄像头）
        return None


class CameraSource:
    """持有一个摄像头设备的采集器；set_device 切换（自动重开）。"""

    def __init__(self, device: object = "default", max_width: int = 768) -> None:
        self._lock = threading.Lock()
        self._device = device
        self._max_width = max_width
        self._cap = None
        self._open_ok = False
        # 关键：构造时【不】打开设备。否则模块级单例 _PREVIEW_SRC 在 import 时即占用
        # 默认摄像头（index 0），导致 list_cameras() 枚举时同一设备被自身占用而 isOpened()
        # 返回 False、单摄像头机器直接漏检。改为首次 frame()/set_device() 惰性打开。

    # ---- 内部（持锁） ----
    def _open_locked(self) -> None:
        try:
            import cv2  # 延迟导入：无 opencv 环境不拖垮其他功能
        except Exception:  # noqa: BLE001
            logger.warning("摄像头采集不可用：未安装 opencv")
            self._open_ok = False
            return
        try:
            idx = _resolve_index(self._device)
            if idx is None:
                cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
            else:
                cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
            if cap is None or not cap.isOpened():
                self._open_ok = False
                self._cap = None
                logger.warning("摄像头打开失败 device=%r", self._device)
                return
            self._cap = cap
            self._open_ok = True
            logger.info("摄像头已打开 device=%r", self._device)
        except Exception:  # noqa: BLE001
            logger.exception("摄像头打开异常 device=%r", self._device)
            self._open_ok = False
            self._cap = None

    def _close_locked(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:  # noqa: BLE001
                pass
            self._cap = None
        self._open_ok = False

    # ---- 公开 API ----
    def set_device(self, device: object) -> None:
        with self._lock:
            if device == self._device and self._open_ok:
                return
            self._close_locked()
            self._device = device
            self._open_locked()

    def set_max_width(self, w: int) -> None:
        self._max_width = max(160, int(w or 768))

    def frame(self) -> Optional[bytes]:
        """抓一帧并编码为 JPEG bytes；失败/未打开返回 None。"""
        with self._lock:
            if not self._open_ok:
                # 惰性打开：构造时不占设备（见 __init__ 注释），这里首次取帧才真正打开。
                self._open_locked()
            if not self._open_ok or self._cap is None:
                return None
            ok, img = self._cap.read()
        if not ok or img is None:
            return None
        try:
            import cv2
            h, w = img.shape[:2]
            if w > self._max_width:
                nh = int(h * self._max_width / w)
                img = cv2.resize(img, (self._max_width, nh))
            ok2, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if not ok2:
                return None
            return buf.tobytes()
        except Exception:  # noqa: BLE001
            logger.debug("摄像头帧编码失败", exc_info=True)
            return None

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    @property
    def is_open(self) -> bool:
        return self._open_ok


def list_cameras() -> list[dict]:
    """探测可用摄像头，返回 [{index, name}]（index 用于配置，name 供 UI 展示）。

    ⚠️ 隔离式探测（2026-09-27 修复侧边栏切换闪退）：
    本机某些摄像头驱动下，OpenCV(DirectShow) 在「枚举→释放」时会触发**原生堆损坏**
    （exit 0xC0000374 STATUS_HEAP_CORRUPTION，Python 层 faulthandler/excepthook 完全拦不住，
    直接杀掉整个进程）。而通话设置页每次进入都会调本函数拉摄像头列表 → 点侧边栏来回切就崩。
    因此把 OpenCV 枚举整个丢进独立子进程：子进程崩了/卡死只死子进程，主程序毫发无伤；
    任何异常（含超时、非零退出码）都返回空列表，UI 优雅降级（不崩）。
    """
    _PROBE = (
        "import sys, json\n"
        "try:\n"
        "    import cv2\n"
        "except Exception:\n"
        "    print(json.dumps([])); sys.exit(0)\n"
        "out = []\n"
        "for i in range(8):\n"
        "    cap = None\n"
        "    try:\n"
        "        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)\n"
        "        if cap is not None and cap.isOpened():\n"
        "            ok = False\n"
        "            for _ in range(3):\n"
        "                try:\n"
        "                    f, frame = cap.read()\n"
        "                except Exception:\n"
        "                    f, frame = False, None\n"
        "                if f and frame is not None:\n"
        "                    ok = True\n"
        "                    break\n"
        "            if ok:\n"
        "                out.append({'index': i, 'name': '摄像头 %d' % (i + 1)})\n"
        "    except Exception:\n"
        "        pass\n"
        "    finally:\n"
        "        if cap is not None:\n"
        "            try:\n"
        "                cap.release()\n"
        "            except Exception:\n"
        "                pass\n"
        "print(json.dumps(out))\n"
    )
    try:
        import sys, json, subprocess, tempfile, os
        fd, path = tempfile.mkstemp(suffix=".py", prefix="cam_probe_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(_PROBE)
            r = subprocess.run(
                [sys.executable, path],
                capture_output=True, text=True, errors='replace', timeout=20,
                cwd=os.path.dirname(os.path.abspath(__file__)),
            )
        finally:
            try:
                os.remove(path)
            except Exception:  # noqa: BLE001
                pass
        if r.returncode == 0 and r.stdout.strip():
            result = json.loads(r.stdout.strip())
            if not result:
                logger.info("摄像头枚举为空（未检测到可用设备，或均被其它程序占用）")
            else:
                logger.info("摄像头枚举结果: %s", result)
            return result
        logger.warning("摄像头枚举子进程异常（退出码=%s），返回空列表降级", r.returncode)
    except Exception:  # noqa: BLE001
        logger.warning("摄像头枚举子进程失败（返回空列表，UI 优雅降级）", exc_info=True)
    return []


# ---- 预览单例（设置页实时预览用，与双工各自持有设备） ----
_PREVIEW_SRC = CameraSource("default")
_PREVIEW_LOCK = threading.Lock()


def grab_preview(device: object = "default") -> Optional[bytes]:
    """取一帧预览 JPEG（base64 之前由调用方编码）。内部维护单例，设备变更时热切换。"""
    with _PREVIEW_LOCK:
        _PREVIEW_SRC.set_device(device)
        return _PREVIEW_SRC.frame()


def close_preview() -> None:
    with _PREVIEW_LOCK:
        _PREVIEW_SRC.close()
