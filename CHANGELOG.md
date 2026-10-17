# 变更记录

> 项目：达妮娅 AI 桌面伴侣（`D:\Denia`）
> 起点：2026-10-17 19:39 建立版本控制（此前无 git）
> 对应诊断报告：`项目诊断报告_2026-10-17.md`

**维护约定**：每次提交后在这里追加一行速览，并在下面写一条明细（问题 → 改动 → 验证）。
明细只写"为什么"和"验证证据"，不抄 diff。踩过的坑要写进去，那是最贵的部分。

---

## 速览

| commit | 时间 | 类型 | 一句话 | 验证 |
|---|---|---|---|---|
| `5bffc68` | 19:39 | chore | 建立版本控制基线（此项目此前无 git） | 646 对象 / 11.97 MiB |
| `e8e1d03` | 19:40 | docs | 全项目诊断报告 | — |
| `57d6101` | 19:46 | security | 清空发布模板漏网的 API Key + 打包凭据硬闸 | 变异：塞假 Key → exit 1 |
| `ecd60a3` | 19:46 | security | `run_command` 白名单 fail-closed | 9 项用例 + 变异 |
| `c90d56b` | 19:46 | security | OneBot 反向 WS 只听回环 + 入站鉴权 | 6 项用例 + 变异 |
| `a90ccf2` | 19:48 | fix | 补 `start` 内建命令（上一提交的回归） | 9 项用例 |
| `2dabbf1` | 19:54 | refactor | `webapp` 抽出 25 个纯函数；修 2 个 NameError | AST 逐字比对 25/25 |
| `972aef9` | 20:00 | refactor | 15 个模块级全局收敛为 `STATE` | 跨模块同一对象 |
| `08cabbd` | 20:08 | refactor | 桌宠域拆 `PetAPI` mixin；修 3 处引用错误 | 21 方法逐字一致 |
| `aab4c70` | 20:13 | refactor | 窗口域拆 `main_window` + `WindowAPI` | 9 函数 + 17 方法逐字一致 |
| `5fa19d8` | 20:16 | test | 通话链路冒烟 22 项 | 变异 3 项 FAIL |
| `c566bc9` | 20:19 | chore | 静默异常扫描器 + 口径纠错 | `--strict` 行为验证 |
| `add925a` | 20:26 | fix | 去掉写死盘符与 locale 编码依赖 | import 实测通过 |
| `3fb4a71` | 20:26 | chore | `live2d/rig` 由 gitlink 改为真实跟踪 | 变异：删文件 → exit 1 |
| `1207233` | 20:31 | chore | 打包闸双向化（也查"必须带的"） | 三组假包实测 |
| `206cc47` | 20:38 | feat | 导入冒烟 + 依赖对账 | 变异 3 次 |
| `01d185c` | 20:41 | fix | CLR 崩溃守卫此前从未装上 | `_CLR_CRASH_GUARD_INSTALLED` 可置 True |
| `5b348a5` | 20:51 | fix | 6 处无界队列改有界丢最旧 | 14 项测试 + 变异 |
| `c1f6b30` | 20:55 | docs | 报告回填队列结果 | — |
| `a4a6a38` | 23:40 | feat | 交付目录组装器 `make_dist.py` + launcher 去旧盘符 | 组装 65.3MB/1334 文件，三道闸门 |
| `2f14c0a` | 23:58 | fix | 应用图标/任务栏归属 + Win32 置顶 64 位句柄截断 | AUMID 读回一致、WM_GETICON 非零、TOPMOST 位正确 |
| `e46f21b` | 00:40 | fix | 单实例锁 + DPI/单实例前置到建窗之前 | `test_win_shell.py` 6 项；跨进程同名锁 Failure |
| `86fa3df` | 00:45 | chore | AGENTS.md 协作宪章 + `check_all.py` 统一门禁 | 16 项全 PASS；变异后 exit 1 并点名 |
| `6d75b24` | 00:52 | feat | 换壳 PoC：同一 rig 页面在 Electron 下真透明 | 角落与真背景逐点相等、中心不同 |
| `cac2760` | 00:53 | chore | 锁定 Electron 依赖版本 | — |
| `4792f40` | 01:20 | feat | 换壳 1/2 步：CharacterAPI 暴露本地 RPC + 壳侧 shim | 19 方法两通道一致；端到端五项全通过 |

---

## 2026-10-17

### `5bffc68` 建立版本控制

