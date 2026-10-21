"""桥接三层闸门（tools/check_bridge_methods.py）的变异刀。

为什么要有这个脚本：这道闸门这轮改了核心逻辑（内联 `<script>` 也支持别名），而它自己
**没有变异脚本**。本仓的规矩是"加门必须同时加变异"，否则门只是装饰品——尤其这道门
连着出过两次"看着绿、其实整个文件没被扫"：

  · 第一版只扫 HTML 内联 `<script>` → `onboarding_role.js`（2000 行）完全不可见；
  · 第二版补了外链，但只认两种别名写法 → `llm_write.js` 整文件漏扫（总数 44→56 才发现）；
  · 第三版（本轮）发现内联脚本**根本没有别名支持** → `index.html` 的工具栏块
    （`var B = (window.Backend && window.Backend) || ...`）整段不可见，总数 136→163。

四把刀各打一条路径：
  M1 适配层漏导出，调用方在 **index.html 内联脚本**里用别名 B
  M2 同上，但调用方在**另一个页面**（first_launch_setup.html）
  M3 调用方在**外链 JS**（onboarding_role.js），名字写错
  M4 直接往 index.html 内联脚本里塞一个不存在的桥调用（证明内联扫描没退化成空集）
"""

from __future__ import annotations

import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
GATE = os.path.join(ROOT, "tools", "check_bridge_methods.py")

ADAPTER = os.path.join("src", "ui", "web", "redesign", "app_backend.js")
INDEX = os.path.join("src", "ui", "web", "index.html")
ONB = os.path.join("src", "ui", "web", "onboarding_role.js")


def _read(rel: str) -> bytes:
    with io.open(os.path.join(ROOT, rel), "rb") as f:
        return f.read()


def _write(rel: str, data: bytes) -> None:
    with io.open(os.path.join(ROOT, rel), "wb") as f:
        f.write(data)


def _eol(data: bytes) -> bytes:
    return b"\r\n" if b"\r\n" in data else b"\n"


def _line(rel: str, text: str) -> bytes:
    return text.encode("utf-8") + _eol(_read(rel))


MUTS = [
    ("M1 适配层漏导出 exportChatHistory（index.html 内联 + 别名 B）", ADAPTER,
     lambda: _line(ADAPTER, "    exportChatHistory,"),
     lambda: _line(ADAPTER, "    // exportChatHistory,"),
     "index.html"),
    ("M2 适配层漏导出 listCameras（另一页面的内联脚本）", ADAPTER,
     lambda: _line(ADAPTER,
                   "    getCallSettings, saveCallSettings, cameraFrame, listCameras, setCameraDevice,"),
     lambda: _line(ADAPTER,
                   "    getCallSettings, saveCallSettings, cameraFrame, setCameraDevice,"),
     "first_launch_setup.html"),
    ("M3 外链 JS 里把桥名字写错（onboarding_role.js）", ONB,
     lambda: b"",
     lambda: b"function _qcProbe(B){ return B.zzzNotARealBridgeMethod(); }\n",
     "onboarding_role.js"),
    ("M4 内联脚本里塞一个不存在的桥调用（index.html）", INDEX,
     lambda: b"",
     lambda: b"<script>function _qcProbe2(B){ return B.zzzAlsoNotReal(); }</script>\n",
     "index.html"),
]


def run_gate() -> tuple[int, str]:
    p = subprocess.run([PY, GATE], capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    bad = 0
    for label, rel, mk_old, mk_new, expect in MUTS:
        data = _read(rel)
        old, new = mk_old(), mk_new()
        n = data.count(old) if old else 1        # 空锚点 = 纯追加，视为命中
        if old and n != 1:
            print("  FAIL %s：锚点命中 %d 次（应为 1）—— 变异被跳过" % (label, n))
            bad += 1
            continue
        _write(rel, data.replace(old, new, 1) if old else data + new)
        try:
            code, out = run_gate()
        finally:
            _write(rel, data)
        if code == 0:
            print("  FAIL %s：闸门仍然全绿（这条路径没被扫到）" % label)
            bad += 1
        elif expect not in out:
            # 红了但没点对名：可能是别的原因红的，等于没验到这条路径
            print("  FAIL %s：闸门变红但没点名 %s" % (label, expect))
            bad += 1
        else:
            line = next((ln.strip() for ln in out.splitlines() if expect in ln), "")
            print("  PASS %s → %s" % (label, line))

    code, out = run_gate()
    if code != 0:
        print("  FAIL 还原后闸门仍红（源码没恢复干净）")
        bad += 1
    else:
        print("  PASS 还原后闸门全绿（%s）" % next(
            (ln.strip() for ln in out.splitlines() if "三层全对得上" in ln), ""))

    print("\n变异结果：%d 处异常" % bad)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
