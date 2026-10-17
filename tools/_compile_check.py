# -*- coding: utf-8 -*-
import py_compile, traceback, os
OUT = r"G:\ai对话\11\docs\_compile_check.txt"
targets = [r"G:\ai对话\11\tools\real_software_drive.py"]
res = []
for p in targets:
    try:
        py_compile.compile(p, doraise=True)
        res.append("OK   " + os.path.basename(p))
    except Exception:
        res.append("FAIL " + os.path.basename(p) + "\n" + traceback.format_exc())
with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(res))
print("done")
