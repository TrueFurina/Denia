# -*- coding: utf-8 -*-
"""ASR 两处修复的变异验证：改回 bug 形态，测试必须抓住。

跑法：.venv/Scripts/python.exe -u tools/_qc_asr_mutation.py

注意（2026-10-19 连续踩）：多行锚点用 J() 逐行拼，别在字符串里写 \n
（heredoc 会把 \n 换成字面 /n）；中文不进 bytes 字面量，一律 E()/J()。
"""
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

PY = ".venv/Scripts/python.exe"
ASR = "src/voice/asr.py"
DR = "tools/doctor.py"
T1 = "src/tests/test_asr_voice_pipeline.py"
T2 = "src/tests/test_doctor_asr.py"


def E(s: str) -> bytes:
    return s.encode("utf-8")


def J(*lines: str) -> bytes:
    return chr(10).join(lines).encode("utf-8")


MUTS = [
    ("① 语言代码改回 zh-cn（原始 bug）",
     ASR, T1,
     E('        return Model(lang="cn")'),
     E('        return Model(lang="zh-cn")')),

    ("② 语言代码改成中文全拼 zhongwen（同样是无效代码）",
     ASR, T1,
     E('        return Model(lang="cn")'),
     E('        return Model(lang="zh")')),

    ("③ model_path 留空时不走下载分支（永远用空路径）",
     ASR, T1,
     E('        if self._model_path:\n'
       '            return Model(self._model_path)'),
     E('        if True:\n            return Model(self._model_path)')),

    ("④ doctor：model_path 不存在也只报 WARN（回到「只是黄一下」）",
     DR, T2,
     E("            return (FAIL, \"语音识别\",\n"
       "                    f\"model_path={model_path!r} 目录不存在，且非空值会把\""),
     E("            return (WARN, \"语音识别\",\n"
       "                    f\"model_path={model_path!r} 目录不存在，且非空值会把\"")),

    ("⑤ doctor：从 run_all 摘掉 check_asr",
     DR, T2,
     E("                    (check_voice_backend, cfg),\n"
       "                    (check_asr, cfg)):"),
     E("                    (check_voice_backend, cfg)):")),

    ("⑥ doctor：没有模型也判 PASS（没网时假装听得见）",
     DR, T2,
     E("    return (WARN, \"语音识别\",\n"
       "            \"没有 sherpa / vosk 模型：第一次用麦克风时会自动下载（约 42MB，需联网）\","),
     E("    return (PASS, \"语音识别\",\n"
       "            \"没有 sherpa / vosk 模型：第一次用麦克风时会自动下载（约 42MB，需联网）\",")),
]


def main() -> int:
    bad = 0
    snaps = {}
    for name, path, test, old, new in MUTS:
        if path not in snaps:
            snaps[path] = open(path, "rb").read()
        orig = snaps[path]
        if orig.count(old) != 1:
            print(f"[跳过] {name}：{path} 锚点命中 {orig.count(old)} 次")
            bad += 1
            continue
        open(path, "wb").write(orig.replace(old, new))
        try:
            r = subprocess.run([PY, "-u", test], capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
            caught = r.returncode != 0
            lines = [l for l in (r.stdout or "").splitlines() if l.strip()]
            why = [l for l in lines if l.strip().startswith("FAIL")]
            why = (why or lines or ["<无输出>"])[-1].strip()[:88]
            print(name)
            print("   -> " + ("已抓住：" + why if caught else "没抓住（测试失效）"))
            if not caught:
                bad += 1
        finally:
            open(path, "wb").write(orig)

    for path, orig in snaps.items():
        ok = hashlib.sha256(open(path, "rb").read()).hexdigest() == \
            hashlib.sha256(orig).hexdigest()
        print(f"还原 {path}：字节一致={ok}")
        if not ok:
            bad += 1
    print("六刀全红" if bad == 0 else f"有 {bad} 处异常")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
