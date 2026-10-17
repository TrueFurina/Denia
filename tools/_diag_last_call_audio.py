# -*- coding: utf-8 -*-
"""诊断：① 引擎进程启动时刻（决定加载哪个 prompt_cache）② 现有通话追踪记了什么。

关键判据：声码器在 omni_init 时读入 prompt_cache.gguf。
若 llama-server.exe 的启动时刻 **早于** 达妮娅 cache 换回的时刻（09-22 22:30），
则内存里加载的仍是错版 ⇒ 听到的就是 MiniCPM 原生嗓。
"""
import os
import sys
import glob
import json
import subprocess
import datetime

CACHE_DIR = r"H:\minicpm models\MiniCPM-o-4_5-gguf\token2wav-gguf"
LIVE_CACHE = os.path.join(CACHE_DIR, "prompt_cache.gguf")
SWAP_TS = datetime.datetime(2026, 9, 22, 22, 30)  # 达妮娅 cache 换回 live 的时刻


def fmt(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M:%S")


def proc_start_time(name):
    """用 PowerShell 取进程启动时刻（比 wmic 稳）。"""
    ps = (
        "Get-Process -Name '%s' -ErrorAction SilentlyContinue | "
        "Select-Object Id,StartTime | ConvertTo-Json" % name
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=40,
                             encoding="utf-8", errors="replace")
        txt = out.stdout.strip()
        if not txt:
            return []
        data = json.loads(txt)
        if isinstance(data, dict):
            data = [data]
        res = []
        for d in data:
            st = d.get("StartTime")
            # /Date(1769...)/ 形式
            if isinstance(st, str) and st.startswith("/Date("):
                ms = int(st[6:st.find(")")].split("+")[0].split("-")[0])
                res.append((d.get("Id"), datetime.datetime.fromtimestamp(ms / 1000.0)))
            elif isinstance(st, str):
                res.append((d.get("Id"), datetime.datetime.fromisoformat(st)))
        return res
    except Exception as e:
        print("  (取进程启动时刻失败: %r)" % e)
        return []


def main():
    print("=" * 78)
    print("① 引擎进程启动时刻 vs 达妮娅 cache 换回时刻")
    print("=" * 78)
    print("  达妮娅 cache 换回 live = %s" % SWAP_TS.strftime("%m-%d %H:%M"))
    print("  live cache 文件 mtime  = %s  (md5 见上次核查: f3c56d7099ed=达妮娅)"
          % fmt(os.path.getmtime(LIVE_CACHE)))
    print()
    for nm in ("llama-server", "Comni"):
        procs = proc_start_time(nm)
        if not procs:
            print("  %-14s : 未运行" % nm)
            continue
        for pid, st in procs:
            older = "❌ 早于换回 ⇒ 内存里是【错版/原生嗓】" if st < SWAP_TS else "✅ 晚于换回 ⇒ 应为达妮娅"
            print("  %-14s pid=%-8s 启动于 %s   %s" % (nm, pid, st.strftime("%m-%d %H:%M:%S"), older))

    print()
    print("=" * 78)
    print("② 现有通话追踪记了什么（data/call_trace_*.jsonl）")
    print("=" * 78)
    fs = sorted(glob.glob(os.path.join("data", "call_trace_*.jsonl")),
                key=os.path.getmtime)
    if not fs:
        print("  没有追踪文件")
        return
    print("  共 %d 个，最近 3 个：" % len(fs))
    for p in fs[-3:]:
        print("    %-46s %s  %.1f KB" % (os.path.basename(p), fmt(os.path.getmtime(p)),
                                         os.path.getsize(p) / 1024))
    latest = fs[-1]
    print("\n  最新文件 %s 的事件统计：" % os.path.basename(latest))
    rows = []
    for line in open(latest, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    kinds = {}
    for r in rows:
        kinds[r.get("ev")] = kinds.get(r.get("ev"), 0) + 1
    for k, v in sorted(kinds.items(), key=lambda x: -x[1]):
        print("    %-12s %d" % (k, v))

    print("\n  最近 10 条 emit（她开口的事件）：")
    em = [r for r in rows if r.get("ev") == "emit"][-10:]
    if not em:
        print("    （无 emit）")
    for r in em:
        print("    chars=%-4s pcm_ms=%-7s text=%r"
              % (r.get("chars"), r.get("pcm_ms"), (r.get("text") or "")[:36]))

    print("\n  ⚠️ 结论：追踪里有 文字 + 音频时长(pcm_ms)，但【没有音频本体】。")
    print("      ⇒ 无法事后用 campplus 量「这段到底是不是达妮娅」，只能靠耳朵。")
    print("       ⇒ 这正是要补的：把每次 emit 的 pcm 落成 wav。")


if __name__ == "__main__":
    main()
