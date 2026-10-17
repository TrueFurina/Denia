# proactive_pack · 主动互动包

> 「真人感主动搭话」的**独立实现包**：可单独开发、单独开关、单独回滚，
> 不与主软件其它分支抢文件。约定的做法与 `src/channels/`（社交通道）一致。
>
> 设计依据：`docs/真人感主动搭话_设计调研_20261011.md`、
> 落地方案：`docs/主动互动包_设计与重规划_20261012.md`

## 它解决什么

宿主的聊天页主动只给模型一句任务书（"用户 300 秒没操作…只输出一句轻声关心"），
于是她只会说"在忙吗 / 歇会儿吧"这种**对谁都能说的万能句**。

本包补上中间层：**把"用户的当下处境"翻译成一坨真实材料**，再要求模型说一句
**带具体锚点**的话。分七类触发器（何时说） × 一个情境事实包（说什么）。

七类触发器（优先级高 → 低）：

| # | 触发器 | 何时 | 例子 |
|---|---|---|---|
| 1 | `festival` | 当天首次进入 + 今年没用过 | 「中秋快乐」 |
| 2 | `pending` | 用户宣告要做的事超时未归 | 「我去打两把游戏」→ 1.5h 没动静 →「打完了？」（**只问一次**） |
| 3 | `followup` | 聊得正热（≥3 轮）突然静默 1–3h | 接着刚才话题补一句 |
| 4 | `recall` | 情绪重的记忆到了回访窗口 | 说过感冒 → 过一两天问「好点没」；**问过即闭，除非再说一次** |
| 5 | `rhythm` | 饭点未聊 / 连续熬夜 | 「吃了吗」 |
| 6 | `share` | 兴趣画像 × 真实素材 × 长静默（≥6h） | 「原神新版本前瞻定档了」（抖音式；**没素材/没兴趣绝不硬编**） |
| 7 | `silence` | 长时间没来（阶梯） | **允许自然地问「在忙什么」**（此档豁免万能句黑名单） |

状态类事件（感冒/面试/考试…）回访窗口收窄到 5 天——问晚了像没在听。

## 一行开关

```jsonc
// data/proactive_pack.json
{ "flags": { "enabled": true } }        // 默认 false —— 装上不打开 = 什么都不做
```

也可以写 `config.yaml`（可选，不用也行）：

```yaml
proactive:
  proactive_pack:
    enabled: true
    festival: true          # 节日 / 纪念日
    pending: true           # 宣告事件跟进（「去玩游戏」→「打完了？」）
    followup: true          # 会话内跟进
    recall: true            # 关系型召回
    rhythm: true            # 生活节律（饭点 / 熬夜）
    share: true             # 兴趣分享（需素材池 + 兴趣画像）
    silence: true           # 长期静默召回（允许问「在忙什么」）
    day_cap: 6              # 每日开口上限（独立记账，与宿主同语义）
    share_per_day: 1        # 兴趣分享每日上限（最"推内容"的一类，压最低）
```

优先级：内置默认 ← `config.yaml` ← `data/proactive_pack.json`（运行时热切用这个）。

## 接入（A 方案：零文件改动，推荐）

在软件启动、拿到 `ProactiveCompanion` 实例之后加两行：

```python
from src.proactive_pack import install
install(proactive)          # 幂等；总开关为关时行为与装之前完全一致
```

本包在**实例上**包装 `check_and_trigger`：包装层先问一句"本包要不要开口"，
是则让位，否则**原方法照常执行**。任何异常都被吞掉并放行原逻辑。

想热回滚：`from src.proactive_pack import uninstall; uninstall(proactive)`。

## 接入（B 方案：显式改 proactive.py）

若不想用包装，可在 `check_and_trigger` 的**关系闸门之前**插一个显式钩子：

