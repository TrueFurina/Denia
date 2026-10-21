/* 批量语法检查：把一个清单里的每段 JS 都交给 V8 解析一遍，逐条回结果。
 *
 * 用法：node --experimental-vm-modules tools/js_syntax_runner.js <manifest.json>
 *   manifest = [{ "id": 0, "name": "index.html 内联块#3", "file": ".../0.js", "module": false }]
 *
 * 【为什么不是「每段起一个 node --check」】实测 Windows 上起一次 node 约 230ms，
 * 全站 88 段就是 20 秒 —— 够把这道门禁挤成慢项。`vm.Script` / `vm.SourceTextModule`
 * 用的是**同一个 V8 解析器**（只编译、不执行），一个进程跑完，总耗时约 1 秒。
 *
 * 【为什么要区分 script / module 两种解析目标】这不是"宽松一点让它过"：
 *   - `<script>` 里出现 `import` → 浏览器直接 SyntaxError，整块不执行（真缺陷）；
 *   - `<script type="module">` 里的 `import` 才是对的。
 * 目标由 Python 侧按**加载上下文**判定（内联看 `type=`，外链看引用它的页面），
 * 这里只负责按指定目标解析。用 `node --check` 的自动探测反而会把这个区别抹平
 * （Node 22 的 `--check` 会自动按 ESM 重试，于是"普通脚本里写了 import"也判绿）。
 *
 * 【为什么逐行 JSON】语法错误信息里会带**出错那行源码**，而那行源码本身可能含换行
 * （正是 `out.join('` + 真换行 这种错误），裸文本协议会被它撕开，导致「少回一条」
 * 被误判成「那条通过了」。
 *
 * 【自检】开跑前先编译一段必然错误的代码（两种目标各一次），若它居然通过，说明这套
 * 检查恒真（比如将来有人换成惰性构造），直接 exit 2 让 Python 侧判红 —— 否则这道
 * 门禁就是装饰品。
 */
'use strict';

const fs = require('fs');
const vm = require('vm');

const manifestPath = process.argv[2];
if (!manifestPath) {
  console.error('用法: node --experimental-vm-modules js_syntax_runner.js <manifest.json>');
  process.exit(2);
}

if (typeof vm.SourceTextModule !== 'function') {
  /* 没带 --experimental-vm-modules：模块段根本没法按模块解析，不能静默降级 */
  console.log(JSON.stringify({ selftest: 'no-source-text-module' }));
  process.exit(2);
}

let manifest;
try {
  manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
} catch (e) {
  console.error('读不了清单: ' + e.message);
  process.exit(2);
}

/* 自检：两种解析目标都必须拒绝 `var a = ;`，模块目标还必须接受 `export` */
let selfCheckOk = false;
try {
  new vm.Script('var a = ;');
} catch (e) {
  try {
    new vm.SourceTextModule('var a = ;');
  } catch (e2) {
    selfCheckOk = true;
  }
}
try {
  new vm.SourceTextModule('export const a = 1;');
} catch (e) {
  selfCheckOk = false;
}
if (!selfCheckOk) {
  console.log(JSON.stringify({ selftest: 'failed' }));
  process.exit(2);
}

function compile(src, item) {
  if (item.module) {
    new vm.SourceTextModule(src, { identifier: item.name });
  } else {
    new vm.Script(src, { filename: item.name });
  }
}

for (const item of manifest) {
  let src;
  try {
    src = fs.readFileSync(item.file, 'utf8');
  } catch (e) {
    console.log(JSON.stringify({ id: item.id, ok: false, msg: '读不到源码: ' + e.message, stack: [] }));
    continue;
  }
  try {
    compile(src, item);
    console.log(JSON.stringify({ id: item.id, ok: true }));
  } catch (e) {
    const stack = String((e && e.stack) || '').split('\n').slice(0, 4);
    console.log(JSON.stringify({
      id: item.id,
      ok: false,
      msg: String((e && e.message) || e),
      stack: stack,
    }));
  }
}
