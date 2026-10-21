"""免费额度耗尽 → 提醒 + 询问切换 + 健康探测（2026-10-02）。

覆盖：
 1. is_quota_exhausted 能识别额度类错误，且不误判网络抖动/Key 错误/普通 403；
 2. 额度耗尽 → 该模型进**长冷却**（30min），不再每 60s 撞墙；
 3. 触发提醒回调，且**同一模型 30 分钟内只提醒一次**（不刷屏）；
 4. 提醒里列出候选（全部候选+额度状态标注，不静默隐藏）；
 5. 手动 set_active 切到某模型 → 清除其冷却与额度标记（给重试机会）；
 6. snapshot 暴露 quota_exhausted（供 UI 显示）；
 7. 瞬时失败（超时/429）单次不冷却，连续 2 次才冷却（防抖动误杀）；
 8. probe_model 健康探测：成功恢复路由 / 失败按分类返回提示。

不需要真实网络：直接构造 VisionRouter 并调内部方法。
"""

import asyncio
import os
import sys
import time
import types
import unittest

_ollama = types.ModuleType("ollama")
_ollama.AsyncClient = object
_ollama.Client = object
sys.modules.setdefault("ollama", _ollama)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

from src.llm.vision_router import (  # noqa: E402
    VisionRouter, is_quota_exhausted, QUOTA_EXHAUSTED_COOLDOWN_S, FAIL_COOLDOWN_S)

# 今天实测的真实错误（openai_client 已把服务端错误码透传出来）
QUOTA_ERR = ("OpenAI 兼容接口调用失败 (https://dashscope.aliyuncs.com/compatible-mode/v1): "
             "Client error '403 Forbidden' [服务端: AllocationQuota.FreeTierOnly "
             "Free quota exhausted. To continue accessing the model on a paid basis]")
NET_ERR = "OpenAI 兼容接口调用失败: Connection timeout"
KEY_ERR = "Client error '401 Unauthorized' for url"


class _FakeClient:
    vision_available = True
    model_name = "fake"

    def __init__(self, name="fake", fail_err: str = ""):
        self.model_name = name
        self.fail_err = fail_err

    async def vision_describe(self, image_bytes, prompt):
        if self.fail_err:
            raise RuntimeError(self.fail_err)
        return "ok"


class _Cfg:
    """最小 cfg：vision_models 带两个 openai 候选。"""

    class ns:
        class vision_models:
            active = "vl-plus"
            enabled = True
            models = [
                types.SimpleNamespace(
                    id="vl-plus", name="Qwen-VL-Plus", provider="openai",
                    model="qwen-vl-plus", base_url="https://x/v1",
                    api_key="", timeout_s=30),
                types.SimpleNamespace(
                    id="vl-max", name="Qwen-VL-Max", provider="openai",
                    model="qwen-vl-max", base_url="https://x/v1",
                    api_key="", timeout_s=30),
            ]


def _router():
    r = VisionRouter(_Cfg())
    # 用假客户端替换真实构造（避免依赖 openai/ollama 可用性）
    for e in r._entries:
        e["client"] = _FakeClient(e["model"])
    return r


class TestQuotaDetection(unittest.TestCase):

    def test_detects_quota_errors(self):
        self.assertTrue(is_quota_exhausted(QUOTA_ERR))
        self.assertTrue(is_quota_exhausted("AllocationQuota.FreeTierOnly"))
        self.assertTrue(is_quota_exhausted("Free quota exhausted"))

    def test_no_false_positive(self):
        """网络抖动 / Key 错误 / 普通 403 不能被误判成额度问题。"""
        for s in (NET_ERR, KEY_ERR, "Client error '403 Forbidden' for url", "", None):
            self.assertFalse(is_quota_exhausted(s), f"误判: {s!r}")


