# -*- coding: utf-8 -*-
"""桌宠窗 js_api（自 CharacterAPI 抽出，2026-10-17）。

PetAPI 是 CharacterAPI 的 mixin：方法体未改动，通过 self 访问实例属性
（__init__ 仍由 CharacterAPI 提供）与同域方法；跨域调用（pet_open_main →
recreate_main_window）也走 self，因此实例化时必须与其他 mixin 组合。
"""
from __future__ import annotations

import ctypes as _ct
import logging
from ctypes import wintypes as _wt

from ..event_bus import Event, EventType
from ..queue_util import put_drop_oldest
from .pet_window import (
    _POINT, _pet_apply_topmost, _pet_find_hwnd, _pet_install_topmost_guard,
    _pet_set_capture_hidden, _u32,
)
from .ui_state import STATE

logger = logging.getLogger("src.ui.webapp")


class PetAPI:
    """桌宠窗口相关的前端桥方法。"""


    def pet_show(self) -> dict:
        """显示桌面桌宠浮窗（透明 / 无边框 / 置顶）。"""
        if self._pet_win is None:
            return {"ok": False, "error": "桌宠窗口未初始化"}
        try:
            self._pet_win.show()
            # 刷新位置缓存（拖动时按增量移动，避免每次查询窗口位置）
            try:
                self._pet_pos = (int(self._pet_win.x), int(self._pet_win.y))
            except Exception:  # noqa: BLE001
                self._pet_pos = None
            self._pet_visible = True
            logger.info("桌宠已显示")
            return {"ok": True, "visible": True}
        except Exception as e:  # noqa: BLE001
            logger.warning("显示桌宠失败: %s", e)
            return {"ok": False, "error": str(e)}

    def pet_hide(self) -> dict:
        """隐藏桌面桌宠浮窗。"""
        if self._pet_win is None:
            return {"ok": False, "error": "桌宠窗口未初始化"}
        try:
            self._pet_win.hide()
            self._pet_visible = False
            logger.info("桌宠已隐藏")
            return {"ok": True, "visible": False}
        except Exception as e:  # noqa: BLE001
            logger.warning("隐藏桌宠失败: %s", e)
            return {"ok": False, "error": str(e)}

    def pet_toggle_always_on_top(self) -> dict:
        """切换桌宠窗口置顶（直接用 pywebview 原生 window.on_top 控 Form.TopMost）。

        为什么不用裸 SetWindowPos + 读 WS_EX_TOPMOST：本窗是 frameless+transparent 的
        WebView2 窗，WS_EX_TOPMOST 样式读取/设置在该类窗上不稳定（实测出现"窗口已钉但
        读不到 WS_EX_TOPMOST、取消不掉"）。pywebview 的 on_top 直接操作正确的窗口对象，
        取消 100% 生效、且 window.on_top 可直读真实状态 → 右键文案与窗口永远一致。
        钉上时仍起每 1s 保活线程重新 on_top=True，恒盖过音乐软件等其它 TopMost 窗口。
        """
        try:
            STATE.pet_topmost = not STATE.pet_topmost
            if STATE.pet_topmost:
                _pet_install_topmost_guard()   # 钉最前：启动每 1s 保活
            else:
                STATE.pet_keepalive = False       # 关保活线程
            _pet_apply_topmost()
            # 直读 window.on_top 真实状态（不再读 WS_EX_TOPMOST）
            on_top = STATE.pet_topmost
            if self._pet_win is not None:
                try:
                    on_top = bool(self._pet_win.on_top)
                except Exception:  # noqa: BLE001
                    pass
            logger.info("桌宠置顶: %s (win_on_top=%s)", STATE.pet_topmost, on_top)
            return {"ok": True, "on_top": on_top}
        except Exception as e:  # noqa: BLE001
            logger.warning("切换桌宠置顶失败: %s", e)
            return {"ok": False, "error": str(e), "on_top": STATE.pet_topmost}

    def pet_is_on_top(self) -> dict:
        """返回桌宠当前是否真的置顶（直读 window.on_top 真实状态，而非 Python 标志）。"""
        try:
            if self._pet_win is not None:
                return {"ok": True, "on_top": bool(self._pet_win.on_top)}
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True, "on_top": STATE.pet_topmost}

    def pet_set_capture_hidden(self, hidden: bool) -> dict:
        """切换桌宠对屏幕捕获是否隐身（截图/录屏/共享都抓不到；自己屏上照常可见）。"""
        try:
            ok = _pet_set_capture_hidden(bool(hidden))
            return {"ok": ok, "hidden": STATE.pet_capture_hidden}
        except Exception as e:  # noqa: BLE001
            logger.warning("切换桌宠截屏隐身失败: %s", e)
            return {"ok": False, "error": str(e)}

    def pet_is_capture_hidden(self) -> dict:
        """返回桌宠是否对屏幕捕获隐身（供右键菜单显示真实状态）。"""
        return {"ok": True, "hidden": STATE.pet_capture_hidden}

    def pet_toggle(self) -> dict:
        """切换桌宠显示/隐藏。"""
        if self._pet_win is None:
            return {"ok": False, "error": "桌宠窗口未初始化"}
        try:
            if self._pet_visible:
                return self.pet_hide()
            return self.pet_show()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def pet_is_visible(self) -> bool:
        """桌宠窗口当前是否可见。"""
        return self._pet_visible

    def pet_drag_start(self) -> dict:
        """桌宠拖动会话开始：用 Win32 物理像素记录窗口基准 + 光标基准。

        前端 mousedown 一次；后续 pet_move 完全在物理像素空间用 GetCursorPos 增量
        定位，彻底避开 pywebview 逻辑像素(x/y 滞后且不随 SetWindowPos 更新)与
        SetWindowPos 物理像素之间的 DPI 缩放错位——这正是"按下即瞬移/拖飞"的根因。
        """
        if self._pet_win is None:
            return {"ok": False, "error": "桌宠窗口未初始化"}
        try:
            hwnd = _pet_find_hwnd()
            bx, by = 0, 0
            if hwnd:
                r = _wt.RECT()
                if _u32.GetWindowRect(hwnd, _ct.byref(r)):
                    bx, by = r.left, r.top
            cur = _POINT()
            _u32.GetCursorPos(_ct.byref(cur))
            self._pet_base = (bx, by)
            self._pet_cursor0 = (cur.x, cur.y)
            self._pet_dragging = True
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            logger.warning("记录桌宠拖动基准失败: %s", e)
            return {"ok": False, "error": str(e)}

    def pet_drag_end(self) -> dict:
        """拖动会话结束：复位拖动标志，并把最终位置**落盘**（mouseup 时前端调用）。

        【阶段 A4】位置以前只在内存里（`_pet_pos` 是个 tuple），进程一退就没了，
        用户每次开机都得重新摆人。这里只在拖动结束时写一次——拖拽过程中
        `pet_move` 是高频调用，逐帧写盘没必要。
        """
        try:
            self._pet_dragging = False
        except Exception:  # noqa: BLE001
            pass
        try:
            from .pet_state import save_physical

            hwnd = _pet_find_hwnd()
            if hwnd:
                r = _wt.RECT()
                if _u32.GetWindowRect(hwnd, _ct.byref(r)):
                    save_physical(r.left, r.top,
                                  r.right - r.left, r.bottom - r.top)
        except Exception as _e:  # noqa: BLE001  存盘失败绝不能影响拖放手感
            logger.debug("[桌宠] 拖动结束存盘失败：%s", _e)
        return {"ok": True}

    def pet_move(self, dx: float = 0, dy: float = 0) -> dict:
        """拖动中绝对定位：窗口新位置 = 基准(物理) + 光标物理位移，抓取点零漂移。

        dx/dy 不再使用（由后端用 GetCursorPos 自行计算，保证与基准同处物理像素空间，
        在 4K/150% 等缩放下也 1:1 跟手、按下不瞬移）。
        SWP_NOSIZE|NOZORDER|NOACTIVATE 保持尺寸/置顶/不抢焦点——拖动不影响置顶状态。
        """
        if self._pet_win is None:
            return {"ok": False, "error": "桌宠窗口未初始化"}
        try:
            if not getattr(self, "_pet_dragging", False) or self._pet_base is None:
                return {"ok": False, "error": "未进入拖动"}
            bx, by = self._pet_base
            cx0, cy0 = self._pet_cursor0
            cur = _POINT()
            _u32.GetCursorPos(_ct.byref(cur))
            nx = bx + (cur.x - cx0)
            ny = by + (cur.y - cy0)
            hwnd = _pet_find_hwnd()
            if hwnd:
                _u32.SetWindowPos(hwnd, 0, nx, ny, 0, 0, 0x0001 | 0x0004 | 0x0010)
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            logger.warning("移动桌宠失败: %s", e)
            return {"ok": False, "error": str(e)}

    def pet_hit_rect(self, rects: list) -> dict:
        """前端上报角色命中矩形（窗口客户区物理像素矩形列表）→ 窗体层画矩形提供命中。

        结论：TransparencyKey 分层窗口命中只认"窗体自绘非 key 像素"（WebView2
        内容不计入），因此角色矩形(非品红)画到窗体层——被角色像素覆盖视觉不可见、
        又提供命中；矩形外品红 key 色系统自动挖空+点击穿透。前端 reportPetBody
        在模型渲染稳定后一次性用 gl.readPixels 算角色包围盒（固定矩形，覆盖整个
        人物），此后不再计算上报，避免逐像素抠像的计算负担和晃动时卡黑边。
        """
        try:
            logger.debug("[PET] 命中形状上报: %d 个矩形", len(rects) if rects else 0)
            native = getattr(self._pet_win, "native", None)
            if native is not None and hasattr(native, "set_pet_hit_rect"):
                native.set_pet_hit_rect(rects)
        except Exception as e:  # noqa: BLE001
            logger.warning("更新桌宠命中区失败: %s", e)
        return {"ok": True}

    def pet_open_main(self) -> dict:
        """回到主窗口：隐藏桌宠并恢复/聚焦主窗口；主窗口已关闭时自动重建。"""
        if self._pet_win is not None:
            try:
                self._pet_win.hide()
                self._pet_visible = False
            except Exception:  # noqa: BLE001
                pass

        win = self._main_win
        if win is None or self._main_win_closed:
            win = self.recreate_main_window()
        if win is not None:
            try:
                # pywebview show() 内部会 Show()+Activate()，即带回前台聚焦
                win.show()
                self.main_ui_expected_visible = True   # 窗口守护：恢复显示即重置期望
                return {"ok": True}
            except Exception as e:  # noqa: BLE001
                logger.warning("恢复主窗口失败: %s", e)
        return {"ok": True, "note": "主窗口不可恢复（可能已关闭）"}

    def pet_interact(self, context: str = "click") -> dict:
        """桌宠互动：触发AI生成个性化对话和动作。
        
        Args:
            context: 互动上下文，如 "click", "double_click", "drag" 等
            
        Returns:
            {"success": bool, "text": str, "action": str, "expression": str}
        """
        if self._dialogue is None:
            return {"success": False, "error": "对话系统未注入"}
        if self._bus is None:
            return {"success": False, "error": "事件总线未注入"}
        if self._loop is None or not self._loop.is_running():
            return {"success": False, "error": "主事件循环未运行"}
        
        try:
            # 检查缓存
            import time
            now = time.time()
            cache_key = self._generate_cache_key(context)
            
            # 检查是否有有效的缓存
            cached_response = self._get_cached_response(cache_key, now)
            if cached_response:
                # 使用缓存响应，不记录到历史（避免重复）
                self._add_to_conversation_history(context, cached_response['text'], 
                                                cached_response['action'], cached_response['expression'])
                
                # 发送缓存响应事件到总线
                async def _publish_cached():
                    await self._bus.publish(Event(EventType.AI_DECISION, {
                        "text": cached_response['text'],
                        "action": cached_response['action'],
                        "emotion": cached_response['expression'],
                        "source": "pet_interaction_cached",
                        "user_activity": f"桌宠{context}互动（缓存）",
                    }, "webapp"))
                
                self._run_coro(_publish_cached())
                
                return {
                    "success": True,
                    "text": cached_response['text'],
                    "action": cached_response['action'],
                    "expression": cached_response['expression'],
                    "cached": True
                }
            
            # 更新互动时间
            self._pet_last_interaction = now
            
            # 构建包含上下文的提示词
            prompt = self._build_pet_interaction_prompt_with_context(context)
            
            # 异步调用AI生成对话
            async def _generate_response():
                # 构建丰富的上下文信息
                temp_context = {
                    "type": context,
                    "character": getattr(self._cfg, 'character', '达妮娅'),
                    "personality": getattr(self._cfg, 'personality', '慵懒俏皮'),
                    "time_of_day": self._get_time_of_day(),
                    "interaction_count": len(self._pet_conversation_history) + 1,
                    "recent_interactions": self._get_recent_interactions(3),  # 最近3次互动
                    "user_activity": f"桌宠{context}互动",
                }
                
                # 调用对话系统
                if hasattr(self._dialogue, 'generate_response'):
                    response = await self._dialogue.generate_response(
                        user_message=prompt,
                        context=temp_context,
                        mode="pet_interaction"
                    )
                    return response
                else:
                    # 回退到简单响应
                    return self._get_fallback_pet_response(context)
            
            # 投递到主线程执行
            response = self._run_coro(_generate_response())
            
            if response and isinstance(response, dict):
                # 解析响应并返回结构化数据
                text = response.get('text', '')
                action = response.get('action', '')
                # 【2026-10-11】generate_response 返回的主键是 `emotion`（`expression` 为兼容
                #   别名）；此前只读 expression ⇒ 表情会丢成空串（实测泄漏条目 emotion=''）。
                expression = response.get('expression') or response.get('emotion') or ''
                # 桌宠气泡同样会上屏 + 被 TTS 念：去动作/场景描写 + 剥 markdown，
                #   别把「（懒洋洋地翻了个身）」这类旁白念出来；顺带兜一道万一漏进来的 JSON 残骸。
                if self._dialogue is not None:
                    try:
                        text = self._dialogue._strip_markdown(text)
                        text = self._dialogue._strip_stage_directions(text)
                    except Exception:  # noqa: BLE001
                        logger.debug("桌宠文本清理失败（忽略）", exc_info=True)
                
                # 缓存响应
                self._cache_response(cache_key, response, now)
                
                # 记录到对话历史
                self._add_to_conversation_history(context, text, action, expression)
                
                # 发送互动事件到总线
                async def _publish_interaction():
                    await self._bus.publish(Event(EventType.AI_DECISION, {
                        "text": text,
                        "action": action,
                        "emotion": expression,
                        "source": "pet_interaction",
                        "user_activity": f"桌宠{context}互动",
                    }, "webapp"))
                
                self._run_coro(_publish_interaction())
                
                return {
                    "success": True,
                    "text": text,
                    "action": action,
                    "expression": expression,
                    "cached": False
                }
            else:
                # AI响应失败，使用回退对话
                fallback = self._get_fallback_pet_response(context)
                self._cache_response(cache_key, fallback, now)
                self._add_to_conversation_history(context, fallback['text'], fallback['action'], fallback['expression'])
                return {
                    "success": True,
                    "text": fallback['text'],
                    "action": fallback['action'],
                    "expression": fallback['expression'],
                    "cached": False
                }
                
        except Exception as e:
            logger.exception("桌宠互动失败")
            # 失败时使用回退对话
            fallback = self._get_fallback_pet_response(context)
            self._add_to_conversation_history(context, fallback['text'], fallback['action'], fallback['expression'])
            return {
                "success": True,
                "text": fallback['text'],
                "action": fallback['action'],
                "expression": fallback['expression'],
                "note": f"AI响应失败，使用回退对话: {str(e)}"
            }

    def clear_pet_cache(self):
        """清空桌宠响应缓存。"""
        self._pet_response_cache.clear()
        logger.info("桌宠响应缓存已清空")

    def _build_pet_interaction_prompt_with_context(self, context: str) -> str:
        """构建包含上下文的桌宠互动提示词。"""
        character = getattr(self._cfg, 'character', '达妮娅')
        personality = getattr(self._cfg, 'personality', '慵懒俏皮')
        
        # 基础提示词模板
        base_prompts = {
            "click": f"""
你是{character}，性格{personality}。用户点击了你，你应该做出可爱的反应。
最近互动：{self._get_recent_interactions_summary()}
当前时间：{self._get_time_of_day()}
请返回JSON格式的响应：
{{
    "text": "你说的话",
    "action": "动作名称（如nod, shake, thinking等）",
    "emotion": "表情名称（如smile, surprise, curious等）"
}}""",
            "double_click": f"""
你是{character}，性格{personality}。用户双击了你，表现出惊喜和喜悦。
最近互动：{self._get_recent_interactions_summary()}
当前时间：{self._get_time_of_day()}
请返回JSON格式的响应：
{{
    "text": "惊喜的话",
    "action": "惊讶动作（如surprised, cheer等）",
    "emotion": "惊讶表情（如surprise等）"
}}""",
            "drag": f"""
你是{character}，性格{personality}。用户拖动你，你感到有些紧张但又配合，应该表现出轻微摇摆和闭眼的反应。
最近互动：{self._get_recent_interactions_summary()}
当前时间：{self._get_time_of_day()}
请返回JSON格式的响应：
{{
    "text": "被拖动时紧张的话",
    "action": "下半身摇摆（sway）",
    "emotion": "紧张表情（sleepy）"
}}"""
        }
        
        return base_prompts.get(context, base_prompts["click"])

    def _build_pet_interaction_prompt(self, context: str) -> str:
        """构建桌宠互动的提示词。"""
        character = getattr(self._cfg, 'character', '达妮娅')
        personality = getattr(self._cfg, 'personality', '慵懒俏皮')
        
        prompts = {
            "click": f"{character}被用户点击了一下，{personality}的性格，应该做出什么反应？请返回JSON格式：{{'text': '对话内容', 'action': '动作名称', 'expression': '表情名称'}}",
            "double_click": f"{character}被用户双击，表现出惊讶和喜悦，{personality}的性格，应该说什么做什么？请返回JSON格式：{{'text': '惊喜的对话', 'action': '惊讶动作', 'expression': '惊讶表情'}}",
            "drag": f"{character}被用户拖动，{personality}的性格，应该有什么反应？请返回JSON格式：{{'text': '被拖动的对话', 'action': '轻微摆动', 'expression': '中性表情'}}",
        }
        
        return prompts.get(context, prompts["click"])

    def _get_fallback_pet_response(self, context: str) -> dict:
        """获取回退的桌宠互动响应（增加多样性）。"""
        # 根据互动历史选择不同的回应，避免重复
        recent_texts = [h['text'] for h in self._pet_conversation_history[-3:] if h['context'] == context]
        
        # 点击互动的多样化回应
        click_responses = [
            {"text": "哎呀，被你戳到了～", "action": "nod", "expression": "smile"},
            {"text": "嗯？叫我有什么事吗？", "action": "thinking", "expression": "curious"},
            {"text": "嘿嘿，被你发现啦～", "action": "wink", "expression": "winkL"},
            {"text": "啊！好痒～", "action": "giggle", "expression": "smile"},
            {"text": "你在玩我吗？", "action": "pout", "expression": "neutral"},
            {"text": "戳我干嘛呀～", "action": "head_tilt", "expression": "curious"},
            {"text": "嘻嘻，被你戳到啦～", "action": "bounce", "expression": "smile"},
            {"text": "哎呀，别戳了啦～", "action": "shake_head", "expression": "smile"}
        ]
        
        # 双击互动的多样化回应
        double_click_responses = [
            {"text": "哇！吓我一跳～", "action": "surprised", "expression": "surprise"},
            {"text": "呀！你双击我干嘛？", "action": "jump", "expression": "surprise"},
            {"text": "哇哦！好惊喜～", "action": "cheer", "expression": "smile"},
            {"text": "啊！好突然～", "action": "blink", "expression": "surprise"},
            {"text": "嘻嘻，好激动～", "action": "excited", "expression": "smile"},
            {"text": "哇！你好热情～", "action": "wave", "expression": "smile"}
        ]
        
        # 拖拽互动的多样化回应
        drag_responses = [
            {"text": "嘿嘿，你在带我玩吗～", "action": "thinking", "expression": "curious"},
            {"text": "哇！你在带我飞～", "action": "sway", "expression": "sleepy"},
            {"text": "好紧张呀～别掉下去～", "action": "sway", "expression": "sleepy"},
            {"text": "嘻嘻，好刺激～", "action": "excited", "expression": "smile"},
            {"text": "你要带我去哪里呀～", "action": "sway", "expression": "curious"},
            {"text": "哇！好高呀～", "action": "sway", "expression": "surprise"}
        ]
        
        # 根据上下文选择响应池
        response_pools = {
            "click": click_responses,
            "double_click": double_click_responses,
            "drag": drag_responses
        }
        
        pool = response_pools.get(context, click_responses)
        
        # 避免重复使用最近的回应
        available_responses = [r for r in pool if r['text'] not in recent_texts]
        
        # 如果没有可用的（避免重复），随机选择
        if available_responses:
            import random
            return random.choice(available_responses)
        else:
            return random.choice(pool)
        
        # 默认回应（理论上不会执行到这里）
        default_responses = {
            "click": {"text": "在呢～有什么事吗？", "action": "nod", "expression": "smile"},
            "double_click": {"text": "哇哦！", "action": "surprised", "expression": "surprise"},
            "drag": {"text": "嘿嘿～", "action": "sway", "expression": "curious"}
        }
        
        return default_responses.get(context, default_responses["click"])

    def _pet_bubble(self, text: str) -> None:
        """把双工看屏互动文本推入桌宠气泡队列（由桌宠窗轮询取走显示）。

        不在这里直接 evaluate_js 打桌宠窗：pywebview 的 evaluate_js 必须在 GUI 线程
        调用，而本回调运行在事件总线/双工线程，跨线程 evaluate_js 经实测不可靠、气泡
        会不弹（见 webapp.py:5279 注释）。改用"桌宠窗主动轮询 window.pywebview.api
        .pop_pet_bubble"——与点击 pet_interact 是同一条已验证可用的通道，彻底规避
        跨线程注入与 WebView2 消息事件的坑。
        """
        try:
            # 有界投递：桌宠窗未轮询时丢最旧，避免长时间后台后积压成内存增长
            put_drop_oldest(self._pet_bubble_q, text)
        except Exception:  # noqa: BLE001
            pass

    def pop_pet_bubble(self) -> str:
        """桌宠窗轮询取走待显示的气泡文本（window.pywebview.api 调用）。

        返回队列中最新一条并清空更早积压（避免一次性弹出一堆旧句）；无则空串。
        由桌宠窗 JS 每 ~250ms 调用一次，线程安全（queue.get_nowait）。
        """
        last = ""
        while True:
            try:
                last = self._pet_bubble_q.get_nowait()
            except Exception:  # noqa: BLE001
                break
        return last or ""

    def pet_bubble_shown(self, text: str = "") -> None:
        """桌宠窗确认气泡已渲染（调试回执，验证后端→桌宠窗投递链路是否真的到达渲染）。

        由 rig/index.html 的 petShowBubble 在真正露出气泡后调用，仅用于排障日志。
        """
        logger.info("[pet_bubble] 已渲染回执: %r", (text or "")[:60])
