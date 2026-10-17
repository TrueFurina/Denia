# -*- coding: utf-8 -*-
"""扫描用户实例的引擎日志，提取音色相关事实。

目标（回答"用户实际听到的音色由谁决定"）：
  1) Token2Wav 是否 using prompt_cache / 是否 fallback 到 prompt_bundle
  2) [Duplex RefAudio] LLM=? TTS=?  —— 实际用的参考音路径
  3) voice_audio prefilled / voice_audio_used  —— LLM 层克隆是否发生
  4) set_ref_audio / RefAudio using custom from system_content
"""
import os
import re
import sys

LOGS = [
    r"G:\ai对话\11\logs\engine\worker.log",
    r"G:\ai对话\11\logs\engine\gateway.log",
]

PATTERNS = [
    (r"Token2Wav:\s*(using prompt_cache|prompt_cache failed|all model files|initialized successfully|found models|使用)",
     "T2W 加载"),
    (r"\[Duplex RefAudio\]", "双工参考音指派"),
    (r"\[Duplex RefAudio/lang\]", "语言默认参考音"),
    (r"voice_audio", "LLM层音频克隆"),
    (r"set_ref_audio", "运行时设置参考音"),
    (r"\[RefAudio\]", "从 system_content 取参考音"),
    (r"prompt_bundle|default_ref_audio", "bundle/默认音"),
    (r"使用 C\+\+ 实现|Python T2W", "T2W 实现选择"),
]

MAX_PER_CAT = 14


def main():
    tail_mb = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    for lp in LOGS:
        if not os.path.exists(lp):
            print("MISS %s" % lp)
            continue
        size = os.path.getsize(lp)
        print("=" * 104)
        print("%s   (%.1f MB)" % (lp, size / 1048576))
        with open(lp, "rb") as f:
            if size > tail_mb * 1048576:
                f.seek(size - tail_mb * 1048576)
                f.readline()
            data = f.read().decode("utf-8", "replace")
        lines = data.splitlines()
        print("  读取 %d 行（尾部 %d MB）" % (len(lines), tail_mb))
        for pat, cat in PATTERNS:
            rx = re.compile(pat, re.I)
            hits = []
            for l in lines:
                if rx.search(l):
                    s = l.strip()
                    if s not in hits:
                        hits.append(s)
            if not hits:
                continue
            print()
            print("  --- [%s] 命中 %d 条唯一，展示最近 %d 条" % (cat, len(hits), MAX_PER_CAT))
            for s in hits[-MAX_PER_CAT:]:
                print("      %s" % s[:230])


if __name__ == "__main__":
    main()
