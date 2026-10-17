# -*- coding: utf-8 -*-
"""通话大脑一键切换（P1 级联开关）。

用法：
    python tools/switch_call_brain.py status    # 看当前是哪种
    python tools/switch_call_brain.py duplex    # 回到本地双工（66 分基线，现状）
    python tools/switch_call_brain.py cascade   # 启用外接 LLM（阿里云百炼 qwen-plus）

改完必须重启通话（client 是构造时装大脑的）。改之前自动备份。
"""
import os, sys, io, re, shutil, time, yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FP = os.path.join(ROOT, 'config.yaml')

def read():
    raw = open(FP, 'r', encoding='utf-8', errors='ignore').read()
    return raw, ('\r\n' in raw)

def current():
    d = yaml.safe_load(open(FP, 'r', encoding='utf-8'))
    return str(d.get('proactive', {}).get('duplex_call_brain', 'duplex')).strip().lower()

def switch(to: str) -> None:
    to = to.strip().lower()
    if to not in ('duplex', 'cascade', 'cloud'):
        print('只能是 duplex / cascade / cloud')
        sys.exit(2)
    if to == 'cloud':
        print('cloud 模式目前是「独立 demo 体验」，不修改主程序配置。')
        print('请直接运行：python tools/cloud_duplex_demo.py')
        print('（主程序内 cloud 接线为后续步骤，暂勿在 app 启用，以免通话异常）')
        return
    cur = current()
    if cur == to:
        print(f'已经是 {to} 了，无需改动')
        return
    raw, crlf = read()
    bak = FP + '._bak_brain_' + time.strftime('%Y%m%d_%H%M%S')
    shutil.copy2(FP, bak)
    txt = raw.replace('\r\n', '\n').replace('\r', '\n')
    pat = re.compile(r'^(\s*)duplex_call_brain:\s*\S+\s*$', re.M)
    hits = pat.findall(txt)
    assert len(hits) == 1, f'duplex_call_brain 出现 {len(hits)} 次，手工检查 config.yaml'
    txt = pat.sub(lambda m: f'{m.group(1)}duplex_call_brain: {to}', txt, count=1)
    out = txt.replace('\n', '\r\n') if crlf else txt
    with io.open(FP, 'w', encoding='utf-8', newline='') as f:
        f.write(out)
    # 真解析校验
    d = yaml.safe_load(out)
    got = str(d['proactive']['duplex_call_brain']).strip().lower()
    assert got == to, f'写入后解析为 {got}'
    print(f'已切换: {cur} -> {got}   （备份 {os.path.basename(bak)}）')
    if to == 'cascade':
        print('  外接 LLM: %s / %s' % (d['cascade']['base_url'], d['cascade']['model']))
        print('  注意：必须重新发起通话才生效（大脑在 client 构造时装载）。')
    else:
        print('  已回到本地双工自产文本 = 改造前的基线状态。')

if __name__ == '__main__':
    cmd = (sys.argv[1] if len(sys.argv) > 1 else 'status').strip().lower()
    if cmd == 'status':
        print('当前通话大脑 =', current())
        print('  duplex  = 本地双工引擎自产文本（改造前基线，66 分）')
        print('  cascade = 外接 LLM（阿里云百炼 qwen-plus）接管文本（P1 级联，未真机验）')
        print('  cloud   = 云端全双工 Qwen-Omni-Realtime（独立 demo 体验，tools/cloud_duplex_demo.py）')
    else:
        switch(cmd)
