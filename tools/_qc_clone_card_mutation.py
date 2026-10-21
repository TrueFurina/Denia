# -*- coding: utf-8 -*-
"""克隆面板参考音频两道门的变异刀（验证判据有牙齿）。

规矩：**加门必须同时加变异**。六刀，每刀打一个"会让这条链悄悄退化"的方向：

  M1 试听按钮改回只读 c.voice.ref（原 bug 的读法）
     → 前端真值表 S1「试听播的是随包 url」必须红
  M2 随包资产不物化，直接把 url 当磁盘路径传给引擎
     → S1「经后端物化了一次」「带的是物化后的真实磁盘路径」必须红
  M3 无桥分支改回画假进度条 + 谎报「克隆完成」
     → S3「明说没有语音桥」「没有谎报」「完成区没亮」必须红
  M4 预置数据把开发机绝对路径加回去
     → S4「预置数据里的开发机绝对路径已删除」必须红
  M5 后端白名单前缀改严（_role_assets2/），合法资产也被拒
     → 后端门禁 [1]「成功」必须红
  M6 /local_img 去掉 wav 的 audio Content-Type
     → 后端门禁 [4]「wav → audio/wav」必须红

改源码一律按字节读写 + 按各文件实际换行符拼锚点（`_encode`）。锚点命中 0 次或
多次**判红**，不是跳过 —— 缩进/换行一变锚点就失效，静默跳过等于这把刀不存在。
"""

from __future__ import annotations

import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
JS_TEST = os.path.join("tools", "test_clone_card_ui.py")
PY_TEST = os.path.join("tools", "test_clone_ref_audio.py")
JS = os.path.join("src", "ui", "web", "onboarding_role.js")
WEBAPP = os.path.join("src", "ui", "webapp.py")
MAINWIN = os.path.join("src", "ui", "main_window.py")


def _read(rel: str) -> bytes:
    with io.open(os.path.join(ROOT, rel), "rb") as f:
        return f.read()


def _write(rel: str, data: bytes) -> None:
    with io.open(os.path.join(ROOT, rel), "wb") as f:
        f.write(data)


def _encode(rel: str, text: str) -> bytes:
    eol = b"\r\n" if b"\r\n" in _read(rel) else b"\n"
    return text.replace("\n", eol.decode()).encode("utf-8")


def run_test(test: str) -> tuple[int, str]:
    p = subprocess.run([PY, test], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


KNIVES = [
    # (label, file, old, new, want, test)
    ("M1 试听改回只读 voice.ref",
     JS,
     "  var ref = _clRef; if(!ref) return;",
     "  var ref = _clChar && _clChar.voice && _clChar.voice.ref; if(!ref) return;",
     "试听播的是随包 url", JS_TEST),
    ("M2 随包资产不物化",
     JS,
     "      return Promise.resolve(B.useBundledRefAudio({url: ref.url})).then(function(r){",
     "      return Promise.resolve({success: true, stored_path: ref.url}).then(function(r){",
     "经后端物化了一次", JS_TEST),
    ("M3 无桥改回假进度+谎报",
     JS,
     "    if(!(B && B.testVoice && B.getVoiceTestStatus)){\n"
     "      toast('软件运行时这里会真的本地克隆（当前环境没有语音桥，不模拟进度）');\n"
     "      return;\n"
     "    }",
     "    if(!(B && B.testVoice && B.getVoiceTestStatus)){\n"
     "      btn.disabled = true; btn.textContent = '克隆中…';\n"
     "      $('clProgWrap').classList.add('on');\n"
     "      var pct = 0;\n"
     "      var t = setInterval(function(){\n"
     "        pct = Math.min(100, pct + (6 + Math.random()*9));\n"
     "        if(pct >= 100){ clearInterval(t); S.cloned[c.id] = true;\n"
     "          $('clDone').classList.add('on');\n"
     "          toast('克隆完成：' + c.name + ' 现在有专属声音了'); }\n"
     "      }, 5);\n"
     "      return;\n"
     "    }",
     "没有语音桥", JS_TEST),
    ("M4 预置数据加回开发机路径",
     JS,
     "     ref:{name:'xiaowei.wav', url:'_role_assets/ref/xiaowei.wav',",
     "     ref:{name:'xiaowei.wav', path:'D:/GPT-SoVITS/ref_audio/xiaowei.wav',"
     " url:'_role_assets/ref/xiaowei.wav',",
     "预置数据里的开发机绝对路径已删除", JS_TEST),
    ("M5 后端白名单前缀改严",
     WEBAPP,
     '        if not url.startswith("_role_assets/"):',
     '        if not url.startswith("_role_assets2/"):',
     "成功", PY_TEST),
    ("M6 /local_img 去掉 wav 音频类型",
     MAINWIN,
     "                               '.wav': 'audio/wav', '.mp3': 'audio/mpeg',",
     "                               '.mp3': 'audio/mpeg',",
     "wav → audio/wav", PY_TEST),
]


def main() -> int:
    bad = 0
    for test in (JS_TEST, PY_TEST):
        code, out = run_test(test)
        if code != 0:
            print("  FAIL 起点就不是绿的（%s），变异没有意义" % test)
            print(out[-800:])
            return 1
    print("  PASS 起点绿（两道门）")

    for label, rel, old_txt, new_txt, want, test in KNIVES:
        data = _read(rel)
        old, new = _encode(rel, old_txt), _encode(rel, new_txt)
        n = data.count(old)
        if n != 1:
            print("  FAIL %s：锚点命中 %d 次（应为 1）—— 变异被跳过" % (label, n))
            bad += 1
            continue
        _write(rel, data.replace(old, new))
        try:
            code, out = run_test(test)
        finally:
            _write(rel, data)
        if code == 0:
            print("  FAIL %s：自测仍然全绿（这条判据没牙齿）" % label)
            bad += 1
        elif want not in out:
            # 红了，但红的不是想红的那条——可能是别处先炸，判据指错也要修
            print("  FAIL %s：红了但没看到「%s」（红的可能不是这条判据）" % (label, want))
            print("      " + out.strip().splitlines()[-1] if out.strip() else "      （无输出）")
            bad += 1
        else:
            print("  PASS %s → 红在「%s」" % (label, want))

    # 还原后必须仍然全绿（防刀把自己文件改坏）
    for test in (JS_TEST, PY_TEST):
        code, out = run_test(test)
        if code != 0:
            print("  FAIL 还原后 %s 不绿" % test)
            print(out[-800:])
            bad += 1
    if bad == 0:
        print("  PASS 还原后两道门仍全绿")

    print("\n结果: %s" % ("全部变异刀有牙" if bad == 0 else "%d 处有问题" % bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
