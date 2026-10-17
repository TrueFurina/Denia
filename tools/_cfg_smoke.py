# -*- coding: utf-8 -*-
"""冒烟：config.yaml 真解析 + 值确认 + 换行体检。"""
import io
import re
ROOT = r"G:\ai对话\11"
p = ROOT + r"\config.yaml"
t = io.open(p, encoding="utf-8").read()
import yaml
d = yaml.safe_load(t)
assert isinstance(d, dict) and len(d) > 20
m = re.search(r"duplex_call_asr_trailing_silence:\s*([\d.]+)", t)
out = "top_sections=%d\ntrailing_silence=%s\nCR=%d CRLF=%d rr=%d\n" % (
    len(d), m.group(1), t.count("\r"), t.count("\r\n"), t.count("\r\r"))
io.open(ROOT + r"\tools\_cfgsmoke_out.txt", "w", encoding="utf-8").write(out)
print("done")
