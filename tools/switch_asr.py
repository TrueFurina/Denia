# -*- coding: utf-8 -*-
"""用户侧 ASR 模型一键切换（治「复读」）。

用法：
    python tools/switch_asr.py status     # 看当前是哪种
    python tools/switch_asr.py zipformer  # 回到 2023 旧款 transducer（会打结/复读，但稳）
    python tools/switch_asr.py paraformer # 启用阿里 DAMO Paraformer 流式（非自回归，不复读）

背景：旧 zipformer-transducer 自回归复读（47 句 44.7% 打结）；Paraformer 离线 A/B 胜，
但真机曾因 sherpa 内置 endpoint 过早触发而碎片化（"你的名""叫什"）。2026-09-24 已改为
自管静音分段（sherpa_recognizer._feed_paraformer），翻回 paraformer 前应真机验证。

改完必须重启通话（ASR 在 client 构造时装）。改之前自动备份。
"""
import os, sys, io, re, shutil, time, yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FP = os.path.join(ROOT, 'config.yaml')


def read():
    raw = open(FP, 'r', encoding='utf-8', errors='ignore').read()
    return raw, ('\r\n' in raw)


def current():
    d = yaml.safe_load(open(FP, 'r', encoding='utf-8'))
    return str(d.get('proactive', {}).get('duplex_user_asr_model', 'paraformer')).strip().lower()


def switch(to: str) -> None:
    to = to.strip().lower()
    if to not in ('zipformer', 'paraformer'):
        print('只能是 zipformer 或 paraformer')
        sys.exit(2)
    cur = current()
    if cur == to:
        print(f'已经是 {to} 了，无需改动')
        return
    raw, crlf = read()
    bak = FP + '._bak_asr_' + time.strftime('%Y%m%d_%H%M%S')
    shutil.copy2(FP, bak)
    txt = raw.replace('\r\n', '\n').replace('\r', '\n')
    pat = re.compile(r'^(\s*)duplex_user_asr_model:\s*\S+\s*$', re.M)
    hits = pat.findall(txt)
    assert len(hits) == 1, f'duplex_user_asr_model 出现 {len(hits)} 次，手工检查 config.yaml'
    txt = pat.sub(lambda m: f'{m.group(1)}duplex_user_asr_model: {to}', txt, count=1)
    out = txt.replace('\n', '\r\n') if crlf else txt
    with io.open(FP, 'w', encoding='utf-8', newline='') as f:
        f.write(out)
    d = yaml.safe_load(out)
    got = str(d['proactive']['duplex_user_asr_model']).strip().lower()
    assert got == to, f'写入后解析为 {got}'
    print(f'已切换: {cur} -> {got}   （备份 {os.path.basename(bak)}）')
    if to == 'paraformer':
        print('  Paraformer 流式（非自回归，不复读）。务必真机验证一句完整吐出，')
        print('  若出现 2~3 字碎片，立刻切回 zipformer：python tools/switch_asr.py zipformer')
    else:
        print('  已回到 2023 zipformer-transducer（会打结/复读，但输出完整、不碎）。')


if __name__ == '__main__':
    cmd = (sys.argv[1] if len(sys.argv) > 1 else 'status').strip().lower()
    if cmd == 'status':
        print('当前用户侧 ASR =', current())
        print('  zipformer  = 2023 旧款 transducer（会打结/复读，但输出完整）')
        print('  paraformer = 阿里 DAMO 流式（非自回归，不复读；2026-09-24 修碎片化）')
    else:
        switch(cmd)