```python
# === proactive_pack 钩子（可用 data/proactive_pack.json 的 enabled 整体关闭）===
_pk = getattr(self, '_proactive_pack', None)
if _pk is not None and _pk.enabled:
    try:
        if await _pk.maybe_emit():
            return
    except Exception:
        logger.debug('proactive_pack 钩子异常（放行原逻辑）', exc_info=True)
# === end ===
```

## 文件职责

| 文件 | 职责 |
|---|---|
| `__init__.py` | `build_proactive_pack()` / `install()` / `uninstall()` |
| `config.py` | 三级开关叠加、状态文件读写（**只读 config.yaml，绝不回写**） |
| `marks.py` | 去重标记 / 全局日账本 / 回访标记 / 宣告与分享标记（`data/proactive_pack.json`） |
| `festivals.py` | 公历固定日 + 农历年表（2024–2031）+ 关系纪念日 |
| `situation.py` | **情境事实包**装配器（12 块：now/gap/habit/**longterm**/pending/memory/interest/topic/**usage**/**screen**/**dayreview**/relation）；读数一律按角色隔离。`usage`/`dayreview`/`screen` 只读 `data/activity/`（今日电脑使用实况 + 昨日日报小结 + 她看屏的第一手观察/游戏战况，后者兼作兴趣信号），`usage_context`/`screen_context` 开关分别控制。⚠️ `order` 列表必须与装配的块一致（longterm 曾被漏接、文本里见不到——已有回归守卫断言） |
| `triggers.py` | 七类触发器的"何时说"判定（纯代码，无台词） |
| `feed.py` | 兴趣分享素材池（`data/proactive_pack_feed.json`；缺文件=静默降级） |
| `prompts.py` | 生成端提示词（禁万能句 + 反 PUA + 必须带锚点 + 身份铁律 + 引用铁律） |
| `judge.py` | **审查端**（第二个 LLM）：判"这句话此刻能不能发"，给出类别/原因/改写方向 |
| `pack.py` | 门面 `maybe_emit()`：闸门 → **生成 → 代码闸 → LLM 审查 → 回灌重写** → 派发 → 记账 |
| `selftest.py` | 自测（全桩，不依赖主软件） |

## 生成 / 审查双 LLM（2026-10-12 起）

问题：只靠堆生成端提示词（黑名单 + 反 PUA + 引用铁律 + 身份铁律 + 一次只说一件事…）
会让提示词越来越长、规则互相打架、模型顾此失彼 —— **物极必反**。

改法：把"想怎么说"和"能不能说"拆成**两条流水线**：

```
触发 → 生成器 LLM（只管想一句像人话）
        ↓
      代码闸（零成本，只抓形状：黑名单 / 自我编造 / 自称名字 / 无材料编造 / 重复）
        ↓ 通过
      审查器 LLM（看得见同一份真实材料，按 10 条判据判 PASS/FAIL + 类别 + 原因 + 改写方向）
        ↓ FAIL
      把「驳回原因 + 改写方向 + 硬性修正」**回灌**给生成器重写 → 回到顶部
        ↓ PASS（或达到 judge_attempts 上限 → 本轮放弃，下次还有机会）
      派发
```

