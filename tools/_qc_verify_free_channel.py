# -*- coding: utf-8 -*-
"""端到端验证：配置展开 → 级联大脑启用 → 真的发出一次请求拿到回复。"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import load_config  # noqa: E402

FAIL = 0


def check(cond, msg):
    global FAIL
    print(("  ✅ " if cond else "  ❌ ") + msg)
    if not cond:
        FAIL += 1


cfg = load_config("config.yaml")
raw = cfg.raw

print("① 配置加载与 ${VAR} 展开")
check("${" not in str(raw.get("llm", {}).get("openai", {}).get("api_key")),
      "llm.openai.api_key 不是模板字面量")
key = raw["llm"]["openai"]["api_key"]
check(bool(key), f"llm.openai.api_key 已展开（长度 {len(key)}）")
check(raw["llm"]["openai"]["base_url"].startswith("https://open.bigmodel.cn"),
      f"主聊天端点：{raw['llm']['openai']['base_url']}")
check(raw["cascade"]["api_key"] == key, "cascade 与主聊天用同一个 key")
env_issues = [i for i in cfg.config_issues if "环境变量" in str(i)]
check(not env_issues, f"没有「环境变量未设置」告警：{env_issues[:1]}")

print("② 级联大脑（通话的\"嘴\"）是否真的启用")
from src.llm.cascade_brain import CascadeBrain  # noqa: E402
brain = CascadeBrain.from_config(cfg.ns, repo_root=".")
check(brain.enabled, f"enabled={brain.enabled} model={brain.model}")
check(len(brain.system_prompt) > 500,
      f"人设已装载（system_prompt {len(brain.system_prompt)} 字，正常 ~785）")

print("③ 模型槽位与诊断")
from src.llm.model_registry import diagnose, read_all  # noqa: E402
slots = read_all(cfg.raw)
for loc, slot, info in (slots if isinstance(slots, list) else []):
    print(f"     {loc}/{slot}: {info}")
diags = diagnose(cfg.raw)
if diags:
    for d in diags:
        print(f"     · {d}")
else:
    print("     诊断：无异常")

print("④ 真的发一条（走项目自己的客户端，不是探针）")
from src.llm.openai_client import OpenAIClient  # noqa: E402
cli = OpenAIClient(cfg)
try:
    out = asyncio.run(cli.chat([{"role": "user", "content": "用一句话介绍你自己，10 字以内"}]))
    print(f"     回复：{str(out)[:60]!r}")
    check(bool(str(out).strip()), "拿到非空回复")
except Exception as e:  # noqa: BLE001
    print(f"     ✗ {type(e).__name__}: {e}")
    FAIL += 1

print()
if FAIL:
    print(f"✗ {FAIL} 项失败")
    raise SystemExit(1)
print("✅ 全部通过：主链路已能出话")
