"""端到端链路测试：桌面聊天 → dialogue._on_user_text → 媒体摄入富集 → respond。

覆盖：
 1. 真实 config.yaml 能解析且 desktop.media.watch_videos 默认开；
 2. Dialogue._desktop_media_enabled() 正确读取该开关；
 3. 桌面来源消息含视频链接时，_on_user_text 把"视频观看"上下文拼到消息前再交给 respond；
 4. 开关关闭时，同样链接只当普通消息（不富集、不调用 respond 之外的副作用）；
 5. 非视频链接（普通网页）默认不走观看（ingest_urls=False），不富集。

不需要真实网络：patch media_ingest.watch_video 返回固定上下文。
"""

import asyncio
import os
import sys
import types
import unittest
from unittest.mock import patch

# —— 让 dialogue 模块在未装 ollama 的测试环境也能 import ——
_ollama = types.ModuleType("ollama")
_ollama.AsyncClient = object
_ollama.Client = object
sys.modules.setdefault("ollama", _ollama)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

from src.config import load_config  # noqa: E402
from src.companion.dialogue import Dialogue  # noqa: E402
from src.channels import media_ingest  # noqa: E402
from src.event_bus import Event, EventType  # noqa: E402


CFG_PATH = os.path.join(ROOT, "config.yaml")


class _Bus:
    def publish(self, *a, **k):
        return None


class _State:
    def transition(self, *a, **k):
        return None


class _Persona:
    name = "达妮娅"


class _LLM:
    async def vision_describe(self, *a, **k):
        return "fake-frame"


class _DialogueHarness:
    """构造一个最小可用的 Dialogue，并捕获 respond 收到的文本。"""

    def __init__(self, cfg):
        self.d = Dialogue(bus=_Bus(), llm=_LLM(), state=_State(),
                          persona=_Persona(), cfg=cfg)
        self.responded = []
        self.d.respond = self._respond  # 替换成记录桩

    async def _respond(self, text, source=""):
        self.responded.append(text)

    def send(self, text, source):
        """用 Event 包装后调用 _on_user_text（与真实总线投递一致）。"""
        return asyncio.run(self.d._on_user_text(
            Event(EventType.USER_TEXT, {"text": text, "source": source}, "test")))


async def _fake_watch(llm, url, **kw):
    return "【视频观看】标题：集成测试视频\n画面理解：一帧测试画面"


def _load_cfg():
    return load_config(CFG_PATH)


class TestDesktopVideoE2E(unittest.TestCase):

    def test_config_has_desktop_watch_videos_default_true(self):
        cfg = _load_cfg()
        media = cfg.ns.desktop.media
        self.assertTrue(getattr(media, "watch_videos", False),
                        "config.yaml 应含 desktop.media.watch_videos: true")

    def test_media_enabled_reads_switch(self):
        cfg = _load_cfg()
        h = _DialogueHarness(cfg)
        self.assertTrue(h.d._desktop_media_enabled(),
                        "默认 config 下桌面媒体开关应为开")

    def test_enabled_video_link_enriches_respond(self):
        cfg = _load_cfg()
        h = _DialogueHarness(cfg)
        with patch.object(media_ingest, "watch_video", _fake_watch):
            h.send("看看这个 https://b23.tv/abc123 讲什么的", "webui")
        self.assertEqual(len(h.responded), 1)
        sent = h.responded[0]
        self.assertIn("【视频观看】", sent, "视频链接应触发观看上下文")
        self.assertIn("看看这个", sent, "原始用户文本应保留")
        # 视频上下文应拼在用户文本之前
        self.assertLess(sent.index("【视频观看】"), sent.index("看看这个"))

    def test_disabled_switch_no_enrich(self):
        cfg = _load_cfg()
        # 临时把 desktop.media.watch_videos 设为 False
        cfg.ns.desktop.media.watch_videos = False
        h = _DialogueHarness(cfg)
        with patch.object(media_ingest, "watch_video", _fake_watch):
            h.send("看看这个 https://b23.tv/abc123", "webui")
        self.assertEqual(len(h.responded), 1)
        sent = h.responded[0]
        self.assertNotIn("【视频观看】", sent, "关闭后不应触发观看")
        self.assertIn("https://b23.tv/abc123", sent)

    def test_non_video_link_not_watched(self):
        cfg = _load_cfg()
        h = _DialogueHarness(cfg)
        calls = []
        async def _spy(llm, url, **kw):
            calls.append(url)
            return "【视频观看】x"
        with patch.object(media_ingest, "watch_video", _spy):
            h.send("看这篇新闻 https://example.com/article", "webui")
        self.assertEqual(len(h.responded), 1)
        self.assertEqual(calls, [], "普通网页链接不应触发视频观看")
        self.assertIn("https://example.com/article", h.responded[0])

    def test_social_source_not_double_processed(self):
        """社交通道（QQ/微信）消息不应在 dialogue 层再走桌面摄入。"""
        cfg = _load_cfg()
        h = _DialogueHarness(cfg)
        calls = []
        async def _spy(llm, url, **kw):
            calls.append(url)
            return "【视频观看】x"
        with patch.object(media_ingest, "watch_video", _spy):
            h.send("看看这个 https://b23.tv/abc123", "qq")  # 社交通道来源
        self.assertEqual(calls, [], "社交通道来源不应在 dialogue 层重复观看")
        self.assertEqual(len(h.responded), 1)
        self.assertNotIn("【视频观看】", h.responded[0])


if __name__ == "__main__":
    unittest.main()
