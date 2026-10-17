/* 通话记录页 UI 真机渲染（mock 后端喂 call_records.json 聚合会话 + 主题头像）
   用途：验证伪造记录列表 + .sess-av 头像占位是否已被真图替换。
   用法：node tools/_cdp_calls_shot.cjs [outdir] */
const { spawn } = require('child_process');
const path = require('path'), fs = require('fs');
const PY = 'C:/Users/Administrator/AppData/Local/Programs/Python/Python312/python.exe';
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const ROOT = 'G:\\ai对话\\11';
const WEBROOT = path.join(ROOT, 'src', 'ui', 'web');
const CALLJSON = path.join(ROOT, 'call_records.json');
const AVSRC = path.join(ROOT, 'data', 'char_banners', 'themes', 'default_b65751', 'avatar', 'avatar.png');
const HTTP_PORT = 8897, CDP_PORT = 9337;
const OUT = process.argv[2] || path.join(ROOT, 'docs', '_shots_calls');
const sleep = ms => new Promise(r => setTimeout(r, ms));

// ---- 聚合会话（与后端 _build_call_sessions 同规则）----
const rows = JSON.parse(fs.readFileSync(CALLJSON, 'utf-8')).sort((a, b) => a.ts - b.ts);
const groups = new Map();
rows.forEach(r => {
  const k = (r.session_id || '').trim() || ('c' + Math.floor(r.ts));
  if (!groups.has(k)) groups.set(k, []);
  groups.get(k).push(r);
});
const mode = arr => { const m = {}; let best = '', bc = 0; arr.forEach(x => { m[x] = (m[x] || 0) + 1; if (m[x] > bc) { bc = m[x]; best = x; } }); return best; };
const sessions = [...groups.values()].map(grp => {
  const start = grp[0].ts, end = grp[grp.length - 1].ts;
  const users = grp.filter(r => r.speaker === 'user');
  const topicSrc = users.find(r => r.text) || grp.find(r => r.text);
  let topic = (topicSrc ? topicSrc.text : '通话记录').trim().replace(/\n/g, ' ');
  if (topic.length > 24) topic = topic.slice(0, 24) + '…';
  return {
    id: (grp[0].session_id || ('c' + Math.floor(start))),
    start_ts: start, end_ts: end, char_name: '达妮娅', topic,
    engine: mode(grp.map(r => r.engine).filter(Boolean)),
    count: grp.length, n_user: users.length, n_assistant: grp.length - users.length,
    has_audio: grp.some(r => r.audio_url), duration_s: Math.max(0, Math.floor(end - start)),
  };
}).sort((a, b) => b.start_ts - a.start_ts);
const MOCK = { success: true, sessions, in_call: false, active_session_id: null };

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
  const avDst = path.join(WEBROOT, 'redesign', '_tmp_av.png');
  fs.copyFileSync(AVSRC, avDst);
  const srv = spawn(PY, ['-m', 'http.server', String(HTTP_PORT), '--bind', '127.0.0.1'], { cwd: WEBROOT, stdio: 'ignore' });
  const prof = 'G:\\_tm_probe\\edge_calls_' + Date.now();
  const edge = spawn(EDGE, ['--headless=new', '--disable-gpu', '--no-first-run', '--disable-extensions',
    '--mute-audio', '--remote-debugging-port=' + CDP_PORT, '--user-data-dir=' + prof, 'about:blank'], { stdio: 'ignore' });
  const clean = () => { try { edge.kill(); } catch {} try { srv.kill(); } catch {} try { fs.rmSync(prof, { recursive: true, force: true }); } catch {} try { fs.unlinkSync(avDst); } catch {} };
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
    await cdp(ws, 'Page.addScriptToEvaluateOnNewDocument', {
      source: `window.pywebview = { api: { get_call_sessions: function(){ return Promise.resolve(${JSON.stringify(MOCK)}); } } };`,
    });
    await cdp(ws, 'Emulation.setDeviceMetricsOverride', { width: 1090, height: 760, deviceScaleFactor: 1, mobile: false });
    await cdp(ws, 'Page.navigate', { url: `http://127.0.0.1:${HTTP_PORT}/redesign/ui_redesign_calls.html` });
    await sleep(2600);
    // 应用主题头像（真实链路里由 theme_manager 写入 root 的 --tm-avatar）
    await cdp(ws, 'Runtime.evaluate', { expression: `(() => {
      document.documentElement.style.setProperty('--tm-avatar','url("/redesign/_tmp_av.png")');
      document.documentElement.setAttribute('data-tm-avatar','on');
      if (typeof applySessAvatars === 'function') applySessAvatars();
      return 'ok';
    })()` });
    await sleep(700);
    const diag = await cdp(ws, 'Runtime.evaluate', { expression: `(() => {
      const v = getComputedStyle(document.documentElement).getPropertyValue('--tm-avatar');
      const el = document.querySelector('.sess-av');
      return JSON.stringify({ tmAvatar: String(v).slice(0,50),
        bg: el ? getComputedStyle(el).backgroundImage.slice(0,70) : null });
    })()`, returnByValue: true });
    console.log('DIAG =', diag.result.value);
    const ev = await cdp(ws, 'Runtime.evaluate', { expression: `(() => {
      const cards = document.querySelectorAll('.session-card');
      const avs = document.querySelectorAll('.sess-av');
      const withImg = [...avs].filter(e => /_tmp_av\\.png/.test(getComputedStyle(e).backgroundImage));
      const first = document.querySelector('.session-card');
      return JSON.stringify({
        cards: cards.length, avatars: avs.length, avatarsWithImg: withImg.length,
        countText: (document.getElementById('callCount')||{}).textContent,
        firstTopic: first ? (first.querySelector('.sess-topic')||{}).textContent : null,
        firstMeta: first ? (first.querySelector('.sess-meta')||{}).textContent : null,
        firstBadges: first ? [...first.querySelectorAll('.sess-badge')].map(e=>e.textContent.trim()) : [],
        bg: withImg[0] ? getComputedStyle(withImg[0]).backgroundImage.slice(0,60) : null,
      });
    })()`, returnByValue: true });
    console.log('DOM 真值 =', ev.result.value);
    const shot = await cdp(ws, 'Page.captureScreenshot', { format: 'png' });
    const f = path.join(OUT, 'calls_ui.png');
    fs.writeFileSync(f, Buffer.from(shot.data, 'base64'));
    console.log('shot ->', f);
    ws.close();
  } catch (e) { console.error('失败:', e.message); process.exitCode = 1; }
  finally { clean(); process.exit(); }
})();
