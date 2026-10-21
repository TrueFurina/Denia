"""「导出聊天记录」的七把变异刀（验证 tools/test_chat_export.py 真的能抓错）。

本仓的规矩：**加门必须同时加变异**，否则那道门只是装饰品。这个脚本自己也在闸门里
（`tools/check_all.py` 的「导出聊天记录变异」，标慢项），所以它必须**快且自还原**。

后端四刀：
  M1 `load_all` 退回「最近 200 条」      → 导出会静默只导一段（用户拿到手才发现少一半）
  M2 去掉「没记录就报错」的守卫          → 写出一份只有标题的空 .md（比报错更误导）
  M3 `render_bundle` 不再跳过空角色      → 导出一堆只有标题的小节
  M4 文件名不过滤 Windows 非法字符       → 角色名带 `/` `:` 时 OSError（真因藏在异常里）

前端三刀（[11] 那组守的是"入口在不在"，最容易被误判成假绿）：
  M5 删掉右键绑定                        → 「绑了 contextmenu」必须红
  M6 右键绑着但不传 scope                → 「右键传 scope: 'all'」必须红（只查绑定不查载荷会漏）
  M7 左右键的载荷对调                    → 「左键仍走默认」必须红（两个范围搞反，用户点左键
                                            导出全部、点右键只导当前，且两边都提示成功）

锚点写法注意（踩过两次的坑）：
  · 本仓 `src/ui/webapp.py` 是 **CRLF**，`src/companion/*.py` 是 **LF**，
    `src/ui/web/index.html` 是 **LF**。锚点按各文件实际的换行符拼，写死 `\\n` 会在
    CRLF 文件上命中 0 次，然后这一刀被**静默跳过**——脚本把"跳过"记成失败，
    但看起来像"检查器没抓到"。
  · 改源码必须用**二进制模式**（`io.open(p, "wb")`）。文本模式在 Windows 上会把
    `\\n` 写成 `\\r\\n`，把整个文件变成 CRLF，之后所有 `"\\n"` 锚点全失效。
  · 锚点不许依赖位置（"最后一项"这种假设已经失效过两次）。
  · 锚点**不能只命中一个前缀**：`ec.addEventListener('contextmenu'` 是
    `ec.addEventListener('contextmenu', function(ev){...})` 的前缀，用前缀当锚点会把
    整段替换掉一半、留下语法残骸（上一版就差点这么写）。
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
TEST = os.path.join(ROOT, "tools", "test_chat_export.py")

WEBAPP = os.path.join("src", "ui", "webapp.py")
STORE = os.path.join("src", "companion", "history_store.py")
EXPORT = os.path.join("src", "companion", "chat_export.py")
INDEX = os.path.join("src", "ui", "web", "index.html")


def _read(rel: str) -> bytes:
    with io.open(os.path.join(ROOT, rel), "rb") as f:
        return f.read()


def _write(rel: str, data: bytes) -> None:
    with io.open(os.path.join(ROOT, rel), "wb") as f:
        f.write(data)


def _eol(data: bytes) -> bytes:
    """该文件实际用的换行符（CRLF 优先判断，因为 LF 是 CRLF 的子集）。"""
    return b"\r\n" if b"\r\n" in data else b"\n"


def _block(rel: str, lines: list[str]) -> bytes:
    """按该文件实际换行符拼一段锚点（含行尾）。"""
    eol = _eol(_read(rel))
    return eol.join(ln.encode("utf-8") for ln in lines) + eol


MUTS = [
    (
        "M1 load_all 退回最近 200 条",
        STORE,
        lambda: _block(STORE, ["        return self._read(char_id)"]),
        lambda: _block(STORE, ["        return self.load_recent(char_id)"]),
        "导出静默只导最近 200 条",
    ),
    (
        "M2 去掉「没记录就报错」的守卫",
        WEBAPP,
        lambda: _block(WEBAPP, [
            "                if not recs:",
            '                    return {"success": False,',
            '                            "error": "这个角色还没有聊天记录可导出"}',
        ]),
        lambda: b"",
        "写出只有标题的空 .md",
    ),
    (
        "M3 render_bundle 不再跳过空角色",
        EXPORT,
        lambda: _block(EXPORT, ["        if not recs:", "            continue"]),
        lambda: _block(EXPORT, ["        if not recs:", "            recs = []"]),
        "导出里出现只有标题的空小节",
    ),
    (
        "M4 文件名不过滤 Windows 非法字符",
        WEBAPP,
        lambda: _block(WEBAPP, [
            '        out = "".join("_" if ch in bad else ch for ch in str(name or "")).strip(" .")',
        ]),
        lambda: _block(WEBAPP, ['        out = str(name or "").strip(" .")']),
        "角色名带 / : * ? 时 OSError",
    ),
    (
        "M5 删掉右键绑定",
        INDEX,
        lambda: _block(INDEX, [
            "    ec.addEventListener('contextmenu', function(ev){",
            "      ev.preventDefault();",
            "      doExport({scope: 'all'}, '全部角色');",
            "    });",
        ]),
        lambda: b"",
        "「导出全部角色」没有入口了（scope=all 变成没人调的半截功能）",
    ),
    (
        "M6 右键绑着但不传 scope",
        INDEX,
        lambda: _block(INDEX, ["      doExport({scope: 'all'}, '全部角色');"]),
        lambda: _block(INDEX, ["      doExport({}, '全部角色');"]),
        "右键点下去提示「已导出全部角色 0 条」——实际导出的是当前角色",
    ),
    (
        "M7 左右键的载荷对调",
        INDEX,
        lambda: _block(INDEX, [
            "    ec.addEventListener('click', function(){ doExport({}, '当前角色'); });",
        ]),
        lambda: _block(INDEX, [
            "    ec.addEventListener('click', function(){ doExport({scope: 'all'}, '全部角色'); });",
        ]),
        "左键导出全部、右键只导当前，两边都提示成功",
    ),
]


def run_test() -> tuple[int, list[str], int]:
    p = subprocess.run([PY, TEST], capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    out = p.stdout or ""
    fails = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("FAIL")]
    # 通过数从测试自己的汇总行取，不写死——写死的话测试加一项，这里就永远说旧数字
    # （本仓出现过："还原后 33 项全绿"，而实际早就是 38 项）。
    m = re.search(r"结果:\s*(\d+)\s*PASS", out)
    n_pass = int(m.group(1)) if m else -1
    return p.returncode, fails, n_pass


def main() -> int:
    bad = 0
    for label, rel, mk_old, mk_new, why in MUTS:
        data = _read(rel)
        old, new = mk_old(), mk_new()
        n = data.count(old)
        if n != 1:
            # 锚点失效 = 这一刀没跑。**必须判失败**：不然脚本"全绿"而它验证的检查器
            # 可能早就抓不到东西了（本仓 4 个变异脚本都出过这个毛病）。
            print("  FAIL %s：锚点命中 %d 次（应为 1）—— 变异被跳过" % (label, n))
            bad += 1
            continue
        _write(rel, data.replace(old, new))
        try:
            code, fails, _ = run_test()
        finally:
            _write(rel, data)          # 无论如何都还原
        if code == 0:
            print("  FAIL %s：测试仍然全绿（%s 没被抓住）" % (label, why))
            bad += 1
        else:
            print("  PASS %s：变红 %d 项  %s" % (label, len(fails), (fails[0] if fails else "")))

    code, _, n_pass = run_test()
    if code != 0:
        print("  FAIL 还原后测试仍失败（源码没恢复干净）")
        bad += 1
    else:
        print("  PASS 还原后 %d 项全绿" % n_pass)

    print("\n变异结果：%d 处异常" % bad)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
