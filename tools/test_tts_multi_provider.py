"""音色获取器 · 多服务商适配 单测（2026-10-09）。

验证点：
  1) _cloud_tts_providers 归一：旧单 provider / 新多 provider 分桶 / 空 / 别名兼容 /
     **全局 Key 多源兜底（cloud_tts → cloud_omni）**。
  2) fetch_provider_voices：各服务商预设音色恒在；克隆音色在有 Key(+group) 时出现；
     网络异常被吞，不连真实外网。
  3) FishAudioBackend.synthesize：用假 httpx 跑通「请求→落盘假 mp3」全链路（不联网），
     并覆盖 **HTTP 错误 / JSON(audio_url) 二次下载 / 空音频 / 缺 reference_id** 四条错路。
  4) create_backend 工厂能按 'fish_audio' 造出 FishAudioBackend。
  5) merge_cloud_tts_provider 不覆盖其它服务商；旧单层升级不丢 Key；
     **空字符串=不改**（只回传部分字段时不许把已存 Key 擦空）。
  6) TTS worker 异常隔离。

不依赖 giant Backend / 不联网。运行：python tools/test_tts_multi_provider.py

【2026-11-10 判据大修】此前 30 条判据里五处零判据 + 一处**静默跳过**：
  · 第 3 段整块包在 `try/except Exception: print("! 跳过")` 里 —— 把 synthesize 改成
    直接抛错，这段整段被吞掉，结果打成「27 通过 / 0 失败」→ **exit 0 全绿**。
    现在钉总数 EXPECTED_CHECKS，少一条就是红，且异常本身记 FAIL。
  · 全局 Key 多源兜底、merge「空字符串=不改」、JSON(audio_url) 分支、空音频、
    缺 reference_id —— 全部没有判据（改完门仍 30/0 绿）。详见
    tools/_qc_tts_multi_provider_mutation.py 十一刀。
"""
from __future__ import annotations
import asyncio
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

PASS, FAIL = 0, 0

# 本文件不用测试框架，**没有东西帮你数条数** —— 少跑一整段也不会有人喊。
# 2026-11-10 实测：把 FishAudioBackend.synthesize 改成直接抛错，第 3 段整块被
# `except Exception: print("! 跳过…")` 吞掉，结果打成「27 通过 / 0 失败」→ exit 0 全绿。
# 所以这里钉一个总数：跑完必须恰好这么多条，少一条就是红（多一条也红，提醒改这个数）。
EXPECTED_CHECKS = 45


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}" + (f"   {extra}" if extra else ""))
    else:
        FAIL += 1
        print(f"  ✗ {name}" + (f"   {extra}" if extra else ""))


def _finish() -> int:
    """汇总 + **条数总账**：静默跳过比失败更危险，必须让它红。"""
    global FAIL
    ran = PASS + FAIL
    if ran != EXPECTED_CHECKS:
        FAIL += 1
        print(f"  ✗ 判据条数 {ran} != 期望 {EXPECTED_CHECKS}"
              f"（有整段被跳过/异常吞掉了 —— 少跑的判据不算通过）")
    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    return 1 if FAIL else 0


# ---------- 1) 归一 ----------
from models.cloud_tts import (
    _cloud_tts_providers, fetch_provider_voices, CLOUD_TTS_PROVIDERS, PROVIDER_ALIASES)

print("[1] _cloud_tts_providers 归一")
# 旧单 provider
old = {"voice": {"clone": {"cloud": {"provider": "dashscope_cosy", "api_key": "k1", "model_name": "cosyvoice-v2"}}}}
c = _cloud_tts_providers(old)
check("旧格式归一出 1 个 provider", len(c) == 1 and "dashscope_cosy" in c)
check("旧格式 api_key 透传", c.get("dashscope_cosy", {}).get("api_key") == "k1")
# 别名
old_alias = {"voice": {"clone": {"cloud": {"provider": "aliyun", "api_key": "k2"}}}}
check("aliyun 别名归一到 dashscope_cosy", "dashscope_cosy" in _cloud_tts_providers(old_alias))
# 新多 provider 分桶
new = {"voice": {"clone": {"cloud": {
    "dashscope_cosy": {"api_key": "k3"},
    "fish_audio": {"api_key": "k4"},
}}}}
c2 = _cloud_tts_providers(new)
check("新格式两个 provider 都在", set(c2.keys()) == {"dashscope_cosy", "fish_audio"})
# 空
check("空 cloud → 空 dict", _cloud_tts_providers({"voice": {"clone": {}}}) == {})