**问题**：99,751 行代码、175 个 Python 文件，**没有任何版本控制**。任何一次改动都不可回滚，排查问题只能靠记忆。

**改动**：`git init` + 首次提交。`.gitignore` 排除 `.venv/`（692MB）、`python/`（45MB）、三个 `.exe`（35MB）、`config.yaml`、`data/`、`call_records.json`（141 条真实通话记录）。

**验证**：确认库内无密钥、无隐私数据（`git diff --cached` 检查）。

---

### `57d6101` 发布模板漏了一枚真实 Key

**问题**：`config.release.yaml` 32 个 `api_key` 里 31 个手工置空、漏了 1 个（`sticker.api_key`，智谱 GLM）。根因不是疏忽，而是脱敏方式本身不可靠——这个"发布模板"和 `config.yaml` 全文 diff 只有 **1 行**，本质是实配副本改的。

**改动**：置空该行（保留 CRLF 行尾）；新增 `tools/check_package_secrets.py`，模板出现非空凭据即 `exit 1`，`--dir` 模式同时拦 `data/`、`logs/`、`call_records.json` 等。

**踩坑**：第一次用 `sed -i` 改，1787 行 CRLF 全变 LF，文件从 62992 掉到 61159 字节，diff 显示整个文件重写。**已回滚**，改用 Python 二进制定点替换。

**验证**：三个 `.exe` 二进制里搜索该 Key 前 10 字符，**0 命中**（尚未外泄）。变异测试：塞假 Key → `exit 1`；还原 → `exit 0`。

---

### `ecd60a3` 命令白名单能被一个分号绕过

**问题**（两个缺口叠加）：

```python
if allow and head not in [...]:   # allow 为空 → 短路 → 全放行
subprocess.run(command, shell=True)  # 只校验首词，整串交给 shell
```

白名单有 `git`，模型输出 `git status; del /s /q C:\...` 照样通过。

**改动**：白名单为空即拒绝；拒元字符 `; & | \` $ > < % ^ ! ( )` 与换行；`shlex` 解析后按 basename 校验，`shell=False` 执行；cmd 内建命令单独走 `cmd /d /c`。

**验证**：8 条越权用例 + 正常执行用例全绿。变异：把元字符闸改成永不匹配 → 测试立即 FAIL。

---

### `c90d56b` OneBot 反向 WS 默认听 0.0.0.0 且无入站鉴权

**问题**：同局域网任意主机连上 WS 就能伪造 QQ 消息，消息会进聊天上下文并写入长期记忆——等于可以往记忆库投毒。

**改动**：`listen_host` 默认 `0.0.0.0` → `127.0.0.1`；新增 `_inbound_authorized`，无 token 时只允许回环，配 token 后校验 `Authorization: Bearer` 或 `?access_token=`（常时比较）；非回环 + 无 token 启动即打 error 并拒绝所有连入（`close(1008)`）。

**验证**：6 组用例全绿；变异（鉴权恒真）→ 测试必 FAIL。

---

### `a90ccf2` 安全修复差点弄坏功能（回归）

**问题**：实配 `allow_commands` 是 `['notepad','calc','explorer','start']`，其中 `start` 是 cmd 内建命令，`shell=False` 下 `FileNotFoundError`——等于把"能打开程序"改成"执行失败"。

**改动**：`_CMD_BUILTINS` 补 `start`；新增 `_norm_exe` 统一大小写与 `.exe` 后缀。

**教训**：改安全默认值之前必须 grep 实际配置，否则修漏洞会顺手制造功能回归。

---

### `2dabbf1` webapp.py 第一刀：25 个纯函数

**问题**：`webapp.py` 19,820 行，改不动。

**改动**：按"不引用任何模块级可变状态"为标准抽出 4 组纯函数：

| 新模块 | 内容 | 函数 |
|---|---|---|
| `ui_fmt.py` | 时间/路径/图片格式化 | 5 |
| `bailian_key.py` | 百炼 Key 识别与归一 | 5 |
| `wv2_env.py` | WebView2 加固、唯一 user-data 目录 | 8 |
| `notify_geom.py` | 通知浮窗几何 + 滑动动画 | 7 |

**顺带修两个真实 NameError**（新写的 `check_module_globals.py` 首次运行就抓到）：
1. `_list_speaker_devices` 尾部 `return out`，`out` 从未定义 → 走回退分支必 NameError → 被 `except` 吞 → **用户看到"扬声器列表是空的"**；
2. `slot_config._set_duplex` 用 `logger.info`，整个模块没定义 `logger` → 引导页一键配置传 `bailian`/`aliyun` 别名时必崩。

