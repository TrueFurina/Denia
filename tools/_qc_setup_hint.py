"""真机探针：验证「没配大脑 → 点发送 → 浮层出现、顶部条让位 → 点稍后 → 条回来」。

为什么不进门禁：它依赖本机 Edge 的 headless 模式（`--dump-dom`），换台机器就跑不了。
门禁只放「不依赖浏览器」的判据；这条留作**人工探针**，改了发送路径或提示逻辑就跑一次。

三个场景（每个场景一次独立加载，状态不串）：
  A. 聊天槽「未配置」→ 点发送 → 浮层出现 + 顶部条收起
  B. 接 A 点「稍后」→ 浮层消失 + 顶部条回来（它是常驻兜底，不能被顺手关掉）
  C. 聊天槽「已连通」但发送失败（比如额度用尽）→ 浮层**不出现**（弹了就是撒谎）

踩过的坑（都会让探针静默失效）：
  - **`</body>` 在这个页面里出现两次**，第一次在注释里（记 callOverlay 漏标签那段）。
    按第一个替换会把探针塞进注释，永远不执行。必须锚 `</body>\\n</html>` 这个尾部。
  - **`window.Backend` 会被 `app_backend.js` 换成真适配层**，所以不能提前注入假对象
    （会被覆盖）。要在页面加载后**在真对象上改方法**。
  - 端口要**让系统分配**：8899 常被上一次没退干净的探针或用户实例占着（WinError 10048）。

跑法：python tools/_qc_setup_hint.py
产物：`_qc_setup_hint.png`（场景 A 的截图，落在仓库根，已 gitignore）
退出码：0 = 三个场景都成立；1 = 有不符合。
"""
from __future__ import annotations

import functools
import http.server
import os
import re
import shutil
import socketserver
import subprocess
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "src", "ui", "web")
PAGE = os.path.join(WEB, "index.html")

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

# 两种聊天槽状态（写进探针脚本里的字面量）
SLOTS_NONE = "{success:true, health:{chat:'none'}, slots:{chat:{state_text:'未配置'}}}"
SLOTS_OK = "{success:true, health:{chat:'ok'}, slots:{chat:{state_text:'已连通'}}}"

COMMON = """<script>
window.__qc = ['load'];
window.addEventListener('error', function(e){ window.__qc.push('ERR:' + (e.message||'').slice(0,60)); });
function qcSay(){ document.title = 'QC|' + window.__qc.join(','); }
setTimeout(function(){
  var B = window.Backend;
  if (!B) { window.__qc.push('no-backend'); qcSay(); return; }
  B.getCapabilitySlots = function(){ window.__qc.push('slots');
    return Promise.resolve(SLOTS); };
  B.sendChat = function(){ window.__qc.push('sendChat');
    return Promise.resolve({success:false, error:ERRMSG}); };
  B.toast = function(m){ window.__qc.push('toast:' + String(m).slice(0,14)); };
  var i = document.getElementById('input'), b = document.getElementById('send');
  if (i && b) { i.value = 'hi'; b.click(); window.__qc.push('clicked'); }
  setTimeout(function(){
    var h = document.getElementById('setupHint'), bar = document.getElementById('brainBar');
    window.__qc.push('cls=' + (h ? h.className : 'null'));
    window.__qc.push('barHidden=' + (bar ? bar.hidden : 'null'));
    window.__qc.push('title=' + ((h && h.querySelector('.sh-title')) || {}).textContent);
    TAIL
    qcSay();
  }, 1200);
}, 900);
</script>
"""

# 场景 A/B：点完发送后再点「稍后」，看条会不会回来
TAIL_DISMISS = """var x = document.getElementById('setupHintX');
    if (x) { x.click(); window.__qc.push('dismissed'); }
    setTimeout(function(){
      var h2 = document.getElementById('setupHint'), b2 = document.getElementById('brainBar');
      window.__qc.push('cls2=' + (h2 ? h2.className : 'null'));
      window.__qc.push('bar2=' + (b2 ? b2.hidden : 'null'));
      qcSay();
    }, 800);"""

TAIL_NONE = ""


def _edge() -> str:
    for p in EDGE_CANDIDATES:
        if os.path.isfile(p):
            return p
    return shutil.which("msedge") or ""


