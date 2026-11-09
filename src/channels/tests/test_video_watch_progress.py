"""陪你看视频·进度链路测试。

覆盖：
 1. watch_video 在 解析/下载/抽帧/看画面/完成 各阶段正确调用 on_progress（含下载百分比）；
 2. Dialogue._make_video_progress() 把进度通过总线 VIDEO_WATCH_PROGRESS 事件投出；
 3. 集成：桌面发视频链接 → _on_user_text 经 _desktop_video_context → media_ingest
    → watch_video 调用 on_progress → 总线 publish_threadsafe 收到进度事件。

不需要真实网络：patch download_video / sample_frames / watch_video。
"""

import asyncio
import os
import sys
import types
import unittest
from unittest.mock import patch

_ollama = types.ModuleType("ollama")
_ollama.AsyncClient = object
_ollama.Client = object
sys.modules.setdefault("ollama", _ollama)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

from src.config import load_config  # noqa: E402
from src.companion.dialogue import Dialogue  # noqa: E402
from src.channels import video_watch, media_ingest  # noqa: E402
from src.event_bus import Event, EventType  # noqa: E402


class _Bus:
    def __init__(self):
        self.published = []
        self.threadsafe = []

    def publish(self, *a, **k):
        return None

    def publish_threadsafe(self, event):
        self.threadsafe.append(event)


class _State:
    def transition(self, *a, **k):
        return None


class _Persona:
    name = "达妮娅"


class _LLM:
    def vision_available(self):
        return True

    async def vision_describe(self, *a, **k):
        return "画面描述"


class _DialogueHarness:
    def __init__(self, cfg):
        self.bus = _Bus()
        self.d = Dialogue(bus=self.bus, llm=_LLM(), state=_State(),
                          persona=_Persona(), cfg=cfg)
        self.responded = []
        self.d.respond = self._respond

    async def _respond(self, text, source="", channel="", reply_target=None):
        # 【2026-10-23】签名跟着生产 respond 长齐（_on_user_text 现在会把载荷里的
        # channel/reply_target 一起传下来）。桩停在旧签名会 TypeError——**这正是要的**：
        # 签名漂移要炸得响亮，不要被吞掉。
        self.responded.append(text)
        self.last_channel = channel
        self.last_reply_target = reply_target

    def send(self, text, source):
        return asyncio.run(self.d._on_user_text(
            Event(EventType.USER_TEXT, {"text": text, "source": source}, "test")))


# 【2026-11-09 加·「缺环境」不是「判据失败」】`config.yaml` 在 `.gitignore` 里
# （实配，含 api_key），任何新克隆的树上都没有。本文件 3 个用例要 `_load_cfg()`，
# 于是它们在新机器上全是 FileNotFoundError —— 而这几条验的是「进度事件有没有
# 发到总线上」，与 api_key 无关。退到出厂模板 `config.release.yaml` 即可。
def _pick_cfg() -> str:
    for _name in ("config.yaml", "config.release.yaml"):
        _p = os.path.join(ROOT, _name)
        if os.path.isfile(_p):
            return _p
    return ""


CFG_PATH = _pick_cfg()


def _load_cfg():
    return load_config(CFG_PATH)


