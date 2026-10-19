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

跑法：python src/tests/test_setup_hint.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_fails: list = []
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PAGE = os.path.join(_ROOT, "src", "ui", "web", "index.html")


def check(ok: bool, msg: str) -> None:
    print(("  ✓ " if ok else "  ✗ ") + msg)
    if not ok:
        _fails.append(msg)


def _js_func(src: str, name: str) -> str:
    """从整页 JS 里抠出 `window.NAME = function(...){...};`（按花括号配平，不靠正则收尾）。

    按字面量切末尾会漏（函数体里有嵌套块），配平才稳。
    """
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
    """把真实 JS 判据丢给 node 跑真值表。

    不在这里用 Python 复刻一遍逻辑——复刻出来的测试只会证明"我两次写得一样"，
    证明不了页面里那段 JS 是对的（2026-10-20「改配置字段前必须查后端读哪个 key」
    就是这么被骗的：33 项断言全绿，因为测试里写的也是我以为的字段名）。
    """
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
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main() -> int:
    with open(_PAGE, encoding="utf-8") as f:
        html = f.read()

    print("=== [1] 只在发送失败那一刻出现，不在加载时亮 ===")
    m = re.search(r'<div class="setup-hint"[^>]*>', html)
    check(m is not None, "页面里有承接浮层 #setupHint")
    check(m is not None and "show" not in (m.group(0) if m else ""),
          "静态标签不带 show 类（带了就是「一进页面就亮」，等于把顶部条换个位置）")
    # add('show') 只能有一处，且必须在「承接浮层唯一入口」函数体内 ——
    # 允许散落多处的写法，迟早会在成功路径上也点一次
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

    cases = [
        ({"success": True, "health": {"chat": "ok"},
          "slots": {"chat": {"state_text": "已连通"}}}, "yes"),
        ({"success": True, "health": {"chat": "warn"},
          "slots": {"chat": {"state_text": "聊天已通·看屏未配"}}}, "yes"),
        ({"success": True, "health": {"chat": "warn"},
          "slots": {"chat": {"state_text": "未激活"}}}, "no"),
        ({"success": True, "health": {"chat": "none"},
          "slots": {"chat": {"state_text": "未配置"}}}, "no"),
        ({"success": True, "health": {"chat": "bad"}}, "no"),
        ({"success": False, "health": {"chat": "ok"}}, "unknown"),
        (None, "unknown"),
        ({}, "unknown"),
        ({"success": True, "health": {"chat": "ok"}}, "yes"),   # 缺 slots 不能崩
    ]
    got = _run_predicate(fn, [c[0] for c in cases])
    if not got:
        print("  - 跳过：找不到 node，无法执行真值表")
        _fails.append("真值表未执行（node 不可用）")
    else:
        for (inp, want), g in zip(cases, got):
            tag = json.dumps(inp, ensure_ascii=False)[:64]
            check(g == want, "%s → %s（期望 %s）" % (tag, g, want))
        check(all(g != "THROW" and not str(g).startswith("THROW:") for g in got),
              "任何入参都不抛异常（页面里抛了就等于静默不弹）")

    print("\n=== [4] 文案承诺的事真的有代码保证 ===")
    i_restore = html.find("input.value = v; input.style.height = 'auto';")
    check(i_restore >= 0 and i_call_text > i_restore,
          "「刚才那句话还在输入框里」有据：先把原话还回输入框，再决定弹不弹")
    check("(st === 'none') ? '她还没接上「大脑」' : '她还聊不了天'" in html,
          "标题跟着状态走：只有「未配置」才说「没接上大脑」（配了 Key 没激活还说就是撒谎）")
    # 语音没有输入框：同一句副文套到语音上就是假话
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

    print("\n=== [6] 出现方式：立刻可见，不淡入、不遮输入框 ===")
    rule = re.search(r"\.setup-hint\{([^}]*)\}", html)
    body = rule.group(1) if rule else ""
    show_rule = re.search(r"\.setup-hint\.show\{([^}]*)\}", html)
    show_body = show_rule.group(1) if show_rule else ""
    check("display:none" in body, "默认 display:none（不占位、不可点）")
    check("display:block" in show_body, ".show 用 display:block 立刻出现")
    # 两条规则都要查：刀8 就是只往 .show 里加淡入，只盯基础规则会漏（变异实测 8 刀曾漏 1）
    check(all(k not in (body + show_body) for k in ("transition", "opacity", "animation")),
          "两条规则都不做淡入（承接提示迟 180ms 出现没有任何好处，headless 下也验不出来）")
    check("position:fixed" in body and "bottom:" in body,
          "浮在输入框上方（fixed + bottom），不挤动对话流")
    check('role="dialog"' in (m.group(0) if m else "") and 'aria-live="polite"' in (m.group(0) if m else ""),
          "有无障碍标注（读屏用户也要能知道她没接上大脑）")

    print()
    if _fails:
        print("❌ %d 项未通过" % len(_fails))
        for x in _fails:
            print("   - %s" % x)
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
