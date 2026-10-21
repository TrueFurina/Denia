# -*- coding: utf-8 -*-
"""跑首启克隆面板「预填她的」的行为真值表（node 侧在 `test_clone_card_ui.js`）。

为什么这么绕：判据说的是"点了以后界面/桥接发生什么"（试听播了哪个 src、克隆调了
谁、失败怎么报），静态查源码只能证明字符串在不在。JS 侧用 jsdom **原样加载**
`src/ui/web/onboarding_role.js` 整文件，mock window.Backend 后真点真走——
不是照着逻辑再写一份（那只证明"我两次写得一样"）。

依赖解析（按优先级，都不写死开发机路径）：
  1. 环境变量 DANIYA_JSDOM：指向含 jsdom 的 node_modules 目录；
  2. <仓库>/node_modules；
  3. 本机受管 node workspace（~/.workbuddy-ai/binaries/node/workspace/node_modules）。
找不到 jsdom → 明确打印缺什么并退出 77（check_all 约定：单列跳过，不计通过），
**不**静默通过。

退出码：0 = 全过；1 = 有失败；77 = 缺 node 或 jsdom（跳过）。
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NODE = os.environ.get("DANIYA_NODE") or shutil.which("node") or ""
if not NODE:
    print("[SKIP] 找不到 node（行为真值表需要它）；可用 DANIYA_NODE 指定")
    raise SystemExit(77)


def _has_jsdom(node_modules_dir: str) -> bool:
    return bool(node_modules_dir) and os.path.isdir(os.path.join(node_modules_dir, "jsdom"))


def _find_jsdom_dir() -> str:
    """返回含 jsdom 的 node_modules 目录；找不到返回 ''。"""
    cands = []
    env = os.environ.get("DANIYA_JSDOM")
    if env:
        # 允许直接指向 jsdom 包目录本身或其父目录
        cands.append(env if os.path.basename(env.rstrip("/\\")) != "jsdom"
                     else os.path.dirname(env.rstrip("/\\")))
    cands.append(os.path.join(ROOT, "node_modules"))
    home = os.path.expanduser("~")
    cands.append(os.path.join(home, ".workbuddy-ai", "binaries", "node", "workspace",
                              "node_modules"))
    for c in cands:
        if _has_jsdom(c):
            return os.path.abspath(c)
    return ""


JSDOM_DIR = _find_jsdom_dir()
if not JSDOM_DIR:
    print("[SKIP] 找不到 jsdom（克隆面板真值表需要它）。安装到仓库 node_modules，"
          "或用 DANIYA_JSDOM 指向含 jsdom 的目录")
    raise SystemExit(77)

env = dict(os.environ)
# NODE_PATH 让 node 从受管 workspace 解析 jsdom；只加不覆盖其它已有项
_prev = env.get("NODE_PATH", "")
env["NODE_PATH"] = JSDOM_DIR + (os.pathsep + _prev if _prev else "")
print("[env] NODE_PATH=%s" % env["NODE_PATH"])

r = subprocess.run([NODE, os.path.join("tools", "test_clone_card_ui.js")], cwd=ROOT,
                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                   env=env)
sys.stdout.write(r.stdout or "")
sys.stderr.write(r.stderr or "")
raise SystemExit(r.returncode)
