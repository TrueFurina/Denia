// 最小对照：一个最朴素的不透明窗（纯蓝页，屏幕采样时无歧义）。
// 目的：判断 GPU 回退开关下 Electron 到底还能不能把画面呈现到屏幕上。
// 附带把 webContents 关键事件打出来——外面看"窗口在、画面空"时，
// 只有进程内部知道是加载失败、渲染进程崩了、还是根本没提交帧。
const { app, BrowserWindow } = require('electron')

const SKIP_OCCLUSION_SWITCH = process.argv.includes('--keep-occlusion')

app.whenReady().then(async () => {
  if (!SKIP_OCCLUSION_SWITCH) {
    app.commandLine.appendSwitch('disable-features', 'CalculateNativeWinOcclusion')
  }
  const win = new BrowserWindow({
    x: 100, y: 100, width: 500, height: 300,
    title: 'DaniyaQcPlainWhite',
    show: true,
    transparent: false,
    frame: true,
    backgroundColor: '#0000ff',
    // 壳是 sandbox:false；--sandbox 用来测沙箱化渲染进程的对照
    webPreferences: { sandbox: process.argv.includes('--sandbox') },
  })

  const wc = win.webContents
  const log = (t) => console.log('EV ' + t)
  wc.on('did-start-loading', () => log('did-start-loading'))
  wc.on('did-finish-load', () => log('did-finish-load'))
  wc.on('did-fail-load', (_e, code, desc, url) => log(`did-fail-load ${code} ${desc} ${url}`))
  wc.on('dom-ready', () => log('dom-ready'))
  wc.on('render-process-gone', (_e, d) => log('render-process-gone ' + JSON.stringify(d)))
  win.on('unresponsive', () => log('unresponsive'))

  try {
    await win.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(
      '<html><body style="margin:0;background:#0000ff">' +
      '<div style="font:700 40px sans-serif;color:#000;padding:40px">QC BLUE</div>' +
      '</body></html>'))
    log('loadURL resolved')
  } catch (e) {
    log('loadURL rejected ' + e)
  }

  // 直接问合成器要一帧：能拿到蓝色说明"页面画出来了"。
  // 与"画到屏幕上"是两件事，但能排除"页面根本没渲染"。
  const img = await wc.capturePage()
  const s = img.getSize()
  const b = img.toBitmap()
  const i = (Math.floor(s.height / 2) * s.width + Math.floor(s.width / 2)) * 4
  console.log('CAPTURE ' + JSON.stringify({
    size: s, center: [b[i + 2], b[i + 1], b[i], b[i + 3]],
    bounds: win.getBounds(), visible: win.isVisible(), loading: wc.isLoading(),
  }))
  setTimeout(() => app.exit(0), 6000)
})