**验证**：AST 逐字比对 25/25 完全一致；`compileall` + import 冒烟 + 能力槽 41 项回归全绿。

---

### `972aef9` 15 个隐式全局收敛为 `STATE`

**问题**：`webapp.py` 有 15 处模块级可变全局，同时被类方法和模块级函数读写。**上一刀真的引入了 bug**：`wv2_env._safe_js_api` 里 `global _WV2_CONTROLS` + 下标赋值，函数迁走后这个名字在新模块不存在 → `NameError` → 被紧挨着的 `except: pass` 吞掉 → **WebView2 控件永远注册不上**，后端原生推送全部静默失效。

**根因**：我的自由变量分析脚本里可变全局清单是手工维护的，只写了 `_WV2_BC_EXECUTOR`，漏了 `_WV2_CONTROLS`。**手工清单一定会漏，这是方法论错误。**

**改动**：新增 `src/ui/ui_state.py`（`WindowState` + 进程级单例 `STATE`），61 处读写点改为 `STATE.<field>`，删掉 15 条 `global` 与 15 个定义行。检查器补硬规则：`global X` 但模块顶层没定义 X → 直接报错。

**顺带复活一个失效测试**：`tools/test_key_unify.py` 里硬编码 `G:\ai对话\11`，项目搬家后一直跑不了。改成基于 `__file__` 的相对路径，41 项断言全部复活并通过。

---

### `08cabbd` 桌宠域拆为 `PetAPI` mixin

**改动**：`_pet_*` 9 个 Win32 函数 → `src/ui/pet_window.py`（262 行）；21 个 js_api 方法 → `src/ui/api_pet.py`（529 行）。`class CharacterAPI(PetAPI)`。

**三个引用错误**：
1. `_wt` 未定义 —— **原始代码的 bug**：`pet_drag_start`/`pet_move` 用 `_wt.RECT()`，但整个文件只有另一个函数内部 import 过 → 桌宠拖拽必然 NameError → 表现就是"按下拖不动"；
2. `_u32` 顶层引用 —— **这次拆分引入的**：`webapp.py` 末尾有模块级 `argtypes` 声明，删掉 `_u32` 定义后 **import 的瞬间就崩**，而 `compileall` 完全通过；
3. `_CLR_CRASH_GUARD_INSTALLED` —— 上一轮遗留：`wv2_env` 里 `global` 它，变量却留在 `webapp`。

**检查器两次迭代**：先加了"顶层语句"检查（要跳过 `__main__` 自测块，否则误报）；再修掉"已定义"集合的判定——原来用 `symtable.get_symbols()`，`_u32.Attr = 1` 这种属性赋值会让它误判为已定义，**变异验证第一次因此误报通过**。改用 AST 只收集真实绑定后才抓住。

---

### `aab4c70` 窗口域拆分（唯一难点是循环 import）

**改动**：`main_window.py`（1,348 行：本地静态服务、GUI 心跳/卡死 dump、4 个 `create_*_window`、`run_webview`）+ `api_window.py`（`WindowAPI` 17 个方法）。`webapp.py` 18,371 → 16,616 行。

**难点**：`create_main_window` 等函数内部要 `CharacterAPI()`，而这两个类还在 `webapp.py`——模块级 import 会成环。解法是**函数内延迟 import**（4 处，带 `PLC0415` 注释）。

---

### `5fa19d8` 通话链路补 22 项冒烟

**问题**：`webapp` 通话链路零测试覆盖，而其中几个逻辑一旦改错，用户直接感知、日志看不出来。

**覆盖**：`_finalize_device_list` 7 项（WDM-KS 剔除、空名端点剔除、31 字符截断重名丢弃、同名多 host 合并、`id` 兼容老配置、顺序稳定）；`_features_to_engine_config` 15 项（含 **解耦开关下"云端语音+本地看屏"必须并存**、`vision_models.enabled` 恒 True——这是 2026-10-04 实锤过的 bug）。

**踩坑**：变异验证第一次是**假通过**——我只改了 1 处 `vm["enabled"] = True`，而测试没覆盖解耦分支。补断言后全量替换，3 项 FAIL 才被抓到。

---

### `c566bc9` 静默异常：我第一版报告写重了

**问题**：原报告称"`cloud_omni_realtime.py` 有 **41 处**吞异常，通话失败无从排查"。实际核下来，那是把**静默总数**当成了**真空吞**。

