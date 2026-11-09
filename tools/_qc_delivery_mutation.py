# -*- coding: utf-8 -*-
"""`tools/check_delivery.py` 的变异验证：证明它不是恒绿。

为什么需要：这道闸门连着几轮都出现「看着绿、其实什么都没查」——清单漏项、
`--root` 验错包、只判 `isfile` 被 0 字节文件骗过。每加一层判据都必须配一把
「故意弄坏」的刀，否则无法区分「真的没缺陷」和「判据根本没生效」。

做法：全部在临时目录里造「最小交付包」，不碰工作树里的任何文件。
  · A–X 走进程内直调 CHECKS；
  · R/S/AC 走真实命令行（子进程），验 `--root` 与 `--smoke` 的接线；
  · AD/AE/AF 用替身 `subprocess.run` 只验判据逻辑（避免无交互环境弹错误框）。

用法：python tools/_qc_delivery_mutation.py    # 退出码 0 = 全部符合预期
本脚本自建夹具，在源码仓里也能跑（config.yaml 不入库，会从模板派生一份）。
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

REAL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(REAL_ROOT, "tools", "check_delivery.py")

# 最小包里放一份「行为正确」的自愈实现。第九轮起闸门验的是目标包这份文件，
# 而不是脚本所在包的 src.config —— 所以占位文件不能再是空文件。
FAITHFUL_CONFIG_PY = (
    "import os\n"
    "CONFIG_TEMPLATE_NAMES = (\"config.release.yaml\", \"config.example.yaml\", \"config.template.yaml\")\n"
    "def ensure_config_file(path):\n"
    "    if os.path.exists(path):\n"
    "        return (\"exists\", path)\n"
    "    base = os.path.dirname(os.path.abspath(path)) or \".\"\n"
    "    for name in CONFIG_TEMPLATE_NAMES:\n"
    "        tpl = os.path.join(base, name)\n"
    "        if os.path.exists(tpl):\n"
    "            import shutil\n"
    "            shutil.copyfile(tpl, path)\n"
    "            return (\"created\", tpl)\n"
    "    return (\"missing\", path)\n"
)
spec = importlib.util.spec_from_file_location(
    "check_delivery_under_test", os.path.join(REAL_ROOT, "tools", "check_delivery.py"))
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)


def stub_bytes(rel: str) -> bytes:
    """按真实产物格式造占位，让「正常态」能过第十轮的文件实体检查。"""
    if rel.endswith((".exe", ".dll")):
        return b"MZ" + b"\x00" * 126
    if rel.endswith(".html"):
        return b"<!DOCTYPE html><html><head></head><body>stub</body></html>" + b" " * 64
    if rel.endswith(".wav"):
        return b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * 64
    if rel.endswith((".js", ".txt")):
        return b"// stub\n" + b"x" * 64
    return b"x" * 128


def make_root(td: str) -> str:
    """造一份最小但结构完整的交付包：必备文件占位 + 资源树占位 + 两份真实配置副本。"""
    root = os.path.join(td, "pkg")
    os.makedirs(root)
    for rel in cd.REQUIRED_FILES:
        p = os.path.join(root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if rel == "config.yaml" and not os.path.isfile(os.path.join(REAL_ROOT, rel)):
            # 源码仓里 config.yaml 是 gitignore 的实配。用模板派生一份「跑过一次」
            # 的等价物：唯一差别就是 force_first_run，check_template 正是比对这两点。
            tpl = open(os.path.join(REAL_ROOT, "config.release.yaml"),
                       encoding="utf-8").read()
            with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(tpl.replace("force_first_run: true", "force_first_run: false", 1))
        elif rel in ("config.yaml", "config.release.yaml", "requirements.txt"):
            shutil.copyfile(os.path.join(REAL_ROOT, rel), p)
        elif rel == "src/config.py":
            with io.open(p, "w", encoding="utf-8") as fh:
                fh.write(FAITHFUL_CONFIG_PY)
        else:
            with open(p, "wb") as fh:
                fh.write(stub_bytes(rel))
    # 资源子树按各自下限补足占位文件，让「正常态」能全绿
    for rel, minimum, _why in cd.REQUIRED_DIRS:
        d = os.path.join(root, rel.replace("/", os.sep))
        os.makedirs(d, exist_ok=True)
        for i in range(minimum):
            with open(os.path.join(d, "_stub_%d.dat" % i), "wb") as fh:
                fh.write(b"")
    # 第十一轮：内置运行时里要有核心/重要依赖的 dist-info，否则「依赖完整」会 FAIL。
    # 只造 dist-info 不造模块 —— dist-info 是 pip 安装的权威痕迹，够用。
    sp = os.path.join(root, cd.BUNDLED_SITE_PACKAGES.replace("/", os.sep))
    os.makedirs(sp, exist_ok=True)
    secs = cd._parse_requirement_sections(os.path.join(root, "requirements.txt"))
    for dist in (secs.get("核心") or []) + (secs.get("重要") or []):
        d = os.path.join(sp, "%s-0.0.0.dist-info" % dist)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "METADATA"), "wb") as fh:
            fh.write(b"Metadata-Version: 2.1\nName: " + dist.encode() + b"\n")
    return root


def run(root: str) -> dict:
    """返回 {检查项: 等级}。"""
    return {fn.__name__: fn(root)[0] for fn in cd.CHECKS}


def fails(root: str) -> list:
    return [k for k, v in run(root).items() if v == "FAIL"]


results = []


def case(name, mutate, expect_items, expect_no_fail=False, expect_warn=()):
    with tempfile.TemporaryDirectory(prefix="qc_del_mut_") as td:
        root = make_root(td)
        mutate(root)
        lv = run(root)
        f = [k for k, v in lv.items() if v == "FAIL"]
        if expect_no_fail:
            # 除「不许有 FAIL」外，还可要求某些项**恰好**是 WARN（降级而非放行）
            bad_warn = [x for x in expect_warn if lv.get(x) != "WARN"]
            ok = len(f) == 0 and not bad_warn
            if f:
                detail = f"意外 FAIL: {f}"
            elif bad_warn:
                detail = f"期望 WARN 实际 {[ (x, lv.get(x)) for x in bad_warn ]}"
            else:
                detail = "0 FAIL" + (f"，{len(expect_warn)} 项按预期 WARN" if expect_warn else "")
        else:
            ok = len(f) > 0 and all(any(x in it for it in f) for x in expect_items)
            detail = f"FAIL 项={f}"
        print(f"  {'OK ' if ok else 'BAD'} {name:34s} {detail}")
        results.append((name, ok))


print("=== 变异验证：tools/check_delivery.py ===")

case("A 正常态（必须全绿）", lambda r: None, [], expect_no_fail=True)

case("B 漏发 config.release.yaml", lambda r: os.remove(os.path.join(r, "config.release.yaml")),
     ["check_required_files", "check_template", "check_template_secrets", "check_self_heal"])


def leak_key(r):
    p = os.path.join(r, "config.release.yaml")
    s = io.open(p, encoding="utf-8").read()
    s = s.replace("api_key: ''", "api_key: 'sk-REAL-SECRET-123456'", 1)
    io.open(p, "w", encoding="utf-8").write(s)


case("C 模板夹带明文密钥", leak_key, ["check_template_secrets"])


def flip_first_run(r):
    p = os.path.join(r, "config.release.yaml")
    s = io.open(p, encoding="utf-8").read().replace("force_first_run: true", "force_first_run: false", 1)
    io.open(p, "w", encoding="utf-8").write(s)


case("D 模板不是出厂态", flip_first_run, ["check_template"])


def bump_version(r):
    p = os.path.join(r, "config.release.yaml")
    s = io.open(p, encoding="utf-8").read().replace("version: 0.2.0", "version: 9.9.9", 1)
    io.open(p, "w", encoding="utf-8").write(s)


case("E 模板版本与实配不一致", bump_version, ["check_template"])

case("F 漏发 config.yaml", lambda r: os.remove(os.path.join(r, "config.yaml")),
     ["check_required_files"])

case("G 漏发内置 Electron",
     lambda r: os.remove(os.path.join(r, "shell", "node_modules", "electron", "dist", "electron.exe")),
     ["check_required_files"])


def env_ref_only(r):
    """源码仓式 ${VAR} 模板不该被判成泄密（防止误报）。"""
    p = os.path.join(r, "config.release.yaml")
    s = io.open(p, encoding="utf-8").read().replace("api_key: ''", "api_key: '${ZHIPU_API_KEY}'", 1)
    io.open(p, "w", encoding="utf-8").write(s)


case("H ${VAR} 引用不误报", env_ref_only, [], expect_no_fail=True)


# ---- 第八轮新增：资源目录 / 源码闭包的变异刀 ----

def drop_web_tree(r):
    shutil.rmtree(os.path.join(r, "src", "ui", "web"), ignore_errors=True)


case("J 整棵 Web UI 漏发", drop_web_tree,
     ["check_required_files", "check_required_dirs"])


def thin_web_tree(r):
    """入口页都在，但 120 个页面资源只剩 4 个——平铺清单查不出，目录下限要拦住。"""
    d = os.path.join(r, "src", "ui", "web")
    for f in os.listdir(d):
        if f.startswith("_stub_"):
            os.remove(os.path.join(d, f))


case("K Web UI 只剩入口页", thin_web_tree, ["check_required_dirs"])


def ghost_module(r):
    with io.open(os.path.join(r, "src", "_ghost_probe.py"), "w", encoding="utf-8") as fh:
        fh.write("from src.ui.never_shipped import x\n")


case("L 新增未打包源码模块", ghost_module, ["check_source_closure"])


def known_missing_ref(r):
    """白名单里的已知缺件只能 WARN，不能把整道闸门染红。"""
    with io.open(os.path.join(r, "src", "_known_probe.py"), "w", encoding="utf-8") as fh:
        fh.write("from src.ui.quota_guard import build_quota_report\n")


case("M 已知缺件只 WARN 不 FAIL", known_missing_ref, [], expect_no_fail=True)


def broken_syntax(r):
    with io.open(os.path.join(r, "src", "_broken.py"), "w", encoding="utf-8") as fh:
        fh.write("def oops(:\n")


case("N 源码语法损坏", broken_syntax, ["check_source_closure"])


def drop_site_packages(r):
    shutil.rmtree(os.path.join(r, "python", "Lib", "site-packages"), ignore_errors=True)


case("O 内置依赖树漏发", drop_site_packages,
     ["check_required_files", "check_required_dirs"])


def break_target_heal(r):
    """第九轮回归刀：改坏**目标包自己**的 src/config.py（旧实现会拿脚本所在包顶替 → 假绿）。"""
    with io.open(os.path.join(r, "src", "config.py"), "w", encoding="utf-8") as fh:
        fh.write("import os\nCONFIG_TEMPLATE_NAMES = (\"config.release.yaml\",)\n"
                 "def ensure_config_file(path):\n    return (\"missing\", path)\n")


case("I 目标包自愈失效（--root 假绿）", break_target_heal, ["check_self_heal"])


def remove_target_fn(r):
    with io.open(os.path.join(r, "src", "config.py"), "w", encoding="utf-8") as fh:
        fh.write("CONFIG_TEMPLATE_NAMES = (\"config.release.yaml\",)\n")


case("P 目标包缺 ensure_config_file", remove_target_fn, ["check_self_heal"])


def remove_target_const(r):
    with io.open(os.path.join(r, "src", "config.py"), "w", encoding="utf-8") as fh:
        fh.write("def ensure_config_file(path):\n    return (\"created\", path)\n")


case("Q 目标包模板常量不可解析", remove_target_const, ["check_self_heal"])


# ---- 第十轮：文件「在但坏了」的变异刀 ----

def empty_index(r):
    open(os.path.join(r, "src", "ui", "web", "index.html"), "wb").close()


case("T 首页 index.html 被截成 0 字节", empty_index, ["check_file_integrity"])


def fake_pe(r):
    p = os.path.join(r, "shell", "node_modules", "electron", "dist", "electron.exe")
    with open(p, "wb") as fh:
        fh.write(b"NO" + b"\x00" * 126)   # 大小够，但不是 PE


case("U electron.exe 非 PE 头", fake_pe, ["check_file_integrity"])


def corrupt_config(r):
    with io.open(os.path.join(r, "config.yaml"), "w", encoding="utf-8") as fh:
        fh.write("just a plain string\n")   # YAML 合法但顶层不是映射


case("V config.yaml 顶层不是映射", corrupt_config, ["check_file_integrity"])


def empty_js(r):
    open(os.path.join(r, "shell", "preload.js"), "wb").close()


case("W preload.js 空文件", empty_js, ["check_file_integrity"])


def broken_wav(r):
    with open(os.path.join(r, "assets", "voices", "daniya_ref.wav"), "wb") as fh:
        fh.write(b"NOPE" + b"\x00" * 64)


case("X 参考音频非 RIFF/WAVE", broken_wav, ["check_file_integrity"])


# ---- 第十一轮：requirements.txt ↔ 内置运行时依赖（dist-info 为证据） ----

def _sp(r):
    return os.path.join(r, cd.BUNDLED_SITE_PACKAGES.replace("/", os.sep))


def _drop_dist(r, dist):
    for e in os.listdir(_sp(r)):
        if e.lower().startswith(dist.lower() + "-") and e.lower().endswith(".dist-info"):
            shutil.rmtree(os.path.join(_sp(r), e))


def drop_core_dep(r):
    """核心依赖漏装：numpy 的 dist-info 没了、也没有模块 → 必须 FAIL。"""
    _drop_dist(r, "numpy")


case("Y 核心依赖漏装", drop_core_dep, ["check_dependencies"])


def drop_important_dep(r):
    """重要档漏装只降级：pypinyin 缺了唤醒词退化，但能启动 → WARN，不许 FAIL。"""
    _drop_dist(r, "pypinyin")


case("Z 重要依赖漏装只 WARN", drop_important_dep, [], expect_no_fail=True,
     expect_warn=["check_dependencies"])


def no_core_section(r):
    """requirements.txt 丢了分节注释 → 无法核对，WARN 而不是假装绿。"""
    p = os.path.join(r, "requirements.txt")
    io.open(p, "w", encoding="utf-8").write("PyYAML>=6.0\nhttpx>=0.27\n")


case("AA requirements 无核心段", no_core_section, [], expect_no_fail=True,
     expect_warn=["check_dependencies"])


def module_without_meta(r):
    """模块在、dist-info 没了：能跑但元数据缺失 → WARN，不误判成 FAIL。"""
    _drop_dist(r, "rich")
    d = os.path.join(_sp(r), "rich")
    os.makedirs(d, exist_ok=True)
    io.open(os.path.join(d, "__init__.py"), "w", encoding="utf-8").write("# stub\n")


case("AB 核心依赖只有模块无元数据", module_without_meta, [], expect_no_fail=True,
     expect_warn=["check_dependencies"])


# ---- 第九轮：--root CLI 端到端（此前从未真正跑过命令行路径） ----

def cli(root, *extra):
    p = subprocess.run([sys.executable, GATE, "--root", root, "--json", *extra],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, encoding="utf-8", errors="replace")
    try:
        payload = json.loads(p.stdout)
    except Exception:  # noqa: BLE001
        payload = None
    return p.returncode, payload


with tempfile.TemporaryDirectory(prefix="qc_del_cli_") as td:
    root = make_root(td)
    rc, payload = cli(root)
    ok = rc == 0 and payload is not None and len(payload) == len(cd.CHECKS) \
        and all(x["level"] != "FAIL" for x in payload)
    print(f"  {'OK ' if ok else 'BAD'} {'R --root CLI 正常包':34s} rc={rc}, 检查项={len(payload) if payload else '解析失败'}")
    results.append(("R --root CLI 正常包", ok))

    break_target_heal(root)
    rc2, payload2 = cli(root)
    ok2 = rc2 == 1 and payload2 is not None \
        and any(x["item"] == "自愈三态" and x["level"] == "FAIL" for x in payload2)
    print(f"  {'OK ' if ok2 else 'BAD'} {'S --root CLI 坏包被拦':34s} rc={rc2}, "
          f"FAIL={[x['item'] for x in payload2 if x['level']=='FAIL'] if payload2 else '解析失败'}")
    results.append(("S --root CLI 坏包被拦", ok2))


# ---- 第十二轮：可选运行时冒烟（--smoke） ----

with tempfile.TemporaryDirectory(prefix="qc_del_smoke_") as td:
    root = make_root(td)
    # 最小包里的 python.exe 是占位（MZ+0），真执行必然起不来 → 冒烟必须 FAIL 且 rc=1
    rc3, payload3 = cli(root, "--smoke")
    ok3 = rc3 == 1 and payload3 is not None and any(
        x["item"] == "运行时冒烟" and x["level"] == "FAIL" for x in payload3)
    print(f"  {'OK ' if ok3 else 'BAD'} {'AC --smoke 拦住假解释器':34s} rc={rc3}, "
          f"FAIL={[x['item'] for x in payload3 if x['level']=='FAIL'] if payload3 else '解析失败'}")
    results.append(("AC --smoke 拦住假解释器", ok3))


class _FakeProc:
    def __init__(self, rc, out):
        self.returncode = rc
        self.stdout = out.encode("utf-8")


_SMOKE_ROOT = None


def _smoke_root():
    """AD/AE/AF 用的假包根：只需要存在 python/python.exe 这个文件。

    subprocess.run 已被替身，不会真执行。**不能用 REAL_ROOT**：源码仓里没有
    python/（.gitignore 掉了），check_runtime_smoke 会走「缺解释器」分支而测不到判据。
    """
    global _SMOKE_ROOT
    if _SMOKE_ROOT is None:
        d = tempfile.mkdtemp(prefix="qc_del_smoke_unit_")
        os.makedirs(os.path.join(d, "python"))
        open(os.path.join(d, "python", "python.exe"), "wb").close()
        _SMOKE_ROOT = d
    return _SMOKE_ROOT


def smoke_with(rc, out):
    """把 check_runtime_smoke 里的 subprocess.run 换掉，只验判据逻辑（不真起进程）。"""
    real = cd.subprocess.run
    cd.subprocess.run = lambda *a, **k: _FakeProc(rc, out)
    try:
        return cd.check_runtime_smoke(_smoke_root())
    finally:
        cd.subprocess.run = real


lv, _item, detail, _hint = smoke_with(0, "boom no marker")
ok4 = lv == "FAIL"
print(f"  {'OK ' if ok4 else 'BAD'} {'AD exit=0 但无成功标记':34s} {lv} | {detail[:60]}")
results.append(("AD exit=0 但无成功标记", ok4))

lv, _item, detail, _hint = smoke_with(0, "SMOKE_OK 3 12 2.5.4 6.0.3 0")
ok5 = lv == "PASS" and "isolated=0" in detail
print(f"  {'OK ' if ok5 else 'BAD'} {'AE isolated=0 只提示不判红':34s} {lv} | {detail[:70]}")
results.append(("AE isolated=0 只提示不判红", ok5))

lv, _item, detail, _hint = smoke_with(1, "ModuleNotFoundError: No module named 'numpy'")
ok6 = lv == "FAIL" and "ModuleNotFoundError" in detail
print(f"  {'OK ' if ok6 else 'BAD'} {'AF 导入失败带出原始报错':34s} {lv} | {detail[:60]}")
results.append(("AF 导入失败带出原始报错", ok6))

print("\n=== 断言 ===")
allok = all(ok for _, ok in results)
for name, ok in results:
    print(f"  {'PASS' if ok else 'FAIL'} {name}")
print("\nMUTATION_RESULT:", "ALL PASS" if allok else "FAILED")
sys.exit(0 if allok else 1)
