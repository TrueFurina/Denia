import os
import re

LOGS = [
    r'G:\ai对话\11\logs\engine\worker.log',
    r'G:\ai对话\11\logs\engine\worker.log.1',
]

PAT = re.compile(
    r'(Calling omni_init|omni_init success|Token2Wav: using prompt_cache from|'
    r'TTS: weights loaded successfully|Duplex RefAudio|prefilling voice_audio|'
    r'duplex_llm_ref_|Duplex LLM ref_audio|Duplex TTS ref_audio|'
    r'WebSocket /ws/duplex|Duplex WebSocket connected|'
    r'Duplex \u4e2d\u6587|system_content|update_session_config|'
    r'voice_audio_used|custom_prompt)'
)

for lp in LOGS:
    if not os.path.isfile(lp):
        print('missing', lp)
        continue
    print('=' * 100)
    print(lp, os.path.getsize(lp))
    print('=' * 100)
    t = open(lp, 'rb').read().decode('utf-8', errors='replace')
    lines = t.split('\n')

    # 找最后一次 duplex session 开始的序号，往后打印
    idxs = [i for i, l in enumerate(lines)
            if 'Calling omni_init' in l or 'prefilling voice_audio' in l
            or 'Duplex RefAudio]' in l or 'Duplex WebSocket connected' in l
            or 'Duplex LLM ref_audio' in l or 'Duplex TTS ref_audio' in l
            or 'omni_init success' in l]
    print('相关行总数', len(idxs))
    # 打印最后 40 条
    for i in idxs[-40:]:
        s = lines[i].rstrip()
        # 压缩时间戳
        s = re.sub(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ ', r'\1 ', s)
        print('  ', s[:190])
    print()
