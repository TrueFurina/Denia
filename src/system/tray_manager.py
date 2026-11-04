"""系统托盘管理器。

负责创建和管理系统托盘图标，提供托盘菜单和程序后台运行功能。
"""
import os
import sys
import threading
import logging
from typing import Optional, Callable, Any
import pystray
from PIL import Image, ImageDraw, ImageFont
try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
    PYSTRAY_AVAILABLE = True
except ImportError:
    PYSTRAY_AVAILABLE = False

logger = logging.getLogger(__name__)


class TrayManager:
    """系统托盘管理器。"""
    
    def __init__(self, icon_path: Optional[str] = None):
        """初始化托盘管理器。
        
        Args:
            icon_path: 托盘图标路径，如果为None则使用默认图标
        """
        self.icon_path = icon_path
        self.icon: Optional[pystray.Icon] = None
        self.is_running = False
        self.pet_visible = False
        self.main_window_visible = True
        # 【2026-11-04】静音 / 看屏 的当前状态：菜单文案跟着它变（"静音"↔"取消静音"）。
        # 状态由 main.py 在启动时灌入、每次点击后按回调返回值刷新。
        self.muted = False
        self.watch_enabled = False
        # 【2026-11-04】状态提供者：返回 (muted, watch_enabled) 的实时读取函数。
        # 为什么不能只靠上面的缓存字段：用户可能在**设置页**改静音/看屏，
        # 而托盘这边只在"点托盘"时刷新 —— 于是菜单会显示"静音"却实际静音中，
        # 点一下反而把想关的打开了。菜单文案在**显示时**求值，这里每次现读。
        self.state_provider: Optional[Callable] = None
        self.on_exit_callback: Optional[Callable] = None
        self.on_show_main_callback: Optional[Callable] = None
        self.on_show_pet_callback: Optional[Callable] = None
        self.on_minimize_callback: Optional[Callable] = None
        self.on_open_page_callback: Optional[Callable] = None
        self.on_toggle_mute_callback: Optional[Callable] = None
        self.on_toggle_watch_callback: Optional[Callable] = None
        
    def create_icon(self) -> Optional[Any]:
        """创建托盘图标。
        
        Returns:
            PIL Image对象，如果创建失败则返回None
        """
        if not PYSTRAY_AVAILABLE:
            logger.error("pystray不可用，无法创建托盘图标")
            return None
            
        try:
            if self.icon_path and os.path.exists(self.icon_path):
                return Image.open(self.icon_path)
            
            # 创建默认图标
            img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            
            # 绘制一个简单的AI图标
            # 外圈
            draw.ellipse([8, 8, 56, 56], fill=(100, 150, 255, 255))
            # 内部AI符号
            draw.ellipse([20, 20, 44, 44], fill=(255, 255, 255, 255))
            # 眼睛
            draw.ellipse([26, 26, 32, 30], fill=(50, 100, 200, 255))
            draw.ellipse([36, 26, 42, 30], fill=(50, 100, 200, 255))
            # 微笑
            draw.arc([26, 32, 42, 40], start=0, end=180, fill=(50, 100, 200, 255), width=2)
            
            return img
        except Exception as e:
            logger.error(f"创建托盘图标失败: {e}")
            return None
    
    def create_menu(self) -> pystray.Menu:
        """创建托盘菜单。
        
        Returns:
            pystray菜单对象
        """
        if not PYSTRAY_AVAILABLE:
            return pystray.Menu()
            
        menu_items = [
            # default=True：托盘图标左键/双击激活此默认项（Win32 后端点击图标触发 Menu 的默认项）
            pystray.MenuItem("显示主界面", self._on_show_main, default=True),
            pystray.MenuItem("显示桌宠", self._on_show_pet),
            pystray.MenuItem("隐藏桌宠", self._on_hide_pet),
            pystray.Menu.SEPARATOR,
            # 【2026-11-04】直达入口：在托盘里就能开记忆库 / 关系档案 / 设置，
            # 不用先"显示主界面"再自己找导航。
            pystray.MenuItem("记忆库", self._on_open_memory),
            pystray.MenuItem("关系档案", self._on_open_relation),
            pystray.MenuItem("设置", self._on_open_settings),
            pystray.Menu.SEPARATOR,
            # 【2026-11-04】静音 / 看屏：文案跟着状态走，点一下立即生效。
            # 这两项只改配置与运行时闸门、不碰任何窗口对象，所以在托盘线程里调是安全的
            # （"显示主界面"那种要动窗口的，仍走 main.py 里那套多重兜底）。
            pystray.MenuItem(lambda item: "取消静音" if self._live_muted() else "静音",
                             self._on_toggle_mute),
            pystray.MenuItem(lambda item: "关闭看屏" if self._live_watch() else "开启看屏",
                             self._on_toggle_watch),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("最小化到托盘", self._on_minimize),
            pystray.MenuItem("退出", self._on_exit)
        ]
        
        # 根据当前状态更新菜单
        if not self.pet_visible:
            menu_items[2] = pystray.MenuItem("桌宠已隐藏", None, enabled=False)
        else:
            menu_items[2] = pystray.MenuItem("隐藏桌宠", self._on_hide_pet)
            
        return pystray.Menu(*menu_items)
    
    def _on_show_main(self, icon, item):
        """显示主界面回调。"""
        logger.info("托盘菜单: 显示主界面")
        if self.on_show_main_callback:
            self.on_show_main_callback()
    
    def _on_show_pet(self, icon, item):
        """显示桌宠回调。"""
        logger.info("托盘菜单: 显示桌宠")
        if self.on_show_pet_callback:
            self.on_show_pet_callback()
    
    def _on_hide_pet(self, icon, item):
        """隐藏桌宠回调。"""
        logger.info("托盘菜单: 隐藏桌宠")
        if self.on_show_pet_callback:
            self.on_show_pet_callback(hide=True)
    
    def _on_toggle_mute(self, icon, item):
        """静音开关回调。

        回调用返回值把"切换后的真实状态"带回来，而不是这边自己取反——
        取反会在"写盘失败 / 模式冲突"时和真实状态错位（菜单显示"取消静音"
        但其实是静音的），这类错位用户根本查不出来。
        """
        logger.info("托盘菜单: 静音切换")
        if not self.on_toggle_mute_callback:
            return
        try:
            r = self.on_toggle_mute_callback()
            if isinstance(r, dict) and "muted" in r:
                self.muted = bool(r["muted"])
                self._refresh_menu()
        except Exception:  # noqa: BLE001  托盘线程里绝不能把异常抛给 pystray
            logger.warning("托盘静音切换失败", exc_info=True)

    def _on_toggle_watch(self, icon, item):
        """看屏开关回调（同上：以返回值为准，不本地取反）。"""
        logger.info("托盘菜单: 看屏切换")
        if not self.on_toggle_watch_callback:
            return
        try:
            r = self.on_toggle_watch_callback()
            if isinstance(r, dict) and "watch_enabled" in r:
                self.watch_enabled = bool(r["watch_enabled"])
                self._refresh_menu()
        except Exception:  # noqa: BLE001
            logger.warning("托盘看屏切换失败", exc_info=True)

    def _on_open_page(self, page: str) -> None:
        """把"打开某个页面"转给 main.py 注入的回调（那边负责先显示主窗口）。"""
        logger.info("托盘菜单: 打开页面 %s", page)
        if not self.on_open_page_callback:
            return
        try:
            self.on_open_page_callback(page)
        except Exception:  # noqa: BLE001  托盘线程不能把异常抛给 pystray
            logger.warning("托盘打开页面失败: %s", page, exc_info=True)

    def _on_open_memory(self, icon, item):
        self._on_open_page("memory")

    def _on_open_relation(self, icon, item):
        self._on_open_page("relation")

    def _on_open_settings(self, icon, item):
        self._on_open_page("settings")

    def _on_minimize(self, icon, item):
        """最小化到托盘回调。"""
        logger.info("托盘菜单: 最小化到托盘")
        if self.on_minimize_callback:
            self.on_minimize_callback()
    
    def _on_exit(self, icon, item):
        """退出程序回调。"""
        logger.info("托盘菜单: 退出程序")
        if self.on_exit_callback:
            self.on_exit_callback()
    
    def start(self, title: str = "AI桌面伴侣") -> bool:
        """启动托盘。
        
        Args:
            title: 托盘提示文字
            
        Returns:
            启动成功返回True，失败返回False
        """
        if not PYSTRAY_AVAILABLE:
            logger.error("pystray不可用，无法启动托盘")
            return False
            
        if self.is_running:
            logger.warning("托盘已在运行中")
            return True
            
        img = self.create_icon()
        if img is None:
            logger.error("无法创建托盘图标")
            return False
        
        menu = self.create_menu()
        self.icon = pystray.Icon(
            title,
            img,
            title,
            menu
        )
        
        # 在单独线程中运行托盘
        def run_tray():
            try:
                self.icon.run()
            except Exception as e:
                logger.error(f"托盘运行异常: {e}")
        
        tray_thread = threading.Thread(target=run_tray, daemon=True)
        tray_thread.start()
        
        self.is_running = True
        logger.info(f"托盘已启动: {title}")
        return True
    
    def stop(self) -> None:
        """停止托盘。"""
        if not self.is_running or self.icon is None:
            return
            
        try:
            self.icon.stop()
            self.is_running = False
            logger.info("托盘已停止")
        except Exception as e:
            logger.error(f"停止托盘时出错: {e}")
    
    def _live_muted(self) -> bool:
        """菜单文案用的实时静音状态：优先问 state_provider，没有就用缓存值。"""
        if self.state_provider is not None:
            try:
                return bool(self.state_provider()[0])
            except Exception:  # noqa: BLE001  读配置失败就退回缓存，别让菜单炸掉
                logger.debug("读取实时静音状态失败，用缓存值", exc_info=True)
        return self.muted

    def _live_watch(self) -> bool:
        """菜单文案用的实时看屏状态（同上）。"""
        if self.state_provider is not None:
            try:
                return bool(self.state_provider()[1])
            except Exception:  # noqa: BLE001
                logger.debug("读取实时看屏状态失败，用缓存值", exc_info=True)
        return self.watch_enabled

    def _refresh_menu(self) -> None:
        """按当前状态重建菜单（pystray 只在菜单重新赋值时才会重算文案）。

        抽出来是因为原来 update_pet_visibility / update_main_window_visibility
        各写了一遍同样的三行——两处维护同一件事，迟早有一处忘了改。
        """
        if self.is_running and self.icon:
            try:
                self.icon.menu = self.create_menu()
            except Exception:  # noqa: BLE001  重建菜单失败不该影响托盘存活
                logger.warning("刷新托盘菜单失败", exc_info=True)

    def set_mute_state(self, muted: bool) -> None:
        """外部（启动时/其它入口改过静音后）同步静音状态到菜单。"""
        self.muted = bool(muted)
        self._refresh_menu()

    def set_watch_state(self, enabled: bool) -> None:
        """外部同步看屏状态到菜单。"""
        self.watch_enabled = bool(enabled)
        self._refresh_menu()

    def update_pet_visibility(self, visible: bool) -> None:
        """更新桌宠可见性状态。
        
        Args:
            visible: 桌宠是否可见
        """
        self.pet_visible = visible
        self._refresh_menu()

    def update_main_window_visibility(self, visible: bool) -> None:
        """更新主窗口可见性状态。
        
        Args:
            visible: 主窗口是否可见
        """
        self.main_window_visible = visible
        self._refresh_menu()
    
    def set_callbacks(self, 
                     on_exit: Optional[Callable] = None,
                     on_show_main: Optional[Callable] = None,
                     on_show_pet: Optional[Callable] = None,
                     on_minimize: Optional[Callable] = None,
                     on_open_page: Optional[Callable] = None,
                     on_toggle_mute: Optional[Callable] = None,
                     on_toggle_watch: Optional[Callable] = None) -> None:
        """设置回调函数。
        
        Args:
            on_exit: 退出程序回调
            on_show_main: 显示主界面回调
            on_show_pet: 显示/隐藏桌宠回调
            on_minimize: 最小化到托盘回调
            on_toggle_mute: 静音切换回调（返回 {"muted": bool}）
            on_toggle_watch: 看屏切换回调（返回 {"watch_enabled": bool}）
        """
        self.on_exit_callback = on_exit
        self.on_show_main_callback = on_show_main
        self.on_show_pet_callback = on_show_pet
        self.on_minimize_callback = on_minimize
        self.on_open_page_callback = on_open_page
        self.on_toggle_mute_callback = on_toggle_mute
        self.on_toggle_watch_callback = on_toggle_watch