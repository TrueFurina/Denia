# -*- coding: utf-8 -*-
"""「陌生人装完能不能起来」的判据链（阶段 1）。

一句话：把 2026-11-04 那份「六步验包」里靠人记的部分，变成一条能无人值守、
自带变异刀、且**如实标注自己没验什么**的命令。

用法：
    python tools/verify_first_run.py                     # 默认 git ls-files 造干净副本（门禁用）
    python tools/verify_first_run.py --dir <解包目录>    # 验真实交付产物
    python tools/verify_first_run.py --report <path>     # 指定报告落盘位置

它验的是「一个没装过 Python、没配过 Key 的人，解压后第一次跑 --doctor」：
  ① 干净副本（不得有 data/ logs/ .venv/ config.yaml 等开发机残留）
  ② 从出厂模板起一份 config.yaml，且模板里没有开发机盘符/UNC/真实 Key
  ③ 环境剥干净（PYTHON*、VIRTUAL_ENV、ELECTRON_RUN_AS_NODE、所有 Key）
     —— 用注入哨兵来证明「真的剥了」，不依赖当前机器恰好有没有 Key
  ④ 用副本内运行时跑 main.py --doctor，汇总行必须 0 FAIL、无 Traceback
  ⑤ 副本内再跑首启浮层与配置兜底两条既有判据

**诚实边界（本链最重要的部分）**：以下项目前无法无人值守验证，报告里一律标
`requires_human`，绝不计入 PASS：首屏是否真的可见、白底/边缘糊、点击热区、
150% 缩放、双屏坐标、休眠唤醒、第二台干净电脑。删掉这份清单会让链判红
（防止「把没验的写成验过」）。

【为什么必须先在副本里检查干净】main.py 的导入期副作用会在包根造出
data/ 与 logs/（make_dist.verify_deps 的 docstring 已记过这个事故）。
所以「减法检查」必须排在跑 doctor **之前**，否则验收动作自己把自己的判据弄脏。

登记方式：由 check_all.GATES 直接登记（本文件不是 check_*/test_* 命名）。
"""
from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable

FORBIDDEN_DIRS = ("data", "logs", ".venv", "venv", ".git", "__pycache__", "ort_dml")
# config.yaml 不在这里：安装版按设计会把 config.release.yaml 改名成 config.yaml
# 一起打包（make_dist.RENAME）。「见到就判红」会误伤合法产物，改由
# check_factory_config() 判「它是不是出厂模板」，而不是二态的「有没有」。
FORBIDDEN_FILES = ("call_records.json", "user_knowledge.yaml",
                   "memory.json", "memory_profile.jsonl")
REQUIRED_FILES = ("main.py", "tools/doctor.py", "config.release.yaml",
                  "src/version.py", "requirements.txt")
SCRUB_MARKERS = ("API_KEY", "APIKEY", "TOKEN", "SECRET", "ACCESS_KEY",
                 "AUTH", "CREDENTIAL", "PASSWORD")
SENTINELS = {
    "OPENAI_API_KEY": "SENTINEL_NOT_A_REAL_KEY",
    "DASHSCOPE_API_KEY": "SENTINEL_NOT_A_REAL_KEY",
    "ANTHROPIC_API_KEY": "SENTINEL_NOT_A_REAL_KEY",
    "DEEPSEEK_API_KEY": "SENTINEL_NOT_A_REAL_KEY",
}
# 必须人眼的项（删掉这份清单 = 假装验过 = 判红）
HUMAN_ONLY = [
    "首屏是否真的可见（桌面窗口/Live2D 渲染，不是 doctor 的文字输出）",
    "白底 / 透明镂空闪烁 / 边缘糊（150% 缩放、双屏下更明显）",
    "点击热区与托盘右键菜单",
    "休眠唤醒后心跳能否恢复（两阶段 slow 带是否按预期只告警）",
    "第二台干净电脑、全程不指导、按官网下载→双击→首启→第一句话",
]
DRIVE_RE = re.compile(r"(?:^[A-Za-z]:[\\/])|(?:^\\\\[^\\]+\\)")
SECRET_RE = re.compile(r"(?:sk-[A-Za-z0-9]{16,})|(?:^[0-9a-f]{40,}$)", re.I)
SUMMARY_RE = re.compile(r"汇总：\s*(\d+)\s*PASS\s*/\s*(\d+)\s*WARN\s*/\s*(\d+)\s*FAIL")


def rel(*parts: str) -> str:
    return os.path.join(*parts).replace("\\", "/")


class Chain:
    def __init__(self) -> None:
        self.checks: list[dict] = []
        self.meta: dict = {}

    def chk(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:300]})
        print("  %s %s%s" % ("PASS" if ok else "FAIL", name,
                             ("  <- " + str(detail)[:160]) if detail else ""))
        return bool(ok)

    @property
    def ok(self) -> bool:
        return all(c["ok"] for c in self.checks)


def git_files() -> list[str]:
    p = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError("git ls-files 失败：%s" % (p.stderr or "")[:200])
    return [x for x in p.stdout.splitlines() if x.strip()]


