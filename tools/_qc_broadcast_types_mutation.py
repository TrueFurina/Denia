# -*- coding: utf-8 -*-
"""`tools/check_broadcast_types.py` 的变异刀（验证那道闸门有牙齿）。

规矩：**加门必须同时加变异**。这道闸门守的是"前端 host message 监听器里分发的 type，
后端必须真的会广播"，它有两种失效方向，都必须被这里抓住：

  M1 **原 bug 原样**（2026-10-22 修的 character_switched 死分支）
     把后端那行 `_wv2_broadcast({"type": "character_switched", ...})` 改成不广播
     → `character_switched` 离开后端全集 → 必须红并点名。
     这一刀的价值：它证明闸门在**修复之前**确实是红的（而不是我修完才写的马后炮）。
  M2 前端监听器里多一个后端不广播的分支 → 必须红并点名
     锚点：app_backend.js 的 startChatLive 监听器里插 `else if (d.type === 'zzzNotBroadcast')`
  M3 **对照组·不该误伤**：页面内部 postMessage 监听器里的 type 不许报红
     锚点：live2d/rig/index.html 再加一个 `window.addEventListener('message')`，
     里面判 `dd.type === 'zzzPageInternalOnly'`。rig 页已登记在 PAGE_INTERNAL 里，
     所以仍是绿的。没有这一刀，M2 的红可能只是因为闸门在"全仓库找 .type ==="。
  M4 **对照组·不该误伤**：`dataset.type` 不是消息分发
     锚点：app.js 插 `btn.dataset.type === "zzzDatasetType"`。全仓库有 25 处以上
     这种非消息 `.type` 比较（通知项 n.type、骨架 s.type==='row'、本地事件总线
     ev.type==='asr'…），一刀切必假红。
  M5 **自保·覆盖数**：少一个 host message 监听器 → 必须 exit 2，不许当成通过
     锚点：app_backend.js 的角色切换监听器把 `'message'` 改成 `'zzzmessage'`
     → 监听器 4 → 3。这是闸门最阴的失效：0 命中 ⇒ 没有违规 ⇒ 打印"✓"退出 0。
  M6 **对照组·剥注释**：监听器注释里提到的 type 不算分发分支 → 必须仍然绿
     没有这一刀，M2 的红可能只是因为闸门根本不剥注释（那任何人写句注释都会误报，
     下一个人会去放宽判据）。
  M7 **自保·动态 type**：广播的 type 不是字面量 → 必须 exit 2
     锚点：把那行广播的 `"character_switched"` 换成变量 `_ZZZ_DYNAMIC_TYPE`。
     动态 type 意味着全集不可知，判据不能在不可知的全集上跑。

改源码一律按**字节**读写，锚点按各文件实际换行符拼（`_encode`）：文本模式在 Windows
上会把 `\n` 写成 `\r\n`，锚点静默失效。**换行符必须实测**——本仓同一批文件里出现过
CRLF 与 LF 混杂（这四个文件现在都是 LF，但别猜）。
"""

from __future__ import annotations

import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
GATE = os.path.join("tools", "check_broadcast_types.py")

WEBAPP = os.path.join("src", "ui", "webapp.py")
ADAPTER = os.path.join("src", "ui", "web", "redesign", "app_backend.js")
RIG = os.path.join("src", "ui", "web", "live2d", "rig", "index.html")
APPJS = os.path.join("src", "ui", "web", "app.js")

# 后端那行广播（M1 删它 / M7 把它变成动态 type）
BC_LINE = '            _wv2_broadcast({"type": "character_switched", "char_id": char_id})'
# startChatLive 监听器里的分发分支（M2 加分支 / M6 加注释）
CHAT_PUSH = "          if (d.type === 'chat_push') pollChat();\n"
# 角色切换监听器的头两行（M5 改事件名 → 少一个监听器）
CS_HEAD = "      wv.addEventListener('message', function(ev){\n        const d = ev && ev.data;\n"
# rig 页已有的页面内部监听器（M3 在它前面再加一个）
RIG_HEAD = "window.addEventListener('message', function(ev){\n  const d=ev.data;\n  if(!d) return;\n"
# app.js 里的 dataset.type（M4）
DS_LINE = '        btn.textContent = btn.dataset.type === "kill" ? "录制击杀音" :\n'


def _read(rel: str) -> bytes:
    with io.open(os.path.join(ROOT, rel), "rb") as f:
        return f.read()


def _write(rel: str, data: bytes) -> None:
    with io.open(os.path.join(ROOT, rel), "wb") as f:
        f.write(data)


def _encode(rel: str, text: str) -> bytes:
    eol = b"\r\n" if b"\r\n" in _read(rel) else b"\n"
    return text.replace("\n", eol.decode()).encode("utf-8")


