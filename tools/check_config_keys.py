# -*- coding: utf-8 -*-
"""出厂模板对账：模板里每个配置键，都必须在代码里有读取点。

为什么需要它：2026-10-24 普查发现出厂模板 424 个叶子键里 **24 个是死的**——
没有任何代码读它们。用户/队友照着模板配了，什么都没发生（`voice`/`voice_id`
同型的放大版）。已全部删除（CHANGELOG 2026-10-24）。本闸门守住这个状态：
以后往模板里加键，必须同时在代码里有读取点，否则红。

判据：解析 config.release.yaml → 全部叶子键 → 叶名必须出现在 src/**/*.py 与
根入口脚本（main.py / launch_companion.py / run_webui.py）的 **NAME/STRING
token** 里。用 tokenize 丢 COMMENT——注释里提一句不算读取点，那正是本次修掉的
`max_per_type_per_day` 谎言形态（docstring 承诺每日上限，真实机制是
type_cooldown_s，键却没人读）。

例外清单（每条带原因，不许裸豁免）：
  - `_release_note`：模板元信息（给人看的说明），不是设置项。
  - `ollama.options.*`：泛型透传——src/llm/ollama_client.py 把整棵 options
    子树 `vars()` 转发给 ollama API，任何子键都生效，叶名不会逐个出现在代码里。

已知盲区（写在这免得下次被它骗）：**常见词叶名会假通过**——`with`/`after`/
`mode` 这类名字作为 NAME token 在代码里到处都是（sticker.random_probs 的
`with`/`after` 两个死键就这样漏过本判据，靠人工普查抓出来）。本闸门抓强信号
（特征名键），常见词键靠人工。

退出码：0 = 全部有着落；1 = 有死键；2 = 闸门自身坏（YAML 解析失败等）。
"""
from __future__ import annotations

import io
import os
import sys
import tokenize

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, "config.release.yaml")
SCAN = ["src", "main.py", "launch_companion.py", "run_webui.py"]
# 例外：精确键名或子树前缀。加条目必须写原因（见 docstring）。
EXEMPT_EXACT = ("_release_note",)
EXEMPT_SUBTREE = ("ollama.options",)


def _leaf_keys(data: object, path: list) -> list:
    out = []
    if isinstance(data, dict):
        for k, v in data.items():
            out.extend(_leaf_keys(v, path + [str(k)]))
    else:
        out.append(".".join(path))
    return out


def _code_tokens() -> set:
    """src/ 与根入口脚本的全部 NAME/STRING token（COMMENT 丢掉）。"""
    tokens: set = set()
    errors: list = []

    def scan(p: str) -> None:
        try:
            with io.open(p, "rb") as fh:
                for tok in tokenize.tokenize(fh.readline):
                    if tok.type in (tokenize.NAME, tokenize.STRING):
                        v = tok.string
                        if tok.type == tokenize.STRING:
                            v = v.strip("\"'")
                        tokens.add(v)
        except Exception as exc:  # noqa: BLE001
            errors.append("%s: %s" % (p, exc))

    for entry in SCAN:
        full = os.path.join(ROOT, entry)
        if os.path.isdir(full):
            for dp, dn, fn in os.walk(full):
                dn[:] = [d for d in dn if d != "__pycache__"]
                for f in sorted(fn):
                    if f.endswith(".py"):
                        scan(os.path.join(dp, f))
        elif os.path.isfile(full):
            scan(full)
        else:
            errors.append("扫描目标不存在: %s" % entry)
    if errors:
        raise RuntimeError("；".join(errors))
    return tokens


def main() -> int:
    try:
        with io.open(TEMPLATE, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
    except Exception as exc:  # noqa: BLE001
        print("✗ 闸门自身坏：解析 config.release.yaml 失败: %s" % exc)
        return 2
    try:
        tokens = _code_tokens()
    except Exception as exc:  # noqa: BLE001
        print("✗ 闸门自身坏：扫描代码 token 失败: %s" % exc)
        return 2

    keys = _leaf_keys(cfg, [])
    dead, exempt = [], []
    for k in keys:
        if k in EXEMPT_EXACT or any(k == p or k.startswith(p + ".") for p in EXEMPT_SUBTREE):
            exempt.append(k)
            continue
        if k.split(".")[-1] not in tokens:
            dead.append(k)

    print("检查 %d 键 / 豁免 %d / 死键 %d" % (len(keys), len(exempt), len(dead)))
    for k in exempt:
        print("  豁免 %s" % k)
    if dead:
        print("✗ 出厂模板里有死键（没有任何代码读取点）：")
        for k in dead:
            print("  ✗ %s" % k)
        return 1
    print("✓ 模板里每个键都有代码读取点（或在例外清单里且写明原因）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