- 判据（审查器 10 条）：`fabrication` 编造 / `self_claim` 自曝身体 / `pua` 施压 /
  `bland` 万能句 / `multitopic` 两件事 / `off_topic` 答非所问 / `robotic` 客服腔 /
  `emoji` 表情或动作描写 / `repeat` 重复 / **`awkward` 生硬别扭**（硬凑两件不相干的事、绕口）。
  > `robotic` 覆盖三态：① 书面 / 翻译 / 新闻腔；② 说教腔（「要注意身体」「要劳逸结合」）；
  > ③ **把自己的名字当旁白第三人称念出来**（「达妮娅记得你…」）——2026-10-12 全量实测真机踩到过。
  > 生成端 `prompts.self_name_rule()` 同口径先禁（有材料 / 无材料 / 节日**三条分支都带**），R7 兜底；
  > 与 R10 同理，属"软形态"，故也给 R7 配了**命中范例**（范例 3）。
  > R1「编造」的分寸（实测踩过误拒后才写准）：**断言**材料里**完全没有**的人事物才算编造；
  > 顺着材料里**已写过**的事追问细节（「那牛排后来怎么样」「小程序还顺吗」）是**自然回指，不算**。
  > 漏看这条，审查器会把正当追问一起毙掉 → 连续驳回 → 用尽次数不说话。
  > **空材料**时，一句只基于当前时间 / 作息的轻度关心（「这个点还没吃晚饭吧」）是**合格**的：
  > 没回指往事不算 R1，带了时间锚点也不算 R4。漏看这条，审查器会把正常关心判成万能句 →
  > 她该关心却干脆不出声（实测三模型通病）。
  > R10 只管**明显**别扭；平实、稍普通**不算**，否则又会过度驳回。
  > ⚠️ 解析器正则要认**两位序号** `R10`（`R(\d)` 会把「R10 命中」误读成 R1 → 类别错判）。
  >
  > **软判据（自然度）光写判据行会失效**（2026-10-12 真机实测）：模型对"生硬别扭"这类主观条目
  > 有强烈的"判未命中"偏置——3 条明显别扭的话（硬凑「煎牛排↔猫」、说教腔）全被放行。
  > 补法是给**命中的范例**（`R7 说教腔: 命中` + `R10 生硬别扭: 命中`），并把说教腔从 R10
  > **归并到 R7**（两处重叠 → 模型两处都不判）。纯加判据行是没用的。
- 节日场景**额外**加一条 `R11 off_festival`（祝福必须落到**当天这个节日**）：
  生成端的节日提示词只写了"祝福落到节日本身"，弱模型仍会把国庆说成中秋（实测：国庆开口就是
  「月圆时分，记得赏月」）——所以审查端加一条硬判据 + 生成端补"今天哪个节日就只说哪个"。
- **职责分工：硬形状 → 代码闸；语义 → LLM 审查**（2026-10-12 定向实测的教训）。
  「第三人称自指」这条真机 bug：LLM 审查只认得**字面形**（「达妮娅记得你…」），
  而同义变体（「达妮娅觉得…」「达妮娅跟你说…」）**时判时不判** → 于是把它**降级成代码闸**
  `pack._is_self_name`（她自己开口时，**剥掉引号内内容后**仍出现她自己的名字即拒，类别 `robotic`）。
  定向验证（`tools/judge_selfname_probe.py`）：**代码闸 5/5 全中、LLM 层漏 2/3**。
  > 经验：**别拿"该由代码判"的形状问题去为难 LLM**——又慢又不准；反之亦然。
  > 报告 `docs/proactive_pack_selfname_report.md`。
- 开关：`llm_gate`（默认开）控制审查器；`judge_attempts`（默认 3）控制最大重写轮数。
  关掉 `llm_gate` = 只留代码闸（离线 / 省钱降级形态）。
- **审查器要换更强的模型**（实测结论，别省这一步）：`judge_provider` 填 config.yaml 里
  `text_chat.providers` 的某个 id（默认 `zai-glm`）；`judge_model` 非空时**覆盖**该端口自带模型，
  留空则用端口自带模型。默认组合 = `zai-glm` + `glm-4.5-flash`（覆盖端口自带的 `glm-4-flash`）。
  也可代码注入：`install(host, judge_llm=...)`。
  > ⚠️ 换 `judge_provider` 到别的厂商时，记得同步改 `judge_model`——否则会拿 glm-4.5-flash 去
  > 调别的厂商端点（对方不认识这个模型名 → 报错 → fail-open 放行）。
  > 为什么必须换强模型：同一套 10 条判据，**glm-4-flash 只有 3/10**——编造（"你上周去爬山拍的照片真好看"）、
  > 自曝身体（"我昨天晚上煮了碗面"）、万能句（"在忙吗"）它全放行；加了"逐条核查 + 范例"仍 3/10。
  > 弱模型做不了"拿材料逐条比对"这件事。对比数据见 `docs/proactive_pack_judge_model_probe.md`。