# 【2026-11-10 补】全局 Key 多源兜底（2026-10-17 修「又要搞一遍 key」）
# 用户实际是在「设置 → 模型与API → 一键配置」里填的 Key，落在 cloud_tts / cloud_omni，
# 不在 voice.clone.cloud。只读 voice.clone.cloud 的话，角色卡会把已配好的服务当
# 「没配 Key」隐藏起来，用户只能手填一遍。这条路径此前**一条判据都没有**。
_fb1 = _cloud_tts_providers({
    "voice": {"clone": {"cloud": {"provider": "dashscope_cosy"}}},
    "cloud_tts": {"dashscope_cosy": {"api_key": "k-global", "model_name": "m-global"}},
})
check("cloud_tts 全局 Key 回填到分桶", _fb1.get("dashscope_cosy", {}).get("api_key") == "k-global")
check("cloud_tts 全局 model_name 一并回填",
      _fb1.get("dashscope_cosy", {}).get("model_name") == "m-global")

_fb2 = _cloud_tts_providers({
    "voice": {"clone": {"cloud": {}}},
    "cloud_omni": {"api_key": "k-omni", "model": "m-omni"},
})
check("cloud_omni 兜底：空 cloud 也能归出一个 dashscope_cosy",
      _fb2.get("dashscope_cosy", {}).get("api_key") == "k-omni")

# 已有 Key 时不许被全局 Key 覆盖（用户显式配的优先）
_fb3 = _cloud_tts_providers({
    "voice": {"clone": {"cloud": {"dashscope_cosy": {"api_key": "k-own"}}}},
    "cloud_tts": {"dashscope_cosy": {"api_key": "k-global"}},
})
check("显式 Key 优先于全局兜底", _fb3["dashscope_cosy"]["api_key"] == "k-own")

# ---------- 2) fetch_provider_voices ----------
print("[2] fetch_provider_voices（不联网）")

def fake_bailian(api_key):
    return {"success": True, "voices": [
        {"voice_id": "v-clone-1", "target_model": "cosyvoice-v3"}]}

ds = fetch_provider_voices("dashscope_cosy", {"api_key": "k", "model_name": "cosyvoice-v2"}, list_bailian_fn=fake_bailian)
# 【2026-10-21 修·数量断言】原来硬编码 `== 6`。预设清单已从 6 个减到 4 个
# （Cherry / Serena / Chelsie / Ethan）——那是正常的音色增删，不是回归。
# 判据改成「非空 + 每项都有 id」：具体集合由 `_PRESETS` 自己守，
# 这里只守"别被误清空"和"别产出没有 id 的条目（前端选不了）"。
_ds_presets = [v for v in ds if v["type"] == "preset"]
check("dashscope 预设非空且每项有 id",
      len(_ds_presets) >= 3 and all(v.get("id") for v in _ds_presets))
check("dashscope 克隆出现（fake_bailian）", any(v["id"] == "v-clone-1" for v in ds))

# 无 Key 时不查克隆，但预设仍在
ds2 = fetch_provider_voices("dashscope_cosy", {}, list_bailian_fn=fake_bailian)
check("无 Key → 无克隆", not any(v["type"] == "cloned" for v in ds2))
check("无 Key → 预设仍在", len([v for v in ds2 if v["type"] == "preset"]) == len(_ds_presets))

# minimax 需要 group_id
mm_no_group = fetch_provider_voices("minimax", {"api_key": "k"})
check("minimax 无 group → 预设 4 个、无克隆", len([v for v in mm_no_group if v["type"] == "preset"]) == 4 and not any(v["type"] == "cloned" for v in mm_no_group))

# openai 仅预设
oa = fetch_provider_voices("openai_tts", {})
check("openai 预设 6 个、无克隆", len(oa) == 6 and not any(v["type"] == "cloned" for v in oa))

