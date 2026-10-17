/* 记忆库页 UI 真机渲染（mock 后端喂 data/memory.json 真实数据）
   用途：验证「单条删除」按钮渲染 + 条目布局（不依赖运行中的达妮娅主进程）。
   用法：node tools/_cdp_memory_shot.cjs [outdir] */
const { spawn } = require('child_process');
const path = require('path'), fs = require('fs');
const PY = 'C:/Users/Administrator/AppData/Local/Programs/Python/Python312/python.exe';
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const WEBROOT = 'G:\\ai对话\\11\\src\\ui\\web';
const MEMJSON = 'G:\\ai对话\\11\\data\\memory.json';
const HTTP_PORT = 8896, CDP_PORT = 9336;
const OUT = process.argv[2] || 'G:\\ai对话\\11\\docs\\_shots_memory';
const sleep = ms => new Promise(r => setTimeout(r, ms));

const raw = JSON.parse(fs.readFileSync(MEMJSON, 'utf-8'));
const mems = raw.memories || [];
const items = mems.map(m => ({
  id: m.id || '', text: m.text || '', type: m.type || 'user',
  hits: m.hits || 1, updated_at: m.updated_at || 0,
})).sort((a, b) => b.updated_at - a.updated_at);
const cbt = { directive: 0, user: 0, world: 0, episode: 0 };
items.forEach(i => { if (cbt[i.type] != null) cbt[i.type]++; });
const MOCK = {
  success: true, enabled: true, max_items: 100, mode: 'jarvis', semantic_search: true,
  stats: { count: items.length, count_by_type: cbt, items },
};

async function cdp(ws, method, params) {
  const id = (cdp._i = (cdp._i || 0) + 1);
  return new Promise((res, rej) => {
    const to = setTimeout(() => rej(new Error('t/o ' + method)), 25000);
    const h = ev => { let m; try { m = JSON.parse(ev.data); } catch { return; }
      if (m.id === id) { clearTimeout(to); ws.removeEventListener('message', h); m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result); } };
    ws.addEventListener('message', h); ws.send(JSON.stringify({ id, method, params: params || {} }));
  });
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const srv = spawn(PY, ['-m', 'http.server', String(HTTP_PORT), '--bind', '127.0.0.1'], { cwd: WEBROOT, stdio: 'ignore' });
  const prof = 'G:\\_tm_probe\\edge_mem_' + Date.now();
  const edge = spawn(EDGE, ['--headless=new', '--disable-gpu', '--no-first-run', '--disable-extensions',
    '--mute-audio', '--remote-debugging-port=' + CDP_PORT, '--user-data-dir=' + prof, 'about:blank'], { stdio: 'ignore' });
  const clean = () => { try { edge.kill(); } catch {} try { srv.kill(); } catch {} try { fs.rmSync(prof, { recursive: true, force: true }); } catch {} };
  process.on('exit', clean);
  try {
    let v = null;
    for (let i = 0; i < 40; i++) { try { v = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`)).json(); break; } catch { await sleep(250); } }
    if (!v) throw new Error('CDP 未就绪');
    const t = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/new?about:blank`, { method: 'PUT' })).json();
    const ws = new WebSocket(t.webSocketDebuggerUrl);
    await new Promise((r, j) => { ws.addEventListener('open', r); ws.addEventListener('error', j); });
    await cdp(ws, 'Page.enable');
    await cdp(ws, 'Runtime.enable');
    // 在页面脚本执行前注入 mock 后端
    await cdp(ws, 'Page.addScriptToEvaluateOnNewDocument', {
      source: `window.pywebview = { api: { get_memory_settings: function(){ return Promise.resolve(${JSON.stringify(MOCK)}); } } };`,
    });
    await cdp(ws, 'Emulation.setDeviceMetricsOverride', { width: 1090, height: 720, deviceScaleFactor: 1, mobile: false });
    await cdp(ws, 'Page.navigate', { url: `http://127.0.0.1:${HTTP_PORT}/redesign/ui_redesign_memory.html` });
    await sleep(2600);
    const ev = await cdp(ws, 'Runtime.evaluate', { expression: `(() => {
      const items = document.querySelectorAll('.mem-item');
      const dels  = document.querySelectorAll('.mem-del');
      const stats = [...document.querySelectorAll('.mstat-num')].map(e=>e.textContent.trim());
      const first = document.querySelector('.mem-item');
      return JSON.stringify({
        itemCount: items.length, delCount: dels.length, stats: stats,
        mode: (document.getElementById('memMode')||{}).textContent,
        firstText: first ? (first.querySelector('.mem-item-text')||{}).textContent : null,
        firstTag:  first ? (first.querySelector('.mem-tag')||{}).textContent : null,
        delLabel:  dels[0] ? dels[0].textContent : null,
        delHasKey: dels[0] ? !!dels[0].getAttribute('data-key') : false,
      });
    })()`, returnByValue: true });
    console.log('DOM 真值 =', ev.result.value);
    const shot = await cdp(ws, 'Page.captureScreenshot', { format: 'png' });
    const f = path.join(OUT, 'memory_ui.png');
    fs.writeFileSync(f, Buffer.from(shot.data, 'base64'));
    console.log('shot ->', f);
    ws.close();
  } catch (e) { console.error('失败:', e.message); process.exitCode = 1; }
  finally { clean(); process.exit(); }
})();
