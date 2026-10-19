"""TTS：多后端合成 + pygame 播放。

订阅 AI_DECISION 事件：有文字则切 SPEAKING 状态、合成播放、完成后回 IDLE。
支持运行时切换角色/音色（通过 switch_character()）。
播放用独立 worker 队列，避免阻塞事件总线派发。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import time
from types import SimpleNamespace
from typing import Any, Optional

from ..config import Config
from ..event_bus import EventBus, Event, EventType
from ..protocol import State
from ..state_machine import StateMachine
from .tts_backends import TTSBackend, create_backend, EdgeTTSBackend, GPTSoVITSBackend, Pyttsx3Backend, VoxCpmBackend
from ..perception.process_loopback import FFPLAY_PLAYING, INPROC_SPEAK_GATE, driver_excludes_host

logger = logging.getLogger(__name__)


def _ctrace(event: str, **fields) -> None:
    """通话追踪埋点（诊断专用；未开启时立即返回，见 src/llm/call_trace.py）。"""
    try:
        from ..llm.call_trace import trace as _t
        _t(event, **fields)
    except Exception:  # noqa: BLE001
        pass

# 语音回放存档目录：WebUI 静态资源下的 chat_audio，前端 <audio src="assets/chat_audio/x.wav"> 直接引用
WEB_ASSETS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "src", "ui", "web", "assets", "chat_audio")
AUDIO_URL_PREFIX = "assets/chat_audio/"


class TTS:
    def __init__(self, bus: EventBus, state: StateMachine, cfg: Config) -> None:
        self._bus = bus
        self._state = state
        self._cfg = cfg
        self._backend: TTSBackend = self._create_backend_from_config()
        # GPT-SoVITS 服务是否可用（启动时探测一次；不可用则本轮直接走 edge-tts，
        # 避免每次回复都先撞一次连接失败——这是"语音合成慢"的主要来源）
        self._sovits_down = self._probe_sovits()
        # 【2026-10-13】主后端整场失效记忆（如云端克隆音色过期 → "Invalid voice"）。
        # 置位后流式句子/整段播放直接走兜底链（edge-tts→pyttsx3），不再每句空撞主后端。
        # 换后端（switch_character）时复位。
        self._primary_down = False
        self._primary_down_reason = ""
        # 队列项: ("full", text) 整段播放 / ("partial", text) 流式句子 / ("decision", d) 流式收尾
        self._queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        self._pygame_inited = False
        self._sd_out = None  # sounddevice 流式播放输出（CosyVoice2 流式/定向播放用）
        # 【2026-09-17 扬声器设备切换】'default' = 系统默认（走 ffplay，低延迟）；
        # 其它值 = sounddevice 输出设备索引字符串（定向播放，见 _play_file_on_device）。
        self._out_device = "default"
        self._pending_sentences = 0  # 流式待播句子数
        self._decision_seen = False  # 当前流式轮次是否已收到决策（收尾标志）
        self._streaming = False      # 是否处于流式播放会话（只发一次 TTS_STARTED）
        # 语音回放存档：本次回复所有分段音频文件，播完合并为一个文件发 TTS_SAVED 供聊天页回放
        self._save_stack: list[str] = []
        self._stream_text = ""
        # 【通话打断 barge-in】中断标志 + ffplay 子进程句柄。
        # 背景：本机播放走 ffplay **独立进程**（进程内播放有数十秒延迟，见 _play_via_ffplay），
        # 而 stop() 只能停 pygame/sounddevice —— 杀不掉 ffplay，所以"打断"一直做不了。
        # 这里记下 Popen 句柄，interrupt() 时能真正 terminate 掉它。
        self._interrupted = False
        # 【2026-09-18 修复】打断后「丢弃残留任务」的时间窗口截止（monotonic 秒）。
        # 原实现把 _interrupted 当闩锁用（见 _worker）：interrupt() 已把队列取空，
        # 于是 worker 的下一次 get() 必然阻塞到**打断之后**才入队的第一句新回复，
        # 再把它当残留丢掉 → 每次 barge-in 都白吞一句（实测 00:17:29 / 00:17:40
        # 两次插话后，紧接的「我叫达妮娅。」「好的，没问题的。」只有气泡没声音）。
        # 改为时间窗口：只有窗口内取到的任务才可能是打断瞬间的残留。
        self._interrupt_until = 0.0
        # ===== 【2026-09-18 TTS 链路优化】缓存 / 预合成 / 过期丢弃 =====
        # 背景：worker 是串行的（合成 ~1.1s + 整句播放 + 尾音），03:55 实测排队
        # 4.56s / 9.21s 才轮到下一句 —— 播出来就是"答非所问""音文不同步"。
        self._tts_pcm_cache = {}      # key -> int16 PCM 字节（LRU，有上限）
        self._tts_prefetch = {}       # key -> asyncio.Task（入队即合成）
        # 【2026-09-23 修复·流式被预取短路】key -> asyncio.Queue（预取的**增量**块通道）。
        # 原实现只把整段 bytes 攒在 task 里返回，而 _speak_stream 用 await 等它完成
        # = 「等整句合成完」，流式因此退化成整句（21:07 那通 34 字实测首包 4.07s，
        # 真流式首块应 ≈1s）。现在边收边入队，播放侧首块一到就出声。
        self._tts_prefetch_q = {}
        self._tts_warmed = False      # 是否已做过一次 WS/TLS 预热
        self._tts_stale_s = 3.0       # 排队超过这个秒数就丢弃（0=不丢弃）
        self._tts_cache_enabled = True
        self._tts_prefetch_enabled = True
        self._tts_warmup_enabled = True
        self._tts_cache_max_entries = 64
        self._tts_cache_max_bytes = 8 * 1024 * 1024
        try:
            _raw = getattr(cfg, 'raw', None) or {}
            _pro = _raw.get('proactive', {}) or {}
            self._tts_stale_s = float(_pro.get('duplex_tts_stale_s', 3.0) or 3.0)
            self._tts_cache_enabled = bool(_pro.get('duplex_tts_cache_enabled', True))
            self._tts_prefetch_enabled = bool(_pro.get('duplex_tts_prefetch', True))
            self._tts_warmup_enabled = bool(_pro.get('duplex_tts_warmup', True))
            self._tts_cache_max_entries = int(_pro.get('duplex_tts_cache_max_entries', 64) or 64)
            self._tts_cache_max_bytes = int(_pro.get('duplex_tts_cache_max_mb', 8) or 8) * 1024 * 1024
        except Exception:
            pass
        self._ffplay_proc = None
        os.makedirs(WEB_ASSETS_DIR, exist_ok=True)

    def _create_backend_from_config(self) -> TTSBackend:
        """从当前角色配置创建 TTS 后端。"""
        voice_cfg = self._cfg.get_active_voice_config()
        if voice_cfg is None:
            # 降级：空配置
            return Pyttsx3Backend()
        self._attach_emotion_shell(voice_cfg)
        return create_backend(voice_cfg.engine, voice_cfg)

    def _attach_emotion_shell(self, voice_cfg) -> None:
        """把全局 default_emotion_tags 合并进角色 voice_cfg.emotion_tags（角色项覆盖全局）。

        情绪壳=数据（情绪→语音表达标记），与音色(trait)/引擎解耦、可替换：
        换参考音/换角色只改数据不改代码。「风格(慵懒/胆怯…)」来自克隆音色本身，
        不在这里注入；这里只定「怎么表达情绪」。缺省项由 tts_backends 内置通用表兜底。
        """
        try:
            default = self._cfg.get_default_emotion_tags()
        except Exception:  # noqa: BLE001
            default = {}
        own = getattr(voice_cfg, "emotion_tags", None)
        if isinstance(own, SimpleNamespace):
            own = dict(vars(own))
        own = own if isinstance(own, dict) else {}
        if default or own:
            voice_cfg.emotion_tags = {**(default or {}), **own}

    def _probe_sovits(self) -> bool:
        """探测本地克隆服务（GPT-SoVITS / VoxCPM2）是否可用（1.5s 超时）。不可用返回 True。"""
        backend = self._backend
        if not isinstance(backend, (GPTSoVITSBackend, VoxCpmBackend)):
            return False
        import socket
        default_port = 9880 if isinstance(backend, GPTSoVITSBackend) else 9881
        api_url = getattr(backend, "_api_url", "") or f"http://127.0.0.1:{default_port}"
        try:
            host, port = api_url.split("://")[1].split(":")[0], int(api_url.split(":")[-1].split("/")[0])
        except Exception:  # noqa: BLE001
            host, port = "127.0.0.1", default_port
        try:
            with socket.create_connection((host, port), timeout=1.5):
                logger.info("%s 服务可用 (%s:%s)", backend.name, host, port)
                return False
        except OSError:
            logger.warning("%s 服务未启动 (%s:%s)，本次运行直接使用 edge-tts 兜底音色", backend.name, host, port)
            return True

    def is_muted(self) -> bool:
        """全局无声模式（config.yaml[voice][muted] 或 voice.mode=mute）是否开启。

        切换由 webapp.set_voice_mode 写入 cfg.raw 并重建 ns；此处每次实时读 raw，
        保证运行中点「无声」立即生效。mute 下 TTS 不出任何声（含云端原生 audio_pcm
        与 edge-tts 兜底），文字仍由聊天页独立事件上屏。
        """
        try:
            raw = getattr(self._cfg, "raw", None) or {}
            v = raw.get("voice", {}) or {}
        except Exception:  # noqa: BLE001
            return False
        if v.get("muted"):
            return True
        return str(v.get("mode") or "").strip().lower() == "mute"

    def is_desktop_silent(self) -> bool:
        """桌面陪伴静默：config.yaml[proactive][desktop_tts_enabled]=false 表示桌面互动不接 TTS。

        默认关闭（键不存在→True）= 桌面双工/快照陪伴只飘字不出声；通话/视频/语音不受影响
        （由 AI_DECISION 的 desktop_companion 标记控制，见 proactive._flush_duplex_emit）。
        每次实时读 raw，设置页「桌面陪伴语音」开关热切换即时生效、无需重启托盘。
        """
        try:
            raw = getattr(self._cfg, "raw", None) or {}
            pro = raw.get("proactive", {}) or {}
            return not bool(pro.get("desktop_tts_enabled", False))
        except Exception:  # noqa: BLE001
            return False

    def subscribe(self) -> None:
        self._bus.subscribe(EventType.AI_DECISION, self._on_decision)
        self._bus.subscribe(EventType.PARTIAL_TEXT, self._on_partial)
        self._bus.subscribe(EventType.TTS_INTERRUPT, self._on_interrupt)

    async def start_worker(self) -> None:
        asyncio.create_task(self._worker())

    def switch_character(self, char_id: str) -> bool:
        """运行时切换角色对应的 TTS 后端。"""
        if not self._cfg.switch_character(char_id):
            return False
        voice_cfg = self._cfg.get_active_voice_config()
        if voice_cfg:
            self._attach_emotion_shell(voice_cfg)
            # 【2026-10-17 根治·绑音色后聊天不理人】
            # 旧实现 create_backend 无保护：角色绑了失效音色（如已下线的
            # cosyvoice-v3.5-plus 系）或缺 Key 时，这里抛异常 → 事件订阅线程崩
            # → 之后所有消息都没人处理 → 用户看到"聊天页面不理人"。
            # 现在：创建失败 → 降级 edge-tts 兜底（文本照常回复，出声用默认音色），
            # 并把原因记日志/埋点，用户至少能继续用。
            try:
                self._backend = create_backend(voice_cfg.engine, voice_cfg)
            except Exception as e:  # noqa: BLE001
                # 【2026-10-17 修正·降级目标】edge-tts 已按发布口径下架（不在引擎表里，
                # create_backend 会给"静音后端"），降级必须用**离线 pyttsx3**：
                # 不联网、不需要 Key、Windows 自带 SAPI —— 永不失败。
                logger.error(
                    "TTS 后端创建失败（engine=%s），降级 pyttsx3 离线兜底: %s",
                    getattr(voice_cfg, "engine", "?"), e)
                _ctrace("tts_fallback", engine=getattr(voice_cfg, "engine", "?"),
                        error=str(e)[:120])
                try:
                    _fb_cfg = SimpleNamespace(
                        engine="pyttsx3", voice="",
                        rate="+0%", pitch="+0Hz", volume="+0%",
                        api_key="", api_url="", voice_id="", group_id="",
                        model="", reference_audio="", reference_text="",
                        language="zh", fallback_pyttsx3=True, speed=1.0,
                    )
                    self._backend = create_backend("pyttsx3", _fb_cfg)
                except Exception:  # noqa: BLE001
                    logger.exception("pyttsx3 兜底也失败 → 用静音后端（文本照常回复）")
                    self._backend = create_backend("__silent__", voice_cfg)
            self._sovits_down = self._probe_sovits()
            # 换后端（换角色/换引擎）= 新的音色/服务，解除「主后端失效」记忆
            self._primary_down = False
            logger.info("TTS 后端切换: %s (角色: %s)", self._backend.name, char_id)
        return True

    def reprobe_service(self) -> None:
        """本地克隆服务按需挂载完成后复查可用性，修正 _sovits_down 兜底标志。

        切换角色时本地服务还没拉起，_sovits_down 会误判为不可用（直接走 edge-tts）；
        服务加载完成后再调用一次，让新音色真正生效。
        """
        self._sovits_down = self._probe_sovits()
        # 服务复查后主后端可能已恢复（本地服务挂载完成），解除失效记忆
        self._primary_down = False
        logger.info("TTS 服务复查: backend=%s sovits_down=%s", self._backend.name, self._sovits_down)

    async def _on_partial(self, e: Event) -> None:
        """LLM 流式句子：入队逐句播放（不阻塞事件总线派发）。"""
        text = (e.data or {}).get("text", "").strip()
        # 【G 社交通道】带非桌面 channel（如 "qq"）的流式句子不是给桌面念的，
        # 会被对应通道发回手机/客户端；桌面扬声器必须静音，否则 QQ 私聊在电脑上出声。
        if e.data and e.data.get("channel") and e.data.get("channel") != "desktop":
            return
        if not text:
            return
        self._pending_sentences += 1
        await self._queue.put(("partial", text))

    async def _on_decision(self, e: Event) -> None:
        d = e.data or {}
        # 【G 社交通道】带非桌面 channel（如 "qq"）的决策不在这里合成发声，
        # 由对应通道发回手机/客户端；桌面只合成 channel 为空（桌面默认）的决策。
        if d.get("channel") and d.get("channel") != "desktop":
            _ctrace("tts_drop", reason="non_desktop_channel", kind="full")
            return
        text = (d.get("text") or "").strip()
        # 【2026-09-18】TTS 层入口埋点：补齐 speak_out → tts_begin 之间的盲区。
        # 这一段原先完全没有记录，导致"她说了但没声"只能靠猜——本轮就栽在这：
        # 任务在 worker 里被静默丢弃，追踪里只有 speak_out 却没有任何 tts_begin。
        _ctrace("tts_recv", text=text[:40], chars=len(text),
                blocked=bool(d.get("blocked")),
                desktop=bool(d.get("desktop_companion")),
                pcm=bool(d.get("audio_pcm")), streamed=bool(d.get("streamed")))
        if d.get("blocked"):
            # 质量门拦截的乱语：只上屏标注，不合成发声
            _ctrace("tts_drop", reason="blocked", kind="full")
            return
        # 【2026-10-02 修·对齐启动器】云端真全双工（Qwen-Omni-Realtime）的字幕气泡：
        # 语音已由 cloud_omni 自身播放器直接出声（auto_play=True），此处只用于上屏字幕，
        # TTS 必须跳过合成，否则会与云端原生音叠加成双声。cloud_native=True 即此标记。
        if d.get("cloud_native"):
            _ctrace("tts_drop", reason="cloud_native", kind="full")
            try:
                await self._state.to_idle("tts:cloud-native")
            except Exception:  # noqa: BLE001
                pass
            return
        # 【2026-09-11】桌面陪伴静默：桌面双工/快照开口（事件带 desktop_companion=True）
        # 不接 TTS、只飘字（气泡/弹幕由 webapp 订阅 AI_DECISION 独立渲染，照常上屏）。
        # 通话/视频/语音(desktop_companion=False)不受影响 —— 用户明确不能动。
        if self.is_desktop_silent() and d.get("desktop_companion"):
            _ctrace("tts_drop", reason="desktop_silent", kind="full")
            try:
                await self._state.to_idle("tts:desktop-silent")
            except Exception:  # noqa: BLE001
                pass
            return
        if d.get("audio_pcm"):
            # 云端真全双工：模型自带原生语音，跳过合成直接播放
            await self._queue.put(("audio", d))
            return
        if d.get("failed"):
            # 发送失败提示（断连兜底）：只上屏不合成播放，直接收尾
            _ctrace("tts_drop", reason="failed", kind="full")
            await self._state.to_idle("tts")
            return
        if d.get("streamed"):
            # 流式决策：句子已逐句播放，此事件只负责收尾（不整段重播）
            self._stream_text = (d.get("text") or "").strip()
            await self._queue.put(("decision", d))
            return
        if not text:
            _ctrace("tts_drop", reason="empty_text", kind="full")
            await self._state.to_idle("tts")
            return
        # 携带 emotion 一并传给 TTS（决策 LLM 的情绪标签重新接回语音，给克隆音加活人感）
        # 【2026-09-18】记入队时刻（worker 据此判过期）+ 立刻起预合成，让网络合成
        # 与上一句的播放并行进行，轮到它时直接播缓存 PCM（首包 ≈ 0）。
        d["_enq_ts"] = time.monotonic()
        self._kick_prefetch(d)
        await self._queue.put(("full", d))

    async def _on_interrupt(self, e: Event) -> None:
        """收到打断事件（通话 barge-in）：立即停掉正在播的和排队待播的语音。"""
        d = e.data or {}
        reason = d.get("reason", "") if isinstance(d, dict) else ""
        await self.interrupt(reason)

    def set_output_device(self, dev) -> None:
        """设置 TTS 播放输出设备（'default' 或 sounddevice 输出设备索引）。

        【为什么不能只给 ffplay 传参】ffplay 8.x 没有「播放设备」选项（只有 dshow
        采集用的 -audio_device* 系列），SDL 输出只能落到系统默认设备。所以：
          · 'default' → 维持原路径（ffplay 独立进程，本机延迟最低）
          · 指定设备 → 改走 sounddevice 定向输出（_play_file_on_device），失败自动回退
        """
        d = str(dev if dev is not None else "default").strip()
        if d.lower() in ("", "none", "null", "auto"):
            d = "default"
        if d == getattr(self, "_out_device", "default"):
            return
        self._out_device = d
        logger.info("TTS 播放输出设备 → %s（default=系统默认/ffplay）", d)

    def set_output_device_from_cfg(self, cfg) -> None:
        """从配置读 proactive.duplex_speaker_device 并应用（启动/通话建立时调用）。"""
        try:
            pro = getattr(cfg, "ns", None)
            pro = getattr(pro, "proactive", None)
            dev = getattr(pro, "duplex_speaker_device", None)
            self.set_output_device(dev if dev else "default")
        except Exception:  # noqa: BLE001
            logger.debug("读取扬声器设备配置失败（忽略）", exc_info=True)

    def _resolve_out_index(self, dev):
        """设备键 → sounddevice 输出设备索引（'default' → None 表示系统默认）。

        存的键可能是**设备名**（有意为之：索引会随插拔/换 host API 漂移，名字才稳），
        所以每次播放都拿名字在**当前**设备表里现查，再退化为名字子串匹配。
        """
        s = str(dev if dev is not None else "").strip()
        if not s or s.lower() in ("default", "none", "auto"):
            return None
        try:
            return int(s)
        except Exception:  # noqa: BLE001
            pass
        try:
            import sounddevice as sd
            rows = list(enumerate(sd.query_devices()))
            for i, d in rows:
                if ((d or {}).get("max_output_channels", 0) > 0
                        and str((d or {}).get("name") or "") == s):
                    return i
            low = s.lower()
            for i, d in rows:
                if ((d or {}).get("max_output_channels", 0) > 0
                        and low in str((d or {}).get("name") or "").lower()):
                    return i
        except Exception:  # noqa: BLE001
            logger.debug("按名字解析输出设备失败: %s", dev, exc_info=True)
        logger.warning("输出设备 %r 在当前设备表里找不到，本次回退系统默认设备", s)
        return None

    def _play_file_on_device(self, path: str, dev) -> bool:
        """把音频文件定向播到指定输出设备（sounddevice）。成功返回 True。

        解码交给 ffmpeg（独立进程、只做解码不做播放），PCM 用 sounddevice 写到目标
        设备；任何异常/失败一律返回 False，交回 ffplay（系统默认设备）兜底，
        绝不因为"选了个设备"而彻底没声。
        """
        import shutil
        import subprocess
        import numpy as np
        try:
            import sounddevice as sd
        except Exception as e:  # noqa: BLE001
            logger.warning("sounddevice 不可用，无法定向播放: %s", e)
            return False
        ff = (shutil.which("ffmpeg")
              or os.path.expanduser(r"~\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"))
        if not os.path.isfile(ff) and not shutil.which("ffmpeg"):
            logger.warning("找不到 ffmpeg，无法定向解码播放")
            return False
        idx = self._resolve_out_index(dev)
        try:
            info = sd.query_devices(idx, "output")
            sr = int((info or {}).get("default_samplerate") or 24000)
        except Exception:  # noqa: BLE001
            sr = 24000
        use_gate = not driver_excludes_host()
        if use_gate:
            INPROC_SPEAK_GATE.set()
        out = None
        try:
            raw = subprocess.run(
                [ff, "-v", "error", "-i", path, "-f", "s16le", "-ac", "1", "-ar", str(sr), "-"],
                capture_output=True, timeout=60).stdout
            if not raw:
                logger.warning("ffmpeg 定向解码为空: %s", path)
                return False
            out = sd.OutputStream(device=idx, samplerate=sr, channels=1, dtype="int16")
            self._sd_out = out          # 让 stop()/打断 能立刻停掉
            out.start()
            out.write(np.frombuffer(raw, dtype=np.int16))
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("定向播放到设备 %s 失败，回退系统默认: %s", dev, e)
            return False
        finally:
            if out is not None:
                try:
                    out.stop()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    out.close()
                except Exception:  # noqa: BLE001
                    pass
            self._sd_out = None
            if use_gate:
                time.sleep(0.6)         # 尾音静音：吃掉采集缓冲延迟
                INPROC_SPEAK_GATE.clear()

    def _kill_ffplay(self) -> None:
        """终止正在播放的 ffplay 子进程（打断的核心：stop() 管不了独立进程）。"""
        proc = self._ffplay_proc
        self._ffplay_proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=0.4)
                except Exception:  # noqa: BLE001
                    proc.kill()
        except Exception:  # noqa: BLE001
            logger.debug("ffplay 终止失败（可能已退出）", exc_info=True)

    async def interrupt(self, reason: str = "") -> None:
        """打断：停当前播放 + 杀 ffplay + 清空待播队列 + 状态回 idle。

        仅由通话 barge-in 触发（EventType.TTS_INTERRUPT）。聊天/桌宠路径从不清队列，
        历史教训：队列与聊天共用，误清会杀掉正在播放的聊天回复。
        """
        self._interrupted = True
        self._interrupt_until = time.monotonic() + 0.4
        self._streaming = False
        self._pending_sentences = 0
        self._decision_seen = False
        self._save_stack = []
        # 【2026-09-23】预取增量通道随打断作废（在途 task 自己会跑完，无泄漏）
        self._tts_prefetch_q.clear()
        had_sd_out = self._sd_out is not None
        self._kill_ffplay()
        self.stop()
        try:
            await self._state.to_idle("tts:interrupt")
        except Exception:  # noqa: BLE001
            logger.debug("打断后状态回落失败", exc_info=True)
        # 清空待播队列：否则打断后旧句仍会一条条播出来（用户体感"根本停不下来"）。
        # 【2026-09-18】刻意放在所有 await 之后：此后入队的一定是打断之后的新句，
        # 不该被丢；此前的残留已被本循环取空。（get_nowait 不 await，循环内无切换点）
        drained = 0
        try:
            while True:
                self._queue.get_nowait()
                drained += 1
        except Exception:  # noqa: BLE001
            pass
        # 【2026-09-18】此处**刻意不复位** _interrupted：它同时是"杀掉正在播的那一次"
        # 的信号。若在合成期（ffplay 尚未 Popen）打断，_kill_ffplay() 是空操作，只有让
        # 标志留到 _play_via_ffplay 的轮询里才会真正终止——提前复位会让那次播放照播到底。
        # 复位交给 worker：取到下一条被接受的任务时（= 新的一次播放开始）才清。
        _ctrace("tts_interrupt", reason=reason, drained=drained,
                killed_audio=bool(self._ffplay_proc) or had_sd_out)
        logger.info("TTS 已打断（reason=%s，清掉待播 %d 条）", reason, drained)

    async def _worker(self) -> None:
        while True:
            kind, payload = await self._queue.get()
            # 【2026-09-18 TTS 链路优化】过期丢弃：播放串行，排队越久这句越"过期"。
            # 03:55 实测出现排队 4.56s / 9.21s 才轮到 —— 播出来是 9 秒前的旧回应，
            # 既答非所问又造成音文不同步。超过阈值直接丢（宁可不说，也别说旧话）。
            if kind == "full" and isinstance(payload, dict) and self._tts_stale_s > 0:
                _enq = float(payload.get("_enq_ts") or 0.0)
                if _enq and (time.monotonic() - _enq) > self._tts_stale_s:
                    _ctrace("tts_drop", reason="stale", kind=kind,
                            waited=round(time.monotonic() - _enq, 2))
                    logger.info("TTS 丢弃过期任务(已等待 %.2fs): %r",
                                time.monotonic() - _enq,
                                str(payload.get("text") or "")[:30])
                    try:
                        await self._state.to_idle("tts:stale")
                    except Exception:
                        pass
                    continue
            # 【2026-09-18 修复】只丢弃「打断瞬间」的残留任务，判据必须是**时间窗口**
            # 而非布尔闩锁。队列已被 interrupt() 取空，能在这里 get() 到的基本都是打断
            # 之后才入队的新句——原闩锁版本会把这第一句正常回复整句吃掉：get() 一直
            # 阻塞到新句到达，恰好命中闩锁 → continue（无声），而后闩锁才复位。
            if self._interrupt_until:
                if time.monotonic() < self._interrupt_until:
                    logger.info("TTS 丢弃打断残留任务: kind=%s", kind)
                    _ctrace("tts_drop", reason="interrupt_residue", kind=kind)
                    continue
                self._interrupt_until = 0.0
            # 播放中断标志只对"打断时那一次播放"有效：取到被接受的新任务即复位
            # （原实现是在**丢弃时**复位，于是标志被下一次 get() 消费掉，正常回复陪葬）
            self._interrupted = False
            _ctrace("tts_pick", kind=kind)
            # 无声模式：丢弃所有播放任务（含云端原生 audio_pcm / edge-tts 兜底），
            # 仅确保状态回落 idle，文字由聊天页独立事件上屏。这样即便本地克隆服务
            # 已卸载、掉到 edge-tts，mute 也绝不会出声——"选了无声就是真没声"。
            if self.is_muted():
                self._streaming = False
                self._decision_seen = False
                self._pending_sentences = 0
                try:
                    await self._state.to_idle("tts:muted")
                except Exception:  # noqa: BLE001
                    pass
                continue
            if kind == "full":
                await self._speak_flow(payload)  # payload 现在为决策 dict（含 emotion）
            elif kind == "partial":
                await self._play_stream_sentence(payload)
            elif kind == "decision":
                await self._finish_stream()
            elif kind == "audio":
                await self._speak_audio(payload)

    async def _play_stream_sentence(self, text: str) -> None:
        """播放一句流式句子：第一句切 SPEAKING 并发 TTS_STARTED，全部播完统一收尾。"""
        if not self._streaming:
            self._streaming = True
            await self._state.transition(State.SPEAKING, "tts:stream")
            await self._bus.publish(Event(EventType.TTS_STARTED, {"text": text}, "tts"))
        # 【2026-10-13】主后端已判定整场失效（克隆音色过期等）→ 直接走兜底链，
        # 不再对每句空撞一次主后端（省 2~4s/句的失败等待）
        if self._primary_down:
            if not await self._fallback_speak(text):
                logger.warning("流式句子兜底播放失败（主后端已失效）: %s", text[:40])
        else:
            # 【2026-10-16 修复·UnboundLocalError 把 TTS worker 当场打死】
            # 原代码把"兜底链"写在了 except **之外**却又引用 except 里的变量 e：
            #     try: await self.speak(text)
            #     except Exception as e: logger.warning(...)
            #     _es = str(e)                      ← 这里
            # 而 Python 3 会在 except 块结束时**删除** e，于是"本句播放成功"这条正常路径
            # 反而抛 UnboundLocalError → TTS._worker 协程直接终止 → 队列里后面的句子永远
            # 没人播。实测症状（2026-10-16）：日志出现该异常 + "流式播放超过 25s 仍未结束，
            # 强制收尾(防队列饿死)"，用户听到的就是"聊天有字没声 / 只说半句"。
            # 正解：只在**确实抛异常**时才走兜底，且 e 只在 except 内使用。
            _es = ""
            try:
                await self.speak(text)
            except Exception as e:  # noqa: BLE001
                # 单句失败不打断后续句子
                _es = str(e)
                logger.warning("流式句子 TTS 播放失败，跳过: %s", e)
            if _es:
                # 【2026-10-13 修复】旧逻辑只"跳过"——当主后端整场失效（典型：云端克隆
                # 音色过期 → 每句 "Invalid voice"，2026-10-13 实证刷屏）时表现为
                # "聊天有字没声"。这里补走兜底链（edge-tts→pyttsx3），并识别
                # "音色/引擎级失效"错误后记住，后续句子直接走兜底不再每句空撞。
                if await self._fallback_speak(text):
                    if ("Invalid voice" in _es or "Engine error" in _es
                            or "Model not exist" in _es or "InvalidParameter" in _es):
                        self._primary_down = True
                        self._primary_down_reason = _es[:200]
                        logger.warning(
                            "TTS 主后端判定整场失效（%s）→ 后续句子直接走 edge-tts 兜底；"
                            "请到「语音·音色」重新克隆/更换音色后切一次角色即可恢复", _es[:120])
                else:
                    logger.warning("流式句子兜底播放也失败: %s", _es)
        self._pending_sentences -= 1
        if self._decision_seen and self._pending_sentences <= 0:
            await self._end_stream()

    async def _finish_stream(self) -> None:
        """LLM 流式结束、决策已到：等句子全部播完后收尾。"""
        self._decision_seen = True
        if self._pending_sentences <= 0:
            await self._end_stream()

    async def _end_stream(self) -> None:
        self._streaming = False
        self._decision_seen = False
        self._pending_sentences = 0
        await self._flush_saved_audio(self._stream_text)
        self._stream_text = ""
        await self._bus.publish(Event(EventType.TTS_DONE, None, "tts"))
        await self._state.to_idle("tts")

    async def _speak_audio(self, d: dict) -> None:
        """播放云端真全双工模型的原生语音（不合成，直接播 PCM）。"""
        text = (d.get("text") or "").strip()
        await self._state.transition(State.SPEAKING, "tts:duplex")
        await self._bus.publish(Event(EventType.TTS_STARTED, {"text": text}, "tts"))
        try:
            await asyncio.to_thread(
                self._play_pcm, d.get("audio_pcm"), int(d.get("sample_rate", 24000)))
        except Exception as e:  # noqa: BLE001
            logger.exception("真全双工语音播放失败")
            await self._bus.publish(Event(EventType.ERROR, {
                "code": "TTS_ERROR",
                "message": str(e),
            }, "tts"))
        await self._flush_saved_audio(text)
        await self._bus.publish(Event(EventType.TTS_DONE, None, "tts"))
        await self._state.to_idle("tts")

    def _play_pcm(self, pcm: bytes, sample_rate: int) -> None:
        """float32 PCM 字节 → 16-bit WAV 临时文件 → 播放（ffplay 优先，pygame 回退）。"""
        import wave
        import numpy as np

        arr = np.frombuffer(pcm, dtype=np.float32)
        arr16 = (np.clip(arr, -1.0, 1.0) * 32767).astype(np.int16)
        self._save_pcm_wav(arr16, sample_rate)  # 存档供聊天页语音回放
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        tmp.close()
        try:
            with wave.open(tmp.name, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(arr16.tobytes())
            # 【2026-09-17】用户指定了输出设备 → 必须先走定向播放（ffplay 做不到）
            if getattr(self, "_out_device", "default") not in ("default", "", None):
                if self._play_file_on_device(tmp.name, self._out_device):
                    return
            # 优先 ffplay 独立进程（绕开 python 进程内音频库在本机的延迟/会话问题）
            if self._play_via_ffplay(tmp.name):
                return
            import pygame
            if not self._pygame_inited:
                pygame.mixer.init()
                self._pygame_inited = True
            pygame.mixer.music.load(tmp.name)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                pygame.time.Clock().tick(30)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    def _save_pcm_wav(self, arr16, sample_rate: int) -> None:
        """把 int16 PCM 数组写为存档 wav（供聊天页语音回放）。"""
        import wave
        try:
            fname = f"{time.strftime('%Y%m%d-%H%M%S')}-{len(self._save_stack)}.wav"
            dst = os.path.join(WEB_ASSETS_DIR, fname)
            with wave.open(dst, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(arr16.tobytes())
            self._save_stack.append(dst)
        except Exception:  # noqa: BLE001
            logger.exception("语音存档写入失败")

    def _save_wav_copy(self, src_path: str) -> None:
        """把已合成的音频文件复制一份到回放存档目录（只收集，由 _flush_saved_audio 统一发事件）。"""
        import shutil
        try:
            ext = os.path.splitext(src_path)[1] or ".wav"
            fname = f"{time.strftime('%Y%m%d-%H%M%S')}-{len(self._save_stack)}{ext}"
            dst = os.path.join(WEB_ASSETS_DIR, fname)
            shutil.copyfile(src_path, dst)
            self._save_stack.append(dst)
        except Exception:  # noqa: BLE001
            logger.exception("语音存档复制失败")

    def _merge_wavs(self, paths: list[str]) -> str | None:
        """把同参数的多段 wav 拼接为单个存档文件，返回相对 URL。非 wav / 参数不一致的分段跳过。"""
        import wave
        try:
            with wave.open(paths[0], "rb") as w0:
                params = w0.getparams()
                frames = w0.readframes(w0.getnframes())
        except Exception:  # noqa: BLE001
            logger.warning("语音存档首段不可用，跳过合并")
            return None
        for p in paths[1:]:
            try:
                with wave.open(p, "rb") as wf:
                    if (wf.getframerate() != params.framerate
                            or wf.getnchannels() != params.nchannels
                            or wf.getsampwidth() != params.sampwidth):
                        logger.warning("语音分段参数不一致，跳过合并: %s", p)
                        continue
                    frames += wf.readframes(wf.getnframes())
            except Exception:  # noqa: BLE001
                logger.warning("语音分段不可用，跳过合并: %s", p)
        fname = f"{time.strftime('%Y%m%d-%H%M%S')}-merged.wav"
        dst = os.path.join(WEB_ASSETS_DIR, fname)
        try:
            with wave.open(dst, "wb") as wf:
                wf.setparams(params)
                wf.writeframes(frames)
            return AUDIO_URL_PREFIX + fname
        except Exception:  # noqa: BLE001
            logger.exception("语音合并存档写入失败")
            return None

    async def _flush_saved_audio(self, text: str) -> None:
        """本次回复播放完毕：把所有分段存档合并为一个文件，发 TTS_SAVED 事件。"""
        stack = self._save_stack
        self._save_stack = []
        if not stack:
            return
        try:
            if len(stack) == 1:
                url = AUDIO_URL_PREFIX + os.path.basename(stack[0])
            else:
                url = self._merge_wavs(stack)
            if url:
                await self._bus.publish(Event(EventType.TTS_SAVED, {
                    "url": url, "text": text,
                }, "tts"))
        except Exception:  # noqa: BLE001
            logger.exception("语音存档事件发布失败")

    async def _speak_flow(self, payload) -> None:
        # payload 可能是 dict（含 emotion）或纯文本；统一抽取 text/emotion
        if isinstance(payload, dict):
            text = (payload.get("text") or "").strip()
            emotion = payload.get("emotion")
        else:
            text = (payload or "").strip()
            emotion = None
        if not text:
            await self._state.to_idle("tts")
            return
        await self._state.transition(State.SPEAKING, "tts")
        await self._bus.publish(Event(EventType.TTS_STARTED, {"text": text}, "tts"))
        # GPT-SoVITS / VoxCPM2 未启动：跳过主后端，直接走兜底链（edge-tts → pyttsx3），
        # 省去每次回复先撞一次连接失败的时间
        if self._sovits_down or self._primary_down:
            fallback_ok = await self._fallback_speak(text)
            if not fallback_ok:
                await self._bus.publish(Event(EventType.ERROR, {
                    "code": "TTS_ERROR",
                    "message": ("GPT-SoVITS 未启动" if self._sovits_down
                                else "主后端失效(%s)" % self._primary_down_reason[:80])
                              + "且全部兜底失败",
                }, "tts"))
        else:
            try:
                await self.speak(text, emotion=emotion)
            except Exception as e:
                logger.exception("TTS 播放失败")
                fallback_ok = await self._fallback_speak(text)
                if not fallback_ok:
                    await self._bus.publish(Event(EventType.ERROR, {
                        "code": "TTS_ERROR",
                        "message": str(e),
                    }, "tts"))
        await self._flush_saved_audio(text)
        await self._bus.publish(Event(EventType.TTS_DONE, None, "tts"))
        await self._state.to_idle("tts")

    async def _fallback_speak(self, text: str) -> bool:
        """主后端失败后的兜底链（2026-10-16 起**不再使用 edge-tts**）：pyttsx3（本地离线）。

        发布口径（用户明确要求）：默认不绑任何声音、不使用 edge-tts，只允许用户绑定
        自己的服务商与其账号里的音色 ID。故这里只保留"本地离线 pyttsx3"这一级兜底，
        保证"用户自己绑的后端偶尔抽风时还能出声"，而不会偷偷换成微软在线音色。
        """
        candidates: list[TTSBackend] = []
        # 最后用本地离线 pyttsx3（Windows SAPI5，无需联网，音色机械但保证有声音）
        if not isinstance(self._backend, Pyttsx3Backend):
            candidates.append(Pyttsx3Backend())
        for backend in candidates:
            try:
                await self._speak_with_backend(backend, text)
                logger.info("TTS 降级成功: %s", backend.name)
                return True
            except Exception as e:  # noqa: BLE001
                logger.warning("TTS 降级 %s 失败: %s", backend.name, e)
        return False

    async def speak(self, text: str, emotion: str | None = None) -> None:
        """合成并播放一段文本（emotion 可选，供克隆后端注入副语言标签）。"""
        await self._speak_with_backend(self._backend, text, emotion=emotion)

    # 长文本分段只对慢速克隆后端生效（快后端分段反而打断语气、无收益）
    _SPLIT_SLOW_ENGINES = {"qwen3tts"}
    # 单段最大字符数。Qwen3-TTS 0.6B 每段独立克隆，分段过细会造成段间停顿 +
    # 音色漂移（每段克隆音色有细微差异），所以阈值放宽到一句正常回复不切分，
    # 只有 AI 讲超长段落时才按完整句子切，兼顾首次出声延迟与音色一致性。
    _SPLIT_MAX_CHARS = 120

    @classmethod
    def _split_for_slow_backend(cls, backend: TTSBackend, text: str) -> list[str]:
        """慢速克隆后端的长文本按句子标点切成多段（每段 <= max_chars）。"""
        if backend.name not in cls._SPLIT_SLOW_ENGINES or len(text) <= cls._SPLIT_MAX_CHARS:
            return [text]
        parts = re.split(r"(?<=[。！？!?；;])", text)
        segs: list[str] = []
        cur = ""
        for part in parts:
            if not part:
                continue
            if cur and len(cur) + len(part) > cls._SPLIT_MAX_CHARS:
                segs.append(cur)
                cur = part
            else:
                cur += part
        if cur:
            segs.append(cur)
        return segs or [text]

    async def _speak_with_backend(self, backend: TTSBackend, text: str, emotion: str | None = None) -> None:
        """用指定后端合成并播放（临时文件后缀按后端类型选择）。"""
        segments = self._split_for_slow_backend(backend, text)
        if len(segments) > 1:
            logger.info("%s 长文本分段 %d 段，逐段合成播放", backend.name, len(segments))
        for seg in segments:
            await self._speak_single(backend, seg, emotion=emotion)

    # ===== 【2026-09-18 TTS 链路优化】缓存 / 预合成 / 预热 =====
    def _tts_cache_key(self, text: str, emotion: str | None = None) -> str:
        """缓存键：引擎 + 音色 + 文本 + 情绪（换音色/换引擎/换情绪自动失效）。

        【2026-10-01 F】把 emotion 纳入键：否则情绪句会命中中性预合成缓存，
        导致「开心的语气」实际播出来是中性的。CosyVoice 支持情绪后端尤其需要注意。
        """
        try:
            _n = str(getattr(self._backend, "name", "?"))
            _v = str(getattr(self._backend, "fallback_voice", "") or "")
        except Exception:
            _n, _v = "?", ""
        _e = (emotion or "").strip().lower()
        return "%s|%s|%s|%s" % (_n, _v, (text or "").strip(), _e)

    def _tts_cache_get(self, key: str):
        try:
            return self._tts_pcm_cache.get(key)
        except Exception:
            return None

    def _tts_cache_put(self, key: str, data: bytes) -> None:
        """写入缓存：条目数与总字节双上限，超出按插入顺序淘汰最旧。"""
        try:
            if not data or len(data) > 400000:   # 单条 >20s 音频不缓存
                return
            if len(self._tts_pcm_cache) >= self._tts_cache_max_entries:
                self._tts_pcm_cache.pop(next(iter(self._tts_pcm_cache)), None)
            self._tts_pcm_cache[key] = data
            _tot = sum(len(v) for v in self._tts_pcm_cache.values())
            while _tot > self._tts_cache_max_bytes and len(self._tts_pcm_cache) > 1:
                _tot -= len(self._tts_pcm_cache.pop(next(iter(self._tts_pcm_cache))))
        except Exception:
            pass

    async def _pcm_chunks(self, data: bytes, size: int = 9600):
        """把整段 PCM 切片喂播放器（复用流式播放里的打断检查，0.2s/块）。"""
        for i in range(0, len(data), size):
            yield data[i:i + size]

    def _kick_prefetch(self, d: dict) -> None:
        """入队即起预合成：让云端合成与上一句播放并行（轮到它时首包≈0）。"""
        if not self._tts_prefetch_enabled:
            return
        try:
            text = (d.get("text") or "").strip()
            if not text or len(text) > 80:
                return
            backend = self._backend
            if not getattr(backend, "supports_stream", False):
                return
            loop = asyncio.get_event_loop()
            if not self._tts_warmed and self._tts_warmup_enabled:
                # 每个进程预热一次 WS/TLS 握手（首包里握手占 ~200~400ms），结果丢弃
                self._tts_warmed = True
                loop.create_task(self._warmup_ws(backend))
            key = self._tts_cache_key(text, emotion=d.get("emotion"))
            if key in self._tts_pcm_cache or key in self._tts_prefetch:
                return
            self._tts_prefetch_q[key] = asyncio.Queue()
            self._tts_prefetch[key] = loop.create_task(
                self._prefetch_pcm(backend, text, key, emotion=d.get("emotion")))
        except Exception:
            pass

    async def _prefetch_pcm(self, backend, text: str, key: str, emotion: str | None = None):
        """入队即起预合成，**边收边推入增量队列**（不再攒完整段）。

        队列元素约定：bytes=PCM 块 / None=正常结束 / Exception=失败。
        用 put_nowait（队列无界）避免在 await 点被取消时漏发结束标记；
        结束/失败标记放在 finally 里同步补齐，保证消费侧永不死等。
        【2026-10-01 F】emotion 透传：支持情绪的后端预合成也带情绪，避免缓存串味。
        """
        q = self._tts_prefetch_q.get(key)
        acc = bytearray()
        try:
            _stream = (backend.synthesize_stream(text, emotion=emotion)
                       if getattr(backend, "supports_emotion", False)
                       else backend.synthesize_stream(text))
            async for chunk in _stream:
                if not chunk:
                    continue
                acc.extend(chunk)
                if q is not None:
                    q.put_nowait(bytes(chunk))
            data = bytes(acc)
            if data:
                self._tts_cache_put(key, data)
            return data or None
        except Exception as e:  # noqa: BLE001
            logger.debug("TTS 预合成失败(不影响主链路): %s", e)
            if q is not None:
                q.put_nowait(e)
            return None
        finally:
            if q is not None:
                q.put_nowait(None)

    async def _iter_prefetch(self, q, backend: TTSBackend, text: str,
                             emotion: str | None = None):
        """消费预取的增量队列；预取在产出任何音频前失败则自动改直连重试。

        兼容原语义（原实现 _data 为空时回落到 synthesize_stream）：预取完全没用上
        时不静默丢句，而是自己再合成一次，代价只是多一次网络请求。
        """
        got = False
        failed = None
        while True:
            item = await q.get()
            if item is None:
                break
            if isinstance(item, Exception):
                failed = item
                break
            got = True
            yield item
        if not got:
            if failed is not None:
                logger.debug("预取无产出(%s)，改直连流式重试", failed)
            _s = (backend.synthesize_stream(text, emotion=emotion)
                  if getattr(backend, "supports_emotion", False)
                  else backend.synthesize_stream(text))
            async for _c in _s:
                yield _c

    async def _warmup_ws(self, backend) -> None:
        """预热一次云端 WS/TLS 握手（结果丢弃）。失败不影响主链路。"""
        try:
            async for _ in backend.synthesize_stream("。"):
                break
        except Exception as e:  # noqa: BLE001
            logger.debug("TTS 预热失败(忽略): %s", e)

    def _stream_enabled(self) -> bool:
        """是否允许流式合成（config.proactive.duplex_tts_stream，默认开）。

        关掉后所有后端都退回「整句合成 → 落盘 → 播放」，便于排障或对比音质。
        """
        try:
            raw = getattr(self._cfg, "raw", None) or {}
            pro = raw.get("proactive", {}) or {}
            return bool(pro.get("duplex_tts_stream", True))
        except Exception:  # noqa: BLE001
            return True

    async def _speak_single(self, backend: TTSBackend, text: str, emotion: str | None = None) -> None:
        if getattr(backend, "supports_stream", False) and self._stream_enabled():
            # CosyVoice2 / VoxCPM2 / 百炼实时：原生流式合成，边收 PCM 边播，首块约 1s 出声
            # （整句路径要等合成完成 + 下载 + ffplay 启动，实测 ~6s）
            await self._speak_stream(backend, text, emotion=emotion)
            return
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=backend.output_ext)
        tmp.close()
        playable = None
        try:
            # 只有声明支持情绪的后端才接收 emotion 参数（其余后端签名无此参数，硬传会 TypeError）
            if emotion and getattr(backend, "supports_emotion", False):
                await backend.synthesize(text, tmp.name, emotion=emotion)
            else:
                await backend.synthesize(text, tmp.name)
            playable = backend.ensure_playable(tmp.name)
            self._save_wav_copy(playable)  # 存档供聊天页语音回放
            await asyncio.to_thread(self._play_file, playable)
        finally:
            for p in {tmp.name, playable}:
                if p:
                    try:
                        os.unlink(p)
                    except OSError:
                        pass

    def _trim_silence_edges(pcm: "np.ndarray", sr: int, thresh: int, min_run_s: float = 0.06) -> "np.ndarray":
        """去除 int16 PCM 首尾静音（幅值绝对值 <= thresh 的样本），返回裁剪后的数组。

        【2026-09-23】根治「扭扭捏捏」：云端 cosyvoice-v3.5-flash 每句前垫 ~0.4s 纯静音、
        句尾 ~0.2~0.5s，导致每句话晚半拍出声、对话一卡一卡。这里把首尾死气裁掉。
        min_run_s 守卫：若首/尾静音短于 60ms（可能是柔软起音/换气），不裁，避免切掉真语音。
        """
        if pcm.size == 0:
            return pcm
        n = pcm.size
        min_run = int(min_run_s * sr)
        # 首静音
        i = 0
        while i < n and abs(int(pcm[i])) <= thresh:
            i += 1
        if 0 < i < min_run:  # 短静音：保护柔软起音，不裁
            i = 0
        # 尾静音
        j = n - 1
        while j >= 0 and abs(int(pcm[j])) <= thresh:
            j -= 1
        if (n - 1 - j) < min_run and j < n - 1:  # 短静音：不裁
            j = n - 1
        if i > j:
            return pcm[0:0]
        return pcm[i:j + 1]

    async def _speak_stream(self, backend: TTSBackend, text: str, emotion: str | None = None) -> None:
        """CosyVoice2 流式播放：sounddevice 边收边播，首块到达即可出声。

        首块到达前失败（如服务未启动）会继续抛给上层走兜底链；
        已经开始播放后失败只记日志，不打断已播部分。
        """
        import sounddevice as sd
        import numpy as np

        fs = getattr(backend, "stream_sample_rate", 24000)
        started = False
        out = None
        _pcm_acc = bytearray()  # 【2026-09-15】累积 int16 PCM，供聊天页语音回放存档
        # 进程内播放(CosyVoice2 sounddevice)：若 driver 已用进程环回排除自身声音则无需闸门，
        # 否则（旧系统回退）置 INPROC_SPEAK_GATE 让采集端静音，避免模型自言自语。
        use_gate = not driver_excludes_host()
        if use_gate:
            INPROC_SPEAK_GATE.set()
        # 【2026-09-17 通话追踪】合成请求发出（此时刻 - speak_out = 上层门控开销）
        _key = self._tts_cache_key(text, emotion=emotion)
        _ctrace("tts_begin", engine=str(getattr(backend, "name", "?")), chars=len(text))
        try:
            # 只有支持情绪的后端才接收 emotion（CosyVoice2 等流式后端签名无此参数）
            _cached = self._tts_cache_get(_key) if self._tts_cache_enabled else None
            _q = self._tts_prefetch_q.pop(_key, None)
            _task = self._tts_prefetch.pop(_key, None)
            if _cached is not None:
                # 命中缓存：跳过网络合成与握手，直接播 PCM（首包 ≈ 0）
                _ctrace("tts_cache_hit", chars=len(text))
                stream = self._pcm_chunks(_cached)
            elif _q is not None:
                # 【2026-09-23 修复】消费预取**增量**流：首块一到即出声，
                # 不再 await 整段合成（原写法在此处把流式退化成整句）。
                _ctrace("tts_prefetch_hit", chars=len(text), mode="stream")
                stream = self._iter_prefetch(_q, backend, text, emotion=emotion)
            else:
                if _task is not None:
                    _task.cancel()   # 只有 task 没有队列（如打断后清过）→ 放弃预取
                stream = (backend.synthesize_stream(text, emotion=emotion)
                          if getattr(backend, "supports_emotion", False)
                          else backend.synthesize_stream(text))
            _stream_start = time.monotonic()
            # 【2026-09-23 修复·句首死气】云端 cosyvoice 每句前垫 ~0.4s 纯静音，让每句话
            # "晚半拍才出声"、对话一卡一卡（扭扭捏捏）。这里缓冲首段、检测到首个有效语音
            # 样本后再开声，跳过死气；最多缓冲 0.6s 保底（防止极端情况永不触发）。
            _lead_buf = bytearray()
            _lead_scanned = 0
            _speech_start = None
            _SIL_THRESH = 100  # int16 幅值阈值：数字静音(~0-10)远低于此，柔软起音(>100)保留
            async for chunk in stream:
                if time.monotonic() - _stream_start > 25.0:
                    logger.warning("流式播放超过 25s 仍未结束，强制收尾(防队列饿死)")
                    break
                # 【2026-09-18 修复】打断窗口内立即收尾，避免继续往已停的流里写
                if self._interrupted or (self._interrupt_until and time.monotonic() < self._interrupt_until):
                    break
                _pcm_acc.extend(chunk)
                if not started:
                    _lead_buf.extend(chunk)
                    # 【2026-09-23 修复·BufferError】必须用 bytes 副本做 frombuffer 扫描：
                    # 直接 np.frombuffer(_lead_buf) 会对 bytearray 创建 buffer export，导致
                    # 下一轮 _lead_buf.extend() 抛 BufferError(object cannot be re-sized)，
                    # 整句云端合成崩掉、掉进 edge-tts 兜底（微软晓伊）。用 bytes(_lead_buf)
                    # 不可变副本，不锁定原 bytearray，后续 extend 仍安全。
                    _arr = np.frombuffer(bytes(_lead_buf), dtype=np.int16)
                    _n = len(_arr)
                    while _lead_scanned < _n and abs(int(_arr[_lead_scanned])) <= _SIL_THRESH:
                        _lead_scanned += 1
                    if _speech_start is None and _lead_scanned < _n:
                        _speech_start = _lead_scanned
                    if _speech_start is not None or len(_lead_buf) >= int(0.6 * fs) * 2:
                        out = sd.OutputStream(samplerate=fs, channels=1, dtype="int16")
                        out.start()
                        started = True
                        # 【2026-09-18 修复】把正在播放的流句柄交给 self._sd_out，否则
                        # interrupt()->stop() 只能停 None（流式子进程/进程内流不会被切断）→
                        # 用户打断后旧句仍播完（"文字停了但声音没停"）。
                        self._sd_out = out
                        # 【2026-09-17 通话追踪】首个有效语音块入设备 = 用户真正听见的时刻
                        _start_byte = (_speech_start or 0) * 2
                        _ctrace("tts_first_audio",
                                engine=str(getattr(backend, "name", "?")),
                                chunk_bytes=(len(_lead_buf) - _start_byte))
                        if len(_lead_buf) > _start_byte:
                            await asyncio.to_thread(out.write, np.frombuffer(_lead_buf[_start_byte:], dtype=np.int16))
                else:
                    # sounddevice 只接受 numpy 数组；write 阻塞节流：内部缓冲满时
                    # 等待播放进度，避免无界积压
                    await asyncio.to_thread(out.write, np.frombuffer(chunk, dtype=np.int16))
        except Exception as e:  # noqa: BLE001
            if not started:
                raise
            logger.warning("流式播放中断（已播部分不受影响）: %s", e)
        finally:
            # 【2026-09-23 修复·首尾死气】出声后把整段首尾静音裁掉再存档/缓存：
            # 云端 cosyvoice 每句垫 ~0.4s 句首 + ~0.2~0.5s 句尾静音，回放/缓存若带回去
            # 会让"扭扭捏捏"复发。在线播放已靠 _lead_buf 跳过句首，这里补裁句首+句尾。
            if _pcm_acc:
                try:
                    _full = np.frombuffer(bytes(_pcm_acc), dtype=np.int16)
                    _trimmed = self._trim_silence_edges(_full, fs, 100, min_run_s=0.06)
                    if _trimmed.size > 0:
                        _pcm_acc = bytearray(_trimmed.tobytes())
                except Exception:  # noqa: BLE001
                    logger.debug("首尾静音裁剪异常（原样存档）", exc_info=True)
            # 【2026-09-18】整句播完 → 存进缓存，下次同文本直接复用（高频短句收益最大）
            if self._tts_cache_enabled and started and _pcm_acc:
                self._tts_cache_put(_key, bytes(_pcm_acc))
            if use_gate:
                # 尾音静音：吃掉采集缓冲延迟（约 0.5s），避免模型听到自己刚说完的尾音接话
                # 【2026-09-18】原为阻塞式 time.sleep，会卡死整个事件循环导致 TTS 队列饿死
                # （表现：文字已上屏却迟迟不出声 / 音文不同步）。改 await 让出控制权。
                await asyncio.sleep(0.6)
                INPROC_SPEAK_GATE.clear()
            if out is not None:
                try:
                    out.stop()
                except Exception:  # noqa: BLE001
                    pass
                out.close()
                if self._sd_out is out:
                    self._sd_out = None
            # 【2026-09-15】流式路径原先不落盘 → 聊天页语音回放拿不到音频。
            # 这里把累积的 PCM 写一份存档 wav（与 _play_pcm 走同一出口，不影响实时性）。
            if _pcm_acc:
                try:
                    self._save_pcm_wav(np.frombuffer(bytes(_pcm_acc), dtype=np.int16), fs)
                except Exception:  # noqa: BLE001
                    logger.debug("流式语音存档失败", exc_info=True)

    def _play_via_ffplay(self, path: str) -> bool:
        """用 ffplay **独立进程**播放（-nodisp 无窗口、阻塞到播完），成功返回 True。

        【2026-09-10 决定性修复】本机实测：python 进程内 winsound / sounddevice /
        pygame 播放存在**数十秒延迟**（UI 早已显示"播放完成"却迟迟没声），而
        **独立进程**（ffplay / 系统播放器双击）都**立即**出声。
        故 TTS 播放统一优先走 ffplay 独立进程，失败再回退 pygame。
        """
        import shutil
        import subprocess
        import sys as _sys

        ff = shutil.which("ffplay")
        if not ff:
            for _c in (
                os.path.expanduser(r"~\AppData\Local\Microsoft\WinGet\Links\ffplay.exe"),
                os.path.join(os.path.dirname(_sys.executable), "ffplay.exe"),
            ):
                if os.path.exists(_c):
                    ff = _c
                    break
        if not ff or not os.path.exists(path):
            return False
        # ffplay 是独立子进程（不同 pid），进程环回无法排除第二 pid，故用临时闸门：
        # 播放期间采集端把环回静音（用户照常能听到达妮娅，只是不进环回）。
        FFPLAY_PLAYING.set()
        # 【通话打断】改为 Popen + 轮询等待：原本 subprocess.run 会一直阻塞到播完，
        # 打断时杀不掉进程。轮询期间每 50ms 检查中断标志，命中即 terminate。
        # 播放主体仍是 ffplay 独立进程（进程内播放有数十秒延迟），行为与改动前一致。
        proc = None
        # 【2026-09-17 通话追踪】ffplay 独立进程启动 = 非流式路径的"首音频"时刻
        _ctrace("tts_play", file=os.path.basename(path))
        try:
            proc = subprocess.Popen(
                [ff, "-nodisp", "-autoexit", "-loglevel", "quiet", path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=(0x08000000 if os.name == "nt" else 0),  # CREATE_NO_WINDOW
            )
            self._ffplay_proc = proc
            _waited = 0.0
            while proc.poll() is None:
                if self._interrupted:
                    self._kill_ffplay()
                    break
                if _waited > 600:  # 对齐原 subprocess.run(timeout=600)，防 ffplay 卡死永久阻塞
                    logger.warning("ffplay 播放超时(600s)，强制终止")
                    self._kill_ffplay()
                    break
                time.sleep(0.05)
                _waited += 0.05
            return True
        except Exception:  # noqa: BLE001
            logger.warning("ffplay 播放失败，回退 pygame: %s", path, exc_info=True)
            return False
        finally:
            self._ffplay_proc = None
            # 尾音静音：吃掉采集缓冲延迟，避免模型听到自己刚说完的尾音接话
            time.sleep(0.6)
            FFPLAY_PLAYING.clear()

    def _play_file(self, path: str) -> None:
        # 【2026-09-17】指定了输出设备 → 优先定向播放（同上）
        if getattr(self, "_out_device", "default") not in ("default", "", None):
            if self._play_file_on_device(path, self._out_device):
                return
        # 优先 ffplay 独立进程（绕开 python 进程内音频库在本机的延迟/会话问题）
        if self._play_via_ffplay(path):
            return
        # pygame 进程内播放(host pid)：driver 未排除时置闸门，避免模型自言自语
        use_gate = not driver_excludes_host()
        if use_gate:
            INPROC_SPEAK_GATE.set()
        try:
            import pygame
            if not self._pygame_inited:
                pygame.mixer.init()
                self._pygame_inited = True
            pygame.mixer.music.load(path)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                pygame.time.Clock().tick(30)
        finally:
            if use_gate:
                time.sleep(0.6)  # 尾音静音：吃掉采集缓冲延迟
                INPROC_SPEAK_GATE.clear()

    async def _pyttsx3_speak(self, text: str) -> None:
        # pyttsx3 进程内播放(host pid)：driver 未排除时置闸门，避免模型自言自语
        use_gate = not driver_excludes_host()
        if use_gate:
            INPROC_SPEAK_GATE.set()
        try:
            def _speak() -> None:
                try:
                    import pyttsx3
                    engine = pyttsx3.init()
                    engine.say(text)
                    engine.runAndWait()
                except Exception:
                    logger.exception("pyttsx3 降级也失败")

            await asyncio.to_thread(_speak)
        finally:
            if use_gate:
                time.sleep(0.6)  # 尾音静音：吃掉采集缓冲延迟
                INPROC_SPEAK_GATE.clear()

    def stop(self) -> None:
        if self._pygame_inited:
            try:
                import pygame
                pygame.mixer.music.stop()
            except Exception:
                pass
        if self._sd_out is not None:
            try:
                self._sd_out.stop()
            except Exception:
                pass
            self._sd_out = None
