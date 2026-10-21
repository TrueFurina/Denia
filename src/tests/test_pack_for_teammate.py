"""`tools/pack_for_teammate.py` 的排除规则与验包逻辑测试。

为什么单独测排除规则
--------------------
这个脚本在真实仓库上跑一次，**排除清单一项都不会触发**——因为包内容取的是
`git ls-files`，而 `.gitignore` 早就把 node_modules / python / data / config.yaml
洗掉了。也就是说：把 `_exclude_reason` 整个改成 `return None`，出包照样是
764 个文件、照样验包通过。这正是 2026-10-19 那次假绿的同一个形状——
"跑通了"证明的是另一件事。

所以这里不测"能不能出包"，直接对着规则表喂路径，逐条断言哪条规则是活的。
第二层防御（硬排除）本来就是为"哪天有人 `git add -f` 了实配文件"准备的，
它必须能在没有真实触发场景时也被证明有效。

运行：.venv/Scripts/python.exe src/tests/test_pack_for_teammate.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import pack_for_teammate as pk  # noqa: E402

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        _fails.append(msg)


def _mk_zip(path: str, entries: dict[str, bytes]) -> str:
    with zipfile.ZipFile(path, "w") as zf:
        for n, b in entries.items():
            zf.writestr(n, b)
    return path


def main() -> int:
    print("=== [1] 隐私/实例红线：必须排除 ===")
    redlines = [
        "config.yaml",
        "Config.yaml",                  # Windows 大小写不敏感，必须一起拦
        "config.local.yaml",
        ".env",
        ".env.local",
        "call_records.json",
        "user_knowledge.yaml",
        "memory.json",
        "data/memory.json",
        "data/char_avatars/danya.png",
        "logs/run_20261020.log",
        "python/python.exe",
        "shell/node_modules/electron/index.js",
        "src/__pycache__/config.cpython-312.pyc",
        "dist/达妮娅.zip",
        "dist_stage/main.py",
        ".workbuddy-ai/memory/2026-10-19.md",
        ".workbuddy/memory/2026-10-18.md",
        "config.backup_20261020.bak",
        "assets/voices/daniya_ref.wav.swapbak_20260921_094240",
        "tools/daniya_sticker_skill/data/sticker_1.png",   # 嵌套 data/ 也要拦
        ".git/config",
        ".build_tools/nsis/makensis.exe",
        "setup.exe",
        "logs",                          # 目录名本身（防御性）
    ]
    for rel in redlines:
        reason = pk._exclude_reason(rel)
        check(reason is not None, f"排除 {rel}" + (f"（{reason}）" if reason else " ← 漏了"))

    print("\n=== [1b] 开发期残留页面：必须排除 ===")
    # 2026-10-22 补：`src/ui/web/redesign/` 下 14 个 `_` 前缀文件（设计稿 / 重构前
    # 备份 / 测试台）合计约 1.1MB，全仓零引用，却按 `git ls-files` 一路进包。
    # 队友解包后能在 URL 里打开它们，看到的是**能点但没反应**的旧 UI（假接口博物馆）。
    #
    # 【先断言清单非空】变异刀 M1 抓到的：下面这个循环是 `for rel in
    # EXCLUDE_DEAD_WEB.items()`——**清单被清空时循环空转，一条都不报**，看着像全绿。
    # 与"锚点命中 0 次就静默跳过"是同一种失效：判据的输入没了，它不会喊。
    check(len(pk.EXCLUDE_DEAD_WEB) >= 10,
          f"残留页面清单非空（现在 {len(pk.EXCLUDE_DEAD_WEB)} 条）")
    #
    # 逐个断言而不是只数数量：数数量的话，清单少一条、包里多一条，数字还是对得上。
    for rel, why in sorted(pk.EXCLUDE_DEAD_WEB.items()):
        reason = pk._exclude_reason(rel)
        check(reason is not None,
              f"排除 {rel}" + (f"（{reason}）" if reason else " ← 漏了，会进队友包"))

    print("\n=== [2] 必须保留：不能误伤 ===")
    keep = [
        "main.py",
        "launch_companion.py",
        "config.release.yaml",           # 发布模板，不是实配
        "requirements.txt",
        "src/version.py",
        "src/ui/web/index.html",
        "src/config.py",
        "shell/main.js",
        "shell/package.json",
        "tools/check_all.py",
        "tools/pack_for_teammate.py",
        "src/tests/test_pack_for_teammate.py",
        "assets/voices/daniya_ref.wav",
        "README.md",
        "src/data_loader.py",            # 以 data 开头的**文件名**，不是 data/
        "src/logging_setup.py",          # 以 log 开头的文件名
        "distutils_compat.py",           # 以 dist 开头的根文件
        "tools/daniya_sticker_skill/README.md",
        # 对照组：同样带 `_` 前缀，但它是**活的**运行时数据，不是残留页面。
        # 没有这一条，"按前缀一刀切排除"也能让上面 [1b] 全绿。
        "src/ui/web/_gpu.txt",
    ]
    for rel in keep:
        reason = pk._exclude_reason(rel)
        check(reason is None, f"保留 {rel}" + (f" ← 被误排：{reason}" if reason else ""))

    print("\n=== [3] collect() 与规则表一致 ===")
    files, dropped = pk.collect()
    check(len(files) > 0, f"收集到文件（{len(files)} 个）")
    leaked = [f for f in files if pk._exclude_reason(f)]
    check(not leaked, f"入包清单里没有违反规则的条目（违例 {leaked[:3]}）")
    missing = [r for r in pk.REQUIRED if r not in files]
    check(not missing, f"必需文件全部在清单里（缺 {missing}）")
    check(all(os.path.isfile(os.path.join(ROOT, f)) for f in files),
          "清单里每个路径在磁盘上真实存在")
    check(all("\\" not in f for f in files), "清单路径统一用 /（避免 zip 内混用分隔符）")
    dead_in = [f for f in files if f in pk.EXCLUDE_DEAD_WEB]
    check(not dead_in, f"入包清单里没有开发期残留页面（违例 {dead_in[:3]}）")

    print("\n=== [3b] 残留页面清单必须自己催：新出现的未登记 _ 页面要报错 ===")
    # 光有 [1b] 不够——它只证明"清单里的都被排了"，证明不了"没进清单的进不了包"。
    # 这条反向盯着：`redesign/` 下新出现一个 `_` 前缀文件而清单没登记 → 这里红。
    # 与"白名单必须会自己催你删"是同一条判据：清单不催人，就会变成背景噪音。
    scan_dir = os.path.join(ROOT, pk.DEAD_WEB_SCAN_DIR)
    if os.path.isdir(scan_dir):
        found = sorted(
            f"{pk.DEAD_WEB_SCAN_DIR}{n}"
            for n in os.listdir(scan_dir)
            if n.startswith("_") and os.path.isfile(os.path.join(scan_dir, n))
        )
        unregistered = [f for f in found if f not in pk.EXCLUDE_DEAD_WEB]
        check(not unregistered,
              f"{pk.DEAD_WEB_SCAN_DIR} 下没有未登记的 _ 前缀文件"
              + (f"（未登记：{unregistered}）" if unregistered else ""))
        # 清单里已不存在的条目 = stale，同样要红（文件删了就该把登记也删掉）
        stale = [f for f in pk.EXCLUDE_DEAD_WEB if not os.path.isfile(os.path.join(ROOT, f))]
        check(not stale, f"登记表里没有已消失的条目（stale：{stale}）")
    else:
        check(False, f"找不到扫描目录 {pk.DEAD_WEB_SCAN_DIR}（规则失效了）")

    print("\n=== [4] verify_zip 能抓住「包不对」 ===")
    scratch = tempfile.mkdtemp(prefix="pk_test_")
    try:
        # 4a 缺必需文件
        z1 = _mk_zip(os.path.join(scratch, "a.zip"), {"T/main.py": b"x"})
        b1 = pk.verify_zip(z1)
        check(any("缺必需文件" in x for x in b1), "缺必需文件被拦住")

        # 4b 混入红线文件
        z2 = _mk_zip(os.path.join(scratch, "b.zip"), {
            "T/main.py": b"x",
            "T/config.yaml": b"api_key: sk-real",
        })
        b2 = pk.verify_zip(z2)
        check(any("config.yaml" in x for x in b2), "包内混入 config.yaml 被拦住")

        # 4c 混入 data/ 目录
        z3 = _mk_zip(os.path.join(scratch, "c.zip"), {
            "T/main.py": b"x",
            "T/data/memory.json": b"{}",
        })
        b3 = pk.verify_zip(z3)
        check(any("data/" in x for x in b3), "包内混入 data/ 被拦住")

        # 4d 一个干净的 zip：只放必需文件的最小集合（不含内容，只验结构）
        entries = {"T/" + r: b"stub" for r in pk.REQUIRED}
        z4 = _mk_zip(os.path.join(scratch, "d.zip"), entries)
        b4 = [x for x in pk.verify_zip(z4) if "缺必需文件" in x or "混入" in x]
        check(not b4, "结构完整的 zip 不再报结构问题")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    print("\n=== [5] 随包说明写清楚「怎么跑」 ===")
    notice = pk._notice("9.9.9", 123)
    for token, why in [
        ("pip install -r requirements.txt", "Python 依赖"),
        ("npm install", "壳依赖（node_modules 未随包）"),
        ("copy config.release.yaml config.yaml", "配置从模板复制"),
        ("python main.py --doctor", "自检入口"),
        ("python tools/check_all.py", "门禁入口"),
        ("v9.9.9", "版本号来自 src/version.py"),
        ("123 个文件", "条目数与包内容一致"),
    ]:
        check(token in notice, f"说明含 {why}：{token}")

    print()
    if _fails:
        print(f"❌ {len(_fails)} 项未通过：")
        for m in _fails:
            print("   - " + m)
        return 1
    print("✅ 打包脚本排除规则测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