**实测**（`tools/check_silent_except.py` 区分"带注释的有意识降级"与"无注释的真静默"）：

| 范围 | 静默总数 | 真静默 |
|---|---|---|
| `cloud_omni_realtime.py` | 49 | 1（`queue.Empty`，正常） |
| `dialogue.py` | 17 | 0 |
| `tts.py` | 17 | 8（7 处临时文件清理） |

**唯一值得修的 1 处**：`tts.__init__` 解析双工 TTS 配置失败时静默退回默认值 → 缓存/预取/stale 阈值全变，用户只感到"变卡"，日志零线索。已补 `logger.debug(exc_info=True)`。

---

### `add925a` 生产代码遗留旧盘符

**问题**：项目从 `G:\ai对话\11` 搬到 `D:\Denia` 后，`src/tts/cloud_clone_bridge.py` 仍写死旧路径，且第 41 行在 **import 阶段**执行 `CACHE_DIR.mkdir()`：

```
>>> import src.tts.cloud_clone_bridge
FileNotFoundError: [WinError 3] 系统找不到指定的路径。: 'G:\\'
```

**改动**：`CACHE_DIR` / `DANIYA_REF` 默认值改为相对仓库根（参考音指向确实存在的 `assets/voices/daniya_ref.wav`）；`mkdir` 改为惰性调用，目录不可写只告警降级。

**同时纠错**：原报告"167 处 `open()` 未指定 `encoding`，读中文 JSON 必炸"是粗 grep 的产物（把二进制模式、`Image.open`、`pa.open` 全算进去了）。AST 复核后 **`src` + `main.py` 实为 0 处**，`tools/` 下 2 处已补。

---

### `3fb4a71` 看板娘根本不在仓库里

**问题**：`src/ui/web/live2d/rig` 以 gitlink（mode `160000`）记录，但仓库**没有 `.gitmodules`** → clone 后只得到一个空目录。而它是**运行时依赖**：

- `src/ui/web/index.html:291` — 主界面右下角 `<iframe src="live2d/rig/index.html?v=2">`
- `src/ui/main_window.py:868` — 桌宠透明窗加载同一页（`?bare`）

**后果**：任何人重新 clone 或按目录打包，看板娘和桌宠窗一起消失，日志里只有一条 404。本地 961 行自研改造（`__rigControl`、`__petInteract`、`petShowBubble`、`?bare` 透明模式、FX 偏移持久化）也不在任何历史里。

**改动**：移除 gitlink，9 个文件改为普通文件跟踪（先导出 1251 行补丁核对，上游只有 1 个提交 `d488258` 可重取）；新增 `LOCAL_CHANGES.md` 记录上游基线与每项改造；新增 `tools/check_web_assets.py`（HTML 的 `src`/`href` 存在性断言）。

**验证**：变异——把 `rig/index.html` 临时挪走 → 精确报出 `index.html` 与 `index.legacy.html` 两处引用并 `exit 1`。

---

### `1207233` 打包闸从单向变双向

**问题**：原闸只查"不该带的"（`data/`、`call_records.json` 等）。看板娘事故是"该带的没带"，一条都拦不到。

**改动**：`--dir` 模式在隐私检查后追加"包内 HTML 引用的本地资源必须存在"（复用 `check_web_assets.scan_dir`，根相对路径跳过以免误报，`--no-assets` 可关）。

**验证**：三组假包——含隐私目录 → `exit 1` 逐项列出；正常 → `exit 0`；删掉 `rig/index.html` → `exit 1` 并指出两处引用。

---

### `206cc47` 导入冒烟 + 依赖对账

**问题**：静态检查证明不了"模块能被加载"，当天已经踩到两次只在 import 时才暴露的问题。

**改动**：
- `tools/smoke_import.py`：每模块独立子进程 import、超时即杀、按错误类型归类；配 `import_allowlist.txt`（带原因），`--strict` 只对"非预期失败"报错。
- `tools/check_requirements.py`：AST 真实 import → `packages_distributions` 映射发行包 → 与 requirements 对账；支持别名表（`clr`→pythonnet、`win32*`→pywin32）、注释排除、`# not-pip:` 登记、运行时注入插件豁免。

**结果**：155 模块 / 149 通过 / 6 已知可选。修掉两个漏声明：`pythonnet`、`websocket-client`。新增 `requirements-optional.txt`（A 段可 pip、B 段 `not-pip` 登记，避免 `pip install -r` 直接失败）。

