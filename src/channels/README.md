# 社交通道（G 脚手架）

把外部 IM（QQ / 微信 / QQ空间）接入**同一个 AI 大脑**（`Dialogue`），让达妮娅在桌面之外的渠道也能聊、能记、有性格。

> 状态：脚手架已完成三个通道（QQ / 微信 / QQ空间）、默认全部关闭、单测通过（见 `tests/`）。**尚未接真实联调**——需要你自备对应小号 + 网关运行环境后，按下面步骤开启。架构路线＝保留自研大脑、借外部桥接（即"一个大脑，多副身体"，与 VR 同构）。
>
> **软件内开关已落地**：设置页新增「社交渠道」子栏，可勾选启用 QQ / 微信 / QQ空间 并填写连接字段（token/端口/地址/白黑名单/cookie），点"保存"即写回 `config.yaml`。⚠️ 开关只控制"连/断通道"，外部桥（NapCat/Gewechat/Docker/cookie）仍需你单独部署；改动**重启软件生效**（通道在启动时构建）。详见下方「软件内开关」。

## 核心设计：大脑与通道解耦

```
QQ 消息 ──(OneBot v11)──▶ OneBotChannel ──▶ dialogue.respond(text, source="qq",
                                                          channel="qq",
                                                          reply_target={peer})
                                                        │
                                          AI_DECISION{channel:"qq", reply_target, text}
                                                        │
                              ┌─────────────────────────┴───────────────────────┐
                      OneBotChannel._on_decision                         ws_server（桌面）
                      → 发回 QQ 对应 peer                                  → 仅播放 channel 为空
                                                                            的桌面决策（不串台）
```

- 大脑 `respond()` **不直接调 TTS**，只发 `AI_DECISION` 事件；谁订阅谁播放/发送。
- 每条外部消息都带 `channel="qq"` 与 `reply_target`（私聊=user_id / 群=group_id），回发时按 `reply_target` 精准路由，并发消息互不串。
- 桌面（Unity/WebView）只播 `channel` 为空（桌面默认）的决策；QQ 回复不会在桌面被念出来。
- 主动搭话（E 步）**不接任何双工/外部通道**，只在桌面生效——QQ 侧是纯"用户发才回"的反应式。

## 陪你看视频（链接 → 取原视频 → AI 观看 → 讨论）

让达妮娅能"陪你刷视频"：**转发一个 B站 / 抖音 / YouTube / 西瓜 / 快手 / 小红书 等视频链接**，她会取原视频、抽关键帧、用现有 VLM 看画面，再结合标题/简介/作者与你讨论——就像你俩一起看。

- 实现：`src/channels/video_watch.py`
  - `is_video_url()` 命中已知视频平台 host 才走观看，普通网页仍走原"抓取摘要"。
  - `watch_video()`：yt-dlp 取原视频 + 元数据（B站 1080P30 无需登录；抖音/通用在支持列表）→ ffmpeg 均匀抽 4 帧 → 现有 `llm.vision_describe` 看每帧 → 拼成"视频理解"上下文喂给大脑。
  - 优雅降级：yt-dlp / ffmpeg 缺失或下载失败时，`watch_video` 返回 `None`，`media_ingest` 自动退回"网页摘要"，**绝不因缺依赖而崩**。
- 触发点：`media_ingest.ingest_media(..., watch_videos=True)`（QQ / 微信 通道收到消息时调用）。仅当链接命中视频平台且 `watch_videos` 开启才触发，普通链接不受影响。
- 依赖（需在**运行主程序的那个 Python 环境**里装，不只是测试环境）：
  - `pip install yt-dlp`（取原视频，182k★ 标准工具，支持 1000+ 站点）
  - `pip install imageio-ffmpeg`（自带 ffmpeg 二进制，免去系统装 ffmpeg，用于抽帧）
  - 抖音若 yt-dlp 取不到，可后续接 `Douyin_TikTok_Download_API`（Evil0ctal，无水印）当备选后端。
- 软件内开关：设置页「社交渠道 → 媒体摄入 → 陪你看视频」勾选（写 `config.channels.*.media.watch_videos`）。

> ⚠️ 时长护栏：`download_video` 对超过 `max_duration`（默认 1800s=30 分钟）的视频直接跳过下载，避免卡死/大文件。短视频（抖音/B站切片）是主要场景。

## 软件内开关（设置页）

不想手改 `config.yaml`？已在 `src/ui/web/redesign/ui_redesign_settings.html` 加了「社交渠道」子栏：

