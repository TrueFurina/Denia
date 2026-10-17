/* ============================================================
   _cdp_transparency_audit.cjs  ·  自适应透明度「覆盖度」审计（2026-10-13）
   ------------------------------------------------------------
   目的：拿到地面真值 —— 在有背景图（body.has-hero）下，各页关键
   卡片/面板的 computed background-color 到底是「实底」还是「半透明」，
   以及是否已消费 --tm-panel-boost。

   做法：python -m http.server 起静态服（root=src/ui/web）→ 无头 Edge
   CDP 打开每页 → 注入 has-hero + --tm-panel-boost → 读 computed 样式。
   用法：node tools/_cdp_transparency_audit.cjs [boost]
   ============================================================ */
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');

const NODE_PY = 'C:/Users/Administrator/AppData/Local/Programs/Python/Python312/python.exe';
const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
  .replace('/Microsoft/Edge', '/Microsoft/Edge'); // keep, resolved below
const EDGE_EXE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const WEBROOT = 'G:\\ai对话\\11\\src\\ui\\web';
const HTTP_PORT = 8891;
const CDP_PORT = 9333;
const BOOST = process.argv[2] || '0.00';

const PAGES = [
  ['daily', 'redesign/ui_redesign_daily.html'],
  ['calls', 'redesign/ui_redesign_calls.html'],
  ['characters', 'redesign/ui_redesign_characters.html'],
  ['memory', 'redesign/ui_redesign_memory.html'],
  ['knowledge', 'redesign/ui_redesign_knowledge.html'],
  ['lib', 'redesign/lib.html'],
  ['settings', 'redesign/ui_redesign_settings.html'],
  ['main', 'redesign/ui_redesign_main.html'],
];

const SELECTORS = [
  '.app', '.side', '.main', '.main > .top', '.main > .composer', '.stage',
  '.group', '.card', '.ccard', '.daycard', '.report', '.mstat', '.model-card',
  '.ov-card', '.ov-note', '.subnav',
  '.edit-card', '.archive-card', '.form input', '.form textarea', '.dl-bar',
  '.model-card .micon', '.mem-item', '.mem-row', '.mcard', '.kb-card', '.kcard',
  '.call-card', '.ccard2', '.rec', '.rec-item', '.list-item', '.panel',
];

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function cdp(ws, method, params) {
  const id = (cdp._id = (cdp._id || 0) + 1);
  return new Promise((resolve, reject) => {
    const to = setTimeout(() => reject(new Error('cdp timeout ' + method)), 20000);
    const onMsg = (ev) => {
      let m; try { m = JSON.parse(ev.data); } catch { return; }
      if (m.id === id) { clearTimeout(to); ws.removeEventListener('message', onMsg); m.error ? reject(new Error(method + ': ' + JSON.stringify(m.error))) : resolve(m.result); }
    };
    ws.addEventListener('message', onMsg);
    ws.send(JSON.stringify({ id, method, params: params || {} }));
  });
}

(async () => {
  // 1) static server
  const srv = spawn(NODE_PY, ['-m', 'http.server', String(HTTP_PORT), '--bind', '127.0.0.1'],
    { cwd: WEBROOT, stdio: 'ignore' });
  const prof = 'G:\\_tm_probe\\edge_tm_' + Date.now();   // ⚠️ 不落 C 盘（C 盘常满）
  // 2) headless edge
  const edge = spawn(EDGE_EXE, [
    '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--disable-extensions', '--mute-audio',
    '--remote-debugging-port=' + CDP_PORT, '--user-data-dir=' + prof, 'about:blank',
  ], { stdio: 'ignore' });

  const cleanup = () => { try { edge.kill(); } catch {} try { srv.kill(); } catch {} try { fs.rmSync(prof, { recursive: true, force: true }); } catch {} };
  process.on('exit', cleanup);

  try {
    let version = null;
    for (let i = 0; i < 40; i++) {
      try { version = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`)).json(); break; }
      catch { await sleep(250); }
    }
    if (!version) throw new Error('Edge CDP 未就绪');
    console.log('CDP ready:', version.Browser);

    const out = {};
    for (const [key, rel] of PAGES) {
      const tgt = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/new?about:blank`, { method: 'PUT' })).json();
      const ws = new WebSocket(tgt.webSocketDebuggerUrl);
      await new Promise((res, rej) => { ws.addEventListener('open', res); ws.addEventListener('error', rej); });
      await cdp(ws, 'Page.enable');
      await cdp(ws, 'Page.navigate', { url: `http://127.0.0.1:${HTTP_PORT}/${rel}` });
      await sleep(1400);
      const esc = process.env.WALL_ONLY === '1';
      const expr = `(() => {
        ${esc ? "document.body.setAttribute('data-tm-wall','on');" : "document.body.classList.add('has-hero');"}
        document.documentElement.style.setProperty('--tm-panel-boost', '${BOOST}');
        document.documentElement.style.setProperty('--tm-hero', 'url(/redesign/assets/theme_default_hero.png)');
        const body = getComputedStyle(document.body);
        const sels = ${JSON.stringify(SELECTORS)};
        const res = {};
        for (const s of sels) {
          let el = null;
          try { el = document.querySelector(s); } catch (e) { continue; }
          if (!el) continue;
          const cs = getComputedStyle(el);
          res[s] = { bg: cs.backgroundColor, bd: cs.borderColor, bf: (cs.backdropFilter || cs.webkitBackdropFilter || 'none') };
        }
        return JSON.stringify({
          panelBoost: document.documentElement.style.getPropertyValue('--tm-panel-boost'),
          surface: body.getPropertyValue('--surface').trim(),
          surface2: body.getPropertyValue('--surface-2').trim(),
          found: res,
        });
      })()`;
      const r = await cdp(ws, 'Runtime.evaluate', { expression: expr, returnByValue: true });
      out[key] = JSON.parse(r.result.value || '{}');
      ws.close();
    }
    console.log('\n===== 审计结果（boost=' + BOOST + '）=====');
    for (const [k, v] of Object.entries(out)) {
      console.log(`\n--- ${k} ---  --surface=${v.surface}  --surface-2=${v.surface2}  boost=${v.panelBoost}`);
      for (const [s, d] of Object.entries(v.found || {})) {
        const alpha = (d.bg.match(/rgba?\(([^)]+)\)/) || [])[1];
        const a = alpha ? (alpha.split(',').pop().trim()) : '?';
        const opaque = d.bg === 'rgb(0, 0, 0)' ? 'none/transparent' : (Number(a) === 1 ? 'OPAQUE' : `a=${a}`);
        console.log(`   ${s.padEnd(24)} ${d.bg.padEnd(30)} ${opaque}  bf=${d.bf.slice(0, 18)}`);
      }
    }
  } catch (e) {
    console.error('审计失败:', e.message);
    process.exitCode = 1;
  } finally {
    cleanup();
    process.exit();
  }
})();