- ⚠️ **审查模型若是推理模型**（glm-4.5 系等）必须设 `judge_thinking: false`：否则思维链会吃光
  `max_tokens`、正文返回空 → 被当成"审查器不可用"→ fail-open 全部放行，闸门形同虚设（实测踩过）。
- **fail-open 但非静默**：审查器超时 / 异常 / 输出不可解析 → 放行，但会打 `warning` 并计入
  `status()["judge_unavailable"]`；连续出现即说明闸门没在工作，按 warning 里的提示换模型 / 关思考。

### 接入宿主模型引擎（生产环境：同模型走引擎既发言又审查）

开发期默认 `judge_provider="zai-glm"`（审查走独立强免费模型）。**生产环境不需要任何外部/免费模型**：
把开关 `judge_use_engine` 设为 `true` 即可 —— 生成 + 审查都走宿主模型引擎的 llm/vlm 档，同一模型、无限流。

- **引擎 llm 档不是 OpenAI 端点**（实测核对 Comni 源码 `gateway.py`）：网关暴露的是
  `POST {endpoint}/api/chat`（另有 `/ws/chat`、`/ws/duplex/...`）。请求体是 Comni 原生
  `ChatRequest`：`{"messages":[{"role","content"}], "generation":{"max_new_tokens":N,"temperature":T},
  "enable_thinking":false}`；响应 `ChatResponse`：`{"text": "...", "success": true, "error": null}`。
  本包已内置 `engine_llm.EngineChatClient` 按此 schema 封装（stdlib urllib；绕过网关自签/过期证书、
  屏蔽系统代理），**宿主无需改代码**。
- **端点自动解析**（`judge_use_engine=true` 时）：`host.engine_llm`（客户端对象，或**懒求值函数**）
  → `host.engine_llm_factory()` → 从宿主引擎对象自动发现
  （`host._local_omni_engine` / `host._engine` 的 `.endpoint`，或 `host._duplex_local_omni_endpoint`）。
  也可用配置键 `engine_endpoint` 显式指定。**解析不到 → 回落宿主聊天路由，绝不静默改行为**。
- 解析顺序（审查器）：`judge_llm` 注入 → `judge_use_engine`（引擎档）→ `judge_provider` 端口 → 生成端。
  生成端（`_llm_client`）：注入 →（开关开）引擎档 → `host._llm`。**缺省关时生成不切引擎**（回归保护）。
- **不干涉用户聊天**：主动包每次调用都是**自带 messages 列表的 stateless `chat()`**，走独立的
  `_schedule_duplex_emit` 发射路径；不读/不改用户的双工或聊天会话上下文，因此不会污染用户被动回消息。
  `status()` 如实上报 `engine_llm_connected` / `engine_llm_endpoint` / `judge_use_engine`。
- 代码实测：`selftest.py` 的 `t_engine_adapter`（[15]）验证"引擎档既发言又审查 + 用户聊天路由零调用 +
  引擎自动发现 + 缺省关不切引擎"；`t_engine_client`（[16]）验证 `/api/chat` 协议与失败边界。
- ⚠️ 引擎的 turn-based 档需在 Comni 侧启用（`app_registry.is_enabled("turnbased")`），否则
  `/api/chat` 返回 403；失败会在日志里如实报出（不静默吃空）。
- 兼容旧口径：判定词同时接受 `PASS/SEND` 与 `FAIL/SKIP`。
- 排障：`pack.status()["last_rejects"]` 能看到本轮每次被驳回的「来源/类别/原因/原句」。
- 真机证据：`tools/judge_real_test.py`（**生成端 glm-4-flash / 审查端 glm-4.5-flash 关思考**，
  跑 A 判别力 / B 闭环 / C 打回重写）；报告 `docs/proactive_pack_judge_report.md`。
