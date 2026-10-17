"""P1 后端增量写接口行为矩阵：upsert_text_chat_provider / delete_text_chat_provider
                              / set_mixed_mode(atomic)。

判据（对应「会丢数据」的三个后果）：
  ① 只动目标那一条 —— 其余端口的字段（尤其 api_key）逐字节不变。
  ② 不传 api_key ⇒ 保留原值；显式传空串 ⇒ 清空（不是"漏传即清空"）。
  ③ 删当前 manual 端口 ⇒ 自动回落，不留悬空 manual_provider。
  ④ 原子混合模式：只在最后写一次盘；中途失败回滚内存态。
  ⑤ 老接口 save_text_chat_providers 行为不变（回归）。

不依赖真实 pywebview：用一个最小 stub 顶掉 self._cfg / _save_config / _apply_llm_live，
只测**配置改动语义**（这才是 P1 的核心）。不写盘、不联网、不碰真实 config.yaml。
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# ★ 导入 src.ui.webapp 必须让「项目根」在 sys.path 上：
#   webapp.py 里有 `from ..config import Config`，只有作为包 src.ui.webapp 的一部分
#   导入时相对导入才成立。把 <root>/src 放上去（core 里那样）会让 ui 成为顶层包，
#   于是 `..config` 越出顶层 → ImportError: attempted relative import beyond top-level。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0
FAILS: list[str] = []


def ck(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILS.append(name)
        print(f"  [FAIL] {name}  {extra}")


# ---------------------------------------------------------------- 最小 stub
class _Cfg:
    def __init__(self, raw):
        self.raw = raw
        self.ns = None

    def save(self):
        # 记录写盘次数（原子性判据用）
        _Cfg.writes += 1
        _Cfg.last_raw = copy.deepcopy(self.raw)


class _Pro:
    def __init__(self):
        self.calls = []

    def set_mixed_mode(self, enabled):
        self.calls.append(("set_mixed_mode", enabled))
        return {"success": True, "enabled": enabled}


def make_api(raw):
    """造一个只带被测方法的实例（绕过 CharacterAPI 的完整 __init__）。"""
    from src.ui.webapp import CharacterAPI

    api = CharacterAPI.__new__(CharacterAPI)
    _Cfg.writes = 0
    _Cfg.last_raw = None
    api._cfg = _Cfg(raw)
    api._proactive = _Pro()

    def _save_config():
        api._cfg.save()

    api._save_config = _save_config
    api._apply_llm_live = lambda: {"ok": True}
    # save_duplex_settings 会走很长的双工链路 —— 原子测试里换成记录桩
    api._duplex_calls = []

    def _save_duplex(data=None):
        api._duplex_calls.append(copy.deepcopy(data or {}))
        return {"success": True, "engine": (data or {}).get("engine", "")}

    api.save_duplex_settings = _save_duplex
    api.set_vision_enabled = lambda on: {"success": True, "enabled": on}
    return api


def base_raw():
    return {
        "text_chat": {
            "mode": "manual",
            "manual_provider": "bailian-chat",
            "providers": [
                {"id": "bailian-chat", "name": "百炼", "provider": "openai-compatible",
                 "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                 "model": "qwen3.8-max", "timeout_s": 30, "api_key": "sk-AAA"},
                {"id": "deepseek-chat", "name": "deepseek", "provider": "openai-compatible",
                 "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat",
                 "timeout_s": 30, "api_key": "sk-BBB"},
            ],
        },
        "cloud_duplex": {"prefer_cloud": False, "video_enabled": False, "local_fallback": True},
    }


print("=" * 66)
print("P1 增量写接口：upsert_text_chat_provider / delete_text_chat_provider")
print("=" * 66)

# ---------- ① 只改目标：改 name/model，别人逐字节不变 ----------
raw = base_raw()
api = make_api(raw)
r = api.upsert_text_chat_provider({"provider": {"id": "deepseek-chat", "name": "DeepSeek 官方", "model": "deepseek-reasoner"}})
ck("① upsert 返回 success", r.get("success") is True, r)
pv = raw["text_chat"]["providers"]
ck("① 目标已改（name/model）", pv[1]["name"] == "DeepSeek 官方" and pv[1]["model"] == "deepseek-reasoner", pv[1])
ck("① 目标 base_url 未动", pv[1]["base_url"] == "https://api.deepseek.com/v1")
ck("① 目标 api_key 未动（未传该键⇒保留）", pv[1]["api_key"] == "sk-BBB", pv[1].get("api_key"))
ck("① 另一个端口逐字节不变", pv[0] == base_raw()["text_chat"]["providers"][0], pv[0])
ck("① 端口数量不变", len(pv) == 2, len(pv))

# ---------- ② api_key 语义 ----------
raw = base_raw()
api = make_api(raw)
api.upsert_text_chat_provider({"provider": {"id": "deepseek-chat", "api_key": ""}})
ck("② 显式空串 ⇒ 清空 Key", raw["text_chat"]["providers"][1]["api_key"] == "", raw["text_chat"]["providers"][1].get("api_key"))
raw = base_raw()
api = make_api(raw)
api.upsert_text_chat_provider({"provider": {"id": "deepseek-chat", "model": "x"}})
ck("② 不传 api_key ⇒ 保留（不是漏传即清）", raw["text_chat"]["providers"][1]["api_key"] == "sk-BBB")
raw = base_raw()
api = make_api(raw)
api.upsert_text_chat_provider({"provider": {"id": "deepseek-chat", "api_key": "sk-NEW"}})
ck("② 传新 Key ⇒ 覆盖", raw["text_chat"]["providers"][1]["api_key"] == "sk-NEW")
ck("② 传入新 Key 时不影响别家 Key", raw["text_chat"]["providers"][0]["api_key"] == "sk-AAA")

# ---------- ③ 新增（id 不存在）----------
raw = base_raw()
api = make_api(raw)
r = api.upsert_text_chat_provider({"provider": {"id": "zhipu-glm", "name": "智谱", "provider": "openai-compatible",
                                                "base_url": "https://open.bigmodel.cn/api/paas/v4",
                                                "model": "glm-4.6", "api_key": "sk-CCC"}})
ck("③ 新增返回 created=True", r.get("success") and r.get("created") is True, r)
ck("③ 追加到尾部、数量=3", len(raw["text_chat"]["providers"]) == 3, len(raw["text_chat"]["providers"]))
ck("③ 新增项字段完整", raw["text_chat"]["providers"][2]["model"] == "glm-4.6")
ck("③ 原有两端口未动", raw["text_chat"]["providers"][0]["api_key"] == "sk-AAA" and raw["text_chat"]["providers"][1]["api_key"] == "sk-BBB")

# ---------- ④ 校验失败不留垃圾 ----------
raw = base_raw()
api = make_api(raw)
r = api.upsert_text_chat_provider({"provider": {"id": "bad-new", "base_url": "https://x.com/v1"}})  # 缺 model
ck("④ 缺 model ⇒ 失败", r.get("success") is False, r)
ck("④ 缺 model 的新条目未留在表里", len(raw["text_chat"]["providers"]) == 2, len(raw["text_chat"]["providers"]))
raw = base_raw()
api = make_api(raw)
before = copy.deepcopy(raw["text_chat"]["providers"][1])
r = api.upsert_text_chat_provider({"provider": {"id": "deepseek-chat", "model": ""}})  # 改坏已有
ck("④ 改坏已有（model 空）⇒ 失败", r.get("success") is False, r)
ck("④ 失败后原有项未被破坏", raw["text_chat"]["providers"][1] == before, raw["text_chat"]["providers"][1])

# ---------- ⑤ 删除 + manual 回落 ----------
raw = base_raw()
api = make_api(raw)
r = api.delete_text_chat_provider("bailian-chat")   # 删的正是当前 manual
ck("⑤ 删除返回 removed=True", r.get("success") and r.get("removed") is True, r)
ck("⑤ 只剩 1 个端口", len(raw["text_chat"]["providers"]) == 1, len(raw["text_chat"]["providers"]))
ck("⑤ manual_provider 自动回落到剩下的", raw["text_chat"]["manual_provider"] == "deepseek-chat", raw["text_chat"]["manual_provider"])
ck("⑤ 剩下的端口未被误删", raw["text_chat"]["providers"][0]["id"] == "deepseek-chat")
raw = base_raw()
api = make_api(raw)
r = api.delete_text_chat_provider("nope-not-exist")
ck("⑤ 删不存在 ⇒ 幂等成功 removed=False", r.get("success") and r.get("removed") is False, r)
ck("⑤ 删不存在 ⇒ 表不变", len(raw["text_chat"]["providers"]) == 2)
raw = base_raw()
api = make_api(raw)
api.delete_text_chat_provider("bailian-chat")
api.delete_text_chat_provider("deepseek-chat")
ck("⑤ 删空 ⇒ manual 清空 + mode 回 auto", raw["text_chat"]["manual_provider"] == "" and raw["text_chat"]["mode"] == "auto", raw["text_chat"])

# ---------- ⑥ mode / manual_provider 独立可控 ----------
raw = base_raw()
api = make_api(raw)
r = api.upsert_text_chat_provider({"provider": {"id": "deepseek-chat", "model": "deepseek-chat"},
                                   "mode": "manual", "manual_provider": "deepseek-chat"})
ck("⑥ 切 manual 到目标端口", raw["text_chat"]["mode"] == "manual" and raw["text_chat"]["manual_provider"] == "deepseek-chat", raw["text_chat"])
raw = base_raw()
api = make_api(raw)
r = api.upsert_text_chat_provider({"provider": {"id": "deepseek-chat", "model": "deepseek-chat"},
                                   "manual_provider": "ghost-id"})
ck("⑥ manual 指向不存在 ⇒ 失败", r.get("success") is False, r)
raw = base_raw()
api = make_api(raw)
api.upsert_text_chat_provider({"provider": {"id": "deepseek-chat", "model": "deepseek-chat"}})
ck("⑥ 不给 mode/manual ⇒ 原值不变", raw["text_chat"]["mode"] == "manual" and raw["text_chat"]["manual_provider"] == "bailian-chat", raw["text_chat"])
raw = base_raw()
api = make_api(raw)
r = api.upsert_text_chat_provider({"provider": {"id": "deepseek-chat", "model": "deepseek-chat"},
                                   "mode": "manual", "manual_provider": "deepseek-chat"})
ck("⑥ 成功时只写一次盘", _Cfg.writes == 1, _Cfg.writes)

# ---------- ⑦ 原子混合模式 ----------
print()
print("=" * 66)
print("P1 原子混合模式：set_mixed_mode({atomic:true})")
print("=" * 66)

raw = base_raw()
api = make_api(raw)
r = api.set_mixed_mode({"enabled": True, "atomic": True})
ck("⑦ 开启成功", r.get("success") is True, r)
ck("⑦ 标记 atomic", r.get("atomic") is True)
ck("⑦ 双工切 cloud + 解耦看屏", any(c.get("engine") == "cloud" and c.get("decouple_screen_voice") for c in api._duplex_calls), api._duplex_calls)
ck("⑦ 混合开关被拉起", ("set_mixed_mode", True) in api._proactive.calls, api._proactive.calls)
ck("⑦ 只写一次盘（关键：不是 3 次）", _Cfg.writes == 1, _Cfg.writes)
ck("⑦ steps 三段齐全", len(r.get("steps", [])) == 3, r.get("steps"))

raw = base_raw()
api = make_api(raw)
r = api.set_mixed_mode({"enabled": False, "atomic": True})
ck("⑦ 关闭成功", r.get("success") is True, r)
ck("⑦ 关闭时双工回 local", any(c.get("engine") == "local" for c in api._duplex_calls), api._duplex_calls)
ck("⑦ 关闭只写一次盘", _Cfg.writes == 1, _Cfg.writes)

# 中途失败 → 回滚
raw = base_raw()
api = make_api(raw)
_orig = api.save_duplex_settings


def _boom(data=None):
    api._duplex_calls.append(copy.deepcopy(data or {}))
    return {"success": False, "error": "模拟双工切换失败"}


api.save_duplex_settings = _boom
snap_before = copy.deepcopy(raw)
r = api.set_mixed_mode({"enabled": True, "atomic": True})
ck("⑦ 中途失败 ⇒ success=False", r.get("success") is False, r)
ck("⑦ 中途失败 ⇒ 标记已回滚", r.get("rolled_back") is True, r)
ck("⑦ 中途失败 ⇒ 配置回到原样", raw == snap_before, (raw, snap_before))
ck("⑦ 中途失败 ⇒ 混合开关未被误开", ("set_mixed_mode", True) not in api._proactive.calls, api._proactive.calls)

# 老调用（不带 atomic）行为不变
raw = base_raw()
api = make_api(raw)
r = api.set_mixed_mode({"enabled": True})
ck("⑦ 老调用（无 atomic）仍走原路径", r.get("success") is True and r.get("atomic") is not True, r)
ck("⑦ 老调用不碰双工/不写盘", api._duplex_calls == [] and _Cfg.writes == 0, (api._duplex_calls, _Cfg.writes))

print()
print("=" * 66)
print(f"总计: PASS={PASS}  FAIL={FAIL}")
if FAILS:
    print("失败项：")
    for f in FAILS:
        print("  -", f)
print("=" * 66)
sys.exit(0 if FAIL == 0 else 1)