# fish_audio：无 Key → 仅一个占位提示；有 Key 时走网络（此处不联网，网络异常被吞 → 占位提示）
fa_nokey = fetch_provider_voices("fish_audio", {})
check("fish_audio 无 Key → 占位提示", len(fa_nokey) == 1 and fa_nokey[0]["type"] == "preset")

# fish_audio 有 Key：用一个会抛错的 fake httpx，验证异常被吞、返回占位不崩
_real_httpx = sys.modules.get("httpx")
_fake_hx = types.ModuleType("httpx")
class _BadClient:
    def __init__(self, *a, **k): pass
    def get(self, *a, **k):
        raise RuntimeError("网络不通")
_fake_hx.Client = _BadClient
sys.modules["httpx"] = _fake_hx
try:
    fa_err = fetch_provider_voices("fish_audio", {"api_key": "k"})
    check("fish_audio 网络异常被吞、不崩", isinstance(fa_err, list) and len(fa_err) >= 1)
finally:
    if _real_httpx is not None:
        sys.modules["httpx"] = _real_httpx
    else:
        sys.modules.pop("httpx", None)

# fish_audio 有 Key 且返回 items：验证克隆解析
_fake_hx2 = types.ModuleType("httpx")
class _GoodResp:
    status_code = 200
    def json(self):
        return {"items": [{"_id": "fish-123", "title": "我的声音"}]}
class _GoodClient:
    def __init__(self, *a, **k): pass
    def get(self, *a, **k):
        return _GoodResp()
_fake_hx2.Client = _GoodClient
sys.modules["httpx"] = _fake_hx2
try:
    fa_ok = fetch_provider_voices("fish_audio", {"api_key": "k"})
    check("fish_audio 解析克隆 _id", any(v["id"] == "fish-123" for v in fa_ok))
    check("fish_audio 克隆 name 带标记", any("Fish Audio" in v["name"] for v in fa_ok if v["type"] == "cloned"))
finally:
    sys.modules.pop("httpx", None)
    if _real_httpx is not None:
        sys.modules["httpx"] = _real_httpx

# ---------- 3) FishAudioBackend ----------
print("[3] FishAudioBackend.synthesize（假 httpx）")
# 【2026-11-10 修·静默跳过】原版整块包在 `try/except Exception: print("! 跳过")` 里：
# synthesize 一旦整体抛错，这段 5 条判据**一条都不跑**，结果打成「27 通过 / 0 失败」，
# exit 0 全绿 —— 合成功能彻底坏了，门却是绿的。现在异常本身记 FAIL（并打出原因），
# 末尾再用 EXPECTED_CHECKS 兜一层「少跑就是红」。
_import_err = ""
try:
    import tempfile
    import voice.tts_backends as tb
except Exception as e:  # noqa: BLE001
    tb = None
    _import_err = f"{type(e).__name__}: {e}"
    check(f"导入 voice.tts_backends（{_import_err}）", False)

