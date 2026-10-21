# -*- coding: utf-8 -*-
"""「音色参考音录音」自测的变异刀（验证 tools/test_voice_ref_record.py 真的能抓错）。

本仓规矩：**加门必须同时加变异**，否则那道门只是装饰品。这个脚本自己也在闸门里
（`tools/check_all.py` 的「音色录音变异」，标慢项），所以必须**快且自还原**。

三刀，各打一条判据（不是同一个分支打三遍）：
  M1 去掉时长上限                     → 「99 秒只录 30 秒」必须红
  M2 立体声不下混（双声道字节塞单声道）→ 「帧数/字节数」必须红
  M3 桥方法把失败当成功               → 「不谎报」必须红

坑：
  · 改源码按**字节**读写（文本模式在 Windows 上会把 `\\n` 写成 `\\r\\n`，锚点全失效）。
  · 断言要**点名是哪条判据红了**，不能只看退出码：任意一条红都退出 1，
    只判退出码的话，M1 实际打中了 M2 的判据也照样"通过"。
"""

from __future__ import annotations

import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
TEST = os.path.join(ROOT, "tools", "test_voice_ref_record.py")

REC = os.path.join("src", "audio", "voice_ref_record.py")
API = os.path.join("src", "ui", "webapp.py")

MUTS = [
    ("M1 去掉时长上限", REC,
     '    return max(MIN_SECONDS, min(MAX_SECONDS, s))',
     '    return max(MIN_SECONDS, s)',
     "帧数 = 30 秒（不是 99 秒）"),
    ("M2 立体声不下混", REC,
     '        if channels == 2:\n            pcm = _downmix_to_mono(pcm)',
     '        if False:\n            pcm = _downmix_to_mono(pcm)',
     "帧数与单声道一致"),
    ("M3 失败当成功", API,
     '        r = record_wav(path, seconds)\n        if not r.get("success"):',
     '        r = record_wav(path, seconds)\n        if False:',
     "录音失败 → success=False（不谎报）"),
]


def _read(rel: str) -> bytes:
    with io.open(os.path.join(ROOT, rel), "rb") as f:
        return f.read()


def _write(rel: str, data: bytes) -> None:
    with io.open(os.path.join(ROOT, rel), "wb") as f:
        f.write(data)


def _eol(data: bytes) -> bytes:
    return b"\r\n" if b"\r\n" in data else b"\n"


def _encode(rel: str, text: str) -> bytes:
    """按该文件**实际换行符**拼锚点（本仓 webapp.py 是 CRLF，audio/*.py 是 LF）。"""
    return text.replace("\n", "\r\n" if _eol(_read(rel)) == b"\r\n" else "\n").encode("utf-8")


def run_test() -> tuple[int, str]:
    p = subprocess.run([PY, TEST], capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    bad = 0
    code, out = run_test()
    if code != 0:
        print("  FAIL 起点就不是绿的，变异没有意义")
        print(out[-800:])
        return 1
    print("  PASS 起点绿")

    for label, rel, old_txt, new_txt, want in MUTS:
        data = _read(rel)
        old, new = _encode(rel, old_txt), _encode(rel, new_txt)
        n = data.count(old)
        if n != 1:
            print("  FAIL %s：锚点命中 %d 次（应为 1）—— 变异被跳过" % (label, n))
            bad += 1
            continue
        _write(rel, data.replace(old, new))
        try:
            code, out = run_test()
        finally:
            _write(rel, data)
        if code == 0:
            print("  FAIL %s：自测仍然全绿（这条判据没牙齿）" % label)
            bad += 1
        elif want not in out or ("FAIL " + want) not in out:
            print("  FAIL %s：红了但不是因为「%s」" % (label, want))
            print(out[-800:])
            bad += 1
        else:
            print("  PASS %s：红了「%s」" % (label, want))

    code, out = run_test()
    if code != 0:
        print("  FAIL 还原后自测仍红（源码没恢复干净）")
        print(out[-800:])
        bad += 1
    else:
        print("  PASS 还原后仍然绿")

    print("\n变异结果：%d 处异常" % bad)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