class TestWatchVideoProgress(unittest.TestCase):

    def test_watch_video_emits_all_stages(self):
        """watch_video 应依次触发 resolve/download/frame/describe/done 阶段。"""
        stages = []
        pcts = {}

        async def fake_dl(url, workdir, ffmpeg_exe, **kw):
            # 模拟 yt-dlp 进度钩子：先 60% 再回 100%（download_media 返回 [(path,kind), meta]）
            cb = kw.get("on_download_progress")
            if cb:
                cb(60)
                cb(100)
            return ([("/tmp/test.mp4", "video")],
                    {"title": "测试视频", "duration": 5, "uploader": "UP"})

        async def fake_frames(path, n, exe):
            return [b"frame1", b"frame2"]

        llm = _LLM()
        # 【2026-11-09 修·测试依赖宿主有没有 ffmpeg】`watch_video` 里抽帧那道闸是
        # `if video_files and ffmpeg_exe:`，而 `ffmpeg_exe = _resolve_ffmpeg()` 是
        # **前置能力探测**。原来只 mock 了下游的 download_media / sample_frames，
        # 探测没 mock —— 本机没装 ffmpeg 时 frame 阶段根本不触发，于是
        # 「缺少阶段 frame」判红，而 sample_frames 那个 mock 一次都没被调用。
        # 结论取决于跑它的机器，不是代码。前置探测一起 mock 掉。
        with patch.object(video_watch, "download_media", fake_dl), \
             patch.object(video_watch, "sample_frames", fake_frames), \
             patch.object(video_watch, "_resolve_ffmpeg",
                          return_value="/fake/ffmpeg"):
            async def run():
                return await video_watch.watch_video(
                    llm, "https://b23.tv/abc123",
                    # 【2026-10-21 修·签名过时】产品给 on_progress 加了第 5 个参数 meta
                    # （kind/title/url，供前端气泡显示"在看什么"）。这个 lambda 还停在
                    # 4 参 → TypeError，而 watch_video 的 _prog 用 `except: pass` 吞掉了
                    # 回调异常 → 表现为"一个阶段都没收到"，看不出是签名不匹配。
                    on_progress=lambda s, m, done=False, pct=None, meta=None: (
                        stages.append((s, done)) or pcts.__setitem__(s, pct)))
            out = asyncio.run(run())

        self.assertIsNotNone(out, "应返回视频上下文")
        self.assertIn("测试视频", out)
        seen = [s for s, _ in stages]
        for expect in ("resolve", "download", "frame", "describe", "done"):
            self.assertIn(expect, seen, f"缺少阶段 {expect}")
        # 最后一步必须是 done=True
        self.assertTrue(stages[-1][1], "最后阶段应为 done=True")
        # 下载阶段应收到百分比
        self.assertIn("download", pcts)
        self.assertEqual(pcts["download"], 100)

    def test_make_video_progress_publishes_event(self):
        """_make_video_progress 的回调应通过 publish_threadsafe 发出 VIDEO_WATCH_PROGRESS。"""
        cfg = _load_cfg()
        h = _DialogueHarness(cfg)
        cb = h.d._make_video_progress()
        cb("download", "达妮娅正在下载视频…", pct=42)
        cb("done", "看完了～", done=True)
        self.assertEqual(len(h.bus.threadsafe), 2)
        types_seen = [e.type for e in h.bus.threadsafe]
        self.assertIn(EventType.VIDEO_WATCH_PROGRESS, types_seen)
        last = h.bus.threadsafe[-1]
        self.assertEqual(last.data["stage"], "done")
        self.assertTrue(last.data["done"])
        self.assertEqual(last.data["pct"], None)

    def test_on_user_text_emits_progress_event(self):
        """集成：桌面发视频链接，总线应收到 VIDEO_WATCH_PROGRESS 进度事件。"""
        cfg = _load_cfg()
        h = _DialogueHarness(cfg)

        async def fake_watch(llm, url, **kw):
            # 真实路径里 watch_video 会调用 on_progress；这里模仿它触发，
            # 验证 dialogue 把回调接到了总线。
            op = kw.get("on_progress")
            if op:
                op("resolve", "达妮娅正在解析这个视频…")
                op("download", "达妮娅正在下载视频…", pct=100)
                op("done", "看完了，这就和你聊～", done=True)
            return "【视频观看】标题：集成视频\n画面理解：1. 测试帧"

        with patch.object(media_ingest, "watch_video", fake_watch):
            h.send("看看这个 https://b23.tv/abc123 讲什么的", "webui")

        # respond 收到富集后的消息
        self.assertEqual(len(h.responded), 1)
        self.assertIn("【视频观看】", h.responded[0])
        # 总线收到了进度事件
        kinds = [e.type for e in h.bus.threadsafe]
        self.assertIn(EventType.VIDEO_WATCH_PROGRESS, kinds)
        stages = [e.data["stage"] for e in h.bus.threadsafe
                  if e.type == EventType.VIDEO_WATCH_PROGRESS]
        self.assertIn("done", stages)

    def test_social_source_no_progress_event(self):
        """社交通道来源不应在 dialogue 层触发观看进度（避免重复处理）。"""
        cfg = _load_cfg()
        h = _DialogueHarness(cfg)
        with patch.object(media_ingest, "watch_video", _fake_watch_noop):
            h.send("看看这个 https://b23.tv/abc123", "qq")
        kinds = [e.type for e in h.bus.threadsafe]
        self.assertNotIn(EventType.VIDEO_WATCH_PROGRESS, kinds)


