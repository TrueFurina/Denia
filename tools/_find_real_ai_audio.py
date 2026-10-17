# -*- coding: utf-8 -*-
"""在真实通话会话里找"AI 音频"轨道，用于判断结巴是引擎固有还是测试台伪影。"""
import os
import time

R = r"H:\Comni\_internal\resources\apps\server\data\sessions"


def main():
    ds = sorted(os.listdir(R))
    with_ai = []
    with_replay = []
    for d in ds:
        p = os.path.join(R, d)
        if not os.path.isdir(p):
            continue
        ai = os.path.join(p, "ai_audio")
        n_ai = len(os.listdir(ai)) if os.path.isdir(ai) else 0
        tot_ai = 0
        if n_ai:
            for f in os.listdir(ai):
                try:
                    tot_ai += os.path.getsize(os.path.join(ai, f))
                except OSError:
                    pass
        rep = os.path.join(p, "merged_replay.wav")
        rs = os.path.getsize(rep) if os.path.exists(rep) else 0
        if n_ai:
            with_ai.append((n_ai, tot_ai, d, os.path.getmtime(p)))
        if rs > 500000:
            with_replay.append((rs, d, os.path.getmtime(p), n_ai, tot_ai))

    print("=" * 108)
    print("有 ai_audio 的会话: %d 个" % len(with_ai))
    for n_ai, tot, d, mt in sorted(with_ai, key=lambda x: -x[1])[:20]:
        print("   %-46s ai_audio=%4d 个 / %7.2fMB   %s" % (d, n_ai, tot / 1048576,
                                                           time.strftime("%m-%d %H:%M", time.localtime(mt))))
    print()
    print("有 merged_replay.wav(>0.5MB) 的会话: %d 个" % len(with_replay))
    for rs, d, mt, n_ai, tot in sorted(with_replay, key=lambda x: -x[0])[:20]:
        print("   %-46s replay=%7.2fMB  ai_audio=%4d/%6.2fMB  %s"
              % (d, rs / 1048576, n_ai, tot / 1048576, time.strftime("%m-%d %H:%M", time.localtime(mt))))
    print()
    # 最近 10 个含 ai_audio 的会话
    print("最近 10 个含 ai_audio 的会话:")
    for n_ai, tot, d, mt in sorted(with_ai, key=lambda x: -x[3])[:10]:
        print("   %-46s ai_audio=%4d / %6.2fMB  %s" % (d, n_ai, tot / 1048576,
                                                       time.strftime("%m-%d %H:%M", time.localtime(mt))))


if __name__ == "__main__":
    main()
