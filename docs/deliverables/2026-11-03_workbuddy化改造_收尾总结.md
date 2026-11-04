# Denia → WorkBuddy 化改造 · 收尾总结

> 日期：2026-11-03 13:35 – 15:10
> 范围：`D:/Denia`（公开仓库，MIT，master 分支）
> 状态：代码已落地并端到端验证，**改动仍在 working tree 未提交**

---

## 0. 一句话定性

Denia 已从「桌宠」拆成「**桌宠外壳 + WorkBuddy 内核**」：
默认启动的是**工具引擎**（任务是一等公民），Live2D 桌宠退化为**手动拉起的可选皮肤层**——
这正是 WorkBuddy 自身的形态（对话窗是引擎，buddy 宠物形象是对话窗上方的皮肤）。

**血缘上**不是 datawhale 的 `mini-workbuddy` 仓库（独立代码线）；
**概念上**是一个 mini-workbuddy（本地轻量 agent，有真实工具层 + 任务账本 + 中性人设 + 无壳入口），
且工具层比教学仓库实在得多。

---

## 1. 交付物清单

### 新增文件（4 个）

| 文件 | 行数 | 作用 |
|---|---:|---|
| `workbuddy.py` | 244 | 交互式 REPL 入口。默认=工具引擎；`--pet` 手动拉起桌宠浮窗 |
| `src/system/tasks.py` | 291 | 任务账本。三态判定 + 统计，纯 stdlib |
| `src/system/task_cli.py` | 125 | 无壳命令行入口 `python -m src.system.task_cli "打开记事本"` |
| `prompts/workbuddy_persona.txt` | 11 | 中性任务导向人设（取代达妮娅陪伴人设） |

### 修改文件（2 个）

| 文件 | 改动 | 要点 |
|---|---:|---|
| `src/llm/agent_brain.py` | +282 | 任务账本接线、文本 tool_call 兜底、意图闸门、复读拦截、身份参数化 |
| `src/llm/agent_tools.py` | +86 | `open_app` 双通道 + 启动后核验（不再「报成功就算成功」） |

### 明确未触碰

- `main.py`、`cascade_brain.py`、`config.yaml` —— **桌宠路径零影响**（下文 §4 有回归证据）
- `data/`、`logs/` 已被 `.gitignore` 排除（`:37` / `:39`），运行产物不进仓库

---

## 2. 运行产物（`data/tasks.jsonl`）

任务账本是这次改造的**核心资产**——它让「用数字说话」成为可能。

```
total=13  ok_true=6  ok_false=5  ok_none=2  ok_rate_decided=0.545
human_intervened=6  duration_total=116.9s
tools: open_app×7  ui_inspect×6
```

### 13 条记录 = 完整排障轨迹（每条 False/None 都是当时的真实缺陷，不是噪声）

| # | 判定 | 工具 | 现象 |
|---:|---|---|---|
| 1 | NONE | open_app | stub 记录（判定加固前写的） |
| 2 | FAIL | — | HTTP 429 模型过载 |
| 3 | FAIL | — | **假绿 #1**：输出像干了，工具一次没跑（`tools_used=[]`） |
| 4 | NONE | open_app,ui_inspect | 工具跑了但事没成（notepad 未启动） |
| 5-6 | OK | open_app,ui_inspect | 修好后的成功样本 ×2 |
| 7 | FAIL | open_app,ui_inspect | **假绿 #3**：收尾复读系统提示词 |
| 8 | FAIL | — | 身份冲突：自称"达妮娅"（口径错位，非事实） |
| 9 | OK | — | 身份修好 + 记账口径修好 |
| 10 | FAIL | — | 管道喂 `exit` 被确认闸当拒绝（测试环境假象） |
| 11-13 | OK | open_app,ui_inspect | 闲聊路径 + 任务路径**最终双绿** |

**这 13 条本身就是交付物的一部分**：它们证明账本在假绿面前**没有变绿**——
是 `tools_used` 与硬化后的判定替我们抓出了 bug A。

---

## 3. 修掉的 6 个 P0

### P0-A：尾部 assistant 消息 ⇒ 原生 tool_calls 丢失 ⇒ 工具从未执行（假绿 #1）

