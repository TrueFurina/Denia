# -*- coding: utf-8 -*-
"""
通话大脑状态自检 —— 打完一通电话后跑，回答三个问题：
不进闸门：人工诊断工具——读真实通话 trace 按需跑，不是通过/失败式自动闸门
（2026-10-22 普查定性，见 tools/check_gate_registry.py）。
  1. 现在配置是 duplex 还是 cascade？
  2. 这一通里外接 LLM 到底产出过没有（cascade_sent）？
  3. 如果没有，是被哪道闸拦了（cascade_drop 的 reason）？

用法：
    python tools/check_cascade_status.py            # 看最新一通
    python tools/check_cascade_status.py 3          # 看最近 3 通
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config.yaml")

REASON_DESC = {
    "self_speaking": "她自己还在响时被判为回声 → 正常拦截（若占比过高说明防回声窗口太长）",
    "busy": "上一句还没答完就来了新一句 → 一问一答下的正常丢弃",
}


def read_mode() -> str:
    try:
        txt = open(CONFIG, "r", encoding="utf-8", errors="ignore").read()
    except OSError:
        return "(读不到 config.yaml)"
    m = re.search(r"^\s*duplex_call_brain\s*:\s*(\S+)", txt, re.M)
    return m.group(1) if m else "(未配置，等同 duplex)"


def latest_traces(n: int):
    pats = [
        os.path.join(ROOT, "data", "call_trace_*.jsonl"),
        os.path.join(ROOT, "logs", "call_trace_*.jsonl"),
    ]
    files = []
    for p in pats:
        files += glob.glob(p)
    files.sort(key=os.path.getmtime, reverse=True)
    return files[:n]


def main() -> int:
    n = 1
    if len(sys.argv) > 1:
        try:
            n = int(sys.argv[1])
        except ValueError:
            n = 1

    print("=" * 62)
    print("通话大脑状态自检")
    print("=" * 62)
    mode = read_mode()
    print(f"当前开关 duplex_call_brain = {mode}")
    if mode == "cascade":
        print("  → 外接 LLM（百炼 qwen-plus）接管通话文本")
    else:
        print("  → 本地双工引擎自产文本（P0 基线，66 分那版）")
    print()

    files = latest_traces(n)
    if not files:
        print("没找到 call_trace_*.jsonl —— 确认打过电话，且 data/ 目录在当前仓库下。")
        return 1

    for f in files:
        print("-" * 62)
        print(f"文件: {os.path.relpath(f, ROOT)}")
        sent, drops, emits, asrs = [], Counter(), 0, 0
        bad = 0
        try:
            raw = open(f, "r", encoding="utf-8", errors="ignore").read()
        except OSError as e:
            print("  读取失败:", e)
            continue
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                bad += 1
                continue
            # 【2026-09-24】真实字段名是 "ev"（call_trace.py 写入），
            # 曾误写成 type/event，导致全部计数为 0 —— 会给用户假结论。
            t = ev.get("ev") or ev.get("type") or ev.get("event") or ""
            if t == "cascade_sent":
                sent.append(ev)
            elif t == "cascade_drop":
                drops[ev.get("reason", "?")] += 1
            elif t == "emit":
                emits += 1
            elif t == "asr":
                asrs += 1

        print(f"  用户 ASR 落定句数 : {asrs}")
        print(f"  外接 LLM 成句数   : {len(sent)}")
        print(f"  实际发声段数      : {emits}")
        if drops:
            print("  被拦掉的提交      :")
            for k, v in drops.most_common():
                print(f"      {k:<14} x{v}   {REASON_DESC.get(k, '')}")
        if bad:
            print(f"  （{bad} 行无法解析，忽略）")

        # 判定
        print()
        if mode != "cascade":
            print("  判定: 当前是 duplex，以上只是基线的发声统计，外接 LLM 未参与。")
        elif asrs == 0:
            print("  判定: ⚠ 一整通都没有 ASR 落定 —— 麦克风没进 ASR，"
                  "级联永远不会触发。检查 duplex_user_asr 是否为 true。")
        elif not sent and not drops:
            print("  判定: ⚠ ASR 有句但一次都没提交给 LLM —— 大脑没装载上。"
                  "确认改完配置后重启过应用、并重新拨号。")
        elif sent and emits > 0:
            dts = [s.get("dt") for s in sent if isinstance(s.get("dt"), (int, float))]
            if dts:
                print(f"  判定: ✅ 级联生效。首句延迟 {dts[0]:.2f}s，"
                      f"全通均值 {sum(dts)/len(dts):.2f}s")
            else:
                print("  判定: ✅ 级联生效（本通未记录 dt）")
            for s in sent[:6]:
                print(f"      +{s.get('dt', '?'):>5}s  {str(s.get('text', ''))[:52]}")
        elif drops and not sent:
            print("  判定: ⚠ 全部被拦（见上表 reason）—— 级联装上了但一句没放出去。")
        else:
            print("  判定: 数据不完整，再打一通看看。")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
