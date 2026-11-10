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

【2026-11-11 判据大修（第 46 轮变异探针）】63 把刀下去捅出 **40 处零判据**：

  ① **文档字符串里的判据不算判据**：原来第 ⑤ 条写着「老接口 save_text_chat_providers
     行为不变（回归）」，但整份文件**一次都没调用过老接口** —— 老接口的 8 处分支
     （重复 id / 非法 provider / 缺 model / 缺 base_url / 漏传 Key / 非法 mode /
     manual 不存在 / 写盘）全是零判据，改坏任何一处本门都照样绿。
  ② **「配置回到原样」绿在「压根没改过」上**：桩里的 save_duplex_settings 只记录调用、
     不改 raw，_Pro.set_mixed_mode 同理。于是 `⑦ 中途失败 ⇒ 配置回到原样` 无论回滚
     代码整段删掉都成立 —— 和第 44 轮「去重判据被饱和度满足」是同一型假绿。
     现在桩会**先真改内存态再失败**，回滚必须真的把改动撤掉才算过。
  ③ **副作用全被桩吃掉**：`_apply_llm_live` / `_cfg.ns` 重建 / commit_model_write
     写入收口 / 删除路径与幂等路径的写盘次数，一条判据都没有。现在全部记账。
  ④ **只走 happy path**：平铺入参、非法 provider/mode/base_url 归一、去尾斜杠、
     timeout 坏值、caps 缺键、vision、ollama 豁免、三端口回落点、关闭路径的
     两步失败、step2 失败、视觉管线失败不阻断 —— 全部零判据。
  ⑤ **判据崩 ≠ 判据错**：`③ 新增项字段完整` 原来直接下标 providers[2]，生产代码
     一坏就 IndexError 炸掉整个文件、连汇总行都没有。改成安全读取，坏实现只报
     `[FAIL]` 不崩门。

  顺带修掉一个**真 bug**（探针问出来的）：`_set_mixed_mode_atomic` 关闭路径只把
  两步成败记进 steps 却不检查，双工回 local 失败时照样返回 success=True
  （steps 里明明是 `('duplex', False)`），与它自己的 docstring「任一步失败时回滚」
  矛盾。现在关闭路径与开启路径对称地 raise，并由 ⑩ 的两条判据钉死。
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
EXPECTED_CHECKS = 94  # 实测 44（原）+ 50（第 46 轮补）；少跑的判据不算通过


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
        self._ns = None          # 不经过 setter，初始化不算「重建命名空间」
        self.ns_writes = 0

    @property
    def ns(self):
        return self._ns

    @ns.setter
    def ns(self, v):
        self._ns = v
        self.ns_writes += 1

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
    # 【第 46 轮】副作用全部记账：原来这些桩只返回成功、什么都不记，于是
    # 「热切换」「命名空间重建」有没有真发生，本门一个字都没问过。
    api._live_calls = []

    def _apply_live():
        api._live_calls.append(True)
        return {"ok": True}

    api._apply_llm_live = _apply_live
    # save_duplex_settings 会走很长的双工链路 —— 原子测试里换成记录桩。
    # _duplex_mutate 允许「切引擎时真的改内存态」，这样回滚判据才不是空跑。
    api._duplex_calls = []
    api._duplex_fail = False
    api._duplex_mutate = None

    def _save_duplex(data=None):
        api._duplex_calls.append(copy.deepcopy(data or {}))
        if api._duplex_mutate is not None:
            api._duplex_mutate(raw, data)
        if api._duplex_fail:
            return {"success": False, "error": "模拟双工切换失败"}
        return {"success": True, "engine": (data or {}).get("engine", "")}

    api.save_duplex_settings = _save_duplex
    api._vision_calls = []

    def _set_vision(on):
        api._vision_calls.append(on)
        return {"success": True, "enabled": on}

    api.set_vision_enabled = _set_vision
    return api


def _at(raw, idx, key, default=None):
    """安全读 providers[idx][key]：生产代码把表改坏时只报 FAIL，不炸整个门。"""
    try:
        item = raw["text_chat"]["providers"][idx]
        return item.get(key, default) if isinstance(item, dict) else default
    except (IndexError, KeyError, TypeError):
        return default


