/* model_downloader.js 行为门测（2026-10-13，Task #27）
 * 真实加载 src/ui/web/redesign/model_downloader.js 进 jsdom，mock window.Backend：
 *  1. mountInto 后接管后台在跑的任务（进度不归零、轮询启动）
 *  2. 切到有断点残留的档 → 主按钮变「继续下载（62%）」+ 断点保留标签
 *  3. 别档在跑 → 出现横幅，横幅「取消」调 stopModelDownload(那个任务, false)
 *  4. 已安装档行内有卸载按钮；未安装档没有；点击走 deleteModelQuant(正确参数)
 *  5. 接管态点「取消」→ stopModelDownload(自己的任务, false)
 *  6. autoMini：小胶囊显示真实百分比；任务 done → 完成气泡 + toast
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const SRC = path.join(__dirname, '..', 'src', 'ui', 'web', 'redesign', 'model_downloader.js');
const src = fs.readFileSync(SRC, 'utf-8');

const PASS = [], FAIL = [];
function chk(name, cond, detail) {
  (cond ? PASS : FAIL).push(name);
  console.log((cond ? 'PASS ' : 'FAIL ') + name + (detail !== undefined ? ' | ' + detail : ''));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const CATALOG = {
  success: true, vram_gb: 16, model_dir: '/models',
  quants: [
    { id: 'Q4_K_M', file: 'q4.gguf', size_gb: 4.8, vram_gb: 7, available: true, recommended: true, quality: '流畅', note: '' },
    { id: 'Q6_K', file: 'q6.gguf', size_gb: 6.72, vram_gb: 9, available: true, quality: '均衡', note: '' },
    { id: 'Q8_0', file: 'q8.gguf', size_gb: 8.5, vram_gb: 11, available: false, quality: '高保真', note: '' },
  ],
};

function makeMock(state) {
  const calls = { stop: [], del: [], toast: [], download: [] };
  const B = {
    ready: () => true,
    getModelCatalog: () => Promise.resolve(CATALOG),
    modelDirInfo: () => Promise.resolve({ success: true, free_gb: 100, installed: [{ id: 'Q6_K' }] }),
    pickModelDir: () => Promise.resolve({ success: true, path: '/models' }),
    downloadModelQuant: (d) => { calls.download.push(d); return Promise.resolve({ success: true, task_id: 'dl_new_' + Date.now() }); },
    modelQuantStatus: (tid) => Promise.resolve(state.status[tid] || { success: true, status: 'running', overall_pct: 37, speed_mbps: 5, eta_s: 120, log: '下载中' }),
    stopModelDownload: (tid, keep) => { calls.stop.push([tid, keep]); return Promise.resolve({ success: true }); },
    deleteModelQuant: (d) => { calls.del.push(d); return Promise.resolve({ success: true, freed_gb: 6.72 }); },
    listModelDownloads: (d) => Promise.resolve(state.overview(d)),
    toast: (m, k) => { calls.toast.push([m, k]); },
  };
  return { B, calls };
}

function boot(state) {
  const vc = [];
  const dom = new JSDOM('<!DOCTYPE html><html><body><div id="host"></div></body></html>', {
    runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/',
    virtualConsole: undefined,
  });
  const { window } = dom;
  const errs = [];
  window.addEventListener('error', (e) => errs.push(String(e.error || e.message)));
  const origErr = console.error;
  window.confirm = () => true;
  const { B, calls } = makeMock(state);
  window.Backend = B;
  window.eval(src);
  return { window, document: window.document, calls, errs, dom };
}

function click(window, el) {
  el.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
}

async function main() {
  /* ---------- 场景 1：接管后台任务 ---------- */
  {
    const state = {
      status: { dl_q4: { success: true, status: 'running', overall_pct: 37, speed_mbps: 5.2, eta_s: 240, log: '下载中：q4.gguf' } },
      overview: () => ({
        success: true,
        tasks: [{ quant_id: 'Q4_K_M', model_dir: '/models', task_id: 'dl_q4', kind: 'quant', status: 'running', overall_pct: 37, speed_mbps: 5.2, eta_s: 240, log: '下载中' }],
        partials: [],
      }),
    };
    const { window, document, calls, errs } = boot(state);
    window.ModelDownloader.mountInto(document.getElementById('host'));
    await sleep(60);
    const pct = document.getElementById('mdlPct').textContent;
    chk('1.1 接管后显示真实进度 37%', pct === '37%', pct);
    chk('1.2 运行中主按钮禁用', document.getElementById('mdlMain').disabled === true);
    chk('1.3 取消按钮可用（不失联）', document.getElementById('mdlCancel').disabled === false);
    chk('1.4 无别档任务时横幅隐藏', document.getElementById('mdlOther').style.display === 'none');
    chk('1.5 日志显示后端真实日志', /下载中/.test(document.getElementById('mdlLog').textContent));
    chk('1.6 无 JS 错误', errs.length === 0, errs.join(';'));

    /* 场景 5：接管态点取消 */
    click(window, document.getElementById('mdlCancel'));
    await sleep(30);
    chk('5.1 取消调用 stopModelDownload(自己任务, false)',
      calls.stop.length === 1 && calls.stop[0][0] === 'dl_q4' && calls.stop[0][1] === false,
      JSON.stringify(calls.stop));
    window.ModelDownloader.close();
  }

  /* ---------- 场景 2+3+4：断点 / 别档横幅 / 行内卸载 ---------- */
  {
    let curQuant = '';
    const state = {
      status: {},
      overview: (d) => {
        if (curQuant === 'Q6_K') {
          return {
            success: true,
            tasks: [{ quant_id: 'Q4_K_M', model_dir: '/models', task_id: 'dl_q4b', kind: 'quant', status: 'running', overall_pct: 71, speed_mbps: 3.1, eta_s: 90, log: '下载中' }],
            partials: [{ quant_id: 'Q6_K', model_dir: '/models', pct: 62, partial_gb: 4.1, size_gb: 6.72 }],
          };
        }
        return { success: true, tasks: [], partials: [] };
      },
    };
    const { window, document, calls, errs } = boot(state);
    window.ModelDownloader.mountInto(document.getElementById('host'));
    await sleep(60);

    /* 场景 4：行内卸载按钮（Q6 已安装 → 有；Q4 未安装 → 无） */
    const rows = document.querySelectorAll('.mdl-qrow');
    const q4row = Array.from(rows).find((r) => r.querySelector('[data-q="Q4_K_M"]'));
    const q6row = Array.from(rows).find((r) => r.querySelector('[data-q="Q6_K"]'));
    chk('4.1 未安装档没有行内卸载按钮', q4row && !q4row.querySelector('.mdl-qdel'));
    chk('4.2 已安装档有行内卸载按钮', q6row && !!q6row.querySelector('.mdl-qdel'));
    if (q6row) {
      click(window, q6row.querySelector('.mdl-qdel'));
      await sleep(30);
      chk('4.3 点击卸载走 deleteModelQuant(正确档位+目录)',
        calls.del.length === 1 && calls.del[0].quant_id === 'Q6_K' && calls.del[0].model_dir === '/models',
        JSON.stringify(calls.del));
      chk('4.4 卸载后 toast 提示', calls.toast.some((t) => /卸载/.test(t[0])), JSON.stringify(calls.toast));
    }

    /* 场景 2：切到 Q6 → 断点保留（注意：卸载后 refreshDir 重建了列表，必须重新查询节点） */
    curQuant = 'Q6_K';
    const q6row2 = Array.from(document.querySelectorAll('.mdl-qrow'))
      .find((r) => r.querySelector('[data-q="Q6_K"]'));
    click(window, q6row2.querySelector('.mdl-q'));
    await sleep(60);
    const mainBtn = document.getElementById('mdlMain');
    chk('2.1 断点档主按钮=继续下载（62%）', /继续下载（62%）/.test(mainBtn.textContent), mainBtn.textContent);
    chk('2.2 断点档主按钮可点', mainBtn.disabled === false);
    chk('2.3 状态标签=断点保留', /断点保留/.test(document.getElementById('mdlState').textContent));
    chk('2.4 副文案说明保留体积', /4\.1 GB/.test(document.getElementById('mdlSub').textContent), document.getElementById('mdlSub').textContent);

    /* 场景 3：别档在跑 → 横幅 + 取消 */
    const ob = document.getElementById('mdlOther');
    chk('3.1 别档任务横幅显示', ob.style.display === 'flex', ob.style.display);
    chk('3.2 横幅文案含别档档位与进度', /Q4_K_M/.test(document.getElementById('mdlOtherT').textContent) && /71%/.test(document.getElementById('mdlOtherT').textContent));
    click(window, document.getElementById('mdlOtherCancel'));
    await sleep(30);
    chk('3.3 横幅取消调 stopModelDownload(别档任务, false)',
      calls.stop.length === 1 && calls.stop[0][0] === 'dl_q4b' && calls.stop[0][1] === false,
      JSON.stringify(calls.stop));
    chk('3.4 无 JS 错误', errs.length === 0, errs.join(';'));
    window.ModelDownloader.close();
  }

  /* ---------- 场景 6：autoMini 小胶囊 + 完成气泡 ---------- */
  {
    let phase = 'running';
    const state = {
      status: { dl_q4c: { success: true, status: phase, overall_pct: 37, speed_mbps: 5, eta_s: 120, log: 'x' } },
      overview: () => phase === 'running'
        ? { success: true, tasks: [{ quant_id: 'Q4_K_M', model_dir: '/models', task_id: 'dl_q4c', kind: 'quant', status: 'running', overall_pct: 37, speed_mbps: 5, eta_s: 120, log: 'x' }], partials: [] }
        : { success: true, tasks: [{ quant_id: 'Q4_K_M', model_dir: '/models', task_id: 'dl_q4c', kind: 'quant', status: 'done', overall_pct: 100, speed_mbps: 0, eta_s: null, log: 'done' }], partials: [] },
    };
    const { window, document, calls, errs } = boot(state);
    window.ModelDownloader.autoMini();
    await sleep(80);
    const pill = document.getElementById('mdlMiniPill');
    chk('6.1 小胶囊出现', !!pill);
    chk('6.2 小胶囊显示真实进度 37%', pill && /37%/.test(pill.textContent), pill && pill.textContent);
    chk('6.3 小胶囊显示档位', pill && /Q4_K_M/.test(pill.textContent));
    /* × 收起 */
    click(window, pill.querySelector('.mx'));
    await sleep(20);
    chk('6.4 点 × 收起小胶囊', !document.getElementById('mdlMiniPill'));
    /* 手动收起后同任务不再蹦出；换 done 由另一窗口验气泡（这里直接验气泡逻辑） */
    window.ModelDownloader.close();
  }

  /* ---------- 场景 6b：完成气泡（独立窗口，避免 6.4 收起干扰） ---------- */
  {
    let phase = 'running';
    const state = {
      status: {},
      overview: () => phase === 'running'
        ? { success: true, tasks: [{ quant_id: 'Q4_K_M', model_dir: '/models', task_id: 'dl_q4d', kind: 'quant', status: 'running', overall_pct: 37, speed_mbps: 5, eta_s: 120, log: 'x' }], partials: [] }
        : { success: true, tasks: [{ quant_id: 'Q4_K_M', model_dir: '/models', task_id: 'dl_q4d', kind: 'quant', status: 'done', overall_pct: 100, speed_mbps: 0, eta_s: null, log: 'done' }], partials: [] },
    };
    const { window, document, calls, errs } = boot(state);
    window.ModelDownloader.autoMini();
    await sleep(80);
    chk('6b.1 首屏小胶囊在', !!document.getElementById('mdlMiniPill'));
    phase = 'done';
    await sleep(3300);   // 等第二个轮询 tick（3s）
    const bubble = document.getElementById('mdlMiniBubble');
    chk('6b.2 下载完成弹出气泡', !!bubble);
    chk('6b.3 气泡文案=完成+档位', bubble && /下载完成/.test(bubble.textContent) && /Q4_K_M/.test(bubble.textContent), bubble && bubble.textContent);
    chk('6b.4 气泡有「查看」按钮', bubble && !!bubble.querySelector('#mdlMiniGo'));
    chk('6b.5 完成同时 toast 兜底', calls.toast.some((t) => /下载完成/.test(t[0])), JSON.stringify(calls.toast));
    chk('6b.6 无 JS 错误', errs.length === 0, errs.join(';'));
    window.ModelDownloader.close();
  }

  console.log('\n== 结果：%d PASS / %d FAIL ==', PASS.length, FAIL.length);
  if (FAIL.length) { console.log('失败项：' + FAIL.join('、')); process.exitCode = 1; }
}

main().catch((e) => { console.error('FATAL', e); process.exitCode = 1; });