if tb is not None:
    fake_httpx = types.ModuleType("httpx")

    class _Resp:
        def __init__(self, content=b"FAKEMP3", status=200, headers=None, j=None, text=""):
            self.content = content
            self.status_code = status
            self.headers = headers or {}
            self.text = text
            self._j = j or {}
        def json(self):
            return self._j

    class _AsyncClient:
        """按 plan 返回：{status, headers, content, json, text}；并记录每次请求。"""
        plan: dict = {}
        calls: list = []
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None, headers=None):
            _AsyncClient.calls.append(("POST", url, json, headers))
            p = _AsyncClient.plan
            return _Resp(p.get("content", b"FAKEMP3"), p.get("status", 200),
                         p.get("headers", {"content-type": "audio/mpeg"}),
                         p.get("json"), p.get("text", ""))
        async def get(self, url):
            _AsyncClient.calls.append(("GET", url, None, None))
            return _Resp(b"DOWNLOADED")

    fake_httpx.AsyncClient = _AsyncClient
    fake_httpx.Client = _AsyncClient
    sys.modules["httpx"] = fake_httpx

    _tmpdir = tempfile.mkdtemp(prefix="fa_tts_")
    out = os.path.join(_tmpdir, "out.mp3")

    def _try(coro_fn):
        """跑一次 synthesize，返回异常对象（None=没抛）。"""
        try:
            asyncio.run(coro_fn())
            return None
        except Exception as e:  # noqa: BLE001
            return e

    cfg = types.SimpleNamespace(api_key="k", voice_id="fish-123", model="s2-pro", timeout_s=5)
    be = tb.FishAudioBackend(cfg)
    check("FishAudioBackend.name == fish_audio", be.name == "fish_audio")

    _AsyncClient.plan = {}
    _AsyncClient.calls = []
    err = _try(lambda: be.synthesize("你好", out))
    check("FishAudioBackend 落盘成功",
          err is None and os.path.isfile(out) and os.path.getsize(out) > 0,
          )
    if err is not None:
        print(f"      ↳ 抛了：{type(err).__name__}: {err}")
    _post = next((c for c in _AsyncClient.calls if c[0] == "POST"), None)
    _payload = (_post[2] or {}) if _post else {}
    _headers = (_post[3] or {}) if _post else {}
    check("请求体带 reference_id / format=mp3（音色与格式不能漏）",
          _payload.get("reference_id") == "fish-123" and _payload.get("format") == "mp3",
          f"payload={_payload}")
    check("请求头带 Bearer Key 与 model（换模型/鉴权靠它）",
          _headers.get("Authorization") == "Bearer k" and _headers.get("model") == "s2-pro",
          f"headers={_headers}")

    # ① HTTP 4xx：必须报错并带状态码（否则用户只看到「没声音」）
    _AsyncClient.plan = {"status": 401, "text": "invalid api key",
                         "headers": {"content-type": "text/plain"}}
    err = _try(lambda: be.synthesize("hi", out))
    check("HTTP 401 → 抛 RuntimeError 且带状态码",
          isinstance(err, RuntimeError) and "401" in str(err),
          f"{type(err).__name__ if err else None}: {err}")

    # ② JSON + audio_url：二次下载落盘（异步任务的返回形态）
    _AsyncClient.plan = {"headers": {"content-type": "application/json"},
                         "json": {"audio_url": "https://api.fish.audio/a/1.mp3"}}
    _AsyncClient.calls = []
    err = _try(lambda: be.synthesize("hi", out))
    _body = b""
    if os.path.isfile(out):
        with open(out, "rb") as f:
            _body = f.read()
        os.remove(out)
    check("JSON 返回 audio_url → 二次下载并落盘",
          err is None and _body == b"DOWNLOADED"
          and any(c[0] == "GET" and c[1].endswith("1.mp3") for c in _AsyncClient.calls),
          f"err={err} body={_body[:20]!r}")

    # ③ JSON 但没有 audio_url：不能静默写空文件
    _AsyncClient.plan = {"headers": {"content-type": "application/json"}, "json": {}}
    err = _try(lambda: be.synthesize("hi", out))
    check("JSON 缺 audio_url → 抛错（不静默）", isinstance(err, RuntimeError), f"{err}")

    # ④ 空音频：不能写出 0 字节文件（下游播放器会静默失败）
    _AsyncClient.plan = {"content": b"", "headers": {"content-type": "audio/mpeg"}}
    err = _try(lambda: be.synthesize("hi", out))
    check("空音频 → 抛错（不写 0 字节文件）",
          isinstance(err, RuntimeError) and "空音频" in str(err), f"{err}")

    # ⑤ 无 key / 无 reference_id：两条不同的报错，各自指名
    _AsyncClient.plan = {}
    be2 = tb.FishAudioBackend(types.SimpleNamespace(api_key="", voice_id="", model="s2-pro"))
    err = _try(lambda: be2.synthesize("hi", out))
    check("无 key 应抛错且指明 Key", isinstance(err, RuntimeError) and "API Key" in str(err),
          f"{err}")
    be4 = tb.FishAudioBackend(types.SimpleNamespace(api_key="k", voice_id="", model="s2-pro"))
    err = _try(lambda: be4.synthesize("hi", out))
    check("有 key 但缺 reference_id → 抛错且指明 reference_id",
          isinstance(err, RuntimeError) and "reference_id" in str(err), f"{err}")

    # 工厂能造出
    be3 = tb.create_backend("fish_audio", cfg)
    check("create_backend('fish_audio') 返回 FishAudioBackend",
          isinstance(be3, tb.FishAudioBackend))

    # 收尾：临时目录别留垃圾（原版把产物写在 tools/ 下，中途崩了会留脏文件）
    try:
        for _f in os.listdir(_tmpdir):
            os.remove(os.path.join(_tmpdir, _f))
        os.rmdir(_tmpdir)
    except OSError:
        pass