class TestQuotaCooldownAndAlert(unittest.TestCase):

    def test_quota_fail_uses_long_cooldown(self):
        r = _router()
        r._mark_fail("vl-plus", QUOTA_ERR)
        left = r._cooldown_until["vl-plus"] - time.time()
        self.assertGreater(left, QUOTA_EXHAUSTED_COOLDOWN_S - 5,
                           "额度耗尽可能用长冷却（不是 60s）")
        self.assertIn("vl-plus", r._quota_exhausted)

    def test_transient_fail_needs_streak(self):
        """瞬时失败（超时/网络）：单次不冷却，连续 2 次才冷却（防抖动误杀）。"""
        r = _router()
        kind, cd = r._mark_fail("vl-plus", NET_ERR)
        self.assertEqual(cd, 0.0, "第一次瞬时失败不应冷却")
        self.assertFalse(r._in_cooldown("vl-plus"))
        kind, cd = r._mark_fail("vl-plus", NET_ERR)
        self.assertGreater(cd, 0, "连续失败达到阈值应冷却")
        left = r._cooldown_until["vl-plus"] - time.time()
        self.assertLessEqual(left, FAIL_COOLDOWN_S + 1, "普通失败仍是 60s 档冷却")
        self.assertNotIn("vl-plus", r._quota_exhausted)

    def test_alert_does_not_fire_proactively(self):
        """额度耗尽时**不主动弹窗**（2026-10-02 用户决策）。

        【2026-10-21 修·判据守着已被否决的行为】原用例
        `test_alert_fires_once_and_lists_candidates` 验的是"主动提醒一次、列出候选"。
        那个行为已被用户明确停掉，理由写在 `_fire_quota_alert` 的 docstring 里：
        额度耗尽是**持续状态**，而失败可能发生在桌面看屏 / 陪你看视频 / 聊天发图
        多条链路上，各自有各自的重试与冷却 → 主动弹窗会①用户什么都没干时突然弹出、
        ②反复弹、③措辞必然含糊。现在改成随"那次失败消息"一起提示。
        这里锁住"别再弹回来"：谁把回调重新接上，这条就红。
        """
        r = _router()
        got = []
        r.set_quota_alert_callback(lambda info: got.append(info))
        r._mark_fail("vl-plus", QUOTA_ERR)
        r._mark_fail("vl-plus", QUOTA_ERR)
        self.assertEqual(got, [], "额度耗尽不应主动弹窗（2026-10-02 决策）")
        self.assertIn("vl-plus", r._quota_exhausted,
                      "但状态必须记下来——quota_state() 与冷却都靠它")

    def test_quota_state_lists_all_exhausted(self):
        """所有耗尽的候选都要能被 quota_state() 查到（不静默隐藏）。

        这是"用户主动点『检测额度』"那条路的判据：主动弹窗停了之后，
        额度信息只能从这里拿到，漏一个就等于那个模型对用户隐身。
        """
        r = _router()
        r._mark_fail("vl-plus", QUOTA_ERR)
        r._mark_fail("vl-max", QUOTA_ERR)
        ids = [e["id"] for e in r.quota_state()["exhausted"]]
        self.assertIn("vl-plus", ids)
        self.assertIn("vl-max", ids)

    def test_no_callback_is_safe(self):
        """未注入回调时不应报错。"""
        r = _router()
        r._mark_fail("vl-plus", QUOTA_ERR)   # 不应抛异常
        self.assertIn("vl-plus", r._quota_exhausted)

    def test_set_active_clears_quota_flag(self):
        """用户主动切回 = 给一次机会，清冷却与额度标记。"""
        r = _router()
        r._mark_fail("vl-plus", QUOTA_ERR)
        self.assertIn("vl-plus", r._quota_exhausted)
        ok, err = r.set_active("vl-plus")
        self.assertTrue(ok, err)
        self.assertNotIn("vl-plus", r._quota_exhausted)
        self.assertFalse(r._in_cooldown("vl-plus"), "切换应清冷却")

    def test_snapshot_exposes_quota_exhausted(self):
        r = _router()
        r._mark_fail("vl-plus", QUOTA_ERR)
        snap = r.snapshot()
        by_id = {m["id"]: m for m in snap["models"]}
        self.assertTrue(by_id["vl-plus"]["quota_exhausted"])
        self.assertFalse(by_id["vl-max"]["quota_exhausted"])


