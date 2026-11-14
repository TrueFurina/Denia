# -*- coding: utf-8 -*-
"""首启承接浮层门（src/tests/test_setup_hint.py）的变异刀。

每把刀 = 把**生产页面** `src/ui/web/index.html` 改坏一处 → 本门必须变红，
而且必须红在**指定的那条判据**上。

判据口径（沿用第 38~48 轮）：本门自己打 `PASS=N  FAIL=M`；
「红了」= 非零退出 + 有汇总行（不是半路崩掉），
「命中」= 判据名出现在 `✗` 行里。

为什么要有这套刀（第 48 轮探针：41 把刀捅出 25 处洞）：

  ① **源码文本量不出接线**（S01~S12 整片 12/12 全洞）：把调用塞进注释、
     把 `classList.add('show')` 塞进 `if(false)`，`n_add==1`、
     `i_call_text > i_toast`、`in gate_fn`、`count==2` 一条都不红。
     补法：把 `__maybeSetupHint` / `__showSetupHint` / 顶部条 IIFE
     以及**两条发送失败分支的页面原文片段**注入假 DOM 的 node 环境真跑一遍（H00~H14）。
  ② **判据绿的原因跟名字无关**：`S28` 把 `bottom:104px` 改成 `bottom:0`
     压住输入框，旧判据只查 `"bottom:" in body` 照样绿；`S20` 更离谱，
     旧正则 `setup-hint"` 遇到 `class="setup-hint show"` 直接匹配不到，
     红的是「没找到标签」而不是 show。补法：CSS 判**值语义**（C00~C09）、
     正则放宽到 `setup-hint[^"]*`，让判据真的去量它名字里那件事。
  ③ **文案承诺没代码保证**（S35/S36/S39）：标题、副文、「原话还在输入框里」
     三句承诺全靠 `in html` / 正则，赋值塞进 `if(false)` 字符串还在，照样绿。
  ④ **等价变异不是洞**（S18/S42）：真值表缺 `success:true` 且无 `health` 的样本，
     `none→ok` 与去掉 `health` 兜底都没样本能击中。补 `T02-10` / `T01` 样本后，
     S18 红在 T01、S42 的 THROW 被 node 的 `catch` 接住红在 T03，不崩门。
  ⑤ **真值表判据改成静态名字**（T02-01~T02-10）：原先名字里带实得值，
     一变异名字就变，刀架没法点名。

运行：  python tools/_qc_setup_hint_mutation.py
"""
import hashlib
import os
import re
import subprocess
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
GATE = [os.path.join("src", "tests", "test_setup_hint.py")]

