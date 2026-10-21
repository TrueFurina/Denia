# -*- coding: utf-8 -*-
"""「导入冒烟」的串行复核逻辑自测（2026-10-21 新增）。

## 守的是什么

`smoke_import` 用 8 个并发子进程 import 每个模块。本机空闲内存只剩 2.4G 时，几个
numpy/scipy 模块同时 import，OpenBLAS 报 `Memory allocation still failed after 10
retries` —— 于是**闸门红了，而产品没有任何问题**（同一模块单独跑就过）。红成噪音之后，
真红就没人看了。

修法是"只对这一种错误串行复核一次"。这个修法本身有两个失败模式，本测试各守一条：

  1. **复核范围放宽**（什么错都重试）→ 真缺陷被重试掩盖 → 用 `real.py` 守：
     它必须红，而且**不能带"串行复核仍失败"的标记**（说明它压根没进复核）。
  2. **复核一律判过**（复核完就当成功）→ 用 `memfail.py` 守：它每次都报同样的
     OpenBLAS 文案，串行复核后**必须仍然红**。

再加一个 `flaky.py`：第一次失败、第二次成功（模拟"并发抢不到内存、串行就能过"），
它必须被救回来并出现在 `[串行复核]` 那行里 —— 否则这条修法等于没生效。

用**独立假根**（临时目录里的 `src/`），不碰真仓库的模块。
"""
import io
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "smoke_import.py")

OK = FAIL = 0


def chk(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print("  PASS", name, extra)
    else:
        FAIL += 1
        print("  FAIL", name, extra)


_MEM_ERR = "OpenBLAS error: Memory allocation still failed after 10 retries, giving up."


def build_fixture(base: str) -> None:
    pkg = os.path.join(base, "src", "fixturepkg")
    os.makedirs(pkg, exist_ok=True)
    io.open(os.path.join(pkg, "__init__.py"), "w", encoding="utf-8", newline="\n").write("")
    io.open(os.path.join(pkg, "ok.py"), "w", encoding="utf-8", newline="\n").write("VALUE = 1\n")
    # 第一次失败（模拟并发抢不到内存），第二次成功
    io.open(os.path.join(pkg, "flaky.py"), "w", encoding="utf-8", newline="\n").write(
        "import os\n"
        "marker = os.path.join(os.path.dirname(__file__), '.seen')\n"
        "if not os.path.exists(marker):\n"
        "    open(marker, 'w').write('1')\n"
        "    raise RuntimeError(%r)\n" % _MEM_ERR)
    # 永远报同样的 OpenBLAS 文案：串行复核也救不回来 → 必须仍然红
    io.open(os.path.join(pkg, "memfail.py"), "w", encoding="utf-8", newline="\n").write(
        "raise RuntimeError(%r)\n" % _MEM_ERR)
    # 真缺陷：文案不是 OpenBLAS → **不许被复核**
    io.open(os.path.join(pkg, "real.py"), "w", encoding="utf-8", newline="\n").write(
        "raise RuntimeError('这是真缺陷')\n")


def main() -> int:
    base = tempfile.mkdtemp(prefix="daniya_smoke_retry_")
    build_fixture(base)
    out_json = os.path.join(base, "r.json")

    p = subprocess.run([sys.executable, TOOL, "--root", base, "--strict",
                        "--jobs", "4", "--timeout", "30", "--json", out_json],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    res = {}
    if os.path.isfile(out_json):
        for r in json.load(io.open(out_json, encoding="utf-8")):
            res[r["module"].split(".")[-1]] = r

    print("[1] 整体：真缺陷必须仍然让闸门红")
    chk("退出码 1（real.py 是真失败）", p.returncode == 1, p.returncode)
    chk("四条结果都回来了", set(res) == {"ok", "flaky", "memfail", "real"}, sorted(res))

    print("[2] 复核救得回来的：flaky")
    chk("flaky 最终判 OK", res.get("flaky", {}).get("status") == "OK",
        res.get("flaky", {}).get("status"))
    chk("输出里点名复核了它",
        "[串行复核]" in out and "flaky" in out.split("[串行复核]")[-1].split("\n")[0],
        [l for l in out.splitlines() if "[串行复核]" in l][:1])

    print("[3] 复核救不回来的：memfail 仍然红")
    chk("memfail 仍然 FAIL", res.get("memfail", {}).get("status") == "FAIL",
        res.get("memfail", {}).get("status"))
    chk("memfail 带「串行复核仍失败」标记（说明真复核过）",
        "串行复核仍失败" in (res.get("memfail", {}).get("detail") or ""),
        res.get("memfail", {}).get("detail"))

    print("[4] 不该被复核的：real（否则重试会掩盖真缺陷）")
    chk("real 是 FAIL", res.get("real", {}).get("status") == "FAIL",
        res.get("real", {}).get("status"))
    chk("real **没有**复核标记", "串行复核仍失败" not in (res.get("real", {}).get("detail") or ""),
        res.get("real", {}).get("detail"))
    chk("real 没出现在复核名单里",
        "real" not in out.split("[串行复核]")[-1].split("\n")[0]
        if "[串行复核]" in out else True)

    print("[5] 非预期失败计数 = 2（memfail + real，flaky 已被救回）")
    chk("非预期失败 2", "非预期失败 2" in out,
        [l for l in out.splitlines() if "非预期失败" in l][:1])

    print("[6] 白名单里的模块被复核救回后，「白名单内 N 条」必须重算")
    # 【2026-10-21】known 原本在复核**之前**算：flaky 首次失败、又恰好在白名单里，
    # 于是救回之后仍印「白名单内 1 条」——数字对不上就是谎话。这里把 flaky 写进
    # 白名单再跑一次，断言这一格是 0。
    allow = os.path.join(base, "allow.txt")
    io.open(allow, "w", encoding="utf-8", newline="\n").write(
        "src.fixturepkg.flaky  # 自测用：模拟「并发抢不到内存」的白名单条目\n")
    p2 = subprocess.run([sys.executable, TOOL, "--root", base, "--strict",
                         "--allow", allow, "--jobs", "4", "--timeout", "30"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    out2 = (p2.stdout or "") + (p2.stderr or "")
    chk("flaky 仍被救回", "[串行复核]" in out2 and "flaky" in out2.split("[串行复核]")[-1],
        [l for l in out2.splitlines() if "[串行复核]" in l][:1])
    chk("「白名单内」是 0（不是复核前算的 1）", "白名单内（已知可选）0" in out2,
        [l for l in out2.splitlines() if "白名单内" in l][:1])

    print()
    print("结果: %d PASS / %d FAIL" % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
