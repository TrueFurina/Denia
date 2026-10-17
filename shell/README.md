# shell/ — 桌面壳（PoC 阶段）

用途：验证"同一份 rig 页面换宿主后能否真正透出桌面，且功能还能跑"。
结论与证据见根目录 `产品化路线_2026-10-18.md` 第三节。

## 运行

```bash
cd shell
ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/ npm install electron
cd .. && python -u tools/rpc_bridge_smoke.py
```

`rpc_bridge_smoke.py` 会自动起一个最小 Python 后端（静态服务器 + RPC），写握手文件，
拉起 Electron，然后验证五件事：桥接存在、后端收到 RPC 调用、PSD 数据真的送达、
窗口真透明、人物在渲染。

## 已验证的事实

- `transparent:true` + `frame:false` 在本机 win32 上确实得到每像素透明；
- rig 页面 `?bare=1` 在 Electron 里正常渲染，**前端零改动**；
- `window.pywebview.api` 由 `preload.js` 的 Proxy 转发到 Python RPC（不是壳内重写）。

## 已知坑

- 本机 shell 常带 `ELECTRON_RUN_AS_NODE=1`，会让 `electron.exe` 退化成 node（`app` 未定义）。
  启动前必须剔除该环境变量。
- **控制命令必须走 TCP，不要用 stdin**：Windows 管道里的 stdin 收不到，命令全丢，
  会让"隐藏后抓屏"变成假阳性。`tools/shell_control.py` 已封装。
- 不要把 `node_modules/`（约 247MB）提交进仓库，已在 `.gitignore` 里排除。