async def _fake_watch_noop(llm, url, **kw):
    return None


class TestVideoWatchDiagnostics(unittest.TestCase):
    """诊断记录（开发者页用）：成功/失败都应留下可排查的记录。"""

    def _clear(self):
        video_watch._DIAG_RUNS.clear()

    def test_success_run_recorded(self):
        self._clear()
        async def fake_dl(url, workdir, ffmpeg_exe, **kw):
            return ([("/tmp/x.mp4", "video")], {"title": "诊断测试", "duration": 5})

        async def fake_frames(path, n, exe):
            return [b"f1", b"f2"]

        with patch.object(video_watch, "download_media", fake_dl), \
             patch.object(video_watch, "sample_frames", fake_frames), \
             patch.object(video_watch, "_resolve_ffmpeg",
                          return_value="/fake/ffmpeg"):
            asyncio.run(video_watch.watch_video(
                _LLM(), "https://b23.tv/diag1"))

        runs = video_watch.get_diagnostics()["runs"]
        self.assertTrue(runs, "成功观看应留下诊断记录")
        last = runs[-1]
        self.assertTrue(last["ok"])
        self.assertEqual(last["url"], "https://b23.tv/diag1")
        self.assertEqual(last["frames"], 2, "应记录 AI 看了几帧")
        self.assertIsNotNone(last["elapsed"])
        self.assertIn("done", [s["stage"] for s in last["stages"]])
        self.assertEqual(last["title"], "诊断测试")

    def test_failure_run_recorded_with_error(self):
        self._clear()
        async def fake_dl(url, workdir, ffmpeg_exe, **kw):
            raise RuntimeError("yt-dlp 未安装")

        with patch.object(video_watch, "download_media", fake_dl):
            out = asyncio.run(video_watch.watch_video(
                _LLM(), "https://b23.tv/diag2"))
        self.assertIsNone(out, "失败应返回 None 以便上层降级")

        runs = video_watch.get_diagnostics()["runs"]
        self.assertTrue(runs, "失败观看也应留下诊断记录（否则无法排查）")
        last = runs[-1]
        self.assertFalse(last["ok"])
        self.assertIn("yt-dlp", (last["error"] or ""), "错误信息应保留失败原因")
        self.assertIn("error", [s["stage"] for s in last["stages"]])

    def test_diagnostics_exposes_dependencies(self):
        """诊断应暴露 yt-dlp / ffmpeg 可用性——这是"看不了视频"最常见原因。"""
        d = video_watch.get_diagnostics()
        self.assertIn("yt_dlp", d)
        self.assertIn("ffmpeg", d)
        self.assertIn("runs", d)
        # 环境里装了 yt-dlp 时应返回字符串版本号，而不是模块对象
        if d["yt_dlp"] is not None:
            self.assertIsInstance(d["yt_dlp"], str,
                                  "版本号必须是字符串（曾误取到 yt_dlp.version 子模块）")


if __name__ == "__main__":
    unittest.main()
