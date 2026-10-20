# -*- coding: utf-8 -*-
"""doctor「配置文件」检查项：升级覆盖检测的三态 + 对照组。

【2026-10-21 修·判据恒真】
原判据 `keys_empty = all(not (raw_cfg.get(k) or "") for k in ("api_key",))` ——
而**顶层根本没有 `api_key` 这个键**（模板与实配都没有；密钥在 `llm.openai` /
`text_chat.providers[]` / `vision_models.models[]` / `voice` / `voice_profiles[]` 里）。
于是 `keys_empty` 恒为 True，只要 `data/` 非空就恒报「config.yaml 是出厂默认值，
但 data/ 里有使用痕迹」——**所有真正在用的人都看到这条假警**。

危害是"狼来了"：一条恒真的 WARN 会把真 WARN 淹掉。

这里锁住修好后的三态，并且**必须有对照组**（有 Key 的老用户不许报）：
只验"该报时报了"分不清"判据正确"与"判据恒真"。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import yaml  # noqa: E402

import tools.doctor as doctor  # noqa: E402

PASS = 0
FAIL = 0


def check(ok: bool, label: str) -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  ✓ " + label)
    else:
        FAIL += 1
        print("  ✗ " + label)


def _blank_api_keys(node) -> None:
    """把配置树里所有 api_key 清空（原地改）。"""
    if isinstance(node, dict):
        for k, v in list(node.items()):
            if k == "api_key":
                node[k] = ""
            else:
                _blank_api_keys(v)
    elif isinstance(node, list):
        for x in node:
            _blank_api_keys(x)


def _scene(tmp: str, *, key_value: str, has_data: bool) -> str:
    """造一个临时 ROOT：config.yaml（api_key 按需）+ 可选 data/。

    key_value:
      ""             → 所有 api_key 置空（模拟"配置被覆盖"）
      "sk-test-123"  → 写一把明文 Key（模拟正常老用户）
      "${TEST_KEY}"  → 环境变量占位符（**也算有配置**）
    """
    tpl = os.path.join(ROOT, "config.release.yaml")
    with open(tpl, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    _blank_api_keys(raw)
    if key_value:
        raw.setdefault("llm", {}).setdefault("openai", {})["api_key"] = key_value
    cfg_path = os.path.join(tmp, "config.yaml")
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(raw, f, allow_unicode=True)
    if has_data:
        d = os.path.join(tmp, "data")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "usage.txt"), "w", encoding="utf-8") as f:
            f.write("有使用痕迹")
    return cfg_path


def _run(cfg_path: str, root: str):
    """在临时 ROOT 下跑 check_config（ROOT 影响 data/ 与 bak 的查找位置）。"""
    with mock.patch.object(doctor, "ROOT", root):
        return doctor.check_config(cfg_path)


def main() -> int:
    print("== doctor 配置文件检查：升级覆盖检测三态 ==")

    # ---- 1 该报的场景：全空 + 有 data/ ----
    tmp = tempfile.mkdtemp(prefix="doc_cfg_1_")
    try:
        lvl, name, msg, _hint = _run(_scene(tmp, key_value="", has_data=True), tmp)
        check(lvl == "WARN" and "使用痕迹" in msg,
              f"1 全空 + 有 data/ → WARN（实际 {lvl}：{msg[:40]}）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- 2 对照组（本次修复的主角）：有 Key + 有 data/ 不许报 ----
    tmp = tempfile.mkdtemp(prefix="doc_cfg_2_")
    try:
        lvl, _n, msg, _h = _run(_scene(tmp, key_value="sk-test-123", has_data=True), tmp)
        check(lvl == "PASS",
              f"2 有 Key + 有 data/ → PASS（实际 {lvl}：{msg[:40]}）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- 3 不该报的场景：全空但没有 data/（就是新用户） ----
    tmp = tempfile.mkdtemp(prefix="doc_cfg_3_")
    try:
        lvl, _n, msg, _h = _run(_scene(tmp, key_value="", has_data=False), tmp)
        check(lvl == "PASS",
              f"3 全空 + 无 data/（新用户）→ PASS（实际 {lvl}：{msg[:40]}）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- 4 环境变量占位符：环境里有值才算"有配置" ----
    # 注意 `cfg.raw` 是**展开后**的配置，不是原始 YAML 文本。
    # 所以 `${TEST_KEY}` 在环境里没有时会展开成空串 → 报 WARN 是**正确**的（真的没 Key）。
    # 这里显式注入环境变量，验证"从环境读"也算有配置（密钥不落盘是推荐做法）。
    tmp = tempfile.mkdtemp(prefix="doc_cfg_4_")
    try:
        cfg_path = _scene(tmp, key_value="${TEST_KEY}", has_data=True)
        with mock.patch.dict(os.environ, {"TEST_KEY": "sk-from-env"}):
            lvl, _n, msg, _h = _run(cfg_path, tmp)
        check(lvl == "PASS",
              f"4a ${{ENV}}（环境有值）+ 有 data/ → PASS（实际 {lvl}：{msg[:40]}）")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TEST_KEY", None)
            lvl2, _n2, msg2, _h2 = _run(cfg_path, tmp)
        check(lvl2 == "WARN" and "使用痕迹" in msg2,
              f"4b ${{ENV}}（环境没值→展开为空）+ 有 data/ → WARN（实际 {lvl2}）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- 5 递归扫描本身：Key 埋在深层也要能找到 ----
    check(doctor._has_any_api_key({"a": {"b": [{"api_key": "sk-x"}]}}) is True,
          "5a 深层嵌套里的 api_key 能被扫到")
    check(doctor._has_any_api_key({"a": {"api_key": "  "}}) is False,
          "5b 空白串不算有 Key")
    check(doctor._has_any_api_key({"api_key": "${ENV}"}) is True,
          "5c 环境变量占位符算有 Key")
    check(doctor._has_any_api_key({"api_key": ""}) is False,
          "5d 空串不算有 Key")

    print("\n结果: %d PASS / %d FAIL" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
