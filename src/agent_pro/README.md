# agent_pro · 通用 Agent 能力提升包

> 电脑操作 Agent 的**独立实现包**：可单独开发、单独开关、单独回滚，
> 约定与 `src/proactive_pack` 完全一致。设计依据：
> `通用Agent能力调研与对标报告_20261016.html`（业界对标：Claude Computer Use /
> OpenAI CUA / UFO2 / Agent S2 / Windows-MCP）。

## 它解决什么

宿主的 `agent_tools` 有 35 个工具，但有三个通用性短板（2026-10-16 实测定轨）：

| 短板 | 症状 | 业界做法 |
|---|---|---|
| 应用启动靠硬编码映射 + 每次 2-20s 的实时 PowerShell | 换电脑/换版本/白名单外软件就废 | **索引化检索**：一次扫全系统，本地毫秒级匹配 |
| 启动后不验证进程 | 模型被骗"已打开"（3 个模型全栽在这） | **Verify 闭环**：Observe→Act→**Verify** |
| 散落的固定 sleep / 无状态快照 / os.walk 找文件 | 慢、盲、不可靠 | wait_for 轮询 + State 快照 + Everything 双模式 |

## 本包内容（零应用名硬编码）

| 文件 | 职责 |
|---|---|
| `config.py` | 三级开关叠加（DEFAULTS ← config.yaml `agent.agent_pro` ← `data/agent_pro.json`），缺省即关；只读 config.yaml 绝不回写 |
| `app_index.py` | **应用索引**：开始菜单 lnk（162）+ 注册表卸载表（212）+ App Paths（21）+ Get-StartApps(UWP)；缓存 `data/agent_pro_app_index.json`，TTL 默认 24h；实机 **835 应用 ~2.5s** 建完（uninstall 212 / startmenu 162 / apppaths 21 / **uwp 82** / system 358），TTL 缓存后查询 1-2ms |
| `launch.py` | **启动引擎**：explorer 委托（launcher 型应用唯一可靠通道）+ 启动后轮询验证进程存活（不依赖 psutil，ctypes Toolhelp32）+ lnk 二进制解析兜底（沙箱封 powershell 也能用）+ a11y 直启回落 |
| `wait.py` | `wait_for`（窗口/进程/屏幕轮询，替代固定 sleep）、`state_snapshot`（前台+窗口清单+可选 VLM 概括）、`find_file`（es.exe 优先，受限 walk 兜底，8s 预算） |
| `specs.py` | 7 个新工具的 function-calling schema + 包内派发 |
| `screen.py` | **屏幕/控件操作**：`ui_elements`（编号元素清单）、`click_element`（UIA 直触优先 → 坐标回退）、`search_in_app`（语义化搜索输入）、DPI 一致性守卫 |
| `media.py` | `media_state`：winsdk SMTC 系统媒体会话（零视觉成本验证放歌）|
| `selftest.py` | 全桩离线自测（19 项，不依赖主软件/LLM/GUI） |

## 一行开关

```jsonc
// data/agent_pro.json
{ "flags": { "enabled": true } }   // 默认 false —— 装上不打开 = 什么都不做
```

或 `config.yaml`（可选）：

```yaml
agent:
  agent_pro:
    enabled: true
    index_ttl_h: 24          # 应用索引缓存有效期
    verify_timeout_s: 10     # 启动验证预算
    file_search_engine: auto # auto | everything | walk
```

## 接入（A 方案：一行零侵入）

软件启动拿到 Config 后：

```python
from src.agent_pro import install
install(cfg)                # 幂等；enabled=false 时零副作用
```

装上后对 agent_tools 的影响（enabled=true 才生效）：

1. **新增 3 个工具**：`find_file` / `wait_for` / `state_snapshot`（自动进 schema、自动带超时与就绪探针）。
2. **`open_app` 增强**：白名单未命中后先走**应用索引**（毫秒级）再走原 PowerShell 实时查询；
   并且**启动后验证进程**——真起来了才说"已启动"，没起来如实说"大概率失败"。
3. 热回滚：`from src.agent_pro import uninstall; uninstall()`。

## 实测数据（2026-10-16 包测）

