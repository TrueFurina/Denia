# -*- coding: utf-8 -*-
"""配置校验与迁移（阶段 B4）。

解决什么问题：`load_config()` 只是 `yaml.safe_load` + 转 SimpleNamespace，
**没有任何校验**。后果分两类，都很难受：
  · 缺必需字段 → 启动时某个 `cfg.ns.ollama.host` 直接 AttributeError，崩得莫名其妙；
  · 缺可选字段 → 到处 `getattr(x, "y", default)` 静默回落，用户以为配了其实没生效。

设计原则（与项目其余部分一致）：
  · **不阻断启动**：配置有问题是"该被大声告知"的事，不是"该让软件打不开"的事。
    issues 收集到 `cfg.config_issues`，由 `--doctor` 集中呈现；
  · **缺失可选字段要留下痕迹**：`apply_defaults()` 补齐时会逐条 log 一条 WARNING，
    杜绝"静默默认"——那正是"我明明配了却没生效"这类工单的根因。

只校验**会崩或会静默出错**的关键项，不做全量 schema——那是维护陷阱。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field as _dc_field
from typing import Any, Optional

logger = logging.getLogger("src.config_schema")

ERROR = "error"
WARN = "warn"


@dataclass
class Issue:
    """一条配置问题。hint 必须**可执行**（写清改哪个键、改成什么）。"""

    level: str
    path: str
    message: str
    hint: str = ""

    def __str__(self) -> str:
        return f"[{self.level}] {self.path}: {self.message}"


@dataclass
class Field:
    path: str
    kind: str = "str"          # section | str | int | number | bool | list
    required: bool = False
    default: Any = None
    hint: str = ""
    choices: Optional[tuple] = _dc_field(default=None)


# 只列关键项：Config 里不带 getattr 兜底直接访问的（缺了就崩），
# 以及决定功能开关的（缺了会静默回落，最坑）。
# hint 一律写成「改哪个文件的哪个键」，能复制粘贴去改，别写"请检查配置"这种废话。
SPEC: tuple[Field, ...] = (
    Field("app", "section", True, hint="config.yaml 的 app: 段"),
    Field("app.log_level", "str", False, "INFO",
          "config.yaml 的 app.log_level",
          ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")),
    Field("app.version", "str", False, None,
          "config.yaml 的 app.version（应与 src/version.py 的 APP_VERSION 一致）"),

    Field("ollama", "section", True, hint="config.yaml 的 ollama: 段"),
    Field("ollama.host", "str", True,
          hint="config.yaml 的 ollama.host，例：http://127.0.0.1:11434"),
    Field("ollama.vision_model", "str", True,
          hint="config.yaml 的 ollama.vision_model（需先 ollama pull）"),
    Field("ollama.fallback_model", "str", True,
          hint="config.yaml 的 ollama.fallback_model（需先 ollama pull）"),

    Field("llm", "section", True, hint="config.yaml 的 llm: 段"),
    Field("llm.provider", "str", False, "openai",
          "config.yaml 的 llm.provider", ("openai", "ollama", "minicpm")),

    Field("runtime", "section", True, hint="config.yaml 的 runtime: 段"),
    Field("runtime.text_mode_only", "bool", False, False,
          "config.yaml 的 runtime.text_mode_only"),

    Field("websocket", "section", True, hint="config.yaml 的 websocket: 段"),
    Field("websocket.host", "str", True, hint="config.yaml 的 websocket.host"),
    Field("websocket.port", "int", True, hint="config.yaml 的 websocket.port"),
    Field("websocket.heartbeat_interval_s", "number", False, 30.0,
          "config.yaml 的 websocket.heartbeat_interval_s"),

    Field("voice", "section", True, hint="config.yaml 的 voice: 段"),
    Field("voice.mic_enabled", "bool", False, False,
          "config.yaml 的 voice.mic_enabled"),
    Field("voice.asr", "section", True, hint="config.yaml 的 voice.asr: 段"),
    Field("voice.asr.model_path", "str", True,
          hint="config.yaml 的 voice.asr.model_path（留空则运行时自动下载）"),

    Field("perception", "section", True, hint="config.yaml 的 perception: 段"),
    Field("perception.vision", "section", True,
          hint="config.yaml 的 perception.vision: 段"),
    Field("perception.vision.enabled", "bool", False, True,
          "config.yaml 的 perception.vision.enabled"),
    Field("perception.screenshot", "section", True,
          hint="config.yaml 的 perception.screenshot: 段"),

    Field("memory", "section", False,
          hint="config.yaml 的 memory: 段（缺失则按关闭处理）"),

    Field("characters", "list", True, hint="config.yaml 的 characters: 数组，至少一项"),

    # 【阶段 A1】桌宠渲染宿主；老配置没有这一项 → 补 auto（默认值，不静默）
    # 默认 auto 而不是 webview：壳起不来会自动静默回退（见 src/ui/pet_backend.py），
    # 结果与 webview 相同，但壳能起来时用户才拿得到"真透明"。
    Field("ui", "section", False, hint="config.yaml 的 ui: 段"),
    Field("ui.pet_backend", "str", False, "auto",
          "config.yaml 的 ui.pet_backend", ("auto", "webview", "shell")),
)


def _dig(raw: Any, path: str):
    """按点号路径取值，任一环不存在返回 `None`（不抛）。"""
    cur = raw
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _dig_parent(raw: Any, path: str):
    """返回 (父容器, 末段键)，找不到返回 (None, None)。"""
    parts = path.split(".")
    cur = raw
    for part in parts[:-1]:
        if not isinstance(cur, dict) or part not in cur:
            return None, None
        cur = cur[part]
    return cur, parts[-1]


def _type_ok(value: Any, kind: str) -> bool:
    if kind == "section":
        return isinstance(value, dict)
    if kind == "str":
        return isinstance(value, str)
    if kind == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "list":
        return isinstance(value, list)
    return True


def apply_defaults(raw: Any) -> list[Issue]:
    """就地补齐缺失字段；返回"补了什么"的清单（必需项 ERROR、可选项 WARN）。

    为什么要补而不是直接报错：缺 `perception.vision.enabled` 时 `Config.__init__`
    里的 `getattr(self.ns.perception.vision, ...)` 会直接 AttributeError——
    **崩在一个完全看不出是配置问题的地方**。补齐后至少能起来，问题由 doctor 报。

    为什么不静默补：静默默认 = 用户以为配了其实没生效，是"玄学 bug"的主要来源。
    所以每补一项都 log 一条，级别按是否必需区分。
    """
    filled: list[Issue] = []
    if not isinstance(raw, dict):
        return filled
    for spec in SPEC:
        parent, key = _dig_parent(raw, spec.path)
        if parent is None or key in parent:
            continue
        # 默认值是 None 的可选项：**不补、也不在这里报**。
        # 补一个假值（以前 app.version 补的是 ""）会让后续校验把它当成
        # "用户真的写了这个值"，于是"缺失"变成"漂移"，一个可选项硬生生报成
        # ERROR（2026-10-18 质检 P1）。而不补又报，会让 apply_defaults 不再幂等
        # （第二次调用还在报同一项）。所以这类项交给对应检查去说——
        # app.version 由 doctor 的「版本一致」项负责（WARN）。
        if spec.kind != "section" and spec.default is None:
            logger.debug("[配置] %s 缺失且无默认值 → 保持缺失（由专项检查报告）",
                         spec.path)
            continue
        if spec.kind == "section":
            parent[key] = {} if spec.default is None else dict(spec.default)
        else:
            parent[key] = spec.default
        lvl = ERROR if spec.required else WARN
        filled.append(Issue(
            lvl, spec.path,
            ("缺少必需配置项" if spec.required else "缺少可配置项"),
            f"在 {spec.hint} 补上该项（已临时按默认 {spec.default!r} 处理）"))
        logger.warning("[配置] 缺少 %s（%s）→ 临时按默认 %r 处理",
                       spec.path, "必需" if spec.required else "可选",
                       spec.default)
    return filled


def validate(raw: Any) -> list[Issue]:
    """校验配置，返回问题清单（不抛异常、不修改输入）。

    空列表 = 配置没问题。
    """
    issues: list[Issue] = []
    if not isinstance(raw, dict):
        return [Issue(ERROR, "<root>", f"配置根节点不是映射（实际 {type(raw).__name__}）",
                      "检查 YAML 缩进，顶层必须是 `键: 值` 形式")]

    for spec in SPEC:
        value = _dig(raw, spec.path)
        if value is None:
            if spec.required:
                issues.append(Issue(
                    ERROR, spec.path, "缺少必需配置项",
                    f"在 {spec.hint} 补上该项；或复制 config.release.yaml 对应段"))
            continue
        if not _type_ok(value, spec.kind):
            issues.append(Issue(
                ERROR, spec.path,
                f"类型不对：应为 {spec.kind}，实际 {type(value).__name__}（{value!r}）",
                f"修正 {spec.hint} 的取值"))
            continue
        if spec.choices and value not in spec.choices:
            issues.append(Issue(
                ERROR, spec.path,
                f"取值非法：{value!r}（可选 {('/'.join(spec.choices))}）",
                f"把 {spec.hint} 改成其中之一"))

    # 跨字段一致性：app.version 必须与代码里的单点一致。
    # 空串按"没配"处理：以前 apply_defaults 会补 ""，这里再一比就变成
    # "配置 '' vs 0.2.0" 的 ERROR —— 可选项缺失被硬判成漂移（2026-10-18 质检 P1）。
    cfg_ver = _dig(raw, "app.version")
    try:
        from src.version import APP_VERSION
    except Exception:  # noqa: BLE001
        APP_VERSION = None
    cfg_ver_text = "" if cfg_ver is None else str(cfg_ver).strip()
    if APP_VERSION and cfg_ver_text and cfg_ver_text != str(APP_VERSION):
        issues.append(Issue(
            ERROR, "app.version",
            f"与代码单点不一致：配置 {cfg_ver!r} vs src/version.py {APP_VERSION!r}",
            f"把 config.yaml 的 app.version 改成 {APP_VERSION!r}；发版只改 src/version.py"))

    return issues


def validate_config(cfg: Any) -> list[Issue]:
    """对已加载的 Config 对象做校验（取 raw 再验一遍）。"""
    raw = getattr(cfg, "raw", None)
    return validate(raw)


__all__ = ["ERROR", "WARN", "Issue", "Field", "SPEC",
           "apply_defaults", "validate", "validate_config"]
