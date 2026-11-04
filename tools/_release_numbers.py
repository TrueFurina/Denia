# -*- coding: utf-8 -*-
"""发布口径的**单一规格表**：真值文件 + 站点里所有「受管数字」的锚点。

不进闸门：共享规格模块（非 check_*/test_*），由 check_release_numbers.py
与 sync_release_site.py 共同导入，避免两边各写一份实现互相「证两次写得一样」。

为什么要有这张表：2026-11-04 出包实测安装包是 175.5 MB，而 README / 官网 /
定位文档里还写着 72.8 MB。数字散在 HTML、MD、JS 常量里，靠人手改必然漂。
现在只有 site/release.json 是真值，站点里的每一处数字都挂在一个锚点上：
  · check_release_numbers.py 断言「锚点命中数 == 期望」且「锚点值 == 真值」
  · sync_release_site.py 用同一张表把真值回填进站点
  · 站点里任何「小数 + MB」token 都必须等于三个受管值之一（防漏挂锚点）

新增/删除一处对外数字时，必须同时改这张表，否则闸门会因为命中数不符而报红。
"""
from __future__ import annotations

import io
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRUTH_REL = "site/release.json"
VERSION_REL = "src/version.py"
SITE_FILES = ("site/index.html", "site/PUBLISH.md")

# (文件, 含命名组 n 的正则, 真值键, 期望命中次数)
NUMBER_SPECS: list[tuple[str, str, str, int]] = [
    ("site/index.html", r'content="[^"\n]*?(?P<n>\d+\.\d+)MB 双击就装[^"\n]*"', "setup_size_mb", 1),
    ("site/index.html", r'content="(?P<n>\d+\.\d+)MB，双击就装', "setup_size_mb", 1),
    ("site/index.html", r'<strong>(?P<n>\d+\.\d+)MB，双击就装。</strong>', "setup_size_mb", 1),
    ("site/index.html", r'id="cta-sz">(?P<n>\d+\.\d+) MB</small>', "setup_size_mb", 1),
    ("site/index.html", r'<div class="stat"><b>(?P<n>\d+\.\d+)<span style="font-size:\.5em">MB</span></b><span>安装包体积</span></div>', "setup_size_mb", 1),
    ("site/index.html", r'<b>(?P<n>\d+\.\d+) MB / 双击就装</b>', "setup_size_mb", 1),
    ("site/index.html", r'，(?P<n>\d+\.\d+) MB / 848 条目，包内不含运行时', "source_size_mb", 1),
    ("site/index.html", r'<td class="mono">(?P<n>\d+\.\d+) MB / 848 条目</td>', "source_size_mb", 1),
    ("site/index.html", r'<span>下载安装包</span><span class="sz">(?P<n>\d+\.\d+) MB</span>', "setup_size_mb", 1),
    ("site/index.html", r'<span>下载源码包</span><span class="sz">(?P<n>\d+\.\d+) MB</span>', "source_size_mb", 1),
    ("site/index.html", r'实测 (?P<n>\d+\.\d+) MB）。例：', "setup_size_mb", 1),
    ("site/index.html", r'// 源码包（(?P<n>\d+\.\d+)MB，Release', "source_size_mb", 1),
    ("site/PUBLISH.md", r'当前 HEAD 实测 (?P<n>\d+\.\d+) MB\*\*', "setup_size_mb", 1),
    ("site/PUBLISH.md", r'（NSIS/LZMA；stage (?P<n>\d+\.\d+) MB', "stage_size_mb", 1),
    ("site/PUBLISH.md", r'（(?P<n>\d+\.\d+)MB，安装版）', "setup_size_mb", 1),
    ("site/PUBLISH.md", r'（(?P<n>\d+\.\d+)MB，源码包）', "source_size_mb", 1),
    ("site/PUBLISH.md", r'\| (?P<n>\d+\.\d+) MB \| 内置运行时', "setup_size_mb", 1),
    ("site/PUBLISH.md", r'\| (?P<n>\d+\.\d+) MB \| 需要 Python 3\.12 \|', "source_size_mb", 1),
    ("site/PUBLISH.md", r'\*\*源码包\*\*（(?P<n>\d+\.\d+) MB / 848 条目', "source_size_mb", 1),
    ("site/PUBLISH.md", r'\*\*安装包\*\*（(?P<n>\d+\.\d+) MB / 双击就装', "setup_size_mb", 1),
]

VERSION_SPEC = ("site/index.html", r"version\s*:\s*'(?P<v>v\d+\.\d+\.\d+)'", 1)
SHA_SOURCE_SPEC = ("site/index.html", r"shaSource\s*:\s*'(?P<h>[0-9a-f]*)'", 1)
SHA_SETUP_SPEC = ("site/index.html", r"shaSetup\s*:\s*'(?P<h>[0-9a-f]*)'", 1)
SIZE_KEYS = ("setup_size_mb", "source_size_mb", "stage_size_mb")
REQUIRED_KEYS = ("schema_version", "version", *SIZE_KEYS,
                 "setup_sha256", "source_sha256")


def abs_path(rel: str) -> str:
    return os.path.join(ROOT, rel)


def read_site(rel: str) -> str:
    with io.open(abs_path(rel), "r", encoding="utf-8", newline="") as f:
        return f.read()


def write_site(rel: str, text: str) -> None:
    # 二进制写入保留工作副本的真实 EOL，文本模式在 Windows 会把 \n 洗成 \r\n
    with io.open(abs_path(rel), "wb") as f:
        f.write(text.encode("utf-8"))


def load_truth() -> dict:
    data = json.loads(read_site(TRUTH_REL))
    if data.get("schema_version") != 1:
        raise ValueError("release.json schema_version 必须是 1")
    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        raise ValueError("release.json 缺字段: %s" % ", ".join(missing))
    for k in SIZE_KEYS:
        if not isinstance(data[k], (int, float)) or data[k] <= 0:
            raise ValueError("release.json %s 必须是正数" % k)
    return data


def save_truth(data: dict) -> None:
    write_site(TRUTH_REL, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def fmt(value: float) -> str:
    return "%.1f" % float(value)


def allowed_numbers(truth: dict) -> set[str]:
    return {fmt(truth[k]) for k in SIZE_KEYS}


def app_version() -> str:
    """版本号的唯一定义处是 src/version.py，这里只读不写。"""
    m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', read_site(VERSION_REL), re.M)
    if not m:
        raise ValueError("src/version.py 里找不到 APP_VERSION")
    return m.group(1)


def sha256_of(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with io.open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def size_mb_of(path: str) -> float:
    return round(os.path.getsize(path) / 1048576.0, 1)
