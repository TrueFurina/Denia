# -*- coding: utf-8 -*-
"""「没配大脑」承接浮层：在**用户真正想说话的那一刻**接住他。

背景（2026-10-20，竞品调研后的 P1）：首启流程只做了「建角色 → 体检」，不做"填 Key"，
好处是不拿表格拦住人，代价是**用户第一次打完字、点发送，得到的是「未发送：xxx」**。
调研 28 个消费级产品，0 个要求填 API Key；成功的那几个都是「意图点承接」——
Chatbox 把引导做成一个常驻会话、Cline 把选择放在输入框上方。
顶部常驻条（#brainBar）解决不了这件事：它出现在**错误的时刻**——刚进桌面时用户没有
聊天意图，看到了也不会动；等他真想说话，眼睛早把它过滤掉了。

本文件测的不是"页面上有个浮层"，而是四件更容易做错的事：
  1. 它只在**发送失败**那一刻出现（加载时就亮 = 顶部条换个位置，白做）；
  2. 判据是**能力槽状态**，不是 error 文案（文案会改，"端口冷却"和"没配"在 error 里
     长得几乎一样）；
  3. 判据是**三态**：后端没就绪（success:false）是"问不出来"，不是"没配"——
     压成二态就会在"她正在加载模型"的时刻弹「她还没接上大脑」；
  4. 文案里承诺的事（"那句话还在输入框里"）**真的有代码保证**，不是写得好听。

【2026-11-11 第 48 轮】判据 43 → 70：探针 41 把捅出 25 处零判据，最刺眼的是
S01~S12 十二把接线刀整片全洞 —— 把调用塞进注释、`classList.add('show')` 塞进
if(false)，`n_add==1` / 下标先后 / `in gate_fn` / `count==2` 这些源码文本判据
一条都不红。补法（判据先行）：把页面两段 <script> 与两条发送失败分支的原文片段
注入假 DOM 的 node 环境真跑（H00~H14，不复刻逻辑）；CSS 判据从「字符串存在」
升级为值语义（C00~C09）；真值表改静态命名并补 {"success": true} 缺 health 的
样本（T01 / T02-01..10 / T03）；加 EXPECTED_CHECKS=70 条数总账，少跑的判据不算通过。
变异刀：tools/_qc_setup_hint_mutation.py 四十一刀。

跑法：python src/tests/test_setup_hint.py
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

PASS = 0
FAIL = 0
EXPECTED_CHECKS = 70  # 实测 70 条；少跑的判据不算通过
_fails: list = []
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PAGE = os.path.join(_ROOT, "src", "ui", "web", "index.html")


def check(ok: bool, msg: str) -> None:
    global PASS, FAIL
    print(("  ✓ " if ok else "  ✗ ") + msg)
    if ok:
        PASS += 1
    else:
        FAIL += 1
        _fails.append(msg)


def _safe(fn, *args):
    """坏实现抛异常时返回标记串，不让整门崩掉（判据崩 ≠ 判据错）。"""
    try:
        return fn(*args)
    except Exception as e:
        return "<RAISED:%s>" % type(e).__name__


def _js_func(src: str, name: str) -> str:
    """从整页 JS 里抠出 `window.NAME = function(...){...};`（按花括号配平）。"""
    key = "window.%s = function" % name
    i = src.find(key)
    if i < 0:
        return ""
    b = src.find("{", i)
    if b < 0:
        return ""
    depth = 0
    for j in range(b, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1] + ";"
    return ""


def _node() -> str:
    cand = [
        os.path.join(os.path.expanduser("~"), ".workbuddy-ai", "binaries", "node",
                     "versions", "22.22.2-6", "node.exe"),
        shutil.which("node") or "",
    ]
    for c in cand:
        if c and os.path.isfile(c):
            return c
    return ""


def _run_predicate(fn_src: str, cases: list) -> list:
    """把真实 JS 判据丢给 node 跑真值表（不在 Python 里复刻逻辑）。"""
    node = _node()
    if not node:
        return []
    js = "var window = {};\n" + fn_src + """