1. 打开设置 → 右侧子栏点「社交渠道」。
2. 三个分组（QQ / 微信 / QQ空间）各有"启用"开关 + 连接字段；最下方"媒体摄入"两组开关（图片/链接吸入记忆）。
3. 填好字段后点「保存（重启软件后生效）」——走 `CharacterAPI.save_social_channels` 写回 `config.yaml` 的 `channels.*`。
4. **重启主程序**，通道在启动时按 `enabled` 构建。

> 注意：UI 只是把配置落到文件，真正的"直连 QQ/微信"仍依赖你在别处跑的 NapCat / Gewechat(Docker) / 浏览器 cookie。UI 不能替你免桥。

## 接入 QQ（NapCat + OneBot v11）

1. 准备一个 QQ 小号，在手机/电脑登录。
2. 部署 NapCat（OneBot v11 实现）：https://napcat.uk 或用其 Docker / Windows 客户端。
3. 在 NapCat 里配置 OneBot v11 正向 WebSocket（默认 `ws://127.0.0.1:3001`）或反向 WebSocket（让 NapCat 连回本程序 `ws://你的IP:3002`）。
4. 编辑 `config.yaml` 打开通道：

```yaml
channels:
  qq:
    enabled: true
    onebot:
      mode: forward          # forward=本程序连 NapCat；reverse=本程序开服务端等 NapCat 连入
      ws_url: "ws://127.0.0.1:3001"
      # reverse 模式才用：
      listen_host: "0.0.0.0"
      listen_port: 3002
      access_token: ""      # 与 NapCat 配置一致（若有）
      http_url: "http://127.0.0.1:3000"   # 发消息的 HTTP API 兜底
      reply_in_group: true  # 群消息仅回复 @ 本机器人的消息
      allow_users: []       # 私聊白名单，空=所有人
      block_users: []       # 私聊黑名单，命中直接忽略
      allow_groups: []
      media:
        ingest_images: true # 图片→VLM 描述→作为上下文喂给大脑（需 llm 支持视觉）
        ingest_urls: true   # 链接→抓取摘要→作为上下文
        watch_videos: true  # 视频链接（B站/抖音/YouTube等）→取原视频→AI 看画面→讨论
        store_to_memory: true # 媒体内容写入长期记忆(world, source=qq_media)
```

5. 重启主程序。日志里应出现：`OneBot 通道已启动（mode=forward）` 与 `OneBot(forward) 已连接`。
6. 用 QQ 小号给机器人发消息，机器人会走同一个大脑回复。

## 接入微信（Gewechat 网关）

