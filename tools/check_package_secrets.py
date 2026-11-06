# -*- coding: utf-8 -*-
"""打包前硬闸：拦住任何会被带进交付包的明文凭据。

为什么需要它
------------
2026-10-17 的审计里发现：`config.release.yaml` 的 32 个 api_key 中
31 个手工置空、**漏了 1 个**（sticker.api_key，智谱 GLM）。根因不是疏忽，
而是"复制实配 → 逐行手改"这种脱敏方式本身不可靠：全文和 config.yaml
只差 1 行，任何一次手改都可能漏项。

所以把"检查"从人的注意力里拿出来，变成打包流程里一个会 exit(1) 的进程。

用法
----
    # 1) 检查源码目录里"必然入包"的模板文件（默认行为）
    python tools/check_package_secrets.py

    # 2) 检查一个已经准备好的待打包目录（会同时看包内是否混入隐私/环境目录，
    #    并校验包内 HTML 引用的本地资源是否齐全；--no-assets 可跳过后者）
    python tools/check_package_secrets.py --dir dist

    # 3) 连 config.yaml（实配，本就不该入包）也当失败处理
    python tools/check_package_secrets.py --strict

退出码：0 = 可打包；1 = 有阻断项。
"""
from __future__ import annotations

import argparse
import os
import re
import sys

# 键名精确匹配（避免 max_new_tokens / tokenizer 之类误报）
SENSITIVE_KEY_RE = re.compile(
    r"^(api_key|apikey|access_token|refresh_token|app_secret|appsecret|"
    r"secret|cookie|cookies|password|passwd|bot_token|webhook_secret)$",
    re.I,
)

# 【2026-10-18】`${ENV_VAR}`：环境变量引用（src/config.py 的 _expand_env_refs），
# 值是变量名而不是密钥，扫它等于把"没落盘"误判成"泄漏了"。
ENV_REF_RE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$")

# 视为"空"的占位值
EMPTY_VALUES = {"", "''", '""', "null", "~", "none", "None", "<your-key>", "your-key"}

# 必须保持全空的发布模板（进包的候选）
TEMPLATE_FILES = ["config.release.yaml", "config.example.yaml", "config.template.yaml"]

# 实配文件：默认只告警；--strict 下阻断
INSTANCE_FILES = ["config.yaml", "config.local.yaml"]

# 不该出现在待打包目录里的东西
FORBIDDEN_DIRS = [".venv", "venv", ".git", "__pycache__", "data", "logs", "build", "ort_dml"]
# 目录名匹配的最大深度：只抓包根附近，避免把内置运行时里的同名目录误判
MAX_FORBIDDEN_DEPTH = 2
FORBIDDEN_FILES = ["call_records.json", "user_knowledge.yaml", "memory.json", "memory_profile.jsonl"]


