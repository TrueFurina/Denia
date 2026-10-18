"""配置校验测试（阶段 B4，src/config_schema.py）。

为什么值得测：这个模块的全部价值就是"**能抓到配置问题**"。
如果它抓不到，那它就是一段无害但无用的代码——比没有更糟，因为它会让人
以为"配置已经被校验过了"。

所以这里既测"好配置不出声"，更测"坏配置必须被点名"。

跑法：.venv/Scripts/python.exe src/tests/test_config_schema.py
"""
from __future__ import annotations

import copy
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import config_schema  # noqa: E402
# 版本号从单点取，不写字面量：发版只改 src/version.py 一处，
# 写死 "0.2.0" 会让"配置校验"这道闸在下次改版本时必红（2026-10-18 质检 P2）。
from src.version import APP_VERSION  # noqa: E402

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        _fails.append(msg)


def _good_raw() -> dict:
    """一份关键项齐全的配置（字段值照抄真实 config.yaml 的结构）。"""
    return {
        "app": {"log_level": "INFO", "name": "AI桌面伴侣", "version": APP_VERSION},
        "ollama": {"host": "http://localhost:11434",
                   "vision_model": "openbmb/minicpm-o4.5",
                   "fallback_model": "qwen3:14b"},
        "llm": {"provider": "openai"},
        "runtime": {"text_mode_only": False},
        "websocket": {"host": "0.0.0.0", "port": 8765, "heartbeat_interval_s": 30},
        "voice": {"mic_enabled": False, "asr": {"model_path": "models/vosk"}},
        "perception": {"vision": {"enabled": True}, "screenshot": {}},
        "memory": {"enabled": True},
        "characters": [{"id": "danya", "name": "达妮娅"}],
        "ui": {"pet_backend": "auto"},
    }


def _paths(issues) -> set:
    return {i.path for i in issues}


def _capture_logs(fn):
    """跑 fn，顺便收集 src.config_schema 打出的日志。"""
    import logging as _logging

    lg = _logging.getLogger("src.config_schema")
    box: list = []

    class _Cap(_logging.Handler):
        def emit(self, record):
            box.append(record)

    cap = _Cap()
    old_level, old_prop = lg.level, lg.propagate
    lg.addHandler(cap)
    lg.setLevel(_logging.DEBUG)
    try:
        result = fn()
    finally:
        lg.removeHandler(cap)
        lg.setLevel(old_level)
        lg.propagate = old_prop
    return result, box