微信个人号**没有官方 API**。这里用 [Gewechat](https://github.com/gewechat/gewechat)（开源 OneBot 风格的微信网关，Docker 部署，底层走 iPad 协议）——它是目前相对干净的方案（扫码登录、无需付费 puppet token，但仍是灰色逆向，有封号风险，**务必用微信小号、同省部署更稳**）。

1. 准备一个微信小号，登录并保持在线。
2. 部署 Gewechat（`docker pull gewechat/gewechat`，默认暴露 `http://127.0.0.1:2531`）。在 Gewechat 后台把"消息回调地址"填为本通道的回调地址（本通道起 HTTP 服务接收消息）：Gewechat 与本程序**同机**填 `http://127.0.0.1:9912`；Gewechat 在 **Docker 里**（Windows Docker Desktop 的容器访问不到宿主的回环地址）填 `http://<本机IP>:9912/?token=<callback_token>`，并在配置里把 `callback_host` 改成 `0.0.0.0`。
3. 编辑 `config.yaml`：

```yaml
channels:
  wechat:
    enabled: true
    gewechat:
      base_url: "http://127.0.0.1:2531"
      token: ""              # 若 Gewechat 开启了 token 校验则填
      callback_host: "127.0.0.1"  # 默认只听回环；改 0.0.0.0 必须同时配 callback_token
      callback_port: 9912    # 与 Gewechat 后台填的回调端口一致
      callback_token: ""     # 回调共享密钥；非空时回调地址要带 ?token=<它>
      allow_users: []        # 私聊白名单，空=所有人
      block_users: []
      media:
        ingest_images: true
        ingest_urls: true
        watch_videos: true  # 视频链接→取原视频→AI 看画面
        store_to_memory: true
```

> **回调为什么必须鉴权**：Gewechat 的回调是**无签名**的裸 JSON POST。绑 `0.0.0.0` 又没令牌，
> 等于把 AI 的"耳朵"接到局域网上——同一网段的任何主机都能伪造一条"微信消息"，AI 会当成
> 真人说的去回复、甚至调工具。所以 `callback_host` 非回环且 `callback_token` 为空时，
> **回调服务会直接拒绝启动**并打 ERROR 日志（与 OneBot(reverse) 的边界同一套标准）。

4. 重启主程序。日志出现 `Gewechat 登录二维码已生成` 后，在 Gewechat 管理页扫码登录（首次需人工扫码；后续登录态可持久化）。
5. 用微信小号给机器人发消息，机器人走同一大脑回复；发图/发视频已支持。

> 备注：Gewechat 的 REST 接口路径（`login/GetToken`、`message/PostText` 等）以你部署版本为准，联调时按实际命名核对 `src/channels/gewechat.py`。

## 接入 QQ空间（qzone 发布器，仅出站）

QQ空间用**浏览器登录态 cookie** 调腾讯空间接口（`emotion_cgi_publish_v6` 发说说、`emotion_cgi_msglist_v6` 读说说，需算 `g_tk` 防伪 token）。属非官方接口，灰色；cookie 泄露等于把空间交出去，**妥善保管，且只用离身小号**。

触发方式：当大脑/主动/媒体钩子产出一条 `channel="qzone"` 的 `AI_DECISION`（例如 AI 想主动发一条生活动态、或发出自己生成的图），本发布器自动发说说。这正是不少开源 AI 伴侣"在 QQ 动态里发图"的真人感来源。

1. 浏览器登录 `i.qq.com` → F12 → Console 输入 `document.cookie` → 复制全部输出。
2. 编辑 `config.yaml`：

```yaml
channels:
  qzone:
    enabled: true
    cookie: "<粘贴的 cookie>"
    uin: "<你的 QQ 号>"
```

3. 重启主程序。日志出现 `QQ空间发布器已就绪` 即生效。
4. 由大脑侧（媒体生成钩子 / 主动搭话）触发 `channel=qzone` 决策来发说说；也支持读说说（`QzoneClient.get_moments`）。

> 备注：发说说的**图片发布**需先调空间相册上传接口取得 picBo，结构随版本变动，本文先实现纯文本说说。⏸ **QQ空间/微信朋友圈的"自动发图"已由用户决定延后**——列入待办清单（任务 #103），暂不实现（灰色逆向 + cookie/账号风险高，且需真实小号+网关联调）。

## 安全性提示

- **务必设 `allow_users` / `block_users` / `allow_groups`**：默认对所有私聊、所有群里 @ 它的消息都会触发大脑回复，可能暴露你的长期记忆与性格设定。至少把 `allow_users` 填成你自己的 QQ 号。
- 媒体摄入（图片/链接）会调用你的 LLM 与网络抓取，开启前确认成本与隐私。
- access_token 会被写入 `config.yaml` 明文落盘——注意该文件安全（与现有 api_key 同等级别）。

## 代码结构

```
src/channels/
  __init__.py        通道注册表 + build_channels（按 config 构建已启用通道：qq/wechat/qzone）
  base.py            Channel 基类（start/stop/_feed_brain/_on_decision）
  onebot.py          OneBotChannel(QQ)：forward/reverse WS、CQ 解析、发文本/图片/视频(CQ)、黑白名单
  gewechat.py        GewechatChannel(微信)：REST 客户端 + 扫码登录 + 回调收消息 + 发文本/图/视频
  qzone.py           QzonePublisher(QQ空间)：cookie+g_tk 发说说/读说说（仅出站）
  media_ingest.py    媒体摄入：parse_onebot_message + 图片/链接→上下文 + 落记忆
  tests/test_onebot.py         无 QQ/LLM/WS 单测（解析/路由/隔离/群@/黑名单）
  tests/test_gewechat_qzone.py 客户端单测（Gewechat 参数 / Qzone g_tk+JSONP 剥壳+解析）
  tests/test_media_routing.py   媒体/空间路由单测（OneBot 媒体 CQ / qzone 仅本通道发）
```

## 已知限制 / 后续

- 三个通道均已脚手架化、单测覆盖离线逻辑；**真实联调未做**（需你提供对应小号 + 网关环境）。
- 微信/空间的接口路径与 cookie 字段随版本变动，联调时按实际核对 `gewechat.py` / `qzone.py`。
- QQ空间**图片说说**需先调相册上传接口，本文先实现纯文本说说（TODO）。
- 媒体摄入是"附上下文 + 落记忆"，未做"把整段聊天沉淀为关系记忆"的深度处理（属 E/F 后续）。
- 群消息目前"仅 @ 回复"，未做群内上下文去重/防刷。
- **设置页内开关 UI** 尚未做（当前需在 `config.yaml` 改 `enabled` 后重启）；媒体生成钩子（AI 主动发自拍/图）待接。
