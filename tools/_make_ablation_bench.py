# -*- coding: utf-8 -*-
"""从 gen_sample_app.py 派生一个「可消融」的测试台 gen_ablation_mix.py。

只改三处，其余逐字继承（保证与 app 同构的性质不变）：
  1. REF_WAV 可用环境变量 ABL_REF_WAV 覆盖
  2. 新增 ABL_REF=0/1 —— 控制 prepare 里是否携带参考音
  3. prepare 字典的 ref 三件套 + system_content 的 audio 段改为条件构造
"""
import io
import os

SRC = r"G:\ai对话\11\tools\gen_sample_app.py"
DST = r"G:\ai对话\11\tools\gen_ablation_mix.py"

s = io.open(SRC, encoding="utf-8").read()
nl = "\r\n" if "\r\n" in s else "\n"
flat = s.replace("\r\n", "\n")

A_OLD = 'REF_WAV = r"G:\\ai对话\\11\\assets\\voices\\daniya_ref.wav"'
A_NEW = ('REF_WAV = os.environ.get("ABL_REF_WAV",\n'
         '                      r"G:\\ai对话\\11\\assets\\voices\\daniya_ref.wav")\n'
         'USE_REF = os.environ.get("ABL_REF", "1") == "1"')

B_OLD = '''    ref_ascii = ascii_ref_path()
    ref_b64 = base64.b64encode(open(REF_WAV, "rb").read()).decode("ascii")
    log("参考音 %s (%d B) -> %s" % (os.path.basename(REF_WAV), os.path.getsize(REF_WAV), ref_ascii))
'''
B_NEW = '''    if USE_REF:
        ref_ascii = ascii_ref_path()
        ref_b64 = base64.b64encode(open(REF_WAV, "rb").read()).decode("ascii")
        log("参考音 %s (%d B) -> %s" % (os.path.basename(REF_WAV), os.path.getsize(REF_WAV), ref_ascii))
    else:
        ref_ascii, ref_b64 = "", ""
        log("参考音: 本次不传（消融对照）")
'''

C_OLD = '''            prepare = {
                "type": "prepare",
                "system_prompt": PERSONA,
                "config": dict(GEN_CFG),
                "ref_audio_path": ref_ascii,
                "ref_audio_base64": ref_b64,
                "tts_ref_audio_base64": ref_b64,
                "system_content": [
                    {"type": "text", "text": CLONE_INSTR + "\\n" + PERSONA},
                    {"type": "audio", "data": ref_b64},
                ],
            }
'''
C_NEW = '''            prepare = {
                "type": "prepare",
                "system_prompt": PERSONA,
                "config": dict(GEN_CFG),
            }
            sys_content = [{"type": "text", "text": CLONE_INSTR + "\\n" + PERSONA}]
            if USE_REF:
                prepare["ref_audio_path"] = ref_ascii
                prepare["ref_audio_base64"] = ref_b64
                prepare["tts_ref_audio_base64"] = ref_b64
                sys_content.append({"type": "audio", "data": ref_b64})
            prepare["system_content"] = sys_content
'''

for lab, old, new in (("REF_WAV", A_OLD, A_NEW), ("ref build", B_OLD, B_NEW), ("prepare", C_OLD, C_NEW)):
    n = flat.count(old)
    assert n == 1, "锚点 %s 命中 %d 次（应为 1）" % (lab, n)
    flat = flat.replace(old, new)

flat = flat.replace('"""应用保真版合成台：修复"结巴"必须先把测试台改成与 app 同构。',
                    '"""消融版测试台（由 gen_sample_app.py 派生）：唯一新增能力是 ABL_REF / ABL_REF_WAV。\n\n'
                    '原文件说明：应用保真版合成台：修复"结巴"必须先把测试台改成与 app 同构。')

assert "\r\r" not in flat.replace("\n", nl)
out = flat.replace("\n", nl) if nl == "\r\n" else flat
io.open(DST, "w", encoding="utf-8", newline="").write(out)

chk = io.open(DST, encoding="utf-8").read()
assert "ABL_REF" in chk and "ABL_REF_WAV" in chk
assert chk.count("USE_REF") == 3, chk.count("USE_REF")
assert "\\r\\r" not in chk
import subprocess
r = subprocess.run([r"C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe",
                    "-m", "py_compile", DST], capture_output=True, text=True)
print("py_compile rc=%d %s" % (r.returncode, (r.stderr or "")[-400:]))
print("wrote", DST, os.path.getsize(DST), "bytes")
