# -*- coding: utf-8 -*-
"""盘点 config.yaml 里所有指向阿里云百炼的模型/端口，标出「active 的」与「仅备用的」。

阿里云百炼账户 2026-10-18 实测 HTTP 400 \"not in good standing\"（账户级拦截，
`GET /models` 还能列出 176 个模型，但 chat 一律拒绝）。凡是被标为 **active**
的条目 = 用户实际会用到、当前必然失败的位置。
"""
import sys

import yaml

P = "config.yaml"
raw = yaml.safe_load(open(P, encoding="utf-8"))
rows: list[tuple[str, str, str]] = []   # (性质, 位置, 说明)


def note(tag: str, where: str, what: str) -> None:
    rows.append((tag, where, what))


def scan_providers(where: str, lst: list, active: str) -> None:
    for p in lst:
        if not isinstance(p, dict):
            continue
        base = str(p.get("base_url") or "")
        if "dashscope" not in base and "aliyuncs" not in base:
            continue
        pid = p.get("id", "?")
        has_key = bool(str(p.get("api_key") or "").strip())
        if pid == active:
            tag = "ACTIVE-死key" if has_key else "ACTIVE-空key"
        elif has_key:
            tag = "备用-有key"
        else:
            tag = "备用-空key"
        note(tag, f"{where}[{pid}]",
             f"{p.get('model', '')} @ {base.split('/')[2]}")


o = (raw.get("llm") or {}).get("openai") or {}
if "dashscope" in str(o.get("base_url") or ""):
    note("ACTIVE-主链路", "llm.openai", f"{o.get('model')} — 主聊天（也驱动 cascade）")

c = raw.get("cascade") or {}
if "dashscope" in str(c.get("base_url") or ""):
    note("ACTIVE-主链路", "cascade", f"{c.get('model')} — 通话大脑（≤90 token）")

a = ((raw.get("agent") or {}).get("llm")) or {}
if "dashscope" in str(a.get("base_url") or ""):
    note("ACTIVE-主链路", "agent.llm", f"{a.get('model')} — 工具/电脑控制")

scan_providers("text_chat.providers", raw.get("text_chat", {}).get("providers") or [],
               str(raw.get("text_chat", {}).get("manual_provider") or ""))

vm = raw.get("vision_models") or {}
scan_providers("vision_models.models", vm.get("models") or [], str(vm.get("active") or ""))

av = raw.get("agent") or {}
if "dashscope" in str(av.get("vision_model") or ""):
    note("ACTIVE-主链路", "agent.vision_model", str(av.get("vision_model")))

co = raw.get("cloud_omni") or {}
if co.get("base_url") or co.get("api_key"):
    note("双工-cloud", "cloud_omni",
         f"prefer_cloud={raw.get('cloud_duplex', {}).get('prefer_cloud')} "
         f"local_fallback={raw.get('cloud_duplex', {}).get('local_fallback')}")

vp = raw.get("voice_profiles") or []
for i, v in enumerate(vp):
    eng = str(v.get("engine") or "")
    if "dashscope" in str(v.get("api_url") or "") or eng.startswith("dashscope"):
        note("备用-云端克隆", f"voice_profiles[{i}] {v.get('name')}",
             f"{v.get('model')}（主用本地 {raw.get('voice', {}).get('clone_local_engine')}）")

print(f"{'性质':16s} {'位置':34s} 说明")
print("-" * 100)
for tag, where, what in rows:
    print(f"{tag:16s} {where:34s} {what}")

bad = [r for r in rows if r[0].startswith("ACTIVE")]
print(f"\n合计 {len(rows)} 处指向百炼；其中 ★正在生效（当前必然失败）{len(bad)} 处：")
for tag, where, what in bad:
    print(f"  ★ {where}: {what}")
sys.exit(1 if bad else 0)
