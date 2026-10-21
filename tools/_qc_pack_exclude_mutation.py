#!/usr/bin/env python
"""打包排除规则（开发期残留页面）的变异刀（2026-10-22）。

2026-10-22 发现 `src/ui/web/redesign/` 下 14 个 `_` 前缀文件（设计稿 / 重构前备份 /
测试台）合计约 1.1MB，全仓零引用，却按 `git ls-files` 一路进队友的包。队友解包后能在
URL 里打开它们，看到的是**能点但没反应**的旧 UI（假接口博物馆）。

修法：`pack_for_teammate.py` 加 `EXCLUDE_DEAD_WEB` 精确清单 + `_exclude_reason` 里
一条判定。**不用前缀一刀切**：`src/ui/web/_gpu.txt` 同样带 `_` 前缀，却是
`proactive._has_usable_gpu()` 写的运行时数据，排了会坏功能。

本脚本验四刀：
  M1 清空 EXCLUDE_DEAD_WEB        → [1b] 必须红
  M2 删掉 _exclude_reason 那条判定 → [1b] 必须红
  M3 改成按 `_` 前缀一刀切        → [2] 的 _gpu.txt 必须红（对照组）
  M4 DEAD_WEB_SCAN_DIR 指向不存在 → [3b] 的"找不到扫描目录"必须红

M3 不是走过场：没有它，"把规则放宽成一刀切"也能让 [1b] 全绿，而 _gpu.txt 已经被
静默排掉——正是这条判据要防的那种"绿着失效"。

用法：python tools/_qc_pack_exclude_mutation.py
退出码 0 = 四刀都按预期红。
"""

import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK = os.path.join(ROOT, "tools", "pack_for_teammate.py")
TEST = os.path.join(ROOT, "src", "tests", "test_pack_for_teammate.py")
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")

# 两个文件都是 LF
EOL = b"\n"


def _enc(s):
    return s.encode("utf-8")


def _read(p):
    with io.open(p, "rb") as f:
        return f.read()


def _write(p, data):
    with io.open(p, "wb") as f:
        f.write(data)


def run_test():
    """跑打包闸门测试，返回 (退出码, 输出)。不接管道。"""
    out_path = os.path.join(ROOT, "logs", "_qc_pack_exclude.out")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with io.open(out_path, "wb") as fo:
        proc = subprocess.run([PY, TEST], stdout=fo, stderr=subprocess.STDOUT, cwd=ROOT)
    with io.open(out_path, "r", encoding="utf-8", errors="replace") as fi:
        return proc.returncode, fi.read()


def red_lines(out):
    return [l for l in out.splitlines() if "✗" in l]


