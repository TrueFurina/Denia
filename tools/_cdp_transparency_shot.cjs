/* 用真实主题图 + 真实 boost 渲染各页并截图（视觉真值）。
   用法：node tools/_cdp_transparency_shot.cjs [boost] [outdir] */
const { spawn } = require('child_process');
const path = require('path'), fs = require('fs'), os = require('os');
const PY = 'C:/Users/Administrator/AppData/Local/Programs/Python/Python312/python.exe';
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const WEBROOT = process.env.WEBROOT || 'G:\\ai对话\\11\\src\\ui\\web';
const HERO_SRC = 'G:\\ai对话\\11\\data\\char_banners\\themes\\default_b65751\\hero\\hero.jpg';
const HTTP_PORT = 8892, CDP_PORT = 9334;
const BOOST = process.argv[2] || '0.068';
const OUT = process.argv[3] || path.join(os.tmpdir(), 'tm_shots');
const THEME = process.argv[4] || 'light';
// veil 梯度（由 python 侧按同公式算好传入）—— 这里按 hero [60,24,32,80]*k
const K = parseFloat(process.argv[5] || '0.58');
const P = [60, 24, 32, 80].map(b => Math.max(8, Math.min(92, Math.round(b * K))));

const PAGES = [
  ['daily', 'redesign/ui_redesign_daily.html'],
  ['calls', 'redesign/ui_redesign_calls.html'],
  ['characters', 'redesign/ui_redesign_characters.html'],
  ['memory', 'redesign/ui_redesign_memory.html'],
  ['knowledge', 'redesign/lib.html'],
  ['settings', 'redesign/ui_redesign_settings.html'],
];
const sleep = ms => new Promise(r => setTimeout(r, ms));
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
  const heroDst = path.join(WEBROOT, 'redesign', '_tmp_hero.jpg');
  fs.copyFileSync(HERO_SRC, heroDst);
  const srv = spawn(PY, ['-m', 'http.server', String(HTTP_PORT), '--bind', '127.0.0.1'], { cwd: WEBROOT, stdio: 'ignore' });
  const prof = 'G:\\_tm_probe\\edge_sh_' + Date.now();   // ⚠️ 不落 C 盘（C 盘常满）
  const edge = spawn(EDGE, ['--headless=new', '--disable-gpu', '--no-first-run', '--disable-extensions',
    '--mute-audio', '--remote-debugging-port=' + CDP_PORT, '--user-data-dir=' + prof, 'about:blank'], { stdio: 'ignore' });
  const clean = () => { try { edge.kill(); } catch {} try { srv.kill(); } catch {} try { fs.rmSync(prof, { recursive: true, force: true }); } catch {} try { fs.unlinkSync(heroDst); } catch {} };
  process.on('exit', clean);
  try {
    let v = null;
    for (let i = 0; i < 40; i++) { try { v = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`)).json(); break; } catch { await sleep(250); } }
    if (!v) throw new Error('CDP 未就绪');
    const veil = `linear-gradient(180deg, color-mix(in srgb, var(--bg) ${P[0]}%, transparent) 0%,`
      + ` color-mix(in srgb, var(--bg) ${P[1]}%, transparent) 36%,`
      + ` color-mix(in srgb, var(--bg) ${P[2]}%, transparent) 64%,`
      + ` color-mix(in srgb, var(--bg) ${P[3]}%, transparent) 100%)`;
    for (const [key, rel] of PAGES) {
      const t = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/new?about:blank`, { method: 'PUT' })).json();
      const ws = new WebSocket(t.webSocketDebuggerUrl);
      await new Promise((r, j) => { ws.addEventListener('open', r); ws.addEventListener('error', j); });
      await cdp(ws, 'Page.enable');
      await cdp(ws, 'Emulation.setDeviceMetricsOverride', { width: 1090, height: 680, deviceScaleFactor: 1, mobile: false });
      await cdp(ws, 'Page.navigate', { url: `http://127.0.0.1:${HTTP_PORT}/${rel}` });
      await sleep(1500);
      await cdp(ws, 'Runtime.evaluate', { expression: `(() => {
        document.documentElement.setAttribute('data-theme','${THEME}');
        document.body.classList.add('has-hero');
        const r = document.documentElement.style;
        r.setProperty('--tm-hero','url(/redesign/_tmp_hero.jpg)');
        r.setProperty('--tm-hero-veil', ${JSON.stringify(veil)});
        r.setProperty('--tm-panel-boost','${BOOST}');
        document.querySelectorAll('.skeleton,.empty-card').forEach(e=>e.remove());
        return 'ok';
      })()` });
      await sleep(600);
      const shot = await cdp(ws, 'Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
      const f = path.join(OUT, `${key}.png`);
      fs.writeFileSync(f, Buffer.from(shot.data, 'base64'));
      console.log('shot ->', f);
      ws.close();
    }
    console.log('\nOUTDIR=' + OUT + '  BOOST=' + BOOST + ' THEME=' + THEME + ' VEIL=' + JSON.stringify(P));
  } catch (e) { console.error('失败:', e.message); process.exitCode = 1; }
  finally { clean(); process.exit(); }
})();
