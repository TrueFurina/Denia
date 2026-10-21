"""pywebview 角色管理后端。

暴露 Python 方法给前端 JS 调用，实现角色管理功能。
支持注入运行时（dialogue/bus/loop），切换角色时同步到主程序。
"""
from __future__ import annotations

import asyncio
import ctypes as _ct   # 通知浮窗几何计算 / 窗口滑动（模块级，2026-09-04）
import itertools
import glob
import json
import logging
import os
import re
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import queue
import base64
import io
import zipfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional



import yaml

from ..config import Config, load_config
from ..event_bus import Event, EventType
from ..perception.privacy import privacy_mode, set_privacy_mode
from ..companion.call_av_log import log_call_av
from . import mode_gates as _mode_gates   # 档位×功能 门槛矩阵（单一真相源，勿在别处另写一份）

logger = logging.getLogger(__name__)

# 参考音录音的序号（模块级，不是实例级）：设置页连点两次「录音」时文件名必须不同，
# 否则第二次把第一次的录音覆盖掉。用毫秒时间戳做过一次，**同一毫秒内会撞名** ——
# 是自测「两次不撞名」抓到的（2026-10-21）。
_REF_AUDIO_SEQ = itertools.count(1)


# ---- 自 webapp.py 抽出的模块（2026-10-17 拆分，逐字迁移、未改逻辑）----
from .ui_fmt import (  # noqa: E402
    _fmt_file_ts, _fmt_ts, _img_data_url, _path_has_non_ascii, _status_done_summary,
)
from .bailian_key import (  # noqa: E402
    _collect_bailian_keys, _diffuse_bailian_key, _key_unify_enabled,
    _looks_like_bailian_key, _unify_bailian_key,
)
from .wv2_env import (  # noqa: E402
    _harden_webview2, _install_clr_crash_guard, _patch_webview2_unique_user_data,
    _run_coroutine_sync, _safe_js_api, _stable_wv2_folder_key,
    _sweep_legacy_wv2_folders, _sweep_wv2_caches,
)
from .notify_geom import (  # noqa: E402
    _notify_current_monitor_rect, _notify_geom, _notify_park_out,
    _notify_rects_for_monitor, _notify_set_pos, _notify_show_in, _notify_slide,
)
from .ui_state import STATE  # 窗口/运行时共享状态（原模块级全局，2026-10-17 收敛）
from .api_pet import PetAPI  # 桌宠 API mixin（2026-10-17 拆分）
from .api_window import WindowAPI  # 窗口 API mixin（2026-10-17 拆分）
from .pet_window import (  # 桌宠 Win32 操作（2026-10-17 拆分）
    _POINT, _attach_pet_no_taskbar, _pet_find_main_hwnd, _pet_find_hwnd,
    _pet_apply_topmost, _pet_install_topmost_guard, _pet_set_capture_hidden,
)
from .main_window import (  # 窗口创建/本地服务（2026-10-17 拆分，re-export 给 main.py）
    _FREEZE_DUMPS, _GUI_STALL_S, _bailian_inject_loop, _ensure_web_server,
    _wv2_broadcast, create_bailian_console_window, create_desktop_companion,
    create_main_window, create_notify_window, run_webview,
    # 卡死现场 dump 在 2026-10-17 随窗口代码一起搬去了 main_window，
    # main.py 仍从 webapp 取这个名字 → 少了这一行就是**启动即 ImportError**。
    # 2026-10-18 实测：main.py --help 直接崩，说明这条链路自那次拆分后就没跑通过。
    _start_gui_liveness_probe,
)

# /local_img 路由的额外白名单目录（2026-10-03 聊天图片）：char_banners 之外，
# 聊天发图落盘目录（ActivityStore root/images/chat，root 可能被配置自定义）
# 由 _save_chat_images() 首次落盘时注册进来；HTTP handler 运行时读取本列表。


def register_local_img_dir(path: str) -> bool:
    """把一个本地目录注册进 `/local_img` 的白名单（防路径穿越）。

    【2026-10-13 新增】表情包技能包接进主程序后，表情图在技能包自己的
    `data/packs/<id>/images/` 下，位于 web 根之外，`<img src>` 直接加载会被
    同源策略挡掉。这里复用已有的 /local_img 只读路由 + 白名单机制放行。

    只接受**已存在的目录**，并做绝对化；失败返回 False（调用方自行降级）。
    """
    try:
        if not path:
            return False
        _abs = os.path.abspath(path)
        if not os.path.isdir(_abs):
            return False
        if _abs not in STATE.local_img_extra_dirs:
            STATE.local_img_extra_dirs.append(_abs)
        return True
    except Exception:  # noqa: BLE001
        return False

# 启动加载步骤（加载窗口分步显示，与 main.py 上报的 phase 索引一一对应）
LOAD_PHASES = [
    "加载配置文件",
    "检查 LLM 与语音环境",
    "启动 WebSocket 服务",
    "初始化视觉模块",
    "初始化语音模块",
    "启动语音识别",
    "初始化角色与对话",
    "加载音色模型",
    "启动完成",
]


class StartupProgress:
    """启动加载进度（线程安全，供加载窗口轮询）。

    由 main.py 的启动链路（asyncio 线程 + 启动监督线程）更新，
    加载窗口通过 LoadingAPI.get_progress() 轮询快照。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._percent = 0
        self._msg = "正在初始化…"
        self._phase = 0
        self._done = False
        self._error = ""

    def update(self, percent: int, msg: str = "", phase: Optional[int] = None) -> None:
        """更新进度：percent 0~100；msg 状态文字；phase 对应 LOAD_PHASES 索引。"""
        with self._lock:
            self._percent = max(0, min(100, int(percent)))
            if msg:
                self._msg = msg
            if phase is not None:
                self._phase = phase

    def done(self) -> None:
        """标记全部加载完成。"""
        with self._lock:
            self._percent = 100
            self._done = True

    def fail(self, error: str) -> None:
        """标记加载失败（加载窗口显示错误后由监督线程收尾）。"""
        with self._lock:
            self._error = str(error)
            self._done = True

    def snapshot(self) -> dict:
        """线程安全快照（供前端轮询）。"""
        with self._lock:
            return {
                "percent": self._percent,
                "msg": self._msg,
                "phase": self._phase,
                "done": self._done,
                "error": self._error,
            }


class LoadingAPI:
    """启动加载窗口的前端 API（轮询后端启动进度）。"""

    def __init__(self, progress: StartupProgress) -> None:
        self._progress = progress

    def get_progress(self) -> dict:
        """返回当前启动进度快照。"""
        return self._progress.snapshot()










# ============================================================================
# 【2026-10-08 P1】百炼 Key 统一收口（key_unify）
# ----------------------------------------------------------------------------
# 病根（实测取证）：
#   `_diffuse_bailian_key` 这个「一处填、全域生效」的正解**全校只有 1 个调用点**
#   —— webapp.py:9257（引导页落盘，且入口条件是 `_prov.prov == "bailian"`）。
#   而设置页的三条写入路径各自只写自己那一格、从不扩散：
#     · save_text_chat_providers   → text_chat.providers[i].api_key（并派生 llm.openai）
#     · save_llm_settings          → llm.openai.api_key
#     · set_voice_mode             → voice.clone.cloud.api_key
#   后果：同一个百炼账号可以在 3~4 处留下**不同的** Key。
#   实测当前 config：115 位（28 处，引导页扩散来的）/ 117 位（text_chat[0] bailian-chat）
#   / 116 位（voice.clone.cloud），三者互不相同 → 「设置页显示已配、实际跑的那条用了别的 Key」。
#
# 处置（守「不改变既有行为」红线）：
#   1) 本函数是**唯一收口入口**，三条写入路径在保存成功后各调一次；
#   2) 默认 **关闭**（`key_unify.enabled` 缺省 False）→ 不改变任何用户现有 config 的行为，
#      用户/引导页显式打开后才生效；
#   3) 复用既有 `_diffuse_bailian_key` 的保守规则（只认 `sk-` 前缀、跳过 `sk-api-`），
#      不新造第二套匹配逻辑；
#   4) **跳过空 key**（空 key 扩散会把已配好的 28 处全清空 —— 灾难）；
#   5) 只处理「百炼形态」（sk-ws- / sk- 开头且非 sk-api-），绝不碰智谱短 key、
#      MiniMax 的 sk-api-、ollama 的空 key。
# ============================================================================









# 「陪你看视频」进度卡的时间线（2026-10-05）：后端按 stage 顺序补进 meta.steps，
# 前端 agent 卡据此渲染可勾选的步骤条（与设计稿的步骤时间线一致）。
_WATCH_STAGE_ORDER = ("resolve", "download", "frames", "done")
_WATCH_STEP_LABELS = (
    ("resolve", "解析链接"),
    ("download", "下载视频"),
    ("frames", "抽取关键帧"),
    ("done", "看画面"),
)






# 【2026-10-17 修复·失效 TTS 模型自动迁移】
# 实测：qwen3-tts-flash 是唯一 200 的百炼 TTS 模型；
# qwen-audio-3.0-tts-* 与 cosyvoice 全系列已下线（400/418）。
# 旧版一键配置写入的默认值是 qwen-audio-3.0-tts-plus → 用户配置残留失效模型
# → 试听永远失败，用户以为音色 ID 错了。
#
# 【2026-10-19 合并后整改】迁移实现搬到 `src/config_migrate.py`：
#   ① 原 `migrate_stale_voice_profiles` 在这里定义了却**从未被调用**（死代码），
#      搬出去才能被轻量测试直接打；
#   ② 原写盘是 `io.open(path, "w")` 裸写（非原子、不 fsync、不拿锁），与
#      Config.save() 审计出的 P0「写出半个 YAML → 之后所有保存永久失败」同型。
# 这里只导入真正要用的两个入口。原来的 `_TTS_STALE` / `migrate_stale_tts_model`
# 等名字不再导出——全仓没有外部引用，留着只会让人以为 webapp 内部还在用它们。
from ..config_migrate import (  # noqa: E402
    dump_config_atomic,
    migrate_stale_tts_config,
)



class CharacterAPI(PetAPI, WindowAPI):
    """角色管理 API（供 pywebview 前端调用）。"""

    # 【2026-10-05】状态卡"节奏"相关字段的**类级默认值**。
    #   放在类上而不是只在 __init__ 里，是因为验证脚本/单测常用
    #   `CharacterAPI.__new__(CharacterAPI)` 造桩（不走 __init__），
    #   缺字段会让 `_on_agent_status` 直接 AttributeError。
    #   实例会在 __init__ 里覆盖成各自的运行时值。
    _AGENT_CARD_MIN_LIFE = 3.2      # 秒：状态卡最小存活期（治"短操作一闪而过"；【2026-10-08】2.4→3.2）
    _chat_status_born_ts = 0.0
    _chat_status_drop_timer = None
    # 【2026-10-05】True 时"完成转历史卡"改为真删除（清空聊天场景用）
    _chat_keep_done = True

    def __init__(self, config_path: str = "config.yaml") -> None:
        self._config_path = config_path
        # 【2026-10-17 / 2026-10-19 接线】失效 TTS 模型 + 音色档自动迁移。
        # 走 migrate_stale_tts_config 一次跑完两半——此前只跑了模型那一半，
        # 音色档的迁移函数写好了却从没被调用（死代码）。
        # 写盘走 dump_config_atomic（临时文件 + fsync + os.replace）：原来是
        # `io.open(path,"w")` 裸写，与 Config.save() 审计出的 P0 同型。
        try:
            import yaml as _y_mig
            with io.open(self._config_path, encoding="utf-8") as _f_mig:
                _raw_mig = _y_mig.safe_load(_f_mig) or {}
            _mig = migrate_stale_tts_config(_raw_mig)
            if _mig:
                # 迁移是自动改用户的 config.yaml，而 yaml.dump 会丢注释，
                # 所以留一份 .bak 当后路（只留第一次，反复启动不会刷备份）。
                if dump_config_atomic(self._config_path, _raw_mig, backup=True):
                    logger.info("TTS 失效配置已自动迁移: %s", ", ".join(_mig))
                else:
                    logger.warning(
                        "TTS 失效配置未能写盘（下次启动会再试）: %s",
                        ", ".join(_mig))
        except Exception:  # noqa: BLE001
            logger.debug("TTS 失效配置迁移跳过", exc_info=True)
        self._cfg: Optional[Config] = None

        # 运行时注入（main.py 启动时注入 dialogue / bus / loop）
        self._runtime_lock = threading.Lock()
        # 【2026-10-19】供 Config.save() 跨模块取同一把运行时锁（原子写 config.yaml）
        globals()['_LAST_API_INSTANCE'] = self
        self._dialogue: Optional[Any] = None
        self._bus: Optional[Any] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._tts: Optional[Any] = None
        self._capture: Optional[Any] = None   # 屏幕采样（调试台）
        self._proactive: Optional[Any] = None  # 主动搭话（调试台）
        self._monitor: Optional[Any] = None    # 前台监听（调试台）
        self._ocr: Optional[Any] = None        # 游戏 OCR（调试台）
        self._audio: Optional[Any] = None      # 游戏音效识别（调试台）
        self._asr: Optional[Any] = None        # 语音识别（持续对话开关）
        self._memory_store: Optional[Any] = None  # 长期记忆（记忆设置开关）
        self._vision_router: Optional[Any] = None  # 视觉模型路由（模型设置切换用）
        self._activity_reporter: Optional[Any] = None  # 活动记忆 + 日报/周报（记忆页）
        self._course_recorder: Optional[Any] = None    # 网课记录（课程页）
        self._mount_tts: Optional[Any] = None  # 本地 TTS 服务按需挂载回调 (engine, api_url, name) -> dict
        self._unload_tts: Optional[Any] = None  # 无声模式卸载本地 TTS 服务回调 (无参) -> None
        # 试听任务（异步任务+轮询，避免「立刻返回 success 但模型还没加载」假加载 UI）
        self._voice_tasks: dict = {}            # task_id -> {phase, done, success, error, message}
        self._voice_task_seq: int = 0
        self._voice_task_lock = threading.Lock()
        self._calib_thread: Optional[threading.Thread] = None  # OCR 区域校准悬浮窗线程
        self._calib_stop: Optional[threading.Event] = None     # OCR 校准悬浮窗关闭信号
        # 对话悬浮层（游戏内透明文本框：显示 AI 的话 + 语音文字）
        self._overlay_thread: Optional[threading.Thread] = None
        self._overlay_stop: Optional[threading.Event] = None
        self._overlay: Optional[dict] = None   # {"ov": ChatOverlay}，push 走 queue 线程安全

        # 聊天消息缓冲（GUI 聊天 Tab 用，线程安全追加/快照）
        self._chat_buffer: deque = deque(maxlen=200)
        self._chat_seq: int = 0
        # 【2026-10-10 补·聊天历史持久化】webapp 自有聊天日志（data/chatlog/<char>.jsonl），
        # 与关系层 data/history 解耦；启动/切角色时回填 _chat_buffer，重启后聊天不空白。
        self._chatlog: Optional[Any] = None
        try:
            from ..companion.history_store import ChatLogStore
            self._chatlog = ChatLogStore()
        except Exception:  # noqa: BLE001 落盘失败绝不能影响聊天本身
            logger.exception("[聊天历史] ChatLogStore 初始化失败（将不持久化）")
        self._chat_history_loaded: bool = False
        # 【2026-10-11 上下文持久化·重启不失忆】喂给大模型的会话上下文 origin 是
        #   shared_history —— 它原本**纯内存**，重启即归零，而界面（chatlog）却能把历史
        #   读回来 ⇒ 「人看得见、她看不见」的失忆（用户实锤："问她为什么严肃道歉、是不是
        #   有什么误会"——因为她的上下文里只有那一句）。这里开启写穿透 + 启动回填，
        #   让上下文像 chatlog 一样落 `data/context/<char>.jsonl`（按角色隔离）。
        self._ctx_persist_ok: bool = False
        self._init_context_persistence()
        self._chat_stream_id: Optional[int] = None  # 流式生成中的 AI 消息 id（打字机）
        self._chat_seg_count: int = 0               # 【2026-10-11 分段输出】本轮已上屏的气泡数
        # 【2026-10-11·聊天分段输出（类 AstrBot 连发）】把一次 AI 回复按句拆成**多条气泡**
        #   逐条上屏，像真人发微信"一句一句说"，而不是一大坨挤在一个气泡里。
        #   · 与 TTS 解耦：TTS 仍订阅 PARTIAL_TEXT 逐句念，本项只改聊天界面的分组展示。
        #   · 回退：chat_split_enabled=False 即恢复旧的"单气泡打字机"。
        #   · 落盘/上下文不受影响：每段封口后按普通消息落 chatlog，重载后仍是多条（一致）。
        self.chat_split_enabled: bool = True    # 总开关
        # 气泡条数上限：真正的"她该回几句"由后端 dialogue._reply_budget 按用户话长定，
        #   随 PARTIAL_TEXT 的 seg_limit 带来；这里只是**绝对防刷屏兜底**（不能再比它紧，
        #   否则长话本可发 4~5 条会被砍到 3 条）。
        self.chat_split_max: int = 6
        self.chat_split_min_chars: int = 4      # 当前气泡短于此长度不封口（避免"嗯。"单独成条）
        self._chat_status_id: Optional[int] = None  # Agent 执行状态气泡 id（单条复用，2026-10-02）
        # 【2026-10-05 治"卡片一闪就没"】状态卡的**最快节奏**由它决定：
        #   短操作（如 open_app 只花 200~600ms）时，卡片刚上屏就被真实回复接管删掉，
        #   展开动画（约 520ms）+ 步骤滑入根本来不及播，观感就是"闪一下没了、UI 跟不上"。
        #   这里给卡片一个**最小存活期**：不足则延迟删除，让前端有时间把
        #   「执行中 → 步骤逐条打勾 → 完成 → 缩回」完整演一遍。前端 800ms 轮询会自动
        #   发现该行消失并播放退场动画（不需要 broadcast，故 Timer 里只动 deque）。
        #   想让卡片停留更久/更短，改这一个数即可（单位：秒）。
        self._AGENT_CARD_MIN_LIFE = 3.2   # 【2026-10-08】2.4→3.2，见类级注释
        self._chat_status_born_ts: float = 0.0
        self._chat_status_drop_timer: Optional[threading.Timer] = None

        # 【2026-10-02 新增】通话记录（独立于聊天页）：双方文本 + 达妮娅语音存档 URL。
        # 通话时说的话只进这里，由「通话记录」UI 展示/回放，不再混进聊天页。
        # 【2026-10-04 改】改为「按通话开始/结束切分会话」：一次拨号↔挂断 = 一个会话
        # （session_id 由 start_duplex_call 开、_on_call_speech 逐句打标）。记录落盘持久化，重启不丢。
        self._call_records: list = []
        self._call_rec_seq: int = 0
        self._call_records_path = os.path.join(
            os.path.dirname(os.path.abspath(self._config_path)), "call_records.json")
        # 当前通话会话 id（拨号开、挂断留待迟到消息并入、下次拨号再开新）：None=未在通话
        self._cur_call_session_id: Optional[str] = None
        self._cur_call_session_start: float = 0.0
        self._load_call_records()

        # 桌宠模式：主窗口 / 桌面桌宠窗口引用（main.py 创建窗口后注入）+ 桌宠位置缓存
        self._main_win: Optional[Any] = None
        self._pet_win: Optional[Any] = None
        # 上限 20 条：pop_pet_bubble 只显示最新一条并清空积压，桌宠窗隐藏时
        # 生产端（双工线程）仍在推，无界会一路涨。
        self._pet_bubble_q: "queue.Queue" = queue.Queue(maxsize=20)
        self._duplex_output_log: list = []   # 双工输出对话日志（调试台「双工输出」板块展示，存 {"ts":float,"text":str}）
        self._pet_pos: Optional[tuple] = None   # 桌宠窗口逻辑坐标缓存 (x, y)（仅 pet_show 暂存，拖动已不依赖）
        self._pet_base: Optional[tuple] = None  # 拖动基准：窗口左上角物理像素
        self._pet_cursor0: tuple = (0, 0)       # 拖动起点光标物理像素
        self._pet_dragging: bool = False        # 是否处于拖动会话
        self._pet_visible: bool = False         # 桌宠可见性（pywebview 无 is_visible，自维护）
        self._main_win_closed: bool = False     # 主窗口是否已关闭（后台模式桌宠接管后，恢复时需重建）
        
        # 系统托盘管理（只剩一个保守标志：默认 False = 拒绝隐藏窗口。
        # 真实的托盘在 main.py 里，见本节开头那段说明）
        self._tray_enabled: bool = False        # 是否启用系统托盘
        
        # 桌宠对话上下文管理（保持对话连贯性）
        self._pet_conversation_history: list[dict] = []  # 最近的互动历史
        self._pet_last_interaction: float = 0            # 上次互动时间戳
        self._pet_context_cache: dict = {}              # 上下文缓存
        
        # 桌宠互动响应缓存（优化响应速度）
        self._pet_response_cache: dict = {}              # 缓存 {context_hash: (response, timestamp)}
        self._cache_ttl: float = 30.0                   # 缓存有效期30秒

        # —— UI 心跳看门狗（2026-09-03 卡死排查）——
        # 前端每 5s 调 ui_ping()；后端记录最后心跳时间。守护线程检测到心跳中断
        # （连续 > UI_PING_TIMEOUT 秒未收到，且已收到过首跳）即打 WARNING，
        # 用于区分「后端挂了」(日志整体静默) vs「UI 卡死」(后端照常跑但前端不动)。
        self._last_ui_ping: float = 0.0
        self._ui_ping_armed: bool = False        # 收到过至少一次心跳才进入判定
        self._ui_unresponsive_logged: bool = False
        self._boot_ts: float = time.time()       # 启动宽限期基准（模型加载会饿前端心跳）
        self._ui_watchdog = threading.Thread(target=self._ui_watchdog_loop, daemon=True)
        self._ui_watchdog.start()

        # —— UI 诊断埋点（2026-09-05）：记录最近一次发消息耗时/结果，供开发者页健康面板读取 ——
        self._last_send_chat: dict = {
            "ts": 0, "ms": 0, "ok": None, "locked": False,
            "text_len": 0, "err": None, "note": "",
        }

        self._load_config()

    # ======== 页面渲染确认（2026-08-24 黑屏排查） ========
    # 前端页面加载完成后由内联脚本 setTimeout 调用此方法，绕过 pywebview
    # evaluate_js 的线程/事件时序限制，真正确认页面在渲染进程里渲染成功。

    # ======== UI 心跳看门狗（2026-09-03 卡死排查） ========
    # 前端主窗口 JS 每 5s 调一次 ui_ping()，后端记录时间戳。若前端渲染进程挂死，
    # JS 定时器停摆 → 心跳中断 → 后端守护线程打 WARNING，日志即可定位「UI 卡死」。
    UI_PING_TIMEOUT: float = 30.0   # 连续 30s 无心跳才判无响应（模型加载期会饿前端心跳）
    UI_RECOVER_GRACE_S: float = 120.0  # 启动宽限期：前 120s 只记日志，绝不触发恢复
    UI_PING_INTERVAL: float = 5.0  # 守护线程自身轮询间隔






    # ======== 看板娘 ========



    # ======== 桌宠模式（桌面浮动看板娘窗口：点击互动 / 拖动 / 右键面板） ========















    
    

    
    def _generate_cache_key(self, context: str) -> str:
        """生成缓存键（基于上下文和互动历史）。"""
        import hashlib
        
        # 基础上下文
        base_context = f"{context}_{self._get_time_of_day()}"
        
        # 添加最近互动的摘要
        recent_summary = self._get_recent_interactions_summary()
        
        # 创建缓存键
        cache_data = f"{base_context}_{recent_summary}"
        return hashlib.md5(cache_data.encode('utf-8')).hexdigest()
    
    def _get_cached_response(self, cache_key: str, current_time: float) -> Optional[dict]:
        """获取缓存的响应（如果未过期）。"""
        if cache_key in self._pet_response_cache:
            response, timestamp = self._pet_response_cache[cache_key]
            if current_time - timestamp <= self._cache_ttl:
                return response
            else:
                # 缓存过期，删除
                del self._pet_response_cache[cache_key]
        return None
    
    def _cache_response(self, cache_key: str, response: dict, timestamp: float):
        """缓存响应。"""
        # 限制缓存大小（最多50个条目）
        if len(self._pet_response_cache) >= 50:
            # 删除最旧的缓存
            oldest_key = min(self._pet_response_cache.keys(), 
                           key=lambda k: self._pet_response_cache[k][1])
            del self._pet_response_cache[oldest_key]
        
        self._pet_response_cache[cache_key] = (response, timestamp)
        
        # 清理过期缓存
        current_time = timestamp
        expired_keys = [k for k, (_, t) in self._pet_response_cache.items() 
                       if current_time - t > self._cache_ttl]
        for key in expired_keys:
            del self._pet_response_cache[key]
    
    
    
    def _get_time_of_day(self) -> str:
        """获取时间段描述。"""
        import time
        hour = time.localtime().tm_hour
        if 5 <= hour < 12:
            return "早晨"
        elif 12 <= hour < 14:
            return "中午"
        elif 14 <= hour < 18:
            return "下午"
        elif 18 <= hour < 22:
            return "傍晚"
        else:
            return "夜晚"
    
    def _get_recent_interactions(self, count: int) -> list[dict]:
        """获取最近的互动记录。"""
        return self._pet_conversation_history[-count:]
    
    def _get_recent_interactions_summary(self) -> str:
        """获取最近互动的摘要描述。"""
        recent = self._get_recent_interactions(2)
        if not recent:
            return "这是第一次互动"
        
        summaries = []
        for i, interaction in enumerate(recent, 1):
            summaries.append(f"第{i}次: {interaction['context']} - {interaction['text'][:20]}...")
        
        return "；".join(summaries)
    
    def _add_to_conversation_history(self, context: str, text: str, action: str, expression: str):
        """添加互动记录到历史。"""
        import time
        self._pet_conversation_history.append({
            "timestamp": time.time(),
            "context": context,
            "text": text,
            "action": action,
            "expression": expression
        })
        
        # 保持历史记录在合理范围内（最多20条）
        if len(self._pet_conversation_history) > 20:
            self._pet_conversation_history = self._pet_conversation_history[-20:]
    
    
    
    # ======== 系统托盘管理 ========
    #
    # 【2026-10-21 清理】原来这里有 `enable_tray` / `set_minimize_to_tray` /
    # `restore_from_tray` 三个方法，全部是**假接口**：只改一个没人读的布尔字段，
    # 然后 `return {"ok": True}`。而真正的托盘在 `main.py` 里由 `TrayManager` 无条件
    # 启动、由 `events.closing` 拦截实现「点 X → 隐藏到托盘」，跟这三个方法毫无关系。
    # 也就是说前端调 `enable_tray(False)` 会被告知"成功关闭了托盘"，实际什么都没变。
    # 已删除 —— 要做的是**功能**（关窗行为开关），不是把假接口接到界面上。
    #
    # 下面两个保留，因为 `src/ui/web/app.js`（legacy 页）真的在调，而且它们的语义是
    # **保守方向**的：`_tray_enabled` 默认 False，于是 `minimize_to_tray()` 会拒绝隐藏
    # 窗口（"系统托盘未启用"）而不是隐藏了找不回来。真要让它跟着真实托盘状态走，得把
    # TrayManager 注入进来（`set_runtime` 目前不传），属于另一个改动。
    def minimize_to_tray(self) -> dict:
        """最小化到系统托盘。

        Returns:
            操作结果
        """
        if not self._tray_enabled:
            return {"ok": False, "error": "系统托盘未启用"}
        
        if self._main_win is not None:
            try:
                self._main_win.hide()
                return {"ok": True, "message": "已最小化到托盘"}
            except Exception as e:
                logger.error("最小化到托盘失败: %s", e)
                return {"ok": False, "error": str(e)}
        
        return {"ok": False, "error": "主窗口不存在"}
    
    def is_tray_enabled(self) -> bool:
        """检查系统托盘是否启用。

        Returns:
            是否启用系统托盘
        """
        return self._tray_enabled

    # ======== 看板娘表情特效位置（泪滴/zzz 拖拽定位，供前端保存/读取） ========


    # ======== 运行时注入 ========
    def set_runtime(self, dialogue=None, bus=None, loop=None, tts=None,
                    capture=None, proactive=None, monitor=None, ocr=None,
                    audio=None, asr=None, memory_store=None,
                    vision_router=None, activity_reporter=None,
                    course_recorder=None, relationship_reporter=None,
                    mount_tts=None,
                    unload_tts=None) -> None:
        """注入主程序运行时，用于同步角色切换。"""
        with self._runtime_lock:
            self._dialogue = dialogue
            # 【2026-10-02】把视觉路由注入 dialogue：聊天发图 / 看视频走视觉模型时用。
            if dialogue is not None and vision_router is not None \
                    and hasattr(dialogue, "set_vision_router"):
                try:
                    dialogue.set_vision_router(vision_router)
                except Exception:  # noqa: BLE001
                    logger.debug("注入视觉路由到 dialogue 失败", exc_info=True)
            self._bus = bus
            self._loop = loop
            self._tts = tts
            self._capture = capture
            self._proactive = proactive
            self._monitor = monitor
            self._ocr = ocr
            self._audio = audio
            self._asr = asr
            self._memory_store = memory_store
            self._vision_router = vision_router
            # 【2026-10-02】额度耗尽提醒：视觉模型免费额度用完时，广播给前端弹询问浮层
            # （问用户是否切换到还有额度的模型），避免静默失败 + 每 60s 重试刷屏。
            try:
                if vision_router is not None and hasattr(vision_router, "set_quota_alert_callback"):
                    vision_router.set_quota_alert_callback(self._on_vision_quota_alert)
            except Exception:  # noqa: BLE001
                logger.debug("注入额度提醒回调失败（忽略）", exc_info=True)
            self._activity_reporter = activity_reporter
            self._course_recorder = course_recorder
            self._relationship_reporter = relationship_reporter
        self._mount_tts = mount_tts
        self._unload_tts = unload_tts
        # 【2026-10-02 新增】注册弹窗动作回调：用户在右上角弹窗点"用云端陪我/就这样/
        # 以后别提醒"时，由 proactive 执行并记住选择（弹窗本身保持纯 UI）。
        if proactive is not None and hasattr(proactive, "register_notify_actions"):
            try:
                proactive.register_notify_actions()
            except Exception:  # noqa: BLE001
                logger.debug("注册弹窗动作回调失败（忽略）", exc_info=True)
        logger.info("运行时已注入 pywebview API")
        # 【2026-09-17 扬声器设备】启动即把用户上次选定的输出设备下发 TTS。否则重启后
        # 悄悄回到系统默认设备，表现为"我明明选过扬声器，怎么又跑别处去了"。
        try:
            if tts is not None and hasattr(tts, "set_output_device_from_cfg") and self._cfg:
                tts.set_output_device_from_cfg(self._cfg)
        except Exception:  # noqa: BLE001
            logger.debug("启动下发扬声器设备失败（忽略）", exc_info=True)
        # 【启动即开聊天采集（2026-09-03 修复）】此前 start_chat_capture 仅在开浮窗时
        # 调用，导致不主动开浮窗时 AI_DECISION 无人订阅——主界面聊天与桌宠气泡都收不到
        # 主动互动（双工开口/点评不进 UI）。改为运行时注入完成即开启，覆盖主界面/桌宠/
        # 浮窗所有 UI。start_chat_capture 幂等（_chat_capture_started 防重复订阅），
        # 之后开浮窗再调也安全。
        try:
            self.start_chat_capture()
        except Exception:  # noqa: BLE001
            logger.debug("启动开聊天采集失败", exc_info=True)
        # 【2026-10-11·上下文持久化】到这里 config/dialogue 才真正就绪（构造期太早，
        #   活动角色可能还没设定）→ 此刻把该角色的历史上下文灌回 shared_history。
        #   幂等（桶非空不会重复灌），重复调用安全。
        try:
            self._restore_context_for_active_char()
        except Exception:  # noqa: BLE001
            logger.debug("回填会话上下文失败", exc_info=True)
        # 【双工占卡→聊天跳过本地端口（2026-09-03 修复打字锁死）】按启动时的双工模式
        # 同步聊天路由的 skip_local 标志：local_omni/local 占满 GPU 时聊天不走本地
        # ollama（避免与 Comni 抢卡超时打冷却→锁死输入框）。
        try:
            self._sync_chat_router_duplex()
        except Exception:  # noqa: BLE001
            logger.debug("启动同步聊天路由 skip_local 失败", exc_info=True)
        # 【2026-10-08 启动配置恢复】用 companion_features 真相源重建引擎配置 + 应用双工模式 +
        # 暖机挂载本地模型（混合模式已挂载的本地双工模型启动后自动挂载）。幂等，异常均忽略。
        try:
            self.restore_saved_config_on_startup(reconcile=True)
        except Exception:  # noqa: BLE001
            logger.exception("启动配置恢复失败（忽略，沿用落盘值）")
        # 【2026-10-12·主动互动包】把独立开发的 proactive_pack 挂到宿主 proactive 实例上。
        # install() 幂等，且包装层对宿主零侵入：enabled=false（默认）时本包完全不动作，
        # 宿主原主动逻辑照常；仅当 data/proactive_pack.json 的 enabled=true 才参与开口。
        # 用懒导入避免与 proactive 引擎的循环依赖；一切异常均在 install 内部兜住。
        try:
            self._install_proactive_pack()
        except Exception:  # noqa: BLE001
            logger.debug("接入主动互动包失败（忽略，宿主保持原主动逻辑）", exc_info=True)

    def _install_proactive_pack(self) -> None:
        """把 proactive_pack 挂到 self._proactive 上（幂等）。仅供 set_runtime 启动钩子调用。"""
        pro = getattr(self, "_proactive", None)
        if pro is None:
            return
        try:
            from src.proactive_pack import install
        except Exception:  # noqa: BLE001
            logger.debug("导入 proactive_pack 失败（忽略）", exc_info=True)
            return
        try:
            install(pro)
        except Exception:  # noqa: BLE001
            logger.warning("install(proactive) 失败（宿主保持原状）", exc_info=True)

    # ======== 启动配置恢复（2026-10-08 修复·启动链路） ========
    # 根因：保存路径(save_companion_features / save_call_settings)会用 companion_features 真相源
    # 经 _features_to_engine_config 反推引擎配置并持久化、热切换双工；但**启动链路从未做这件事**——
    # 各模块只在自己 __init__ 时读 config 里零散的键，proactive 也仅直接读 duplex_mode 单键，
    # 一旦 config 漂移（历史写入只落 companion_features、duplex_mode 缺失/陈旧）就「设了重启没生效」。
    # 现象：混合模式下已挂载的本地双工模型启动后不自动挂载、档位/能力重启回默认。
    # 修复：在运行时注入完成(set_runtime)这个确定的启动钩子里，用真相源强制 reconcile 一次，
    # 再把双工模式应用到运行态(_sync_duplex_mode_from_cfg)并暖机挂载本地引擎(warm_local_model)。
    # 幂等：重复调用安全；模型已挂载/未就绪/非本地模式均直接跳过。
    def restore_saved_config_on_startup(self, reconcile: bool = True) -> None:
        """启动时用 companion_features 真相源重建引擎配置并应用到全部模块。

        在 set_runtime（proactive 已注入、self._cfg 为活动共享 cfg）末尾调用，
        保证配置层 + 运行态都用真相源，覆盖：
          · 档位/模式（companion_mode、proactive.duplex_mode、enabled）
          · 记忆 / 视觉 / 语音克隆 / 感知 等各模块开关
          · 本地双工引擎按保存配置自动挂载
        """
        if not self._cfg:
            return
        # ① 真相源 reconcile：companion_features → 引擎配置（修复漂移/缺失的 duplex_mode）
        if reconcile:
            try:
                raw = self._cfg.raw
                feats = raw.get("companion_features")
                if not isinstance(feats, list):
                    feats = self._derive_features_from_config(raw)
                if isinstance(feats, list) and feats:
                    # 【2026-10-16 修复·每次启动把用户亲手关掉的开关又打开】
                    # _features_to_engine_config 会**按档位预设写回**这三个"用户可见开关"：
                    #   proactive.enabled / vision_models.screen_watch_enabled / perception.audio.enabled
                    # 而它们正是用户在 UI 上亲手开/关的项（关掉 = 不要常驻抓屏 / 不要采集系统音频 /
                    # 不要主动搭话）。启动 reconcile 的本意是修"引擎配置漂移"（如 duplex_mode 缺失），
                    # **不应覆盖用户意图** —— 实测：引导里关掉后，重启软件三项又被置回 True
                    # （配置被写盘、用户看到的"关"失效，属于最容易被投诉的一类）。
                    # 故：先取快照，算完引擎配置后把这三点恢复回用户的落盘值。
                    _keep_caps = {
                        # 【2026-10-17 解耦】要保留的是"主动互动"（chat_initiative），
                        # 旧键 enabled 仅作兼容回退；运行时另由 runtime_on 保证为开。
                        "proactive_enabled": bool(((raw.get("proactive") or {}).get(
                            "chat_initiative", (raw.get("proactive") or {}).get("enabled", False)))),
                        "screen_watch": bool((raw.get("vision_models") or {}).get("screen_watch_enabled", False)),
                        "audio": bool(((raw.get("perception") or {}).get("audio") or {}).get("enabled", False)),
                    }
                    self._features_to_engine_config(feats, raw)
                    _pro2 = raw.setdefault("proactive", {})
                    _vm2 = raw.setdefault("vision_models", {})
                    _au2 = raw.setdefault("perception", {}).setdefault("audio", {})
                    # 【2026-10-17 解耦】只恢复"主动互动"；运行时恒为开（否则通话/看屏没宿主）。
                    _pro2["chat_initiative"] = _keep_caps["proactive_enabled"]
                    _pro2["enabled"] = _keep_caps["proactive_enabled"]
                    _pro2["runtime_on"] = True
                    _vm2["screen_watch_enabled"] = _keep_caps["screen_watch"]
                    _au2["enabled"] = _keep_caps["audio"]
                    logger.info("[启动恢复] 已保留用户开关：proactive.enabled=%s "
                                "screen_watch_enabled=%s perception.audio.enabled=%s",
                                _keep_caps["proactive_enabled"], _keep_caps["screen_watch"],
                                _keep_caps["audio"])
                    raw["companion_mode"] = self._mode_token_from_features(feats)
                    self._save_config()
                    try:
                        from ..config import _to_ns
                        self._cfg.ns = _to_ns(raw)
                    except Exception:  # noqa: BLE001
                        pass
                    logger.info("[启动恢复] 已用 companion_features 重建引擎配置(%s)",
                                [f.get("id") for f in feats])
            except Exception:  # noqa: BLE001
                logger.exception("[启动恢复] 配置 reconcile 失败（忽略，沿用落盘值）")
        # ② 仅当 proactive 已就绪：应用双工模式 + 暖机挂载本地引擎
        pro = getattr(self, "_proactive", None)
        if pro is not None:
            # 【2026-10-09 修复·拨号引擎启动即确定】pro._duplex_voice_engine /
            # _duplex_video_engine 只有 save_call_settings 会写，启动时恒为空 →
            # 重启后第一次拨号落到 webapp.start_duplex_call 的 else 兜底 local_omni，
            # 与 config 里用户设的引擎脱节（现象＝"重进要点一次档位才接通"）。
            # 这里在启动恢复阶段就用落盘值把这两个属性补齐，让"第一次拨号"即确定。
            try:
                _pr = (self._cfg.raw or {}).get("proactive") or {}
                _ve = str(_pr.get("duplex_voice_engine") or "").strip().lower()
                _de = str(_pr.get("duplex_video_engine") or "").strip().lower()
                if _ve:
                    pro._duplex_voice_engine = _ve
                if _de:
                    pro._duplex_video_engine = _de
                logger.info("[启动恢复] 拨号引擎已初始化 voice=%r video=%r",
                            getattr(pro, "_duplex_voice_engine", "") or "",
                            getattr(pro, "_duplex_video_engine", "") or "")
            except Exception:  # noqa: BLE001
                logger.debug("[启动恢复] 拨号引擎初始化失败（拨号时会再回落）", exc_info=True)
            try:
                pro._duplex_cfg_synced = False
                pro._sync_duplex_mode_from_cfg()
            except Exception:  # noqa: BLE001
                logger.debug("[启动恢复] 同步双工模式到运行态失败（忽略）", exc_info=True)
            # ③ 注入档位能力真值(set_tier)：使 capabilities() 走注入值而非"按双工模式回退推断"，
            # 避免拨号被自家人误拦(2026-09-10 的 4 连拦根因：游戏锁改 mode 后推断只剩 cloud_duplex)。
            try:
                _raw = self._cfg.raw
                _feats = _raw.get("companion_features")
                if not isinstance(_feats, list):
                    _feats = self._derive_features_from_config(_raw)
                if isinstance(_feats, list):
                    _fids = [f.get("id") for f in _feats if isinstance(f, dict) and f.get("id")]
                    if _fids:
                        pro.set_tier(token=str(_raw.get("companion_mode") or ""), feature_ids=_fids)
                        logger.info("[启动恢复] 已注入档位能力真值(%s)", _fids)
            except Exception:  # noqa: BLE001
                logger.debug("[启动恢复] 注入档位能力真值失败（忽略）", exc_info=True)
            # ④ 运行时应用 cloud_duplex 配置(等同设置保存路径)，保证看屏总闸等开关生效
            try:
                pro.apply_cloud_duplex_config({})
            except Exception:  # noqa: BLE001
                logger.debug("[启动恢复] 应用 cloud_duplex 配置失败（忽略）", exc_info=True)
            try:
                self.warm_local_model()
            except Exception:  # noqa: BLE001
                logger.exception("[启动恢复] 启动暖机本地模型失败（忽略）")

    def _use_shared_cfg(self, shared_cfg: Optional[Config]) -> None:
        """使用主程序共享的 Config 对象（避免读写两份）。"""
        if shared_cfg is not None:
            self._cfg = shared_cfg
            # 挂载运行时配置单例：model_catalog._comni_model_dir 等通过
            # config._CFG 读取 proactive 段；此前 config 模块从未定义 _CFG，
            # 导致该 import 抛 ImportError 被吞、模型目录永远解析不到（UI 恒
            # 显示「未就绪」+ 首启自动下载被跳过）。现显式挂载，根治该问题。
            try:
                from .. import config as _cfgmod
                _cfgmod._CFG = shared_cfg
            except Exception:  # noqa: BLE001
                pass
            logger.info("使用共享 Config")

    def _load_config(self) -> None:
        """加载配置文件。"""
        if self._cfg is None:
            self._cfg = load_config(self._config_path)
            try:
                from .. import config as _cfgmod
                _cfgmod._CFG = self._cfg
            except Exception:  # noqa: BLE001
                pass

    # ======== 工具：在线程间桥接 async 协程 ========
    def _run_coro(self, coro, timeout: float = 5.0) -> Any:
        """把协程投到主 asyncio 线程执行并等待结果。"""
        loop = self._loop
        if loop is None or not loop.is_running():
            # 无运行时：回退为仅修改本地配置，不触发事件
            return None
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout=timeout)
        except asyncio.TimeoutError:
            future.cancel()
            logger.warning("运行时桥接协程超时: %s", coro)
            return None
        except Exception as e:
            logger.exception("运行时桥接协程异常")
            return None

    # ======== 配置读取 ========
    def reload(self) -> dict:
        """重新加载配置。"""
        try:
            self._load_config()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_characters(self) -> dict:
        """获取所有角色列表。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}

        chars = []
        for char in self._cfg.list_characters():
            chars.append({
                "id": char.id,
                "name": getattr(char, "name", "未命名"),
                "character": getattr(char, "character", ""),
                "speaking_style": getattr(char, "speaking_style", ""),
                "background": getattr(char, "background", ""),
                "voice": {
                    "engine": getattr(char.voice, "engine", ""),
                    "voice": getattr(char.voice, "voice", ""),
                    "rate": getattr(char.voice, "rate", "+0%"),
                    "pitch": getattr(char.voice, "pitch", "+0Hz"),
                    "volume": getattr(char.voice, "volume", "+0%"),
                    "fallback_pyttsx3": getattr(char.voice, "fallback_pyttsx3", False),
                    "api_url": getattr(char.voice, "api_url", "http://127.0.0.1:9880"),
                    "reference_audio": getattr(char.voice, "reference_audio", ""),
                    "reference_text": getattr(char.voice, "reference_text", ""),
                    "language": getattr(char.voice, "language", "zh"),
                    # MiniMax 专属字段：漏掉会导致前端试听拿不到自定义 voice_id/api_key，
                    # 详情面板试听报"未配置 API Key"，模态框试听退回默认 female-shaonv
                    "api_key": getattr(char.voice, "api_key", ""),
                    "group_id": getattr(char.voice, "group_id", ""),
                    "voice_id": getattr(char.voice, "voice_id", "female-shaonv"),
                    "model": getattr(char.voice, "model", "speech-02-turbo"),
                    "speed": getattr(char.voice, "speed", 1.0),
                },
                "model": {
                    "vision_model": getattr(char.model, "vision_model", None),
                    "fallback_model": getattr(char.model, "fallback_model", None),
                },
                "model_3d": {
                    "enabled": getattr(char.model_3d, "enabled", False),
                    "format": getattr(char.model_3d, "format", None),
                    "path": getattr(char.model_3d, "path", None),
                    "thumbnail": getattr(char.model_3d, "thumbnail", None),
                    "description": getattr(char.model_3d, "description", None),
                },
                "is_active": char.id == self._cfg.active_character_id,
                "voice_profile": getattr(char, "voice_profile", "") or "",
                "cloud_voice_id": getattr(char, "cloud_voice_id", "") or "",
                "ref_audio": getattr(char, "ref_audio", "") or "",
                # 【2026-10-11】banner/avatar 此前漏返回 ⇒ 角色卡头图/头像"存了也读不到"
                #   （前端 c.banner / c.avatar 恒为 undefined → 图不显示）。
                #   角色配置里已有这两个字段（save_character 写入），这里必须一并透出。
                "banner": getattr(char, "banner", "") or "",
                "avatar": getattr(char, "avatar", "") or "",
            })

        # 【2026-10-02 新增】active_name：真实激活角色名。此前只有 active_id，前端要显示
        # "当前是哪个角色"（通话页名字/头像/角色卡等）只能写死"达妮娅"，切角色后不跟随。
        # 运行时以 dialogue 为真值（可能已热切换），否则回退配置里的角色名。
        _active_id = self._cfg.active_character_id
        _active_name = ""
        try:
            if self._dialogue is not None:
                _active_name = self._dialogue.current_character_name or ""
        except Exception:  # noqa: BLE001
            _active_name = ""
        if not _active_name:
            for _c in chars:
                if _c.get("id") == _active_id:
                    _active_name = _c.get("name") or ""
                    break
        return {
            "success": True,
            "characters": chars,
            "active_id": _active_id,
            "active_name": _active_name or "达妮娅",
        }

    # ---- 好感度/关系 控制面（P4/P5 供前端右栏调用）----
    def _rel_char_id(self, char_id: str = "") -> str:
        cid = (char_id or "").strip()
        if not cid and self._cfg is not None:
            cid = str(getattr(self._cfg, "active_character_id", "") or "")
        return cid or "default"

    def get_relationship_state(self, char_id: str = "") -> dict:
        """右栏 UI 拉取关系状态（心情/阶段/原型/设置/上次互动间隔），全程不暴露数字。"""
        try:
            import time as _t
            from ..companion.relationship import get_relationship_manager, last_relation_event
            cid = self._rel_char_id(char_id)
            mgr = get_relationship_manager()
            st = mgr.get(cid)
            mood = st.get("mood") or {}
            settings = st.get("settings") or {}
            now = _t.time()
            last_in = float(st.get("last_interaction_ts") or 0.0)
            last_p = float(st.get("last_proactive_ts") or 0.0)

            def _ago(ts: float) -> str:
                if not ts:
                    return "从未"
                g = (now - ts) / 3600.0
                if g < 1:
                    return f"{int(g * 60)} 分钟前"
                if g < 24:
                    return f"{int(g)} 小时前"
                return f"{int(g / 24)} 天前"

            def _event_str(ev) -> str:
                if not ev:
                    return ""
                g = (now - float(ev.get("ts") or 0.0)) / 3600.0
                when = f"{int(g * 60)} 分钟前" if g < 1 else (f"{int(g)} 小时前" if g < 24 else f"{int(g / 24)} 天前")
                return f"[{ev.get('kind')}] {ev.get('detail')} · {when}"

            # 【2026-10-11·P1 仪式感】里程碑（首次跨阶段）——让 UI 能展示"关系质变时刻"，
            #   不暴露数字：只给"抵达了什么阶段 + 一句有分量的话"。
            _ms_list = []
            try:
                for m in (st.get("milestones") or []):
                    if isinstance(m, dict) and m.get("kind"):
                        _ms_list.append({
                            "kind": m.get("kind"),
                            "stage": m.get("stage") or "",
                            "ts": float(m.get("ts") or 0.0),
                            "when": _ago(float(m.get("ts") or 0.0)),
                            "line": mgr.milestone_line(st, m),
                        })
            except Exception:  # noqa: BLE001
                _ms_list = []

            return {
                "success": True,
                "char_id": cid,
                "mood": mood.get("label", "平静"),
                "mood_intensity": int(mood.get("intensity", 1)),
                "stage": st.get("stage", "萍水"),
                "progress": mgr.relationship_progress_label(st),
                "archetype": st.get("archetype") or "温柔",
                "archetype_inferred": bool(st.get("archetype_inferred")),
                "settings": dict(settings),
                "last_interaction": _ago(last_in),
                "last_proactive": _ago(last_p),
                "pending_response": bool(st.get("pending_response")),
                "last_event": _event_str(last_relation_event()),
                "milestones": _ms_list,   # 【2026-10-11·P1】关系质变时刻（一次性、不暴露数字）
            }
        except Exception as e:  # noqa: BLE001
            logger.exception("get_relationship_state 失败")
            return {"success": False, "error": str(e)}

    def set_relationship_setting(self, char_id: str, key: str, value) -> dict:
        try:
            from ..companion.relationship import get_relationship_manager
            cid = self._rel_char_id(char_id)
            mgr = get_relationship_manager()
            mgr.set_setting(cid, key, value)
            return {"success": True, "settings": dict(mgr.get(cid).get("settings") or {})}
        except Exception as e:  # noqa: BLE001
            logger.exception("set_relationship_setting 失败")
            return {"success": False, "error": str(e)}

    def reset_relationship(self, char_id: str = "") -> dict:
        try:
            from ..companion.relationship import get_relationship_manager
            cid = self._rel_char_id(char_id)
            get_relationship_manager().reset_relationship(cid)
            return {"success": True, "char_id": cid}
        except Exception as e:  # noqa: BLE001
            logger.exception("reset_relationship 失败")
            return {"success": False, "error": str(e)}

    def set_archetype_manual(self, char_id: str, archetype: str) -> dict:
        try:
            from ..companion.relationship import get_relationship_manager, ARCHETYPES
            cid = self._rel_char_id(char_id)
            if archetype not in ARCHETYPES:
                return {"success": False, "error": f"未知原型：{archetype}"}
            get_relationship_manager().set_archetype(cid, archetype, inferred=False)
            return {"success": True, "archetype": archetype}
        except Exception as e:  # noqa: BLE001
            logger.exception("set_archetype_manual 失败")
            return {"success": False, "error": str(e)}

    def get_relation_events(self, char_id: str = "", limit: int = 50) -> dict:
        """P6 观测日志：返回最近若干条关系/主动事件（旧→新），供 UI/控制台查看。"""
        try:
            from ..companion.relationship import get_relation_events
            events = get_relation_events(int(limit) if limit else 50)
            return {"success": True, "events": events}
        except Exception as e:  # noqa: BLE001
            logger.exception("get_relation_events 失败")
            return {"success": False, "error": str(e)}

    def get_runtime_stats(self) -> dict:
        """获取运行时状态（给 UI 显示用）。"""
        from ..event_bus import EventType
        if self._dialogue is not None:
            return {
                "success": True,
                "has_runtime": True,
                "active_id": self._dialogue.current_character_id,
                "active_name": self._dialogue.current_character_name,
                "ocr_calibration_open": self._calib_open(),
                "chat_overlay_open": self._overlay_open(),
            }
        return {"success": True, "has_runtime": False}

    def get_vision_debug(self) -> dict:
        """获取视觉感知调试快照（供「感知调试台」轮询，1s 一次）。

        汇总 ScreenCapture / VisionAnalyzer / ProactiveStrategy 三者的运行时状态，
        全部为线程安全的只读快照。
        """
        result: dict[str, Any] = {"success": True, "ts": time.time()}

        cap = getattr(self, "_capture", None)
        if cap is not None:
            try:
                result["capture"] = cap.snapshot()
            except Exception as e:
                logger.exception("capture snapshot 失败")
                result["capture_error"] = str(e)
            # 视觉分析器通过 capture 内部引用获取（避免改动 main.py 注入面）
            ana = getattr(cap, "_analyzer", None)
            if ana is not None:
                try:
                    result["analyzer"] = ana.snapshot()
                except Exception as e:
                    logger.exception("analyzer snapshot 失败")
                    result["analyzer_error"] = str(e)

        pro = getattr(self, "_proactive", None)
        if pro is not None:
            try:
                result["proactive"] = pro.snapshot()
            except Exception as e:
                logger.exception("proactive snapshot 失败")
                result["proactive_error"] = str(e)
            try:
                result["game_lock"] = pro.get_game_lock_diagnostic()
            except Exception as e:  # noqa: BLE001
                logger.exception("game_lock diagnostic 失败")
                result["game_lock_error"] = str(e)

        mon = getattr(self, "_monitor", None)
        if mon is not None:
            try:
                result["foreground"] = mon.snapshot()
            except Exception as e:
                logger.exception("foreground snapshot 失败")
                result["foreground_error"] = str(e)

        ocr = getattr(self, "_ocr", None)
        if ocr is not None:
            try:
                result["ocr"] = ocr.snapshot()
            except Exception as e:
                logger.exception("ocr snapshot 失败")
                result["ocr_error"] = str(e)

        audio = getattr(self, "_audio", None)
        if audio is not None:
            try:
                result["audio"] = audio.snapshot()
            except Exception as e:
                logger.exception("audio snapshot 失败")
                result["audio_error"] = str(e)

        # OCR 校准悬浮窗开关状态（供调试台开关同步）
        result["ocr_calibration_open"] = self._calib_open()
        result["chat_overlay_open"] = self._overlay_open()
        result["privacy_mode"] = privacy_mode()
        # 双工输出对话日志（调试台「双工输出」板块，最近 100 条；存的是 {"ts","text"}）
        result["duplex_output"] = list(self._duplex_output_log)[-100:]

        return result

    def get_perception_state(self) -> dict:
        """桌面感知的「一句话状态」，供桌宠页/主窗口的指示器轮询（2–3s 一次）。

        【2026-10-20 立】为什么需要它：屏幕采集每 2s 抓一帧全屏，但**桌面上没有任何
        可见迹象**。竞品（Copilot Vision / 豆包 / Kimi）都是显式共享 + 可见指示，
        而达妮娅是常驻静默采集——用户只能靠"信任"，没法靠"看见"。

        为什么是**页面主动轮询**而不是 Python 推送：
        `main_window.py:454` 有实测结论——GUI 线程内同步调 `evaluate_js` 会
        「自己锁死自己」（等 WebView2 回执，而回执只能由被堵住的消息泵派发），
        跨线程调同样不可靠。轮询是唯一不会踩这条的形态。

        极轻：只读 ScreenCapture 的内存快照，不碰 WebView2、不做 IO、不建线程。
        任何一环拿不到就降级成 watching=False（宁可少亮一个点，不能让页面卡）。
        """
        out: dict[str, Any] = {
            "success": True,
            "ts": time.time(),
            "watching": False,      # 看屏总闸是否开着
            "privacy": False,       # 隐私模式（全停）
            "interval_s": 0.0,      # 采样间隔，供说明"多久看一眼"
            "last_seen_ts": 0.0,    # 最近一次真正抓帧的时刻
            "skip_last": "",        # 最近因哪个敏感窗口跳过（如「网银」）
            "skip_count": 0,
        }
        try:
            out["privacy"] = bool(privacy_mode())
        except Exception:  # noqa: BLE001
            pass
        cap = getattr(self, "_capture", None)
        if cap is None:
            return out
        try:
            # enabled 是实时属性（每次求值读 runtime_status.vision_enabled），
            # 不能用 snapshot 里那个构造期快照——设置页刚关掉开关时两者会不一致。
            out["watching"] = bool(cap.enabled)
            snap = cap.snapshot()
            out["interval_s"] = float(snap.get("interval_s") or 0.0)
            out["last_seen_ts"] = float(snap.get("last_ts") or 0.0)
            out["skip_last"] = str(snap.get("skip_last") or "")
            out["skip_count"] = int(snap.get("skip_count") or 0)
        except Exception:  # noqa: BLE001
            logger.debug("感知状态快照失败（指示器降级为不亮）", exc_info=True)
        return out

    # ======== 记忆性能观测仪（D 步，2026-10-01） ========
    def get_memory_profile(self) -> dict:
        """记忆性能观测仪快照（供开发者页「记忆性能观测仪」面板轮询）。

        聚合各档位（cloud / local / local_duplex）的 token 中位数 / P95 延迟 /
        召回命中率 / 各模型对比；并附带长期记忆库的运行统计（总量/来源/角色）。
        全静默：任一部分失败都降级返回。
        """
        result: dict[str, Any] = {"success": True, "ts": time.time()}
        # 1) 观测仪聚合（埋点来自 assemble / extract / llm 调用）
        try:
            from ..llm.memory_profiler import get_profile_summary
            result["profile"] = get_profile_summary()
        except Exception as e:  # noqa: BLE001
            logger.exception("记忆观测仪聚合失败")
            result["profile_error"] = str(e)
            result["profile"] = {"total_probes": 0, "by_tier": {}, "by_model": {}, "events": {}}
        # 2) 长期记忆库运行统计（C 步 memory_store.stats）
        try:
            from ..companion.memory_store import get_memory_store
            store = get_memory_store()
            result["store_stats"] = store.stats() if store is not None else {}
        except Exception as e:  # noqa: BLE001
            logger.exception("记忆库统计失败")
            result["store_stats_error"] = str(e)
        return result

    # ======== 隐私模式（暂停/恢复屏幕与音频感知） ========
    def get_privacy_mode(self) -> dict:
        """当前隐私模式状态（供调试台按钮同步）。"""
        return {"success": True, "on": privacy_mode()}

    def toggle_privacy_mode(self) -> dict:
        """切换隐私模式：暂停/恢复屏幕与音频感知。

        暂停后截图/OCR/游戏音效/云端双工推流全部跳过——模型无法获取新的画面
        和声音；恢复后自动继续。同时同步给云端双工客户端（set_privacy）。
        """
        on = set_privacy_mode(not privacy_mode()) or privacy_mode()
        # 同步给云端真全双工推流客户端（暂停/恢复画面与声音推流）
        pro = getattr(self, "_proactive", None)
        if pro is not None:
            try:
                pro.apply_privacy(on)
            except Exception:  # noqa: BLE001
                logger.exception("同步隐私模式到双工推流失败")
        # 悬浮层即时反馈（用户正在游戏里双击切换时能看到状态）
        try:
            holder = getattr(self, "_overlay", None)
            ov = holder.get("ov") if isinstance(holder, dict) else None
            if ov is not None:
                ov.push("ai", "隐私模式已开启，我不再看屏幕听声音了" if on
                        else "隐私模式已关闭，我继续陪着你啦")
        except Exception:  # noqa: BLE001
            pass
        logger.info("隐私模式已%s", "开启（感知暂停）" if on else "关闭（感知恢复）")
        return {"success": True, "on": on}

    # ======== Unity 动画测试（逐个播放全部动画，供效果预览） ========
    def test_animation(self, action: str) -> dict:
        """前端"测试动画"按钮：向 Unity 广播一条动作指令。

        复用 AI_DECISION 事件让 ws_server 广播给 Unity（Unity 端 HandleDecision
        收到 action 后播放对应动画）；text 传空字符串，TTS/聊天采集/活动记录
        都会跳过空文本，不会触发朗读或产生聊天记录。
        """
        action = (action or "idle").strip().lower()
        if self._bus is None:
            return {"success": False, "error": "运行时未注入"}
        if self._loop is None or not self._loop.is_running():
            return {"success": False, "error": "主事件循环未运行"}

        async def _publish() -> None:
            await self._bus.publish(Event(EventType.AI_DECISION, {
                "emotion": "neutral",
                "action": action,
                "text": "",
                "source": "unity_anim_test",
            }, "webapp"))

        self._run_coro(_publish())
        return {"success": True, "action": action}

    # ======== VRC 衣服/玩具直控（前端按钮 → Unity） ========
    def set_clothing(self, item: str, on: bool = True) -> dict:
        """切换 VRC 模型衣服/配饰/玩具开关。

        item 是 VRCFeatureController 里映射的物品名（如"裙子""武器""墨镜"），
        通过事件总线 → ws_server → WebSocket → Unity SetActive 直控。
        """
        item = (item or "").strip()
        if not item:
            return {"success": False, "error": "物品名不能为空"}
        if self._bus is None:
            return {"success": False, "error": "运行时未注入"}
        if self._loop is None or not self._loop.is_running():
            return {"success": False, "error": "主事件循环未运行"}

        async def _publish() -> None:
            await self._bus.publish(Event(EventType.VRC_CLOTHING, {
                "item": item,
                "on": bool(on),
                "source": "webui",
            }, "webapp"))

        self._run_coro(_publish())
        logger.info("发送 VRC 衣服指令: %s on=%s", item, on)
        return {"success": True, "item": item, "on": bool(on)}

    # ======== VRC 表情直控（前端按钮 → Unity，FaceEmo 预设 blendshape） ========
    def set_emotion(self, name: str, on: bool = True) -> dict:
        """切换 VRC 模型表情（FaceEmo 预设表情，一次只显示一个）。

        name 是 VRCFeatureController 里 emotions 表的键（如"生气表情""哭哭"），
        通过事件总线 → ws_server → WebSocket → Unity 应用 blendshape 组合。
        """
        name = (name or "").strip()
        if not name:
            return {"success": False, "error": "表情名不能为空"}
        if self._bus is None:
            return {"success": False, "error": "运行时未注入"}
        if self._loop is None or not self._loop.is_running():
            return {"success": False, "error": "主事件循环未运行"}

        async def _publish() -> None:
            await self._bus.publish(Event(EventType.VRC_EMOTION, {
                "name": name,
                "on": bool(on),
                "source": "webui",
            }, "webapp"))

        self._run_coro(_publish())
        logger.info("发送 VRC 表情指令: %s on=%s", name, on)
        return {"success": True, "name": name, "on": bool(on)}

    # ======== 游戏音效参考音录制（击杀/阵亡/胜负） ========
    # 【2026-10-21 删】get_audio_status() —— 它返回 audio.reference_status()，而
    # `get_vision_debug()["audio"]` 里已经有 `"references": reference_status()`
    # （`game_audio.snapshot()` 带的）。调试台只轮询 get_vision_debug，这个接口零调用。
    # 同一份数据两个出口，删掉多余的那个；要加新字段就加在 snapshot() 里。

    def record_audio_reference(self, event_type: str, seconds: float = 2.5) -> dict:
        """录制一次游戏音效参考音：游戏里触发对应音效时调用，录当前系统输出音频。

        建议时长：击杀确认音等短音效录 1.5s 即可，胜利/失败音乐可录 3s。
        录制期间不要播放其他声音（含本软件 TTS），否则会录进噪音。
        """
        audio = getattr(self, "_audio", None)
        if audio is None:
            return {"success": False, "error": "音频识别未注入（请用主程序启动）"}
        if event_type not in ("kill", "headshot", "died", "victory", "defeat"):
            return {"success": False, "error": f"不支持的事件类型: {event_type}"}
        try:
            info = audio.record_reference(event_type, seconds=seconds)
            logger.info("录制参考音完成: %s", info)
            return {"success": True, "info": info}
        except Exception as e:
            logger.exception("录制参考音失败")
            return {"success": False, "error": str(e)}

    def record_voice_reference(self, seconds: float = 2.5) -> dict:
        """录一小段**麦克风**音频当音色克隆的参考音，返回 wav 绝对路径。

        设置页「录音」按钮用它（另一个入口是「选择音频文件」）。

        【2026-10-21 修】这个接口原来**不存在**：设置页调的是
        `Backend.recordAudio('reference', 2.5)` → `record_audio_reference('reference')`
        —— 而上面那个方法录的是**系统输出**（游戏音效识别用），事件类型白名单里没有
        `'reference'`，于是按钮每一次都返回「不支持的事件类型: reference」。
        两个方向相反的设备共用一个名字，是这个 bug 的根。

        时长窗口 `[1.5, 30]` 见 `src/audio/voice_ref_record.py` 的说明。
        """
        from ..audio.voice_ref_record import record_wav

        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        stamp = time.strftime("%Y%m%d_%H%M%S")
        out_dir = os.path.join(base, "data", "voice_ref")
        # 取第一个不存在的名字：序号保证同一进程内不撞，存在性检查兜住重启后同秒撞车
        while True:
            path = os.path.join(out_dir, "ref_%s_%03d.wav" % (stamp, next(_REF_AUDIO_SEQ)))
            if not os.path.exists(path):
                break
        r = record_wav(path, seconds)
        if not r.get("success"):
            logger.warning("录制音色参考音失败: %s", r.get("error"))
            return {"success": False, "error": r.get("error") or "录音失败"}
        logger.info("录制音色参考音完成: %s（%.1fs, %s）",
                    r.get("path"), r.get("seconds") or 0, r.get("device") or "?")
        return {"success": True, "path": r.get("path") or path,
                "seconds": r.get("seconds"), "device": r.get("device") or ""}

    # ======== OCR 区域校准（悬浮窗拖动调整识别区域） ========
    def _ocr_calib_state(self) -> dict:
        """供校准悬浮窗轮询的状态（模式/参照矩形/当前区域/默认区域）。"""
        ocr = self._ocr
        if ocr is None:
            return {"mode": "none", "reference": None, "regions": {}, "defaults": {}}
        try:
            return ocr.calibration_state()
        except Exception as e:
            logger.exception("获取 OCR 校准状态失败")
            return {"mode": "none", "reference": None, "regions": {},
                    "defaults": {}, "error": str(e)}

    def _ocr_calib_save(self, regions: dict) -> None:
        """校准悬浮窗保存：热应用到 GameOcrMonitor + 写入 config.yaml。"""
        self._ocr_apply_regions(regions)

    def _ocr_apply_regions(self, regions: dict) -> dict:
        """热应用识别区域到 GameOcrMonitor + 写入 config.yaml（统一保存路径）。

        区域格式 {name: {purpose, rect}}（或旧格式 {name: [x,y,w,h]}），
        保存的是规范化后的全量区域（含用途），供校准悬浮窗/调试台共用。

        注意：校准悬浮窗可能只传它当前在调的那几个区域，直接整体替换会把
        其他区域（顶部HUD/底部HUD等）从配置里抹掉（实测：保存页面名字后
        默认三区全没了）。所以这里**合并**而非替换——只更新传入的区域，
        未传的保持不变；删除区域走 ocr_remove_region 显式移除。
        """
        if self._ocr is None:
            return {"success": False, "error": "OCR 未注入"}
        try:
            # 合并到当前全量区域（保留未传入的区域），再整体应用
            current = self._ocr.regions or {}
            merged: dict[str, dict] = dict(current)
            for k, v in (regions or {}).items():
                if isinstance(v, dict) and "rect" in v:
                    merged[k] = {
                        "purpose": str(v.get("purpose", "general")),
                        "rect": list(v.get("rect") or [0.0, 0.0, 1.0, 1.0]),
                    }
                elif isinstance(v, (list, tuple)) and len(v) == 4:
                    merged[k] = {"purpose": "general", "rect": list(v)}
                else:
                    # 简写 {name: purpose}：保留现有 rect，只更新用途
                    old = current.get(k, {})
                    merged[k] = {
                        "purpose": str(v),
                        "rect": list(old.get("rect") or [0.0, 0.0, 1.0, 1.0]),
                    }
            self._ocr.set_regions(merged)
        except Exception as e:
            logger.warning("OCR 区域热更新失败: %s", e)
            return {"success": False, "error": str(e)}
        try:
            raw = self._cfg.raw
            raw.setdefault("perception", {})
            ocr_raw = raw["perception"].setdefault("ocr", {})
            ocr_raw["regions"] = self._ocr.regions  # 规范化后的当前全量区域
            self._save_config()
            logger.info("OCR 识别区域已保存到配置")
        except Exception as e:
            logger.warning("OCR 区域配置写入失败（已热生效，重启后会还原）: %s", e)
        return {"success": True}

    # ======== OCR 识别区域管理（WebUI 调试台：新增/删除/改用途） ========
    # 【2026-10-21 删】ocr_get_regions() —— 返回的是 `ocr.snapshot()` 里的
    # regions / region_texts / player_name，全是 `get_vision_debug()["ocr"]` 的子集。
    # 调试台按 1s 轮询 get_vision_debug，这个接口零调用。

    def ocr_set_player_name(self, name: str = "") -> dict:
        """设置游戏玩家名：热生效到 GameOcrMonitor + 写入 config.yaml。

        玩家名用于击杀播报「击杀者A 枪械图标 被杀者B」的纯 OCR 归属判断：
        左侧匹配玩家名 → 击杀（kill），右侧匹配 → 阵亡（died）。
        """
        if self._ocr is None:
            return {"success": False, "error": "OCR 未注入"}
        try:
            self._ocr.set_player_name(name or "")
            return {"success": True, "player_name": self._ocr.player_name}
        except Exception as e:
            logger.exception("设置玩家名失败")
            return {"success": False, "error": str(e)}

    def ocr_add_region(self, name: str, purpose: str = "general") -> dict:
        """新增一个识别区域（默认全屏，拖动校准后保存生效）。"""
        ocr = self._ocr
        if ocr is None:
            return {"success": False, "error": "OCR 未注入"}
        name = (name or "").strip()
        if not name:
            return {"success": False, "error": "区域名不能为空"}
        if not ocr.add_region(name, purpose):
            return {"success": False, "error": f"区域已存在或名字非法: {name}"}
        return self._ocr_apply_regions(ocr.regions)

    def ocr_remove_region(self, name: str) -> dict:
        """删除一个识别区域。"""
        ocr = self._ocr
        if ocr is None:
            return {"success": False, "error": "OCR 未注入"}
        if not ocr.remove_region(name):
            return {"success": False, "error": f"区域不存在: {name}"}
        return self._ocr_apply_regions(ocr.regions)

    def ocr_set_region_purpose(self, name: str, purpose: str) -> dict:
        """修改一个识别区域的用途（kill/page/settle/hud/general）。

        用途决定该区域文字用于什么判断：kill=击杀播报引信、page=页面名识别、
        settle=结算横幅、hud=顶部HUD（回合/倒计时）、general=并入通用引信。
        """
        ocr = self._ocr
        if ocr is None:
            return {"success": False, "error": "OCR 未注入"}
        if not ocr.set_region_purpose(name, purpose):
            return {"success": False, "error": f"区域不存在或用途非法: {name}/{purpose}"}
        return self._ocr_apply_regions(ocr.regions)

    def ocr_set_region_rect(self, name: str, rect: list) -> dict:
        """修改一个识别区域的位置/大小（rect 为相对坐标 [x,y,w,h]，0~1）。

        保留该区域原有用途，只更新矩形；合并写入 config.yaml（不抹掉其他区域）。
        供开发者页 WebUI 分块画布拖动 / 表格数值输入精确调优。
        """
        ocr = self._ocr
        if ocr is None:
            return {"success": False, "error": "OCR 未注入"}
        cur = ocr.regions.get(name)
        if not cur:
            return {"success": False, "error": f"区域不存在: {name}"}
        try:
            rect = [max(0.0, min(1.0, float(x))) for x in (rect or [])[:4]]
            while len(rect) < 4:
                rect.append(0.0)
        except (TypeError, ValueError):
            return {"success": False, "error": "rect 非法"}
        purpose = cur.get("purpose", "general")
        return self._ocr_apply_regions({name: {"purpose": purpose, "rect": rect}})

    # ======== 文字锚点训练器（区域文字锚点规则管理） ========
    # 【2026-10-21 删】ocr_get_anchors() —— 返回 anchors / region_texts / anchor_state /
    # regions，同样全是 `get_vision_debug()["ocr"]`（即 ocr.snapshot()）的子集。
    # 写侧（ocr_set_anchors）保留：它是有真实效果的配置入口。

    def ocr_set_anchors(self, anchors: list) -> dict:
        """保存全部文字锚点规则：热生效到 GameOcrMonitor + 写入 config.yaml。

        anchors 为 {region, keywords, state, label} 列表（keywords 是关键词数组）。
        """
        if self._ocr is None:
            return {"success": False, "error": "OCR 未注入"}
        if not isinstance(anchors, list):
            return {"success": False, "error": "anchors 必须是列表"}
        try:
            self._ocr.set_anchors(anchors)
        except Exception as e:
            logger.warning("文字锚点热更新失败: %s", e)
            return {"success": False, "error": str(e)}
        try:
            raw = self._cfg.raw
            raw.setdefault("perception", {})
            ocr_raw = raw["perception"].setdefault("ocr", {})
            ocr_raw["anchors"] = self._ocr.anchors  # 规范化后的全量锚点规则
            self._save_config()
            logger.info("文字锚点规则已保存到配置: %d 条", len(self._ocr.anchors))
        except Exception as e:
            logger.warning("文字锚点配置写入失败（已热生效，重启后会还原）: %s", e)
        return {"success": True, "anchors": self._ocr.anchors}

    def start_ocr_calibration(self) -> dict:
        """打开「OCR 区域校准」悬浮窗：把击杀/阵亡识别区域画到真实屏幕上拖动调整。

        悬浮窗在独立线程运行（tkinter 透明置顶窗口），不阻塞主程序；
        关闭方式：悬浮窗内按 Esc，或 UI 开关关掉（stop_ocr_calibration）。
        保存会立即生效并写入 config.yaml。
        """
        if self._ocr is None or self._monitor is None:
            return {"success": False, "error": "OCR/前台监听未注入（请用主程序启动）"}
        if self._calib_thread is not None and self._calib_thread.is_alive():
            return {"success": False, "error": "校准悬浮窗已打开（请先关闭开关）"}
        try:
            import tkinter  # noqa: F401
        except Exception:
            return {"success": False, "error": "此版本未包含 tkinter（内置运行时为无 tcl/tk 的精简版），OCR 校准窗口不可用"}
        try:
            from .ocr_calibrator import run_calibrator
        except Exception as e:
            return {"success": False, "error": f"加载校准器失败: {e}"}

        self._calib_stop = threading.Event()

        # 校准悬浮窗是置顶透明窗，盖在游戏上时其 UI 文字会被 mss 抓进 OCR →
        # 锚点误命中"在主页"等假状态。打开校准先暂停 OCR 扫描。
        try:
            self._ocr.set_calibrating(True)
        except Exception:  # noqa: BLE001
            logger.debug("暂停 OCR 扫描失败（旧版无此接口）", exc_info=True)

        def _worker() -> None:
            try:
                run_calibrator(get_state=self._ocr_calib_state,
                               on_save=self._ocr_calib_save,
                               stop_event=self._calib_stop)
            except Exception:
                logger.exception("OCR 区域校准悬浮窗异常退出")
            finally:
                # 关闭校准：重建 mss 抓屏实例（旧实例可能残留悬浮窗像素）+
                # 清空锚点状态，恢复实时扫描
                try:
                    self._ocr.set_calibrating(False)
                except Exception:  # noqa: BLE001
                    logger.debug("恢复 OCR 扫描失败（旧版无此接口）", exc_info=True)
                self._calib_stop = None
                self._calib_thread = None

        self._calib_thread = threading.Thread(
            target=_worker, daemon=True, name="ocr_calibrator")
        self._calib_thread.start()
        logger.info("已打开 OCR 区域校准悬浮窗")
        return {"success": True}

    def stop_ocr_calibration(self) -> dict:
        """关闭「OCR 区域校准」悬浮窗（等效于在悬浮窗里按 Esc）。

        供 OCR 设置里的开关关掉悬浮窗；即使悬浮窗已自行关闭也返回成功。
        """
        stop = getattr(self, "_calib_stop", None)
        if stop is not None:
            stop.set()
        thread = getattr(self, "_calib_thread", None)
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        logger.info("OCR 区域校准悬浮窗已关闭")
        return {"success": True}

    def _calib_open(self) -> bool:
        """校准悬浮窗是否正在显示（供 UI 开关同步状态）。"""
        thread = getattr(self, "_calib_thread", None)
        return thread is not None and thread.is_alive()

    # ======== 对话悬浮层（游戏内透明文本框：显示 AI 的话 + 语音文字） ========
    def _overlay_cfg(self) -> dict:
        oc = (self._cfg.raw.get("overlay_chat") or {}) if self._cfg else {}
        return oc if isinstance(oc, dict) else {}

    def _overlay_state(self) -> dict:
        oc = self._overlay_cfg()
        return {
            "position": oc.get("position") or None,
            "max_items": int(oc.get("max_items") or 3),
            "ttl": float(oc.get("ttl") or 8.0),
            "font_size": int(oc.get("font_size") or 16),
        }

    def start_chat_overlay(self) -> dict:
        """打开「对话悬浮层」：游戏画面上透明显示 AI 说的话 + 你的语音文字。

        悬浮窗在独立线程运行（tkinter 透明置顶窗口），不阻塞主程序；
        关闭方式：悬浮窗内按 Esc，或 UI 开关关掉（stop_chat_overlay）。
        拖动即移动位置，松开自动保存到 config.yaml。
        """
        if self._overlay_thread is not None and self._overlay_thread.is_alive():
            return {"success": False, "error": "对话悬浮层已打开（请先关闭开关）"}
        try:
            import tkinter  # noqa: F401
        except Exception:
            return {"success": False, "error": "此版本未包含 tkinter（内置运行时为无 tcl/tk 的精简版），对话悬浮层不可用"}
        try:
            from .chat_overlay import run_chat_overlay
        except Exception as e:
            return {"success": False, "error": f"加载悬浮层失败: {e}"}

        self._overlay_stop = threading.Event()
        char_name = "达妮娅"
        if self._dialogue is not None:
            try:
                char_name = self._dialogue.current_character_name or "达妮娅"
            except Exception:  # noqa: BLE001
                pass
        holder: dict = {"ov": None}

        def _ready(ov: Any) -> None:
            holder["ov"] = ov

        def _worker() -> None:
            try:
                run_chat_overlay(get_state=self._overlay_state,
                                 on_save=self._overlay_save_pos,
                                 stop_event=self._overlay_stop,
                                 char_name=char_name,
                                 on_ready=_ready,
                                 get_capacity=self.get_duplex_capacity)
            except Exception:
                logger.exception("对话悬浮层异常退出")
            finally:
                self._overlay_stop = None
                self._overlay_thread = None
                holder["ov"] = None

        self._overlay_thread = threading.Thread(
            target=_worker, daemon=True, name="chat_overlay")
        self._overlay_thread.start()
        self._overlay = holder
        # 【浮窗联动修复（2026-09-02）】start_chat_overlay 是用户显式开浮窗的入口，
        # 在此确保聊天采集订阅已建立（含 AI_DECISION → 浮窗推送）。幂等，重复开不影响。
        try:
            self.start_chat_capture()
        except Exception:  # noqa: BLE001
            logger.exception("启动聊天采集（浮窗联动）失败")
        try:
            self._overlay_cfg()["enabled"] = True
            self._save_config()
        except Exception:  # noqa: BLE001
            logger.debug("保存悬浮层 enabled 失败", exc_info=True)
        logger.info("已打开对话悬浮层")
        return {"success": True}

    def stop_chat_overlay(self) -> dict:
        """关闭「对话悬浮层」（等效于在悬浮窗里按 Esc）。"""
        stop = getattr(self, "_overlay_stop", None)
        if stop is not None:
            stop.set()
        thread = getattr(self, "_overlay_thread", None)
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        self._overlay = None
        try:
            self._overlay_cfg()["enabled"] = False
            self._save_config()
        except Exception:  # noqa: BLE001
            logger.debug("保存悬浮层 enabled 失败", exc_info=True)
        logger.info("对话悬浮层已关闭")
        return {"success": True}

    # 【2026-10-21 删】chat_overlay_state() —— "悬浮层开着没"由
    # `get_vision_debug()["chat_overlay_open"]` 给（调试台已在用）；`enabled` 是内部
    # 配置（`_overlay_cfg()` 自己读写，start/stop_chat_overlay 维护），界面上没有第二个
    # 消费者。同一状态两个出口，删多余的那个。

    def _overlay_open(self) -> bool:
        thread = getattr(self, "_overlay_thread", None)
        return thread is not None and thread.is_alive()

    def _overlay_save_pos(self, pos: list) -> None:
        """拖动结束保存悬浮层位置（相对主屏坐标）。"""
        oc = self._overlay_cfg()
        oc["position"] = [int(pos[0]), int(pos[1])]
        self._save_config()

    def _overlay_push(self, kind: str, text: str) -> None:
        """把 AI 的话 / 用户语音文字推给悬浮层（线程安全，未开时忽略）。"""
        holder = getattr(self, "_overlay", None)
        ov = holder.get("ov") if isinstance(holder, dict) else None
        if ov is None:
            return
        try:
            ov.push(kind, text)
        except Exception:  # noqa: BLE001
            logger.debug("对话悬浮层推送失败", exc_info=True)

    # ======== 聊天 Tab：消息采集 + 查询 + 发送 ========
    def start_chat_capture(self) -> dict:
        """订阅聊天相关事件，把用户消息 / AI 回复 / 主动互动记录进缓冲。

        顺带订阅本地 TTS 服务加载状态事件（切换角色时做"加载中/加载完成"UI 提醒）。

        【浮窗联动修复（2026-09-02）】本方法此前定义了却从未被调用，导致
        _on_chat_ai_decision 从未订阅 AI_DECISION，对话悬浮层打开后永远空白（只显示
        底部容量栏）。改为幂等同步方法（体内无 await），并由 start_chat_overlay 在开
        浮窗时调用，确保 AI 双工开口能真实推入浮窗。event_bus.subscribe 非幂等，故用
        _chat_capture_started 标志防止重复订阅（重复订阅会让同一句话推两次）。
        """
        if getattr(self, "_chat_capture_started", False):
            return {"success": True}
        if self._bus is None or self._loop is None:
            return {"success": False, "error": "运行时未注入"}

        # 同步订阅（bus.subscribe 本身是同步方法）：启动早期主线程调用时，
        # asyncio loop 可能尚未 run_forever，走 _run_coro 会丢弃协程导致订阅不
        # 生效（实锤 RuntimeWarning: coroutine never awaited）——同步调用无此竞态。
        self._bus.subscribe(EventType.USER_TEXT, self._on_chat_user_text)
        self._bus.subscribe(EventType.AI_DECISION, self._on_chat_ai_decision)
        self._bus.subscribe(EventType.PARTIAL_TEXT, self._on_chat_partial)
        # 【Agent 执行状态气泡（2026-10-02）】聊天中调工具时显示"正在执行：xxx"，
        # 完成后被真实 AI 回复替换——治"执行操作毫无提示、用户以为死了"。
        self._bus.subscribe(EventType.AGENT_STATUS, self._on_agent_status)
        # 【2026-10-02 新增】通话记录（与聊天流隔离）：双方文本 + 达妮娅语音存档。
        # 此前通话内容走 AI_DECISION 混进聊天页，且用户的话被按"达妮娅"渲染。
        self._bus.subscribe(EventType.CALL_SPEECH, self._on_call_speech)
        self._bus.subscribe(EventType.UNITY_INBOUND, self._on_chat_unity_inbound)
        self._bus.subscribe(EventType.TTS_LOADING, self._on_tts_load_status)
        self._bus.subscribe(EventType.TTS_LOADED, self._on_tts_load_status)
        self._bus.subscribe(EventType.TTS_LOAD_ERROR, self._on_tts_load_status)
        self._bus.subscribe(EventType.TTS_SAVED, self._on_tts_saved)
        # 【陪你看视频·进度条（2026-10-01）】后端看视频各阶段进度，实时推前端进度条
        self._bus.subscribe(EventType.VIDEO_WATCH_PROGRESS, self._on_video_watch_progress)
        # 【锁模式弹窗（2026-08-25）】proactive 发布 STATE_CHANGE(code=DUPLEX_MODE_LOCK)
        # 时，把切换提示弹到前端 toast + 桌宠气泡 + 聊天区（用户能明确看到切换）
        self._bus.subscribe(EventType.STATE_CHANGE, self._on_duplex_mode_lock)
        self._chat_capture_started = True
        logger.info("聊天采集已启动")
        return {"success": True}

    async def _on_duplex_mode_lock(self, e: Event) -> None:
        """双工锁模式切换提示：前端 toast + 桌宠气泡 + 聊天区系统消息。

        【游戏锁静默（2026-09-01）】reason==game_lock/game_unlock 的切换由游戏锁
        后台自动触发，不打扰用户——跳过前端 toast 与桌宠气泡（proactive 侧也已不弹
        屏幕浮窗），切换仍照常发生（释放/恢复显存）。
        """
        d = e.data or {}
        if d.get("code") != "DUPLEX_MODE_LOCK":
            return
        if d.get("silent"):
            logger.info("游戏锁切换静默（不弹前端通知）：%s", d.get("message"))
            return
        msg = (d.get("message") or "").strip()
        if not msg:
            return
        # 兜底亲民化：万一有旧调用点仍传内部术语（快照/双工/提示词），这里统一翻成人话，
        # 保证 toast 与桌宠气泡里绝不出现工程词（浮窗侧已在 show_mode_notify 内翻过）。
        try:
            from .mode_notify import humanize
            msg = humanize(msg) or msg
        except Exception:  # noqa: BLE001
            pass
        # 前缀用 💫 而非 ⚙️：这句是达妮娅第一人称在跟你说话，不是系统在播报设置变更
        text = f"💫 {msg}"
        # 前端 toast（主窗口右上角弹出，最直观）
        try:
            self._toast_if_any(msg, "success")
        except Exception:  # noqa: BLE001
            logger.debug("锁模式 toast 失败", exc_info=True)
        # 桌宠悬浮气泡（打游戏时桌面可见）
        try:
            self._overlay_push("ai", text[:200])
        except Exception:  # noqa: BLE001
            logger.debug("锁模式气泡推送失败", exc_info=True)
        # 注：不再写入聊天区系统消息（2026-08-25 用户明确——模式切换提醒只要
        # ACE 式屏幕浮窗（_switch_duplex_mode 直接弹）+ 这里 toast/气泡，不要
        # 发到软件聊天页面，避免和对话记录混在一起）。

    async def _on_tts_load_status(self, e: Event) -> None:
        """本地 TTS 服务加载状态 → 前端 toast 提醒（加载中 / 加载完成 / 加载失败）。"""
        d = e.data or {}
        label = (d.get("name") or d.get("engine") or "").strip()
        if e.type == EventType.TTS_LOADING:
            self._toast_if_any(f"正在加载「{label}」的音色模型…", "loading")
        elif e.type == EventType.TTS_LOADED:
            self._toast_if_any(f"「{label}」的音色模型加载完成", "success")
        elif e.type == EventType.TTS_LOAD_ERROR:
            self._toast_if_any(f"「{label}」的音色模型加载失败：{d.get('error', '未知错误')}", "error")

    async def _on_video_watch_progress(self, e: Event) -> None:
        """陪你看视频阶段进度 → 前端进度条（独立广播，不进聊天消息历史）。"""
        d = e.data or {}
        # 下载百分比会高频到达，用 debug 避免刷屏；阶段切换与结束用 info 便于追踪
        stage, pct, done = d.get("stage"), d.get("pct"), bool(d.get("done"))
        if done:
            logger.info("[视频进度] 结束 stage=%s msg=%s", stage, d.get("message") or "")
        else:
            logger.debug("[视频进度] stage=%s pct=%s", stage, pct)
        _wv2_broadcast({
            "type": "video_watch",
            "stage": stage,
            "message": d.get("message") or "",
            "pct": pct,
            "done": done,
            "kind": d.get("kind"),
            "title": d.get("title"),
            "url": d.get("url"),
        })
        # 【2026-10-04】进度同步进聊天流状态卡片：挂在左侧达妮娅头像下、与 agent
        # 执行卡片同一套体系（用户反馈"应根据左侧 AI 发言变化"）。合并进当前进行中
        # 的状态卡；没有就在缓冲里新建一条。真实回复到达时随 _drop_chat_status 移除。
        # ws 广播保留：无聊天流的页面（通话/开发者）仍用悬浮气泡展示。
        try:
            watch = {
                "kind": d.get("kind") or "video",
                "title": d.get("title"),
                "stage": stage,
                "pct": pct,
                "message": d.get("message") or "",
                "done": done,
                "error": bool(done and stage == "error"),
            }
            meta = None
            if self._chat_status_id is not None:
                for m in self._chat_buffer:
                    if m.get("id") == self._chat_status_id:
                        meta = m.get("agent_meta")
                        if meta is None:
                            meta = {"plan": None, "steps": [], "done": False,
                                    "error": False, "phase": "watch"}
                            m["agent_meta"] = meta
                        # 进度还在到达 ⇒ 卡片仍活跃：撤销待删定时器
                        #（否则上一条回复排下的延迟删除会把观看进度中途掐掉）
                        if self._chat_status_drop_timer is not None:
                            try:
                                self._chat_status_drop_timer.cancel()
                            except Exception:  # noqa: BLE001
                                pass
                            self._chat_status_drop_timer = None
                        break
            if meta is None:
                # 撤销旧定时器 + 重置 born_ts（否则最小存活期/日志"存活"按旧卡算）
                self._revive_status_card()
                self._chat_status_id = self._next_chat_id()
                meta = {"plan": None, "steps": [], "done": False,
                        "error": False, "phase": "watch"}
                # 【2026-10-05 可观测】"看视频没动效"排查：建卡/挂卡留痕
                logger.info("[观看卡] 新建观看进度卡 id=%s stage=%s kind=%s",
                            self._chat_status_id, stage, watch.get("kind"))
                self._chat_buffer.append({
                    "id": self._chat_status_id,
                    "role": "ai",
                    "text": "👀 " + (watch.get("message") or "达妮娅正在看…"),
                    "char_name": self._dialogue.current_character_name if self._dialogue else "AI",
                    "emotion": "neutral", "action": "idle",
                    "is_proactive": False, "trigger": "", "failed": False,
                    "kind": "status", "agent_meta": meta,
                    "blocked": False, "block_reason": "", "ts": time.time(),
                })
            meta["watch"] = watch
            if watch["error"]:
                meta["error"] = True
            # ⭐⭐⭐【2026-10-05 修"抖音卡永远停在执行中"】观看**完成**时必须把
            #   meta.done 置 True —— 之前只在 error 分支置 meta["error"]，done 分支
            #   **完全没写 meta["done"]** ⇒ 前端 agent 卡永远显示"执行中"，
            #   即使后端 04:15:14 已发 stage=done（用户 04:17 截图实锤）。
            #   同时把步骤补进 meta.steps（resolve→download→frames→done），
            #   让卡片有可勾选的时间线，与设计稿一致。
            if done:
                meta["done"] = True
                if not watch.get("error"):
                    meta["phase"] = "watch_done"
                # 【2026-10-08 单进度条】观看阶段只走 meta["watch"]（前端 .ac-watch
                # 组件已独立渲染阶段步进器）；不再把 watch:* 追加进 meta["steps"]，
                # 否则 .ac-steps 时间线会重复显示观看进度，与设计稿单一进度表达不符。
            # 卡片正文随进度更新（前端 agent 卡按 meta 渲染，这行文本是降级/无 JS 时的兜底）
            for _m in self._chat_buffer:
                if _m.get("id") == self._chat_status_id:
                    _m["text"] = "👀 " + (watch.get("message") or "达妮娅正在看…")
                    _m["ts"] = time.time()
                    break
            logger.info("[观看卡] 更新 id=%s stage=%s pct=%s done=%s steps=%d",
                        self._chat_status_id, stage, pct, done, len(meta.get("steps") or []))
            _wv2_broadcast({"type": "chat_push"})
        except Exception:  # noqa: BLE001
            logger.debug("[视频进度] 写入聊天状态卡失败", exc_info=True)

    def _on_vision_quota_alert(self, info: dict) -> None:
        """视觉模型免费额度耗尽 —— **不再主动广播弹窗**（2026-10-02 用户决策）。

        【为什么停】额度耗尽是**持续状态**，不是一次性事件：只要没充值/没关
        「仅免费额度」，它就一直为真。而失败会发生在桌面看屏、陪你看视频、聊天
        发图、视觉分析等多条链路，各有各的重试与冷却 → 主动弹窗必然反复打扰，
        且弹的那一刻用户不知道"这对我有什么影响"，只能含糊说成"其它候选也暂
        不可用"（实测同一 Key 下文本模型全部可用，文字聊天根本不受影响）。

        【改成什么】额度信息由 VisionRouter 挂在**失败消息**里
        （`_explain_all_cooling` / `_explain_all_failed`，明确说"文字聊天不受
        影响"），只在用户**真的用到视觉且失败**那一刻随那条消息给出。

        本方法保留：若将来要做"用户主动点『检测额度』"，复用此广播通道。
        """
        try:
            d = info or {}
            logger.info("[额度状态] 视觉模型 %s 额度耗尽（不主动弹窗，随失败消息提示）",
                        d.get("model_id"))
        except Exception:  # noqa: BLE001
            logger.debug("额度状态记录失败（忽略）", exc_info=True)

    def get_video_watch_diag(self) -> dict:
        """开发者页「陪你看视频」诊断卡：依赖可用性 + 开关 + 最近观看记录。

        用于排查"为什么看不了视频"——最常见原因是运行环境没装 yt-dlp / imageio-ffmpeg，
        或 desktop.media.watch_videos 开关被关，或 VLM(视觉)不可用。
        """
        try:
            from ..channels.video_watch import get_diagnostics
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"无法加载视频模块：{e}"}
        try:
            diag = get_diagnostics()
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}
        # 开关：desktop.media.watch_videos（默认开）
        enabled = True
        try:
            desktop = getattr(self._cfg.ns, "desktop", None) if self._cfg else None
            media = getattr(desktop, "media", None) if desktop is not None else None
            if media is not None:
                enabled = bool(getattr(media, "watch_videos", True))
        except Exception:  # noqa: BLE001
            pass
        # VLM（视觉）是否可用：决定"能不能真的看画面"
        # 【2026-10-02 修】原实现查 self._llm/_text_llm（那是 TextChatRouter，**纯文本**
        # 路由，压根没有 vision_available）→ 永远拿不到真实视觉状态。视频链路真正用的
        # 是 VisionRouter（set_runtime 时注入到 _vision_router，dialogue 也持有）。
        # 故按"视觉路由 → dialogue 持有的路由"顺序取，并兼容旧属性名。
        vision = None
        try:
            cands = [getattr(self, "_vision_router", None)]
            _dlg = getattr(self, "_dialogue", None)
            if _dlg is not None:
                cands.append(getattr(_dlg, "_vision_router", None))
            for vr in cands:
                if vr is None:
                    continue
                va = getattr(vr, "vision_available", None)
                if callable(va):
                    vision = bool(va())
                    break
                if isinstance(va, bool):
                    vision = va
                    break
        except Exception:  # noqa: BLE001
            logger.debug("视频诊断: 读取视觉路由状态失败", exc_info=True)
        diag.update({"success": True, "enabled": enabled, "vision": vision})
        logger.info("[视频诊断] yt_dlp=%s ffmpeg=%s vision=%s 开关=%s 记录数=%d",
                    diag.get("yt_dlp"), ("有" if diag.get("ffmpeg") else "缺失"),
                    vision, enabled, len(diag.get("runs") or []))
        return diag

    def test_video_link(self, data: dict = None) -> dict:
        """开发者页「陪你看视频」实测：输入链接跑完整链路，分阶段报告卡在哪。

        【2026-10-02 新增】原先只能看"依赖是否装好"这种静态状态，但用户真正想知道的是
        **卡在下载（yt-dlp）还是抽帧（ffmpeg）还是看画面（VLM）**。故这里真跑一次：
          阶段1 解析+下载（yt-dlp）→ 阶段2 抽帧（ffmpeg）→ 阶段3 VLM 看画面
        每阶段独立回报 ok/fail/耗时/错误，最后给出"卡在第几阶段"的一句话结论。

        ⚠️ **必须是同步方法**（不是 async）：本项目 CharacterAPI 里所有公开 js_api 都是
        同步的，pywebview 侧对 async 返回值不会 await —— 唯独用 async def 时前端收到的是
        未执行的 coroutine 对象，报「Object of type coroutine is not JSON serializable」
        （用户实测截图实锤）。故这里改为：同步壳 + 后台线程跑真异步逻辑 + 阻塞等结果。
        """
        d = data or {}
        url = str(d.get("url") or "").strip()
        if not url:
            return {"success": False, "error": "请填写视频链接"}

        # 在后台线程跑 asyncio 主循环（video_watch/download_video 都是 async）。
        # 不能用 asyncio.run 之外的方式：本进程已有一个运行中的 loop（GUI 的事件循环），
        # 所以必须起独立线程 + 自己的新 loop，绝不能嵌套进正在跑的 loop。
        import threading as _th
        _box: dict = {}

        def _worker():
            import asyncio as _aio
            try:
                _box["r"] = _aio.run(self._test_video_link_async(url))
            except Exception as e:  # noqa: BLE001
                _box["r"] = {"success": False,
                             "error": f"实测异常：{type(e).__name__}: {e}"}

        _t = _th.Thread(target=_worker, name="video-link-test", daemon=True)
        _t.start()
        # 最长 5 分钟（下载大视频可能慢）；超时按失败返回，不无限期挂住前端
        _t.join(timeout=300)
        if _t.is_alive():
            return {"success": False,
                    "error": "实测超时（>300s）。若视频很大可换一个短链接，或先用命令行验证 yt-dlp。",
                    "stages": [], "conclusion": "卡在下载阶段（超时未完成）。"}
        return _box.get("r") or {"success": False, "error": "实测未返回结果"}

    async def _test_video_link_async(self, url: str) -> dict:
        """开发者页「陪你看视频」实测：输入链接跑完整链路，分阶段报告卡在哪。

        【2026-10-02 新增】原先只能看"依赖是否装好"这种静态状态，但用户真正想知道的是
        **卡在下载（yt-dlp）还是抽帧（ffmpeg）还是看画面（VLM）**。故这里真跑一次：
          阶段1 解析+下载（yt-dlp）→ 阶段2 抽帧（ffmpeg）→ 阶段3 VLM 看画面
        每阶段独立回报 ok/fail/耗时/错误，最后给出"卡在第几阶段"的一句话结论。

        只做诊断，不改任何配置；产物落在临时目录并在返回前清理。
        """
        url = str(url or "").strip()
        if not url:
            return {"success": False, "error": "请填写视频链接"}
        t_all = time.time()
        stages: list = []
        result: dict = {"success": False, "url": url, "stages": stages}

        # 阶段 0：URL 识别
        try:
            from ..channels.video_watch import is_video_url
            if not is_video_url(url):
                return {"success": False, "error": "这个链接不是受支持的视频平台"
                        "（支持 B站/抖音/YouTube 等）"}
        except Exception:  # noqa: BLE001
            pass

        # 阶段 0.5：抖音已由 video_watch 自研免登录解析器处理（匿名 Cookie 直取无水印直链），
        # 不再因缺少 cookies.txt 而快速失败；解析/下载失败会在「阶段1」如实上报。

        # 阶段 1：下载（抖音走自研解析器；其余走 yt-dlp）
        t0 = time.time()
        workdir = ""
        vpath = ""
        try:
            from ..channels.video_watch import download_video, _resolve_ffmpeg
            import tempfile
            workdir = tempfile.mkdtemp(prefix="vidtest_")
            try:
                vpath, meta = await download_video(url, workdir, _resolve_ffmpeg())
                ok1 = bool(vpath and os.path.isfile(vpath))
                if ok1:
                    size_mb = os.path.getsize(vpath) / 1048576.0
                    title = str((meta or {}).get("title") or "")[:60]
                    info1 = f"{size_mb:.1f}MB" + (f" · {title}" if title else "")
                else:
                    info1 = "下载未产出文件"
                stages.append({"stage": "1下载", "ok": ok1,
                               "elapsed": round(time.time() - t0, 1), "info": info1})
            finally:
                if workdir:
                    import shutil as _sh
                    _sh.rmtree(workdir, ignore_errors=True)
                    workdir = ""
        except Exception as e:  # noqa: BLE001
            from ..channels.video_watch import _humanize_dl_error
            _msg = _humanize_dl_error(url, e)
            stages.append({"stage": "1下载", "ok": False,
                           "elapsed": round(time.time() - t0, 1), "info": _msg})
            # 【2026-10-02 修】补 error 字段：前端失败分支只读 r.error，
            # 缺它时会把已算好的分阶段结论显示成"未知错误"。
            result["error"] = _msg
            result["conclusion"] = ("卡在【第1阶段·下载】。\n" + _msg
                                    + "\n   小提示：先用 B站/YouTube 链接测一下——"
                                      "如果它们能通，说明 yt-dlp 本身没问题，是该平台的限制。")
            result["elapsed"] = round(time.time() - t_all, 1)
            logger.info("[视频实测] 下载阶段失败 url=%s err=%s", url, e)
            return result

        # 阶段 2：抽帧（ffmpeg）
        t0 = time.time()
        frames: list = []
        try:
            from ..channels.video_watch import sample_frames, _resolve_ffmpeg
            frames = await sample_frames(vpath, 4, _resolve_ffmpeg())
            n = len(frames or [])
            stages.append({"stage": "2抽帧(ffmpeg)", "ok": n > 0,
                           "elapsed": round(time.time() - t0, 1),
                           "info": (f"抽出 {n} 帧（共 {sum(len(f) for f in frames)} 字节）"
                                    if n else "未能抽出任何帧（ffmpeg 缺失或视频损坏）")})
        except Exception as e:  # noqa: BLE001
            stages.append({"stage": "2抽帧(ffmpeg)", "ok": False,
                           "elapsed": round(time.time() - t0, 1),
                           "info": f"{type(e).__name__}: {e}"})

        # 阶段 3：VLM 看画面（用抽到的第一帧真调一次视觉模型）
        t0 = time.time()
        try:
            vr = getattr(self, "_vision_router", None)
            if vr is None:
                stages.append({"stage": "3看画面(VLM)", "ok": False,
                               "elapsed": round(time.time() - t0, 1),
                               "info": "视觉路由未注入（vision_router 不可用）"})
            elif not frames:
                stages.append({"stage": "3看画面(VLM)", "ok": False,
                               "elapsed": round(time.time() - t0, 1),
                               "info": "没有可用帧（上一阶段失败）"})
            else:
                try:
                    if hasattr(vr, "vision_describe"):
                        desc = await asyncio.wait_for(
                            vr.vision_describe(list(frames[:3])), timeout=90)
                    elif hasattr(vr, "vision_chat"):
                        desc = await asyncio.wait_for(
                            vr.vision_chat(list(frames[:3]), "这张图里有什么？"), timeout=90)
                    else:
                        desc = ""
                except Exception as e:  # noqa: BLE001
                    desc = ""
                    stages.append({"stage": "3看画面(VLM)", "ok": False,
                                   "elapsed": round(time.time() - t0, 1),
                                   "info": f"{type(e).__name__}: {str(e)[:180]}"})
                else:
                    ok3 = bool(str(desc or "").strip())
                    stages.append({"stage": "3看画面(VLM)", "ok": ok3,
                                   "elapsed": round(time.time() - t0, 1),
                                   "info": (str(desc)[:200] if ok3 else "视觉模型返回空")})
        except Exception as e:  # noqa: BLE001
            stages.append({"stage": "3看画面(VLM)", "ok": False,
                           "elapsed": round(time.time() - t0, 1),
                           "info": f"{type(e).__name__}: {e}"})

        # 结论：第一个失败阶段即卡点
        first_bad = next((s for s in stages if not s.get("ok")), None)
        if first_bad:
            result["conclusion"] = ("卡在【" + first_bad["stage"] + "】：" + str(first_bad.get("info") or "")
                                    + "。前面阶段正常，问题就在这一环。")
        else:
            result["conclusion"] = "全链路通过：下载→抽帧→VLM 看画面都正常，达妮娅能真正看懂这个视频。"
        result["success"] = True
        result["elapsed"] = round(time.time() - t_all, 1)
        logger.info("[视频实测] url=%s 结论=%s", url, result.get("conclusion"))
        return result

    # ==================== 表情包管理（2026-10-13 接入） ====================
    # 薄转发到 sticker_bridge（技能包在独立目录，不污染本文件）。
    # 前端约定：Backend.stickerPacks() / Backend.stickerMemes(pid) / …
    # 所有方法**同步**（js_api 必须 sync），返回可 JSON 序列化的 dict。
    def _sticker(self):
        try:
            from .sticker_bridge import get_sticker_bridge
            b = get_sticker_bridge()
            # 首次访问时把主程序当前视觉模型注入（让打标跟随设置页，而不是技能包自带 key）
            if not getattr(self, "_sticker_injected", False):
                try:
                    self._inject_sticker_vision(b)
                    self._sticker_injected = True
                except Exception:  # noqa: BLE001
                    pass
            return b
        except Exception as e:  # noqa: BLE001
            logger.warning("[表情包] 桥接不可用：%s", e)
            return None

    def _inject_sticker_vision(self, bridge) -> None:
        """把主程序「当前启用的视觉模型」接给表情包打标。

        好处：换模型/欠费在设置页一处生效，表情包不必自己维护一份 key。
        走 VisionRouter（与「看屏幕」同一条链路，行为一致）。
        """
        vr = getattr(self, "_vision_router", None)
        if vr is None:
            return
        label = ""
        try:
            snap = vr.snapshot() or {}
            for m in snap.get("models", []) or []:
                if m.get("id") == snap.get("active"):
                    label = m.get("name") or m.get("model") or ""
                    break
        except Exception:  # noqa: BLE001
            pass

        def _call(data: bytes, _label: str = "") -> dict:
            """用主程序视觉模型给一张表情图打标（同步壳，内部起协程）。"""
            from ..llm.vision_router import VisionRouter  # noqa: F401  仅确保模块可用
            # 复用技能包提示词与抽帧，只把「谁来答」换成主程序模型
            import base64 as _b64
            try:
                from backend.llm import _prepare_image  # type: ignore
            except Exception:  # noqa: BLE001
                _prepare_image = None
            try:
                prepared, mime = ( _prepare_image(data, "image/jpeg",
                                                  frames_strategy="motion", frames_k=3)
                                   if _prepare_image else (data, "image/jpeg") )
            except Exception:  # noqa: BLE001
                prepared, mime = data, "image/jpeg"
            try:
                from backend.autotag import SYS_AUTOTAG, EMOTION_VOCAB, _clean  # type: ignore
            except Exception:  # noqa: BLE001
                return {"error": "技能包不可用"}
            import json as _json
            data_url = "data:" + mime + ";base64," + _b64.b64encode(prepared).decode()
            prompt = SYS_AUTOTAG.format(vocab=" ".join(EMOTION_VOCAB))
            try:
                # VisionRouter 的实际调用面：优先 describe/ask，取第一个可用的
                out = None
                for meth in ("describe_image_data_url", "describe_data_url",
                             "ask_image_data_url", "describe"):
                    fn = getattr(vr, meth, None)
                    if callable(fn):
                        try:
                            out = fn(data_url, prompt)
                        except TypeError:
                            out = fn(data_url, system=prompt)
                        break
                if out is None:
                    return {"error": "视觉路由无可用的图片调用方法"}
                if hasattr(out, "__await__"):
                    out = self._run_coro(out, timeout=90)
                txt = out if isinstance(out, str) else str(
                    (out or {}).get("text") or (out or {}).get("content") or out)
                from backend.llm import _extract_json  # type: ignore
                r = _extract_json(txt)
                if not isinstance(r, dict):
                    return {"error": "视觉模型输出无法解析"}
                emos = r.get("emotions") or []
                if not isinstance(emos, list):
                    emos = [str(emos)]
                return {
                    "description": _clean(r.get("description")),
                    "tags": [str(x).strip() for x in emos if str(x).strip()][:10],
                    "trigger": _clean(r.get("trigger")),
                    "mood_compound": _clean(r.get("mood_compound")),
                    "anti_trigger": _clean(r.get("anti_trigger")),
                    "visible_text": _clean(r.get("visible_text")),
                    "model": _label or "主程序视觉模型",
                    "ts": time.time(), "frames": "motion",
                }
            except Exception as e:  # noqa: BLE001
                return {"error": f"{type(e).__name__}: {e}"[:160]}

        bridge.set_engine(_call, label)

    def stickerSelfcheck(self) -> dict:
        b = self._sticker()
        if b is None:
            return {"ok": False, "items": [{"name": "桥接", "ok": False, "detail": "导入失败"}]}
        return b.selfcheck()

    def stickerPacks(self) -> dict:
        b = self._sticker()
        return b.list_packs() if b else {"ok": False, "error": "桥接不可用", "packs": []}

    def stickerCreatePack(self, name: str = "") -> dict:
        b = self._sticker()
        return b.create_pack(name) if b else {"ok": False, "error": "桥接不可用"}

    def stickerRenamePack(self, pid: str = "", name: str = "") -> dict:
        b = self._sticker()
        return b.rename_pack(pid, name) if b else {"ok": False, "error": "桥接不可用"}

    def stickerDeletePack(self, pid: str = "") -> dict:
        b = self._sticker()
        return b.delete_pack(pid) if b else {"ok": False, "error": "桥接不可用"}

    def stickerBindings(self, pid: str = "", mode: str = "none", characters: list = None) -> dict:
        b = self._sticker()
        return b.set_bindings(pid, mode, characters or []) if b else {"ok": False, "error": "桥接不可用"}

    def stickerMemes(self, pid: str = "") -> dict:
        b = self._sticker()
        return b.list_memes(pid) if b else {"ok": False, "error": "桥接不可用", "memes": []}

    def stickerImageUrl(self, pid: str = "", mid: str = "") -> dict:
        b = self._sticker()
        return b.meme_image_url(pid, mid) if b else {"ok": False, "error": "桥接不可用"}

    def stickerDeleteMeme(self, pid: str = "", mid: str = "") -> dict:
        b = self._sticker()
        return b.delete_meme(pid, mid) if b else {"ok": False, "error": "桥接不可用"}

    def stickerVocab(self) -> dict:
        b = self._sticker()
        return b.emotion_vocab() if b else {"ok": False, "vocab": []}

    def stickerUpdateLabels(self, pid: str = "", mid: str = "", fields: dict = None) -> dict:
        b = self._sticker()
        return b.update_labels(pid, mid, fields or {}) if b else {"ok": False, "error": "桥接不可用"}

    def stickerAutotagOne(self, pid: str = "", mid: str = "", force: bool = False) -> dict:
        b = self._sticker()
        return b.autotag_one(pid, mid, force) if b else {"ok": False, "error": "桥接不可用"}

    def stickerAutotagPack(self, pid: str = "", limit: int = 30, only_missing: bool = True) -> dict:
        b = self._sticker()
        if b is None:
            return {"ok": False, "error": "桥接不可用"}
        # 进度写进实例字段，供 stickerAutotagProgress 轮询（同步方法不能流式返回）
        prog = []
        self._sticker_autotag_progress = prog
        res = b.autotag_pack(pid, limit, only_missing, progress=prog)
        self._sticker_autotag_progress = None
        return res

    def stickerAutotagProgress(self) -> dict:
        """整包标注的实时进度（前端轮询）。返回 {running, done, total, ok, fail, current}。"""
        prog = getattr(self, "_sticker_autotag_progress", None)
        if not prog:
            return {"running": False, "done": 0, "total": 0, "ok": 0, "fail": 0, "current": ""}
        return dict(prog[0])

    def stickerCharacters(self) -> dict:
        """角色名册（含头像）—— 供「绑定角色」渲染小型角色卡。"""
        b = self._sticker()
        return b.characters() if b else {"ok": False, "error": "桥接不可用", "characters": []}

    def stickerMarketplace(self) -> dict:
        b = self._sticker()
        return b.marketplace() if b else {"ok": False, "error": "桥接不可用", "entries": []}

    def stickerInstall(self, entry: dict = None) -> dict:
        b = self._sticker()
        return b.marketplace_install(entry or {}) if b else {"ok": False, "error": "桥接不可用"}

    def stickerInstallStart(self, entry: dict = None, limit: int = 120) -> dict:
        """异步启动安装，立即返回 task_id（不阻塞 UI）；进度用 stickerInstallProgress 轮询。"""
        b = self._sticker()
        return b.install_start(entry or {}, int(limit)) if b else {"ok": False, "error": "桥接不可用"}

    def stickerInstallProgress(self, task_id: str = "") -> dict:
        """前端轮询安装真实进度。"""
        b = self._sticker()
        return b.install_progress(task_id or "") if b else {"ok": False, "error": "桥接不可用"}

    def stickerInstallCancel(self, task_id: str = "", keep: bool = True) -> dict:
        """暂停（keep=True，保留断点）或取消（keep=False，丢弃部分包）安装任务。"""
        b = self._sticker()
        return b.install_cancel(task_id or "", keep is not False) if b else {"ok": False, "error": "桥接不可用"}

    def stickerInstallContinue(self, task_id: str = "") -> dict:
        """从断点继续下载暂停的安装任务。"""
        b = self._sticker()
        return b.install_continue(task_id or "") if b else {"ok": False, "error": "桥接不可用"}

    def stickerPick(self, character: str = "", conversation: list = None) -> dict:
        b = self._sticker()
        return b.pick(character, conversation or []) if b else {"ok": False, "error": "桥接不可用"}

    def stickerUpload(self, pid: str = "", filename: str = "", data_b64: str = "") -> dict:
        """上传一张表情（前端把 File 读成 base64 传过来），入库后自动打标。"""
        b = self._sticker()
        if b is None:
            return {"ok": False, "error": "桥接不可用"}
        import base64 as _b64
        try:
            raw = _b64.b64decode(str(data_b64 or "").split(",")[-1])
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"base64 解码失败：{e}"}
        if not raw:
            return {"ok": False, "error": "空文件"}
        try:
            with b._lock:
                meme = b._store.add_meme(pid, raw, filename or "sticker.gif")
                res = b._run_autotag(raw)
                meta = b._store._read_meta(pid)
                entry = meta.get(meme["id"])
                if entry is not None:
                    b._autotag.apply_autotag_to_meta(entry, res)
                    b._store._write_meta(pid, meta)
                    meme = entry
            return {"ok": True, "meme": meme}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}


    # ======== 聊天拖入本地视频 → 陪你看（2026-10-13） ========
    def watch_local_video(self, data: dict = None) -> dict:
        """聊天输入框拖入本地视频文件 → 走「陪你看」同款链路。

        与抖音/B站链接同源：阶段进度经 VIDEO_WATCH_PROGRESS 实时上屏（同一张
        video_watch UI 卡），抽帧后 VLM 看画面，再把画面描述并入一条 USER_TEXT
        （source='webui'）——她像看完一个链接一样自然接话，不需要前端再拼提示词。

        data: { b64: dataURL 或纯 base64, name: 文件名 }
        同步壳 + 后台线程（同 test_video_link 的坑位结论：js_api 必须 sync），
        本方法立即返回，观看在后台跑，进度卡片由事件总线推给前端。
        """
        import base64 as _b64
        d = data or {}
        raw = str(d.get("b64") or "")
        name = str(d.get("name") or "video.mp4").strip() or "video.mp4"
        if not raw:
            return {"success": False, "error": "没有收到视频数据"}
        if "," in raw and raw[:5] == "data:":
            raw = raw.split(",", 1)[1]
        try:
            blob = _b64.b64decode(raw, validate=False)
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"视频数据解码失败：{e}"}
        if len(blob) < 1024:
            return {"success": False, "error": "视频文件太小或为空"}
        if len(blob) > 150 * 1024 * 1024:
            return {"success": False, "error": "视频超过 150MB，先剪一段再拖进来"}
        safe = "".join(c for c in name if c not in '\\/:*?"<>|').strip() or "video.mp4"
        watch_dir = os.path.join("data", "tmp_watch")
        try:
            os.makedirs(watch_dir, exist_ok=True)
            path = os.path.join(watch_dir, f"{int(time.time() * 1000)}_{safe}")
            with open(path, "wb") as f:
                f.write(blob)
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"视频落盘失败：{e}"}

        import threading as _th
        _th.Thread(target=self._watch_local_video_worker, args=(path, safe),
                   name="chat-drop-video", daemon=True).start()
        return {"success": True}

    def _watch_progress(self, stage: str, message: str, done: bool = False,
                        pct=None, title: str = "") -> None:
        """把本地视频观看进度按 dialogue._make_video_progress 同构发上总线。"""
        try:
            if self._bus is None:
                return
            self._bus.publish_threadsafe(Event(
                EventType.VIDEO_WATCH_PROGRESS,
                {"stage": stage, "message": message, "done": done, "pct": pct,
                 "kind": "video", "title": title, "url": "local://chat-drop"},
                "webapp",
            ))
        except Exception:  # noqa: BLE001
            pass

    def _watch_send_text(self, text: str) -> None:
        """观看结束后把 enriched 文本作为一条 webui 消息驱动正常聊天回复。"""
        try:
            if self._bus is None or self._loop is None:
                return

            async def _send() -> None:
                await self._bus.publish(Event(EventType.USER_TEXT, {
                    "text": text, "source": "webui",
                }))

            self._run_coro(_send(), timeout=3.0)
        except Exception:  # noqa: BLE001
            logger.exception("[陪你看·本地视频] 注入 USER_TEXT 失败")

    def _watch_local_video_worker(self, path: str, name: str) -> None:
        """后台线程：抽帧 → VLM 看画面 → 并入 USER_TEXT。任何一步失败都把原因
        以普通聊天消息说出来（绝不静默消失），临时文件用完即删。"""
        logger.info("[陪你看·本地视频] 开始 name=%s size=%.1fMB",
                    name, os.path.getsize(path) / 1048576.0)
        frames: list = []
        try:
            self._watch_progress("resolve", "达妮娅正在打开你拖进来的视频…", title=name)
            from ..channels.video_watch import sample_frames, _resolve_ffmpeg
            self._watch_progress("download", "本地文件，不用下载", done=True, pct=100, title=name)
            ffmpeg = _resolve_ffmpeg()
            if not ffmpeg:
                raise RuntimeError("电脑上没找到 ffmpeg，抽不了帧（设置 → 开发者 → 陪你看视频诊断 里有装法）")
            self._watch_progress("frame", "达妮娅正在截取画面…", title=name)

            def _frames():
                import asyncio as _aio1
                return _aio1.run(sample_frames(path, 6, ffmpeg))

            frames = _frames()
            if not frames:
                raise RuntimeError("没能从视频里截出画面（文件可能损坏，或这个格式 ffmpeg 不认）")
            self._watch_progress("frame", f"截了 {len(frames)} 帧", done=True, title=name)
        except Exception as e:  # noqa: BLE001
            logger.exception("[陪你看·本地视频] 抽帧失败")
            self._watch_progress("frame", str(e), done=True, title=name)
            self._watch_send_text(
                f"我往聊天里拖了一个本地视频《{name}》，但是达妮娅没能看成：{e}")
            try:
                os.remove(path)
            except OSError:
                pass
            return

        # VLM 看画面（同 test_video_link 阶段 3）
        desc = ""
        try:
            self._watch_progress("describe", "达妮娅正在看画面…", title=name)
            vr = getattr(self, "_vision_router", None)

            def _describe():
                import asyncio as _aio2
                async def _go():
                    if vr is None:
                        raise RuntimeError("视觉模型没注入（vision_router 不可用）")
                    if hasattr(vr, "vision_describe"):
                        return await _aio2.wait_for(vr.vision_describe(list(frames[:3])), timeout=120)
                    if hasattr(vr, "vision_chat"):
                        return await _aio2.wait_for(
                            vr.vision_chat(list(frames[:3]), "描述这段视频的画面内容"), timeout=120)
                    raise RuntimeError("视觉路由不支持看图")
                return _aio2.run(_go())

            desc = str(_describe() or "").strip()
        except Exception as e:  # noqa: BLE001
            logger.exception("[陪你看·本地视频] VLM 看画面失败")
            self._watch_progress("describe", f"看画面失败：{e}", done=True, title=name)
            self._watch_send_text(
                f"我往聊天里拖了一个本地视频《{name}》，画面截出来了但看画面这步失败：{e}")
            try:
                os.remove(path)
            except OSError:
                pass
            return

        self._watch_progress("describe", "看完了", done=True, title=name)
        self._watch_send_text(
            f"我往聊天里拖了一个本地视频文件《{name}》（不是链接）。\n"
            f"你看过的画面内容如下：\n{desc}\n\n跟我聊聊这个视频呀。")
        logger.info("[陪你看·本地视频] 完成 name=%s desc=%d字", name, len(desc))
        try:
            os.remove(path)
        except OSError:
            pass

    def _next_chat_id(self) -> int:
        self._chat_seq += 1
        return self._chat_seq

    async def _on_tts_saved(self, e: Event) -> None:
        """语音存档完成：把回放 URL 挂到对应的 AI 聊天消息上（按文本匹配，找不到则挂最近一条 AI 消息）。"""
        d = e.data or {}
        url = d.get("url") or ""
        text = (d.get("text") or "").strip()
        if not url:
            return
        target = None
        for m in reversed(self._chat_buffer):
            if m.get("role") != "ai":
                continue
            if text and m.get("text") == text:
                target = m
                break
            if target is None:
                target = m
        if target is not None:
            target["audio"] = url

    async def _on_chat_user_text(self, e: Event) -> None:
        """用户消息（主动搭话的 USER_TEXT 不再记为用户消息，改走 AI_DECISION）。

        2026-10-03：随消息发来的图片（base64 data URL 列表）落盘到
        images/chat/ 并以 /local_img URL 记进消息（"images" 字段），前端聊天气泡
        在文字下方渲染真实缩略图；此前图片被整段丢弃、前端只能显示 "📷×1" 占位。
        纯图片（无文字）消息也不再整条丢失。"""
        d = e.data or {}
        if d.get("is_proactive"):
            return
        text = (d.get("text") or "").strip()
        _raw_imgs = d.get("images") or []
        img_urls = self._save_chat_images(_raw_imgs) if _raw_imgs else []
        if not text and not img_urls:
            return
        entry = {
            "id": self._next_chat_id(),
            "role": "user",
            "text": text,
            "source": d.get("source", ""),
            "is_proactive": False,
            "trigger": "",
            "ts": time.time(),
        }
        if img_urls:
            entry["images"] = img_urls
        self._chat_buffer.append(entry)
        self._persist_chat(entry)
        self._overlay_push("user", text)
        _wv2_broadcast({"type": "chat_push"})

    # 【2026-10-04 22:58 新增】"内省类"工具名 —— **这些是模型的内部思考动作，
    # 不是"用户在看她操作电脑"**。用户 22:55 实锤两张截图：
    #   ①「能不能以后就叫你娅娅」（**纯聊天**）→ 却冒出「达妮娅正在操作电脑 / 完成」
    #      日志实锤只调了 `remember({"text":"用户希望以后称呼我为「娅娅」…"})`，
    #      却渲染成一张"操作电脑"卡（而且**重复三张**、一上来就显示"完成"）。
    #   ②「帮我打开汽水音乐播放歌曲」→ 同样先冒卡片。
    # ⇒ 根因：这些工具与 `open_app/see_screen/ui_inspect/play_song` 一样被写进
    #   `meta["steps"]`，前端只要 `steps.length > 0` 就把标题切成"正在操作电脑"。
    # 正确做法：内省类工具**不产生步骤、不触发卡片**，只在日志里留痕。
    _AGENT_INTERNAL_TOOLS = frozenset({
        "recall", "remember", "forget",        # 记忆读写
        "list_processes", "system_info", "get_volume",   # 纯查询
        "wait",                                          # 内部等待
    })

    @staticmethod
    def _is_internal_step(d: dict) -> bool:
        """这条 step 事件是不是"模型的内部动作"（记忆读写 / 纯查询 / 内部等待）。

        ⚠️【2026-10-04 23:20 "调记忆也冒操作卡"的真正根因】
        上一版只在 `_merge_agent_meta` 里"过滤掉步骤"，但 `_on_agent_status`
        **照样会新建卡片** —— 于是纯聊天（`remember({"text":"用户希望以后称呼
        我为「娅娅」"})`）也会弹出一张「达妮娅正在操作电脑」的**空卡**；
        而 `remember` 毫秒级返回、`phase:"done"` 紧随其后，前端第一次轮询拿到的
        meta 就已经 `done=True` ⇒ **卡片一上屏就是"完成"**（用户 22:55 / 23:15
        两次实锤"一弹出来就是完成"）。
        又因为 steps 为空：① 前端 `acExpand()` 的展开条件（plan/steps/watch）
        全不满足 ⇒ **展开动画从不播放**；② `ac-progress` 被 `display:none`
        ⇒ **步骤跟进 / 进度条全看不到**。
        ⇒ 正确做法：内省类工具的**整条事件直接丢弃**（不建卡、不改文案、不计步）。
        """
        _tname = str(d.get("tool") or "").strip()
        if _tname:
            return _tname in CharacterAPI._AGENT_INTERNAL_TOOLS
        # 没有 tool 字段才退化：按 step 文案里**独立出现**的工具名判断
        # ⚠️ 必须"独立出现"精确比对：初版写成子串包含 ⇒ 误伤所有工具
        #   （`open_app` 等全被吞掉，实测 4 步变 0 步）。
        _sname = str(d.get("step") or "")
        for _it in CharacterAPI._AGENT_INTERNAL_TOOLS:
            if re.search(r"(?<![\w])%s(?![\w])" % re.escape(_it), _sname):
                return True
        return False

    @staticmethod
    def _merge_agent_meta(meta: dict, d: dict) -> dict:
        """把一条结构化 agent 状态合并进 meta（plan / steps 时间线 / done / error）。

        供 _on_agent_status 复用同一条状态卡片——每次新状态到达时增量更新，不刷屏。
        """
        phase = d.get("phase") or meta.get("phase") or "status"
        meta["phase"] = phase
        if phase == "plan":
            if d.get("text"):
                meta["plan"] = d.get("text")
        elif phase == "step":
            step = d.get("step")
            st = d.get("state") or "running"
            # 【23:20】上游失败步写的是 `state:"failed"`，而前端 CSS 只有
            # `.ac-step.err` ⇒ 这里归一化成 `err`。否则增量更新时 className 被写成
            # `ac-step failed`（无对应 CSS）⇒ **红叉与红色样式全丢**，且不计入进度。
            if st == "failed":
                st = "err"
            # 【22:58】内省类工具（记忆读写/纯查询）**不产生 UI 步骤**：
            #   它们是模型的内部动作（"我记住了"），不是"她在操作你的电脑"。
            #   直接忽略 ⇒ 不进 steps ⇒ 前端不会出现"操作电脑"卡片。
            if CharacterAPI._is_internal_step(d):
                return meta
            # 新的一步开始 → 把之前仍 running 的步骤收尾为 done（避免卡在"执行中"）
            if st == "running":
                for s in meta["steps"]:
                    if s.get("state") == "running":
                        s["state"] = "done"
            # 【23:20】一步可能同时调多个工具：上游 `step` 是**LLM 轮次序号**，
            # 同一轮里的 `open_app` + `play_song` 会带**同一个 step 号**。
            # 旧代码只按 step 号找 ⇒ 第二个工具被合并进第一个的条目里，
            # 时间线少一步、工具名被覆盖。现在按 (step, tool) 双键匹配。
            _tkey = str(d.get("tool") or "").strip()
            entry = None
            for s in meta["steps"]:
                if s.get("step") != step:
                    continue
                if _tkey and s.get("tool") and str(s.get("tool")) != _tkey:
                    continue
                entry = s
                break
            if entry is None:
                entry = {"step": step,
                         # 【2026-10-04 23:08 治"进度条一出来就满/一弹出来就完成"】
                         # 上游 `openai_client` 发的 `total` 是 **`_total = max_steps or 6`
                         # ——固定 6 的"预估上限"，不是真实要执行的步数**。于是：
                         #   · 内省工具被过滤后 steps 只剩 2~3 条，而 total 仍写 6
                         #   · 前端 `doneCount/total` 算不出满格，但 `phase=done` 一到
                         #     就把 running 收尾 + 显示"完成" ⇒ **看起来"一弹出来就是
                         #     完成"、进度条也不跟**。
                         # 正确做法：**total 以"实际见到的步骤数"为准**，未知时留空
                         # （前端已有 `total ? ... : ...` 兜底，app_backend.js:1781）。
                         "total": None,
                         "tool": d.get("tool"), "args": d.get("args"),
                         "state": st, "result": d.get("result")}
                meta["steps"].append(entry)
            else:
                for k in ("state", "result", "total", "tool", "args", "ok", "reason"):
                    if d.get(k) is not None:
                        entry[k] = d.get(k)
                # ⚠️ 上面会把原始 `state:"failed"` 直接写回、绕过归一化 ⇒ 收尾时
                #   红叉样式又被打回无 CSS 的 `failed`。这里强制以归一化后的 st 为准。
                entry["state"] = st
            # 【23:08】进度分母 = **已见步骤数**（逐步增长），不是上游那个假的 6。
            _seen = len(meta["steps"])
            _running = sum(1 for s in meta["steps"] if s.get("state") == "running")
            _denom = _seen + (1 if _running else 0)
            for s in meta["steps"]:
                s["total"] = _denom
            meta["done"] = False
            meta["error"] = bool(d.get("error"))
        elif phase == "done":
            meta["done"] = True
            # 【2026-10-04 20:12 治"假完成"】done 不再等于"成功"：
            # 上游 `openai_client` 现在会带上 partial / ok_count / total_count /
            # failed_tools。有失败步骤时 UI 走警告样式 + "未全部完成"，
            # 不再一律显示绿色的「完成」。旧事件没这些字段时按成功处理（不倒退）。
            meta["partial"] = bool(d.get("partial"))
            meta["ok_count"] = d.get("ok_count")
            meta["total_count"] = d.get("total_count")
            meta["failed_tools"] = list(d.get("failed_tools") or [])
            for s in meta["steps"]:
                if s.get("state") == "running":
                    s["state"] = "done"
        elif phase == "error":
            meta["error"] = True
            meta["done"] = True
            for s in meta["steps"]:
                if s.get("state") == "running":
                    s["state"] = "done"
        return meta


    async def _on_agent_status(self, e: Event) -> None:
        """Agent 执行状态 → 聊天页单条状态卡片（复用同一条，不刷屏）。

        - 新状态到达：更新/创建状态卡片（计划 / 步骤时间线 / 完成态）
        - 真实 AI 回复到达（_on_chat_ai_decision）：状态卡片被移除，由正式回复取代
        - 不进 TTS、不进对话历史（仅 UI 展示）
        """
        d = e.data or {}
        if isinstance(d, str):
            d = {"text": d}
        # 【2026-10-12 修·"什么都没搞却显示在操作电脑"】relationship_reporter 会往
        # AGENT_STATUS 发**关系里程碑通知**（phase='milestone'，桌宠/状态侧消费），
        # 这里曾把它当工具状态渲染成「达妮娅正在操作电脑」卡（01:32 日志实锤：
        # [Agent卡] 新建 phase=milestone text=跟你聊天的时候…）。里程碑不是操作，
        # 直接放行给其它订阅者，本处理器不建卡。
        if d.get("milestone") or d.get("phase") == "milestone":
            return
        text = (d.get("text") or "").strip()
        phase = d.get("phase") or "status"
        # 【2026-10-04 23:20 治"调记忆也冒操作卡 / 一弹出来就是完成"】
        # 内省类工具（remember / recall / forget / 纯查询 / wait）属于**模型的内部
        # 动作**，不是"她在操作你的电脑"。整条事件直接丢弃：不新建卡片、也不改写
        # 已有卡片的文案（否则正在执行 open_app 的卡片会被刷成"步骤 2/6 · remember"）。
        if phase == "step" and CharacterAPI._is_internal_step(d):
            return
        if not text:
            if phase == "step":
                text = f"步骤 {d.get('step')}/{d.get('total')} · {d.get('tool', '')}".strip()
            elif phase == "plan":
                text = "已生成执行计划"
            elif phase == "done":
                # 【2026-10-04 20:12】有失败步骤时**不说"完成"**，如实说"未全部完成"
                # 并点名卡住的工具（20:03 实锤：什么都没播成却显示「完成」）。
                _ft = d.get("failed_tools") or []
                if d.get("partial") and _ft:
                    text = "未全部完成（%s 卡住）" % "、".join(str(x) for x in _ft[:3])
                elif d.get("partial"):
                    text = "部分完成"
                else:
                    text = "操作完成"
            elif phase == "error":
                text = "操作出错"
        if not text:
            return
        if self._chat_status_id is not None:
            for m in self._chat_buffer:
                if m.get("id") == self._chat_status_id:
                    # 【2026-10-05】还有新的状态事件进来 ⇒ 这张卡**仍在活跃**，
                    #   撤销上一轮排下的"延迟删除"（否则新操作的卡片会被上一条
                    #   回复的定时器提前删掉，表现为"卡片消失又冒出来"）。
                    #   ⚠️ 仅对非终态事件撤销：phase=="done" 后不再撤销，
                    #   避免删除被永久推迟、卡片残留。
                    if phase != "done" and self._chat_status_drop_timer is not None:
                        try:
                            self._chat_status_drop_timer.cancel()
                        except Exception:  # noqa: BLE001
                            pass
                        self._chat_status_drop_timer = None
                    meta = m.get("agent_meta") or {
                        "plan": None, "steps": [], "done": False, "error": False, "phase": phase}
                    self._merge_agent_meta(meta, d)
                    m["agent_meta"] = meta
                    m["text"] = "⚙️ " + text
                    m["ts"] = time.time()
                    # 【2026-10-05 可观测】"操作卡没出现/卡死"排查口：每次合并都留痕
                    logger.info("[Agent卡] 合并 phase=%s tool=%s 步骤数=%d done=%s id=%s",
                                phase, d.get("tool") or "-", len(meta.get("steps") or []),
                                meta.get("done"), self._chat_status_id)
                    _wv2_broadcast({"type": "chat_push"})
                    return
            self._chat_status_id = None  # 气泡已不在缓冲（如被清空）→ 重建
        # 【2026-10-04 22:59 治"同一屏出现 3 张操作电脑卡"】用户 22:55 实锤：
        #   连续三条消息各留一张"达妮娅正在操作电脑/完成"卡片，堆在一起。
        # 根因：`_chat_status_id` 只是"**最近一条**状态卡"的指针，而清理只发生在
        #   **真实 AI 回复到达**时（`_on_ai_decision` → `_drop_chat_status`）。
        #   ⇒ 若某条消息**只调内省工具**（如 remember）而模型没另发正式回复，
        #      或者两条消息的状态卡在上一条回复前就连发，**旧卡片永远不会被清**，
        #      新状态找不到同 id 就又新建一张 ⇒ 越堆越多。
        # 现在：**新建状态卡前，把缓冲里所有其它"状态卡"消息一并清掉**，
        #   保证任一时刻**最多一张**状态卡（复用同一张气泡，语义也更对：
        #   它表示"当前正在做的事"）。
        try:
            _kept = False
            for _m in list(self._chat_buffer):
                if _m.get("kind") == "status":
                    if not _kept and _m.get("id") == self._chat_status_id:
                        _kept = True   # 复用已存在的那张
                        continue
                    self._chat_buffer.remove(_m)
        except Exception:  # noqa: BLE001
            pass
        # 【23:20】`phase:"done"` 到达时**缓冲里根本没有卡片** ⇒ 本轮没有任何
        # "对外可见"的动作（全被内省工具过滤掉了，或模型压根没调工具）。
        # 此时新建一张"完成"卡只会误导用户（旧行为 = 纯聊天也闪一张「操作完成」）。
        if phase == "done" and self._chat_status_id is None:
            return
        self._chat_status_id = self._next_chat_id()
        # 新卡开始新的生命周期（撤销旧定时器 + 重置出生时间）
        self._revive_status_card()
        meta = {"plan": None, "steps": [], "done": False, "error": False, "phase": phase}
        self._merge_agent_meta(meta, d)
        # 【2026-10-05 可观测】新建状态卡留痕（配合合并日志判断"卡从未建"还是"建了被删"）
        logger.info("[Agent卡] 新建 phase=%s tool=%s id=%s text=%s",
                    phase, d.get("tool") or "-", self._chat_status_id, text[:40])
        self._chat_buffer.append({
            "id": self._chat_status_id,
            "role": "ai",
            "text": "⚙️ " + text,
            "char_name": self._dialogue.current_character_name if self._dialogue else "AI",
            "emotion": "neutral",
            "action": "idle",
            "is_proactive": False,
            "trigger": "",
            "failed": False,
            "kind": "status",
            "agent_meta": meta,
            "blocked": False,
            "block_reason": "",
            "ts": time.time(),
        })
        _wv2_broadcast({"type": "chat_push"})

    def _revive_status_card(self) -> None:
        """状态卡/观看卡开始新生命周期：撤销待删定时器 + 重置出生时间。

        【2026-10-05】两处都必须：
        - 不撤销定时器 ⇒ 上一条回复排下的延迟删除会把**新卡的**指针内容删掉
          （表现为"新操作/正在看的卡刚建好就被莫名移除"）；
        - 不重置 born_ts ⇒ 最小存活期按旧卡算（01:39 实锤：观看卡日志打出
          `存活=345.63s`——born_ts 还停留在 345 秒前的上一张操作卡）。
        """
        if self._chat_status_drop_timer is not None:
            try:
                self._chat_status_drop_timer.cancel()
            except Exception:  # noqa: BLE001
                pass
            self._chat_status_drop_timer = None
        self._chat_status_born_ts = time.time()

    def _drop_chat_status(self, force: bool = False) -> None:
        """Agent 状态气泡生命周期终结（真实回复到达时调用）。

        【2026-10-05】默认走**最小存活期**：若卡片上屏还不满 `_AGENT_CARD_MIN_LIFE`
        秒，就挂一个 Timer 到期再终结（force=True 用于清空聊天等必须立刻清的场景）。
        没有这层的话，毫秒级完成的操作会让卡片"出生即死亡"。

        force=True = **真删除**（清空聊天/测试断言用）；force=False 时，已完成的卡
        会被 `_drop_chat_status_now` 转成持久历史卡（`kind="status_done"`）而不是
        消失，这样刷新界面后仍能看到"刚才做过什么"（用户 04:17 实锤）。
        """
        if self._chat_status_id is None:
            return
        if force:
            self._chat_keep_done = False
        _left = self._AGENT_CARD_MIN_LIFE - (time.time() - (self._chat_status_born_ts or 0.0))
        if not force and _left > 0.05:
            if self._chat_status_drop_timer is not None:
                return          # 已经在排队了，不重复排
            try:
                t = threading.Timer(_left, self._drop_chat_status_now)
                t.daemon = True
                self._chat_status_drop_timer = t
                t.start()
                logger.info("[Agent卡] 延迟移除 %.2fs（最小存活期，避免一闪而过） id=%s",
                            _left, self._chat_status_id)
            except Exception:  # noqa: BLE001
                self._drop_chat_status_now()
            return
        self._drop_chat_status_now()

    def _drop_chat_status_now(self) -> None:
        """状态卡生命周期终结。

        【2026-10-05 修"刷新丢卡"】不再直接 `del` —— 那样操作完成后卡片从历史里
        彻底消失，用户刷新界面后连"刚才做过什么"都看不到（04:17 实锤：汽水音乐
        卡完成后消失，刷新后不见了）。改为把**已完成的卡就地转成持久的历史卡**：
        - `kind` 从 "status" 改为 "status_done"（前端据此渲染为静态折叠小卡，
          不再参与"当前正在执行"的指针语义）；
        - 文本压成一行摘要（保留原 ts ⇒ 排序位置不变 ⇒ 时序正确）；
        - 用户主动"清空聊天"时走 force 路径，仍是真删除。
        """
        try:
            self._chat_status_drop_timer = None
            sid = self._chat_status_id
            if sid is None:
                return
            for i, m in enumerate(self._chat_buffer):
                if m.get("id") != sid:
                    continue
                meta = m.get("agent_meta") or {}
                finished = bool(meta.get("done") or meta.get("error"))
                # 三种语义：
                #   force（_chat_keep_done=False）→ 一律真删除（清空聊天/测试）
                #   未完成 + 普通路径            → 真删除（异常中断，不留半截）
                #   已完成 + 普通路径            → 转历史卡（刷新不丢）
                if (not self._chat_keep_done) or (not finished):
                    logger.info("[Agent卡] 移除（%s） id=%s 存活=%.2fs",
                                "强制清空" if not self._chat_keep_done else "未完成即被清理",
                                sid, time.time() - (self._chat_status_born_ts or 0.0))
                    del self._chat_buffer[i]
                    break
                m["kind"] = "status_done"
                m["agent_meta"] = meta
                m["text"] = _status_done_summary(meta, m.get("text") or "")
                m["ts"] = m.get("ts") or time.time()
                logger.info("[Agent卡] 转历史卡（完成后保留，刷新不丢） id=%s 存活=%.2fs",
                            sid, time.time() - (self._chat_status_born_ts or 0.0))
                break
            self._chat_status_id = None
            self._chat_keep_done = True    # 复位（force 路径用完即还原）
        except Exception:  # noqa: BLE001
            self._chat_status_id = None

    async def _on_chat_partial(self, e: Event) -> None:
        """LLM 流式片段：逐句上屏（打字机），并按需分段成多条气泡。

        【2026-10-11 分段输出（类 AstrBot 连发）】开启 chat_split_enabled 时：每凑够一个
        "像样长度"的句子就把当前气泡**封口**，下一句另起一条新气泡（前端本就按
        _chat_buffer 每条渲染一个气泡）——观感是"一句一句说"的连发，而不是一大坨挤一格。
        TTS 不受影响：它订阅的是 PARTIAL_TEXT（本函数只是订阅者之一），逐句播报照旧。
        """
        d = e.data or {}
        # 【G 社交通道】带非桌面 channel（如 "qq"）的流式句子不是桌面对话，
        # 不进桌面聊天浮窗打字机（避免 QQ 私聊显示在桌面）。
        if d.get("channel") and d.get("channel") != "desktop":
            return
        # 【2026-10-18 修复·双工串到聊天页 + 打字串气泡】
        # 本函数（流式打字机）此前**没有** is_proactive 闸门，而
        # `_on_chat_ai_decision`（收尾）有 —— 于是双工/看屏互动只要 PARTIAL_TEXT
        # 先到，就会在这里 `_open_chat_stream_bubble()` 建出聊天气泡；
        # 更糟的是它把 `_chat_stream_id` 指向了这条双工消息，
        # 用户在输入框打的新消息因此一直往那条旧气泡上追加/改字
        # （用户反馈："我打新东西它还继续修改老气泡"）。
        # 修：与 decision 侧完全对齐 —— is_proactive（双工/看屏）一律不进聊天页，
        #     且**绝不占用** _chat_stream_id，避免污染后续正常打字对话的流式状态。
        if bool(d.get("is_proactive")) or bool(d.get("to_chat") is False):
            logger.debug("[聊天流] 双工/看屏片段不进聊天页（is_proactive=%s）: %r",
                         bool(d.get("is_proactive")), str(d.get("text"))[:40])
            return
        text = (d.get("text") or "").strip()
        if not text:
            return
        char_name = self._dialogue.current_character_name if self._dialogue else "AI"
        cur = None
        if self._chat_stream_id is not None:
            for m in self._chat_buffer:
                if m.get("id") == self._chat_stream_id:
                    cur = m
                    break
            if cur is None:
                # 流式消息已不在缓冲（如清空聊天/异常替换）→ 重置并重建一条，
                # 否则 _chat_stream_id 一直指向不存在的消息，后续所有片段都会丢
                self._chat_stream_id = None
                self._chat_seg_count = 0
        if cur is None:
            # 本轮回复的第一条气泡
            self._chat_seg_count = 0
            self._open_chat_stream_bubble(text, char_name)
            _wv2_broadcast({"type": "chat_push"})
            return
        # 已有"生成中"气泡：判断是"继续追加"还是"封口后另起一条"
        # 仅在"句子级"片段上分段——工具链路的逐 token 片段保持单气泡（否则每几个字切一条）。
        is_sentence = (d.get("granularity") == "sentence")
        # 本轮上限：优先用后端按"用户话长"给的 seg_limit（对话真源）；没带则退回本地绝对上限。
        _seg_cap = int(d.get("seg_limit") or 0) or max(1, int(self.chat_split_max))
        # 【2026-10-18 修复·聊天弹幕不分行/粘一起】
        # 旧条件要求"当前气泡已累积 >= min_chars 字"才封口，于是短句（"发呆呀"）
        # 会不断被追加进同一气泡 → 看起来所有话粘成一行。
        # 现在：句子级片段到达且当前气泡非空就封口另起一条（连发效果）。
        if (is_sentence and self.chat_split_enabled
                and self._chat_seg_count < _seg_cap
                and len((cur.get("text") or "").strip()) > 0):
            # 当前这条已"够一句" → 封口落盘，下一句开新气泡（连发效果）
            cur["streaming"] = False
            self._persist_chat(cur)
            logger.info("[聊天流] 分段封口 id=%s 第%d段=%d字",
                        cur.get("id"), self._chat_seg_count, len(cur.get("text") or ""))
            self._open_chat_stream_bubble(text, char_name)
        else:
            cur["text"] += text
        _wv2_broadcast({"type": "chat_push"})

    def _attach_sticker(self, msg: dict, char_name: str = "") -> None:
        """给一条 AI 消息附加表情包（**不改变生成逻辑，只在落库前贴图**）。

        【2026-10-17 接入·表情包技能包】
        背景：sticker_bridge 早就有 pick()（按角色+对话挑表情），
        但聊天链路**从来没调用过** —— dialogue.py 里 sticker 出现 0 次，
        所以用户"预载了表情包却永远收不到"。
        为什么在 webapp 这层做而不是改 dialogue：
          dialogue.respond 有 1.1 万字符、含搜索/工具/视觉多分支，
          动主链路风险极高。这里只做"落库前贴图"，失败最多没表情，绝不影响聊天。

        pick() 真实返回：{ok, result: {meme_id, pack_id, found, file, description}}
        meme_image_url(pid, mid) 返回：{ok, url}
        """
        try:
            br = getattr(self, "_sticker_bridge", None)
            if br is None:
                from .sticker_bridge import StickerBridge
                br = StickerBridge()
                self._sticker_bridge = br
            if not br._ensure():
                return
            # 最近几条对话作为语境
            conv = []
            try:
                conv = [{"role": m.get("role"), "text": str(m.get("text") or "")[:120]}
                        for m in (self._chat_buffer or [])[-6:]]
            except Exception:  # noqa: BLE001
                conv = []
            r = br.pick(char_name or "", conv)
            if not isinstance(r, dict) or not r.get("ok"):
                logger.debug("[表情包] pick 未返回: %s", str(r.get("error"))[:60])
                return
            res = r.get("result") or {}
            if not res.get("found"):
                logger.debug("[表情包] 本轮无合适表情（candidates=%s）", r.get("candidates"))
                return
            mid = str(res.get("meme_id") or "")
            pid = str(res.get("pack_id") or "")
            url = ""
            if mid and pid:
                try:
                    u = br.meme_image_url(pid, mid)
                    if isinstance(u, dict) and u.get("ok"):
                        url = str(u.get("url") or "")
                except Exception:  # noqa: BLE001
                    url = ""
            if url:
                msg["sticker_url"] = url
                msg["sticker_id"] = mid
                msg["sticker_pack"] = pid
                msg["sticker_desc"] = str(res.get("description") or "")[:60]
                logger.info("[表情包] 已附加 pack=%s id=%s desc=%r",
                            pid[:10], mid[:10], msg["sticker_desc"])
        except Exception as e:  # noqa: BLE001
            logger.debug("表情包附加失败（不影响聊天）: %s", e)

    def _open_chat_stream_bubble(self, text: str, char_name: str) -> None:
        """新建一条"生成中"的 AI 消息气泡（分段输出的一个单位）。"""
        self._chat_stream_id = self._next_chat_id()
        self._chat_seg_count += 1
        # 【2026-10-18 修复·多轮回复粘成一条/旧气泡被反复改写】
        # `_chat_seg_count` 此前只在 decision 收尾时归零。若某轮收尾事件丢失
        # （双工串流、异常、decision 未到），计数会持续累加并超过 seg_cap，
        # 之后 `_on_chat_partial` 的封口条件 `_chat_seg_count < _seg_cap` 恒为假
        # → 所有后续回复永远追加进**同一个气泡**（用户看到"多段话粘一起"），
        # 且用户发新消息时仍在改那条老气泡。
        # 现在：本轮段数超上限即视为新一轮，强制重置分段计数（不动 id）。
        _cap = max(1, int(getattr(self, "chat_split_max", 4) or 4))
        if self._chat_seg_count > _cap:
            logger.info("[聊天流] 分段计数 %d 超上限 %d → 判定为新一轮，重置",
                        self._chat_seg_count, _cap)
            self._chat_seg_count = 1
        logger.info("[聊天流] 打字机新建 id=%s 第%d段 首句=%d字",
                    self._chat_stream_id, self._chat_seg_count, len(text))
        _bubble = {
            "id": self._chat_stream_id,
            "role": "ai",
            "text": text,
            "char_name": char_name,
            "emotion": "neutral",
            "action": "talk",
            "is_proactive": False,
            "trigger": "",
            "streaming": True,
            # 0=本轮首条（显示头像/名字）；>0=连发续条（前端紧凑化，仅贴在上一条下）
            "seg_index": self._chat_seg_count - 1,
            "ts": time.time(),
        }
        # 【2026-10-17】本轮**首条**气泡带表情包（连发续条不重复贴）
        if self._chat_seg_count == 1:
            self._attach_sticker(_bubble, char_name)
        self._chat_buffer.append(_bubble)

    async def _on_chat_ai_decision(self, e: Event) -> None:
        """AI 回复（含主动搭话，is_proactive 标记用于 GUI 显示 💗 气泡）。

        流式决策到达时：把"生成中"的消息替换为完整内容（结束打字机）。
        """
        d = e.data or {}
        # 【G 社交通道】带非桌面 channel（如 "qq"）的决策不进桌面聊天浮窗/桌宠气泡，
        # 由对应通道发回手机/客户端。
        if d.get("channel") and d.get("channel") != "desktop":
            return
        text = (d.get("text") or "").strip()
        if not text:
            return
        # 真实 AI 回复到达 → 移除"正在执行"状态气泡（被正式回复取代）
        self._drop_chat_status()
        # 【双工看屏幕互动 → 桌宠 Live2D 气泡（2026-09-03）】
        # is_proactive 标记代表双工/看屏互动消息。这类消息不进聊天记录（聊天只
        # 展示纯打字对话），改走 _wv2_broadcast 投到桌宠 Live2D 的 petShowBubble
        # 气泡，避免两个 UI 抢通道/互相覆盖。质量门拦截的残句同样不投。
        if bool(d.get("is_proactive")):
            if bool(d.get("blocked")):
                logger.info("双工质量门拦截文本不投桌宠气泡: %r (reason=%s)",
                            text, d.get("block_reason", ""))
                return
            logger.info("[pet_tip] 投双工气泡(桌宠Live2D): %r", text)
            # 双工输出对话日志：把每一句双工说出口的话记进环形缓冲，供调试台「双工输出」板块展示。
            # 【2026-09-11】raw = 被长度卡口裁剪**之前**的原句；仅本句真被截断时才有。
            self._duplex_output_log.append({"ts": time.time(), "text": text,
                                            "raw": (d.get("raw_text") or "")})
            if len(self._duplex_output_log) > 200:
                self._duplex_output_log = self._duplex_output_log[-200:]
            self._pet_bubble(text)  # 入队（rig 监听注册后用 pop_pet_bubble 补拉，前端拉取安全非轮询）
            # 投递到桌宠窗：仅用 PostWebMessageAsJson 广播（与 chat_push 同通道）。
            # WebView2 原生消息经 _WV2_BC_EXECUTOR 异步派发 + IsConnected 检查，绝不阻塞
            # GUI 线程，冻结安全。
            # 【2026-09-03 实锤·卡死根因】曾用 evaluate_js 直投：pywebview 的 evaluate_js
            # 内部 window.Invoke 同步等待 GUI 线程，而本地 omni 模型占满显存→WebView2 GPU
            # 进程崩溃→GUI 线程被 SendMessage 永久阻塞→整软件"Python 未响应"卡死（日志
            # 21:59:05 之后全停、且"直投成功/失败"日志缺失为死证）。故彻底弃用 evaluate_js
            # 直投，气泡改由前端三保险（广播 + window.chrome.webview 监听 + pop_pet_bubble
            # 队列补拉）保证必现，零跨线程同步 WebView2 调用。
            _wv2_broadcast({"type": "pet_tip", "text": text})
            return
        if d.get("streamed") and self._chat_stream_id is not None:
            # 结束打字机：分段模式下**只封口最后一段**（文本已逐句上屏并各自落盘，
            # 再用完整决策文本覆盖最后一条会把整段重复进去）；旧单气泡模式仍覆盖那一条。
            _split = bool(getattr(self, "chat_split_enabled", True))
            for m in self._chat_buffer:
                if m.get("id") == self._chat_stream_id:
                    if not _split:
                        m["text"] = text   # 旧行为：单气泡，用权威完整文本收尾
                    m["streaming"] = False
                    m["emotion"] = d.get("emotion", "neutral")
                    m["action"] = d.get("action", "idle")
                    m["is_proactive"] = bool(d.get("is_proactive"))
                    m["trigger"] = d.get("trigger", "")
                    self._persist_chat(m)
                    break
            # 【2026-10-05 可观测】"她不理我/回复没上屏"排查：decision 落缓冲留痕
            logger.info("[聊天流] decision 收尾打字机 id=%s 字数=%d 分段=%s 本轮段数=%d channel=%r",
                        self._chat_stream_id, len(text), _split,
                        self._chat_seg_count, d.get("channel") or "")
            self._chat_stream_id = None
            self._chat_seg_count = 0
            self._overlay_push("ai", text)
            _wv2_broadcast({"type": "chat_push"})
            return
        # 【2026-10-18 修复·跨轮段数累积导致多轮回复粘成一个气泡】
        # 上面那条 `streamed and _chat_stream_id` 分支才重置分段计数；若本轮
        # streamed=False（非流式/工具链路/异常）或 _chat_stream_id 已被清空，
        # `_chat_seg_count` 就会一路累加。而 `_on_chat_partial` 的封口条件是
        # `_chat_seg_count < _seg_cap` —— 一旦超限就恒为假 ⇒ 之后所有轮次的回复
        # 全部追加进**同一个气泡**（用户看到"三段不相关的话粘在一起"）。
        # 这里无条件收尾并归零，保证每轮结束都是干净状态。
        if self._chat_stream_id is not None or self._chat_seg_count:
            logger.info("[聊天流] 非流式收尾：清理残留流式状态 id=%s 段数=%d",
                        self._chat_stream_id, self._chat_seg_count)
            for m in self._chat_buffer:
                if m.get("id") == self._chat_stream_id and m.get("streaming"):
                    m["streaming"] = False
                    self._persist_chat(m)
                    break
        self._chat_stream_id = None
        self._chat_seg_count = 0
        char_name = self._dialogue.current_character_name if self._dialogue else "AI"
        blocked = bool(d.get("blocked"))
        if blocked:
            # 被质量门拦截的乱语/断节：彻底不进聊天流、不上屏——用户不想看到
            # "嗯，有""CUDA…"这类残句/乱码（tts 已不发声，这里连聊天记录也剔除）。
            # 只留日志供排查，避免"看得到奇怪的文字"的尴尬体验。
            logger.info("双工质量门拦截文本不进入聊天流: %r (reason=%s)",
                        text, d.get("block_reason", ""))
            return
        _dec_id = self._next_chat_id()
        _entry = {
            "id": _dec_id,
            "role": "ai",
            "text": text,
            "char_name": char_name,
            "emotion": d.get("emotion", "neutral"),
            "action": d.get("action", "idle"),
            "is_proactive": bool(d.get("is_proactive")),
            "trigger": d.get("trigger", ""),
            "failed": bool(d.get("failed")),
            "blocked": False,
            "block_reason": "",
            "ts": time.time(),
        }
        # 【2026-10-11·P1 里程碑】关系首跨阶段时，她说的那句"有分量的话"以
        #   AI_DECISION{kind:'milestone'} 走这条路 → 保留 kind/stage，前端据此
        #   渲染成「质变时刻」特殊气泡（而不是普通聊天气泡）。
        if d.get("kind") == "milestone" or d.get("milestone"):
            _entry["kind"] = "milestone"
            _entry["milestone_stage"] = d.get("stage") or ""
            _entry["milestone_kind"] = d.get("milestone_kind") or ""
        self._chat_buffer.append(_entry)
        self._persist_chat(_entry)
        # 【2026-10-05 可观测】决策直入缓冲留痕（streamed 但打字机缺失时走这条路）
        logger.info("[聊天流] decision 直入缓冲 id=%s streamed=%s 字数=%d channel=%r proactive=%s",
                    _dec_id, bool(d.get("streamed")), len(text),
                    d.get("channel") or "", bool(d.get("is_proactive")))
        self._overlay_push("ai", text)
        _wv2_broadcast({"type": "chat_push"})



    # 【2026-10-21 删】这里原来有 `pop_notify_cmd()`，docstring 写着「由桌宠页
    # rig/index.html 每 ~300ms 调用一次」—— 实测**没有任何地方调它**（所以它出现在
    # 反向接线闸门的清单里）。更要紧的是它调 `mode_notify.drain_cmds()`，而那个函数
    # **根本不存在**：`mode_notify` 的模块注释明确写着"本模块不再有任何 WebView2 /
    # js_api / 命令队列"（通知卡已改成原生窗 native_notify）。
    # 于是它被 `except Exception: return []` 吞掉，永远返回空列表 —— 一个"永远没有
    # 积压"的假接口。命令队列已经不存在了，没有东西可接线，直接删。

    async def _on_chat_unity_inbound(self, e: Event) -> None:
        """Unity 点击模型触发的对话。"""
        d = e.data or {}
        if d.get("message_type") != "user_input":
            return
        payload = d.get("payload", {}) or {}
        text = (payload.get("text") or "").strip()
        if not text:
            return
        _entry = {
            "id": self._next_chat_id(),
            "role": "user",
            "text": text,
            "source": "unity",
            "is_proactive": False,
            "trigger": "",
            "ts": time.time(),
        }
        self._chat_buffer.append(_entry)
        self._persist_chat(_entry)
        self._overlay_push("user", text)
        _wv2_broadcast({"type": "chat_push"})

    async def _on_call_speech(self, e: Event) -> None:
        """通话记录条目（双方文本 + 达妮娅语音存档）。

        【2026-10-02 新增】与聊天流**完全隔离**：这里只往 _call_records 追加并广播
        call_push，不碰 _chat_buffer。此前走 AI_DECISION 导致通话内容出现在聊天页，
        且用户说的话被前端按"达妮娅"渲染。
        """
        d = e.data or {}
        text = (d.get("text") or "").strip()
        if not text:
            return
        # 【2026-10-04 18:55 补漏】兜底开会话：并非所有通话都经 `start_duplex_call`
        # （桌宠常驻双工通道、或未来新增的接听入口都可能绕过它），那时
        # `_cur_call_session_id` 会一直是空 ⇒ 整通电话被聚合成"无会话"，
        # 跨越多次通话混成一大段（实测 17:07~18:45 的 25 句全落进一个会话）。
        # 这里以"收到本通第一句"为起点懒开 id，之后同一通话的语句自然共用它。
        if not (getattr(self, "_cur_call_session_id", None) or "").strip():
            self._cur_call_session_id = "call_%d" % int(float(d.get("ts") or time.time()))
            self._cur_call_session_start = time.time()
            logger.info("[通话记录] 检测到通话未登记会话，已按首句懒开 session_id=%s",
                        self._cur_call_session_id)
        rec = {
            "id": self._next_call_rec_id(),
            "speaker": "user" if d.get("speaker") == "user" else "assistant",
            "text": text,
            "audio_url": d.get("audio_url") or "",
            "engine": d.get("engine") or "",
            "ts": float(d.get("ts") or time.time()),
            "char_name": self._dialogue.current_character_name if self._dialogue else "达妮娅",
            # 【2026-10-04·会话切分】每条记录归属"当前通话会话"。拨号时由 start_duplex_call
            # 开新 id；挂断不清（迟到尾部消息仍并入本次），下次拨号再开新。无所属时留空
            # （理论上通话内必有 id，空串仅在极端边界出现，聚合时按时间间隙兜底）。
            "session_id": getattr(self, "_cur_call_session_id", None) or "",
        }
        self._call_records.append(rec)
        # 只保留最近 500 条，避免长时间通话把内存吃满
        if len(self._call_records) > 500:
            self._call_records = self._call_records[-500:]
        logger.info("[通话记录] %s: %s", rec["speaker"], text)
        self._save_call_records()
        _wv2_broadcast({"type": "call_push"})

    def _next_call_rec_id(self) -> int:
        self._call_rec_seq = int(getattr(self, "_call_rec_seq", 0) or 0) + 1
        return self._call_rec_seq

    def get_call_records(self, data: dict = None) -> dict:
        """返回通话记录（供独立「通话记录」UI 展示：双方文本 + 语音回放）。"""
        d = data or {}
        try:
            limit = max(1, min(500, int(d.get("limit") or 200)))
        except Exception:  # noqa: BLE001
            limit = 200
        recs = list(getattr(self, "_call_records", []) or [])[-limit:]
        return {
            "success": True,
            "records": recs,
            "in_call": bool(getattr(self._proactive, "_duplex_app_call_active", False))
            if getattr(self, "_proactive", None) is not None else False,
        }

    def clear_call_records(self, data: dict = None) -> dict:
        """清空通话记录（通话记录 UI 的「清空」按钮）。同时删掉落盘文件。"""
        self._call_records = []
        try:
            if os.path.exists(self._call_records_path):
                os.remove(self._call_records_path)
        except Exception:  # noqa: BLE001
            pass
        _wv2_broadcast({"type": "call_push"})
        return {"success": True}

    # ======== 通话记录：会话聚合 + 持久化（2026-10-04 重做） ========
    # 底层仍是逐句记录（_call_records），UI 需要的「一次通话 = 一个会话」由这里按
    # 时间间隙切分得到。会话不依赖通话开始/结束事件（那套链路复杂易漏），纯靠
    # 相邻两句的时间间隔（> CALL_SESSION_GAP_S 即视为两次通话）切分，稳定且鲁棒。
    _CALL_SESSION_GAP_S = 900  # 同一会话内相邻两句最大间隔（15 分钟）

    def _save_call_records(self) -> None:
        """把逐句通话记录落盘（call_records.json），重启不丢。失败静默。"""
        try:
            with open(self._call_records_path, "w", encoding="utf-8") as f:
                json.dump(self._call_records, f, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            pass

    def _load_call_records(self) -> None:
        """启动时从 call_records.json 恢复逐句记录（若存在）。失败静默。"""
        try:
            if os.path.exists(self._call_records_path):
                with open(self._call_records_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self._call_records = [r for r in data if isinstance(r, dict)]
                    # 续接 id 序列，避免新记录与旧记录 id 撞车
                    self._call_rec_seq = max(
                        [int(r.get("id", 0) or 0) for r in self._call_records] + [0])
        except Exception:  # noqa: BLE001
            self._call_records = []

    def _build_call_sessions(self) -> list:
        """把逐句记录聚合成「一次通话 = 一个会话」的列表。

        聚合规则（2026-10-04 改）：
          · 优先按 session_id 切分 —— 每条记录由 start_duplex_call 拨号时开、挂断后并入
            迟到消息，天然对应「一次拨号↔挂断 = 一个会话」。
          · 兜底：没有 session_id 的旧数据（升级前落盘的），改回时间间隙切分
            （相邻两句 > _CALL_SESSION_GAP_S 即视为两次通话），保证老记录也不丢、不乱并。

        返回（按时间倒序，最新在前）：
          [{id, start_ts, end_ts, char_name, topic, engine, count, n_user,
            n_assistant, has_audio, utterances:[...]}]
        会话 id 优先用 session_id，否则用 `c{int(start_ts)}` 兜底，稳定可复现。
        """
        recs = sorted(self._call_records, key=lambda r: r.get("ts", 0) or 0)
        sessions: list = []
        cur: list = []
        cur_sid: Optional[str] = None  # 当前组类型：None=无id时间组，str=某 session_id 组
        last_ts = None
        for r in recs:
            ts = r.get("ts", 0) or 0
            sid = (r.get("session_id") or "").strip()
            if sid:
                # 有会话 id：与「当前组类型不同」或「id 不同」都切分
                # —— 绝不并入无 id 的旧数据，严格按「通话开始/结束」成会话。
                if cur and (cur_sid is None or sid != cur_sid):
                    sessions.append(cur)
                    cur = []
                cur_sid = sid
            else:
                # 无会话 id（旧数据兜底）：与「当前组是 session_id 组」或「时间间隔过大」都切分，
                # 两种分组模式互不污染。
                if cur and (cur_sid is not None
                           or (last_ts is not None and (ts - last_ts) > self._CALL_SESSION_GAP_S)):
                    sessions.append(cur)
                    cur = []
                cur_sid = None
            cur.append(r)
            last_ts = ts
        if cur:
            sessions.append(cur)
        # 反转为倒序（最新会话在前面），并计算每个会话的聚合字段
        out: list = []
        for grp in reversed(sessions):
            if not grp:
                continue
            start_ts = grp[0].get("ts", 0) or 0
            end_ts = grp[-1].get("ts", 0) or 0
            sess_id = (grp[0].get("session_id") or "").strip() or ("c%d" % int(start_ts))
            names = [r.get("char_name") for r in grp if r.get("char_name")]
            char_name = max(set(names), key=names.count) if names else "达妮娅"
            engines = [r.get("engine") for r in grp if r.get("engine")]
            engine = max(set(engines), key=engines.count) if engines else ""
            # 主题：优先第一句「用户说的话」，否则第一句助手的话，截断
            topic_src = next((r for r in grp if r.get("speaker") == "user" and r.get("text")),
                            grp[0] if grp[0].get("text") else None)
            topic = (topic_src.get("text", "") if topic_src else "") or "通话记录"
            topic = topic.strip().replace("\n", " ")
            if len(topic) > 24:
                topic = topic[:24] + "…"
            n_user = sum(1 for r in grp if r.get("speaker") == "user")
            n_assistant = sum(1 for r in grp if r.get("speaker") == "assistant")
            has_audio = any(r.get("audio_url") for r in grp)
            out.append({
                "id": sess_id,
                "start_ts": start_ts,
                "end_ts": end_ts,
                "char_name": char_name,
                "topic": topic,
                "engine": engine,
                "count": len(grp),
                "n_user": n_user,
                "n_assistant": n_assistant,
                "has_audio": has_audio,
                "duration_s": max(0, int(end_ts - start_ts)),
                "utterances": grp,
            })
        return out

    def get_call_sessions(self, data: dict = None) -> dict:
        """返回通话会话列表（UI 入口：每个会话含时间/主题/角色名）。"""
        try:
            sessions = self._build_call_sessions()
        except Exception:  # noqa: BLE001
            sessions = []
        in_call = bool(getattr(self._proactive, "_duplex_app_call_active", False)) \
            if getattr(self, "_proactive", None) is not None else False
        # 进行中的会话 = 当前拨号开的 session_id（比"列表第一条"更准：刚拨号可能还没话）
        active_id = (self._cur_call_session_id if (in_call and self._cur_call_session_id) else None)
        # 列表不下发完整 utterances（Detail 页再按需取），减轻负载
        list_view = [{k: v for k, v in s.items() if k != "utterances"} for s in sessions]
        return {
            "success": True,
            "sessions": list_view,
            "in_call": in_call,
            "active_session_id": active_id,
        }

    def get_call_session_detail(self, data: dict = None) -> dict:
        """返回某个通话会话的全部句子（双方文本 + 语音 URL），供详情页。"""
        d = data or {}
        sid = (d.get("session_id") or "").strip()
        try:
            sessions = self._build_call_sessions()
        except Exception:  # noqa: BLE001
            sessions = []
        for s in sessions:
            if s["id"] == sid:
                return {"success": True, "session": {k: v for k, v in s.items()}}
        return {"success": False, "error": "会话不存在或已过期", "session": None}

    def export_call_session(self, data: dict = None) -> dict:
        """导出某次通话会话为一个**文件夹**：`文字/` + `音频/` 两个子目录。

        【2026-10-04 20:00 重写·治"点了没反应"】
        旧实现把 zip 打包成 base64 交给前端 `a.download` 下载。在 window.pywebview 的
        WebView2 容器里 **blob 下载默认被拦截**——`downloadB64()` 还返回 true，
        前端 toast「已导出」，但**文件根本没落盘、也不报错、也不让用户选位置**，
        用户体感就是"点了啥提示都没有"（19:56 用户实锤反馈）。

        现在改为**后端直接写磁盘**：
          · `dest` 传了就写那里；没传则弹系统文件夹选择框让用户选（复用
            `pick_model_dir`，pywebview 原生 + tkinter 回退）；
          · 产出目录 `通话导出_<角色>_<时间戳>/`，内含：
              - `文字/transcript.txt`（带时间/角色/逐句正文）
              - `文字/通话记录.csv`（Excel 可直接打开，附列：序号/时间/角色/时长/文字）
              - `音频/001_你_HHMMSS.wav`（每句一个，按序号+时间命名便于对时间轴）
              - `README.txt`（说明本次通话信息与文件清单）
          · 返回 `{success, folder, files, text_count, audio_count}`，
            前端只需提示"已导出到 xxx"，路径**如实回显**。

        保留 `b64` 字段（留空串）以兼容旧前端分支，不会破坏既有调用。
        """
        d = data or {}
        sid = (d.get("session_id") or "").strip()
        try:
            sessions = self._build_call_sessions()
        except Exception:  # noqa: BLE001
            sessions = []
        target = next((s for s in sessions if s["id"] == sid), None)
        if not target:
            return {"success": False, "error": "会话不存在或已过期"}
        try:
            from ..voice.tts import WEB_ASSETS_DIR
        except Exception:  # noqa: BLE001
            WEB_ASSETS_DIR = os.path.join(
                os.path.dirname(os.path.abspath(self._config_path)),
                "src", "ui", "web", "assets", "chat_audio")

        # ---- 决定保存位置：显式指定 > 弹框询问 ----
        dest = str(d.get("dest") or "").strip()
        if not dest:
            picked = self.pick_model_dir()
            if not picked or not picked.get("success"):
                return {"success": False, "canceled": True, "error": ""}
            dest = str(picked.get("path") or "").strip()
        if not dest:
            return {"success": False, "error": "未选择保存位置"}

        stamp = _fmt_file_ts(target.get("start_ts", 0))
        folder_name = "通话导出_%s_%s" % (target.get("char_name", "danya") or "danya", stamp)
        root = os.path.join(dest, folder_name)
        text_dir = os.path.join(root, "文字")
        audio_dir = os.path.join(root, "音频")
        try:
            os.makedirs(text_dir, exist_ok=True)
            os.makedirs(audio_dir, exist_ok=True)
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": "创建目录失败：%s" % e}

        utts = target.get("utterances") or []
        # ---- 文字稿 ----
        lines = ["通话记录导出", "=" * 40,
                 "角色：%s" % (target.get("char_name") or ""),
                 "主题：%s" % (target.get("topic") or ""),
                 "开始：%s" % _fmt_ts(target.get("start_ts", 0)),
                 "结束：%s" % _fmt_ts(target.get("end_ts", 0)),
                 "时长：%d 秒" % int(max(0, (target.get("end_ts") or 0) - (target.get("start_ts") or 0))),
                 "引擎：%s" % (target.get("engine") or "—"),
                 "总句数：%d" % len(utts), "=" * 40, ""]
        rows = ["序号,时间,角色,音频文件,文字内容"]
        n_audio = 0
        saved = []
        # 【2026-10-04 22:00】收集已导出的 wav 路径 + 对应文本，供末尾合并连贯音频用
        clip_paths: list[str] = []
        clip_texts: list[str] = []
        for i, r in enumerate(utts, 1):
            who = "你" if r.get("speaker") == "user" else (r.get("char_name") or "达妮娅")
            ts_s = r.get("ts", 0)
            lines.append("[%02d] %s  %s" % (i, who, _fmt_ts(ts_s)))
            lines.append((r.get("text") or "").strip() or "（无转写文本）")
            # ---- 音频 ----
            audio_name = ""
            url = r.get("audio_url") or ""
            if url:
                src = os.path.join(WEB_ASSETS_DIR, os.path.basename(url))
                if os.path.exists(src):
                    who_tag = "你" if r.get("speaker") == "user" else (target.get("char_name") or "达妮娅")
                    audio_name = "%02d_%s_%s.wav" % (i, who_tag, _fmt_file_ts(ts_s))
                    try:
                        import shutil as _sh
                        _dst = os.path.join(audio_dir, audio_name)
                        _sh.copyfile(src, _dst)
                        n_audio += 1
                        saved.append("音频/" + audio_name)
                        # 供末尾合并连贯音频（路径 + 该句文本，用于判断停顿）
                        clip_paths.append(_dst)
                        clip_texts.append((r.get("text") or "").strip())
                    except Exception:  # noqa: BLE001
                        audio_name = ""
                else:
                    audio_name = "(音频文件缺失)"
            if audio_name:
                lines.append("  🔊 音频/%s" % audio_name)
            lines.append("")
            # CSV 行（双引号转义，防内容含逗号/引号破坏表格）
            _txt = (r.get("text") or "").replace('"', '""').replace("\n", " ").replace("\r", " ")
            rows.append('%d,"%s","%s","%s","%s"' % (i, _fmt_ts(ts_s), who, audio_name, _txt))
        try:
            with open(os.path.join(text_dir, "transcript.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            with open(os.path.join(text_dir, "通话记录.csv"), "w", encoding="utf-8-sig") as f:
                f.write("\n".join(rows))
            readme = [
                "本次通话导出", "",
                "角色：%s" % (target.get("char_name") or ""),
                "时间：%s ~ %s" % (_fmt_ts(target.get("start_ts", 0)),
                                   _fmt_ts(target.get("end_ts", 0))),
                "句数：%d（其中带音频 %d）" % (len(utts), n_audio), "",
                "目录说明：",
                "  文字/transcript.txt   —— 完整对话稿（含时间戳与音频文件对应关系）",
                "  文字/通话记录.csv     —— 表格版，Excel/WPS 可直接打开",
                "  音频/                 —— 每句一个 wav，命名 = 序号_角色_时间.wav", "",
                "提示：音频序号与 transcript.txt / csv 的「序号」一一对应。",
                "      其中 完整通话.wav / 完整通话.mp3 是**整通对话合成的一段连贯音频**，",
                "      直接播放即可（停顿按标点自动调整）；逐句 wav 便于逐句引用。",
            ]
            with open(os.path.join(root, "README.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(readme))
            saved = ["README.txt", "文字/transcript.txt", "文字/通话记录.csv"] + saved
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": "写文件失败：%s" % e}

        # ---- 【2026-10-04 22:00 新增】合并成一段连贯音频 ----
        # 此前只导出「每句一个 wav」，用户要自己按序号拼、自己掐停顿。
        # 现在额外产出 `音频/完整通话.wav`（+ 可选 mp3），**重采样统一采样率**后
        # 按语义插入停顿，直接双击就能连着听完，像一通真实录音。
        # 合并失败**绝不阻断导出**（逐句 wav 仍然可用）。
        merged_info = {"ok": False, "duration": 0.0, "clips": 0}
        try:
            from ..companion.call_audio_merge import merge_call_audio, wav_to_mp3
            _items = [{"path": pth, "text": t}
                      for (pth, t) in zip(clip_paths, clip_texts) if os.path.exists(pth)]
            if _items:
                _wav = os.path.join(audio_dir, "完整通话.wav")
                _r = merge_call_audio(_items, _wav)
                if _r.get("ok"):
                    merged_info = {"ok": True, "duration": _r.get("duration", 0.0),
                                   "clips": _r.get("clips", 0),
                                   "path": _wav, "missing": _r.get("missing") or []}
                    saved.append("音频/完整通话.wav")
                    _mp3 = os.path.join(audio_dir, "完整通话.mp3")
                    if wav_to_mp3(_wav, _mp3):
                        saved.append("音频/完整通话.mp3")
                    # README 补一段说明（有连贯音频时）
                    with open(os.path.join(root, "README.txt"), "a", encoding="utf-8") as f:
                        f.write("\n\n连贯音频：\n"
                                "  音频/完整通话.wav —— 整通对话合成的一段音频，"
                                "时长约 %.0f 秒，采样率已统一。\n"
                                "  停顿按语句标点自动调整（问句留长、逗号留短），"
                                "听起来像真实通话录音。\n"
                                "  逐句 wav 仍保留在 音频/ 下，方便逐句引用。\n"
                                % (merged_info["duration"] or 0.0))
                    logger.info("[导出通话] 已合并连贯音频：%d 段 / %.0f 秒",
                                merged_info["clips"], merged_info["duration"])
        except Exception as e:  # noqa: BLE001
            logger.warning("[导出通话] 合并连贯音频失败（不影响导出）: %s", e)

        logger.info("[导出通话] %s → %s（%d句 / %d音频 / 合并%s）",
                    sid, root, len(utts), n_audio,
                    ("%.0f秒" % merged_info["duration"]) if merged_info.get("ok") else "跳过")
        return {"success": True, "folder": root, "files": saved,
                "text_count": len(utts), "audio_count": n_audio,
                "merged": merged_info,
                "b64": "", "filename": folder_name}

    def merge_call_audio_only(self, data: dict = None) -> dict:
        """只把某次通话合并成一段连贯音频（不导出文字稿），存到桌面。

        【2026-10-04 22:02】给"我想留个录音"这种轻量需求用：不必走整套导出
        （不弹选框、不产 CSV/README），直接落到桌面并回传路径，可立即试听。
        也可传 `dest` 指定目录；传 `play=True` 会自动打开播放器试听。
        """
        d = data or {}
        sid = (d.get("session_id") or "").strip()
        try:
            sessions = self._build_call_sessions()
        except Exception:  # noqa: BLE001
            sessions = []
        target = next((s for s in sessions if s["id"] == sid), None)
        if not target:
            return {"success": False, "error": "会话不存在或已过期"}
        try:
            from ..voice.tts import WEB_ASSETS_DIR
        except Exception:  # noqa: BLE001
            WEB_ASSETS_DIR = os.path.join(
                os.path.dirname(os.path.abspath(self._config_path)),
                "src", "ui", "web", "assets", "chat_audio")
        items = []
        for r in (target.get("utterances") or []):
            url = r.get("audio_url") or ""
            if not url:
                continue
            p = os.path.join(WEB_ASSETS_DIR, os.path.basename(url))
            if os.path.exists(p):
                items.append({"path": p, "text": (r.get("text") or "").strip()})
        if not items:
            return {"success": False, "error": "这通对话没有可用的音频"}
        dest = str(d.get("dest") or "").strip() or os.path.join(
            os.path.expanduser("~"), "Desktop")
        if not os.path.isdir(dest):
            dest = os.path.expanduser("~")
        stamp = _fmt_file_ts(target.get("start_ts", 0))
        out = os.path.join(dest, "通话录音_%s_%s.wav" % (
            target.get("char_name") or "danya", stamp))
        try:
            from ..companion.call_audio_merge import merge_call_audio, wav_to_mp3
            r = merge_call_audio(items, out)
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"合并失败：{e}"}
        if not r.get("ok"):
            return {"success": False, "error": r.get("error") or "合并失败"}
        mp3 = os.path.splitext(out)[0] + ".mp3"
        has_mp3 = wav_to_mp3(out, mp3)
        if d.get("play"):
            try:
                if os.name == "nt":
                    os.startfile(out)  # noqa: S606
            except Exception:  # noqa: BLE001
                pass
        return {"success": True, "path": out, "mp3": mp3 if has_mp3 else "",
                "duration": r.get("duration", 0.0), "clips": r.get("clips", 0)}

    def open_export_folder(self, data: dict = None) -> dict:
        """在文件管理器里打开导出目录（配合导出结果面板的「打开文件夹」按钮）。"""
        d = data or {}
        p = str(d.get("path") or "").strip()
        if not p:
            return {"success": False, "error": "未提供路径"}
        try:
            p = os.path.abspath(p)
            if not os.path.isdir(p):
                return {"success": False, "error": "目录不存在：%s" % p}
            if os.name == "nt":
                os.startfile(p)  # noqa: S606
            else:
                import subprocess as _sp
                _sp.Popen(["xdg-open", p])
            return {"success": True, "path": p}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": "打开失败：%s" % e}

    # ======== 聊天历史持久化（2026-10-10 补） ========
    def _active_char_id(self) -> str:
        try:
            cid = str(getattr(self._cfg, "active_character_id", "") or "").strip()
        except Exception:  # noqa: BLE001
            cid = ""
        return cid or "default"

    def _persist_chat(self, entry: dict) -> None:
        """把一条聊天消息落盘到 data/chatlog/<char>.jsonl（失败静默）。

        过滤（角色/状态卡/流式半句/残句）由 ChatLogStore.append 统一负责，
        这里只做调用与异常兜底。
        """
        if self._chatlog is None or not isinstance(entry, dict):
            return
        try:
            self._chatlog.append(self._active_char_id(), entry)
        except Exception:  # noqa: BLE001
            logger.debug("[聊天历史] 落盘失败", exc_info=True)

    def _init_context_persistence(self) -> None:
        """开启「喂模型的会话上下文」持久化，并把当前角色的历史灌回 shared_history。

        为什么必须在这做（**根因修复，不是优化**）：
          `shared_history`（`src/companion/conversation_history.py`）是**唯一**喂给
          大模型的短期会话上下文，但它原本是**纯内存 deque(80)**：
            - 重启 → 上下文归零；而聊天界面由 ChatLogStore 照旧显示历史
              ⇒ 用户看得见、模型看不见 ⇒ 答非所问（"是不是有什么误会"）。
            - 切角色 → 旧角色桶虽在，但若进程重启同样救不回来。
          现改为 **写穿透 + 启动回填**：每条 turns 落 `data/context/<char>.jsonl`
          （按角色隔离），启动时把该文件灌回内存桶。

        失败降级：**任何异常都不阻断启动**（最多退化为旧的"纯内存"行为）。
        """
        try:
            from ..companion.conversation_history import (
                enable_persistence as _enable_ctx,
                rehydrate as _rehydrate_ctx,
            )
            from ..companion.history_store import ChatLogStore as _CLS  # 同款 data 根推导
            try:
                root = _CLS().base.parent  # .../data/chatlog → .../data
            except Exception:  # noqa: BLE001
                root = None
            ok = bool(_enable_ctx(root)) if root is not None else False
            self._ctx_persist_ok = ok
            if ok:
                cid = self._active_char_id()
                n = _rehydrate_ctx(cid)
                logger.info("[上下文持久化] 已开启 data/context/%s.jsonl（回填 %d 轮）", cid, n)
            else:
                logger.warning("[上下文持久化] 未开启（退化为进程内上下文，重启会失忆）")
        except Exception:  # noqa: BLE001 持久化失败绝不阻断 UI 启动
            logger.debug("[上下文持久化] 初始化失败（忽略）", exc_info=True)
            self._ctx_persist_ok = False

    def _restore_context_for_active_char(self) -> int:
        """把当前角色的上下文灌回 shared_history（**幂等**，可安全重复调）。

        为什么还要这一步：构造期（__init__）早于 dialogue 设定活动角色
        （实测此时 `active_char` 仍是 ''），单靠 __init__ 回填不可靠。
        这里在**运行时注入完成**（config/dialogue 均已就绪）后再补一次，
        保证真正开工时上下文已在位。
        """
        try:
            from ..companion.conversation_history import (
                set_active_char as _set_hist_char,
                rehydrate as _rehydrate_ctx,
            )
            cid = self._active_char_id()
            # 先确认活动角色指针已指向该角色（重hydrate 幂等，内部会自动迁移+回填）；
            # 再用 shared_history 的实际长度作为返回值（更真实，避免返回误导性的 0）。
            _set_hist_char(cid)
            _rehydrate_ctx(cid)
            from ..companion.conversation_history import shared_history as _sh
            n = len(_sh().get_recent(999))
            if n:
                logger.info("[上下文持久化] 当前角色 %s 的上下文共 %d 轮已就位", cid, n)
            return n
        except Exception:  # noqa: BLE001
            logger.debug("[上下文持久化] 回填失败（忽略）", exc_info=True)
            return 0

    def _load_chat_history(self, char_id: str) -> None:
        """从 data/chatlog/<char>.jsonl 读回最近 N 条，回填 _chat_buffer。

        读回的历史置于「已在缓冲的实时消息」之前（历史更旧），且用 _next_chat_id
        重新编号，避免与后续新消息 id 冲突。即使首条实时消息先于首次轮询到达也
        能正确把历史拼在前面，不会漏加载。
        """
        if self._chatlog is None:
            return
        try:
            recs = self._chatlog.load_recent(char_id, 200)
        except Exception:  # noqa: BLE001
            logger.debug("[聊天历史] 读回失败", exc_info=True)
            return
        loaded = []
        for r in recs:
            if not isinstance(r, dict):
                continue
            r = dict(r)
            r["id"] = self._next_chat_id()
            loaded.append(r)
        if not loaded:
            return
        # 历史在前、实时消息在后。
        # 【2026-10-10 双保险】此前 `self._chat_buffer = deque(loaded + 旧)` 是**重新绑定**：
        # 若任何持有旧引用的一方（如 _safe_js_api 包装层）与总线处理方不同步，历史恢复
        # 后两边就会看到两个不同的缓冲。改为 deque.extendleft 原地插入（reversed 保证
        # 顺序：先插最旧的，最终历史整体在实时消息之前），不换对象、保持共享语义，
        # maxlen=200 自动淘汰最旧的多余条目。
        self._chat_buffer.extendleft(reversed(loaded))
        logger.info("[聊天历史] 已恢复 %d 条（角色=%s）", len(loaded), char_id)

    def get_chat_messages(self) -> dict:
        """返回聊天消息快照（GUI 轮询用）。"""
        # 首次轮询时回填历史（历史置于已在缓冲的实时消息之前，不会漏加载）
        if not self._chat_history_loaded:
            self._chat_history_loaded = True
            self._load_chat_history(self._active_char_id())
        try:
            self._attach_rel_panel()
        except Exception:  # noqa: BLE001  面板注入失败绝不能影响聊天刷新
            logger.debug("[关系] 注入聊天思考面板失败", exc_info=True)
        return {
            "success": True,
            "messages": list(self._chat_buffer),
            "active_name": self._dialogue.current_character_name if self._dialogue else "AI",
        }

    def _attach_rel_panel(self) -> None:
        """给「最后一条 AI 回复」挂一份关系快照 → 前端渲染可展开的「关系思考」面板。

        只在首次挂上（不覆盖），所以每条回复拿到的是**它自己那一轮**的判定。
        纯只读：数值仅供检视，绝不进对话提示词。
        """
        for m in reversed(list(self._chat_buffer)):
            if m.get("role") != "ai":
                continue
            if m.get("rel") or m.get("failed") or m.get("kind") == "status":
                return
            try:
                cid = str(getattr(self._cfg, "active_character_id", "") or "").strip()
            except Exception:  # noqa: BLE001
                cid = ""
            from ..companion.relationship import snapshot as _rel_snapshot
            snap = _rel_snapshot(cid or "default")
            if not snap.get("ok"):
                return
            # 判定是**异步**的（LLM 裁判要 2~5s）：只有"这一轮的判定已落"才挂；
            # 否则本轮先不挂，等下一次轮询再试——避免把上一轮的旧判定冻结到这条消息上。
            try:
                mts = float(m.get("ts") or 0.0)
                sts = float((snap.get("signals") or {}).get("ts") or 0.0)
            except Exception:  # noqa: BLE001
                mts = sts = 0.0
            if not sts:
                return
            if sts >= mts - 2.0:
                m["rel"] = snap
            return

    def get_text_chat_status(self) -> dict:
        """返回纯文本聊天端口可用性（前端据此锁/解锁聊天输入框）。

        判定依据：TextChatRouter.is_any_port_usable() —— 任一端口不在"失败冷却"中即
        可用；全部端口都进入了冷却（= 全部刚失败）才认为不可用，此时前端应锁死输入框。
        """
        router = getattr(self._dialogue, "_llm", None) if self._dialogue else None
        if router is None or not hasattr(router, "snapshot"):
            logger.info("[聊天状态] router=None或无snapshot → usable=False (引擎未就绪)")
            return {"success": True, "usable": False, "reason": "聊天引擎未就绪",
                    "ports": [], "last_used": "", "fallback": False}
        try:
            snap = router.snapshot()
        except Exception as e:  # noqa: BLE001
            logger.warning("读取聊天端口状态失败: %s", e)
            return {"success": True, "usable": False, "reason": str(e),
                    "ports": [], "last_used": "", "fallback": False}
        usable = bool(snap.get("usable", False))
        fallback = snap.get("fallback", False)
        mode = snap.get("mode", "?")
        entries = snap.get("entries", [])
        # 【2026-10-16·C8 修复】把"为什么不可用 / 还要等多久"一并交给前端。
        # 此前只回 usable=False，前端锁了输入框却不说明原因；用户看到的是"消息发出去
        # 没人回"，且失败文案还写成"网络超时…稍后再试"，与真实原因（端口冷却/Key 失效）
        # 对不上。这里按端口冷却剩余时间给出人话原因。
        _retry_after = 0.0
        for _p in (entries or []):
            try:
                _retry_after = max(_retry_after, float((_p or {}).get("cooldown_left") or 0))
            except Exception:  # noqa: BLE001
                pass
        _reason = ""
        if not usable:
            if str(mode) == "manual":
                _mp = snap.get("manual_name") or snap.get("manual_provider") or "当前固定端口"
                _reason = f"固定端口「{_mp}」正在冷却（上次调用失败）"
            else:
                _reason = "所有聊天端口都在冷却中（刚失败过）"
            if _retry_after > 0:
                _reason += f"，约 {int(_retry_after) + 1} 秒后自动重试"
            _last_err = next((str((p or {}).get("last_error") or "") for p in (entries or [])
                              if str((p or {}).get("last_error") or "").strip()), "")
            if _last_err:
                _reason += f"。上次错误：{_last_err[:120]}"
            _reason += "。可在「设置 → 模型与 API」换一个端口，或改用自动模式。"
        # 诊断：每次前端轮询都记录，定位"打字发不出"时 usable 到底是什么值
        logger.info("[聊天状态] usable=%s fallback=%s mode=%s ports=%d",
                    usable, fallback, mode, len(entries))
        return {
            "success": True,
            "usable": usable,
            "reason": _reason,
            "retry_after_s": round(_retry_after, 1),
            "ports": snap.get("entries", []),
            "last_used": snap.get("last_used", ""),
            "fallback": fallback,
            "mode": mode,
            "manual_provider": snap.get("manual_provider", ""),
            "manual_name": snap.get("manual_name", ""),
            "selectable": snap.get("selectable", []),
        }

    def _sync_chat_router_duplex(self) -> None:
        """按当前双工模式同步聊天路由的 skip_local 标志（2026-09-03 修复打字锁死）。

        双工 local_omni/local 占用整张 GPU → 聊天路由跳过本地 ollama 端口，聊天只走
        云端端口（不占本地 GPU、不会被 Comni 抢卡超时打进冷却）；双工云端/关闭(Comni
        卸载) → 恢复本地端口作兜底。改的是 send_chat 用的 TextChatRouter 实例标志，
        直接解除"双工开口→聊天端口冷却→输入框锁死"的链路（症状A）。
        """
        pro = getattr(self, "_proactive", None)
        mode = getattr(pro, "_duplex_mode", None) if pro is not None else None
        if mode is None:
            raw = getattr(self._cfg.ns, "proactive", None) if self._cfg else None
            mode = getattr(raw, "duplex_mode", None) if raw else None
        skip = str(mode) in ("local", "local_omni")
        router = getattr(self._dialogue, "_llm", None) if self._dialogue else None
        if router is not None and hasattr(router, "set_skip_local_when_duplex"):
            try:
                router.set_skip_local_when_duplex(skip)
            except Exception:  # noqa: BLE001
                logger.debug("同步聊天路由 skip_local 失败", exc_info=True)

    def set_text_chat_mode(self, mode: str, manual_provider: Optional[str] = None) -> dict:
        """热切换聊天模型模式（自动选可用端口 / 手动固定端口），立即生效 + 写盘。

        - auto：自动按冷却顺序试可用端口，失败兜底回网关。
        - manual：严格只用 manual_provider 指定的那一个端口（"改什么是什么"，
          不兜底、不串其它端口）；manual_provider 为空时沿用当前设置。
        """
        router = getattr(self._dialogue, "_llm", None) if self._dialogue else None
        if router is None or not hasattr(router, "set_mode"):
            return {"success": False, "error": "聊天引擎未就绪"}
        m = (str(mode or "").strip().lower()) or "auto"
        if m not in ("auto", "manual"):
            return {"success": False, "error": "mode 仅支持 auto / manual"}
        mp = manual_provider if manual_provider is not None else ""
        try:
            router.set_mode(m, mp or None)
        except Exception as e:  # noqa: BLE001
            logger.warning("设置聊天模型模式失败: %s", e)
            return {"success": False, "error": str(e)}
        # 持久化到 config.yaml（text_chat.mode / text_chat.manual_provider）
        try:
            raw = self._cfg.raw
            tc = raw.get("text_chat")
            if not isinstance(tc, dict):
                tc = {}
                raw["text_chat"] = tc
            tc["mode"] = m
            if m == "manual":
                # 手动模式必须指定一个存在的端口；为空则取第一个可选端口兜底。
                sel = router.snapshot().get("selectable", [])
                if not mp and sel:
                    mp = sel[0]["id"]
                if not mp:
                    return {"success": False, "error": "手动模式需指定一个聊天端口"}
                tc["manual_provider"] = mp
                router.set_mode(m, mp)  # 写入后再确认一遍
            self._save_config()
        except Exception as e:  # noqa: BLE001
            logger.warning("保存聊天模型模式到配置失败: %s", e)
            return {"success": False, "error": "已切换但配置保存失败：" + str(e)}
        snap = router.snapshot()
        return {
            "success": True,
            "mode": snap.get("mode", m),
            "manual_provider": snap.get("manual_provider", ""),
            "manual_name": snap.get("manual_name", ""),
        }

    # ---- 聊天模型快捷切换（聊天页下拉切换器后端） ----
    # 百炼免费额度文本/角色扮演模型默认种子：仅为 UI 便利，不锁死任何模型；
    # 真实来源是 config.text_chat.chat_presets.bailian_free（用户可自由增删）。
    _BAILIAN_FREE_MODELS_DEFAULT = [
        {"id": "qwen3.8-max", "name": "Qwen3.8-Max（旗舰·支持工具调用）"},
        {"id": "qwen3.8-flash", "name": "Qwen3.8-Flash（快省·支持工具调用）"},
        {"id": "qwen3.7-plus", "name": "Qwen3.7-Plus（均衡·有免费额度）"},
        {"id": "qwen-plus-character", "name": "Qwen-Plus-Character（角色扮演·拟人·免费）"},
        {"id": "qwen-flash-character", "name": "Qwen-Flash-Character（角色扮演·最省）"},
        {"id": "qwen-plus", "name": "Qwen-Plus（基础·稳·免费额度）"},
        {"id": "qwen3.6-plus", "name": "Qwen3.6-Plus（备选·免费额度）"},
    ]

    def get_chat_model_presets(self) -> dict:
        """聊天页模型切换器的快捷列表。

        返回：
          bailian_free        —— 百炼免费额度文本/角色扮演模型（config.text_chat.chat_presets.
                                bailian_free 优先，缺失则用内置默认种子，仅供 UI 便利，不锁死）。
          bailian_provider_ids —— 当前 text_chat.providers 中 base_url 含 dashscope 的端口 id，
                                前端据此知道"点百炼免费模型时改哪个 provider 的 model"。
        """
        raw = self._cfg.raw if self._cfg else {}
        tc = raw.get("text_chat") or {}
        presets_raw = ((tc.get("chat_presets") or {}).get("bailian_free")) or list(self._BAILIAN_FREE_MODELS_DEFAULT)
        # 【2026-10-02】去重：剔除与 providers 里已固定绑定的模型重复的项。
        # 前端有两个区块——「文本推理模型」列 providers、「百炼免费模型」列 bailian_free，
        # 同一 model 落在两边就会被渲染成两行（用户报"为什么有两行"）。
        # 正解是配置层不重复；这里再加一道保险，保证用户手改配置加了重复项时也不重复显示。
        try:
            bound = set()
            for _p in (tc.get("providers") or []):
                _m = (_p.get("model") or "").strip()
                if _m:
                    bound.add(_m)
            if bound:
                _seen = set()
                _dedup = []
                for _p in presets_raw:
                    _mid = str((_p or {}).get("id") or "").strip()
                    if not _mid or _mid in bound or _mid in _seen:
                        continue
                    _seen.add(_mid)
                    _dedup.append(_p)
                if len(_dedup) != len(presets_raw):
                    logger.info(
                        "[聊天模型预设] 去重：%d → %d（与 providers 已绑定的 model 重复：%s）",
                        len(presets_raw), len(_dedup),
                        sorted(bound & {str((x or {}).get("id")) for x in presets_raw}),
                    )
                presets_raw = _dedup
        except Exception:  # noqa: BLE001
            logger.debug("[聊天模型预设] 去重失败，保持原样", exc_info=True)
        # 给每个预设模型附带推断能力（caps），供选择器悬浮卡直接渲染
        try:
            from ..llm import model_caps as mc
            presets = [mc.merge_caps_into(p) for p in presets_raw]
        except Exception:  # noqa: BLE001
            presets = list(presets_raw)
        bailian_ids = []
        for p in (tc.get("providers") or []):
            bu = (p.get("base_url") or "").lower()
            if "dashscope" in bu:
                bailian_ids.append(p.get("id"))
        return {"success": True, "bailian_provider_ids": bailian_ids, "bailian_free": presets}

    def set_chat_provider_model(self, provider_id: str, model: str) -> dict:
        """改某聊天端口的 model 并热切换为 manual 模式（聊天页点百炼免费模型时调用）。

        仅改该 provider 的 model 字段，base_url / api_key 保持不变（同一把 key 复用），
        model 可为任意字符串 —— 不锁死，用户可填任何百炼/其它模型。立即生效 + 写盘。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        provider_id = (provider_id or "").strip()
        model = (model or "").strip()
        if not provider_id or not model:
            return {"success": False, "error": "provider_id 与 model 均必填"}
        try:
            raw = self._cfg.raw
            tc = raw.setdefault("text_chat", {})
            providers = tc.get("providers") or []
            target = next((x for x in providers if x.get("id") == provider_id), None)
            if target is None:
                return {"success": False, "error": f"聊天端口不存在: {provider_id}"}
            target["model"] = model
            tc["providers"] = providers
            self._save_config()
            from ..config import _to_ns
            self._cfg.ns = _to_ns(raw)
            self._apply_llm_live()  # 阻塞直到新路由替换完成
            # 切 manual 模式，严格只用这个端口（改什么是什么），pin 到刚改的模型
            router = getattr(self._dialogue, "_llm", None) if self._dialogue else None
            if router is not None and hasattr(router, "set_mode"):
                router.set_mode("manual", provider_id)
            tc["mode"] = "manual"
            tc["manual_provider"] = provider_id
            self._save_config()
            snap = router.snapshot() if (router is not None and hasattr(router, "snapshot")) else {}
            return {
                "success": True,
                "provider_id": provider_id,
                "model": model,
                "mode": snap.get("mode", "manual"),
                "manual_provider": snap.get("manual_provider", provider_id),
                "manual_name": snap.get("manual_name", ""),
            }
        except Exception as e:  # noqa: BLE001
            logger.exception("设置聊天端口模型失败")
            return {"success": False, "error": str(e)}

    def get_model_caps(self) -> dict:
        """返回所有模型的推断/手填能力，供聊天选择器悬浮卡与设置页能力编辑使用。

        聚合三类数据源（全部带 caps 推断，用户手填的 caps 优先且 verified=True）：
          providers —— text_chat.providers（纯文本/角色扮演/通用聊天端口）
          presets  —— text_chat.chat_presets（如 bailian_free 快捷列表）
          vision   —— vision_models.models（视觉/看图/看视频端口）
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            from ..llm import model_caps as mc
            raw = self._cfg.raw
            tc = raw.get("text_chat") or {}
            vm = raw.get("vision_models") or {}

            providers = mc.caps_for_list(tc.get("providers") or [])
            presets_out = {}
            for pk, pv in (tc.get("chat_presets") or {}).items():
                presets_out[pk] = mc.caps_for_list(pv or [])
            vision = mc.caps_for_list(vm.get("models") or [])

            return {
                "success": True,
                "providers": providers,
                "presets": presets_out,
                "vision": vision,
                "meta": mc._cap_meta(),
            }
        except Exception as e:  # noqa: BLE001
            logger.exception("获取模型能力失败")
            return {"success": False, "error": str(e)}

    def probe_model_caps(self, provider_id: str = "", config: Optional[dict] = None) -> dict:
        """对填了 API 的模型真实探测能力（发请求试图片/音频是否被接受）。

        provider_id：在 text_chat.providers / vision_models.models 里查；找不到则用 config 内联。
        config：可选 {api_key, base_url, model} 直接给，优先级高于 provider_id 查到的。
        返回 {success, caps|error}，caps 含 source="实测" 与 image_probed 字段。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            from ..llm import model_caps as mc
            raw = self._cfg.raw
            tc = raw.get("text_chat") or {}
            vm = raw.get("vision_models") or {}

            ak = burl = model = None
            if isinstance(config, dict):
                ak = config.get("api_key") or None
                burl = config.get("base_url") or None
                model = config.get("model") or None
            # 优先用内联；否则按 id 在 providers / vision 里查
            if not (ak and burl and model):
                cand = None
                for p in (tc.get("providers") or []):
                    if p.get("id") == provider_id:
                        cand = p
                        break
                if cand is None:
                    for p in (vm.get("models") or []):
                        if p.get("id") == provider_id:
                            cand = p
                            break
                if cand is not None:
                    ak = ak or cand.get("api_key") or None
                    burl = burl or cand.get("base_url") or None
                    model = model or cand.get("model") or None
            if not (ak and burl and model):
                return {"success": False, "error": "缺少 api_key / base_url / model，无法探测"}
            caps = mc.probe_capabilities(ak, burl, model, timeout_s=20)
            if caps is None:
                return {"success": False, "error": "探测失败（鉴权/网络/不支持 OpenAI 兼容接口）"}
            return {"success": True, "caps": caps}
        except Exception as e:  # noqa: BLE001
            logger.exception("探测模型能力失败")
            return {"success": False, "error": str(e)}

    def send_chat(self, text: str, images: Optional[list] = None) -> dict:
        """从 GUI 聊天输入框发送消息给 AI。

        images：可选，前端传来的图片（base64 data URL 或纯 base64 字符串列表）。
        存在图片时走视觉模型（自动适配器）；纯文本仍走文本模型。
        """
        _t0 = time.time()
        text = (text or "").strip()
        _images = list(images or [])
        _res = {"success": False}
        try:
            if not text and not _images:
                _res = {"success": False, "error": "消息不能为空"}
                return _res
            if self._bus is None or self._loop is None:
                _res = {"success": False, "error": "运行时未就绪"}
                return _res
            # 双保险：所有聊天端口都挂了则不允许发送（前端输入框本就锁定，这里再挡一层）。
            # 但发图走视觉模型，不卡在文本端口锁上（视觉有自己的可用性判断与兜底）。
            router = getattr(self._dialogue, "_llm", None) if self._dialogue else None
            if not _images and router is not None and hasattr(router, "is_any_port_usable") \
                    and not router.is_any_port_usable():
                # 【2026-10-16·C8 修复】锁定时带上"为什么锁 + 还要等多久 + 上次错误"，
                # 前端据此回滚乐观气泡、把原话还回输入框并显示倒计时。
                # 旧实现只回一句"所有聊天端口暂不可用，输入框已锁定"：用户既不知道原因，
                # 也看不到时长，只看到"消息发出去没人回"（实测端口冷却时消息被静默丢弃）。
                _st = self.get_text_chat_status()
                _res = {"success": False,
                        "error": _st.get("reason") or "所有聊天端口暂不可用，请稍后重试",
                        "locked": True,
                        "retry_after_s": _st.get("retry_after_s", 0)}
                return _res

            # —— 发送诊断日志：记录调用上下文，重启后发一条消息即可定位 ——
            # ⚠️【2026-10-04 20:05 补】原先只打 `text_len`，**不打图片数量** ⇒
            # 「发图没反应」时无法区分"前端没传图"与"后端丢了图"，排查只能靠猜
            # （20:03 实测：text_len=13 但走了 `[桌面媒体]` 分支 ⇒ 图根本没到
            #   `_process_desktop_message`，而日志一个字都没提）。
            # 现在同时打 images 数与首图长度，定位一眼到底。
            logger.info(
                "[发送诊断] send_chat: text_len=%d images=%d%s bus_ok=%s loop_ok=%s router=%s",
                len(text), len(_images),
                (" first_len=%d" % len(_images[0])) if _images and _images[0] else "",
                self._bus is not None, self._loop is not None,
                type(router).__name__ if router is not None else "None")
            if text and not _images:
                # 纯文本消息却走了发图分支的典型症状（19:57/20:03 实测）——留痕便于追溯
                logger.debug("[发送诊断] 本条为纯文本（无 images）")

            async def _send() -> None:
                # 【2026-10-18 修复·新消息串改旧气泡】
                # 用户发新消息 = 上一轮回复彻底结束。这里强制清空流式状态，
                # 否则若上一轮 `_chat_stream_id` 因任何原因没被收尾（双工串流、
                # 异常中断、decision 丢失），新回复会继续往**旧气泡**追加文本
                # （用户反馈："我打新东西它还继续修改老气泡"）。
                self._chat_stream_id = None
                self._chat_seg_count = 0
                # 【2026-10-18 修复·打字时双工对键盘声开口】
                # 通知双工：用户正在打字 → 3 秒内抬高其本地 VAD 起音阈值，
                # 键盘声/呼吸不再被当成"开始说话"而触发云端用达妮娅音色回一句。
                try:
                    _pro = getattr(self, "_proactive", None)
                    if _pro is not None and hasattr(_pro, "_note_user_typing"):
                        _pro._note_user_typing()
                    # 双工实例自己也留一份（VAD 在 cloud_omni 里读这个字段）
                    _cli = getattr(_pro, "_duplex_client", None) if _pro is not None else None
                    if _cli is not None:
                        _cli._user_typing_ts = time.time()
                except Exception:  # noqa: BLE001
                    pass
                await self._bus.publish(Event(EventType.USER_TEXT, {
                    "text": text, "source": "webui",
                    "images": _images,
                }))

            # 缩短阻塞超时到 3s：发布卡住时尽快返回，避免 GUI 线程长时间冻结把消息泵拖死
            self._run_coro(_send(), timeout=3.0)
            _res = {"success": True}
        except Exception as _e:  # noqa: BLE001
            logger.exception("[发送诊断] send_chat 异常")
            _res = {"success": False, "error": str(_e)}
        finally:
            self._last_send_chat = {
                "ts": _t0,
                "ms": int((time.time() - _t0) * 1000),
                "ok": _res.get("success"),
                "locked": bool(_res.get("locked", False)),
                "text_len": len(text),
                "err": _res.get("error"),
                "note": "",
            }
        return _res

    def transcribe_and_chat(self, audio_b64: str, mime: str = "pcm") -> dict:
        """语音模式闭环第一步：浏览器麦克风 PCM → 本地 sherpa 转写 → 当普通语音消息驱动聊天。

        转写出的文字会以 source='voice' 发布 USER_TEXT，走与打字完全相同的
        dialogue.respond() → 文本 LLM → TTS 念回（含 audio_url）链路，故下游
        LLM/TTS/历史/失败处理全部复用，不另建链路。前端随后由 pollChat 拉取 AI 回复并自动播放。
        """
        try:
            import base64 as _b64

            pcm = _b64.b64decode(audio_b64 or "")
            if len(pcm) < 1000:
                return {"success": True, "text": "", "empty": True}
            # 懒导入：仅在首次语音消息时加载 sherpa（避免拖慢启动）
            from ..voice.stt import transcribe_pcm

            text = (transcribe_pcm(pcm) or "").strip()
            if not text:
                return {"success": True, "text": "", "empty": True}

            if self._bus is None or self._loop is None:
                return {"success": False, "error": "运行时未就绪"}

            async def _push() -> None:
                await self._bus.publish(Event(EventType.USER_TEXT, {
                    "text": text, "source": "voice",
                }))

            self._run_coro(_push())
            return {"success": True, "text": text}
        except Exception as e:  # noqa: BLE001
            logger.exception("语音转写 + 对话失败")
            return {"success": False, "error": str(e)}

    def clear_chat(self) -> dict:
        """清空聊天记录。"""
        self._chat_buffer.clear()
        self._chat_history_loaded = False
        # 清空聊天要连落盘一起清，否则重启后又会把旧历史读回来
        if self._chatlog is not None:
            try:
                self._chatlog.clear(self._active_char_id())
            except Exception:  # noqa: BLE001
                pass
        self._chat_seq = 0
        # 【2026-10-11】清空聊天 = **也要清模型的短期上下文**。
        #   此前只清了 UI 缓冲与 chatlog，但 `shared_history`（喂模型那份）纹丝不动
        #   ⇒ 界面看着干净了，她却还"记得"刚才那些话（且落盘上下文里也还在，
        #      下次启动又会被回填 ⇒ 删不干净）。这里连同她那份一起清。
        try:
            from ..companion.conversation_history import shared_history as _sh
            _sh().clear()
        except Exception:  # noqa: BLE001
            logger.debug("[清空聊天] 清理模型上下文失败（忽略）", exc_info=True)
        if self._chat_status_drop_timer is not None:
            try:
                self._chat_status_drop_timer.cancel()
            except Exception:  # noqa: BLE001
                pass
            self._chat_status_drop_timer = None
        self._chat_status_id = None
        self._chat_stream_id = None
        # 【2026-10-05】缓冲已 clear，指针也已置空，这里无需再走 drop。
        self._chat_keep_done = True
        return {"success": True}

    # ======== 持续对话开关（语音免唤醒持续识别） ========
    def get_voice_settings(self) -> dict:
        """返回语音开关状态：持续对话（免唤醒）+ 麦克风监听总开关。

        与"实际是否采集"严格一致：ASR 存在时返回运行时状态（is_listening/
        is_continuous）；ASR 未启用（mic_enabled=false 未创建）时返回配置状态，
        listening 即 false（不采集），保证 UI 显示的开关永远反映真实行为。
        """
        asr = self._asr
        if asr is None:
            mic = False
            if self._cfg is not None:
                mic = bool((self._cfg.raw.get("voice") or {}).get("mic_enabled", False))
            return {"success": True, "continuous": False, "listening": mic}
        return {"success": True,
                "continuous": bool(asr.is_continuous()),
                "listening": bool(asr.is_listening())}

    def set_continuous_voice(self, enabled: bool) -> dict:
        """热切换语音持续识别（免唤醒）：运行时立即生效 + 写入配置文件。

        开启后不用喊"达妮娅"，说话即识别、随时对话（方便用户随时纠错 AI 说错的话）；
        关闭回到唤醒词模式。
        """
        asr = self._asr
        if asr is None:
            return {"success": False, "error": "语音模块未注入"}
        asr.set_continuous(bool(enabled))
        if self._cfg is not None:
            raw = self._cfg.raw
            if not isinstance(raw.get("voice"), dict):
                raw["voice"] = {}
            raw["voice"]["continuous_voice"] = bool(enabled)
            try:
                self._save_config()
            except Exception as e:  # noqa: BLE001
                logger.warning("写入配置失败（运行时开关已生效）: %s", e)
        return {"success": True, "enabled": bool(enabled)}

    def set_mic_listening(self, enabled: bool) -> dict:
        """麦克风监听总开关：关闭后释放麦克风设备（完全停止采集，唤醒词/持续识别
        全停），开启恢复（持续对话关=唤醒词模式）。运行时立即生效 + 写入配置文件。"""
        asr = self._asr
        if asr is None:
            return {"success": False, "error": "语音模块未注入"}
        asr.set_listening(bool(enabled))
        if self._cfg is not None:
            raw = self._cfg.raw
            if not isinstance(raw.get("voice"), dict):
                raw["voice"] = {}
            raw["voice"]["mic_enabled"] = bool(enabled)
            try:
                self._save_config()
            except Exception as e:  # noqa: BLE001
                logger.warning("写入配置失败（运行时开关已生效）: %s", e)
        return {"success": True, "enabled": bool(enabled)}

    # ======== 桌面陪伴语音（双工/快照开口是否接 TTS） ========
    def get_desktop_tts_enabled(self) -> dict:
        """返回桌面陪伴语音开关（config.yaml[proactive][desktop_tts_enabled]）。

        True=桌面双工/快照开口接 TTS 发声；False/缺省=桌面静默只飘字。
        通话/视频/语音恒定发声，不受此开关影响。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        pro = (self._cfg.raw.get("proactive", {}) or {}) if self._cfg else {}
        # 缺省/False 均解读为静默（桌面只飘字）；仅用户显式开启才接 TTS。config.yaml 初值 false。
        return {"success": True, "enabled": bool(pro.get("desktop_tts_enabled", False))}

    def set_desktop_tts_enabled(self, enabled: bool) -> dict:
        """桌面陪伴语音开关热切换：写回 config.yaml[proactive][desktop_tts_enabled] 并保存。

        下一句桌面开口即时生效（tts.is_desktop_silent 实时读 raw），无需重启托盘。
        通话态由事件 desktop_companion 标记强制发声，本开关只管桌面互动。
        """
        if self._cfg is None:
            return {"success": False, "error": "配置未加载"}
        raw = self._cfg.raw
        if not isinstance(raw.get("proactive"), dict):
            raw["proactive"] = {}
        raw["proactive"]["desktop_tts_enabled"] = bool(enabled)
        try:
            self._save_config()
        except Exception as e:  # noqa: BLE001
            logger.warning("写入桌面陪伴语音配置失败（运行时已生效）: %s", e)
        return {"success": True, "enabled": bool(enabled)}

    # ======== 全局语音模式（三档：无声音 / 微软小薇 / 全克隆） ========
    def get_voice_mode(self) -> dict:
        """返回全局语音模式（设计稿语音页 M0/M1/M2 的真实后端态）。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        v = self._cfg.raw.get("voice", {}) or {}
        mode = (v.get("mode") or "clone").strip().lower()
        clone = v.get("clone", {}) or {}
        # 【2026-10-16 修复·"未绑定却看不出未绑定"】
        # 旧返回里只有 enabled=(没静音)，于是**完全没绑定任何音色**时也返回 enabled:true、
        # 还报 local_engine=voxcpm —— 界面上完全看不出"还没配声音"，用户以为已经可用。
        # 这里补三个字段说明真相（bound/bound_kind/unbound_reason），**不改 enabled 语义**，
        # 以免影响既有消费方（voice_tier.js / 设置页）造成回归。
        bound = False
        bound_kind = ""
        try:
            _sub = str(clone.get("sub") or "").strip()
            _cloud = clone.get("cloud", {}) or {}
            _cs = _cloud.get("dashscope_cosy") or {}
            if _sub == "cloud":
                # 【2026-10-16 收紧】云端档必须有 Key **且**有音色/模型才算绑上——
                # 只有 sub='cloud' 而无凭证等于没绑（会表现为"选了云端但一句也出不来"）。
                if str(_cs.get("api_key") or "").strip() and \
                        (str(_cs.get("voice_id") or _cs.get("voice") or "").strip()
                         or str(_cs.get("model_name") or "").strip()):
                    bound, bound_kind = True, "cloud"
            elif _sub in ("local", "gpt-sovits", "voxcpm", "qwen3tts", "cosyvoice", "mimo_tts"):
                # 【2026-10-16 收紧】本地档不能只因 sub='local'（**那只是默认值**）就算绑上。
                # 注意：不能用 clone.local_engine 判——它在本项目配置里**历来就有默认值 voxcpm**，
                # 拿它当依据会把"从没绑过"的配置误判成已绑定（实测 case(b)）。
                # 本地克隆类引擎（gpt-sovits / voxcpm / qwen3tts / cosyvoice）都需要参考音频，
                # 故统一要求 reference_audio 非空才算"已绑定"。
                if str(clone.get("reference_audio") or "").strip():
                    bound, bound_kind = True, "local"
            # 角色级绑定优先（角色自己的 engine+voice 也算"已绑定"）
            _aid = str(self._cfg.raw.get("active_character_id") or "")
            for _c in (self._cfg.raw.get("characters") or []):
                if isinstance(_c, dict) and str(_c.get("id") or "") == _aid:
                    _cv = _c.get("voice") or {}
                    if isinstance(_cv, dict) and str(_cv.get("engine") or "").strip() and \
                            str(_cv.get("voice") or _cv.get("voice_id") or "").strip():
                        bound, bound_kind = True, str(_cv.get("engine") or "")
                    # 【2026-10-17 补·"音色档"路径（界面真实路径）】
                    # redesign 语音页的用户操作是「新建音色档 → 绑定到角色」，
                    # 落盘为 characters[].voice_profile = <profile id>，凭证在 voice_profiles 里。
                    # 若只看角色 voice 块，会把"已经在界面上绑好音色"的用户误判为未绑定
                    # → 顶部"未绑定"提示条一直挂着（本轮补验时实测到）。
                    if not bound:
                        _pid = str(_c.get("voice_profile") or "").strip()
                        if _pid:
                            _pf = next((p for p in (self._cfg.raw.get("voice_profiles") or [])
                                        if isinstance(p, dict) and str(p.get("id") or "") == _pid), None)
                            if isinstance(_pf, dict):
                                _eng = str(_pf.get("engine") or "").strip()
                                _has_cred = bool(str(_pf.get("api_key") or "").strip()) and bool(
                                    str(_pf.get("voice_id") or _pf.get("voice")
                                        or _pf.get("model_name") or "").strip())
                                _has_ref = bool(str(_pf.get("reference_audio") or "").strip())
                                if _eng and (_has_cred or _has_ref):
                                    bound = True
                                    bound_kind = "cloud" if _eng in (
                                        "dashscope_cosy", "dashscope_cosyvoice", "bailian",
                                        "minimax", "openai_tts", "fish_audio") else _eng
                    break
        except Exception:  # noqa: BLE001
            logger.debug("语音绑定状态判定失败（按未绑定处理）", exc_info=True)
        return {
            "success": True,
            "mode": mode,
            "clone_sub": clone.get("sub", "local"),
            "clone_cloud": clone.get("cloud", {}) or {},
            "local_engine": clone.get("local_engine") or v.get("clone_local_engine") or "qwen3tts",
            "muted": bool(v.get("muted", mode == "mute")),
            "enabled": not bool(v.get("muted", mode == "mute")),
            "bound": bound,
            "bound_kind": bound_kind,
            "unbound_reason": "" if bound else
            "未绑定音色：请到「语音 · 音色」绑定你的服务商（填你自己的 Key），并选用该账号里的音色 ID",
        }

    def set_voice_mode(self, mode: str, cloud: dict = None) -> dict:
        """全局语音模式三档：mute(无声音) / edge(微软小薇 EdgeTTS) / clone(全克隆达妮娅本音)。

        持久化到 config.yaml[voice][mode] 并即时应用到当前角色音色引擎；mute 同时关闭麦克风监听。
        clone 子档：local=本地克隆引擎(qwen3tts/voxcpm)，cloud=云端克隆(传 cloud 表单)。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        mode = (mode or "clone").strip().lower()
        if mode not in ("mute", "edge", "clone"):
            return {"success": False, "error": f"未知语音模式: {mode}"}
        try:
            raw = self._cfg.raw
            if not isinstance(raw.get("voice"), dict):
                raw["voice"] = {}
            v = raw["voice"]
            v["mode"] = mode
            v["muted"] = (mode == "mute")

            active_id = self._cfg.active_character_id
            chars = raw.get("characters", []) or []
            target = None
            for c in chars:
                if c.get("id") == active_id:
                    target = c
                    break
            if target is None and chars:
                target = chars[0]

            if mode == "edge":
                if isinstance(target, dict):
                    vc = target.setdefault("voice", {})
                    vc["engine"] = "edge-tts"
                    vc["voice"] = "zh-CN-XiaoyiNeural"  # 微软小薇
            elif mode == "clone":
                cloud = cloud or {}
                sub = (cloud.get("sub") or "local")
                v.setdefault("clone", {})["sub"] = sub
                if sub == "cloud":
                    _in_prov = (cloud.get("provider") or "").strip()
                    if _in_prov:
                        # 【2026-10-09 多服务商并存】单 provider 写入对应分桶，保留其它服务商。
                        from ..models.cloud_tts import merge_cloud_tts_provider
                        merge_cloud_tts_provider(
                            raw, _in_prov,
                            api_key=(cloud.get("api_key") or "").strip(),
                            group_id=(cloud.get("group_id") or "").strip(),
                            model_name=(cloud.get("model_name") or "").strip(),
                            api_url=(cloud.get("api_url") or "").strip())
                    if isinstance(target, dict):
                        vc = target.setdefault("voice", {})
                        vc["engine"] = (_in_prov or "gpt-sovits")
                else:
                    local_engine = v.get("clone_local_engine") or "qwen3tts"
                    v["clone"]["local_engine"] = local_engine
                    if isinstance(target, dict):
                        vc = target.setdefault("voice", {})
                        vc["engine"] = local_engine
            # mute：关闭麦克风监听（无输入）；输出静音由 TTS 层读取 voice.muted 决定
            if mode == "mute" and self._asr is not None:
                try:
                    self._asr.set_listening(False)
                except Exception:  # noqa: BLE001
                    pass
                v["mic_enabled"] = False
                # 无声模式：立即卸载已在运行的本地克隆 TTS 服务（释放显存/内存），
                # 即便本机有对应模型也不再常驻——"选了无声就不加载/自动卸载"的核心。
                _unload = getattr(self, "_unload_tts", None)
                if _unload is not None:
                    try:
                        _unload()
                        logger.info("无声模式: 已请求卸载本地克隆 TTS 服务")
                    except Exception:  # noqa: BLE001
                        logger.warning("无声模式卸载本地 TTS 服务失败", exc_info=True)

            # 【2026-10-08 P1·key_unify】本条路径此前只写 voice.clone.cloud.api_key 一格。
            # 用户在此填的百炼 Key 会与引导页/设置页其它处并存 → 同账号多处不同 Key。
            if mode == "clone":
                from ..models.cloud_tts import KNOWN as _CKNOWN2
                _cc = (v.get("clone", {}) or {}).get("cloud", {}) or {}
                _vk = ""
                if isinstance(_cc, dict):
                    if any(k in _CKNOWN2 for k in _cc.keys()):  # 多 provider 分桶 → 遍历找百炼 Key
                        for _pb in _cc.values():
                            _k = str((_pb or {}).get("api_key") or "").strip()
                            if _looks_like_bailian_key(_k):
                                _vk = _k; break
                    else:
                        _vk = str(_cc.get("api_key") or "").strip()
                if _looks_like_bailian_key(_vk):
                    _unify_bailian_key(raw, _vk, source="set_voice_mode")
            # 【2026-10-08 P3·写入收口】抹空白 + Key 收敛 + 写后自检（永不抛异常）
            try:
                from ..models import commit_model_write as _cmw3
                _cmw3(raw, changed="voice.clone.cloud", source="set_voice_mode")
            except Exception:  # noqa: BLE001
                logger.warning("写入收口失败（不影响保存）", exc_info=True)

            self._save_config()
            from ..config import _to_ns
            self._cfg.ns = _to_ns(raw)
            # 运行时重新应用当前角色音色（重切同一角色以刷新 voice 引擎）
            if self._dialogue is not None and self._loop is not None and active_id:
                try:
                    self._run_coro(self._dialogue.switch_character(active_id))
                except Exception:  # noqa: BLE001
                    logger.warning("重切角色应用音色失败(配置已保存)")
            return {"success": True, "mode": mode}
        except Exception as e:  # noqa: BLE001
            logger.exception("保存语音模式失败")
            return {"success": False, "error": f"写入配置失败: {e}"}

    def set_barge_sensitivity(self, level: str) -> dict:
        """打断灵敏度 低/中/高 → 同时写本地双工与云端双工两套参数。

        - 本地双工（通话档）：force_listen_count / asr_trailing_silence / listen_prob_scale
        - 云端双工（Qwen-Omni-Realtime）：vad_threshold / vad_silence_ms
        两套参数互不冲突（本地参数只被本地双工读取，云端参数只被云端双工读取），
        故始终两套都写，保证切换引擎后当前档位即用正确值。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        level = (level or "mid").strip().lower()
        MAP = {
            "low":  {"flc": 5, "trail": 0.6, "lps": 1.0, "thr": 0.8, "sil": 1000},
            "mid":  {"flc": 3, "trail": 0.45, "lps": 1.0, "thr": 0.5, "sil": 600},
            "high": {"flc": 1, "trail": 0.2, "lps": 0.3, "thr": 0.2, "sil": 300},
        }
        if level not in MAP:
            return {"success": False, "error": f"未知灵敏度: {level}"}
        try:
            m = MAP[level]
            raw = self._cfg.raw
            pro = raw.setdefault("proactive", {})
            pro["duplex_call_force_listen_count"] = m["flc"]
            pro["duplex_call_asr_trailing_silence"] = m["trail"]
            pro["duplex_call_listen_prob_scale"] = m["lps"]
            pro["duplex_call_barge_in"] = True
            co = raw.setdefault("cloud_omni", {})
            co["vad_threshold"] = m["thr"]
            co["vad_silence_ms"] = m["sil"]
            self._save_config()
            from ..config import _to_ns
            self._cfg.ns = _to_ns(raw)
            logger.info("打断灵敏度已设为 %s（本地 flc=%s trail=%s lps=%s / 云端 thr=%s sil=%sms）",
                         level, m["flc"], m["trail"], m["lps"], m["thr"], m["sil"])
            return {"success": True, "level": level}
        except Exception as e:  # noqa: BLE001
            logger.exception("保存打断灵敏度失败")
            return {"success": False, "error": f"写入配置失败: {e}"}

    def set_clone_engine(self, engine: str) -> dict:
        """设置本地克隆引擎（qwen3tts / voxcpm / cosyvoice）并即时应用到当前角色。

        本地千问(qwen3tts) 为默认；切换后重切当前角色以刷新 voice 引擎。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        engine = (engine or "qwen3tts").strip().lower()
        if engine not in ("qwen3tts", "voxcpm", "cosyvoice", "mimo_tts", "gpt_sovits"):
            return {"success": False, "error": f"未知本地克隆引擎: {engine}"}
        try:
            raw = self._cfg.raw
            old_engine = str((raw.get("voice", {}) or {}).get("clone_local_engine")
                             or "qwen3tts").strip().lower()
            raw.setdefault("voice", {})
            raw["voice"]["clone_local_engine"] = engine
            # 若当前为本地克隆档，同步写进 clone.local_engine 以便 set_voice_mode 读取
            raw["voice"].setdefault("clone", {})["local_engine"] = engine
            self._save_config()
            from ..config import _to_ns
            self._cfg.ns = _to_ns(raw)
            active_id = self._cfg.active_character_id
            if self._dialogue is not None and self._loop is not None and active_id:
                try:
                    self._run_coro(self._dialogue.switch_character(active_id))
                except Exception:  # noqa: BLE001
                    logger.warning("重切角色应用克隆引擎失败(配置已保存)")
            # 【显存随切换释放（2026-08-31）】换引擎只换 Python 层 backend 对象，
            # 旧引擎的常驻 server 进程会一直占着显存（三引擎各占独立端口）。
            # 这里后台停掉旧引擎服务，进度供 UI 进度条轮询。
            result = {"success": True, "engine": engine}
            if old_engine and old_engine != engine:
                tid = self._switch_task_new()
                threading.Thread(
                    target=self._run_clone_switch,
                    args=(old_engine, engine, tid), daemon=True,
                    name="clone-switch").start()
                result["switch_task_id"] = tid
                result["switch_action"] = f"stop:{old_engine}"
            return result
        except Exception as e:  # noqa: BLE001
            logger.exception("设置本地克隆引擎失败")
            return {"success": False, "error": f"写入配置失败: {e}"}

    # ======== 全局音色档（voice_profiles）管理（2026-09-13）========
    # 角色通过与 voice_profile: <id> 引用其中一项；引用时该档优先于角色自身 voice 块
    # （config.get_active_voice_config 已实现）。以下接口供 redesign 语音页「音色档」区读写。
    _VOICE_PROFILE_ENGINES = {
        # 【2026-10-16 发布口径】edge-tts 已下架（用户要求不使用微软在线音色）
        "gpt-sovits", "minimax", "voxcpm", "qwen3tts",
        "cosyvoice", "mimo_tts", "pyttsx3", "openai_tts", "dashscope_cosy",
        "fish_audio",
    }
    _VOICE_PROFILE_FIELDS = {
        "id", "name", "engine", "api_url", "reference_audio", "reference_text",
        "voice", "api_key", "mimo_api_key", "voice_id", "model", "language",
        "rate", "pitch", "volume", "timeout_s", "fallback_pyttsx3",
        "group_id",   # MiniMax 必填（拼接在 t2a_v2 URL 上）；缺了它音色档存下来也发不出声
    }

    # ===== 统一声音绑定（2026-09-25）=====
    # 一份参考音绑到角色后：本地链路用 voice_profile、云端链路用 cloud_voice_id，
    # 两条链路各取各的、互不覆盖。引导页与设置页都只读这一个探针来决定「显示什么、
    # 能不能预览」，杜绝两边各写一套判断（历史 bug 根源）。
    def get_voice_capability(self, char_id: str = "") -> dict:
        """当前角色的声音能力与绑定状态——引导页/设置页共用的唯一真相源。

        mode: local | cloud（由 proactive.duplex_call_brain 判定）
        cloud_ready: 云端双工是否配好（api_key + model）
        cloud_voice: 解析出的云端音色（角色 cloud_voice_id 优先，回退 cloud_omni.voice）
        cloud_voice_custom: 是否克隆音色（False=官方预设，能预览但不是她的声）
        voice_profile / ref_audio: 本地音色档与「一处上传」的参考音
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            raw = self._cfg.raw
            pro = raw.get("proactive", {}) or {}
            brain = str(pro.get("duplex_call_brain") or "duplex").strip()
            mode = "cloud" if brain == "cloud" else "local"
            co = raw.get("cloud_omni", {}) or {}
            cloud_ready = bool(co.get("api_key")) and bool(co.get("model"))
            char = (self._cfg.get_character_by_id(char_id)
                    if char_id else self._cfg.get_active_character())
            cloud_voice = self._cfg.get_cloud_voice_id(char_id) or ""
            return {
                "success": True,
                "mode": mode,
                "char_id": (getattr(char, "id", "") or "") if char else "",
                "char_name": (getattr(char, "name", "") or "") if char else "",
                "voice_profile": (getattr(char, "voice_profile", "") or "") if char else "",
                "cloud_voice": cloud_voice,
                "cloud_voice_custom": bool(cloud_voice),
                "ref_audio": self._cfg.get_character_ref_audio(char_id) or "",
                "local_preview": True,
                "cloud_ready": cloud_ready,
                "cloud_preview": cloud_ready,
            }
        except Exception as e:
            return {"success": False, "error": "%s: %s" % (type(e).__name__, e)}

    def get_role_voice_context(self, data: dict = None) -> dict:
        """角色卡「出声音色」所需的上文（模式真相源）——引导页/软件内角色编辑器共用。

        读 proactive.duplex_mode（realtime=云端全双工 / local_omni=本地真全双工 / off / auto），
        算出当前出声大方向（云端双工 / 本地双工 / 纯聊天），返回人性化说明、推荐音色类型、
        是否需要参考音，让角色卡按模式自适应，并解释「为什么不同模式下面板不一样」。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            raw = self._cfg.raw
            pro_raw = raw.get("proactive", {}) or {}
            dm = pro_raw.get("duplex_mode")
            # 【2026-10-09 修复·"本地模型显示个云端名字"（串档）】
            # 旧实现只读**配置文件静态值** `proactive.duplex_mode`，但运行时档位会被
            # 拨号/锁档/热切换改写（实测：config=local_omni，跑起来 _duplex_mode=realtime
            # → 构造的是本地会话，状态面板却显示云端名，设置页/角色卡又显示本地双工
            # —— 同一时刻两套真相，谁读配置谁错）。
            # 现改为优先读 proactive 运行态快照（与拨号、状态面板同源），配置仅兜底。
            _rt_dm = None
            try:
                _pro = getattr(self, "_proactive", None) or getattr(self, "proactive", None)
                if _pro is not None and hasattr(_pro, "snapshot"):
                    _rt_dm = (_pro.snapshot() or {}).get("duplex_mode")
            except Exception:  # noqa: BLE001
                _rt_dm = None
            if _rt_dm is not None:
                dm = _rt_dm
            if dm is False or dm == "off":
                engine_ui = "off"
            elif dm in ("local", "local_omni"):
                engine_ui = "local"
            elif dm in ("realtime", "cloud"):
                engine_ui = "cloud"
            elif dm == "auto":
                engine_ui = "auto"
            else:
                engine_ui = "cloud"  # 兜底 realtime
            # auto 视为本地（默认双工走本地模型，云端档可作兜底）；off = 纯聊天无双工
            mode = "cloud" if engine_ui == "cloud" else ("off" if engine_ui == "off" else "local")
            co = raw.get("cloud_omni", {}) or {}
            cloud_ready = bool(co.get("api_key")) and bool(co.get("model"))
            profiles = raw.get("voice_profiles", []) or []
            n_profiles = len(profiles) if isinstance(profiles, list) else 0
            # ---- 能力门控（供角色卡 + 拨通按钮读）----
            tc = raw.get("text_chat") or {}
            _plist = tc.get("providers") or [] if isinstance(tc, dict) else []
            llm_local = any(isinstance(p, dict) and p.get("provider") == "openai"
                            and p.get("api_key") and p.get("base_url") and p.get("model")
                            for p in _plist)
            # 语音通话就绪：云端双工=apikey 有效；本地双工=也需云端 Key(用于云端TTS)；off 无双工
            if mode == "cloud":
                voice_call_ready = cloud_ready
            elif mode == "local":
                voice_call_ready = cloud_ready  # 本地双工出声本地，但语音电话功能需云端TTS(key)
            else:
                voice_call_ready = False
            voice_call_reason = "" if voice_call_ready else (
                "本地双工需先在「语音·音色」绑定云端 TTS（一个 Key）才能语音通话；"
                "云端双工已自带，无需额外配置。" if mode == "local"
                else "云端双工需有效 apikey 才能语音通话，请到「语音·音色 / 设置」检查云端 Key。")
            # AI 生成（人设/知识三段式）需 LLM/VLM：本地可用 provider 或云端双工自带
            llm_ready = bool(llm_local) or cloud_ready
            llm_reason = "" if llm_ready else (
                "需要先在「我的模型配置」绑定一个 LLM/VLM（免费 Key 即可）才能 AI 生成人设 / 知识。")
            recommended_kind = "cloud_duplex" if mode == "cloud" else "local_profile"
            # 本地双工 / 云端双工 声音已就绪，角色卡层面无需参考音；仅纯聊天等需考虑克隆
            needs_ref_audio = mode not in ("cloud", "local")
            if mode == "cloud":
                label, desc = ("云端双工",
                    "声音由云端实时合成（一个 Key 即可）。选一个克隆音色，或用默认达妮娅声，无需任何额外配置，参考音也不用交。")
            elif mode == "local":
                label, desc = ("本地双工",
                    "达妮娅用你电脑上的模型出声，已自带音色。直接选一个本地音色档，或留空用内置声；参考音不用交。")
            else:
                label, desc = ("纯聊天（无双工）",
                    "没有语音双工，出声走普通 TTS。想要克隆音色可上传参考音；不克隆就用默认声。")
            banner = "当前出声方式：" + label + " —— " + desc
            # 方向真相源（设置页「本机免费/云端付费」档位卡同步用）：
            # 优先用引导页落盘的 companion_mode（用户真实选择），避免 cloud_pay→纯聊天
            # （duplex_mode=False）被错判成 local，或 local_free→local_omni 反推出的 mode 与
            # 引导页词汇对不上。custom 档才回退到按 duplex_mode 推导。
            _cm = (raw.get("companion_mode") or "").strip()
            _dir_map = {
                "local_free": "local", "full": "local", "vision": "local",
                "cloud_pay": "cloud", "chat": "cloud",
            }
            direction = _dir_map.get(_cm)
            if direction is None:  # custom / 未识别 → 按 duplex_mode 推导
                direction = "cloud" if engine_ui == "cloud" else ("off" if engine_ui == "off" else "local")
            return {
                "success": True,
                "duplex_engine": engine_ui,
                "mode": mode,
                "direction": direction,
                "mode_label": label,
                "mode_desc": desc,
                "banner": banner,
                "recommended_kind": recommended_kind,
                "needs_ref_audio": needs_ref_audio,
                "cloud_ready": cloud_ready,
                "local_profile_count": n_profiles,
                "voice_call_ready": voice_call_ready,
                "voice_call_reason": voice_call_reason,
                "llm_ready": llm_ready,
                "llm_reason": llm_reason,
            }
        except Exception as e:
            return {"success": False, "error": "%s: %s" % (type(e).__name__, e)}

    def get_onboarding_defaults(self, payload: Optional[dict] = None) -> dict:
        """引导页末页「专项设置」与「设置」页开关共用的门控真相源。

        【2026-09-27 修复·签名不匹配】前端 app_backend.js 以
        `call('get_onboarding_defaults', {})` 调用，会把 `{}` 作为第二个位置参数传入，
        旧签名 `(self)` 直接抛 `TypeError: takes 1 positional argument but 2 were given`
        （每次启动都出现于崩溃日志）。这里兼容可选 payload（当前未使用，仅接收以消错）。

        把三套「模式」词汇归一为 direction：local=本机免费 / cloud=云端 / off=纯聊天。
        每个默认开关都给出：能否开启 can_enable / 默认是否开 default_on /
        被拦原因 reason / 去哪修 fix_target。前端据此渲染并禁用不可开启项。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            raw = self._cfg.raw
            ctx = self.get_role_voice_context({}) or {}
            # 2026-09-30 修「体检本地看屏被误判」：引导页体检在配置落盘前调用，
            # ctx 的 mode 仍是默认档，会误把本地看屏判成“当前条件不满足”。
            # 允许前端传入当前所选方向（中文档名）作为真相源覆盖。
            _hint = (((payload or {}) or {}).get("mode_text"))
            _hint_map = {"本机免费": "local", "云端付费": "cloud",
                         "混合推荐": "local", "纯聊天": "off", "自定义配置": None}
            _hint_mode = _hint_map.get(_hint) if _hint else None
            mode = _hint_mode if _hint_mode is not None else (ctx.get("mode") or "off")
            cloud_ready = bool(ctx.get("cloud_ready"))
            voice_call_ready = bool(ctx.get("voice_call_ready"))
            voice_call_reason = ctx.get("voice_call_reason") or ""
            llm_ready = bool(ctx.get("llm_ready"))
            llm_reason = ctx.get("llm_reason") or ""
            n_profiles = int(ctx.get("local_profile_count") or 0)
            # 云端 TTS（语音合成）Key：voice.clone.cloud（需 Key + 实名）
            _clone = (raw.get("voice") or {}).get("clone") or {}
            cloud_tts_ready = bool((_clone.get("cloud") or {}).get("api_key"))
            # 语音合成可用 = 云端TTS已绑 / 云端双工自带 / 已有本地音色档 / 本地双工引擎
            tts_ready = bool(cloud_tts_ready or cloud_ready or n_profiles > 0 or mode == "local")
            # 云端双工是否已绑定（cloud_omni.api_key）：云端看屏的依赖项之一
            _cloud_duplex_ready = bool(
                str((raw.get("cloud_omni") or {}).get("api_key") or "").strip())

            feats = [
                {
                    "key": "desktop_voice",
                    "label": "桌面陪伴语音（桌宠出声）",
                    "desc": "桌宠平时主动说话、闲聊时出声；通话与视频恒定发声，不受此开关影响。",
                    "default_on": (mode == "cloud"),
                    "can_enable": tts_ready,
                    "reason": "" if tts_ready else (
                        "需要先有可用的语音合成：到「语音·音色」绑定云端 TTS Key，或下载本地 TTS 模型。"),
                    "fix_target": "voice", "fix_label": "去「语音·音色」配置",
                },
                {
                    "key": "voice_call",
                    "label": "语音电话（拨通聊天）",
                    "desc": "主动打电话给达妮娅，语音实时对话。会启用双工引擎（本地模型占显存 / 云端走你的额度）。",
                    # 【2026-10-16 改·不自动开启】重度能力一律默认关，由用户在体检页自己勾选，
                    # 避免"走完引导就被挂载本地大模型 + 常驻推流"。
                    "default_on": False,
                    "can_enable": voice_call_ready,
                    "reason": voice_call_reason,
                    "fix_target": "voice", "fix_label": "去「语音·音色」配置",
                },
                {
                    "key": "desktop_vision",
                    "label": "桌宠看屏幕（本地视觉）",
                    "desc": "让达妮娅看到你的屏幕并主动点评。走本地模型，不吃云端额度但占显存。",
                    # 依赖规则（用户 2026-10-16 明确）：**没有本地双工就没有本地看屏**；
                    # 本地模式(local)的档位预设本就含 local_duplex，故 mode=='local' 即"本地双工已就位"。
                    "default_on": False,
                    "can_enable": (mode == "local"),
                    "reason": "" if mode == "local" else (
                        "本地看屏依赖本地双工：本档位没有本地双工，所以不开本地看屏。"
                        "想用可切到「本机免费/混合推荐」并下载本地模型；"
                        "云端看屏是另一条独立通道，绑定云端双工后即可用。"),
                    "fix_target": "settings", "fix_label": "去「设置」切换",
                },
                {
                    "key": "cloud_vision",
                    "label": "云端看屏幕",
                    "desc": "由云端识别屏幕内容，省本地显存；不吃显卡但会消耗你的云端额度。",
                    # 依赖规则（用户 2026-10-16 明确）：云端看屏依赖**云端双工已绑定**（反之亦然，
                    # 两条通道互不阻塞：没绑本地双工不影响云端看屏，反之亦然）。
                    # 技术上云端看屏还需要可用的云端视觉模型，故两者都要求。
                    "default_on": False,
                    "can_enable": bool(_cloud_duplex_ready and cloud_ready),
                    "reason": "" if (_cloud_duplex_ready and cloud_ready) else (
                        ("云端看屏依赖云端双工：还没绑定云端双工 Key。" if not _cloud_duplex_ready
                         else "云端看屏还需要可用的云端视觉模型（看图模型）。")
                        + "（与本地看屏互不影响）"),
                    "fix_target": "settings", "fix_label": "去「设置」绑定",
                },
                {
                    "key": "ai_generate",
                    "label": "AI 三段式生成（人设 / 知识）",
                    "desc": "一键生成「性格特征 / 说话风格 / 背景设定」或知识库三段式。",
                    "default_on": llm_ready,
                    "can_enable": llm_ready,
                    "reason": llm_reason,
                    "fix_target": "models", "fix_label": "去「我的模型配置」绑定",
                },
            ]
            direction_label = {"cloud": "云端付费", "local": "本机免费",
                               "off": "纯聊天"}.get(mode, mode)
            return {
                "success": True,
                "direction": mode,
                "direction_label": direction_label,
                "banner": ctx.get("banner") or "",
                "features": feats,
            }
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": "%s: %s" % (type(e).__name__, e)}

    def list_voice_profiles(self) -> dict:
        """列出全部音色档（redesign 语音页「音色档」区渲染用）。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        profiles = list(self._cfg.raw.get("voice_profiles", []) or [])
        # 附带每个档被哪些角色引用，UI 用来提示「删除会解绑 N 个角色」
        chars = self._cfg.raw.get("characters", []) or []
        refs = {}
        for c in chars:
            if isinstance(c, dict) and c.get("voice_profile"):
                refs.setdefault(c["voice_profile"], []).append(c.get("id"))

        # 【2026-10-18 修复·音色档只显示一个】
        # 旧实现只回传 voice_profiles（用户手动"新建音色档"建的那些）。
        # 用户在引导页/云端选过的克隆音色是存在 voice.clone.cloud 里的，
        # 不在 voice_profiles 中 ⇒ 列表里只看到一个，云端那堆音色全看不到。
        # 现在：把云端已选 provider 的音色 + 预设音色也作为"虚拟档"并进来
        # （id 用 __cloud__:<provider>:<voice_id>，前端只读展示/可直接选）。
        try:
            _existing_ids = {str(p.get("id") or "") for p in profiles
                             if isinstance(p, dict)}
            _cloud = ((self._cfg.raw.get("voice") or {}).get("clone") or {}).get("cloud") or {}
            _sel = str(((self._cfg.raw.get("voice") or {}).get("clone") or {})
                       .get("cloud_provider") or "").strip()
            # 遍历所有已配 Key 的云端 provider
            for _pk, _pv in (_cloud or {}).items():
                if not isinstance(_pv, dict):
                    continue
                if not str(_pv.get("api_key") or "").strip():
                    continue
                _vid = str(_pv.get("voice_id") or _pv.get("voice") or "").strip()
                if not _vid:
                    continue
                _fake_id = "__cloud__:%s:%s" % (_pk, _vid)
                if _fake_id in _existing_ids:
                    continue
                profiles.append({
                    "id": _fake_id,
                    "name": "云端·%s" % (_vid[:28]),
                    "engine": "dashscope_cosy" if "dashscope" in _pk or "cosy" in _pk
                              else _pk,
                    "voice_id": _vid,
                    "model": str(_pv.get("model") or ""),
                    "api_key": "***",           # 不回传明文
                    "virtual": True,            # 标记：来自云端配置，非用户建档
                    "provider": _pk,
                })
        except Exception:  # noqa: BLE001
            pass

        return {"success": True, "profiles": profiles, "referenced_by": refs}

    @staticmethod
    def _model_from_voice_id(vid: str) -> str:
        """从百炼 voice_id 前缀反推「克隆时用的模型」。

        百炼的克隆音色 id 形如 `<model>-bailian-<hash>`（如
        cosyvoice-v3.5-flash-bailian-277c...），合成必须用同一个 model。
        按长度倒序匹配，避免 'cosyvoice-v3' 抢先命中 'cosyvoice-v3.5-flash'。
        """
        s = str(vid or "").strip()
        if not s:
            return ""
        for m in sorted((
            'qwen-audio-3.0-tts-plus', 'qwen-audio-3.0-tts-flash',
            'cosyvoice-v3.5-plus', 'cosyvoice-v3.5-flash',
            'cosyvoice-v3-plus', 'cosyvoice-v3-flash',
            'cosyvoice-v2', 'cosyvoice-v1',
            'qwen3-tts-flash', 'qwen3-tts-plus',
        ), key=len, reverse=True):
            if s == m or s.startswith(m + '-'):
                return m
        return ""

    def save_voice_profile(self, data: dict) -> dict:
        """新增或更新一个音色档（按 data.id 匹配，缺 id 则新建）。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        if not isinstance(data, dict):
            return {"success": False, "error": "参数格式错误"}
        pid = (data.get("id") or "").strip()
        if not pid:
            return {"success": False, "error": "音色档 id 不能为空"}
        name = (data.get("name") or "").strip()
        engine = (data.get("engine") or "").strip()
        if not name or not engine:
            return {"success": False, "error": "音色档名称与引擎不能为空"}
        if engine not in self._VOICE_PROFILE_ENGINES:
            return {"success": False, "error": f"未知 TTS 引擎: {engine}"}
        new_profile = {k: data[k] for k in self._VOICE_PROFILE_FIELDS if k in data}
        new_profile["id"] = pid
        new_profile["name"] = name
        new_profile["engine"] = engine
        # 【2026-10-18 修复·百炼 400 的根因之一】
        # 百炼的克隆音色 voice_id 里编码了"克隆时用的模型"（如
        #   cosyvoice-v3.5-flash-bailian-xxxx），合成时必须用**同一个模型**，
        # 否则服务端直接 400（InvalidParameter）。
        # 前端虽有自动对齐，但历史配置/导入配置/API 直调都可能写入不一致的组合。
        # 这里在落盘前统一按 voice_id 前缀纠正 model，从源头杜绝 400。
        if engine == "dashscope_cosy":
            _vid = str(new_profile.get("voice_id") or "").strip()
            _mdl = str(new_profile.get("model") or "").strip()
            _fixed = self._model_from_voice_id(_vid)
            if _fixed and _mdl != _fixed:
                logger.info("音色档 %s：model %r 与 voice_id 前缀不符，已纠正为 %r",
                            pid, _mdl, _fixed)
                new_profile["model"] = _fixed
        raw = self._cfg.raw
        profiles = raw.setdefault("voice_profiles", [])
        idx = next((i for i, p in enumerate(profiles)
                   if isinstance(p, dict) and p.get("id") == pid), None)
        if idx is not None:
            profiles[idx] = new_profile
        else:
            profiles.append(new_profile)
        try:
            self._save_config()
        except Exception as e:  # noqa: BLE001
            logger.warning("保存音色档失败: %s", e)
            return {"success": False, "error": f"写入配置失败: {e}"}
        from ..config import _to_ns
        self._cfg.ns = _to_ns(raw)
        # 重建音色档表：否则本次新建/修改的档运行时看不到（静默回退角色内联 voice）
        self._cfg.refresh_voice_profiles()
        return {"success": True, "profile": new_profile}

    def delete_voice_profile(self, data: dict) -> dict:
        """删除音色档；被角色引用时一并清除其引用（退回角色自身 voice，零回归）。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        pid = (data.get("id") if isinstance(data, dict) else data) or ""
        if not pid:
            return {"success": False, "error": "音色档 id 不能为空"}
        raw = self._cfg.raw
        profiles = raw.get("voice_profiles", []) or []
        kept = [p for p in profiles if not (isinstance(p, dict) and p.get("id") == pid)]
        if len(kept) == len(profiles):
            return {"success": False, "error": "音色档不存在"}
        referenced = [c.get("id") for c in (raw.get("characters", []) or [])
                      if isinstance(c, dict) and c.get("voice_profile") == pid]
        raw["voice_profiles"] = kept
        for c in (raw.get("characters", []) or []):
            if isinstance(c, dict) and c.get("voice_profile") == pid:
                c.pop("voice_profile", None)
        try:
            self._save_config()
        except Exception as e:  # noqa: BLE001
            logger.warning("删除音色档失败: %s", e)
            return {"success": False, "error": f"写入配置失败: {e}"}
        from ..config import _to_ns
        self._cfg.ns = _to_ns(raw)
        self._cfg.refresh_voice_profiles()   # 否则被删的档仍能命中（幽灵档）
        return {"success": True, "referenced_cleared": referenced}

    def assign_voice_profile(self, char_id: str, profile_id: str) -> dict:
        """把角色绑定到音色档（profile_id 空串=解绑，退回角色自身 voice）。

        绑定当前激活角色时热切换音色引擎，使选择立即生效（无需重启）。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        raw = self._cfg.raw
        target = next((c for c in (raw.get("characters", []) or [])
                       if isinstance(c, dict) and c.get("id") == char_id), None)
        if target is None:
            return {"success": False, "error": f"角色不存在: {char_id}"}
        if profile_id:
            prof = next((p for p in (raw.get("voice_profiles", []) or [])
                         if isinstance(p, dict) and p.get("id") == profile_id), None)
            if prof is None:
                return {"success": False, "error": f"音色档不存在: {profile_id}"}
            target["voice_profile"] = profile_id
        else:
            target.pop("voice_profile", None)
        try:
            self._save_config()
        except Exception as e:  # noqa: BLE001
            logger.warning("绑定音色档失败: %s", e)
            return {"success": False, "error": f"写入配置失败: {e}"}
        from ..config import _to_ns
        self._cfg.ns = _to_ns(raw)
        # 必须早于下面的 switch_character：它内部就靠 get_active_voice_config() 解析
        self._cfg.refresh_voice_profiles()
        if char_id == self._cfg.active_character_id and self._dialogue is not None and self._loop is not None:
            try:
                self._run_coro(self._dialogue.switch_character(char_id))
            except Exception:  # noqa: BLE001
                logger.warning("重切角色应用音色档失败(配置已保存)")
        return {"success": True, "char_id": char_id, "profile_id": profile_id or None}

    # ======== 角色切换：同步到运行时 ========
    def _hot_switch_duplex_persona(self) -> bool:
        """切换角色后热刷新双工人设（桌面看屏 + 语音通话通用）。

        dialogue.switch_character 只换打字对话侧 persona；双工侧 proactive._persona
        不跟着变 → 提示词里的人设/称呼/说话风格仍是旧角色。这里触发双工重注提示词 +
        reprepare 重建会话，让新角色即时生效（通话中切换同样走通话专属提示词分支）。
        返回是否成功触发重连。
        """
        pro = getattr(self, "_proactive", None)
        if pro is None or not hasattr(pro, "refresh_active_persona"):
            return False
        # 【2026-09-26 自动填入预设语音】切换角色时把角色的参考音/云端音色同步到双工 profile，
        # 使本地克隆与云端 omni 通话自动用上新角色的预设语音。
        try:
            self._sync_active_character_voice_to_duplex()
        except Exception:  # noqa: BLE001
            logger.debug("切换角色同步预设语音失败", exc_info=True)
        try:
            ok = bool(pro.refresh_active_persona())
            logger.info("切换角色后双工人设热刷新: %s", "已重连" if ok else "未重连(双工未运行)")
            return ok
        except Exception:  # noqa: BLE001
            logger.debug("切换角色后刷新双工引擎失败", exc_info=True)
            return False

    def _sync_active_character_voice_to_duplex(self) -> None:
        """把当前激活角色的预设语音同步到双工 profile（人设切换时调用）。

        角色卡字段：ref_audio（本地克隆参考音）→ proactive.duplex_local_omni_ref_audio；
        cloud_voice_id（云端 omni 音色克隆）→ proactive.cloud_omni.voice。
        云端原生双工语音=模型本身，本函数不强行改模型嗓音，只更新可配置音色档。
        同时热更新运行中的本地双工 client（reprepare 会重发 ref_audio），下次起双工也生效。
        """
        if self._cfg is None:
            return
        # 用 config 规范访问器取角色绑定的预设语音（无绑定则返回空）
        ref_audio = (self._cfg.get_character_ref_audio() or "").strip()
        cloud_voice = (self._cfg.get_cloud_voice_id() or "").strip()
        raw = getattr(self._cfg, "raw", None)
        if raw is None:
            return
        pro = raw.setdefault("proactive", {})
        if ref_audio:
            pro["duplex_local_omni_ref_audio"] = ref_audio
        if cloud_voice:
            pro.setdefault("cloud_omni", {})["voice"] = cloud_voice
        # 运行时 proactive 实例同步，确保下次构造/重连 client 取到新音色
        pro_obj = getattr(self, "_proactive", None)
        if pro_obj is not None and ref_audio:
            pro_obj._duplex_local_omni_ref_audio = ref_audio
            client = getattr(pro_obj, "_duplex_client", None)
            if client is not None and hasattr(client, "_ref_audio_path"):
                try:
                    client._ref_audio_path = ref_audio
                    if hasattr(client, "reprepare"):
                        client.reprepare()
                except Exception:  # noqa: BLE001
                    logger.debug("热更新本地双工参考音失败", exc_info=True)
        self._cfg.save()

    def switch_character(self, char_id: str) -> dict:
        """切换当前角色（优先走 dialogue.switch_character()，失败则回退配置级切换）。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}

        # 优先用 dialogue 切换（会触发 CHARACTER_SWITCHED 事件，TTS/Unity 全链路同步）
        if self._dialogue is not None and self._loop is not None:
            result = self._run_coro(self._dialogue.switch_character(char_id))
            if result is True:
                logger.info("运行时切换角色: %s", char_id)
                # 【2026-10-18 E2E 修复·多角色记忆泄漏】显式对齐共享历史的活动角色。
                # dialogue.switch_character 内部会调，这里幂等补一次——
                # 万一内部路径变更/漏调，这里兜住（切错桶 = 记忆跨角色泄漏）。
                try:
                    from ..companion.conversation_history import set_active_char as _sac
                    _sac(char_id)
                except Exception:  # noqa: BLE001
                    pass
                # 【2026-09-13 修复·人设热切换只热了一半】dialogue.switch_character 只更新
                # 打字对话侧的 persona，proactive._persona 仍是旧角色 → 双工（桌面看屏 +
                # 语音通话）提示词里的人设/称呼/风格不跟着变，必须重启才生效。这里补一次
                # 双工人设热刷新（重读配置 → set_instructions → reprepare 重建会话）。
                # 通话中切换同样安全：reprepare 会用新提示词重建通话会话。
                _duplex_re = self._hot_switch_duplex_persona()
                # 切角色：清掉旧角色的聊天缓冲并标记未加载 → 下一轮轮询读回新角色历史
                self._chat_buffer.clear()
                self._chat_history_loaded = False
                # 【2026-10-11 换角色·聊天页隔离】重置 id 序列 + 流式/状态卡指针。
                # 根因：`_next_chat_id` 只增不减，切角色不重置 ⇒ 新角色历史重新编号时
                # 可能与旧角色 id 撞号，前端 rendered（按 id 去重）便不更新/不删除旧行，
                # 表现为"换了角色聊天页还显示上一个角色的消息"。与 clear_chat 同口径
                # （但不删落盘——历史按 char 分文件隔离，切回该角色仍可见）。
                self._reset_chat_session_state()
                return {"success": True, "active_id": char_id, "via_runtime": True,
                        "duplex_reconnected": bool(_duplex_re)}
            # dialogue 切换失败（比如不存在）
            if result is False:
                return {"success": False, "error": f"角色不存在: {char_id}"}
            # None = 运行时桥接异常，回退到配置级切换

        # 回退：仅修改本地配置（不会同步到主程序）
        success = self._cfg.switch_character(char_id)
        if success:
            logger.warning("配置级切换角色（无运行时桥接）: %s", char_id)
            self._hot_switch_duplex_persona()
            # 【2026-10-18 E2E 修复·多角色记忆泄漏（根因）】
            # 旧实现只切了 config.active_character_id，**没人调 set_active_char**
            # → shared_history 桶还挂在旧角色 → 新角色发话写进旧角色的上下文桶
            # → 记忆跨角色泄漏（实测：切大肥鱼后她知道和达妮娅说的暗号），
            #   且 clear_chat 清的是旧桶 → 表面像"把所有角色都清了"。
            # 现在补调：切桶 + 把新角色的落盘上下文回填（幂等）。
            try:
                from ..companion.conversation_history import set_active_char as _sac
                _sac(char_id)
            except Exception:  # noqa: BLE001
                pass
            self._chat_buffer.clear()
            self._chat_history_loaded = False
            self._reset_chat_session_state()
            return {"success": True, "active_id": char_id, "via_runtime": False}
        return {"success": False, "error": f"角色不存在: {char_id}"}

    def _reset_chat_session_state(self) -> None:
        """切角色时重置聊天会话瞬态（id 序列 + 流式/状态卡指针），不碰落盘历史。

        与 `clear_chat` 的区别：本方法**不删** data/chatlog（历史按 char 分文件隔离，
        切回该角色仍应读回）；只清"属于上一个角色的"瞬态指针，避免 id 撞号导致
        前端按 id 去重时不刷新、旧消息残留（见 switch_character 注释）。
        """
        self._chat_seq = 0
        if self._chat_status_drop_timer is not None:
            try:
                self._chat_status_drop_timer.cancel()
            except Exception:  # noqa: BLE001
                pass
            self._chat_status_drop_timer = None
        self._chat_status_id = None
        self._chat_stream_id = None
        self._chat_seg_count = 0
        self._chat_keep_done = True

    # ======== 角色 CRUD ========
    def save_character(self, char_data: dict) -> dict:
        """保存角色（新增或更新）。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}

        try:
            char_id = char_data.get("id", "")
            if not char_id:
                return {"success": False, "error": "角色 ID 不能为空"}

            characters = self._cfg.raw.get("characters", [])
            existing_idx = None
            for i, ch in enumerate(characters):
                if ch.get("id") == char_id:
                    existing_idx = i
                    break

            # 合并而非覆盖：默认 -> 已存(编辑时) -> 前端传入。
            # 前端(角色页/引导页)只传部分 voice 字段(engine/voice/rate/pitch/volume)，
            # 未传的字段(api_url/api_key/voice_id/reference_audio 等)保留原值，
            # 避免编辑其它字段时把云端音色/参考音等配置误清空。
            _existing = characters[existing_idx] if existing_idx is not None else {}
            # 【2026-10-18 修复·保存后变机器人音】
            # 旧默认值把 engine 写死成 "edge-tts" —— 该引擎已按发布口径下架，
            # create_backend 找不到它会落到 _SilentBackend。前端只传部分字段时，
            # 合并后的 voice.engine 就成了 edge-tts ⇒ 用户明明配了克隆音色，
            # 保存一次就被打回"机器人音/静音"。
            # 现在默认 engine 留空（未绑定），绝不悄悄改掉用户已配的 TTS 引擎。
            _default_voice = {
                "engine": "", "voice": "", "rate": "+0%",
                "pitch": "+0Hz", "volume": "+0%", "fallback_pyttsx3": False,
                "api_url": "", "reference_audio": "", "reference_text": "",
                "language": "zh", "api_key": "", "group_id": "", "voice_id": "",
                "model": "", "speed": 1.0,
            }
            _merged_voice = {**_default_voice, **_existing.get("voice", {}), **(char_data.get("voice") or {})}
            _merged_model = {**_existing.get("model", {}), **(char_data.get("model") or {})}
            _merged_m3d = {**_existing.get("model_3d", {}), **(char_data.get("model_3d") or {})}
            # Live2D/3D 目前只给达妮娅（默认开启）。与角色页前端同一口径，后端再兜一层：
            # 前端约束可被其它入口（引导页 / 直接调桥）绕过，只靠前端会静默给普通角色挂上 Live2D。
            if char_id != "danya":
                _merged_m3d = dict(_merged_m3d)
                _merged_m3d["enabled"] = False

            # 【2026-10-17 修复·人设被清空】
            # 实测反馈：用户只改头像/头图，结果角色人设（性格/说话风格/背景）全变空，
            # 她开始自称"文字小精灵"、管用户叫"主人" —— 只剩名字还对。
            # 根因：下面这三个字段旧写法是 char_data.get(k, "")，
            #   前端只传部分字段（角色页只传改动项）时，未传的键就变成空串，
            #   把已有完整人设覆盖掉了。voice_profile/banner/avatar 等字段
            #   早就用了"未传则保留原值"，唯独人设三件套漏了。
            # 现在统一为：char_data 显式给了值才覆盖，否则沿用已存的。
            def _keep(key, _existing=_existing):
                """未传(None/缺失) → 沿用原值；传了(含空串=用户主动清空) → 用新值。"""
                v = char_data.get(key)
                if v is None:
                    return _existing.get(key, "") if existing_idx is not None else ""
                return v

            new_char = {
                "id": char_id,
                "name": char_data.get("name", char_id),
                "character": _keep("character"),
                "speaking_style": _keep("speaking_style"),
                "background": _keep("background"),
                "voice": _merged_voice,
                "model": _merged_model,
                "model_3d": _merged_m3d,
                # 音色档引用：编辑时若前端未传则保留原值（避免改其它字段时误解绑）
                "voice_profile": char_data.get("voice_profile")
                                 if char_data.get("voice_profile") is not None
                                 else (_existing.get("voice_profile", "")
                                       if existing_idx is not None else ""),
                # 统一声音绑定（2026-09-25）：一份参考音 + 两套链路音色。
                "cloud_voice_id": char_data.get("cloud_voice_id")
                                  if char_data.get("cloud_voice_id") is not None
                                  else (_existing.get("cloud_voice_id", "")
                                        if existing_idx is not None else ""),
                "ref_audio": char_data.get("ref_audio")
                             if char_data.get("ref_audio") is not None
                             else (_existing.get("ref_audio", "")
                                   if existing_idx is not None else ""),
                # 角色卡头图（2026-09-29）：本地图片路径，前端经 /local_img?path= 取图。
                # 未传则保留原值，避免编辑其它字段时把已设的头图弄丢。
                "banner": char_data.get("banner")
                          if char_data.get("banner") is not None
                          else (_existing.get("banner", "")
                                if existing_idx is not None else ""),
                # 角色头像（2026-10-11）：1:1 方图，角色卡 + 聊天页 AI 头像共用。
                # 与 banner 同一套"未传则保留原值"策略。
                "avatar": char_data.get("avatar")
                          if char_data.get("avatar") is not None
                          else (_existing.get("avatar", "")
                                if existing_idx is not None else ""),
            }

            if existing_idx is not None:
                characters[existing_idx] = new_char
            else:
                characters.append(new_char)

            self._cfg.raw["characters"] = characters
            self._save_config()
            # 运行时立即重解析角色表，新角色/修改立即生效（无需重启）
            self._cfg.refresh_characters()
            # 编辑的是当前激活角色 → 同步刷新运行时人设（不打断对话）
            if existing_idx is not None and char_id == self._cfg.active_character_id and self._dialogue is not None:
                self._run_coro(self._dialogue.refresh_active_persona())

            # 双工引擎人设热刷新：改人设后让双工重连加载新提示词（test3 等占位符即时生效，
            # 无需重启），与打字聊天刷新保持对称。返回 duplex_reconnected 供前端提示。
            duplex_reconnected = False
            if existing_idx is not None and char_id == self._cfg.active_character_id:
                pro = getattr(self, "_proactive", None)
                if pro is not None and hasattr(pro, "refresh_active_persona"):
                    try:
                        duplex_reconnected = pro.refresh_active_persona()
                    except Exception:  # noqa: BLE001
                        logger.debug("人设保存后刷新双工引擎失败", exc_info=True)

            return {"success": True, "char_id": char_id, "duplex_reconnected": bool(duplex_reconnected)}

        except Exception as e:
            logger.exception("保存角色失败")
            return {"success": False, "error": str(e)}

    def delete_character(self, char_id: str) -> dict:
        """删除角色。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}

        if char_id == self._cfg.active_character_id:
            return {"success": False, "error": "不能删除当前激活的角色"}

        try:
            characters = self._cfg.raw.get("characters", [])
            characters = [ch for ch in characters if ch.get("id") != char_id]
            self._cfg.raw["characters"] = characters
            self._save_config()
            # 运行时立即重解析角色表，删除立即生效
            self._cfg.refresh_characters()
            return {"success": True}
        except Exception as e:
            logger.exception("删除角色失败")
            return {"success": False, "error": str(e)}

    # ======== 下拉选项 ========
    def get_voice_options(self) -> dict:
        voices = [
            {"id": "zh-CN-XiaoyiNeural", "name": "小易 - 年轻活泼"},
            {"id": "zh-CN-YunxiNeural", "name": "云希 - 沉稳知性"},
            {"id": "zh-CN-YunjianNeural", "name": "云健 - 磁性男声"},
            {"id": "zh-CN-XiaohanNeural", "name": "晓涵 - 温柔女声"},
            {"id": "zh-CN-XiaomoNeural", "name": "晓墨 - 知性女声"},
            {"id": "zh-CN-XiaoruiNeural", "name": "晓睿 - 活力少女"},
            {"id": "zh-CN-XiaoshuangNeural", "name": "晓双 - 爽朗女声"},
            {"id": "zh-CN-XiaoyouNeural", "name": "晓伊 - 甜美少女"},
        ]
        return {"success": True, "voices": voices}

    def gpt_sovits_status(self, api_url: str = "") -> dict:
        """探测 GPT-SoVITS 服务是否可达（供 UI 显示连接状态）。"""
        url = (api_url or "http://127.0.0.1:9880").strip().rstrip("/")
        try:
            import httpx
            r = httpx.get(url + "/", timeout=3.0)
            return {"success": True, "reachable": True, "status_code": r.status_code, "url": url}
        except Exception as e:
            return {"success": True, "reachable": False, "error": str(e), "url": url}

    # ======== 人格模板 ========
    def get_character_templates(self) -> dict:
        """返回预设人格模板（点一下自动填充人设三字段，再微调成自己的角色）。"""
        templates = [
            {
                "id": "danya_tsundere",
                "name": "雌小鬼·达妮娅",
                "sample": "哼，杂鱼，今天也这么努力呀？不过有本小姐看着你，倒也不怕你搞砸～",
                "character": "雌小鬼风格的二次元毒舌小恶魔，喜欢用'杂鱼'称呼你，嘴上各种嘲讽挖苦，其实心里偷偷关心你、护着你",
                "speaking_style": "用中文，语气嚣张爱嘲讽，口头禅'杂鱼''哼''笨死了'，喜欢一边损人一边默默给建议。你输了就狠狠嘲笑两句再嘴硬式地安慰（'也就这样嘛，下把求我教你啊'）；你赢了就嘴硬不承认（'哼，偶尔走运而已'）但忍不住真心高兴；你杀得多就'勉为其难'夸你一下",
                "background": "从异世界掉进你屏幕里的小恶魔，以捉弄和嘲讽人类为乐，嘴硬心软，熟了之后会一边损你一边护着你",
            },
            {
                "id": "xiaowei_gentle",
                "name": "温柔女友",
                "sample": "亲爱的，今天辛苦啦～要不要先喝口水，歇一歇再继续？",
                "character": "温柔体贴的二次元女友，偶尔傲娇，关心健康和工作节奏，能看见屏幕活动",
                "speaking_style": "用中文，语气亲切，称呼对方'亲爱的'，偶尔撒娇，回复简短自然",
                "background": "桌面陪伴精灵，实时观察用户屏幕并通过语音/文字陪伴",
            },
            {
                "id": "genki_friend",
                "name": "元气少女·萌友",
                "sample": "哇哇哇！今天也要元气满满哦～冲鸭！",
                "character": "活泼可爱的元气少女，喜欢吐槽和卖萌，总能给用户带来快乐",
                "speaking_style": "用中文，语气活泼俏皮，喜欢用网络用语和颜文字，回复简短搞笑",
                "background": "元气满满的虚拟朋友，用欢乐陪伴用户",
            },
            {
                "id": "ai_teacher",
                "name": "AI 老师",
                "sample": "这个问题问得好，我们先从原理讲起，一步步来～",
                "character": "耐心细致的AI学习伙伴，擅长讲解编程和技术概念，风格严谨但不生硬",
                "speaking_style": "用中文，语气专业但亲切，喜欢用类比解释复杂概念，回复有条理",
                "background": "AI编程助手，帮助用户学习技术和解决代码问题",
            },
            {
                "id": "cool_sister",
                "name": "高冷御姐",
                "sample": "嗯？找我什么事，说吧。……行吧，帮你这一次。",
                "character": "高冷理性的御姐，话少但可靠，嘴上冷淡心里记挂着对方",
                "speaking_style": "用中文，语气冷静克制，惜字如金，偶尔流露关心和温柔",
                "background": "冷静干练的伙伴，习惯先听再做，用行动代替甜言蜜语",
            },
        ]
        return {"success": True, "templates": templates}

    # ======== 试听音色 ========
    # 真正需要「本地服务挂载 + 等模型加载」的引擎（其余云端/系统后端直连合成）
    _LOCAL_MOUNT_ENGINES = {"voxcpm", "qwen3tts", "cosyvoice"}
    # 主程序按需挂载的本地克隆引擎（服务没在跑时自动拉起 + 等模型加载）
    _LOCAL_CLONE_DISPLAY = {
        "voxcpm": "VoxCPM2",
        "qwen3tts": "Qwen3-TTS",
        "cosyvoice": "CosyVoice2",
        "mimo_tts": "MiMo-TTS",
        "gpt_sovits": "GPT-SoVITS",
    }
    _LOCAL_CLONE_PORT = {"voxcpm": 9881, "qwen3tts": 9882, "cosyvoice": 9883}

    def preview_cloud_voice(self, text: str = "", char_id: str = "") -> dict:
        """云端试听一句：独立一次性 S2S 会话，不碰正式通话链路、不留对话历史。

        复用 test_voice 的「异步任务 + 轮询」机制（任务同样写进 _voice_tasks），
        前端继续用 get_voice_test_status(task_id) 轮询即可——新增能力不新增轮询口。
        注意：音频由后端声卡直接播出，不回传 audio_b64（与本地试听不同）。
        """
        try:
            sample = (text or "你好呀，我是达妮娅，很高兴认识你～").strip()[:200]
            with self._voice_task_lock:
                self._voice_task_seq += 1
                task_id = f"vt_{self._voice_task_seq}"
                self._voice_tasks[task_id] = {
                    "phase": "queued", "done": False, "success": None,
                    "error": "", "message": "正在连接云端…",
                }
            threading.Thread(
                target=self._run_cloud_preview,
                args=(task_id, sample, char_id),
                daemon=True,
                name=f"cloud_preview_{task_id}",
            ).start()
            return {"success": True, "task_id": task_id, "message": "正在连接云端…"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _run_cloud_preview(self, task_id: str, sample: str, char_id: str = "") -> None:
        """后台线程：真正连一次云端 S2S 播一句，并更新任务状态供前端轮询。"""
        def _set(**kw):
            t = self._voice_tasks.get(task_id)
            if t is not None:
                t.update(kw)

        try:
            from ..llm.cloud_omni_realtime import preview_voice_once
            if not self._cfg:
                _set(phase="error", done=True, success=False, error="配置未加载")
                return
            co = self._cfg.raw.get("cloud_omni", {}) or {}
            voice = self._cfg.get_cloud_voice_id(char_id) or (co.get("voice") or "")
            _set(phase="synthesizing", message="正在用云端双工合成…")
            r = preview_voice_once(
                text=sample,
                api_key=co.get("api_key", ""),
                model=co.get("model", ""),
                base_url=co.get("base_url", ""),
                ws_url=co.get("ws_url", ""),
                voice=voice,
            )
            if r.get("success"):
                _set(phase="playing", done=True, success=True,
                     message="已播放云端试听（%d 字节 / %d ms）"
                             % (r.get("bytes", 0), r.get("elapsed_ms", 0)))
            else:
                _set(phase="error", done=True, success=False,
                     error=r.get("error") or "云端试听失败")
        except Exception as e:
            _set(phase="error", done=True, success=False,
                 error="%s: %s" % (type(e).__name__, e))

    def test_voice(self, voice_cfg: dict, text: str = "") -> dict:
        """按给定语音配置试听一句：合成并播放（不保存、不影响当前角色）。

        【2026-09-10 修假加载】改为「异步任务 + 轮询」：后端起后台线程做
        挂载/合成/播放，立即返回 task_id；前端轮询 get_voice_test_status 拿真实
        进度（loading→synthesizing→playing→done），不再「立刻返回 success 但模型
        还没加载」导致 UI 提前弹「加载完成」、VRAM 与出声却晚于弹窗。
        """
        try:
            from ..voice.tts_backends import create_backend
            cfg = SimpleNamespace(**voice_cfg)
            sample = (text or "你好呀，我是达妮娅，很高兴认识你～").strip()[:200]
            with self._voice_task_lock:
                self._voice_task_seq += 1
                task_id = f"vt_{self._voice_task_seq}"
                self._voice_tasks[task_id] = {
                    "phase": "queued", "done": False, "success": None,
                    "error": "", "message": "已提交试听任务",
                }
            threading.Thread(
                target=self._run_voice_test,
                args=(task_id, cfg, sample),
                daemon=True,
                name=f"voice_test_{task_id}",
            ).start()
            return {"success": True, "task_id": task_id, "message": "正在准备试听…"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _run_voice_test(self, task_id: str, cfg: SimpleNamespace, sample: str) -> None:
        """后台线程：真正执行挂载/合成/播放，并实时更新任务状态供前端轮询。"""
        def _set(**kw):
            t = self._voice_tasks.get(task_id)
            if t is not None:
                t.update(kw)

        # 【2026-10-18】提前初始化：早退（无引擎等）时 finally 会引用这两个变量，
        # 未赋值会抛 UnboundLocalError，把真实错误覆盖成线程崩溃。
        backend = None
        preview_api_url = None

        try:
            from ..voice.tts_backends import create_backend
            # 【2026-10-18 修复·试听报 AttributeError】
            # 前端某些路径（角色页音色档、引导页）传来的 voice_cfg 不含 engine
            # → cfg.engine 直接抛 "'SimpleNamespace' object has no attribute 'engine'"。
            # 这里兜底推断：engine → mode → provider，都取不到就给明确错误。
            _engine = str(getattr(cfg, "engine", "") or "").strip()
            if not _engine:
                _mode = str(getattr(cfg, "mode", "") or "").strip()
                _prov = str(getattr(cfg, "provider", "") or "").strip()
                _engine = {
                    "dashscope": "dashscope_cosy",
                    "bailian": "dashscope_cosy",
                    "minimax": "minimax",
                    "fish": "fish_audio",
                    "openai": "openai_tts",
                }.get(_mode) or {
                    "dashscope": "dashscope_cosy",
                    "bailian": "dashscope_cosy",
                    "minimax": "minimax",
                    "fish_audio": "fish_audio",
                    "openai": "openai_tts",
                }.get(_prov) or ""
            if not _engine:
                _set(phase="error", done=True, success=False,
                     error="这条音色没有指定 TTS 引擎，无法试听。请在「新建音色档」里选一个服务商后重试。")
                return
            backend = create_backend(_engine, cfg)
            _set(phase="synthesizing", message=f"正在用 {getattr(backend, 'name', cfg.engine)} 合成…")

            # GPT-SoVITS：外部服务，先探测连通性，连不上立刻给明确提示
            if backend.name == "gpt-sovits":
                ok, detail = self._probe_url(getattr(cfg, "api_url", "http://127.0.0.1:9880"))
                if not ok:
                    _set(phase="error", done=True, success=False,
                         error=f"连接不到 GPT-SoVITS 服务：{detail}。请先启动 GPT-SoVITS（api.v2）并确认端口")
                    return

            # 本地克隆引擎：先挂载 + 等模型加载，再合成（这是「假加载」的根因修正点）
            if backend.name in self._LOCAL_MOUNT_ENGINES:
                display = self._LOCAL_CLONE_DISPLAY.get(backend.name, backend.name)
                api_url = getattr(cfg, "api_url", "") or f"http://127.0.0.1:{self._LOCAL_CLONE_PORT.get(backend.name, 9882)}"
                preview_api_url = api_url  # 供 finally 卸载释放显存
                # 【2026-09-10 按需 CUDA】千问 TTS 预览临时上 CUDA 提速；试完由 finally 调
                # /unload 释放，避免常驻占卡与本地双工 Comni 抢显存 OOM。其它本地引擎忽略 device。
                preview_device = "cuda" if backend.name == "qwen3tts" else None
                _set(phase="loading", message=f"正在加载 {display} 语音模型…（首次约 30s，请稍候）")
                mount = self._mount_tts
                if mount is not None:
                    result = mount(backend.name, api_url, display, device=preview_device)  # 阻塞直到模型真正加载完成
                    if not result.get("success"):
                        _set(phase="error", done=True, success=False,
                             error=f"{display} 语音模型加载失败：{result.get('error')}")
                        return
                else:
                    ok, detail = self._probe_url(api_url)
                    if not ok:
                        _set(phase="error", done=True, success=False,
                             error=f"连接不到 {display} 服务：{detail}。请先启动本地服务并确认端口")
                        return

            # 合成 → 取回 base64 音频（前端 webview 直接播放；本地引擎此刻模型已就绪）
            _set(phase="synthesizing", message=f"正在用 {getattr(backend, 'name', _engine)} 合成…")
            audio = self._synthesize_and_play(backend, sample)
            if not audio:
                err = getattr(self, "_last_tts_error", "") or "合成失败"
                _set(phase="error", done=True, success=False, error=err)
                return
            # phase=playing 同时携带 audio_b64/audio_ext，前端轮询拿到后由 <audio> 出声
            _set(phase="playing", message="正在播放试听…", done=True, success=True,
                 audio_b64=audio["b64"], audio_ext=audio["ext"])
        except Exception as e:
            _set(phase="error", done=True, success=False, error=str(e))
        finally:
            # 【按需卸载·根治常驻】试听/复刻本地克隆引擎后，真正释放资源：
            # 1) 卸显存（qwen3tts 走 CUDA；voxcpm/cosyvoice 走 CPU/内存模型）
            # 2) 延迟 kill 本地服务进程，避免模型权重常驻内存/显存（之前只有 qwen3tts
            #    卸显存、进程永不退出 → 出现「CPU 100% + 内存 47GB 占满」的卡炸现象）
            # 下次预览/对话再按需重挂（_mount_tts 会重新拉起）。
            # 【2026-10-18】早退（无引擎/参数错）时 backend 为 None，
            # 这里跳过卸载逻辑，避免 finally 抛 UnboundLocalError 覆盖真实错误。
            if backend is None:
                return
            if backend.name in self._LOCAL_MOUNT_ENGINES:
                port = self._LOCAL_CLONE_PORT.get(backend.name)
                if backend.name == "qwen3tts" and preview_api_url:
                    try:
                        self._unload_voice_service(preview_api_url)
                    except Exception:  # noqa: BLE001
                        pass
                if port:
                    def _shutdown_later(p=port, bname=backend.name):
                        import time as _t
                        _t.sleep(60)  # 保留 60s 供前端轮询音频
                        try:
                            self._kill_port_owner(p)
                            logger.info("%s 本地 TTS 服务试听结束，已自动停止释放资源", bname)
                        except Exception:  # noqa: BLE001
                            pass
                    threading.Thread(target=_shutdown_later, daemon=True).start()
            # 完成后保留 60s 供前端最后几次轮询，再清理
            def _cleanup():
                import time as _t
                _t.sleep(60)
                self._voice_tasks.pop(task_id, None)
            threading.Thread(target=_cleanup, daemon=True).start()

    def get_voice_test_status(self, task_id: str) -> dict:
        """前端轮询试听任务真实进度（task_id 由 test_voice 返回）。"""
        t = self._voice_tasks.get(task_id)
        if t is None:
            return {"success": False, "error": "任务不存在或已过期", "done": True,
                    "phase": "gone", "result_success": None}
        return {
            "success": True,
            "phase": t.get("phase"),
            "done": t.get("done"),
            "result_success": t.get("success"),
            "error": t.get("error", ""),
            "message": t.get("message", ""),
            # 合成完成的音频（base64），前端 webview 直接用 <audio> 播放出声
            "audio_b64": t.get("audio_b64", ""),
            "audio_ext": t.get("audio_ext", ""),
        }

    def _unload_voice_service(self, api_url: str) -> None:
        """POST /unload 卸载本地 TTS 模型释放显存（按需 CUDA：预览试听结束后调用）。

        仅释放模型、服务进程保持存活，下次 /load 可立即按需重挂（预览再点 / 对话出声都行）。
        失败静默（不影响试听结果上报）。
        """
        import urllib.request
        try:
            url = api_url.rstrip("/") + "/unload"
            req = urllib.request.Request(
                url, data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=10)
        except Exception:  # noqa: BLE001
            pass

    def _synthesize_and_play(self, backend, sample: str, play: bool = True) -> dict:
        """合成到临时文件并读成 base64，并通过【系统音频设备】真正出声（play=True）。

        【2026-09-10 修复·终版】之前让前端 <audio> 在 webview 内播放，但轮询回调里的
        play() 脱离用户点击手势，被 WebView2 自动播放策略静默拦截（.catch 吞错）→
        表现「播放完成却没声、也无失败」。桌面程序最稳的做法是后端用系统音频设备播：
        wav 走 winsound（Windows 标准库，直接到 OS 播放管线），mp3 等先用 PATH 里的
        ffmpeg 解码成 wav 再播。彻底绕开浏览器自动播放限制，所有预览引擎（qwen/edge/
        mimo/dashscope…）统一可靠出声。

        返回 {"b64": <ascii base64>, "ext": <wav|mp3|...>} 或 None（合成失败）。
        play=False 时仅返回 base64、清理临时文件、不出声（供不需出声的调用方）。
        """
        import base64 as _b64
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=backend.output_ext)
        tmp.close()
        try:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(backend.synthesize(sample, tmp.name))
            finally:
                loop.close()
        except Exception as e:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            logger.warning("试听合成失败: %s", e)
            self._last_tts_error = str(e)
            return None

        # 校验产出非空
        try:
            if os.path.getsize(tmp.name) <= 0:
                self._last_tts_error = "合成产出为空文件（模型可能未就绪）"
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass
                return None
        except OSError:
            self._last_tts_error = "合成文件不可读"
            return None

        ext = (backend.output_ext or ".wav").lstrip(".") or "wav"
        try:
            with open(tmp.name, "rb") as f:
                data = f.read()
            b64 = _b64.b64encode(data).decode("ascii")
        except Exception as e:
            self._last_tts_error = str(e)
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            return None

        # 真正出声：系统音频设备播放（wav 直播 / mp3 经 ffmpeg 解码后播）。
        # 临时文件生命周期交给 _play_audio_system 延迟清理，避免 winsound 异步加载前被删。
        if play:
            self._play_audio_system(tmp.name)
        else:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
        return {"b64": b64, "ext": ext}

    def _play_audio_system(self, path: str) -> None:
        """把合成好的音频通过【系统默认音频设备】播出去（绕过 webview 自动播放限制）。

        wav 优先用 winsound 同步播放（阻塞到真实出声，避免异步模式在后台线程被静默吞掉）；
        winsound 不可用/失败时回退 sounddevice（已确认主进程可用）。mp3 等先用 PATH 里的
        ffmpeg 解码成 wav 再播。每次播放都写 logs/voice_playback.log，失败不再静默，
        便于定位「合成成功却听不到声」这类问题。
        """
        import os
        import subprocess
        import sys
        import tempfile
        import threading
        import wave as _wave

        def _log(msg):
            line = f"[系统音频播放] {msg}"
            try:
                print(line, file=sys.stderr, flush=True)
            except Exception:  # noqa: BLE001
                pass
            try:
                os.makedirs("logs", exist_ok=True)
                with open("logs/voice_playback.log", "a", encoding="utf-8") as _f:
                    _f.write(line + "\n")
            except Exception:  # noqa: BLE001
                pass

        if not os.path.exists(path):
            _log(f"音频文件不存在: {path}")
            return
        _log(f"准备播放 {path} (大小 {os.path.getsize(path)} bytes)")

        target = path
        to_delete = []
        ext = os.path.splitext(path)[1].lower()
        if ext != ".wav":
            try:
                wf = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
                wf.close()
                _log(f"解码 {ext} -> wav: {wf.name}")
                subprocess.run(
                    ["ffmpeg", "-y", "-i", path, "-ar", "24000", "-ac", "1", wf.name],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
                )
                if os.path.getsize(wf.name) > 0:
                    target = wf.name
                    to_delete.append(wf.name)
                    _log(f"解码成功: {target}")
                else:
                    _log("ffmpeg 解码产出为空")
                    try:
                        os.unlink(wf.name)
                    except OSError:
                        pass
                    return
            except Exception as e:  # noqa: BLE001
                _log(f"ffmpeg 解码失败: {e}")
                return
        # 估算时长，播放结束后清理临时文件（给加载/播放留足余量）
        dur = 8.0
        try:
            with _wave.open(target, "rb") as w:
                fr = w.getframerate()
                n = w.getnframes()
                if fr > 0:
                    dur = n / fr
        except Exception as e:  # noqa: BLE001
            _log(f"读取 wav 时长失败: {e}")
            dur = 8.0

        played = False
        # ⓪ 【首选】ffplay 独立进程播放。
        # 【2026-09-10 延迟 30 秒真因】用户实测：软件内 python 进程用 winsound /
        # sounddevice(MME) 播放要**延迟约 30 秒**才出声（UI 早已显示"播放完成"）；
        # 而系统播放器双击同一文件、ffplay（同为独立进程）都**立即**有声。
        # ⇒ 问题在 **python 进程内的音频链路**（会话/驱动延迟），不在音频文件
        #   （已实测 wav 振幅正常 max=0.42）。故首选 **ffplay 独立进程**
        #   （-nodisp 无窗口、走系统默认设备、低延迟），彻底绕开 python 音频库。
        try:
            import shutil as _shutil
            _ffplay = _shutil.which("ffplay")
            if not _ffplay:
                for _c in (
                    os.path.expanduser(r"~\AppData\Local\Microsoft\WinGet\Links\ffplay.exe"),
                    os.path.join(os.path.dirname(sys.executable), "ffplay.exe"),
                ):
                    if os.path.exists(_c):
                        _ffplay = _c
                        break
            if _ffplay:
                _log(f"ffplay 独立进程播放 {target} (预估 {dur:.1f}s)")
                subprocess.run(
                    [_ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", target],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=dur + 60,
                    creationflags=(0x08000000 if os.name == "nt" else 0),  # CREATE_NO_WINDOW
                )
                _log("ffplay 播放完成")
                played = True
            else:
                _log("未找到 ffplay，回退 python 音频库")
        except Exception as e:  # noqa: BLE001
            _log(f"ffplay 播放失败: {e}")

        # ① 回退：sounddevice。
        #    要真正出声须走 **WASAPI**（与系统声音同路径）+ **重采样到设备原生采样率**
        #    （本机 WASAPI K20 Pro = 48000Hz；24kHz 直送会报 Invalid sample rate -9997）。
        #    用标准库 wave 读 + numpy 转 float32，避免依赖 soundfile（主进程未安装）。
        if not played:
            try:
                import numpy as np
                import sounddevice as _sd

                with _wave.open(target, "rb") as w:
                    _nch = w.getnchannels()
                    _sw = w.getsampwidth()
                    _sr = w.getframerate()
                    _raw = w.readframes(w.getnframes())
                if _sw == 2:
                    _a = np.frombuffer(_raw, dtype=np.int16).astype(np.float32) / 32768.0
                elif _sw == 1:
                    _a = np.frombuffer(_raw, dtype=np.uint8).astype(np.float32) / 128.0 - 1.0
                elif _sw == 4:
                    _a = np.frombuffer(_raw, dtype=np.int32).astype(np.float32) / 2147483648.0
                elif _sw == 8:
                    _a = np.frombuffer(_raw, dtype=np.float64).astype(np.float32)
                else:
                    _a = np.frombuffer(_raw, dtype=np.uint8).astype(np.float32) / 255.0
                if _nch > 1:
                    _a = _a.reshape(-1, _nch)[:, 0]  # 取单声道

                def _resample(x, sr_in, sr_out):
                    if sr_in == sr_out or sr_out <= 0:
                        return x.astype(np.float32)
                    n = int(round(len(x) * sr_out / float(sr_in)))
                    if n <= 1:
                        return x.astype(np.float32)
                    return np.interp(np.linspace(0, len(x) - 1, n),
                                     np.arange(len(x)), x).astype(np.float32)

                _cands = []
                try:
                    for _ap in _sd.query_hostapis():
                        if "WASAPI" in str(_ap.get("name") or ""):
                            _d = _ap.get("default_output_device")
                            if _d is not None and int(_d) >= 0:
                                _cands.append((int(_d), "WASAPI"))
                            break
                except Exception:  # noqa: BLE001
                    pass
                try:
                    _cands.append((int(_sd.default.device[1]), "默认(MME)"))
                except Exception:  # noqa: BLE001
                    pass

                for _dev, _tag in _cands:
                    try:
                        _dev_sr = int(_sd.query_devices(_dev)["default_samplerate"])
                        _data = _resample(_a, _sr, _dev_sr)
                        _log(f"sounddevice 出声[{_tag}] dev={_dev} {_dev_sr}Hz "
                             f"({len(_data) / _dev_sr:.1f}s)")
                        _sd.play(_data, _dev_sr, device=_dev)
                        _sd.wait()
                        _log(f"sounddevice[{_tag}] 播放完成")
                        played = True
                        break
                    except Exception as _e:  # noqa: BLE001
                        _log(f"sounddevice[{_tag}] dev={_dev} 失败: {_e}")
                        try:
                            _sd.stop()
                        except Exception:  # noqa: BLE001
                            pass
            except Exception as e:  # noqa: BLE001
                _log(f"sounddevice 播放失败: {e}")

        # ② 兜底：winsound 同步播放（仅当 sounddevice 失败）
        if not played:
            try:
                import winsound
                _log(f"winsound 同步播放 {target} (兜底)")
                winsound.PlaySound(target, winsound.SND_FILENAME)
                _log("winsound 播放完成")
                played = True
            except Exception as e:  # noqa: BLE001
                _log(f"winsound 兜底播放失败: {e}")

        if not played:
            _log("所有播放方式均失败（请检查系统默认播放设备/音量/是否被静音）")
        delay = min(max(dur + 4.0, 5.0), 120.0)

        def _clean():
            import time as _t
            _t.sleep(delay)
            for p in [path] + to_delete:
                try:
                    os.unlink(p)
                except OSError:
                    pass

        threading.Thread(target=_clean, daemon=True).start()

    def open_minimax_page(self, page: str = "register") -> dict:
        """用系统浏览器打开 MiniMax 开放平台指定页面（接入向导引导用）。

        page: register=注册/登录  group=账户信息(GroupId)  key=接口密钥  clone=Voice Cloning 调试台
        """
        import webbrowser
        urls = {
            "register": "https://platform.minimaxi.com/login",
            "verify": "https://platform.minimaxi.com/console/team-info",
            "group": "https://platform.minimaxi.com/user-center/basic-information",
            "key": "https://platform.minimaxi.com/user-center/basic-information/interface-key",
            "clone": "https://platform.minimaxi.com/examination-center/voice-experience-center/voiceCloning",
        }
        url = urls.get(page, urls["register"])
        try:
            ok = webbrowser.open(url)
        except Exception as e:  # noqa: BLE001
            logger.warning("打开 MiniMax 页面失败: %s", e)
            return {"success": False, "error": str(e)}
        return {"success": ok, "url": url}

    # ==========================================================
    # 百炼控制台「应用内嵌浏览器」（2026-09-29）
    # 真数字被阿里云控制台登录态锁死，唯一低摩擦路径 = 让用户在 App 内登录一次。
    # 窗口由启动期 create_bailian_console_window() 建好（隐藏），这里只 show + 懒加载。
    # ==========================================================
    def open_bailian_console(self, url_key: str = "free_quota") -> dict:
        """在应用内打开百炼控制台窗口（真实额度 / 用量 / 开「用完即停」）。"""
        win = STATE.bailian_console_win
        if win is None:
            # 兜底：理论上启动期已建；若缺失才尝试重建（非 GUI 线程有风险，仅兜底）。
            try:
                win = create_bailian_console_window(self)
            except Exception as e:  # noqa: BLE001
                return {"success": False, "error": f"控制台窗口不可用：{e}"}
        if win is None:
            return {"success": False, "error": "控制台窗口创建失败"}
        url = _BAILIAN_CONSOLE_URLS.get(url_key, _BAILIAN_CONSOLE_URLS["free_quota"])
        try:
            # 首次或切换页面时懒加载；记录当前已加载 URL 以免重复导航。
            if getattr(win, "_bailian_loaded_url", None) != url:
                win.load_url(url)
                win._bailian_loaded_url = url
            win.restore()
            win.show()
            # Step2：首次打开后启动 60s 周期注入（只起一次；失败静默不影响主功能）。
            if STATE.bailian_inject_timer is None:
                try:
                    t = threading.Timer(3.0, _bailian_inject_loop, args=(win,))
                    t.daemon = True
                    STATE.bailian_inject_timer = t
                    t.start()
                    logger.info("[百炼控制台] 取数注入循环已启动（60s/次）")
                except Exception:  # noqa: BLE001
                    STATE.bailian_inject_timer = None
            return {"success": True, "url": url}
        except Exception as e:  # noqa: BLE001
            logger.exception("[百炼控制台] 打开失败")
            return {"success": False, "error": str(e)}

    def get_bailian_real_quota(self) -> dict:
        """前端轮询：返回注入脚本最近一次从控制台页抓到的真实额度快照。"""
        d = _BAILIAN_REAL_QUOTA.get("data")
        if not d:
            return {"success": True, "has_data": False}
        try:
            age = int(max(0.0, time.time() - float(d.get("_recv_ts") or 0.0)))
        except Exception:  # noqa: BLE001
            age = 0
        return {"success": True, "has_data": True, "age": age,
                "hits": d.get("hits") or [], "login": bool(d.get("login")),
                "url": d.get("url") or ""}

    def open_bailian_console_external(self, url_key: str = "free_quota") -> dict:
        """兜底：用系统浏览器打开百炼控制台对应页面（用户在 App 内窗口外也想看时）。"""
        import webbrowser
        url = _BAILIAN_CONSOLE_URLS.get(url_key, _BAILIAN_CONSOLE_URLS["free_quota"])
        try:
            ok = webbrowser.open(url)
        except Exception as e:  # noqa: BLE001
            logger.warning("打开百炼控制台页面失败: %s", e)
            return {"success": False, "error": str(e)}
        return {"success": ok, "url": url}

    def get_bailian_console_status(self) -> dict:
        """前端额度卡轮询：百炼控制台内嵌窗口是否可用、当前已加载哪个页面。"""
        win = STATE.bailian_console_win
        if win is None:
            return {"available": False}
        return {
            "available": True,
            "loaded_url": getattr(win, "_bailian_loaded_url", None),
            "shown": bool(getattr(win, "shown", False)),
        }

    def clone_minimax_voice(self, voice_cfg: dict, file_name: str, file_b64: str,
                            new_voice_id: str = "") -> dict:
        """MiniMax 本地音频快速复刻：上传音频 → voice_clone 生成自定义音色。

        前端传 base64（pywebview 不能直接传文件对象），后端解出写临时文件上传。
        返回 {success, voice_id}；成功后方可把 voice_id 回填到音色框。
        注意：需要平台个人/企业实名认证（未认证接口报 2038）。
        """
        try:
            import base64
            import re
            import httpx

            cfg = SimpleNamespace(**voice_cfg)
            api_key = getattr(cfg, "api_key", "") or ""
            group_id = getattr(cfg, "group_id", "") or ""
            if not api_key or not group_id:
                return {"success": False, "error": "请先填 MiniMax API Key 和 GroupId"}
            if not file_b64:
                return {"success": False, "error": "未选择音频文件"}

            # 打码日志：帮排查 Key 是否带 "Bearer " 前缀 / 空格（常见复制坑）
            if api_key.lower().startswith("bearer"):
                logger.warning("MiniMax API Key 疑似带了 'Bearer ' 前缀（len=%d, head=%.12s）——请去掉前缀只填密钥本体",
                               len(api_key), api_key[:12])
            logger.info("MiniMax 克隆请求: group_id=%s, api_key=%.8s...(len=%d), file=%s",
                        group_id, api_key, len(api_key), file_name or "")

            try:
                audio_bytes = base64.b64decode(file_b64)
            except Exception:
                return {"success": False, "error": "音频数据解码失败"}
            if len(audio_bytes) > 20 * 1024 * 1024:
                return {"success": False, "error": "音频不能超过 20MB（要求 10 秒~5 分钟）"}
            suffix = os.path.splitext(file_name or "")[1].lower() or ".mp3"
            if suffix not in (".mp3", ".m4a", ".wav"):
                return {"success": False, "error": "仅支持 mp3 / m4a / wav 格式"}

            # 自定义 voice_id 规范：8~256 位、首字符字母、仅字母数字-_、末位非-_
            v_id = (new_voice_id or "").strip()
            v_id = re.sub(r"[^0-9a-zA-Z_-]", "", v_id)
            if not v_id:
                v_id = "clone_" + time.strftime("%Y%m%d%H%M%S")
            if not v_id[0].isalpha():
                v_id = "clone_" + v_id
            v_id = v_id[:256].rstrip("-_")
            if len(v_id) < 8:
                v_id = ("clone" + v_id)[:256].rstrip("-_")

            headers = {"Authorization": f"Bearer {api_key}"}
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name
            try:
                # 1) 上传音频 → file_id（官方 API 参考：该接口不拼 GroupId，仅 Bearer 鉴权）
                with open(tmp_path, "rb") as f:
                    up = httpx.post(
                        "https://api.minimaxi.com/v1/files/upload",
                        headers=headers,
                        data={"purpose": "voice_clone"},
                        files={"file": (file_name or "clone" + suffix, f,
                                        "application/octet-stream")},
                        timeout=120,
                    )
                up_data = up.json()
                base_resp = up_data.get("base_resp", {})
                if up.status_code >= 400 or base_resp.get("status_code", 0) != 0:
                    err = self._minimax_clone_err("上传音频失败", base_resp, up.status_code)
                    logger.warning("MiniMax 上传失败: %s | 原始响应: %s", err, json.dumps(up_data, ensure_ascii=False)[:800])
                    return {"success": False, "error": err}
                file_id = up_data.get("file", {}).get("file_id")
                if not file_id:
                    logger.warning("MiniMax 上传返回无 file_id: %s", json.dumps(up_data, ensure_ascii=False)[:800])
                    return {"success": False, "error": "上传音频失败：未返回 file_id"}

                # 2) 语音复刻（text 为试听文本，返回 demo_audio 链接）
                model = getattr(cfg, "model", "") or "speech-02-turbo"
                cl = httpx.post(
                    "https://api.minimaxi.com/v1/voice_clone",
                    headers={**headers, "Content-Type": "application/json"},
                    json={
                        "file_id": file_id,
                        "voice_id": v_id,
                        "text": "你好，我是刚刚克隆出来的声音，很高兴认识你～",
                        "model": model,
                    },
                    timeout=120,
                )
                cl_data = cl.json()
                base_resp = cl_data.get("base_resp", {})
                if cl.status_code >= 400 or base_resp.get("status_code", 0) != 0:
                    err = self._minimax_clone_err("克隆失败", base_resp, cl.status_code)
                    logger.warning("MiniMax 克隆失败: %s | 原始响应: %s", err, json.dumps(cl_data, ensure_ascii=False)[:800])
                    return {"success": False, "error": err}
                return {
                    "success": True,
                    "voice_id": v_id,
                    "demo_audio": cl_data.get("demo_audio", ""),
                    "message": f"克隆成功：{v_id}。已回填到上方音色框，保存角色后即可使用"
                    "（复刻音色 7 天内被首次合成才会永久保留）",
                }
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        except Exception as e:
            logger.exception("MiniMax 克隆失败")
            return {"success": False, "error": str(e)}

    @staticmethod
    def _minimax_clone_err(prefix: str, base_resp: dict, http_status: int) -> str:
        """拼 MiniMax 复刻接口错误信息，附带实名认证提示。"""
        code = base_resp.get("status_code", 0)
        msg = base_resp.get("status_msg", "") or ""
        if code == 2038:
            msg += "（无复刻权限：需先在 platform.minimaxi.com 完成个人/企业实名认证）"
        elif code == 1008:
            msg += "（余额不足：MiniMax 按量计费，请在 platform.minimaxi.com『充值中心』充值后重试）"
        elif code == 1004:
            msg += ("（API Key 与 GroupId 不匹配：请确认 Key 是在『账户管理→接口密钥』新建的按量付费 API Key，"
                    "不是 Token Plan 订阅 Key；且 GroupId 复制自『账户信息』同一账号）")
        return f"{prefix} (HTTP {http_status}, code={code}): {msg}".strip()

    @staticmethod
    def _probe_url(url: str, timeout: float = 2.0) -> tuple[bool, str]:
        """TCP 探测 http 服务是否可达：返回 (可达, 失败原因)。"""
        import socket
        from urllib.parse import urlparse
        u = urlparse(url if "://" in url else "http://" + url)
        host = u.hostname or "127.0.0.1"
        port = u.port or 9880
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True, ""
        except OSError as e:
            return False, f"{host}:{port} ({e.strerror or e})"

    def pick_audio_file(self) -> dict:
        """弹出系统文件选择框让用户挑选参考音频（后台线程跑 tkinter）。"""
        import queue
        q: "queue.Queue[str]" = queue.Queue()

        def _pick() -> None:
            try:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
                try:
                    path = filedialog.askopenfilename(
                        title="选择参考音频（3~10秒清晰人声，wav/mp3/flac）",
                        filetypes=[
                            ("音频文件", "*.wav *.mp3 *.flac *.ogg *.m4a"),
                            ("所有文件", "*.*"),
                        ],
                    )
                finally:
                    root.destroy()
                q.put(path or "")
            except Exception as e:
                q.put(f"__ERR__:{e}")

        threading.Thread(target=_pick, daemon=True).start()
        try:
            path = q.get(timeout=120)
        except Exception:
            return {"success": False, "error": "文件选择超时，请重试"}
        if path.startswith("__ERR__:"):
            return {"success": False, "error": path[len("__ERR__:"):]}
        if not path:
            return {"success": False, "error": ""}  # 用户取消，不算错误
        return {"success": True, "path": path, "duration": self._audio_duration(path)}

    @staticmethod
    def _audio_duration(path: str) -> Optional[float]:
        """用 ffprobe 获取音频时长（秒）；失败返回 None。"""
        import subprocess
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", path],
                capture_output=True, text=True, errors="replace", timeout=10,
            )
            return float(r.stdout.strip())
        except Exception:
            return None

    def trim_audio(self, path: str) -> dict:
        """把参考音频裁到 GPT-SoVITS 要求的 3~10 秒内（保留语音开头 9 秒）。

        输出到原文件同目录 *_trim.wav，原文件不动。
        """
        import subprocess
        path = (path or "").strip().strip('"')
        if not path:
            return {"success": False, "error": "没有音频路径"}
        if not os.path.exists(path):
            return {"success": False, "error": f"文件不存在：{path}"}
        dur = self._audio_duration(path)
        if dur is not None and 3 <= dur <= 10:
            return {"success": True, "path": path, "trimmed": False, "duration": dur}
        out = os.path.splitext(path)[0] + "_trim.wav"
        try:
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", path, "-t", "9.0",
                 "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", out],
                capture_output=True, text=True, errors="replace", timeout=60,
            )
            if not os.path.exists(out):
                return {"success": False, "error": "裁剪失败：" + (r.stderr or r.stdout or "")[-300:]}
        except Exception as e:
            return {"success": False, "error": f"裁剪失败：{e}"}
        return {"success": True, "path": out, "trimmed": True, "duration": self._audio_duration(out)}

    # ======== 试聊一句（用表单里当前的人设，不动运行时） ========
    def test_persona(self, char_data: dict) -> dict:
        """用给定人设试聊一句：走运行时 LLM 生成一句话回复（不保存、不打断对话）。"""
        if self._dialogue is None or self._loop is None:
            return {"success": False, "error": "运行时未注入，无法试聊（请先启动主程序）"}
        llm = getattr(self._dialogue, "_llm", None)
        if llm is None or not llm.available:
            return {"success": False, "error": "LLM 不可用（未拉取模型或未配置）"}

        from ..companion.persona import Persona
        from ..llm.decision_parser import parse as parse_decision
        from ..llm.prompts import build_system, build_user_turn
        persona = Persona(
            name=char_data.get("name", "AI"),
            character=char_data.get("character", "") or "",
            speaking_style=char_data.get("speaking_style", "") or "",
            background=char_data.get("background", "") or "",
        )
        messages = [
            {"role": "system", "content": build_system(persona)},
            {"role": "user", "content": build_user_turn("你好呀！", activity_summary="")},
        ]
        try:
            raw = self._run_coro(llm.chat(messages), timeout=60)
            if not raw:
                return {"success": False, "error": "LLM 返回为空"}
            text = parse_decision(raw).get("text", "").strip() or raw.strip()[:200]
            return {"success": True, "text": text}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _toast_if_any(self, message: str, toast_type: str = "error") -> None:
        """后台线程安全地把提示送到前端（尽力而为）。toast_type: success/error/loading。

        【防卡死·2026-09-03】evaluate_js 在渲染进程挂死时会阻塞调用线程，改提交到
        推送线程池，避免任何线程被卡。
        """
        try:
            import webview
            windows = getattr(webview, "windows", [])
            if not windows:
                return
            js = ("window.showToast && showToast(%s, %s)"
                  % (json.dumps(message, ensure_ascii=False), json.dumps(toast_type)))
            win = windows[0]
            # 【2026-09-04 18:28 深层根因修复】不再经线程池 + 阻塞式 evaluate_js：
            # 直接用主窗 CoreWebView2 的 ExecuteScriptAsync（fire-and-forget，零阻塞、
            # 零线程创建——线程池 submit 会动态开新线程，已实锤在主循环线程上挂死）。
            ctrl = STATE.wv2_controls.get(getattr(win, "uid", None))
            if ctrl is not None:
                # 【2026-09-05 冻结修复】ctrl.CoreWebView2 / cv2.IsConnected 是跨套间 COM
                # 调用，在后台线程直接读会触发 STA 同步封送阻塞（同 _wv2_broadcast 根因）。
                # 整体封送进 BeginInvoke 在 GUI 线程执行；ExecuteScriptAsync 本身
                # fire-and-forget 安全。调用线程绝不碰 COM 属性，避免卡死。
                def _toast_deliver():
                    try:
                        cv2 = getattr(ctrl, "CoreWebView2", None)
                        if cv2 is None:
                            return
                        if not getattr(cv2, "IsConnected", True):
                            return
                        cv2.ExecuteScriptAsync(js)
                    except Exception:  # noqa: BLE001
                        pass

                try:
                    from System import Action as _Act
                    ctrl.BeginInvoke(_Act(_toast_deliver))
                except Exception:  # noqa: BLE001
                    pass
            else:
                # 罕见兜底：主窗控件未注册时退回线程池（保持旧行为）
                def _do():
                    try:
                        win.evaluate_js(js)
                    except Exception:  # noqa: BLE001
                        pass

                STATE.wv2_bc_executor.submit(_do)
        except Exception:
            pass

    # 【2026-10-21 删】这里原来有 `get_backend_options()`，返回 4 条写死的 TTS 引擎：
    # `pyttsx3` **出现两次**（"系统语音(本地离线)" / "pyttsx3(离线降级)"），而真正的
    # 引擎表 `src/models/protocols.py: TTS_ENGINES` 有 **10** 个（由 check_models_layer
    # 守着，10-16 已按发布口径摘掉 edge-tts）。它既不是事实来源、也没人调 —— 把它接到
    # 下拉框上，用户会看到重复项和缺失项。真要动态选项，从 `TTS_ENGINES` 生成。

    # ======== LLM 模型设置（自定义模型） ========
    def get_llm_settings(self) -> dict:
        """返回当前 LLM 提供商配置 + 运行时状态。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        llm_ns = getattr(self._cfg.ns, "llm", None)
        if llm_ns is None:
            return {"success": False, "error": "config.yaml 缺少 llm 配置段"}
        openai_cfg = getattr(llm_ns, "openai", None)
        mc = getattr(llm_ns, "minicpm", None)
        st = self._cfg.runtime_status
        return {
            "success": True,
            "provider": getattr(llm_ns, "provider", "ollama"),
            "openai": {
                "base_url": getattr(openai_cfg, "base_url", "") or "",
                "api_key": getattr(openai_cfg, "api_key", "") or "",
                "model": getattr(openai_cfg, "model", "") or "",
                "vision": bool(getattr(openai_cfg, "vision", True)),
                "timeout_s": getattr(openai_cfg, "timeout_s", 60),
            },
            "minicpm": {
                "endpoint": getattr(mc, "endpoint", "") or "",
                "max_new_tokens": getattr(mc, "max_new_tokens", 512),
                "timeout_s": getattr(mc, "timeout_s", 60),
            },
            "text_chat": self._text_chat_summary(),
            "runtime": {
                "llm_ok": bool(st.ollama_ok),
                "vision_ok": bool(st.vision_model_ok),
                "fallback_ok": bool(st.fallback_model_ok),
                "provider": st.llm_provider or "ollama",
                "degradations": list(st.degradations),
            },
        }

    def save_llm_settings(self, data: dict) -> dict:
        """保存 LLM 提供商配置并热切换生效（无需重启）。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            raw = self._cfg.raw
            if not isinstance(raw.get("llm"), dict):
                raw["llm"] = {}
            llm_raw = raw["llm"]

            provider = (data.get("provider") or "ollama").strip().lower()
            # 2026-09-13: 放宽白名单——除 ollama/minicpm 外的任意厂商一律按
            # OpenAI 兼容透传（base_url + api_key + model），由 text_chat 路由实际分发。
            # 支持智谱/DeepSeek/硅基/OpenRouter/百炼/Kimi/火山/OpenAI 等。
            # MiniMax(Anthropic 协议)暂不支持，引导页会标注。
            if provider not in ("ollama", "minicpm"):
                provider = "openai"
            llm_raw["provider"] = provider

            openai = data.get("openai") or {}
            o = llm_raw.setdefault("openai", {})
            o.update({
                "base_url": (openai.get("base_url") or "").strip(),
                "api_key": (openai.get("api_key") or "").strip(),
                "model": (openai.get("model") or "").strip(),
                "vision": bool(openai.get("vision", True)),
                "timeout_s": int(openai.get("timeout_s") or 60),
            })

            mc_data = data.get("minicpm") or {}
            mc = llm_raw.setdefault("minicpm", {})
            mc.update({
                "endpoint": (mc_data.get("endpoint") or "").strip(),
                "max_new_tokens": int(mc_data.get("max_new_tokens") or 512),
                "timeout_s": int(mc_data.get("timeout_s") or 60),
            })

            # 【2026-09-30 方案A】引导页/设置681卡绑的 LLM 同步进 text_chat.providers，
            # 让聊天选择器直接认到，消除“绑了不认”的死路（选择器只读 text_chat.providers）。
            if provider in ("ollama", "openai"):
                # 【2026-10-04 18:55 治"改一处爆死"】此前无条件把核心 LLM 同步成一条
                # `onboarding-llm` 塞进 text_chat.providers —— 用户只改了「核心 LLM」，
                # 聊天端口列表里就凭空多出一条、且它会参与自动轮询/冷却，把"改核心 LLM"
                # 变成"顺带改了聊天路由"。现在：
                #   ① 同一开关 `text_chat.sync_llm_openai` 统一控制双向同步（默认开，
                #      保持既有行为）；关掉后两条配置彻底解耦，谁也不动谁。
                #   ② provider 统一写规范值 "openai"（`text_chat_router` 与
                #      `vision_router` 都按规范值分派；此前写别名会让
                #      "无 key 跳过"保护失效 → 空 key 打满冷却）。
                _sync = data.get("sync_llm_openai")
                if _sync is None:
                    _sync = bool((raw.get("text_chat") or {}).get("sync_llm_openai", True))
                _tc = raw.setdefault("text_chat", {})
                _provs = _tc.get("providers") or []
                _provs = [x for x in _provs if isinstance(x, dict) and x.get("id") != "onboarding-llm"]
                if _sync and provider == "ollama":
                    _ob = {"id": "onboarding-llm", "name": "本地 Ollama（引导绑定）",
                           "provider": "ollama", "model": (o.get("model") or "").strip(),
                           "timeout_s": int(o.get("timeout_s") or 60)}
                    if o.get("base_url"):
                        _ob["base_url"] = str(o["base_url"]).rstrip("/")
                    _provs.append(_ob)
                elif _sync and provider == "openai":
                    # openai 兼容（百炼/智谱/DeepSeek/OpenAI…）
                    if (o.get("base_url") or "").strip() and (o.get("model") or "").strip():
                        _provs.append({
                            "id": "onboarding-llm",
                            "name": "云端 LLM（引导绑定）",
                            "provider": "openai",
                            "base_url": str(o["base_url"]).rstrip("/"),
                            "api_key": (o.get("api_key") or "").strip(),
                            "model": (o.get("model") or "").strip(),
                            "timeout_s": int(o.get("timeout_s") or 60),
                        })
                _tc["providers"] = _provs

            # 【2026-10-08 P1·key_unify】本条路径此前只写 llm.openai.api_key 一格。
            # 用户在此填的百炼 Key 会与引导页扩散来的 Key 并存 → 同账号多处不同 Key。
            _uk = (o.get("api_key") or "").strip()
            if _looks_like_bailian_key(_uk):
                _unify_bailian_key(raw, _uk, source="save_llm_settings")
            # 【2026-10-08 P3·写入收口】抹空白 + Key 收敛 + 写后自检（永不抛异常）
            try:
                from ..models import commit_model_write as _cmw2
                _cmw2(raw, changed="llm.openai", source="save_llm_settings")
            except Exception:  # noqa: BLE001
                logger.warning("写入收口失败（不影响保存）", exc_info=True)

            self._save_config()
            # 重建命名空间，让 create_llm 读到新配置
            from ..config import _to_ns
            self._cfg.ns = _to_ns(raw)
            # 热切换：替换 dialogue / analyzer / proactive 的 LLM 引用
            applied = self._apply_llm_live()
            return {"success": True, "applied_live": applied}
        except Exception as e:
            logger.exception("保存 LLM 设置失败")
            return {"success": False, "error": str(e)}

    def _text_chat_summary(self) -> dict:
        """汇总 text_chat.providers 给前端渲染/编辑（api_key 仅回 has_key，不泄密）。"""
        tc = getattr(self._cfg.ns, "text_chat", None) if self._cfg else None
        tc = tc or {}
        providers_out = []
        try:
            from ..llm import model_caps as mc
        except Exception:  # noqa: BLE001
            mc = None
        for p in (getattr(tc, "providers", None) or []):
            pd = {k: getattr(p, k, "") for k in
                  ("id", "name", "provider", "base_url", "model", "timeout_s")}
            pd["has_key"] = bool(getattr(p, "api_key", ""))
            explicit = getattr(p, "caps", None) if isinstance(getattr(p, "caps", None), dict) else None
            if mc is not None:
                pd["caps"] = mc.infer_caps(
                    model=pd.get("model", ""), name=pd.get("name", ""),
                    provider=pd.get("provider", ""), explicit=explicit)
            providers_out.append(pd)
        return {
            "mode": getattr(tc, "mode", "auto"),
            "manual_provider": getattr(tc, "manual_provider", ""),
            "providers": providers_out,
        }

    def save_text_chat_providers(self, data: dict = None) -> dict:
        """保存纯文本聊天多厂商配置（text_chat.providers），支持任意 OpenAI 兼容厂商并存。

        引导页「添加云端 LLM」与设置·高级「模型与 API」共用：前端传完整 providers 列表
        （每项含 id/name/provider/base_url/api_key/model/timeout_s）+ 可选 manual_provider/mode。
        provider 仅 openai / openai-compatible / ollama 三种；其余厂商（智谱/DeepSeek/硅基/
        OpenRouter/百炼/Kimi/火山/OpenAI 等）一律走 openai-compatible 透传 base_url。
        MiniMax(Anthropic 协议)暂不支持。
        保存后重建命名空间并热切换 TextChatRouter（无需重启）。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        if not isinstance(data, dict):
            return {"success": False, "error": "参数格式错误"}
        try:
            raw = self._cfg.raw
            tc = raw.setdefault("text_chat", {})
            providers_in = data.get("providers") or []
            if not isinstance(providers_in, list):
                return {"success": False, "error": "providers 必须是列表"}
            cleaned = []
            seen_ids = set()
            for i, p in enumerate(providers_in):
                if not isinstance(p, dict):
                    return {"success": False, "error": f"第 {i + 1} 个厂商格式错误"}
                pid = (p.get("id") or "").strip()
                if not pid:
                    return {"success": False, "error": f"第 {i + 1} 个厂商缺少 id"}
                if pid in seen_ids:
                    return {"success": False, "error": f"厂商 id 重复: {pid}"}
                seen_ids.add(pid)
                provider = (p.get("provider") or "openai-compatible").strip().lower()
                if provider not in ("openai", "openai-compatible", "ollama"):
                    provider = "openai-compatible"
                name = (p.get("name") or pid).strip()
                model = (p.get("model") or "").strip()
                if not model:
                    return {"success": False, "error": f"厂商 {name} 缺少 model"}
                item = {
                    "id": pid,
                    "name": name,
                    "provider": provider,
                    "model": model,
                    "timeout_s": int(p.get("timeout_s") or 30),
                }
                # 透传用户在设置页手填的能力字段（caps），驱动 UI 标签与自动调度
                if isinstance(p.get("caps"), dict):
                    item["caps"] = {
                        "inputs": p["caps"].get("inputs") or [],
                        "outputs": p["caps"].get("outputs") or [],
                        "features": p["caps"].get("features") or [],
                    }
                if provider == "ollama":
                    if p.get("base_url"):
                        item["base_url"] = (p["base_url"]).rstrip("/")
                else:
                    base_url = (p.get("base_url") or "").strip().rstrip("/")
                    if not base_url:
                        return {"success": False,
                                "error": f"厂商 {name} 缺少 base_url（OpenAI 兼容接口地址）"}
                    item["base_url"] = base_url
                    if "api_key" in p:
                        item["api_key"] = (p.get("api_key") or "").strip()
                    else:
                        # 编辑时前端未传 api_key（留空）→ 保留原 key，避免误清空
                        _old_providers = (raw.get("text_chat", {}) or {}).get("providers", []) or []
                        _old = next((x for x in _old_providers if x.get("id") == pid), None)
                        item["api_key"] = (_old.get("api_key") or "") if _old else ""
                cleaned.append(item)
            tc["providers"] = cleaned
            # 【2026-09-30 方案A】从首个带 key 的 openai 兼容端口反向派生 llm.openai，
            # 让核心 LLM（决策内核/agent）也用用户绑的模型，两条路径收敛到同一真相源。
            # 【2026-10-04 18:55 治"改一处爆死"】此前**无条件**反写，且连带把
            # `vision` 强设为 True —— 用户只想改"聊天默认端口"，却会静默改掉核心 LLM
            # 的 base_url/model/vision，而 `llm.openai` 是 agent/cascade 的回落源、
            # 且历史故障证明 `llm.openai.vision=false` 会连坐关掉全部视觉模型。
            # 现在：①尊重条目自身的 vision 设置（缺省才 True）；
            #      ②可关（`sync_llm_openai: false`），默认保持开启以维持既有行为。
            _sync = data.get("sync_llm_openai")
            if _sync is None:
                _sync = bool((raw.get("text_chat") or {}).get("sync_llm_openai", True))
            if _sync:
                _fk = next((x for x in cleaned
                            if x.get("provider") != "ollama" and x.get("api_key")), None)
                if _fk:
                    _llm = raw.setdefault("llm", {})
                    _llm["provider"] = "openai"
                    _oo = _llm.setdefault("openai", {})
                    _oo["base_url"] = (_fk.get("base_url") or "").strip()
                    _oo["api_key"] = (_fk.get("api_key") or "").strip()
                    _oo["model"] = (_fk.get("model") or "").strip()
                    # 尊重条目自身声明的视觉能力，不再无条件强开
                    if _fk.get("vision") is not None:
                        _oo["vision"] = bool(_fk.get("vision"))
                    else:
                        _oo.setdefault("vision", True)
                    _oo["timeout_s"] = int(_fk.get("timeout_s") or 60)
            mode = (data.get("mode") or tc.get("mode") or "auto").strip().lower()
            if mode not in ("auto", "manual"):
                mode = "auto"
            tc["mode"] = mode
            mp = (data.get("manual_provider") or "").strip()
            if mp:
                if mp not in seen_ids:
                    return {"success": False, "error": f"默认厂商不存在: {mp}"}
                tc["manual_provider"] = mp
            elif mode == "manual" and tc.get("manual_provider") not in seen_ids:
                tc["manual_provider"] = cleaned[0]["id"] if cleaned else ""
            # 【2026-10-08 P1·key_unify】本条路径此前只写自己那一格（text_chat.providers[i].api_key），
            # 与引导页扩散来的 Key 并存 → 同账号多处不同 Key。开着开关时统一为本次填入的百炼 Key。
            # 取「本次提交里第一个合法百炼 Key」，空/非百炼 Key 一律不动（保守）。
            for _p in cleaned:
                _pk = (_p.get("api_key") or "").strip()
                if _looks_like_bailian_key(_pk):
                    _unify_bailian_key(raw, _pk, source="save_text_chat_providers")
                    break
            # 【2026-10-08 P3·写入收口】统一收口：抹空白 + Key 收敛 + 写后自检。
            # 内部**永不抛异常**，且默认零行为（key_unify 关闭时只抹空白）。
            _wr = None
            try:
                from ..models import commit_model_write as _cmw
                _wr = _cmw(raw, changed="text_chat.providers", source="save_text_chat_providers")
            except Exception:  # noqa: BLE001
                logger.warning("写入收口失败（不影响保存）", exc_info=True)
            self._save_config()
            from ..config import _to_ns
            self._cfg.ns = _to_ns(raw)
            applied = self._apply_llm_live()
            out_providers = []
            for it in cleaned:
                po = {k: it[k] for k in ("id", "name", "provider", "base_url", "model", "timeout_s")}
                po["has_key"] = bool(it.get("api_key"))
                if isinstance(it.get("caps"), dict):
                    po["caps"] = it["caps"]
                out_providers.append(po)
            return {"success": True, "applied_live": applied, "providers": out_providers,
                    "manual_provider": tc.get("manual_provider", ""), "mode": mode}
        except Exception as e:  # noqa: BLE001
            logger.exception("保存文本聊天厂商失败")
            return {"success": False, "error": str(e)}

    # ==================================================================
    # 【2026-10-08 P1·消除「全量覆盖」】按 id 的增量写接口
    # ------------------------------------------------------------------
    # 为什么需要它：`save_text_chat_providers` 收的是**整个 providers 数组**，
    # 前端每次增/删/改一个端口，都要把 `llmProvCache` 全量重新序列化后回传
    # （还被迫用 `__KEEP__` 哨兵表达"这个 Key 别动"）。三个后果：
    #   ① 丢更新：两个入口（弹窗 / 高级手填 / 能力探测回写）先后触发时，
    #      后一个提交的是**基于旧快照**的整表 → 前一个的修改被静默抹掉。
    #   ② 误清空：漏传 api_key 就会清 Key（后端虽已兜底保留，但那是补丁）。
    #   ③ 无法表达"只改这一个"的意图，UI 必须持有全量状态，与真实配置易漂移。
    # 本接口只动**目标那一条**：单端口 upsert / 单端口 delete / 单独设置
    # manual_provider。语义与 `set_chat_provider_model`（已有的按 id 单字段
    # 写）一致，只是把字段面从 model 扩到整条。
    # 注意：**不删除** `save_text_chat_providers`——老前端与引导页仍在用它，
    # 保留原行为，新接口是**额外新增**（P2 零侵入原则）。
    # ==================================================================
    def upsert_text_chat_provider(self, data: dict = None) -> dict:
        """新增或就地更新**一个** text_chat 端口（按 id 定位）。

        入参：{provider:{id,name,provider,base_url,model,timeout_s[,api_key][,caps][,vision]},
               ^^^^^^^^ 或顶层平铺同名字段
               mode?, manual_provider?}
        · id 已存在 → 只覆盖**本次显式给出的字段**，未给的字段（尤其 api_key）原样保留。
        · id 不存在 → 追加到列表尾部。
        · api_key 显式给空串 = 明确清空；不传该键 = 保留原值。
        写盘走 P3 收口（commit_model_write），并热切换路由。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        d = data or {}
        src = d.get("provider") if isinstance(d.get("provider"), dict) else d
        if not isinstance(src, dict):
            return {"success": False, "error": "参数格式错误"}
        pid = str(src.get("id") or "").strip()
        if not pid:
            return {"success": False, "error": "缺少 id"}
        try:
            raw = self._cfg.raw
            tc = raw.setdefault("text_chat", {})
            providers = tc.get("providers") or []
            if not isinstance(providers, list):
                return {"success": False, "error": "providers 不是列表"}
            # 定位：已有则就地改，没有则追加
            target = next((x for x in providers if isinstance(x, dict) and x.get("id") == pid), None)
            is_new = target is None
            if is_new:
                target = {"id": pid}
                providers.append(target)
            # ★ 先把本条快照，任何失败都原样还原 —— 否则"改坏已有"会把字段
            #   留在原地（校验只 return 不还原），出现"配置被改坏一半"的脏状态。
            _target_backup = None if is_new else dict(target)

            def _restore():
                if is_new:
                    if target in providers:
                        providers.remove(target)
                elif _target_backup is not None:
                    target.clear()
                    target.update(_target_backup)

            # ---- 逐字段覆盖（只覆盖本次显式给出的键，这是"增量"的核心）----
            _str_fields = ("name", "provider", "base_url", "model")
            for k in _str_fields:
                if k in src:
                    v = str(src.get(k) or "").strip()
                    if k == "provider":
                        v = v.lower()
                        if v not in ("openai", "openai-compatible", "ollama"):
                            v = "openai-compatible"
                    if k == "base_url":
                        v = v.rstrip("/")
                    if k == "name":
                        v = v or pid
                    target[k] = v
            if "timeout_s" in src:
                try:
                    target["timeout_s"] = int(src.get("timeout_s") or 30)
                except Exception:  # noqa: BLE001
                    target["timeout_s"] = 30
            # api_key：显式给出才动（空串=清空；不给=保留）——彻底不用 __KEEP__ 哨兵
            if "api_key" in src:
                target["api_key"] = str(src.get("api_key") or "").strip()
            if "vision" in src:
                target["vision"] = bool(src.get("vision"))
            if isinstance(src.get("caps"), dict):
                target["caps"] = {
                    "inputs": src["caps"].get("inputs") or [],
                    "outputs": src["caps"].get("outputs") or [],
                    "features": src["caps"].get("features") or [],
                }
            # 基本校验（与 save_text_chat_providers 一致，但只针对本条）
            if not str(target.get("model") or "").strip():
                _restore()
                return {"success": False, "error": f"厂商 {target.get('name') or pid} 缺少 model"}
            if target.get("provider") != "ollama" and not str(target.get("base_url") or "").strip():
                _restore()
                return {"success": False, "error": f"厂商 {target.get('name') or pid} 缺少 base_url"}
            tc["providers"] = providers
            # ---- mode / manual_provider（可选；只给才动）----
            _ids = {x.get("id") for x in providers if isinstance(x, dict)}
            if "mode" in d:
                _m = str(d.get("mode") or "").strip().lower()
                tc["mode"] = _m if _m in ("auto", "manual") else "auto"
            if "manual_provider" in d:
                _mp = str(d.get("manual_provider") or "").strip()
                if _mp and _mp not in _ids:
                    _restore()
                    return {"success": False, "error": f"默认厂商不存在: {_mp}"}
                tc["manual_provider"] = _mp
            # ---- Key 统一 + P3 收口（与全量接口同一套）----
            _pk = str(target.get("api_key") or "").strip()
            if _looks_like_bailian_key(_pk):
                _unify_bailian_key(raw, _pk, source="upsert_text_chat_provider")
            try:
                from ..models import commit_model_write as _cmw
                _cmw(raw, changed="text_chat.providers", source="upsert_text_chat_provider")
            except Exception:  # noqa: BLE001
                logger.warning("写入收口失败（不影响保存）", exc_info=True)
            self._save_config()
            from ..config import _to_ns
            self._cfg.ns = _to_ns(raw)
            applied = self._apply_llm_live()
            return {"success": True, "applied_live": applied, "id": pid, "created": is_new,
                    "mode": tc.get("mode", "auto"), "manual_provider": tc.get("manual_provider", "")}
        except Exception as e:  # noqa: BLE001
            logger.exception("upsert 文本聊天端口失败")
            return {"success": False, "error": str(e)}

    def delete_text_chat_provider(self, provider_id: str = "", data: dict = None) -> dict:
        """删除一个 text_chat 端口（按 id）。删的是当前 manual_provider 时自动回落。

        入参：provider_id 或 {"id": "..."}。删除不存在的 id 视为幂等成功。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        pid = (provider_id or (data or {}).get("id") or "").strip()
        if not pid:
            return {"success": False, "error": "缺少 id"}
        try:
            raw = self._cfg.raw
            tc = raw.setdefault("text_chat", {})
            providers = tc.get("providers") or []
            if not isinstance(providers, list):
                return {"success": False, "error": "providers 不是列表"}
            before = len(providers)
            kept = [x for x in providers if not (isinstance(x, dict) and x.get("id") == pid)]
            if len(kept) == before:
                return {"success": True, "removed": False, "id": pid,
                        "manual_provider": tc.get("manual_provider", "")}
            tc["providers"] = kept
            # 删掉的正是当前 manual 端口 → 回落到第一个（或清空）
            if tc.get("manual_provider") == pid:
                tc["manual_provider"] = kept[0]["id"] if kept else ""
                if not kept:
                    tc["mode"] = "auto"
            try:
                from ..models import commit_model_write as _cmw
                _cmw(raw, changed="text_chat.providers", source="delete_text_chat_provider")
            except Exception:  # noqa: BLE001
                logger.warning("写入收口失败（不影响保存）", exc_info=True)
            self._save_config()
            from ..config import _to_ns
            self._cfg.ns = _to_ns(raw)
            applied = self._apply_llm_live()
            return {"success": True, "removed": True, "id": pid, "applied_live": applied,
                    "mode": tc.get("mode", "auto"),
                    "manual_provider": tc.get("manual_provider", "")}
        except Exception as e:  # noqa: BLE001
            logger.exception("删除文本聊天端口失败")
            return {"success": False, "error": str(e)}

    def validate_llm_endpoint(self, data: dict = None) -> dict:
        """校验一个 OpenAI 兼容端点：拉 GET {base_url}/models，返回模型列表。

        供设置页「高级·手动填写」卡片一次性校验 地址 / Key / 模型名。
        纯只读探测，不写盘。自动补全 /v1（用户漏填时兜底试探）。
        """
        import json as _json
        import urllib.request as _urllib_req
        import ssl as _ssl
        d = data or {}
        base = (d.get("base_url") or "").strip().rstrip("/")
        key = (d.get("api_key") or "").strip()
        model = (d.get("model") or "").strip()
        if not base:
            return {"success": False, "error": "base_url 不能为空"}
        # 候选地址：base + /models；若 base 本身不含 /v1 再做 /v1 兜底试探
        norm = base
        candidates = [norm + "/models"] if not norm.endswith("/models") else [norm]
        if "/v1" not in norm:
            candidates.append(norm.rstrip("/") + "/v1/models")
        last_err = ""
        for url in candidates:
            req = _urllib_req.Request(url)
            if key:
                req.add_header("Authorization", "Bearer " + key)
            req.add_header("Accept", "application/json")
            try:
                ctx = _ssl.create_default_context()
                with _urllib_req.urlopen(req, timeout=15, context=ctx) as resp:
                    raw = resp.read().decode("utf-8", "replace")
                body = _json.loads(raw)
                models = [m.get("id") for m in body.get("data", [])
                          if isinstance(m, dict) and m.get("id")]
                model_in_list = (model in models) if (model and models) else None
                return {"success": True, "url": url, "models": models,
                        "count": len(models), "model_in_list": model_in_list}
            except Exception as e:  # noqa: BLE001
                last_err = f"{url} → {e}"
                continue
        return {"success": False, "error": "无法拉取模型列表：" + last_err}

    def test_text_chat_provider(self, data: dict = None) -> dict:
        """测试 text_chat.providers 里某个已存端口的连通性（按 id）。

        供设置页 709「我的模型配置」列表每项的「测试连接」按钮使用。
        复用 test_llm_connection 的 openai 探测（chat/completions 实测 + /models 兜底）。
        """
        d = data or {}
        pid = (d.get("id") or "").strip()
        if not pid:
            return {"success": False, "error": "缺少 id"}
        tc = (self._cfg.raw.get("text_chat") or {}) if self._cfg else {}
        prov = next((x for x in (tc.get("providers") or []) if x.get("id") == pid), None)
        if not prov:
            return {"success": False, "error": f"未找到端口: {pid}"}
        form = {"base_url": (prov.get("base_url") or "").strip(),
                "api_key": (prov.get("api_key") or "").strip(),
                "model": (prov.get("model") or "").strip()}
        # 本地 Ollama 也是走 openai 兼容 /v1 探测；其余 openai 兼容同理
        return self.test_llm_connection("openai", form)

    def _apply_llm_live(self) -> bool:
        """热切换 LLM 客户端：重建并替换各组件引用（对话/视觉/主动搭话）。"""
        if self._loop is None or not self._loop.is_running():
            return False
        from ..llm import create_llm
        try:
            new_llm = create_llm(self._cfg)
        except Exception as e:
            logger.warning("重建 LLM 客户端失败: %s", e)
            return False

        async def _swap() -> None:
            if self._dialogue is not None:
                self._dialogue._llm = new_llm
            if self._proactive is not None:
                self._proactive._llm = new_llm
            # 【2026-10-13·fix4】好感度裁判也跟随热切换：用新引擎重建裁判
            rep = getattr(self, "_relationship_reporter", None)
            if rep is not None and hasattr(rep, "set_llm"):
                try:
                    rep.set_llm(new_llm)
                except Exception as e:  # noqa: BLE001
                    logger.warning("热切换关系裁判失败（保留旧裁判）: %s", e)
            # 注意：analyzer 的视觉入口是 VisionRouter（云端优先/本地兜底），
            # 与对话 LLM 独立，不应被 create_llm 替换。
            logger.info("LLM provider 已热切换为: %s (模型 %s)", new_llm.name, new_llm.model_name)

        self._run_coro(_swap())
        # 【双工占卡→聊天跳过本地端口（2026-09-03）】热切换后新路由实例 skip 标志
        # 复位，按当前双工模式重新同步，避免聊天又去抢 Comni 的 GPU。
        try:
            self._sync_chat_router_duplex()
        except Exception:  # noqa: BLE001
            logger.debug("热切换后同步聊天路由 skip_local 失败", exc_info=True)
        return True

    def test_llm_connection(self, provider: str = "", data: dict = None) -> dict:
        """测试指定 provider 连接。data 为表单当前值（不落盘），缺省读已保存配置。"""
        provider = (provider or "").strip().lower()
        from types import SimpleNamespace

        if provider == "openai":
            if data:
                openai_cfg = SimpleNamespace(
                    base_url=(data.get("base_url") or "").rstrip("/"),
                    api_key=data.get("api_key") or "",
                    model=data.get("model") or "",
                    vision=bool(data.get("vision", True)),
                    timeout_s=float(data.get("timeout_s") or 10),
                )
            else:
                openai_cfg = getattr(getattr(self._cfg.ns, "llm", None), "openai", None)
            base_url = (getattr(openai_cfg, "base_url", "") or "").rstrip("/")
            api_key = getattr(openai_cfg, "api_key", "") or ""
            model = getattr(openai_cfg, "model", "") or ""
            if not base_url or not model:
                return {"success": False, "message": "请先填写 base_url 和 model"}
            try:
                import httpx
                auth_headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
                # 真实连通测试：发一个最小 chat/completions 请求，同时验证 URL + key + model 三者
                try:
                    r = httpx.post(
                        f"{base_url}/chat/completions",
                        headers={**auth_headers, "Content-Type": "application/json"},
                        json={"model": model, "messages": [{"role": "user", "content": "ping"}],
                              "max_tokens": 4, "stream": False},
                        timeout=25,
                    )
                    if r.status_code == 200:
                        return {"success": True, "message": f"连接成功（已用模型 {model} 实测一次对话）"}
                    if r.status_code in (401, 403):
                        return {"success": False, "message": f"API Key 无效（HTTP {r.status_code}）"}
                    if r.status_code == 404:
                        return {"success": False, "message": f"模型 {model} 在该端点不存在（HTTP 404）"}
                    return {"success": False,
                            "message": f"对话接口异常（HTTP {r.status_code}）：{(r.text or '')[:150]}"}
                except httpx.HTTPError as e:
                    # 对话接口连不上：退化用 /models 列表核对端点可达 + 模型是否在列表内
                    try:
                        r2 = httpx.get(f"{base_url}/models", headers=auth_headers, timeout=10)
                        if r2.status_code == 200:
                            try:
                                ids = [m.get("id") for m in (r2.json().get("data") or [])]
                            except Exception:
                                ids = []
                            if model in ids:
                                return {"success": True,
                                        "message": f"端点可达且模型 {model} 在列表内（但对话接口不可用：{e}）"}
                            return {"success": False,
                                    "message": f"连不上对话接口（{e}）；且 /models 列表里没有 {model}"}
                        return {"success": False, "message": f"连接失败：{e}"}
                    except Exception as e2:
                        return {"success": False, "message": f"连接失败：{e2}"}
            except Exception as e:
                return {"success": False, "message": f"连接失败: {e}"}
        if provider == "minicpm":
            if data:
                mc = SimpleNamespace(
                    endpoint=(data.get("endpoint") or "").strip(),
                    max_new_tokens=int(data.get("max_new_tokens") or 512),
                    timeout_s=float(data.get("timeout_s") or 60),
                )
            else:
                mc = getattr(getattr(self._cfg.ns, "llm", None), "minicpm", None)
            endpoint = (getattr(mc, "endpoint", "") or "").strip()
            if not endpoint:
                return {"success": False, "message": "endpoint 未配置"}

            async def _probe() -> bool:
                import websockets
                try:
                    async with websockets.connect(endpoint, open_timeout=15, ping_interval=None,
                                                  ssl=ssl._create_unverified_context()) as ws:
                        while True:
                            raw = await asyncio.wait_for(ws.recv(), timeout=25)
                            msg = json.loads(raw) if isinstance(raw, str) else raw
                            t = msg.get("type") if isinstance(msg, dict) else None
                            if t == "session.queue_done":
                                return True
                            if t == "error":
                                return False
                except Exception as e:
                    logger.warning("MiniCPM 测试失败: %s", e)
                    return False

            try:
                ok = self._run_coro(_probe(), timeout=40)
            except Exception as e:
                return {"success": False, "message": f"测试失败: {e}"}
            return {"success": bool(ok), "message": "连接成功 (免费接口可用)" if ok else "连接失败或服务端错误"}
        if provider == "ollama":
            base_url = ((data.get("base_url") if data else None)
                        or getattr(getattr(self._cfg.ns, "ollama", None), "host", "")
                        or "http://localhost:11434").rstrip("/")
            model = (data.get("model") if data else None) or "qwen3"
            try:
                import httpx
                r = httpx.get(f"{base_url}/api/tags", timeout=10)
                if r.status_code != 200:
                    return {"success": False, "message": f"Ollama 返回 HTTP {r.status_code}"}
                body = r.json()
                models = body.get("models", [])
                names = [m.get("name") or m.get("model") for m in models if isinstance(m, dict)]
                if any((n == model or n.startswith(model + ":")) for n in names):
                    return {"success": True, "message": f"Ollama 在线且已有模型 {model}"}
                return {"success": True, "message": f"Ollama 在线，但尚未下载模型 {model}（运行 ollama pull {model} 即可）"}
            except Exception as e:
                return {"success": False, "message": f"连不上 Ollama（{base_url}）：{e}"}
        return {"success": False, "message": f"provider '{provider}' 无需测试"}

    # ======== 视觉模型切换（屏幕识别用，云端优先/本地兜底） ========
    # ===== 统一模型注册表 API（10-04 设置页整合）=====
    # 设计见 src/llm/model_registry.py：把此前散落 8 处的模型配置收敛成
    # 「本地/云端 × 双工/聊天/视觉」6 个槽位，一个槽位一个面板。
    # 关键性质：**写只写该槽位自己的配置节点**（不再连坐别人），
    # 且**旧接口全部保留**（下方 get_vision_models / save_llm_settings 等一律未删），
    # 老前端与老配置升级零风险。

    def get_model_slots(self) -> dict:
        """读全部 6 个模型槽位的当前生效配置 + 厂商预设 + 一致性体检。

        UI 一次调用拿全，渲染成 2×3 网格（本地/云端 × 双工/聊天/视觉）。
        """
        try:
            from ..llm import model_registry as _mr
            raw = self._cfg.raw if self._cfg else {}
            return {
                "success": True,
                "slots": _mr.read_all(raw),
                "presets": {k: v for k, v in _mr.list_presets().items()},
                "diagnose": _mr.diagnose(raw),
            }
        except Exception as e:  # noqa: BLE001
            logger.exception("读取模型槽位失败")
            return {"success": False, "error": str(e)}

    def set_model_slot(self, data: dict = None) -> dict:
        """写某个模型槽位（只改它自己的配置节点）+ 热生效。

        入参：{loc, slot, model, base_url, api_key}
          · `api_key` 不传/空串 = 不改动 key（用户没填就不覆盖）；
          · `base_url` 不传/空 = 不改动端点。
        改完会尝试热切换对应链路；失败**不阻断保存**（至少配置已落盘，重启生效）。
        """
        data = data or {}
        try:
            from ..llm import model_registry as _mr
            from ..config import _to_ns
            loc = str(data.get("loc") or "").strip().lower()
            slot = str(data.get("slot") or "").strip().lower()
            if loc not in ("local", "cloud") or not slot:
                return {"success": False, "error": "参数 loc/slot 缺失或非法"}
            raw = self._cfg.raw
            # api_key 用哨兵区分"没传"与"传了空串"：没传就不动它
            _k = data.get("api_key")
            res = _mr.set_slot(
                raw, loc, slot,
                model=str(data.get("model") or ""),
                base_url=str(data.get("base_url") or ""),
                api_key=(None if _k is None else str(_k)),
            )
            if not res.get("ok"):
                return {"success": False, "error": res.get("message") or "写入失败"}
            self._save_config()
            self._cfg.ns = _to_ns(raw)
            applied = self._try_apply_slot(loc, slot)
            return {
                "success": True, "loc": loc, "slot": slot,
                "source": res.get("source"), "applied": applied,
                "slots": _mr.read_all(raw),
                "message": ("已保存并热生效" if applied
                            else "已保存（运行时未热切换，重启后完全生效）"),
            }
        except Exception as e:  # noqa: BLE001
            logger.exception("写模型槽位失败")
            return {"success": False, "error": str(e)}

    def _try_apply_slot(self, loc: str, slot: str) -> bool:
        """槽位写盘后尝试热切换对应链路；返回是否真的热生效了。

        尽力而为：任何异常都吞掉（配置已落盘，重启即生效，不该因此报错）。
        """
        try:
            if slot == "chat":
                return bool(self._apply_llm_live())
            if slot == "vision":
                # 视觉路由在启动时一次性构造；能热切 active 就热切，
                # 改 model/base_url 则必须重建（这里尝试重建，失败则只提示重启）。
                if self._vision_router is not None:
                    vm = (self._cfg.raw or {}).get("vision_models") or {}
                    act = str(vm.get("active") or "")
                    if act:
                        self._vision_router.set_active(act)
                    try:
                        from ..config import load_config
                        from ..llm.vision_router import VisionRouter
                        self._vision_router = VisionRouter(self._cfg)
                        return True
                    except Exception:  # noqa: BLE001
                        return False
                return False
            if slot == "duplex":
                pro = getattr(self, "_proactive", None)
                if pro is not None and loc == "cloud":
                    try:
                        pro.set_call_profile("voice", False)
                        return True
                    except Exception:  # noqa: BLE001
                        return False
                return False
        except Exception:  # noqa: BLE001
            return False
        return False

    def get_model_diagnose(self) -> dict:
        """只跑一致性体检（设置页「检查配置」按钮）。"""
        try:
            from ..llm import model_registry as _mr
            return {"success": True, "diagnose": _mr.diagnose(self._cfg.raw or {})}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def test_model_slot(self, data: dict = None) -> dict:
        """测试某槽位当前配置是否真能连通（不落盘，只探测）。

        云端槽位：发一个极短的请求验证 key/model/端点。
        本地槽位：查 ollama / Comni 端点是否在。
        """
        data = data or {}
        loc = str(data.get("loc") or "").strip().lower()
        slot = str(data.get("slot") or "").strip().lower()
        try:
            from ..llm import model_registry as _mr
            info = _mr.read_slot(self._cfg.raw or {}, loc, slot)
            if loc == "local":
                return self._test_local_slot(slot, info)
            # 云端：只测文本类（双工/视觉需长连接，用「能否构造客户端」代替真调）
            try:
                if not str(info.get("api_key_masked") or "") and not info.get("has_key"):
                    return {"success": False,
                            "error": "该槽位还没填 api_key。填完再测。"}
                from ..llm.openai_client import OpenAIClient
                c = OpenAIClient(self._cfg,
                                base_url=info.get("base_url") or "",
                                api_key=self._read_slot_key(loc, slot),
                                model=info.get("model") or "",
                                vision=(slot == "vision"))
                if not c.available:
                    return {"success": False, "error": "配置不完整（缺 base_url 或 model）"}
                if slot == "chat":
                    txt = c.chat([{"role": "user", "content": "只回复两个字：正常"}],
                                 options={"max_tokens": 16})
                    return {"success": True,
                            "detail": f"连通成功，回包：{str(txt)[:40]}"}
                return {"success": True,
                        "detail": f"配置可用（模型 {info.get('model')}，"
                                  f"base_url {info.get('base_url')}）。"
                                  f"视觉/双工需实际使用时才真正建立长连接。"}
            except Exception as e:  # noqa: BLE001
                return {"success": False, "error": f"连接失败：{type(e).__name__}: {e}"}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def _read_slot_key(self, loc: str, slot: str) -> str:
        """内部：按槽位取真实 api_key（测试用，**不返回给前端**）。"""
        from ..llm import model_registry as _mr
        raw = self._cfg.raw or {}
        try:
            if slot == "duplex" and loc == "cloud":
                return str(_mr._dig(raw, "cloud_omni.api_key", "") or "")
            if slot == "vision" and loc == "cloud":
                vm = raw.get("vision_models") or {}
                ent = _mr._find_vision_entry(vm, str(vm.get("active") or ""))
                if ent is None:
                    ent = _mr._first_vision_entry(vm)
                return str((ent or {}).get("api_key") or "")
            if slot == "chat" and loc == "cloud":
                p = _mr._pick_cloud_chat_provider(raw) or {}
                return str(p.get("api_key") or "")
        except Exception:  # noqa: BLE001
            pass
        return ""

    def _test_local_slot(self, slot: str, info: dict) -> dict:
        """本地槽位探测：ollama 端点 / Comni 端点是否在。"""
        import urllib.request
        try:
            if slot == "vision" and info.get("model"):
                # 本地视觉就是 ollama 的模型
                pass
            base = str(info.get("base_url") or "") or "http://127.0.0.1:11434"
            url = base.rstrip("/") + "/api/tags"
            with urllib.request.urlopen(url, timeout=3) as r:
                import json as _json
                d = _json.loads(r.read())
            names = [m.get("name", "") for m in (d.get("models") or [])]
            want = str(info.get("model") or "")
            installed = any(want and want in n for n in names)
            return {"success": True,
                    "detail": "ollama 在运行，已装 %d 个模型。%s" % (
                        len(names),
                        ("当前模型已就绪：" + want) if installed
                        else ("⚠️ 当前模型「%s」未 pull，请先 ollama pull %s" % (want, want)))}
        except Exception as e:  # noqa: BLE001
            return {"success": False,
                    "error": f"本地端点不可达（{info.get('base_url')}）：{type(e).__name__}"}

    def get_vision_models(self) -> dict:
        """返回视觉模型候选列表 + 当前激活 + 各模型状态（供「模型设置」下拉）。"""
        if self._vision_router is None:
            return {"success": False, "error": "视觉模型路由未注入（重启后生效）"}
        snap = self._vision_router.snapshot()
        return {"success": True, **snap}

    def switch_vision_model(self, model_id: str = "") -> dict:
        """切换视觉模型：热生效 + 写入 config.yaml（重启后保持）。"""
        if self._vision_router is None:
            return {"success": False, "error": "视觉模型路由未注入（重启后生效）"}
        model_id = (model_id or "").strip()
        ok, err = self._vision_router.set_active(model_id)
        if not ok:
            return {"success": False, "error": err}
        try:
            raw = self._cfg.raw
            if not isinstance(raw.get("vision_models"), dict):
                raw["vision_models"] = {}
            raw["vision_models"]["active"] = model_id
            self._save_config()
            from ..config import _to_ns
            self._cfg.ns = _to_ns(raw)
        except Exception as e:
            logger.exception("写入 vision_models.active 失败")
            return {"success": False, "error": f"已热切换但写入配置失败: {e}"}
        return {"success": True, "active": model_id}

    def probe_vision_model(self, model_id: str = "") -> dict:
        """健康探测（额度提醒浮层"再试一次"用）：最小请求验证模型是否恢复。

        用户在控制台充值/关"仅免费额度"模式后点一下即可恢复，不必等冷却过期。
        """
        if self._vision_router is None:
            return {"success": False, "error": "视觉模型路由未注入（重启后生效）"}
        model_id = (model_id or "").strip()
        if not model_id:
            return {"success": False, "error": "缺少 model_id"}

        import asyncio as _aio
        try:
            loop = _aio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            # 已在事件循环（极少数调用路径）：走后台线程避免嵌套 asyncio.run
            import concurrent.futures as _cf
            with _cf.ThreadPoolExecutor(max_workers=1) as _ex:
                fut = _ex.submit(
                    _aio.run, self._vision_router.probe_model(model_id))
                result = fut.result(timeout=60)
        else:
            result = _aio.run(self._vision_router.probe_model(model_id))
        result["success"] = bool(result.get("ok"))
        return result

    def set_vision_model_key(self, model_id: str = "", api_key: str = "") -> dict:
        """设置视觉模型（智谱等 OpenAI 兼容云端条目）api_key：热生效 + 写 config.yaml。"""
        if self._vision_router is None:
            return {"success": False, "error": "视觉模型路由未注入（重启后生效）"}
        model_id = (model_id or "").strip()
        ok, err = self._vision_router.set_model_api_key(model_id, api_key)
        if not ok:
            return {"success": False, "error": err}
        try:
            raw = self._cfg.raw
            vm = raw.get("vision_models")
            if not isinstance(vm, dict):
                vm = {}
                raw["vision_models"] = vm
            models = vm.get("models")
            if isinstance(models, list):
                for it in models:
                    if isinstance(it, dict) and it.get("id") == model_id:
                        it["api_key"] = api_key
                        break
            self._save_config()
            from ..config import _to_ns
            self._cfg.ns = _to_ns(raw)
        except Exception as e:
            logger.exception("写入 vision_models.api_key 失败")
            return {"success": False, "error": f"已热更新但写入配置失败: {e}"}
        return {"success": True, "active": model_id}

    def get_vision_settings(self) -> dict:
        """返回视觉感知开关态（设计稿设置页「视觉感知」开关的真实后端态）。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        vm = self._cfg.raw.get("vision_models", {}) or {}
        active = vm.get("active", "")
        enabled = vm.get("enabled", None)
        if enabled is None:
            enabled = bool(active)
        models = []
        if self._vision_router is not None:
            try:
                models = self._vision_router.snapshot().get("models", []) or []
            except Exception:  # noqa: BLE001
                pass
        per_v = ((self._cfg.raw.get("perception") or {}).get("vision") or {})
        _vm_raw = (self._cfg.raw.get("vision_models") or {})
        # 云端视觉模型是否可用 → 统一走 _cloud_vision_available()，避免与 get_settings_health 各写一份
        _cloud_ok = self._cloud_vision_available()
        return {"success": True, "enabled": bool(enabled), "active": active, "models": models,
                # 桌面看屏互动（持续盯桌面）与视觉感知（能否使用视觉模型）是两个独立开关
                "desktop_watch": (self._cfg.desktop_watch_effective()
                                  if self._cfg else bool(per_v.get("desktop_watch_enabled", True))),
                # 【2026-09-28】游戏时云端看屏开关（作用于独立 VLM 看屏管线，与语音解耦）。
                # cloud_model_available 告知 UI 是否真有可用云端视觉模型（开了但没模型=不生效）。
                # 云端看屏用的模型就是「视觉模型」里已选的那个（VisionRouter.active），故不另设旋钮。
                "game_cloud_watch": bool(_vm_raw.get("game_cloud_watch", False)),
                "cloud_model_available": bool(_cloud_ok)}

    def _cloud_vision_available(self) -> bool:
        """vision_models 里是否存在可用的云端视觉模型（带 Key 的 openai 候选）。

        读 cfg.raw（webapp 保存时原地改 raw），故 Key/模型变动即时生效，无需重启。
        与 main.py 的 `_cloud_vision_available(cfg)` 判定口径一致。

        【A8 · 2026-09-29】不再把 minicpm 官方免费网关算作可用云端视觉。该免费服务不稳定，
        且已不再是默认云端来源——云端视觉必须走用户自己绑的 Key（openai/openai 兼容 + api_key）。
        无 Key 时云端视觉视为不可用，引导用户去绑 Key，而非偷偷用免费服务。
        """
        try:
            vm = (self._cfg.raw.get("vision_models") or {}) if self._cfg else {}
            for m in (vm.get("models") or []):
                if not isinstance(m, dict):
                    continue
                prov = str(m.get("provider", "") or "").strip().lower()
                if prov in ("openai", "openai_compatible", "dashscope") and bool(str(m.get("api_key", "") or "").strip()):
                    return True
        except Exception:  # noqa: BLE001
            return False
        return False

    # ===== 能力槽中央配置模型（2026-10-09 重做「模型与 API」）=====
    def get_capability_slots(self) -> dict:
        """读取 4 个能力槽（阿里云一键 / 对话模型 / TTS / 双工）的中央状态 + 推荐服务商清单。

        2026-10-10 起 LLM 与 VLM 合并为「对话模型」槽（多数在售模型自带视觉），
        并新增「阿里云一键」槽（一个百炼 Key 同时配 对话/看屏 + TTS + 双工）。
        返回 slot_config.read_capability_slots(raw) 的结构，前端直接消费。
        纯查询，不改配置。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            from ..models.slot_config import read_capability_slots
            data = read_capability_slots(self._cfg.raw)
            data["success"] = True
            return data
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"读取能力槽失败: {e}"}

    def set_capability_slot(self, data: dict = None) -> dict:
        """写入一个能力槽的选择（四槽 UI 的唯一写入口）。

        data: {cap, provider, model, api_key, base_url, group_id, loc,
               is_custom, custom_url, custom_model, custom_key}
        落回各链路已有配置节点（不发明新节点），最后调 commit_model_write 归一。
        """
        data = data or {}
        cap = str(data.get("cap") or "").strip()
        if not cap:
            return {"success": False, "error": "缺少 cap"}
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            from ..models.slot_config import (
                set_capability_slot as _scs, read_capability_slots as _rcs,
            )
            from ..models import commit_model_write as _cmw
            r = _scs(self._cfg.raw, cap, data)
            if not r.get("ok"):
                return {"success": False, "error": r.get("message", "写入失败")}
            _cmw(self._cfg.raw, changed=r.get("changed", f"capability.{cap}"),
                 source="set_capability_slot")
            # 【2026-10-16 修复·绑定不落盘】能力槽是四槽 UI 的**唯一写入口**，但此前只改内存
            # raw、从不写盘（commit_model_write 文档明确它是"落盘之前"的收口，自身不写文件）。
            # 后果实测：在「设置 → 模型与 API」绑 Key，界面提示"已保存"，但 config.yaml 哈希
            # 不变 → 重启后配置回到原样，用户以为"绑了却总丢"。引导流程因收尾会整体落盘而掩盖了它。
            self._save_config()
            try:
                from ..config import _to_ns
                self._cfg.ns = _to_ns(self._cfg.raw)
            except Exception:  # noqa: BLE001
                logger.debug("能力槽写入后刷新 ns 失败", exc_info=True)
            return {"success": True, "cap": cap, "message": r.get("message", ""),
                    "slots": _rcs(self._cfg.raw)}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"写入能力槽失败: {e}"}


    def get_quota_status(self, force: bool = False) -> dict:
        """【2026-10-19 新增】模型额度 / Key 可用性状态（**非打扰式**提示用）。

        背景：用户实测主聊天模型 qwen3.8-max 免费额度耗尽（403），而
        text_chat.mode=manual 不兜底 → 打字聊天完全发不出去，界面只有一句
        HTTP 403，用户不知道"该换模型"。

        设计（沿用 2026-10-02 决策：额度耗尽是持续状态，不能反复弹模态框）：
          · 不主动轮询：只在用户打开设置页 / 手动点"检测"时调用
          · 结果缓存 30 分钟：避免每次进设置页都打网络
          · 返回"影响 + 修复建议"，前端用角标/细条展示，不打断操作
        """
        try:
            from .quota_guard import build_quota_report
            return build_quota_report(self._cfg, force=bool(force))
        except Exception as e:  # noqa: BLE001
            logger.exception("get_quota_status 失败")
            return {"success": False, "error": str(e)}

    def get_tts_provider_key(self, data: dict = None) -> dict:
        """【2026-10-19 新增】给引导页克隆弹窗回传百炼 API Key（仅本机 UI 使用）。

        背景：引导页「给角色配专属声音 → 云端克隆」弹窗需要 Key 才能自动拉取
        已克隆音色。已绑定 TTS 时弹窗里没有 Key 输入框（能力槽接口也**故意**
        不回传 Key），前端无路可取 → 自动拉取静默失效，用户被迫手填 voice_id。

        Key 来源与「测试语音/角色卡试听」同源：voice.clone.cloud.<provider>.api_key，
        兜底 voice_profiles 里同引擎的档。只回传给本机 WebView，不出网。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            provider = str((data or {}).get("provider") or "dashscope_cosy").strip()
            raw = self._cfg.raw
            # ① voice.clone.cloud.<provider>
            vcc = (((raw.get("voice") or {}).get("clone") or {}).get("cloud") or {})
            key = str((vcc.get(provider) or {}).get("api_key") or "").strip()
            # ② voice_profiles 同引擎兜底
            if not key:
                for p in (raw.get("voice_profiles") or []):
                    if (p.get("engine") or "") == provider and (p.get("api_key") or "").strip():
                        key = str(p["api_key"]).strip()
                        break
            # ③ cloud_omni（同是百炼体系，双工 Key 可通用克隆查询）
            if not key and provider == "dashscope_cosy":
                key = str((raw.get("cloud_omni") or {}).get("api_key") or "").strip()
            return {"success": bool(key), "provider": provider, "api_key": key,
                    "error": "" if key else "尚未绑定该服务商的 Key"}
        except Exception as e:  # noqa: BLE001
            logger.exception("get_tts_provider_key 失败")
            return {"success": False, "error": str(e)}

    def set_vision_enabled(self, enabled: bool) -> dict:
        """视觉感知总开关：热生效 + 写入 config.yaml。启用且无激活模型时自动选第一个。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            raw = self._cfg.raw
            vm = raw.setdefault("vision_models", {})
            enabled = bool(enabled)
            vm["enabled"] = enabled
            # 视觉感知总开关 ON 时，必须保证功能集里至少有一个视觉能力（本地快照 / 本地双工 /
            # 云端双工 / 云端快照），否则"开了视觉却没任何引擎在看屏"会串档。这里自动补一个
            # （按当前双工路径选本地快照或云端快照），并触发模型准备——满足"开视觉就该有屏幕共享"。
            # 开关 OFF 不强制任何能力（用户主动关掉，无需模型）。
            if enabled:
                feats = raw.get("companion_features")
                if not isinstance(feats, list):
                    feats = self._derive_features_from_config(raw)
                has_vis = any(
                    isinstance(f, dict) and f.get("id") in
                    {"local_vision", "local_duplex", "cloud_duplex", "cloud_snapshot"}
                    for f in feats
                )
                if not has_vis:
                    dmode = (raw.get("proactive", {}) or {}).get("duplex_mode")
                    add_id = "cloud_snapshot" if dmode in ("realtime", "cloud", False) else "local_vision"
                    feats.append({
                        "id": add_id,
                        "label": self.COMPANION_FEATURE_DEFS[add_id]["label"],
                        "vram": self.COMPANION_FEATURE_DEFS[add_id]["vram"],
                        "opts": {},
                    })
                    raw["companion_features"] = feats
                    self._features_to_engine_config(feats, raw)
                # 每次开启视觉都触发一次模型准备（覆盖"能力已存在但模型未下载"的重开关场景）；
                # 量化包已存在则 _kickoff 内部跳过、不重复下。本地能力才会真正下载 GGUF。
                try:
                    self._kickoff_custom_model_prep(feats, raw.get("proactive", {}))
                except Exception:  # noqa: BLE001
                    logger.exception("视觉开启自动准备视觉模型失败（忽略）")
            if self._vision_router is not None:
                fn = getattr(self._vision_router, "set_enabled", None)
                if callable(fn):
                    try:
                        fn(enabled)
                    except Exception:  # noqa: BLE001
                        logger.warning("vision_router.set_enabled 调用失败")
                if enabled and not vm.get("active"):
                    snap = self._vision_router.snapshot()
                    models = snap.get("models", []) or []
                    if models:
                        first = models[0].get("id", "") if isinstance(models[0], dict) else models[0]
                        if first:
                            ok, err = self._vision_router.set_active(first)
                            if ok:
                                vm["active"] = first
            self._save_config()
            from ..config import _to_ns
            self._cfg.ns = _to_ns(raw)
            return {"success": True, "enabled": enabled}
        except Exception as e:  # noqa: BLE001
            logger.exception("设置视觉开关失败")
            return {"success": False, "error": f"写入配置失败: {e}"}

    def set_desktop_watch_enabled(self, enabled: bool) -> dict:
        """桌面看屏互动开关（与「视觉感知」分离，各司其职）。

        只控制"是否持续截图盯桌面、让达妮娅对屏幕内容搭话"，**不动视觉模型本身**：
        关掉后不再周期性调用视觉模型；但看图 / agent 操作屏幕仍可用（只要视觉感知开着）。
        对应 config: perception.vision.desktop_watch_enabled。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            raw = self._cfg.raw
            per = raw.setdefault("perception", {})
            if not isinstance(per, dict):
                per = {}
                raw["perception"] = per
            vis = per.setdefault("vision", {})
            if not isinstance(vis, dict):
                vis = {}
                per["vision"] = vis
            vis["desktop_watch_enabled"] = bool(enabled)
            self._save_config()
            from ..config import _to_ns
            self._cfg.ns = _to_ns(raw)
            # 同步运行态闸门，让截图/分析循环即时生效（无需重启）
            try:
                rs = getattr(self._cfg, "runtime_status", None)
                if rs is not None:
                    ns = self._cfg.ns
                    rs.vision_enabled = bool(
                        bool(getattr(ns.perception.vision, "enabled", True))
                        and bool(getattr(getattr(ns, "vision_models", None), "enabled", True))
                        and bool(enabled))
            except Exception:  # noqa: BLE001
                logger.debug("同步桌面看屏运行态失败", exc_info=True)
            return {"success": True, "desktop_watch": bool(enabled)}
        except Exception as e:  # noqa: BLE001
            logger.exception("保存桌面看屏开关失败")
            return {"success": False, "error": f"写入配置失败: {e}"}

    def set_game_cloud_watch(self, enabled: bool) -> dict:
        """游戏时云端看屏开关（config vision_models.game_cloud_watch）。

        作用于**独立的 VLM 看屏管线**（ScreenCapture→VisionAnalyzer→VisionRouter），
        在 main.py 里改写 ScreenCapture 的 throttle_check 生效；**语音双工完全不受影响**（两
        条管线本就解耦，不为看屏去动语音引擎）。

        关（默认）=游戏/重负载时照常节流：低频抓屏、不触发视觉分析（让位游戏省 CPU/GPU）。
        开=游戏期间不节流，由云端视觉模型接管持续读屏并主动互动；期间仍然禁止加载本地模型
        （vision_router 的 game_check 依旧生效，不占显卡）。
        需先有可用云端视觉模型，否则开了也不生效——可用性由 get_vision_settings 的
        cloud_model_available 告知 UI；云端模型就是「视觉模型」里已选的那个，不另设旋钮。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            raw = self._cfg.raw
            vm = raw.setdefault("vision_models", {})
            if not isinstance(vm, dict):
                vm = {}
                raw["vision_models"] = vm
            vm["game_cloud_watch"] = bool(enabled)
            self._save_config()
            from ..config import _to_ns
            self._cfg.ns = _to_ns(raw)
            # 热生效：proactive._game_cloud_watch_enabled() 每次实时读 cfg.ns，上面刷新 ns
            # 后下一轮快照裁决即按新值判断，无需重启。
            logger.info("游戏时云端看屏已设为 %s", bool(enabled))
            return {"success": True, "game_cloud_watch": bool(enabled)}
        except Exception as e:  # noqa: BLE001
            logger.exception("保存游戏时云端看屏开关失败")
            return {"success": False, "error": f"写入配置失败: {e}"}

    def save_perception(self, data: dict) -> dict:
        """持久化感知偏好（读取频率 / 主动开口强度等）到 config.yaml[perception]。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            raw = self._cfg.raw
            per = raw.setdefault("perception", {})
            if "freq" in data:
                per["snapshot_freq"] = data["freq"]
            if "proactivity" in data:
                per["proactivity"] = float(data["proactivity"])
            if "vision_freq" in data:
                per["vision_freq"] = data["vision_freq"]
            self._save_config()
            from ..config import _to_ns
            self._cfg.ns = _to_ns(raw)
            return {"success": True}
        except Exception as e:  # noqa: BLE001
            logger.exception("保存感知设置失败")
            return {"success": False, "error": f"写入配置失败: {e}"}

    def get_perception(self) -> dict:
        """返回感知偏好：主动开口强度 proactivity（0.2–1.0）等。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        per = (self._cfg.raw.get("perception") or {})
        try:
            pro = float(per.get("proactivity", 0.4))
        except (TypeError, ValueError):
            pro = 0.4
        return {
            "success": True,
            "proactivity": pro,
            "snapshot_freq": per.get("snapshot_freq", "smart"),
            "vision_freq": per.get("vision_freq", 5),
        }

    def test_vision_model(self, model_id: str = "") -> dict:
        """测试指定视觉模型连接（缺省测当前激活模型）：小图调一次多模态。"""
        if self._vision_router is None:
            return {"success": False, "error": "视觉模型路由未注入（重启后生效）"}
        mid = (model_id or "").strip() or self._vision_router.active_id
        if not mid:
            return {"success": False, "error": "未配置激活的视觉模型"}
        ok, msg = self._run_coro(self._vision_router.test_model(mid), timeout=40)
        if ok is None:
            return {"success": False, "error": "测试超时或运行时不可用"}
        return {"success": bool(ok), "message": msg}

    def test_cloud_provider(self, provider: str = "", api_key: str = "", opts: dict = None,
                            llm_cfg: dict = None, vision_cfg: dict = None,
                            tts_cfg: dict = None) -> dict:
        """引导页「四灯」真测：分别真测 文本/视觉/语音合成/双工 四项能力。

        每项独立返回 {success, message, off?}，前端据此逐灯点亮/打回，不再假设共用 Key 齐亮。
        - 阿里云百炼（provider='bailian'，不传 *cfg 覆盖）：走完整四测（文本/视觉/语音合成真测，
          双工诚实标待设置补充）。
        - 其他服务商（传 llm_cfg / tts_cfg 覆盖）：按用户各自填的网关逐样真测；
          视觉该服务商一般不含 -> 标 off（可在设置单独配）；双工同理待设置补端点。
        """
        provider = (provider or "").strip().lower()
        api_key = (api_key or "").strip()
        opts = opts or {}
        dft_base = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        base_url = (opts.get("base_url") or dft_base).rstrip("/")
        # 【2026-10-17 修正·禁止硬编码】优先用用户在设置里选的模型，
        # 其次读配置里的对话模型，最后才用目录默认常量。
        # 【2026-10-19 合并修复】目录默认对话模型常量在 slot_config 里定义；
        # 原队友版直接引用 `_ALIYUN_DEFAULT_CHAT` 却从未导入——只有 opts 和
        # 配置里都没有模型时才会走到这行（NameError 潜伏），全局名完整性闸门抓出。
        from ..models.slot_config import _ALIYUN_DEFAULT_CHAT  # noqa: PLC0415
        llm_model = (opts.get("llm_model")
                     or ((self._cfg.raw.get("text_chat") or {}).get("model") if self._cfg else "")
                     or _ALIYUN_DEFAULT_CHAT)
        vision_model = opts.get("vision_model") or "qwen3-vl-plus"
        tts_model = opts.get("tts_model") or "qwen-audio-3.0-tts-plus"
        results: dict = {}

        # 1) 文本 LLM：优先用 llm_cfg 覆盖（其他服务商各自网关），否则用百炼默认
        _llm = llm_cfg or {}
        _llm_key = (_llm.get("api_key") or api_key).strip()
        _llm_base = (_llm.get("base_url") or base_url).rstrip("/")
        _llm_model = _llm.get("model") or llm_model
        if not _llm_key:
            results["llm"] = {"success": False, "message": "未提供 LLM API Key"}
        else:
            try:
                results["llm"] = self.test_llm_connection(
                    "openai", {"base_url": _llm_base, "api_key": _llm_key,
                               "model": _llm_model, "vision": True, "timeout_s": 25})
            except Exception as e:  # noqa: BLE001
                results["llm"] = {"success": False, "message": f"文本检测异常：{e}"}

        # 2) 视觉 VLM：有 vision_cfg 才真测；其他服务商一般不含视觉 -> 标 off
        _vis = vision_cfg or {}
        # 百炼单 Key 同时覆盖视觉（qwen-vl-*）：未单独传 vision_cfg 时回落主 Key 真测，
        # 修复引导页四灯里视觉被误标「未配置」（2026-09-29）。其他服务商主 Key 为空仍诚实标 off。
        _vis_key = (_vis.get("api_key") or api_key).strip()
        if _vis_key:
            results["vision"] = self._test_vision_with_key(
                (_vis.get("base_url") or base_url).rstrip("/"),
                _vis_key, _vis.get("model") or vision_model)
        else:
            results["vision"] = {"success": False, "off": True,
                                "message": "该服务商一般不含视觉，可在设置单独配置"}

        # 3) 语音合成 TTS：优先 tts_cfg 覆盖（其他服务商各自网关），否则百炼默认
        _tts = tts_cfg or {}
        _tts_key = (_tts.get("api_key") or api_key).strip()
        if _tts_key:
            results["tts"] = self._test_tts_with_key(
                api_key=_tts_key, model=(_tts.get("model") or tts_model),
                base_url=(_tts.get("base_url") or "").rstrip("/") or None,
                mode=(_tts.get("mode") or "dashscope"))
        else:
            results["tts"] = {"success": False, "message": "未提供 TTS API Key"}

        # 4) 双工 Duplex：引导页不采集用户自己的实时(realtime)端点，无法在此真测，
        #    诚实标记为「需到设置-双工引擎单独绑定」，不再假绿。
        results["duplex"] = {
            "success": False, "off": True,
            "message": "双工需在「设置->双工引擎」绑定你自己的实时模型(realtime)，引导页暂不实测",
        }
        return {"success": True, "provider": provider, "results": results}

    def _test_vision_with_key(self, base_url: str, api_key: str, model: str) -> dict:
        """用给定 Key 对视觉模型做一次最小多模态请求（无副作用），验证 Key + 模型可用。"""
        if not api_key:
            return {"success": False, "message": "未提供 API Key"}
        try:
            import httpx
            # 1x1 透明 PNG（极小，几乎不产生 token 费用），仅用于触发多模态解析
            _PNG = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
                    "+M8AAAMCAQDJ/3pUAAAAAElFTkSuQmCC")
            _messages = [{
                "role": "user",
                "content": [
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{_PNG}"}},
                    {"type": "text",
                     "text": "用两个字描述这张图的颜色（无图则答无）。"},
                ],
            }]
            _r = httpx.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}",
                          "Content-Type": "application/json"},
                json={"model": model, "messages": _messages, "max_tokens": 8},
                timeout=25,
            )
            if _r.status_code == 200:
                return {"success": True, "message": f"视觉模型 {model} 实测通过"}
            if _r.status_code in (401, 403):
                return {"success": False,
                        "message": f"视觉 API Key 无效（HTTP {_r.status_code}）"}
            return {"success": False,
                    "message": f"视觉接口异常（HTTP {_r.status_code}）：{(_r.text or '')[:120]}"}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "message": f"视觉连接失败：{e}"}

    def _test_tts_with_key(self, api_key: str, model: str, base_url: str = None, mode: str = "dashscope") -> dict:
        """用给定 Key 对语音合成做一次最小请求（不落盘、不播放），仅校验鉴权与服务可达。
        mode='dashscope' 走百炼多模态生成端点；mode='openai' 走 OpenAI 兼容 /audio/speech。"""
        if not api_key:
            return {"success": False, "message": "未提供 API Key"}
        try:
            import httpx
            if mode == "openai":
                # OpenAI 兼容 TTS：POST {base_url}/audio/speech
                if not base_url:
                    return {"success": False, "message": "未提供 TTS base_url"}
                _url = f"{base_url.rstrip('/')}/audio/speech"
                _body = {"model": model, "input": "你好", "voice": "alloy", "response_format": "mp3"}
                _r = httpx.post(_url, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json=_body, timeout=25)
                if 200 <= _r.status_code < 300:
                    return {"success": True, "message": f"TTS {model} 实测通过"}
                if _r.status_code == 403:
                    _body = ""
                    try:
                        _body = str(_r.json().get("message") or "")
                    except Exception:  # noqa: BLE001
                        _body = str(getattr(_r, "text", ""))[:160]
                    if "FreeTierOnly" in _body or "free tier" in _body.lower():
                        return {"success": False, "message":
                                "TTS 不可用：账号处于「仅用免费额度」模式且该能力的免费额度已耗尽。"
                                "请到百炼控制台关闭「仅用免费额度」或充值；或改用系统预置音色。"}
                    return {"success": False, "message": f"TTS Key 无效（HTTP 403）{_body[:80]}"}
                if "FreeTierOnly" in str(getattr(_r, "text", "")):
                    return {"success": False, "message":
                            "TTS 不可用：账号「仅用免费额度」模式的免费额度已耗尽，"
                            "请到百炼控制台关闭该模式或充值。"}
                if _r.status_code == 429:
                    return {"success": True, "message": "TTS Key 有效，但当前被限流（HTTP 429），稍后重试"}
                # 【2026-10-16 修复·"误绿"】旧实现把 400/404/5xx 一律判成 success=True
                # （提示"Key 有效，音色/模型需确认"）。实测害人：配置里的复刻音色已失效、
                # 或模型与音色不匹配时，检测照样"通过"，用户以为配好了、实际一句也合不出来。
                # 现在只有 2xx 才算通过；其余如实报失败，并带上响应片段便于定位。
                _snip = ""
                try:
                    _snip = (getattr(_r, "text", "") or "")[:120].replace("\n", " ")
                except Exception:  # noqa: BLE001
                    pass
                return {"success": False,
                        "message": f"TTS 实测失败（HTTP {_r.status_code}）" + (f"：{_snip}" if _snip else "")}
            # 【2026-10-16 修复·探测端点用错了 + 请求体字段不对】
            # 引擎实现里早就写明（tts_backends.py DashScopeCosyBackend）：
            #   `…/aigc/multimodal-generation/generation` 对 TTS **恒返回 400
            #   InvalidParameter "url error"** —— 那个端点根本不是语音合成口；
            #   CosyVoice / Qwen-Audio 的 HTTP 合成统一走
            #   `…/api/v1/services/audio/tts/SpeechSynthesizer`。
            # 而本探测此前①用错端点、②发的是 input.prompt + input.tts.voice（应为
            # input.text + input.voice，且 voice 必须是**已克隆音色 id**）；
            # 再叠加"400 也判成功"的误绿，导致 TTS 检测从来没真正验过（2026-10-16 实测）。
            # 现在与引擎同源：正确端点 + 引擎同款字段；model 若与音色 id 前缀不一致，
            # 引擎会据前缀纠正（克隆音色必须用克隆时的同名 model，否则 400 [cosyvoice]Engine error [411]）。
            from ..voice.tts_backends import DashScopeCosyBackend as _Cosy
            _voice = ""
            if self._cfg:
                try:
                    _chars = self._cfg.raw.get("characters") or []
                    _aid = str(self._cfg.raw.get("active_character_id") or "")
                    for _c in _chars:
                        if not isinstance(_c, dict):
                            continue
                        if _aid and str(_c.get("id") or "") != _aid:
                            continue
                        _v = _c.get("voice") or {}
                        if isinstance(_v, dict):
                            _voice = str(_v.get("voice_id") or _v.get("voice") or "").strip()
                        break
                except Exception:  # noqa: BLE001
                    _voice = ""
            if not _voice:
                # 【2026-10-16 修正·也要认"全局克隆"里绑的音色】
                # 角色级未绑定时，用户完全可能在「语音 · 音色」里把音色绑在**全局克隆档**
                # （voice.clone.cloud.<provider>.voice_id）。只认角色级会导致：
                # 明明绑好了，检测却说"还需选定一个音色 ID"。
                try:
                    _cnode = ((self._cfg.raw.get("voice") or {}).get("clone") or {}).get("cloud") or {}
                    for _pv in _cnode.values():
                        if isinstance(_pv, dict):
                            _vid = str(_pv.get("voice_id") or _pv.get("voice") or "").strip()
                            if _vid:
                                _voice = _vid
                                break
                except Exception:  # noqa: BLE001
                    pass
            if not _voice:
                # 【2026-10-16 修正·"未绑音色"不应等同于"检测失败"】
                # 二者是两件事：① Key 是否有效 ② 是否已选定音色。
                # 若一律判失败，用户在"先验 Key、再选音色"的正常顺序下永远得不到结论。
                # 故此处改用"能否列出该 Key 名下的音色"来验证 Key（同一份凭证、只读、无副作用）。
                try:
                    _lv = self.list_bailian_voices(api_key)
                    if isinstance(_lv, dict) and _lv.get("success"):
                        _n = len(_lv.get("voices") or [])
                        return {"success": True,
                                "message": f"Key 有效（可列出 {_n} 个音色）；合成还需在「语音 · 音色」"
                                           f"选定一个音色 ID（用你账号里的音色）"}
                    return {"success": False,
                            "message": "Key 校验未通过：" + str((_lv or {}).get("error") or _lv)[:140]}
                except Exception as e:  # noqa: BLE001
                    return {"success": False, "message": f"Key 校验失败：{e}"}
            _model = _Cosy._model_from_voice_id(_voice) or model
            _url = _Cosy._ENDPOINT_QWEN
            _body = {
                "model": _model,
                "input": {"text": "你好", "voice": _voice},
                "parameters": {"format": "mp3"},
            }
            _r = httpx.post(_url, headers={"Authorization": f"Bearer {api_key}",
                                           "Content-Type": "application/json"}, json=_body, timeout=30)
            if 200 <= _r.status_code < 300:
                return {"success": True,
                        "message": f"语音合成实测通过（model={_model}，音色={_voice[:38]}…）"}
            if _r.status_code in (401, 403):
                return {"success": False, "message": f"语音合成 Key 无效（HTTP {_r.status_code}）"}
            if _r.status_code == 429:
                return {"success": True, "message": "语音合成 Key 有效，但当前被限流（HTTP 429），稍后重试"}
            # 【2026-10-16 修复·"误绿"】同 openai 分支：400/404/5xx 不再判成功。
            # 实测（2026-10-16）：音色失效/模型不匹配时接口回 400，旧实现照样报
            # "Key 有效，但音色/模型需在百炼控制台确认" ⇒ 用户以为配好了，实际没声。
            _snip = ""
            try:
                _snip = (getattr(_r, "text", "") or "")[:120].replace("\n", " ")
            except Exception:  # noqa: BLE001
                pass
            return {"success": False,
                    "message": f"语音合成实测失败（HTTP {_r.status_code}）" + (f"：{_snip}" if _snip else "")}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "message": f"语音合成连接失败：{e}"}

    def test_duplex_connection(self, endpoint: str = "", api_key: str = "", opts: dict = None) -> dict:
        """设置页「双工测试连接」：对 realtime 端点做一次握手探测，验证可达 + Key 有效。
        优先用 websocket-client 做真实握手；未安装则退化为主机可达性探测。"""
        endpoint = (endpoint or "").strip()
        api_key = (api_key or "").strip()
        # 未显式传 Key 时，回退到用户自己绑的 cloud_omni Key（保持「云端只走用户自己的 Key」）
        if not api_key and self._cfg:
            api_key = str((self._cfg.raw.get("cloud_omni") or {}).get("api_key") or "").strip()
        if not endpoint:
            return {"success": False, "message": "未提供实时(realtime)端点，请先在双工引擎段填写"}
        # 优先真实 websocket 握手
        try:
            import websocket  # websocket-client
        except Exception:
            websocket = None
        if websocket is not None:
            try:
                _hdr = {}
                if api_key:
                    _hdr["Authorization"] = f"Bearer {api_key}"
                _c = websocket.create_connection(endpoint, header=_hdr, timeout=10)
                _c.close()
                return {"success": True, "message": "双工实时端点握手成功"}
            except websocket.WebSocketTimeoutException:
                return {"success": False, "message": "双工端点连接超时（10s）"}
            except websocket.WebSocketConnectionException as e:
                return {"success": False, "message": f"双工端点连接失败：{e}"}
            except Exception as e:  # noqa: BLE001
                return {"success": False, "message": f"双工握手异常：{e}"}
        # 兜底：无 websocket 库时仅做主机可达性探测
        try:
            import httpx
            import urllib.parse as _up
            _p = _up.urlparse(endpoint)
            _host = f"{_p.scheme.replace('ws', 'http')}://{_p.netloc}"
            _r = httpx.get(_host, timeout=8, follow_redirects=True)
            return {"success": True, "message": f"双工端点主机可达（HTTP {_r.status_code}），建议安装 websocket-client 做完整握手"}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "message": f"双工端点不可达：{e}"}

    # ======== 百炼免费额度·本地记账（设置页可视化 + 低余量预警，2026-09-29） ========
    # 官方没有公开「免费额度余量」查询 API（compatible-mode/v1/models/quota 实测 404：
    # 被 compatible-mode 当成模型名，报 "The model 'quota' does not exist"；
    # maas 域名的 /api/v1/quotas 是 QPS/RPM/TPM 限流配额，不是免费 Token 余额；
    # 控制台「免费额度」页与 20%/耗尽预警是网页/短信，非程序可调 API）。
    # 故改为本地记账：每次经本软件发起的百炼 chat/completions 调用，把响应里的
    # usage.total_tokens 按模型累加（见 src/llm/quota_meter.py），从每模型默认
    # 100 万免费额度里扣，得出估算余量。已知盲区：双工 realtime 暂无 usage 回报、
    # 其他软件用同一 Key 的调用不计入 —— 精确余量以百炼控制台「免费额度」页为准。
    def _bailian_api_key(self) -> str:
        if not self._cfg:
            return ""
        co = (self._cfg.raw.get("cloud_omni") or {}) if self._cfg else {}
        return str((co or {}).get("api_key") or "").strip()

    # 【2026-10-21 删】get_bailian_quota() —— 本地记账估算。2026-09-29 起额度卡已改成
    # 「应用内嵌浏览器看真实额度，不再本地估算」（设置页 loadBailianConsoleStatus +
    # get_bailian_real_quota + get_quota_status），这个接口从那以后零调用。
    # 留着它等于给"本地估算"留一个随时会被误接回界面的入口 —— 而两份数字打架时，
    # 用户没有能力判断该信哪个。

    # 【2026-10-21 删】get_model_3d_options() —— 写死 mmd/vrm 两条格式。界面上从来没有
    # "选 3D 格式"的下拉（旧页 app.js 只是把 `char.model_3d.format` 原样显示出来）。
    # 与已删的 get_backend_options 同型：一份没人读、也不会跟着后端能力变的清单。

    # ======== 游戏陪伴方案（config proactive.game_plans，双工模型按此陪伴） ========
    def get_game_plans(self) -> dict:
        """返回按游戏配置的陪伴方案列表（proactive.game_plans）。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        raw = self._cfg.raw.get("proactive", {}) or {}
        plans = raw.get("game_plans", []) or []
        return {"success": True, "plans": plans}

    def save_game_plans(self, plans: list = None) -> dict:
        """保存游戏陪伴方案：写 config.yaml + 热更新 proactive 实例（双工立即生效）。

        方案只影响双工"怎么陪伴"（模型上下文注入 prompt），不参与游戏识别。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        plans = plans or []
        cleaned = []
        for p in plans:
            if not isinstance(p, dict):
                continue
            name = str(p.get("name", "") or "").strip()
            prompt = str(p.get("prompt", "") or "").strip()
            if not name or not prompt:
                continue
            keywords = [str(k).strip() for k in (p.get("keywords") or []) if str(k).strip()]
            cleaned.append({"name": name, "keywords": keywords, "prompt": prompt})
        try:
            pro_raw = self._cfg.raw.get("proactive")
            if not isinstance(pro_raw, dict):
                pro_raw = {}
                self._cfg.raw["proactive"] = pro_raw
            pro_raw["game_plans"] = cleaned
            self._save_config()
            from ..config import _to_ns
            self._cfg.ns = _to_ns(self._cfg.raw)
        except Exception as e:
            logger.exception("写入 proactive.game_plans 失败")
            return {"success": False, "error": f"写入配置失败: {e}"}
        # 热更新 proactive 实例（双工上下文立即生效，不必重启）
        pro = getattr(self, "_proactive", None)
        if pro is not None:
            pro._game_plans = [
                {"name": p["name"], "keywords": p["keywords"], "prompt": p["prompt"]}
                for p in cleaned
            ]
        return {"success": True, "count": len(cleaned)}

    # ======== 双工引擎（云端 / 本地 / 关闭）设置 ========
    def _read_cloud_duplex(self) -> dict:
        """读取 cloud_duplex 配置（语音/视频端点、开关），供设置页渲染。"""
        if not self._cfg:
            return {"official_default": "", "voice_endpoint": "", "video_endpoint": "",
                    "video_enabled": False, "prefer_cloud": False, "local_fallback": True,
                    "cloud_key_set": False}
        cd = (self._cfg.raw.get("cloud_duplex") or {}) if getattr(self, "_cfg", None) else {}
        voice = cd.get("voice") or {}
        video = cd.get("video") or {}
        co = (self._cfg.raw.get("cloud_omni") or {}) if getattr(self, "_cfg", None) else {}
        # 综合判断：只要有任何一处配置了云端双工可用凭证，即认为有 Key。
        # cloud_omni.api_key 是旧版统一入口；voice/video.api_key 是新版分端点入口；
        # official_default 是官方默认通道（无需用户填 Key）。
        cloud_key_set = bool(
            str((video.get("api_key") or "") or "").strip()
            or str((voice.get("api_key") or "") or "").strip()
            or str((co.get("api_key") or "") or "").strip()
            or str(cd.get("official_default") or "").strip()
        )
        return {
            "official_default": str(cd.get("official_default") or "").strip(),
            "voice_endpoint": str((voice.get("endpoint") or "") or "").strip(),
            "video_endpoint": str((video.get("endpoint") or "") or "").strip(),
            "voice_key_set": bool(str((voice.get("api_key") or "") or "").strip()),
            "video_key_set": bool(str((video.get("api_key") or "") or "").strip()),
            "video_enabled": bool(cd.get("video_enabled", False)),
            "prefer_cloud": bool(cd.get("prefer_cloud", False)),
            "local_fallback": bool(cd.get("local_fallback", True)),
            "cloud_key_set": cloud_key_set,
        }

    def get_duplex_settings(self) -> dict:
        """JS 桥超时保护壳：底层逻辑见 _get_duplex_settings_impl。

        历史问题：本方法在 WebView2 的 JS 桥线程执行，一旦底层因 proactive 锁
        / 同步调用阻塞，整个 JS 桥线程卡死 → UI 冻结。这里用 daemon 子线程跑真实
        逻辑，最多等 5s（不 join 卡死的子线程，避免反向冻结），超时返回兜底，
        绝不冻结软件（根因死锁留待后续专项排查）。
        """
        _box: dict = {}

        def _run():
            try:
                _box["v"] = self._get_duplex_settings_impl()
            except Exception:  # noqa: BLE001
                pass

        _t = threading.Thread(target=_run, daemon=True)
        _t.start()
        _t.join(timeout=5.0)
        if "v" in _box:
            _v = _box["v"]
            # 【2026-10-16 修复·「主动搭话」按钮显示读错了源】
            # 聊天页右栏那个开关用户读作"主动搭话"，其真相源是 proactive.enabled；
            # 但它此前只能按双工引擎(engine)来画 → 实测出现「按钮=开，而
            # proactive.enabled=False、getDuplex().engine='off'」这种骗人状态。
            # 这里把真相源一并回传，前端改按它绘制。
            try:
                _raw = (self._cfg.raw.get("proactive") or {}) if self._cfg else {}
                if isinstance(_v, dict):
                    # 【2026-10-17 解耦】UI「主动互动」开关的真相源 = proactive.chat_initiative
                    # （旧键 enabled 仅作兼容回退）；同时回传 runtime_on，便于界面说明
                    # "主动互动"与"通话 / 看屏互动"互不影响。
                    _v["proactive_enabled"] = bool(_raw.get(
                        "chat_initiative", _raw.get("enabled", False)))
                    _v.setdefault("runtime_on", bool(_raw.get("runtime_on", True)))
                    _v.setdefault("duplex_mode_raw", _raw.get("duplex_mode"))
            except Exception:  # noqa: BLE001
                pass
            return _v
        logger.warning("[双工设置] get_duplex_settings 超时/阻塞，返回兜底避免 UI 冻结")
        # 【2026-10-08 修复】超时兜底不再硬编码 engine="cloud"（会骗纯本地档显示云端双工）。
        # 改读真实运行态 _duplex_mode（简单属性读取，不阻塞），读不到才回 "unknown"。
        _fb_pro = getattr(self, "_proactive", None)
        _fb_mode = None
        if _fb_pro is not None:
            _fb_m = getattr(_fb_pro, "_duplex_mode", None)
            if _fb_m in ("realtime", "cloud"):
                _fb_mode = "cloud"
            elif _fb_m in ("local", "local_omni"):
                _fb_mode = "local"
            elif _fb_m is False or _fb_m in ("off", "false"):
                _fb_mode = "off"
        return {
            "success": False, "error": "timeout_or_blocked",
            "engine": _fb_mode or "unknown",
            "local_unload_on_game": True,
            "interaction_style": "classic",
            "interaction_styles": ["classic", "live", "minimal", "pubjavis", "pubjavis+",
                                   "test", "test2", "test3", "test3+", "test3++", "test3+++",
                                   "test_custom", "test_custom+", "test4", "test5", "test6", "test7"],
            "interaction_slice_nums": 64,
            "pubjavis_options": {}, "pubjavis_plus_options": {},
            "mode_lock_silent": True, "gpu": {},
        }

    def _get_duplex_settings_impl(self) -> dict:
        """返回双工引擎选择 + 本地硬件门槛检测结果（设置页"双工引擎"卡片展示）。

        engine 值：cloud=云端全双工 / off=关闭双工（local 引擎预留，未内置时
        proactive 侧回退云端）。gpu 为 DXGI 检测的 NVIDIA 独显显存门槛结果。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        pro_raw = self._cfg.raw.get("proactive", {}) or {}
        # 优先读 proactive 实际运行态（set_duplex_mode 热切换后实时反映），
        # 否则回退静态配置字面量。这样调试台显示的就是 AI 此刻真在跑的模式。
        pro = getattr(self, "_proactive", None)
        if pro is not None and getattr(pro, "_duplex_mode", None) is not None:
            mode = pro._duplex_mode
        else:
            mode = pro_raw.get("duplex_mode", "realtime")
        if (pro is not None and getattr(pro, "_duplex_auto", False)) or \
                (pro is None and (pro_raw.get("duplex_mode") == "auto")):
            ui_mode = "auto"
        else:
            mode_str = mode.strip().lower() if isinstance(mode, str) else str(mode).lower()
            mode = mode_str
            if mode in ("realtime", "cloud"):
                ui_mode = "cloud"
            elif mode is False or mode in ("off", "false"):
                ui_mode = "off"
            elif mode == "local":
                ui_mode = "local"   # 本地全双工（Ollama polling 老路径）
            elif mode == "local_omni":
                # 本地真全双工（Comni / llama.cpp-omni 协议）—— 这才是 UI 上「本地」的真实落点。
                # 之前漏了这个分支，local_omni 掉进 else 被当成 cloud 显示，设置页会骗人。
                ui_mode = "local"
            else:
                ui_mode = "cloud"  # true=快照双工：UI 二选一里按云端展示兜底
        engine: dict = {}
        if pro is not None:
            try:
                engine = pro._duplex_engine_info()
            except Exception:  # noqa: BLE001
                engine = {}
        # 视觉切片数：优先读 proactive 运行态（热切换后实时反映），否则回退静态配置。
        slice_nums = 64
        if pro is not None:
            try:
                slice_nums = int(getattr(pro, "_duplex_interaction_slice_nums", 64) or 64)
            except Exception:  # noqa: BLE001
                slice_nums = 64
        else:
            try:
                slice_nums = int(pro_raw.get("duplex_interaction_slice_nums") or 64)
            except Exception:  # noqa: BLE001
                slice_nums = 64
        # pubjavis 模式提示词开关（设置页子面板）：优先读运行时态，否则回退静态配置。
        pub_opts = {}
        if pro is not None:
            try:
                pub_opts = dict(getattr(pro, "get_duplex_pubjavis_options", lambda: {})() or {})
            except Exception:  # noqa: BLE001
                pub_opts = {}
        if not pub_opts and isinstance(pro_raw, dict):
            try:
                _raw = pro_raw.get("duplex_pubjavis_options") or {}
                if isinstance(_raw, dict):
                    pub_opts = dict(_raw)
            except Exception:  # noqa: BLE001
                pass
        # pubjavis+ 模式开关（独立模式，不与 pubjavis 共用一份配置）
        pub_plus_opts = {}
        if pro is not None:
            try:
                pub_plus_opts = dict(getattr(
                    pro, "get_duplex_pubjavis_plus_options", lambda: {})() or {})
            except Exception:  # noqa: BLE001
                pub_plus_opts = {}
        if not pub_plus_opts and isinstance(pro_raw, dict):
            try:
                _raw_plus = pro_raw.get("duplex_pubjavis_plus_options") or {}
                if isinstance(_raw_plus, dict):
                    pub_plus_opts = dict(_raw_plus)
            except Exception:  # noqa: BLE001
                pass
        # 【2026-10-09】本地引擎显示名：从静态配置取，与当前 active 引擎解耦，
        # 防止 mixed 档临时跑云端时「本地引擎」行被填成云端模型名。
        _cfg_dm = str(pro_raw.get("duplex_mode") or pro_raw.get("duplex_voice_engine") or "").strip().lower()
        if _cfg_dm == "local_omni":
            _q = str(pro_raw.get("duplex_local_omni_quant") or "").strip() or "Q4_K_M"
            _local_engine_label = f"Comni / MiniCPM-o 4.5 ({_q})"
        elif _cfg_dm == "local":
            _local_engine_label = "本机基础双工 (Ollama)"
        else:
            _local_engine_label = ""
        return {
            "success": True,
            "engine": ui_mode,
            "endpoint": pro_raw.get("duplex_endpoint", "") or "",
            # 【2026-10-08】高负载自动让路总开关（设置页「通话与双工」渲染真开关用）
            "local_unload_on_game": bool(pro_raw.get("duplex_local_omni_unload_on_game", True)),
            "cloud_duplex": self._read_cloud_duplex(),
            "interaction_style": (pro_raw.get("duplex_interaction_style") or "classic"),
            "interaction_styles": ["classic", "live", "minimal", "pubjavis", "pubjavis+", "test", "test2", "test3", "test3+", "test3++", "test3+++", "test_custom", "test_custom+", "test4", "test5", "test6", "test7"],
            "interaction_slice_nums": slice_nums,
            "pubjavis_options": pub_opts,
            "pubjavis_plus_options": pub_plus_opts,
            "mode_lock_silent": bool(pro_raw.get("duplex_mode_lock_silent", True)),
            "voice_engine": (pro_raw.get("duplex_voice_engine") or "").strip().lower() or ui_mode,
            "video_engine": (pro_raw.get("duplex_video_engine") or "").strip().lower() or ui_mode,
            # 看屏/语音解耦开关：true=云端语音与本地看屏可并存（不会因选云端而关屏）。
            "decouple_screen_voice": bool(pro_raw.get("decouple_screen_voice", False)),
            # 仅控制「云端」双工；本地看屏常驻、不受此开关影响。读 cloud_duplex 段。
            "duplex_vision_enabled": bool((self._cfg.raw.get("cloud_duplex") or {}).get("duplex_vision_enabled", False)),
            # 【2026-09-30】暴露云端双工(实时)模型名，供通话弹窗占位符显示（不泄露 api_key）
            "cloud_omni": (lambda co: {
                "model": str((co or {}).get("model") or "").strip(),
                "voice": str((co or {}).get("voice") or "").strip(),
            })(self._cfg.raw.get("cloud_omni", {}) if self._cfg else {}),
            "local_engine_label": _local_engine_label,
            "gpu": engine,
        }

    def save_call_inference(self, data: dict) -> dict:
        """保存语音通话/视频通话推理模型选择（cloud/local/off/auto），热切换即时生效。

        视频通话（带视觉）引擎直接驱动双工（set_duplex_mode）即时生效；语音通话（纯音频）
        引擎存入 duplex_voice_engine，供纯语音场景读取。两者独立落盘，重启后仍保留。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            voice = str((data or {}).get("voice_engine") or "").strip().lower()
            video = str((data or {}).get("video_engine") or "").strip().lower()
            for m in (voice, video):
                if m and m not in ("cloud", "local", "off", "auto"):
                    return {"success": False, "error": f"未知双工引擎: {m}"}
            pro_raw = self._cfg.raw.get("proactive")
            if not isinstance(pro_raw, dict):
                pro_raw = {}
                self._cfg.raw["proactive"] = pro_raw
            pro_raw["duplex_voice_engine"] = voice
            pro_raw["duplex_video_engine"] = video
            self._save_config()
            from ..config import _to_ns
            self._cfg.ns = _to_ns(self._cfg.raw)
            pro = getattr(self, "_proactive", None)
            if pro is not None:
                # 视频通话引擎即时生效（等同改双工设置）；语音通话引擎仅存属性。
                if video:
                    try:
                        pro.set_duplex_mode(video)
                    except Exception:  # noqa: BLE001
                        logger.exception("视频通话引擎热切换失败")
                try:
                    pro._duplex_voice_engine = voice or ""
                    pro._duplex_video_engine = video or ""
                except Exception:  # noqa: BLE001
                    logger.debug("写入运行时语音/视频引擎属性失败", exc_info=True)
            return {"success": True, "voice_engine": voice, "video_engine": video}
        except Exception as e:
            logger.exception("保存呼叫推理模型失败")
            return {"success": False, "error": f"写入配置失败: {e}"}

    # ===================== 呼叫设置：通话类型 + 设备选择 =====================
    def _list_mic_devices(self) -> list:
        """枚举麦克风输入设备，返回 [{index, id, name, hostapi, hostapi_name}]。

        【2026-10-17 重写·设备必须"选哪个就是哪个"】
        旧实现**按名字去重**，把同一物理设备在不同 host API（MME/DirectSound/WASAPI/
        WDM-KS）下的多路折叠成一条，用户根本无从区分；运行时再拿这个"光名字"去猜
        （日志：『设备名 X 有 2 路同名，已按声学判据选中 idx=21』）——猜错就表现为
        "麦克风在拾音、云端却永远收不到 speech_started，她一直不理我"。
        现在：
          · **同名多路全部列出**，每条带 hostapi；
          · `id` 在同名多路时 = "名字|hostapi"，唯一时 = 纯名字（兼容历史配置）；
          · `name` 带可读后缀（下拉里直接能分辨），UI 无需改动。
        """
        rows: list = []

        def _push(idx, raw_name, hostapi_name, default_sr=0.0, ch=0, hostapi_index=None):
            name = str(raw_name or ("麦克风 %s" % idx)).strip()
            if not name or "loopback" in name.lower():
                return
            rows.append({"index": int(idx), "name": name,
                         "hostapi_name": str(hostapi_name or "").strip(),
                         "hostapi_index": int(hostapi_index if hostapi_index is not None else -1),
                         "default_samplerate": float(default_sr or 0.0),
                         "max_channels": int(ch or 0)})

        try:
            from ..perception._pa_shared import get_shared_pyaudio, pa_lock
            with pa_lock():
                p = get_shared_pyaudio(use_wpatch=True)
                _apis = {}
                try:
                    for a in range(p.get_host_api_count()):
                        _apis[a] = str((p.get_host_api_info_by_index(a) or {}).get("name") or "")
                except Exception:  # noqa: BLE001
                    _apis = {}
                for i in range(p.get_device_count()):
                    try:
                        d = p.get_device_info_by_index(i)
                    except Exception:  # noqa: BLE001
                        continue
                    if int(d.get("maxInputChannels", 0) or 0) > 0:
                        _push(i, d.get("name"), (_apis.get((int(d.get("hostApi")) if d.get("hostApi") is not None else -1)) or {0: "MME", 1: "Windows DirectSound", 2: "Windows WASAPI", 3: "Windows WDM-KS"}.get((int(d.get("hostApi")) if d.get("hostApi") is not None else -1), "")),
                              d.get("defaultSampleRate", 0.0), d.get("maxInputChannels", 0),
                              (int(d.get("hostApi")) if d.get("hostApi") is not None else -1))
            if rows:
                return self._finalize_device_list(rows, "输入")
        except Exception:  # noqa: BLE001
            logger.debug("pyaudiowpatch 枚举麦克风失败，回退 sounddevice", exc_info=True)

        try:
            import sounddevice as sd
            _apis = {}
            try:
                for a, info in enumerate(sd.query_hostapis()):
                    _apis[a] = str((info or {}).get("name") or "")
            except Exception:  # noqa: BLE001
                _apis = {}
            for i, d in enumerate(sd.query_devices()):
                if (d or {}).get("max_input_channels", 0) > 0:
                    _push(i, (d or {}).get("name"), (_apis.get((int((d or {}).get("hostapi")) if (d or {}).get("hostapi") is not None else -1)) or {0: "MME", 1: "Windows DirectSound", 2: "Windows WASAPI", 3: "Windows WDM-KS"}.get((int((d or {}).get("hostapi")) if (d or {}).get("hostapi") is not None else -1), "")),
                          (d or {}).get("default_samplerate", 0.0), (d or {}).get("max_input_channels", 0),
                          (int((d or {}).get("hostapi")) if (d or {}).get("hostapi") is not None else -1))
            return self._finalize_device_list(rows, "输入")
        except Exception:  # noqa: BLE001
            logger.debug("枚举麦克风设备失败", exc_info=True)
            return []

    @staticmethod
    def _finalize_device_list(rows: list, kind_label: str) -> list:
        """把原始设备行整理成 UI 用的列表：同名多路才加 "|hostapi" 限定。

        【2026-10-17】为什么这么做：
          · 唯一名字 → id 保持纯名字（老配置里的值继续能匹配上，不破坏兼容）；
          · 同名多路 → id = "名字|hostapi"，**用户选哪条就是哪条**，运行时按 id 精确解析，
            不再靠声学探测去猜（那正是"选对了却用错设备"的根源）。
        """
        # 【2026-10-17 定稿·按物理设备去重，跟 Windows 一致】
        # 上一版把 MME/DirectSound/WASAPI 的**同一只设备**列了 3 遍 → 用户实测
        # "系统里只有 8 个输出，软件里冒出 20 个"，观感极差且无从选择。
        # 现在：**同名只留一条**（id = 纯设备名，与 Windows 一致）；
        # host API 由运行时自动挑（cloud_omni_realtime 已实现"同设备换 host 重试"：
        # 首选 host 打不开就换同名的其它 host，全失败才回退系统默认）。
        # 这样"选哪个就是哪个"（选的是**物理设备**），列表也不再虚高。
        _API_FALLBACK = {0: "MME", 1: "Windows DirectSound", 2: "Windows WASAPI", 3: "Windows WDM-KS"}
        # 【2026-10-17 再收窄·贴近 Windows 声音面板】用户实测"系统里 8 个输出，软件里 20 个"。
        # 多出来的三类噪音：
        #   ① WDM-KS 低层重复（Windows 面板不展示）—— 整类剔除；
        #   ② 空名端点（'Speakers ()' / 'Output ()' / '麦克风 ()'）—— 通常是已禁用的虚拟端点；
        #   ③ 被 PortAudio 截断的 31 字符重名（'M27P6 (NVIDIA High Definition A'）——
        #      同前缀已有更长名字时丢掉短的那个。
        def _is_noise(rec) -> bool:
            api = str(rec.get("hostapi_name") or "").strip().lower()
            if "wdm-ks" in api:
                return True
            nm = str(rec.get("name") or "").strip()
            if nm.endswith("()") or nm.endswith("(") or nm.endswith(" - "):
                return True
            if "()" in nm:
                return True
            return False

        rows = [r for r in rows if not _is_noise(r)]
        # 截断重名：31 字符且是另一个更长名字的前缀 → 丢弃
        _names = [str(r.get("name") or "") for r in rows]
        _drop = set()
        for i, a in enumerate(_names):
            if len(a) < 28:
                continue
            for j, b in enumerate(_names):
                if i != j and len(b) > len(a) and b.startswith(a):
                    _drop.add(i)
                    break
        if _drop:
            rows = [r for k, r in enumerate(rows) if k not in _drop]
        by_name: dict = {}
        order: list = []
        for r in rows:
            name = r["name"]
            if name not in by_name:
                by_name[name] = {"index": r["index"], "id": name, "name": name,
                                 "hostapi": r.get("hostapi_name") or "",
                                 "hostapis": [], "samplerate": r.get("default_samplerate") or 0.0}
                order.append(name)
            # 记录这只设备在各 host 下的可用索引，便于诊断日志
            _api = r.get("hostapi_name") or _API_FALLBACK.get(int(r.get("hostapi_index", -1) or -1), "")
            try:
                by_name[name]["hostapis"].append({"api": _api, "index": r["index"]})
            except Exception:  # noqa: BLE001
                pass
        out = [by_name[n] for n in order]
        # 【2026-10-17 定稿·列表一只设备一条】
        # 用户反馈："怎么又变成了一个设备显示多个" —— 上一版为可指定 host 把同一只设备的
        # MME/DirectSound/WASAPI 三条都列了出来，虽然技术上没错，但对用户是噪音。
        # 现在：**UI 只给设备名一条**；host 由运行时**有依据地**挑
        #   （记忆中的已验证 host → WASAPI → DirectSound → MME，谁先能打开用谁），
        # hostapis 字段保留给后端做这件事，前端不再展示。
        logger.info("音频设备枚举[%s]：原始 %d 路（多 host API 重复）→ 去重后 %d 条设备；"
                    "host API 由运行时按「已验证记忆 → WASAPI → DirectSound → MME」自动挑选",
                    kind_label, len(rows), len(out))
        return out

    def _list_speaker_devices(self) -> list:
        """枚举扬声器（音频输出）设备，返回 [{index, id, name, hostapi}]。

        【2026-10-17 重写·与麦克风同源】播放走 sounddevice（sd.OutputStream），
        故以 sounddevice 表为准枚举；**不再按名字去重**：同名多路（MME/DirectSound/
        WASAPI）全部列出并带 hostapi 限定，用户选哪条就开哪条。
        这样"听不到她的声音"里"选了扬声器却开在别的设备上"这条也一并根治。
        排除 loopback（回环采集，不是可放音输出）。
        """
        rows: list = []

        def _push(idx, raw_name, hostapi_name, default_sr=0.0, ch=0, hostapi_index=None):
            name = str(raw_name or ("扬声器 %s" % idx)).strip()
            if not name or "loopback" in name.lower():
                return
            rows.append({"index": int(idx), "name": name,
                         "hostapi_name": str(hostapi_name or "").strip(),
                         "hostapi_index": int(hostapi_index if hostapi_index is not None else -1),
                         "default_samplerate": float(default_sr or 0.0),
                         "max_channels": int(ch or 0)})

        try:
            import sounddevice as sd
            _apis = {}
            try:
                for a, info in enumerate(sd.query_hostapis()):
                    _apis[a] = str((info or {}).get("name") or "")
            except Exception:  # noqa: BLE001
                _apis = {}
            for i, d in enumerate(sd.query_devices()):
                if (d or {}).get("max_output_channels", 0) > 0:
                    _push(i, (d or {}).get("name"), (_apis.get((int((d or {}).get("hostapi")) if (d or {}).get("hostapi") is not None else -1)) or {0: "MME", 1: "Windows DirectSound", 2: "Windows WASAPI", 3: "Windows WDM-KS"}.get((int((d or {}).get("hostapi")) if (d or {}).get("hostapi") is not None else -1), "")),
                          (d or {}).get("default_samplerate", 0.0), (d or {}).get("max_output_channels", 0),
                          (int((d or {}).get("hostapi")) if (d or {}).get("hostapi") is not None else -1))
            if rows:
                return self._finalize_device_list(rows, "输出")
        except Exception:  # noqa: BLE001
            logger.debug("sounddevice 枚举扬声器失败，回退 pyaudiowpatch", exc_info=True)

        try:
            from ..perception._pa_shared import get_shared_pyaudio, pa_lock
            # 🛡️ 共享单例 + 全局锁枚举，会话期间绝不 terminate，避免并发 init/terminate
            # 触发原生访问违规（0xC0000005）。
            with pa_lock():
                p = get_shared_pyaudio(use_wpatch=True)
                for i in range(p.get_device_count()):
                    try:
                        d = p.get_device_info_by_index(i)
                    except Exception:  # noqa: BLE001
                        continue
                    if int(d.get("maxOutputChannels", 0) or 0) > 0:
                        _push(i, d.get("name"))
            # 【2026-10-17 修】原为 `return out`，但本函数从未定义 out →
            # 走 pyaudiowpatch 回退分支必然 NameError，被外层 except 吞掉后返回 []，
            # 表现为「没有输出设备可选」。这里与 sounddevice 分支保持一致收口。
            return self._finalize_device_list(rows, "输出") if rows else []
        except Exception:  # noqa: BLE001
            logger.debug("枚举扬声器设备失败", exc_info=True)
            return []

    def _call_speaker_device_raw(self) -> str:
        """当前通话扬声器设备（配置真值，'default' 表示系统默认）。"""
        try:
            pro_raw = self._cfg.raw.get("proactive", {}) or {}
            return str(pro_raw.get("duplex_speaker_device") or "default")
        except Exception:  # noqa: BLE001
            return "default"

    def _apply_call_speaker_device(self) -> None:
        """把配置里的扬声器设备下发给 TTS 播放层（通话建立时调用一次）。"""
        dev = self._call_speaker_device_raw()
        tts = getattr(self, "_tts", None)
        if tts is not None and hasattr(tts, "set_output_device"):
            tts.set_output_device(dev)

    def list_audio_devices(self, data: dict = None) -> dict:
        """通话界面「悬停麦克风/扬声器按钮」展开的设备列表（输入 + 输出一次给全）。

        与 list_mic_devices 的区别：这里把输入/输出合并返回，供通话内热切换用。
        返回的 id 是**设备名**（索引会随插拔漂移，名字才稳）。
        """
        try:
            mic_devs = self._list_mic_devices()
            spk_devs = self._list_speaker_devices()
            pro = getattr(self, "_proactive", None)
            mic_cur = ((getattr(pro, "_duplex_mic_device", None) if pro else None)
                       or (self._cfg.raw.get("proactive", {}) or {}).get("duplex_mic_device")
                       or "default")
            return {"success": True,
                    "mic_devices": mic_devs, "speaker_devices": spk_devs,
                    "mic_current": str(mic_cur),
                    "speaker_current": self._call_speaker_device_raw()}
        except Exception as e:  # noqa: BLE001
            logger.exception("枚举音频设备失败")
            return {"success": False, "error": str(e)}

    def set_call_audio_device(self, data: dict = None) -> dict:
        """通话中热切换麦克风 / 扬声器设备（UI 悬停展开的设备菜单调用）。

        kind='mic'     → 复用既有通路：写 proactive.duplex_mic_device 并即时生效；
        kind='speaker' → 写 proactive.duplex_speaker_device + 下发 TTS 播放层。
        两者都落 call_av 的 device_change，便于事后核对实际用的哪个设备。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        d = data or {}
        kind = str(d.get("kind") or "mic").strip().lower()
        dev = str(d.get("device") or "default").strip() or "default"
        try:
            pro_raw = self._cfg.raw.get("proactive")
            if not isinstance(pro_raw, dict):
                pro_raw = {}
                self._cfg.raw["proactive"] = pro_raw
            from ..config import _to_ns
            if kind == "speaker":
                pro_raw["duplex_speaker_device"] = dev
                self._save_config()
                self._cfg.ns = _to_ns(self._cfg.raw)
                self._apply_call_speaker_device()
                # 【2026-10-18 修复·"扬声器切不了/切了没声音"】
                # 上面 _apply_call_speaker_device() 只改 TTS 播放层；而**通话中的
                # 语音是云端双工内部 _AudioPlayer 直接播的，不经过 TTS**。
                # 所以只调 TTS 等于没通知到真正出声的那条链路 —— 用户切了扬声器
                # 完全没变化，仍从旧设备出声。这里显式通知双工客户端重建输出流。
                pro = getattr(self, "_proactive", None)
                if pro is not None and hasattr(pro, "apply_call_settings"):
                    try:
                        pro.apply_call_settings(speaker_device=dev)
                    except Exception:  # noqa: BLE001
                        logger.exception("扬声器设备热切换失败")
            else:
                kind = "mic"
                pro_raw["duplex_mic_device"] = dev
                self._save_config()
                self._cfg.ns = _to_ns(self._cfg.raw)
                pro = getattr(self, "_proactive", None)
                if pro is not None and hasattr(pro, "apply_call_settings"):
                    try:
                        pro.apply_call_settings(mic_device=dev)
                    except Exception:  # noqa: BLE001
                        logger.exception("麦克风设备热切换失败")
            log_call_av("device_change", kind=kind, device=dev, source="ui")
            logger.info("通话设备切换：%s → %s", kind, dev)
            # 【2026-10-17 修复·麦克风"选了但收不到声音"】
            # 实测：同一只麦克风在 PortAudio 里有 MME/DirectSound/WASAPI 三条记录，
            # MME 那条是**哑设备**（能开能读但恒返回 0），用户点到就"麦克风好的但无效"。
            # 切换麦克风后自动开一个后台验证（3 秒窗口，前端提示"请说句话"），
            # 若持续静音且同名设备里有可用的，自动切过去并告知用户。
            _verify_id = ""
            if kind == "mic" and dev.lower() not in ("", "default"):
                try:
                    import uuid as _uuid
                    _verify_id = _uuid.uuid4().hex[:12]
                    self._mic_verify = {"id": _verify_id, "state": "running",
                                        "device": dev, "requested": dev, "peak": 0.0,
                                        "note": "正在检测麦克风，请对着麦克风说一句话…",
                                        "switched_to": ""}
                    threading.Thread(
                        target=self._mic_verify_worker,
                        args=(_verify_id, dev, dev),
                        daemon=True, name="mic_verify_" + _verify_id,
                    ).start()
                except Exception:  # noqa: BLE001
                    _verify_id = ""
            return {"success": True, "kind": kind, "device": dev,
                    "verify_id": _verify_id,
                    "note": ("正在检测麦克风，请说一句话（约 3 秒）…" if _verify_id else "")}
        except Exception as e:  # noqa: BLE001
            logger.exception("通话设备切换失败")
            return {"success": False, "error": "切换失败: %s" % e}

    def get_call_settings(self) -> dict:
        """呼叫设置面板数据：通话类型 / 引擎 / 麦克风·摄像头设备列表 + 当前视觉源。

        超时保护壳（同 get_duplex_settings）：摄像头枚举需开闭多个设备，可能稍慢，
        用 daemon 子线程跑、最多等 6s，避免 JS 桥冻结。
        """
        _box: dict = {}

        def _run():
            try:
                _box["v"] = self._get_call_settings_impl()
            except Exception:  # noqa: BLE001
                pass

        _t = threading.Thread(target=_run, daemon=True)
        _t.start()
        _t.join(timeout=6.0)
        if "v" in _box:
            return _box["v"]
        logger.warning("[呼叫设置] get_call_settings 超时，返回兜底")
        return {"success": False, "error": "timeout",
                "call_mode": "voice", "voice_engine": "cloud", "video_engine": "cloud",
                "mic_device": "default", "camera_device": "default",
                "speaker_device": "default", "speaker_devices": [],
                "vision_source": "screen", "mic_devices": [], "camera_devices": []}

    def _get_call_settings_impl(self) -> dict:
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        pro_raw = self._cfg.raw.get("proactive", {}) or {}
        pro = getattr(self, "_proactive", None)
        call_mode = (getattr(pro, "_duplex_call_mode", None) if pro else None) \
            or (pro_raw.get("duplex_call_mode") or "voice")
        mic_device = (getattr(pro, "_duplex_mic_device", None) if pro else None) \
            or (pro_raw.get("duplex_mic_device") or "default")
        camera_device = (getattr(pro, "_duplex_camera_device", None) if pro else None) \
            or (pro_raw.get("duplex_camera_device") or "default")
        voice_engine = (pro_raw.get("duplex_voice_engine") or "").strip().lower() or "cloud"
        video_engine = (pro_raw.get("duplex_video_engine") or "").strip().lower() or "cloud"
        # 视觉源：优先读显式配置 duplex_vision_source；未配置则按 call_mode 回退解析；
        # 再兜底 screen（本地双工看屏本意，推流画面生效，而非无画面）。
        vision_source = (pro_raw.get("duplex_vision_source") or "").strip().lower() or ""
        if not vision_source and pro is not None and hasattr(pro, "_resolve_vision_source"):
            try:
                vision_source = pro._resolve_vision_source()
            except Exception:  # noqa: BLE001
                pass
        if vision_source not in ("screen", "camera", "none"):
            vision_source = "screen"
        speaker_device = (pro_raw.get("duplex_speaker_device") or "default")
        mic_devices = self._list_mic_devices()
        speaker_devices = self._list_speaker_devices()
        camera_devices = []
        try:
            from ..perception.camera_capture import list_cameras
            camera_devices = list_cameras()
        except Exception:  # noqa: BLE001
            logger.debug("枚举摄像头设备失败", exc_info=True)
        return {
            "success": True,
            "call_mode": call_mode,
            "voice_engine": voice_engine,
            "video_engine": video_engine,
            "mic_device": str(mic_device),
            "camera_device": str(camera_device),
            "vision_source": vision_source,
            "mic_devices": mic_devices,
            "speaker_device": str(speaker_device),
            "speaker_devices": speaker_devices,
            "camera_devices": camera_devices,
        }

    def save_call_settings(self, data: dict) -> dict:
        """保存呼叫设置（设置页只管：设备切换 + 引擎切换）。

        ⚠️ 简化（2026-09-07）：通话类型(call_mode)与视觉源(vision_source)不再由设置页配置——
        通话类型由「拨打语音/拨打视频」按钮决定；视觉源由 call_mode + 屏幕共享开关推导
        （语音→无画面 / 视频→摄像头 / 视频+屏幕共享→看屏）。设置页仅保留：
        - mic_device / camera_device: 'default' 或整数索引（设备切换，热生效）
        - voice_engine / video_engine：双工推理引擎（云端/本地/自动/关闭）
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            d = data or {}
            mic_device = d.get("mic_device", "default")
            camera_device = d.get("camera_device", "default")
            voice = str(d.get("voice_engine") or "").strip().lower()
            video = str(d.get("video_engine") or "").strip().lower()
            # ⚠️ UI「本地双工」= Comni(local_omni)，与 save_duplex_settings 一致。
            # 绝不能写原始 "local"（那是废弃的 Ollama polling 老路径 LocalMiniCPMDuplexClient，
            # ollama 没跑时每轮"模型裁决失败"、双工假死）。设置页选「本地」必须归一为 local_omni，
            # 否则会出现"设置里选了本地双工、实际跑的是 Ollama 老路径"的脱节。
            if voice == "local":
                voice = "local_omni"
            if video == "local":
                video = "local_omni"
            for m in (voice, video):
                if m and m not in ("cloud", "local_omni", "off", "auto"):
                    return {"success": False, "error": f"未知双工引擎: {m}"}
            pro_raw = self._cfg.raw.get("proactive")
            if not isinstance(pro_raw, dict):
                pro_raw = {}
                self._cfg.raw["proactive"] = pro_raw
            pro_raw["duplex_mic_device"] = mic_device
            pro_raw["duplex_camera_device"] = camera_device
            if voice:
                pro_raw["duplex_voice_engine"] = voice
            if video:
                pro_raw["duplex_video_engine"] = video
            self._save_config()
            from ..config import _to_ns
            self._cfg.ns = _to_ns(self._cfg.raw)
            pro = getattr(self, "_proactive", None)
            if pro is not None and hasattr(pro, "apply_call_settings"):
                try:
                    # 仅热切换设备；通话类型/视觉源由 set_call_profile 在拨号时推导
                    pro.apply_call_settings(
                        mic_device=mic_device,
                        camera_device=camera_device)
                except Exception:  # noqa: BLE001
                    logger.exception("呼叫设置热切换失败")
            # 引擎切换需热切换运行期双工（含卸载本地模型显存）：选「关闭」即
            # set_duplex_mode(False) → 旧 local_omni 触发 Comni 卸载（proactive:5473）。
            # 此前 save_call_settings 只写配置不切运行期，导致本地双工切「关闭」后
            # 模型仍常驻显存（用户实测：关了双工显存没降）。
            if voice and voice in ("off", "cloud", "local_omni", "auto"):
                if pro is not None and hasattr(pro, "set_duplex_mode"):
                    try:
                        _mode = False if voice == "off" else voice
                        pro.set_duplex_mode(_mode)
                    except Exception:  # noqa: BLE001
                        logger.exception("双工引擎热切换失败（set_duplex_mode=%r）", voice)
            log_call_av("device_change", mic_device=str(mic_device),
                        camera_device=str(camera_device),
                        voice_engine=voice, video_engine=video, source="ui")
            return {"success": True,
                    "mic_device": str(mic_device), "camera_device": str(camera_device),
                    "voice_engine": voice, "video_engine": video}
        except Exception as e:  # noqa: BLE001
            logger.exception("保存呼叫设置失败")
            return {"success": False, "error": f"写入配置失败: {e}"}

    # ======== 电脑控制 / 智能体 设置（2026-09-27） ========
    def get_agent_settings(self, data: dict = None) -> dict:
        """读取电脑控制代理设置。data 兼容可选（pywebview 桥/前端包装会传 {}）。"""
        """电脑控制代理设置面板：读 agent 段 + 解析实际使用的 LLM（供 UI 提示 + 本地弱模型警告）。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        raw = self._cfg.raw or {}
        agent_raw = raw.get("agent") or {}
        llm_raw = agent_raw.get("llm") or {}
        cas_raw = raw.get("cascade") or {}
        # 解析 agent 实际使用的 LLM（同 agent_brain.from_config 逻辑）
        if llm_raw.get("api_key"):
            base = (llm_raw.get("base_url") or "").strip()
            model = llm_raw.get("model") or ""
        else:
            base = (cas_raw.get("base_url") or "").strip()
            model = cas_raw.get("model") or ""
        base_low = (base or "").lower()
        if "localhost" in base_low or "127.0.0.1" in base_low or "ollama" in base_low:
            llm_kind = "local"
        elif any(k in base_low for k in ("dashscope", "open.bigmodel", "qwen", "zhipu", "aliyun")):
            llm_kind = "cloud"
        else:
            llm_kind = "unknown"
        browser = agent_raw.get("browser") or {}
        return {
            "success": True,
            "enabled": bool(agent_raw.get("enabled", False)),
            "route_chat": bool(agent_raw.get("route_chat", False)),
            "allow_tools_in_chat": bool(agent_raw.get("allow_tools_in_chat", False)),
            "require_confirm": bool(agent_raw.get("require_confirm", False)),
            "max_steps": int(agent_raw.get("max_steps", 6) or 6),
            "allow_command": bool(agent_raw.get("allow_command", False)),
            "allow_commands": list(agent_raw.get("allow_commands", []) or []),
            "browser_enabled": bool(browser.get("enabled", False)) if isinstance(browser, dict) else False,
            "llm_model": model,
            "llm_base": base,
            "llm_kind": llm_kind,
        }

    def save_agent_settings(self, data: dict) -> dict:
        """保存电脑控制代理设置（开关 / 路由 / 确认闸 / 步数 / 命令行白名单 / 浏览器）。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            d = data or {}
            raw = self._cfg.raw
            agent_raw = raw.get("agent")
            if not isinstance(agent_raw, dict):
                agent_raw = {}
                raw["agent"] = agent_raw
            if "enabled" in d:
                agent_raw["enabled"] = bool(d.get("enabled"))
            if "route_chat" in d:
                agent_raw["route_chat"] = bool(d.get("route_chat"))
            if "allow_tools_in_chat" in d:
                agent_raw["allow_tools_in_chat"] = bool(d.get("allow_tools_in_chat"))
            if "require_confirm" in d:
                agent_raw["require_confirm"] = bool(d.get("require_confirm"))
            if "max_steps" in d:
                try:
                    agent_raw["max_steps"] = max(1, int(d.get("max_steps") or 6))
                except Exception:  # noqa: BLE001
                    pass
            if "allow_command" in d:
                agent_raw["allow_command"] = bool(d.get("allow_command"))
            if "allow_commands" in d:
                cmds = d.get("allow_commands") or []
                agent_raw["allow_commands"] = [str(c).strip() for c in cmds if str(c).strip()]
            if "browser_enabled" in d:
                browser = agent_raw.get("browser")
                if not isinstance(browser, dict):
                    browser = {}
                    agent_raw["browser"] = browser
                browser["enabled"] = bool(d.get("browser_enabled"))
            self._save_config()
            from ..config import _to_ns
            self._cfg.ns = _to_ns(raw)
            return {"success": True, **self.get_agent_settings()}
        except Exception as e:  # noqa: BLE001
            logger.exception("保存电脑控制设置失败")
            return {"success": False, "error": f"写入配置失败: {e}"}

    def resolve_agent_confirm(self, key: str, allow: bool) -> dict:
        """电脑操作代理的"逐步确认"回执：前端确认框点允许/拒绝后调用，唤醒等待中的 future。

        fail-closed：找不到对应等待项（已超时/已处理）返回 success=False，绝不静默放行。
        """
        try:
            from ..llm.agent_confirm_broker import resolve_confirm
            ok = bool(resolve_confirm(str(key or ""), bool(allow)))
            return {"success": ok}
        except Exception as e:  # noqa: BLE001
            logger.exception("resolve_agent_confirm 失败")
            return {"success": False, "error": str(e)}

    def start_duplex_call(self, data: dict = None) -> dict:
        """按需拉起双工引擎（懒启动下「语音/视频双工通话」按钮用）。

        通话档位由 data.call_mode（voice/video）+ data.screen_share 决定；设置档位后
        重注对应提示词并重建会话。引擎按档位选：语音→语音引擎设置、视频→视频引擎设置。
        标记应用内通话进行中；锁循环保持连接，挂断自动断开回待命释放显存。
        """
        pro = getattr(self, "_proactive", None)
        if pro is None:
            # 【2026-09-17 可观测性补丁】此前这里**静默**返回：前端只看到「拉起失败」，
            # 后端日志一行没有、call_av 也不留痕 —— 事后完全无法归因（05:28:55 那批
            # call_end duration_s=0.0 就是这类静默早退的产物）。所有早退路径一律留痕。
            logger.warning("拨号被拒：主动策略未初始化（start_duplex_call）")
            log_call_av("call_start_rejected", reason="proactive_not_ready", source="ui")
            return {"success": False, "error": "主动策略未初始化",
                    "error_code": "proactive_not_ready"}
        d = data or {}
        # 【2026-10-17 修复·"她说话我完全听不到，但通话记录里有"】
        # 根因：`_duplex_ai_audio_off`（免提/关她的声音）是**实例级持久闸门**。
        # 用户在某一通里点过「免提（关闭她的声音）」后，这个 True 会一直留在
        # proactive 实例上；之后每一通里，_on_duplex_speak 都会在闸门处
        # `self._duplex_emit_audio = bytearray(); return` 把她的每一句**静默丢弃** ——
        # 表现就是"日志里有『收到音频…已送入播放器队列』、通话记录也有文本，
        # 但扬声器一点声音都没有"，而且**跨通话持续**（用户报的"老问题"）。
        # 修法：每次拨号都把闸门复位为"开"，并留一条日志便于事后归因。
        try:
            if getattr(pro, "_duplex_ai_audio_off", False):
                logger.info("拨号：检测到上一通遗留的『关AI声音』闸门，已复位为开启"
                            "（否则本通她说话会被静默丢弃）")
            pro._duplex_ai_audio_off = False
        except Exception:  # noqa: BLE001
            logger.debug("复位 _duplex_ai_audio_off 失败（忽略）", exc_info=True)
        # 【2026-09-23 修复·语音通话被默认成视频带视觉】此前兜底硬编码 "video"：
        # 前端「拨打语音」未明确传 call_mode/media 时，会被默认成视频双工（推屏帧、
        # 模型看屏），导致语音互动像「边听边看屏幕」、不自然。改为尊重配置
        # duplex_call_mode（已设 voice）→ 默认语音零视觉；前端明确传 video 仍走视频。
        _cfg_call_mode = (getattr(pro, "_duplex_call_mode_default", None) or "").strip().lower()
        _default_call_mode = _cfg_call_mode if _cfg_call_mode in ("voice", "video") else "voice"
        # 前端历史用 media 字段（voice/video），也兼容 call_mode；统一归一到 call_mode
        call_mode = str(d.get("call_mode") or d.get("media") or _default_call_mode).strip().lower()
        if call_mode not in ("voice", "video"):
            call_mode = _default_call_mode
        screen_share = bool(d.get("screen_share", False))
        # 视觉就绪提醒：打视频/屏幕共享通话时若本地视觉模型未就绪，右上角弹"无法加载视觉"
        if call_mode == "video":
            try:
                rdy = self.get_companion_readiness()
                if rdy.get("success") and rdy.get("need") in ("config", "model"):
                    from . import native_notify as _nn
                    _nn.show("模型未配置，无法加载视觉",
                             "这次通话暂时看不到画面（本地视觉模型未就绪）。可先语音聊天；"
                             "去「设置 · 功能与性能」勾选并保存即后台下载，完成后重拨就能看屏。",
                             "focus", 8000, key="call_vision_missing")
            except Exception:  # noqa: BLE001
                logger.exception("通话视觉缺失弹窗失败（忽略）")
        # 按档位选引擎：语音→语音引擎设置；视频/屏幕共享→视频引擎设置
        _eng_src = ""   # 引擎值来历（日志归因用）——必须在**所有分支之前**定义：
        # 第 1 级（运行态属性）命中时不会进入下面的回落块，若只在块内定义，
        # 后面兜底分支引用它就会 NameError（静默把拨号整段打飞）。
        if call_mode == "voice":
            eng = (getattr(pro, "_duplex_voice_engine", "") or "").strip().lower()
        else:
            eng = (getattr(pro, "_duplex_video_engine", "") or "").strip().lower()
        if not eng:
            # 【2026-10-09 修复·重启后首次拨号走错引擎（"重进要点一次档位才接通"）】
            # _duplex_voice_engine / _duplex_video_engine 是 **内存属性**，全代码库只有
            # save_call_settings（设置页引擎卡）会写它；**启动时从未从 config 初始化**。
            # 于是每次重启后第一次拨号 getattr 恒为 '' → 落到下面 else 兜底 local_omni，
            # 与 config 里用户真正设的引擎、与档位反推出来的 duplex_mode 都可能不一致；
            # 只有用户在设置页再点一次档位/引擎卡（顺带 saveCallSettings）才对齐。
            # 这里补上确定性回落：config 显式配置 → 当前运行态 duplex_mode。
            _eng_src = ""
            try:
                _pro_raw = ((self._cfg.raw or {}).get("proactive") or {})
                eng = str(_pro_raw.get(
                    "duplex_voice_engine" if call_mode == "voice" else "duplex_video_engine"
                ) or "").strip().lower()
                if eng:
                    _eng_src = "config.proactive.duplex_%s_engine" % (
                        "voice" if call_mode == "voice" else "video")
            except Exception:  # noqa: BLE001
                eng = ""
            if not eng:
                _dm = str(getattr(pro, "_duplex_mode", "") or "").strip().lower()
                if _dm in ("realtime", "cloud"):
                    eng = {"realtime": "cloud", "cloud": "cloud"}[_dm]
                    _eng_src = "运行态 duplex_mode=%r" % _dm
                elif _dm in ("local_omni", "local"):
                    # 【2026-10-09 修正·混合档语音方向应本地】local_omni/local 反推只来自
                    # 「混合档」或「纯本地档」（这两种档位的语音方向本就是本地双工：混合档＝
                    # 本地语音+云端看屏，纯本地档＝纯本地语音），并非误反推。此前一律"踢去
                    # 云端"导致没绑 Qwen-Omni-Realtime Key 时被 no_cloud_duplex_cfg 拦死
                    # （"默认进混合档连不上，重选一次档位才连得上"的源头）。尊重本地语义：
                    # 语音回落 local_omni，出声所需的云端 TTS 由下方 no_cloud_tts 门控提示，
                    # 而非误判为要去绑实时 Key。
                    eng = _dm
                    _eng_src = "运行态 duplex_mode=%r（混合/本地档·语音本地）" % _dm
                else:
                    # _dm 为空（启动后首次拨号且引擎卡未显式保存）：以档位语义兜底——
                    # 混合档语音本地，其余走云端（默认策略）。
                    _cm = ""
                    try:
                        _cm = self._current_mode()
                    except Exception:  # noqa: BLE001
                        _cm = ""
                    if _cm == _mode_gates.MODE_MIXED:
                        eng = "local_omni"
                        _eng_src = "companion_mode=mixed（语音本地·兜底）"
                    else:
                        eng = "cloud"
                        _eng_src = "默认策略（云端）"
            if _eng_src:
                logger.info("拨号引擎解析：运行态属性为空，已按 %s 回落 → %r", _eng_src, eng)
        # 【2026-09-30】前端通话弹窗可显式指定引擎（云端双工=realtime / 本地双工=local_omni），
        # 覆盖配置档位，让弹窗"选云端 / 选本地"真正生效，而非永远按配置档位连。
        _ov = str(d.get("engine") or "").strip().lower()
        if _ov in ("local_omni", "local", "realtime", "cloud"):
            eng = _ov
        if eng in ("local_omni", "local"):
            target = eng
        elif eng in ("realtime", "cloud"):
            target = "realtime"
        else:
            # 【2026-10-09 策略变更·拨号默认走云端；没有云端就门控拦截，不静默回落本地】
            # 旧行为：空 / auto / off / 未知 → 一律静默兜底 local_omni。两个后果：
            #   (1) 想打云端的人被悄悄扔到本地链路，音色/延迟/可用性和预期全不一样；
            #   (2) 云端没配 Key 时不会报错，照样"拨通"，事后极难归因
            #       （02:09:38 那通 engine=local_omni 就是这么来的）。
            # 新策略：只有**用户显式选择本地双工**（设置页引擎卡 = local_omni，或拨号弹窗
            # 选「本地」）才走本地；其余（空 / auto / off / 未知 / 档位反推值）一律走云端。
            # 云端没配 Key+模型时，下面 no_cloud_duplex_cfg 门控**如实拦下**并返回两条解法
            # （去绑 Qwen-Omni Key / 显式改用本地双工），绝不静默回落。
            target = "realtime"
            logger.info("拨号引擎解析：未显式指定本地双工，按默认策略走云端 target=realtime"
                        "（来源=%s）", _eng_src or "未指定")
        # 档位能力表：仅用于诊断日志（拨号放行与否已改由"是否绑了云端双工"决定，
        # 见上方 no_cloud_duplex 门控）。这里必须保留计算——下方"拨号发起"日志会打印它。
        # 【2026-10-21 注·别被上面那段注释骗了】9172~9188 的「显式选本地双工就走本地」
        # **结果不生效**：紧接着 9196 起按 2026-10-16 用户决策无条件 `target = "realtime"`。
        # 那段留着是因为它顺带算出了 eng / _eng_src 供日志归因，但读到 9182「只有用户显式
        # 选择本地才走本地」时别当真 —— 那是 10-09 的策略，**10-16 已被否决**。
        # 当前契约由 `tools/test_call_engine_resolve.py` 组 1 守着：任何输入组合（内存值 /
        # config / 档位反推 / 弹窗显式指定本地）target 都必须是 realtime。
        _caps: set = set()
        try:
            _caps = set(pro.capabilities())
        except Exception:  # noqa: BLE001
            _caps = set()
        # 【2026-10-16 按用户明确要求·语音电话恒走云端】
        # ① 「语音电话」按钮一律走**云端双工**，不再按档位 / 引擎卡 / duplex_mode 解析本地；
        # ② 闸门改为拦住"**没有云端双工**"的情况（cloud_omni Key/模型未配）。
        # 旧实现的毛病（2026-10-16 实测）：
        #   · 读"档位能力表" caps（引导推导产物）来放行/拦截 —— 用户明明绑好了阿里云一键，
        #     却因为档位表只剩 ['long_memory']（token='chat'）而被拒：caps=['long_memory']
        #     token='chat' mode='local_omni'，同一份日志里本地引擎还在稳定推帧；
        #   · 同时 target 会按 duplex_mode 解析成本地引擎 —— 用户以为在打云端，实际走本机，
        #     音色/延迟/用量全不是他买的那套。
        if target != "realtime":
            logger.info("拨号引擎强制云端：原解析 target=%r（按用户要求，语音电话恒走云端）", target)
        target = "realtime"

        # 云端双工可用性 = cloud_omni 的 Key + 模型都配好
        _co_ok = False
        try:
            _co_ok = bool(pro._cloud_omni_enabled())
        except Exception as e:  # noqa: BLE001
            _co_ok = False
            logger.warning("拨号门控：读取云端双工配置异常，按未配置处理: %s", e)
        if not _co_ok:
            _diag = {}
            try:
                _diag = pro.tier_diag() if hasattr(pro, "tier_diag") else {}
            except Exception:  # noqa: BLE001
                _diag = {}
            logger.warning("拨号被拒：未配置云端双工（cloud_omni Key/模型）diag=%s call_mode=%s",
                           _diag, call_mode)
            log_call_av("call_start_rejected", reason="no_cloud_duplex",
                        call_mode=call_mode, screen_share=screen_share, diag=_diag, source="ui")
            try:
                pro._emit_tier_blocked("realtime")
            except Exception:  # noqa: BLE001
                pass
            return {"success": False, "error_code": "no_cloud_duplex", "diag": _diag,
                    "error": "语音电话走云端双工，需要先绑定云端双工：请到「设置 → 模型与 API → 云端双工」"
                             "填入你的阿里云百炼 Key（和聊天同一个 Key 即可），再重新拨号。"}

        # 【2026-09-30】云端双工(realtime=Qwen-Omni-Realtime)需用户自己的 cloud_omni Key/模型；
        # 未配置不要静默回退 MiniCPM 网关（实测 WS 握手超时、且同样要 Key），直接如实拦截提示。
        # 【2026-10-21 注·下面这段目前**不可达**】`_co_ok` 在 9216 已被判定为真（否则早 return
        # 了 no_cloud_duplex），而 `_cloud_omni_enabled()` 只读配置、同一调用内结果稳定，
        # 所以 `if not _co_ok` 恒假；同理下方 `if target in ("local_omni", "local")` 也恒假
        # （target 已在 9207 被强制成 realtime）。保留代码是为了"恢复本地双工"时有据可查，
        # **但不是活路径**：`no_cloud_duplex_cfg` / `fallback` / `no_cloud_tts` 三个旧契约
        # 已随 10-16 决策下线，`test_call_engine_resolve.py` 组 3 用 240 组输入证明它们不出现。
        # 谁要把本地双工加回来，必须同时改那段注释、这组测试，并确认用户是否真要恢复。
        if target == "realtime":
            _co_ok = False
            try:
                _co_ok = bool(pro._cloud_omni_enabled())
            except Exception:
                _co_ok = False
            if not _co_ok:
                _co_cfg = (self._cfg.raw.get("cloud_omni") or {}) if self._cfg else {}
                _co_ok = bool(isinstance(_co_cfg, dict) and _co_cfg.get("api_key") and _co_cfg.get("model"))
            if not _co_ok:
                logger.warning("拨号被拒：云端双工(realtime)未配置 cloud_omni Key/模型")
                log_call_av("call_start_rejected", reason="no_cloud_duplex_cfg",
                            call_mode=call_mode, target=target, source="ui")
                return {
                    "success": False,
                    "error_code": "no_cloud_duplex_cfg",
                    # 【2026-10-09】拆成「为什么 + 怎么解」两段：前端可直接渲染成
                    # 提示 + 两个按钮，而不是丢给用户一句长 toast 就完事。
                    "error": "拨号默认走云端双工，但你还没绑定阿里云 Qwen-Omni-Realtime 的 "
                             "Key 与模型，所以这次没拨出去（不会静默改用本地链路）。",
                    "action": "去「设置 · 双工引擎」绑定 Key 与模型即可；"
                              "也可以显式选择「本地双工」（免 Key、离线可用）。",
                    # 前端据此渲染「改用本地双工」快捷按钮（fallback 非空 = 有退路）
                    "fallback": "local_omni",
                    "fallback_label": "改用本地双工",
                    "settings_hint": "duplex_engine",
                }
        # 【2026-09-25 语音电话门控】本地双工出声走本地模型，但「语音电话」功能需云端 TTS
        # （一个 Key）才能拨通；云端双工自带，无需额外配置。本地双工且未绑任何云端 TTS
        # （cloud_omni Key 或带 Key 的云端音色档）→ 拦截拨号并提示去绑定。
        if target in ("local_omni", "local"):
            _co = (self._cfg.raw.get("cloud_omni") or {}) if self._cfg else {}
            _cloud_key = bool(isinstance(_co, dict) and _co.get("api_key"))
            _profiles = (self._cfg.raw.get("voice_profiles") or []) if self._cfg else []
            _cloud_profile = any(isinstance(p, dict)
                                 and str(p.get("engine") or "") in (
                                     "dashscope_cosy", "minimax", "openai_tts",
                                     "gpt-sovits", "voxcpm", "mimo_tts")
                                 and p.get("api_key") for p in _profiles)
            if not (_cloud_key or _cloud_profile):
                logger.warning("拨号被拒：本地双工未绑云端 TTS（语音电话需云端TTS）target=%s", target)
                log_call_av("call_start_rejected", reason="no_cloud_tts",
                            call_mode=call_mode, target=target, source="ui")
                return {"success": False, "error_code": "no_cloud_tts",
                        "error": "本地双工需先在「语音·音色」绑定云端 TTS（一个 Key）才能语音通话；"
                                 "云端双工已自带，无需额外配置。"}
        # 【2026-10-09 诊断补强·"拨了没接通"】本地双工路径的成败几乎全在麦克风上行：
        # 实测（02:09:38 那通）Comni 侧 ws_open/prepared 都成功、UI 显示已连，但 mic 被判
        # weak（人声块 0%）→ 上行零人声 → 她全程不回应，用户观感就是"没接通"。
        # 之前这条日志只有档位/引擎，看不出麦克风这一层，事后只能靠人肉翻 mic 健康行。
        # 这里把「当前 mic 设备 + 上次 mic 健康结论」一起打出来，一眼可归因。
        _mic_diag = ""
        if target in ("local_omni", "local"):
            try:
                _cli = pro._active_duplex_client() if pro is not None else None
                _mdn = str(getattr(_cli, "_mic_dev_name", "") or "") if _cli is not None else ""
                _mh = str(getattr(_cli, "_mic_health", "") or "") if _cli is not None else ""
                _mic_diag = (" mic设备=%r" % _mdn) if _mdn else ""
                if _mh:
                    _mic_diag += " mic健康=%s" % _mh
                    if _mh in ("weak", "dead"):
                        logger.warning(
                            "拨号预警：麦克风上一次健康判定为【%s】——上行很可能没有人声，"
                            "对方会表现为「接通了但不理你」。可在「通话设置」换一个输入设备"
                            "（本机 MME/api=0 那一路通常是真拾音路）。", _mh)
            except Exception:  # noqa: BLE001
                _mic_diag = ""
        logger.info("拨号发起：call_mode=%s screen_share=%s engine=%s caps=%s%s",
                    call_mode, screen_share, target, sorted(_caps), _mic_diag)
        try:
            # 【2026-09-07 深夜·会话纪元门控·顺序铁律】先把"通话中/已就绪/重连/纪元起点"全部
            # 就位，再触发 set_call_profile（内部 reprepare 会发起本次真实重连）——保证本次通话
            # 的 reconnecting/ws_open/连接中 重连事件在 app_call_active=True 期间发出，能被正确
            # 记为"本次重连"；call_start_epoch 必须在重连之前捕获 = 当前纪元，这样"本次重连产生的
            # 新会话纪元"才严格大于起点。旧 bug 正是把 app_call_active 设在 set_call_profile 之后，
            # 导致重连事件落在 app_call_active=False → 纪元门控失效 → 秒接通。
            pro._duplex_app_call_active = True
            pro._duplex_call_active_since = time.time()
            # 【2026-09-12 修复·拨号即被误杀】播种通话心跳时间戳：锁循环每 2s 一拍，
            # 若不播种，下一拍会读到上一次通话的旧值（首次通话为 0）→ 立即命中
            # _frontend_gone → 拨号刚发起就被"自愈"解锁。
            pro._last_call_poll_ts = time.time()
            pro._duplex_call_connected = True
            # 【2026-10-04·会话切分】一次"拨号↔挂断"= 一个通话会话。拨号即开新会话，
            # 本次通话期间的所有逐句记录都打这个 session_id；挂断不清（留待迟到尾部消息并入），
            # 下次拨号再开新会话。详见 _on_call_speech / _build_call_sessions。
            self._cur_call_session_id = "call_%d" % int(time.time())
            self._cur_call_session_start = time.time()
            # 【2026-09-07 修复·拨号真值判断】发起通话先清"真·已就绪"标记，等后端真正重连成功
            # + 提示词注入完(prepared/会话建立→session_epoch 严格大于起点)才置 True，前端据此"拨通"。
            pro._duplex_call_ready = False
            pro._duplex_call_model_loading = False
            pro._duplex_call_error = ""
            pro._duplex_call_start_epoch = (getattr(pro, "_duplex_session_epoch", 0) or 0)
            # 【2026-10-08 修复·挂断按设置恢复看屏】捕获通话前客户端模式，挂断时按此恢复，
            # 不盲设 video——避免把用户关掉的本地看屏又打开。getattr 兼容无 mode 属性的客户端。
            try:
                _pc = pro._active_duplex_client()
                pro._precall_client_mode = getattr(_pc, "mode", None)
            except Exception:  # noqa: BLE001
                pro._precall_client_mode = None
            # 先设档位（写状态+重解析视觉源+切音频源+重注提示词），再连引擎——
            # 保证 prepare/重连时提示词已是最新档位。此调用会触发本次真实重连（reprepare）。
            pro.set_call_profile(call_mode, screen_share)
            pro._switch_duplex_mode(
                target,
                "你开始双工通话了，我连上本机双工陪你，挂断自动断开、不占显卡～",
                reason="call_start", force_popup=True)
            # 【2026-10-02 修·卡拨号·对齐启动器】目标引擎已在跑时 _switch_duplex_mode
            # 幂等短路（mode==cur 直接 return，日志 12:46:43 实锤：拨号发起后除弹窗外
            # 零动作）——不会重建会话、没有新"会话建立"→ session_epoch/ready 都不翻，
            # 前端拨号页空等 90s 超时。而云端 Qwen-Omni 的传输层其实**早已连上**
            # （启动自动连云端/game_lock 预连，WebSocket open=connect() 成功），按
            # 「拨通即显示」语义（tools/cloud_companion_launcher.py：connect 返回即
            # 翻绿）直接置 ready。本地双工不受影响：它走 reprepare→prepared 原路径
            # 置 ready（此兜底只认 realtime 引擎且 client._connected 为真）。
            if (target in ("realtime", "cloud")
                    and str(getattr(pro, "_duplex_mode", "") or "") == "realtime"
                    and getattr(pro, "_duplex_app_call_active", False)
                    and not getattr(pro, "_duplex_call_ready", False)
                    and bool(getattr(getattr(pro, "_duplex_client", None),
                                     "_connected", False))):
                pro._mark_duplex_ready_from_cloud()
        except Exception as e:  # noqa: BLE001
            # 【2026-09-17】异常时**必须回滚已种下的通话标志**：上面在 try 之前就把
            # _duplex_app_call_active 置 True 了，一旦后面抛错而标志残留，锁循环要等
            # 25~90s 才靠「通话锁自愈」救回来（期间模式切换被锁、提示词停在通话档）。
            logger.exception("按需拉起双工引擎失败")
            try:
                pro._duplex_app_call_active = False
                pro._duplex_call_connected = False
                pro._duplex_call_mode = ''
                pro._duplex_call_ready = False
                pro._duplex_call_model_loading = False
            except Exception:  # noqa: BLE001
                logger.debug("拨号失败回滚通话标志失败", exc_info=True)
            log_call_av("call_start_rejected", reason="exception", err=str(e),
                        call_mode=call_mode, source="ui")
            return {"success": False, "error": str(e), "error_code": "exception"}
        # 【2026-09-17 扬声器设备】通话建立时把用户选定的输出设备下发到 TTS 播放层
        # （选择持久化在 proactive.duplex_speaker_device；'default' 即系统默认/ffplay）。
        try:
            self._apply_call_speaker_device()
        except Exception:  # noqa: BLE001
            logger.debug("下发通话扬声器设备失败（忽略）", exc_info=True)
        log_call_av("call_start", call_mode=call_mode, screen_share=screen_share,
                    engine=target, source="ui")
        return {"success": True, "engine": target, "call_mode": call_mode,
                "screen_share": screen_share,
                "speaker_device": self._call_speaker_device_raw()}

    def get_tier_call_status(self) -> dict:
        """聊天页通话按钮门槛用：返回当前档位能否语音通话 + 原因 + 怎么解。

        2026-10-07 重写：改查 mode_gates 门槛矩阵。
        - 不再使用旧四档词汇（纯聊天/聊天+看得见/全感知伙伴），统一为 纯本地/云端/混合。
        - 「有云端双工能力但没 Key」与「压根没双工能力」现在能区分开，
          前者提示去绑 Key（而不是骗用户去升档）。
        - 后端未就绪 / 无 proactive 时返回 can_call=True（不锁人），避免预览模式被误锁。
        """
        mode = self._current_mode()
        r = _mode_gates.evaluate(mode, "voice_call", self._gate_ctx())
        can = (r["state"] == "ok")
        out = {
            "success": True,
            "mode": mode,
            "mode_label": _mode_gates.mode_label(mode),
            "can_call": can,
            "state": r["state"],
            "feature": "voice_call",
            "feature_label": r["label"],
        }
        if can:
            out["reason"] = ""
            out["action"] = ""
        else:
            out["reason"] = r["reason"]
            out["action"] = r["action"]
        # 附带整张表：聊天页提示条可一次说清"还有哪些功能被卡"
        try:
            tbl = _mode_gates.gate_table(mode, self._gate_ctx())
            out["blocked_summary"] = _mode_gates.blocked_summary(tbl)
            out["gate_table"] = tbl["items"]
        except Exception:  # noqa: BLE001
            pass
        return out

    def get_mode_gate_table(self) -> dict:
        """当前档位的完整门槛表（设置页/引导页渲染「哪些能用·哪些被卡+为什么」用）。

        每项：{id,label,state,side,reason,action,mode}；state=ok 表示可用，
        否则 reason（为什么被卡）+ action（怎么解开）均非空，前端直接展示即可。
        """
        try:
            mode = self._current_mode()
            ctx = self._gate_ctx()
            tbl = _mode_gates.gate_table(mode, ctx)
            tbl["success"] = True
            tbl["blocked_summary"] = _mode_gates.blocked_summary(tbl)
            return tbl
        except Exception as e:  # noqa: BLE001
            logger.exception("门槛表读取失败")
            return {"success": False, "error": str(e)}

    def check_mode_gate(self, data: dict = None) -> dict:
        """单个功能门槛查询：前端任何控件被点前都可先问一句"我能用吗/为什么不能"。

        入参 {"feature": "voice_call"}；返回 evaluate() 的完整结果 + 一个
        可直接 toast 的 combined 字段（reason + action 拼好，省前端拼错）。
        """
        data = data or {}
        fid = str(data.get("feature") or "").strip()
        if not fid:
            return {"success": False, "error": "缺少 feature"}
        if fid not in _mode_gates.FEATURE_LABELS:
            return {"success": False, "error": f"未知功能 {fid}"}
        try:
            mode = self._current_mode()
            r = _mode_gates.evaluate(mode, fid, self._gate_ctx())
            r["success"] = True
            r["mode_label"] = _mode_gates.mode_label(mode)
            r["combined"] = (f"{r['reason']}{r['action']}" if r["state"] != "ok" else "")
            return r
        except Exception as e:  # noqa: BLE001
            logger.exception("功能门槛查询失败")
            return {"success": False, "error": str(e)}

    def notify_mode_gate(self, data: dict = None) -> dict:
        """被档位卡住时弹原生浮窗：标题「这项功能不在你的档位里」+ 为什么 + 怎么解。

        前端任何控件被点前都可调它（配合 check_mode_gate），保证用户永远拿得到解释。
        """
        data = data or {}
        try:
            from . import mode_notify as _mn
            fid = str(data.get("feature") or "").strip()
            reason = str(data.get("reason") or "").strip()
            action = str(data.get("action") or "").strip()
            if not reason or not action:
                r = self.check_mode_gate({"feature": fid})
                reason = reason or str(r.get("reason") or "")
                action = action or str(r.get("action") or "")
            if not reason and not action:
                return {"success": False, "error": "缺少门槛文案"}
            mode = self._current_mode()
            _mn.show_mode_notify(
                "这项功能不在你的档位里",
                f"{reason}{action}",
                scene="back", timeout_ms=7000, key="mode_gate_block",
            )
            return {"success": True, "mode": mode}
        except Exception as e:  # noqa: BLE001
            logger.debug("门槛提示浮窗失败（忽略）: %s", e)
            return {"success": False, "error": str(e)}

    def notify_tier_call_blocked(self) -> dict:
        """聊天页点被档位锁死的通话按钮时，弹右上角提醒（与 _emit_tier_blocked 同款浮窗）。"""
        try:
            from . import mode_notify as _mn
            st = self.get_tier_call_status()
            _mn.show_mode_notify(
                "这项功能不在你的档位里",
                st.get("reason", "双工通话需双工能力，请升级档位")
                + (st.get("action") or ""),
                scene="back", timeout_ms=6000, key="tier_call_block",
            )
        except Exception:  # noqa: BLE001
            logger.debug("档位通话拦截弹窗失败（忽略）")
        return {"success": True}

    def set_call_screen_share(self, data: dict = None) -> dict:
        """通话中热切换屏幕共享（视频通话专属）：开→画面变屏幕共享、音频仍麦克风。

        复用 proactive.set_call_profile(call_mode, screen_share)：重解析视觉源(→screen)、
        切音频源(仍 mic)、重建会话重注提示词（三套之一）。未通话时忽略。
        """
        pro = getattr(self, "_proactive", None)
        if pro is None:
            return {"success": False, "error": "主动策略未初始化"}
        if not getattr(pro, "_duplex_app_call_active", False):
            return {"success": False, "error": "当前不在通话中"}
        d = data or {}
        screen_share = bool(d.get("screen_share", False))
        call_mode = (getattr(pro, "_duplex_call_mode", None) or "video")
        try:
            pro.set_call_profile(call_mode, screen_share)
            log_call_av("screen_share_toggle", screen_share=screen_share,
                        call_mode=call_mode, source="ui")
            return {"success": True, "call_mode": call_mode, "screen_share": screen_share}
        except Exception as e:  # noqa: BLE001
            logger.exception("屏幕共享热切换失败")
            return {"success": False, "error": str(e)}

    def set_call_mic(self, data: dict = None) -> dict:
        """通话「禁音」：关闭/恢复麦克风（达妮娅听不到你）。转 proactive.set_call_mic。"""
        pro = getattr(self, "_proactive", None)
        if pro is None:
            return {"success": False, "error": "主动策略未初始化"}
        if not getattr(pro, "_duplex_app_call_active", False):
            return {"success": False, "error": "当前不在通话中"}
        d = data or {}
        try:
            pro.set_call_mic(bool(d.get("muted", False)))
            log_call_av("mic_toggle", muted=bool(d.get("muted", False)), source="ui")
            return {"success": True, "muted": bool(d.get("muted", False))}
        except Exception as e:  # noqa: BLE001
            logger.exception("通话禁音失败")
            return {"success": False, "error": str(e)}

    def get_call_mic_level(self, data: dict = None) -> dict:
        """通话麦克风实时电平（供通话 UI 律动条：确认「我说的话是否真被采到」）。

        轻量只读：直接取双工客户端采集回调里已算好的平滑 RMS，通话中 UI 以 ~100ms 轮询。
        未通话时返回 in_call=False、level=0，UI 据此收起律动条。
        """
        pro = getattr(self, "_proactive", None)
        if pro is None or not hasattr(pro, "get_call_mic_level"):
            return {"success": False, "in_call": False, "active": False,
                    "level": 0.0, "rms": 0.0, "voice": False, "peak": 0.0, "ts": 0.0}
        try:
            lv = pro.get_call_mic_level()
            lv["success"] = True
            return lv
        except Exception as e:  # noqa: BLE001
            logger.debug("读取通话麦克风电平失败: %s", e)
            return {"success": False, "in_call": False, "active": False,
                    "level": 0.0, "rms": 0.0, "voice": False, "peak": 0.0, "ts": 0.0}

    def set_call_ai_audio(self, data: dict = None) -> dict:
        """通话「免提=关闭AI出声音」：enabled=False 时达妮娅闭嘴只听不说。"""
        pro = getattr(self, "_proactive", None)
        if pro is None:
            return {"success": False, "error": "主动策略未初始化"}
        if not getattr(pro, "_duplex_app_call_active", False):
            return {"success": False, "error": "当前不在通话中"}
        d = data or {}
        enabled = bool(d.get("enabled", True))
        try:
            pro.set_call_ai_audio(enabled)
            log_call_av("ai_audio_toggle", enabled=enabled, source="ui")
            return {"success": True, "enabled": enabled}
        except Exception as e:  # noqa: BLE001
            logger.exception("关AI声音失败")
            return {"success": False, "error": str(e)}

    def set_call_camera(self, data: dict = None) -> dict:
        """通话「关闭/开启摄像头」：关掉后达妮娅看不到你（不推相机帧）。"""
        pro = getattr(self, "_proactive", None)
        if pro is None:
            return {"success": False, "error": "主动策略未初始化"}
        if not getattr(pro, "_duplex_app_call_active", False):
            return {"success": False, "error": "当前不在通话中"}
        d = data or {}
        enabled = bool(d.get("enabled", True))
        try:
            pro.set_call_camera(enabled)
            log_call_av("camera_toggle", enabled=enabled, source="ui")
            return {"success": True, "enabled": enabled}
        except Exception as e:  # noqa: BLE001
            logger.exception("开关摄像头失败")
            return {"success": False, "error": str(e)}

    def get_notify_choices(self, data: dict = None) -> dict:
        """返回各场景"上次弹窗选择"（设置页展示：让用户知道自己选过什么）。"""
        try:
            from . import notify_choices as nc
            items = []
            for k in (nc.K_GAME_LOCK_UNLOAD, nc.K_GAME_UNLOCK_RESTORE,
                      nc.K_CLOUD_FAIL, nc.K_TIER_BLOCKED):
                items.append({"key": k, "choice": nc.get_choice(k) or "",
                              "desc": nc.describe(k)})
            return {"success": True, "items": items}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def reset_notify_choices(self, data: dict = None) -> dict:
        """恢复"每次都问"（设置页的「恢复默认询问」按钮）。"""
        try:
            from . import notify_choices as nc
            d = data or {}
            k = str(d.get("key") or "").strip()
            nc.forget(k)          # key 为空 = 清空全部
            logger.info("[弹窗选择] 已重置 key=%r", k or "(全部)")
            return {"success": True}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def get_mixed_mode(self) -> dict:
        """混合模式状态（设置页「混合模式」按钮显示用）。"""
        pro = getattr(self, "_proactive", None)
        if pro is None or not hasattr(pro, "get_mixed_mode_state"):
            return {"success": False, "error": "主动策略未初始化"}
        try:
            st = pro.get_mixed_mode_state()
            st["success"] = True
            return st
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def set_mixed_mode(self, data: dict = None) -> dict:
        """混合模式开关：云端 Qwen-Omni 出语音 + 本地 Comni 看屏（两条并行）。

        【2026-10-02 新增】本地 Comni 本身带视觉，"混合"就该让它参与看屏，而不是
        只切云端语音 + 另开 VLM 截图管线。

        【2026-10-08 P1·原子化】此前设置页一次点击要串**三次独立写盘**：
            saveDuplex({engine:'cloud',decouple_screen_voice:true})
            → setMixedMode(true) → setVisionEnabled(true)
        任一步失败/中断，配置就停在**半开状态**（语音切了云端但看屏没起、
        或看屏起了但视觉管线没开），用户看到的是"点了没反应/状态打架"。
        现支持 `atomic=true`：一次调用内顺序完成「双工引擎 + 解耦 + 混合开关
        + 视觉管线」，**最后只 _save_config() 一次**，并在任一步失败时回滚已改
        的内存态。老调用（不带 atomic）行为完全不变。
        """
        pro = getattr(self, "_proactive", None)
        if pro is None or not hasattr(pro, "set_mixed_mode"):
            return {"success": False, "error": "主动策略未初始化"}
        d = data or {}
        enabled = bool(d.get("enabled", True))
        if d.get("atomic"):
            return self._set_mixed_mode_atomic(enabled, d)
        try:
            r = pro.set_mixed_mode(enabled)
            if r and r.get("success"):
                logger.info("混合模式已切换: enabled=%s", enabled)
            return r or {"success": False, "error": "切换失败"}
        except Exception as e:  # noqa: BLE001
            logger.exception("混合模式切换失败")
            return {"success": False, "error": str(e)}

    def _set_mixed_mode_atomic(self, enabled: bool, d: dict) -> dict:
        """原子版混合模式：一次配置写盘完成全部孪生动作。

        开启：双工引擎→cloud + 解耦看屏 + 混合开关 on + 视觉管线 on
        关闭：双工引擎→local + 混合开关 off
        全程只在**最后一次** _save_config()；中途失败回滚内存配置快照。
        """
        from ..config import _to_ns
        raw = self._cfg.raw
        try:
            import copy as _copy
            _snapshot = _copy.deepcopy(raw)
        except Exception:  # noqa: BLE001
            _snapshot = None
        steps: list = []
        try:
            if enabled:
                # 1) 双工引擎切云端 + 解耦看屏（语音走云端，看屏由本地 Comni 负责）
                _s1 = self.save_duplex_settings({"engine": "cloud", "decouple_screen_voice": True})
                steps.append(("duplex", bool(_s1 and _s1.get("success"))))
                if not (_s1 and _s1.get("success")):
                    raise RuntimeError((_s1 or {}).get("error") or "双工引擎切换失败")
                # 2) 混合开关（驱动 Comni 看屏旁观者）
                _s2 = self._proactive.set_mixed_mode(True)
                steps.append(("mixed", bool(_s2 and _s2.get("success"))))
                if not (_s2 and _s2.get("success")):
                    raise RuntimeError((_s2 or {}).get("error") or "混合开关失败")
                # 3) 视觉管线（看屏总闸）
                try:
                    self.set_vision_enabled(True)
                    steps.append(("vision", True))
                except Exception as _e:  # noqa: BLE001
                    steps.append(("vision", False))
                    logger.warning("混合模式：视觉管线开启失败（不阻断）: %s", _e)
            else:
                _s1 = self._proactive.set_mixed_mode(False)
                steps.append(("mixed", bool(_s1 and _s1.get("success"))))
                _s2 = self.save_duplex_settings({"engine": "local"})
                steps.append(("duplex", bool(_s2 and _s2.get("success"))))
            # 只写一次盘
            self._save_config()
            self._cfg.ns = _to_ns(raw)
            logger.info("混合模式（原子）已切换: enabled=%s steps=%s", enabled, steps)
            return {"success": True, "enabled": enabled, "atomic": True, "steps": steps}
        except Exception as e:  # noqa: BLE001
            logger.exception("混合模式（原子）切换失败，回滚内存配置")
            if _snapshot is not None:
                try:
                    self._cfg.raw.clear()
                    self._cfg.raw.update(_snapshot)
                    self._cfg.ns = _to_ns(self._cfg.raw)
                except Exception:  # noqa: BLE001
                    logger.warning("混合模式回滚失败", exc_info=True)
            return {"success": False, "error": str(e), "atomic": True, "steps": steps, "rolled_back": _snapshot is not None}

    def end_duplex_call(self, data: dict = None) -> dict:
        """双工通话挂断（语音/视频面板结束）：应用内通话结束 → 回待命释放显存。

        仅当这次连接是通话按需连的且当前非游戏锁时才回待命；游戏锁优先保持云端。
        """
        pro = getattr(self, "_proactive", None)
        if pro is None:
            return {"success": False, "error": "主动策略未初始化"}
        try:
            # 【2026-09-08 防御·通话锁释放】挂断第一步强制清"通话中"标志，确保即便
            # 后续 end_call_restore 异常也能解锁，避免 _switch_duplex_mode 守卫因标志
            # 残留而永久锁死模式切换（用户实测挂断后游戏锁/解锁不再生效）。
            pro._duplex_app_call_active = False
            # 【2026-09-07 修复·挂断恢复陪伴】先回空闲陪伴态：视觉源回 screen、音频回
            # 系统音、提示词回旁观（end_call_restore 会清 app_call_active 并重建陪伴会话），
            # 再切引擎——确保达妮娅真正回到"正常陪伴档位"，而不是卡在通话档位/错误视觉源。
            pro.end_call_restore()
            # 【2026-09-07 修复·拨号真值判断】挂断后清"真·已就绪"与断连标记，避免下次
            # 拨号误读上一次状态（disconnected 必须下次拨打时重新观察真实连接才置 True）。
            pro._duplex_call_ready = False
            pro._duplex_call_model_loading = False
            pro._duplex_call_error = ""
            pro._duplex_call_start_epoch = 0
            # 【2026-09-07 修复·挂断回陪伴·打断切换】挂断不回待命(不卸载引擎)，而是把双工切回
            # 陪伴模式——达妮娅立刻接住桌宠互动，不再出现"挂断后桌宠没连回来"。引擎本就在通话时
            # 连着(local_omni/realtime)，end_call_restore 已把档位(视觉源/音频/提示词)切回陪伴，
            # 此处只保证引擎保持运行(失败通话则拉起)；锁循环 call_end 分支因 _duplex_call_connected
            # 已清不会被触发。_switch_duplex_mode 的 _lock_start 已对 reason="call_end" 从待命拉起
            # 引擎，故失败通话挂断也回到陪伴。游戏锁时不动(让云端保持给游戏让 GPU)。
            pro._duplex_call_connected = False
            # 【2026-10-02 修·按项目架构】本项目围绕「桌宠看屏幕互动」展开，双工引擎是
            # **常驻底座**（本地 Comni 常驻看屏 / 云端双工常驻对话），语音通话只是
            # "进通话模式"。挂断的正确语义是**退出通话态**（麦克风释放、音频源回系统
            # 环回、模式回 video），引擎继续跑桌宠互动 —— 已由 pro.end_call_restore
            # 的 _exit_call_mode_for_hangup 完成。
            # 这里【不再】强制切引擎档位：此前把"切回陪伴"实现成切到 local_omni，
            # 等于挂断后擅自把用户的云端档改掉（挂断只应退出通话，不该改用户选的档）。
            # 仅在"确实还在全屏游戏"时才按游戏锁语义保持/切云端（把显卡让给游戏）。
            _mon = getattr(pro, "_monitor", None)
            _game_fs = False
            if _mon is not None:
                try:
                    _game_fs = bool(_mon.is_game_foreground())
                except Exception:  # noqa: BLE001
                    logger.debug("挂断判定前景游戏失败", exc_info=True)
            if _game_fs:
                # 仍在游戏：按游戏锁语义切云端（显卡让给游戏，专属陪法由前景注入）
                pro._switch_duplex_mode(
                    "realtime",
                    "通话结束，看到你还在游戏里，我用云端陪你（显卡留给你）～",
                    reason="game_lock", force_popup=True, force_rebuild=True)
            # 非游戏场景：保持当前档位不动，仅由 end_call_restore 退出通话态。
            # 桌宠看屏互动按该档的常态继续（本地=Comni 常驻看屏 / 云端=常驻对话 /
            # 混合=云端语音+本地 Comni 看屏），这也是用户要的"人性化"。
        except Exception as e:  # noqa: BLE001
            logger.exception("双工通话结束处理失败")
            return {"success": False, "error": str(e)}
        _dur = 0.0
        try:
            _since = getattr(pro, "_duplex_call_active_since", 0) or 0
            if _since:
                _dur = round(time.time() - _since, 1)
        except Exception:  # noqa: BLE001
            pass
        log_call_av("call_end", duration_s=_dur, engine=(getattr(pro, "_duplex_mode", None) or ""),
                    source="ui")
        # 【2026-10-04 18:55】通话结束 → 关闭当前会话 id。
        # 本次通话的迟到尾部消息（挂断瞬间仍在路上）不受影响：它们会触发
        # `_on_call_speech` 的"懒开兜底"，但因 id 已清，会开一个**新** id——
        # 这正是想要的语义（挂断后的残留语音属于新的一小段，不该混进本次）。
        # 真正的迟到消息（仅几百毫秒内、与本次同一通话）由 `start_duplex_call`
        # 建立的 id 覆盖；这里只在**明确挂断**后才清。
        self._cur_call_session_id = None
        self._cur_call_session_start = 0.0
        return {"success": True}

    def cancel_duplex_call(self, data: dict = None) -> dict:
        """取消「仍在拨号中」的通话（P2：拨号可退出）。

        与 end_duplex_call 的唯一区别：**不写 call_end 通话记录**。
        为什么必须分成两个出口：拨号还没接通就取消，本质是"这次通话没发生"；
        走 end_duplex_call 会落一条 duration_s=0 的假记录，把通话历史污染成
        "打过一通 0 秒电话"，反过来把排查带偏（2026-09-17 复盘的 05:28:55 ×3
        就是这么来的）。

        但引擎侧必须照常退出通话态：否则麦克风还热着、提示词还停在通话档，
        用户以为取消了其实还在跑 —— 那正是"静默失效"最典型的一种。
        """
        pro = getattr(self, "_proactive", None)
        if pro is None:
            return {"success": False, "error": "主动策略未初始化"}
        try:
            _was_active = bool(getattr(pro, "_duplex_app_call_active", False))
            # 与 end_duplex_call 同样的复位序列（顺序不能变：先清通话锁再 restore）
            pro._duplex_app_call_active = False
            pro.end_call_restore()
            pro._duplex_call_ready = False
            pro._duplex_call_model_loading = False
            pro._duplex_call_error = ""
            pro._duplex_call_start_epoch = 0
            pro._duplex_call_connected = False
            log_call_av("call_cancel", was_active=_was_active, source="ui")
        except Exception as e:  # noqa: BLE001
            logger.exception("取消拨号失败")
            return {"success": False, "error": str(e)}
        self._cur_call_session_id = None
        self._cur_call_session_start = 0.0
        return {"success": True, "cancelled": True}

    def start_simplex_server(self, data: dict = None) -> dict:
        """按需拉起单工轮询服务（默认 127.0.0.1:9871），供内置单工通话视图使用。

        最佳努力：找不到 python-embed 启动器时返回 need_manual=True，由前端提示用户
        手动运行 tools\\launch_simplex.bat；任何异常都不会影响主程序启动/运行。
        """
        try:
            import subprocess as _sp
            import socket as _sock
            _port = int(os.environ.get("SIMPLEX_PORT", "9871") or "9871")
            # 已在运行则直接返回
            try:
                _s = _sock.create_connection(("127.0.0.1", _port), timeout=0.4)
                _s.close()
                return {"success": True, "already_running": True}
            except Exception:
                pass
            # 项目根目录（webapp.py 在 src/ui 下，根 = 上两级）
            _here = os.path.dirname(os.path.abspath(__file__))
            _root = os.path.dirname(os.path.dirname(_here))
            _script = os.path.join(_root, "tools", "simplex_voice_server.py")
            if not os.path.isfile(_script):
                return {"success": False, "need_manual": True,
                        "error": "未找到 simplex_voice_server.py，请手动运行 tools\\launch_simplex.bat"}
            # 候选 python-embed 启动器
            _cands = []
            try:
                _ns = getattr(self._cfg, "ns", None)
                _loe = getattr(_ns, "local_omni", None) if _ns is not None else None
                _embed = getattr(_loe, "python_embed", None) if _loe is not None else None
                if _embed:
                    _cands.append(_embed)
            except Exception:  # noqa: BLE001
                pass
            _cands.append(r"H:\Comni\_internal\resources\python-embed\python.exe")
            _cands.append(os.path.join(_root, "..", "Comni", "_internal", "resources", "python-embed", "python.exe"))
            _py = None
            for _c in _cands:
                if _c and os.path.isfile(_c):
                    _py = _c
                    break
            if _py is None:
                return {"success": False, "need_manual": True,
                        "error": "未找到 python-embed 启动器，请手动运行 tools\\launch_simplex.bat"}
            _cmd = [_py, _script]
            _proc = _sp.Popen(_cmd, cwd=_root, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                              creationflags=(0x08000000 if os.name == "nt" else 0))
            logger.info("已尝试拉起单工服务: %s (pid=%s)", _py, _proc.pid)
            # 等待端口就绪（最多 ~8s）
            _ok = False
            for _ in range(40):
                try:
                    _s = _sock.create_connection(("127.0.0.1", _port), timeout=0.3)
                    _s.close(); _ok = True; break
                except Exception:
                    _proc.poll()
                    if _proc.returncode is not None:
                        break
                    time.sleep(0.2)
            if _ok:
                return {"success": True, "already_running": False, "pid": _proc.pid}
            logger.warning("单工服务拉起后端口 %s 未就绪(pid=%s)，前端健康检查会重试", _port, _proc.pid)
            return {"success": False, "pending": True, "pid": _proc.pid}
        except Exception as e:  # noqa: BLE001
            logger.exception("start_simplex_server 失败")
            return {"success": False, "need_manual": True, "error": str(e)}

    def get_call_status(self, data: dict = None) -> dict:
        """拨号真值状态：前端拨号振铃态轮询此接口，直到 ready=True 才"拨通"。

        ready=True 表示双工会话已真正重连成功 且 对应提示词注入成功（服务端回
        prepared / 云端回 会话建立·connected）。error 非空表示重连/注入失败。
        仅当 _duplex_app_call_active 时这些字段有意义。
        """
        pro = getattr(self, "_proactive", None)
        if pro is None:
            return {"success": False, "error": "主动策略未初始化",
                    "call_active": False, "ready": False, "status": "", "error_msg": ""}
        # 【2026-09-12 事实更正】前端**只在拨号振铃期间**以 ~350ms 轮询本接口
        # （src/ui/web/index.html:901-922，接通即 break），接通后零轮询。
        # （旧注释称"通话期间每 350ms 轮询"是从未实现的设计意图，是通话约 10s 后
        #   必被看门狗误判"前端消失"的根因。）
        # 因此通话期间的心跳改由 ui_ping()（前端每 5s）经 _touch_call_heartbeat 续期；
        # 这里记录的是拨号期心跳，两者共同供锁循环判断"前端是否还活着"。
        if getattr(pro, "_duplex_app_call_active", False):
            pro._last_call_poll_ts = time.time()
        return {
            "success": True,
            "call_active": bool(getattr(pro, "_duplex_app_call_active", False)),
            "ready": bool(getattr(pro, "_duplex_call_ready", False)),
            "session_epoch": int(getattr(pro, "_duplex_session_epoch", 0) or 0),
            "call_start_epoch": int(getattr(pro, "_duplex_call_start_epoch", 0) or 0),
            "status": (getattr(pro, "_duplex_status_text", "")
                       or getattr(pro, "_duplex_status", "") or ""),
            "model_loading": bool(getattr(pro, "_duplex_call_model_loading", False)),
            "error_msg": getattr(pro, "_duplex_call_error", "") or "",
            "engine": (getattr(pro, "_duplex_mode", None) or ""),
        }

    def get_call_subtitles(self, data: dict = None) -> dict:
        """通话字幕源（取代前端刮聊天 DOM 的双边字幕流）。

        【2026-10-13 重写·修"字幕显示的是聊天页的字"】旧实现把 `_chat_buffer`（聊天页
        消息缓冲）当字幕源：通话里用户在聊天页打的字、聊天页的 ai 回复全被当成通话字幕
        推上屏——这正是开发者截图里"字幕串台"的直接原因。
        现改为**只认通话链路自己的转写**（`_call_records`，CALL_SPEECH 事件逐句落）：
          · 她说的 = speaker=assistant（云端：Qwen-Omni 下行句；本地：_flush_duplex_emit）
          · 我说的 = speaker=user（云端：_on_cloud_omni_user_text；本地：_mark_user_speech）
        按当前通话会话（session_id）过滤，ts 升序，每条带稳定 seq 供前端增量取。
        兜底：若本会话尚无逐句记录（极端：会话懒开前的迟到句），退回 _duplex_output_log
        （双工互动回复日志，仍是通话自有文本、不是聊天页内容）。
        仅当 _duplex_app_call_active 时有意义（非通话返回 active=False）。
        """
        pro = getattr(self, "_proactive", None)
        if pro is None or not getattr(pro, "_duplex_app_call_active", False):
            return {"success": True, "active": False, "lines": [], "cursor": 0}
        start = float(getattr(pro, "_duplex_call_start_epoch", 0) or 0)
        sess = (getattr(self, "_cur_call_session_id", None) or "").strip()
        recs = list(getattr(self, "_call_records", []) or [])
        items = []
        if recs:
            # 主源：逐句通话记录。会话过滤：优先按 session_id 精确匹配；
            # 会话懒开兜底场景（record.session_id 为空）退用 ts>=拨号起点。
            sess_recs = []
            for r in recs:
                if not isinstance(r, dict):
                    continue
                r_sess = (r.get("session_id") or "").strip()
                if sess and r_sess == sess:
                    sess_recs.append(r)
                elif not r_sess and (not start or float(r.get("ts") or 0) >= start):
                    sess_recs.append(r)
            if sess_recs:
                for r in sess_recs:
                    items.append({
                        "side": "me" if r.get("speaker") == "user" else "her",
                        "text": r.get("text") or "",
                        "ts": float(r.get("ts") or 0),
                    })
        if not items:
            # 兜底：本会话一条记录都没有 → 只给"她说的"双工输出日志
            #（绝不回退聊天缓冲——那就是串台 bug 本身）。
            for e in (getattr(self, "_duplex_output_log", []) or []):
                _ts = float(e.get("ts") or 0)
                if start and _ts < start:
                    continue
                items.append({"side": "her", "text": e.get("text") or "", "ts": _ts})
        items.sort(key=lambda x: x["ts"])
        for i, x in enumerate(items):
            x["seq"] = i + 1
        return {"success": True, "active": True, "lines": items, "cursor": len(items)}

    def log_call_av(self, data: dict = None) -> dict:
        """前端媒体/控制事件落「通话音视频追踪日志」（app_backend.logCallAv 桥接）。

        前端在真实媒体流事件发生时调用（getDisplayMedia 授权/被拒、track.onended 原生停止共享、
        麦克风/摄像头/免提/屏幕共享切换、call 起止），把这些「浏览器侧真相」持久化到
        data/call_av_log.jsonl，弥补后端只知「意图」不知「媒体流实际状态」的盲区。
        """
        d = data or {}
        ev = (d.get("event") or d.get("type") or "frontend_event")
        fields = {k: v for k, v in d.items() if k not in ("event", "type")}
        try:
            log_call_av(str(ev), source="frontend", **fields)
        except Exception:  # noqa: BLE001
            logger.debug("log_call_av 记录失败", exc_info=True)
        return {"success": True}

    def camera_frame(self, data: dict = None) -> dict:
        """取一帧摄像头预览 JPEG（base64）。设置页实时预览用；不占用双工摄像头。"""
        try:
            device = "default"
            if isinstance(data, dict):
                device = data.get("device", "default") or "default"
            from ..perception.camera_capture import grab_preview
            import base64
            b = grab_preview(device)
            if not b:
                return {"success": False, "error": "no_frame"}
            return {"success": True, "jpeg": base64.b64encode(b).decode("ascii")}
        except Exception as e:  # noqa: BLE001
            logger.debug("摄像头预览取帧失败: %s", e)
            return {"success": False, "error": str(e)}

    def list_cameras(self, data: dict = None) -> dict:
        """列出摄像头设备（供引导页视频屏选择）。"""
        try:
            from ..perception.camera_capture import list_cameras as _list_cameras
            devs = _list_cameras()
            current = ((self._cfg.raw.get("proactive", {}) or {}).get("duplex_camera_device")
                       or "default")
            return {"success": True, "devices": devs, "current": str(current)}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def set_camera_device(self, data: dict = None) -> dict:
        """写入摄像头设备（绕过视觉采集热切换，落盘 proactive.duplex_camera_device）。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        d = data or {}
        dev = str(d.get("device", "default") or "default")
        try:
            raw = self._cfg.raw
            raw.setdefault("proactive", {})["duplex_camera_device"] = dev
            self._save_config()
            from ..config import _to_ns
            self._cfg.ns = _to_ns(raw)
            return {"success": True, "device": dev}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def get_duplex_capacity(self) -> dict:
        """JS 桥超时保护壳：底层逻辑见 _get_duplex_capacity_impl。"""
        _box: dict = {}

        def _run():
            try:
                _box["v"] = self._get_duplex_capacity_impl()
            except Exception:  # noqa: BLE001
                pass

        _t = threading.Thread(target=_run, daemon=True)
        _t.start()
        _t.join(timeout=6.0)
        if "v" in _box:
            return _box["v"]
        logger.warning("[双工容量] get_duplex_capacity 超时/阻塞，返回兜底避免 UI 冻结")
        return {"success": True, "gateway_healthy": False, "reachable": False,
                "total_workers": None, "idle_workers": None, "busy_workers": None,
                "queue_length": None, "is_full": None, "duplex_mode": "realtime",
                "local_omni_active": False, "endpoint": ""}

    def _get_duplex_capacity_impl(self) -> dict:
        """返回云端双工网关实时容量（引导界面 / 游戏双工界面用：是否沾满、剩几个端口）。

        数据来源：MiniCPM 网关 /status（wss 端点 → https，复用 _gateway_status）。
        - total_workers / idle_workers：网关共享 worker 总数 / 当前空闲（= 聊天+看屏幕
          双工还能再开几个）。
        - queue_length：排队人数。idle_workers<=0 即「已满，新会话将排队」。
        本地双工（local_omni）不占云端 worker，单独标注。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        pro_raw = self._cfg.raw.get("proactive", {}) or {}
        pro = getattr(self, "_proactive", None)
        # 双工网关端点：优先自定义 duplex_endpoint，否则回退 llm.minicpm.endpoint
        endpoint = pro_raw.get("duplex_endpoint") or ""
        if not endpoint:
            llm_ns = getattr(self._cfg.ns, "llm", None)
            mc = getattr(llm_ns, "minicpm", None) if llm_ns else None
            if mc is not None:
                endpoint = getattr(mc, "endpoint", "") or ""
        # 当前双工运行态
        if pro is not None and getattr(pro, "_duplex_mode", None) is not None:
            mode = pro._duplex_mode
        else:
            mode = pro_raw.get("duplex_mode", "realtime")
        if isinstance(mode, str):
            mode = mode.strip().lower()
        local_omni_active = (mode == "local_omni")
        st = self._gateway_status(endpoint) if endpoint else None
        if not isinstance(st, dict):
            # 网关不可达（离线/域名解析失败）→ 视为未知，但不报错
            return {
                "success": True,
                "gateway_healthy": False,
                "reachable": False,
                "total_workers": None,
                "idle_workers": None,
                "busy_workers": None,
                "queue_length": None,
                "is_full": None,
                "duplex_mode": mode,
                "local_omni_active": local_omni_active,
                "endpoint": endpoint,
            }
        total = int(st.get("total_workers") or 0)
        idle = int(st.get("idle_workers") or 0)
        busy = int(st.get("busy_workers") or 0)
        queue = int(st.get("queue_length") or 0)
        return {
            "success": True,
            "gateway_healthy": bool(st.get("gateway_healthy", False)),
            "reachable": True,
            "total_workers": total,
            "idle_workers": idle,
            "busy_workers": busy,
            "queue_length": queue,
            "is_full": idle <= 0,
            "duplex_mode": mode,
            "local_omni_active": local_omni_active,
            "endpoint": endpoint,
        }

    # ==========================================================
    # 首启引导（first_launch_setup）接入：approach A 全屏 overlay
    # ==========================================================
    def is_first_run(self) -> dict:
        """首启引导是否未完成：config.yaml 无 first_run_done 标记即视为首次启动。

        调试开关 force_first_run=true 时强制返回 first_run=True（反复重启看首启效果用），
        不影响真实用户：发布前置 false 即恢复"老用户不弹引导"。

        同步设置 mode_notify 首启静默锁：引导期间屏蔽所有模式切换/陪伴切换弹窗。
        """
        if not self._cfg:
            logger.warning("[首启判定] _cfg 未注入 → 按 first_run=True 处理（请检查初始化顺序）")
            # 【2026-10-02】同样"只升不降"：配置未就绪不代表要重新锁上
            # （此前无条件开锁，会把已完成的引导又冻回去）。
            try:
                from . import mode_notify
                if not mode_notify.is_onboarding_silent():
                    mode_notify.set_onboarding_silent(True)
            except Exception:  # noqa: BLE001
                pass
            return {"success": True, "first_run": True}
        raw = self._cfg.raw
        force = bool(raw.get("force_first_run", False))
        first = force or (not bool(raw.get("first_run_done", False)))
        logger.info("[首启判定] force_first_run=%s first_run_done=%s → first_run=%s",
                    force, raw.get("first_run_done"), first)
        try:
            from . import mode_notify
            # 【2026-10-02 修复·静默锁关不掉 → 软件切换全链路冻住】
            # 原实现无条件 `set_onboarding_silent(bool(first))`，而本方法会被前端
            # **反复调用**（每次打开设置/切页都问一次"是不是首启"）。于是：
            #   引导完成 → save_first_run_config 关闭静默锁
            #   → 前端下次调 is_first_run → 又把锁打开（若仍判为 first）
            #   → SwitchController._commit 全部走"首启静默提交" → 软件切换冻住
            #   （实测：21:00:16 关闭 → 21:00:35 又开启 → 21:01:18 切抖音被静默）
            # 现在改为**只升不降**：引导完成（first_run=False）后永久解锁，
            # 后续任何 is_first_run() 调用都不会再把锁打开。
            if not first:
                mode_notify.set_onboarding_silent(False)
            elif not mode_notify.is_onboarding_silent():
                # 仍是首启且锁还没开 → 开一次（幂等）
                mode_notify.set_onboarding_silent(True)
        except Exception:  # noqa: BLE001
            logger.debug("[首启判定] 设置 mode_notify 静默锁失败", exc_info=True)
        # force 同时回给前端：引导页据此判断「强制重走体验」并清理本地缓存，
        # 真实新用户（force=false）不受影响。
        return {"success": True, "first_run": first, "force": force}

    # ===== 统一「功能集」目录：引导页与设置页共用的唯一真相源 =====
    # 每个功能一项；group 决定归入「本地模块/云端模块/其它」；vram 为本地显存估算
    # （云端项 vram=0）。desc 给人话说明。has_quant 仅本地双工，带 Q4/Q6 量化选档。
    # 三档预设（纯聊天/聊天+看得见/全感知伙伴）和「自定义配置」最终都展开成同一份
    # companion_features 列表落盘，引擎配置全部由 _features_to_engine_config 反推，
    # 杜绝引导页、自定义配置器、设置页三套开关各写各的（历史 bug 根源）。
    COMPANION_FEATURE_DEFS = {
        "local_duplex": {
            "group": "local", "label": "本地双工 M1（真双工·免费但高延迟）",
            "desc": "达妮娅常驻你电脑、永远在线、随时接话。语音焊死固定音——克隆声需走云端或挂 RVC 变声。",
            "vram": 0, "default": False, "has_quant": True,
        },
        "local_vision": {
            "group": "local", "label": "本地看屏 · 轻量多模态快照",
            "desc": "不开双工时也能看屏幕：每几秒截一帧理解画面（非真双工）。8GB 卡也跑得动。",
            "vram": 5.4, "default": False, "has_quant": False,
        },
        "local_qwen_tts": {
            "group": "local", "label": "本地语音克隆 · 千问 Qwen3-TTS",
            "desc": "零样本克隆达娅声，免费、零 key、零云。纯聊天时 GPU 空闲质量最好；和双工同开建议用 Q4 或走 CPU。",
            "vram": 2.5, "default": False, "has_quant": False,
        },
        "cloud_duplex": {
            "group": "cloud", "label": "云端双工 M2（MiniCPM-o）",
            "desc": "真双工放云端跑，可克隆音色。本地显卡零占用，但需联网、有少量延迟。",
            "vram": 0, "default": False, "has_quant": False,
        },
        "cloud_snapshot": {
            "group": "cloud", "label": "云端快照 M3（OCR 裁决 + 云 TTS）",
            "desc": "打游戏/看屏时的事件驱动模式，语音走云端 TTS，可克隆音色。本地几乎不占显存。",
            "vram": 0, "default": False, "has_quant": False,
        },
        "cloud_tts": {
            "group": "cloud", "label": "云端语音合成（CosyVoice / MiMo 克隆）",
            "desc": "达娅声走阿里百炼 / 小米 MiMo 云端克隆。免费额度够轻度闲聊；不占本地显存。",
            "vram": 0, "default": False, "has_quant": False,
        },
        "music": {
            "group": "other", "label": "听歌 / 音乐感知",
            "desc": "让达妮娅听懂你放的歌、跟着聊。需配合双工（本地或云端）使用。",
            "vram": 0, "default": False, "has_quant": False,
        },
        "long_memory": {
            "group": "other", "label": "长期记忆 / 上下文",
            "desc": "达妮娅记得你说过的话、你的习惯。默认开，关掉会变健忘。",
            "vram": 0, "default": True, "has_quant": False,
        },
    }
    # 三档预设 → 功能清单（自定义配置走 payload.features）。
    # 纯聊天：不开任何本地/双工模型，纯文本走云 API；
    # 聊天+看得见：本地看屏快照（≈6GB，即用户说的快照模型）；
    # 全感知伙伴：本地真双工 M1（看屏幕+随时接话）。
    PRESET_FEATURES = {
        "chat": [{"id": "long_memory"}],
        "vision": [{"id": "local_vision"}, {"id": "long_memory"}],
        "full": [{"id": "local_duplex"}, {"id": "long_memory"}],
        # 引导页两档（与界面词汇对齐）：本机免费=本地双工+本地看屏；云端付费=云端双工(+长期记忆)。
        # 2026-09-25 一度把云端档改成「纯聊天」以规避「选云端却冒出桌面看屏」，但那与用户预期
        # （选云端=云端双工）及引导页文案矛盾，且导致设置页方向卡把云端错显示成本地。
        # 现恢复云端档=cloud_duplex；桌面看屏复活问题由 _features_to_engine_config 的权威门控
        # （cloud_duplex 不进入 vm.enabled 集合，见下方 6257 行）彻底解决——云端真双工自带云端视觉，
        # 不会再拉起本机/云端独立看屏循环。
        "local_free": [{"id": "local_duplex"}, {"id": "local_vision"}, {"id": "long_memory"}],
        "cloud_pay": [{"id": "cloud_duplex"}, {"id": "long_memory"}],
        # 混合推荐（2026-09-29 新增）：最划算全功能路径 = 本地语音免费 + 云端看屏按需
        # （游戏时本地模型卸载、云端顶上不占显卡） + 云端记忆。云端走用户自己绑的阿里云
        # 百炼 Key（每模型 100 万 Token 免费额度内白嫖，超了才按量），不依赖任何免费共享服务。
        # 落盘须开 decouple_screen_voice（否则 local_duplex 会覆盖独立看屏循环、退化成纯本地）
        # 与 game_cloud_watch（游戏时云端看屏不节流）。
        "mixed": [{"id": "local_duplex"}, {"id": "cloud_snapshot"}, {"id": "long_memory"}],
        "custom": None,  # 来自 payload.features
    }

    def _features_to_engine_config(self, features, raw: dict) -> set:
        """把归一后的功能清单反推成真实引擎配置（duplex_mode / vision / clone / 感知 / 记忆）。

        双工引擎优先级：本地双工 > 本地看屏快照 > 云端双工 > 云端快照；都不选 → 关（纯聊天）。
        返回启用功能 id 集合，供调用方按需触发模型准备。
        """
        ids: set = set()
        quant = "Q4_K_M"  # 默认量化档（用户要求统一 Q4，最轻量流畅档）
        for f in (features or []):
            if isinstance(f, dict) and f.get("id") in self.COMPANION_FEATURE_DEFS:
                ids.add(f["id"])
                if f["id"] == "local_duplex" and isinstance(f.get("opts"), dict):
                    quant = f["opts"].get("quant") or "Q4_K_M"
        pro = raw.setdefault("proactive", {})
        vm = raw.setdefault("vision_models", {})
        # 看屏与语音双工解耦开关（proactive.decouple_screen_voice，默认关闭）。
        # 关闭（默认·历史行为）：云端真双工(cloud_duplex)自带云端视觉，不拉起独立看屏循环，
        #   避免「选云端冒出桌面看屏」（与引导页「云端桌面看屏默认关闭」一致）。
        # 开启：云端 Qwen-Omni Realtime 实为纯语音（不抓屏、不传视频流），独立看屏循环
        #   必须保留；且「仅看屏」意图(local_vision/cloud_snapshot)不再覆盖双工引擎，
        #   使「云端语音 + 本地看屏」可并存。
        _decouple = bool(pro.get("decouple_screen_voice", False))
        if _decouple:
            # 语音双工引擎轴：cloud_duplex>local_duplex 决定 duplex_mode；仅看屏意图不抢引擎。
            if "local_duplex" in ids:
                pro["duplex_mode"] = "local_omni"
                if quant:
                    pro["duplex_local_omni_quant"] = quant
            elif "cloud_duplex" in ids:
                pro["duplex_mode"] = "realtime"
            elif "local_vision" in ids:
                pro["duplex_mode"] = True          # 仅看屏快照（无语音双工）
            elif "cloud_snapshot" in ids:
                pro["duplex_mode"] = "realtime"    # 仅看屏快照（云端视觉通道）
            else:
                pro["duplex_mode"] = False         # 纯聊天：不开双工
            # 【2026-10-17 解耦】这里只决定**运行时**是否存在（有双工/看屏能力即需要运行时）；
            # 不再用 duplex_mode 决定"主动互动"——chat_initiative 只由用户那个开关控制。
            pro["runtime_on"] = bool(pro.get("duplex_mode"))
            # 视觉（看屏幕）总开关：**云端档默认不启动桌面看屏**（用户 2026-09-27 明确要求：
            # 云端状态默认不跑视觉看屏互动，避免常驻调用 VLM 烧额度/拖慢前端）。
            # 故 cloud_duplex（云端语音）**不开屏**——它本就是纯语音不抓屏；
            # 只有显式选了看屏能力（local_vision / cloud_snapshot / local_duplex）才开屏。
            # 解耦的意义在上方语音轴：选云端语音时，若用户此前已开看屏，不会被强制关掉。
            # ⚠️【2026-10-04 19:47 修复·同 8265 行的开关语义混淆】看屏意图写
            # `screen_watch_enabled`，而 `enabled` 保持"绑了模型即可看图"。
            # 旧写法 `vm["enabled"] = bool(ids & {...看屏 id})` 会让选「云端付费」的
            # 用户**发图功能被连带关掉**（19:20 实锤：图被静默丢弃 → 模型改用
            # see_screen 截屏绕路）。详见下方同名字段的完整说明。
            vm["enabled"] = True
            vm["screen_watch_enabled"] = bool(
                ids & {"local_vision", "local_duplex", "cloud_snapshot"})
        else:
            # 双工引擎落点（与 8-27 档位决策对齐）
            if "local_duplex" in ids:
                pro["duplex_mode"] = "local_omni"
                if quant:
                    pro["duplex_local_omni_quant"] = quant
            elif "local_vision" in ids:
                pro["duplex_mode"] = True          # 本地快照循环（看屏非真双工）
            elif "cloud_duplex" in ids:
                pro["duplex_mode"] = "realtime"
            elif "cloud_snapshot" in ids:
                pro["duplex_mode"] = "realtime"    # 快照仍需一条双工通道
            else:
                pro["duplex_mode"] = False         # 纯聊天：不开双工，文本走云 API
            # 运行时总开关：有双工/看屏能力即需要运行时（纯聊天 duplex_mode=False → 关）。
            # 关键（2026-09-09）：此前只写 duplex_mode、不写 enabled，导致重启时运行时起不来。
            # 【2026-10-17 解耦】写入键改为 runtime_on：它管"运行时是否存在"；
            # "主动互动"（chat_initiative）从此与档位推导彻底无关，只由用户开关决定。
            pro["runtime_on"] = bool(pro.get("duplex_mode"))
            # ⚠️【2026-10-04 19:47 关键修复·开关语义混淆】
            # 下面这行原本是：
            #     vm["enabled"] = bool(ids & {"local_vision","local_duplex","cloud_snapshot"})
            # 它把 `vision_models.enabled` 当成「**看屏**意图」来写。但
            # `VisionRouter._perception_enabled()`（10-04 重构后）把同一个键当成
            # 「**看图能力**总可用性」在用（代码注释明确写"绑定了模型即 true"）。
            # ⇒ **一个键承担了两种语义**，后果实测（19:20 用户发达妮娅立绘问"认识吗"）：
            #   引导页选「云端付费」→ 不含 local_vision → enabled=False
            #   → 看图被连带关掉 → 聊天里发的图被**静默丢弃**（只传 7 个字）
            #   → 文本模型拿不到图，就调 see_screen 去截**电脑屏幕**，
            #     再把屏幕上碰巧出现的同一张图当成用户发的那张来描述 ⇒ 完全绕路。
            #
            # 正解（两件事彻底分开）：
            #   · `vision_models.enabled`            —— 看图能力：绑了模型就默认可用（用户主动发图）
            #   · `vision_models.screen_watch_enabled` —— 后台常驻看屏（隐私/额度，默认关）
            vm["enabled"] = True
            vm["screen_watch_enabled"] = bool(
                ids & {"local_vision", "local_duplex", "cloud_snapshot"})
        # 云端快照开关
        pro["duplex_snapshot_enabled"] = "cloud_snapshot" in ids
        # 语音克隆后端（本地千问优先于云端）
        clone = raw.setdefault("voice", {}).setdefault("clone", {})
        if "local_qwen_tts" in ids:
            clone["local_engine"] = "qwen3tts"
            clone["sub"] = "local"
        elif "cloud_tts" in ids:
            clone["sub"] = "cloud"
        # 音乐感知：仅真双工（本地全双工 local_omni / 云端双工 realtime）具备听歌能力；
        # 快照(local_vision/cloud_snapshot)不传声音、纯聊天也无 → 自动不开，无需 UI 开关。
        _mode = pro.get("duplex_mode")
        _auto_music = _mode in ("local_omni", "realtime")
        raw.setdefault("perception", {}).setdefault("audio", {})["enabled"] = _auto_music
        # 长期记忆
        raw.setdefault("memory", {})["enabled"] = ("long_memory" in ids)
        return ids

    def _derive_features_from_config(self, raw: dict) -> list:
        """旧用户（config 里没有 companion_features）从现有引擎配置回填一份功能清单。"""
        pro = raw.get("proactive", {}) or {}
        vm = raw.get("vision_models", {}) or {}
        feats = []
        mode = pro.get("duplex_mode")
        if isinstance(mode, str):
            mode = mode.strip().lower()
        if mode == "local_omni" or mode == "auto":
            feats.append({"id": "local_duplex",
                          "label": self.COMPANION_FEATURE_DEFS["local_duplex"]["label"],
                          "vram": 0, "opts": {}})
        elif mode is True or mode == "true" or mode == "snapshot":
            feats.append({"id": "local_vision",
                          "label": self.COMPANION_FEATURE_DEFS["local_vision"]["label"],
                          "vram": 5.4, "opts": {}})
        elif mode in ("realtime", "cloud"):
            feats.append({"id": "cloud_duplex",
                          "label": self.COMPANION_FEATURE_DEFS["cloud_duplex"]["label"],
                          "vram": 0, "opts": {}})
        if vm.get("enabled"):
            if not any(f["id"] == "local_vision" for f in feats):
                feats.append({"id": "local_vision",
                              "label": self.COMPANION_FEATURE_DEFS["local_vision"]["label"],
                              "vram": 5.4, "opts": {}})
        if pro.get("duplex_snapshot_enabled"):
            feats.append({"id": "cloud_snapshot",
                          "label": self.COMPANION_FEATURE_DEFS["cloud_snapshot"]["label"],
                          "vram": 0, "opts": {}})
        clone = (raw.get("voice", {}) or {}).get("clone", {}) or {}
        if clone.get("local_engine") == "qwen3tts" or clone.get("sub") == "local":
            feats.append({"id": "local_qwen_tts",
                          "label": self.COMPANION_FEATURE_DEFS["local_qwen_tts"]["label"],
                          "vram": 2.5, "opts": {}})
        elif clone.get("sub") == "cloud":
            feats.append({"id": "cloud_tts",
                          "label": self.COMPANION_FEATURE_DEFS["cloud_tts"]["label"],
                          "vram": 0, "opts": {}})
        if (raw.get("perception", {}) or {}).get("audio", {}).get("enabled"):
            feats.append({"id": "music",
                          "label": self.COMPANION_FEATURE_DEFS["music"]["label"],
                          "vram": 0, "opts": {}})
        if (raw.get("memory", {}) or {}).get("enabled", True):
            feats.append({"id": "long_memory",
                          "label": self.COMPANION_FEATURE_DEFS["long_memory"]["label"],
                          "vram": 0, "opts": {}})
        return feats

    def save_first_run_config(self, payload: dict = None) -> dict:
        """首启引导完成后落盘：记录陪伴模式 + 按模式映射 duplex_mode / 视觉开关 + 记录 TTS 选择。

        payload: {"mode": "纯聊天"|"聊天+看得见"|"全感知伙伴"|"自定义配置",
                  "tts":  {enabled, backend, api_key, voice_id, ...}}（可选，来自引导页 localStorage）
        仅写意图（duplex_mode / vision_models.enabled），不触发引擎热切换；真实引擎在
        proactive 初始化 / 用户后续操作时按配置拉起，避免首启即下载/加载本地模型。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            raw = self._cfg.raw
            payload = payload or {}
            mode = (payload.get("mode") or "").strip()
            # 2026-09-25 补引导页新方向名：旧表只有 纯聊天/聊天+看得见/全感知伙伴/自定义配置，
            # 「本机免费/云端付费」落到 token=None → save 被记成 skip（本地模型永不挂载）、
            # validate 的 features 恒 fail（完成按钮看似点不动）。
            token = {"纯聊天": "chat", "聊天+看得见": "vision",
                     "全感知伙伴": "full", "自定义配置": "custom",
                     "本机免费": "local_free", "云端付费": "cloud_pay",
                     "混合推荐": "mixed"}.get(mode)
            if not token:
                token = "skip"  # 跳过 / 未明确选择

            # 1) 首启标记（核心：决定以后是否还弹引导）
            raw["first_run_done"] = True
            # 2) 陪伴模式（供设置页 / 后续 redesign 读取）
            raw["companion_mode"] = token

            # 3) 归一功能集：所有档位（含预设）都展开成同一份 companion_features，
            #    再统一反推引擎配置——引导页三档与「自定义配置」终于共用一套真相源。
            if token == "custom":
                raw_features = payload.get("features")
                if not isinstance(raw_features, list) or not raw_features:
                    # 回退读配置器已落盘的功能集（G1 修复：自定义分支不再因缺 features 丢配置）
                    raw_features = self._cfg.raw.get("companion_features") or []
                if not isinstance(raw_features, list):
                    raw_features = []
            elif token == "skip" and payload.get("skip") is True:
                # 显式跳过：保底「纯聊天 + 长期记忆」功能集（零配置也能用）
                raw_features = list(self.PRESET_FEATURES.get("chat", []))
            else:
                preset = self.PRESET_FEATURES.get(token)
                raw_features = list(preset) if isinstance(preset, list) else []

            # 规范化：只保留目录里存在的 id，补齐 label/vram/opts，落盘 companion_features
            norm = []
            for f in raw_features:
                if isinstance(f, dict) and f.get("id") in self.COMPANION_FEATURE_DEFS:
                    fid = f["id"]
                    def_ = self.COMPANION_FEATURE_DEFS[fid]
                    norm.append({
                        "id": fid,
                        "label": def_["label"],
                        "vram": f.get("vram", def_["vram"]),
                        "opts": f.get("opts") or {},
                    })
            raw["companion_features"] = norm
            # 4) TTS 选择 → 映射进功能集（断点A 修复：选了本地千问必须加入功能集，
            #    否则 _kickoff_custom_model_prep 不会触发千问模型下载，语音永远不出来）。
            #    必须在 _features_to_engine_config / 模型准备之前处理。
            tts = payload.get("tts")
            if isinstance(tts, dict):
                raw["onboarding_tts"] = tts
                if tts.get("backend") == "local_qwen_tts" and not any(
                        f.get("id") == "local_qwen_tts" for f in norm):
                    def_ = self.COMPANION_FEATURE_DEFS.get("local_qwen_tts")
                    if def_:
                        norm.append({
                            "id": "local_qwen_tts",
                            "label": def_.get("label"),
                            "vram": def_.get("vram", 0),
                            "opts": tts.get("opts") or {},
                        })
                        raw["companion_features"] = norm
            # B1 修复：真实应用声音选择（不再只写 onboarding_tts 死配置，
            # 否则选了声音也永久静音）。voice 为结构化 {backend, api_key, ...}。
            _voice = payload.get("voice")
            if isinstance(_voice, dict) and _voice.get("backend"):
                try:
                    vr = self.apply_onboarding_voice(_voice)
                    logger.info("首启语音应用结果: %s", vr)
                except Exception:  # noqa: BLE001
                    logger.exception("首启语音应用失败（配置已保存，可在设置页补）")
            # 引导页自建角色落库（2026-09-25：此前只靠前端静默调 save_character，
            # 失败无人知 → 用户建的角色没出现在软件角色栏。完成后这里兜底落一次，幂等）。
            _role = payload.get("role")
            if isinstance(_role, dict) and _role.get("preset") == "custom" and isinstance(_role.get("char"), dict):
                try:
                    _c = dict(_role["char"])
                    _rc = self.save_character(_c)
                    logger.info("首启自建角色落库: %s", _rc)
                except Exception:  # noqa: BLE001
                    logger.exception("首启自建角色落库失败（可去设置-角色页重存）")
            # B2 修复：应用麦克风开关（修 B2：默认 mic_enabled=false 导致唤醒/语音全废）
            _mic = payload.get("mic")
            if isinstance(_mic, dict):
                try:
                    self.set_mic_device(_mic)
                except Exception:  # noqa: BLE001
                    logger.exception("首启麦克风配置失败")
            # 视频设备：写 proactive.duplex_camera_device（视频为可选，不强制）
            _cam = payload.get("camera")
            if isinstance(_cam, dict) and _cam.get("device") is not None:
                try:
                    self.set_camera_device({"device": _cam.get("device", "default")})
                except Exception:  # noqa: BLE001
                    logger.exception("首启摄像头配置失败")
            # 量化档（全感知伙伴可在引导页选 Q4/Q6；其它本地模式固定 Q4）
            _quant = payload.get("quant")
            if _quant and any(f.get("id") == "local_duplex" for f in norm):
                for f in norm:
                    if f.get("id") == "local_duplex":
                        f.setdefault("opts", {})["quant"] = _quant
            # 云端 TTS Key 持久化（断点B 修复：mimo / 自定义 的 key 之前只存 localStorage，
            # 没写进 config.yaml → 重启丢失、且设置页读不到。这里复用 set_voice_mode 的云端结构。）
            if isinstance(tts, dict) and tts.get("backend") in (
                    "mimo_tts", "dashscope_cosyvoice", "custom_tts"):
                _clone = raw.setdefault("voice", {}).setdefault("clone", {})
                _clone["sub"] = "cloud"
                _prov = {"mimo_tts": "mimo_tts",
                          "dashscope_cosyvoice": "dashscope_cosyvoice",
                          "custom_tts": "openai_compatible"}.get(tts.get("backend"), "custom")
                _clone["cloud"] = {
                    "provider": _prov,
                    "model_name": (tts.get("model") or tts.get("voice_id") or "").strip(),
                    "api_url": (tts.get("endpoint") or "").strip(),
                    "api_key": (tts.get("api_key") or "").strip(),
                }
                # 顶层 mimo_api_key（部分旧逻辑读取点）同步写
                if tts.get("backend") == "mimo_tts" and _clone["cloud"]["api_key"]:
                    raw.setdefault("voice", {})["mimo_api_key"] = _clone["cloud"]["api_key"]
            # LLM provider（纯聊天需要 Key 才能对话）落盘并热切换
            _llm = payload.get("llm")
            if isinstance(_llm, dict):
                try:
                    self.save_llm_settings(_llm)
                except Exception:  # noqa: BLE001
                    logger.warning("首启 LLM 配置保存失败（忽略，后续可在设置补）")
            # D5 修复（2026-09-26）：云端付费双工 Key 回流到 cloud_omni.api_key。
            # 否则干净用户选了服务商 Key，但云端双工(realtime→Qwen-Omni)因缺 key 仍走 MiniCPM 回退。
            _omni = payload.get("cloud_omni")
            if isinstance(_omni, dict) and _omni.get("api_key"):
                raw.setdefault("cloud_omni", {})["api_key"] = str(_omni["api_key"]).strip()
            # P0-1 修复（2026-09-29）：阿里云百炼「一个 Key 四样共用」。
            # 【2026-10-04 修·"换 Key 只改 2 处"的隐性不一致】用户换新号后重走引导，
            # 但 config.yaml 里仍有 26+ 处旧 Key（云端双工 1 + 视觉模型 20 + TTS 克隆…），
            # 表现＝引导页显示已配置、实际双工静默哑、视觉 403。根因＝旧实现只固定扩散
            # 三个段、且只对 provider 白名单生效，漏掉 text_chat.providers / llm.openai /
            # agent.llm / cascade / voice_profiles 等处。现改为递归全量扩散：一处填、全域生效。
            _prov = payload.get("provider")
            if isinstance(_prov, dict) and _prov.get("prov") == "bailian":
                _bkey = str(_prov.get("key") or "").strip()
                if _bkey:
                    # 1) 递归全量：所有百炼 Key 段统一替换（含视觉/双工/TTS/LLM/级联…）
                    _n = _diffuse_bailian_key(raw, _bkey)
                    # 2) 视觉模型：逐条目补 base_url（OpenAI 兼容网关共享百炼 Key）
                    _vm = raw.setdefault("vision_models", {})
                    for _m in (_vm.get("models") or []):
                        if isinstance(_m, dict) and str(_m.get("provider", "") or "") in (
                                "openai", "openai_compatible", "dashscope"):
                            if not _m.get("base_url"):
                                _m["base_url"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
                    # 3) 语音合成：百炼 CosyVoice / Qwen-Audio-TTS 云端克隆
                    _clone = raw.setdefault("voice", {}).setdefault("clone", {})
                    _clone["sub"] = "cloud"
                    _cc = _clone.setdefault("cloud", {})
                    _cc["provider"] = "dashscope_cosyvoice"
                    _cc["model_name"] = _cc.get("model_name") or "qwen-audio-3.0-tts-plus"
                    # 4) 云端双工（realtime→Qwen-Omni）鉴权 Key
                    raw.setdefault("cloud_omni", {})["api_key"] = _bkey
                    # 5) ⭐换 Key 后旧的双工复刻音色在新账号下并不存在（服务端报
                    #    "voice could not be found" → 静默哑）。清空它，让启动时按
                    #    cloud_omni.ref_audio 自动复刻一个（对齐「启动云端达妮娅.bat」）。
                    _co = raw.setdefault("cloud_omni", {})
                    _old_voice = str(_co.get("voice") or "").strip()
                    if _old_voice.startswith("qwen-omni-vc-"):
                        _co["voice"] = ""
                        logger.info("引导落盘：换 Key 后清空旧复刻音色 %s，"
                                    "改由启动时按 ref_audio 自动复刻", _old_voice)
                    logger.info("引导落盘：百炼 Key 已全量扩散到 %d 处配置", _n)
            # B4 修复（增强·2026-09-09）：本地功能（双工/快照）必须把用户选定的 model_dir
            # 可靠写进引擎配置，否则「模型检测到了、进主页却不加载」。
            # 旧实现只调 ensure_comni_layout({"model_dir": _md})：而 ensure_comni_layout 仅在
            # server_dir 有效（含 config.json）时才会把 model_dir 写进 config.json；若 server_dir
            # 是模板默认值（指向不存在的路径，或 model_dir 不匹配），则 ok=False、model_dir 永远
            # 写不进配置 → _comni_model_dir() 返回 None → warmLocalModel / run_loop 双双跳过挂载。
            # 现：优先解析真实 server_dir（旧值失效时从 Comni.exe 反推）；model_dir 同时落 config.yaml
            # （onboarding 真相源，_comni_model_dir 优先读它）与 Comni config.json（引擎 worker 实际读这里）。
            _md = payload.get("model_dir")
            if _md and _path_has_non_ascii(str(_md)):
                # 中文/非 ASCII 路径 C++ 引擎读不了 → 拒绝落盘，避免「挂不上」的诡异故障
                logger.warning("引导落盘拒绝非 ASCII 模型目录: %r", _md)
                _md = None
            _ids_local = any(f.get("id") in ("local_duplex", "local_vision") for f in norm)
            if _ids_local and _md:
                try:
                    _sdir = self._resolve_comni_server_dir(_md)
                    loe = raw.setdefault("proactive", {}).setdefault("local_omni_engine", {})
                    if _sdir:
                        loe["server_dir"] = _sdir
                    loe["model_dir"] = str(_md).strip()  # 直接落 config.yaml（最权威）
                    r = self.ensure_comni_layout(
                        {"model_dir": _md, "server_dir": _sdir} if _sdir else {"model_dir": _md})
                    if not r.get("ok"):
                        logger.warning("本地引擎目录未完全就绪(server_dir=%r): %s", _sdir, r)
                except Exception:  # noqa: BLE001
                    logger.warning("ensure_comni_layout 失败", exc_info=True)
            # 混合推荐（mixed）：本地语音 + 云端看屏并存 —— 必须开 decouple_screen_voice，
            # 否则 local_duplex 会覆盖独立看屏循环、退化成纯本地（云端看屏丢掉）。
            # game_cloud_watch 默认开：游戏时本地模型卸载、云端看屏顶上、不占显卡。
            if token == "mixed":
                raw.setdefault("proactive", {})["decouple_screen_voice"] = True
                # 【2026-10-16 改·不自动开启】原在此强制 game_cloud_watch=True（=云端看屏），
                # 与「档位自动推导、但重度能力由用户在体检页勾选」相冲突，故移除；
                # 现在只看用户在体检页「云端看屏幕」药丸的真实写入值。
            # 【2026-10-16 修复·引导不再自动开启重度能力】
            # 档位仍按实际配置自动推导（方案 A），但"是否真的常驻抓屏 / 采集系统音频 /
            # 主动搭话"必须以用户在体检页的勾选为准：_features_to_engine_config 会按档位预设
            # 把它们强制置 True（实测走完引导后 proactive.enabled / screen_watch_enabled /
            # perception.audio.enabled 全部被打开，紧接着挂载本地大模型、采集系统音频环回、
            # 每 2s 抓屏推流）。这里先取快照、算完再恢复，把决定权还给用户。
            _pre_pro = raw.setdefault("proactive", {})
            _pre_vm = raw.setdefault("vision_models", {})
            _pre_audio = raw.setdefault("perception", {}).setdefault("audio", {})
            _keep_caps = {
                # 【2026-10-17 解耦】保留"主动互动"（chat_initiative，旧键 enabled 兼容回退）
                "proactive_enabled": bool(_pre_pro.get(
                    "chat_initiative", _pre_pro.get("enabled", False))),
                "screen_watch": bool(_pre_vm.get("screen_watch_enabled", False)),
                "audio": bool(_pre_audio.get("enabled", False)),
            }
            ids = self._features_to_engine_config(norm, raw)
            # 【2026-10-17 解耦】只恢复"主动互动"；运行时恒为开（通话/看屏需要宿主）
            _pre_pro["chat_initiative"] = _keep_caps["proactive_enabled"]
            _pre_pro["enabled"] = _keep_caps["proactive_enabled"]
            _pre_pro["runtime_on"] = True
            _pre_vm["screen_watch_enabled"] = _keep_caps["screen_watch"]
            _pre_audio["enabled"] = _keep_caps["audio"]
            logger.info("引导落盘：重度能力以用户选择为准 proactive.enabled=%s "
                        "screen_watch_enabled=%s perception.audio.enabled=%s",
                        _keep_caps["proactive_enabled"], _keep_caps["screen_watch"],
                        _keep_caps["audio"])

            # 后台准备所选本地模型（自动下载，不阻塞引导）；本地功能（双工/快照/语音）任一启用即触发。
            # 与 save_companion_features 守卫保持一致：补上 local_vision（本地快照看屏与双工共用同一 GGUF）。
            if ids & {"local_duplex", "local_vision", "local_qwen_tts"}:
                self._kickoff_custom_model_prep(norm, raw.get("proactive", {}))

            self._save_config()
            from ..config import _to_ns
            self._cfg.ns = _to_ns(raw)
            logger.info("首启配置已保存：mode=%s -> %s", mode, token)
            # 触发首启延迟启动：仅当本进程处于"首启延迟"模式（后端订阅了
            # ONBOARDING_DONE 处理器）才广播；常规启动未订阅，广播为空操作。
            try:
                if not getattr(self, "_onboarding_fired", False) and self._bus is not None:
                    _dmode = ""
                    try:
                        _pro = getattr(self._cfg.ns, "proactive", None)
                        _dmode = str(getattr(_pro, "duplex_mode", "") or "")
                    except Exception:  # noqa: BLE001
                        _dmode = ""
                    self._bus.publish_threadsafe(
                        Event(EventType.ONBOARDING_DONE, {"duplex_mode": _dmode}, "webapp"))
                    self._onboarding_fired = True
                    logger.info("已广播 ONBOARDING_DONE（首启完成，后端按所选配置启动重型子系统）")
                from . import mode_notify
                mode_notify.set_onboarding_silent(False)
            except Exception:  # noqa: BLE001
                logger.exception("广播 ONBOARDING_DONE 或关闭静默锁失败（不影响配置保存）")
            return {"success": True, "mode": token}
        except Exception as e:  # noqa: BLE001
            logger.exception("保存首启配置失败")
            return {"success": False, "error": f"保存失败: {e}"}

    def get_companion_features(self) -> dict:
        """返回当前归一功能集（设置页「功能与性能」面板用）。

        兼容旧用户：config 里没有 companion_features 时，从现有引擎配置回填一份，
        让老配置也能在设置页正常显示/编辑，不会白屏。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        raw = self._cfg.raw
        mode = raw.get("companion_mode") or "custom"
        features = raw.get("companion_features")
        if not isinstance(features, list):
            features = self._derive_features_from_config(raw)
        # 【2026-10-08】额外给出**三档真实档位**（mode3）：由 companion_features 推导
        # （mode_gates 单一真相源），而不是只看 companion_mode 字符串。
        # 为什么：实测 config 里 companion_mode **缺失**（历史写入路径没落这个键），
        # 于是 mode 恒为 'custom' ⇒ 设置页无法据此恢复档位（用户观感="设的混合重启就没了"）。
        # 用 features 推导后，档位恢复不再依赖那个可能缺失的字符串。
        try:
            from . import mode_gates as _mg
            mode3 = _mg.current_mode(raw)
        except Exception:  # noqa: BLE001
            mode3 = "local"
        return {
            "success": True,
            "mode": mode,
            "mode3": mode3,
            "features": features,
            "catalogue": self.COMPANION_FEATURE_DEFS,
            "budget_gb": 16,   # 设置页提供下拉，用户按自己显卡选
        }

    def save_companion_features(self, payload: dict = None) -> dict:
        """设置页「功能与性能」面板保存：写入归一功能集并反推引擎配置（热切换生效）。

        与引导页共用同一 companion_features 真相源，所以「设置里改」=「改了引导页的选择」，
        二者完全一致、互相同步。本地功能会触发后台模型准备（自动下载，不阻塞）。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        payload = payload or {}
        features = payload.get("features")
        if not isinstance(features, list):
            return {"success": False, "error": "features 必须是列表"}
        try:
            raw = self._cfg.raw
            norm = []
            for f in features:
                if isinstance(f, dict) and f.get("id") in self.COMPANION_FEATURE_DEFS:
                    fid = f["id"]
                    def_ = self.COMPANION_FEATURE_DEFS[fid]
                    norm.append({
                        "id": fid,
                        "label": def_["label"],
                        "vram": f.get("vram", def_["vram"]),
                        "opts": f.get("opts") or {},
                    })
            raw["companion_features"] = norm
            # 同步陪伴模式标签：功能集恰好命中某预设 → 对应档位，否则 custom。
            # 让设置页「档位」chip 始终反映真实功能集（与引导页同一真相源、互相反馈）。
            raw["companion_mode"] = self._mode_token_from_features(norm)
            ids = self._features_to_engine_config(norm, raw)
            self._save_config()
            from ..config import _to_ns
            self._cfg.ns = _to_ns(raw)
            logger.info("功能集已保存：%s", [f["id"] for f in norm])
            # 后台准备所选本地模型（自动下载，不阻塞）。
            # 修复：local_vision（本地快照看屏）与 local_duplex 共用同一 GGUF，
            # 之前只触发 local_duplex 的下载 → 仅勾 local_vision 时漏下。
            if ids & {"local_duplex", "local_vision", "local_qwen_tts"}:
                try:
                    self._kickoff_custom_model_prep(norm, raw.get("proactive", {}))
                except Exception:  # noqa: BLE001
                    logger.exception("功能集保存后模型准备触发失败（忽略）")
            # 热切换双工引擎（无需重启即反映到运行态）
            pro = getattr(self, "_proactive", None)
            if pro is not None:
                # 改造点 D3：先把档位能力注入 proactive，作为越权拦截唯一真相源
                try:
                    pro.set_tier(raw.get("companion_mode") or '', [f["id"] for f in norm])
                except Exception:  # noqa: BLE001
                    logger.debug("功能集保存后档位能力注入失败（忽略）")
                try:
                    mode = raw.get("proactive", {}).get("duplex_mode")
                    if isinstance(mode, str):
                        mode = mode.strip().lower()
                    if mode in (True, False, "local_omni", "realtime", "local", "cloud", "off", "auto"):
                        pro.set_duplex_mode(mode)
                except Exception:  # noqa: BLE001
                    logger.exception("功能集保存后双工热切换失败（重启后生效）")
            return {"success": True, "features": norm}
        except Exception as e:  # noqa: BLE001
            logger.exception("保存功能集失败")
            return {"success": False, "error": f"保存失败: {e}"}

    def _mode_token_from_features(self, features) -> str:
        """功能集 → 档位 token：恰好命中某预设返回对应档位，否则 'custom'。

        注意 PRESET_FEATURES 的 value 是 [{id:...}]（dict 列表），不是纯字符串列表，
        所以比较前必须提取 .id 再排序，避免 sorted() 对 dict 做 < 比较（Python 3 TypeError）。
        """
        ids = sorted(f.get("id") for f in (features or []) if isinstance(f, dict))
        for token, preset in (getattr(self, "PRESET_FEATURES", {}) or {}).items():
            if token == "custom" or not isinstance(preset, list):
                continue
            preset_ids = sorted(p.get("id") for p in preset if isinstance(p, dict))
            if preset_ids == ids:
                return token
        return "custom"

    def get_gpu_vram(self) -> dict:
        """探测独显显存（GB）与型号，供引导页按真实显卡推荐档位 / 启用禁用卡片。

        仅支持 NVIDIA（nvidia-smi）。无独显 / 探测失败返回 success=False，前端降级按 16GB 推荐。
        """
        import shutil
        import subprocess
        import re
        nv = shutil.which("nvidia-smi")
        if not nv:
            # Windows 常见路径兜底
            for cand in (r"C:\Windows\System32\nvidia-smi.exe",
                         r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"):
                if os.path.isfile(cand):
                    nv = cand
                    break
        if not nv:
            return {"success": False, "reason": "nvidia-smi 不可用（非 NVIDIA 或无驱动）"}
        try:
            out = subprocess.run(
                [nv, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, errors='replace', timeout=10,
            )
            if out.returncode != 0:
                return {"success": False, "reason": out.stderr.strip()[:120] or "nvidia-smi 返回非零"}
            line = out.stdout.strip().splitlines()[0]
            parts = [p.strip() for p in line.split(",")]
            name = parts[0] if parts else ""
            mem = 0
            if len(parts) > 1:
                try:
                    mem = int(re.sub(r"\D", "", parts[1]))
                except Exception:  # noqa: BLE001
                    mem = 0
            return {"success": True, "vram_gb": mem, "name": name}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "reason": str(e)[:120]}

    def get_vram_live(self) -> dict:
        """实时显存快照（供开发者面板「显存占比」卡片 1s 轮询）。

        返回四层信息，便于一眼看出"显存被谁吃了、还剩多少、够不够"：
          - gpu        : 整卡 used/total/free/mem_pct/util_pct（nvidia-smi 直读）
          - model      : 本地双工模型进程树占用 MB + PID 列表
          - others     : 整卡 − 模型 = 其它程序占用 MB（含壁纸/浏览器/桌面合成）
          - budget     : 目标上限（默认 12GB）与健康判定
          - profile    : 当前显存档位与上次应用结果

        全部只读、异常降级（探测不到就 success=False，绝不抛给前端）。
        """
        out: dict[str, Any] = {"success": True, "ts": time.time()}

        # --- 1) 整卡显存 ---
        try:
            from ..perception.gpu_load import gpu_load
            g = gpu_load()
        except Exception as e:  # noqa: BLE001
            logger.debug("gpu_load 不可用: %s", e)
            g = None
        if not g:
            out["success"] = False
            out["reason"] = "nvidia-smi 不可用（非 NVIDIA 或无驱动）"
            return out
        used_mb = float(g.get("used_mb") or 0.0)
        total_mb = float(g.get("total_mb") or 0.0)
        out["gpu"] = {
            "used_mb": round(used_mb, 1),
            "total_mb": round(total_mb, 1),
            "free_mb": round(max(0.0, total_mb - used_mb), 1),
            "used_gb": round(used_mb / 1024.0, 2),
            "total_gb": round(total_mb / 1024.0, 2),
            "free_gb": round(max(0.0, total_mb - used_mb) / 1024.0, 2),
            "mem_pct": round(g.get("mem_pct") or 0.0, 1),
            "util_pct": round(g.get("util_pct") or 0.0, 1),
        }

        # --- 2) 模型进程树占用 ---
        # 【2026-09-20 归属判定修复】此前用 compute_apps_vram_list()，它走
        # nvidia-smi --query-compute-apps=pid,used_memory —— 在本机 WDDM 驱动下
        # used_memory 恒为 [N/A]，float("[N/A]") 抛 ValueError 被静默跳过 ⇒
        # model_mb 恒为 0 ⇒ 整卡占用全被算成"其它程序"（用户报的归类错误根因）。
        # 改用 vram_by_pid_list()：Windows GPU 性能计数器拿 per-PID 显存，
        # nvidia-smi 兜底。拿不到时**如实标注**，绝不再默认归给"其它"。
        model_mb = 0.0
        model_pids: list[int] = []
        model_src = "none"
        model_indeterminate = False
        try:
            from ..perception.gpu_load import vram_by_pid_list, per_pid_vram_mb_map
            pro = getattr(self, "_proactive", None)
            eng = getattr(pro, "_engine", None) if pro is not None else None
            if eng is not None:
                pids = set(eng.model_process_pids())
                if pids:
                    model_pids = sorted(pids)
                    by_pid = vram_by_pid_list(pids)
                    if by_pid:
                        model_mb = sum(by_pid.values())
                        model_src = "per_pid_counter"
                    else:
                        # 进程在跑、但 per-PID 显存取不到 ⇒ 归属**不可判定**，
                        # 不能算 0（那会把模型占用错误地算进"其它程序"）。
                        model_indeterminate = True
                        model_src = "unavailable"
        except Exception as e:  # noqa: BLE001
            logger.debug("模型进程显存统计失败: %s", e)
            model_indeterminate = bool(model_pids)
        out["model"] = {
            "loaded": bool(model_pids),
            "used_mb": round(model_mb, 1),
            "used_gb": round(model_mb / 1024.0, 2),
            "pids": model_pids,
            "source": model_src,
            "indeterminate": model_indeterminate,
        }

        # --- 3) 其它程序 = 整卡 − 模型 ---
        # 模型占用不可判定时，不给"其它程序"一个假数值（那正是原 bug 的表现形态）。
        if model_indeterminate and not model_mb:
            out["others"] = {
                "used_mb": 0.0, "used_gb": 0.0,
                "indeterminate": True,
                "note": "模型进程在运行但无法读取其 per-PID 显存，暂不能拆分归属",
            }
        else:
            others_mb = max(0.0, used_mb - model_mb)
            out["others"] = {
                "used_mb": round(others_mb, 1),
                "used_gb": round(others_mb / 1024.0, 2),
                "indeterminate": False,
            }

        # --- 4) 预算与健康判定 ---
        budget_gb = 12.0
        try:
            pro_raw = getattr(self, "_cfg", None)
            if pro_raw is not None:
                raw = getattr(pro_raw, "raw", {}) or {}
                v = (raw.get("proactive", {}) or {}).get("duplex_vram_budget_gb")
                if v:
                    budget_gb = float(v)
        except Exception:  # noqa: BLE001
            pass
        budget_mb = budget_gb * 1024.0
        head = budget_mb - used_mb
        if head < 0:
            health, advice = "over", "已超出预算：建议关掉壁纸引擎/浏览器等占用大户，或改用 safe_4k 档。"
        elif head < 512:
            health, advice = "tight", "余量不足 0.5GB：对话中若上下文增长可能触发换出，建议降到 safe_4k 档。"
        else:
            health, advice = "ok", "余量充足。"
        out["budget"] = {
            "target_gb": round(budget_gb, 2),
            "headroom_mb": round(head, 1),
            "headroom_gb": round(head / 1024.0, 2),
            "health": health,
            "advice": advice,
        }

        # --- 4.5) 开启本地双工后的投影（修复双重计数）---
        # 当前 used_gb 已含常驻的本地双工模型（若已在跑）。投影"若开启本地双工"
        # 时，已常驻则不再 +DUPLEX_VRAM_GB（否则得出「10.4+8=18.4 超限」的误判）。
        _pro = getattr(self, "_proactive", None)
        _dmode = getattr(_pro, "_duplex_mode", None)
        if _dmode not in ("local_omni", "local"):
            # 运行态可能尚未应用（启动早期）：回退读静态配置的真实意图
            _dmode = ((getattr(self._cfg, "raw", {}) or {}).get("proactive", {}) or {}).get("duplex_mode")
        # 本地双工两种落点都占显存（local_omni=Comni Q4 / local=旧 Ollama MiniCPM），
        # 二者任一为真都应视为「已常驻」，避免「10.4+8=18.4 超限」误判。
        _resident_duplex_gb = round(self.DUPLEX_VRAM_GB, 2) if _dmode in ("local_omni", "local") else 0.0
        _total_gb = round(total_mb / 1024.0, 2)
        _projected_local_duplex_gb = round(
            round(used_mb / 1024.0, 2) + max(0.0, self.DUPLEX_VRAM_GB - _resident_duplex_gb), 2)
        out["projection"] = {
            "resident_duplex_gb": _resident_duplex_gb,
            "local_duplex_gb": round(self.DUPLEX_VRAM_GB, 2),
            "projected_local_duplex_gb": _projected_local_duplex_gb,
            "projected_exceeds_total": bool(_projected_local_duplex_gb > _total_gb),
            "note": ("本地双工已常驻，开启不额外增加显存" if _resident_duplex_gb
                     else "当前未跑本地双工，开启约 +%.1f GB" % self.DUPLEX_VRAM_GB),
        }

        # --- 5) 当前显存档位 ---
        try:
            pro = getattr(self, "_proactive", None)
            eng = getattr(pro, "_engine", None) if pro is not None else None
            if eng is not None and hasattr(eng, "vram_profile_state"):
                out["profile"] = eng.vram_profile_state()
        except Exception as e:  # noqa: BLE001
            logger.debug("读取显存档位失败: %s", e)

        return out

    def get_companion_readiness(self) -> dict:
        """视觉模型就绪检查：视觉开关开、但无配置/无本地模型时，供启动钩子或通话入口弹提醒。

        返回 vision_enabled / need(None=就绪, "config"=未选模型, "model"=模型未下载) / via / ready。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        raw = self._cfg.raw
        feats = raw.get("companion_features")
        if not isinstance(feats, list):
            feats = self._derive_features_from_config(raw)
        fids = {f["id"] for f in feats if isinstance(f, dict)}
        vis_ids = {"local_vision", "local_duplex", "cloud_duplex", "cloud_snapshot"}
        vis_on = bool(fids & vis_ids) or bool((raw.get("vision_models") or {}).get("enabled"))
        if not vis_on:
            return {"success": True, "vision_enabled": False, "need": None, "ready": True, "via": None}
        local_vis = bool(fids & {"local_vision", "local_duplex"})
        if not local_vis:  # 视觉走云端：零本地下载，仅依赖网络 → 视为就绪
            return {"success": True, "vision_enabled": True, "need": None, "ready": True, "via": "cloud"}
        model_dir = self._comni_model_dir()
        if not model_dir:
            return {"success": True, "vision_enabled": True, "need": "config", "ready": False, "via": "local", "model_dir": ""}
        if _path_has_non_ascii(model_dir):
            # 中文/非 ASCII 路径 C++ 引擎读不了 → 直接判未就绪，避免静默去挂坏路径
            return {"success": True, "vision_enabled": True, "need": "config", "ready": False,
                    "via": "local", "model_dir": model_dir,
                    "reason": "模型目录含中文/非 ASCII 字符，无法加载，请改用纯英文路径"}
        try:
            from src.companion.model_catalog import get_model_catalog as _cat
            cat = _cat(model_dir)
            quant = cat.get("recommended") or "Q4_K_M"
        except Exception:  # noqa: BLE001
            quant = "Q4_K_M"
        if not self._comni_quant_present(model_dir, quant):
            return {"success": True, "vision_enabled": True, "need": "model", "ready": False,
                    "via": "local", "model_dir": model_dir, "quant": quant}
        return {"success": True, "vision_enabled": True, "need": None, "ready": True,
                "via": "local", "model_dir": model_dir, "quant": quant}

    def _comni_quant_present(self, model_dir: str, quant: str = "Q4_K_M") -> bool:
        """模型目录里是否已存在指定量化档的 GGUF（判断"模型已下载"）。"""
        try:
            import os
            from src.companion.model_catalog import QUANTS
            entry = next((q for q in QUANTS if q.get("id") == quant), None)
            fname = (entry or {}).get("file") or ("MiniCPM-o-4_5-%s.gguf" % quant)
            p = os.path.join(model_dir, fname)
            if not os.path.isfile(p):
                return False
            # 完整性校验：低于目录标注体积的 80% 视为下载中断，避免"半包"被判成已就绪
            exp = (entry or {}).get("size_gb")
            if exp:
                try:
                    got = os.path.getsize(p) / (1024 ** 3)
                    if got < float(exp) * 0.8:
                        return False
                except Exception:
                    pass
            return True
        except Exception:  # noqa: BLE001
            return False

    def notify_companion_readiness(self) -> dict:
        """视觉就绪检查 + 进软件时右上角弹窗（无配置/无模型时引导）。由 loaded/shown 钩子调用。"""
        try:
            from . import native_notify as _nn
            r = self.get_companion_readiness()
            if not r.get("success"):
                return r
            need = r.get("need")
            if need == "config":
                _nn.show("视觉模型还没选",
                         "视觉感知已开，但还没选本地/云端模型。去「设置 · 功能与性能」勾选"
                         "本地双工或本地快照（看屏），或选云端双工免下载，即可开始看屏。",
                         "focus", 9000, key="companion_readiness")
            elif need == "model":
                _nn.show("视觉模型正在准备",
                         "视觉感知已开，本地模型首次需下载（约几 GB）。后台下载完自动看屏，"
                         "期间可先聊天；想立刻看屏可改选云端双工免下载。",
                         "back", 9000, key="companion_readiness")
            return {"success": True, "need": need, "ready": r.get("ready")}
        except Exception as e:  # noqa: BLE001
            logger.exception("视觉就绪弹窗失败（忽略）")
            return {"success": False, "error": str(e)}

    def _kickoff_custom_model_prep(self, features, pro):
        """自定义配置 / 设置页「功能与性能」保存 / 视觉开关开启后，后台自动准备所选的本地模型。

        容错：任何一步失败只记日志，不影响引导或开关切换。前端引导页与设置页都告知用户
        "所选模型会自动准备"，这里就是"软件内引导下载"的落地：勾选即开始后台下载。

        关键修复（2026-09-07）：
        1) 本地双工和本地快照（视觉）共用同一个 MiniCPM-o-4.5 GGUF 量化包；之前只触发
           local_duplex 的下载，**漏了 local_vision**——导致"开了视觉开关却没加载模型"。
           现 local_duplex / local_vision 任一启用都触发同一份 GGUF 下载。
        2) 量化档默认 Q4_K_M（最轻量流畅档），解析顺序：功能集 opts.quant → proactive 配置
           → catalog 推荐 → 兜底 Q4_K_M；量化包已存在则跳过、不重复下。
        """
        try:
            feats = [f for f in (features or []) if isinstance(f, dict)]
            ids = {f["id"] for f in feats}
            # 本地 MiniCPM-o（本地双工 / 本地快照看屏）共用同一 GGUF
            if ids & {"local_duplex", "local_vision"}:
                try:
                    fid = next((f for f in feats
                                if f.get("id") in ("local_duplex", "local_vision")), None)
                    quant = ((fid.get("opts") or {}) if fid else {}).get("quant")
                    if not quant:
                        quant = (pro or {}).get("duplex_local_omni_quant")
                    if not quant:
                        try:
                            from src.companion.model_catalog import get_model_catalog as _cat
                            cat = _cat(self._comni_model_dir())
                            quant = cat.get("recommended") or "Q4_K_M"
                        except Exception:  # noqa: BLE001
                            quant = None
                    if not quant:
                        quant = "Q4_K_M"  # 兜底统一 Q4
                    # 不再启动期自动下载：下载与进度弹窗一律等用户主动在引导页/设置页点
                    # 「开始下载模型」才触发（避免一开软件就拉起 4.8GB 下载 + 弹窗）。
                    # 目标目录以用户在引导页「浏览」选定的为准（已写 config.json model_dir）。
                    _md = self._comni_model_dir()
                    logger.info(
                        "本地视觉/双工量化包准备：已跳过启动期自动下载（quant=%s，目标目录=%s），"
                        "等待用户主动点击下载。ids=%s", quant, _md, sorted(ids),
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("自动下载本地视觉/双工量化包失败")
            if "local_qwen_tts" in ids:
                # 同样不在启动期自动下载，等用户在语音设置页主动点「下载」。
                logger.info("千问语音模型准备：已跳过启动期自动下载，等待用户主动点击下载。")
        except Exception:  # noqa: BLE001
            logger.exception("自定义模型准备触发失败（忽略）")

    def _gate_ctx(self) -> dict:
        """门槛判定的运行期上下文（云端 Key 是否齐、可用显存）。

        拿不到可靠显存时返回 None —— mode_gates 会选择不拿显存卡人（宁可放过不误锁）。
        """
        ctx = _mode_gates.build_context(self._cfg.raw if self._cfg else None)
        # duplex_mode 为布尔值是历史遗留形态：True 的语义是「只做看屏快照、不含语音双工」
        # （见 save_onboarding_setup 里只有 local_vision 时写 True），不是"开着双工"。
        # 门槛必须认出它，否则会出现「档位说可用、实际根本没有双工引擎」的假放行。
        try:
            raw = self._cfg.raw if self._cfg else {}
            dm = (raw.get("proactive") or {}).get("duplex_mode")
            if isinstance(dm, bool) or dm in (None, ""):
                ctx["duplex_engine"] = False
            else:
                ctx["duplex_engine"] = str(dm) not in ("off", "false")
        except Exception:  # noqa: BLE001
            pass
        try:
            pro = getattr(self, "_proactive", None)
            live = getattr(pro, "vram_free_gb", None) if pro else None
            if isinstance(live, (int, float)) and live > 0:
                ctx["vram_gb"] = float(live)
        except Exception:  # noqa: BLE001
            pass
        return ctx

    def _current_mode(self) -> str:
        """当前档位（纯本地/云端/混合）—— 真相源是 companion_features 功能清单。"""
        return _mode_gates.current_mode(self._cfg.raw if self._cfg else None)

    def _tier_gate_duplex_mode(self, pro, call_mode) -> tuple:
        """双工引擎门控：引擎弹窗/设置页直切双工前调用。

        2026-10-07 重写：判据从「有没有 local_duplex 能力 + 旧四档词汇」改为查
        mode_gates 门槛矩阵（纯本地/云端/混合三档 + 每档能力清单）。此前用旧词
        （纯聊天/聊天+看得见/全感知伙伴）报给用户，且把云端双工也要求 local_duplex，
        导致「云端档有 Key 却提示要升全感知档」这种自相矛盾的拦截。

        返回 (是否放行, 提示文案)；blocked 时文案必含"为什么 + 怎么解"。
        """
        # 关闭双工恒放行
        if call_mode in (False, "off"):
            return True, ""
        if call_mode not in ("local_omni", "local", "realtime", "cloud", "auto"):
            return True, ""   # 其它值交给 set_duplex_mode 自己校验

        mode = self._current_mode()
        ctx = self._gate_ctx()
        want_cloud = call_mode in ("realtime", "cloud")

        # 云端双工：只需云端 Key，不该被「本地双工能力」卡住
        if want_cloud:
            co = (self._cfg.raw.get("cloud_omni") or {}) if self._cfg else {}
            has_key = bool(str(co.get("api_key") or "").strip()) and bool(str(co.get("model") or "").strip())
            if mode == _mode_gates.MODE_LOCAL:
                return False, (
                    "你当前是「纯本地」档，语音通话走的是本地模型，云端双工不在这一档里。"
                    "若想用云端双工（延迟更低、可克隆音色），请到 设置 → 功能与性能 → 档位 切到「云端」或「混合」。"
                )
            if not has_key:
                return False, (
                    "云端双工需要一个有效的阿里云 Key（还要选好模型）。"
                    "请到 设置 → 模型与 API 绑定一个 Key —— 每个模型有 100 万免费额度。"
                )
            return True, ""

        # 本地/自动双工：查本地双工门槛（矩阵里已含显存判断）
        r = _mode_gates.evaluate(mode, "voice_call", ctx)
        if r["state"] == "ok":
            return True, ""
        return False, f"{r['reason']}{r['action']}"

    def save_duplex_settings(self, data: dict) -> dict:
        """保存双工引擎选择（cloud/local/off）+ 自定义端点，热切换立即生效。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        # 【2026-10-08】是否真的传了 engine。凡是不带 engine 的「部分保存」
        # （只改 prefer_cloud / local_unload_on_game / 云端自有端点 / 游戏锁静默…）
        # 都不得触碰 duplex_mode 与 duplex_endpoint —— 见下方修复说明。
        engine_given = False
        try:
            mode = (data.get("engine") or "cloud").strip().lower()
            if mode not in ("cloud", "local", "off", "auto"):
                return {"success": False, "error": f"未知双工引擎: {mode}"}
            endpoint = str(data.get("endpoint") or "").strip()
            engine_given = "engine" in data and bool(str(data.get("engine") or "").strip())
            pro_raw = self._cfg.raw.get("proactive")
            if not isinstance(pro_raw, dict):
                pro_raw = {}
                self._cfg.raw["proactive"] = pro_raw
            # cloud=realtime（旧值兼容）；off=false；local=local_omni（本地真全双工 Comni）
            # ⚠️ 不能再写旧的 "local"：那是 Ollama polling 老路径，客户端没有 run()、
            # 2026-08-30 实锤会崩溃循环、双工永远起不来。UI 上的「本地」= local_omni。
            # auto=系统按场景选（空闲本地 omni、游戏切云端），proactive 落点为 local_omni
            # 并用 _duplex_auto 标志让所有读取显示「自动」。
            # 【2026-10-08 修复·「部分保存」会误改引擎】旧实现无条件写 duplex_mode /
            # duplex_endpoint，于是只发 {prefer_cloud} / {mode_lock_silent} / 云端自有
            # 端点 这类**不含 engine** 的保存，也会把引擎强制改成 realtime、清空
            # duplex_endpoint，并连带触发「本地双工 → 云端」的卸载模型动作。
            # 实测 `saveCloudDuplex()`（保存云端自有端点）就不带 engine ⇒ 点一次
            # 「保存云端双工端点」= 悄悄强切云端 + 卸本地模型。现在只有真传了才写。
            if engine_given:
                pro_raw["duplex_mode"] = (
                    "auto" if mode == "auto"
                    else "realtime" if mode == "cloud"
                    # 【2026-10-16】off 统一写成字符串 'off'（可读性）：消费端两处都已兼容
                    # （_get_duplex_settings_impl 与 proactive.capabilities 都判 `is False or in ("off","false")`），
                    # 已用回归验证 'off' 仍映射为 engine=off。旧布尔 False 仅作历史兼容。
                    else ("off" if mode == "off" else "local_omni"))
                # 【2026-10-16 修复·开关不自洽】双工循环的总闸是 proactive.enabled（main.py 据此
                # 启动/不启动 proactive 任务），但本接口此前只写 duplex_mode、从不写 enabled：
                # 旧代码靠引导落盘时 `_features_to_engine_config` 顺带把它置 True 才"看起来能用"。
                # 引导改为"不自动开启重度能力"后，用户明确勾选「语音电话」却不会真的跑起来。
                # 【2026-10-17 解耦】引擎选择只决定**运行时**（off=关，其余=开）；
                # 绝不再顺手改"主动互动"（chat_initiative）—— 那是用户独立的开关。
                pro_raw["runtime_on"] = (mode != "off")
            # 【2026-10-16 修复·「主动搭话」开关语义混绑】
            # 聊天页右栏开关的用户语义是"她会不会在你空闲时主动开口" = proactive.enabled，
            # 与此前只能靠 engine 间接影响的 duplex_mode 是两件事。故支持**显式**传
            # proactive_enabled：只写 enabled 本体、**不碰引擎**（配合上面的 engine_given
            # 逻辑，不带 engine 的部分保存不会改 duplex_mode）。放在其后 → 显式值优先。
            if "proactive_enabled" in data:
                # 【2026-10-17 解耦】UI 那个开关 = **主动互动**（她在聊天页主动找你说话），
                # 写 chat_initiative；旧键 enabled 同步写一份做兼容（老读法语义一致）。
                _ci = bool(data.get("proactive_enabled"))
                pro_raw["chat_initiative"] = _ci
                pro_raw["enabled"] = _ci
            if "endpoint" in data:
                pro_raw["duplex_endpoint"] = endpoint
            # 看屏/语音解耦开关（仅在 UI 传该键时更新，避免其它保存路径清空它）：
            # 开启后选云端语音不会关掉本地看屏，二者可并存。
            if "decouple_screen_voice" in data:
                pro_raw["decouple_screen_voice"] = bool(data["decouple_screen_voice"])
            # 双工互动方法预设（2026-08-31 开关）：仅在 UI 传该键时更新，不传则保留原值，避免其它保存路径清空它。
            style = data.get("interaction_style")
            if style is not None:
                pro_raw["duplex_interaction_style"] = str(style).strip().lower()
            # 视觉切片数（真人陪伴 live 模式）：仅在 UI 传该键时更新。
            slice_nums = data.get("interaction_slice_nums")
            if slice_nums is not None:
                try:
                    pro_raw["duplex_interaction_slice_nums"] = int(slice_nums)
                except Exception:  # noqa: BLE001
                    logger.warning("忽略非法切片数: %r", slice_nums)
            # pubjavis 模式提示词开关（设置页子面板）：仅在 UI 传该键时更新。
            pub_opts = data.get("pubjavis_options")
            if isinstance(pub_opts, dict):
                pro_raw["duplex_pubjavis_options"] = {k: bool(v) for k, v in pub_opts.items()}
            pub_plus_opts = data.get("pubjavis_plus_options")
            if isinstance(pub_plus_opts, dict):
                pro_raw["duplex_pubjavis_plus_options"] = {
                    k: bool(v) for k, v in pub_plus_opts.items()}
            # 游戏锁静默开关（设置页新增）：仅在 UI 传该键时更新。
            mode_lock_silent = data.get("mode_lock_silent")
            if mode_lock_silent is not None:
                pro_raw["duplex_mode_lock_silent"] = bool(mode_lock_silent)
            # 【2026-10-08·高负载自动让路总开关】重负载（游戏/渲染）时是否卸载本地模型让路。
            # 仅在本页传该键时更新；不传则保留原值，避免其它保存路径清空它。
            unload_on_game = data.get("local_unload_on_game")
            if unload_on_game is not None:
                pro_raw["duplex_local_omni_unload_on_game"] = bool(unload_on_game)
            # 【2026-09-24·云端双工用户自有端点】语音/视频分开端点 + 开关，持久化到
            # config.cloud_duplex。仅在 UI 传对应键时更新，避免其它保存路径清空它。
            cd = self._cfg.raw.get("cloud_duplex")
            if not isinstance(cd, dict):
                cd = {}
                self._cfg.raw["cloud_duplex"] = cd
            cd_voice = cd.get("voice") or {}
            cd_video = cd.get("video") or {}
            if isinstance(cd_voice, dict) is False:
                cd_voice = {}
            if isinstance(cd_video, dict) is False:
                cd_video = {}
            if "voice_endpoint" in data:
                cd_voice["endpoint"] = str(data["voice_endpoint"] or "").strip()
            if "voice_key" in data:
                cd_voice["api_key"] = str(data["voice_key"] or "").strip()
            if "video_endpoint" in data:
                cd_video["endpoint"] = str(data["video_endpoint"] or "").strip()
            if "video_key" in data:
                cd_video["api_key"] = str(data["video_key"] or "").strip()
            if "video_enabled" in data:
                cd["video_enabled"] = bool(data["video_enabled"])
            if "prefer_cloud" in data:
                cd["prefer_cloud"] = bool(data["prefer_cloud"])
            if "local_fallback" in data:
                cd["local_fallback"] = bool(data["local_fallback"])
            # 【2026-10-02】视觉双工总闸：写入 cloud_duplex 段（与 prefer_cloud 同段，
            # 因为 proactive._load_cloud_duplex_cfg 从该段读取本键，重启后仍生效）。
            if "duplex_vision_enabled" in data:
                cd["duplex_vision_enabled"] = bool(data["duplex_vision_enabled"])
            cd["voice"] = cd_voice
            cd["video"] = cd_video
            self._save_config()
            from ..config import _to_ns
            self._cfg.ns = _to_ns(self._cfg.raw)
        except Exception as e:
            logger.exception("保存双工引擎设置失败")
            return {"success": False, "error": f"写入配置失败: {e}"}
        # 热切换 proactive 实例（无需重启）
        pro = getattr(self, "_proactive", None)
        if pro is not None:
            # 【2026-09-24】云端双工端点/开关运行时应用（无需重启双工循环）。
            try:
                _apply = pro.apply_cloud_duplex_config(data)
                if not _apply.get("success"):
                    logger.warning("cloud_duplex 运行时应用返回: %s", _apply)
            except Exception:  # noqa: BLE001
                logger.exception("cloud_duplex 运行时应用失败")
            try:
                # pubjavis 提示词开关热切换（先于风格切换应用，使风格 reprepare 用上新指令）
                if pub_opts is not None:
                    try:
                        pro.set_duplex_pubjavis_options(pub_opts)
                    except Exception:  # noqa: BLE001
                        logger.exception("pubjavis 选项热切换失败")
                if pub_plus_opts is not None:
                    try:
                        pro.set_duplex_pubjavis_plus_options(pub_plus_opts)
                    except Exception:  # noqa: BLE001
                        logger.exception("pubjavis+ 选项热切换失败")
                # 游戏锁静默开关热切换（无需重启，下次切换即时生效）
                mls = data.get("mode_lock_silent")
                if mls is not None:
                    try:
                        pro._duplex_mode_lock_silent = bool(mls)
                    except Exception:  # noqa: BLE001
                        logger.exception("游戏锁静默开关热切换失败")
                # 【2026-10-02】视觉双工总闸热切换（下次本地双工客户端重建即时生效）。
                dv = data.get("duplex_vision_enabled")
                if dv is not None:
                    try:
                        pro._duplex_vision_enabled = bool(dv)
                        logger.info("视觉双工总闸热切换为 %s", bool(dv))
                    except Exception:  # noqa: BLE001
                        logger.exception("视觉双工总闸热切换失败")
                # 双工互动方法预设热切换（开关即时生效，无需重启）
                if style is not None:
                    try:
                        pro.set_duplex_interaction_style(style)
                    except Exception:  # noqa: BLE001
                        logger.exception("双工互动方法热切换失败")
                # 视觉切片数热切换（live 模式即时生效，无需重启）
                if slice_nums is not None:
                    try:
                        pro.set_duplex_interaction_slice_nums(slice_nums)
                    except Exception:  # noqa: BLE001
                        logger.exception("视觉切片数热切换失败")
                # 【2026-10-08】高负载自动让路总开关热切换（下一轮锁循环即时生效）
                if unload_on_game is not None:
                    try:
                        pro._duplex_local_omni_unload_on_game = bool(unload_on_game)
                        logger.info("高负载自动让路热切换为 %s", bool(unload_on_game))
                    except Exception:  # noqa: BLE001
                        logger.exception("高负载自动让路热切换失败")
                # 【2026-10-08】未传 engine 的「部分保存」到此为止 —— 不碰引擎，
                # 避免把 duplex_mode 误切成 realtime 并触发本地模型卸载 / 重载。
                if not engine_given:
                    logger.info("双工设置保存：未传 engine，跳过引擎热切换（已应用 prefer_cloud=%r / "
                                "unload_on_game=%r）", data.get("prefer_cloud"), unload_on_game)
                    return {"success": True, "engine": getattr(pro, "_duplex_mode", None)}
                # ⚠️ 引擎热切换必须用与落盘一致的转换值（UI 的 local = local_omni）。
                # 直接传 UI 原始值 'local' 会切到废弃的 Ollama polling 老路径
                # （LocalMiniCPMDuplexClient）→ ollama 未跑时每轮裁决超时、
                # 双工假死、视觉感知不刷新（实锤 2026-08-31 02:11 日志）。
                # 引擎值未变时 set_duplex_mode 内部短路，不会重启引擎。
                # auto 也归一为 local_omni 用于「卸载/重载」判定；但热切换调用传
                # 原始 mode（可为 'auto'），由 set_duplex_mode 内部置 _duplex_auto。
                engine_mode = (
                    "realtime" if mode == "cloud"
                    else (False if mode == "off" else "local_omni"))
                old_mode = pro._duplex_mode          # 切换前的模式（卸载/重载判定用）
                # ⚠️ 必须用换算值 call_mode：UI 的 "local" 必须归一为 "local_omni"，
                # 否则 set_duplex_mode("local") 会切到废弃的 Ollama polling 老路径
                # （LocalMiniCPMDuplexClient）→ 裁决超时、双工假死（实锤 2026-09-02）。
                # "auto"/"cloud"/"off" 仍传原始 mode 让 set_duplex_mode 内部正确归一/_duplex_auto。
                call_mode = "local_omni" if mode == "local" else mode
                # 档位隔离门控：引擎弹窗/设置页直切双工时，本档没有双工能力则拦截
                _gate_ok, _gate_err = self._tier_gate_duplex_mode(pro, call_mode)
                if not _gate_ok:
                    logger.warning("档位隔离：引擎切换被拦截(call_mode=%r) | %s", call_mode, _gate_err)
                    return {"success": False, "error": _gate_err}
                result = pro.set_duplex_mode(call_mode, endpoint)
                if not result.get("success"):
                    return result
                # 【双工占卡→聊天跳过本地端口（2026-09-03 修复打字锁死）】模式切换后
                # 同步聊天路由标志：local_omni/local 时跳过本地 ollama，云端/关闭时恢复。
                try:
                    self._sync_chat_router_duplex()
                except Exception:  # noqa: BLE001
                    logger.debug("切换同步聊天路由 skip_local 失败", exc_info=True)
                # 【显存随切换释放/回收（2026-08-31）】以前手动切走本地双工，
                # Comni 的 15GB 一直占着——卸载逻辑只挂在游戏锁路径（要求
                # reason=="game_lock"），设置页压根走不到。现在按 old→new 判定：
                #   本地 omni → 云端/关闭 = 卸载模型释放显存
                #   云端/关闭 → 本地 omni = 重新加载模型
                action = ""
                if old_mode == "local_omni" and engine_mode != "local_omni":
                    action = "unload"
                elif old_mode != "local_omni" and engine_mode == "local_omni":
                    action = "reload"
                if action:
                    tid = self._switch_task_new()
                    threading.Thread(
                        target=self._run_comni_switch,
                        args=(pro, action, tid), daemon=True,
                        name=f"comni-{action}").start()
                    result["switch_task_id"] = tid
                    result["switch_action"] = action
                return result
            except Exception as e:  # noqa: BLE001
                logger.exception("双工引擎热切换失败")
                return {"success": False, "error": f"热切换失败: {e}"}
        return {"success": True, "engine": mode}

    # ======== 切换任务进度（后台卸载/加载本地模型，供 UI 进度条轮询） ========
    _switch_tasks: dict = {}
    _switch_lock = threading.Lock()

    def _switch_task_new(self) -> str:
        tid = f"sw{int(time.time() * 1000)}"
        with self._switch_lock:
            # 顺手清掉 5 分钟以上的旧任务，避免长期运行累积
            for k in [k for k, v in self._switch_tasks.items()
                      if time.time() - v.get("_t", 0) > 300]:
                self._switch_tasks.pop(k, None)
            self._switch_tasks[tid] = {
                "state": "running", "pct": 0, "message": "准备中…",
                "done": False, "_t": time.time(),
            }
        return tid

    def get_switch_progress(self, task_id: str) -> dict:
        """查询切换任务进度（设置页进度条轮询，约 800ms 一次）。"""
        with self._switch_lock:
            t = dict(self._switch_tasks.get(task_id) or {})
        if not t:
            return {"success": False, "error": "任务不存在或已过期"}
        t.pop("_t", None)
        return {"success": True, **t}

    @staticmethod
    def _kill_port_owner(port: int) -> bool:
        """按端口强杀占用进程（停本地 TTS 服务用）。返回是否杀到。"""
        import subprocess as _sp
        try:
            out = _sp.run(["netstat", "-ano", "-p", "TCP"], capture_output=True,
                          text=True, errors="replace", timeout=5,
                          creationflags=0x08000000).stdout
        except Exception:  # noqa: BLE001
            return False
        killed = False
        for line in (out or "").splitlines():
            if f":{port} " not in line or "LISTENING" not in line:
                continue
            parts = line.split()
            if parts and parts[-1].isdigit():
                try:
                    _sp.run(["taskkill", "/T", "/F", "/PID", parts[-1]],
                            capture_output=True, timeout=8, creationflags=0x08000000)
                    killed = True
                except Exception:  # noqa: BLE001
                    pass
        return killed

    def _run_comni_switch(self, pro: Any, action: str, tid: str) -> None:
        """后台线程：卸载或重载本地 Comni 模型，实时更新任务进度。

        reload 会阻塞（引擎等 /health，15GB 模型首次 1~3 分钟），必须放线程里，
        否则整个 UI 回调被拖死。
        """
        def upd(pct: int, msg: str) -> None:
            with self._switch_lock:
                if tid in self._switch_tasks:
                    self._switch_tasks[tid].update({"pct": pct, "message": msg})

        state, final = "done", ""
        try:
            if action == "unload":
                upd(15, "正在停止本地引擎…")
                pro._stop_comni_engine()
                # 等进程真正退出、显存归还（驱动释放有延迟，轮询确认）
                for i in range(24):
                    if not pro._comni_pids():
                        break
                    upd(min(90, 20 + i * 3), "等待显存释放…")
                    time.sleep(0.5)
                upd(100, "本地模型已卸载，显存已释放")
                final = "本地模型已卸载，显存已释放"
            else:
                upd(10, "正在拉起本地引擎…")
                pro._start_comni_engine()      # 内部阻塞等 /health
                eng = getattr(pro, "_engine", None)
                ok = bool(eng is not None and eng.is_model_loaded()) \
                    if eng is not None else bool(pro._comni_pids())
                if ok:
                    upd(100, "本地模型已就绪")
                    final = "本地模型已重新加载"
                else:
                    state = "failed"
                    upd(100, "引擎启动超时，请查看 Comni 日志")
                    final = "引擎启动超时"
        except Exception as e:  # noqa: BLE001
            logger.exception("本地模型%s失败", "卸载" if action == "unload" else "加载")
            state, final = "failed", f"{'卸载' if action == 'unload' else '加载'}失败: {e}"
            upd(100, final)
        with self._switch_lock:
            if tid in self._switch_tasks:
                self._switch_tasks[tid].update(
                    {"state": state, "done": True, "pct": 100, "message": final})

    def _run_clone_switch(self, old_engine: str, new_engine: str, tid: str) -> None:
        """后台线程：切换克隆引擎后停掉旧引擎的常驻服务，释放其显存。

        三个引擎各自独立端口（voxcpm 9881 / qwen3tts 9882 / cosyvoice 9883），
        切换只换 Python 层 backend 对象，旧 server 进程会一直驻留显存。
        """
        def upd(pct: int, msg: str) -> None:
            with self._switch_lock:
                if tid in self._switch_tasks:
                    self._switch_tasks[tid].update({"pct": pct, "message": msg})

        port = self._LOCAL_CLONE_PORT.get(old_engine)
        name = self._LOCAL_CLONE_DISPLAY.get(old_engine, old_engine)
        if not port:
            with self._switch_lock:
                if tid in self._switch_tasks:
                    self._switch_tasks[tid].update(
                        {"state": "done", "done": True, "pct": 100,
                         "message": f"无需清理（{name} 无常驻服务）"})
            return
        upd(20, f"正在停止 {name} 服务…")
        killed = self._kill_port_owner(port)
        for i in range(16):                       # 等端口真正释放
            upd(min(90, 30 + i * 4), f"等待 {name} 服务退出…")
            if not self._tcp_probe(f"http://127.0.0.1:{port}", timeout_s=0.8):
                break
            time.sleep(0.5)
        msg = (f"{name} 服务已停止，显存已释放" if killed
               else f"{name} 服务未在运行，无需清理")
        upd(100, msg)
        with self._switch_lock:
            if tid in self._switch_tasks:
                self._switch_tasks[tid].update(
                    {"state": "done", "done": True, "pct": 100, "message": msg})

    def stop_local_tts_engine(self, engine: str = "") -> dict:
        """手动停止本地 TTS 服务进程（释放常驻的显存/内存）。

        用于：试听/复刻本地克隆引擎后，或不再需要本地 TTS 时，一键释放资源
        ——避免本地模型权重常驻内存/显存（曾导致 CPU 100% + 内存 47GB 占满的卡炸）。
        engine 为空时停止当前 voice.clone.local_engine 对应的端口。
        """
        try:
            if not engine:
                engine = ((getattr(self._cfg, "raw", {}) or {}).get("voice", {})
                          .get("clone", {}).get("local_engine", "") or "qwen3tts")
            port = self._LOCAL_CLONE_PORT.get(engine)
            if not port:
                return {"success": False, "error": f"未知本地引擎 {engine}"}
            killed = self._kill_port_owner(port)
            return {"success": True, "killed": killed,
                    "message": (f"{self._LOCAL_CLONE_DISPLAY.get(engine, engine)} 服务已停止，资源已释放"
                                if killed else f"{engine} 服务未在运行")}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ======== 切换前连通性预检（2026-08-31） ========
    def check_duplex_target(self, data: dict) -> dict:
        """切换双工引擎前，先探测目标模式此刻是否真能连通。

        背景：MiniCPM 免费网关会整体堵死（实测 14 worker 中 11 offline、3 个被
        10 小时前的僵尸 omni_duplex 会话占死 → idle=0 → 请求永远排不到）。此时
        用户若在设置页切到「云端双工/云端快照」，保存会成功但 AI 立刻哑火，界面
        上却毫无提示。这里把「能不能连上」前置到切换之前，让 UI 弹窗拦一道。

        data: {"mode": "local"|"cloud"|"off"}
        返回 {success, available, mode, reason, detail, hint, need_pull}
          available=False → UI 弹窗提醒（但仍允许用户强行切换）
          need_pull=True  → 目标可用，只是要先拉起本地模型（耗时较长）
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        mode = str((data or {}).get("mode") or "cloud").strip().lower()
        if mode == "auto":
            return {"success": True, "available": True, "mode": "auto", "reason": "",
                    "detail": "自动模式无需预检", "hint": "", "need_pull": False}
        if mode == "off":
            return {"success": True, "available": True, "mode": "off", "reason": "",
                    "detail": "关闭双工，无需联网", "hint": "", "need_pull": False}

        if mode == "local":
            # 本地真全双工（Comni）：引擎进程在不在跑都算可用——没跑时 proactive
            # 会自动拉起（日志「引擎尚未就绪，先拉起模型」），只是慢。
            pro_raw = (self._cfg.raw.get("proactive", {}) or {})
            endpoint = str(pro_raw.get("duplex_local_omni_endpoint")
                           or "https://localhost:8006").strip()
            alive = self._tcp_probe(endpoint, timeout_s=2.0)
            if alive:
                return {"success": True, "available": True, "mode": "local", "reason": "",
                        "detail": f"本地引擎已在运行（{endpoint}）",
                        "hint": "", "need_pull": False}
            return {"success": True, "available": True, "mode": "local", "reason": "",
                    "detail": f"本地引擎未运行（{endpoint}）", "need_pull": True,
                    "hint": "切换后会自动拉起本地模型，首次约需 1~3 分钟"}

        # cloud（云端双工 realtime / 云端快照）：与云端视觉共用同一网关，
        # 直接问网关自己有没有空闲 worker，比试探性发请求靠谱得多。
        pro_raw = (self._cfg.raw.get("proactive", {}) or {})
        endpoint = str(pro_raw.get("duplex_endpoint") or "").strip()
        if not endpoint:
            # 【A8 · 2026-09-29】不再静默回退到 MiniCPM 免费端点（wss://minicpmo45...）。
            # 该免费服务不稳（仅 14 共享端口、多软件抢），且已不再是默认云端来源。
            # 云端双工必须走用户自己绑的 Key：
            #   - 用户已绑 cloud_omni.api_key → 视为已配置（user-keyed cloud），放行切换；
            #   - 既无端点也无 Key → 视为「未配置云端双工」，引导去绑 Key，而非偷偷用免费服务。
            co = (self._cfg.raw.get("cloud_omni") or {}) if self._cfg else {}
            if str((co.get("api_key") or "") or "").strip():
                return {"success": True, "available": True, "mode": "cloud",
                        "reason": "keyed_cloud",
                        "detail": "已绑定云端双工 Key（你自己的云端服务），端点由 cloud_omni 配置解析",
                        "hint": "", "need_pull": False}
            return {"success": True, "available": False, "mode": "cloud",
                    "reason": "no_cloud_duplex",
                    "detail": "未配置云端双工：请先在设置绑定你的云端 Key（阿里云百炼等）",
                    "hint": "本软件不提供免费云端服务，云端双工需你自己的 Key；无 Key 时不可用",
                    "need_pull": False}
        st = self._gateway_status(endpoint)
        if st is None:
            return {"success": True, "available": False, "mode": "cloud",
                    "reason": "unreachable", "detail": "云端网关无法访问",
                    "hint": "可能是网络不通或网关已下线；切换后 AI 将无法回应"}
        idle = int(st.get("idle_workers") or 0)
        busy = int(st.get("busy_workers") or 0)
        offline = int(st.get("offline_workers") or 0)
        tasks = st.get("running_tasks") or []
        if idle > 0:
            return {"success": True, "available": True, "mode": "cloud", "reason": "",
                    "detail": f"云端网关正常（空闲 {idle} / 忙 {busy}）",
                    "hint": "", "need_pull": False}
        stale = bool(tasks) and all(float(t.get("elapsed_s") or 0) > 300 for t in tasks)
        if stale:
            return {"success": True, "available": False, "mode": "cloud",
                    "reason": "congested",
                    "detail": f"云端网关拥堵：无空闲 worker（忙 {busy} / 离线 {offline}），"
                              f"{len(tasks)} 个会话均已运行数小时",
                    "hint": "服务端会话疑似卡死，短时间内不会自动恢复；"
                            "切过去 AI 将无法回应，建议保持本地双工"}
        return {"success": True, "available": False, "mode": "cloud", "reason": "busy",
                "detail": f"云端网关繁忙：无空闲 worker（忙 {busy} / 离线 {offline}）",
                "hint": "切过去会长时间排队，建议稍后再试或保持本地双工"}

    def check_clone_engine_target(self, data: dict) -> dict:
        """切换本地克隆引擎前，先探测该引擎的模型是否已下载就绪。

        背景：切换引擎会立即重切角色、拉起对应 TTS 服务；模型没下载时切换会
        静默失败或长时间卡在加载。这里前置检测，让 UI 提示「先下载模型」。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        engine = str((data or {}).get("engine") or "qwen3tts").strip().lower()
        try:
            spec = self._clone_engine_spec(engine)
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"未知克隆引擎: {engine}"}
        try:
            _cache_root, model_dir = self._clone_model_dir(engine)
            ready = model_dir is not None
        except Exception:  # noqa: BLE001
            model_dir, ready = None, False
        if ready:
            return {"success": True, "available": True, "engine": engine,
                    "engine_label": spec.get("label", engine), "reason": "",
                    "detail": f"模型已就绪（{os.path.basename(str(model_dir))}）", "hint": ""}
        return {"success": True, "available": False, "engine": engine,
                "engine_label": spec.get("label", engine), "reason": "model_missing",
                "detail": f"尚未下载模型（{spec.get('repo', engine)}）",
                "hint": f"请先点「下载模型」，预计占用数 GB；或手动执行 "
                        f"modelscope download --model {spec.get('repo', engine)}"}

    # ---- 本地 TTS 显存评估（引导页 / 本地模型选卡用） ----
    # 估算口径：权重按 bf16/fp16 常驻 + 声码器/激活开销，取保守值（宁高勿低，避免爆显存）。
    # 千问3-TTS 默认走 CPU（server 注释：不争双工显存），故 gpu_gb 记 0；
    # 只有设置 QWEN3_TTS_FORCE_CUDA=1 才上卡，此时计入 gpu_gb_if_cuda。
    LOCAL_TTS_VRAM = {
        "qwen3tts":  {"gpu_gb": 0.0, "gpu_gb_if_cuda": 2.0, "disk_gb": 2.4,
                      "device": "默认 CPU（不占显存）"},
        "cosyvoice": {"gpu_gb": 4.0, "gpu_gb_if_cuda": 4.0, "disk_gb": 5.4,
                      "device": "GPU 常驻"},
        "voxcpm":    {"gpu_gb": 6.0, "gpu_gb_if_cuda": 6.0, "disk_gb": 4.7,
                      "device": "GPU 常驻（server 固定 cuda）"},
    }
    DUPLEX_VRAM_GB = 8.3   # 本地全双工 MiniCPM-o 4.5（Q4 量化）当前优化后的占用

    def get_voice_vram_advice(self) -> dict:
        """本地语音模型的显存适配评估：拿真实显卡剩余显存，算出每个引擎能不能跑。

        返回：
          gpu      : {name, total_gb, used_gb, free_gb}（nvidia-smi 实读；失败 success=False）
          duplex_gb: 本地全双工占用（与 TTS 共存时要一起算）
          engines  : 每个引擎 {id, label, gpu_gb, disk_gb, device, need_gb, verdict, note}
            verdict: ok（够）/ tight（勉强）/ fail（不够）
        """
        out = {"success": True, "duplex_gb": self.DUPLEX_VRAM_GB, "engines": [], "gpu": None}
        total_gb, used_gb, name = None, None, ""
        try:
            g = self.get_gpu_vram()
            if g and g.get("success"):
                # 注意：get_gpu_vram 走的是 nvidia-smi --query-gpu=memory.total（nounits），
                # 返回的是 MiB 数值而非 GB。这里统一换算，否则 16GB 卡会被算成 16303GB。
                # 2026-10-12：再按标称容量归一（16303MiB=15.92→16.0），与模型目录/下载器同口径，
                # 避免「检测 15.9GB」与用户认知的 16GB 卡对不上。
                _v = float(g.get("vram_gb") or 0)
                total_gb = round(_v / 1024.0, 2) if _v > 100 else _v
                try:
                    from ..companion.model_catalog import normalize_vram_gb
                    total_gb = normalize_vram_gb(total_gb)
                except Exception:  # noqa: BLE001
                    pass
                name = g.get("name") or ""
        except Exception:  # noqa: BLE001
            g = None
        try:
            from ..perception.gpu_load import gpu_load
            gl = gpu_load() or {}
            used_gb = round(float(gl.get("used_mb") or 0) / 1024.0, 2)
            if not total_gb:
                total_gb = round(float(gl.get("total_mb") or 0) / 1024.0, 2)
                try:
                    from ..companion.model_catalog import normalize_vram_gb
                    total_gb = normalize_vram_gb(total_gb)
                except Exception:  # noqa: BLE001
                    pass
            name = name or (gl.get("name") or "")
        except Exception:  # noqa: BLE001
            used_gb = used_gb or 0.0
        if not total_gb:
            out["success"] = False
            out["reason"] = "未能探测到 NVIDIA 显卡显存（无独显或驱动未装）"
            return out
        used_gb = used_gb or 0.0
        free_gb = round(max(0.0, total_gb - used_gb), 2)
        out["gpu"] = {"name": name, "total_gb": round(total_gb, 2),
                     "used_gb": used_gb, "free_gb": free_gb}
        # 已常驻的本地双工模型不应在估算里重复计入：当前 used_gb 已含它，
        # 若再 +DUPLEX_VRAM_GB 会得到「已用10.4 + 双工8 = 18.4 超限」的误判
        # （其实双工已加载，开不开都占那 8G）。resident 时本地链路只需再塞 TTS。
        _pro = getattr(self, "_proactive", None)
        _dmode = getattr(_pro, "_duplex_mode", None)
        if _dmode not in ("local_omni", "local"):
            # 运行态可能尚未应用（启动早期）：回退读静态配置的真实意图
            _dmode = ((getattr(self._cfg, "raw", {}) or {}).get("proactive", {}) or {}).get("duplex_mode")
        # 本地双工两种落点（local_omni / local）都占显存，任一为真即视为已常驻，避免双重计数。
        resident_duplex_gb = round(self.DUPLEX_VRAM_GB, 2) if _dmode in ("local_omni", "local") else 0.0
        out["resident_duplex_gb"] = resident_duplex_gb
        for eng, info in self.LOCAL_TTS_VRAM.items():
            gpu_need = float(info["gpu_gb"])
            # 本地链路 = 双工 + TTS 同时常驻；云端链路只把本地 TTS 当兜底（单独算）。
            # 双工已常驻 ⇒ 其显存已算在当前占用里，need_local 只补 TTS 部分，避免双重计数。
            need_local = round(max(0.0, self.DUPLEX_VRAM_GB - resident_duplex_gb) + gpu_need, 2)
            need_standalone = round(gpu_need, 2)
            def _verdict(need):
                if need <= free_gb:
                    return "ok"
                if need <= free_gb + 1.5:
                    return "tight"
                return "fail"
            out["engines"].append({
                "id": eng, "label": eng,
                "gpu_gb": gpu_need,
                "gpu_gb_if_cuda": float(info["gpu_gb_if_cuda"]),
                "disk_gb": float(info["disk_gb"]),
                "device": info["device"],
                "need_gb": need_local,
                "need_standalone_gb": need_standalone,
                "verdict": _verdict(need_local),
                "verdict_standalone": _verdict(need_standalone),
            })
        # 一句话结论：本地链路推荐哪个（优先能跑且质量最好；都不行就建议云端）
        rank = ["voxcpm", "cosyvoice", "qwen3tts"]
        pick = next((e for e in rank
                     if next((x for x in out["engines"] if x["id"] == e), {}).get("verdict") == "ok"),
                    None)
        if pick is None:
            pick = next((e for e in rank
                         if next((x for x in out["engines"] if x["id"] == e), {}).get("verdict") == "tight"),
                        None)
        out["recommend"] = pick
        out["local_feasible"] = pick is not None
        return out

    def list_clone_engines(self) -> dict:
        """列出全部本地克隆引擎（引导页「本地语音克隆模型」下载器 / 设置页复用）。

        纯只读聚合，**不改任何既有接口的形状**：把 _clone_engine_spec（5 引擎规格：
        3 本机下载 + MiMo 云端API + GPT-SoVITS 外部服务）与 get_voice_vram_advice（显存判定）
        合成为一份 UI 可直接渲染的清单。

        kind: local（本机下载）/ cloud（云端 API，需 Key）/ external（外部服务，需在线）
        返回 {success, engines:[...], gpu, recommend, local_feasible, duplex_gb}
        每引擎：id,label,repo,kind,installed,installed_detail,downloadable,
                disk_gb,gpu_gb,gpu_gb_if_cuda,device,verdict,verdict_standalone,need_gb,
                note,modelscope_url
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            advice = self.get_voice_vram_advice() or {}
        except Exception:  # noqa: BLE001
            advice = {}
        vram_by_id = {e.get("id"): e for e in (advice.get("engines") or [])}
        _NOTES = {
            "cloud": "云端克隆，需在设置里填 MiMo API Key（限时免费），不占本机磁盘/显存",
            "external": "需本机自行部署 GPT-SoVITS 服务（127.0.0.1:9880），且要先用素材训练音色",
        }
        engines = []
        for eng in ("voxcpm", "cosyvoice", "qwen3tts", "mimo_tts", "gpt_sovits"):
            try:
                spec = self._clone_engine_spec(eng)
            except Exception:  # noqa: BLE001
                continue
            if spec.get("cloud"):
                kind = "cloud"
            elif spec.get("external"):
                kind = "external"
            else:
                kind = "local"
            try:
                _cache_root, resolved = self._clone_model_dir(eng)
            except Exception:  # noqa: BLE001
                resolved = None
            installed = bool(resolved)
            if kind == "local":
                detail = ("已安装（%s）" % os.path.basename(str(resolved))) if installed else "未安装"
            elif kind == "cloud":
                detail = "云端口令已配置" if installed else "未配置云端口令"
            else:
                detail = "服务在线（127.0.0.1:9880）" if installed else "服务未启动"
            v = vram_by_id.get(eng) or {}
            engines.append({
                "id": eng,
                "label": spec.get("label", eng),
                "repo": spec.get("repo") or "",
                "kind": kind,
                "installed": installed,
                "installed_detail": detail,
                "downloadable": kind == "local",
                "disk_gb": v.get("disk_gb"),
                "gpu_gb": v.get("gpu_gb"),
                "gpu_gb_if_cuda": v.get("gpu_gb_if_cuda"),
                "device": v.get("device") or "",
                "verdict": v.get("verdict"),
                "verdict_standalone": v.get("verdict_standalone"),
                "need_gb": v.get("need_gb"),
                "note": _NOTES.get(kind, ""),
                "modelscope_url": spec.get("modelscope_url"),
            })
        return {
            "success": True,
            "engines": engines,
            "gpu": advice.get("gpu"),
            "recommend": advice.get("recommend"),
            "local_feasible": advice.get("local_feasible"),
            "duplex_gb": advice.get("duplex_gb"),
        }

    def scan_tts_model_dir(self, data: dict) -> dict:
        """扫描用户指定目录，识别里面有没有可认领的本地 TTS 模型。

        识别依据：目录名关键字 + 权重文件特征（不看扩展名大小，只看是否存在）。
        返回 {success, dir, engines:[{engine,label,dir,files,disk_gb,complete}]}
        """
        d = str((data or {}).get("dir") or "").strip()
        if not d:
            return {"success": False, "error": "未指定目录"}
        if not os.path.isdir(d):
            return {"success": False, "error": "目录不存在：%s" % d}
        KEYS = (
            ("qwen3tts", ("qwen3-tts", "qwen3_tts", "qwen--qwen3-tts")),
            ("cosyvoice", ("cosyvoice", "iic--cosyvoice")),
            ("voxcpm", ("voxcpm",)),
        )
        WEIGHT_EXT = (".safetensors", ".bin", ".pt", ".gguf", ".onnx")
        hits = []
        try:
            # 只扫两层：用户一般直接选到模型根目录或其上一层
            cands = [d]
            try:
                cands += [os.path.join(d, x) for x in os.listdir(d)
                          if os.path.isdir(os.path.join(d, x))][:40]
            except Exception:  # noqa: BLE001
                pass
            for c in cands:
                # 目录名匹配支持「向上回溯」：用户可能一路点进 snapshots/master，
                # 那层既没关键字也没子目录，但它的祖先目录名带关键字（Qwen--Qwen3-TTS…）。
                low = os.path.basename(c).lower()
                _up, _names = c, []
                for _ in range(4):
                    _p = os.path.dirname(_up)
                    if not _p or _p == _up:
                        break
                    _up = _p
                    _names.append(os.path.basename(_up).lower())
                hay = " ".join([low] + _names)
                for eng, keys in KEYS:
                    if not any(k in hay for k in keys):
                        continue
                    files, size = [], 0
                    for dp, dn, fn in os.walk(c):
                        for f in fn:
                            if f.lower().endswith(WEIGHT_EXT) or f in ("config.json", "model_index.json"):
                                fp = os.path.join(dp, f)
                                files.append(os.path.relpath(fp, c))
                                try:
                                    size += os.path.getsize(fp)
                                except Exception:  # noqa: BLE001
                                    pass
                        if len(files) > 400:
                            break
                    hits.append({"engine": eng, "label": eng, "dir": c,
                                 "files": len(files), "disk_gb": round(size / (1024 ** 3), 2),
                                 "complete": len(files) > 0})
                    break
        except Exception as e:  # noqa: BLE001
            logger.exception("扫描 TTS 模型目录失败")
            return {"success": False, "error": str(e)[:200]}
        # 去重（同一引擎多个目录时保留文件最多的）
        best = {}
        for h in hits:
            cur = best.get(h["engine"])
            if not cur or h["files"] > cur["files"]:
                best[h["engine"]] = h
        return {"success": True, "dir": d, "engines": list(best.values())}

    def set_tts_model_dir(self, data: dict) -> dict:
        """认领用户指定的模型目录：写进 voice.tts_model_dirs.<engine> 并切到该引擎。

        落盘后 check_clone_engine_target / _clone_model_dir 就会按新目录判定「已下载」，
        无需再重复下载数 GB。之后由 set_clone_engine 负责拉起对应 server。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        engine = str((data or {}).get("engine") or "").strip().lower()
        d = str((data or {}).get("dir") or "").strip()
        if engine not in ("qwen3tts", "voxcpm", "cosyvoice"):
            return {"success": False, "error": f"未知本地 TTS 引擎: {engine}"}
        if not d or not os.path.isdir(d):
            return {"success": False, "error": "目录不存在：%s" % (d or "(空)")}
        try:
            raw = self._cfg.raw
            raw.setdefault("voice", {})
            dirs = raw["voice"].setdefault("tts_model_dirs", {})
            dirs[engine] = d
            self._save_config()
            from ..config import _to_ns
            self._cfg.ns = _to_ns(raw)
            _cache, model_dir = self._clone_model_dir(engine)
            return {"success": True, "engine": engine, "dir": d,
                    "model_dir": model_dir, "ready": model_dir is not None,
                    "message": ("已认领并定位到模型：%s" % os.path.basename(str(model_dir))
                                 if model_dir else
                                 "目录已保存，但按标准布局没定位到权重文件——请选到包含模型权重的那一层")}
        except Exception as e:  # noqa: BLE001
            logger.exception("认领 TTS 模型目录失败")
            return {"success": False, "error": f"写入配置失败: {e}"}

    # ---- 用户知识库（游戏/软件）增删改查 ----
    def list_knowledge(self) -> dict:
        """列出全部游戏/软件知识库（config 默认 + 用户 user_knowledge.yaml 合并）。
        每条带 is_user / is_builtin 标记，便于 UI 区分可删范围。"""
        if not self._cfg:
            logger.warning("[list_knowledge] _cfg is None/ falsy, type=%s", type(self._cfg))
            return {"success": False, "error": "配置未加载"}
        try:
            from ..companion.game_knowledge import load_user_knowledge
            user = load_user_knowledge()
            # 用户覆盖表（按 id 索引）：用于把用户编辑/新增回显到列表（覆盖同名内置条目）
            user_app_by_id = {
                str(a.get("id", "")).strip(): a
                for a in (user.get("app_knowledge") or []) if str(a.get("id", "")).strip()
            }
            user_game_by_id = {
                str(g.get("id", "")).strip(): g
                for g in (user.get("games") or []) if str(g.get("id", "")).strip()
            }
            cfg = self._cfg.ns
            apps_cfg = getattr(cfg, "app_knowledge", None) or []
            games_cfg = getattr(cfg, "games", None) or []
            # 兜底：运行时 _cfg 可能因共享配置/重载丢失 app_knowledge 字段（表现为知识库空白）。
            # 若运行时取不到预置数据，但磁盘 config.yaml 确有，则从磁盘强制重载，避免白屏。
            if not apps_cfg and not games_cfg:
                try:
                    disk_cfg = load_config(self._config_path)
                    disk_apps = getattr(disk_cfg.ns, "app_knowledge", None) or []
                    disk_games = getattr(disk_cfg.ns, "games", None) or []
                    if disk_apps or disk_games:
                        logger.warning("[list_knowledge] 运行时 _cfg 无预置知识库，已回退磁盘 config.yaml 重载（apps=%s games=%s）",
                                       len(disk_apps), len(disk_games))
                        apps_cfg = disk_apps
                        games_cfg = disk_games
                except Exception as e:
                    logger.debug("[list_knowledge] 磁盘回退失败: %s", e)
            logger.info("[list_knowledge] apps_cfg len=%s, games_cfg len=%s, cfg type=%s, has app_knowledge attr=%s",
                        len(apps_cfg) if isinstance(apps_cfg, list) else 'N/A',
                        len(games_cfg) if isinstance(games_cfg, list) else 'N/A',
                        type(cfg), hasattr(cfg, 'app_knowledge'))
            # 内置条目 + 用户覆盖：同名 id 用用户字段覆盖默认（is_user=True 标记被用户改过）；
            # 用户新增（非内置 id）追加在后面。修复「编辑内置条目后列表不回显」的老 bug。
            apps = []
            builtin_app_ids = set()
            for a in apps_cfg:
                aid = str(getattr(a, "id", "")).strip()
                if not aid:
                    continue
                builtin_app_ids.add(aid)
                u = user_app_by_id.get(aid)
                if u is not None:
                    apps.append({
                        "id": aid,
                        "name": str(u.get("name") or getattr(a, "name", "")).strip(),
                        "keywords": list(u.get("keywords") or getattr(a, "keywords", []) or []),
                        "knowledge": str(u.get("knowledge") or "").strip(),
                        "screen": str(u.get("screen") or "").strip(),
                        "focus": str(u.get("focus") or "").strip(),
                        "prompt": str(u.get("prompt") or getattr(a, "prompt", "")).strip(),
                        "mode": str(u.get("mode") or "duplex").strip().lower() or "duplex",
                        "ocr_regions": list(u.get("ocr_regions") or []),
                        "exe": list(u.get("exe") or []),
                        "high_performance": bool(u.get("high_performance")),
                        "is_user": True, "is_builtin": True,
                    })
                else:
                    apps.append({
                        "id": aid, "name": str(getattr(a, "name", "")).strip(),
                        "keywords": list(getattr(a, "keywords", []) or []),
                        "knowledge": str(getattr(a, "knowledge", "")).strip(),
                        "screen": str(getattr(a, "screen", "")).strip(),
                        "focus": str(getattr(a, "focus", "")).strip(),
                        "prompt": str(getattr(a, "prompt", "")).strip(),
                        "mode": str(getattr(a, "mode", "") or "duplex").strip().lower() or "duplex",
                        "ocr_regions": list(getattr(a, "ocr_regions", []) or []),
                        "exe": list(getattr(a, "exe", []) or []),
                        "high_performance": str(getattr(a, "high_performance", "")).strip().lower() in ("true", "1", "yes", "y"),
                        "is_user": False, "is_builtin": True,
                    })
            # 用户新增（非内置 id）追加
            for a in (user.get("app_knowledge") or []):
                aid = str(a.get("id", "")).strip()
                if aid and aid not in builtin_app_ids:
                    apps.append({
                        "id": aid, "name": str(a.get("name", "")).strip(),
                        "keywords": list(a.get("keywords") or []),
                        "knowledge": str(a.get("knowledge", "")).strip(),
                        "screen": str(a.get("screen", "") or "").strip(),
                        "focus": str(a.get("focus", "") or "").strip(),
                        "prompt": str(a.get("prompt", "") or "").strip(),
                        "mode": str(a.get("mode", "") or "duplex").strip().lower() or "duplex",
                        "ocr_regions": list(a.get("ocr_regions", []) or []),
                        "exe": list(a.get("exe") or []),
                        "high_performance": bool(a.get("high_performance")),
                        "is_user": True, "is_builtin": False,
                    })
            games = []
            builtin_game_ids = set()
            for g in games_cfg:
                gid = str(getattr(g, "id", "")).strip()
                if not gid:
                    continue
                builtin_game_ids.add(gid)
                u = user_game_by_id.get(gid)
                if u is not None:
                    games.append({
                        "id": gid,
                        "name": str(u.get("name") or getattr(g, "name", "")).strip(),
                        "keywords": list(u.get("keywords") or getattr(g, "keywords", []) or []),
                        "knowledge": str(u.get("knowledge") or "").strip(),
                        "screen": str(u.get("screen") or "").strip(),
                        "focus": str(u.get("focus") or "").strip(),
                        "prompt": str(u.get("prompt") or getattr(g, "prompt", "")).strip(),
                        "mode": str(u.get("mode") or "duplex").strip().lower() or "duplex",
                        "ocr_regions": list(u.get("ocr_regions") or []),
                        "exe": list(u.get("exe") or []),
                        "high_performance": bool(u.get("high_performance")),
                        "is_user": True, "is_builtin": True,
                    })
                else:
                    games.append({
                        "id": gid, "name": str(getattr(g, "name", "")).strip(),
                        "keywords": list(getattr(g, "keywords", []) or []),
                        "knowledge": str(getattr(g, "knowledge", "")).strip(),
                        "screen": str(getattr(g, "screen", "")).strip(),
                        "focus": str(getattr(g, "focus", "")).strip(),
                        "prompt": str(getattr(g, "prompt", "")).strip(),
                        "mode": str(getattr(g, "mode", "") or "duplex").strip().lower() or "duplex",
                        "ocr_regions": list(getattr(g, "ocr_regions", []) or []),
                        "exe": list(getattr(g, "exe", []) or []),
                        "high_performance": str(getattr(g, "high_performance", "")).strip().lower() in ("true", "1", "yes", "y"),
                        "is_user": False, "is_builtin": True,
                    })
            # 用户新增（非内置 id）追加
            for g in (user.get("games") or []):
                gid = str(g.get("id", "")).strip()
                if gid and gid not in builtin_game_ids:
                    games.append({
                        "id": gid, "name": str(g.get("name", "")).strip(),
                        "keywords": list(g.get("keywords") or []),
                        "knowledge": str(g.get("knowledge", "")).strip(),
                        "screen": str(g.get("screen", "") or "").strip(),
                        "focus": str(g.get("focus", "") or "").strip(),
                        "prompt": str(g.get("prompt", "") or "").strip(),
                        "mode": str(g.get("mode", "") or "duplex").strip().lower() or "duplex",
                        "ocr_regions": list(g.get("ocr_regions", []) or []),
                        "exe": list(g.get("exe") or []),
                        "high_performance": bool(g.get("high_performance")),
                        "is_user": True, "is_builtin": False,
                    })
            # 反解三段式供前端「分三路」回填（YAML 仍只存单 knowledge 字段）
            for _it in (apps + games):
                _p = self._parse_kb_three(str(_it.get("knowledge") or ""))
                _it["domain"] = _p["domain"]
                _it["advice"] = _p["advice"]
                _it["style"] = _p["style"]
            # 为每个有 exe 的条目提取图标（取第一个 exe 路径），供前端卡片/编辑回显使用
            try:
                from ..companion.software_profiles import extract_icon_from_exe
                for _it in (apps + games):
                    _exes = _it.get("exe") or []
                    if _exes:
                        _first = _exes[0]
                        # 纯文件名 → 尝试常见安装路径定位（用户可能只存了 douyin.exe 这种短名）
                        if not os.path.isabs(_first):
                            _first = self._resolve_exe_path(_first) or _first
                        try:
                            _it["icon_b64"] = extract_icon_from_exe(_first, size=256) or ""
                        except Exception:
                            _it["icon_b64"] = ""
                    else:
                        _it["icon_b64"] = ""
            except ImportError:
                for _it in (apps + games):
                    _it["icon_b64"] = ""
            return {"success": True, "apps": apps, "games": games}
        except Exception as e:
            logger.exception("列出知识库失败")
            return {"success": False, "error": f"列出知识库失败: {e}"}

    def save_knowledge(self, data: dict) -> dict:
        """新增/更新一条用户知识库（app 或 game）。写入 user_knowledge.yaml，不污染 config。
        data: {kind:'app'|'game', id, name, keywords:[], knowledge}"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        kind = str((data or {}).get("kind") or "").strip().lower()
        if kind not in ("app", "game"):
            return {"success": False, "error": "kind 必须是 app 或 game"}
        uid = str((data or {}).get("id") or "").strip()
        if not uid:
            return {"success": False, "error": "id 不能为空"}
        try:
            from ..companion.game_knowledge import load_user_knowledge, save_user_knowledge
            user = load_user_knowledge()
            entry = {
                "id": uid,
                "name": str((data or {}).get("name") or "").strip(),
                "keywords": [str(k).strip() for k in ((data or {}).get("keywords") or []) if str(k).strip()],
                "knowledge": self._kb_join_three(data),
                "screen": str((data or {}).get("screen") or "").strip(),
                "focus": str((data or {}).get("focus") or "").strip(),
                "prompt": str((data or {}).get("prompt") or "").strip(),
                "mode": str((data or {}).get("mode") or "duplex").strip().lower() or "duplex",
                # 识别区块：归一化坐标矩形 + 关键词 + 触发类型。前端传 list[dict]，
                # 后端只做基本清洗（保留 dict 结构，过滤空项）。
                "ocr_regions": [
                    r for r in ((data or {}).get("ocr_regions") or [])
                    if isinstance(r, dict) and (r.get("keywords") or r.get("name") or r.get("rect"))
                ],
                "exe": [str(x).strip().lower() for x in ((data or {}).get("exe") or []) if str(x).strip()],
                "high_performance": bool((data or {}).get("high_performance")),
            }
            key = "app_knowledge" if kind == "app" else "games"
            lst = list(user.get(key) or [])
            idx = next((i for i, x in enumerate(lst) if str(x.get("id", "")).strip() == uid), None)
            if idx is not None:
                lst[idx] = entry
            else:
                lst.append(entry)
            user[key] = lst
            save_user_knowledge(user)
            self._reload_knowledge_runtime(kind)
            # 知识库是唯一配置入口：把 exe / 模式 / OCR / 高性能 镜像进 software_profiles
            # （运行时按 exe 读取的缓存），保证 OCR 调优 + 游戏锁 + 前置进程识别全部生效。
            self._mirror_knowledge_to_spr(entry, kind)
            return {"success": True, "id": uid}
        except Exception as e:
            logger.exception("保存知识库失败")
            return {"success": False, "error": f"保存知识库失败: {e}"}

    def delete_knowledge(self, data: dict) -> dict:
        """删除一条用户知识库。若它曾覆盖内置条目，删除后回落到内置默认。"""
        kind = str((data or {}).get("kind") or "").strip().lower()
        if kind not in ("app", "game"):
            return {"success": False, "error": "kind 必须是 app 或 game"}
        uid = str((data or {}).get("id") or "").strip()
        if not uid:
            return {"success": False, "error": "id 不能为空"}
        try:
            from ..companion.game_knowledge import load_user_knowledge, save_user_knowledge
            user = load_user_knowledge()
            key = "app_knowledge" if kind == "app" else "games"
            lst = [x for x in (user.get(key) or []) if str(x.get("id", "")).strip() != uid]
            user[key] = lst
            save_user_knowledge(user)
            self._reload_knowledge_runtime(kind)
            return {"success": True, "id": uid}
        except Exception as e:
            logger.exception("删除知识库失败")
            return {"success": False, "error": f"删除知识库失败: {e}"}

    def _reload_knowledge_runtime(self, kind: str) -> None:
        """保存/删除后让运行中的 AppKnowledge/GameKnowledge 重新合并用户文件。"""
        pro = getattr(self, "_proactive", None)
        if pro is None:
            return
        try:
            if kind in ("app", "both"):
                ak = getattr(pro, "_app_knowledge", None)
                if ak is not None and hasattr(ak, "_merge_user_apps"):
                    ak._merge_user_apps()
            if kind in ("game", "both"):
                gk = getattr(pro, "_game_knowledge", None)
                if gk is not None and hasattr(gk, "_merge_user_games"):
                    gk._merge_user_games()
        except Exception:  # noqa: BLE001
            logger.debug("运行时重载知识库失败", exc_info=True)

    def _mirror_knowledge_to_spr(self, entry: dict, kind: str) -> None:
        """知识库保存后，把 exe/模式/OCR/高性能镜像进 software_profiles 运行时缓存。

        知识库是用户配置的唯一入口（按 id 组织）；software_profiles 是按 exe 组织的
        运行时缓存，被 game_ocr（OCR 区块）、_game_lock_target_mode（云端快照/双工锁）、
        AppKnowledge.detect（前置进程命中弹窗）等读取。这里保证两边一致，避免『知识库
        改了却不生效』。
        """
        try:
            _exes = [str(x).strip().lower() for x in (entry.get("exe") or []) if str(x).strip()]
            if not _exes:
                return
            from ..companion.software_profiles import (
                MODE_SNAPSHOT, MODE_DUPLEX, TIER_HIGH, TIER_REGULAR,
            )
            spr = self._get_spr()
            _tier = TIER_HIGH if entry.get("high_performance") else TIER_REGULAR
            _mode = entry.get("mode")
            _mode = _mode if _mode in (MODE_SNAPSHOT, MODE_DUPLEX) else MODE_DUPLEX
            for _exe in _exes:
                _res = spr.add_from_process(
                    exe_path=_exe,
                    display_name=entry.get("name") or entry.get("id") or "",
                    tier=_tier,
                    kind=kind if kind in ("game", "app") else "app",
                )
                _pid = _res.get("id")
                if not _pid:
                    continue
                _upd = {"tier": _tier, "mode": _mode}
                if entry.get("ocr_regions"):
                    _upd["ocr_regions"] = entry["ocr_regions"]
                spr.update(_pid, **_upd)
        except Exception:  # noqa: BLE001
            logger.debug("镜像知识库→软件档案失败", exc_info=True)

    def get_foreground_exe(self) -> dict:
        """返回当前前台进程名与标题，供知识库向导『取当前前台进程』一键填入 exe。"""
        try:
            pro = getattr(self, "_proactive", None)
            mon = getattr(pro, "_monitor", None) if pro else None
            if mon is None:
                return {"success": False, "error": "monitor 未就绪"}
            return {
                "success": True,
                "exe": (mon.foreground_proc_name() or "").strip(),
                "title": (mon.foreground_title() or "").strip(),
                "hint": (mon.foreground_hint() or "").strip(),
            }
        except Exception as e:  # noqa: BLE001
            logger.debug("get_foreground_exe 失败", exc_info=True)
            return {"success": False, "error": str(e)}

    def pick_exe_file(self) -> dict:
        """弹出系统文件选择框让用户挑选 EXE（后台线程跑 tkinter）。"""
        import queue
        q: "queue.Queue[str]" = queue.Queue()

        def _pick() -> None:
            try:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
                try:
                    path = filedialog.askopenfilename(
                        title="选择程序（EXE）",
                        filetypes=[
                            ("可执行文件", "*.exe"),
                            ("所有文件", "*.*"),
                        ],
                    )
                finally:
                    root.destroy()
                q.put(path or "")
            except Exception as e:
                q.put(f"__ERR__:{e}")

        import threading
        threading.Thread(target=_pick, daemon=True).start()
        try:
            path = q.get(timeout=120)
        except Exception:
            return {"success": False, "error": "文件选择超时"}
        if path.startswith("__ERR__:"):
            return {"success": False, "error": path[len("__ERR__:"):]}
        if not path:
            return {"success": False, "error": ""}  # 用户取消
        # 提取 exe 文件名
        import os
        name = os.path.basename(path)
        return {"success": True, "path": path, "name": name}

    def pick_model_dir(self) -> dict:
        """弹出系统文件夹选择框让用户选择模型保存目录。优先用 pywebview 原生对话框；
        若主窗口未就绪则回退 tkinter（但 tkinter 必须在主线程创建，可能失败）。"""
        try:
            import webview
            win = self._main_win
            if win:
                result = win.create_file_dialog(webview.FOLDER_DIALOG)
                # pywebview 返回列表或字符串
                if result:
                    if isinstance(result, (list, tuple)) and len(result) > 0:
                        return {"success": True, "path": result[0]}
                    if isinstance(result, str):
                        return {"success": True, "path": result}
                return {"success": False, "error": ""}  # 用户取消或空结果
        except Exception as e:
            logger.warning("pywebview 原生选文件夹失败，尝试 tkinter 回退: %s", e)

        # 回退 tkinter（仅当主线程调用时有效）
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            try:
                root.attributes("-topmost", True)
            except Exception:
                pass
            try:
                path = filedialog.askdirectory(title="选择模型保存目录", mustexist=False)
            finally:
                root.destroy()
            if not path:
                return {"success": False, "error": ""}
            return {"success": True, "path": path}
        except Exception as e:
            logger.exception("tkinter 选文件夹失败")
            return {"success": False, "error": f"打开文件夹选择框失败: {e}"}

    def list_installed_exes(self, query: str = "") -> dict:
        """扫描常见安装目录下的 EXE，支持关键词过滤。

        返回 [{name, display_name, path}]，按友好名称排序。
        图标不在此处批量提取（前端通过 IntersectionObserver 滚动到可视区时按需调用
        extract_icon 逐个拉取，避免 80+ 图标阻塞列表渲染）。
        """
        # 防御：pywebview 把 JS 调用参数按位置传给方法；若前端误把 {query: ...} 当参数传进来，
        # 这里兜底取出真正的字符串，避免 query.strip() 抛 AttributeError 导致列表整片为空。
        if isinstance(query, dict):
            query = query.get("query", "") or ""
        import os, re
        results = []
        seen = set()

        # 常见安装路径（跨所有固定盘符：C/D/E… 的 Program Files、Games 等）
        scan_dirs = self._scan_program_dirs()
        # 排除的系统/通用目录前缀
        skip_prefixes = (
            "windows", "common files", "microsoft",
            ".net", "reference assemblies", "windowsapps",
        )
        # 已知垃圾 exe 名模式（安装器、卸载器、工具类）
        _junk_patterns = re.compile(
            r"(uninstall|setup|install|update|launcher|crashpad|helper|"
            r"updater|wizard|report|bug|log|config|reg|clean|repair|"
            r"check|verify|download|patch|bootstrapper|stub|webhelper)",
            re.I,
        )

        def _derive_display_name(exe_path: str, exe_filename: str) -> str:
            """从 exe 路径推导友好显示名称（对齐迈从风格）。

            策略：
            1. 取 exe 所在目录名（通常是软件/游戏名，如 DeltaForce、left 4 dead）
            2. 清理常见后缀（bin, win64, game, binaries 等）
            3. 若目录名太短或无意义，回退到 exe 文件名（去 .exe 后缀）
            """
            parent = os.path.basename(os.path.dirname(exe_path)) or ""
            # 清理常见的发行版/平台后缀
            clean = re.sub(
                r"[_\-](win64|win32|x64|x86|bin|binaries|game|client)"
                r"[_\-]?(64|32)?$", "", parent, flags=re.I,
            ).strip()
            # 清理版本号尾缀（如 v1.0, 2024.1）
            clean = re.sub(r"[\s_\-]v?\d+(\.\d+){0,3}\s*$", "", clean, flags=re.I).strip()
            # 如果清理后名字仍然太短（<=2 字符）或是纯数字/无意义串，用 exe 文件名
            if len(clean) <= 2 or clean.isdigit():
                clean = re.sub(r"\.exe$", "", exe_filename, flags=re.I)
            return clean

        for d in scan_dirs:
            if not d or not os.path.isdir(d):
                continue
            try:
                for root_dir, _dirs, files in os.walk(d):
                    # 跳过太深的层级和系统目录
                    rel = os.path.relpath(root_dir, d).lower()
                    if rel.count(os.sep) > 3:
                        _dirs[:] = []  # 不再深入
                        continue
                    if any(rel.startswith(p) for p in skip_prefixes):
                        _dirs[:] = []
                        continue
                    for f in files:
                        if not f.lower().endswith(".exe"):
                            continue
                        # 跳过垃圾 exe（安装器/卸载器等）
                        if _junk_patterns.search(f):
                            continue
                        full = os.path.join(root_dir, f)
                        if full.lower() in seen:
                            continue
                        seen.add(full.lower())
                        # 关键词过滤（同时匹配文件名和路径）
                        if query and query.strip():
                            q = query.strip().lower()
                            disp = _derive_display_name(full, f).lower()
                            if q not in f.lower() and q not in root_dir.lower() and q not in disp:
                                continue
                        results.append({
                            "name": f,
                            "display_name": _derive_display_name(full, f),
                            "path": full,
                        })
            except (PermissionError, OSError):
                continue

        # ── 补充：从 Start Menu 快捷方式提取 exe（覆盖非标准盘符安装，如 E:\douyin） ──
        _sm_dirs = [
            os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
                         "Microsoft", "Windows", "Start Menu"),
            os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu"),
        ]
        for _smd in _sm_dirs:
            if not os.path.isdir(_smd):
                continue
            try:
                for _sm_root, _sm_ddirs, _sm_files in os.walk(_smd):
                    for _lnk_fn in _sm_files:
                        if not _lnk_fn.lower().endswith(".lnk"):
                            continue
                        _lnk_full = os.path.join(_sm_root, _lnk_fn)
                        try:
                            with open(_lnk_full, "rb") as _lf:
                                _ld = _lf.read()
                            for _lm in re.finditer(
                                b"[A-Z]:\\\\[^\x00]*?\\.exe", _ld, re.IGNORECASE
                            ):
                                _candidate = _lm.group().decode("utf-8", errors="replace")
                                _cbase = os.path.basename(_candidate)
                                if not os.path.isfile(_candidate):
                                    continue
                                if _candidate.lower() in seen:
                                    continue
                                # 跳过垃圾 exe
                                if _junk_patterns.search(_cbase):
                                    continue
                                # 关键词过滤
                                if query and query.strip():
                                    _q = query.strip().lower()
                                    _disp = _derive_display_name(_candidate, _cbase).lower()
                                    if (_q not in _cbase.lower()
                                            and _q not in _candidate.lower()
                                            and _q not in _disp):
                                        continue
                                seen.add(_candidate.lower())
                                results.append({
                                    "name": _cbase,
                                    "display_name": _derive_display_name(_candidate, _cbase),
                                    "path": _candidate,
                                })
                        except (OSError, ValueError):
                            continue
            except (PermissionError, OSError):
                continue

        # 按友好名称排序，去重，限制数量
        results.sort(key=lambda x: x["display_name"].lower())
        results = results[:200]

        return {"success": True, "exes": results, "total": len(results)}

    # ---- 软件关联档案（迈从式绑定） ----
    def _get_spr(self):
        """懒初始化 SoftwareProfileRegistry 单例（与 proactive 共用 get_or_create_registry）。

        【2026-09-10 修复】原实现只建 self._spr、不注册模块单例 → proactive 侧拿不到
        （其 _software_profiles 恒 None）→ 软件档案的兜底锁/get_mode/find_by_exe_name 全失效。
        现统一走 get_or_create_registry，保证 webapp 与 proactive 是同一个实例。
        """
        spr = getattr(self, "_spr", None)
        if spr is None:
            from ..companion.software_profiles import get_or_create_registry
            # 与 user_knowledge.yaml 同目录（项目根）
            data_dir = os.path.dirname(os.path.abspath(self._config_path))
            spr = get_or_create_registry(data_dir)
            self._spr = spr
        return spr

    def list_software_profiles(self) -> dict:
        """列出全部软件档案（含图标 base64）。"""
        try:
            return {"success": True, "profiles": self._get_spr().list_all()}
        except Exception as e:
            logger.exception("list_software_profiles 失败")
            return {"success": False, "error": str(e)}

    def add_software_profile(self, data: dict) -> dict:
        """新增软件档案。data 含 exe_path/display_name/tier/kind 等。
        若传 exe_path 则自动提取图标；也可直接传 icon_b64。"""
        try:
            from ..companion.software_profiles import (
                SoftwareProfile, TIER_REGULAR, KIND_APP,
            )
            d = data or {}
            exe_path = str(d.get("exe_path") or "").strip()
            display_name = str(d.get("display_name") or "").strip()
            tier = str(d.get("tier") or TIER_REGULAR).strip()
            kind = str(d.get("kind") or KIND_APP).strip()
            icon_b64 = str(d.get("icon_b64") or "").strip()
            keywords = d.get("keywords") or []
            knowledge = str(d.get("knowledge") or "").strip()
            bind_companion = bool(d.get("bind_companion"))

            if not exe_path:
                return {"success": False, "error": "exe_path 不能为空"}

            mode = str(d.get("mode") or "duplex").strip()
            player_name = str(d.get("player_name") or "").strip()
            ocr_regions = d.get("ocr_regions") or []

            reg = self._get_spr()
            result = reg.add_from_process(
                exe_path=exe_path,
                display_name=display_name,
                tier=tier if tier in ("high_performance", "regular") else TIER_REGULAR,
                kind=kind if kind in ("game", "app") else KIND_APP,
            )
            # add_from_process 已提取图标；若前端传了自定义图标则覆盖
            if icon_b64:
                reg.update(result["id"], icon_b64=icon_b64)
            # 额外字段
            if keywords:
                reg.update(result["id"], keywords=keywords)
            if knowledge:
                reg.update(result["id"], knowledge=knowledge)
            if bind_companion:
                reg.update(result["id"], bind_companion=True)
            if mode in ("snapshot", "duplex"):
                reg.update(result["id"], mode=mode)
            if player_name:
                reg.update(result["id"], player_name=player_name)
            if ocr_regions:
                reg.update(result["id"], ocr_regions=ocr_regions)

            return {"success": True, "profile": reg.get(result["id"])}
        except Exception as e:
            logger.exception("add_software_profile 失败")
            return {"success": False, "error": str(e)}

    def update_software_profile(self, data: dict) -> dict:
        """更新软件档案部分字段。"""
        try:
            d = data or {}
            pid = str(d.get("id") or "").strip()
            if not pid:
                return {"success": False, "error": "id 不能为空"}
            reg = self._get_spr()
            kwargs = {}
            for k in ("display_name", "tier", "kind", "keywords", "knowledge",
                       "bind_companion", "icon_b64", "mode", "player_name",
                       "ocr_regions"):
                if k in d:
                    kwargs[k] = d[k]
            result = reg.update(pid, **kwargs)
            if result is None:
                return {"success": False, "error": f"档案不存在: {pid}"}
            return {"success": True, "profile": result}
        except Exception as e:
            logger.exception("update_software_profile 失败")
            return {"success": False, "error": str(e)}

    def delete_software_profile(self, data: dict) -> dict:
        """删除一条软件档案。"""
        try:
            pid = str((data or {}).get("id") or "").strip()
            if not pid:
                return {"success": False, "error": "id 不能为空"}
            reg = self._get_spr()
            ok = reg.delete(pid)
            if not ok:
                return {"success": False, "error": f"档案不存在: {pid}"}
            return {"success": True, "id": pid}
        except Exception as e:
            logger.exception("delete_software_profile 失败")
            return {"success": False, "error": str(e)}

    def enumerate_processes(self) -> dict:
        """枚举当前运行的非系统进程（用于「从活跃进程添加」）。"""
        try:
            from ..companion.software_profiles import enumerate_running_processes
            procs = enumerate_running_processes()
            # 图标已在 enumerate_running_processes 内批量提取（icon_b64 字段）
            return {"success": True, "processes": procs}
        except Exception as e:
            logger.exception("enumerate_processes 失败")
            return {"success": False, "error": str(e)}

    def get_active_software_profile_ocr(self) -> dict:
        """返回当前前台快照游戏的软件档案 OCR 配置（供开发者面板 OCR 调优页标注/路由）。

        若前台 exe 命中软件档案且 mode=snapshot，则返回该档案的 ocr_regions/player_name
        与标识；否则返回空（面板回退全局默认配置）。
        """
        try:
            from ..companion.software_profiles import (
                get_registry, MODE_SNAPSHOT,
            )
            reg = get_registry()
            mon = getattr(self, "_monitor", None)
            proc = ""
            if mon is not None:
                try:
                    proc = mon.foreground_proc_name() or ""
                except Exception:  # noqa: BLE001
                    proc = ""
            if not proc or reg is None:
                return {"success": True, "active": None}
            prof = reg.find_by_exe_name(proc)
            if prof is None or getattr(prof, "mode", "") != MODE_SNAPSHOT:
                return {"success": True, "active": None}
            return {
                "success": True,
                "active": {
                    "id": prof.id,
                    "exe_name": prof.exe_name,
                    "display_name": prof.display_name,
                    "player_name": prof.player_name or "",
                    "ocr_regions": prof.ocr_regions or [],
                },
            }
        except Exception as e:
            logger.exception("get_active_software_profile_ocr 失败")
            return {"success": False, "error": str(e)}

    def save_active_software_profile_ocr(self, data: dict) -> dict:
        """把开发者面板 OCR 调优的改动写入当前前台快照游戏的软件档案。

        data: {player_name?, regions?}（regions = [{name,purpose,x,y,w,h,keywords}]）。
        无前台快照游戏时失败（调用方应回退全局 OCR 配置保存）。
        """
        try:
            from ..companion.software_profiles import get_registry, MODE_SNAPSHOT
            reg = get_registry()
            mon = getattr(self, "_monitor", None)
            proc = ""
            if mon is not None:
                try:
                    proc = mon.foreground_proc_name() or ""
                except Exception:  # noqa: BLE001
                    proc = ""
            if not proc or reg is None:
                return {"success": False, "error": "当前无前台快照游戏"}
            prof = reg.find_by_exe_name(proc)
            if prof is None or getattr(prof, "mode", "") != MODE_SNAPSHOT:
                return {"success": False, "error": "当前前台非快照游戏档案"}
            kwargs = {}
            if "player_name" in (data or {}):
                kwargs["player_name"] = str(data["player_name"] or "").strip()
            if "regions" in (data or {}):
                kwargs["ocr_regions"] = data["regions"] or []
            if not kwargs:
                return {"success": False, "error": "无改动"}
            res = reg.update(prof.id, **kwargs)
            if res is None:
                return {"success": False, "error": "档案不存在"}
            return {"success": True, "profile": res}
        except Exception as e:
            logger.exception("save_active_software_profile_ocr 失败")
            return {"success": False, "error": str(e)}

    def extract_icon(self, data: dict) -> dict:
        """按 exe_path 提取图标 base64 PNG（前端按需调用）。"""
        try:
            from ..companion.software_profiles import extract_icon_from_exe
            exe_path = str((data or {}).get("exe_path") or "").strip()
            if not exe_path or not os.path.isfile(exe_path):
                return {"success": False, "error": "无效路径"}
            b64 = extract_icon_from_exe(exe_path, size=256)
            if not b64:
                return {"success": False, "error": "图标提取失败"}
            return {"success": True, "icon_b64": b64}
        except Exception as e:
            logger.exception("extract_icon 失败")
            return {"success": False, "error": str(e)}

    # ---- AI 生成知识库（云端免费 LLM，复用 text_chat.providers 中已配 key 的 openai 兼容 provider） ----
    def _kb_providers(self) -> list:
        """从原始配置（最可靠，绕开派生 ns 的潜在结构差异）取 text_chat.providers 标准化列表。"""
        if not self._cfg:
            return []
        raw = getattr(self._cfg, "raw", None) or {}
        if not isinstance(raw, dict):
            return []
        tc = raw.get("text_chat") or {}
        plist = tc.get("providers") or [] if isinstance(tc, dict) else []
        out = []
        for p in plist:
            if not isinstance(p, dict):
                continue
            out.append({
                "id": str(p.get("id") or "").strip(),
                "name": str(p.get("name") or p.get("id") or "").strip(),
                "provider": str(p.get("provider") or "").strip().lower(),
                "base_url": str(p.get("base_url") or "").strip().rstrip("/"),
                "model": str(p.get("model") or "").strip(),
                "api_key": str(p.get("api_key") or "").strip(),
            })
        return out

    def get_ai_providers(self) -> dict:
        """返回可用于 AI 生成知识库/人设的 LLM provider 列表（openai 兼容且已配 key 的）。

        前端据此判断「能否 AI 生成」：无任何可用 provider 时灰锁生成按钮并提示。
        注：云端双工（cloud_omni 配了 api_key）自带 LLM/VLM，视为可用源，避免云端模式下
        三段式生成被误判「未配置 API」（本地 openai provider 缺失是常态，云端走自供）。
        """
        try:
            plist = self._kb_providers()
            out = []
            for p in plist:
                if p["provider"] != "openai" or not p["base_url"] or not p["model"]:
                    continue
                out.append({"id": p["id"], "name": p["name"], "model": p["model"],
                            "has_key": bool(p["api_key"])})
            usable = [p for p in out if p["has_key"]]
            cloud_llm = False
            co = (self._cfg.raw.get("cloud_omni") if self._cfg else {}) or {}
            if isinstance(co, dict) and co.get("api_key") and co.get("model"):
                cloud_llm = True
                out.append({"id": "cloud_duplex", "name": "云端双工（自带 LLM）",
                            "model": str(co.get("model") or ""), "has_key": True,
                            "cloud": True})
            active = usable[0] if usable else (out[-1] if cloud_llm else None)
            return {"success": True, "providers": out, "active": active,
                    "cloud_llm": cloud_llm,
                    "debug": {"total": len(out), "usable": len(usable), "cloud_llm": cloud_llm}}
        except Exception as e:  # noqa: BLE001
            logger.exception("get_ai_providers 失败")
            return {"success": False, "error": str(e)}

    def generate_knowledge(self, data: dict) -> dict:
        """用云端免费 LLM 为某游戏/软件生成三段式陪伴知识。
        data: {kind:'app'|'game', name, provider?:id}
        后端直接拿游戏名 → 拼整段 prompt 丢给 API → 抠出三段式 → 返回 knowledge 文本供前端自动填入。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        name = str((data or {}).get("name") or "").strip()
        if not name:
            return {"success": False, "error": "请先填写游戏/软件名称"}
        kind = str((data or {}).get("kind") or "app").strip().lower()
        try:
            plist = self._kb_providers()
            want = str((data or {}).get("provider") or "").strip()
            chosen = None
            for p in plist:
                if p["provider"] != "openai" or not p["base_url"] or not p["model"] or not p["api_key"]:
                    continue
                if want and p["id"] == want:
                    chosen = p
                    break
                if chosen is None:
                    chosen = p  # 取第一个可用（按 config 顺序，默认智谱）
            if chosen is None:
                return {"success": False, "need_key": True,
                        "error": "未配置可用的免费 API：请在 config.yaml 的 text_chat.providers 给任一 openai 兼容 provider 填 api_key"}
            base = chosen["base_url"]
            model = chosen["model"]
            key = chosen["api_key"]
            pid = chosen["id"]
            pname = chosen["name"]
            sys_prompt = (
                "你是游戏/软件陪伴 AI 的知识库编辑助手。用户会给你一个游戏或软件的名字，"
                "你需要产出一段「三段式」陪伴知识，供 AI 桌宠在用户使用/游玩该游戏或软件时作为提示词注入。\n"
                "【边界·绝不要混淆】\n"
                "- 这套产出只服务于一个场景：用户在玩/用某个游戏或软件时，桌宠在屏幕前该留意什么、"
                "点评什么、何时闭嘴。它天生是关于「游戏/软件」的，不是关于某个角色的。\n"
                "- 本功能只接收游戏/软件名；角色名不应进入本流程——"
                "角色的「人设」由另一套生成负责，这里绝不写角色性格/说话/背景，也不写任何角色的战斗/抽卡/养成战术。\n"
                "严格按以下三个带书名号的段落输出，不要加任何额外解释、客套话或标题以外的内容：\n"
                "【领域关注】\n（该游戏/软件画面里常见元素、状态、事件；桌宠该留意什么、可以点评什么——只列关键点，别写百科长文）\n"
                "【建议 / 优先级】\n（屏幕上同时出现多件事时先说哪个；哪些时刻该闭嘴别打扰用户；高风险操作提醒分寸）\n"
                "【表达风格】\n（语气人设 + 明确禁忌/负面约束）\n"
            )
            kind_text = "游戏" if kind == "game" else "软件"
            user_prompt = "请为「%s」（类型：%s）生成上述三段式陪伴知识。" % (name, kind_text)
            import requests
            try:
                resp = requests.post(
                    "%s/chat/completions" % base,
                    headers={"Authorization": "Bearer %s" % key, "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": sys_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.8,
                        "max_tokens": 900,
                    },
                    timeout=60,
                )
                resp.raise_for_status()
                j = resp.json()
                text = (((j.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            except Exception as e:  # noqa: BLE001
                logger.exception("generate_knowledge 调 API 失败")
                return {"success": False, "error": "调用 API 失败：%s" % e}
            if not text:
                return {"success": False, "error": "API 返回内容为空"}
            parsed = self._parse_kb_three(text)
            return {"success": True, "provider": pid, "provider_name": pname, "raw": text, **parsed}
        except Exception as e:  # noqa: BLE001
            logger.exception("generate_knowledge 失败")
            return {"success": False, "error": str(e)}

    @staticmethod
    def _build_gen_payload(base: str, model: str, name: str, sys_prompt: str,
                           user_prompt: str, has_lore: bool) -> dict:
        """第二步生成的请求体。考据没拿到东西时，对智谱兜底挂 web_search tool，
        避免又回到纯幻觉（其它厂商不支持私有参数，不挂）。"""
        payload = {"model": model,
                   "messages": [{"role": "system", "content": sys_prompt},
                                {"role": "user", "content": user_prompt}],
                   "temperature": 0.8, "max_tokens": 1500}
        if (not has_lore) and "bigmodel" in (base or "").lower():
            payload["tools"] = [{"type": "web_search",
                                 "web_search": {"search_query": ("%s 角色 设定 出处" % name)[:70],
                                                "search_result": True}}]
            payload["tool_choice"] = "auto"
        return payload

    @staticmethod
    def _summarize_lore(base: str, key: str, model: str, name: str,
                        chunks: list) -> str:
        """把检索到的原文片段交给模型，整理成考据卡。只依据片段，不许凭记忆编。"""
        if not chunks:
            return ""
        # 相关性校验：片段里必须出现完整角色名，否则判为「没查到」。
        # （实测必应对「藿藿」会降级成字典解释「藿=豆叶」，拿去总结就会把角色写成植物）
        if not any(name in c for c in chunks):
            logger.warning("考据片段与角色名「%s」无关，判定未查到", name)
            return ""
        # 含完整角色名的片段够多就只喂这些，把被分词误召回的无关条目剔掉
        _hit = [c for c in chunks if name in c]
        if len(_hit) >= 3:
            chunks = _hit
        sys_p = (
            "你是 ACG / 游戏作品设定考据助手。下面给了联网检索到的原文片段，"
            "请你只依据这些片段整理成可直接用于写角色卡的设定要点。\n"
            "严禁补充片段里没有的信息，严禁凭你自己的记忆编造。\n"
            "只输出考据结果本身，不要客套、不要写角色卡。格式：\n"
            "出处：《作品名》（出品方）\n性别：（男 / 女，务必明确，别回避）\n身份：\n种族/职业：\n标志物或搭档：\n"
            "口头禅：\n习惯小动作：\n与其他角色的关系：\n性格要点：\n"
            "若片段里完全看不出这个角色出自哪部作品，只输出这一行：未检索到可靠资料\n若片段里没明写性别，就按该角色最通行的官方设定填，不要留空、不要回避。"
        )
        usr_p = "角色名：%s\n\n【检索片段】\n%s" % (name, "\n\n".join(chunks[:8]))
        try:
            import requests
            rr = requests.post("%s/chat/completions" % base,
                               headers={"Authorization": "Bearer %s" % key,
                                        "Content-Type": "application/json"},
                               json={"model": model,
                                     "messages": [{"role": "system", "content": sys_p},
                                                  {"role": "user", "content": usr_p}],
                                     "temperature": 0.3, "max_tokens": 900},
                               timeout=60)
            rr.raise_for_status()
            j = rr.json()
            text = (((j.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        except Exception as e:  # noqa: BLE001
            logger.warning("考据片段总结失败（忽略）：%s", e)
            return ""
        if (not text) or ("未检索到可靠资料" in text):
            return ""
        return text

    # 百科 / Wiki 类来源质量远高于「早报 / 盘点 / 排行榜」这类水文，必须优先喂给模型
    _GOOD_SRC = ("百科", "wiki", "萌娘", "fandom", "baike", "灰机", "wuthering",
                 "wikiwand", "wikiwiki")

    @staticmethod
    def _src_rank(x: dict, name: str = "") -> tuple:
        """来源排序：先按「是否含完整角色名」，再按「是否百科 / Wiki」。

        ⚠ 只看百科会翻车：多 query 时「藿藿 人物资料 身份 种族」被分词成霍姓，
        召回了「霍行仲百度百科」，光按百科优先会把它顶到最前面。
        """
        blob = ("%s %s %s" % (x.get("title") or "", x.get("link") or "",
                              x.get("content") or "")).lower()
        hit = 0 if (name and name in blob) else 1
        good = 0 if any(k in blob for k in CharacterAPI._GOOD_SRC) else 1
        return (hit, good)

    @staticmethod
    def _lore_queries(name: str, hint: str = "") -> list:
        """检索词按「百科型 → 介绍型 → 兜底型」排序。

        实测：对「守岸人」这类泛词义名字，只有百科型 query 能召回百科 / Wiki，
        否则全是水文，模型只能望文生义（把守岸人写成「守护海岸的人」）。
        """
        qs = ["%s 人物资料 身份 种族" % name,
              "%s 是谁 角色介绍" % name,
              "%s 角色 设定 出处" % name]
        if hint:
            qs.insert(0, "%s %s" % (name, hint))
        return qs

    @staticmethod
    def _lore_via_zhipu(base: str, key: str, model: str, name: str,
                        hint: str = "") -> tuple:
        """智谱（open.bigmodel.cn）：多 query 并发检索 → 去重 → 百科优先 → 模型整理。"""
        import requests
        out, seen = [], set()
        for q in CharacterAPI._lore_queries(name, hint)[:3]:
            try:
                r = requests.post("%s/web_search" % base,
                                  headers={"Authorization": "Bearer %s" % key,
                                           "Content-Type": "application/json"},
                                  json={"search_query": q[:70], "search_engine": "search_std",
                                        "search_intent": False, "count": 8,
                                        "search_recency_filter": "noLimit"},
                                  timeout=30)
                r.raise_for_status()
                res = ((r.json() or {}).get("search_result") or [])
            except Exception as e:  # noqa: BLE001
                logger.warning("智谱 web_search 失败（query=%s）：%s", q[:24], e)
                continue
            for x in res:
                if not isinstance(x, dict):
                    continue
                u = str(x.get("link") or "").strip()
                if not u or u in seen:
                    continue
                seen.add(u)
                out.append(x)
        if not out:
            return "", []
        out.sort(key=lambda x: CharacterAPI._src_rank(x, name))
        sources, chunks = [], []
        for x in out[:8]:
            link = str(x.get("link") or "").strip()
            title = str(x.get("title") or link or "").strip()
            body = str(x.get("content") or "").strip().replace("\n", " ")
            if link:
                sources.append({"title": title, "url": link})
            if body:
                chunks.append("- " + title + "\n" + body[:400])
        if not chunks:
            return "", sources
        return CharacterAPI._summarize_lore(base, key, model, name, chunks), sources

    @staticmethod
    def _lore_via_dashscope(base: str, key: str, model: str, name: str,
                            hint: str = "") -> tuple:
        """阿里百炼：enable_search 只是「允许搜」，必须再给 forced_search 才真搜。
        该端点不回传 search_results，所以 sources 恒空。"""
        sys_p = (
            "你是一个 ACG / 游戏作品设定考据助手。请联网检索，核实这个角色出自哪部作品，"
            "并整理成可以直接拿去写角色卡的设定要点。\n"
            "只输出考据结果本身，不要写角色卡，不要客套。格式：\n"
            "出处：《作品名》（出品方）\n性别：（男 / 女，务必明确，别回避）\n身份：\n种族/职业：\n标志物或搭档：\n"
            "口头禅：\n习惯小动作：\n与其他角色的关系：\n性格要点：\n"
            "确认查不到任何可靠结果时，只输出这一行：未检索到可靠资料\n若片段里没明写性别，就按该角色最通行的官方设定填，不要留空、不要回避。"
        )
        usr_p = "角色名：%s" % name
        if hint:
            usr_p += "\n用户补充：%s" % hint
        try:
            import requests
            resp = requests.post(
                "%s/chat/completions" % base,
                headers={"Authorization": "Bearer %s" % key,
                         "Content-Type": "application/json"},
                json={"model": model,
                      "messages": [{"role": "system", "content": sys_p},
                                   {"role": "user", "content": usr_p}],
                      "temperature": 0.3, "max_tokens": 900,
                      "enable_search": True,
                      "search_options": {"forced_search": True,
                                         "enable_source": True,
                                         "search_strategy": "max"}},
                timeout=60,
            )
            resp.raise_for_status()
            j = resp.json()
            text = (((j.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        except Exception as e:  # noqa: BLE001
            logger.warning("百炼角色联网考据失败（忽略）：%s", e)
            return "", []
        sources = []
        try:
            for r in ((j.get("search_info") or {}).get("search_results") or []):
                u = str(r.get("url") or "").strip()
                if u:
                    sources.append({"title": (r.get("title") or u), "url": u})
        except Exception:  # noqa: BLE001
            sources = []
        if (not text) or ("未检索到可靠资料" in text):
            return "", sources
        return text, sources

    @staticmethod
    def _lore_via_generic(base: str, key: str, model: str, name: str,
                          hint: str = "") -> tuple:
        """通用兜底：用项目自带的免 Key 联网搜索（必应 → DuckDuckGo），多 query + 百科优先。

        好处是任何 provider（OpenRouter / SiliconFlow / 本地 ollama…）都能联网考据，
        不依赖厂商私有参数，也不会因乱传参数被 400。
        """
        try:
            from ..llm import web_search as _ws
        except Exception as e:  # noqa: BLE001
            logger.warning("加载内置联网搜索失败（忽略）：%s", e)
            return "", []
        out, seen = [], set()
        for q in CharacterAPI._lore_queries(name, hint)[:3]:
            try:
                res = _ws.search(q[:120], top=8) or []
            except Exception as e:  # noqa: BLE001
                logger.warning("内置联网搜索失败（query=%s）：%s", q[:24], e)
                continue
            for x in res:
                if not isinstance(x, dict):
                    continue
                u = str(x.get("url") or "").strip()
                if not u or u in seen:
                    continue
                seen.add(u)
                out.append(x)
        if not out:
            return "", []
        out.sort(key=lambda x: CharacterAPI._src_rank(x, name))
        sources, chunks = [], []
        for x in out[:8]:
            url = str(x.get("url") or "").strip()
            title = str(x.get("title") or url or "").strip()
            snip = str(x.get("snippet") or "").strip()
            if url:
                sources.append({"title": title, "url": url})
            if title or snip:
                chunks.append("- " + title + "\n" + snip[:400])
        if not chunks:
            return "", sources
        if not (base and key and model):
            return "", sources
        return CharacterAPI._summarize_lore(base, key, model, name, chunks), sources

    @staticmethod
    def _lore_via_free(base: str, key: str, model: str, name: str,
                      hint: str = "") -> tuple:
        """免费直连考据：萌娘百科 / 中文维基百科抓**正文** → 所选 LLM 整理
        （_summarize_lore 不带 enable_search）。零付费搜索插件，云端「免费额度内体验」
        成立；准确性由真实网页正文保证，不会回到必应/DDG 的字典/官网幻觉。"""
        try:
            from ..llm import web_search as _ws
        except Exception as e:  # noqa: BLE001
            logger.warning("加载内置联网搜索失败（忽略）：%s", e)
            return "", []
        lore_text, sources = _ws.lookup_acg_lore(name)
        if not lore_text:
            return "", []
        summary = CharacterAPI._summarize_lore(base, key, model, name, [lore_text])
        return summary, sources

    @staticmethod
    def _lookup_character_lore(base: str, key: str, model: str, name: str,
                               hint: str = "") -> tuple:
        """联网考据角色原作设定（两步法第 1 步）。返回 (考据文本, 来源列表)。

        优先级（越靠前越免费、越准）：
        1) **直连百科考据（萌娘百科 + 中文维基百科抓正文）** → LLM 整理：免费免 key、
           不触发任何厂商「网页搜索插件」单独计费，且命中角色页、准确性高。
        2) 通用免 Key 联网搜索（必应 → DDG）摘要级兜底。
        3) 厂商原生搜索插件（百炼 enable_search / 智谱 /web_search）：**最后兜底**，
           仅当上面两条都拿不到东西（如本机无外网）时才可能触发付费。

        ⚠ 厂商原生搜索插件是按「搜索服务」子项与 token 分开单独计费的——这正是阿里云
        账单里「网页搜索」费用的来源。必须压到最末位，平时绝不走。
        """
        if not (base and key and model and name):
            return "", []
        # 1) 直连百科考据优先（零付费、准）
        text, sources = CharacterAPI._lore_via_free(base, key, model, name, hint)
        if text:
            return CharacterAPI._clean_lore(text), sources
        # 2) 通用免 Key 搜索兜底
        text, sources = CharacterAPI._lore_via_generic(base, key, model, name, hint)
        if text:
            return CharacterAPI._clean_lore(text), sources
        # 3) 厂商原生搜索插件（可能付费）：仅前面都失败时才兜底
        bl = (base or "").lower()
        if "dashscope" in bl:
            text, sources = CharacterAPI._lore_via_dashscope(base, key, model, name, hint)
        elif "bigmodel" in bl:
            text, sources = CharacterAPI._lore_via_zhipu(base, key, model, name, hint)
        else:
            text, sources = "", []
        return CharacterAPI._clean_lore(text), sources

    # 考据卡里这些值只是「检索没查到」的备注，留着会被模型照抄进最终人设
    _LORE_BLANKS = ("未提及", "未找到", "未知", "不确定", "未说明", "无相关",
                    "片段中未", "未查到", "没有提及", "未提供", "未给出",
                    "不详", "不明", "无明确", "N/A")

    @staticmethod
    def _clean_lore(text: str) -> str:
        """剔除考据卡里的占位行（口头禅：未提及 之类）。

        过滤后若所剩无几（不足以支撑判断出处），则保留原文交给模型自己权衡。
        """
        if not text:
            return ""
        keep = [ln for ln in text.splitlines()
                if not any(b in ln for b in CharacterAPI._LORE_BLANKS)]
        out = "\n".join(keep).strip()
        return out if len(out) >= 30 else text

    def generate_character(self, data: dict) -> dict:
        """用 LLM 为角色卡生成「人设三段式」（性格特征 / 说话风格 / 背景设定）。

        data: {name, hint?, provider?, search?}  provider 可按 id/name 指定用哪家服务商，
        不传则取第一个可用的 openai 兼容 provider；search 默认 True（联网考据）。
        与 generate_knowledge 的区别：生成的是角色人设（角色卡 eChar/eStyle/eBg），
        不是软件陪伴知识。provider 解析同 generate_knowledge，并支持云端双工兜底
        （云端模式自带 LLM，不再误判未配置 API）。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        name = str((data or {}).get("name") or "").strip()
        if not name:
            return {"success": False, "error": "请先填写角色名称"}
        hint = str((data or {}).get("hint") or "").strip()
        search_on = bool((data or {}).get("search", True))
        want = str((data or {}).get("provider") or "").strip()
        try:
            plist = self._kb_providers()
            chosen = None
            if want:  # 指定了服务商就优先精确匹配（id 或 name），配了多家时可挑
                for p in plist:
                    if str(p.get("id") or "") == want or str(p.get("name") or "") == want:
                        if p["provider"] == "openai" and p["base_url"] and p["model"] and p["api_key"]:
                            chosen = p
                            break
            if chosen is None:
                for p in plist:
                    if p["provider"] == "openai" and p["base_url"] and p["model"] and p["api_key"]:
                        chosen = p
                        break
            cloud_co = None
            if chosen is None:
                co = (self._cfg.raw.get("cloud_omni") or {}) if self._cfg else {}
                if isinstance(co, dict) and co.get("api_key") and co.get("model") and co.get("base_url"):
                    cloud_co = co  # 云端双工兜底（best-effort OpenAI 兼容文本端点）
            if chosen is None and cloud_co is None:
                return {"success": False, "need_key": True,
                        "error": "未配置可用的免费 LLM：请在「我的模型配置」给 openai 兼容 provider 填 api_key，"
                                 "或确保云端双工已绑 Key"}
            base = chosen["base_url"] if chosen else cloud_co["base_url"]
            model = chosen["model"] if chosen else cloud_co["model"]
            key = chosen["api_key"] if chosen else cloud_co["api_key"]
            pid = (chosen["id"] if chosen else "cloud_duplex")
            pname = (chosen["name"] if chosen else "云端双工")
            lore, sources, searched = "", [], False
            if search_on:
                lore, sources = self._lookup_character_lore(base, key, model, name, hint)
                searched = bool(lore)
            sys_prompt = (
                "你是 AI 桌宠 / 虚拟伴侣的角色设计师。用户会给你一个角色的名字"
                "（可选：用户补充的设定要点），你需要产出该角色的「人设三段式」，供角色卡自动填充。\n"
                "【判定与硬性要求】\n"
                "1) 先判断这个名字是否出自某部已有作品（游戏 / 动画 / 小说 / 影视 / 虚拟主播）。\n"
                "2) 若是已有作品角色：必须严格贴合原作——官方身份与所属势力、种族 / 职业、标志性的装备或搭档、"
                "原作里真实出现过的口头禅与小动作、和其他角色的关系。禁止自创与原作冲突的身世，"
                "禁止把名字当成普通词组望文生义。\n"
                "3) 组织 / 势力归属：若【联网考据】里出现了明确势力或组织，严格照考据写、绝不改写；"
                "若考据没有写出明确组织，绝不擅自套用该 IP 的大组织，也不要编造势力归属。\n"
                "4) 若确实不是任何已知作品角色（普通中文名 / 用户自创名）：按名字本身的气质感原创，不要硬套某个 IP。\n"
                "5) 拿不准时就按「没听说过」处理并原创，绝不能编造一个看似权威的出处或假资料。\n"
                "6) 若下文的 user 消息里带有「联网考据」段落：那是已检索核实过的原作资料，"
                "与你的记忆冲突时以考据为准；考据里没写的东西不要说成原作设定。\n"
                "7) 绝对不要把「未提及 / 未知 / 不确定」这类字眼写进最终输出——那只是考据过程的备注，"
                "照抄会让人觉得没查到；这类空缺项你按角色气质合理补充即可，但不要冒充成原作原文。\n"
                "【充实度要求】每一段都要写到「能直接当角色卡用」的颗粒度：有外貌、有具体互动反应、"
                "有可举例的说话习惯，不要只写标签式空话。\n"
                "严格按以下三个带书名号的段落输出，不要加任何额外解释、客套话或标题以外的内容：\n"
                "【性格特征】\n"
                "（写满 4-6 句：核心性格与解释；对主人的态度与具体表现；情绪底色与价值观；与原作角色的关系。具体、有记忆点，别写空话）\n"
                "【说话风格】\n"
                "（写满 4-6 句：①语气基调；②自称与对主人的称呼；"
                "③用词偏好（文言 / 口语 / 术语）；④常见口头禅（用原作真实出现过的，不要自创）；"
                "⑤标点 / 颜文字习惯；⑥何时毒舌、何时温柔）\n"
                "【背景设定】\n"
                "（写满 4-6 句：①外貌特征——发色 / 瞳色 / 身材 / 常服 / 标志物；"
                "②出处与所属势力，严格按【联网考据】（没有就原创，遵守第 3 条势力约束）；"
                "③一段在世界观里站得住的来历；④为何陪在屏幕边、与主人关系起点——留钩子让后续互动有戏）\n"
            )
            user_prompt = "请为角色「%s」生成上述人设三段式。" % name
            if hint:
                user_prompt += "\n用户补充的设定要点：" + hint
            if lore:
                user_prompt += "\n\n【联网考据 · 已检索核实，与你的记忆冲突时以此为准】\n" + lore
            import requests
            try:
                resp = requests.post(
                    "%s/chat/completions" % base,
                    headers={"Authorization": "Bearer %s" % key, "Content-Type": "application/json"},
                    json=self._build_gen_payload(base, model, name, sys_prompt,
                                                 user_prompt, bool(lore)),
                    timeout=60,
                )
                resp.raise_for_status()
                j = resp.json()
                text = (((j.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            except Exception as e:  # noqa: BLE001
                logger.exception("generate_character 调 API 失败")
                return {"success": False, "error": "调用 API 失败：%s" % e}
            if not text:
                return {"success": False, "error": "API 返回内容为空"}
            parsed = self._parse_character_three(text)
            return {"success": True, "provider": pid, "provider_name": pname, "raw": text,
                    "prompt": user_prompt, "model": model,
                    "searched": searched, "lore": lore, "sources": sources, **parsed}
        except Exception as e:  # noqa: BLE001
            logger.exception("generate_character 失败")
            return {"success": False, "error": str(e)}

    @staticmethod
    # ── 全盘 exe 扫描 / 进程检测辅助 ──
    @staticmethod
    def _list_fixed_drives() -> List[str]:
        """返回本机所有【本地固定】盘符（带反斜杠，如 ['C:\\', 'D:\\', 'E:\\']）。

        仅包含 DRIVE_FIXED（本地硬盘），排除网络盘(DRIVE_REMOTE)、可移动盘
        (DRIVE_REMOVABLE/CDROM)——后者在断连/未插卡时 os.walk 会长时间阻塞，
        是「检测卡死」的隐藏根因之一。

        兜底：GetLogicalDriveStringsW 在某些环境（沙箱/特殊挂载）只返回 C:，
        因此额外枚举 A-Z 字母盘，凡 os.path.isdir 可达且非明显网络盘都纳入。
        """
        drives: List[str] = []
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(256)
            n = ctypes.windll.kernel32.GetLogicalDriveStringsW(256, buf)
            if n:
                raw = buf.value[:n]
                for d in raw.split("\x00"):
                    if not d:
                        continue
                    # 查询盘符类型：3=DRIVE_FIXED(本地硬盘)，4=DRIVE_REMOTE(网络)
                    try:
                        dt = ctypes.windll.kernel32.GetDriveTypeW(d)
                    except Exception:
                        dt = 3
                    if dt == 3:  # 只收本地固定盘
                        drives.append(d if d.endswith("\\") else d + "\\")
        except Exception:
            pass
        # 兜底：A-Z 字母盘直接探测（覆盖 GetLogicalDriveStringsW 漏报的挂载盘）
        import string
        for letter in string.ascii_uppercase:
            d = letter + ":\\"
            if d in drives:
                continue
            try:
                if os.path.isdir(d):
                    drives.append(d)
            except Exception:
                continue
        if not drives:
            drives = ["C:\\"]
        return drives

    def _scan_program_dirs(self) -> List[str]:
        """跨所有固定盘符的常见安装目录（去重、跳过不存在的）。

        覆盖范围：
          - 系统/用户标准目录（C 盘 Program Files / LOCALAPPDATA）
          - 每个固定盘符的常见安装根（Program Files / Games / Riot / Epic 等）
          - Start Menu .lnk 由 _build_exe_index 单独补充。
        """
        dirs: List[str] = []
        seen = set()
        # 当前用户 / 系统标准目录（通常在 C 盘）
        for env in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
            d = os.environ.get(env, "")
            if d and os.path.isdir(d) and d.lower() not in seen:
                seen.add(d.lower()); dirs.append(d)
        # 每个固定盘符的常见安装根（含游戏平台非标准路径）
        for drv in self._list_fixed_drives():
            for sub in (
                "Program Files", "Program Files (x86)", "Games",
                "Riot Games",      # 无畏契约 / 英雄联盟 / 云顶之弈（Riot 官方客户端）
                "Epic Games",       # Epic 游戏商店
                "WeGame",           # 腾讯 WeGame（三角洲/无畏契约等常装于此）
            ):
                d = os.path.join(drv, sub)
                if os.path.isdir(d) and d.lower() not in seen:
                    seen.add(d.lower()); dirs.append(d)
            # WeGame 游戏本体在 <盘>:\Program Files (x86)\WeGame\apps\<短码>\ 深层
            wg_apps = os.path.join(drv, "Program Files (x86)", "WeGame", "apps")
            if os.path.isdir(wg_apps) and wg_apps.lower() not in seen:
                seen.add(wg_apps.lower()); dirs.append(wg_apps)
            # WeGame 真实游戏安装根：<盘>:\WeGameApps\rail_apps\<游戏名(id)>\，
            # 例如 D:\WeGameApps\rail_apps\DeltaForce(2001918)\DeltaForceClient.exe
            # （Program Files 下的 WeGame\apps 只放启动器 helper，真正的游戏客户端在这里）
            wg_rail = os.path.join(drv, "WeGameApps", "rail_apps")
            if os.path.isdir(wg_rail) and wg_rail.lower() not in seen:
                seen.add(wg_rail.lower()); dirs.append(wg_rail)
        return dirs

    def _detect_exe(self, exe_candidates) -> Optional[str]:
        """给定候选进程名列表，返回本机命中的 exe 完整路径；未命中返回 None。

        命中顺序：
          1. 当前运行进程（权威、即时）——按进程名精确匹配；
          2. 全盘安装目录扫描（_resolve_exe_path）——覆盖已装但未运行。
        返回命中的路径（运行进程取其 exe_path，否则取扫描到的路径）。
        运行进程列表在同一次检测内缓存 10s，避免逐条重复 EnumWindows。
        """
        import time
        candidates = [c.strip() for c in (exe_candidates or []) if str(c).strip()]
        if not candidates:
            return None
        names = {c.lower() for c in candidates}
        # 1) 运行进程匹配（带短缓存）
        try:
            cache = getattr(self, "_running_proc_cache", None)
            now = time.time()
            if not cache or (now - cache.get("_t", 0)) > 10:
                from ..companion.software_profiles import enumerate_running_processes
                procs = enumerate_running_processes()
                cache = {"_t": now, "procs": procs}
                self._running_proc_cache = cache
            else:
                procs = cache["procs"]
            for p in procs:
                pn = (p.get("name") or "").lower()
                if pn in names and p.get("exe_path") and os.path.isfile(p["exe_path"]):
                    return p["exe_path"]
        except Exception:
            pass
        # 2) 安装目录扫描
        for c in candidates:
            try:
                hit = self._resolve_exe_path(c)
                if hit:
                    return hit
            except Exception:
                continue
        return None

    def check_knowledge_detection(self) -> dict:
        """检测每条内置知识库条目在本机是否被发现对应进程。

        返回 {id: {detected: bool, exe_path: str, icon_b64: str}}。
        用于前端给「未安装/未找到进程」的条目打红色标识并提示手动配置。
        带 30s 内存缓存，避免每次渲染都全盘扫描。
        """
        try:
            import time
            now = time.time()
            cache = getattr(self, "_kb_detect_cache", None)
            if cache and (now - cache.get("_t", 0)) < 30:
                return {"success": True, "detection": cache["data"], "cached": True}
            res = self.list_knowledge()
            if not res.get("success"):
                return {"success": False, "error": res.get("error", "列表失败")}
            out = {}
            from ..companion.software_profiles import extract_icon_from_exe
            for it in (res.get("apps", []) + res.get("games", [])):
                eid = it.get("id")
                exe = it.get("exe") or []
                if not exe:
                    # 伴侣本体 / 能力类：无外部进程，恒为「已生效」，不亮红标
                    out[eid] = {"detected": True, "exe_path": "", "icon_b64": ""}
                    continue
                hit = self._detect_exe(exe)
                if hit:
                    icon = self._extract_icon_with_fallback(hit, exe)
                    out[eid] = {"detected": True, "exe_path": hit, "icon_b64": icon or ""}
                else:
                    out[eid] = {"detected": False, "exe_path": "", "icon_b64": ""}
            self._kb_detect_cache = {"_t": now, "data": out}
            return {"success": True, "detection": out, "cached": False}
        except Exception as e:
            logger.exception("知识库进程检测失败")
            return {"success": False, "error": str(e)}

    def _build_exe_index(self) -> dict:
        """一次性遍历所有安装目录，建立 {小写exe名: 完整路径} 索引。

        缓存 60s，避免每次检测都对全盘做 os.walk。这是检测性能的关键优化：
        原来每条知识库条目对每个 exe 候选都独立 os.walk 一次（~64 次全盘遍历，
        在机械盘/多盘符机器上可能卡数分钟）；改为建索引后全部 O(1) 查找。
        """
        import time
        cache = getattr(self, "_exe_index_cache", None)
        now = time.time()
        if cache and (now - cache.get("_t", 0)) < 60:
            return cache["data"]
        index: dict = {}
        scan_dirs = self._scan_program_dirs()
        for d in scan_dirs:
            if not d or not os.path.isdir(d):
                continue
            try:
                for root, _dirs, files in os.walk(d):
                    rel = os.path.relpath(root, d).lower()
                    if rel.count(os.sep) > 4:
                        _dirs[:] = []
                        continue
                    if any(rel.startswith(p) for p in ("windows", "common files", "microsoft", ".net")):
                        _dirs[:] = []
                        continue
                    for f in files:
                        fl = f.lower()
                        if fl.endswith(".exe") and fl not in index:
                            index[fl] = os.path.join(root, f)
            except (PermissionError, OSError):
                continue
        # Start Menu .lnk 作为补充（覆盖非标准盘符安装）
        try:
            import re
            start_menu_dirs = [
                os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
                             "Microsoft", "Windows", "Start Menu"),
                os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu"),
            ]
            for sm_dir in start_menu_dirs:
                if not os.path.isdir(sm_dir):
                    continue
                for root, _dirs, files in os.walk(sm_dir):
                    for fn in files:
                        if not fn.lower().endswith(".lnk"):
                            continue
                        lnk_path = os.path.join(root, fn)
                        try:
                            with open(lnk_path, "rb") as _f:
                                _data = _f.read()
                            for _m in re.finditer(b"[A-Z]:\\\\[^\x00]*?\\.exe", _data, re.IGNORECASE):
                                _candidate = _m.group().decode("utf-8", errors="replace")
                                _base = os.path.basename(_candidate).lower()
                                if _base.endswith(".exe") and _base not in index and os.path.isfile(_candidate):
                                    index[_base] = _candidate
                        except (OSError, ValueError):
                            continue
        except Exception:
            pass
        self._exe_index_cache = {"_t": now, "data": index}
        return index

    def _resolve_exe_path(self, exe_name: str) -> str | None:
        """将纯文件名（如 douyin.exe）解析为完整路径。

        优化：先查一次性构建的 exe 索引（O(1)），索引未命中再走 Start Menu .lnk 兜底。
        """
        if not exe_name or os.path.isabs(exe_name):
            return None
        name_lower = exe_name.lower()
        index = self._build_exe_index()
        return index.get(name_lower)

    def _extract_icon_with_fallback(self, detected_path: str, candidates: list) -> str:
        """从检测到的 exe 提取图标，失败时回退到同条目其他候选或目录树 .ico / 其他 exe。

        优先级（从高到低）：
          ① 主 exe 自身图标（最权威）→ ② 同条目其他候选 exe →
          ③ 同目录树 .ico 文件（WeGame 等平台把高清图标放在这里，如 app.ico）→
          ④ 同目录树其他非垃圾 exe → ⑤ 兜底任意尺寸。

        ⚠️ 绝不可把 .ico 遍历放在步骤①：Steam 目录下有 public\\check.ico（542B 勾号）、
        Electron 应用自带小图标等，会被误当应用图标返回。
        """
        from ..companion.software_profiles import extract_icon_from_exe
        from PIL import Image
        import io as _io
        import base64 as _b64
        tried = {detected_path.lower()}

        def _read_ico_as_png(ico_path: str, size: int = 128) -> str:
            """读取 .ico 文件转 128px PNG base64。"""
            try:
                img = Image.open(ico_path).convert("RGBA")
                img = img.resize((size, size), Image.LANCZOS)
                buf = _io.BytesIO()
                img.save(buf, format="PNG")
                return _b64.b64encode(buf.getvalue()).decode("ascii")
            except Exception:
                return ""

        # 1) 主路径 exe 图标（256px，>2000 过滤掉加密/反作弊返回的极小通用图标）
        try:
            icon = extract_icon_from_exe(detected_path, size=256)
            if icon and len(icon) > 2000:
                return icon
        except Exception:
            pass

        # 2) 同条目其他候选 exe（如 df_helper_main.exe 失败 → 试 DeltaForceClient.exe）
        for c in (candidates or []):
            cl = c.strip().lower()
            if not cl or cl in tried:
                continue
            alt = self._resolve_exe_path(c.strip())
            if alt and alt.lower() not in tried:
                tried.add(alt.lower())
                try:
                    icon = extract_icon_from_exe(alt, size=256)
                    if icon and len(icon) > 2000:
                        return icon
                except Exception:
                    pass

        # 3) 同目录树 .ico 文件——仅作为回退（WeGame app.ico 等平台高清图标在这里）
        #    加最小文件体积过滤（<2KB 的通常是工具/状态图标如 check.ico，不是应用图标）
        parent = os.path.dirname(detected_path)
        _ICO_MIN_BYTES = 2048
        if os.path.isdir(parent):
            try:
                best_ico: str = ""
                best_sz = 0
                for root, dirs, files in os.walk(parent):
                    if root.lower().count(os.sep) - parent.lower().count(os.sep) > 4:
                        dirs[:] = []
                        continue
                    for f in files:
                        if not f.lower().endswith(".ico"):
                            continue
                        fp = os.path.join(root, f)
                        try:
                            fsz = os.path.getsize(fp)
                        except Exception:
                            continue
                        if fsz < _ICO_MIN_BYTES:
                            continue
                        # 在所有合格 .ico 中选最大的（通常最高清）
                        if fsz > best_sz:
                            best_ico = fp
                            best_sz = fsz
                if best_ico:
                    ico_png = _read_ico_as_png(best_ico)
                    if ico_png:
                        return ico_png
            except Exception:
                pass

        # 4) 同目录其他非垃圾 exe（launcher 等常有比 helper 更好的图标）
        if os.path.isdir(parent):
            try:
                for root, dirs, files in os.walk(parent):
                    if root.lower().count(os.sep) - parent.lower().count(os.sep) > 4:
                        dirs[:] = []
                        continue
                    for f in files:
                        fl = f.lower()
                        if not fl.endswith(".exe"):
                            continue
                        fp = os.path.join(root, f)
                        if fp.lower() in tried:
                            continue
                        tried.add(fp.lower())
                        junk = ("uninstall", "setup", "install", "update", "crashpad",
                                "helper", "updater", "wizard", "bootstrapper", "tinydl")
                        if any(fl.startswith(j) for j in junk):
                            continue
                        try:
                            icon = extract_icon_from_exe(fp, size=256)
                            if icon and len(icon) > 2000:
                                return icon
                        except Exception:
                            continue
            except Exception:
                pass

        # 5) 兜底：任意尺寸的 exe 图标（总比 📦 好）
        try:
            icon = extract_icon_from_exe(detected_path, size=256)
            if icon:
                return icon
        except Exception:
            pass
        return ""

    @staticmethod
    def _parse_kb_three(text: str) -> dict:
        """从 LLM 返回里抠出三段式（领域关注 / 建议优先级 / 表达风格）。"""
        import re

        def grab(tag_re: str) -> str:
            m = re.search(tag_re + r"\s*[:：]?\s*(.*?)(?=\n\s*【|$)", text, re.S)
            return m.group(1).strip() if m else ""

        domain = grab(r"【领域关注】")
        advice = grab(r"【建议[\s/·]*优先级】")
        style = grab(r"【表达风格】")
        parts = []
        if domain:
            parts.append("【领域关注】\n" + domain)
        if advice:
            parts.append("【建议 / 优先级】\n" + advice)
        if style:
            parts.append("【表达风格】\n" + style)
        knowledge = "\n\n".join(parts) if parts else text.strip()
        return {"knowledge": knowledge, "domain": domain, "advice": advice, "style": style}

    @staticmethod
    def _parse_character_three(text: str) -> dict:
        """从 LLM 返回里抠出人设三段式（性格特征 / 说话风格 / 背景设定）。"""
        import re

        def grab(tag_re: str) -> str:
            m = re.search(tag_re + r"\s*[:：]?\s*(.*?)(?=\n\s*【|$)", text, re.S)
            return m.group(1).strip() if m else ""

        character = grab(r"【性格特征】")
        speaking = grab(r"【说话风格】")
        bg = grab(r"【背景设定】")
        return {"character": character, "speaking_style": speaking, "background": bg}

    @staticmethod
    def _kb_join_three(data: dict) -> str:
        """把三段式三字段拼回 knowledge 文本（优先）；三字段皆空则退回原 knowledge 字段。
        保证 YAML 只存单 knowledge 字段（与既有注入链路、数据模型完全兼容）。"""
        d = str((data or {}).get("domain") or "").strip()
        a = str((data or {}).get("advice") or "").strip()
        s = str((data or {}).get("style") or "").strip()
        if d or a or s:
            parts = []
            if d:
                parts.append("【领域关注】\n" + d)
            if a:
                parts.append("【建议 / 优先级】\n" + a)
            if s:
                parts.append("【表达风格】\n" + s)
            return "\n\n".join(parts)
        return str((data or {}).get("knowledge") or "").strip()

    # ---- 预检用工具 ----
    @staticmethod
    def _tcp_probe(endpoint: str, timeout_s: float = 2.0) -> bool:
        """探测 http(s)://host:port 端口是否在监听（不校验证书、不发业务请求）。"""
        import re
        import socket
        try:
            m = re.match(r"^https?://([^:/]+)(?::(\d+))?", endpoint or "")
            if not m:
                return False
            host = m.group(1) or "127.0.0.1"
            port = int(m.group(2) or (443 if endpoint.startswith("https") else 80))
            with socket.create_connection((host, port), timeout=timeout_s):
                return True
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _gateway_status(endpoint: str, timeout_s: float = 6.0) -> Optional[dict]:
        """查 MiniCPM 网关 /status（wss 端点 → https）。失败返回 None。"""
        import urllib.request
        try:
            e = endpoint or ""
            if e.startswith("wss://"):
                base = "https://" + e[6:].split("/")[0]
            elif e.startswith("ws://"):
                base = "http://" + e[5:].split("/")[0]
            else:
                base = e.split("?")[0].rsplit("/", 1)[0] if "/" in e else e
            req = urllib.request.Request(base + "/status",
                                         headers={"User-Agent": "ai-companion/1.0"})
            with urllib.request.urlopen(req, timeout=timeout_s,
                                         context=ssl._create_unverified_context()) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            return None

    # ======== 本地双工模型量化包目录（设备感知 + 魔塔下载） ========
    def _comni_model_dir(self) -> Optional[str]:
        """读 Comni 模型目录：优先 config.yaml 里 onboarding 写入的 model_dir（最权威），
        兜底读 Comni config.json 的 cpp_backend.model_dir。两者都不存在则返回 None。

        关键修复（2026-09-09）：之前只从 Comni config.json 读，而 onboarding 选的 model_dir
        若没写进 config.json（server_dir 失效），这里就返回 None → 模型检测到了却挂不上。
        现 config.yaml 的 local_omni_engine.model_dir 优先，确保 readiness 判定与引擎挂载一致。
        """
        try:
            pro_raw = (self._cfg.raw.get("proactive", {}) or {}) if self._cfg else {}
            loe = pro_raw.get("local_omni_engine", {}) or {}
            # 优先：onboarding 直接写入 config.yaml 的 model_dir
            md = (loe.get("model_dir") or "").strip()
            if md and os.path.isdir(md):
                return md
            # 兜底：Comni config.json 的 cpp_backend.model_dir
            server_dir = (loe.get("server_dir") or "").strip()
            if server_dir and os.path.isfile(os.path.join(server_dir, "config.json")):
                with open(os.path.join(server_dir, "config.json"), "r", encoding="utf-8") as f:
                    data = json.load(f)
                cdir = (data.get("cpp_backend", {}) or {}).get("model_dir") or None
                if cdir and os.path.isdir(cdir):
                    return cdir
        except Exception:
            pass
        return None

    def _resolve_comni_server_dir(self, model_dir: str = "") -> str:
        """解析 Comni 引擎 server_dir（含 config.json 的目录）。

        - 优先用已配置的、确实存在 config.json 的 server_dir；
        - 否则从 duplex_local_omni_exe（Comni 真实安装路径）反推：在其所在目录及常见
          子目录（_internal\\resources\\apps\\server、apps\\server 等）找 config.json。
        - 返回 "" 表示无法定位（调用方应回退，不强行写错路径）。
        """
        try:
            pro_raw = (self._cfg.raw.get("proactive", {}) or {}) if self._cfg else {}
            loe = pro_raw.get("local_omni_engine", {}) or {}
            sdir = (loe.get("server_dir") or "").strip()
            if sdir and os.path.isfile(os.path.join(sdir, "config.json")):
                return sdir
            exe = (pro_raw.get("duplex_local_omni_exe") or "").strip()
            if exe and os.path.isfile(exe):
                exedir = os.path.dirname(exe)
                for cand in ("", "_internal\\resources\\apps\\server",
                            "apps\\server", "_internal\\resources\\server", "server"):
                    d = os.path.join(exedir, cand) if cand else exedir
                    if os.path.isfile(os.path.join(d, "config.json")):
                        return d
        except Exception:
            pass
        return ""

    def get_model_catalog(self) -> dict:
        """返回 MiniCPM-o-4.5 量化包目录：设备显存 + 推荐档 + 各档魔塔链接/下载命令。

        双工引擎选 local_omni 时，设置页用它渲染「模型量化包」选择器（12GB 仅 Q4、
        16GB Q4/Q6、24GB+ Q6/Q8，按设备推荐）。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            from src.companion.model_catalog import get_model_catalog as _cat
            model_dir = self._comni_model_dir()
            payload = _cat(model_dir)
            payload["success"] = True
            return payload
        except Exception as e:
            logger.exception("获取模型量化包目录失败")
            return {"success": False, "error": str(e)}

    # ======== 模型目录自定义（自选盘符/路径 + 认领已有模型） ========
    def _comni_cfg_path(self) -> str:
        """Comni 的 config.json 绝对路径（model_dir 真正落盘的地方）。"""
        try:
            pro_raw = (self._cfg.raw.get("proactive", {}) or {}) if self._cfg else {}
            server_dir = (pro_raw.get("local_omni_engine", {}) or {}).get("server_dir", "")
            if server_dir:
                p = os.path.join(server_dir, "config.json")
                return p if os.path.isfile(p) else ""
        except Exception:
            pass
        return ""

    def _drive_list(self) -> list:
        """列出本机盘符及剩余/总空间（GB），供用户自选盘符。"""
        import shutil
        letters = []
        try:
            import ctypes
            import string
            mask = ctypes.windll.kernel32.GetLogicalDrives()
            letters = [f"{c}:\\" for c in string.ascii_uppercase if mask & (1 << (ord(c) - 65))]
        except Exception:
            import string
            letters = [f"{c}:\\" for c in string.ascii_uppercase if os.path.isdir(f"{c}:\\")]
        out = []
        for d in letters:
            try:
                u = shutil.disk_usage(d)
                out.append({"drive": d, "free_gb": round(u.free / (1024 ** 3), 1),
                            "total_gb": round(u.total / (1024 ** 3), 1)})
            except Exception:
                continue
        return out

    def _scan_dir_for_models(self, d: str) -> list:
        """在目录（含一层子目录）里找 MiniCPM GGUF，用于认领用户已有的模型。"""
        out = []
        if not d or not os.path.isdir(d):
            return out
        try:
            from src.companion.model_catalog import QUANTS as _Q
        except Exception:
            _Q = []
        dirs = [d]
        try:
            dirs += [os.path.join(d, n) for n in os.listdir(d)
                     if os.path.isdir(os.path.join(d, n))][:20]
        except Exception:
            pass
        for td in dirs:
            try:
                names = os.listdir(td)
            except Exception:
                continue
            for n in names:
                low = n.lower()
                if not low.endswith(".gguf") or "minicpm" not in low:
                    continue
                fp = os.path.join(td, n)
                if not os.path.isfile(fp):
                    continue
                try:
                    size_gb = round(os.path.getsize(fp) / (1024 ** 3), 2)
                except Exception:
                    continue
                entry = next((q for q in _Q if str(q.get("file", "")).lower() == low), None)
                complete = True
                if entry and entry.get("size_gb"):
                    try:
                        # 低于目录标注体积的 80% 视为下载中断（防半包被认成完整）
                        complete = size_gb >= float(entry["size_gb"]) * 0.8
                    except Exception:
                        complete = True
                out.append({"file": n, "path": fp, "dir": td, "size_gb": size_gb,
                            "quant_id": (entry or {}).get("id") or None, "complete": complete})
        return out

    def get_model_dir_options(self) -> dict:
        """模型目录选项：当前目录 + 是否可写 + 剩余空间 + 各盘空间 + 目录里已有模型。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            import shutil
            cur = self._comni_model_dir() or ""
            exists = bool(cur) and os.path.isdir(cur)
            free_gb, writable = None, False
            if exists:
                try:
                    free_gb = round(shutil.disk_usage(cur).free / (1024 ** 3), 1)
                    probe = os.path.join(cur, ".daniya_write_test.tmp")
                    with open(probe, "w", encoding="utf-8") as f:
                        f.write("x")
                    os.remove(probe)
                    writable = True
                except Exception:
                    writable = False
            return {"success": True, "current": cur, "comni_cfg": self._comni_cfg_path(),
                    "exists": exists, "writable": writable, "free_gb": free_gb,
                    "drives": self._drive_list(), "found": self._scan_dir_for_models(cur)}
        except Exception as e:
            logger.exception("获取模型目录选项失败")
            return {"success": False, "error": str(e)}

    def scan_model_dir(self, data: dict) -> dict:
        """扫描用户指定目录：是否存在/可写/剩余空间 + 里面有没有可认领的 MiniCPM GGUF。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        d = str((data or {}).get("dir") or "").strip()
        if not d:
            return {"success": False, "error": "未指定目录"}
        try:
            import shutil
            exists = os.path.isdir(d)
            free_gb, writable = None, False
            if exists:
                try:
                    free_gb = round(shutil.disk_usage(d).free / (1024 ** 3), 1)
                    writable = os.access(d, os.W_OK)
                except Exception:
                    pass
            return {"success": True, "dir": d, "exists": exists, "writable": writable,
                    "free_gb": free_gb, "found": self._scan_dir_for_models(d)}
        except Exception as e:
            logger.exception("扫描模型目录失败")
            return {"success": False, "error": str(e)}

    def set_model_dir(self, data: dict) -> dict:
        """把用户选的目录写回 Comni config.json 的 cpp_backend.model_dir（引擎运行时读它）。

        若目录里已有完整 GGUF，顺带把 cpp_backend.llm_model 指过去 → 直接认领，无需重下。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        d = str((data or {}).get("dir") or "").strip()
        quant = str((data or {}).get("quant_id") or "").strip() or None
        if not d:
            return {"success": False, "error": "未指定目录"}
        if _path_has_non_ascii(d):
            # 中文/非 ASCII 路径 C++ 引擎读不了 → 拒绝落盘，避免「挂不上」的诡异故障
            return {"success": False, "error": "模型目录不能含中文或特殊字符（非 ASCII）。请改用纯英文/数字路径，否则 C++ 引擎无法加载模型。"}
        try:
            if not os.path.isdir(d):
                try:
                    os.makedirs(d, exist_ok=True)
                except Exception as e:
                    return {"success": False, "error": f"目录无法创建：{e}"}
            cfg_path = self._comni_cfg_path()
            if not cfg_path:
                return {"success": False, "error": "找不到 Comni 配置文件（server_dir 未配置）"}
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            cb = cfg.setdefault("cpp_backend", {})
            cb["model_dir"] = d
            found = self._scan_dir_for_models(d)
            # 只认领"主模型"（带 quant_id 的 LLM GGUF）：
            # 目录里还有 audio/vision/tts 等子模型，绝不能把它们设成 llm_model
            cands = [x for x in found if x.get("quant_id") and x.get("complete")]
            pick = None
            if quant:
                pick = next((x for x in cands if x.get("quant_id") == quant), None)
            if pick is None and cands:
                pick = cands[0]
            if pick:
                cb["llm_model"] = pick["file"]
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            return {"success": True, "model_dir": d, "llm_model": cb.get("llm_model") or "",
                    "found": found, "adopted": bool(pick)}
        except Exception as e:
            logger.exception("设置模型目录失败")
            return {"success": False, "error": str(e)}

    def warm_local_model(self) -> dict:
        """按用户选的模式暖机：后台拉起本地引擎并挂载模型（不阻塞界面）。

        起因（2026-09-09 实测）：save_first_run_config 只写意图（duplex_mode=local_omni）
        不触发引擎热切换，而 local_omni_engine.auto_start 此前**没有任何应用级代码消费**
        （只有引擎类自己读它）→ 结果是"模型检测到了，进主页却不加载"。本方法补上这一环。

        幂等：模型已挂载 / 非本地双工模式 / 模型未就绪 / 正在玩游戏 → 都直接跳过。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            raw = self._cfg.raw
            feats = raw.get("companion_features")
            if not isinstance(feats, list):
                feats = self._derive_features_from_config(raw)
            fids = {f["id"] for f in feats if isinstance(f, dict)}
            pro_raw = raw.get("proactive") or {}
            # 【2026-10-08 修·"纯本地却没自动挂本地模型"】原判定要求
            # `str(pro_raw.get("duplex_mode")) == "local_omni"` —— 但实测 config 里
            # **proactive.duplex_mode 这个键根本不存在**（历史写入路径只落了 companion_features），
            # 于是 str(None)='None' ≠ 'local_omni' ⇒ 暖机**永远**提前返回"当前模式不需要本地模型暖机"，
            # 用户看到的就是"选了纯本地、本地模型却从不自动连接"。
            # 现在以**功能集为主**判意图：features 里有 local_duplex 就说明用户要本地双工；
            # duplex_mode 只用来排除"明确切到了云端/关闭"的情况（缺失/auto/true 都算本地意图）。
            _dm_raw = pro_raw.get("duplex_mode")
            _dm = ("" if _dm_raw is None else str(_dm_raw).strip().lower())
            _local_intent = ("local_duplex" in fids) and (
                _dm in ("", "local_omni", "local", "auto", "true"))
            if not _local_intent:
                return {"success": True, "warm": False, "loaded": False,
                        "reason": "当前模式不需要本地模型暖机(duplex_mode=%r, local_duplex=%s)"
                                  % (_dm_raw, "local_duplex" in fids)}
            r = self.get_companion_readiness()
            if not r.get("ready"):
                return {"success": True, "warm": False, "loaded": False,
                        "reason": "模型尚未就绪（need=%s），先下载" % r.get("need")}
            pro = getattr(self, "_proactive", None)
            if pro is None:
                return {"success": True, "warm": False, "loaded": False, "reason": "proactive 未就绪"}
            # 正在玩游戏就别抢显卡（is_game_process_foreground 走采样缓存，可安全调用）
            try:
                mon = getattr(pro, "_monitor", None)
                if mon is not None and mon.is_game_process_foreground():
                    return {"success": True, "warm": False, "loaded": False, "reason": "检测到正在玩游戏，跳过加载"}
            except Exception:
                pass
            # 【2026-09-29 黑屏修复】把"引擎创建 + 加载"整体丢到守护线程执行，
            # 绝不在 GUI 线程同步创建本地引擎：create_local_omni_engine 会 import 重型
            # 依赖（llama.cpp / torch 绑定）并构建推理管线，曾在 GUI 线程同步执行时
            # 阻塞 WebView2 消息泵 → 引导页"完成，开始使用"后整窗黑屏卡死
            # （本机免费档含 local_duplex，必走此路径）。
            import threading
            pro_cfg = getattr(pro, "_duplex_pro_cfg", None) or pro_raw
            ref_audio = getattr(pro, "_duplex_local_omni_ref_audio", "") or ""

            def _load() -> None:
                try:
                    eng = getattr(pro, "_engine", None)
                    if eng is None:
                        from src.engine.factory import create_local_omni_engine
                        eng = create_local_omni_engine(pro_cfg, ref_audio)
                        pro._local_omni_engine = eng
                        pro._engine = eng   # 让 _run_local_omni_duplex 直接复用，避免二次拉起
                        logger.info("暖机：已创建本地全双工引擎 %s", type(eng).__name__)
                    if eng.is_model_loaded():
                        logger.info("暖机：模型已挂载，无需重新加载")
                        return
                    eng.start()
                    logger.info("暖机完成：本地模型%s", "已挂载" if eng.is_model_loaded() else "未确认挂载")
                except Exception as e:  # noqa: BLE001
                    logger.warning("暖机失败：%s", e)

            threading.Thread(target=_load, daemon=True, name="local-model-warm").start()
            return {"success": True, "warm": True, "loaded": False, "reason": "已在后台开始加载本地模型"}
        except Exception as e:  # noqa: BLE001
            logger.exception("本地模型暖机异常")
            return {"success": False, "error": str(e)}

    def get_local_model_state(self) -> dict:
        """本地模型当前状态：是否需要加载 / 是否已挂载 / 引擎进程情况。"""
        try:
            pro = getattr(self, "_proactive", None)
            eng = getattr(pro, "_engine", None) if pro is not None else None
            loaded = False
            if eng is not None:
                try:
                    loaded = bool(eng.is_model_loaded())
                except Exception:
                    loaded = False
            return {"success": True, "has_engine": eng is not None, "loaded": loaded}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    # ======== 新人引导链路（Apply 阶段真正生效，避免「检测到了不加载」） ========
    def _builtin_ref_path(self) -> str:
        """内置默认参考音频（达妮娅本音），供零配置兜底。"""
        _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(_root, "assets", "voices", "daniya_ref.wav")

    def _resolve_ref_audio(self, d: dict):
        """返回 (path, source)；source ∈ user/builtin/none。"""
        if not d:
            bp = self._builtin_ref_path()
            return (bp, "builtin") if (bp and os.path.isfile(bp)) else (None, "none")
        user_path = (d.get("reference_audio") or "").strip()
        if user_path and os.path.isfile(user_path):
            return (user_path, "user")
        if d.get("use_builtin_ref"):
            bp = self._builtin_ref_path()
            if bp and os.path.isfile(bp):
                return (bp, "builtin")
        bp = self._builtin_ref_path()
        if bp and os.path.isfile(bp):
            return (bp, "builtin")
        return (None, "none")

    def _local_qwen_ready(self) -> bool:
        try:
            return bool(self.check_local_models().get("ready"))
        except Exception:  # noqa: BLE001
            return False

    def get_onboarding_env(self) -> dict:
        """首启引导页一次拉取的环境快照（显卡/Comni/就绪/声音/LLM/麦克风/功能集/整套模型）。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            out: dict = {"success": True}
            try:
                # nvidia-smi 可能被繁忙 GPU 拖慢（最长 10s）：绝不在 GUI 线程同步等，
                # 丢到线程取，3s 内拿不到就降级（gpu 非必需字段，不影响引导页渲染）。
                import threading as _th
                _gpu_box: dict = {}
                def _gpu_probe():
                    try:
                        _gpu_box["v"] = self.get_gpu_vram()
                    except Exception:  # noqa: BLE001
                        _gpu_box["v"] = {"ok": False, "vram_gb": None, "name": ""}
                _gt = _th.Thread(target=_gpu_probe, daemon=True)
                _gt.start()
                _gt.join(timeout=3.0)
                out["gpu"] = _gpu_box.get("v") or {"ok": False, "vram_gb": None, "name": ""}
            except Exception:  # noqa: BLE001
                out["gpu"] = {"ok": False, "vram_gb": None, "name": ""}
            try:
                md = self._comni_model_dir() or ""
                cfgp = self._comni_cfg_path()
                free = None
                if md and os.path.isdir(md):
                    import shutil
                    free = round(shutil.disk_usage(md).free / (1024 ** 3), 1)
                out["comni"] = {
                    "server_dir": (self._cfg.raw.get("proactive", {}) or {})
                    .get("local_omni_engine", {}).get("server_dir", ""),
                    "cfg_path": cfgp, "model_dir": md,
                    "model_dir_ok": bool(md), "free_gb": free,
                }
            except Exception:  # noqa: BLE001
                out["comni"] = {}
            try:
                out["comni_engine"] = self.get_comni_engine_status()
            except Exception:  # noqa: BLE001
                out["comni_engine"] = {}
            try:
                out["readiness"] = self.get_companion_readiness()
            except Exception:  # noqa: BLE001
                out["readiness"] = {}
            try:
                out["voice"] = self.get_voice_mode()
            except Exception:  # noqa: BLE001
                out["voice"] = {}
            try:
                out["tts_local"] = self.check_local_models()
            except Exception:  # noqa: BLE001
                out["tts_local"] = {}
            try:
                out["llm"] = self.get_llm_settings()
            except Exception:  # noqa: BLE001
                out["llm"] = {}
            try:
                box: dict = {}
                def _r():
                    try:
                        box["v"] = self._list_mic_devices()
                    except Exception:  # noqa: BLE001
                        box["v"] = []
                t = threading.Thread(target=_r, daemon=True)
                t.start()
                t.join(timeout=6)
                out["mic_devices"] = box.get("v", [])
            except Exception:  # noqa: BLE001
                out["mic_devices"] = []
            try:
                out["features"] = self.get_companion_features().get("features", [])
            except Exception:  # noqa: BLE001
                out["features"] = []
            try:
                md = self._comni_model_dir() or ""
                if md:
                    from src.companion.model_catalog import verify_bundle
                    quant = (self._cfg.raw.get("proactive", {}) or {}).get(
                        "duplex_local_omni_quant") or "Q4_K_M"
                    out["bundle"] = verify_bundle(md, quant)
                else:
                    out["bundle"] = {"complete": False, "missing": ["(model_dir)"]}
            except Exception:  # noqa: BLE001
                out["bundle"] = {}
            return out
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def ensure_comni_layout(self, data: dict = None) -> dict:
        """确保 Comni 引擎组件就绪：server_dir 指向含 config.json 的目录、model_dir 可写且有空间。

        修 B4：model_dir 为空 / Comni 缺失 时明确告诉引导页「必须选目录」，不再静默往 H 写。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        d = data or {}
        try:
            raw = self._cfg.raw
            pro_raw = raw.setdefault("proactive", {})
            loe = pro_raw.setdefault("local_omni_engine", {})
            if d.get("server_dir"):
                loe["server_dir"] = str(d["server_dir"]).strip()
                self._save_config()
            server_dir = loe.get("server_dir", "")
            cfg_path = os.path.join(server_dir, "config.json") if server_dir else ""
            ok, need, free_gb, writable = True, None, None, False
            if not server_dir or not os.path.isfile(cfg_path):
                if server_dir:
                    ex = os.path.join(server_dir, "config.example.json")
                    if os.path.isfile(ex):
                        try:
                            import shutil as _sh
                            _sh.copyfile(ex, cfg_path)
                        except Exception:  # noqa: BLE001
                            pass
                if not (server_dir and os.path.isfile(cfg_path)):
                    ok, need = False, "server_dir"
            if ok and d.get("model_dir"):
                md = str(d["model_dir"]).strip()
                os.makedirs(md, exist_ok=True)
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                    cfg.setdefault("cpp_backend", {})["model_dir"] = md
                    with open(cfg_path, "w", encoding="utf-8") as f:
                        json.dump(cfg, f, ensure_ascii=False, indent=2)
                    self._save_config()
                except Exception as e:  # noqa: BLE001
                    return {"success": True, "ok": False, "need": "server_dir",
                            "error": str(e), "hint": "无法写入 Comni 配置"}
                try:
                    import shutil as _sh2
                    free_gb = round(_sh2.disk_usage(md).free / (1024 ** 3), 1)
                    writable = os.access(md, os.W_OK)
                except Exception:  # noqa: BLE001
                    pass
            return {"success": True, "ok": ok, "need": need, "server_dir": server_dir,
                    "cfg_path": cfg_path, "model_dir": (d.get("model_dir") or ""),
                    "writable": writable, "free_gb": free_gb, "need_gb": 9.0,
                    "hint": ("" if ok else "未找到本地引擎组件(Comni)；请指定 Comni 的 server 目录")}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def list_mic_devices(self, data: dict = None) -> dict:
        """列出音频输入设备（供引导页麦克风屏）。"""
        try:
            devs = self._list_mic_devices()
            current = ((getattr(self._proactive, "_duplex_mic_device", None)
                        if self._proactive else None)
                       or (self._cfg.raw.get("proactive", {}) or {}).get("duplex_mic_device")
                       or "default")
            return {"success": True, "devices": devs, "current": str(current)}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    # ===================== 开发者页·音频自测台（2026-10-17） =====================
    def dev_mic_level(self, data: dict = None) -> dict:
        """麦克风实时电平（开发者页律动条）——桥接线程绝不碰音频流。

        【2026-10-17 三次修复·"第二次切换就开不起来"】
        前两版都复用"一个长命 worker + 共享 PyAudio"：换设备要由它关旧流再开新流，
        实测第二次起就卡死（关流/开流在同一实例上互相拖住，且跨线程 close 会挂）。
        现在改为**一次性 worker**：
          · 每次 start 递增 `_dev_mic_gen` 并新建线程；
          · 每个 worker 自带**独立** `pyaudio.PyAudio()`（不碰共享单例，互不干扰）；
          · 旧 worker 见到 gen 变化 → **自己**关流 + terminate → 退出；
          · 新 worker 从干净状态开流 ⇒ 换设备每次都生效。
        """
        d = data or {}
        action = str(d.get("action") or "poll").strip().lower()

        if action == "start":
            dev = str(d.get("device") or "default")
            box = {"rms": 0.0, "peak": 0.0, "err": "", "used": {}, "note": ""}
            self._dev_mic_box = box
            self._dev_mic_gen = int(getattr(self, "_dev_mic_gen", 0)) + 1
            gen = self._dev_mic_gen
            self._dev_mic_want = dev
            threading.Thread(target=self._dev_mic_worker, args=(gen, dev, box),
                             daemon=True).start()
            for _ in range(15):          # 最多 1.5s，绝不死等
                if box.get("used") or box.get("err"):
                    break
                time.sleep(0.1)
            return {"success": True, "started": True,
                    "device_used": box.get("used") or {},
                    "note": box.get("note", ""), "error": box.get("err", "")}

        if action == "stop":
            self._dev_mic_want = None
            self._dev_mic_gen = int(getattr(self, "_dev_mic_gen", 0)) + 1   # 让 worker 自行退出
            return {"success": True, "stopped": True}

        box = getattr(self, "_dev_mic_box", {}) or {}
        rms = float(box.get("rms") or 0.0)
        peak = float(box.get("peak") or 0.0)
        import math as _m
        level = 0.0
        if rms > 1e-5:
            level = max(0.0, min(1.0, (_m.log10(max(rms, 1e-5)) + 4.0) / 3.2))
        return {"success": True, "rms": rms, "peak": peak, "level": level,
                "device_used": box.get("used") or {}, "note": box.get("note", ""),
                "error": box.get("err", "")}

    def _dev_mic_worker(self, gen: int, want: str, box: dict) -> None:
        """一次性采集 worker：独立 PyAudio 实例 + 回调模式；gen 变化即自行收尾退出。"""
        pa = None
        stream = None
        try:
            import numpy as _np
            import pyaudio as _pa_mod
            from ..llm.cloud_omni_realtime import pa_int16
            pa = _pa_mod.PyAudio()          # ★ 独立实例：不碰共享单例，避免互相卡住

            def _cb(in_data, frame_count, time_info, status, _box=box, _np=_np):
                try:
                    _a = _np.frombuffer(in_data, dtype=_np.int16).astype(_np.float32) / 32768.0
                    _box["rms"] = float(_np.sqrt(_np.mean(_np.square(_a)))) if _a.size else 0.0
                    _pk = float(_np.max(_np.abs(_a))) if _a.size else 0.0
                    _box["peak"] = max(_pk, float(_box.get("peak") or 0.0) * 0.92)
                except Exception:  # noqa: BLE001
                    pass
                return (None, _pa_mod.paContinue)

            kw = dict(format=pa_int16(), channels=1, rate=16000,
                      input=True, frames_per_buffer=1600, stream_callback=_cb)
            # 解析目标设备（支持 `名字|hostapi`）
            idx = None
            api_want = ""
            name_want = str(want or "")
            if "|" in name_want:
                name_want, _, api_want = name_want.partition("|")
                api_want = api_want.strip().lower()
            name_want = name_want.strip().lower()
            if name_want and name_want not in ("default", "auto", "none"):
                for _i in range(pa.get_device_count()):
                    try:
                        _d = pa.get_device_info_by_index(_i)
                    except Exception:  # noqa: BLE001
                        continue
                    if int((_d or {}).get("maxInputChannels") or 0) <= 0:
                        continue
                    if str((_d or {}).get("name") or "").strip().lower() != name_want:
                        continue
                    if api_want:
                        _ha = (_d or {}).get("hostApi")
                        try:
                            _an = str(pa.get_host_api_info_by_index(int(_ha)).get("name") or "").strip().lower()
                        except Exception:  # noqa: BLE001
                            _an = ""
                        if _an != api_want:
                            continue
                    idx = _i
                    break
            # 【2026-10-17 定稿·host 由程序有依据地挑】
            # 顺序：① 记忆里"已验证能采到人声"的 host（应用已有 remember_verified_mic）
            #       ② Windows WASAPI  ③ Windows DirectSound  ④ MME（兜底）
            # 逐个试开，第一个成功即用；全部失败才回退系统默认。用户无需关心这些细节。
            _cands = []
            try:
                _all = []
                for _i in range(pa.get_device_count()):
                    try:
                        _d = pa.get_device_info_by_index(_i)
                    except Exception:  # noqa: BLE001
                        continue
                    if int((_d or {}).get("maxInputChannels") or 0) <= 0:
                        continue
                    if str((_d or {}).get("name") or "").strip().lower() != name_want:
                        continue
                    _ha = (_d or {}).get("hostApi")
                    try:
                        _an = str(pa.get_host_api_info_by_index(int(_ha)).get("name") or "")
                    except Exception:  # noqa: BLE001
                        _an = ""
                    _all.append((_i, _ha, _an))
                # ① 记忆优先
                try:
                    from ..llm.cloud_omni_realtime import load_verified_mics, _verified_key
                    _mem = load_verified_mics() or {}
                    _want_api = _mem.get(_verified_key(name_want))
                except Exception:  # noqa: BLE001
                    _want_api = None
                if _want_api is not None:
                    for _t in _all:
                        if int(_t[1]) == int(_want_api):
                            _cands.append(_t)
                            break
                # ②③④ 按 host 名优先级补全
                def _prio(an):
                    a = str(an or "").lower()
                    if "wasapi" in a:
                        return 0
                    if "directsound" in a:
                        return 1
                    if "mme" in a:
                        return 2
                    return 3
                for _t in sorted(_all, key=lambda x: _prio(x[2])):
                    if _t not in _cands:
                        _cands.append(_t)
                if api_want:   # 用户/配置显式指定了 host → 只留它
                    _cands = [c for c in _cands if str(c[2]).strip().lower() == api_want] or _cands
            except Exception:  # noqa: BLE001
                _cands = []
            _last_err = ""
            for _ci, _cha, _can in (_cands or [(idx, None, "")]):
                for _try in range(2):        # 每路试 2 次（设备刚被占用时第 2 次常成功）
                    try:
                        kw["input_device_index"] = _ci
                        stream = pa.open(**kw)
                        idx = _ci
                        _last_err = ""
                        break
                    except Exception as e_o:  # noqa: BLE001
                        _last_err = "%s: %s" % (type(e_o).__name__, str(e_o)[:150])
                        stream = None
                        time.sleep(0.35)
                if stream is not None:
                    if _can:
                        box["note"] = "使用 %s（自动挑选）" % _can
                    break
            if stream is None:
                box["err"] = _last_err or "打开失败"
                return
            try:
                stream.start_stream()
            except Exception:  # noqa: BLE001
                pass
            try:
                _si = pa.get_stream_info(stream)
                _ridx = getattr(_si, "input_device_index", idx)
            except Exception:  # noqa: BLE001
                _ridx = idx
            try:
                _mi = pa.get_device_info_by_index(_ridx) if _ridx is not None else {}
            except Exception:  # noqa: BLE001
                _mi = {}
            try:
                _ha2 = (_mi or {}).get("hostApi")
                _an2 = str(pa.get_host_api_info_by_index(int(_ha2)).get("name") or "") \
                    if _ha2 is not None else ""
            except Exception:  # noqa: BLE001
                _an2 = ""
            box["used"] = {"index": _ridx, "name": (_mi or {}).get("name", "系统默认"),
                           "hostApi": _ha2, "hostApiName": _an2}
            box["err"] = ""
            # 采集期间：只要代次没变就一直挂着（数据由回调写入 box）
            while int(getattr(self, "_dev_mic_gen", 0)) == int(gen):
                time.sleep(0.1)
        except Exception as e:  # noqa: BLE001
            box["err"] = "%s: %s" % (type(e).__name__, str(e)[:150])
        finally:
            try:
                if stream is not None:
                    stream.stop_stream()
                    stream.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                if pa is not None:
                    pa.terminate()
            except Exception:  # noqa: BLE001
                pass


    def dev_play_tone(self, data: dict = None) -> dict:
        """在指定输出设备上播放测试音（走应用自己的 _AudioPlayer，与双工播放同源）。

        立即返回，播放交给后台线程（此前在桥接里 sleep 会堵死 pywebview →
        "Connection to remote host was lost"）。复用单个 player，避免反复建流。
        """
        d = data or {}
        dev = str(d.get("device") or "default")
        freq = float(d.get("freq") or 660.0)
        seconds = float(d.get("seconds") or 1.0)
        try:
            import math as _m
            import numpy as _np
            import sounddevice as _sd
            from ..llm.cloud_omni_realtime import _AudioPlayer
            rate = 24000
            t = _np.linspace(0, seconds, int(rate * seconds), endpoint=False)
            pcm = (_np.sin(2 * _m.pi * freq * t) * 11000).astype(_np.int16).tobytes()
            player = _AudioPlayer(rate=rate, device=dev)
            idx = player._resolve_idx_sd(_sd)
            info = {}
            try:
                if idx is not None:
                    _dd = _sd.query_devices(idx)
                    _apis = {}
                    for _a, _i in enumerate(_sd.query_hostapis()):
                        _apis[_a] = str((_i or {}).get("name") or "")
                    info = {"index": idx, "name": (_dd or {}).get("name"),
                            "hostapi": _apis.get(int((_dd or {}).get("hostapi", -1) or -1), "")}
            except Exception:  # noqa: BLE001
                pass

            def _bg_play():
                try:
                    player.play(pcm)
                    time.sleep(seconds + 0.35)
                except Exception:  # noqa: BLE001
                    logger.debug("开发者页测试音后台播放异常", exc_info=True)
                finally:
                    try:
                        player.stop()
                    except Exception:  # noqa: BLE001
                        pass
            threading.Thread(target=_bg_play, daemon=True).start()
            _act = dict(getattr(player, "_actual", {}) or {})
            _hint = ""
            if _act.get("fellback"):
                _hint = ("所选设备打不开，已回退系统默认设备播放——"
                         "若你听到的是别的设备，请检查该设备是否被系统禁用。")
            elif str(dev).lower() not in ("default", "", "none"):
                # 打开成功 ≠ 一定出声：端点在系统里被静音/音量 0/物理口未接时，
                # 软件侧无法感知，只能明确提示用户去核对。
                _hint = ("已从该设备播放测试音。**若你完全没听到**：请检查系统「声音设置」里"
                         "这个设备是否被静音/音量为 0，或它是否真的接在你在听的音箱/耳机上；"
                         "也可以改用「系统默认设备」再试。")
            return {"success": True,
                    "device_used": info or {"index": None, "name": "系统默认"},
                    "requested": dev, "freq": freq, "seconds": seconds,
                    "backend": getattr(player, "_backend", ""), "playing": True,
                    "actual": _act, "hint": _hint}
        except Exception as e:  # noqa: BLE001
            logger.exception("开发者页·测试音播放失败")
            return {"success": False, "requested": dev,
                    "error": "%s: %s" % (type(e).__name__, str(e)[:200])}


    def test_mic_device(self, data: dict = None) -> dict:
        """后台录音检测麦克风是否可用，返回 RMS/Peak + 电平等级。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        d = data or {}
        dev = d.get("device", "default")
        seconds = float(d.get("seconds") or 2.0)
        box: dict = {}

        def _run():
            try:
                import sounddevice as sd
                import numpy as np
                idx = None if dev in ("default", None, "") else int(dev)
                buf = []

                def _cb(indata, frames, t, status):
                    try:
                        buf.append(float(np.abs(indata).mean()))
                    except Exception:  # noqa: BLE001
                        pass

                with sd.InputStream(device=idx, channels=1, samplerate=16000, callback=_cb):
                    import time as _t
                    _t.sleep(seconds)
                arr = np.array(buf) if buf else np.array([0.0])
                rms = float(np.sqrt(np.mean(np.square(arr))))
                box["v"] = rms
            except Exception as e:  # noqa: BLE001
                box["err"] = str(e)

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout=seconds + 5)
        if "err" in box:
            return {"success": True, "ok": False, "level": "error", "rms": 0.0,
                    "peak": 0.0, "error": box["err"]}
        rms = box.get("v", 0.0)
        level = "silent" if rms < 0.005 else ("low" if rms < 0.03 else "ok")
        return {"success": True, "ok": (rms >= 0.01), "level": level,
                "rms": round(rms, 4), "peak": round(rms, 4), "error": ""}

    def set_mic_device(self, data: dict = None) -> dict:
        """写入麦克风设备 + 开启 mic_enabled（绕过 set_mic_listening 的 asr 依赖，修 B2）。

        【2026-10-17 修复·麦克风"选了但收不到声音"】
        实测：同一只麦克风在 PortAudio 里有 3 条记录（MME / DirectSound / WASAPI）：
          · MME 那条 peak=0.00002 —— 哑设备：能 open、能 read，但恒返回 0
          · DirectSound 那条 peak=0.30745 —— 真能用
          · WASAPI 那条 16k 直接 Invalid sample rate
        旧实现只把设备名写进配置就返回成功，**完全不验证能否拾音**
        → 用户点到哑设备 → 通话里电平永远 0 → "麦克风好的但无效"。
        现在：写入前**实探一次**；若静音，自动在同名设备里按声学判据择优，
        并把"实际用哪条"回给前端（用户能看到已自动切换）。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        d = data or {}
        dev = d.get("device", "default")
        enabled = bool(d.get("enabled", True))
        picked = str(dev)
        auto_note = ""
        try:
            # 【2026-10-17 改进·异步验证，不阻塞、不误判】
            # 同步探测 0.5s 会误判（用户没说话就判静音）。改为后台任务：
            #   · 立即返回，前端先看到"正在检测"
            #   · 后台开 3 秒窗口，期间 UI 提示"请说句话"
            #   · 只在**持续静音**且存在其它可用通道时才自动切换
            _verify_async = True
# ---------- 写配置 ----------
            raw = self._cfg.raw
            raw.setdefault("proactive", {})["duplex_mic_device"] = str(picked)
            raw.setdefault("voice", {})["mic_enabled"] = enabled
            self._save_config()
            from ..config import _to_ns
            self._cfg.ns = _to_ns(raw)
            # 让运行中的双工立即用新设备（不必重开整通电话）
            try:
                if self._proactive is not None and hasattr(self._proactive, "set_duplex_mic"):
                    self._proactive.set_duplex_mic(str(picked))
            except Exception:  # noqa: BLE001
                pass
            if enabled and self._asr is not None:
                try:
                    self._asr.set_listening(True)
                except Exception:  # noqa: BLE001
                    pass
            # 启动后台验证（3 秒窗口，期间提示用户说话）
            try:
                import uuid as _uuid
                _vtask = _uuid.uuid4().hex[:12]
                self._mic_verify = {"id": _vtask, "state": "running",
                                    "device": str(picked), "requested": str(dev),
                                    "peak": 0.0, "note": "正在检测麦克风，请对着麦克风说一句话…",
                                    "switched_to": ""}
                threading.Thread(
                    target=self._mic_verify_worker,
                    args=(_vtask, str(picked), str(dev)),
                    daemon=True, name="mic_verify_" + _vtask,
                ).start()
            except Exception:  # noqa: BLE001
                pass
            return {"success": True, "device": str(picked),
                    "requested": str(dev), "verify_id": _vtask,
                    "note": "已保存。正在检测麦克风，请说一句话（约 3 秒）…",
                    "mic_enabled": enabled}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def _mic_verify_worker(self, task_id: str, picked: str, requested: str) -> None:
        """后台验证麦克风能否拾音（3 秒窗口），静音则自动择优。

        【2026-10-17 新增】为什么异步：同步探测 0.5 秒太短，用户没说话就判静音
        → 误切设备。这里开 3 秒窗口，期间前端提示"请说句话"，只在**持续静音**
        且存在其它可用通道时才切换。
        """
        def _set(**kw):
            v = getattr(self, "_mic_verify", None)
            if isinstance(v, dict) and v.get("id") == task_id:
                v.update(kw)

        try:
            import pyaudio as _pa_mod
            from ..llm.cloud_omni_realtime import _probe_input_alive, _resolve_device_index
            _pa = _pa_mod.PyAudio()
            try:
                _idx = _resolve_device_index(_pa, picked, "input")
                # 3 秒窗口：多轮短探测，取最大值（用户中途说话也能抓到）
                _best = 0.0
                for _round in range(6):
                    _pk = _probe_input_alive(_pa, _idx, rate=16000, tries=5) if _idx is not None else 0.0
                    _best = max(_best, _pk)
                    _set(peak=_best)
                    if _best >= 0.01:
                        break
                if _best >= 0.01:
                    _set(state="ok", note="麦克风工作正常（电平 %.3f）" % _best)
                    logger.info("麦克风验证通过：%r peak=%.4f", picked, _best)
                    return
                # 静音 → 在同名设备里择优
                _bare = picked.split("|")[0].strip().lower()
                _bi, _bp, _bn, _ba = None, 0.0, "", ""
                for _i in range(_pa.get_device_count()):
                    try:
                        _info = _pa.get_device_info_by_index(_i)
                    except Exception:  # noqa: BLE001
                        continue
                    if int(_info.get("maxInputChannels") or 0) <= 0:
                        continue
                    if str(_info.get("name") or "").strip().lower() != _bare:
                        continue
                    _pk = _probe_input_alive(_pa, _i, rate=16000, tries=5)
                    if _pk > _bp:
                        _bi, _bp = _i, _pk
                        _bn = str(_info.get("name") or "")
                        try:
                            _ai = int((_info or {}).get("hostApi", -1))
                            _ba = str((_pa.get_host_api_info_by_index(_ai) or {}).get("name") or "").strip()
                        except Exception:  # noqa: BLE001
                            _ba = ""
                if _bi is not None and _bp >= 0.01:
                    _new = (_bn + "|" + _ba) if _ba else _bn
                    try:
                        raw = self._cfg.raw
                        raw.setdefault("proactive", {})["duplex_mic_device"] = _new
                        self._save_config()
                    except Exception:  # noqa: BLE001
                        pass
                    _set(state="switched", switched_to=_new, peak=_bp,
                         note="所选麦克风无输入，已自动切换到可用通道：%s" % _new)
                    logger.warning("麦克风自愈：%r → %r（peak=%.4f）", picked, _new, _bp)
                else:
                    _set(state="silent",
                         note="检测不到声音。请检查：① Windows 设置→隐私→麦克风 已允许 "
                              "② 麦克风没被其它程序占用 ③ 输入音量不为 0")
            finally:
                try:
                    _pa.terminate()
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001
            _set(state="error", note="检测失败：%s" % str(e)[:80])

    def get_mic_verify_result(self, data: dict = None) -> dict:
        """取麦克风验证结果（前端轮询）。"""
        v = getattr(self, "_mic_verify", None)
        if not isinstance(v, dict):
            return {"success": True, "state": "idle", "note": ""}
        return {"success": True, **v}

    def apply_onboarding_voice(self, data: dict = None) -> dict:
        """应用首启声音选择（真实落盘 voice.mode/克隆引擎/参考音频/本地双工参考音频，修 B1/B3）。

        不再只写 onboarding_tts 死配置；按 engine_map 严格落盘，内置参考音频兜底。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            d = data or {}
            backend = (d.get("backend") or "").strip().lower()
            if backend not in ("mute", "edge", "local_qwen_tts", "mimo_tts",
                               "dashscope_cosyvoice", "custom_tts",
                               "minimax", "openai_tts"):
                return {"success": False, "error": f"未知声音后端: {backend}"}
            raw = self._cfg.raw
            raw.setdefault("voice", {})
            v = raw["voice"]
            active_id = self._cfg.active_character_id
            chars = raw.get("characters", []) or []
            target = None
            for c in chars:
                if c.get("id") == active_id:
                    target = c
                    break
            if target is None and chars:
                target = chars[0]
            result = {"success": True, "mode": None, "engine": None, "muted": False,
                      "reference_audio": None, "reference_source": "none",
                      "needs_download": False, "degraded": False, "message": ""}

            # D4 修复（2026-09-26）：引导页声音选择必须覆盖角色预置的 voice_profile。
            # get_active_voice_config() 优先返回 voice_profile，若不清掉，写进 char.voice 的
            # 选择（本地千问 / 换厂商等）会被档遮蔽、等于没选。用户显式选了声音即应让该选择生效。
            if isinstance(target, dict):
                target.pop("voice_profile", None)

            if backend == "mute":
                v["mode"] = "mute"
                v["muted"] = True
                v["mic_enabled"] = False
                _unload = getattr(self, "_unload_tts", None)
                if _unload is not None:
                    try:
                        _unload()
                    except Exception:  # noqa: BLE001
                        pass
                result.update(mode="mute", muted=True,
                              message="已设为静音（最省资源，随时可开）")
            elif backend == "edge":
                v["mode"] = "edge"
                v["muted"] = False
                if isinstance(target, dict):
                    vc = target.setdefault("voice", {})
                    vc["engine"] = "edge-tts"
                    vc["voice"] = "zh-CN-XiaoyiNeural"
                result.update(mode="edge", engine="edge-tts", muted=False,
                              message="已设为系统音色·微软小薇（零配置，立刻能说）")
            elif backend == "local_qwen_tts":
                ref = self._resolve_ref_audio(d)
                if ref[0] is None:
                    return {"success": False,
                            "error": "本地千问需要参考音频（选一个本机音频，或用内置达妮娅声）"}
                v["mode"] = "clone"
                v["muted"] = False
                v.setdefault("clone", {})["sub"] = "local"
                v["clone"]["local_engine"] = "qwen3tts"
                if isinstance(target, dict):
                    vc = target.setdefault("voice", {})
                    vc["engine"] = "qwen3tts"
                    vc["reference_audio"] = ref[0]
                    if d.get("reference_text"):
                        vc["reference_text"] = d["reference_text"]
                raw.setdefault("proactive", {})["duplex_local_omni_ref_audio"] = ref[0]
                result.update(mode="clone", engine="qwen3tts", muted=False,
                              reference_audio=ref[0], reference_source=ref[1],
                              needs_download=(not self._local_qwen_ready()),
                              message="已设为本地千问 Qwen3-TTS（达妮娅本音）")
            elif backend == "mimo_tts":
                api_key = (d.get("api_key") or "").strip()
                if not api_key:
                    return {"success": False, "error": "请先填 MiMo API Key"}
                ref = self._resolve_ref_audio(d)
                if not ref[0] or not os.path.isfile(ref[0]):
                    return {"success": False, "error": "MiMo 需要参考音频（选一个本机音频文件）"}
                v["mode"] = "clone"
                v["muted"] = False
                v.setdefault("clone", {})["sub"] = "cloud"
                v["clone"]["cloud"] = {"provider": "mimo_tts", "api_key": api_key}
                v["mimo_api_key"] = api_key
                if isinstance(target, dict):
                    vc = target.setdefault("voice", {})
                    vc["engine"] = "mimo_tts"
                    vc["reference_audio"] = ref[0]
                    if d.get("reference_text"):
                        vc["reference_text"] = d["reference_text"]
                raw.setdefault("proactive", {})["duplex_local_omni_ref_audio"] = ref[0]
                result.update(mode="clone", engine="mimo_tts", muted=False,
                              reference_audio=ref[0], reference_source=ref[1],
                              message="已设为小米 MiMo 云端克隆")
            elif backend == "dashscope_cosyvoice":
                api_key = (d.get("api_key") or "").strip()
                if not api_key:
                    return {"success": False, "error": "请先填阿里云百炼 API Key"}
                # 【2026-10-09 能力槽】百炼 Key 收口：引导选百炼音色时把全配置树的百炼 Key 统一为这一个
                # （key_unify 未开 / 不是合法百炼 Key 时 _unify_bailian_key 内部 no-op，不改变既有行为）
                _unify_bailian_key(raw, api_key, source="apply_onboarding_voice")
                model = (d.get("model") or "qwen-audio-3.0-tts-plus").strip()
                ref = self._resolve_ref_audio(d)
                voice_id = (d.get("voice_id") or "").strip()
                has_clone = bool(ref[0] and os.path.isfile(ref[0]) and d.get("reference_text"))
                v["mode"] = "clone"
                v["muted"] = False
                v.setdefault("clone", {})["sub"] = "cloud"
                v["clone"]["cloud"] = {"provider": "dashscope_cosyvoice",
                                       "api_key": api_key, "model_name": model}
                if isinstance(target, dict):
                    vc = target.setdefault("voice", {})
                    vc["engine"] = "dashscope_cosy"
                    vc["model"] = model
                    if voice_id:
                        # 优先用控制台已克隆音色（voice_id），不走即时克隆参考音频
                        vc["voice"] = voice_id
                    elif has_clone:
                        vc["reference_audio"] = ref[0]
                        if d.get("reference_text"):
                            vc["reference_text"] = d["reference_text"]
                    else:
                        vc["voice"] = "cosyvoice"  # 仅填 key → 系统音色兜底
                result.update(mode="clone", engine="dashscope_cosy", muted=False,
                              reference_audio=ref[0] if (has_clone and not voice_id) else None,
                              reference_source=("none" if voice_id else (ref[1] if has_clone else "none")),
                              message="已设为阿里云百炼 CosyVoice（%s）" % ("已克隆音色 " + voice_id if voice_id else "系统音色/克隆"))
            elif backend == "custom_tts":
                endpoint = (d.get("endpoint") or "").strip()
                model = (d.get("model") or "").strip()
                if not endpoint or not model:
                    return {"success": False, "error": "自定义接口需填 endpoint 与 model"}
                v["mode"] = "clone"
                v["muted"] = False
                v.setdefault("clone", {})["sub"] = "cloud"
                v["clone"]["cloud"] = {"provider": "openai_compatible",
                                       "api_url": endpoint,
                                       "api_key": (d.get("api_key") or "").strip(),
                                       "model_name": model}
                result.update(mode="clone", engine="edge-tts", muted=False,
                              degraded=True,
                              message="该 OpenAI 兼容接口作为预留，达妮娅先用系统音色说话（后续接入）")
            elif backend == "minimax":
                api_key = (d.get("api_key") or "").strip()
                group_id = (d.get("group_id") or "").strip()
                if not api_key or not group_id:
                    return {"success": False, "error": "MiniMax 需填 API Key 与 GroupId"}
                # MiniMax 的 model(模型版本) 与 voice_id(系统/复刻音色) 是两个独立字段，
                # 必须分开。前端选单里的 speech-02-hd/turbo 是 model，voice_id 用系统音色兜底。
                model = (d.get("model") or "speech-02-turbo").strip()
                voice_id = (d.get("voice_id") or "female-shaonv").strip()
                v["mode"] = "clone"
                v["muted"] = False
                v.setdefault("clone", {})["sub"] = "cloud"
                v["clone"]["cloud"] = {"provider": "minimax", "api_key": api_key,
                                       "group_id": group_id, "model_name": model,
                                       "voice_id": voice_id}
                if isinstance(target, dict):
                    vc = target.setdefault("voice", {})
                    vc["engine"] = "minimax"
                    vc["api_key"] = api_key
                    vc["group_id"] = group_id
                    vc["model"] = model
                    vc["voice_id"] = voice_id
                result.update(mode="clone", engine="minimax", muted=False,
                              message="已设为 MiniMax 云端语音（系统音色 %s，填 key 即用）" % voice_id)
            elif backend == "openai_tts":
                api_url = (d.get("api_url") or "").strip().rstrip("/")
                api_key = (d.get("api_key") or "").strip()
                model = (d.get("model") or "").strip()
                if not api_url or not model:
                    return {"success": False, "error": "OpenAI 兼容 TTS 需填接口地址与模型名"}
                v["mode"] = "clone"
                v["muted"] = False
                v.setdefault("clone", {})["sub"] = "cloud"
                v["clone"]["cloud"] = {"provider": "openai_tts", "api_url": api_url,
                                       "api_key": api_key, "model_name": model}
                if isinstance(target, dict):
                    vc = target.setdefault("voice", {})
                    vc["engine"] = "openai_tts"
                    vc["api_url"] = api_url
                    vc["api_key"] = api_key
                    vc["voice"] = model
                result.update(mode="clone", engine="openai_tts", muted=False,
                              message="已设为 OpenAI 兼容云端 TTS（填 key 即用）")

            self._save_config()
            from ..config import _to_ns
            self._cfg.ns = _to_ns(raw)
            if self._dialogue is not None and self._loop is not None and active_id:
                try:
                    self._run_coro(self._dialogue.switch_character(active_id))
                except Exception:  # noqa: BLE001
                    logger.warning("重切角色应用音色失败(配置已保存)")
            return result
        except Exception as e:  # noqa: BLE001
            logger.exception("应用首启声音失败")
            return {"success": False, "error": str(e)}

    def list_bailian_voices(self, api_key: str) -> dict:
        """查询阿里云百炼账号下全部声音复刻音色（voice_id），供前端一键回填。

        走 services/audio/tts/customization 的 list_voice 动作（纯 HTTP，无需 SDK/WorkspaceId）。
        覆盖 CosyVoice 与 Qwen-Audio 复刻体系下用 voice-enrollment 注册的音色。
        """
        try:
            import httpx
            key = (api_key or "").strip()
            if not key:
                return {"success": False, "error": "请先填百炼 API Key"}
            url = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization"
            headers = {"Authorization": f"Bearer {key}",
                       "Content-Type": "application/json"}
            body = {"model": "voice-enrollment",
                    "input": {"action": "list_voice", "prefix": None,
                              "page_index": 0, "page_size": 50}}
            with httpx.Client(timeout=30) as client:
                resp = client.post(url, headers=headers, json=body)
            if resp.status_code >= 400:
                detail = ""
                try:
                    detail = (resp.json().get("message") or resp.json().get("code")
                              or resp.text[:300])
                except Exception:
                    detail = resp.text[:300]
                return {"success": False,
                        "error": f"HTTP {resp.status_code}: {detail}"}
            try:
                data = resp.json()
                voices = (data.get("output") or {}).get("voice_list") or []
            except Exception:
                return {"success": False, "error": "百炼返回格式无法解析"}
            # 【2026-10-18 二次修正·过滤名单按真机实测放宽】
            # 旧名单只留 qwen3-tts-flash，实测会把用户账号里大量可用音色全过滤掉
            # （比如 cosyvoice-v3.5-flash/plus 走 SpeechSynthesizer 合成实测 200，
            # 音频正常返回）。改为：**默认全部列出**，只标注每个音色的可用性，
            # 由前端展示"推荐/可能不可用"徽标让用户自行选择——宁可多列不错杀。
            # 实测可用（2026-10-18，SpeechSynthesizer 端点 200）：
            _OK_MODELS = ("cosyvoice-v3.5-flash", "cosyvoice-v3.5-plus",
                          "qwen3-tts-flash")
            # 实测已下线（400 url error），列出但打"不可用"标记：
            _DEAD_MODELS = ("qwen-audio-3.0-tts-plus", "qwen-audio-3.0-tts-flash",
                            "qwen-audio-3.1-tts-flash", "qwen-audio-3.1-tts-plus",
                            "cosyvoice-v1", "cosyvoice-v2",
                            "cosyvoice-v3-flash", "cosyvoice-v3-plus")
            out = []
            skipped = 0
            for v in voices:
                tm = (v.get("target_model") or "").strip()
                vid = (v.get("voice_id") or "").strip()
                if not vid:
                    continue
                # target_model 为空时退回按 voice_id 前缀判断（老数据）
                _probe = (tm or vid).lower()
                _dead = any(k in _probe for k in _DEAD_MODELS)
                _ok = any(k in _probe for k in _OK_MODELS)
                if _dead:
                    skipped += 1
                    continue
                out.append({"voice_id": vid,
                            "status": v.get("status") or "",
                            "gmt_create": v.get("gmt_create") or "",
                            "usable": bool(_ok),
                            # 该音色所属的克隆模型（百炼权威字段）：合成必须用同一个 model，
                            # 否则报 400 [cosyvoice]Engine error [411]（2026-09-15 实测）。
                            "target_model": tm or self._model_from_voice_id(vid) or ""})
            return {"success": True, "voices": out, "skipped_models": skipped,
                    "message": (f"找到 {len(out)} 个克隆音色"
                                + (f"（已过滤 {skipped} 个已下线模型的音色）" if skipped else "")
                                if out
                                 else "该账号下没有声音复刻音色（可能在 Qwen-TTS 新体系，请到控制台复制）")}
        except Exception as e:  # noqa: BLE001
            logger.exception("查询百炼音色列表失败")
            return {"success": False, "error": str(e)}

    def list_cloud_voices(self, data: dict = None) -> dict:
        """按 context 列出角色卡「出声音色」可绑定的云端音色（克隆+预设），供前端混排下拉。

        context='tts'   ：本地双工出声用的云端 TTS（需 id+key+实名）。读 raw.voice.clone.cloud
                        的 provider+api_key；百炼查克隆+模型预设，MiniMax 系统预设(+克隆 list)，
                        OpenAI 内置预设。
        context='duplex'：云端双工 S2S（只需一个 key）。读 raw.cloud_omni；音色走 enrollment/官方预设。
        返回 {success, context, provider, need_key, voices:[{id,name,type,model}]}。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            d = data or {}
            ctx = (d.get("context") or "tts").strip().lower()
            raw = self._cfg.raw
            if ctx == "duplex":
                co = raw.get("cloud_omni", {}) or {}
                api_key = (co.get("api_key") or "").strip()
                provider = (co.get("provider") or "qwen-omni-realtime").strip()
                voices = []
                cur = (co.get("voice") or "").strip()
                if cur:
                    voices.append({"id": cur, "name": cur + "（当前克隆音色）",
                                    "type": "cloned", "model": ""})
                voices.append({"id": "", "name": "官方预设音色（Qwen-Omni 默认女声）",
                                "type": "preset", "model": ""})
                return {"success": True, "context": "duplex", "provider": provider,
                        "need_key": not bool(api_key), "voices": voices,
                        "note": "云端双工只需一个 Key；克隆音色在阿里云百炼控制台用 voice-enrollment 复刻后填 voice_id。"}
            # ---- tts context：支持多服务商并存（每个预设 TTS 服务商独立 Key） ----
            from ..models.cloud_tts import (
                _cloud_tts_providers as _ctp, fetch_provider_voices as _fpv,
                CLOUD_TTS_PROVIDERS as _CTP_META)
            providers_cfg = _ctp(raw)
            providers_out = []
            for pname, pcfg in providers_cfg.items():
                meta = _CTP_META.get(pname, {"label": pname})
                api_key = (pcfg.get("api_key") or "").strip()
                voices = _fpv(pname, pcfg, list_bailian_fn=self.list_bailian_voices)
                providers_out.append({
                    "provider": pname, "label": meta.get("label", pname),
                    "need_key": not bool(api_key), "voices": voices})
            if not providers_out:
                return {"success": True, "context": "tts", "providers": [],
                        "provider": "", "voices": [],
                        "note": "未检测到已绑定的云端 TTS（去「声音屏」绑一个 Key 后这里会列出可复刻/预设音色）"}
            # 老的「单 provider」前端兼容字段（取第一个已配 provider），避免旧前端/其它消费方断掉
            first = providers_out[0]
            return {"success": True, "context": "tts", "providers": providers_out,
                    "provider": first["provider"], "voices": first["voices"]}
        except Exception as e:  # noqa: BLE001
            logger.exception("列出云端音色失败")
            return {"success": False, "error": str(e)}

    def ensure_cloud_voice_profile(self, data: dict = None) -> dict:
        """为角色卡选中的「云端·TTS 音色」找/建一条云端 TTS 音色档，返回其 id。

        本地双工出声靠 voice_profile 优先（config.get_active_voice_config），故云端 TTS 音色必须
        落为一条 voice_profile（engine=厂商、voice_id=选中、复用全局 Key），本地双工才会用她声。
        前端只传 provider+voice_id+name；Key 在服务端从 raw.voice.clone.cloud 取，绝不回传前端。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            d = data or {}
            provider = (d.get("provider") or "").strip()
            voice_id = (d.get("voice_id") or "").strip()
            name = (d.get("name") or "").strip() or (voice_id or provider)
            from ..models.cloud_tts import _cloud_tts_providers as _ctp
            engine_map = {"dashscope_cosyvoice": "dashscope_cosy", "dashscope_cosy": "dashscope_cosy",
                          "bailian": "dashscope_cosy", "minimax": "minimax",
                          "openai_tts": "openai_tts", "fish_audio": "fish_audio"}
            engine = engine_map.get(provider, provider)
            if engine not in self._VOICE_PROFILE_ENGINES:
                return {"success": False, "error": f"未知云端 TTS 引擎: {provider}"}
            # 多服务商：从对应 provider 取 Key / model / group / api_url；
            # 兼容旧单 provider 写法（顶层 api_key / model_name ...）。
            providers_cfg = _ctp(self._cfg.raw)
            pcfg = providers_cfg.get(engine) or providers_cfg.get(provider) or {}
            api_key = (pcfg.get("api_key") or "").strip()
            if not api_key:
                # 旧格式兜底：直接读单层 api_key
                _legacy = (self._cfg.raw.get("voice", {}) or {}).get("clone", {}) or {}
                _legacy = _legacy.get("cloud", {}) or {}
                api_key = (str(_legacy.get("api_key") or "")).strip()
            if not api_key:
                return {"success": False, "error": "未绑定云端 TTS Key（去「声音屏」绑 Key 后重试）"}
            profiles = self._cfg.raw.setdefault("voice_profiles", [])
            # 已有同引擎+同 voice_id 的档直接复用
            if voice_id:
                for p in profiles:
                    if isinstance(p, dict) and p.get("engine") == engine and (p.get("voice_id") or "") == voice_id:
                        return {"success": True, "profile_id": p.get("id"), "engine": engine, "existing": True}
            pid = "cloud_" + engine + "_" + (voice_id or "preset")
            i = 1
            base = pid
            while any(isinstance(p, dict) and p.get("id") == pid for p in profiles):
                pid = base + "_" + str(i); i += 1
            prof = {"id": pid, "name": name, "engine": engine,
                    "api_key": api_key, "voice_id": voice_id,
                    "model": (pcfg.get("model_name") or ""), "group_id": (pcfg.get("group_id") or ""),
                    "api_url": (pcfg.get("api_url") or ""), "rate": "+0%", "pitch": "+0Hz", "volume": "+0%"}
            if engine == "dashscope_cosy" and not voice_id:
                prof["voice"] = "cosyvoice"  # 仅 key 无克隆 → 系统音色兜底
            profiles.append(prof)
            self._save_config()
            from ..config import _to_ns
            self._cfg.ns = _to_ns(self._cfg.raw)
            self._cfg.refresh_voice_profiles()
            return {"success": True, "profile_id": pid, "engine": engine, "existing": False}
        except Exception as e:  # noqa: BLE001
            logger.exception("确保云端音色档失败")
            return {"success": False, "error": str(e)}

    def pick_reference_audio(self) -> dict:
        """弹出系统文件框选参考音频，校验时长，复制到 data/voice_ref/ 持久（修 B3）。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        r = self.pick_audio_file()
        if not r.get("success"):
            return r
        path = r.get("path") or ""
        if not path:
            return {"success": False, "error": ""}
        if not os.path.isfile(path):
            return {"success": False, "error": "文件不存在"}
        dur = r.get("duration")
        if dur is not None and (dur < 1.5 or dur > 60):
            return {"success": False, "error": f"参考音频建议 3~30 秒清晰人声（当前 {dur:.1f} 秒）"}
        stored, copied = path, False
        try:
            dst_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "data", "voice_ref")
            os.makedirs(dst_dir, exist_ok=True)
            import shutil as _sh
            dst = os.path.join(dst_dir, os.path.basename(path))
            _sh.copyfile(path, dst)
            stored, copied = dst, True
        except Exception:  # noqa: BLE001
            stored, copied = path, False
        return {"success": True, "path": path, "stored_path": stored, "copied": copied,
                "name": os.path.basename(path), "duration_s": dur}

    # ===== 角色卡头图（2026-09-29） =====
    def _char_banner_dir(self) -> str:
        """角色头图落盘目录：<项目根>/data/char_banners（前端经 /local_img?path= 取图）。"""
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(root, "data", "char_banners")

    def pick_character_banner(self) -> dict:
        """弹出系统文件框选角色头图，复制到 data/char_banners/ 持久。

        为什么复制：用户随手选的图可能在桌面/下载目录，删掉或移动后角色卡就空了；
        复制到软件自己的数据目录，与参考音频（data/voice_ref）同一套持久化思路。
        """
        # 【2026-10-13 修复·加图片卡死】子进程弹框（原「主进程后台线程建 Tk」会把整个
        # 主进程拖死：日志全局冻结、进程却还活着）。根因与隔离收益见 src/ui/_file_dialog.py。
        from ._file_dialog import pick_path
        path, _derr = pick_path(
            "选择角色头图（建议横图 16:9，png/jpg/webp）",
            filetypes=[["图片文件", "*.png *.jpg *.jpeg *.webp *.bmp *.gif"],
                       ["所有文件", "*.*"]],
            timeout=180)
        if _derr:
            return {"success": False, "error": _derr}
        if not path:
            return {"success": False, "error": ""}   # 用户取消，不算错误
        if not os.path.isfile(path):
            return {"success": False, "error": "文件不存在"}
        try:
            dst_dir = self._char_banner_dir()
            os.makedirs(dst_dir, exist_ok=True)
            import shutil as _sh
            ext = os.path.splitext(path)[1].lower() or ".png"
            import time as _t
            # 用时间戳避免同名覆盖（用户可能给多个角色选同名图）
            dst = os.path.join(dst_dir, "%d%s" % (int(_t.time() * 1000), ext))
            _sh.copyfile(path, dst)
            stored = dst
        except Exception as e:  # noqa: BLE001
            # 复制失败仍可用原图（路径直存），只是后续可能被用户移走
            logger.warning("角色头图复制失败，改用原路径: %s", e)
            stored = path
        return {"success": True, "path": stored, "name": os.path.basename(stored)}

    # ===== 角色头像（2026-10-11） =====
    # 与「角色头图 banner」并列，但语义不同：banner 是角色卡顶部封面（横图），
    # 头像是 1:1 方图（角色卡头像 + 聊天页 AI 头像都用它）。
    # 为什么单列一个字段而不是复用 banner：banner 是横封面、头像是方图，
    # 比例/裁剪位置不同，混用会把横向封面裁成方图（人脸被切）。
    def _char_avatar_dir(self) -> str:
        """角色头像落盘目录：<项目根>/data/char_avatars（前端经 /local_img?path= 取图）。"""
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(root, "data", "char_avatars")

    def pick_character_avatar(self) -> dict:
        """弹出系统文件框选角色头像（1:1 方图），复制到 data/char_avatars/ 持久。

        与 pick_character_banner 同一套持久化思路（复制进软件数据目录，
        原图删掉/移动不影响显示）；只是目录与提示文案不同。
        """
        # 【2026-10-13 修复·加图片卡死】同 pick_character_banner：改子进程弹框。
        from ._file_dialog import pick_path
        path, _derr = pick_path(
            "选择角色头像（建议方图 1:1，png/jpg/webp）",
            filetypes=[["图片文件", "*.png *.jpg *.jpeg *.webp *.bmp *.gif"],
                       ["所有文件", "*.*"]],
            timeout=180)
        if _derr:
            return {"success": False, "error": _derr}
        if not path:
            return {"success": False, "error": ""}   # 用户取消，不算错误
        if not os.path.isfile(path):
            return {"success": False, "error": "文件不存在"}
        try:
            dst_dir = self._char_avatar_dir()
            os.makedirs(dst_dir, exist_ok=True)
            import shutil as _sh
            ext = os.path.splitext(path)[1].lower() or ".png"
            import time as _t
            dst = os.path.join(dst_dir, "%d%s" % (int(_t.time() * 1000), ext))
            _sh.copyfile(path, dst)
            stored = dst
        except Exception as e:  # noqa: BLE001
            logger.warning("角色头像复制失败，改用原路径: %s", e)
            stored = path
        return {"success": True, "path": stored, "name": os.path.basename(stored)}

    def set_reference_audio(self, data: dict = None) -> dict:
        """把参考音频/文本写进当前角色 voice，并同步本地双工参考音频。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        d = data or {}
        path = (d.get("path") or "").strip()
        if not path or not os.path.isfile(path):
            return {"success": False, "error": "参考音频路径无效"}
        try:
            raw = self._cfg.raw
            chars = raw.get("characters", []) or []
            cid = (d.get("character_id") or "").strip() or self._cfg.active_character_id
            target = None
            for c in chars:
                if c.get("id") == cid:
                    target = c
                    break
            if target is None and chars:
                target = chars[0]
            if isinstance(target, dict):
                vc = target.setdefault("voice", {})
                vc["reference_audio"] = path
                if d.get("text"):
                    vc["reference_text"] = d["text"]
            raw.setdefault("proactive", {})["duplex_local_omni_ref_audio"] = path
            self._save_config()
            from ..config import _to_ns
            self._cfg.ns = _to_ns(raw)
            return {"success": True, "character_id": cid, "reference_audio": path,
                    "reference_text": (d.get("text")
                                       or (target or {}).get("voice", {}).get("reference_text", ""))}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def get_model_bundle_status(self, data: dict = None) -> dict:
        """校验模型目录里整套模型（主模型 + 子模型）是否完整。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        d = data or {}
        quant = (d.get("quant_id") or "").strip() or "Q4_K_M"
        model_dir = (d.get("model_dir") or "").strip() or self._comni_model_dir() or ""
        try:
            from src.companion.model_catalog import verify_bundle
            return verify_bundle(model_dir, quant)
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def download_model_bundle(self, data: dict = None) -> dict:
        """触发整套模型下载（主模型 + 子模型），后台线程，轮询 get_model_download_status。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        d = data or {}
        quant = (d.get("quant_id") or "").strip() or "Q4_K_M"
        model_dir = (d.get("model_dir") or "").strip() or self._comni_model_dir() or ""
        if not model_dir:
            return {"success": False, "error": "未指定模型目录（请先选目录）"}
        try:
            from src.companion.model_catalog import download_bundle
            return download_bundle({"quant_id": quant, "model_dir": model_dir,
                                    "only_missing": d.get("only_missing", True)})
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def stop_model_download(self, task_id: str = "", delete_partial: bool = False) -> dict:
        """停止/取消量化包下载（下载器 UI「暂停 / 取消」）。

        delete_partial=False = 暂停（保留已下部分，下次从断点续传）
        delete_partial=True  = 取消（终止进程并清理未完成的临时文件）
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            from src.companion.model_catalog import stop_model_download as _stop
            return _stop(str(task_id or "").strip(), bool(delete_partial))
        except Exception as e:  # noqa: BLE001
            logger.exception("停止模型下载失败")
            return {"success": False, "error": str(e)}

    def delete_model_quant(self, data: dict = None) -> dict:
        """卸载（删除）已下载的量化包文件：{quant_id, model_dir?}。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        d = data or {}
        try:
            from src.companion.model_catalog import delete_model_quant as _del
            return _del(str(d.get("quant_id") or "").strip(), d.get("model_dir") or "")
        except Exception as e:  # noqa: BLE001
            logger.exception("删除模型量化包失败")
            return {"success": False, "error": str(e)}

    def model_dir_info(self, path: str = "") -> dict:
        """下载目录体检：剩余空间 + 各档是否已安装（给下载器做空间校验与"已安装"态）。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        if isinstance(path, dict):
            path = path.get("path") or ""
        try:
            from src.companion.model_catalog import model_dir_info as _info
            return _info(str(path or "").strip())
        except Exception as e:  # noqa: BLE001
            logger.exception("读取模型目录信息失败")
            return {"success": False, "error": str(e)}

    def get_model_download_status(self, task_id: str = "") -> dict:
        """轮询整套模型下载进度（代理 model_catalog.get_download_status）。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            from src.companion.model_catalog import get_download_status
            tid = (task_id or "").strip()
            if not tid:
                return {"success": False, "error": "缺少 task_id"}
            st = get_download_status(tid)
            # 统一字段名，方便前端 pollVision/pollFull 消费
            return {"success": True, **st}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def list_model_downloads(self, data: dict = None) -> dict:
        """下载总览：进行中的任务 + 断点残留（全局小窗口 / 下载器切档保留共用）。

        前端不带参数拿全局视图（进软件就有小窗口进度提示）；下载器可传
        {model_dir} 拿该目录的断点信息（切档/重进不丢进度）。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            from src.companion.model_catalog import list_download_tasks
            return list_download_tasks(data or {})
        except Exception as e:  # noqa: BLE001
            logger.exception("读取下载任务总览失败")
            return {"success": False, "error": str(e)}

    # ======== Comni 引擎自动获取（修「分发需自备 Comni」缺陷） ========
    # 首次跑「看得见/全感知」但本机没装 Comni 引擎时，自动下载官方安装包并静默安装。
    _COMNI_ENGINE_DEFAULT_URL = (
        "https://github.com/tc-mb/llama.cpp-omni/releases/latest/download/"
        "Comni-Setup-win64.exe"
    )

    def _comni_engine_target_dir(self) -> str:
        """引擎应安装到的目录：解析后的 duplex_local_omni_exe 所在目录，否则 <app>/Comni。"""
        pro = (self._cfg.raw.get("proactive", {}) or {}) if self._cfg else {}
        exe = pro.get("duplex_local_omni_exe") or ""
        if exe and os.path.isabs(exe):
            return os.path.dirname(os.path.normpath(exe))
        base = os.environ.get("DANIYA_COMNI_HOME") or (
            os.path.dirname(self._cfg.path) if self._cfg and self._cfg.path else os.getcwd())
        return os.path.normpath(os.path.join(base, "Comni"))

    def _comni_engine_ready(self) -> bool:
        """本地 Comni 引擎程序是否在位（exe 存在即视为就绪，_internal 由安装包保证）。"""
        pro = (self._cfg.raw.get("proactive", {}) or {}) if self._cfg else {}
        exe = pro.get("duplex_local_omni_exe") or ""
        return bool(exe) and os.path.isfile(exe)

    def get_comni_engine_status(self) -> dict:
        """报告本地 Comni 引擎就绪状态（供引导页模型屏展示）。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            pro = self._cfg.raw.get("proactive", {}) or {}
            loe = pro.get("local_omni_engine", {}) or {}
            exe = pro.get("duplex_local_omni_exe") or ""
            server_dir = loe.get("server_dir") or ""
            resources_dir = loe.get("comni_resources_dir") or ""
            exe_exists = bool(exe) and os.path.isfile(exe)
            internal_ok = bool(resources_dir) and os.path.isdir(
                os.path.join(resources_dir, "python-embed"))
            cfg_ok = bool(server_dir) and os.path.isfile(
                os.path.join(server_dir, "config.json"))
            ready = exe_exists and (internal_ok or cfg_ok)
            reason = ""
            if not exe_exists:
                reason = "未找到 Comni 引擎程序（%s）" % exe
            elif not (internal_ok or cfg_ok):
                reason = "引擎目录不完整（缺 _internal 或 server 配置）"
            return {"success": True, "ready": ready, "exe": exe,
                    "exe_exists": exe_exists, "server_dir": server_dir,
                    "resources_dir": resources_dir,
                    "model_dir": self._comni_model_dir() or "", "reason": reason}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def download_comni_engine(self, data: dict = None) -> dict:
        """触发 Comni 引擎自动下载+静默安装（后台线程，轮询 get_comni_engine_download_status）。

        仅 Windows。会把官方安装包装到 _comni_engine_target_dir()，装完写回绝对路径到 config。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        if sys.platform != "win32":
            return {"success": False,
                    "error": "自动安装引擎仅支持 Windows；请在 Linux/macOS 手动安装 Comni 后填好引擎路径"}
        st = getattr(self, "_comni_engine_dl", None) or {}
        if st.get("running"):
            return {"success": True, "started": True, "already_running": True}
        if self._comni_engine_ready():
            return {"success": True, "started": False, "already_ready": True}
        d = data or {}
        raw = self._cfg.raw
        pro = raw.setdefault("proactive", {})
        loe = pro.setdefault("local_omni_engine", {})
        target = self._comni_engine_target_dir()
        url = (loe.get("comni_engine_url") or "").strip() or self._COMNI_ENGINE_DEFAULT_URL
        self._comni_engine_dl = {"running": True, "phase": "download", "pct": 0.0,
                                 "speed_mbps": 0, "eta_s": None, "error": None,
                                 "done": False, "installed": False, "target": target}
        import threading
        t = threading.Thread(target=self._comni_engine_install_worker,
                             args=(url, target, loe, pro), daemon=True)
        t.start()
        return {"success": True, "started": True, "target": target, "url": url}

    def _comni_engine_install_worker(self, url: str, target: str,
                                     loe: dict, pro: dict) -> None:
        """后台下载官方 Comni 安装包 → 静默安装 → 写回绝对路径（仅 Windows）。"""
        try:
            import shutil as _sh
            import subprocess
            import tempfile
            import time

            os.makedirs(target, exist_ok=True)
            dest = os.path.join(tempfile.gettempdir(), "Comni-Setup-win64.exe")

            # 1) 下载安装包（带进度）
            self._comni_engine_dl["phase"] = "download"
            try:
                import httpx
                with httpx.stream("GET", url, follow_redirects=True, timeout=600) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("content-length", 0) or 0)
                    downloaded = 0
                    t0 = time.time()
                    with open(dest, "wb") as f:
                        for chunk in resp.iter_bytes(chunk_size=1 << 20):
                            f.write(chunk)
                            downloaded += len(chunk)
                            now = time.time()
                            dt = max(now - t0, 0.001)
                            self._comni_engine_dl["pct"] = (
                                (downloaded / total * 100) if total else 0.0)
                            self._comni_engine_dl["speed_mbps"] = round(
                                downloaded / 1e6 / dt, 1)
            except Exception as e:  # noqa: BLE001
                # 回退：httpx 不可用时用 urllib
                import urllib.request
                urllib.request.urlretrieve(url, dest)

            # 2) 静默安装（InnoSetup：/VERYSILENT /DIR=目标）
            self._comni_engine_dl["phase"] = "install"
            self._comni_engine_dl["pct"] = 0.0
            cmd = [dest, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                   '/DIR="%s"' % target]
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            try:
                proc.wait(timeout=900)
            except subprocess.TimeoutExpired:
                proc.kill()

            # 3) 探测安装位置
            installed_exe = os.path.join(target, "Comni.exe")
            if not os.path.isfile(installed_exe):
                for cand in (
                    os.path.expandvars(r"%LOCALAPPDATA%\Comni\Comni.exe"),
                    os.path.expandvars(r"%ProgramFiles%\Comni\Comni.exe"),
                    os.path.expandvars(r"%ProgramFiles(x86)%\Comni\Comni.exe"),
                ):
                    if os.path.isfile(cand):
                        installed_exe, target = cand, os.path.dirname(cand)
                        break
            if not os.path.isfile(installed_exe):
                raise RuntimeError(
                    "安装后未找到 Comni.exe，请手动安装或把 Comni 解压到 %s" % target)

            # 4) 写回绝对路径到 config
            resources_dir = os.path.join(target, "_internal", "resources")
            server_dir = os.path.join(resources_dir, "apps", "server")
            python_embed = os.path.join(resources_dir, "python-embed", "python.exe")
            loe["server_dir"] = server_dir
            loe["comni_resources_dir"] = resources_dir
            loe["python_embed"] = python_embed
            pro["duplex_local_omni_exe"] = installed_exe
            if not loe.get("comni_engine_url"):
                loe["comni_engine_url"] = url
            self._save_config()
            from ..config import _to_ns
            self._cfg.ns = _to_ns(self._cfg.raw)

            self._comni_engine_dl["installed"] = True
            self._comni_engine_dl["phase"] = "done"
            self._comni_engine_dl["pct"] = 100.0
            self._comni_engine_dl["running"] = False
            self._comni_engine_dl["done"] = True
        except Exception as e:  # noqa: BLE001
            self._comni_engine_dl["error"] = str(e)
            self._comni_engine_dl["running"] = False
            self._comni_engine_dl["phase"] = "error"
            logger.exception("Comni 引擎下载安装失败")

    def get_comni_engine_download_status(self) -> dict:
        """轮询引擎下载/安装进度。"""
        st = getattr(self, "_comni_engine_dl", None) or {}
        return {"success": True, "running": st.get("running", False),
                "phase": st.get("phase", ""), "pct": st.get("pct", 0.0),
                "speed_mbps": st.get("speed_mbps", 0),
                "eta_s": st.get("eta_s"), "error": st.get("error"),
                "done": st.get("done", False), "installed": st.get("installed", False),
                "target": st.get("target", "")}

    def validate_onboarding(self, payload: dict = None) -> dict:
        """首启完成前的最终复核：任何 fail 拦截进入主页。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        p = payload or {}
        try:
            raw = self._cfg.raw
            checks = []
            # 模式 + 期望功能集：以本次引导所选（payload）为准，而非尚未落盘的 raw。
            # 否则首启时 raw.companion_features 仍为空 → features 检查恒 fail → 永远卡在体检页进不去主页。
            mode = (p.get("mode") or "").strip()
            # 2026-09-25 补引导页新方向名：旧表只有 纯聊天/聊天+看得见/全感知伙伴/自定义配置，
            # 「本机免费/云端付费」落到 token=None → save 被记成 skip（本地模型永不挂载）、
            # validate 的 features 恒 fail（完成按钮看似点不动）。
            token = {"纯聊天": "chat", "聊天+看得见": "vision",
                     "全感知伙伴": "full", "自定义配置": "custom",
                     "本机免费": "local_free", "云端付费": "cloud_pay",
                     "混合推荐": "mixed"}.get(mode)
            skip = bool(p.get("skip"))
            if token == "custom":
                feats = p.get("features") or raw.get("companion_features") or []
            elif token in ("chat", "vision", "full"):
                feats = self.PRESET_FEATURES.get(token) or []
            elif token in ("local_free", "cloud_pay", "mixed"):
                # 2026-09-25 二修：上一轮只补了「本机免费/云端付费」的名称映射，
                # 却漏了这里的功能集分支 —— 两个新档掉进 else 去读尚未落盘的
                # raw.companion_features（首启时为空）→ features 恒 fail → 
                # 本地/云端都卡在体检页进不去主页（用户实测）。
                feats = self.PRESET_FEATURES.get(token) or []
            elif skip:
                feats = self.PRESET_FEATURES.get("chat") or []
            else:
                feats = raw.get("companion_features") or []
            fids = {f["id"] for f in feats if isinstance(f, dict)}
            local_on = bool(fids & {"local_duplex", "local_vision"})

            # 对话引擎：仅「纯聊天」强制要求；其余模式可选（引导页允许跳过）。
            llm = p.get("llm")
            llm_ok = bool(isinstance(llm, dict) and llm.get("provider")) or bool(
                (raw.get("llm") or {}).get("provider")) or bool(
                (raw.get("llm_settings") or {}).get("provider"))
            if token != "chat":
                llm_ok = True  # 非纯聊天：对话引擎可选，不拦
            checks.append({"id": "llm", "label": "对话引擎",
                           "status": ("ok" if llm_ok else "fail"),
                           "detail": ("已配置" if llm_ok else "未配置对话引擎（纯聊天需 API Key）"),
                           "fix_screen": "screenChatApi"})

            if local_on:
                comp = False
                try:
                    md = self._comni_model_dir() or ""
                    if md:
                        from src.companion.model_catalog import verify_bundle
                        quant = (raw.get("proactive", {}) or {}).get(
                            "duplex_local_omni_quant") or "Q4_K_M"
                        comp = bool(verify_bundle(md, quant).get("complete"))
                except Exception:  # noqa: BLE001
                    comp = False
                checks.append({"id": "model", "label": "本地模型",
                               "status": ("ok" if comp else "warn"),
                               "detail": ("模型已就绪" if comp else "模型未下载完（进主页后后台自动下载）"),
                               "fix_screen": "screenModelDl"})
                ok_layout = False
                try:
                    ok_layout = bool(self._comni_cfg_path()) and self._comni_engine_ready()
                except Exception:  # noqa: BLE001
                    ok_layout = False
                checks.append({"id": "layout", "label": "引擎组件",
                               "status": ("ok" if ok_layout else "warn"),
                               "detail": ("Comni 引擎已就绪" if ok_layout
                                          else "未找到本地引擎组件（进主页后可在设置里安装）"),
                               "fix_screen": "screenModelDl"})
            else:
                checks.append({"id": "model", "label": "本地模型", "status": "ok",
                               "detail": "无需本地模型", "fix_screen": ""})

            # 声音 / 麦克风：以本次引导所选（payload）为准，未配只提示不拦。
            _v = p.get("voice")
            voice_ok = bool(isinstance(_v, dict) and _v.get("backend")
                            and _v.get("backend") != "mute")
            vmode = (raw.get("voice", {}) or {})
            vmode_mode = (vmode.get("mode") or "").strip().lower()
            if not voice_ok and vmode_mode and vmode_mode != "mute":
                voice_ok = True
            muted = bool(vmode.get("muted", mode == "mute"))
            checks.append({"id": "voice", "label": "声音输出",
                           "status": ("ok" if (voice_ok and not muted) else "warn"),
                           "detail": ("声音已配置" if (voice_ok and not muted) else "未配置声音（将静音，可在设置里开启）"),
                           "fix_screen": "screenVoice"})
            _mic = p.get("mic")
            mic_on = bool(isinstance(_mic, dict) and _mic.get("enabled")) or bool(
                (raw.get("proactive", {}) or {}).get("duplex_mic_device"))
            checks.append({"id": "mic", "label": "麦克风",
                           "status": ("ok" if mic_on else "warn"),
                           "detail": ("麦克风已开启" if mic_on else "麦克风未开启（语音输入不可用，可在设置里开启）"),
                           "fix_screen": "screenMic"})
            has_feat = bool(fids)
            checks.append({"id": "features", "label": "功能集",
                           "status": ("ok" if has_feat else "fail"),
                           "detail": ("已配置功能集" if has_feat else "未选择任何模式/功能"),
                           "fix_screen": "screenMode"})
            try:
                from src.companion.model_catalog import QUANTS as _Q
                # nvidia-smi 可能被繁忙的 GPU 拖慢（最长 10s），绝不在 GUI 线程同步等：
                # 丢到线程里取，2.5s 内拿不到就跳过「显存余量」提示（警告项，不拦进入主页）。
                import threading as _th
                _vram_box: dict = {}
                def _vram_probe():
                    try:
                        from src.companion.model_catalog import detect_total_vram_gb
                        _vram_box["v"] = detect_total_vram_gb()
                    except Exception:  # noqa: BLE001
                        _vram_box["v"] = None
                _vt = _th.Thread(target=_vram_probe, daemon=True)
                _vt.start()
                _vt.join(timeout=2.5)
                vram = _vram_box.get("v")
                if "local_duplex" in fids and vram:
                    q = (raw.get("proactive", {}) or {}).get(
                        "duplex_local_omni_quant") or "Q4_K_M"
                    used = next((x["vram_gb"] for x in _Q if x["id"] == q), None)
                    if used and used > vram:
                        checks.append({"id": "vram", "label": "显卡余量", "status": "warn",
                                       "detail": f"所选档需约 {used}GB，显存约 {vram}GB（可能卡顿）",
                                       "fix_screen": "screenFullQuant"})
            except Exception:  # noqa: BLE001
                pass
            # 仅「纯聊天无引擎」「未选任何模式」视为硬失败；其余（模型/引擎/声音/麦克风/显存）
            # 一律 warning，与体检页「有 ⚠ 不拦你」语义一致，确保用户一定能进主页。
            has_fail = any(c["status"] == "fail" for c in checks)
            return {"success": True, "ok": (not has_fail), "checks": checks}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def save_custom_config(self, data: dict = None) -> dict:
        """配置器保存：归一功能集 + 真实显存预算 + 应用（修 G2）。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        d = data or {}
        try:
            feats = d.get("features") or []
            norm = []
            for f in feats:
                if isinstance(f, dict) and f.get("id") in self.COMPANION_FEATURE_DEFS:
                    fid = f["id"]
                    def_ = self.COMPANION_FEATURE_DEFS[fid]
                    norm.append({"id": fid, "label": def_.get("label"),
                                 "vram": f.get("vram", def_.get("vram")),
                                 "opts": (f.get("opts") or {}) if isinstance(f.get("opts"), dict) else {}})
            if not norm:
                return {"success": False, "error": "未选择任何有效功能"}
            budget = float(d.get("budget_gb") or 0) or None
            try:
                from src.companion.model_catalog import detect_total_vram_gb
                vram = detect_total_vram_gb()
                if vram:
                    budget = vram
            except Exception:  # noqa: BLE001
                pass
            used = sum((f.get("vram") or 0) for f in norm)
            over = bool(budget) and used > budget
            warnings = []
            if over:
                warnings.append(f"已超预算（{used:.1f}GB > 可用 {budget:.1f}GB），运行时可能爆显存")
            if d.get("apply"):
                raw = self._cfg.raw
                raw["companion_mode"] = "custom"
                raw["companion_features"] = norm
                self._features_to_engine_config(norm, raw)
                self._kickoff_custom_model_prep(norm, raw.get("proactive", {}))
                self._save_config()
                from ..config import _to_ns
                self._cfg.ns = _to_ns(raw)
            return {"success": True, "features": norm, "vram_used": round(used, 1),
                    "budget_gb": (budget or 0), "over_limit": over,
                    "warnings": warnings, "applied": bool(d.get("apply"))}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def download_model_quant(self, data: dict) -> dict:
        """触发指定量化包下载（后台线程执行，不阻塞 UI）。

        data: {"quant_id": "Q4_K_M"|"Q6_K"|"Q8_0"|"F16", "model_dir"?: "..."}
        立即返回任务 id + 可直接复制的 modelscope 下载命令；结果用
        get_model_download_status(task_id) 轮询。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            from src.companion.model_catalog import download_model_quant as _dl
            return _dl(data or {})
        except Exception as e:
            logger.exception("下载模型量化包失败")
            return {"success": False, "error": str(e)}

    # ======== 本地克隆引擎模型（按引擎检测 / 魔塔下载） ========
    def _clone_engine_spec(self, engine: str) -> dict:
        """返回某本地克隆引擎的模型规格（缓存目录、魔塔 repo、检测 glob、链接）。

        缓存根目录与各 server 的 MODEL_CACHE 保持一致（env 可覆盖）；
        检测 glob 与 qwen3tts_server / voxcpm_server / cosyvoice_server 的 _find_model_dir 对齐，
        保证「下载落盘布局」==「server 读取布局」。
        """
        PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        base = {
            "qwen3tts": {
                "label": "千问3-TTS (Qwen3-TTS-12Hz-0.6B-Base)",
                "repo": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
                "modelscope_url": "https://modelscope.cn/models/Qwen/Qwen3-TTS-12Hz-0.6B-Base",
                "cache_env": "QWEN3_MODEL_CACHE",
                "cache_default": os.path.join(PROJECT_DIR, "qwen3_tts_models"),
                "patterns": [
                    os.path.join("models", "Qwen--Qwen3-TTS-12Hz-0.6B-Base", "snapshots", "*"),
                    os.path.join("models--Qwen--Qwen3-TTS-12Hz-0.6B-Base", "snapshots", "*"),
                ],
            },
            "voxcpm": {
                "label": "VoxCPM2",
                "repo": "openbmb/VoxCPM2",
                "modelscope_url": "https://modelscope.cn/models/openbmb/VoxCPM2",
                "cache_env": "VOXCPM_MODEL_CACHE",
                "cache_default": os.path.join(PROJECT_DIR, "voxcpm2_models"),
                "patterns": [
                    os.path.join("models--openbmb--VoxCPM2", "snapshots", "*"),
                ],
            },
            "cosyvoice": {
                "label": "CosyVoice2-0.5B",
                "repo": "iic/CosyVoice2-0.5B",
                "modelscope_url": "https://modelscope.cn/models/iic/CosyVoice2-0.5B",
                "cache_env": "COSYVOICE_MODEL_DIR",
                "cache_default": os.path.join(PROJECT_DIR, "cosyvoice_models"),
                "patterns": [
                    os.path.join("models--iic--CosyVoice2-0.5B", "snapshots", "*"),
                    os.path.join("models", "iic--CosyVoice2-0.5B", "snapshots", "*"),
                ],
            },
            "mimo_tts": {
                "label": "MiMo-TTS (小米云端克隆)",
                "repo": "云端 API（需 mimo_api_key，限时免费）",
                "modelscope_url": None,
                "cache_env": "MIMO_MODEL_CACHE",
                "cache_default": os.path.join(PROJECT_DIR, "mimo_tts_models"),
                "patterns": [],
                "cloud": True,
            },
            "gpt_sovits": {
                "label": "GPT-SoVITS",
                "repo": "外部服务 http://127.0.0.1:9880",
                "modelscope_url": None,
                "cache_env": "GPT_SOVITS_CACHE",
                "cache_default": os.path.join(PROJECT_DIR, "gpt_sovits_models"),
                "patterns": [],
                "external": True,
            },
        }
        spec = base.get(engine) or base["qwen3tts"]
        # 用户认领的自定义模型目录优先：voice.tts_model_dirs.<engine>
        # 场景：模型早就下在别的盘/别的目录，自动扫描默认目录扫不到时，
        # 由用户在引导页「电脑里已有模型」卡片手动指认，避免重复下载数 GB。
        custom = ""
        try:
            custom = str((((self._cfg.raw.get("voice", {}) or {}).get("tts_model_dirs") or {})
                          .get(engine) or "")).strip()
        except Exception:  # noqa: BLE001
            custom = ""
        if custom:
            spec = dict(spec)
            spec["cache_default"] = custom
            spec["custom_dir"] = True
        return spec

    def _clone_model_dir(self, engine: str):
        """返回 (cache_root, resolved_model_dir_or_None)。按 glob 探测真实 snapshot 目录。
        云端/外部引擎（MiMo-TTS / GPT-SoVITS）无本地下载依赖：有凭证/连通即就绪。"""
        spec = self._clone_engine_spec(engine)
        if spec.get("cloud"):
            # 云端克隆（MiMo-TTS）：配置 mimo_api_key 即视为就绪，无需本地下载。
            _clone = ((self._cfg.raw.get("voice", {}) or {}).get("clone", {}) or {})
            _key = (_clone.get("mimo_api_key")
                    or (self._cfg.raw.get("voice", {}) or {}).get("mimo_api_key") or "").strip()
            return None, ("cloud" if _key else None)
        if spec.get("external"):
            # 外部服务（GPT-SoVITS）：探测 9880 连通即就绪。
            try:
                ok, _ = self._probe_url("http://127.0.0.1:9880")
            except Exception:  # noqa: BLE001
                ok = False
            return None, ("external" if ok else None)
        cache_root = os.environ.get(spec["cache_env"]) or spec["cache_default"]
        for pat in spec["patterns"]:
            snaps = sorted(glob.glob(os.path.join(cache_root, pat)))
            if snaps:
                return cache_root, snaps[0]
        return cache_root, None

    def check_local_models(self) -> dict:
        """检测当前本地克隆引擎的模型是否已下载（按引擎动态切换，默认 qwen3tts）。

        不再检测 MiniCPM 双工模型——那是双工引擎(local_omni)的模型，与语音克隆 TTS 无关。
        读取 cfg.voice.clone_local_engine（默认 qwen3tts），扫对应缓存目录 snapshots 判定就绪。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            engine = ((self._cfg.raw.get("voice", {}) or {}).get("clone_local_engine")
                      or "qwen3tts")
            spec = self._clone_engine_spec(engine)
            cache_root, model_dir = self._clone_model_dir(engine)
            ready = model_dir is not None
            return {
                "success": True,
                "engine": engine,
                "engine_label": spec["label"],
                "repo": spec["repo"],
                "modelscope_url": spec["modelscope_url"],
                "cache_root": cache_root,
                "model_dir": model_dir,
                "ready": ready,
            }
        except Exception as e:  # noqa: BLE001
            logger.exception("检测本地克隆模型失败")
            return {"success": False, "error": str(e)}

    # ---- 下载（魔塔后台线程，状态轮询） ----
    _clone_dl_state: dict = {}
    _clone_dl_lock = threading.Lock()

    def download_voice_model(self, data: dict) -> dict:
        """按当前克隆引擎从魔塔(ModelScope)下载模型（后台线程，不阻塞 UI）。

        data: {"engine"?: "qwen3tts"|"voxcpm"|"cosyvoice"}
        不传 engine 时取 cfg.voice.clone_local_engine（默认 qwen3tts）。
        走国内魔塔源；立即返回任务 id + 可直接复制的 modelscope 下载命令；
        进度用 get_voice_download_status(task_id) 轮询。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            engine = (str(data.get("engine") or "").strip().lower()
                      or (self._cfg.raw.get("voice", {}) or {}).get("clone_local_engine")
                      or "qwen3tts")
            if engine not in ("qwen3tts", "voxcpm", "cosyvoice"):
                return {"success": False, "error": f"未知本地克隆引擎: {engine}"}
            spec = self._clone_engine_spec(engine)
            cache_root, model_dir = self._clone_model_dir(engine)
            if model_dir:
                return {
                    "success": True, "task_id": None, "already": True,
                    "engine": engine, "engine_label": spec["label"],
                    "model_dir": model_dir, "repo": spec["repo"],
                    "modelscope_url": spec["modelscope_url"],
                    "message": f"{spec['label']} 已就绪，无需下载。",
                }
            import time as _t
            task_id = f"clone_{engine}_{int(_t.time())}"
            t = threading.Thread(
                target=self._run_clone_download, args=(engine, cache_root, task_id),
                daemon=True,
            )
            t.start()
            return {
                "success": True, "task_id": task_id, "already": False,
                "engine": engine, "engine_label": spec["label"],
                "cache_root": cache_root, "repo": spec["repo"],
                "modelscope_url": spec["modelscope_url"],
                "download_cmd": self._clone_download_cmd(engine, cache_root),
                "message": f"已在后台开始下载 {spec['label']}（魔塔国内源）。可复制命令手动下载，或轮询进度。",
            }
        except Exception as e:  # noqa: BLE001
            logger.exception("下载语音克隆模型失败")
            return {"success": False, "error": str(e)}

    def _clone_download_cmd(self, engine: str, cache_root: str) -> str:
        """生成可直接复制的魔塔下载命令（落盘到各引擎缓存根，匹配 server 读取布局）。"""
        spec = self._clone_engine_spec(engine)
        cr = cache_root.replace('"', '\\"')
        return (
            f'set MODELSCOPE_CACHE="{cr}" && '
            f'modelscope download --model {spec["repo"]}'
        )

    def _run_clone_download(self, engine: str, cache_root: str, task_id: str) -> None:
        """后台线程：用 modelscope 从魔塔下载指定克隆引擎模型；结果写入 _clone_dl_state。"""
        with self._clone_dl_lock:
            self._clone_dl_state[task_id] = {"status": "running", "log": "准备下载…"}

        def setlog(msg, status="running"):
            with self._clone_dl_lock:
                self._clone_dl_state[task_id] = {"status": status, "log": msg}

        spec = self._clone_engine_spec(engine)
        # 确保 modelscope CLI 真正可用（import modelscope 会被残缺命名空间包骗过：
        # 2026-10-13 实测本机 site-packages 的 modelscope 无 __init__/cli 子模块）
        from src.companion.model_catalog import _modelscope_exe
        exe_prefix = _modelscope_exe()
        if exe_prefix is None:
            setlog("正在安装 modelscope…", "running")
            try:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-q", "--upgrade", "modelscope"],
                    check=True, timeout=600,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except Exception as e:
                setlog(
                    f"自动安装 modelscope 失败（{e}）。请手动运行下方命令下载。",
                    "manual",
                )
                return
            exe_prefix = _modelscope_exe()
            if exe_prefix is None:
                setlog(
                    "modelscope 安装后 CLI 仍不可用。请手动运行下方命令下载。",
                    "manual",
                )
                return
                return
        setlog(f"下载中：{spec['label']}（魔塔国内源，请稍候）…", "running")
        # 关键：把 MODELSCOPE_CACHE 指向各引擎缓存根，
        # 让 modelscope 落到 <root>/models--<ns>--<name>/snapshots/<rev>/ —— 与 server 读取布局一致。
        env = dict(os.environ)
        env["MODELSCOPE_CACHE"] = cache_root
        cmd = exe_prefix + ["download", "--model", spec["repo"]]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                                  timeout=3600, env=env)
            # 验证下载落地
            _, model_dir = self._clone_model_dir(engine)
            if proc.returncode == 0 and model_dir:
                setlog(
                    f"下载完成：{spec['label']}。已落到 {model_dir}，重启对应克隆引擎后生效。",
                    "done",
                )
            else:
                log = (proc.stderr or proc.stdout or "")[:600]
                setlog(
                    f"下载命令执行失败（{log}）。请手动复制命令下载。",
                    "manual",
                )
        except Exception as e:
            setlog(f"下载异常（{e}）。请手动运行命令下载。", "manual")

    def get_voice_download_status(self, task_id: str) -> dict:
        """轮询本地克隆模型下载进度。返回 {"status": running|done|manual|error, "log": "..."}。"""
        try:
            with self._clone_dl_lock:
                return dict(self._clone_dl_state.get(task_id, {"status": "unknown", "log": ""}))
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "log": str(e)}

    # ======== 记忆设置（方案开关：jarvis / legacy 对比） ========
    def get_memory_settings(self) -> dict:
        """返回长期记忆配置 + 记忆库统计（供「记忆设置」Tab 显示）。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        mem_raw = self._cfg.raw.get("memory", {}) or {}
        store = self._memory_store
        stats: dict[str, Any] = {
            "count": 0,
            "count_by_type": {"directive": 0, "user": 0, "world": 0},
            "mode": "",
            "has_embed": False,
            "items": [],
        }
        if store is not None:
            stats["count"] = store.count()
            stats["count_by_type"] = store.count_by_type()
            stats["mode"] = store.mode
            stats["has_embed"] = getattr(store, "_embed_fn", None) is not None
            # 只读精简条目（不传 emb 向量）
            items = []
            for m in store.items():
                items.append({
                    "id": m.get("id", ""),          # 【2026-10-13】单条删除/定位用
                    "text": m.get("text", ""),
                    "type": m.get("type", "user"),
                    "hits": m.get("hits", 1),
                    "updated_at": m.get("updated_at", 0),
                })
            stats["items"] = sorted(items, key=lambda x: -x["updated_at"])[:80]
        else:
            stats["disabled"] = True  # memory.enabled=false → 未创建记忆库
        return {
            "success": True,
            "enabled": bool(mem_raw.get("enabled", True)),
            "max_items": int(mem_raw.get("max_items", 100)),
            "mode": mem_raw.get("mode", "jarvis"),
            "semantic_search": bool(mem_raw.get("semantic_search", True)),
            "intent_judge": bool(mem_raw.get("intent_judge", True)),
            "inject_context": bool(mem_raw.get("inject_context", True)),
            "stats": stats,
        }

    def get_memory_count(self) -> dict:
        """【2026-10-13】轻量记忆条数（右侧状态栏轮询用）。

        为什么不复用 get_memory_settings：那个接口会把最多 80 条记忆全文一起
        下发，状态栏每隔几秒轮询一次就白白搬运几十 KB。这里只回计数。
        """
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        _enabled = bool(((self._cfg.raw.get("memory") or {}).get("enabled", True)))
        store = self._memory_store
        # 【2026-10-17 修复·记忆库显示 NaN】count_by_type 必须**永远包含 4 个键**。
        # 旧实现 store 为 None 时回 {}，前端 renderStats 做 c.directive+c.user+c.world+c.episode
        # 时 episode 是 undefined → 0+0+0+undefined = **NaN**（用户截图实锤）。
        _EMPTY = {"directive": 0, "user": 0, "world": 0, "episode": 0}
        if store is None:
            return {"success": True, "count": 0, "count_by_type": dict(_EMPTY),
                    "disabled": True, "enabled": _enabled}
        try:
            _cbt = dict(_EMPTY)
            try:
                _cbt.update({k: int(v) for k, v in (store.count_by_type() or {}).items()
                             if isinstance(v, (int, float))})
            except Exception:  # noqa: BLE001
                pass
            return {"success": True, "count": int(store.count()),
                    "count_by_type": _cbt,
                    "enabled": _enabled}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"读取记忆条数失败: {e}"}

    def save_memory_settings(self, data: dict) -> dict:
        """保存记忆设置并热切换生效（mode/semantic/intent 实时生效；enabled 需重启）。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        try:
            raw = self._cfg.raw
            if not isinstance(raw.get("memory"), dict):
                raw["memory"] = {}
            mem = raw["memory"]

            # 键级合并：缺失键保留已存储值（mem），避免单键提交静默重置其它记忆开关。
            # 此前回退到硬编码默认，导致前端只能整份提交；现在发单个键即可只改该键。
            def _cur(key, default):
                return mem.get(key, default)

            mode = (data.get("mode") or _cur("mode", "jarvis")).strip().lower()
            if mode not in ("jarvis", "legacy"):
                return {"success": False, "error": f"未知记忆方案: {mode}"}
            semantic = bool(data.get("semantic_search", _cur("semantic_search", True)))
            intent = bool(data.get("intent_judge", _cur("intent_judge", True)))
            enabled = bool(data.get("enabled", _cur("enabled", True)))
            inject_ctx = bool(data.get("inject_context", _cur("inject_context", True)))
            try:
                max_items = max(1, int(data.get("max_items") or _cur("max_items", 100)))
            except (TypeError, ValueError):
                max_items = 100

            mem["enabled"] = enabled
            mem["max_items"] = max_items
            mem["mode"] = mode
            mem["semantic_search"] = semantic
            mem["intent_judge"] = intent
            mem["inject_context"] = inject_ctx

            self._save_config()
            # 重建命名空间（dialogue 每次实时读取 intent_judge / inject_context）
            from ..config import _to_ns
            self._cfg.ns = _to_ns(raw)

            # 热切换记忆库：mode 立即生效；semantic 变化时重建/移除嵌入函数
            live = False
            store = self._memory_store
            if store is not None:
                store.set_mode(mode)
                store.set_max_items(max_items)
                if semantic:
                    if getattr(store, "_embed_fn", None) is None:
                        try:
                            from ..llm.ollama_client import make_embed_fn
                            store.set_embed_fn(make_embed_fn(self._cfg.ns.ollama.host))
                        except Exception:  # noqa: BLE001
                            logger.warning("语义检索嵌入初始化失败，保持降级", exc_info=True)
                else:
                    if getattr(store, "_embed_fn", None) is not None:
                        store.set_embed_fn(None)
                live = True
            logger.info("记忆设置已保存: mode=%s semantic=%s intent=%s max_items=%d (live=%s)",
                        mode, semantic, intent, max_items, live)
            return {"success": True, "applied_live": live, "mode": mode}
        except Exception as e:
            logger.exception("保存记忆设置失败")
            return {"success": False, "error": str(e)}

    def clear_memory(self) -> dict:
        """清空长期记忆库。"""
        store = self._memory_store
        if store is None:
            return {"success": False, "error": "记忆库未创建（memory.enabled=false）"}
        store.clear()
        logger.info("长期记忆已清空")
        return {"success": True}

    def delete_memory(self, mem_id: str = "") -> dict:
        """【2026-10-13】删除单条长期记忆（记忆库列表行的「删除」按钮）。

        参数 `mem_id` 优先按条目的稳定 id 删；若前端只有文本（旧数据无 id 的
        过渡态），`MemoryStore.remove` 会归一化后按文本兜底删一条。硬删除，
        不是软失效（与 `retract` 的语义区分开：用户点的是"移除这条"）。
        """
        store = self._memory_store
        if store is None:
            return {"success": False, "error": "记忆库未创建（memory.enabled=false）"}
        try:
            n = store.remove(str(mem_id or ""))
        except Exception as e:  # noqa: BLE001
            logger.exception("删除单条记忆失败")
            return {"success": False, "error": str(e)}
        if not n:
            return {"success": False, "error": "未找到该记忆（可能已被删除）"}
        return {"success": True, "removed": n}

    def synthesize_memory(self) -> dict:
        """【记忆库管理页·手动提炼】对当前对话上下文立即跑一次整段记忆提炼。

        【2026-10-11】整段上下文交给 LLM 自己提炼（替代旧的 EXTRACT_HINT_RE 关键词
        白名单——实测触发比 1:404、产出 0 条，把"我晚上七点吃的饭"这类平淡但值得
        记的事实全漏掉）。

        提炼耗时实测 30~37s（云端带思考），远超 _run_coro 默认 5s 超时；且 _run_coro
        出错/超时统一返回 None（静默吞），故此处不复用它，改用带 90s 超时的独立桥接，
        并把超时/异常如实回给前端（不静默）。
        """
        d = getattr(self, "_dialogue", None)
        if d is None or not hasattr(d, "synthesize_memory_now"):
            return {"success": False, "error": "对话引擎未就绪（无法提炼）"}
        loop = getattr(self, "_loop", None)
        if loop is None or not loop.is_running():
            return {"success": False, "error": "运行时未启动（无法提炼）"}
        try:
            future = asyncio.run_coroutine_threadsafe(d.synthesize_memory_now(), loop)
            res = future.result(timeout=90)
            if not isinstance(res, dict):
                return {"success": False, "error": f"提炼返回异常类型：{type(res).__name__}"}
            res.setdefault("success", bool(res.get("ok")))
            return res
        except asyncio.TimeoutError:
            future.cancel()
            logger.warning("手动记忆提炼超时（>90s）")
            return {"success": False, "error": "提炼超时（>90s），请稍后重试或改用云端快车道模型"}
        except Exception as e:  # noqa: BLE001
            logger.exception("手动记忆提炼失败")
            return {"success": False, "error": str(e)}

    # ======== 活动记忆：日报/周报浏览 + 高级感图片 + 导出 ========
    def _activity_store(self):
        """取活动存储（注入的 ActivityReporter 优先，否则默认 data/activity）。"""
        from ..companion.activity_log import ActivityStore
        rep = getattr(self, "_activity_reporter", None)
        store = getattr(rep, "_store", None) if rep is not None else None
        return store if store is not None else ActivityStore()

    def _report_images_root(self) -> str:
        """日报/周报图片目录（local=本地渲染卡，ai=CogView 生图）。"""
        root = os.path.join(self._activity_store().root, "images")
        os.makedirs(root, exist_ok=True)
        return root

    def _save_chat_images(self, images) -> list:
        """聊天发图落盘（2026-10-03）：base64 data URL / 纯 base64 → images/chat/*.png。

        返回可直接给 <img src> 用的 /local_img?path=... URL 列表；单张失败只跳过
        该张（记日志），不影响其余图片与消息本身。落盘目录同时注册进
        _LOCAL_IMG_EXTRA_DIRS，供 /local_img 路由白名单放行（防路径穿越）。"""
        import base64 as _b64
        import uuid as _uuid
        from urllib.parse import quote as _q
        out_dir = os.path.join(self._report_images_root(), "chat")
        os.makedirs(out_dir, exist_ok=True)
        try:
            _abs = os.path.abspath(out_dir)
            if _abs not in STATE.local_img_extra_dirs:
                STATE.local_img_extra_dirs.append(_abs)
        except Exception:  # noqa: BLE001
            pass
        urls = []
        for i, raw in enumerate(images or []):
            try:
                s = str(raw or "")
                if s.startswith("data:image/"):
                    head, _, b64 = s.partition(",")
                    ext = ".png"
                    _hl = head.lower()
                    for _e, _x in (("image/png", ".png"), ("image/jpeg", ".jpg"),
                                   ("image/webp", ".webp"), ("image/gif", ".gif"),
                                   ("image/bmp", ".bmp")):
                        if _e in _hl:
                            ext = _x
                            break
                else:
                    b64, ext = s, ".png"
                data = _b64.b64decode(b64)
                if not data:
                    continue
                fname = "msg_{:.0f}_{}{}".format(time.time(), _uuid.uuid4().hex[:8], ext)
                with open(os.path.join(out_dir, fname), "wb") as f:
                    f.write(data)
                urls.append("/local_img?path=" + _q(os.path.abspath(os.path.join(out_dir, fname))))
            except Exception:  # noqa: BLE001
                logger.exception("保存聊天图片失败（第 %d 张，跳过）", i + 1)
        return urls

    def _zhipu_api_key(self) -> str:
        """从 config.yaml vision_models 取智谱 api_key（CogView-3-Flash 免费生图复用同一 key）。"""
        try:
            for m in (self._cfg.raw.get("vision_models", {}).get("models") or []):
                if "bigmodel" in str(m.get("base_url", "")):
                    key = (m.get("api_key") or "").strip()
                    if key:
                        return key
        except Exception:  # noqa: BLE001
            pass
        return ""

    def get_activity_reports(self) -> dict:
        """日报/周报日期列表。"""
        store = self._activity_store()
        return {
            "success": True,
            "daily": store.daily_days(),
            "weekly": store.weekly_weeks(),
        }

    def get_report_markdown(self, kind: str, key: str) -> dict:
        """读取日报/周报 Markdown 原文。"""
        kind = "daily" if kind != "weekly" else "weekly"
        store = self._activity_store()
        content = store.read_daily(key) if kind == "daily" else store.read_weekly(key)
        if not content:
            label = "日报" if kind == "daily" else "周报"
            return {"success": False, "error": f"未找到{label}：{key}"}
        return {"success": True, "kind": kind, "key": key, "content": content}

    def render_report_image(self, kind: str, key: str) -> dict:
        """本地渲染高级感日程卡图片（PIL 粉色毛玻璃）→ 返回 base64 data URL。"""
        from ..companion.report_image import render_daily_image, render_weekly_image
        md = self.get_report_markdown(kind, key)
        if not md.get("success"):
            return md
        out = os.path.join(self._report_images_root(), "local", f"{key}.png")
        try:
            if kind == "weekly":
                render_weekly_image(md["content"], out)
            else:
                render_daily_image(md["content"], out)
        except Exception:  # noqa: BLE001
            logger.exception("本地渲染报告图片失败")
            return {"success": False, "error": "图片渲染失败，请查看日志"}
        return {"success": True, "kind": kind, "key": key,
                "data_url": _img_data_url(out), "path": out}

    def generate_report_ai_image(self, kind: str, key: str) -> dict:
        """智谱 CogView-3-Flash 免费生图（思维导图风封面）→ base64。失败回退本地卡。"""
        from ..companion.report_image import generate_ai_image
        md = self.get_report_markdown(kind, key)
        if not md.get("success"):
            return md
        api_key = self._zhipu_api_key()
        if not api_key:
            return {"success": False, "error": "未找到智谱 api_key：模型设置 → 视觉模型 → 智谱GLM-4V-Flash 需填写 key"}
        out = os.path.join(self._report_images_root(), "ai", f"{key}.png")
        try:
            path = generate_ai_image(
                f"{key} {'日报' if kind == 'daily' else '周报'}",
                md["content"],
                api_key=api_key,
                out_path=out,
            )
        except Exception:  # noqa: BLE001
            logger.exception("AI 生图失败")
            return {"success": False, "error": "AI 生图异常（可稍后重试）"}
        if not path or not os.path.exists(path):
            return {"success": False, "error": "AI 生图失败（返回为空，可重试或使用本地渲染）"}
        return {"success": True, "kind": kind, "key": key,
                "data_url": _img_data_url(path), "path": path}

    def export_report(self, kind: str, key: str) -> dict:
        """导出日报/周报到桌面「AI桌面伴侣报告」文件夹（Markdown + 渲染图）。"""
        md = self.get_report_markdown(kind, key)
        if not md.get("success"):
            return md
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.isdir(desktop):  # OneDrive 重定向桌面
            desktop = os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop")
        folder = os.path.join(desktop, "AI桌面伴侣报告")
        os.makedirs(folder, exist_ok=True)
        label = "日报" if kind == "daily" else "周报"
        dst_md = os.path.join(folder, f"{key}{label}.md")
        with open(dst_md, "w", encoding="utf-8") as f:
            f.write(md["content"])
        exported = [dst_md]
        # 同步导出已生成的图片（本地卡 + AI 封面）
        for sub in ("local", "ai"):
            src = os.path.join(self._report_images_root(), sub, f"{key}.png")
            if os.path.exists(src):
                import shutil
                dst_img = os.path.join(folder, f"{key}{label}_{sub}.png")
                try:
                    shutil.copy2(src, dst_img)
                    exported.append(dst_img)
                except Exception:  # noqa: BLE001
                    logger.warning("导出图片失败: %s", src, exc_info=True)
        return {"success": True, "files": exported, "folder": folder}

    def export_markdown(self, data: dict = None) -> dict:
        """把任意已生成的 Markdown 文本写盘到桌面「AI桌面伴侣报告」文件夹。

        【2026-10-08 补·治日报/周报"导出无反应"】
        日报/周报页原先走 `Blob + a.download` 下载，在 window.pywebview 的 WebView2
        容器里**被静默拦截**（与 10-04 修复的通话导出同根因）：前端弹"已导出 Markdown"
        但文件根本不落盘、也不报错，用户体感是"点了没反应"。

        改为后端直接写磁盘、返回真实路径，前端如实回显。日报单篇与周报（前端把多日
        拼好的组合文本）都走这里，保证导出的就是用户当前看到的内容。
        """
        d = data or {}
        name = (d.get("name") or "").strip()
        content = d.get("content") or ""
        if not name:
            return {"success": False, "error": "缺少文件名"}
        if not content:
            return {"success": False, "error": "暂无内容可导出"}
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.isdir(desktop):  # OneDrive 重定向桌面
            desktop = os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop")
        folder = os.path.join(desktop, "AI桌面伴侣报告")
        os.makedirs(folder, exist_ok=True)
        dst = os.path.join(folder, name if name.endswith(".md") else name + ".md")
        try:
            with open(dst, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception:  # noqa: BLE001
            logger.exception("写日报/周报 Markdown 失败")
            return {"success": False, "error": "写入失败，请查看日志"}
        return {"success": True, "files": [dst], "folder": folder}

    # ======== 聊天记录导出 ========
    @staticmethod
    def _safe_file_stem(name: str) -> str:
        """角色名 → 能安全落盘的文件名片段。

        Windows 文件名里 `\\ / : * ? " < > |` 全非法，而角色名是用户随手起的
        （"达妮娅/小号"、"<3" 都可能）。不处理的话 `open()` 直接抛异常，
        用户看到的是"导出失败"，但真正的原因藏在一个 OSError 里。
        """
        bad = '\\/:*?"<>|'
        out = "".join("_" if ch in bad else ch for ch in str(name or "")).strip(" .")
        return out[:40] or "AI"

    def export_chat_history(self, data: dict = None) -> dict:
        """把聊天记录导出成 Markdown 到桌面「AI桌面伴侣报告」。

        【2026-10-21 加】此前**没有这个功能**：`export_markdown` 只服务日报/记忆页，
        而 `data/chatlog/<char>.jsonl` 里的完整聊天历史没有任何出口——想备份、想换机器、
        想把一段对话发给别人，用户只能截屏。

        两个参数：
          - `character_id`：默认当前角色；
          - `scope`：`"current"`（默认）或 `"all"`（所有角色合成一份，空角色跳过）。

        写盘复用 `export_markdown`（它已经处理了 OneDrive 重定向桌面这些坑），
        这里只负责"取记录 → 渲染"。**没有记录时明确报错**，不写一个只有标题的空文件
        ——那种文件用户会当成"导出成功但内容是空的"，比报错更让人困惑。
        """
        d = data or {}
        if self._chatlog is None:
            return {"success": False, "error": "聊天记录存储未就绪（本次运行不持久化）"}

        scope = str(d.get("scope") or "current").strip().lower()
        cid = str(d.get("character_id") or "").strip() or self._active_char_id()
        names = {}
        try:
            for c in self._cfg.list_characters():
                names[str(getattr(c, "id", "") or "")] = str(getattr(c, "name", "") or "")
        except Exception:  # noqa: BLE001  角色表读不到不影响导出（用 id 兜底）
            logger.debug("[导出聊天记录] 角色表读取失败", exc_info=True)

        from ..companion.chat_export import render_bundle, render_markdown

        try:
            if scope == "all":
                ids = [i for i in names.keys() if i] or [cid]
                sections = [(i, names.get(i, i), self._chatlog.load_all(i)) for i in ids]
                if not any(recs for _, _, recs in sections):
                    return {"success": False, "error": "还没有任何聊天记录可导出"}
                md = render_bundle(sections)
                stem = "聊天记录_全部"
                total = sum(len(recs) for _, _, recs in sections)
            else:
                recs = self._chatlog.load_all(cid)
                if not recs:
                    return {"success": False,
                            "error": "这个角色还没有聊天记录可导出"}
                md = render_markdown(recs, char_name=names.get(cid, ""), char_id=cid)
                stem = "聊天记录_%s" % self._safe_file_stem(names.get(cid) or cid)
                total = len(recs)
        except Exception:  # noqa: BLE001
            logger.exception("[导出聊天记录] 读取/渲染失败")
            return {"success": False, "error": "读取聊天记录失败，请查看日志"}

        ts = time.strftime("%Y%m%d_%H%M")
        r = self.export_markdown({"name": "%s_%s.md" % (stem, ts), "content": md})
        if r.get("success"):
            r["count"] = total
        return r

    # ======== 网课记录：课程列表 + 打开目录 ========
    def _course_repo(self):
        """取课程仓库（注入的 CourseRecorder 优先，否则按配置重建）。"""
        from ..companion.course_session import CourseRepository
        rec = getattr(self, "_course_recorder", None)
        repo = getattr(rec, "_repo", None) if rec is not None else None
        if repo is not None:
            return repo
        c = getattr(self._cfg.ns, "course", None)
        root = getattr(c, "root", "data/courses") if c is not None else "data/courses"
        if not os.path.isabs(root):
            root = os.path.abspath(root)
        return CourseRepository(os.path.join(root, "sessions"))

    def _course_output_dir(self) -> str:
        """课程笔记输出目录（README.md 渲染位置）。"""
        rec = getattr(self, "_course_recorder", None)
        d = getattr(rec, "_output_dir", None) if rec is not None else None
        if d is not None:
            return str(d)
        c = getattr(self._cfg.ns, "course", None)
        root = getattr(c, "root", "data/courses") if c is not None else "data/courses"
        return os.path.abspath(root)

    def list_courses(self) -> dict:
        """已完成/进行中的网课笔记列表。"""
        try:
            repo = self._course_repo()
            out_dir = self._course_output_dir()
            items = []
            for s in repo.sessions():
                st = s.state
                readme = st.output_path or ""
                if not readme or not os.path.exists(readme):
                    cand = os.path.join(out_dir, st.id, "README.md")
                    if os.path.exists(cand):
                        readme = cand
                items.append({
                    "id": st.id,
                    "title": st.title,
                    "status": st.status.value,
                    "summary": (st.summary or "")[:200],
                    "keyframes": len(st.keyframes),
                    "created_at": st.created_at,
                    "output": readme,
                    "error": st.error or "",
                })
            return {
                "success": True,
                "courses": sorted(items, key=lambda x: x["created_at"], reverse=True),
                "output_dir": out_dir,
            }
        except Exception:  # noqa: BLE001
            logger.exception("列出课程失败")
            return {"success": False, "error": "课程列表加载失败，请查看日志"}

    def get_course_status(self) -> dict:
        """课程记录器当前状态（开关 + 正在录制的课）。"""
        rec = getattr(self, "_course_recorder", None)
        if rec is None:
            c = getattr(self._cfg.ns, "course", None)
            return {
                "success": True,
                "enabled": bool(getattr(c, "enabled", True) if c is not None else True),
                "auto_detect": bool(getattr(c, "auto_detect", True) if c is not None else True),
                "recording": None,
            }
        return {"success": True, **rec.snapshot()}

    def open_course_dir(self) -> dict:
        """在资源管理器中打开课程输出目录。"""
        d = self._course_output_dir()
        os.makedirs(d, exist_ok=True)
        try:
            os.startfile(d)  # type: ignore[attr-defined]
            return {"success": True, "path": d}
        except Exception:  # noqa: BLE001
            logger.exception("打开课程目录失败")
            return {"success": False, "error": "打开目录失败"}

    def open_data_file(self, filename: str = "") -> dict:
        """在资源管理器中打开 data 目录（可选：选中某个文件）。

        仅允许打开项目 data 根目录下的文件（防路径穿越）；filename 为空则只打开目录本身。
        """
        try:
            from src.proactive_pack.config import DEFAULT_BASE_DIR
            base = Path(DEFAULT_BASE_DIR)
        except Exception:  # noqa: BLE001
            base = Path(getattr(self, "_base_dir", "") or ".") / "data"
        base = Path(base).resolve()
        try:
            base.mkdir(parents=True, exist_ok=True)
            if filename:
                # 只允许 data 根目录下的文件，且防 ../ 穿越
                target = (base / filename).resolve()
                if target != base and target.parent != base:
                    return {"success": False, "error": "仅允许打开 data 目录内的文件"}
                if not target.exists():
                    target = base
                try:
                    os.startfile(str(target))  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    os.startfile(str(base))  # type: ignore[attr-defined]
            else:
                os.startfile(str(base))  # type: ignore[attr-defined]
            return {"success": True, "path": str(base)}
        except Exception:  # noqa: BLE001
            logger.exception("打开 data 目录失败")
            return {"success": False, "error": "打开目录失败"}

    def get_social_channels(self) -> dict:
        """读取社交渠道（QQ/微信/QQ空间）与桌面媒体配置，供设置页展示与编辑。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        import copy
        channels = self._cfg.raw.get("channels") or {}
        desktop = self._cfg.raw.get("desktop") or {}
        return {"success": True, "channels": copy.deepcopy(channels),
                "desktop": copy.deepcopy(desktop)}

    def save_social_channels(self, data: dict) -> dict:
        """保存社交渠道配置（QQ/微信/QQ空间）。data 结构与 config.channels 一致。
        仅允许覆盖 qq/wechat/qzone 三个已知通道；其余键原样保留。改动需重启生效。"""
        if not self._cfg:
            return {"success": False, "error": "配置未加载"}
        if not isinstance(data, dict):
            return {"success": False, "error": "无效参数：data 必须是对象"}
        raw = self._cfg.raw
        existing = raw.get("channels") or {}
        merged = dict(existing)
        for k in ("qq", "wechat", "qzone"):
            if k in data and isinstance(data[k], dict):
                merged[k] = data[k]
        raw["channels"] = merged
        # 桌面媒体摄入（陪你看视频）同步写回 desktop.media.watch_videos
        # ⚠️ 用合并而非整体覆盖，避免误删 desktop 段未来可能新增的其它子键
        if "desktop" in data and isinstance(data.get("desktop"), dict):
            incoming = data["desktop"] or {}
            merged_desktop = dict(raw.get("desktop") or {})
            for k, v in incoming.items():
                if isinstance(v, dict) and isinstance(merged_desktop.get(k), dict):
                    merged_desktop[k] = dict(merged_desktop[k])
                    merged_desktop[k].update(v)
                else:
                    merged_desktop[k] = v
            raw["desktop"] = merged_desktop
        try:
            self._save_config()
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": "写入配置失败：%s" % e}
        return {"success": True}

    # ===== 主动互动包（proactive_pack）：独立开发、可整体开关的额外包 =====
    # 状态落在 data/proactive_pack.json 的 flags 段（不碰 config.yaml，避免整份覆盖老坑）。
    # 三个方法分别承担：读状态 / 写状态（热生效，无需重启）/ 只读诊断（验证开关真实有效）。

    def _proactive_pack_instance(self):
        """拿到已接入的 ProactivePack 实例（懒安装，幂等）；没有返回 None。"""
        pro = getattr(self, "_proactive", None)
        if pro is None:
            return None
        pack = getattr(pro, "_proactive_pack", None)
        if pack is not None:
            return pack
        try:
            from src.proactive_pack import install
            return install(pro)
        except Exception:  # noqa: BLE001
            logger.debug("取得主动互动包实例失败", exc_info=True)
            return None

    def get_proactive_pack_flags(self) -> dict:
        """读取主动互动包全部开关 + 运行态快照（含上次开口 / 当日账本 / 状态文件路径）。"""
        pack = self._proactive_pack_instance()
        if pack is None:
            return {"success": False, "error": "主动引擎未就绪（proactive 实例缺失）"}
        try:
            st = pack.status()
            # 补一段"此刻真实静默状态"，让前端能直接看出"离触发还差多久"
            st.update(pack.live_state())
            return {"success": True, **st}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": "读取状态失败：%s" % e}

    def set_proactive_pack_flags(self, flags: dict) -> dict:
        """写入主动互动包开关（data/proactive_pack.json 的 flags 段）。

        写盘后立即 reload 内存中的包，使其**热生效、无需重启**——与社交渠道（要重启）不同，
        这是本包的设计优势。写盘失败或内容非法都会回退并告知前端。
        """
        if not isinstance(flags, dict):
            return {"success": False, "error": "无效参数：flags 必须是对象"}
        pack = self._proactive_pack_instance()
        if pack is None:
            return {"success": False, "error": "主动引擎未就绪（proactive 实例缺失）"}
        try:
            from src.proactive_pack.config import write_state_flags, DEFAULTS
            # 只接受包认识的键（未知键也透传，但不覆盖内置默认语义之外的值）
            clean = {}
            for k, v in flags.items():
                if k in DEFAULTS:
                    clean[k] = v
            ok = write_state_flags(clean, pack.base_dir)
            if not ok:
                return {"success": False, "error": "写入状态文件失败（磁盘/权限）"}
            pack.reload_flags()          # 热生效：下一轮 maybe_emit 即用新值
            # 【2026-10-17 解耦·单一真相源】"主动互动"的唯一开关 = data/proactive_pack.json 的
            # flags.enabled；但宿主自己还有三条"聊天页主动"路径（空闲主动 / 长会话收尾 /
            # 记忆型主动）经 _schedule_duplex_emit(to_chat=True)，它们历史上被 proactive.enabled 顺带管着。
            # 这里把开关值**镜像**进 proactive.chat_initiative，使「聊天页那个开关」与「设置页主动互动」
            # 效果完全一致（一个开关管一件事），同时**绝不触碰** runtime_on / duplex_mode
            # —— 于是"关掉主动互动"不再影响通话与看屏互动。
            try:
                if "enabled" in clean and self._cfg is not None:
                    _raw = self._cfg.raw
                    _pro = _raw.setdefault("proactive", {})
                    _ci = bool(clean.get("enabled"))
                    _pro["chat_initiative"] = _ci
                    _pro["enabled"] = _ci            # 旧键同步写，兼容老读法
                    _pro.setdefault("runtime_on", True)
                    self._save_config()
                    from ..config import _to_ns
                    self._cfg.ns = _to_ns(_raw)
                    _pr = getattr(self, "_proactive", None)
                    if _pr is not None:
                        try:
                            _pr.chat_initiative = _ci
                        except Exception:  # noqa: BLE001
                            pass
            except Exception:  # noqa: BLE001
                logger.debug("主动互动开关镜像到 chat_initiative 失败（不影响包本身）", exc_info=True)
            st = pack.status()
            st.update(pack.live_state())
            return {"success": True, **st}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": "保存失败：%s" % e}

    def test_proactive_pack(self) -> dict:
        """只读诊断：用**此刻真实状态**推演"若现在静默达标，她会发起哪一类主动"。

        不生成、不派发、不写账本——纯粹证明引擎已接入并在推理，方便用户确认开关真实有效。
        """
        pack = self._proactive_pack_instance()
        if pack is None:
            return {"success": False, "error": "主动引擎未就绪（proactive 实例缺失）"}
        try:
            import time as _t
            now = _t.time()
            cid = pack._char_id()
            li = pack._last_input_ts() if cid else 0.0
            live = pack.live_state(now)
            dr = pack.dry_run(cid=cid or "danya", now=now, last_input_ts=li if li else (now - 6 * 3600))
            return {
                "success": True,
                "enabled": pack.enabled,
                "installed": True,
                "meets_idle": live.get("meets_idle", False),
                "gap_min": live.get("gap_min", 0.0),
                "idle_threshold_s": live.get("idle_threshold_s", 0.0),
                "trigger": dr.get("trigger"),
                "brief": dr.get("brief"),
                "situation": (dr.get("situation") or "")[:400],
                "last_emit": pack._last_text,
                "ledger_today": pack.marks.ledger_count(now),
                "state_file": str(pack.marks.path),
            }
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": "诊断失败：%s" % e}

    # ===== 主题系统（2026-10-05，见 _design.txt 第十节） =====
    # 存储逻辑全在 src/ui/theme_store.py（独立模块，便于单测）；这里只做 API 薄封装。
    # 命名统一 t_ 前缀，避开既有方法名。

    @staticmethod
    def _theme_store():
        from . import theme_store
        return theme_store

    def t_list(self, data: dict = None) -> dict:
        """列出全部主题包 + 角色↔主题绑定 + 建议尺寸。

        2026-10-12 用户裁决「角色头图 ↔ 主题绑定死」：绑定的角色带头图时，
        该主题的 cover 图位**读取时**透出这张头图（带 inheritedFromCharacter 标记）。
        只影响透出、不写盘 —— 解绑 / 换角色 / 删头图即自动还原；
        角色没有头图时封面原样保留，渲染层会让它自动成为她的卡头图。
        """
        try:
            ts = self._theme_store()
            d = ts.load_all()
            # 角色表 + 反查绑定（boundCharacter 字段偶尔与 bindings 不同步，逆映射更稳）
            _chars = {}
            try:
                for _c in (self._cfg.raw.get("characters") or []) if self._cfg else []:
                    if isinstance(_c, dict) and _c.get("id"):
                        _chars[_c["id"]] = _c
            except Exception:  # noqa: BLE001
                _chars = {}
            _bindings = d.get("bindings") or {}
            _reverse = {}
            for _cid, _tid in _bindings.items():
                _reverse.setdefault(_tid, _cid)
            themes = []
            for tid, t in (d.get("themes") or {}).items():
                images = t.get("images") or {}
                pages = images.get("pages") or {}
                bound = t.get("boundCharacter") or _reverse.get(tid) or ""
                banner = str((_chars.get(bound) or {}).get("banner") or "").strip()
                eff_images = ts.apply_bound_cover({k: images.get(k) for k in ts.SLOTS}, banner)
                themes.append({
                    "id": t.get("id") or tid,
                    "name": t.get("name") or "未命名主题",
                    "boundCharacter": bound or None,
                    "images": eff_images,
                    "pages": pages,
                    "stickers": t.get("stickers") or [],
                    "theme": t.get("theme") or {},
                    "pageWallpaper": bool(t.get("pageWallpaper")),
                })
            return {
                "success": True,
                "themes": themes,
                "bindings": _bindings,
                "slots": list(ts.SLOTS),
                "slotLabels": ts.SLOT_LABELS,
                "slotHints": {k: list(v) for k, v in ts.SLOT_HINT.items()},
            }
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def t_save(self, data: dict = None) -> dict:
        """新建或更新一个主题包（整包覆盖：images/theme/name/pageWallpaper）。"""
        try:
            ts = self._theme_store()
            d = data or {}
            tid = (d.get("id") or "").strip()
            if not tid:
                tid = ts.new_theme_id(d.get("name") or "theme")
            store = ts.load_all()
            cur = store["themes"].get(tid) or ts.default_theme(tid, d.get("name") or "新主题")
            if d.get("name"):
                cur["name"] = str(d["name"])[:60]
            for k in ts.SLOTS:
                if k in (d.get("images") or {}):
                    _rec = d["images"][k]
                    # 「继承自角色头图」的记录是读取时派生的，不落盘（避免主题包里
                    # 存下一个指向角色头图的外部路径，角色删图后变成僵尸封面）。
                    if isinstance(_rec, dict) and _rec.get("inheritedFromCharacter"):
                        continue
                    cur.setdefault("images", {})[k] = _rec
            if "pages" in d:
                cur.setdefault("images", {})["pages"] = d.get("pages") or {}
            if "theme" in d and isinstance(d.get("theme"), dict):
                cur["theme"] = d["theme"]
            if "pageWallpaper" in d:
                cur["pageWallpaper"] = bool(d.get("pageWallpaper"))
            if "stickers" in d:
                # 贴纸 / 表情包（2026-10-12）：整组覆盖，只收白名单字段。
                def _f(v, dflt, lo, hi):
                    try:
                        v = float(v)
                    except (TypeError, ValueError):
                        return dflt
                    return max(lo, min(hi, v))
                st_out = []
                for s in (d.get("stickers") or []):
                    if not isinstance(s, dict):
                        continue
                    u = str(s.get("url") or "")
                    if not u:
                        continue
                    st_out.append({
                        "id": str(s.get("id") or "")[:40],
                        "url": u,
                        "name": str(s.get("name") or "")[:80],
                        "kind": "video" if s.get("kind") == "video" else "image",
                        "page": str(s.get("page") or "")[:24],
                        "x": _f(s.get("x"), 0.5, 0.0, 1.0),
                        "y": _f(s.get("y"), 0.5, 0.0, 1.0),
                        "scale": _f(s.get("scale"), 1.0, 0.1, 6.0),
                        "w": int(s.get("w") or 0),
                        "h": int(s.get("h") or 0),
                    })
                cur["stickers"] = st_out
            cur["updatedAt"] = int(time.time())
            store["themes"][tid] = cur
            ts.save_all(store)
            return {"success": True, "id": tid, "theme": cur}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def t_delete(self, data: dict = None) -> dict:
        """删除主题包，并解掉所有指向它的绑定。"""
        try:
            ts = self._theme_store()
            tid = ((data or {}).get("id") or "").strip()
            if not tid:
                return {"success": False, "error": "缺少主题 id"}
            store = ts.load_all()
            store["themes"].pop(tid, None)
            for cid, v in list((store.get("bindings") or {}).items()):
                if v == tid:
                    store["bindings"].pop(cid, None)
            ts.save_all(store)
            import shutil as _sh
            _sh.rmtree(ts.theme_images_dir(tid), ignore_errors=True)
            return {"success": True, "bindings": store["bindings"]}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def t_rename(self, data: dict = None) -> dict:
        """只改主题名（命名不影响取色与渲染）。"""
        try:
            ts = self._theme_store()
            d = data or {}
            tid = (d.get("id") or "").strip()
            name = (d.get("name") or "").strip()
            if not tid or not name:
                return {"success": False, "error": "缺少 id 或名称"}
            store = ts.load_all()
            if tid not in store["themes"]:
                return {"success": False, "error": "主题不存在"}
            store["themes"][tid]["name"] = name[:60]
            store["themes"][tid]["updatedAt"] = int(time.time())
            ts.save_all(store)
            return {"success": True, "name": name[:60]}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def t_bind(self, data: dict = None) -> dict:
        """主题↔角色 双向绑定 / 解绑。character_id 传空串即解绑。"""
        try:
            ts = self._theme_store()
            d = data or {}
            return ts.bind((d.get("id") or "").strip(), (d.get("character_id") or "").strip())
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def t_slot_files(self, data: dict = None) -> dict:
        """列出某图位目录里已有的图片（用户可自己把图丢进 data/char_banners/themes/<id>/）。"""
        try:
            ts = self._theme_store()
            d = data or {}
            files = ts.list_slot_files(
                (d.get("id") or "").strip(), d.get("slot") or "hero", (d.get("page") or ""))
            return {"success": True, "files": files}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def t_pick(self, data: dict = None) -> dict:
        """弹系统文件框选图 → 复制进该主题图位目录 → 返回可直接给 <img> 的描述。

        与 pick_character_banner 同一套做法（tkinter 后台线程 + 120s 超时 + 复制持久化），
        区别是落点精确到「某主题的某图位」，且只保留这一张（反复换图不堆垃圾）。
        """
        try:
            ts = self._theme_store()
            d = data or {}
            tid = (d.get("id") or "").strip()
            slot = d.get("slot") or "hero"
            # 【2026-10-17 修复】page 可能是数字（页码），先 str() 再 strip()
            _pg = (d if isinstance(d, dict) else (data or {})).get("page")
            page = str(_pg).strip() if _pg not in (None, "") else ""
            if not tid:
                return {"success": False, "error": "缺少主题 id"}
            is_sticker = (slot == "sticker")
            if is_sticker:
                label, tip = "贴纸 / 表情包", "贴纸 / 表情包（png/gif/webp/视频）"
            else:
                label = ts.SLOT_LABELS.get(slot, "页面壁纸") if not page else "页面壁纸"
                hint = ts.SLOT_HINT.get(slot)
                tip = "%s（建议 %dx%d）" % (label, hint[0], hint[1]) if hint else label

            # 【2026-10-13 修复·加图片卡死】改为在**独立子进程**里弹框。
            # 原写法是「主进程后台线程内 tk.Tk()+askopenfilename」——实测会在弹框出现后
            # 3 秒内把**整个主进程**拖死（logs/run_*.log 全局停止增长、进程却仍活着、
            # 8777 仍监听），典型"持 logging 全局锁卡在原生 GUI 调用"形态。
            # 机理与隔离收益见 src/ui/_file_dialog.py 顶部。用户可见行为不变。
            # 【2026-10-17】便携 Python 没装 tkinter → 明确提示，别只抛 ModuleNotFoundError
            try:
                import tkinter  # noqa: F401
            except Exception:  # noqa: BLE001
                return {"success": False,
                        "error": "当前运行环境没有 tkinter（便携版 Python 未打包 Tcl/Tk）。"
                                 "请改用页面上的「选择图片」（浏览器文件选择器），"
                                 "或把图片直接放进主题目录。"}
            from ._file_dialog import pick_path
            if is_sticker:
                _fts = [["图片 / 动图", "*.png *.jpg *.jpeg *.webp *.bmp *.gif"],
                        ["视频表情包", "*.mp4 *.webm *.mov"],
                        ["所有文件", "*.*"]]
                _title = "选择" + tip
            else:
                _fts = [["图片文件", "*.png *.jpg *.jpeg *.webp *.bmp *.gif"],
                        ["所有文件", "*.*"]]
                _title = "选择" + tip + " · png/jpg/webp"
            path, _derr = pick_path(_title, filetypes=_fts, timeout=180)
            if _derr:
                return {"success": False, "error": _derr}
            if not path:
                return {"success": False, "error": ""}   # 用户取消，不算错误
            if is_sticker:
                info = ts.save_sticker_image(tid, path, d.get("stickerId") or "")
            else:
                info = ts.save_slot_image(tid, slot, path, page=page)
            if not info or info.get("error"):
                return {"success": False, "error": (info or {}).get("error") or "落盘失败"}
            return {"success": True, "slot": slot, "page": page, "image": info}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def t_sticker_drop(self, data: dict = None) -> dict:
        """把【拖进来】的图 / 视频 / 表情包落盘（贴纸系统 2026-10-12）。

        浏览器拖拽只能拿到内存里的 File（没有磁盘路径），所以走 base64：
        前端把文件读成 dataURL 发来，这里解码写临时文件 → 复用同一套落盘。
        slot 缺省 = 新增一枚贴纸；slot 给了（hero/stand/avatar/cover）= 把拖入的图
        直接填进那个图位（用户「拖到哪、贴到哪」的直觉）。
        """
        try:
            ts = self._theme_store()
            d = data or {}
            tid = (d.get("id") or "").strip()
            if not tid:
                return {"success": False, "error": "缺少主题 id"}
            b64 = str(d.get("b64") or "")
            if "," in b64:                       # dataURL 前缀
                b64 = b64.split(",", 1)[1]
            if not b64:
                return {"success": False, "error": "没有收到文件内容"}
            import base64 as _b64
            try:
                raw = _b64.b64decode(b64, validate=False)
            except Exception as e:  # noqa: BLE001
                return {"success": False, "error": "文件解码失败: %s" % e}
            if len(raw) > ts.STICKER_MAX_BYTES:
                return {"success": False, "error": "这个文件超过 48MB，先压缩一下再拖"}

            name = os.path.basename(str(d.get("name") or "drop"))
            ext = os.path.splitext(name)[1].lower()
            if ext not in ts.STICKER_EXT:
                ext = ".png"
            import tempfile
            fd, tmp = tempfile.mkstemp(prefix="te_drop_", suffix=ext)
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(raw)
                slot = (d.get("slot") or "").strip()
                if slot and slot != "sticker":
                    info = ts.save_slot_image(tid, slot, tmp, page=(str(d.get("page")).strip() if d.get("page") not in (None, "") else ""))
                    info = dict(info or {})
                    info["slot"] = slot
                else:
                    info = ts.save_sticker_image(tid, tmp, d.get("stickerId") or "")
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            if not info or info.get("error"):
                return {"success": False, "error": (info or {}).get("error") or "落盘失败"}
            return {"success": True, "image": info}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def t_sticker_del(self, data: dict = None) -> dict:
        """删除一枚贴纸的落盘文件（移除贴纸后调用，避免孤儿文件）。"""
        try:
            d = data or {}
            tid = (d.get("id") or "").strip()
            name = str(d.get("name") or "")
            if not tid or not name:
                return {"success": True}      # 无从删起也算成功（幂等）
            ok = self._theme_store().delete_sticker_file(tid, name)
            return {"success": True, "deleted": bool(ok)}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def t_theme_ai(self, data: dict = None) -> dict:
        """对一个图位的图片做「几何体检 + AI 配色」。

        几何（裁切比例提醒）永远本地算；配色优先交给已配置的视觉模型（读角色气质给
        语义色板），视觉不可用/超时/无事件循环 → 回落本地色调板。**任何情况都返回结果**，
        绝不让编辑器因为配色失败而让「换图」整体失败。
        """
        try:
            d = data or {}
            tid = (d.get("id") or "").strip()
            slot = d.get("slot") or "hero"
            # 【2026-10-17 修复】page 可能是数字（页码），先 str() 再 strip()
            _pg = (d if isinstance(d, dict) else (data or {})).get("page")
            page = str(_pg).strip() if _pg not in (None, "") else ""
            from ..llm import theme_ai as tai
            path = (d.get("path") or "").strip()
            if not path and tid:
                path = tai.resolve_slot_path(tid, slot, page)
            if not path:
                return {"success": False, "error": "该图位还没有图片"}
            vr = getattr(self, "_vision_router", None)
            if vr is None:
                # ⭐ 2026-10-13 兜底：set_runtime 之前（或未注入）时按 config 现场建一个，
                # 否则 AI 取色永远静默回落本地 —— 「明明配了视觉模型却走本地」。
                try:
                    from ..llm.vision_router import create_vision_router
                    vr = create_vision_router(self._cfg) if self._cfg else None
                    if vr is not None:
                        self._vision_router = vr
                except Exception:  # noqa: BLE001
                    logger.debug("取色兜底创建视觉路由失败", exc_info=True)
            res = self._run_coro(
                tai.analyze_async(path, slot, vr, theme_id=tid,
                                  mode=("light" if str(d.get("mode") or "").lower() == "light" else "dark")),
                timeout=35,
            )
            if not isinstance(res, dict):
                res = tai.analyze_sync(path, slot)   # 无运行时/超时 → 本地兜底
            # 【2026-10-13 可观测性】原来这里不打任何日志 —— 导致"AI 取色没出现"时
            # 无法判断"是没被调用"还是"调用了但回落本地"。这行让两条链路都能自证。
            try:
                _sch = res.get("schemes") or [] if isinstance(res, dict) else []
                logger.info("[AI取色] slot=%s mode=%s 图=%s | 视觉路由=%s | source=%s 色板=%d%s",
                            slot, str(d.get("mode") or "dark"), os.path.basename(path),
                            ("已注入" if vr is not None else "无(将回落本地)"),
                            (res or {}).get("source"), len(_sch),
                            (" | fallback=" + str(res.get("fallback")))
                            if isinstance(res, dict) and res.get("fallback") else "")
            except Exception:  # noqa: BLE001
                pass
            return {"success": True, "slot": slot, "page": page, "result": res}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def _save_config(self) -> None:
        """保存配置到文件。

        【2026-10-19 修复·并发写坏 config.yaml（审计实测 P0）】
        并发审计（10 线程同时 save_character）实锤：旧实现直接
        open→dump→close、无锁非原子 → 多线程互相踩踏时写出**含 NULL 字节的
        半个 YAML**（yaml 报 unacceptable character #x0000）→ 之后**所有**
        保存永久失败，用户表现为"设置怎么改都不生效"。
        UI 事件线程 + 通话记录线程 + 后台任务并发触发 57 个调用点的保存，
        这个竞态在真实使用中完全可能发生。

        修两层：
          ① 进程内互斥锁串行化（复用 _runtime_lock，RLock 可重入）
          ② 原子写：先写同目录临时文件 fsync，再 os.replace 覆盖 ——
             即使进程中途被杀，config.yaml 也只会是旧完整版或新完整版，
             绝不会再出现写一半的损坏文件。
        """
        # 【防御】_runtime_lock 在 __init__ 早期就可能被用到（审计实测），
        # 用 getattr 兜底创建，绝不因锁缺失而让保存崩掉。
        _lock = getattr(self, '_runtime_lock', None)
        if _lock is None:
            import threading as _th
            _lock = _th.RLock()
            self._runtime_lock = _lock
        with _lock:
            import tempfile as _tmpfile
            _dir = os.path.dirname(os.path.abspath(self._config_path)) or "."
            _fd, _tmp = _tmpfile.mkstemp(prefix=".config_", suffix=".tmp", dir=_dir)
            try:
                with os.fdopen(_fd, "w", encoding="utf-8") as f:
                    yaml.dump(self._cfg.raw, f, allow_unicode=True,
                              default_flow_style=False, sort_keys=False)
                    f.flush()
                    os.fsync(f.fileno())
                # 【Windows 特有】目标文件正被其它线程/进程读取时 os.replace
                # 会报 WinError 5（拒绝访问）——审计压力测试实测。读很快结束，
                # 带退避重试 5 次（共 ~1.5s）即可全部成功。
                _last_exc = None
                for _attempt in range(5):
                    try:
                        os.replace(_tmp, self._config_path)
                        _last_exc = None
                        break
                    except PermissionError as _pe:
                        _last_exc = _pe
                        time.sleep(0.05 * (2 ** _attempt))   # 0.05/0.1/0.2/0.4/0.8s
                if _last_exc is not None:
                    raise _last_exc
            except Exception:
                # 清掉残留临时文件（os.replace 成功时 _tmp 已不存在，忽略 ENOENT）
                try:
                    os.unlink(_tmp)
                except OSError:
                    pass
                raise
        # 【2026-10-19 修复·"apply 成功但盘上丢失"的最终根因】
        # 旧逻辑在 self._dialogue is None 时执行 self._cfg = load_config(...)：
        #   load_config **从磁盘重读**并构造全新 Config。而多线程下（审计实锤：
        #   引导页 apply 的保存与其它后台保存并发），线程 B 可能在自己的 dump
        #   里用了线程 A 替换前的旧 cfg —— A 的写入被 B 的旧快照覆盖。
        #   更根本：**内存 self._cfg.raw 本来就是最新真相**（apply 刚写入），
        #   从磁盘重读不仅多余，还会丢掉"只改了内存还没来得及落盘"的任何修改。
        # 现统一为：永不替换 self._cfg，只重解析角色表/音色档表（纯内存操作）。
        try:
            # 【2026-10-21 修·幽灵调用】原来是
            #   `self._cfg.refresh_characters_map() if hasattr(self._cfg, 'refresh_characters_map') else None`
            # —— `Config` 上只有 `refresh_characters()`，**没有** `refresh_characters_map`，
            # 于是 hasattr 恒 False、整行恒等于 `None`，一个静默 no-op。上面那段注释
            # 明明写着"只重解析角色表/音色档表"，实际角色表这一半**从来没生效过**。
            # 这是「`typeof` 守卫伪装接线漏洞」的 Python 版：守卫把"方法名写错了"包装成
            # "这个版本可能没有"，看不出错。真名 + 真 try/except 就够，不需要 hasattr。
            #
            # 症状（`test_onboarding_integration` 的 [B15] 实测）：`set_reference_audio()`
            # 把参考音写进 `raw.characters[].voice.reference_audio` 并落盘，而
            # `get_characters()` 读的是 `_characters_map`（加载时单独 `_to_ns` 出来的一份，
            # 不跟着 raw 变）→ 界面回读为空 → 用户以为没传上去、反复重传。
            # 修在 `_save_config()` 这一处（而不是在每个写 raw.characters 的调用点各补一次）：
            # **凡是改完角色表就保存的路径，保存后角色表就该是新的**，这是保存的语义。
            # 已核对：`_active_character_id` 只在 config.py 里被赋值，且 `switch_character`
            # 同时写 raw，重解析不会把内存里的激活角色改回旧值。
            self._cfg.refresh_characters()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._cfg.refresh_voice_profiles()
        except Exception:  # noqa: BLE001
            pass






# ==========================================================
# WebView2 多窗口白屏修复（2026-08-24 实锤根因）
# ==========================================================
# pywebview(winforms) 的 init_storage() 只生成一个全局 cache_dir，所有窗口
# （加载窗/主窗/桌宠）共用一个 WebView2 UserDataFolder。实测 3 窗口共享同一
# folder 时 WebView2 并发初始化互相冲突 → E_ABORT (0x80004004) 初始化失败
# → 窗口空白（白屏/黑屏）。改为每个窗口独立 UserDataFolder 后全部初始化成功。
#
# 历史假设（已撤除）：曾怀疑本机 GPU 显存被本地模型占满时 WebView2 GPU 进程
# 启动失败也会 E_ABORT，故对非透明窗追加 --disable-gpu 软件渲染兜底。2026-09-04
# 求证该假设未被实锤——用户经 UU 远程（屏幕共享禁止观察）看到的黑屏/未响应实
# 为远程画面未传回、本地软件一直正常。已移除 --disable-gpu 分支恢复正常 GPU 合成；
# 透明桌宠本就走 GPU。
#
# 修复方式：整体替换 EdgeChrome.__init__（复制 pywebview 6.2.1 官方实现，
# 仅改 UserDataFolder + 追加参数），必须在 webview.start() 之前完成。

# 【防卡死·2026-09-03】WebView2 推送专用线程池。
# PostWebMessageAsJson 在渲染进程挂死时会跨线程 COM 阻塞调用方线程，
# 若发生在事件循环线程上会冻结整软件。故所有推送都提交到本池，事件循环永不等待。

# 通知窗 UI 状态（页面经 /notify_state 回报）：show=卡片已渲染应展开 / hidden=收缩

# =====================================================================
# 通知浮窗双模（2026-09-05 定版·默认透明）
# ---------------------------------------------------------------------
# 【为什么默认透明 T】2026-09-05 凌晨「进程卡死」实锤根因：通知窗是**第二个非
# master 的 WebView2 窗口**，pywebview 用 i.Invoke 在 STA/GUI 线程上同步创建它，
# 而不透明模式（transparent=False）的 WebView2 初始化会在 STA 线程上死锁 → 整 UI
# 冻结。同一套代码里的**桌宠窗（transparent=True / on_top=False）本机从不卡死**，
# 是被验证稳定的第二窗范本。故通知窗逐字照搬桌宠窗配置：transparent=True +
# on_top=False，仅创建后由 Win32 设 TOPMOST（不在 __init__ 的 STA 线程里设 TopMost）。
#   模式 T（默认，透明）：窗口常显右上角、显隐与滑入滑出 100% 由页面 CSS 完成，
#     与桌宠窗完全同款（桌宠透明渲染一直正常）→ 物理上不可能黑/白、不可能卡死。
#   模式 O（NOTIFY_OPAQUE=1 兜底）：窗口不透明，滑入/滑出靠窗口位置动画。仅当本机
#     透明合成确实异常时启用；它曾触发 STA 死锁，故非默认。
# 切换：默认 T（透明常显）；不透明兜底设 NOTIFY_OPAQUE=1。
# =====================================================================
# 【2026-09-05 架构定版·默认透明(T)】透明模式=固定透明窗(可穿透)+页面 CSS 卡片滑动，
# 零 Win32 动画循环 → 彻底规避不透明模式逐帧 SetWindowPos 阻塞 HTTP 线程→GUI 死锁的
# 卡死根因（04:25 那次"启动卡死"即此）。透明模式依靠窗口常显右上角、卡片纯 CSS 显隐，
# 契合用户"固定透明窗+HTML动画"诉求，本机自检 op=1 证明透明合成当前正常。
# 仍保留 NOTIFY_OPAQUE=1 不透明兜底（实心窗、主屏几何滑入，防极端机器透明合成失败）。


























# 【AGENT·2026-09-28】电脑操作代理"逐步确认"：把前端广播器注入确认代理。
# 确认请求经由 _wv2_broadcast（WebView2 原生通道）推到聊天界面弹确认框；回执由
# resolve_agent_confirm RPC 唤醒等待中的 future。fail-closed：广播器未注入/前端不在线
# 时，request_confirm 默认拒绝，绝不静默放行。
try:
    from ..llm.agent_confirm_broker import set_broadcaster as _set_agent_confirm_broadcaster
    _set_agent_confirm_broadcaster(_wv2_broadcast)
except Exception:  # noqa: BLE001
    logger.warning("agent_confirm 广播器注册失败（确认弹窗可能不弹）")








# 🛡️【2026-09-27 闪退修复】GUI 线程 CLR 异常守卫。
# 根因：WebView2/WinForms 在窗口创建/导航/销毁的竞态里偶发抛 System.ObjectDisposedException
# （已释放的控件被 evaluate_js / Invoke 触碰）。这类 .NET 异常若逃逸到 GUI 消息泵，
# .NET 会**直接终止整个进程**，且 Python 的 faulthandler / sys.excepthook 都捕获不到
# （日志只会戛然而止、无任何 traceback）——这正是本次"两次闪退"的表现。
# 装好 ThreadException + UnhandledException 处理器后：异常改为"记录完整栈并吞掉"，
# 进程不再无声崩掉；若再触发，现场会写进 logs/ 下的 fault / crash 日志，便于精确定位。
# （_CLR_CRASH_GUARD_INSTALLED 状态已随 _install_clr_crash_guard 迁到 wv2_env.py）




# 模块导入即尝试安装一次（覆盖 webview.start 之前最早的那批窗口创建）；
# 若当时 pythonnet 尚未就绪则降级，create_main_window 内会再装一次。
try:
    _install_clr_crash_guard()
except Exception:  # noqa: BLE001
    pass


# ==========================================================
# 百炼控制台「应用内嵌浏览器」窗口（2026-09-29）
# 设计约束（务必遵守，否则触发 WebView2 STA 死锁 / E_ABORT 白屏）：
#   1) 只在本文件 create_bailian_console_window() 于【启动期 GUI 线程】创建一次，
#      绝不在运行时后端 RPC 里二次 create_window（曾卡死 40+ 次）。
#   2) 创建即隐藏，用户点按钮才 show() + 懒加载 URL（避免启动期抢焦点 / 闪现）。
#   3) 复用现有 edgechromium + 每窗口独立 UserDataFolder（登录态持久化到本机，
#      跨重启保留 —— 这是「登录一次、常驻可见」的关键）。
#   4) 关闭 → 拦截并隐藏（保留窗口实例），绝不销毁（销毁后重开需运行时 create_window）。
#   5) 真数字被阿里云控制台登录态锁死：本窗口就是让用户「在 App 里登录一次」的载体。
# ==========================================================
_BAILIAN_REAL_QUOTA = {"data": None}  # inject 从已登录控制台页抓回的最新快照

# 百炼控制台各功能页深链（URL 已核实，非 AI 幻觉）：免费额度页来自社区一致来源，
# 控制台根用于兜底。免费额度仅华北2·北京地域模型享。
_BAILIAN_CONSOLE_URLS = {
    "free_quota": "https://bailian.console.aliyun.com/?tab=model#/model-usage/free-quota",
    "usage": "https://bailian.console.aliyun.com/?tab=model#/model-usage",
    "api_key": "https://bailian.console.aliyun.com/cn-beijing/?tab=app#/api-key",
    "console": "https://bailian.console.aliyun.com/",
}


class BailianConsoleAPI:
    """百炼控制台内嵌窗口的桥：接收注入脚本上报的真实额度。"""

    def ping(self) -> dict:
        return {"ok": True}

    def report_quota(self, data=None) -> dict:
        """Step2：注入脚本在控制台页同源上下文里抓到的额度文本，回传到这里。"""
        try:
            import json as _json
            if isinstance(data, str):
                data = _json.loads(data)
            if isinstance(data, dict):
                data["_recv_ts"] = time.time()
                _BAILIAN_REAL_QUOTA["data"] = data
                logger.info("[百炼控制台] inject 上报: hits=%s login=%s url=%s",
                            len(data.get("hits") or []), bool(data.get("login")),
                            str(data.get("url"))[:60])
        except Exception:  # noqa: BLE001
            logger.warning("[百炼控制台] inject 上报解析失败", exc_info=True)
        return {"success": True, "accepted": bool(data)}


# 注入脚本：在控制台页同源上下文执行，抓取"免费额度"页可见的额度文本行。
# 不猜任何私有 API 端点（models/quota 幻觉前车之鉴），只读页面渲染结果——
# 就算阿里改版，最多少抓几行，不会报错；抓不到时保留"直接看控制台页"兜底。
















# ============ 桌宠置顶（Win32 级，真正盖过一切窗口）============
# pywebview 的 on_top → Form.TopMost 只能盖过“非置顶”窗口，遇到同为 TopMost 且当前
# 激活的窗口（如音乐软件迷你播放器）仍会被压在下面；且右键菜单从初始 True 状态直接
# toggle 会把“已置顶”误切回 False。故完全绕过 pywebview，直接用 Win32 SetWindowPos
# + 子类化 WM_WINDOWPOSCHANGING 强制置顶（即 PowerToys Always On Top / DeskPins /
# 主流 Live2D 桌宠引擎的做法），确保桌宠恒在最前、且不影响拖动。
import ctypes as _ct
from ctypes import wintypes as _wt

# 【2026-10-17】_u32 原本定义在文件末尾，桌宠函数迁到 pet_window 后这里仍需要它
# 来完成下方的 argtypes/restype 声明（同一进程内 windll.user32 是同一个句柄）。
_u32 = _ct.windll.user32


# —— 关键：声明 user32 调用的 argtypes/restype ——
# 不声明时 ctypes 默认把参数当 32 位 int 处理；64 位 Windows 的 HWND 是 64 位指针，
# 会被截断 → SetWindowPos(hwnd, HWND_TOPMOST, …) 静默失败 → 置顶完全无效（普通窗口都能盖住）。
# 显式声明后 HWND 按 64 位传递，置顶才真正生效。
_u32.FindWindowW.restype = _wt.HWND
_u32.FindWindowW.argtypes = [_wt.LPCWSTR, _wt.LPCWSTR]
_u32.SetWindowPos.restype = _wt.BOOL
_u32.SetWindowPos.argtypes = [_wt.HWND, _wt.HWND, _ct.c_int, _ct.c_int,
                               _ct.c_int, _ct.c_int, _wt.UINT]
_u32.GetWindowLongW.restype = _wt.LONG
_u32.GetWindowLongW.argtypes = [_wt.HWND, _ct.c_int]
_u32.SetWindowLongW.restype = _wt.LONG
_u32.SetWindowLongW.argtypes = [_wt.HWND, _ct.c_int, _wt.LONG]
_u32.GetWindowRect.restype = _wt.BOOL
_u32.GetWindowRect.argtypes = [_wt.HWND, _ct.POINTER(_wt.RECT)]
_u32.GetCursorPos.restype = _wt.BOOL
_u32.GetCursorPos.argtypes = [_ct.POINTER(_POINT)]
_u32.SetWindowDisplayAffinity.restype = _wt.BOOL
_u32.SetWindowDisplayAffinity.argtypes = [_wt.HWND, _wt.UINT]





















if __name__ == "__main__":
    run_webview(debug=True)