- **现象**：输出 `（执行计划）1. open_app 记事本`，看着像干了；账本 `tools_used=[]` `steps:0` ⇒ 真相是一次都没跑。
- **根因**：计划作为**最后一条 assistant 消息**注入，模型顺着它"接着往下写"，把工具调用写成 ```json 文本，`if not tool_calls:` 分支把它当最终回复直接返回。
- **A/B/C/D/E/F 对照实验（决定性）**：user 尾部 / BASE_PROMPT / 计划注入 全部 `tool_calls=1`；**仅"尾部=assistant"（真实顺序）→ `tool_calls=0`**；补一条 user → 恢复 1。**与模型能力无关、与 prompt 无关，纯粹是消息顺序。**
- **修复**：① 计划后补 `{"role":"user","content":"按上面的计划开始执行。"}`；② 新增 `_parse_text_tool_call()` 把文本调用还原成标准 tool_calls（**只认已注册工具名**，防乱调）。

### P0-B：`open_app` 报成功但事没办成（假绿 #2）

- **现象**：工具返回 `已通过 Shell 委托启动：notepad`，但 **notepad 进程 NOT RUNNING**。
- **根因**（`agent_tools.py:115`）：`_launch_token` 只走 `explorer.exe tok` 一条通道；Win11 上 Store 化记事本经 explorer 开不出来。
- **通道对照实测**：`explorer.exe notepad` ❌ / **`os.startfile("notepad")` ✅（窗口"无标题 - 记事本"）** / `explorer shell:AppsFolder\...` ❌。
- **但**仓库注释载明：汽水音乐等 Electron launcher 直启会 code 0 秒退 ⇒ explorer 是其唯一可靠通道 ⇒ **不能全换**。
- **修复**：**双通道 + 启动后核验** —— explorer 委托 → `_target_visible()` 核验（复用 `_win32_windows()`）→ 不成换 startfile → 仍不成**如实回报"未检测到窗口"，绝不报成功**。

### P0-C：`human_intervened` 指标永久虚高

`bool(confirmed)` 把 `--confirm auto` 的自动放行也记成人工介入。**修复：auto 模式根本不挂确认钩**（语义等价）⇒ `confirmed=0` / `human_intervened=false`，指标恢复诚实。

### P0-D：规划无条件注入 ⇒ 闲聊被当任务 + 收尾复读（假绿 #3）

- **现象**：输入「你是」→ 凭空编出"打开记事本/输入文本/保存文件"计划；收尾输出把注入的自检提示**原样复读**给用户。
- **A/B 对照（决定性）**：同样输入「你是」—— A 无计划注入 → `tool_calls:0` 正常回答 ✅；B 有计划注入 → `tool_calls:1 (open_app)` 乱执行 ❌。
- **复读的唯一变量也是计划注入**（精确复现 4a/4b）：4a 完整真实序列 → 复读=True；4b 去掉计划注入 → "动作已生效，未发现异常。"复读=False。
- **机制**：注入的计划让模型进入"执行计划"框架，而末尾 `role=user` 的自检提示成了"最后一条待答的提问" ⇒ 被原样吐出。
- **修复**：① 规划阶段加**意图闸门**；② 判定为"不需要动手"时**一条计划消息都不注入**（根因）；③ 收尾加**复读检测** `_is_echo()` —— 命中就拿同一上下文**不带 tools** 重问"用你自己的话汇报"，仍复读则如实说明，**绝不透传提示词**。

### P0-E：`_BASE_PROMPT` 硬编码"达妮娅" ⇒ 工作助手自称达妮娅

- **根因**：`workbuddy_persona.txt` 前置，但 `_BASE_PROMPT` 第 1 行"你是「达妮娅」的电脑操作代理" + 规则 7"用达妮娅的口吻"**在后**，覆盖了前置人设。
- **修复**：`_BASE_PROMPT` 的 `{agent_name}` 参数化 + `AgentBrain(agent_name=...)` + `from_config` 读 `cfg.ns.agent.agent_name`（默认"达妮娅"）+ `workbuddy.py` **仅内存**置"助手"。
- **安全**：`_BASE_PROMPT` 内只有 2 对花括号（全为 `{agent_name}` 占位符）⇒ `.format()` 安全；全仓库只有 1 个渲染点。

### P0-F：闲聊被账本误判为失败（口径错位）

「你是」模型**正确**回答了，但 `ok:false` —— 因为 `judge_ok` 的"没调工具→False"规则对**工具型任务**正确、对**闲聊型任务**是误判。
**修复**：`judge_ok(..., expect_tools=True)` 新参数；`AgentBrain._last_is_chat` 标记闲聊型；新增 `_CHAT_NEG_MARKERS`（"无法做到/不知道/不支持"等**能力类**否定不是动作动词，`_NEG_RE` 抓不到）。

---

## 4. ⚠️ 修 bug 时差点引入第四个 bug（本次最值钱的教训）

第一版意图闸门把"闲聊"和"简单任务"塞进**同一个标记**"无需多步计划"。

**真实 API 采样 9 条**后发现：**真任务也被判成 noop**
（"把桌面上的 report.docx 重命名为 周报.docx" → 模型也吐 `无需多步计划`）。
若沿用此版 ⇒ `_last_is_chat=True` ⇒ 记账 `expect_tools=False` ⇒
**"没调工具的真任务"被记成成功** —— 正是我们一直在防的假绿。

**修正**：改**显式标签驱动**（模型第一行必是 `[闲聊]` / `[任务]`），
标签缺失才退回"整段精确匹配"兜底，**拿不准一律当任务型**（保守：宁可要求调工具也不放过真任务）。
`_split_plan_tag()` 返回 `(is_chat, 计划正文)`。

> 单元用例全绿、真实采样才暴露 ⇒ **判定函数必须做真实 API 采样回归，不能只过单元用例。**

---

## 5. 验证证据

### 判定与解析用例

| 组件 | 用例 | 结果 |
|---|---:|---|
| `_split_plan_tag` | 10 | **10/10** ✅（含"真任务不得被误判成闲聊"） |
| `judge_ok` | 11 | **11/11** ✅ |
| `_is_echo` | 6 | **6/6** ✅ |
| `_parse_text_tool_call` | 7 | 6/7（唯一 MISS 是**截断 JSON 被拒绝**——保守方向正确） |
| 真实 API 意图分流采样 | 9 | **9/9** ✅ |

### 端到端（真机，非 stub）

```
路径1 闲聊「你是」：
  结果：我是工作助手，一个运行在本地的电脑操作智能体…
  账本：ok=true  tools_used=[]  steps=0
  → 不再自称达妮娅、不再执行工具、不再复读提示词

