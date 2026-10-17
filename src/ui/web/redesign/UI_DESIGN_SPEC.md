# 达妮娅桌面伴侣 · UI 设计规范（v4.1 · 二次元风格）

> **v4.1（2026-09-29 修订）**：核对代码后修正 §六 config 映射（原映射已过期，照做会接错线）、
> 新增 §七「设置页规范」（含开发者收口、开关状态样式、顶部切换处置）。
> 基线标签 `pre-refactor-20260929`。
>
> 角色名：**达妮娅**（此前误称达娅，本次统一）。
> 风格：二次元（粉樱 + 薰衣草紫 + 天空蓝的柔彩渐变；圆润可爱；轻微柔光与装饰）。
> 角色"在场感"由 **Live2D 模型**承担，**UI 不做角色立绘**，只负责二次元化框架 + 给 Live2D 留渲染位。

---

## 一、设计原则

| 要点 | 说明 |
|---|---|
| 角色在场 = Live2D | 聊天页内一个舞台 + 主窗口最小化时桌面透明小窗（桌宠）。UI 不生成/不重做立绘（rig 不可重做）。 |
| 二次元素但不 AI 味 | 用**统一柔彩渐变 + 圆润形状 + 单色强调**撑质感，不堆随机渐变球/霓虹/呼吸光晕。 |
| 配色 | 背景柔彩渐变；强调色樱粉 `#FF6FA5` + 薰衣草 `#9B6DFF` + 天空蓝 `#5BB8E8`；只用渐变填充在「主按钮 / 品牌标 / Live2D 舞台」三处。 |
| 图标 | 内联 SVG 线性图标（stroke 1.6，currentColor），不混 emoji 当主图标（emoji 仅作闲聊气泡里的语气点缀）。 |
| 字体 | 中文系统字为正文；衬线（Songti/STSong）用于品牌字、角色名、大标题，制造二次元"手写感"。 |
| 文案 | 真实内容（她在看什么、记了什么）；角色口吻用「🌸 在看你摸鱼哦～」这类轻语气，不空洞。 |

---

## 二、设计令牌（`design_tokens.css` 为真源）

- **主题**：浅色=白天甜系柔彩渐变；深色=夜晚梦幻紫；`prefers-color-scheme` 自动 + `data-theme` 手动覆盖。
- **表面**：`--surface` 实底卡、`--surface-2` 次表面、`--surface-glass` 半透明玻璃（侧栏/输入框/Live2D 舞台）。
- **文字**：`--ink` 暖深（非纯黑）、`--ink-2` 次、`--ink-3` 辅助。
- **边框**：`--line` 淡紫发丝边、`--line-2` 略重。
- **强调**：`--accent` 樱粉、`--accent-2` 薰衣草、`--accent-3` 天空蓝；`--accent-soft` 淡粉底；`--glow` 柔光。
- **圆角**：sm 14 / md 18 / lg 24 / xl 30 / pill 999（比 v3 更圆，更萌）。
- **阴影**：`--shadow-1/2/pop` + `--glow` 柔光（二次元标志）。
- **动效**：`--ease` 带回弹、`--dur-1/2/3`；`prefers-reduced-motion` 全关。

---

## 三、信息架构（按用户决策定稿）

**日常层**
- 对话（聊天主页：**左侧栏 + 对话 + 右侧 Live2D 舞台三栏**）
- 日报周报（仅日报/周报 + 预览/导出；屏幕活动流已移出）

**设置层**
- 角色：**达妮娅**唯一实装（标注 Live2D）+ **「开发中」占位卡**；**隐藏「新增角色」按钮**
- 语音·音色 / 视觉（含视觉总开关）/ 双工（含文本模式开关 + 主动开口强度滑块 0.2–1.0 默认 0.4）

**开发者（收口，标注只读/开发者）**
- 屏幕活动流 / 记忆库（只读·不可改）/ 模型设置 / 游戏校准 / Unity 视图 / 课程记录

**Live2D 两个布置点**
1. 聊天页内：右侧 `stage2d` 舞台（带换装/戳一戳/隐藏迷你控件）。
2. 桌面透明小窗（桌宠）：主窗口最小化时，达妮娅以半透明圆角小窗浮在桌面，含气泡 + 拖拽条 + 迷你控件。

---

## 四、GSAP 用法约定（克制）

1. 仅用途：入场错峰上浮、卡片 `back.out` 弹性进入、消息气泡轻入、发送键涟漪、双工四态声波、主题切换、Live2D 舞台 `back.out` 放大入场。
2. 曲线：`power2.out` / `back.out(1.4~1.6)`；时长 ≤ 0.6s，stagger ≤ 0.1s。
3. 禁止：无限呼吸、霓虹扫描、视差炫技。
4. 降级：`prefers-reduced-motion` 全部关闭。
5. 接入：原型用 CDN；**正式接入 pywebview 改为本地 vendored `gsap.min.js`**（离线可用）。

---

## 五、原型清单（均已预览）

| 文件 | 内容 |
|---|---|
| `design_tokens.css` | 二次元统一令牌（浅/深 + 柔光 + 降级） |
| `ui_redesign_main.html` | 聊天主页：三栏（侧栏 + 对话 + Live2D 舞台） |
| `ui_redesign_characters.html` | 角色：达妮娅（Live2D 已接入）+ 发展中占位卡 |
| `ui_redesign_daily.html` | 日报周报：日报/周报切换 + 真实内容 + 导出 |
| `ui_redesign_developer.html` | 开发者收口：屏幕活动流/记忆库(只读)/模型/游戏/Unity/课程 |

