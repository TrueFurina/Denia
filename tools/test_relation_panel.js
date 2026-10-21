/* 关系面板两个新入口的行为真值表（2026-10-21）：原型下拉 + 最近事件列表。
 *
 * 为什么这么绕：判据是"界面变成什么样"，静态查源码只能证明字符串在不在。本仓规矩是
 * **把真值表交给 node 跑页面里的真 JS** —— 这里**原样加载** `relation_panel.js`
 * （整文件，不抽片段）在一个假 DOM + 假 pywebview 的沙箱里跑，断言 DOM 与调用次数。
 * 所以面板代码一改，这里立刻跟着变，不会出现"测试里另有一份实现"。
 *
 * 六个场景（含两个对照组）：
 *   S1 正常：原型下拉的选项来自 state.archetypes；事件列表按"最近的在上"渲染
 *   S2 改下拉 → 调 set_archetype_manual('', '傲娇') 恰好一次，参数正确
 *   S3 **对照组**：老后端没有 archetypes 字段 → 禁用 + 说明，**不许**写死一份枚举兜底
 *   S4 事件为空 → 整块隐藏，不留空行
 *   S5 **对照组**：后端没连上（没有 pywebview）→ 不抛异常、不把下拉变成空壳
 *   S6 detail 里带尖括号 → 走 textContent，不许当 HTML 插进去
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const SRC_PATH = path.join(ROOT, 'src', 'ui', 'web', 'redesign', 'relation_panel.js');

let ok = 0, bad = 0;
function chk(name, cond, extra) {
  if (cond) { ok++; console.log('  PASS ' + name + (extra ? '  ' + extra : '')); }
  else { bad++; console.log('  FAIL ' + name + (extra ? '  ' + extra : '')); }
}

/* ---------------- 假 DOM ---------------- */
function mkEl(tag, id) {
  const el = {
    tagName: String(tag || 'div').toUpperCase(),
    id: id || '', className: '', title: '', value: '', textContent: '',
    style: {}, dataset: {}, disabled: false, attrs: {}, _children: [],
    _on: {}, _htmlSet: 0,
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); },
      remove(c) { this._s.delete(c); },
      contains(c) { return this._s.has(c); },
      toggle(c, on) {
        const v = (on === undefined) ? !this._s.has(c) : !!on;
        if (v) this._s.add(c); else this._s.delete(c);
        return v;
      }
    },
    appendChild(c) { el._children.push(c); return c; },
    removeChild(c) {
      const i = el._children.indexOf(c);
      if (i >= 0) el._children.splice(i, 1);
      return c;
    },
    addEventListener(ev, fn) { el._on[ev] = fn; },
    setAttribute(k, v) { el.attrs[k] = v; },
    getAttribute(k) { return el.attrs[k]; }
  };
  Object.defineProperty(el, 'firstChild', { get() { return el._children[0] || null; } });
  Object.defineProperty(el, 'children', { get() { return el._children; } });
  Object.defineProperty(el, 'innerHTML', {
    get() { return ''; },
    set(v) { el._htmlSet++; if (!v) el._children.length = 0; }
  });
  return el;
}

const IDS = ['relMoodChip', 'relStage', 'relArch', 'relArchTag', 'relProgress', 'relCardText',
  'relProactiveBtn', 'relPace', 'relFreq', 'relLast', 'relEvent', 'relEventList',
  'relArchSel', 'relResetBtn', 'relFoldBtn', 'relBody'];

function mkDoc() {
  const els = {};
  IDS.forEach((id) => { els[id] = mkEl(id === 'relArchSel' ? 'select' : 'div', id); });
  /* 页面里那个占位 option（"读取中…"）：真页面有，这里也放一个，
     才能验证"老后端时不许把它换成一串枚举"。 */
  const ph = mkEl('option');
  ph.value = '';
  ph.textContent = '读取中…';
  els.relArchSel.appendChild(ph);

  const body = mkEl('body', 'body');
  const panel = mkEl('div', 'panel');
  const doc = {
    body,
    querySelector(sel) {
      if (sel === '.stage .panel') return panel;
      if (sel && sel[0] === '#') return els[sel.slice(1)] || null;
      return null;
    },
    createElement(tag) { return mkEl(tag); }
  };
  return { doc, els, panel };
}

