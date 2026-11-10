# 官网发布指南

> 目标：把 `site/` 变成一个能让人**真的下载到达妮娅**的公开网页。
>
> 参考实现：`https://truefurina.github.io/SecAutoMind/` —— 同一套做法：**静态页放 GitHub Pages，二进制放 GitHub Releases**。仓库里不放 exe（GitHub 单文件 100MB 限制 + 仓库会变肥 + clone 变慢）。

---

## 一句话流程

```
① 出安装包  →  ② 建 Release 上传 exe  →  ③ 推 site/ 到 gh-pages  →  ④ 开 Pages  →  ⑤ 回填 3 个 URL  →  ⑥ 验证
```

全程约 40 分钟（不含出包时间）。

---

## ① 出安装包

项目里现在**没有可发布的 exe**（`dist/` 下只有两个源码 zip）。先出一个：

```bash
cd D:/Denia
.venv/Scripts/python.exe tools/make_dist.py
```

产物预期：`达妮娅AI桌面伴侣_Setup_v0.2.0.exe`。**2026-11-04 当前 HEAD 实测 175.0 MB**
（NSIS/LZMA；stage 645.1 MB = Electron 壳 321.9 + python 295.0 + src 26.5 + 其余 1.7）。
体积随依赖档位变化，出包后以 `make_dist.py` 打印的实测值为准，不要沿用旧数字。

**出包后记两个值**（后面要填进网页）：

```bash
# 1) 文件大小
ls -la *.exe

# 2) SHA-256（Windows 自带工具）
certutil -hashfile "达妮娅AI桌面伴侣_安装程序_v0.2.0.exe" SHA256
```

> ⚠️ 如果 `make_dist.py` 还没跑通，就**先只发源码包**——网页上安装版会显示「待发布」，不影响源码版下载。不要为了上线临时手压一个包（2026-10-19 就是这么出事：手压整个工作目录，混进了 `data/`、`call_records.json`、`user_knowledge.yaml` 三个隐私文件）。
>
> 用 `tools/pack_for_teammate.py` 出包，它自带排除 + 出包后复核，混进隐私文件会直接删包。

---

## ② 建 Release 上传 exe

### 方式 A：网页操作（推荐，最直观）

1. 打开 `https://github.com/<你的用户名>/<仓库名>/releases/new`
2. **Tag**：`v0.2.0` → 点「Create new tag」
3. **Title**：`达妮娅 AI 桌面伴侣 v0.2.0`
4. **Description**：见下方模板
5. **上传附件**：
   - `达妮娅AI桌面伴侣_安装程序_v0.2.0.exe`（175.0MB，安装版）
   - `达妮娅AI桌面伴侣_源码_v0.2.0_20261104.zip`（17.9MB，源码包）
6. ⚠️ **不要勾**「Set as a pre-release」（除非你确实想标测试）
7. 点 **Publish release**

### 方式 B：命令行（有 gh CLI 时）

```bash
gh release create v0.2.0 \
  "达妮娅AI桌面伴侣_安装程序_v0.2.0.exe" \
  "dist/达妮娅AI桌面伴侣_源码_v0.2.0_20261104.zip" \
  --title "达妮娅 AI 桌面伴侣 v0.2.0" \
  --notes-file RELEASE_NOTES.md
```

### Release 说明模板

```markdown
## 达妮娅 · AI 桌面伴侣 v0.2.0

她住在你的 Windows 里。她记住的事，全在你们自己的硬盘上。

### 下载
| 版本 | 文件 | 大小 | 说明 |
|---|---|---|---|
| 安装版（推荐） | DaniaAI-Setup-0.2.0-x64.exe | 175.0 MB | 内置运行时，双击就装，不需要 Python |
| 源码包 | 达妮娅AI桌面伴侣_源码_v0.2.0_20261104.zip | 17.9 MB | 需要 Python 3.12 |

### 校验值
```
SHA-256 (安装版) : <填这里>
SHA-256 (源码包) : <填这里>
```

### ⚠️ 首次运行提示
分发包未做代码签名，Windows SmartScreen 会拦截一次。点「更多信息」→「仍要运行」即可。

### 本版验证记录
- 241 道发布闸门全绿 / 变异刀逐条验证
- 干净环境验包：doctor **16 PASS / 3 WARN / 0 FAIL**，启动约 7 秒，未捕获异常 **0**
- 已知限制：真透明桌宠需 `cd shell && npm install`（**本机安装后的目录体积**，实测 381 MB / 569 文件；与安装包内 `shell/` stage 的约 322 MB 不是同一口径）；首启引导完整流程待验证

完整验包记录见仓库 `docs/验包记录_2026-11-04.md`。
```

