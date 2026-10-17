# AGENTS.md — 本仓库的 AI/人类协作宪章

> 这份文件写给"下一个要改这个项目的人（或 AI）"。它记录的不是愿望，而是**已经踩过的坑**和**已经被证据推翻的判断**。
> 违反下面的条目不会报错，只会让 bug 静默回来。

## 第一条 · 结论必须有证据，且要能复现

- 说"已修复"之前，必须有一条**可复现的命令**和一份**落盘产物**（截图、日志、测试输出）作背书。
- 说"不行/做不到"之前，同样要有实验记录。本项目已有多条"以为不行、实测可行"和"以为可行、实测不行"的例子（见第五条）。
- **数字要报口径**。同一件事用不同口径能得出相反结论：
  - `grep -c "Queue()"` 得 13 个无界队列 → 逐个看生产/消费端后，真正需要处理的只有 6 个；
  - 粗 grep 得 167 处 `open()` 缺 encoding → 用 AST 判定文本模式后，`src` 里实际 **0 处**。
  - 报数前先问：这个数字的**分母**是什么、**排除了什么**。

## 第二条 · 门禁不是装饰品：必须做过变异验证

新增或修改任何检查脚本后，必须做一次变异：

1. 故意破坏（删白名单一行、注入坏模块、把"丢最旧"改成"丢新"）；
2. 检查必须 **FAIL**；
3. 还原后必须 **PASS**。

只看到"检查通过"就认为检查有效，是本项目已经犯过的错（有一次测试是"假通过"：`/tmp` 在 Git Bash 与 Windows Python 下不是同一个目录，`cp` 失败后 `;` 吞掉了真实退出码）。

统一入口：`python tools/check_all.py`（`--fast` 跳过耗时的导入冒烟与测试）。

## 第三条 · Windows 壳层的三条硬约束

| 约束 | 为什么 |
|---|---|
| **DPI 感知、单实例互斥体必须在任何窗口创建之前设置** | 建窗后再设 DPI 感知会被系统静默忽略，且不报错。故固定放在 `main()` 最前面（`src/win_shell.py`）。 |
| **ctypes 调用 Win32 必须声明 `argtypes`** | 未声明时 `HWND_TOPMOST = -1` 被当 32 位 int 传成 `0x00000000FFFFFFFF`，句柄校验必失败（`GetLastError=1400`）。项目里所有置顶曾因此从未生效。 |
| **不要跨线程写 WinForms 控件属性** | 如 `window.on_top = x` 底层是 `form.TopMost`，后台线程每秒写一次会在切窗时卡住。改走纯 Win32 `SetWindowPos`。 |

## 第四条 · WebView2 透明：已确认为硬限制，别再试

`transparent=True` 在 pywebview 的 Windows 后端只让**页面**透明，**不等于桌面透明**。
实测（`tools/diag_transparency.py`，最小用例、逐点与"窗口隐藏后的真背景"比对）：

| 手法 | 结果 |
|---|---|
| 基线 `transparent=True, frameless=True` | 角落恒为 `#F0F0F0`（WinForms 客户区底色） |
| `WS_EX_LAYERED` + `LWA_ALPHA(255)` | 不生效 |
| `WS_EX_LAYERED` + `LWA_COLORKEY(#F0F0F0)` | 透明区变 `(32,32,32)` |
| `Form.TransparencyKey = #F0F0F0` | 同上 |
| `Form.BackColor = ARGB(0,0,0,0)` | 不生效 |
| `SetWindowRgn` 裁剪 | 裁掉处变纯黑 |
| `WS_EX_NOREDIRECTIONBITMAP` | 无变化 |
| `DwmEnableBlurBehindWindow` | 变成白色填充 |

根因：WebView2 用 DirectComposition 硬件合成，宿主窗体一旦分层/裁剪，合成链就断。
**唯一出路是换渲染宿主**——同一份 rig 页面在 Electron 下已实测真正透出桌面
（`tools/rpc_bridge_smoke.py`，`?bare` 模式零改动，js_api 由 `shell/preload.js` 同名 shim 转发到 RPC）。

## 第五条 · 本仓库已知环境坑（照抄，别再踩）

- **Git Bash 缺核心命令**：`mkdir/head/tail/cat/env/ls/grep/rm` 不可用，用绝对路径或专用工具。
- **给 git 的绝对路径用 `D:/...`，不要用 MSYS 的 `/d/...`**：本机 git 不做路径转换，会报 "could not read log file"。
- **不要用 `git fetch` 做本地状态核验**：本机常年挂住。要联网核对走 SSH 或 API。
- **别用 `sed -i` 改大文件**：会把 CRLF 洗成 LF（曾把 1787 行的文件从 62992 洗成 61159 字节）。改配置用二进制模式定点替换。
- **`ELECTRON_RUN_AS_NODE=1`**：本机 shell 常带这个变量，会让 `electron.exe` 退化成 node（`app` 未定义、窗口不起）。启动 Electron 前必须剔除。
- **stdout 通过管道时 Python 会缓冲**：长脚本一律 `python -u`，否则超时被杀时一个字都留不下。
- **不要用 stdin 给 Electron 主进程发命令**：Windows 下管道里的 stdin 收不到数据，命令全丢。
  若用它做"隐藏/显示"对照，两次采样都是显示态 → 会得出"透明"的假阳性（本项目已踩过）。
  正确做法：壳起一个 TCP 控制端口，命令必须**有回执**才算执行；拿不到回执直接判失败。
- **读子进程 stdout 不要用裸 `readline()`**：子进程没输出时会无限阻塞，用读线程 + 队列轮询。

## 第六条 · 交付红线

- 打包前必须过双向闸：`python tools/check_package_secrets.py --dir <包目录>`
  - 减法：包内不得有 `data/`、`logs/`、`.venv/`、`call_records.json`、`memory.json`、`user_knowledge.yaml`
  - 加法：包内 HTML 引用的本地资源必须存在（看板娘曾因 rig 目录为空而整个消失）
- 组装交付目录只走 `python tools/make_dist.py`，不要手工复制文件夹。
- 不得把 `*.db`、真实 Key、用户录音提交进仓库。
- **本地 RPC（`src/ui/rpc_server.py`）的三条边界不能拆**：只绑 `127.0.0.1`、每个请求校验随机令牌、
  只允许公开成员（下划线/dunder 一律拒绝）。任何一条被拿掉都等于给本机任意网页开了后门。
  改这里必须跑 `src/tests/test_rpc_server.py`，并做一次"关掉令牌校验应当失败"的变异验证。

## 第七条 · 判断"这个功能到底有没有接线"

定义齐全 ≠ 接线。判断一个能力是否真的可用，至少要做三件事：

1. `grep` 生产调用点（排除定义文件本身）；
2. 实际 `import` 并调用一次（`tools/smoke_import.py`）；
3. 看它在**失败路径**上返回什么。

本项目据此发现过：CLR 崩溃守卫从未装上过、`pet_hit_rect` 调用的 `native.set_pet_hit_rect` 根本不存在、
两个 tkinter 功能在发布版里永远返回"不可用"。
