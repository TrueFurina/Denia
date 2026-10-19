import glob, os, subprocess, sys

# locate project dir: 11
cands = glob.glob("G:/ai*")
proj = None
for c in cands:
    p = os.path.join(c, "11")
    if os.path.isdir(p) and os.path.isfile(os.path.join(p, "main.py")):
        proj = p
        break
if not proj:
    print("[ERR] cannot locate project dir (G:/ai*/11/main.py)")
    sys.exit(1)

os.chdir(proj)
os.environ["WEBVIEW2_ADDITIONAL_ARGS"] = "--disable-gpu --disable-software-rasterizer --no-sandbox"

log = os.path.join(proj, "bat_log.txt")
with open(os.path.join(proj, "bat_launched.txt"), "w", encoding="utf-8") as fh:
    fh.write("launching\n")
with open(log, "a", encoding="utf-8") as lf:
    lf.write("start\n")
py = r"C://Users//Administrator//AppData//Local//Programs//Python//Python312//python.exe"
rc = subprocess.call([py, "main.py"], cwd=proj)
with open(log, "a", encoding="utf-8") as lf:
    lf.write("exit %d\n" % rc)
with open(os.path.join(proj, "bat_launched.txt"), "a", encoding="utf-8") as fh:
    fh.write("done %d\n" % rc)
print("EXIT", rc)
