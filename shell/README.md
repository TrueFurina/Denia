# shell/ — 桌面壳可行性验证（PoC，不是最终壳）

用途：验证"同一份 rig 页面换宿主后能否真正透出桌面"。
结论见根目录 `产品化路线_2026-10-18.md` 第三节。

## 运行

```bash
cd shell
ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/ npm install electron
cd .. && python -u tools/poc_shell_transparency.py
```

## 已验证的事实

- `transparent:true` + `frame:false` 在本机 win32 上确实得到每像素透明（角落与桌面逐点相等）；
- rig 页面 `?bare=1` 在 Electron 里正常渲染，**前端零改动**；
- `window.pywebview.api` 由 `preload.js` 的 Proxy shim 顶替，未实现的方法统一回 `null`。

## 已知坑

- 本机 shell 常带 `ELECTRON_RUN_AS_NODE=1`，会让 `electron.exe` 退化成 node（`app` 未定义）。
  启动前必须剔除该环境变量。
- 不要把 `node_modules/`（约 247MB）提交进仓库，已在 `.gitignore` 里排除。
