"""陪你看视频（G 脚手架扩展）。

把网页 / 抖音 / B站 / YouTube 等视频链接 → 取原视频 → 抽关键帧 →
现有 VLM(vision_describe) 看画面 → 组装成大脑能吃的"视频理解"上下文。

设计原则（与 media_ingest 一致）：
  - 依赖懒加载：yt_dlp / imageio_ffmpeg 缺失时优雅降级（退回网页摘要），不崩。
  - 纯函数拆出（build_video_context / is_video_url）便于离线单测。
  - 不直接调 TTS / 记忆；只产出上下文文本，由调用方（media_ingest）拼进消息。

外部依赖（managed venv，可缺失）：
  - yt_dlp：下载原视频 + 元数据（B站 1080P30 无需登录；抖音/通用在支持列表）。
  - imageio_ffmpeg：自带 ffmpeg 二进制，用于抽关键帧（免去系统装 ffmpeg）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
import time
from collections import deque
from typing import Any, Optional

logger = logging.getLogger("channels.video_watch")

# ----------------- 诊断（开发者页「陪你看视频」卡片用） -----------------
# 最近 N 次观看记录（内存态，重启即清），用于排查"为什么看不了/看得很慢"。
_DIAG_RUNS: deque = deque(maxlen=10)


def get_diagnostics() -> dict:
    """返回看视频功能的诊断信息，供开发者页排查问题。

    - yt_dlp: 已装返回版本号字符串，未装返回 None（这是"看不了视频"最常见原因）。
    - ffmpeg: 抽帧用的可执行文件路径；None 表示既无 imageio_ffmpeg 也无系统 ffmpeg。
    - runs: 最近 10 次观看记录（url/是否成功/耗时/各阶段/错误信息）。
    """
    yt = None
    try:
        import yt_dlp  # noqa: F401
        # 注意：yt_dlp.version 是子模块（不是字符串），真实版本号在 version.__version__
        v = getattr(yt_dlp, "__version__", None)
        if not isinstance(v, str):
            vm = getattr(yt_dlp, "version", None)
            v = getattr(vm, "__version__", None) if vm is not None else None
        yt = v if isinstance(v, str) else "已安装"
    except Exception:  # noqa: BLE001
        yt = None
    return {
        "yt_dlp": yt,
        "ffmpeg": _resolve_ffmpeg(),
        "runs": list(_DIAG_RUNS),
        # 【2026-10-02】Cookie 引导：抖音必须（B站/YouTube 不需要）。开发者页据此显示
        # "抖音 Cookie 已配置/未配置"，未配置时给导入指引，而不是等实测失败才提示。
        "has_cookies": bool(_resolve_cookies_file()),
        "cookies_file": _resolve_cookies_file() or "",
    }

# 已知「媒体平台」host（命中才走"陪你看"媒体路径：视频 / 图文 / 图片）。
# 仅收录确实含视频或图文的平台；纯文字站（GitHub / CSDN / Gitee 等）不收录，
# 以免在"只看媒体"模式（ingest_urls=False）下被路由到媒体路径后静默丢弃。
# 参考 AstrBot media_parser 覆盖的平台，结合本项目实际能跑通的后端（yt-dlp / 抖音自研 / Pixiv）筛选。
_MEDIA_HOST_RE = re.compile(
    r"(?:https?://)?(?:"
    # —— 视频 / 长短视频 ——
    r"(?:www\.|m\.|player\.)?bilibili\.com"
    r"|b23\.tv"                                   # B站短链
    r"|t\.bilibili\.com"                          # B站动态（图文/视频）
    r"|(?:v\.|www\.)?douyin\.com"
    r"|iesdouyin\.com"
    r"|(?:www\.)?tiktok\.com"
    r"|vm\.tiktok\.com"                           # TikTok 短链
    r"|(?:www\.|m\.)?youtube\.com"
    r"|youtu\.be"
    r"|(?:www\.)?ixigua\.com"                      # 西瓜视频
    r"|(?:www\.)?kuaishou\.com"                    # 快手
    r"|v\.kuaishou\.com"
    r"|(?:www\.|m\.)?xiaohongshu\.com"             # 小红书（视频笔记 / 图文笔记）
    r"|xhslink\.com"                              # 小红书短链
    r"|(?:www\.|m\.)?weibo\.com"
    r"|(?:m\.|www\.)?weibo\.cn"                    # 微博
    r"|acfun\.cn"                                 # AcFun
    r"|music\.163\.com"                            # 网易云音乐
    r"|163cn\.tv"                                 # 网易云短链
    r"|ximalaya\.com"                             # 喜马拉雅
    r"|tieba\.baidu\.com"                          # 百度贴吧
    r"|(?:bbs\.|www\.)?nga\.cn"                    # NGA
    r"|ngabbs\.com"
    r"|zhihu\.com"                                # 知乎（视频/回答）
    r"|douban\.com"
    r"|doubanio\.com"                             # 豆瓣
    r"|v2ex\.com"                                 # V2EX
    r"|reddit\.com"                               # Reddit
    r"|(?:www\.|mobile\.|m\.)?twitter\.com"
    r"|x\.com"                                    # Twitter / X
    r"|store\.steampowered\.com"
    r"|steamcommunity\.com"                        # Steam
    r"|pixiv\.net"                                # Pixiv（图片 / 漫画）
    r"|pixiv\.com"
    r")",
    re.IGNORECASE,
)

# 图片扩展名（用于把 yt-dlp / Pixiv 下载产物按类型分类：视频 vs 图片）
_IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "bmp", "avif", "svg"}
_VIDEO_EXTS = {"mp4", "mkv", "webm", "mov", "flv", "avi", "m4v", "ts"}


def is_media_url(url: str) -> bool:
    """判断一个链接是否命中已知媒体平台（视频 / 图文 / 图片），决定是否走"陪你看"。"""
    if not url:
        return False
    return bool(_MEDIA_HOST_RE.search(url.strip()))


# 兼容旧名：历史测试与调用方（media_ingest / dialogue）仍用 is_video_url，语义已扩展为"媒体"。
is_video_url = is_media_url


def is_pixiv_url(url: str) -> bool:
    """是否为 Pixiv 作品链接（图片 / 漫画）。"""
    return bool(url) and ("pixiv.net" in url or "pixiv.com" in url)


# ----------------- ffmpeg 解析 -----------------
def _resolve_ffmpeg() -> Optional[str]:
    """返回 ffmpeg 可执行路径（优先 imageio_ffmpeg 自带，其次系统 PATH）。"""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except Exception:  # noqa: BLE001
        logger.debug("imageio_ffmpeg 不可用，尝试系统 ffmpeg")
    return shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")


def _parse_duration(text: str) -> float:
    """从 ffmpeg -i 的 stderr 里解析 Duration（秒）。解析不到返回 0。"""
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text or "")
    if not m:
        return 0.0
    h, mm, ss = float(m.group(1)), float(m.group(2)), float(m.group(3))
    return h * 3600 + mm * 60 + ss


# ----------------- 下载原视频 -----------------
def _make_dl_hook(on_download_progress):
    """构造 yt-dlp progress_hooks：下载中按百分比回调（同步，hook 内调用）。"""
    def _hook(d):
        if not on_download_progress:
            return
        if d.get("status") != "downloading":
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        got = d.get("downloaded_bytes") or 0
        if total:
            try:
                on_download_progress(int(got / total * 100))
            except Exception:  # noqa: BLE001
                pass
    return _hook


def _resolve_cookies_file() -> str:
    """找一个可用的 cookies.txt（抖音必需）。

    查找顺序：环境变量 YTDL_COOKIES_FILE → 项目 data/cookies.txt → 用户目录 yt-cookies.txt。
    找不到返回空串（此时 B站/YouTube 仍可正常下载，只是抖音不行）。
    """
    try:
        cands = []
        env = os.environ.get("YTDL_COOKIES_FILE", "").strip()
        if env:
            cands.append(env)
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.dirname(os.path.dirname(here))          # .../src/channels → 项目根
        cands.append(os.path.join(root, "data", "cookies.txt"))
        cands.append(os.path.join(root, "yt-cookies.txt"))
        home = os.path.expanduser("~")
        cands.append(os.path.join(home, "yt-cookies.txt"))
        for c in cands:
            if c and os.path.isfile(c) and os.path.getsize(c) > 0:
                return c
    except Exception:  # noqa: BLE001
        logger.debug("查找 cookies.txt 失败", exc_info=True)
    return ""


def _humanize_dl_error(url: str, err: BaseException) -> str:
    """把 yt-dlp 的英文报错翻译成用户看得懂的中文（尤其是平台限制类）。"""
    s = str(err or "")
    low = s.lower()
    if "cookie" in low and ("douyin" in low or "tiktok" in low or "fresh cookies" in low):
        return ("抖音解析失败：软件默认已用「匿名身份」自动获取（无需登录），若仍报 Cookie 相关，\n"
                "一般是网络波动或被抖音风控，稍后重试即可。\n"
                "   · B站 / YouTube / 微博 等一直可以直接看，不受影响。\n"
                "   · 想看「需登录才能看」的抖音内容：从浏览器装 cookies.txt 扩展导出 Cookie，"
                "放到项目 data/cookies.txt（或设环境变量 YTDL_COOKIES_FILE），即可增强。")
    if "login" in low or "sign in" in low or "会员" in s:
        return "该链接需要登录才能访问，试试换一个公开视频。"
    if "private" in low or "私密" in s:
        return "这是私密视频，无法抓取。"
    if "geo" in low or "region" in low or "地区" in s:
        return "该视频有地区限制，当前网络无法访问。"
    if "unavailable" in low or "deleted" in low or "已删除" in s:
        return "视频不存在或已被删除。"
    if "unsupported url" in low:
        return f"yt-dlp 不支持这个链接：{url[:80]}"
    return s.strip()[:300] or f"{type(err).__name__}"


# ----------------- 抖音免登录解析（2026-10-02 实锤修复） -----------------
# 实测结论：抖音 v.douyin.com 短链跟随重定向能拿到 /video/<id>（不像早期被弹回首页），
# 且 aweme/v1/web/aweme/detail 接口只需「匿名新鲜 Cookie」(ttwid + __ac_nonce)，
# 不必登录。yt-dlp 的 DouyinIE 因没把匿名 Cookie 带对（且缺 Referer）而报
# "Fresh cookies (not necessarily logged in) are needed"，故这里自己打接口直取无水印直链。
# 走不到/失败时退回原 yt-dlp(+cookies.txt) 路径，保证不退化。
_DOUYIN_API_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def _is_douyin_url(url: str) -> bool:
    return bool(url) and ("douyin.com" in url or "iesdouyin.com" in url)


def _douyin_resolve_id(url: str) -> Optional[str]:
    """把抖音短链/分享链解析成纯 video_id（跟随重定向）。失败返回 None。"""
    m = re.search(r"/video/(\d+)", url or "")
    if m:
        return m.group(1)
    # 【2026-10-05】加重试：抖音短链跳转偶发超时/风控，一次失败就整体退回 yt-dlp，
    # 而 yt-dlp 对抖音**必然**报 "Fresh cookies ... are needed"（它没带匿名身份）
    # ⇒ 表现为"抖音看不了"。实测同一条链重试即可成功，故这里退避重试 3 次。
    for attempt in range(3):
        try:
            import requests  # 抖音解析依赖 requests；缺失时调用方会退回 yt-dlp
            hdrs = {"User-Agent": _DOUYIN_API_UA, "Referer": "https://www.douyin.com/"}
            r = requests.get(url.strip(), headers=hdrs, allow_redirects=True, timeout=25)
            m = re.search(r"/video/(\d+)", r.url or "")
            if m:
                return m.group(1)
            m = re.search(r"/share/video/(\d+)", r.text or "")
            if m:
                return m.group(1)
        except Exception:  # noqa: BLE001
            logger.debug("抖音短链解析失败（第 %d 次）", attempt + 1, exc_info=True)
        if attempt < 2:
            time.sleep(0.8 * (attempt + 1))
    return None


def _douyin_anon_cookies() -> dict:
    """获取匿名新鲜 Cookie（ttwid + __ac_nonce），无需登录。返回 name->value。

    ⚠️ 【2026-10-05】ttwid 注册接口（ttwid.bytedance.com）下发的 domain 是
    `.bytedance.com`，而 detail 接口主机是 `.douyin.com` —— requests 的 CookieJar
    **不跨域发送**，所以下面把 ttwid 手动塞进 dict 随请求显式带上（技能记录的坑）。
    本函数**带重试**：匿名身份是一次性的网络交互，偶发失败不该让整个抖音功能瘫。
    """
    last_exc: Optional[BaseException] = None
    for attempt in range(3):
        cookies: dict = {}
        try:
            import requests
            s = requests.Session()
            s.headers.update({"User-Agent": _DOUYIN_API_UA})
            # 先访问首页拿 __ac_nonce 等基础匿名 cookie
            s.get("https://www.douyin.com/", timeout=15)
            if s.cookies.get("__ac_nonce"):
                cookies["__ac_nonce"] = s.cookies.get("__ac_nonce")
            # 注册 ttwid（抖音接口必需）
            r = s.post("https://ttwid.bytedance.com/ttwid/union/register/",
                       json={"region": "cn", "aid": 1768, "needFid": False,
                             "service": "www.ixigua.com",
                             "migrate_info": {"ticket": "", "source": "node"},
                             "cbUrlProtocol": "https", "union": True}, timeout=15)
            for c in r.cookies:
                if c.name == "ttwid":
                    cookies["ttwid"] = c.value
            if cookies.get("ttwid"):
                return cookies
            last_exc = RuntimeError("ttwid 注册未下发 cookie")
        except Exception as e:  # noqa: BLE001
            last_exc = e
            logger.debug("获取抖音匿名 cookie 失败（第 %d 次）", attempt + 1, exc_info=True)
        if attempt < 2:
            time.sleep(0.8 * (attempt + 1))
    logger.debug("抖音匿名身份最终失败: %s", last_exc)
    return {}


def _douyin_fetch_detail(video_id: str, cookies: dict) -> Optional[dict]:
    """打 aweme/v1/web/aweme/detail 拿视频 JSON。失败返回 None。

    Referer 必需（缺了返回空体 —— 这正是 yt-dlp 报 "Fresh cookies needed" 的真相）。
    抖音风控会偶发返回空/非 200，故带重试。
    """
    for attempt in range(3):
        try:
            import requests
            api = ("https://www.douyin.com/aweme/v1/web/aweme/detail/"
                   f"?aweme_id={video_id}")
            r = requests.get(api, headers={
                "User-Agent": _DOUYIN_API_UA,
                "Referer": "https://www.douyin.com/",
                "Accept": "application/json",
            }, cookies=cookies, timeout=20)
            if r.status_code == 200:
                det = (r.json() or {}).get("aweme_detail")
                if det:
                    return det
        except Exception:  # noqa: BLE001
            logger.debug("抖音 detail 接口失败（第 %d 次）", attempt + 1, exc_info=True)
        if attempt < 2:
            time.sleep(0.6 * (attempt + 1))
    return None


def _douyin_pick_urls(detail: dict) -> list:
    """从无水印 play_addr 收集直链候选（多个镜像 + 高画质备选），逐个试。"""
    va = (detail or {}).get("video") or {}
    cands: list = []
    for u in ((va.get("play_addr") or {}).get("url_list") or []):
        if u:
            cands.append(u)
    for b in (va.get("bit_rate") or []):
        for u in ((b.get("play_addr") or {}).get("url_list") or []):
            if u:
                cands.append(u)
    # 去重保序
    seen, out = set(), []
    for u in cands:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _douyin_download_file(play_url: str, out_path: str, cookies: dict,
                         on_progress, socket_timeout: int) -> None:
    import requests
    hdrs = {"User-Agent": _DOUYIN_API_UA, "Referer": "https://www.douyin.com/"}
    r = requests.get(play_url, headers=hdrs, cookies=cookies,
                     stream=True, timeout=socket_timeout)
    r.raise_for_status()
    total = int(r.headers.get("Content-Length") or 0)
    got = 0
    with open(out_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 256):
            if chunk:
                f.write(chunk)
                got += len(chunk)
                if total and on_progress:
                    try:
                        on_progress(int(got / total * 100))
                    except Exception:  # noqa: BLE001
                        pass


async def _download_douyin_http(
    url: str, workdir: str, ffmpeg_exe: Optional[str], *,
    max_duration: int, socket_timeout: int, on_download_progress=None,
) -> tuple[str, dict]:
    """抖音纯 HTTP 免登录（解析 ID → 匿名 Cookie → 详情接口 → 无水印直链 → 直下 mp4）。

    ⚠️ 【2026-10-08 实测】此路已**大概率被抖音拦掉**：`aweme/v1/web/aweme/detail` 现在挂了
    ArgusSecurityPlugin，纯 requests 打过去返回
    `403 Blocked by ArgusSecurityPlugin Uifid Not Found`（伪造 Uifid 头则变 Signature Not Found）
    —— Uifid / Signature(a_bogus) 都是**页面 JS 现算**的，纯 HTTP 不可能通过。
    保留它只为"偶发放行时更快"；真正的退路是 `_download_douyin_browser`（真实浏览器）。
    因此本函数失败是**预期**，失败信息会被上层包装成"改用浏览器解析"。
    """
    video_id = _douyin_resolve_id(url)
    if not video_id:
        raise RuntimeError("无法从抖音链接解析出视频 ID（短链可能被风控，稍后重试）")
    cookies = _douyin_anon_cookies()
    if not cookies.get("ttwid"):
        raise RuntimeError("获取抖音匿名身份失败（网络异常）")
    detail = _douyin_fetch_detail(video_id, cookies)
    if not detail:
        raise RuntimeError("抖音接口未返回视频信息（可能被风控，稍后重试）")
    cands = _douyin_pick_urls(detail)
    if not cands:
        raise RuntimeError("抖音视频无可用播放地址")
    va = detail.get("video") or {}
    dur_ms = va.get("duration") or detail.get("duration") or 0
    dur = float(dur_ms) / 1000.0 if dur_ms else 0.0
    author = (detail.get("author") or {}).get("nickname") or ""
    meta = {
        "id": video_id,
        "title": (detail.get("desc") or "").strip(),
        "uploader": author,
        "channel": author,
        "description": (detail.get("desc") or "").strip(),
        "duration": dur,
        "ext": "mp4",
        "webpage_url": url,
        "extractor": "douyin_anon",
    }
    if dur and dur > max_duration:
        raise RuntimeError(
            f"视频时长 {dur:.0f}s 超过上限 {max_duration}s，已跳过下载（避免卡死）")
    out = os.path.join(workdir, f"{video_id}.mp4")
    last_err: Optional[BaseException] = None
    for cand in cands:
        try:
            _douyin_download_file(cand, out, cookies,
                                  on_download_progress, socket_timeout)
            if os.path.isfile(out) and os.path.getsize(out) > 0:
                return out, meta
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning("抖音直链下载失败，换下一个: %s", e)
            continue
    raise RuntimeError("抖音视频下载失败：" + (str(last_err)[:200]
                                            if last_err else "所有直链均不可用"))


async def _download_douyin_browser(
    url: str, workdir: str, *, max_duration: int,
    socket_timeout: int, on_download_progress=None,
) -> tuple[str, dict]:
    """抖音真实浏览器退路：交给 douyin_browser 用本机 Edge 打开页面拿真实直链。

    为什么必须开浏览器：抖音的 Uifid / Signature 由页面 JS 现算（见 douyin_browser 模块头注释）。
    这里只做"拿直链 + 落地下载 + 拼元数据"，帧抽取/VLM 看画面由下游统一处理
    —— 这样 AI 的点评才基于**真画面**，而不是像失败退化时那样只看到网页标题。
    """
    from . import douyin_browser  # noqa: PLC0415  延迟导入：不用抖音就完全不加载
    info = await asyncio.to_thread(douyin_browser.resolve, url)
    if not info or not info.get("play_url"):
        raise RuntimeError("浏览器解析也没拿到播放地址（可能需登录/受限内容）")
    dur = float(info.get("duration") or 0.0)
    if dur and dur > max_duration:
        raise RuntimeError(
            f"视频时长 {dur:.0f}s 超过上限 {max_duration}s，已跳过下载（避免卡死）")
    vid = str(info.get("video_id") or "").strip() or "douyin"
    out = os.path.join(workdir, f"{vid}.mp4")
    # 直链来自 douyinvod CDN：带 Referer 即可，无需任何 Cookie（免登录）
    _douyin_download_file(str(info["play_url"]), out, {},
                          on_download_progress, socket_timeout)
    if not (os.path.isfile(out) and os.path.getsize(out) > 0):
        raise RuntimeError("浏览器解析到直链但下载为空")
    title = str(info.get("title") or "").strip()
    title = re.sub(r"\s*-\s*抖音\s*$", "", title).strip() or title
    return out, {
        "id": vid,
        "title": title,
        "uploader": "",
        "channel": "",
        "description": title,
        "duration": dur,
        "ext": "mp4",
        "webpage_url": str(info.get("url") or url),
        "extractor": "douyin_browser",
    }


async def _download_douyin(
    url: str, workdir: str, ffmpeg_exe: Optional[str], *,
    max_duration: int, socket_timeout: int, on_download_progress=None,
) -> tuple[str, dict]:
    """抖音下载总入口：**先纯 HTTP（快，偶尔能过）→ 失败就用真实浏览器**。

    返回 (本地路径, 元数据 dict)，字段与 yt-dlp 路径对齐（title/uploader/description/duration），
    供下游 build_video_context 与实测链路复用。

    【2026-10-08】顺序刻意如此：纯 HTTP 命中时 ~7s 就好；被 ArgusSecurityPlugin 拦掉
    （现在几乎是常态）时再开浏览器（~5s 起）——不会白开浏览器，也不会因为 HTTP 必然失败而放弃。
    """
    try:
        return await _download_douyin_http(
            url, workdir, ffmpeg_exe, max_duration=max_duration,
            socket_timeout=socket_timeout, on_download_progress=on_download_progress)
    except Exception as e:  # noqa: BLE001
        logger.warning("抖音纯 HTTP 解析不可用（%s）→ 改用真实浏览器解析",
                       str(e)[:140])
    return await _download_douyin_browser(
        url, workdir, max_duration=max_duration,
        socket_timeout=socket_timeout, on_download_progress=on_download_progress)


def _resolve_pixiv_token() -> str:
    """取 Pixiv refresh_token：环境变量 PIXIV_REFRESH_TOKEN → 项目 data/pixiv_token.txt。"""
    try:
        env = os.environ.get("PIXIV_REFRESH_TOKEN", "").strip()
        if env:
            return env
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.dirname(os.path.dirname(here))      # .../src/channels → 项目根
        p = os.path.join(root, "data", "pixiv_token.txt")
        if os.path.isfile(p) and os.path.getsize(p) > 0:
            with open(p, "r", encoding="utf-8") as fh:
                tok = fh.read().strip()
                if tok:
                    return tok
    except Exception:  # noqa: BLE001
        logger.debug("读取 Pixiv token 失败", exc_info=True)
    return ""


async def _yt_dlp_download(
    url: str, workdir: str, ffmpeg_exe: Optional[str] = None, *,
    max_duration: int = 1800, socket_timeout: int = 60,
    on_download_progress=None, collect_all: bool = False,
) -> tuple[list, dict]:
    """用 yt-dlp 下载媒体，返回 [(本地路径, 类型), ...] 与元数据。

    - collect_all=False：只关系单个视频文件（保持 download_video 旧契约）。
    - collect_all=True：扫描 workdir 下本次新增媒体文件，按扩展名分为 video / image
      （图文 / 图集 / 相册类链接，yt-dlp 会自动把图集拆成多张图）。
    - 时长护栏仅对视频生效；图片无时长概念，不拦。
    """
    import yt_dlp  # 懒加载：缺失即抛出，由上层降级

    common = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": socket_timeout,
        "noprogress": True,
        "http_headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
    }
    if ffmpeg_exe:
        common["ffmpeg_location"] = ffmpeg_exe
    # 带 Cookie（抖音必需；从浏览器导出的 cookies.txt）
    _ck = _resolve_cookies_file()
    if _ck:
        common["cookiefile"] = _ck
        logger.info("媒体下载: 使用 Cookie 文件 %s", _ck)

    # 1) 廉价拿元数据（含时长护栏）
    with yt_dlp.YoutubeDL({**common, "skip_download": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise RuntimeError("yt-dlp 未能解析该链接")
    dur = float(info.get("duration") or 0)
    if dur and dur > max_duration:
        raise RuntimeError(
            f"媒体时长 {dur:.0f}s 超过上限 {max_duration}s，已跳过下载（避免卡死）")

    # 2) 下载（mp4 优先）
    outtmpl = os.path.join(workdir, "%(id)s.%(ext)s")
    dl_opts = {
        **common,
        "outtmpl": outtmpl,
        "format": "best[ext=mp4]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best",
        "merge_output_format": "mp4",
        "writethumbnail": False,
        "progress_hooks": [_make_dl_hook(on_download_progress)],
    }
    with yt_dlp.YoutubeDL(dl_opts) as ydl:
        entry = ydl.extract_info(url, download=True)

    if not collect_all:
        path = ydl.prepare_filename(entry)
        if not os.path.isfile(path):
            # 某些合并场景下 ext 与实际不符，兜底 glob
            cand = [os.path.join(workdir, f) for f in os.listdir(workdir)
                    if f.startswith(str(entry.get("id", "")))]
            if cand:
                path = sorted(cand, key=lambda p: os.path.getsize(p), reverse=True)[0]
        if not os.path.isfile(path):
            raise RuntimeError("下载完成但找不到媒体文件")
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        kind = "image" if ext in _IMAGE_EXTS else "video"
        return [(path, kind)], info

    # collect_all：扫描 workdir 全部媒体文件（排除明显非媒体）
    files: list = []
    for f in os.listdir(workdir):
        fp = os.path.join(workdir, f)
        if not os.path.isfile(fp):
            continue
        ext = f.rsplit(".", 1)[-1].lower() if "." in f else ""
        if ext in _IMAGE_EXTS:
            files.append((fp, "image"))
        elif ext in _VIDEO_EXTS:
            files.append((fp, "video"))
    if not files:
        raise RuntimeError("下载完成但未产出任何媒体文件")
    return files, info


async def download_video(
    url: str, workdir: str, ffmpeg_exe: Optional[str] = None, *,
    max_duration: int = 1800, socket_timeout: int = 60,
    on_download_progress=None,
) -> tuple[str, dict]:
    """（兼容接口）只取单个视频文件，保持旧契约 (本地路径, 元数据)。

    抖音强制要求新鲜 Cookie（实测报错 `[Douyin] Fresh cookies ... needed`——即使不登录也要），
    B站/YouTube 则不需要；抖音优先走自研免登录解析，失败再退回 yt-dlp(+cookies.txt) 兜底。
    """
    if _is_douyin_url(url):
        try:
            return await _download_douyin(
                url, workdir, ffmpeg_exe, max_duration=max_duration,
                socket_timeout=socket_timeout,
                on_download_progress=on_download_progress)
        except Exception as e:  # noqa: BLE001
            logger.warning("抖音两条自研路（HTTP + 真实浏览器）都失败，退回 yt-dlp: %s", e)
    files, info = await _yt_dlp_download(
        url, workdir, ffmpeg_exe, max_duration=max_duration,
        socket_timeout=socket_timeout, on_download_progress=on_download_progress,
        collect_all=False)
    for p, kind in files:
        if kind == "video":
            return p, info
    return files[0][0], info


async def download_media(
    url: str, workdir: str, ffmpeg_exe: Optional[str] = None, *,
    max_duration: int = 1800, socket_timeout: int = 60,
    on_download_progress=None, max_pages: int = 12,
) -> tuple[list, dict]:
    """统一的媒体下载入口：视频 / 图文 / 图片 全部走这里。

    返回 (files, meta)，files 为 [(本地路径, 类型), ...]，类型 ∈ {"video","image"}。
    路由优先级：Pixiv → 自研解析（图片）；抖音 → 自研免登录（视频）；其余 → yt-dlp（视频/图集）。
    """
    # ① Pixiv：图片 / 漫画（R18 默认过滤）
    if is_pixiv_url(url):
        return await _download_pixiv(
            url, workdir, max_pages=max_pages,
            socket_timeout=socket_timeout, on_download_progress=on_download_progress)
    # ② 抖音：自研免登录（视频）
    if _is_douyin_url(url):
        try:
            p, meta = await _download_douyin(
                url, workdir, ffmpeg_exe, max_duration=max_duration,
                socket_timeout=socket_timeout,
                on_download_progress=on_download_progress)
            return [(p, "video")], meta
        except Exception as e:  # noqa: BLE001
            logger.warning("抖音两条自研路（HTTP + 真实浏览器）都失败，退回 yt-dlp: %s", e)
    # ③ 其余：yt-dlp（自动处理视频 / 图集 / 相册）
    return await _yt_dlp_download(
        url, workdir, ffmpeg_exe, max_duration=max_duration,
        socket_timeout=socket_timeout, on_download_progress=on_download_progress,
        collect_all=True)


async def _download_pixiv(
    url: str, workdir: str, *, max_pages: int = 12,
    socket_timeout: int = 60, on_download_progress=None,
) -> tuple[list, dict]:
    """用 pixivpy3 获取 Pixiv 作品原图（图片 / 漫画多页）。

    - 安全默认：R18（x_restrict / sanity_level>=6）一律过滤，绝不下载 R18 内容。
    - 需要 Pixiv 账号 refresh_token（见 _resolve_pixiv_token）；缺少则优雅报错，
      由上层退回网页摘要，不崩。
    """
    m = (re.search(r"illust/(\d+)", url) or re.search(r"artworks/(\d+)", url)
         or re.search(r"/i/(\d+)", url) or re.search(r"pid=(\d+)", url))
    if not m:
        raise RuntimeError("无法从 Pixiv 链接解析作品 ID（链接形如 .../artworks/123456）")
    illust_id = m.group(1)
    token = _resolve_pixiv_token()
    if not token:
        raise RuntimeError(
            "未配置 Pixiv 账号 refresh_token，看图功能未启用。\n"
            "   · 在 data/pixiv_token.txt 写入你的 Pixiv refresh_token，"
            "或设环境变量 PIXIV_REFRESH_TOKEN。\n"
            "   · 获取方式见 pixivpy3 文档 / Pixiv OAuth 教程。")
    try:
        from pixivpy3 import AppPixivAPI
    except Exception:  # noqa: BLE001
        raise RuntimeError("未安装 pixivpy3（pip install pixivpy3），无法获取 Pixiv 图片")

    api = AppPixivAPI()
    api.auth(refresh_token=token)
    detail = api.illust_detail(illust_id) or {}
    illust = detail.get("illust") or {}
    if not illust:
        raise RuntimeError("Pixiv 未返回作品信息（可能 404 或被风控，或需代理）")
    # 安全默认：过滤 R18
    if illust.get("x_restrict") or (illust.get("sanity_level") or 0) >= 6:
        raise RuntimeError("该 Pixiv 作品为 R18 内容，已按安全默认过滤（不下载）")
    title = (illust.get("title") or "").strip()
    author = ((illust.get("user") or {}).get("name") or "").strip()
    tags = [t.get("name") for t in (illust.get("tags") or []) if t.get("name")]
    # 收集原图地址（单页 / 多页）
    pages: list = []
    single = (illust.get("meta_single_page") or {}).get("original_image_url")
    if single:
        pages.append(single)
    else:
        for p in (illust.get("meta_pages") or []):
            iu = (p.get("image_urls") or {})
            u = iu.get("original") or iu.get("large")
            if u:
                pages.append(u)
    if not pages:
        raise RuntimeError("Pixiv 作品无可用图片地址")
    files: list = []
    total = min(len(pages), max_pages)
    for idx, u in enumerate(pages[:max_pages]):
        if on_download_progress:
            try:
                on_download_progress(int((idx + 1) / total * 100))
            except Exception:  # noqa: BLE001
                pass
        fp = os.path.join(workdir, f"{illust_id}_{idx}.jpg")
        try:
            api.download(u, path=workdir, name=os.path.basename(fp))
        except Exception as e:  # noqa: BLE001
            logger.warning("Pixiv 单页下载失败: %s", e)
            continue
        if os.path.isfile(fp) and os.path.getsize(fp) > 0:
            files.append((fp, "image"))
    if not files:
        raise RuntimeError("Pixiv 图片下载失败（原图地址可能失效或风控）")
    meta = {
        "title": title,
        "uploader": author,
        "channel": author,
        "description": " ".join(tags),
        "tags": tags,
        "duration": 0,
        "ext": "jpg",
        "webpage_url": url,
        "extractor": "pixiv",
        "page_count": len(files),
    }
    return files, meta


# ----------------- 抽关键帧 -----------------
async def sample_frames(video_path: str, n: int, ffmpeg_exe: Optional[str]) -> list[bytes]:
    """用 ffmpeg 从视频均匀抽取 n 帧，返回每帧 JPEG 字节列表。"""
    exe = ffmpeg_exe or _resolve_ffmpeg()
    if not exe or not os.path.isfile(video_path):
        return []

    async def _get_duration() -> float:
        proc = await asyncio.create_subprocess_exec(
            exe, "-i", video_path,
            stderr=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
        )
        _, err = await proc.communicate()
        return _parse_duration(err.decode("utf-8", "replace"))

    duration = await _get_duration()
    if duration <= 0:
        # 拿不到时长：只抽首帧
        return await _extract_at(exe, video_path, 0.0)
    step = duration / max(n, 1)
    frames: list[bytes] = []
    for i in range(n):
        ts = (i + 0.5) * step
        frames.extend(await _extract_at(exe, video_path, ts))
    return frames


async def _extract_at(exe: str, video_path: str, ts: float) -> list[bytes]:
    out = tempfile.mktemp(suffix=".jpg")
    proc = await asyncio.create_subprocess_exec(
        exe, "-y", "-ss", f"{ts:.3f}", "-i", video_path,
        "-frames:v", "1", "-q:v", "2", out,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.communicate()
    try:
        if os.path.isfile(out):
            with open(out, "rb") as fh:
                data = fh.read()
            return [data]
    finally:
        if os.path.isfile(out):
            os.remove(out)
    return []


# ----------------- 上下文组装（纯函数，便于单测） -----------------
def build_media_context(meta: dict, frame_descs: Optional[list] = None,
                        image_descs: Optional[list] = None,
                        source_url: str = "") -> str:
    """把元数据 + 帧描述 + 图片描述拼成一段"AI 看过的媒体"上下文。

    - 视频为主（有帧描述）→ 标题【视频观看】；
    - 图片/图集为主（无视频帧但有图片描述）→ 标题【图文观看】。
    """
    frame_descs = frame_descs or []
    image_descs = image_descs or []
    title = (meta.get("title") or "").strip()
    uploader = (meta.get("uploader") or meta.get("channel") or "").strip()
    desc = (meta.get("description") or "").strip()
    dur = float(meta.get("duration") or 0)
    tags = meta.get("tags") or []
    page_count = meta.get("page_count") or 0

    is_image_media = bool(page_count) or (not frame_descs and image_descs)
    header = "【图文观看】" if is_image_media else "【视频观看】"
    lines: list[str] = [header]
    if title:
        lines.append(f"标题：{title}")
    if uploader:
        lines.append(f"作者/来源：{uploader}")
    if dur:
        mm, ss = divmod(int(dur), 60)
        lines.append(f"时长：{mm}分{ss:02d}秒")
    if tags:
        lines.append("标签：" + " / ".join(tags[:20]))
    if desc and not tags:
        lines.append("简介：" + desc[:500])
    if source_url:
        lines.append(f"链接：{source_url}")
    if image_descs:
        lines.append(f"图片理解（AI 看了 {len(image_descs)} 张图）：")
        for i, d in enumerate(image_descs, 1):
            if d:
                lines.append(f"  {i}. {d}")
    if frame_descs:
        lines.append(f"画面理解（AI 看了 {len(frame_descs)} 帧）：")
        for i, d in enumerate(frame_descs, 1):
            if d:
                lines.append(f"  {i}. {d}")
    if not image_descs and not frame_descs:
        lines.append("（未能取到可看的画面/图片，仅基于标题/简介/标签理解）")
    return "\n".join(lines)


def build_video_context(meta: dict, frame_descs: list[str], source_url: str = "") -> str:
    """兼容旧签名：仅视频帧描述。"""
    return build_media_context(meta, frame_descs=frame_descs, source_url=source_url)


# ----------------- 编排：观看一个视频 -----------------
async def watch_video(
    llm, url: str, *, frames: int = 4, workdir: Optional[str] = None,
    max_duration: int = 1800, on_progress=None,
) -> Optional[str]:
    """观看一个视频链接，返回上下文文本；不可观看（无依赖/失败）返回 None。

    调用方（media_ingest）应在返回 None 时退回网页摘要，保证不丢上下文。
    on_progress(stage, message, done=False, pct=None): 阶段进度回调（同步，可在
    yt-dlp 钩子里调用），供桌面聊天界面前端显示"正在看视频"进度条。
    """
    t0 = time.time()
    run = {
        "url": url,
        "at": time.strftime("%H:%M:%S"),
        "stages": [],
        "ok": False,
        "error": None,
        "frames": 0,
        "elapsed": None,
        "title": None,
    }

    def _prog(stage, message, done=False, pct=None):
        # 记录阶段轨迹（开发者页诊断用；pct 仅在下载阶段有意义）
        run["stages"].append({
            "stage": stage, "pct": pct, "t": round(time.time() - t0, 2)})
        # 下载百分比会高频回调，用 debug 避免刷屏；阶段切换本身也是追踪点
        logger.debug("[视频观看] 阶段=%s pct=%s 用时%.2fs",
                     stage, pct, round(time.time() - t0, 2))
        if on_progress:
            try:
                # 【2026-10-03 增强】把 kind/title/url 一并带出，供前端气泡显示
                # 「在看什么」+ 多阶段步进器（更详细 / 更进进度）。
                # title 在早期阶段可能仍是 None（meta 尚未解析），前端会兜底显示。
                meta = {"kind": "video", "title": run.get("title"), "url": url}
                on_progress(stage, message, done, pct, meta)
            except Exception:  # noqa: BLE001
                pass

    if not is_media_url(url):
        return None
    ffmpeg_exe = _resolve_ffmpeg()
    logger.info("[媒体观看] 开始 url=%s ffmpeg=%s", url, ("有" if ffmpeg_exe else "缺失"))
    tmp = workdir or tempfile.mkdtemp(prefix="dnya_media_")
    try:
        _prog("resolve", "达妮娅正在解析这个媒体…")
        files, meta = await download_media(
            url, tmp, ffmpeg_exe, max_duration=max_duration,
            on_download_progress=lambda p: _prog(
                "download", "达妮娅正在获取媒体…", pct=p),
        )
        _prog("download", "达妮娅正在获取媒体…", pct=100)
        video_files = [p for p, k in files if k == "video"]
        image_files = [p for p, k in files if k == "image"]
        logger.info("[媒体观看] 获取完成 用时%.1fs 视频=%d 图片=%d 标题=%s",
                    time.time() - t0, len(video_files), len(image_files),
                    (meta.get("title") or "")[:40])

        # 视频：抽帧
        raw_frames: list[bytes] = []
        if video_files and ffmpeg_exe:
            try:
                _prog("frame", "达妮娅正在截取画面…")
                raw_frames = await sample_frames(video_files[0], frames, ffmpeg_exe)
                logger.info("[媒体观看] 抽帧 %d 张", len(raw_frames))
            except Exception:  # noqa: BLE001
                logger.exception("抽帧失败（继续用元数据）")
        elif video_files:
            logger.warning("[媒体观看] 无 ffmpeg，跳过抽帧（仅基于标题/简介理解）")

        # 【2026-10-03 修】vision_available 现在是 property（返回 bool），不是方法。
        # 旧写法 getattr(llm, "vision_available", lambda: False)() 对 bool 再调用
        # → TypeError: 'bool' object is not callable → 整个观看流程在下载/抽帧
        # 全部成功后仍炸掉、上下文全弃 → 用户看到"点开只会看个空壳"。
        # 兼容两种形态：property(bool) 与旧式 lambda。
        _va = getattr(llm, "vision_available", False)
        vision_ok = bool(_va() if callable(_va) else _va)
        # 视频帧 → VLM
        frame_descs: list[str] = []
        if raw_frames and vision_ok:
            _prog("describe", "达妮娅正在看画面，稍等一下～")
            for fb in raw_frames:
                try:
                    d = await llm.vision_describe(
                        fb,
                        "用一句话描述这个视频画面里能看到什么；若是截图/带文字的画面，"
                        "点出关键信息。不要过度解读。")
                    if d:
                        frame_descs.append(d.strip())
                except Exception:  # noqa: BLE001
                    logger.exception("单帧描述失败（跳过）")
            logger.info("[媒体观看] 看完画面 %d/%d 帧", len(frame_descs), len(raw_frames))
        elif raw_frames:
            logger.warning("[媒体观看] 视觉(VLM)不可用，跳过看画面（仅基于标题/简介理解）")

        # 图片 / 图集 → 直接送 VLM（不抽帧）
        image_descs: list[str] = []
        if image_files and vision_ok:
            _prog("describe", f"达妮娅正在看 {len(image_files)} 张图…")
            for ip in image_files:
                try:
                    with open(ip, "rb") as fh:
                        data = fh.read()
                    d = await llm.vision_describe(
                        data,
                        "用一句话描述这张图片里有什么；若是插画/截图/带文字的图片，"
                        "点出主体与关键信息。不要过度解读或编造。")
                    if d:
                        image_descs.append(d.strip())
                except Exception:  # noqa: BLE001
                    logger.exception("单图描述失败（跳过）")
            logger.info("[媒体观看] 看完图片 %d/%d 张", len(image_descs), len(image_files))
        elif image_files:
            logger.warning("[媒体观看] 视觉(VLM)不可用，跳过看图（仅基于标题/标签理解）")

        run["ok"] = True
        run["frames"] = len(frame_descs) + len(image_descs)
        run["title"] = (meta.get("title") or "")[:80] or None
        run["elapsed"] = round(time.time() - t0, 2)
        _DIAG_RUNS.append(dict(run))
        logger.info("[媒体观看] 完成 url=%s 总用时%.1fs 图=%d 帧=%d 标题=%s",
                    url, run["elapsed"], len(image_descs), len(frame_descs), run["title"])
        _prog("done", "看完了，这就和你聊～", done=True)
        return build_media_context(meta, frame_descs=frame_descs,
                                   image_descs=image_descs, source_url=url)
    except Exception as e:  # noqa: BLE001
        logger.exception("观看视频失败，退回网页摘要: %s", url)
        logger.warning("[视频观看] 失败 url=%s 用时%.1fs 原因=%s（将退回网页摘要/按链接文字聊）",
                       url, round(time.time() - t0, 2), str(e)[:200])
        run["ok"] = False
        # 【2026-10-02】错误翻译成人话：抖音/B站等平台的限制（Cookie/登录/地区）对
        # 开发者页与用户都是"看得懂"才有意义，yt-dlp 原始英文报错没有解释力。
        run["error"] = _humanize_dl_error(url, e)
        run["elapsed"] = round(time.time() - t0, 2)
        _DIAG_RUNS.append(dict(run))
        _prog("error", "这个视频暂时看不了，我按链接文字和你聊～", done=True)
        return None
    finally:
        if not workdir:
            shutil.rmtree(tmp, ignore_errors=True)
