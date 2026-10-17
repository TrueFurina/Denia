# -*- coding: utf-8 -*-
"""按角色自动标定「高频补偿曲线」。

做什么
------
对**某一个角色**，用它的真源音频与该角色在引擎里的**真实产出**，算出一条固定的
零相位 EQ 增益曲线，存到 `assets/voice_eq/<key>.json`，供 `src/voice/hf_eq.py`
在输出侧做补偿。

为什么必须每角色各算一条
------------------------
`g = 20log10(LTAS_真源 / LTAS_产出)` 里的「产出」随角色变（跨音色 >8k 电平差 4.7dB）。
把 A 角色的曲线套给 B ⇒ 朝一个没有证据的目标推 10dB ⇒ 灌先验嘶声。
**本工具只写自己那条曲线，不做任何跨角色推断。**

关键的工程纪律（踩过坑）
------------------------
1. **产出样本必须够长/够多**：单个 2–3 秒短句的 LTAS 不稳，算出的曲线可能**反向**
   （实测：2.28s 样本算出的曲线会把 f95 从 3734 压到 1153）。⇒ 建议累计 ≥8 秒、
   最好 ≥3 句；`--min-total-s` 可卡下限。
2. **产出必须是「原生双工 PCM」**，不能混入外挂 TTS 的产出（两者亮度差 2 倍以上）。
   最可靠的挑法 = 用通话追踪里的 `emit.pcm_ms` 去匹配存档 wav 的时长（`--match-trace`）。
3. **写完必验收**：把「补偿前 / 补偿后 / 真源」三者的频段剖面与谱形距离都打出来；
   若补偿后**离真源更远**，拒绝写入并报错（不生成坏曲线）。

用法
----
    # 1) 从通话追踪自动挑原生产出（推荐）
    python tools/voice_eq_calib.py --key daniya \\
        --ref assets/voices/daniya_ref.wav \\
        --match-trace "data/call_trace_*.jsonl"

    # 2) 显式给产出样本
    python tools/voice_eq_calib.py --key daniya \\
        --ref assets/voices/daniya_ref.wav --out a.wav --out b.wav

    # 3) 体检现有曲线 / 列出已有曲线
    python tools/voice_eq_calib.py --check daniya
    python tools/voice_eq_calib.py --list
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
import wave
from typing import Dict, List, Optional, Tuple

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

import soundfile as sf  # noqa: E402

sys.path.insert(0, HERE)
import hf_restore as HR  # noqa: E402  参考实现（曲线只用它的 build_gain，应用走 hf_eq）

ARCHIVE = os.path.join(PROJ, "src", "ui", "web", "assets", "chat_audio")
CURVE_DIR = os.path.join(PROJ, "assets", "voice_eq")
SR = 24000


# --------------------------------------------------------------------------- #
# 体检指标（与 tools/diag_ceiling_verdict.py 同族口径）
# --------------------------------------------------------------------------- #
def _ltas_db(x: np.ndarray, sr: int = SR) -> Tuple[np.ndarray, np.ndarray]:
    L, f, _ = HR.ltas(x)
    return 20 * np.log10(L + 1e-12), f


def metrics(x: np.ndarray, sr: int = SR) -> Dict[str, float]:
    n = min(len(x), sr * 20)
    if n < 512:
        return {}
    s = x[:n] * np.hanning(n)
    S = np.abs(np.fft.rfft(s)) ** 2
    f = np.fft.rfftfreq(n, 1 / sr)
    r = S[(f >= 300) & (f < 3400)].sum() + 1e-12
    seg = {}
    for lo, hi in ((3400, 6000), (6000, 8000), (8000, 12000)):
        seg["b%d" % lo] = float(10 * np.log10((S[(f >= lo) & (f < hi)].sum() + 1e-12) / r))
    mag = np.abs(np.fft.rfft(s))
    seg["cent"] = float((f * mag).sum() / (mag.sum() + 1e-12))
    cs = np.cumsum(S)
    seg["f95"] = float(f[np.searchsorted(cs, 0.95 * (cs[-1] + 1e-12))])
    m = (f >= 300) & (f <= 8000)
    seg["slope"] = float(np.polyfit(np.log2(np.maximum(f[m], 1)),
                                    10 * np.log10(S[m] + 1e-20), 1)[0])
    return seg


def flatness_f95(x: np.ndarray, sr: int = SR) -> Tuple[float, float]:
    """质检两个数：flat（谱平坦度）与 f95（95% 能量带宽）。"""
    st = HR.stft(x)
    S = st[0] if isinstance(st, tuple) else st
    P = np.abs(S) ** 2
    f = np.fft.rfftfreq(HR.N, 1 / sr)
    m = (f >= 500) & (f < 4000)
    p = P[:, m].mean(axis=0) + 1e-20
    flat = float(np.exp(np.log(p).mean()) / p.mean())
    tot = P.mean(axis=0).sum()
    cs = np.cumsum(P.mean(axis=0))
    f95 = float(f[np.searchsorted(cs, 0.95 * (tot + 1e-12))])
    return flat, f95


def ltas_dist(a_gain_db: np.ndarray, a_f: np.ndarray,
              b_gain_db: np.ndarray, b_f: np.ndarray) -> float:
    """两段音频的 LTAS 距离（dB，1k–11k 平均绝对差）。"""
    f = a_f
    m = (f >= 1000) & (f <= 11000)
    bb = np.interp(f[m], b_f, b_gain_db)
    return float(np.mean(np.abs(a_gain_db[m] - bb)))


def band_dev(x: np.ndarray, ref_m: Dict[str, float]) -> float:
    """整段频段剖面与真源的平均绝对偏差（dB）——与人耳听感更接近的第二把尺子。

    只用 LTAS（1kHz 归一）会被单个元音的基频强度带偏；加这一把可防「一个指标改善、
    另一个变差」的假改善（实测踩过：LTAS 好 54%，但 3.4-6k 反而低 1.1dB）。
    """
    m = metrics(x)
    if not m:
        return float("nan")
    return float(np.mean([abs(m[k] - ref_m[k]) for k in ("b3400", "b6000", "b8000")]))


def read_mono24(p: str) -> Optional[np.ndarray]:
    try:
        x, sr = sf.read(p, dtype="float32")
    except Exception:
        try:
            with wave.open(p, "rb") as wf:
                sr = wf.getframerate()
                raw = wf.readframes(wf.getnframes())
            x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        except Exception:
            return None
    if x.ndim > 1:
        x = x.mean(axis=1)
    if sr != SR:
        n_out = max(1, int(len(x) * SR / sr))
        idx = np.linspace(0, len(x) - 1, n_out)
        lo = idx.astype(np.int64)
        hi = np.minimum(lo + 1, len(x) - 1)
        fr = (idx - lo).astype(np.float32)
        x = (x[lo] * (1 - fr) + x[hi] * fr).astype(np.float32)
    return x


def wav_duration(p: str) -> Optional[float]:
    try:
        with wave.open(p, "rb") as wf:
            return wf.getnframes() / float(wf.getframerate())
    except Exception:
        x = read_mono24(p)
        return len(x) / SR if x is not None else None


# --------------------------------------------------------------------------- #
# 用通话追踪的 pcm_ms 精确挑出「原生双工产出」
# --------------------------------------------------------------------------- #
def match_trace(patterns: List[str], min_s: float = 1.5,
                tol: float = 0.03) -> List[Tuple[str, str]]:
    """返回 [(wav路径, 说明)]：只取与追踪里 emit.pcm_ms 对得上的存档 wav。"""
    emits: List[Tuple[float, str, int]] = []
    for pat in patterns:
        for tp in glob.glob(pat if os.path.isabs(pat) else os.path.join(PROJ, pat)):
            try:
                for line in open(tp, encoding="utf-8", errors="replace"):
                    line = line.strip()
                    if not line:
                        continue
                    r = json.loads(line)
                    if r.get("ev") == "emit" and (r.get("pcm_ms") or 0) >= min_s * 1000:
                        emits.append((os.path.getmtime(tp), tp, int(r["pcm_ms"])))
            except Exception:
                continue
    if not emits:
        return []
    # 归档 wav 建索引：时长 -> [(路径, mtime)]
    idx: List[Tuple[float, str, float]] = []
    for f in os.listdir(ARCHIVE):
        if not f.lower().endswith(".wav"):
            continue
        fp = os.path.join(ARCHIVE, f)
        d = wav_duration(fp)
        if d:
            idx.append((d, fp, os.path.getmtime(fp)))
    out: List[Tuple[str, str]] = []
    seen = set()
    for tmt, tp, ms in emits:
        want = ms / 1000.0
        best = None
        for d, fp, mt in idx:
            if abs(d - want) <= max(tol, want * 0.01):
                if abs(mt - tmt) > 1800:          # 与追踪文件 mtime 相差 30 分钟内
                    continue
                if best is None or abs(d - want) < abs(best[0] - want):
                    best = (abs(d - want), fp, d)
        if best and best[1] not in seen:
            seen.add(best[1])
            out.append((best[1], "追踪 %s pcm_ms=%d ↔ %.2fs（差 %.3fs）"
                        % (os.path.basename(tp), ms, best[2], best[0])))
    return out


# --------------------------------------------------------------------------- #
# 标定
# --------------------------------------------------------------------------- #
def calibrate(key: str, ref_path: str, out_paths: List[str],
              min_total_s: float = 6.0, scale: float = 1.0,
              max_boost_db: float = 16.0, write: bool = True,
              note: str = "", also: Optional[List[str]] = None) -> int:
    print("=" * 104)
    print("标定角色：%s" % key)
    print("=" * 104)
    if not os.path.isfile(ref_path):
        print("❌ 真源不存在：%s" % ref_path)
        return 2
    ref = read_mono24(ref_path)
    if ref is None or len(ref) < SR:
        print("❌ 真源不可读或过短：%s" % ref_path)
        return 2
    rf, rf95 = flatness_f95(ref)
    ok_ref = (rf > 0.002 and rf95 > 1200)
    print("真源：%s  %.2fs  flat=%.5f f95=%.0f  → 质检 %s"
          % (os.path.basename(ref_path), len(ref) / SR, rf, rf95,
             "通过" if ok_ref else "★不通过（flat>0.002 且 f95>1200）"))
    if not ok_ref:
        print("   ⚠️ 真源本身不合格，标定出来的曲线不可信。建议先换素材。")
    rm = metrics(ref)
    rl, rfa = _ltas_db(ref)
    print("   真源  3.4-6k=%.1f 6-8k=%.1f 8-12k=%.1f 谱心=%.0f f95(整体)=%.0f 斜率=%.2f"
          % (rm["b3400"], rm["b6000"], rm["b8000"], rm["cent"], rm["f95"], rm["slope"]))
    print()

    # 各产出体检
    print("产出样本体检（只应包含「原生双工 PCM」；外挂 TTS 的产出亮度差一倍以上）")
    print("-" * 104)
    print("%-34s%8s%9s%10s%9s%9s%9s%9s" %
          ("文件", "时长s", "flat", "f95", "3.4-6k", "6-8k", "8-12k", "谱心"))
    goods: List[Tuple[str, np.ndarray]] = []
    tot = 0.0
    for p, _unused_note in [(q, "") for q in out_paths]:
        if os.path.isfile(p):
            goods.append((p, read_mono24(p)))
        else:
            print("%-34s  不存在" % os.path.basename(p))
    for p, x in goods:
        if x is None:
            continue
        f_, f95_ = flatness_f95(x)
        m_ = metrics(x)
        print("%-34s%8.2f%9.5f%10.0f%9.1f%9.1f%9.1f%9.0f" %
              (os.path.basename(p), len(x) / SR, f_, f95_,
               m_["b3400"], m_["b6000"], m_["b8000"], m_["cent"]))
        tot += len(x) / SR
    if not goods:
        print("❌ 没有可用的产出样本")
        return 2
    print("-" * 104)
    print("合计产出 %.2fs（建议 ≥%.1fs）%s" % (tot, min_total_s,
          "" if tot >= min_total_s else "  ⚠️ 偏短，曲线可能不稳，建议多录几轮通话"))
    if tot < min_total_s:
        print("   ⚠️ 仍继续，但会在验收阶段检查是否真的变好；不过关就拒绝写入。")

    # ---- 防混档闸门（★重要）：不同档位的产出亮度差一倍以上，混在一起算出的曲线必然偏 ---- #
    slopes = [(os.path.basename(p), metrics(x)["slope"]) for p, x in goods if x is not None]
    if slopes:
        vals = [s for _, s in slopes]
        spread = max(vals) - min(vals)
        print("   样本谱斜率跨度 %.2f dB/oct（%s）"
              % (spread, "  ".join("%s=%.2f" % (n[:14], s) for n, s in slopes)))
        if spread > 3.0:
            print("   ❌ **样本横跨不同档位**（音频档与视频档的产出斜率差可达 ~5 dB/oct）。")
            print("      混档标定出的曲线会把好的样本过补偿、差的样本欠补偿。")
            print("      ⇒ 请只保留「与当前运行档位一致」的产出（语音通话用 audio 档产出），重跑。")
            if not os.environ.get("VOICE_EQ_ALLOW_MIXED"):
                print("      （确要用混合样本：设环境变量 VOICE_EQ_ALLOW_MIXED=1）")
                return 4
    print()

    # 拼接产出 → 算曲线
    out = np.concatenate([x for _, x in goods if x is not None])
    g, f = HR.build_gain(ref, out, cap_hi=max_boost_db, cap_lo=-6.0, scale=scale)

    # 逐样本验收（对每个样本单独施加同一曲线）
    print("验收：两把尺子都必须变好 —— ① LTAS 距真源（1k 归一，声学）② 频段剖面偏差（听感）")
    print("-" * 116)
    print("%-32s%21s%21s%14s%14s" % ("文件", "补偿前 b3.4/6/8k", "补偿后 b3.4/6/8k",
                                     "LTAS前→后", "剖面前→后"))
    improved = 0
    total = 0
    for p, x in goods:
        if x is None:
            continue
        y = HR.apply_gain(x, g)
        m0, m1 = metrics(x), metrics(y)
        l0, f0_ = _ltas_db(x)
        l1, _ = _ltas_db(y)
        d0 = ltas_dist(l0, f0_, rl, rfa)
        d1 = ltas_dist(l1, f0_, rl, rfa)
        b0 = band_dev(x, rm)
        b1 = band_dev(y, rm)
        total += 1
        both = (d1 < d0) and (b1 < b0)
        if both:
            improved += 1
        print("%-32s[%5.1f %5.1f %5.1f] [%5.1f %5.1f %5.1f] %6.2f→%5.2f %6.2f→%5.2f %s" %
              (os.path.basename(p), m0["b3400"], m0["b6000"], m0["b8000"],
               m1["b3400"], m1["b6000"], m1["b8000"], d0, d1, b0, b1,
               "✅" if both else ("△仅LTAS" if d1 < d0 else "❌")))
    print("-" * 116)
    print("验收结果：%d/%d 个样本「两把尺子都变好」" % (improved, total))
    if improved == 0 or improved * 2 < total:
        print("❌ **拒绝写入**：曲线没有在听感口径上稳定改善。")
        print("   最常见原因：产出样本太短 / 太少（单个 2–3 秒短句的 LTAS 不稳）。")
        print("   ⇒ 建议打一通 1–2 分钟的语音通话（多说几句），再重跑标定。")
        return 3

    # 全量（拼接）验收
    y_all = HR.apply_gain(out, g)
    m0, m1 = metrics(out), metrics(y_all)
    l1, _ = _ltas_db(y_all)
    print()
    print("合并产出整体：")
    print("  补偿前 3.4-6k=%.1f 6-8k=%.1f 8-12k=%.1f 谱心=%.0f 斜率=%.2f"
          % (m0["b3400"], m0["b6000"], m0["b8000"], m0["cent"], m0["slope"]))
    print("  补偿后 3.4-6k=%.1f 6-8k=%.1f 8-12k=%.1f 谱心=%.0f 斜率=%.2f"
          % (m1["b3400"], m1["b6000"], m1["b8000"], m1["cent"], m1["slope"]))
    print("  真源   3.4-6k=%.1f 6-8k=%.1f 8-12k=%.1f 谱心=%.0f 斜率=%.2f"
          % (rm["b3400"], rm["b6000"], rm["b8000"], rm["cent"], rm["slope"]))
    _d0 = ltas_dist(*_ltas_db(out), rl, rfa)
    _d1 = ltas_dist(l1, f0_, rl, rfa)
    print("  LTAS 距真源：%.2f → %.2f dB（消 %.0f%%）"
          % (_d0, _d1, 100 * max(0.0, 1 - _d1 / max(_d0, 1e-9))))
    print("  频段剖面偏差：%.2f → %.2f dB%s"
          % (band_dev(out, rm), band_dev(y_all, rm),
             "" if band_dev(y_all, rm) < band_dev(out, rm) else "  ⚠️ 未改善（听感口径）"))
    print()

    if not write:
        print("（--dry-run：未写盘）")
        return 0

    os.makedirs(CURVE_DIR, exist_ok=True)
    dst = os.path.join(CURVE_DIR, "%s.json" % key)
    payload = {
        "key": key,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ref": os.path.relpath(ref_path, PROJ),
        "sources": [os.path.relpath(p, PROJ) for p, _ in goods],
        "total_out_s": round(tot, 2),
        "scale": scale,
        "trim_db": 0.0,
        "freqs": [round(float(v), 3) for v in f],
        "gains_db": [round(float(v), 4) for v in g],
        "verify": {"improved": improved, "total": total},
        "note": note,
    }
    with open(dst, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False)
    print("✅ 已写入曲线：%s（%d 个频点，%.1f KB）"
          % (os.path.relpath(dst, PROJ), len(f), os.path.getsize(dst) / 1024))
    for alias in (also or []):
        alias = str(alias).strip()
        if not alias or alias == key:
            continue
        ap_p = os.path.join(CURVE_DIR, "%s.json" % alias)
        payload2 = dict(payload)
        payload2["key"] = alias
        payload2["alias_of"] = key
        with open(ap_p, "w", encoding="utf-8") as fp2:
            json.dump(payload2, fp2, ensure_ascii=False)
        print("   别名：%s（共用同一条曲线；显式指定，不做自动推断）" % os.path.relpath(ap_p, PROJ))
    print("   提示：若引擎正在运行，需重启 companion 让新曲线生效（曲线有进程内缓存）。")
    return 0


def check(key: str) -> int:
    p = os.path.join(CURVE_DIR, "%s.json" % key)
    if not os.path.isfile(p):
        print("角色 %s：没有曲线（%s）" % (key, p))
        return 1
    d = json.load(open(p, encoding="utf-8"))
    f = np.asarray(d["freqs"]); g = np.asarray(d["gains_db"])
    print("角色 %s 的补偿曲线" % key)
    print("  创建：%s   真源：%s" % (d.get("created"), d.get("ref")))
    print("  产出样本 %d 个，合计 %.2fs" % (len(d.get("sources") or []), d.get("total_out_s") or 0))
    print("  验收：%s/%s 个样本更接近真源" % (d.get("verify", {}).get("improved"),
                                              d.get("verify", {}).get("total")))
    if d.get("note"):
        print("  备注：%s" % d["note"])
    print("  增益(dB)：3k=%.1f  4k=%.1f  6k=%.1f  8k=%.1f  10k=%.1f  11k=%.1f"
          % tuple(float(np.interp(q, f, g)) for q in (3000, 4000, 6000, 8000, 10000, 11000)))
    return 0


def list_all() -> int:
    if not os.path.isdir(CURVE_DIR):
        print("还没有任何曲线（%s 不存在）" % CURVE_DIR)
        return 0
    fs = sorted(x for x in os.listdir(CURVE_DIR) if x.endswith(".json"))
    if not fs:
        print("还没有任何曲线")
        return 0
    print("已有曲线（%s）：" % CURVE_DIR)
    for x in fs:
        p = os.path.join(CURVE_DIR, x)
        try:
            d = json.load(open(p, encoding="utf-8"))
            print("  %-22s %s  样本%2d个/%.1fs  验收 %s/%s"
                  % (x, d.get("created", ""), len(d.get("sources") or []),
                     d.get("total_out_s") or 0,
                     d.get("verify", {}).get("improved"), d.get("verify", {}).get("total")))
        except Exception as e:  # noqa: BLE001
            print("  %-22s 读取失败 %s" % (x, e))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="按角色自动标定高频补偿曲线")
    ap.add_argument("--key", help="角色键（= characters[].id，与 assets/voice_eq/<key>.json 对应）")
    ap.add_argument("--ref", help="该角色的真源音频（越干净、越有代表性越好）")
    ap.add_argument("--out", action="append", default=[], help="产出样本 wav（可重复）")
    ap.add_argument("--match-trace", action="append", default=[],
                    help="用通话追踪自动挑原生产出，如 'data/call_trace_*.jsonl'")
    ap.add_argument("--min-total-s", type=float, default=6.0, help="产出总时长下限（秒）")
    ap.add_argument("--scale", type=float, default=1.0, help="曲线整体折扣（0.6=温和档）")
    ap.add_argument("--max-boost-db", type=float, default=16.0, help="单点最大抬升")
    ap.add_argument("--note", default="", help="写入曲线的备注（如「临时版，待重标定」）")
    ap.add_argument("--also", action="append", default=[],
                    help="额外写入同一条曲线到这些键（如共用音色的其它角色 id）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", metavar="KEY", help="体检某个已有曲线")
    ap.add_argument("--list", action="store_true", help="列出全部曲线")
    a = ap.parse_args()

    if a.list:
        return list_all()
    if a.check:
        return check(a.check)
    if not a.key or not a.ref:
        ap.print_help()
        return 2

    outs = list(a.out)
    if a.match_trace:
        ms = match_trace(a.match_trace)
        print("从通话追踪匹配到 %d 个原生产出：" % len(ms))
        for p, note in ms:
            print("   %s   %s" % (os.path.basename(p), note))
        print()
        outs += [p for p, _ in ms]
    if not outs:
        print("❌ 没有产出样本：请用 --out 指定，或用 --match-trace 自动挑。")
        return 2
    return calibrate(a.key, a.ref, outs, min_total_s=a.min_total_s,
                     scale=a.scale, max_boost_db=a.max_boost_db, write=not a.dry_run,
                     note=a.note, also=a.also)


if __name__ == "__main__":
    sys.exit(main())
