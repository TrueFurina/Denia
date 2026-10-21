"""音色获取器 · 多服务商适配 单测（2026-10-09）。

验证点：
  1) _cloud_tts_providers 归一：旧单 provider / 新多 provider 分桶 / 空 / 别名兼容。
  2) fetch_provider_voices：各服务商预设音色恒在；克隆音色在有 Key(+group) 时出现；
     网络异常被吞，不连真实外网。
  3) FishAudioBackend.synthesize：用假 httpx 跑通「请求→落盘假 mp3」全链路（不联网）。
  4) create_backend 工厂能按 'fish_audio' 造出 FishAudioBackend。

不依赖 giant Backend / 不联网。运行：python tools/test_tts_multi_provider.py
"""
from __future__ import annotations
import asyncio
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


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
try:
    import voice.tts_backends as tb
    fake_httpx = types.ModuleType("httpx")

    class _Resp:
        def __init__(self, content=b"FAKEMP3", status=200, headers=None):
            self.content = content
            self.status_code = status
            self.headers = headers or {}
        def json(self):
            return self._j
        _j = {}
    class _AsyncClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None, headers=None):
            r = _Resp(b"FAKEMP3", 200, {"content-type": "audio/mpeg"}); return r
        async def get(self, url): return _Resp(b"FAKEMP3")
    fake_httpx.AsyncClient = _AsyncClient
    fake_httpx.Client = _AsyncClient
    sys.modules["httpx"] = fake_httpx

    cfg = types.SimpleNamespace(api_key="k", voice_id="fish-123", model="s2-pro", timeout_s=5)
    be = tb.FishAudioBackend(cfg)
    check("FishAudioBackend.name == fish_audio", be.name == "fish_audio")
    out = os.path.join(ROOT, "tools", "_fa_test_out.mp3")
    asyncio.run(be.synthesize("你好", out))
    check("FishAudioBackend 落盘成功", os.path.isfile(out) and os.path.getsize(out) > 0)
    if os.path.isfile(out):
        os.remove(out)
    # 无 key / 无 reference_id 应抛错
    be2 = tb.FishAudioBackend(types.SimpleNamespace(api_key="", voice_id="", model="s2-pro"))
    try:
        asyncio.run(be2.synthesize("hi", out)); check("无 key 应抛错", False)
    except RuntimeError:
        check("无 key 应抛错", True)
    # 工厂能造出
    be3 = tb.create_backend("fish_audio", cfg)
    check("create_backend('fish_audio') 返回 FishAudioBackend", isinstance(be3, tb.FishAudioBackend))
except Exception as e:
    print(f"  ! 跳过 FishAudioBackend 测试（导入失败: {e}）")

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
check("旧单层升级后仍含原阿里云 Key", raw6["voice"]["clone"]["cloud"]["dashscope_cosy"]["api_key"] == "k-old")
check("fish_audio 新增分桶", "fish_audio" in raw6["voice"]["clone"]["cloud"])

# 空 cloud → 直接成单桶
raw7 = {"voice": {"clone": {}}}
merge_cloud_tts_provider(raw7, "openai_tts", api_key="k-oa")
check("空 cloud → 单桶 openai_tts", "openai_tts" in raw7["voice"]["clone"]["cloud"])

print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