var cases = %s;
var out = cases.map(function(c){
  try { return String(window.%s(c)); } catch (e) { return 'THROW:' + e.message; }
});
console.log(JSON.stringify(out));
""" % (json.dumps(cases, ensure_ascii=False), "__chatUsable")
    fd, path = tempfile.mkstemp(suffix=".js")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(js)
        r = subprocess.run([node, path], capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            print("    node stderr:", (r.stderr or "").strip()[:400])
            return []
        return json.loads((r.stdout or "").strip().splitlines()[-1])
    except Exception:
        return []
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ---------------- 假 DOM harness：把页面里的真 JS 跑起来 ----------------

SLOT_NO = {"success": True, "health": {"chat": "none"},
           "slots": {"chat": {"state_text": "未配置"}}}
SLOT_UNKNOWN = {"success": False, "health": {"chat": "ok"}}

HINT_HARNESS = r"""
var SLOT_NO = __SLOT__;
var SLOT_UNKNOWN = __SLOT_UNKNOWN__;
var CURRENT = SLOT_NO;
var dom = {};
function mkEl(id){
  var cls = {}, subs = {}, listeners = {};
  return {
    id: id, hidden: false, textContent: '',
    classList: {
      add: function(c){ cls[c] = true; },
      remove: function(c){ delete cls[c]; },
      contains: function(c){ return !!cls[c]; }
    },
    querySelector: function(sel){ return subs[sel] || (subs[sel] = mkEl(id + sel)); },
    addEventListener: function(ev, fn){ (listeners[ev] || (listeners[ev] = [])).push(fn); },
    _fire: function(ev){ (listeners[ev] || []).forEach(function(f){ f({}); }); }
  };
}
['setupHint','brainBar','brainBarGo','brainBarX','setupHintGo','setupHintX']
  .forEach(function(id){ dom[id] = mkEl(id); });
var document = { getElementById: function(id){ return dom[id] || null; },
                 createElement: function(){ return mkEl('new'); } };
var window = {};
var location = { href: '' };
var Backend = { getCapabilitySlots: function(){ return Promise.resolve(CURRENT); },
                toast: function(){} };
window.Backend = Backend;

__JS1__

/* 探针包住两个入口：默认照常放行；spyOnly 期间只记调用、不产生异步副作用。 */
var refreshCalls = 0, callLog = [], spyOnly = false;
var _origMaybe = window.__maybeSetupHint;
window.__maybeSetupHint = function(v){
  callLog.push(!!v);
  if (spyOnly) return;
  return _origMaybe ? _origMaybe.apply(this, arguments) : undefined;
};
var _origRefresh = window.__brainBarRefresh;
if (typeof _origRefresh === 'function') {
  window.__brainBarRefresh = function(){
    refreshCalls++;
    return _origRefresh.apply(this, arguments);
  };
}

__JS2__

/* 页面原文片段①：打字发送失败分支（承接调用 + 原话还回输入框）。 */
function runSendFragment(){
  var res = { success: false, error: '没配' };
  var scroll = { querySelector: function(){ return null; },
                 querySelectorAll: function(){ return []; } };
  var optMsg = { id: 1 };
  var input = { value: '', style: {} };
  var v = '我刚打的那句话';
  callLog = []; spyOnly = true;
__JS_SEND__
  spyOnly = false;
  return { calls: callLog.slice(), restored: input.value };
}
/* 页面原文片段②：语音识别失败分支（第二条发送入口）。 */
function runVoiceFragment(){
  var r = { success: false, error: '识别失败' };
  callLog = []; spyOnly = true;
  (function(r){
__JS_VOICE__
  })(r);
  spyOnly = false;
  return { calls: callLog.slice() };
}

