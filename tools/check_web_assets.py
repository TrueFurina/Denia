"""校验前端 HTML 引用的静态资源是否真实存在。

背景：`src/ui/web/index.html` 里的看板娘是
    <iframe src="live2d/rig/index.html?v=2">
而 `live2d/rig` 当时以 gitlink 记录、仓库又没有 .gitmodules —— 文件在开发机
上齐全，重新 clone 或打包后整个目录为空，看板娘静默消失，日志里只有一条 404。

本脚本把「HTML 引用的本地资源必须存在于磁盘」变成一条可执行断言。

用法：
  python tools/check_web_assets.py                # 默认扫 src/ui/web
  python tools/check_web_assets.py --dir src/ui/web
  python tools/check_web_assets.py --strict       # 有缺失即 exit 1
"""
from __future__ import annotations

import os
import re
import sys
from urllib.parse import unquote

SKIP_DIRS = {"node_modules", "__pycache__", ".git", "models"}
# 远程/内联/模板化资源不检查
EXTERNAL = ("http://", "https://", "//", "data:", "blob:", "javascript:", "mailto:", "#", "{{", "${")

_ATTR = re.compile(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", re.I)


def scan(html_path: str, webroot: str | None = None) -> list[str]:
    base = os.path.dirname(os.path.abspath(html_path))
    root = os.path.abspath(webroot) if webroot else base
    try:
        text = open(html_path, encoding="utf-8", errors="replace").read()
    except OSError:
        return []
    missing = []
    for raw in _ATTR.findall(text):
        u = raw.strip()
        if not u or u.lower().startswith(EXTERNAL):
            continue
        if u.startswith("?"):
            continue
        path = unquote(u.split("?")[0].split("#")[0])
        if not path:
            continue
        # 误报排除：
        #   $2 / $1 —— JS 里 replace() 替换串，不是真实路径
        #   带查询且末段无扩展名 —— 后端路由（如 /local_img?path=…），不是静态文件
        if "$" in path:
            continue
        last = path.rstrip("/").rsplit("/", 1)[-1]
        if "?" in u and "." not in last:
            continue
        # 以 / 开头 = 站点根相对路径，按 webroot 解析
        if path.startswith("/"):
            full = os.path.normpath(os.path.join(root, path.lstrip("/").replace("/", os.sep)))
        else:
            full = os.path.normpath(os.path.join(base, path.replace("/", os.sep)))
        if not os.path.exists(full):
            missing.append(f"{html_path}: 引用了不存在的资源 {u!r}")
    return missing


def main(argv: list[str]) -> int:
    roots = [argv[i + 1] for i, a in enumerate(argv) if a == "--dir"]
    if not roots:
        roots = ["src/ui/web"]
    strict = "--strict" in argv

    htmls: list[str] = []
    webroot: str | None = None
    for r in roots:
        if os.path.isfile(r):
            htmls.append(r)
            continue
        if webroot is None:
            webroot = r
        for root, dirs, names in os.walk(r):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            # 下划线开头 = 归档的设计稿/草稿，不参与运行时资源校验
            htmls += [os.path.join(root, n) for n in names
                      if n.lower().endswith((".html", ".htm")) and not n.startswith("_")]

    problems: list[str] = []
    for h in htmls:
        problems += scan(h, webroot)

    print(f"检查 {len(htmls)} 个 HTML，缺失资源 {len(problems)} 处")
    for p in problems:
        print("  ✗ " + p)
    if strict and problems:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
