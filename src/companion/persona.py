"""人设：从配置加载伴侣角色设定，支持运行时切换。"""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Optional

from ..config import Config


@dataclass
class Persona:
    name: str
    character: str
    speaking_style: str
    background: str
    appearance: str = ""
    call_instructions: str = ""  # 【2026-09-16 测试】按角色单独追加的通话提示词（仅测试角色配置，全局 danya 留空即不注入）

    @classmethod
    def from_config(cls, cfg: Config) -> "Persona":
        """从当前激活角色配置加载。"""
        char = cfg.get_active_character()
        if char is None:
            # 降级：空配置
            return cls(name="AI", character="AI助手", speaking_style="中文", background="")
        return cls.from_character_config(char)

    @classmethod
    def from_character_config(cls, char: SimpleNamespace) -> "Persona":
        """从角色配置对象创建。"""
        return cls(
            name=char.name,
            character=char.character,
            speaking_style=char.speaking_style,
            background=char.background,
            appearance=getattr(char, "appearance", ""),
            call_instructions=getattr(char, "call_instructions", ""),
        )

    def update_from_character(self, char: SimpleNamespace) -> None:
        """从角色配置更新当前属性（运行时切换）。"""
        self.name = char.name
        self.character = char.character
        self.speaking_style = char.speaking_style
        self.background = char.background
        self.appearance = getattr(char, "appearance", "")
        self.call_instructions = getattr(char, "call_instructions", "")