**验证**：三次变异（删白名单行、注入 import 期崩溃、删 `not-pip` 行）全部被抓。

---

### `01d185c` CLR 崩溃守卫从未装上

**问题**：`_install_clr_crash_guard` 一次都没生效，两层原因叠加：

1. 只写 `import System`，而 pythonnet 3.x 必须先 `import clr` 再 `AddReference` → 恒定 `ModuleNotFoundError`；
2. 调用点在 `import webview` 之后、`create_window` 之前，而 **`import webview` 不会加载 clr**（实测 `'clr' in sys.modules == False`）。

结果：每次启动只留一条 warning，兜底形同不存在。

**改动**：守卫默认"CLR 未加载就返回"（不给测试平白拉起 .NET）；新增 `force_load` 参数，建窗前主动 `import clr` + `clr.AddReference("System.Windows.Forms")`（与 pywebview 自身一致）；修正 `UnhandledExceptionMode` 取法（是 `System.Windows.Forms` 的类，不是 `Application` 属性）。

**验证**：默认参数下 `_CLR_CRASH_GUARD_INSTALLED` 仍为 False 且 clr 未被加载；`force_load=True` 后变 True。

---

### `5b348a5` 6 处无界队列改有界丢最旧

**先纠错**：13 处 `Queue()` 里真正需要处理的只有 6 处。`event_bus` / `tts` 用 `await put()`（asyncio 阻塞投递）天然背压，加 `maxsize` 反而可能死锁；`webapp` 两处是函数内临时队列。

**新增 `src/queue_util.py`**：`put_drop_oldest` / `put_drop_oldest_async`，满则丢最旧、绝不阻塞。为什么不是"丢新"——那会让**最新一句话永远进不来**。

| 位置 | 上限 | 说明 |
|---|---|---|
| `voice/audio_io.py` | 32 帧 ≈16s | **最严重**：sounddevice 回调每 0.5s 无条件 put，识别线程被推理拖住就无界涨（32KB/s） |
| `llm/cloud_omni_realtime.py` | 256 块 | 实时音频增量 → 播放线程 |
| `channels/gewechat.py` | 200 条 | HTTP 回调线程 → 主循环 |
| `ui/chat_overlay.py` | 32 条 | tkinter 轮询 |
| `companion/course_recorder.py` | 500 段 | 转写线程 → `_tick` |
| `ui/api_pet.py` | 20 条 | 桌宠窗 250ms 轮询 |

**验证**：`src/tests/test_queue_util.py` 14 项全绿；变异（丢最旧→丢新）4 项 FAIL。

---

### `a4a6a38` 交付目录组装器 + launcher 去旧盘符

**问题**：项目根同时是开发目录——`config.yaml`（真实 Key）、`call_records.json`（141 条真实通话）、`data/`、`.venv/`（692MB）、290 个一次性脚本都在同一个文件夹。只要打包方式是"复制文件夹"，这些就会一起进安装包。

**改动**：新增 `tools/make_dist.py`，白名单是唯一清单来源，默认 dry-run：

- 目录：`src` / `assets` / `prompts`（+ 可选 `python` 运行时）
- 文件：`main.py`、`launch_companion.py`、`run_webui.py`、`README`、两个 requirements
- 改名：`config.release.yaml` → `config.yaml`
- `tools/` 只带 `clone_danya_voice.py`——运行时真会调用它（`proactive.py:5097`、`cloud_omni_realtime.py:2218`），其余 290 个是开发脚本
- 输出目录非空且未加 `--force` 时**拒绝执行**（只覆盖同名文件，绝不删多余文件）

**实测发现（重要）**：内置 `python/Lib/site-packages` 里**只有 pip**，没有 yaml，也没有 `.pth` 指向 `.venv`，`main.py` 里同样没有 site-packages 引导。**单靠 `python/` 打出来的包双击起不来**——现有发布流程必然依赖「安装环境.exe」往运行时补装依赖。已加成第三道闸门：默认告警，`--strict-deps` 下失败。

**顺带修打包闸误判**：`FORBIDDEN_DIRS` 原本全树匹配目录名，把内置运行时里天然存在的 `python/Lib/venv`、`pip/_internal/operations/build` 判成隐私目录，正常包直接不合格。改为只看深度 2 以内。变异验证：`dist/data/` + `dist/logs/` → `exit 1` 并逐项列出；删掉 → `exit 0`。

