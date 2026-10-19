# 本目录的来源与本地改造（重要，勿删）

`live2d/rig` 是**运行时依赖，不是开发工具**。桌面看板娘就是它：

- `src/ui/web/index.html:291` —— 主界面右下角 `<iframe src="live2d/rig/index.html?v=2">`
- `src/ui/main_window.py:868` —— 桌宠透明窗加载同一个页面（`?bare` 纯人物模式）

## 上游

- 仓库：https://github.com/852wa/Anime2.5DRig
- 基线提交：`d488258` "Add files via upload"（作者 hakoniwa）
- 许可证：见本目录 `LICENSE`

## 为什么直接纳入本仓库（而不是 submodule）

此前它以 gitlink（mode `160000`）形式记录，但仓库里**没有 `.gitmodules`**。
后果是：任何人重新 clone 本仓库，`live2d/rig` 只会得到一个空目录，
看板娘与桌宠窗口直接失效；用 zip 打包发布同样会漏掉整个目录。

由于本目录是产品运行时的一部分，且本地改造量已远超上游（见下），
改为把文件直接纳入主仓库跟踪。全目录 9 个文件、约 4.7MB。

## 本地改造（相对上游 `d488258`）

改动集中在 `index.html`（+961 行）与 `lib/rigger.js`（10 行），都是为接入
「达妮娅」桌面伴侣加的：

| 改造 | 作用 |
|---|---|
| `?bare` 纯人物模式 | 隐藏全部编辑面板（`#panel.hidden`），只渲染人物，供桌宠透明窗使用 |
| `html{background:transparent}` | 配合 frameless 窗口实现透明背景 |
| `window.__rigControl` | 后端控制器：表情 / 动作 / FX 位置与偏移持久化 |
| `window.__petInteract` | 桌宠交互入口（点击、拖拽命中区域） |
| `petShowBubble` / `startPetBubblePoll` | 气泡渲染与轮询，露出后回调 `pet_bubble_shown` |
| `setFxPosMode` / `saveFxOffsets` / `saneOffset` | 特效锚点模式与偏移保存 |
| `window.onerror` / `__eyeDbg` / `__dbgSet` | 前端错误上报与表情调试入口 |

## 升级上游时怎么做

1. 在临时目录 clone 上游，切到目标提交；
2. `diff -u` 对比本目录与上游，把上述改造点重新打上去；
3. 重点回归：`?bare` 透明背景、`__rigControl` 表情切换、`petShowBubble` 气泡回调；
4. 不要把上游的 `index.html` 整份覆盖回来 —— 那会一次性丢掉全部桥接接口。