def _spy_commit():
    """把 src.models.commit_model_write 换成记录桩，返回 (还原函数, 调用列表)。

    生产代码是**在函数内部** `from ..models import commit_model_write`，
    所以替换包模块上的属性即可被看见（不是替换局部名字）。
    """
    import src.models as _M
    orig = _M.commit_model_write
    calls = []

    def _fake(raw, *, changed="", source=""):
        calls.append((changed, source))
        return {"normalized": 0, "key_unified": {"applied": False},
                "report": [], "ok": True}

    _M.commit_model_write = _fake

    def _restore():
        _M.commit_model_write = orig

    return _restore, calls


def _spy_unify():
    """把 src.ui.webapp._unify_bailian_key 换成记录桩（不真扩散 Key）。"""
    import src.ui.webapp as _W
    orig = _W._unify_bailian_key
    calls = []

    def _fake(raw, new_key, source=""):
        calls.append((new_key, source))
        return 0

    _W._unify_bailian_key = _fake

    def _restore():
        _W._unify_bailian_key = orig

    return _restore, calls


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
ck("③ 新增项字段完整", _at(raw, 2, "model") == "glm-4.6",
   raw["text_chat"]["providers"])
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

# ======================================================================
# ⑧～⑪：第 46 轮补上的 50 条判据。每条都对应探针里的一处 HOLE，
#        判据名与 tools/_qc_p1_incremental_mutation.py 的 expect 字字对应。
# ======================================================================
print()
print("=" * 66)
print("⑧ upsert 补充判据：入参形态 / 归一 / 副作用记账")
print("=" * 66)

raw = base_raw(); api = make_api(raw)
r = api.upsert_text_chat_provider({"id": "deepseek-chat", "model": "flat-model"})
ck("⑧ 平铺入参也认（顶层同名字段）",
   r.get("success") is True and _at(raw, 1, "model") == "flat-model", r)
raw = base_raw(); api = make_api(raw)
r = api.upsert_text_chat_provider({"provider": {"id": "deepseek-chat", "model": "nested-model"}})
ck("⑧ 嵌套入参也认（provider 子字典）",
   r.get("success") is True and _at(raw, 1, "model") == "nested-model", r)

# 隔离对照：model/base_url 都齐全、**只差 id**。若样本也缺 base_url，删掉 id 校验
# 后会被下一道「缺 base_url」校验拦下并 _restore —— 判据照样绿，绿的原因跟 id 无关
# （第 44 轮饱和度同型）。字段齐全时，id 校验一拆就会真往表里追加一条 id="" 的脏数据。
raw = base_raw(); api = make_api(raw)
snap8 = copy.deepcopy(raw)
r = api.upsert_text_chat_provider({"provider": {"model": "m", "base_url": "https://x.com/v1"}})
ck("⑧ 缺 id ⇒ 失败且表不变", r.get("success") is False and raw == snap8, r)

raw = base_raw(); api = make_api(raw)
api.upsert_text_chat_provider({"provider": {"id": "deepseek-chat", "provider": "ANTHROPIC/XYZ"}})
ck("⑧ 非法 provider 归一 openai-compatible",
   _at(raw, 1, "provider") == "openai-compatible", _at(raw, 1, "provider"))
raw = base_raw(); api = make_api(raw)
api.upsert_text_chat_provider({"provider": {"id": "deepseek-chat",
                                            "base_url": "https://api.deepseek.com/v1///"}})
ck("⑧ base_url 去尾斜杠",
   _at(raw, 1, "base_url") == "https://api.deepseek.com/v1", _at(raw, 1, "base_url"))