### 点击能力实测（2026-10-16，`tools/agent_pro_uia_native_test.py`）

原来所有点击路径（`click_at` / `click_element` / 宿主 `ui_click`）**本质都是模拟鼠标**：
`uiautomation 2.0.29` 的 `Control.Click()` 实测 = `MoveCursorToInnerPos + mouse click`
（不是 Invoke Pattern）。于是必须满足：窗口在前台可见 + 坐标体系一致 + 不抢用户鼠标。

本包新增 **UIA 直触优先**：对控件直接调 Invoke / LegacyIAccessible.DoDefaultAction /
Toggle / ExpandCollapse / SelectionItem.Select，**不移动鼠标、不需要窗口在前台、
不受遮挡影响、与服务端坐标无关**。真实窗口抽样：30 个交互控件里 **28 个支持 Invoke（93%）**。

真机验证（自建原生 Win32 窗口 + 按钮，零副作用）：

- 原生 BUTTON：`click_element(index=1)` → Invoke 直触 → 窗口过程收到 **BN_CLICKED=1**；
  全程 `pyautogui.position()` 前后完全相同（**未移动鼠标**）→ PASS。
- DPI 一致性：本机 150% 缩放下 UNAWARE 进程 `GetSystemMetrics` 报 2560x1440 而物理是
  3840x2160 —— 两套像素混用就是「点歪」的成因；`_ensure_dpi_aware()` 已把进程对齐到物理像素。
- **诚实边界**：tkinter 控件的 UIA 是空壳（Invoke 返回成功但回调不触发）→
  「直触返回成功」不等于「动作真的发生」，所以 `click_element` 的返回文案始终提示
  用 `state_snapshot` / `wait_for` / `media_state` 核实结果。

- 离线自测：`python -m src.agent_pro.selftest` → **39 PASS / 0 FAIL**（含 install/uninstall
  幂等与还原、已在运行短路、路由后 NameError 拦截——最后一项是 LLM 包测真踩出的 bug 后补的守卫）。
- 索引：395 应用（uninstall 212 / startmenu 162 / apppaths 21），1.6s；TTL 缓存后毫秒级。
- 匹配：汽水音乐/抖音/微信/QQ音乐 100 分直中；没装的软件如实拒绝（不硬编不猜测）。
- 启动验证三态实测：
  - 冷启动：杀进程后 `open_app("汽水音乐")` → 1.6s 确认 `sodamusic.exe` 出现；
  - 已在运行：短路报告"应用已在运行，未重复启动"（launcher 家族按**安装目录**判定，
    token 是 SodaMusicLauncher.exe 而进程是 SodaMusic.exe 也能对上）；
  - 启动失败：如实说"启动命令已发出但未检测到新进程"，绝不谎报。
- find_file：桌面 30 张 png，一次调用直出结果；Everything 未装时如实标注引擎。
- LLM 端到端（`tools/agent_pro_e2e_llm.py`，大脑 qwen3.8-flash）：
  打开应用 / 找文件 / 列窗口 三条指令全部完成且**如实汇报**；中途包测抓到
  `find_file` 的 NameError（Edit 部分落地红线再+1）修复后从 170s 乱撞降为单步 10-22s。
  聚焦失败场景模型主动认错不装成功——正是本包 Verify 设计想要的行为。

### 全工具真机验收（2026-10-16 二轮，`tools/agent_pro_accept_all.py`）

一次跑完 7 工具 + 索引 + 启动，全部真实环境、零副作用（不动鼠标、不改用户文件）：

| 项 | 实测结果 |
|---|---|
| app_index | **835** 应用 2.5s，缓存 2ms；记录结构自检 **缺 token 0 条** |
| launch | 冷启动 `记事本` 4.3s → 已验证 notepad.exe 在运行；**`汽水音乐` 1.6s 经 AppID 通道启动成功**（已验证 sodamusic.exe） |
| find_file | 桌面 `*.png` 0.1s 出 30 条 |
| wait_for | 进程已在运行 0.0s；不存在的窗口 3.4s 超时并如实报告 |
| state_snapshot | 0.3s；`ctx` 无视觉凭据时如实提示去配模型 |
| ui_elements | 记事本 60 个元素，**控件引用 60/60 全抓到** |
| click_element | 真实记事本上直触「最大化」→ `IsZoomed` 由 False 变 True（**真生效**） |
| search_in_app | 前台确为目标窗口时执行：聚焦→粘贴→回车全链路走通 |
| media_state | 0.16s 检出 1 个真实会话（抖音 paused） |