- **更宽覆盖的真机验证**：`tools/judge_real_test_broad.py` —— 合成多角色 +
  长期历史 + 情绪重事件 + 兴趣素材等丰富虚拟上下文，覆盖 9 类情境（久别/会话内跟进/
  关系召回/饭点/熬夜/宣告事件/兴趣分享/长期回指/无材料）+ 角色隔离实证；报告
  `docs/proactive_pack_broad_report.md`。
- **全量主动发话验证**：`tools/judge_all_flows_test.py` —— 让**真实 `triggers.evaluate` 自己判**
  「此刻该不该说、属于哪一类」，再走**完整 `maybe_emit`**（collect→evaluate→闸门→生成→代码闸→
  LLM 审查→回灌→派发），覆盖全部 7 类时机共 8 条发话路径 + **2 条空材料路径**（只有时间/作息、
  无往事可引用）+ 9 条闸门负例 + 用户视角打分；报告 `docs/proactive_pack_all_flows_report.md`。

### 双强模型实测（开发者 2026-10-12 追问：两个都换聪明模型会怎样？）

结论：**「更强」≠越新越好；且强模型当生成器必须先关思考，否则把内心独白念出来。**

- 模型可用性（智谱免费 key 真机探测）：`glm-4-flash`（弱）、`glm-4.5-flash`（强）可用；
  **`glm-4.6-flash` 实测 403 Forbidden——不存在/此 key 未授权**（别记成 4.6）；`glm-4.7-flash` 可用（更强）。
- 审查器：**`glm-4.5-flash` 关思考 = 8~10/10 最佳；`glm-4.7-flash` 关思考只有 4/10**，且免费 key 上频繁
  429 限流——4.7 把「我去了那家咖啡店」「在忙吗」等全放行。**审查器别追新，守住 glm-4.5-flash。**
- 生成器（统一用强审查 glm-4.5-flash 关思考对比）：
  - ⚠️ **不关思考的强模型当生成器 = 灾难**：glm-4.5/4.7 是推理模型，会把思维链
    （「首先，用户要求我作为达妮娅，只输出一句话…」「1. 分析请求：角色：达…」）直接当台词念出来，
    用户听到的是模型的内心独白。
  - ✅ **注意：这其实是自动满足的，不用额外加护栏。** 生成端走项目 `TextChatRouter.chat()`，
    该路由对纯聊天（非工具调用）默认 `deep=False` → 推理模型自动关思考、非推理模型不塞任何思考字段
    （见 `src/llm/openai_client.py` 的 `_thinking_body`）——所以只要生成调用不显式传 `enable_thinking`，
    无论换什么聊天模型都是纯 chat。**不要**为对称去生成端硬塞 `enable_thinking=False`：显式覆盖路径会绕过
    `_is_reasoning_model` 守卫，给 glm-4-flash 这类非推理模型也发 `thinking.type:disabled`，有 400 风险。
    审查端则不同——它走独立的 `judge.MiniOpenAIClient`，必须显式 `enable_thinking=False`（`judge_thinking` 默认 false）。
  - 关思考后，强生成器（4.5/4.7）产出的话与弱生成 glm-4-flash 一样干净自然，且略微更丰富温暖
    （如「小程序上线前要不要我帮你查点资料？」「牛排煎得不错，今晚早点休息呀～」）；用户视角评分
    （自然/人设/互动欲/信息量，各 1-5）三家都落在 4~5 档，**强生成并没有明显碾压弱生成**。
  - 当前默认（**弱生成 glm-4-flash + 强审查 glm-4.5-flash**）在免费 Zhipu 模型里已接近最优：
    生成端够说人话、快、稳、不被限流；安全闸门交给强模型。