raw = base_raw(); api = make_api(raw)
api.upsert_text_chat_provider({"provider": {"id": "deepseek-chat", "name": "   "}})
ck("⑧ name 空回落到 id", _at(raw, 1, "name") == "deepseek-chat", _at(raw, 1, "name"))
raw = base_raw(); api = make_api(raw)
api.upsert_text_chat_provider({"provider": {"id": "deepseek-chat", "timeout_s": "不是数字"}})
ck("⑧ timeout_s 坏值回落 30", _at(raw, 1, "timeout_s") == 30, _at(raw, 1, "timeout_s"))
raw = base_raw(); api = make_api(raw)
api.upsert_text_chat_provider({"provider": {"id": "deepseek-chat", "timeout_s": "120"}})
ck("⑧ timeout_s 合法值照写", _at(raw, 1, "timeout_s") == 120, _at(raw, 1, "timeout_s"))

raw = base_raw(); api = make_api(raw)
api.upsert_text_chat_provider({"provider": {"id": "deepseek-chat", "vision": True}})
ck("⑧ vision 显式给出才写", _at(raw, 1, "vision") is True, _at(raw, 1, "vision"))
raw = base_raw(); api = make_api(raw)
api.upsert_text_chat_provider({"provider": {"id": "deepseek-chat", "model": "deepseek-chat"}})
ck("⑧ vision 不给时不凭空多出字段", "vision" not in raw["text_chat"]["providers"][1])

raw = base_raw(); api = make_api(raw)
api.upsert_text_chat_provider({"provider": {"id": "deepseek-chat",
                                            "caps": {"inputs": ["text"], "outputs": ["text"],
                                                     "features": ["tools"]}}})
_c8 = raw["text_chat"]["providers"][1].get("caps")
ck("⑧ caps 三键齐全",
   isinstance(_c8, dict) and _c8.get("inputs") == ["text"]
   and _c8.get("outputs") == ["text"] and _c8.get("features") == ["tools"], _c8)
# 隔离对照：caps 用**空字典**（三个键全缺）。若样本带了 inputs，刀改的正是
# inputs 那行的 `or []`，就变成等价变异 —— 判据绿的原因跟「缺键补空」无关。
raw = base_raw(); api = make_api(raw)
api.upsert_text_chat_provider({"provider": {"id": "deepseek-chat", "caps": {}}})
_c8 = raw["text_chat"]["providers"][1].get("caps")
ck("⑧ caps 缺键补空列表",
   isinstance(_c8, dict) and _c8.get("inputs") == []
   and _c8.get("outputs") == [] and _c8.get("features") == [], _c8)

raw = base_raw(); api = make_api(raw)
r = api.upsert_text_chat_provider({"provider": {"id": "no-url", "model": "m"}})
ck("⑧ 缺 base_url 的新端口 ⇒ 失败",
   r.get("success") is False and len(raw["text_chat"]["providers"]) == 2, r)
raw = base_raw(); api = make_api(raw)
r = api.upsert_text_chat_provider({"provider": {"id": "local-ollama", "model": "qwen",
                                                "provider": "ollama"}})
ck("⑧ ollama 允许缺 base_url",
   r.get("success") is True and _at(raw, 2, "provider") == "ollama", r)

raw = base_raw(); del raw["text_chat"]["providers"]
api = make_api(raw)
r = api.upsert_text_chat_provider({"provider": {"id": "fresh", "model": "m",
                                                "base_url": "https://x.com/v1"}})
ck("⑧ 空配置新增真的落表",
   r.get("success") is True
   and len(raw["text_chat"].get("providers", [])) == 1
   and _at(raw, 0, "id") == "fresh", (r, raw["text_chat"]))

raw = base_raw(); api = make_api(raw)
api.upsert_text_chat_provider({"provider": {"id": "deepseek-chat", "model": "x"},
                               "mode": "GARBAGE"})
ck("⑧ 非法 mode 归一 auto", raw["text_chat"]["mode"] == "auto", raw["text_chat"]["mode"])

_long_key8 = "sk-ws-" + "a" * 50
_ru8, _ucalls8 = _spy_unify()
_rc8, _ = _spy_commit()
try:
    raw = base_raw(); api = make_api(raw)
    api.upsert_text_chat_provider({"provider": {"id": "deepseek-chat", "api_key": _long_key8}})
    ck("⑧ 百炼 Key 触发 Key 归一",
       len(_ucalls8) == 1 and _ucalls8[0][0] == _long_key8
       and _ucalls8[0][1] == "upsert_text_chat_provider", _ucalls8)