### 本轮抓出的 6 个真 bug（都已修 + 加防退化测试）

1. **中英名之间没有桥**：「记事本」查不到 `notepad`，difflib 相似度为 0。
   → 新增 `file_desc()`：读 exe 的**本地化文件描述**（中文系统上 notepad.exe 就是「记事本」）。
   这条桥**跟着系统语言走，零硬编码**。
2. **索引把 Windows 自带应用整段漏掉**：Win11 把记事本/画图/计算器做成了商店应用，
   不再出现在 lnk / 卸载表 / App Paths；Get-StartApps 通道受限时整段缺失。
   → 新增**第 5 路来源 `_system_gui_apps()`**：扫系统目录，用 **PE 子系统字段**筛掉命令行工具，
   用本地化描述做名字。索引 395 → **762**。
   → 附**防噪音**：系统段降权 15% + 「组件名比用户说法长出 len(查询)+4 字就丢弃」
   （否则「计算器」会被「Windows SQM 合并计算器」抢走）。
3. **新来源漏 `token` 字段 → `KeyError` 把整条启动链炸掉**（验收脚本 [2] 当场抓到）。
   → 治本补字段 + `index_launch` 加防御（缺 token 时用 exe 兜底，不再崩）。
4. **「已在运行」误判**：`notepad.exe` 在 System32，而 System32 下永远有几百个进程，
   只按「同目录」判定 → 任何系统应用都被判成"已在运行"（直接不启动）。
   → `_dir_has_process` 必须叠加**进程名家族前缀**（`SodaMusicLauncher` ↔ `SodaMusic` 算一家，
   `notepad` ↔ `svchost` 不算）。