# (编号, 文件, 原锚点, 变异后, 必须红在哪条判据, 为什么, 锚点是否允许多处)
MUTS = [
    ('S01', 'src/ui/web/index.html', '          window.__maybeSetupHint(false);\n        } else {', '          /* window.__maybeSetupHint(false); */\n        } else {', 'H01 打字失败分支真的调用了 __maybeSetupHint(false)', '打字路径承接调用整行注释（字符串与下标先后都还在）', False),
    ('S02', 'src/ui/web/index.html', '            window.__maybeSetupHint(true);\n', '            /* window.__maybeSetupHint(true); */\n', 'H02 语音失败分支真的调用了 __maybeSetupHint(true)', '语音路径承接调用整行注释', False),
    ('S03', 'src/ui/web/index.html', "          sh.classList.add('show');\n", "          /* sh.classList.add('show'); */\n", 'H04 唯一点亮入口真的加上 show 类', '唯一点亮入口注释掉但字符串仍在（n_add==1 照样绿）', False),
    ('S04', 'src/ui/web/index.html', "              if (window.__chatUsable(r) !== 'no') return;\n", "              /* if (window.__chatUsable(r) !== 'no') return; */\n", 'H05 能力槽闸门真执行：unknown 不点亮', '能力槽闸门注释掉，所有状态都弹', False),
    ('S05', 'src/ui/web/index.html', "        function _hintShowing(){\n          var h = document.getElementById('setupHint');\n          return !!(h && h.classList.contains('show'));\n        }\n", "        function _hintShowing(){\n          var h = document.getElementById('setupHint');\n          // classList.contains('show')\n          return false;\n        }\n", 'H06 顶部条让位：浮层显示时顶部条隐藏', '顶部条让位判据永远 false，字符串留注释里', False),
    ('S06', 'src/ui/web/index.html', "            bar.hidden = (window.__chatUsable(r) === 'yes') || _hintShowing();\n", "            // bar.hidden = (window.__chatUsable(r) === 'yes') || _hintShowing();\n            bar.hidden = (window.__chatUsable(r) === 'yes');\n", 'H06 顶部条让位：浮层显示时顶部条隐藏', '重算时不再看浮层是否显示，精确全文留注释里', False),
    ('S07', 'src/ui/web/index.html', '        window.__brainBarRefresh = ask;\n        ask();\n', '        // window.__brainBarRefresh = ask;\n        ask();\n', 'H07 refresh 真的暴露成函数', '不暴露 refresh，字符串留注释里', False),
    ('S08', 'src/ui/web/index.html', "          if (typeof window.__brainBarRefresh === 'function') {\n            window.__brainBarRefresh();\n", "          if (false && typeof window.__brainBarRefresh === 'function') {\n            window.__brainBarRefresh();\n", 'H08 点亮走 refresh 而不是直接写 hidden', '点亮时改走「直接写 hidden」兜底分支，不再 refresh', False),
    ('S09', 'src/ui/web/index.html', "  if(x)  x.addEventListener('click', function(){\n    el.classList.remove('show');\n", "  if(x)  x.addEventListener('click', function(){\n    // el.classList.remove('show');\n", 'H09 稍后真的移除 show 类', '「稍后」不再关浮层，字符串留注释里', False),
    ('S10', 'src/ui/web/index.html', "    if (typeof window.__brainBarRefresh === 'function') window.__brainBarRefresh();\n", "    // if (typeof window.__brainBarRefresh === 'function') window.__brainBarRefresh();\n", 'H10 稍后真的请回顶部条（refresh 再调一次）', '「稍后」不再请回顶部条', False),
    ('S11', 'src/ui/web/index.html', "  if(go) go.addEventListener('click', function(){ location.href = '/redesign/ui_redesign_modelapi.html'; });\n", "  if(go) go.addEventListener('click', function(){ if (false) { location.href = '/redesign/ui_redesign_modelapi.html'; } });\n", 'H11 浮层去配一个真的改 location.href', '「去配一个」跳转塞进 if(false)，count==2 仍成立', False),
    ('S12', 'src/ui/web/index.html', "          location.href = '/redesign/ui_redesign_modelapi.html';\n        });\n        document.getElementById('brainBarX')", "          if (false) location.href = '/redesign/ui_redesign_modelapi.html';\n        });\n        document.getElementById('brainBarX')", 'H12 顶部条去配一个真的改 location.href', '顶部条跳转塞进 if(false)', False),
    ('S13', 'src/ui/web/index.html', "          if (st === 'ok') return 'yes';\n", "          if (st === 'ok') return 'no';\n", 'T02-01 ok / 已连通 → yes', 'ok 判成 no（真值表第 1 条应红）', False),
    ('S14', 'src/ui/web/index.html', "          if (!r || r.success !== true) return 'unknown';\n", "          if (!r) return 'unknown';\n", 'T02-06 success:false → unknown', '不再认 success!==true，success:false 被误判', False),
    ('S15', 'src/ui/web/index.html', "          if (st === 'warn' && txt.indexOf('看屏') >= 0) return 'yes';\n", "          if (st === 'warn' && txt.indexOf('看屏') >= 999) return 'yes';\n", 'T02-02 warn / 聊天已通·看屏未配 → yes', '「聊天已通·看屏未配」不再放行', False),
    ('S16', 'src/ui/web/index.html', "          return 'no';\n        };\n", "          return 'yes';\n        };\n", 'T02-04 none / 未配置 → no', '末档 no 改成 yes', False),
    ('S17', 'src/ui/web/index.html', "          if (!r || r.success !== true) return 'unknown';\n", "          if (!r || r.success !== true) return 'no';\n", 'T02-06 success:false → unknown', 'unknown 压成二态 no', False),
    ('S18', 'src/ui/web/index.html', "          var st = ((r.health || {}).chat) || 'none';\n", "          var st = ((r.health || {}).chat) || 'ok';\n", 'T01 真值表补样本：success=true 且无 health → no', 'health 缺失回落 none→ok（真值表缺 success=true 且无 health 的样本 → 等价变异）', False),
    ('S42', 'src/ui/web/index.html', "          var st = ((r.health || {}).chat) || 'none';\n", '          var st = r.health.chat;\n', 'T03 任何入参都不抛异常', '去掉 health 兜底，缺 health 会抛（同样缺样本 → 等价变异）', False),
    ('S19', 'src/ui/web/index.html', "          sh.classList.add('show');\n", "          sh.classList.add('show');\n          // sh.classList.add('show')\n", '全页只有一处点亮', '全页出现两处点亮字符串（n_add==1 应红）', False),
    ('S20', 'src/ui/web/index.html', '<div class="setup-hint" id="setupHint" role="dialog" aria-live="polite">', '<div class="setup-hint show" id="setupHint" role="dialog" aria-live="polite">', '静态标签不带 show 类', '静态标签带 show（一进页面就亮）', False),
    ('S21', 'src/ui/web/index.html', '<div class="setup-hint" id="setupHint" role="dialog" aria-live="polite">', '<div class="setup-hint" id="setupHint">', '有无障碍标注', '去掉 role/aria 无障碍标注', False),
    ('S22', 'src/ui/web/index.html', "              if (window.__chatUsable(r) !== 'no') return;\n", "              if (window.__chatUsable(r) !== 'no') return; var reason = res.error;\n", '判定与点亮两个函数都不碰 res.error / reason', '闸门碰 res.error / reason', False),
    ('S23', 'src/ui/web/index.html', "  if(go) go.addEventListener('click', function(){ location.href = '/redesign/ui_redesign_modelapi.html'; });\n", "  if(go) go.addEventListener('click', function(){ location.href = '/redesign/nope.html'; });\n", '顶部条与浮层跳同一个目标', '浮层跳到别处（count==2 应红）', False),
    ('S24', 'src/ui/web/index.html', "    el.classList.remove('show');\n", "    el.classList.remove('show');\n    var bar = document.getElementById('brainBar'); bar.hidden = true;\n", '「稍后」不直接关顶部条', '「稍后」直接关顶部条（tail 含 bar.hidden=true 应红）', False),
    ('S25', 'src/ui/web/index.html', '        .setup-hint.show{display:block}\n', '        .setup-hint.show{display:block;opacity:0;transition:opacity .18s}\n', '两条规则都不做淡入', '.show 加淡入', False),
    ('S26', 'src/ui/web/index.html', '        .setup-hint{display:none;position:fixed;left:50%;bottom:104px;\n', '        .setup-hint{display:block;position:fixed;left:50%;bottom:104px;\n', '默认 display:none', '默认 display 改成 block', False),
    ('S27', 'src/ui/web/index.html', '          padding:14px 16px 12px;border-radius:14px;z-index:9000;\n', '          padding:14px 16px 12px;border-radius:14px;z-index:0;\n', 'C01 z-index 真的大于 100', 'z-index 归零（浮层被遮住，无判据）', False),
    ('S28', 'src/ui/web/index.html', '        .setup-hint{display:none;position:fixed;left:50%;bottom:104px;\n', '        .setup-hint{display:none;position:fixed;left:50%;bottom:0;\n', 'C02 bottom 真的抬离输入框', 'bottom 归零压住输入框（bottom: 仍在 → 绿）', False),
    ('S29', 'src/ui/web/index.html', '          transform:translateX(-50%);width:min(392px,calc(100vw - 36px));\n', '          transform:translateX(-50%);width:2px;\n', 'C03 width 真的够宽', '宽度改 2px（无判据）', False),
    ('S30', 'src/ui/web/index.html', '          background:color-mix(in srgb,var(--accent-1) 8%,var(--bg-2));\n', '          background:transparent;\n', 'C04 background 不能是 transparent', '背景透明（无判据）', False),
    ('S31', 'src/ui/web/index.html', '          box-shadow:0 14px 38px rgba(0,0,0,.18);font-size:13px;color:var(--ink-2);line-height:1.5}\n', '          box-shadow:0 14px 38px rgba(0,0,0,.18);font-size:2px;color:var(--ink-2);line-height:0}\n', 'C05 font-size 真的可读', '字号/行高改坏（无判据）', False),
    ('S32', 'src/ui/web/index.html', '          padding:14px 16px 12px;border-radius:14px;z-index:9000;\n', '          padding:0;border-radius:14px;z-index:9000;\n', 'C07 padding 四边都非 0', 'padding 归零（无判据）', False),
    ('S33', 'src/ui/web/index.html', '        .sh-title{color:var(--ink);font-weight:600;font-size:14px;margin-bottom:3px}\n', '        .sh-title{color:transparent;font-weight:600;font-size:14px;margin-bottom:3px}\n', 'C08 .sh-title color 不能是 transparent', '标题文字透明（无判据）', False),
    ('S34', 'src/ui/web/index.html', '        .sh-sub{margin-bottom:11px}\n', '        .sh-sub{margin-bottom:0}\n', 'C09 .sh-sub margin-bottom 非 0', '副文间距归零（无判据）', False),
    ('S35', 'src/ui/web/index.html', "          if (t) t.textContent = (st === 'none') ? '她还没接上「大脑」' : '她还聊不了天';\n", "          if (false) t.textContent = (st === 'none') ? '她还没接上「大脑」' : '她还聊不了天';\n", 'H13 标题真的跟着 st 走', '标题不再跟着状态走，精确全文仍在', False),
    ('S36', 'src/ui/web/index.html', '          if (s) s.textContent = voice\n', '          if (false) s.textContent = voice\n', 'H14 副文真的按 voice 二选一赋值', "副文整段不赋值，正则仍能抠到 voice ? '...'", False),
    ('S37', 'src/ui/web/index.html', "            ? '刚才那句话没送到她那里——配好之后直接开口说就行。'\n", "            ? '刚才那句话还在输入框里——配好之后直接点发送就行。'\n", '语音分支的副文不提「输入框」', '语音副文提「输入框」（应红）', False),
    ('S38', 'src/ui/web/index.html', "            ? '刚才那句话没送到她那里——配好之后直接开口说就行。'\n", "            ? '刚才那句话没送到她那里。'\n", '语音分支的副文给了语音能照做的下一步', '语音副文不给下一步（应红）', False),
    ('S39', 'src/ui/web/index.html', "          if (input && !input.value.trim()) { input.value = v; input.style.height = 'auto'; }\n", "          if (false) { input.value = v; input.style.height = 'auto'; }\n", 'H03 原话真的还回输入框', '原话不还回输入框，文案撒谎但字符串还在', False),
    ('S40', 'src/ui/web/index.html', '  /* 关掉浮层后把顶部条请回来', "  /* sessionStorage.setItem('hint_seen','1');\n     关掉浮层后把顶部条请回来", '不做「只弹一次」的记忆', 'tail 里出现 sessionStorage（应红，对照刀）', False),
]

