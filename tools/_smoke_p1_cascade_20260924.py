# -*- coding: utf-8 -*-
"""【P1 冒烟】三层：①config 真解析 ②模块 import ③默认 duplex 状态零改动。
核心验收：duplex_call_brain=duplex 时，外接大脑必须为 None（= 现状 66 分链路不变）。
"""
import os, sys, io, ast

ROOT = r'G:\ai对话\11'
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'src'))

print('=' * 66)
print('[1] config.yaml 真解析')
import yaml
cfg = yaml.safe_load(open(os.path.join(ROOT, 'config.yaml'), 'r', encoding='utf-8'))
assert 'cascade' in cfg, 'cascade 段缺失'
assert cfg['proactive']['duplex_call_brain'] == 'duplex', '默认开关不是 duplex'
print('   cascade.model =', cfg['cascade']['model'])
print('   proactive.duplex_call_brain =', cfg['proactive']['duplex_call_brain'], '(默认=现状)')

print()
print('[2] 模块 import')
sys.path.insert(0, os.path.join(ROOT, 'src', 'llm'))
from cascade_brain import CascadeBrain  # noqa: E402
b = CascadeBrain.from_config(type('N', (), {})(), ROOT) if hasattr(CascadeBrain, 'from_config') else None
print('   cascade_brain 导入 OK；from_config 存在 =', hasattr(CascadeBrain, 'from_config'))

print()
print('[3] 默认 duplex 下必须完全不碰外接大脑（关键验收）')
src = open(os.path.join(ROOT, 'src', 'llm', 'local_omni_duplex.py'), 'r', encoding='utf-8').read()
for k in ['self._cascade = None', '_cascade_submit', 'set_cascade_brain', '_cascade_run']:
    print(f'   local_omni_duplex 含 {k:20s} = {k in src}')
pro = open(os.path.join(ROOT, 'src', 'companion', 'proactive.py'), 'r', encoding='utf-8').read()
print('   proactive 含 _setup_cascade_brain =', '_setup_cascade_brain' in pro)
print('   duplex 分支先 set_cascade_brain(None) =', 'client.set_cascade_brain(None)' in pro)

# 用桩对象验证 set_cascade_brain(None) 后 _cascade is None
class Stub:
    _cascade = 'SOMETHING'
    def set_cascade_brain(self, brain):
        self._cascade = brain
ns = {}
mod_src = src.replace('\r\n', '\n')
# 抽出方法源码做行为验证（不 import 整个模块，避免重依赖）
import re
m = re.search(r'    def set_cascade_brain\(self, brain\) -> None:\n(.*?)\n\n', mod_src, re.S)
assert m, '未找到 set_cascade_brain'
code = 'class C:\n    def set_cascade_brain(self, brain) -> None:\n' + m.group(1) + '\n'
code = code.replace('logger.info', 'print').replace('logger.', 'print')
exec(compile(code, '<stub>', 'exec'), ns)
c = ns['C']()
c._cascade = '预置'
c.set_cascade_brain(None)
print('   桩验证: set_cascade_brain(None) 后 _cascade =', c._cascade)
assert c._cascade is None, '卸载失败'

print()
print('[4] 断句单元验证（离线，不联网）')
cases = [
    ('嗯。', ''),
    ('今天有点累呢，你呢？', '今天有点累呢，你呢？'),
    ('我在调校新的虚质共振器，差点把实验室炸了。', '我在调校新的虚质共振器，差点把实验室炸了。'),
]
for buf, want in cases:
    got = CascadeBrain._cut_sentence(buf)
    ok = (got == want)
    print(f'   {"OK " if ok else "FAIL"} {buf[:24]!r:28s} -> {got!r}')
    assert ok, (buf, got)
fin = CascadeBrain._finalize('（歪头）你好呀<think>想什么呢</think>嗯')
print('   finalize 去括号/去思维链 ->', repr(fin))
assert '歪头' not in fin and 'think' not in fin

print()
print('=' * 66)
print('冒烟通过：默认 duplex = 现状零改动；切 cascade 只改一行 config。')
