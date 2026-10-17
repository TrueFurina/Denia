// 达妮娅桌面壳 · PoC 宿主（验证"换壳后透明 + 功能可用"）
//
// 页面一行不改：pywebview 的 js_api 由 preload.js 的同名 shim 转发到 Python 本地 RPC。
//
// 运行：
//   .\node_modules\electron\dist\electron.exe main.js --handshake <path> --x 200 --y 200 --w 320 --h 420
const { app, BrowserWindow, ipcMain } = require('electron')
const path = require('path')

const NL = String.fromCharCode(10)

function arg(name, dflt) {
  const i = process.argv.indexOf('--' + name)
  if (i < 0) return dflt
  const v = process.argv[i + 1]
  return v === undefined ? dflt : v
}

const ROOT = path.resolve(__dirname, '..')

// 握手文件由 Python 侧写入：{ url, token, web_base }
// 有了它，壳不需要知道端口，也不用自己起静态服务器——页面与 RPC 都来自同一个后端进程。
let handshake = null
const hsPath = arg('handshake', '')
if (hsPath) {
  try {
    handshake = JSON.parse(require('fs').readFileSync(hsPath, 'utf8'))
  } catch (e) {
    console.error('HANDSHAKE_FAIL ' + e)
  }
}

const url = arg('url', handshake && handshake.web_base
  ? handshake.web_base + '/live2d/rig/index.html?v=2&bare=1'
  : 'file:///' + path.join(ROOT, 'src/ui/web/live2d/rig/index.html').replace(/\\/g, '/') + '?v=2&bare=1')
const x = parseInt(arg('x', '200'), 10)
const y = parseInt(arg('y', '200'), 10)
const w = parseInt(arg('w', '300'), 10)
const h = parseInt(arg('h', '400'), 10)
const autoQuit = parseInt(arg('quit', '0'), 10)

// transparent 必须配合 frame:false，否则 Windows 上透明区会显示成黑色
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
      // RPC 连接信息只交给 preload，不进页面 DOM
      additionalArguments: [
        '--daniya-rpc=' + (handshake ? handshake.url : ''),
        '--daniya-token=' + (handshake ? handshake.token : ''),
      ],
    },
  })

  win.loadURL(url)

  win.webContents.on('did-finish-load', async () => {
    const b = win.getBounds()
    const sf = require('electron').screen.getDisplayMatching(b).scaleFactor
    console.log('BOUNDS ' + JSON.stringify({ ...b, scaleFactor: sf }))
    try {
      console.log('TITLE ' + (await win.webContents.executeJavaScript('document.title')))
    } catch (e) { console.log('TITLE <读取失败>') }
    console.log('READY')

    // 控制通道：TCP，不用 stdin。
    // 实测 Windows 下 Electron 主进程 stdin 在管道模式里收不到数据（命令全丢）；
    // 若拿它做"隐藏/显示"对照，两次抓到的都是显示态 → 会得出"透明"的假阳性。
    const net = require('net')
    const control = net.createServer((sock) => {
      sock.setEncoding('utf-8')
      const say = (text) => sock.write(text + NL)
      sock.on('data', (chunk) => {
        for (const cmd of String(chunk).split(/\s+/)) {
          if (!cmd) continue
          if (cmd === 'hide') { win.hide(); say('HIDDEN') }
          else if (cmd === 'show') { win.show(); say('SHOWN') }
          else if (cmd === 'visible') { say('VISIBLE ' + (win.isVisible() ? '1' : '0')) }
          else if (cmd === 'bounds') {
            say('BOUNDS2 ' + JSON.stringify({ ...win.getBounds(), scaleFactor: sf }))
          } else if (cmd === 'probe') {
            win.webContents.executeJavaScript(`(async () => {
              const out = {
                hasBridge: !!(window.pywebview && window.pywebview.api),
                psdType: typeof (window.pywebview && window.pywebview.api.get_live2d_psd),
                title: document.title,
              };
              try { out.psdLen = String(await window.pywebview.api.get_live2d_psd()).length }
              catch (e) { out.psdErr = String((e && e.message) || e) }
              return out;
            })()`).then((r) => say('PROBE ' + JSON.stringify(r)))
              .catch((e) => say('PROBE_ERR ' + e))
          } else if (cmd === 'quit') {
            say('BYE')
            setTimeout(() => app.quit(), 50)
          } else {
            say('UNKNOWN ' + cmd)
          }
        }
      })
      sock.on('error', () => {})
    })
    control.listen(0, '127.0.0.1', () => {
      console.log('CONTROL ' + control.address().port)
    })

    if (autoQuit > 0) setTimeout(() => app.quit(), autoQuit * 1000)
  })

  // 壳侧退出通道（页面/托盘都可以调）
  ipcMain.on('daniya-quit', () => app.quit())
}).catch((e) => {
  console.error('START_FAIL ' + e)
  app.quit()
})