def make_copy(src: str | None, scratch: str) -> str:
    """绝不验原件：一律复制到临时副本，验收动作弄脏也只脏副本。"""
    dst = os.path.join(scratch, "clean")
    os.makedirs(dst, exist_ok=True)
    if src is None:
        for r in git_files():
            s = os.path.join(ROOT, r)
            d = os.path.join(dst, r)
            if not os.path.isfile(s):
                continue
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(s, d)
    else:
        shutil.copytree(src, dst, dirs_exist_ok=True)
    return dst


def _sha(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with io.open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_factory_config(c: Chain, ws: str) -> None:
    """config.yaml 允许存在，但必须逐字节等于出厂模板。

    安装版 make_dist.RENAME 把 config.release.yaml 改名成 config.yaml 打包，
    这是**合法**的出厂配置；开发机实配（带 Key/盘符）才是残留。用「有没有」
    二态判会误伤安装版，所以这里比 sha，并在模板缺席时给出明确适用说明。
    """
    tpl = os.path.join(ws, "config.release.yaml")
    cfg = os.path.join(ws, "config.yaml")
    if not os.path.isfile(cfg):
        c.chk("副本无现成 config.yaml（首启时由模板生成）", True)
        return
    if not os.path.isfile(tpl):
        c.chk("现成 config.yaml 有出厂模板可比对", False,
              "包内缺 config.release.yaml；含包内运行时的 stage 属内部构建产物，"
              "本链只验源码包/干净 clone，安装版请用 make_dist.verify_deps")
        return
    same = _sha(tpl) == _sha(cfg)
    c.chk("现成 config.yaml 逐字节等于出厂模板（不是开发机实配）", same,
          "sha 不同：可能混进了开发机 config.yaml")


def check_clean(c: Chain, ws: str) -> None:
    """减法检查必须在跑 doctor 之前（doctor 会在副本里造 logs/）。"""
    found_dirs = [d for d in FORBIDDEN_DIRS
                  if os.path.isdir(os.path.join(ws, d))]
    found_files = [f for f in FORBIDDEN_FILES
                   if os.path.isfile(os.path.join(ws, f))]
    c.chk("副本无开发机/隐私目录残留", not found_dirs, ",".join(found_dirs))
    c.chk("副本无开发机/隐私文件残留", not found_files, ",".join(found_files))
    check_factory_config(c, ws)
    bundled = os.path.isfile(os.path.join(ws, "python", "python.exe"))
    c.chk("本链适用范围（源码包/干净 clone，非安装版 stage）", not bundled,
          "检测到包内运行时：安装版由 make_dist.verify_deps 覆盖" if bundled else "")
    missing = [f for f in REQUIRED_FILES
               if not os.path.isfile(os.path.join(ws, *f.split("/")))]
    c.chk("运行必需文件齐全", not missing, ",".join(missing))


def _walk_strings(node, out: list[str]) -> None:
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for v in node.values():
            _walk_strings(v, out)
    elif isinstance(node, (list, tuple)):
        for v in node:
            _walk_strings(v, out)


def check_template(c: Chain, ws: str) -> None:
    tpl = os.path.join(ws, "config.release.yaml")
    cfg = os.path.join(ws, "config.yaml")
    if not os.path.isfile(tpl):
        c.chk("出厂模板存在", False, "缺 config.release.yaml")
        return
    c.chk("出厂模板存在", True)
    try:
        import yaml
        with io.open(tpl, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as exc:
        c.chk("模板可解析", False, str(exc))
        return
    c.chk("模板可解析", True)
    vals: list[str] = []
    _walk_strings(data, vals)
    # 只看解析后的值：注释里写盘符不算数（tokenize/yaml 解析会丢注释）
    drives = [v for v in vals if DRIVE_RE.search(v)]
    secrets = [v[:24] for v in vals if SECRET_RE.search(v)]
    c.chk("出厂模板无开发机盘符/UNC", not drives, ",".join(drives[:3]))
    c.chk("出厂模板无真实 Key", not secrets, ",".join(secrets[:3]))
    shutil.copy2(tpl, cfg)
    c.chk("已从模板起一份出厂 config.yaml", os.path.isfile(cfg))


def build_env(c: Chain) -> dict:
    raw = dict(os.environ)
    raw.update(SENTINELS)          # 无论当前机器有没有 Key，都先放哨兵
    env = {k: v for k, v in raw.items()
           if not k.upper().startswith("PYTHON") and k.upper() != "VIRTUAL_ENV"}
    for k in list(env):
        ku = k.upper()
        if any(m in ku for m in SCRUB_MARKERS):
            env.pop(k, None)
    env.pop("ELECTRON_RUN_AS_NODE", None)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    leaked = [k for k in SENTINELS if k in env]
    c.chk("所有 Key 哨兵已被剥掉（不依赖本机有没有 Key）", not leaked, ",".join(leaked))
    bad = [k for k in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV",
                       "ELECTRON_RUN_AS_NODE") if k in env]
    c.chk("无 PYTHONPATH/HOME/VIRTUAL_ENV/ELECTRON_RUN_AS_NODE", not bad, ",".join(bad))
    c.chk("PYTHONNOUSERSITE=1 已设置", env.get("PYTHONNOUSERSITE") == "1")
    c.meta["scrubbed_keys"] = sorted(SENTINELS)
    return env


def pick_python(c: Chain, ws: str) -> str:
    bundled = os.path.join(ws, "python", "python.exe")
    if os.path.isfile(bundled):
        c.meta["shape"] = "installer"
        py = bundled
    else:
        c.meta["shape"] = "source"
        c.meta["caveat"] = "源码包形态：未使用包内运行时，不能代表安装版"
        py = PY
    c.chk("解释器存在（%s）" % c.meta["shape"], os.path.isfile(py), py)
    c.meta["python"] = py
    return py


def run_doctor(c: Chain, ws: str, py: str, env: dict) -> None:
    try:
        p = subprocess.run([py, "main.py", "--doctor"], cwd=ws, env=env,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180)
    except Exception as exc:
        c.chk("main.py --doctor 可运行", False, repr(exc))
        return
    out = (p.stdout or "") + (p.stderr or "")
    c.chk("main.py --doctor 退出码 0", p.returncode == 0, "rc=%d" % p.returncode)
    m = SUMMARY_RE.search(out)
    c.chk("doctor 有汇总行", m is not None, out.strip().splitlines()[-1:] or "")
    if m:
        npass, nwarn, nfail = (int(x) for x in m.groups())
        c.meta["doctor"] = {"pass": npass, "warn": nwarn, "fail": nfail}
        c.chk("doctor 汇总 FAIL == 0", nfail == 0,
              "%d PASS / %d WARN / %d FAIL" % (npass, nwarn, nfail))
    c.chk("doctor 输出无 [FAIL] 行", "[FAIL]" not in out)
    c.chk("doctor 输出无 Traceback / 自检加载失败",
          "Traceback" not in out and "自检脚本加载失败" not in out)
    c.meta["doctor_tail"] = out.strip().splitlines()[-6:]


def run_wiring(c: Chain, ws: str, env: dict) -> None:
    """首启不问 Key 的两条既有判据，在**副本内**跑（不是仓库根）。"""
    cases = [
        ("src/tests/test_setup_hint.py", "全部通过"),
        ("tools/test_config_bootstrap.py", "0 FAIL"),
    ]
    for script, marker in cases:
        path = os.path.join(ws, *script.split("/"))
        if not os.path.isfile(path):
            c.chk("%s 存在于副本" % script, False)
            continue
        try:
            p = subprocess.run([PY, "-u", script], cwd=ws, env=env,
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=120)
        except Exception as exc:
            c.chk("%s 可运行" % script, False, repr(exc))
            continue
        out = (p.stdout or "") + (p.stderr or "")
        # 同时断言 rc 和可观测的成功文案，防「脚本没跑起来也算过」
        c.chk("%s 通过" % script, p.returncode == 0 and marker in out,
              "rc=%d marker=%s" % (p.returncode, marker in out))


def write_report(c: Chain, path: str, src: str | None) -> None:
    report = {
        "schema_version": 1,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "input": src or "git ls-files (干净副本)",
        "meta": c.meta,
        "checks": c.checks,
        "passed": sum(1 for x in c.checks if x["ok"]),
        "failed": sum(1 for x in c.checks if not x["ok"]),
        "requires_human": list(HUMAN_ONLY),
        "note": "requires_human 里的项未验，不计入 passed；本链只证明「陌生人第一次跑 --doctor 起得来」。",
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print("\n报告：%s" % path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", help="干净解包目录（验真实产物时用）")
    ap.add_argument("--report", default="logs/first_run_report.json")
    args = ap.parse_args()

    c = Chain()
    scratch = tempfile.mkdtemp(prefix="denia_first_run_")
    try:
        print("1) 造干净副本（验收只碰副本，不碰原件）")
        try:
            ws = make_copy(args.dir, scratch)
            c.chk("副本建立成功", os.path.isdir(ws), ws)
        except Exception as exc:
            c.chk("副本建立成功", False, repr(exc))
            write_report(c, args.report, args.dir)
            return 1
        c.meta["copy_files"] = sum(len(f) for _, _, f in os.walk(ws))

        print("\n2) 减法检查（必须排在跑 doctor 之前）")
        check_clean(c, ws)

        print("\n3) 出厂状态")
        check_template(c, ws)

        print("\n4) 环境隔离（哨兵法）")
        env = build_env(c)

        print("\n5) 解释器")
        py = pick_python(c, ws)

        print("\n6) 真的跑一次 main.py --doctor")
        run_doctor(c, ws, py, env)

        print("\n7) 首启接线判据（副本内）")
        run_wiring(c, ws, env)

        print("\n8) 诚实边界")
        c.chk("报告保留 requires_human 清单（不许把没验的写成验过）",
              len(HUMAN_ONLY) >= 3, "共 %d 项" % len(HUMAN_ONLY))

        write_report(c, args.report, args.dir)
        print("\n结果: %d PASS / %d FAIL" % (
            sum(1 for x in c.checks if x["ok"]),
            sum(1 for x in c.checks if not x["ok"])))
        return 0 if c.ok else 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