finally:
    _ru8(); _rc8()

_rc8, _ccalls8 = _spy_commit()
try:
    raw = base_raw(); api = make_api(raw)
    api.upsert_text_chat_provider({"provider": {"id": "deepseek-chat", "model": "x"}})
    ck("⑧ upsert 写入收口被调用",
       len(_ccalls8) == 1 and _ccalls8[0][0] == "text_chat.providers"
       and _ccalls8[0][1] == "upsert_text_chat_provider", _ccalls8)
finally:
    _rc8()
raw = base_raw(); api = make_api(raw)
api.upsert_text_chat_provider({"provider": {"id": "deepseek-chat", "model": "x"}})
ck("⑧ upsert 命名空间重建", api._cfg.ns_writes == 1, api._cfg.ns_writes)
ck("⑧ upsert 热切换被调用", api._live_calls == [True], api._live_calls)
raw = base_raw(); api = make_api(raw)
r = api.upsert_text_chat_provider({"provider": {"id": "deepseek-chat", "model": "x"}})
ck("⑧ 返回值带 mode 与 manual_provider",
   r.get("mode") == "manual" and r.get("manual_provider") == "bailian-chat", r)

# ---------------------------------------------------------------- ⑨ delete
print()
print("=" * 66)
print("⑨ delete 补充判据：入参形态 / 过度回落 / 副作用记账")
print("=" * 66)

raw = base_raw(); api = make_api(raw)
r = api.delete_text_chat_provider(data={"id": "deepseek-chat"})
ck("⑨ data={'id':...} 入口也认",
   r.get("success") is True and r.get("removed") is True
   and len(raw["text_chat"]["providers"]) == 1, r)

# 隔离对照：三端口 + 当前 manual 指向**第二项**，删的是第三项。
# 若用两端口且当前 manual 恰好是首项，「过度回落」落到 kept[0] 正好等于原值，
# 判据绿的原因就跟「不回落」无关了。三端口才能把「多余回落」暴露出来。
raw = base_raw(); api = make_api(raw)
raw["text_chat"]["providers"].append(
    {"id": "third-chat", "name": "third", "provider": "openai-compatible",
     "base_url": "https://third.example/v1", "model": "m3", "timeout_s": 30,
     "api_key": "sk-CCC"})
raw["text_chat"]["manual_provider"] = "deepseek-chat"
r = api.delete_text_chat_provider("third-chat")   # 删的不是当前 manual
ck("⑨ 删的不是当前 manual ⇒ 不回落",
   r.get("removed") is True and raw["text_chat"]["manual_provider"] == "deepseek-chat",
   (r, raw["text_chat"]["manual_provider"]))

raw = base_raw(); api = make_api(raw)
raw["text_chat"]["providers"].append(
    {"id": "third-chat", "name": "third", "provider": "openai-compatible",
     "base_url": "https://third.example/v1", "model": "m3", "timeout_s": 30,
     "api_key": "sk-CCC"})
api.delete_text_chat_provider("bailian-chat")
ck("⑨ 三端口时回落到剩余首项",
   raw["text_chat"]["manual_provider"] == "deepseek-chat",
   raw["text_chat"]["manual_provider"])

raw = base_raw(); api = make_api(raw)
api.delete_text_chat_provider("nope-not-exist")
ck("⑨ 幂等删不写盘", _Cfg.writes == 0, _Cfg.writes)
raw = base_raw(); api = make_api(raw)
api.delete_text_chat_provider("deepseek-chat")
ck("⑨ 删除成功写一次盘", _Cfg.writes == 1, _Cfg.writes)
ck("⑨ delete 命名空间重建", api._cfg.ns_writes == 1, api._cfg.ns_writes)
ck("⑨ delete 热切换被调用", api._live_calls == [True], api._live_calls)
_rd9, _dcalls9 = _spy_commit()
try:
    raw = base_raw(); api = make_api(raw)
    api.delete_text_chat_provider("deepseek-chat")
    ck("⑨ delete 写入收口被调用",
       len(_dcalls9) == 1 and _dcalls9[0][1] == "delete_text_chat_provider", _dcalls9)
