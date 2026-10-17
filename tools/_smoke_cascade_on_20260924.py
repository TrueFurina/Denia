# -*- coding: utf-8 -*-
"""【P1 冒烟·开启态】把开关切到 cascade，验证大脑真能装上并真能说话（不是静默降级）。"""
import os, sys, io, time, yaml, asyncio, importlib.util
from types import SimpleNamespace

ROOT = r'G:\ai对话\11'
sys.path.insert(0, os.path.join(ROOT, 'src', 'llm'))

def to_ns(d):
    if isinstance(d, dict):
        return SimpleNamespace(**{k: to_ns(v) for k, v in d.items()})
    if isinstance(d, list):
        return [to_ns(x) for x in d]
    return d

cfg = to_ns(yaml.safe_load(open(os.path.join(ROOT, 'config.yaml'), 'r', encoding='utf-8')))
print('config: cascade.model =', cfg.cascade.model,
      '| duplex_call_brain =', cfg.proactive.duplex_call_brain)

from cascade_brain import CascadeBrain  # noqa: E402
print('CascadeBrain.from_config 是类方法 =', hasattr(CascadeBrain, 'from_config'))

brain = CascadeBrain.from_config(cfg, ROOT)
print('装载结果: enabled =', brain.enabled, '| model =', brain.model,
      '| system_prompt', len(brain.system_prompt), '字')
assert brain.enabled, '大脑未启用（Key/端点/模型缺失）→ 会静默降级，必须修'

# 真跑一轮，确认链路通
async def main():
    got = []
    async def on_sentence(s):
        got.append(s)
    t0 = time.time()
    full = await brain.stream_reply('我刚下班，累死了。', on_sentence=on_sentence)
    print(f'实跑 {time.time()-t0:.2f}s: {full}')
    print('成句回调', len(got), '次:', got)
    assert full, '外接 LLM 没有产出'
    return full

full = asyncio.run(main())
print()
print('开启态冒烟通过：切 cascade 后大脑可用，不会静默降级。')