def main():
    bad = 0
    base = _read(PACK)

    code, out = run_test()
    if code != 0:
        print("  FAIL 基线就不绿，先查测试本身：")
        for l in red_lines(out)[:8]:
            print("    " + l.strip()[:160])
        return 1
    print("  基线全绿")

    # ---------------- M1：清空清单 ----------------
    anchor = _enc("EXCLUDE_DEAD_WEB = {")
    end = _enc("}\n# 哪些目录要接受\"未登记 _ 前缀文件\"的反向检查")
    if base.count(anchor) != 1 or base.count(end) != 1:
        print("  FAIL M1：锚点命中 %d/%d 次（应各为 1）" % (base.count(anchor), base.count(end)))
        bad += 1
    else:
        i = base.find(anchor)
        j = base.find(end, i) + len(end)
        _write(PACK, base[:i] + anchor + EOL + end + base[j:])
        try:
            code, out = run_test()
        finally:
            _write(PACK, base)
        if code == 0:
            print("  FAIL M1 清空清单：测试仍绿（残留页面会悄悄进包）")
            bad += 1
        elif not red_lines(out):
            print("  FAIL M1：红了但看不到 ✗ 行")
            bad += 1
        else:
            hit = [l for l in red_lines(out) if "残留" in l or "_av_test" in l or "_te_" in l]
            print("  PASS M1 清空清单：红了（%d 条 ✗，含残留页面相关 %d 条）"
                  % (len(red_lines(out)), len(hit)))

    # ---------------- M2：删掉判定 ----------------
    judge = (
        _enc("    rel_posix = rel.replace(\"\\\\\", \"/\")") + EOL
        + _enc("    if rel_posix in EXCLUDE_DEAD_WEB:") + EOL
        + _enc("        return f\"开发期残留页面：{EXCLUDE_DEAD_WEB[rel_posix]}\"") + EOL
    )
    if base.count(judge) != 1:
        print("  FAIL M2：判定锚点命中 %d 次（应为 1）" % base.count(judge))
        bad += 1
    else:
        _write(PACK, base.replace(judge, b""))
        try:
            code, out = run_test()
        finally:
            _write(PACK, base)
        if code == 0:
            print("  FAIL M2 删掉判定：测试仍绿（清单成了不执行的花名册）")
            bad += 1
        elif not red_lines(out):
            print("  FAIL M2：红了但看不到 ✗ 行")
            bad += 1
        else:
            print("  PASS M2 删掉判定：红了（%d 条 ✗）" % len(red_lines(out)))

    # ---------------- M3：对照组——按 _ 前缀一刀切 ----------------
    loose = (
        _enc("    rel_posix = rel.replace(\"\\\\\", \"/\")") + EOL
        + _enc("    if low.startswith(\"_\"):") + EOL
        + _enc("        return \"带 _ 前缀，一概当残留\"") + EOL
    )
    if base.count(judge) != 1:
        print("  FAIL M3：锚点异常")
        bad += 1
    else:
        _write(PACK, base.replace(judge, loose))
        try:
            code, out = run_test()
        finally:
            _write(PACK, base)
        if code == 0:
            print("  FAIL M3 按前缀一刀切：测试仍绿——_gpu.txt 被静默排掉了！")
            bad += 1
        else:
            hit = [l for l in red_lines(out) if "_gpu" in l]
            if not hit:
                print("  FAIL M3：红了但不是 _gpu.txt 被误排")
                for l in red_lines(out)[:5]:
                    print("    " + l.strip()[:160])
                bad += 1
            else:
                print("  PASS M3 按前缀一刀切：红了「%s」" % hit[0].strip()[:90])

    # ---------------- M4：扫描目录指向不存在 ----------------
    scan = _enc('DEAD_WEB_SCAN_DIR = "src/ui/web/redesign/"')
    if base.count(scan) != 1:
        print("  FAIL M4：扫描目录锚点命中 %d 次" % base.count(scan))
        bad += 1
    else:
        _write(PACK, base.replace(scan, _enc('DEAD_WEB_SCAN_DIR = "src/ui/web/nope_dir/"')))
        try:
            code, out = run_test()
        finally:
            _write(PACK, base)
        if code == 0:
            print("  FAIL M4 扫描目录失效：测试仍绿（反向检查被静默跳过）")
            bad += 1
        else:
            hit = [l for l in red_lines(out) if "找不到扫描目录" in l]
            if not hit:
                print("  FAIL M4：红了但不是「找不到扫描目录」")
                for l in red_lines(out)[:5]:
                    print("    " + l.strip()[:160])
                bad += 1
            else:
                print("  PASS M4 扫描目录失效：报了「找不到扫描目录」")

    # ---------------- 还原校验 ----------------
    if _read(PACK) != base:
        print("  FAIL 文件没有还原到原字节！")
        bad += 1
    else:
        print("  pack_for_teammate.py 已字节级还原")

    print("=" * 60)
    if bad:
        print("变异刀发现 %d 处问题" % bad)
        return 1
    print("四刀全部符合预期")
    return 0


if __name__ == "__main__":
    sys.exit(main())