def _serve() -> socketserver.TCPServer:
    """端口让系统分配：8899 常被上一次探针或用户实例占着，
    写死端口会让探针在"上一次没退干净"时直接抛 WinError 10048。"""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=WEB)
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _page(slots: str, err: str, tail: str) -> str:
    js = (COMMON.replace("SLOTS", slots)
                .replace("ERRMSG", repr(err))
                .replace("TAIL", tail))
    with open(PAGE, encoding="utf-8") as f:
        src = f.read()
    # 锚尾部，不能按第一个 </body>：那个在注释里
    anchor = "</body>\n</html>"
    if anchor not in src:
        raise RuntimeError("页面尾部结构与预期不符，探针无法注入")
    return src.replace(anchor, js + anchor, 1)


def _run(edge: str, httpd, name: str, html: str, budget: str = "12000"):
    path = os.path.join(WEB, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    url = "http://127.0.0.1:%d/%s" % (httpd.server_address[1], name)
    r = subprocess.run([edge, "--headless=new", "--disable-gpu", "--dump-dom",
                        "--virtual-time-budget=" + budget, url],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    m = re.search(r"<title>(QC\|[^<]*)</title>", r.stdout or "")
    return (m.group(1)[3:] if m else ""), url


def main() -> int:
    edge = _edge()
    if not edge:
        print("跳过：找不到 Edge（这个探针只在本机有 Edge 时能跑）")
        return 0

    httpd = _serve()
    probes = ["_qc_sh_a.html", "_qc_sh_c.html"]
    try:
        line_a, url_a = _run(edge, httpd, probes[0],
                             _page(SLOTS_NONE, "还没配模型", TAIL_DISMISS))
        line_c, _ = _run(edge, httpd, probes[1],
                         _page(SLOTS_OK, "额度已用尽", TAIL_NONE))

        print("场景 A/B 埋点：" + (line_a or "（没拿到，探针可能没执行）"))
        print("场景 C   埋点：" + (line_c or "（没拿到）"))

        after_dismiss = line_a.split("cls2=")[-1].split(",")[0] if "cls2=" in line_a else ""
        # 用正则取"键=值"，不要用子串包含：`cls=setup-hint` 是 `cls=setup-hint show` 的前缀，
        # 用 in 判断会让"没弹"和"弹了"看起来一样（这正是假绿的经典形态）
        def field(line: str, key: str) -> str:
            m = re.search(r"(?:^|,)%s=([^,]*)" % re.escape(key), line)
            return m.group(1) if m else ""

        checks = [
            ("sendChat" in line_a, "A: send() 走到了失败分支（sendChat 被调用）"),
            (field(line_a, "cls") == "setup-hint show", "A: 浮层真的拿到了 show 类"),
            (field(line_a, "barHidden") == "true", "A: 顶部条让位（一句话不说两遍）"),
            ("她还没接上「大脑」" in line_a, "A: 标题是「没接上大脑」（未配置态）"),
            ("dismissed" in line_a and field(line_a, "cls2") == "setup-hint",
             "B: 点「稍后」后浮层收起（实际 cls2=%s）" % (field(line_a, "cls2") or "无")),
            (field(line_a, "bar2") == "false", "B: 顶部条回来了（常驻兜底没被顺手关掉）"),
            ("sendChat" in line_c, "C: 能聊时也真的发了（对照组确实走到了失败分支）"),
            (field(line_c, "cls") == "setup-hint",
             "C: 能聊时不弹浮层（实际 cls=%s，弹了就是撒谎）" % (field(line_c, "cls") or "无")),
            (field(line_c, "barHidden") == "true", "C: 能聊时顶部条也不打扰"),
        ]
        ok = True
        for cond, msg in checks:
            print(("  ✓ " if cond else "  ✗ ") + msg)
            ok = ok and cond

        shot = os.path.join(ROOT, "_qc_setup_hint.png")
        subprocess.run([edge, "--headless=new", "--disable-gpu",
                        "--screenshot=" + shot, "--window-size=520,900",
                        "--virtual-time-budget=12000", url_a],
                       capture_output=True, timeout=120)
        if os.path.isfile(shot):
            print("  截图：%s" % shot)
        return 0 if ok else 1
    finally:
        httpd.shutdown()
        for n in probes:
            try:
                os.unlink(os.path.join(WEB, n))
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
