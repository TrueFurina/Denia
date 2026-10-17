import py_compile
ok = True
msg = []
for p in ["G:/ai对话/11/tools/ux_bailian_selftest.py"]:
    try:
        py_compile.compile(p, doraise=True)
        msg.append("OK " + p.split("/")[-1])
    except Exception as e:
        ok = False
        msg.append("FAIL " + repr(e))
with open("G:/ai对话/11/docs/_compile_check.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(msg) + ("\nALL_OK" if ok else "\nHAS_ERROR"))