**上传后拿到两个下载地址**，形如：

```
https://github.com/用户/仓库/releases/download/v0.2.0/DaniaAI-Setup-0.2.0-x64.exe
https://github.com/用户/仓库/releases/download/v0.2.0/达妮娅AI桌面伴侣_源码_v0.2.0_20261104.zip
```

---

## ③ 推 site/ 到 gh-pages 分支

**为什么不放 main：** `site/` 和源码混在 main 上会跟 `docs/` 里那些内部文档（验包记录、产品化路线）一起被 Pages 暴露。单独一个分支干净。

```bash
cd D:/Denia

# 首次创建 gh-pages 分支（把 site/ 的内容当分支根）
git subtree push --prefix site origin gh-pages

# 或者用更可控的方式：
git checkout --orphan gh-pages
git rm -rf .
cp -r site/* .
git add .
git commit -m "site: 官网 v0.2.0"
git push origin gh-pages
git checkout main
```

> 💡 `git subtree push` 每次都重新算，慢但省心；`orphan` 方式更可控但要小心别在 gh-pages 分支上误删东西。

---

## ④ 开启 GitHub Pages

1. 打开仓库 → **Settings** → 左侧 **Pages**
2. **Source**：`Deploy from a branch`
3. **Branch**：`gh-pages` ／ 目录 `/ (root)`
4. 点 **Save**
5. 等 1–3 分钟，页面顶部会显示网址：

```
https://<你的用户名>.github.io/<仓库名>/
```

> 如果仓库名是 `<用户名>.github.io`，网址就是 `https://<用户名>.github.io/`（根路径）。

---

## ⑤ 回填 3 个 URL

打开 `site/index.html`，找到这一段（约在第 700 行，搜 `const RELEASE`）：

```javascript
const RELEASE = {
  version : 'v0.2.0',

  repo    : '',   // ← 填仓库主页
  setup   : '',   // ← 填安装版下载地址
  source  : '',   // ← 填源码包下载地址

  shaSetup  : '', // ← 填安装版 SHA-256（可选）
  shaSource : ''  // ← 填源码包 SHA-256（可选）
};
```

填成（示例）：

```javascript
const RELEASE = {
  version : 'v0.2.0',
  repo    : 'https://github.com/你的用户名/Denia',
  setup   : 'https://github.com/你的用户名/Denia/releases/download/v0.2.0/DaniaAI-Setup-0.2.0-x64.exe',
  source  : 'https://github.com/你的用户名/Denia/releases/download/v0.2.0/达妮娅AI桌面伴侣_源码_v0.2.0_20261104.zip',
  shaSetup  : 'A1B2C3...（64位十六进制）',
  shaSource : 'D4E5F6...'
};
```

**留空的行为**：按钮变灰显示「安装包待发布 / 源码包待发布」，不可点击，但页面其他部分正常。所以**可以先上线，后填空**。

改完重新推：

```bash
cp site/index.html .   # 如果在 gh-pages 分支直接改就不用这步
git add site/index.html
git commit -m "site: 回填下载地址"
git subtree push --prefix site origin gh-pages
```

---

## ⑥ 验证（别跳过这步）

按顺序点一遍，任何一步失败都说明链接有问题：

