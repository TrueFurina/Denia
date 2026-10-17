# -*- coding: utf-8 -*-
"""追加 2026-10-16 第三轮记录到工作日志（append-only）。"""
import io

p = r"G:\ai对话\.workbuddy\memory\2026-10-16.md"
add = """
### 【第三轮·点击能力实锤：UIA 直触 + DPI 一致性】
- 目标：回答"怎么才能真的在电脑上点软件"，补齐 agent_pro 的点击短板。
- ★ 三个实锤（全真机跑出）：
  1. **`uiautomation 2.0.29` 的 `Control.Click()` 实测 = `MoveCursorToInnerPos + mouse click`**（inspect 源码确认）→ 所有"控件点击"本质仍是**模拟鼠标**：要窗口前台可见 + 坐标一致 + 会抢用户鼠标。
  2. **真实窗口抽样 30 个交互控件 → 28 个支持 Invoke Pattern（93%）**，而旧代码一次都没用上。
  3. **本机 DPI 真相**：进程 UNAWARE 时 `GetSystemMetrics` 报 2560x1440，物理却是 3840x2160（缩放 150%，GetDpiForSystem=144）→ 两套像素混用 = 「点歪」。主软件 webapp.py 已 SetProcessDPIAware；agent_pro 补 `_ensure_dpi_aware()` 兜底。
- ★★ **新修的死 bug（典型静默失效）**：`screen.py::_ui_cache_ts` **从未被赋值**（只有定义/global/读取三处）→ `click_element(index=N)` 恒判"快照已过期" → **编号点击路径 100% 不可用**（模型会在 ui_elements ↔ click 之间死循环）。已修：ui_elements 成功时写 `time.monotonic()`。
- ★★ **新增 UIA 直触 `_uia_activate`**：Invoke > LegacyIAccessible.DoDefaultAction > Toggle > ExpandCollapse > SelectionItem；**不动鼠标、不需前台、不受遮挡、与坐标无关**；不支持才回退坐标点击。`ui_elements` 同步缓存控件引用（`_ui_ctrls`）；`search_in_app` 改为优先 `SetFocus()` 直触聚焦。
- ★ **真机验证 PASS**（`tools/agent_pro_uia_native_test.py`：自建原生 Win32 窗口+按钮，零副作用）：`click_element(index=1)` → Invoke → 窗口过程收到 **BN_CLICKED=1**，且 `pyautogui.position()` 前后完全相同 → **证明不动鼠标也能真的点到**。
- ★ **诚实边界**：**tkinter 的 UIA 是空壳**（Invoke 返回成功但回调不触发）→「直触返回成功」≠「动作真的发生」，必须用 state_snapshot / wait_for / media_state 核实。已写进返回文案与 README。
- selftest：**35 PASS / 0 FAIL**（新增 4 项：DPI 幂等 / 直触 None 与双击 / pattern 优先级 / 可直触时绝不走鼠标）。
- 状态：**agent_pro 仍未接入主软件**（install 一行待批）。Electron 类（汽水音乐/抖音）UIA 树残缺 → 直触无效，只能走"视觉定位 + 坐标点击 + 前台强化"，需在软件本体真机验证（命令行沙箱无前台权限）。
- 工具坑（老红线复现）：**一条消息里对同一文件发 3 个 Edit → 只落地 1 个**（selftest 补丁踩到）→ 同文件多处修改必须逐个 Edit 并各自 grep 核验，或改用 Python 脚本做替换。
- 一次性探测脚本：`_deps_probe.py` / `_dpi_probe.py` / `_uia_probe.py`（均在 `G:\\ai对话\\` 根目录）。
"""
with io.open(p, "a", encoding="utf-8") as f:
    f.write(add)
print("LOG APPENDED")
