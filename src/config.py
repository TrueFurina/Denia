"""配置加载与环境探测。

读取 config.yaml，提供点号访问（cfg.ns.ollama.host），
并在启动阶段探测 Ollama / Vosk 模型 / 麦克风可用性，把降级结果挂到 runtime_status。
支持多角色配置：active_character_id + characters 数组。
"""
from __future__ import annotations

import copy
import logging
import os
import ssl
from types import SimpleNamespace
from typing import Any, Optional

import yaml

from src import secret_box

logger = logging.getLogger(__name__)


def _ensure_localhost_proxy_bypass() -> None:
    """让本机地址（Ollama / 本地引擎 / 本机 HTTP 服务）绕过系统代理。

    背景（2026-10-16 实测）：Windows 上 httpx 会读取注册表里的系统代理，且
    **不认** ProxyOverride 绕过列表；而 urllib 认。于是当用户开着代理开关、但代理
    进程没起来时（Clash/v2ray 默认 127.0.0.1:7890，极为常见）：
      · urllib 路径仍能连 127.0.0.1；
      · httpx 路径连 127.0.0.1 都失败 → 本机明明在跑的 Ollama 被判"未运行"，
        报错还误导用户"请检查 Ollama 是否已下载并在运行"。
    实测：死代理 + 不设 NO_PROXY → ConnectError；设 NO_PROXY → 200。

    这里只做"本地地址绕过"，不干预用户对外网代理的既有设置。
    """
    _local = ("localhost", "127.0.0.1", "::1")
    for name in ("NO_PROXY", "no_proxy"):
        cur = os.environ.get(name, "")
        parts = [p.strip() for p in cur.split(",") if p.strip()]
        for host in _local:
            if host not in parts:
                parts.append(host)
        os.environ[name] = ",".join(parts)


_ensure_localhost_proxy_bypass()


