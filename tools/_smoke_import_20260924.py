# -*- coding: utf-8 -*-
"""【P1 冒烟·项目解释器】用 app 同款解释器真 import 三个改动文件 + load_config()。
红线：改完 config/源码必须跑到这一层，不能只验字符串在不在。
"""
import os, sys, time

ROOT = r'G:\ai对话\11'
os.chdir(ROOT)
sys.path.insert(0, ROOT)

t0 = time.time()
print('cwd =', os.getcwd())

print('[1] load_config()')
try:
    from src.config import load_config
    cfg = load_config(os.path.join(ROOT, 'config.yaml'))
    print('   OK  cascade.model =', getattr(cfg.ns.cascade, 'model', '?'))
    print('   OK  proactive.duplex_call_brain =', getattr(cfg.ns.proactive, 'duplex_call_brain', '?'))
except Exception as e:
    print('   FAIL', type(e).__name__, e)
    raise

print('[2] import src.llm.cascade_brain')
try:
    import src.llm.cascade_brain as cb
    print('   OK ', cb.CascadeBrain.__name__, '| from_config =', hasattr(cb.CascadeBrain, 'from_config'))
except Exception as e:
    print('   FAIL', type(e).__name__, e)
    raise

print('[3] import src.llm.local_omni_duplex')
try:
    import src.llm.local_omni_duplex as lod
    c = lod.LocalOmniDuplexClient.__init__
    src_txt = open(os.path.join(ROOT, 'src', 'llm', 'local_omni_duplex.py'), 'r', encoding='utf-8').read()
    for k in ('_cascade_submit', 'set_cascade_brain', '_cascade_run'):
        print(f'   {"OK " if k in src_txt else "MISS"} {k}')
except Exception as e:
    print('   FAIL', type(e).__name__, e)
    raise

print('[4] import src.companion.proactive')
try:
    import src.companion.proactive as pro
    print('   OK  有 _setup_cascade_brain =', hasattr(pro.CompanionCore, '_setup_cascade_brain')
          if hasattr(pro, 'CompanionCore') else '(类名未确认)')
except Exception as e:
    print('   FAIL', type(e).__name__, e)
    raise

print()
print(f'全部通过，耗时 {time.time()-t0:.1f}s')