- 副作用观察（与强弱无关，三模型一致）：**无旧材料的 rhythm 场景容易用尽 3 次驳回后闭嘴**，
  是 no-material 分支过严 / 生成器无锚点时的通病，后续可单独调优。
- 真机报告：`docs/proactive_pack_two_strong_probe.md`（模型可用性 + 审查判别力 + 生成质量 + 用户评分）、
  `docs/proactive_pack_gen_thinkoff.md`（强生成器关思考公平对照）。

## 长期上下文与角色隔离（2026-10-12）

- **长期**：情境包新增 `longterm` 块 —— 总轮次 + 最早一次多久前 + **均匀取样的"更早之前聊过的事"**。
  数据三级回退：`data/context/<角色>.jsonl` → `data/history/<角色>.jsonl` → 内存桶。
  兴趣画像也吃长期历史，不再只看"最近几轮"。
- **隔离**：记忆库读数一律走 `situation._scoped_memories()`（按 `char_id` 过滤
  「本角色 + 通用」），**不信全局 `_character`**——主动互动是后台触发，全局作用域可能是空串
  或另一个角色，直接调用 `recent_episodes/list_user_facts` 会**串味**（A 的往事拿去给 B 开口）。
  对话历史走 `shared_history_for` / 分角色落盘文件；承诺读取也按角色过滤。

## 兴趣分享素材池（share 触发器的数据口）

`data/proactive_pack_feed.json`（没有这个文件 = share 永不触发，其余六类照常）：

```json
[
  { "id": "genshin-52", "text": "原神 5.2 版本前瞻直播定档周五晚",
    "tags": ["原神", "游戏"], "ts": 1760000000.0 }
]
```

- `text` 必须是**真实发生的事**（版本公告 / 新闻 / 赛事 / 新片上映），由外部投喂
  （后续可接搜索桥 / RSS / 官方公告），本包只读取、匹配、包装。
- 匹配：素材 tags/正文 命中用户兴趣画像（从记忆事实 + 最近原话抽取，如「在玩原神」）。
- 安全阀：每日 1 条（`share_per_day`）、每条素材只分享一次、静默 ≥6h 才用、
  **兴趣画像为空 = 永不分享**。

## 自测

```
cd G:\ai对话\11
python -m src.proactive_pack.selftest
# → 104 PASS / 0 FAIL
```

## 借鉴的开源实现（未整体引入任何依赖）

调研过 N.E.K.O、thunlp/ProactiveAgent（ICLR 2025）、leomariga/ProactiveAgent、
proactive-ai-companion、Amitia，结论：无可整体接入者，择其长处抄进包内——

| 来源 | 借了什么 | 落在哪 |
|---|---|---|
| thunlp/ProactiveAgent | **reward 思想**：发出前判一次"值不值得发"，错误方向宁可漏发不误发 | `judge.py` 审查器 LLM（`llm_gate` 开关，fail-open；见上文"生成/审查双 LLM"） |
| N.E.K.O | 拉热点/新闻喂话题的**素材源**做法 | `feed_sources.py` 热榜自动拉取（喂 `feed.py` 素材池的 hot 段） |
| leomariga/ProactiveAgent | 动态间隔/决策引擎的思路 | 暂未引入（其库要接管调度循环，与宿主 tick 架构冲突） |

## 素材池（share 触发器的数据口）

`data/proactive_pack_feed.json`，数组，每条：`{"id", "text", "tags", "ts", "src"}`。
- `src:""` = **手工投喂**（你或搜索桥塞进来的，刷新永不覆盖）。
- `src:"hot"` = **热榜自动拉取**（`feed_sources.py`，默认 4h 后台保鲜一次）。

### 热榜自动拉取（借鉴 N.E.K.O 的"拉热点喂话题"）