def _to_ns(obj: Any) -> Any:
    """递归把 dict/list 转成 SimpleNamespace / list，支持点号访问。"""
    if isinstance(obj, dict):
        return SimpleNamespace(**{k: _to_ns(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return [_to_ns(x) for x in obj]
    return obj


class Config:
    def __init__(self, raw: dict[str, Any], path: str = "") -> None:
        self.raw = raw
        self.path = path  # 配置文件路径（load_config 时记录），供 save() 持久化
        # 【阶段 B4】加载即校验：缺字段/类型错/取值非法都会被收集，
        # 由 `--doctor` 集中呈现。**不抛异常**——配置写错不该让软件打不开，
        # 但必须大声说出来，不能静默回落（那才是"我配了却没生效"的根因）。
        self.config_issues: list = []
        try:
            from src.config_schema import apply_defaults as _apply_defaults
            from src.config_schema import validate as _validate

            self.config_issues.extend(_apply_defaults(self.raw))
            self.config_issues.extend(_validate(self.raw))
        except Exception as _e:  # noqa: BLE001  校验器自己出问题也不能拖垮启动
            logger.debug("[配置] 校验跳过：%s", _e)
        self.ns = _to_ns(raw)
        self._characters_map: dict[str, SimpleNamespace] = {}
        self._active_character_id: str = ""
        self._parse_characters()
        self._parse_voice_profiles()
        self._resolve_engine_paths()
        # 桌面看屏循环总闸门（只被 ScreenCapture._enabled 与 VisionAnalyzer._disabled
        # 读取，即专门管"是否持续盯桌面"），三个条件缺一不可：
        #   perception.vision.enabled            感知层总开关
        #   vision_models.enabled                视觉感知＝模型能不能看图（看图/agent/看屏共用）
        #   perception.vision.desktop_watch_enabled  桌面看屏＝要不要一直盯着桌面截图搭话
        # 2026-09-28 拆分：此前把「视觉感知」直接当成桌面看屏开关（09-27 我为了修
        # "开关失效"把两者 AND 在一起），导致关掉视觉感知＝连看图能力一起禁用，
        # 且无法做到"保留视觉模型、只关掉盯桌面"。现两者各司其职。
        # 桌面看屏缺省（键缺失）按运行方式：本地档默认开、云端档默认关。
        # 显式写了 desktop_watch_enabled 则以用户手动选择为准（手动开关优先级最高）。
        _dw_raw = getattr(self.ns.perception.vision, "desktop_watch_enabled", None)
        # 【2026-10-04 19:55 修正·开关语义混淆的第二处】此前还会回落到
        # `vision_models.screen_watch_enabled` / `game_cloud_watch` 当看屏默认值。
        # 但引导页 `_features_to_engine_config` 会把 `screen_watch_enabled` 按
        # 「是否勾了看屏能力」写盘，而 `vision_models.enabled` 又被拿去当**看图总闸**
        # —— 一个键两种语义，正是 19:20「发图被丢弃 → 模型改用 see_screen 截屏」
        # 的根因链上游。此处只认真正生效的那个键（desktop_watch_enabled）+ 档位默认，
        # 避免第三个开关再被引进来搅浑。
        if _dw_raw is None:
            _dw_raw2 = (getattr(getattr(self.ns, "vision_models", None),
                                "screen_watch_enabled", None))
            if _dw_raw2 is not None:
                _dw_raw = _dw_raw2
        _duplex_mode = getattr(getattr(self.ns, "proactive", None), "duplex_mode", None)
        _is_cloud = _duplex_mode in ("realtime", "cloud")
        _desktop_watch = bool(_dw_raw) if _dw_raw is not None else (not _is_cloud)
        # 运行时探测结果（启动阶段填充）
        self.runtime_status = SimpleNamespace(
            ollama_ok=False,
            vision_model_ok=False,
            fallback_model_ok=False,
            llm_provider=self._resolve_provider(),
            vosk_model_ok=False,
            mic_ok=False,
            # ⚠️ 这里是「**后台桌面截图循环**」的闸（`screen_capture`），**不是**「发图」的闸。
            # ⚠️ 二者刻意区分：
            #   · 发图（用户主动发图问一句）→ 由 `VisionRouter.vision_chat` 自己判
            #     `_perception_enabled()`，**不经过** runtime_status ⇒ 桌面看屏关着
            #     完全不影响发图（10-04 事故的正解）。
            #   · 桌面循环（后台常驻盯屏幕）→ 必须真的调得动 VLM，所以这里
            #     **需要** `vision_models.enabled`（模型可用性）。它以前是"被误关"
            #     才出问题——根因在引导页把 enabled 按看屏意图写盘，已于 19:47 修掉
            #     （enabled 恒 True，看屏意图改写 screen_watch_enabled）。
            vision_enabled=(
                bool(self.ns.perception.vision.enabled)
                and bool(getattr(getattr(self.ns, "vision_models", None), "enabled", True))
                and _desktop_watch
            ),
            voice_enabled=not bool(self.ns.runtime.text_mode_only) and bool(self.ns.voice.mic_enabled),
            degradations=[],  # list[str] 降级说明
        )

    def _resolve_engine_path(self, p: str) -> str:
        """把引擎路径归一：绝对且存在→原样；相对→按 <app根> 或 DANIYA_COMNI_HOME 解析。

        仅做路径解析，不触碰文件。发行版 config 写相对路径 Comni\\... 也能在任意机器上
        正确定位到 <app>/Comni；开发者机的 H:\\Comni 绝对路径原样透传（不强制搬家）。
        """
        if not p:
            return p
        p2 = os.path.expandvars(str(p))
        if os.path.isabs(p2):
            return os.path.normpath(p2)
        base = os.environ.get("DANIYA_COMNI_HOME") or self._app_root or os.getcwd()
        return os.path.normpath(os.path.join(base, p2))

    def _resolve_engine_paths(self) -> None:
        """启动期把 Comni 引擎相关路径解析为绝对路径（写回 raw + ns）。

        修「分发缺陷」：软件发出去不该让用户自备 Comni。这里让配置既能写相对路径
        （随包 <app>/Comni 即可），也兼容开发者机的绝对路径 H:\\Comni。
        """
        self._app_root = os.path.dirname(self.path) if self.path else os.getcwd()
        pro = self.raw.setdefault("proactive", {})
        loe = pro.setdefault("local_omni_engine", {})
        for key in ("server_dir", "comni_resources_dir", "python_embed"):
            if key in loe and loe[key]:
                loe[key] = self._resolve_engine_path(loe[key])
        if "duplex_local_omni_exe" in pro and pro.get("duplex_local_omni_exe"):
            pro["duplex_local_omni_exe"] = self._resolve_engine_path(
                pro["duplex_local_omni_exe"])
        # 重新构建 ns，使点号访问拿到解析后的绝对路径（raw 同步更新，保存即持久化）
        self.ns = _to_ns(self.raw)

    def _resolve_provider(self) -> str:
        """解析 LLM 提供商（默认 openai，兼容旧配置无 llm 段）。

        改默认 ollama→openai：纯聊天新人零本地依赖（云 API 即可），不再暗需 Ollama。
        已有配置写了 provider 的（包括 ollama）原样透传。
        """
        llm_ns = getattr(self.ns, "llm", None)
        return getattr(llm_ns, "provider", "openai") if llm_ns else "openai"

    def desktop_watch_effective(self) -> bool:
        """桌面看屏互动的有效开关值（模式感知的默认值）。

        未写 desktop_watch_enabled 时：本地档（duplex_mode 为 local/local_omni 或缺省）默认开、
        云端档（realtime/cloud）默认关；显式写过则以用户手动选择为准。供后端 getVisionSettings
        回显与设置页一致，避免「云端却默认开看屏」踩中默认陷阱。
        """
        per_v = (self.raw.get("perception") or {}).get("vision") or {}
        raw = per_v.get("desktop_watch_enabled", None)
        if raw is not None:
            return bool(raw)
        dmode = (self.raw.get("proactive") or {}).get("duplex_mode")
        return dmode not in ("realtime", "cloud")

    def _parse_characters(self) -> None:
        """解析角色列表，建立 ID -> 配置映射。"""
        raw_chars = self.raw.get("characters", []) or []
        for ch in raw_chars:
            ch_id = ch.get("id", "")
            if ch_id:
                self._characters_map[ch_id] = _to_ns(ch)
        self._active_character_id = self.raw.get("active_character_id", "")
        if not self._active_character_id and self._characters_map:
            # 默认用第一个角色
            self._active_character_id = next(iter(self._characters_map))

    def refresh_characters(self) -> None:
        """运行时重解析角色表（保存/删除角色后调用，无需重启）。

        只重建角色映射，不覆盖 raw 中其他字段，active_character_id 从 raw 恢复。
        """
        self._characters_map.clear()
        self._parse_characters()

    @property
    def active_character_id(self) -> str:
        return self._active_character_id

    def get_active_character(self) -> Optional[SimpleNamespace]:
        """获取当前激活角色的配置（persona + voice + model + model_3d）。"""
        return self._characters_map.get(self._active_character_id)

    def get_character_by_id(self, char_id: str) -> Optional[SimpleNamespace]:
        """按 ID 获取角色配置。"""
        return self._characters_map.get(char_id)

    def list_characters(self) -> list[SimpleNamespace]:
        """列出所有角色的基本信息（id, name）。"""
        return list(self._characters_map.values())

    def switch_character(self, char_id: str) -> bool:
        """切换激活角色。返回是否成功。"""
        if char_id in self._characters_map:
            self._active_character_id = char_id
            self.raw["active_character_id"] = char_id
            logger.info("切换角色: %s → %s", self._active_character_id, char_id)
            return True
        logger.warning("角色不存在: %s", char_id)
        return False

    def _parse_voice_profiles(self) -> None:
        """解析全局音色档列表 voice_profiles：id -> 原始 dict。

        保留原始 dict（不下转 ns），resolve 时深拷贝一层，避免 TTS 在 voice_cfg 上
        挂 emotion_tags 时污染全局音色档（多角色共用同一档会互相串味）。
        """
        raw_profiles = self.raw.get("voice_profiles", []) or []
        self._voice_profiles_map: dict[str, dict] = {}
        for p in raw_profiles:
            if not isinstance(p, dict):
                continue
            pid = p.get("id", "")
            if pid:
                self._voice_profiles_map[pid] = p

    def refresh_voice_profiles(self) -> None:
        """运行时重解析音色档表（新增/修改/删除音色档后必须调用）。

        关键：_voice_profiles_map 只在 __init__ 里建一次，而 save/delete/assign
        三个入口过去只写 raw + 重建 ns，从不重建这张表 → 运行时
        get_active_voice_config() 拿到的是「启动那一刻」的旧表：新建的档查不到
        （静默回退角色内联 voice）、改过的档参数不生效（换 voice_id/api_key 无效）、
        删掉的档仍能命中（幽灵档）。UI 列表读的是 raw 所以是新的 → 两边不一致。
        """
        self._parse_voice_profiles()

    def list_voice_profiles(self) -> list[dict]:
        """列出全部音色档（原始 dict，UI 直接序列化展示）。"""
        return list(self._voice_profiles_map.values())

    def get_voice_profile(self, pid: str) -> Optional[dict]:
        """按 id 取单个音色档原始配置。"""
        return self._voice_profiles_map.get(pid)

    def get_active_voice_config(self) -> Optional[SimpleNamespace]:
        """快捷方法：获取当前角色的 voice 配置。

        支持「音色档」架构（2026-09-13）：若角色配了 voice_profile: <id> 且全局
        voice_profiles 存在该档，则以该档为准（音色档优先），返回其深拷贝。
        档缺失 / 角色未引用时，退回角色自身 char.voice（零回归）。
        设计取舍：角色卡里「选音色档」= 声音立即变该档，故档优先于角色内联 voice；
        角色内联 voice 仅作「无档」时的回退，不被用来覆盖已选档。
        resolve 返回深拷贝，TTS 挂 emotion_tags 不会污染全局档（多角色共用不串味）。
        """
        char = self.get_active_character()
        if not char:
            return None
        profile_id = getattr(char, "voice_profile", None)
        if profile_id:
            prof = self._voice_profiles_map.get(profile_id)
            if prof:
                return _to_ns(dict(prof))  # 深拷贝一层，避免共享可变状态
        return getattr(char, "voice", None)

    def get_cloud_voice_id(self, char_id: str = "") -> str:
        """取角色绑定的云端音色 id；角色未绑定则回退全局 cloud_omni.voice。

        统一声音绑定（2026-09-25）：一份参考音上传后，本地链路用 voice_profile、
        云端链路用 cloud_voice_id，两套并存互不覆盖。角色层优先，没配则回退
        config 的 cloud_omni.voice（全局默认克隆音色），保证老配置零回归。
        """
        char = self.get_character_by_id(char_id) if char_id else self.get_active_character()
        if char is not None:
            v = getattr(char, "cloud_voice_id", "") or ""
            if v:
                return v
        omni = getattr(self.ns, "cloud_omni", None)
        return (getattr(omni, "voice", "") or "") if omni is not None else ""

    def get_character_ref_audio(self, char_id: str = "") -> str:
        """取角色绑定的参考音路径——即「一处上传、两链路共用」的那份参考音。"""
        char = self.get_character_by_id(char_id) if char_id else self.get_active_character()
        return (getattr(char, "ref_audio", "") or "") if char is not None else ""

    def get_default_emotion_tags(self) -> dict:
        """全局默认情绪壳：情绪(state)→语音表达标记（角色 voice.emotion_tags 覆盖全局）。

        与角色「风格(trait)」无关——风格由克隆参考音自带；这里只定「怎么表达情绪」。
        供所有角色 / 客制化克隆继承，缺省项再由 tts_backends 内置通用表兜底。
        """
        val = self.raw.get("default_emotion_tags", {}) or {}
        return val if isinstance(val, dict) else {}

    def get_active_model_config(self) -> Optional[SimpleNamespace]:
        """快捷方法：获取当前角色的 model 配置。"""
        char = self.get_active_character()
        if char:
            return char.model
        return None

    def resolve_vision_model(self) -> str:
        """解析角色专属视觉模型，null 则用全局默认。"""
        model_cfg = self.get_active_model_config()
        if model_cfg and model_cfg.vision_model:
            return model_cfg.vision_model
        return self.ns.ollama.vision_model

    def resolve_fallback_model(self) -> str:
        """解析角色专属降级模型，null 则用全局默认。"""
        model_cfg = self.get_active_model_config()
        if model_cfg and model_cfg.fallback_model:
            return model_cfg.fallback_model
        return self.ns.ollama.fallback_model

    def add_degradation(self, msg: str) -> None:
        self.runtime_status.degradations.append(msg)
        logger.warning("降级: %s", msg)

    def save(self) -> None:
        """把当前 raw 写回配置文件（内存修改持久化）。无 path 时忽略。

        【C9-1】写盘前把明文 api_key 加密成 `dpapi:` 密文——**只加密写盘副本**，
        内存 self.raw 保持明文，调用方零感知；加密失败降级为明文（与现状一致）。
        """
        if not self.path:
            return
        try:
            data = copy.deepcopy(self.raw)
            secret_box.seal_tree(data)
            with open(self.path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True,
                          default_flow_style=False, sort_keys=False)
        except Exception:  # noqa: BLE001
            logger.exception("保存配置失败: %s", self.path)


def load_config(path: str) -> Config:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raw = {}
    # 【C9-1】读盘时把 `dpapi:` 密文解密回内存明文。解密失败（典型：配置
    # 从别的机器拷来，DPAPI 不认）不抛异常——保留密文并转成降级提示。
    _ok, failed = secret_box.unseal_tree(raw)
    cfg = Config(raw, path=path)
    for p in failed:
        cfg.add_degradation(
            f"密钥 {p} 是 DPAPI 加密值且本机无法解密——"
            "若配置是从别的电脑拷来的，请重新填写该 API Key")
    return cfg


# ==================== 环境探测 ====================

def check_ollama(cfg: Config) -> None:
    """探测 Ollama 连通性与模型可用性。失败不抛异常，仅标记降级。"""
    host = cfg.ns.ollama.host
    try:
        from ollama import Client
    except Exception as e:  # noqa: BLE001
        cfg.add_degradation(f"未安装 ollama 库: {e}")
        return

    try:
        client = Client(host=host)
        resp = client.list()
        # 兼容 dict / Pydantic 对象两种返回
        if isinstance(resp, dict):
            models = resp.get("models", []) or []
        else:
            models = getattr(resp, "models", []) or []
        names: list[str] = []
        for m in models:
            n = m.get("model") if isinstance(m, dict) else (getattr(m, "model", None) or getattr(m, "name", None))
            if n:
                names.append(n)
    except Exception as e:  # noqa: BLE001
        cfg.add_degradation(f"Ollama 不可用 ({host}): {e}")
        return

    cfg.runtime_status.ollama_ok = True
    vision_tag = cfg.resolve_vision_model()
    fallback_tag = cfg.resolve_fallback_model()
    cfg.runtime_status.vision_model_ok = any(n == vision_tag or n.startswith(vision_tag + ":") for n in names)
    cfg.runtime_status.fallback_model_ok = any(n == fallback_tag or n.startswith(fallback_tag + ":") for n in names)

    if not cfg.runtime_status.vision_model_ok:
        cfg.add_degradation(f"视觉模型 {vision_tag} 未 pull，运行 `ollama pull {vision_tag}` 后可启用视觉")
        if not cfg.runtime_status.fallback_model_ok:
            cfg.add_degradation(f"降级模型 {fallback_tag} 也未 pull，对话功能将不可用")


def check_openai_compatible(cfg: Config) -> None:
    """探测 OpenAI 兼容云端服务（轻量 GET /models）。"""
    openai_cfg = getattr(getattr(cfg.ns, "llm", None), "openai", None)
    base_url = (getattr(openai_cfg, "base_url", "") or "").rstrip("/")
    api_key = getattr(openai_cfg, "api_key", "") or ""
    model = getattr(openai_cfg, "model", "") or ""
    vision = bool(getattr(openai_cfg, "vision", True))
    if not base_url or not model:
        cfg.add_degradation(
            "OpenAI 兼容服务未配置（base_url/model 为空），请到 config.yaml 的 llm.openai 填写"
        )
        return
    try:
        import httpx
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        r = httpx.get(f"{base_url}/models", headers=headers, timeout=10)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        cfg.add_degradation(f"OpenAI 兼容服务不可用 ({base_url}): {e}")
        return
    cfg.runtime_status.ollama_ok = True
    cfg.runtime_status.vision_model_ok = vision
    cfg.runtime_status.fallback_model_ok = True


def check_minicpm(cfg: Config) -> None:
    """探测 MiniCPM-o Realtime 云端服务（WS 连接 + 等 queue_done）。

    【A8 · 2026-09-29】降级为「可选探测，非默认来源」：若 endpoint 指向官方免费网关
    （wss://minicpmo45.modelbest.cn），该免费服务不稳定（仅 14 共享端口、多软件抢），
    已不再是默认云端来源——云端双工/视觉必须走用户自己绑的 Key。此时不再把它标记为
    可用云端（避免「没绑 Key 却显示云端在线」的假象），而是记一条降级提示引导去绑 Key。
    仅当用户显式把 minicpm 指向一个非免费的自定义端点时，才走原探测逻辑。
    """
    mc = getattr(getattr(cfg.ns, "llm", None), "minicpm", None)
    endpoint = (getattr(mc, "endpoint", "") or "").strip()
    if not endpoint:
        cfg.add_degradation("MiniCPM endpoint 未配置，请检查 config.yaml 的 llm.minicpm")
        return
    # A8：免费官方网关不再作为默认云端来源——提示用户云端应绑自己的 Key。
    if "minicpmo45.modelbest.cn" in endpoint:
        cfg.add_degradation(
            "MiniCPM 官方免费网关已不再是默认云端来源（不稳定）："
            "云端双工/视觉请改用你自己绑的 Key（阿里云百炼等）。未绑 Key 时云端不可用。")
        return

    import asyncio
    import json

    async def _probe() -> bool:
        import websockets

        async def _loop() -> bool:
            # 整体超时（2026-08-30 修复）：旧实现 while True 等 queue_done，但
            # minicpmo45 实测只持续发 session.queued / session.queue_update、从不发
            # queue_done，导致 recv 的 25s 超时永不触发（消息每几秒来一条）→ 主线程
            # 死循环 → 启动卡在"加载配置文件"。改为：收到任意会话状态即视为服务在线
            # （可达性探测无需等到生成完成），并加整体超时兜底，杜绝任何无限挂起。
            async with websockets.connect(endpoint, open_timeout=15, ping_interval=None,
                                          ssl=ssl._create_unverified_context()) as ws:
                while True:
                    raw = await ws.recv()
                    msg = json.loads(raw)
                    t = msg.get("type")
                    if t in ("session.queue_done", "session.queued", "session.queue_update"):
                        return True
                    if t == "error":
                        logger.warning("MiniCPM 探测返回错误: %s", msg.get("error"))
                        return False

        try:
            return await asyncio.wait_for(_loop(), timeout=20)
        except asyncio.TimeoutError:
            logger.warning("MiniCPM 探测整体超时（20s，按不可用处理）: %s", endpoint)
            return False
        except Exception as e:  # noqa: BLE001
            logger.warning("MiniCPM 探测异常: %s", e)
            return False

    try:
        ok = asyncio.run(_probe())
    except Exception as e:  # noqa: BLE001
        cfg.add_degradation(f"MiniCPM Realtime 不可用 ({endpoint}): {e}")
        return
    if not ok:
        cfg.add_degradation(f"MiniCPM Realtime 探测失败 ({endpoint})")
        return
    cfg.runtime_status.ollama_ok = True
    cfg.runtime_status.vision_model_ok = True
    cfg.runtime_status.fallback_model_ok = True


def check_llm(cfg: Config) -> None:
    """按 llm.provider 分发探测。"""
    provider = cfg.runtime_status.llm_provider or "ollama"
    if provider == "openai":
        check_openai_compatible(cfg)
    elif provider == "minicpm":
        check_minicpm(cfg)
    else:
        check_ollama(cfg)


def check_vosk_model(cfg: Config) -> None:
    """探测 Vosk 模型目录是否存在。"""
    if not cfg.runtime_status.voice_enabled:
        return
    path = cfg.ns.voice.asr.model_path
    if not path:
        cfg.runtime_status.vosk_model_ok = True  # 留空则运行时尝试 lang="zh-cn" 自动下载
        return
    if os.path.isdir(path):
        cfg.runtime_status.vosk_model_ok = True
    else:
        cfg.add_degradation(
            f"Vosk 模型未找到 ({path})，下载并解压: "
            "https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip"
        )


def check_mic(cfg: Config) -> None:
    """探测麦克风是否可用。"""
    if not cfg.runtime_status.voice_enabled:
        return
    try:
        import sounddevice as sd
        inputs = [d for d in sd.query_devices() if d.get("max_input_channels", 0) > 0]
        if inputs:
            cfg.runtime_status.mic_ok = True
        else:
            cfg.add_degradation("未检测到麦克风输入设备，语音功能禁用")
    except Exception as e:  # noqa: BLE001
        cfg.add_degradation(f"麦克风探测失败: {e}")


def probe_all(cfg: Config) -> None:
    """执行全部环境探测。"""
    check_llm(cfg)
    check_vosk_model(cfg)
    check_mic(cfg)
    # 综合判定：语音模块是否可用
    if cfg.runtime_status.voice_enabled and not (cfg.runtime_status.vosk_model_ok and cfg.runtime_status.mic_ok):
        cfg.runtime_status.voice_enabled = False
    # 综合判定：视觉模块是否可用
    if cfg.runtime_status.vision_enabled and not cfg.runtime_status.vision_model_ok:
        cfg.runtime_status.vision_enabled = False