路径2 真任务「打开记事本」：
  · 步骤 1/15 · 正在执行：open_app   （确认闸 y 通过）
  · 步骤 2/15 · 正在执行：ui_inspect
  结果：记事本已成功打开。
  账本：ok=true  tools_used=["open_app","ui_inspect"]  steps=2  confirmed=1
  硬证据：Get-Process notepad → RUNNING，窗口「无标题 - 记事本」
```

### 回归（零回归证据）

```
tools/check_all.py --fast  →  114/117 通过，3 项失败

【决定性对照】git stash 掉本次全部改动 → baseline 闸门同样是 3/117 失败，
失败项完全相同（配置引用体检 / 配置引用判级 / 参考音频物化）
⇒ 这 3 项与本次改造零关联。

【2026-11-04 二次更正（以一手证据为准）】
本节原写「转公开时清理私人音频（.wav）导致的既有状态」——**这个说法是对的**。
中途曾据 `bf04139` 的提交说明改成「文件从未进过 git」，**那次改错了，现改回**。

`bf04139` 的推理是：`git log --all --name-status -- assets/voices` → 空
⇒ 「从没进过 git」。但 11-03 转公开时做的是 **filter-branch 重写历史 +
删 refs/original + reflog expire + gc --prune=now**，
**查不到正是清理成功的结果，不是「从未发生」**。用被重写过的历史反推历史，是循环论证。

一手证据（不依赖当前 git 历史）：
  · `D:/Denia-pre-public-20261103.bundle`（清理**前**的备份快照）
    → `git ls-tree -r` 含 4 个 .wav：
      assets/voices/daniya_ref.wav、daniya_ref_0813_24k.wav、
      src/ui/web/_role_assets/ref/daniya_ref.wav、ref/xiaowei.wav
  · 11-03 操作日志：转公开时**两轮 filter-branch 专门清这 4 个** + force push，
    远端验证 `tree 内 wav = 0`
  · 清除原因原文：「用户确认这两个音频**来自他人或特定角色** → 涉**声音权**
    （民法典第 1023 条），公开前必须清除」
⇒ 3 道闸门红的成因是「**配置引用着被主动清除的文件**」，不是「文件从未存在」。

11-04 由 `bf04139` 从建仓前的 `DanYaAI.zip` 找回三份真素材并**重新提交**，
闸门随之转绿。

⚠️ **因此 push 的真正风险不是「隐私措辞」，而是推翻 11-03 已有的法律判断**：
这三个 wav 是他人/特定角色的原始录音（CHANGELOG 亦记 `xiaowei.wav` 是「她的原始录音」），
`origin/master` 上没有它们 —— **push 会把它们重新放进公开仓库**。
`bf04139` 的提交说明不代表授权状态变化，只是把文件找回来了。
push 前需用户明确拍板：确认音源可公开，或重新排除。

