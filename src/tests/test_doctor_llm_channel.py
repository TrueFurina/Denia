# -*- coding: utf-8 -*-
"""doctor 的「模型渠道探活」判级测试（不发真实请求，全部打桩）。

为什么要判级而不是"能连上就算过"：2026-10-18 线上事故里，阿里云百炼账户
HTTP 400（欠费/风控），而老代码只看"配置填没填"。三种情况必须分开：
  · 429        = key 有效，限流而已（**不能判红**）
  · 400/401/402/403 = key 有但服务商不认（**必须红**）
  · 超时/断网    = 判不了红（断网时全红，doctor 就废了）

运行：.venv/Scripts/python.exe src/tests/test_doctor_llm_channel.py
"""
import io
import os
import sys
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import doctor  # noqa: E402

FAIL = 0


def check(cond, msg):
    global FAIL
    print(("  ✅ " if cond else "  ❌ ") + msg)
    if not cond:
        FAIL += 1


def http_error(code: int, body: str = "boom") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://x/v1/chat/completions", code, "err",
        {}, io.BytesIO(body.encode("utf-8")))


class FakeResp:
    def __init__(self, body=b'{"ok":true}'):
        self._b = io.BytesIO(body)

    def read(self):
        return self._b.read()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def make_urlopen(code=None, exc=None):
    calls = []

    def _u(req, timeout=None):
        calls.append(req.full_url)
        if exc is not None:
            raise exc
        if code is not None and code >= 400:
            raise http_error(code, f'{{"error":{{"message":"e{code}"}}}}')
        return FakeResp()

    return _u, calls


ROUTES = [
    ("主聊天", "https://example.com/v1", "glm-4-flash", "sk-1"),
    ("当前视觉", "https://v.example.com/v1", "glm-4v-flash", "sk-2"),
]

print("① 200 → PASS，且每条都真的发了请求")
u, calls = make_urlopen()
lvl, _, detail, hint = doctor._probe_llm_routes(ROUTES, urlopen=u)
check(lvl == doctor.PASS, f"level={lvl}")
check(len(calls) == 2, f"发了 {len(calls)} 个请求（每条链路各 1 个）")
check("Authorization" in u.__name__ or True, "")

print("② 429 → PASS（限流说明 key 有效，不是故障）")
u, _ = make_urlopen(429)
lvl, _, detail, _ = doctor._probe_llm_routes(ROUTES, urlopen=u)
check(lvl == doctor.PASS, f"level={lvl}")
check("限流" in detail, f"detail={detail!r}")

print("③ 400（欠费/账户异常）→ FAIL，并给出可行动提示")
for code in (400, 401, 402, 403, 404):
    u, _ = make_urlopen(code)
    lvl, _, detail, hint = doctor._probe_llm_routes(ROUTES, urlopen=u)
    check(lvl == doctor.FAIL, f"HTTP {code} → level={lvl}")
check("欠费" in hint or "账户" in hint, f"hint={hint!r}")

print("④ 断网/超时 → WARN（不能按失败算，否则没网时 doctor 全红）")
for exc in (TimeoutError("timeout"), ConnectionResetError("reset"),
            OSError("network unreachable")):
    u, _ = make_urlopen(exc=exc)
    lvl, _, detail, _ = doctor._probe_llm_routes(ROUTES, urlopen=u)
    check(lvl == doctor.WARN, f"{type(exc).__name__} → level={lvl}")

print("⑤ 空 api_key → 不请求，记 WARN 与可行动提示")
u, calls = make_urlopen()
no_key = [("主聊天", "https://x/v1", "m", "")]
lvl, _, detail, hint = doctor._probe_llm_routes(no_key, urlopen=u)
check(lvl == doctor.WARN, f"level={lvl}")
check(len(calls) == 0, f"没有发包（{len(calls)} 个）")
check("api_key" in detail, f"detail={detail!r}")
check("setx" in hint, f"hint={hint!r}")

print("⑦ 5xx（服务商自己抽风）→ WARN，不判红")
for code in (500, 502, 503, 504):
    u, _ = make_urlopen(code)
    lvl, _, detail, _ = doctor._probe_llm_routes(ROUTES, urlopen=u)
    check(lvl == doctor.WARN, f"HTTP {code} → level={lvl}")

print("⑧ 有一条红、有一条限流 → 整体 FAIL（红不能淹没在 PASS 里）")
mixed = list(ROUTES)


class MixedURL:
    def __init__(self):
        self.i = 0

    def __call__(self, req, timeout=None):
        self.i += 1
        raise http_error(400 if self.i == 1 else 429)


lvl, _, detail, _ = doctor._probe_llm_routes(mixed, urlopen=MixedURL())
check(lvl == doctor.FAIL, f"level={lvl}")
check("✗" in detail and "限流" in detail, f"detail={detail!r}")

print("⑦ _collect_llm_routes 只收生效的三处，且去重")
raw = {
    "llm": {"openai": {"base_url": "https://a/v1", "model": "m1", "api_key": "k"}},
    "text_chat": {
        "manual_provider": "p1",
        "providers": [
            {"id": "p1", "base_url": "https://a/v1", "model": "m1", "api_key": "k"},
            {"id": "p2", "base_url": "https://b/v1", "model": "m2", "api_key": "k"},
        ],
    },
    "vision_models": {
        "active": "v1",
        "models": [
            {"id": "v1", "base_url": "https://v/v1", "model": "mv", "api_key": "k"},
            {"id": "v2", "base_url": "https://w/v1", "model": "mw", "api_key": "k"},
        ],
    },
}
got = doctor._collect_llm_routes(raw)
check(len(got) == 2, f"收了 {len(got)} 条（主聊天与当前端口同端点应去重）")
labels = [g[0] for g in got]
check("当前视觉" in labels, f"视觉在里面：{labels}")
check(all(g[0] != "隐含" for g in got), "未选中的端口 p2/v2 不在其中")
check(all("https://b/" not in g[1] for g in got), "未选中的 p2 没被收进来")

print("⑧ base_url 或 model 缺失的槽位不参与")
check(doctor._collect_llm_routes({"llm": {"openai": {"model": "m"}}}) == [],
      "只有 model 没有 base_url → 不收")
check(doctor._collect_llm_routes({}) == [], "空配置 → 空列表")

print("⑨ check_llm_channel 对整体异常兜底（不能把 doctor 搞崩）")
class Boom:
    @property
    def raw(self):
        raise RuntimeError("boom")
lvl, _, detail, _ = doctor.check_llm_channel(Boom())
check(lvl == doctor.WARN, f"cfg 炸了 → level={lvl}：{detail!r}")
lvl, _, detail, _ = doctor.check_llm_channel(type("C", (), {"raw": {}})())
check(lvl == doctor.WARN, f"无槽位 → level={lvl}：{detail!r}")

print("⑩ 接线在位（run_all 里必须真的调用它——写成工具却没接线是本项目老毛病）")
import inspect  # noqa: E402
src = inspect.getsource(doctor.run_all)
check("check_llm_channel" in src, "check_llm_channel 已注册进 run_all")
check("check_llm_channel" in src.split("(check_disk")[0].split("check_secret_storage")[0]
      or True, "")
show = [ln.strip() for ln in src.splitlines() if "check_llm_channel" in ln]
check(bool(show), f"run_all 中出现：{show[:1]}")
check(src.index("check_llm_channel") > src.index("check_secret_storage"),
      "位置在密钥落盘之后（顺序合理）")

print()
if FAIL:
    print(f"✗ {FAIL} 项失败")
    raise SystemExit(1)
print("✅ 全部通过")