def main() -> int:
    print("[1] 好配置 → 一个字都不该说")
    issues = config_schema.validate(_good_raw())
    check(issues == [], f"无问题（实际 {[str(i) for i in issues]}）")
    check(config_schema.apply_defaults(_good_raw()) == [],
          "好配置不需要补任何默认值")

    print("[2] 缺必需段 → ERROR 且指明去哪补")
    raw = _good_raw()
    raw.pop("ollama")
    issues = config_schema.validate(raw)
    check(any(i.level == config_schema.ERROR and i.path == "ollama"
              for i in issues), "ollama 缺失被报 ERROR")
    check("config.yaml" in (issues[0].hint if issues else ""),
          "提示里写明该改哪个文件")

    print("[3] 类型错 → ERROR 且给出实际值")
    raw = _good_raw()
    raw["websocket"]["port"] = "8765"      # 字符串而不是整数
    issues = config_schema.validate(raw)
    hit = [i for i in issues if i.path == "websocket.port"]
    check(bool(hit), "类型错被抓到")
    check(hit and hit[0].level == config_schema.ERROR, "级别是 ERROR")
    check(hit and "8765" in hit[0].message, "明细里带出实际值，便于定位")

    print("[4] 取值非法 → ERROR 且列出可选值")
    raw = _good_raw()
    raw["ui"]["pet_backend"] = "banana"
    issues = config_schema.validate(raw)
    hit = [i for i in issues if i.path == "ui.pet_backend"]
    check(bool(hit), "非法取值被抓到")
    check(hit and "auto/webview/shell" in hit[0].message,
          "明细里列出可选值")

    print("[5] 缺可选项 → 补齐 + WARN（不能静默）")
    raw = _good_raw()
    raw.pop("ui")
    filled, records = _capture_logs(lambda: config_schema.apply_defaults(raw))
    check(raw.get("ui", {}).get("pet_backend") == "auto",
          "ui.pet_backend 已被补成 auto（先试壳，起不来自动回退）")
    check(any(i.path == "ui.pet_backend" and i.level == config_schema.WARN
              for i in filled), "补齐动作留在返回清单里（WARN）")
    # 关键：光"补"不够，必须**出声**。静默默认正是"我配了却没生效"的根因。
    check(any(r.levelno >= 30 for r in records),
          f"补齐时确实打出了 WARNING 日志（实际 {len(records)} 条）")
    check(any("ui.pet_backend" in r.getMessage() for r in records),
          "日志里点名补的是哪个键，用户能照着去改")

    print("[6] 必需段缺失：补齐后 Config 不应直接崩")
    raw = _good_raw()
    raw.pop("perception")
    filled = config_schema.apply_defaults(raw)
    check("perception" in raw, "perception 段被补回来了")
    check(any(i.path == "perception" and i.level == config_schema.ERROR
              for i in filled), "必需段缺失记 ERROR")
    # 补齐后重新校验：子项已由默认值填上，不应再有残留 ERROR
    again = config_schema.validate(raw)
    check(not any(i.level == config_schema.ERROR for i in again),
          f"补齐后重验无 ERROR（实际 {[str(i) for i in again]}）")

    print("[7] apply_defaults 幂等")
    raw = _good_raw()
    raw.pop("app")
    raw.pop("ui")
    first = config_schema.apply_defaults(raw)
    second = config_schema.apply_defaults(raw)
    check(len(first) > 0 and second == [],
          f"第二次不再补任何东西（{len(first)} → {len(second)}）")

    print("[8] 根节点不是映射 / 空配置")
    bad = config_schema.validate("not a dict")
    check(len(bad) == 1 and bad[0].level == config_schema.ERROR,
          "根节点非法 → 一条 ERROR")
    empty = config_schema.validate({})
    check(all(i.level == config_schema.ERROR for i in empty),
          "空配置 → 全部必需项报 ERROR")
    check("app" in _paths(empty) and "characters" in _paths(empty),
          "空配置点名 app 与 characters")

    print("[9] 跨字段：app.version 必须与 src/version.py 一致")
    try:
        from src.version import APP_VERSION
    except Exception:  # noqa: BLE001
        APP_VERSION = None
    check(APP_VERSION is not None, "src/version.py 提供 APP_VERSION")
    if APP_VERSION is not None:
        raw = _good_raw()
        raw["app"]["version"] = "0.0.1"
        issues = config_schema.validate(raw)
        hit = [i for i in issues if i.path == "app.version"]
        check(bool(hit), "版本漂移被抓到")
        check(hit and str(APP_VERSION) in hit[0].message,
              "明细里给出代码侧的真值")

        # 回归（2026-10-18 质检 P1）：缺失 ≠ 漂移。
        # 以前 apply_defaults 把 app.version 补成 ""，validate 再拿 "" 去比，
        # 于是一个**可选项没写**被报成 ERROR，doctor 同时红两条。
        raw_missing = _good_raw()
        raw_missing["app"].pop("version")
        filled = config_schema.apply_defaults(raw_missing)
        check("version" not in raw_missing["app"],
              "缺失的 app.version 不被补成假值（以前补的是空串）")
        check(not [i for i in filled if i.path == "app.version"],
              "也不在这里反复报（apply_defaults 必须幂等；该由 doctor 的版本项说）")
        issues_missing = config_schema.validate(raw_missing)
        check(not [i for i in issues_missing if i.path == "app.version"],
              "缺失不会被判成漂移（不产生 app.version 的 ERROR）")
        check(config_schema.apply_defaults(raw_missing) == [],
              "再补一次仍为空 → apply_defaults 幂等")

    print("[10] 边界：None / 非 dict 不抛异常")
    try:
        check(config_schema.apply_defaults(None) == [], "None → 空清单")
        check(config_schema.apply_defaults([]) == [], "list → 空清单")
        check(config_schema.validate(None)[0].level == config_schema.ERROR,
              "validate(None) → ERROR 而非异常")
    except Exception as _e:  # noqa: BLE001
        check(False, f"边界输入抛异常了：{_e}")

    print("[11] 迁移：只补缺失的，不动已有值")
    raw = _good_raw()
    raw["ui"]["pet_backend"] = "shell"
    raw.pop("ui")
    config_schema.apply_defaults(raw)
    check(raw.get("ui", {}).get("pet_backend") == "auto",
          "整段缺失 → 补出默认 auto")
    raw2 = _good_raw()
    raw2["ui"].pop("pet_backend")
    config_schema.apply_defaults(raw2)
    check(raw2["ui"]["pet_backend"] == "auto", "仅键缺失 → 补默认 auto")
    raw3 = _good_raw()
    before = copy.deepcopy(raw3)
    config_schema.apply_defaults(raw3)
    check(raw3 == before, "完整配置 → 一个字节都不动")

    print()
    if _fails:
        print(f"❌ FAIL：{len(_fails)} 项")
        for f in _fails:
            print("   - " + f)
        return 1
    print("✅ 全部通过（配置校验）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
