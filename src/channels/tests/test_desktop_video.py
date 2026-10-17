"""桌面聊天「陪你看视频」接入测试（G 扩展）。

验证 dialogue._on_user_text 复用的桌面媒体摄入路径：
  1) 桌面消息含视频链接 + watch_videos=True → 取回【视频观看】上下文
  2) 非视频链接且 ingest_urls=False → 不处理（保持普通文本，不联网抓取）
  3) watch_videos=False → 不产生视频上下文
  4) _desktop_media_enabled 配置读取逻辑（默认开，可关）
不触网：patch video_watch.watch_video / is_video_url，避免真实下载。
"""
import asyncio
import os
import sys
import types
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.channels import media_ingest, video_watch  # noqa: E402


class FakeLLM:
    def __init__(self):
        self.calls = 0
        self.vision_available = lambda: True

    async def vision_describe(self, image_bytes, prompt):
        self.calls += 1
        return "（测试帧描述）"


def _run(coro):
    return asyncio.run(coro)


class TestDesktopVideoIngest(unittest.TestCase):
    def test_video_url_yields_context(self):
        async def fake_watch(llm, url, **kw):
            return "【视频观看】标题：测试视频\n作者：某人\n画面理解：1. 测试帧"
        with patch.object(media_ingest, "watch_video", fake_watch):
            ctxs = _run(media_ingest.ingest_media(
                FakeLLM(), None, "看看这个 https://b23.tv/abc123", [],
                ingest_images=False, ingest_urls=False,
                store_to_memory=False, watch_videos=True))
        self.assertEqual(len(ctxs), 1)
        self.assertIn("【视频观看】", ctxs[0])
        self.assertIn("测试视频", ctxs[0])

    def test_non_video_url_skipped_when_ingest_urls_false(self):
        hit = {}

        async def fake_watch(llm, url, **kw):
            hit["url"] = url
            return "SHOULD_NOT_APPEAR"
        with patch.object(media_ingest, "watch_video", fake_watch):
            ctxs = _run(media_ingest.ingest_media(
                FakeLLM(), None, "读这篇 https://example.com/article", [],
                ingest_images=False, ingest_urls=False,
                store_to_memory=False, watch_videos=True))
        self.assertEqual(ctxs, [])
        self.assertNotIn("url", hit)  # watch 根本没被调用

    def test_watch_disabled_no_context(self):
        async def fake_watch(llm, url, **kw):
            return "SHOULD_NOT_APPEAR"
        with patch.object(video_watch, "watch_video", fake_watch):
            ctxs = _run(media_ingest.ingest_media(
                FakeLLM(), None, "https://b23.tv/abc123", [],
                ingest_images=False, ingest_urls=False,
                store_to_memory=False, watch_videos=False))
        self.assertEqual(ctxs, [])

    def test_multiple_urls_one_video(self):
        seen = []

        async def fake_watch(llm, url, **kw):
            seen.append(url)
            return "【视频观看】x"
        with patch.object(media_ingest, "watch_video", fake_watch):
            ctxs = _run(media_ingest.ingest_media(
                FakeLLM(), None,
                "视频 https://www.bilibili.com/video/BV1xx 和 普通 https://example.com",
                [], ingest_images=False, ingest_urls=False,
                store_to_memory=False, watch_videos=True))
        self.assertEqual(len(ctxs), 1)
        self.assertEqual(len(seen), 1)
        self.assertIn("bilibili", seen[0])


class TestDesktopMediaEnabled(unittest.TestCase):
    """复刻 dialogue._desktop_media_enabled 的读取逻辑（避免导入重依赖的 dialogue 模块）。"""

    def _call(self, cfg_ns):
        # 与 dialogue._desktop_media_enabled 完全一致的读取逻辑
        try:
            desktop = getattr(cfg_ns, "desktop", None)
            if desktop is None:
                return True
            media = getattr(desktop, "media", None)
            if media is None:
                return True
            return bool(getattr(media, "watch_videos", True))
        except Exception:
            return True

    def test_default_when_absent(self):
        self.assertTrue(self._call(types.SimpleNamespace()))

    def test_explicit_false(self):
        ns = types.SimpleNamespace(desktop=types.SimpleNamespace(
            media=types.SimpleNamespace(watch_videos=False)))
        self.assertFalse(self._call(ns))

    def test_explicit_true(self):
        ns = types.SimpleNamespace(desktop=types.SimpleNamespace(
            media=types.SimpleNamespace(watch_videos=True)))
        self.assertTrue(self._call(ns))

    def test_media_missing(self):
        ns = types.SimpleNamespace(desktop=types.SimpleNamespace())
        self.assertTrue(self._call(ns))


if __name__ == "__main__":
    unittest.main(verbosity=2)
