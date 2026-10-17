"""双工接通检测(Bug A) + 视频链接一起看路由(Bug B)（2026-10-02）。

Bug A：云端实时链路(CloudOmniRealtime)此前从不回报连接状态 → proactive 的
        _on_duplex_status 在云端档永远不被调用 → _duplex_call_ready 永远 False →
        UI 主页面一直卡拨号（但音频/字幕已在跑，Live2D 气泡能弹）。
        修复：CloudOmniRealtime 新增 on_status 回调，_on_open/close/error 回报连接状态。

Bug B：用户发抖音/B站链接，被 agent 的 open_app/browse_web 当成普通网址丢进浏览器，
        而不是"陪你看视频"。修复：_respond_with_tools 顶部检测到视频链接且开启一起看
        时，跳过浏览器工具，回落普通链路（此时 user_text 已含视频上下文，AI 直接聊画面）。
"""

import asyncio
import sys
import types
import unittest

# 测试环境未装 ollama（src.llm 包级 import 需要），桩掉避免 ImportError。
if "ollama" not in sys.modules:
    _ollama = types.ModuleType("ollama")
    _ollama.AsyncClient = object
    _ollama.Client = object
    sys.modules["ollama"] = _ollama

from src.channels.video_watch import is_video_url
from src.llm.cloud_omni_realtime import CloudOmniRealtime


class TestCloudOmniStatusBugA(unittest.TestCase):
    def _make(self, on_status=None):
        # 构造函数轻量（不在构造时 import dashscope），可直接实例化。
        return CloudOmniRealtime(
            model="qwen-omni-turbo", api_key="x", base_url="https://dashscope.aliyuncs.com",
            on_status=on_status,
        )

    def test_constructor_stores_on_status(self):
        cb = lambda m: None  # noqa: E731
        c = self._make(cb)
        self.assertIs(c._on_status, cb)

    def test_on_open_reports_connected(self):
        got = []
        c = self._make(got.append)
        c._on_open()
        self.assertEqual(len(got), 1)
        self.assertIn("会话建立", got[0])

    def test_on_close_reports(self):
        got = []
        c = self._make(got.append)
        c._on_close("bye")
        self.assertEqual(len(got), 1)
        self.assertIn("关闭", got[0])

    def test_on_error_reports(self):
        got = []
        c = self._make(got.append)
        c._on_error("boom")
        self.assertEqual(len(got), 1)
        self.assertIn("错误", got[0])

    def test_none_on_status_is_safe(self):
        # on_status 未传时，_on_open 不应抛异常（此前若直接调用会 AttributeError）。
        c = self._make(None)
        c._on_open()
        c._on_close("x")
        c._on_error("x")


class TestVideoUrlRecognizeBugB(unittest.TestCase):
    def test_douyin_recognized(self):
        for u in ("https://v.douyin.com/abc123/", "https://www.douyin.com/video/7000",
                  "https://iesdouyin.com/xxx"):
            self.assertTrue(is_video_url(u), f"应识别为视频链接: {u}")

    def test_bilibili_recognized(self):
        for u in ("https://www.bilibili.com/video/BV1xx", "https://b23.tv/abc"):
            self.assertTrue(is_video_url(u), f"应识别为视频链接: {u}")

    def test_non_video_not_recognized(self):
        for u in ("https://example.com/article/123", "https://github.com/foo/bar"):
            self.assertFalse(is_video_url(u), f"不应识别为视频链接: {u}")


class TestWatchTogetherIntentBugB(unittest.TestCase):
    """直接复用 Dialogue._is_watch_together_intent 方法体（不构造完整 Dialogue 实例）。

    Dialogue._xxx 在 Python3 是普通函数，可把 stub 当 self 传入。
    """

    def _stub(self, media_enabled=True):
        s = types.SimpleNamespace()
        s._desktop_media_enabled = lambda: media_enabled
        return s

    def _call(self, stub, text, media_enabled=True):
        from src.companion.dialogue import Dialogue  # noqa: PLC0415
        return Dialogue._is_watch_together_intent(stub, text, media_enabled)

    def test_video_link_with_media_on(self):
        s = self._stub(True)
        self.assertTrue(self._call(s, "看这个 https://v.douyin.com/abc123/ 好好笑"))

    def test_video_link_but_media_disabled(self):
        s = self._stub(False)
        # media_enabled=False 时，不应走一起看（交给普通链路/agent 决定）
        self.assertFalse(self._call(s, "https://v.douyin.com/abc123/", False))

    def test_no_url_is_false(self):
        s = self._stub(True)
        self.assertFalse(self._call(s, "今天天气不错"))

    def test_normal_url_is_false(self):
        s = self._stub(True)
        self.assertFalse(self._call(s, "看这篇 https://example.com/article/1"))

    def test_multiple_urls_one_video(self):
        s = self._stub(True)
        self.assertTrue(self._call(
            s, "参考 https://example.com 和视频 https://www.bilibili.com/video/BV1xx"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
