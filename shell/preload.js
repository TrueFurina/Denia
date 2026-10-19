// 页面零改动的关键：用同名 shim 顶替 pywebview 的 js_api，转发到 Python 的本地 RPC。
//
// 为什么不把方法在壳里重写一遍：CharacterAPI 有几十个方法且会继续增长，
// 壳里重写必然与后端漂移。RPC 让两条通道指向**同一个对象实例**，
// 等价性由 src/tests/test_rpc_server.py 保证。
const { ipcRenderer } = require('electron')

function argValue(prefix) {
  const hit = process.argv.find((a) => a.startsWith(prefix))
  return hit ? hit.slice(prefix.length) : ''
}

const RPC_URL = argValue('--daniya-rpc=')
const TOKEN = argValue('--daniya-token=')

// 页面里大量 "await api.x()" 后再判断返回值；网络失败必须变成**异常**，
// 不能静默返回 null，否则页面会以为"功能正常但没有数据"。
async function call(method, args) {
  if (!RPC_URL || !TOKEN) {
    throw new Error('RPC 未配置（缺少握手信息）')
  }
  const resp = await fetch(RPC_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Daniya-Token': TOKEN },
    body: JSON.stringify({ method, args: args || [], kwargs: {} }),
  })
  let payload
  try {
    payload = await resp.json()
  } catch (e) {
    throw new Error('RPC 返回非 JSON（HTTP ' + resp.status + '）')
  }
  if (!resp.ok || !payload.ok) {
    const err = payload.error || {}
    const e = new Error(err.message || ('RPC HTTP ' + resp.status))
    e.name = err.type || 'RpcError'
    throw e
  }
  return payload.result
}

// pywebview 给的是"方法即属性"：api.foo(a, b) 直接调用。
// Proxy 把任意方法名映射到 RPC；方法是否存在由后端判定（不存在回 404 → 这里抛异常）。
const api = new Proxy({}, {
  get(_target, prop) {
    if (typeof prop !== 'string') return undefined
    return (...args) => call(prop, args)
  },
})

window.pywebview = {
  api,
  token: 'electron-shim',
  platform: 'windows',
}

// 壳侧能力：页面里已有 if(window.pywebview.api.xxx) 的守卫，这里只补退出通道，
// 便于 PoC 与后续托盘联动。
window.daniyaShell = {
  quit: () => ipcRenderer.send('daniya-quit'),
}
