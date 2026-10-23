"""P0 接线自测：走真实 EventBus，验证「对话 → 关系状态 + 历史落盘」跑通。

**不启动 GUI、不加载模型、不碰真实 data/**（全部写在临时目录）。
跑法：
    C:\\Users\\Administrator\\AppData\\Local\\Programs\\Python\\Python312\\python.exe tools\\test_relationship_wiring.py
退出码 0 = 全绿。
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))   # 用 src.xxx 形式导入（reporter 内部有相对导入 ..event_bus）

from src.event_bus import Event, EventBus, EventType  # noqa: E402
from src.companion.relationship_reporter import RelationshipReporter  # noqa: E402
from src.companion.history_store import HistoryStore  # noqa: E402

RESULTS: list[tuple[bool, str]] = []


def check(cond: bool, name: str) -> None:
    RESULTS.append((bool(cond), name))
    print(("[PASS] " if cond else "[FAIL] ") + name)


class FakeCfg:
    """最小替身：只提供 reporter 用到的两个属性。"""

    def __init__(self, char_id: str = "danya") -> None:
        self.active_character_id = char_id
        self.ns = types.SimpleNamespace()      # 没有 relationship 节点 → 默认启用


class LogCatcher(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(record.getMessage())
        except Exception:  # noqa: BLE001
            pass


async def scenario(tmp: Path) -> None:
    bus = EventBus()
    cfg = FakeCfg()
    rep = RelationshipReporter(bus, cfg, base_dir=tmp)

    catcher = LogCatcher()
    lg = logging.getLogger("src.companion.relationship_reporter")
    lg.addHandler(catcher)
    lg.setLevel(logging.INFO)

    rep.subscribe()
    rep.start()
    bus_task = asyncio.create_task(bus.run())

    # 第一轮：普通聊天（较长、带语气词）
    await bus.publish(Event(EventType.USER_TEXT, {"text": "今天好累啊，写了一天代码总算弄完了"}, "chat"))
    await bus.publish(Event(EventType.AI_DECISION, {"text": "辛苦啦，那早点休息", "emotion": "gentle"}, "dialogue"))
    await asyncio.sleep(0.2)

    # 第二轮：主动关心她
    await bus.publish(Event(EventType.USER_TEXT, {"text": "你怎么样呀，今天还好吗？"}, "chat"))
    await bus.publish(Event(EventType.AI_DECISION, {"text": "我挺好的呀", "emotion": "happy"}, "dialogue"))
    await asyncio.sleep(0.2)

    # 第三轮：她在聊天页主动开口（源码 _publish_proactive → source='dialogue' + is_proactive）
    #         应记 initiative，且不推进状态
    await bus.publish(Event(EventType.AI_DECISION,
                            {"text": "在忙什么呢", "emotion": "curious", "is_proactive": True}, "dialogue"))
    await asyncio.sleep(0.2)

    # 第三轮补：双工自言自语（source='proactive'）——**必须被排除**：
    #   不进聊天历史、不记 initiative（双工是另一种介质，有独立通话记录）
    await bus.publish(Event(EventType.AI_DECISION,
                            {"text": "桌面好乱呀，是工作忙还是找不着了？", "emotion": "curious",
                             "is_proactive": True, "source": "proactive"}, "proactive"))
    await asyncio.sleep(0.2)

    # 第四轮：敷衍（**真实流式路径**：最终整句带 streamed=True —— 这条曾被误当分片过滤掉！）
    await bus.publish(Event(EventType.USER_TEXT, {"text": "嗯"}, "chat"))
    await bus.publish(Event(EventType.AI_DECISION,
                            {"text": "唔……只是回了一个嗯字吗", "emotion": "neutral",
                             "streamed": True}, "dialogue"))
    await asyncio.sleep(0.2)
    # 重复投递同一句 → 应被去重，不重复计轮
    await bus.publish(Event(EventType.AI_DECISION,
                            {"text": "唔……只是回了一个嗯字吗", "emotion": "neutral",
                             "streamed": True}, "dialogue"))
    await asyncio.sleep(0.2)

    # 角色切换
    await bus.publish(Event(EventType.CHARACTER_SWITCHED, {"char_id": "other", "char_name": "别的角色"}, "dialogue"))
    await asyncio.sleep(0.2)

    bus.stop()
    bus_task.cancel()
    try:
        await bus_task
    except asyncio.CancelledError:
        # 取消后台总线任务 = 场景正常收尾
        pass

    # ---------------- 断言 ----------------
    hs = HistoryStore(base_dir=tmp)
    # 7 条 = 2 轮问答(4) + 聊天页主动那句(1) + 敷衍那轮(2，含流式最终整句)
    # （双工自言自语不计入；重复投递的那句被去重）
    check(hs.count("danya") == 7, f"历史已落盘 7 条（实际 {hs.count('danya')}）")
    texts = [str(t.get("content")) for t in hs.load_recent("danya", n=50)]
    check("桌面好乱呀，是工作忙还是找不着了？" not in texts, "双工自言自语不入聊天历史")
    check("在忙什么呢" in texts, "聊天页主动开口入历史")
    check(texts.count("唔……只是回了一个嗯字吗") == 1,
          f"流式最终整句落历史且只记一次（实际 {texts.count('唔……只是回了一个嗯字吗')} 次）")

    rel = tmp / "affinity" / "danya.json"
    check(rel.exists(), "关系状态文件已生成 data/affinity/danya.json")
    st = json.loads(rel.read_text(encoding="utf-8"))
    check(float(st["affinity"]["trust"]) > 50.0,
          f"聊得好 → 信任上升（{st['affinity']['trust']:.1f} > 50）")
    check(int(st.get("turns", 0)) == 3, f"计数 3 轮真实互动（实际 {st.get('turns')}）")
    check(bool(st.get("last_her_initiative_ts")), "她主动开口已记录")
    # 【2026-10-21 修·白名单抄的是旧版】原来手写枚举
    # ("雀跃","平静","想你","闹别扭","蔫蔫的","炸毛") —— 那正是 v1 的
    # relationship.MOODS，而运行时用的是 relationship_v2（17 种，含「困倦」「心动」
    # 「安心」「柔软」「感动」「哭笑不得」「委屈」「失落」「不安」「吃醋」「心疼」）。
    # 于是**凌晨跑出「困倦」就红、早上跑出「雀跃」就绿** —— 判据依赖时间，成了偶发红。
    # 改为直接取产品侧的权威集合：具体有哪些心情由 MOODS_V2 自己守，这里只守
    # "产出的 label 必须在合法集合里"。
    from src.companion.relationship_v2 import MOODS_V2
    check(st["mood"]["label"] in MOODS_V2,
          f"心情值合法（{st['mood']['label']}）")

    other = tmp / "affinity" / "other.json"
    check(other.exists(), "切换角色后新角色状态已生成（角色隔离）")

    rel_logs = [l for l in catcher.lines if l.startswith("[关系]")]
    check(len(rel_logs) >= 4, f"日志打了 [关系] 行（{len(rel_logs)} 行）")
    check(any("恢复" in l for l in rel_logs), "启动时有『恢复』日志（可证明重启不丢）")
    if rel_logs:
        print("\n    日志样例：")
        for l in rel_logs[:4]:
            print("      " + l)


async def scenario_disabled(tmp: Path) -> None:
    bus = EventBus()
    cfg = FakeCfg()
    rep = RelationshipReporter(bus, cfg, enabled=False, base_dir=tmp)
    rep.subscribe()
    rep.start()
    t = asyncio.create_task(bus.run())
    await bus.publish(Event(EventType.USER_TEXT, {"text": "你好"}, "chat"))
    await bus.publish(Event(EventType.AI_DECISION, {"text": "嗯", "emotion": "neutral"}, "dialogue"))
    await asyncio.sleep(0.2)
    bus.stop()
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        # 同上：取消是预期路径
        pass
    check(HistoryStore(base_dir=tmp).count("danya") == 0, "关闭开关后不写任何数据")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="relwire_"))
    print("P0 接线自测（真实事件总线，不启动 GUI）\n")
    asyncio.run(scenario(tmp))
    asyncio.run(scenario_disabled(tmp / "off"))

    passed = sum(1 for ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n===== {passed}/{total} 通过 =====")
    if passed != total:
        print("失败项：")
        for ok, name in RESULTS:
            if not ok:
                print("  - " + name)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
