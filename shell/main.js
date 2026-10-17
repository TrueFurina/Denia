// 达妮娅桌面壳 · 透明窗可行性验证（PoC）
//
// 目的：用同一份 rig 页面，验证"换壳后是否真能得到桌面级透明"。
// 页面本身一行不改：pywebview 的 js_api 由 preload 里的同名 shim 顶替。
//
// 运行（由 tools/poc_shell_transparency.py 驱动，也可手动）：
//   .\node_modules\electron\dist\electron.exe main.js --x 200 --y 200 --w 300 --h 400
const { app, BrowserWindow, ipcMain } = require('electron')
const path = require('path')

function arg(name, dflt) {
  const i = process.argv.indexOf('--' + name)
  if (i < 0) return dflt
  const v = process.argv[i + 1]
  return v === undefined ? dflt : v
}

const ROOT = path.resolve(__dirname, '..')
const url = arg('url', 'file:///' + path.join(ROOT, 'src/ui/web/live2d/rig/index.html').replace(/\\/g, '/') + '?v=2&bare=1')
const x = parseInt(arg('x', '200'), 10)
const y = parseInt(arg('y', '200'), 10)
const w = parseInt(arg('w', '300'), 10)
const h = parseInt(arg('h', '400'), 10)
const autoQuit = parseInt(arg('quit', '0'), 10)

// 透明无边框窗口：transparent 必须配合 frame:false，否则 Windows 上透明区会显示成黑色
app.commandLine.appendSwitch('disable-features', 'CalculateNativeWinOcclusion')

app.whenReady().then(() => {
  const win = new BrowserWindow({
    x, y, width: w, height: h,
    transparent: true,
    frame: false,
    hasShadow: false,
    resizable: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    focusable: true,
    backgroundColor: '#00000000',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: false,
      sandbox: false,
      nodeIntegration: false,
      backgroundThrottling: false,
    },
  })

  win.loadURL(url)

  win.webContents.on('did-finish-load', async () => {
    // 把真实边界与缩放比交给外部采样脚本，避免两边各算一套坐标
    const b = win.getBounds()
    const sf = require('electron').screen.getDisplayMatching(b).scaleFactor
    console.log('BOUNDS ' + JSON.stringify({ ...b, scaleFactor: sf }))
    try {
      console.log('TITLE ' + (await win.webContents.executeJavaScript('document.title')))
    } catch (e) { console.log('TITLE <读取失败>') }
    console.log('READY')

    // 采样握手：外部脚本用 stdin 控制"隐藏/显示"，才能拿到同一区域的真背景做对照
    process.stdin.setEncoding('utf-8')
    process.stdin.on('data', (chunk) => {
      for (const cmd of String(chunk).split(/\s+/)) {
        if (cmd === 'hide') { win.hide(); console.log('HIDDEN') }
        else if (cmd === 'show') { win.show(); console.log('SHOWN') }
        else if (cmd === 'alpha') {
          // 页面自身像素的 alpha（判断"页面透明"是否成立，与窗口合成是两件事）
          win.webContents.capturePage().then((img) => {
            const { width, height } = img.getSize()
            const p = img.getBitmap()
            // BGRA：取左上角第一个像素的 alpha
            console.log('PAGE_ALPHA ' + p[3] + ' size=' + width + 'x' + height)
          }).catch((e) => console.log('PAGE_ALPHA ERR ' + e))
        }
        else if (cmd === 'quit') { app.quit() }
      }
    })

    if (autoQuit > 0) setTimeout(() => app.quit(), autoQuit * 1000)
  })

  ipcMain.handle('read-psd', () => {
    const fs = require('fs')
    for (const name of ['model.psd', 'model_fixed.psd']) {
      const p = path.join(ROOT, 'src/ui/web/live2d/models', name)
      try {
        if (fs.existsSync(p)) {
          return 'data:application/octet-stream;base64,' + fs.readFileSync(p).toString('base64')
        }
      } catch (e) { /* 忽略，继续找下一个 */ }
    }
    return ''
  })
}).catch((e) => {
  console.error('START_FAIL ' + e)
  app.quit()
})