**顺带重写 `launch_companion.py`**：旧版写死 `glob.glob("G:/ai*")` 和别人机器上的 `C://Users//Administrator/.../python.exe`。改为按 `__file__` 定位 + 优先内置 `python/python.exe`，日志改到 `logs/launcher.log`。

**验证**：dry-run 65.3MB / 1334 文件；实际组装成功；闸门 1 通过、必需文件齐全、闸门 3 正确报出依赖未装。

---

### `2f14c0a` 图标/任务栏 + 置顶 64 位句柄（用户反馈：像 python 脚本、切窗卡死、背后白底）

**① 任务栏显示 Python、没有图标**
仓库里本来就有 `app.ico`（30KB），但从未被使用。两处修：
- `main.py` 早期设 `SetCurrentProcessExplicitAppUserModelID("Daniya.DesktopCompanion.1")`——不设时任务栏按 python.exe 归组；
- `webview.start(..., icon=app.ico)`，pywebview 在 `winforms.py:242-244` 会设给窗体。

实测：`GetCurrentProcessExplicitAppUserModelID` 读回一致；窗口 `WM_GETICON` 返回非零句柄。

**② 切窗卡死：置顶保活每秒跨线程写 WinForms 属性**
`_pet_topmost_keepalive` 每秒调 `_pet_apply_topmost()`，原实现优先 `STATE.pet_win.on_top = ...` → `form.TopMost = ...`（`winforms.py:1006`）。WinForms 控件属性不允许跨线程访问，与切窗（激活变化）叠加时容易卡死。改为**永远优先纯 Win32 `SetWindowPos`**。

**③ 顺带查出：Win32 置顶一直没成功过**
改完实测 `SetWindowPos` 返回 0、`GetLastError=1400`（无效句柄）。根因是 **ctypes 没声明 argtypes**：`HWND_TOPMOST=-1` 按 C int 传成 32 位 `0xFFFFFFFF`，不是 64 位全 1 的伪句柄。

这解释了原代码为什么「优先用 on_top」——不是偏好，是 Win32 那条路一直失败。现在在 `pet_window` 里声明齐 `SetWindowPos / FindWindowW / GetWindowRect / GetWindowLongW / SetWindowLongW` 签名，并用 `_hwnd_arg()` 把 -1/-2 包成 `c_void_p`；`agent_tools._win32_focus_hwnd` 的 TOPMOST 翻转兜底同样修了。

实测：置顶 `set=True` 且 EXSTYLE 含 `WS_EX_TOPMOST`；取消后 `set=True` 且该位清除；last error = 0。

**④ 白底长方形：四条原生路线实测全部不可行（只记录，未改行为）**

新增 `tools/diag_pet_window.py`：起同参数窗口 → 截屏取角落像素 → 与"藏窗后的真背景"逐点比对。

| 方案 | 结果 |
|---|---|
| 现状 `transparent=True` | 四周均匀 `#F0F0F0`（WinForms 客户区底色） |
| `WS_EX_LAYERED` + colorkey | 整片变 (32,32,32) 实色，桌面透不出来 |
| `WS_EX_NOREDIRECTIONBITMAP` | 无变化，仍 `#F0F0F0` |
| `DwmEnableBlurBehindWindow`（空区域） | 变 (255,255,255)，仍是填充色 |
| `SetWindowRgn` 圆角裁剪 | 裁掉部分变纯黑，比白底更糟 |

原因：WebView2 是 DirectComposition 硬件合成面，宿主窗体一旦分层/裁剪，合成链就断，只剩填充色。`_pet_apply_region` 曾实现并接入，实测四角变黑后**已回滚**。

**教训**：`transparent=True` 在 pywebview 的 Windows 后端只是"把页面背景设透明"，不等于桌面透明。验证窗口透明度必须**截屏 + 与藏窗后的真背景逐像素比对**，光看"有没有变黑"会误判（我一开始把 240,240,240 判成"非白"，差点据此下错结论）。

---

## `e46f21b` 单实例锁 + 壳层前置（2026-10-18 00:40）

**问题**：全仓搜不到 `CreateMutex`——双击两次图标就是两个进程、两只宠物、两套后端，互相抢麦克风和麦克风设备。
顺带发现 DPI 感知实测值为 **0**（不感知），而 `notify_geom.py`、`create_desktop_companion` 等多处注释都写着"进程已 SetProcessDPIAware"。

