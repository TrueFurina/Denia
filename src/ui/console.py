"""控制台界面：rich 彩色输出思考过程，并接收文字输入。

支持命令:
- /角色 查看角色列表
- /切换 <id> 切换角色
无麦克风时控制台是主输入；有麦克风时控制台仍可输入（与语音并存）。
"""
from __future__ import annotations

import asyncio
import logging

from rich.console import Console as RichConsole
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..event_bus import EventBus, Event, EventType

logger = logging.getLogger(__name__)


class ConsoleUI:
    def __init__(self, bus: EventBus, persona_name: str = "达妮娅",
                 dialogue=None, tts=None) -> None:
        self._bus = bus
        self._persona = persona_name
        self._rich = RichConsole()
        self._running = True
        self._dialogue = dialogue
        self._tts = tts

    # ---- 启动横幅 ----
    def banner(self, status) -> None:
        self._rich.rule(f"[bold magenta]{status.name if hasattr(status, 'name') else 'AI桌面伴侣'}[/] 上线啦~")
        lines: list[str] = []
        lines.append(f"对话模型: {'✓ 视觉+对话' if status.vision_model_ok else '✓ 纯文本(降级)' if status.fallback_model_ok else '✗ 不可用'}")
        lines.append(f"语音唤醒: {'✓' if status.voice_enabled else '✗ 禁用(纯文字模式)'}")
        lines.append(f"屏幕感知: {'✓' if status.vision_enabled else '✗ 禁用'}")
        if status.degradations:
            lines.append("降级提示:")
            for d in status.degradations:
                lines.append(f"  ⚠ {d}")
        self._rich.print(Panel("\n".join(lines), title="运行状态", border_style="cyan"))
        self._rich.print("[dim]控制台输入文字对话，输入 exit 退出。[/]")
        self._rich.print("[dim]命令: /角色 查看角色列表，/切换 <id> 切换角色[/]")

    # ---- 订阅与输入循环 ----
    def subscribe(self) -> None:
        if getattr(self, "_subscribed", False):
            return
        self._subscribed = True
        self._bus.subscribe(EventType.AI_DECISION, self._on_decision)
        self._bus.subscribe(EventType.STATE_CHANGE, self._on_state)
        self._bus.subscribe(EventType.SCREEN_ACTIVITY, self._on_perception)
        self._bus.subscribe(EventType.WAKE_WORD, self._on_wake)
        self._bus.subscribe(EventType.USER_TEXT, self._on_user_text_echo)
        self._bus.subscribe(EventType.CHARACTER_SWITCHED, self._on_character_switched)
        self._bus.subscribe(EventType.ERROR, self._on_error)

    async def input_loop(self) -> bool:
        """控制台输入循环。

        返回 True 表示用户显式输入 exit/quit/退出；
        返回 False 表示 EOF / Ctrl+C / 异常退出（非用户主动）。
        调用方据此决定是否连带关闭 GUI。
        """
        self.subscribe()
        await asyncio.sleep(0.2)
        explicit = False
        while self._running:
            try:
                text = await asyncio.to_thread(input, "\n你 > ")
            except (EOFError, KeyboardInterrupt):
                break
            text = text.strip()
            if not text:
                continue
            if text.lower() in ("exit", "quit", "退出"):
                self._rich.print("[dim]再见~[/]")
                self._running = False
                explicit = True
                break
            # 处理命令
            if text.startswith("/"):
                await self._handle_command(text)
            else:
                await self._bus.publish(Event(EventType.USER_TEXT, {"text": text, "source": "console"}))
        return explicit

    def stop(self) -> None:
        self._running = False

    async def _handle_command(self, text: str) -> None:
        """处理控制台命令。"""
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        if cmd in ("/角色", "/characters", "/list"):
            self._show_characters()
        elif cmd in ("/切换", "/switch"):
            if not args:
                self._rich.print("[yellow]用法: /切换 <角色ID>[/]")
                return
            await self._switch_character(args.strip())
        else:
            self._rich.print(f"[yellow]未知命令: {cmd}[/]")

    def _show_characters(self) -> None:
        """显示角色列表。"""
        if not self._dialogue:
            self._rich.print("[red]Dialogue 未初始化[/]")
            return

        chars = self._dialogue.list_characters()
        active_id = self._dialogue.current_character_id

        table = Table(title="可用角色", show_header=True, header_style="magenta")
        table.add_column("ID", style="cyan")
        table.add_column("名称", style="green")
        table.add_column("人设摘要", max_width=40)
        table.add_column("音色")
        table.add_column("状态", justify="center")

        for ch in chars:
            status = "[green]★ 当前[/]" if ch["is_active"] else "[dim]待切换[/]"
            table.add_row(
                ch["id"],
                ch["name"],
                ch["character"],
                f"{ch['voice_engine']}: {ch['voice_name']}",
                status,
            )

        self._rich.print(table)

    async def _switch_character(self, char_id: str) -> None:
        """切换角色。"""
        if not self._dialogue:
            self._rich.print("[red]Dialogue 未初始化[/]")
            return

        success = await self._dialogue.switch_character(char_id)
        if success:
            char = self._dialogue.current_character_name
            self._persona = char
            self._rich.print(f"[green]✓ 已切换到角色: {char}[/]")
        else:
            self._rich.print(f"[red]✗ 切换失败，角色不存在: {char_id}[/]")

    # ---- 事件展示 ----
    def log_thinking(self, msg: str) -> None:
        self._rich.print(f"[dim]💭 {msg}[/]")

    async def _on_decision(self, e: Event) -> None:
        d = e.data or {}
        if d.get("is_proactive"):
            # 主动搭话：AI 自己说的话（不再走 LLM），用 💗 标记
            trigger_txt = {"screen_batch": "屏幕累计", "idle": "空闲关心",
                           "screen_change": "屏幕变化", "moment": "游戏时刻"}.get(
                d.get("trigger", ""), "主动互动")
            title = f"{self._persona} · 💗 主动·{trigger_txt} · 情绪={d.get('emotion','neutral')}"
        else:
            title = f"{self._persona} · 情绪={d.get('emotion','neutral')} 动作={d.get('action','idle')}"
        body = Text(d.get("text", ""), style="magenta")
        self._rich.print(Panel(body, title=title, border_style="magenta", expand=False))

    async def _on_state(self, e: Event) -> None:
        d = e.data or {}
        self._rich.print(f"[cyan]⟿ 状态: {d.get('from','')} → {d.get('to','')}[/]")

    async def _on_perception(self, e: Event) -> None:
        d = e.data or {}
        if isinstance(d, dict) and d.get("summary"):
            hint = f" ({d.get('app_hint')})" if d.get("app_hint") else ""
            moment = f" [yellow]时刻:{d['moment']}[/]" if d.get("moment") else ""
            self._rich.print(f"[blue]👀 感知: {d['summary']}{hint}{moment}[/]")

    async def _on_wake(self, e: Event) -> None:
        d = e.data or {}
        self._rich.print(f"[yellow]🎙️ 唤醒词命中: {d.get('word','')}[/]")

    async def _on_user_text_echo(self, e: Event) -> None:
        d = e.data or {}
        source = d.get("source", "")
        if source == "voice":
            self._rich.print(f"[green]🎙️ 你说: {d.get('text','')}[/]")
        # 主动搭话不再在此回显，统一走 _on_decision 的 💗 面板展示

    async def _on_character_switched(self, e: Event) -> None:
        d = e.data or {}
        self._rich.print(f"[cyan]✨ 角色切换: {d.get('char_name','')} [{d.get('char_id','')}][/]")

    async def _on_error(self, e: Event) -> None:
        d = e.data or {}
        self._rich.print(f"[red]⚠ [{d.get('code','ERR')}] {d.get('message','')}[/]")