finally:
    _rd9()
raw = base_raw(); api = make_api(raw)
r = api.delete_text_chat_provider("deepseek-chat")
ck("⑨ delete 返回值带 applied_live/mode/manual_provider",
   r.get("applied_live") == {"ok": True} and r.get("mode") == "manual"
   and r.get("manual_provider") == "bailian-chat", r)

# ---------------------------------------------------------------- ⑩ atomic
print()
print("=" * 66)
print("⑩ 原子混合模式补充判据：steps 明细 / 视觉 / 回滚真生效 / 关闭路径失败")
print("=" * 66)

raw = base_raw(); api = make_api(raw)
r = api.set_mixed_mode({"enabled": True, "atomic": True})
ck("⑩ 开启 steps 顺序与内容（三段全 True）",
   r.get("steps") == [("duplex", True), ("mixed", True), ("vision", True)], r.get("steps"))
ck("⑩ 视觉管线真的被调用", api._vision_calls == [True], api._vision_calls)
ck("⑩ 原子成功路径命名空间重建", api._cfg.ns_writes == 1, api._cfg.ns_writes)

raw = base_raw(); api = make_api(raw)

def _vision_boom10(on):
    api._vision_calls.append(on)
    raise RuntimeError("视觉管线挂了")

api.set_vision_enabled = _vision_boom10
r = api.set_mixed_mode({"enabled": True, "atomic": True})
ck("⑩ 视觉失败不阻断且 steps 标记 False",
   r.get("success") is True and ("vision", False) in r.get("steps", []), r)

raw = base_raw(); api = make_api(raw)
_orig_pro10 = api._proactive.set_mixed_mode

def _pro_fail10(enabled):
    api._proactive.calls.append(("set_mixed_mode", enabled))
    return {"success": False, "error": "模拟混合开关失败"}

api._proactive.set_mixed_mode = _pro_fail10
r = api.set_mixed_mode({"enabled": True, "atomic": True})
ck("⑩ step2 失败 ⇒ success=False 且回滚",
   r.get("success") is False and r.get("rolled_back") is True
   and _Cfg.writes == 0, r)
api._proactive.set_mixed_mode = _orig_pro10

raw = base_raw(); api = make_api(raw)
r = api.set_mixed_mode({"enabled": False, "atomic": True})
ck("⑩ 关闭时混合开关真的被关",
   ("set_mixed_mode", False) in api._proactive.calls, api._proactive.calls)
ck("⑩ 关闭 steps 两段且全 True",
   r.get("steps") == [("mixed", True), ("duplex", True)], r.get("steps"))

raw = base_raw(); api = make_api(raw)
api._duplex_fail = True
r = api.set_mixed_mode({"enabled": False, "atomic": True})
ck("⑩ 关闭时双工失败 ⇒ success=False",
   r.get("success") is False and r.get("rolled_back") is True
   and _Cfg.writes == 0, r)

raw = base_raw(); api = make_api(raw)
api._proactive.set_mixed_mode = _pro_fail10
r = api.set_mixed_mode({"enabled": False, "atomic": True})
ck("⑩ 关闭时混合开关失败 ⇒ success=False",
   r.get("success") is False and _Cfg.writes == 0, r)

# ★ 回滚判据必须让桩「真的改过内存态」，否则绿在「压根没改过」上
raw = base_raw(); api = make_api(raw)
snap10 = copy.deepcopy(raw)

def _mutate_then_fail10(_raw, data):
    _raw["cloud_duplex"]["prefer_cloud"] = True
    _raw["text_chat"]["mode"] = "CLOUD-HALF-OPEN"

