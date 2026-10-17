/* 感知活动来源标注 · 前端门测（Task #28）
 *
 * 不做"复制一份代码测"——直接从线上文件 ui_redesign_developer.html 里
 * 抽取 renderAnalyzer / visSrcChip / renderTimeline 真实源码eval，
 * 配 jsdom DOM + 桩（esc/fmtClock/kv/elAnalyzer/elTl），验证：
 *   1. 云端 VLM：视觉事件出「云端 VLM」芯片（src-cloud）
 *   2. 逐事件戳优先：事件 source 是本地、总链路是云端 → 标「本地 VLM」（兜底后不说谎）
 *   3. 事件无 source（旧事件）→ 回退 analyzer.vision_source
 *   4. 老后端完全没有 vision_source → 裸「视觉」签，不崩
 *   5. OCR：source=ocr →「OCR」+「本地」；ocr+input →「OCR+输入」（真实来源值）
 *   6. 音效 →「音效」+「本地」
 *   7. 空态文案
 *   8. renderAnalyzer「看屏链路」行：云端/本地/未绑定/未知 四种态
 *   9. 时间线卡片有来源说明静态文案
 *   10. pages.css 有 src-cloud/src-local 芯片样式
 *   11. 死分支 ocr+vision 已从 renderTimeline 消失（v2 起视觉不参与时刻裁决）
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const ROOT = path.resolve(__dirname, '..');
const HTML = fs.readFileSync(
  path.join(ROOT, 'src/ui/web/redesign/ui_redesign_developer.html'), 'utf8');
const CSS = fs.readFileSync(
  path.join(ROOT, 'src/ui/web/redesign/pages.css'), 'utf8');

const REPORT = path.join(ROOT, 'docs/_test_vision_source_ui.txt');
const lines = [];
let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; lines.push(`  PASS ${name}${detail ? ' | ' + detail : ''}`); }
  else { fail++; lines.push(`  FAIL ${name}${detail ? ' | ' + detail : ''}`); }
}

/* ---- 从线上文件抽取真实源码块 ---- */
const anaStart = HTML.indexOf('/* ---- 视觉分析（模型识别屏幕的概述） ---- */');
const anaEnd = HTML.indexOf('/* ---- 前台监听 ---- */');
const tlStart = HTML.indexOf('/* ---- 感知活动时间线');
const tlEnd = HTML.indexOf('function renderDuplexOut');
if (anaStart < 0 || anaEnd < 0 || tlStart < 0 || tlEnd < 0) {
  console.error('抽取失败：标记没找到', { anaStart, anaEnd, tlStart, tlEnd });
  process.exit(2);
}
const anaBlock = HTML.slice(anaStart, anaEnd);
const tlBlock = HTML.slice(tlStart, tlEnd);

const dom = new JSDOM('<!doctype html><html><body>' +
  '<div id="dbgAnalyzer"></div><div id="dbgTimeline"></div></body></html>');
const document = dom.window.document;

const stub = `
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmtClock = ts => new Date((ts || 0) * 1000).toISOString().slice(11, 19);
const fmtAgo = (ts, now) => Math.max(0, Math.round((now || 0) - (ts || 0))) + 's前';
const kv = (k, v, cls) => '<div class="dbg-kv ' + (cls || '') +
  '"><span class="k">' + k + '</span><span class="v">' + v + '</span></div>';
let elAnalyzer = document.getElementById('dbgAnalyzer');
let elTl = document.getElementById('dbgTimeline');
`;

const factory = new Function('document', stub + anaBlock + '\n' + tlBlock + `
return { renderAnalyzer, renderTimeline, visSrcChip };
`);
const api = factory(document);

const NOW = 1780000000;
const CLOUD_SRC = { provider: 'openai', model: 'glm-4v-flash',
  label: '智谱 GLM-4V-Flash（云端免费）', local: false,
  active_id: 'glm-4v-flash', last_used_id: 'glm-4v-flash' };
const LOCAL_SRC = { provider: 'ollama', model: 'openbmb/minicpm-o2.6:q4_K_M',
  label: '本地 MiniCPM-o 2.6', local: true,
  active_id: 'glm-4v-flash', last_used_id: 'minicpm-o2.6' };

lines.push('==== 感知活动来源标注 · 前端门测（jsdom，真源码抽取）====');

/* 1 云端视觉 */
api.renderTimeline({ ts: NOW, analyzer: { vision_source: CLOUD_SRC, recent: [
  { ts: NOW - 5, summary: '在写代码', source: CLOUD_SRC }] } });
let tl = document.getElementById('dbgTimeline').innerHTML;
check('1 云端事件出「云端 VLM」芯片', tl.includes('src-cloud') && tl.includes('云端 VLM'), tl.slice(0, 120));
check('1b 类型签仍是「视觉」', tl.includes('>视觉</span>'));

/* 2 逐事件戳优先：事件本地、总链路云端 */
api.renderTimeline({ ts: NOW, analyzer: { vision_source: CLOUD_SRC, recent: [
  { ts: NOW - 5, summary: '在写代码', source: LOCAL_SRC }] } });
