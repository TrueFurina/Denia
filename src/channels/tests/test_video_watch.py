"""video_watch 离线单测：不触网、不依赖 yt-dlp/ffmpeg 真实下载。

覆盖：
  1) is_video_url 命中已知平台 / 拒绝普通网页
  2) build_video_context 纯函数组装（标题/作者/时长/简介/帧描述）
  3) watch_video 成功路径（mock download_video + sample_frames + llm）→ 含帧描述
  4) watch_video 下载失败 → 返回 None（调用方退回网页摘要）
  5) watch_video 非视频链接 → 返回 None
  6) media_ingest 集成：watch_videos=True 时视频链接走观看、不重复抓网页
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import patch, AsyncMock

# 把仓库根加入 path，使 src.* 相对导入可用（与 test_onebot.py 一致）
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.channels import video_watch as vw          # noqa: E402
from src.channels import media_ingest as mi          # noqa: E402


class FakeLLM:
    def __init__(self, descs):
        self._descs = list(descs)
        self.calls = 0
        self.vision_available = lambda: True

    async def vision_describe(self, image_bytes, prompt):
        self.calls += 1
        if self._descs:
            return self._descs.pop(0)
        return "（无描述）"


class TestIsVideoUrl(unittest.TestCase):
    def test_hits(self):
        for u in [
            "https://www.bilibili.com/video/BV1xx",
            "https://b23.tv/abc123",
            "https://v.douyin.com/iRxx/",
            "https://www.douyin.com/video/7000",
            "https://www.youtube.com/watch?v=abc",
            "https://youtu.be/abc",
            "https://www.tiktok.com/@u/video/1",
            "https://www.xiaohongshu.com/explore/abc",
        ]:
            self.assertTrue(vw.is_video_url(u), f"应为视频链接: {u}")

    def test_rejects(self):
        for u in [
            "https://example.com/article/123",
            "https://www.baidu.com/s?wd=hello",
            "https://github.com/owner/repo",
            "just some text",
        ]:
            self.assertFalse(vw.is_video_url(u), f"不应是视频链接: {u}")


class TestBuildContext(unittest.TestCase):
    def test_full(self):
        meta = {
            "title": "如何做红烧肉",
            "uploader": "美食UP",
            "duration": 125,
            "description": "本期教你家常红烧肉的做法，软糯入味。",
        }
        ctx = vw.build_video_context(meta, ["锅里的肉", "成品摆盘"])
        self.assertIn("如何做红烧肉", ctx)
        self.assertIn("美食UP", ctx)
        self.assertIn("2分05秒", ctx)
        self.assertIn("家常红烧肉", ctx)
        self.assertIn("锅里的肉", ctx)
        self.assertIn("成品摆盘", ctx)

    def test_no_frames(self):
        ctx = vw.build_video_context({"title": "T"}, [])
        self.assertIn("仅基于标题/简介/标签理解", ctx)


class TestDouyinResolver(unittest.TestCase):
    """抖音免登录解析器的纯函数部分（不触网）。"""

    def test_is_douyin_url(self):
        self.assertTrue(vw._is_douyin_url("https://v.douyin.com/xxxx/"))
        self.assertTrue(vw._is_douyin_url("https://www.douyin.com/video/7000"))
        self.assertTrue(vw._is_douyin_url("https://www.iesdouyin.com/share/video/1"))
        self.assertFalse(vw._is_douyin_url("https://www.bilibili.com/video/BV1"))
        self.assertFalse(vw._is_douyin_url("https://www.youtube.com/watch?v=1"))

    def test_resolve_id_direct(self):
        # 直接是 /video/<id> 时无需联网即可解析
        self.assertEqual(
            vw._douyin_resolve_id("https://www.douyin.com/video/7692028054213988851"),
            "7692028054213988851")

    def test_resolve_id_shortlink(self):
        # 短链需联网跟随重定向；用 mock 让测试离线、确定
        class _Resp:
            def __init__(self, url, text=""):
                self.url = url
                self.text = text
        with patch("requests.get", return_value=_Resp(
                "https://www.douyin.com/video/7692028054213988851?x=1")):
            self.assertEqual(
                vw._douyin_resolve_id("https://v.douyin.com/-2hmXbOxekk/"),
                "7692028054213988851")
        # 重定向后是首页且页面里也没有 share/video/<id> → 解析不出 ID（返回 None）
        with patch("requests.get", return_value=_Resp(
                "https://www.douyin.com/", text="欢迎来到抖音")):
            self.assertIsNone(
                vw._douyin_resolve_id("https://v.douyin.com/-2hmXbOxekk/"))

    def test_pick_urls(self):
        detail = {
            "video": {
                "play_addr": {"url_list": ["https://a/1.mp4", "https://b/1.mp4"]},
                "bit_rate": [
                    {"play_addr": {"url_list": ["https://c/2.mp4"]}},
                    {"play_addr": {"url_list": ["https://a/1.mp4"]}},  # 重复应被去重
                ],
            }
        }
        urls = vw._douyin_pick_urls(detail)
        self.assertEqual(urls, ["https://a/1.mp4", "https://b/1.mp4", "https://c/2.mp4"])

    def test_pick_urls_empty(self):
        self.assertEqual(vw._douyin_pick_urls({"video": {}}), [])


class TestMediaRouting(unittest.TestCase):
    """「加全」后：媒体白名单 / Pixiv 识别 / 图文上下文 / token 解析（均不触网）。"""

    def test_is_media_url_broad(self):
        ok = [
            "https://www.bilibili.com/video/BV1", "https://b23.tv/xxx",
            "https://v.douyin.com/xxx/", "https://www.douyin.com/video/1",
            "https://www.youtube.com/watch?v=1", "https://youtu.be/1",
            "https://www.tiktok.com/@a/video/1", "https://v.kuaishou.com/xxx",
            "https://www.xiaohongshu.com/explore/1", "https://xhslink.com/xxx",
            "https://m.weibo.cn/detail/1", "https://www.zhihu.com/question/1",
            "https://www.douban.com/movie/1", "https://www.reddit.com/r/x/",
            "https://x.com/user/status/1", "https://www.pixiv.net/artworks/123456",
        ]
        for u in ok:
            self.assertTrue(vw.is_media_url(u), f"应识别为媒体链接: {u}")
        # 纯文字站不在白名单（避免「只看媒体」模式下被吞）
        no = [
            "https://github.com/owner/repo",
            "https://blog.csdn.net/abc/article/details/1",
            "https://www.gitee.com/owner/repo",
            "https://example.com/abc",
        ]
        for u in no:
            self.assertFalse(vw.is_media_url(u), f"不应识别为媒体链接: {u}")

    def test_is_pixiv_url(self):
        self.assertTrue(vw.is_pixiv_url("https://www.pixiv.net/artworks/123456"))
        self.assertTrue(vw.is_pixiv_url("https://pixiv.net/i/123456"))
        self.assertFalse(vw.is_pixiv_url("https://www.bilibili.com/video/BV1"))

    def test_build_media_context_image(self):
        meta = {"title": "插画", "uploader": "画师A", "tags": ["原创", "风景"],
                "page_count": 3, "duration": 0}
        ctx = vw.build_media_context(meta, image_descs=["蓝色的天空", "远处的山"])
        self.assertIn("【图文观看】", ctx)
        self.assertIn("插画", ctx)
        self.assertIn("画师A", ctx)
        self.assertIn("原创 / 风景", ctx)
        self.assertIn("蓝色的天空", ctx)
        self.assertIn("远处的山", ctx)

    def test_build_media_context_video(self):
        meta = {"title": "做菜", "duration": 125}
        ctx = vw.build_media_context(meta, frame_descs=["厨房", "出锅"])
        self.assertIn("【视频观看】", ctx)
        self.assertIn("2分05秒", ctx)

    def test_resolve_pixiv_token_env(self):
        with patch.dict(os.environ, {"PIXIV_REFRESH_TOKEN": "ENVTOK"}):
            self.assertEqual(vw._resolve_pixiv_token(), "ENVTOK")

    def test_resolve_pixiv_token_missing(self):
        # 无环境变量、data/pixiv_token.txt 不存在 → 返回空（看图功能优雅未启用）
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(vw._resolve_pixiv_token(), "")


class TestWatchVideo(unittest.TestCase):
    def test_success(self):
        llm = FakeLLM(["第一帧：厨房", "第二帧：出锅"])
        meta = {"title": "做菜", "duration": 60}
        # watch_video 现统一走 download_media（返回 [(path,kind), meta]）
        # 【2026-11-09 修·测试依赖宿主有没有 ffmpeg】原来只 mock 了 download_media
        # 和 sample_frames，但 watch_video 里那道闸是
        #     if video_files and ffmpeg_exe:  → 抽帧
        # `ffmpeg_exe = _resolve_ffmpeg()` 是**前置能力探测**，没被 mock：
        # 本机装了 ffmpeg 就绿、没装就红（日志里那句「无 ffmpeg，跳过抽帧」），
        # 而 sample_frames 这个 mock 从头到尾没被调用过 —— 也就是说这条用例的
        # 结论取决于跑它的机器，而不是代码。前置探测一起 mock 掉，才真的在验
        # 「有帧时会用帧」。
        with patch.object(vw, "download_media", new=AsyncMock(
                return_value=([("/tmp/x.mp4", "video")], meta))), \
             patch.object(vw, "sample_frames", new=AsyncMock(return_value=[b"jpg1", b"jpg2"])), \
             patch.object(vw, "_resolve_ffmpeg", return_value="/fake/ffmpeg"):
            out = asyncio.run(vw.watch_video(llm, "https://www.bilibili.com/video/BV1", frames=2))
        self.assertIsNotNone(out)
        self.assertIn("做菜", out)
        self.assertIn("厨房", out)
        self.assertIn("出锅", out)
        self.assertEqual(llm.calls, 2)

    def test_download_fails_returns_none(self):
        llm = FakeLLM([])
        # download_media 抛错 → watch_video 应返回 None（调用方退回网页摘要）
        with patch.object(vw, "download_media", new=AsyncMock(side_effect=RuntimeError("boom"))):
            out = asyncio.run(vw.watch_video(llm, "https://www.bilibili.com/video/BV1"))
        self.assertIsNone(out)  # 调用方应退回网页摘要

    def test_non_video_url_returns_none(self):
        llm = FakeLLM([])
        out = asyncio.run(vw.watch_video(llm, "https://example.com/abc"))
        self.assertIsNone(out)


class TestIngestMediaIntegration(unittest.TestCase):
    def test_video_routed_to_watch(self):
        # watch_videos=True 时，视频链接走观看而非网页抓取
        captured = {}

        async def fake_watch(llm, url, *, frames=4, on_progress=None):
            captured["url"] = url
            return "【视频观看】标题：测试视频"

        async def fake_fetch(url):
            captured["fetched"] = url  # 不应被调用
            return "摘要"

        with patch.object(mi, "watch_video", new=fake_watch), \
             patch.object(mi, "_fetch_page", new=fake_fetch):
            ctx = asyncio.run(mi.ingest_media(
                FakeLLM([]), None,
                "快看这个 https://www.bilibili.com/video/BV1xx", [],
                ingest_images=False, ingest_urls=True, store_to_memory=False,
                watch_videos=True))
        self.assertIn("【视频观看】标题：测试视频", ctx)
        self.assertEqual(captured.get("fetched"), None)  # 没走网页抓取

    def test_video_fallback_to_page_on_watch_none(self):
        # watch 返回 None（如依赖缺失）→ 退回网页摘要
        async def fake_watch(llm, url, *, frames=4, on_progress=None):
            return None
        async def fake_fetch(url):
            return "网页摘要内容"

        with patch.object(mi, "watch_video", new=fake_watch), \
             patch.object(mi, "_fetch_page", new=fake_fetch):
            ctx = asyncio.run(mi.ingest_media(
                FakeLLM([]), None,
                "https://www.bilibili.com/video/BV1xx", [],
                ingest_images=False, ingest_urls=True, store_to_memory=False,
                watch_videos=True))
        self.assertTrue(any("网页摘要内容" in c for c in ctx))

    def test_non_video_never_calls_watch(self):
        async def fake_watch(llm, url, *, frames=4, on_progress=None):
            raise AssertionError("不应调用 watch")
        async def fake_fetch(url):
            return "摘要"

        with patch.object(mi, "watch_video", new=fake_watch), \
             patch.object(mi, "_fetch_page", new=fake_fetch):
            ctx = asyncio.run(mi.ingest_media(
                FakeLLM([]), None,
                "看这篇 https://example.com/article", [],
                ingest_images=False, ingest_urls=True, store_to_memory=False,
                watch_videos=True))
        self.assertTrue(any("摘要" in c for c in ctx))


if __name__ == "__main__":
    unittest.main(verbosity=2)
