# -*- coding: utf-8 -*-
"""「投影层自检」闸门的变异验证：五刀，刀刀必须被抓住。

为什么补这把刀：这道门守的是 `src/models/` 那一层投影（13 个调用点、只读性、
summary 自洽、与 `Config` 口径一致）。2026-11-09 第三十轮给它加了「缺 config.yaml
就退到出厂模板」的回退 —— 动了判据的入口，就得证明牙还在。此前它没有任何刀，
也就是「判据有没有牙」这件事从没被验过。

五刀各打一条判据：
  M1 让 `build_model_view` 偷偷改 raw      → 「只读性」必须红
  M2 返回前砍掉一个 site（13 → 12）        → 「调用点数应为 13」必须红
  M3 两个 site 用同一个 id                 → 「调用点 id 重复」必须红
  M4 summary 里 in_slot 凭空 +1            → 「summary 不自洽」必须红
  M5 `CALL_SITES` 少一项（第二份声明漂移） → 「CALL_SITES 与视图不一致」必须红

【2026-11-09 修·头一版四把刀里有两把是废刀，而且废得很有教育意义】
初版 M2/M3 去改 `CALL_SITES`（删一项 / 让两个 id 撞车），跑出来是「锚点命中 0 次」——
因为真实源码里三个 id 挤在同一行，锚点本身就没写对。修锚点时才发现更要命的事：
**改 `CALL_SITES` 根本不会让这道门变红**。`CALL_SITES` 只是被 `__all__` 导出的一份
声明，`build_model_view` 建视图走的是 13 段手写 `sites.append`，压根不读它。
也就是说那道「调用点数应为 13」的判据，守的从来不是 `CALL_SITES`。
于是分两步补：先给闸门加一条 `CALL_SITES` 与视图的逐项对账（判据），
再把 M5 对准它 —— 有判据才有刀，不是有刀就有判据。

判据三件套（沿用本仓 2026-11-08 的修法）：
  · `_judged()` —— 非零退出**且**输出里有 [FAIL]，"崩了"不算"抓住了"；
  · `_baseline_green()` —— 起点必须先绿，恒红的判定能让每把刀都变红；
  · 每刀都备份字节、finally 还原并核对 sha256，刀自己不许把源码弄坏。

跑法：.venv/Scripts/python.exe -u tools/_qc_models_layer_mutation.py
退出码：0 = 五刀全红；1 = 有刀没被抓住；77 = 本机跑不了（缺依赖等）。
"""
from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

PY = sys.executable
GATE = os.path.join("tools", "check_models_layer.py")
CATALOG = os.path.join("src", "models", "catalog.py")

# (标签, 目标文件, 原文锚点, 替换文本, 期望出现的失败关键词)
MUTS = [
    ("M1 让 build_model_view 偷改 raw",
     CATALOG,
     "    # 6 槽位：**直接调** model_registry，不复制逻辑（第二份真相是 bug 温床）\n",
     "    # 6 槽位：**直接调** model_registry，不复制逻辑（第二份真相是 bug 温床）\n"
     "    raw[\"_qc_probe\"] = 1  # 变异刀：故意破坏只读性\n",
     "只读性"),
    ("M2 返回前砍掉一个 site（13 → 12）",
     CATALOG,
     '    ready_n = sum(1 for s in sites if s.get("ready"))\n',
     '    sites = sites[:-1]  # 变异刀：故意少报一个调用点\n'
     '    ready_n = sum(1 for s in sites if s.get("ready"))\n',
     "调用点数应为 13"),
    ("M3 两个 site 用同一个 id",
     CATALOG,
     '        "id": "credentials.inventory", "family": "creds",\n',
     '        "id": "ocr.engine", "family": "creds",\n',
     "id 重复"),
    ("M4 summary 里 in_slot 凭空 +1",
     CATALOG,
     '            "in_slot": in_slot, "blind": len(sites) - in_slot,\n',
     '            "in_slot": in_slot + 1, "blind": len(sites) - in_slot,\n',
     "summary 不自洽"),
    ("M5 CALL_SITES 少一项（声明漂移）",
     CATALOG,
     '    "credentials.inventory",\n',
     '',
     "CALL_SITES 与视图不一致"),
]

# 给 `check_knife_noop.py` 认形状用：MUTS 每项是
# (标签, 目标文件, 原文锚点, 替换文本, 期望关键词) —— 看类型分不出 old/new，
# 这里显式指出第 2 项是 old、第 3 项是 new（下标从 0 起）。
# 不写这行，空变异体检会判 SHAPE（"分不清 old / new"），SHAPE 不算通过。
NOOP_SHAPE = ("idx", 2, 3)


def _read(p: str) -> bytes:
    with io.open(p, "rb") as f:
        return f.read()


def _write(p: str, data: bytes) -> None:
    with io.open(p, "wb") as f:
        f.write(data)


def _run() -> tuple:
    p = subprocess.run([PY, "-u", GATE], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _judged(rc: int, out: str) -> bool:
    """「判出了问题」而不是「崩了」：非零退出 **且** 打出 [FAIL]。"""
    return rc != 0 and "[FAIL]" in out


def _baseline_green() -> tuple:
    rc, out = _run()
    if rc == 77:
        return True, "77"
    return (rc == 0 and "[FAIL]" not in out), ""


def main() -> int:
    ok_green, why77 = _baseline_green()
    if why77:
        print("  [SKIP] 起点 exit 77：本机缺夹具（既无 config.yaml 也无 "
              "config.release.yaml），一道判据都没跑（不是通过）")
        return 77
    if not ok_green:
        print("  FAIL 起点就不是绿的 —— 恒红的判定能让每把刀都变红，"
              "这种\"全红\"不证明任何事")
        return 1
    print("  PASS 起点绿")

    bad = 0
    for label, target, old, new, keyword in MUTS:
        orig = _read(target)
        sha0 = hashlib.sha256(orig).hexdigest()
        text = orig.decode("utf-8")
        n = text.count(old)
        if n != 1:
            print("  FAIL %s：锚点命中 %d 次（应恰好 1 次），锚点已腐烂" % (label, n))
            bad += 1
            continue
        try:
            _write(target, text.replace(old, new).encode("utf-8"))
            rc, out = _run()
            caught = _judged(rc, out)
            hit = keyword in out
            if caught and hit:
                print("  PASS %s → 红了，且点名「%s」" % (label, keyword))
            elif caught:
                print("  FAIL %s → 红了，但没点名「%s」（可能是别的原因崩的）"
                      % (label, keyword))
                for ln in [x for x in out.splitlines() if "[FAIL]" in x][:3]:
                    print("        | " + ln.strip()[:150])
                bad += 1
            else:
                print("  FAIL %s → 没抓住（闸门失效）" % label)
                bad += 1
        finally:
            _write(target, orig)
            if hashlib.sha256(_read(target)).hexdigest() != sha0:
                print("  FAIL %s：还原后字节不一致！" % label)
                bad += 1

    rc, out = _run()
    if rc == 0 and "[FAIL]" not in out:
        print("  PASS 还原后仍然绿")
    else:
        print("  FAIL 还原后不是绿的 —— 刀自己把源码弄坏了")
        bad += 1

    print("\n%s" % ("五刀全红" if bad == 0 else "有 %d 处异常" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