**改动**：新增 `src/win_shell.py`，四个能力（DPI 感知、读回感知级别、单实例、唤起已有窗口），
在 `main()` 最前面调用——**必须在任何窗口创建之前**，否则系统静默忽略且不报错。
`--no-gui` 是控制台工具模式，跳过单实例检查。

**验证**：`src/tests/test_win_shell.py` 6 项全绿（DPI 0→2；同名锁第二次 False、子进程 False、异名锁 True）。

**没有做的事（诚实口径）**：我原以为 DPI 不感知会导致高缩放文字模糊，实测 `devicePixelRatio` **已经是 1.5**、
开关 DPI 感知窗口矩形完全一致——页面本来就按原生分辨率渲染。所以这条的收益是"Win32 坐标与注释假设一致"，
不是修可见缺陷。**不把它写成"修好了模糊"**。

---

## `86fa3df` AGENTS.md + 统一门禁（2026-10-18 00:45）

**问题**：闸门散在 `tools/` 下十几个脚本里，靠人记必漏——这个项目已经漏过一次（看板娘整个目录没进版本控制）。

**改动**：`tools/check_all.py` 编排 16 项闸门（`--fast` 跳过慢项，约 5 秒回环），只做编排与汇总，
检查逻辑仍以各自脚本为单一真值源。`AGENTS.md` 把踩过的坑写成条款，重点是：
证据口径、变异验证、Windows 三条硬约束、WebView2 透明已确认的结论、环境坑、交付红线。

**验证**：16 项全 PASS；变异（临时挪走 `check_web_assets.py`）→ `exit 1` 并点名失败项，还原 → `exit 0`。

---

## `6d75b24` 换壳可行性 PoC（2026-10-18 00:52）

**问题**：桌宠背后那块白底，之前一直被当成"参数没调对"。

**查到底**：pywebview 的 Windows 后端是 WebView2 **windowed hosting**，不提供每像素 alpha。
用**最小用例**（完全不含项目页面）逐个手测 8 种原生手法：

| 手法 | 角落像素 |
|---|---|
| 基线 `transparent=True, frameless=True` | `(240,240,240)` |
| `WS_EX_LAYERED` + `LWA_ALPHA(255)` | `(240,240,240)` |
| `WS_EX_LAYERED` + `LWA_COLORKEY(#F0F0F0)` | `(32,32,32)` |
| `Form.TransparencyKey = #F0F0F0` | `(32,32,32)` |
| `Form.BackColor = ARGB(0,0,0,0)` | `(240,240,240)` |
| `WS_EX_NOREDIRECTIONBITMAP` | 无变化 |
| `DwmEnableBlurBehindWindow` | 白色填充 |
| `SetWindowRgn` 裁剪 | 裁掉处纯黑 |
| `WS_EX_LAYERED` + `LWA_ALPHA(0)` | 整窗消失（**反证：DWM 合成链路是通的，缺的只是顶层 alpha**） |

**正证**：同一份 `rig/index.html?bare=1` 换到 Electron 宿主，前端一行未改，
`window.pywebview.api` 由 `shell/preload.js` 的 Proxy shim 顶替，结果——窗口隐藏态与显示态三处角落**逐点相等**，
中心不同（人物在渲染）。截图里人物背后是后面的应用窗口文字，不是白底。

**踩坑两条**（已写进 AGENTS.md）：本机 shell 带 `ELECTRON_RUN_AS_NODE=1` 会让 Electron 退化成 node（`app` 未定义）；
读子进程 stdout 用裸 `readline()` 会无限阻塞，第一次跑直接挂满 180 秒、且 `python` 不加 `-u` 时被杀后一个字都不留。

**产出**：`产品化路线_2026-10-18.md`（证据表、三方案代价、四步可回滚迁移与验收）。

---

## `4792f40` 换壳第 1、2 步：RPC + 壳侧 shim（2026-10-18 01:20）

**为什么不是直接开始搬窗口**：透明只是外观。换壳真正会翻车的是"页面调用的
`window.pywebview.api` 没人实现"——rig 页面有几十处这样的调用。把方法在壳里重写一遍，
必然与后端漂移；正确做法是**让两条通道指向同一个 `char_api` 实例**。

**Python 侧**（`src/ui/rpc_server.py`）：
只绑 `127.0.0.1`、端口随机、每次启动生成令牌；只允许公开成员（下划线/dunder 一律拒绝）；
请求体上限 32MB；业务异常原样回报（不吞）；不可 JSON 化的返回值降级为字符串并标 `degraded`。
`main.py` 新增 `--rpc` / `--rpc-handshake`，启动后把 `{url, token, web_base}` 写进握手文件
（先写临时文件再 rename，避免壳读到半截 JSON）。