def _sha(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _run():
    return subprocess.run([PY] + GATE, cwd=PROJ, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=900)


def _out(r):
    return (r.stdout or "") + (r.stderr or "")


def _summary(r):
    m = re.search(r"PASS=\d+\s+FAIL=\d+", _out(r))
    return m.group(0) if m else "(无汇总行)"


def _judged(r):
    """非零退出 + 本门自己打了汇总行（而不是半路崩掉）。"""
    return r.returncode != 0 and bool(
        re.search(r"PASS=\d+\s+FAIL=\d+", _out(r)))


def _hit(r, name):
    """本门把失败的判据打成 `  ✗ <名字>` 行（不是 ❌ 汇总行）。"""
    return any(ln.strip().startswith("✗") and name in ln
               for ln in _out(r).splitlines())


def main():
    base = _run()
    if not (base.returncode == 0
            and re.search(r"PASS=\d+\s+FAIL=0", _out(base))):
        print("FAIL 基线不是绿的 —— 后面的红不能证明任何事：")
        print(_out(base)[-1500:])
        return 2
    print("基线：绿 | %s" % _summary(base))

    rows = []
    for key, rel, old, new, expect, why, all_hits in MUTS:
        p = os.path.join(PROJ, *rel.split("/"))
        orig = open(p, encoding="utf-8").read()
        n = orig.count(old)
        if n == 0 or (not all_hits and n != 1):
            print("  %-6s 锚点命中 %d 次（应恰好 1 次），锚点已腐烂" % (key, n))
            rows.append((key, "ANCHOR", why))
            continue
        h0 = _sha(p)
        try:
            open(p, "w", encoding="utf-8", newline="").write(orig.replace(old, new))
            r = _run()
        finally:
            open(p, "w", encoding="utf-8", newline="").write(orig)
            if _sha(p) != h0:
                raise SystemExit("还原失败（字节不一致）：%s" % rel)
        judged = _judged(r)
        hit = _hit(r, expect)
        tag = "红·命中" if (judged and hit) else ("红·跑偏" if judged else "没红")
        print("  %-6s %-8s %s" % (key, tag, _summary(r)))
        if judged and not hit:
            print("      ↳ 红了但没红在「%s」上：%s"
                  % (expect, [ln[:80] for ln in _out(r).splitlines()
                              if ln.strip().startswith("✗")][:3]))
        rows.append((key, tag, why))

    print("\n" + "=" * 72)
    ok = sum(1 for _, t, _ in rows if t == "红·命中")
    print("刀 %d 把：红且命中 %d、红但跑偏 %d、没红 %d、锚点腐烂 %d"
          % (len(rows), ok,
             sum(1 for _, t, _ in rows if t == "红·跑偏"),
             sum(1 for _, t, _ in rows if t == "没红"),
             sum(1 for _, t, _ in rows if t == "ANCHOR")))
    if ok != len(rows):
        print("\n没验住的：")
        for key, t, why in rows:
            if t != "红·命中":
                print("  [%s] %s —— %s" % (t, key, why))
        return 1
    print("全部 %d 把刀都红在指定判据上。" % ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