def _iter_rows(path: str):
    """逐行产出 (行号, 键, 原始值文本)。跳过注释与空行，不做 YAML 解析。"""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for no, line in enumerate(fh, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("- "):
                # 列表项形如 "- api_key: xxx" 也要覆盖
                if not stripped.startswith("- "):
                    continue
                stripped = stripped[2:].strip()
            if ":" not in stripped:
                continue
            key, _, value = stripped.partition(":")
            yield no, key.strip(), value.strip()


def scan_config(path: str) -> list[tuple[int, str]]:
    """返回该配置文件中所有『非空凭据』的 (行号, 键)。

    【2026-10-18】`${ENV_VAR}` 引用不算凭据：它是"密钥只存环境变量、不落盘"
    约定（见 src/config.py 的 _expand_env_refs）的落地形式，模板里写它是为了让
    用户少抄一遍 key，文件本身不含任何机密。不排除它，发布模板就只能永远写空
    key，用户反而要把 key 手抄进配置——那才正是要防的。
    """
    hits: list[tuple[int, str]] = []
    for no, key, value in _iter_rows(path):
        if not SENSITIVE_KEY_RE.match(key):
            continue
        # 去掉行尾注释后判断
        raw = value.split(" #", 1)[0].strip().strip("'\"")
        if raw in EMPTY_VALUES:
            continue
        # `dpapi:` 是 secret_box 的密文前缀（DPAPI 绑定本机，别处解不开），
        # 扫它等于把"已加密"误判成"泄漏了"——2026-10-18 实测 sticker.api_key
        # 就这样每次打包都报一次假警，噪音会把真警淹掉。
        if raw.startswith("dpapi:"):
            continue
        if ENV_REF_RE.fullmatch(raw):
            continue
        hits.append((no, key))
    return hits


def scan_dir(root: str) -> tuple[list[str], list[str]]:
    """检查待打包目录：返回 (阻断项, 告警项)。

    目录名匹配只看到深度 MAX_FORBIDDEN_DEPTH 以内：真正的隐私风险是包根下的
    `data/`、`logs/`、`.venv/`；而随包附带的 Python 运行时里天然存在
    `python/Lib/venv`、`pip/_internal/operations/build` 这类同名目录，
    按全树匹配会把正常交付包误判为不合格（2026-10-17 实测撞到）。
    """
    blocking: list[str] = []
    warnings: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        depth = 0 if rel_dir == "." else rel_dir.count(os.sep) + 1
        for d in list(dirnames):
            if d in FORBIDDEN_DIRS and depth < MAX_FORBIDDEN_DEPTH:
                blocking.append(f"包内混入目录：{os.path.join(rel_dir, d)}")
                dirnames.remove(d)  # 不再深入
        for f in filenames:
            rel = os.path.join(rel_dir, f)
            if f in FORBIDDEN_FILES:
                blocking.append(f"包内混入隐私/实例文件：{rel}")
            if f.lower().endswith((".yaml", ".yml", ".json")) and "config" in f.lower():
                for no, key in scan_config(os.path.join(root, rel)):
                    blocking.append(f"{rel}:{no} 凭据非空（{key}）")
            if f.lower().endswith((".log", ".wav", ".mp3", ".m4a", ".ogg", ".aac", ".flac")) \
                    and "chat_audio" in rel.lower():
                # 【2026-11-06 升级为阻断】原来是 WARN，重出包实测 3 个用户通话
                # 录音被 --force 残留进 stage，闸门却判「✓ 通过」，差点随 public
                # 仓库的安装包公开发布。用户语音不是「提示一下就行」的东西。
                blocking.append(f"包内含通话录音（用户语音，严禁发布）：{rel}")
    return blocking, warnings


def main() -> int:
    ap = argparse.ArgumentParser(description="打包前凭据硬闸")
    ap.add_argument("--dir", help="检查一个待打包目录（不给则检查当前目录的模板文件）")
    ap.add_argument("--strict", action="store_true", help="把 config.yaml 等实配文件也视为阻断")
    ap.add_argument("--no-assets", action="store_true", help="跳过包内 HTML 静态资源完整性检查")
    ap.add_argument("--cwd", default=".", help="项目根目录")
    args = ap.parse_args()

    root = os.path.abspath(args.cwd)
    blocking: list[str] = []
    warnings: list[str] = []

    if args.dir:
        b, w = scan_dir(os.path.abspath(args.dir))
        blocking += b
        warnings += w
        # 「不该带的」查完了，再查「必须带的」：包内 HTML 引用的本地资源是否齐全。
        # 2026-10-17 的教训：live2d/rig 曾是 gitlink 且无 .gitmodules，重新 clone
        # 或打包后目录为空，看板娘静默消失，日志里只有一条 404。
        if not args.no_assets:
            try:
                from check_web_assets import scan_dir as scan_html_assets
                blocking += scan_html_assets(os.path.abspath(args.dir), relative_only=True)
            except ImportError:
                warnings.append("找不到 check_web_assets.py，已跳过静态资源完整性检查")
    else:
        # 模板文件：必须全空
        for name in TEMPLATE_FILES:
            path = os.path.join(root, name)
            if not os.path.exists(path):
                continue
            hits = scan_config(path)
            for no, key in hits:
                blocking.append(f"{name}:{no} 模板中出现非空凭据（{key}）——发布模板必须全空")
            print(f"[扫描] {name}: {'OK' if not hits else f'{len(hits)} 处泄漏'}")
        # 实配文件：默认告警
        for name in INSTANCE_FILES:
            path = os.path.join(root, name)
            if not os.path.exists(path):
                continue
            hits = scan_config(path)
            if hits:
                msg = f"{name} 含 {len(hits)} 处明文凭据（第 {', '.join(str(n) for n, _ in hits[:5])} 行…）——此文件严禁入包"
                (blocking if args.strict else warnings).append(msg)

    for w in warnings:
        print(f"[告警] {w}")
    for b in blocking:
        print(f"[阻断] {b}", file=sys.stderr)

    if blocking:
        print(f"\n✗ 打包已阻断：{len(blocking)} 项。先把上面的凭据清空/文件移出再重试。", file=sys.stderr)
        return 1
    print("\n✓ 通过：无明文凭据泄漏，且包内静态资源齐全。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