- 来源：Bilibili 热搜（游戏/ACG 浓度高）+ 微博热搜（vvhan 聚合口，兜底）。
- **只搬事实不编内容**：条目就是热榜原句；「怎么说」由提示词现场即兴。
- 内容过滤（宁严勿松）：涉政（合规硬红线）/ 灾难伤亡 / 疾病 / 明星八卦一律不进池。
- 时序：`maybe_emit` 里发现有「share 开 + 用户确有兴趣画像」→ 后台线程按 TTL 拉取，
  **不阻塞本轮**；本轮用现有素材，新素材下一轮生效。拉不到 → 旧素材原样保留。
- 手动刷新（真机排查）：`python -m src.proactive_pack.feed_sources --force`
- 开关：`feed_hot`（默认开）、`feed_ttl_h`（4h）、`feed_max_items`（60，手工条目优先保留）。

注意：热榜条目要命中**用户兴趣画像**才会被分享（`feed.matches` 双向包含判定），
所以大多数泛热点不会开口——只有用户亲口提过的关键词出现在热榜上时，她才会"刷到了想给你看"。

1. **不编造**：所有锚点都来自情境包里的真实数据；没有就说少一点，绝不虚构她的离线生活。
2. **反 PUA**：不施压 / 不卖惨 / 不制造愧疚 / 不催回复 / 不问"你去哪了"。
3. **只新增**：不改引擎底座与模型调用层；`config.yaml` 零副作用；缺省即关。

## 软件内接入（设置页 · 开关面板）

- **面板位置**：设置页新增「主动互动」分类（`data-to="proactive"`），与「社交渠道」并列在子导航末尾。
  - 总开关 `启用主动互动` + 七类时机（回访 / 宣告跟进 / 会话内跟进 / 生活节律 / 久别 / 节日 / 兴趣分享）+ 两道安全阀（**LLM 审查闸** / 热榜刷新）。
  - **热生效**：保存即写 `data/proactive_pack.json`，并 `reload_flags()` 立即生效，**无需重启**（与社交渠道"需重启"不同）。
  - **验证区**：实时显示「是否已静默达门槛 / 还差多久 / 当日已开口次数 / 上次主动内容 / 状态文件路径」；「立即诊断」用此刻真实状态推演"若达标她会发起哪一类"（只读，不发声、不写账本）；「打开状态文件」在资源管理器选中 `proactive_pack.json`。
- **后端桥**（webapp.py，新增三个 BK 方法，不碰既有 95 个）：
  - `get_proactive_pack_flags` → 状态 + `live_state()` + `status()`。
  - `set_proactive_pack_flags(flags)` → 写状态文件 + 热 reload。
  - `test_proactive_pack` → 只读诊断（`dry_run` + `live_state`）。
  - `open_data_file(filename)` → 打开 data 目录并选中文件（供「打开状态文件」按钮）。
- **启动接入**：`webapp.set_runtime` 末尾懒导入并 `install(self._proactive)`；`install` 幂等，
  且 `enabled=false`（默认）时包装层对宿主**零侵入**——宿主原 `check_and_trigger` 照常执行。
- **状态文件**：`data/proactive_pack.json` 的 `flags` 段（三级叠加：DEFAULTS ← config.yaml ← 本文件；本文件最高优先级）。
4. **降级不静默**：任一块取不到数据 → 该块留空 + 记日志，绝不让整条主动挂掉。
5. **总量不变**：日上限仍 6 次，改的是**质量与时机**，不是数量。

## 包外仍需的一件事（1 行，可选）

真机上"聊天页主动的 TTS 出声"被宿主 `proactive.py:3770` 的
`payload['desktop_companion'] = not self._in_call()` 打上了"桌面陪伴"标记，
因而被"桌面陪伴语音"开关误管（默认关）。按已拍板的**方案甲**改成：

```python
# proactive.py  _flush_duplex_emit 内
_payload_to_chat = bool(payload.get('to_chat'))
payload['desktop_companion'] = (not self._in_call()) and (not _payload_to_chat)
```

（属宿主文件，故未纳入本包；不改也不影响打字上屏，只是她"只飘字不出声"。）
