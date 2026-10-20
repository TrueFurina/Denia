"""真机探针：验证「没配大脑 → 点发送 → 浮层出现、顶部条让位」这条链。

为什么不进门禁：它依赖本机 Edge 的 headless 模式（`--dump-dom`），换台机器就跑不了。
门禁只放「不依赖浏览器」的判据；这条留作**人工探针**，改了发送路径或提示逻辑就跑一次。

它验的是静态断言验不了的三件事：
  1. `send()` 真的走到了失败分支（`Backend.sendChat` 被调用）；
  2. 浮层真的拿到了 `show` 类（不是"源码里有 add('show')"）；
  3. 浮层出现时顶部条真的被收起（同一句话不说两遍）。

踩过的两个坑（都会让探针静默失效）：
  - **`</body>` 在这个页面里出现两次**，第一次在注释里（记 callOverlay 漏标签那段）。
    按第一个替换会把探针塞进注释，永远不执行。必须锚 `</body>\\n</html>` 这个尾部。
  - **`window.Backend` 会被 `app_backend.js` 换成真适配层**，所以不能提前注入假对象
    （会被覆盖）。要在页面加载后**在真对象上改方法**。

跑法：python tools/_qc_setup_hint.py
产物：`_qc_setup_hint.png`（截图，落在仓库根，用完可删）
退出码：0 = 三件事都成立；1 = 有不符合。
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
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "src", "ui", "web")
PAGE = os.path.join(WEB, "index.html")
PROBE_NAME = "_qc_setup_hint_probe.html"

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

PROBE_JS = """<script>
/* 探针：在页面末尾执行（此时 app_backend.js 已就位），在真 Backend 上改方法。 */
window.__qc = ['load'];
window.addEventListener('error', function(e){ window.__qc.push('ERR:' + (e.message||'').slice(0,60)); });
setTimeout(function(){
  var B = window.Backend;
  if (!B) { document.title = 'QC|no-backend'; return; }
  B.getCapabilitySlots = function(){ window.__qc.push('slots'); return Promise.resolve({success:true,
    health:{chat:'none'}, slots:{chat:{state_text:'未配置'}}}); };
  B.sendChat = function(){ window.__qc.push('sendChat'); return Promise.resolve({success:false, error:'还没配模型'}); };
  B.toast = function(m){ window.__qc.push('toast:' + String(m).slice(0,14)); };
  var i = document.getElementById('input'), b = document.getElementById('send');
  if (i && b) { i.value = 'hi'; b.click(); window.__qc.push('clicked'); }
  setTimeout(function(){
    var h = document.getElementById('setupHint'), bar = document.getElementById('brainBar');
    window.__qc.push('cls=' + (h ? h.className : 'null'));
    window.__qc.push('barHidden=' + (bar ? bar.hidden : 'null'));
    window.__qc.push('title=' + ((h && h.querySelector('.sh-title')) || {}).textContent);
    document.title = 'QC|' + window.__qc.join(',');
  }, 1200);
}, 900);
</script>
"""


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


def main() -> int:
    edge = _edge()
    if not edge:
        print("跳过：找不到 Edge（这个探针只在本机有 Edge 时能跑）")
        return 0

    with open(PAGE, encoding="utf-8") as f:
        src = f.read()
    # 锚尾部，不能按第一个 </body>：那个在注释里
    tail = "</body>\n</html>"
    if tail not in src:
        print("✗ 页面尾部结构与预期不符，探针无法注入")
        return 1
    probe = os.path.join(WEB, PROBE_NAME)
    with open(probe, "w", encoding="utf-8") as f:
        f.write(src.replace(tail, PROBE_JS + tail, 1))

    httpd = _serve()
    try:
        url = "http://127.0.0.1:%d/%s" % (httpd.server_address[1], PROBE_NAME)
        r = subprocess.run([edge, "--headless=new", "--disable-gpu", "--dump-dom",
                            "--virtual-time-budget=12000", url],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        m = re.search(r"<title>(QC\|[^<]*)</title>", r.stdout or "")
        line = m.group(1)[3:] if m else ""
        print("埋点：" + (line or "（没拿到，探针可能没执行）"))
        ok = True
        for cond, msg in [
            ("sendChat" in line, "send() 走到了失败分支（sendChat 被调用）"),
            ("cls=setup-hint show" in line, "浮层真的拿到了 show 类"),
            ("barHidden=true" in line, "浮层出现时顶部条被收起（一句话不说两遍）"),
            ("她还没接上「大脑」" in line, "标题是「没接上大脑」（未配置态）"),
        ]:
            print(("  ✓ " if cond else "  ✗ ") + msg)
            ok = ok and cond

        # 留一张截图当证据（不进门禁，用完可删）
        shot = os.path.join(ROOT, "_qc_setup_hint.png")
        subprocess.run([edge, "--headless=new", "--disable-gpu",
                        "--screenshot=" + shot, "--window-size=520,900",
                        "--virtual-time-budget=12000", url],
                       capture_output=True, timeout=120)
        if os.path.isfile(shot):
            print("  截图：%s" % shot)
        return 0 if ok else 1
    finally:
        httpd.shutdown()
        try:
            os.unlink(probe)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
