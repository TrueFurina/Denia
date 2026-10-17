# -*- coding: utf-8 -*-
"""烘焙前置侦察：环境/依赖/现有 bundle/现有 cache/引擎占用。"""
import os, sys, glob, json, hashlib, time

def sz(p):
    try:
        return os.path.getsize(p)
    except Exception:
        return -1

def mt(p):
    try:
        return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(p)))
    except Exception:
        return '-'

print('=' * 70)
print('1) Comni / llama-server 可执行文件')
print('=' * 70)
for p in [r'H:\Comni\_internal\resources\build\bin\Release\llama-server.exe',
          r'H:\Comni\Comni.exe']:
    print('  %-70s exists=%s' % (p, os.path.exists(p)))

print()
print('=' * 70)
print('2) 当前 default_ref_audio bundle（决定 cache 的内容）')
print('=' * 70)
BO = r'H:\Comni\_internal\resources\tools\omni\assets'
print('  assets 目录存在:', os.path.isdir(BO))
if os.path.isdir(BO):
    for root, dirs, files in os.walk(BO):
        for f in sorted(files):
            p = os.path.join(root, f)
            print('    %10.1f KB  %s  %s' % (sz(p) / 1024, mt(p), os.path.join(root.replace(BO, '.'), f)))

print()
print('=' * 70)
print('3) 线上 prompt_cache.gguf（模型目录）')
print('=' * 70)
MD = r'H:\minicpm models\MiniCPM-o-4_5-gguf'
for f in sorted(glob.glob(os.path.join(MD, 'token2wav-gguf', '*'))):
    print('  %12.1f KB  %s  %s' % (sz(f) / 1024, mt(f), os.path.basename(f)))
print('  token2wav-gguf 目录其他:')
for f in sorted(os.listdir(os.path.join(MD, 'token2wav-gguf')))[:20]:
    print('     ', f)

print()
print('=' * 70)
print('4) client_venv 依赖（烘焙需要 torch/s3tokenizer/onnxruntime/librosa）')
print('=' * 70)
SP = r'H:\daniya_voice_tools\client_venv\Lib\site-packages'
print('  site-packages 存在:', os.path.isdir(SP))
if os.path.isdir(SP):
    names = sorted(os.listdir(SP))
    want = ['torch', 'torchaudio', 's3tokenizer', 'onnxruntime', 'librosa', 'soundfile', 'numpy', 'scipy']
    for w in want:
        hit = [n for n in names if n.lower().startswith(w.lower())]
        print('    %-14s -> %s' % (w, hit[:2] if hit else 'MISSING'))
PY = r'H:\daniya_voice_tools\client_venv\Scripts\python.exe'
print('  venv python:', PY, os.path.exists(PY))

print()
print('=' * 70)
print('5) 已生成的原生音频样本（看输出采样率）')
print('=' * 70)
for f in glob.glob(r'G:\ai对话\11\data\native_audio_test\*.wav'):
    with open(f, 'rb') as fh:
        h = fh.read(64)
    import struct
    try:
        ch, sr = struct.unpack('<HI', h[22:28])
        print('    %-40s ch=%d sr=%d' % (os.path.basename(f), ch, sr))
    except Exception as e:
        print('    %-40s ?' % os.path.basename(f))

print()
print('=' * 70)
print('6) 参考音素材（当前线上 vs 48k 候选）')
print('=' * 70)
for f in [r'G:\ai对话\11\assets\voices\daniya_ref.wav',
          r'H:\daniya_voice_tools\wechat_ref.wav',
          r'H:\daniya_voice_tools\daniya_clean_10s.wav',
          r'H:\daniya_voice_tools\daniya_clean_10s_pitchfix.wav',
          r'H:\daniya_voice_tools\daniya_clean_10s_pitchfix5.wav']:
    if os.path.exists(f):
        with open(f, 'rb') as fh:
            h = fh.read(64)
        ch, sr = struct.unpack('<HI', h[22:28])
        import wave
        try:
            w = wave.open(f)
            dur = w.getnframes() / w.getframerate()
            w.close()
        except Exception:
            dur = -1
        print('    %-52s %6dHz %5.2fs %8.1fKB  %s' % (os.path.basename(f), sr, dur, sz(f) / 1024, mt(f)))
