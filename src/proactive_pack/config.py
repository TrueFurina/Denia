# -*- coding: utf-8 -*-
"""主动互动包 · 配置与开关（三级叠加，缺省即关）。

读取顺序（后面的覆盖前面的）：
  1) `DEFAULTS` —— 内置默认，**全部保守**；总开关 `enabled=False`。
  2) `config.yaml` 的 `proactive.proactive_pack`（**可选**；缺失 = 不覆盖）。
  3) 运行时状态文件 `data/proactive_pack.json` 的 `flags` 段
     （供 UI / 桥热切换，**不必碰 config.yaml**，避免和别的开发分支抢文件）。

红线：
  · 本模块**只读** config.yaml，**绝不回写**（回写会整份覆盖，项目踩过坑）。
  · 任何一级读不到 / 损坏 → 安静落到下一级，并在最后一个可用级上打一条日志（降级不静默）。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("proactive_pack")

# 项目 data 根目录（src/proactive_pack/config.py → parents[2] == 项目根）。
DEFAULT_BASE_DIR = Path(__file__).resolve().parents[2] / "data"
STATE_FILENAME = "proactive_pack.json"

# 总开关默认 **关**：装上包但不开，软件行为与装之前完全一致（可回滚 / 可灰度）。
DEFAULTS: dict[str, Any] = {
    "enabled": False,        # 总开关：关 = 一行代码都不执行（零副作用）
    "situation": True,       # 情境事实包总开关（喂给模型"此刻关于你的真实材料"）
    "usage_context": True,   # 认知材料：接入"今日电脑使用实况 + 昨日状态回顾"（只读 data/activity/，
    "screen_context": True,  # 认知材料：接入"她看屏的第一手观察 + 游戏战况"（读 events.jsonl 的
                             # screen/game 事件原话，兼作兴趣信号；数据源与双工专用提示词无关）
                             #   让她能看到"你今天一直在XX"这类贴身材料；无数据则块留空）
    "festival": True,        # ① 节日 / 关系纪念日
    "followup": True,        # ② 会话内跟进（聊到一半走开）
    "pending": True,         # ②b 宣告事件跟进（「我去玩游戏」→ 半天没动静 →「打完了？」）
    "recall": True,          # ③ 关系型召回（"你提过的事"，情绪重优先）
    "rhythm": True,          # ④ 生活节律（饭点 / 熬夜 / 久坐）
    "silence": True,         # ⑤ 长期静默召回（阶梯升温；此档允许问「在忙什么」）
    "share": True,           # ⑥ 兴趣分享（抖音式：基于用户兴趣 + 真实素材，长静默才用）
    "emotion_weight": True,  # 记忆条目情绪加权（只影响"挑哪条"，不改记忆库本体）
    "day_cap": 6,            # 每日开口上限（与宿主 DAILY_PROACTIVE_CAP 语义一致，独立记账）
    "followup_per_day": 2,   # 会话内跟进每日上限
    "followup_cooldown_h": 12.0,   # 会话内跟进每角色冷却（小时）
    "followup_silence_h": 1.0,     # 会话内跟进：距上条消息至少静默多久（小时）
    "followup_silence_max_h": 3.0, # 会话内跟进：静默上限（超过就不再算"会话内"）
    "recall_min_age_h": 24.0,      # 关系型召回：那条记忆至少"隔了多久"才回访
    "recall_window_days": 14.0,    # 关系型召回：最早回访窗口（超过就不翻旧账）
    "recall_status_window_days": 5.0,  # 状态类事件（感冒/面试/考试…）回访窗口更短：过了多半已了结
    "recall_per_day": 1,           # 关系型召回每日上限
    "share_per_day": 1,            # 兴趣分享每日上限（最"推内容"的一类，压得最低）
    "silence_per_day": 2,          # 久别招呼每日上限（6 组实测里 silence 占比最高≈37%，
                                   #   长缺席时每 3h 一句偏唠叨；压到 2 次/天更克制）
    "share_min_gap_h": 6.0,        # 兴趣分享：至少静默这么久才值得"给你看个东西"
    "feed_hot": True,              # 热榜素材自动保鲜（借鉴 N.E.K.O 的"拉热点喂话题"；
                                   #   只搬事实不编内容，涉政/灾难/八卦条目一律过滤）
    "feed_ttl_h": 4.0,             # 热榜刷新间隔（小时）；拉取在后台线程，不阻塞宿主
    "feed_max_items": 60,          # 素材池条目上限（优先保手工投喂的条目）
    "llm_gate": True,              # LLM 审查闸：发出前让**审查 LLM** 判"这句话此刻能不能发"
                                   #   （生成器只管想怎么说、审查器只管判放不放行；驳回则回灌重写。
                                   #    审查器故障/输出不可解析 → 放行 fail-open，不因闸门坏掉闭麦）
    "judge_attempts": 3,           # 生成+审查的最大轮数：每次被驳回都把理由回灌给生成器重写，
                                   #   用尽仍未通过 → 本轮放弃开口（下次有机会）。1 = 不重写
    "judge_provider": "zai-glm",   # 审查器**独立模型**（dev 用）：填 config.yaml 里 text_chat.providers 的 id。
                                   #   留空/找不到 = 回退与生成同一个模型（⚠️ 生成若是弱模型，
                                   #   审查会形同虚设——实测 glm-4-flash 做审查只有 3/10）。
                                   #   ★ 生产环境不要依赖它：把 judge_use_engine 设 true，
                                   #     审查器直接吃宿主模型引擎（host.engine_llm），无视本键。
    "judge_model": "glm-4.5-flash",  # 可选：覆盖该端口的 model（留空用端口自带）。
                                   #   本项目默认：生成用 glm-4-flash（会说人话就够），
                                   #   审查换 glm-4.5-flash（同一把智谱 key）——实测判别 10/10。
    "judge_use_engine": False,    # ★ 生产环境开关：true → **生成 + 审查都走「宿主模型引擎 llm/vlm 档」**
                                   #   （同一个模型走引擎既发言又审查），不再调用任何免费/外部模型、
                                   #   无限流。dev 保持 false（生成走聊天路由、审查走上面的独立强免费模型）。
                                   #   引擎客户端由本包自动从宿主解析（host.engine_llm / 引擎 endpoint），
                                   #   解析不到 → 回落聊天路由，绝不静默改行为。
    "engine_endpoint": "",         # 可选：显式指定引擎网关端点（留空 = 自动从宿主解析，
                                   #   如 host._local_omni_engine.endpoint / _duplex_local_omni_endpoint；
                                   #   默认 https://localhost:8006）。仅在 judge_use_engine=true 时生效。
    "judge_thinking": "false",     # 审查器思考开关：`auto`=不传（厂商默认）/ `false` / `true`。
                                   #   ★ 审查模型若是**推理模型**（glm-4.5 系等）务必 false——
                                   #   否则思维链吃光 max_tokens，正文为空 → 审查静默失效（全放行）。
    "rhythm_cooldown_h": 5.0,      # 生活节律关心冷却
}


def _coerce(value: Any, default: Any) -> Any:
    """按默认值的类型做一次温和转换；失败则退回默认（坏值隔离，不静默改语义）。"""
    if value is None:
        return default
    try:
        if isinstance(default, bool):
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on", "是", "开")
            return bool(value)
        if isinstance(default, int) and not isinstance(default, bool):
            return int(value)
        if isinstance(default, float):
            return float(value)
        return value
    except Exception:  # noqa: BLE001
        logger.warning("[proactive_pack] 配置值 %r 无法转为 %s，回退默认 %r",
                       value, type(default).__name__, default)
        return default


def _read_state_flags(base_dir: Path) -> dict[str, Any]:
    """读运行时状态文件的 flags 段；缺失 / 损坏一律返回 {}（并留痕）。"""
    p = Path(base_dir) / STATE_FILENAME
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            logger.warning("[proactive_pack] 状态文件顶层不是对象，已忽略：%s", p)
            return {}
        flags = d.get("flags")
        return dict(flags) if isinstance(flags, dict) else {}
    except Exception as e:  # noqa: BLE001
        logger.warning("[proactive_pack] 状态文件损坏，已忽略（%s）：%s", p, e)
        return {}


def load_flags(cfg_ns: Optional[Any] = None,
               base_dir: Optional[Path | str] = None) -> dict[str, Any]:
    """合成最终开关表：DEFAULTS ← config.yaml ← 运行时状态文件。

    `cfg_ns` 为 `Config.ns`（点号命名空间树）；传 None 表示跳过 config.yaml 这一级。
    """
    out = dict(DEFAULTS)

    # 第 2 级：config.yaml 的 proactive.proactive_pack
    if cfg_ns is not None:
        try:
            section = getattr(getattr(cfg_ns, "proactive", None), "proactive_pack", None)
            if section:
                for k in DEFAULTS:
                    if hasattr(section, k):
                        out[k] = _coerce(getattr(section, k), DEFAULTS[k])
        except Exception:  # noqa: BLE001
            logger.debug("[proactive_pack] config.yaml 段读取失败，跳过该级", exc_info=True)

    # 第 3 级：运行时状态文件（最高优先级）
    base = Path(base_dir) if base_dir else DEFAULT_BASE_DIR
    state = _read_state_flags(base)
    for k, v in state.items():
        if k in DEFAULTS:
            out[k] = _coerce(v, DEFAULTS[k])
        else:
            out[k] = v          # 允许未来新增键先落地，不做丢弃

    return out


def write_state_flags(flags: dict[str, Any], base_dir: Optional[Path | str] = None) -> bool:
    """把 flags 写进运行时状态文件的 flags 段（其余段原样保留）。返回是否成功。

    供 UI / 桥热切换用；**不碰 config.yaml**。原子写（tmp + replace）。

    ⚠️ **必须是合并写**（2026-10-12 修）：以前是整份替换 `cur["flags"]`，
    于是 UI 只提交它管的那些开关时，会把别的键（如 `judge_provider/judge_model/
    judge_thinking`）**悄悄冲掉**——典型的「多个写者各自 load→改→save，静默丢更新」。
    现在只覆盖本次显式给出的键，其余原样保留。
    """
    base = Path(base_dir) if base_dir else DEFAULT_BASE_DIR
    p = base / STATE_FILENAME
    try:
        base.mkdir(parents=True, exist_ok=True)
        cur: dict[str, Any] = {}
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(d, dict):
                    cur = d
            except Exception:  # noqa: BLE001
                cur = {}
        merged = dict(cur.get("flags") or {}) if isinstance(cur.get("flags"), dict) else {}
        merged.update({k: v for k, v in dict(flags).items()})
        cur["flags"] = merged
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("[proactive_pack] 写入状态文件失败：%s", e)
        return False