5. **`Get-StartApps` 的 tab 转义失效 → UWP/商店应用段恒为空**，日志还把它误报成「通道受限」：
   PowerShell **单引号**里的 `` `t `` 不做反引号转义，输出的是字面量而不是 tab → 后面
   `split("\t")` 全部失败。实测同一命令改用 `[char]9` 能正常返回 275 行。
   → 改 `[char]9` + 自己解码（utf-8→gbk；`text=True` 会按系统 ANSI 把中文解成乱码）。
   修好后 UWP 段 **0 → 82 条**，索引 762 → **835**：记事本 / 计算器 / 画图 从「无匹配」变成 **100 分命中**。
6. **单通道启动没有退路 → 汽水音乐永远起不来**：去重时把 AppID 候选丢掉，只剩会秒退的
   `SodaMusicLauncher.exe`。→ `_dedupe` 把落选 token 收进 `_alts`；启动改成**多候选降级链**
   （真包名 appid → exe → lnk，逐个验证、失败自动换下一条）。
   **实测 `index_launch("汽水音乐")` 1.6s → 已验证新进程 `sodamusic.exe`**（走 `com.soda.music` AppID 通道）。

附带修：`state_snapshot` 的窗口列表过滤无标题 shell 窗口 + 同标题去重（噪音从 5 条降到 0）。

## 通用性设计（回应"别人电脑上怎么办"）

1. **无任何应用名/路径硬编码**——查名全部走四路系统来源，别的电脑上自动适配。
2. 匹配打分做规整（去空白/大小写/版本号尾巴）+ 包含 + difflib 相似度，exe 名也参与。
3. 通道降级链路：PowerShell 受限 → lnk 二进制解析兜底；Everything 未装 → 受限 walk 兜底；
   psutil 未装 → ctypes 原生快照。任何一环缺失都有下一环，且**降级打日志不静默**。
4. 可选 `data/agent_pro_aliases.json` 手工别名（`{"my音乐": "汽水音乐"}`），是索引的补充而非主路径。

## 包测复跑

```
cd G:\ai对话\11
python -m src.agent_pro.selftest          # 离线全桩
python tools\agent_pro_e2e_index.py       # 索引/启动/find_file 真实环境
python tools\agent_pro_e2e_llm.py         # 真实 LLM 三指令（需 config 里的模型可用）
```

## 已知边界

- UWP 段依赖 Get-StartApps：沙箱/受限环境拿不到时会缺商店应用（有 warning 日志），
  Win32 应用不受影响；lnk 二进制解析对 UWP 型 lnk 无效（它们本就不该用 exe 通道）。
- `state_snapshot` 的视觉概括需要 ctx 里带 VLM 凭据（与 `see_screen` 同源）。
- `find_file` 不做全文检索（那是 search_web/fetch_web 与未来 Everything 场景的事）。
- **商店应用（计算器/画图）**：真实身份是 UWP 包，正确来源是 `Get-StartApps`；
  该通道在受限环境（命令行/沙箱）拿不到 → 会如实拒绝匹配。软件本体里 powershell
  可用时这一段应当恢复正常，需要在真机上复测。
- **命令行工具不在索引里**（PE 子系统筛选会剔掉 cmd/powershell）：用户说「打开命令提示符」
  时走 `start_process` / `run_command`，而不是 `open_app`。


## 设计借用：DeepSeek Harness（dsh）+ 其 computer-use 插件族（2026-10-16）

调研结论先说清楚：**dsh 本体是「编码 Agent 运行框架」**（TypeScript + Cordis 插件内核，
一切皆插件：模型/工具/会话/沙箱/存储/UI；append-only 会话日志可回放；MIT）。
它的**电脑操作能力来自社区插件**（dsh-computer-use / dsh-computer-use-win /
dsh-computer-use-windows 等，MIT）。所以「直接拿 dsh 来用」不解决 GUI 问题，
但它的插件设计是本包最好的参照系——**借思路、不抄代码**（语言不同，也没法抄）。

| dsh 的设计 | 本包对应实现 | 状态 |
|---|---|---|
| `screen_observe`：编号元素 + 坐标一次返回，零视觉 token | `ui_elements`（编号/类型/坐标；中英窗口名、launcher 家族都能定位） | 已实现 |
| **观察快照 TTL**：过期快照上的动作一律拒绝（禁止盲操作） | `_SNAPSHOT_TTL_S=20`；`click_element(index)` 超时即拒并要求重新枚举 | 已实现 |
| AX 树空 → 自动降级视觉兜底 | `ui_elements` 空则明确引导 `click_element desc=`；`search_in_app` 内建 UIA→VLM 降级链 | 已实现 |
| `computer_search_in_window`：定位输入框→输入→回车（语义化，免逐应用补丁） | `search_in_app`（UIA 找 Edit → 前置聚焦 → 剪贴板粘贴中文 → 回车 → 提示用 media_state 验证） | 已实现 |
| `computer_observe_media_controls`：查系统媒体会话判播放状态 | `media_state`（winsdk SMTC；实测 0.1s 返回真实会话；零视觉成本验证「放歌成功」） | 已实现 |
| 安全护栏：危险词（删除/支付）→ 审批；密码框保护 | `_DANGER_RE` 危险词闸（`click_element` desc / `search_in_app` query 都过闸），需 `confirmed=true` 显式放行 | 已实现 |
| 动作前让目标窗口可见（前置/还原） | `search_in_app` 内建 best-effort 还原 + 前置 | 已实现 |
| 剪贴板粘贴输中文（pyautogui.write 对 CJK 无效） | `search_in_app` 用 pyperclip + Ctrl+V | 已实现 |
| append-only 会话日志 / Trajectory 回放（每次运行可追溯） | 未做——达妮娅已有日志；结构化轨迹留待 P2 | 待办 |
| 插件内核（热插拔/依赖管理）、沙箱（文件+网络硬限制）、PTC（模型写代码组合多轮工具） | 本包用 install/uninstall 挂钩做轻量版；沙箱/PTC 不适用于伴侣应用 | 按需 |

**不搬 dsh 本体的理由**：它是 Node/TypeScript 独立运行时 + 自带 agent loop，嵌进达妮娅等于跑第二套大脑；
而它的 GUI 能力恰恰在插件层——我们借的正是插件层思路。