function observe(){
  return {
    show: dom.setupHint.classList.contains('show'),
    title: dom.setupHint.querySelector('.sh-title').textContent,
    sub: dom.setupHint.querySelector('.sh-sub').textContent,
    barHidden: dom.brainBar.hidden,
    hasRefresh: typeof window.__brainBarRefresh === 'function',
    refreshCalls: refreshCalls
  };
}
function reset(){
  dom.setupHint.classList.remove('show');
  dom.setupHint.querySelector('.sh-title').textContent = '';
  dom.setupHint.querySelector('.sh-sub').textContent = '';
  dom.brainBar.hidden = false;
  location.href = '';
  callLog = []; refreshCalls = 0;
}
function scenario(slot, voice, cb){
  CURRENT = slot; reset();
  if (typeof window.__maybeSetupHint === 'function') window.__maybeSetupHint(voice);
  setTimeout(function(){ cb(observe()); }, 50);
}

var out = {};
out.frag = { send: runSendFragment(), voice: runVoiceFragment() };
scenario(SLOT_NO, false, function(a){
  out.no = a;
  dom.setupHintX._fire('click');
  dom.setupHintGo._fire('click'); out.goHref = location.href; location.href = '';
  dom.brainBarGo._fire('click');  out.barHref = location.href;
  setTimeout(function(){
    out.afterLater = observe();
    scenario(SLOT_UNKNOWN, false, function(b){
      out.unknown = b;
      scenario(SLOT_NO, true, function(c){
        out.voice = c;
        console.log(JSON.stringify(out));
      });
    });
  }, 50);
});
"""


def _frag_of(html: str, start: str, stop: str) -> str:
    s = html.find(start)
    if s < 0:
        raise ValueError("片段起点找不到：%r" % start[:60])
    e = html.find(stop, s)
    if e < 0:
        raise ValueError("片段终点找不到：%r" % stop[:60])
    return html[s:e]


def _hint_obs(html: str) -> dict:
    """把页面里的真 JS（含两段失败分支原文）注入假 DOM 的 node 环境，返回观测值。"""
    node = _node()
    if not node:
        return {"ERROR": "node 不可用"}
    try:
        i = html.find("window.__chatUsable = function")
        s1 = html.rfind("<script>", 0, i) + len("<script>")
        e1 = html.find("</script>", i)
        j = html.find("承接浮层的开关与跳转")
        s2 = html.rfind("<script>", 0, j) + len("<script>")
        e2 = html.find("</script>", j)
        if min(s1, e1, s2, e2) < 0:
            return {"ERROR": "抠 <script> 失败"}
        send = _frag_of(html,
                        "          var reason = (res && res.error) ? res.error : '后端未连接，消息未送达';",
                        "\n        } else {")
        voice = _frag_of(html,
                         "          if (r && r.success && r.text){ /* 文字消息随后经 pollChat 出现 */ }",
                         "\n          if (V.on && !V.aiSpeaking) startListen();")
        src = (HINT_HARNESS.replace("__SLOT_UNKNOWN__", json.dumps(SLOT_UNKNOWN, ensure_ascii=False))
               .replace("__SLOT__", json.dumps(SLOT_NO, ensure_ascii=False))
               .replace("__JS1__", html[s1:e1]).replace("__JS2__", html[s2:e2])
               .replace("__JS_SEND__", send).replace("__JS_VOICE__", voice))
        fd, path = tempfile.mkstemp(suffix=".js")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(src)
            r = subprocess.run([node, path], capture_output=True, text=True, timeout=60,
                               encoding="utf-8", errors="replace")
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        if r.returncode != 0:
            return {"ERROR": (r.stderr or "")[:180]}
        lines = [x for x in (r.stdout or "").splitlines() if x.strip().startswith("{")]
        if not lines:
            return {"ERROR": "node 无输出"}
        return json.loads(lines[-1])
    except Exception as e:
        return {"ERROR": "%s: %s" % (type(e).__name__, str(e)[:160])}


def _o(obs, section, key, default=None):
    return default if "ERROR" in obs else obs.get(section, {}).get(key, default)


def _fg(obs, which, key, default=None):
    return default if "ERROR" in obs else obs.get("frag", {}).get(which, {}).get(key, default)


# ---------------- CSS 值语义：判「值」，不判 `in body` ----------------

def _rules(html: str) -> dict:
    out = {}
    for m in re.finditer(r"([.#][\w-]+(?:\.[\w-]+)*)\{([^}]*)\}", html):
        out.setdefault(m.group(1), m.group(2))
    return out


def css_val(html: str, sel: str, prop: str):
    body = _rules(html).get(sel)
    if body is None:
        return None
    for decl in body.split(";"):
        if ":" in decl:
            k, v = decl.split(":", 1)
            if k.strip() == prop:
                return v.strip()
    return None


def _nums(v) -> list:
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", v or "")]


def _ge(html, sel, prop, floor):
    v = css_val(html, sel, prop)
    if v is None:
        return False
    n = _nums(v)
    return bool(n) and n[0] >= floor


def _all_gt(html, sel, prop, floor):
    v = css_val(html, sel, prop)
    if v is None:
        return False
    n = _nums(v)
    return bool(n) and all(x > floor for x in n)


def main() -> int:
    global PASS, FAIL
    with open(_PAGE, encoding="utf-8") as f:
        html = f.read()

    print("=== [1] 只在发送失败那一刻出现，不在加载时亮 ===")
    # 【第 48 轮】原来写死 `setup-hint"`，加个空格就匹配不到 →
    # 「静态标签不带 show 类」红的其实是「没找到标签」，跟名字无关。
    # 放宽成 class 里允许别的 token，让这条判据真的去量 show。
    m = re.search(r'<div class="setup-hint[^"]*"[^>]*>', html)
    check(m is not None, "页面里有承接浮层 #setupHint")
    check(m is not None and "show" not in (m.group(0) if m else ""),
          "静态标签不带 show 类（带了就是「一进页面就亮」，等于把顶部条换个位置）")
    n_add = html.count("sh.classList.add('show')")
    check(n_add == 1, "全页只有一处点亮（找到 %d 处）" % n_add)
    i_add = html.find("sh.classList.add('show')")
    i_def = html.find("window.__showSetupHint = function")
    check(i_def > 0 and i_add > i_def,
          "点亮只发生在 __showSetupHint 内部（唯一入口，别的分支想亮必须调它）")
    i_toast = html.find("Backend.toast('未发送：'")
    i_call_text = html.find("window.__maybeSetupHint(false)")
    check(i_toast >= 0 and i_call_text > i_toast,
          "打字路径：接线在「未发送」提示之后（即在失败分支内，成功路径不会亮）")
    i_vtoast = html.find("Backend.toast('语音识别失败：'")
    i_call_voice = html.find("window.__maybeSetupHint(true)")
    check(i_vtoast >= 0 and i_call_voice > i_vtoast,
          "语音路径也接了（只守打字那条，用语音的人永远等不到这句话）")

    print("\n=== [2] 判据是能力槽状态，不是 error 文案 ===")
    gate_fn = _js_func(html, "__maybeSetupHint")
    show_fn = _js_func(html, "__showSetupHint")
    check("if (window.__chatUsable(r) !== 'no') return;" in gate_fn,
          "用 __chatUsable(r) 做闸门")
    check(gate_fn != "" and show_fn != ""
          and "res.error" not in (gate_fn + show_fn) and "reason" not in (gate_fn + show_fn),
          "判定与点亮两个函数都不碰 res.error / reason（文案会改，且「端口冷却」与「没配」长得一样）")

    print("\n=== [3] 判据三态：'unknown' 不等于「没配」===")
    fn = _js_func(html, "__chatUsable")
    check(fn != "", "window.__chatUsable 定义存在")
    check("'unknown'" in fn, "判据有 'unknown' 这一态（后端没就绪时问不出来，不能当没配）")
    check(html.count("window.__chatUsable = function") == 1,
          "判据全页只定义一次（两处各写一遍迟早漂移成「顶部条说能聊、浮层说聊不了」）")
    check("bar.hidden = (window.__chatUsable(r) === 'yes')" in html,
          "顶部条也走同一个判据（不是各判各的）")

    # 【第 48 轮】每条真值表判据改成**静态名字**：原先名字里带实得值，
    # 一变异名字就变，刀架没法「点名」它红的是哪条（第 47 轮 PREFER 的教训）。
    cases = [
        ("T02-01 ok / 已连通 → yes",
         {"success": True, "health": {"chat": "ok"},
          "slots": {"chat": {"state_text": "已连通"}}}, "yes"),
        ("T02-02 warn / 聊天已通·看屏未配 → yes",
         {"success": True, "health": {"chat": "warn"},
          "slots": {"chat": {"state_text": "聊天已通·看屏未配"}}}, "yes"),
        ("T02-03 warn / 未激活 → no",
         {"success": True, "health": {"chat": "warn"},
          "slots": {"chat": {"state_text": "未激活"}}}, "no"),
        ("T02-04 none / 未配置 → no",
         {"success": True, "health": {"chat": "none"},
          "slots": {"chat": {"state_text": "未配置"}}}, "no"),
        ("T02-05 bad → no",
         {"success": True, "health": {"chat": "bad"}}, "no"),
        ("T02-06 success:false → unknown",
         {"success": False, "health": {"chat": "ok"}}, "unknown"),
        ("T02-07 null → unknown", None, "unknown"),
        ("T02-08 {} → unknown", {}, "unknown"),
        ("T02-09 ok 但缺 slots → yes",
         {"success": True, "health": {"chat": "ok"}}, "yes"),
        ("T02-10 success:true 且缺 health → no",
         {"success": True}, "no"),
    ]
    got = _run_predicate(fn, [c[1] for c in cases])
    if not got:
        print("  - 跳过：找不到 node，无法执行真值表")
        for label, inp, want in cases:
            check(False, label + "（node 不可用，真值表未执行）")
        check(False, "T03 任何入参都不抛异常（node 不可用，未验证）")
    else:
        for (label, inp, want), g in zip(cases, got):
            check(g == want, label)
        check(all(g != "THROW" and not str(g).startswith("THROW:") for g in got),
              "T03 任何入参都不抛异常（页面里抛了就等于静默不弹）")
    # 【第 48 轮】静态命名的补样本判据：EXPECT 里点它，不点上面会随实际值变化的串
    one = _run_predicate(fn, [{"success": True}])
    check(one == ["no"],
          "T01 真值表补样本：success=true 且无 health → no（不是 unknown，也不抛）")

    print("\n=== [4] 文案承诺的事真的有代码保证 ===")
    i_restore = html.find("input.value = v; input.style.height = 'auto';")
    check(i_restore >= 0 and i_call_text > i_restore,
          "「刚才那句话还在输入框里」有据：先把原话还回输入框，再决定弹不弹")
    check("(st === 'none') ? '她还没接上「大脑」' : '她还聊不了天'" in html,
          "标题跟着状态走：只有「未配置」才说「没接上大脑」（配了 Key 没激活还说就是撒谎）")
    voice_sub = re.search(r"voice\s*\n?\s*\?\s*'([^']*)'", html)
    check(voice_sub is not None and "输入框" not in (voice_sub.group(1) if voice_sub else ""),
          "语音分支的副文不提「输入框」（语音没有输入框，说那句话就是假话）")
    check(voice_sub is not None and "开口" in (voice_sub.group(1) if voice_sub else ""),
          "语音分支的副文给了语音能照做的下一步")
    check("去配一个" in html and "稍后" in html, "两个出口都在（能去配，也能这次不配）")

    print("\n=== [5] 出口有效，且不把用户困在浮层里 ===")
    tgt = "/redesign/ui_redesign_modelapi.html"
    check(tgt in html, "「去配一个」指向模型与 API 页")
    check(os.path.isfile(os.path.join(_ROOT, "src", "ui", "web", tgt.lstrip("/"))),
          "跳转目标是真实文件（%s 存在）" % tgt)
    check(html.count("location.href = '%s'" % tgt) == 2,
          "顶部条与浮层跳同一个目标（两处不同就会有一个把人带错地方）")
    i_script = html.find("承接浮层的开关与跳转")
    tail = html[i_script:] if i_script > 0 else ""
    check(tail != "" and "el.classList.remove('show')" in tail,
          "「稍后」真的能关掉浮层")
    check(tail != "" and "sessionStorage" not in tail and "localStorage" not in tail,
          "不做「只弹一次」的记忆：每次点发送都是一次真实意图，每次都被同一个原因挡住")

    print("\n=== [5b] 一次只说一句话：浮层在时顶部条让位，关掉后还回来 ===")
    check(show_fn != "" and "window.__brainBarRefresh()" in show_fn,
          "浮层出现时收顶部条，且走 refresh 而不是直接写 hidden")
    check("function _hintShowing" in html and "classList.contains('show')" in html,
          "顶部条的判据会看「浮层是不是正显示着」（读 DOM，不另存标志位）")
    check("bar.hidden = (window.__chatUsable(r) === 'yes') || _hintShowing();" in html,
          "顶部条重算时把「浮层正显示」算进去（否则重试会把条放回来）")
    check("window.__brainBarRefresh = ask" in html,
          "暴露 __brainBarRefresh（否则「稍后」之后顶部条回不来）")
    check(tail != "" and "window.__brainBarRefresh()" in tail,
          "「稍后」会把顶部条请回来（它才是常驻的那层兜底）")
    check(tail != "" and "bar.hidden = true" not in tail,
          "「稍后」不直接关顶部条（要经 refresh 按真实状态重算）")

    print("\n=== [6] 出现方式：立刻可见，不淡入、不遮输入框 ===")
    rule = re.search(r"\.setup-hint\{([^}]*)\}", html)
    body = rule.group(1) if rule else ""
    show_rule = re.search(r"\.setup-hint\.show\{([^}]*)\}", html)
    show_body = show_rule.group(1) if show_rule else ""
    check("display:none" in body, "默认 display:none（不占位、不可点）")
    check("display:block" in show_body, ".show 用 display:block 立刻出现")
    check(all(k not in (body + show_body) for k in ("transition", "opacity", "animation")),
          "两条规则都不做淡入（承接提示迟 180ms 出现没有任何好处，headless 下也验不出来）")
    check("position:fixed" in body and "bottom:" in body,
          "浮在输入框上方（fixed + bottom），不挤动对话流")
    check('role="dialog"' in (m.group(0) if m else "") and 'aria-live="polite"' in (m.group(0) if m else ""),
          "有无障碍标注（读屏用户也要能知道她没接上大脑）")

    # ===== 【第 48 轮新增】[7] 把接线真跑起来：源码文本量不出接线 =====
    print("\n=== [7] 判据先行：把 __maybeSetupHint / __showSetupHint / 顶部条 IIFE 真跑一遍 ===")
    obs = _safe(_hint_obs, html)
    if not isinstance(obs, dict):
        obs = {"ERROR": str(obs)}
    check("ERROR" not in obs,
          "H00 前提：假 DOM harness 跑通且四段页面原文都能抠出来（%s）"
          % obs.get("ERROR", "ok"))
    check(_fg(obs, "send", "calls") == [False],
          "H01 打字失败分支真的调用了 __maybeSetupHint(false)")
    check(_fg(obs, "voice", "calls") == [True],
          "H02 语音失败分支真的调用了 __maybeSetupHint(true)")
    check(_fg(obs, "send", "restored") == "我刚打的那句话",
          "H03 原话真的还回输入框")
    check(_o(obs, "no", "show") is True,
          "H04 唯一点亮入口真的加上 show 类")
    check(_o(obs, "unknown", "show") is False,
          "H05 能力槽闸门真执行：unknown 不点亮")
    check(_o(obs, "no", "barHidden") is True and _o(obs, "afterLater", "barHidden") is False,
          "H06 顶部条让位：浮层显示时顶部条隐藏（_hintShowing 真被算进重算）")
    check(_o(obs, "no", "hasRefresh") is True,
          "H07 refresh 真的暴露成函数")
    check((_o(obs, "no", "refreshCalls") or 0) >= 1,
          "H08 点亮走 refresh 而不是直接写 hidden")
    check(_o(obs, "afterLater", "show") is False,
          "H09 稍后真的移除 show 类")
    check((_o(obs, "afterLater", "refreshCalls") or 0) > (_o(obs, "no", "refreshCalls") or 0),
          "H10 稍后真的请回顶部条（refresh 再调一次）")
    check(str(_safe(lambda: obs.get("goHref", ""))).endswith("ui_redesign_modelapi.html"),
          "H11 浮层去配一个真的改 location.href")
    check(str(_safe(lambda: obs.get("barHref", ""))).endswith("ui_redesign_modelapi.html"),
          "H12 顶部条去配一个真的改 location.href")
    check(_o(obs, "no", "title") == "她还没接上「大脑」",
          "H13 标题真的跟着 st 走")
    check(bool(_o(obs, "no", "sub")) and "输入框" in str(_o(obs, "no", "sub"))
          and "输入框" not in str(_o(obs, "voice", "sub")),
          "H14 副文真的按 voice 二选一赋值")

    # ===== 【第 48 轮新增】[8] CSS 判据从「字符串存在」升级为「值语义」 =====
    print("\n=== [8] 判据先行：CSS 判值不判字符串（绿的原因必须跟名字有关）===")
    check(all(_rules(html).get(s) is not None for s in (".setup-hint", ".sh-title", ".sh-sub")),
          "C00 前提：.setup-hint / .sh-title / .sh-sub 三条规则都能 parse 出规则体")
    check(_ge(html, ".setup-hint", "z-index", 100),
          "C01 z-index 真的大于 100（浮层不被遮）")
    check(_ge(html, ".setup-hint", "bottom", 40),
          "C02 bottom 真的抬离输入框（>= 40px）")
    check(_ge(html, ".setup-hint", "width", 300),
          "C03 width 真的够宽（min() 里的 px >= 300）")
    check(css_val(html, ".setup-hint", "background") not in (None, "", "transparent")
          and "transparent" not in (css_val(html, ".setup-hint", "background") or ""),
          "C04 background 不能是 transparent")
    check(_ge(html, ".setup-hint", "font-size", 12),
          "C05 font-size 真的可读（>= 12px）")
    check(_ge(html, ".setup-hint", "line-height", 1.2),
          "C06 line-height 真的可读（>= 1.2）")
    check(_all_gt(html, ".setup-hint", "padding", 0),
          "C07 padding 四边都非 0")
    check(css_val(html, ".sh-title", "color") not in (None, "", "transparent")
          and "transparent" not in (css_val(html, ".sh-title", "color") or ""),
          "C08 .sh-title color 不能是 transparent")
    check(_all_gt(html, ".sh-sub", "margin-bottom", 0),
          "C09 .sh-sub margin-bottom 非 0")

    print()
    if PASS + FAIL != EXPECTED_CHECKS:
        check(False, "判据条数总账（少跑的判据不算通过）")
    print("PASS=%d  FAIL=%d" % (PASS, FAIL))
    if _fails:
        print("❌ %d 项未通过" % len(_fails))
        for x in _fails:
            print("   - %s" % x)
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
