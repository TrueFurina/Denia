"""「导出聊天记录」的四把变异刀（验证 tools/test_chat_export.py 真的能抓错）。

本仓的规矩：**加门必须同时加变异**，否则那道门只是装饰品。这个脚本自己也在闸门里
（`tools/check_all.py` 的「导出聊天记录变异」，标慢项），所以它必须**快且自还原**。

四刀：
  M1 `load_all` 退回「最近 200 条」      → 导出会静默只导一段（用户拿到手才发现少一半）
  M2 去掉「没记录就报错」的守卫          → 写出一份只有标题的空 .md（比报错更误导）
  M3 `render_bundle` 不再跳过空角色      → 导出一堆只有标题的小节
  M4 文件名不过滤 Windows 非法字符       → 角色名带 `/` `:` 时 OSError（真因藏在异常里）

锚点写法注意（踩过两次的坑）：
  · 本仓 `src/ui/webapp.py` 是 **CRLF**，`src/companion/*.py` 是 **LF**。锚点按各文件
    实际的换行符拼，写死 `\\n` 会在 CRLF 文件上命中 0 次，然后这一刀被**静默跳过**
    ——脚本把"跳过"记成失败，但看起来像"检查器没抓到"。
  · 改源码必须用**二进制模式**（`io.open(p, "wb")`）。文本模式在 Windows 上会把
    `\\n` 写成 `\\r\\n`，把整个文件变成 CRLF，之后所有 `"\\n"` 锚点全失效。
  · 锚点不许依赖位置（"最后一项"这种假设已经失效过两次）。
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
TEST = os.path.join(ROOT, "tools", "test_chat_export.py")

WEBAPP = os.path.join("src", "ui", "webapp.py")
STORE = os.path.join("src", "companion", "history_store.py")
EXPORT = os.path.join("src", "companion", "chat_export.py")


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
]


def run_test() -> tuple[int, list[str]]:
    p = subprocess.run([PY, TEST], capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    fails = [ln.strip() for ln in (p.stdout or "").splitlines() if ln.strip().startswith("FAIL")]
    return p.returncode, fails


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
            code, fails = run_test()
        finally:
            _write(rel, data)          # 无论如何都还原
        if code == 0:
            print("  FAIL %s：测试仍然全绿（%s 没被抓住）" % (label, why))
            bad += 1
        else:
            print("  PASS %s：变红 %d 项  %s" % (label, len(fails), (fails[0] if fails else "")))

    code, _ = run_test()
    if code != 0:
        print("  FAIL 还原后测试仍失败（源码没恢复干净）")
        bad += 1
    else:
        print("  PASS 还原后 33 项全绿")

    print("\n变异结果：%d 处异常" % bad)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