function runScenario(opts) {
  const { doc, els } = mkDoc();
  const calls = { setArchetype: [], getEvents: [] };

  const apiObj = {};
  if (!opts.offline) {
    apiObj.get_relationship_state = () => Promise.resolve(opts.state);
    apiObj.set_relationship_setting = () => Promise.resolve({ success: true });
    apiObj.reset_relationship = () => Promise.resolve({ success: true });
    apiObj.get_relation_events = (cid, limit) => {
      calls.getEvents.push([cid, limit]);
      return Promise.resolve(opts.events);
    };
    apiObj.set_archetype_manual = (cid, arch) => {
      calls.setArchetype.push([cid, arch]);
      /* 模拟后端真的写进去了：下一次 state 拉回来就是这个值 */
      if (opts.state) { opts.state.archetype = arch; opts.state.archetype_inferred = false; }
      return Promise.resolve({ success: true, archetype: arch });
    };
  }

  const ctx = {
    console,
    Date, Math, JSON, Number, String, Boolean, Array, Object, isNaN, parseInt, parseFloat,
    setTimeout: () => 0,
    setInterval: () => 0,
    clearInterval: () => {},
    clearTimeout: () => {},
    confirm: () => true,
    localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    document: doc
  };
  ctx.window = ctx;
  ctx.window.matchMedia = () => ({ matches: false });
  ctx.window.pywebview = opts.offline ? undefined : { api: apiObj };

  const src = fs.readFileSync(SRC_PATH, 'utf8');
  vm.runInNewContext(src, ctx, { filename: 'relation_panel.js' });
  return { ctx, els, calls };
}

const tick = () => new Promise((r) => setImmediate(r));

