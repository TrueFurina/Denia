# -*- coding: utf-8 -*-
"""质检仪器用的「造一份像样的仓库副本」。

为什么不能直接 `cp -r`，也不能不带 `.git`
------------------------------------------
两个方向都踩过，所以这里定成一条规矩：**副本里重新 init 一个仓，而不是复制原仓的
`.git`，也不是干脆没有 `.git`。**

① 复制原仓的 `.git` 不行。体积大是小事，要命的是它描述的是**原仓**：副本里刀改完
   文件再还原，索引/工作区状态跟原仓对不上，`git status` 一片脏，靠 `git ls-files`
   取清单的判定读到的未必是"这份副本里该有的文件"。

② 干脆没有 `.git` 也不行。`check_bundled_assets`、`check_pack_exclude`、
   `_qc_first_run_mutation` 都靠 `git ls-files` 取文件清单，没有 `.git` 时它们要么
   直接抛 `fatal: not a git repository`，要么报"取不到 git 文件清单"。这曾经让
   `_qc_bundled_assets_mutation` 在 `check_knife_vacuity.py` 里被挂进 `EXCUSED`
   （理由写的是"判定需要 git ls-files，而工具复制仓库时把 .git 丢了"）——
   那不是刀的问题，是**副本造得不像仓库**。

重新 `git init` + `git add -A` + commit 之后，两件事同时成立：
  · `git ls-files` 返回这份副本的文件清单（925 个，实测）；
  · 天然遵守 `.gitignore` —— `config.yaml`、`logs/`、`.venv/` 不进清单，
    这**正好**是新克隆的样子。副本因此比"复制原仓"更接近真实场景。

不碰工作区字节
--------------
`git add` 只写索引，不改工作区（`core.autocrlf` 也在这里显式关掉，免得索引归一化
连带影响后续 `git checkout` 类的判定）。刀做锚点匹配靠的是工作区字节，必须原样。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".pytest_cache")

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "qc",
    "GIT_AUTHOR_EMAIL": "qc@local",
    "GIT_COMMITTER_NAME": "qc",
    "GIT_COMMITTER_EMAIL": "qc@local",
    # 别让开发机上的 ~/.gitconfig / 系统配置影响副本（签名、hooks、换行策略都可能坏事）
    "GIT_CONFIG_NOSYSTEM": "1",
}


def _git(dst: str, *args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(_GIT_ENV)
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    return subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=dst, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def git_init(dst: str) -> None:
    """在 dst 里造一个"刚克隆"的仓：init + add -A + commit。

    失败一律抛异常而不是静默降级 —— 静默降级会让调用方以为有 `.git`，
    于是判定退化成"缺环境红"，又绕回本文件开头说的那个坑。
    """
    steps = (("init", "-q", "."), ("add", "-A"),
             ("commit", "-qm", "qc fixture"))
    for args in steps:
        p = _git(dst, *args)
        if p.returncode != 0:
            raise RuntimeError("git %s 失败（rc=%d）：%s"
                               % (" ".join(args), p.returncode, (p.stderr or "").strip()[:300]))


def copy_repo(root: str, need_git: bool = True) -> str:
    """复制 root 到临时目录并（默认）造好 git 仓，返回副本仓根。

    调用方负责 `shutil.rmtree(os.path.dirname(副本))`。
    """
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise RuntimeError("仓根不存在：%s" % root)
    d = tempfile.mkdtemp(prefix="qc_repo_")
    dst = os.path.join(d, "repo")
    shutil.copytree(root, dst, ignore=IGNORE)
    if need_git:
        git_init(dst)
    return dst


def reset(dst: str) -> None:
    """把副本工作区还原到 `git_init` 那次提交的样子。

    为什么要这一步
    --------------
    质检工具常要跑两遍同一把刀（起点一遍、把变异/写入退化掉再一遍），而第一遍
    **会在盘上留下产物**：写出 `probe.txt` 的、生成 `gen_mod.py` 再 import 的、
    往 `src/` 扔坏文件的……第二遍于是读到上一次的残留，"什么都没变"被它当成
    "变了"（或者反过来），结论就是仪器在替刀作弊。

    早先的做法是再复制一份干净副本，代价是每把刀多拷一次仓（实测 61 把刀
    多花十几分钟），而且复制本身还可能被运行中的 `.venv` 句柄卡住。
    副本已经是 git 仓了，直接还原更省也更准：

        git checkout -- .   还原被改过的已跟踪文件
        git clean -fd       删掉新增的未跟踪文件/目录

    **故意不带 `-x`**：`.gitignore` 里那些（`.venv/`、`config.yaml`、`logs/`）要
    留着 —— 刀要用 venv 的解释器、要有配置可读。带 `-x` 会把它们一起删掉，
    下一遍就变成"缺环境"，那正是这一轮在修的毛病。
    """
    steps = (("checkout", "--", "."), ("clean", "-fd"))
    for args in steps:
        p = _git(dst, *args)
        if p.returncode != 0:
            raise RuntimeError("git %s 失败（rc=%d）：%s"
                               % (" ".join(args), p.returncode, (p.stderr or "").strip()[:300]))
