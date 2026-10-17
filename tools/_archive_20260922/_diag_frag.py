# -*- coding: utf-8 -*-
"""看引擎把文本切成了多少片喂给 TTS —— 这是"每片独立收尾→结巴"的源头。"""
import os
import re
import glob

D = r"G:\ai对话\11\data\native_audio_test\final_sample"

PAT_TEXT = re.compile(r"LLM->TTS: text='([^']*)', n_tokens=(\d+)")
PAT_BOS = re.compile(r"在 prefill 前添加 audio_bos \(chunk_idx=(\d+), new_size=(\d+)\)")
PAT_EOS = re.compile(r"is_end_of_turn=(\w+), 添加 text_eos_embed \(chunk_idx=(\d+), new_size=(\d+)\)")


def main():
    for lp in sorted(glob.glob(os.path.join(D, "worker_*.log"))):
        t = open(lp, encoding="utf-8", errors="replace").read()
        lines = t.splitlines()
        texts = [(m.group(1), int(m.group(2)), l[11:19]) for l in lines for m in [PAT_TEXT.search(l)] if m]
        bos = [m.group(1) for l in lines for m in [PAT_BOS.search(l)] if m]
        eos = [m.group(1) for l in lines for m in [PAT_EOS.search(l)] if m]
        if not texts and not bos:
            continue
        joined = "".join(x[0] for x in texts)
        print("=" * 106)
        print("%s" % os.path.basename(lp))
        print("  LLM->TTS 文本片数 = %d   audio_bos(chunk0) = %d   text_eos = %d"
              % (len(texts), sum(1 for c in bos if c == "0"), len(eos)))
        print("  各片 token 数: %s" % [x[1] for x in texts][:60])
        print("  各片文本（前 40 片）:")
        for i, (tx, n, ts) in enumerate(texts[:40]):
            print("     %2d) [%s] tok=%-3d %r" % (i, ts, n, tx))
        if len(texts) > 40:
            print("     ...(略 %d 片)" % (len(texts) - 40))
        print("  ≈拼接全文: %s" % joined[:400])
        print()


if __name__ == "__main__":
    main()
