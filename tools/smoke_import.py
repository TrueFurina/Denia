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
    code = f"import {mod}"
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


def load_allow(path: str) -> dict[str, str]:
    """读白名单：每行 `模块名  # 原因`。允许清单只有带原因才收。"""
    out: dict[str, str] = {}
    if not path or not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            mod, _, why = line.partition("#")
            mod = mod.strip()
            if mod:
                out[mod] = why.strip() or "（未写原因）"
    return out


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

    allow = load_allow(os.path.join(root, args.allow) if not os.path.isabs(args.allow) else args.allow)
    unexpected = [r for r in fails if r["module"] not in allow]
    known = [r for r in fails if r["module"] in allow]
    stale = [m for m in allow if m not in {r["module"] for r in results}]

    kinds: dict[str, int] = {}
    for r in fails:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1

    print(f"\n=== 结果：{len(results)} 个模块，通过 {len(results) - len(fails)}，失败 {len(fails)} ===")
    print(f"    其中白名单内（已知可选）{len(known)}，**非预期失败 {len(unexpected)}**")
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

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(results, fh, ensure_ascii=False, indent=1)
        print(f"\n完整结果已写入 {args.json}")

    if args.strict and unexpected:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
