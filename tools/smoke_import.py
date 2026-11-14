"""逐个 import 项目内每个模块，找出「导入期就崩」的真实故障。

为什么需要它
------------
静态检查（compileall / 名字完整性）只能保证语法和名字对得上，保证不了
「这个模块能不能被加载」。2026-10-17 实测到两个只在 import 时才暴露的问题：

  · src/tts/cloud_clone_bridge.py  import 期 mkdir('G:\\') → FileNotFoundError
  · src/ui/webapp.py               顶层 argtypes 声明引用已删的 _u32 → NameError

两者 compileall 全绿。所以需要一次真实的加载。

做法
----
每个模块在**独立子进程**里 import，互不污染（有的模块会起线程/建设备/写文件），
超时即杀。按错误类型归类，便于区分「缺依赖」「真 bug」「路径问题」。

用法
----
    .venv/Scripts/python.exe tools/smoke_import.py
    .venv/Scripts/python.exe tools/smoke_import.py --jobs 8 --timeout 25
    .venv/Scripts/python.exe tools/smoke_import.py --strict   # 有失败即 exit 1
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys

SKIP_DIRS = {"__pycache__", ".git", ".venv", "python", "node_modules", "tests"}


def module_name(root: str, path: str) -> str:
    rel = os.path.relpath(path, root)[:-3]
    return rel.replace(os.sep, ".").replace("/", ".")


def collect(root: str) -> list[tuple[str, str]]:
    out = []
    for base in ("src",):
        for r, dirs, names in os.walk(os.path.join(root, base)):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for n in names:
                if n.endswith(".py") and not n.startswith("_"):
                    p = os.path.join(r, n)
                    out.append((module_name(root, p), p))
    return sorted(out)


_MEM_FAIL_MARK = "Memory allocation still failed"


def is_mem_contention(result: dict) -> bool:
    """这条失败是不是「并发下 OpenBLAS 分配不到内存」—— 允许串行复核一次的错之一。

    为什么只认这一类：**复核会掩盖真缺陷**。这条错误有外部证据（同一模块单独跑就过、
    文案来自 OpenBLAS 而不是产品代码），别的错误没有这个证据。所以判据是"错误文案"
    而不是"失败了几条"或"是不是第一次跑"。
    """
    return _MEM_FAIL_MARK in ((result.get("detail") or "") + (result.get("trace") or ""))


def is_load_artifact(result: dict) -> bool:
    """这条失败是不是「机器忙」造成的假红 —— 允许串行复核一次的第二种。

    【2026-11-10】全量 `check_all`（242 道门连跑 1.5 小时）里，`src.agent_pro.selftest`
    报了 `[超时] >25s` —— 而它单独跑只要几秒。当时机器正被前后几道重闸门压着，
    182 个模块 × 8 并发子进程一起抢 CPU，25s 的墙钟超时被撞穿。
    后果和 2026-10-21 那次内存假红一模一样：**门红成噪音之后，真红就没人看了**。

    为什么超时够格进复核名单（和内存那条同一个证据标准）：
      · 复核用的是**同一个 timeout**，所以"真的卡死"的模块复核还会超时，仍然红；
      · 复核是**串行**的，排除并发抢 CPU 这个唯一变量；
      · 判据是 `status == "TIMEOUT"`（异常类型），不是"第几次跑"。
    别的错误一律不重试 —— 重试会把真缺陷盖掉。
    """
    return result.get("status") == "TIMEOUT" or is_mem_contention(result)


def classify(err: str) -> str:
    if "ModuleNotFoundError" in err or "ImportError" in err:
        return "缺依赖"
    if "NameError" in err:
        return "未定义名字"
    if "FileNotFoundError" in err or "WinError 3" in err or "No such file" in err:
        return "路径不存在"
    if "SyntaxError" in err:
        return "语法错误"
    if "OSError" in err or "WinError" in err:
        return "环境/设备"
    if "TimeoutExpired" in err or "超时" in err:
        return "超时"
    return "其他异常"


def run_one(exe: str, root: str, mod: str, timeout: int) -> dict:
    # 【2026-11-08 修·PYTHONPATH 会被嵌入式运行时静默忽略】原来是
    #     code = "import %s" % mod
    #     env["PYTHONPATH"] = root
    # 两条一起指望"子进程能从 root 导入"。但本仓的 `.venv` 是用**随包的嵌入式运行时**
    # （`python/python.exe`，带 `python312._pth`）建的，那个运行时 `sys.path` 里
    # 既没有 `''`（cwd）也不吃 `PYTHONPATH` —— 于是 `--root` 形同虚设，
    # 所有模块一律 `ModuleNotFoundError`。
    # 实测（`tools/test_smoke_import_retry.py` 的自建假根）：
    #     python            → 12 PASS / 0 FAIL
    #     .venv/Scripts/python → 6 PASS / 6 FAIL（四条夹具全报 No module named 'src.fixturepkg'）
    # 而 `check_all.py` 给这道门配的正是 `PY_VENV`。
    # 修法：**不靠环境变量，直接在子进程里插 sys.path** —— 任何解释器都吃这一套。
    # PYTHONPATH 保留，对吃它的解释器无害。
    code = "import sys; sys.path.insert(0, %r); import %s" % (root, mod)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = root
    try:
        p = subprocess.run([exe, "-c", code], cwd=root, capture_output=True,
                           text=True, timeout=timeout, env=env,
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return {"module": mod, "status": "TIMEOUT", "kind": "超时", "detail": f">{timeout}s"}
    if p.returncode == 0:
        return {"module": mod, "status": "OK", "kind": "", "detail": ""}
    err = (p.stderr or "").strip().split("\n")
    # 取最后一条非空行作为摘要，完整栈另存
    last = next((l for l in reversed(err) if l.strip()), "")
    return {
        "module": mod,
        "status": "FAIL",
        "kind": classify(p.stderr or ""),
        "detail": last[:160],
        "trace": "\n".join(err[-18:]),
    }


def load_allow(path: str) -> tuple[dict[str, str], list[str]]:
    """读白名单：每行 `模块名  # 原因`。**没写原因的条目单独返回**，由调用方判错。

    【2026-10-21 修·判据松了一档】原实现是 `out[mod] = why.strip() or "（未写原因）"`，
    等于允许无原因加入白名单。白名单是藏污纳垢的经典位置：一旦可以无原因加进去，
    下一个真 bug 就会被「顺手加一行」永久埋掉，而「（未写原因）」在输出里看着还像
    一句说明，比留空更隐蔽。变异实测：不写原因 → 旧实现 exit 0（放行）。
    """
    out: dict[str, str] = {}
    no_reason: list[str] = []
    if not path or not os.path.exists(path):
        return out, no_reason
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            mod, _, why = line.partition("#")
            mod = mod.strip()
            if mod:
                out[mod] = why.strip()
                if not why.strip():
                    no_reason.append(mod)
    return out, no_reason


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--timeout", type=int, default=25)
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--allow", default="tools/import_allowlist.txt",
                    help="已知可选失败白名单；--strict 下只有不在名单里的失败才算错")
    ap.add_argument("--json", help="把完整结果写到指定文件")
    ap.add_argument("--only", help="只跑匹配该子串的模块")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    exe = sys.executable
    mods = collect(root)
    if args.only:
        mods = [(m, p) for m, p in mods if args.only in m]
    print(f"用 {exe} 逐个 import {len(mods)} 个模块（并发 {args.jobs}，单模块超时 {args.timeout}s）\n")

    results: list[dict] = []
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futs = {pool.submit(run_one, exe, root, m, args.timeout): m for m, _ in mods}
        for f in concurrent.futures.as_completed(futs):
            results.append(f.result())
            done += 1
            if done % 20 == 0:
                print(f"  … {done}/{len(mods)}", flush=True)

    results.sort(key=lambda r: (r["status"] != "FAIL", r["module"]))
    fails = [r for r in results if r["status"] != "OK"]

    allow, no_reason = load_allow(os.path.join(root, args.allow) if not os.path.isabs(args.allow) else args.allow)
    unexpected = [r for r in fails if r["module"] not in allow]
    known = [r for r in fails if r["module"] in allow]

    # 【2026-10-21】并发下 OpenBLAS 分配失败 → **串行复核一次**。
    # 实测：本机空闲内存只剩 2.4G 时，8 个并发子进程各 import numpy/scipy，OpenBLAS 报
    # "Memory allocation still failed after 10 retries" —— 3 个模块被判红，而产品没有
    # 任何问题（同一批模块单独跑就过）。这是**闸门自己在造噪音**：红成噪音之后，真红
    # 就没人看了。
    # 三条边界，缺一条就变成"用重试糊过去"：
    #   ① 只复核**这两类错误**（内存分配失败 / 墙钟超时；别的错不重试 —— 重试会把真缺陷盖掉）；
    #   ② **串行**复核（一次一个子进程，这才是排除并发竞争的办法）；
    #   ③ **打印复核了几条、结果如何**，不静默。
    # ④ 复核范围是**全部失败项**，不只是"非预期失败"（2026-10-21 修）：白名单里的模块
    #    同样会因为并发抢内存而假红，而它会被印成「白名单内 N 条」—— 数字上没错，但
    #    那个 N 在骗人（它让"白名单条目是否还该留着"这个判断失去依据）。
    # ⑤ 【2026-11-10】超时也进复核名单：全量 check_all 连跑 1.5 小时时，
    #    `src.agent_pro.selftest` 撞穿 25s 墙钟超时（单独跑只要几秒），整份门报红。
    #    复核用**同一个 timeout**、且**串行**，所以真卡死的模块复核还会超时、仍然红。
    serial_ok: list[str] = []
    _mem = [r for r in fails if is_load_artifact(r)]
    for r in _mem:
        again = run_one(exe, root, r["module"], args.timeout)
        if again["status"] == "OK":
            for i, x in enumerate(results):
                if x is r:
                    results[i] = again
                    break
            if r in unexpected:
                unexpected.remove(r)
            serial_ok.append(r["module"])
        else:
            r["detail"] = (r.get("detail") or "") + "（串行复核仍失败）"
    # `--only` 下大部分模块压根没跑，把它们当"已不再失败"会误导人去删白名单条目。
    stale = [] if args.only else [m for m in allow if m not in {r["module"] for r in results}]

    # 复核通过的要从 fails 里摘掉，否则"通过数/失败数"还是旧账（数字对不上就是谎话）。
    # known 同理：它在复核**之前**算过，若救回的那条正好在白名单里，不重算就会印出
    # "白名单内 1 条"而实际 0 条。
    fails = [r for r in results if r["status"] != "OK"]
    known = [r for r in fails if r["module"] in allow]
    kinds: dict[str, int] = {}
    for r in fails:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    print(f"\n=== 结果：{len(results)} 个模块，通过 {len(results) - len(fails)}，失败 {len(fails)} ===")
    print(f"    其中白名单内（已知可选）{len(known)}，**非预期失败 {len(unexpected)}**")
    if serial_ok:
        print(f"    [串行复核] {len(serial_ok)} 条并发下的假红（OpenBLAS 抢内存 / 超时），"
              f"串行后全部通过：{', '.join(serial_ok)}")
    for k, c in sorted(kinds.items(), key=lambda x: -x[1]):
        print(f"  {k:10s} {c}")

    if unexpected:
        print("\n=== 非预期失败（需要处理）===")
        for r in unexpected:
            print(f"  [{r['kind']}] {r['module']}")
            print(f"      {r['detail']}")
    if known:
        print("\n=== 白名单内失败（已知可选，不阻断）===")
        for r in known:
            print(f"  {r['module']}: {allow[r['module']]}")
    if stale:
        print("\n=== 白名单里已不再失败的条目（建议清理）===")
        for m in stale:
            print(f"  {m}")
    if no_reason:
        print("\n=== 白名单条目缺原因（必须补上，否则等于没有白名单）===")
        for m in no_reason:
            print(f"  {m}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(results, fh, ensure_ascii=False, indent=1)
        print(f"\n完整结果已写入 {args.json}")

    if args.strict and (unexpected or no_reason):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
