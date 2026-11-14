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


# 【2026-11-08 修·刀自己会空转】原来只判 `r.returncode != 0`，于是判定文件**崩了**
# 也被算成"抓住"。实测：把判定文件整个换成一句 `raise RuntimeError("自己崩了")`，
# 本刀仍然逐条打印「已抓住：<无输出>」、最后打印「N 刀全红」并 exit 0 ——
# **一个连第一行都跑不完的判定，被认证成"全抓"**。
# 崩了和识别是两件事：前者说明判定没跑，后者才说明判据在工作。
# 起点也必须先绿：恒红的判定能让**每一把**刀都变红，那种"全红"什么都不证明。
# 本刀有两个判定文件（T1/T2，每把刀各用其一），所以起点要两个都绿。
def _judged(r) -> bool:
    """判定文件是「判出了问题」而不是「崩了」或「什么都没说」。

    【2026-11-08 第二版·第一版太严】第一版还要求「输出里没有 Traceback」，
    实测把**先印 FAIL、随后收尾时崩掉**的判定判成了"没抓住"：
      · `_qc_asr_mutation` ①②：印了 `FAIL lang='zh-cn'`，随后 vosk 桩抛
        `AttributeError: 'Model' object has no attribute '_handle'`
      · `_qc_config_bootstrap_mutation` B：印了 `FAIL 状态 created`，随后读临时
        `config.yaml` 抛 `FileNotFoundError`
    两次都是**仪器太窄**（本轮第三次）。真正的判别是**有没有点名**：
    退化桩只印一个 `exit 1`，一个字都没有 —— 那才叫"什么都没说"。
    """
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode != 0 and ("FAIL" in out or "✗" in out)


def _baseline_green() -> bool:
    for t in (T1, T2):
        r = subprocess.run([PY, "-u", t], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode != 0 or "Traceback" in out:
            print("   起点红：%s（exit %d）" % (t, r.returncode))
            return False
    return True


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
     # 【2026-10-21 修·锚点过时】原来是
     #   "(check_voice_backend, cfg),\n                    (check_asr, cfg)):"
     # 也就是假定 check_asr 是列表**最后一项**。后来 run_all 里又加了
     # check_wake_words，于是 check_asr 后面多了个逗号、`):` 也移到了新项之后，
     # 锚点命中 0 次 → 这一刀被跳过（脚本把跳过记成失败，所以是"1 处异常"）。
     # 现在只锚 check_asr 那一行本身，不依赖它是不是最后一项。
     E("                    (check_voice_backend, cfg),\n"
       "                    (check_asr, cfg),"),
     E("                    (check_voice_backend, cfg),")),

    ("⑥b doctor：麦克风关着也判 PASS（模型就绪但她照样听不见）",
     DR, T2,
     J("        if mic_note and level == PASS:",
       "            return (WARN, \"语音识别\", f\"{detail}；{mic_note}\","),
     J("        if False:",
       "            return (WARN, \"语音识别\", f\"{detail}；{mic_note}\",")),

    ("⑥ doctor：没有模型也判 PASS（没网时假装听得见）",
     DR, T2,
     E("    return (WARN, \"语音识别\",\n"
       "            \"没有 sherpa / vosk 模型：第一次用麦克风时会自动下载（约 42MB，需联网）\","),
     E("    return (PASS, \"语音识别\",\n"
       "            \"没有 sherpa / vosk 模型：第一次用麦克风时会自动下载（约 42MB，需联网）\",")),
]


def main() -> int:
    bad = 0
    if not _baseline_green():
        print("FAIL 起点就不是绿的 —— 恒红的判定能让每把刀都变红，这种\"全红\"不证明任何事")
        return 1
    print("  PASS 起点绿")
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
            caught = _judged(r)
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
    print("七刀全红" if bad == 0 else f"有 {bad} 处异常")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