# ---------- 4) 兼容性：旧单 provider 走新逻辑得到等价预设 ----------
print("[4] 旧配置经新逻辑等价")
old_raw = {"voice": {"clone": {"cloud": {"provider": "openai_tts", "api_key": "k"}}}}
providers = _cloud_tts_providers(old_raw)
check("旧 openai 配置归一为 1 provider", len(providers) == 1)
vs = fetch_provider_voices("openai_tts", providers["openai_tts"])
check("旧 openai 配置仍出 6 预设", len(vs) == 6)

# ---------- 5) merge_cloud_tts_provider 不覆盖其它服务商 ----------
print("[5] merge_cloud_tts_provider（保留其它服务商）")
from models.cloud_tts import merge_cloud_tts_provider

# 单 provider 写入不破坏已配的另一家
raw5 = {"voice": {"clone": {"cloud": {"dashscope_cosy": {"api_key": "k-ali"}}}}}
merge_cloud_tts_provider(raw5, "minimax", api_key="k-mm", group_id="g1")
check("合并后 dashscope 仍在", "dashscope_cosy" in raw5["voice"]["clone"]["cloud"])
check("合并后 minimax 新增", "minimax" in raw5["voice"]["clone"]["cloud"])
check("dashscope 的 key 未被 minimax 覆盖", raw5["voice"]["clone"]["cloud"]["dashscope_cosy"]["api_key"] == "k-ali")

# 旧单层结构提升为分桶（不丢旧 Key）
raw6 = {"voice": {"clone": {"cloud": {"provider": "dashscope_cosy", "api_key": "k-old", "model_name": "cosyvoice-v2"}}}}
merge_cloud_tts_provider(raw6, "fish_audio", api_key="k-fa")
# 【2026-11-10 改·崩了不算判据】原版直接链式下标：一旦升级逻辑坏掉，
# 这里抛 KeyError，整份文件**崩在半路**，后面第 6 段的判据一条都不跑，
# 报告里只有一段 Traceback、没有「哪条判据红了」。改成 .get() 链 + 明确判据。
_c6 = (raw6.get("voice", {}).get("clone", {}).get("cloud", {}) or {})
check("旧单层升级后仍含原阿里云 Key",
      (_c6.get("dashscope_cosy") or {}).get("api_key") == "k-old",
      f"cloud={_c6}")
check("fish_audio 新增分桶", "fish_audio" in _c6)

# 空 cloud → 直接成单桶
raw7 = {"voice": {"clone": {}}}
merge_cloud_tts_provider(raw7, "openai_tts", api_key="k-oa")
check("空 cloud → 单桶 openai_tts", "openai_tts" in (raw7.get("voice", {}).get("clone", {}).get("cloud", {}) or {}))

# 【2026-11-10 补】「空字符串=不改」（修 F3）
# 引导页/角色保存只回传部分字段（provider/model/voice_id），不带 api_key。
# 原实现无条件写 `api_key or ""` → 把已配好的 Key 静默擦成空 → 用户"昨天还能用，
# 今天要重填 Key"。这条契约在 docstring 里写得很清楚，但**一条判据都没有**。
raw8 = {"voice": {"clone": {"cloud": {"fish_audio": {"api_key": "k-keep", "model_name": "m1"}}}}}
merge_cloud_tts_provider(raw8, "fish_audio", model_name="m2")   # 只带 model_name
_b8 = (raw8["voice"]["clone"]["cloud"].get("fish_audio") or {})
check("只回传部分字段时已存 Key 不被擦空", _b8.get("api_key") == "k-keep", f"bucket={_b8}")
check("同一次调用里显式给的字段要生效", _b8.get("model_name") == "m2", f"bucket={_b8}")

# 显式传新 Key 时应当覆盖旧值（「空=不改」不能变成「永远不改」）
merge_cloud_tts_provider(raw8, "fish_audio", api_key="k-new")
_b8b = (raw8["voice"]["clone"]["cloud"].get("fish_audio") or {})
check("显式新 Key 覆盖旧 Key", _b8b.get("api_key") == "k-new", f"bucket={_b8b}")