class TestProbeModel(unittest.TestCase):
    """健康探测（借鉴 LiteLLM health check）：充值后"再试一次"立即恢复。"""

    def test_probe_success_restores(self):
        r = _router()
        r._mark_fail("vl-plus", QUOTA_ERR)
        self.assertTrue(r._in_cooldown("vl-plus"))
        res = asyncio.run(r.probe_model("vl-plus"))
        self.assertTrue(res["ok"], res)
        self.assertFalse(r._in_cooldown("vl-plus"), "探测成功应清冷却")
        self.assertNotIn("vl-plus", r._quota_exhausted, "探测成功应清额度标记")

    def test_probe_fail_returns_kind_and_hint(self):
        r = _router()
        r._entries[0]["client"] = _FakeClient("qwen-vl-plus", fail_err=QUOTA_ERR)
        res = asyncio.run(r.probe_model("vl-plus"))
        self.assertFalse(res["ok"])
        self.assertEqual(res["kind"], "quota")
        self.assertIn("控制台", res["hint"])

    def test_probe_fail_remarks_cooldown(self):
        """探测失败 = 额度仍耗尽 → 重新计 30 分钟冷却。"""
        r = _router()
        r._entries[0]["client"] = _FakeClient("qwen-vl-plus", fail_err=QUOTA_ERR)
        asyncio.run(r.probe_model("vl-plus"))
        left = r._cooldown_until["vl-plus"] - time.time()
        self.assertGreater(left, QUOTA_EXHAUSTED_COOLDOWN_S - 5)

    def test_probe_unknown_model_is_safe(self):
        r = _router()
        res = asyncio.run(r.probe_model("nope"))
        self.assertFalse(res["ok"])

    # ---------------------------------------------------------------- 产品层墓碑
    # 2026-10-22 删掉了三段死代码：webapp 的注册点、只打日志的存根方法、前端
    # quota_alert 浮层与广播分支。删之前它是"三段各自看着都在工作、合起来是死的"：
    #   Router 有回调槽位（set_quota_alert_callback）但 _fire_quota_alert 不调；
    #   webapp 注册了回调，但存根只 logger.info，从不广播；
    #   前端有浮层、有 quota_alert 分支，但后端广播 type 全集里从来没有它。
    # 只测 Router 层不够——注册点写着"主动广播给前端弹询问浮层"，谁把它加回来，
    # Router 层那条仍然绿，而主动打扰已经铺回产品里了。所以这里补产品层。

    @staticmethod
    def _code_only(path: str) -> str:
        """剥掉注释与字符串后剩下的**代码 token**。

        【为什么必须剥】删代码时按本仓惯例在原处留了"为什么删"的注释，注释里就含
        `_on_vision_quota_alert` 这个名字。不剥的话这条判据会被自己的注释满足
        （要么恒绿、要么恒红）——同一坑在 check_bridge_guards 上踩过。
        """
        import io as _io
        import tokenize as _tk
        out = []
        with _io.open(path, "rb") as f:
            for tok in _tk.tokenize(f.readline):
                if tok.type in (_tk.COMMENT, _tk.STRING):
                    continue
                out.append(tok.string)
        return " ".join(out)

    def test_product_code_does_not_register_proactive_popup(self):
        """webapp 不许再注册额度回调（产品层墓碑，2026-10-22）。

        注册点 + 存根 + 前端浮层三段一起才构成"主动弹窗"。只拆其中一段，
        另外两段还在，行为就还有可能被接回来——所以这条盯的是**注册点**：
        它一出现，就说明有人重新铺"主动打扰"这条路。
        """
        code = self._code_only(os.path.join(ROOT, "src", "ui", "webapp.py"))
        for name in ("set_quota_alert_callback", "_on_vision_quota_alert"):
            self.assertNotIn(
                name, code,
                "webapp 里又出现了 %s —— 主动弹窗这条路是 2026-10-02 用户决策停掉的，"
                "额度信息只能随失败消息给出（数据源 VisionRouter.quota_state()）" % name)

    @staticmethod
    def _strip_js_comments(txt: str) -> str:
        """剥掉 JS 的行注释与块注释，返回剩下的代码。

        【为什么必须是状态机，不能两条正则】先剥行注释会**破坏块注释结构**：
        块注释里只要出现 `//`（URL 最常见），`(?<![:\\w])//.*$` 就从那儿删到
        行尾，把同行的 `*/` 一起删掉 → 块注释变成"只有 /* 没有 */" → 第二条
        `/\\*.*?\\*/` 匹配错位，**吃掉后面大段真实代码**。

        这个坑是变异刀 M2 抓到的：把 quota_alert 分支加回 app_backend.js，
        判据却仍然绿——正因为插入点前一个块注释里含 URL，剥离后连插入行一起
        被吃掉了。没有对照组 + 变异刀，这条判据会一直"绿着失效"。

        `//` 前面是 `:` 或单词字符时不算注释起点（https:// 、a//b）。
        """
        out = []
        i, n = 0, len(txt)
        while i < n:
            if txt.startswith("/*", i):
                j = txt.find("*/", i + 2)
                i = (j + 2) if j >= 0 else n
                out.append(" ")
            elif txt.startswith("//", i) and not (
                    i > 0 and (txt[i - 1] == ":" or txt[i - 1].isalnum()
                               or txt[i - 1] == "_")):
                j = txt.find("\n", i)
                i = j if j >= 0 else n
                out.append(" ")
            else:
                out.append(txt[i])
                i += 1
        return "".join(out)

    def test_frontend_has_no_quota_alert_branch(self):
        """前端不许再出现 quota_alert 消息分发（2026-10-22）。

        后端广播 type 全集：chat_push / call_push / pet_tip / video_watch，
        外加 agent_confirm_broker 注入的 agent_confirm。**从来没有 quota_alert**——
        停掉主动弹窗后前端那个分支收不到任何消息，与浮层一起是死代码。

        剥注释后再比：注释里提这个名字是在说明"为什么删"，不算违规。
        """
        path = os.path.join(ROOT, "src", "ui", "web", "redesign", "app_backend.js")
        with open(path, "r", encoding="utf-8") as f:
            txt = f.read()
        code = self._strip_js_comments(txt)
        self.assertNotIn(
            "quota_alert", code,
            "app_backend.js 里又出现了 quota_alert —— 后端从不广播这个 type，"
            "该分支收不到消息（浮层已于 2026-10-22 删除）")


if __name__ == "__main__":
    unittest.main()
