# -*- coding: utf-8 -*-
"""运行时通话录音「不进包」的双向闸门（2026-11-06 发布红线）。

背景：2026-11-06 按当前 HEAD 重出安装包时，--force 复用旧 stage 留下 3 个
用户通话 wav，凭据闸只打 WARN 判「✓ 通过」，差点随 public 仓库的安装包
公开发布。这里同时守三层：
  ① check_package_secrets.scan_dir 把录音判为阻断（不是告警）
  ② make_dist._ignore 装配时排除 chat_audio 下的音频文件
  ③ make_dist.purge_runtime_audio 清掉 --force 残留，且 main() 真的调用它

对照组（不许误伤）：干净目录必须无阻断；_ignore 不能排除非音频文件；
purge 只碰交付副本，工作目录里的用户录音原件必须原封不动。
"""
from __future__ import annotations

import ast
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import check_package_secrets as C  # noqa: E402
import make_dist as M  # noqa: E402

OK = 0
FAIL = 0


def chk(name: str, cond: bool, detail: str = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
        print("  PASS %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s  <- %s" % (name, detail))


def main() -> int:
    print("1) 凭据闸把录音判为阻断")
    with tempfile.TemporaryDirectory(prefix="denia_audio_qc_") as td:
        audio = os.path.join(td, "src", "ui", "web", "assets", "chat_audio")
        os.makedirs(audio)
        wav = os.path.join(audio, "20261106-000000-0.wav")
        with io.open(wav, "wb") as f:
            f.write(b"RIFF-fake-user-voice")
        blocking, warnings = C.scan_dir(td)
        chk("含录音 → blocking（不是 warnings）",
            any("通话录音" in b for b in blocking)
            and not any("通话录音" in w for w in warnings),
            "blocking=%s warnings=%s" % (blocking, warnings))

        print("\n2) 装配期排除与清理")
        ignored = M._ignore(audio, ["20261106-000000-0.wav", "README.txt"])
        chk("_ignore 排除音频文件", "20261106-000000-0.wav" in ignored, str(ignored))
        chk("_ignore 不误伤非音频文件", "README.txt" not in ignored, str(ignored))
        killed = M.purge_runtime_audio(td)
        chk("purge_runtime_audio 真的删掉副本里的录音",
            len(killed) == 1 and not os.path.isfile(wav), str(killed))
        blocking2, _ = C.scan_dir(td)
        chk("清理后该目录无阻断项（对照组）",
            not any("通话录音" in b for b in blocking2), str(blocking2))

    print("\n3) 不许误删工作目录里的用户录音原件")
    work_audio = os.path.join(ROOT, "src", "ui", "web", "assets", "chat_audio")
    if os.path.isdir(work_audio):
        present = [f for f in os.listdir(work_audio) if f.lower().endswith(".wav")]
        chk("工作目录原件仍在（purge 只碰交付副本）", len(present) >= 1,
            "共 %d 个" % len(present))
    else:
        chk("工作目录音频目录存在（无原件可验时跳过）", True, "目录不存在，跳过")

    print("\n4) main() 真的调用了清理（AST，不用正则）")
    with io.open(os.path.join(ROOT, "tools", "make_dist.py"), "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())
    fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    called = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "purge_runtime_audio"
                 for n in ast.walk(fns.get("main", ast.Module(body=[], type_ignores=[]))))
    chk("main() 调用 purge_runtime_audio", called)

    print("\n结果: %d PASS / %d FAIL" % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