# 别名写入：aliyun 必须落到 dashscope_cosy 桶（否则同一家配出两个桶）
raw9 = {"voice": {"clone": {"cloud": {}}}}
merge_cloud_tts_provider(raw9, "aliyun", api_key="k-alias")
check("别名 aliyun 写入 dashscope_cosy 桶",
      "dashscope_cosy" in (raw9["voice"]["clone"].get("cloud") or {})
      and "aliyun" not in (raw9["voice"]["clone"].get("cloud") or {}),
      f"cloud={raw9['voice']['clone'].get('cloud')}")

# ---------- 6) TTS worker 异常隔离（2026-10-23）----------
# 背景：原 _worker 由 start_worker 以 asyncio.create_task 启动，既没存任务句柄也没加
# done_callback，且循环体无顶层守卫。任一条任务里的未捕获异常（如 _state.transition /
# _bus.publish 在异常态抛错、或 _speak_flow 逃逸出的异常）会直接杀死 worker 协程 → 队列
# 停止排空 → 之后所有 TTS 静默失效（"她说了却后面没声"）。本段验证修复：单条任务异常被
# 兜住后 worker 仍存活并继续处理后续任务；且 done_callback 会在 worker 异常退出时重启它。
print("[6] TTS worker 异常隔离（单条任务异常不杀死整条播放链路）")
sys.path.insert(0, ROOT)   # 让 src 成为包根（tts.py 顶层 `from ..config` 需要）
from src.voice.tts import TTS as _TTS


class _FakeState:
    async def transition(self, *a, **k):
        pass

    async def to_idle(self, *a, **k):
        pass


class _FakeBus:
    async def publish(self, *a, **k):
        pass


def _mk_tts():
    o = _TTS.__new__(_TTS)
    o._queue = asyncio.Queue()
    o._tts_stale_s = 0
    o._interrupt_until = 0.0
    o._interrupted = False
    o._streaming = False
    o._decision_seen = False
    o._pending_sentences = 0
    o._state = _FakeState()
    o._bus = _FakeBus()
    o._worker_task = None
    return o


async def _run_guard():
    o = _mk_tts()
    seen = []
    st = {"n": 0}

    async def _dispatch(payload):
        st["n"] += 1
        if st["n"] == 1:
            seen.append("boom")
            raise RuntimeError("模拟状态机/事件总线在异常态抛错")
        seen.append("ok")

    o._speak_flow = _dispatch
    await o._queue.put(("full", {"text": "会崩的一句"}))
    await o._queue.put(("full", {"text": "正常的一句"}))
    task = asyncio.ensure_future(o._worker())
    await asyncio.sleep(0.15)
    alive = not task.done()          # 关键：第一条崩了之后 worker 还活着
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        # 取消后台任务 = 用例正常收尾
        pass
    return seen, alive


_seen, _alive = asyncio.run(_run_guard())
# 鉴别力：若 worker 没有顶层守卫（原实现），第一条 boom 会直接杀死 worker 协程 →
# 第二条 ok 永远不会被分发、且 task 已 done → 本断言必败。故它能证明守卫确实在起作用。
check("worker 一条任务异常后继续：两条都被分发（不静默死亡）",
      _seen == ["boom", "ok"])
check("worker 一条任务异常后仍存活（不吞异常、不卡死队列）", _alive is True)


async def _run_cb():
    o = _mk_tts()

    async def _raises():
        raise RuntimeError("boom")

    bad = asyncio.ensure_future(_raises())
    await asyncio.sleep(0.02)          # 让其带异常结束
    o._on_worker_done(bad)             # 模拟 asyncio 调 done_callback
    new_task = o._worker_task
    restarted = new_task is not None and new_task is not bad
    if new_task is not None:           # 别让重启出来的 worker 一直阻塞在空队列
        new_task.cancel()
        try:
            await new_task
        except asyncio.CancelledError:
            # 同上：取消是预期路径
            pass
    return restarted


_restarted = asyncio.run(_run_cb())
# 鉴别力：若 _on_worker_done 没重启（原实现根本没这个回调），o._worker_task 仍是 None/原任务
# → 本断言必败。
check("done_callback 在 worker 异常退出时重启 worker（避免永久失声）",
      _restarted is True)

sys.exit(_finish())
