# -*- coding: utf-8 -*-
"""用确证基准校准：把 chat_audio 每个片段与'纯云端'/'纯本地双工'对比，判定是否混入了本地双工漏出。
指标：HI = (8k-12k + 12k+) / (4k-8k + 8k-12k + 12k+)  本地双工≈0（8k+≈0），云端>0。
"""
import os, glob
import numpy as np, soundfile as sf

def analyze(path):
    x, sr = sf.read(path, dtype="float32")
    if x.ndim > 1: x = x[:,0]
    n=len(x); dur=n/sr
    peak=float(np.max(np.abs(x))) if n else 0.0
    win=np.hanning(2048); hop=1024
    bands=[(0,1000),(1000,2000),(2000,4000),(4000,8000),(8000,12000),(12000,min(sr//2,20000))]
    acc=np.zeros(len(bands)); cnt=0
    for s in range(0,max(1,n-len(win)),hop):
        seg=x[s:s+len(win)]*win
        if np.max(np.abs(seg))<1e-4: continue
        sp=np.abs(np.fft.rfft(seg)); fr=np.fft.rfftfreq(len(win),1.0/sr); cnt+=1
        for i,(lo,hi) in enumerate(bands):
            mask=(fr>=lo)&(fr<hi); acc[i]+=np.sum(sp[mask]**2)
    total=acc.sum()
    frac=acc/total if total>0 else acc
    e4_8,e8_12,e12=frac[3],frac[4],frac[5]
    hi = (e8_12+e12)/(e4_8+e8_12+e12) if (e4_8+e8_12+e12)>0 else 0.0
    return sr,dur,peak,frac,hi

def show(label,path):
    try:
        sr,dur,peak,frac,hi=analyze(path)
    except Exception as e:
        print("  [跳过]%s -> %s"%(label,e)); return None
    print("  %-40s dur=%.2fs sr=%d peak=%.2f HI=%.3f  占比[0-1k,1-2k,2-4k,4-8k,8-12k,12k+]=%s"
          % (label[:40],dur,sr,peak,hi," ".join("%.1f"%(f*100) for f in frac)))
    return hi

print("== 基准A：纯本地双工原生（duplex_out 子目录每段的 HI）==")
dsubs=sorted(glob.glob("G:/ai对话/11/data/duplex_out/*/"),key=os.path.getmtime)[-1]
fs=sorted(glob.glob(os.path.join(dsubs,"*.wav")))
his=[]
for p in fs:
    r=show(os.path.basename(p),p); his.append(r)
if his: print("  -> 本地双工 HI min=%.3f med=%.3f max=%.3f"%(min(his),sorted(his)[len(his)//2],max(his)))

print("\n== 基准B：确证纯云端（之前合成的样本）==")
for lbl,p in [
    ("云端百炼克隆_同文本","G:/ai对话/11/data/云端vs本地_同文本_20260923/云端百炼克隆_同文本.wav"),
    ("指定_qwen-audio-3.1-tts-flash","G:/ai对话/11/data/TTS模型实测_20260923/指定_qwen-audio-3.1-tts-flash.wav"),
    ("达妮娅真源(daniya_ref_0813_24k)","G:/ai对话/11/assets/voices/daniya_ref_0813_24k.wav"),
]:
    show(lbl,p)

print("\n== 待测：chat_audio（云端 TTS 实际出声，最近 8 个）==")
ca=sorted(glob.glob("G:/ai对话/11/src/ui/web/assets/chat_audio/*.wav"),key=os.path.getmtime)[-8:]
for p in ca: show(os.path.basename(p),p)