api._duplex_mutate = _mutate_then_fail10
api._duplex_fail = True
r = api.set_mixed_mode({"enabled": True, "atomic": True})
ck("⑩ 回滚真生效（step1 改过内存态）",
   r.get("success") is False and raw == snap10
   and raw["cloud_duplex"]["prefer_cloud"] is False,
   (r, raw["cloud_duplex"], raw["text_chat"]["mode"]))
ck("⑩ 失败时 steps 带明细", r.get("steps") == [("duplex", False)], r.get("steps"))

# ------------------------------------------------- ⑪ 老接口（文档声称有）
print()
print("=" * 66)
print("⑪ 老接口 save_text_chat_providers 回归（原门一行没调过）")
print("=" * 66)


def _old_p(pid="bailian-chat", **kw):
    p = {"id": pid, "name": "百炼", "provider": "openai-compatible",
         "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
         "model": "qwen3.8-max", "timeout_s": 30, "api_key": "sk-AAA"}
    p.update(kw)
    return p


raw = base_raw(); api = make_api(raw)
r = api.save_text_chat_providers({"providers": [_old_p(), _old_p("deepseek-chat")],
                                  "mode": "manual", "manual_provider": "bailian-chat"})
ck("⑪ 老接口整表替换写一次盘",
   r.get("success") is True and _Cfg.writes == 1
   and len(raw["text_chat"]["providers"]) == 2, (r, _Cfg.writes))

raw = base_raw(); api = make_api(raw)
r = api.save_text_chat_providers({"providers": [_old_p(), _old_p()]})
ck("⑪ 老接口重复 id ⇒ 失败", r.get("success") is False, r)

raw = base_raw(); api = make_api(raw)
r = api.save_text_chat_providers({"providers": [_old_p(provider="ANTHROPIC")]})
ck("⑪ 老接口非法 provider 归一",
   r.get("success") is True and _at(raw, 0, "provider") == "openai-compatible",
   (r, _at(raw, 0, "provider")))

raw = base_raw(); api = make_api(raw)
r = api.save_text_chat_providers({"providers": [_old_p(model="")]})
ck("⑪ 老接口缺 model ⇒ 失败", r.get("success") is False, r)

raw = base_raw(); api = make_api(raw)
r = api.save_text_chat_providers({"providers": [_old_p(base_url="")]})
ck("⑪ 老接口缺 base_url ⇒ 失败", r.get("success") is False, r)

raw = base_raw(); api = make_api(raw)
_p11 = _old_p(); _p11.pop("api_key")
r = api.save_text_chat_providers({"providers": [_p11]})
ck("⑪ 老接口漏传 api_key ⇒ 保留原 Key",
   r.get("success") is True and _at(raw, 0, "api_key") == "sk-AAA",
   (r, _at(raw, 0, "api_key")))

raw = base_raw(); api = make_api(raw)
r = api.save_text_chat_providers({"providers": [_old_p()], "mode": "GARBAGE"})
ck("⑪ 老接口非法 mode 归一 auto",
   r.get("success") is True and raw["text_chat"]["mode"] == "auto",
   (r, raw["text_chat"]["mode"]))

raw = base_raw(); api = make_api(raw)
r = api.save_text_chat_providers({"providers": [_old_p()],
                                  "manual_provider": "ghost-id"})
ck("⑪ 老接口 manual 不存在 ⇒ 失败", r.get("success") is False, r)

raw = base_raw(); api = make_api(raw)
r = api.save_text_chat_providers({"providers": [_old_p()]})
ck("⑪ 老接口热切换被调用", api._live_calls == [True], api._live_calls)

# ---------------------------------------------------------------- 条数总账
print()
print("=" * 66)
if PASS + FAIL != EXPECTED_CHECKS:
    ck("判据条数总账（少跑的判据不算通过）", False,
       f"{PASS + FAIL} != 期望 {EXPECTED_CHECKS}（有整段被跳过/异常吞掉了）")
print(f"总计: PASS={PASS}  FAIL={FAIL}")
if FAILS:
    print("失败项：")
    for f in FAILS:
        print("  -", f)
print("=" * 66)
sys.exit(0 if FAIL == 0 else 1)
