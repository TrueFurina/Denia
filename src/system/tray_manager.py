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
        self.on_exit_callback: Optional[Callable] = None
        self.on_show_main_callback: Optional[Callable] = None
        self.on_show_pet_callback: Optional[Callable] = None
        self.on_minimize_callback: Optional[Callable] = None
        
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
    
    def update_pet_visibility(self, visible: bool) -> None:
        """更新桌宠可见性状态。
        
        Args:
            visible: 桌宠是否可见
        """
        self.pet_visible = visible
        if self.is_running and self.icon:
            # 更新菜单
            self.icon.menu = self.create_menu()
    
    def update_main_window_visibility(self, visible: bool) -> None:
        """更新主窗口可见性状态。
        
        Args:
            visible: 主窗口是否可见
        """
        self.main_window_visible = visible
        if self.is_running and self.icon:
            # 更新菜单
            self.icon.menu = self.create_menu()
    
    def set_callbacks(self, 
                     on_exit: Optional[Callable] = None,
                     on_show_main: Optional[Callable] = None,
                     on_show_pet: Optional[Callable] = None,
                     on_minimize: Optional[Callable] = None) -> None:
        """设置回调函数。
        
        Args:
            on_exit: 退出程序回调
            on_show_main: 显示主界面回调
            on_show_pet: 显示/隐藏桌宠回调
            on_minimize: 最小化到托盘回调
        """
        self.on_exit_callback = on_exit
        self.on_show_main_callback = on_show_main
        self.on_show_pet_callback = on_show_pet
        self.on_minimize_callback = on_minimize