tl = document.getElementById('dbgTimeline').innerHTML;
check('2 逐事件本地戳压过总链路云端', tl.includes('src-local') && tl.includes('本地 VLM') && !tl.includes('src-cloud'), tl.slice(0, 160));

/* 3 事件无 source → 回退总链路 */
api.renderTimeline({ ts: NOW, analyzer: { vision_source: CLOUD_SRC, recent: [
  { ts: NOW - 5, summary: '在写代码' }] } });
tl = document.getElementById('dbgTimeline').innerHTML;
check('3 旧事件回退 vision_source=云端', tl.includes('src-cloud') && tl.includes('云端 VLM'));

/* 4 老后端无 vision_source */
api.renderTimeline({ ts: NOW, analyzer: { recent: [
  { ts: NOW - 5, summary: '在写代码' }] } });
tl = document.getElementById('dbgTimeline').innerHTML;
check('4 老后端不崩且出裸「视觉」签', tl.includes('>视觉</span>') && !tl.includes('src-cloud') && !tl.includes('src-local'), tl.slice(0, 160));

/* 5 OCR 真实来源值 */
api.renderTimeline({ ts: NOW, analyzer: { vision_source: CLOUD_SRC, recent: [] },
  ocr: { recent: [
    { ts: NOW - 10, summary: '击杀播报：你击杀了敌人', source: 'ocr+input' },
    { ts: NOW - 20, summary: '中央文字：回合 3/7', source: 'ocr' }] } });
tl = document.getElementById('dbgTimeline').innerHTML;
check('5a ocr+input →「OCR+输入」+本地', tl.includes('OCR+输入') && tl.includes('src-local'));
check('5b ocr →「OCR」+本地', tl.includes('>OCR</span>'));
check('5c 不存在虚构的「多模态」签', !tl.includes('多模态'));

/* 6 音效 */
api.renderTimeline({ ts: NOW, analyzer: { recent: [] },
  audio: { recent: [{ ts: NOW - 30, summary: '枪声命中', source: 'audio' }] } });
tl = document.getElementById('dbgTimeline').innerHTML;
check('6 音效 →「音效」+本地', tl.includes('>音效</span>') && tl.includes('src-local'));

/* 7 空态 */
api.renderTimeline({ ts: NOW, analyzer: { recent: [] } });
tl = document.getElementById('dbgTimeline').innerHTML;
check('7 空态文案', tl.includes('暂无感知活动'));

/* 8 renderAnalyzer 看屏链路四种态 */
api.renderAnalyzer({ ts: NOW, analyzer: {
  vision_available: true, disabled: false, call_count: 3, dedup_skip: 1,
  fail_count: 0, fail_limit: 3, recent: [], vision_source: CLOUD_SRC } });
let an = document.getElementById('dbgAnalyzer').innerHTML;
check('8a 看屏链路行存在', an.includes('看屏链路'));
check('8b 云端 VLM + 模型名', an.includes('云端 VLM') && an.includes('智谱 GLM-4V-Flash'));

api.renderAnalyzer({ ts: NOW, analyzer: {
  vision_available: true, disabled: false, call_count: 3, dedup_skip: 1,
  fail_count: 0, fail_limit: 3, recent: [], vision_source: LOCAL_SRC } });
an = document.getElementById('dbgAnalyzer').innerHTML;
check('8c 本地 VLM + 模型名', an.includes('本地 VLM') && an.includes('本地 MiniCPM-o 2.6'));

api.renderAnalyzer({ ts: NOW, analyzer: {
  vision_available: false, disabled: false, call_count: 0, dedup_skip: 0,
  fail_count: 0, fail_limit: 3, recent: [],
  vision_source: { provider: '', model: '', label: '', local: null,
    active_id: '', last_used_id: '' } } });
an = document.getElementById('dbgAnalyzer').innerHTML;
check('8d 未绑定视觉模型', an.includes('未绑定视觉模型'));

api.renderAnalyzer({ ts: NOW, analyzer: {
  vision_available: false, disabled: false, call_count: 0, dedup_skip: 0,
  fail_count: 0, fail_limit: 3, recent: [] } });
an = document.getElementById('dbgAnalyzer').innerHTML;
check('8e 老后端未知态', an.includes('未知（后端未上报）'));

/* 9 时间线卡片来源说明 */
check('9 卡片有来源说明静态文案',
  HTML.includes('来源：<b>视觉</b>＝VLM 看屏') && HTML.includes('OCR</b>＝本地 RapidOCR'));

/* 10 CSS 芯片样式 */
check('10a src-cloud 样式存在', /\.dbg-tag\.src-cloud\{[^}]*var\(--warn\)/.test(CSS));
check('10b src-local 样式存在', /\.dbg-tag\.src-local\{/.test(CSS));

/* 11 死分支清除（注释里允许出现这个词，代码里不允许再有比较分支） */
check('11 renderTimeline 无 ocr+vision 死分支',
  !/['"]ocr\+vision['"]\s*\?/.test(tlBlock) && !tlBlock.includes("=== 'ocr+vision'"));

lines.push('');
lines.push(`结果: ${pass} PASS / ${fail} FAIL`);
fs.writeFileSync(REPORT, lines.join('\n') + '\n', 'utf8');
console.log(lines.join('\n'));
process.exit(fail ? 1 : 0);