---

## 六、接入说明

- **pywebview（Edge Chromium）**：加载上述 HTML + CSS；GSAP 本地 vendored。无框架、无构建。
- **Live2D**：聊天页舞台与桌宠小窗均只提供渲染容器（canvas/iframe），rig 由既有引擎注入，UI 不重做。
- **游戏内气泡（tkinter）**：无真毛玻璃，按本规范给色值（surface/line/accent）做近似。
- **config 映射（v4.1 已按代码核对修正，勿再用 v4 旧值）**：
  - **视觉总开关 → `vision_models.enabled`**（真总闸；`webapp.set_vision_enabled` 写的就是它；
    `VisionRouter._perception_enabled()` 读它）。⚠ v4 写的 `perception.vision.enabled` **不是**主开关——
    它仍在生效但是**第二道闸**：`config.py` 里 `runtime_status.vision_enabled` =
    `perception.vision.enabled` **且** `vision_models.enabled` **且** `desktop_watch_enabled`。
    两道闸任一为关，视觉全链路静默失效 ⇒ 排查"开了看屏没反应"必须**两个都查**。
  - **文本模式 → 已删除**（09-28 移除重复双工开关时一并删掉；`runtime.text_mode_only` 键仍残留但无 UI）。
    v4 该条作废。双工开/关以 `voiceDuplexSwitch` 为准（关=`engine:'off'`）。
  - **桌面看屏互动 → `perception.vision.desktop_watch_enabled`**（键缺省时按运行方式推导：云端档默认关）。
  - **游戏时云端看屏 → `vision_models.game_cloud_watch`**（作用于独立 VLM 看屏管线，与语音解耦，默认关）。
  - **主动开口 → `proactive.duplex_local_omni_listen_prob_scale`**（0.2–1.0，默认 0.4）。
  - **电脑控制步数上限 → `agent.max_steps`**（1–20，聊天与 agent 共用；`require_confirm` 决定是否暴露危险工具）。

---

## 七、设置页规范（v4.1 新增 · 原规范未覆盖设置页）

> 现状（2026-09-29 实测）：设置页 **20 个分组 / 24 个 switch**，其中约 10 个分组属开发者层；
> `ui_redesign_developer.html` **是空壳（0 个 switch）** ⇒ 开发者内容全部漏进设置页。
> 这就是"开关平铺"与"开发者项混入"的共同根因。

### 7.1 设置页只保留 4 项（与 §三 一致）
**角色 / 语音·音色 / 视觉（含视觉总开关）/ 双工**。其余一律收口到开发者页（见 7.2）。

### 7.2 开发者页收口（必做）
`ui_redesign_developer.html` 应承担（规范 §三 已写明"开发者收口"）：
屏幕活动流 / 记忆库（只读·不可改）/ 模型设置 / 游戏校准 / Unity 视图 / 课程记录。

**当前应从设置页迁出**的分组：设备检测、显存预算、档位（与引导页一致）、聊天 API（打字用）、
我的模型配置（多厂商并存）、高级·手动填写、聊天模型（打字用）、记忆库、看屏引擎、双工引擎。
⇒ 做完这一步，设置页从 20 组降到 4 组，依赖链与引擎段合并等问题**会自行消解**（先减法，后加法）。

### 7.3 顶部「本地 / 云端」主切换的处置
**规范 §三 没有这一层**——方向由「双工」段表达。
⇒ 顶部 `scopeSeg` **移除，或降级为只读的方向指示**（显示由 `duplex_mode` 推导出的 local/cloud/off）。
**不要**把它做成真正的运行方式切换（那会重复「双工」段职责，且切换会卸载/加载模型）。
方向的唯一入口 = 「双工」段；档位卡保持与引导页一致。

### 7.4 开关状态样式约定（"开了却不生效"是一等公民）
每个开关必须能表达三态：

| 态 | 表现 |
|---|---|
| 生效中 | `.switch.on` |
| **已开但未生效** | 加 `.inert`（**opacity 置灰，但保留可点击**——用户点了才看得到原因），下方常驻原因行 |
| 关闭 | 常规 off |

- **原因行**：用 `--ink-3` + 复用 `.note` 类；**不要用 toast**（会消失，用户回头又不知道原因）。
- **生效方式徽标**：每个开关标 `data-apply="hot|restart"`，统一渲染（"热生效"/"需重启"）。
- **动效**：`power2.out` ≤ 0.6s；`prefers-reduced-motion` 全关（§四）。
- 判定统一由后端 `get_settings_health()` 提供 `{on, effective, blocked_by, reason}`，
  前端**不要**各自手抄判定规则（尤其视觉类必须同时查**两道总闸**，见 §六）。

### 7.5 新增开关的接线清单（缺一即"静默失效"）
1. config.yaml 加键 → 2. `config.py` 命名空间浮出（或走 `cfg.raw` 直读以保证热生效）→
3. webapp getter/setter（setter 写 `raw` 后 `_save_config()` + `cfg.ns = _to_ns(raw)`）→
4. `app_backend.js` 包装并**加入 return 导出对象**（漏导出 = `Backend.xxx` 为 undefined，点击静默无反应）→
5. 设置页 UI + `get_settings_health()` 纳入判定 + `data-apply` 徽标 →
6. **SPA 铁律**：需被 `window.xxx` 调用的函数必须 `window.xxx = xxx`（子页脚本经 `new Function` 执行）。