def run_gate() -> tuple[int, str]:
    p = subprocess.run([PY, GATE], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


# (标签, 文件, 原文, 改后, 期望退出码, 期望输出里的字串)
KNIVES = [
    ("M2 前端多一个后端不广播的分支",
     ADAPTER, CHAT_PUSH,
     CHAT_PUSH + "          else if (d.type === 'zzzNotBroadcast') { /* 后端从不广播这个 type */ }\n",
     1, "zzzNotBroadcast"),
    ("M3 对照组：页面内部 postMessage 的 type 不许误伤",
     RIG, RIG_HEAD,
     "/* 对照组：又一个页面内部 postMessage 监听器（rig 收宿主页），不经过后端广播 */\n"
     "window.addEventListener('message', function(ev){\n"
     "  var dd = ev && ev.data;\n"
     "  if (dd && dd.type === 'zzzPageInternalOnly') { /* 不该报红 */ }\n"
     "});\n" + RIG_HEAD,
     0, "✓"),
    ("M4 对照组：dataset.type 不是消息分发",
     APPJS, DS_LINE,
     '        var _zzzDs = btn.dataset.type === "zzzDatasetType";\n' + DS_LINE,
     0, "✓"),
    ("M5 自保：少一个 host message 监听器 → exit 2",
     ADAPTER, CS_HEAD,
     "      wv.addEventListener('zzzmessage', function(ev){\n        const d = ev && ev.data;\n",
     2, "host message 监听器"),
    ("M6 对照组：监听器注释里提的 type 不算分发分支",
     ADAPTER, CHAT_PUSH,
     CHAT_PUSH + "          /* 对照组：注释里提到的 type 不算分发分支（zzzInCommentOnly） */\n",
     0, "✓"),
    ("M7 自保：广播的 type 不是字面量 → exit 2",
     WEBAPP, BC_LINE,
     '            _wv2_broadcast({"type": _ZZZ_DYNAMIC_TYPE, "char_id": char_id})',
     2, "不是字面量"),
]


def main() -> int:
    bad = 0
    code, out = run_gate()
    if code != 0:
        print("  FAIL 起点就不是绿的，变异没有意义")
        print(out[-800:])
        return 1
    print("  PASS 起点绿：%s" % out.strip().splitlines()[0])

    # ---------------- M1：原 bug 原样（后端不广播 character_switched）--------
    # 这是这道闸门存在的理由：2026-10-22 之前，`d.type === 'character_switched'`
    # 这条分支已经写了三个月，后端却从来没往 WebView2 通道发过这个 type。
    data = _read(WEBAPP)
    old = _encode(WEBAPP, BC_LINE + "\n")
    n = data.count(old)
    if n != 1:
        print("  FAIL M1：锚点命中 %d 次（应为 1）—— 变异被跳过" % n)
        bad += 1
    else:
        _write(WEBAPP, data.replace(old, _encode(WEBAPP, "            pass  # 变异：不再广播\n")))
        try:
            code, out = run_gate()
        finally:
            _write(WEBAPP, data)
        if code != 1:
            print("  FAIL M1 后端不广播 character_switched：退出码 %d（期望 1）" % code)
            print(out[-800:])
            bad += 1
        elif "character_switched" not in out:
            print("  FAIL M1：红了但没点名 character_switched")
            print(out[-800:])
            bad += 1
        else:
            print("  PASS M1 后端不广播 character_switched：闸门红了并点名")

    for label, rel, old_txt, new_txt, want_code, want_str in KNIVES:
        data = _read(rel)
        old, new = _encode(rel, old_txt), _encode(rel, new_txt)
        n = data.count(old)
        if n != 1:
            print("  FAIL %s：锚点命中 %d 次（应为 1）—— 变异被跳过" % (label, n))
            bad += 1
            continue
        _write(rel, data.replace(old, new))
        try:
            code, out = run_gate()
        finally:
            _write(rel, data)
        if code != want_code:
            print("  FAIL %s：退出码 %d（期望 %d）" % (label, code, want_code))
            print(out[-800:])
            bad += 1
        elif want_str not in out:
            print("  FAIL %s：结果里没有「%s」" % (label, want_str))
            print(out[-800:])
            bad += 1
        else:
            print("  PASS %s：退出码 %d，结果含「%s」" % (label, code, want_str))

    code, out = run_gate()
    if code != 0:
        print("  FAIL 还原后闸门仍红（源码没恢复干净）")
        print(out[-800:])
        bad += 1
    else:
        print("  PASS 还原后重新变绿")

    print("变异刀：%d 把刀，%d 把失效" % (len(KNIVES) + 1, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
