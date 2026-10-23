# -*- coding: utf-8 -*-
"""检验「她说了多少字，就放出多少声音」——抓「说一半没了」。
不进闸门：人工诊断——读真实通话 trace 按需跑，不是通过/失败式自动闸门
（2026-10-22 普查定性，见 tools/check_gate_registry.py）。

背景（2026-09-22 实测）：
  `config.yaml` 里 `duplex_call_max_new_speak_tokens_per_chunk` 限制引擎**每个发言块**
  最多生成多少音频 token。音频语速 ≈ **25.4 token/秒**，所以：
      上限 24  ⇒ 每块最多 ≈0.94 秒声音
      上限 48  ⇒ 每块最多 ≈1.9 秒声音
  上限偏小时，**长句的文字生成完了、声音还在半路就被 `is_listen` 收尾** ⇒
  UI 上整句文字都在，但耳朵里只有前半段 —— 用户称之为「说一半没了」。

判据（中文口播实测语速 ≈ **0.20 秒/字**，见 `_barge_est_per_char_s=0.22`）：
  应有时长 ≈ 字数 × 0.20s；**实际 pcm_ms / 应有 < 70% 即视为「说不完」**。

用法：
    python tools/check_turn_audio_len.py                # 自动取最新 trace
    python tools/check_turn_audio_len.py <trace.jsonl>
"""
import os
import sys
import glob
import json


def latest_trace() -> str:
    fs = sorted(glob.glob(os.path.join('data', 'call_trace_*.jsonl')),
                key=os.path.getmtime)
    return fs[-1] if fs else ''


def main(path: str) -> int:
    if not path or not os.path.isfile(path):
        print('❌ 找不到通话追踪文件：%r' % path)
        return 2
    R = [json.loads(l) for l in open(path, encoding='utf-8') if l.strip()]
    em = [r for r in R if r.get('ev') == 'emit']
    if not em:
        print('⚠️ 该文件里没有 emit 事件（可能不是通话，或通话太短）')
        return 2

    SEC_PER_CHAR = 0.20
    print('追踪文件：%s（emit %d 条）' % (os.path.basename(path), len(em)))
    print('=' * 96)
    print('%-3s %-46s %5s %8s %8s %7s' % ('#', '她说的文字', '字数', '应有', '实际', '覆盖率'))
    print('-' * 96)
    bad = []
    for i, r in enumerate(em, 1):
        txt = (r.get('text') or '').replace('\n', ' ')
        n = r.get('chars') or len(txt)
        ms = r.get('pcm_ms') or 0
        if n <= 0:
            continue
        need = n * SEC_PER_CHAR
        act = ms / 1000.0
        cov = act / need if need > 0 else 0
        flag = ''
        if cov < 0.70:
            flag = '  ✗ 说不完'
            bad.append((i, txt, n, need, act, cov))
        print('%-3d %-46s %5d %7.1fs %7.1fs %6.0f%%%s'
              % (i, txt[:44], n, need, act, cov * 100, flag))

    print('-' * 96)
    tot_need = sum((r.get('chars') or 0) * SEC_PER_CHAR for r in em if (r.get('chars') or 0) > 0)
    tot_act = sum((r.get('pcm_ms') or 0) / 1000.0 for r in em)
    ratio = tot_act / tot_need if tot_need > 0 else 0
    print('整体：应有 %.1fs / 实际 %.1fs ⇒ 覆盖率 **%.0f%%**' % (tot_need, tot_act, ratio * 100))
    print('「说不完」(<70%%) 的句子：%d / %d' % (len(bad), len(em)))
    print()
    if len(bad) >= 3 or ratio < 0.75:
        print('❌ **判定：存在「说一半」问题**')
        print('   最可能的原因：`duplex_call_max_new_speak_tokens_per_chunk` 太小')
        print('   （音频语速 ≈25.4 token/秒 ⇒ 上限 24 只能产 ~0.94 秒声音/块）')
        print('   ⇒ 调到 48 或更大再测。也可能与 `duplex_call_listen_prob_scale`')
        print('     偏高（过早转 LISTEN 收尾）叠加。')
        return 1
    print('✅ **判定：她说的和放出来的基本对得上**（覆盖率 %.0f%%）' % (ratio * 100))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else latest_trace()))
