// 页面零改动的关键：用同名 shim 顶替 pywebview 的 js_api。
//
// rig 页面只依赖 window.pywebview.api.<方法>() 这一层，
// 所以换壳时前端不需要任何修改——只要把这一层重新接到新宿主上。
const { ipcRenderer } = require('electron')

let psdCache = null

const api = {
  get_live2d_psd: async () => {
    if (psdCache === null) psdCache = await ipcRenderer.invoke('read-psd')
    return psdCache
  },
  get_live2d_prop: async () => '',
}

// 未实现的方法统一回一个 resolved promise，避免页面里 await 到 undefined 后抛错
const shim = new Proxy(api, {
  get(target, prop) {
    if (prop in target) return target[prop]
    if (typeof prop !== 'string') return undefined
    return async () => null
  },
})

window.pywebview = {
  api: shim,
  token: 'poc-token',
  platform: 'windows',
}
