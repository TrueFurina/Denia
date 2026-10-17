# -*- coding: utf-8 -*-
"""收尾：① 校验 MEMORY.md / 技能 结构完好 ② 把本轮一次性诊断脚本归档到 tools/_archive_20260922/"""
import io, os, shutil, time

print('=== 1) 记忆与技能校验 ===')
P = r'G:\ai对话\.workbuddy\memory\MEMORY.md'
b = io.open(P, 'rb').read()
c = b.decode('utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')
print('  MEMORY.md %d B  CRLF=%d  行数=%d' % (len(b), b.count(b'\r\n'), c.count('\n') + 1))
for k in ['## 0. 心法', '## 0b. 通话全链路追踪器', '## 1. 改代码红线', '### 1b. 环境',
          '## 2. 问题族谱', '### A 组：已修', '### B 组：进行中 / 决策', '## 3. 未修项',
          '2as（修正版）', '§2ap 录音', '§2aq ', '2at ', '2aj–2ao']:
    assert k in c, 'MEMORY 缺: %r' % k
print('  章节齐全 ✓  含本轮 §2at ✓  含 §2aj–2ao 压缩块 ✓')

S = r'C:\Users\Administrator\.workbuddy\skills\comni-voice-clone-audit\SKILL.md'
sb = io.open(S, 'rb').read()
sc = sb.decode('utf-8-sig')
assert sc.startswith('---\n'), 'frontmatter 丢了'
assert 'name:' in sc[:400] and 'description:' in sc[:400]
assert '## ⚠️ 双工「模型加载失败' in sc
assert 'C++ 引擎版本 << 模型版本' not in sc
print('  技能 %d B  frontmatter OK  旧错误判据已清除 ✓  章节数(## )=%d' % (len(sb), sc.count('\n## ')))

print()
print('=== 2) 归档本轮一次性脚本 ===')
TD = r'G:\ai对话\11\tools'
DST = os.path.join(TD, '_archive_20260922')
os.makedirs(DST, exist_ok=True)

KEEP = {'check_tts_audio.py', 'verify_gguf.py', 'probe_duplex_refaudio.py'}
PREFIX = ('_diag', '_verify_fp16', '_verify_reftext', '_verify_align', '_verify_em',
          '_regen_after_rollback', '_add_skill', '_write_mem', '_fix_emoji',
          '_append_mem', '_rebuild_mem', '_diag_tokens', '_fix_skill_versions',
          '_append_verfix_mem', '_slim_memory', '_exa')

moved, kept = [], []
for n in sorted(os.listdir(TD)):
    p = os.path.join(TD, n)
    if not os.path.isfile(p) or not n.endswith('.py'):
        continue
    if n in KEEP:
        kept.append(n); continue
    if n.startswith(PREFIX):
        tgt = os.path.join(DST, n)
        if os.path.exists(tgt):
            tgt = tgt + '.' + time.strftime('%H%M%S')
        shutil.move(p, tgt)
        moved.append(n)

print('  归档 %d 个 -> tools/_archive_20260922/' % len(moved))
for n in moved:
    print('     ', n)
print('  保留（可复用）:', ', '.join(kept))