**壳侧**（`shell/preload.js`）：Proxy 把任意 `api.xxx()` 转发到 RPC。
**失败一律抛异常，不静默返回 null**——页面大量 `await` 之后判断返回值，
静默 null 会被当成"功能正常但没有数据"，是最难查的一类 bug。

**验证（两层）**：
1. `src/tests/test_rpc_server.py`：19 个方法两条通道结果**逐一比对完全一致**；
   缺/错令牌 401、私有方法 403、不存在 404、超大体 413、OPTIONS 204、
   业务异常 500 保留类型、不可序列化降级、`stop()` 后端口关闭。
   变异（把 `_authorized` 改成恒真）→ 4 项失败，还原 → 全绿。
2. `tools/rpc_bridge_smoke.py` 连续两次通过：

```
PROBE {"hasBridge":true,"psdType":"function","title":"看板娘[OK]","psdLen":4187477}
后端收到 RPC 调用：['get_live2d_psd']
窗口真透明（角落与背景差 0/0/60，背景噪声 70，容差内）
```

**踩到的假阳性（重要）**：第一版用 **stdin** 给壳发 `hide`/`show`。Windows 下管道里的 stdin
收不到数据，命令全丢 → "隐藏后抓屏"抓的其实是显示态 → 两次采样一样 → 得出"透明 ✅"。
现在命令走 TCP 且必须有回执，拿不到回执直接判失败。已写进 AGENTS.md。

**顺带修正判定口径**：桌面背景本身会变（壁纸动画/背后窗口刷新），严格"逐点相等"会把真透明
判成失败（第二次跑就出现了 14 级差）。改为先连续抓两次隐藏态量化噪声，再看显示态是否落在
噪声内；并单独检测 `#F0F0F0`（WinForms 客户区底色）作为反面特征。

---

## 待办（需要你决策）

| 项 | 现状 | 需决定什么 |
|---|---|---|
| **桌宠白底（换壳）** | ✅ 已确认 Electron 可行，第 1、2 步（RPC + shim）已落地并端到端验证 | 下一步是第 3 步：把桌宠窗真正切到壳上（要动 `run_webview` 的启动分支），动手前确认是否同时迁主窗口 |
| **tkinter 两功能不可用** | 内置运行时无 `Lib/tkinter`、无 `_tkinter.pyd`、无 `tcl/`，OCR 校准与对话悬浮层在任何机器上都返回"不可用"（代码优雅降级，不崩） | 补 tcl/tk（+10MB，版本须严格匹配）／改 WebView2 浮层／隐藏入口 |
| **交付物依赖安装** | `tools/make_dist.py` 已能产出干净目录（65.3MB），但内置运行时只有 pip，必须再执行 `dist/python/python.exe -m pip install -r requirements.txt`（或走「安装环境.exe」）才能启动 | 这段安装是留在安装器里，还是做成 `make_dist.py --install-deps` 一步到位 |
| **打包方式** | 目前是"便携目录"，`dist/` 已在 `.gitignore` 内 | 若迁 Electron，则整体转为 electron-builder + NSIS |

---

## 项目级经验（踩过的坑，别再踩）

1. **`sed -i` 会洗掉 CRLF**：中文 Windows 项目的 YAML/配置文件，改单行用 Python 二进制定点替换。
2. **`compileall` 通过 ≠ 能加载**：`webapp.py` 顶层 `argtypes` 引用已删名字，编译全绿、import 必崩。每次拆分后必须真实 import 一次。
3. **手工维护的"可变全局清单"一定会漏**：漏了 `_WV2_CONTROLS`，代价是 WebView2 控件永远注册不上且无日志。用工具扫，不要靠记。
4. **测试"通过"可能是假的**：变异验证要确认真的跑在变异体上（`/tmp` 在 Git Bash 和 Windows Python 下不是同一个目录，`cp` 失败时 `;` 让 `$?` 变成 cp 的退出码）。
5. **数字对不代表结论对**：13 处无界队列只有 6 处该改；167 处缺 encoding 实为 0 处。报数前先确认口径。
6. **修安全默认值前先 grep 实际配置**：白名单里有 `start`，`shell=False` 会把它从"能开程序"变成"执行失败"。
