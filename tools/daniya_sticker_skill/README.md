# 达妮娅 · 表情包技能包（独立测试版）

为桌面 AI 伴侣「达妮娅」设计的**表情包（sticker）管理系统**，当前为**独立可测试**版本，
不接入主程序。设计借鉴 astrbot 的 `meme_manager` / `stealer` 思路（数据模型 / 交互 / 图床抽象 /
LLM 语义匹配 / 自动收集），但为「单机桌面伴侣 + 多角色绑定 + 真人感选贴」场景**自研**。

> 状态（2026-10-15）：独立技能包功能已基本做完，含两套真实测试库（表情包测试库 24 张 GIF、
> 丛雨表情包 40 张）。接入达妮娅主程序与真实图床适配器为后续步骤（见文末）。

## 已实现功能

- 📁 **文件夹式表情包管理**：新建 / 重命名 / 删除，左键进入、拖拽上传、批量整理
- 🖱️ **右键菜单**：打开 / 重命名 / 删除 / 绑定角色
- 🔒 **角色绑定（白名单 / 黑名单）**：每个文件夹可绑定到指定角色
  - `none`：所有角色可用（默认）  ·  `whitelist`：仅列表内角色可用  ·  `blacklist`：列表内角色不可用
- 🔍 **语义搜索**：按文字描述检索（如「开心猫」「安慰」），有 key 时走 LLM 重排，无 key 时本地关键词粗筛
- 🎯 **真人感选贴**：聊天时按「当前角色 + 上下文 + 角色心情」自动挑合适表情（LLM 驱动）
- ✨ **上传自动描述**：用**视觉模型**给新表情自动打标（多帧 GIF 抽帧拼接，见下）
- ✍️ **手改标签 + provenance**：情绪词点选、适用场景、反触发、分类；手改后标 `manual`，自动标注不再覆盖
- 📥 **自动收集（astrbot auto_collect 思路）**：用户在聊天里发来的图，经「重复采样闸门→去重→待审队列」收进库，人工确认分类后入包
- 🛒 **资源广场**：复用 astrbot 社区索引（community-index.json），一键安装云端免费表情包（jsDelivr 镜像 + 原站回退）
- ☁️ **图床同步**：测试期仅本地；`marketplace.py` 已按 astrbot 协议预留下载/安装链路，真实图床适配器（Lsky Pro / R2 / WebDAV）待接入

## 关键技术点（踩过的坑，已修）

1. **智谱 GLM-4V-Flash 对图片格式极挑**：原图（调色板模式 P / 特殊编码 JPG）直接发会 400。
   解决：送图前 `convert("RGB")` 重编码成标准 JPEG，并把最长边缩到 ≤512px。
2. **测试库 24/24 张其实是 GIF 却叫 .jpg**：按文件名存 → 扩展名错 → 前端 Content-Type 错 → **动图不播**。
   解决：`store.sniff_ext()` 按文件内容魔数判断真实扩展名。
3. **GIF 只取首帧会漏掉关键情绪**：19/24 张首末帧差异显著（首帧「面无表情」、末帧「笑着冒爱心」）。
   解决：`frames.py` 按动作差异抽多帧横向拼接，一次请求让模型看全过程（`config.AUTOTAG_FRAMES=motion`）。
4. **选贴偏见（用户笑却选悲伤图）**：历史累积让模型锚定旧情绪。
   解决：`pick_sticker` 只看最近 6 条、显式标「最新一条用户消息」、并以**达妮娅关系状态机的心情**为最强语义线索
   （详见 `mood_matrix_report.json`：带心情 6.0/6 vs 基线 5.5/6）。
5. **模型 JSON 输出不稳定**（键无引号、裸中文值）→ 静默「永远不发表情」。
   解决：`_extract_json` 多策略容错（围栏剥离 / 宽松正则 / 裸 JSON 加引号修复）。

## 实验与验证（已落地，报告在数据盘根目录）

- `vlm_compare_report.json` / `vlm_eval_report.json`：不同视觉模型打标准确度对比（结论是免费智谱够用，强模型更准）
- `mood_matrix_report.json`：选贴是否带心情的对照实验
- `caption_matrix_report_*.json`：画面描述质量对比
- `chat_report_BEFORE_fix.*` / `chat_report_AFTER_fix_run1.md` / `chat_report_WITH_mood.md`：选贴质量修复前后对照
- `chat_report.md`：选贴质量验证结论（含修复前偏见示例）

## 运行

```bash
# 1. 准备 venv（已装好依赖则跳过）
python -m venv venv && venv/Scripts/pip install -r requirements.txt

# 2. （可选）配置智谱免费端口以启用 LLM 能力
export ZHIPU_API_KEY=你的key        # PowerShell: $env:ZHIPU_API_KEY="你的key"

# 3. 启动
python run.py
# 打开 http://127.0.0.1:8787
```

## 目录结构

```
daniya_sticker_skill/
  backend/
    config.py        路径 / LLM / 角色 / 索引 / 抽帧 配置（接入时改这里）
    models.py        API 模型
    store.py         存储层（registry + 包/表情数据模型，含魔数嗅探扩展名）
    llm.py           LLM/VLM 抽象层（ZhipuBackend + DaniyaLLMBackend 桩）
    frames.py        GIF 多帧抽帧拼接（供视觉模型打标）
    autotag.py       自动标注 + 情绪词表 + 手改/provenance
    collect.py       自动收集：采样闸门 / 去重 / 待审队列 / 7天过期
    marketplace.py   资源广场（astrbot 社区索引拉取/安装）
    main.py          FastAPI 后端 + 静态托管 SPA
  frontend/
    index.html / styles.css / app.js   单页管理界面
  data/              运行时数据（自动生成；含两套测试库）
  run.py / requirements.txt
```

## 数据模型

- `data/registry.json`：全局包索引（含绑定信息，供聊天选包快速枚举）
- `data/packs/<id>/pack.json`：包元信息（含 `bindings`、`categories`）
- `data/packs/<id>/meta.json`：`{meme_id: {file, category, description, tags, trigger, anti_trigger, mood_compound, visible_text, provenance, auto, style_variants}}`
- `data/packs/<id>/images/`：图片文件（真实扩展名按内容嗅探）
- `data/collect.json`：自动收集的去重集合 + 待审队列

## 接入达妮娅（后续，待指令）

1. **引擎替换**：`backend/main.py` 把 `llm_backend = llm.ZhipuBackend()` 换成
   `llm.DaniyaLLMBackend(engine)`，并在 `DaniyaLLMBackend` 三方法内接通「用户设置的主 LLM/VLM」
   （`_relation_block` 已直连 `relationship_v2`，与聊天语气同源）。调用面不变量。
2. **桥接**：通过 pywebview 桥或子服务接入；聊天时在 LLM 输出后插入「选贴」步骤（参考 `/api/pick`）。
3. **角色与开关**：`config.KNOWN_CHARACTERS` 改为读达妮娅角色名册；提供 on/off 开关。
4. **自动收集打通**：把聊天里「用户发来的图片」接入 `/api/collect/observe`（采样闸门在后端已就绪）。
5. **图床**：实现 Lsky Pro / R2 / WebDAV 适配器（marketplace.py 已预留）。

> 注：当前为独立测试版，所有 LLM 调用走智谱免费端口；无 key 时自动降级为本地管理/搜索模式。
