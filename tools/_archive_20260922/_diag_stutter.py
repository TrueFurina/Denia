# -*- coding: utf-8 -*-
"""诊断「结巴/卡顿」到底出自哪一环。

要回答的问题：
  Q1 [WAV nowait] 的 round_dir 会不会换？换轮后 wav 编号是否重新从 wav_0 开始？
  Q2 _sent_wav_files 跨轮是否复位？若不复位 → 新轮的 wav_0 被当"已发"跳过 = 每轮开头吞一段。
  Q3 每次 audio_only 实际下发多少样本？与最终 wav 时长是否对得上（有没有丢）？
  Q4 文本本身是否复读（模型侧结巴）？
"""
import os
import re
import glob
import json
import collections

D = r"G:\ai对话\11\data\native_audio_test\final_sample"


def parse_nowait(line):
    m = re.search(r"\[WAV nowait\] dir=(.+?), all_files=\[([^\]]*)\], sent=(\d+)", line)
    if not m:
        return None
    d, fl, sent = m.group(1), m.group(2), int(m.group(3))
    wavs = re.findall(r"'(wav_\d+\.wav)'", fl)
    bins = re.findall(r"'(audio_tokens_chunk_\d+\.bin)'", fl)
    return d, wavs, bins, sent


def main():
    for p in sorted(glob.glob(os.path.join(D, "worker_*.log"))):
        t = open(p, encoding="utf-8", errors="replace").read()
        lines = t.splitlines()
        rows = []
        for l in lines:
            r = parse_nowait(l)
            if r:
                ts = l[:23]
                rows.append((ts, r[0], tuple(r[1]), len(r[2]), r[3]))
        print("=" * 104)
        print("%s   总行 %d   [WAV nowait] %d" % (os.path.basename(p), len(lines), len(rows)))

        # 轮次切换（round_dir 变化）
        rounds = []
        for ts, d, wavs, nb, sent in rows:
            if not rounds or rounds[-1][0] != d:
                rounds.append([d, ts, ts, set(), 0])
            rounds[-1][2] = ts
            rounds[-1][3].update(wavs)
            rounds[-1][4] = max(rounds[-1][4], sent)
        print("  轮次数 = %d" % len(rounds))
        for i, (d, t0, t1, wavset, mx) in enumerate(rounds):
            nums = sorted(int(re.search(r"(\d+)", w).group(1)) for w in wavset)
            tag = d.replace(r"H:\Comni\_internal\resources\tools\omni", "").replace("/", "\\")
            print("    [轮%d] %s → %s   dir=%s" % (i, t0[11:], t1[11:], tag))
            print("          wav 编号: %s   最大 sent=%d" % (nums, mx))

        # sent 是否单调递增（若跨轮不复位 → 新轮 wav_0 会被跳过）
        sents = [r[4] for r in rows]
        print("  sent 序列: 起点=%s 终点=%s 是否单调不减=%s" % (sents[0], sents[-1], all(b >= a for a, b in zip(sents, sents[1:]))))
        # 同一 dir 内 sent 是否出现回退（回退=换了 dir 却复用了计数）
        drops = [(i, sents[i - 1], sents[i]) for i in range(1, len(sents)) if sents[i] < sents[i - 1]]
        print("  sent 回退次数 = %d  %s" % (len(drops), drops[:6]))

        # audio_only 下发量
        ao = [l for l in lines if "sent audio_only" in l]
        sizes = [int(m.group(1)) for l in ao for m in [re.search(r"\((\d+) chars\)", l)] if m]
        print("  audio_only 条数=%d  每条 base64 字符数分布=%s" % (len(sizes), collections.Counter(sizes).most_common(6)))
        print("  ⇒ 估算总样本 = %.2fs @24k" % (sum(sizes) / 4 * 3 / 4 / 24000))

        # 文本复读检查
        texts = []
        for l in lines:
            m = re.search(r"\[SPEAK\s*\d+\]\s*(.+)$", l)
            if m:
                texts.append(m.group(1).strip())
        print("  SPEAK 文本 %d 条" % len(texts))
        dup = 0
        for i, tx in enumerate(texts[:14]):
            flag = ""
            if i and tx == texts[i - 1]:
                flag = "  <<< 与前一条完全相同"
                dup += 1
            print("     %2d) %s%s" % (i, tx[:110], flag))
        print("  完全重复条数=%d" % dup)
        for w in ["我我", "你你", "的的", "是是", "不不", "了了", "在在"]:
            c = sum(t.count(w) for t in texts)
            if c:
                print("  叠字 %r 出现 %d 次" % (w, c))
        print()


if __name__ == "__main__":
    main()