- [ ] 打开 `https://<用户名>.github.io/<仓库名>/`
- [ ] **立绘显示**（`assets/stand.png` 加载成功；如果裂图，说明图片没推上去）
- [ ] **点「下载安装包」** → 真的开始下载 exe（不是跳到 404）
- [ ] **点「下载源码包」** → 真的开始下载 zip
- [ ] 页脚 GitHub 链接 → 到仓库
- [ ] **手机打开**（微信里发给自己，用手机浏览器看）→ 排版不乱
- [ ] 下载完对一次 SHA-256：
      ```bash
      certutil -hashfile 下载的文件.exe SHA256
      ```
      与网页上显示的一致
- [ ] 换一个没登录 GitHub 的浏览器/无痕窗口 → 下载链接仍可点（Releases 是公开的，应该没问题）

---

## 常见问题

### Q：`git subtree push` 报错 `gh-pages` 已存在

```bash
git push origin `git subtree split --prefix site main`:gh-pages --force
```

### Q：Pages 显示 404

1. 确认分支是 `gh-pages` 且根目录有 `index.html`
2. 确认文件名叫 `index.html`（全小写，不是 `Index.html`）
3. Settings → Pages 里看有没有报错
4. 等 3 分钟（首次部署慢）

### Q：立绘不显示

```bash
ls site/assets/stand.png      # 确认存在
```

确认 `index.html` 里的路径是 `assets/stand.png`（相对路径，不是 `D:/Denia/...` 绝对路径）。**大小写敏感**：GitHub Pages 区分 `Stand.png` 和 `stand.png`。

### Q：exe 上传失败（超过 100MB）

GitHub Release 单个附件上限 **2GB**（足够），但**仓库内单文件**上限 100MB。
用 Release 上传就没这个问题——**不要把 exe 提交进仓库**。

### Q：想用自己的域名

1. 仓库根目录加 `CNAME` 文件，内容一行：`daniya.example.com`
2. 域名 DNS 加 `CNAME` 记录指向 `<用户名>.github.io`
3. Settings → Pages → Custom domain 填入并勾选 Enforce HTTPS

### Q：以后每次发版要做什么

```
改 site/index.html 里的 version + 三个 URL（指向新 Release）
→ 推 gh-pages
→ 30 秒完成
```

建议把这一步写进 `make_dist.py` 的收尾提示，避免忘记。

---

## 附：站点结构

```
site/
├── index.html          # 单文件站点（内联 CSS + JS，无构建步骤，无外部依赖）
├── assets/
│   └── stand.png       # 角色立绘（从 src/ui/web/danya/av/stand.png 复制）
└── PUBLISH.md          # 本文档（不发布到 Pages 也没关系）
```

**技术特点**：
- 零依赖、零构建——改完直接推，不需要 npm/webpack
- 响应式（手机、平板、桌面）
- 滚动淡入、锚点平滑滚动
- 所有下载地址集中在 `RELEASE` 对象一处，改一处全站生效

---

## 最后：一个提醒

上线之前请确认这两件事，否则会给自己招麻烦：

1. **隐私文件没进包** —— 用 `pack_for_teammate.py` 出包，别手压（2026-10-19 的教训）
2. **网页上的数字是真的，且各有出处** —— 两组数字来自**两个不同的包**，不可互换引用：
   - **源码包**（17.9 MB / 848 条目 / 16 PASS 3 WARN 0 FAIL / 启动约 7 秒 / 241 道门禁）
     → `docs/验包记录_2026-11-04.md`，包内不含运行时，用户须自建 venv。
   - **安装包**（175.0 MB / 双击就装 / 包内 doctor 17 PASS 2 WARN 0 FAIL）
     → `docs/deliverables/2026-11-04_当前HEAD出包与验包记录.md`，当前 HEAD 实测，
       内置运行时与 166 项依赖；体积随依赖与 Electron 壳变化，重出包后**必须重测再改**。
   如果后续版本数字变了，记得同步改网页，否则会从「诚实」变成「虚假宣传」。

> 这个站点最大的资产不是设计，是**每一句都能被验证**。
> 别让它变成第二份 README。