config.yaml 未改动（git status 空）；main.py / cascade_brain.py 未改动
桌宠默认仍读 cascade.persona_file = 'prompts/call_profiles/danya_persona.txt'
桌宠身份渲染仍是「达妮娅」
```

### 已知既有失败（非本次引入，需决策）

`config.yaml` 仍引用 `assets/voices/daniya_ref_0813_24k.wav`（`:393` / `:408`），
该文件在转公开时随私人音频一并清除 ⇒ 3 道闸门失败。
修法二选一：把参考音频换成无版权素材，或把这几处引用清空。**与工作助手模式无关。**

---

## 6. 使用方式

```powershell
cd D:\Denia

# 纯工作助手（默认）
.\.venv\Scripts\python.exe workbuddy.py --model glm-4-flash --confirm ask

# 同时浮起桌宠形象（独立进程，手动开的皮肤层）
.\.venv\Scripts\python.exe workbuddy.py --model glm-4-flash --confirm ask --pet

# 无壳单命令
.\.venv\Scripts\python.exe -m src.system.task_cli "打开记事本"
```

| 参数 | 说明 |
|---|---|
| `--model` | 覆盖 LLM 档位（**仅内存**，不改 config.yaml）。当前必须用 `glm-4-flash` |
| `--confirm ask\|auto` | 高危工具是否逐项询问。`auto` 不挂确认钩 ⇒ `human_intervened=false` |
| `--pet` | 拉起桌宠浮窗（`main.py` 作独立进程） |
| `--persona` | 指定人设文件相对工程根 |

### 模型可用性矩阵（2026-11-03 实测，同账号同一时刻）

| 模型 | 结果 |
|---|---|
| `glm-4.7-flash`（config 默认） | ❌ 429 `1305` 该模型当前访问量过大（瞬时过载） |
| **`glm-4-flash`** | ✅ **HTTP 200（唯一可用档位）** |
| `glm-4-plus` / `glm-4-air` / `glm-4.5-x` | ❌ 429 `1113` **余额不足或无可用资源包** |

> `glm-4.7-flash` 的限流是**瞬时**的，恢复后 `--model` 可省；
> `1113` 是余额问题，得充值。

### 前置条件

`agent.llm.api_key` 是 `${ZHIPU_API_KEY}`，`src/config.py:145` 在加载时就地展开。
变量已在 **User scope 写进注册表**（len=49）⇒ **新开终端自动继承，无需 export**。

---

## 7. 未决事项

| # | 事项 | 优先级 |
|---|---|---|
| 1 | ~~本次 6 文件改动未提交~~ → **已于 `d2ccb6e` 提交**；截至 11-04 共 16 个提交 ahead 未 push | 高 |
| 2 | 闲聊自我介绍偏"技术说明书口吻"（"一个运行在本地的电脑操作智能体"）—— 要不要改得更像个人助手 | 中 |
| 3 | ~~参考音频 `.wav` 缺失导致 3 道闸门失败~~ → **`bf04139` 已找回三份，闸门已绿**；但这三个 wav 是 11-03 因**声音权**（民法典 1023 条）主动从 git 历史清除的他人/角色录音，**push = 重新公开**，需用户确认授权或重新排除 | **高** |
| 4 | 5 个 Live2D PSD 源文件版权未处理（用户已明确"都不用管"） | 低 |
| 5 | `task_cli` 尚未接进 `main.py --no-gui` | 低 |

---

## 8. 方法论沉淀（可复用到其他弱模型改造）

1. **弱模型 + 长 prompt 的问题，多半是「消息序列」问题，不是「模型能力」问题** ——
   一律用**对照实验**（只变一个变量）定位，别猜。
   本轮三个假绿（A/B/D）全部由"只变一个变量"的对照实验定位。

2. **判定函数必须做真实 API 采样回归** —— 单元用例全绿不代表对。
   本轮 `_is_noop_plan` 单元 11/11，真实采样却把真任务误判成闲聊。

3. **任何「关键词/短语」判定都要防误伤** —— 宁可保守（拿不准=任务型），也不假绿。

4. **改 prompt 时检查有没有更长的、更后面的规则在覆盖它** ——
   P0-E 的成因就是前置人设被后置硬编码身份覆盖。

5. **工具返回"成功"不等于事办成了** —— 必须有独立的**核验通道**（`_target_visible()`），
   且核验要能如实说"没生效"。

6. **指标口径唯一**：auto 放行不能算人工介入；闲聊没调工具不能算失败。
   `expect_tools` 参数化是这条原则的落地形态。