async function main() {
  const STATE = {
    success: true, mood: '想你', stage: '熟识', progress: '有点熟',
    archetype: '温柔', archetype_inferred: true,
    archetypes: ['黏人', '傲娇', '温柔', '毒舌', '高冷', '元气'],
    settings: { proactive: 'on', pace: '标准', frequency: 'normal' },
    last_interaction: '3 分钟前', last_event: '[turn] 聊了两句'
  };
  const EVENTS = {
    success: true,
    events: [
      { ts: 1761000000, kind: 'turn', detail: '聊了两句' },
      { ts: 1761000060, kind: 'mood', detail: '心情 → 想你' },
      { ts: 1761000120, kind: 'proactive', detail: '她主动开了口' }
    ]
  };

  /* ---------- S1 正常 ---------- */
  console.log('[S1] 原型下拉 + 事件列表都按后端数据渲染');
  {
    const s = runScenario({ state: JSON.parse(JSON.stringify(STATE)), events: EVENTS });
    s.ctx.window.initRelationPanel();
    await tick(); await tick();
    const sel = s.els.relArchSel;
    chk('下拉有 6 个选项（来自 state.archetypes）', sel.children.length === 6, sel.children.length);
    chk('选项值就是后端给的名字',
      sel.children.map((o) => o.value).join(',') === '黏人,傲娇,温柔,毒舌,高冷,元气',
      sel.children.map((o) => o.value).join(','));
    chk('当前值选中「温柔」', sel.value === '温柔', sel.value);
    chk('下拉没有被禁用', sel.disabled === false);
    const list = s.els.relEventList;
    chk('事件列表渲染 3 行', list.children.length === 3, list.children.length);
    chk('最近的在上（第一行是最后一条）',
      list.children.length > 0 && list.children[0].textContent.indexOf('她主动开了口') >= 0,
      list.children[0] && list.children[0].textContent);
    chk('事件列表显示出来了', list.style.display !== 'none', list.style.display);
    chk('拉事件时带了 limit=6', s.calls.getEvents.length > 0 && s.calls.getEvents[0][1] === 6,
      JSON.stringify(s.calls.getEvents[0] || null));
    chk('「·自动」标签在（推断来的值）', s.els.relArchTag.textContent === '·自动',
      s.els.relArchTag.textContent);
  }

  /* ---------- S2 改下拉 ---------- */
  console.log('[S2] 改下拉 → 调 set_archetype_manual，且「·自动」标签消失');
  {
    const s = runScenario({ state: JSON.parse(JSON.stringify(STATE)), events: EVENTS });
    s.ctx.window.initRelationPanel();
    await tick(); await tick();
    const sel = s.els.relArchSel;
    sel.value = '傲娇';
    await sel._on.change();
    await tick(); await tick();
    chk('调了一次 set_archetype_manual', s.calls.setArchetype.length === 1,
      JSON.stringify(s.calls.setArchetype));
    chk('参数是 ("", "傲娇")',
      s.calls.setArchetype[0] && s.calls.setArchetype[0][0] === '' && s.calls.setArchetype[0][1] === '傲娇',
      JSON.stringify(s.calls.setArchetype[0] || null));
    chk('改完下拉停在「傲娇」', sel.value === '傲娇', sel.value);
    chk('「·自动」标签消失了', s.els.relArchTag.textContent === '', s.els.relArchTag.textContent);
  }

  /* ---------- S3 对照组：老后端没有 archetypes ---------- */
  console.log('[S3] 对照组：老后端没有 archetypes → 禁用 + 说明，不许写死枚举兜底');
  {
    const st = JSON.parse(JSON.stringify(STATE));
    delete st.archetypes;
    const s = runScenario({ state: st, events: EVENTS });
    s.ctx.window.initRelationPanel();
    await tick(); await tick();
    const sel = s.els.relArchSel;
    chk('下拉被禁用', sel.disabled === true);
    chk('title 说明了原因', String(sel.title).indexOf('没有提供原型列表') >= 0, sel.title);
    chk('**没有**塞进一串写死的枚举', sel.children.length === 1,
      sel.children.map((o) => o.value).join(','));
  }

  /* ---------- S4 事件为空 ---------- */
  console.log('[S4] 没有事件 → 整块隐藏，不留空行');
  {
    const s = runScenario({ state: JSON.parse(JSON.stringify(STATE)), events: { success: true, events: [] } });
    s.ctx.window.initRelationPanel();
    await tick(); await tick();
    chk('列表 0 行', s.els.relEventList.children.length === 0, s.els.relEventList.children.length);
    chk('整块 display:none', s.els.relEventList.style.display === 'none', s.els.relEventList.style.display);
  }

  /* ---------- S5 对照组：后端没连上 ---------- */
  console.log('[S5] 对照组：没有 pywebview → 不抛异常，也不把下拉变成空壳');
  {
    const s = runScenario({ offline: true });
    let threw = null;
    try {
      s.ctx.window.initRelationPanel();
      await tick(); await tick();
    } catch (e) { threw = e; }
    chk('没有抛异常', threw === null, threw && String(threw));
    chk('下拉保持占位（没被清空成空壳）', s.els.relArchSel.children.length === 1,
      s.els.relArchSel.children.length);
    chk('下拉没有被误判成"老后端"而禁用', s.els.relArchSel.disabled === false);
  }

  /* ---------- S6 detail 里带尖括号 ---------- */
  console.log('[S6] detail 带尖括号 → 走 textContent，不许当 HTML 插');
  {
    const ev = {
      success: true,
      events: [{ ts: 1761000200, kind: 'turn', detail: '<img src=x onerror=alert(1)>' }]
    };
    const s = runScenario({ state: JSON.parse(JSON.stringify(STATE)), events: ev });
    s.ctx.window.initRelationPanel();
    await tick(); await tick();
    const row = s.els.relEventList.children[0];
    chk('渲染了一行', !!row);
    chk('文本里是原样的尖括号（说明走的是 textContent）',
      !!row && row.textContent.indexOf('<img') >= 0, row && row.textContent);
    chk('**没有**往 innerHTML 里写', !!row && row._htmlSet === 0, row && row._htmlSet);
  }

  console.log('\n结果: ' + ok + ' PASS / ' + bad + ' FAIL');
  return bad === 0 ? 0 : 1;
}

main().then((c) => process.exit(c)).catch((e) => {
  console.log('  FAIL 测试自己崩了：' + (e && e.stack || e));
  process.exit(1);
});
