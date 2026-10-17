# -*- coding: utf-8 -*-
"""【达妮娅 · 云端全双工 一键伴侣】（独立 GUI 进程）

目标：把"接云端全双工"压成三步 —— 双击启动 → 自动加载人设 + 自动克隆音色 → 点「📞 拨打电话」。

启动后自动完成：
  1. 人设：从 prompts/call_profiles/danya_persona.txt + danya_behavior.txt 读取（达妮娅人格），无需手动配置。
  2. 依赖：检测 dashscope/pyaudio 等，缺失则一键安装（也可点「一键安装依赖」手动触发）。
  3. 音色：优先用 config.cloud_omni.ref_audio 指定的达妮娅参考音频（如 H:\\daniya_ref.mp3）
     自动克隆达妮娅音色（立体声下混单声道 / 24k / 裁到 20s，md5 缓存避免重复克隆）；
     未配置 ref_audio 时回退到已存 voice_id 或仓库片段拼接克隆，克隆失败则回退官方二次元预设 Chelsie。
  4. 拨打电话：后台连 Qwen-Omni-Realtime，麦克风 16k 上行、服务端 VAD 原生打断；达妮娅 24k 音频下行播放。
  5. 挂断：结束会话。

运行环境：需要带 tkinter 的 Python（已验证系统 Python 3.12 自带）。首次运行会按需 pip 安装 dashscope/pyaudio。
用法：双击「启动云端达妮娅.bat」，或 python tools/cloud_companion_launcher.py
"""
import os
import sys
import time
import types
import queue
import base64
import logging
import threading
import tempfile
import subprocess
import importlib.util
import tkinter as tk
from tkinter import scrolledtext

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FP = os.path.join(ROOT, 'config.yaml')
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')


# ----------------------------------------------------------------------------
# 依赖加载（按文件直接 load，绕开 src/llm/__init__ 的兄弟依赖）
# ----------------------------------------------------------------------------
def _load_cloud_omni():
    # 以「孤立模块 + 包上下文」加载：cloud_omni_realtime.py 内含相对导入
    # （from ..perception._pa_shared）。若用 spec_from_file_location 纯单文件加载
    # 会因「无父包」而抛 ImportError，导致启动器卡在「构造云端客户端失败」。
    # 这里显式给模块设置 __package__='src.llm' 并把项目根加入 sys.path，使相对导入
    # 解析为 src.perception._pa_shared，同时【不触发 src.llm.__init__】，避免连带
    # 加载 ollama 等重型依赖（启动器本就设计为轻量加载 cloud_omni）。
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    p = os.path.join(ROOT, 'src', 'llm', 'cloud_omni_realtime.py')
    spec = importlib.util.spec_from_file_location('src.llm.cloud_omni_realtime', p)
    m = importlib.util.module_from_spec(spec)
    m.__package__ = 'src.llm'
    sys.modules['src.llm.cloud_omni_realtime'] = m
    spec.loader.exec_module(m)
    return m.CloudOmniRealtime


def _load_clone_tool():
    p = os.path.join(ROOT, 'tools', 'clone_danya_voice.py')
    spec = importlib.util.spec_from_file_location('clone_danya_voice', p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def prep_ref_audio(src_path):
    """把任意参考音频规整成克隆所需规格：单声道 / 24k / 裁到 <=20s -> 临时 wav。

    克隆接口要求：>=24kHz、单声道、10~20s（上限 60s）。立体声会静默被服务端
    处理导致音色偏差，这里主动下混为单声道并裁到 20s，保证复刻质量。
    """
    import soundfile as sf
    import numpy as np
    data, sr = sf.read(src_path, dtype='float32')
    if data.ndim > 1:
        data = data.mean(axis=1)  # 立体声 -> 单声道
    if sr != 24000:
        # 简单线性重采样到 24k（克隆只需 >=24k 且干净人声，精度足够）
        n = int(round(len(data) * 24000 / sr))
        idx = (np.arange(n) * (len(data) - 1) / max(n - 1, 1)).round().astype(int)
        data = data[idx]
        sr = 24000
    max_len = 24000 * 20
    if len(data) > max_len:
        data = data[:max_len]
    tmp = os.path.join(tempfile.gettempdir(), "danya_clone_ref_prepared.wav")
    sf.write(tmp, data, 24000, subtype='PCM_16')
    dur = len(data) / 24000
    logging.info("克隆参考音频已规整: %s (%.1fs, %dHz, mono)", tmp, dur, sr)
    return tmp


_CLONE_CACHE = os.path.join(tempfile.gettempdir(), "danya_clone_cache.json")


def cached_clone(audio_path, name, api_key, model, base_url):
    """带缓存的克隆：**同一(参考音频 + 合成模型)** 只克隆一次。

    【2026-10-17 修复·缓存键必须含模型】旧实现只用音频 md5 作键，于是"为 A 模型克隆的
    音色"会被复用到 B 模型上 —— 而实时模型要求音色的 target_model 与连接模型一致，
    跨模型复用会被服务端拒绝（实测：`Voice 'qwen-omni-vc-danya…' is not supported`，
    表现是"连上了但一个 audio.delta 都没有"）。现在键 = md5 + 模型名。
    """
    import json
    import hashlib
    md5 = hashlib.md5(open(audio_path, 'rb').read()).hexdigest()
    key = "%s|%s" % (md5, (model or '').strip().lower())
    cache = {}
    if os.path.exists(_CLONE_CACHE):
        try:
            cache = json.load(open(_CLONE_CACHE, 'r', encoding='utf-8'))
        except Exception:  # noqa: BLE001
            cache = {}
    if cache.get(key):
        logging.info("复用缓存的克隆音色(模型=%s): %s", model, cache[key])
        return cache[key]
    ct = _load_clone_tool()
    voice = ct.clone(audio_path, name, api_key, model, base_url)
    cache[key] = voice
    try:
        json.dump(cache, open(_CLONE_CACHE, 'w', encoding='utf-8'))
    except Exception:  # noqa: BLE001
        pass
    return voice


def build_reference_audio(repo_root):
    """拼接仓库里的达妮娅参考音频 -> 临时 ~18s wav。返回路径或 None。

    候选片段均为 24k/单声道/PCM16，直接拼接无需重采样；总时长裁到 18s（满足克隆 10~20s 要求）。
    """
    import soundfile as sf
    import numpy as np
    cands = ["daniya_duplex_default.wav", "daniya_duplex_sample.wav",
             "test_qwen3_clone.wav", "daniya_sample.wav"]
    segs = []
    for c in cands:
        fp = os.path.join(repo_root, c)
        if not os.path.exists(fp):
            continue
        try:
            data, sr = sf.read(fp, dtype='int16')
            if data.ndim > 1:
                data = data[:, 0]
            if sr != 24000:
                logging.warning("跳过 %s（采样率 %d 非 24000）", c, sr)
                continue
            segs.append(data)
            logging.info("拼接片段 %s: %.1fs", c, len(data) / sr)
        except Exception as e:  # noqa: BLE001
            logging.warning("读取 %s 失败: %s", c, e)
    if not segs:
        return None
    merged = np.concatenate(segs)
    max_len = 24000 * 18
    if len(merged) > max_len:
        merged = merged[:max_len]
    tmp = os.path.join(tempfile.gettempdir(), "danya_clone_ref_concat.wav")
    sf.write(tmp, merged, 24000, subtype='PCM_16')
    logging.info("克隆参考音频已生成: %s (%.1fs)", tmp, len(merged) / 24000)
    return tmp


# ----------------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------------
class CompanionApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.bot = None
        self.running = False
        self.deps_ok = False
        self.session_thread = None
        self.mic_thread = None
        self._pa = None
        self._mic = None

        self._build_ui()
        # 启动后台初始化（加载人设 / 装依赖 / 克隆音色），不卡 UI
        threading.Thread(target=self._boot, daemon=True).start()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ----- UI -----
    def _build_ui(self) -> None:
        self.root.title("达妮娅 · 云端全双工")
        self.root.geometry("520x620")
        self.root.resizable(False, False)
        try:
            self.root.iconbitmap()
        except Exception:  # noqa: BLE001
            pass

        tk.Label(self.root, text="达妮娅 · 云端全双工 (Qwen-Omni-Realtime)",
                 font=("Microsoft YaHei", 13, "bold"), pady=8).pack()

        # 状态条
        status = tk.Frame(self.root)
        status.pack(fill=tk.X, padx=12, pady=(0, 6))
        self.var_persona = tk.StringVar(value="人设：初始化…")
        self.var_voice = tk.StringVar(value="音色：初始化…")
        self.var_conn = tk.StringVar(value="连接：待机（未拨号）")
        for var, w in ((self.var_persona, 18), (self.var_voice, 18), (self.var_conn, 16)):
            tk.Label(status, textvariable=var, font=("Microsoft YaHei", 10),
                     width=w, anchor="w").pack(side=tk.LEFT)

        # 对话记录
        tk.Label(self.root, text="对话记录", font=("Microsoft YaHei", 10),
                 anchor="w").pack(fill=tk.X, padx=12)
        self.log = scrolledtext.ScrolledText(
            self.root, wrap=tk.WORD, height=22,
            font=("Microsoft YaHei", 10), state=tk.DISABLED)
        self.log.pack(fill=tk.BOTH, expand=True, padx=12, pady=(2, 8))

        # 按钮区
        btn_row = tk.Frame(self.root)
        btn_row.pack(fill=tk.X, padx=12, pady=(0, 10))
        self.btn_dial = tk.Button(
            btn_row, text="📞 拨打电话", font=("Microsoft YaHei", 13, "bold"),
            bg="#2e7d32", fg="white", height=2, command=self._on_dial,
            state=tk.DISABLED)
        self.btn_dial.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.btn_hang = tk.Button(
            btn_row, text="挂断", font=("Microsoft YaHei", 13),
            bg="#c62828", fg="white", height=2, command=self._on_hang,
            state=tk.DISABLED)
        self.btn_hang.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))
        self.btn_install = tk.Button(
            self.root, text="一键安装依赖", font=("Microsoft YaHei", 10),
            command=self._on_install)
        self.btn_install.pack_forget()  # 默认隐藏，缺依赖时出现

    # ----- 线程安全的 UI 更新 -----
    def _append(self, msg: str) -> None:
        def _do():
            self.log.configure(state=tk.NORMAL)
            self.log.insert(tk.END, msg + "\n")
            self.log.configure(state=tk.DISABLED)
            self.log.see(tk.END)
        self.root.after(0, _do)

    def _set(self, var: tk.StringVar, text: str) -> None:
        self.root.after(0, lambda: var.set(text))

    def _set_buttons(self, dial_state, hang_state) -> None:
        def _do():
            self.btn_dial.configure(state=dial_state)
            self.btn_hang.configure(state=hang_state)
        self.root.after(0, _do)

    def _show_install(self, show: bool) -> None:
        def _do():
            if show:
                self.btn_install.pack(fill=tk.X, padx=12, pady=(0, 6))
            else:
                self.btn_install.pack_forget()
        self.root.after(0, _do)

    # ----- 初始化 -----
    def _boot(self) -> None:
        self._set(self.var_conn, "连接：初始化…")
        self._append("开始初始化：① 加载人设 → ② 安装依赖 → ③ 克隆音色")
        # 1) 读 config + 构造 bot（人设自动加载）
        try:
            import yaml
            data = yaml.safe_load(open(FP, 'r', encoding='utf-8'))
        except Exception as e:  # noqa: BLE001
            self._append(f"✗ 读取 config.yaml 失败: {e}")
            return
        try:
            CloudOmniRealtime = _load_cloud_omni()
            ns = types.SimpleNamespace(**{
                k: (types.SimpleNamespace(**v) if isinstance(v, dict) else v)
                for k, v in data.items()
            })
            self.bot = CloudOmniRealtime.from_config(ns, ROOT)
        except Exception as e:  # noqa: BLE001
            self._append(f"✗ 构造云端客户端失败: {e}")
            return
        if not self.bot.enabled:
            self._append("✗ cloud_omni 未启用：api_key 为空，请在 config.yaml 填 DashScope Key")
            return
        self.bot._on_user_text = lambda t: self._append(f"【你】{t}")
        self.bot._on_assistant_text = lambda t: self._append(f"【达妮娅】{t}")
        self._append(f"✓ 人设已加载（达妮娅，{len(self.bot.instructions)} 字）")
        self._set(self.var_persona, "人设：达妮娅 ✓")

        # 2) 依赖
        missing = self._check_deps()
        if missing:
            self._append(f"缺少依赖：{', '.join(missing)}，正在自动安装…")
            if not self._install_deps(missing):
                self._append("✗ 自动安装失败。请手动运行：")
                self._append(f"   pip install {' '.join(missing)}")
                self._show_install(True)
                return
            missing = self._check_deps()
            if missing:
                self._append(f"✗ 仍缺依赖：{', '.join(missing)}，请手动安装")
                self._show_install(True)
                return
        self.deps_ok = True
        self._append("✓ 依赖就绪")

        # 3) 音色（自动克隆 or 预设）
        self._resolve_voice(data)

        # 4) 允许拨打
        self._set_buttons(tk.NORMAL, tk.DISABLED)
        self._append("准备就绪 → 点「📞 拨打电话」开始通话")

    def _check_deps(self):
        miss = []
        for m in ("dashscope", "pyaudio", "requests", "soundfile", "numpy", "yaml"):
            try:
                importlib.import_module(m)
            except Exception:  # noqa: BLE001
                miss.append(m)
        return miss

    # pip 包名与 import 模块名不一致的映射（yaml 模块来自 PyYAML 发行包）
    _PIP_NAMES = {"yaml": "pyyaml"}

    def _install_deps(self, missing):
        pkgs = [self._PIP_NAMES.get(m, m) for m in missing]
        try:
            self._append(f"  → pip install {' '.join(pkgs)}")
            proc = subprocess.Popen(
                [sys.executable, "-m", "pip", "install", "-U", *pkgs],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, encoding="utf-8", errors="replace")
            start = time.time()
            for line in proc.stdout:
                line = line.rstrip("\n")
                if line.strip():
                    self._append("  pip> " + line)
                if time.time() - start > 600:  # 超时保护：>10 分钟视为卡死
                    proc.kill()
                    self._append("✗ 安装超时（>10分钟），可能被网络/代理卡住，"
                                 "请手动在终端跑：pip install " + " ".join(pkgs))
                    return False
            return proc.wait() == 0
        except Exception as e:  # noqa: BLE001
            self._append(f"✗ pip 安装异常: {e}")
            return False

    def _resolve_voice(self, data) -> None:
        co = data.get('cloud_omni', {}) or {}
        ref = (co.get('ref_audio') or '').strip()
        # 优先级 1：配置了 ref_audio 且文件存在 → 强制用此文件重新克隆达妮娅音色
        if ref and os.path.exists(ref):
            self._append(f"发现参考音频：{ref}，正在克隆达妮娅音色…")
            try:
                audio = prep_ref_audio(ref)
                key = co.get('api_key') or os.getenv('DASHSCOPE_API_KEY', '')
                base = co.get('base_url', 'https://dashscope.aliyuncs.com')
                voice = cached_clone(audio, 'danya', key, self.bot.model, base)
                ct = _load_clone_tool()
                ct.write_voice(voice)
                self.bot.voice = voice
                self._append(f"✓ 达妮娅音色克隆成功并写入：{voice}")
                self._set(self.var_voice, "音色：达妮娅 ✓")
            except Exception as e:  # noqa: BLE001
                self._append(f"⚠ 克隆失败（{e}）")
                # 回退：若 config 里还有旧 voice 就先用着，否则用 Chelsie 预设
                if co.get('voice'):
                    self.bot.voice = co['voice']
                    self._set(self.var_voice, "音色：已配置(旧)")
                    self._append(f"  回退到已配置音色：{co['voice']}")
                else:
                    self._fallback_preset()
            return
        # 优先级 2：config 已存克隆 voice_id 直接用
        if co.get('voice'):
            self._append(f"✓ 音色已配置：{co['voice']}")
            self._set(self.var_voice, "音色：已配置 ✓")
            return
        # 优先级 3：仓库参考音频拼接 -> 克隆 -> 否则回退预设
        self._append("未配置参考音频 → 尝试拼接仓库片段克隆…")
        audio = build_reference_audio(ROOT)
        if not audio:
            self._fallback_preset()
            return
        try:
            ct = _load_clone_tool()
            key = co.get('api_key') or os.getenv('DASHSCOPE_API_KEY', '')
            base = co.get('base_url', 'https://dashscope.aliyuncs.com')
            voice = ct.clone(audio, 'danya', key, self.bot.model, base)
            ct.write_voice(voice)
            self.bot.voice = voice
            self._append(f"✓ 达妮娅音色克隆成功：{voice}")
            self._set(self.var_voice, "音色：达妮娅 ✓")
        except Exception as e:  # noqa: BLE001
            self._append(f"⚠ 克隆失败（{e}），回退官方二次元预设 Chelsie")
            self._fallback_preset()

    def _fallback_preset(self) -> None:
        self.bot.voice = "Chelsie"
        self._set(self.var_voice, "音色：Chelsie(预设)")

    # ----- 拨打 / 挂断 -----
    def _on_dial(self) -> None:
        if not self.deps_ok or not self.bot:
            return
        self.running = True
        self._set_buttons(tk.DISABLED, tk.NORMAL)
        self._set(self.var_conn, "连接：连接中…")
        self.session_thread = threading.Thread(target=self._session, daemon=True)
        self.session_thread.start()

    def _session(self) -> None:
        try:
            self.bot.connect()
        except Exception as e:  # noqa: BLE001
            self._append(f"✗ 连接失败：{type(e).__name__}: {e}")
            self._append("排查：① 网络可达 dashscope；② 若公共域名连不通，"
                         "把 config.cloud_omni.ws_url 改为工作空间专属域名；"
                         "③ 确认 DashScope Key 有效。")
            self.running = False
            self._set(self.var_conn, "连接：失败")
            self._set_buttons(tk.NORMAL, tk.DISABLED)
            return
        self._set(self.var_conn, "连接：通话中 🟢")
        self._append("已接通，对麦克风说话即可（服务端自动打断）")
        self.mic_thread = threading.Thread(target=self._mic_loop, daemon=True)
        self.mic_thread.start()

    def _mic_loop(self) -> None:
        import pyaudio
        pa = pyaudio.PyAudio()
        self._pa = pa
        try:
            mic = pa.open(format=pyaudio.paInt16, channels=1, rate=16000,
                          input=True, frames_per_buffer=1600)
        except Exception as e:  # noqa: BLE001
            self._append(f"✗ 打开麦克风失败：{e}")
            self.running = False
            return
        self._mic = mic
        while self.running:
            # 【2026-10-04 修·1007 风暴】服务端致命拒绝（1007 Access denied / 账户异常）
            # 会在 connect 成功后瞬间到达；不检测会让 UI 一直显示"通话中"假象、麦克风
            # 静默空推。检测到即停麦并给出明确账户排查提示。
            if getattr(self.bot, "_fatal_close", False):
                _r = getattr(self.bot, "_fatal_reason", "") or "服务端拒绝连接"
                try:
                    self.bot.close()
                except Exception:  # noqa: BLE001
                    pass
                self.running = False
                self._append(
                    f"✗ 云端拒绝连接（{_r}）。多为阿里云账户异常：欠费、免费额度用尽、"
                    "未开通 qwen-omni 实时语音、或未完成实名认证。请到阿里云百炼控制台核实账户状态。")
                self._set(self.var_conn, "连接：被拒绝")
                self._set_buttons(tk.NORMAL, tk.DISABLED)
                return
            try:
                d = mic.read(1600, exception_on_overflow=False)
            except Exception:  # noqa: BLE001
                break
            if self.bot:
                self.bot.send_audio(d)
            time.sleep(0.005)
        try:
            mic.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            pa.terminate()
        except Exception:  # noqa: BLE001
            pass

    def _on_hang(self) -> None:
        self.running = False
        try:
            if self.bot:
                self.bot.close()
        except Exception:  # noqa: BLE001
            pass
        self._set(self.var_conn, "连接：已挂断")
        self._append("已挂断")
        self._set_buttons(tk.NORMAL, tk.DISABLED)

    def _on_install(self) -> None:
        self._show_install(False)
        self._append("正在安装依赖…")
        threading.Thread(target=self._boot_install_then_continue, daemon=True).start()

    def _boot_install_then_continue(self) -> None:
        missing = self._check_deps()
        if missing and self._install_deps(missing):
            missing = self._check_deps()
        if not missing:
            self.deps_ok = True
            self._append("✓ 依赖就绪")
            self._set_buttons(tk.NORMAL, tk.DISABLED)
        else:
            self._append(f"✗ 仍缺依赖：{', '.join(missing)}")
            self._show_install(True)

    def _on_close(self) -> None:
        self.running = False
        try:
            if self.bot:
                self.bot.close()
        except Exception:  # noqa: BLE001
            pass
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    CompanionApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
